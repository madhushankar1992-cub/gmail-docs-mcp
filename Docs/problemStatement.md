# Problem Statement: Generic Gmail + Google Docs MCP Server

> **Purpose of this document:** a complete, self-contained specification that an AI coding agent
> (Cursor, Google Antigravity, or similar) can use to build, test, and document the MCP server
> described below without further clarification.

---

## 1. Summary

Build a **Model Context Protocol (MCP) server** that exposes two real-world Google Workspace
capabilities as tools any MCP-compatible AI agent can call:

1. **Send an email via Gmail**
2. **Append content to an existing Google Doc**

The server must be **agent-agnostic**: it must work unchanged with ChatGPT, Claude, Gemini, Cursor,
Antigravity, and any other client that speaks MCP. No tool, schema, or behavior may depend on one
vendor's quirks.

---

## 2. Background and Motivation

AI agents can reason and draft, but they cannot act on a user's accounts without tools. MCP is the
open standard that lets any agent discover and call tools over a uniform JSON-RPC protocol. Today a
user who wants "summarize this and email it to my team, then log it in the weekly notes doc" must
copy and paste between three apps. This server closes that gap with a single, vendor-neutral
integration, so the same server can be plugged into any agent of the user's choosing.

---

## 3. Goals

| # | Goal |
|---|------|
| G1 | Expose a `send_email` tool that sends mail from the authenticated user's Gmail account. |
| G2 | Expose an `append_to_google_doc` tool that appends text to the end of an existing Google Doc. |
| G3 | Be fully MCP-spec compliant so any compliant client can discover and call the tools. |
| G4 | Run locally over **stdio** (primary) and optionally remotely over **Streamable HTTP**. |
| G5 | Use least-privilege Google OAuth scopes and never leak credentials or tokens. |
| G6 | Ship with clear setup docs, per-client config snippets, and automated tests. |

## 4. Non-Goals

- Reading, searching, or deleting emails.
- Creating new Google Docs, editing or deleting existing content, or formatting beyond plain text.
- Multi-user / multi-tenant hosting. One server instance serves one Google account.
- A UI. The server is headless.
- Building an LLM client. Only the server is in scope.

---

## 5. Target Users and Clients

- **Primary user:** a developer or power user who wants their AI agent to send mail and log notes.
- **Build environments:** Cursor and Google Antigravity (the agent that *builds* the server).
- **Consuming clients (must all work):** Claude Desktop / Claude Code, ChatGPT (via remote MCP
  connector), Gemini CLI, Cursor, Antigravity, MCP Inspector, and any other MCP client.

---

## 6. Functional Requirements

### 6.1 Tool: `send_email`

Sends a plain-text or HTML email from the authenticated Gmail account.

**Input schema**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `to` | array of string | yes | One or more recipient email addresses. |
| `subject` | string | yes | Subject line. Max 998 chars. |
| `body` | string | yes | Message body. |
| `cc` | array of string | no | CC recipients. |
| `bcc` | array of string | no | BCC recipients. |
| `is_html` | boolean | no (default `false`) | If `true`, `body` is sent as `text/html`; otherwise `text/plain`. |
| `dry_run` | boolean | no (default `false`) | If `true`, validate and build the message but do **not** send. |

**Behavior**

- Validate every address (RFC 5322-reasonable syntax). Reject the whole call if any is invalid.
- Reject any header value containing CR or LF characters (header-injection protection).
- Build the message with the standard library MIME tooling, encode as base64url, and call Gmail
  `users.messages.send` with `userId="me"`.
- Require at least one recipient in `to`; cap total recipients (default 50, configurable).
- Cap body size (default 1 MB, configurable).

**Success output**

```json
{
  "status": "sent",
  "message_id": "18f3c2a1b4d5e6f7",
  "thread_id": "18f3c2a1b4d5e6f7",
  "to": ["alice@example.com"]
}
```

