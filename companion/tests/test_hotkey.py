"""The global STOP hotkey: which key combinations are accepted, and what
they are on a Mac. (Registering one needs Windows or macOS: those tests run
there.) And the hidden window that hears Windows logging off or shutting
down."""

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
        # The same keys by the names a Mac keyboard has for them.
        ("ctrl+option+x", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("Control + Opt + X", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("cmd+shift+f9", (MOD_WIN | MOD_SHIFT, 0x78)),
        ("command+space", (MOD_WIN, 0x20)),
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
        "option",
        "cmd+option",
    ],
)
def test_unusable_combinations_are_refused(text):
    assert parse_hotkey(text) is None


def test_describe():
    assert hotkey.describe("ctrl+alt+x", "win32") == "Ctrl+Alt+X"
    assert hotkey.describe(" control + shift + f9 ", "win32") == "Ctrl+Shift+F9"
    assert hotkey.describe("cmd+option+x", "win32") == "Win+Alt+X"
    assert hotkey.describe("", "win32") == ""
    # On a Mac the keys are called what is printed on them there.
    assert hotkey.describe("ctrl+alt+x", "darwin") == "Control+Option+X"
    assert hotkey.describe("win+shift+f9", "darwin") == "Command+Shift+F9"
    assert hotkey.describe("", "darwin") == ""
    # ... and without being told, as on the computer this runs on.
    assert hotkey.describe("ctrl+alt+x") == hotkey.describe("ctrl+alt+x", sys.platform)


def test_a_hotkey_that_cannot_be_registered_reports_why_and_never_raises():
    calls: list[int] = []
    disabled = GlobalHotkey("", lambda: calls.append(1))
    assert disabled.start() is False and disabled.error == "disabled" and not disabled.active
    unusable = GlobalHotkey("x", lambda: calls.append(1))
    assert unusable.start() is False and "not a usable" in unusable.error
    for hk in (disabled, unusable):
        hk.stop()  # stopping what never started is fine
        assert hk.to_dict()["active"] is False
    if sys.platform not in ("win32", "darwin"):
        elsewhere = GlobalHotkey("ctrl+alt+x", lambda: calls.append(1))
        assert elsewhere.start() is False and elsewhere.error == hotkey.UNSUPPORTED
        assert elsewhere.to_dict() == {"combination": "Ctrl+Alt+X", "active": False, "error": elsewhere.error}
        elsewhere.pump()  # asking for key presses where there are none to be had is fine too
        elsewhere.stop()
    assert calls == []


# ---- the same combinations on a Mac ---------------------------------------------------------

CMD, SHIFT, OPTION, CONTROL = 0x0100, 0x0200, 0x0800, 0x1000  # Carbon's cmdKey, shiftKey, optionKey, controlKey


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ctrl+alt+x", (CONTROL | OPTION, 0x07)),  # the default
        ("cmd+shift+a", (CMD | SHIFT, 0x00)),
        ("ctrl+alt+shift+win+z", (CONTROL | OPTION | SHIFT | CMD, 0x06)),
        ("ctrl+0", (CONTROL, 0x1D)),
        ("ctrl+5", (CONTROL, 0x17)),  # (5 and 6 are the other way round on a Mac)
        ("ctrl+6", (CONTROL, 0x16)),
        ("alt+space", (OPTION, 0x31)),
        ("ctrl+enter", (CONTROL, 0x24)),
        ("ctrl+escape", (CONTROL, 0x35)),
        ("ctrl+f1", (CONTROL, 0x7A)),
        ("ctrl+f12", (CONTROL, 0x6F)),
        ("f13", (0, 0x69)),
        ("f20", (0, 0x5A)),
        ("ctrl+numpad0", (CONTROL, 0x52)),
        ("ctrl+numpad8", (CONTROL, 0x5B)),
        ("ctrl+home", (CONTROL, 0x73)),
        ("ctrl+delete", (CONTROL, 0x75)),
        ("ctrl+backspace", (CONTROL, 0x33)),
        ("pause", (0, 0x71)),  # a PC keyboard's Pause is F15 to a Mac ...
        ("scrolllock", (0, 0x6B)),  # ... and its Scroll Lock is F14
    ],
)
def test_combinations_as_macos_wants_them(text: str, expected: tuple[int, int]):
    assert hotkey.mac_hotkey(*parse_hotkey(text)) == expected


