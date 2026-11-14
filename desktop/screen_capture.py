"""User-approved, one-shot primary-screen capture for a pending chat attachment.

Calling code MUST obtain per-capture owner approval first. This module never
captures in the background, transmits data, or invokes an AI model.
"""
from __future__ import annotations

from pathlib import Path
import sys
import uuid


class ScreenCaptureError(RuntimeError):
    pass


def capture_primary_screen(folder: Path, *, grabber=None,
                           platform: str | None = None) -> Path:
    if (platform or sys.platform) != "win32":
        raise ScreenCaptureError("Screen capture currently requires Windows")
    if grabber is None:
        try:
            from PIL import ImageGrab
            grabber = ImageGrab.grab
        except ImportError as exc:
            raise ScreenCaptureError("Pillow screen capture is not installed") from exc
    directory = Path(folder)
    if directory.is_symlink():
        raise ScreenCaptureError("Screenshot directory cannot be a link")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("screen-" + uuid.uuid4().hex + ".png")
    try:
        image = grabber()
        if not (0 < image.width <= 16384 and 0 < image.height <= 16384):
            raise ScreenCaptureError("Unsupported screen capture dimensions")
        image.save(target, format="PNG")
        if not (0 < target.stat().st_size <= 15 * 1024 * 1024):
            raise ScreenCaptureError("Screenshot exceeds the attachment limit")
        return target
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, ScreenCaptureError):
            raise
        raise ScreenCaptureError("Could not capture the primary screen") from exc
