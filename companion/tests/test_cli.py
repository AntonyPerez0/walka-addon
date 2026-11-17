"""The command line: argument parsing, wiring, and a real start/Ctrl+C run."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import SRC
from walka import __version__, cli
from walka import config as config_module
from walka.sources import BeaconSource
from walka.treadmill import BeltState
from walka.treadmill.ftms_ble import FtmsTreadmill
from walka.treadmill.simulated import SimulatedTreadmill

# ---- parsing -------------------------------------------------------------------------


def test_defaults():
    args = cli.build_parser().parse_args([])
    assert args.command is None  # "run" is implied
    assert not args.simulate and not args.demo and not args.no_capture and not args.no_browser
    assert args.port is None and args.data_dir is None and not args.verbose


def test_run_flags_at_top_level_and_on_the_run_command():
    parser = cli.build_parser()
    for argv in (
        ["--demo", "--no-browser", "--port", "9001"],
        ["run", "--demo", "--no-browser", "--port", "9001"],
    ):
        args = parser.parse_args(argv)
        assert args.demo and args.no_browser and args.port == 9001 and not args.simulate
    args = parser.parse_args(["--simulate", "--no-capture", "--data-dir", "/tmp/x", "-v"])
    assert args.simulate and args.no_capture and args.data_dir == "/tmp/x" and args.verbose and not args.demo


def test_subcommands():
    parser = cli.build_parser()
    assert parser.parse_args(["scan"]).seconds == 8.0
    assert parser.parse_args(["scan", "--seconds", "3"]).seconds == 3.0
    probe = parser.parse_args(["probe", "--address", "AA:BB", "--seconds", "5", "--out", "reports"])
    assert (probe.command, probe.address, probe.seconds, probe.out) == ("probe", "AA:BB", 5.0, "reports")
    assert parser.parse_args(["probe"]).address is None and parser.parse_args(["probe"]).seconds == 30.0
    assert parser.parse_args(["beacon"]).seconds == 30.0
    install = parser.parse_args(["install-addon", "--dir", "C:\\WoW\\Interface\\AddOns"])
    assert install.command == "install-addon" and install.dir == "C:\\WoW\\Interface\\AddOns"
    assert parser.parse_args(["install-addon"]).dir is None


@pytest.mark.parametrize(
    "argv", [["--port", "eighty"], ["--port"], ["fly"], ["scan", "--seconds", "soon"], ["--no-such-flag"]]
)
def test_bad_arguments_exit_with_usage(argv, capsys):
    with pytest.raises(SystemExit) as info:
        cli.build_parser().parse_args(argv)
    assert info.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        cli.build_parser().parse_args(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"walka {__version__}"


# ---- wiring ----------------------------------------------------------------------------


def test_data_dir_argument_beats_the_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WALKA_DATA_DIR", str(tmp_path / "env"))
    parser = cli.build_parser()
    assert cli._data_dir(parser.parse_args([])) == tmp_path / "env"
    assert cli._data_dir(parser.parse_args(["--data-dir", str(tmp_path / "arg")])) == tmp_path / "arg"


def test_treadmill_selection():
    cfg = config_module.Config()
    simulated = cli._build_treadmill(cfg, simulate=True)
    assert isinstance(simulated, SimulatedTreadmill) and simulated.us_firmware  # units default to mph
    cfg.units = "kmh"
    assert not cli._build_treadmill(cfg, simulate=True).us_firmware
    cfg.treadmill.kind = "simulated"
    assert isinstance(cli._build_treadmill(cfg, simulate=False), SimulatedTreadmill)

    cfg = config_module.Config()
    cfg.treadmill.address = "AA:BB:CC:DD:EE:FF"
    cfg.treadmill.units = "mph"
    cfg.treadmill.name_prefixes = ["URTM", "KS-"]
    real = cli._build_treadmill(cfg, simulate=False)
    assert isinstance(real, FtmsTreadmill)  # constructing it must not touch Bluetooth
    assert real.address == "AA:BB:CC:DD:EE:FF" and real.units == "mph" and real.name_prefixes == ("URTM", "KS-")
    cfg.treadmill.address = ""
    assert cli._build_treadmill(cfg, simulate=False).address is None


def test_beacon_selection():
    cfg = config_module.Config()
    injected = cli._build_beacon(cfg, read_screen=False)
    assert isinstance(injected, BeaconSource) and injected._reader_factory is None
    cfg.wow.capture_fps = 20.0
    capturing = cli._build_beacon(cfg)
    assert capturing._reader_factory is not None and capturing.fps == 20.0  # (nothing is captured until started)


def test_beacon_diagnosis_names_both_kinds_of_frame():
    """``walka beacon`` prints what is on the strip. Every few frames that is
    a number of the character's journey, not the live state."""
    from walka.beacon import Decoded
    from walka.protocol import Flags, Frame, Stat, StatFrame

    live = Decoded(
        Frame(seq=7, session=2, flags=Flags.ACTIVE | Flags.MOVING, target_raw=322, char_speed_raw=350), 0, 4.0, 255, 0
    )
    assert cli._frame_text(live) == "seq=7 session=2 target=3.22 km/h char=7.00 yd/s [ACTIVE,MOVING]"
    assert cli._frame_text(Decoded(Frame(seq=8), 0, 4.0, 255, 0)).endswith("[-]")
    stat = Decoded(None, 0, 4.0, 255, 0, stat=StatFrame(seq=9, stat_id=Stat.FOOT_YD, value=123456))
    assert cli._frame_text(stat) == "seq=9 journey FOOT_YD=123456"
    unknown = Decoded(None, 0, 4.0, 255, 0, stat=StatFrame(seq=10, stat_id=40, value=5))
    assert cli._frame_text(unknown) == "seq=10 journey number 40=5"


