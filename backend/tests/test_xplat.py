"""What differs per operating system in the back end, checked with a mocked platform: the app folder, the
llama-server binary, the Google token store, and the Unix clean-up after a crashed app."""
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import keyring
import keyring.backend
import pytest

from sniff import config, db, desktop, gcal
from sniff.ai import server


def test_app_home_per_os():
    assert config.app_home("win32", {"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}) == Path(r"C:\Users\a\AppData\Local", "Sniff")
    assert config.app_home("darwin", {"HOME": "/Users/a"}) == Path("/Users/a/Library/Application Support/Sniff")
    assert config.app_home("linux", {"HOME": "/home/a"}) == Path("/home/a/.local/share/sniff")
    assert config.app_home("linux", {"HOME": "/home/a", "XDG_DATA_HOME": "/data/a"}) == Path("/data/a/sniff")
    # the XDG spec says to ignore a relative path
    assert config.app_home("linux", {"HOME": "/home/a", "XDG_DATA_HOME": "rel"}) == Path("/home/a/.local/share/sniff")


def test_llama_server_name_and_lookup(tmp_path, monkeypatch):
    monkeypatch.delenv("SNIFF_LLAMA_SERVER", raising=False)
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert server.exe("win32", frozen=False) == tmp_path / "llama" / "llama-server.exe"
    assert server.exe("linux", frozen=False) == tmp_path / "llama" / "llama-server"  # missing: can_start() says no
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    assert server.exe("darwin", frozen=False) == Path("/opt/homebrew/bin/llama-server")  # Homebrew's llama.cpp
    (tmp_path / "llama").mkdir()
    (tmp_path / "llama" / "llama-server").touch()
    assert server.exe("linux", frozen=False) == tmp_path / "llama" / "llama-server"  # models/llama wins over the PATH
    # the desktop app: resources/backend/sniff-backend next to resources/llama
    monkeypatch.setattr(sys, "executable", str(tmp_path / "res" / "backend" / "sniff-backend"))
    assert server.exe("darwin", frozen=True) == (tmp_path / "res" / "llama" / "llama-server").resolve()
    monkeypatch.setenv("SNIFF_LLAMA_SERVER", "/usr/bin/llama-server")
    assert server.exe("linux", frozen=True) == Path("/usr/bin/llama-server")


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def test_token_goes_to_the_credential_store(tmp_path, monkeypatch):
    store = MemoryKeyring()
    monkeypatch.setattr(keyring.core, "_keyring_backend", store)
    monkeypatch.setattr(gcal, "TOKEN_FILE", tmp_path / "google_token.json")
    gcal.TOKEN_FILE.write_text('{"old": 1}')  # an older version kept it in a file
    assert gcal.load_token() == '{"old": 1}'
    gcal.save_token('{"refresh_token": "r"}')
    assert store.items[gcal.KEYRING] == '{"refresh_token": "r"}' and not gcal.TOKEN_FILE.exists()
    assert gcal.load_token() == '{"refresh_token": "r"}'


def test_token_falls_back_to_a_file_without_a_store(tmp_path, monkeypatch):
    # conftest.py selects keyring's "fail" backend, the one keyring uses when a system has no store
    monkeypatch.setattr(gcal, "TOKEN_FILE", tmp_path / "data" / "google_token.json")
    assert gcal.load_token() is None
    gcal.save_token('{"refresh_token": "r"}')
    assert gcal.TOKEN_FILE.read_text() == '{"refresh_token": "r"}'
    assert gcal.load_token() == '{"refresh_token": "r"}'


def test_backend_ends_its_group_when_the_app_dies(monkeypatch):
    parents = iter([100, 100, 100, 1])  # the app's pid, then init's after the app crashed
    killed = []
    monkeypatch.setattr(desktop.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(desktop.os, "getpgrp", lambda: 4242, raising=False)
    monkeypatch.setattr(desktop.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False)
    desktop.end_with_parent(poll=0)
    assert killed == [(4242, signal.SIGTERM)]


def test_db_says_how_to_get_a_python_that_loads_sqlite_extensions(monkeypatch):
    """python.org's macOS Python has no enable_load_extension (issue #18): say what to do, not AttributeError."""
    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **k: SimpleNamespace(execute=lambda sql: None))
    with pytest.raises(RuntimeError, match="only-managed"):
        db.connect(":memory:")
