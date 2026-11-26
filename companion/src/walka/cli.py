"""Command line entry point: ``walka`` (or ``python -m walka``)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import logging.handlers
import os
import queue
import re
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, addon_sync
from . import config as config_module
from .journey import DEMO_KEY, demo_journey, djb2, name_of
from .road import demo_road

log = logging.getLogger("walka")

DISCLAIMER_URL = "https://github.com/AntonyPerez0/walka-addon/blob/HEAD/DISCLAIMER.md"

# Shown every time the companion starts: most people get Walka from the
# download button and never see the repository's README or DISCLAIMER.md.
LISTENS_ONLY = (
    "\nWalka only listens to your treadmill: it cannot start, stop or change the speed of the belt.\n"
    "You set the speed on the treadmill itself. Walking while you play is at your own risk.\n"
    f"  disclaimer: {DISCLAIMER_URL}\n"
)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _dashboard_goes(args: argparse.Namespace, cfg: config_module.Config) -> str:
    """Where the dashboard is shown when Walka starts: in Walka's own
    ``"window"``, in the web ``"browser"``, or ``"nowhere"`` (it is still
    served, for whoever opens its address)."""
    if args.no_window or not cfg.web.open_browser:
        return "nowhere"
    if args.browser or not cfg.web.window:
        return "browser"
    from . import window

    reason = window.unavailable()
    if reason is None:
        return "window"
    log.info("the dashboard opens in your web browser, not in a window of Walka's own: %s", reason)
    return "browser"


def _data_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) if getattr(args, "data_dir", None) else config_module.default_data_dir()


def _build_replay(cfg: config_module.Config, path: str):
    """--replay: a recording made with ``probe --record``, played back."""
    from .recording import load
    from .treadmill.replay import ReplayTreadmill

    return ReplayTreadmill(load(path), units=cfg.treadmill.units)


def _build_treadmill(cfg: config_module.Config, simulate: bool):
    if simulate or cfg.treadmill.kind == "simulated":
        from .treadmill.simulated import SimulatedTreadmill

        return SimulatedTreadmill(us_firmware=cfg.units == "mph")
    from .treadmill import readers

    return readers.build(
        cfg.treadmill.kind if cfg.treadmill.kind in readers.KINDS else "ftms",
        cfg.treadmill.address or None,
        name_prefixes=cfg.treadmill.name_prefixes,
        units=cfg.treadmill.units,
    )


# ---- the Walka account's part ---------------------------------------------------------

# Where the accounts are, and the key that says which project is asked - its
# PUBLISHABLE key, the one that stands in the website's own pages - is built
# into Walka (walka_account/project.py, and nowhere else). Somebody who runs
# Walka from its source code can name a database of their own instead, on
# their own computer or elsewhere, with these two variables. (Not
# SUPABASE_URL and SUPABASE_KEY, which other tools export.)
ACCOUNT_URL_VARIABLE = "WALKA_ACCOUNT_URL"
ACCOUNT_KEY_VARIABLE = "WALKA_ACCOUNT_KEY"
# The oldest aiohttp the part's client was tried on (and on 3.14.5). With
# 3.9.0 a request that is cut off leaves a task behind, and closing Walka
# must leave none.
ACCOUNT_NEEDS_AIOHTTP = (3, 9, 5)


def _aiohttp_is_new_enough() -> bool:
    import aiohttp

    numbers = []
    for part in str(getattr(aiohttp, "__version__", "")).split(".")[:3]:
        digits = re.match(r"\d+", part)
        if digits is None:
            break
        numbers.append(int(digits.group()))
    return len(numbers) >= 2 and tuple(numbers) >= ACCOUNT_NEEDS_AIOHTTP


def _open_sign_in_page(url: str) -> None:
    """Open the page where the person signs in to their Walka account, in
    their own web browser. On a thread of its own, like the dashboard:
    launching a browser can initialise COM on the calling thread in a mode
    the Bluetooth stack cannot use - and the caller is the event loop. (The
    address is a key of the sign-in: it is never written to the log.)"""
    threading.Thread(target=_open_browser, args=(url,), name="walka-browser", daemon=True).start()


def _build_walka_account(data_dir: Path, treadmill, environ=os.environ):
    """The part that signs in to a Walka account and sends walks to it
    (``walka_account``) - or None, and then Walka has no such part at all:
    no card on the dashboard, no key in the snapshot, routes that answer 404.

    The packaged program (Walka.exe, Walka on a Mac) and Walka run from
    its source code both have it, made with the address and the key that
    are built in (``walka_account/project.py``). Having it asks nobody:
    the part opens no connection until somebody has signed in.

    From source - and only there - the two variables name another
    database instead: a developer's own. Both must be set, the address
    must be one Walka takes and the key not a secret one. One of them
    alone is refused, and then there is no part at all - not the built-in
    one either: whoever set a variable did not ask for the real accounts.
    The packaged program does not so much as read them: whatever they
    hold, it knows the built-in address and no other.

    None with the simulated treadmill, always: a walk made with it never
    reaches a Walka account. (And only with an aiohttp the part's client
    was tried on: from source, whichever is installed is used.)

    What is said in the log names a variable, never what it holds - and
    never the built-in address or key.
    """
    url = key = None
    if not _is_packed():
        url, key = environ.get(ACCOUNT_URL_VARIABLE), environ.get(ACCOUNT_KEY_VARIABLE)
    own = bool(url or key)  # somebody's own database, named - whole or by half - by the variables
    # (What is said quietly: to whoever set the variables, in a line of the log. Without them
    # nobody asked, and it is for "walka -v" only.)
    why, level, quietly = None, logging.WARNING, logging.INFO if own else logging.DEBUG
    try:
        from .walka_account import https, keystore, project
        from .walka_account.service import WalkaAccount

        if not own:
            url, key = project.ADDRESS, project.PUBLISHABLE_KEY
            if not url or not key:
                return None  # a Walka that was given no address: nothing is said, and nothing is there
        origin = https.parse_origin(url) if url and key else None
        if not url or not key:
            why = f"{ACCOUNT_URL_VARIABLE} and {ACCOUNT_KEY_VARIABLE} must both be set"
        elif origin is None and not own:
            why = "the address built into Walka is not one it asks"
        elif origin is None:
            why = (
                f"{ACCOUNT_URL_VARIABLE} is not an address Walka asks (HTTPS only - or, for a database on this "
                "computer, 127.0.0.1 with its port - and nothing after the address)"
            )
        elif not https.key_is_usable(key) and not own:
            why = "the key built into Walka is not one it uses"
        elif not https.key_is_usable(key):
            why = f"{ACCOUNT_KEY_VARIABLE} is not a key Walka uses (the accounts' publishable key, never a secret one)"
        elif treadmill.kind == "simulated":
            # (--simulate, --demo, or "kind": "simulated" in config.json. Nothing is wrong: said quietly.)
            why, level = "the treadmill is the simulated one, and made-up walks never go to a Walka account", quietly
        elif not _aiohttp_is_new_enough():
            why = "it needs aiohttp {}.{}.{} or newer".format(*ACCOUNT_NEEDS_AIOHTTP)
        else:
            part = WalkaAccount(origin, key, data_dir, keystore=keystore.open_keystore(), opener=_open_sign_in_page)
            if own:
                log.info(
                    "Walka has its Walka account's part: it is run from its source code, with %s and %s set",
                    ACCOUNT_URL_VARIABLE,
                    ACCOUNT_KEY_VARIABLE,
                )
            else:
                log.debug("Walka has its Walka account's part")
            return part
    except Exception as exc:
        # Walka works in full without the part: an error here must not keep
        # it from starting. (The kind of error only: its words could hold
        # the address or the key.)
        why = f"it could not be made ready (an error of the kind {type(exc).__name__})"
    log.log(level, "Walka has no Walka account's part: %s", why)
    return None


DEMO_SESSION_STARTS_AFTER_S = 2.0
DEMO_SAVES_EVERY_S = 10.0  # how often the made-up game "saves its file"


async def _demo_game(app, stop: asyncio.Event) -> None:
    """--demo: a made-up game for the walks to be filed under.

    A real game says what it did only in its saved file, at a logout or
    /reload. The demo's game "saves" every few seconds: one walking session,
    marked from a moment after the start and still running, and the journey
    of a made-up character for the Share page."""
    from .game_file import GameSession, SavedGame

    started = time.time() + DEMO_SESSION_STARTS_AFTER_S
    journey = demo_journey()
    while not stop.is_set():
        # (A real game's file says how long a running session had gone on
        # when it was saved. The demo's says: until its next save - so that
        # a walk that ends before then is filed under it too.)
        until = max(time.time(), started) + DEMO_SAVES_EVERY_S
        session = GameSession(started, until, 1, djb2(DEMO_KEY), name_of(DEMO_KEY), running=True)
        app.game.use_demo(SavedGame((session,), (journey,)))
        app.take_game_file()
        try:
            await asyncio.wait_for(stop.wait(), DEMO_SAVES_EVERY_S)
        except TimeoutError:
            pass


DEMO_WALK_STARTS_AFTER_S = 3.0
DEMO_SPEEDS_KMH = (3.2, 2.4, 3.7, 2.9)  # what the made-up walker sets on the remote, one after the other
DEMO_SPEED_FOR_S = 45.0


async def _demo_walker(treadmill, stop: asyncio.Event) -> None:
    """--demo: somebody to walk on the simulated pad. They press start on
    its remote - the companion could not - and change the speed now and
    then, the way a walker does. The companion sees none of this but what
    the pad reports, exactly as with a real one."""
    await asyncio.sleep(DEMO_WALK_STARTS_AFTER_S)
    treadmill.press_start()
    step = 0
    while not stop.is_set():
        # (Does nothing to a belt that was stopped with the simulator's
        # buttons on the dashboard: that remote is the user's too.)
        treadmill.press_speed(DEMO_SPEEDS_KMH[step % len(DEMO_SPEEDS_KMH)])
        step += 1
        await asyncio.sleep(DEMO_SPEED_FOR_S)


# ---- the console, and being closed from outside --------------------------------------

ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
STD_INPUT_HANDLE = 0xFFFFFFF6  # (DWORD)-10


def _without_quick_edit(mode: int) -> int:
    """A console input mode with QuickEdit switched off."""
    return (mode & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS


def _disable_console_quick_edit() -> Callable[[], None]:
    """Switch off the console window's QuickEdit mode; returns a function
    that puts it back.

    With QuickEdit on (the default in the classic Windows console) one click
    inside the window starts a text selection, and from then on every write
    to the console blocks until the selection is dismissed.
    """

    def nothing() -> None:
        pass

    if sys.platform != "win32":
        return nothing
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = wintypes.DWORD()
        if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return nothing  # no console window, or input comes from a file or pipe
        original = mode.value
        if original == _without_quick_edit(original) or not kernel32.SetConsoleMode(
            handle, _without_quick_edit(original)
        ):
            return nothing

        def restore() -> None:
            kernel32.SetConsoleMode(handle, original)

        return restore
    except Exception:  # cosmetic next to everything else: never worth failing to start over
        return nothing


def _line_of_descent(parents: dict[int, int], pid: int, generations: int = 3) -> list[int]:
    """The process that started ``pid``, the one that started that, and so
    on, from ``parents`` (process id -> its parent's)."""
    line: list[int] = []
    seen = {pid}
    while len(line) < generations:
        parent = parents.get(pid)
        if not parent or parent in seen:  # (process ids are used again: no going round in circles)
            break
        line.append(parent)
        seen.add(parent)
        pid = parent
    return line


def _started_by() -> list[int]:
    """(Windows) The ids of the processes this one descends from, nearest first."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS: every process
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return _line_of_descent(parents, os.getpid())


def _attach_to_a_console_it_descends_from() -> bool:
    """(Windows) Take the console of whoever started this program, if they
    have one. Not just the parent's: packed into one file, Walka.exe is two
    processes - the one the command prompt starts unpacks the program and
    starts the other, which is this one - and the first has no console
    either."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AttachConsole.argtypes = [wintypes.DWORD]
        kernel32.AttachConsole.restype = wintypes.BOOL
        return any(kernel32.AttachConsole(pid) for pid in _started_by())
    except (AttributeError, OSError):
        return False


def _use_the_console_it_was_started_from(argv: Sequence[str]) -> None:
    """The packaged program for Windows is a windowed one, so that a
    double-click opens Walka's window and no console with it. The price:
    Windows gives such a program nowhere to print, even when it was started
    from a command prompt - and ``Walka.exe probe`` is all printing.

    So: started with a command or an option, from a command prompt, it
    writes to that prompt's window. (Started with nothing - as a
    double-click does - it leaves the prompt alone: it has its own window,
    and a program that has taken a console is closed with it.) Whatever is
    still missing after that becomes a stream that swallows what is written
    to it, so that nothing has to check before it prints.
    """
    if sys.platform != "win32" or None not in (sys.stdin, sys.stdout, sys.stderr):
        return
    attached = bool(argv) and _attach_to_a_console_it_descends_from()
    # (A stream that is there was sent somewhere on purpose - "> report.txt" - and is left alone.)
    for name, device, mode in (("stdin", "CONIN$", "r"), ("stdout", "CONOUT$", "w"), ("stderr", "CONOUT$", "w")):
        if getattr(sys, name) is not None:
            continue
        try:
            # Line by line: output must be on screen when it is written, as on any console.
            stream = open(device if attached else os.devnull, mode, encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            stream = open(os.devnull, mode, encoding="utf-8")
        setattr(sys, name, stream)


LOG_FILE = "walka.log"
LOG_FILE_BYTES = 512 * 1024  # then it starts over, keeping the one before as walka.log.1


def _log_file_handler(file: Path) -> logging.Handler | None:
    """Without a console, the log is the only place where Walka can say
    what went wrong. (With one it is the same lines, kept.)"""
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            file, maxBytes=LOG_FILE_BYTES, backupCount=1, encoding="utf-8", delay=True
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    return handler


def _start_logging(verbose: bool, stream=None, file: Path | None = None) -> Callable[[], None]:
    """Send log records to the console (``stream``: stderr unless given) -
    and to ``file``, if one is given - by way of a queue; returns a function
    that flushes and undoes it.

    The console is written to on a thread of its own. A console that is
    slow or stuck - a text selection, a paused scroll, a pipe nobody reads -
    can then hold up that thread only, never the event loop that is
    recording the walk.
    """
    console = logging.StreamHandler(stream)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    handlers: list[logging.Handler] = [console]
    kept = _log_file_handler(file) if file is not None else None
    if kept is not None:
        handlers.append(kept)
    records: queue.SimpleQueue = queue.SimpleQueue()  # unbounded: handing a record over never blocks
    listener = logging.handlers.QueueListener(records, *handlers)
    handler = logging.handlers.QueueHandler(records)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    listener.start()

    def stop() -> None:
        root.removeHandler(handler)
        root.setLevel(previous_level)

        # Write out what is still queued - but a stuck console must not keep
        # the program from exiting either.
        def flush() -> None:
            listener.stop()
            if kept is not None:
                kept.close()

        flusher = threading.Thread(target=flush, name="walka-log-flush", daemon=True)
        flusher.start()
        flusher.join(2.0)

    return stop


def _ignore_interrupts() -> Callable[[], None]:
    """Ignore Ctrl+C from now on; returns a function that listens again."""
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # not the main thread (embedded, or under test): not ours to change
        return lambda: None
    return lambda: signal.signal(signal.SIGINT, signal.SIG_DFL if previous is None else previous)


# How long whoever is about to end the process is kept waiting for the walk
# to be saved. (Windows allows a console program five seconds.)
SAVE_WAIT_S = 4.0


def _stop_and_wait(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event, wait_s: float = SAVE_WAIT_S
) -> None:
    """The process is about to be ended - Windows is killing it (console
    closed, log-off, shutdown), or Walka's window is closing - and whoever
    does that is waiting for our answer on a thread of their own: ask the
    event loop for an orderly stop, and hold the answer back until the walk
    has been saved - or the few seconds we are allowed are nearly up."""
    try:
        loop.call_soon_threadsafe(stop.set)
    except RuntimeError:  # the loop is already closed: everything is stopped
        return
    stopped.wait(wait_s)


def _install_console_close_handler(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event
) -> None:
    """On Windows, closing the console window kills the process within
    seconds. Catch that so the walk is saved first."""
    if sys.platform != "win32":
        return
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event: int) -> int:
        # CTRL_CLOSE_EVENT=2. (CTRL_LOGOFF_EVENT=5 and CTRL_SHUTDOWN_EVENT=6
        # are not delivered to a program that has loaded user32, as this one
        # has: see _watch_for_session_end.)
        if event in (2, 5, 6):
            _stop_and_wait(loop, stop, stopped)  # Windows waits for this handler before killing us
            return 1
        return 0

    _install_console_close_handler.keep = handler  # type: ignore[attr-defined]  # keep a reference
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, 1)


