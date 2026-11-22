"""Extract a document's key fields. The LLM copies each value exactly as printed (structured JSON output);
code then parses dates, months and amounts, checks them, and looks for the copied text on the page."""
import re
from datetime import date, datetime

from pydantic import ValidationError, create_model

from ..schemas import FACT_MODELS
from . import llm

PROMPT = """Extract these fields from a {doc_type} document: {fields}.
Copy each value exactly as it is printed, including dates and amounts (for example "21.03.2003", "Rs. 4,320/-", "July 2026").
billing_month is the month the bill is for. Use null for any field that is not in the document. Do not guess.
Reply with JSON only."""

MAX_CHARS = 6000
MONEY_FIELDS = {"amount_due", "total_amount"}
ID_FIELDS = {"id_number", "account_number"}
MONTH_FIELDS = {"billing_month"}
# day-first, as on the documents we target; ISO first so already-normalised values pass
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y",
                "%b %d %Y", "%B %d %Y", "%d/%m/%y")
MONTH_FORMATS = ("%Y-%m", "%m/%Y", "%m-%Y", "%m.%Y", "%b %Y", "%B %Y", "%b-%Y", "%B-%Y", "%b-%y", "%b %y")


def _raw_model(doc_type: str):
    """Same fields as the typed model, but every value is the string as printed."""
    return create_model(f"{FACT_MODELS[doc_type].__name__}Printed", **{f: (str | None, None) for f in FACT_MODELS[doc_type].model_fields})


def extract(doc_type: str, pages: list[tuple[int, str]]) -> list[dict]:
    model = FACT_MODELS[doc_type]
    text = "\n\n".join(f"[page {p}]\n{t}" for p, t in pages)[:MAX_CHARS]
    prompt = PROMPT.format(doc_type=doc_type.replace("_", " "), fields=", ".join(model.model_fields))
    printed = llm.chat_json([{"role": "system", "content": prompt}, {"role": "user", "content": text}], _raw_model(doc_type))

    facts = []
    for field, raw in printed.model_dump().items():
        if not raw or not raw.strip():
            continue
        value = parse(field, model.model_fields[field].annotation, raw)
        if value is None:
            continue
        try:  # plausibility checks live on the typed model (e.g. dates between 1900 and 2100)
            model(**{field: value})
        except ValidationError:
            continue
        page, verified, source = locate(raw, pages)
        stored = value.isoformat() if isinstance(value, date) else str(value)
        facts.append({"field": field, "value": stored, "value_type": value_type(field, value), "page": page,
                      "verified": verified, "source_text": source})
    return facts


def parse(field: str, annotation, raw: str):
    s = re.sub(r"\s+", " ", raw.replace(",", " ")).strip()
    if field in MONEY_FIELDS:
        m = re.search(r"\d+(?:\.\d+)?", raw.replace(",", ""))
        return float(m.group()) if m else None
    if field in MONTH_FIELDS:
        for fmt in MONTH_FORMATS:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m")
            except ValueError:
                pass
        d = parse_date(s)
        return d.strftime("%Y-%m") if d else None
    if "date" in str(annotation).lower():
        return parse_date(s)
    return raw.strip()


def parse_date(s: str) -> date | None:
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def value_type(field: str, value) -> str:
    if isinstance(value, date):
        return "date"
    if field in MONEY_FIELDS:
        return "money"
    if field in ID_FIELDS:
        return "id"
    return "text"


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()  # OCR often drops or adds spaces, so ignore them


def locate(raw: str, pages: list[tuple[int, str]]) -> tuple[int, bool, str | None]:
    """Find the printed value on a page. Returns (page, verified, surrounding text); unverified defaults to page 1."""
    needle = _norm(raw)
    for page, text in pages:
        i = _norm(text).find(needle)
        if i >= 0:
            flat = re.sub(r"\s+", " ", text)
            j = flat.lower().find(raw.strip().lower()[:12])
            return page, True, flat[max(0, j - 40): j + len(raw) + 40] if j >= 0 else None
    return (pages[0][0] if pages else 1), False, None
