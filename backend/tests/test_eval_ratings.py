"""Human-rating harness: kappa against published worked examples, reading rated sheets, and the exported form."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import ratings as r  # noqa: E402


def test_cohen_kappa_matches_the_textbook_example():
    # 50 items: both yes 20, A yes / B no 5, A no / B yes 10, both no 15 -> kappa 0.4 (Wikipedia's worked example)
    a = [1] * 20 + [1] * 5 + [0] * 10 + [0] * 15
    b = [1] * 20 + [0] * 5 + [1] * 10 + [0] * 15
    assert round(r.cohen_kappa(a, b, 0, 1, False), 3) == 0.4


def test_weighted_kappa_forgives_near_misses_more():
    a, b = [1, 2, 3, 4, 5, 3], [2, 2, 3, 4, 4, 3]
    plain, weighted = r.cohen_kappa(a, b, 1, 5, False), r.cohen_kappa(a, b, 1, 5, True)
    assert weighted > plain
    assert r.cohen_kappa([2, 2, 2], [2, 2, 2], 0, 2, True) is None  # one value throughout: chance agreement is total
    assert r.cohen_kappa([0, 1, 2, 1], [0, 1, 2, 1], 0, 2, True) == 1.0


def test_fleiss_kappa_matches_the_textbook_example():
    # 10 items, 14 raters, 5 categories (Wikipedia's worked example): kappa 0.210
    counts = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
              [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]
    items = [[c for c, n in enumerate(row, 1) for _ in range(n)] for row in counts]
    assert round(r.fleiss_kappa(items, 1, 5), 3) == 0.21


def sheet(folder, rater, rows, bom=True):
    text = "id,rater,correctness,groundedness,usefulness\n" + "".join(f"{i},{rater},{c},{g},{u}\n" for i, c, g, u in rows)
    (folder / f"rated_{rater}.csv").write_text(text, encoding="utf-8-sig" if bom else "utf-8")


def test_read_rated_skips_blanks_and_rejects_out_of_scale_values(tmp_path):
    sheet(tmp_path, "ali", [(1, 2, 1, 5), (2, "", "", 3)])
    got = r.read_rated([tmp_path / "rated_ali.csv"])
    assert got == {"ali": {1: {"correctness": 2, "groundedness": 1, "usefulness": 5}, 2: {"usefulness": 3}}}
    sheet(tmp_path, "bad", [(1, 3, 1, 5)])
    with pytest.raises(ValueError, match="correctness must be 0 to 2"):
        r.read_rated([tmp_path / "rated_bad.csv"])


def test_summary_with_three_raters(tmp_path):
    items = {i: {"id": i, "category": "semantic" if i < 3 else "unanswerable", "question": f"q{i}", "answer": f"a{i}", "reference_answer": "x"} for i in range(1, 5)}
    sheet(tmp_path, "a", [(1, 2, 1, 5), (2, 2, 1, 4), (3, 0, 0, 1), (4, 1, 1, 3)])
    sheet(tmp_path, "b", [(1, 2, 1, 5), (2, 2, 1, 4), (3, 0, 0, 1), (4, 1, 1, 3)], bom=False)
    sheet(tmp_path, "c", [(1, 2, 1, 4), (2, 1, 1, 4), (3, 0, 0, 2), (4, 1, 0, 3)])
    rows, pairs, by_cat, bad = r.summarise(r.read_rated(sorted(tmp_path.glob("rated_*.csv"))), items)
    m = {x["metric"]: x for x in rows}
    assert pairs["correctness"]["a vs b"] == 1.0 and pairs["correctness"]["a vs c"] < 1.0
    assert m["correctness"]["mean"] == round((2 + 2 + 0 + 1) / 4 * 2 / 3 + (2 + 1 + 0 + 1) / 4 / 3, 3) and m["correctness"]["items_rated_by_all"] == 4
    assert m["correctness"]["mean_a"] == 1.25 and m["groundedness"]["fleiss_kappa"] is not None and m["usefulness"]["exact_agreement"] is not None
    assert by_cat["semantic"]["n"] == 2 and by_cat["unanswerable"]["n"] == 2
    assert [b["id"] for b in bad] == [3]  # every rater gave item 3 a zero for correctness


def test_build_items_is_fixed_and_hides_nothing_needed_for_rating():
    qs = [{"q": f"question {i}", "answer": "A" if i else "", "category": "semantic" if i else "unanswerable"} for i in range(6)]
    ask = lambda q: NS(answer=f"reply to {q}", sources=[])
    one, two = r.build_items(qs, ask, lambda a: "p"), r.build_items(qs, ask, lambda a: "p")
    assert one == two and [x["id"] for x in one] == [1, 2, 3, 4, 5, 6]
    assert {x["question"] for x in one} == {q["q"] for q in qs}
    assert [x["reference_answer"] for x in one if x["category"] == "unanswerable"][0].startswith("(not in the documents")


def test_form_and_sheet_files(tmp_path):
    items = [{"id": 1, "category": "semantic", "question": "q?", "answer": 'It says </script><b>"hi"</b>', "passages": "[1] a.pdf, page 1: text", "reference_answer": "x"}]
    r.write_files(tmp_path, items)
    html = (tmp_path / "form.html").read_text(encoding="utf-8")
    assert html.count("</script>") == 1 and "semantic" not in html  # the category is not shown to raters
    assert (tmp_path / "sheet.csv").read_bytes().startswith(b"\xef\xbb\xbfid,question")
    assert json.loads((tmp_path / "answers.json").read_text(encoding="utf-8"))[0]["category"] == "semantic"
    if not shutil.which("node"):
        pytest.skip("node not installed: form JavaScript not checked")
    js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    (tmp_path / "form.js").write_text(js, encoding="utf-8")
    assert subprocess.run(["node", "--check", str(tmp_path / "form.js")], capture_output=True).returncode == 0
    # the CSV the form downloads: quotes escaped, unrated cells empty
    fn = "\n".join(re.search(rf"(function {n}\(.*?\n}}|function {n}\(.*?}})", js, re.S).group(1) for n in ("csvCell", "toCsv"))
    out = subprocess.run(["node", "-e", f"const ITEMS=[{{id:1}},{{id:2}}];{fn}\nconsole.log(JSON.stringify(toCsv('A \"B\"', {{1:{{correctness:2,groundedness:0}}}})))"],
                         capture_output=True, text=True).stdout
    assert json.loads(out) == 'id,rater,correctness,groundedness,usefulness\r\n1,"A ""B""",2,0,\r\n2,"A ""B""",,,\r\n'


def test_score_writes_the_result_file(tmp_path, monkeypatch):
    import common
    monkeypatch.setattr(common, "RESULTS", tmp_path / "results")
    items = [{"id": i, "category": "semantic", "question": f"q{i}", "answer": "a", "passages": "p", "reference_answer": "x"} for i in (1, 2, 3)]
    r.write_files(tmp_path, items)
    sheet(tmp_path, "ali", [(1, 2, 1, 5), (2, 1, 1, 3), (3, 0, 0, 1)])
    sheet(tmp_path, "sara", [(1, 2, 1, 4), (2, 1, 1, 3), (3, 0, 1, 1)])
    r.score(tmp_path)
    res = json.loads((tmp_path / "results" / "human_ratings.json").read_text())
    assert res["raters"] == ["ali", "sara"] and res["n"] == 3 and res["failures"] == 1
    assert {x["metric"] for x in res["rows"]} == {"correctness", "groundedness", "usefulness"}
