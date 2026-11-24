"""Document types, their fields and rules, and the API response shapes. The frontend and eval scripts rely on these.
docs/FIELDS.md is the readable version of FIELDS below; keep the two in step."""
from datetime import date as Date  # aliased: Receipt has a field called "date"
from typing import Literal, NamedTuple

from pydantic import BaseModel, create_model, model_validator

DOC_TYPES: tuple[str, ...] = ("id_card", "utility_bill", "certificate", "receipt", "passport", "transcript", "bank_statement",
                              "warranty_card", "offer_letter", "insurance_policy", "tenancy_agreement", "fee_voucher", "other")
DocType = Literal[DOC_TYPES]  # type: ignore[valid-type]

FactStatus = Literal["verified", "needs_review", "corrected", "approved"]


# ---------- fields per document type ----------
class Field(NamedTuple):
    kind: str  # text | id | date | month (YYYY-MM) | money | status (paid / unpaid) | passport | serial | gpa
    meaning: str
    anchors: tuple[str, ...]  # labels printed next to the value; verification looks for one of them near it
    required: bool = False
    stable: bool = False  # does not change for the same person or record, so two different values are a conflict
    header: bool = False  # an organisation name that may stand alone in the letterhead (first lines of the page)


FIELDS: dict[str, dict[str, Field]] = {
    "id_card": {
        "name": Field("text", "the card holder's full name", ("Name", "Full Name", "Holder", "Name of Holder"), True, True),
        "id_number": Field("id", "the card number (CNIC #####-#######-#, licence, roll or employee number)",
                           ("Identity Number", "ID No", "ID Number", "CNIC", "Card No", "Licence No", "License No",
                            "Roll No", "Employee No", "Registration No"), True, True),
        "date_of_birth": Field("date", "the holder's date of birth", ("Date of Birth", "DOB", "Born", "Birth"), False, True),
        "issue_date": Field("date", "the date the card was issued", ("Date of Issue", "Issue Date", "Issued", "Issued on", "Valid From"), False, True),
        "expiry_date": Field("date", "the date the card stops being valid",
                             ("Date of Expiry", "Expiry", "Expiry Date", "Expires", "Valid Until", "Valid Thru", "Valid Through"), True, True),
    },
    "utility_bill": {
        "provider": Field("text", "the company that sent the bill",
                          ("Electric", "Electricity", "Gas", "Water", "Internet", "Telecom", "Power", "Supply", "Ltd",
                           "Limited", "Co", "Company", "Bill", "Statement", "Invoice"), True, True, True),
        "account_number": Field("id", "the customer's account or consumer number",
                                ("Account No", "Account Number", "Account", "Acct", "Consumer No", "Customer ID"), True, True),
        "billing_month": Field("month", "the month the bill is for", ("Billing Month", "Bill Month", "Billing Period", "Statement Period", "Period", "Month"), True, True),
        "bill_date": Field("date", "the date the bill was issued", ("Issue Date", "Bill Date", "Statement Date", "Invoice Date", "Date of Issue", "Issued on")),
        "due_date": Field("date", "the last date to pay without a late fee", ("Due Date", "Last date of payment", "Pay by", "Payment Due", "Last Date"), True),
        "current_charges": Field("money", "this month's charges, including taxes, without old unpaid amounts",
                                 ("Current Charges", "Current Bill", "Charges for the month", "Current Month Charges", "Current Amount"), True),
        "previous_balance": Field("money", "unpaid amount carried over from earlier bills (arrears)",
                                  ("Previous Balance", "Arrears", "Arrear", "Balance brought forward", "Previous Dues", "Unpaid Balance", "Outstanding")),
        "tax": Field("money", "the tax on this bill", ("Tax", "Taxes", "GST", "Sales Tax", "VAT", "Duties")),
        "amount_due": Field("money", "the total payable: current charges plus previous balance",
                            ("Amount Due", "Amount Payable", "Total Payable", "Total Due", "Net Amount Due", "Total Amount Due",
                             "Net Payable", "Payable within due date"), True),
        "payment_status": Field("status", "paid or unpaid, only if the bill says so", ("Paid", "Unpaid", "Payment Status", "Status", "Payment received")),
        "payment_date": Field("date", "the date the bill was paid, if printed", ("Paid on", "Payment Date", "Date of Payment", "Payment received on", "Received on")),
    },
    "certificate": {
        "holder_name": Field("text", "the person the certificate is for",
                             ("This is to certify that", "certify that", "Presented to", "confers upon", "Awarded to", "granted to", "Name"), True, True),
        "title": Field("text", "the degree, course or award",
                       ("has successfully completed", "completed the course", "the degree of", "the certificate in",
                        "for successfully completing", "for being awarded", "requirements for", "examination for", "in recognition of"), True, True),
        "institution": Field("text", "the organisation that issued it",
                             ("University", "College", "Institute", "Academy", "School", "Center", "Centre", "Board", "Learning", "Issued by"), True, True, True),
        "issue_date": Field("date", "the date it was issued", ("Issue Date", "Date of Issue", "Issued on", "Awarded on", "Given this", "Date"), True, True),
    },
    "receipt": {
        "merchant": Field("text", "the shop or website", ("Receipt", "Invoice", "Cash Memo", "Order Confirmation", "Tel", "Mart",
                                                         "Store", "Pharmacy", "Station", "Restaurant", "Grocers", "Appliances"), True, False, True),
        "date": Field("date", "the purchase date", ("Date", "Placed on", "Order Date", "Purchase Date", "Invoice Date", "Dated"), True),
        "subtotal": Field("money", "the amount before tax, if printed", ("Subtotal", "Sub Total", "Sub-total")),
        "tax": Field("money", "the tax, if printed", ("Tax", "GST", "Sales Tax", "Service Tax", "VAT")),
        "total_amount": Field("money", "the total paid", ("Total", "Grand Total", "Amount Paid", "Net Payable", "Amount Due", "Net Total",
                                                         "Order Total", "Total Paid", "Total Amount"), True),
        "warranty_until": Field("date", "the date the warranty ends, if any", ("Warranty", "Guarantee", "Valid until", "Warranty till", "expires")),
    },
    # ---- the A4 types
    "passport": {
        "name": Field("text", "the passport holder's full name", ("Name", "Full Name", "Holder's Name"), True, True),
        "passport_number": Field("passport", "the passport number", ("Passport No", "Passport Number", "Passport #"), True, True),
        "nationality": Field("text", "the holder's nationality", ("Nationality",), True, True),
        "date_of_birth": Field("date", "the holder's date of birth", ("Date of Birth", "DOB", "Born"), False, True),
        "issue_date": Field("date", "the date the passport was issued", ("Date of Issue", "Issue Date", "Issued on"), False, True),
        "expiry_date": Field("date", "the date the passport stops being valid", ("Date of Expiry", "Expiry Date", "Expiry", "Valid Until"), True, True),
    },
    "transcript": {
        "student_name": Field("text", "the student's full name", ("Student Name", "Name of Student", "Name"), True, True),
        "registration_number": Field("id", "the student's registration or roll number",
                                     ("Registration No", "Registration Number", "Reg No", "Roll No", "Student ID"), True, True),
        "institution": Field("text", "the university or college that issued it", ("University", "College", "Institute", "School", "Academy"), True, True, True),
        "program": Field("text", "the degree programme", ("Program", "Programme", "Degree Program", "Degree"), True, True),
        "cgpa": Field("gpa", "the cumulative GPA (CGPA) on the transcript, not a semester GPA", ("CGPA", "Cumulative GPA", "Cumulative Grade Point Average"), True, True),
        "issue_date": Field("date", "the date the transcript was issued", ("Date of Issue", "Issue Date", "Issued on", "Date"), True, True),
    },
    "bank_statement": {
        "bank": Field("text", "the bank that issued the statement", ("Bank", "Bank Limited", "Bank Ltd"), True, True, True),
        "account_number": Field("id", "the account number (not the IBAN)", ("Account Number", "Account No", "A/C No", "Account #"), True, True),
        "period_start": Field("date", "the first day the statement covers", ("Statement Period", "Period From", "From"), True, True),
        "period_end": Field("date", "the last day the statement covers", ("Period To", "To"), True, True),
        "opening_balance": Field("money", "the balance at the start of the period", ("Opening Balance", "Balance brought forward", "Balance B/F")),
        "total_credits": Field("money", "all money paid into the account in the period", ("Total Credits", "Total Deposits", "Credits")),
        "total_debits": Field("money", "all money paid out of the account in the period", ("Total Debits", "Total Withdrawals", "Debits")),
        "closing_balance": Field("money", "the balance at the end of the period", ("Closing Balance", "Balance C/F", "Ending Balance"), True),
    },
    "warranty_card": {
        "product": Field("text", "the product the warranty covers", ("Product", "Product Name", "Item", "Appliance"), True, True),
        "brand": Field("text", "the brand or maker", ("Brand", "Manufacturer"), True, True, True),
        "serial_number": Field("serial", "the product's serial number", ("Serial No", "Serial Number", "S/N"), True, True),
        "purchase_date": Field("date", "the date it was bought", ("Date of Purchase", "Purchase Date", "Purchased on"), True, True),
        "warranty_until": Field("date", "the date the warranty ends",
                                ("Warranty valid until", "Valid until", "Warranty till", "Warranty expires", "Valid Till"), True, True),
    },
    "offer_letter": {
        "employer": Field("text", "the company making the offer", ("Pvt Ltd", "Ltd", "Limited", "Company", "on behalf of"), True, True, True),
        "candidate_name": Field("text", "the person offered the job", ("Dear", "Candidate", "Name", "To"), True, True),
        "position": Field("text", "the job title offered", ("Position", "Designation", "Job Title"), True, True),
        "monthly_salary": Field("money", "the gross salary per month",
                                ("Monthly Gross Salary", "Gross Monthly Salary", "Monthly Salary", "Gross Salary", "Salary"), True, True),
        "offer_date": Field("date", "the date of the letter", ("Date", "Dated"), True, True),
        "start_date": Field("date", "the date the job starts", ("Joining Date", "Date of Joining", "Start Date", "join us on"), True, True),
    },
    "insurance_policy": {
        "insurer": Field("text", "the insurance company", ("Insurance", "Assurance", "Takaful", "Insurer"), True, True, True),
        "policy_number": Field("id", "the policy number", ("Policy No", "Policy Number", "Policy #"), True, True),
        "insured_name": Field("text", "the person insured or the policy holder",
                              ("Insured", "Name of Insured", "Policy Holder", "Policyholder", "Insured Person"), True, True),
        "premium": Field("money", "the premium for the policy period", ("Premium", "Annual Premium", "Total Premium", "Premium Payable"), True, True),
        "start_date": Field("date", "the date cover starts", ("Period of Insurance", "From", "Start Date", "Effective Date", "Commencement Date"), True, True),
        "end_date": Field("date", "the date cover ends (the policy expires)", ("To", "End Date", "Expiry Date", "Valid Until"), True, True),
    },
    "tenancy_agreement": {
        "landlord": Field("text", "the owner letting the property", ("Landlord", "Lessor", "Owner"), True, True),
        "tenant": Field("text", "the person renting the property", ("Tenant", "Lessee"), True, True),
        "property_address": Field("text", "the address of the rented property", ("Property Address", "Property", "Premises", "Address of the Premises"), True, True),
        "monthly_rent": Field("money", "the rent per month", ("Monthly Rent", "Rent per month", "Rent", "rent of"), True, True),
        "security_deposit": Field("money", "the refundable security deposit", ("Security Deposit", "Deposit"), False, True),
        "start_date": Field("date", "the first day of the tenancy", ("Commencement Date", "Start Date", "with effect from"), True, True),
        "end_date": Field("date", "the last day of the tenancy", ("Expiry Date", "End Date", "ending on"), True, True),
    },
    "fee_voucher": {
        "student_name": Field("text", "the student the fee is for", ("Student Name", "Name of Student", "Name"), True, True),
        "registration_number": Field("id", "the student's registration or roll number", ("Registration No", "Reg No", "Roll No", "Student ID"), True, True),
        "institution": Field("text", "the school, college or university", ("University", "College", "School", "Institute", "Academy"), True, True, True),
        "semester": Field("text", "the semester or term the fee is for, e.g. Fall 2026", ("Semester", "Session", "Term", "Fee for"), True, True),
        "amount": Field("money", "the total to pay by the due date",
                        ("Total Payable", "Amount Payable", "Total Amount", "Total Fee", "Payable within due date", "Net Payable"), True, True),
        "issue_date": Field("date", "the date the voucher was issued", ("Issue Date", "Date of Issue", "Issued on"), False, True),
        "due_date": Field("date", "the last date to pay", ("Due Date", "Last Date", "Pay by", "Last date of payment"), True, True),
    },
}

