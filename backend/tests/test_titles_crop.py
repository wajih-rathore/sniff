"""What the launcher shows: document titles, why a hit matched, recent documents, folder counts, and page crops."""
import io

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sniff import api, titles
from sniff.schemas import Answer, Source

from conftest import fake_vector


@pytest.fixture
def client(conn):
    return TestClient(api.app)


def test_titles():
    assert titles.title("utility_bill", {"provider": "Metro Gas Ltd", "billing_month": "2025-11"}, "x.pdf") == "Metro Gas, utility bill, Nov 2025"
    assert titles.title("utility_bill", {"provider": "Northern Electric Supply Co."}, "x.pdf") == "Northern Electric, utility bill"
    assert titles.title("id_card", {"name": "ZAINAB MALIK", "expiry_date": "2026-02-14"}, "x.pdf") == "Zainab Malik, ID card"
    assert titles.title("fee_voucher", {"student_name": "Omar Farooq", "semester": "Spring 2025"}, "v.pdf") == "Omar Farooq, fee voucher, Spring 2025"
    assert titles.title("receipt", {"total_amount": "12.0"}, r"D:\scans\IMG_01.jpg") == "IMG_01.jpg"  # no name on it: the file name
    assert titles.title(None, {}, "notes.pptx") == "notes.pptx"


@pytest.fixture
def three(conn, add_doc, tmp_path):
    """A gas bill that names the query, a page that only mentions it, and a bill that shares no word with it."""
    gas = add_doc(str(tmp_path / "a.pdf"), "utility_bill", {"provider": "Metro Gas Ltd", "billing_month": "2025-11", "bill_date": "2025-12-05"})
    note = add_doc(str(tmp_path / "b.pdf"), "utility_bill", {"provider": "Northern Electric Supply Co.", "billing_month": "2025-10"})
    other = add_doc(str(tmp_path / "c.pdf"), "utility_bill", {"provider": "FastLink Internet", "billing_month": "2025-09"})
    for fid, text in [(gas, "Metro Gas Ltd monthly statement"), (note, "Paid to metro gas by mistake"), (other, "Broadband invoice")]:
        cid = conn.execute("INSERT INTO chunks(file_id, page, text) VALUES (?, 1, ?)", (fid, text)).lastrowid
        conn.execute("INSERT INTO chunk_vec(chunk_id, embedding) VALUES (?, ?)", (cid, fake_vector(text).tobytes()))
    conn.commit()
    return gas, note, other


def test_search_hits_say_why_they_matched(client, three):
    gas, note, other = three
    hits = {h["file_id"]: h for h in client.get("/search", params={"q": "metro gas"}).json()}
    assert hits[gas]["match"] == "fact" and hits[gas]["match_reason"] == "Matches the provider name on page 1"
    assert hits[gas]["fact"]["value"] == "Metro Gas Ltd" and hits[gas]["title"] == "Metro Gas, utility bill, Nov 2025"
    assert hits[gas]["date"] == "2025-12-05"
    assert hits[note]["match"] == "text" and hits[note]["match_reason"] == "Matches the text on page 1"
    assert hits[other]["match"] == "meaning"  # found by the vector search only: a weaker match


def test_recent_documents_and_source_titles(client, conn, three):
    gas, note, other = three
    conn.execute("UPDATE files SET indexed_at=CASE id WHEN ? THEN '2026-11-02' WHEN ? THEN '2026-11-03' ELSE '2026-11-01' END", (gas, note))
    conn.commit()
    recent = client.get("/documents", params={"recent": 2}).json()
    assert [d["id"] for d in recent] == [note, gas] and recent[1]["title"] == "Metro Gas, utility bill, Nov 2025"
    a = Answer(question="q", answer="a", route="facts", sources=[Source(file_id=gas, path="a.pdf", name="a.pdf", page=1)])
    assert titles.name_sources(conn, a).sources[0].title == "Metro Gas, utility bill, Nov 2025"


def test_watch_counts_per_folder(client, conn, add_doc, tmp_path, monkeypatch):
    folder = tmp_path / "Bills"
    add_doc(str(folder / "a.pdf"), "utility_bill", {"provider": "Metro Gas Ltd"})
    waiting = add_doc(str(folder / "sub" / "b.pdf"), "utility_bill", {"provider": "Metro Gas Ltd"})
    add_doc(str(tmp_path / "Billsx" / "c.pdf"), "utility_bill", {"provider": "Metro Gas Ltd"})  # a different folder
    conn.execute("INSERT INTO queue(file_id, pages, boxes) VALUES (?, '[]', '[]')", (waiting,))
    conn.commit()
    monkeypatch.setattr(api.WATCHER, "folders", lambda: [str(folder)])
    assert client.get("/watch").json()["counts"] == {str(folder): {"files": 2, "errors": 0, "waiting": 1}}


@pytest.fixture
def printed(conn, add_doc, tmp_path):
    path = tmp_path / "bill.pdf"
    doc = pymupdf.open()
    page = doc.new_page()  # A4-ish: 595 x 842
    page.insert_text((50, 60), "Metro Gas Ltd")
    page.insert_text((50, 620), "Current Charges Rs. 5,558")
    doc.save(str(path))
    fid = add_doc(str(path), "utility_bill", {"provider": "Metro Gas Ltd", "current_charges": "5558.0"})
    conn.execute("UPDATE facts SET source_text='Current Charges Rs. 5,558' WHERE field='current_charges'")
    conn.commit()
    return fid, conn.execute("SELECT id FROM facts WHERE field='current_charges'").fetchone()[0], path


def test_crop_finds_the_value_on_the_page(client, printed):
    fid, fact, path = printed
    with pymupdf.open(str(path)) as doc:
        spot = api._spot(doc[0], "5558.0", "money", "Current Charges Rs. 5,558")
    assert spot and 600 < spot.y1 < 630 and spot.x0 > 100  # the printed "5,558", not the label before it
    r = client.get(f"/documents/{fid}/crop", params={"fact_id": fact})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.width == round(595 * 2) and img.height < img.width  # a strip at 144 dpi, not the whole page
    top = Image.open(io.BytesIO(client.get(f"/documents/{fid}/crop").content))  # no fact: the top of the page
    assert top.width == img.width and abs(top.height - img.height) <= 1 and top.tobytes() != img.tobytes()
    assert client.get(f"/documents/{fid}/crop", params={"fact_id": 9999}).status_code == 404
