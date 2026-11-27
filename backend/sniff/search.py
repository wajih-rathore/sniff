"""Hybrid search: BM25 keyword ranking and embedding similarity, fused with reciprocal rank fusion (RRF)."""
import re
import sqlite3
from pathlib import Path

from .ai import embed
from .schemas import Hit

RRF_K = 60
CANDIDATES = 50
STOP = {"a", "an", "the", "my", "our", "of", "for", "in", "on", "to", "and", "or", "is", "are", "what", "when",
        "where", "which", "who", "how", "much", "many", "do", "does", "did", "i", "we", "me", "with", "from", "this", "that"}


def fts_query(q: str) -> str:
    words = [w for w in re.findall(r"\w+", q.lower()) if w not in STOP]
    return " OR ".join(f'"{w}"' for w in words)


def search(conn: sqlite3.Connection, q: str, k: int = 10, mode: str = "hybrid") -> list[Hit]:
    """mode: hybrid | bm25 | vector (the last two exist so the evaluation can compare them)."""
    scores: dict[int, float] = {}
    if mode in ("hybrid", "bm25") and (match := fts_query(q)):
        rows = conn.execute(
            "SELECT rowid AS id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, CANDIDATES),
        ).fetchall()
        for rank, r in enumerate(rows):
            scores[r["id"]] = scores.get(r["id"], 0) + 1 / (RRF_K + rank)
    if mode in ("hybrid", "vector"):
        rows = conn.execute(
            "SELECT chunk_id AS id FROM chunk_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (embed.embed_query(q).tobytes(), CANDIDATES),
        ).fetchall()
        for rank, r in enumerate(rows):
            scores[r["id"]] = scores.get(r["id"], 0) + 1 / (RRF_K + rank)

    hits: dict[int, Hit] = {}  # best chunk per file
    for cid, score in sorted(scores.items(), key=lambda x: -x[1]):
        row = conn.execute(
            "SELECT c.file_id, c.page, c.text, f.path, f.doc_type, EXISTS(SELECT 1 FROM queue q WHERE q.file_id=c.file_id) AS reading "
            "FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id=?",
            (cid,),
        ).fetchone()
        if not row or row["file_id"] in hits:
            continue
        hits[row["file_id"]] = Hit(
            chunk_id=cid, file_id=row["file_id"], path=row["path"], name=Path(row["path"]).name, page=row["page"],
            snippet=snippet(row["text"], q), score=round(score, 5), doc_type=row["doc_type"], reading=bool(row["reading"]),
        )
        if len(hits) == k:
            break
    return list(hits.values())


def snippet(text: str, q: str, width: int = 240) -> str:
    text = re.sub(r"\s*[-=*_.~]{4,}\s*", " ", text)  # drop separator lines like "-------" from receipts
    low = text.lower()
    positions = [low.find(w) for w in re.findall(r"\w+", q.lower()) if w not in STOP and low.find(w) >= 0]
    start = max(0, min(positions) - 60) if positions else 0
    return ("…" if start else "") + text[start : start + width] + ("…" if start + width < len(text) else "")