def _stop_on_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """Off Windows a program is closed from outside with a signal: SIGHUP
    when its terminal window is closed - which is how Walka is closed on a
    Mac - and SIGTERM at log-out, at shutdown and from ``kill``. Left alone,
    either ends the process on the spot, with the walk unsaved. Here they
    ask for the same orderly stop as Ctrl+C. (The handlers go when the
    event loop is closed.)"""
    if sys.platform == "win32":
        return
    for name in ("SIGTERM", "SIGHUP"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (AttributeError, NotImplementedError, RuntimeError, ValueError) as exc:
            # (Not the main thread - embedded, or under test: not ours to change.)
            log.debug("not listening for %s: %s", name, exc)


def _watch_for_session_end(loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event):
    """Save the walk when Windows logs off or shuts down, the same way as
    when the console window is closed. Returns the watcher (to be
    stopped)."""
    from .winsession import SessionEndWatcher

    watcher = SessionEndWatcher(lambda: _stop_and_wait(loop, stop, stopped))
    try:
        watcher.start()  # does nothing off Windows
    except Exception as exc:  # the companion runs without it
        log.debug("not watching for Windows shutting down: %s", exc)
    return watcher


@dataclass
class _Running:
    """The companion, up and serving its dashboard: what somebody on
    another thread - the window - needs to show the page and to stop it."""

    url: str
    loop: asyncio.AbstractEventLoop
    stop: asyncio.Event
    stopped: threading.Event  # set once the walk is saved
    app: Any = None  # the App: touched only on ``loop``, through ``call``

    def call(self, function: Callable[..., Any], *args: Any) -> None:
        """Run ``function(*args)`` on the companion's loop, from any thread."""
        if self.stopped.is_set():
            return
        try:
            self.loop.call_soon_threadsafe(function, *args)
        except RuntimeError:  # the loop is closed: Walka is on its way out
            pass

    def ask_to_stop(self, wait_s: float = SAVE_WAIT_S) -> None:
        """Ask for an orderly stop, and wait - that long at most - until the
        walk has been saved. From any thread but the companion's own."""
        _stop_and_wait(self.loop, self.stop, self.stopped, wait_s)


QUIT_BOX = (
    "Walka is running. Its dashboard is open in your web browser:\n\n{url}\n\n"
    "Leave this box open while you walk. OK closes Walka (the walk being recorded is saved first)."
)


def _offer_a_way_to_quit(running: _Running) -> None:
    """With its dashboard in the browser and no console (the packaged
    program on Windows, started with a double-click), nothing of Walka
    itself is on screen - nothing to close. A message box is something."""
    from . import window

    if not window.no_console():
        return

    def box() -> None:
        window.message_box(QUIT_BOX.format(url=running.url))
        running.ask_to_stop(0)

    threading.Thread(target=box, name="walka-quit-box", daemon=True).start()


def _could_not_start(text: str) -> None:
    """Say why Walka is not starting - where it can be seen."""
    from . import window

    print(text, file=sys.stderr)
    log.error("%s", text)
    if window.no_console():
        window.message_box(text, error=True)


async def _run(
    args: argparse.Namespace,
    *,
    cfg: config_module.Config | None = None,
    goes: str | None = None,
    started: Callable[[_Running], None] | None = None,
) -> int:
    """Run the companion until it is told to stop.

    ``goes``: where the dashboard is shown (see ``_dashboard_goes``; worked
    out here if not given). A ``"window"`` is not opened here - it needs the
    main thread, and this is then running on another: ``started`` is called
    once the dashboard is being served, and whoever gave it does the rest
    (``_run_in_window``).
    """
    from . import web
    from .app import App
    from .hr import HeartRateMonitor
    from .window import enable_dpi_awareness

    enable_dpi_awareness()
    data_dir = _data_dir(args)
    if cfg is None:
        cfg = config_module.load(data_dir)
    if goes is None:
        goes = _dashboard_goes(args, cfg)
        if goes == "window":
            goes = "browser"  # nobody is here to open one
    if args.port:
        cfg.web.port = args.port
    replay = getattr(args, "replay", None)
    simulate = bool(args.simulate or args.demo or replay)

    treadmill = _build_replay(cfg, replay) if replay else _build_treadmill(cfg, simulate)
    heart_rate = None if simulate else HeartRateMonitor(
        enabled=cfg.heart_rate.enabled, address=cfg.heart_rate.address, name=cfg.heart_rate.name
    )
    app = App(
        cfg,
        data_dir,
        treadmill=treadmill,
        heart_rate=heart_rate,
        walka_account=_build_walka_account(data_dir, treadmill),
        # (Made up: the walks are kept, marked, and the game is not shown them.)
        writes_addon=treadmill.kind != "simulated",
    )
    if args.demo:
        # The demo's made-up character must not turn up among the real ones later.
        app.journeys.use_memory_only(demo=True)
        app.roads.use_demo(demo_road(DEMO_KEY))

    stop = asyncio.Event()
    stopped = threading.Event()  # set once app.stop() has run: the walk is saved
    app.quit = stop.set  # (an update's restart: the same orderly stop as closing the window)
    loop = asyncio.get_running_loop()
    _install_console_close_handler(loop, stop, stopped)
    _stop_on_signals(loop, stop)
    session_end = _watch_for_session_end(loop, stop, stopped)

    runner = None
    demo_tasks: list[asyncio.Task] = []
    try:
        if args.demo:
            demo_tasks.append(asyncio.create_task(_demo_game(app, stop)))
            demo_tasks.append(asyncio.create_task(_demo_walker(treadmill, stop)))
        await app.start()
        port = cfg.web.port
        for attempt in range(10):
            try:
                runner = await web.serve(app, cfg.web.host, port + attempt)
                port += attempt
                break
            except OSError:
                continue
        if runner is None:
            _could_not_start(f"Could not open a port near {cfg.web.port} for the dashboard.")
            return 2
        shown_host = "127.0.0.1" if cfg.web.host in ("0.0.0.0", "::", "") else cfg.web.host
        url = f"http://{shown_host}:{port}/"
        in_window = started is not None and goes == "window"
        print(f"Walka companion {__version__}")
        print(f"  dashboard: {url}")
        print(f"  data:      {data_dir}")
        if replay:
            print(f"  treadmill: PLAYING BACK {replay} (no Bluetooth)")
        elif simulate:
            print("  treadmill: SIMULATED (no Bluetooth)")
        if in_window:
            print("Close Walka's window to quit (the walk being recorded is saved first).")
        else:
            print("Press Ctrl+C to quit (the walk being recorded is saved first).")
        print(LISTENS_ONLY)
        log.info("Walka %s is running: dashboard at %s, data in %s", __version__, url, data_dir)
        running = _Running(url, loop, stop, stopped, app)
        if started is not None:
            started(running)
        if goes == "browser":
            # On its own thread: launching a browser can initialise COM on
            # the calling thread in a mode the Bluetooth stack cannot use.
            threading.Thread(target=_open_browser, args=(url,), name="walka-browser", daemon=True).start()
            _offer_a_way_to_quit(running)
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        # Shutdown has begun, and it ends with the walk being saved. A
        # second Ctrl+C (a double tap, key repeat) would abort it half way,
        # so it is ignored until that is done.
        listen_to_interrupts = _ignore_interrupts()
        for task in demo_tasks:
            task.cancel()
        try:
            await app.stop()
        finally:
            stopped.set()
            listen_to_interrupts()
            session_end.stop()
            if runner is not None:
                await runner.cleanup()
    return 0


# ---- with a window of its own ---------------------------------------------------------------

# Where the window keeps what the page stores between runs, in the data folder.
WINDOW_DATA = "window"
# How long the companion gets to finish stopping once its walk is saved
# (letting go of the treadmill, closing the server) before Walka exits anyway.
STOP_WAIT_S = 10.0


def _hear_signals(
    callback: Callable[[], None], names: Sequence[str] = ("SIGINT", "SIGTERM", "SIGHUP")
) -> Callable[[], None]:
    """Off Windows, with the main thread inside the window's event loop:
    call ``callback`` when one of these signals arrives; returns a function
    that undoes it.

    Python runs a signal handler on the main thread, between two steps of
    Python code - and a main thread that is waiting in the system's event
    loop for the next click is running none. A ``kill`` would then be
    heard when the mouse next moved over the window, or never. So the
    handlers installed here do nothing; what is used is the one thing
    Python does the moment a signal arrives, on whatever thread: it writes
    the signal's number to a socket (``signal.set_wakeup_fd``). A thread
    reads the other end.

    Must be called on the main thread; anywhere else it does nothing.
    """

    def nothing() -> None:
        pass

    if sys.platform == "win32":
        return nothing
    import socket

    wanted = {getattr(signal, name) for name in names if hasattr(signal, name)}
    reader, writer = socket.socketpair()
    writer.setblocking(False)
    previous: dict[int, object] = {}
    try:
        old_fd = signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)
        for number in wanted:
            previous[number] = signal.signal(number, lambda *_: None)
    except ValueError:  # not the main thread (embedded, or under test): not ours to change
        reader.close()
        writer.close()
        return nothing

    def listen() -> None:
        while True:
            try:
                numbers = reader.recv(32)
            except OSError:
                return
            if not numbers:
                return  # undone
            if wanted.intersection(numbers):
                callback()

    threading.Thread(target=listen, name="walka-signals", daemon=True).start()

    def undo() -> None:
        signal.set_wakeup_fd(old_fd)
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)
        writer.close()  # ends the listening thread
        reader.close()

    return undo


