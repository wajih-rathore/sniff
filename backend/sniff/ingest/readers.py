"""Turn a file into (page number, text) pairs. Pages with no text layer go through OCR."""
from pathlib import Path

import docx
import pymupdf
from pptx import Presentation

from . import ocr

MIN_TEXT = 20  # fewer characters than this on a PDF page means it is a scan


def read(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path)
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
    if ext in (".txt", ".md"):
        return [(1, path.read_text(encoding="utf-8", errors="ignore"))]
    return [(1, ocr.image_file(path))]


def _pdf(path: Path) -> list[tuple[int, str]]:
    pages = []
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if len(text) < MIN_TEXT:
                text = ocr.image_bytes(page.get_pixmap(dpi=200).tobytes("png"))
            pages.append((i, text))
    return pages


def chunk(pages: list[tuple[int, str]], size: int = 180, overlap: int = 30) -> list[tuple[int, str]]:
    """Split each page into overlapping windows of `size` words, keeping the page number."""
    out = []
    for page, text in pages:
        words = text.split()
        for i in range(0, max(len(words) - overlap, 1), size - overlap):
            if words[i : i + size]:
                out.append((page, " ".join(words[i : i + size])))
    return out
