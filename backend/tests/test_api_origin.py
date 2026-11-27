"""Only Sniff's own window can read the local API; other websites and DNS-rebinding hosts cannot."""
import pytest
from fastapi.testclient import TestClient

from sniff import api


@pytest.mark.parametrize("origin", ["http://tauri.localhost", "tauri://localhost", "http://localhost:5173", "http://127.0.0.1:4173"])
def test_sniffs_own_window_may_read(origin):
    r = TestClient(api.app).options("/search", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost.evil.example", "null"])
def test_other_websites_get_no_cors(origin):
    r = TestClient(api.app).options("/search", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_a_rebinding_host_name_is_refused():
    assert TestClient(api.app, base_url="http://evil.example:8765").get("/setup").status_code == 400
    assert TestClient(api.app, base_url="http://127.0.0.1:8765").get("/docs").status_code == 200
