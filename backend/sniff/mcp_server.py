"""MCP server: lets Claude Code, Cursor and other agents look things up in Sniff instead of reading files.

Limits (feedback point 10):
- stdio only. There is no network port, so only the agent the user starts it from can call it.
- Read-only. No tool writes to the database, the files or the calendar.
- Off until the user turns it on (settings table, see mcp_settings.py). While off, every tool refuses and the call is logged.
  The settings are read on every call, so a change in the app takes effect without a restart.
- the allow-list (saved settings, else config.MCP_ALLOW) lists the document types an agent may read and their fields. Other types are hidden: their
  files, facts and page text are never returned. Page text (search snippets, search answers) is given only for
  types allowed with every field ("*"), since text cannot be cut down to a few fields. ID cards are hidden by default.
- ID numbers are masked (last 4 characters kept), staff-style ones like EMP-27986 too.
- A refused call says why ("agent access is off", "this type is hidden"), so the agent can tell the user.
- Every call is appended to .sniff/mcp_access.log: time, tool, arguments and the ids of the documents returned.
Run: uv run python -m sniff.mcp_server
"""
import json
import re
from contextlib import closing
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import ask as ask_mod
from . import config, db, mcp_settings
from .search import search

mcp = MCPServer("sniff")
WITHHELD = "Withheld:"  # the start of every withheld answer; withheld() says why


def withheld(types: set) -> str:
    names = ", ".join(sorted((t or "unclassified").replace("_", " ") for t in types)) or "some"
    return f"{WITHHELD} the answer needs {names} documents, or fields or page text of them, that Sniff's settings hide from agents."


DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d+)?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}-?[A-Za-z]{3,9}-?\d{2,4}|[A-Za-z]{3,9}-\d{1,2}-\d{2,4}")
AMOUNT = re.compile(r"(?i)(?:rs|pkr|usd|aed|inr)\d+")  # "Rs12500" as OCR writes it


def mask_ids(text: str) -> str:
    """Hide ID-like tokens (keep the last 4 characters), whatever the document type. A token is a run of letters,
    digits and hyphens (no underscores). It is an ID if it has 9 or more digits (35202-1234567-8), or 5 or more digits
    plus a letter (KHI-21-725642, EMP-27986). Dates (2026-11-24, 06-Jan-2025), year ranges, plain amounts (1200,
    4,320.50) and amounts with a currency (Rs12500) are left alone."""
    def hide(m):
        s = m.group()
        digits = sum(c.isdigit() for c in s)
        if DATE.fullmatch(s) or AMOUNT.fullmatch(s) or (digits < 9 and not (digits >= 5 and re.search("[A-Za-z]", s))):
            return s
        return re.sub(r"\d", "•", s[:-4]) + s[-4:]
    return re.sub(r"(?<![A-Za-z0-9-])[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9](?![A-Za-z0-9-])", hide, text)


REFUSED = "Agent access is turned off in Sniff's settings. The user can turn it on in Sniff."
_allow: dict[str, list[str]] = {}  # set by gate() at the start of each call; the server handles one call at a time


def gate(tool: str, args: dict) -> None:
    """Read the settings afresh. If MCP is off, log the call and refuse it."""
    global _allow
    with closing(db.connect()) as conn:
        s = mcp_settings.load(conn)
    _allow = s["allow"]
    if not s["enabled"]:
        log(tool, args, [], refused=True)
        raise ToolError(REFUSED)  # a ToolError's text reaches the agent; any other exception becomes "Error executing tool"


def allowed(doc_type: str | None, field: str | None = None) -> bool:
    """May an agent see documents of this type (and this field of them)? Unclassified documents are hidden."""
    fields = _allow.get(doc_type or "", [])
    return bool(fields) and (field is None or "*" in fields or field in fields)


def text_allowed(doc_type: str | None) -> bool:
    return "*" in _allow.get(doc_type or "", [])


def log(tool: str, args: dict, ids: list[int], refused: bool = False) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DATA_DIR / "mcp_access.log", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "tool": tool, "args": args, "ids": ids, "refused": refused}) + "\n")


@mcp.tool()
def search_documents(query: str, k: int = 5) -> list[dict]:
    """Search the user's indexed documents by meaning and keywords. Returns file, page and a short snippet."""
    gate("search_documents", {"query": query, "k": k})
    with closing(db.connect()) as conn:
        hits = [h for h in search(conn, query, k=k * 4) if text_allowed(h.doc_type)][:k]  # fetch extra: hidden types drop out
    log("search_documents", {"query": query, "k": k}, [h.file_id for h in hits])
    return [{"file": h.path, "page": h.page, "type": h.doc_type, "snippet": mask_ids(h.snippet)} for h in hits]


@mcp.tool()
def ask(question: str) -> dict:
    """Answer a question from the user's documents. Totals, counts and dates are computed from extracted facts, with sources."""
    gate("ask", {"question": question})
    with closing(db.connect()) as conn:
        a = ask_mod.ask(conn, question)
        types = {r["id"]: r["doc_type"] for r in conn.execute("SELECT id, doc_type FROM files")}
        ids = {s.file_id for s in a.sources + a.counted + a.skipped_duplicates} | {f.file_id for f in a.facts + a.unreviewed}
        if a.route == "search":  # the model read every passage search gave it, cited or not
            ids |= {h.file_id for h in search(conn, question, k=5)}
            hidden = {types.get(i) for i in ids if not text_allowed(types.get(i))}
        else:
            hidden = ({types.get(i) for i in ids if not allowed(types.get(i))}
                      | {types.get(f.file_id) for f in a.facts + a.unreviewed if not allowed(types.get(f.file_id), f.field)}
                      | {c.doc_type for c in a.conflicts if not allowed(c.doc_type, c.field)})
        ok = not hidden
    out = {"answer": mask_ids(a.answer), "route": a.route, "sources": [f"{s.path} (page {s.page})" for s in a.sources]} if ok \
        else {"answer": withheld(hidden), "route": a.route, "sources": []}
    log("ask", {"question": question}, sorted({s.file_id for s in a.sources}) if ok else [])
    return out


@mcp.tool()
def list_documents(doc_type: str | None = None) -> list[dict]:
    """List indexed documents with their type and extracted facts. doc_type: utility_bill, certificate, receipt, warranty_card,
    fee_voucher, insurance_policy, tenancy_agreement, other.
    Types the user has not allowed are not listed."""
    gate("list_documents", {"doc_type": doc_type})
    if doc_type and not allowed(doc_type):
        log("list_documents", {"doc_type": doc_type}, [], refused=True)
        raise ToolError(f"{doc_type.replace('_', ' ').capitalize()} documents are hidden from agents in Sniff's settings.")
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
