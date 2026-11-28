"""Human ratings of search answers (human evaluation; at least two raters, agreement reported).

    uv run python ../eval/ratings.py make                       # ask 60 search questions, write data/ratings/
    uv run python ../eval/ratings.py score                      # read data/ratings/rated_*.csv

make needs the chat and embedding servers and the test set indexed (run_index.py). It answers 60 questions of
data/eval/ask.jsonl whose route is "search" (round-robin over categories, fixed shuffle) with ask.answer_from_search, the
same call the app uses, and writes:
  answers.json   the 60 items: id, category, question, reference_answer, answer, passages (the cited text)
  sheet.csv      one row per item with empty rater, correctness, groundedness and usefulness columns (opens in Excel)
  form.html      the same as a form for a browser; it saves as rated_<name>.csv with the Download button
Each team member rates all 60 and saves the file as data/ratings/rated_<name>.csv. The category is not shown to raters.
Scales: correctness 0 wrong or missing, 1 partly right, 2 fully right (against the reference answer); groundedness 0 or 1
(1 = everything the answer says is supported by the passages shown); usefulness 1 to 5.

score reads every rated_*.csv (columns id, rater, correctness, groundedness, usefulness; other columns are ignored; an
empty cell means not rated) and computes, in code: mean per metric and per rater, exact agreement, Cohen's kappa for each
pair of raters (linear weights for the ordered scales), Fleiss' kappa over the items every rater rated, and the means by
category. Results: eval/results/human_ratings.json; answers rated 0 for correctness by most raters go to the failures."""
import argparse
import csv
import itertools
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from common import ROOT, db_path, jsonl, save, stay_awake, stratified

RATINGS = ROOT / "data" / "ratings"
METRICS = {"correctness": (0, 2, True), "groundedness": (0, 1, False), "usefulness": (1, 5, True)}  # low, high, ordered
PASSAGE_CHARS = 700


# ---------------------------------------------------------------- agreement (all arithmetic is here, not in a model)
def cohen_kappa(a: list, b: list, low: int, high: int, weighted: bool) -> float | None:
    """Agreement of two raters on the same items, beyond chance. weighted: linear weights, so 4 vs 5 counts as a smaller
    disagreement than 1 vs 5. None when chance agreement is already total (both gave one value throughout)."""
    n = len(a)
    if not n:
        return None
    cats = range(low, high + 1)
    w = lambda i, j: abs(i - j) / (high - low) if weighted else float(i != j)
    ra, rb = {i: sum(x == i for x in a) for i in cats}, {j: sum(y == j for y in b) for j in cats}
    observed = sum(w(x, y) for x, y in zip(a, b)) / n
    expected = sum(w(i, j) * ra[i] * rb[j] for i in cats for j in cats) / n ** 2
    return None if expected == 0 else 1 - observed / expected


def fleiss_kappa(items: list[list], low: int, high: int) -> float | None:
    """items: for each item, the labels of all raters (the same number for every item, at least 2)."""
    n, N = len(items[0]), len(items)
    counts = [[sum(x == c for x in item) for c in range(low, high + 1)] for item in items]
    p_item = [(sum(c * c for c in row) - n) / (n * (n - 1)) for row in counts]
    p_cat = [sum(row[j] for row in counts) / (N * n) for j in range(high - low + 1)]
    p_e = sum(p * p for p in p_cat)
    return None if p_e == 1 else (sum(p_item) / N - p_e) / (1 - p_e)


def r3(x):
    return None if x is None else round(x, 3)


# ---------------------------------------------------------------- reading rated files
def read_rated(files: list[Path]) -> dict[str, dict[int, dict[str, int]]]:
    """{rater: {item id: {metric: value}}}. Empty cells are skipped; a value outside its scale stops the run."""
    out: dict[str, dict[int, dict[str, int]]] = defaultdict(dict)
    for f in files:
        for row in csv.DictReader(f.open(encoding="utf-8-sig", newline="")):
            rater = (row.get("rater") or f.stem.removeprefix("rated_")).strip()
            vals = {}
            for m, (lo, hi, _) in METRICS.items():
                cell = (row.get(m) or "").strip()
                if not cell:
                    continue
                if not re.fullmatch(r"\d", cell) or not lo <= int(cell) <= hi:
                    raise ValueError(f"{f.name}, item {row.get('id')}: {m} must be {lo} to {hi}, not '{cell}'")
                vals[m] = int(cell)
            if vals:
                out[rater].setdefault(int(row["id"]), {}).update(vals)
    return out


