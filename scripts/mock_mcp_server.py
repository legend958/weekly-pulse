"""Local stand-in for the n8n MCP Server Trigger — a development aid.

It implements the same MCP-over-HTTP handshake the pipeline uses and prints every
`tools/call` payload, so you can see exactly what n8n will receive and mirror it
in the real workflow.

Interactive (leave running, point the pipeline at it):

    python scripts/mock_mcp_server.py --port 8787
    python run_pipeline.py --decision approve ^
        --mcp-transport http ^
        --mcp-endpoint http://127.0.0.1:8787/mcp ^
        --mcp-token dev

One-shot (handles two tool calls then exits; handy for CI or a quick check):

    python scripts/mock_mcp_server.py --port 8787 --once
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STATE = {"calls": 0, "once": False, "server": None}
_BAR = "-" * 72


class MockMCPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console clean
        pass

    # -- helpers ----------------------------------------------------------- #
    def _json(self, obj, code=200, extra=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _empty(self, code=202):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- MCP transport ----------------------------------------------------- #
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}})
            return

        method = payload.get("method")
        if method == "initialize":
            self._json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mock-n8n-mcp", "version": "1.0"},
                    },
                },
                extra={"Mcp-Session-Id": "mock-session"},
            )
        elif method == "notifications/initialized":
            self._empty(202)
        elif method == "tools/call":
            self._handle_tool_call(payload)
        elif method == "tools/list":
            self._json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "tools": [
                            {"name": "append_to_note", "description": "Append the weekly packet to a Notes/Doc"},
                            {"name": "create_email_draft", "description": "Create an email draft (never sends)"},
                        ]
                    },
                }
            )
        else:
            self._json({"jsonrpc": "2.0", "id": payload.get("id"),
                        "error": {"code": -32601, "message": f"method not found: {method}"}})

    def _handle_tool_call(self, payload):
        params = payload.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        auth = self.headers.get("Authorization", "")

        print(_BAR)
        print(f"tools/call  name={name}")
        print(f"  Authorization: {auth or '(none)'}")
        print("  arguments:")
        print("\n".join("    " + line for line in json.dumps(args, indent=2).splitlines()))

        if name == "append_to_note":
            result = {
                "content": [{"type": "text", "text": "Appended to Weekly Pulse notes"}],
                "structuredContent": {"url": "https://notes.example/weekly-pulse/doc-42"},
            }
        elif name == "create_email_draft":
            result = {
                "content": [{"type": "text", "text": "Draft created"}],
                "structuredContent": {"draftId": "draft-7f3a91"},
            }
        else:
            print(f"  !! unknown tool '{name}' -> returning error")
            self._json({"jsonrpc": "2.0", "id": payload.get("id"),
                        "error": {"code": -32602, "message": f"unknown tool: {name}"}})
            return

        self._json({"jsonrpc": "2.0", "id": payload.get("id"), "result": result})
        print(f"  -> {json.dumps(result.get('structuredContent'))}")

        _STATE["calls"] += 1
        if _STATE["once"] and _STATE["calls"] >= 2:
            print(_BAR)
            print("handled 2 tool calls; shutting down (--once)")
            threading.Thread(target=_STATE["server"].shutdown, daemon=True).start()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mock n8n MCP server (dev aid)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--once", action="store_true", help="exit after 2 tool calls")
    args = parser.parse_args(argv)

    _STATE["once"] = args.once
    server = ThreadingHTTPServer((args.host, args.port), MockMCPHandler)
    _STATE["server"] = server
    print(f"mock n8n MCP server listening on http://{args.host}:{args.port}/mcp")
    print("point the pipeline at it with:")
    print(f"  python run_pipeline.py --decision approve --mcp-transport http \\")
    print(f"    --mcp-endpoint http://{args.host}:{args.port}/mcp --mcp-token dev")
    print(_BAR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
