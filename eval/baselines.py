"""Simple non-AI baselines the AI components are compared against: keyword rules and regex rules."""
import re
from datetime import date, datetime

TYPE_KEYWORDS = {
    "id_card": ["identity card", "id card", "date of birth", "national id", "card no"],
    "utility_bill": ["bill", "amount due", "billing", "account no", "units", "due date", "electric", "gas", "internet"],
    "certificate": ["certificate", "certify", "degree", "diploma", "awarded", "university", "institute"],
    "receipt": ["receipt", "invoice", "total", "qty", "cashier", "thank you for shopping", "warranty"],
}


def classify_keywords(text: str) -> str:
    t = text.lower()
    scores = {k: sum(t.count(w) for w in words) for k, words in TYPE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


DATE_RX = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2} [A-Za-z]{3,9},? \d{4}|[A-Za-z]{3,9} \d{1,2},? \d{4})"
MONEY_RX = r"(?:rs\.?|pkr)?\s*([\d,]+(?:\.\d{1,2})?)"


def parse_date(s: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s.replace(",", ""), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def after(label_rx: str, value_rx: str, text: str):
    m = re.search(label_rx + r"[^\n\d]{0,25}" + value_rx, text, re.I)
    return m.group(m.re.groups) if m else None


def _date(label_rx, text):
    v = after(label_rx, DATE_RX, text)
    return parse_date(v) if v else None


def _money(label_rx, text):
    v = after(label_rx, MONEY_RX, text)
    try:
        return float(v.replace(",", "")) if v else None
    except ValueError:
        return None


def extract_regex(doc_type: str, text: str) -> dict:
    if doc_type == "id_card":
        return {
            "id_number": (m.group(0) if (m := re.search(r"\d{5}-\d{7}-\d", text)) else None),
            "date_of_birth": _date(r"(date of birth|dob|born)", text),
            "issue_date": _date(r"(issue|issued)", text),
            "expiry_date": _date(r"(expiry|expires|valid until|valid till)", text),
        }
    if doc_type == "utility_bill":
        due = _date(r"(due date|pay by|last date)", text)
        return {
            "account_number": after(r"(account|acct|consumer)\s*(no|number|#)?", r"([A-Z0-9-]{6,})", text),
            "amount_due": _money(r"(amount due|amount payable|total due|payable)", text),
            "due_date": due,
        }
    if doc_type == "certificate":
        return {"issue_date": _date(r"(issued|date|dated|awarded on)", text)}
    if doc_type == "receipt":
        return {
            "date": _date(r"(date)", text),
            "total_amount": _money(r"(grand total|total amount|total)", text),
            "warranty_until": _date(r"(warranty)", text),
        }
    return {}


FACTS_WORDS = re.compile(r"\b(how much|how many|total|sum|spen[dt]|pa(y|id)|cost|highest|lowest|largest|smallest|cheapest|"
                         r"most expensive|average|count|when|expir\w*|due|next|earliest|latest|oldest|newest|tax|"
                         r"balance|arrears|outstanding|duplicates?)\b", re.I)


def route_keywords(question: str) -> str:
    """Router baseline: questions with calculation or date words go to the facts table, the rest to search."""
    return "facts" if FACTS_WORDS.search(question) else "search"


def today() -> date:
    return date.today()
