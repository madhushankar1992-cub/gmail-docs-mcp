"""Docs/Gmail tools behind the Pulse publication contract."""

from __future__ import annotations

import base64
from email import message_from_bytes, policy

import pytest

from gmail_docs_mcp import pulse_docs, pulse_drafts
from gmail_docs_mcp.errors import INVALID_INPUT, NOT_FOUND, ToolFailure, map_exception
from tests.conftest import make_http_error

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-aB"
MARKER = "groww-pulse:0123456789abcdef"

MARKDOWN = """# Title | week

Coverage line.

## Top themes
1. First theme
2. Second theme

## What users said
- “quote one”
- “quote two”

## Action ideas
1. Do a thing
"""


def _raw(msg_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(msg_bytes).decode("ascii")


# ---- markdown -> Docs requests ---------------------------------------------------------------


def test_parse_markdown_blocks():
    kinds = [b.kind for b in pulse_docs.parse_markdown(MARKDOWN)]
    assert kinds == [
        "heading1", "paragraph", "heading2", "number", "number",
        "heading2", "bullet", "bullet", "heading2", "number",
    ]  # fmt: skip


def test_write_requests_replace_body_and_style_by_exact_ranges():
    blocks = pulse_docs.parse_markdown("# Hi\nbody\n- a\n- b\n1. x")
    reqs = pulse_docs.build_write_requests(blocks, existing_end_index=10)

    assert reqs[0] == {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 9}}}
    assert reqs[1]["insertText"] == {"location": {"index": 1}, "text": "Hi\nbody\na\nb\nx\n"}

    heading = next(
        r
        for r in reqs
        if "updateParagraphStyle" in r
        and r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
    )
    assert heading["updateParagraphStyle"]["range"] == {"startIndex": 1, "endIndex": 4}

    bullets = [r["createParagraphBullets"] for r in reqs if "createParagraphBullets" in r]
    assert [(b["bulletPreset"], b["range"]) for b in bullets] == [
        ("BULLET_DISC_CIRCLE_SQUARE", {"startIndex": 9, "endIndex": 13}),
        ("NUMBERED_DECIMAL_ALPHA_ROMAN", {"startIndex": 13, "endIndex": 15}),
    ]


def test_empty_document_has_nothing_to_delete():
    reqs = pulse_docs.build_write_requests(pulse_docs.parse_markdown("hello"), existing_end_index=2)
    assert "deleteContentRange" not in reqs[0]


