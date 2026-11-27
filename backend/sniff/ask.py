"""Answer a question. The LLM only routes and writes text; every number and date comes from code.

understanding: code reads the common shapes of question (rules); the LLM router fills the template only when they
               cannot, and code then checks its output against the question (repair)
facts route:   code filters the facts, folds duplicates, computes the result
search route:  hybrid search -> LLM answers from the top passages with citations (RAG)
"""
import calendar
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import records, verify
from .ai import llm
from .schemas import EXPIRY_FIELDS, FACT_MODELS, FIELDS, HOLDER_FIELDS, ORG_FIELDS, Answer, Conflict, Source
from .search import STOP, search

Op = Literal["list", "sum", "count", "min", "max", "next_expiring", "outstanding", "latest"]
Period = Literal["this_year", "last_year", "this_month", "last_month", "next_30_days", "next_90_days"]


class Filters(BaseModel):
    holder: str | None = None    # person's name, e.g. "Sara"
    provider: str | None = None  # company, shop or institution
    year: int | None = None      # only when the question names a year, e.g. 2026
    month: str | None = None     # one month as written, e.g. "March 2026"
    date_from: str | None = None  # dates as written in the question; code reads them
    date_to: str | None = None
    period: Period | None = None  # relative periods; code turns them into dates


class Route(BaseModel):
    route: Literal["facts", "search"]
    doc_type: Literal[tuple(FIELDS)] | None = None  # type: ignore[valid-type]
    field: str | None = None
    op: Op | None = None
    filters: Filters = Filters()

    @classmethod
    def model_json_schema(cls, *args, **kw) -> dict:
        """The schema llama-server holds the router's reply to: a key may be left out but never set to null. The model
        writes about 9 tokens a second, and a reply that spells out seven null filters costs several seconds."""
        return no_nulls(super().model_json_schema(*args, **kw))


