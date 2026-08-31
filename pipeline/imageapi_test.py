#!/usr/bin/env python3
"""Unit tests for the OpenAI-compatible client (no outbound network, no spend).

The chat() tests run against a throwaway http.server on localhost, so the request
body and the response parsing are both exercised for real rather than mocked.

Run directly so pipeline/ is on sys.path for `import imageapi`:
    python3 pipeline/imageapi_test.py
"""
import base64
import json
import os
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import imageapi

PNG_BYTES = b"\x89PNG\r\n\x1a\n-not-a-real-png-"


class _Handler(BaseHTTPRequestHandler):
    """Replies with whatever the test parked on the server, and records the
    request so the test can assert the wire format."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.server.last_path = self.path
        self.server.last_headers = dict(self.headers)
        self.server.last_body = json.loads(self.rfile.read(length) or b"{}")
        status, payload, headers = self.server.replies.pop(0)
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class ServedChat(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.replies = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def reply(self, payload, status=200, headers=None):
        self.srv.replies.append((status, payload, headers or {}))

    def image_reply(self, data=PNG_BYTES, mime="image/png"):
        b64 = base64.b64encode(data).decode()
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "",
            "images": [{"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}}],
        }}]}

    def test_posts_to_chat_completions_with_bearer_auth(self):
        self.reply(self.image_reply())
        imageapi.chat(self.url, "k-123", "some/model",
                      [imageapi.text_part("draw a bird")], want_image=True)
        self.assertEqual(self.srv.last_path, "/v1/chat/completions")
        self.assertEqual(self.srv.last_headers["Authorization"], "Bearer k-123")

    def test_request_body_shape(self):
        self.reply(self.image_reply())
        parts = [
            imageapi.text_part("prompt body"),
            imageapi.text_part("IMAGE 1 (positive, target species):"),
            imageapi.image_part(b"jpegbytes", "image/jpeg"),
        ]
        imageapi.chat(self.url, "k", "some/model", parts, want_image=True)
        body = self.srv.last_body
        self.assertEqual(body["model"], "some/model")
        self.assertEqual(body["modalities"], ["image", "text"])
        content = body["messages"][0]["content"]
        # The captioned ordering is the whole reason for chat/completions.
        self.assertEqual([c["type"] for c in content],
                         ["text", "text", "image_url"])
        self.assertEqual(content[1]["text"], "IMAGE 1 (positive, target species):")
        self.assertEqual(content[2]["image_url"]["url"],
                         "data:image/jpeg;base64," + base64.b64encode(b"jpegbytes").decode())

    def test_no_modalities_for_a_text_call(self):
        self.reply({"choices": [{"message": {"content": "hello"}}]})
        resp = imageapi.chat(self.url, "k", "m", [imageapi.text_part("hi")])
        self.assertNotIn("modalities", self.srv.last_body)
        self.assertEqual(imageapi.first_text(resp), "hello")

    def test_image_round_trip(self):
        self.reply(self.image_reply())
        resp = imageapi.chat(self.url, "k", "m", [imageapi.text_part("x")], want_image=True)
        self.assertEqual(imageapi.first_image(resp), PNG_BYTES)

    def test_retries_a_429_then_succeeds(self):
        # Retry-After: 0 so the test doesn't sit through the 4s backoff — which
        # also covers that the header is honoured over the backoff.
        self.reply({"error": {"message": "slow down"}}, status=429,
                   headers={"Retry-After": "0"})
        self.reply(self.image_reply())
        resp = imageapi.chat(self.url, "k", "m", [imageapi.text_part("x")], want_image=True)
        self.assertEqual(imageapi.first_image(resp), PNG_BYTES)

    def test_non_retryable_status_raises_with_the_body_message(self):
        self.reply({"error": {"message": "No endpoints found for that model"}}, status=404)
        with self.assertRaises(RuntimeError) as ctx:
            imageapi.chat(self.url, "k", "m", [imageapi.text_part("x")], want_image=True)
        self.assertIn("No endpoints found", str(ctx.exception))


class FirstImage(unittest.TestCase):
    def data_url(self, data=PNG_BYTES):
        return "data:image/png;base64," + base64.b64encode(data).decode()

    def test_reads_message_images(self):
        resp = {"choices": [{"message": {"images": [
            {"image_url": {"url": self.data_url()}}]}}]}
        self.assertEqual(imageapi.first_image(resp), PNG_BYTES)

    def test_falls_back_to_content_parts(self):
        resp = {"choices": [{"message": {"content": [
            {"type": "text", "text": "here you go"},
            {"type": "image_url", "image_url": {"url": self.data_url()}},
        ]}}]}
        self.assertEqual(imageapi.first_image(resp), PNG_BYTES)

    def test_skips_an_unusable_entry_and_takes_the_next(self):
        resp = {"choices": [{"message": {"images": [
            {"image_url": {}},
            {"image_url": {"url": self.data_url()}},
        ]}}]}
        self.assertEqual(imageapi.first_image(resp), PNG_BYTES)

    def test_a_text_only_reply_reports_the_refusal(self):
        resp = {"choices": [{"finish_reason": "stop", "message": {
            "content": "I can't draw that."}}]}
        with self.assertRaises(RuntimeError) as ctx:
            imageapi.first_image(resp)
        msg = str(ctx.exception)
        self.assertIn("no image", msg)
        self.assertIn("I can't draw that.", msg)

    def test_an_in_band_error_is_surfaced(self):
        resp = {"error": {"message": "model does not support image output"},
                "choices": [{"finish_reason": "error", "message": {}}]}
        with self.assertRaises(RuntimeError) as ctx:
            imageapi.first_image(resp)
        self.assertIn("does not support image output", str(ctx.exception))

    def test_an_empty_response_does_not_crash(self):
        with self.assertRaises(RuntimeError):
            imageapi.first_image({})


class FirstText(unittest.TestCase):
    def test_plain_string_content(self):
        self.assertEqual(
            imageapi.first_text({"choices": [{"message": {"content": " {\"a\": 1} "}}]}),
            '{"a": 1}')

    def test_joins_text_parts(self):
        resp = {"choices": [{"message": {"content": [
            {"type": "text", "text": "one"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            {"type": "text", "text": "two"},
        ]}}]}
        self.assertEqual(imageapi.first_text(resp), "one\ntwo")

    def test_missing_content_is_empty(self):
        self.assertEqual(imageapi.first_text({}), "")


class Resolve(unittest.TestCase):
    def setUp(self):
        for name in (imageapi.ENV_API_KEY, imageapi.ENV_API_URL,
                     imageapi.ENV_MODEL, imageapi.LEGACY_ENV_API_KEY):
            self.addCleanup(_restore_env, name, os.environ.get(name))
            os.environ.pop(name, None)

    def test_flags_win_over_environment(self):
        os.environ[imageapi.ENV_API_KEY] = "env-key"
        os.environ[imageapi.ENV_MODEL] = "env/model"
        url, key, model = imageapi.resolve("flag-key", "http://local/v1", "flag/model", "d/m")
        self.assertEqual((url, key, model), ("http://local/v1", "flag-key", "flag/model"))

    def test_environment_then_defaults(self):
        os.environ[imageapi.ENV_API_KEY] = "env-key"
        url, key, model = imageapi.resolve(None, None, None, "default/model")
        self.assertEqual(url, imageapi.DEFAULT_API_URL)
        self.assertEqual(key, "env-key")
        self.assertEqual(model, "default/model")

    def test_trailing_slash_is_trimmed_so_the_path_join_is_clean(self):
        os.environ[imageapi.ENV_API_KEY] = "k"
        url, _, _ = imageapi.resolve(None, "http://local:1234/v1/", None, "d/m")
        self.assertEqual(url, "http://local:1234/v1")

    def test_no_key_at_all(self):
        with self.assertRaises(imageapi.ConfigError) as ctx:
            imageapi.resolve(None, None, None, "d/m")
        self.assertIn(imageapi.ENV_API_KEY, str(ctx.exception))

    def test_a_stale_gemini_key_says_what_to_do(self):
        os.environ[imageapi.LEGACY_ENV_API_KEY] = "AIza-old"
        with self.assertRaises(imageapi.ConfigError) as ctx:
            imageapi.resolve(None, None, None, "d/m")
        msg = str(ctx.exception)
        self.assertIn(imageapi.LEGACY_ENV_API_KEY, msg)
        self.assertIn(imageapi.ENV_API_KEY, msg)


class ErrorDetail(unittest.TestCase):
    def detail(self, body: bytes, code=400):
        import io
        e = urllib.error.HTTPError("http://x", code, "Bad Request", {}, io.BytesIO(body))
        return imageapi.error_detail(e)

    def test_openai_shaped_error(self):
        self.assertIn("bad model",
                      self.detail(b'{"error":{"message":"bad model","code":"x"}}'))

    def test_string_error(self):
        self.assertEqual(self.detail(b'{"error":"rate limited"}'), "rate limited")

    def test_html_body_falls_back_to_a_snippet(self):
        self.assertIn("Gateway", self.detail(b"<html>502 Bad Gateway</html>", code=502))


class MimeFor(unittest.TestCase):
    def test_known_types(self):
        from pathlib import Path
        self.assertEqual(imageapi.mime_for(Path("a.png")), "image/png")
        self.assertEqual(imageapi.mime_for(Path("a.webp")), "image/webp")
        self.assertEqual(imageapi.mime_for(Path("a.jpg")), "image/jpeg")

    def test_unknown_falls_back_to_a_valid_image_type(self):
        from pathlib import Path
        # Never application/octet-stream: that would build an invalid data URL.
        self.assertTrue(imageapi.mime_for(Path("a.bin")).startswith("image/"))


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
