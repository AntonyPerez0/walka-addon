""""Check my treadmill": the probe, from the dashboard.

The same read-only probe as ``walka probe --record`` (see ``probe``): it
scans, connects, lists what the treadmill exposes and listens for a while,
writing nothing but the switches that turn notifications on. Most treadmills
take one Bluetooth connection at a time, so Walka lets go of the treadmill
for the check and connects again afterwards (if it was connected or looking
before). The belt is not touched either way.

The report and the recording are saved in the ``reports`` folder next to the
walks, and can be downloaded from the dashboard. Both are the filtered,
made-to-be-posted versions: what the probe shows on a console in full is not
kept here.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .recording import Recording, recording_path

log = logging.getLogger(__name__)

REPORTS_DIR = "reports"
LISTEN_S = 30.0
SCAN_S = 8.0

ProbeFn = Callable[..., Awaitable[list[str]]]


@dataclass
class CheckState:
    running: bool = False
    started: float | None = None  # wall clock
    finished: float | None = None
    line: str = ""  # the newest line of the report, as progress
    report: str = ""  # file names in the reports folder
    recording: str = ""
    notifications: int = 0
    error: str = ""

    def to_dict(self, now: float) -> dict[str, Any]:
        return {
            "running": self.running,
            "seconds": round(now - self.started) if self.running and self.started else None,
            "expected_s": round(SCAN_S + LISTEN_S + 5),
            "line": self.line,
            "report": self.report,
            "recording": self.recording,
            "notifications": self.notifications,
            "error": self.error,
            "finished": self.finished,
        }


async def run_check(
    state: CheckState,
    directory: Path,
    *,
    address: str | None,
    name_prefixes: Sequence[str],
    probe_fn: ProbeFn | None = None,
    listen_s: float = LISTEN_S,
    wall: Callable[[], float] = time.time,
) -> None:
    """Run one check into ``state``. Never raises: a failure is ``state.error``."""
    from .treadmill.ftms_ble import describe_bluetooth_error

    if probe_fn is None:
        from .probe import probe as probe_fn

    recording = Recording(walka=__version__)
    report: list[str] = []

    def emit(_shown: str) -> None:
        # What the screen would show is not kept: the newest line of the
        # filtered report is the progress.
        if report:
            state.line = report[-1]

    state.running, state.error, state.report, state.recording = True, "", "", ""
    state.line, state.notifications, state.started, state.finished = "Scanning...", 0, wall(), None
    try:
        report = await probe_fn(
            address=address,
            name_prefixes=name_prefixes,
            listen_s=listen_s,
            scan_s=SCAN_S,
            emit=emit,
            recording=recording,
            report=report,
        )
        from .probe import save_report

        path = save_report(report, directory)
        state.report = path.name
        if recording.services:
            state.recording = recording.save(recording_path(path)).name
            state.notifications = len(recording.notifications)
        if any(line.startswith("No treadmill found") for line in report):
            state.error = "No treadmill was found. Is it switched on and in range, and is its own phone app closed?"
    except Exception as exc:  # no adapter, Bluetooth off, the pad dropped the link ...
        log.info("the treadmill check failed: %s", exc)
        state.error = f"The check did not finish: {describe_bluetooth_error(exc)}"
        if report:
            try:
                from .probe import save_report

                state.report = save_report([*report, f"(The check stopped: {exc})"], directory).name
            except OSError:
                pass
    finally:
        state.running = False
        state.finished = wall()
        state.line = ""


def report_file(directory: Path, name: str) -> Path | None:
    """A file of the reports folder, by the name the dashboard was given - and
    only such a file."""
    if not name or "/" in name or "\\" in name or not name.startswith("walka-probe-"):
        return None
    if not name.endswith((".txt", ".json")):
        return None
    path = directory / name
    return path if path.is_file() else None