# labels printed on these documents that belong to no field: a value next to one of them is not trusted
DISTRACTORS: dict[str, tuple[str, ...]] = {
    "utility_bill": ("Reference", "Amount after due date", "Late payment surcharge", "Surcharge", "Charges", "Rent",
                     "Adjustment", "Consumption", "Customer Care", "Units", "Add-ons"),
    "receipt": ("Cash tendered", "Change", "Receipt #", "Receipt No", "Order No", "Unit Price", "Qty", "Time"),
    "certificate": ("Certificate No", "Serial No", "Certificate ID", "Registrar", "Director"),
    "id_card": ("Signature",),
    "passport": ("Place of Birth", "Place of Issue", "Issuing Authority", "Citizenship No", "Tracking No", "Booklet No", "Sex",
                 "Type", "Country Code", "Signature"),
    "transcript": ("SGPA", "GPA", "Semester GPA", "Credit Hours", "Credits", "Grade", "Transcript No", "Date of Birth",
                   "Father's Name", "Session"),
    "bank_statement": ("Statement Date", "IBAN", "Branch", "Branch Code", "Cheque No", "Customer ID"),
    "warranty_card": ("Model No", "Model", "Dealer", "Warranty Period", "Customer Name", "Card No", "Phone", "Invoice No"),
    "offer_letter": ("Ref", "Reference", "Basic Salary", "Allowance", "House Rent Allowance", "Medical Allowance", "Probation",
                     "Reply by", "Department", "Working Hours"),
    "insurance_policy": ("Sum Insured", "Sum Assured", "Cover Type", "Deductible", "Vehicle", "Issue Date", "Agent", "Branch"),
    "tenancy_agreement": ("Agreement Date", "made on", "Witness", "Signature", "Annual increase", "Notice period", "Advance rent"),
    "fee_voucher": ("Challan No", "Voucher No", "Tuition Fee", "Admission Fee", "Lab Fee", "Library Fee", "Exam Fee", "Sports Fee",
                    "Late Fee", "Payable after due date", "Amount after due date", "Program", "Account No", "Branch"),
}