With `dry_run: true`, return `"status": "dry_run"` and omit the IDs.

### 6.2 Tool: `append_to_google_doc`

Appends text to the end of an existing Google Doc.

**Input schema**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `document` | string | yes | The Google Doc ID **or** its full URL. The server extracts the ID. |
| `text` | string | yes | Text to append. Newlines are preserved. |
| `add_leading_newline` | boolean | no (default `true`) | Insert a newline before the text so it starts on its own line. |

**Behavior**

- Accept either a bare document ID or a URL of the form
  `https://docs.google.com/document/d/<ID>/edit...` and extract `<ID>`.
- Append using the Docs API `documents.batchUpdate` with an `InsertTextRequest` whose location is
  `endOfSegmentLocation` (body segment). This avoids having to fetch the document and compute an
  end index, and is safe under concurrent edits.
- The operation is **append-only**. The server must never delete or overwrite existing content.
- Cap text size per call (default 100 KB, configurable).

**Success output**

```json
{
  "status": "appended",
  "document_id": "1AbC...xyz",
  "characters_appended": 142,
  "document_url": "https://docs.google.com/document/d/1AbC...xyz/edit"
}
```

### 6.3 Optional (stretch) tool: `get_google_doc_text`

A read-only tool that returns the plain text of a doc so an agent can verify an append. Include it
only after §6.1 and §6.2 are complete and tested. If implemented, it must declare
`readOnlyHint: true`.

---

## 7. Cross-Agent Compatibility Requirements

These rules exist so the same server works with every agent, not just one.

1. **Spec compliance:** implement the current MCP specification using the official SDK
   (`mcp` for Python, or `@modelcontextprotocol/sdk` for TypeScript). Do not hand-roll the protocol.