def test_install_addon_command(tmp_path: Path, capsys, monkeypatch):
    addons = tmp_path / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    assert cli.main(["install-addon", "--dir", str(addons)]) == 0
    assert (addons / "Walka" / "Walka.toc").exists()
    assert "Installed to" in capsys.readouterr().out

    assert cli.main(["install-addon", "--dir", str(tmp_path / "missing")]) == 1
    assert "Install failed" in capsys.readouterr().out

    from walka import addon_sync

    monkeypatch.setattr(addon_sync, "find_addons_dirs", lambda: [])
    assert cli.main(["install-addon"]) == 1
    assert "--dir" in capsys.readouterr().out


# ---- running ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_demo_run_in_process_stops_the_belt_when_interrupted(tmp_path: Path, monkeypatch):
    """Ctrl+C cancels the main task; whatever the belt was doing, it must be
    stopped and the companion must leave nothing behind."""
    built: list[SimulatedTreadmill] = []
    real_build = cli._build_treadmill

    def build(cfg, simulate):
        treadmill = real_build(cfg, simulate)
        treadmill.countdown_s = 0.2  # (speeds the test up; the real countdown is 3 s)
        treadmill.ramp_kmh_per_s = 20.0
        built.append(treadmill)
        return treadmill

    monkeypatch.setattr(cli, "_build_treadmill", build)
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 0.5)
    port = free_port()
    args = cli.build_parser().parse_args(["--demo", "--no-browser", "--port", str(port), "--data-dir", str(tmp_path)])

    async def scenario():
        task = asyncio.create_task(cli._run(args))
        treadmill = None
        for _ in range(600):
            await asyncio.sleep(0.01)
            if built and built[0].status.speed_kmh > 1.0:
                treadmill = built[0]
                break
        assert treadmill is not None, "the demo never got the belt moving"
        assert treadmill.status.state is BeltState.RUNNING
        task.cancel()  # what asyncio.run does on SIGINT
        assert await asyncio.wait_for(task, 10) == 0
        assert treadmill.status.state is BeltState.STOPPED, "the belt was left running on exit"
        assert not treadmill.status.connected
        leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftovers == []

    asyncio.run(asyncio.wait_for(scenario(), 30))
    assert (tmp_path / "config.json").exists()
    with socket.socket() as again:  # the dashboard port was released
        again.bind(("127.0.0.1", port))


