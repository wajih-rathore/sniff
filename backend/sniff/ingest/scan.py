"""Index a folder and keep the index in step with the disk: new, changed, renamed and deleted files."""
import ctypes
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import openai

from .. import config, db
from ..ai import classify, embed, extract, typer
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


LOCK = threading.Lock()  # one scan at a time, whether it comes from /index or the watcher; the second one waits
RETRY_AFTER = 30.0  # seconds before files another program held open are tried again
_retries: dict[str, threading.Timer] = {}  # folder -> its pending retry, so they do not pile up


def scan(conn: sqlite3.Connection, folder, ai: bool = True, status: dict | None = None) -> dict:
    """Bring the index for `folder` up to date. Returns counts of what happened."""
    with LOCK:
        return _scan(conn, folder, ai, status)


def _scan(conn, folder, ai, status) -> dict:
    folder = Path(folder).resolve()
    status = status if status is not None else {}
    on_disk = {str(p): p for p in iter_files(folder)}
    known = {r["path"]: r for r in conn.execute("SELECT * FROM files") if r["path"].startswith(str(folder).rstrip(os.sep) + os.sep)}  # "docs" must not claim "docs2"
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
    locked = []
    for p in todo:
        status["current"] = str(p)
        try:
            st, r, h = p.stat(), known.get(str(p)), sha256(p)
        except OSError as e:  # held open by another program (Windows will not share it), or gone meanwhile
            if not (known.get(str(p)) and known[str(p)]["status"] == "indexed"):  # an indexed copy stays findable
                _mark_error(conn, p, None, None, e)
            locked.append(p)
            counts["errors"] += 1
            status["errors"] += 1
            status["done"] += 1
            continue
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
                index_text(conn, p, h, ai)  # searchable now; the facts worker reads its type and facts later
                counts["indexed"] += 1
            except Exception as e:  # one bad file must not stop the scan
                conn.rollback()
                _mark_error(conn, p, h, st, e)
                if isinstance(e, PermissionError):  # locked while it was being read
                    locked.append(p)
                counts["errors"] += 1
                status["errors"] += 1
        conn.commit()
        QUEUED.set()  # after the commit, so the worker's own connection sees the new queue row
        status["done"] += 1

    for r in gone.values():
        db.release(conn, r["id"])  # a copy elsewhere keeps the document alive
        conn.execute("DELETE FROM files WHERE id=?", (r["id"],))
        db.log(conn, "removed", r["path"])
        counts["removed"] += 1
    conn.commit()
    status.update(running=False, current=None)
    if locked:
        _retry_later(folder, ai)
    return counts


def _retry_later(folder: Path, ai: bool) -> None:
    """Scan the folder again in RETRY_AFTER seconds, for the files that were held open. An error row is never
    skipped by the size and time check, so those files are read then; the rest is a quick check."""
    if (t := _retries.get(str(folder))) and t.is_alive():
        return

    def run():
        _retries.pop(str(folder), None)
        conn = db.connect()
        try:
            scan(conn, folder, ai)
        finally:
            conn.close()

    t = _retries[str(folder)] = threading.Timer(RETRY_AFTER, run)
    t.daemon = True
    t.start()


def index_file(conn: sqlite3.Connection, path: Path, sha: str, ai: bool = True) -> int:
    """Both phases at once, for the evaluation and the answer refresh in /ask. The rows are the same as the queue makes."""
    file_id = index_text(conn, path, sha, ai)
    read_facts(conn, file_id)
    return file_id


def index_text(conn: sqlite3.Connection, path: Path, sha: str, ai: bool = True) -> int:
    """Phase 1, about a second: read, chunk and embed the file and replace its rows, so search finds it at once. Its type
    and facts come later from the queue (read_facts). A file with the same bytes as one already indexed is recorded as
    a copy of it instead. Returns the document's id."""
    st = path.stat()
    row = conn.execute("SELECT id FROM files WHERE path=?", (str(path),)).fetchone()
    same = conn.execute("SELECT id, doc_type FROM files WHERE sha256=? AND status='indexed' AND copy_of IS NULL AND path<>? ORDER BY id LIMIT 1",
                        (sha, str(path))).fetchone()
    if same:  # exact copy in another folder: one document, several paths, facts stored once
        if row:
            db.release(conn, row["id"])
        conn.execute(
            "INSERT INTO files(path, size, mtime, sha256, doc_type, indexed_at, status, copy_of) VALUES (?,?,?,?,?,?, 'indexed', ?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, sha256=excluded.sha256, doc_type=excluded.doc_type, "
            "indexed_at=excluded.indexed_at, status='indexed', error=NULL, copy_of=excluded.copy_of",
            (str(path), st.st_size, st.st_mtime, sha, same["doc_type"], datetime.now().isoformat(timespec="seconds"), same["id"]),
        )
        db.log(conn, "copy", f"{path} = file {same['id']}")
        return same["id"]

    pages, boxes = readers.read_layout(path)
    pieces = embed.fit(readers.chunk(pages))
    vectors = embed.embed([t for _, t in pieces]) if pieces else []
    if row:
        file_id = row["id"]
        db.release(conn, file_id)
    else:
        file_id = conn.execute("INSERT INTO files(path) VALUES (?)", (str(path),)).lastrowid
    for (page, text), vec in zip(pieces, vectors):
        cid = conn.execute("INSERT INTO chunks(file_id, page, text) VALUES (?,?,?)", (file_id, page, text)).lastrowid
        if vec is not None:  # refused by the embedding server: keyword search still finds the chunk
            conn.execute("INSERT INTO chunk_vec(chunk_id, embedding) VALUES (?,?)", (cid, vec.tobytes()))
    missed = sum(v is None for v in vectors)
    if ai and pieces:
        conn.execute("INSERT OR REPLACE INTO queue(file_id, pages, boxes, added_at) VALUES (?,?,?,?)",
                     (file_id, json.dumps(pages), json.dumps(boxes), datetime.now().isoformat(timespec="seconds")))
    conn.execute(
        "UPDATE files SET size=?, mtime=?, sha256=?, doc_type=NULL, indexed_at=?, status='indexed', error=?, copy_of=NULL WHERE id=?",
        (st.st_size, st.st_mtime, sha, datetime.now().isoformat(timespec="seconds"),
         f"{missed} of {len(pieces)} chunks could not be embedded; keyword search still finds them" if missed else None, file_id),
    )
    db.log(conn, "indexed", str(path))
    return file_id


