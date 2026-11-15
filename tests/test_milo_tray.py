"""Tray behavior contract without creating real Windows notification icons."""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from desktop.tray_service import MiloTray


class FakeMenuItem:
    def __init__(self, label, callback, default=False):
        self.label = label
        self.callback = callback
        self.default = default


class FakeMenu:
    def __init__(self, *items):
        self.items = items


class FakeIcon:
    instances = []

    def __init__(self, name, image, title, menu):
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.visible = False
        self.exited = threading.Event()
        FakeIcon.instances.append(self)

    def run(self, setup=None):
        setup(self)
        self.exited.wait(5)

    def stop(self):
        self.exited.set()


class TrayTests(unittest.TestCase):
    def test_not_available_on_other_platforms(self):
        events = queue.Queue()
        with patch("desktop.tray_service.sys.platform", "linux"):
            icon = MiloTray(events, Path("missing.ico"))
            self.assertFalse(icon.start())
            self.assertFalse(icon.ready)

    def test_tray_open_and_close_have_separate_events_not_direct_tk_calls(self):
        from PIL import Image
        FakeIcon.instances.clear()
        pystray = types.SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem,
                                        Icon=FakeIcon)
        events = queue.Queue()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Milo.png"
            Image.new("RGBA", (32, 32), (100, 40, 140, 255)).save(path)
            with patch("desktop.tray_service.sys.platform", "win32"):
                with patch.dict(sys.modules, {"pystray": pystray}):
                    tray = MiloTray(events, path)
                    self.assertTrue(tray.start())
                    self.assertTrue(tray._ready.wait(2.0))
                    self.assertTrue(tray.ready)
                    try:
                        self.assertEqual(len(FakeIcon.instances), 1)
                        win_icon = FakeIcon.instances[0]
                        labels = [item.label for item in win_icon.menu.items]
                        self.assertEqual(labels, ["Open Milo", "Close Milo"])
                        self.assertTrue(win_icon.menu.items[0].default)
                        win_icon.menu.items[0].callback()
                        win_icon.menu.items[1].callback()
                        self.assertEqual(events.get_nowait(), ("tray_open",))
                        self.assertEqual(events.get_nowait(), ("tray_exit",))
                        self.assertTrue(events.empty())
                    finally:
                        tray.stop()
                    self.assertFalse(tray.ready)
                    self.assertTrue(win_icon.exited.is_set())

    def test_tray_host_failure_never_reports_safe_to_hide(self):
        class BadIcon(FakeIcon):
            def run(self, setup=None):
                raise RuntimeError("OS icon host error")
        fake = types.SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem,
                                     Icon=BadIcon)
        events = queue.Queue()
        with patch("desktop.tray_service.sys.platform", "win32"):
            with patch.dict(sys.modules, {"pystray": fake}):
                tray = MiloTray(events, Path("missing.ico"))
                try:
                    self.assertTrue(tray.start())
                    self.assertEqual(events.get(timeout=2), ("tray_unavailable",))
                    self.assertFalse(tray.ready)
                finally:
                    tray.stop()


if __name__ == "__main__":
    unittest.main()
