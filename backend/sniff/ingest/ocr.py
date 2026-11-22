from functools import lru_cache
from pathlib import Path


@lru_cache(1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR  # loads ONNX models, so only on first use

    return RapidOCR()


def image_bytes(data: bytes) -> str:
    result, _ = _engine()(data)
    return join_lines(result or [])


def image_file(path: Path) -> str:
    return image_bytes(Path(path).read_bytes())


def join_lines(result: list) -> str:
    """RapidOCR returns boxes in no guaranteed order; rebuild lines top to bottom, left to right."""
    items = sorted(
        ((b[0][1] + b[2][1]) / 2, b[0][0], text, abs(b[2][1] - b[0][1]))
        for b, text, _score in result
    )
    lines, cur, cur_y, cur_h = [], [], 0.0, 0.0
    for y, x, text, h in items:
        if cur and abs(y - cur_y) > max(cur_h, h) * 0.5:
            lines.append(" ".join(t for _, t in sorted(cur)))
            cur = []
        if not cur:
            cur_y, cur_h = y, h
        cur.append((x, text))
    if cur:
        lines.append(" ".join(t for _, t in sorted(cur)))
    return "\n".join(lines)
