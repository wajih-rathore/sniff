"""MCP settings kept in the database, so the user can change them in the app and the running MCP server notices.
Off by default. `allow` is {document type: fields} (["*"] is every field and the page text); until the user saves a
list, config.MCP_ALLOW is the default. If SNIFF_MCP_ALLOW is set, it wins and MCP is on (a developer override)."""
import json
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
