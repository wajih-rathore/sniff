"""Compare embedding models on the same indexed passages (no re-reading or OCR).
For each model: copy the evaluation DB, re-embed every chunk with that model's server, run the search eval.
Usage: uv run python ../eval/run_embeddings.py all-minilm=http://127.0.0.1:8081/v1=384 nomic-embed-text=http://127.0.0.1:8083/v1=768"""
import json
import os
import shutil
import subprocess
import sys

from common import EVAL_DB, RESULTS, ROOT, save


def main(specs: list[str]):
    rows = []
    for spec in specs:
        name, url, dim = spec.split("=")
        db_copy = EVAL_DB.with_name(f"eval-{name}.db")
        shutil.copy(EVAL_DB, db_copy)
        env = {**os.environ, "SNIFF_DB": str(db_copy), "SNIFF_EMBED_MODEL": name, "SNIFF_EMBED_URL": url, "SNIFF_EMBED_DIM": dim}
        subprocess.run([sys.executable, "-c", REEMBED], env=env, check=True, cwd=ROOT / "backend")
        subprocess.run([sys.executable, str(ROOT / "eval" / "run_search.py")], env=env, check=True, cwd=ROOT / "backend",
                       stdout=subprocess.DEVNULL)
        result = json.loads((RESULTS / "search.json").read_text())
        for r in result["rows"][:3]:  # hybrid, BM25, embeddings only
            if "BM25" not in r["system"] or not rows:
                rows.append({**r, "system": f"{r['system']} [{name}]"})
    save("embeddings", {"n": result["n"], "rows": rows}, [])


REEMBED = """
import numpy as np
from sniff import config, db
from sniff.ai import embed
conn = db.connect()
conn.execute("DROP TABLE chunk_vec")
conn.execute(f"CREATE VIRTUAL TABLE chunk_vec USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{config.EMBED_DIM}] distance_metric=cosine)")
rows = conn.execute("SELECT id, text FROM chunks").fetchall()
for (cid, _), v in zip(rows, embed.embed([r["text"] for r in rows])):
    conn.execute("INSERT INTO chunk_vec(chunk_id, embedding) VALUES (?,?)", (cid, v.tobytes()))
conn.commit()
print("re-embedded", len(rows), "chunks with", config.EMBED_MODEL)
"""

if __name__ == "__main__":
    main(sys.argv[1:])
