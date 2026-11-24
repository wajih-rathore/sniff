"""Every field rule and cross-field rule (docs/FIELDS.md). Pure code, no model."""
from datetime import date

import pytest

from sniff.verify import cross_problems, field_problem, parse, spend

D = date


@pytest.mark.parametrize("kind,raw,want", [
    ("date", "21.03.2003", D(2003, 3, 21)),
    ("date", "05/04/2026", D(2026, 4, 5)),
    ("date", "12 Mar 2026", D(2026, 3, 12)),
    ("date", "10th day of July, 2025", D(2025, 7, 10)),
    ("date", "sometime soon", None),
    ("month", "March 2026", "2026-03"),
    ("month", "Mar-2026", "2026-03"),
    ("month", "03/2026", "2026-03"),
    ("money", "Rs. 4,320/-", 4320.0),
    ("money", "PKR 1848.50", 1848.5),
    ("money", "n/a", None),
    ("status", "PAID", "paid"),
    ("status", "Payment status: Unpaid", "unpaid"),
    ("status", "pending", None),
    ("id", "35202 - 1234567 - 1", "35202-1234567-1"),
])
def test_parse(kind, raw, want):
    assert parse(kind, raw) == want


@pytest.mark.parametrize("kind,value,required,problem", [
    ("date", D(2026, 1, 1), False, None),
    ("date", D(2103, 3, 21), False, "date is not plausible"),
    ("month", "2026-13", False, "not a month"),
    ("money", 4320.5, True, None),
    ("money", 0.0, False, None),  # no arrears is printed as 0
    ("money", 0.0, True, "amount is not positive"),
    ("money", -5.0, False, "amount is not positive"),
    ("money", 12.345, False, "amount has more than 2 decimals"),
    ("id", "35202-1234567-1", True, None),
    ("id", "LHR-23-123456", True, None),
    ("id", "3520212345671", True, "13-digit ID number is not in CNIC form #####-#######-#"),
    ("id", "Ali Raza Khan", True, "does not look like an ID or account number"),
    ("status", "maybe", False, "status is not paid or unpaid"),
    ("text", "Lakeshore University", True, None),
    ("text", "--", True, "text is empty or too long"),
])
def test_field_rules(kind, value, required, problem):
    assert field_problem(kind, value, required) == problem


def reasons(doc_type, **v):
    return [why for _, why in cross_problems(doc_type, v)]


def test_bill_dates():
    assert reasons("utility_bill", bill_date=D(2026, 4, 3), due_date=D(2026, 4, 1)) == ["due date before bill date"]
    assert reasons("utility_bill", bill_date=D(2026, 2, 28), billing_month="2026-03") == ["bill date before the billing month"]
    assert reasons("utility_bill", bill_date=D(2026, 4, 3), payment_date=D(2026, 4, 1)) == ["payment date before bill date"]
    assert reasons("utility_bill", bill_date=D(2026, 4, 3), due_date=D(2026, 4, 20), billing_month="2026-03", payment_date=D(2026, 4, 10)) == []


def test_bill_amounts_add_up():
    ok = "amount due is not current charges + previous balance (+ tax)"
    assert reasons("utility_bill", current_charges=5000.0, previous_balance=1200.0, amount_due=6200.0) == []
    assert reasons("utility_bill", current_charges=5000.0, amount_due=5000.4) == []  # within 1
    assert reasons("utility_bill", current_charges=5000.0, tax=900.0, amount_due=5900.0) == []  # tax listed outside current charges
    assert reasons("utility_bill", current_charges=5000.0, previous_balance=1200.0, amount_due=5000.0) == [ok]


def test_spend_adds_tax_only_when_outside_current_charges():
    assert spend({"current_charges": 5000.0, "tax": 500.0, "amount_due": 5000.0}) == 5000.0
    assert spend({"current_charges": 5000.0, "tax": 500.0, "amount_due": 5500.0}) == 5500.0
    assert spend({"current_charges": 5000.0, "previous_balance": 800.0, "tax": 500.0, "amount_due": 5800.0}) == 5000.0


def test_id_card_dates():
    assert reasons("id_card", issue_date=D(2027, 1, 1), expiry_date=D(2017, 1, 1)) == ["expiry date not after issue date"]
    assert reasons("id_card", date_of_birth=D(2020, 1, 1), issue_date=D(2017, 1, 1)) == ["date of birth not before issue date"]
    assert reasons("id_card", date_of_birth=D(2003, 3, 21), issue_date=D(2021, 1, 1), expiry_date=D(2031, 1, 1)) == []


def test_receipt_rules():
    assert reasons("receipt", subtotal=1000.0, tax=170.0, total_amount=1170.0) == []
    assert reasons("receipt", subtotal=1000.0, total_amount=1000.0) == []
    assert reasons("receipt", subtotal=1000.0, tax=170.0, total_amount=1000.0) == ["total is not subtotal + tax"]
    assert reasons("receipt", date=D(2026, 5, 1), warranty_until=D(2026, 1, 1)) == ["warranty ends before the purchase date"]


def test_missing_values_skip_the_rule():
    assert reasons("utility_bill", due_date=D(2026, 4, 1)) == []
    assert reasons("receipt", total_amount=500.0) == []
