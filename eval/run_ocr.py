"""OCR: character error rate (CER) of RapidOCR vs Tesseract on scanned PDFs and photos with known text.

    uv run python ../eval/run_ocr.py                 # synthetic test scans and photos -> ocr.json
    uv run python ../eval/run_ocr.py --set hard      # phone photos, low-res scans, handwriting -> ocr_hard.json
    uv run python ../eval/run_ocr.py --limit 10      # quick check"""
import argparse
import shutil
from pathlib import Path

import pymupdf
from common import Timer, labels, latency, norm, path_of, save, stay_awake, stratified

from sniff.ingest import ocr

TESSERACT = shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def image_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(str(path)) as doc:
            return doc[0].get_pixmap(dpi=200).tobytes("png")
    return path.read_bytes()


def tesseract(data: bytes) -> str:
    import io

    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = TESSERACT
    return pytesseract.image_to_string(Image.open(io.BytesIO(data)))


def cer(pred: str, gold: str) -> float:
    """Levenshtein distance / length of the gold text, on lower-cased, space-normalised text."""
    a, b = norm(pred), norm(gold)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(b), 1)


def main(split: str, limit: int | None):
    items = stratified([l for l in labels(split) if l.get("format") in ("image", "pdf_scan") and l.get("text")], limit)
    rapid, tess, ms, failures = [], [], [], []
    for l in items:
        data = image_bytes(path_of(l))
        with Timer() as t:
            text = ocr.image_bytes(data)
        ms.append(t.ms)
        rapid.append(cer(text, l["text"]))
        tess.append(cer(tesseract(data), l["text"]))
        if rapid[-1] > 0.1:
            failures.append({"file": l["file"], "variant": l.get("variant"), "effects": l.get("effects"), "cer": round(rapid[-1], 3), "pred": text[:300], "gold": l["text"][:300]})
    save("ocr" if split == "test" else f"ocr_{split}", {
        "n": len(items),
        "rows": [
            {"system": "RapidOCR (PaddleOCR models)", "CER": sum(rapid) / len(rapid)},
            {"system": "Tesseract defaults (baseline)", "CER": sum(tess) / len(tess)},
        ],
        "latency_ms": latency(ms),
    }, failures)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="test", choices=["test", "hard"])
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    stay_awake()
    main(a.set, a.limit)
