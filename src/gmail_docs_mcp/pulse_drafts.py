"""Create, read back and find unsent Gmail drafts for the Pulse publication contract.

Nothing here can send mail: only ``drafts.create``, ``drafts.get`` and ``drafts.list`` are used.
The idempotency marker travels in an ``X-Pulse-Marker`` header so the visible subject and body
stay exactly as supplied.
"""

from __future__ import annotations

import base64
import re
from email import message_from_bytes, policy
from typing import Any

from .config import Settings
from .errors import INVALID_INPUT, ToolFailure
from .gmail_service import build_message, encode_message
from .pulse_docs import validate_marker
from .retry import RATE_LIMIT_ONLY, TRANSIENT, call_with_retry
from .validation import validate_email, validate_subject, validate_text

MARKER_HEADER = "X-Pulse-Marker"
MAX_DRAFTS_SCANNED = 50

_DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def validate_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or not _DRAFT_ID_RE.match(draft_id):
        raise ToolFailure(INVALID_INPUT, "draft_id: not a valid Gmail draft ID.")
    return draft_id


def create_draft(
    services: Any, settings: Settings, *, to: str, subject: str, body_text: str, marker: str
) -> dict[str, Any]:
    recipient = validate_email(to, "to")
    clean_subject = validate_subject(subject)
    body = validate_text(body_text, "body_text", settings.max_email_body_bytes)
    clean_marker = validate_marker(marker)

    msg = build_message(to=[recipient], subject=clean_subject, body=body)
    msg[MARKER_HEADER] = clean_marker
    raw = encode_message(msg)

    with services.gmail() as gmail:
        # Creating a draft is not idempotent: only explicit rate-limit rejections are retried.
        created = call_with_retry(
            lambda: gmail.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute(),
            retry_statuses=RATE_LIMIT_ONLY,
        )
    return {"draft_id": created["id"], "message_id": (created.get("message") or {}).get("id", "")}


def _parse_raw(raw: str) -> Any:
    padded = raw + "=" * (-len(raw) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(padded), policy=policy.default)


def get_draft(services: Any, settings: Settings, *, draft_id: str) -> dict[str, Any]:
    clean_id = validate_draft_id(draft_id)
    with services.gmail() as gmail:
        draft = call_with_retry(
            lambda: gmail.users().drafts().get(userId="me", id=clean_id, format="raw").execute(),
            retry_statuses=TRANSIENT,
        )
    message = draft.get("message") or {}
    parsed = _parse_raw(message.get("raw", ""))
    part = parsed.get_body(preferencelist=("plain",))
    text = part.get_content() if part is not None else ""
    text = text.replace("\r\n", "\n")
    if text.endswith("\n"):  # EmailMessage.set_content appends exactly one trailing newline
        text = text[:-1]
    return {
        "draft_id": draft.get("id", clean_id),
        "to": str(parsed.get("To", "")),
        "subject": str(parsed.get("Subject", "")),
        "body_text": text,
        "is_draft": "DRAFT" in (message.get("labelIds") or []),
    }


def find_drafts(services: Any, settings: Settings, *, marker: str) -> dict[str, Any]:
    clean_marker = validate_marker(marker)
    matches: list[dict[str, str]] = []
    with services.gmail() as gmail:
        listing = call_with_retry(
            lambda: gmail.users()
            .drafts()
            .list(userId="me", maxResults=MAX_DRAFTS_SCANNED)
            .execute(),
            retry_statuses=TRANSIENT,
        )
        for item in listing.get("drafts", []):
            draft = call_with_retry(
                lambda i=item["id"]: gmail.users()
                .drafts()
                .get(userId="me", id=i, format="metadata")
                .execute(),
                retry_statuses=TRANSIENT,
            )
            headers = {
                h.get("name", "").lower(): h.get("value", "")
                for h in (draft.get("message", {}).get("payload", {}) or {}).get("headers", [])
            }
            if headers.get(MARKER_HEADER.lower()) == clean_marker:
                matches.append({"draft_id": item["id"], "subject": headers.get("subject", "")})
    return {"drafts": matches}