def read_facts(conn: sqlite3.Connection, file_id: int) -> bool:
    """Phase 2: classify and extract one queued file from the pages saved in phase 1, and store its type and facts.
    The type comes from the classifier on the stored vectors when it is sure (typer), else from the chat model; the
    fields come from rules first (extract). A confident "other" never reaches the model at all. When no model can sort
    the document, the classifier's guess above a floor is used, with the rules only, and its facts go to review.
    Returns False if the file is not queued (done, re-indexed or deleted meanwhile). If the chat server cannot be reached,
    the error is raised and the file stays queued; any other model failure is recorded on the file, as before."""
    q = conn.execute("SELECT * FROM queue WHERE file_id=?", (file_id,)).fetchone()
    if not q:
        return False
    pages = [tuple(p) for p in json.loads(q["pages"])]
    boxes = {int(k): [tuple(b) for b in v] for k, v in json.loads(q["boxes"]).items()}  # JSON turned the keys into strings
    doc_type, facts, ai_error = None, [], None
    try:
        doc_type = typer.classify(conn, file_id, pages) or classify.classify(pages)
        guessed = doc_type is None
        doc_type = doc_type or typer.guess(conn, file_id)
        if doc_type in FACT_MODELS:
            facts = extract.extract(doc_type, pages, boxes, fill_gaps=not guessed)
        if guessed:  # no model could sort it: the classifier's guess, so every fact waits for the user
            facts = [f | {"status": "needs_review", "reason": "; ".join(filter(None, ("type guessed", f.get("reason"))))} for f in facts]
    except openai.APIConnectionError:
        raise
    except Exception as e:  # model output stayed invalid after retries: keep the file searchable, record why
        ai_error = f"{type(e).__name__}: {e}"[:500]

    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")  # hold the write lock from this check to the commit, so a re-index cannot slip in
    if not conn.execute("SELECT 1 FROM queue WHERE id=?", (q["id"],)).fetchone():
        return False  # the file changed or went away while the model read it; a new queue row is read next
    for f in facts:
        status = f.get("status") or ("verified" if f["verified"] else "needs_review")
        conn.execute(
            "INSERT INTO facts(file_id, field, value, value_type, page, verified, source_text, status, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (file_id, f["field"], f["value"], f["value_type"], f["page"], int(f["verified"]), f["source_text"], status, f.get("reason")),
        )
    conn.execute("UPDATE files SET doc_type=? WHERE id=? OR copy_of=?", (doc_type, file_id, file_id))
    if ai_error:
        conn.execute("UPDATE files SET error=coalesce(error || '; ', '') || ? WHERE id=?", (ai_error, file_id))
    conn.execute("DELETE FROM queue WHERE id=?", (q["id"],))
    return True


QUEUED = threading.Event()  # set when a scan has queued files, so the worker starts on them without waiting


def waiting(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM queue").fetchone()[0]


class FactsWorker:
    """One background thread that reads types and facts for queued files, oldest first. It holds back while someone is
    searching or asking (user_active), so the chat server is free for them, and retries later when the server is down."""

    def __init__(self, idle: float = 3.0, retry: float = 10.0):
        self.idle, self.retry = idle, retry
        self.last_user = 0.0
        self.stopping = threading.Event()
        self.thread = None

    def user_active(self) -> None:
        self.last_user = time.monotonic()

    def start(self) -> None:
        if not self.thread:
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name="facts-worker")
            self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        QUEUED.set()
        if self.thread:
            self.thread.join(5)
        self.thread = None

    def _run(self) -> None:
        if sys.platform == "win32":  # below normal, like the Windows Search indexer
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -1)
        conn = db.connect()
        try:
            while not self.stopping.is_set():
                if (quiet := self.idle - (time.monotonic() - self.last_user)) > 0:
                    self.stopping.wait(quiet)
                    continue
                QUEUED.clear()
                row = conn.execute("SELECT file_id FROM queue ORDER BY id LIMIT 1").fetchone()
                if not row:
                    QUEUED.wait(5)
                    continue
                try:
                    read_facts(conn, row["file_id"])
                    conn.commit()
                except Exception:  # server down or database busy: the file stays queued, try again later
                    conn.rollback()
                    self.stopping.wait(self.retry)
        finally:
            conn.close()


def _mark_error(conn, path: Path, sha: str | None, st, err: Exception) -> None:
    """Record why a file could not be indexed. `st` is None when the file could not even be opened."""
    conn.execute(
        "INSERT INTO files(path, size, mtime, sha256, status, error) VALUES (?,?,?,?, 'error', ?) "
        "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, sha256=excluded.sha256, status='error', error=excluded.error",
        (str(path), st.st_size if st else None, st.st_mtime if st else None, sha, f"{type(err).__name__}: {err}"[:500]),
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
