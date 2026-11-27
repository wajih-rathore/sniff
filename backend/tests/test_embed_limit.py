"""Chunks never pass the embedding model's input limit, and one chunk the server refuses does not fail its file."""
import httpx
import openai

from conftest import fake_vector
from sniff import api
from sniff.ai import embed
from sniff.ai.embed import embed as real_embed
from sniff.ingest import readers, scan

BILL = "Electricity bill for March. Amount due 1200."


def test_long_chunks_are_split_and_others_stay_the_same(monkeypatch):
    monkeypatch.setattr(embed, "tokens", lambda text: len(text) // 2)  # number-heavy text: about 2 characters a token
    numbers = " ".join(f"{i},{i * 7}.50" for i in range(180))  # 180 words, about 1,800 characters
    plain = " ".join(["word"] * 180)
    out = embed.fit([(1, plain), (2, numbers)])
    assert out[0] == (1, plain)  # fits: unchanged
    parts = [t for p, t in out[1:]]
    assert all(p == 2 for p, _ in out[1:]) and len(parts) > 1
    assert all(len(t) // 2 <= embed.MAX_TOKENS for t in parts)
    assert " ".join(parts).split() == numbers.split()  # nothing lost, nothing repeated


def test_a_chunk_the_server_refuses_does_not_fail_the_file(conn, tmp_path, monkeypatch):
    def vectors(texts):
        if any("REFUSED" in t for t in texts):
            raise openai.BadRequestError("input (540 tokens) is larger than the max context size (512 tokens)",
                                         response=httpx.Response(400, request=httpx.Request("POST", "http://127.0.0.1:8081/v1/embeddings")), body=None)
        return [fake_vector(t) for t in texts]
    monkeypatch.setattr(embed, "_vectors", vectors)
    monkeypatch.setattr(embed, "embed", real_embed)
    monkeypatch.setattr(readers, "chunk", lambda pages: [(1, BILL), (1, "REFUSED " * 3)])
    (tmp_path / "a.txt").write_text(BILL)
    assert scan.scan(conn, tmp_path)["errors"] == 0
    row = conn.execute("SELECT status, error FROM files").fetchone()
    assert row["status"] == "indexed" and row["error"].startswith("1 of 2 chunks could not be embedded")
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 1
    assert [h.name for h in api.search(conn, "refused", mode="bm25")] == ["a.txt"]  # still found by keyword