# The floating window's page, beside the dashboard's.
FLOATING_PATH = "floating"


def _floating_window(window: Any, running: _Running) -> Any:
    """The floating window (Settings -> Floating window), handed to the
    App, which shows and hides it as the setting says. What it reports -
    dragged here, closed - goes to the App on its own loop."""
    app = running.app
    if app is None:
        return None

    def place() -> tuple[int, int] | None:
        overlay = app.config.overlay  # (read only: a pair of numbers)
        if overlay.x is None or overlay.y is None:
            return None
        return overlay.x, overlay.y

    floating = window.FloatingWindow(
        running.url + FLOATING_PATH,
        place=place,
        on_moved=lambda x, y: running.call(app.overlay_moved, x, y),
        on_closed=lambda: running.call(app.overlay_closed),
    )
    running.call(app.attach_overlay, floating)
    return floating


def _run_in_window(args: argparse.Namespace, cfg: config_module.Config) -> int:
    """Run the companion with its dashboard in a window of Walka's own;
    closing the window closes Walka.

    The window has the main thread (see ``window.py``). The companion -
    ``_run``, with its event loop - gets a thread of its own, and is
    stopped from here the way the system stops it from a thread of its
    own when it logs off: ask, and wait until the walk is saved.
    """
    from . import window

    started: queue.SimpleQueue[_Running | None] = queue.SimpleQueue()
    codes: list[int] = []

    def companion() -> None:
        try:
            codes.append(asyncio.run(_run(args, cfg=cfg, goes="window", started=started.put)))
        except BaseException:
            log.exception("Walka stopped on an error")
            codes.append(1)
        finally:
            started.put(None)  # (if it never got as far as serving: do not wait for it)

    # (A daemon: every way out of here asks it to stop and waits for it. The
    # one that cannot - Ctrl+C before it is even up - has no walk to lose.)
    thread = threading.Thread(target=companion, name="walka-companion", daemon=True)
    thread.start()
    running = started.get()
    if running is None:  # it could not start, and has said why
        thread.join()
        return codes[0]

    floating = _floating_window(window, running)
    dashboard = window.DashboardWindow(
        running.url, _data_dir(args) / WINDOW_DATA, on_closing=running.ask_to_stop, floating=floating
    )

    def close_with_the_companion() -> None:
        # The companion can stop without the window having been closed:
        # Windows logging off, a kill. The window goes with it.
        running.stopped.wait()
        dashboard.close()

    threading.Thread(target=close_with_the_companion, name="walka-window-closer", daemon=True).start()
    unhear = _hear_signals(lambda: running.ask_to_stop(0))
    try:
        dashboard.run()  # until the window is closed
    except Exception:
        if not running.stopped.is_set():
            log.exception("Walka's window could not be opened: the dashboard is in your web browser instead")
            _open_browser(running.url)
            _offer_a_way_to_quit(running)
            try:
                while not running.stopped.wait(0.5):  # until Ctrl+C, a signal, or the box
                    pass
            except KeyboardInterrupt:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        # Closing the window has asked already (DashboardWindow's
        # on_closing): this is for every other way of getting here.
        running.ask_to_stop()
        thread.join(STOP_WAIT_S)
        unhear()
    return codes[0] if codes else 1


