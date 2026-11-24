"""Extract a document's key fields. The LLM copies each value exactly as printed (structured JSON output);
code then parses dates, months and amounts, and verify.check decides whether each value can be trusted."""
from pydantic import create_model

from .. import verify
from ..schemas import FIELDS
from . import llm

PROMPT = """Extract these fields from the {doc_type} document above:
{fields}
Copy each value exactly as it is printed, including dates and amounts (for example "21.03.2003", "Rs. 4,320/-", "July 2026").
Use null for any field that is not in the document. Do not guess or calculate.
Reply with JSON only."""


def _raw_model(doc_type: str):
    """Same fields as the typed model, but every value is the string as printed."""
    return create_model(f"{doc_type.title().replace('_', '')}Printed", **{f: (str | None, None) for f in FIELDS[doc_type]})


def extract(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None = None) -> list[dict]:
    spec = FIELDS[doc_type]
    fields = "\n".join(f"- {f}: {s.meaning}" for f, s in spec.items())
    printed = llm.chat_json(llm.document_message(pages, PROMPT.format(doc_type=doc_type.replace("_", " "), fields=fields)), _raw_model(doc_type))

    facts = []
    for field, raw in printed.model_dump().items():
        if not raw or not raw.strip():
            continue
        value = verify.parse(spec[field].kind, raw)
        if value is not None:
            facts.append({"field": field, "raw": raw, "value": value})
    verify.check(doc_type, facts, pages, boxes or {})
    return [{"field": f["field"], "value": verify.stored(f["value"]), "value_type": verify.VALUE_TYPES.get(spec[f["field"]].kind, "text"),
             "page": f["page"], "verified": f["verified"], "status": f["status"], "reason": f["reason"], "source_text": f["source_text"]}
            for f in facts]
