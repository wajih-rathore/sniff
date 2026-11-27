"""The optional AI layer: off until set up, what leaves the laptop (masked candidate lines, no hidden types), failing
closed, and each provider's request. Every provider call goes to a mocked HTTP transport: no real API is called and
no real key is used."""
import json
from datetime import date

import anthropic
import httpx
import httpx2
import keyring
import pytest
from openai import OpenAI

from sniff import ai_settings, ask, config
from sniff.ai import classify, extract, llm, server
from sniff.mcp_settings import mask_ids
from sniff.schemas import Hit
from test_xplat import MemoryKeyring

REAL_EXTRACT, REAL_CLASSIFY = extract.extract, classify.classify  # conftest's conn fixture replaces both with fakes
KEY = "sk-test-NOT-A-REAL-KEY-0123456789"
CNIC = "35202-1234567-8"
BILL = ("Northwind Electric Supply Co\nELECTRICITY BILL\nConsumer No: 04127788123\nBilling Month: October 2026\n"
        "Due Date: 15/11/2026\nAmount Due: Rs. 4,320\nCustomer CNIC " + CNIC + "   Current Charges: see overleaf\nThank you")


@pytest.fixture
def store(monkeypatch):
    s = MemoryKeyring()
    monkeypatch.setattr(keyring.core, "_keyring_backend", s)
    llm._build.cache_clear()  # a client built in another test holds that test's transport
    return s


class Sent(list):
    """Every request a provider client sent, as (url, headers, JSON body). reply(request) -> (status, JSON body) answers."""
    reply = staticmethod(lambda request: _openai_reply('{"current_charges": "Rs. 4,320"}'))


@pytest.fixture
def sent(monkeypatch, store):
    log = Sent()

    def handler(response_class):
        def handle(request):
            log.append((str(request.url), dict(request.headers), json.loads(request.content)))
            status, body = log.reply(request)
            return response_class(status, json=body)
        return handle
    real_openai, real_anthropic = OpenAI, anthropic.Anthropic
    monkeypatch.setattr(llm, "OpenAI", lambda **kw: real_openai(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(handler(httpx.Response)))))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: real_anthropic(
        **kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler(httpx2.Response)))))
    return log


