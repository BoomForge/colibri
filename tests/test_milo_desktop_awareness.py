"""Privacy and portability regression tests for Milo desktop awareness."""
from __future__ import annotations

import queue
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from colibri.milo_core.global_hotkey import GlobalQuickChatHotkey
from colibri.milo_core.tool_activity import (
    init_activity, record_activity, list_activity, clear_activity)
from desktop.screen_capture import (
    capture_primary_screen, ScreenCaptureError)


class ActivityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        init_activity(self.db)

    def tearDown(self):
        self.db.close()

    def test_no_arbitrary_or_sensitive_labels(self):
        record_activity(self.db, "web_research", "approved")
        with self.assertRaises(ValueError):
            record_activity(self.db, "search: private user words", "started")
        with self.assertRaises(ValueError):
            record_activity(self.db, "web_research", "stole files")
        self.assertEqual(list_activity(self.db)[0][1:],
                         ("web_research", "approved"))

    def test_bounded_and_clearable(self):
        for _ in range(315):
            record_activity(self.db, "local_preview", "completed")
        count = self.db.execute("SELECT COUNT(*) FROM tool_activity").fetchone()[0]
        self.assertEqual(count, 300)
        clear_activity(self.db)
        self.assertEqual(list_activity(self.db), [])


class ScreenTests(unittest.TestCase):
    def test_non_windows_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ScreenCaptureError):
                capture_primary_screen(Path(folder), platform="linux")

    def test_one_shot_capture_writes_only_local_file(self):
        class StubImage:
            width = 8
            height = 8
            def save(self, path, format=None):
                Path(path).write_bytes(b"minimal synthetic PNG placeholder")
        with tempfile.TemporaryDirectory() as folder:
            grab = mock.Mock(return_value=StubImage())
            path = capture_primary_screen(Path(folder), grabber=grab,
                                          platform="win32")
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix, ".png")
            self.assertEqual(grab.call_count, 1)

    def test_failed_capture_does_not_leave_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ScreenCaptureError):
                capture_primary_screen(Path(folder),
                    grabber=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
                    platform="win32")
            self.assertEqual(list(Path(folder).iterdir()), [])


class HotkeyTests(unittest.TestCase):
    def test_hotkey_is_disabled_on_other_platforms(self):
        hotkey = GlobalQuickChatHotkey(queue.Queue())
        with mock.patch("colibri.milo_core.global_hotkey.sys.platform", "linux"):
            self.assertFalse(hotkey.start())
        hotkey.stop()


if __name__ == "__main__":
    unittest.main()
