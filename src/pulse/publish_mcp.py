"""Stage G — MCP publish (PRD R15/R17; architecture.md §5 Stage G).

Two outcomes, both performed only when the approval state is exactly `approved`:

1. ``append_note``         — append the weekly packet (pulse + fee explainer) to a
                             Notes/Doc (§ ``mcp_tool_append_note``).
2. ``create_email_draft``  — create an email draft containing the same packet for a
                             human to review and send (§ ``mcp_tool_email_draft``).

The live implementation targets an **n8n MCP Server Trigger** over streamable HTTP
(JSON-RPC 2.0). ``MockMCPClient`` keeps the pipeline runnable offline (tests/CI).
See ``docs/mcp-n8n.md`` for the exact tool contract the n8n workflow must expose.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import Config
from .models import ApprovalState, FeeExplainer, ProductPulse, PublishRecord


class MCPError(RuntimeError):
    """Raised when an MCP tool call cannot be completed."""


# --------------------------------------------------------------------------- #
# Client interface
# --------------------------------------------------------------------------- #
class MCPClient:
    """Minimal interface every MCP client implements."""

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke tool `name`; return {"destination": str, "detail": str,
        "result": dict (live client only)}."""
        raise NotImplementedError

    def list_tools(self) -> list[str]:
        """Return the tool names the server exposes (used by the preflight)."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Offline / test client
# --------------------------------------------------------------------------- #
class MockMCPClient(MCPClient):
    """Records intended writes without touching any external system."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._n = 0

    def call_tool(self, name: str, arguments: dict) -> dict:
        self._n += 1
        run_id = arguments.get("run_id", "run")
        scheme = "docs" if name == self.config.mcp_tool_append_note else "mail"
        return {
            "destination": f"{scheme}://weekly-pulse/{run_id}/{name}",
            "detail": f"mock:{name}",
        }

    def list_tools(self) -> list[str]:
        return [self.config.mcp_tool_append_note, self.config.mcp_tool_email_draft]


# --------------------------------------------------------------------------- #
# Live client — n8n MCP Server Trigger (streamable HTTP, JSON-RPC 2.0)
# --------------------------------------------------------------------------- #
class N8NMCPClient(MCPClient):
    """JSON-RPC over HTTP client for an n8n MCP Server Trigger.

    Handshake: ``initialize`` -> ``notifications/initialized`` -> ``tools/call``.
    The server may answer with ``application/json`` or an SSE frame; both are
    parsed. A returned ``Mcp-Session-Id`` header is echoed on later requests.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._session_id: str | None = None
        self._next_id = 1
        self._initialized = False

    # -- plumbing ---------------------------------------------------------- #
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.config.mcp_token:
            headers["Authorization"] = f"Bearer {self.config.mcp_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False):
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = self._next_id
            self._next_id += 1

        req = urllib.request.Request(
            self.config.mcp_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.mcp_timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                body = resp.read().decode("utf-8", "replace")
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise MCPError(f"MCP HTTP {exc.code} on {method}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"MCP connection failed on {method}: {exc.reason}") from exc

        if notify:
            return None
        return self._parse(body, ctype)

    @staticmethod
    def _parse(body: str, ctype: str) -> dict:
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
            raise MCPError(f"no JSON payload in SSE response: {body[:200]}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MCPError(f"invalid JSON from MCP: {body[:200]}") from exc

    def _result(self, method: str, params: dict | None = None) -> dict:
        """Issue a request, raise on a JSON-RPC error, return its ``result``."""
        res = self._rpc(method, params)
        if res is None:
            raise MCPError(f"no response for {method}")
        if "error" in res:
            err = res["error"]
            message = err.get("message", err) if isinstance(err, dict) else err
            raise MCPError(f"{method}: {message}")
        return res.get("result", {}) or {}

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._result(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "weekly-product-pulse", "version": "1.0"},
            },
        )
        self._rpc("notifications/initialized", notify=True)
        self._initialized = True

    @property
    def session_id(self) -> str | None:
        """The `Mcp-Session-Id` captured from the handshake, if any."""
        return self._session_id

    # -- public ------------------------------------------------------------ #
    def list_tools(self) -> list[str]:
        """Initialize (once) and return the server's tool names."""
        self._ensure_initialized()
        result = self._result("tools/list")
        names: list[str] = []
        for tool in result.get("tools", []) or []:
            if isinstance(tool, dict) and tool.get("name"):
                names.append(str(tool["name"]))
        return names

    def call_tool(self, name: str, arguments: dict) -> dict:
        self._ensure_initialized()
        result = self._result("tools/call", {"name": name, "arguments": arguments})
        text = _extract_text(result)
        # Destination may be empty (e.g. Google Docs returns no URL); publish()
        # resolves it per outcome and applies a config-aware fallback.
        return {
            "destination": _guess_destination(result, text),
            "detail": text[:300],
            "result": result,
        }


