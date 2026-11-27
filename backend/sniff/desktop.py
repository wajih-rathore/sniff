"""Entry point of the desktop build (frozen with PyInstaller): the API on 127.0.0.1, plus the model downloads.

The desktop app (desktop/src-tauri) starts this with SNIFF_HOME set, and starts the embedding llama-server once its
model file is on disk. The first run downloads only that file: search, types and facts need nothing else (the OCR
models ship inside the back end). The download screen (frontend/src/views/Setup.tsx) reads GET /setup and starts
POST /setup/download. The chat model for Settings > AI > This laptop is optional: GET /ai/local reports it and
POST /ai/local/download fetches it. The chat model server is started by this back end only when a model call needs
it (ai/server.py). Imports are absolute so PyInstaller can run this file as the main script.
"""
import hashlib
import http.client
import os
import signal
import socket
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

import uvicorn

from sniff import config
from sniff.api import app

PORT = 8765  # same as desktop/src-tauri/src/main.rs and frontend/src/api.ts
OLLAMA = "https://registry.ollama.ai/v2/library"
# file name in MODELS_DIR: (url, sha256, size). The two models the app can run, from the same source as eval/fetch_models.py.
MODELS = {
    "all-minilm-l6-v2.gguf": (f"{OLLAMA}/all-minilm/blobs/sha256:797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662",
                              "797b70c4edf85907fe0a49eb85811256f65fa0f7bf52166b147fd16be2be4662", 45949216),
    "qwen3-1.7b-q4_k_m.gguf": (f"{OLLAMA}/qwen3/blobs/sha256:3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6",
                               "3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6", 1359279776),
}
SETUP = ["all-minilm-l6-v2.gguf"]  # the first run: the embedding model search needs
CHAT = "qwen3-1.7b-q4_k_m.gguf"  # optional: This laptop's chat model (server.MODEL_FILE)
CHUNK = 1 << 20
STATE = {"running": False, "error": None}  # the first-run download
CHAT_STATE = {"running": False, "error": None}  # the chat model's download


def fetch(name: str, state: dict = STATE) -> None:
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
                    state["error"] = None
        except (OSError, http.client.HTTPException) as e:  # dropped or stalled connection: show it, wait, resume
            state["error"] = f"{type(e).__name__}: {e}. Retrying in 10 s."
            time.sleep(10)
    h = hashlib.sha256()
    with open(part, "rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    if h.hexdigest() != digest:
        part.unlink()
        raise RuntimeError(f"{name} did not match its checksum, so it was deleted. Press Download to try again.")
    part.rename(dest)


def download(names: list[str], state: dict) -> None:
    try:
        for name in names:
            fetch(name, state)
    except Exception as e:
        state["error"] = str(e)
    finally:
        state["running"] = False


def start(names: list[str], state: dict) -> None:
    if not state["running"]:
        state.update(running=True, error=None)
        threading.Thread(target=download, args=(names, state), daemon=True).start()


def progress(name: str) -> dict:
    dest, part, size = config.MODELS_DIR / name, config.MODELS_DIR / (name + ".part"), MODELS[name][2]
    return {"name": name, "size": size, "have": size if dest.exists() else part.stat().st_size if part.exists() else 0}


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
    """servers: the embedding server answers, so Sniff can index and search. The chat server is not waited for: it is
    optional, and starts on the first model call."""
    return {"servers": answers(config.EMBED_URL), "ready": all((config.MODELS_DIR / n).exists() for n in SETUP),
            "folder": str(config.MODELS_DIR), "models": [progress(n) for n in SETUP], **STATE}


@app.post("/setup/download")
def start_download():
    start(SETUP, STATE)
    return setup()


@app.get("/ai/local")
def local_model():
    """This laptop's chat model: its download progress, and ready once the file is on disk (Settings > AI then offers
    This laptop)."""
    return {**progress(CHAT), "ready": (config.MODELS_DIR / CHAT).exists(), **CHAT_STATE}


@app.post("/ai/local/download")
def start_local_download():
    start([CHAT], CHAT_STATE)
    return local_model()


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


def end_with_parent(poll: float = 2.0) -> None:
    """macOS and Linux: the desktop app starts this back end as the leader of its own process group, and the chat
    server started here stays in that group. The app stops the group when it quits; if the app dies without doing so
    (a crash, SIGKILL), we are handed to another parent, and this ends the group. Windows uses a job object instead."""
    parent = os.getppid()
    while os.getppid() == parent:
        time.sleep(poll)
    os.killpg(os.getpgrp(), signal.SIGTERM)


if __name__ == "__main__":
    # only when we lead our own process group (the app starts us so): the signal then reaches no one else's processes
    if os.name == "posix" and config.HOME and os.getpgrp() == os.getpid():
        threading.Thread(target=end_with_parent, daemon=True, name="end-with-parent").start()
    check() if "--check" in sys.argv else uvicorn.run(app, host="127.0.0.1", port=PORT)
