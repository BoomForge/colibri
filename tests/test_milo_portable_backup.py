"""Offline synthetic round-trip tests for encrypted Milo backup and restore."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from colibri.milo_core.portable_backup import BackupError, export_bundle, import_bundle


class PortableBackupTests(unittest.TestCase):
    PASSWORD = "a long private recovery phrase"

    def make_sources(self, root: Path):
        memory = root / "milo.sqlite3"
        with sqlite3.connect(memory) as db:
            db.execute("CREATE TABLE notes(text TEXT)")
            db.execute("INSERT INTO notes VALUES ('synthetic private memory')")
        settings = root / "settings"
        settings.mkdir()
        (settings / "config.json").write_text('{"theme":"dark"}', encoding="utf-8")
        models = root / "models"
        models.mkdir()
        (models / "sample.gguf").write_bytes(b"synthetic model weights")
        return memory, {"settings": settings, "models": models}

    def test_round_trip_preserves_files_and_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            archive = base / "portable.milo"
            summary = export_bundle(archive, self.PASSWORD, memory_db=memory, roots=roots)
            self.assertEqual(summary.files, 3)
            self.assertNotIn(b"synthetic private memory", archive.read_bytes())
            self.assertNotIn(b"synthetic model weights", archive.read_bytes())
            restored = base / "restored"
            imported = import_bundle(archive, self.PASSWORD, destination=restored)
            self.assertEqual(imported.files, summary.files)
            self.assertEqual((restored / "models" / "sample.gguf").read_bytes(),
                             b"synthetic model weights")
            self.assertEqual((restored / "settings" / "config.json").read_text(),
                             '{"theme":"dark"}')
            with sqlite3.connect(restored / "data" / "memory.sqlite3") as db:
                self.assertEqual(db.execute("SELECT text FROM notes").fetchone()[0],
                                 "synthetic private memory")

    def test_wrong_password_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            archive = base / "portable.milo"
            export_bundle(archive, self.PASSWORD, memory_db=memory, roots=roots)
            restored = base / "restored"
            with self.assertRaises(BackupError):
                import_bundle(archive, "a completely wrong phrase", destination=restored)
            self.assertFalse(restored.exists())

    def test_modified_archive_rejected_without_partial_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            archive = base / "portable.milo"
            export_bundle(archive, self.PASSWORD, memory_db=memory, roots=roots)
            content = bytearray(archive.read_bytes())
            content[len(content) // 2] ^= 1
            archive.write_bytes(content)
            restored = base / "restored"
            with self.assertRaises(BackupError):
                import_bundle(archive, self.PASSWORD, destination=restored)
            self.assertFalse(restored.exists())

    def test_destination_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            archive = base / "portable.milo"
            export_bundle(archive, self.PASSWORD, memory_db=memory, roots=roots)
            existing = base / "old-profile"
            existing.mkdir()
            with self.assertRaises(BackupError):
                import_bundle(archive, self.PASSWORD, destination=existing)

    def test_missing_registered_models_refuses_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            archive = base / "portable.milo"
            with self.assertRaises(BackupError):
                export_bundle(archive, self.PASSWORD, memory_db=memory,
                              roots={**roots, "models": base / "missing"})
            self.assertFalse(archive.exists())

    def test_linked_model_weights_are_copied_not_relinked(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory, roots = self.make_sources(base)
            model_file = roots["models"] / "sample.gguf"
            model_file.unlink()
            original = base / "downloaded.gguf"
            original.write_bytes(b"original external weights")
            try:
                model_file.symlink_to(original)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable to this account")
            archive = base / "portable.milo"
            export_bundle(archive, self.PASSWORD, memory_db=memory, roots=roots)
            restored = base / "restored"
            import_bundle(archive, self.PASSWORD, destination=restored)
            self.assertFalse((restored / "models" / "sample.gguf").is_symlink())
            self.assertEqual((restored / "models" / "sample.gguf").read_bytes(),
                             b"original external weights")


if __name__ == "__main__":
    unittest.main()
