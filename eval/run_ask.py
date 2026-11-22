"""Router (tool selection) and Ask (answer correctness, citations, abstention) vs plain RAG without the facts table."""
import re
from datetime import date

from common import Timer, jsonl, latency, norm, rel, save

from sniff import ask, db

TODAY = date(2026, 11, 22)  # the generator's reference date, so "next to expire" has a fixed answer


def correct(answer, gold: dict) -> bool:
    text = answer.answer
    if gold["answer_type"] == "none":
        return answer.route == "none"
    if answer.route == "none":
        return False
    if gold["answer_type"] == "number":
        nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]
        return any(abs(n - float(gold["answer"])) <= max(0.01, 0.005 * float(gold["answer"])) for n in nums)
    if gold["answer_type"] == "list":
        return all(norm(x) in norm(text) for x in gold["answer"].split(";"))
    return norm(gold["answer"]) in norm(text)  # dates (ISO) and short text


def cited_ok(answer, gold: dict) -> bool:
    return not gold["sources"] or bool({rel(s.path) for s in answer.sources} & set(gold["sources"]))


def main():
    conn = db.connect()
    qs = jsonl("ask.jsonl")
    for g in qs:  # the generator appends "Today is 2026-11-22."; we pass TODAY to the code instead
        g["q"] = re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", g["q"]).strip()
    route_ok, sniff_ok, rag_ok, sniff_cite, rag_cite, ms, failures, route_fail = 0, 0, 0, 0, 0, [], [], []
    for g in qs:
        with Timer() as t:
            r = ask.route(g["q"])
            a = ask.answer_from_facts(conn, g["q"], r, TODAY) if ask.valid(r) else ask.answer_from_search(conn, g["q"])
        ms.append(t.ms)
        chosen = "facts" if ask.valid(r) else "search"
        route_ok += chosen == g["route"]
        if chosen != g["route"]:
            route_fail.append({"q": g["q"], "gold": g["route"], "pred": r.model_dump()})
        ok = correct(a, g)
        sniff_ok += ok
        sniff_cite += cited_ok(a, g)
        if not ok:
            failures.append({"q": g["q"], "gold": g["answer"], "got": a.answer, "route": a.route, "query": a.query})
        base = ask.answer_from_search(conn, g["q"])  # plain RAG: always retrieve and let the LLM answer
        rag_ok += correct(base, g)
        rag_cite += cited_ok(base, g)
    n = len(qs)
    save("router", {"n": n, "rows": [{"system": "Sniff router (Qwen3-1.7B tool calling)", "tool_selection_accuracy": route_ok / n}]}, route_fail)
    save("ask", {
        "n": n,
        "rows": [
            {"system": "Sniff (facts table + RAG)", "correct": sniff_ok / n, "citation_correct": sniff_cite / n},
            {"system": "Plain RAG (baseline)", "correct": rag_ok / n, "citation_correct": rag_cite / n},
        ],
        "latency_ms": latency(ms),
    }, failures)


if __name__ == "__main__":
    main()
