"""Milo local-vision tests: no cloud, no silent capture and no fabricated pixels."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from colibri.milo_core.vision import (
    OllamaLocalVision, VisionError, validate_local_endpoint,
    vision_context,
)


class LocalVisionTests(unittest.TestCase):
    def test_remote_hosts_paths_and_embedded_credentials_are_rejected(self):
        for url in ("https://api.example.com",
                    "http://192.168.0.3:11434",
                    "http://localhost:11434",
                    "http://127.0.0.1:11434/secret",
                    "http://user:pass@127.0.0.1:11434",
                    "http://127.0.0.1:11434?x=y",
                    "file:///private/model"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_local_endpoint(url)
        self.assertEqual(validate_local_endpoint(
            "http://127.0.0.1:11434"), "http://127.0.0.1:11434")

    def test_user_approval_is_mandatory(self):
        backend = OllamaLocalVision(model="qwen3-vl:2b")
        with self.assertRaises(PermissionError):
            backend.analyse([Path("anything.png")], "What is this?")
        with self.assertRaises(VisionError):
            backend.analyse([], "What is this?", approved=True)

    def test_genuine_image_bytes_are_sent_to_local_endpoint_only(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / "screen.png"
            Image.new("RGB", (19, 17), (20, 30, 40)).save(filename)
            captured = []
            def fake_urlopen(request, timeout):
                captured.append((request.full_url, json.loads(request.data), timeout))
                return BytesIO(json.dumps({
                    "message": {"role": "assistant",
                                "content": "The window contains an error dialog."}
                }).encode("utf-8"))
            with mock.patch("colibri.milo_core.vision.urlopen", fake_urlopen):
                findings = OllamaLocalVision(
                    model="qwen3-vl:2b").analyse(
                        [filename], "What error do you see?", approved=True)
            self.assertEqual(len(findings), 1)
            self.assertEqual(captured[0][0], "http://127.0.0.1:11434/api/chat")
            body = captured[0][1]
            self.assertEqual(body["model"], "qwen3-vl:2b")
            self.assertFalse(body["stream"])
            self.assertEqual(body["keep_alive"], 0)
            self.assertIn("What error do you see?", body["messages"][0]["content"])
            self.assertEqual(len(body["messages"][0]["images"]), 1)
            self.assertGreater(len(body["messages"][0]["images"][0]), 50)
            context = vision_context(findings)
            self.assertIn("SEPARATE LOCAL VISION MODEL", context)
            self.assertIn("error dialog", context)

    def test_failure_never_produces_invented_analysis(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / "test.png"
            Image.new("RGB", (8, 8)).save(filename)
            with mock.patch("colibri.milo_core.vision.urlopen",
                            return_value=BytesIO(b'{"message":{"content":""}}')):
                with self.assertRaisesRegex(VisionError, "no valid description"):
                    OllamaLocalVision(model="qwen3-vl:2b").analyse(
                        [filename], "Describe", approved=True)

    def test_reject_symlink_or_non_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "bad.png"
            file.write_bytes(b"not an image")
            with self.assertRaises(VisionError):
                OllamaLocalVision(model="qwen3-vl:2b").analyse(
                    [file], "Describe", approved=True)
            with self.assertRaises(ValueError):
                OllamaLocalVision(model="an invalid model name")

if __name__ == "__main__":
    unittest.main()
