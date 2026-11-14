"""Milo isolated core regressions. All dialogue and notes are synthetic."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from colibri.milo_core.conversation import MiloConversation
from colibri.milo_core.consultation import (
    ConsultationError, approve_consultation, prepare_consultation,
    request_sol_opinion,
)
from colibri.milo_core.filesystem import find_files_by_prefix
from colibri.milo_core.provider import LocalOpenAIProvider, ProviderError
from colibri.milo_core.store import LocalMemoryStore


class FakeProvider:
    def __init__(self, parts=("Good ", "morning."), fail=False):
        self.parts = parts
        self.fail = fail
        self.messages = []

    def stream(self, messages):
        self.messages = list(messages)
        for part in self.parts:
            yield part
        if self.fail:
            raise ProviderError("synthetic interrupted inference")


class FakeSolChannel:
    def __init__(self):
        self.requests = []

    def request_opinion(self, question, context):
        self.requests.append((question, context))
        return "Synthetic second opinion"


class MiloCoreTests(unittest.TestCase):
    def test_conversation_persists_between_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "milo.sqlite3"
            with LocalMemoryStore(db) as store:
                provider = FakeProvider()
                chat = MiloConversation(provider, store)
                self.assertEqual(chat.reply("Hello"), "Good morning.")
                self.assertEqual(store.recent("main"), [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Good morning."},
                ])
            with LocalMemoryStore(db) as reopened:
                self.assertEqual(len(reopened.recent("main")), 2)

    def test_conversation_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with LocalMemoryStore(Path(tmp) / "milo.sqlite3") as store:
                MiloConversation(FakeProvider(), store).reply("First", chat_id="a")
                self.assertEqual(store.recent("b"), [])

    def test_approved_memory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with LocalMemoryStore(Path(tmp) / "milo.sqlite3") as store:
                with self.assertRaises(PermissionError):
                    store.add_approved_note(
                        "Lighthouse project", source_kind="synthetic",
                        source_ref="case-1"
                    )
                note_id = store.add_approved_note(
                    "Lighthouse project", source_kind="synthetic",
                    source_ref="case-1", approved=True
                )
                provider = FakeProvider()
                MiloConversation(provider, store).reply("What about lighthouse?")
                self.assertIn("case-1", provider.messages[0]["content"])
                self.assertTrue(store.delete_note(note_id))
                self.assertEqual(store.find_notes("lighthouse"), [])

    def test_partial_stream_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with LocalMemoryStore(Path(tmp) / "milo.sqlite3") as store:
                chat = MiloConversation(FakeProvider(fail=True), store)
                with self.assertRaises(ProviderError):
                    chat.reply("Hello")
                self.assertEqual(store.recent("main"), [])

    def test_user_cancelled_stream_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with LocalMemoryStore(Path(tmp) / "milo.sqlite3") as store:
                chat = MiloConversation(FakeProvider(), store)
                response = chat.stream_reply("Hello")
                self.assertEqual(next(response), "Good ")
                response.close()
                self.assertEqual(store.recent("main"), [])

    def test_prefix_search_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Milo_notes.txt").write_text("synthetic", encoding="utf-8")
            (root / "milo_plan.md").write_text("synthetic", encoding="utf-8")
            (root / "other.txt").write_text("synthetic", encoding="utf-8")
            self.assertEqual(
                [p.name for p in find_files_by_prefix(root, "milo")],
                ["Milo_notes.txt", "milo_plan.md"],
            )
            with self.assertRaises(ValueError):
                find_files_by_prefix(root, "../")

    def test_remote_provider_requires_explicit_https(self):
        with self.assertRaises(ValueError):
            LocalOpenAIProvider(model="synthetic", base_url="https://example.com/v1")
        with self.assertRaises(ValueError):
            LocalOpenAIProvider(model="synthetic",
                                base_url="http://example.com/v1", allow_remote=True)

    def test_sol_consultation_is_closed_by_default(self):
        draft = prepare_consultation("Should we redesign this?", context="Synthetic notes")
        channel = FakeSolChannel()
        with self.assertRaises(ConsultationError):
            request_sol_opinion(draft, channel=channel)
        self.assertEqual(channel.requests, [])
        approved = approve_consultation(draft, user_approved=True)
        with self.assertRaises(ConsultationError):
            request_sol_opinion(approved)
        self.assertEqual(request_sol_opinion(approved, channel=channel),
                         "Synthetic second opinion")
        self.assertEqual(channel.requests, [("Should we redesign this?", "Synthetic notes")])


if __name__ == "__main__":
    unittest.main()