def test_run_falls_back_to_the_next_port_when_the_port_is_taken(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 60.0)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        args = cli.build_parser().parse_args(
            ["--demo", "--no-browser", "--port", str(port), "--data-dir", str(tmp_path)]
        )

        async def scenario():
            task = asyncio.create_task(cli._run(args))
            await asyncio.sleep(0.5)
            task.cancel()
            assert await asyncio.wait_for(task, 10) == 0

        asyncio.run(asyncio.wait_for(scenario(), 30))
    out = capsys.readouterr().out
    shown = int(out.split("dashboard: http://127.0.0.1:")[1].split("/")[0])
    assert port < shown <= port + 9


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; see the Windows checklist")
def test_demo_subprocess_serves_a_moving_belt_and_exits_cleanly_on_sigint(tmp_path: Path):
    """The real thing: ``python -m walka --demo --no-browser --port N``."""
    port = free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["WALKA_DATA_DIR"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "walka", "--demo", "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    state = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert process.poll() is None, f"exited early: {process.stderr.read()}"
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
                    state = json.load(response)
            except OSError:
                time.sleep(0.2)
                continue
            if state["treadmill"]["speed_kmh"] > 0.5:
                break
            time.sleep(0.2)
        assert state is not None, "the dashboard never answered"
        assert state["treadmill"]["kind"] == "simulated" and state["treadmill"]["connected"]
        assert state["treadmill"]["speed_kmh"] > 0.5, f"the belt never moved: {state['governor']}"
        assert state["treadmill"]["speed_kmh"] <= state["treadmill"]["cap_kmh"] + 1e-6
        assert state["governor"]["phase"] == "following" and state["source"]["kind"] == "wow"
        assert state["session"]["source"] == "wow"

        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode in (0, 130), err
    assert "Traceback" not in err, err
    assert f"dashboard: http://127.0.0.1:{port}/" in out and "SIMULATED" in out
    # App.stop() ran to its last step (stop the belt, save the walk, disconnect, save the config).
    assert (tmp_path / "config.json").exists()
    assert not (tmp_path / "active-session.json").exists()


def test_module_entry_point_reports_its_version():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "walka", "--version"], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0 and result.stdout.strip() == f"walka {__version__}"


# ---- the hidden "selfcheck" command and the build script ----------------------------------------


