"""Being told that the Windows session is ending.

When the user logs off or Windows shuts down, the companion wants a moment
to save the walk that is being recorded. A console program is normally told
through its console control handler - but not once it has loaded user32 or
gdi32, as this one has (its window, display scaling): Windows then tells its
windows instead. So the companion keeps one window that is never shown, on
a thread with a message loop of its own, just to be told
(``SessionEndWatcher``).
"""

from __future__ import annotations

import ctypes
import itertools
import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

WM_QUIT = 0x0012
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016


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

    The window that is never shown is the remedy Microsoft's documentation
    of SetConsoleCtrlHandler gives for a console program that has loaded
    user32.

    ``callback`` runs on the watcher's thread. It should return only when
    the process may be killed: Windows waits a few seconds for the answer
    and can end the process as soon as it has it.
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
                log.exception("closing down for the end of the Windows session failed")
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
