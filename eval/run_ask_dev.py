"""Ask on the dev documents only, to check the answer fixes before and after. Not the held-out test: it never reads
data/eval/ask.jsonl or the acceptance questions.

    uv run python ../eval/run_ask_dev.py setup --db <an index of the dev documents> --docs <the folder it indexed>
    uv run python ../eval/run_ask_dev.py run --tag before      # or after

setup copies an existing index (we used the acceptance test's, which holds all 72 dev documents), deletes every
document that is not in the dev split, and plants three records straight into the facts table (no file, no model):
a phone photo of the November 2025 gas bill whose month OCR misread, a second copy of a motor policy with another end
date, and a re-issued fee voucher with another amount. The questions are in data/eval/ask_dev.jsonl.
run asks each question with the real chat model and writes eval/results/ask_dev.json (one section per tag).
"""
import argparse
import json
import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from common import RESULTS, ROOT, jsonl, norm

from sniff import ask, db, verify
from sniff.ai import llm

DEV_DB = ROOT / ".sniff" / "dev-fixes.db"
TODAY = date(2025, 12, 1)  # most dev documents are from 2025, so "this year", "next 3 months" and "next bill" mean something
OUT = RESULTS / "ask_dev.json"

# (dev file to copy, new name, field changes)
PLANTS = [
    ("Documents/old/ece95bbc.pdf", "WhatsApp Images/IMG-20251206-WA0101.jpg", {"billing_month": "2025-08"}),
    ("Downloads/Document (29).pdf", "Downloads/Motor policy renewal copy.pdf", {"end_date": "2026-06-09"}),
    ("Downloads/Print_2025-01-06.pdf", "Downloads/Fee voucher reissued.pdf", {"amount": "140000.0", "due_date": "2025-02-10"}),
]


def setup(src: Path, docs: Path, labels: Path):
    DEV_DB.parent.mkdir(exist_ok=True)
    shutil.copy(src, DEV_DB)
    conn = db.connect(DEV_DB)
    dev = {r["file"].removeprefix("dev/") for r in map(json.loads, labels.read_text(encoding="utf-8").splitlines()) if r["split"] == "dev"}
    rel = lambda p: Path(p).relative_to(docs).as_posix()
    gone = [r["id"] for r in conn.execute("SELECT id, path FROM files") if rel(r["path"]) not in dev]
    for fid in gone:
        db.delete_file_data(conn, fid)
        conn.execute("DELETE FROM files WHERE id=?", (fid,))
    for src_rel, name, changes in PLANTS:
        old = conn.execute("SELECT id, doc_type FROM files WHERE path=?", (str(docs / src_rel),)).fetchone()
        new = conn.execute("INSERT INTO files(path, doc_type, status, indexed_at) VALUES (?,?, 'indexed', datetime('now'))",
                           (str(docs / name), old["doc_type"])).lastrowid
        for f in conn.execute("SELECT * FROM facts WHERE file_id=?", (old["id"],)).fetchall():
            conn.execute("INSERT INTO facts(file_id, field, value, value_type, page, verified, source_text, status, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                         (new, f["field"], changes.get(f["field"], f["value"]), f["value_type"], f["page"], f["verified"], f["source_text"],
                          f["status"], f["reason"]))
    conn.commit()
    print(f"{DEV_DB}: {conn.execute('SELECT count(*) FROM files').fetchone()[0]} files, removed {len(gone)}, planted {len(PLANTS)}")


def dates_in(text: str) -> set[str]:
    found = set(re.findall(r"\d{4}-\d{2}-\d{2}", text))
    for m in re.findall(r"\d{1,2}(?:st|nd|rd|th)? [A-Za-z]{3,9},? \d{4}|[A-Za-z]{3,9} \d{1,2},? \d{4}", text):
        if d := verify.parse_date(m.replace(",", " ")):
            found.add(d.isoformat())
    return found


def has(text: str, item: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
        return item in dates_in(text)
    if re.fullmatch(r"[\d.]+", item):
        nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]
        return any(abs(n - float(item)) <= max(0.01, 0.005 * float(item)) for n in nums)
    return norm(item) in norm(text)


def abstained(a) -> bool:
    return a.route == "none" or (a.route == "facts" and not a.facts and not a.sources)


def correct(a, g: dict) -> bool:
    if g["answer_type"] == "none":
        return abstained(a)
    if abstained(a):
        return False
    items = [x.strip() for x in str(g["answer"]).split(";")]
    ok = all(has(a.answer, x) for x in items)
    return ok and bool(a.conflicts) if g["answer_type"] == "alternatives" else ok


def run(tag: str):
    conn = db.connect(DEV_DB)
    calls = []
    chat = llm.chat

    def counted(*a, **kw):  # every model call goes through llm.chat (chat_json too); the stream is counted below
        calls.append(1)
        return chat(*a, **kw)
    llm.chat = counted
    rows = []
    for g in jsonl("ask_dev.jsonl"):
        calls.clear()
        t = time.perf_counter()
        a = ask.ask(conn, g["q"], TODAY)
        rows.append({"q": g["q"], "op": g["op"], "ok": correct(a, g), "gold": g["answer"], "got": a.answer, "route": a.route,
                     "parser": getattr(a, "parser", None), "model_calls": len(calls), "s": round(time.perf_counter() - t, 1),
                     "query": a.query, "conflicts": len(a.conflicts), "ungrounded_numbers": getattr(a, "ungrounded_numbers", None)})
        print(("OK  " if rows[-1]["ok"] else "BAD ") + f"[{g['op']}] {g['q']} -> {a.answer[:150]}")
    by_op = defaultdict(Counter)
    for r in rows:
        by_op[r["op"]]["n"] += 1
        by_op[r["op"]]["ok"] += r["ok"]
    summary = {"n": len(rows), "correct": sum(r["ok"] for r in rows), "by_op": {k: f"{v['ok']}/{v['n']}" for k, v in sorted(by_op.items())},
               "parser": dict(Counter(str(r["parser"]) for r in rows)), "model_calls": sum(r["model_calls"] for r in rows),
               "today": TODAY.isoformat(), "when": time.strftime("%Y-%m-%d %H:%M")}
    print(json.dumps(summary, indent=2))
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    out[tag] = {"summary": summary, "rows": rows}
    RESULTS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--db", type=Path, required=True)
    s.add_argument("--docs", type=Path, required=True)
    s.add_argument("--labels", type=Path, default=ROOT / "data" / "synth" / "out" / "labels.jsonl")
    r = sub.add_parser("run")
    r.add_argument("--tag", required=True)
    args = ap.parse_args()
    setup(args.db, args.docs.resolve(), args.labels) if args.cmd == "setup" else run(args.tag)
