"""Tests for scripts/check_n8n_workflow.py (static n8n workflow contract check).

Run from the repo root:  python -m unittest discover -s tests
No third-party dependencies; no network access.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_n8n_workflow.py")

_spec = importlib.util.spec_from_file_location("check_n8n_workflow", SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _node(name, type_, type_version=1, params=None, node_id=None, credentials=None):
    node = {
        "parameters": params or {},
        "type": type_,
        "typeVersion": type_version,
        "position": [0, 0],
        "id": node_id or f"id-{name}",
        "name": name,
    }
    if credentials is not None:
        node["credentials"] = credentials
    return node


def _valid_workflow():
    """A minimal workflow that satisfies the Stage G contract."""
    return {
        "name": "test",
        "nodes": [
            _node(
                "MCP Server Trigger",
                "@n8n/n8n-nodes-langchain.mcpTrigger",
                2.1,
                {"authentication": "bearerAuth", "path": "weekly-pulse", "options": {}},
            ),
            _node(
                "append_to_note",
                "n8n-nodes-base.googleDocs",
                2,
                {
                    "operation": "update",
                    "title": "={{ $fromAI('title') }}",
                    "actionsUi": {
                        "actionFields": [
                            {"action": "insert", "content": "={{ $fromAI('content') }}"}
                        ]
                    },
                },
                credentials={
                    "googleDocsOAuth2Api": {"id": "c1", "name": "Google Docs account"}
                },
            ),
            _node(
                "create_email_draft",
                "n8n-nodes-base.gmail",
                2.1,
                {
                    "resource": "draft",
                    "subject": "={{ $fromAI('subject') }}",
                    "message": "={{ $fromAI('body') }}",
                },
                credentials={"gmailOAuth2": {"id": "c2", "name": "Gmail account"}},
            ),
        ],
        "connections": {
            "MCP Server Trigger": {
                "ai_tool": [
                    [
                        {"node": "append_to_note", "type": "ai_tool", "index": 0},
                        {"node": "create_email_draft", "type": "ai_tool", "index": 0},
                    ]
                ]
            }
        },
    }


def _run(workflow):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "workflow.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(workflow, handle)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = checker.main([path])
    return code, buf.getvalue()


class TestN8nWorkflowCheck(unittest.TestCase):
    def test_valid_minimal_workflow_passes(self):
        code, out = _run(_valid_workflow())
        self.assertEqual(code, 0, out)
        self.assertIn("contract satisfied", out)

    def test_missing_tool_node_fails(self):
        wf = _valid_workflow()
        wf["nodes"] = [n for n in wf["nodes"] if n["name"] != "append_to_note"]
        code, out = _run(wf)
        self.assertEqual(code, 1, out)
        self.assertIn("append_to_note", out)

    def test_missing_fromai_arg_fails(self):
        wf = _valid_workflow()
        for node in wf["nodes"]:
            if node["name"] == "append_to_note":
                node["parameters"]["actionsUi"] = {
                    "actionFields": [{"action": "insert", "content": "static text"}]
                }
        code, out = _run(wf)
        self.assertEqual(code, 1, out)
        self.assertIn("content", out)

    def test_wrong_authentication_fails(self):
        wf = _valid_workflow()
        for node in wf["nodes"]:
            if node["name"] == "MCP Server Trigger":
                node["parameters"]["authentication"] = "none"
        code, out = _run(wf)
        self.assertEqual(code, 1, out)

    def test_missing_credentials_is_advisory(self):
        wf = _valid_workflow()
        for node in wf["nodes"]:
            node.pop("credentials", None)
        code, out = _run(wf)
        self.assertEqual(code, 0, out)
        self.assertIn("[warn]", out)

    def test_webhook_profile_requires_original_flow(self):
        wf = _valid_workflow()
        wf["nodes"].append(
            _node("Webhook", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": "x"})
        )
        code, out = _run(wf)
        self.assertEqual(code, 1, out)
        self.assertIn("missing original edge", out)

    def test_shipped_v2_workflow_is_valid(self):
        path = os.path.join(ROOT, "docs", "mcp_workflow_v2.json")
        if not os.path.exists(path):
            self.skipTest("docs/mcp_workflow_v2.json not present")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = checker.main([path])
        self.assertEqual(code, 0, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