def _run_command(args: argparse.Namespace) -> int:
    cfg = config_module.load(_data_dir(args))
    goes = _dashboard_goes(args, cfg)
    if goes == "window":
        return _run_in_window(args, cfg)
    return asyncio.run(_run(args, cfg=cfg, goes=goes))


async def _scan(args: argparse.Namespace) -> int:
    from .treadmill.ftms_ble import FtmsTreadmill, describe_bluetooth_error

    cfg = config_module.load(_data_dir(args))
    treadmill = FtmsTreadmill(name_prefixes=cfg.treadmill.name_prefixes)
    print(f"Scanning for treadmills for {args.seconds:.0f} s ...")
    try:
        found = await treadmill.discover(args.seconds)
    except Exception as exc:  # no adapter, Bluetooth switched off, ...
        print(f"Bluetooth is not available: {describe_bluetooth_error(exc)}")
        return 2
    if not found:
        print("None found. Is the treadmill on and in range, and is its own phone app closed?")
        return 1
    for device in found:
        tag = "FTMS" if device.ftms else "name match"
        if device.treadmill:
            tag += ", says it is a treadmill"
        print(f"  {device.address}  rssi={device.rssi}  {device.name or '(no name)'}  [{tag}]")
    return 0


async def _probe(args: argparse.Namespace) -> int:
    from . import probe
    from .recording import Recording, recording_path
    from .treadmill.ftms_ble import describe_bluetooth_error

    cfg = config_module.load(_data_dir(args))
    recording = Recording(walka=__version__) if args.record else None
    try:
        lines = await probe.probe(
            address=args.address or cfg.treadmill.address or None,
            name_prefixes=cfg.treadmill.name_prefixes,
            listen_s=args.seconds,
            all_characteristics=args.all,
            recording=recording,
        )
    except Exception as exc:  # no adapter, Bluetooth switched off, the pad dropped the link, ...
        print(f"Bluetooth problem: {describe_bluetooth_error(exc)}")
        return 2
    path = probe.save_report(lines, Path(args.out) if args.out else Path.cwd())
    print(f"\nReport saved to {path}")
    if recording is not None and recording.services:
        print(
            f"Recording saved to {recording.save(recording_path(path))} ({len(recording.notifications)} notifications)"
        )
    print(
        "The saved report is made to be posted: it lists fitness machines only, cuts Bluetooth addresses short\n"
        "and leaves out the treadmill's serial number. (What you see above is the whole of it.)"
    )
    return 0


