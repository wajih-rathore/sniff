import json
import re
import threading
from collections import Counter
import webbrowser
from contextlib import asynccontextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import ai_settings, config, db, gcal, ics, mcp_settings, records, titles, verify, watch
from . import ask as ask_mod
from .ai import classify, extract, llm, server, typer
from .ask import ask, ask_stream
from .ingest import readers, scan
from .schemas import (ACTION_DATE_FIELDS, DOC_TYPES, EXPIRY_FIELDS, FIELDS, HOLDER_FIELDS, ORG_FIELDS, Answer, Conflict, Document, Fact, Hit, MissingFields, Overview,
                      ReviewItem, UpcomingItem)
from .search import search

STATUS = {"running": False, "done": 0, "total": 0, "current": None, "errors": 0, "watching": []}
WATCHER = watch.Watcher(STATUS)
WORKER = scan.FactsWorker()  # reads types and facts of new files in the background


@asynccontextmanager
async def lifespan(_):
    WATCHER.restore()  # folders watched before the last shutdown
    WORKER.start()  # also finishes the queue left by the last run
    yield
    WATCHER.stop()
    WORKER.stop()


app = FastAPI(title="Sniff", lifespan=lifespan)
# Only Sniff's own window may read the API: the Tauri app (tauri.localhost on Windows, tauri://localhost elsewhere) and
# a launcher served from this machine. Any other website gets no CORS headers, so it cannot read documents.
app.add_middleware(CORSMiddleware, allow_origins=["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"],
                   allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["*"], allow_headers=["*"])
# A DNS-rebinding page reaches 127.0.0.1 under its own host name; refuse every host but this machine's.
# "testserver" is the host FastAPI's TestClient uses.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, e: RequestValidationError):
    """FastAPI's 422 without the rejected input: it can be an API key (PUT /ai/settings without a provider)."""
    return JSONResponse({"detail": jsonable_encoder([{k: v for k, v in err.items() if k != "input"} for err in e.errors()])}, 422)


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


class McpSettings(BaseModel):
    enabled: bool
    allow: dict[str, list[str]]


class AiSettingsReq(BaseModel):
    provider: str
    base_url: str = ""
    model: str = ""
    allow: list[str] = []
    key: str | None = None  # write-only: None keeps the saved key, "" removes it; never returned


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
    if STATUS.get("indexing"):  # another /index request; a watcher scan does not count, /index waits for it
        raise HTTPException(409, "Indexing is already running")
    STATUS.update(indexing=True, running=True)

    def work():
        conn = db.connect()
        try:
            STATUS["last"] = scan.scan(conn, folder, ai=req.ai, status=STATUS)
        finally:
            STATUS.update(indexing=False, running=False)
            conn.close()

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/index/status")
def index_status(conn=Depends(get_conn)):
    return {**STATUS, "queued": scan.waiting(conn)}  # queued: files whose type and facts are not read yet


@app.get("/watch")
def watch_list():
    """counts: per folder, its documents, those still waiting for their facts, and those that could not be read."""
    conn = db.connect()
    try:
        counts = {}
        for f in WATCHER.folders():
            prefix = f.rstrip("\\/") + ("\\" if "\\" in f else "/")
            r = conn.execute("SELECT count(*) AS files, coalesce(sum(status='error'), 0) AS errors, "
                             "coalesce(sum(id IN (SELECT file_id FROM queue)), 0) AS waiting "
                             "FROM files WHERE copy_of IS NULL AND substr(path, 1, ?)=?", (len(prefix), prefix)).fetchone()
            counts[f] = dict(r)
    finally:
        conn.close()
    return {"folders": WATCHER.folders(), "last": STATUS.get("watch_last"), "error": STATUS.get("watch_error"), "counts": counts}


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
    WORKER.user_active()  # someone is typing: the background reading waits, so an answer gets the model at once
    return titles.describe(conn, q, search(conn, q, k=k, mode=mode))


@app.post("/ask", response_model=Answer)
def ask_route(req: AskReq, conn=Depends(get_conn)):
    WORKER.user_active()
    answer = ask(conn, req.question)
    if _refresh_stale(conn, answer):
        answer = ask(conn, req.question)
    return titles.name_sources(conn, answer)