def summarise(rated: dict, items: dict[int, dict]) -> tuple[list[dict], dict, dict, list[dict]]:
    """Returns (rows per metric, pairwise kappas, means by category, items most raters rated 0 for correctness)."""
    raters = sorted(rated)
    rows, pairs = [], {}
    for m, (lo, hi, ordered) in METRICS.items():
        by = {r: {i: v[m] for i, v in rated[r].items() if m in v} for r in raters}
        allv = [v for r in raters for v in by[r].values()]
        row = {"metric": m, "scale": f"{lo} to {hi}", "ratings": len(allv), "mean": r3(sum(allv) / len(allv)) if allv else None,
               **{f"mean_{r}": r3(sum(by[r].values()) / len(by[r])) if by[r] else None for r in raters}}
        ks, same, total = {}, 0, 0
        for a, b in itertools.combinations(raters, 2):
            ids = sorted(set(by[a]) & set(by[b]))
            ks[f"{a} vs {b}"] = r3(cohen_kappa([by[a][i] for i in ids], [by[b][i] for i in ids], lo, hi, ordered)) if ids else None
            same += sum(by[a][i] == by[b][i] for i in ids)
            total += len(ids)
        pairs[m] = ks
        shared = sorted(set.intersection(*(set(by[r]) for r in raters))) if raters else []
        row["items_rated_by_all"] = len(shared)
        row["exact_agreement"] = r3(same / total) if total else None
        row["fleiss_kappa"] = r3(fleiss_kappa([[by[r][i] for r in raters] for i in shared], lo, hi)) if len(raters) > 1 and shared else None
        rows.append(row)
    cats: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for r in raters:
        for i, v in rated[r].items():
            for m, x in v.items():
                cats[items.get(i, {}).get("category", "unknown")][m].append(x)
    by_cat = {c: {"n": len({i for r in raters for i in rated[r] if items.get(i, {}).get("category", "unknown") == c}),
                  **{m: r3(sum(xs) / len(xs)) for m, xs in d.items()}} for c, d in sorted(cats.items())}
    bad = []
    for i, it in sorted(items.items()):
        scores = [rated[r][i]["correctness"] for r in raters if i in rated[r] and "correctness" in rated[r][i]]
        if scores and sum(s == 0 for s in scores) > len(scores) / 2:
            bad.append({"id": i, "category": it["category"], "question": it["question"], "answer": it["answer"], "reference": it["reference_answer"],
                        "correctness_scores": scores})
    return rows, pairs, by_cat, bad


def score(folder: Path):
    files = sorted(folder.glob("rated_*.csv"))
    if not files:
        raise SystemExit(f"no rated_*.csv in {folder}: each rater saves a filled sheet or form there")
    items = {i["id"]: i for i in json.loads((folder / "answers.json").read_text(encoding="utf-8"))}
    rated = read_rated(files)
    rows, pairs, by_cat, bad = summarise(rated, items)
    save("human_ratings", {"n": len(items), "raters": sorted(rated), "rows": rows, "pairwise_cohen_kappa": pairs, "by_category": by_cat}, bad)


# ---------------------------------------------------------------- make
def passages(conn, answer) -> str:
    """The text of every passage the answer cites, as the rater should check it against."""
    out = []
    for n, s in enumerate(answer.sources, 1):
        row = conn.execute("SELECT text FROM chunks WHERE file_id=? AND page=? ORDER BY id LIMIT 1", (s.file_id, s.page)).fetchone()
        out.append(f"[{n}] {s.name}, page {s.page}: {' '.join((row['text'] if row else '').split())[:PASSAGE_CHARS]}")
    return "\n".join(out) or "(no passage: the system said it could not find an answer)"


def build_items(questions: list[dict], ask_fn, passages_fn, seed: int = 7) -> list[dict]:
    """ask_fn(question) -> Answer; passages_fn(answer) -> text. The order is shuffled once, with a fixed seed."""
    items = []
    for g in questions:
        a = ask_fn(g["q"])
        items.append({"category": g["category"], "question": g["q"], "answer": a.answer, "passages": passages_fn(a),
                      "reference_answer": g["answer"] or "(not in the documents: the right reply is that it cannot be found)"})
    random.Random(seed).shuffle(items)
    return [{"id": i, **it} for i, it in enumerate(items, 1)]


