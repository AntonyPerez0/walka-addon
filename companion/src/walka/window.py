"""Walka's own window.

The dashboard is a page served on this computer (``web.py``). It used to be
shown in a tab of the web browser, with Walka itself a console window to be
left open next to it. Here it gets a window of its own - title bar, icon,
its place on the taskbar or in the Dock - and closing that window closes
Walka.

The window is drawn by what the system already has for showing pages
inside programs: Microsoft Edge WebView2 on Windows (part of Windows 11,
and on nearly every Windows 10), WebKit on a Mac. `pywebview
<https://pywebview.flowrl.com>`_ is the library between that and Python.
Nothing is downloaded for it, and the page is the same page: it still
comes from ``127.0.0.1``, and still only from there.

Where no such window can be had - a Windows without WebView2, a system
this is not packaged for - ``unavailable()`` says why, and the dashboard
opens in the browser as before.

The window wants the program's main thread for itself (macOS insists, and
pywebview with it). So with a window, everything else Walka does - the
Bluetooth, the screen, the server - runs on a second thread: see
``cli._run_in_window``. Bluetooth on Windows is happier there too: the
window makes its thread a single-threaded COM apartment, which the
Windows Runtime's Bluetooth cannot be used from.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TITLE = "Walka"
# Fits beside a game on a laptop's screen (1366 x 768, less the taskbar). The
# page arranges itself for whatever width it is given, down to about a
# phone's (app.css), so the window can be made as small or large as wanted.
SIZE = (1200, 720)
MIN_SIZE = (480, 420)

# What marks the WebView2 Runtime as installed, as Microsoft documents it:
# a version ("pv") under one of these keys, for the whole computer or for
# this user. https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_KEYS = (
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
    ("HKEY_CURRENT_USER", rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}"),
)


def _registry_value(root: str, key: str, name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(getattr(winreg, root), key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return str(value)


def webview2_version(read: Callable[[str, str, str], str | None] = _registry_value) -> str | None:
    """The version of the WebView2 Runtime on this computer, or ``None``."""
    for root, key in WEBVIEW2_KEYS:
        version = (read(root, key, "pv") or "").strip()
        if version and version != "0.0.0.0":
            return version
    return None


def unavailable(platform: str = sys.platform) -> str | None:
    """Why Walka cannot have a window of its own here - ``None`` if it can."""
    if importlib.util.find_spec("webview") is None:
        return "pywebview, which makes the window, is not installed"
    if platform == "win32":
        if webview2_version() is None:
            return "Microsoft Edge WebView2, which Windows shows pages inside programs with, is not on this computer"
    elif platform != "darwin":
        # Not packaged for, but it works where pywebview finds Qt or GTK.
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return "there is no screen to open a window on"
        if not any(importlib.util.find_spec(name) for name in ("qtpy", "gi")):
            return "pywebview has neither Qt nor GTK to draw a window with"
    return None


def _toolkit(platform: str = sys.platform) -> str | None:
    """Which of its toolkits pywebview is to use: ``None`` - its own choice -
    on Windows and on a Mac, where there is one. Elsewhere it tries GTK
    first, and says so at length when GTK is not there."""
    if platform in ("win32", "darwin") or importlib.util.find_spec("gi") is not None:
        return None
    return "qt"


class DashboardWindow:
    """The dashboard at ``url``, in a window.

    ``run()`` shows it and returns when it has been closed; it must be
    called on the main thread. ``on_closing`` is called - on the window's
    thread - when the window is about to close, whoever asked: the X, Quit
    in the menu, the system logging out. The program may be ended as soon
    as it returns (macOS does that after Quit), so it is the place to save
    what must be saved.

    ``storage`` is a folder for what the page keeps between runs (the
    choices on the Share page). Only Windows needs one: macOS keeps a
    folder per program by itself.
    """

    def __init__(
        self,
        url: str,
        storage: Path,
        *,
        on_closing: Callable[[], None],
        floating: FloatingWindow | None = None,
    ) -> None:
        self.url = url
        self.storage = storage
        self._on_closing = on_closing
        # The floating window, if there is to be one: it can only be opened
        # once this window is, and it goes when this one does (the program
        # runs for as long as any of its windows is open).
        self.floating = floating
        self._lock = threading.Lock()
        self._window = None
        self._close_asked = False
        self._closing_itself = False

    def run(self) -> None:
        import webview

        # The page saves files: a walk as TCX, the Share picture as PNG. In
        # a window that is a "Save as" box (in a browser it was the
        # downloads folder).
        webview.settings["ALLOW_DOWNLOADS"] = True
        # The window shows Walka's own page and nothing else: the two links
        # that lead elsewhere (the disclaimer) open in the web browser.
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        with self._lock:
            if self._close_asked:
                return
            window = webview.create_window(
                TITLE,
                self.url,
                width=SIZE[0],
                height=SIZE[1],
                min_size=MIN_SIZE,
                text_select=True,  # numbers on a dashboard are there to be copied
                zoomable=True,
            )
            window.events.closing += self._closing
            window.events.shown += self._shown
            window.events.loaded += self._loaded
            self._window = window
        try:
            self.storage.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # the window works without; the Share page forgets its choices
            log.debug("no folder for the window's own data (%s): %s", self.storage, exc)
        # private_mode=False: keep what the page stores (localStorage).
        webview.start(private_mode=False, storage_path=str(self.storage), gui=_toolkit())

    def close(self) -> None:
        """Close the window, from any thread. (``on_closing`` is not called:
        whoever closes it this way is already stopping Walka.)"""
        if self.floating is not None:
            self.floating.finish()
        with self._lock:
            self._close_asked = True
            window = self._window
        if window is None or self._closing_itself:
            return  # not open yet (and now it will not), or on its way out already
        try:
            window.destroy()  # waits until the window is there to be closed
        except Exception as exc:  # already gone
            log.debug("closing the window: %s", exc)

    # (pywebview calls these with no arguments: it looks at the signature.)
    def _closing(self) -> None:
        self._closing_itself = True
        if self.floating is not None:
            self.floating.finish()
        try:
            self._on_closing()
        except Exception:
            log.exception("while Walka's window was closing")

    def _shown(self) -> None:
        import webview

        renderer = getattr(webview, "renderer", None) or "?"
        log.info("Walka's window is open (drawn by %s)", renderer)
        if self.floating is not None:
            self.floating.can_open()
        if renderer == "mshtml":
            # Internet Explorer's engine: what pywebview falls back to on
            # Windows. unavailable() should have kept us from getting here.
            log.warning("the window is drawn by Internet Explorer's engine, which cannot show the dashboard")

    def _loaded(self) -> None:
        log.info("the dashboard is loaded in Walka's window")


# ---- the floating window ---------------------------------------------------------------------
#
# A small panel that stays on top of every other window - the game's
# included, as long as the game runs in a window or borderless (as it has
# to for Walka to see it at all): the live numbers of the walk, where the
# addon's own panel used to be. An addon cannot be told what the treadmill
# measures while the game runs; a window of Walka's own can show it.

FLOATING_TITLE = "Walka - walk"
FLOATING_SIZE = (240, 132)
# Out of the way where it first opens: the top-right corner of the main
# screen, this far in.
FLOATING_MARGIN = (24, 96)
FLOATING_BACKGROUND = "#14171c"
# A drag sends the window's place many times a second; it is remembered
# once it has been still for this long.
MOVED_SETTLE_S = 1.0


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware, so that the places of
    windows and screens are in physical pixels (Windows only)."""
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


