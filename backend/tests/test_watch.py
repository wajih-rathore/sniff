"""F1 live watching: real file events in a temp folder, fake models, short waits."""
import time

import pytest
from fastapi.testclient import TestClient

from sniff import api, watch


def wait_for(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def rows(conn):
    return {r["path"]: r for r in conn.execute("SELECT * FROM files WHERE status='indexed'")}


@pytest.fixture
def watcher(conn):
    w = watch.Watcher({"running": False}, debounce=0.3, poll=0.05)
    yield w
    w.stop()


def test_relevant_ignores_temp_and_partial_files():
    assert watch.relevant("C:/d/bill.pdf") and watch.relevant("C:/d/notes.TXT") and watch.relevant("C:/d/sub", is_dir=True)
    for name in ("~$bill.docx", "bill.pdf.crdownload", "bill.pdf.part", "bill.tmp", "setup.exe"):
        assert not watch.relevant("C:/d/" + name)


def test_create_modify_rename_delete(conn, tmp_path, watcher):
    folder = tmp_path / "docs"
    folder.mkdir()
    watcher.add(folder)
    f = folder / "a.txt"

    f.write_text("Electricity bill for March. Amount due 1200.")
    assert wait_for(lambda: len(rows(conn)) == 1), "new file was not indexed"
    old = next(iter(rows(conn).values()))
    assert old["doc_type"] == "utility_bill"

    f.write_text("Lecture notes on transformers and attention.")
    assert wait_for(lambda: rows(conn).get(str(f.resolve())) and rows(conn)[str(f.resolve())]["doc_type"] == "other"), "edit was not re-indexed"

    g = folder / "renamed.txt"
    f.rename(g)
    assert wait_for(lambda: list(rows(conn)) == [str(g.resolve())]), "rename not followed"
    assert rows(conn)[str(g.resolve())]["id"] == old["id"]  # same row, new path: not indexed again

    g.unlink()
    assert wait_for(lambda: not rows(conn)), "deleted file still in the index"
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert watcher.status["watch_last"]["folder"] == str(folder.resolve())


def test_temp_and_partial_files_are_ignored(conn, tmp_path, watcher):
    folder = tmp_path / "docs"
    folder.mkdir()
    watcher.add(folder)
    assert wait_for(lambda: "watch_last" in watcher.status)  # the first catch-up scan
    for name in ("~$a.docx", "b.pdf.crdownload", "c.part", "d.tmp"):
        (folder / name).write_text("Electricity bill")
    time.sleep(0.5)
    assert not watcher.pending and not rows(conn)


def test_a_file_that_is_still_growing_waits(conn, tmp_path, watcher):
    folder = tmp_path / "docs"
    folder.mkdir()
    watcher.add(folder)
    assert wait_for(lambda: "watch_last" in watcher.status)
    f = folder / "big.txt"
    with open(f, "w") as out:
        for i in range(5):  # a slow writer: gaps shorter than the debounce
            out.write("Electricity bill line\n")
            out.flush()
            time.sleep(0.15)
            assert not rows(conn)
    assert wait_for(lambda: len(rows(conn)) == 1)


def test_watched_folders_come_back_after_restart(conn, tmp_path):
    (tmp_path / "docs").mkdir()
    first = watch.Watcher({}, debounce=0.3, poll=0.05)
    first.add(tmp_path / "docs")
    first.stop()
    again = watch.Watcher({}, debounce=0.3, poll=0.05)
    try:
        again.restore()
        assert again.folders() == [str((tmp_path / "docs").resolve())]
        assert again.remove(tmp_path / "docs") and not again.remove(tmp_path / "docs")
    finally:
        again.stop()
    assert conn.execute("SELECT count(*) FROM watched").fetchone()[0] == 0


def test_watch_endpoints(conn, tmp_path, monkeypatch):
    w = watch.Watcher(api.STATUS, debounce=0.3, poll=0.05)
    monkeypatch.setattr(api, "WATCHER", w)
    client = TestClient(api.app)
    folder = tmp_path / "docs"
    folder.mkdir()
    try:
        assert client.post("/watch", json={"folder": str(tmp_path / "nope")}).status_code == 400
        assert client.get("/watch").json()["folders"] == []
        assert client.post("/watch", json={"folder": str(folder)}).json()["folders"] == [str(folder.resolve())]
        assert api.STATUS["watching"] == [str(folder.resolve())]
        (folder / "a.txt").write_text("Electricity bill. Amount due 1200.")
        assert wait_for(lambda: len(rows(conn)) == 1)
        assert client.request("DELETE", "/watch", json={"folder": str(folder)}).json()["folders"] == []
        assert client.request("DELETE", "/watch", json={"folder": str(folder)}).status_code == 404
        (folder / "b.txt").write_text("Electricity bill. Amount due 900.")
        time.sleep(0.8)
        assert len(rows(conn)) == 1  # no longer watched
    finally:
        w.stop()


def test_index_and_watcher_scan_do_not_overlap(conn, tmp_path, monkeypatch):
    """/index starts while the watcher's scan of the same new files is due: the second waits, each file is indexed once."""
    from sniff.ingest import scan
    calls = []
    real = scan.index_file

    def slow(conn, path, sha, ai=True):
        calls.append(path.name)
        time.sleep(0.5)  # long enough for the two scans to overlap if nothing stopped them
        return real(conn, path, sha, ai)

    monkeypatch.setattr(scan, "index_file", slow)
    api.STATUS.update(running=False, indexing=False)
    w = watch.Watcher(api.STATUS, debounce=0.3, poll=0.05)
    monkeypatch.setattr(api, "WATCHER", w)
    client = TestClient(api.app)
    folder = tmp_path / "docs"
    folder.mkdir()
    try:
        client.post("/watch", json={"folder": str(folder)})
        assert wait_for(lambda: "watch_last" in api.STATUS)
        for i in range(3):
            (folder / f"bill{i}.txt").write_text(f"Electricity bill number {i}")
        assert client.post("/index", json={"folder": str(folder)}).json() == {"started": True}
        assert wait_for(lambda: len(rows(conn)) == 3)
        time.sleep(2)  # the watcher's own scan runs after the /index one has finished
        assert sorted(calls) == ["bill0.txt", "bill1.txt", "bill2.txt"]
        assert conn.execute("SELECT count(*) FROM actions WHERE kind='indexed'").fetchone()[0] == 3
    finally:
        w.stop()
