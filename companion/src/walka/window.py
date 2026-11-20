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

    def __init__(self, url: str, storage: Path, *, on_closing: Callable[[], None]) -> None:
        self.url = url
        self.storage = storage
        self._on_closing = on_closing
        self._lock = threading.Lock()
        self._window = None
        self._close_asked = False

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
        with self._lock:
            self._close_asked = True
            window = self._window
        if window is None:
            return
        try:
            window.destroy()  # waits until the window is there to be closed
        except Exception as exc:  # already gone
            log.debug("closing the window: %s", exc)

    # (pywebview calls these with no arguments: it looks at the signature.)
    def _closing(self) -> None:
        try:
            self._on_closing()
        except Exception:
            log.exception("while Walka's window was closing")

    def _shown(self) -> None:
        import webview

        renderer = getattr(webview, "renderer", None) or "?"
        log.info("Walka's window is open (drawn by %s)", renderer)
        if renderer == "mshtml":
            # Internet Explorer's engine: what pywebview falls back to on
            # Windows. unavailable() should have kept us from getting here.
            log.warning("the window is drawn by Internet Explorer's engine, which cannot show the dashboard")

    def _loaded(self) -> None:
        log.info("the dashboard is loaded in Walka's window")


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
