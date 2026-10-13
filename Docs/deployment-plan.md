# Deployment Plan: gmail-docs-mcp

Written for: the developer who deployed and operates this server.

## 1. Outcome

| | |
|---|---|
| **Live MCP URL** | `https://gmail-docs-mcp.vercel.app/mcp` |
| Host | Vercel (Python serverless function), project `mad-6a83/gmail-docs-mcp` |
| Transport | Streamable HTTP, **stateless**, plain JSON responses |
| Auth | `Authorization: Bearer <MCP_AUTH_TOKEN>` on every request (token is in the git-ignored `.env`) |
| Tools | `send_email`, `append_to_google_doc` for one Google account |

## 2. Why Vercel (and not Railway)

Railway was the first choice because it runs the long-lived container as-is ([Dockerfile](../Dockerfile),
[railway.json](../railway.json) are kept and work). The Railway account is on the free plan with
its resource limit already exceeded by two existing projects, so it refused to create a new one.
Vercel was chosen instead. It runs the same code through [api/index.py](../api/index.py), which
adapts the server to serverless:

- **Stateless MCP:** serverless instances cannot hold MCP sessions in memory, so each request is
  handled independently (`stateless_http=True`) and answered as JSON (`json_response=True`).
  Clients need no `initialize` handshake state.
- **One server per request:** the SDK session manager runs once per instance, so a fresh
  `MCPServer` is built per request; Google credentials are reused across warm invocations.
- **Any path maps to the endpoint:** `vercel.json` rewrites everything to the function and the
  entry point routes it to `/mcp`.
- **Token from env:** the container filesystem is read-only/ephemeral, so the Google token is read
  from `GOOGLE_TOKEN_JSON` and refreshed in memory.

## 3. Configuration (Vercel production env, stored as sensitive)

| Variable | Source |
|---|---|
| `MCP_AUTH_TOKEN` | generated; also saved in local `.env` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth Desktop client |
| `GOOGLE_TOKEN_JSON` | contents of local `token.json` (one line) |
| `LOG_LEVEL` | `INFO` |

Function settings (`vercel.json`): max duration 30 s; `src/**` bundled; Python 3.12 (`.python-version`).

## 4. How it was deployed

```powershell
# from a clean staging folder containing only api/, src/, vercel.json, requirements.txt, .python-version
vercel link --yes --project gmail-docs-mcp
# each variable piped via stdin (values never echoed)
vercel env add MCP_AUTH_TOKEN production --sensitive      # likewise the other variables
vercel deploy --prod --yes
```

Staging a clean folder guarantees `.env`, `token.json` and `client_secret_*.json` are never uploaded.

## 5. Verification performed (live URL)

| Check | Result |
|---|---|
| No token / wrong token | `401` |
| `tools/list` with token | `send_email`, `append_to_google_doc` |
| `send_email` `dry_run` | `dry_run` |
| `send_email` real send to own address | `sent` |
| `append_to_google_doc` on throwaway doc | `appended` |

## 6. Connecting clients

Remote servers need the bearer header:

```json
{
  "mcpServers": {
    "gmail-docs": {
      "url": "https://gmail-docs-mcp.vercel.app/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

Key names for remote servers differ per client (`url` / `serverUrl` / `httpUrl`); confirm in the
client's docs. ChatGPT connectors must be able to send a custom bearer header; if the connector UI
only offers OAuth or no auth, put a proxy in front rather than removing the token.

## 7. Redeploying and operating

- **Code change:** re-stage the folder and run `vercel deploy --prod --yes`.
- **Rotate access token:** `vercel env add MCP_AUTH_TOKEN production --sensitive --force`, redeploy, update clients.
- **Google token expiry:** while the OAuth consent screen is in *Testing*, refresh tokens expire
  after 7 days. Re-run `python -m gmail_docs_mcp auth`, then update `GOOGLE_TOKEN_JSON` and redeploy.
- **Logs:** Vercel dashboard -> project -> Logs (JSON lines; no tokens, bodies or doc text).
- **Teardown:** `vercel remove gmail-docs-mcp` or delete the project; revoke Google access at
  https://myaccount.google.com/permissions.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Public URL can send mail as you | Long random bearer token; rotate; delete project when unused. No rate limiting beyond Google's. |
| Cold starts add latency | Acceptable for tool calls; first call after idle is slower. |
| Hobby plan function limits | 30 s configured; Gmail/Docs calls take ~1-2 s. |
| Ambiguous 5xx on append may duplicate text | Documented; `send_email` is never retried on 5xx. |
