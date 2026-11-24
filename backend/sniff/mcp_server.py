"""MCP server: lets Claude Code, Cursor and other agents look things up in Sniff instead of reading files.

Limits (feedback point 10):
- stdio only. There is no network port, so only the agent the user starts it from can call it.
- Read-only. No tool writes to the database, the files or the calendar.
- config.MCP_ALLOW lists the document types an agent may read and their fields. Other types are hidden: their
  files, facts and page text are never returned. Page text (search snippets, search answers) is given only for
  types allowed with every field ("*"), since text cannot be cut down to a few fields. ID cards are hidden by default.
- ID numbers are masked (last 4 digits kept).
- Every call is appended to .sniff/mcp_access.log: time, tool, arguments and the ids of the documents returned.
Run: uv run python -m sniff.mcp_server
"""
import json
import re
from contextlib import closing
from datetime import datetime

from mcp.server.mcpserver import MCPServer

from . import ask as ask_mod
from . import config, db
from .search import search

mcp = MCPServer("sniff")
WITHHELD = "Withheld: the answer would use documents or fields this agent is not allowed to read."


DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d+)?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}")


def mask_ids(text: str) -> str:
    """Hide ID-like tokens (keep the last 4 characters), whatever the document type. A token is a run of letters,
    digits and hyphens (no underscores). It is an ID if it has 9 or more digits (35202-1234567-8), or 6 or more digits
    plus a letter (KHI-21-725642). Dates, year ranges and plain amounts (1200, 4,320.50) are left alone."""
    def hide(m):
        s = m.group()
        digits = sum(c.isdigit() for c in s)
        if DATE.fullmatch(s) or (digits < 9 and not (digits >= 6 and re.search("[A-Za-z]", s))):
            return s
        return re.sub(r"\d", "•", s[:-4]) + s[-4:]
    return re.sub(r"(?<![A-Za-z0-9-])[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9](?![A-Za-z0-9-])", hide, text)


def allowed(doc_type: str | None, field: str | None = None) -> bool:
    """May an agent see documents of this type (and this field of them)? Unclassified documents are hidden."""
    fields = config.MCP_ALLOW.get(doc_type or "", [])
    return bool(fields) and (field is None or "*" in fields or field in fields)


def text_allowed(doc_type: str | None) -> bool:
    return "*" in config.MCP_ALLOW.get(doc_type or "", [])


def log(tool: str, args: dict, ids: list[int]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DATA_DIR / "mcp_access.log", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "tool": tool, "args": args, "ids": ids}) + "\n")


@mcp.tool()
def search_documents(query: str, k: int = 5) -> list[dict]:
    """Search the user's indexed documents by meaning and keywords. Returns file, page and a short snippet."""
    with closing(db.connect()) as conn:
        hits = [h for h in search(conn, query, k=k * 4) if text_allowed(h.doc_type)][:k]  # fetch extra: hidden types drop out
    log("search_documents", {"query": query, "k": k}, [h.file_id for h in hits])
    return [{"file": h.path, "page": h.page, "type": h.doc_type, "snippet": mask_ids(h.snippet)} for h in hits]


@mcp.tool()
def ask(question: str) -> dict:
    """Answer a question from the user's documents. Totals, counts and dates are computed from extracted facts, with sources."""
    with closing(db.connect()) as conn:
        a = ask_mod.ask(conn, question)
        types = {r["id"]: r["doc_type"] for r in conn.execute("SELECT id, doc_type FROM files")}
        ids = {s.file_id for s in a.sources + a.counted + a.skipped_duplicates} | {f.file_id for f in a.facts + a.unreviewed}
        if a.route == "search":  # the model read every passage search gave it, cited or not
            ids |= {h.file_id for h in search(conn, question, k=5)}
            ok = all(text_allowed(types.get(i)) for i in ids)
        else:
            ok = (all(allowed(types.get(i)) for i in ids) and all(allowed(types.get(f.file_id), f.field) for f in a.facts + a.unreviewed)
                  and all(allowed(c.doc_type, c.field) for c in a.conflicts))
    out = {"answer": mask_ids(a.answer), "route": a.route, "sources": [f"{s.path} (page {s.page})" for s in a.sources]} if ok \
        else {"answer": WITHHELD, "route": a.route, "sources": []}
    log("ask", {"question": question}, sorted({s.file_id for s in a.sources}) if ok else [])
    return out


@mcp.tool()
def list_documents(doc_type: str | None = None) -> list[dict]:
    """List indexed documents with their type and extracted facts. doc_type: utility_bill, certificate, receipt, other.
    Types the user has not allowed are not listed."""
    with closing(db.connect()) as conn:
        rows = [r for r in conn.execute("SELECT * FROM files WHERE copy_of IS NULL AND (? IS NULL OR doc_type=?) ORDER BY path",
                                        (doc_type, doc_type)) if allowed(r["doc_type"])]
        out = [{"file": r["path"], "type": r["doc_type"],
                "facts": {f["field"]: mask_ids(f["value"]) for f in conn.execute("SELECT field, value FROM facts WHERE file_id=?", (r["id"],))
                          if allowed(r["doc_type"], f["field"])}}
               for r in rows]
    log("list_documents", {"doc_type": doc_type}, [r["id"] for r in rows])
    return out


if __name__ == "__main__":
    mcp.run(transport="stdio")