def test_every_usable_key_is_a_key_on_a_mac_except_f21_to_f24():
    keys = [f"ctrl+{chr(c)}" for c in range(ord("a"), ord("z") + 1)] + [f"ctrl+{d}" for d in range(10)]
    keys += [f"ctrl+{name}" for name in hotkey._KEYS] + [f"ctrl+f{n}" for n in range(1, 25)]
    keys += [f"ctrl+numpad{n}" for n in range(10)]
    missing = [text for text in keys if hotkey.mac_hotkey(*parse_hotkey(text)) is None]
    assert missing == ["ctrl+f21", "ctrl+f22", "ctrl+f23", "ctrl+f24"]
    # No two letters or digits share a key.
    typed = [hotkey._MAC_KEY_CODES[ord(c)] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
    assert len(set(typed)) == 36


def test_a_key_a_mac_does_not_have_is_refused_in_words(monkeypatch):
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    absent = GlobalHotkey("ctrl+f24", lambda: None)
    assert absent.start() is False and not absent.active
    assert "Mac keyboards do not have" in absent.error and "Control+F24" in absent.error
    absent.pump()
    absent.stop()


def test_on_a_mac_the_hotkey_is_registered_on_the_main_thread_or_not_at_all(monkeypatch):
    """Carbon hands key presses to the main thread only: a hotkey registered
    from another one would look fine and never fire."""
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    made: list[object] = []
    monkeypatch.setattr(hotkey, "_CarbonHotkey", lambda *args: made.append(args) or object())
    outcome: list[tuple[bool, str]] = []
    elsewhere = GlobalHotkey("ctrl+alt+x", lambda: None)
    thread = threading.Thread(target=lambda: outcome.append((elsewhere.start(), elsewhere.error)))
    thread.start()
    thread.join()
    assert outcome == [(False, "on macOS a hotkey can only be registered from the program's main thread")]
    assert made == [] and not elsewhere.active


def test_what_macos_objects_to_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")

    def taken(*_args):
        raise OSError(hotkey._HOTKEY_EXISTS, "macOS did not register the hotkey")

    monkeypatch.setattr(hotkey, "_CarbonHotkey", taken)
    refused = GlobalHotkey("ctrl+alt+x", lambda: None)
    assert refused.start() is False and "already used by another program" in refused.error

    def broken(*_args):
        raise AttributeError("function 'RegisterEventHotKey' not found")

    monkeypatch.setattr(hotkey, "_CarbonHotkey", broken)
    failed = GlobalHotkey("ctrl+alt+x", lambda: None)
    assert failed.start() is False and "RegisterEventHotKey" in failed.error and not failed.active
    failed.stop()


def test_a_registered_mac_hotkey_is_pumped_and_given_back(monkeypatch):
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    log: list[str] = []

    class Carbon:
        def __init__(self, modifiers, key_code, callback):
            log.append(f"registered {modifiers:#x} {key_code:#x}")
            self.callback = callback

        def pump(self):
            log.append("pumped")
            self.callback()

        def close(self):
            log.append("closed")

    monkeypatch.setattr(hotkey, "_CarbonHotkey", Carbon)
    pressed: list[int] = []
    registered = GlobalHotkey("ctrl+alt+x", lambda: pressed.append(1))
    assert registered.start() is True and registered.active and registered.error == ""
    assert registered.to_dict() == {"combination": "Control+Option+X", "active": True, "error": ""}
    registered.pump()
    assert pressed == [1]
    registered.stop()
    assert not registered.active
    registered.pump()  # nothing to ask any more
    registered.stop()
    assert log == ["registered 0x1800 0x7", "pumped", "closed"] and pressed == [1]


def _press_keys_on_this_mac(key_code: int, flags: int) -> None:
    """Press and release a key the way a keyboard would, as far as macOS
    lets a program do that."""
    import ctypes

    graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    graphics.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    graphics.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    graphics.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    graphics.CGEventSetFlags.restype = None
    graphics.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    graphics.CGEventPost.restype = None
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    foundation.CFRelease.restype = None
    for down in (True, False):
        event = graphics.CGEventCreateKeyboardEvent(None, key_code, down)
        assert event, "macOS would not make a key event"
        graphics.CGEventSetFlags(event, flags)
        graphics.CGEventPost(0, event)  # kCGHIDEventTap: where a real keyboard's events come in
        foundation.CFRelease(event)


def _queue_a_hotkey_event_on_this_mac(hotkey_id: int) -> None:
    """Put an event in the main thread's queue that is made like the one
    macOS puts there when a registered combination is pressed."""
    import ctypes

    carbon = hotkey._load_carbon()
    carbon.CreateEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_double,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.CreateEvent.restype = ctypes.c_int32
    carbon.SetEventParameter.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    carbon.SetEventParameter.restype = ctypes.c_int32
    carbon.GetMainEventQueue.argtypes = []
    carbon.GetMainEventQueue.restype = ctypes.c_void_p
    carbon.PostEventToQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int16]
    carbon.PostEventToQueue.restype = ctypes.c_int32

    event = ctypes.c_void_p()
    made = carbon.CreateEvent(
        None, hotkey._EVENT_CLASS_KEYBOARD, hotkey._EVENT_HOTKEY_PRESSED, 0.0, 0, ctypes.byref(event)
    )
    assert made == 0 and event, f"macOS would not make an event ({made})"
    which = hotkey._EventHotKeyID(hotkey._HOTKEY_SIGNATURE, hotkey_id)
    assert (
        carbon.SetEventParameter(
            event, hotkey._PARAM_DIRECT_OBJECT, hotkey._TYPE_HOTKEY_ID, ctypes.sizeof(which), ctypes.byref(which)
        )
        == 0
    )
    assert carbon.PostEventToQueue(carbon.GetMainEventQueue(), event, 1) == 0  # kEventPriorityStandard
    carbon.ReleaseEvent(event)  # (the queue keeps its own hold on it)


