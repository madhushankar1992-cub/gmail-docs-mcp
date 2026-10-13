"""Append text to a Google Doc through the Docs API (append-only)."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .retry import TRANSIENT, call_with_retry
from .validation import extract_document_id, validate_text


def build_append_request(text: str, add_leading_newline: bool = True) -> dict[str, Any]:
    """Return the ``documents.batchUpdate`` body. It only ever contains one ``insertText``."""
    payload = ("\n" if add_leading_newline else "") + text
    return {
        "requests": [
            {
                "insertText": {
                    # Empty segmentId targets the document body. Text lands just before the
                    # body's final newline, i.e. at the end, without fetching the document.
                    "endOfSegmentLocation": {"segmentId": ""},
                    "text": payload,
                }
            }
        ]
    }


def document_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def append_to_document(
    services: Any,
    settings: Settings,
    *,
    document: str,
    text: str,
    add_leading_newline: bool = True,
) -> dict[str, Any]:
    document_id = extract_document_id(document)
    clean_text = validate_text(text, "text", settings.max_doc_append_bytes)
    body = build_append_request(clean_text, add_leading_newline)
    inserted = body["requests"][0]["insertText"]["text"]

    with services.docs() as docs:
        call_with_retry(
            lambda: docs.documents().batchUpdate(documentId=document_id, body=body).execute(),
            retry_statuses=TRANSIENT,
        )

    return {
        "status": "appended",
        "document_id": document_id,
        "characters_appended": len(inserted),
        "document_url": document_url(document_id),
    }
