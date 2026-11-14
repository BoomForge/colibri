"""Sage/Nova/Forge aliases and one-time non-destructive identity migration."""
from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.bots import (
    Bot, ChatSettings, attributed_messages, get_bot, public_handle,
    resolve_target, save_bot, set_chat_settings,
)
from colibri.milo_core.specialist_presets import (
    ensure_specialists, RESEARCH_PROMPT,
)
from colibri.milo_core.store import LocalMemoryStore


class SpecialistIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalMemoryStore(Path(self.tmp.name) / "milo.db")
        self.addCleanup(self.store.close)
        self.db = self.store.db
        self.models_root = Path(self.tmp.name) / "Models"
        self.models_root.mkdir()

    def test_first_install_display_names_and_user_mention_aliases(self):
        self.assertEqual(set(ensure_specialists(self.db, self.models_root)),
                         {"research", "webdev", "gamedev"})
        self.assertEqual(ensure_specialists(self.db, self.models_root), [])
        for internal, handle, display in (
            ("research", "sage", "Sage"),
            ("webdev", "nova", "Nova"),
            ("gamedev", "forge", "Forge"),
        ):
            self.assertEqual(get_bot(self.db, internal).name, display)
            self.assertEqual(public_handle(internal), handle)
            actual, prompt = resolve_target(
                self.db, f"Hello @{handle}, can you help with this?",
                ChatSettings("main"))
            self.assertEqual(actual, internal)
            self.assertEqual(prompt, "Hello , can you help with this?")
            old, old_prompt = resolve_target(
                self.db, f"@{internal} please check", ChatSettings("main"))
            self.assertEqual((old, old_prompt), (internal, "please check"))
        self.assertEqual(resolve_target(
            self.db, "@milo help", ChatSettings("main")), ("milo", "help"))
        with self.assertRaisesRegex(ValueError, "enabled bot"):
            resolve_target(self.db, "@unknown help", ChatSettings("main"))

    def test_migrate_existing_specialist_without_changing_model_or_docs(self):
        # This emulates the formerly installed built-in ID and prompts.
        old_prompt = RESEARCH_PROMPT.replace("You are Sage", "You are Research", 1)
        save_bot(self.db, Bot("research", "Research", old_prompt,
                             "Models/example", "", True))
        self.store.append_turn("chat-for-research", "Hello", "Old reply")
        set_chat_settings(
            self.db, ChatSettings(
                "chat-for-research", "private", "research", ""))
        self.assertEqual(ensure_specialists(self.db, self.models_root),
                         ["webdev", "gamedev"])
        current = get_bot(self.db, "research")
        self.assertEqual(current.name, "Sage")
        self.assertEqual(current.model_location, "Models/example")
        self.assertEqual(current.instructions, RESEARCH_PROMPT)
        self.assertEqual(self.store.recent("chat-for-research")[0]["content"],
                         "Hello")
        self.assertEqual(self.db.execute(
            "SELECT bot_id FROM chat_settings WHERE chat_id='chat-for-research'"
        ).fetchone()[0], "research")
        # Owner editing a display name or instructions after migration wins.
        save_bot(self.db, Bot(
            "research", "Owner Custom Sage", "Custom instructions",
            "Models/custom", "", True))
        ensure_specialists(self.db, self.models_root)
        self.assertEqual(get_bot(self.db, "research").name,
                         "Owner Custom Sage")
        self.assertEqual(get_bot(self.db, "research").instructions,
                         "Custom instructions")

    def test_custom_avatared_bot_and_instructions_are_never_overwritten(self):
        named_image = "c" * 32 + ".webp"
        save_bot(self.db, Bot(
            "webdev", "Web Development", "Owner web instructions",
            "model-location", named_image, False))
        ensure_specialists(self.db, self.models_root)
        got = get_bot(self.db, "webdev")
        self.assertEqual(got.name, "Nova")
        self.assertEqual(got.instructions, "Owner web instructions")
        self.assertEqual(got.avatar, named_image)
        self.assertFalse(got.active)
        self.assertEqual(got.model_location, "model-location")
        with self.assertRaisesRegex(ValueError, "enabled bot"):
            resolve_target(self.db, "@nova create a page", ChatSettings("main"))


if __name__ == "__main__":
    unittest.main()
