"""Answer a question. The LLM only routes and writes text; every number and date comes from code.

facts route:  router fills a fixed query template -> code filters the facts table and computes the result
search route: hybrid search -> LLM answers from the top passages with citations (RAG)
"""
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .ai import llm
from .schemas import FACT_MODELS, HOLDER_FIELDS, Answer, Fact, Source
from .search import search

Op = Literal["list", "sum", "count", "min", "max", "next_expiring"]
Period = Literal["this_year", "last_year", "this_month", "last_month", "next_30_days", "next_90_days"]


class Filters(BaseModel):
    holder: str | None = None    # person's name, e.g. "Sara"
    provider: str | None = None  # company, shop or institution
    year: int | None = None      # only when the question names a year, e.g. 2026
    period: Period | None = None  # relative periods; code turns them into dates


class Route(BaseModel):
    route: Literal["facts", "search"]
    doc_type: Literal["id_card", "utility_bill", "certificate", "receipt"] | None = None
    field: str | None = None
    op: Op | None = None
    filters: Filters = Filters()


ROUTER_PROMPT = """You turn a question about a person's documents into a query. Do not answer it.
Use route "facts" when the question asks for a value, total, count, earliest/latest or next expiry of one of these fields:
{fields}
Then set doc_type, field, op (list, sum, count, min, max, next_expiring) and filters:
- holder: a person's name if one is mentioned
- provider: a company, shop or institution if one is mentioned (for example "electricity" -> "electric")
- year: only if a year number is written in the question
- period: this_year, last_year, this_month, last_month, next_30_days or next_90_days for relative times
Use route "search" for anything else, such as questions about what a document says.
Reply with JSON only."""

# the date each document type is "about", used for year/period filters
PERIOD_FIELD = {"utility_bill": "billing_month", "receipt": "date", "certificate": "issue_date", "id_card": "expiry_date"}
NAME_FIELDS = {"provider", "merchant", "institution"}

ANSWER_PROMPT = """Answer the question using only the numbered passages from the user's documents.
Cite passages like [1]. Keep it to one or two sentences.
If the passages do not contain the answer, reply exactly: NOT FOUND"""


def route(question: str) -> Route:
    fields = "\n".join(f"- {t}: {', '.join(m.model_fields)}" for t, m in FACT_MODELS.items())
    return llm.chat_json(
        [{"role": "system", "content": ROUTER_PROMPT.format(fields=fields)}, {"role": "user", "content": question}],
        Route,
    )


def valid(r: Route) -> bool:
    return r.route == "facts" and r.doc_type is not None and r.op is not None and r.field in FACT_MODELS[r.doc_type].model_fields


def ask(conn: sqlite3.Connection, question: str, today: date | None = None) -> Answer:
    r = route(question)
    if valid(r):
        return answer_from_facts(conn, question, r, today or date.today())
    return answer_from_search(conn, question)


# ---------- facts route ----------
def date_range(f: Filters, today: date) -> tuple[str, str] | None:
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


def matching_files(conn, r: Route, today: date) -> list[int]:
    ids = [row["id"] for row in conn.execute("SELECT id FROM files WHERE doc_type=? AND status='indexed'", (r.doc_type,))]
    keep = []
    for fid in ids:
        facts = {row["field"]: row["value"] for row in conn.execute("SELECT field, value FROM facts WHERE file_id=?", (fid,))}
        f = r.filters
        if f.holder and not any(f.holder.lower() in facts.get(h, "").lower() for h in HOLDER_FIELDS):
            continue
        if f.provider and not any(f.provider.lower() in facts.get(n, "").lower() for n in NAME_FIELDS):
            continue
        if rng := date_range(f, today):
            d = facts.get(PERIOD_FIELD[r.doc_type], "")
            d = d + "-01" if len(d) == 7 else d  # billing_month YYYY-MM
            if not d or not (rng[0] <= d <= rng[1]):
                continue
        keep.append(fid)
    return keep


def answer_from_facts(conn, question: str, r: Route, today: date) -> Answer:
    files = matching_files(conn, r, today)
    rows = [
        row for fid in files
        for row in conn.execute("SELECT fa.*, f.path FROM facts fa JOIN files f ON f.id=fa.file_id WHERE fa.file_id=? AND fa.field=?", (fid, r.field))
    ]
    query = r.model_dump()
    if not rows:
        return Answer(question=question, answer="No matching documents found.", route="facts", query=query)

    def num(row):
        return float(row["value"])

    def name(row):
        return Path(row["path"]).name

    if r.op == "sum":
        used, text = rows, f"{_fmt(sum(num(x) for x in rows))} in total, from {len(rows)} documents."
    elif r.op == "count":
        used, text = rows, f"{len(files)} documents."
    elif r.op in ("min", "max"):
        pick = (min if r.op == "min" else max)(rows, key=lambda x: num(x) if x["value_type"] == "money" else x["value"])
        used, text = [pick], f"{_show(pick)} ({name(pick)})."
    elif r.op == "next_expiring":
        future = sorted((x for x in rows if x["value"] >= today.isoformat()), key=lambda x: x["value"])
        if not future:
            return Answer(question=question, answer="Nothing upcoming; all matching dates have passed.", route="facts", query=query)
        pick = future[0]
        days = (date.fromisoformat(pick["value"]) - today).days
        used, text = [pick], f"{name(pick)}: {r.field.replace('_', ' ')} {pick['value']}, in {days} days."
    else:  # list
        used = sorted(rows, key=lambda x: x["value"])
        text = "; ".join(f"{_show(x)} ({name(x)})" for x in used) + "."
    return Answer(
        question=question, answer=text, route="facts", query=query,
        facts=[_fact(x) for x in used], sources=_sources(conn, {x["file_id"]: x["page"] for x in used}),
    )


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def _show(row) -> str:
    return _fmt(float(row["value"])) if row["value_type"] == "money" else row["value"]


def _fact(row) -> Fact:
    return Fact(id=row["id"], file_id=row["file_id"], field=row["field"], value=row["value"],
                value_type=row["value_type"], page=row["page"], verified=bool(row["verified"]))


def _sources(conn, pages: dict[int, int]) -> list[Source]:
    out = []
    for fid, page in pages.items():
        p = conn.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()["path"]
        out.append(Source(file_id=fid, path=p, name=Path(p).name, page=page))
    return out


# ---------- search route (RAG) ----------
def answer_from_search(conn, question: str, k: int = 5) -> Answer:
    hits = search(conn, question, k=k)
    if not hits:
        return Answer(question=question, answer="I couldn't find this in your documents.", route="none")
    texts = [conn.execute("SELECT text FROM chunks WHERE id=?", (h.chunk_id,)).fetchone()["text"] for h in hits]
    passages = "\n\n".join(f"[{i}] ({h.name}, page {h.page}) {t}" for i, (h, t) in enumerate(zip(hits, texts), 1))
    text = llm.chat([
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"Passages:\n{passages}\n\nQuestion: {question}"},
    ])
    if "NOT FOUND" in text:
        return Answer(question=question, answer="I couldn't find this in your documents.", route="none")
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(hits)} or {1}
    used = [hits[i - 1] for i in sorted(cited)]
    return Answer(question=question, answer=text, route="search",
                  sources=[Source(file_id=h.file_id, path=h.path, name=h.name, page=h.page) for h in used])
