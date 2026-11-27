"""Fast indexing: fields from rules first, the candidate-line window, the type classifier and its fallback to the
model, the chat server started on demand, and files another program holds open."""
import sys
import time

import numpy as np
import pytest

from sniff.ai import classify, extract, llm, rules, server, typer
from sniff.ingest import scan

REAL_CLASSIFY = classify.classify  # conftest's conn fixture replaces it with a fake model

BILL = """Metro Gas Ltd
Monthly Gas Statement
Account Number: MG-6421-9595
Statement Period: 2025-11
Statement Date: 05 Dec 2025
Billed To: Bilal Ahmed
Net Amount Due: Rs. 5,558
Pay by: 2025-12-25
Taxes 5% Rs. 278
Current Charges Rs. 5,558
A late payment surcharge applies if the bill is paid after the due date: Rs. 6,002.64"""

CARD = """REPUBLIC OF EASTLAND
NATIONAL IDENTITY CARD
Name
Bilal Ahmed
CNIC: 81004-5001148-1
Date of Birth: 11 Feb 1985
Issued: 06 May 2019
Valid Until: 06 May 2029"""


# ---------- rules ----------
def test_rules_read_the_value_next_to_each_label():
    got = rules.extract("utility_bill", [(1, BILL)])
    assert got == {"provider": "Metro Gas Ltd", "account_number": "MG-6421-9595", "billing_month": "2025-11",
                   "bill_date": "05 Dec 2025", "due_date": "2025-12-25", "current_charges": "5,558", "tax": "278",
                   "amount_due": "5,558"}  # tax skips "5%"; "paid after the due date" is no payment status


def test_rules_read_a_value_printed_under_its_label():
    got = rules.extract("id_card", [(1, CARD)])
    assert got["name"] == "Bilal Ahmed" and got["id_number"] == "81004-5001148-1" and got["expiry_date"] == "06 May 2029"


def test_a_label_is_never_taken_as_a_value():
    page = "Name DOB\nBilal Ahmed 11.02.1985\nPosition\nThe position of Manager is open."
    got = rules.extract("id_card", [(1, page)])
    assert got.get("name") != "DOB"
    assert "position" not in rules.extract("offer_letter", [(1, "We offer you the position of Manager at Orchard.")])


def test_rules_leave_out_what_they_cannot_anchor():
    assert rules.extract("receipt", [(1, "Thanks for visiting. 12 Mar 2026 was a good day. 4,500")]) == {}


# ---------- window ----------
def test_window_keeps_the_lines_near_the_labels():
    lines = [f"filler line {i}" for i in range(20)]
    lines[10] = "Due Date: 25.12.2025"
    pages = [(1, "\n".join(lines))]
    win = rules.window("utility_bill", pages, None, ["due_date"])
    assert win == [(1, "Due Date: 25.12.2025\nfiller line 11")]  # the label's row and the one under it
    wide = rules.window("utility_bill", pages, None, ["due_date"], before=1, after=2)
    assert wide == [(1, "filler line 9\nDue Date: 25.12.2025\nfiller line 11\nfiller line 12")]
    assert rules.window("utility_bill", [(1, "nothing useful")], None, ["due_date"]) == [(1, "nothing useful")]  # no label: all of it


# ---------- extract: model only for required fields the rules missed ----------
def test_the_model_is_asked_only_for_missing_required_fields(monkeypatch):
    asked = []

    def fake_chat_json(messages, model, **kw):
        asked.append((list(model.model_fields), messages[1]["content"]))
        return model(expiry_date="06 May 2029")
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    page = CARD.replace("Valid Until: 06 May 2029", "Footer text\n06 May 2029")  # the expiry has lost its label
    facts = {f["field"]: f for f in extract.extract("id_card", [(1, page)])}
    assert [a[0] for a in asked] == [["expiry_date"]]  # one call, for the one required field the rules missed
    assert facts["name"]["status"] == "verified" and facts["expiry_date"]["status"] == "needs_review"  # no label next to it

    asked.clear()
    extract.extract("id_card", [(1, CARD)])
    assert asked == []  # the rules found every required field: no model call


# ---------- type classifier ----------
def toy(seed=0):
    rng = np.random.default_rng(seed)
    centres = rng.standard_normal((3, 16))
    x = np.vstack([c + 0.1 * rng.standard_normal((20, 16)) for c in centres]).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True), np.repeat(np.arange(3), 20)