def test_document_text_is_plain_lines():
    doc = {
        "body": {
            "content": [
                {"sectionBreak": {}},
                {"paragraph": {"elements": [{"textRun": {"content": "Title\n"}}]}},
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "a "}},
                            {"textRun": {"content": "b\n"}},
                        ]
                    }
                },
                {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
            ]
        }
    }
    assert pulse_docs.document_text(doc) == "Title\na b"
    assert (
        pulse_docs.document_text(
            {"body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}}]}}
        )
        == ""
    )


# ---- Docs tools -----------------------------------------------------------------------------


def test_create_document_tags_marker_in_one_call(settings, services):
    services.drive_client.files.return_value.create.return_value.execute.return_value = {
        "id": DOC_ID
    }
    result = pulse_docs.create_document(services, settings, title=" Pulse ", marker=MARKER)
    assert result == {
        "document_id": DOC_ID,
        "url": f"https://docs.google.com/document/d/{DOC_ID}/edit",
    }
    body = services.drive_client.files.return_value.create.call_args.kwargs["body"]
    assert body["name"] == "Pulse"
    assert body["appProperties"] == {"pulse_marker": MARKER}
    assert body["mimeType"] == "application/vnd.google-apps.document"


@pytest.mark.parametrize("marker", ["", "bad marker", "x'y", "a" * 129])
def test_invalid_marker_never_calls_google(settings, services, marker):
    with pytest.raises(ToolFailure) as exc:
        pulse_docs.create_document(services, settings, title="T", marker=marker)
    assert exc.value.code == INVALID_INPUT
    services.drive_client.files.assert_not_called()


def test_write_content_reads_length_then_batch_updates(settings, services):
    docs = services.docs_client.documents.return_value
    docs.get.return_value.execute.return_value = {"body": {"content": [{"endIndex": 12}]}}
    docs.batchUpdate.return_value.execute.return_value = {
        "writeControl": {"requiredRevisionId": "rev9"}
    }
    result = pulse_docs.write_content(
        services, settings, document_id=DOC_ID, content_markdown="# H\ntext"
    )
    assert result == {"document_id": DOC_ID, "revision_id": "rev9"}
    sent = docs.batchUpdate.call_args.kwargs["body"]["requests"]
    assert sent[0]["deleteContentRange"]["range"] == {"startIndex": 1, "endIndex": 11}


def test_get_document_shape(settings, services):
    docs = services.docs_client.documents.return_value
    docs.get.return_value.execute.return_value = {
        "documentId": DOC_ID,
        "title": "T",
        "revisionId": "r1",
        "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "hi\n"}}]}}]},
    }
    assert pulse_docs.get_document(services, settings, document_id=DOC_ID) == {
        "document_id": DOC_ID,
        "title": "T",
        "text": "hi",
        "url": f"https://docs.google.com/document/d/{DOC_ID}/edit",
        "revision_id": "r1",
    }


def test_find_by_marker_queries_app_properties(settings, services):
    files = services.drive_client.files.return_value
    files.list.return_value.execute.return_value = {"files": [{"id": DOC_ID, "name": "T"}]}
    out = pulse_docs.find_by_marker(services, settings, marker=MARKER)
    assert out["documents"] == [
        {"document_id": DOC_ID, "title": "T",
         "url": f"https://docs.google.com/document/d/{DOC_ID}/edit"}
    ]  # fmt: skip
    q = files.list.call_args.kwargs["q"]
    assert MARKER in q and "pulse_marker" in q and "trashed=false" in q


def test_missing_document_maps_to_not_found(settings, services):
    docs = services.docs_client.documents.return_value
    docs.get.return_value.execute.side_effect = make_http_error(404)
    with pytest.raises(Exception) as exc:
        pulse_docs.get_document(services, settings, document_id=DOC_ID)
    assert map_exception(exc.value).code == NOT_FOUND


# ---- Gmail draft tools ------------------------------------------------------------------------


def _drafts(services):
    return services.gmail_client.users.return_value.drafts.return_value


def test_create_draft_builds_unsent_draft_with_marker_header(settings, services):
    _drafts(services).create.return_value.execute.return_value = {
        "id": "r-123",
        "message": {"id": "m-1"},
    }
    out = pulse_drafts.create_draft(
        services,
        settings,
        to="me@example.com",
        subject="S | 1",
        body_text="Hello\nWorld",
        marker=MARKER,
    )
    assert out == {"draft_id": "r-123", "message_id": "m-1"}

    raw = _drafts(services).create.call_args.kwargs["body"]["message"]["raw"]
    msg = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert msg["To"] == "me@example.com"
    assert msg["Subject"] == "S | 1"
    assert msg["X-Pulse-Marker"] == MARKER
    services.gmail_client.users.return_value.messages.return_value.send.assert_not_called()


def test_create_draft_rejects_bad_recipient(settings, services):
    with pytest.raises(ToolFailure) as exc:
        pulse_drafts.create_draft(
            services, settings, to="nope", subject="S", body_text="b", marker=MARKER
        )
    assert exc.value.code == INVALID_INPUT
    _drafts(services).create.assert_not_called()


def test_get_draft_round_trips_body_exactly(settings, services):
    body = "Weekly “pulse”\nDraft only.\n\n1. one\nhttps://docs.google.com/document/d/x/edit"
    created = {}

    def capture(userId, body):  # noqa: N803 - mirrors the Google client signature
        created["raw"] = body["message"]["raw"]
        m = type("R", (), {})()
        m.execute = lambda: {"id": "d1", "message": {"id": "m1"}}
        return m

    _drafts(services).create.side_effect = capture
    pulse_drafts.create_draft(
        services, settings, to="me@example.com", subject="Sub", body_text=body, marker=MARKER
    )
    _drafts(services).get.return_value.execute.return_value = {
        "id": "d1",
        "message": {"raw": created["raw"], "labelIds": ["DRAFT"]},
    }
    got = pulse_drafts.get_draft(services, settings, draft_id="d1")
    assert got == {
        "draft_id": "d1",
        "to": "me@example.com",
        "subject": "Sub",
        "body_text": body,
        "is_draft": True,
    }


def test_get_draft_reports_not_draft_when_label_missing(settings, services):
    msg = _raw(b"To: a@example.com\nSubject: s\n\nbody\n")
    _drafts(services).get.return_value.execute.return_value = {
        "id": "d1",
        "message": {"raw": msg, "labelIds": ["SENT"]},
    }
    assert pulse_drafts.get_draft(services, settings, draft_id="d1")["is_draft"] is False


def test_find_drafts_filters_on_marker_header(settings, services):
    d = _drafts(services)
    d.list.return_value.execute.return_value = {"drafts": [{"id": "a"}, {"id": "b"}]}

    def get(userId, id, format):  # noqa: A002, N803
        marker = MARKER if id == "b" else "other"
        m = type("R", (), {})()
        m.execute = lambda: {"message": {"payload": {"headers": [
            {"name": "Subject", "value": f"S-{id}"},
            {"name": "X-Pulse-Marker", "value": marker},
        ]}}}  # fmt: skip
        return m

    d.get.side_effect = get
    assert pulse_drafts.find_drafts(services, settings, marker=MARKER) == {
        "drafts": [{"draft_id": "b", "subject": "S-b"}]
    }


def test_invalid_draft_id_rejected(settings, services):
    with pytest.raises(ToolFailure):
        pulse_drafts.get_draft(services, settings, draft_id="bad id!")


# ---- page count -------------------------------------------------------------------------------


def _pdf(pages: int, declared: int | None = None) -> bytes:
    leaves = b"".join(b"<< /Type /Page /Parent 1 0 R >>\n" for _ in range(pages))
    count = pages if declared is None else declared
    header = b"%PDF-1.4\n<< /Type /Pages /Kids [] /Count " + str(count).encode() + b" >>\n"
    return header + leaves


def test_count_pdf_pages_reads_one_and_many():
    assert pulse_docs.count_pdf_pages(_pdf(1)) == 1
    assert pulse_docs.count_pdf_pages(_pdf(3)) == 3


def test_count_pdf_pages_fails_closed_on_disagreement_or_garbage():
    with pytest.raises(ToolFailure):
        pulse_docs.count_pdf_pages(_pdf(2, declared=5))
    with pytest.raises(ToolFailure):
        pulse_docs.count_pdf_pages(b"<html>nope</html>")


def test_get_page_count_exports_pdf(settings, services):
    files = services.drive_client.files.return_value
    files.export.return_value.execute.return_value = _pdf(1)
    out = pulse_docs.get_page_count(services, settings, document_id=DOC_ID)
    assert out == {"document_id": DOC_ID, "page_count": 1}
    assert files.export.call_args.kwargs["mimeType"] == "application/pdf"
