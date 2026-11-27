"""Fields from rules, before any model: the value printed next to each field's label.

For every label of a field (its anchors in schemas.FIELDS), the candidates are the rest of the label's cell, the next
cell on the same row, and the cell under the label on the next two rows. Each candidate is cut to the shape of its
kind (a date, an amount, an ID, a name) and kept only if verify.check_anchor confirms that its own label is the
nearest one and the kind's rules pass. So a value the rules give would also pass the label check; a field they cannot
fill is left empty here, for the model. No model is involved.

window() is the other half: the few lines near the labels, which is all the model is shown when it has to help.
"""
import re

from .. import verify
from ..schemas import FIELDS

DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s?[/.-]\s?\d{1,2}\s?[/.-]\s?\d{2,4}|\d{1,2}-[A-Za-z]{3,9}-\d{2,4}"
                  r"|\d{1,2}(?:st|nd|rd|th)?\s*(?:day\s+of\s+)?[A-Za-z]{3,9}\.?,?\s*\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}")
MONTH = re.compile(r"\d{4}-\d{2}(?![-\d])|[A-Za-z]{3,9}[\s-]?\d{4}|[A-Za-z]{3}-\d{2}\b|\d{1,2}[/.-]\d{4}")
AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")
CODE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9/]|\s?-\s?)*[A-Za-z0-9]")
STATUS = re.compile(r"unpaid|not paid|outstanding|paid", re.I)
SEP = " \t:;#-–.,|*"
# words of document titles and form headings: a header line made only of these is not an organisation's name
GENERIC = set("""receipt invoice tax sales cash memo bill electricity gas internet water statement monthly account warranty card
certificate transcript official academic record offer letter employment insurance policy schedule tenancy rent agreement
fee voucher challan bank copy customer student institution original of the and for order confirmation completion
achievement passport driving licence license national identity id staff service document page no date to office
controller examinations guarantee e-receipt e thank you your""".split())


def _cells(row: verify.Row) -> list[tuple[int, int]]:
    """Character spans of the row's cells: its layout boxes, or for plain text lines the pieces between wide gaps."""
    if len(row.parts) > 1:
        return [(s, e) for s, e, _, _ in row.parts]
    spans, start = [], 0
    for m in re.finditer(r"\s{3,}|\t", row.text):
        spans.append((start, m.start()))
        start = m.end()
    return spans + [(start, len(row.text))]


def _cell_at(cells, i):
    return next(((s, e) for s, e in cells if s <= i < e), (i, i))


def _below(rows, i, x0, x1, span_rows=2):
    """The cell on each of the next rows that sits under the x range x0..x1 (the one overlapping it most)."""
    out = []
    for row in rows[i + 1 : i + 1 + span_rows]:
        best, score = None, None
        for s, e in _cells(row):
            if not row.text[s:e].strip():
                continue
            cx0, cx1 = row.x_extent(s, e)
            overlap = min(x1, cx1) - max(x0, cx0)
            k = overlap if overlap > 0 else -abs(cx0 - x0) - 1e6
            if score is None or k > score:
                best, score = row.text[s:e], k
        if best:
            out.append(best)
    return out


def _clean(text: str, doc_type: str, field: str) -> str:
    """A text value: cut at the next label printed after it, and trimmed of separators. A leading label of this field
    is dropped ("Issued by X" gives X); a leading label of another field means there is no value here. An
    organisation's own label words belong to its name ("Metro Gas Ltd"), so they do not cut it."""
    keep_own = FIELDS[doc_type][field].header
    text = text.strip(SEP)
    while True:
        spans = [(s, e, f) for f, label, pat in verify.labels(doc_type) for s, e in verify.label_spans(label, pat, text)]
        own_lead = max((e for s, e, f in spans if s == 0 and f == field), default=0)
        if not own_lead:
            break
        text = text[own_lead:].strip(SEP)
    cut = len(text)
    for s, _, f in spans:
        if s == 0:
            return ""
        if not (keep_own and f == field):
            cut = min(cut, s)
    return text[:cut].strip(SEP + "( ")[:120]


def _generic(text: str) -> bool:
    words = [w.lower() for w in re.findall(r"[A-Z]?[a-z]+(?:-[a-z]+)?|[A-Z]+(?![a-z])", text)]  # OCR runs words together
    return not words or all(w in GENERIC for w in words)


