"""Milo desktop workflow regressions using synthetic private/temp data only."""
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from colibri.milo_core.attachments import (
    AttachmentError, attachment_context, inspect_attachment,
    stage_attachments, parse_dropped_paths,
)
from colibri.milo_core.bots import (
    Bot, ChatSettings, append_attributed_turn, attributed_messages,
    chat_settings, delete_conversation, get_bot, save_bot, set_chat_settings,
)
from colibri.milo_core.specialist_presets import (
    PRESETS, ensure_specialists,
)
from colibri.milo_core.store import LocalMemoryStore
from colibri.milo_core.web_research import (
    WebSource, harvard_references, parse_rss,
)


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LocalMemoryStore(self.root / "mem.sqlite3")
        self.db = self.store.db

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_default_specialists_seed_once_without_replacing_user_edits(self):
        count = ensure_specialists(self.db, self.root / "Models")
        self.assertEqual(count, ["research", "webdev", "gamedev"])
        self.assertEqual(get_bot(self.db, "research").name, "Sage")
        self.assertTrue("Harvard" in get_bot(self.db, "research").instructions)
        self.assertTrue("HTML/CSS" in get_bot(self.db, "webdev").instructions)
        self.assertTrue("Blender" in get_bot(self.db, "gamedev").instructions)
        custom = Bot("research", "My Edited Researcher", "I am customised.",
                     "", "", False)
        save_bot(self.db, custom)
        self.assertEqual(ensure_specialists(self.db, self.root / "Models"), [])
        self.assertEqual(get_bot(self.db, "research"), custom)
        self.assertEqual(len(PRESETS), 3)

    def test_delete_only_one_chat_and_preserve_memories_and_profiles(self):
        ensure_specialists(self.db, self.root / "Models")
        set_chat_settings(self.db, ChatSettings("dm1", "private", "research", ""))
        set_chat_settings(self.db, ChatSettings("dm2", "private", "webdev", ""))
        append_attributed_turn(self.db, "dm1", "Prompt A", "Answer A",
                               bot_id="research", bot_name="Research")
        append_attributed_turn(self.db, "dm2", "Prompt B", "Answer B",
                               bot_id="webdev", bot_name="Web Development")
        self.store.add_approved_note(
            "Retain this note", source_kind="owner", source_ref="test", approved=True)
        self.assertEqual(delete_conversation(self.db, "dm1"), 2)
        self.assertEqual(attributed_messages(self.db, "dm1"), [])
        self.assertEqual(len(attributed_messages(self.db, "dm2")), 2)
        self.assertEqual(chat_settings(self.db, "dm1").kind, "room")
        self.assertEqual(get_bot(self.db, "research").id, "research")
        self.assertEqual(self.store.find_notes("Retain note")[0].text, "Retain this note")

    def test_harvard_references_do_not_invent_dates(self):
        text = harvard_references([
            WebSource("Original page", "https://nasa.gov/uap", "snippet", ""),
        ], accessed=datetime(2026, 11, 14))
        self.assertIn("nasa.gov (n.d.)", text)
        self.assertIn("Available at: https://nasa.gov/uap", text)
        self.assertIn("Accessed: 14 November 2026", text)
        self.assertIn("Search snippet only", text)

    def test_search_result_parsing_skips_search_engine_tracking_links(self):
        rss = (
            b'<rss><channel>'
            b'<item><title>Tracking</title>'
            b'<link>https://www.bing.com/ck/a?tracking</link></item>'
            b'<item><title>Original</title>'
            b'<link>https://science.nasa.gov/uap/</link></item>'
            b'</channel></rss>')
        items = parse_rss(rss)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Original")

    def test_safe_text_and_image_attachments(self):
        from PIL import Image
        doc = self.root / "notes.txt"
        doc.write_text("My character is called Pixel Knight.", encoding="utf-8")
        img = self.root / "visual.png"
        Image.new("RGB", (24, 24), "pink").save(img)
        inspected = [inspect_attachment(doc), inspect_attachment(img)]
        staged = stage_attachments(inspected, self.root / "Milo")
        try:
            self.assertEqual([x.kind for x in staged], ["document", "image"])
            context, images = attachment_context(staged)
            self.assertTrue(images)
            self.assertIn("Pixel Knight", context)
            self.assertIn("cannot analyse", context.lower().replace("not supplied", "cannot analyse"))
            self.assertIn("Pixels NOT supplied", context)
            self.assertTrue(all(x.path.exists() for x in staged))
        finally:
            for item in staged:
                item.path.unlink(missing_ok=True)

    def test_tcl_file_drop_handles_paths_with_spaces(self):
        class FakeTk:
            def splitlist(self, raw):
                return (
                    "C:/Users/Test/My Documents/game script.md",
                    "C:/Users/Test/Downloads/Milo image.png"
                )
        class FakeRoot:
            tk = FakeTk()
        paths = parse_dropped_paths(FakeRoot(), "{ignored}")
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0].name, "game script.md")

    def test_unsafe_or_missing_attachments_are_rejected(self):
        with self.assertRaises((OSError, ValueError)):
            inspect_attachment(self.root / "missing.txt")
        source = self.root / "script.exe"
        source.write_bytes(b"MZ fake")
        with self.assertRaises((AttachmentError, ValueError)):
            inspect_attachment(source)


if __name__ == "__main__":
    unittest.main()
