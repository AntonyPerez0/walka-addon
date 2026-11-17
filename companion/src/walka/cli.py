"""Command line entry point: ``walka`` (or ``python -m walka``)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from . import __version__, addon_sync, capture
from . import config as config_module
from .protocol import Flags, Frame, kmh_to_raw, yps_to_raw

log = logging.getLogger("walka")


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
        return capture.BeaconReader(
            capture.MssGrabber(),
            capture.default_window_finder(),
            manual_origin=cfg.wow.origin,
        )

    return BeaconSource(make_reader, fps=cfg.wow.capture_fps)


DEMO_SESSION_STARTS_AFTER_S = 2.0


async def _demo_character(beacon, stop: asyncio.Event) -> None:
    """--demo: feed the beacon source a scripted character so the whole
    pipeline can be watched without the game.

    Like a real player it first stands there with no session, then starts
    one: a session that is already running when the companion first sees it
    would (rightly) not start the belt until the character starts moving.
    """
    seq = 0
    started = time.monotonic()
    while not stop.is_set():
        t = time.monotonic() - started
        if t < DEMO_SESSION_STARTS_AFTER_S:
            seq = (seq + 1) % 256
            beacon.inject(Frame(seq=seq, session=0, flags=Flags(0)))
            await asyncio.sleep(0.1)
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
            yps, flags = 0.0, Flags.COMBAT | Flags.ESTIMATED
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
        seq = (seq + 1) % 256
        beacon.inject(
            Frame(
                seq=seq,
                session=1,
                flags=flags | Flags.ACTIVE,
                target_raw=kmh_to_raw(target),
                char_speed_raw=yps_to_raw(yps),
            )
        )
        await asyncio.sleep(0.1)


def _install_console_close_handler(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """On Windows, closing the console window kills the process within
    seconds. Catch that so the belt is stopped first."""
    if sys.platform != "win32":
        return
    import ctypes

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def handler(event: int) -> int:
        # CTRL_CLOSE_EVENT=2, CTRL_LOGOFF_EVENT=5, CTRL_SHUTDOWN_EVENT=6
        if event in (2, 5, 6):
            loop.call_soon_threadsafe(stop.set)
            time.sleep(4.0)  # Windows waits for this handler before killing us
            return 1
        return 0

    _install_console_close_handler.keep = handler  # type: ignore[attr-defined]  # keep a reference
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, 1)


async def _run(args: argparse.Namespace) -> int:
    from . import web
    from .app import App
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
    app = App(cfg, data_dir, treadmill=treadmill, beacon=beacon, heart_rate=heart_rate)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_console_close_handler(loop, stop)

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
        if cfg.web.open_browser and not args.no_browser:
            # On its own thread: launching a browser can initialise COM on
            # the calling thread in a mode the Bluetooth stack cannot use.
            threading.Thread(target=_open_browser, args=(url,), name="walka-browser", daemon=True).start()
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        if demo_task is not None:
            demo_task.cancel()
        try:
            await app.stop()
        finally:
            if runner is not None:
                await runner.cleanup()
    return 0


async def _scan(args: argparse.Namespace) -> int:
    from .treadmill.ftms_ble import FtmsTreadmill

    cfg = config_module.load(_data_dir(args))
    treadmill = FtmsTreadmill(name_prefixes=cfg.treadmill.name_prefixes)
    print(f"Scanning for treadmills for {args.seconds:.0f} s ...")
    found = await treadmill.discover(args.seconds)
    if not found:
        print("None found. Is the pad on, in range, and the UREVO phone app closed?")
        return 1
    for device in found:
        tag = "FTMS" if device.ftms else "name match"
        print(f"  {device.address}  rssi={device.rssi}  {device.name or '(no name)'}  [{tag}]")
    return 0


async def _probe(args: argparse.Namespace) -> int:
    from . import probe

    cfg = config_module.load(_data_dir(args))
    lines = await probe.probe(
        address=args.address or cfg.treadmill.address or None,
        name_prefixes=cfg.treadmill.name_prefixes,
        listen_s=args.seconds,
    )
    path = probe.save_report(lines, Path(args.out) if args.out else Path.cwd())
    print(f"\nReport saved to {path}")
    return 0


def _beacon(args: argparse.Namespace) -> int:
    """Diagnose screen capture: print what is decoded from the screen."""
    capture.enable_dpi_awareness()
    cfg = config_module.load(_data_dir(args))
    reader = capture.BeaconReader(capture.MssGrabber(), capture.default_window_finder(), manual_origin=cfg.wow.origin)
    print("Looking for the Walka beacon (top-left corner of the game window). Ctrl+C to stop.")
    deadline = time.monotonic() + args.seconds
    seen = 0
    last_line = ""
    try:
        while time.monotonic() < deadline:
            result = reader.read()
            decoded = result.decoded
            if decoded is None:
                window = reader.window
                line = "no beacon" + (
                    f" (game window found: {window.width}x{window.height})" if window else " (game window not found)"
                )
            else:
                seen += 1
                frame = decoded.frame
                flags = ",".join(flag.name for flag in Flags if flag in frame.flags) or "-"
                line = (
                    f"OK via {result.where} at {result.origin} cell={decoded.cell_px:.2f}px "
                    f"contrast={decoded.white - decoded.black} | seq={frame.seq} session={frame.session} "
                    f"target={frame.target_kmh:.2f} km/h char={frame.char_speed_yps:.2f} yd/s [{flags}]"
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
    return 0 if seen else 1


def _install_addon(args: argparse.Namespace) -> int:
    if args.dir:
        targets = [Path(args.dir)]
    else:
        targets = addon_sync.find_addons_dirs()
    if not targets:
        print("No World of Warcraft AddOns folder found. Pass it with --dir, e.g.")
        print(r'  walka install-addon --dir "C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns"')
        return 1
    try:
        installed = addon_sync.install_addon(targets[0])
    except (OSError, FileNotFoundError) as exc:
        print(f"Install failed: {exc}")
        return 1
    print(f"Installed to {installed}")
    print("Restart the game (or /reload) and enable Walka in the AddOns list.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="walka", description="Walka companion: links a walking pad to your game.")
    parser.add_argument("--version", action="version", version=f"walka {__version__}")
    parser.add_argument("--data-dir", help="folder for config and recorded walks")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command")

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

    beacon = sub.add_parser("beacon", help="check that the addon's beacon can be read off the screen")
    beacon.add_argument("--seconds", type=float, default=30.0)

    install = sub.add_parser("install-addon", help="copy the Walka addon into the game's AddOns folder")
    install.add_argument("--dir", help="the Interface\\AddOns folder to install into")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
    command = args.command or "run"
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
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
