"""MCP tools or plain file reading? (agents and tool calling)

The same questions over the same documents, answered by the local chat model acting as a tool-using agent, in two
conditions:
  mcp     the agent gets Sniff's MCP tools (search_documents, ask, list_documents) through a real MCP client session to
          sniff.mcp_server over stdio. The index is the test database from run_index.py.
  direct  the agent gets list_files and read_file over the same folder of documents (data/synth/out/test). read_file
          returns the text with the same reader Sniff indexes with (OCR for scans and photos), so the only difference
          is what the agent has to do itself: find the document, and add up the numbers.

    uv run python ../eval/run_mcp_vs_direct.py                  # 60 questions, both conditions (hours on a laptop CPU)
    uv run python ../eval/run_mcp_vs_direct.py --n 6 --conditions direct
    uv run python ../eval/run_mcp_vs_direct.py --fresh          # forget earlier runs (a stopped run resumes by default)

Needs the chat server (8080, llama-server with --jinja, which tool calling needs) and the embedding server (8081), and
the test set indexed (run_index.py). The server needs room for the conversation: if you see context errors in the
failures, restart it with -np 1 and a bigger -c. Questions come from data/eval/ask.jsonl, without expiring_window (the
MCP tools take no reference date). The model sees questions and tool results only; test documents are never put in a
prompt by hand.

Metrics per condition: correctness (the same checks as run_ask.py; a refusal is a reply that says it could not find
the answer), retrieval success (the agent opened or was given a gold source document), source recall (share of the
gold sources it reached), steps (model calls) per question, tool-call success rate, p50/p95 latency, and prompt and
completion tokens per question. For mcp the tokens include the chat calls made inside Sniff's own tools (counted by
eval/mcp_launcher.py). The MCP server is started with every document type allowed, so ID cards are readable as in the
direct condition; ID numbers are still masked. Results: mcp_vs_direct.json, mcp_vs_direct_failures.json and the
per-question traces in mcp_vs_direct_runs.json."""
import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import run_ask
from common import RESULTS, ROOT, SETS, Timer, db_path, jsonl, latency, rel, save, stay_awake, stratified, use_llm

CATEGORIES = ["single_fact", "cross_doc_total", "semantic", "unanswerable", "duplicates", "conflict"]
CONDITIONS = {"mcp": "Agent with Sniff MCP tools", "direct": "Agent with plain file tools"}
SYSTEM = ("You answer questions about the user's own documents (bills, receipts, cards, certificates, statements, letters and notes). "
          "Use the tools to look things up and do not guess. Answer in one or two sentences with the figure, date or name asked for. "
          "If the documents do not contain the answer, say that you could not find it.")
REFUSAL = re.compile(r"couldn.?t find|could not find|cannot find|can.?t find|not found|no (matching )?(documents?|information|records?)|"
                     r"don.?t have|do not have|unable to|not (mentioned|available|in your)|no mention", re.I)

