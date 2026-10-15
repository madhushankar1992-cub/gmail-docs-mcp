"""Drive the real MCP server in-process through an MCP client."""

from __future__ import annotations

import json

from mcp import Client

from gmail_docs_mcp.errors import ERROR_CODES
from gmail_docs_mcp.server import create_server
from tests.conftest import make_http_error

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-aB"
FORBIDDEN_SCHEMA_KEYS = {"anyOf", "oneOf", "allOf", "$ref", "$defs", "not"}

READ_ONLY_TOOLS = {
    "docs_get_document",
    "docs_find_by_marker",
    "docs_get_page_count",
    "gmail_get_draft",
    "gmail_find_drafts",
}

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


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def connect(settings, services=None):
    """In-process MCP client. Used inline per test: anyio scopes can't span fixture tasks."""
    return Client(create_server(settings, services))


async def test_tools_are_listed_with_portable_schemas_and_annotations(settings, services):
    async with connect(settings, services) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS

        for tool in tools.values():
            assert tool.description and len(tool.name) < 64
            assert tool.name.replace("_", "").isalnum() and tool.name == tool.name.lower()
            assert not FORBIDDEN_SCHEMA_KEYS & set(_keys(tool.input_schema))
            for name, prop in tool.input_schema["properties"].items():
                assert prop.get("description"), f"{tool.name}.{name} lacks a description"
            a = tool.annotations
            assert a.destructive_hint is False and a.open_world_hint is True
            if tool.name in READ_ONLY_TOOLS:
                assert a.read_only_hint is True
            elif tool.name in ("send_email", "append_to_google_doc", "gmail_create_draft"):
                assert (a.read_only_hint, a.idempotent_hint) == (False, False)

        assert tools["send_email"].input_schema["required"] == ["to", "subject", "body"]
        assert tools["append_to_google_doc"].input_schema["required"] == ["document", "text"]


async def test_send_email_success_is_json_text(settings, services):
    async with connect(settings, services) as c:
        result = await c.call_tool(
            "send_email", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
        )
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "sent" and payload["message_id"] == "msg123"


async def test_send_email_dry_run(settings, services):
    async with connect(settings, services) as c:
        result = await c.call_tool(
            "send_email",
            {"to": ["a@example.com"], "subject": "Hi", "body": "Hello", "dry_run": True},
        )
        assert json.loads(result.content[0].text)["status"] == "dry_run"
        services.gmail_send.assert_not_called()


async def test_validation_failure_is_error_result_with_code(settings, services):
    async with connect(settings, services) as c:
        result = await c.call_tool(
            "send_email", {"to": ["not-an-email"], "subject": "Hi", "body": "Hello"}
        )
        assert result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["error_code"] == "INVALID_INPUT"
        assert payload["message"]


async def test_append_success(settings, services):
    async with connect(settings, services) as c:
        result = await c.call_tool(
            "append_to_google_doc",
            {"document": f"https://docs.google.com/document/d/{DOC_ID}/edit", "text": "log line"},
        )
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "appended" and payload["document_id"] == DOC_ID


async def test_google_failure_maps_to_stable_code(settings, services):
    async with connect(settings, services) as c:
        services.docs_execute.side_effect = make_http_error(404)
        result = await c.call_tool("append_to_google_doc", {"document": DOC_ID, "text": "x"})
        assert result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["error_code"] == "NOT_FOUND"
        assert payload["error_code"] in ERROR_CODES


async def test_missing_auth_returns_auth_required(settings):
    # Real GoogleServices with no token on disk: must fail cleanly, never prompt or crash.
    async with Client(create_server(settings)) as c:
        result = await c.call_tool(
            "send_email", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
        )
    assert result.is_error
    payload = json.loads(result.content[0].text)
    assert payload["error_code"] == "AUTH_REQUIRED"
    assert "auth" in payload["message"]
