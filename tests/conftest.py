from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from gmail_docs_mcp.config import Settings


def make_http_error(status: int, reason: str = "", message: str = "boom") -> HttpError:
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    return HttpError(httplib2.Response({"status": str(status)}), json.dumps(body).encode())


class FakeServices:
    """Stand-in for GoogleServices exposing mock Gmail/Docs clients."""

    def __init__(self) -> None:
        self.gmail_client = MagicMock(name="gmail")
        self.docs_client = MagicMock(name="docs")

    @property
    def gmail_send(self) -> MagicMock:
        return self.gmail_client.users.return_value.messages.return_value.send

    @property
    def gmail_execute(self) -> MagicMock:
        return self.gmail_send.return_value.execute

    @property
    def docs_batch_update(self) -> MagicMock:
        return self.docs_client.documents.return_value.batchUpdate

    @property
    def docs_execute(self) -> MagicMock:
        return self.docs_batch_update.return_value.execute

    @contextmanager
    def gmail(self):
        yield self.gmail_client

    @contextmanager
    def docs(self):
        yield self.docs_client


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        max_recipients=5,
        max_email_body_bytes=1000,
        max_doc_append_bytes=500,
    )


@pytest.fixture
def services() -> FakeServices:
    fake = FakeServices()
    fake.gmail_execute.return_value = {"id": "msg123", "threadId": "thr456"}
    fake.docs_execute.return_value = {"documentId": "ignored"}
    return fake


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gmail_docs_mcp.retry.time.sleep", lambda _s: None)
