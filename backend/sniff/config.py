import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root


def app_home(platform: str = sys.platform, env: Mapping[str, str] = os.environ) -> Path:
    """Where the desktop app keeps data, models and logs on each system. The same rule as home() in
    desktop/src-tauri/src/platform.rs. Relative XDG paths are ignored, as the XDG spec asks."""
    if platform == "win32":
        return Path(env["LOCALAPPDATA"], "Sniff")
    if platform == "darwin":
        return Path(env["HOME"], "Library", "Application Support", "Sniff")
    xdg = env.get("XDG_DATA_HOME", "")
    return Path(xdg if xdg.startswith("/") else Path(env["HOME"], ".local", "share"), "sniff")


# The desktop app sets SNIFF_HOME (app_home()): data, models and secrets then live under it. A frozen back end
# started on its own uses the same folder; a source checkout keeps its data in the repo.
HOME = os.environ.get("SNIFF_HOME") or (str(app_home()) if getattr(sys, "frozen", False) else None)
DATA_DIR = Path(os.environ.get("SNIFF_DATA", Path(HOME, "data") if HOME else ROOT / ".sniff"))
DB_PATH = Path(os.environ.get("SNIFF_DB", DATA_DIR / "sniff.db"))
MODELS_DIR = Path(os.environ.get("SNIFF_MODELS", Path(HOME, "models") if HOME else ROOT / "models"))
SECRETS_DIR = Path(HOME, "secrets") if HOME else ROOT / "backend" / ".secrets"

# any OpenAI-compatible server: llama.cpp llama-server (default), Ollama, or the team server
LLM_URL = os.environ.get("SNIFF_LLM_URL", "http://127.0.0.1:8080/v1")
LLM_MODEL = os.environ.get("SNIFF_LLM_MODEL", "qwen3-1.7b")

# a second llama-server started with --embedding. all-MiniLM-L6-v2 (384) by default; nomic-embed-text (768) is the
# alternative under evaluation. (Ollama's EmbeddingGemma GGUF fails to load in llama.cpp: 2 tensors missing.)
EMBED_URL = os.environ.get("SNIFF_EMBED_URL", "http://127.0.0.1:8081/v1")
EMBED_MODEL = os.environ.get("SNIFF_EMBED_MODEL", "all-minilm")
EMBED_DIM = int(os.environ.get("SNIFF_EMBED_DIM", "384"))

SUPPORTED = {".pdf", ".docx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

# MCP: what an outside agent may read, as {document type: fields}. ["*"] means every field and the page text too.
# Types not listed are hidden: ID cards, passports, bank statements, offer letters and transcripts. Every other type with field rules is
# open with a list of fields that name no person and give no ID, account, policy or serial number or address. A list
# also means no page text. "other" (unclassified files, where a licence once landed) is hidden too: the user can grant
# it in settings. ID numbers are always masked. This is only the default: the settings saved in the database (mcp_settings.py)
# win, unless the env var SNIFF_MCP_ALLOW is set, which is a developer override of both the list and the on/off switch.
_MCP_ENV = json.loads(os.environ.get("SNIFF_MCP_ALLOW", "null"))
MCP_ENV_OVERRIDE = bool(_MCP_ENV)
MCP_DEFAULT: dict[str, list[str]] = {
    "utility_bill": ["provider", "billing_month", "bill_date", "due_date", "current_charges", "previous_balance", "tax", "amount_due",
                     "payment_status", "payment_date"],
    "certificate": ["title", "institution", "issue_date"],
    "receipt": ["merchant", "date", "subtotal", "tax", "total_amount", "warranty_until"],
    "warranty_card": ["product", "brand", "purchase_date", "warranty_until"],
    "fee_voucher": ["institution", "semester", "amount", "issue_date", "due_date"],
    "insurance_policy": ["insurer", "premium", "start_date", "end_date"],
    "tenancy_agreement": ["monthly_rent", "security_deposit", "start_date", "end_date"]}
MCP_ALLOW: dict[str, list[str]] = _MCP_ENV or MCP_DEFAULT
