"""Router (tool selection) and Ask (answer correctness, citations, abstention), overall and by question category,
against keyword-rule routing and plain RAG without the facts table.

    uv run python ../eval/run_ask.py              # all questions in data/eval/ask.jsonl, on .sniff/eval.db
    uv run python ../eval/run_ask.py --limit 10   # a quick check (round-robin over categories)

Results: router.json (LLM router vs keyword rules), ask.json (Sniff vs plain RAG) and ask_by_category.json."""
import argparse
import re
from collections import defaultdict
from datetime import date

import baselines
from common import Timer, db_path, jsonl, latency, norm, rel, save, stay_awake, stratified

from sniff import ai_settings, ask, db

TODAY = date(2026, 11, 22)  # the generator's reference date, so "next to expire" has a fixed answer


def category(g: dict) -> str:
    """ask.jsonl rows carry a category; older rows without one are grouped by what we can tell for sure."""
    return g.get("category") or ("unanswerable" if g["answer_type"] == "none" else f"uncategorised ({g['route']})")


def abstained(answer) -> bool:
    """Refused: search found nothing, or the facts route matched no documents (no facts, no sources)."""
    return answer.route == "none" or (answer.route == "facts" and not answer.facts and not answer.sources)


def correct(answer, gold: dict) -> bool:
    text = answer.answer
    if gold["answer_type"] == "none":
        return abstained(answer)
    if abstained(answer):
        return False
    if gold["answer_type"] == "number":
        nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]
        return any(abs(n - float(gold["answer"])) <= max(0.01, 0.005 * float(gold["answer"])) for n in nums)
    if gold["answer_type"] == "list":
        return all(norm(x) in norm(text) for x in re.split(r"[;,]\s*", str(gold["answer"])) if x.strip())
    return norm(gold["answer"]) in norm(text)  # dates (ISO) and short text


def cited_ok(answer, gold: dict) -> bool:
    """At least one cited source is a gold source (only scored for questions that have gold sources)."""
    return bool({rel(s.path) for s in answer.sources} & set(gold["sources"]))


def main(limit: int | None):
    conn = db.connect(db_path("test"))
    qs = jsonl("ask.jsonl")
    for g in qs:  # the generator appends "Today is 2026-11-22."; we pass TODAY to the code instead
        g["q"] = re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", g["q"]).strip()
        g["type"] = category(g)  # stratified() groups by "type"
    qs = stratified(qs, limit) if limit else qs

    by_cat = defaultdict(lambda: defaultdict(int))
    ms, fails, route_fail = [], [], []
    for g in qs:
        k = by_cat[g["type"]]
        with Timer() as t:
            a = ask.ask(conn, g["q"], TODAY)
        ms.append(t.ms)
        chosen = "facts" if a.route == "facts" else "search"  # an invalid facts query falls back to search
        kw = baselines.route_keywords(g["q"])
        base = ask.answer_from_search(conn, g["q"])  # plain RAG: always retrieve and let the LLM answer
        ok, has_src = correct(a, g), bool(g["sources"])
        k["n"] += 1
        k["route_ok"] += chosen == g["route"]
        k["kw_route_ok"] += kw == g["route"]
        k[f"gold_{g['route']}"] += 1
        k[f"route_ok_{g['route']}"] += chosen == g["route"]
        k[f"kw_ok_{g['route']}"] += kw == g["route"]
        k["ok"] += ok
        k["rag_ok"] += correct(base, g)
        k["with_sources"] += has_src
        k["cite_ok"] += has_src and cited_ok(a, g)
        k["rag_cite_ok"] += has_src and cited_ok(base, g)
        k["answerable"] += g["answer_type"] != "none"
        k["abstained_wrongly"] += g["answer_type"] != "none" and abstained(a)
        k["unanswerable"] += g["answer_type"] == "none"
        k["abstained"] += g["answer_type"] == "none" and abstained(a)
        k["rag_abstained"] += g["answer_type"] == "none" and abstained(base)
        k["conflicts_flagged"] += bool(getattr(a, "conflicts", None))
        if chosen != g["route"]:
            route_fail.append({"q": g["q"], "category": g["type"], "gold": g["route"], "pred": chosen, "query": a.query})
        if not ok:
            fails.append({"q": g["q"], "category": g["type"], "gold": g["answer"], "got": a.answer, "route": a.route,
                          "query": a.query, "sources": [rel(s.path) for s in a.sources],
                          **{f: getattr(a, f) for f in ("counted", "skipped_duplicates", "unreviewed", "conflicts") if hasattr(a, f)}})

    tot = defaultdict(int)
    for k in by_cat.values():
        for key, v in k.items():
            tot[key] += v
    div = lambda a, b: round(a / b, 3) if b else None
    n = tot["n"]
    off = ai_settings.current()["provider"] == "off"  # no model: rules route, nothing is written, so no RAG baseline
    save("router", {"n": n, "rows": [
        {"system": "Sniff router (rules only, AI off)" if off else "Sniff router (LLM fills a query template)", "tool_selection_accuracy": div(tot["route_ok"], n),
         "facts_recall": div(tot["route_ok_facts"], tot["gold_facts"]), "search_recall": div(tot["route_ok_search"], tot["gold_search"])},
        {"system": "Keyword rules (baseline)", "tool_selection_accuracy": div(tot["kw_route_ok"], n),
         "facts_recall": div(tot["kw_ok_facts"], tot["gold_facts"]), "search_recall": div(tot["kw_ok_search"], tot["gold_search"])},
    ]}, route_fail)
    save("ask", {"n": n, "rows": [
        {"system": "Sniff (facts table, AI off: no written answers)" if off else "Sniff (facts table + RAG)", "correct": div(tot["ok"], n), "citation_correct": div(tot["cite_ok"], tot["with_sources"]),
         "abstention_on_unanswerable": div(tot["abstained"], tot["unanswerable"]),
         "false_abstention": div(tot["abstained_wrongly"], tot["answerable"])},
    ] + ([] if off else [
        {"system": "Plain RAG (baseline)", "correct": div(tot["rag_ok"], n), "citation_correct": div(tot["rag_cite_ok"], tot["with_sources"]),
         "abstention_on_unanswerable": div(tot["rag_abstained"], tot["unanswerable"])},
    ]), "latency_ms": latency(ms)}, fails)
    save("ask_by_category", {"n": n, "rows": [
        {"category": c, "n": k["n"], "Sniff_correct": div(k["ok"], k["n"]), **({} if off else {"plain_RAG_correct": div(k["rag_ok"], k["n"])}),
         "citation_correct": div(k["cite_ok"], k["with_sources"]), "router_accuracy": div(k["route_ok"], k["n"]),
         "keyword_router_accuracy": div(k["kw_route_ok"], k["n"]),
         **({"conflicts_flagged": div(k["conflicts_flagged"], k["n"])} if c == "conflict" else {})}
        for c, k in sorted(by_cat.items())
    ]}, [])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only this many questions, round-robin over categories")
    stay_awake()
    main(ap.parse_args().limit)
