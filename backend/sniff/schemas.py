"""Document types, their fields, and the API response shapes. The frontend and eval scripts rely on these."""
from datetime import date as Date  # aliased: Receipt has a field called "date"
from typing import Literal

from pydantic import BaseModel, model_validator

DocType = Literal["id_card", "utility_bill", "certificate", "receipt", "other"]
DOC_TYPES: tuple[str, ...] = ("id_card", "utility_bill", "certificate", "receipt", "other")


# ---------- facts per document type (what the LLM extracts) ----------
class Facts(BaseModel):
    @model_validator(mode="after")
    def plausible_dates(self):
        # a misread day-first date (21.03.2003 -> 2103-03-21) fails here, so the LLM is asked again
        for name, v in self:
            if isinstance(v, Date) and not 1900 <= v.year <= 2100:
                raise ValueError(f"{name} {v} is not a plausible date; dates are written day first")
        return self


class IdCard(Facts):
    name: str | None = None
    id_number: str | None = None
    date_of_birth: Date | None = None
    issue_date: Date | None = None
    expiry_date: Date | None = None


class UtilityBill(Facts):
    provider: str | None = None
    account_number: str | None = None
    billing_month: str | None = None  # YYYY-MM
    amount_due: float | None = None
    due_date: Date | None = None


class Certificate(Facts):
    holder_name: str | None = None
    title: str | None = None
    institution: str | None = None
    issue_date: Date | None = None


class Receipt(Facts):
    merchant: str | None = None
    date: Date | None = None
    total_amount: float | None = None
    warranty_until: Date | None = None


FACT_MODELS: dict[str, type[BaseModel]] = {
    "id_card": IdCard,
    "utility_bill": UtilityBill,
    "certificate": Certificate,
    "receipt": Receipt,
}

# fields that hold a person's name, used by the "holder" filter
HOLDER_FIELDS = ("name", "holder_name")
# fields that hold a date the user should act on, used by Upcoming and reminders
ACTION_DATE_FIELDS = ("expiry_date", "due_date", "warranty_until")


# ---------- API shapes ----------
class Hit(BaseModel):
    chunk_id: int
    file_id: int
    path: str
    name: str
    page: int
    snippet: str
    score: float
    doc_type: str | None = None


class Fact(BaseModel):
    id: int
    file_id: int
    field: str
    value: str
    value_type: Literal["date", "money", "text", "id"]
    page: int
    verified: bool  # value found word for word on the page


class Source(BaseModel):
    file_id: int
    path: str
    name: str
    page: int


class Answer(BaseModel):
    question: str
    answer: str
    route: Literal["facts", "search", "none"]
    sources: list[Source] = []
    facts: list[Fact] = []
    query: dict | None = None  # the facts query template the router filled, for transparency


class Document(BaseModel):
    id: int
    path: str
    name: str
    doc_type: str | None
    indexed_at: str | None
    status: str
    facts: list[Fact] = []


class UpcomingItem(BaseModel):
    fact: Fact
    document: str  # file name
    path: str
    doc_type: str | None
    days_left: int
    reminder_status: str | None = None  # None, "created"