def _game(args: argparse.Namespace) -> int:
    """What the game has saved for Walka: the files found and what they hold."""
    from .game_file import GameFiles

    cfg = config_module.load(_data_dir(args))
    folders = addon_sync.game_dirs(cfg.wow.addon_dir or None)
    if not folders:
        print("World of Warcraft was not found. Install the Walka addon (walka install-addon), or set its folder in")
        print("the dashboard's Settings.")
        return 1
    files = GameFiles(lambda: folders)
    files.scan(force=True)
    status = files.status()
    if not status["files"]:
        print("The game has not saved anything for Walka yet. It does when you log out or type /reload, with the")
        print("Walka addon enabled (AddOns button on the character screen).")
        return 1
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(status["saved_at"])) if status["saved_at"] else "?"
    print(f"The game's saved file: {status['files']} account(s), last written {when}.")
    sessions = files.sessions()
    print(f"  {len(sessions)} walking session(s) marked in the game.")
    if sessions:
        last = sessions[-1]
        whose = f" for {last.character}" if last.character else ""
        print(
            f"  The newest: {time.strftime('%Y-%m-%d %H:%M', time.localtime(last.start))} to "
            f"{time.strftime('%H:%M', time.localtime(last.end))}{whose}"
            + (" (still running when the game saved)." if last.running else ".")
        )
    names = ", ".join(sorted({journey.name for journey in files.journeys()})) or "none yet"
    print(f"  Characters: {names}.")
    return 0