# near-duplicate keys: two documents with the same key are the same record (e.g. one bill as a PDF and as a photo)
KEYS: dict[str, tuple[str, ...]] = {
    "utility_bill": ("provider", "account_number", "billing_month"),
    "receipt": ("merchant", "date", "total_amount"),
    "id_card": ("id_number",),
    "certificate": ("holder_name", "title", "issue_date"),
    "passport": ("passport_number",),
    "transcript": ("registration_number", "issue_date"),
    "bank_statement": ("bank", "account_number", "period_end"),
    "warranty_card": ("brand", "serial_number"),
    "offer_letter": ("employer", "candidate_name", "position"),
    "insurance_policy": ("insurer", "policy_number", "start_date"),
    "tenancy_agreement": ("landlord", "tenant", "start_date"),
    "fee_voucher": ("registration_number", "semester"),
}
assert set(FIELDS) == set(KEYS) == set(DOC_TYPES) - {"other"}


class Facts(BaseModel):
    @model_validator(mode="after")
    def plausible_dates(self):
        for name, v in self:
            if isinstance(v, Date) and not 1900 <= v.year <= 2100:
                raise ValueError(f"{name} {v} is not a plausible date; dates are written day first")
        return self


PY_TYPES = {"date": Date, "money": float, "gpa": float}
FACT_MODELS: dict[str, type[BaseModel]] = {
    t: create_model("".join(w.title() for w in t.split("_")), __base__=Facts,
                    **{f: (PY_TYPES.get(spec.kind, str) | None, None) for f, spec in fields.items()})
    for t, fields in FIELDS.items()
}

