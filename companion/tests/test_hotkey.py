"""The global STOP hotkey: which key combinations are accepted. (Registering
one is Windows-only - see the checklist in the test report.) And the hidden
window that hears Windows logging off or shutting down."""

from __future__ import annotations

import sys
import threading

import pytest

from walka import hotkey
from walka.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    WM_CLOSE,
    WM_ENDSESSION,
    WM_QUERYENDSESSION,
    GlobalHotkey,
    SessionEndWatcher,
    parse_hotkey,
    session_end_reply,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ctrl+alt+x", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("Ctrl + Alt + X", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("control+shift+f9", (MOD_CONTROL | MOD_SHIFT, 0x78)),
        ("win+space", (MOD_WIN, 0x20)),
        ("alt+7", (MOD_ALT, ord("7"))),
        ("ctrl+numpad0", (MOD_CONTROL, 0x60)),
        ("ctrl+f24", (MOD_CONTROL, 0x87)),
        ("pause", (0, 0x13)),  # keys nobody types with may be used bare
        ("scrolllock", (0, 0x91)),
        ("f13", (0, 0x7C)),
        ("shift+pause", (MOD_SHIFT, 0x13)),
    ],
)
def test_usable_combinations(text: str, expected: tuple[int, int]):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "x",  # would swallow ordinary typing in the game
        "shift+x",
        "space",
        "f5",
        "enter",
        "ctrl",
        "ctrl+alt",
        "ctrl+x+y",
        "ctrl++",
        "+",
        "ctrl+",
        "ctrl+f0",
        "ctrl+f25",
        "ctrl+numpad10",
        "ctrl+banana",
        "ctrl+é",
        "ctrl+xx",
    ],
)
def test_unusable_combinations_are_refused(text):
    assert parse_hotkey(text) is None


def test_describe():
    assert hotkey.describe("ctrl+alt+x") == "Ctrl+Alt+X"
    assert hotkey.describe(" control + shift + f9 ") == "Ctrl+Shift+F9"
    assert hotkey.describe("") == ""


def test_a_hotkey_that_cannot_be_registered_reports_why_and_never_raises():
    calls: list[int] = []
    disabled = GlobalHotkey("", lambda: calls.append(1))
    assert disabled.start() is False and disabled.error == "disabled" and not disabled.active
    unusable = GlobalHotkey("x", lambda: calls.append(1))
    assert unusable.start() is False and "not a usable" in unusable.error
    for hk in (disabled, unusable):
        hk.stop()  # stopping what never started is fine
        assert hk.to_dict()["active"] is False
    if sys.platform != "win32":
        elsewhere = GlobalHotkey("ctrl+alt+x", lambda: calls.append(1))
        assert elsewhere.start() is False and "Windows" in elsewhere.error
        assert elsewhere.to_dict() == {"combination": "Ctrl+Alt+X", "active": False, "error": elsewhere.error}
        elsewhere.stop()
    assert calls == []


# ---- the end of the Windows session -----------------------------------------------------

WM_USER = 0x0400


def test_what_the_hidden_window_answers():
    # "May the session end?" - yes, and nothing is stopped yet (it can still be called off).
    assert session_end_reply(WM_QUERYENDSESSION, 0) == (1, False)
    assert session_end_reply(WM_QUERYENDSESSION, 1) == (1, False)
    # "The session is ending" - stop now; "... is not ending after all" - carry on.
    assert session_end_reply(WM_ENDSESSION, 1) == (0, True)
    assert session_end_reply(WM_ENDSESSION, 0) == (0, False)
    # Asked to close: refused, or the window (and with it the warning) would be gone.
    assert session_end_reply(WM_CLOSE, 0) == (0, False)
    # Everything else is Windows' business.
    for message in (0x0001, 0x0002, 0x0081, 0x0312, WM_USER + 1):
        assert session_end_reply(message, 1) == (None, False)


def test_the_watcher_stops_the_belt_only_when_the_session_really_ends():
    calls: list[int] = []
    watcher = SessionEndWatcher(lambda: calls.append(1))
    assert watcher.handle(WM_QUERYENDSESSION, 0) == 1 and calls == []
    assert watcher.handle(WM_ENDSESSION, 0) == 0 and calls == []
    assert watcher.handle(WM_USER + 1, 1) is None and calls == []
    assert watcher.handle(WM_ENDSESSION, 1) == 0 and calls == [1]

    def failing() -> None:
        raise RuntimeError("the event loop has gone")

    # A failing callback must not escape into Windows' message dispatch.
    assert SessionEndWatcher(failing).handle(WM_ENDSESSION, 1) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_the_watcher_is_a_no_op_off_windows():
    watcher = SessionEndWatcher(lambda: None)
    assert watcher.start() is False and "Windows" in watcher.error and not watcher.active
    watcher.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="needs a real window and Windows' message queue")
def test_the_hidden_window_hears_the_end_of_the_session():
    """The real thing, as far as it can be had without logging off: the
    window exists, and the messages Windows would send it at log-off or
    shutdown - sent here by hand - reach the callback and get the right
    answers."""
    import ctypes
    from ctypes import wintypes

    called = threading.Event()
    calls: list[str] = []

    def stop_the_belt() -> None:
        calls.append(threading.current_thread().name)
        called.set()

    watcher = SessionEndWatcher(stop_the_belt)
    assert watcher.start(), watcher.error
    try:
        hwnd = watcher.hwnd
        assert watcher.active and hwnd
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        def send(message: int, wparam: int) -> int:
            reply = ctypes.c_size_t(12345)
            # SMTO_ABORTIFHUNG, five seconds: a broken message loop fails the test instead of hanging it.
            sent = user32.SendMessageTimeoutW(hwnd, message, wparam, 0, 0x0002, 5000, ctypes.byref(reply))
            assert sent, ctypes.WinError(ctypes.get_last_error())
            return reply.value

        assert user32.IsWindow(hwnd) and not user32.IsWindowVisible(hwnd)
        assert send(WM_QUERYENDSESSION, 0) == 1 and calls == []
        assert send(WM_ENDSESSION, 0) == 0 and calls == []  # the shutdown was called off
        assert send(WM_ENDSESSION, 1) == 0 and calls == ["walka-session-end"]
        assert send(WM_USER + 1, 7) == 0 and len(calls) == 1  # left to Windows
        assert send(WM_CLOSE, 0) == 0 and user32.IsWindow(hwnd)  # still there

        called.clear()
        assert user32.PostMessageW(hwnd, WM_ENDSESSION, 1, 0)  # posted rather than sent
        assert called.wait(5) and len(calls) == 2
    finally:
        watcher.stop()
    assert not watcher.active and watcher.hwnd is None

    # A second watcher in the same process gets a window of its own.
    again = SessionEndWatcher(lambda: None)
    assert again.start(), again.error
    again.stop()
