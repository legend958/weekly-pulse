"""Tests for the Weekly Product Pulse pipeline.

Run from the repo root:  python -m unittest discover -s tests
No third-party dependencies; runs fully offline against the real dataset.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from pulse.approval import decide  # noqa: E402
from pulse.cluster import cluster  # noqa: E402
from pulse.config import Config  # noqa: E402
from pulse.fee_explainer import build_fee_explainer  # noqa: E402
from pulse.ingest import EXPECTED_HEADER, ingest  # noqa: E402
from pulse.insights import extract_insights  # noqa: E402
from pulse.models import ProductPulse  # noqa: E402
from pulse.publish_mcp import N8NMCPClient, publish  # noqa: E402
from pulse.pulse import generate_pulse  # noqa: E402
from pulse.utils import word_count  # noqa: E402
from pulse.validators import validate  # noqa: E402


def _config() -> Config:
    c = Config()
    c.input_csv = os.path.join(ROOT, "groww-reviews.csv")
    return c


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = _config()
        cls.reviews, cls.quality = ingest(cls.config)
        cls.theme_set = cluster(cls.reviews, cls.config)
        cls.insights = extract_insights(cls.reviews, cls.theme_set, cls.config)
        cls.pulse = generate_pulse(cls.insights, cls.config)
        cls.fee = build_fee_explainer(cls.insights, cls.config)
        cls.validation = validate(
            cls.reviews, cls.theme_set, cls.insights, cls.pulse, cls.fee, cls.config
        )

    # -- Stage A ----------------------------------------------------------- #
    def test_ingest_schema_and_volume(self):
        self.assertTrue(self.quality.header_ok)
        self.assertGreaterEqual(self.quality.rows, 600)
        self.assertEqual(self.quality.rating_mix and min(self.quality.rating_mix), 1)
        self.assertEqual(max(self.quality.rating_mix), 5)

    def test_ingest_dedupes(self):
        self.assertGreater(self.quality.duplicates_removed, 0)

    # -- Stage B ----------------------------------------------------------- #
    def test_theme_cap(self):
        self.assertLessEqual(len(self.theme_set.themes), 5)
        self.assertGreaterEqual(len(self.theme_set.themes), 3)

    def test_issue_themes_dominate(self):
        # background praise must be smaller than the smallest issue theme
        smallest = min(t.count for t in self.theme_set.themes)
        self.assertLess(self.theme_set.background_praise, smallest)

    # -- Stage C ----------------------------------------------------------- #
    def test_top_three_ranked(self):
        top = self.insights.top_themes
        self.assertEqual(len(top), 3)
        self.assertEqual(top, sorted(top, key=lambda t: t.score, reverse=True))

    def test_quotes_verbatim_and_traceable(self):
        by_id = {r.id: r for r in self.reviews}
        self.assertEqual(len(self.insights.quotes), 3)
        for q in self.insights.quotes:
            self.assertIn(q.review_id, by_id)
            self.assertIn(q.text, by_id[q.review_id].text)
            self.assertTrue(q.url.startswith("https://"))

    def test_fee_confusion_detected(self):
        fee = self.insights.fee_confusion
        self.assertTrue(fee.detected)
        self.assertGreaterEqual(fee.mention_count, 50)
        self.assertTrue(fee.evidence_ids)

    # -- Stage D ----------------------------------------------------------- #
    def test_pulse_word_cap_and_sections(self):
        self.assertLessEqual(word_count(self.pulse.text), 250)
        for key in ("themes", "quotes", "observation", "actions"):
            self.assertTrue(self.pulse.sections.get(key))
        self.assertEqual(len(self.pulse.sections["actions"]), 3)

    def test_fee_explainer_shape(self):
        self.assertIsNotNone(self.fee)
        self.assertEqual(len(self.fee.bullets), 6)
        self.assertEqual(len(self.fee.links), 2)
        self.assertTrue(self.fee.last_checked)
        for link in self.fee.links:
            self.assertTrue(link.startswith("https://groww.in"))

    # -- Stage E ----------------------------------------------------------- #
    def test_validation_passes(self):
        self.assertTrue(self.validation.ok, self.validation.failures)

    # -- Stage F/G --------------------------------------------------------- #
    def test_no_publish_without_approval(self):
        records = publish("pending_approval", self.pulse, self.fee, self.config, "test-run")
        self.assertEqual(records, [])

    def test_publish_when_approved(self):
        records = publish("approved", self.pulse, self.fee, self.config, "test-run")
        self.assertEqual(len(records), 2)
        for rec in records:
            self.assertIn(rec.artifact, ("pulse", "fee_explainer"))
            self.assertTrue(rec.destination)

    def test_publish_two_outcomes(self):
        records = publish("approved", self.pulse, self.fee, self.config, "test-run")
        outcomes = {r.outcome for r in records}
        self.assertEqual(outcomes, {"append_note", "create_email_draft"})
        note = next(r for r in records if r.outcome == "append_note")
        draft = next(r for r in records if r.outcome == "create_email_draft")
        self.assertEqual(note.tool, self.config.mcp_tool_append_note)
        self.assertEqual(draft.tool, self.config.mcp_tool_email_draft)
        self.assertEqual(note.artifact, "pulse")
        self.assertEqual(draft.artifact, "fee_explainer")

    def test_validator_rejects_bad_pulse(self):
        from pulse.models import ProductPulse

        bad = ProductPulse(text="too short", sections={})
        report = validate(self.reviews, self.theme_set, self.insights, bad, self.fee, self.config)
        self.assertFalse(report.ok)
        self.assertTrue(any("AC5" in f for f in report.failures))

    def test_approval_gate_writes_record(self):
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "approval.json")
        state, reviewer = decide(self.config, "approve", self.validation, path)
        self.assertEqual(state, "approved")
        self.assertTrue(os.path.exists(path))

    def test_run_writes_normalized_reviews_artifact(self):
        import tempfile

        from pulse.run_pipeline import run

        out = tempfile.mkdtemp()
        cfg = _config()
        cfg.output_dir = out
        result = run(cfg, "pending", quiet=True)

        path = os.path.join(out, result.run_id, "reviews.normalized.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]

        self.assertEqual(lines[-1]["type"], "quality_report")
        reviews = [ln for ln in lines if ln["type"] == "review"]
        self.assertEqual(len(reviews), result.quality.rows)
        self.assertTrue(all("date" in r and "rating" in r for r in reviews))


class _FakeMCPHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for an n8n MCP Server Trigger (streamable HTTP)."""

    calls: list[dict] = []
    flaky_attempts: int = 0

    def log_message(self, *args):  # silence
        pass

    def _json(self, obj, code=200, extra=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")

        if method == "initialize":
            self._json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "fake-n8n", "version": "1"},
                    },
                },
                extra={"Mcp-Session-Id": "sess-abc"},
            )
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/list":
            self._json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "tools": [
                            {"name": "append_to_note", "description": "append"},
                            {"name": "create_email_draft", "description": "draft"},
                        ]
                    },
                }
            )
        elif method == "tools/call":
            name = payload["params"]["name"]
            type(self).calls.append(
                {
                    "name": name,
                    "args": payload["params"]["arguments"],
                    "auth": self.headers.get("Authorization"),
                    "session": self.headers.get("Mcp-Session-Id"),
                }
            )
            if name == "boom":
                self._json(
                    {"jsonrpc": "2.0", "id": payload.get("id"),
                     "error": {"code": -32000, "message": "node exploded"}}
                )
                return
            if name == "flaky":
                type(self).flaky_attempts += 1
                if type(self).flaky_attempts < 3:
                    self._json(
                        {"jsonrpc": "2.0", "id": payload.get("id"),
                         "error": {"code": -32000, "message": "temporary glitch"}}
                    )
                    return
                self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {"structuredContent": {"url": "https://example.test/flaky"}},
                    }
                )
                return
            if name == "text_only":
                # typical n8n shape: JSON returned inside a text content block
                self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "content": [
                                {"type": "text", "text": '{"url": "https://example.test/text-only"}'}
                            ]
                        },
                    }
                )
                return
            if name == "no_url":
                # Google Docs shape: success with no identifier returned
                self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {"content": [{"type": "text", "text": "batchUpdate ok"}]},
                    }
                )
                return
            if name == "array_doc":
                # n8n Google Docs shape: a JSON *array* inside a text block
                self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": '[{"documentId":"10wnp8Qe3bn5x2JGy1N0kQV7t_7NYJTXxDrHTCzbXk2k"}]',
                                }
                            ]
                        },
                    }
                )
                return
            if name == "array_draft":
                # n8n Gmail shape: created draft id + nested message id
                self._json(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        '[{"id":"r1687169641787871004",'
                                        '"message":{"id":"1a14af4422b2e4ee",'
                                        '"threadId":"1a14af4422b2e4ee",'
                                        '"labelIds":["DRAFT"]}}]'
                                    ),
                                }
                            ]
                        },
                    }
                )
                return
            self._json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": f"ok:{name}"}],
                        "structuredContent": {"url": f"https://example.test/{name}"},
                    },
                }
            )
        else:
            self._json({"jsonrpc": "2.0", "id": payload.get("id"),
                        "error": {"code": -32601, "message": "method not found"}})


