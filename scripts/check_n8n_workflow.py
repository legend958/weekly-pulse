"""Static validation of an n8n workflow JSON against the pipeline's Stage G contract.

Checks:
  1. valid JSON, unique node ids/names
  2. MCP Server Trigger present (mcpTrigger) with bearerAuth + a path
  3. tool nodes named exactly `append_to_note` / `create_email_draft`
  4. those nodes wired from the trigger via `ai_tool`
  5. tool node types + the `$fromAI(...)` bindings the pipeline actually sends
  6. credentials attached on both tool nodes (advisory)
  7. if the workflow also has a Webhook node (the "client workflow" profile), the
     original Webhook -> Wait -> Docs/Gmail flow must still be intact

Run:  python scripts/check_n8n_workflow.py [path-to-workflow.json]
Exit 0 = no hard problems; 1 = a hard problem was found.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tool names and arguments the pipeline uses (src/pulse/config.py + publish_mcp.py)
DEFAULT_NOTE_TOOL = "append_to_note"
DEFAULT_EMAIL_TOOL = "create_email_draft"
NOTE_ARGS = {"title", "content"}
EMAIL_ARGS = {"subject", "body"}

EXPECTED_PATH = "weekly-pulse"
EXPECTED_FLOW = [
    ("Webhook", "main", "Wait for Approval (MCP Gate)"),
    ("Wait for Approval (MCP Gate)", "main", "Google Docs (Log Entry)"),
    ("Wait for Approval (MCP Gate)", "main", "Gmail (Create Draft)"),
]

_problems = 0


def fail(msg: str) -> None:
    global _problems
    _problems += 1
    print(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    print(f"  [warn] {msg}")


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def fromai_keys(text: str) -> set[str]:
    return set(re.findall(r"\$fromAI\('([^']+)'", text or ""))


def walk_strings(node) -> list[str]:
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            out += walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            out += walk_strings(v)
    return out


def main(argv=None) -> int:
    global _problems
    _problems = 0
    argv = argv or sys.argv[1:]
    path = argv[0] if argv else os.path.join(ROOT, "docs", "mcp_workflow_v2.json")
    print(f"checking: {path}\n")

    try:
        with open(path, encoding="utf-8") as f:
            wf = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot load JSON: {exc}")
        return 1

    nodes = wf.get("nodes", [])
    ids = [n.get("id") for n in nodes]
    names = [n.get("name") for n in nodes]
    by = {n.get("name"): n for n in nodes}

    # 1. structure ---------------------------------------------------------- #
    if len(set(ids)) == len(ids) and len(set(names)) == len(names):
        ok(f"{len(nodes)} nodes, ids and names unique")
    else:
        fail("duplicate node ids or names")

    # 2. MCP trigger --------------------------------------------------------- #
    trigger = by.get("MCP Server Trigger")
    if trigger is None:
        fail("no node named 'MCP Server Trigger'")
    else:
        if trigger.get("type") == "@n8n/n8n-nodes-langchain.mcpTrigger":
            ok(f"MCP trigger type {trigger.get('type')} v{trigger.get('typeVersion')}")
        else:
            fail(f"trigger type is {trigger.get('type')!r}, "
                 "expected @n8n/n8n-nodes-langchain.mcpTrigger")
        params = trigger.get("parameters", {})
        if params.get("authentication") == "bearerAuth":
            ok("authentication = bearerAuth")
        else:
            fail(f"trigger authentication is {params.get('authentication')!r}")
        path_val = params.get("path", "")
        if not path_val:
            fail("trigger has no path")
        elif path_val == EXPECTED_PATH:
            ok(f"path = /mcp/{path_val} (production URL)")
        else:
            warn(f"path = /mcp/{path_val} (docs assume '{EXPECTED_PATH}')")

    # 3. tool nodes ---------------------------------------------------------- #
    note_tool = by.get(DEFAULT_NOTE_TOOL)
    email_tool = by.get(DEFAULT_EMAIL_TOOL)
    for label, node, expected_type in (
        (DEFAULT_NOTE_TOOL, note_tool, "n8n-nodes-base.googleDocs"),
        (DEFAULT_EMAIL_TOOL, email_tool, "n8n-nodes-base.gmail"),
    ):
        if node is None:
            fail(f"no node named {label!r}")
        elif node.get("type") != expected_type:
            fail(f"{label!r} is {node.get('type')!r}, expected {expected_type}")
        else:
            ok(f"{label!r} -> {expected_type} v{node.get('typeVersion')}")

    # 4. ai_tool wiring ------------------------------------------------------ #
    tools = [
        t["node"]
        for br in wf.get("connections", {}).get("MCP Server Trigger", {}).get("ai_tool", [])
        for t in br
    ]
    if DEFAULT_NOTE_TOOL in tools and DEFAULT_EMAIL_TOOL in tools:
        ok(f"MCP trigger exposes tools via ai_tool: {tools}")
    else:
        fail(f"MCP trigger ai_tool connections are {tools!r}")

    # 5. $fromAI bindings ---------------------------------------------------- #
    for label, node, expected in (
        (DEFAULT_NOTE_TOOL, note_tool, NOTE_ARGS),
        (DEFAULT_EMAIL_TOOL, email_tool, EMAIL_ARGS),
    ):
        if node is None:
            continue
        keys: set[str] = set()
        for s in walk_strings(node.get("parameters", {})):
            keys |= fromai_keys(s)
        if expected <= keys:
            ok(f"{label} reads $fromAI args {sorted(keys)}")
        else:
            fail(f"{label} missing $fromAI args {sorted(expected - keys)} "
                 f"(pipeline sends {sorted(expected)})")

    # 6. credentials (advisory) ---------------------------------------------- #
    for label, node in ((DEFAULT_NOTE_TOOL, note_tool), (DEFAULT_EMAIL_TOOL, email_tool)):
        if node is None:
            continue
        creds = node.get("credentials") or {}
        if creds:
            for ctype, cval in creds.items():
                ok(f"{label} credential: {cval.get('name')!r} "
                   f"({ctype}, id {cval.get('id')})")
        else:
            warn(f"{label} has no credential in the JSON - attach it in the n8n UI")

    placeholders = sorted({
        m
        for n in nodes
        for s in walk_strings(n.get("parameters", {}))
        for m in re.findall(r"REPLACE_WITH_[A-Z0-9_]+", s)
    })
    if placeholders:
        warn(f"placeholder(s) left to fill in the UI: {placeholders}")

    # 7. client-workflow profile: original flow must survive ----------------- #
    if "Webhook" in by:
        actual = {
            (src, kind, tgt["node"])
            for src, spec in wf.get("connections", {}).items()
            for kind, branches in spec.items()
            for br in branches
            for tgt in br
        }
        for edge in EXPECTED_FLOW:
            if edge in actual:
                ok(f"original flow: {edge[0]} --{edge[1]}--> {edge[2]}")
            else:
                fail(f"missing original edge {edge[0]} -> {edge[2]}")

    print()
    if _problems:
        print(f"RESULT: {_problems} problem(s) - fix before importing")
        return 1
    print("RESULT: contract satisfied - importable")
    print("Note: the trigger's Bearer credential is attached in the n8n UI, so it")
    print("cannot be validated from the file. Verify it with:")
    print("  python scripts/check_n8n_endpoint.py --endpoint <url> --token <token>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
