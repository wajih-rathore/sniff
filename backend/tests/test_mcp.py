"""MCP limits: hidden document types, the field allow-list, masked ID numbers and the access log."""
import json

import pytest

from sniff import config, mcp_server
from sniff.schemas import Answer, Fact, Hit, Source


@pytest.fixture(autouse=True)
def mcp_on(conn):
    conn.execute("INSERT INTO settings(key, value) VALUES ('mcp', '{\"enabled\": true}')")  # on, with the default allow-list
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
    assert mcp_server.list_documents("id_card") == []


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
    assert mcp_server.ask("bill?")["answer"] == mcp_server.WITHHELD  # ...but the model also read the ID card's text
    assert mcp_server.ask("bill?") == {"answer": "Your bill is 1200.", "route": "search", "sources": [f"C:/docs/{bill} (page 1)"]}
    assert mcp_server.ask("cnic?") == {"answer": mcp_server.WITHHELD, "route": "facts", "sources": []}


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
