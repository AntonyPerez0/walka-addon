"""What the companion asks macOS: where the game window is, and whether the
screen may be read at all.

Everything here goes through the system's own C libraries (CoreGraphics,
CoreFoundation, libproc) by way of ``ctypes`` - the same way ``capture.py``
asks Windows - so nothing needs to be installed for it. The module can be
imported anywhere; the functions that ask the system only work on a Mac.

Positions are in points, the unit macOS measures the screen in, with the
origin at the top-left corner of the main display: the units python-mss
takes its capture rectangles in.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .capture import MIN_GAME_WINDOW, GameWindow, _window_rank

log = logging.getLogger(__name__)

# What the game's windows are listed under when the path of its executable
# cannot be read. (When it can, the path decides: see is_game_executable.)
WOW_APP_NAMES = ("World of Warcraft", "World of Warcraft Classic")
# The game's executable is called like this ...
WOW_EXE_PREFIXES = ("world of warcraft", "wow")
# ... and sits in an application bundle: <game folder>/<name>.app/Contents/MacOS/<name>.
APP_SUFFIX = ".app"

# macOS says where a window is, title bar included, but not how tall the
# title bar is - or whether there is one (a full-screen game has none). The
# strip is looked for this far down from the window's top edge. A title bar
# is 28 points (22 before macOS 11); in full screen on a MacBook with a
# camera notch the picture can begin up to 38 points down.
TITLE_BAR_SLACK = 48

SCREEN_RECORDING_ADVICE = (
    "macOS has not allowed this program to record the screen, so the game cannot be seen. "
    "Open System Settings > Privacy & Security > Screen & System Audio Recording (Screen Recording on older "
    "versions), switch on Walka - or Terminal, if Walka was started from there - and start Walka again"
)


@dataclass(frozen=True)
class WindowInfo:
    """One entry of the system's window list."""

    owner: str  # the name of the application the window belongs to
    pid: int
    title: str  # empty unless the Screen Recording permission has been given
    layer: int  # 0 for the ordinary windows of applications
    left: int  # the window's frame, title bar included, in points
    top: int
    width: int
    height: int
    on_screen: bool


# ---- which window is the game ---------------------------------------------------------


def is_game_executable(exe_path: str) -> bool:
    """Is this the game's executable inside a game folder
    (``.../_retail_/World of Warcraft.app/Contents/MacOS/World of Warcraft``)?

    The folder is what tells the game from the other programs that carry its
    name (the launcher of old was *World of Warcraft Launcher.app*, one
    folder up)."""
    parts = [part for part in exe_path.split("/") if part]
    if len(parts) < 3 or not parts[-1].lower().startswith(WOW_EXE_PREFIXES):
        return False
    for index in range(len(parts) - 2, 0, -1):
        if parts[index].lower().endswith(APP_SUFFIX):
            folder = parts[index - 1]
            return len(folder) > 2 and folder.startswith("_") and folder.endswith("_")
    return False


def is_wow_app(owner: str, exe_path: str) -> bool:
    """Do the windows of this application belong to the game?

    By the path of its executable when that is known; by the application's
    name only when it is not."""
    if exe_path:
        return is_game_executable(exe_path)
    return owner in WOW_APP_NAMES


def pick_game_window(windows: Iterable[WindowInfo], exe_of: Callable[[int], str]) -> GameWindow | None:
    """The game's main window among ``windows``, or None.

    ``exe_of`` gives the executable path of a process id ("" if it cannot
    be read). It is asked anew every time: process ids are handed on to new
    programs, and asking costs next to nothing here.
    """
    paths: dict[int, str] = {}
    found: list[GameWindow] = []
    for window in windows:
        # Menus, the Dock and the menu bar live on other layers; small
        # helper windows are not the game. (A minimised window keeps its size.)
        if window.layer != 0 or window.width < MIN_GAME_WINDOW[0] or window.height < MIN_GAME_WINDOW[1]:
            continue
        if window.pid not in paths:
            try:
                paths[window.pid] = exe_of(window.pid)
            except Exception as exc:
                log.debug("no executable path for process %s: %s", window.pid, exc)
                paths[window.pid] = ""
        exe = paths[window.pid]
        if not is_wow_app(window.owner, exe):
            continue
        found.append(
            GameWindow(
                title=window.title or window.owner,
                left=window.left,
                top=window.top,
                width=window.width,
                height=window.height,
                exe_path=exe,
                # Minimised, hidden, or on another desktop ("Space"): either
                # way its picture is not on the screen.
                minimized=not window.on_screen,
                top_slack=TITLE_BAR_SLACK,
            )
        )
    if not found:
        return None
    found.sort(key=_window_rank)
    return found[0]


# ---- asking the system ------------------------------------------------------------------

_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_LIBPROC = "/usr/lib/libproc.dylib"

_UTF8 = 0x08000100  # kCFStringEncodingUTF8
_SINT64 = 4  # kCFNumberSInt64Type
_ON_SCREEN_ONLY = 1 << 0  # kCGWindowListOptionOnScreenOnly
_NO_DESKTOP = 1 << 4  # kCGWindowListExcludeDesktopElements
_PATH_BUFFER = 4096  # PROC_PIDPATHINFO_MAXSIZE


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


_libraries: tuple[ctypes.CDLL, ctypes.CDLL] | None = None


