"""Memory of the Python back end: private bytes (commit) and working set. Windows only (process counters via ctypes).
Run from backend/:

    uv run python ../eval/backend_memory.py                     # after each import step, in this process
    uv run python ../eval/backend_memory.py serve --app-dir DIR --folder F
        # a real back end (uvicorn) from DIR with an empty data folder: idle after 30 s, then indexing F through /index
        # with peaks sampled every 0.5 s until the facts queue is empty, then idle 150 s later. Chat servers the back end
        # starts on demand are sampled too (llama-server children).
"""
import ctypes
import importlib
import json
import sys
import time
from ctypes import wintypes


class Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def mb(pid: int | None = None) -> dict:
    c = Counters()
    c.cb = ctypes.sizeof(c)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = k32.OpenProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED | VM_READ
    k32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb)
    if pid is not None:
        k32.CloseHandle(h)
    return {"private_mb": round(c.PrivateUsage / 2**20), "working_set_mb": round(c.WorkingSetSize / 2**20)}


def children(name: str) -> list[int]:
    """PIDs of running processes with this image name (tasklist, to avoid a psutil dependency)."""
    import csv
    import io
    import subprocess
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh", "/fi", f"imagename eq {name}"], capture_output=True, text=True).stdout
    return [int(r[1]) for r in csv.reader(io.StringIO(out)) if len(r) > 1 and r[1].isdigit()]


def listener(port: int) -> int | None:
    """The PID listening on a local TCP port. (The venv's python.exe is a launcher that starts the real interpreter
    as a child, so the PID Popen returns is not the process that holds the memory.)"""
    import subprocess
    for line in subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 5 and parts[1] == f"127.0.0.1:{port}" and parts[3] == "LISTENING":
            return int(parts[4])
    return None


def serve(app_dir: str, folder: str | None, port: int = 8799) -> None:
    import os
    import subprocess
    import tempfile
    import urllib.request

    data = tempfile.mkdtemp(prefix="sniff-mem-")
    env = {**os.environ, "SNIFF_DATA": data, "SNIFF_DB": os.path.join(data, "sniff.db")}
    before_llama = set(children("llama-server.exe"))
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "sniff.api:app", "--port", str(port)], cwd=app_dir, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    get = lambda path: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30).read())
    out = {}
    try:
        for _ in range(120):
            try:
                get("/index/status")
                break
            except OSError:
                time.sleep(0.5)
        pid = listener(port)
        time.sleep(30)
        out["idle"] = mb(pid)
        if folder:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/index", data=json.dumps({"folder": folder}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=30).read()
            peak, chat_peak, t = {"private_mb": 0, "working_set_mb": 0}, {"private_mb": 0, "working_set_mb": 0}, time.time()
            while time.time() - t < 1800:
                m = mb(pid)
                peak = {k: max(peak[k], m[k]) for k in peak}
                for chat in set(children("llama-server.exe")) - before_llama:
                    c = mb(chat)
                    chat_peak = {k: max(chat_peak[k], c[k]) for k in chat_peak}
                s = get("/index/status")
                if not s["running"] and not s["queued"]:
                    break
                time.sleep(0.5)
            out["indexing_peak"], out["chat_server_peak"], out["indexing_s"] = peak, chat_peak, round(time.time() - t)
            time.sleep(150)  # past the chat server's 120 s idle stop
            out["idle_150s_after_indexing"] = mb(pid)
            out["chat_servers_left"] = len(set(children("llama-server.exe")) - before_llama)
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)  # the launcher, the server and its chat server
    print(json.dumps(out))


def main():
    steps = {"python": mb()}
    for name in ("numpy", "pymupdf", "docx", "pptx", "openai", "sqlite_vec", "fastapi", "watchdog", "mcp", "sniff.api"):
        t = time.perf_counter()
        importlib.import_module(name)
        steps[f"+ {name}"] = {**mb(), "s": round(time.perf_counter() - t, 2)}
    heavy = sorted(m for m in sys.modules if m.split(".")[0] in ("rapidocr_onnxruntime", "onnxruntime", "googleapiclient", "cv2", "PIL"))
    steps["loaded at idle"] = sorted({m.split(".")[0] for m in heavy})
    if "--ocr" in sys.argv:
        import pymupdf

        from sniff.ingest import ocr
        doc = pymupdf.open()
        doc.new_page().insert_text((40, 80), "Sniff check 2026", fontsize=36)
        t = time.perf_counter()
        ocr.image_bytes(doc[0].get_pixmap(dpi=100).tobytes("png"))
        steps["+ first OCR"] = {**mb(), "s": round(time.perf_counter() - t, 2)}
        ocr.release()
        import gc
        gc.collect()
        steps["after ocr.release()"] = mb()
    print(json.dumps(steps, indent=1))


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("serve")
        ap.add_argument("--app-dir", required=True)
        ap.add_argument("--folder")
        ap.add_argument("--port", type=int, default=8799)
        a = ap.parse_args()
        serve(a.app_dir, a.folder, a.port)
    else:
        main()
