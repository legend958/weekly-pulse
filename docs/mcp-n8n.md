# n8n MCP publish — Stage G contract

Stage G publishes the approved weekly packet through an **n8n MCP Server Trigger**
speaking **MCP over streamable HTTP** (JSON-RPC 2.0). The pipeline performs the
handshake (`initialize` → `notifications/initialized` → `tools/list` / `tools/call`)
and calls **two tools**, only when the approval state is exactly `approved`.

## Shipped workflows

| File | What it is | Use it? |
|---|---|---|
| `docs/n8n-weekly-pulse-mcp-prewired.json` | The minimal MCP workflow with **your** doc URL and Google Docs / Gmail credentials already attached | ✅ **easiest** — only the Bearer credential is left to create |
| `docs/n8n-weekly-pulse-mcp.json` | Minimal MCP workflow: `MCP Server Trigger` + `append_to_note` + `create_email_draft` (generic, placeholders) | ✅ import this if you want the smallest setup |
| `docs/mcp_workflow_v2.json` | The Milestone 5 workflow with an `MCP Server Trigger` **added on top** of the existing Webhook → Wait for Approval → Google Docs / Gmail flow (reuses the same credentials) | ✅ import this to keep your existing approval flow |
| `docs/mcp_workflow.json` | Your original export — **Webhook only**, no MCP trigger | ❌ not callable by the pipeline |

> **Webhook path conflict:** `mcp_workflow_v2.json` keeps the original `mcp-review-pulse`
> webhook. If your existing workflow is still active on that same path, publishing the
> import fails with a path conflict — deactivate the old one or use a prewired/minimal file.

Validate any of them without n8n:

```powershell
python scripts/check_n8n_workflow.py docs/mcp_workflow_v2.json
```

## One-command provisioning (optional shortcut)

`scripts/setup_n8n_mcp.py` does the whole n8n side through the public API —
create the Bearer credential, create the workflow wired to it, and publish it —
so you don't have to click through the UI:

```powershell
# secrets live in .env.local (gitignored), never on the command line
#   PULSE_N8N_BASE_URL=https://<your-n8n>
#   PULSE_N8N_API_KEY=<key with scopes: credential:create, workflow:create, workflow:activate>
python scripts/setup_n8n_mcp.py --check     # read-only: verify URL + API key
python scripts/setup_n8n_mcp.py --dry-run   # print the requests, send nothing
python scripts/setup_n8n_mcp.py             # do it, then print the MCP URL
```

It creates the Bearer credential with a fixed ID (`pulseMcpBearer1`) and wires it,
reuses your existing Google Docs / Gmail credentials, rewrites the sticky note,
publishes the workflow, and writes `PULSE_MCP_ENDPOINT` back into `.env.local`.
On n8n versions without `POST /credentials`, create the credential in the UI and
re-run with `--no-credential --credential-id <id>`.

## Endpoint

| | Value |
|---|---|
| Production | `https://<your-n8n>/mcp/<path>` — with `path = weekly-pulse` → `https://<your-n8n>/mcp/weekly-pulse` |
| Test | `https://<your-n8n>/mcp-test/<path>` — only answers while you click **Listen for Test Event** |
| Auth header | `Authorization: Bearer <token>` |

## Tool contract

| Tool (must keep this exact name) | Arguments the pipeline sends | Should return |
|---|---|---|
| `append_to_note` | `title`, `content` (Markdown packet), `run_id` (+ optional `target`) | a `url` / `id` for the appended entry |
| `create_email_draft` | `subject`, `body` (same packet), `run_id` (+ optional `to`) | a `draftId` / `id` — **create only, never send** |

In n8n, `$fromAI('key')` binds a node parameter to the MCP argument of the same
name, so the two nodes read `$fromAI('title'|'content')` and
`$fromAI('subject'|'body')` respectively.

## Pipeline → n8n settings

| Setting | Env var | Example |
|---|---|---|
| Transport | `PULSE_MCP_TRANSPORT` | `http` |
| Endpoint | `PULSE_MCP_ENDPOINT` | `https://n8n.example.com/mcp/weekly-pulse` |
| Token | `PULSE_MCP_TOKEN` | `Bearer …` secret |
| Tool 1 name | `PULSE_MCP_TOOL_APPEND_NOTE` | `append_to_note` |
| Tool 2 name | `PULSE_MCP_TOOL_EMAIL_DRAFT` | `create_email_draft` |
| Note target | `PULSE_MCP_NOTE_TARGET` | Google Doc ID or full URL (optional; enables a canonical doc URL in logs) |
| Draft recipient | `PULSE_MCP_EMAIL_TO` | optional; blank leaves it to the reviewer |
| Max retries | `PULSE_MCP_MAX_RETRIES` | `2` — retries after the first attempt |
| Backoff base (s) | `PULSE_MCP_BACKOFF_SECONDS` | `0.5` — exponential (`base * 2**attempt`) |

## What the pipeline sends on approval

`publish()` calls both tools and appends one `PublishRecord` each to
`output/<run-id>/publish_log.json`:

| outcome | tool | artifact tag | destination |
|---|---|---|---|
| `append_note` | `append_to_note` | `pulse` | canonical doc URL (from the returned `documentId`) |
| `create_email_draft` | `create_email_draft` | `fee_explainer` | Gmail draft link (from the returned message id) |

If a call fails, the record is written with `outcome: "error"` and the message;
the other outcome still runs.

## Behaviour the server must honour

