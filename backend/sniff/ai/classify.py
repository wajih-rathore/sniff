from pydantic import BaseModel

from ..schemas import DocType
from . import llm

PROMPT = """You sort a person's documents into types. Read the document text and pick one type:
- id_card: a national identity card or similar ID card
- utility_bill: an electricity, gas, water, internet or phone bill
- certificate: a degree, diploma, course or award certificate
- receipt: a purchase receipt or invoice from a shop or website
- other: anything else, such as notes, letters, slides or forms
Reply with JSON only, for example {"doc_type": "receipt"}."""

MAX_CHARS = 2000  # the start of a document is enough to tell its type


class TypeOut(BaseModel):
    doc_type: DocType


def classify(text: str) -> str:
    out = llm.chat_json(
        [{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:MAX_CHARS]}],
        TypeOut,
    )
    return out.doc_type
