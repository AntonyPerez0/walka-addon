"""A global STOP hotkey (Windows and macOS).

While you are in a game the dashboard's STOP button is an alt-tab away, so
the companion registers a system-wide key combination that stops the belt
from anywhere. The system delivers a registered hotkey to this program only -
the key press is not sent to the game.

The treadmill's own remote is still the primary way to stop the belt.

On Windows the hotkey lives on a background thread with a message loop of
its own. The same kind of thread, with a hidden window instead of a hotkey,
is how the companion learns that Windows is logging off or shutting down -
or going to sleep - (``SessionEndWatcher``), so that it can stop the belt
first.

On macOS a hotkey is registered with the system's Carbon library, which
needs no permission from the user. It hands key presses to the program's
main thread and nowhere else, and only when asked: ``GlobalHotkey.pump``
has to be called there a good many times a second (``App`` does).
"""

from __future__ import annotations

import ctypes
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
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004  # the computer is about to sleep or hibernate

# (A Mac keyboard has Option where a PC has Alt, and Command where it has
# the Windows key: both spellings mean the same key on either.)
_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "option": MOD_ALT,
    "opt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
    "command": MOD_WIN,
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


_KEY_NAMES = {MOD_CONTROL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift", MOD_WIN: "Win"}
# ... and as the keys are called on a Mac keyboard.
_MAC_KEY_NAMES = {MOD_CONTROL: "Control", MOD_ALT: "Option", MOD_SHIFT: "Shift", MOD_WIN: "Command"}


def describe(text: str, platform: str | None = None) -> str:
    """``"ctrl+alt+x"`` -> ``"Ctrl+Alt+X"`` for display (``"Control+Option+X"``
    on a Mac, where that is what the keys are called)."""
    names = _MAC_KEY_NAMES if (platform or sys.platform) == "darwin" else _KEY_NAMES
    parts = [part.strip() for part in (text or "").split("+")]
    return "+".join(names[_MODIFIERS[p.lower()]] if p.lower() in _MODIFIERS else p.capitalize() for p in parts)


# ---- macOS: the same combinations in Carbon's terms ----------------------------------

_MAC_MODIFIERS = {MOD_WIN: 0x0100, MOD_SHIFT: 0x0200, MOD_ALT: 0x0800, MOD_CONTROL: 0x1000}  # cmdKey, shiftKey, ...

# Windows virtual-key code -> macOS virtual key code (kVK_... in the system's
# Events.h). macOS numbers keys by their position on a US keyboard.
_MAC_KEY_CODES: dict[int, int] = {
    **dict(
        zip(
            b"ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            bytes.fromhex("00 0b 08 02 0e 03 05 04 22 26 28 25 2e 2d 1f 23 0c 0f 01 11 20 09 0d 07 10 06"),
            strict=True,
        )
    ),
    **dict(zip(b"0123456789", bytes.fromhex("1d 12 13 14 15 17 16 1a 1c 19"), strict=True)),
    0x20: 0x31,  # space
    0x08: 0x33,  # backspace ("delete" on a Mac keyboard)
    0x09: 0x30,  # tab
    0x0D: 0x24,  # enter
    0x1B: 0x35,  # escape
    0x21: 0x74,  # page up
    0x22: 0x79,  # page down
    0x23: 0x77,  # end
    0x24: 0x73,  # home
    0x2D: 0x72,  # insert (the Help key of old Mac keyboards)
    0x2E: 0x75,  # delete ("forward delete")
    # A PC keyboard's Pause and Scroll Lock arrive as F15 and F14.
    0x13: 0x71,
    0x91: 0x6B,
    # F1 ... F20 (a Mac has no F21-F24).
    **dict(
        zip(
            range(0x70, 0x84), bytes.fromhex("7a 78 63 76 60 61 62 64 65 6d 67 6f 69 6b 71 6a 40 4f 50 5a"), strict=True
        )
    ),
    # The number pad's 0 ... 9.
    **dict(zip(range(0x60, 0x6A), bytes.fromhex("52 53 54 55 56 57 58 59 5b 5c"), strict=True)),
}


def mac_hotkey(modifiers: int, key: int) -> tuple[int, int] | None:
    """What ``parse_hotkey`` returns, as Carbon wants it: ``(modifiers,
    key code)``. None for a key a Mac does not have."""
    code = _MAC_KEY_CODES.get(key)
    if code is None:
        return None
    return sum(mac for windows, mac in _MAC_MODIFIERS.items() if modifiers & windows), code


_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb'
_EVENT_HOTKEY_PRESSED = 5
_PARAM_DIRECT_OBJECT = 0x2D2D2D2D  # '----'
_TYPE_HOTKEY_ID = 0x686B6964  # 'hkid'
_HOTKEY_SIGNATURE = 0x57616C6B  # 'Walk'
_HOTKEY_EXISTS = -9878  # eventHotKeyExistsErr
_NOT_HANDLED = -9874  # eventNotHandledErr
_PUMP_AT_MOST = 32  # events handed on per call of pump()


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_EventHandler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_carbon: ctypes.CDLL | None = None


def _load_carbon() -> ctypes.CDLL:
    """The Carbon library, with the signatures of what is used (without
    them ctypes would pass pointers as 32-bit ints)."""
    global _carbon
    if _carbon is not None:
        return _carbon
    carbon = ctypes.CDLL(_CARBON)
    pointer, status, count = ctypes.c_void_p, ctypes.c_int32, ctypes.c_ulong
    carbon.GetApplicationEventTarget.argtypes = []
    carbon.GetApplicationEventTarget.restype = pointer
    carbon.GetEventDispatcherTarget.argtypes = []
    carbon.GetEventDispatcherTarget.restype = pointer
    carbon.InstallEventHandler.argtypes = [
        pointer,
        _EventHandler,
        count,
        ctypes.POINTER(_EventTypeSpec),
        pointer,
        ctypes.POINTER(pointer),
    ]
    carbon.InstallEventHandler.restype = status
    carbon.RemoveEventHandler.argtypes = [pointer]
    carbon.RemoveEventHandler.restype = status
    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        _EventHotKeyID,
        pointer,
        ctypes.c_uint32,
        ctypes.POINTER(pointer),
    ]
    carbon.RegisterEventHotKey.restype = status
    carbon.UnregisterEventHotKey.argtypes = [pointer]
    carbon.UnregisterEventHotKey.restype = status
    carbon.GetEventParameter.argtypes = [
        pointer,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        count,
        ctypes.POINTER(count),
        pointer,
    ]
    carbon.GetEventParameter.restype = status
    carbon.ReceiveNextEvent.argtypes = [
        count,
        ctypes.POINTER(_EventTypeSpec),
        ctypes.c_double,
        ctypes.c_ubyte,
        ctypes.POINTER(pointer),
    ]
    carbon.ReceiveNextEvent.restype = status
    carbon.SendEventToEventTarget.argtypes = [pointer, pointer]
    carbon.SendEventToEventTarget.restype = status
    carbon.ReleaseEvent.argtypes = [pointer]
    carbon.ReleaseEvent.restype = None
    _carbon = carbon
    return carbon


