"""MCP server definition: tool registration and result shaping."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .auth import GoogleServices
from .config import Settings
from .docs_service import append_to_document
from .errors import map_exception
from .gmail_service import send_email as send_gmail
from .logging_setup import get_logger

log = get_logger("server")

INSTRUCTIONS = (
    "Tools for acting on the user's Google account: send an email through Gmail "
    "(send_email) and append text to the end of an existing Google Doc "
    "(append_to_google_doc). Both actions are real and cannot be undone by this server; "
    "confirm recipients and content with the user when in doubt, and use send_email with "
    "dry_run=true to validate a message without sending it."
)

SEND_EMAIL_DESCRIPTION = (
    "Send an email from the user's authenticated Gmail account. Use this when the user asks "
    "to email someone. The email is really sent unless dry_run is true. "
    "Returns JSON: status 'sent', message_id, thread_id, to."
)
APPEND_DESCRIPTION = (
    "Append text to the end of an existing Google Doc without altering its existing content. "
    "Use this to add notes, logs or summaries to a document the user already has. "
    "Returns JSON: status 'appended', document_id, characters_appended, document_url."
)


def _json_result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=is_error,
    )


async def _run_tool(
    tool: str, fn: Callable[[], dict[str, Any]], log_keys: tuple[str, ...]
) -> CallToolResult:
    """Run a blocking tool body off the event loop; convert every outcome to a tool result."""
    started = time.perf_counter()
    try:
        result = await anyio.to_thread.run_sync(fn)
    except Exception as exc:  # noqa: BLE001 - mapped to a stable error code below
        failure = map_exception(exc)
        log.warning(
            "tool_failed",
            extra={
                "tool": tool,
                "error_code": failure.code,
                "exc_type": type(exc).__name__,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return _json_result(
            {"status": "error", "error_code": failure.code, "message": failure.message},
            is_error=True,
        )

    meta = {k: result[k] for k in log_keys if k in result}
    log.info(
        "tool_ok",
        extra={
            "tool": tool,
            "status": result.get("status"),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            **meta,
        },
    )
    return _json_result(result)


def create_server(settings: Settings, services: Any | None = None) -> MCPServer:
    """Build the MCP server. ``services`` is injectable for tests."""
    services = services if services is not None else GoogleServices(settings)
    mcp = MCPServer(
        "gmail-docs",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=settings.log_level,  # type: ignore[arg-type]
    )

    @mcp.tool(
        name="send_email",
        title="Send email via Gmail",
        description=SEND_EMAIL_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Send email via Gmail",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def send_email(
        to: Annotated[
            list[str],
            Field(
                description="Recipient email addresses, e.g. ['alice@example.com']. "
                "At least one is required.",
            ),
        ],
        subject: Annotated[
            str, Field(description="Email subject line (single line), e.g. 'Weekly status'.")
        ],
        body: Annotated[
            str,
            Field(
                description="Message body. Plain text by default; HTML when is_html is true."
            ),
        ],
        cc: Annotated[
            list[str],
            Field(description="Optional CC recipient addresses. Omit or use [] for none."),
        ] = [],  # noqa: B006 - pydantic copies defaults; never mutated
        bcc: Annotated[
            list[str],
            Field(description="Optional BCC recipient addresses. Omit or use [] for none."),
        ] = [],  # noqa: B006
        is_html: Annotated[
            bool, Field(description="Set true if body is HTML. Default false (plain text).")
        ] = False,
        dry_run: Annotated[
            bool,
            Field(
                description="If true, validate and build the message but do NOT send it. "
                "Default false."
            ),
        ] = False,
    ) -> CallToolResult:
        return await _run_tool(
            "send_email",
            lambda: send_gmail(
                services,
                settings,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                is_html=is_html,
                dry_run=dry_run,
            ),
            log_keys=("message_id",),
        )

    @mcp.tool(
        name="append_to_google_doc",
        title="Append text to a Google Doc",
        description=APPEND_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Append text to a Google Doc",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def append_to_google_doc(
        document: Annotated[
            str,
            Field(
                description="The Google Doc ID or its full URL, e.g. "
                "'https://docs.google.com/document/d/1AbC...xyz/edit'."
            ),
        ],
        text: Annotated[
            str,
            Field(description="Text to append at the end of the document. Newlines are kept."),
        ],
        add_leading_newline: Annotated[
            bool,
            Field(
                description="If true (default), start the appended text on a new line."
            ),
        ] = True,
    ) -> CallToolResult:
        return await _run_tool(
            "append_to_google_doc",
            lambda: append_to_document(
                services,
                settings,
                document=document,
                text=text,
                add_leading_newline=add_leading_newline,
            ),
            log_keys=("document_id", "characters_appended"),
        )

    return mcp