class TestN8NMCPClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _FakeMCPHandler.calls = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMCPHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _config(self) -> Config:
        c = Config()
        c.mcp_transport = "http"
        c.mcp_endpoint = self.endpoint
        c.mcp_token = "secret-token"
        return c

    def test_two_outcomes_over_http(self):
        _FakeMCPHandler.calls = []
        client = N8NMCPClient(self._config())
        note = client.call_tool("append_to_note", {"run_id": "r1", "content": "x"})
        draft = client.call_tool("create_email_draft", {"run_id": "r1", "body": "y"})
        self.assertEqual(note["destination"], "https://example.test/append_to_note")
        self.assertEqual(draft["destination"], "https://example.test/create_email_draft")

        names = [c["name"] for c in _FakeMCPHandler.calls]
        self.assertEqual(names, ["append_to_note", "create_email_draft"])
        # bearer token sent, session id captured from initialize and echoed
        self.assertTrue(all(c["auth"] == "Bearer secret-token" for c in _FakeMCPHandler.calls))
        self.assertTrue(all(c["session"] == "sess-abc" for c in _FakeMCPHandler.calls))

    def test_tool_error_is_surfaced(self):
        _FakeMCPHandler.calls = []
        config = self._config()
        config.mcp_backoff_seconds = 0.0  # keep the retry test fast
        config.mcp_tool_append_note = "boom"  # fake server errors on this tool
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r2"
        )
        self.assertEqual(len(records), 2)
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(by_outcome["error"].tool, "boom")
        self.assertIn("node exploded", by_outcome["error"].detail)
        # bounded retry: 1 attempt + 2 retries before giving up
        self.assertEqual(sum(1 for c in _FakeMCPHandler.calls if c["name"] == "boom"), 3)
        # the healthy outcome still landed
        self.assertEqual(by_outcome["create_email_draft"].destination,
                         "https://example.test/create_email_draft")

    def test_retry_with_backoff_recovers(self):
        _FakeMCPHandler.calls = []
        _FakeMCPHandler.flaky_attempts = 0
        config = self._config()
        config.mcp_backoff_seconds = 0.0
        config.mcp_max_retries = 3
        config.mcp_tool_append_note = "flaky"  # fails twice, then succeeds
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r5"
        )
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(by_outcome["append_note"].destination, "https://example.test/flaky")
        self.assertEqual(_FakeMCPHandler.flaky_attempts, 3)

    def test_destination_from_text_json_block(self):
        # n8n often returns JSON as a text block rather than structuredContent
        client = N8NMCPClient(self._config())
        res = client.call_tool("text_only", {"run_id": "r3"})
        self.assertEqual(res["destination"], "https://example.test/text-only")

    def test_destination_from_json_array_text_block(self):
        # n8n wraps tool output in a JSON array: [{"documentId": "..."}]
        client = N8NMCPClient(self._config())
        res = client.call_tool("array_doc", {"run_id": "r8"})
        self.assertEqual(
            res["destination"], "10wnp8Qe3bn5x2JGy1N0kQV7t_7NYJTXxDrHTCzbXk2k"
        )

    def test_note_array_document_id_resolves_to_doc_url(self):
        config = self._config()
        config.mcp_tool_append_note = "array_doc"
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r9"
        )
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(
            by_outcome["append_note"].destination,
            "https://docs.google.com/document/d/"
            "10wnp8Qe3bn5x2JGy1N0kQV7t_7NYJTXxDrHTCzbXk2k/edit",
        )

    def test_email_draft_resolves_to_gmail_link(self):
        config = self._config()
        config.mcp_tool_email_draft = "array_draft"
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r10"
        )
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(
            by_outcome["create_email_draft"].destination,
            "https://mail.google.com/mail/u/0/#drafts/1a14af4422b2e4ee",
        )

    def test_list_tools(self):
        client = N8NMCPClient(self._config())
        self.assertEqual(client.list_tools(), ["append_to_note", "create_email_draft"])
        # initialize ran once and the session id was captured
        self.assertEqual(client.session_id, "sess-abc")

    def test_mock_client_lists_configured_tools(self):
        from pulse.publish_mcp import MockMCPClient

        names = MockMCPClient(Config()).list_tools()
        self.assertEqual(names, ["append_to_note", "create_email_draft"])

    def test_note_destination_falls_back_to_gdoc_url(self):
        config = self._config()
        config.mcp_tool_append_note = "no_url"  # returns no identifier
        config.mcp_note_target = (
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
        )
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r6"
        )
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(
            by_outcome["append_note"].destination,
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
        )

    def test_note_destination_defaults_without_target(self):
        config = self._config()
        config.mcp_tool_append_note = "no_url"
        config.mcp_note_target = ""
        records = publish(
            "approved", ProductPulse(text="p", sections={}), None, config, "r7"
        )
        by_outcome = {r.outcome: r for r in records}
        self.assertEqual(by_outcome["append_note"].destination, "no_url://r7")

    def test_gdoc_url_normalisation(self):
        from pulse.publish_mcp import _gdoc_url

        self.assertEqual(
            _gdoc_url("1AbCdEfGhIjKlMnOpQrStUvWxYz"),
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
        )
        self.assertEqual(
            _gdoc_url("https://docs.google.com/document/d/ABC123xyz/edit?tab=t.0"),
            "https://docs.google.com/document/d/ABC123xyz/edit",
        )
        self.assertEqual(_gdoc_url(""), "")
        self.assertEqual(_gdoc_url("not an id"), "")

    def test_preflight_script_ok(self):
        import subprocess

        script = os.path.join(ROOT, "scripts", "check_n8n_endpoint.py")
        proc = subprocess.run(
            [sys.executable, script, "--endpoint", self.endpoint, "--token", "secret-token"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)
        self.assertIn("append_to_note", proc.stdout)

    def test_preflight_script_reports_missing_tool(self):
        import subprocess

        script = os.path.join(ROOT, "scripts", "check_n8n_endpoint.py")
        proc = subprocess.run(
            [sys.executable, script, "--endpoint", self.endpoint,
             "--expected", "append_to_note,not_a_tool"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("MISSING", proc.stdout)


class TestEnvFile(unittest.TestCase):
    """`load_env_file` — the stdlib stand-in for python-dotenv."""

    def test_load_env_file_sets_and_respects_existing(self):
        import tempfile

        from pulse.config import load_env_file

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env.local")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "# a comment\n"
                    "\n"
                    "PULSE_TEST_NEW=hello\n"
                    'QUOTED="spaced value"\n'
                    "export EXPORTED=yes\n"
                    "NO_EQUALS_LINE\n"
                )
            os.environ["PULSE_TEST_NEW"] = "already"
            for key in ("PULSE_TEST_NEW", "QUOTED", "EXPORTED"):
                self.addCleanup(os.environ.pop, key, None)

            loaded = load_env_file(path)

            # an existing environment variable wins and is not reported as loaded
            self.assertEqual(os.environ["PULSE_TEST_NEW"], "already")
            self.assertNotIn("PULSE_TEST_NEW", loaded)
            self.assertEqual(loaded["QUOTED"], "spaced value")
            self.assertEqual(os.environ["QUOTED"], "spaced value")
            self.assertEqual(loaded["EXPORTED"], "yes")

    def test_load_env_file_missing_is_noop(self):
        from pulse.config import load_env_file

        self.assertEqual(load_env_file(os.path.join(ROOT, "no-such-file.env")), {})


class TestRepublish(unittest.TestCase):
    """`--mcp-only`: re-publish a saved run without re-analysing the CSV."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        from pulse.run_pipeline import run

        cls.out = tempfile.mkdtemp()
        cls.config = _config()
        cls.config.output_dir = cls.out
        cls.config.mcp_transport = "mock"
        cls.result = run(cls.config, "approve", quiet=True)
        cls.run_dir = os.path.join(cls.out, cls.result.run_id)

    def _republish_config(self) -> Config:
        c = _config()
        c.output_dir = self.out
        c.mcp_transport = "mock"
        return c

    def test_republish_writes_sidecar_and_keeps_original_log(self):
        from pulse.run_pipeline import republish

        records = republish(self.result.run_id, self._republish_config(), quiet=True)
        self.assertEqual(len(records), 2)
        self.assertEqual(
            {r.outcome for r in records}, {"append_note", "create_email_draft"}
        )

        sidecar = os.path.join(self.run_dir, "publish_log.republish.json")
        self.assertTrue(os.path.exists(sidecar))
        with open(sidecar, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)), 2)
        # the first publish stays auditable
        with open(os.path.join(self.run_dir, "publish_log.json"), encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)), 2)

    def test_republish_is_faithful_to_saved_artifacts(self):
        from pulse.run_pipeline import load_run_artifacts

        pulse, fee = load_run_artifacts(self.run_dir, self._republish_config())

        with open(os.path.join(self.run_dir, "pulse.md"), encoding="utf-8") as fh:
            self.assertEqual(pulse.text, fh.read())
        self.assertIsNotNone(fee)
        self.assertEqual(len(fee.bullets), 6)
        self.assertEqual(len(fee.links), 2)
        # the run's own date is preserved, not rewritten to today
        with open(os.path.join(self.run_dir, "fee_explainer.md"), encoding="utf-8") as fh:
            self.assertIn(f"Last checked: {fee.last_checked}", fh.read())

    def test_republish_refuses_unapproved_run(self):
        import shutil

        from pulse.run_pipeline import republish

        rejected = os.path.join(self.out, "rejected-run")
        shutil.copytree(self.run_dir, rejected)
        with open(os.path.join(rejected, "approval.json"), "w", encoding="utf-8") as fh:
            json.dump({"state": "rejected", "validation_ok": True}, fh)

        with self.assertRaises(RuntimeError):
            republish("rejected-run", self._republish_config(), quiet=True)

    def test_republish_unknown_run_raises(self):
        from pulse.run_pipeline import republish

        with self.assertRaises(RuntimeError):
            republish("no-such-run", self._republish_config(), quiet=True)

    def test_latest_run_id_picks_newest_run(self):
        import tempfile

        from pulse.run_pipeline import _latest_run_id

        with tempfile.TemporaryDirectory() as d:
            for name in ("20260101T000000Z-aaa", "20260202T000000Z-bbb"):
                os.makedirs(os.path.join(d, name))
                open(os.path.join(d, name, "approval.json"), "w").close()
            os.makedirs(os.path.join(d, "not-a-run"))  # no approval.json
            self.assertEqual(_latest_run_id(d), "20260202T000000Z-bbb")

    def test_cli_mcp_only_runs_offline(self):
        from pulse.run_pipeline import main

        code = main([
            "--mcp-only", "--from-run", self.result.run_id,
            "--output-dir", self.out, "--mcp-transport", "mock",
            "--env-file", os.path.join(self.out, "no-such.env"), "--quiet",
        ])
        self.assertEqual(code, 0)

    def test_cli_mcp_only_unknown_run_returns_1(self):
        from pulse.run_pipeline import main

        code = main([
            "--mcp-only", "--from-run", "no-such-run",
            "--output-dir", self.out, "--mcp-transport", "mock",
            "--env-file", os.path.join(self.out, "no-such.env"), "--quiet",
        ])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()