def pywebview_scale(platform: str = sys.platform) -> float:
    """How many screen pixels one unit of a window's place is to pywebview.

    Walka is aware of display scaling (``enable_dpi_awareness``), so
    the screens pywebview lists are in pixels. But on Windows pywebview
    takes a window's x and y in scaled units and multiplies them by the
    display's scale (150 % -> 1.5) itself: a corner worked out in pixels
    lands off the screen. The scale of the main display is what it uses
    for a new window. Elsewhere (a Mac counts in points throughout) it is 1.
    """
    if platform != "win32":
        return 1.0
    import ctypes

    try:
        dpi = int(ctypes.WinDLL("user32").GetDpiForSystem())
    except (AttributeError, OSError, ValueError):
        return 1.0
    return dpi / 96 if dpi > 0 else 1.0


def _screen_box(screen: Any, scale: float) -> tuple[float, float, float, float] | None:
    """A screen's left, top, width and height, in pywebview's units."""
    try:
        return (
            float(screen.x) / scale,
            float(screen.y) / scale,
            float(screen.width) / scale,
            float(screen.height) / scale,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _main_screen(screens: list[Any]) -> Any:
    """The main screen: the one whose corner is the origin, else the first."""
    for screen in screens:
        if getattr(screen, "x", None) == 0 and getattr(screen, "y", None) == 0:
            return screen
    return screens[0]


def default_floating_place(
    screens: list[Any], size: tuple[int, int] = FLOATING_SIZE, scale: float = 1.0
) -> tuple[int, int] | None:
    """Where the floating window first opens: the top-right corner of the
    main screen, in pywebview's units (``scale``: see ``pywebview_scale``),
    or ``None`` (the system's choice) if no screen is known."""
    if not screens:
        return None
    box = _screen_box(_main_screen(screens), scale)
    if box is None:
        return None
    left, top, width, _height = box
    return int(left + max(0.0, width - size[0] - FLOATING_MARGIN[0])), int(top + FLOATING_MARGIN[1])


def on_a_screen(
    place: tuple[int, int], screens: list[Any], size: tuple[int, int] = FLOATING_SIZE, scale: float = 1.0
) -> bool:
    """Would a window at ``place`` show enough of itself on some screen to
    be seen and dragged? (A place remembered on a screen that has since gone,
    or one worked out wrong, would put it where nobody can reach it.)"""
    if not screens:
        return True  # nothing to judge by: the place is taken as it is
    x, y = place
    for screen in screens:
        box = _screen_box(screen, scale)
        if box is None:
            continue
        left, top, width, height = box
        # Its top strip (what it is dragged by) at least half on this screen.
        if left - size[0] / 2 <= x <= left + width - size[0] / 2 and top <= y <= top + height - 24:
            return True
    return False


class FloatingWindow:
    """The floating window at ``url``: frameless, always on top, dragged by
    any part of it, and not taking the keyboard from the game when clicked.

    ``set_shown`` opens and closes it, from any thread and without waiting:
    one thread of its own does the opening and closing, in order, once the
    main window is up (``can_open``). ``place`` says where to open it -
    ``(x, y)``, or ``None`` for the top-right corner of the screen;
    ``on_moved(x, y)`` hears where it was dragged to, once the drag has
    settled; ``on_closed`` hears that it was closed by something other than
    ``set_shown`` (the system, Alt+F4).
    """

    def __init__(
        self,
        url: str,
        *,
        place: Callable[[], tuple[int, int] | None] = lambda: None,
        on_moved: Callable[[int, int], None] = lambda x, y: None,
        on_closed: Callable[[], None] = lambda: None,
    ) -> None:
        self.url = url
        self._place = place
        self._on_moved = on_moved
        self._on_closed = on_closed
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._wanted = False
        self._finished = False
        self._ready = False
        self._window: Any = None
        self._closing_it = False  # closed by set_shown(False): not news for on_closed
        self._moved_to: tuple[int, int] | None = None
        self._moved_timer: threading.Timer | None = None
        self._thread = threading.Thread(target=self._work, name="walka-floating", daemon=True)
        self._thread.start()

    @property
    def shown(self) -> bool:
        with self._lock:
            return self._window is not None

    def set_shown(self, shown: bool) -> None:
        with self._lock:
            self._wanted = bool(shown)
            self._wake.notify_all()

    def can_open(self) -> None:
        """The main window is up: windows can be opened beside it now."""
        with self._lock:
            self._ready = True
            self._wake.notify_all()

    def finish(self) -> None:
        """Close it for good: Walka is closing."""
        with self._lock:
            self._finished = True
            self._wanted = False
            self._wake.notify_all()

    # ---- its thread -------------------------------------------------------------------------
    def _work(self) -> None:
        while True:
            with self._lock:
                while True:
                    window = self._window
                    want_open = self._wanted and self._ready and not self._finished
                    if want_open and window is None:
                        action = "open"
                        break
                    if not want_open and window is not None:
                        action = "close"
                        break
                    if self._finished:
                        return
                    self._wake.wait()
            try:
                if action == "open":
                    self._open()
                else:
                    self._close(window)
            except Exception:
                log.exception("the floating window could not be %s", "opened" if action == "open" else "closed")
                with self._lock:
                    # Not tried again and again: the setting stays as it is,
                    # and the next change of it tries again.
                    self._wanted = self._window is not None

    def _open(self) -> None:
        import webview

        place = None
        try:
            screens = list(webview.screens or [])
            scale = pywebview_scale()
            place = self._place()
            if place is not None and not on_a_screen(place, screens, scale=scale):
                log.info("the floating window's last place is on no screen now: it opens in the corner")
                place = None
            place = place or default_floating_place(screens, scale=scale)
        except Exception as exc:
            log.debug("no place for the floating window: %s", exc)
        x, y = place if place else (None, None)
        window = webview.create_window(
            FLOATING_TITLE,
            self.url,
            width=FLOATING_SIZE[0],
            height=FLOATING_SIZE[1],
            x=x,
            y=y,
            resizable=False,
            frameless=True,
            easy_drag=False,  # dragged by its .pywebview-drag-region, so that its button can be pressed
            on_top=True,
            focus=False,  # a click on it leaves the keyboard with the game
            background_color=FLOATING_BACKGROUND,
            min_size=FLOATING_SIZE,
        )
        if window is None:
            return
        window.events.moved += self._moved
        window.events.closed += self._closed
        with self._lock:
            self._window = window
        log.info("the floating window is open")

    def _close(self, window: Any) -> None:
        with self._lock:
            self._closing_it = True
        try:
            window.destroy()
        finally:
            with self._lock:
                if self._window is window:
                    self._window = None
                self._closing_it = False
        self._remember_place()
        log.info("the floating window is closed")

    # (pywebview calls these on threads of its own.)
    def _moved(self, x: Any, y: Any) -> None:
        try:
            place = (int(x), int(y))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._moved_to = place
            if self._moved_timer is not None:
                self._moved_timer.cancel()
            timer = threading.Timer(MOVED_SETTLE_S, self._remember_place)
            timer.daemon = True
            self._moved_timer = timer
        timer.start()

    def _remember_place(self) -> None:
        with self._lock:
            place, self._moved_to = self._moved_to, None
            if self._moved_timer is not None:
                self._moved_timer.cancel()
                self._moved_timer = None
        if place is not None:
            try:
                self._on_moved(*place)
            except Exception:
                log.exception("could not remember where the floating window is")

    def _closed(self) -> None:
        with self._lock:
            by_us = self._closing_it or self._finished
            self._window = None
            if not by_us:
                self._wanted = False
        if not by_us:
            self._remember_place()
            try:
                self._on_closed()
            except Exception:
                log.exception("while the floating window was closing")


# ---- when there is no window at all (Windows) ----------------------------------------------
#
# The packaged program for Windows is a windowed one: started with a
# double-click it has no console either. If the dashboard then has to go to
# the browser, nothing of Walka itself is on screen - nothing to close it
# with, and nowhere to say that it could not start. A message box is.

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000


def no_console() -> bool:
    """Is this a Windows program without a console window?"""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return not ctypes.WinDLL("kernel32").GetConsoleWindow()
    except (AttributeError, OSError):
        return False


def message_box(text: str, *, error: bool = False) -> None:
    """Show a message box and wait until it is dismissed. (Windows only;
    elsewhere it returns at once.)"""
    if sys.platform != "win32":
        return
    import ctypes

    icon = MB_ICONERROR if error else MB_ICONINFORMATION
    try:
        ctypes.WinDLL("user32").MessageBoxW(None, text, TITLE, MB_OK | icon | MB_SETFOREGROUND)
    except (AttributeError, OSError) as exc:
        log.debug("no message box: %s", exc)
