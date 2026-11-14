"""Milo's local executable discovery: bounded, owner-approved Windows matching."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.app_discovery import (
    AppMatch, find_installed_apps, normalise_name,
)
from colibri.milo_core.pc_broker import (
    PCActionError, execute_pc_plan, parse_pc_request, plan_discovered_launch,
)


class AutoDiscoverTests(unittest.TestCase):
    def test_unknown_apps_route_to_discovery_without_launch(self):
        examples = [
            ("open Blender", "blender"),
            ("Milo, can you open OBS Studio?", "obs studio"),
            ("please launch VRoid Studio", "vroid studio"),
            ("open steam.exe", "steam"),
            ("start Firefox", "firefox"),
        ]
        for prompt, query in examples:
            with self.subTest(prompt=prompt):
                plan = parse_pc_request(prompt)
                self.assertIsNotNone(plan)
                self.assertEqual(plan.kind, "discover_app")
                self.assertEqual(plan.args, (query,))
        self.assertEqual(parse_pc_request("open downloads").kind, "open_folder")
        self.assertEqual(parse_pc_request("open notepad").kind, "launch_app")
        self.assertIsNone(parse_pc_request("open sketch.png"))
        self.assertIsNone(parse_pc_request("open report.pdf"))

    def test_never_routes_arbitrary_commands_or_files_to_discovery(self):
        for prompt in (
            "how to open Blender?", "explain Blender",
            "run powershell", "open cmd", "open task manager",
            "open a.bat", "open script.ps1", "open C:\\tools\\evil.exe",
            "open ..\\secret.exe", "open https://localhost/test",
            "open example.com/file.exe", "open settings",
        ):
            with self.subTest(prompt=prompt):
                plan = parse_pc_request(prompt)
                self.assertTrue(
                    plan is None or plan.kind != "discover_app",
                    f"unsafe discovery: {prompt!r} => {plan!r}")

    def test_registry_and_installed_folder_matches_are_real_exes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Program Files"
            blender = root / "Blender Foundation" / "Blender 4.5" / "blender.exe"
            blender.parent.mkdir(parents=True)
            blender.write_bytes(b"exe placeholder")
            obs = root / "obs-studio" / "bin" / "64bit" / "obs64.exe"
            obs.parent.mkdir(parents=True)
            obs.write_bytes(b"obs exe placeholder")
            wrong = root / "Blender Foundation" / "uninstall.exe"
            wrong.write_bytes(b"uninstaller")
            (root / "Blender Foundation" / "Blender.bat").write_text("bad")
            candidates = find_installed_apps(
                "blender", roots=(root,), registry_entries=[])
            self.assertEqual([p.path for p in candidates], [blender])
            obs_candidates = find_installed_apps(
                "OBS Studio", roots=(root,), registry_entries=[])
            self.assertEqual([p.path for p in obs_candidates], [obs])
            reg_only = find_installed_apps(
                "Blender", roots=(), registry_entries=[
                    (blender, "Blender 4.5", "Windows App Paths")])
            self.assertEqual(len(reg_only), 1)
            self.assertEqual(reg_only[0].path, blender)

    def test_ambiguous_matches_return_separate_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for label in ("Blender 4.0", "Blender 4.5"):
                app = root / label / "blender.exe"
                app.parent.mkdir()
                app.write_bytes(b"placeholder")
            found = find_installed_apps(
                "blender", roots=(root,), registry_entries=[])
            self.assertEqual(len(found), 2)
            self.assertNotEqual(found[0].path, found[1].path)

    def test_search_is_bounded_and_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            root.mkdir(exist_ok=True)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "blender.exe").write_bytes(b"binary")
            hidden = root / "search"
            hidden.mkdir()
            try:
                (hidden / "not-really-an-exe").symlink_to(
                    elsewhere, target_is_directory=True)
            except OSError:
                pass
            self.assertEqual(find_installed_apps(
                "blender", roots=(hidden,), registry_entries=[]), [])
            with self.assertRaises(ValueError):
                find_installed_apps(
                    "blender", roots=(hidden,), registry_entries=[],
                    max_visited=0)

    def test_one_time_approval_revalidates_discovered_executable(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "Blender.exe"
            exe.write_bytes(b"first")
            match = find_installed_apps(
                "blender", roots=(Path(d),), registry_entries=[])[0]
            plan = plan_discovered_launch("blender", match)
            called = []
            with self.assertRaises(PermissionError):
                execute_pc_plan(plan, platform="win32", app_launcher=called.append)
            self.assertEqual(called, [])
            result = execute_pc_plan(
                plan, approved=True, platform="win32",
                app_launcher=called.append)
            self.assertIn("Launched blender", result)
            self.assertEqual(called, [str(exe.resolve())])
            exe.write_bytes(b"changed")
            with self.assertRaises(PCActionError):
                execute_pc_plan(
                    plan, approved=True, platform="win32",
                    app_launcher=called.append)
            self.assertEqual(len(called), 1)

    def test_discovery_does_not_treat_unsafe_names_as_apps(self):
        for text in ("PowerShell", "cmd", "Administrator", "settings"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    normalise_name(text)


if __name__ == "__main__":
    unittest.main()
