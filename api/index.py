"""Vercel serverless entry point: stateless Streamable HTTP MCP endpoint.

Serverless functions cannot keep MCP sessions in memory between requests, so every request is
handled independently (stateless_http) and answered as plain JSON. Every request must carry
``Authorization: Bearer <MCP_AUTH_TOKEN>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from starlette.types import Receive, Scope, Send  # noqa: E402

from gmail_docs_mcp.auth import GoogleServices  # noqa: E402
from gmail_docs_mcp.config import load_settings  # noqa: E402
from gmail_docs_mcp.http_app import BearerAuthMiddleware  # noqa: E402
from gmail_docs_mcp.logging_setup import configure_logging  # noqa: E402
from gmail_docs_mcp.server import create_server  # noqa: E402

settings = load_settings()
configure_logging(settings.log_level)
_services = GoogleServices(settings)  # module level: warm invocations reuse refreshed creds


async def _mcp(scope: Scope, receive: Receive, send: Send) -> None:
    # The SDK's session manager can only run once per instance, so build one per request.
    mcp = create_server(settings, _services)
    # host="0.0.0.0" keeps the SDK's localhost-only Host check off; the bearer token guards access.
    inner = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    # This deployment serves only MCP, so any path the platform forwards maps to the endpoint.
    scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
    async with inner.router.lifespan_context(inner):
        await inner(scope, receive, send)


_authenticated = BearerAuthMiddleware(_mcp, settings.auth_token)


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    await _authenticated(scope, receive, send)
