"""Regression for deleting one Milo chat without touching other local data.

A separate native Windows smoke test invokes the actual Tk popup command.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.bots import (
    ChatSettings, append_attributed_turn, chat_settings,
    delete_conversation, init_bot_schema, set_chat_settings,
)
from colibri.milo_core.store import LocalMemoryStore


class ConversationDeleteTests(unittest.TestCase):
    def test_deletes_selected_thread_and_provenance_only(self):
        with tempfile.TemporaryDirectory() as folder:
            with LocalMemoryStore(Path(folder) / "milo.sqlite3") as store:
                init_bot_schema(store.db)
                set_chat_settings(store.db, ChatSettings("delete-me"))
                set_chat_settings(store.db, ChatSettings("keep-me"))
                append_attributed_turn(
                    store.db, "delete-me", "Delete this thread",
                    "This response was from Milo", bot_id="milo", bot_name="Milo")
                append_attributed_turn(
                    store.db, "keep-me", "Preserve this thread",
                    "Keep this message", bot_id="milo", bot_name="Milo")
                note_id = store.add_approved_note(
                    "Owner memory remains", approved=True,
                    source_kind="owner_approved", source_ref="test")
                before = store.db.execute(
                    "SELECT COUNT(*) FROM assistant_provenance").fetchone()[0]
                self.assertEqual(before, 2)
                deleted = delete_conversation(store.db, "delete-me")
                self.assertEqual(deleted, 2)
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM conversations WHERE chat_id=?",
                        ("delete-me",)).fetchone()[0], 0)
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM chat_settings WHERE chat_id=?",
                        ("delete-me",)).fetchone()[0], 0)
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM assistant_provenance").fetchone()[0], 1)
                self.assertEqual(store.recent("keep-me")[0]["content"],
                                 "Preserve this thread")
                self.assertEqual(store.note_provenance(note_id)["text"],
                                 "Owner memory remains")

    def test_deleting_empty_thread_settings_succeeds_without_touching_other(self):
        with tempfile.TemporaryDirectory() as folder:
            with LocalMemoryStore(Path(folder) / "milo.sqlite3") as store:
                set_chat_settings(store.db, ChatSettings("empty-chat"))
                set_chat_settings(store.db, ChatSettings("other-chat"))
                self.assertEqual(delete_conversation(store.db, "empty-chat"), 0)
                self.assertIsNone(store.db.execute(
                    "SELECT chat_id FROM chat_settings WHERE chat_id=?",
                    ("empty-chat",)).fetchone())
                self.assertIsNotNone(store.db.execute(
                    "SELECT chat_id FROM chat_settings WHERE chat_id=?",
                    ("other-chat",)).fetchone())
                with self.assertRaises(ValueError):
                    delete_conversation(store.db, "")
                self.assertIsNotNone(store.db.execute(
                    "SELECT chat_id FROM chat_settings WHERE chat_id=?",
                    ("other-chat",)).fetchone())


if __name__ == "__main__":
    unittest.main()
