"""Reading the beacon off the screen.

The companion never touches the game: it takes a small screenshot of the
top-left corner of the game window (the same thing OBS or a screenshot tool
does) and decodes the strip the addon paints there.

Where to look, in order:

1. the game window's client area, found through the Win32 API;
2. the top-left corner of every monitor (a borderless full-screen game);
3. as a last resort, an occasional scan of the whole screen for the strip.

Once the strip has been seen its position is remembered, so normal
operation is one ~700x10 pixel grab per frame.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from . import beacon
from .beacon import Decoded

log = logging.getLogger(__name__)

# Enough for 86 cells at the largest cell size plus slack.
STRIP_WIDTH = int(beacon.TOTAL_CELLS * 8 + 24)
STRIP_HEIGHT = 12
# Rows tried within a grab: row 1 first (inside the strip for any cell size
# >= 2), then its neighbours.
ROW_ORDER = (1, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)

FULL_SCAN_INTERVAL_S = 5.0


@dataclass(frozen=True)
class Shot:
    """A captured rectangle: BGRA bytes, row-major, top row first."""

    width: int
    height: int
    bgra: bytes

    def green_row(self, y: int) -> bytes:
        """Green channel of row ``y`` (the strip is grey, any channel will do)."""
        start = y * self.width * 4 + 1
        return self.bgra[start : start + self.width * 4 : 4]


class Grabber(Protocol):
    """Screen-capture backend."""

    def grab(self, left: int, top: int, width: int, height: int) -> Shot | None: ...

    def monitors(self) -> list[tuple[int, int, int, int]]:
        """(left, top, width, height) of each monitor, in screen pixels."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class GameWindow:
    """The game's window, if it could be found."""

    title: str
    left: int  # client area origin in screen pixels
    top: int
    width: int
    height: int
    exe_path: str = ""
    minimized: bool = False


WindowFinder = Callable[[], GameWindow | None]


@dataclass
class ReadResult:
    decoded: Decoded | None
    origin: tuple[int, int] | None  # screen position of the strip when found
    where: str = ""  # "window" | "monitor" | "scan" | "manual"


