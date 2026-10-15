"""The Vercel entry point must serve stateless MCP over plain JSON with bearer auth."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ENTRY = Path(__file__).resolve().parent.parent / "api" / "index.py"
TOKEN = "vercel-test-token"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "none.json"))
    spec = importlib.util.spec_from_file_location("vercel_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as c:
        yield c


def _rpc(client, method, params=None, id_=1, path="/mcp"):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    return client.post(path, headers=HEADERS, content=json.dumps(body))


def test_rejects_missing_token(client):
    assert client.post("/mcp", json={}).status_code == 401


def test_lists_tools_statelessly_on_any_path(client):
    for path in ("/mcp", "/", "/api/index"):
        resp = _rpc(client, "tools/list", path=path)
        assert resp.status_code == 200, resp.text
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        assert names == {"send_email", "append_to_google_doc"}


def test_tool_call_without_prior_session(client):
    resp = _rpc(
        client,
        "tools/call",
        {
            "name": "send_email",
            "arguments": {"to": ["a@example.com"], "subject": "s", "body": "b", "dry_run": True},
        },
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["status"] == "dry_run"
