"""Robustness harness: hard documents are paired with their clean twins and the drops are computed per corruption."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import common  # noqa: E402
import run_robustness as rr  # noqa: E402

from sniff import db  # noqa: E402


def put(conn, root, rel, doc_type, facts):
    fid = conn.execute("INSERT INTO files(path, doc_type, status) VALUES (?,?, 'indexed')", (str(root / rel), doc_type)).lastrowid
    for field, value in facts.items():
        conn.execute("INSERT INTO facts(file_id, field, value, value_type, verified, status) VALUES (?,?,?, 'text', 1, 'verified')", (fid, field, value))
    conn.commit()


def labels_file(root, rows):
    root.mkdir(parents=True, exist_ok=True)
    (root / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_drops_per_corruption(tmp_path, monkeypatch):
    test_root, hard_root = tmp_path / "synth", tmp_path / "hard"
    bill = {"amount_due": 100.0, "tax": 5.0, "due_date": "2026-01-01"}
    labels_file(test_root, [
        {"file": "test/a.pdf", "type": "utility_bill", "fields": bill, "split": "test"},
        {"file": "test/b.pdf", "type": "receipt", "fields": {"merchant": "Mart", "total_amount": 50.0}, "split": "test"},
    ])
    labels_file(hard_root, [
        {"file": "hard/cutoff/a.jpg", "type": "utility_bill", "fields": {**bill, "amount_due": None}, "split": "hard", "source": "test/a.pdf",
         "variant": "cutoff", "effects": ["cut_bottom", "blur"]},
        {"file": "hard/photo/b.jpg", "type": "receipt", "fields": {"merchant": "Mart", "total_amount": 50.0}, "split": "hard", "source": "test/b.pdf",
         "variant": "photo", "effects": ["blur"]},
    ])
    for name, root in (("test", test_root), ("hard", hard_root)):
        monkeypatch.setitem(common.SETS, name, root)
    monkeypatch.setattr(common, "EVAL_DB", tmp_path / "eval.db")
    monkeypatch.setattr(common, "RESULTS", tmp_path / "results")
    t, h = db.connect(common.db_path("test")), db.connect(common.db_path("hard"))
    put(t, test_root, "test/a.pdf", "utility_bill", {"amount_due": "100.0", "tax": "5.0", "due_date": "2026-01-01"})
    put(t, test_root, "test/b.pdf", "receipt", {"merchant": "Mart", "total_amount": "50.0"})
    put(h, hard_root, "hard/cutoff/a.jpg", "utility_bill", {"amount_due": "90.0", "tax": "5.0"})  # invents the cut field, misses the date
    put(h, hard_root, "hard/photo/b.jpg", "other", {})  # the photo is read as the wrong type
    t.close()
    h.close()

    rr.main()
    res = json.loads((tmp_path / "results" / "robustness.json").read_text())
    rows = {r["variant"]: r for r in res["rows"]}
    assert rows["cutoff"]["field_drop"] == 0.5 and rows["cutoff"]["type_drop"] == 0.0 and rows["cutoff"]["cut_fields_filled"] == 1.0
    assert rows["photo"]["type_drop"] == 1.0 and rows["photo"]["field_drop"] == 1.0
    assert rows["all"]["n"] == 2 and res["by_effect"]["blur"]["n"] == 2 and res["by_effect"]["cut_bottom"]["field_drop"] == 0.5
    fails = json.loads((tmp_path / "results" / "robustness_failures.json").read_text())
    assert fails and all(f["clean_twin_also_failed"] is False for f in fails)  # every one was caused by the corruption
    assert {f["source"] for f in fails} == {"test/a.pdf", "test/b.pdf"}


def test_pairs_need_both_sides_indexed():
    hard = [{"file": "h1", "source": "t1", "fields": {"x": 1}, "variant": "photo", "effects": []},
            {"file": "h2", "source": "t2", "fields": {"x": 1}, "variant": "photo", "effects": []}]
    test = [{"file": "t1", "fields": {"x": 1}}, {"file": "t2", "fields": {"x": 1}}]
    assert [p[0]["file"] for p in rr.build_pairs(hard, test, {"h1", "h2"}, {"t1"})] == ["h1"]