@app.post("/ask/stream")
def ask_stream_route(req: AskReq):
    """/ask as server-sent events: "token" events while a search answer is being written, then one "answer" event with
    the whole Answer, as /ask returns it. "error" if it fails."""
    WORKER.user_active()

    def events():
        conn = db.connect()  # its own connection: the stream outlives the request handler
        try:
            for kind, item in ask_stream(conn, req.question):
                WORKER.user_active()
                if kind == "answer" and _refresh_stale(conn, item):
                    item = ask(conn, req.question)
                if kind == "answer":
                    item = titles.name_sources(conn, item)
                yield f"event: {kind}\ndata: {item.model_dump_json() if kind == 'answer' else json.dumps(item)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(f'{type(e).__name__}: {e}')}\n\n"
        finally:
            conn.close()
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _refresh_stale(conn, answer: Answer) -> bool:
    """A cited file changed after indexing: index it again, both phases now, so the answer can be asked again."""
    stale = [s.file_id for s in answer.sources if scan.is_stale(conn, s.file_id)]
    for fid in stale:
        path = Path(conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()["path"])
        if path.exists():
            scan.index_file(conn, path, scan.sha256(path))
        else:
            db.release(conn, fid)  # a copy in another folder keeps the document
            conn.execute("DELETE FROM files WHERE id=?", (fid,))
    conn.commit()
    return bool(stale)


def _facts(conn, file_id: int) -> list[Fact]:
    return [records.fact(r) for r in conn.execute("SELECT * FROM facts WHERE file_id=? ORDER BY id", (file_id,))]


def _paths(conn, file_id: int) -> list[str]:
    """Every path holding this document: its own first, then exact copies in other folders."""
    return [r["path"] for r in conn.execute("SELECT path FROM files WHERE id=? OR copy_of=? ORDER BY copy_of IS NOT NULL, id", (file_id, file_id))]


def _document(conn, r, near: list[int] | None = None) -> Document:
    pages = conn.execute("SELECT max(page) FROM chunks WHERE file_id=?", (r["id"],)).fetchone()[0] or 1
    facts = _facts(conn, r["id"])
    name = titles.title(r["doc_type"], {f.field: f.value for f in reversed(facts) if f.status != "superseded"}, r["path"])
    return Document(id=r["id"], path=r["path"], name=Path(r["path"]).name, doc_type=r["doc_type"], indexed_at=r["indexed_at"],
                    status=r["status"], facts=facts, paths=_paths(conn, r["id"]), near_duplicates=near or [], pages=pages, title=name)


def _canonical(conn, file_id: int):
    r = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if r and r["copy_of"]:  # a copy's id leads to the document it copies
        r = conn.execute("SELECT * FROM files WHERE id=?", (r["copy_of"],)).fetchone()
    if not r:
        raise HTTPException(404, "No such document")
    return r


@app.get("/documents", response_model=list[Document])
def documents(type: str | None = None, recent: int | None = None, conn=Depends(get_conn)):
    """recent=N: the N documents read most recently, newest first (the launcher's empty state)."""
    sql = "SELECT * FROM files WHERE copy_of IS NULL" + (" AND doc_type=?" if type else "")
    sql += " AND status='indexed' ORDER BY indexed_at DESC, id DESC LIMIT ?" if recent else " ORDER BY path"
    return [_document(conn, r) for r in conn.execute(sql, (*([type] if type else []), *([recent] if recent else [])))]


@app.get("/documents/{file_id}", response_model=Document)
def document(file_id: int, conn=Depends(get_conn)):
    """The review view: the document, all its paths, its facts with status, reason, page and source text."""
    r = _canonical(conn, file_id)
    near = []
    if r["doc_type"] in FIELDS:
        docs = records.load(conn, r["doc_type"])
        me = next((d for d in docs if d.id == r["id"]), None)
        near = [d.id for d in docs if me and d.id != me.id and records.same_record(me, d)]
    return _document(conn, r, near)


@app.get("/documents/{file_id}/pages/{n}.png")
def page_image(file_id: int, n: int, conn=Depends(get_conn)):
    """One page as an image, so the review screen can show the value next to the original."""
    paths = [Path(p) for p in _paths(conn, _canonical(conn, file_id)["id"]) if Path(p).exists()]
    if not paths:
        raise HTTPException(404, "File not found on disk")
    p = paths[0]
    if p.suffix.lower() == ".pdf":
        import pymupdf  # only when a page is shown, so an idle back end does not load it

        with pymupdf.open(str(p)) as doc:
            if not 1 <= n <= len(doc):
                raise HTTPException(404, "No such page")
            return Response(doc[n - 1].get_pixmap(dpi=110).tobytes("png"), media_type="image/png")
    if p.suffix.lower() in readers.IMAGES and n == 1:
        return FileResponse(p)
    raise HTTPException(404, "No page image for this kind of file")


