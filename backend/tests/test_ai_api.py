"""The AI routes: GET and PUT /ai/settings (validated, the key write-only), POST /ai/test and GET /ai/preview, which
returns exactly what extraction or a question sends. Provider calls go to the mocked transport of test_ai_providers."""
import json
import logging

import keyring
import pytest
from fastapi.testclient import TestClient

from sniff import api, ask, config
from sniff.ai import llm
from sniff.ingest import readers
from test_ai_providers import CNIC, KEY, BILL, REAL_EXTRACT, _openai_reply, add_chunks, hits, sent, store, use  # noqa: F401 (fixtures)


# ---------- the preview shows exactly what is sent ----------
def test_preview_of_a_document_is_what_extraction_sends(conn, sent, tmp_path):
    path = tmp_path / "bill.txt"
    path.write_text(BILL, encoding="utf-8")
    conn.execute("INSERT INTO files(id, path, doc_type, status) VALUES (1, ?, 'utility_bill', 'indexed')", (str(path),))
    conn.commit()
    client = TestClient(api.app)

    off = client.get("/ai/preview", params={"file_id": 1}).json()  # off: what a provider off this laptop would get
    assert off["provider"] == "off" and off["leaves_laptop"] and off["calls"][0]["purpose"].endswith("current_charges")
    use(conn, "openai")
    preview = client.get("/ai/preview", params={"file_id": 1}).json()
    REAL_EXTRACT("utility_bill", *readers.read_layout(path))
    assert preview["calls"][0]["messages"] == sent[0][2]["messages"] == off["calls"][0]["messages"]


def test_preview_of_a_question(conn, sent, monkeypatch):
    use(conn, "openai")
    add_chunks(conn, f"Passport {CNIC}", f"Gas bill, consumer {CNIC}")
    monkeypatch.setattr(ask, "search", lambda conn, q, k: hits("passport", "utility_bill")[:k])
    r = TestClient(api.app).get("/ai/preview", params={"question": "what does the gas bill say?"}).json()
    passages = json.dumps(r["calls"][-1]["messages"], ensure_ascii=False)
    assert "Gas bill" in passages and "Passport" not in passages and CNIC not in passages
    assert sent == []  # a preview calls nobody
    assert TestClient(api.app).get("/ai/preview").status_code == 422


# ---------- the key ----------
def test_key_is_write_only_and_never_leaks(conn, sent, caplog):
    caplog.set_level(logging.DEBUG)
    client = TestClient(api.app)
    assert client.get("/ai/settings").json()["key_saved"] is False
    r = client.put("/ai/settings", json={"provider": "openai", "base_url": "https://llm.example.test/v1", "model": "m", "key": KEY})
    assert r.status_code == 200 and r.json()["key_saved"] is True and KEY not in r.text
    assert KEY not in client.get("/ai/settings").text
    assert keyring.get_password("Sniff", "ai-openai") == KEY  # in the password store, and nowhere in the data folder
    assert not [p for p in config.DATA_DIR.iterdir() if KEY.encode() in p.read_bytes()]

    # a refused key: the provider's error quotes it, Sniff's never does
    sent.reply = lambda req: (401, {"error": {"message": f"Incorrect API key provided: {KEY}"}})
    r = client.post("/ai/test")
    assert r.json() == {"ok": False, "error": "The provider refused the key."}
    with pytest.raises(llm.Unavailable) as e:
        llm.chat([{"role": "user", "content": "hi"}])
    assert KEY not in str(e.value) and e.value.__cause__ is None
    assert sent and sent[-1][1]["authorization"] == f"Bearer {KEY}"  # sent to the provider, as it must be
    assert KEY not in caplog.text

    r = client.put("/ai/settings", json={"provider": "openai", "base_url": "https://llm.example.test/v1", "model": "m", "key": ""})
    assert r.json()["key_saved"] is False and keyring.get_password("Sniff", "ai-openai") is None


def test_test_reports_success(conn, sent):
    use(conn, "openai")
    sent.reply = lambda req: _openai_reply("OK")
    assert TestClient(api.app).post("/ai/test").json() == {"ok": True}


@pytest.mark.parametrize("body,error", [
    ({"provider": "gpt"}, "Unknown provider"),
    ({"provider": "openai", "base_url": "ftp://x", "model": "m"}, "must start with https://"),
    ({"provider": "openai", "base_url": "http://llm.example.test/v1", "model": "m"}, "only for a server on this laptop"),
    ({"provider": "openai", "base_url": "http://192.168.1.5:8080/v1", "model": "m"}, "only for a server on this laptop"),
    ({"provider": "anthropic", "allow": ["receipt"]}, "Not a hidden document type"),
    ({"provider": "local"}, "not downloaded"),
])
def test_settings_are_validated(conn, store, body, error):
    r = TestClient(api.app).put("/ai/settings", json=body)
    assert r.status_code == 422 and error in r.json()["detail"]


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://127.0.0.1:8080/v1", "http://[::1]:1234/v1"])
def test_plain_http_is_fine_for_a_server_on_this_laptop(conn, store, url):
    r = TestClient(api.app).put("/ai/settings", json={"provider": "openai", "base_url": url, "model": "m"})
    assert r.status_code == 200 and r.json()["base_url"] == url


def test_the_key_never_follows_a_new_address(conn, sent):
    client = TestClient(api.app)
    put = lambda url, **kw: client.put("/ai/settings", json={"provider": "openai", "base_url": url, "model": "small-model", **kw})
    put("", key=KEY)
    put("https://llm.example.test/v1")  # the key was typed before its address: it is for this one
    put("https://llm.example.test/v2")  # the same server
    assert client.get("/ai/settings").json()["key_saved"] is True
    put("https://other.example.test/v1")  # another server (from OpenAI to Groq, or a page that rewrote the address)
    assert client.get("/ai/settings").json()["key_saved"] is False
    client.post("/ai/test")
    assert sent and all(h.get("authorization") != f"Bearer {KEY}" for _, h, _ in sent)
    put("https://other.example.test/v1", key=KEY)
    put("")  # clearing the address drops it too, so it cannot be kept for the next address
    assert client.get("/ai/settings").json()["key_saved"] is False


def test_without_a_password_store_the_key_is_not_saved_anywhere(conn):
    # conftest.py selects keyring's "fail" backend: a system without a store
    r = TestClient(api.app).put("/ai/settings", json={"provider": "anthropic", "key": KEY})
    assert r.status_code == 422 and "password store" in r.json()["detail"] and KEY not in r.text
    assert conn.execute("SELECT 1 FROM settings WHERE key='ai'").fetchone() is None
