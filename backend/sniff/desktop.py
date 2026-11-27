"""Entry point of the desktop build (frozen with PyInstaller): the API on 127.0.0.1, plus the first-run model download.

The desktop app (desktop/src-tauri) starts this with SNIFF_HOME set, and starts the embedding llama-server once the
model files are on disk. The chat model server is started by this back end only when a model call needs it (ai/server.py). The download screen (frontend/src/views/Setup.tsx) reads GET /setup and starts POST /setup/download.
Imports are absolute so PyInstaller can run this file as the main script.
"""
import hashlib
import http.client
import socket
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

import uvicorn

from sniff import config
from sniff.ai import server
from sniff.api import app

PORT = 8765  # same as desktop/src-tauri/src/main.rs and frontend/src/api.ts
OLLAMA = "https://registry.ollama.ai/v2/library"
# file name in MODELS_DIR: (url, sha256, size). The two models the app runs on, from the same source as eval/fetch_models.py.
MODELS = {
    "all-minilm-l6-v2.gguf": (f"{OLLAMA}/all-minilm/blobs/sha256:797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662",
                              "797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662", 45949216),
    "qwen3-1.7b-q4_k_m.gguf": (f"{OLLAMA}/qwen3/blobs/sha256:3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6",
                               "3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6", 1359279776),
}
CHUNK = 1 << 20
STATE = {"running": False, "error": None}


def fetch(name: str) -> None:
    """Download one model, resuming its .part file with an HTTP Range request. The file gets its final name only
    after its sha256 matches."""
    url, digest, size = MODELS[name]
    dest, part = config.MODELS_DIR / name, config.MODELS_DIR / (name + ".part")
    if dest.exists():
        return
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    while (have := part.stat().st_size if part.exists() else 0) < size:
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-", "User-Agent": "sniff"})
        try:
            # the timeout is the stall check: no byte for 60 s means reconnect and resume
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if r.status == 206 else "wb") as f:
                while b := r.read(CHUNK):
                    f.write(b)
                    STATE["error"] = None
        except (OSError, http.client.HTTPException) as e:  # dropped or stalled connection: show it, wait, resume
            STATE["error"] = f"{type(e).__name__}: {e}. Retrying in 10 s."
            time.sleep(10)
    h = hashlib.sha256()
    with open(part, "rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    if h.hexdigest() != digest:
        part.unlink()
        raise RuntimeError(f"{name} did not match its checksum, so it was deleted. Press Download to try again.")
    part.rename(dest)


def download() -> None:
    try:
        for name in MODELS:
            fetch(name)
    except Exception as e:
        STATE["error"] = str(e)
    finally:
        STATE["running"] = False


def answers(url: str) -> bool:
    """Something listens on the URL's port. A TCP connect only, so it sends the model server no request."""
    u = urlparse(url)
    try:
        socket.create_connection((u.hostname, u.port), timeout=0.5).close()
        return True
    except OSError:
        return False


@app.get("/setup")
def setup():
    models = []
    for name, (_, _, size) in MODELS.items():
        dest, part = config.MODELS_DIR / name, config.MODELS_DIR / (name + ".part")
        models.append({"name": name, "size": size, "have": size if dest.exists() else part.stat().st_size if part.exists() else 0})
    chat = answers(config.LLM_URL) or server.can_start()  # the chat server starts on the first model call
    return {"servers": chat and answers(config.EMBED_URL), "ready": all((config.MODELS_DIR / n).exists() for n in MODELS),
            "folder": str(config.MODELS_DIR), "models": models, **STATE}


@app.post("/setup/download")
def start_download():
    if not STATE["running"]:
        STATE.update(running=True, error=None)
        threading.Thread(target=download, daemon=True).start()
    return setup()


def check() -> None:
    """`sniff-backend.exe --check`: the frozen build loads OCR, PDF and vector search. It needs no model server."""
    import pymupdf

    from sniff import db
    from sniff.ingest import ocr

    doc = pymupdf.open()
    doc.new_page().insert_text((40, 80), "Sniff check 2026", fontsize=36)
    text = ocr.image_bytes(doc[0].get_pixmap(dpi=100).tobytes("png"))
    vec = db.connect(":memory:").execute("SELECT vec_version()").fetchone()[0]
    print(f"OCR read {text!r}; sqlite-vec {vec}")


if __name__ == "__main__":
    check() if "--check" in sys.argv else uvicorn.run(app, host="127.0.0.1", port=PORT)
