"""MCP settings in the database: off by default, a refusal while off, validated PUT, read on every call, the log endpoint."""
import json

import pytest
from fastapi.testclient import TestClient

from sniff import api, config, mcp_server, mcp_settings


@pytest.fixture
def client(conn):
    return TestClient(api.app)


def logged():
    return [json.loads(line) for line in (config.DATA_DIR / "mcp_access.log").read_text(encoding="utf-8").splitlines()]


def test_off_by_default_and_every_tool_refuses_but_logs(conn, add_doc):
    add_doc("C:/docs/bill.pdf", "utility_bill", {"provider": "LESCO"})
    assert mcp_settings.load(conn) == {"enabled": False, "allow": config.MCP_ALLOW, "env_override": False}
    for call in (lambda: mcp_server.list_documents(), lambda: mcp_server.search_documents("bill"), lambda: mcp_server.ask("bill?")):
        with pytest.raises(ValueError, match="turned off"):
            call()
    rows = logged()
    assert [r["tool"] for r in rows] == ["list_documents", "search_documents", "ask"]
    assert all(r["refused"] and r["ids"] == [] for r in rows)


def test_default_allow_list_hides_the_private_types(conn):
    allow = mcp_settings.load(conn)["allow"]
    for hidden in ("id_card", "passport", "bank_statement", "offer_letter", "transcript"):
        assert hidden not in allow


def test_settings_are_read_on_every_call(conn, add_doc):
    add_doc("C:/docs/bill.pdf", "utility_bill", {"provider": "LESCO"})
    with pytest.raises(ValueError):
        mcp_server.list_documents()
    mcp_settings.save(conn, True, {"utility_bill": ["provider"]})  # no restart in between
    assert mcp_server.list_documents()[0]["facts"] == {"provider": "LESCO"}
    mcp_settings.save(conn, False, {"utility_bill": ["provider"]})
    with pytest.raises(ValueError):
        mcp_server.list_documents()


def test_env_var_overrides_the_database(conn, monkeypatch):
    mcp_settings.save(conn, False, {"receipt": ["*"]})
    monkeypatch.setattr(config, "MCP_ENV_OVERRIDE", True)
    monkeypatch.setattr(config, "MCP_ALLOW", {"utility_bill": ["*"]})
    assert mcp_settings.load(conn) == {"enabled": True, "allow": {"utility_bill": ["*"]}, "env_override": True}


def test_put_validates_types_and_fields(client, conn):
    assert client.get("/mcp/settings").json()["enabled"] is False
    ok = client.put("/mcp/settings", json={"enabled": True, "allow": {"utility_bill": ["provider", "amount_due"], "other": ["*"]}})
    assert ok.status_code == 200 and ok.json()["enabled"] and ok.json()["allow"]["utility_bill"] == ["provider", "amount_due"]
    assert client.get("/mcp/settings").json() == ok.json()
    bad_type = client.put("/mcp/settings", json={"enabled": True, "allow": {"driving_licence": ["*"]}})
    assert bad_type.status_code == 422 and "driving_licence" in bad_type.json()["detail"]
    bad_field = client.put("/mcp/settings", json={"enabled": True, "allow": {"utility_bill": ["provider", "shoe_size"]}})
    assert bad_field.status_code == 422 and "shoe_size" in bad_field.json()["detail"]
    assert client.put("/mcp/settings", json={"enabled": True, "allow": {"other": ["name"]}}).status_code == 422  # other has no fields
    assert client.get("/mcp/settings").json() == ok.json()  # rejected requests changed nothing


def test_log_endpoint_returns_newest_first(client, conn):
    assert client.get("/mcp/log").json() == []
    for tool in ("a", "b", "c"):
        mcp_server.log(tool, {}, [1])
    assert [r["tool"] for r in client.get("/mcp/log").json()] == ["c", "b", "a"]
    assert [r["tool"] for r in client.get("/mcp/log?limit=2").json()] == ["c", "b"]
