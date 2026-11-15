"""PL-01: non-destructive synthetic continuity backup/restore acceptance tests.

No real user files, network, credentials or model weights are accessed.
These tests are necessary but are NOT an owner's real Windows restore check.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from colibri.milo_core.bots import Bot, save_bot
from colibri.milo_core.portable_backup import BackupError, export_bundle, import_bundle
from colibri.milo_core.store import LocalMemoryStore


class MiloPL01RestoreTests(unittest.TestCase):
    PASSPHRASE = "a synthetic passphrase for offline unit testing"

    @staticmethod
    def _prepare(root: Path):
        active = root / "active"
        active.mkdir()
        db_path = active / "memory.sqlite3"
        with LocalMemoryStore(db_path) as store:
            # A live WAL database must still produce one consistent snapshot.
            store.db.execute("PRAGMA journal_mode=WAL")
            store.append_turn("main", "Synthetic user line", "Synthetic Milo reply")
            store.add_approved_note(
                "Synthetic continuity preference",
                source_kind="owner",
                source_ref="pl01-synthetic",
                approved=True,
            )
            save_bot(store.db, Bot(
                id="research", name="Sage", instructions="Synthetic bot profile",
                model_location="", avatar="",
            ))
            store.db.commit()
            original = {
                "conversation_count": store.db.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0],
                "note": store.db.execute(
                    "SELECT text, source_kind FROM approved_notes"
                ).fetchone(),
                "bot": store.db.execute(
                    "SELECT name, instructions FROM bot_profiles WHERE id='research'"
                ).fetchone(),
            }
        roots = {}
        for component in ("settings", "avatars", "archives", "documents", "models"):
            folder = active / component
            folder.mkdir()
            roots[component] = folder
        (roots["settings"] / "preferences.json").write_text(
            '{"milo_name": "Milo", "version": "synthetic"}',
            encoding="utf-8",
        )
        (roots["avatars"] / "avatar.png").write_bytes(b"synthetic avatar bytes")
        (roots["archives"] / "historical-chat.json").write_bytes(
            b"synthetic source-labelled history, NOT actual Kindroid data"
        )
        (roots["documents"] / "source.md").write_text(
            "Synthetic private reference", encoding="utf-8"
        )
        (roots["models"] / "small.gguf").write_bytes(b"fake test model bytes")
        return db_path, roots, original

    def test_backup_keeps_active_profile_and_recovers_history_bot_and_models(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            db_path, roots, original = self._prepare(base)
            archive = base / "safe" / "private.milobak"
            summary = export_bundle(
                archive, self.PASSPHRASE, memory_db=db_path, roots=roots
            )
            self.assertGreaterEqual(summary.files, 6)
            ciphertext = archive.read_bytes()
            self.assertNotIn(b"Synthetic continuity preference", ciphertext)
            self.assertNotIn(b"fake test model bytes", ciphertext)

            active_snapshot = {
                name: (folder / filename).read_bytes()
                for name, folder, filename in (
                    ("settings", roots["settings"], "preferences.json"),
                    ("avatars", roots["avatars"], "avatar.png"),
                    ("archives", roots["archives"], "historical-chat.json"),
                    ("models", roots["models"], "small.gguf"),
                )
            }

            staging = base / "milo-restored-to-new-directory"
            restored = import_bundle(
                archive, self.PASSPHRASE, destination=staging
            )
            self.assertEqual(restored.files, summary.files)
            self.assertNotEqual(staging, db_path.parent)
            for name, value in active_snapshot.items():
                filename = {
                    "settings": "preferences.json",
                    "avatars": "avatar.png",
                    "archives": "historical-chat.json",
                    "models": "small.gguf",
                }[name]
                self.assertEqual((staging / name / filename).read_bytes(), value)
                self.assertEqual((roots[name] / filename).read_bytes(), value)

            with closing(sqlite3.connect(staging / "data" / "memory.sqlite3")) as restored_db:
                self.assertEqual(
                    restored_db.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT COUNT(*) FROM conversations"
                    ).fetchone()[0],
                    original["conversation_count"],
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT text, source_kind FROM approved_notes"
                    ).fetchone(),
                    original["note"],
                )
                self.assertEqual(
                    restored_db.execute(
                        "SELECT name, instructions FROM bot_profiles "
                        "WHERE id='research'"
                    ).fetchone(),
                    original["bot"],
                )
            # Import must not modify or replace the original database.
            with closing(sqlite3.connect(db_path)) as live_db:
                self.assertEqual(
                    live_db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                    original["conversation_count"],
                )

    def test_failed_authentication_preserves_existing_profile_and_no_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            db_path, roots, original = self._prepare(base)
            archive = base / "backup.milobak"
            export_bundle(archive, self.PASSPHRASE, memory_db=db_path, roots=roots)
            staging = base / "should-not-exist"
            with self.assertRaises(BackupError):
                import_bundle(
                    archive,
                    "incorrect but long enough backup passphrase",
                    destination=staging,
                )
            self.assertFalse(staging.exists())
            with closing(sqlite3.connect(db_path)) as live_db:
                self.assertEqual(
                    live_db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                    original["conversation_count"],
                )


if __name__ == "__main__":
    unittest.main()
