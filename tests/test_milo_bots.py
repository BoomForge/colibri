"""Local bot / private-chat / model-selection tests with synthetic content only."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from colibri.milo_core.bots import (
    Bot, ChatSettings, attributed_messages, chat_settings, get_bot,
    init_bot_schema, list_bots, model_for_turn, recent_for_bot,
    resolve_target, save_bot, set_bot_active, set_chat_settings,
)
from colibri.milo_core.conversation import MiloConversation, MiloProfile
from colibri.milo_core.store import LocalMemoryStore


class Provider:
    def __init__(self, answer="Synthetic bot response"):
        self.answer = answer
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        yield self.answer


class BotLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalMemoryStore(Path(self.tmp.name) / "memory.sqlite3")
        init_bot_schema(self.store.db)
        self.code = Bot(
            id="code", name="Code",
            instructions="You are synthetic Code. Write clear Python snippets.",
            model_location="qwen/model-a", avatar="")
        self.research = Bot(
            id="research", name="Research",
            instructions="You are synthetic Research. Be precise and cite evidence.",
            model_location="", avatar="")
        save_bot(self.store.db, self.code)
        save_bot(self.store.db, self.research)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_profile_creation_and_user_owned_names(self):
        names = [bot.id for bot in list_bots(self.store.db)]
        self.assertEqual(names, ["code", "research"])
        self.assertIsNone(get_bot(self.store.db, "milo"))
        self.assertEqual(get_bot(self.store.db, "code").name, "Code")
        with self.assertRaises(ValueError):
            save_bot(self.store.db, Bot("milo", "Fake Milo", "Personality", "", ""))
        with self.assertRaises(ValueError):
            save_bot(self.store.db, Bot("../hack", "Bad", "Prompt", "", ""))

    def test_shared_room_mention_routes_explicit_bot(self):
        settings = ChatSettings("main")
        self.assertEqual(resolve_target(
            self.store.db, "@code please help with this error", settings),
            ("code", "please help with this error"))
        self.assertEqual(resolve_target(
            self.store.db, "Can @research check these notes?", settings),
            ("research", "Can  check these notes?"))
        self.assertEqual(resolve_target(self.store.db, "Hello Milo", settings),
                         ("milo", "Hello Milo"))
        with self.assertRaisesRegex(ValueError, "does not match"):
            resolve_target(self.store.db, "@unknown debug this", settings)
        self.assertEqual(resolve_target(
            self.store.db, "send a message to someone@example.com", settings),
            ("milo", "send a message to someone@example.com"))

    def test_private_dm_is_locked_to_its_bot(self):
        settings = ChatSettings("dm-1", "private", "code", "")
        set_chat_settings(self.store.db, settings)
        self.assertEqual(chat_settings(self.store.db, "dm-1"), settings)
        self.assertEqual(resolve_target(
            self.store.db, "@research debug the code", settings),
            ("code", "@research debug the code"))
        self.assertEqual(chat_settings(self.store.db, "other-chat").kind, "room")
        set_bot_active(self.store.db, "code", False)
        with self.assertRaisesRegex(ValueError, "disabled"):
            resolve_target(self.store.db, "Hello again", settings)

    def test_per_chat_model_takes_precedence(self):
        room = ChatSettings("main", "room", "milo", "qwen/standard")
        private = ChatSettings("dm", "private", "code", "")
        self.assertEqual(model_for_turn(room, self.code, "default"),
                         "qwen/standard")
        self.assertEqual(model_for_turn(private, self.code, "default"),
                         "qwen/model-a")
        self.assertEqual(model_for_turn(ChatSettings("main"), None, "default"),
                         "default")
        set_chat_settings(self.store.db, room)
        self.assertEqual(chat_settings(self.store.db, "main"), room)

    def test_specialist_isolation_and_attributed_history(self):
        room = "shared"
        provider = Provider()
        specialist = MiloConversation(
            provider, self.store,
            profile=MiloProfile("Code", self.code.instructions),
            bot_id="code", bot_name="Code", shared_room=True)
        response = specialist.reply(
            "Write some Python", chat_id=room)
        self.assertEqual(response, "Synthetic bot response")
        self.assertEqual(provider.messages[0]["role"], "system")
        self.assertIn("synthetic Code", provider.messages[0]["content"])
        self.assertNotIn("Milo, a personal conversational", provider.messages[0]["content"])
        self.assertEqual(
            attributed_messages(self.store.db, room)[-1][3:5], ("code", "Code"))
        self.assertEqual(
            recent_for_bot(self.store.db, room)[-1]["content"],
            "[Reply by @code (Code)]\nSynthetic bot response")
        milo = Provider("Milo says hi")
        MiloConversation(milo, self.store, shared_room=True).reply(
            "What happened?", chat_id=room)
        self.assertEqual(milo.messages[1]["content"], "Write some Python")
        self.assertEqual(milo.messages[2]["content"],
                         "[Reply by @code (Code)]\nSynthetic bot response")
        self.assertEqual(attributed_messages(self.store.db, room)[-1][3], "milo")

    def test_incomplete_specialist_response_not_persisted(self):
        class Failing:
            def stream(self, messages):
                yield "half"
                raise RuntimeError("synthetic error")
        specialist = MiloConversation(
            Failing(), self.store,
            profile=MiloProfile("Code", self.code.instructions),
            bot_id="code", bot_name="Code")
        with self.assertRaises(RuntimeError):
            specialist.reply("hello", chat_id="dm")
        self.assertEqual(self.store.recent("dm"), [])

    def test_private_specialist_history_has_no_milo_archive(self):
        provider = Provider()
        MiloConversation(
            provider, self.store,
            profile=MiloProfile("Code", self.code.instructions),
            bot_id="code", bot_name="Code", shared_room=False).reply(
                "Is my private Milo archive available?", chat_id="dm")
        self.assertNotIn("VERIFIED HISTORICAL", provider.messages[0]["content"])
        self.assertEqual(attributed_messages(self.store.db, "dm")[-1][3], "code")


if __name__ == "__main__":
    unittest.main()
