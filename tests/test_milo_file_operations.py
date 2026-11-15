"""No-overwrite and preview consent tests for user-chosen asset folders."""
from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.file_operations import (
    FileMoveError, plan_file_moves, execute_file_moves,
)


class FileOrganisationTests(unittest.TestCase):
    def test_preview_does_not_modify_files_and_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "source", root / "target"
            a.mkdir()
            b.mkdir()
            f = a / "Stone_Grey.PNG"
            f.write_bytes(b"safe original bytes")
            (a / "stone-trap.exe").write_bytes(b"not allowed")
            (a / "wood.png").write_bytes(b"not chosen")
            plan = plan_file_moves(a, b, "stone")
            self.assertEqual(len(plan.files), 1)
            self.assertEqual(plan.files[0].source.name, "Stone_Grey.PNG")
            self.assertTrue(f.is_file())
            self.assertEqual(list(b.iterdir()), [])
            with self.assertRaises(PermissionError):
                execute_file_moves(plan)
            self.assertEqual(execute_file_moves(plan, approved=True), 1)
            self.assertFalse(f.exists())
            self.assertEqual((b / f.name).read_bytes(), b"safe original bytes")
            self.assertTrue((a / "stone-trap.exe").exists())

    def test_conflicts_abort_without_any_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            a.mkdir()
            b.mkdir()
            (a / "stone.png").write_bytes(b"original")
            (b / "STONE.PNG").write_bytes(b"destination")
            with self.assertRaises(FileMoveError):
                plan_file_moves(a, b, "stone")
            self.assertEqual((a / "stone.png").read_bytes(), b"original")
            self.assertEqual((b / "STONE.PNG").read_bytes(), b"destination")

    def test_source_change_invalidates_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            a.mkdir()
            b.mkdir()
            file = a / "stone.png"
            file.write_bytes(b"before")
            plan = plan_file_moves(a, b, "stone")
            file.write_bytes(b"after the preview")
            with self.assertRaises(FileMoveError):
                execute_file_moves(plan, approved=True)
            self.assertEqual(list(b.iterdir()), [])

    def test_invalid_pattern_or_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            a.mkdir()
            b.mkdir()
            for fragment in ("", "x", "../other", "*.png", "stone/"):
                with self.subTest(fragment=fragment), self.assertRaises(FileMoveError):
                    plan_file_moves(a, b, fragment)
            with self.assertRaises(FileMoveError):
                plan_file_moves(a, a, "stone")

if __name__ == "__main__":
    unittest.main()
