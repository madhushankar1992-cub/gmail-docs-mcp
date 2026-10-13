import json

import pytest

from gmail_docs_mcp.docs_service import append_to_document, build_append_request
from gmail_docs_mcp.errors import (
    INVALID_INPUT,
    NOT_FOUND,
    PERMISSION_DENIED,
    ToolFailure,
    map_exception,
)
from tests.conftest import make_http_error

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-aB"


def test_request_uses_end_of_segment_insert_text_only():
    body = build_append_request("hello", add_leading_newline=True)
    assert body == {
        "requests": [
            {"insertText": {"endOfSegmentLocation": {"segmentId": ""}, "text": "\nhello"}}
        ]
    }
    assert "delete" not in json.dumps(body).lower()
    assert "location" not in body["requests"][0]["insertText"]  # no index-based insert


def test_no_leading_newline():
    body = build_append_request("hello", add_leading_newline=False)
    assert body["requests"][0]["insertText"]["text"] == "hello"


def test_append_returns_summary_and_calls_api(settings, services):
    result = append_to_document(
        services,
        settings,
        document=f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0",
        text="note",
    )
    assert result == {
        "status": "appended",
        "document_id": DOC_ID,
        "characters_appended": 5,  # newline + "note"
        "document_url": f"https://docs.google.com/document/d/{DOC_ID}/edit",
    }
    kwargs = services.docs_batch_update.call_args.kwargs
    assert kwargs["documentId"] == DOC_ID
    assert len(kwargs["body"]["requests"]) == 1


def test_only_insert_text_is_ever_emitted(settings, services):
    append_to_document(services, settings, document=DOC_ID, text="x")
    for req in services.docs_batch_update.call_args.kwargs["body"]["requests"]:
        assert list(req) == ["insertText"]


@pytest.mark.parametrize("override", [{"document": "bad id"}, {"text": ""}, {"text": "x" * 501}])
def test_invalid_input_never_calls_docs(settings, services, override):
    args = {"document": DOC_ID, "text": "x"}
    args.update(override)
    with pytest.raises(ToolFailure) as exc:
        append_to_document(services, settings, **args)
    assert exc.value.code == INVALID_INPUT
    services.docs_batch_update.assert_not_called()


def test_transient_errors_are_retried(settings, services):
    services.docs_execute.side_effect = [make_http_error(503), make_http_error(500), {}]
    result = append_to_document(services, settings, document=DOC_ID, text="x")
    assert result["status"] == "appended"
    assert services.docs_execute.call_count == 3


@pytest.mark.parametrize(
    ("status", "code"), [(404, NOT_FOUND), (403, PERMISSION_DENIED), (400, INVALID_INPUT)]
)
def test_http_errors_map_to_codes_without_retry(settings, services, status, code):
    services.docs_execute.side_effect = make_http_error(status)
    with pytest.raises(Exception) as exc:
        append_to_document(services, settings, document=DOC_ID, text="x")
    assert map_exception(exc.value).code == code
    assert services.docs_execute.call_count == 1