def _install_addon(args: argparse.Namespace) -> int:
    if args.dir:
        targets = [Path(args.dir)]
    else:
        targets = addon_sync.find_addons_dirs()
    if not targets:
        print("No World of Warcraft AddOns folder found. Pass it with --dir, e.g.")
        if sys.platform == "win32":
            print(r'  walka install-addon --dir "C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns"')
        else:
            print('  walka install-addon --dir "/Applications/World of Warcraft/_forever_/Interface/AddOns"')
        return 1
    try:
        installed = addon_sync.install_addon(targets[0])
    except (OSError, FileNotFoundError) as exc:
        print(f"Install failed: {exc}")
        return 1
    print(f"Installed to {installed}")
    print("Restart the game (or /reload) and enable Walka in the AddOns list.")
    return 0


# What the packaged program (Walka.exe, or Walka.app on a Mac) must be able to
# import at run time. Most of the Windows list is imported by name from
# inside compiled modules, or chosen at run time, where PyInstaller cannot
# see it - so a build can be missing one and still start. "walka selfcheck"
# imports them all, and the build's smoke test runs it: a missing module
# then fails the build, not the first Bluetooth scan on somebody's computer.
RUNTIME_MODULES: tuple[str, ...] = (
    "aiohttp.web",
    "bleak",
    "walka.web",
    "walka.probe",
    "walka.game_file",
    "walka.treadmill.bleak_backend",
    # What asks a Walka account (walka/walka_account), every module of it.
    # aiohttp starts without "ssl" and says nothing: only naming it shows
    # that it is packed.
    "ssl",
    "walka.walka_account.calls",
    "walka.walka_account.https",
    "walka.walka_account.keystore",
    "walka.walka_account.ledger",
    "walka.walka_account.project",
    "walka.walka_account.row",
    "walka.walka_account.sender",
    "walka.walka_account.service",
    "walka.walka_account.signin",
    "walka.walka_account.threads",
)
WINDOWS_RUNTIME_MODULES: tuple[str, ...] = (
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",  # the vectors every advertisement and service list arrives in
    "winrt.windows.devices.bluetooth",
    "winrt.windows.devices.bluetooth.advertisement",
    "winrt.windows.devices.bluetooth.genericattributeprofile",
    "winrt.windows.devices.enumeration",
    "winrt.windows.devices.radios",
    "winrt.windows.storage.streams",
    "bleak.backends.winrt.scanner",
    "bleak.backends.winrt.client",
    "bleak.backends.winrt.util",
    # Walka's window: pywebview, .NET (pythonnet) and the WebView2 libraries it brings.
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
)
MACOS_RUNTIME_MODULES: tuple[str, ...] = (
    "bleak.backends.corebluetooth.scanner",
    "bleak.backends.corebluetooth.client",
    # Walka's window: pywebview on WebKit.
    "webview",
    "webview.platforms.cocoa",
)


def runtime_modules(platform: str = sys.platform) -> tuple[str, ...]:
    """Everything a build for ``platform`` imports at run time."""
    extra = {"win32": WINDOWS_RUNTIME_MODULES, "darwin": MACOS_RUNTIME_MODULES}.get(platform, ())
    return RUNTIME_MODULES + extra


