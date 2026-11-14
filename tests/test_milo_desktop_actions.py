"""Real local file opening requests: bounded filename resolution, no execution."""
from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.desktop_actions import (
    OpenFileError, parse_open_request, locate_file,
    preview_type, validate_preview_path,
)


class DesktopFileActionsTests(unittest.TestCase):
    def test_example_from_user_downloads(self):
        query = parse_open_request("can you open milo_icon_alpha_cleaned.png")
        self.assertIsNotNone(query)
        self.assertEqual(query.name, "milo_icon_alpha_cleaned.png")
        self.assertIsNone(query.folder)
        self.assertEqual(
            parse_open_request("please preview 'Milo concept sketch.png' from downloads").name,
            "Milo concept sketch.png")

    def test_unapproved_executables_and_urls_never_routed(self):
        self.assertIsNone(parse_open_request("open virus.exe"))
        self.assertIsNone(parse_open_request("open https://example.com/test.png"))
        self.assertIsNone(parse_open_request("how would you open a window?"))
        self.assertIsNone(parse_open_request("open config.bat"))
        self.assertIsNone(parse_open_request("open ../secrets.pdf"))

    def test_actual_file_from_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            downloads = root / "Downloads"
            downloads.mkdir()
            sample = downloads / "milo_icon_alpha_cleaned.png"
            sample.write_bytes(b"fake pixels for path resolution only")
            query = parse_open_request("can you open milo_icon_alpha_cleaned.png")
            found = locate_file(query, folder_roots={"downloads": downloads})
            self.assertEqual(found, sample)
            self.assertEqual(preview_type(sample), "image")

    def test_ambiguity_refuses_arbitrary_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("Downloads", "Pictures"):
                (root / folder).mkdir()
                (root / folder / "same.png").write_bytes(b"fake")
            query = parse_open_request("open same.png")
            with self.assertRaisesRegex(OpenFileError, "Several"):
                locate_file(query, folder_roots={
                    "downloads": root / "Downloads",
                    "pictures": root / "Pictures",
                })
            query = parse_open_request("open same.png in downloads")
            self.assertEqual(
                locate_file(query, folder_roots={"downloads": root / "Downloads"}),
                root / "Downloads" / "same.png")

    def test_symlink_directory_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "Downloads"
            folder.mkdir()
            hidden = root / "elsewhere"
            hidden.mkdir()
            (hidden / "secret.png").write_bytes(b"fake")
            try:
                (folder / "link").symlink_to(hidden, target_is_directory=True)
            except OSError:
                return  # Windows restricted symlink creation
            query = parse_open_request("open secret.png in downloads")
            with self.assertRaisesRegex(OpenFileError, "couldn't find"):
                locate_file(query, folder_roots={"downloads": folder})

    def test_reject_oversized_file(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "huge.png"
            with file.open("wb") as writer:
                writer.seek(16 * 1024 * 1024)
                writer.write(b"x")
            with self.assertRaises(OpenFileError):
                validate_preview_path(file)


if __name__ == "__main__":
    unittest.main()