def test_training_is_deterministic_and_separates_the_types():
    x, y = toy()
    w1, b1 = typer.train(x, y, 3)
    w2, b2 = typer.train(x, y, 3)
    assert np.array_equal(w1, w2) and np.array_equal(b1, b2)
    assert (typer.probabilities(x, w1, b1).argmax(axis=1) == y).all()


def test_decide_abstains_unless_sure_and_the_rules_agree():
    classes = ["other", "receipt", "utility_bill"]
    sure_bill = np.array([0.02, 0.03, 0.95])
    assert typer.decide(sure_bill, classes, "Electricity bill, amount due", 0.8) == "utility_bill"
    assert typer.decide(sure_bill, classes, "Receipt. Thank you for shopping", 0.8) is None  # rules say receipt
    assert typer.decide(np.array([0.3, 0.3, 0.4]), classes, "Electricity bill", 0.8) is None  # not sure
    assert typer.decide(np.array([0.97, 0.02, 0.01]), classes, "Recipe: chocolate brownies", 0.8) == "other"


def test_unsure_type_falls_back_to_the_model_and_confident_other_skips_it(conn, tmp_path, drain, monkeypatch):
    from sniff.ai import classify
    calls = []
    monkeypatch.setattr(classify, "classify", lambda pages: calls.append("model") or "utility_bill")
    monkeypatch.setattr(extract, "extract", lambda doc_type, pages, boxes=None, fill_gaps=True: calls.append("extract") or [])
    (tmp_path / "a.txt").write_text("Electricity bill. Amount due 1200.")
    (tmp_path / "b.txt").write_text("Chocolate brownies: melt the butter.")
    scan.scan(conn, tmp_path)
    monkeypatch.setattr(typer, "classify", lambda conn, fid, pages: None if "bill" in pages[0][1] else "other")
    drain()
    types = dict(conn.execute("SELECT substr(path, -5), doc_type FROM files").fetchall())
    assert types == {"a.txt": "utility_bill", "b.txt": "other"}
    assert calls == ["model", "extract"]  # the brownies never reached the model


def test_guess_takes_the_top_type_only_above_the_floor(monkeypatch):
    def probs(top, p):
        v = np.full(len(typer.CLASSES), (1 - p) / (len(typer.CLASSES) - 1))
        v[typer.CLASSES.index(top)] = p
        return lambda conn, fid: v

    for top, p, want in [("receipt", typer.GUESS_FLOOR + 0.05, "receipt"), ("receipt", typer.GUESS_FLOOR - 0.05, None),
                         ("other", 0.9, None)]:
        monkeypatch.setattr(typer, "_probs", probs(top, p))
        assert typer.guess(None, 1) == want
    monkeypatch.setattr(typer, "_probs", lambda conn, fid: None)  # no vector stored
    assert typer.guess(None, 1) is None


def test_rules_only_extraction_never_calls_the_model(monkeypatch):
    asked = []
    monkeypatch.setattr(extract, "ask_model", lambda *a, **kw: asked.append(a[1]) or {})
    monkeypatch.setenv("SNIFF_AI", "local")  # a provider that would get the missing fields
    extract.extract("id_card", [(1, "Student card")])
    assert asked  # the control: required fields are missing, so the model is asked
    asked.clear()
    extract.extract("id_card", [(1, "Student card")], fill_gaps=False)
    assert asked == []


def test_with_ai_off_an_unsure_type_is_guessed_and_its_facts_go_to_review(conn, tmp_path, drain, monkeypatch):
    fill = []
    monkeypatch.setattr(classify, "classify", REAL_CLASSIFY)  # AI is off: no model sorts the documents
    monkeypatch.setattr(typer, "guess", lambda c, fid: "utility_bill" if c.execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()[0].endswith("a.txt") else None)
    monkeypatch.setattr(extract, "extract", lambda doc_type, pages, boxes=None, fill_gaps=True: fill.append(fill_gaps) or [
        {"field": "amount_due", "value": "1200.0", "value_type": "money", "page": 1, "verified": True, "status": "verified", "reason": None, "source_text": None},
        {"field": "due_date", "value": "2025-01-10", "value_type": "date", "page": 1, "verified": False, "status": "needs_review",
         "reason": "value not found on the page", "source_text": None}])
    (tmp_path / "a.txt").write_text("Electricity bill. Amount due 1200.")
    (tmp_path / "b.txt").write_text("Chocolate brownies: melt the butter.")
    scan.scan(conn, tmp_path)
    drain()
    assert dict(conn.execute("SELECT substr(path, -5), doc_type FROM files").fetchall()) == {"a.txt": "utility_bill", "b.txt": None}
    assert fill == [False]  # the rules only: nothing is sent to a provider for a type that may be wrong
    assert [tuple(r) for r in conn.execute("SELECT field, status, reason FROM facts ORDER BY id")] == [
        ("amount_due", "needs_review", "type guessed"), ("due_date", "needs_review", "type guessed; value not found on the page")]