def _openai_reply(text, status=200):
    return status, {"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def use(conn, provider, **kw):
    kw.setdefault("new_key", KEY if provider in ("anthropic", "openai") else None)
    if provider == "openai":
        kw.setdefault("base_url", "https://llm.example.test/v1")
        kw.setdefault("model", "small-model")
    return ai_settings.save(conn, provider, **kw)


def hits(*types):
    return [Hit(chunk_id=i, file_id=i, path=f"C:/d/{t}.txt", name=f"{t}.txt", page=1, snippet="", score=1.0, doc_type=t)
            for i, t in enumerate(types, 1)]


def add_chunks(conn, *texts):
    for i, t in enumerate(texts, 1):
        conn.execute("INSERT INTO files(id, path, status) VALUES (?, ?, 'indexed')", (i, f"C:/d/{i}.txt"))
        conn.execute("INSERT INTO chunks(id, file_id, page, text) VALUES (?, ?, 1, ?)", (i, i, t))
    conn.commit()


# ---------- off ----------
def test_off_by_default_and_nothing_is_called(conn, monkeypatch):
    def no_call(*a, **kw):
        raise AssertionError("no provider may be called while AI is off")
    monkeypatch.setattr(llm, "OpenAI", no_call)
    monkeypatch.setattr(llm, "client", no_call)
    monkeypatch.setattr(anthropic, "Anthropic", no_call)
    assert ai_settings.current()["provider"] == "off"

    facts = {f["field"]: f["value"] for f in REAL_EXTRACT("utility_bill", [(1, BILL)])}
    assert facts["amount_due"] == "4320.0" and "current_charges" not in facts  # the rules' facts, nothing more
    assert REAL_CLASSIFY([(1, BILL)]) is None  # stays unclassified
    assert llm.check() == {"ok": False, "error": "AI is off."}

    add_chunks(conn, "The recipe needs 3 eggs.")
    monkeypatch.setattr(ask, "search", lambda conn, q, k: hits("other")[:k])
    a = ask.ask(conn, "what does the recipe say?")
    assert a.route == "none" and a.answer.startswith("AI is off. A written answer needs AI.") and a.sources


# ---------- what leaves the laptop ----------
def test_only_masked_candidate_lines_are_sent(conn, sent):
    use(conn, "openai")
    facts = {f["field"]: f for f in REAL_EXTRACT("utility_bill", [(1, BILL)])}
    assert facts["current_charges"]["value"] == "4320.0"  # the model's value, checked by code against the page
    (url, headers, body), = sent
    assert url == "https://llm.example.test/v1/chat/completions" and body["model"] == "small-model"
    text = json.dumps(body["messages"], ensure_ascii=False)
    assert "Current Charges: see overleaf" in text and mask_ids(CNIC) in text and CNIC not in text
    assert "Northwind" not in text and "Meter" not in text and "Due Date" not in text  # never the whole document
    assert "temperature" not in body and "max_tokens" not in body


def test_hidden_types_are_never_sent_unless_allowed(conn, sent):
    card = "IDENTITY CARD\nName: Ayesha Fictional\nIdentity Number: see back\nExpiry: 01/01/2030"
    use(conn, "openai")
    REAL_EXTRACT("id_card", [(1, card)])
    assert sent == []
    use(conn, "openai", allow=["id_card"])
    REAL_EXTRACT("id_card", [(1, card)])
    assert len(sent) == 1


def test_a_document_without_its_labels_is_not_sent_whole(conn, sent):
    use(conn, "openai")
    REAL_EXTRACT("utility_bill", [(1, "Northwind Electric Supply Co\nhello")])
    assert sent == []


def test_a_remote_provider_never_sorts_whole_documents(conn, sent):
    use(conn, "openai")
    assert REAL_CLASSIFY([(1, BILL)]) is None and sent == []


def test_written_answers_leave_out_hidden_types(conn, sent, monkeypatch):
    use(conn, "openai")
    add_chunks(conn, "Passport No AB1234567, expires 2031", "The gas bill is due on 15 Nov 2026")
    monkeypatch.setattr(ask, "search", lambda conn, q, k: hits("passport", "utility_bill")[:k])
    sent.reply = lambda req: _openai_reply("It is due on 15 Nov 2026 [1].")
    a = ask.answer_from_search(conn, "when is it due?")
    body = json.dumps(sent[0][2]["messages"])
    assert "Passport" not in body and "gas bill" in body
    assert a.route == "search" and a.sources[0].name == "utility_bill.txt"


def test_ids_are_masked_in_every_message_sent(conn, sent):
    use(conn, "openai")
    llm.chat([{"role": "user", "content": f"My CNIC is {CNIC} and EMP-27986"}])
    assert sent[0][2]["messages"][0]["content"] == f"My CNIC is {mask_ids(CNIC)} and EMP-•7986"


# ---------- fail closed ----------
def _timeout(request):
    raise httpx.ReadTimeout("slow")


@pytest.mark.parametrize("reply", [lambda req: _openai_reply("", 500), _timeout])
def test_a_failed_call_keeps_the_rules_facts(conn, sent, monkeypatch, reply):
    use(conn, "openai")
    sent.reply = reply
    facts = {f["field"]: f["value"] for f in REAL_EXTRACT("utility_bill", [(1, BILL)])}
    assert facts["amount_due"] == "4320.0" and "current_charges" not in facts
    add_chunks(conn, "The recipe needs 3 eggs.")
    monkeypatch.setattr(ask, "search", lambda conn, q, k: hits("other")[:k])
    use(conn, "openai", allow=["other"])
    a = ask.ask(conn, "what does the recipe say?")
    assert a.route == "none" and "A written answer needs AI" in a.answer
    events = list(ask.ask_stream(conn, "what does the recipe say?"))
    assert events[-1][0] == "answer" and events[-1][1].route == "none"


def test_timeouts_are_set_and_retries_are_few(conn, store):
    use(conn, "openai")
    p = llm.provider()
    assert p.client.timeout == llm.TIMEOUT and p.client.max_retries == 1
    use(conn, "anthropic")
    p = llm.provider()
    assert p.client.timeout == llm.TIMEOUT and p.client.max_retries == 1


# ---------- Anthropic ----------
def test_anthropic_request_shape(conn, sent):
    use(conn, "anthropic")
    sent.reply = lambda req: (200, {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"current_charges": "Rs. 4,320"}'}], "usage": {"input_tokens": 1, "output_tokens": 1}})
    facts = {f["field"]: f["value"] for f in REAL_EXTRACT("utility_bill", [(1, BILL)])}
    assert facts["current_charges"] == "4320.0"
    (url, headers, body), = sent
    assert url.startswith("https://api.anthropic.com/v1/messages") and headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "claude-haiku-5-5" and "temperature" not in body
    assert body["system"] == llm.DATA_RULE and [m["role"] for m in body["messages"]] == ["user"]
    assert body["output_config"]["format"]["type"] == "json_schema" and body["max_tokens"] >= 1024
    assert CNIC not in json.dumps(body)


def test_anthropic_without_a_key_is_unavailable(conn, store):
    ai_settings.save(conn, "anthropic")
    assert llm.check() == {"ok": False, "error": "No API key is saved."}


def test_hidden_types_are_the_ones_mcp_hides_by_default():
    assert set(ai_settings.HIDDEN) == {"id_card", "passport", "bank_statement", "offer_letter", "transcript", "other"}


def test_sniff_ai_overrides_the_saved_provider(conn, monkeypatch):
    monkeypatch.setenv("SNIFF_AI", "local")  # the evaluation's switch: it measures this laptop's model
    assert ai_settings.current()["provider"] == "local" and not ai_settings.remote(ai_settings.current())


def test_documents_kept_from_the_provider_are_named_not_reported_missing(conn, sent, monkeypatch):
    use(conn, "openai")
    add_chunks(conn, "Passport No AB1234567, expires 2031")
    monkeypatch.setattr(ask, "search", lambda conn, q, k: hits("passport")[:k])
    a = ask.answer_from_search(conn, "when does it expire?")
    assert sent == [] and a.route == "none" and a.answer.startswith("Your settings keep these documents from the AI provider.")
    assert [s.name for s in a.sources] == ["passport.txt"]


def test_an_openai_compatible_provider_without_url_or_model_says_so(conn, store):
    ai_settings.save(conn, "openai")  # saved field by field from the app
    assert llm.check() == {"ok": False, "error": "Set the base URL and the model first."}


# ---------- one provider per call chain ----------
@pytest.fixture
def local_then_cloud(conn, sent):
    """AI is on this laptop's model; the returned function moves it to a cloud provider, as a user could at any moment."""
    config.MODELS_DIR.mkdir()
    (config.MODELS_DIR / server.MODEL_FILE).touch()
    ai_settings.save(conn, "local")
    return lambda: use(conn, "openai")


def test_a_retry_never_goes_to_a_provider_the_privacy_check_did_not_see(conn, sent, monkeypatch, local_then_cloud):
    calls = []

    def local_chat(self, messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            local_then_cloud()  # the user switches to the cloud while the local model's first reply is read
            return "{"  # cut off by the token cap: chat_json asks again
        return '{"id_number": null}'
    monkeypatch.setattr(llm.Local, "chat", local_chat)
    REAL_EXTRACT("id_card", [(1, "IDENTITY CARD\nName: Ayesha Fictional\nIdentity Number: see back\nExpiry: 01/01/2030")])
    assert len(calls) == 2 and sent == []  # the retry went to the same local model; the cloud got nothing


def test_a_written_answer_goes_to_the_provider_its_passages_were_chosen_for(conn, sent, monkeypatch, local_then_cloud):
    add_chunks(conn, "Passport No AB1234567, expires 2031")

    def search_then_switch(conn, q, k):
        local_then_cloud()  # the user switches while the passages are being chosen
        return hits("passport")[:k]
    monkeypatch.setattr(ask, "search", search_then_switch)
    monkeypatch.setattr(llm.Local, "chat", lambda self, messages, **kw: "It expires in 2031 [1].")
    ask.answer_from_search(conn, "when does it expire?")
    assert sent == []  # the passport passage, chosen for the local model, never reached the cloud


# ---------- replies that are not plain JSON ----------
def test_json_in_a_markdown_fence_is_read_the_first_time(conn, sent):
    use(conn, "openai")
    sent.reply = lambda req: _openai_reply('```json\n{"doc_type": "receipt"}\n```')
    assert llm.chat_json([{"role": "user", "content": "Sort it."}], classify.TypeOut).doc_type == "receipt"
    assert len(sent) == 1


def test_a_router_that_never_answers_in_json_sends_the_question_to_search(conn, sent):
    use(conn, "openai")
    sent.reply = lambda req: _openai_reply("Sorry, I can only answer in prose.")
    r, how = ask.understand(conn, "tell me about the electricity bill", date(2026, 11, 28))
    assert r.route == "search" and how == "rules" and len(sent) == 3
