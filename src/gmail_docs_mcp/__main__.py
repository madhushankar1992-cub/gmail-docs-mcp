"""CLI entry point: ``gmail-docs-mcp [serve|auth] [--transport stdio|http]``."""

from __future__ import annotations

import argparse
import dataclasses
import sys

from dotenv import load_dotenv

from .config import VALID_TRANSPORTS, ConfigError, load_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gmail-docs-mcp",
        description="MCP server: send Gmail and append to Google Docs.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("serve", "auth"),
        default="serve",
        help="serve (default): run the MCP server; auth: one-time Google sign-in.",
    )
    parser.add_argument("--transport", choices=VALID_TRANSPORTS, help="Overrides MCP_TRANSPORT.")
    parser.add_argument("--host", help="HTTP bind host (overrides MCP_HOST).")
    parser.add_argument("--port", type=int, help="HTTP bind port (overrides MCP_PORT).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    overrides = {
        k: v
        for k, v in (("transport", args.transport), ("host", args.host), ("port", args.port))
        if v is not None
    }
    settings = dataclasses.replace(settings, **overrides)

    if args.command == "auth":
        from .auth import run_auth_flow

        try:
            token_path = run_auth_flow(settings)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Authorized. Token saved to {token_path}", file=sys.stderr)
        return 0

    from .logging_setup import configure_logging
    from .server import create_server

    configure_logging(settings.log_level)
    mcp = create_server(settings)

    if settings.transport == "stdio":
        mcp.run("stdio")
        return 0

    if not settings.auth_token:
        print(
            "Configuration error: MCP_AUTH_TOKEN is required when using the http transport.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .http_app import build_http_app

    uvicorn.run(
        build_http_app(settings, mcp),
        host=settings.host,
        port=settings.port,
        log_config=None,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
