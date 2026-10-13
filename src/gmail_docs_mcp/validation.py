"""Pure input-validation helpers. Every failure raises ``ToolFailure(INVALID_INPUT, ...)``."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .errors import INVALID_INPUT, ToolFailure

MAX_SUBJECT_CHARS = 998
MAX_ADDRESS_CHARS = 254

# Pragmatic RFC 5322 subset: dot-atom local part, DNS-style domain with at least one dot.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)

_DOC_URL_RE = re.compile(r"^https://docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def _fail(message: str) -> ToolFailure:
    return ToolFailure(INVALID_INPUT, message)


def has_line_break(value: str) -> bool:
    return "\r" in value or "\n" in value or "\x00" in value


def validate_email(address: str, field: str = "address") -> str:
    if not isinstance(address, str):
        raise _fail(f"{field}: expected a string email address.")
    if has_line_break(address):  # checked before strip so trailing CRLF is rejected, not trimmed
        raise _fail(f"{field}: line breaks are not allowed in an email address.")
    candidate = address.strip()
    if len(candidate) > MAX_ADDRESS_CHARS or not _EMAIL_RE.match(candidate):
        raise _fail(f"{field}: {address!r} is not a valid email address.")
    local = candidate.rsplit("@", 1)[0]
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise _fail(f"{field}: {address!r} is not a valid email address.")
    return candidate


def validate_recipients(
    to: Sequence[str],
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    max_recipients: int = 50,
) -> tuple[list[str], list[str], list[str]]:
    if not to:
        raise _fail("to: at least one recipient is required.")
    clean_to = [validate_email(a, "to") for a in to]
    clean_cc = [validate_email(a, "cc") for a in cc]
    clean_bcc = [validate_email(a, "bcc") for a in bcc]
    total = len(clean_to) + len(clean_cc) + len(clean_bcc)
    if total > max_recipients:
        raise _fail(f"Too many recipients: {total} (maximum {max_recipients}).")
    return clean_to, clean_cc, clean_bcc


def validate_subject(subject: str) -> str:
    if not isinstance(subject, str) or not subject.strip():
        raise _fail("subject: must be a non-empty string.")
    if has_line_break(subject):
        raise _fail("subject: line breaks are not allowed.")
    if len(subject) > MAX_SUBJECT_CHARS:
        raise _fail(f"subject: too long ({len(subject)} chars, maximum {MAX_SUBJECT_CHARS}).")
    return subject


def validate_text(value: str, field: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{field}: must be a non-empty string.")
    size = len(value.encode("utf-8"))
    if size > max_bytes:
        raise _fail(f"{field}: too large ({size} bytes, maximum {max_bytes}).")
    return value


def extract_document_id(document: str) -> str:
    """Accept a bare Google Doc ID or a docs.google.com URL and return the ID."""
    if not isinstance(document, str) or not document.strip():
        raise _fail("document: a Google Doc ID or URL is required.")
    candidate = document.strip()
    match = _DOC_URL_RE.match(candidate)
    if match:
        return match.group(1)
    if _DOC_ID_RE.match(candidate):
        return candidate
    raise _fail(
        "document: not a valid Google Doc ID or URL. Expected an ID or "
        "https://docs.google.com/document/d/<ID>/edit."
    )