def _load() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    """CoreFoundation and CoreGraphics, with the signatures of what is used
    (without them ctypes would pass pointers as 32-bit ints)."""
    global _libraries
    if _libraries is not None:
        return _libraries
    if sys.platform != "darwin":
        raise OSError("this only works on macOS")
    cf = ctypes.CDLL(_CORE_FOUNDATION)
    cg = ctypes.CDLL(_CORE_GRAPHICS)
    pointer = ctypes.c_void_p
    cf.CFArrayGetCount.argtypes = [pointer]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [pointer, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = pointer
    cf.CFDictionaryGetValue.argtypes = [pointer, pointer]
    cf.CFDictionaryGetValue.restype = pointer
    cf.CFNumberGetValue.argtypes = [pointer, ctypes.c_long, pointer]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFBooleanGetValue.argtypes = [pointer]
    cf.CFBooleanGetValue.restype = ctypes.c_bool
    cf.CFStringGetLength.argtypes = [pointer]
    cf.CFStringGetLength.restype = ctypes.c_long
    cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    cf.CFStringGetCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [pointer]
    cf.CFRelease.restype = None
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = pointer
    cg.CGRectMakeWithDictionaryRepresentation.argtypes = [pointer, ctypes.POINTER(_CGRect)]
    cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
    _libraries = (cf, cg)
    return _libraries


def _text(cf: ctypes.CDLL, string: int | None) -> str:
    if not string:
        return ""
    size = cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(string), _UTF8) + 1
    buffer = ctypes.create_string_buffer(size)
    if not cf.CFStringGetCString(string, buffer, size, _UTF8):
        return ""
    return buffer.value.decode("utf-8", errors="replace")


def _whole(cf: ctypes.CDLL, number: int | None, default: int = 0) -> int:
    if not number:
        return default
    value = ctypes.c_int64()
    return value.value if cf.CFNumberGetValue(number, _SINT64, ctypes.byref(value)) else default


def list_windows(*, on_screen_only: bool = False) -> list[WindowInfo]:
    """Every window the system knows of, front to back - including, unless
    ``on_screen_only``, the ones that are minimised or on another desktop."""
    cf, cg = _load()

    def key(name: str) -> ctypes.c_void_p:
        return ctypes.c_void_p.in_dll(cg, name)  # a constant of the library: a CFString

    owner_key, pid_key, title_key = key("kCGWindowOwnerName"), key("kCGWindowOwnerPID"), key("kCGWindowName")
    layer_key, bounds_key, shown_key = key("kCGWindowLayer"), key("kCGWindowBounds"), key("kCGWindowIsOnscreen")

    listed = cg.CGWindowListCopyWindowInfo(_NO_DESKTOP | (_ON_SCREEN_ONLY if on_screen_only else 0), 0)
    if not listed:
        return []
    windows: list[WindowInfo] = []
    try:
        for index in range(cf.CFArrayGetCount(listed)):
            entry = cf.CFArrayGetValueAtIndex(listed, index)
            if not entry:
                continue
            rect = _CGRect()
            bounds = cf.CFDictionaryGetValue(entry, bounds_key)
            if not bounds or not cg.CGRectMakeWithDictionaryRepresentation(bounds, ctypes.byref(rect)):
                continue
            shown = cf.CFDictionaryGetValue(entry, shown_key)  # left out for a window that is not shown
            windows.append(
                WindowInfo(
                    owner=_text(cf, cf.CFDictionaryGetValue(entry, owner_key)),
                    pid=_whole(cf, cf.CFDictionaryGetValue(entry, pid_key)),
                    title=_text(cf, cf.CFDictionaryGetValue(entry, title_key)),
                    layer=_whole(cf, cf.CFDictionaryGetValue(entry, layer_key), default=-1),
                    left=round(rect.origin.x),
                    top=round(rect.origin.y),
                    width=round(rect.size.width),
                    height=round(rect.size.height),
                    on_screen=bool(shown and cf.CFBooleanGetValue(shown)),
                )
            )
    finally:
        cf.CFRelease(listed)
    return windows


_libproc: ctypes.CDLL | None = None


def executable_path(pid: int) -> str:
    """The path of the program process ``pid`` runs, or "" if it cannot be read."""
    global _libproc
    if sys.platform != "darwin":
        return ""
    if _libproc is None:
        _libproc = ctypes.CDLL(_LIBPROC)
        _libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        _libproc.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(_PATH_BUFFER)
    length = _libproc.proc_pidpath(int(pid), buffer, _PATH_BUFFER)
    return os.fsdecode(buffer.raw[:length]) if length > 0 else ""


def find_wow_window() -> GameWindow | None:
    """Locate the World of Warcraft window (macOS only)."""
    if sys.platform != "darwin":
        return None
    return pick_game_window(list_windows(), executable_path)


# ---- may the screen be read? ----------------------------------------------------------


def screen_recording_allowed() -> bool | None:
    """Has this program been given the *Screen Recording* permission?
    None if that cannot be asked (not a Mac, or macOS before 10.15, which
    had no such permission)."""
    if sys.platform != "darwin":
        return None
    try:
        _cf, cg = _load()
        cg.CGPreflightScreenCaptureAccess.argtypes = []
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except (AttributeError, OSError) as exc:
        log.debug("cannot ask about the screen recording permission: %s", exc)
        return None


def request_screen_recording() -> None:
    """Have macOS ask the user for the permission (it does so once) and put
    this program on the list in System Settings."""
    if sys.platform != "darwin":
        return
    try:
        _cf, cg = _load()
        cg.CGRequestScreenCaptureAccess.argtypes = []
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        cg.CGRequestScreenCaptureAccess()
    except (AttributeError, OSError) as exc:
        log.debug("cannot ask for the screen recording permission: %s", exc)
