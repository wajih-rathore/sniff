from pydantic import BaseModel

from .. import ai_settings
from ..schemas import DocType
from . import llm

PROMPT = """Sort the document above into one type:
- id_card: an identity card of any kind, such as a national identity card, driving licence, student card or staff card
- passport: a passport
- utility_bill: a bill or statement for electricity, gas, water, internet or phone service, even when it is called an invoice
- certificate: a degree, diploma, course or award certificate
- transcript: a transcript or result card with grades and a GPA
- receipt: a receipt or invoice for things bought from a shop or website
- warranty_card: a product warranty card
- bank_statement: a bank account statement
- offer_letter: a job offer letter
- insurance_policy: an insurance policy or policy schedule
- tenancy_agreement: a rent or tenancy agreement
- fee_voucher: a school, college or university fee voucher
- other: anything else, such as notes, letters, slides or forms
Reply with JSON only, for example {"doc_type": "receipt"}."""


class TypeOut(BaseModel):
    doc_type: DocType


def classify(pages: list[tuple[int, str]]) -> str | None:
    """The type the chat model reads, or None (the document stays unclassified): when AI is off, and for a provider off
    this laptop, which never gets a whole document."""
    s = ai_settings.current()
    if ai_settings.remote(s):
        return None
    try:
        return llm.chat_json(llm.document_message(pages, PROMPT), TypeOut, p=llm.provider(s)).doc_type
    except llm.Unavailable:
        return None
