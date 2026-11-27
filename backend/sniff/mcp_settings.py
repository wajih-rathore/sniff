"""MCP settings kept in the database, so the user can change them in the app and the running MCP server notices.
Off by default. `allow` is {document type: fields} (["*"] is every field and the page text); until the user saves a
list, config.MCP_ALLOW is the default. If SNIFF_MCP_ALLOW is set, it wins and MCP is on (a developer override)."""
import json
import re
import sqlite3

from . import config
from .schemas import DOC_TYPES, FIELDS


def validate(allow: dict) -> dict[str, list[str]]:
    """Raise ValueError for an unknown document type or field. Types without field rules (other) only take "*"."""
    out = {}
    for doc_type, fields in allow.items():
        if doc_type not in DOC_TYPES:
            raise ValueError(f"Unknown document type: {doc_type}")
        known = set(FIELDS.get(doc_type, {})) | {"*"}
        if bad := [f for f in fields if f not in known]:
            raise ValueError(f"Unknown field for {doc_type}: {', '.join(map(str, bad))}")
        out[doc_type] = list(dict.fromkeys(fields))
    return out


def load(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key='mcp'").fetchone()
    saved = json.loads(row["value"]) if row else {}
    if config.MCP_ENV_OVERRIDE:
        return {"enabled": True, "allow": config.MCP_ALLOW, "env_override": True}
    return {"enabled": bool(saved.get("enabled", False)), "allow": saved.get("allow", config.MCP_ALLOW), "env_override": False}


def save(conn: sqlite3.Connection, enabled: bool, allow: dict) -> dict:
    allow = validate(allow)
    conn.execute("INSERT INTO settings(key, value) VALUES ('mcp', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps({"enabled": enabled, "allow": allow}),))
    conn.commit()
    return load(conn)


DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d+)?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}-?[A-Za-z]{3,9}-?\d{2,4}|[A-Za-z]{3,9}-\d{1,2}-\d{2,4}")
AMOUNT = re.compile(r"(?i)(?:rs|pkr|usd|aed|inr)\d+")  # "Rs12500" as OCR writes it


def mask_ids(text: str) -> str:
    """Hide ID-like tokens (keep the last 4 characters), whatever the document type: in what agents get (MCP) and what
    an AI provider gets (llm.outgoing). A token is a run of letters,
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
