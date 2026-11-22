"""Tests run without the LLM server and without downloading models: AI calls are replaced by small fakes."""
import hashlib

import numpy as np
import pytest

from sniff import config, db
from sniff.ai import classify, embed, extract


def fake_vector(text: str) -> np.ndarray:
    seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(config.EMBED_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "test.db")
    monkeypatch.setattr(embed, "embed", lambda texts: [fake_vector(t) for t in texts])
    monkeypatch.setattr(embed, "embed_query", fake_vector)
    monkeypatch.setattr(classify, "classify", lambda pages: "utility_bill" if any("bill" in t.lower() for _, t in pages) else "other")
    monkeypatch.setattr(extract, "extract", lambda doc_type, pages: [
        {"field": "amount_due", "value": "1200.0", "value_type": "money", "page": 1, "verified": True, "source_text": None}
    ])
    c = db.connect()
    yield c
    c.close()
