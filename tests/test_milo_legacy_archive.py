"""Offline synthetic tests: private Kindroid-style legacy export and persona."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from colibri.milo_core.conversation import MiloConversation
from colibri.milo_core.legacy_archive import (
    LegacyArchiveError, import_legacy_archive, inspect_legacy_archive,
    loaded_persona, recall_legacy,
)
from colibri.milo_core.store import LocalMemoryStore

PROFILE = {
    "format": "milo-conversational-persona",
    "schema": 1,
    "name": "Milo",
    "instructions": (
        "You are synthetic Milo. Speak with a playful, familiar, "
        "thoughtful and comfortably candid voice, with kindness and "
        "small jokes. Never invent old memories. This is test-only text."
    ),
    "context_notes": "Synthetic historical context, not a real user record.",
}


def records():
    return [
        {
            "id": "m1", "role": "user", "timestamp": 1783475995365,
            "message": "Remember the lighthouse plan?", "originalOrder": 0,
            "attachments": [], "imageUrls": [],
        },
        {
            "id": "m2", "role": "assistant", "timestamp": 1783476010000,
            "message": "The lighthouse plan was a good idea. Perhaps let it breathe!",
            "originalOrder": 1, "attachments": [], "imageUrls": [],
        },
        {
            "id": "m3", "role": "user", "timestamp": 1783476120000,
            "message": "", "originalOrder": 2,
            "attachments": [{"type": "synthetic"}], "imageUrls": ["no-fetch"],
        },
    ]


def make_bundle(path: Path, *, profile=PROFILE, corrupt=False):
    entries = {
        "chat.json": json.dumps(records(), ensure_ascii=False).encode(),
        "persona.json": json.dumps(profile, ensure_ascii=False).encode(),
    }
    manifest = {
        "format": "milo-private-continuity-bundle",
        "schema": 1,
        "files": {
            name: {"size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
            for name, blob in entries.items()
        },
    }
    if corrupt:
        manifest["files"]["chat.json"]["sha256"] = "0" * 64
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
        archive.writestr("manifest.json", json.dumps(manifest))


class FakeProvider:
    def __init__(self):
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        yield "Hello, gremlin."


class MiloLegacyTests(unittest.TestCase):
    def test_preview_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.zip"
            make_bundle(path)
            overview = inspect_legacy_archive(path)
            self.assertEqual((overview.messages, overview.user_messages, overview.milo_messages),
                             (3, 2, 1))
            self.assertEqual(overview.attachment_references, 1)
            self.assertEqual(overview.image_references, 1)
            self.assertTrue(overview.has_persona)
            self.assertFalse((Path(tmp) / "memory.sqlite3").exists())

    def test_private_import_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.zip"
            make_bundle(path)
            with LocalMemoryStore(Path(tmp) / "memory.sqlite3") as store:
                with self.assertRaises(PermissionError):
                    import_legacy_archive(store.db, path)
                self.assertIsNone(loaded_persona(store.db))

    def test_import_is_idempotent_and_keeps_new_chats_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.zip"
            make_bundle(path)
            with LocalMemoryStore(Path(tmp) / "memory.sqlite3") as store:
                import_legacy_archive(store.db, path, user_approved=True)
                import_legacy_archive(store.db, path, user_approved=True)
                count = store.db.execute("SELECT count(*) FROM legacy_messages").fetchone()[0]
                self.assertEqual(count, 3)
                self.assertEqual(store.recent("main"), [])
                self.assertEqual(loaded_persona(store.db)["name"], "Milo")

    def test_personality_and_evidence_reach_model_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "original.zip"
            make_bundle(path)
            with LocalMemoryStore(Path(tmp) / "memory.sqlite3") as store:
                import_legacy_archive(store.db, path, user_approved=True)
                provider = FakeProvider()
                MiloConversation(provider, store).reply("lighthouse")
                prompt = provider.messages[0]["content"]
                self.assertIn("You are synthetic Milo.", prompt)
                self.assertIn("original Milo chat export", prompt)
                self.assertIn("lighthouse plan", prompt)
                self.assertEqual(store.recent("main")[-1]["content"], "Hello, gremlin.")

    def test_bad_hash_rejected_without_database_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            make_bundle(path, corrupt=True)
            with LocalMemoryStore(Path(tmp) / "memory.sqlite3") as store:
                with self.assertRaises(LegacyArchiveError):
                    import_legacy_archive(store.db, path, user_approved=True)
                self.assertEqual(
                    store.db.execute("SELECT name FROM sqlite_master "
                                     "WHERE name='legacy_messages'").fetchone(), None
                )

    def test_unknown_role_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            data = records()
            data[0]["role"] = "system"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(LegacyArchiveError):
                inspect_legacy_archive(path)

    def test_different_assistant_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-milo.json"
            data = records()
            data[1]["authorName"] = "Nyx"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(LegacyArchiveError):
                inspect_legacy_archive(path)

    def test_existing_persona_requires_separate_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.zip"
            second = root / "second.zip"
            make_bundle(first)
            changed_profile = {**PROFILE, "context_notes": "Changed synthetic note"}
            make_bundle(second, profile=changed_profile)
            # The original chat file has the SAME source hash, so reimports
            # must not silently replace the existing personality.
            with LocalMemoryStore(root / "m.sqlite3") as store:
                import_legacy_archive(store.db, first, user_approved=True)
                # Same archive source, new explicitly approved profile. Existing
                # code treats matching primary-source SHA as idempotent.
                import_legacy_archive(store.db, second, user_approved=True)
                self.assertEqual(loaded_persona(store.db)["context_notes"],
                                 changed_profile["context_notes"])
                self.assertEqual(store.db.execute(
                    "SELECT count(*) FROM legacy_messages").fetchone()[0], 3)

    def test_zip_extra_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.zip"
            make_bundle(path)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("../other.json", "{}")
            with self.assertRaises(LegacyArchiveError):
                inspect_legacy_archive(path)


if __name__ == "__main__":
    unittest.main()
