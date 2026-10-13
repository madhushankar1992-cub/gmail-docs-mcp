"""Streamable HTTP transport wrapped with a mandatory static bearer token."""

from __future__ import annotations

import hmac

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class BearerAuthMiddleware:
    """Reject every HTTP request that lacks ``Authorization: Bearer <token>``."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("A bearer token is required for HTTP transport.")
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan events
            await self.app(scope, receive, send)
            return

        header = Headers(scope=scope).get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        ok = scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode("utf-8"), self._token
        )
        if not ok:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="gmail-docs-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def transport_security(settings: Settings) -> TransportSecuritySettings | None:
    """DNS-rebinding protection; extra hostnames come from MCP_ALLOWED_HOSTS (tunnels/proxies)."""
    if not settings.allowed_hosts:
        return None  # SDK auto-enables localhost-only protection for loopback binds
    hosts: list[str] = []
    origins: list[str] = []
    for host in (*LOCAL_HOSTS, *settings.allowed_hosts):
        hosts.extend([host, f"{host}:*"])
        origins.extend([f"https://{host}", f"http://{host}", f"http://{host}:*"])
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins
    )


def build_http_app(settings: Settings, mcp: MCPServer) -> ASGIApp:
    inner = mcp.streamable_http_app(
        host=settings.host, transport_security=transport_security(settings)
    )
    return BearerAuthMiddleware(inner, settings.auth_token)
