"""MCP limits: hidden document types, the field allow-list, masked ID numbers and the access log."""
import json

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from sniff import api, config, mcp_server
from sniff.schemas import Answer, Conflict, ConflictValue, Fact, Hit, Source


@pytest.fixture(autouse=True)
def mcp_on(conn, monkeypatch):
    # on, with full access to these four types on top of the default list: most tests below are about how the limits work
    monkeypatch.setattr(config, "MCP_ALLOW", {**config.MCP_ALLOW, **{t: ["*"] for t in ("utility_bill", "certificate", "receipt", "other")}})
    conn.execute("INSERT INTO settings(key, value) VALUES ('mcp', '{\"enabled\": true}')")
    conn.commit()


@pytest.fixture
def docs(conn, add_doc):
    card = add_doc("C:/docs/cnic.jpg", "id_card", {"name": "Ayesha Khan", "id_number": "35202-1234567-8"})
    bill = add_doc("C:/docs/bill.pdf", "utility_bill", {"provider": "LESCO", "account_number": "04112233344556", "amount_due": "1200.0"})
    conn.execute("INSERT INTO files(path, status, doc_type, copy_of) VALUES ('C:/backup/bill.pdf', 'indexed', 'utility_bill', ?)", (bill,))
    conn.commit()
    return card, bill


def hit(file_id, doc_type, snippet):
    return Hit(chunk_id=file_id, file_id=file_id, path=f"C:/docs/{file_id}", name=str(file_id), page=1, snippet=snippet, score=1.0, doc_type=doc_type)


def fact(file_id, field, value):
    return Fact(id=file_id * 10, file_id=file_id, field=field, value=value, value_type="text", page=1, verified=True)


def logged():
    return [json.loads(line) for line in (config.DATA_DIR / "mcp_access.log").read_text(encoding="utf-8").splitlines()]


def test_list_hides_id_cards_and_copies_and_masks_numbers(docs):
    card, bill = docs
    out = mcp_server.list_documents()
    assert [d["file"] for d in out] == ["C:/docs/bill.pdf"]
    assert out[0]["facts"]["account_number"] == "••••••••••4556" and out[0]["facts"]["provider"] == "LESCO"
    with pytest.raises(ToolError, match="Id card documents are hidden from agents"):  # the agent is told why
        mcp_server.list_documents("id_card")


def test_field_allow_list(docs, monkeypatch):
    monkeypatch.setattr(config, "MCP_ALLOW", {"utility_bill": ["provider"]})
    assert mcp_server.list_documents()[0]["facts"] == {"provider": "LESCO"}


def test_search_gives_no_page_text_for_hidden_types(docs, monkeypatch):
    card, bill = docs
    monkeypatch.setattr(mcp_server, "search", lambda conn, q, k: [hit(card, "id_card", "CNIC 35202-1234567-8"),
                                                                  hit(bill, "utility_bill", "Account 04112233344556"), hit(99, None, "unclassified")])
    out = mcp_server.search_documents("account")
    assert [o["type"] for o in out] == ["utility_bill"] and out[0]["snippet"] == "Account ••••••••••4556"
    monkeypatch.setattr(config, "MCP_ALLOW", {"utility_bill": ["provider"]})  # some fields only: no page text
    assert mcp_server.search_documents("account") == []


