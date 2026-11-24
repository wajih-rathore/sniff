"""Keep watched folders in step with the disk while the app runs.

watchdog reports that something changed. A worker waits until the folder has been quiet for `debounce` seconds and
the changed files have stopped growing, then runs scan.scan on the folder. That one call already handles new and
edited files (index_file, _mark_error on failure), renames (same content, new path) and deletes.
The watched folders are rows in the `watched` table, so they come back after a restart (restore).
"""
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config, db
from .ingest import scan

PARTIAL = {".crdownload", ".part", ".tmp"}  # a download or save that is not finished


def relevant(path: str, is_dir: bool = False) -> bool:
    """False for Office lock files (~$x.docx), partial downloads and file types Sniff does not read."""
    p = Path(path)
    ext = p.suffix.lower()
    return not p.name.startswith("~$") and ext not in PARTIAL and (is_dir or ext in config.SUPPORTED)


def _size(path: str) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None  # gone


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "Watcher", folder: str):
        self.watcher, self.folder = watcher, folder

    def on_any_event(self, event):
        if event.event_type not in ("created", "modified", "moved", "deleted"):
            return
        paths = [p for p in (event.src_path, getattr(event, "dest_path", "")) if isinstance(p, str) and p and relevant(p, event.is_directory)]
        if paths:
            self.watcher.touch(self.folder, paths)


class Watcher:
    def __init__(self, status: dict, debounce: float = 2.0, poll: float = 0.25, ai: bool = True):
        self.status, self.debounce, self.poll, self.ai = status, debounce, poll, ai
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = {}  # folder -> {"last": time of the last event, "sizes": {path: size at that time}}
        self.watches: dict = {}
        self.observer = None
        self.stopping = threading.Event()
        self.thread = None

    def start(self) -> None:
        if self.observer:
            return
        self.stopping.clear()
        self.observer = Observer()
        self.observer.start()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.observer:
            self.observer.stop()
            self.observer.join(5)
            self.thread.join(5)
        self.observer, self.watches = None, {}
        self.status["watching"] = []

    def folders(self) -> list[str]:
        return sorted(self.watches)

    def add(self, folder) -> str:
        """Watch a folder and bring it up to date once (it may have changed while Sniff was closed)."""
        folder = str(Path(folder).expanduser().resolve())
        conn = db.connect()
        try:
            conn.execute("INSERT OR IGNORE INTO watched(folder, added_at) VALUES (?,?)", (folder, datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        self._watch(folder)
        return folder

    def remove(self, folder) -> bool:
        folder = str(Path(folder).expanduser().resolve())
        conn = db.connect()
        try:
            conn.execute("DELETE FROM watched WHERE folder=?", (folder,))
            conn.commit()
        finally:
            conn.close()
        with self.lock:
            handle = self.watches.pop(folder, None)
            self.pending.pop(folder, None)
        if handle:
            self.observer.unschedule(handle)
        self.status["watching"] = self.folders()
        return handle is not None

    def restore(self) -> None:
        """Start watching the folders saved in the database. Folders that are gone are kept in the table but not watched."""
        conn = db.connect()
        try:
            rows = [r["folder"] for r in conn.execute("SELECT folder FROM watched ORDER BY folder")]
        finally:
            conn.close()
        for folder in rows:
            if Path(folder).is_dir():
                self._watch(folder)

    def _watch(self, folder: str) -> None:
        if folder not in self.watches:
            self.start()
            self.watches[folder] = self.observer.schedule(_Handler(self, folder), folder, recursive=True)
            self.status["watching"] = self.folders()
        self.touch(folder, [])

    def touch(self, folder: str, paths: list[str]) -> None:
        with self.lock:
            p = self.pending.setdefault(folder, {"last": 0.0, "sizes": {}})
            p["last"] = time.monotonic()
            p["sizes"].update({x: _size(x) for x in paths})

    def _run(self) -> None:
        while not self.stopping.wait(self.poll):
            self._tick(time.monotonic())

    def _tick(self, now: float) -> None:
        if self.status.get("running"):  # an index run is going; changes wait their turn
            return
        with self.lock:
            due = [(f, p, p["last"], dict(p["sizes"])) for f, p in self.pending.items() if now - p["last"] >= self.debounce]
        for folder, p, last, sizes in due:
            now_sizes = {x: _size(x) for x in sizes}
            with self.lock:
                if self.pending.get(folder) is not p or p["last"] != last:
                    continue  # more events arrived meanwhile
                if now_sizes != sizes:  # still being written: wait for it to settle
                    p["last"], p["sizes"] = time.monotonic(), now_sizes
                    continue
                del self.pending[folder]
            self._scan(folder)

    def _scan(self, folder: str) -> None:
        if not Path(folder).is_dir():  # an unplugged drive must not look like "everything was deleted"
            return
        conn = db.connect()
        try:
            self.status["watch_last"] = {"folder": folder, "at": datetime.now().isoformat(timespec="seconds"),
                                         **scan.scan(conn, folder, ai=self.ai, status=self.status)}
        except Exception as e:
            self.status.update(running=False, watch_error=f"{type(e).__name__}: {e}"[:300])
        finally:
            conn.close()
