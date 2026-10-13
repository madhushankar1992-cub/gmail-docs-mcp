"""Environment-driven settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

VALID_TRANSPORTS = ("stdio", "http")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """Raised when an environment variable holds an unusable value."""


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _path(env: Mapping[str, str], name: str, default: str) -> Path:
    raw = env.get(name, "").strip() or default
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    credentials_path: Path
    token_path: Path
    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    token_json: str = field(default="", repr=False)
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    auth_token: str = field(default="", repr=False)
    allowed_hosts: tuple[str, ...] = ()
    max_recipients: int = 50
    max_email_body_bytes: int = 1_048_576
    max_doc_append_bytes: int = 102_400
    request_timeout_seconds: int = 30
    log_level: str = "INFO"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``env`` (defaults to ``os.environ``)."""
    env = os.environ if env is None else env

    transport = env.get("MCP_TRANSPORT", "").strip().lower() or "stdio"
    if transport not in VALID_TRANSPORTS:
        raise ConfigError(f"MCP_TRANSPORT must be one of {VALID_TRANSPORTS}, got {transport!r}")

    log_level = env.get("LOG_LEVEL", "").strip().upper() or "INFO"
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {VALID_LOG_LEVELS}, got {log_level!r}")

    allowed_hosts = tuple(
        h.strip() for h in env.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
    )

    return Settings(
        credentials_path=_path(env, "GOOGLE_CREDENTIALS_PATH", "./credentials.json"),
        token_path=_path(env, "GOOGLE_TOKEN_PATH", "./token.json"),
        google_client_id=env.get("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=env.get("GOOGLE_CLIENT_SECRET", "").strip(),
        token_json=env.get("GOOGLE_TOKEN_JSON", "").strip(),
        transport=transport,
        host=env.get("MCP_HOST", "").strip() or "127.0.0.1",
        # Platforms such as Railway inject PORT; MCP_PORT wins when both are set.
        port=_int(env, "MCP_PORT", _int(env, "PORT", 8000)),
        auth_token=env.get("MCP_AUTH_TOKEN", "").strip(),
        allowed_hosts=allowed_hosts,
        max_recipients=_int(env, "MAX_RECIPIENTS", 50),
        max_email_body_bytes=_int(env, "MAX_EMAIL_BODY_BYTES", 1_048_576),
        max_doc_append_bytes=_int(env, "MAX_DOC_APPEND_BYTES", 102_400),
        request_timeout_seconds=_int(env, "REQUEST_TIMEOUT_SECONDS", 30),
        log_level=log_level,
    )
