"""Settings of Sniff's optional AI layer, kept in the database (key "ai"), so the app, the background worker and the
MCP server read the same ones. AI is off until the user picks a provider:
- anthropic: Claude with the user's own key (claude-haiku-5-5 unless they name another model)
- openai: any OpenAI-compatible endpoint (OpenAI, OpenRouter, Groq, Gemini, Ollama, LM Studio, llama.cpp) with a base
  URL, a model and a key; a local server needs no key
- local: this laptop's bundled llama-server, when its model file is downloaded
The key is kept only in the OS password store (keyring). The database, logs and error messages never hold it.

What a provider other than this laptop may get (llm.py and its callers apply it; GET /ai/preview shows it): the
candidate lines the extractor builds, a question, and the passages for a written answer, never a whole document.
Types hidden from agents by default (MCP) are never sent unless the user allows them here, and ID numbers are masked
as for MCP. SNIFF_AI (off, anthropic, openai or local) overrides the saved provider: a developer switch."""
import json
import os
import sqlite3
from contextlib import closing

from . import config, db
from .ai import server
from .schemas import DOC_TYPES

PROVIDERS = ("off", "anthropic", "openai", "local")
DEFAULT_MODEL = {"anthropic": "claude-haiku-5-5", "local": config.LLM_MODEL}
HIDDEN = [t for t in DOC_TYPES if t not in config.MCP_DEFAULT]  # id_card, passport, bank_statement, ... and other
KEYRING = "Sniff"  # service name in the OS password store; the user name is ai-<provider>


def load(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key='ai'").fetchone()
    s = {"provider": "off", "base_url": "", "model": "", "allow": []} | (json.loads(row["value"]) if row else {})
    if env := os.environ.get("SNIFF_AI"):
        s["provider"] = env
    return s


def current() -> dict:
    """The saved settings, read afresh, so a change in the app applies to the next call in every process."""
    with closing(db.connect()) as conn:
        return load(conn)


def remote(s: dict) -> bool:
    """The text goes to a provider off this laptop (a local Ollama counts too: Sniff cannot see where it forwards)."""
    return s["provider"] in ("anthropic", "openai")


def may_send(s: dict, doc_type: str | None) -> bool:
    """May text of a document of this type go to the provider? Unclassified documents count as "other"."""
    t = doc_type or "other"
    return not remote(s) or t not in HIDDEN or t in s["allow"]


def local_available() -> bool:
    return (config.MODELS_DIR / server.MODEL_FILE).exists()


def key(provider: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING, f"ai-{provider}")
    except Exception:  # no store or a locked one: as if no key were saved
        return None


def save(conn: sqlite3.Connection, provider: str, base_url: str = "", model: str = "", allow: list[str] | None = None,
         new_key: str | None = None) -> dict:
    """Validate and store. new_key: None keeps the saved key, "" removes it. Raises ValueError with a plain reason."""
    allow = list(dict.fromkeys(allow or []))
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    if bad := [t for t in allow if t not in HIDDEN]:
        raise ValueError(f"Not a hidden document type: {', '.join(bad)}")
    base_url, model = base_url.strip(), model.strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("The base URL must start with http:// or https://.")
    if provider == "local" and not local_available():
        raise ValueError("This laptop's model is not downloaded.")
    if new_key is not None and provider in ("anthropic", "openai"):
        try:
            import keyring

            if new_key.strip():
                keyring.set_password(KEYRING, f"ai-{provider}", new_key.strip())
            elif key(provider):
                keyring.delete_password(KEYRING, f"ai-{provider}")
        except Exception:
            raise ValueError("The system's password store is not available, so the key was not saved.") from None
    conn.execute("INSERT INTO settings(key, value) VALUES ('ai', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps({"provider": provider, "base_url": base_url, "model": model, "allow": allow}),))
    conn.commit()
    return load(conn)


def payload(s: dict) -> dict:
    """What GET and PUT /ai/settings return: never the key, only whether one is saved."""
    return {**s, "key_saved": bool(key(s["provider"])), "hidden": HIDDEN, "local_available": local_available(),
            "default_model": DEFAULT_MODEL.get(s["provider"], "")}
