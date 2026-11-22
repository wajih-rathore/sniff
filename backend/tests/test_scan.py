"""Functional tests for F1: indexing and keeping the index in step with the disk."""
import os

from sniff.ingest import scan
from sniff.search import search


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_index_then_search(conn, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("Electricity bill for March. Amount due 1200.")
    (tmp_path / "docs" / "b.txt").write_text("Lecture notes on transformers and attention.")
    counts = scan.scan(conn, tmp_path / "docs")
    assert counts["indexed"] == 2 and counts["errors"] == 0
    hits = search(conn, "electricity", mode="bm25")
    assert hits[0].name == "a.txt"
    types = dict(conn.execute("SELECT path, doc_type FROM files").fetchall())
    assert sorted(types.values()) == ["other", "utility_bill"]
    assert count(conn, "facts") == 1  # only the bill gets facts


def test_unchanged_files_are_skipped(conn, tmp_path):
    (tmp_path / "a.txt").write_text("Electricity bill")
    scan.scan(conn, tmp_path)
    counts = scan.scan(conn, tmp_path)
    assert counts == {"indexed": 0, "unchanged": 0, "renamed": 0, "removed": 0, "errors": 0}


def test_touched_but_same_content_is_not_reindexed(conn, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill")
    scan.scan(conn, tmp_path)
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 10))
    assert scan.scan(conn, tmp_path)["unchanged"] == 1


def test_changed_file_replaces_old_rows(conn, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill for March")
    scan.scan(conn, tmp_path)
    f.write_text("Gas bill for April, a different text")
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 10))
    assert scan.scan(conn, tmp_path)["indexed"] == 1
    assert search(conn, "March", mode="bm25") == []
    assert search(conn, "April", mode="bm25")[0].name == "a.txt"
    assert count(conn, "chunks") == count(conn, "chunk_vec")  # no orphan vectors


def test_rename_keeps_rows_without_reindexing(conn, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill")
    scan.scan(conn, tmp_path)
    file_id = conn.execute("SELECT id FROM files").fetchone()[0]
    f.rename(tmp_path / "renamed.txt")
    counts = scan.scan(conn, tmp_path)
    assert counts["renamed"] == 1 and counts["indexed"] == 0
    assert conn.execute("SELECT id, path FROM files").fetchone()[0] == file_id


def test_deleted_file_is_removed(conn, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill")
    scan.scan(conn, tmp_path)
    f.unlink()
    assert scan.scan(conn, tmp_path)["removed"] == 1
    assert count(conn, "files") == count(conn, "chunks") == count(conn, "chunk_vec") == count(conn, "facts") == 0


def test_stale_check(conn, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Electricity bill")
    scan.scan(conn, tmp_path)
    file_id = conn.execute("SELECT id FROM files").fetchone()[0]
    assert not scan.is_stale(conn, file_id)
    f.write_text("Electricity bill, edited after indexing")
    assert scan.is_stale(conn, file_id)


def test_bad_file_does_not_stop_scan(conn, tmp_path):
    (tmp_path / "good.txt").write_text("Electricity bill")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    counts = scan.scan(conn, tmp_path)
    assert counts["indexed"] == 1 and counts["errors"] == 1
    assert conn.execute("SELECT status FROM files WHERE path LIKE '%broken.pdf'").fetchone()[0] == "error"
