"""Tests for scripts/setup_n8n_mcp.py (n8n provisioning helpers).

Only the pure/deterministic parts are tested - nothing here touches the network.

Run from the repo root:  python -m unittest discover -s tests
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "setup_n8n_mcp.py")

_spec = importlib.util.spec_from_file_location("setup_n8n_mcp", SCRIPT)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


def _args(**over):
    base = dict(
        workflow_name="Weekly Product Pulse - MCP publish",
        credential_id="pulseMcpBearer1",
        credential_name="Weekly Pulse MCP (Bearer)",
        note_target="https://docs.google.com/document/d/DOC123/edit",
        google_docs_credential_id="77YPwrdD4clfMUnS",
        gmail_credential_id="wPxafhsftdettiAZ",
    )
    base.update(over)
    return argparse.Namespace(**base)


class TestBaseUrl(unittest.TestCase):
    def test_strips_trailing_slash(self):
        self.assertEqual(
            setup.normalise_base_url("https://x.app.n8n.cloud/"),
            "https://x.app.n8n.cloud",
        )

    def test_strips_api_v1_suffix(self):
        self.assertEqual(
            setup.normalise_base_url("https://x.app.n8n.cloud/api/v1"),
            "https://x.app.n8n.cloud",
        )

    def test_keeps_plain_url(self):
        self.assertEqual(
            setup.normalise_base_url("http://localhost:5678"),
            "http://localhost:5678",
        )


class TestWorkflowPayload(unittest.TestCase):
    def setUp(self):
        self.payload = setup.build_workflow_payload(
            "pulseMcpBearer1", "Weekly Pulse MCP (Bearer)", _args()
        )
        self.nodes = {n["name"]: n for n in self.payload["nodes"]}

    def test_required_api_fields_present(self):
        for field in ("name", "nodes", "connections", "settings"):
            self.assertIn(field, self.payload)

    def test_readonly_fields_removed(self):
        # active/tags are read-only in the public API and cause a 400
        self.assertNotIn("active", self.payload)
        self.assertNotIn("tags", self.payload)

    def test_trigger_wired_to_bearer_credential(self):
        trigger = self.nodes["MCP Server Trigger"]
        self.assertEqual(trigger["parameters"]["authentication"], "bearerAuth")
        self.assertEqual(trigger["parameters"]["path"], "weekly-pulse")
        self.assertEqual(
            trigger["credentials"]["httpBearerAuth"]["id"], "pulseMcpBearer1"
        )

    def test_note_tool_gets_doc_and_google_credential(self):
        note = self.nodes["append_to_note"]
        self.assertEqual(
            note["parameters"]["documentURL"],
            "https://docs.google.com/document/d/DOC123/edit",
        )
        self.assertEqual(
            note["credentials"]["googleDocsOAuth2Api"]["id"], "77YPwrdD4clfMUnS"
        )

    def test_email_tool_gets_gmail_credential_and_never_sends(self):
        draft = self.nodes["create_email_draft"]
        self.assertEqual(
            draft["credentials"]["gmailOAuth2"]["id"], "wPxafhsftdettiAZ"
        )
        self.assertEqual(draft["parameters"]["resource"], "draft")
        self.assertEqual(draft["parameters"]["operation"], "create")

    def test_sticky_note_replaced_with_provisioned_summary(self):
        content = self.nodes["Setup notes"]["parameters"]["content"]
        self.assertIn("Provisioned by", content)
        self.assertNotIn("REPLACE_WITH", content)

    def test_source_file_is_not_mutated(self):
        # building twice must give identical payloads (no in-place edits)
        again = setup.build_workflow_payload(
            "pulseMcpBearer1", "Weekly Pulse MCP (Bearer)", _args()
        )
        self.assertEqual(self.payload, again)

    def test_missing_doc_target_leaves_source_placeholder(self):
        payload = setup.build_workflow_payload(
            "c", "n", _args(note_target="")
        )
        note = {n["name"]: n for n in payload["nodes"]}["append_to_note"]
        self.assertEqual(note["parameters"]["documentURL"], "REPLACE_WITH_YOUR_GOOGLE_DOC_ID")


class TestEnvFile(unittest.TestCase):
    def test_load_ignores_comments_and_parses_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env.local")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# comment\n\nA=1\nB = two \nC=\"quoted\"\n")
            data = setup.load_env_file(path)
        self.assertEqual(data, {"A": "1", "B": "two", "C": "quoted"})

    def test_set_replaces_in_place_and_appends_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env.local")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("KEEP=1\nPULSE_MCP_ENDPOINT=old\n")
            setup.set_env_file_value(path, "PULSE_MCP_ENDPOINT", "new")
            setup.set_env_file_value(path, "PULSE_MCP_TRANSPORT", "http")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("KEEP=1", text)
        self.assertIn("PULSE_MCP_ENDPOINT=new", text)
        self.assertNotIn("PULSE_MCP_ENDPOINT=old", text)
        self.assertIn("PULSE_MCP_TRANSPORT=http", text)


if __name__ == "__main__":
    unittest.main()