def missing_page_files(static: Path) -> list[str]:
    """Files the dashboard page needs that are not there: what index.html
    loads, and whatever those scripts import in turn (a script left out of
    the build would only show as a view that stays empty)."""
    page = static / "index.html"
    if not page.is_file():
        return ["index.html"]
    wanted = re.findall(r"""(?:src|href)=["'](?!https?:|//|#|data:)/?(?:static/)?([^"'#?]+)""", page.read_text("utf-8"))
    seen: set[str] = set()
    missing: list[str] = []
    while wanted:
        name = wanted.pop()
        if name in seen:
            continue
        seen.add(name)
        file = static / name
        if not file.is_file():
            missing.append(name)
        elif file.suffix == ".js":
            imports = re.findall(
                r"""^\s*(?:import|export)\b[^;]*?\bfrom\s*["']\./([^"']+)["']""", file.read_text("utf-8"), re.M
            )
            wanted += [str(Path(name).parent / target).replace("\\", "/").removeprefix("./") for target in imports]
    return sorted(missing)


# The licences of the libraries packed into the program: written by the
# build (companion/third_party.py), so only a packaged program has them.
LICENCES_FILE = Path(__file__).parent / "THIRD-PARTY-LICENSES.txt"


def _licenses() -> int:
    """Print the licences of what is packed into this program."""
    try:
        text = LICENCES_FILE.read_text("utf-8")
    except OSError:
        print(
            "This is Walka run from its source code, so nothing is packed with it: the libraries it uses are\n"
            "the ones installed on this computer, each with its own licence ('pip show <name>' says which).\n"
            "Walka's own licence is in LICENSE, and what it owes to other projects in THIRD-PARTY-NOTICES.md.\n"
            "The packaged program (Walka.exe, Walka on a Mac) prints the licences of everything in it here."
        )
        return 0
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:  # a console that cannot show an author's name as it is written
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
    return 0


def _selfcheck() -> int:
    """Hidden command: is everything this build needs at run time there?
    Prints one line; exits 0 if so."""
    modules = runtime_modules()
    problems = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            problems.append(f"cannot import {name} ({type(exc).__name__}: {exc})")
    static = Path(__file__).parent / "static"
    absent = missing_page_files(static)
    if absent:
        problems.append(f"the dashboard page is missing files ({', '.join(absent)} in {static})")
    if addon_sync.bundled_addon_dir() is None:
        problems.append("the addon folder is missing")
    if getattr(sys, "frozen", False) and not LICENCES_FILE.is_file():
        problems.append("the licences of the packed libraries are missing")
    if problems:
        print("selfcheck FAILED: " + "; ".join(problems))
        return 1
    import platform
    import ssl

    # (The build compares these with what it was built with: see ci.yml.)
    print(
        f"selfcheck OK: {len(modules)} modules, the dashboard page and the addon folder are all there; "
        f"Python {platform.python_version()}, {ssl.OPENSSL_VERSION}"
    )
    return 0


def _is_packed() -> bool:
    """Is this the packaged program (Walka.exe, Walka on a Mac), and not Walka run from its source code?"""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


# "walka httpscheck": the one page it fetches. A constant, and the command
# takes no address: it cannot be made to ask anybody else.
HTTPSCHECK_HOST = "walka.dev"
HTTPSCHECK_TRIES = 3  # the internet between a build machine and a website is not always there
HTTPSCHECK_PAUSE_S = 1.0
HTTPSCHECK_TRY_S = 10.0


class _HttpsCheckFailed(Exception):
    """What "walka httpscheck" found wrong, as the sentence it prints."""


async def _httpscheck_steps(refuse_port: int | None, mono: Callable[[], float] = time.monotonic) -> list[str]:
    """What the check found, piece by piece - each with the seconds it
    took (by ``mono``), and the page with every try: the build's log then
    shows where the time of the check went, and whether the page answered
    at once or only at the last try."""
    import ssl

    from .walka_account import https

    if refuse_port is not None and not 1 <= refuse_port <= 65535:
        raise _HttpsCheckFailed("--refuse-port takes a port, 1 to 65535")
    began = mono()

    def since(moment: float) -> str:
        return f"{max(0.0, mono() - moment):.1f} s"

    # 1. Without asking anybody: is there anything to check a certificate against?
    try:
        report = https.roots_report(https.make_ssl_context())
    except Exception as exc:
        raise _HttpsCheckFailed(f"the root certificates could not be read ({type(exc).__name__})") from None
    if not report["count"]:
        raise _HttpsCheckFailed(
            f"there are no root certificates to check anybody's certificate against (from {report['source']})"
        )
    named = f" ({', '.join(report['named'])} among them)" if report.get("named") else ""
    said = [
        f"{ssl.OPENSSL_VERSION}, {'packed' if _is_packed() else 'run from source'}",
        f"{report['count']} root certificates from {report['source']}{named}, read in {since(began)}",
    ]

    # 2. A page that is there for everybody: it must answer, on a connection that was checked.
    page = f"https://{HTTPSCHECK_HOST}/"
    client = https.Client(https.Origin("https", HTTPSCHECK_HOST, None))
    tries: list[str] = []  # how long each try took - and, of those that got no answer, that they got none
    for attempt in range(1, HTTPSCHECK_TRIES + 1):
        asked = mono()
        try:
            answer = await client.request("GET", "/", timeout_s=HTTPSCHECK_TRY_S)
        except https.NetError as error:
            kind = error.kind
        else:
            tries.append(since(asked))
            said.append(f"{page} answered ({answer.status}) on a checked connection, on try {attempt}")
            break
        if kind == "too_big":  # an answer all the same - a long one
            tries.append(since(asked))
            said.append(f"{page} answered on a checked connection, on try {attempt}")
            break
        if kind == "certificate":
            raise _HttpsCheckFailed(f"the certificate of {page} could not be checked")
        if kind != "no_answer":
            raise _HttpsCheckFailed(f"{page} could not be asked ({kind})")
        tries.append(f"{since(asked)} no answer")
        if attempt == HTTPSCHECK_TRIES:
            raise _HttpsCheckFailed(f"{page} did not answer ({HTTPSCHECK_TRIES} tries): {', '.join(tries)}")
        await asyncio.sleep(HTTPSCHECK_PAUSE_S)
    said[-1] += f" ({', '.join(tries)})"

    # 3. A server with a certificate nobody vouches for (the build starts
    # one): it must be refused. Not answering is not refusing - a server
    # that is not there would pass for one that was turned down.
    if refuse_port is not None:
        there = f"127.0.0.1:{refuse_port}"
        asked = mono()
        try:
            answer = await https.Client(https.Origin("https", "127.0.0.1", refuse_port)).request(
                "GET", "/", timeout_s=HTTPSCHECK_TRY_S
            )
        except https.NetError as error:
            kind = error.kind
        else:
            raise _HttpsCheckFailed(
                f"the server on {there} was asked and answered ({answer.status}): its certificate was not checked"
            )
        if kind == "no_answer":
            raise _HttpsCheckFailed(f"nothing answered on {there}, and that is not a certificate refused")
        if kind != "certificate":
            raise _HttpsCheckFailed(f"the server on {there} was not refused for its certificate ({kind})")
        said.append(f"the home-made certificate on {there} was refused, and nothing was asked there ({since(asked)})")
    said.append(f"{since(began)} in all")
    return said


