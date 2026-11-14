"""Private file-listing tool regression tests (synthetic temporary trees)."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from colibri.milo_core.chat_files import (
    FileQuery, answer_file_query, list_folder, parse_file_query,
)


class ChatFileToolsTests(unittest.TestCase):
    def test_downloads_listing_intent(self):
        q = parse_file_query(
            "can you search the downloads folder and tell me what files are in there please")
        self.assertIsNotNone(q)
        self.assertEqual(q.folder, "downloads")
        self.assertFalse(q.recursive)

    def test_content_read_not_implicitly_authorised(self):
        self.assertIsNone(parse_file_query("Read the files inside downloads for me"))
        self.assertIsNone(parse_file_query("Hello, how is your evening?"))
        self.assertIsNone(parse_file_query("Write some code for me"))

    def test_no_folder_gets_explicit_clarification(self):
        q = parse_file_query("list my files")
        self.assertIsNotNone(q)
        self.assertIsNone(q.folder)
        self.assertIn("Which folder", answer_file_query(q))

    def test_scans_names_only_and_filters(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "notes.txt").write_text("PRIVATE SHOULD NOT APPEAR")
            (root / "game.png").write_bytes(b"fake binary")
            (root / "Subfolder").mkdir()
            (root / "Subfolder" / "secret.txt").write_text("PRIVATE")
            text, count = list_folder(root, FileQuery("downloads"))
            self.assertEqual(count, 3)
            self.assertIn("[File] notes.txt", text)
            self.assertIn("[Folder] Subfolder", text)
            self.assertNotIn("PRIVATE", text)
            self.assertNotIn("secret.txt", text)
            recursive, count = list_folder(root, FileQuery("downloads", recursive=True))
            self.assertEqual(count, 4)
            self.assertIn("Subfolder", recursive)
            only_png, count = list_folder(root, FileQuery("downloads", extension=".png"))
            self.assertEqual(count, 1)
            self.assertIn("game.png", only_png)
            self.assertNotIn("notes.txt", only_png)

    def test_symlinks_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "safe.txt").write_text("ok")
            try:
                (root / "outside").symlink_to(root, target_is_directory=True)
            except OSError:
                return
            text, count = list_folder(root, FileQuery("downloads", recursive=True))
            self.assertEqual(count, 1)
            self.assertNotIn("[Folder] outside", text)

    def test_uses_actual_downloads_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "real-file.zip").write_bytes(b"ok")
            with patch("colibri.milo_core.chat_files.known_folder", return_value=root):
                answer = answer_file_query(FileQuery(folder="downloads"))
            self.assertIn("real-file.zip", answer)
            self.assertIn("not opened any files", answer)


if __name__ == "__main__":
    unittest.main()
