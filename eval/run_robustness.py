"""Robustness (A4): how much accuracy does Sniff lose when the same document is a photo, a low-resolution scan,
handwriting or cut off? Each hard document is compared with its clean twin, the synthetic test document it was made from.

    uv run python ../eval/run_robustness.py

Needs the test and hard databases from run_index.py (`run_index.py` and `run_index.py --set hard`). It calls no model.
Both sides are scored by run_extraction.score, so the numbers mean the same as in the extraction results.

A drop is clean accuracy minus hard accuracy, on the same documents. Cut-off documents lose some fields for real (the
printed value is on the missing part), so for them both sides are scored only on the fields that survive the cut, and the
fields that were cut are reported separately: `cut_fields_filled` is the share of them where Sniff stored a value anyway
(an invented value, which is worse than a missing one).
Results: eval/results/robustness.json (rows per corruption type, by_effect per effect tag) and robustness_failures.json,
each hard failure marked with whether its clean twin failed on the same thing."""
from collections import defaultdict

import run_extraction as rx
from common import db_path, labels, rel, save

from sniff import db

VARIANTS = ["photo", "lowres", "handwriting", "cutoff"]


def build_pairs(hard: list[dict], test: list[dict], have_hard: set, have_test: set) -> list[tuple[dict, dict, list[str]]]:
    """(hard label, clean twin label, cut fields), both scored on the fields that survive the cut."""
    twins = {t["file"]: t for t in test}
    pairs = []
    for h in hard:
        t = twins.get(h["source"])
        if not t or h["file"] not in have_hard or t["file"] not in have_test:
            continue
        cut = [k for k, v in t["fields"].items() if v is not None and h["fields"].get(k) is None]
        keep = lambda fields: {k: v for k, v in fields.items() if k not in cut}
        pairs.append(({**h, "fields": keep(h["fields"])}, {**t, "fields": keep(t["fields"]), "variant": h["variant"], "effects": h["effects"]}, cut))
    return pairs


def drop(clean, hard):
    return round(clean - hard, 3) if clean is not None and hard is not None else None


def cut_filled(conn, pairs) -> float | None:
    """Of the fields a cut removed, the share where Sniff stored a value anyway."""
    files = {rel(r["path"]): r for r in conn.execute("SELECT * FROM files")}
    total = filled = 0
    for h, _, cut in pairs:
        r = files.get(h["file"])
        stored = {f["field"] for f in conn.execute("SELECT field FROM facts WHERE file_id=?", (rx.owner(r),))} if r else set()
        total += len(cut)
        filled += sum(k in stored for k in cut)
    return round(filled / total, 3) if total else None


def compare(conn_hard, conn_test, pairs, tag: dict) -> tuple[dict, list[dict]]:
    """One row of numbers for a group of pairs, and the hard failures of the group."""
    h, c = [p[0] for p in pairs], [p[1] for p in pairs]
    mh, _, tf, ff = rx.score(conn_hard, h, {}, {**tag, "set": "hard"})
    mc, _, ctf, cff = rx.score(conn_test, c, {}, {**tag, "set": "test"})
    twin_failed = {(f["file"], f.get("field")) for f in ctf + cff}
    source = {p[0]["file"]: p[1]["file"] for p in pairs}
    fails = [{**f, "source": source[f["file"]], "clean_twin_also_failed": (source[f["file"]], f.get("field")) in twin_failed} for f in tf + ff]
    row = {"n": len(pairs), "clean_type_accuracy": mc["type_accuracy"], "hard_type_accuracy": mh["type_accuracy"],
           "type_drop": drop(mc["type_accuracy"], mh["type_accuracy"]),
           "clean_field_accuracy": mc["field_accuracy"], "hard_field_accuracy": mh["field_accuracy"],
           "field_drop": drop(mc["field_accuracy"], mh["field_accuracy"])}
    return row, fails


def main():
    for s in ("test", "hard"):
        if not db_path(s).exists():
            raise SystemExit(f"{db_path(s).name} not found: run run_index.py --set {s} first")
    conn_t, conn_h = db.connect(db_path("test")), db.connect(db_path("hard"))
    have = lambda conn: {rel(r["path"]) for r in conn.execute("SELECT path FROM files")}
    pairs = build_pairs(labels("hard"), labels("test"), have(conn_h), have(conn_t))
    if not pairs:
        raise SystemExit("no hard document has both itself and its clean twin indexed")

    rows, failures = [], []
    for v in VARIANTS + ["all"]:
        group = [p for p in pairs if v == "all" or p[0]["variant"] == v]
        if not group:
            continue
        row, fails = compare(conn_h, conn_t, group, {"variant": v})
        rows.append({"variant": v, **row, **({"cut_fields_filled": cut_filled(conn_h, group)} if v == "cutoff" else {})})
        if v != "all":
            failures += fails
    by_effect = defaultdict(list)
    for p in pairs:
        for e in p[0]["effects"] or []:
            by_effect[e].append(p)
    effects = {e: {k: v for k, v in compare(conn_h, conn_t, g, {})[0].items() if k in ("n", "type_drop", "field_drop")}
               for e, g in sorted(by_effect.items())}
    save("robustness", {"n": len(pairs), "rows": rows, "by_effect": effects}, failures)


if __name__ == "__main__":
    main()
