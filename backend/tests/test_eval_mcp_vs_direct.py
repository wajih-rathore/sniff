"""MCP-vs-direct harness: the agent loop, the file tools, the scoring, and a real MCP client session to the stdio server."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import common  # noqa: E402
import run_mcp_vs_direct as m  # noqa: E402


def reply(content=None, calls=(), prompt=10, completion=5):
    tcs = [NS(id=f"c{i}", function=NS(name=n, arguments=a)) for i, (n, a) in enumerate(calls)] or None
    return NS(choices=[NS(message=NS(content=content, tool_calls=tcs))], usage=NS(prompt_tokens=prompt, completion_tokens=completion))


def script(*replies):
    it = iter(replies)
    return lambda messages, tools: next(it)


def test_agent_loop_counts_steps_tokens_and_tool_errors():
    seen = []

    def call(name, args):
        seen.append(name)
        if name == "read_file" and args["path"] == "missing.pdf":
            raise ValueError("no such file: missing.pdf")
        return "x" * 50

    chat = script(reply(calls=[("list_files", "{}")]), reply(calls=[("read_file", '{"path": "missing.pdf"}'), ("read_file", "not json")]),
                  reply("<think>hm</think>The total is Rs. 5,000."))
    out = m.run_agent(chat, [], call, "q", max_steps=5, tool_chars=20)
    assert out["answer"] == "The total is Rs. 5,000." and out["steps"] == 3 and out["tool_calls"] == 3 and out["tool_errors"] == 2
    assert out["prompt_tokens"] == 30 and out["completion_tokens"] == 15 and seen == ["list_files", "read_file"]
    assert [c["error"] for c in out["calls"]] == [False, True, True] and not out["out_of_steps"]


def test_agent_loop_stops_at_the_step_cap_and_survives_a_server_error():
    out = m.run_agent(lambda *a: reply(calls=[("list_files", "{}")]), [], lambda n, a: "ok", "q", max_steps=3, tool_chars=100)
    assert out["out_of_steps"] and out["steps"] == 3 and out["answer"] == ""

    def boom(*a):
        raise RuntimeError("exceeds the available context size")
    out = m.run_agent(boom, [], lambda n, a: "ok", "q", max_steps=3, tool_chars=100)
    assert out["error"].startswith("RuntimeError") and out["steps"] == 0


def test_long_tool_results_are_cut_for_the_model():
    sent = []

    def chat(messages, tools):
        sent.append(messages[-1])
        return reply(calls=[("list_files", "{}")]) if len(sent) == 1 else reply("done")
    m.run_agent(chat, [], lambda n, a: "y" * 500, "q", 4, tool_chars=100)
    assert sent[1]["role"] == "tool" and len(sent[1]["content"]) < 150 and "cut" in sent[1]["content"]


def test_correctness_uses_run_ask_checks_and_refusals():
    num = {"answer_type": "number", "answer": "6071"}
    assert m.is_correct("The March electricity bill was Rs. 6,071.", num)
    assert not m.is_correct("It was Rs. 6,000.", num)
    assert not m.is_correct("I could not find that bill, but maybe Rs. 6,071.", num)  # a refusal is not an answer
    none = {"answer_type": "none", "answer": ""}
    assert m.is_correct("I could not find a passport number in your documents.", none)
    assert not m.is_correct("Your passport number is AB1234567.", none)
    assert m.is_correct("2017-03-05 and 2027-02-10", {"answer_type": "list", "answer": "2017-03-05;2027-02-10"})


def test_reached_matches_gold_sources_by_relative_path(monkeypatch, tmp_path):
    monkeypatch.setitem(common.SETS, "test", tmp_path)
    gold = {"sources": ["test/a.pdf", "test/b.pdf"]}
    assert m.reached([str(tmp_path / "test" / "a.pdf"), str(tmp_path / "test" / "z.pdf")], gold) == (True, 0.5)
    assert m.reached([], gold) == (False, 0.0)
    assert m.reached([str(tmp_path / "x.pdf")], {"sources": []}) == (False, None)


def rec(cond, cat, correct, retrieved, recall, **kw):
    return {"condition": cond, "category": cat, "correct": correct, "retrieved": retrieved, "source_recall": recall, "steps": 2,
            "tool_calls": 4, "tool_errors": 1, "out_of_steps": False, "error": None, "prompt_tokens": 100, "completion_tokens": 20, "ms": 1000, **kw}


def test_summary_per_condition_and_category():
    records = [rec("mcp", "single_fact", True, True, 1.0), rec("mcp", "unanswerable", True, False, None),
               rec("direct", "single_fact", False, True, 1.0), rec("direct", "unanswerable", True, False, None, out_of_steps=True, ms=3000)]
    rows, cats = m.summarise(records)
    a, b = rows
    assert a["system"].endswith("MCP tools") and a["correct"] == 1.0 and a["retrieval_success"] == 1.0 and a["tool_call_success"] == 0.75
    assert b["correct"] == 0.5 and b["out_of_steps_rate"] == 0.5 and b["prompt_tokens_per_question"] == 100.0 and b["latency_p95_ms"] == 3000
    assert cats["single_fact"] == {"n": 1, "mcp_correct": 1.0, "mcp_retrieval": 1.0, "direct_correct": 0.0, "direct_retrieval": 1.0}


def test_direct_tools_list_read_and_stay_inside(tmp_path):
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "bill.pdf").write_bytes(b"x")
    t = m.DirectTools(tmp_path, reader=lambda p: f"text of {p.name}")
    assert t.call("list_files", {}) == "Desktop/" and t.call("list_files", {"folder": "Desktop"}) == "Desktop/bill.pdf"
    assert t.call("read_file", {"path": "Desktop/bill.pdf"}) == "text of bill.pdf" and t.opened == [str((tmp_path / "Desktop" / "bill.pdf").resolve())]
    for bad in ({"path": "../secret.txt"}, {"path": "Desktop/none.pdf"}):
        with pytest.raises(ValueError):
            t.call("read_file", bad)
    with pytest.raises(ValueError):
        t.call("list_files", {"folder": ".."})


def test_mcp_result_helpers():
    assert m.result_text(NS(structured_content={"result": [{"file": "a"}]}, content=[], is_error=False)) == '[{"file": "a"}]'
    assert m.result_text(NS(structured_content=None, content=[NS(text="boom")], is_error=True)) == "Error: boom"
    assert m.mcp_documents("search_documents", '[{"file": "C:/d/a.pdf", "page": 1}]') == ["C:/d/a.pdf"]
    assert m.mcp_documents("ask", '{"answer": "x", "sources": ["C:/d/a.pdf (page 2)"]}') == ["C:/d/a.pdf"]
    assert m.mcp_documents("list_documents", '[{"file": "C:/d/a.pdf"}]') == [] and m.mcp_documents("ask", "not json") == []


def test_usage_log_counts_only_new_lines(tmp_path):
    f = tmp_path / "u.jsonl"
    assert m.usage_total(f, 0) == (0, 0, 0)
    f.write_text('{"prompt": 5, "completion": 1}\n{"prompt": 7, "completion": 2}\n')
    assert m.usage_total(f, 1) == (7, 2, 2)


def test_real_mcp_session_over_stdio(conn, add_doc, tmp_path):
    """A real MCP client talks to the launcher (sniff.mcp_server over stdio) and calls a tool that needs no model."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from sniff import config
    from sniff import mcp_settings
    add_doc("C:/docs/bill.pdf", "utility_bill", {"provider": "LESCO", "amount_due": "1200.0"})
    mcp_settings.save(conn, True, {"utility_bill": ["*"]})  # MCP is off until the user turns it on
    env = {**os.environ, "SNIFF_DB": str(config.DB_PATH), "SNIFF_DATA": str(tmp_path / "mcp"), "SNIFF_USAGE_LOG": str(tmp_path / "u.jsonl")}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "eval" / "mcp_launcher.py")], cwd=str(ROOT / "backend"), env=env)

    async def go():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            tools = m.openai_tools((await s.list_tools()).tools)
            res = await s.call_tool("list_documents", {})
            return tools, m.result_text(res)
    tools, text = asyncio.run(asyncio.wait_for(go(), 60))
    assert {t["function"]["name"] for t in tools} == {"search_documents", "ask", "list_documents"}
    assert all(t["function"]["parameters"]["type"] == "object" for t in tools)
    assert json.loads(text)[0]["facts"]["provider"] == "LESCO"


def test_main_with_a_fake_model_writes_the_result_file(tmp_path, monkeypatch):
    (tmp_path / "test").mkdir()
    monkeypatch.setitem(common.SETS, "test", tmp_path)
    monkeypatch.setattr(m, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(common, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(m, "make_chat", lambda: lambda messages, tools: reply("I could not find it."))
    m.main(NS(llm_url=None, model=None, n=8, conditions=["direct"], max_steps=3, tool_chars=100, fresh=True))
    res = json.loads((tmp_path / "results" / "mcp_vs_direct.json").read_text())
    assert res["n"] == 8 and len(res["rows"]) == 1 and res["rows"][0]["system"] == m.CONDITIONS["direct"]
    assert 0 < res["rows"][0]["correct"] < 1  # refusing is right only on the unanswerable questions
