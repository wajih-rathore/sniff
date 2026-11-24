"""System benchmark (feedback point 1): index N documents and ask M questions through the real pipeline, while
sampling the memory of every Sniff process every 0.5 s. Writes eval/results/system.json.

    cd backend
    uv run --with psutil python ../scripts/benchmark.py --n 20 --m 20                            # attach to running model servers
    uv run --with psutil python ../scripts/benchmark.py --start-servers --threads 4 --n 20 --m 20  # start them (8 GB laptop)

The back end runs inside this process (the same code the API calls: scan.index_file and ask.ask), so this
process is "backend (python)". The model servers are the llama-server processes on the chat and embedding ports.
Per process it records the peak working set (RAM in use) and the peak commit (private memory Windows must back
with RAM or the pagefile); "all Sniff processes" is the peak of their sum at the same moment. Model weights are
memory-mapped, so they count in the working set but not in commit.
Stages (p50/p95 per call): read (without OCR, per file), ocr (per page or image), embed (per file),
classify, extract (incl. verification), index_total (per file), search (per query), route (LLM router), ask (end to end).
--start-servers starts llama-server with -t/-tb THREADS to act like a typical 4-core laptop CPU, and stops it after.
"""
import argparse
import json
import os
import platform
import random
import subprocess
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
BENCH_DB = ROOT / ".sniff" / "bench.db"
os.environ.setdefault("SNIFF_DB", str(BENCH_DB))
sys.path.insert(0, str(ROOT / "backend"))

TODAY = date(2026, 11, 22)
BUDGET_MB = 4.5 * 1024  # what an 8 GB laptop leaves for apps after Windows and its services
MB = 1024 * 1024


# ---------------------------------------------------------------- memory sampling
class Sampler(threading.Thread):
    def __init__(self, procs: dict[str, psutil.Process], interval: float):
        super().__init__(daemon=True)
        self.procs, self.interval, self.stop = procs, interval, threading.Event()
        self.peak = defaultdict(lambda: {"working_set_MB": 0.0, "commit_MB": 0.0})
        self.timeline = []

    def run(self):
        t0 = time.perf_counter()
        while not self.stop.is_set():
            tot_ws = tot_commit = 0.0
            for name, p in self.procs.items():
                try:
                    m = p.memory_info()
                except psutil.Error:  # a server that exited
                    continue
                ws, commit = m.rss / MB, getattr(m, "private", m.vms) / MB  # Windows: rss = working set, private = commit
                pk = self.peak[name]
                pk["working_set_MB"], pk["commit_MB"] = max(pk["working_set_MB"], ws), max(pk["commit_MB"], commit)
                tot_ws, tot_commit = tot_ws + ws, tot_commit + commit
            pk = self.peak["all Sniff processes"]
            pk["working_set_MB"], pk["commit_MB"] = max(pk["working_set_MB"], tot_ws), max(pk["commit_MB"], tot_commit)
            self.timeline.append([round(time.perf_counter() - t0, 1), round(tot_ws), round(tot_commit)])
            self.stop.wait(self.interval)


def os_peaks(p: psutil.Process) -> dict:
    """Peaks the OS kept for the whole life of the process (Windows only), as a cross-check."""
    try:
        m = p.memory_info()
        return {"os_peak_working_set_MB": round(getattr(m, "peak_wset", 0) / MB), "os_peak_commit_MB": round(getattr(m, "peak_pagefile", 0) / MB)}
    except psutil.Error:
        return {}


# ---------------------------------------------------------------- model servers
def port_of(cmd: list[str]) -> int | None:
    return int(cmd[cmd.index("--port") + 1]) if "--port" in cmd else None


def find_servers(ports: dict[str, int]) -> dict[str, psutil.Process]:
    found = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        if (p.info["name"] or "").lower().startswith("llama-server"):
            port = port_of(p.info["cmdline"] or [])
            for role, want in ports.items():
                if port == want:
                    found[f"llama-server {role} (:{port})"] = p
    return found