def no_nulls(schema):
    if isinstance(schema, list):
        return [no_nulls(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    schema = {k: v for k, v in schema.items() if not (k == "default" and v is None)}
    if "anyOf" in schema:
        rest = [s for s in schema.pop("anyOf") if s != {"type": "null"}]
        schema = {**schema, **rest[0]} if len(rest) == 1 else {**schema, "anyOf": rest}
    return {k: no_nulls(v) for k, v in schema.items()}


ROUTER_PROMPT = """You turn a question about a person's documents into a query. Do not answer it.
Use route "facts" when the question asks for a value, total, count, earliest/latest, next expiry or unpaid balance of one of these fields:
{fields}
Then set doc_type, field, op and filters.
op: list, sum, count, min, max, next_expiring, outstanding (what is still unpaid on the latest bills, field amount_due),
or latest (the value on the newest document, e.g. someone's current passport or CGPA).
Money paid for a utility is current_charges; amount_due also includes old unpaid bills.
A bank balance is closing_balance with op sum; money paid in is total_credits, money paid out is total_debits.
For things expiring across all kinds of documents, leave doc_type empty and use field expiry_date.
filters:
- holder: a person's name if one is mentioned
- provider: a company, shop or institution if one is mentioned (for example "electricity" -> "electric")
- year: only if a year number is written in the question
- month: one month if the question names it, as written (for example "March 2026")
- date_from, date_to: dates as written, for "between X and Y", "from X to Y", "after X" or "before Y"
- period: this_year, last_year, this_month, last_month, next_30_days or next_90_days for relative times
Use route "search" for anything else, such as questions about what a document says.
Reply with JSON only."""

# the date each document type is "about", used for date filters when the asked field is not itself a date
PERIOD_FIELD = {"utility_bill": "billing_month", "receipt": "date", "certificate": "issue_date", "id_card": "expiry_date",
                "passport": "expiry_date", "transcript": "issue_date", "bank_statement": "period_end", "warranty_card": "purchase_date",
                "offer_letter": "offer_date", "insurance_policy": "start_date", "tenancy_agreement": "start_date", "fee_voucher": "due_date"}
NAME_FIELDS = set(ORG_FIELDS)

ANSWER_PROMPT = """Answer the question using only the numbered passages from the user's documents.
Cite passages like [1]. Keep it to one or two sentences.
If the passages do not contain the answer, reply exactly: NOT FOUND
""" + llm.DATA_RULE


def route(question: str) -> Route:
    def show(f, spec):
        return f"{f} ({spec.meaning})" if spec.kind in ("money", "status") else f
    fields = "\n".join(f"- {t}: {', '.join(show(f, s) for f, s in specs.items())}" for t, specs in FIELDS.items())
    r = llm.chat_json(
        [{"role": "system", "content": ROUTER_PROMPT.format(fields=fields)}, {"role": "user", "content": question}],
        Route,
    )
    if r.field in EXPIRY_FIELDS and r.doc_type and r.field not in FIELDS[r.doc_type]:
        r.doc_type, r.field = None, "expiry_date"  # an expiry asked of a type that has none: the question is about every type
    return repair(question, r) if r.route == "facts" else r


# Utility words -> the part of the provider's name to match. phone, mobile and power only count next to "bill" (a phone
# or a power bank can be on a receipt).
UTILITIES = {"gas": "gas", "electricity": "electric", "electric": "electric", "internet": "internet", "broadband": "internet",
             "water": "water", "power": "electric", "phone": "phone", "mobile": "mobile"}
PRONOUNS = {"i", "me", "my", "mine", "we", "us", "our", "ours", "myself"}
RELATIVE = {"this", "last", "next", "past", "coming", "recent", "previous", "upcoming", "within", "soon", "days", "weeks", "months"}


def repair(question: str, r: Route) -> Route:
    """Slips the small router made on dev questions like "How much is my gas bill?": it dropped the utility, called the
    bill a receipt, took "my" for a person's name, and added a year, month, period or name the question never named. Code
    puts back what the question says and drops filters it does not say."""
    q = question.lower()
    words = set(re.findall(r"[a-z]+", q))
    f = r.filters
    if f.holder and f.holder.strip().lower() in PRONOUNS:
        f.holder = None
    for k in ("holder", "provider"):  # a name none of whose words is in the question was made up
        v = getattr(f, k)
        if v and not any(t in q for t in re.findall(r"[a-z0-9]{3,}", v.lower())):
            setattr(f, k, None)
    if f.year and str(f.year) not in q:
        f.year = None
    if f.period and not RELATIVE & words:
        f.period = None
    for k in ("month", "date_from", "date_to"):  # keep it if a month word or a year from it is in the question
        v = getattr(f, k)
        if v and not any(t in q for t in re.findall(r"[a-z]{3,}|\d{4}", v.lower())):
            setattr(f, k, None)
    utility = next((UTILITIES[w] for w in UTILITIES if w in words), None)
    plain = utility in ("gas", "electric", "internet") and (r.doc_type in ("receipt", "utility_bill") or (r.doc_type is None and r.field not in EXPIRY_FIELDS))
    if utility and ({"bill", "bills"} & words or plain):
        r.doc_type = "utility_bill"
        f.provider = f.provider or utility
        if r.field not in FIELDS["utility_bill"]:
            r.field = "amount_due"
    # "How much is my gas bill?" means the latest bill. A total needs total-like words or a time ("in 2026", "last month").
    timed = f.year or f.month or f.date_from or f.date_to or f.period
    if r.doc_type == "utility_bill" and r.field in ("amount_due", "current_charges") and r.op in ("sum", "list", None) and not timed \
            and not TOTAL_WORDS & words:
        r.op, r.field = "latest", "amount_due"
    return r


TOTAL_WORDS = {"total", "totals", "sum", "spent", "spend", "spending", "altogether", "combined", "overall", "all", "every", "each"}


def valid(r: Route) -> bool:
    if r.route != "facts" or r.op is None:
        return False
    if r.doc_type is None:  # "what expires between X and Y", "what is due next" across document types
        return r.field in EXPIRY_FIELDS + ("due_date",) and r.op in ("list", "count", "min", "max", "next_expiring")
    if r.op == "outstanding":
        return r.doc_type == "utility_bill"
    if r.op == "sum" and r.field in FIELDS[r.doc_type] and FIELDS[r.doc_type][r.field].kind != "money":
        return False  # "sum insured" once came back as a sum of names: only money adds up
    return r.field in FACT_MODELS[r.doc_type].model_fields


# ---------- rules: the common shapes of question, read by code ----------
TYPE_WORDS = [  # the first that matches names the document type
    ("bank_statement", r"\bbank\b|\bstatements?\b|closing balance|paid into|went out of"),
    ("fee_voucher", r"\bfees?\b|vouchers?|tuition|challan"),
    ("insurance_policy", r"insurance|\bpolic(?:y|ies)\b|premiums?"),
    ("tenancy_agreement", r"tenancy|\bleases?\b|\brent\b|landlord|tenant"),
    ("warranty_card", r"warrant(?:y|ies)"),
    ("passport", r"passports?"),
    ("transcript", r"transcripts?|\bc?gpa\b"),
    ("offer_letter", r"\boffers?\b|\boffered\b|salary|\bjobs?\b"),
    ("certificate", r"certificates?|diplomas?|\bdegrees?\b|\bcourses?\b"),
    ("id_card", r"\bid\b|\bcnic\b|identity|licen[cs]e|\bcards?\b"),
    ("utility_bill", r"\bbills?\b|electricity|\bgas\b|internet|broadband|\bwater\b"),
    ("receipt", r"receipts?|purchases?|\bbought\b|\bshopping\b|spen[dt] at"),
]
EXPIRES = r"expir|run(?:s|ning)? out|\bends?\b|\bending\b|valid (?:until|till|thru)|\blapses?\b"
DUE = r"\bdue\b|pay[- ]by|last date"
FIELD_WORDS = [  # the first whose field the type has; "expiry" is the type's own expiry field
    (r"\bc?gpa\b", "cgpa"), (r"serial", "serial_number"), (r"passport (?:no|number)", "passport_number"),
    (r"policy (?:no|number)", "policy_number"), (r"(?:registration|roll|reg) (?:no|number)", "registration_number"),
    (r"account (?:no|number)", "account_number"), (r"(?:\bid|cnic|card|licen[cs]e) (?:no|number)", "id_number"),
    (r"security deposit|\bdeposit\b", "security_deposit"), (r"paid into|money in\b|\bcredits?\b|deposited|received", "total_credits"),
    (r"went out|paid out|money out|\bdebits?\b|withdr", "total_debits"), (r"balance", "closing_balance"),
    (r"\btax\b|\bgst\b|\bvat\b", "tax"), (r"premiums?", "premium"), (r"\brent\b", "monthly_rent"), (r"salary", "monthly_salary"),
    (r"\bjobs?\b|positions?|\broles?\b", "position"), (r"nationality", "nationality"), (r"\bborn\b|birth", "date_of_birth"),
    (r"\bissued?\b", "issue_date"), (r"\bstart|\bjoin", "start_date"), (r"certificates?|courses?|degrees?|diplomas?", "title"),
    (DUE, "due_date"), (EXPIRES, "expiry"),
]
# the value a question means when it names no field: "How much is my gas bill?", "List my HomeEase receipts"
MAIN_FIELD = {"utility_bill": "amount_due", "receipt": "total_amount", "fee_voucher": "amount", "insurance_policy": "premium",
              "tenancy_agreement": "monthly_rent", "offer_letter": "monthly_salary", "bank_statement": "closing_balance",
              "transcript": "cgpa", "certificate": "title", "warranty_card": "warranty_until", "id_card": "expiry_date", "passport": "expiry_date"}
SPENT = {"utility_bill": "current_charges"}  # what a total adds up, where it is not MAIN_FIELD (CALCULATIONS.md)
COUNT = r"\bhow many\b|\bcount\b|\bnumber of\b"
OWE = r"\bowe\b|outstanding|unpaid|still (?:have to |need to )?pay|need to pay"
NEXT = r"\bnext\b(?!\s+(?:\d|few|couple|day|week|month|year))|\bsoonest\b"
SUM = r"\btotals?\b|\bsum\b|add(?:ed)? up|altogether|all together|\bin all\b|combined|\bspen[dt]\b|spending|paid into|went out|paid out"
PAID = r"how much (?:did|have|do) (?:i|we) (?:pay|paid)"
LATEST = r"\blatest\b|\bnewest\b|most recent|\bcurrent(?:ly)?\b|\bnow\b|\blast (?!\d|few|day|week|month|year)"
LIST = r"^\s*(?:list|show)\b|\blist\b|\bshow\b|\bwhich\b|\bwhat are\b|\bwhat \w+ (?:do|does|did|has|have) \w+ (?:have|got|been)\b"
LOOKUP = r"how much (?:is|was)|what(?:'s| is| was)"
DOCS = r"documents?|files?|things?|items?"
# words in company names that say nothing about which company: "insurance" must not pick one insurer
GENERIC = {"ltd", "limited", "pvt", "private", "company", "inc", "the", "and", "for", "bank", "insurance", "assurance", "takaful",
           "university", "college", "institute", "school", "academy", "supply", "electric", "electricity", "gas", "internet", "water"}
MONTHS = r"january|february|march|april|may(?=\s+\d{4})|june|july|august|september|october|november|december"
NOT_NAMES = STOP | {"today", "there", "here", "let", "everyone", "anyone", "somebody"}


def _plus_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def when(q: str, today: date) -> dict:
    """The time a question names, as Filters fields: relative spans become dates here, written dates stay as written."""
    if m := re.search(r"\b(next|coming|within|past|last|previous)\s+(?:the\s+next\s+)?(\d+)\s+(day|week|month|year)s?\b", q):
        n, unit, back = int(m[2]), m[3], m[1] in ("past", "last", "previous")
        n = -n if back else n
        end = _plus_months(today, n * (12 if unit == "year" else 1)) if unit in ("month", "year") \
            else date.fromordinal(today.toordinal() + n * (7 if unit == "week" else 1))
        lo, hi = (end, today) if back else (today, end)
        return {"date_from": lo.isoformat(), "date_to": hi.isoformat()}
    if m := re.search(r"\b(this|last|next) (year|month)\b", q):
        if m[1] != "next":
            return {"period": f"{m[1]}_{m[2]}"}
        lo = date(today.year + 1, 1, 1) if m[2] == "year" else _plus_months(today.replace(day=1), 1)
        hi = date(lo.year, 12, 31) if m[2] == "year" else date(lo.year, lo.month, calendar.monthrange(lo.year, lo.month)[1])
        return {"date_from": lo.isoformat(), "date_to": hi.isoformat()}
    if m := re.search(r"\b(?:between|from)\s+(.+?)\s+(?:and|to|until|till)\s+(.+?)\s*(?:[?.!,]|$)", q):
        a, b = m[1], m[2]
        if (y := re.search(r"\d{4}", b)) and not re.search(r"\d{4}", a):
            a = f"{a} {y[0]}"  # "from January to March 2025"
        if read_day(a) and read_day(b, end=True):
            return {"date_from": a, "date_to": b}
    day = rf"(?:\d{{1,2}}\s+)?(?:{MONTHS})(?:\s+\d{{4}})?|\d{{4}}"
    if m := re.search(rf"\b(before|until|till|by|after|since)\s+({day})\b", q):
        return {"date_to" if m[1] in ("before", "until", "till", "by") else "date_from": m[2]}
    if m := re.search(rf"\b({MONTHS})(?:\s+(\d{{4}}))?\b", q):
        if m[2]:
            return {"month": f"{m[1]} {m[2]}"}
        n = list(calendar.month_name).index(m[1].title())  # no year: the last such month up to today
        return {"month": f"{m[1]} {today.year if n <= today.month else today.year - 1}"}
    if m := re.search(r"\b(?:19|20)\d{2}\b", q):
        return {"year": int(m[0])}
    return {}


def names(conn: sqlite3.Connection, question: str) -> dict | None:
    """People and companies the question names, matched against the names in the indexed facts. Returns the filter
    words ({"holder": "omar farooq"}), the types those names occur in, or None if two different names tie."""
    q = question.lower()
    words = [w for w in dict.fromkeys(re.findall(r"[a-z0-9]+", q))
             if len(w) >= 3 and w not in NOT_NAMES | GENERIC and not any(re.search(p, w) for _, p in TYPE_WORDS)]
    roles = {f: "holder" for f in HOLDER_FIELDS} | {f: "provider" for f in ORG_FIELDS}
    best: dict[str, tuple[int, set[str], set[str]]] = {}  # role -> (score, filter words, types)
    for row in conn.execute(f"SELECT DISTINCT fa.field, fa.value, f.doc_type FROM facts fa JOIN files f ON f.id=fa.file_id "
                            f"WHERE f.copy_of IS NULL AND fa.field IN ({','.join('?' * len(roles))})", list(roles)):
        tokens = set(re.findall(r"[a-z0-9]+", row["value"].lower()))  # OCR joins words: "CareFirstHealth", "OmarFarooq"
        hit = [w for w in words if w in tokens or (len(w) >= 4 and any(t.startswith(w) or t.endswith(w) for t in tokens))]
        if not hit:
            continue
        role, text = roles[row["field"]], " ".join(hit)
        score, texts, types = best.get(role, (0, set(), set()))
        if len(hit) > score:
            best[role] = (len(hit), {text}, {row["doc_type"]})
        elif len(hit) == score:
            best[role] = (score, texts | {text}, types | {row["doc_type"]})
    if any(len(texts) > 1 for _, texts, _ in best.values()):
        return None
    out = {role: next(iter(texts)) for role, (_, texts, _) in best.items()}
    out["types"] = set().union(*(types for _, _, types in best.values())) if best else set()
    if "holder" not in out:  # "Sara's passport": a name the index does not have still filters (to nothing)
        for m in re.finditer(r"\b([A-Z][a-z]{2,})'s\b", question):
            if m[1].lower() not in NOT_NAMES and not any(re.search(p, m[1].lower()) for _, p in TYPE_WORDS):
                out["holder"] = m[1]
                break
    return out


def field_for(q: str, doc_type: str | None) -> str | None:
    for pattern, field in FIELD_WORDS:
        if not re.search(pattern, q):
            continue
        if field == "expiry":
            if doc_type is None:
                return "warranty_until" if re.search(r"warrant", q) else "expiry_date"
            field = next((f for f in EXPIRY_FIELDS if f in FIELDS[doc_type]), None)
        if field and (field in FIELDS[doc_type] if doc_type else field == "due_date"):
            return field
    return None


def rules(conn: sqlite3.Connection, question: str, today: date) -> Route | None:
    """The question as a facts query, read by code, or None when the rules cannot fill every slot."""
    q = question.lower().replace("’", "'")
    found = names(conn, question)
    if found is None:
        return None
    period = when(q, today)
    doc_type = next((t for t, p in TYPE_WORDS if re.search(p, q)), None)
    if doc_type is None and len(found["types"]) == 1:
        doc_type = next(iter(found["types"]))  # "What did I spend at Daily Needs Mart?": only receipts name it
    utility = next((UTILITIES[w] for w in UTILITIES if re.search(rf"\b{w}\b", q)), None)
    if utility and doc_type == "utility_bill" and "provider" not in found and (utility in ("gas", "electric", "internet", "water")
                                                                                or re.search(r"\bbills?\b", q)):
        found["provider"] = utility
    if doc_type == "warranty_card" and not re.search(r"\bcards?\b", q) and re.search(EXPIRES, q):
        doc_type = None  # "When does the Sunwave warranty run out?": receipts carry warranties too
    hint = field_for(q, doc_type)
    is_bill = doc_type == "utility_bill"
    if re.search(COUNT, q) and (doc_type or re.search(DOCS, q)):
        op, field = "count", hint if hint in EXPIRY_FIELDS + ("due_date",) else next(iter(FIELDS[doc_type])) if doc_type else "expiry_date"
    elif re.search(OWE, q) and is_bill:
        op, field = "outstanding", "amount_due"
    elif re.search(NEXT, q):
        op, field = "next_expiring", "due_date" if re.search(DUE + r"|\bpay(?:ment)?s?\b", q) else hint or "expiry_date"
        if field == "due_date" and doc_type not in ("utility_bill", "fee_voucher"):
            doc_type = None
    elif hint in EXPIRY_FIELDS or (hint == "due_date" and period):
        op, field = "list", hint
    elif hint == "due_date":
        op, field = "latest", hint
    elif re.search(SUM, q) or (re.search(PAID, q) and (period or not is_bill)):
        op, field = "sum", hint or SPENT.get(doc_type) or MAIN_FIELD.get(doc_type)
    elif re.search(LATEST, q):
        op, field = "latest", hint or MAIN_FIELD.get(doc_type)
    elif re.search(LIST, q):
        op, field = "list", hint or MAIN_FIELD.get(doc_type)
    elif hint or (re.search(LOOKUP, q) and doc_type) or (re.search(PAID, q) and is_bill):
        op, field = "latest", hint or MAIN_FIELD.get(doc_type)
    else:
        return None
    r = Route(route="facts", doc_type=doc_type, field=field, op=op,
              filters=Filters(holder=found.get("holder"), provider=found.get("provider"), **period))
    return r if field and valid(r) else None


def understand(conn: sqlite3.Connection, question: str, today: date) -> tuple[Route, str]:
    """Rules first; the model router only when they cannot fill the query. Names and times the model left out but
    code found in the question are put back."""
    if r := rules(conn, question, today):
        return r, "rules"
    r = route(question)
    if r.route == "facts" and (found := names(conn, question)):
        f = r.filters
        f.holder, f.provider = f.holder or found.get("holder"), f.provider or found.get("provider")
        if not (f.year or f.month or f.date_from or f.date_to or f.period):
            for k, v in when(question.lower(), today).items():
                setattr(f, k, v)
    return r, "model"


def ask(conn: sqlite3.Connection, question: str, today: date | None = None) -> Answer:
    today = today or date.today()
    r, how = understand(conn, question, today)
    a = answer_from_facts(conn, question, r, today) if valid(r) else answer_from_search(conn, question)
    a.parser = how
    return a


# ---------- facts route: code does every filter, sum, count and date comparison ----------
def read_day(text: str, end: bool = False) -> str | None:
    """A date as written ("5 March 2026", "March 2026", "2026") in ISO form; a month or year means its first or last day."""
    if d := verify.parse_date(re.sub(r"\s+", " ", text.replace(",", " ")).strip()):
        return d.isoformat()
    if re.fullmatch(r"\s*\d{4}\s*", text):
        return f"{text.strip()}-12-31" if end else f"{text.strip()}-01-01"
    if m := verify.parse("month", text):
        return f"{m}-{calendar.monthrange(int(m[:4]), int(m[5:]))[1] if end else 1:02d}"
    return None


def date_range(f: Filters, today: date) -> tuple[str, str] | None:
    if f.date_from or f.date_to:
        lo = read_day(f.date_from) if f.date_from else None
        hi = read_day(f.date_to, end=True) if f.date_to else None
        if lo or hi:
            return lo or "0001-01-01", hi or "9999-12-31"
    if f.month and (lo := read_day(f.month)):
        return lo, read_day(f.month, end=True)
    if f.year:
        return f"{f.year}-01-01", f"{f.year}-12-31"
    y, m = today.year, today.month
    match f.period:
        case "this_year":
            return f"{y}-01-01", f"{y}-12-31"
        case "last_year":
            return f"{y - 1}-01-01", f"{y - 1}-12-31"
        case "this_month":
            return f"{y}-{m:02d}-01", f"{y}-{m:02d}-31"
        case "last_month":
            ly, lm = (y, m - 1) if m > 1 else (y - 1, 12)
            return f"{ly}-{lm:02d}-01", f"{ly}-{lm:02d}-31"
        case "next_30_days" | "next_90_days":
            days = 30 if f.period == "next_30_days" else 90
            return today.isoformat(), date.fromordinal(today.toordinal() + days).isoformat()
    return None


def asked_field(r: Route, doc_type: str) -> str | None:
    """The field to read on a document of this type. Across types, expiry_date means each type's own expiry field;
    warranty_until and due_date mean the types that have that field."""
    if r.doc_type:
        return r.field
    if r.field == "expiry_date":
        return next((f for f in EXPIRY_FIELDS if f in FIELDS[doc_type]), None)
    return r.field if r.field in FIELDS[doc_type] else None


def named(want: str, have: str | None) -> bool:
    """Does a name from the question match a stored name? "electricity" matches "Northern Electric Supply Co." by
    the first 6 letters of a long word."""
    want, have = want.lower(), (have or "").lower()
    return want in have or _squash(want) in _squash(have) or any(len(w) >= 6 and w[:6] in have for w in want.split())


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())  # OCR drops spaces: "OmarFarooq", "ORCHARDBANKLTD"


def keep(d: records.Doc, r: Route, today: date) -> bool:
    f = r.filters
    people = [h for h in HOLDER_FIELDS if h in FIELDS[d.doc_type]] or NAME_FIELDS  # bills and receipts have no person
    if f.holder and not any(named(f.holder, d.get(h)) for h in people):
        return False
    if f.provider and not any(named(f.provider, d.get(n)) for n in NAME_FIELDS):
        return False
    if rng := date_range(f, today):
        field = asked_field(r, d.doc_type)
        on = field if field and FIELDS[d.doc_type][field].kind == "date" else PERIOD_FIELD[d.doc_type]
        v = d.get(on) or ""
        v = v + "-01" if len(v) == 7 else v  # billing_month YYYY-MM
        if not v or not (rng[0] <= v <= rng[1]):
            return False
    return True


def answer_from_facts(conn, question: str, r: Route, today: date) -> Answer:
    docs = records.load(conn, r.doc_type)
    res = records.resolve([d for d in docs if keep(d, r, today)])
    res.conflicts = records.resolve(docs).conflicts  # before filtering, as /upcoming does: a misread name ("HAMZAKHAN") must not hide one
    query, note = r.model_dump(), ""
    if r.op == "outstanding":
        used, text = _outstanding(res.kept)
    elif r.op == "latest" and not (r.doc_type == "bank_statement" and r.field == "closing_balance"):
        used, text = _latest(res.kept, r.field)
    elif r.doc_type == "bank_statement" and r.field == "closing_balance" and r.op in ("sum", "latest"):
        used, text = _balances(res.kept)
    else:
        field = r.field
        accounts = {(d.get("provider"), d.get("account_number")) for d in res.kept}
        if r.doc_type == "utility_bill" and r.op == "sum" and field == "amount_due" and len(res.kept) > len(accounts):
            field = "current_charges"  # each amount_due repeats the last bill's unpaid amount: summing them counts it twice
            note = " Counted current charges, not amount due, so unpaid amounts carried from one bill to the next are not counted twice."
        items = [(d, d.facts[f]) for d in res.kept if (f := field if r.doc_type else asked_field(r, d.doc_type)) and f in d.facts]
        used, text = items, ""
        if r.op == "count":
            text = f"{len(items)} documents."
        elif items and r.op == "sum":
            used, text = _sum(items, field)
        elif items and r.op in ("min", "max"):
            pick = (min if r.op == "min" else max)(items, key=lambda x: _sortable(x[1]))
            used, text = [pick], f"{_show(pick[1])} ({_name(pick[0])})."
        elif items and r.op == "next_expiring":
            future = sorted((x for x in items if x[1]["value"] >= today.isoformat() and not (x[1]["field"] == "due_date" and x[0].get("payment_status") == "paid")),
                            key=lambda x: x[1]["value"])
            if not future:
                return Answer(question=question, answer="Nothing upcoming; all matching dates have passed.", route="facts", query=query)
            d, fr = future[0]
            used, text = [future[0]], f"{_name(d)}: {fr['field'].replace('_', ' ')} {fr['value']}, in {(date.fromisoformat(fr['value']) - today).days} days."
        elif items:  # list
            used = sorted(items, key=lambda x: _sortable(x[1]))
            text = "; ".join(f"{_show(fr)} ({_name(d)})" for d, fr in used) + "."
    if not text:
        return Answer(question=question, answer="No matching documents found.", route="facts", query=query)
    return _finish(question, text + note, query, used, res)


def _outstanding(kept: list[records.Doc]):
    """Unpaid balance: per account, the latest bill. Its amount_due already includes older unpaid bills, so nothing
    else is added. A latest bill marked paid adds nothing."""
    latest: dict[tuple, records.Doc] = {}
    for d in kept:
        if not (d.get("billing_month") and d.get("amount_due")):
            continue
        k = tuple(records.norm(d.doc_type, f, d.get(f) or "") for f in ("provider", "account_number"))
        if k not in latest or d.get("billing_month") > latest[k].get("billing_month"):
            latest[k] = d
    unpaid = [(d, d.facts["amount_due"]) for d in latest.values() if d.get("payment_status") != "paid"]
    if not latest:
        return [], ""
    if not unpaid:
        return [(d, d.facts["amount_due"]) for d in latest.values()], "0 outstanding: the latest bills are marked paid."
    parts = "; ".join(f"{d.get('provider')} {d.get('billing_month')}: {_show(fr)}" for d, fr in unpaid)
    return unpaid, f"{_fmt(sum(float(fr['value']) for _, fr in unpaid))} outstanding, from the latest unpaid bills ({parts})."


def _latest(kept: list[records.Doc], field: str):
    """The field on the newest document, by the date each type is about (PERIOD_FIELD). Ties (a conflict) show both."""
    dated = [(d, d.get(PERIOD_FIELD[d.doc_type])) for d in kept if field in d.facts and d.get(PERIOD_FIELD[d.doc_type])]
    if not dated:
        return [], ""
    top = max(v for _, v in dated)
    used = [(d, d.facts[field]) for d, v in dated if v == top]
    return used, "; ".join(f"{_show(fr)} ({_name(d)})" for d, fr in used) + f", from the newest document ({top})."


def _balances(kept: list[records.Doc]):
    """Bank balance: per account, the closing balance of the newest statement. Each statement's balance already holds
    the money of the earlier ones, so adding the balances of several statements of one account counts it again."""
    latest: dict[tuple, records.Doc] = {}
    for d in kept:
        if not (d.get("period_end") and d.get("closing_balance")):
            continue
        k = tuple(records.norm(d.doc_type, f, d.get(f) or "") for f in ("bank", "account_number"))
        if k not in latest or d.get("period_end") > latest[k].get("period_end"):
            latest[k] = d
    if not latest:
        return [], ""
    used = [(d, d.facts["closing_balance"]) for d in latest.values()]
    parts = "; ".join(f"{d.get('bank')} on {d.get('period_end')}: {_show(fr)}" for d, fr in used)
    return used, f"{_fmt(sum(float(fr['value']) for _, fr in used))} in total, from the newest statement of each account ({parts})."


def _sum(items, field: str):
    if field != "current_charges":
        return items, f"{_fmt(sum(float(fr['value']) for _, fr in items))} in total, from {len(items)} documents."
    used, total = [], 0.0
    for d, fr in items:  # spend per bill; tax is added only when the bill lists it outside the current charges
        v = {f: verify.typed(FIELDS["utility_bill"][f].kind, d.get(f))
             for f in ("current_charges", "previous_balance", "tax", "amount_due") if d.get(f) is not None}
        spent = verify.spend(v)
        total += spent
        used.append((d, fr))
        if spent != v["current_charges"]:
            used.append((d, d.facts["tax"]))
    return used, f"{_fmt(total)} in total, from {len(items)} documents."


def _finish(question, text, query, used, res: records.Resolved) -> Answer:
    used_ids = {d.id for d, _ in used}
    skipped = [dup for dup, kept in res.skipped if kept.id in used_ids]
    unreviewed = [records.fact(fr) for _, fr in used if fr["status"] == "needs_review"]
    fields = {fr["field"] for _, fr in used}
    conflicts = [c for c in res.conflicts if c.field in fields and any(v.source.file_id in used_ids for v in c.values)]
    if skipped:
        text += f" Left out {len(skipped)} duplicate {'copy' if len(skipped) == 1 else 'copies'} of documents already counted."
    if unreviewed:
        text += f" {len(unreviewed)} of the values used {'needs' if len(unreviewed) == 1 else 'need'} review."
    for c in conflicts:
        text += " Conflict: " + _conflict_text(c)
    pages: dict[int, tuple[str, int]] = {}
    for d, fr in used:
        pages.setdefault(d.id, (d.path, fr["page"]))
    sources = [records.source(fid, p, page) for fid, (p, page) in pages.items()]
    return Answer(question=question, answer=text, route="facts", query=query, facts=[records.fact(fr) for _, fr in used],
                  sources=sources, counted=sources, skipped_duplicates=[records.source(d.id, d.path) for d in skipped],
                  unreviewed=unreviewed, conflicts=conflicts)


def _conflict_text(c: Conflict) -> str:
    vals = " but ".join(f"{v.value} in {v.source.name}" for v in c.values)
    return f"the {c.field.replace('_', ' ')} for {c.key} is {vals}. Check which is right."


def _sortable(row):
    return float(row["value"]) if row["value_type"] == "money" else row["value"]


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def _show(row) -> str:
    return _fmt(float(row["value"])) if row["value_type"] == "money" else row["value"]


def _name(d: records.Doc) -> str:
    return Path(d.path).name


# ---------- search route (RAG) ----------
# The model reads about 45 tokens a second on the laptop, so the prompt is kept small: the top 3 passages, each cut to
# the sentences around the words of the question (about 100 tokens each).
TOP, PASSAGE_WORDS, PIECE_WORDS = 3, 60, 20
NOT_FOUND = "I couldn't find this in your documents."


def passage(text: str, q: str, limit: int = PASSAGE_WORDS) -> str:
    """The sentences around the best match for the question's words, at most `limit` words. Text with no sentence ends
    (bills, receipts) is cut into pieces of 20 words instead. With no word in common, the start of the chunk."""
    if len(text.split()) <= limit:
        return text
    units = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        w = sentence.split()
        units += [" ".join(w[i : i + PIECE_WORDS]) for i in range(0, len(w), PIECE_WORDS)]
    terms = {t[:5] for t in re.findall(r"\w+", q.lower()) if t not in STOP and len(t) > 2}  # 5 letters: "insurance" finds "insured"
    found = [terms & {w[:5] for w in re.findall(r"\w+", u.lower())} for u in units]
    # a word in every sentence (the company's name in its own letter) says little about where the answer is
    spread = {t: sum(t in f for f in found) for t in terms}
    hits = [sum(1 / spread[t] for t in f) for f in found]
    lo = hi = hits.index(max(hits))
    size = len(units[lo].split())
    grew = True
    while grew:  # add the next sentence, then the one before, while they fit
        grew = False
        for j in (hi + 1, lo - 1):
            if 0 <= j < len(units) and size + len(units[j].split()) <= limit:
                size += len(units[j].split())
                lo, hi, grew = min(lo, j), max(hi, j), True
    return " ".join(units[lo : hi + 1])


def _search_prompt(conn, question: str, k: int):
    hits = search(conn, question, k=k)
    texts = [passage(conn.execute("SELECT text FROM chunks WHERE id=?", (h.chunk_id,)).fetchone()["text"], question) for h in hits]
    passages = "\n\n".join(f"[{i}] ({h.name}, page {h.page}) {t}" for i, (h, t) in enumerate(zip(hits, texts), 1))
    return hits, [
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"{llm.fenced('PASSAGES', passages)}\n\nQuestion: {question}"},
    ]