class _CarbonHotkey:
    """One key combination registered with macOS.

    Carbon puts a press of it in the event queue of the program's main
    thread; ``pump`` takes what is waiting there and hands it on, and that
    is when ``callback`` runs.
    """

    _serial = itertools.count(1)

    def __init__(self, modifiers: int, key_code: int, callback: Callable[[], None]) -> None:
        carbon = self._carbon = _load_carbon()
        self._id = next(self._serial)
        self._hotkey = ctypes.c_void_p()
        self._installed = ctypes.c_void_p()

        def on_event(_next_handler: int, event: int, _user_data: int) -> int:
            # Every handler is offered every hotkey of this program, the one
            # installed last first: is it this one's?
            pressed = _EventHotKeyID()
            status = carbon.GetEventParameter(
                event, _PARAM_DIRECT_OBJECT, _TYPE_HOTKEY_ID, None, ctypes.sizeof(pressed), None, ctypes.byref(pressed)
            )
            if status != 0 or pressed.signature != _HOTKEY_SIGNATURE or pressed.id != self._id:
                return _NOT_HANDLED  # ... no: Carbon then offers it to the next handler
            try:
                callback()
            except Exception:
                log.exception("stop hotkey handler failed")
            return 0

        self._handler = _EventHandler(on_event)  # (kept: Carbon holds a pointer to it)
        target = carbon.GetApplicationEventTarget()
        kind = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        status = carbon.InstallEventHandler(
            target, self._handler, 1, ctypes.byref(kind), None, ctypes.byref(self._installed)
        )
        if status != 0:
            raise OSError(status, "macOS did not take the hotkey handler")
        status = carbon.RegisterEventHotKey(
            key_code, modifiers, _EventHotKeyID(_HOTKEY_SIGNATURE, self._id), target, 0, ctypes.byref(self._hotkey)
        )
        if status != 0:
            carbon.RemoveEventHandler(self._installed)
            raise OSError(status, "macOS did not register the hotkey")

    def pump(self) -> None:
        carbon = self._carbon
        dispatcher = carbon.GetEventDispatcherTarget()
        event = ctypes.c_void_p()
        for _ in range(_PUMP_AT_MOST):
            # (No waiting: whatever is there now, or nothing.)
            if carbon.ReceiveNextEvent(0, None, 0.0, 1, ctypes.byref(event)) != 0 or not event:
                return
            carbon.SendEventToEventTarget(event, dispatcher)
            carbon.ReleaseEvent(event)

    def close(self) -> None:
        if self._hotkey:
            self._carbon.UnregisterEventHotKey(self._hotkey)
            self._hotkey = ctypes.c_void_p()
        if self._installed:
            self._carbon.RemoveEventHandler(self._installed)
            self._installed = ctypes.c_void_p()