def test_ask_withholds_answers_built_on_hidden_documents(docs, monkeypatch):
    card, bill = docs
    src = lambda fid: Source(file_id=fid, path=f"C:/docs/{fid}", name=str(fid), page=1)
    answers = iter([
        Answer(question="q", answer="Your bill is 1200.", route="search", sources=[src(bill)]),  # cites the bill only...
        Answer(question="q", answer="Your bill is 1200.", route="search", sources=[src(bill)]),
        Answer(question="q", answer="Ayesha Khan, 35202-1234567-8.", route="facts", sources=[src(card)], facts=[fact(card, "id_number", "35202-1234567-8")]),
    ])
    monkeypatch.setattr(mcp_server.ask_mod, "ask", lambda conn, q: next(answers))
    passages = [[hit(bill, "utility_bill", ""), hit(card, "id_card", "")], [hit(bill, "utility_bill", "")]]
    monkeypatch.setattr(mcp_server, "search", lambda conn, q, k: passages.pop(0))
    assert mcp_server.ask("bill?")["answer"] == mcp_server.withheld({"id_card"})  # ...but the model also read the ID card's text
    assert "id card documents" in mcp_server.withheld({"id_card"})
    assert mcp_server.ask("bill?") == {"answer": "Your bill is 1200.", "route": "search", "sources": [f"C:/docs/{bill} (page 1)"]}
    assert mcp_server.ask("cnic?") == {"answer": mcp_server.withheld({"id_card"}), "route": "facts", "sources": []}


def test_a_guessed_type_stays_hidden_until_a_person_confirms_it(docs, conn, monkeypatch):
    card, bill = docs
    conn.execute("UPDATE files SET type_guessed=1 WHERE id=? OR copy_of=?", (bill, bill))  # as scan.read_facts sets it
    conn.commit()
    assert mcp_server.list_documents() == [] and mcp_server.list_documents("utility_bill") == []
    monkeypatch.setattr(mcp_server, "search", lambda conn, q, k: [hit(bill, "utility_bill", "Account 04112233344556")])
    assert mcp_server.search_documents("account") == []
    src = Source(file_id=bill, path="C:/docs/bill.pdf", name="bill.pdf", page=1)
    answers = [Answer(question="q", answer="Your bill is 1200.", route="facts", sources=[src], facts=[fact(bill, "amount_due", "1200.0")]),
               Answer(question="q", answer="1200 or 1300.", route="facts", conflicts=[Conflict(  # only a conflict names it
                   doc_type="utility_bill", key="k", field="amount_due", values=[ConflictValue(value="1200.0", fact_id=1, status="verified", source=src)])])]
    monkeypatch.setattr(mcp_server.ask_mod, "ask", lambda conn, q: answers.pop(0))
    assert mcp_server.ask("bill?")["answer"] == mcp_server.withheld({None})
    assert mcp_server.ask("bill?")["answer"] == mcp_server.withheld({None})

    fid = conn.execute("SELECT id FROM facts WHERE file_id=? AND field='amount_due'", (bill,)).fetchone()[0]
    assert TestClient(api.app).post(f"/facts/{fid}/approve").status_code == 200  # approving a fact confirms the type
    assert [d["file"] for d in mcp_server.list_documents()] == ["C:/docs/bill.pdf"]
    assert conn.execute("SELECT count(*) FROM files WHERE type_guessed").fetchone()[0] == 0  # its copy too


def test_correcting_a_fact_confirms_a_guessed_type(docs, conn):
    card, bill = docs
    conn.execute("UPDATE files SET type_guessed=1 WHERE id=?", (bill,))
    conn.commit()
    fid = conn.execute("SELECT id FROM facts WHERE file_id=? AND field='amount_due'", (bill,)).fetchone()[0]
    assert TestClient(api.app).patch(f"/facts/{fid}", json={"value": "1300"}).status_code == 200
    assert conn.execute("SELECT type_guessed FROM files WHERE id=?", (bill,)).fetchone()[0] == 0


def test_every_call_is_logged(docs, monkeypatch):
    card, bill = docs
    monkeypatch.setattr(mcp_server, "search", lambda conn, q, k: [hit(card, "id_card", "x"), hit(bill, "utility_bill", "y")])
    mcp_server.list_documents()
    mcp_server.search_documents("power bill", k=3)
    rows = logged()
    assert [(r["tool"], r["args"], r["ids"]) for r in rows] == [("list_documents", {"doc_type": None}, [bill]),
                                                               ("search_documents", {"query": "power bill", "k": 3}, [bill])]
    assert all(r["time"] for r in rows)