2. **Flat, simple JSON Schemas:** use only `string`, `boolean`, `integer`, `array`, `object`, and
   `enum`. **Avoid** `oneOf`, `anyOf`, `allOf`, `$ref`, recursive schemas, and `format` keywords
   beyond `email`/`uri`. Some clients (notably Gemini's function-calling layer) support only a
   subset of JSON Schema and will reject richer schemas.
3. **Portable tool names:** `snake_case`, ASCII letters/digits/underscores only, under 64 chars,
   unique. No dots, spaces, or hyphens.
4. **Rich descriptions:** every tool and every parameter needs a clear natural-language
   description including an example value. The model reads these to decide when and how to call.
5. **Text-first results:** always return the result as a `text` content block containing JSON.
   Optionally also populate `structuredContent`, but never rely on it; not all clients render it.
6. **Errors as tool results:** on failure return `isError: true` with a human-readable message and
   a stable machine-readable `error_code`. Do not crash the process or emit protocol-level errors
   for ordinary failures such as a bad address or a doc the user cannot access.
7. **Tool annotations:** set `destructiveHint`, `idempotentHint`, `openWorldHint`, and
   `readOnlyHint` correctly so clients can prompt for confirmation appropriately.
   - `send_email`: `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=false`,
     `openWorldHint=true`.
   - `append_to_google_doc`: `readOnlyHint=false`, `destructiveHint=false`,
     `idempotentHint=false`, `openWorldHint=true`.
8. **No client-specific code paths.** If behavior must differ per client, the design is wrong.

---

## 8. Transport

| Transport | Status | Used by |
|-----------|--------|---------|
| **stdio** | Required, default | Claude Desktop, Claude Code, Cursor, Antigravity, Gemini CLI, MCP Inspector |
| **Streamable HTTP** | Required to support remote clients; enabled by flag | ChatGPT connectors and any hosted agent |

- Select transport with a CLI flag or env var, e.g. `--transport stdio|http` (default `stdio`),
  plus `--host` / `--port` for HTTP (default `127.0.0.1:8000`).
- **stdio hygiene:** in stdio mode, **never write anything to stdout except MCP protocol
  messages.** All logs go to **stderr** or a log file. A stray `print()` corrupts the stream and
  breaks every client.
- **HTTP mode security:** bind to localhost by default. If exposed publicly it must sit behind
  HTTPS and require a bearer token (`MCP_AUTH_TOKEN`), validated on every request. Document that
  ChatGPT requires a publicly reachable HTTPS URL (e.g., via a tunnel) and that this is an
  explicit opt-in with a security warning.

---

## 9. Authentication and Authorization (Google)

### 9.1 Mechanism

Google **OAuth 2.0 for installed (Desktop) apps** with a refresh token.

### 9.2 Scopes (least privilege)

| Scope | Why |
|-------|-----|
| `https://www.googleapis.com/auth/gmail.send` | Send only; cannot read the mailbox. |
| `https://www.googleapis.com/auth/documents` | Required to edit Google Docs. |

Do **not** request `gmail.modify`, `gmail.readonly`, full `mail.google.com`, or Drive-wide scopes.
(`https://www.googleapis.com/auth/documents.readonly` may be added only if the optional
§6.3 tool is built.)

### 9.3 First-run flow

1. The user places `credentials.json` (OAuth client, type *Desktop app*) at the path given by
   `GOOGLE_CREDENTIALS_PATH`.
2. A one-time command (`python -m <package> auth`, or `npm run auth`) opens the browser consent
   flow and writes `token.json` to `GOOGLE_TOKEN_PATH`.
3. At runtime the server loads `token.json`, **refreshes the access token automatically**, and
   re-saves it.
4. If the refresh token is missing or revoked, tools return an `isError` result with
   `error_code: "AUTH_REQUIRED"` and instructions to re-run the auth command. The server itself
   must not block on an interactive prompt while serving.

### 9.4 Google Cloud setup (must be documented in the README)

1. Create a Google Cloud project.
2. Enable the **Gmail API** and the **Google Docs API**.
3. Configure the OAuth consent screen; add the user's account as a **test user**.
4. Create an OAuth client ID of type **Desktop app**; download `credentials.json`.
5. Note: while the consent screen is in **Testing** status, refresh tokens expire after 7 days,
   after which the user must re-run the auth command. Document this clearly.

### 9.5 Secret handling

- `credentials.json`, `token.json`, and `.env` must be listed in `.gitignore` from the first commit.
- Never log tokens, auth codes, email bodies, or full document contents. Log only metadata
  (tool name, status, message ID, document ID, duration).
- Create `token.json` with restrictive file permissions where the OS supports it.

---

## 10. Non-Functional Requirements

| Area | Requirement |
|------|-------------|
| **Reliability** | Retry transient Google errors (HTTP 429, 500, 503) with exponential backoff and jitter, max 3 attempts. Do **not** retry on `send_email` after an ambiguous failure that may have already sent; surface the error instead to avoid duplicate emails. |
| **Timeouts** | Per-call timeout of 30 s (configurable). |
| **Performance** | Tool call overhead beyond the Google API round trip should be negligible (< 50 ms). |
| **Security** | Input validation on every parameter; header-injection protection; size caps; no shell execution; no arbitrary file reads. |
| **Observability** | Structured logs (JSON lines) to stderr with a configurable `LOG_LEVEL`. |
| **Portability** | Runs on Windows, macOS, and Linux. Use `pathlib`/`path` and avoid shell-specific assumptions. |
| **Config** | All settings via environment variables, with an `.env.example` committed. |
| **Quality** | Type hints throughout; linted and formatted; automated tests; CI-friendly one-command test run. |

### 10.1 Error codes

Use a small, stable set:

| `error_code` | Meaning |
|--------------|---------|
| `AUTH_REQUIRED` | No valid token; user must run the auth command. |
| `INVALID_INPUT` | Validation failed (bad address, empty body, bad doc ID, size cap). |
| `NOT_FOUND` | Document does not exist or is not visible to the authorized account. |
| `PERMISSION_DENIED` | Account lacks edit access to the document, or scope missing. |
| `RATE_LIMITED` | Google quota hit after retries. |
| `UPSTREAM_ERROR` | Any other Google API failure. |

---

## 11. Recommended Technology Stack

The implementing agent should use **Python 3.11+** unless the user states otherwise:

- `mcp` (official MCP Python SDK, `FastMCP` high-level API)
- `google-api-python-client`, `google-auth`, `google-auth-oauthlib`
- `pydantic` for input validation
- `python-dotenv` for config
- `pytest`, `pytest-asyncio` for tests; `ruff` for lint/format; `mypy` optional
- Package manager: `uv` (preferred) or `pip` + `venv`

A TypeScript implementation (`@modelcontextprotocol/sdk`, `googleapis`, `zod`) is an acceptable
alternative; if chosen, all requirements above still apply unchanged.

---

## 12. Suggested Project Structure

```
mcp-gmail-docs-server/
├── pyproject.toml
├── README.md
├── problemStatement.md
├── .env.example
├── .gitignore                # credentials.json, token.json, .env, __pycache__, .venv
├── src/
│   └── gmail_docs_mcp/
│       ├── __init__.py
│       ├── __main__.py        # CLI: serve | auth
│       ├── server.py          # FastMCP app, tool registration, transport selection
│       ├── config.py          # env-driven settings
│       ├── auth.py            # OAuth flow, token load/refresh/save
│       ├── gmail_service.py   # build MIME, call users.messages.send
│       ├── docs_service.py    # parse doc ID, call documents.batchUpdate
│       ├── validation.py      # email + doc-ID + size validation
│       ├── errors.py          # error codes, exception → tool result mapping
│       └── logging_setup.py   # stderr JSON logging
├── tests/
│   ├── test_validation.py
│   ├── test_gmail_service.py  # mocked Google client
│   ├── test_docs_service.py   # mocked Google client
│   └── test_server_tools.py   # in-memory MCP client calling the tools
└── examples/
    ├── cursor.mcp.json
    ├── claude_desktop_config.json
    ├── gemini_settings.json
    └── antigravity_mcp_config.json
```

---

## 13. Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CREDENTIALS_PATH` | `./credentials.json` | OAuth client secrets file. |
| `GOOGLE_TOKEN_PATH` | `./token.json` | Stored user token. |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MCP_HOST` | `127.0.0.1` | HTTP bind host. |
| `MCP_PORT` | `8000` | HTTP bind port. |
| `MCP_AUTH_TOKEN` | *(unset)* | Required bearer token when transport is `http`. |
| `MAX_RECIPIENTS` | `50` | Cap on to+cc+bcc. |
| `MAX_EMAIL_BODY_BYTES` | `1048576` | Cap on email body. |
| `MAX_DOC_APPEND_BYTES` | `102400` | Cap on a single append. |
| `REQUEST_TIMEOUT_SECONDS` | `30` | Per Google API call. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. |

---

## 14. Client Configuration Examples (must ship in `examples/`)

Exact file locations and key names vary by client and version; the README must tell the user to
confirm against the client's current documentation. Command shapes:

**Cursor** (`.cursor/mcp.json` or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "gmail-docs": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/mcp-gmail-docs-server", "run", "gmail-docs-mcp"],
      "env": {
        "GOOGLE_CREDENTIALS_PATH": "C:/path/to/credentials.json",
        "GOOGLE_TOKEN_PATH": "C:/path/to/token.json"
      }
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`) and **Gemini CLI** (`settings.json`) use the same
`mcpServers` shape. **Antigravity** uses its own MCP config (Agent panel → MCP servers → manage /
raw config) with the same `command` / `args` / `env` fields.

