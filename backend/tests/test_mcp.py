"""MCP limits: hidden document types, the field allow-list, masked ID numbers and the access log."""
import json

import pytest

from sniff import config, mcp_server
from sniff.schemas import Answer, Fact, Hit, Source


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
