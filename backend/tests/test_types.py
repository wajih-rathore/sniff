"""The eight A4 document types: schema, field rules, cross-field rules, label checks, extraction parsing and the
calculations on their facts. Pure code with fakes; no model server."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from sniff import api, ask, gcal, verify
from sniff.ai import classify, extract, llm
from sniff.ask import Filters, Route
from sniff.schemas import ACTION_DATE_FIELDS, DOC_TYPES, EXPIRY_FIELDS, FACT_MODELS, FIELDS, HOLDER_FIELDS, KEYS, ORG_FIELDS

D = date
TODAY = date(2026, 11, 22)
NEW = ("passport", "transcript", "bank_statement", "warranty_card", "offer_letter", "insurance_policy", "tenancy_agreement", "fee_voucher")


# ---------- schema ----------
def test_twelve_types_with_fields_and_keys():
    assert len(FIELDS) == 12 and set(NEW) <= set(FIELDS) and DOC_TYPES[-1] == "other"
    for t, fields in FIELDS.items():
        assert set(KEYS[t]) <= set(fields), t
        assert all(s.anchors and s.meaning for s in fields.values()), t
        assert any(s.required for s in fields.values()), t
    names = {f for fs in FIELDS.values() for f in fs}
    assert set(ACTION_DATE_FIELDS) | set(EXPIRY_FIELDS) | set(HOLDER_FIELDS) | set(ORG_FIELDS) <= names
    assert set(ask.PERIOD_FIELD) == set(FIELDS) and all(ask.PERIOD_FIELD[t] in FIELDS[t] for t in FIELDS)
    assert FACT_MODELS["transcript"](cgpa=3.5).cgpa == 3.5


def test_classifier_and_router_accept_the_new_types(monkeypatch):
    assert classify.TypeOut(doc_type="fee_voucher").doc_type == "fee_voucher"
    assert Route(route="facts", doc_type="bank_statement", field="closing_balance", op="latest").doc_type == "bank_statement"
    for t in NEW:
        assert f"- {t}:" in classify.PROMPT
    seen = []
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: seen.append(messages) or model(doc_type="passport"))
    assert classify.classify([(1, "REPUBLIC OF EASTLAND PASSPORT")]) == "passport"


# ---------- reading and checking one value ----------
@pytest.mark.parametrize("kind,raw,want", [
    ("passport", "ab 1234567", "AB1234567"),
    ("gpa", "3.45 / 4.00", 3.45),
    ("gpa", "CGPA: 3.5", 3.5),
    ("serial", "KV47C 1234567", "KV47C 1234567"),
    ("serial", "SN - 4471", "SN-4471"),
])
def test_parse_new_kinds(kind, raw, want):
    assert verify.parse(kind, raw) == want


@pytest.mark.parametrize("kind,value,problem", [
    ("passport", "AB1234567", None),
    ("passport", "X1234", "not a passport number (6 to 9 letters and digits)"),
    ("passport", "PASSPORT", "not a passport number (6 to 9 letters and digits)"),
    ("serial", "KV47C1234567", None),
    ("serial", "SN-4471", None),
    ("serial", "Serial", "not a serial number (4 to 30 letters and digits)"),
    ("gpa", 3.45, None),
    ("gpa", 4.0, None),
    ("gpa", 4.2, "CGPA is not between 0 and 4"),
    ("gpa", 0.0, "CGPA is not between 0 and 4"),
    ("gpa", 3.456, "CGPA is not between 0 and 4"),
])
def test_new_field_rules(kind, value, problem):
    assert verify.field_problem(kind, value, True) == problem


def reasons(doc_type, **v):
    return [why for _, why in verify.cross_problems(doc_type, v)]


def test_passport_rules():
    ok = dict(date_of_birth=D(1978, 5, 14), issue_date=D(2016, 4, 14))
    assert reasons("passport", **ok, expiry_date=D(2026, 4, 14)) == []
    assert reasons("passport", **ok, expiry_date=D(2021, 4, 13)) == []  # the day before the 5-year anniversary
    assert reasons("passport", **ok, expiry_date=D(2023, 4, 14)) == ["passport is not valid for 5 or 10 years"]
    assert reasons("passport", **ok, expiry_date=D(2015, 1, 1)) == ["expiry date not after issue date"]
    assert reasons("passport", date_of_birth=D(2020, 1, 1), issue_date=D(2016, 4, 14)) == ["date of birth not before issue date"]
    assert verify.passport_term_ok(D(2024, 2, 29), D(2029, 2, 28))  # leap day


def test_rules_of_the_other_new_types():
    assert reasons("transcript", issue_date=D(2025, 7, 15)) == []
    assert reasons("transcript", issue_date=date.today() + timedelta(days=30)) == ["issue date is in the future"]
    bank = dict(period_start=D(2026, 3, 1), period_end=D(2026, 3, 31), opening_balance=1000.0, total_credits=500.0, total_debits=300.0)
    assert reasons("bank_statement", **bank, closing_balance=1200.0) == []
    assert reasons("bank_statement", **bank, closing_balance=1250.0) == ["opening balance + credits - debits is not the closing balance"]
    assert reasons("bank_statement", period_start=D(2026, 3, 31), period_end=D(2026, 3, 1)) == ["period start not before period end"]
    assert reasons("warranty_card", purchase_date=D(2024, 3, 2), warranty_until=D(2034, 3, 2)) == []
    assert reasons("warranty_card", purchase_date=D(2024, 3, 2), warranty_until=D(2035, 3, 2)) == ["warranty longer than 10 years"]
    assert reasons("warranty_card", purchase_date=D(2024, 3, 2), warranty_until=D(2024, 1, 1)) == ["warranty ends before the purchase date"]
    assert reasons("offer_letter", offer_date=D(2026, 10, 5), start_date=D(2026, 10, 5)) == []
    assert reasons("offer_letter", offer_date=D(2026, 10, 5), start_date=D(2026, 9, 1)) == ["start date before the offer date"]
    for t in ("insurance_policy", "tenancy_agreement"):
        assert reasons(t, start_date=D(2026, 7, 1), end_date=D(2026, 7, 1)) == ["end date not after start date"]
    assert reasons("fee_voucher", issue_date=D(2026, 1, 28), due_date=D(2026, 2, 10)) == []
    assert reasons("fee_voucher", issue_date=D(2026, 1, 28), due_date=D(2026, 1, 10)) == ["due date before issue date"]
    assert reasons("fee_voucher", due_date=D(2026, 1, 10)) == []  # missing values skip the rule


# ---------- labels next to values ----------
def check(doc_type, text, **raw):
    facts = [{"field": f, "raw": r, "value": verify.parse(FIELDS[doc_type][f].kind, r)} for f, r in raw.items()]
    verify.check(doc_type, facts, [(1, text)], {})
    return {f["field"]: f for f in facts}


def test_labels_pick_the_right_amount_and_gpa():
    voucher = ("NORTHFIELD COLLEGE\nFEE VOUCHER\nStudent Name: Hamza Khan\nRegistration No: 2025-NC-SE-089\nSemester: Fall 2026\n"
               "Due Date: 21/08/2026\nTuition Fee 72,000\nExam Fee 2,900\nTotal Payable 74,900\nPayable after due date 76,400")
    got = check("fee_voucher", voucher, student_name="Hamza Khan", registration_number="2025-NC-SE-089", institution="Northfield College",
                semester="Fall 2026", due_date="21/08/2026", amount="74,900")
    assert all(g["verified"] for g in got.values()), {f: g["reason"] for f, g in got.items()}
    assert not check("fee_voucher", voucher, amount="76,400")["amount"]["verified"]  # the late amount
    transcript = "LAKESHORE UNIVERSITY\nFall 2024\nSemester GPA: 3.67\nSpring 2025\nSemester GPA: 3.33\nCumulative GPA (CGPA): 3.52 / 4.00"
    assert check("transcript", transcript, cgpa="3.52")["cgpa"]["verified"]
    assert "Semester GPA" in check("transcript", transcript, cgpa="3.67")["cgpa"]["reason"]
    offer = "Brightwave Systems (Pvt) Ltd\nDate: 04/08/2025\nGross Monthly Salary: Rs. 95,000\nBasic Salary: Rs. 57,000"
    assert check("offer_letter", offer, monthly_salary="95,000")["monthly_salary"]["verified"]
    assert not check("offer_letter", offer, monthly_salary="57,000")["monthly_salary"]["verified"]
    policy = "CareFirst Health Insurance Ltd\nSum Insured: Rs. 1,500,000\nAnnual Premium: Rs. 89,000"
    assert check("insurance_policy", policy, premium="89,000")["premium"]["verified"]
    assert not check("insurance_policy", policy, premium="1,500,000")["premium"]["verified"]


def test_statement_period_on_one_line():
    text = "Crescent Bank Limited\nAccount No.: 1234-56789012-34\nStatement Period: 01/03/2026 to 31/03/2026\nClosing Balance: 434,060.00"
    got = check("bank_statement", text, bank="Crescent Bank Limited", account_number="1234-56789012-34", period_start="01/03/2026",
                period_end="31/03/2026", closing_balance="434,060.00")
    assert all(g["verified"] for g in got.values()), {f: g["reason"] for f, g in got.items()}
    swapped = check("bank_statement", text, period_start="31/03/2026", period_end="01/03/2026")
    assert not swapped["period_start"]["verified"] and not swapped["period_end"]["verified"]


def test_extraction_of_a_new_type(monkeypatch):
    page = ("SafeNest Home Insurance\nPOLICY SCHEDULE\nPolicy No.: SN-HC-2026-118204\nPolicy Holder: Ali Raza Khan\n"
            "Period of Insurance: From 01/11/2026 To 31/10/2027\nSum Insured: Rs. 2,500,000\nAnnual Premium: Rs. 18,900")
    printed = dict(insurer="SafeNest Home Insurance", policy_number="SN-HC-2026-118204", insured_name="Ali Raza Khan",
                   premium="Rs. 18,900", start_date="01/11/2026", end_date="31/10/2027")
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: model(**printed))
    facts = {f["field"]: f for f in extract.extract("insurance_policy", [(1, page)])}
    assert facts["premium"]["value"] == "18900.0" and facts["premium"]["value_type"] == "money"
    assert facts["end_date"]["value"] == "2027-10-31" and facts["policy_number"]["value_type"] == "id"
    assert all(f["status"] == "verified" for f in facts.values()), {k: f["reason"] for k, f in facts.items()}


# ---------- calculations ----------
@pytest.fixture
def docs(conn, add_doc):
    a = {"bank": "Crescent Bank Limited", "account_number": "1234-56789012-34"}
    for end, close in [("2026-08-31", "500000.0"), ("2026-09-30", "520000.0"), ("2026-10-31", "540000.5")]:
        add_doc(f"/x/cres_{end}.pdf", "bank_statement", {**a, "period_end": end, "closing_balance": close, "total_credits": "285000.0"})
    add_doc("/x/unity.pdf", "bank_statement", {"bank": "Unity Bank Limited", "account_number": "9876-54321098-76",
                                               "period_end": "2026-10-31", "closing_balance": "60000.0", "total_credits": "140000.0"})
    for no, issue, exp in [("AB1234567", "2016-04-14", "2026-04-14"), ("CD7654321", "2026-02-20", "2036-02-20")]:
        add_doc(f"/x/pass_{no}.jpg", "passport", {"name": "Ali Raza Khan", "passport_number": no, "issue_date": issue, "expiry_date": exp})
    v = {"student_name": "Hamza Khan", "registration_number": "2025-NC-SE-089", "institution": "Northfield College"}
    add_doc("/x/fee_spring.jpg", "fee_voucher", {**v, "semester": "Spring 2026", "amount": "69000.0", "due_date": "2026-01-24"})
    add_doc("/x/fee_spring_new.jpg", "fee_voucher", {**v, "semester": "Spring 2026", "amount": "69000.0", "due_date": "2026-02-10"})
    add_doc("/x/fee_2027.pdf", "fee_voucher", {**v, "semester": "Spring 2027", "amount": "73000.0", "due_date": "2027-01-12"})
    add_doc("/x/lease.pdf", "tenancy_agreement", {"landlord": "Nusrat Khan", "tenant": "Danish Rauf", "monthly_rent": "63500.0",
                                                  "start_date": "2026-07-01", "end_date": "2027-05-31"})
    add_doc("/x/policy.pdf", "insurance_policy", {"insurer": "GlobeGuard Travel Insurance", "policy_number": "GG-TR-2026-1",
                                                  "insured_name": "Hamza Khan", "start_date": "2026-12-10", "end_date": "2026-12-30"})
    return conn


def run(conn, monkeypatch, r: Route):
    monkeypatch.setattr(ask, "route", lambda q: r)
    return ask.ask(conn, "question", today=TODAY)


def test_bank_balance_is_the_newest_statement_of_each_account(docs, monkeypatch):
    for op in ("sum", "latest"):  # adding every statement's balance would count the same money three times
        a = run(docs, monkeypatch, Route(route="facts", doc_type="bank_statement", field="closing_balance", op=op))
        assert a.answer.startswith("600,000.50 in total, from the newest statement of each account"), a.answer
        assert len(a.counted) == 2
    a = run(docs, monkeypatch, Route(route="facts", doc_type="bank_statement", field="closing_balance", op="sum",
                                     filters=Filters(provider="crescent")))
    assert a.answer.startswith("540,000.50 in total")
    a = run(docs, monkeypatch, Route(route="facts", doc_type="bank_statement", field="total_credits", op="sum",
                                     filters=Filters(provider="crescent", date_from="September 2026", date_to="October 2026")))
    assert a.answer == "570,000 in total, from 2 documents."


def test_latest_takes_the_newest_document(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="passport", field="passport_number", op="latest", filters=Filters(holder="Ali")))
    assert a.answer == "CD7654321 (pass_CD7654321.jpg), from the newest document (2036-02-20)."
    a = run(docs, monkeypatch, Route(route="facts", doc_type="tenancy_agreement", field="monthly_rent", op="latest",
                                     filters=Filters(holder="Nusrat")))  # Nusrat is the landlord
    assert a.answer.startswith("63,500 (lease.pdf)")


def test_fee_vouchers_by_due_date_and_their_conflict(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="fee_voucher", field="due_date", op="next_expiring", filters=Filters(holder="Hamza")))
    assert a.answer == "fee_2027.pdf: due date 2027-01-12, in 51 days."
    a = run(docs, monkeypatch, Route(route="facts", doc_type="fee_voucher", field="amount", op="list", filters=Filters(month="January 2027")))
    assert a.answer == "73,000 (fee_2027.pdf)."
    a = run(docs, monkeypatch, Route(route="facts", doc_type="fee_voucher", field="due_date", op="list", filters=Filters(year=2026)))
    assert "2026-01-24" in a.answer and "2026-02-10" in a.answer and "Conflict: the due date" in a.answer
    assert len(a.conflicts) == 1 and a.conflicts[0].field == "due_date"


def test_expiry_across_types_includes_end_dates(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", field="end_date", op="list",
                                     filters=Filters(date_from="1 December 2026", date_to="31 January 2027")))
    assert a.answer == "2026-12-30 (policy.pdf)."


def test_upcoming_and_reminders_cover_new_dates(docs, monkeypatch):
    created = []
    monkeypatch.setattr(gcal, "create_event", lambda title, day, desc: created.append(title) or "evt")
    client = TestClient(api.app)
    items = client.get("/upcoming", params={"days": 400}).json()
    fields = {(i["doc_type"], i["fact"]["field"]) for i in items}
    assert {("fee_voucher", "due_date"), ("insurance_policy", "end_date"), ("tenancy_agreement", "end_date")} <= fields
    end = next(i for i in items if i["doc_type"] == "insurance_policy")
    assert client.post("/reminders", json={"fact_id": end["fact"]["id"]}).status_code == 200
    assert created == ["Insurance policy ends (GlobeGuard Travel Insurance)"]
