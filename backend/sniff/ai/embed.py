"""Embeddings from a local llama.cpp server started with --embedding (OpenAI-compatible /v1/embeddings)."""
from functools import lru_cache

import numpy as np
from openai import OpenAI

from .. import config

BATCH = 32


@lru_cache(1)
def _client() -> OpenAI:
    return OpenAI(base_url=config.EMBED_URL, api_key="local", timeout=180)


def _vectors(texts: list[str]) -> list[np.ndarray]:
    out = []
    for i in range(0, len(texts), BATCH):
        r = _client().embeddings.create(model=config.EMBED_MODEL, input=texts[i : i + BATCH])
        out += [np.asarray(d.embedding, dtype=np.float32) for d in sorted(r.data, key=lambda d: d.index)]
    return out


def _gemma() -> bool:
    return "gemma" in config.EMBED_MODEL.lower()


def embed(texts: list[str]) -> list[np.ndarray]:
    # EmbeddingGemma was trained with these task prefixes; other models take plain text
    return _vectors([f"title: none | text: {t}" for t in texts] if _gemma() else texts)


def embed_query(text: str) -> np.ndarray:
    return _vectors([f"task: search result | query: {text}" if _gemma() else text])[0]
