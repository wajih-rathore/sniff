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

EMBED_MODEL = os.environ.get("SNIFF_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("SNIFF_EMBED_DIM", "384"))

SUPPORTED = {".pdf", ".docx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
