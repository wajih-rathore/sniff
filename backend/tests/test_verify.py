"""Verification v2: a value counts as verified only when it is on the page, next to its own field's label, and
passes the rules. Uses a real PDF made with PyMuPDF, plain text, and a fake tilted OCR result."""
import math

import pymupdf
import pytest

from sniff import verify
from sniff.ingest import ocr, readers

BILL = [  # (x, y, text, size): labels at x=40, values at x=300, like a printed bill
    (40, 50, "Northern Electric Supply Co.", 18),
    (40, 90, "Account No.", 10), (300, 90, "ELC-1234-5678", 10),
    (40, 110, "Billing Month", 10), (300, 110, "March 2026", 10),
    (40, 130, "Issue Date", 10), (300, 130, "05/04/2026", 10),
    (40, 170, "Energy Charges", 10), (300, 170, "Rs. 4,100", 10),
    (40, 190, "Taxes and Duties", 10), (300, 190, "Rs. 900", 10),
    (40, 210, "Current Charges", 10), (300, 210, "Rs. 5,000", 10),
    (40, 230, "Arrears", 10), (300, 230, "Rs. 1,200", 10),
    (40, 260, "Amount Due", 12), (300, 260, "Rs. 6,200", 12),
    (40, 300, "Due Date: 20/04/2026", 10), (300, 300, "Amount after due date: Rs. 6,696", 10),
]


@pytest.fixture(scope="module")
def bill(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "bill.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, y, text, size in BILL:
        page.insert_text((x, y), text, fontsize=size)
    doc.save(str(path))
    return readers.read_layout(path)


def check(doc_type, pages, boxes, **raw):
    facts = [{"field": f, "raw": r, "value": verify.parse(verify.FIELDS[doc_type][f].kind, r)} for f, r in raw.items()]
    verify.check(doc_type, facts, pages, boxes)
    return {f["field"]: f for f in facts}


def test_correct_values_are_verified(bill):
    got = check("utility_bill", *bill, provider="Northern Electric Supply Co.", account_number="ELC-1234-5678",
                billing_month="March 2026", bill_date="05/04/2026", current_charges="Rs. 5,000", previous_balance="Rs. 1,200",
                tax="Rs. 900", amount_due="Rs. 6,200", due_date="20/04/2026")
    assert {f: g["status"] for f, g in got.items() if not g["verified"]} == {}
    assert got["amount_due"]["page"] == 1 and "Amount Due" in got["amount_due"]["source_text"]


def test_model_tidied_value_is_still_found(bill):
    got = check("utility_bill", *bill, due_date="2026-04-20", amount_due="6200")
    assert got["due_date"]["verified"] and got["amount_due"]["verified"]


def test_value_next_to_another_label_needs_review(bill):
    got = check("utility_bill", *bill, amount_due="Rs. 6,696", current_charges="Rs. 1,200", billing_month="05/04/2026")
    assert all(g["status"] == "needs_review" for g in got.values())
    assert "Amount after due date" in got["amount_due"]["reason"] or "Due Date" in got["amount_due"]["reason"]
    assert got["current_charges"]["reason"].startswith("found next to 'Arrears'")
    assert got["billing_month"]["reason"].startswith("found next to 'Issue Date'")


def test_value_not_on_page(bill):
    got = check("utility_bill", *bill, due_date="01/05/2026")
    assert got["due_date"]["reason"] == "value not found on the page" and not got["due_date"]["verified"]


def test_cross_rule_failure_marks_both_fields(bill):
    got = check("utility_bill", *bill, current_charges="Rs. 5,000", amount_due="Rs. 6,200")  # arrears not extracted
    for f in ("current_charges", "amount_due"):
        assert got[f]["status"] == "needs_review" and "current charges + previous balance" in got[f]["reason"]


def test_plain_text_uses_line_order():
    pages = [(1, "Invoice\nTotal Due\nRs. 900\n\nThank you\nPlease come again\nKeep this slip\nCash 900")]
    assert check("utility_bill", pages, {}, amount_due="900")["amount_due"]["verified"]
    pages = [(1, "Invoice\nThank you\nPlease come again\nKeep this slip\nTotal Due\nnotes\nmore notes\nRs. 900")]
    assert not check("utility_bill", pages, {}, amount_due="900")["amount_due"]["verified"]


def test_label_words_inside_values():
    pages = [(1, "Certificate of Completion\nThis is to certify that\nSara Khan\nhas passed the examination for\n"
                 "Secondary School Certificate\nIssue Date: 20/06/2023\nIssued by Brightpath School")]
    got = check("certificate", pages, {}, holder_name="Sara Khan", title="Secondary School Certificate",
                issue_date="20/06/2023", institution="Brightpath School")
    assert all(g["verified"] for g in got.values()), {f: g["reason"] for f, g in got.items()}


def test_label_edges():
    total = verify.labels("receipt")
    pat = next(p for f, a, p in total if a == "Total")
    assert verify.label_spans("Total", pat, "Subtotal 900") == [] and verify.label_spans("Total", pat, "SUBTOTAL") == []
    assert verify.label_spans("Total", pat, "GRAND TOTAL 900") == [(6, 11)]
    issued = next(p for f, a, p in verify.labels("certificate") if a == "Issued by")
    assert verify.label_spans("Issued by", issued, "IssuedbyBrightpathAcademy")  # OCR dropped the spaces


def test_tilted_scan_is_straightened(monkeypatch):
    a = math.radians(4)

    def box(x0, y0, w, h):  # a box rotated by 4 degrees about the origin, as RapidOCR returns it
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        return [[x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)] for x, y in pts]

    fake = [[box(40, 300, 160, 20), "Amount Due", 0.9], [box(600, 300, 140, 20), "Rs. 6,200", 0.9],
            [box(40, 340, 160, 20), "Due Date", 0.9], [box(600, 340, 140, 20), "20/04/2026", 0.9]]
    monkeypatch.setattr(ocr, "_engine", lambda: lambda data: (fake, None))
    text, boxes = ocr.read(b"")
    rows = verify.rows_from_boxes(boxes)
    assert [r.text for r in rows] == ["Amount Due Rs. 6,200", "Due Date 20/04/2026"]
    got = check("utility_bill", [(1, text)], {1: boxes}, amount_due="Rs. 6,200", due_date="20/04/2026")
    assert got["amount_due"]["verified"] and got["due_date"]["verified"]
