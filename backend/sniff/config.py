import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
DATA_DIR = Path(os.environ.get("SNIFF_DATA", ROOT / ".sniff"))
DB_PATH = Path(os.environ.get("SNIFF_DB", DATA_DIR / "sniff.db"))
MODELS_DIR = Path(os.environ.get("SNIFF_MODELS", ROOT / "models"))
SECRETS_DIR = ROOT / "backend" / ".secrets"

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
# Types not listed are hidden, so ID cards are hidden by default. ID numbers are always masked.
MCP_ALLOW: dict[str, list[str]] = json.loads(os.environ.get("SNIFF_MCP_ALLOW", "null")) or {
    "utility_bill": ["*"], "certificate": ["*"], "receipt": ["*"], "other": ["*"]}