**ChatGPT:** requires the HTTP transport exposed at a public HTTPS URL, added as a custom MCP
connector in ChatGPT's settings. Document the steps and the security implications.

> Use **absolute paths** in every client config. Many clients launch the server from an unknown
> working directory.

---

## 15. Testing Requirements

### 15.1 Automated (must pass with one command, e.g. `uv run pytest`)

- **Validation:** valid/invalid emails, CR/LF injection attempts, empty `to`, oversize body,
  doc ID extraction from bare ID and several URL forms (`/edit`, `/edit?tab=...`, `/view`).
- **Gmail service:** MIME construction (plain and HTML), correct base64url encoding, CC/BCC
  handling, `dry_run` makes no API call. Google client is mocked.
- **Docs service:** request body contains `insertText` with `endOfSegmentLocation`; no delete
  requests are ever emitted; leading-newline option works.
- **Server layer:** use an in-memory MCP client to confirm both tools are listed with correct
  schemas and annotations, success results parse as JSON, and failures return `isError: true`
  with a valid `error_code`.
- **Auth:** missing/expired token returns `AUTH_REQUIRED` rather than raising or prompting.

### 15.2 Manual end-to-end checklist (document in README)

1. Run `auth`; confirm `token.json` is created.
2. Open **MCP Inspector**, connect over stdio, list tools, confirm schemas.
3. Call `send_email` with `dry_run: true`, then a real send to the user's own address.
4. Call `append_to_google_doc` against a throwaway doc; confirm text appears at the end.
5. Connect from at least two different agents (e.g., Cursor and Claude Desktop) and have each
   call both tools through natural-language prompts.
