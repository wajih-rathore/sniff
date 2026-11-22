from pydantic import BaseModel

from ..schemas import DocType
from . import llm

PROMPT = """Sort the document above into one type:
- id_card: a national identity card or similar ID card
- utility_bill: an electricity, gas, water, internet or phone bill
- certificate: a degree, diploma, course or award certificate
- receipt: a purchase receipt or invoice from a shop or website
- other: anything else, such as notes, letters, slides or forms
Reply with JSON only, for example {"doc_type": "receipt"}."""


class TypeOut(BaseModel):
    doc_type: DocType


def classify(pages: list[tuple[int, str]]) -> str:
    return llm.chat_json(llm.document_message(pages, PROMPT), TypeOut).doc_type
