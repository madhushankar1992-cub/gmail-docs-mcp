import base64
import email
from email import policy

import pytest

from gmail_docs_mcp.errors import INVALID_INPUT, RATE_LIMITED, ToolFailure, map_exception
from gmail_docs_mcp.gmail_service import build_message, send_email
from tests.conftest import make_http_error


def _decode(raw: str):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


def test_send_builds_plain_message(settings, services):
    result = send_email(
        services,
        settings,
        to=["a@example.com", "b@example.com"],
        subject="Héllo",
        body="Body text",
        cc=["c@example.com"],
        bcc=["d@example.com"],
    )
    assert result == {
        "status": "sent",
        "message_id": "msg123",
        "thread_id": "thr456",
        "to": ["a@example.com", "b@example.com"],
    }
    services.gmail_send.assert_called_once()
    kwargs = services.gmail_send.call_args.kwargs
    assert kwargs["userId"] == "me"
    msg = _decode(kwargs["body"]["raw"])
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Cc"] == "c@example.com"
    assert msg["Bcc"] == "d@example.com"
    assert msg["Subject"] == "Héllo"
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content().strip() == "Body text"


def test_raw_is_urlsafe_base64(settings, services):
    send_email(services, settings, to=["a@example.com"], subject="s", body="?>>>???" * 20)
    raw = services.gmail_send.call_args.kwargs["body"]["raw"]
    assert "+" not in raw and "/" not in raw


def test_html_message(settings, services):
    send_email(
        services, settings, to=["a@example.com"], subject="s", body="<b>hi</b>", is_html=True
    )
    msg = _decode(services.gmail_send.call_args.kwargs["body"]["raw"])
    assert msg.get_content_type() == "text/html"


def test_no_cc_bcc_headers_when_absent():
    msg = build_message(to=["a@example.com"], subject="s", body="b")
    assert msg["Cc"] is None and msg["Bcc"] is None


def test_dry_run_makes_no_api_call(settings, services):
    result = send_email(
        services, settings, to=["a@example.com"], subject="s", body="b", dry_run=True
    )
    assert result["status"] == "dry_run"
    assert "message_id" not in result and "thread_id" not in result
    services.gmail_send.assert_not_called()


def test_dry_run_still_validates(settings, services):
    with pytest.raises(ToolFailure) as exc:
        send_email(services, settings, to=["nope"], subject="s", body="b", dry_run=True)
    assert exc.value.code == INVALID_INPUT


@pytest.mark.parametrize(
    "override",
    [
        {"to": []},
        {"subject": "x\nBcc: evil@example.com"},
        {"body": "x" * 1001},
        {"to": ["a@example.com"] * 6},
    ],
)
def test_invalid_input_never_calls_gmail(settings, services, override):
    args = {"to": ["a@example.com"], "subject": "s", "body": "b"}
    args.update(override)
    with pytest.raises(ToolFailure) as exc:
        send_email(services, settings, **args)
    assert exc.value.code == INVALID_INPUT
    services.gmail_send.assert_not_called()


def test_rate_limit_is_retried_then_succeeds(settings, services):
    services.gmail_execute.side_effect = [make_http_error(429), {"id": "m2", "threadId": "t2"}]
    result = send_email(services, settings, to=["a@example.com"], subject="s", body="b")
    assert result["message_id"] == "m2"
    assert services.gmail_execute.call_count == 2


def test_server_error_is_not_retried_to_avoid_duplicate_send(settings, services):
    services.gmail_execute.side_effect = [make_http_error(503), {"id": "m"}]
    with pytest.raises(Exception) as exc:
        send_email(services, settings, to=["a@example.com"], subject="s", body="b")
    assert services.gmail_execute.call_count == 1
    assert map_exception(exc.value).code == "UPSTREAM_ERROR"


def test_rate_limit_gives_up_after_three_attempts(settings, services):
    services.gmail_execute.side_effect = make_http_error(429)
    with pytest.raises(Exception) as exc:
        send_email(services, settings, to=["a@example.com"], subject="s", body="b")
    assert services.gmail_execute.call_count == 3
    assert map_exception(exc.value).code == RATE_LIMITED
