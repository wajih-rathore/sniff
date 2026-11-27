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
    monkeypatch.setattr(ask, "understand", lambda conn, q, today: (Route(route="facts", **route), "model"))
    return ask.ask(conn, "question", today=TODAY)


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


# ---------- exact copies ----------
def test_exact_copy_is_one_document(conn, tmp_path, drain):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "bill.txt").write_text("Electricity bill for March. Amount due 1200.")
    counts = scan.scan(conn, tmp_path)
    drain()
    assert counts["indexed"] == 2 and counts["errors"] == 0
    assert count(conn, "SELECT count(*) FROM files") == 2
    assert count(conn, "SELECT count(*) FROM files WHERE copy_of IS NOT NULL") == 1
    assert count(conn, "SELECT count(*) FROM facts") == 1  # facts stored once
    assert len(search(conn, "electricity", mode="bm25")) == 1
    docs = TestClient(api.app).get("/documents").json()
    assert len(docs) == 1 and len(docs[0]["paths"]) == 2


def test_deleting_one_copy_keeps_the_document(conn, tmp_path, drain):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "bill.txt").write_text("Electricity bill. Amount due 1200.")
    scan.scan(conn, tmp_path)
    drain()
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


def test_copy_rename_and_change(conn, tmp_path, drain):
    (tmp_path / "a.txt").write_text("Electricity bill. Amount due 1200.")
    shutil.copy(tmp_path / "a.txt", tmp_path / "b.txt")
    scan.scan(conn, tmp_path)
    (tmp_path / "b.txt").rename(tmp_path / "c.txt")
    assert scan.scan(conn, tmp_path)["renamed"] == 1
    assert count(conn, "SELECT count(*) FROM files WHERE copy_of IS NOT NULL") == 1
    (tmp_path / "c.txt").write_text("Gas bill, now a different document")  # a changed copy becomes its own document
    assert scan.scan(conn, tmp_path)["indexed"] == 1
    drain()
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
    assert a.answer == "0 outstanding: the latest bills are marked paid."


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


def test_conflict_found_when_the_other_copy_fails_the_name_filter(conn, add_doc, monkeypatch):
    add_doc("/x/cnic.pdf", "id_card", {"name": "Hamza Khan", "id_number": "42101-7654321-3", "expiry_date": "2030-05-01"})
    add_doc("/x/cnic_scan.jpg", "id_card", {"name": "HAMZAKHAN", "id_number": "42101-7654321-3", "expiry_date": "2031-05-01"})  # OCR lost the space
    a = run(conn, monkeypatch, doc_type="id_card", field="expiry_date", op="list", filters=Filters(holder="hamza khan"))
    assert [(x.source.name, x.answer) for x in a.alternatives] == [("cnic.pdf", "2030-05-01 (cnic.pdf)."), ("cnic_scan.jpg", "2031-05-01 (cnic_scan.jpg).")]
    assert len(a.conflicts) == 1 and {v.value for v in a.conflicts[0].values} == {"2030-05-01", "2031-05-01"}


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
    assert [x.answer for x in a.alternatives] == ["2026-12-10 (cnic_old.jpg); 2026-12-20 (laptop.jpg).", "2026-12-20 (laptop.jpg)."]  # due dates are not expiries


# ---------- near-duplicates despite an OCR misread ----------
GAS = {"provider": "Metro Gas Ltd", "account_number": "MG-6421-9595"}


def gas_bills(add_doc, photo_dates=True):
    """The planted case of the acceptance test: September and October bills, and a phone photo of October whose
    billing month OCR read as July (values as the indexer extracted them)."""
    add_doc("/x/Metro Gas Sep 2026.pdf", "utility_bill", {**GAS, "billing_month": "2026-09", "bill_date": "2026-10-03", "due_date": "2026-10-15",
                                                          "current_charges": "3000.0", "tax": "150.0", "amount_due": "3000.0"})
    oct_ = {**GAS, "billing_month": "2026-10", "bill_date": "2026-11-04", "due_date": "2026-12-12", "current_charges": "5400.0",
            "previous_balance": "3000.0", "tax": "270.0", "amount_due": "8400.0"}
    add_doc("/x/Metro Gas Oct 2026.pdf", "utility_bill", oct_)
    photo = {**oct_, "billing_month": "2026-07"}
    if not photo_dates:
        photo = {k: v for k, v in photo.items() if k not in ("bill_date", "due_date")}
    return add_doc("/x/IMG-20261105-WA0412.jpg", "utility_bill", photo, statuses={"current_charges": "needs_review"})


def test_photo_with_a_misread_month_is_the_same_bill(conn, add_doc, monkeypatch):
    photo = gas_bills(add_doc)
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum", filters=Filters(provider="gas", year=2026))
    assert a.answer.startswith("8,400 in total, from 2 documents") and [s.file_id for s in a.skipped_duplicates] == [photo]
    client = TestClient(api.app)
    assert client.get("/overview").json()["near_duplicates"] == 1
    due = [i["fact"]["value"] for i in client.get("/upcoming", params={"days": 400}).json() if i["fact"]["field"] == "due_date"]
    assert due.count("2026-12-12") == 1  # the bill is listed once, not once per copy
    assert photo in client.get(f"/documents/{photo - 1}").json()["near_duplicates"]


