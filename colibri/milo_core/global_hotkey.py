"""Windows-only global quick-chat shortcut with no keyboard interception.

Registers Ctrl+Shift+M using the operating-system hotkey API. It does not
record keys or install a keyboard hook. The worker only posts a UI event; Tk
widgets are always touched on the main thread.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import queue
import sys
import threading

HOTKEY_ID = 0x4D10
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_M = ord("M")


class GlobalQuickChatHotkey:
    def __init__(self, events: queue.Queue):
        self.events = events
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()

    def start(self) -> bool:
        """Start the message-only shortcut worker on Windows. Never blocks Tk."""
        if sys.platform != "win32" or self._stop.is_set():
            return False
        if self._thread is not None:
            return self._thread.is_alive()
        self._thread = threading.Thread(
            target=self._run, name="milo-global-quick-chat", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                        wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                      wintypes.HWND, wintypes.UINT,
                                      wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = bool(user32.RegisterHotKey(
            None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_M))
        self._ready.set()
        if not registered:
            self.events.put(("hotkey_unavailable",))
            return
        try:
            message = wintypes.MSG()
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if (message.message == WM_HOTKEY
                        and message.wParam == HOTKEY_ID):
                    self.events.put(("quick_chat",))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        self._stop.set()
        self._ready.wait(timeout=1.5)
        if self._thread_id is not None and sys.platform == "win32":
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW.argtypes = [
                wintypes.DWORD, wintypes.UINT, wintypes.WPARAM,
                wintypes.LPARAM]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
