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
from .governor import Action, Belt, Command, Governor, Reading
from .protocol import Flags
from .session import SessionRecord, SessionRecorder, SessionStore
from .sources import BeaconSource, ManualSource
from .treadmill import Treadmill, TreadmillError, TreadmillEvent

log = logging.getLogger(__name__)

TICK_S = 0.2
CHECKPOINT_EVERY_S = 30.0
FREE_WALK_GRACE_S = 60.0  # a remote-controlled walk ends after this long stopped
ABANDONED_AFTER_S = 900.0  # a session with no signal and no movement is closed
STALE_DATA_S = 5.0  # treadmill data older than this is not trusted
STATE_FILE = "state.json"


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

        self._connect_now = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._last_checkpoint = 0.0
        self._idle_since: float | None = None
        self._last_reading = Reading()
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
        elif event is TreadmillEvent.DISCONNECTED:
            self.governor.notify_disconnected()
            self.log_event("Treadmill disconnected.", "warning")
        elif event is TreadmillEvent.CONNECTED:
            status = self.treadmill.status
            self.connect_error = ""
            self.log_event(f"Treadmill connected: {status.name or status.address}.")
            self._state["treadmill"] = {
                "name": status.name,
                "min_kmh": status.min_kmh,
                "max_kmh": status.max_kmh,
                "step_kmh": status.step_kmh,
                "mph_grid": status.mph_grid,
            }
            self._save_state()
            self.sync_addon()

    # ---- the control tick --------------------------------------------------------
    def current_reading(self, mono: float) -> Reading:
        if self.manual.active:
            return self.manual.reading()
        if self.beacon is not None:
            return self.beacon.reading(mono)
        return Reading()

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
        return Belt(status.connected, speed, status.min_kmh, status.max_kmh, status.step_kmh)

    async def _execute(self, command: Command) -> None:
        treadmill = self.treadmill
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
                # Forget what we think we commanded so that it is re-sent.
                self.governor.commanded_kmh = 0.0

    async def tick(self) -> None:
        """One control step: read the source, run the governor, record."""
        mono, wall = self.mono(), self.wall()
        reading = self.current_reading(mono)
        self._last_reading = reading
        belt = self._belt(mono)
        for command in self.governor.step(mono, reading, belt):
            await self._execute(command)
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
        if moving:
            return ("free", None)  # walking with the remote, no session
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
                self.store.checkpoint(recorder.snapshot(wall))

    def _finish_recording(self, wall: float) -> SessionRecord | None:
        recorder, self.recorder = self.recorder, None
        self.store.clear_checkpoint()
        if recorder is None:
            return None
        record = recorder.finish(wall)
        if record is None:
            return None
        self.store.add(record)
        self.last_record = record
        self._totals_cache = None
        self.log_event(
            f"Walk saved: {record.seconds / 60:.0f} min, {record.distance_m / 1000:.2f} km, "
            f"{record.steps} steps, {record.kcal:.0f} kcal."
        )
        self.sync_addon()
        return record

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
        self.manual.stop()
        self.governor.notify_external_stop()
        self.governor.user_stopped = True
        self.log_event("STOP pressed - belt stopped.", "warning")
        if self.treadmill.status.connected:
            try:
                async with self._command_lock:
                    await self.treadmill.stop()
            except TreadmillError as exc:
                self.log_event(f"Stop command failed: {exc} - use the treadmill's own stop button!", "error")

    def connect(self, address: str | None = None) -> None:
        """Ask the connection loop to (re)connect, optionally to a new address."""
        if address is not None:
            self.config.treadmill.address = address
            if hasattr(self.treadmill, "address"):
                self.treadmill.address = address or None
            self.save_config()
        self.want_connected = True
        self._connect_now.set()

    async def disconnect(self) -> None:
        self.want_connected = False
        await self.treadmill.disconnect()

    def save_config(self) -> None:
        try:
            config_module.save(self.config, self.data_dir)
        except OSError as exc:
            log.warning("could not save the config: %s", exc)

    def update_config(self, patch: dict[str, Any]) -> None:
        """Apply a partial config (from the dashboard), save and re-sync."""
        config_module.apply_update(self.config, patch)
        self.manual.speed_kmh = self.config.manual.speed_kmh
        self.save_config()
        self.sync_addon()

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
        wall, mono = self.wall(), self.mono()
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
            if self.want_connected and not self.treadmill.status.connected:
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
                wait = backoff if not self.treadmill.status.connected else 2.0
                await asyncio.wait_for(self._connect_now.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        """Start the background loops."""
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
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self.config.treadmill.stop_on_exit and self.treadmill.status.connected:
            for command in self.governor.shutdown(self.mono()):
                await self._execute(command)
        self._finish_recording(self.wall())
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
