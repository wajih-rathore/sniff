"""Verification v2: a fact is verified only if (1) its value is on the page, (2) one of its field's labels is next to
it, and (3) it passes its own rules and the cross-field rules. Everything here is plain code; no model is involved.

"Next to" uses the page layout: PyMuPDF word boxes for text PDFs, OCR boxes for scans and photos. Boxes are grouped
into rows (visual lines). For each place the value appears we find the nearest label of any field of this document
type, plus labels that belong to no field (DISTRACTORS). The value passes if its own label is near and no other
label is nearer. Plain text without layout (Word, text files) uses line order: same line or one or two lines away.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from .schemas import DISTRACTORS, FIELDS

Box = tuple[float, float, float, float, str]  # x0, y0, x1, y1, text
TOLERANCE = 1.0  # money cross-checks allow rounding by up to 1 unit
CNIC = re.compile(r"\d{5}-\d{7}-\d")
HEADER_ROWS = 3
# day-first, as on the documents we target; ISO first so already-normalised values pass
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y",
                "%b %d %Y", "%B %d %Y", "%d/%m/%y")
MONTH_FORMATS = ("%Y-%m", "%m/%Y", "%m-%Y", "%m.%Y", "%b %Y", "%B %Y", "%b-%Y", "%B-%Y", "%b-%y", "%b %y")
VALUE_TYPES = {"date": "date", "money": "money", "id": "id"}  # stored value_type per kind; the rest are "text"


# ---------- reading a printed value ----------
def parse(kind: str, raw: str):
    """The printed text as a typed value (date, "YYYY-MM", float, "paid"/"unpaid" or str), or None if unreadable."""
    s = re.sub(r"\s+", " ", raw.replace(",", " ")).strip()
    if kind == "money":
        m = re.search(r"\d+(?:\.\d+)?", raw.replace(",", ""))
        return float(m.group()) if m else None
    if kind == "month":
        for fmt in MONTH_FORMATS:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m")
            except ValueError:
                pass
        d = parse_date(s)
        return d.strftime("%Y-%m") if d else None
    if kind == "date":
        return parse_date(s)
    if kind == "status":
        low = raw.lower()
        return "unpaid" if re.search(r"unpaid|not paid|outstanding", low) else "paid" if "paid" in low else None
    if kind == "id":
        return re.sub(r"\s*-\s*", "-", raw.strip()) or None
    return raw.strip() or None


def parse_date(s: str) -> date | None:
    s = re.sub(r"(\d)(st|nd|rd|th)", r"\1 ", s)
    s = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", s)  # OCR drops spaces: "06Sep2026", "04NoV2026"
    s = re.sub(r"\s+", " ", re.sub(r"\bday of\b", " ", s)).strip()  # "10th day of July, 2025"
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def stored(value) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def typed(kind: str, text: str):
    """A value as stored in the facts table, back to its Python type."""
    return date.fromisoformat(text) if kind == "date" else float(text) if kind == "money" else text


# ---------- rules on one value ----------
def field_problem(kind: str, value, required: bool = False) -> str | None:
    """Why a parsed value breaks its field's rules, or None if it is fine."""
    if kind == "date":
        return None if 1900 <= value.year <= 2100 else "date is not plausible"
    if kind == "month":
        return None if re.fullmatch(r"(19|20)\d\d-(0[1-9]|1[0-2])", value) else "not a month"
    if kind == "money":
        if value < 0 or (required and value == 0):
            return "amount is not positive"
        if abs(value * 100 - round(value * 100)) > 1e-6:
            return "amount has more than 2 decimals"
        return None
    if kind == "id":
        if sum(c.isdigit() for c in value) == 13 and not CNIC.fullmatch(value):
            return "13-digit ID number is not in CNIC form #####-#######-#"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/-]{3,24}", value) or sum(c.isdigit() for c in value) < 3:
            return "does not look like an ID or account number"
        return None
    if kind == "status":
        return None if value in ("paid", "unpaid") else "status is not paid or unpaid"
    return None if 2 <= len(value) <= 120 and re.search(r"[A-Za-z]", value) else "text is empty or too long"


