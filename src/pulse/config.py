"""Runtime configuration (see architecture.md §7). Values overridable via env vars.

The CLIs read a local ``.env.local`` secrets file first (``load_env_file``), so
``PULSE_MCP_ENDPOINT`` / ``PULSE_MCP_TOKEN`` do not have to be exported by hand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def load_env_file(path: str = ".env.local") -> dict[str, str]:
    """Import ``KEY=VALUE`` pairs from a local secrets file into the environment.

    A stdlib stand-in for ``python-dotenv`` so the documented ``.env.local`` is
    actually honoured by the CLIs. Accepts ``export`` prefixes, quoted values and
    ``#`` comments. Variables already set in the environment always win, so a
    shell override or a CI secret still applies. Returns the pairs that were set.
    """
    if not path or not os.path.isfile(path):
        return {}
    loaded: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
    return loaded


@dataclass
class Config:
    input_csv: str = "groww-reviews.csv"
    output_dir: str = "output"
    llm_backend: str = "offline"          # offline | gateway | direct
    model_id: str = "deterministic-offline"
    max_themes: int = 5
    pulse_max_words: int = 250
    fee_min_mentions: int = 5
    fee_link_allowlist: list[str] = field(default_factory=lambda: ["groww.in"])
    allow_auto_approve: bool = False
    seed: int = 20261016

    # --- Stage G: n8n MCP server (two outcomes) --------------------------- #
    # transport "mock" keeps the pipeline offline (tests/CI); "http" speaks
    # JSON-RPC to an n8n MCP Server Trigger over streamable HTTP.
    mcp_transport: str = "mock"           # mock | http
    mcp_endpoint: str = ""                # e.g. https://n8n.example.com/mcp/weekly-pulse
    mcp_token: str = ""                   # Bearer token for the n8n MCP endpoint
    mcp_timeout: int = 30
    mcp_max_retries: int = 2              # retries after the first attempt (bounded)
    mcp_backoff_seconds: float = 0.5      # exponential: base * 2**attempt
    mcp_tool_append_note: str = "append_to_note"      # outcome 1
    mcp_tool_email_draft: str = "create_email_draft"  # outcome 2
    mcp_note_target: str = ""             # optional notebook/doc id the note is appended to
    mcp_email_to: str = ""                # draft "To:" (blank = leave for the reviewer)
    mcp_email_subject: str = "Weekly Product Pulse"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            input_csv=_env("PULSE_INPUT_CSV", "groww-reviews.csv"),
            output_dir=_env("PULSE_OUTPUT_DIR", "output"),
            llm_backend=_env("PULSE_LLM_BACKEND", "offline"),
            model_id=_env("PULSE_MODEL_ID", "deterministic-offline"),
            allow_auto_approve=_env("ALLOW_AUTO_APPROVE", "0") == "1",
            mcp_transport=_env("PULSE_MCP_TRANSPORT", "mock"),
            mcp_endpoint=_env("PULSE_MCP_ENDPOINT", ""),
            mcp_token=_env("PULSE_MCP_TOKEN", ""),
            mcp_timeout=int(_env("PULSE_MCP_TIMEOUT", "30")),
            mcp_max_retries=int(_env("PULSE_MCP_MAX_RETRIES", "2")),
            mcp_backoff_seconds=float(_env("PULSE_MCP_BACKOFF_SECONDS", "0.5")),
            mcp_tool_append_note=_env("PULSE_MCP_TOOL_APPEND_NOTE", "append_to_note"),
            mcp_tool_email_draft=_env("PULSE_MCP_TOOL_EMAIL_DRAFT", "create_email_draft"),
            mcp_note_target=_env("PULSE_MCP_NOTE_TARGET", ""),
            mcp_email_to=_env("PULSE_MCP_EMAIL_TO", ""),
            mcp_email_subject=_env("PULSE_MCP_EMAIL_SUBJECT", "Weekly Product Pulse"),
        )