def _extract_text(result: dict) -> str:
    parts: list[str] = []
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    if not parts and isinstance(result.get("structuredContent"), dict):
        parts.append(json.dumps(result["structuredContent"]))
    return "\n".join(parts).strip()


def _iter_dicts(value) -> list[dict]:
    """Flatten a parsed JSON value into the dicts nested inside it.

    n8n returns tool output as a JSON **array** (``[{"documentId": ...}]``) while
    other servers return a bare object; both shapes must be walkable.
    """
    found: list[dict] = []
    if isinstance(value, dict):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_iter_dicts(item))
    return found


def _candidate_dicts(result: dict) -> list[dict]:
    """Objects we can read a destination from.

    MCP servers return either `structuredContent` or a text block containing
    JSON (n8n commonly does the latter). Both an object and an array payload are
    considered.
    """
    candidates: list[dict] = _iter_dicts(result.get("structuredContent"))
    for block in result.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            raw = str(block.get("text", "")).strip()
            if raw.startswith(("{", "[")):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                candidates.extend(_iter_dicts(parsed))
    return candidates


# Keys that name a link, most explicit first; then keys that name an identifier.
_DEST_URL_KEYS = ("url", "link", "permalink", "webViewLink")
_DEST_ID_KEYS = (
    "documentId", "document_id", "draftId", "draft_id",
    "messageId", "message_id", "id",
)


def _guess_destination(result: dict, text: str) -> str:
    candidates = _candidate_dicts(result)
    for candidate in candidates:
        for key in _DEST_URL_KEYS:
            value = candidate.get(key)
            if value and str(value).startswith(("http://", "https://")):
                return str(value)
    for candidate in candidates:
        for key in _DEST_ID_KEYS:
            if candidate.get(key):
                return str(candidate[key])
    for candidate in candidates:  # e.g. {"message": {"id": "..."}}
        for value in candidate.values():
            if isinstance(value, dict) and value.get("id"):
                return str(value["id"])
    for token in text.split():
        if token.startswith(("http://", "https://")):
            return token.strip("\"'.,)")
    return ""


def _find_doc_id(result: dict) -> str:
    """The Google Doc id a note write touched, if the server reported one."""
    for candidate in _candidate_dicts(result):
        for key in ("documentId", "document_id"):
            if candidate.get(key):
                return str(candidate[key])
    return ""


def _find_message_id(result: dict) -> str:
    """The Gmail message id behind a created draft, if the server reported one."""
    for candidate in _candidate_dicts(result):
        message = candidate.get("message")
        if isinstance(message, dict) and message.get("id"):
            return str(message["id"])
        for key in ("messageId", "message_id"):
            if candidate.get(key):
                return str(candidate[key])
    return ""


def _resolve_destination(
    outcome: str, tool: str, res: dict, config: Config, run_id: str
) -> str:
    """Turn a tool's raw JSON reply into the most useful destination string.

    - ``append_note``: prefer the ``documentId`` the server returned and render a
      canonical Docs URL; fall back to the configured ``PULSE_MCP_NOTE_TARGET``.
    - ``create_email_draft``: prefer a Gmail deep link to the created draft using
      the returned message id; otherwise use whatever identifier came back.

    ``res`` carries the client's best guess under ``"destination"`` and, for the
    live client, the parsed JSON-RPC ``"result"`` under ``"result"``.
    """
    result = res.get("result")
    raw = str(res.get("destination") or "")

    if isinstance(result, dict):
        if outcome == "append_note":
            url = _gdoc_url(_find_doc_id(result))
            if url:
                return url
        else:
            message_id = _find_message_id(result)
            if message_id:
                return f"https://mail.google.com/mail/u/0/#drafts/{message_id}"

    if outcome == "append_note":
        url = _gdoc_url(raw)
        if url:
            return url
    if raw:
        return raw
    return _fallback_destination(outcome, tool, config, run_id)


_GDOC_ID_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")
_GDOC_BARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,}")


