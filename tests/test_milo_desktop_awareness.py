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


class MemoryCorrectionTests(unittest.TestCase):
    def test_correction_requires_approval_and_preserves_original_provenance(self):
        from colibri.milo_core.store import LocalMemoryStore
        with tempfile.TemporaryDirectory() as folder:
            with LocalMemoryStore(Path(folder) / "private.sqlite") as store:
                note_id = store.add_approved_note(
                    "Original preference", source_kind="owner_approved",
                    source_ref="desktop:initial", approved=True)
                with self.assertRaises(PermissionError):
                    store.revise_note(note_id, "Revised preference")
                self.assertTrue(store.revise_note(
                    note_id, "Revised preference", approved=True))
                note = store.note_provenance(note_id)
                self.assertEqual(note["text"], "Revised preference")
                self.assertEqual(note["source_kind"], "owner_corrected")
                self.assertEqual(note["original_source_kind"], "owner_approved")
                self.assertEqual(note["original_source_ref"], "desktop:initial")
                self.assertEqual(note["revision_count"], 1)
                self.assertFalse(store.revise_note(
                    note_id, "Revised preference", approved=True))
                self.assertEqual(store.find_notes("Revised")[0].id, note_id)
                self.assertTrue(store.delete_note(note_id))
                self.assertEqual(store.db.execute(
                    "SELECT COUNT(*) FROM approved_note_edits").fetchone()[0], 0)


class WindowsActionTests(unittest.TestCase):
    def test_explicit_commands_only(self):
        from colibri.milo_core.windows_actions import parse_action_request
        self.assertEqual(
            parse_action_request("Milo open display settings").action_id,
            "display")
        self.assertEqual(
            parse_action_request("please show bluetooth settings").action_id,
            "bluetooth")
        for text in ("How do I open display settings?",
                     "Could you explain display settings?",
                     "run explorer.exe", "open ms-settings:display"):
            self.assertIsNone(parse_action_request(text))

    def test_exact_action_and_approval_gate(self):
        from colibri.milo_core.windows_actions import (
            plan_action, WindowsAction, execute_action)
        opened = []
        plan = plan_action("sound")
        with self.assertRaises(PermissionError):
            execute_action(plan, platform="win32", launcher=opened.append)
        with self.assertRaises(ValueError):
            execute_action(WindowsAction("sound", "Sound settings",
                                         "file:///bad"), approved=True,
                           platform="win32", launcher=opened.append)
        with self.assertRaises(OSError):
            execute_action(plan, approved=True,
                           platform="linux", launcher=opened.append)
        execute_action(plan, approved=True, platform="win32",
                       launcher=opened.append)
        self.assertEqual(opened, ["ms-settings:sound"])


if __name__ == "__main__":
    unittest.main()
