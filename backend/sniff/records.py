"""Near-duplicates and conflicts across documents. Plain code, no model.

Exact copies (the same bytes in two folders) are already one document: files.copy_of. Here we deal with different
files that hold the same record, like one bill saved as a PDF and as a phone photo. Documents with the same key
(schemas.KEYS) are the same record, and so are two bills whose key differs only by an OCR misread (same_record). If they also agree on every stable field they are near-duplicates: calculations
count the most trusted one and list the rest as skipped. If a stable field differs, they are a conflict: neither copy
is picked (Resolved.disputed); answers give the result for each choice, until the user picks one (POST /conflicts/pick),
which marks the other copy's value superseded. A superseded value no longer counts as a disagreement.
"""
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .schemas import FIELDS, KEYS, Conflict, ConflictValue, Fact, Source


@dataclass
class Doc:
    id: int
    path: str
    doc_type: str
    facts: dict  # field -> row of the facts table

    def get(self, field: str) -> str | None:
        r = self.facts.get(field)
        return r["value"] if r else None

    def live(self, field: str) -> str | None:
        """The value, unless the user chose another copy's value instead."""
        r = self.facts.get(field)
        return r["value"] if r and r["status"] != "superseded" else None

    @property
    def trust(self) -> tuple:
        """Sort key, best first: fewest values the user overruled, fewest facts needing review, then most facts a
        person corrected or approved."""
        st = [r["status"] for r in self.facts.values()]
        return st.count("superseded"), st.count("needs_review"), -(st.count("approved") + st.count("corrected")), self.id


@dataclass
class Resolved:
    kept: list[Doc]  # one document per record, except that every side of a conflict is here (for lists such as Upcoming)
    skipped: list[tuple[Doc, Doc]]  # (near-duplicate left out, the document kept instead)
    conflicts: list[Conflict]
    disputed: list[list[Doc]]  # the sides of each conflict, most trusted first: calculations count one side at a time


def load(conn: sqlite3.Connection, doc_type: str | None = None) -> list[Doc]:
    """Indexed documents with a fact type, copies folded, with their facts."""
    sql = "SELECT id, path, doc_type FROM files WHERE status='indexed' AND copy_of IS NULL AND doc_type IN (%s)"
    types = [doc_type] if doc_type else list(FIELDS)
    docs = {r["id"]: Doc(r["id"], r["path"], r["doc_type"], {}) for r in conn.execute(sql % ",".join("?" * len(types)), types)}
    for r in conn.execute("SELECT * FROM facts ORDER BY id"):
        if r["file_id"] in docs:
            docs[r["file_id"]].facts.setdefault(r["field"], r)
    return list(docs.values())


def norm(doc_type: str, field: str, value: str) -> str:
    if FIELDS[doc_type][field].kind == "money":
        return f"{float(value):.2f}"
    return re.sub(r"[^a-z0-9]", "", value.lower())  # case, spaces and punctuation differ between a scan and a PDF


def key(d: Doc) -> tuple | None:
    vals = [d.get(f) for f in KEYS[d.doc_type]]
    return None if None in vals else (d.doc_type, *(norm(d.doc_type, f, v) for f, v in zip(KEYS[d.doc_type], vals)))


def stable_fields(doc_type: str) -> list[str]:
    return [f for f, s in FIELDS[doc_type].items() if s.stable and f not in KEYS[doc_type]]


def agree(a: Doc, b: Doc) -> bool:
    return all(norm(a.doc_type, f, a.live(f)) == norm(a.doc_type, f, b.live(f))
               for f in stable_fields(a.doc_type) if a.live(f) is not None and b.live(f) is not None)


# A bill photo whose billing month OCR misread has another key, but it is the same bill when the provider, account and
# amount agree exactly and the bill's own dates are within a few days of each other, or unreadable on one copy.
SAME_BILL, BILL_DATES, NEAR_DAYS = ("provider", "account_number", "amount_due"), ("bill_date", "due_date"), 3


def same_record(a: Doc, b: Doc) -> bool:
    if a.doc_type != b.doc_type:
        return False
    if (k := key(a)) is not None and k == key(b):
        return True
    if a.doc_type != "utility_bill" or any(a.get(f) is None or b.get(f) is None or norm(a.doc_type, f, a.get(f)) != norm(a.doc_type, f, b.get(f))
                                           for f in SAME_BILL):
        return False
    # shortcut: two months of a flat-rate bill are merged if one copy has no readable date at all; compare the billing
    # months' distance too if that ever shows up
    return all(abs(date.fromisoformat(a.get(f)).toordinal() - date.fromisoformat(b.get(f)).toordinal()) <= NEAR_DAYS
               for f in BILL_DATES if a.get(f) and b.get(f))


def resolve(docs: list[Doc]) -> Resolved:
    groups: list[list[Doc]] = []
    for d in docs:
        if g := next((g for g in groups if same_record(g[0], d)), None):
            g.append(d)
        else:
            groups.append([d])
    kept, skipped, conflicts, disputed = [], [], [], []
    for group in groups:
        group.sort(key=lambda d: d.trust)
        reps: list[Doc] = []
        for d in group:
            rep = next((r for r in reps if agree(r, d)), None)
            if rep:
                skipped.append((d, rep))
                for f, row in d.facts.items():  # the user chose this copy's value of f: the record keeps it
                    if f in rep.facts and rep.facts[f]["status"] == "superseded" and row["status"] != "superseded":
                        rep.facts[f] = row
            else:
                reps.append(d)
        kept += reps
        if len(reps) > 1:
            conflicts += _conflicts(reps)
            disputed.append(reps)
    return Resolved(sorted(kept, key=lambda d: d.id), skipped, conflicts, disputed)


def _conflicts(reps: list[Doc]) -> list[Conflict]:
    t = reps[0].doc_type
    label = ", ".join(f"{f} {reps[0].get(f)}" for f in KEYS[t])
    out = []
    for f in stable_fields(t):
        has = [d for d in reps if d.live(f) is not None]
        if len({norm(t, f, d.live(f)) for d in has}) > 1:
            out.append(Conflict(doc_type=t, key=label, field=f, values=[
                ConflictValue(value=d.get(f), fact_id=d.facts[f]["id"], status=d.facts[f]["status"], source=source(d.id, d.path, d.facts[f]["page"]))
                for d in has]))
    return out


def source(file_id: int, path: str, page: int = 1) -> Source:
    return Source(file_id=file_id, path=path, name=Path(path).name, page=page)


def fact(r) -> Fact:
    return Fact(id=r["id"], file_id=r["file_id"], field=r["field"], value=r["value"], value_type=r["value_type"], page=r["page"],
                verified=bool(r["verified"]), status=r["status"], reason=r["reason"], source_text=r["source_text"])