DIRECT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List the files and folders directly inside one folder of the user's documents. Folders end with '/'.",
        "parameters": {"type": "object", "properties": {"folder": {"type": "string", "description": "Folder path; empty for the top folder"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read the text of one file (PDF, Word, PowerPoint, or the text of a scan or photo).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Path as listed by list_files"}}, "required": ["path"]}}},
]


# ---------------------------------------------------------------- scoring
def refused(text: str) -> bool:
    return not text.strip() or bool(REFUSAL.search(text))


def is_correct(text: str, gold: dict) -> bool:
    """The checks of run_ask.correct on free text. A reply that says it could not find the answer is an abstention."""
    a = SimpleNamespace(answer=text, route="none" if refused(text) else "search", facts=[], sources=[])
    return run_ask.correct(a, gold)


def reached(paths: list[str], gold: dict) -> tuple[bool, float | None]:
    """(did it reach a gold source, share of gold sources reached); None for questions without sources."""
    if not gold["sources"]:
        return False, None
    got = {rel(p) for p in paths} & set(gold["sources"])
    return bool(got), round(len(got) / len(gold["sources"]), 3)


def summarise(records: list[dict]) -> tuple[list[dict], dict]:
    div = lambda a, b: round(a / b, 3) if b else None
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    rows, cats = [], defaultdict(dict)
    for cond, name in CONDITIONS.items():
        rs = [r for r in records if r["condition"] == cond]
        if not rs:
            continue
        withsrc = [r for r in rs if r["source_recall"] is not None]
        calls = sum(r["tool_calls"] for r in rs)
        rows.append({
            "system": name, "n": len(rs), "correct": div(sum(r["correct"] for r in rs), len(rs)),
            "retrieval_success": div(sum(r["retrieved"] for r in withsrc), len(withsrc)),
            "source_recall": round(sum(r["source_recall"] for r in withsrc) / len(withsrc), 3) if withsrc else None,
            "steps_per_question": mean([r["steps"] for r in rs]), "tool_call_success": div(calls - sum(r["tool_errors"] for r in rs), calls),
            "out_of_steps_rate": div(sum(r["out_of_steps"] for r in rs), len(rs)), "error_rate": div(sum(bool(r["error"]) for r in rs), len(rs)),
            "prompt_tokens_per_question": mean([r["prompt_tokens"] for r in rs]),
            "completion_tokens_per_question": mean([r["completion_tokens"] for r in rs]),
            "latency_p50_ms": latency([r["ms"] for r in rs])["p50"], "latency_p95_ms": latency([r["ms"] for r in rs])["p95"]})
        for c in sorted({r["category"] for r in rs}):
            cr = [r for r in rs if r["category"] == c]
            cats[c]["n"] = len(cr)
            cats[c][f"{cond}_correct"] = div(sum(r["correct"] for r in cr), len(cr))
            ws = [r for r in cr if r["source_recall"] is not None]
            cats[c][f"{cond}_retrieval"] = div(sum(r["retrieved"] for r in ws), len(ws))
    return rows, dict(cats)


# ---------------------------------------------------------------- the agent loop
def run_agent(chat, tools: list[dict], call, question: str, max_steps: int, tool_chars: int) -> dict:
    """chat(messages, tools) -> an OpenAI chat completion; call(name, args) -> the tool's text (may raise).
    One step is one model call. Stops with an answer, or after max_steps."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    out = {"answer": "", "steps": 0, "prompt_tokens": 0, "completion_tokens": 0, "tool_calls": 0, "tool_errors": 0, "calls": [],
           "out_of_steps": False, "error": None}
    for _ in range(max_steps):
        try:
            r = chat(messages, tools)
        except Exception as e:  # e.g. the conversation no longer fits the server's context
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            return out
        out["steps"] += 1
        u = getattr(r, "usage", None)
        out["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
        out["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
        m = r.choices[0].message
        if not m.tool_calls:
            out["answer"] = re.sub(r"<think>.*?</think>", "", m.content or "", flags=re.S).strip()
            return out
        messages.append({"role": "assistant", "content": m.content or "", "tool_calls": [
            {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in m.tool_calls]})
        for t in m.tool_calls:
            out["tool_calls"] += 1
            name, args, text = t.function.name, {}, ""
            try:
                args = json.loads(t.function.arguments or "{}")
                text = call(name, args)
            except Exception as e:  # bad arguments, unknown tool, missing file: the model sees the error and may retry
                out["tool_errors"] += 1
                text = f"Error: {e}"
            out["calls"].append({"name": name, "args": args, "error": text.startswith("Error: ")})
            shown = text if len(text) <= tool_chars else text[:tool_chars] + "\n[cut: the result was longer]"
            messages.append({"role": "tool", "tool_call_id": t.id, "content": shown})
    out["out_of_steps"] = True
    return out


# ---------------------------------------------------------------- condition (b): plain files
class DirectTools:
    """list_files and read_file over a folder. Tracks which files were opened."""

    def __init__(self, root: Path, reader=None):
        self.root, self.opened, self.cache = root.resolve(), [], {}
        if reader is None:
            from sniff.ingest import readers
            reader = lambda p: "\n\n".join(f"[page {n}]\n{t}" for n, t in readers.read(p))
        self.reader = reader

    def _inside(self, p: str) -> Path:
        full = (self.root / p.strip("/\\")).resolve()
        if full != self.root and self.root not in full.parents:
            raise ValueError("that path is outside the documents folder")
        return full

    def call(self, name: str, args: dict) -> str:
        if name == "list_files":
            d = self._inside(args.get("folder") or "")
            if not d.is_dir():
                raise ValueError(f"no such folder: {args.get('folder')}")
            return "\n".join(sorted(f"{p.relative_to(self.root).as_posix()}{'/' if p.is_dir() else ''}" for p in d.iterdir())) or "(empty)"
        if name == "read_file":
            f = self._inside(args["path"])
            if not f.is_file():
                raise ValueError(f"no such file: {args['path']}")
            self.opened.append(str(f))
            if f not in self.cache:
                self.cache[f] = self.reader(f)
            return self.cache[f]
        raise ValueError(f"unknown tool {name}")


# ---------------------------------------------------------------- condition (a): Sniff over MCP
def openai_tools(mcp_tools) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}} for t in mcp_tools]


def result_text(res) -> str:
    """What a CallToolResult says, as one string. An error result comes back as 'Error: ...' so the agent loop counts it."""
    if getattr(res, "structured_content", None) is not None:
        s = res.structured_content
        text = json.dumps(s.get("result", s) if isinstance(s, dict) else s, ensure_ascii=False)
    else:
        text = "\n".join(c.text for c in res.content if hasattr(c, "text"))
    return f"Error: {text}" if getattr(res, "is_error", False) else text


def mcp_documents(name: str, text: str) -> list[str]:
    """The documents a Sniff tool result points at (listing every document is not reading it)."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if name == "search_documents" and isinstance(data, list):
        return [h["file"] for h in data if isinstance(h, dict) and "file" in h]
    if name == "ask" and isinstance(data, dict):
        return [re.sub(r" \(page \d+\)$", "", s) for s in data.get("sources", [])]
    return []


def usage_total(path: Path, skip: int) -> tuple[int, int, int]:
    """(prompt tokens, completion tokens, lines read) of the server-side chat calls logged after line `skip`."""
    lines = path.read_text(encoding="utf-8").splitlines()[skip:] if path.exists() else []
    rows = [json.loads(x) for x in lines if x.strip()]
    return sum(r["prompt"] for r in rows), sum(r["completion"] for r in rows), skip + len(lines)


# ---------------------------------------------------------------- the run
def record(g: dict, cond: str, agent: dict, docs: list[str], ms: float, inside: tuple[int, int] = (0, 0)) -> dict:
    ok, recall = reached(docs, g)
    text = agent["answer"]
    return {"condition": cond, "q": g["q"], "category": g["type"], "gold": g["answer"], "answer": text, "ms": ms,
            "correct": bool(text) and is_correct(text, g), "retrieved": ok, "source_recall": recall,
            "steps": agent["steps"], "tool_calls": agent["tool_calls"], "tool_errors": agent["tool_errors"],
            "out_of_steps": agent["out_of_steps"], "error": agent["error"],
            "prompt_tokens": agent["prompt_tokens"] + inside[0], "completion_tokens": agent["completion_tokens"] + inside[1],
            "tokens_inside_tools": sum(inside), "trace": agent["calls"]}


def questions(n: int) -> list[dict]:
    qs = [g for g in jsonl("ask.jsonl") if g.get("category") in CATEGORIES]
    for g in qs:  # the generator appends "Today is ..."; the tools have no date to give it to
        g["q"] = re.sub(r"\s*Today is \d{4}-\d{2}-\d{2}\.?", "", g["q"]).strip()
        g["type"] = g["category"]
    return stratified(qs, n)


def make_chat():
    from sniff import config
    from sniff.ai import llm
    return lambda messages, tools: llm.client().chat.completions.create(model=config.LLM_MODEL, messages=messages, tools=tools, temperature=0)


def run_direct(qs, done, args, chat) -> list[dict]:
    out = []
    for g in qs:
        if ("direct", g["q"]) in done:
            continue
        tools = DirectTools(SETS["test"] / "test")
        with Timer() as t:
            a = run_agent(chat, DIRECT_TOOLS, tools.call, g["q"], args.max_steps, args.tool_chars)
        out.append(record(g, "direct", a, tools.opened, t.ms))
        print(f"direct [{len(out)}] {g['type']}: {'ok' if out[-1]['correct'] else 'wrong'} in {t.ms / 1000:.0f} s", flush=True)
    return out


async def run_mcp(qs, done, args, chat) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from sniff import config
    from sniff.schemas import DOC_TYPES

    data = ROOT / ".sniff" / "mcp-eval"
    data.mkdir(parents=True, exist_ok=True)
    usage = data / "llm_usage.jsonl"
    env = {**os.environ, "SNIFF_DB": str(db_path("test")), "SNIFF_DATA": str(data), "SNIFF_USAGE_LOG": str(usage)}
    env |= {"SNIFF_LLM_URL": config.LLM_URL, "SNIFF_LLM_MODEL": config.LLM_MODEL}  # the server's own chat calls use the same model
    env.setdefault("SNIFF_MCP_ALLOW", json.dumps({t: ["*"] for t in DOC_TYPES}))  # every type, as in the direct condition
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("mcp_launcher.py"))], cwd=str(ROOT / "backend"), env=env)
    out, seen = [], usage_total(usage, 0)[2]
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = openai_tools((await session.list_tools()).tools)
        loop = asyncio.get_running_loop()
        for g in qs:
            if ("mcp", g["q"]) in done:
                continue
            docs = []

            def call(name, a):
                text = result_text(asyncio.run_coroutine_threadsafe(session.call_tool(name, a), loop).result(timeout=600))
                docs.extend(mcp_documents(name, text))
                return text

            with Timer() as t:
                a = await asyncio.to_thread(run_agent, chat, tools, call, g["q"], args.max_steps, args.tool_chars)
            p, c, seen = usage_total(usage, seen)
            out.append(record(g, "mcp", a, docs, t.ms, (p, c)))
            print(f"mcp [{len(out)}] {g['type']}: {'ok' if out[-1]['correct'] else 'wrong'} in {t.ms / 1000:.0f} s", flush=True)
    return out


