"""Duplicates (exact copies and near-duplicates), the calculation engine (arrears, tax, outstanding) and conflicts."""
import shutil
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from sniff import api, ask, records
from sniff.ask import Filters, Route
from sniff.ingest import scan
from sniff.search import search

TODAY = date(2026, 11, 22)
ELEC = {"provider": "Northern Electric Supply Co.", "account_number": "ELC-1"}


def run(conn, monkeypatch, **route):
    monkeypatch.setattr(ask, "route", lambda q: Route(route="facts", **route))
    return ask.ask(conn, "question", today=TODAY)


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


# ---------- exact copies ----------
def test_exact_copy_is_one_document(conn, tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "bill.txt").write_text("Electricity bill for March. Amount due 1200.")
    counts = scan.scan(conn, tmp_path)
    assert counts["indexed"] == 2 and counts["errors"] == 0
    assert count(conn, "SELECT count(*) FROM files") == 2
    assert count(conn, "SELECT count(*) FROM files WHERE copy_of IS NOT NULL") == 1
    assert count(conn, "SELECT count(*) FROM facts") == 1  # facts stored once
    assert len(search(conn, "electricity", mode="bm25")) == 1
    docs = TestClient(api.app).get("/documents").json()
    assert len(docs) == 1 and len(docs[0]["paths"]) == 2


def test_deleting_one_copy_keeps_the_document(conn, tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "bill.txt").write_text("Electricity bill. Amount due 1200.")
    scan.scan(conn, tmp_path)
    original = conn.execute("SELECT id, path FROM files WHERE copy_of IS NULL").fetchone()
    conn.execute("UPDATE facts SET status='corrected', value='1250.0'")  # a correction must survive
    conn.commit()
    shutil.rmtree(Path(original["path"]).parent)  # delete the copy that holds the rows
    assert scan.scan(conn, tmp_path)["removed"] == 1
    row = conn.execute("SELECT id, copy_of FROM files").fetchone()
    assert row["copy_of"] is None and row["id"] != original["id"]
    fact = conn.execute("SELECT file_id, value, status FROM facts").fetchone()
    assert tuple(fact) == (row["id"], "1250.0", "corrected")
    assert len(search(conn, "electricity", mode="bm25")) == 1


def test_copy_rename_and_change(conn, tmp_path):
    (tmp_path / "a.txt").write_text("Electricity bill. Amount due 1200.")
    shutil.copy(tmp_path / "a.txt", tmp_path / "b.txt")
    scan.scan(conn, tmp_path)
    (tmp_path / "b.txt").rename(tmp_path / "c.txt")
    assert scan.scan(conn, tmp_path)["renamed"] == 1
    assert count(conn, "SELECT count(*) FROM files WHERE copy_of IS NOT NULL") == 1
    (tmp_path / "c.txt").write_text("Gas bill, now a different document")  # a changed copy becomes its own document
    assert scan.scan(conn, tmp_path)["indexed"] == 1
    assert count(conn, "SELECT count(*) FROM files WHERE copy_of IS NOT NULL") == 0
    assert count(conn, "SELECT count(DISTINCT file_id) FROM facts") == 2


# ---------- near-duplicates and money ----------
def bill(add_doc, path, month, current, prev="0.0", tax=None, status="verified", paid=None, statuses=None):
    due = float(current) + float(prev)
    f = {**ELEC, "billing_month": month, "current_charges": current, "previous_balance": prev, "amount_due": f"{due}"}
    if tax:
        f["tax"] = tax
    if paid:
        f["payment_status"] = paid
    return add_doc(path, "utility_bill", f, status=status, statuses=statuses)


def test_near_duplicate_counted_once_preferring_the_verified_copy(conn, add_doc, monkeypatch):
    bill(add_doc, "/x/jan.pdf", "2026-01", "3000.0")
    scan_copy = bill(add_doc, "/x/jan_photo.jpg", "2026-01", "3800.0", status="needs_review")  # misread amount
    bill(add_doc, "/x/feb.pdf", "2026-02", "2000.0")
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum", filters=Filters(provider="electric"))
    assert a.answer.startswith("5,000 in total, from 2 documents")
    assert [s.file_id for s in a.skipped_duplicates] == [scan_copy] and not a.unreviewed
    a = run(conn, monkeypatch, doc_type="utility_bill", field="amount_due", op="count")
    assert a.answer.startswith("2 documents")


def test_corrected_copy_wins_and_unreviewed_values_are_listed(conn, add_doc, monkeypatch):
    first = bill(add_doc, "/x/jan.pdf", "2026-01", "3000.0", status="needs_review")
    fixed = bill(add_doc, "/x/jan_scan.pdf", "2026-01", "3100.0", status="needs_review", statuses={"current_charges": "corrected"})
    lonely = bill(add_doc, "/x/feb.pdf", "2026-02", "2000.0", status="needs_review")
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum")
    assert a.answer.startswith("5,100 in total")  # the corrected value is the one used
    assert [s.file_id for s in a.skipped_duplicates] == [first]
    assert [f.file_id for f in a.unreviewed] == [lonely] and "1 of the values used needs review" in a.answer
    assert {s.file_id for s in a.counted} == {fixed, lonely}


