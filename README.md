# gmail-docs-mcp

A vendor-neutral [Model Context Protocol](https://modelcontextprotocol.io) server that gives any
MCP-capable agent (Claude, ChatGPT, Gemini, Cursor, Antigravity, ...) two tools:

| Tool | What it does |
|------|--------------|
| `send_email` | Sends a plain-text or HTML email from your Gmail account (`dry_run` supported). |
| `append_to_google_doc` | Appends text to the end of an existing Google Doc. Append-only. |

Spec: [Docs/problemStatement.md](Docs/problemStatement.md).

## 1. Install

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

(macOS/Linux: `source .venv/bin/activate && pip install -e ".[dev]"`.)

## 2. Google Cloud setup (one time)

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/).
2. **APIs & Services -> Library**: enable the **Gmail API** and the **Google Docs API**.
3. **OAuth consent screen**: choose *External*, fill in the basics, and add your Google account
   as a **Test user**.
4. **Credentials -> Create credentials -> OAuth client ID**, type **Desktop app**. Either put the
   client ID and secret in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, or download the
   JSON and save it as `credentials.json` in this folder (or point `GOOGLE_CREDENTIALS_PATH` at it).
   The file takes precedence when both are present.
5. Sign in once:

   ```powershell
   .\.venv\Scripts\python.exe -m gmail_docs_mcp auth
   ```

   A browser opens; approve **send email** and **Google Docs** access. `token.json` is written.

> **Testing-mode caveat:** while the consent screen is in *Testing* status, Google expires
> refresh tokens after **7 days**. When that happens tools return `AUTH_REQUIRED`; just re-run
> the `auth` command. Publishing the app to production removes the limit.

Scopes requested (and nothing else): `gmail.send` and `documents`.

## 3. Run

```powershell
# stdio (default) - what desktop/IDE clients launch for you
.\.venv\Scripts\python.exe -m gmail_docs_mcp

# Streamable HTTP (remote clients). MCP_AUTH_TOKEN is mandatory.
$env:MCP_AUTH_TOKEN = "<long random string>"
.\.venv\Scripts\python.exe -m gmail_docs_mcp --transport http --port 8000
# endpoint: http://127.0.0.1:8000/mcp   header: Authorization: Bearer <token>
```

Console script `gmail-docs-mcp` is equivalent to `python -m gmail_docs_mcp`.

## 4. Connect a client

Ready-made configs are in [examples/](examples/). **Edit the absolute paths to match your
machine** and confirm the file location in your client's current docs.

| Client | Config |
|--------|--------|
| Cursor | `.cursor/mcp.json` or `~/.cursor/mcp.json` - see `examples/cursor.mcp.json` |
| Claude Desktop | `claude_desktop_config.json` - see `examples/claude_desktop_config.json` |
| Gemini CLI | `~/.gemini/settings.json` - see `examples/gemini_settings.json` |
| Antigravity | Agent panel -> MCP servers -> manage / raw config - see `examples/antigravity_mcp_config.json` |
| Claude Code | `claude mcp add gmail-docs -- <python> -m gmail_docs_mcp` |
| MCP Inspector | `npx @modelcontextprotocol/inspector <python> -m gmail_docs_mcp` |

All of these use the same stdio command shape. Always use **absolute paths**: clients often
launch the server from an unknown working directory.

### Hosted deployment

A Vercel deployment is live at `https://gmail-docs-mcp.vercel.app/mcp` (stateless HTTP, bearer
token required). See [Docs/deployment-plan.md](Docs/deployment-plan.md). The entry point is
[api/index.py](api/index.py); a Railway-ready [Dockerfile](Dockerfile) is also included.

### ChatGPT / hosted agents

These need a remote server: run the HTTP transport and expose it at a public **HTTPS** URL
(for example through a tunnel or reverse proxy), then add it as a custom MCP connector.

- The server only accepts `Authorization: Bearer <MCP_AUTH_TOKEN>`. Check that your client's
  connector UI can send a custom bearer token; if it only offers OAuth or no auth, put a proxy
  that injects the header in front, rather than running the server unauthenticated.
- Behind a tunnel the `Host` header is the tunnel's hostname. Set `MCP_ALLOWED_HOSTS`
  (comma-separated hostnames) or the SDK's DNS-rebinding protection will reject requests.
- Exposing this publicly lets anyone holding the token send mail as you. Treat the token like a
  password, rotate it, and shut the tunnel down when not in use.

## 5. Tool reference

### `send_email`

