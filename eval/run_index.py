"""Step 1: index the synthetic test split into the evaluation database, timing each file.
Needs llama-server running. Takes a while on a CPU (two LLM calls per document)."""
import json
import time

from common import EVAL_DB, RESULTS, SYNTH

from sniff import db
from sniff.ingest import scan


def main():
    EVAL_DB.unlink(missing_ok=True)
    conn = db.connect()
    times = {}
    folder = SYNTH / "test"
    files = list(scan.iter_files(folder))
    for i, p in enumerate(files, 1):
        t = time.perf_counter()
        try:
            scan.index_file(conn, p, scan.sha256(p))
        except Exception as e:
            conn.rollback()
            scan._mark_error(conn, p, scan.sha256(p), p.stat(), e)
        conn.commit()
        times[str(p)] = (time.perf_counter() - t) * 1000
        print(f"[{i}/{len(files)}] {p.name} {times[str(p)]:.0f} ms", flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "index_times.json").write_text(json.dumps(times, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
