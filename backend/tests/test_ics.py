"""Local reminders: a .ics file with two alarms, no Google account."""
import pytest
from fastapi.testclient import TestClient

from sniff import api, gcal, ics


@pytest.fixture
def client(conn):
    return TestClient(api.app)


@pytest.fixture
def card(add_doc):
    return add_doc("C:/Users/ayesha/private/cnic.jpg", "id_card", {"name": "Ayesha Khan", "id_number": "35202-1234567-8", "expiry_date": "2027-03-09"})


def test_ics_file(client, conn, card, monkeypatch):
    monkeypatch.setattr(gcal, "create_event", lambda *a: pytest.fail("Google must not be called"))
    r = client.get(f"/reminders/ics?file_id={card}&field=expiry_date")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "attachment" in r.headers["content-disposition"]
    text = r.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20270309\r\n" in text and "DTEND;VALUE=DATE:20270310\r\n" in text
    assert "SUMMARY:Id card expires (Ayesha Khan)\r\n" in text
    assert text.count("BEGIN:VALARM") == 2 and "TRIGGER:-P7D\r\n" in text and "TRIGGER:-P1D\r\n" in text
    assert "From cnic.jpg\\, page 1." in text and "ayesha" not in text and "private" not in text  # file name only, no folders
    assert conn.execute("SELECT payload FROM actions WHERE kind='reminder_ics'").fetchone()
    assert conn.execute("SELECT count(*) FROM reminders").fetchone()[0] == 0  # a download is not a calendar event


def test_ics_errors(client, card):
    assert client.get(f"/reminders/ics?file_id={card}&field=name").status_code == 400
    assert client.get(f"/reminders/ics?file_id={card}&field=due_date").status_code == 404
    assert client.get("/reminders/ics?file_id=999&field=expiry_date").status_code == 404


def test_ics_escapes_and_folds_long_lines():
    from datetime import date
    text = ics.make_ics("u@x", "Fee, due; soon", date(2027, 1, 31), "x" * 200)
    assert "SUMMARY:Fee\\, due\\; soon\r\n" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert text.replace("\r\n ", "").count("x" * 200) == 1  # unfolding restores the text