class BeaconReader:
    """Finds the strip on screen and decodes it; remembers where it was."""

    def __init__(
        self,
        grabber: Grabber,
        window_finder: WindowFinder | None = None,
        *,
        manual_origin: Sequence[int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.grabber = grabber
        self.window_finder = window_finder
        self.manual_origin = tuple(manual_origin) if manual_origin else None
        self.clock = clock
        self.window: GameWindow | None = None
        self.locked: tuple[int, int] | None = None  # where the strip was last decoded
        self.locked_where = ""
        self._window_checked_at = -1e9
        self._last_full_scan = -1e9
        self.window_supported = window_finder is not None

    # ---- helpers ---------------------------------------------------------------
    def _refresh_window(self, force: bool = False) -> None:
        if self.window_finder is None:
            return
        now = self.clock()
        if not force and now - self._window_checked_at < 1.0:
            return
        self._window_checked_at = now
        try:
            self.window = self.window_finder()
        except Exception as exc:  # never let a Win32 hiccup kill the reader
            log.debug("window lookup failed: %s", exc)
            self.window = None

    def _decode_at(self, left: int, top: int) -> Decoded | None:
        try:
            shot = self.grabber.grab(left, top, STRIP_WIDTH, STRIP_HEIGHT)
        except Exception as exc:
            log.debug("screen grab failed at %s,%s: %s", left, top, exc)
            return None
        if shot is None or shot.width <= 0:
            return None
        for y in ROW_ORDER:
            if y >= shot.height:
                continue
            decoded = beacon.decode_row(shot.green_row(y), max_start=8)
            if decoded is not None:
                return decoded
        return None

    def _full_scan(self) -> tuple[Decoded, tuple[int, int]] | None:
        """Search every monitor for the strip (slow; used sparingly).

        Returns the decoded strip and where to grab next time: one row above
        the hit (but not above its monitor, whose top may well be negative),
        so that row 1 of the grab is on the strip.
        """
        try:
            monitors = self.grabber.monitors()
        except Exception as exc:
            log.debug("monitor enumeration failed: %s", exc)
            return None
        for left, top, width, height in monitors:
            try:
                shot = self.grabber.grab(left, top, width, height)
            except Exception as exc:
                log.debug("full-screen grab failed: %s", exc)
                continue
            if shot is None:
                continue
            for y in range(shot.height):
                row = shot.green_row(y)
                if not beacon.row_may_contain_beacon(row):
                    continue
                decoded = beacon.decode_row(row)
                if decoded is not None:
                    return decoded, (left + decoded.x0, top + max(0, y - 1))
        return None

    # ---- the read --------------------------------------------------------------
    def read(self) -> ReadResult:
        """Grab and decode once."""
        if self.manual_origin is not None:
            decoded = self._decode_at(self.manual_origin[0], self.manual_origin[1])
            return ReadResult(decoded, self.manual_origin if decoded else None, "manual")

        # Fast path: where it was last time.
        if self.locked is not None:
            decoded = self._decode_at(*self.locked)
            if decoded is not None:
                return ReadResult(decoded, self.locked, self.locked_where)

        self._refresh_window()
        candidates: list[tuple[tuple[int, int], str]] = []
        if self.window is not None and not self.window.minimized and self.window.width > 0:
            candidates.append(((self.window.left, self.window.top), "window"))
        try:
            for left, top, _w, _h in self.grabber.monitors():
                candidates.append(((left, top), "monitor"))
        except Exception as exc:
            log.debug("monitor enumeration failed: %s", exc)
        for origin, where in candidates:
            if origin == self.locked:
                continue
            decoded = self._decode_at(*origin)
            if decoded is not None:
                self.locked, self.locked_where = origin, where
                return ReadResult(decoded, origin, where)

        # Slow path, rate-limited.
        now = self.clock()
        if now - self._last_full_scan >= FULL_SCAN_INTERVAL_S:
            self._last_full_scan = now
            found = self._full_scan()
            if found is not None:
                decoded, origin = found
                self.locked, self.locked_where = origin, "scan"
                return ReadResult(decoded, self.locked, "scan")
        return ReadResult(None, None, "")

    def close(self) -> None:
        try:
            self.grabber.close()
        except Exception:
            pass


# ---- mss backend ---------------------------------------------------------------


class MssGrabber:
    """python-mss: GDI BitBlt on Windows, also works on Linux/macOS."""

    def __init__(self) -> None:
        import mss

        self._sct = mss.mss()

    def grab(self, left: int, top: int, width: int, height: int) -> Shot | None:
        shot = self._sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
        return Shot(shot.width, shot.height, bytes(shot.bgra))

    def monitors(self) -> list[tuple[int, int, int, int]]:
        # monitors[0] is the union of all monitors; the rest are individual.
        return [(m["left"], m["top"], m["width"], m["height"]) for m in self._sct.monitors[1:]]

    def close(self) -> None:
        self._sct.close()


# ---- Win32 window finder -------------------------------------------------------

WOW_TITLES = ("World of Warcraft",)
WOW_EXE_PREFIXES = ("wow",)
# Every WoW client's main window has a class name starting with this.
WOW_WINDOW_CLASS_PREFIX = "GxWindowClass"
# WoW: Forever is Wow.exe (the same name as retail) inside this folder.
PREFERRED_FLAVOUR = "_forever_"


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware so that window coordinates
    are physical pixels (and match what the screen capture returns)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _is_wow_executable(exe_path: str) -> bool:
    name = exe_path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    return name.startswith(WOW_EXE_PREFIXES) and name.endswith(".exe")


def is_game_executable(exe_path: str) -> bool:
    """Is this the game's executable inside a game folder
    (``...\\_forever_\\Wow.exe``)? That identifies the game whatever its
    window happens to be called."""
    parts = exe_path.replace("/", "\\").split("\\")
    if len(parts) < 2 or not _is_wow_executable(exe_path):
        return False
    folder = parts[-2]
    return len(folder) > 2 and folder.startswith("_") and folder.endswith("_")


def is_wow_window(title: str, exe_path: str, class_name: str = "") -> bool:
    """Is this top-level window the game?

    It must have the game's window class or its exact title and, when the
    executable is known, that must be a Wow*.exe.
    """
    if not (class_name.startswith(WOW_WINDOW_CLASS_PREFIX) or title in WOW_TITLES):
        return False
    if not exe_path:
        return True
    return _is_wow_executable(exe_path)


# Process id -> executable path, so that the once-a-second window search
# does not open every process on the desktop each time.
_EXE_CACHE: dict[int, str] = {}
MIN_GAME_WINDOW = (320, 240)


def _window_rank(window: GameWindow) -> tuple[bool, bool, int]:
    """Sort key: visible first, then the preferred game flavour, then largest."""
    folder = window.exe_path.replace("/", "\\").lower()
    preferred = f"\\{PREFERRED_FLAVOUR}\\" in folder
    return (window.minimized, not preferred, -(window.width * window.height))


def find_wow_window() -> GameWindow | None:
    """Locate the World of Warcraft window (Windows only)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    found: list[GameWindow] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # Declare signatures: without them ctypes would pass handles as 32-bit
    # ints, which truncates them on 64-bit Windows.
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def exe_of(hwnd: int) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cached = _EXE_CACHE.get(pid.value)
        if cached is not None:
            return cached
        path = ""
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    path = buffer.value
            finally:
                kernel32.CloseHandle(handle)
        if len(_EXE_CACHE) > 512:
            _EXE_CACHE.clear()
        _EXE_CACHE[pid.value] = path
        return path

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        class_buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, class_buffer, 128)
        class_name = class_buffer.value
        title = ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        minimized = bool(user32.IsIconic(hwnd))
        exe = exe_of(hwnd)
        if is_wow_window(title, "", class_name):
            # Looks like the game by class or title: the executable, when it
            # can be read, must agree.
            if not is_wow_window(title, exe, class_name):
                return True
        else:
            # Unknown class and title (they are not confirmed for every
            # client): accept the main window of a Wow*.exe that runs from a
            # game folder. Small helper windows are not it.
            big_enough = rect.right - rect.left >= MIN_GAME_WINDOW[0] and rect.bottom - rect.top >= MIN_GAME_WINDOW[1]
            if not is_game_executable(exe) or not (big_enough or minimized):
                return True
        point = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
            return True
        found.append(
            GameWindow(
                title=title,
                left=point.x,
                top=point.y,
                width=rect.right - rect.left,
                height=rect.bottom - rect.top,
                exe_path=exe,
                minimized=minimized,
            )
        )
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    if not found:
        return None
    found.sort(key=_window_rank)
    return found[0]


def default_window_finder() -> WindowFinder | None:
    return find_wow_window if sys.platform == "win32" else None
