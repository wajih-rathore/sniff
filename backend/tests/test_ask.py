"""Functional tests for F5 (answers computed by code) and F6 (reminders only after approval)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from sniff import api, ask, gcal
from sniff.ask import Filters, Route
from sniff.schemas import Answer

TODAY = date(2026, 11, 22)


@pytest.fixture
def docs(conn, add_doc):
    for month, amount in [("2026-01", "3000.0"), ("2026-02", "2500.5"), ("2025-12", "9999.0")]:
        add_doc(f"/x/bill_{month}.pdf", "utility_bill", {"provider": "Northern Electric Supply Co.", "account_number": "ELC-1",
                                                         "billing_month": month, "current_charges": amount, "amount_due": amount})
    add_doc("/x/gas.pdf", "utility_bill", {"provider": "Metro Gas", "billing_month": "2026-01", "current_charges": "700.0", "amount_due": "700.0"})
    add_doc("/x/id_ali.jpg", "id_card", {"name": "Ali Khan", "id_number": "11111-1111111-1", "expiry_date": "2027-01-10"})
    add_doc("/x/id_sara.jpg", "id_card", {"name": "Sara Khan", "id_number": "22222-2222222-2", "expiry_date": "2026-12-05"})
    add_doc("/x/id_old.jpg", "id_card", {"name": "Omar Khan", "id_number": "33333-3333333-3", "expiry_date": "2026-03-01"})
    return conn


def run(conn, monkeypatch, r: Route):
    monkeypatch.setattr(ask, "route", lambda q: r)
    return ask.ask(conn, "question", today=TODAY)


def test_sum_with_provider_and_year(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                     filters=Filters(provider="electric", year=2026)))
    assert a.route == "facts" and a.answer.startswith("5,500.50 in total, from 2 documents")
    assert len(a.sources) == 2 and len(a.counted) == 2


def test_period_is_resolved_by_code(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="amount_due", op="count",
                                     filters=Filters(period="last_year")))
    assert a.answer == "1 documents."


def test_month_and_date_range_filters(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                     filters=Filters(provider="electric", month="February 2026")))
    assert a.answer.startswith("2,500.50 in total, from 1 documents")
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                     filters=Filters(provider="electric", date_from="December 2025", date_to="January 2026")))
    assert a.answer.startswith("12,999 in total, from 2 documents")


def test_names_from_the_question(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                     filters=Filters(provider="electricity", year=2026)))  # the bill says "Electric"
    assert a.answer.startswith("5,500.50 in total, from 2 documents")
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="amount_due", op="list",
                                     filters=Filters(holder="gas")))  # bills name no person: holder means the company
    assert a.answer == "700 (gas.pdf)."


def test_amount_due_of_one_bill_is_not_switched(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="utility_bill", field="amount_due", op="sum",
                                     filters=Filters(provider="electric", month="January 2026")))
    assert a.answer == "3,000 in total, from 1 documents."  # one bill per account: nothing can be counted twice


def test_router_expiry_on_a_type_without_one_means_every_type(monkeypatch):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: Route(route="facts", doc_type="utility_bill", field="expiry_date", op="list"))
    r = ask.route("What expires between 1 December 2026 and 31 January 2027?")
    assert r.doc_type is None and ask.valid(r)


@pytest.mark.parametrize("question, slip", [  # router outputs seen on the dev split, 25 Nov
    ("How much is my gas bill?", Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum")),
    ("How much was my gas bill?", Route(route="facts", doc_type="receipt", field="amount_due", op="sum", filters=Filters(provider="gas", year=2023))),
    ("What is my gas bill?", Route(route="facts", doc_type="receipt", field="utility_bill", op="sum", filters=Filters(holder="my"))),
    ("How much do I owe for gas?", Route(route="facts", doc_type="receipt", field="amount_due", op="outstanding",
                                         filters=Filters(provider="gas", year=2023, month="March"))),
    ("How much did I pay for gas?", Route(route="facts", doc_type="receipt", field="amount_due", op="sum", filters=Filters(provider="gas", date_from="today"))),
    ("How much was my gas bill?", Route(route="facts", doc_type="utility_bill", field="amount_due", op="sum", filters=Filters(provider="gas", period="this_month"))),
])
def test_router_slips_on_utility_bills_are_repaired(docs, monkeypatch, question, slip):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: slip.model_copy(deep=True))
    a = ask.ask(docs, question, today=TODAY)
    assert a.route == "facts" and [s.name for s in a.sources] == ["gas.pdf"] and "700" in a.answer


def test_utility_words_pick_the_provider(docs, monkeypatch):
    from sniff.ai import llm
    bare = Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum")
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: bare.model_copy(deep=True))
    assert ask.ask(docs, "How much did we spend on electricity bills?", today=TODAY).answer.startswith("15,499.50 in total, from 3 documents")
    assert ask.ask(docs, "How much was our water bill?", today=TODAY).answer == "No matching documents found."  # no water bill: say so
    r = ask.route("How much did the phone cost?")  # a phone on a receipt is not a phone bill
    assert r.doc_type == "utility_bill" and r.filters.provider is None
    kept = Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum", filters=Filters(year=2026, month="January 2026"))
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: kept.model_copy(deep=True))
    r = ask.route("What did we pay for gas in January 2026?")
    assert (r.filters.year, r.filters.month, r.filters.provider) == (2026, "January 2026", "gas")
    kept.filters = Filters(period="last_month")
    assert ask.route("What did we pay for gas last month?").filters.period == "last_month"


def test_next_expiring_skips_expired(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="next_expiring"))
    assert a.answer == "id_sara.jpg: expiry date 2026-12-05, in 13 days."


def test_holder_filter(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="ali")))
    assert a.answer == "2027-01-10 (id_ali.jpg)."


def test_invalid_template_falls_back_to_search(docs, monkeypatch):
    called = []
    monkeypatch.setattr(ask, "answer_from_search", lambda conn, q: called.append(q) or Answer(question=q, answer="searched", route="search"))
    assert run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="amount_due", op="sum")).answer == "searched"
    assert run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="outstanding")).answer == "searched"
    assert len(called) == 2


def test_no_match_says_so(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="Nobody")))
    assert a.answer == "No matching documents found." and not a.sources


def test_reminder_only_after_approval(docs, monkeypatch):
    created = []
    monkeypatch.setattr(gcal, "create_event", lambda title, day, desc: created.append((title, day, desc)) or "evt1")
    client = TestClient(api.app)  # db.connect() uses the fixture's temp DB path
    items = client.get("/upcoming", params={"days": 400}).json()
    assert items and not created  # listing never creates events
    fact_id = items[0]["fact"]["id"]
    r = client.post("/reminders", json={"fact_id": fact_id})
    assert r.status_code == 200 and len(created) == 1
    assert "/" not in created[0][2] and "\\" not in created[0][2]  # only the file name leaves the laptop
    assert client.get("/upcoming", params={"days": 400}).json()[0]["reminder_status"] == "created"


# ---------- "How much is my gas bill?" means the latest bill ----------
@pytest.mark.parametrize("question, slip, answer", [
    ("How much is my electricity bill?", Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum"),
     "2,500.50 (bill_2026-02.pdf), from the newest document (2026-02)."),
    ("What is my electricity bill?", Route(route="facts", doc_type="utility_bill", field="amount_due", op="list", filters=Filters(provider="electric")),
     "2,500.50 (bill_2026-02.pdf), from the newest document (2026-02)."),
    ("How much is my gas bill?", Route(route="facts", doc_type="utility_bill", field="amount_due", op="sum", filters=Filters(provider="gas")),
     "700 (gas.pdf), from the newest document (2026-01)."),
])
def test_a_bill_with_no_period_is_the_latest_bill(docs, monkeypatch, question, slip, answer):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: slip.model_copy(deep=True))
    a = ask.ask(docs, question, today=TODAY)
    assert a.answer == answer and a.query["op"] == "latest" and a.query["field"] == "amount_due" and len(a.sources) == 1


@pytest.mark.parametrize("question, slip", [
    ("How much did we pay for electricity in 2026?", Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                                           filters=Filters(provider="electric", year=2026))),
    ("What is the total of my electricity bills?", Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum")),
    ("How much did we spend on electricity last month?", Route(route="facts", doc_type="utility_bill", field="current_charges", op="sum",
                                                               filters=Filters(period="last_month"))),
])
def test_totals_need_total_words_or_a_period(docs, monkeypatch, question, slip):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: slip.model_copy(deep=True))
    assert ask.route(question).op == "sum"


# ---------- rules first, model second ----------
def no_model(*a, **kw):
    raise AssertionError("the rules should have answered this without the model")


@pytest.mark.parametrize("question, answer", [
    ("How much did we spend on electricity in 2026?", "5,500.50 in total, from 2 documents."),
    ("How many ID cards do I have?", "3 documents."),
    ("What is the newest electricity bill amount?", "2,500.50 (bill_2026-02.pdf), from the newest document (2026-02)."),
    ("When does Sara's ID card expire?", "2026-12-05 (id_sara.jpg)."),
    ("Which ID card expires next?", "id_sara.jpg: expiry date 2026-12-05, in 13 days."),
    ("What expires in the next 20 days?", "2026-12-05 (id_sara.jpg)."),
    ("List my gas bills.", "700 (gas.pdf)."),
    ("When does Zara's ID card expire?", "No matching documents found."),  # a name the index does not have filters to nothing
])
def test_rules_answer_common_questions_without_the_model(docs, monkeypatch, question, answer):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", no_model)
    a = ask.ask(docs, question, today=TODAY)
    assert a.answer == answer and a.parser == "rules"


def test_names_are_matched_against_the_indexed_facts(docs, add_doc, monkeypatch):
    from sniff.ai import llm
    monkeypatch.setattr(llm, "chat_json", no_model)
    add_doc("/x/id_scan.jpg", "id_card", {"name": "OMARFAROOQ", "id_number": "44444-4444444-4", "expiry_date": "2028-01-06"})  # OCR joined the words
    a = ask.ask(docs, "When does Omar Farooq's card expire?", today=TODAY)
    assert a.answer == "2028-01-06 (id_scan.jpg)." and a.query["filters"]["holder"] == "omar farooq"


def test_when_reads_times_in_code():
    today = date(2026, 11, 30)
    assert ask.when("what expires in the next 3 months", today) == {"date_from": "2026-11-30", "date_to": "2027-02-28"}
    assert ask.when("anything due within the next 2 weeks", today) == {"date_from": "2026-11-30", "date_to": "2026-12-14"}
    assert ask.when("money out from january to march 2025", today) == {"date_from": "january 2025", "date_to": "march 2025"}
    assert ask.when("what did i spend last month", today) == {"period": "last_month"}
    assert ask.when("bills due next month", today) == {"date_from": "2026-12-01", "date_to": "2026-12-31"}
    assert ask.when("my gas bill in december", today) == {"month": "december 2025"}  # no year: the last December up to today
    assert ask.when("receipts from 2025", today) == {"year": 2025}
    assert ask.when("may i see my receipts", today) == {}  # "may" is a month only next to a year


def test_model_output_is_checked_against_the_question(docs, monkeypatch):
    from sniff.ai import llm
    slip = Route(route="facts", doc_type="id_card", field="expiry_date", op="list", filters=Filters(year=2023, provider="NADRA"))
    monkeypatch.setattr(llm, "chat_json", lambda messages, model: slip.model_copy(deep=True))
    a = ask.ask(docs, "Ali Khan ID card?", today=TODAY)  # no operation word: the rules leave it to the model
    assert a.parser == "model" and a.answer == "2027-01-10 (id_ali.jpg)."
    f = a.query["filters"]
    assert f["year"] is None and f["provider"] is None and f["holder"] == "ali khan"  # invented year and name dropped, the dropped name put back