def test_annual_spend_counts_current_charges_not_arrears(conn, add_doc, monkeypatch):
    bill(add_doc, "/x/jun.pdf", "2026-06", "9000.0")  # not paid, so July carries it as arrears
    bill(add_doc, "/x/jul.pdf", "2026-07", "8000.0", prev="9000.0")
    bill(add_doc, "/x/aug.pdf", "2026-08", "7000.0", tax="700.0")
    a = run(conn, monkeypatch, doc_type="utility_bill", field="amount_due", op="sum", filters=Filters(year=2026))
    assert a.answer.startswith("24,000 in total, from 3 documents") and "not counted twice" in a.answer
    a = run(conn, monkeypatch, doc_type="utility_bill", field="tax", op="sum")
    assert a.answer.startswith("700 in total")


def test_tax_outside_current_charges_is_added(conn, add_doc, monkeypatch):
    add_doc("/x/sep.pdf", "utility_bill", {**ELEC, "billing_month": "2026-09", "current_charges": "5000.0", "tax": "800.0", "amount_due": "5800.0"})
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum")
    assert a.answer.startswith("5,800 in total") and {f.field for f in a.facts} == {"current_charges", "tax"}


def test_outstanding_is_the_latest_unpaid_bill(conn, add_doc, monkeypatch):
    bill(add_doc, "/x/oct.pdf", "2026-10", "5000.0")
    bill(add_doc, "/x/nov.pdf", "2026-11", "4000.0", prev="5000.0")
    add_doc("/x/gas.pdf", "utility_bill", {"provider": "Metro Gas", "account_number": "MG-1", "billing_month": "2026-11",
                                           "current_charges": "800.0", "amount_due": "800.0", "payment_status": "paid"})
    a = run(conn, monkeypatch, doc_type="utility_bill", field="amount_due", op="outstanding")
    assert a.answer.startswith("9,000 outstanding") and [s.name for s in a.counted] == ["nov.pdf"]
    a = run(conn, monkeypatch, doc_type="utility_bill", field="amount_due", op="outstanding", filters=Filters(provider="gas"))
    assert a.answer == "Nothing outstanding: the latest bills are marked paid."


# ---------- conflicts ----------
def ids(add_doc):
    add_doc("/x/cnic_old.jpg", "id_card", {"name": "Ali Raza Khan", "id_number": "35202-1234567-1", "expiry_date": "2026-12-10"})
    add_doc("/x/cnic_new.pdf", "id_card", {"name": "ALI RAZA KHAN", "id_number": "35202-1234567-1", "expiry_date": "2036-12-10"})
    add_doc("/x/cnic_copy.jpg", "id_card", {"name": "Ali Raza Khan", "id_number": "35202 1234567 1", "expiry_date": "2036-12-10"},
            status="needs_review")  # a photo of the new card: a near-duplicate, not a third value


def test_conflict_shows_both_values(conn, add_doc, monkeypatch):
    ids(add_doc)
    res = records.resolve(records.load(conn))
    assert len(res.kept) == 2 and len(res.skipped) == 1
    assert [(c.field, sorted(v.value for v in c.values)) for c in res.conflicts] == [("expiry_date", ["2026-12-10", "2036-12-10"])]
    a = run(conn, monkeypatch, doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="ali"))
    assert "2026-12-10" in a.answer and "2036-12-10" in a.answer and "Conflict:" in a.answer
    assert len(a.conflicts) == 1 and len(a.conflicts[0].values) == 2


def test_conflict_in_api_and_upcoming(conn, add_doc, monkeypatch):
    ids(add_doc)
    client = TestClient(api.app)
    assert len(client.get("/conflicts").json()) == 1
    items = client.get("/upcoming", params={"days": 400}).json()  # real today: 2026-12-10 is within 400 days
    soon = [i for i in items if i["fact"]["value"] == "2026-12-10"]
    assert soon and soon[0]["conflict"]["field"] == "expiry_date"


def test_expiring_window_across_types(conn, add_doc, monkeypatch):
    ids(add_doc)
    add_doc("/x/laptop.jpg", "receipt", {"merchant": "Metro Electronics Mart", "date": "2025-12-01", "total_amount": "95000.0",
                                         "warranty_until": "2026-12-20"})
    add_doc("/x/bill.pdf", "utility_bill", {**ELEC, "billing_month": "2026-11", "due_date": "2026-12-15", "current_charges": "1.0", "amount_due": "1.0"})
    a = run(conn, monkeypatch, doc_type=None, field="expiry_date", op="list",
            filters=Filters(date_from="1 December 2026", date_to="31 December 2026"))
    assert a.answer.startswith("2026-12-10 (cnic_old.jpg); 2026-12-20 (laptop.jpg).")  # due dates are not expiries
