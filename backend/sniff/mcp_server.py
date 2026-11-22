"""MCP server: lets Claude Code, Cursor and other agents look things up in Sniff instead of reading files.
Read-only. ID numbers are masked. Run: uv run python -m sniff.mcp_server
"""
import re
from contextlib import closing

from mcp.server.mcpserver import MCPServer

from . import ask as ask_mod
from . import db
from .search import search

mcp = MCPServer("sniff")


def mask_ids(text: str) -> str:
    """Hide long ID-like numbers (keep the last 4 digits); leave dates alone."""
    def hide(m):
        s = m.group()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) or sum(c.isdigit() for c in s) < 9:
            return s
        return re.sub(r"\d", "•", s[:-4]) + s[-4:]
    return re.sub(r"\d[\d-]{7,}\d", hide, text)


@mcp.tool()
def search_documents(query: str, k: int = 5) -> list[dict]:
    """Search the user's indexed documents by meaning and keywords. Returns file, page and a short snippet."""
    with closing(db.connect()) as conn:
        return [{"file": h.path, "page": h.page, "type": h.doc_type, "snippet": mask_ids(h.snippet)} for h in search(conn, query, k=k)]


@mcp.tool()
def ask(question: str) -> dict:
    """Answer a question from the user's documents. Totals, counts and dates are computed from extracted facts, with sources."""
    with closing(db.connect()) as conn:
        a = ask_mod.ask(conn, question)
    return {"answer": mask_ids(a.answer), "route": a.route, "sources": [f"{s.path} (page {s.page})" for s in a.sources]}


@mcp.tool()
def list_documents(doc_type: str | None = None) -> list[dict]:
    """List indexed documents with their type and extracted facts. doc_type: id_card, utility_bill, certificate, receipt, other."""
    with closing(db.connect()) as conn:
        rows = conn.execute("SELECT * FROM files WHERE (? IS NULL OR doc_type=?) ORDER BY path", (doc_type, doc_type)).fetchall()
        return [
            {"file": r["path"], "type": r["doc_type"],
             "facts": {f["field"]: mask_ids(f["value"]) for f in conn.execute("SELECT field, value FROM facts WHERE file_id=?", (r["id"],))}}
            for r in rows
        ]


if __name__ == "__main__":
    mcp.run()
