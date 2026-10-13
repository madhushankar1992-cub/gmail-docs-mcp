"""Stable error codes and mapping from Google/library exceptions to tool failures."""

from __future__ import annotations

import json
import socket

from googleapiclient.errors import HttpError

AUTH_REQUIRED = "AUTH_REQUIRED"
INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
PERMISSION_DENIED = "PERMISSION_DENIED"
RATE_LIMITED = "RATE_LIMITED"
UPSTREAM_ERROR = "UPSTREAM_ERROR"

ERROR_CODES = frozenset(
    {AUTH_REQUIRED, INVALID_INPUT, NOT_FOUND, PERMISSION_DENIED, RATE_LIMITED, UPSTREAM_ERROR}
)

_RATE_LIMIT_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "RATE_LIMIT_EXCEEDED"}
)


class ToolFailure(Exception):
    """An expected, user-reportable failure carrying a stable ``error_code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        assert code in ERROR_CODES, f"unknown error code {code!r}"
        self.code = code
        self.message = message


def http_status(exc: HttpError) -> int:
    try:
        return int(exc.resp.status)
    except (AttributeError, TypeError, ValueError):
        return 0


def _error_details(exc: HttpError) -> tuple[str, str]:
    """Return ``(reason, message)`` parsed from a Google API error body, best effort."""
    try:
        payload = json.loads(exc.content.decode("utf-8"))
        error = payload.get("error", {})
        message = str(error.get("message", "")) if isinstance(error, dict) else ""
        reason = ""
        if isinstance(error, dict):
            errors = error.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason", ""))
            if not reason:
                reason = str(error.get("status", ""))
        return reason, message
    except (ValueError, AttributeError, UnicodeDecodeError):
        return "", ""


def is_rate_limited(exc: HttpError) -> bool:
    if http_status(exc) == 429:
        return True
    if http_status(exc) == 403:
        reason, _ = _error_details(exc)
        return reason in _RATE_LIMIT_REASONS
    return False


def map_exception(exc: BaseException) -> ToolFailure:
    """Translate any exception raised while talking to Google into a :class:`ToolFailure`."""
    if isinstance(exc, ToolFailure):
        return exc

    # Imported lazily so this module stays importable without google-auth quirks in tests.
    from google.auth.exceptions import RefreshError, TransportError

    if isinstance(exc, RefreshError):
        return ToolFailure(
            AUTH_REQUIRED,
            "Google authorization expired or was revoked. "
            "Re-run `gmail-docs-mcp auth` to sign in again.",
        )

    if isinstance(exc, HttpError):
        status = http_status(exc)
        _, detail = _error_details(exc)
        suffix = f" ({detail})" if detail else ""
        if is_rate_limited(exc):
            return ToolFailure(RATE_LIMITED, f"Google API quota or rate limit hit{suffix}.")
        if status == 401:
            return ToolFailure(
                AUTH_REQUIRED,
                "Google rejected the stored credentials. Re-run `gmail-docs-mcp auth`.",
            )
        if status == 403:
            return ToolFailure(
                PERMISSION_DENIED,
                "The authorized Google account lacks permission for this action "
                f"(missing edit access or OAuth scope){suffix}.",
            )
        if status == 404:
            return ToolFailure(
                NOT_FOUND,
                "The requested resource was not found or is not visible to the authorized "
                f"Google account{suffix}.",
            )
        if status == 400:
            return ToolFailure(INVALID_INPUT, f"Google rejected the request{suffix}.")
        return ToolFailure(UPSTREAM_ERROR, f"Google API error (HTTP {status}){suffix}.")

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ToolFailure(UPSTREAM_ERROR, "The request to Google timed out.")
    if isinstance(exc, (TransportError, OSError)):
        return ToolFailure(UPSTREAM_ERROR, "Could not reach Google (network error).")

    return ToolFailure(UPSTREAM_ERROR, f"Unexpected error: {type(exc).__name__}.")