def _values(kind: str, text: str) -> list[str]:
    """The printed pieces of `text` that could be a value of this kind, in reading order."""
    if kind == "date":
        return [m.group() for m in DATE.finditer(text)]
    if kind == "month":
        return [m.group() for m in MONTH.finditer(text)] + [m.group() for m in DATE.finditer(text)]
    if kind in ("money", "gpa"):
        return [m.group() for m in AMOUNT.finditer(text) if not re.match(r"\s*[%$]", text[m.end():])]  # "17%" (OCR: "17$")
    if kind in ("id", "serial", "passport"):
        return [m.group() for m in CODE.finditer(text) if any(c.isdigit() for c in m.group())]
    if kind == "status":
        return [m.group() for m in STATUS.finditer(text)]
    return [text]


def _candidates(doc_type: str, field: str, page_rows):
    """(page, raw text) candidates for one field, label by label, top of the page first."""
    spec = FIELDS[doc_type][field]
    pats = [(label, pat) for f, label, pat in verify.labels(doc_type) if f == field]
    for page, rows in page_rows:
        for i, row in enumerate(rows):
            cells = _cells(row)
            spans = [(s, e, f) for f, label, pat in verify.labels(doc_type) for s, e in verify.label_spans(label, pat, row.text)]
            for label, pat in pats:
                for ls, le in verify.label_spans(label, pat, row.text):
                    if any(s <= ls and e >= le and e - s > le - ls for s, e, _ in spans):
                        continue  # part of a longer label: "Program" in "Programme", "Salary" in "Basic Salary"
                    cs, ce = _cell_at(cells, ls)
                    pieces = []
                    # a label word after other words ("the position of ...", "Bosic Salary") is not a form label
                    if not (ls - cs > 1 and len(label.split()) < {"text": 3, "status": 3, "money": 2}.get(spec.kind, 1)):
                        if spec.kind == "text":
                            pieces.append(row.text[le:ce])  # "Label: value" in one cell
                            pieces += [row.text[s:e] for s, e in cells if s >= ce][:1]  # the next cell on the row
                        else:  # the rest of the row up to another field's label: OCR may split "01 Jan 2025" into cells
                            pieces.append(row.text[le:min((s for s, _, f in spans if s >= le and f != field), default=len(row.text))])
                        x0, x1 = row.x_extent(ls, le)
                        pieces += _below(rows, i, x0, x1)  # value under its label
                    if spec.header or spec.kind == "status" and STATUS.fullmatch(row.text[cs:ce].strip(SEP)):
                        pieces.append(row.text[cs:ce])  # the label is part of the value: "Lakeshore University", "PAID"
                    for p in pieces:
                        if spec.kind == "text":
                            p = _clean(p, doc_type, field)
                            if p and not (spec.header and _generic(p)):
                                yield page, p
                        else:
                            for v in _values(spec.kind, p):
                                yield page, v
        if spec.header and page == page_rows[0][0]:  # the first name-like line of the letterhead, with no label at all
            for p in (_clean(row.text[s:e], doc_type, field) for row in rows[: verify.HEADER_ROWS] for s, e in _cells(row)):
                if p and not _generic(p) and not re.search(r"\d", p):
                    yield page, p
                    break


def extract(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None = None) -> dict[str, str]:
    """{field: printed value} for the fields the rules can read with their label next to them. Others are left out."""
    rows = verify.page_rows(pages, boxes or {})
    out = {}
    for field, spec in FIELDS[doc_type].items():
        tried = set()
        for _, raw in _candidates(doc_type, field, rows):
            raw = raw.strip()
            if not raw or raw in tried:
                continue
            tried.add(raw)
            value = verify.parse(spec.kind, raw)
            if value is None or verify.field_problem(spec.kind, value, spec.required):
                continue
            found, anchored, *_ = verify.check_anchor(doc_type, field, spec.kind, raw, value, rows)
            if anchored:
                out[field] = raw
                break
    return out


def window(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None = None, fields=None, before: int = 0,
           after: int = 1) -> list[tuple[int, str]]:
    """The candidate lines for `fields` (default: all of the type's): each row with one of their labels, `before`
    rows above it and `after` rows below, plus the letterhead when a field may stand there. Pages keep their numbers;
    rows keep their order. A document with none of the labels comes back whole."""
    fields = set(fields or FIELDS[doc_type])
    pats = [(label, pat) for f, label, pat in verify.labels(doc_type) if f in fields]
    header = any(FIELDS[doc_type][f].header for f in fields)
    out = []
    for page, rows in verify.page_rows(pages, boxes or {}):
        keep = set(range(min(verify.HEADER_ROWS, len(rows)))) if header and page == pages[0][0] else set()
        for i, row in enumerate(rows):
            if any(verify.label_spans(label, pat, row.text) for label, pat in pats):
                keep |= set(range(max(0, i - before), min(len(rows), i + after + 1)))
        if keep:
            out.append((page, "\n".join(rows[i].text for i in sorted(keep))))
    return out if any(t for _, t in out) else pages