def load_build_script(repo_root: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("walka_build_exe", repo_root / "companion" / "build_exe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selfcheck_finds_everything_in_a_source_checkout(capsys):
    """On Windows this imports the Windows Runtime modules and the capture
    backend by the same names the packaged app is checked with."""
    assert cli.main(["selfcheck"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("selfcheck OK")


def test_selfcheck_says_what_is_missing(capsys, monkeypatch):
    from walka import addon_sync

    monkeypatch.setattr(cli, "RUNTIME_MODULES", (*cli.RUNTIME_MODULES, "walka.no_such_module"))
    monkeypatch.setattr(addon_sync, "bundled_addon_dir", lambda: None)
    assert cli.main(["selfcheck"]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("selfcheck FAILED")
    assert "walka.no_such_module" in out[0] and "addon folder is missing" in out[0]


def test_selfcheck_is_not_advertised():
    assert "selfcheck" not in cli.build_parser().format_help()
    assert cli.build_parser().parse_args(["selfcheck"]).command == "selfcheck"


def test_the_build_bundles_what_selfcheck_looks_for(repo_root: Path):
    """winrt.windows.foundation.collections is imported by name from inside
    winrt's compiled modules: without a hidden import it is left out of
    Walka.exe, and every Bluetooth scan in the packaged app fails."""
    from walka import addon_sync

    build = load_build_script(repo_root)
    windows = build.pyinstaller_args("win32")
    hidden = {windows[i + 1] for i, arg in enumerate(windows) if arg == "--hidden-import"}
    assert "winrt.windows.foundation.collections" in hidden
    assert {name for name in cli.WINDOWS_RUNTIME_MODULES if name.startswith("winrt.")} <= hidden
    # ... and every Windows Runtime package the project depends on is among them.
    pyproject = (repo_root / "companion" / "pyproject.toml").read_text("utf-8")
    import re

    packages = re.findall(r'"winrt-(Windows\.[A-Za-z.]+)', pyproject)
    assert len(packages) >= 8
    assert {"winrt." + package.lower() for package in packages} <= hidden
    assert "--hidden-import" not in build.pyinstaller_args("linux")

    # The addon is bundled where addon_sync looks for it, and not in a folder
    # that Windows would merge with the package's own ("Walka" vs "walka").
    destinations = [windows[i + 1].split(os.pathsep)[-1] for i, arg in enumerate(windows) if arg == "--add-data"]
    assert "/".join(addon_sync.BUNDLED_ADDON_PATH) in destinations
    top_level = [destination.split("/")[0].lower() for destination in destinations]
    assert len(set(top_level)) == len(top_level) == 2 and "walka" in top_level


# ---- the console -------------------------------------------------------------------------------------


def test_quick_edit_is_the_only_console_mode_bit_changed():
    assert cli._without_quick_edit(0x01F7) == 0x01B7  # QuickEdit off, the rest untouched
    assert cli._without_quick_edit(0x0007) == 0x0087  # "extended flags" is what makes Windows honour it
    assert cli._without_quick_edit(cli._without_quick_edit(0x01F7)) == 0x01B7
    restore = cli._disable_console_quick_edit()  # no console here (or not Windows): must do nothing, quietly
    restore()


def test_logging_does_not_wait_for_the_console():
    """A console whose writes block (a text selection in the window) must
    not block whoever logs - the event loop, in the middle of a STOP."""
    import logging
    import threading

    writing, release = threading.Event(), threading.Event()
    written: list[str] = []

    class StuckConsole:
        def write(self, text: str) -> int:
            writing.set()
            release.wait(20)
            written.append(text)
            return len(text)

        def flush(self) -> None:
            pass

    stop_logging = cli._start_logging(verbose=False, stream=StuckConsole())
    try:
        logging.getLogger("walka.test").warning("STOP pressed - belt stopped.")
        logging.getLogger("walka.test").info("a second line")
        # Both calls came back although nothing has been written yet ...
        assert writing.wait(5) and written == []
    finally:
        release.set()
        stop_logging()
    # ... and nothing is lost: stopping flushes the queue.
    text = "".join(written)
    assert "WARNING STOP pressed - belt stopped." in text and "a second line" in text
    assert not any(isinstance(h, logging.handlers.QueueHandler) for h in logging.getLogger().handlers)


def test_debug_lines_only_with_verbose():
    import io
    import logging

    for verbose, expected in ((False, False), (True, True)):
        stream = io.StringIO()
        stop_logging = cli._start_logging(verbose=verbose, stream=stream)
        logging.getLogger("walka.test").debug("fine detail")
        stop_logging()
        assert ("fine detail" in stream.getvalue()) is expected


# ---- Ctrl+C twice ---------------------------------------------------------------------------------------


def test_a_second_ctrl_c_during_shutdown_does_not_leave_the_belt_running(tmp_path: Path, monkeypatch):
    """The first Ctrl+C starts the shutdown, which ends with the belt being
    stopped. A second one while that stop is on its way used to abort the
    shutdown: the companion exited and the belt kept turning."""
    built: list[SimulatedTreadmill] = []
    interrupts: list[str] = []

    class SlowToStop(SimulatedTreadmill):
        async def set_speed(self, kmh: float) -> None:
            await super().set_speed(kmh)
            if not interrupts and self.status.speed_kmh > 0.5:
                interrupts.append("first")
                signal.raise_signal(signal.SIGINT)  # Ctrl+C, with the belt running

        async def stop(self) -> None:
            if interrupts == ["first"]:
                interrupts.append("second")
                signal.raise_signal(signal.SIGINT)  # ... and again, while the stop is being sent
            await asyncio.sleep(0.1)
            await super().stop()

    def build(cfg, simulate):
        treadmill = SlowToStop(us_firmware=True)
        treadmill.countdown_s = 0.2
        treadmill.ramp_kmh_per_s = 20.0
        built.append(treadmill)
        return treadmill

    monkeypatch.setattr(cli, "_build_treadmill", build)
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 0.3)
    before = signal.getsignal(signal.SIGINT)
    try:
        code = cli.main(["--demo", "--no-browser", "--port", str(free_port()), "--data-dir", str(tmp_path)])
    except KeyboardInterrupt:
        pytest.fail("the second Ctrl+C aborted the shutdown")
    assert interrupts == ["first", "second"]
    assert built[0].status.state is BeltState.STOPPED, "the belt was left running"
    assert code in (0, 130)  # (130 on Python 3.10, whose asyncio.run re-raises the first interrupt)
    assert (tmp_path / "config.json").exists()  # App.stop() ran to its end
    assert signal.getsignal(signal.SIGINT) is before  # Ctrl+C works again afterwards


def test_ctrl_c_is_listened_to_again_after_the_shutdown():
    before = signal.getsignal(signal.SIGINT)
    listen_again = cli._ignore_interrupts()
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    listen_again()
    assert signal.getsignal(signal.SIGINT) is before

    import threading

    # Off the main thread signal handlers cannot be changed: nothing happens, nothing breaks.
    outcome: list[object] = []
    worker = threading.Thread(target=lambda: outcome.append(cli._ignore_interrupts()()))
    worker.start()
    worker.join(5)
    assert outcome == [None] and signal.getsignal(signal.SIGINT) is before


# ---- being closed from outside (console window, log-off, shutdown) -----------------------------------------


def test_windows_is_held_off_until_the_belt_has_been_stopped():
    import threading

    async def scenario():
        loop = asyncio.get_running_loop()
        stop, stopped = asyncio.Event(), threading.Event()
        returned = threading.Event()

        def windows_thread():  # the console handler / the hidden window's thread
            cli._stop_and_wait(loop, stop, stopped, wait_s=20.0)
            returned.set()

        threading.Thread(target=windows_thread, daemon=True).start()
        await asyncio.wait_for(stop.wait(), 5)  # the event loop is asked to shut down ...
        await asyncio.sleep(0.05)
        assert not returned.is_set()  # ... and Windows gets no answer while it is still doing so
        stopped.set()
        assert await loop.run_in_executor(None, returned.wait, 5)
        return loop, stop, stopped

    loop, stop, stopped = asyncio.run(scenario())
    # After the loop has gone there is nothing left to stop, and nothing to wait for.
    started = time.monotonic()
    cli._stop_and_wait(loop, asyncio.Event(), threading.Event(), wait_s=20.0)
    assert time.monotonic() - started < 5


def test_the_wait_for_the_belt_is_bounded():
    """Windows kills a program that does not answer within about five
    seconds; the answer must come before that even if stopping hangs."""
    import threading

    async def scenario():
        started = time.monotonic()
        await asyncio.get_running_loop().run_in_executor(
            None, cli._stop_and_wait, asyncio.get_running_loop(), asyncio.Event(), threading.Event(), 0.1
        )
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 3


# ---- readable Bluetooth failures on the command line ----------------------------------------------------------


def test_scan_says_in_plain_words_that_bluetooth_is_off(tmp_path: Path, capsys, monkeypatch):
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

    from walka_fakes import FakeBackend, FakePad

    backend = FakeBackend(FakePad())
    monkeypatch.setattr(FtmsTreadmill, "_get_backend", lambda self: backend)
    backend.scan_error = BleakBluetoothNotAvailableError(
        "No Bluetooth adapter found", BleakBluetoothNotAvailableReason.NO_BLUETOOTH
    )
    assert cli.main(["--data-dir", str(tmp_path), "scan", "--seconds", "1"]) == 2
    assert capsys.readouterr().out.strip().splitlines()[-1] == "Bluetooth is not available: No Bluetooth adapter found"

    backend.scan_error = None
    assert cli.main(["--data-dir", str(tmp_path), "scan", "--seconds", "1"]) == 0
    assert "URTM030  [FTMS]" in capsys.readouterr().out


def test_probe_reports_a_time_out_readably_and_passes_all_on(tmp_path: Path, capsys, monkeypatch):
    from walka import probe

    seen: dict = {}

    async def fake_probe(**kwargs):
        seen.update(kwargs)
        if kwargs["all_characteristics"]:
            return ["report"]
        raise asyncio.TimeoutError()  # how bleak reports a connection that timed out: no text

    monkeypatch.setattr(probe, "probe", fake_probe)
    assert cli.main(["--data-dir", str(tmp_path), "probe", "--seconds", "1", "--out", str(tmp_path)]) == 2
    assert seen["all_characteristics"] is False
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == (
        "Bluetooth problem: timed out - is the pad switched on and in range, and is the UREVO phone app closed?"
    )
    assert cli.main(["--data-dir", str(tmp_path), "probe", "--all", "--out", str(tmp_path)]) == 0
    assert seen["all_characteristics"] is True
    assert list(tmp_path.glob("walka-probe-*.txt"))
