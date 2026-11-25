"""Error grouping rules and the static evaluation report."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import error_groups as eg  # noqa: E402
import report  # noqa: E402


def fail(**kw):
    return {"set": "test", "file": "a.jpg", "type": "receipt", "field": "tax", "gold": 100.0, "pred": "50.0", "status": "needs_review", **kw}


def test_field_causes():
    gold = {("test", "a.jpg"): {"total_amount": 600.0, "subtotal": 500.0, "tax": 100.0, "date": "2026-01-02"}}
    assert eg.field_cause(fail(), {("test", "a.jpg")}, gold) == "fields: document type was wrong"
    assert eg.field_cause(fail(gold=None), set(), gold) == "fields: value for a field the document does not have"
    assert eg.field_cause(fail(pred=None), set(), gold) == "fields: value missed"
    assert eg.field_cause(fail(pred="500.0"), set(), gold) == "fields: amount from a neighbouring field"
    assert eg.field_cause(fail(), set(), gold) == "fields: wrong value"
    assert eg.field_cause(fail(field="date", gold="2026-01-02", pred="2026-01-03"), set(), gold) == "fields: date read wrong"
    assert eg.field_cause(fail(field="merchant", gold="Metro Mart", pred="Metro Mart Lahore"), set(), gold) == "fields: text partly right"


def test_groups_count_and_order():
    g = eg.groups([("ask: wrong route", "a"), ("ask: refused an answerable question", "b"), ("ask: wrong route", "c")])
    assert [(x["cause"], x["count"], x["example"]) for x in g] == [("wrong route", 2, "a"), ("refused an answerable question", 1, "b")]
    assert g[0]["fix"] == eg.FIXES["ask: wrong route"]


def test_report_headline_and_page(tmp_path):
    rows = [{"system": "Sniff (hybrid BM25 + embeddings)", "Recall@5 (capped)": 0.8, "MRR": 0.7},
            {"system": "Keyword only (BM25)", "Recall@5 (capped)": 0.6, "MRR": 0.5},
            {"system": "File-name search (baseline)", "Recall@5 (capped)": 0.1, "MRR": 0.1}]
    (tmp_path / "search.json").write_text(json.dumps({"component": "search", "n": 78, "rows": rows}))
    (tmp_path / "search_failures.json").write_text("[]")
    R = report.load(tmp_path)
    assert list(R) == ["search"]
    h = report.headline(R)
    assert [(x["baseline"], x["Sniff"], x["baseline value"], x["n"]) for x in h] == [("File-name search", 0.8, 0.1, 78), ("BM25 only", 0.8, 0.6, 78)]
    page = report.page(R)
    assert page.startswith("<!doctype html>") and "<script" not in page and "0.800" in page
