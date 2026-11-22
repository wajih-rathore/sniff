"""Document type (classification) and fact extraction, against keyword and regex baselines.
Run run_index.py first; this reads the pipeline's output from the evaluation database."""
import json
from collections import Counter

import baselines
from common import RESULTS, labels, latency, rel, same_value, save

from sniff import db
from sniff.schemas import DOC_TYPES, FACT_MODELS


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    f1s = []
    for t in DOC_TYPES:
        tp = sum(1 for g, p in pairs if g == t and p == t)
        fp = sum(1 for g, p in pairs if g != t and p == t)
        fn = sum(1 for g, p in pairs if g == t and p != t)
        if tp + fp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s)


def main():
    conn = db.connect()
    gold = {l["file"]: l for l in labels("test")}
    files = {rel(r["path"]): r for r in conn.execute("SELECT * FROM files")}
    times = json.loads((RESULTS / "index_times.json").read_text())

    type_pairs, base_pairs, type_fail = [], [], []
    field_hits = Counter()
    field_fail = []
    schema_ok = 0
    for f, g in gold.items():
        r = files.get(f)
        pred = (r["doc_type"] or "other") if r and r["status"] == "indexed" else "error"
        text = " ".join(c["text"] for c in conn.execute("SELECT text FROM chunks WHERE file_id=? ORDER BY id", (r["id"],))) if r else ""
        base = baselines.classify_keywords(text)
        type_pairs.append((g["type"], pred))
        base_pairs.append((g["type"], base))
        if pred != g["type"]:
            type_fail.append({"file": f, "gold": g["type"], "pred": pred, "error": r["error"] if r else "not indexed", "text": text[:300]})

        if g["type"] not in FACT_MODELS:
            continue
        schema_ok += bool(r and r["status"] == "indexed" and not r["error"])  # error = output invalid after retries
        facts = {x["field"]: x["value"] for x in conn.execute("SELECT field, value FROM facts WHERE file_id=?", (r["id"],))} if r else {}
        regex = baselines.extract_regex(g["type"], text)
        for field, gv in g["fields"].items():
            pv = facts.get(field) if pred == g["type"] else None  # wrong type means no usable facts
            ok, base_ok = same_value(pv, gv), same_value(regex.get(field), gv)
            field_hits["total"] += 1
            field_hits["sniff"] += ok
            field_hits["regex"] += base_ok
            if not ok:
                field_fail.append({"file": f, "field": field, "gold": gv, "pred": pv, "text": text[:300]})

    n = len(type_pairs)
    typed = sum(1 for g in gold.values() if g["type"] in FACT_MODELS)
    ms = [v for k, v in times.items()]
    save("document_type", {
        "n": n,
        "rows": [
            {"system": "Sniff (Qwen3-1.7B, prompted)", "accuracy": sum(g == p for g, p in type_pairs) / n, "macro_F1": macro_f1(type_pairs)},
            {"system": "Keyword rules (baseline)", "accuracy": sum(g == p for g, p in base_pairs) / n, "macro_F1": macro_f1(base_pairs)},
        ],
        "latency_ms": latency(ms),
        "confusion": {
            "labels": list(DOC_TYPES),
            "matrix": [[sum(1 for g, p in type_pairs if g == a and p == b) for b in DOC_TYPES] for a in DOC_TYPES],
        },
    }, type_fail)
    save("fact_extraction", {
        "n": typed,
        "rows": [
            {"system": "Sniff (Qwen3-1.7B, JSON schema)", "field_accuracy": field_hits["sniff"] / field_hits["total"], "schema_valid": schema_ok / typed},
            {"system": "Regex rules (baseline)", "field_accuracy": field_hits["regex"] / field_hits["total"], "schema_valid": 1.0},
        ],
        "fields": field_hits["total"],
    }, field_fail)


if __name__ == "__main__":
    main()
