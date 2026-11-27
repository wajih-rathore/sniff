"""The chat model server, started on demand. Nothing starts it at launch: the first model call does, and it stops
again after SNIFF_LLM_IDLE seconds (default 120) without a call, so the 1.7B model (about 2 GB) is only in memory
while it is needed. Most documents never need it (type and fields come from rules and a small classifier).

Sniff manages only a server it started itself, on a local URL, with llama-server and the model file on disk. If
something else already answers on the chat URL (a server started by hand, the team server), it is used as it is and
never stopped. In the desktop app the back end runs inside the app's job object, and a process it starts joins that
job, so the server also ends if Sniff crashes; on a normal exit atexit stops it.
"""
import atexit
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .. import config

IDLE = float(os.environ.get("SNIFF_LLM_IDLE", "120"))  # seconds without a call before the server is stopped
MODEL_FILE = os.environ.get("SNIFF_LLM_FILE", "qwen3-1.7b-q4_k_m.gguf")
FLAGS = ["--jinja", "--reasoning-budget", "0", "-c", "8192", "--cache-ram", "512", "-np", "1"]
START_TIMEOUT = 120.0  # loading the model from a cold disk can take a while
NO_WINDOW = 0x08000000

_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_last = 0.0
_busy = 0
STATS = {"starts": 0, "last_start_s": None}


def exe() -> Path:
    """llama-server.exe: next to the frozen back end in the desktop app (resources/llama), else models/llama."""
    if env := os.environ.get("SNIFF_LLAMA_SERVER"):
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "llama" / "llama-server.exe"
    return config.MODELS_DIR / "llama" / "llama-server.exe"


def _port() -> tuple[str, int]:
    u = urlparse(config.LLM_URL)
    return u.hostname or "127.0.0.1", u.port or 80


def can_start() -> bool:
    """Sniff can start the chat server itself: the URL is on this machine and both files are there."""
    return _port()[0] in ("127.0.0.1", "localhost") and exe().exists() and (config.MODELS_DIR / MODEL_FILE).exists()


def ready() -> bool:
    """The chat server answers and has its model loaded (llama-server says 503 while it is still loading)."""
    host, port = _port()
    try:
        return httpx.get(f"http://{host}:{port}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def ensure() -> None:
    """Make sure a chat server answers before a model call. Starts one if none answers and Sniff can. If it cannot,
    it returns and the call fails as it did before (the facts worker keeps the file queued and tries again)."""
    global _proc, _last
    with _lock:
        if ready() or not can_start():
            return
        if not running():
            t = time.monotonic()
            host, port = _port()
            logs = Path(config.HOME, "logs") if config.HOME else config.DATA_DIR
            logs.mkdir(parents=True, exist_ok=True)
            with open(logs / "chat-server.log", "ab") as log:  # the child keeps its own handle
                _proc = subprocess.Popen([str(exe()), "-m", str(config.MODELS_DIR / MODEL_FILE), "--host", host, "--port", str(port), *FLAGS],
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         creationflags=NO_WINDOW if sys.platform == "win32" else 0)
            STATS["starts"] += 1
            _last = time.monotonic()
            threading.Thread(target=_stopper, daemon=True, name="chat-server-stopper").start()
            while time.monotonic() - t < START_TIMEOUT and running() and not ready():
                time.sleep(0.25)
            STATS["last_start_s"] = round(time.monotonic() - t, 1)


@contextmanager
def using():
    """Wrap every model call: starts the server if needed, and keeps it up while the call (or a stream) runs."""
    global _busy, _last
    with _lock:
        _busy += 1  # first, so the idle stopper leaves the server alone from here on
    try:
        ensure()
        yield
    finally:
        with _lock:
            _busy -= 1
            _last = time.monotonic()


def stop() -> None:
    """Stop the server Sniff started (never one it found running)."""
    global _proc
    with _lock:
        if _proc is not None:
            if _proc.poll() is None:
                _proc.terminate()
                try:
                    _proc.wait(10)
                except subprocess.TimeoutExpired:
                    _proc.kill()
            _proc = None


def _stopper() -> None:
    me = _proc
    while _proc is me and running():
        time.sleep(min(5.0, IDLE / 4))
        with _lock:  # checked and stopped under the lock, so no call can start in between
            if _busy == 0 and time.monotonic() - _last > IDLE:
                stop()


atexit.register(stop)
