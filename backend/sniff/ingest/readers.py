"""Turn a file into (page number, text) pairs. Pages with no text layer go through OCR."""
from pathlib import Path

import docx
import pymupdf
from pptx import Presentation

from . import ocr

MIN_TEXT = 20  # fewer characters than this on a PDF page means it is a scan
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def read(path: Path) -> list[tuple[int, str]]:
    return read_layout(path)[0]


def read_layout(path: Path) -> tuple[list[tuple[int, str]], dict[int, list[tuple]]]:
    """(page, text) pairs, plus word or OCR boxes per page where the format has a layout (PDFs and images)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path)
    if ext in IMAGES:
        text, boxes = ocr.read(path.read_bytes())
        return [(1, text)], {1: boxes}
    return _plain(path, ext), {}


def _plain(path: Path, ext: str) -> list[tuple[int, str]]:
    if ext == ".docx":
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        parts += [cell.text for t in d.tables for row in t.rows for cell in row.cells]
        return [(1, "\n".join(p for p in parts if p.strip()))]
    if ext == ".pptx":
        prs = Presentation(str(path))
        return [
            (i, "\n".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame))
            for i, slide in enumerate(prs.slides, 1)
        ]
    return [(1, path.read_text(encoding="utf-8", errors="ignore"))]


def _pdf(path: Path):
    pages, boxes = [], {}
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if len(text) < MIN_TEXT:
                text, boxes[i] = ocr.read(page.get_pixmap(dpi=200).tobytes("png"))
            else:
                lines: dict[tuple, list] = {}  # words grouped into PyMuPDF's own lines
                for x0, y0, x1, y1, w, block, line, _ in page.get_text("words"):
                    lines.setdefault((block, line), []).append((x0, y0, x1, y1, w))
                boxes[i] = [(min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws),
                             " ".join(w[4] for w in ws)) for ws in lines.values()]
            pages.append((i, text))
    return pages, boxes


def chunk(pages: list[tuple[int, str]], size: int = 180, overlap: int = 30) -> list[tuple[int, str]]:
    """Split each page into overlapping windows of `size` words, keeping the page number."""
    out = []
    for page, text in pages:
        words = text.split()
        for i in range(0, max(len(words) - overlap, 1), size - overlap):
            if words[i : i + size]:
                out.append((page, " ".join(words[i : i + size])))
    return out
