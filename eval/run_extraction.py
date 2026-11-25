"""Document type (classification) and fact extraction per test set, against keyword and regex baselines,
and the model comparison (feedback point 2).

    uv run python ../eval/run_extraction.py                    # score the databases run_index.py made (test, hard, real)
    uv run python ../eval/run_extraction.py --sets test hard   # only some sets
    uv run python ../eval/run_extraction.py --compare qwen3-1.7b=http://127.0.0.1:8080/v1 gemma3-1b=http://127.0.0.1:8090/v1 --limit 40

--compare indexes the same documents (a fixed subset per set, stratified by type, with --limit) once per model into
.sniff/eval[-set]-<model>.db through the real pipeline, then scores every model the same way. It is resumable, and
models can be run one at a time (the 8B model needs the 1.7B server stopped): rows for other models already in
model_comparison.json are kept.

Metrics per model and set: type accuracy and macro-F1; field accuracy (null labels count: the model must leave a
missing field empty) and per field; schema-valid rate; verified precision (of facts marked verified, how many are
right) and verified recall (of right facts, how many were marked verified); needs-review rate; p50/p95 LLM latency
per document (classify + extract)."""
import argparse
import json
from collections import Counter, defaultdict

import baselines
from common import RESULTS, db_path, index, labels, latency, path_of, rel, same_value, save, stay_awake, stratified, use_llm

from sniff import config, db
from sniff.schemas import DOC_TYPES, FACT_MODELS

SETS = ["test", "hard", "real", "own"]


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    f1s = []
    for t in sorted({x for p in pairs for x in p}):
        tp = sum(1 for g, p in pairs if g == t and p == t)
        fp = sum(1 for g, p in pairs if g != t and p == t)
        fn = sum(1 for g, p in pairs if g == t and p != t)
        f1s.append(2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s) if f1s else 0.0


def owner(r) -> int:
    """An exact copy stores no rows of its own; its facts and chunks belong to the file it copies."""
    return r["copy_of"] if "copy_of" in r.keys() and r["copy_of"] else r["id"]


def status(f) -> str:
    return f["status"] if "status" in f.keys() and f["status"] else ("verified" if f["verified"] else "needs_review")


