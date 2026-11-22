import json
import threading
import webbrowser
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config, db, gcal
from .ask import ask
from .ingest import scan
from .schemas import ACTION_DATE_FIELDS, Answer, Document, Fact, Hit, UpcomingItem
from .search import search

app = FastAPI(title="Sniff")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATUS = {"running": False, "done": 0, "total": 0, "current": None, "errors": 0}
EVAL_DIR = config.ROOT / "eval" / "results"
REMINDER_TITLES = {"expiry_date": "expires", "due_date": "payment due", "warranty_until": "warranty ends"}


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class IndexReq(BaseModel):
    folder: str
    ai: bool = True


class AskReq(BaseModel):
    question: str


class ReminderReq(BaseModel):
    fact_id: int


class OpenReq(BaseModel):
    file_id: int
    page: int = 1


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
                db.delete_file_data(conn, fid)
                conn.execute("DELETE FROM files WHERE id=?", (fid,))
        conn.commit()
        answer = ask(conn, req.question)
    return answer


def _facts(conn, file_id: int) -> list[Fact]:
    return [
        Fact(id=r["id"], file_id=r["file_id"], field=r["field"], value=r["value"], value_type=r["value_type"],
             page=r["page"], verified=bool(r["verified"]))
        for r in conn.execute("SELECT * FROM facts WHERE file_id=? ORDER BY id", (file_id,))
    ]


def _document(conn, r) -> Document:
    return Document(id=r["id"], path=r["path"], name=Path(r["path"]).name, doc_type=r["doc_type"],
                    indexed_at=r["indexed_at"], status=r["status"], facts=_facts(conn, r["id"]))


@app.get("/documents", response_model=list[Document])
def documents(type: str | None = None, conn=Depends(get_conn)):
    sql, args = ("SELECT * FROM files WHERE doc_type=? ORDER BY path", (type,)) if type else ("SELECT * FROM files ORDER BY path", ())
    return [_document(conn, r) for r in conn.execute(sql, args)]


@app.get("/documents/{file_id}", response_model=Document)
def document(file_id: int, conn=Depends(get_conn)):
    r = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if not r:
        raise HTTPException(404, "No such document")
    return _document(conn, r)


@app.get("/upcoming", response_model=list[UpcomingItem])
def upcoming(days: int = 90, conn=Depends(get_conn)):
    today = date.today()
    rows = conn.execute(
        f"SELECT fa.*, f.path, f.doc_type, r.status AS reminder_status FROM facts fa JOIN files f ON f.id=fa.file_id "
        f"LEFT JOIN reminders r ON r.fact_id=fa.id AND r.status='created' "
        f"WHERE fa.field IN ({','.join('?' * len(ACTION_DATE_FIELDS))})",
        ACTION_DATE_FIELDS,
    ).fetchall()
    items = []
    for r in rows:
        left = (date.fromisoformat(r["value"]) - today).days
        if -30 <= left <= days:  # also show what expired in the last month
            fact = Fact(id=r["id"], file_id=r["file_id"], field=r["field"], value=r["value"], value_type=r["value_type"],
                        page=r["page"], verified=bool(r["verified"]))
            items.append(UpcomingItem(fact=fact, document=Path(r["path"]).name, path=r["path"], doc_type=r["doc_type"],
                                      days_left=left, reminder_status=r["reminder_status"]))
    return sorted(items, key=lambda i: i.days_left)


@app.post("/reminders")
def add_reminder(req: ReminderReq, conn=Depends(get_conn)):
    """The user's click on "Add to Google Calendar" is the approval; nothing else creates events."""
    r = conn.execute("SELECT fa.*, f.path, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.id=?", (req.fact_id,)).fetchone()
    if not r or r["field"] not in ACTION_DATE_FIELDS:
        raise HTTPException(400, "This fact is not a date Sniff can remind you about")
    holder = conn.execute("SELECT value FROM facts WHERE file_id=? AND field IN ('name','holder_name','provider','merchant')", (r["file_id"],)).fetchone()
    title = f"{(r['doc_type'] or 'document').replace('_', ' ').capitalize()} {REMINDER_TITLES[r['field']]}" + (f" ({holder['value']})" if holder else "")
    try:
        event_id = gcal.create_event(title, date.fromisoformat(r["value"]), f"From {r['path']}, page {r['page']}. Added by Sniff.")
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
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EVAL_DIR.glob("*.json")) if not p.stem.endswith("_failures")]