def main(args):
    use_llm(args.llm_url, args.model)
    qs = questions(args.n)
    runs_path = RESULTS / "mcp_vs_direct_runs.json"
    records = [] if args.fresh or not runs_path.exists() else json.loads(runs_path.read_text(encoding="utf-8"))
    chat = make_chat()
    for cond in args.conditions:
        done = {(r["condition"], r["q"]) for r in records}
        new = run_direct(qs, done, args, chat) if cond == "direct" else asyncio.run(run_mcp(qs, done, args, chat))
        records += new
        RESULTS.mkdir(parents=True, exist_ok=True)
        runs_path.write_text(json.dumps(records, indent=1, default=str), encoding="utf-8")
    qset = {g["q"] for g in qs}
    records = [r for r in records if r["q"] in qset]
    rows, cats = summarise(records)
    fails = [{k: r[k] for k in ("condition", "category", "q", "gold", "answer", "retrieved", "steps", "out_of_steps", "error", "trace")}
             for r in records if not r["correct"]]
    save("mcp_vs_direct", {"n": len(qset), "rows": rows, "by_category": cats, "max_steps": args.max_steps, "tool_chars": args.tool_chars}, fails)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="questions, round-robin over categories")
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=list(CONDITIONS))
    ap.add_argument("--max-steps", type=int, default=6, help="model calls per question")
    ap.add_argument("--tool-chars", type=int, default=3000, help="longest tool result shown to the model")
    ap.add_argument("--llm-url", help="another OpenAI-compatible chat server (for example the 8B reference model)")
    ap.add_argument("--model")
    ap.add_argument("--fresh", action="store_true")
    stay_awake()
    main(ap.parse_args())