def score(conn, gold: list[dict], times: dict, tag: dict) -> tuple[dict, dict, list, list]:
    """Score one database against its labels. Returns (metrics, per-field accuracy, type failures, field failures)."""
    files = {rel(r["path"]): r for r in conn.execute("SELECT * FROM files")}
    gold = [g for g in gold if g["file"] in files]  # a --limit run indexes a subset
    type_pairs, base_pairs, type_fail, field_fail, ms = [], [], [], [], []
    c, per_field = Counter(), defaultdict(lambda: [0, 0])
    for g in gold:
        r = files[g["file"]]
        pred = (r["doc_type"] or "other") if r["status"] == "indexed" else "error"
        text = " ".join(x["text"] for x in conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id", (owner(r),)))
        type_pairs.append((g["type"], pred))
        base_pairs.append((g["type"], baselines.classify_keywords(text)))
        t = times.get(str(path_of(g)))
        if isinstance(t, dict) and t.get("llm_ms"):  # exact copies make no LLM call
            ms.append(t["llm_ms"])
        info = {**tag, "file": g["file"], "variant": g.get("variant"), "effects": g.get("effects")}
        if pred != g["type"]:
            type_fail.append({**info, "gold": g["type"], "pred": pred, "error": r["error"], "text": text[:300]})
        if g["type"] not in FACT_MODELS:
            continue
        c["docs"] += 1
        c["schema_ok"] += r["status"] == "indexed" and not r["error"]  # error = output invalid after retries
        facts = {f["field"]: f for f in conn.execute("SELECT * FROM facts WHERE file_id=?", (owner(r),))} if pred == g["type"] else {}
        regex = baselines.extract_regex(g["type"], text)
        for field, gv in g["fields"].items():
            f = facts.get(field)
            ok = same_value(f["value"] if f else None, gv)
            c["fields"] += 1
            c["ok"] += ok
            c["regex_ok"] += same_value(regex.get(field), gv)
            per_field[field][0] += ok
            per_field[field][1] += 1
            if f:
                st = status(f)
                c["facts"] += 1
                c["verified"] += st == "verified"
                c["verified_ok"] += st == "verified" and ok
                c["review"] += st == "needs_review"
                c["right"] += ok
                c["right_verified"] += ok and st == "verified"
            if not ok:
                field_fail.append({**info, "type": g["type"], "field": field, "gold": gv, "pred": f["value"] if f else None,
                                   "status": status(f) if f else None, "reason": f["reason"] if f and "reason" in f.keys() else None,
                                   "text": text[:300]})
    n = len(type_pairs)
    div = lambda a, b: round(a / b, 3) if b else None
    lat = latency(ms)
    m = {
        "n": n,
        "type_accuracy": div(sum(g == p for g, p in type_pairs), n), "type_macro_F1": round(macro_f1(type_pairs), 3),
        "keyword_accuracy": div(sum(g == p for g, p in base_pairs), n), "keyword_macro_F1": round(macro_f1(base_pairs), 3),
        "docs_with_fields": c["docs"], "fields": c["fields"],
        "field_accuracy": div(c["ok"], c["fields"]), "regex_field_accuracy": div(c["regex_ok"], c["fields"]),
        "schema_valid": div(c["schema_ok"], c["docs"]),
        "verified_precision": div(c["verified_ok"], c["verified"]), "verified_recall": div(c["right_verified"], c["right"]),
        "needs_review_rate": div(c["review"], c["facts"]),
        "latency_p50_ms": lat["p50"], "latency_p95_ms": lat["p95"],
        "confusion": {"labels": list(DOC_TYPES) + ["error"],
                      "matrix": [[sum(1 for g, p in type_pairs if g == a and p == b) for b in list(DOC_TYPES) + ["error"]]
                                 for a in list(DOC_TYPES) + ["error"]]},
    }
    return m, {f: round(a / b, 3) for f, (a, b) in sorted(per_field.items())}, type_fail, field_fail


def load_times(path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def evaluate(sets: list[str]):
    """Score the databases made by run_index.py."""
    from run_index import times_path

    model = config.LLM_MODEL
    type_rows, fact_rows, type_fail, fact_fail, per_field, confusion = [], [], [], [], {}, None
    for split in sets:
        if not db_path(split).exists():
            print(f"skip {split}: {db_path(split).name} not found (run run_index.py --set {split})")
            continue
        m, pf, tf, ff = score(db.connect(db_path(split)), labels(split), load_times(times_path(split)), {"set": split})
        confusion = confusion or m["confusion"]
        type_rows += [
            {"system": f"Sniff ({model}, prompted)", "set": split, "n": m["n"], "accuracy": m["type_accuracy"], "macro_F1": m["type_macro_F1"]},
            {"system": "Keyword rules (baseline)", "set": split, "n": m["n"], "accuracy": m["keyword_accuracy"], "macro_F1": m["keyword_macro_F1"]},
        ]
        fact_rows += [
            {"system": f"Sniff ({model}, JSON schema)", "set": split, "n": m["docs_with_fields"], "field_accuracy": m["field_accuracy"],
             "schema_valid": m["schema_valid"], "verified_precision": m["verified_precision"],
             "verified_recall": m["verified_recall"], "needs_review_rate": m["needs_review_rate"],
             "latency_p50_ms": m["latency_p50_ms"], "latency_p95_ms": m["latency_p95_ms"]},
            {"system": "Regex rules (baseline)", "set": split, "n": m["docs_with_fields"], "field_accuracy": m["regex_field_accuracy"], "schema_valid": 1.0},
        ]
        per_field[split] = pf
        type_fail += tf
        fact_fail += ff
    if not type_rows:
        raise SystemExit("nothing to score")
    save("document_type", {"n": sum(r["n"] for r in type_rows[::2]), "rows": type_rows, "confusion": confusion}, type_fail)
    save("fact_extraction", {"n": sum(r["n"] for r in fact_rows[::2]), "rows": fact_rows, "per_field": per_field}, fact_fail)


def compare(specs: list[str], sets: list[str], limit: int | None):
    """Index the same documents with each model, then score them all. Merges into model_comparison.json."""
    out = RESULTS / "model_comparison.json"
    old = json.loads(out.read_text()) if out.exists() else {"rows": [], "per_field": {}}
    names = [s.split("=", 1)[0] for s in specs]
    rows = [r for r in old["rows"] if r["system"] not in names]
    per_field = {k: v for k, v in old.get("per_field", {}).items() if k.split(" [")[0] not in names}
    fail_path = RESULTS / "model_comparison_failures.json"
    failures = [f for f in (json.loads(fail_path.read_text()) if fail_path.exists() else []) if f.get("model") not in names]
    for spec in specs:
        name, url = spec.split("=", 1)
        use_llm(url, name)
        for split in sets:
            gold = stratified(labels(split), limit)
            if not gold:
                print(f"skip {split}: no labels")
                continue
            path = db_path(split, name)
            conn = db.connect(path)
            times = index(conn, [path_of(g) for g in gold], path.with_suffix(".times.json"))
            m, pf, tf, ff = score(conn, gold, times, {"set": split, "model": name})
            m.pop("confusion")
            rows.append({"system": name, "set": split, **{k: v for k, v in m.items() if not k.startswith(("keyword", "regex"))}})
            per_field[f"{name} [{split}]"] = pf
            failures += tf + ff
    rows.sort(key=lambda r: (SETS.index(r["set"]) if r["set"] in SETS else 9, r["system"]))
    save("model_comparison", {"n": sum(r["n"] for r in rows), "limit_per_set": limit, "rows": rows, "per_field": per_field}, failures)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", nargs="+", default=SETS, choices=SETS)
    ap.add_argument("--compare", nargs="+", metavar="NAME=URL", help="models to compare, e.g. gemma3-1b=http://127.0.0.1:8090/v1")
    ap.add_argument("--limit", type=int, help="with --compare: documents per set, stratified by type")
    a = ap.parse_args()
    stay_awake()
    compare(a.compare, a.sets, a.limit) if a.compare else evaluate(a.sets)
