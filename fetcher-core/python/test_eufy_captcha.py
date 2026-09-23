#!/usr/bin/env python3
"""
Unit tests for eufy._solve_captcha's Gemini API key handling and 429 backoff.

Run with: python3 -m unittest test_eufy_captcha -v
(from fetcher-core/python, with src/ on PYTHONPATH -- see sys.path hack below,
matching how main.py's siblings are imported elsewhere in this package)
"""
import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import eufy  # noqa: E402


class TestSanitizeExc(unittest.TestCase):
    def test_redacts_key_query_param(self):
        msg = "500 Server Error: url: https://example.com/v1beta/models/x:generateContent?key=SECRET123"
        self.assertNotIn("SECRET123", eufy._sanitize_exc(Exception(msg)))
        self.assertIn("REDACTED", eufy._sanitize_exc(Exception(msg)))

    def test_redacts_case_insensitive_and_mid_query(self):
        msg = "error at ...&KEY=abc123&other=1"
        out = eufy._sanitize_exc(Exception(msg))
        self.assertNotIn("abc123", out)

    def test_leaves_unrelated_text_untouched(self):
        msg = "Connection timed out after 120s"
        self.assertEqual(eufy._sanitize_exc(Exception(msg)), msg)


class TestRetryDelay(unittest.TestCase):
    def test_prefers_retry_after_header(self):
        resp = MagicMock()
        resp.headers = {"Retry-After": "42"}
        self.assertEqual(eufy._gemini_retry_delay_seconds(resp), 42.0)

    def test_falls_back_to_retry_delay_body(self):
        resp = MagicMock()
        resp.headers = {}
        resp.json.return_value = {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "23s"},
                ]
            }
        }
        self.assertEqual(eufy._gemini_retry_delay_seconds(resp), 23.0)

    def test_returns_none_when_nothing_present(self):
        resp = MagicMock()
        resp.headers = {}
        resp.json.return_value = {"error": {"details": []}}
        self.assertIsNone(eufy._gemini_retry_delay_seconds(resp))

    def test_returns_none_on_unparseable_body(self):
        resp = MagicMock()
        resp.headers = {}
        resp.json.side_effect = ValueError("not json")
        self.assertIsNone(eufy._gemini_retry_delay_seconds(resp))


class TestSolveCaptcha(unittest.TestCase):
    def setUp(self):
        eufy._gemini_cooldown_until = 0.0
        self._orig_token = eufy.gemini_token
        eufy.gemini_token = "fake-token-for-tests"

    def tearDown(self):
        eufy.gemini_token = self._orig_token
        eufy._gemini_cooldown_until = 0.0

    @patch("eufy.requests.post")
    def test_sends_key_as_header_not_url(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "AB12"}]}}]
        }
        mock_post.return_value = resp

        answer = eufy._solve_captcha("data:image/png;base64,AAAA")

        self.assertEqual(answer, "AB12")
        called_url = mock_post.call_args.args[0]
        called_headers = mock_post.call_args.kwargs.get("headers", {})
        self.assertNotIn("fake-token-for-tests", called_url)
        self.assertNotIn("key=", called_url)
        self.assertEqual(called_headers.get("x-goog-api-key"), "fake-token-for-tests")

    @patch("eufy.requests.post")
    def test_429_sets_cooldown_and_returns_none(self, mock_post):
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"Retry-After": "5"}
        mock_post.return_value = resp

        answer = eufy._solve_captcha("data:image/png;base64,AAAA")

        self.assertIsNone(answer)
        self.assertGreater(eufy._gemini_cooldown_until, time.monotonic())

    @patch("eufy.requests.post")
    def test_skips_call_entirely_during_cooldown(self, mock_post):
        eufy._gemini_cooldown_until = time.monotonic() + 60

        answer = eufy._solve_captcha("data:image/png;base64,AAAA")

        self.assertIsNone(answer)
        mock_post.assert_not_called()

    @patch("eufy.requests.post")
    def test_uses_configurable_model_in_url(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "XY"}]}}]
        }
        mock_post.return_value = resp

        orig_model = eufy.gemini_model
        try:
            eufy.gemini_model = "gemini-custom-model"
            eufy._solve_captcha("data:image/png;base64,AAAA")
            called_url = mock_post.call_args.args[0]
            self.assertIn("gemini-custom-model", called_url)
        finally:
            eufy.gemini_model = orig_model


if __name__ == "__main__":
    unittest.main()
