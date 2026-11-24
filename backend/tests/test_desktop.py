"""The desktop entry point: the first-run model download resumes after a dropped connection and checks sha256."""
import hashlib
import io
import os

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
    return data


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
    monkeypatch.setattr(config, "LLM_URL", "http://127.0.0.1:9/v1")  # port 9: nothing listens there
    s = TestClient(desktop.app).get("/setup").json()
    assert (s["servers"], s["ready"], s["running"]) == (False, False, False)
    assert s["models"] == [{"name": "m.gguf", "size": len(model), "have": 0}]
