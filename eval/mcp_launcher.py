"""Starts sniff.mcp_server over stdio exactly as `python -m sniff.mcp_server` does, and appends the token usage of every
chat call the server makes (its router and answer calls inside `ask`) to the file named by SNIFF_USAGE_LOG.
run_mcp_vs_direct.py uses it so the tokens an agent "spends" through Sniff's tools are counted, not hidden in the server.
Embedding calls are not chat calls and are not counted."""
import json
import os

from sniff import mcp_server
from sniff.ai import llm

log = os.environ.get("SNIFF_USAGE_LOG")
if log:
    completions = llm.client().chat.completions
    create = completions.create

    def counted(*a, **kw):
        r = create(*a, **kw)
        u = getattr(r, "usage", None)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"prompt": getattr(u, "prompt_tokens", 0) or 0, "completion": getattr(u, "completion_tokens", 0) or 0}) + "\n")
        return r

    completions.create = counted

if __name__ == "__main__":
    mcp_server.mcp.run(transport="stdio")
