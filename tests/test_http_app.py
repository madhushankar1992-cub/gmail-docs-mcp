import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gmail_docs_mcp.config import ConfigError, Settings, load_settings
from gmail_docs_mcp.http_app import BearerAuthMiddleware, transport_security


def _app(token="s3cret"):
    async def ok(request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])])
    return BearerAuthMiddleware(inner, token)


def test_requires_bearer_token():
    client = TestClient(_app())
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Basic s3cret"}).status_code == 401
    assert client.get("/mcp").headers["www-authenticate"].startswith("Bearer")


def test_accepts_correct_token():
    client = TestClient(_app())
    resp = client.post("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200 and resp.text == "ok"


def test_browser_visit_gets_status_page_but_mcp_clients_stay_protected():
    client = TestClient(_app())
    html = {"Accept": "text/html,application/xhtml+xml"}
    for path in ("/", "/health", "/mcp"):
        resp = client.get(path, headers=html)
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
    sse = {"Accept": "text/event-stream"}
    assert client.get("/mcp", headers=sse).status_code == 401
    assert client.get("/mcp").status_code == 401
    assert client.post("/mcp", headers=html).status_code == 401
    # a wrong token is still rejected even from a "browser"
    wrong = {**html, "Authorization": "Bearer wrong"}
    assert client.get("/mcp", headers=wrong).status_code == 401


def test_empty_token_is_refused():
    with pytest.raises(ValueError):
        BearerAuthMiddleware(Starlette(), "")


def test_allowed_hosts_adds_security_settings(tmp_path):
    base = Settings(credentials_path=tmp_path / "c", token_path=tmp_path / "t")
    assert transport_security(base) is None
    custom = Settings(
        credentials_path=tmp_path / "c", token_path=tmp_path / "t", allowed_hosts=("x.example.com",)
    )
    sec = transport_security(custom)
    assert "x.example.com" in sec.allowed_hosts and "127.0.0.1:*" in sec.allowed_hosts


def test_config_defaults_and_validation():
    s = load_settings({})
    assert (s.transport, s.host, s.port, s.max_recipients) == ("stdio", "127.0.0.1", 8000, 50)
    with pytest.raises(ConfigError):
        load_settings({"MCP_TRANSPORT": "carrier-pigeon"})
    with pytest.raises(ConfigError):
        load_settings({"MCP_PORT": "abc"})
    assert load_settings({"MCP_ALLOWED_HOSTS": "a.com, b.com"}).allowed_hosts == ("a.com", "b.com")
    creds = load_settings({"GOOGLE_CLIENT_ID": " id ", "GOOGLE_CLIENT_SECRET": "GOCSPX-hunter2"})
    assert (creds.google_client_id, creds.google_client_secret) == ("id", "GOCSPX-hunter2")
    assert "hunter2" not in repr(creds)


def test_port_falls_back_to_platform_port_env():
    assert load_settings({"PORT": "5555"}).port == 5555
    assert load_settings({"PORT": "5555", "MCP_PORT": "9000"}).port == 9000
