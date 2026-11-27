"""The desktop entry point: the first-run download fetches only the embedding model, resumes after a dropped
connection and checks sha256; This laptop's chat model is a separate, optional download."""
import hashlib
import io
import os
import socket
import time

import pytest
from fastapi.testclient import TestClient

from sniff import config, desktop


class Response(io.BytesIO):
    status = 206


class Dropped(Response):
    def read(self, n=-1):  # the connection breaks after the first chunk
        if self.tell() >= desktop.CHUNK:
            raise ConnectionResetError("connection dropped")
        return super().read(n)


@pytest.fixture
def model(tmp_path, monkeypatch):
    data = os.urandom(3 * desktop.CHUNK + 5)
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "MODELS", {"m.gguf": ("http://models.test/m", hashlib.sha256(data).hexdigest(), len(data))})
    monkeypatch.setattr(desktop, "SETUP", ["m.gguf"])
    return data


@pytest.fixture
def registry(monkeypatch):
    """Both models under their real names, with small fake contents from a fake registry. Returns the files asked for."""
    files = {name: os.urandom(desktop.CHUNK + 7) for name in desktop.MODELS}
    monkeypatch.setattr(desktop, "MODELS", {n: (f"http://models.test/{n}", hashlib.sha256(d).hexdigest(), len(d)) for n, d in files.items()})
    monkeypatch.setattr(desktop, "STATE", {"running": False, "error": None})
    monkeypatch.setattr(desktop, "CHAT_STATE", {"running": False, "error": None})
    asked = []

    def urlopen(req, timeout):
        asked.append(name := req.full_url.rsplit("/", 1)[1])
        return Response(files[name])

    monkeypatch.setattr(desktop.urllib.request, "urlopen", urlopen)
    return asked


def finished(client, path):
    for _ in range(500):
        if not (s := client.get(path).json())["running"]:
            return s
        time.sleep(0.01)
    raise TimeoutError(path)


def test_download_resumes_after_a_dropped_connection(model, tmp_path, monkeypatch):
    starts = []

    def urlopen(req, timeout):
        start = int(req.get_header("Range").removeprefix("bytes=").rstrip("-"))
        starts.append(start)
        return (Dropped if len(starts) == 1 else Response)(model[start:])

    monkeypatch.setattr(desktop.urllib.request, "urlopen", urlopen)
    desktop.fetch("m.gguf")
    assert starts == [0, desktop.CHUNK]  # the second request asks only for the rest
    assert (tmp_path / "m.gguf").read_bytes() == model
    assert not (tmp_path / "m.gguf.part").exists()


def test_download_with_a_wrong_checksum_is_deleted(model, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda req, timeout: Response(b"x" * len(model)))
    with pytest.raises(RuntimeError, match="checksum"):
        desktop.fetch("m.gguf")
    assert list(tmp_path.iterdir()) == []


def test_setup_reports_missing_models_and_no_servers(model, monkeypatch):
    monkeypatch.setattr(config, "EMBED_URL", "http://127.0.0.1:9/v1")  # port 9: nothing listens there
    s = TestClient(desktop.app).get("/setup").json()
    assert (s["servers"], s["ready"], s["running"]) == (False, False, False)
    assert s["models"] == [{"name": "m.gguf", "size": len(model), "have": 0}]


def test_first_run_fetches_only_the_embedding_model(registry, monkeypatch):
    c = TestClient(desktop.app)
    assert [m["name"] for m in c.get("/setup").json()["models"]] == ["all-minilm-l6-v2.gguf"]
    with socket.create_server(("127.0.0.1", 0)) as embed_server:  # the embedding server answers; no chat server does
        monkeypatch.setattr(config, "EMBED_URL", f"http://127.0.0.1:{embed_server.getsockname()[1]}/v1")
        c.post("/setup/download")
        s = finished(c, "/setup")
    assert registry == ["all-minilm-l6-v2.gguf"]
    assert (s["ready"], s["servers"], s["error"]) == (True, True, None)  # the app opens without the chat model
    assert not (config.MODELS_DIR / desktop.CHAT).exists()


def test_this_laptops_model_is_an_optional_download(registry):
    c = TestClient(desktop.app)
    s = c.get("/ai/local").json()
    assert (s["name"], s["ready"], s["have"], s["running"]) == (desktop.CHAT, False, 0, False)
    assert c.get("/ai/settings").json()["local_available"] is False  # Settings > AI hides This laptop
    assert c.put("/ai/settings", json={"provider": "local"}).status_code == 422

    c.post("/ai/local/download")
    s = finished(c, "/ai/local")
    assert registry == [desktop.CHAT]
    assert (s["ready"], s["have"], s["error"]) == (True, s["size"], None)
    assert c.get("/ai/settings").json()["local_available"] is True
    assert c.put("/ai/settings", json={"provider": "local"}).json()["provider"] == "local"