# ---------- rules across fields ----------
def cross_problems(doc_type: str, v: dict) -> list[tuple[tuple[str, ...], str]]:
    """(fields involved, reason) for each cross-field rule the parsed values break. Missing values are skipped."""
    out = []

    def rule(fields, ok, reason):
        if all(v.get(f) is not None for f in fields) and not ok():
            out.append((fields, reason))

    if doc_type == "utility_bill":
        rule(("bill_date", "due_date"), lambda: v["due_date"] >= v["bill_date"], "due date before bill date")
        rule(("bill_date", "billing_month"), lambda: v["bill_date"].strftime("%Y-%m") >= v["billing_month"], "bill date before the billing month")
        rule(("bill_date", "payment_date"), lambda: v["payment_date"] >= v["bill_date"], "payment date before bill date")
        rule(("current_charges", "amount_due"), lambda: bill_adds_up(v), "amount due is not current charges + previous balance (+ tax)")
    elif doc_type == "id_card":
        rule(("issue_date", "expiry_date"), lambda: v["expiry_date"] > v["issue_date"], "expiry date not after issue date")
        rule(("date_of_birth", "issue_date"), lambda: v["date_of_birth"] < v["issue_date"], "date of birth not before issue date")
    elif doc_type == "receipt":
        rule(("subtotal", "total_amount"), lambda: abs(v["subtotal"] + (v.get("tax") or 0) - v["total_amount"]) <= TOLERANCE,
             "total is not subtotal + tax")
        rule(("date", "warranty_until"), lambda: v["warranty_until"] > v["date"], "warranty ends before the purchase date")
    return out


def bill_adds_up(v: dict) -> bool:
    base = v["current_charges"] + (v.get("previous_balance") or 0)
    return abs(v["amount_due"] - base) <= TOLERANCE or abs(v["amount_due"] - base - (v.get("tax") or 0)) <= TOLERANCE


def spend(v: dict) -> float:
    """Money spent on one bill: its current charges, plus tax when the bill lists tax outside the current charges."""
    tax = v.get("tax") or 0
    if tax and v.get("amount_due") is not None:
        base = v["current_charges"] + (v.get("previous_balance") or 0)
        if abs(v["amount_due"] - base) > TOLERANCE and abs(v["amount_due"] - base - tax) <= TOLERANCE:
            return v["current_charges"] + tax
    return v["current_charges"]


# ---------- the page as rows ----------
@dataclass
class Row:
    text: str
    y0: float
    y1: float
    h: float  # line height, the unit for "near"
    parts: list[tuple[int, int, float, float]]  # (char start, char end, x0, x1) of each box in `text`

    def x_extent(self, s: int, e: int) -> tuple[float, float]:
        xs = [(x0, x1) for a, b, x0, x1 in self.parts if a < e and b > s] or [(self.parts[0][2], self.parts[-1][3])]
        return min(x for x, _ in xs), max(x for _, x in xs)


