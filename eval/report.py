"""The evaluation report for the graders: one static HTML file from eval/results/*.json. No model, no JavaScript.

    uv run python ../eval/report.py                                  # -> docs/eval-report.html
    uv run python ../eval/report.py --results DIR --out FILE

It shows a headline table (each component against its baseline, with n), the model comparison, latency and memory,
and the failure groups from error_groups.json. Every number is read from the JSON files; nothing is computed here
except picking rows and rounding."""
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("_failures", "_runs", "index_times", ".times")


def load(results: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(results.glob("*.json")):
        if not any(s in p.name for s in SKIP):
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


def fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, (list, dict)):
        return html.escape(json.dumps(v, ensure_ascii=False)[:200])
    return html.escape(str(v))


def table(rows: list[dict], cols: list[str] | None = None, cls: str = "") -> str:
    if not rows:
        return "<p class=muted>No rows.</p>"
    cols = cols or list(dict.fromkeys(k for r in rows for k in r))
    head = "".join(f"<th>{html.escape(c.replace('_', ' '))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{fmt(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows)
    return f'<div class=scroll><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def pick(rows: list[dict], want: str, field: str = "system") -> dict | None:
    return next((r for r in rows if want.lower() in str(r.get(field, "")).lower()), None)


def first_sniff(rows: list[dict]) -> dict | None:
    return next((r for r in rows if "baseline" not in str(r.get("system", "")).lower()), None)


# ---------------------------------------------------------------- headline: one line per component (and set)
def headline(R: dict) -> list[dict]:
    out = []

    def add(component, set_, n, metric, sniff, base_name, base, better="higher"):
        out.append({"component": component, "set": set_, "n": n, "metric": f"{metric} ({better} is better)",
                    "Sniff": sniff, "baseline": base_name, "baseline value": base})

    for key, set_ in (("ocr", "test"), ("ocr_hard", "hard")):
        if d := R.get(key):
            add("OCR", set_, d["n"], "character error rate", pick(d["rows"], "rapidocr")["CER"], "Tesseract defaults",
                pick(d["rows"], "tesseract")["CER"], "lower")
    for key, comp, metric, col in (("document_type", "Document type", "accuracy", "accuracy"),
                                   ("fact_extraction", "Fact extraction", "field accuracy", "field_accuracy")):
        if d := R.get(key):
            for s in dict.fromkeys(r["set"] for r in d["rows"]):
                rows = [r for r in d["rows"] if r["set"] == s]
                sn, bl = first_sniff(rows), pick(rows, "baseline")
                add(comp, s, sn["n"], metric, sn[col], bl["system"].replace(" (baseline)", ""), bl[col])
    if d := R.get("search"):
        sn, bl = first_sniff(d["rows"]), pick(d["rows"], "baseline")
        k = next(c for c in sn if c.startswith("Recall"))
        add("Search", "test", d["n"], k, sn[k], "File-name search", bl[k])
        bm = pick(d["rows"], "Keyword only")
        add("Search", "test", d["n"], k, sn[k], "BM25 only", bm[k])
    if d := R.get("embeddings"):
        a, b = pick(d["rows"], "hybrid BM25 + embeddings) [all-minilm"), pick(d["rows"], "hybrid BM25 + embeddings) [embeddinggemma")
        if a and b:
            k = next(c for c in a if c.startswith("Recall"))
            add("Embeddings (hybrid search)", "test", d["n"], k, a[k], "EmbeddingGemma instead of all-MiniLM", b[k])
    if d := R.get("router"):
        sn, bl = first_sniff(d["rows"]), pick(d["rows"], "baseline")
        add("Router", "ask questions", d["n"], "tool selection accuracy", sn["tool_selection_accuracy"], "Keyword rules", bl["tool_selection_accuracy"])
    if d := R.get("ask"):
        sn, bl = first_sniff(d["rows"]), pick(d["rows"], "baseline")
        add("Ask", "ask questions", d["n"], "answer correct", sn["correct"], "Plain RAG", bl["correct"])
    if d := R.get("robustness"):
        a = pick(d["rows"], "all", "variant")
        if a:
            add("Robustness", "hard vs clean twin", a["n"], "field accuracy", a["hard_field_accuracy"], "Same documents, clean", a["clean_field_accuracy"])
    if d := R.get("prompt_injection"):
        a = pick(d["rows"], "all", "kind")
        if a:
            out.append({"component": "Prompt injection", "set": "injection", "n": a["n"], "metric": "attack success rate (lower is better)",
                        "Sniff": a["attack_success_rate"], "baseline": "–", "baseline value": None})
    if d := R.get("mcp_vs_direct"):
        a, b = pick(d["rows"], "MCP"), pick(d["rows"], "plain file")
        if a and b:
            add("Agent tools", "ask questions", a["n"], "answer correct", a["correct"], "Same agent with plain file tools", b["correct"])
    return out


# ---------------------------------------------------------------- sections
def section(title: str, body: str, note: str = "") -> str:
    return f"<section><h2>{html.escape(title)}</h2>{f'<p>{note}</p>' if note else ''}{body}</section>"


def details(R: dict) -> str:
    parts = []
    names = {"ocr": "OCR, synthetic scans and photos", "ocr_hard": "OCR, hard set", "document_type": "Document type",
             "fact_extraction": "Fact extraction", "search": "Search", "embeddings": "Embedding models", "router": "Router",
             "ask": "Ask", "ask_by_category": "Ask by question category", "robustness": "Robustness",
             "prompt_injection": "Prompt injection", "mcp_vs_direct": "Agent with Sniff's MCP tools vs plain file tools",
             "human_ratings": "Human ratings"}
    for key, title in names.items():
        d = R.get(key)
        if not d:
            continue
        body = table(d["rows"])
        if lat := d.get("latency_ms"):
            body += f"<p class=muted>Latency per call: p50 {lat['p50']} ms, p95 {lat['p95']} ms.</p>"
        if key == "fact_extraction" and d.get("per_field"):
            for s, pf in d["per_field"].items():
                body += f"<h4>Field accuracy per field, {html.escape(s)}</h4>" + table([{"field": f, "accuracy": a} for f, a in pf.items()], cls="narrow")
        if key == "document_type" and d.get("confusion"):
            c = d["confusion"]
            rows = [{"gold \\ predicted": g, **{p: v or None for p, v in zip(c["labels"], row)}} for g, row in zip(c["labels"], c["matrix"]) if any(row)]
            body += "<h4>Confusion matrix (first set in the file)</h4>" + table(rows, cls="small")
        if key == "robustness" and d.get("by_effect"):
            body += "<h4>By effect</h4>" + table([{"effect": e, **v} for e, v in d["by_effect"].items()], cls="narrow")
        if key == "mcp_vs_direct" and d.get("by_category"):
            body += "<h4>By category</h4>" + table([{"category": c, **v} for c, v in d["by_category"].items()])
        n = d.get("n")
        parts.append(f"<h3>{html.escape(title)}{f' <span class=muted>(n = {n})</span>' if n else ''}</h3>{body}")
    return "".join(parts)


def models(R: dict) -> str:
    parts = []
    for key, note in (("model_comparison", ""), ("model_comparison_8b", "")):
        d = R.get(key)
        if not d:
            continue
        cols = ["system", "set", "n", "type_accuracy", "type_macro_F1", "field_accuracy", "schema_valid", "verified_precision",
                "verified_recall", "needs_review_rate", "latency_p50_ms", "latency_p95_ms"]
        title = d.get("title") or ("Documents per set: " + str(d.get("limit_per_set") or "all"))
        parts.append(f"<h3>{html.escape(title)}</h3>" + table(d["rows"], cols) + (f"<p class=muted>{html.escape(d['note'])}</p>" if d.get("note") else ""))
    return "".join(parts) or "<p class=muted>Not run.</p>"


def system(R: dict) -> str:
    d = R.get("system")
    if not d:
        return "<p class=muted>Not run.</p>"
    stages = [r for r in d["rows"] if "calls" in r]
    mem = [r for r in d["rows"] if "peak_working_set_MB" in r]
    s = d.get("setup", {})
    note = (f"{d['n']} documents indexed and {d['questions']} questions asked; servers {html.escape(str(s.get('mode')))}, "
            f"threads {html.escape(str(s.get('threads')))}. Budget for an 8 GB laptop: {d['budget_MB']:.0f} MB; "
            f"peak working set within budget: {fmt(d.get('peak_working_set_within_budget'))}. Index errors: {d.get('index_errors')}.")
    extra = "".join(f"<p class=muted>{html.escape(k.replace('_', ' '))}: {fmt(v)}</p>" for k, v in d.get("rates", {}).items())
    return f"<p>{note}</p>{extra}<h4>Latency per stage</h4>{table(stages)}<h4>Peak memory per process</h4>{table(mem)}"


def failures(R: dict) -> str:
    d = R.get("error_groups")
    if not d:
        return "<p class=muted>No error_groups.json yet.</p>"
    parts = []
    for c in d["components"]:
        rows = [{"cause": g["cause"], "count": g["count"], "example": g["example"], "planned fix": g["fix"]} for g in c["groups"]]
        parts.append(f"<h3>{html.escape(c['title'])} <span class=muted>({c['failures']} failures)</span></h3>" + table(rows, cls="fail"))
    return "".join(parts)


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6b66;--line:#dedbd2;--head:#f0ede4;--accent:#2f5d8a}
@media (prefers-color-scheme:dark){:root{--bg:#171716;--fg:#e8e6e0;--muted:#9a978f;--line:#34332f;--head:#22221f;--accent:#8fb6dd}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 8px;border-bottom:2px solid var(--line);padding-bottom:4px}
h3{font-size:16px;margin:24px 0 6px}h4{font-size:14px;margin:14px 0 4px;color:var(--muted)}
.muted{color:var(--muted);font-weight:normal}a{color:var(--accent)}
.scroll{overflow-x:auto}table{border-collapse:collapse;margin:6px 0 10px;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}th{background:var(--head);font-weight:600}
table.small td,table.small th{padding:2px 5px;font-size:12px}table.fail td:nth-child(3){max-width:420px;font-size:12.5px}
nav a{margin-right:14px}
"""


def page(R: dict) -> str:
    when = max((d.get("updated", "") for d in R.values()), default="")
    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in (("headline", "Headline"), ("models", "Models"), ("system", "Latency and memory"),
                                                        ("details", "Details"), ("failures", "Failures")))
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Sniff evaluation</title><style>{CSS}</style></head><body>
<h1>Sniff evaluation</h1>
<p class=muted>Generated by eval/report.py from eval/results/*.json. Latest result: {html.escape(when)}. Report built {datetime.now():%Y-%m-%d %H:%M}.
Rates are shares from 0 to 1. Test documents never appear in a prompt or a few-shot example; tuning used the dev split only.</p>
<nav>{nav}</nav>
<div id=headline>{section("Headline: Sniff against its baseline", table(headline(R)))}</div>
<div id=models>{section("Model comparison", models(R), "The same documents, indexed through the same pipeline with each chat model.")}</div>
<div id=system>{section("Latency and memory", system(R))}</div>
<div id=details>{section("Details per component", details(R))}</div>
<div id=failures>{section("Failure groups", failures(R), "Each failure is put in one group by code rules (eval/error_groups.py). The counts are computed, not estimated.")}</div>
</body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=ROOT / "eval" / "results")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "eval-report.html")
    a = ap.parse_args()
    R = load(a.results)
    a.out.write_text(page(R), encoding="utf-8")
    print(f"{len(R)} result files -> {a.out}")
