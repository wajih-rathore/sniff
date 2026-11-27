"""Readable names for documents and a one-line reason for each search hit, built by code from the facts. No model."""
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import records
from .schemas import FIELDS, HOLDER_FIELDS, ORG_FIELDS, Hit
from .search import STOP

TYPE_NAMES = {"id_card": "ID card"}
PERIOD = {"utility_bill": "billing_month", "bank_statement": "period_end", "fee_voucher": "semester", "receipt": "date"}
WHEN = ("bill_date", "date", "issue_date", "period_end", "offer_date", "start_date", "purchase_date")  # the row's date
FIELD_NAMES = {"provider": "provider name", "merchant": "shop name", "id_number": "ID number"}
# "Metro Gas Ltd" -> "Metro Gas", "Northern Electric Supply Co." -> "Northern Electric"
SUFFIX = re.compile(r"(?:[\s,]+(?:ltd|limited|co|company|pvt|private|inc|corp|corporation|supply|plc)\.?)+$", re.I)


def type_name(doc_type: str | None) -> str:
    return TYPE_NAMES.get(doc_type or "", (doc_type or "document").replace("_", " "))


def _who(field: str, value: str) -> str:
    v = value.strip()
    if field in ORG_FIELDS:
        v = SUFFIX.sub("", v) or v
    return v.title() if v.isupper() and len(v) > 3 else v  # OCR'd capitals: "ZAINAB MALIK"


def _period(value: str) -> str:
    try:
        return date.fromisoformat(value[:7] + "-01").strftime("%b %Y")  # "2025-11" or "2025-11-05" -> "Nov 2025"
    except ValueError:
        return value  # a semester, such as "Spring 2025"


def title(doc_type: str | None, facts: dict[str, str], path: str) -> str:
    """"Metro Gas, utility bill, Nov 2025": who, what, when. The file name when the facts give no name."""
    fields = FIELDS.get(doc_type or "", {})
    who = next((f for f in fields if f in ORG_FIELDS + HOLDER_FIELDS and facts.get(f)), None)
    if not who:
        return Path(path).name
    parts = [_who(who, facts[who]), type_name(doc_type)]
    if (p := PERIOD.get(doc_type)) and facts.get(p):
        parts.append(_period(facts[p]))
    return ", ".join(parts)


def _facts(conn: sqlite3.Connection, ids) -> dict[int, list]:
    ids = list(ids)
    out: dict[int, list] = {i: [] for i in ids}
    if ids:
        for r in conn.execute(f"SELECT * FROM facts WHERE file_id IN ({','.join('?' * len(ids))}) AND status<>'superseded' ORDER BY id", ids):
            out[r["file_id"]].append(r)
    return out


def for_files(conn: sqlite3.Connection, ids) -> dict[int, str]:
    """Titles by file id."""
    facts = _facts(conn, ids)
    rows = conn.execute(f"SELECT id, path, doc_type FROM files WHERE id IN ({','.join('?' * len(facts))})", list(facts)).fetchall() if facts else []
    return {r["id"]: title(r["doc_type"], {f["field"]: f["value"] for f in reversed(facts[r["id"]])}, r["path"]) for r in rows}


def name_sources(conn: sqlite3.Connection, answer):
    """Give every source in an Answer its title, so the launcher never shows a bare file name it could avoid."""
    srcs = [*answer.sources, *answer.counted, *answer.skipped_duplicates, *(v.source for c in answer.conflicts for v in c.values),
            *(x.source for x in answer.alternatives)]
    names = for_files(conn, {s.file_id for s in srcs})
    for s in srcs:
        s.title = names.get(s.file_id, s.name)
    return answer


def _has(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}", text.lower()) is not None


def describe(conn: sqlite3.Connection, q: str, hits: list[Hit]) -> list[Hit]:
    """Title, date and why each hit matched: a fact holding the words, the file name, the page text, or only the
    meaning (vector search). Hits matched only by meaning are the weaker ones the launcher folds away."""
    words = [w for w in re.findall(r"\w+", q.lower()) if w not in STOP and len(w) > 1]
    facts = _facts(conn, {h.file_id for h in hits})
    for h in hits:
        rows = facts[h.file_id]
        h.title = title(h.doc_type, {f["field"]: f["value"] for f in reversed(rows)}, h.path)
        h.date = next((f["value"] for w in WHEN for f in rows if f["field"] == w), None)
        best = max(rows, key=lambda f: sum(_has(f["value"], w) for w in words), default=None)
        text = conn.execute("SELECT text FROM chunks WHERE id=?", (h.chunk_id,)).fetchone()
        if best and words and any(_has(best["value"], w) for w in words):
            h.match, h.fact = "fact", records.fact(best)
            h.match_reason = f"Matches the {FIELD_NAMES.get(best['field'], best['field'].replace('_', ' '))} on page {best['page']}"
        elif any(_has(h.name, w) for w in words):
            h.match, h.match_reason = "name", "Matches the file name"
        elif not words or (text and any(_has(text["text"], w) for w in words)):
            h.match, h.match_reason = "text", f"Matches the text on page {h.page}"
        else:
            h.match, h.match_reason = "meaning", "Matched by meaning, not by the words you typed"
    return hits
