"""The companion's engine: wires the speed sources, the governor, the
treadmill, the recorder and the addon sync together.

Everything here runs on one asyncio loop (the beacon reader has its own
thread and is only polled). The web dashboard and the CLI talk to the
``App`` object; nothing else holds state.
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
from .governor import ABANDONED_AFTER_S, Action, Belt, Command, Governor, Reading
from .hotkey import GlobalHotkey
from .metrics import Profile
from .protocol import Flags
from .session import SessionRecord, SessionRecorder, SessionStore
from .sources import BeaconSource, ManualSource
from .treadmill import KMH_PER_MPH, BeltState, Treadmill, TreadmillError, TreadmillEvent

log = logging.getLogger(__name__)

TICK_S = 0.2
CHECKPOINT_EVERY_S = 30.0
FREE_WALK_GRACE_S = 60.0  # a remote-controlled walk ends after this long stopped
# (ABANDONED_AFTER_S, from the governor: a session with no signal and no
# movement is closed - and let go of - after this long.)
STALE_DATA_S = 5.0  # treadmill data older than this is not trusted
STATE_FILE = "state.json"

# What the governor has to tell the user, in plain words: (level, text).
_NOTES = {
    "found_stopped": (
        "warning",
        "The treadmill came back stopped - Walka will not restart it until you resume.",
    ),
    "halt_abandoned": (
        "error",
        "The treadmill has been out of reach for half an hour, so Walka has stopped trying to reconnect "
        "to make sure the belt is off. If the belt is moving, stop it at the treadmill itself!",
    ),
    "resume_expired": (
        "info",
        "Resume was pressed over a minute ago and the belt could not be started in that time, so it no "
        "longer counts. Press Resume again when you are ready to walk.",
    ),
    "released": (
        "info",
        "The belt was started at the treadmill while the game is out of view: it is yours. "
        "Walka leaves it alone until it can see the game again.",
    ),
    "abandoned": ("info", "The game has been out of view for a quarter of an hour: Walka has let go of that session."),
}


class App:
    def __init__(
        self,
        config: Config,
        data_dir: Path,
        *,
        treadmill: Treadmill,
        beacon: BeaconSource | None = None,
        heart_rate: Any = None,
        wall_clock: Callable[[], float] = time.time,
        mono_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wall = wall_clock
        self.mono = mono_clock

        self.store = SessionStore(self.data_dir)
        self.governor = Governor(config.limits)
        self.manual = ManualSource(config.manual.speed_kmh)
        self.beacon = beacon
        self.treadmill = treadmill
        self.treadmill.on_event = self._on_treadmill_event
        self.heart_rate = heart_rate

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
        self.hotkey: GlobalHotkey | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._connect_now = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_checkpoint = 0.0
        self._unsaved: list[SessionRecord] = []  # finished walks that could not be written yet
        self._idle_since: float | None = None
        self._last_reading = Reading()
        self._manual_was_active = False
        self._totals_cache: tuple[float, dict[str, Any]] | None = None
        self._state = self._load_state()
        self._command_lock = asyncio.Lock()

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
        if event is TreadmillEvent.EXTERNAL_STOP:
            if self.governor.driving or self.governor.belt_on:
                self.log_event("Belt stopped at the treadmill - Walka will not restart it until you resume.", "warning")
            self.governor.notify_external_stop()
        elif event is TreadmillEvent.EXTERNAL_START:
            self.governor.notify_external_start(self.mono())
            self._tell_notes()
        elif event is TreadmillEvent.DISCONNECTED:
            self.governor.notify_disconnected()
            self.log_event("Treadmill disconnected.", "warning")
            self._connect_now.set()
        elif event is TreadmillEvent.CONNECTED:
            status = self.treadmill.status
            self.connect_error = ""
            self._eager_connect = False
            self.log_event(f"Treadmill connected: {status.name or status.address}.")
            self._pin_treadmill()
            self._state["treadmill"] = {
                "name": status.name,
                "min_kmh": status.min_kmh,
                "max_kmh": status.max_kmh,
                "step_kmh": status.step_kmh,
                "mph_grid": status.mph_grid,
            }
            self._save_state()
            self.sync_addon()

    def _pin_treadmill(self) -> None:
        """Tie Walka to the treadmill it has just connected to.

        With no address stored, a reconnect takes the first treadmill that
        answers - which, with two pads in range, need not be the one the
        walker is standing on. From the first connection on, only this pad.
        (The simulated treadmill has no address to pin.)"""
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

    def _speed_text(self, kmh: float) -> str:
        if self.config.units == "mph":
            return f"{kmh / KMH_PER_MPH:.1f} mph"
        return f"{kmh:.1f} km/h"

    def _tell_notes(self) -> None:
        """Put what the governor has to say into the event log."""
        notes, self.governor.notes[:] = list(self.governor.notes), []
        for note in notes:
            if note == "ceiling":
                held = self.governor.ceiling_kmh
                if held is not None:
                    self.log_event(
                        f"The speed was lowered at the treadmill: Walka keeps the belt at {self._speed_text(held)} "
                        "or slower until you press Resume (or pause and resume in game)."
                    )
            elif note in _NOTES:
                level, text = _NOTES[note]
                self.log_event(text, level)

    # ---- the control tick --------------------------------------------------------
    def current_reading(self, mono: float) -> Reading:
        if self.manual.active:
            self._manual_was_active = True
            return self.manual.reading()
        if self._manual_was_active:
            self._manual_was_active = False
            # The dashboard session has just ended, and "End session" is a
            # promise that the belt stops - also when a game session is live
            # in the background. Say "session over" once, whatever the game
            # shows: with a stale beacon the governor would otherwise see a
            # *lost signal* and run on through its hold and safe-speed
            # stages, and with a live one the game would take the belt and
            # speed it up. (Marked as ours: it is no proof that the game's
            # session was watched to begin.)
            return Reading(age_s=0.0, active=False, synthetic=True)
        return self.beacon.reading(mono) if self.beacon is not None else Reading()

    def source_kind(self) -> str:
        if self.manual.active:
            return "manual"
        if self._last_reading.active and self._last_reading.age_s <= self.config.limits.signal_timeout_s:
            return "wow"
        return "none"

    def _belt(self, mono: float) -> Belt:
        status = self.treadmill.status
        speed = status.speed_kmh
        if status.connected and status.age_s(mono) > STALE_DATA_S:
            speed = 0.0  # no recent data: do not pretend to know the belt is moving
        return Belt(
            status.connected,
            speed,
            status.min_kmh,
            status.max_kmh,
            status.step_kmh,
            busy=status.busy,
            data_at=status.updated_at,  # changes with every data packet
            target_kmh=status.target_kmh,
        )

    async def _execute(self, command: Command) -> None:
        treadmill = self.treadmill
        if self._stopping.is_set() and command.action in (Action.START, Action.SET_SPEED):
            return  # the companion is closing: nothing that moves the belt goes out any more
        try:
            async with self._command_lock:
                if command.action is Action.START:
                    await treadmill.start()
                elif command.action is Action.SET_SPEED:
                    assert command.speed_kmh is not None
                    await treadmill.set_speed(command.speed_kmh)
                elif command.action is Action.PAUSE:
                    await treadmill.pause()
                elif command.action is Action.STOP:
                    await treadmill.stop()
        except TreadmillError as exc:
            self.log_event(f"Treadmill command {command.action.value} failed: {exc}", "warning")
            if command.action is Action.SET_SPEED:
                # The belt is still set to the speed before this one: the
                # governor tries again from there.
                self.governor.speed_command_failed()

    async def tick(self) -> None:
        """One control step: read the source, run the governor, record."""
        mono, wall = self.mono(), self.wall()
        reading = self.current_reading(mono)
        self._last_reading = reading
        belt = self._belt(mono)
        for command in self.governor.step(mono, reading, belt):
            await self._execute(command)
        self._tell_notes()
        self._record(wall, reading, belt)

    # ---- recording ---------------------------------------------------------------
    def _wanted_recording(self, reading: Reading, moving: bool) -> tuple[str, int | None] | None:
        """Which walk should be being recorded right now, if any."""
        if self.manual.active:
            return ("manual", self.manual.session)
        fresh = reading.age_s <= self.config.limits.signal_timeout_s
        if fresh and reading.active:
            return ("wow", reading.session)
        recorder = self.recorder
        if not fresh and recorder is not None and recorder.source == "wow" and self.governor.driving:
            return ("wow", recorder.beacon_session)  # signal lost mid-session: keep it open
        if moving and not self.governor.halt_pending:
            # Walking with the remote, no session. (A belt still coasting
            # down after our own stop is not the start of such a walk.)
            return ("free", None)
        return None

    def _record(self, wall: float, reading: Reading, belt: Belt) -> None:
        moving = belt.connected and belt.speed_kmh > 0.05
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
            elif idle_for > ABANDONED_AFTER_S and reading.age_s > self.config.limits.signal_timeout_s:
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
                speed_kmh=belt.speed_kmh if belt.connected else 0.0,
                profile=self.config.profile,
                machine_distance_m=status.distance_m,
                machine_steps=status.steps,
                heart_rate=bpm,
                connected=belt.connected,
            )
            if wall - self._last_checkpoint >= CHECKPOINT_EVERY_S:
                self._last_checkpoint = wall
                self._save_unsaved()
                if not self._unsaved:
                    # (While a finished walk is still waiting to be written,
                    # the checkpoint file is what keeps it safe: leave it.)
                    self.store.checkpoint(recorder.snapshot(wall))

    def _finish_recording(self, wall: float) -> SessionRecord | None:
        """Close the walk being recorded and file it. Never raises: a disk
        that is full must not stop the control loop, nor the shutdown."""
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
        self.store.clear_checkpoint()

    # ---- addon sync --------------------------------------------------------------
    def find_addon_dir(self) -> Path | None:
        exe = None
        if self.beacon is not None and self.beacon.window is not None:
            exe = self.beacon.window.exe_path
        self.addon_dir = addon_sync.find_addon_dir(self.config.wow.addon_dir or None, exe)
        return self.addon_dir

    def belt_limits_for_addon(self) -> dict[str, Any] | None:
        known = self._state.get("treadmill")
        if not isinstance(known, dict):
            return None
        limits = self.config.limits
        try:
            return {
                "minKmh": round(float(known["min_kmh"]), 4),
                "maxKmh": round(min(float(known["max_kmh"]), limits.max_speed_kmh), 4),
                "stepKmh": round(float(known["step_kmh"]), 5),
                "accelKmhPerS": limits.accel_kmh_per_s,
                "decelKmhPerS": limits.decel_kmh_per_s,
                "mphGrid": bool(known.get("mph_grid")),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def sync_addon(self) -> bool:
        """Write history, profile and limits into the addon folder."""
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
            addon_sync.sync(
                directory, self.store, profile=profile, limits=self.belt_limits_for_addon(), treadmill_name=name
            )
        except OSError as exc:
            self.addon_error = f"could not write the addon data file: {exc}"
            log.warning(self.addon_error)
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

    # ---- control API (dashboard / CLI) -------------------------------------------
    def manual_start(self, speed_kmh: float | None = None) -> None:
        self.manual.start(speed_kmh)
        self.governor.resume()
        self.log_event("Manual session started.")

    def manual_pause(self) -> None:
        self.manual.pause()

    def manual_resume(self) -> None:
        self.manual.resume()
        self.governor.resume()

    def manual_stop(self) -> None:
        if self.manual.active:
            self.manual.stop()
            self.log_event("Manual session stopped.")

    def manual_set_speed(self, speed_kmh: float) -> None:
        self.manual.set_speed(speed_kmh)
        self.config.manual.speed_kmh = self.manual.speed_kmh

    def resume(self) -> None:
        """Explicit "carry on": clears a stop-at-the-machine or a wait state."""
        self.governor.resume()

    async def emergency_stop(self) -> None:
        """Stop the belt now and keep it stopped until explicitly resumed."""
        await self._stop_now("STOP pressed - belt stopped.")

    async def _stop_now(self, text: str) -> bool:
        """Latch the stop, then send it. Returns whether it could be sent."""
        ended_manual_session = self.manual.active
        self.manual.stop()
        # If the command below fails or is lost, the governor asks again for
        # as long as the belt reports that it is still moving - also after
        # the Bluetooth link has dropped and come back.
        self.governor.stop_pressed(self.mono())
        if ended_manual_session:
            # (not the ordinary "session over" path, which would lift the latch)
            self._manual_was_active = False
            self.governor.end_session()
        self.log_event(text, "warning")
        if not self.treadmill.status.connected:
            self.log_event(
                "The treadmill is not connected, so STOP could not be sent - "
                "if the belt is moving, stop it at the treadmill itself!",
                "error",
            )
            return False
        try:
            async with self._command_lock:
                await self.treadmill.stop()
        except TreadmillError as exc:
            self.log_event(f"Stop command failed: {exc} - use the treadmill's own stop button!", "error")
            return False
        return True

    def _belt_in_use(self) -> bool:
        """Is there a belt that moves, or that may be moving because of us
        (running when the link dropped, or not yet seen to obey a stop)?"""
        status = self.treadmill.status
        governor = self.governor
        if status.connected and (status.speed_kmh > 0.05 or status.state in (BeltState.STARTING, BeltState.RUNNING)):
            return True
        return governor.belt_on or governor.halt_pending or governor.belt_out_of_sight

    def connect(self, address: str | None = None) -> None:
        """Ask the connection loop to (re)connect, optionally to a new address."""
        status = self.treadmill.status
        if address is not None:
            tied_to = self.config.treadmill.address or status.address
            if address.upper() != tied_to.upper() and self._belt_in_use():
                # (Also with the link down: the next reconnect would then
                # reach for another pad than the one that is still running.)
                self.log_event("Stop the belt before switching to another treadmill.", "warning")
                return
            switching = status.connected and bool(address) and address.upper() != status.address.upper()
            self.config.treadmill.address = address
            if hasattr(self.treadmill, "address"):
                self.treadmill.address = address or None
            self.save_config()
            self._switch_treadmill = switching
        self.want_connected = True
        self._eager_connect = True
        self._connect_now.set()

    def forget_treadmill(self) -> bool:
        """Forget which treadmill Walka is tied to, so that the next one it
        finds is taken (and remembered in turn). Refused while a belt is in
        use: Walka could then reconnect to a different pad than the one the
        walker is standing on."""
        if self._belt_in_use():
            self.log_event("Stop the belt before forgetting this treadmill.", "warning")
            return False
        self.config.treadmill.address = ""
        if hasattr(self.treadmill, "address"):
            self.treadmill.address = None
        self.save_config()
        self.log_event("Treadmill forgotten. Walka will take the next treadmill it connects to, and remember that one.")
        return True

    async def disconnect(self) -> bool:
        """Let go of the treadmill and stop looking for it.

        A belt that is moving, counting down, or not yet seen to obey a stop
        is stopped first (and stays stopped until a resume): cutting the link
        under it would leave it running with nobody steering. Returns False,
        still connected, if that stop could not be sent."""
        if self.treadmill.status.connected and self._belt_in_use():
            if not await self._stop_now("Disconnect pressed while the belt was in use - belt stopped first."):
                self.log_event("Not disconnecting: the belt could not be stopped from here.", "error")
                return False
        self.want_connected = False
        await self.treadmill.disconnect()
        return True

    def connection_wanted(self) -> bool:
        """Should the connection loop be trying to reach the treadmill now?"""
        if not self.want_connected:
            return False
        if self._eager_connect:
            return True
        # Otherwise only while something needs the belt - a session, or a
        # stop that a belt of ours has not been seen to obey (the governor
        # gives that up after half an hour out of reach).
        reading = self._last_reading
        signal_live = reading.active and reading.age_s <= self.config.limits.signal_timeout_s
        return self.manual.active or self.governor.driving or signal_live or self.governor.halt_owed

    # ---- global STOP hotkey ----------------------------------------------------------
    def _hotkey_pressed(self) -> None:
        """Called on the hotkey thread: hand the stop to the event loop."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.emergency_stop()))

    def register_hotkey(self) -> None:
        """(Re)register the global STOP hotkey from the config."""
        if self.hotkey is not None:
            self.hotkey.stop()
        self.hotkey = GlobalHotkey(self.config.stop_hotkey, self._hotkey_pressed)
        if self.hotkey.start():
            self.log_event(f"STOP hotkey: {self.hotkey.to_dict()['combination']} (works from inside the game).")
        elif self.config.stop_hotkey and self.hotkey.error and "only available on Windows" not in self.hotkey.error:
            self.log_event(f"STOP hotkey not available: {self.hotkey.error}.", "warning")

    def save_config(self) -> None:
        try:
            config_module.save(self.config, self.data_dir)
        except OSError as exc:
            log.warning("could not save the config: %s", exc)

    def update_config(self, patch: dict[str, Any]) -> None:
        """Apply a partial config (from the dashboard), save and re-sync."""
        treadmill_patch = patch.get("treadmill")
        if isinstance(treadmill_patch, dict) and "address" in treadmill_patch and self._belt_in_use():
            wanted = treadmill_patch["address"]
            if not isinstance(wanted, str) or wanted.upper() != self.config.treadmill.address.upper():
                # Another address would send the next reconnect to another
                # pad than the one that is running.
                self.log_event("Stop the belt before changing the treadmill's address.", "warning")
                patch = {**patch, "treadmill": {k: v for k, v in treadmill_patch.items() if k != "address"}}
        config_module.apply_update(self.config, patch)
        manual_patch = patch.get("manual")
        if isinstance(manual_patch, dict) and "speed_kmh" in manual_patch:
            # Only when the manual speed itself was changed: any other
            # setting must not alter the speed of a session in progress.
            self.manual.speed_kmh = self.config.manual.speed_kmh
        if isinstance(patch.get("profile"), dict) and not self._state.get("profile_set"):
            # Saving the body profile - even unchanged - confirms it, which
            # ticks the dashboard's first-run checklist.
            self._state["profile_set"] = True
            self._save_state()
        if "stop_hotkey" in patch and self._loop is not None:
            self.register_hotkey()
        treadmill_patch = patch.get("treadmill")
        if isinstance(treadmill_patch, dict):
            # Hand these to the driver so they apply on its next connection
            # rather than only after a restart.
            if "address" in treadmill_patch and hasattr(self.treadmill, "address"):
                self.treadmill.address = self.config.treadmill.address or None
            if "units" in treadmill_patch and hasattr(self.treadmill, "units"):
                self.treadmill.units = self.config.treadmill.units
        self.save_config()
        self.sync_addon()

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
        governor = self.governor
        frame = self.beacon.frame if self.beacon is not None else None
        hr = self.heart_rate
        treadmill = status.to_dict()
        treadmill.update(
            kind=self.treadmill.kind,
            want_connected=self.want_connected,
            error=self.connect_error,
            cap_kmh=self.config.limits.max_speed_kmh,
            # Walka is tied to one treadmill (see _pin_treadmill).
            pinned=bool(self.config.treadmill.address) and hasattr(self.treadmill, "address"),
            in_use=self._belt_in_use(),
        )
        reading = self._last_reading
        return {
            "version": __version__,
            "time": wall,
            "units": self.config.units,
            "treadmill": treadmill,
            "governor": {
                "phase": governor.phase.value,
                "reason": governor.reason,
                "wanted_kmh": round(governor.wanted_kmh, 2),
                "commanded_kmh": round(governor.commanded_kmh, 2),
                "driving": governor.driving,
                "user_stopped": governor.user_stopped,
                "needs_act": governor.needs_act,
                "ceiling_kmh": None if governor.ceiling_kmh is None else round(governor.ceiling_kmh, 2),
            },
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
                    "target_kmh": round(frame.target_kmh, 2),
                    "char_speed_yps": round(frame.char_speed_yps, 2),
                    "flags": [flag.name for flag in Flags if flag in frame.flags],
                },
                "manual": {
                    "active": self.manual.active,
                    "paused": self.manual.paused,
                    "speed_kmh": round(self.manual.speed_kmh, 2),
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
            "setup": {"profile_set": self.profile_is_set()},
            "hotkey": self.hotkey.to_dict() if self.hotkey is not None else None,
            "config": self.config.to_dict(),
            "events": list(self.events)[:30],
        }

    # ---- lifecycle ---------------------------------------------------------------
    async def _control_loop(self) -> None:
        while not self._stopping.is_set():
            started = self.mono()
            try:
                await self.tick()
            except Exception:
                log.exception("control tick failed")
            await asyncio.sleep(max(0.02, TICK_S - (self.mono() - started)))

    async def _connect_loop(self) -> None:
        backoff = 5.0
        while not self._stopping.is_set():
            if self._switch_treadmill:
                # A different treadmill was chosen: let go of the current one.
                self._switch_treadmill = False
                if self.treadmill.status.connected:
                    await self.treadmill.disconnect()
            if not self.treadmill.status.connected and self.want_connected and not self.connection_wanted():
                self.connect_error = "not connected - it reconnects when a session starts, or press Connect"
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
            self._connect_now.clear()
            try:
                # Connected, or idle until something needs the belt: look
                # again soon. Otherwise back off between attempts.
                idle = self.treadmill.status.connected or not self.connection_wanted()
                await asyncio.wait_for(self._connect_now.wait(), timeout=2.0 if idle else backoff)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        """Start the background loops."""
        self._loop = asyncio.get_running_loop()
        self.register_hotkey()
        if self.beacon is not None:
            self.beacon.start()
        if self.heart_rate is not None:
            self._tasks.append(asyncio.create_task(self.heart_rate.run(), name="walka-heart-rate"))
        self._tasks.append(asyncio.create_task(self._control_loop(), name="walka-control"))
        self._tasks.append(asyncio.create_task(self._connect_loop(), name="walka-connect"))
        self.sync_addon()

    async def stop(self) -> None:
        """Leave everything safe: stop a belt we drive, save the walk, sync."""
        self._stopping.set()
        self._connect_now.set()
        # The belt first. When the console window is closed Windows gives the
        # process only a few seconds: they go to the stop, not to tidying up.
        if self.config.treadmill.stop_on_exit and self.treadmill.status.connected:
            mono = self.mono()
            for command in self.governor.shutdown(mono, self._belt(mono)):
                await self._execute(command)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.hotkey is not None:
            self.hotkey.stop()
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
        self.save_config()