@pytest.mark.parametrize("text, masked", [
    ("Licence KHI-21-725642 valid", "Licence KHI-••-••5642 valid"),  # the licence the ui agent found unmasked
    ("CNIC 35202-1234567-8", "CNIC •••••-•••••67-8"),
    ("Account 04112233344556", "Account ••••••••••4556"),
    ("Due 2026-11-24 or 24/11/2026, 2024-2026, Rs. 1,250,000 and 4320.50", "Due 2026-11-24 or 24/11/2026, 2024-2026, Rs. 1,250,000 and 4320.50"),
    ("scan_2026-11-24.pdf", "scan_2026-11-24.pdf"),
    ("Staff card EMP-27986 issued 06-Jan-2025", "Staff card EMP-•7986 issued 06-Jan-2025"),  # the acceptance test's unmasked ID
    ("Roll 22K-45123, paid Rs12500 on 06Jan2025 and 12500 later", "Roll ••K-•5123, paid Rs12500 on 06Jan2025 and 12500 later"),
])
def test_mask_ids(text, masked):
    assert mcp_server.mask_ids(text) == masked


def test_unclassified_licence_is_masked_in_every_output(conn, monkeypatch):
    """A driving licence the classifier called 'other': its number must not leave through any tool."""
    fid = conn.execute("INSERT INTO files(path, doc_type, status) VALUES ('C:/docs/licence.jpg', 'other', 'indexed')").lastrowid
    conn.execute("INSERT INTO facts(file_id, field, value, value_type) VALUES (?, 'licence_no', 'KHI-21-725642', 'text')", (fid,))
    conn.commit()
    assert "KHI-21-725642" not in str(mcp_server.list_documents())
    monkeypatch.setattr(mcp_server, "search", lambda conn, q, k: [hit(fid, "other", "Licence No KHI-21-725642")])
    assert mcp_server.search_documents("licence")[0]["snippet"] == "Licence No KHI-••-••5642"
    monkeypatch.setattr(mcp_server.ask_mod, "ask", lambda conn, q: Answer(question=q, answer="It is KHI-21-725642.", route="search"))
    assert mcp_server.ask("licence number?")["answer"] == "It is KHI-••-••5642."


def test_new_types_show_only_non_identifying_fields(conn, add_doc):
    add_doc("C:/docs/fee.pdf", "fee_voucher", {"student_name": "Sara Ali", "registration_number": "22K-4512", "amount": "85000.0", "due_date": "2026-12-10"})
    add_doc("C:/docs/lease.pdf", "tenancy_agreement", {"landlord": "Imran Shah", "tenant": "Sara Ali", "property_address": "House 4, Street 9",
                                                       "monthly_rent": "60000.0", "end_date": "2027-06-30"})
    add_doc("C:/docs/policy.pdf", "insurance_policy", {"insurer": "EFU", "policy_number": "P-778812", "insured_name": "Sara Ali", "end_date": "2027-01-31"})
    add_doc("C:/docs/warranty.pdf", "warranty_card", {"product": "Fridge", "brand": "Dawlance", "serial_number": "DW99812", "warranty_until": "2028-03-01"})
    for t, fields in (("passport", {"passport_number": "AB1234567"}), ("bank_statement", {"bank": "HBL"}), ("offer_letter", {"employer": "Systems Ltd"}),
                      ("transcript", {"registration_number": "22K-4512"}), ("id_card", {"name": "Sara Ali"})):
        add_doc(f"C:/docs/{t}.pdf", t, fields)
    facts = {d["type"]: d["facts"] for d in mcp_server.list_documents()}
    assert facts == {"fee_voucher": {"amount": "85000.0", "due_date": "2026-12-10"}, "tenancy_agreement": {"monthly_rent": "60000.0", "end_date": "2027-06-30"},
                     "insurance_policy": {"insurer": "EFU", "end_date": "2027-01-31"}, "warranty_card": {"product": "Fridge", "brand": "Dawlance", "warranty_until": "2028-03-01"}}
    assert not any(mcp_server.text_allowed(t) for t in facts)  # a field list never gives page text