# What ``GlobalHotkey.error`` says where there is no way to register one.
UNSUPPORTED = "global hotkeys are only available on Windows and macOS"
# Does ``GlobalHotkey.pump`` have to be called for the hotkey to work?
NEEDS_PUMP = sys.platform == "darwin"
PUMP_INTERVAL_S = 0.05


class GlobalHotkey:
    """Calls ``callback`` when the combination is pressed: on a background
    thread on Windows, from within ``pump`` on macOS."""

    def __init__(self, combination: str, callback: Callable[[], None]) -> None:
        self.combination = combination
        self.callback = callback
        self.error = ""
        self.active = False
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._mac: _CarbonHotkey | None = None

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
        if sys.platform == "darwin":
            return self._start_mac(*parsed)
        if sys.platform != "win32":
            self.error = UNSUPPORTED
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, args=parsed, name="walka-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self.active

    def _run(self, modifiers: int, key: int) -> None:
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

    def _start_mac(self, modifiers: int, key: int) -> bool:
        translated = mac_hotkey(modifiers, key)
        if translated is None:
            self.error = f"{describe(self.combination)} has a key that Mac keyboards do not have"
            return False
        if threading.current_thread() is not threading.main_thread():
            # (Carbon delivers key presses to the main thread only.)
            self.error = "on macOS a hotkey can only be registered from the program's main thread"
            return False
        try:
            self._mac = _CarbonHotkey(*translated, self.callback)
        except Exception as exc:  # whatever macOS objects to: carry on without
            taken = isinstance(exc, OSError) and exc.errno == _HOTKEY_EXISTS
            self.error = (
                f"{describe(self.combination)} is already used by another program"
                if taken
                else f"macOS did not accept {describe(self.combination)} ({exc})"
            )
            return False
        self.active = True
        self.error = ""
        return True

    def pump(self) -> None:
        """macOS: let key presses that have arrived reach ``callback``. To
        be called on the main thread, a good many times a second, for as
        long as the hotkey is to work. Does nothing on Windows, where the
        hotkey has a thread of its own."""
        mac = self._mac
        if mac is None:
            return
        try:
            mac.pump()
        except Exception as exc:
            log.debug("could not ask macOS for key presses: %s", exc)

    def stop(self) -> None:
        mac, self._mac = self._mac, None
        if mac is not None:
            self.active = False
            try:
                mac.close()
            except Exception as exc:
                log.debug("could not give the hotkey back to macOS: %s", exc)
            return
        thread, self._thread = self._thread, None
        if thread is None or self._thread_id is None or sys.platform != "win32":
            return

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


def is_suspend(message: int, wparam: int) -> bool:
    """Is this the message that says the computer is about to sleep?"""
    return message == WM_POWERBROADCAST and wparam == PBT_APMSUSPEND


class SessionEndWatcher:
    """Calls ``callback`` when Windows logs the user off or shuts down, and
    ``on_suspend`` (if given) when the computer is about to sleep.

    A console program is normally told through its console control handler -
    but not once it has loaded user32 or gdi32, as this one has for the
    hotkey and the screen capture: Windows then tells its windows instead.
    So the companion keeps one window that is never shown, on a thread of
    its own, just to be told. (This is the remedy Microsoft's documentation
    of SetConsoleCtrlHandler gives.)

    ``callback`` runs on that thread. It should return only when the
    process may be killed: Windows waits a few seconds for the answer and
    can end the process as soon as it has it.

    ``on_suspend`` runs there too, and should return once the belt has been
    told to stop: Windows gives a program about two seconds before it
    sleeps. (A computer with "Modern Standby" may go to sleep without
    telling programs at all. The companion then notices when it wakes:
    ``App._stop_after_gap``.)
    """

    _serial = itertools.count(1)

    def __init__(self, callback: Callable[[], None], on_suspend: Callable[[], None] | None = None) -> None:
        self.callback = callback
        self.on_suspend = on_suspend
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
        if is_suspend(message, wparam):
            if self.on_suspend is not None:
                try:
                    self.on_suspend()
                except Exception:
                    log.exception("stopping the belt before the computer sleeps failed")
            return 1  # (TRUE: the message was dealt with)
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
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