def _httpscheck(args: argparse.Namespace) -> int:
    """Hidden command, run by the build: does HTTPS work in THIS program,
    and does it check certificates? Prints one line; exits 0 if so.

    The part that makes HTTPS requests (walka/walka_account) makes none
    until somebody has signed in - on their own computer, where no build
    is looking. So this tries the client it asks with, here. Unlike the
    rest of Walka it asks another machine unasked: it fetches the front
    page of walka.dev, once. It reads no settings and no folder, and
    writes no log.
    """
    try:
        said = asyncio.run(_httpscheck_steps(args.refuse_port))
    except _HttpsCheckFailed as failed:
        print(f"httpscheck FAILED: {failed}")
        return 1
    except Exception as exc:  # (its name only: an error of the network can carry what was sent)
        print(f"httpscheck FAILED: {type(exc).__name__}")
        return 1
    print("httpscheck OK: " + "; ".join(said))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="walka",
        description="Walka companion: records your walks on a walking pad while you play. It only listens to the pad.",
    )
    parser.add_argument("--version", action="version", version=f"walka {__version__}")
    parser.add_argument("--data-dir", help="folder for config and recorded walks")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    # (The metavar lists the commands meant for people; "selfcheck" and "httpscheck" are for the build.)
    sub = parser.add_subparsers(dest="command", metavar="{run,scan,probe,game,install-addon,licenses}")

    run = sub.add_parser("run", help="run the companion and its dashboard (default)")
    for target in (parser, run):
        target.add_argument("--simulate", action="store_true", help="use a simulated treadmill (no Bluetooth)")
        target.add_argument("--demo", action="store_true", help="simulated treadmill, walker AND game character")
        # (Up to 0.10: do not read the screen. Nothing reads it now; still taken, for old shortcuts.)
        target.add_argument("--no-capture", action="store_true", help=argparse.SUPPRESS)
        target.add_argument(
            "--browser",
            action="store_true",
            help="show the dashboard in your web browser, not in a window of Walka's own",
        )
        # (--no-browser: its name from when the dashboard was a browser tab.)
        target.add_argument(
            "--no-window",
            "--no-browser",
            dest="no_window",
            action="store_true",
            help="do not show the dashboard at all (it is still served, for whoever opens its address)",
        )
        target.add_argument("--port", type=int, help="dashboard port (default 8787)")
        target.add_argument(
            "--replay",
            metavar="FILE",
            help="play back a recording made with 'probe --record' instead of using Bluetooth",
        )

    scan = sub.add_parser("scan", help="list nearby Bluetooth treadmills")
    scan.add_argument("--seconds", type=float, default=8.0)

    probe = sub.add_parser("probe", help="report of what the treadmill exposes, for a treadmill that is not read right")
    probe.add_argument("--address", help="Bluetooth address (default: first treadmill found)")
    probe.add_argument("--seconds", type=float, default=30.0, help="how long to listen for notifications")
    probe.add_argument("--out", help="folder for the report (default: current folder)")
    probe.add_argument(
        "--all",
        action="store_true",
        help="also listen to the vendor's own channels (switches their notifications on)",
    )
    probe.add_argument(
        "--record",
        action="store_true",
        help="also save every notification heard, with its time, next to the report (for replaying)",
    )

    sub.add_parser("game", help="show what the game has saved for Walka (its sessions and characters)")
    # (Up to 0.10 "beacon" checked the strip on the screen. There is none now.)
    beacon = sub.add_parser("beacon")
    beacon.add_argument("--seconds", type=float, default=30.0)

    install = sub.add_parser("install-addon", help="copy the Walka addon into the game's AddOns folder")
    install.add_argument("--dir", help="the Interface\\AddOns folder to install into")

    sub.add_parser("licenses", help="the licences of the libraries packed into this program")

    sub.add_parser("selfcheck")
    httpscheck = sub.add_parser("httpscheck")
    httpscheck.add_argument("--refuse-port", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # (Before anything is printed: --version, --help.)
    _use_the_console_it_was_started_from(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    # (Before the first line is written: only "run" stays up long enough to matter.)
    restore_console = _disable_console_quick_edit() if command == "run" else None
    stop_logging = _start_logging(args.verbose, file=_data_dir(args) / LOG_FILE if command == "run" else None)
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        if command == "run":
            code = _run_command(args)
            # Last, with everything stopped (the walk saved, the window closed,
            # the treadmill let go): an update installed from the dashboard starts here.
            from . import updates

            updates.restart_if_updated()
            return code
        if command == "scan":
            return asyncio.run(_scan(args))
        if command == "probe":
            return asyncio.run(_probe(args))
        if command == "game":
            return _game(args)
        if command == "beacon":
            print("Walka no longer reads anything off the screen: the game tells it what it needs in its saved file.")
            print("'walka game' shows what that file holds.")
            return 0
        if command == "install-addon":
            return _install_addon(args)
        if command == "licenses":
            return _licenses()
        if command == "selfcheck":
            return _selfcheck()
        if command == "httpscheck":
            return _httpscheck(args)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_logging()
        if restore_console is not None:
            restore_console()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
