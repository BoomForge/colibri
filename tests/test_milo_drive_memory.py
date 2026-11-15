"""Read-only Google Drive extended-memory contract (no real Google login)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from colibri.milo_core import drive_memory as dm


class FakeDrive:
    def __init__(self, fail=False):
        self.fail = fail
        self.docs = {
            "1aaaaaaaaaaaa": {
                "id": "1aaaaaaaaaaaa", "name": "11_PROJECTS_AUTOMATION.md",
                "mimeType": "text/markdown", "modifiedTime": "2026-11-15T01:00:00Z",
                "webViewLink": "https://drive.google.com/file/d/1aaaaaaaaaaaa/view",
                "size": "90",
            },
            "1bbbbbbbbbbbb": {
                "id": "1bbbbbbbbbbbb", "name": "04_TOPICS.md",
                "mimeType": "text/markdown", "modifiedTime": "2026-11-15T02:00:00Z",
                "size": "100",
            },
            "1cccccccccccc": {
                "id": "1cccccccccccc", "name": "Secret Family Notes.md",
                "mimeType": "text/markdown", "modifiedTime": "2026-11-15T01:00:00Z",
                "size": "40",
            },
        }
        self.private = "1dddddddddddd"
        self.calls = []
    def metadata(self, folder):
        self.calls.append(("metadata", folder))
        return {"id": folder, "name": "SOL folder",
                "mimeType": dm.FOLDER_MIME, "trashed": False}
    def list_children(self, folder):
        self.calls.append(("list", folder))
        if folder == dm.SOL_FOLDER:
            return [
                self.docs["1aaaaaaaaaaaa"],
                self.docs["1bbbbbbbbbbbb"],
                {"id": self.private, "name": "Source Markdown",
                 "mimeType": dm.FOLDER_MIME},
            ]
        if folder == self.private:
            return [self.docs["1cccccccccccc"]]
        return []
    def text(self, item):
        self.calls.append(("text", item["name"]))
        if self.fail:
            raise dm.DriveMemoryError("Temporary Google API outage")
        if item["name"] == "11_PROJECTS_AUTOMATION.md":
            return ("2026-11-15 TassieNow current project progress: "
                    "repaired website browser checks and live data review.")
        if item["name"] == "04_TOPICS.md":
            return "TassieNow projects; Milo local assistant; anime research topics."
        return "This is sensitive personal information never to be indexed by default."


class MemoryTests(unittest.TestCase):
    def test_read_only_scope_is_exact_and_no_drive_write_api(self):
        self.assertEqual(dm.SCOPES, ("https://www.googleapis.com/auth/drive.readonly",))
        methods = [n for n in vars(dm.DriveReadClient) if not n.startswith("_")]
        self.assertEqual(set(methods), {"get", "metadata", "list_children", "text"})

    def test_folders_default_and_input_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conf = dm.load_config(root)
            self.assertEqual(conf["folders"], [dm.SOL_FOLDER])
            self.assertTrue(conf["exclude_sensitive_subfolders"])
            dm.save_config(root, [
                "https://drive.google.com/drive/folders/"+dm.SOL_FOLDER,
                "1eeeeeeeeeeee",
            ])
            self.assertEqual(dm.load_config(root)["folders"],
                             [dm.SOL_FOLDER, "1eeeeeeeeeeee"])
            with self.assertRaises(dm.DriveMemoryError):
                dm.save_config(root, ["https://evil.example/folders/read"])
            with self.assertRaises(dm.DriveMemoryError):
                dm.save_config(root, [])

    def test_index_is_encrypted_and_only_selected_root_traversed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake = FakeDrive()
            def secure_fake(data, *, decrypt):
                return data[::-1]
            with mock.patch.object(dm, "_dpapi", side_effect=secure_fake):
                dm.token_path(root).parent.mkdir(parents=True)
                dm.token_path(root).write_bytes(b"only_test_token")
                outcome = dm.refresh(root, client=fake)
                self.assertEqual(outcome["count"], 2)
                self.assertEqual(outcome["skipped"], 1)
                saved = dm.cache_path(root).read_bytes()
                self.assertNotIn(b"TassieNow", saved)
                self.assertNotIn(b"Secret Family", saved)
                index = dm.read_index(root)
                self.assertEqual(len(index["documents"]), 2)
                self.assertEqual(len([c for c in fake.calls
                                      if c[0] == "list" and c[1] == fake.private]), 0)
                matches = dm.search_memory(root, "TassieNow project progress")
                self.assertGreaterEqual(len(matches), 1)
                self.assertIn("TassieNow", matches[0]["excerpt"])
                ctx, foot = dm.model_context(matches)
                self.assertIn("NOT Milo's persona", ctx)
                self.assertIn("Drive memory sources", foot)
                self.assertIn("https://drive.google.com", foot)
                dm.disconnect(root)
                self.assertFalse(dm.connected(root))
                self.assertFalse(dm.cache_path(root).exists())

    def test_failed_refresh_preserves_last_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(dm, "_dpapi",
                                   side_effect=lambda x, decrypt: x[::-1]):
                dm.token_path(root).parent.mkdir(parents=True)
                dm.token_path(root).write_bytes(b"test")
                dm.refresh(root, client=FakeDrive())
                before = dm.cache_path(root).read_bytes()
                with self.assertRaises(dm.DriveMemoryError):
                    dm.refresh(root, client=FakeDrive(fail=True))
                self.assertEqual(dm.cache_path(root).read_bytes(), before)

    def test_empty_or_unrelated_queries_never_invent_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(dm, "_dpapi",
                                   side_effect=lambda x, decrypt: x[::-1]):
                dm.token_path(root).parent.mkdir(parents=True)
                dm.token_path(root).write_bytes(b"test")
                dm.refresh(root, client=FakeDrive())
                self.assertEqual(dm.search_memory(root, "what is this"), [])
                self.assertEqual(dm.search_memory(root, "raspberry omega"), [])
                self.assertEqual(dm.model_context([]), ("", ""))

    def test_read_client_only_accepts_fixed_google_drive_get_url(self):
        obj = object.__new__(dm.DriveReadClient)
        class Session:
            def get(self, url, **kwargs):
                raise AssertionError("Network should not be called")
        obj.session = Session()
        with self.assertRaises(dm.DriveMemoryError):
            obj.get("https://evil.example/drive/v3/files")
        with self.assertRaises(dm.DriveMemoryError):
            obj.get("https://www.googleapis.com/drive/v3/filesmalicious")

    def test_local_token_storage_requires_windows_user_encryption(self):
        if dm.sys.platform == "win32":
            with tempfile.TemporaryDirectory() as td:
                sample = b"fake oauth token never remote"
                encoded = dm._dpapi(sample, decrypt=False)
                self.assertNotIn(sample, encoded)
                self.assertEqual(dm._dpapi(encoded, decrypt=True), sample)
        else:
            with self.assertRaises(dm.DriveMemoryError):
                dm._dpapi(b"fake secret", decrypt=False)


if __name__ == "__main__":
    unittest.main()
