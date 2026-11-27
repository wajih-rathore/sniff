"""Answer a question. The LLM only routes and writes text; every number and date comes from code.

facts route:  router fills a fixed query template -> code filters the facts, folds duplicates, computes the result
search route: hybrid search -> LLM answers from the top passages with citations (RAG)
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
from .search import search

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
        r.doc_type = None  # an expiry asked of a type that has none: the question is about every type
    return repair(question, r) if r.route == "facts" else r


# Utility words -> the part of the provider's name to match. phone, mobile and power only count next to "bill" (a phone
# or a power bank can be on a receipt).
UTILITIES = {"gas": "gas", "electricity": "electric", "electric": "electric", "internet": "internet", "broadband": "internet",
             "water": "water", "power": "electric", "phone": "phone", "mobile": "mobile"}
PRONOUNS = {"i", "me", "my", "mine", "we", "us", "our", "ours", "myself"}
RELATIVE = {"this", "last", "next", "past", "coming", "recent", "previous", "upcoming", "within", "soon", "days", "weeks", "months"}


def repair(question: str, r: Route) -> Route:
    """Slips the small router made on dev questions like "How much is my gas bill?": it dropped the utility, called the
    bill a receipt, took "my" for a person's name, and added a year, month or period the question never named. Code puts back
    what the question says and drops filters it does not say."""
    q = question.lower()
    words = set(re.findall(r"[a-z]+", q))
    f = r.filters
    if f.holder and f.holder.strip().lower() in PRONOUNS:
        f.holder = None
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
    if r.doc_type is None:  # "what expires between X and Y" across document types
        return r.field in EXPIRY_FIELDS and r.op in ("list", "count", "min", "max", "next_expiring")
    if r.op == "outstanding":
        return r.doc_type == "utility_bill"
    if r.op == "sum" and r.field in FIELDS[r.doc_type] and FIELDS[r.doc_type][r.field].kind != "money":
        return False  # "sum insured" once came back as a sum of names: only money adds up
    return r.field in FACT_MODELS[r.doc_type].model_fields


def ask(conn: sqlite3.Connection, question: str, today: date | None = None) -> Answer:
    r = route(question)
    if valid(r):
        return answer_from_facts(conn, question, r, today or date.today())
    return answer_from_search(conn, question)


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
    """The field to read on a document of this type. Across types, each type's own expiry field."""
    if r.doc_type:
        return r.field
    return next((f for f in EXPIRY_FIELDS if f in FIELDS[doc_type]), None)


def named(want: str, have: str | None) -> bool:
    """Does a name from the question match a stored name? "electricity" matches "Northern Electric Supply Co." by
    the first 6 letters of a long word."""
    want, have = want.lower(), (have or "").lower()
    return want in have or any(len(w) >= 6 and w[:6] in have for w in want.split())


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
            future = sorted((x for x in items if x[1]["value"] >= today.isoformat()), key=lambda x: x[1]["value"])
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
def answer_from_search(conn, question: str, k: int = 5) -> Answer:
    hits = search(conn, question, k=k)
    if not hits:
        return Answer(question=question, answer="I couldn't find this in your documents.", route="none")
    texts = [conn.execute("SELECT text FROM chunks WHERE id=?", (h.chunk_id,)).fetchone()["text"] for h in hits]
    passages = "\n\n".join(f"[{i}] ({h.name}, page {h.page}) {t}" for i, (h, t) in enumerate(zip(hits, texts), 1))
    text = llm.chat([
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"{llm.fenced('PASSAGES', passages)}\n\nQuestion: {question}"},
    ])
    if "NOT FOUND" in text:
        return Answer(question=question, answer="I couldn't find this in your documents.", route="none")
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(hits)} or {1}
    used = [hits[i - 1] for i in sorted(cited)]
    return Answer(question=question, answer=text, route="search",
                  sources=[Source(file_id=h.file_id, path=h.path, name=h.name, page=h.page) for h in used])
