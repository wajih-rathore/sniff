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

# the evaluation uses its own database so it never touches the user's index
os.environ.setdefault("SNIFF_DB", str(EVAL_DB))
sys.path.insert(0, str(ROOT / "backend"))


def labels(split: str = "test") -> list[dict]:
    rows = [json.loads(line) for line in (SYNTH / "labels.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["split"] == split]


def jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (EVAL_DATA / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def rel(path: str) -> str:
    """Absolute indexed path -> label path relative to data/synth/out, with forward slashes."""
    return Path(path).resolve().relative_to(SYNTH.resolve()).as_posix()


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
    """Field comparison: numbers within 0.01, everything else case- and space-insensitive."""
    if pred is None or gold is None:
        return pred is None and gold is None
    try:
        return abs(float(pred) - float(gold)) < 0.01
    except (TypeError, ValueError):
        return norm(pred) == norm(gold)


def save(component: str, result: dict, failures: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    result = {"component": component, **result, "failures": len(failures), "updated": datetime.now().isoformat(timespec="seconds")}
    (RESULTS / f"{component}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (RESULTS / f"{component}_failures.json").write_text(json.dumps(failures, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2))
