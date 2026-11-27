"""Two-phase indexing (findable first, facts from a background queue), the streamed answer and the trimmed passages."""
import json
import time

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from sniff import api, ask, db
from sniff.ai import classify, extract, llm
from sniff.ask import Route
from sniff.ingest import readers, scan

BILL = "Electricity bill for March. Amount due 1200."


def wait_for(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def dump(conn):
    """Everything indexing stores about the documents, without row ids and times."""
    files = [tuple(r) for r in conn.execute("SELECT path, sha256, doc_type, status, error, copy_of IS NOT NULL FROM files ORDER BY path")]
    chunks = [tuple(r) for r in conn.execute("SELECT f.path, c.page, c.text FROM chunks c JOIN files f ON f.id=c.file_id ORDER BY f.path, c.id")]
    facts = [tuple(r) for r in conn.execute("SELECT f.path, fa.field, fa.value, fa.value_type, fa.page, fa.verified, fa.source_text, fa.status, fa.reason "
                                            "FROM facts fa JOIN files f ON f.id=fa.file_id ORDER BY f.path, fa.id")]
    return files, chunks, facts


# ---------- the queue ----------
def test_file_is_findable_before_its_facts_and_status_counts_the_queue(conn, tmp_path, drain):
    (tmp_path / "a.txt").write_text(BILL)
    scan.scan(conn, tmp_path)
    assert conn.execute("SELECT doc_type FROM files").fetchone()[0] is None
    assert conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    hit = api.search(conn, "electricity", mode="bm25")[0]
    assert hit.name == "a.txt" and hit.reading
    assert TestClient(api.app).get("/index/status").json()["queued"] == 1
    drain()
    assert TestClient(api.app).get("/index/status").json()["queued"] == 0
    assert not api.search(conn, "electricity", mode="bm25")[0].reading


def test_queue_resumes_after_restart(conn, tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(BILL + " " + name)
    scan.scan(conn, tmp_path)
    conn.close()  # the app stops with two files queued

    later = db.connect()  # ... and starts again: the queue is a table, so nothing is lost
    assert scan.waiting(later) == 2
    worker = scan.FactsWorker(idle=0)
    worker.start()
    try:
        assert wait_for(lambda: scan.waiting(later) == 0)
    finally:
        worker.stop()
    assert [r[0] for r in later.execute("SELECT doc_type FROM files")] == ["utility_bill", "utility_bill"]
    assert later.execute("SELECT count(*) FROM facts").fetchone()[0] == 2
    later.close()


def test_two_phase_stores_the_same_rows_as_single_phase(conn, tmp_path, monkeypatch):
    """The queue changes when the work happens, not what is stored. Layout boxes go through JSON in the queue, so the
    fake extractor checks it gets them back as {int page: [tuple]} and puts them in its output."""
    boxes = {1: [(10.0, 20.0, 110.0, 32.0, "Amount due 1200")], 2: []}
    monkeypatch.setattr(readers, "read_layout", lambda path: ([(1, path.read_text()), (2, "page two")], boxes))

    def fake_extract(doc_type, pages, got, fill_gaps=True):
        assert got == boxes and all(isinstance(k, int) and all(isinstance(b, tuple) for b in v) for k, v in got.items())
        return [{"field": "amount_due", "value": "1200.0", "value_type": "money", "page": 1, "verified": True, "status": "verified",
                 "reason": None, "source_text": f"{got[1][0][4]} | {pages[1][1]}"}]
    monkeypatch.setattr(extract, "extract", fake_extract)
    folder = tmp_path / "docs"
    folder.mkdir()
    for name, text in [("bill.txt", BILL), ("copy.txt", BILL), ("notes.txt", "Lecture notes on attention.")]:
        (folder / name).write_text(text)

    one = db.connect(tmp_path / "single.db")
    for p in sorted(folder.iterdir()):
        scan.index_file(one, p, scan.sha256(p))
        one.commit()
    two = db.connect(tmp_path / "queued.db")
    for p in sorted(folder.iterdir()):
        scan.index_text(two, p, scan.sha256(p))
        two.commit()
    assert scan.waiting(two) == 2  # the copy is not read again
    while row := two.execute("SELECT file_id FROM queue ORDER BY id").fetchone():
        scan.read_facts(two, row["file_id"])
        two.commit()
    assert dump(one) == dump(two)
    assert [r[2:] for r in dump(two)[0]] == [("utility_bill", "indexed", None, 0), ("utility_bill", "indexed", None, 1), ("other", "indexed", None, 0)]
    assert len(dump(two)[2]) == 1


def test_a_file_changed_while_its_facts_are_read_keeps_only_the_new_ones(conn, tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text(BILL)
    scan.scan(conn, tmp_path)
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    other = db.connect()

    def edited_meanwhile(pages):
        f.write_text("Lecture notes on attention.")  # the user saves a new version while the model reads the old one
        scan.index_text(other, f, scan.sha256(f))
        other.commit()
        return "utility_bill"
    monkeypatch.setattr(classify, "classify", edited_meanwhile)
    assert scan.read_facts(conn, fid) is False  # the old reading is dropped
    conn.commit()
    assert conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    assert "attention" in json.loads(conn.execute("SELECT pages FROM queue").fetchone()[0])[0][1]  # the new version waits
    other.close()


def test_server_down_keeps_the_file_queued(conn, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text(BILL)
    scan.scan(conn, tmp_path)

    def down(pages):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions"))
    monkeypatch.setattr(classify, "classify", down)
    with pytest.raises(openai.APIConnectionError):
        scan.read_facts(conn, conn.execute("SELECT id FROM files").fetchone()[0])
    conn.rollback()
    assert scan.waiting(conn) == 1 and conn.execute("SELECT error FROM files").fetchone()[0] is None


# ---------- trimmed passages ----------
def test_passage_keeps_the_sentences_around_the_hit():
    filler = " ".join(f"Filler sentence number {i} says nothing useful here." for i in range(20))
    text = f"{filler} The claim must be reported within 30 days. {filler}"
    out = ask.passage(text, "How many days do I have to report a claim?")
    assert "reported within 30 days" in out and len(out.split()) <= ask.PASSAGE_WORDS
    assert ask.passage("Short text stays whole.", "anything") == "Short text stays whole."


def test_passage_cuts_text_without_sentence_ends_into_pieces():
    bill = " ".join(f"Item{i} Rs. {i}00" for i in range(60)) + " Consumption this month: 324 kWh " + " ".join(f"Fee{i} Rs. 5" for i in range(40))
    out = ask.passage(bill, "How many kWh did we use?")
    assert "324 kWh" in out and len(out.split()) <= ask.PASSAGE_WORDS


def test_search_answer_reads_the_top_three_trimmed_passages(conn, monkeypatch):
    long = " ".join(f"Line {i} of an old letter." for i in range(60)) + " The probation period is 3 months."
    for i in range(5):
        fid = conn.execute("INSERT INTO files(path, doc_type, status) VALUES (?, 'other', 'indexed')", (f"C:/d/{i}.txt",)).lastrowid
        conn.execute("INSERT INTO chunks(file_id, page, text) VALUES (?, 1, ?)", (fid, long))
    conn.commit()
    seen = []
    monkeypatch.setattr(llm, "chat", lambda messages, **kw: seen.append(messages) or "3 months [1].")
    a = ask.answer_from_search(conn, "How long is the probation period?")
    prompt = seen[0][1]["content"]
    assert a.route == "search" and prompt.count("] (") == 3 and "probation period is 3 months" in prompt
    assert len(prompt.split()) < 3 * ask.PASSAGE_WORDS + 40


# ---------- streaming ----------
def events(client, question):
    out = []
    with client.stream("POST", "/ask/stream", json={"question": question}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        kind = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                out.append((kind, json.loads(line[5:])))
    return out


def test_search_answer_streams_tokens_then_the_whole_answer(conn, tmp_path, monkeypatch, drain):
    (tmp_path / "a.txt").write_text(BILL)
    scan.scan(conn, tmp_path)
    drain()
    monkeypatch.setattr(ask, "route", lambda q: Route(route="search"))
    monkeypatch.setattr(llm, "chat_stream", lambda messages, **kw: iter(["The amount ", "due is ", "1200 [1]."]))
    ev = events(TestClient(api.app), "What is the amount due?")
    assert [k for k, _ in ev] == ["token", "token", "token", "answer"]
    assert "".join(t for k, t in ev if k == "token") == "The amount due is 1200 [1]."
    final = ev[-1][1]
    assert final["answer"] == "The amount due is 1200 [1]." and final["route"] == "search" and final["sources"][0]["name"] == "a.txt"


def test_stream_holds_back_not_found_and_sends_facts_in_one_piece(conn, tmp_path, monkeypatch, drain):
    (tmp_path / "a.txt").write_text(BILL)
    scan.scan(conn, tmp_path)
    drain()
    client = TestClient(api.app)
    monkeypatch.setattr(ask, "route", lambda q: Route(route="search"))
    monkeypatch.setattr(llm, "chat_stream", lambda messages, **kw: iter(["NOT", " FOUND"]))
    ev = events(client, "What is my blood group?")
    assert [k for k, _ in ev] == ["answer"] and ev[0][1]["route"] == "none"

    monkeypatch.setattr(ask, "route", lambda q: Route(route="facts", doc_type="utility_bill", field="amount_due", op="list"))
    ev = events(client, "What are my bills?")
    assert [k for k, _ in ev] == ["answer"] and ev[0][1]["route"] == "facts" and ev[0][1]["answer"] == "1,200 (a.txt)."