def test_classifier_reads_the_stored_vectors(conn, tmp_path):
    (tmp_path / "a.txt").write_text("Electricity bill. Amount due 1200.")
    scan.scan(conn, tmp_path)
    fid = conn.execute("SELECT id FROM files").fetchone()[0]
    v = typer.doc_vector(conn, fid)
    assert v.shape == (384,) and abs(np.linalg.norm(v) - 1) < 1e-5


# ---------- chat server on demand ----------
class FakeProc:
    def __init__(self, *a, **kw):
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


def test_chat_server_starts_on_first_call_and_stops_when_idle(monkeypatch):
    monkeypatch.undo()  # this test wants the real ensure()
    started = []
    monkeypatch.setattr(server, "can_start", lambda: True)
    monkeypatch.setattr(server, "ready", lambda: bool(started) and started[-1].alive)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **kw: started.append(FakeProc()) or started[-1])
    monkeypatch.setattr(server, "IDLE", 0.4)
    try:
        with server.using():
            assert len(started) == 1 and server.running()
        with server.using():  # still up: no second start
            assert len(started) == 1
        deadline = time.time() + 5
        while server.running() and time.time() < deadline:
            time.sleep(0.05)
        assert not server.running() and not started[0].alive  # stopped after IDLE seconds without a call
    finally:
        server.stop()


def test_a_server_someone_else_runs_is_used_and_never_stopped(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(server, "ready", lambda: True)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not start a second server"))
    with server.using():
        pass
    assert not server.running()


# ---------- a file another program holds open ----------
def test_locked_file_is_recorded_retried_and_the_scan_carries_on(conn, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("Electricity bill one")
    (tmp_path / "locked.txt").write_text("Electricity bill two")
    real, held = scan.sha256, {"on": True}

    def sha256(p):
        if held["on"] and p.name == "locked.txt":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return real(p)
    monkeypatch.setattr(scan, "sha256", sha256)
    monkeypatch.setattr(scan, "RETRY_AFTER", 0.2)
    counts = scan.scan(conn, tmp_path)
    assert counts["indexed"] == 1 and counts["errors"] == 1
    row = conn.execute("SELECT status, error FROM files WHERE path LIKE '%locked.txt'").fetchone()
    assert row["status"] == "error" and "PermissionError" in row["error"]

    held["on"] = False  # the other program closes it; the retry picks it up without any new file event
    deadline = time.time() + 10
    while time.time() < deadline:
        if conn.execute("SELECT status FROM files WHERE path LIKE '%locked.txt'").fetchone()[0] == "indexed":
            break
        time.sleep(0.1)
    assert conn.execute("SELECT status FROM files WHERE path LIKE '%locked.txt'").fetchone()[0] == "indexed"


@pytest.mark.skipif(sys.platform != "win32", reason="a real byte-range lock is a Windows thing")
def test_really_locked_file_does_not_stop_the_scan(conn, tmp_path, monkeypatch):
    import msvcrt
    monkeypatch.setattr(scan, "RETRY_AFTER", 3600)  # no retry during the test
    (tmp_path / "a.txt").write_text("Electricity bill one")
    held = tmp_path / "held.txt"
    held.write_text("Electricity bill two")
    with open(held, "r+b") as f:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, held.stat().st_size)
        counts = scan.scan(conn, tmp_path)
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, held.stat().st_size)
    assert counts["indexed"] == 1 and counts["errors"] == 1


def test_the_shipped_weights_load():
    assert len(typer.CLASSES) == 13 and typer._W.shape == (13, 384) and 0 < typer.THRESHOLD < 1


def test_an_indexed_file_that_gets_locked_stays_findable(conn, tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill one")
    scan.scan(conn, tmp_path)
    f.write_text("Electricity bill one, edited")  # changed on disk, then held open
    monkeypatch.setattr(scan, "sha256", lambda p: (_ for _ in ()).throw(PermissionError(13, "in use")))
    monkeypatch.setattr(scan, "RETRY_AFTER", 3600)
    assert scan.scan(conn, tmp_path)["errors"] == 1
    assert conn.execute("SELECT status FROM files").fetchone()[0] == "indexed"
