"""Create, fill, read back and find Google Docs for the Pulse publication contract.

Documents are created through Drive (``drive.file`` scope) so an idempotency marker can be stored
in ``appProperties`` in the same call that creates the file. Content is written by replacing the
document body with a small markdown subset (``#``/``##``/``###`` headings, ``-`` bullets,
``1.`` numbered items, plain paragraphs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .docs_service import document_url
from .errors import INVALID_INPUT, UPSTREAM_ERROR, ToolFailure
from .retry import RATE_LIMIT_ONLY, TRANSIENT, call_with_retry
from .validation import extract_document_id, has_line_break, validate_text

MARKER_PROPERTY = "pulse_marker"
DOC_MIME = "application/vnd.google-apps.document"
MAX_TITLE_CHARS = 255
MAX_FIND_RESULTS = 100

_MARKER_RE = re.compile(r"^[A-Za-z0-9:_.-]{1,128}$")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^[-*•]\s+(.*\S)\s*$")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+(.*\S)\s*$")

_PDF_LEAF_PAGE_RE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_PDF_PAGES_COUNT_RE = re.compile(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", re.S)

_BULLET_PRESET = "BULLET_DISC_CIRCLE_SQUARE"
_NUMBER_PRESET = "NUMBERED_DECIMAL_ALPHA_ROMAN"


def validate_marker(marker: str) -> str:
    if not isinstance(marker, str) or not _MARKER_RE.match(marker):
        raise ToolFailure(
            INVALID_INPUT,
            "marker: 1-128 characters from letters, digits, ':', '_', '.' and '-' only.",
        )
    return marker


def validate_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ToolFailure(INVALID_INPUT, "title: must be a non-empty string.")
    if has_line_break(title) or len(title) > MAX_TITLE_CHARS:
        raise ToolFailure(
            INVALID_INPUT, f"title: single line of at most {MAX_TITLE_CHARS} characters."
        )
    return title.strip()


@dataclass(frozen=True)
class Block:
    kind: str  # heading1..heading3 | bullet | number | paragraph
    text: str


def parse_markdown(markdown: str) -> list[Block]:
    """Parse the supported markdown subset into blocks. Blank lines are separators only."""
    blocks: list[Block] = []
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if m := _HEADING_RE.match(line):
            blocks.append(Block(f"heading{len(m.group(1))}", m.group(2)))
        elif m := _BULLET_RE.match(line):
            blocks.append(Block("bullet", m.group(1)))
        elif m := _NUMBER_RE.match(line):
            blocks.append(Block("number", m.group(1)))
        else:
            blocks.append(Block("paragraph", line))
    return blocks


def build_write_requests(blocks: list[Block], existing_end_index: int) -> list[dict[str, Any]]:
    """Return ``batchUpdate`` requests replacing the body (starting at index 1) with ``blocks``."""
    requests: list[dict[str, Any]] = []
    if existing_end_index > 2:  # body always ends with a final newline that cannot be deleted
        requests.append(
            {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": existing_end_index - 1}}}
        )

    text = "".join(b.text + "\n" for b in blocks)
    requests.append({"insertText": {"location": {"index": 1}, "text": text}})
    requests.append(
        {
            "updateParagraphStyle": {
                "range": {"startIndex": 1, "endIndex": 1 + len(text)},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        }
    )

    cursor = 1
    spans: list[tuple[Block, int, int]] = []
    for block in blocks:
        end = cursor + len(block.text) + 1
        spans.append((block, cursor, end))
        cursor = end

    for block, start, end in spans:
        if block.kind.startswith("heading"):
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "paragraphStyle": {
                            "namedStyleType": f"HEADING_{block.kind.removeprefix('heading')}"
                        },
                        "fields": "namedStyleType",
                    }
                }
            )

    # One createParagraphBullets per run of consecutive list items of the same kind, so numbered
    # lists restart at 1 after a heading or paragraph.
    run: tuple[str, int, int] | None = None
    for block, start, end in [*spans, (Block("paragraph", ""), cursor, cursor)]:
        if run and block.kind == run[0]:
            run = (run[0], run[1], end)
            continue
        if run:
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": {"startIndex": run[1], "endIndex": run[2]},
                        "bulletPreset": _BULLET_PRESET if run[0] == "bullet" else _NUMBER_PRESET,
                    }
                }
            )
        run = (block.kind, start, end) if block.kind in ("bullet", "number") else None
    return requests


def _body_end_index(document: dict[str, Any]) -> int:
    content = document.get("body", {}).get("content", [])
    return int(content[-1]["endIndex"]) if content else 1


def document_text(document: dict[str, Any]) -> str:
    """Flatten a Docs API document into plain text: one line per paragraph, no markup."""
    lines: list[str] = []
    for element in document.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        text = "".join(
            part.get("textRun", {}).get("content", "") for part in paragraph.get("elements", [])
        )
        lines.append(text.rstrip("\n"))
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def create_document(
    services: Any, settings: Settings, *, title: str, marker: str
) -> dict[str, Any]:
    clean_title = validate_title(title)
    clean_marker = validate_marker(marker)
    body = {
        "name": clean_title,
        "mimeType": DOC_MIME,
        "appProperties": {MARKER_PROPERTY: clean_marker},
    }
    with services.drive() as drive:
        # Creation is not idempotent: only explicit rate-limit rejections are retried.
        created = call_with_retry(
            lambda: drive.files().create(body=body, fields="id").execute(),
            retry_statuses=RATE_LIMIT_ONLY,
        )
    document_id = created["id"]
    return {"document_id": document_id, "url": document_url(document_id)}


def write_content(
    services: Any, settings: Settings, *, document_id: str, content_markdown: str
) -> dict[str, Any]:
    doc_id = extract_document_id(document_id)
    markdown = validate_text(content_markdown, "content_markdown", settings.max_doc_append_bytes)
    blocks = parse_markdown(markdown)
    if not blocks:
        raise ToolFailure(INVALID_INPUT, "content_markdown: no content to write.")

    with services.docs() as docs:
        current = call_with_retry(
            lambda: (
                docs.documents().get(documentId=doc_id, fields="body(content(endIndex))").execute()
            ),
            retry_statuses=TRANSIENT,
        )
        requests = build_write_requests(blocks, _body_end_index(current))
        # The delete range depends on the current length, so a blind retry could corrupt the
        # document: only rate-limit rejections (nothing applied) are retried.
        response = call_with_retry(
            lambda: (
                docs.documents()
                .batchUpdate(documentId=doc_id, body={"requests": requests})
                .execute()
            ),
            retry_statuses=RATE_LIMIT_ONLY,
        )
    revision = (response.get("writeControl") or {}).get("requiredRevisionId", "")
    return {"document_id": doc_id, "revision_id": revision}


def get_document(services: Any, settings: Settings, *, document_id: str) -> dict[str, Any]:
    doc_id = extract_document_id(document_id)
    with services.docs() as docs:
        document = call_with_retry(
            lambda: docs.documents().get(documentId=doc_id).execute(), retry_statuses=TRANSIENT
        )
    return {
        "document_id": document.get("documentId", doc_id),
        "title": document.get("title", ""),
        "text": document_text(document),
        "url": document_url(doc_id),
        "revision_id": document.get("revisionId", ""),
    }


def find_by_marker(services: Any, settings: Settings, *, marker: str) -> dict[str, Any]:
    clean_marker = validate_marker(marker)  # charset excludes quotes, so safe inside the query
    query = (
        f"appProperties has {{ key='{MARKER_PROPERTY}' and value='{clean_marker}' }} "
        f"and mimeType='{DOC_MIME}' and trashed=false"
    )
    with services.drive() as drive:
        listing = call_with_retry(
            lambda: (
                drive.files()
                .list(q=query, fields="files(id,name)", pageSize=MAX_FIND_RESULTS)
                .execute()
            ),
            retry_statuses=TRANSIENT,
        )
    return {
        "documents": [
            {"document_id": f["id"], "title": f.get("name", ""), "url": document_url(f["id"])}
            for f in listing.get("files", [])
        ]
    }


def count_pdf_pages(pdf: bytes) -> int:
    """Count pages in a Google-exported PDF without a PDF library.

    Two independent readings (leaf page objects and the page-tree ``/Count``) must agree;
    otherwise the layout is reported as unknown rather than guessed.
    """
    if not pdf.startswith(b"%PDF"):
        raise ToolFailure(UPSTREAM_ERROR, "Google returned something that is not a PDF.")
    leaves = len(_PDF_LEAF_PAGE_RE.findall(pdf))
    counts = [int(c) for c in _PDF_PAGES_COUNT_RE.findall(pdf)]
    if leaves < 1 or not counts or max(counts) != leaves:
        raise ToolFailure(UPSTREAM_ERROR, "Could not determine the page count of the exported PDF.")
    return leaves


def get_page_count(services: Any, settings: Settings, *, document_id: str) -> dict[str, Any]:
    doc_id = extract_document_id(document_id)
    with services.drive() as drive:
        pdf = call_with_retry(
            lambda: drive.files().export(fileId=doc_id, mimeType="application/pdf").execute(),
            retry_statuses=TRANSIENT,
        )
    return {"document_id": doc_id, "page_count": count_pdf_pages(pdf)}
