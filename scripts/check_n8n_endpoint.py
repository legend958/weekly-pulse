"""Preflight check for a live n8n MCP endpoint (implementation.md Phase 11).

Performs the MCP handshake (``initialize`` -> ``tools/list``) and reports whether
the endpoint is reachable and exposes the two tools Stage G needs. It publishes
nothing and requires no approval, so it is safe to run before a real release.

    python scripts/check_n8n_endpoint.py --endpoint https://<host>/mcp/weekly-pulse --token <bearer>

    # or read PULSE_MCP_ENDPOINT / PULSE_MCP_TOKEN from .env.local / the environment:
    python scripts/check_n8n_endpoint.py

Exit code 0 = reachable and all required tools present; 1 = otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from pulse.config import Config, load_env_file  # noqa: E402
from pulse.publish_mcp import MCPError, N8NMCPClient, get_mcp_client  # noqa: E402

DEFAULT_EXPECTED = ("append_to_note", "create_email_draft")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Preflight an n8n MCP endpoint")
    parser.add_argument("--endpoint", default=None, help="MCP Server Trigger URL")
    parser.add_argument("--token", default=None, help="Bearer token")
    parser.add_argument("--timeout", type=int, default=None, help="seconds")
    parser.add_argument("--expected", default=",".join(DEFAULT_EXPECTED),
                        help="comma-separated required tool names")
    parser.add_argument("--env-file", default=".env.local",
                        help="secrets file read before the environment (default: .env.local)")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    config = Config.from_env()
    if args.endpoint:
        config.mcp_endpoint = args.endpoint
    if args.token:
        config.mcp_token = args.token
    if args.timeout:
        config.mcp_timeout = args.timeout
    if not config.mcp_endpoint:
        print("No endpoint. Pass --endpoint or set PULSE_MCP_ENDPOINT.")
        return 1

    expected = [s.strip() for s in args.expected.split(",") if s.strip()]
    config.mcp_transport = "http"

    print(f"endpoint : {config.mcp_endpoint}")
    print(f"auth     : {'Bearer <set>' if config.mcp_token else 'none'}")
    print(f"timeout  : {config.mcp_timeout}s")

    client = get_mcp_client(config)
    if not isinstance(client, N8NMCPClient):
        print("Refusing to preflight: transport is not http.")
        return 1

    try:
        tools = client.list_tools()
    except MCPError as exc:
        print(f"FAILED   : {exc}")
        print("\nCheck that the workflow is Active and that the URL is the "
              "Production URL (/mcp/<path>, not /mcp-test/<path>).")
        return 1

    print(f"session  : {client.session_id or '(none)'}")
    print(f"tools    : {', '.join(tools) if tools else '(none)'}")

    missing = [name for name in expected if name not in tools]
    if missing:
        print(f"MISSING  : {', '.join(missing)}")
        print("\nRename the tool nodes to match, or pass --expected to override.")
        return 1

    print("\nOK - endpoint is reachable and exposes the required tools.")
    print("Next: python run_pipeline.py --decision approve "
          "--mcp-transport http --mcp-endpoint <url> --mcp-token <token>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
