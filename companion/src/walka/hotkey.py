"""A global STOP hotkey (Windows).

While you are in a game the dashboard's STOP button is an alt-tab away, so
the companion registers a system-wide key combination that stops the belt
from anywhere. Windows delivers a registered hotkey to this program only -
the key press is not sent to the game.

The treadmill's own remote is still the primary way to stop the belt.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

_KEYS = {
    "space": 0x20,
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "pause": 0x13,
    "escape": 0x1B,
    "esc": 0x1B,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "insert": 0x2D,
    "delete": 0x2E,
    "scrolllock": 0x91,
}

# Keys that are safe to grab without a modifier (nobody types with them).
_BARE_OK = {0x13, 0x91} | set(range(0x7C, 0x88))  # Pause, ScrollLock, F13-F24


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """Parse ``"ctrl+alt+x"`` into ``(modifiers, virtual_key)``.

    Returns ``None`` for an empty, malformed or unsafe combination (a plain
    letter without Ctrl/Alt/Win would swallow ordinary typing).
    """
    parts = [part.strip().lower() for part in (text or "").split("+")]
    if not parts or any(not part for part in parts):
        return None
    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
            continue
        if key is not None:
            return None  # two non-modifier keys
        if len(part) == 1 and (part.isascii() and part.isalnum()):
            key = ord(part.upper())
        elif part in _KEYS:
            key = _KEYS[part]
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            key = 0x70 + int(part[1:]) - 1
        elif part.startswith("numpad") and part[6:].isdigit() and 0 <= int(part[6:]) <= 9:
            key = 0x60 + int(part[6:])
        else:
            return None
    if key is None:
        return None
    if not modifiers & (MOD_CONTROL | MOD_ALT | MOD_WIN) and key not in _BARE_OK:
        return None
    return modifiers, key


def describe(text: str) -> str:
    """``"ctrl+alt+x"`` -> ``"Ctrl+Alt+X"`` for display."""
    names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    return "+".join(names.get(p.strip().lower(), p.strip().capitalize()) for p in (text or "").split("+"))


class GlobalHotkey:
    """Calls ``callback`` (on a background thread) when the combination is pressed."""

    def __init__(self, combination: str, callback: Callable[[], None]) -> None:
        self.combination = combination
        self.callback = callback
        self.error = ""
        self.active = False
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def start(self) -> bool:
        """Register the hotkey. Returns ``False`` (see ``error``) if it could
        not be registered; the companion works without it."""
        if not self.combination:
            self.error = "disabled"
            return False
        parsed = parse_hotkey(self.combination)
        if parsed is None:
            self.error = f"'{self.combination}' is not a usable key combination"
            return False
        if sys.platform != "win32":
            self.error = "global hotkeys are only available on Windows"
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, args=parsed, name="walka-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self.active

    def _run(self, modifiers: int, key: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        hotkey_id = 1
        self._thread_id = kernel32.GetCurrentThreadId()
        # The hotkey belongs to the thread that registers it, and that thread
        # must run the message loop.
        if not user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, key):
            self.error = f"{describe(self.combination)} is already used by another program"
            self._ready.set()
            return
        self.active = True
        self.error = ""
        self._ready.set()
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == hotkey_id:
                    try:
                        self.callback()
                    except Exception:
                        log.exception("stop hotkey handler failed")
        finally:
            user32.UnregisterHotKey(None, hotkey_id)
            self.active = False

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None or self._thread_id is None or sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "combination": describe(self.combination) if self.combination else "",
            "active": self.active,
            "error": self.error,
        }
