"""Extract a document's key fields. Rules come first: they read every field they can from the value printed next to
its label (ai/rules.py). The model is asked only for required fields the rules left empty, and it sees only the
lines near those fields' labels (rules.window). It copies each value exactly as printed; code then parses dates,
months and amounts, and verify.check decides whether each value can be trusted, whoever read it. If AI is off or the
provider fails, the rules' facts are kept. A provider off this laptop gets those lines only for the types the user lets
leave it (ai_settings.may_send), and never the whole document, with ID numbers masked (llm.outgoing)."""
from pydantic import ValidationError, create_model

from .. import ai_settings, verify
from ..schemas import FIELDS
from . import llm, rules

PROMPT = """Extract these fields from the {doc_type} document above:
{fields}
Copy each value exactly as it is printed, including dates and amounts (for example "21.03.2003", "Rs. 4,320/-", "July 2026").
Use null for any field that is not in the document. Do not guess or calculate.
Reply with JSON only."""


# a printed value is a few tokens; the cap stops a small model that starts repeating itself inside a string
MAX_TOKENS_PER_FIELD = 40


def _raw_model(doc_type: str, fields: list[str]):
    """The fields asked for, every value the string as printed. Only these keys, so the reply stays short."""
    return create_model(f"{doc_type.title().replace('_', '')}Printed", **{f: (str | None, None) for f in fields})


def messages(doc_type: str, fields: list[str], pages: list[tuple[int, str]]) -> list[dict]:
    spec = FIELDS[doc_type]
    listing = "\n".join(f"- {f}: {spec[f].meaning}" for f in fields)
    return llm.document_message(pages, PROMPT.format(doc_type=doc_type.replace("_", " "), fields=listing))


def ask_model(doc_type: str, fields: list[str], pages: list[tuple[int, str]], p=None) -> dict[str, str]:
    """{field: printed value} from the chat model `p`, for the given fields only."""
    printed = llm.chat_json(messages(doc_type, fields, pages), _raw_model(doc_type, fields), p=p, max_tokens=MAX_TOKENS_PER_FIELD * len(fields) + 20)
    return {f: v for f, v in printed.model_dump().items() if v and v.strip() and "•" not in v}  # • : a masked ID came back


def lines_to_send(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None, fields: list[str], s: dict) -> list[tuple[int, str]]:
    """The candidate lines the model reads for `fields`; [] when nothing may be sent for this type."""
    if not ai_settings.remote(s):
        return rules.window(doc_type, pages, boxes, fields)
    return rules.window(doc_type, pages, boxes, fields, whole=False) if ai_settings.may_send(s, doc_type) else []


def outbound(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None, s: dict) -> list[dict]:
    """What extract() sends for this document, as [{"purpose", "messages"}] before masking; [] when nothing."""
    printed = rules.extract(doc_type, pages, boxes or {})
    missing = [f for f, spec in FIELDS[doc_type].items() if spec.required and f not in printed]
    lines = lines_to_send(doc_type, pages, boxes or {}, missing, s) if missing else []
    return [{"purpose": f"Fill the fields the rules missed: {', '.join(missing)}", "messages": messages(doc_type, missing, lines)}] if lines else []


def extract(doc_type: str, pages: list[tuple[int, str]], boxes: dict | None = None, rules_first: bool = True,
            fill_gaps: bool = True) -> list[dict]:
    """The document's facts. rules_first=False is the old way, kept for the comparison: the model reads every field
    from the whole document. fill_gaps=False: the rules only, no model call (a guessed type: what may be sent depends
    on a type that may be wrong)."""
    spec = FIELDS[doc_type]
    boxes = boxes or {}
    if rules_first:
        printed = rules.extract(doc_type, pages, boxes)
        missing = [f for f, s in spec.items() if s.required and f not in printed] if fill_gaps else []
        s = ai_settings.current()  # one read: what may be sent and where it goes come from the same settings
        if missing and (lines := lines_to_send(doc_type, pages, boxes, missing, s)):
            try:
                printed |= ask_model(doc_type, missing, lines, llm.provider(s))
            except (ValidationError, llm.Unavailable):  # an invalid reply, AI off or the provider failed: keep what the rules read
                pass
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
