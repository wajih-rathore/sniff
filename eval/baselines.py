"""Simple non-AI baselines the AI components are compared against: keyword rules and regex rules."""
import re
from datetime import date, datetime

TYPE_KEYWORDS = {
    "id_card": ["identity card", "id card", "date of birth", "national id", "card no"],
    "utility_bill": ["bill", "amount due", "billing", "account no", "units", "due date", "electric", "gas", "internet"],
    "certificate": ["certificate", "certify", "degree", "diploma", "awarded", "university", "institute"],
    "receipt": ["receipt", "invoice", "total", "qty", "cashier", "thank you for shopping", "warranty"],
    "passport": ["passport", "nationality", "place of birth", "p<"],
    "transcript": ["transcript", "cgpa", "gpa", "credit hours", "grade"],
    "bank_statement": ["statement", "opening balance", "closing balance", "iban", "debit", "credit"],
    "warranty_card": ["warranty", "serial", "model", "dealer", "purchase"],
    "offer_letter": ["offer", "salary", "joining", "position", "designation", "employment"],
    "insurance_policy": ["insurance", "policy", "premium", "insured", "cover"],
    "tenancy_agreement": ["tenancy", "tenant", "landlord", "rent", "lessee", "premises"],
    "fee_voucher": ["fee", "voucher", "challan", "tuition", "payable"],
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
    return m.group(m.re.groups) if m else None  # the value is the last group; the label patterns have groups too


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
    if doc_type == "passport":
        return {"passport_number": (m.group(0) if (m := re.search(r"[A-Z]{2}\d{7}", text)) else None),
                "date_of_birth": _date(r"(date of birth|dob)", text), "expiry_date": _date(r"(expiry|valid until)", text)}
    if doc_type == "transcript":
        v = after(r"(cgpa|cumulative gpa)", r"(\d\.\d{1,2})", text)
        return {"cgpa": float(v) if v else None, "issue_date": _date(r"(date of issue|issue date|issued on)", text)}
    if doc_type == "bank_statement":
        return {"opening_balance": _money(r"(opening balance|balance b/f)", text), "closing_balance": _money(r"(closing balance|balance c/f)", text)}
    if doc_type == "warranty_card":
        return {"purchase_date": _date(r"(purchase)", text), "warranty_until": _date(r"(valid until|valid till|warranty expires)", text)}
    if doc_type == "offer_letter":
        return {"monthly_salary": _money(r"(gross salary|monthly salary)", text), "start_date": _date(r"(joining date|date of joining)", text)}
    if doc_type == "insurance_policy":
        return {"premium": _money(r"(premium)", text), "start_date": _date(r"(from|start date|effective date)", text),
                "end_date": _date(r"(to|end date|expiry date)", text)}
    if doc_type == "tenancy_agreement":
        return {"monthly_rent": _money(r"(monthly rent|rent per month)", text), "start_date": _date(r"(commencement date|start date)", text),
                "end_date": _date(r"(expiry date|end date)", text)}
    if doc_type == "fee_voucher":
        return {"amount": _money(r"(total payable|total amount)", text), "due_date": _date(r"(due date|last date)", text)}
    return {}


FACTS_WORDS = re.compile(r"\b(how much|how many|total|sum|spen[dt]|pa(y|id)|cost|highest|lowest|largest|smallest|cheapest|"
                         r"most expensive|average|count|when|expir\w*|due|next|earliest|latest|oldest|newest|tax|"
                         r"balance|arrears|outstanding|duplicates?)\b", re.I)


def route_keywords(question: str) -> str:
    """Router baseline: questions with calculation or date words go to the facts table, the rest to search."""
    return "facts" if FACTS_WORDS.search(question) else "search"


def today() -> date:
    return date.today()
