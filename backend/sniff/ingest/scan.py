"""Index a folder and keep the index in step with the disk: new, changed, renamed and deleted files."""
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .. import config, db
from ..ai import classify, embed, extract
from ..schemas import FACT_MODELS
from . import readers


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def iter_files(folder: Path):
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in config.SUPPORTED and not p.name.startswith("~$"):
            yield p.resolve()


def scan(conn: sqlite3.Connection, folder, ai: bool = True, status: dict | None = None) -> dict:
    """Bring the index for `folder` up to date. Returns counts of what happened."""
    folder = Path(folder).resolve()
    status = status if status is not None else {}
    on_disk = {str(p): p for p in iter_files(folder)}
    known = {r["path"]: r for r in conn.execute("SELECT * FROM files") if r["path"].startswith(str(folder))}
    gone = {p: r for p, r in known.items() if p not in on_disk}
    gone_by_hash = {r["sha256"]: r for r in gone.values() if r["sha256"]}

    # cheap check first: size and modification time
    todo = []
    for p_str, p in on_disk.items():
        st, r = p.stat(), known.get(p_str)
        if r and r["status"] == "indexed" and r["size"] == st.st_size and r["mtime"] == st.st_mtime:
            continue
        todo.append(p)

    counts = {"indexed": 0, "unchanged": 0, "renamed": 0, "removed": 0, "errors": 0}
    status.update(running=True, done=0, total=len(todo), current=None, errors=0)
    for p in todo:
        status["current"] = str(p)
        st, r, h = p.stat(), known.get(str(p)), sha256(p)
        if r and r["status"] == "indexed" and r["sha256"] == h:  # touched, content unchanged
            conn.execute("UPDATE files SET size=?, mtime=? WHERE id=?", (st.st_size, st.st_mtime, r["id"]))
            counts["unchanged"] += 1
        elif not r and h in gone_by_hash:  # moved or renamed: same content, new path
            old = gone_by_hash.pop(h)
            gone.pop(old["path"], None)
            conn.execute("UPDATE files SET path=?, size=?, mtime=? WHERE id=?", (str(p), st.st_size, st.st_mtime, old["id"]))
            db.log(conn, "renamed", f"{old['path']} -> {p}")
            counts["renamed"] += 1
        else:
            try:
                index_file(conn, p, h, ai)
                counts["indexed"] += 1
            except Exception as e:  # one bad file must not stop the scan
                conn.rollback()
                _mark_error(conn, p, h, st, e)
                counts["errors"] += 1
                status["errors"] += 1
        conn.commit()
        status["done"] += 1

    for r in gone.values():
        db.delete_file_data(conn, r["id"])
        conn.execute("DELETE FROM files WHERE id=?", (r["id"],))
        db.log(conn, "removed", r["path"])
        counts["removed"] += 1
    conn.commit()
    status.update(running=False, current=None)
    return counts


def index_file(conn: sqlite3.Connection, path: Path, sha: str, ai: bool = True) -> int:
    """Read, embed, classify and extract one file, then replace its rows in one transaction."""
    st = path.stat()
    pages = readers.read(path)
    pieces = readers.chunk(pages)
    vectors = embed.embed([t for _, t in pieces]) if pieces else []
    doc_type, facts, ai_error = None, [], None
    if ai and pieces:
        try:
            doc_type = classify.classify("\n".join(t for _, t in pages))
            if doc_type in FACT_MODELS:
                facts = extract.extract(doc_type, pages)
        except Exception as e:  # model output stayed invalid after retries: keep the file searchable, record why
            ai_error = f"{type(e).__name__}: {e}"[:500]

    row = conn.execute("SELECT id FROM files WHERE path=?", (str(path),)).fetchone()
    if row:
        file_id = row["id"]
        db.delete_file_data(conn, file_id)
    else:
        file_id = conn.execute("INSERT INTO files(path) VALUES (?)", (str(path),)).lastrowid
    for (page, text), vec in zip(pieces, vectors):
        cid = conn.execute("INSERT INTO chunks(file_id, page, text) VALUES (?,?,?)", (file_id, page, text)).lastrowid
        conn.execute("INSERT INTO chunk_vec(chunk_id, embedding) VALUES (?,?)", (cid, vec.tobytes()))
    for f in facts:
        conn.execute(
            "INSERT INTO facts(file_id, field, value, value_type, page, verified, source_text) VALUES (?,?,?,?,?,?,?)",
            (file_id, f["field"], f["value"], f["value_type"], f["page"], int(f["verified"]), f["source_text"]),
        )
    conn.execute(
        "UPDATE files SET size=?, mtime=?, sha256=?, doc_type=?, indexed_at=?, status='indexed', error=? WHERE id=?",
        (st.st_size, st.st_mtime, sha, doc_type, datetime.now().isoformat(timespec="seconds"), ai_error, file_id),
    )
    db.log(conn, "indexed", str(path))
    return file_id


def _mark_error(conn, path: Path, sha: str, st, err: Exception) -> None:
    conn.execute(
        "INSERT INTO files(path, size, mtime, sha256, status, error) VALUES (?,?,?,?, 'error', ?) "
        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, sha256=excluded.sha256, status='error', error=excluded.error",
        (str(path), st.st_size, st.st_mtime, sha, f"{type(err).__name__}: {err}"[:500]),
    )
    conn.commit()


def is_stale(conn: sqlite3.Connection, file_id: int) -> bool:
    """True if the file changed or vanished since it was indexed (checked before showing an answer)."""
    r = conn.execute("SELECT path, size, mtime FROM files WHERE id=?", (file_id,)).fetchone()
    p = Path(r["path"]) if r else None
    if not p or not p.exists():
        return True
    st = p.stat()
    return (st.st_size, st.st_mtime) != (r["size"], r["mtime"])