HL = (79 / 255, 70 / 255, 229 / 255)  # the launcher's highlight colour


def _needles(value: str, value_type: str, source_text: str | None) -> list[str]:
    """How a stored value may be printed, most specific first: 5558.0 as "5,558.00", "5,558" or "5558"."""
    out = [value]
    if value_type == "money":
        try:
            x = float(value)
            out = [f"{x:,.2f}", f"{x:,.0f}" if x == int(x) else f"{x:,.2f}", f"{x:.2f}", f"{x:g}"]
        except ValueError:
            pass
    return list(dict.fromkeys(out + ([source_text.strip()] if source_text else [])))


def _spot(page, value: str, value_type: str, source_text: str | None):
    """Where the value is printed: on its source line if that line is found, else anywhere on the page. None for scans."""
    line = page.search_for(source_text.strip()) if source_text and source_text.strip() else []
    for text in _needles(value, value_type, None):
        rects = page.search_for(text)
        if near := [r for r in rects if any(r.intersects(x) for x in line)]:
            return near[0]
        if rects and not line:
            return rects[0]
    return line[0] if line else None


def _snap(top: float, bottom: float, lines: list) -> tuple[float, float]:
    """Move a band's edges inwards until neither cuts through a line of text (PyMuPDF line boxes)."""
    while cut := [r for r in lines if r.y0 < bottom < r.y1]:
        bottom = min(r.y0 for r in cut)
    while cut := [r for r in lines if r.y0 < top < r.y1]:
        top = max(r.y1 for r in cut)
    return top, bottom


@lru_cache(maxsize=32)
def _crop_png(path: str, mtime: float, n: int, value: str | None, value_type: str, source_text: str | None, q: str,
              ar: float = .5) -> bytes:
    import pymupdf  # only when a page is shown, so an idle back end does not load it

    with pymupdf.open(path) as doc:
        if not 1 <= n <= len(doc):
            raise HTTPException(404, "No such page")
        page = doc[n - 1]  # drawn on in memory only; the file is never saved
        w, h = page.rect.width, page.rect.height
        spot = _spot(page, value, value_type, source_text) if value else None
        marks = [r for word in re.findall(r"\w{3,}", q) for r in page.search_for(word)][:20]
        for r in marks:
            page.draw_rect(r, color=None, fill=HL, fill_opacity=.22, width=0)
        if spot:
            page.draw_rect(spot + (-3, -1, 3, 1), color=HL, fill=HL, fill_opacity=.16, width=1.2)
        focus = spot or (marks[0] if marks else None)
        band = min(h, w * ar)  # a strip of the page as tall as the place that shows it (ar = height / width)
        top = 0 if focus is None else min(max(0, (focus.y0 + focus.y1) / 2 - band / 2), h - band)
        lines = [pymupdf.Rect(ln["bbox"]) for b in page.get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"]]
        top, bottom = _snap(top, top + band, lines)  # scans have no lines: their strip is cut where it falls
        band = bottom - top
        z = min(2.0, 1200 / w)  # about 1200 px wide: sharp at 150% scaling, and a phone photo does not become a huge PNG
        return page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=pymupdf.Rect(0, top, w, top + band)).tobytes("png")


@app.get("/documents/{file_id}/crop")
def crop(file_id: int, fact_id: int | None = None, page: int = 1, q: str = "", ar: float = .5, conn=Depends(get_conn)):
    """A PNG strip of a page around a fact's value (outlined), or around the first of the query's words. Without either,
    or on a scan (no text layer to search), the top of the page. ar is the strip's height over its width (the place it
    is shown); the strip ends between lines of text, never through one. Cached by file, page, value, query and ar."""
    ar = round(min(max(ar, .25), 1.5), 2)  # steps of 0.01: one shape per window size, so the cache is reused
    doc = _canonical(conn, file_id)
    fact = _fact_row(conn, fact_id) if fact_id is not None else None
    if fact and fact["file_id"] != doc["id"]:
        raise HTTPException(404, "This fact belongs to another document")
    paths = [Path(p) for p in _paths(conn, doc["id"]) if Path(p).exists()]
    if not paths:
        raise HTTPException(404, "File not found on disk")
    p = paths[0]
    try:
        png = _crop_png(str(p), p.stat().st_mtime, fact["page"] if fact else page, fact["value"] if fact else None,
                        fact["value_type"] if fact else "text", fact["source_text"] if fact else None, q, ar)
    except (RuntimeError, ValueError) as e:  # a format PyMuPDF cannot open, e.g. a Word file
        raise HTTPException(404, f"No page image for this file: {e}")
    return Response(png, media_type="image/png")


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
                        "WHERE fa.status='needs_review' AND f.copy_of IS NULL ORDER BY f.path, fa.id").fetchall()
    names = titles.for_files(conn, {r["file_id"] for r in rows})
    return [ReviewItem(fact=records.fact(r), document=Path(r["path"]).name, path=r["path"], doc_type=r["doc_type"], title=names[r["file_id"]])
            for r in rows]


