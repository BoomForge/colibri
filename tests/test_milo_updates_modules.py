"""Synthetic tests of updater, module manager and integrity gates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from colibri.milo_core.modules import (
    ModuleError, BUILTINS, enabled, set_enabled, install_verified_module,
    load_module_actions, load_state,
)
from colibri.milo_core.updates import (
    CURRENT_VERSION, UpdateError, get_release_asset,
    installer_asset, newer, release_version,
)


class UpdateAndModulesTests(unittest.TestCase):
    def test_semver_ordering(self):
        self.assertTrue(newer("0.3.0", "0.2.0"))
        self.assertFalse(newer("0.2.0", "0.2.0"))
        with self.assertRaises(UpdateError):
            newer("0.3.0-beta", "0.2.0")

    def test_verifies_pinned_release_asset_and_checksum_metadata(self):
        name = "Milo-Setup-0.3.0.exe"
        item = {
            "name": name, "size": 1245, "digest": "sha256:" + ("a" * 64),
            "browser_download_url": (
                "https://github.com/BoomForge/colibri/releases/download/milo-v0.3.0/"
                + name),
        }
        release = {"tag_name": "milo-v0.3.0", "assets": [item]}
        asset = installer_asset(release)
        self.assertEqual(asset.sha256, "a" * 64)
        self.assertEqual(release_version(release), "0.3.0")
        tampered = {**release, "assets": [
            {**item, "browser_download_url": "https://evil.example/fake.exe"}]}
        with self.assertRaises(UpdateError):
            installer_asset(tampered)
        no_digest = {**release, "assets": [{**item, "digest": ""}]}
        with self.assertRaises(UpdateError):
            installer_asset(no_digest)

    def test_builtin_enabled_flag_persisted_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(enabled(root, "memory"))
            set_enabled(root, "files", False)
            self.assertFalse(enabled(root, "files"))
            self.assertTrue(enabled(root, "memory"))
            set_enabled(root, "files", True)
            self.assertTrue(enabled(root, "files"))

    def _archive(self, directory: Path, *, unsafe=False):
        file = directory / "first-party.zip"
        manifest = {
            "api": 1,
            "id": "example_module",
            "version": "0.1.0",
            "name": "Example module",
            "entrypoint": "plugin.py",
            "permissions": ["ui"],
        }
        with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("module.json", json.dumps(manifest))
            z.writestr("plugin.py",
                       "def register(api):\n    api.add_action('Example', lambda: None)\n")
            if unsafe:
                z.writestr("../injected.py", "print('no')")
        return file

    def test_first_party_module_install_and_opt_in_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = self._archive(root)
            item = install_verified_module(
                root, file, module_id="example_module", version="0.1.0")
            self.assertEqual(item["id"], "example_module")
            self.assertEqual(load_state(root)["installed"]["example_module"], "0.1.0")
            self.assertFalse(enabled(root, "example_module"))
            actions, errors = load_module_actions(root)
            self.assertEqual(actions, [])
            set_enabled(root, "example_module", True)
            actions, errors = load_module_actions(root)
            self.assertEqual([x.label for x in actions], ["Example"])
            self.assertEqual(errors, [])
            again = install_verified_module(
                root, file, module_id="example_module", version="0.1.0")
            self.assertEqual(again, item)

    def test_reject_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = self._archive(root, unsafe=True)
            with self.assertRaises(ModuleError):
                install_verified_module(root, file,
                    module_id="example_module", version="0.1.0")
            self.assertFalse((root / "injected.py").exists())


if __name__ == "__main__":
    unittest.main()
