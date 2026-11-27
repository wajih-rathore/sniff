"""Fast indexing: types without the chat model, fields from rules first, a candidate-line window for the rest.

    uv run python ../eval/run_fastindex.py prepare --split dev --synth <folder with labels.jsonl>
    uv run python ../eval/run_fastindex.py rules   --split dev     # rules-only fields, per type and field
    uv run python ../eval/run_fastindex.py types   --split dev     # classifier accuracy and abstain rate
    uv run python ../eval/run_fastindex.py guess   --split dev     # where it abstains: is its top guess right?
    uv run python ../eval/run_fastindex.py window  --split dev     # gold values inside the candidate-line window
    uv run python ../eval/run_fastindex.py run --split dev --mode today|fast [--llm-url URL --llm-model NAME]
    uv run python ../eval/run_fastindex.py run --split dev --mode off   # AI off, as on a new install: no model

`prepare` runs phase 1 (read, OCR, chunk, embed) once per document into .sniff/fast-<split>.db, so the queue holds
each document's pages and layout boxes. The other steps work from that copy and never read the files again; `run`
copies it and times phase 2 (type and facts) per document, which is the model time this work is about.
"""
import argparse
import base64
import json
import os
import shutil
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from common import ROOT, RESULTS, same_value, stay_awake

from sniff import db, verify
from sniff.ai import rules, typer
from sniff.ingest import scan
from sniff.schemas import FIELDS

DBS = ROOT / ".sniff"