@pytest.mark.skipif(sys.platform != "darwin", reason="registers a real hotkey with macOS")
def test_a_hotkey_event_in_a_macs_queue_reaches_the_callback_through_pump():
    """Everything of the hotkey that is this program's: taking an event out
    of the main thread's queue, handing it on, and telling whose it is. What
    is not shown here is macOS putting the event there when the keys are
    pressed (see the next test)."""
    pressed: list[str] = []
    mine = GlobalHotkey("ctrl+alt+shift+f18", lambda: pressed.append("mine"))
    other = GlobalHotkey("ctrl+alt+shift+f17", lambda: pressed.append("other"))
    assert mine.start() is True, mine.error
    assert other.start() is True, other.error
    mine_id, other_id = mine._mac._id, other._mac._id
    try:
        mine.pump()
        assert pressed == []
        _queue_a_hotkey_event_on_this_mac(mine_id)
        assert pressed == []  # not before it is asked for
        mine.pump()
        assert pressed == ["mine"]  # ... and only the one it was for
        _queue_a_hotkey_event_on_this_mac(other_id)
        mine.pump()  # (whichever is asked: the queue is the program's)
        assert pressed == ["mine", "other"]
        _queue_a_hotkey_event_on_this_mac(10**6)  # somebody else's
        mine.pump()
        assert pressed == ["mine", "other"]
    finally:
        mine.stop()
        other.stop()
    # A hotkey that has been given back hears nothing more.
    survivor = GlobalHotkey("ctrl+alt+shift+f17", lambda: pressed.append("survivor"))
    assert survivor.start() is True, survivor.error
    try:
        _queue_a_hotkey_event_on_this_mac(mine_id)
        survivor.pump()
        assert pressed == ["mine", "other"]
    finally:
        survivor.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="registers a real hotkey with macOS")
def test_a_real_hotkey_on_a_mac():
    """The real thing: macOS takes the combination, and a press of it - made
    here, since nobody is at the keyboard - reaches the callback through
    ``pump``. A machine that does not let a program press keys cannot show
    the second half; the test then says so instead of passing."""
    import time

    pressed: list[float] = []
    # (Not the default combination: this must not get in the way of a companion that is running.)
    hk = GlobalHotkey("ctrl+alt+shift+f19", lambda: pressed.append(time.monotonic()))
    assert hk.start() is True, hk.error
    try:
        assert hk.active and hk.error == ""
        hk.pump()
        assert pressed == []  # nothing was pressed yet
        control, option, shift = 1 << 18, 1 << 19, 1 << 17  # kCGEventFlagMaskControl, ...Alternate, ...Shift
        _press_keys_on_this_mac(0x50, control | option | shift)  # F19
        deadline = time.monotonic() + 3.0
        while not pressed and time.monotonic() < deadline:
            hk.pump()
            time.sleep(0.02)
    finally:
        hk.stop()
    assert not hk.active
    if not pressed:
        pytest.skip("the hotkey was registered, but this machine does not let a program press keys to try it")
    hk.pump()  # given back: asking again does nothing
    assert len(pressed) == 1


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