# fields that hold a person's name, used by the "holder" filter
HOLDER_FIELDS = ("name", "holder_name", "student_name", "candidate_name", "insured_name", "tenant", "landlord")
# fields that hold a company or institution, used by the "provider" filter
ORG_FIELDS = ("provider", "merchant", "institution", "bank", "brand", "employer", "insurer")
# fields that hold a date the user should act on, used by Upcoming and reminders
ACTION_DATE_FIELDS = ("expiry_date", "due_date", "warranty_until", "end_date")
# dates that mean something stops being valid, used by "what expires between X and Y"
EXPIRY_FIELDS = ("expiry_date", "warranty_until", "end_date")


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
    verified: bool  # on the page, next to its field's label, and passes the field's rules
    status: FactStatus = "needs_review"
    reason: str | None = None  # why it needs review
    source_text: str | None = None


class Source(BaseModel):
    file_id: int
    path: str
    name: str
    page: int


class ConflictValue(BaseModel):
    value: str
    fact_id: int
    status: FactStatus
    source: Source


class Conflict(BaseModel):
    doc_type: str
    key: str  # what the documents share, e.g. "id_number 35202-1234567-1"
    field: str
    values: list[ConflictValue]


class Answer(BaseModel):
    question: str
    answer: str
    route: Literal["facts", "search", "none"]
    sources: list[Source] = []
    facts: list[Fact] = []
    query: dict | None = None  # the facts query template the router filled, for transparency
    counted: list[Source] = []  # documents used in a calculation
    skipped_duplicates: list[Source] = []  # near-duplicates left out so nothing is counted twice
    unreviewed: list[Fact] = []  # facts used that still need review
    conflicts: list[Conflict] = []


