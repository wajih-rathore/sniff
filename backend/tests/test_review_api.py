"""The review and dashboard API: document detail, page images, corrections, approvals, the review queue, overview."""
from datetime import date, timedelta

import pymupdf
import pytest
from fastapi.testclient import TestClient

from sniff import api


@pytest.fixture
def client(conn):
    return TestClient(api.app)


@pytest.fixture
def bill(conn, add_doc, tmp_path):
    path = tmp_path / "bill.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Electricity bill")
    doc.new_page().insert_text((50, 50), "Page two")
    doc.save(str(path))
    fid = add_doc(str(path), "utility_bill", {"provider": "Northern Electric Supply Co.", "billing_month": "2026-03",
                                              "bill_date": "2026-04-05", "due_date": "2026-04-20", "amount_due": "5000.0"},
                  statuses={"amount_due": "needs_review"})
    conn.execute("UPDATE files SET size=?, mtime=? WHERE id=?", (path.stat().st_size, path.stat().st_mtime, fid))  # not stale
    conn.execute("UPDATE facts SET reason='found but not next to a ''Amount Due'' label', source_text='Rs. 5,000' WHERE field='amount_due'")
    conn.execute("INSERT INTO chunks(file_id, page, text) VALUES (?, 2, 'Page two')", (fid,))
    conn.execute("INSERT INTO files(path, status, doc_type, copy_of) VALUES (?, 'indexed', 'utility_bill', ?)", (str(tmp_path / "copy.pdf"), fid))
    conn.commit()
    return fid


def fact_id(conn, field):
    return conn.execute("SELECT id FROM facts WHERE field=?", (field,)).fetchone()[0]


def test_document_detail(client, bill, tmp_path):
    d = client.get(f"/documents/{bill}").json()
    assert d["paths"] == [str(tmp_path / "bill.pdf"), str(tmp_path / "copy.pdf")] and d["pages"] == 2
    amount = next(f for f in d["facts"] if f["field"] == "amount_due")
    assert amount["status"] == "needs_review" and "Amount Due" in amount["reason"] and amount["source_text"] == "Rs. 5,000"
    copy_id = client.get("/documents").json()[0]["id"]
    assert copy_id == bill and len(client.get("/documents").json()) == 1  # the copy is not listed separately
    assert client.get("/documents/999").status_code == 404


def test_page_image(client, bill, conn, add_doc, tmp_path):
    r = client.get(f"/documents/{bill}/pages/2.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get(f"/documents/{bill}/pages/3.png").status_code == 404
    (tmp_path / "note.txt").write_text("hello")
    note = add_doc(str(tmp_path / "note.txt"), "receipt", {"merchant": "BookNook"})
    assert client.get(f"/documents/{note}/pages/1.png").status_code == 404


def test_correct_a_fact(client, bill, conn):
    r = client.patch(f"/facts/{fact_id(conn, 'amount_due')}", json={"value": "Rs. 5,250/-"})
    assert r.status_code == 200 and r.json()["value"] == "5250.0" and r.json()["status"] == "corrected" and r.json()["reason"] is None
    assert client.patch(f"/facts/{fact_id(conn, 'due_date')}", json={"value": "next week"}).status_code == 422
    assert client.patch(f"/facts/{fact_id(conn, 'amount_due')}", json={"value": "12.345"}).status_code == 422
    r = client.patch(f"/facts/{fact_id(conn, 'due_date')}", json={"value": "01/04/2026"})  # before the bill date: kept, flagged
    assert r.json()["value"] == "2026-04-01" and r.json()["status"] == "corrected" and r.json()["reason"] == "due date before bill date"
    assert client.patch("/facts/999", json={"value": "1"}).status_code == 404


def test_approve_and_review_queue(client, bill, conn):
    queue = client.get("/review").json()
    assert [q["fact"]["field"] for q in queue] == ["amount_due"]
    r = client.post(f"/facts/{fact_id(conn, 'amount_due')}/approve")
    assert r.json()["status"] == "approved" and r.json()["reason"] is None
    assert client.get("/review").json() == []


def test_corrected_value_is_used_by_calculations(client, bill, conn, monkeypatch):
    from sniff import ask
    from sniff.ask import Route
    client.patch(f"/facts/{fact_id(conn, 'amount_due')}", json={"value": "6000"})
    monkeypatch.setattr(ask, "route", lambda q: Route(route="facts", doc_type="utility_bill", field="amount_due", op="max"))
    a = client.post("/ask", json={"question": "highest bill?"}).json()
    assert a["answer"].startswith("6,000") and a["facts"][0]["status"] == "corrected"


def test_overview(client, bill, conn, add_doc):
    soon = (date.today() + timedelta(days=20)).isoformat()
    add_doc("/x/id.jpg", "id_card", {"name": "Sara Khan", "id_number": "11111-1111111-1", "expiry_date": soon})
    add_doc("/x/id_photo.jpg", "id_card", {"name": "Sara Khan", "id_number": "11111-1111111-1", "expiry_date": soon})
    add_doc("/x/id2.jpg", "id_card", {"name": "Ali Khan", "id_number": "22222-2222222-2"})  # no expiry: missing required field
    conn.execute("INSERT INTO files(path, status, error) VALUES ('/x/broken.pdf', 'error', 'bad file')")
    conn.commit()
    o = client.get("/overview").json()
    assert o["total_files"] == 5 and o["documents"] == 4 and o["exact_copies"] == 1 and o["near_duplicates"] == 1
    assert o["by_type"] == {"utility_bill": 1, "id_card": 3}
    assert o["expiring"] == {"30": 1, "60": 1, "90": 1}
    missing = {m["name"]: m["fields"] for m in o["missing_required"]}
    assert missing["id2.jpg"] == ["expiry_date"] and "account_number" in missing["bill.pdf"]
    assert o["needs_review"] == 1 and o["conflicts"] == 0 and o["errors"] == 1
