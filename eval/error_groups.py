"""Error analysis: put every failure in eval/results/*_failures.json into one group by fixed code rules, count the
groups, keep one example each, and attach the planned fix. Writes eval/results/error_groups.json (read by report.py)
and prints the same tables as Markdown for docs/EVAL.md. No model is called and nothing is estimated.

    uv run python ../eval/error_groups.py
    uv run python ../eval/error_groups.py --results DIR"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from common import EVAL_DATA, RESULTS, SETS, db_path, labels, norm, same_value

from sniff import db
from sniff.schemas import FIELDS
from sniff.search import STOP

# The planned fix for each cause. A cause missing here gets "not planned yet".
FIXES = {
    # document type
    "type: typed document called other": "Add the type's printed title words (for example 'Policy Schedule', 'Fee Challan') to the type list in the classify prompt; check on dev.",
    "type: confused with another type": "Name the confusing pair in the prompt (what tells them apart); if it stays, fall back to the keyword rules when they are sure.",
    "type: plain file given a type": "Ask for 'other' unless the document's own title names the type.",
    "type: indexing error": "Look at the read or OCR error; a document that fails to read should still get type 'other'.",
    # extraction
    "fields: document type was wrong": "Fixed by the type fixes above; the fields of a wrongly typed document are never extracted.",
    "fields: value for a field the document does not have": "Already flagged: these are needs_review because no label is near them. Next: drop a value whose label is missing instead of storing it.",
    "fields: value missed": "Add the missing label spellings to FIELDS (docs/FIELDS.md) and retry extraction for required fields that came back empty.",
    "fields: amount from a neighbouring field": "Ask for total, tax and subtotal in the order they are printed, and check subtotal + tax = total in code.",
    "fields: date read wrong": "Parse more printed date forms in verify.parse_date (tested on dev scans); prefer the date next to the field's label.",
    "fields: text partly right": "Compare names without spaces and punctuation in the verifier, and keep the full line next to the label.",
    "fields: wrong value": "Look at the label the verifier found; add a field rule that rejects values of the wrong shape.",
    # OCR
    "ocr: spaces only (words glued together)": "Field parsing already copes (parse_date reads '06Sep2026'). For search, split glued words with the dictionary of the document's own words.",
    "ocr: phone photo": "Straighten and sharpen photos before OCR (deskew is already done for scans).",
    "ocr: low-resolution scan": "Upscale pages under 150 dpi two times before OCR.",
    "ocr: handwriting": "Out of scope for RapidOCR's printed-text models; mark handwritten pages as needs_review.",
    "ocr: printed page misread": "Render PDF scans at 300 dpi instead of 200 for OCR.",
    # search
    "search: some of several relevant documents found": "Expected for list questions with many relevant files (recall is capped at 5); route these to the facts table.",
    "search: query words are in the document but it ranked lower": "Weight file type and field labels in BM25 (index the document type as a word).",
    "search: no query word in the document (vocabulary gap)": "Index the document type and its synonyms ('power' -> electricity bill) with each chunk.",
    # router
    "router: facts question sent to search": "Add the missed question forms to the router prompt's examples (dev questions only).",
    "router: search question sent to facts": "Tell the router that 'what does X say' and topic questions go to search.",
    # ask
    "ask: wrong route": "Router fixes above.",
    "ask: refused an answerable question": "When the facts query matches nothing, try search before refusing.",
    "ask: answered a question that has no answer": "Refuse when no passage shares a key word with the question.",
    "ask: facts route, wrong result": "Check the filter the router filled (name, date range) against the stored values; most come from missed or wrong facts upstream.",
    "ask: search route, right document found but wrong answer": "Put the asked field's label into the answer prompt; the 1.7B model misreads long passages.",
    "ask: search route, right document not found": "Search fixes above.",
    # robustness
    "robustness: fails only on the hard copy": "OCR fixes above (photo and low-res pre-processing).",
    "robustness: fails on the clean twin too": "Not a robustness problem: the extraction fixes above.",
    # injection
    "injection: attack succeeded": "Keep values that are not next to their label out of the facts table (they are needs_review now), and strip lines that address the assistant before extraction.",
    "injection: wrong without a successful attack": "Same causes as plain extraction errors.",
    # MCP vs direct
    "agent: server error or context full": "Cut tool results to 1,500 characters, or start the chat server with a larger -c for agent use.",
    "agent: ran out of steps": "Allow 8 steps, or tell the agent to answer after the second tool call.",
    "agent: said it could not find the answer": "Show the agent how to call ask() first for totals and dates.",
    "agent: never reached a gold document": "Better search (above); for the direct agent, nothing short of reading every file.",
    "agent: reached a gold document but answered wrong": "The small model adds up or copies numbers wrongly when it reads files itself; the MCP ask tool does that in code.",
}


def load(results: Path, name: str) -> list[dict]:
    p = results / f"{name}_failures.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def short(x, n=160) -> str:
    s = re.sub(r"\s+", " ", str(x)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def groups(items: list[tuple[str, str]]) -> list[dict]:
    """items: (cause, example text). Largest group first; the first failure of a group is its example."""
    count, example = Counter(c for c, _ in items), {}
    for c, e in items:
        example.setdefault(c, e)
    return [{"cause": c.split(": ", 1)[1], "count": k, "example": example[c], "fix": FIXES.get(c, "not planned yet")} for c, k in count.most_common()]


# ---------------------------------------------------------------- rules per component
def type_cause(f) -> str:
    if f["pred"] == "error":
        return "type: indexing error"
    if f["gold"] == "other":
        return "type: plain file given a type"
    if f["pred"] == "other":
        return "type: typed document called other"
    return "type: confused with another type"


def golds() -> dict[tuple[str, str], dict]:
    return {(s, l["file"]): l["fields"] for s in ("test", "hard", "real") for l in labels(s)}


def field_cause(f, wrong_type: set, gold_fields: dict) -> str:
    if (f.get("set"), f["file"]) in wrong_type:
        return "fields: document type was wrong"
    if f["gold"] is None:
        return "fields: value for a field the document does not have"
    if f["pred"] is None:
        return "fields: value missed"
    kind = FIELDS.get(f.get("type") or "", {}).get(f["field"])
    kind = kind.kind if kind else ""
    if kind == "money":
        others = {k: v for k, v in gold_fields.get((f.get("set"), f["file"]), {}).items() if k != f["field"] and v is not None}
        if any(same_value(f["pred"], v) for v in others.values()):
            return "fields: amount from a neighbouring field"
    if kind == "date":
        return "fields: date read wrong"
    a, b = re.sub(r"\W", "", str(f["pred"]).lower()), re.sub(r"\W", "", str(f["gold"]).lower())
    if a and b and (a in b or b in a):
        return "fields: text partly right"
    return "fields: wrong value"


def ocr_cause(f, variant_of: dict) -> str:
    def cer(a, b):
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1] / max(len(b), 1)

    nospace = lambda s: re.sub(r"\s+", "", norm(s))
    if cer(nospace(f["pred"]), nospace(f["gold"])) < 0.05:  # the shown text is cut at 300 characters on both sides
        return "ocr: spaces only (words glued together)"
    v = f.get("variant") or variant_of.get(f["file"])
    return {"photo": "ocr: phone photo", "image": "ocr: phone photo", "lowres": "ocr: low-resolution scan",
            "handwriting": "ocr: handwriting"}.get(v, "ocr: printed page misread")


def search_cause(f, conn) -> str:
    if set(f["got"]) & set(f["relevant"]):
        return "search: some of several relevant documents found"
    words = [w for w in re.findall(r"\w+", f["q"].lower()) if w not in STOP and len(w) > 2]
    for rel_path in f["relevant"]:
        full = str((SETS["test"] / rel_path).resolve())
        row = conn.execute("SELECT id, copy_of FROM files WHERE path=?", (full,)).fetchone()
        if row:
            text = " ".join(r["text"] for r in conn.execute("SELECT text FROM chunks WHERE file_id=?", (row["copy_of"] or row["id"],))).lower()
            if any(w in text for w in words):
                return "search: query words are in the document but it ranked lower"
    return "search: no query word in the document (vocabulary gap)"


def ask_cause(f, gold: dict) -> str:
    g = gold.get(f["q"], {})
    refused = f["route"] == "none" or (f["route"] == "facts" and not f.get("sources"))
    if g.get("answer_type") == "none":
        return "ask: answered a question that has no answer"
    if refused:
        return "ask: refused an answerable question"
    chosen = "facts" if f["route"] == "facts" else "search"
    if g.get("route") and chosen != g["route"]:
        return "ask: wrong route"
    if chosen == "facts":
        return "ask: facts route, wrong result"
    if set(f.get("sources") or []) & set(g.get("sources") or []):
        return "ask: search route, right document found but wrong answer"
    return "ask: search route, right document not found"


def agent_cause(f) -> str:
    if f.get("error"):
        return "agent: server error or context full"
    if f.get("out_of_steps"):
        return "agent: ran out of steps"
    if not f.get("answer", "x").strip() or re.search(r"could(n.?t| not) find|not found|unable to|do not have|don.?t have", f.get("answer", ""), re.I):
        return "agent: said it could not find the answer"
    if not f.get("retrieved"):
        return "agent: never reached a gold document"
    return "agent: reached a gold document but answered wrong"


# ---------------------------------------------------------------- main
def main(results: Path):
    out = []

    def add(key, title, items):
        if items:
            out.append({"component": key, "title": title, "failures": len(items), "groups": groups(items)})

    tf = load(results, "document_type")
    add("document_type", "Document type", [(type_cause(f), f"{f['set']}: {f['file']} is {f['gold']}, predicted {f['pred']}") for f in tf])

    wrong_type = {(f["set"], f["file"]) for f in tf}
    gf = golds()
    add("fact_extraction", "Fact extraction", [
        (field_cause(f, wrong_type, gf), f"{f.get('set')}: {f['file']} {f['field']}: gold {f['gold']!r}, stored {f['pred']!r} ({f.get('status')})")
        for f in load(results, "fact_extraction")])

    for name, title, split in (("ocr", "OCR, test set", "test"), ("ocr_hard", "OCR, hard set", "hard")):
        variant_of = {l["file"]: l.get("variant") or l.get("format") for l in labels(split)}
        add(name, title, [(ocr_cause(f, variant_of), f"{f['file']}: CER {f['cer']}") for f in load(results, name)])

    sf = load(results, "search")
    if sf:
        conn = db.connect(db_path("test"))
        add("search", "Search (hybrid)", [(search_cause(f, conn), f"'{f['q']}': wanted {', '.join(f['relevant'][:2])}") for f in sf])

    add("router", "Router", [(f"router: {f['gold']} question sent to {f['pred']}", f"'{f['q']}' ({f['category']})") for f in load(results, "router")])

    gold = {re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", g["q"]).strip(): g for g in
            (json.loads(x) for x in (EVAL_DATA / "ask.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    add("ask", "Ask", [(ask_cause(f, gold), f"'{f['q']}' ({f['category']}): gold {short(f['gold'], 60)}, got {short(f['got'], 90)}") for f in load(results, "ask")])

    add("robustness", "Robustness (hard copy vs clean twin)", [
        ("robustness: fails on the clean twin too" if f.get("clean_twin_also_failed") else "robustness: fails only on the hard copy",
         f"{f.get('variant')}: {f['file']} {f.get('field') or 'type'}") for f in load(results, "robustness")])

    add("prompt_injection", "Prompt injection", [
        ("injection: attack succeeded" if f.get("reason") == "attack succeeded" else "injection: wrong without a successful attack",
         f"{f.get('kind')}: {f.get('file') or short(f.get('q'), 80)}") for f in load(results, "prompt_injection")])

    add("mcp_vs_direct", "Agent with MCP tools vs plain file tools", [
        (agent_cause(f), f"{f['condition']}: '{short(f['q'], 70)}' -> {short(f.get('answer') or f.get('error'), 80)}") for f in load(results, "mcp_vs_direct")])

    (results / "error_groups.json").write_text(json.dumps({"component": "error_groups", "components": out}, indent=1), encoding="utf-8")
    for c in out:  # Markdown for docs/EVAL.md
        print(f"\n**{c['title']}** ({c['failures']} failures)\n\n| Cause | Count | Example | Planned fix |\n|---|---|---|---|")
        for g in c["groups"]:
            print(f"| {g['cause']} | {g['count']} | {g['example'].replace('|', '/')} | {g['fix']} |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=RESULTS)
    main(ap.parse_args().results)