def rows_from_boxes(boxes: list[Box]) -> list[Row]:
    items = sorted(boxes, key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    groups, cur, cy, ch = [], [], 0.0, 0.0
    for b in items:
        y, h = (b[1] + b[3]) / 2, b[3] - b[1]
        if cur and abs(y - cy) > max(ch, h) * 0.5:
            groups.append(cur)
            cur = []
        if not cur:
            cy, ch = y, h
        cur.append(b)
    if cur:
        groups.append(cur)
    rows = []
    for g in groups:
        g.sort(key=lambda b: b[0])
        text, parts = "", []
        for x0, _, x1, _, t in g:
            text += (" " if text else "")
            parts.append((len(text), len(text) + len(t), x0, x1))
            text += t
        rows.append(Row(text, min(b[1] for b in g), max(b[3] for b in g), max(b[3] - b[1] for b in g), parts))
    return rows


def rows_from_text(text: str) -> list[Row]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [Row(ln, i, i + 1, 1, [(0, len(ln), 0, 1)]) for i, ln in enumerate(lines)]


def merge(a: Row, b: Row) -> Row:
    """Two consecutive rows as one, for values wrapped over two lines (long titles)."""
    off = len(a.text) + 1
    return Row(a.text + " " + b.text, a.y0, b.y1, max(a.h, b.h), a.parts + [(s + off, e + off, x0, x1) for s, e, x0, x1 in b.parts])


# ---------- finding values and labels ----------
def _squash(s: str) -> tuple[str, list[int]]:
    keep = [i for i, c in enumerate(s) if not c.isspace()]
    return "".join(s[i] for i in keep).lower(), keep


def find(needles: set[str], row: Row) -> list[tuple[int, int]]:
    """Where any of the needles appears in the row, ignoring spaces and case (OCR adds and drops spaces)."""
    sq, idx = _squash(row.text)
    out = []
    for n in needles:
        n = re.sub(r"\s+", "", n).lower()
        start = sq.find(n) if n else -1
        while start >= 0:
            out.append((idx[start], idx[start + len(n) - 1] + 1))
            start = sq.find(n, start + 1)
    return out


def find_amount(value: float, row: Row) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", row.text)
            if abs(float(m.group().replace(",", "")) - value) < 0.005]


def needles(kind: str, raw: str, value) -> set[str]:
    """The printed text, plus the parsed value written in common formats (in case the model tidied it)."""
    out = {raw.strip()}
    if kind == "date":
        out |= {value.strftime(f) for f in DATE_FORMATS}
    elif kind == "month":
        d = date(int(value[:4]), int(value[5:]), 1)
        out |= {d.strftime(f) for f in MONTH_FORMATS}
    return out


@lru_cache(None)
def labels(doc_type: str) -> list[tuple[str | None, str, re.Pattern]]:
    """(field or None for distractors, label, pattern) for every label of this document type."""
    items = [(f, a) for f, spec in FIELDS[doc_type].items() for a in spec.anchors] + [(None, a) for a in DISTRACTORS.get(doc_type, ())]
    return [(f, a, re.compile(r"\W*".join(map(re.escape, a.split())), re.I)) for f, a in items]


def _edge(text: str, i: int) -> bool:
    """True if a word starts or ends at i. OCR often drops spaces ("IssuedbyBrightpath"), so a change from lower to
    upper case also counts; "Subtotal" still does not contain the word "total"."""
    if i <= 0 or i >= len(text) or not (text[i - 1].isalnum() and text[i].isalnum()):
        return True
    return text[i - 1].islower() and text[i].isupper()


def label_spans(label: str, pat: re.Pattern, text: str):
    """Where a label appears. Short labels must be whole words; labels of 6+ letters may be run together with the
    next word, as OCR prints "Warrantyvaliduntil"."""
    long = len(re.sub(r"\W", "", label)) >= 6
    return [m.span() for m in pat.finditer(text) if long or (_edge(text, m.start()) and _edge(text, m.end()))]


def _distance(vrow: Row, vs: int, ve: int, arow: Row, as_: int, ae: int, same: bool):
    """Sort key for how close a label is to a value, or None if it is not near. Smaller is closer."""
    if same:
        if ae <= vs:
            return (0, vs - ae)  # label before the value on the same line: the usual "Label: value"
        if as_ < ve and ae > vs:
            return (0, 0)  # label inside the value, e.g. "University" in "Lakeshore University"
    vx0, vx1 = vrow.x_extent(vs, ve)
    ax0, ax1 = arow.x_extent(as_, ae)
    dx = max(0.0, ax0 - vx1, vx0 - ax1)
    if same:
        return (1, dx)  # label after the value on the same line
    h = max(vrow.h, arow.h)
    below = arow.y0 >= vrow.y1 - h * 0.25
    dy = max(0.0, arow.y0 - vrow.y1, vrow.y0 - arow.y1)
    if dy > 1.6 * h or dx > 8 * h:
        return None
    return (1, dy + dx + (h if below else 0))  # labels sit above or left of their values far more often than below


