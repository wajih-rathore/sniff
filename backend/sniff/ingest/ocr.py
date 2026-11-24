import math
from functools import lru_cache
from pathlib import Path


@lru_cache(1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR  # loads ONNX models, so only on first use

    return RapidOCR()


def read(data: bytes) -> tuple[str, list[tuple[float, float, float, float, str]]]:
    """Text in reading order, plus each OCR box as (x0, y0, x1, y1, text) for the layout checks in verify.py."""
    result = _engine()(data)[0] or []
    # scans and photos are often tilted a few degrees; turn the boxes back so a label and its value share a row
    angles = sorted(math.atan2(b[1][1] - b[0][1], b[1][0] - b[0][0]) for b, _, _ in result if abs(b[1][0] - b[0][0]) > 3 * abs(b[3][1] - b[0][1]))
    a = angles[len(angles) // 2] if angles else 0.0
    cos, sin = math.cos(a), math.sin(a)
    boxes = []
    for b, text, _ in result:
        pts = [(x * cos + y * sin, -x * sin + y * cos) for x, y in b]
        boxes.append((min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts), text))
    return join_lines(result), boxes


def image_bytes(data: bytes) -> str:
    return read(data)[0]


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