@app.get("/conflicts", response_model=list[Conflict])
def conflicts(conn=Depends(get_conn)):
    return records.resolve(records.load(conn)).conflicts


class PickReq(BaseModel):
    key: str  # Conflict.key
    fact_id: int  # the value the user says is right


@app.post("/conflicts/pick", response_model=list[Conflict])
def pick_conflict(req: PickReq, conn=Depends(get_conn)):
    """The user's choice settles a conflict: the chosen fact is approved, the other copies' values of that field are
    superseded, so later totals use the choice. Returns the conflicts still open."""
    c = next((c for c in records.resolve(records.load(conn)).conflicts if c.key == req.key and any(v.fact_id == req.fact_id for v in c.values)), None)
    if not c:
        raise HTTPException(404, "No open conflict with this key has that fact as one of its values")
    others = [v.fact_id for v in c.values if v.fact_id != req.fact_id]
    conn.execute("UPDATE facts SET status='approved', reason=NULL WHERE id=?", (req.fact_id,))
    conn.executemany("UPDATE facts SET status='superseded', reason=? WHERE id=?", [(f"The user chose fact {req.fact_id} instead", i) for i in others])
    db.log(conn, "conflict_pick", json.dumps({"key": req.key, "field": c.field, "chosen": req.fact_id, "superseded": others}))
    conn.commit()
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


def _reminder_title(conn, r) -> str:
    who = HOLDER_FIELDS + ORG_FIELDS
    holder = conn.execute(f"SELECT value FROM facts WHERE file_id=? AND field IN ({','.join('?' * len(who))})", (r["file_id"], *who)).fetchone()
    return f"{(r['doc_type'] or 'document').replace('_', ' ').capitalize()} {REMINDER_TITLES[r['field']]}" + (f" ({holder['value']})" if holder else "")


def _reminder_event(conn, r) -> tuple[str, date, str]:
    """What Google Calendar, the .ics file and the preview all receive: title, day and description. The description has
    the file name only: the full path stays on the laptop."""
    return _reminder_title(conn, r), date.fromisoformat(r["value"]), f"From {Path(r['path']).name}, page {r['page']}. Added by Sniff."


def _reminder_fact(conn, file_id: int, field: str):
    if field not in ACTION_DATE_FIELDS:
        raise HTTPException(400, "This is not a date Sniff can remind you about")
    fid = _canonical(conn, file_id)["id"]
    r = conn.execute("SELECT fa.*, f.path, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.file_id=? AND fa.field=? ORDER BY fa.id", (fid, field)).fetchone()
    if not r:
        raise HTTPException(404, "This document has no such date")
    try:
        date.fromisoformat(r["value"])
    except ValueError:
        raise HTTPException(422, f"'{r['value']}' is not a date")
    return r


