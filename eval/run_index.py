"""Step 1: index a test set into its own evaluation database through the real pipeline, timing each file.
Needs both llama-server processes running. The full test split takes about an hour on a laptop CPU.

    uv run python ../eval/run_index.py                  # synthetic test split -> .sniff/eval.db
    uv run python ../eval/run_index.py --set hard       # data/hard  -> .sniff/eval-hard.db
    uv run python ../eval/run_index.py --set real       # data/real  -> .sniff/eval-real.db
    uv run python ../eval/run_index.py --limit 10       # a fixed subset, stratified by type (quick check)

Resumable: files already indexed are skipped, so it can be stopped and started again. --fresh starts over."""
import argparse

from common import RESULTS, db_path, index, labels, path_of, stay_awake, stratified

from sniff import db


def times_path(split: str):
    return RESULTS / ("index_times.json" if split == "test" else f"index_times_{split}.json")


def main(split: str, limit: int | None, fresh: bool):
    path, times = db_path(split), times_path(split)
    if fresh:  # the -wal and -shm files too: a stale WAL next to a new database file can bring old rows back
        for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"), times):
            p.unlink(missing_ok=True)
    gold = stratified(labels(split), limit)
    if not gold:
        raise SystemExit(f"no labels for '{split}': build the set first (see docs/EVAL.md)")
    index(db.connect(path), [path_of(g) for g in gold], times)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="test", choices=["test", "hard", "real", "own"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    stay_awake()
    main(a.set, a.limit, a.fresh)
