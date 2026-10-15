"""End-to-end: launch the real server process and talk to it over each transport."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

TOKEN = "test-bearer-token"

EXPECTED_TOOLS = {
    "send_email",
    "append_to_google_doc",
    "docs_create_document",
    "docs_write_content",
    "docs_get_document",
    "docs_find_by_marker",
    "docs_get_page_count",
    "gmail_create_draft",
    "gmail_get_draft",
    "gmail_find_drafts",
}


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GOOGLE_CREDENTIALS_PATH=str(tmp_path / "credentials.json"),
        GOOGLE_TOKEN_PATH=str(tmp_path / "token.json"),
        PYTHONUNBUFFERED="1",
        **extra,
    )
    return env


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _text(result) -> dict:
    return json.loads(result.content[0].text)


async def test_stdio_subprocess_speaks_clean_protocol(tmp_path):
    """Any stray stdout write would corrupt the stream and fail this test."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "gmail_docs_mcp"], env=_env(tmp_path)
    )
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == EXPECTED_TOOLS

        dry = await c.call_tool(
            "send_email",
            {"to": ["a@example.com"], "subject": "Hi", "body": "Hello", "dry_run": True},
        )
        assert _text(dry)["status"] == "dry_run"

        # No token on disk: must come back as a clean AUTH_REQUIRED tool error.
        real = await c.call_tool(
            "send_email", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
        )
        assert real.is_error and _text(real)["error_code"] == "AUTH_REQUIRED"


@pytest.fixture
def http_server(tmp_path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "gmail_docs_mcp", "--transport", "http", "--port", str(port)],
        env=_env(tmp_path, MCP_AUTH_TOKEN=TOKEN),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("HTTP server exited during startup")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("HTTP server did not start in time")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_http_rejects_missing_or_wrong_token(http_server):
    assert httpx.post(http_server, json={}).status_code == 401
    wrong = httpx.post(http_server, json={}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


async def test_http_with_token_serves_tools(http_server):
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        async with Client(streamable_http_client(http_server, http_client=http)) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert names == EXPECTED_TOOLS
            result = await c.call_tool(
                "send_email",
                {"to": ["a@example.com"], "subject": "Hi", "body": "Hello", "dry_run": True},
            )
            assert _text(result)["status"] == "dry_run"


def test_http_transport_refuses_to_start_without_token(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "gmail_docs_mcp", "--transport", "http", "--port", "0"],
        env=_env(tmp_path, MCP_AUTH_TOKEN=""),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2
    assert "MCP_AUTH_TOKEN" in proc.stderr
