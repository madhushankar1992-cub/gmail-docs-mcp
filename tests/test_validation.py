import pytest

from gmail_docs_mcp.errors import INVALID_INPUT, ToolFailure
from gmail_docs_mcp.validation import (
    extract_document_id,
    validate_email,
    validate_recipients,
    validate_subject,
    validate_text,
)

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-aB"


@pytest.mark.parametrize(
    "addr", ["a@example.com", "first.last+tag@sub.example.co.uk", "x_y@a-b.io"]
)
def test_valid_emails(addr):
    assert validate_email(addr) == addr


@pytest.mark.parametrize(
    "addr",
    [
        "",
        "plainaddress",
        "@example.com",
        "a@",
        "a@b",
        "a b@example.com",
        "a..b@example.com",
        ".a@example.com",
        "a@example.com\nBcc: evil@example.com",
        "a@example.com\r\n",
        "Alice <a@example.com>",
        "a@-example.com",
    ],
)
def test_invalid_emails(addr):
    with pytest.raises(ToolFailure) as exc:
        validate_email(addr)
    assert exc.value.code == INVALID_INPUT


def test_email_is_stripped():
    assert validate_email("  a@example.com ") == "a@example.com"


def test_recipients_require_to():
    with pytest.raises(ToolFailure):
        validate_recipients([])


def test_recipients_total_cap():
    with pytest.raises(ToolFailure, match="Too many"):
        validate_recipients(["a@example.com"] * 3, ["b@example.com"] * 2, ["c@example.com"], 5)


def test_one_bad_address_rejects_everything():
    with pytest.raises(ToolFailure):
        validate_recipients(["ok@example.com"], ["nope"])


@pytest.mark.parametrize("subject", ["", "   ", "Hi\nBcc: x@y.com", "a\rb", "x" * 999])
def test_bad_subjects(subject):
    with pytest.raises(ToolFailure):
        validate_subject(subject)


def test_subject_boundary():
    assert validate_subject("x" * 998) == "x" * 998


def test_text_limits():
    assert validate_text("hello", "body", 5) == "hello"
    with pytest.raises(ToolFailure, match="too large"):
        validate_text("hello!", "body", 5)
    with pytest.raises(ToolFailure):
        validate_text("   ", "body", 5)


def test_text_limit_counts_bytes_not_chars():
    with pytest.raises(ToolFailure):
        validate_text("é" * 3, "body", 5)  # 6 bytes


@pytest.mark.parametrize(
    "value",
    [
        DOC_ID,
        f"https://docs.google.com/document/d/{DOC_ID}/edit",
        f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0",
        f"https://docs.google.com/document/d/{DOC_ID}/view",
        f"https://docs.google.com/document/u/0/d/{DOC_ID}/edit#heading=h.abc",
        f"  {DOC_ID}  ",
    ],
)
def test_extract_document_id(value):
    assert extract_document_id(value) == DOC_ID


@pytest.mark.parametrize(
    "value",
    ["", "short", "https://example.com/document/d/" + DOC_ID, "has spaces in the id ok"],
)
def test_extract_document_id_rejects(value):
    with pytest.raises(ToolFailure) as exc:
        extract_document_id(value)
    assert exc.value.code == INVALID_INPUT
