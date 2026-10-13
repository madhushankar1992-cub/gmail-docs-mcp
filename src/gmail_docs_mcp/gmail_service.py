"""Build MIME messages and send them through the Gmail API."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from email.message import EmailMessage
from typing import Any

from .config import Settings
from .retry import RATE_LIMIT_ONLY, call_with_retry
from .validation import (
    validate_recipients,
    validate_subject,
    validate_text,
)


def build_message(
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    is_html: bool = False,
) -> EmailMessage:
    """Create the MIME message. Inputs must already be validated."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # Gmail delivers to Bcc addresses and strips this header from delivered copies.
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if is_html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg


def encode_message(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def send_email(
    services: Any,
    settings: Settings,
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    is_html: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate, build and (unless ``dry_run``) send an email. Returns the tool result dict."""
    clean_to, clean_cc, clean_bcc = validate_recipients(
        to, cc, bcc, max_recipients=settings.max_recipients
    )
    clean_subject = validate_subject(subject)
    clean_body = validate_text(body, "body", settings.max_email_body_bytes)

    msg = build_message(
        to=clean_to,
        subject=clean_subject,
        body=clean_body,
        cc=clean_cc,
        bcc=clean_bcc,
        is_html=is_html,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "to": clean_to,
            "recipient_count": len(clean_to) + len(clean_cc) + len(clean_bcc),
            "note": "Message validated and built; nothing was sent.",
        }

    raw = encode_message(msg)
    with services.gmail() as gmail:
        # Only rate-limit responses are retried: a 5xx or timeout may already have sent the
        # message, and re-sending would duplicate it.
        response = call_with_retry(
            lambda: gmail.users().messages().send(userId="me", body={"raw": raw}).execute(),
            retry_statuses=RATE_LIMIT_ONLY,
        )

    return {
        "status": "sent",
        "message_id": response.get("id"),
        "thread_id": response.get("threadId"),
        "to": clean_to,
    }
