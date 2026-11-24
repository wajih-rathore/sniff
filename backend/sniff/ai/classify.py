from pydantic import BaseModel

from ..schemas import DocType
from . import llm

PROMPT = """Sort the document above into one type:
- id_card: an identity card of any kind, such as a national identity card, driving licence, student card or staff card
- utility_bill: a bill or statement for electricity, gas, water, internet or phone service, even when it is called an invoice
- certificate: a degree, diploma, course or award certificate
- receipt: a receipt or invoice for things bought from a shop or website
- other: anything else, such as notes, letters, slides or forms
Reply with JSON only, for example {"doc_type": "receipt"}."""


class TypeOut(BaseModel):
    doc_type: DocType


def classify(pages: list[tuple[int, str]]) -> str:
    return llm.chat_json(llm.document_message(pages, PROMPT), TypeOut).doc_type
