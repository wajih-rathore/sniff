"""Extract a document's key fields as validated JSON, then find each value on the page to mark it verified."""
import re
from datetime import date

from ..schemas import FACT_MODELS
from . import llm

PROMPT = """Extract these fields from a {doc_type} document: {fields}.
Rules:
- Copy values exactly as printed, except dates, which you write as YYYY-MM-DD, and amounts, which you write as plain numbers without currency or commas.
- billing_month is the month the bill is for, as YYYY-MM.
- Use null for any field that is not in the document. Do not guess.
Reply with JSON only."""

MAX_CHARS = 6000
MONEY_FIELDS = {"amount_due", "total_amount"}
ID_FIELDS = {"id_number", "account_number"}


def extract(doc_type: str, pages: list[tuple[int, str]]) -> list[dict]:
    model = FACT_MODELS[doc_type]
    text = "\n\n".join(f"[page {p}]\n{t}" for p, t in pages)[:MAX_CHARS]
    prompt = PROMPT.format(doc_type=doc_type.replace("_", " "), fields=", ".join(model.model_fields))
    out = llm.chat_json([{"role": "system", "content": prompt}, {"role": "user", "content": text}], model)

    facts = []
    for field, value in out.model_dump().items():
        if value is None or value == "":
            continue
        vtype = value_type(field, value)
        page, verified, source = locate(value, vtype, pages)
        stored = value.isoformat() if isinstance(value, date) else str(value)
        facts.append({"field": field, "value": stored, "value_type": vtype, "page": page, "verified": verified, "source_text": source})
    return facts


def value_type(field: str, value) -> str:
    if isinstance(value, date):
        return "date"
    if field in MONEY_FIELDS:
        return "money"
    if field in ID_FIELDS:
        return "id"
    return "text"


def spellings(value, vtype: str) -> list[str]:
    """Ways a value can be printed on a page, so we can find it."""
    if vtype == "date":
        d = value
        return [
            d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%d-%m-%Y"), d.strftime("%d.%m.%Y"),
            d.strftime("%d %b %Y"), d.strftime("%d %B %Y"), f"{d.day} {d:%b %Y}", f"{d.day} {d:%B %Y}",
            d.strftime("%b %d, %Y"), d.strftime("%B %d, %Y"), d.strftime("%m/%d/%Y"),
        ]
    if vtype == "money":
        x = float(value)
        return [f"{x:,.2f}", f"{x:.2f}", f"{x:,.0f}", f"{x:.0f}"] if x == int(x) else [f"{x:,.2f}", f"{x:.2f}"]
    return [str(value)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def locate(value, vtype: str, pages: list[tuple[int, str]]) -> tuple[int, bool, str | None]:
    """Return (page, verified, surrounding text). Unverified values default to page 1."""
    candidates = [_norm(s) for s in spellings(value, vtype)]
    for page, text in pages:
        t = _norm(text)
        for c in candidates:
            i = t.find(c)
            if i >= 0:
                return page, True, t[max(0, i - 40) : i + len(c) + 40]
    return (pages[0][0] if pages else 1), False, None
