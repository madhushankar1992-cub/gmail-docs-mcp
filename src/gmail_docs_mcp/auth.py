"""Google OAuth (installed-app flow), token persistence, and API client construction."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import AUTH_REQUIRED, ToolFailure

# Least privilege: send-only Gmail access plus Docs editing. Nothing broader.
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
SCOPES = [GMAIL_SEND_SCOPE, DOCS_SCOPE]


def _reauth_hint(settings: Settings) -> str:
    return (
        "Run `gmail-docs-mcp auth` (or `python -m gmail_docs_mcp auth`) once to sign in. "
        f"Expected credentials file: {settings.credentials_path}; "
        f"token file: {settings.token_path}."
    )


def _save_token(path: Path, creds: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # best effort; limited effect on Windows
    except OSError:
        pass


def load_credentials(settings: Settings) -> Any:
    """Load stored credentials, refreshing the access token when needed.

    Never prompts. Raises ``ToolFailure(AUTH_REQUIRED)`` when the user must re-authenticate.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = settings.token_path
    from_file = token_path.is_file()
    if not from_file and not settings.token_json:
        raise ToolFailure(AUTH_REQUIRED, f"No Google token found. {_reauth_hint(settings)}")

    try:
        if from_file:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        else:
            # Hosted deployments: the token JSON arrives via GOOGLE_TOKEN_JSON, not a file.
            creds = Credentials.from_authorized_user_info(json.loads(settings.token_json), SCOPES)
    except (ValueError, KeyError, OSError) as exc:
        raise ToolFailure(
            AUTH_REQUIRED, f"The stored Google token is unreadable. {_reauth_hint(settings)}"
        ) from exc

    if creds.valid:
        return creds

    if creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise ToolFailure(
                AUTH_REQUIRED,
                f"Google authorization expired or was revoked. {_reauth_hint(settings)}",
            ) from exc
        if from_file:  # env-sourced tokens stay in memory; the refresh token itself is unchanged
            try:
                _save_token(token_path, creds)
            except OSError:
                # Read-only filesystem (serverless): the refreshed token still works in memory.
                pass
        return creds

    raise ToolFailure(
        AUTH_REQUIRED,
        f"Stored Google token is invalid and cannot refresh. {_reauth_hint(settings)}",
    )


def run_auth_flow(settings: Settings) -> Path:
    """Interactive one-time browser consent flow. Writes the token file and returns its path."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if settings.credentials_path.is_file():
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_path), SCOPES)
    elif settings.google_client_id and settings.google_client_secret:
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            },
            SCOPES,
        )
    else:
        raise FileNotFoundError(
            "No OAuth client configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env, "
            f"or place a 'Desktop app' client JSON at {settings.credentials_path} "
            "(GOOGLE_CREDENTIALS_PATH)."
        )
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message=(
            "Opening your browser to authorize Gmail send + Docs. "
            "If nothing opens, visit this URL:\n{url}\n"
        ),
        success_message="Authorization complete. You can close this tab.",
    )
    _save_token(settings.token_path, creds)
    return settings.token_path


class GoogleServices:
    """Lazily builds Gmail/Docs clients and serializes access (httplib2 is not thread-safe)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._creds: Any = None
        self._gmail: Any = None
        self._docs: Any = None

    def _authorized_http(self) -> Any:
        import google_auth_httplib2
        import httplib2

        creds = self._current_credentials()
        http = httplib2.Http(timeout=self._settings.request_timeout_seconds)
        return google_auth_httplib2.AuthorizedHttp(creds, http=http)

    def _current_credentials(self) -> Any:
        if self._creds is None or not self._creds.valid:
            self._creds = load_credentials(self._settings)
        return self._creds

    def _build(self, api: str, version: str) -> Any:
        from googleapiclient.discovery import build

        return build(api, version, http=self._authorized_http(), cache_discovery=False)

    @contextmanager
    def gmail(self) -> Iterator[Any]:
        with self._lock:
            self._current_credentials()
            if self._gmail is None:
                self._gmail = self._build("gmail", "v1")
            yield self._gmail

    @contextmanager
    def docs(self) -> Iterator[Any]:
        with self._lock:
            self._current_credentials()
            if self._docs is None:
                self._docs = self._build("docs", "v1")
            yield self._docs
