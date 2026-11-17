"""Reading the beacon off the screen.

The companion never touches the game: it takes a small screenshot of the
top-left corner of the game window (the same thing OBS or a screenshot tool
does) and decodes the strip the addon paints there.

Where to look:

* When Windows can tell us where the game window is, the strip is at the
  top-left corner of that window's client area or it is nowhere. Nothing
  else on the screen is looked at - so nothing else on the screen can be
  mistaken for the strip.
* Only where there is no way to ask (other platforms, or a window search
  that has never found the game on this computer) the reader falls back to
  the top-left corner of every monitor (a borderless full-screen game) and,
  now and then, a scan of the whole screen. What is found that way has to
  prove itself for longer before it is believed (``trust.py``), and a place
  whose picture does not move is given up again.

Normal operation is one ~700x12 pixel grab per frame.
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
# The strip (up to 8 rows), the black row under it that marks its lower
# edge, and the row a lock found by the scan keeps above it.
STRIP_HEIGHT = 12
# Rows tried within a grab: row 1 first (inside the strip for any cell size
# >= 2), then its neighbours.
ROW_ORDER = (1, 0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)

FULL_SCAN_INTERVAL_S = 5.0
# A strip redraws ten times a second. A picture of one whose counter has not
# moved for this long is not being drawn by a running game.
LOCK_STALL_S = 1.5
# How many such pictures are remembered, so that they are not picked again.
MAX_FROZEN_PLACES = 64
# Rows of one strip are at most this far apart (the tallest cell, and the
# row the scan keeps above its hit).
_SAME_STRIP_ROWS = int(beacon.MAX_CELL_PX) + 2


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


class _GreenRows(Sequence[bytes]):
    """The rows of a shot, cut out when asked for (a whole monitor has a
    thousand of them and the scan looks closely at very few)."""

    def __init__(self, shot: Shot) -> None:
        self._shot = shot

    def __len__(self) -> int:
        return self._shot.height

    def __getitem__(self, y):  # type: ignore[override]
        if not 0 <= y < self._shot.height:
            raise IndexError(y)
        return self._shot.green_row(y)


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
    """Finds the strip on screen and decodes it; remembers where it was.

    What it returns is what one look at the screen showed, nothing more:
    whether that is believed is decided by ``trust.TrustGate``.
    """

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
        self._lock_seq: int | None = None  # the counter last seen at the lock ...
        self._lock_ticked_at = -1e9  # ... and when it last changed
        # Places that showed a strip whose counter would not move (a paused
        # video, a screenshot, text that decodes by accident), with that
        # counter. They are passed over for as long as they show it.
        self._frozen: dict[tuple[int, int], int] = {}
        self._window_checked_at = -1e9
        self._last_full_scan = -1e9
        self.window_supported = window_finder is not None
        # The window finder has found the game at least once: from then on,
        # "no window" means the game is not on screen, not that we are blind.
        self.window_finder_works = False

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
        if self.window is not None:
            self.window_finder_works = True

    def _window_origin(self) -> tuple[int, int] | None:
        """Where the strip is if the game window is on screen, else None."""
        window = self.window
        if window is None or window.minimized or window.width <= 0 or window.height <= 0:
            return None
        return (window.left, window.top)

    def _decode_at(self, left: int, top: int) -> Decoded | None:
        try:
            shot = self.grabber.grab(left, top, STRIP_WIDTH, STRIP_HEIGHT)
        except Exception as exc:
            log.debug("screen grab failed at %s,%s: %s", left, top, exc)
            return None
        if shot is None or shot.width <= 0:
            return None
        rows = [shot.green_row(y) for y in range(shot.height)]
        for y in ROW_ORDER:
            if y >= len(rows):
                continue
            decoded = beacon.strip_in(rows, y, max_start=8)
            if decoded is not None:
                return decoded
        return None

    def _lock(self, origin: tuple[int, int], where: str, decoded: Decoded, now: float) -> None:
        self.locked, self.locked_where = origin, where
        self._lock_seq, self._lock_ticked_at = decoded.seq, now

    def _unlock(self) -> None:
        self.locked, self.locked_where, self._lock_seq = None, "", None

    def _ticking(self, decoded: Decoded, now: float) -> bool:
        """Is the strip at the lock being redrawn? (True for the first
        ``LOCK_STALL_S`` after its counter last moved.)"""
        if decoded.seq != self._lock_seq:
            self._lock_seq, self._lock_ticked_at = decoded.seq, now
            return True
        return now - self._lock_ticked_at < LOCK_STALL_S

    def _is_frozen(self, origin: tuple[int, int], decoded: Decoded) -> bool:
        """Is this the unmoving picture that was given up on at ``origin``?

        The scan meets one and the same strip on each of its rows, so a
        place a few rows up or down that shows the same counter is that
        picture again - not a new one to wait a second and a half for.
        """
        x, y = origin
        for (fx, fy), seq in self._frozen.items():
            if seq == decoded.seq and abs(fx - x) <= 2 and abs(fy - y) <= _SAME_STRIP_ROWS:
                return True
        self._frozen.pop(origin, None)  # something else is there now
        return False

    def _give_up(self, origin: tuple[int, int], decoded: Decoded) -> None:
        self._frozen.pop(origin, None)
        while len(self._frozen) >= MAX_FROZEN_PLACES:
            del self._frozen[next(iter(self._frozen))]  # the oldest
        self._frozen[origin] = decoded.seq

    def _full_scan(self) -> tuple[Decoded, tuple[int, int]] | None:
        """Search every monitor for the strip (slow; used sparingly).

        Returns the decoded strip and where to grab next time: one row above
        the hit (but not above its monitor, whose top may well be negative),
        so that row 1 of the grab is on the strip. Pictures that were given
        up on are passed over.
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
            rows = _GreenRows(shot)
            for y in range(shot.height):
                if not beacon.row_may_contain_beacon(rows[y]):
                    continue
                decoded = beacon.strip_in(rows, y)
                if decoded is None:
                    continue
                origin = (left + decoded.x0, top + max(0, y - 1))
                if not self._is_frozen(origin, decoded):
                    return decoded, origin
        return None

    # ---- the read --------------------------------------------------------------
    def read(self) -> ReadResult:
        """Grab and decode once."""
        if self.manual_origin is not None:
            decoded = self._decode_at(self.manual_origin[0], self.manual_origin[1])
            return ReadResult(decoded, self.manual_origin if decoded else None, "manual")
        now = self.clock()

        # Fast path: the strip was at the game window a moment ago and is
        # still being redrawn there, so the window has not gone anywhere.
        tried: tuple[int, int] | None = None
        decoded: Decoded | None = None
        if self.locked is not None and self.locked_where == "window":
            tried = self.locked
            decoded = self._decode_at(*tried)
            if decoded is not None and self._ticking(decoded, now):
                return ReadResult(decoded, tried, "window")

        # Not there, or a picture that does not move: where is the game now?
        self._refresh_window()
        origin = self._window_origin()
        if origin is not None:
            # The game window is on screen: its corner, and nowhere else.
            if origin != tried:
                # (The window moved - or the lock was a place found by
                # searching, which counts for nothing now.)
                self._unlock()
                decoded = self._decode_at(*origin)
                if decoded is not None:
                    self._lock(origin, "window", decoded, now)
            if decoded is None:
                return ReadResult(None, None, "")
            # (If its counter stands still this is a frozen game: reported
            # as it is, the source knows what to make of it.)
            return ReadResult(decoded, origin, "window")
        if self.window_finder_works:
            # Windows knows the game and says it is not on screen (closed or
            # minimised): there is no strip to find, only things to mistake
            # for one.
            self._unlock()
            return ReadResult(None, None, "")
        return self._read_anywhere(now)

    def _read_anywhere(self, now: float) -> ReadResult:
        """No way to ask where the game is: try where the strip was, then the
        monitor corners, then - rate-limited - everything."""
        last_resort: ReadResult | None = None
        if self.locked is not None:
            decoded = self._decode_at(*self.locked)
            if decoded is not None:
                result = ReadResult(decoded, self.locked, self.locked_where)
                if self._ticking(decoded, now):
                    return result
                # It does not move: whatever it is, the game's strip may be
                # somewhere else. Look - and only if there is nothing better
                # report this one (as the frozen picture it is).
                self._give_up(self.locked, decoded)
                last_resort = result

        candidates: list[tuple[int, int]] = []
        try:
            candidates = [(left, top) for left, top, _w, _h in self.grabber.monitors()]
        except Exception as exc:
            log.debug("monitor enumeration failed: %s", exc)
        for origin in candidates:
            if origin == self.locked:
                continue
            decoded = self._decode_at(*origin)
            if decoded is not None and not self._is_frozen(origin, decoded):
                self._lock(origin, "monitor", decoded, now)
                return ReadResult(decoded, origin, "monitor")

        # Slow path, rate-limited.
        if now - self._last_full_scan >= FULL_SCAN_INTERVAL_S:
            self._last_full_scan = now
            found = self._full_scan()
            if found is not None:
                decoded, origin = found
                self._lock(origin, "scan", decoded, now)
                return ReadResult(decoded, origin, "scan")
        return last_resort or ReadResult(None, None, "")

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

        # mss 10.2 deprecates the ``mss.mss()`` factory in favour of the
        # ``mss.MSS`` class; older releases only have the factory.
        factory = getattr(mss, "MSS", None) or mss.mss
        self._sct = factory()

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


