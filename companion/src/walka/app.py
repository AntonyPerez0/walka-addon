"""The companion's engine: wires the game's signal, the treadmill reader,
the recorder and the addon sync together.

Everything here runs on one asyncio loop (the beacon reader has its own
thread and is only polled). The web dashboard and the CLI talk to the
``App`` object; nothing else holds state.

The companion listens to the treadmill and never tells it anything: there
is no command in this file, in the reader interface (``treadmill.base``) or
anywhere else in the program that starts, stops or steers a belt. What the
game says decides one thing only - which game session a walk is filed
under.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__, addon_sync
from . import config as config_module
from .config import Config
from .journey import attach as attach_journeys
from .metrics import Profile
from .protocol import LEGACY_FLAGS, Flags
from .road import RoadBook
from .session import SessionRecord, SessionRecorder, SessionStore
from .sources import BeaconSource, Reading
from .treadmill import Treadmill, TreadmillError, TreadmillEvent
from .treadmill_check import REPORTS_DIR, CheckState, run_check
from .updates import UpdateState
from .updates import check as check_for_update

log = logging.getLogger(__name__)

TICK_S = 0.2
CHECKPOINT_EVERY_S = 30.0
FREE_WALK_GRACE_S = 60.0  # a walk outside a game session ends after this long stopped
# A game session that is out of view, with the belt standing still, is
# closed - and let go of - after this long.
ABANDONED_AFTER_S = 900.0
SIGNAL_TIMEOUT_S = 3.0  # a look at the game older than this counts as lost
STALE_DATA_S = 5.0  # treadmill data older than this is not trusted
MOVING_KMH = 0.05  # a belt reporting more than this is moving
STATE_FILE = "state.json"
# A lost treadmill is listened for (advertisements only, never a connection
# attempt). Heard again after at least this long without a word, it was
# switched off and on: Walka connects. One that keeps advertising - in
# standby - is left alone.
RETURN_GAP_S = 20.0
WATCH_RETRY_S = 30.0  # after the Bluetooth listening failed (adapter off, ...)


class ReturnDetector:
    """Tells a treadmill switched off and on from one in standby, by the gap
    in its advertisements."""

    def __init__(self, now: float, gap_s: float = RETURN_GAP_S) -> None:
        self.last = now  # listening began: silence is counted from here
        self.gap_s = gap_s

    def seen(self, now: float) -> bool:
        """An advertisement at ``now``. True if it ends a long enough silence."""
        returned = now - self.last >= self.gap_s
        self.last = now
        return returned



class App:
    def __init__(
        self,
        config: Config,
        data_dir: Path,
        *,
        treadmill: Treadmill,
        beacon: BeaconSource | None = None,
        heart_rate: Any = None,
        walka_account: Any = None,
        wall_clock: Callable[[], float] = time.time,
        mono_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wall = wall_clock
        self.mono = mono_clock

        self.store = SessionStore(self.data_dir)
        self.beacon = beacon
        self.treadmill = treadmill
        self.treadmill.on_event = self._on_treadmill_event
        self.heart_rate = heart_rate
        # The numbers behind the Share page's picture. They arrive in stat
        # frames, which are kept apart from the live reading (stat_route.py,
        # journey.py).
        self.journeys = attach_journeys(self.data_dir, beacon, wall=wall_clock, mono=mono_clock)
        # The roads characters have travelled, read from the game's saved
        # files when the Share page asks (on a worker thread: see web.py).
        self.roads = RoadBook(self.game_dirs, wall=wall_clock, mono=mono_clock)

        self.recorder: SessionRecorder | None = None
        self.last_record: SessionRecord | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=60)
        self.want_connected = config.treadmill.auto_connect
        self.connect_error = ""
        self.addon_dir: Path | None = None
        self.addon_error = ""
        self.last_sync: float | None = None

        # Keep trying to connect even with no session running: true at
        # start-up and after "Connect" is pressed, until a connection is made.
        # After that a dropped link is only chased while there is a reason
        # to (a connection attempt wakes a pad that was switched off).
        self._eager_connect = True
        self._switch_treadmill = False
        # "Check my treadmill" (treadmill_check): while it runs, the probe
        # holds the treadmill's one connection and the loop leaves it be.
        self.check = CheckState()
        self._checking = False
        self._check_task: asyncio.Task | None = None
        self.probe_fn: Any = None  # tests hand in a stand-in for probe.probe
        # The update notice (updates.py): nothing is asked unless it is switched on.
        self.update = UpdateState()
        self._update_task: asyncio.Task | None = None
        self.update_fetch: Any = None  # tests hand in a stand-in for updates.fetch_latest

        self._connect_now = asyncio.Event()
        self.watching = False  # listening for the lost treadmill's advertisements
        self._watch_after = 0.0  # (after the listening failed: not again before this)
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_checkpoint = 0.0
        self._unsaved: list[SessionRecord] = []  # finished walks that could not be written yet
        self._idle_since: float | None = None
        self._last_reading = Reading()
        self._was_moving = False
        self._totals_cache: tuple[float, dict[str, Any]] | None = None
        self._state = self._load_state()

        # Sending walks to a Walka account (walka_account/). Here it is
        # not there unless it is handed in: cli does that, at every start
        # that listens to a real treadmill (see cli._build_walka_account).
        # It has tasks of its own and is only ever told things here - that
        # a walk was saved, that Walka is closing: recording a walk never
        # waits for it.
        self.walka_account = walka_account
        if walka_account is not None:
            walka_account.attach(self)

        # The floating window (window.FloatingWindow): there only when Walka
        # has a window of its own to put it beside. It is shown and hidden
        # from here, as the setting says; see attach_overlay.
        self.overlay: Any = None

    # ---- small persistent state --------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.data_dir / STATE_FILE).read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            (self.data_dir / STATE_FILE).write_text(json.dumps(self._state), "utf-8")
        except OSError as exc:
            log.debug("could not save state: %s", exc)

    def log_event(self, text: str, level: str = "info") -> None:
        self.events.appendleft({"time": self.wall(), "level": level, "text": text})
        getattr(log, level if level in ("info", "warning", "error") else "info")(text)

    # ---- treadmill events --------------------------------------------------------
    def _on_treadmill_event(self, event: TreadmillEvent) -> None:
        if event is TreadmillEvent.DISCONNECTED:
            self.log_event("Treadmill disconnected.", "warning")
            self._connect_now.set()
        elif event is TreadmillEvent.CONNECTED:
            status = self.treadmill.status
            self.connect_error = ""
            self._eager_connect = False
            # ("Walka only listens" is said once, under the belt's state on the Live screen.)
            self.log_event(f"Treadmill connected: {status.name or status.address}.")
            self._pin_treadmill()
            self._state["treadmill"] = {"name": status.name}
            self._save_state()
            self.sync_addon()

    def _pin_treadmill(self) -> None:
        """Tie Walka to the treadmill it has just connected to.

        With no address stored, a reconnect takes the first treadmill that
        answers - which, with two pads in range, need not be the one the
        walker is standing on: somebody else's walk would be counted. From
        the first connection on, only this pad. (The simulated treadmill
        has no address to pin.)"""
        status = self.treadmill.status
        if not hasattr(self.treadmill, "address") or not status.address:
            return
        if not self.config.treadmill.address:
            self.config.treadmill.address = status.address
            self.save_config()
            self.log_event(
                f"Walka now only reconnects to this treadmill ({status.name or status.address}). "
                'To use another one, pick it from a scan or press "Forget this treadmill".'
            )
        if not self.treadmill.address:
            self.treadmill.address = self.config.treadmill.address

    # ---- the tick ----------------------------------------------------------------
    def current_reading(self, mono: float) -> Reading:
        return self.beacon.reading(mono) if self.beacon is not None else Reading()

    def source_kind(self) -> str:
        """What the walk being recorded would be filed under right now."""
        if self._last_reading.active and self._last_reading.age_s <= SIGNAL_TIMEOUT_S:
            return "wow"
        return "none"

    def belt_speed(self, mono: float) -> float:
        """The speed the belt reports, in km/h: 0 with no treadmill, or
        with one that has not been heard from for a while (no recent data:
        do not pretend to know that the belt is moving)."""
        status = self.treadmill.status
        if not status.connected or status.age_s(mono) > STALE_DATA_S:
            return 0.0
        return status.speed_kmh

    async def tick(self) -> None:
        """One step: look at the game and at the belt, and record."""
        mono, wall = self.mono(), self.wall()
        reading = self.current_reading(mono)
        self._last_reading = reading
        speed = self.belt_speed(mono)
        moving = speed > MOVING_KMH
        if moving != self._was_moving:
            self._was_moving = moving
            if self.treadmill.status.connected:  # (a belt out of reach has not been heard to stop)
                self.log_event("The belt is moving." if moving else "The belt has stopped.")
        self._record(wall, reading, speed)
        self._maybe_check_for_update()

    # ---- recording ---------------------------------------------------------------
    def _wanted_recording(self, reading: Reading, moving: bool) -> tuple[str, int | None] | None:
        """Which walk should be being recorded right now, if any.

        A walk is whatever the belt does. The game only says what it is
        filed under: while a session is marked there, the walk carries that
        session's number, and the addon finds its measured numbers by it."""
        fresh = reading.age_s <= SIGNAL_TIMEOUT_S
        if fresh and reading.active:
            return ("wow", reading.session)
        recorder = self.recorder
        if not fresh and recorder is not None and recorder.source == "wow":
            # The game is out of view mid-session (a loading screen, another
            # window on top): the walk goes on. (A game that was closed
            # reads as fresh, with no session.)
            return ("wow", recorder.beacon_session)
        if moving:
            return ("free", None)  # walking with no session marked in a game
        return None

    def _record(self, wall: float, reading: Reading, speed_kmh: float) -> None:
        moving = speed_kmh > MOVING_KMH
        if moving:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = wall
        idle_for = 0.0 if self._idle_since is None else wall - self._idle_since

        want = self._wanted_recording(reading, moving)
        recorder = self.recorder
        if recorder is not None:
            current = (recorder.source, recorder.beacon_session)
            if want != current:
                if recorder.source == "free" and want is None and idle_for < FREE_WALK_GRACE_S:
                    want = current  # a short stop does not end a free walk
                else:
                    self._finish_recording(wall)
                    recorder = None
            elif idle_for > ABANDONED_AFTER_S and reading.age_s > SIGNAL_TIMEOUT_S:
                self._finish_recording(wall)
                recorder = None
                want = None

        if recorder is None and want is not None:
            status = self.treadmill.status
            recorder = SessionRecorder(
                source=want[0],
                beacon_session=want[1],
                treadmill=status.name,
                started_at=wall,
            )
            self.recorder = recorder
            self._last_checkpoint = wall

        if recorder is not None:
            status = self.treadmill.status
            bpm = None
            hr = self.heart_rate
            if hr is not None and getattr(hr, "fresh", False):
                bpm = hr.bpm
            elif status.heart_rate_bpm:
                bpm = status.heart_rate_bpm
            recorder.update(
                wall,
                speed_kmh=speed_kmh,
                profile=self.config.profile,
                machine_distance_m=status.distance_m,
                machine_steps=status.steps,
                heart_rate=bpm,
                connected=status.connected,
            )
            if wall - self._last_checkpoint >= CHECKPOINT_EVERY_S:
                self._last_checkpoint = wall
                if self._unsaved:
                    # (Only then. Saving clears the checkpoint, and with it
                    # goes the lock that tells another Walka that this walk
                    # is still going: a walk under way keeps both, without
                    # a gap, from its first checkpoint to its end.)
                    self._save_unsaved()
                if not self._unsaved:
                    # (While a finished walk is still waiting to be written,
                    # the checkpoint file is what keeps it safe: leave it.)
                    self.store.checkpoint(recorder.snapshot(wall))

    def _finish_recording(self, wall: float) -> SessionRecord | None:
        """Close the walk being recorded and file it. Never raises: a disk
        that is full must not stop the loop, nor the shutdown."""
        recorder, self.recorder = self.recorder, None
        record = recorder.finish(wall) if recorder is not None else None
        if record is None:
            if not self._unsaved:
                self.store.clear_checkpoint()
            return None
        self._unsaved.append(record)
        self._save_unsaved(announce=True)
        return record if record not in self._unsaved else None

    def _save_unsaved(self, *, announce: bool = False) -> None:
        """Write finished walks to disk. The file is written first and the
        crash checkpoint cleared only afterwards; a walk that cannot be
        written stays in memory for another try and in the checkpoint, where
        the next start of the companion finds it."""
        while self._unsaved:
            record = self._unsaved[0]
            try:
                self.store.add(record)
            except OSError as exc:
                self.store.checkpoint(record)
                if announce:
                    self.log_event(
                        f"The walk could not be saved ({exc}). Walka keeps it and tries again; "
                        "check that the disk is not full and the data folder can be written.",
                        "error",
                    )
                return
            self._unsaved.pop(0)
            self.last_record = record
            self._totals_cache = None
            self.log_event(
                f"Walk saved: {record.seconds / 60:.0f} min, {record.distance_m / 1000:.2f} km, "
                f"{record.steps} steps, {record.kcal:.0f} kcal."
            )
            self.sync_addon()
            if self.walka_account is not None:
                try:
                    self.walka_account.walk_saved()  # (only wakes its sender: nothing is waited for here)
                except Exception as exc:
                    # This walk is on disk, and the next one in the list
                    # must get there too: nothing of a Walka account may
                    # stand in its way. (The kind of error only, as in start().)
                    log.warning(
                        "the Walka account's part could not be told of a saved walk: an error of the kind %s",
                        type(exc).__name__,
                    )
        self.store.clear_checkpoint()

    # ---- addon sync --------------------------------------------------------------
    def find_addon_dir(self) -> Path | None:
        exe = None
        if self.beacon is not None and self.beacon.window is not None:
            exe = self.beacon.window.exe_path
        self.addon_dir = addon_sync.find_addon_dir(self.config.wow.addon_dir or None, exe)
        return self.addon_dir

    def game_dirs(self) -> list[Path]:
        """Folders of installed games (the ones that hold ``WTF``): where the
        addon is installed first, then every other install found."""
        folders: list[Path] = []
        installed = self.find_addon_dir()
        candidates = [installed.parent] if installed is not None else []
        candidates += addon_sync.find_addons_dirs()
        for addons in candidates:
            # <game>/Interface/AddOns
            if addons.parent.name.lower() == "interface" and addons.parent.parent not in folders:
                folders.append(addons.parent.parent)
        return folders

    def sync_addon(self) -> bool:
        """Write history and profile into the addon folder."""
        directory = self.find_addon_dir()
        if directory is None:
            self.addon_error = "Walka addon folder not found - install the addon or set its folder in Settings."
            return False
        profile = {
            "weightKg": round(self.config.profile.weight_kg, 1),
            "heightCm": round(self.config.profile.height_cm, 1),
            "stepCalibration": round(self.config.profile.step_calibration, 3),
        }
        known = self._state.get("treadmill")
        name = known.get("name", "") if isinstance(known, dict) else ""
        try:
            addon_sync.sync(directory, self.store, profile=profile, treadmill_name=name)
        except Exception as exc:
            # The sync is a convenience. Whatever goes wrong with it - a disk
            # that is full, one damaged walk on file - must not take the
            # loop or the start of the companion down with it.
            self.addon_error = f"could not write the addon data file: {exc}"
            log.warning(self.addon_error, exc_info=not isinstance(exc, OSError))
            return False
        self.addon_error = ""
        self.last_sync = self.wall()
        return True

    def install_addon(self, addons_dir: str | None = None) -> Path:
        """Copy the bundled addon into the game's AddOns folder."""
        if addons_dir:
            target = Path(addons_dir)
        else:
            exe = self.beacon.window.exe_path if self.beacon is not None and self.beacon.window else None
            candidates = []
            from_exe = addon_sync.addons_dir_from_exe(exe)
            if from_exe is not None:
                candidates.append(from_exe)
            candidates.extend(addon_sync.find_addons_dirs())
            if not candidates:
                raise FileNotFoundError("could not find a World of Warcraft AddOns folder - choose it manually")
            target = candidates[0]
        installed = addon_sync.install_addon(target)
        self.config.wow.addon_dir = str(installed)
        self.save_config()
        self.log_event(f"Addon installed to {installed}. Restart the game (or /reload) to load it.")
        self.sync_addon()
        return installed

    # ---- the treadmill connection (dashboard / CLI) -------------------------------
    def connect(self, address: str | None = None) -> None:
        """Ask the connection loop to (re)connect, optionally to a new address."""
        status = self.treadmill.status
        if address is not None:
            switching = status.connected and bool(address) and address.upper() != status.address.upper()
            self.config.treadmill.address = address
            if hasattr(self.treadmill, "address"):
                self.treadmill.address = address or None
            self.save_config()
            self._switch_treadmill = switching
        self.want_connected = True
        self._eager_connect = True
        self._connect_now.set()

    async def use_reader(self, kind: str) -> None:
        """Read the treadmill another way (treadmill/readers.py): a device picked
        from all Bluetooth devices that speaks another protocol. The old reader
        lets go first; the walk being recorded ends where its listening ends."""
        from .treadmill import readers

        if kind == self.treadmill.kind or kind not in readers.KINDS or self.treadmill.kind == "simulated":
            return
        old = self.treadmill
        try:
            await old.disconnect()
        except Exception as exc:
            log.debug("letting go of the old reader failed: %s", exc)
        new = readers.build(
            kind,
            self.config.treadmill.address or None,
            name_prefixes=self.config.treadmill.name_prefixes,
            units=self.config.treadmill.units,
            backend=getattr(old, "_backend", None),
        )
        new.on_event = self._on_treadmill_event
        self.treadmill = new
        self.config.treadmill.kind = kind
        self.save_config()
        self.log_event(f"Walka now reads the treadmill as {kind.upper()}.")

    def forget_treadmill(self) -> None:
        """Forget which treadmill Walka is tied to, so that the next one it
        finds is taken (and remembered in turn)."""
        self.config.treadmill.address = ""
        if hasattr(self.treadmill, "address"):
            self.treadmill.address = None
        self.save_config()
        self.log_event("Treadmill forgotten. Walka will take the next treadmill it connects to, and remember that one.")

    async def disconnect(self) -> None:
        """Let go of the treadmill and stop looking for it. The belt is not
        touched: it does whatever it was doing, and the walk being recorded
        ends where the listening ends."""
        self.want_connected = False
        await self.treadmill.disconnect()

    def connection_wanted(self) -> bool:
        """Should the connection loop be trying to reach the treadmill now?"""
        if not self.want_connected or self._checking:
            return False
        if self._eager_connect:
            return True
        # Otherwise only while there is a walk to listen to: one that is
        # being recorded (the link dropped under it), or a session marked
        # in the game.
        reading = self._last_reading
        return self.recorder is not None or (reading.active and reading.age_s <= SIGNAL_TIMEOUT_S)

    # ---- "Check my treadmill" -------------------------------------------------------
    @property
    def reports_dir(self) -> Path:
        return self.data_dir / REPORTS_DIR

    def start_check(self) -> None:
        """Run the probe from the dashboard (see treadmill_check). Raises
        ValueError, in words for the dashboard, when it cannot run."""
        if self.treadmill.kind == "simulated":
            raise ValueError("The check is for a real treadmill. This is the simulated one.")
        if self._checking:
            raise ValueError("A check is already running.")
        self._checking = True
        self.check = CheckState(running=True, started=self.wall(), line="Letting go of the treadmill ...")
        self._check_task = asyncio.create_task(self._run_check(), name="walka-check")

    async def _run_check(self) -> None:
        was_wanted = self.want_connected
        try:
            self.log_event(
                "Checking the treadmill: Walka lets go of it for about 45 seconds, so that the check can connect. "
                "Like Walka, the check only listens."
            )
            try:
                await self.treadmill.disconnect()
            except Exception as exc:
                log.debug("letting go of the treadmill for the check failed: %s", exc)
            await run_check(
                self.check,
                self.reports_dir,
                address=self.config.treadmill.address or None,
                name_prefixes=self.config.treadmill.name_prefixes,
                probe_fn=self.probe_fn,
                wall=self.wall,
            )
            if self.check.error:
                self.log_event(f"Treadmill check: {self.check.error}", "warning")
            else:
                self.log_event(f"Treadmill check done: the report is saved as {self.check.report}.")
        finally:
            self._checking = False
            self.connect_error = ""
            if was_wanted:
                self.connect()

    # ---- the update notice ------------------------------------------------------------
    def _maybe_check_for_update(self) -> None:
        """From the tick: start a check when it is switched on and one is due."""
        if not self.config.updates.check or self._stopping.is_set():
            return
        if self._update_task is not None and not self._update_task.done():
            return
        mono = self.mono()
        if mono < self.update.next_at:
            return
        fetch = self.update_fetch
        if fetch is None:
            from .updates import fetch_latest as fetch
        self.update.next_at = mono + 60.0  # (not twice while this one runs)
        self._update_task = asyncio.create_task(
            check_for_update(self.update, fetch=fetch, wall=self.wall(), mono=mono), name="walka-update-check"
        )

    def save_config(self) -> None:
        try:
            config_module.save(self.config, self.data_dir)
        except OSError as exc:
            log.warning("could not save the config: %s", exc)

    def update_config(self, patch: dict[str, Any]) -> None:
        """Apply a partial config (from the dashboard), save and re-sync."""
        config_module.apply_update(self.config, patch)
        if isinstance(patch.get("profile"), dict) and not self._state.get("profile_set"):
            # Saving the body profile - even unchanged - confirms it, which
            # ticks the dashboard's first-run checklist.
            self._state["profile_set"] = True
            self._save_state()
        treadmill_patch = patch.get("treadmill")
        if isinstance(treadmill_patch, dict):
            # Hand these to the reader so they apply on its next connection
            # rather than only after a restart.
            if "address" in treadmill_patch and hasattr(self.treadmill, "address"):
                self.treadmill.address = self.config.treadmill.address or None
            if "units" in treadmill_patch and hasattr(self.treadmill, "units"):
                self.treadmill.units = self.config.treadmill.units
        self.save_config()
        if isinstance(patch.get("overlay"), dict):
            self._apply_overlay()
        if isinstance(patch.get("updates"), dict):
            # Switched on: ask now. Switched off: forget the answer (and ask nothing).
            self.update = UpdateState()
            if self._update_task is not None:
                self._update_task.cancel()
                self._update_task = None
        self.sync_addon()

    # ---- the floating window ---------------------------------------------------------
    def attach_overlay(self, overlay: Any) -> None:
        """Hand over the floating window (anything with ``set_shown``), and
        show it if the setting says so. On the companion's loop."""
        self.overlay = overlay
        self._apply_overlay()

    def _apply_overlay(self) -> None:
        if self.overlay is None:
            return
        try:
            self.overlay.set_shown(self.config.overlay.enabled)
        except Exception:
            log.exception("could not show or hide the floating window")

    def overlay_moved(self, x: int, y: int) -> None:
        """The floating window was dragged: remember where to, for next time."""
        config_module.apply_update(self.config, {"overlay": {"x": x, "y": y}})
        self.save_config()

    def overlay_closed(self) -> None:
        """The floating window was closed other than from Settings (from the
        system, or its own close button): the setting follows."""
        if self.config.overlay.enabled:
            self.config.overlay.enabled = False
            self.save_config()

    def system_units(self) -> str | None:
        """The units of the computer's region (the first-run guide's
        suggestion), looked up once."""
        if not hasattr(self, "_system_units"):
            from .locale_units import system_units

            try:
                self._system_units: str | None = system_units()
            except Exception:  # a suggestion: never worth a failure
                self._system_units = None
        return self._system_units

    def profile_is_set(self) -> bool:
        """Has the walker's body profile been entered? Defaults do not count
        until they are saved from the dashboard."""
        return bool(self._state.get("profile_set")) or self.config.profile != Profile()

    # ---- snapshot for the dashboard ----------------------------------------------
    def totals(self, wall: float) -> dict[str, Any]:
        cached = self._totals_cache
        if cached is not None and wall - cached[0] < 2.0:
            return cached[1]
        extra = []
        if self.recorder is not None and self.recorder.seconds > 0:
            extra.append(self.recorder.snapshot(wall))
        totals = self.store.totals(wall, extra)
        self._totals_cache = (wall, totals)
        return totals

    def snapshot(self) -> dict[str, Any]:
        wall = self.wall()
        status = self.treadmill.status
        frame = self.beacon.frame if self.beacon is not None else None
        hr = self.heart_rate
        treadmill = status.to_dict()
        treadmill.update(
            kind=self.treadmill.kind,
            replay=bool(getattr(self.treadmill, "replay", False)),
            want_connected=self.want_connected,
            error=self.connect_error,
            # Walka is tied to one treadmill (see _pin_treadmill).
            pinned=bool(self.config.treadmill.address) and hasattr(self.treadmill, "address"),
            moving=self._was_moving,
        )
        reading = self._last_reading
        snapshot: dict[str, Any] = {
            "version": __version__,
            "time": wall,
            "units": self.config.units,
            "treadmill": treadmill,
            "source": {
                "kind": self.source_kind(),
                "signal_age_s": None if math.isinf(reading.age_s) else round(reading.age_s, 2),
                "beacon": self.beacon.status.to_dict() if self.beacon is not None else None,
                "frame": None
                if frame is None
                else {
                    "seq": frame.seq,
                    "session": frame.session,
                    "active": frame.active,
                    "paused": frame.paused,
                    "char_speed_yps": round(frame.char_speed_yps, 2),
                    "flags": [flag.name for flag in Flags if flag in frame.flags and flag not in LEGACY_FLAGS],
                },
            },
            "session": self.recorder.live() if self.recorder is not None else None,
            "last_session": self.last_record.summary() if self.last_record is not None else None,
            "totals": self.totals(wall),
            "heart_rate": hr.to_dict() if hr is not None else None,
            "addon": {
                "dir": str(self.addon_dir) if self.addon_dir else None,
                "last_sync": self.last_sync,
                "error": self.addon_error,
            },
            "check": self.check.to_dict(self.wall()),
            "update": self.update.to_dict(self.config.updates.check),
            "setup": {"profile_set": self.profile_is_set(), "system_units": self.system_units()},
            "config": self.config.to_dict(),
            "events": list(self.events)[:30],
            "journey": self.journeys.summary(),
            "character": self.journeys.playing(),
            # Can the floating window be shown? Only beside Walka's own window.
            "overlay": {"available": self.overlay is not None},
        }
        if self.walka_account is not None:
            # Only with the part handed in: without it the page has no
            # Walka-account card, and nothing here names one.
            snapshot["walka_account"] = self.walka_account.snapshot()
        return snapshot

    # ---- lifecycle ---------------------------------------------------------------
    async def _tick_loop(self) -> None:
        while not self._stopping.is_set():
            started = self.mono()
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(max(0.02, TICK_S - (self.mono() - started)))

    async def _connect_loop(self) -> None:
        backoff = 5.0
        while not self._stopping.is_set():
            if self._switch_treadmill:
                # A different treadmill was chosen: let go of the current one.
                self._switch_treadmill = False
                if self.treadmill.status.connected:
                    await self.treadmill.disconnect()
            if self._checking:
                self.connect_error = "let go of for \"Check my treadmill\" - Walka connects again when the check is done"
            elif not self.treadmill.status.connected and self.want_connected and not self.connection_wanted():
                self.connect_error = (
                    "not connected - Walka connects again when the treadmill is switched on, when a session "
                    "starts in the game, or when you press Connect"
                )
            if self.connection_wanted() and not self.treadmill.status.connected:
                try:
                    await self.treadmill.connect()
                    backoff = 5.0
                except TreadmillError as exc:
                    if str(exc) != self.connect_error:
                        self.log_event(f"Treadmill: {exc}", "warning")
                    self.connect_error = str(exc)
                    backoff = min(60.0, backoff * 1.5)
                except Exception as exc:
                    log.exception("unexpected error while connecting")
                    self.connect_error = str(exc)
                    backoff = min(60.0, backoff * 1.5)
            else:
                backoff = 5.0
            if self.should_watch() and self.mono() >= self._watch_after:
                # Lost, with no walk to listen to: listen for it to come back
                # (returns as soon as anything changes, within a second).
                try:
                    if await self.watch_once():
                        continue
                except Exception as exc:
                    log.debug("listening for the treadmill failed: %s", exc)
                    self._watch_after = self.mono() + WATCH_RETRY_S
            self._connect_now.clear()
            try:
                # Connected, or idle until there is a walk to listen to:
                # look again soon. Otherwise back off between attempts.
                idle = self.treadmill.status.connected or not self.connection_wanted()
                await asyncio.wait_for(self._connect_now.wait(), timeout=2.0 if idle else backoff)
            except asyncio.TimeoutError:
                pass

    def should_watch(self) -> bool:
        """Listen for the lost treadmill coming back? Only for the treadmill
        Walka is tied to, while it wants one and is not reaching for it anyway."""
        return (
            self.want_connected
            and not self._checking
            and not self.treadmill.status.connected
            and not self.connection_wanted()
            and bool(self.config.treadmill.address)
            and hasattr(self.treadmill, "watch")
        )

    async def watch_once(self) -> bool:
        """Listen until the treadmill comes back (True) or listening is no
        longer wanted (False). Raises what the Bluetooth listening raises."""
        detector = ReturnDetector(self.mono())
        returned = asyncio.Event()

        def seen() -> None:
            if detector.seen(self.mono()):
                returned.set()

        self.watching = True
        task = asyncio.create_task(self.treadmill.watch(seen), name="walka-watch-adverts")
        try:
            while not returned.is_set() and self.should_watch() and not self._stopping.is_set():
                if task.done():
                    task.result()  # (raises what it raised)
                    return False
                try:
                    await asyncio.wait_for(returned.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.watching = False
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if returned.is_set() and self.should_watch():
            self.log_event("The treadmill is switched on again: Walka connects to it.")
            self._eager_connect = True
            self._connect_now.set()
            return True
        return False

    async def start(self) -> None:
        """Start the background loops."""
        if self.beacon is not None:
            self.beacon.start()
        if self.heart_rate is not None:
            self._tasks.append(asyncio.create_task(self.heart_rate.run(), name="walka-heart-rate"))
        self._tasks.append(asyncio.create_task(self._tick_loop(), name="walka-tick"))
        self._tasks.append(asyncio.create_task(self._connect_loop(), name="walka-connect"))
        self._tasks.append(asyncio.create_task(self.journeys.run(), name="walka-journey"))
        if self.walka_account is not None:
            try:
                # (Its task is its own, not one of self._tasks: see stop().)
                self.walka_account.start()
            except Exception as exc:
                # Walka works in full without it. (The kind of error only:
                # its words could name the Walka account, and the log is
                # no place for that.)
                log.warning("the Walka account's part did not start: an error of the kind %s", type(exc).__name__)
        self.sync_addon()

    async def stop(self) -> None:
        """Close down: save the walk, sync, let go of the treadmill. The
        belt is left as it is - it was never the companion's to stop."""
        self._stopping.set()
        self._connect_now.set()
        if self._check_task is not None:
            self._check_task.cancel()
        if self._update_task is not None:
            self._update_task.cancel()
        for task in self._tasks:
            task.cancel()
        if self.walka_account is not None:
            # Told to stop here, with the tasks and before the walk is
            # saved - and only told: this waits for nothing. From now on it
            # asks nobody and its routes refuse. What has to be waited for
            # comes last, when everything of Walka's own is done.
            try:
                self.walka_account.cancel()
            except Exception as exc:
                log.warning("the Walka account's part did not stop cleanly: an error of the kind %s", type(exc).__name__)
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        try:
            self._finish_recording(self.wall())
            self._save_unsaved()
        except Exception:
            log.exception("could not save the walk on exit")
        if self.heart_rate is not None:
            try:
                await self.heart_rate.close()
            except Exception:
                pass
        try:
            await self.treadmill.disconnect()
        except Exception as exc:
            log.debug("disconnect on exit failed: %s", exc)
        if self.beacon is not None:
            self.beacon.stop()
        self.journeys.save()
        self.save_config()
        if self.walka_account is not None:
            # Last of all: the walk is on disk, the treadmill let go of and
            # the settings saved. Whoever is ending Walka waits a few
            # seconds at most (cli.SAVE_WAIT_S), and none of them may be
            # spent on a Walka account before the walk is safe.
            try:
                await self.walka_account.close()
            except Exception as exc:
                log.debug("closing the Walka account's part failed: an error of the kind %s", type(exc).__name__)