- **Static check first.** `python scripts/check_n8n_workflow.py [workflow.json]`
  validates the exported workflow against this contract: MCP trigger + `bearerAuth`
  + path, tool nodes named `append_to_note` / `create_email_draft` wired via
  `ai_tool`, the `$fromAI(...)` bindings, and attached credentials.
- **Preflight next.** `scripts/check_n8n_endpoint.py` runs `initialize` +
  `tools/list` against the Production URL (publishes nothing). It fails if the
  workflow is inactive, the URL points at `/mcp-test/`, the tool names differ, or
  authentication is wrong.
- **Return an identifier** (`url` / `draftId`) per call so the log is auditable
  (PRD R17). Google Docs *Update* returns an empty `batchUpdate` body; in that case
  set `PULSE_MCP_NOTE_TARGET` so the pipeline logs a canonical doc URL, or expose
  the append through a **Call n8n Workflow Tool** sub-workflow ending in a **Set**
  node returning `{ "url": ... }`. The pipeline reads `url`/`link`/`id`/
  `documentId`/`draftId` from `structuredContent` **or** a JSON text block — as a
  bare object *or* an array (`[{"documentId": ...}]`, the shape n8n actually
  returns) — and also scans for an `https://` URL. When a `documentId` comes back
  the note destination is rendered as a canonical Docs URL; when a Gmail draft's
  `message.id` comes back the email destination is a `mail.google.com/.../#drafts/`
  link.
- **Fail loudly.** On a node error return a JSON-RPC `error` (or a non-2xx HTTP
  status); the pipeline retries up to `PULSE_MCP_MAX_RETRIES` times with
  exponential backoff, then records `outcome: "error"` rather than pretending
  success. The other outcome still runs.
- **Idempotency** is not handled by the pipeline: `run_id` is passed so the n8n
  side can dedupe if a call is retried (backoff means the same `run_id` may arrive
  more than once).
- **Never auto-send.** Outcome 2 is a draft by contract.
- **No buffering.** Reverse proxies in front of n8n must not buffer the MCP
  endpoint (SSE / streamable HTTP).

## Build steps

> **Shortest path (no API key needed):** import
> `docs/n8n-weekly-pulse-mcp-prewired.json` — your doc URL and Google Docs / Gmail
> credentials are already attached. You only need steps **2** (create the Bearer
> credential) and **6** (publish).

1. **Import** `docs/n8n-weekly-pulse-mcp-prewired.json`, or
   `docs/mcp_workflow_v2.json` / `docs/n8n-weekly-pulse-mcp.json`
   via *Workflows → ⋯ → Import from JSON*.
2. **MCP Server Trigger**: set **Authentication → Bearer Auth**, then pick/create
   the credential. Use the **generic `Bearer Auth` credential** (the one from the
   HTTP Request node) — *not* GitHub, Google, or any service-specific OAuth
   credential. The dropdown only offers **None / Bearer auth / Header auth**, and
   Bearer auth is a single field: **Bearer Token**. It is a shared secret you
   invent, not a token from a third-party service. Generate one with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`, paste it as the
   Bearer Token, and set `PULSE_MCP_TOKEN` to that same string (no `Bearer `
   prefix — n8n adds it). The pipeline sends exactly
   `Authorization: Bearer <PULSE_MCP_TOKEN>`. This credential lives in the n8n UI
   and never appears in the workflow JSON.
3. **Credentials**: attach Google credentials to `append_to_note` and Gmail
   credentials to `create_email_draft`. In `mcp_workflow_v2.json` these are already
   referenced by id from your own export — re-attach them if you import into a
   different n8n instance.
4. **Doc target**: point `append_to_note`'s **Document URL** at your notes doc and
   ensure the insert action uses `insertSegment: body` /
   `locationChoice: endOfSegmentLocation` so it appends to the end of the body.
5. **Do not rename** the tool nodes — the pipeline calls `append_to_note` and
   `create_email_draft`.
6. **Activate** the workflow and copy the **Production URL** shown on the trigger.

   ```bash
   PULSE_MCP_TRANSPORT=http
   PULSE_MCP_ENDPOINT=https://<your-n8n>/mcp/weekly-pulse
   PULSE_MCP_TOKEN=<same bearer token>
   PULSE_MCP_NOTE_TARGET=<your-google-doc-id-or-url>  # optional, canonical URL in logs

   python scripts/check_n8n_workflow.py docs/mcp_workflow_v2.json  # static
   python scripts/check_n8n_endpoint.py                            # handshake + tools/list
   python run_pipeline.py --decision approve
   ```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `MCP connection failed … actively refused` | Workflow inactive, wrong host/port, or the URL is the **test** URL without *Listen for Test Event* |
| `FAILED: MCP HTTP 401` | Bearer token on the trigger ≠ `PULSE_MCP_TOKEN` |
| `MISSING: append_to_note` | A tool node was renamed, or the `ai_tool` connection is missing |
| `tools/list` returns **zero** tools | Nothing is wired into the MCP trigger. A trigger with no `ai_tool` edges exposes no tools — drag each tool node's output into the trigger's **Tool** input, then publish |
| `destination` is `append_to_note://<run_id>` | Google Docs returned no identifier — set `PULSE_MCP_NOTE_TARGET` |
| `destination` is `create_email_draft://<run_id>` | Gmail returned no message id — confirm the draft node ran and its output carries `message.id` |
| Draft not created | Gmail credential missing / draft resource-operation not *Create* |
| "Credential with ID … not found" after import | The export referenced another instance's credentials — open each node and re-select the credential from the dropdown |
| Publish fails with a webhook path conflict | Two active workflows share a webhook path (v2 reuses `mcp-review-pulse`) — deactivate the old one, or import the prewired/minimal file |