def test_photo_with_unreadable_dates_is_the_same_bill(conn, add_doc, monkeypatch):
    gas_bills(add_doc, photo_dates=False)
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum", filters=Filters(provider="gas"))
    assert a.answer.startswith("8,400 in total, from 2 documents")


def test_same_amount_a_month_apart_is_another_bill(conn, add_doc, monkeypatch):
    for month, issued in (("2026-01", "2026-02-04"), ("2026-02", "2026-03-04")):  # a flat-rate internet bill
        add_doc(f"/x/net_{month}.pdf", "utility_bill", {"provider": "FastLink", "account_number": "FL-1", "billing_month": month,
                                                        "bill_date": issued, "current_charges": "3558.0", "amount_due": "3558.0"})
    a = run(conn, monkeypatch, doc_type="utility_bill", field="current_charges", op="sum")
    assert a.answer.startswith("7,116 in total, from 2 documents")


# ---------- conflicts: never both counted, never decided by code ----------
def vouchers(add_doc):
    v = {"student_name": "Zainab Malik", "registration_number": "2024-LU-FA-031", "institution": "Lakeshore University"}
    add_doc("/x/fall.pdf", "fee_voucher", {**v, "semester": "Fall 2024", "amount": "134400.0", "due_date": "2024-08-23"})
    first = add_doc("/x/spring.pdf", "fee_voucher", {**v, "semester": "Spring 2025", "amount": "134400.0", "due_date": "2025-01-27"})
    again = add_doc("/x/spring_reissued.pdf", "fee_voucher", {**v, "semester": "Spring 2025", "amount": "140000.0", "due_date": "2025-02-10"})
    return first, again


def test_two_disagreeing_copies_give_alternatives_not_a_double_count(conn, add_doc, monkeypatch):
    vouchers(add_doc)
    a = run(conn, monkeypatch, doc_type="fee_voucher", field="amount", op="sum", filters=Filters(holder="zainab"))
    assert [(x.source.name, x.answer) for x in a.alternatives] == [("spring.pdf", "268,800 in total, from 2 documents."),
                                                                  ("spring_reissued.pdf", "274,400 in total, from 2 documents.")]
    assert "408,800" not in a.answer and a.answer.startswith("Copies of one record disagree")
    assert {c.field for c in a.conflicts} == {"amount"} and all(x.key == a.conflicts[0].key for x in a.alternatives)


def test_a_conflict_that_does_not_change_the_answer_is_counted_once(conn, add_doc, monkeypatch):
    p = {"insurer": "Highway Motor Insurance Co.", "policy_number": "HM-1", "insured_name": "Bilal Ahmed", "premium": "47000.0", "start_date": "2025-05-10"}
    add_doc("/x/policy.pdf", "insurance_policy", {**p, "end_date": "2026-05-09"})
    add_doc("/x/policy_copy.pdf", "insurance_policy", {**p, "end_date": "2026-06-09"})
    a = run(conn, monkeypatch, doc_type="insurance_policy", field="premium", op="sum")
    assert a.answer == "47,000 in total, from 1 documents." and not a.alternatives


def test_picking_a_copy_settles_the_conflict(conn, add_doc, monkeypatch):
    first, again = vouchers(add_doc)
    client = TestClient(api.app)
    c = next(c for c in client.get("/conflicts").json() if c["field"] == "amount")
    chosen = next(v["fact_id"] for v in c["values"] if v["source"]["file_id"] == again)
    assert client.post("/conflicts/pick", json={"key": c["key"], "fact_id": 999}).status_code == 404
    left = client.post("/conflicts/pick", json={"key": c["key"], "fact_id": chosen}).json()
    assert [x["field"] for x in left] == ["due_date"]  # each field is its own choice
    statuses = dict(conn.execute("SELECT file_id, status FROM facts WHERE field='amount' AND file_id IN (?, ?)", (first, again)).fetchall())
    assert statuses == {first: "superseded", again: "approved"}
    d = next(d for d in left[0]["values"] if d["source"]["file_id"] == again)
    client.post("/conflicts/pick", json={"key": left[0]["key"], "fact_id": d["fact_id"]})
    assert client.get("/conflicts").json() == []
    a = run(conn, monkeypatch, doc_type="fee_voucher", field="amount", op="sum", filters=Filters(holder="zainab"))
    assert a.answer.startswith("274,400 in total, from 2 documents") and [s.file_id for s in a.skipped_duplicates] == [first]
    assert not a.alternatives and not a.conflicts


def test_mixed_picks_keep_each_chosen_value(conn, add_doc, monkeypatch):
    first, again = vouchers(add_doc)
    client = TestClient(api.app)
    for c in client.get("/conflicts").json():  # the reissued amount, but the first copy's due date
        want = again if c["field"] == "amount" else first
        client.post("/conflicts/pick", json={"key": c["key"], "fact_id": next(v["fact_id"] for v in c["values"] if v["source"]["file_id"] == want)})
    a = run(conn, monkeypatch, doc_type="fee_voucher", field="amount", op="sum", filters=Filters(holder="zainab"))
    assert a.answer.startswith("274,400 in total")
    a = run(conn, monkeypatch, doc_type="fee_voucher", field="due_date", op="list", filters=Filters(holder="zainab", year=2025))
    assert a.answer.startswith("2025-01-27 (")
