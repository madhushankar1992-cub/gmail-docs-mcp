import datetime
import json

import pytest

from gmail_docs_mcp import auth
from gmail_docs_mcp.errors import AUTH_REQUIRED, ToolFailure


def _write_token(path, **overrides):
    data = {
        "token": "ya29.access",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "secret",
        "scopes": auth.SCOPES,
        "expiry": "2999-01-01T00:00:00Z",
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_scopes_are_least_privilege():
    assert auth.SCOPES == [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/documents",
    ]


def test_missing_token_requires_auth_without_prompting(settings):
    with pytest.raises(ToolFailure) as exc:
        auth.load_credentials(settings)
    assert exc.value.code == AUTH_REQUIRED
    assert str(settings.token_path) in exc.value.message


def test_corrupt_token_requires_auth(settings):
    settings.token_path.write_text("not json", encoding="utf-8")
    with pytest.raises(ToolFailure) as exc:
        auth.load_credentials(settings)
    assert exc.value.code == AUTH_REQUIRED


def test_valid_token_loads(settings):
    _write_token(settings.token_path)
    assert auth.load_credentials(settings).valid


def test_expired_without_refresh_token_requires_auth(settings):
    _write_token(settings.token_path, refresh_token=None, expiry="2000-01-01T00:00:00Z")
    with pytest.raises(ToolFailure) as exc:
        auth.load_credentials(settings)
    assert exc.value.code == AUTH_REQUIRED


def test_revoked_refresh_token_requires_auth(settings, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    _write_token(settings.token_path, expiry="2000-01-01T00:00:00Z")

    def boom(self, request):
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", boom)
    with pytest.raises(ToolFailure) as exc:
        auth.load_credentials(settings)
    assert exc.value.code == AUTH_REQUIRED


def test_expired_token_is_refreshed_and_saved(settings, monkeypatch):
    from google.oauth2.credentials import Credentials

    _write_token(settings.token_path, expiry="2000-01-01T00:00:00Z")

    def fake_refresh(self, request):
        self.token = "ya29.new"
        self.expiry = datetime.datetime(2999, 1, 1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    creds = auth.load_credentials(settings)
    assert creds.token == "ya29.new"
    assert json.loads(settings.token_path.read_text())["token"] == "ya29.new"


def test_refresh_survives_read_only_filesystem(settings, monkeypatch):
    from google.oauth2.credentials import Credentials

    _write_token(settings.token_path, expiry="2000-01-01T00:00:00Z")

    def fake_refresh(self, request):
        self.token = "ya29.new"
        self.expiry = datetime.datetime(2999, 1, 1)

    def read_only(path, creds):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    monkeypatch.setattr(auth, "_save_token", read_only)
    assert auth.load_credentials(settings).token == "ya29.new"


def test_token_from_env_json_when_no_file(settings):
    import dataclasses

    data = {
        "token": "ya29.env",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "secret",
        "scopes": auth.SCOPES,
        "expiry": "2999-01-01T00:00:00Z",
    }
    hosted = dataclasses.replace(settings, token_json=json.dumps(data))
    creds = auth.load_credentials(hosted)
    assert creds.token == "ya29.env"
    assert not settings.token_path.exists()  # hosted mode never writes the token to disk


def test_bad_env_token_json_requires_auth(settings):
    import dataclasses

    bad = dataclasses.replace(settings, token_json="{not json")
    with pytest.raises(ToolFailure) as exc:
        auth.load_credentials(bad)
    assert exc.value.code == AUTH_REQUIRED