class Document(BaseModel):
    id: int
    path: str
    name: str
    doc_type: str | None
    indexed_at: str | None
    status: str
    facts: list[Fact] = []
    paths: list[str] = []  # every place this exact file is stored; the first is `path`
    near_duplicates: list[int] = []  # other documents holding the same record (e.g. a scan of the same bill)
    pages: int = 1


class UpcomingItem(BaseModel):
    fact: Fact
    document: str  # file name
    path: str
    doc_type: str | None
    days_left: int
    reminder_status: str | None = None  # None, "created"
    conflict: Conflict | None = None  # another document gives a different date


class ReviewItem(BaseModel):
    fact: Fact
    document: str
    path: str
    doc_type: str | None


class MissingFields(BaseModel):
    file_id: int
    name: str
    doc_type: str
    fields: list[str]


class Overview(BaseModel):
    total_files: int  # every indexed path, copies included
    documents: int  # unique documents
    by_type: dict[str, int]
    exact_copies: int  # extra paths holding a file that is already indexed
    near_duplicates: int  # documents that repeat another one's record
    expiring: dict[str, int]  # expiry and warranty dates within 30 / 60 / 90 days
    missing_required: list[MissingFields]
    needs_review: int  # facts
    conflicts: int
    errors: int  # files that could not be read
