import json
import threading
from collections import Counter
import webbrowser
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pymupdf
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config, db, gcal, records, verify, watch
from .ask import ask
from .ingest import readers, scan
from .schemas import (ACTION_DATE_FIELDS, EXPIRY_FIELDS, FIELDS, HOLDER_FIELDS, ORG_FIELDS, Answer, Conflict, Document, Fact, Hit, MissingFields, Overview,
                      ReviewItem, UpcomingItem)
from .search import search

STATUS = {"running": False, "done": 0, "total": 0, "current": None, "errors": 0, "watching": []}
WATCHER = watch.Watcher(STATUS)


@asynccontextmanager
async def lifespan(_):
    WATCHER.restore()  # folders watched before the last shutdown
    yield
    WATCHER.stop()


app = FastAPI(title="Sniff", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EVAL_DIR = config.ROOT / "eval" / "results"
REMINDER_TITLES = {"expiry_date": "expires", "due_date": "payment due", "warranty_until": "warranty ends", "end_date": "ends"}


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class IndexReq(BaseModel):
    folder: str
    ai: bool = True


class WatchReq(BaseModel):
    folder: str


class AskReq(BaseModel):
    question: str


class ReminderReq(BaseModel):
    fact_id: int


class OpenReq(BaseModel):
    file_id: int
    page: int = 1


class FactEdit(BaseModel):
    value: str  # as the user types it, e.g. "12/03/2026" or "Rs. 4,320"


@app.post("/index")
def index(req: IndexReq):
    folder = Path(req.folder).expanduser()
    if not folder.is_dir():
        raise HTTPException(400, f"Folder not found: {folder}")
    if STATUS["running"]:
        raise HTTPException(409, "Indexing is already running")
    STATUS["running"] = True

    def work():
        conn = db.connect()
        try:
            STATUS["last"] = scan.scan(conn, folder, ai=req.ai, status=STATUS)
        finally:
            STATUS["running"] = False
            conn.close()

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/index/status")
def index_status():
    return STATUS


@app.get("/watch")
def watch_list():
    return {"folders": WATCHER.folders(), "last": STATUS.get("watch_last"), "error": STATUS.get("watch_error")}


@app.post("/watch")
def watch_add(req: WatchReq):
    folder = Path(req.folder).expanduser()
    if not folder.is_dir():
        raise HTTPException(400, f"Folder not found: {folder}")
    WATCHER.add(folder)
    return watch_list()


@app.delete("/watch")
def watch_remove(req: WatchReq):
    if not WATCHER.remove(req.folder):
        raise HTTPException(404, "This folder is not being watched")
    return watch_list()


@app.get("/search", response_model=list[Hit])
def search_route(q: str, k: int = 10, mode: str = "hybrid", conn=Depends(get_conn)):
    return search(conn, q, k=k, mode=mode)


@app.post("/ask", response_model=Answer)
def ask_route(req: AskReq, conn=Depends(get_conn)):
    answer = ask(conn, req.question)
    stale = [s.file_id for s in answer.sources if scan.is_stale(conn, s.file_id)]
    if stale:  # a cited file changed after indexing: refresh it, then answer again
        for fid in stale:
            path = Path(conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()["path"])
            if path.exists():
                scan.index_file(conn, path, scan.sha256(path))
            else:
                db.release(conn, fid)  # a copy in another folder keeps the document
                conn.execute("DELETE FROM files WHERE id=?", (fid,))
        conn.commit()
        answer = ask(conn, req.question)
    return answer


def _facts(conn, file_id: int) -> list[Fact]:
    return [records.fact(r) for r in conn.execute("SELECT * FROM facts WHERE file_id=? ORDER BY id", (file_id,))]


def _paths(conn, file_id: int) -> list[str]:
    """Every path holding this document: its own first, then exact copies in other folders."""
    return [r["path"] for r in conn.execute("SELECT path FROM files WHERE id=? OR copy_of=? ORDER BY copy_of IS NOT NULL, id", (file_id, file_id))]


def _document(conn, r, near: list[int] | None = None) -> Document:
    pages = conn.execute("SELECT max(page) FROM chunks WHERE file_id=?", (r["id"],)).fetchone()[0] or 1
    return Document(id=r["id"], path=r["path"], name=Path(r["path"]).name, doc_type=r["doc_type"], indexed_at=r["indexed_at"],
                    status=r["status"], facts=_facts(conn, r["id"]), paths=_paths(conn, r["id"]), near_duplicates=near or [], pages=pages)


def _canonical(conn, file_id: int):
    r = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if r and r["copy_of"]:  # a copy's id leads to the document it copies
        r = conn.execute("SELECT * FROM files WHERE id=?", (r["copy_of"],)).fetchone()
    if not r:
        raise HTTPException(404, "No such document")
    return r


@app.get("/documents", response_model=list[Document])
def documents(type: str | None = None, conn=Depends(get_conn)):
    sql = "SELECT * FROM files WHERE copy_of IS NULL" + (" AND doc_type=?" if type else "") + " ORDER BY path"
    return [_document(conn, r) for r in conn.execute(sql, (type,) if type else ())]


@app.get("/documents/{file_id}", response_model=Document)
def document(file_id: int, conn=Depends(get_conn)):
    """The review view: the document, all its paths, its facts with status, reason, page and source text."""
    r = _canonical(conn, file_id)
    near = []
    if r["doc_type"] in FIELDS:
        docs = records.load(conn, r["doc_type"])
        me = next((d for d in docs if d.id == r["id"]), None)
        k = records.key(me) if me else None
        near = [d.id for d in docs if k and d.id != r["id"] and records.key(d) == k]
    return _document(conn, r, near)


@app.get("/documents/{file_id}/pages/{n}.png")
def page_image(file_id: int, n: int, conn=Depends(get_conn)):
    """One page as an image, so the review screen can show the value next to the original."""
    paths = [Path(p) for p in _paths(conn, _canonical(conn, file_id)["id"]) if Path(p).exists()]
    if not paths:
        raise HTTPException(404, "File not found on disk")
    p = paths[0]
    if p.suffix.lower() == ".pdf":
        with pymupdf.open(str(p)) as doc:
            if not 1 <= n <= len(doc):
                raise HTTPException(404, "No such page")
            return Response(doc[n - 1].get_pixmap(dpi=110).tobytes("png"), media_type="image/png")
    if p.suffix.lower() in readers.IMAGES and n == 1:
        return FileResponse(p)
    raise HTTPException(404, "No page image for this kind of file")


def _fact_row(conn, fact_id: int):
    r = conn.execute("SELECT fa.*, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.id=?", (fact_id,)).fetchone()
    if not r:
        raise HTTPException(404, "No such fact")
    return r


@app.patch("/facts/{fact_id}", response_model=Fact)
def correct_fact(fact_id: int, req: FactEdit, conn=Depends(get_conn)):
    """The user's correction. Field rules must pass; a broken cross-field rule is kept as the reason to look again."""
    r = _fact_row(conn, fact_id)
    specs = FIELDS.get(r["doc_type"], {})
    spec = specs.get(r["field"])
    if not spec:
        raise HTTPException(400, "This fact has no field rules")
    value = verify.parse(spec.kind, req.value)
    if value is None:
        raise HTTPException(422, f"Could not read a {spec.kind} from '{req.value}'")
    if problem := verify.field_problem(spec.kind, value, spec.required):
        raise HTTPException(422, problem)
    others = {x["field"]: verify.typed(specs[x["field"]].kind, x["value"])
              for x in conn.execute("SELECT field, value FROM facts WHERE file_id=? AND id<>?", (r["file_id"], fact_id)) if x["field"] in specs}
    warnings = [why for fields, why in verify.cross_problems(r["doc_type"], {**others, r["field"]: value}) if r["field"] in fields]
    conn.execute("UPDATE facts SET value=?, value_type=?, status='corrected', reason=? WHERE id=?",
                 (verify.stored(value), verify.VALUE_TYPES.get(spec.kind, "text"), "; ".join(warnings) or None, fact_id))
    db.log(conn, "corrected", json.dumps({"fact_id": fact_id, "field": r["field"], "old": r["value"], "new": verify.stored(value)}))
    conn.commit()
    return records.fact(_fact_row(conn, fact_id))


@app.post("/facts/{fact_id}/approve", response_model=Fact)
def approve_fact(fact_id: int, conn=Depends(get_conn)):
    r = _fact_row(conn, fact_id)
    conn.execute("UPDATE facts SET status='approved', reason=NULL WHERE id=?", (fact_id,))
    db.log(conn, "approved", json.dumps({"fact_id": fact_id, "field": r["field"], "value": r["value"]}))
    conn.commit()
    return records.fact(_fact_row(conn, fact_id))


@app.get("/review", response_model=list[ReviewItem])
def review(conn=Depends(get_conn)):
    """Facts that need a person to look at them, grouped by document."""
    rows = conn.execute("SELECT fa.*, f.path, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id "
                        "WHERE fa.status='needs_review' AND f.copy_of IS NULL ORDER BY f.path, fa.id")
    return [ReviewItem(fact=records.fact(r), document=Path(r["path"]).name, path=r["path"], doc_type=r["doc_type"]) for r in rows]


@app.get("/conflicts", response_model=list[Conflict])
def conflicts(conn=Depends(get_conn)):
    return records.resolve(records.load(conn)).conflicts


@app.get("/overview", response_model=Overview)
def overview(conn=Depends(get_conn)):
    files = conn.execute("SELECT status, doc_type, copy_of FROM files").fetchall()
    docs = [f for f in files if f["status"] == "indexed" and f["copy_of"] is None]
    res = records.resolve(records.load(conn))
    today, expiring = date.today(), {"30": 0, "60": 0, "90": 0}
    for d in res.kept:
        for f in EXPIRY_FIELDS:
            if d.get(f):
                left = (date.fromisoformat(d.get(f)) - today).days
                for n in expiring:
                    expiring[n] += 0 <= left <= int(n)
    missing = [MissingFields(file_id=d.id, name=Path(d.path).name, doc_type=d.doc_type, fields=gaps) for d in res.kept
               if (gaps := [f for f, s in FIELDS[d.doc_type].items() if s.required and f not in d.facts])]
    by_type = Counter(f["doc_type"] or "other" for f in docs)
    needs = conn.execute("SELECT count(*) FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.status='needs_review' AND f.copy_of IS NULL").fetchone()[0]
    return Overview(total_files=sum(f["status"] == "indexed" for f in files), documents=len(docs), by_type=by_type,
                    exact_copies=sum(f["copy_of"] is not None for f in files), near_duplicates=len(res.skipped), expiring=expiring,
                    missing_required=missing, needs_review=needs, conflicts=len(res.conflicts), errors=sum(f["status"] == "error" for f in files))


@app.get("/upcoming", response_model=list[UpcomingItem])
def upcoming(days: int = 90, conn=Depends(get_conn)):
    """Dates to act on. Near-duplicates appear once; a date another document contradicts carries the conflict."""
    today = date.today()
    res = records.resolve(records.load(conn))
    reminded = {r["fact_id"] for r in conn.execute("SELECT fact_id FROM reminders WHERE status='created'")}
    conflict_of = {(v.source.file_id, c.field): c for c in res.conflicts for v in c.values}
    items = []
    for d in res.kept:
        for f in ACTION_DATE_FIELDS:
            r = d.facts.get(f)
            if not r or (f == "due_date" and d.get("payment_status") == "paid"):
                continue
            left = (date.fromisoformat(r["value"]) - today).days
            if -30 <= left <= days:  # also show what expired in the last month
                items.append(UpcomingItem(fact=records.fact(r), document=Path(d.path).name, path=d.path, doc_type=d.doc_type,
                                          days_left=left, reminder_status="created" if r["id"] in reminded else None,
                                          conflict=conflict_of.get((d.id, f))))
    return sorted(items, key=lambda i: i.days_left)


@app.post("/reminders")
def add_reminder(req: ReminderReq, conn=Depends(get_conn)):
    """The user's click on "Add to Google Calendar" is the approval; nothing else creates events."""
    r = conn.execute("SELECT fa.*, f.path, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.id=?", (req.fact_id,)).fetchone()
    if not r or r["field"] not in ACTION_DATE_FIELDS:
        raise HTTPException(400, "This fact is not a date Sniff can remind you about")
    who = HOLDER_FIELDS + ORG_FIELDS
    holder = conn.execute(f"SELECT value FROM facts WHERE file_id=? AND field IN ({','.join('?' * len(who))})", (r["file_id"], *who)).fetchone()
    title = f"{(r['doc_type'] or 'document').replace('_', ' ').capitalize()} {REMINDER_TITLES[r['field']]}" + (f" ({holder['value']})" if holder else "")
    try:
        event_id = gcal.create_event(title, date.fromisoformat(r["value"]), f"From {Path(r['path']).name}, page {r['page']}. Added by Sniff.")  # file name only: the full path stays on the laptop
    except Exception as e:
        raise HTTPException(502, f"Could not create the calendar event: {e}")
    conn.execute("INSERT INTO reminders(fact_id, title, date, calendar_event_id, status, created_at) VALUES (?,?,?,?, 'created', datetime('now'))",
                 (r["id"], title, r["value"], event_id))
    db.log(conn, "reminder", json.dumps({"fact_id": r["id"], "event_id": event_id}))
    conn.commit()
    return {"status": "created", "event_id": event_id, "title": title}


@app.post("/open")
def open_file(req: OpenReq, conn=Depends(get_conn)):
    r = conn.execute("SELECT path FROM files WHERE id=?", (req.file_id,)).fetchone()
    if not r or not Path(r["path"]).exists():
        raise HTTPException(404, "File not found")
    uri = Path(r["path"]).as_uri() + (f"#page={req.page}" if r["path"].lower().endswith(".pdf") else "")
    webbrowser.open(uri)
    return {"opened": r["path"], "page": req.page}


@app.get("/eval")
def eval_results():
    if not EVAL_DIR.exists():
        return []
    results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EVAL_DIR.glob("*.json")) if not p.stem.endswith("_failures")]
    return [r for r in results if isinstance(r, dict) and "component" in r]  # skip raw files like index_times.json
