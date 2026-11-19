"""Command line entry point: ``walka`` (or ``python -m walka``)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import logging.handlers
import math
import queue
import re
import signal
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__, addon_sync, capture
from . import config as config_module
from .journey import DEMO_KEY, DemoJourney
from .protocol import Flags, Frame, kmh_to_raw, yps_to_raw
from .road import demo_road

log = logging.getLogger("walka")

DISCLAIMER_URL = "https://github.com/AntonyPerez0/walka-addon/blob/HEAD/DISCLAIMER.md"

# Shown every time the companion starts: most people get Walka from the
# download button and never see the repository's DISCLAIMER.md.
AT_YOUR_OWN_RISK = (
    "\nWalka moves a motorised belt. You use it at your own risk, and it comes with no warranty.\n"
    "It is not a safety device: keep the treadmill's remote within reach, and stop if you feel unwell.\n"
    f"  disclaimer: {DISCLAIMER_URL}\n"
)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _data_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) if getattr(args, "data_dir", None) else config_module.default_data_dir()


def _build_treadmill(cfg: config_module.Config, simulate: bool):
    if simulate or cfg.treadmill.kind == "simulated":
        from .treadmill.simulated import SimulatedTreadmill

        return SimulatedTreadmill(us_firmware=cfg.units == "mph")
    from .treadmill.ftms_ble import FtmsTreadmill

    return FtmsTreadmill(
        cfg.treadmill.address or None,
        name_prefixes=cfg.treadmill.name_prefixes,
        units=cfg.treadmill.units,
    )


def _build_beacon(cfg: config_module.Config, *, read_screen: bool = True):
    from .sources import BeaconSource

    if not read_screen:
        return BeaconSource(None)  # frames are injected (demo)

    def make_reader() -> capture.BeaconReader:
        capture.require_screen_access()  # (macOS: without the permission there is no game to see)
        return capture.BeaconReader(
            capture.MssGrabber(),
            capture.default_window_finder(),
            manual_origin=cfg.wow.origin,
        )

    return BeaconSource(make_reader, fps=cfg.wow.capture_fps)


DEMO_SESSION_STARTS_AFTER_S = 2.0
DEMO_REDRAW_S = 0.1  # the addon redraws its strip ten times a second


async def _demo_character(beacon, stop: asyncio.Event) -> None:
    """--demo: feed the beacon source a scripted character so the whole
    pipeline can be watched without the game.

    Like a real player it first stands there with no session, then starts
    one: a session that is already running when the companion first sees it
    would (rightly) not start the belt until the character starts moving.

    And like the addon it shows, every few redraws, one number of the
    character's journey in place of its live state: the Share page gets a
    made-up character to make a picture of.
    """
    seq = 0
    started = time.monotonic()
    journey = DemoJourney()
    while not stop.is_set():
        t = time.monotonic() - started
        seq = (seq + 1) % 256
        stat = journey.frame(seq, t)
        if stat is not None:
            beacon.inject(stat)
            await asyncio.sleep(DEMO_REDRAW_S)
            continue
        if t < DEMO_SESSION_STARTS_AFTER_S:
            beacon.inject(Frame(seq=seq, session=0, flags=Flags(0)))
            await asyncio.sleep(DEMO_REDRAW_S)
            continue
        t -= DEMO_SESSION_STARTS_AFTER_S
        phase = t % 120
        if phase < 50:
            yps, flags = 7.0, Flags.MOVING  # running
        elif phase < 60:
            yps, flags = 0.0, Flags(0)  # looting
        elif phase < 95:
            yps, flags = 14.0, Flags.MOVING | Flags.MOUNTED
        elif phase < 105:
            yps, flags = 0.0, Flags.ESTIMATED
        else:
            yps, flags = 2.5, Flags.MOVING  # walking
        # The same default curve the addon uses.
        if yps <= 0:
            target = 1.6
        elif yps < 7:
            target = 2.4 + (3.2 - 2.4) * max(0.0, (yps - 2.5) / 4.5)
        else:
            target = 3.2 + (4.0 - 3.2) * min(1.0, (yps - 7.0) / 7.0)
        target += 0.05 * math.sin(t / 7.0)
        beacon.inject(
            Frame(
                seq=seq,
                session=1,
                flags=flags | Flags.ACTIVE,
                target_raw=kmh_to_raw(target),
                char_speed_raw=yps_to_raw(yps),
            )
        )
        await asyncio.sleep(DEMO_REDRAW_S)


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


def _start_logging(verbose: bool, stream=None) -> Callable[[], None]:
    """Send log records to the console (``stream``: stderr unless given) by
    way of a queue; returns a function that flushes and undoes it.

    The console is written to on a thread of its own. A console that is
    slow or stuck - a text selection, a paused scroll, a pipe nobody reads -
    can then hold up that thread only, never the event loop that is watching
    the belt (and whose STOP handling logs a line too).
    """
    console = logging.StreamHandler(stream)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    records: queue.SimpleQueue = queue.SimpleQueue()  # unbounded: handing a record over never blocks
    listener = logging.handlers.QueueListener(records, console)
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
        flusher = threading.Thread(target=listener.stop, name="walka-log-flush", daemon=True)
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


def _stop_and_wait(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event, wait_s: float = 4.0
) -> None:
    """Windows is about to kill the process (console closed, log-off,
    shutdown) and is waiting for our answer on one of its own threads: ask
    the event loop for an orderly stop, and hold the answer back until the
    belt has been stopped and the walk saved - or the few seconds Windows
    allows are nearly up."""
    try:
        loop.call_soon_threadsafe(stop.set)
    except RuntimeError:  # the loop is already closed: everything is stopped
        return
    stopped.wait(wait_s)


def _install_console_close_handler(
    loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event
) -> None:
    """On Windows, closing the console window kills the process within
    seconds. Catch that so the belt is stopped first."""
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
    either ends the process on the spot, with the belt running. Here they
    ask for the same orderly stop as Ctrl+C: belt first, then the walk is
    saved. (The handlers go when the event loop is closed.)"""
    if sys.platform == "win32":
        return
    for name in ("SIGTERM", "SIGHUP"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (AttributeError, NotImplementedError, RuntimeError, ValueError) as exc:
            # (Not the main thread - embedded, or under test: not ours to change.)
            log.debug("not listening for %s: %s", name, exc)


# How long the answer to "the computer is going to sleep" is held back for
# the belt's stop to go out. Windows allows a program about two seconds.
SLEEP_STOP_WAIT_S = 1.5


def _stop_belt_for_sleep(loop: asyncio.AbstractEventLoop, app, wait_s: float = SLEEP_STOP_WAIT_S) -> None:
    """The computer is about to sleep, and says so on one of its own
    threads: have the event loop stop the belt (``App.stop_for_sleep``) and
    hold the answer back until that is done, or the time allowed is up. The
    companion itself carries on: it is still there when the computer wakes."""
    if loop.is_closed():
        return
    try:
        done = asyncio.run_coroutine_threadsafe(app.stop_for_sleep(), loop)
    except RuntimeError:  # the loop was closed meanwhile
        return
    try:
        done.result(wait_s)
    except Exception as exc:  # too slow, or failed: the stop stays latched and is asked for again
        log.debug("the stop before sleeping was not seen to finish: %s", exc)


def _watch_for_session_end(loop: asyncio.AbstractEventLoop, stop: asyncio.Event, stopped: threading.Event, app=None):
    """Stop the belt when Windows logs off or shuts down, the same way as
    when the console window is closed - and when the computer is about to
    sleep (the program then stays). Returns the watcher (to be stopped)."""
    from .hotkey import SessionEndWatcher

    on_suspend = None if app is None else (lambda: _stop_belt_for_sleep(loop, app))
    watcher = SessionEndWatcher(lambda: _stop_and_wait(loop, stop, stopped), on_suspend)
    try:
        watcher.start()  # does nothing off Windows
    except Exception as exc:  # the companion runs without it
        log.debug("not watching for Windows shutting down: %s", exc)
    return watcher


def _watch_for_mac_sleep(loop: asyncio.AbstractEventLoop, app):
    """The same for a Mac that is about to sleep. Returns the watcher (to
    be stopped), or None off macOS."""
    if sys.platform != "darwin":
        return None
    from .macos import SleepWatcher

    watcher = SleepWatcher(lambda: _stop_belt_for_sleep(loop, app, wait_s=5.0))
    try:
        if not watcher.start():
            log.debug("not watching for the Mac going to sleep: %s", watcher.error)
    except Exception as exc:  # the companion runs without it
        log.debug("not watching for the Mac going to sleep: %s", exc)
    return watcher


async def _run(args: argparse.Namespace) -> int:
    from . import web
    from .app import App
    from .guard import GuardLink
    from .hr import HeartRateMonitor

    capture.enable_dpi_awareness()
    data_dir = _data_dir(args)
    cfg = config_module.load(data_dir)
    if args.port:
        cfg.web.port = args.port
    simulate = bool(args.simulate or args.demo)

    treadmill = _build_treadmill(cfg, simulate)
    if args.demo:
        beacon = _build_beacon(cfg, read_screen=False)
    elif args.no_capture:
        beacon = None
    else:
        beacon = _build_beacon(cfg)
    heart_rate = None if simulate else HeartRateMonitor(enabled=cfg.heart_rate.enabled, address=cfg.heart_rate.address)
    # The helper that stops the belt if this program is killed. A simulated
    # treadmill has no belt, and "do not stop the belt on exit" means that.
    guard = None
    if getattr(treadmill, "kind", "") != "simulated" and cfg.treadmill.stop_on_exit:
        guard = GuardLink(data_dir)
    app = App(cfg, data_dir, treadmill=treadmill, beacon=beacon, heart_rate=heart_rate, guard=guard)
    if args.demo:
        # The demo's made-up character must not turn up among the real ones later.
        app.journeys.use_memory_only(demo=True)
        app.roads.use_demo(demo_road(DEMO_KEY))

    stop = asyncio.Event()
    stopped = threading.Event()  # set once app.stop() has run: belt stopped, walk saved
    loop = asyncio.get_running_loop()
    _install_console_close_handler(loop, stop, stopped)
    _stop_on_signals(loop, stop)
    session_end = _watch_for_session_end(loop, stop, stopped, app)
    mac_sleep = _watch_for_mac_sleep(loop, app)

    runner = None
    demo_task = None
    try:
        if args.demo and beacon is not None:
            demo_task = asyncio.create_task(_demo_character(beacon, stop))
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
            print(f"Could not open a port near {cfg.web.port} for the dashboard.", file=sys.stderr)
            return 2
        shown_host = "127.0.0.1" if cfg.web.host in ("0.0.0.0", "::", "") else cfg.web.host
        url = f"http://{shown_host}:{port}/"
        print(f"Walka companion {__version__}")
        print(f"  dashboard: {url}")
        print(f"  data:      {data_dir}")
        if simulate:
            print("  treadmill: SIMULATED (no Bluetooth)")
        print("Press Ctrl+C to quit (the belt is stopped first).")
        print(AT_YOUR_OWN_RISK)
        if not app.terms_accepted():
            print("Before Walka starts or steers the belt, accept the disclaimer on the dashboard.\n")
        if cfg.web.open_browser and not args.no_browser:
            # On its own thread: launching a browser can initialise COM on
            # the calling thread in a mode the Bluetooth stack cannot use.
            threading.Thread(target=_open_browser, args=(url,), name="walka-browser", daemon=True).start()
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        # Shutdown has begun, and it ends with the belt being stopped. A
        # second Ctrl+C (a double tap, key repeat) would abort it half way
        # and leave the belt running, so it is ignored until that is done.
        listen_to_interrupts = _ignore_interrupts()
        if demo_task is not None:
            demo_task.cancel()
        try:
            await app.stop()
        finally:
            stopped.set()
            listen_to_interrupts()
            session_end.stop()
            if mac_sleep is not None:
                mac_sleep.stop()
            if runner is not None:
                await runner.cleanup()
    return 0


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
    from .treadmill.ftms_ble import describe_bluetooth_error

    cfg = config_module.load(_data_dir(args))
    try:
        lines = await probe.probe(
            address=args.address or cfg.treadmill.address or None,
            name_prefixes=cfg.treadmill.name_prefixes,
            listen_s=args.seconds,
            all_characteristics=args.all,
        )
    except Exception as exc:  # no adapter, Bluetooth switched off, the pad dropped the link, ...
        print(f"Bluetooth problem: {describe_bluetooth_error(exc)}")
        return 2
    path = probe.save_report(lines, Path(args.out) if args.out else Path.cwd())
    print(f"\nReport saved to {path}")
    print(
        "The saved report is made to be posted: it lists fitness machines only, cuts Bluetooth addresses short\n"
        "and leaves out the treadmill's serial number. (What you see above is the whole of it.)"
    )
    return 0


def _frame_text(decoded) -> str:
    """One decoded strip in words, for ``walka beacon``."""
    stat = decoded.stat
    if stat is not None:
        # Every few frames the addon shows one number of the character's
        # journey instead of its live state.
        name = stat.stat.name if stat.stat is not None else f"number {stat.stat_id}"
        return f"seq={stat.seq} journey {name}={stat.value}"
    frame = decoded.frame
    flags = ",".join(flag.name for flag in Flags if flag in frame.flags) or "-"
    return (
        f"seq={frame.seq} session={frame.session} "
        f"target={frame.target_kmh:.2f} km/h char={frame.char_speed_yps:.2f} yd/s [{flags}]"
    )


def _beacon(args: argparse.Namespace) -> int:
    """Diagnose screen capture: print what is decoded from the screen."""
    capture.enable_dpi_awareness()
    cfg = config_module.load(_data_dir(args))
    try:
        capture.require_screen_access()
    except capture.ScreenAccessError as exc:
        print(f"{exc}.")
        return 2
    reader = capture.BeaconReader(capture.MssGrabber(), capture.default_window_finder(), manual_origin=cfg.wow.origin)
    print("Looking for the Walka beacon (top-left corner of the game window). Ctrl+C to stop.")
    deadline = time.monotonic() + args.seconds
    seen = 0
    counters: set[int] = set()
    last_line = ""
    window = None
    try:
        while time.monotonic() < deadline:
            result = reader.read()
            decoded = result.decoded
            window = reader.window or window
            if decoded is None:
                found = reader.window
                line = "no beacon" + (
                    f" (game window found: {found.width}x{found.height} at {found.left},{found.top})"
                    if found
                    else " (game window not found)"
                )
            else:
                seen += 1
                counters.add(decoded.seq)
                line = (
                    f"OK via {result.where} at {result.origin} cell={decoded.cell_px:.2f}px "
                    f"contrast={decoded.white - decoded.black} | {_frame_text(decoded)}"
                )
            if line != last_line:
                print(line)
                last_line = line
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    print(f"{seen} frame(s) decoded.")
    if seen and len(counters) < 3:
        print(
            "The strip was read, but its counter did not move: the companion only follows a strip that ticks. "
            "Is the game frozen, or on a loading screen?"
        )
    if not seen and window is not None:
        _beacon_elsewhere(window)
    return 0 if seen else 1


def _beacon_elsewhere(window: capture.GameWindow) -> None:
    """The game window was found but no strip at its corner. The companion
    looks nowhere else, so say whether there is a strip somewhere else on
    screen: that would mean the window's position is being worked out wrong
    (worth reporting), rather than the addon not drawing."""
    searcher = capture.BeaconReader(capture.MssGrabber(), None)
    try:
        result = searcher.read()
    finally:
        searcher.close()
    if result.decoded is None:
        print(
            "No strip anywhere on screen either. Is the Walka addon enabled, and is the top-left corner of the "
            "game window uncovered? (In game, /walka status should print a line.)"
        )
    else:
        print(
            f"A strip was found on screen at {result.origin}, but the game window's corner is at "
            f"({window.left}, {window.top}). Walka only reads that corner, so it will not follow this strip. "
            "Please report these two positions, your display scaling and whether the game is windowed."
            + (" (On a Mac these positions are in points, not pixels.)" if sys.platform == "darwin" else "")
        )


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


# What the packaged program (Walka.exe, or Walka on a Mac) must be able to
# import at run time. Most of the Windows list is imported by name from
# inside compiled modules, or chosen at run time, where PyInstaller cannot
# see it - so a build can be missing one and still start. "walka selfcheck"
# imports them all, and the build's smoke test runs it: a missing module
# then fails the build, not the first Bluetooth scan on somebody's computer.
RUNTIME_MODULES: tuple[str, ...] = (
    "aiohttp.web",
    "bleak",
    "mss",
    "walka.web",
    "walka.probe",
    "walka.guard",
    "walka.treadmill.bleak_backend",
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
    "mss.windows",
    "mss.windows.gdi",
)
MACOS_RUNTIME_MODULES: tuple[str, ...] = (
    "bleak.backends.corebluetooth.scanner",
    "bleak.backends.corebluetooth.client",
    "mss.darwin",
    "walka.macos",
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


def _guard_problem(wait_s: float = 60.0) -> str:
    """Can this program start itself as the guard (walka.guard) - the way
    it does when it runs, pipe and all - and does that guard leave when it
    is told to? "" if so. (For the packaged program above all: the guard is
    a second copy of it, unpacked on its own.)"""
    import subprocess
    import tempfile

    from .guard import LOG_FILE, GuardLink

    with tempfile.TemporaryDirectory(prefix="walka-selfcheck-") as folder:
        link = GuardLink(Path(folder))
        if not link.start():
            return link.error
        process = link._process
        link.watch(None)
        link.release()
        try:
            code = process.wait(timeout=wait_s) if process is not None else 1
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
            return "the guard did not leave when it was told to"
        if code != 0:
            return f"the guard ended with exit code {code}"
        try:
            watched = "watching" in (Path(folder) / LOG_FILE).read_text("utf-8")
        except OSError:
            watched = False
        return "" if watched else "the guard started but never got as far as watching"


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
    if getattr(sys, "frozen", False):
        if not LICENCES_FILE.is_file():
            problems.append("the licences of the packed libraries are missing")
        guard_problem = _guard_problem()
        if guard_problem:
            problems.append(guard_problem)
    if problems:
        print("selfcheck FAILED: " + "; ".join(problems))
        return 1
    print(f"selfcheck OK: {len(modules)} modules, the dashboard page and the addon folder are all there")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="walka", description="Walka companion: links a walking pad to your game.")
    parser.add_argument("--version", action="version", version=f"walka {__version__}")
    parser.add_argument("--data-dir", help="folder for config and recorded walks")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    # (The metavar lists the commands meant for people; "selfcheck" is for the build, "guard" for Walka itself.)
    sub = parser.add_subparsers(dest="command", metavar="{run,scan,probe,beacon,install-addon,licenses}")

    run = sub.add_parser("run", help="run the companion and its dashboard (default)")
    for target in (parser, run):
        target.add_argument("--simulate", action="store_true", help="use a simulated treadmill (no Bluetooth)")
        target.add_argument("--demo", action="store_true", help="simulated treadmill AND a scripted game character")
        target.add_argument("--no-capture", action="store_true", help="do not read the screen (manual mode only)")
        target.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
        target.add_argument("--port", type=int, help="dashboard port (default 8787)")

    scan = sub.add_parser("scan", help="list nearby Bluetooth treadmills")
    scan.add_argument("--seconds", type=float, default=8.0)

    probe = sub.add_parser("probe", help="read-only dump of what the treadmill exposes (never moves the belt)")
    probe.add_argument("--address", help="Bluetooth address (default: first treadmill found)")
    probe.add_argument("--seconds", type=float, default=30.0, help="how long to listen for notifications")
    probe.add_argument("--out", help="folder for the report (default: current folder)")
    probe.add_argument(
        "--all",
        action="store_true",
        help="also listen to the vendor's own channels (switches their notifications on)",
    )

    beacon = sub.add_parser("beacon", help="check that the addon's beacon can be read off the screen")
    beacon.add_argument("--seconds", type=float, default=30.0)

    install = sub.add_parser("install-addon", help="copy the Walka addon into the game's AddOns folder")
    install.add_argument("--dir", help="the Interface\\AddOns folder to install into")

    sub.add_parser("licenses", help="the licences of the libraries packed into this program")

    sub.add_parser("selfcheck")
    sub.add_parser("guard")  # (started by the companion itself: see guard.py)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    # (Before the first line is written: only "run" stays up long enough to matter.)
    restore_console = _disable_console_quick_edit() if command == "run" else None
    stop_logging = _start_logging(args.verbose)
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        if command == "run":
            return asyncio.run(_run(args))
        if command == "scan":
            return asyncio.run(_scan(args))
        if command == "probe":
            return asyncio.run(_probe(args))
        if command == "beacon":
            return _beacon(args)
        if command == "install-addon":
            return _install_addon(args)
        if command == "licenses":
            return _licenses()
        if command == "selfcheck":
            return _selfcheck()
        if command == "guard":
            from .guard import run_guard

            return run_guard(_data_dir(args))
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