| Param | Type | Notes |
|-------|------|-------|
| `to` | string[] | required, at least one |
| `subject` | string | required, single line, max 998 chars |
| `body` | string | required |
| `cc`, `bcc` | string[] | optional |
| `is_html` | boolean | default `false` |
| `dry_run` | boolean | default `false`; validates and builds only, needs no Google login |

Success: `{"status": "sent", "message_id": "...", "thread_id": "...", "to": [...]}`

### `append_to_google_doc`

| Param | Type | Notes |
|-------|------|-------|
| `document` | string | doc ID or full `docs.google.com/document/d/<ID>/...` URL |
| `text` | string | required |
| `add_leading_newline` | boolean | default `true` |

Success: `{"status": "appended", "document_id": "...", "characters_appended": 12, "document_url": "..."}`

The text is inserted at the end of the document body with a single `insertText` request; nothing
is ever deleted or overwritten. Note `characters_appended` includes the leading newline.

### Errors

Failures are returned as a tool result with `isError: true` and JSON
`{"status": "error", "error_code": "...", "message": "..."}`.

| `error_code` | Meaning |
|--------------|---------|
| `AUTH_REQUIRED` | No/expired/revoked token - run `auth` again. |
| `INVALID_INPUT` | Bad address, empty body, bad doc ID, size or recipient cap exceeded. |
| `NOT_FOUND` | Doc does not exist or isn't visible to the signed-in account. |
| `PERMISSION_DENIED` | No edit access to the doc, or OAuth scope missing. |
| `RATE_LIMITED` | Google quota hit after retries. |
| `UPSTREAM_ERROR` | Any other Google/network failure. |

## 6. Configuration

All via environment variables (or a `.env` file; see [.env.example](.env.example)).

| Variable | Default | |
|----------|---------|---|
| `GOOGLE_CREDENTIALS_PATH` | `./credentials.json` | OAuth client file |
| `GOOGLE_TOKEN_PATH` | `./token.json` | Saved user token |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | HTTP bind |
| `MCP_AUTH_TOKEN` | - | Required for `http` |
| `MCP_ALLOWED_HOSTS` | - | Extra Host names for tunnels/proxies |
| `MAX_RECIPIENTS` | `50` | to+cc+bcc cap |
| `MAX_EMAIL_BODY_BYTES` | `1048576` | |
| `MAX_DOC_APPEND_BYTES` | `102400` | |
| `REQUEST_TIMEOUT_SECONDS` | `30` | per Google call |
| `LOG_LEVEL` | `INFO` | JSON lines on stderr |

## 7. Behavior worth knowing

- **Retries:** `send_email` retries only on rate-limit responses (429 / rate-limit 403); a 5xx or
  timeout may have already sent the message, so it is surfaced instead of re-sent.
  `append_to_google_doc` retries 429/500/503 (max 3 attempts, exponential backoff + jitter), so in
  a rare ambiguous 5xx the text could be appended twice.
- **Logging:** structured JSON to stderr only (stdout is the MCP channel). Tokens, email bodies
  and document contents are never logged - only tool name, status, IDs, duration.
- **Secrets:** `credentials.json`, `token.json`, `.env` are git-ignored.
- **Concurrency:** Google calls are serialized (one account, one HTTP client).

## 8. Development and testing

```powershell
.\.venv\Scripts\python.exe -m pytest      # unit + in-process MCP + real stdio/HTTP subprocess tests
.\.venv\Scripts\python.exe -m ruff check src tests
```

Layout:

```
src/gmail_docs_mcp/
  __main__.py      CLI: serve | auth
  server.py        MCPServer + tool registration
  http_app.py      bearer-token wrapper for the HTTP transport
  auth.py          OAuth flow, token refresh, Google client construction
  gmail_service.py MIME building + send
  docs_service.py  documents.batchUpdate append
  validation.py    email / doc-ID / size validation
  errors.py        error codes + exception mapping
  retry.py         bounded backoff
  config.py        env settings
  logging_setup.py JSON stderr logging
```

### Manual end-to-end checklist

1. `python -m gmail_docs_mcp auth` -> `token.json` exists.
2. MCP Inspector over stdio: both tools listed with schemas.
3. `send_email` with `dry_run: true`, then a real send to yourself.
4. `append_to_google_doc` against a throwaway doc; text appears at the end.
5. Connect two different clients and call both tools from natural-language prompts.
6. Delete/revoke `token.json`; confirm `AUTH_REQUIRED` is returned cleanly.