@app.get("/reminders/ics")
def reminder_ics(file_id: int, field: str, conn=Depends(get_conn)):
    """The same reminder as a .ics file for any calendar app, no Google account needed. The user's download is the approval."""
    r = _reminder_fact(conn, file_id, field)
    title, day, description = _reminder_event(conn, r)
    body = ics.make_ics(f"sniff-{r['id']}@sniff.local", title, day, description)
    db.log(conn, "reminder_ics", json.dumps({"fact_id": r["id"], "file_id": r["file_id"], "field": field}))
    conn.commit()
    return Response(body, media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="sniff-reminder-{r["id"]}.ics"'})


class PreviewReq(BaseModel):
    file_id: int
    field: str


@app.post("/reminders/preview")
def reminder_preview(req: PreviewReq, conn=Depends(get_conn)):
    """Exactly what would be sent to Google or written to the .ics file, and whether a person has looked at the date."""
    r = _reminder_fact(conn, req.file_id, req.field)
    title, day, description = _reminder_event(conn, r)
    return {"fact_id": r["id"], "title": title, "date": day.isoformat(), "description": description, "status": r["status"],
            "reviewed": r["status"] != "needs_review"}


@app.post("/reminders")
def add_reminder(req: ReminderReq, conn=Depends(get_conn)):
    """The user's click on "Add to Google Calendar" is the approval; nothing else creates events."""
    r = conn.execute("SELECT fa.*, f.path, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.id=?", (req.fact_id,)).fetchone()
    if not r or r["field"] not in ACTION_DATE_FIELDS:
        raise HTTPException(400, "This fact is not a date Sniff can remind you about")
    title, day, description = _reminder_event(conn, r)
    try:
        event_id = gcal.create_event(title, day, description)
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


def _mcp_payload(settings: dict) -> dict:
    """The settings plus every type's field names, so the UI does not keep its own copy."""
    return {**settings, "fields": {t: list(FIELDS.get(t, {})) for t in DOC_TYPES}}


@app.get("/model")
def model_status():
    """The chat model: ready, or starting (Sniff started its server and the model is still loading, which takes
    seconds on a cold start). The launcher shows it while an answer waits for the model."""
    ready = server.ready()
    return {"ready": ready, "starting": server.running() and not ready}


@app.get("/calendar/status")
def calendar_status():
    return {"connected": gcal.connected()}


@app.get("/mcp/settings")
def mcp_get(conn=Depends(get_conn)):
    return _mcp_payload(mcp_settings.load(conn))


@app.put("/mcp/settings")
def mcp_put(req: McpSettings, conn=Depends(get_conn)):
    try:
        return _mcp_payload(mcp_settings.save(conn, req.enabled, req.allow))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/mcp/log")
def mcp_log(limit: int = 50):
    """The most recent MCP calls, newest first, from .sniff/mcp_access.log."""
    path = config.DATA_DIR / "mcp_access.log"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(line) for line in reversed(lines[-max(1, min(limit, 500)):])]


@app.get("/ai/settings")
def ai_get(conn=Depends(get_conn)):
    return ai_settings.payload(ai_settings.load(conn))


@app.put("/ai/settings")
def ai_put(req: AiSettingsReq, conn=Depends(get_conn)):
    try:
        return ai_settings.payload(ai_settings.save(conn, req.provider, req.base_url, req.model, req.allow, req.key))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/ai/test")
def ai_test():
    """A one-token call to the saved provider: {"ok": true}, or {"ok": false, "error": a plain reason}."""
    return llm.check()


@app.get("/ai/preview")
def ai_preview(file_id: int | None = None, question: str | None = None, conn=Depends(get_conn)):
    """Exactly what the saved provider would get for one document (its gap filling) or for a question, masked as sent.
    While AI is off nothing is sent, so it shows what a provider off this laptop would get."""
    s = ai_settings.load(conn)
    view = s | {"provider": "anthropic"} if s["provider"] == "off" else s
    remote, note = ai_settings.remote(view), None
    if question:
        calls = ask_mod.outbound(conn, question, view)
    elif file_id is not None:
        r = _canonical(conn, file_id)
        if not Path(r["path"]).exists():
            raise HTTPException(404, "File not found on disk")
        pages, boxes = readers.read_layout(Path(r["path"]))
        doc_type = r["doc_type"]
        if doc_type is None and (doc_type := typer.classify(conn, r["id"], pages)):  # not sorted yet: as the worker will
            note = f"Not sorted yet. The classifier on this laptop reads it as {doc_type.replace('_', ' ')}."
        if doc_type in FIELDS:
            calls = extract.outbound(doc_type, pages, boxes, view)
        elif doc_type is None and not remote:  # this laptop's model reads an unclassified document whole to sort it
            calls = [{"purpose": "Sort the document into a type", "messages": llm.document_message(pages, classify.PROMPT)}]
            note = "This laptop's model sorts it first. What it reads next depends on the type it picks."
        else:
            calls = []
            if doc_type is None:
                note = "Not sorted into a type, so nothing is sent: a provider off this laptop never sorts documents."
    else:
        raise HTTPException(422, "Give a file_id or a question.")
    return {"provider": s["provider"], "leaves_laptop": remote, "note": note,
            "calls": [{**c, "messages": llm.outgoing(c["messages"], remote)} for c in calls]}


@app.get("/eval")
def eval_results():
    if not EVAL_DIR.exists():
        return []
    results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EVAL_DIR.glob("*.json")) if not p.stem.endswith("_failures")]
    return [r for r in results if isinstance(r, dict) and "component" in r]  # skip raw files like index_times.json
