"""Shared helpers for the evaluation scripts. Run every script from backend/: uv run python ../eval/<script>.py"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTH = ROOT / "data" / "synth" / "out"
EVAL_DATA = ROOT / "data" / "eval"
RESULTS = ROOT / "eval" / "results"
EVAL_DB = ROOT / ".sniff" / "eval.db"
# test set -> folder that holds its labels.jsonl; label "file" paths are relative to it
SETS = {"test": SYNTH, "hard": ROOT / "data" / "hard" / "out", "real": ROOT / "data" / "real" / "out",
        "own": ROOT / "data" / "own"}  # own: team members' private documents, see data/own/README.md

# the evaluation uses its own database so it never touches the user's index
os.environ.setdefault("SNIFF_DB", str(EVAL_DB))
sys.path.insert(0, str(ROOT / "backend"))


def stay_awake() -> None:
    """Stop Windows from sleeping while a long run works (the laptop slept 18 hours into one run on 25 Nov).
    ES_CONTINUOUS | ES_SYSTEM_REQUIRED now, ES_CONTINUOUS alone at exit. Closing the lid still sleeps the laptop."""
    if sys.platform == "win32":
        import atexit
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState(0x80000001)
        atexit.register(kernel32.SetThreadExecutionState, 0x80000000)


def labels(split: str = "test") -> list[dict]:
    f = SETS[split] / "labels.jsonl"
    rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()] if f.exists() else []
    return [r for r in rows if r["split"] == split]


def path_of(label: dict) -> Path:
    return (SETS[label["split"]] / label["file"]).resolve()


def db_path(split: str, model: str | None = None) -> Path:
    """test -> .sniff/eval.db (what the app and the ask/search evals use); others get their own database,
    so hard copies never count as near-duplicates of test documents in the ask totals."""
    name = "eval" if split == "test" else f"eval-{split}"
    return EVAL_DB.with_name(f"{name}-{model}.db" if model else f"{name}.db")


def stratified(rows: list[dict], n: int | None) -> list[dict]:
    """A fixed subset of n labels, round-robin over document types (sorted by file, so it is the same every run)."""
    if not n or n >= len(rows):
        return rows
    by_type: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: r.get("file", "")):
        by_type.setdefault(r["type"], []).append(r)
    out, i = [], 0
    while len(out) < n:
        out += [g[i] for g in by_type.values() if i < len(g)][: n - len(out)]
        i += 1
    return out


def jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (EVAL_DATA / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def rel(path: str) -> str:
    """Absolute indexed path -> label path (relative to its set's folder), with forward slashes."""
    p = Path(path).resolve()
    for root in SETS.values():
        try:
            return p.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return p.as_posix()


def percentile(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


def latency(ms: list[float]) -> dict:
    return {"p50": round(percentile(ms, 50)), "p95": round(percentile(ms, 95))}


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t) * 1000


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def same_value(pred, gold) -> bool:
    """Field comparison: numbers within 0.01; text ignores case, spaces and punctuation ("MR D.I.Y." = "MR DIY")."""
    if pred is None or gold is None:
        return pred is None and gold is None
    try:
        return abs(float(pred) - float(gold)) < 0.01
    except (TypeError, ValueError):
        return re.sub(r"[\W_]+", "", str(pred).lower()) == re.sub(r"[\W_]+", "", str(gold).lower())


def use_llm(url: str | None, model: str | None) -> None:
    """Point the back end at another OpenAI-compatible chat server (for the model comparison)."""
    from sniff import config
    from sniff.ai import llm

    if url:
        config.LLM_URL = url
    if model:
        config.LLM_MODEL = model
    if hasattr(llm.client, "cache_clear"):
        llm.client.cache_clear()


def index(conn, files: list[Path], times_path: Path) -> dict:
    """Index each file through the real pipeline (scan.index_file), skipping files already indexed, so a stopped run
    can be started again. Records per file the total time and the time spent waiting for the LLM."""
    from sniff.ai import llm
    from sniff.ingest import scan

    times = json.loads(times_path.read_text()) if times_path.exists() else {}
    done = {r["path"] for r in conn.execute("SELECT path FROM files WHERE status='indexed'")}
    spent = [0.0]
    chat = llm.chat

    def timed_chat(*a, **kw):
        t = time.perf_counter()
        try:
            return chat(*a, **kw)
        finally:
            spent[0] += (time.perf_counter() - t) * 1000

    llm.chat = timed_chat  # chat_json looks chat up at call time, so this sees every LLM call
    try:
        for i, p in enumerate(files, 1):
            if str(p) in done:
                continue
            spent[0] = 0.0
            t = time.perf_counter()
            try:
                scan.index_file(conn, p, scan.sha256(p))
            except Exception as e:  # one bad file must not stop the run
                conn.rollback()
                scan._mark_error(conn, p, scan.sha256(p), p.stat(), e)
            conn.commit()
            times[str(p)] = {"total_ms": round((time.perf_counter() - t) * 1000), "llm_ms": round(spent[0])}
            times_path.parent.mkdir(parents=True, exist_ok=True)
            times_path.write_text(json.dumps(times, indent=1), encoding="utf-8")
            print(f"[{i}/{len(files)}] {p.name} {times[str(p)]['total_ms']} ms (LLM {times[str(p)]['llm_ms']} ms)", flush=True)
    finally:
        llm.chat = chat
    return times


def save(component: str, result: dict, failures: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    result = {"component": component, **result, "failures": len(failures), "updated": datetime.now().isoformat(timespec="seconds")}
    (RESULTS / f"{component}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (RESULTS / f"{component}_failures.json").write_text(json.dumps(failures, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2))
