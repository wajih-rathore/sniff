"""Embeddings from a local llama.cpp server started with --embedding (OpenAI-compatible /v1/embeddings)."""
from functools import lru_cache

import numpy as np
from openai import OpenAI

from .. import config

BATCH = 32
# (document prefix, query prefix) each model was trained with; MiniLM takes plain text
PREFIXES = {
    "nomic": ("search_document: ", "search_query: "),
    "gemma": ("title: none | text: ", "task: search result | query: "),
}


@lru_cache(1)
def _client() -> OpenAI:
    return OpenAI(base_url=config.EMBED_URL, api_key="local", timeout=180)


def _prefixes() -> tuple[str, str]:
    return next((p for key, p in PREFIXES.items() if key in config.EMBED_MODEL.lower()), ("", ""))


def _vectors(texts: list[str]) -> list[np.ndarray]:
    out = []
    for i in range(0, len(texts), BATCH):
        r = _client().embeddings.create(model=config.EMBED_MODEL, input=texts[i : i + BATCH])
        out += [np.asarray(d.embedding, dtype=np.float32) for d in sorted(r.data, key=lambda d: d.index)]
    return out


def embed(texts: list[str]) -> list[np.ndarray]:
    return _vectors([_prefixes()[0] + t for t in texts])


def embed_query(text: str) -> np.ndarray:
    return _vectors([_prefixes()[1] + text])[0]