def wait_healthy(port: int, timeout: float = 180) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit(f"llama-server on port {port} did not become healthy")


def start_servers(a) -> list[subprocess.Popen]:
    if find_servers({"chat": a.chat_port, "embedding": a.embed_port}):  # else we would measure the old servers
        raise SystemExit(f"a llama-server already uses port {a.chat_port} or {a.embed_port}: stop it, or leave out --start-servers")
    exe = str(ROOT / "models" / "llama" / ("llama-server.exe" if os.name == "nt" else "llama-server"))
    t = ["-t", str(a.threads), "-tb", str(a.threads)] if a.threads else []
    cmds = [
        [exe, "-m", a.chat_model, "--port", str(a.chat_port), "--jinja", "--reasoning-budget", "0", "-c", "8192", *t, *a.chat_args.split()],
        [exe, "-m", a.embed_model, "--embedding", "--port", str(a.embed_port), "-c", "2048", "-b", "2048", "-ub", "2048", *t],
    ]
    procs = [subprocess.Popen(c, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for c in cmds]
    for port in (a.chat_port, a.embed_port):
        wait_healthy(port)
    return procs


# ---------------------------------------------------------------- stage timers
TIMES: dict[str, list[float]] = defaultdict(list)
OCR_MS = [0.0]


def wrap(mods, name: str, stage: str, on_ocr: bool = False, minus_ocr: bool = False) -> bool:
    """Replace mods[0].name (and the same function imported into the other modules) with a timed version."""
    f = getattr(mods[0], name, None)
    if f is None:
        return False

    def timed(*args, **kw):
        t, o = time.perf_counter(), OCR_MS[0]
        try:
            return f(*args, **kw)
        finally:
            ms = (time.perf_counter() - t) * 1000
            if on_ocr:
                OCR_MS[0] += ms
            TIMES[stage].append(ms - (OCR_MS[0] - o) if minus_ocr else ms)

    for m in mods:
        if getattr(m, name, None) is f:
            setattr(m, name, timed)
    return True


def instrument() -> None:
    from sniff import ask, search
    from sniff.ai import classify, embed, extract
    from sniff.ingest import ocr, readers

    wrap([ocr], "read", "ocr", on_ocr=True) or wrap([ocr], "image_bytes", "ocr", on_ocr=True)
    wrap([readers], "read_layout", "read", minus_ocr=True) or wrap([readers], "read", "read", minus_ocr=True)
    wrap([embed], "embed", "embed")
    wrap([classify], "classify", "classify")
    wrap([extract], "extract", "extract")
    wrap([search, ask], "search", "search")
    wrap([ask], "route", "route")


# ---------------------------------------------------------------- main
def pick_docs(folder: Path, n: int, seed: int) -> list[Path]:
    from sniff.ingest import scan

    files = sorted(scan.iter_files(folder))
    return sorted(random.Random(seed).sample(files, min(n, len(files))))


def pick_questions(m: int, seed: int) -> list[str]:
    import re

    f = ROOT / "data" / "eval" / "ask.jsonl"
    qs = [json.loads(l)["q"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", q).strip() for q in random.Random(seed).sample(qs, min(m, len(qs)))]


def pct(xs: list[float], p: float) -> int:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]) if xs else 0


def main(a) -> None:
    # the back end must talk to the servers we measure (read by sniff.config on first import, below)
    os.environ["SNIFF_LLM_URL"] = f"http://127.0.0.1:{a.chat_port}/v1"
    os.environ["SNIFF_EMBED_URL"] = f"http://127.0.0.1:{a.embed_port}/v1"
    servers = start_servers(a) if a.start_servers else []
    try:
        procs = {"backend (python)": psutil.Process()}
        found = find_servers({"chat": a.chat_port, "embedding": a.embed_port})
        if len(found) < 2:
            raise SystemExit(f"model servers not found on ports {a.chat_port}/{a.embed_port}: start them or use --start-servers")
        procs.update(found)
        instrument()
        from sniff import ask, db
        from sniff.ingest import scan

        BENCH_DB.unlink(missing_ok=True)
        conn = db.connect(BENCH_DB)
        docs, questions = pick_docs(Path(a.docs), a.n, a.seed), pick_questions(a.m, a.seed)
        sampler = Sampler(procs, a.interval)
        sampler.start()
        errors = 0
        for i, p in enumerate(docs, 1):
            t = time.perf_counter()
            try:
                scan.index_file(conn, p, scan.sha256(p))
            except Exception as e:  # count it and go on, like the real scan does
                conn.rollback()
                errors += 1
                print(f"  error: {type(e).__name__}: {e}")
            conn.commit()
            TIMES["index_total"].append((time.perf_counter() - t) * 1000)
            print(f"[{i}/{len(docs)}] {p.name} {TIMES['index_total'][-1]:.0f} ms", flush=True)
        for i, q in enumerate(questions, 1):
            t = time.perf_counter()
            ans = ask.ask(conn, q, TODAY)
            TIMES["ask"].append((time.perf_counter() - t) * 1000)
            print(f"[q{i}/{len(questions)}] {TIMES['ask'][-1]:.0f} ms  {q[:60]} -> {ans.route}", flush=True)
        time.sleep(a.interval * 2)
        sampler.stop.set()
        sampler.join()
        mem_rows = []
        for name, pk in sampler.peak.items():
            row = {"system": name, "peak_working_set_MB": round(pk["working_set_MB"]), "peak_commit_MB": round(pk["commit_MB"])}
            mem_rows.append({**row, **(os_peaks(procs[name]) if name in procs else {})})
        total = sampler.peak["all Sniff processes"]
        order = ["read", "ocr", "embed", "classify", "extract", "index_total", "search", "route", "ask"]
        stage_rows = [{"system": s, "calls": len(TIMES[s]), "latency_p50_ms": pct(TIMES[s], 50), "latency_p95_ms": pct(TIMES[s], 95)}
                      for s in order if TIMES[s]]
        result = {
            "component": "system", "n": len(docs), "questions": len(questions), "index_errors": errors,
            "rows": stage_rows + mem_rows,
            "budget_MB": BUDGET_MB, "peak_working_set_within_budget": total["working_set_MB"] <= BUDGET_MB,
            "setup": {"mode": "started servers" if servers else "attached to running servers", "threads": a.threads if servers else "as running",
                      "servers": {n: " ".join(p.cmdline()[1:]) for n, p in found.items()},
                      "machine": {"cpu": platform.processor(), "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
                                  "ram_GB": round(psutil.virtual_memory().total / 2**30, 1), "os": platform.platform()},
                      "docs": [str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p) for p in docs]},
            "failures": errors, "updated": datetime.now().isoformat(timespec="seconds"),
            "timeline": sampler.timeline,  # [seconds, total working set MB, total commit MB]
        }
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "system.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "timeline"}, indent=1))
    finally:
        for s in servers:
            s.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="documents to index")
    ap.add_argument("--m", type=int, default=20, help="questions to ask (from data/eval/ask.jsonl)")
    ap.add_argument("--docs", default=str(ROOT / "data" / "synth" / "out" / "test"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--interval", type=float, default=0.5, help="memory sampling interval, seconds")
    ap.add_argument("--start-servers", action="store_true", help="start the two llama-server processes and stop them after")
    ap.add_argument("--threads", type=int, default=4, help="with --start-servers: llama-server -t/-tb")
    ap.add_argument("--chat-model", default=str(ROOT / "models" / "qwen3-1.7b-q4_k_m.gguf"))
    ap.add_argument("--embed-model", default=str(ROOT / "models" / "all-minilm-l6-v2.gguf"))
    ap.add_argument("--chat-port", type=int, default=8080)
    ap.add_argument("--embed-port", type=int, default=8081)
    ap.add_argument("--chat-args", default="", help="with --start-servers: extra chat llama-server flags, e.g. \"--cache-ram 512 -np 1\"")
    main(ap.parse_args())