def _gdoc_url(target: str) -> str:
    """Canonical Google Docs URL from a doc id or any Docs URL (else ``""``).

    Google Docs' ``document:update`` returns no URL of its own, so with a fixed
    notes document the destination is derived from ``PULSE_MCP_NOTE_TARGET``.
    """
    target = (target or "").strip()
    if not target:
        return ""
    match = _GDOC_ID_RE.search(target)
    if match:
        return f"https://docs.google.com/document/d/{match.group(1)}/edit"
    if "docs.google.com" in target:
        return target
    if _GDOC_BARE_ID_RE.fullmatch(target):
        return f"https://docs.google.com/document/d/{target}/edit"
    return ""


def _fallback_destination(outcome: str, tool: str, config: Config, run_id: str) -> str:
    """Config-aware destination when a tool call returns no identifier.

    For the note outcome, a configured Google Doc target yields a real URL;
    otherwise we fall back to a scheme that records the tool + run (as before).
    """
    if outcome == "append_note":
        doc = _gdoc_url(config.mcp_note_target)
        if doc:
            return doc
    return f"{tool}://{run_id}"


def get_mcp_client(config: Config) -> MCPClient:
    if config.mcp_transport == "http" and config.mcp_endpoint:
        return N8NMCPClient(config)
    return MockMCPClient(config)


# --------------------------------------------------------------------------- #
# Outcome payloads
# --------------------------------------------------------------------------- #
def _compose_packet(pulse: ProductPulse, fee_explainer: FeeExplainer | None) -> str:
    from .fee_explainer import render_fee_explainer

    sections = [pulse.text.strip()]
    if fee_explainer is not None:
        sections.append("---")
        sections.append(render_fee_explainer(fee_explainer).strip())
    return "\n\n".join(sections)


def _note_arguments(pulse: ProductPulse, fee_explainer: FeeExplainer | None, config: Config, run_id: str) -> dict:
    args = {
        "title": f"Weekly Product Pulse — {run_id}",
        "content": _compose_packet(pulse, fee_explainer),
        "run_id": run_id,
    }
    if config.mcp_note_target:
        args["target"] = config.mcp_note_target
    return args


def _email_arguments(pulse: ProductPulse, fee_explainer: FeeExplainer | None, config: Config, run_id: str) -> dict:
    args = {
        "subject": f"{config.mcp_email_subject} — {run_id}",
        "body": _compose_packet(pulse, fee_explainer),
        "run_id": run_id,
    }
    if config.mcp_email_to:
        args["to"] = config.mcp_email_to
    return args


# --------------------------------------------------------------------------- #
# Publish
# --------------------------------------------------------------------------- #
def _call_with_retry(client: MCPClient, tool: str, args: dict, config: Config) -> dict:
    """Call a tool with bounded exponential backoff (architecture §8).

    Repeats are safe because every call carries ``run_id``, the documented
    idempotency key for the n8n side. After the final attempt the last
    ``MCPError`` propagates so the caller records ``outcome="error"``.
    """
    attempts = max(1, config.mcp_max_retries + 1)
    last: MCPError | None = None
    for attempt in range(attempts):
        try:
            return client.call_tool(tool, args)
        except MCPError as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(max(0.0, config.mcp_backoff_seconds) * (2 ** attempt))
    raise last if last is not None else MCPError(
        f"tool {tool!r} failed after {attempts} attempt(s)"
    )


def publish(
    state: ApprovalState,
    pulse: ProductPulse,
    fee_explainer: FeeExplainer | None,
    config: Config,
    run_id: str,
) -> list[PublishRecord]:
    if state != "approved":
        return []  # hard gate: no approval, no write

    client = get_mcp_client(config)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[PublishRecord] = []

    outcomes = [
        ("append_note", config.mcp_tool_append_note, "pulse",
         _note_arguments(pulse, fee_explainer, config, run_id)),
        ("create_email_draft", config.mcp_tool_email_draft, "fee_explainer",
         _email_arguments(pulse, fee_explainer, config, run_id)),
    ]

    for outcome, tool, artifact, args in outcomes:
        try:
            res = _call_with_retry(client, tool, args, config)
            destination = _resolve_destination(outcome, tool, res, config, run_id)
            records.append(
                PublishRecord(
                    tool=tool,
                    artifact=artifact,  # type: ignore[arg-type]
                    destination=destination,
                    published_at=now,
                    outcome=outcome,
                    detail=res.get("detail", ""),
                )
            )
        except MCPError as exc:
            # Surface but do not silently bypass; the other outcome may still land.
            records.append(
                PublishRecord(
                    tool=tool,
                    artifact=artifact,  # type: ignore[arg-type]
                    destination="",
                    published_at=now,
                    outcome="error",
                    detail=str(exc),
                )
            )
    return records
