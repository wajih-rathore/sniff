"""Search: Recall@5 and MRR for hybrid vs keyword-only vs embeddings-only vs a file-name baseline."""
import re

from common import Timer, jsonl, latency, rel, save

from sniff import db
from sniff.search import STOP, search

K = 5


def filename_search(conn, q: str, k: int) -> list[str]:
    """Baseline: what a file-name search box does."""
    words = [w for w in re.findall(r"\w+", q.lower()) if w not in STOP]
    scored = []
    for r in conn.execute("SELECT path FROM files"):
        name = r["path"].lower().rsplit("\\", 1)[-1]
        s = sum(w in name for w in words)
        if s:
            scored.append((s, r["path"]))
    return [p for _, p in sorted(scored, reverse=True)[:k]]


def score(ranked: list[str], relevant: set[str]) -> tuple[float, float]:
    """Recall@5 is capped: a query with 12 relevant bills scores 1.0 if all 5 results are relevant."""
    top = ranked[:K]
    recall = len(relevant & set(top)) / min(len(relevant), K)
    rr = next((1 / i for i, p in enumerate(top, 1) if p in relevant), 0.0)
    return recall, rr


def main():
    conn = db.connect()
    queries = jsonl("search.jsonl")
    systems = {"Sniff (hybrid BM25 + embeddings)": "hybrid", "Keyword only (BM25)": "bm25", "Embeddings only": "vector"}
    rows, failures, ms = [], [], []
    for label, mode in systems.items():
        rec, mrr = [], []
        for q in queries:
            with Timer() as t:
                hits = search(conn, q["q"], k=K, mode=mode)
            if mode == "hybrid":
                ms.append(t.ms)
            ranked = [rel(h.path) for h in hits]
            r, rr = score(ranked, set(q["relevant"]))
            rec.append(r)
            mrr.append(rr)
            if mode == "hybrid" and r < 1:
                failures.append({"q": q["q"], "relevant": q["relevant"], "got": ranked})
        rows.append({"system": label, "Recall@5 (capped)": sum(rec) / len(rec), "MRR": sum(mrr) / len(mrr)})
    rec, mrr = zip(*(score([rel(p) for p in filename_search(conn, q["q"], K)], set(q["relevant"])) for q in queries))
    rows.append({"system": "File-name search (baseline)", "Recall@5 (capped)": sum(rec) / len(rec), "MRR": sum(mrr) / len(mrr)})
    save("search", {"n": len(queries), "rows": rows, "latency_ms": latency(ms)}, failures)


if __name__ == "__main__":
    main()
