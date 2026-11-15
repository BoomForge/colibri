"""Windows system tray lifecycle for Milo.

Tray callbacks run on pystray's worker, not Tk's GUI thread. They only post
small fixed-label events to Milo's existing main-thread event queue. Milo's
inference engine, conversations and documents stay open while its UI hides.

On Windows, pystray uses the native notification area. There is deliberately
no remote service, polling, or secondary persistent process.
"""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading


class MiloTray:
    def __init__(self, events: queue.Queue, icon_path: Path,
                 fallback_image: Path | None = None) -> None:
        self._events = events
        self._icon_path = Path(icon_path)
        self._fallback_image = Path(fallback_image) if fallback_image else None
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._icon = None
        self._thread = None
        self.error = ""

    @property
    def ready(self) -> bool:
        """Never hide Milo if its restore mechanism is not running."""
        return self._ready.is_set() and not self._stopping.is_set()

    @staticmethod
    def _image(primary: Path, fallback: Path | None):
        from PIL import Image, ImageDraw
        for candidate in (primary, fallback):
            if candidate is None or not candidate.is_file():
                continue
            try:
                if candidate.stat().st_size > 12 * 1024 * 1024:
                    continue
                with Image.open(candidate) as original:
                    return original.convert("RGBA").resize((64, 64))
            except (ValueError, OSError):
                continue
        # Safe offline fallback even if an asset is missing in a local build.
        icon = Image.new("RGBA", (64, 64), (42, 29, 67, 255))
        pen = ImageDraw.Draw(icon)
        pen.rounded_rectangle((2, 2, 61, 61), radius=15,
                              outline=(187, 129, 255, 255), width=4)
        pen.text((23, 21), "M", fill=(235, 217, 255, 255))
        return icon

    def start(self) -> bool:
        """Start exactly one OS tray host; never run Tk on this thread."""
        if sys.platform != "win32":
            self.error = "Windows notification area is required"
            return False
        if self._thread is not None:
            return self._thread.is_alive()
        try:
            import pystray
            icon_image = self._image(self._icon_path, self._fallback_image)
            menu = pystray.Menu(
                pystray.MenuItem("Open Milo", self._open, default=True),
                pystray.MenuItem("Close Milo", self._exit),
            )
            self._icon = pystray.Icon("Pixelwolf.Milo", icon_image,
                                     "Milo — Personal Assistant", menu)
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run, name="milo-windows-tray", daemon=True)
            self._thread.start()
            return True
        except Exception:
            self._icon = None
            self.error = "The Windows tray service could not start"
            return False

    def _run(self):
        icon = self._icon
        try:
            # run is blocking; native Windows tray hosting is supported from
            # this worker. pystray calls setup after the tray icon is ready.
            icon.run(setup=self._setup)
        except Exception:
            self.error = "Windows could not display Milo's tray icon"
            if not self._stopping.is_set():
                self._events.put(("tray_unavailable",))
        finally:
            self._ready.clear()

    def _setup(self, icon):
        if not self._stopping.is_set():
            icon.visible = True
            self._ready.set()

    def _open(self, _icon=None, _item=None):
        if not self._stopping.is_set():
            self._events.put(("tray_open",))

    def _exit(self, _icon=None, _item=None):
        if not self._stopping.is_set():
            self._events.put(("tray_exit",))

    def stop(self) -> None:
        self._stopping.set()
        self._ready.clear()
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except (RuntimeError, OSError):
                pass
