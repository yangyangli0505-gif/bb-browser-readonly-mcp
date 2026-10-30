"""Reader contract tests; every CLI call and wait is replaced with a fake."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend


NOTE_ID = "0123456789abcdef01234567"
OTHER_NOTE_ID = "abcdef0123456789abcdef01"
NOTE = {"note_id": NOTE_ID, "title": "测试笔记", "desc": "正文", "images": []}


def envelope(payload, tab="7b50"):
    return {"ok": True, "data": {"result": payload, "tab": tab}}


def ready(data=None):
    return envelope({"status": "ready", "data": dict(NOTE if data is None else data)})


def pending(started=True):
    return envelope({"status": "pending", "started": started, "stage": "note_loading"})


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise AssertionError("Negative sleep")
        self.sleeps.append(seconds)
        self.now += seconds


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        # A pure placeholder makes the generated request independently JSON-decodable.
        self.template = patch.object(Path, "read_text", return_value="__BB_READ_REQUEST__").start()
        self.addCleanup(patch.stopall)
        patch.object(backend.time, "monotonic", side_effect=self.clock.monotonic).start()
        patch.object(backend.time, "sleep", side_effect=self.clock.sleep).start()
        # Fail closed if any test accidentally attempts a real subprocess.
        patch.object(
            backend.subprocess, "run",
            side_effect=AssertionError("Unexpected subprocess: tests must mock the CLI"),
        ).start()

    def test_invalid_inputs_never_call_cli(self):
        invalid = [
            None, "", "  ", "not-a-note", "a" * 23,
            f"http://www.xiaohongshu.com/explore/{NOTE_ID}",
            f"https://example.com/explore/{NOTE_ID}",
            f"https://www.xiaohongshu.com.evil.example/explore/{NOTE_ID}",
            f"https://user:password@www.xiaohongshu.com/explore/{NOTE_ID}",
            f"https://www.xiaohongshu.com:444/explore/{NOTE_ID}",
            f"https://www.xiaohongshu.com:bad/explore/{NOTE_ID}",
            f"https://www.xiaohongshu.com/user/profile/{NOTE_ID}",
            f"https://www.xiaohongshu.com/explore/{NOTE_ID}/extra",
            "file:///tmp/note", "javascript:alert(1)",
        ]
        with patch.object(backend, "_run_bb") as cli:
            for value in invalid:
                with self.subTest(value=value):
                    result = backend.xiaohongshu_note(value)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error_code"], "INVALID_ARGUMENT")
            invalid_mode = backend._read_xiaohongshu(NOTE_ID, "write")
            self.assertEqual(invalid_mode["error_code"], "INVALID_ARGUMENT")
            cli.assert_not_called()
        self.template.assert_not_called()

    def test_ready_note_returns_content_without_polling(self):
        with patch.object(backend, "_run_bb", return_value=ready()) as cli:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"], NOTE)
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_accepted_url_forms_preserve_expected_note(self):
        values = [
            NOTE_ID.upper(),
            f"https://xiaohongshu.com/explore/{NOTE_ID}",
            f"https://www.xiaohongshu.com:443/search_result/{NOTE_ID}/?xsec_token=test",
        ]
        for value in values:
            with self.subTest(value=value), patch.object(backend, "_run_bb", return_value=ready()):
                self.assertTrue(backend.xiaohongshu_note(value)["ok"])

    def test_ready_empty_comments_are_valid(self):
        data = {"note_id": NOTE_ID, "comments": [], "count": 0}
        with patch.object(backend, "_run_bb", return_value=ready(data)) as cli:
            result = backend.xiaohongshu_comments(NOTE_ID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"], data)
        request = json.loads(cli.call_args.args[0][1])
        self.assertEqual(request["mode"], "comments")

    def test_pending_start_then_poll_keeps_one_tab(self):
        responses = [pending(started=False), pending(started=True), ready()]
        with patch.object(backend, "_run_bb", side_effect=responses) as cli:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertTrue(result["ok"])
        commands = [call.args[0] for call in cli.call_args_list]
        requests = [json.loads(command[1]) for command in commands]
        self.assertEqual([request["phase"] for request in requests], ["start", "start", "poll"])
        self.assertIn("--domain", commands[0])
        self.assertNotIn("--tab", commands[0])
        for command in commands[1:]:
            self.assertNotIn("--domain", command)
            self.assertEqual(command[command.index("--tab") + 1], "7b50")
        self.assertEqual(self.clock.sleeps, [backend.XHS_POLL_INTERVAL_S] * 2)

    def test_missing_or_changed_tab_fails_without_switching(self):
        for tab in (None, "", "abcd"):
            with self.subTest(tab=tab):
                changed = ready()
                changed["data"]["tab"] = tab
                with patch.object(backend, "_run_bb", side_effect=[pending(), changed]) as cli:
                    result = backend.xiaohongshu_note(NOTE_ID)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], "BROWSER_UNAVAILABLE")
                self.assertEqual(cli.call_count, 2)

    def test_first_response_missing_tab_is_an_error(self):
        result_without_tab = ready()
        result_without_tab["data"].pop("tab")
        with patch.object(backend, "_run_bb", return_value=result_without_tab) as cli:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "BROWSER_UNAVAILABLE")
        self.assertEqual(cli.call_count, 1)

    def test_nested_page_error_cannot_be_mistaken_for_success(self):
        cases = [
            envelope({"error": "Not logged in"}),
            envelope({"error": {"message": "Not logged in"}}),
            envelope({"status": "ready", "error": "Not logged in", "data": NOTE}),
            {"ok": True, "data": {"error": {"message": "Evaluation failed"}, "tab": "7b50"}},
        ]
        for response in cases:
            with self.subTest(response=response), patch.object(backend, "_run_bb", return_value=response):
                result = backend.xiaohongshu_note(NOTE_ID)
                self.assertFalse(result["ok"])
                self.assertIsNotNone(result.get("error_code"))

    def test_reader_login_error_remains_actionable(self):
        response = envelope({
            "status": "error", "error_code": "LOGIN_REQUIRED", "message": "Not logged in",
        })
        with patch.object(backend, "_run_bb", return_value=response):
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "LOGIN_REQUIRED")
        self.assertTrue(result["requires_manual_login"])

    def test_incomplete_or_wrong_note_never_returns_success(self):
        bad = [
            None,
            {"status": "unknown"},
            {"status": "ready", "data": {**NOTE, "note_id": OTHER_NOTE_ID}},
            {"status": "ready", "data": {"note_id": NOTE_ID, "title": "Only a placeholder"}},
            {"status": "ready", "data": {**NOTE, "desc": None}},
            {"status": "ready", "data": {**NOTE, "images": "wrong type"}},
        ]
        for payload in bad:
            with self.subTest(payload=payload), patch.object(backend, "_run_bb", return_value=envelope(payload)):
                result = backend.xiaohongshu_note(NOTE_ID)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], "INVALID_BB_BROWSER_OUTPUT")

    def test_total_deadline_includes_cli_time_and_poll_sleeps(self):
        started_at = self.clock.now
        deadline = started_at + backend.XHS_READ_TIMEOUT_S
        timeouts = []

        def waiting_cli(arguments, *, timeout_s):
            remaining = deadline - self.clock.now
            self.assertGreater(timeout_s, 0)
            self.assertLessEqual(timeout_s, remaining)
            self.assertLessEqual(timeout_s, backend.XHS_EVAL_TIMEOUT_S)
            timeouts.append(timeout_s)
            # CLI startup/execution consumes wall-clock time too.
            self.clock.now += min(4.0, timeout_s)
            return pending()

        with patch.object(backend, "_run_bb", side_effect=waiting_cli) as cli:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "TIMEOUT")
        self.assertGreater(cli.call_count, 1)
        self.assertLessEqual(self.clock.now, deadline)
        self.assertAlmostEqual(self.clock.now - started_at, backend.XHS_READ_TIMEOUT_S)
        self.assertLess(timeouts[-1], backend.XHS_EVAL_TIMEOUT_S)
        self.assertTrue(all(delay <= backend.XHS_POLL_INTERVAL_S for delay in self.clock.sleeps))

    def test_cli_failure_is_returned_without_retry_loop(self):
        failed = {"ok": False, "error_code": "TIMEOUT", "message": "CLI timeout"}
        with patch.object(backend, "_run_bb", return_value=failed) as cli:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertEqual(result, failed)
        self.assertEqual(cli.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_quoted_url_is_data_not_script(self):
        value = (
            f'https://www.xiaohongshu.com/explore/{NOTE_ID}'
            '?xsec_token=token";globalThis.unwanted=true;//&xsec_source=测试'
        )
        with patch.object(backend, "_run_bb", return_value=ready()) as cli:
            result = backend.xiaohongshu_note(value)
        self.assertTrue(result["ok"])
        # json.loads rejects trailing JavaScript: interpolation must be one JSON value.
        request = json.loads(cli.call_args.args[0][1])
        self.assertEqual(request["note_id_or_url"], value)
        self.assertEqual(request["phase"], "start")
        self.assertEqual(request["mode"], "note")

    def test_eval_requests_json_envelope_needed_for_tab_tracking(self):
        payload = {"status": "ready", "data": NOTE}

        def fake_subprocess(argv, **kwargs):
            # This models the installed CLI: --json retains tab/result metadata;
            # plain eval output prints only the JavaScript value.
            output = {"result": {"result": payload, "tab": "7b50", "seq": 1}} if "--json" in argv else payload
            return subprocess.CompletedProcess(argv, 0, json.dumps(output), "")

        with patch.object(backend, "_resolve_command", return_value=["node", "bb-browser.js"]), \
                patch.object(backend.subprocess, "run", side_effect=fake_subprocess) as process:
            result = backend.xiaohongshu_note(NOTE_ID)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"], NOTE)
        process.assert_called_once()

    def test_daemon_timeout_is_classified_as_timeout(self):
        self.assertEqual(backend._error_code("Daemon request timed out"), ("TIMEOUT", False))


if __name__ == "__main__":
    unittest.main()