def gold_rows(synth: Path, split: str) -> list[dict]:
    rows = [json.loads(x) for x in (synth / "labels.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r["split"] == split]


def cache_path(split: str) -> Path:
    return DBS / f"fast-{split}.db"


def prepare(synth: Path, split: str, label_text: bool = False) -> None:
    """Phase 1 for every labelled document of the split, with its gold type and fields saved next to it.
    label_text: take each page's text from the generator's labels instead of reading the file (no OCR). Only for
    training the type classifier, where speed matters more than OCR noise; never for scoring."""
    from sniff.ingest import readers
    path = cache_path(split)
    conn = db.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS gold(file_id INTEGER PRIMARY KEY, file TEXT, type TEXT, fields TEXT, format TEXT, read_ms INTEGER)")
    done = {r[0] for r in conn.execute("SELECT file FROM gold")}
    rows = gold_rows(synth, split)
    for i, g in enumerate(rows, 1):
        if g["file"] in done:
            continue
        p = (synth / g["file"]).resolve()
        if label_text:
            readers.read_layout = lambda path, text=g["text"]: ([(1, text)], {})
        t = time.perf_counter()
        fid = scan.index_text(conn, p, scan.sha256(p))
        ms = round((time.perf_counter() - t) * 1000)
        conn.execute("INSERT INTO gold VALUES (?,?,?,?,?,?)", (fid, g["file"], g["type"], json.dumps(g["fields"]), g["format"], ms))
        conn.commit()
        print(f"[{i}/{len(rows)}] {g['file']} {ms} ms", flush=True)


def docs(split: str, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Each prepared document: gold labels, pages and boxes as phase 2 gets them, and its chunk vectors."""
    conn = conn or db.connect(cache_path(split))
    out = []
    for g in conn.execute("SELECT g.*, q.pages, q.boxes FROM gold g LEFT JOIN queue q ON q.file_id=g.file_id ORDER BY g.file_id"):
        out.append({"file_id": g["file_id"], "file": g["file"], "type": g["type"], "fields": json.loads(g["fields"]), "format": g["format"],
                    "pages": [tuple(p) for p in json.loads(g["pages"] or "[]")],
                    "boxes": {int(k): [tuple(b) for b in v] for k, v in json.loads(g["boxes"] or "{}").items()}})
    return out


def stored(doc_type: str, printed: dict) -> dict:
    """Printed values as the facts table stores them (parsed by code), so they compare with the gold labels."""
    out = {}
    for f, raw in printed.items():
        v = verify.parse(FIELDS[doc_type][f].kind, raw)
        if v is not None:
            out[f] = verify.stored(v)
    return out


def score_fields(rows: list[tuple[str, dict, dict]]) -> dict:
    """rows: (type, gold fields, predicted fields). Accuracy counts every gold field, null ones too (a missing field
    must stay empty); filled = share of fields with a value; precision = right values among the filled ones."""
    per_type, per_field = defaultdict(Counter), defaultdict(Counter)
    for t, gold, pred in rows:
        for f, gv in gold.items():
            pv = pred.get(f)
            ok = same_value(pv, gv)
            for c in (per_type[t], per_field[f"{t}.{f}"], per_type["all"]):
                c["n"] += 1
                c["ok"] += ok
                c["filled"] += pv is not None
                c["filled_ok"] += pv is not None and ok
                c["gold"] += gv is not None
    div = lambda a, b: round(a / b, 3) if b else None
    view = lambda c: {"n": c["n"], "accuracy": div(c["ok"], c["n"]), "filled": div(c["filled"], c["n"]),
                      "precision": div(c["filled_ok"], c["filled"])}
    return {"all": view(per_type.pop("all")), "per_type": {t: view(c) for t, c in sorted(per_type.items())},
            "per_field": {f: view(c) for f, c in sorted(per_field.items())}}


def rules_only(split: str) -> None:
    """Fields from the rules alone, given the right type: no model at all."""
    rows, times, fails = [], [], []
    for d in docs(split):
        if d["type"] not in FIELDS:
            continue
        t = time.perf_counter()
        pred = stored(d["type"], rules.extract(d["type"], d["pages"], d["boxes"]))
        times.append((time.perf_counter() - t) * 1000)
        rows.append((d["type"], d["fields"], pred))
        fails += [{"file": d["file"], "format": d["format"], "field": f"{d['type']}.{f}", "gold": gv, "pred": pred.get(f)}
                  for f, gv in d["fields"].items() if not same_value(pred.get(f), gv)]
    out = {"split": split, "docs": len(rows), **score_fields(rows), "ms_per_doc_p50": round(sorted(times)[len(times) // 2])}
    save(f"fastindex_rules_{split}", out, fails)


def typed_docs(split: str):
    """(docs, unit vectors, gold class ids) for the type classifier."""
    conn = db.connect(cache_path(split))
    ds = [d for d in docs(split, conn)]
    vecs = [typer.doc_vector(conn, d["file_id"]) for d in ds]
    keep = [i for i, v in enumerate(vecs) if v is not None]
    return [ds[i] for i in keep], np.stack([vecs[i] for i in keep])


def train_types(split: str = "train", folds: int = 5) -> None:
    """Train the classifier on the training split and write backend/sniff/ai/type_weights.py. The threshold is the
    lowest one at which the out-of-fold decisions on the training split are at least 99% right."""
    ds, x = typed_docs(split)
    classes = sorted({d["type"] for d in ds})
    y = np.array([classes.index(d["type"]) for d in ds])
    texts = ["\n".join(t for _, t in d["pages"]) for d in ds]
    oof = np.zeros((len(ds), len(classes)), dtype=np.float32)
    for k in range(folds):  # fold k: every folds-th document, so each fold has every type
        test = np.arange(len(ds)) % folds == k
        w, b = typer.train(x[~test], y[~test], len(classes))
        oof[test] = typer.probabilities(x[test], w, b)
    grid = {}
    for th in np.arange(0.30, 0.96, 0.05):
        picked = [(typer.decide(oof[i], classes, texts[i], th), classes[y[i]]) for i in range(len(ds))]
        took = [(p, g) for p, g in picked if p]
        grid[round(float(th), 2)] = {"answered": round(len(took) / len(ds), 3), "right": round(sum(p == g for p, g in took) / max(len(took), 1), 3)}
    threshold = min((th for th, r in grid.items() if r["right"] >= 0.99), default=0.95)
    w, b = typer.train(x, y, len(classes))
    out = ROOT / "backend" / "sniff" / "ai" / "type_weights.py"
    out.write_text(
        f'"""Written by eval/run_fastindex.py train-types from the {split} split ({len(ds)} documents). Do not edit."""\n'
        f"CLASSES = {classes!r}\nTHRESHOLD = {threshold}\nBIAS = {[round(float(v), 6) for v in b]!r}\n"
        f"# float32, {len(classes)} x {x.shape[1]}, row by row\nWEIGHTS = {base64.b64encode(w.astype(np.float32).tobytes()).decode()!r}\n",
        encoding="utf-8")
    print(json.dumps({"docs": len(ds), "classes": len(classes), "threshold": threshold, "out_of_fold": grid}, indent=1))


def types(split: str) -> None:
    """The classifier on a prepared split: how often it answers, how often that answer is right, and the rules alone."""
    import importlib
    importlib.reload(typer)  # the weights train-types just wrote
    ds, x = typed_docs(split)
    rows, fails = [], []
    for d, v in zip(ds, x):
        text = "\n".join(t for _, t in d["pages"])
        probs = typer.probabilities(v)
        pred = typer.decide(probs, typer.CLASSES, text, typer.THRESHOLD)
        top = typer.CLASSES[int(np.argmax(probs))]
        rows.append((d["type"], pred, top, typer.rule_type(text) or "other"))
        if pred != d["type"]:
            fails.append({"file": d["file"], "format": d["format"], "gold": d["type"], "decided": pred, "classifier": top,
                          "p": round(float(probs.max()), 3), "rules": typer.rule_type(text)})
    n = len(rows)
    took = [r for r in rows if r[1]]
    out = {"split": split, "docs": n, "threshold": typer.THRESHOLD,
           "abstain_rate": round(1 - len(took) / n, 3), "answered_right": round(sum(g == p for g, p, _, _ in took) / max(len(took), 1), 3),
           "classifier_alone_accuracy": round(sum(g == t for g, _, t, _ in rows) / n, 3),
           "rules_alone_accuracy": round(sum(g == r for g, _, _, r in rows) / n, 3),
           "abstained_by_type": dict(Counter(g for g, p, _, _ in rows if not p)),
           "other_skipped": sum(1 for g, p, _, _ in rows if p == "other"), "other_docs": sum(1 for g, *_ in rows if g == "other")}
    save(f"fastindex_types_{split}", out, fails)


def guesses(split: str, floors=(0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)) -> None:
    """The documents the classifier abstains on: how often its top guess is right at each confidence floor. A guess of
    "other" counts as no guess: such a document stays unclassified either way."""
    ds, x = typed_docs(split)
    rows = []
    for d, v in zip(ds, x):
        text = "\n".join(t for _, t in d["pages"])
        probs = typer.probabilities(v)
        if typer.decide(probs, typer.CLASSES, text, typer.THRESHOLD) is None:
            top = int(np.argmax(probs))
            rows.append({"file": d["file"], "format": d["format"], "gold": d["type"], "guess": typer.CLASSES[top],
                         "p": round(float(probs[top]), 3), "rules": typer.rule_type(text)})
    at = lambda f: [r for r in rows if r["p"] >= f and r["guess"] != "other"]
    out = {"split": split, "docs": len(ds), "abstained": len(rows), "top_guess_right": sum(r["guess"] == r["gold"] for r in rows),
           "floors": {f: {"guessed": len(at(f)), "right": sum(r["guess"] == r["gold"] for r in at(f))} for f in floors}}
    save(f"fastindex_guess_{split}", out, rows)


def run(split: str, mode: str, name: str, limit: int | None = None) -> None:
    """Phase 2 on every prepared document, timed per document, through the real read_facts.
    today: the chat model classifies and reads every field from the whole document (the pipeline before this work).
    fast:  classifier first (model only on abstain), rules first (model only for missing required fields, on the window).
    off:   AI off, as on a new install: the classifier and the rules only, no model call.
    Records the model calls, their tokens and llama-server's own timings, then scores type and fields."""
    from sniff import config
    from sniff.ai import classify, extract, llm, server

    path = DBS / f"fast-{split}-{mode}-{name}.db"
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        p.unlink(missing_ok=True)
    src = sqlite3.connect(cache_path(split))
    src.execute(f"VACUUM INTO '{path}'")  # a clean copy, with the queue still full
    src.close()
    conn = db.connect(path)
    calls: list[dict] = []

    def counted_chat(messages, **kw):
        t0 = time.perf_counter()
        with server.using():
            r = llm.client().chat.completions.create(model=config.LLM_MODEL, messages=messages, temperature=0, **kw)
        t = (r.model_extra or {}).get("timings") or {}
        calls.append({"wall_s": round(time.perf_counter() - t0, 2), "prompt": r.usage.prompt_tokens, "out": r.usage.completion_tokens, "prompt_new": t.get("prompt_n"),
                      "prompt_ms": t.get("prompt_ms"), "gen_ms": t.get("predicted_ms")})
        return llm.clean(r.choices[0].message.content or "")

    if mode == "off":
        os.environ["SNIFF_AI"] = "off"  # overrides the saved provider (ai_settings.load)
    else:
        llm.chat = counted_chat
    if mode == "today":
        typer.classify = lambda conn, file_id, pages: None
        real = extract.extract
        extract.extract = lambda doc_type, pages, boxes=None: real(doc_type, pages, boxes, rules_first=False)
    ds = docs(split, conn)[:limit] if limit else docs(split, conn)
    per_doc, rows, type_rows, fails = [], [], [], []
    for i, d in enumerate(ds, 1):
        before = len(calls)
        t = time.perf_counter()
        scan.read_facts(conn, d["file_id"])
        conn.commit()
        ms = (time.perf_counter() - t) * 1000
        mine = calls[before:]
        r = conn.execute("SELECT doc_type, error FROM files WHERE id=?", (d["file_id"],)).fetchone()
        pred_type = r["doc_type"] or "other"
        facts = {f["field"]: f["value"] for f in conn.execute("SELECT field, value FROM facts WHERE file_id=?", (d["file_id"],))}
        type_rows.append((d["type"], pred_type))
        if d["type"] in FIELDS:
            rows.append((d["type"], d["fields"], facts if pred_type == d["type"] else {}))
            fails += [{"file": d["file"], "format": d["format"], "field": f"{d['type']}.{f}", "gold": gv, "pred": facts.get(f) if pred_type == d["type"] else None}
                      for f, gv in d["fields"].items() if not same_value(facts.get(f) if pred_type == d["type"] else None, gv)]
        if pred_type != d["type"]:
            fails.append({"file": d["file"], "format": d["format"], "type": d["type"], "pred_type": pred_type, "error": r["error"]})
        per_doc.append({"file": d["file"], "ms": round(ms), "calls": len(mine), "prompt": sum(c["prompt"] for c in mine),
                        "prompt_new": sum(c["prompt_new"] or 0 for c in mine), "out": sum(c["out"] for c in mine),
                        "prompt_ms": round(sum(c["prompt_ms"] or 0 for c in mine)), "gen_ms": round(sum(c["gen_ms"] or 0 for c in mine))})
        print(f"[{i}/{len(ds)}] {d['file']} {pred_type} {round(ms)} ms, {len(mine)} model calls", flush=True)
    secs = sorted(x["ms"] / 1000 for x in per_doc)
    pct = lambda q: round(secs[min(len(secs) - 1, int(round(q * (len(secs) - 1))))], 2)
    out = {"split": split, "mode": mode, "model": name, "docs": len(per_doc),
           "type_accuracy": round(sum(g == p for g, p in type_rows) / len(type_rows), 3),
           **{f"fields_{k}": v for k, v in score_fields(rows)["all"].items()},
           "seconds_per_doc": {"mean": round(sum(secs) / len(secs), 2), "p50": pct(.5), "p95": pct(.95)},
           "model_calls_per_doc": round(sum(x["calls"] for x in per_doc) / len(per_doc), 2),
           "docs_without_model": round(sum(x["calls"] == 0 for x in per_doc) / len(per_doc), 3),
           "tokens_per_doc": {k: round(sum(x[k] for x in per_doc) / len(per_doc)) for k in ("prompt", "prompt_new", "out")},
           "model_ms_per_doc": {k: round(sum(x[k] for x in per_doc) / len(per_doc)) for k in ("prompt_ms", "gen_ms")},
           "server_starts": server.STATS["starts"], "server_start_s": server.STATS["last_start_s"],
           "first_answer_s": calls[0]["wall_s"] if calls else None, "per_doc": per_doc}
    save(f"fastindex_run_{split}_{mode}_{name}", out, fails)


def gold_value(kind: str, gv):
    """A gold label as verify's typed value, so check_anchor can look for it in any printed form."""
    from datetime import date
    return date.fromisoformat(gv) if kind == "date" else float(gv) if kind in ("money", "gpa") else gv


def window_recall(split: str, before: int = 0, after: int = 1) -> None:
    """How often a gold value that is on the page is also inside its field's candidate-line window."""
    c, per_field, fails, sizes = Counter(), defaultdict(Counter), [], []
    for d in docs(split):
        if d["type"] not in FIELDS:
            continue
        rows = verify.page_rows(d["pages"], d["boxes"])
        full = sum(len(r.text) + 1 for _, rs in rows for r in rs)
        for f, gv in d["fields"].items():
            if gv is None:
                continue
            kind = FIELDS[d["type"]][f].kind
            value = gold_value(kind, gv)
            if not verify.check_anchor(d["type"], f, kind, str(gv), value, rows)[0]:
                continue  # not on the page at all (OCR lost it): no window can help
            win = rules.window(d["type"], d["pages"], d["boxes"], [f], before, after)
            sizes.append(sum(len(t) for _, t in win) / full)
            inside = verify.check_anchor(d["type"], f, kind, str(gv), value, verify.page_rows(win, {}))[0]
            for k in (c, per_field[f"{d['type']}.{f}"]):
                k["n"] += 1
                k["in"] += inside
            c["whole"] += win == d["pages"]  # no label found: the window is the whole document
            if not inside:
                fails.append({"file": d["file"], "field": f"{d['type']}.{f}", "gold": gv})
    out = {"split": split, "before": before, "after": after, "values_on_page": c["n"], "recall": round(c["in"] / c["n"], 3),
           "whole_document_windows": c["whole"],
           "window_share_of_text": round(sum(sizes) / len(sizes), 3),
           "per_field": {k: round(v["in"] / v["n"], 3) for k, v in sorted(per_field.items()) if v["in"] < v["n"]}}
    save(f"fastindex_window_{split}", out, fails)


def save(name: str, out: dict, fails: list) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (RESULTS / f"{name}_failures.json").write_text(json.dumps(fails, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("per_field", "per_doc")}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "rules", "train-types", "types", "guess", "window", "run"])
    ap.add_argument("--mode", choices=["today", "fast", "off"], default="fast")
    ap.add_argument("--llm-url", help="another OpenAI-compatible chat server, e.g. http://127.0.0.1:8092/v1")
    ap.add_argument("--llm-model", default="qwen3-1.7b")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--label-text", action="store_true", help="prepare: text from the labels, no OCR (training only)")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--synth", type=Path, default=ROOT / "data" / "synth" / "out")
    a = ap.parse_args()
    stay_awake()
    if a.step == "prepare":
        prepare(a.synth.resolve(), a.split, a.label_text)
    elif a.step == "rules":
        rules_only(a.split)
    elif a.step == "train-types":
        train_types(a.split if a.split != "dev" else "train")
    elif a.step == "types":
        types(a.split)
    elif a.step == "guess":
        guesses(a.split)
    elif a.step == "window":
        window_recall(a.split)
    elif a.step == "run":
        from common import use_llm
        use_llm(a.llm_url, a.llm_model)
        run(a.split, a.mode, "ai-off" if a.mode == "off" else a.llm_model, a.limit)
