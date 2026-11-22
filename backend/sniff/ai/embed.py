from functools import lru_cache

import numpy as np

from .. import config


@lru_cache(1)
def _model():
    from fastembed import TextEmbedding  # downloads the ONNX model on first use

    return TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.MODELS_DIR / "fastembed"))


def embed(texts: list[str]) -> list[np.ndarray]:
    return [v.astype(np.float32) for v in _model().embed(texts)]


def embed_query(text: str) -> np.ndarray:
    return next(iter(_model().query_embed(text))).astype(np.float32)