def _cached_exe(pid: int, query: Callable[[int], str], *, refresh: bool = False) -> str:
    """Executable path of process ``pid``, asked for once and remembered."""
    if not refresh:
        cached = _EXE_CACHE.get(pid)
        if cached is not None:
            return cached
    path = query(pid)
    if len(_EXE_CACHE) > 512:
        _EXE_CACHE.clear()
    _EXE_CACHE[pid] = path
    return path


def _exe_for_window(title: str, class_name: str, pid: int, query: Callable[[int], str]) -> str:
    """Executable behind a window, by way of the per-process cache.

    Windows hands the process ids of programs that have exited to new ones,
    so a remembered path can belong to a process that is long gone. It is
    asked for again in the two cases where being wrong matters: a window
    that looks like the game but whose remembered executable is something
    else (the game got a recycled id and would be rejected), and a window
    that is only taken for the game because of its remembered executable
    (some other program got the game's old id).
    """
    exe = _cached_exe(pid, query)
    if is_wow_window(title, "", class_name):
        suspect = not is_wow_window(title, exe, class_name)
    else:
        suspect = is_game_executable(exe)
    if suspect:
        exe = _cached_exe(pid, query, refresh=True)
    return exe


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

    def query_exe(pid: int) -> str:
        path = ""
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    path = buffer.value
            finally:
                kernel32.CloseHandle(handle)
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
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _exe_for_window(title, class_name, pid.value, query_exe)
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
