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
