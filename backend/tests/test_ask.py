"""Functional tests for F5 (answers computed by code) and F6 (reminders only after approval)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from sniff import api, ask, gcal
from sniff.ask import Filters, Route

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


def test_next_expiring_skips_expired(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="next_expiring"))
    assert a.answer == "id_sara.jpg: expiry date 2026-12-05, in 13 days."


def test_holder_filter(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="ali")))
    assert a.answer == "2027-01-10 (id_ali.jpg)."


def test_invalid_template_falls_back_to_search(docs, monkeypatch):
    called = []
    monkeypatch.setattr(ask, "answer_from_search", lambda conn, q: called.append(q) or "searched")
    assert run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="amount_due", op="sum")) == "searched"
    assert run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="outstanding")) == "searched"
    assert len(called) == 2


def test_no_match_says_so(docs, monkeypatch):
    a = run(docs, monkeypatch, Route(route="facts", doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="Nobody")))
    assert a.answer == "No matching documents found." and not a.sources


def test_reminder_only_after_approval(docs, monkeypatch):
    created = []
    monkeypatch.setattr(gcal, "create_event", lambda title, day, desc: created.append((title, day)) or "evt1")
    client = TestClient(api.app)  # db.connect() uses the fixture's temp DB path
    items = client.get("/upcoming", params={"days": 400}).json()
    assert items and not created  # listing never creates events
    fact_id = items[0]["fact"]["id"]
    r = client.post("/reminders", json={"fact_id": fact_id})
    assert r.status_code == 200 and len(created) == 1
    assert client.get("/upcoming", params={"days": 400}).json()[0]["reminder_status"] == "created"
