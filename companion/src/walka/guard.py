"""The guard: a second, small process that stops the belt if the companion
vanishes.

The companion stops the belt before it exits - when its window is closed,
at Ctrl+C, at log-off (``cli.py``). What it cannot do is stop the belt after
it has been killed: a crash inside a Bluetooth library, *End task* in Task
Manager, the system running out of memory. The treadmill does not notice
any of that. Its belt keeps the speed it was last given, with someone on it
whose eyes are on a game.

So the companion starts one helper process and keeps a pipe to it:

* the companion tells it, whenever that changes, whether a belt of its own
  may be turning, and which treadmill (``belt <address>`` / ``idle``);
* when it exits in an orderly way it says ``bye``;
* when it dies, the system closes the pipe. If the last word was ``belt``,
  the guard connects to that treadmill itself and stops it.

The guard only ever sends a stop, and only to the treadmill the companion
named. It gives up after ``TAKEOVER_S``, or as soon as the companion is
started again (someone is there, and the new companion needs the pad's one
Bluetooth connection). What it did is left in a small file that the next
companion reads and shows on the dashboard.

It is a net, not a promise: a guard that is killed together with the
companion (*End process tree*, the computer losing power) stops nothing,
and Bluetooth may not reach the pad. The treadmill's own remote, stop
button and safety key are what stops a belt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import IO, Any

from .treadmill.base import BeltState, Treadmill, TreadmillError

log = logging.getLogger(__name__)

# How long the guard goes on trying to reach and stop the treadmill.
TAKEOVER_S = 180.0
# Between two attempts to connect.
RETRY_S = 3.0
# After connecting: how long to wait for the treadmill's first report of
# its speed before a stop is sent anyway.
FIRST_REPORT_S = 4.0
# After a stop: how long the belt is watched coming down before the stop
# is sent again.
RESTOP_S = 5.0
_MOVING_KMH = 0.05

REPORT_FILE = "guard-report.json"
STAND_DOWN_FILE = "guard-stand-down"
LOG_FILE = "guard.log"

# What the guard did (``result`` in the report file).
STOPPED = "stopped"  # the belt was turning and has been stopped
ALREADY_STOPPED = "already_stopped"  # the belt was found standing still
UNREACHABLE = "unreachable"  # the treadmill could not be reached, or would not stop, in time
STOOD_DOWN = "stood_down"  # the companion was started again: it is theirs now

REPORT_TEXT = {
    STOPPED: (
        "error",
        "Walka did not close properly last time, while the belt was running. Its guard stopped the belt.",
    ),
    ALREADY_STOPPED: (
        "warning",
        "Walka did not close properly last time, during a session. Its guard found the belt already stopped.",
    ),
    UNREACHABLE: (
        "error",
        "Walka did not close properly last time, while the belt was running - and its guard could not reach the "
        "treadmill to stop it. If Walka ever disappears, stop the belt at the treadmill itself.",
    ),
    STOOD_DOWN: (
        "warning",
        "Walka did not close properly last time, during a session. If the belt is still moving, press STOP.",
    ),
}


# ---- what the companion says --------------------------------------------------------


def order_line(address: str | None) -> str:
    """The line that tells the guard which belt is in danger (or none)."""
    return f"belt {address}\n" if address else "idle\n"


def read_orders(lines: Iterable[str]) -> str | None:
    """Follow what the companion says until the pipe closes.

    Returns the address of the treadmill to stop, or ``None`` if there is
    nothing to do: the companion said ``bye``, or no belt of its was turning
    when it went quiet.
    """
    address: str | None = None
    for line in lines:
        word, _, rest = line.strip().partition(" ")
        if word == "bye":
            return None
        if word == "idle":
            address = None
        elif word == "belt" and rest.strip():
            address = rest.strip()
        # (Anything else - a torn line - changes nothing.)
    return address


# ---- the companion's end ------------------------------------------------------------


def guard_command(data_dir: Path) -> list[str]:
    """How this program starts itself as the guard."""
    program = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "walka"]
    return program + ["--data-dir", str(data_dir), "guard"]


class GuardLink:
    """The companion's end of the pipe.

    Nothing here may hold the companion up or bring it down: every failure
    ends with ``error`` set and the companion carrying on without a guard.
    """

    def __init__(self, data_dir: Path, command: Sequence[str] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.command = list(command) if command is not None else guard_command(self.data_dir)
        self.error = ""
        self._process: subprocess.Popen | None = None
        self._told: str | None = None

    @property
    def active(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self) -> bool:
        """Start the guard. An older guard that is still trying to stop a
        belt is told to let go first: the pad takes one connection."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / STAND_DOWN_FILE).write_text(str(time.time()), "utf-8")
        except OSError as exc:
            log.debug("could not tell an older guard to stand down: %s", exc)
        env = dict(os.environ)
        # The packaged program unpacks itself into a folder that is removed
        # when it exits. The guard has to outlive it, so it must unpack a
        # copy of its own (PyInstaller 6.9 and later).
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            # No console of its own, and none shared with the companion:
            # closing the companion's window must not close the guard.
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # A session of its own: Ctrl+C in the terminal, or the terminal
            # window being closed, is meant for the companion.
            options["start_new_session"] = True
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                **options,
            )
        except (OSError, ValueError) as exc:
            self.error = f"the guard could not be started: {exc}"
            self._process = None
            return False
        self._told = None
        self.error = ""
        return True

    def _say(self, line: str) -> bool:
        process = self._process
        if process is None or process.stdin is None:
            return False
        try:
            process.stdin.write(line.encode("utf-8"))
            process.stdin.flush()
        except (OSError, ValueError) as exc:  # the guard is gone
            self.error = f"the guard is not running any more: {exc}"
            self._close_pipe()
            self._process = None
            return False
        return True

    def watch(self, address: str | None) -> None:
        """Tell the guard which treadmill has a belt of ours that may be
        turning (``None``: none). Only a change is passed on."""
        line = order_line(address)
        if line != self._told and self._say(line):
            self._told = line

    def _close_pipe(self) -> None:
        process = self._process
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass

    def release(self, *, handover: bool = False) -> None:
        """The companion is exiting. ``handover``: a belt of ours could not
        be stopped, so the guard is to go on trying after we are gone."""
        process = self._process
        if process is None:
            return
        if not handover:
            self._say("bye\n")
        self._close_pipe()
        self._process = None
        if not handover:
            try:
                process.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                pass


