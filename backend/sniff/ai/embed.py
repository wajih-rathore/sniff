"""Embeddings from a local llama.cpp server started with --embedding (OpenAI-compatible /v1/embeddings)."""
from functools import lru_cache

import httpx
import numpy as np
from openai import BadRequestError, OpenAI

from .. import config

BATCH = 32
MAX_TOKENS = 512  # all-MiniLM's input limit: llama-server refuses a longer input with HTTP 400, special tokens included
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


def embed(texts: list[str]) -> list[np.ndarray | None]:
    """One vector per chunk. If the server refuses an input, the others still get theirs and that one gets None."""
    texts = [_prefixes()[0] + t for t in texts]
    try:
        return _vectors(texts)
    except BadRequestError:
        out = []
        for t in texts:
            try:
                out += _vectors([t])
            except BadRequestError:
                out.append(None)
        return out


def tokens(text: str) -> int:
    """How many tokens the embedding server reads for this chunk, prefix and special tokens included."""
    r = httpx.post(config.EMBED_URL.removesuffix("/v1") + "/tokenize", json={"content": _prefixes()[0] + text, "add_special": True}, timeout=60)
    r.raise_for_status()
    return len(r.json()["tokens"])


def fit(pieces: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Split chunks longer than the model's input limit in half, by words, until each fits. Number-heavy text (a
    statement full of amounts) can pass 512 tokens in 180 words. A chunk that fits comes back unchanged, so files that
    indexed before keep exactly the same chunks."""
    return [(page, part) for page, text in pieces for part in _split(text)]


def _split(text: str) -> list[str]:
    # every token covers at least one character, so a short chunk cannot pass the limit and is not sent to the server
    if len(_prefixes()[0] + text) <= MAX_TOKENS - 2 or tokens(text) <= MAX_TOKENS:
        return [text]
    words = text.split()
    half = len(words) // 2
    parts = [" ".join(words[:half]), " ".join(words[half:])] if half else [text[: len(text) // 2], text[len(text) // 2 :]]
    return [p for part in parts for p in _split(part)]


def embed_query(text: str) -> np.ndarray:
    return _vectors([_prefixes()[1] + text])[0]