6. Revoke the token; confirm `AUTH_REQUIRED` is returned cleanly.

---

## 16. Acceptance Criteria

The project is complete when **all** of the following are true:

- [ ] `send_email` delivers a real email from the authenticated Gmail account.
- [ ] `append_to_google_doc` appends text to the end of an existing doc without altering prior content.
- [ ] Both tools appear with correct names, descriptions, schemas, and annotations in MCP Inspector.
- [ ] The server works over stdio in at least **two different MCP clients** without code changes.
- [ ] The HTTP transport works with MCP Inspector and is protected by a bearer token.
- [ ] No tokens, secrets, email bodies, or document contents appear in any log.
- [ ] Only the two required OAuth scopes are requested.
- [ ] `credentials.json`, `token.json`, and `.env` are git-ignored.
- [ ] The automated test suite passes with one command.
- [ ] README covers Google Cloud setup, install, auth, per-client config, troubleshooting, and
      the 7-day test-mode token expiry caveat.

---

## 17. Implementation Plan for the Building Agent

Work in this order, verifying each step before moving on:

1. **Scaffold** the project, dependencies, `.gitignore`, `.env.example`, and config loader.
2. **Auth module + `auth` CLI command**; verify a token is produced and refreshed.
3. **Validation module** with tests (pure functions, easiest to get right first).
4. **Gmail service** + `send_email` tool; test with `dry_run`, then a real send.
5. **Docs service** + `append_to_google_doc` tool; test against a throwaway doc.
6. **Error mapping** to the stable error-code set; confirm `isError` behavior.
7. **Logging** to stderr only; audit that nothing writes to stdout in stdio mode.
8. **HTTP transport** with bearer-token check.
9. **Examples + README**, then run the manual checklist in §15.2.
10. *(Optional)* `get_google_doc_text` stretch tool.

**Rules for the building agent**

- Do not invent MCP protocol details; use the official SDK and consult its current documentation.
- Do not add features beyond this spec without asking.
- If any requirement here conflicts with current Google API or MCP SDK behavior, stop and report
  the conflict instead of silently working around it.
- Never commit or print real credentials.

---

## 18. Open Questions (to confirm with the user before or during build)

1. Python (default) or TypeScript?
2. Is remote/ChatGPT access needed now, or is local stdio sufficient for the first release?
3. Should recipient allow-listing (restrict `send_email` to approved domains) be added as a
   safety guard?
4. Should the sender display name be configurable (`SENDER_NAME`)?