def check_anchor(doc_type: str, field: str, kind: str, raw: str, value, page_rows: list[tuple[int, list[Row]]]):
    """Returns (found, anchored, page, source text, reason)."""
    spec = FIELDS[doc_type][field]
    want = needles(kind, raw, value)
    first, why = None, None
    for page, rows in page_rows:
        span = 1
        hits = [(i, r, s, e) for i, r in enumerate(rows) for s, e in (find_amount(value, r) if kind == "money" else find(want, r))]
        if not hits:  # a value wrapped over two lines, like a long certificate title
            span = 2
            hits = [(i, r, s, e) for i in range(len(rows) - 1) for r in [merge(rows[i], rows[i + 1])] for s, e in find(want, r)]
        for i, vrow, vs, ve in hits:
            first = first or (page, vrow.text)
            if spec.header and i < HEADER_ROWS:
                return True, True, page, vrow.text[:200], None
            own, other = None, None
            for j, arow in [(None, vrow)] + [(j, r) for j, r in enumerate(rows) if not i <= j < i + span]:
                same = j is None
                for f, label, pat in labels(doc_type):
                    for s, e in label_spans(label, pat, arow.text):
                        if same and f != field and s < ve and e > vs:
                            continue  # another field's word inside this value is part of the value, not a label
                        k = _distance(vrow, vs, ve, arow, s, e, same)
                        if k is None:
                            continue
                        k = (*k, s - e)  # equally near: the longer label is more specific ("Sub Total" beats "Total")
                        if f == field and (own is None or k < own[0]):
                            own = (k, label, arow.text)
                        elif f != field and (other is None or k < other[0]):
                            other = (k, label)
            if own and (other is None or own[0] <= other[0]):
                src = vrow.text if own[2] == vrow.text else f"{own[2]} / {vrow.text}"
                return True, True, page, src[:200], None
            if why is None:
                why = (f"found next to '{other[1]}', not a '{spec.anchors[0]}' label" if other
                       else f"found but not next to a '{spec.anchors[0]}' label")
    if first:
        return True, False, first[0], first[1][:200], why
    return False, False, None, None, "value not found on the page"


def page_rows(pages: list[tuple[int, str]], boxes: dict[int, list[Box]]) -> list[tuple[int, list[Row]]]:
    return [(p, rows_from_boxes(boxes[p]) if boxes.get(p) else rows_from_text(t)) for p, t in pages]


def check(doc_type: str, facts: list[dict], pages: list[tuple[int, str]], boxes: dict[int, list[Box]]) -> None:
    """Fill page, verified, status, reason and source_text on each fact. A fact needs: "field", "raw", "value"."""
    rows = page_rows(pages, boxes)
    for f in facts:
        spec = FIELDS[doc_type][f["field"]]
        found, anchored, page, source, reason = check_anchor(doc_type, f["field"], spec.kind, f["raw"], f["value"], rows)
        f["page"] = page or (pages[0][0] if pages else 1)
        f["source_text"] = source
        f["reasons"] = [reason] if reason else []
        if problem := field_problem(spec.kind, f["value"], spec.required):
            f["reasons"].append(problem)
    by_field = {f["field"]: f for f in facts}
    for fields, reason in cross_problems(doc_type, {k: f["value"] for k, f in by_field.items()}):
        for name in fields:
            by_field[name]["reasons"].append(reason)
    for f in facts:
        reasons = f.pop("reasons")
        f["verified"] = not reasons
        f["status"] = "verified" if not reasons else "needs_review"
        f["reason"] = "; ".join(reasons) or None
