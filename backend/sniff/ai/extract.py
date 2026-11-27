"""Extract a document's key fields. Rules come first: they read every field they can from the value printed next to
its label (ai/rules.py). The model is asked only for required fields the rules left empty, and it sees only the
lines near those fields' labels (rules.window). It copies each value exactly as printed; code then parses dates,
months and amounts, and verify.check decides whether each value can be trusted, whoever read it."""
from pydantic import create_model

from .. import verify
from ..schemas import FIELDS
from . import llm, rules

PROMPT = """Extract these fields from the {doc_type} document above:
{fields}
Copy each value exactly as it is printed, including dates and amounts (for example "21.03.2003", "Rs. 4,320/-", "July 2026").
Use null for any field that is not in the document. Do not guess or calculate.
Reply with JSON only."""


def _raw_model(doc_type: str, fields: list[str]):
    """The fields asked for, every value the string as printed. Only these keys, so the reply stays short."""
    return create_model(f"{doc_type.title().replace('_', '')}Printed", **{f: (str | None, None) for f in fields})


def ask_model(doc_type: str, fields: list[str], pages: list[tuple[int, str]]) -> dict[str, str]:
    """{field: printed value} from the chat model, for the given fields only."""
    spec = FIELDS[doc_type]
    listing = "\n".join(f"- {f}: {spec[f].meaning}" for f in fields)
    printed = llm.chat_json(llm.document_message(pages, PROMPT.format(doc_type=doc_type.replace("_", " "), fields=listing)),
                            _raw_model(doc_type, fields))
    return {f: v for f, v in printed.model_dump().items() if v and v.strip()}


def extract(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None = None, rules_first: bool = True) -> list[dict]:
    """The document's facts. rules_first=False is the old way, kept for the comparison: the model reads every field
    from the whole document."""
    spec = FIELDS[doc_type]
    boxes = boxes or {}
    if rules_first:
        printed = rules.extract(doc_type, pages, boxes)
        missing = [f for f, s in spec.items() if s.required and f not in printed]
        if missing:
            printed |= ask_model(doc_type, missing, rules.window(doc_type, pages, boxes, missing))
    else:
        printed = ask_model(doc_type, list(spec), pages)

    facts = []
    for field, raw in printed.items():
        value = verify.parse(spec[field].kind, raw)
        if value is not None:
            facts.append({"field": field, "raw": raw, "value": value})
    verify.check(doc_type, facts, pages, boxes)
    return [{"field": f["field"], "value": verify.stored(f["value"]), "value_type": verify.VALUE_TYPES.get(spec[f["field"]].kind, "text"),
             "page": f["page"], "verified": f["verified"], "status": f["status"], "reason": f["reason"], "source_text": f["source_text"]}
            for f in sorted(facts, key=lambda f: list(spec).index(f["field"]))]
