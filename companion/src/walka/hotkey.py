"""A global STOP hotkey (Windows).

While you are in a game the dashboard's STOP button is an alt-tab away, so
the companion registers a system-wide key combination that stops the belt
from anywhere. Windows delivers a registered hotkey to this program only -
the key press is not sent to the game.

The treadmill's own remote is still the primary way to stop the belt.

The same kind of background thread, with a hidden window instead of a
hotkey, is how the companion learns that Windows is logging off or shutting
down (``SessionEndWatcher``), so that it can stop the belt first.
"""

from __future__ import annotations

import itertools
import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016

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


# ---- the end of the Windows session ------------------------------------------------


def session_end_reply(message: int, wparam: int) -> tuple[int | None, bool]:
    """How the hidden window answers a message: ``(reply, stop now?)``.

    ``reply`` is ``None`` for every message that is left to Windows.
    """
    if message == WM_QUERYENDSESSION:
        # "May the session end?" Yes. Nothing is done yet: another program
        # can still call the shutdown off.
        return 1, False
    if message == WM_ENDSESSION:
        # wParam says whether the session really is ending. If it is,
        # Windows may end this process as soon as the message is answered.
        return 0, bool(wparam)
    if message == WM_CLOSE:
        # Something asked the hidden window to close. Left to Windows that
        # would destroy it, and the end of the session would go unnoticed.
        return 0, False
    return None, False


class SessionEndWatcher:
    """Calls ``callback`` when Windows logs the user off or shuts down.

    A console program is normally told through its console control handler -
    but not once it has loaded user32 or gdi32, as this one has for the
    hotkey and the screen capture: Windows then tells its windows instead.
    So the companion keeps one window that is never shown, on a thread of
    its own, just to be told. (This is the remedy Microsoft's documentation
    of SetConsoleCtrlHandler gives.)

    ``callback`` runs on that thread. It should return only when the
    process may be killed: Windows waits a few seconds for the answer and
    can end the process as soon as it has it.
    """

    _serial = itertools.count(1)

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.error = ""
        self.active = False
        self.hwnd: int | None = None  # the hidden window, while it exists
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._window_procedure: object = None  # keeps the ctypes callback alive

    def handle(self, message: int, wparam: int) -> int | None:
        """What the window procedure does with one message; returns the
        reply, or ``None`` to leave the message to Windows."""
        reply, stop_now = session_end_reply(message, wparam)
        if stop_now:
            try:
                self.callback()
            except Exception:
                log.exception("stopping for the end of the Windows session failed")
        return reply

    def start(self) -> bool:
        """Create the window. Returns ``False`` (see ``error``) if that is
        not possible; the companion works without it."""
        if sys.platform != "win32":
            self.error = "only needed on Windows"
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="walka-session-end", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self.active

    def _run(self) -> None:
        try:
            self._serve()
        except Exception as exc:  # whatever Windows objects to: carry on without
            self.error = f"could not watch for Windows shutting down: {exc}"
            log.debug(self.error)
        finally:
            self.active = False
            self.hwnd = None
            self._ready.set()

    def _serve(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        # Declare signatures: without them ctypes would pass handles and
        # message parameters as 32-bit ints, which truncates them on 64-bit
        # Windows.
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = lresult
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = lresult
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        def window_procedure(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            reply = self.handle(message, wparam)
            if reply is not None:
                return reply
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._window_procedure = wndproc(window_procedure)
        instance = kernel32.GetModuleHandleW(None)
        # A window class keeps its window procedure for as long as it is
        # registered, so every watcher registers a class of its own.
        class_name = f"WalkaSessionEnd{next(self._serial)}"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._window_procedure
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = None
        try:
            # An ordinary top-level window that is never shown. (Not a
            # "message-only" window: those are not told that the session is
            # ending.)
            hwnd = user32.CreateWindowExW(0, class_name, "Walka", 0, 0, 0, 0, 0, None, None, instance, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._thread_id = kernel32.GetCurrentThreadId()
            self.hwnd = hwnd
            self.active = True
            self._ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self.active = False
            self.hwnd = None
            if hwnd:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, instance)

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None or self._thread_id is None or sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