FORM = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rate Sniff answers</title>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mute:#666;--line:#d8d8dc;--card:#f6f6f8;--acc:#2d5bd8}
@media (prefers-color-scheme:dark){:root{--bg:#18181b;--fg:#ececf0;--mute:#9a9aa5;--line:#34343c;--card:#222227;--acc:#7c9cff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:20px} .help{color:var(--mute)} .help li{margin:2px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:14px 0}
.q{font-weight:600} .lbl{color:var(--mute);font-size:12px;text-transform:uppercase;margin-top:8px}
pre{white-space:pre-wrap;margin:2px 0;font:inherit} fieldset{border:0;padding:0;margin:8px 0 0}
legend{font-size:13px;color:var(--mute)} label{margin-right:14px;white-space:nowrap}
input[type=text]{font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
footer{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:10px 16px}
button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer}
</style></head><body><main>
<h1>Rate Sniff's answers</h1>
<p>__COUNT__ questions. For each one, read the answer and the passages it cites, then rate three things. Rate alone, without talking to the others.</p>
<ul class="help">
<li><b>Correctness</b>: 0 = wrong or missing, 1 = partly right, 2 = fully right. Compare with the reference answer.</li>
<li><b>Grounded</b>: 1 = everything the answer says is supported by the passages shown, 0 = something is not.</li>
<li><b>Usefulness</b>: 1 = useless to 5 = exactly what I needed.</li>
</ul>
<p><label>Your name <input type="text" id="rater" autocomplete="off"></label></p>
<div id="items"></div>
</main>
<footer><div style="max-width:760px;margin:0 auto;display:flex;gap:16px;align-items:center">
<span id="count" class="help"></span><button id="save" type="button">Download my ratings (CSV)</button></div></footer>
<script>
const ITEMS = __DATA__;
const SCALES = {correctness: [0, 1, 2], groundedness: [0, 1], usefulness: [1, 2, 3, 4, 5]};
const NAMES = {correctness: "Correctness", groundedness: "Grounded", usefulness: "Usefulness"};
let ratings = {};
try { ratings = JSON.parse(localStorage.getItem("sniff-ratings") || "{}"); } catch (e) {}
const remember = () => { try { localStorage.setItem("sniff-ratings", JSON.stringify(ratings)); } catch (e) {} };
const add = (parent, tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; parent.appendChild(e); return e; };
const root = document.getElementById("items");
for (const it of ITEMS) {
  const card = add(root, "div", undefined, "card");
  add(card, "div", it.id + ". " + it.question, "q");
  add(card, "div", "Reference answer", "lbl"); add(card, "pre", it.reference_answer);
  add(card, "div", "Sniff's answer", "lbl"); add(card, "pre", it.answer);
  add(card, "div", "Passages it cited", "lbl"); add(card, "pre", it.passages);
  for (const [m, vals] of Object.entries(SCALES)) {
    const fs = add(card, "fieldset"); add(fs, "legend", NAMES[m]);
    for (const v of vals) {
      const lab = add(fs, "label"); const r = add(lab, "input"); r.type = "radio"; r.name = m + it.id; r.value = v;
      r.checked = (ratings[it.id] || {})[m] === v;
      r.onchange = () => { (ratings[it.id] = ratings[it.id] || {})[m] = v; remember(); count(); };
      lab.append(" " + v);
    }
  }
}
const who = document.getElementById("rater");
try { who.value = localStorage.getItem("sniff-rater") || ""; } catch (e) {}
who.oninput = () => { try { localStorage.setItem("sniff-rater", who.value); } catch (e) {} };
function count() {
  const done = ITEMS.filter(it => Object.keys(SCALES).every(m => (ratings[it.id] || {})[m] !== undefined)).length;
  document.getElementById("count").textContent = done + " of " + ITEMS.length + " fully rated";
}
function csvCell(x) { x = String(x); return /[",\\n]/.test(x) ? '"' + x.replace(/"/g, '""') + '"' : x; }
function toCsv(rater, rows) {
  const head = ["id", "rater", "correctness", "groundedness", "usefulness"];
  return [head].concat(ITEMS.map(it => [it.id, rater].concat(head.slice(2).map(m => (rows[it.id] || {})[m] ?? "")))).map(r => r.map(csvCell).join(",")).join("\\r\\n") + "\\r\\n";
}
document.getElementById("save").onclick = () => {
  const name = who.value.trim();
  if (!name) { alert("Type your name first."); who.focus(); return; }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\\ufeff" + toCsv(name, ratings)], {type: "text/csv"}));
  a.download = "rated_" + name.replace(/[^A-Za-z0-9_-]+/g, "_") + ".csv";
  a.click();
};
count();
</script></body></html>
"""


def write_files(folder: Path, items: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "answers.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    with open(folder / "sheet.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "question", "reference_answer", "answer", "passages", "rater", "correctness", "groundedness", "usefulness"])
        for it in items:
            w.writerow([it["id"], it["question"], it["reference_answer"], it["answer"], it["passages"], "", "", "", ""])
    shown = [{k: it[k] for k in ("id", "question", "reference_answer", "answer", "passages")} for it in items]
    data = json.dumps(shown, ensure_ascii=False).replace("</", "<\\/")  # nothing in an answer may close the script tag
    (folder / "form.html").write_text(FORM.replace("__COUNT__", str(len(items))).replace("__DATA__", data), encoding="utf-8")


def make(n: int):
    from sniff import ask, db

    qs = [g for g in jsonl("ask.jsonl") if g.get("route") == "search" and g.get("category")]
    for g in qs:
        g["q"] = re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", g["q"]).strip()
        g["type"] = g["category"]
    qs = stratified(qs, n)
    conn = db.connect(db_path("test"))
    items = build_items(qs, lambda q: ask.answer_from_search(conn, q), lambda a: passages(conn, a))
    write_files(RATINGS, items)
    print(f"{len(items)} items -> {RATINGS} (sheet.csv, form.html, answers.json)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--n", type=int, default=60)
    sc = sub.add_parser("score")
    sc.add_argument("--dir", type=Path, default=RATINGS)
    a = ap.parse_args()
    stay_awake()
    make(a.n) if a.cmd == "make" else score(a.dir)