def _search_answer(question: str, hits, text: str) -> Answer:
    if "NOT FOUND" in text:
        return Answer(question=question, answer=NOT_FOUND, route="none")
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(hits)} or {1}
    used = [hits[i - 1] for i in sorted(cited)]
    return Answer(question=question, answer=text, route="search",
                  sources=[Source(file_id=h.file_id, path=h.path, name=h.name, page=h.page) for h in used])


def answer_from_search(conn, question: str, k: int = TOP) -> Answer:
    hits, messages = _search_prompt(conn, question, k)
    if not hits:
        return Answer(question=question, answer=NOT_FOUND, route="none")
    return _search_answer(question, hits, llm.chat(messages))


def ask_stream(conn: sqlite3.Connection, question: str, today: date | None = None):
    """ask() for the launcher: yields ("token", text) while a search answer is being written, then ("answer", Answer).
    A facts answer is computed by code, so it comes in one piece."""
    today = today or date.today()
    r, how = understand(conn, question, today)
    if valid(r):
        yield "answer", answer_from_facts(conn, question, r, today).model_copy(update={"parser": how})
        return
    hits, messages = _search_prompt(conn, question, TOP)
    if not hits:
        yield "answer", Answer(question=question, answer=NOT_FOUND, route="none", parser=how)
        return
    text, sent = "", 0
    for delta in llm.chat_stream(messages):
        text += delta
        if "NOT FOUND".startswith(text.strip()):  # the reply may still turn out to be NOT FOUND: hold it back
            continue
        yield "token", text[sent:]
        sent = len(text)
    yield "answer", _search_answer(question, hits, llm.clean(text)).model_copy(update={"parser": how})