def take_report(data_dir: Path) -> dict[str, Any] | None:
    """What the guard of an earlier run left behind, once: the file is
    removed. ``None`` if there is none (the usual case)."""
    path = Path(data_dir) / REPORT_FILE
    try:
        report = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        report = None
    try:
        path.unlink()
    except OSError:
        pass
    return report if isinstance(report, dict) and report.get("result") in REPORT_TEXT else None


# ---- the guard's end ----------------------------------------------------------------


async def _wait_for(condition: Callable[[], bool], seconds: float, sleep: Callable[[float], Awaitable[None]]) -> bool:
    waited = 0.0
    while not condition():
        if waited >= seconds:
            return False
        await sleep(0.25)
        waited += 0.25
    return True


def _reported_since(treadmill: Treadmill, mark: float, *, standing: bool = False) -> Callable[[], bool]:
    """Has the treadmill reported since ``mark`` (``standing``: that its
    belt stands still)? Also true once the link is gone: nothing more will
    come."""
    status = treadmill.status

    def check() -> bool:
        if not status.connected:
            return True
        return status.updated_at != mark and (not standing or status.speed_kmh <= _MOVING_KMH)

    return check


async def stop_belt(
    treadmill: Treadmill,
    *,
    budget_s: float = TAKEOVER_S,
    stand_down: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Reach the treadmill and stop its belt; returns what happened (one of
    the results above). Only a stop is ever sent."""
    status = treadmill.status
    deadline = clock() + budget_s
    sent = False
    at_connect = -1.0  # status.updated_at when this connection began: older reports say nothing
    try:
        while clock() < deadline:
            if stand_down():
                return STOOD_DOWN
            if not status.connected:
                try:
                    await treadmill.connect()
                except TreadmillError as exc:
                    log.info("guard: treadmill not reached (%s)", exc)
                    await sleep(RETRY_S)
                    continue
                at_connect = status.updated_at
                # What is the belt doing? The first data packet says.
                await _wait_for(_reported_since(treadmill, at_connect), FIRST_REPORT_S, sleep)
            reported = status.updated_at != at_connect
            if reported and status.speed_kmh <= _MOVING_KMH:
                if sent:
                    return STOPPED
                if status.state not in (BeltState.STARTING, BeltState.RUNNING):
                    return ALREADY_STOPPED
            try:
                await treadmill.stop()
            except TreadmillError as exc:
                log.info("guard: stop failed (%s)", exc)
                await sleep(RETRY_S)
                continue
            sent = True
            log.info("guard: stop sent")
            # Watch it come down. If it does not, the loop asks again.
            await _wait_for(_reported_since(treadmill, status.updated_at, standing=True), RESTOP_S, sleep)
            if status.connected and status.updated_at == at_connect:
                # It took the stop, and never says what its belt does.
                return STOPPED
        return UNREACHABLE
    finally:
        try:
            await treadmill.disconnect()
        except Exception as exc:  # the report matters more
            log.debug("guard: disconnect failed: %s", exc)


def _write_report(data_dir: Path, result: str, address: str) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / REPORT_FILE).write_text(
            json.dumps({"result": result, "address": address, "time": time.time()}), "utf-8"
        )
    except OSError as exc:
        log.info("guard: could not write the report: %s", exc)


def _stand_down_since(data_dir: Path, since: float) -> Callable[[], bool]:
    path = data_dir / STAND_DOWN_FILE

    def asked() -> bool:
        try:
            return path.stat().st_mtime > since
        except OSError:
            return False

    return asked


def _log_to_file(data_dir: Path) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(data_dir / LOG_FILE, mode="w", encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run_guard(
    data_dir: Path,
    orders: IO[str] | Iterable[str] | None = None,
    make_treadmill: Callable[[str], Treadmill] | None = None,
) -> int:
    """The guard process: ``walka guard``. Returns its exit code."""
    _log_to_file(data_dir)
    if make_treadmill is None:
        # Everything a stop needs is loaded now, while the program's files
        # are certain to be there - not once the companion is gone.
        from .treadmill import bleak_backend
        from .treadmill.ftms_ble import FtmsTreadmill

        bleak_backend.prepare_windows_threading()

        def make_treadmill(address: str) -> Treadmill:
            return FtmsTreadmill(address, name_prefixes=(), scan_timeout_s=10.0)

    log.info("guard: watching")
    address = read_orders(orders if orders is not None else sys.stdin)
    if address is None:
        log.info("guard: nothing to do")
        return 0
    began = time.time()
    log.info("guard: the companion is gone with the belt of %s running", address)
    try:
        result = asyncio.run(
            stop_belt(make_treadmill(address), stand_down=_stand_down_since(data_dir, began))
        )
    except Exception as exc:  # whatever Bluetooth objects to: say so, for the next start
        log.info("guard: failed (%s: %s)", type(exc).__name__, exc)
        result = UNREACHABLE
    log.info("guard: %s", result)
    _write_report(data_dir, result, address)
    return 0 if result != UNREACHABLE else 1
