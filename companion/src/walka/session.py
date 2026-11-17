"""Recording walks: live accumulation, storage and history.

The treadmill's own counters cannot be trusted as totals: the pad zeroes
them when a workout ends or after a long pause, its display wraps, and its
calorie figure ignores your weight. So the recorder integrates everything
itself, using the pad's counters only as *deltas* (see ``CounterTracker``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import metrics
from .metrics import Profile

log = logging.getLogger(__name__)

MIN_SESSION_SECONDS = 20.0  # shorter walks are not kept
SAMPLE_EVERY_S = 5.0  # resolution of the stored speed/heart-rate series
MOVING_EPS_KMH = 0.05


class CounterTracker:
    """Accumulates a machine counter that may reset or jump.

    ``update`` returns how much the counter advanced since the previous
    call. A decrease is treated as a reset (the new value counts from zero);
    an implausibly large jump is ignored.
    """

    def __init__(self) -> None:
        self.last: float | None = None
        self.total = 0.0

    def update(self, value: float | None, *, max_delta: float | None = None) -> float:
        if value is None:
            return 0.0
        if self.last is None:
            self.last = value  # baseline: whatever was on the counter before us
            return 0.0
        delta = value - self.last
        self.last = value
        if delta < 0:
            delta = value  # counter was reset: it has counted `value` since
        if max_delta is not None and delta > max_delta:
            delta = 0.0
        self.total += delta
        return delta

    def rebase(self) -> None:
        """Forget the last reading (e.g. after a reconnect)."""
        self.last = None


@dataclass
class SessionRecord:
    """A finished walk, as stored on disk and shown in the history."""

    id: str
    start: float  # epoch seconds
    end: float
    seconds: float  # time the belt was moving
    distance_m: float
    steps: int
    kcal: float
    kcal_extra: float = 0.0  # of which on top of sitting at the desk (0 in older records)
    max_kmh: float = 0.0
    avg_hr: float | None = None
    max_hr: int | None = None
    source: str = "manual"  # "wow" | "manual" | ...
    beacon_session: int | None = None
    steps_source: str = "estimate"  # "treadmill" | "estimate"
    distance_source: str = "speed"  # "treadmill" | "speed"
    calories_source: str = "speed"  # "speed" | "heart_rate"
    treadmill: str = ""
    samples: list[list[float | None]] = field(default_factory=list)  # [t, kmh, bpm]

    @property
    def avg_kmh(self) -> float:
        return self.distance_m / self.seconds * 3.6 if self.seconds > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("samples")
        data["avg_kmh"] = round(self.avg_kmh, 2)
        return data

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SessionRecord:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _plausible(counter_total: float, integrated: float) -> bool:
    """Is a counter-derived total believable next to our own integration?"""
    if integrated <= 0:
        return counter_total <= 0
    return 0.6 * integrated <= counter_total <= 1.6 * integrated


class SessionRecorder:
    """Accumulates one walk from once-per-tick treadmill readings."""

    def __init__(self, *, source: str, beacon_session: int | None, treadmill: str, started_at: float) -> None:
        self.source = source
        self.beacon_session = beacon_session
        self.treadmill = treadmill
        self.started_at = started_at
        self.last_at = started_at
        self.seconds = 0.0
        self.max_kmh = 0.0
        # Our own integration of the reported belt speed.
        self.distance_speed_m = 0.0
        self.steps_estimate = 0.0
        self.kcal_speed = 0.0  # speed-and-weight model over the whole walk
        self.kcal_total = 0.0  # what is reported: heart-rate model where it applies
        self.kcal_extra = 0.0  # on top of what sitting at the desk would have burned
        self.hr_seconds = 0.0  # time the heart-rate model was used
        self._hr_high_since: float | None = None
        # The pad's counters, as deltas.
        self._distance = CounterTracker()
        self._steps = CounterTracker()
        self._saw_steps = False
        self._saw_distance = False
        # Heart rate.
        self._hr_sum = 0.0
        self._hr_time = 0.0
        self.max_hr = 0
        self.last_hr: int | None = None
        # Stored series.
        self.samples: list[list[float | None]] = []
        self._next_sample = 0.0
        self.speed_kmh = 0.0

    # ---- live values -----------------------------------------------------------
    @property
    def distance_m(self) -> float:
        if self._saw_distance and _plausible(self._distance.total, self.distance_speed_m):
            return self._distance.total
        return self.distance_speed_m

    @property
    def distance_source(self) -> str:
        if self._saw_distance and _plausible(self._distance.total, self.distance_speed_m):
            return "treadmill"
        return "speed"

    @property
    def steps(self) -> float:
        if self._saw_steps and _plausible(self._steps.total, self.steps_estimate):
            return self._steps.total
        return self.steps_estimate

    @property
    def steps_source(self) -> str:
        if self._saw_steps and _plausible(self._steps.total, self.steps_estimate):
            return "treadmill"
        return "estimate"

    @property
    def kcal(self) -> float:
        return self.kcal_total

    @property
    def calories_source(self) -> str:
        if self.hr_seconds > 0 and self.seconds > 0 and self.hr_seconds / self.seconds >= 0.5:
            return "heart_rate"
        return "speed"

    @property
    def avg_hr(self) -> float | None:
        return self._hr_sum / self._hr_time if self._hr_time > 0 else None

    def live(self) -> dict[str, Any]:
        """Numbers for the dashboard while the walk is in progress."""
        return {
            "started_at": self.started_at,
            "source": self.source,
            "beacon_session": self.beacon_session,
            "seconds": round(self.seconds, 1),
            "distance_m": round(self.distance_m, 1),
            "steps": int(self.steps),
            "kcal": round(self.kcal, 1),
            "kcal_extra": round(self.kcal_extra, 1),
            "max_kmh": round(self.max_kmh, 2),
            "speed_kmh": round(self.speed_kmh, 2),
            "avg_hr": round(self.avg_hr, 1) if self.avg_hr else None,
            "max_hr": self.max_hr or None,
            "hr": self.last_hr,
            "steps_source": self.steps_source,
            "distance_source": self.distance_source,
            "calories_source": self.calories_source,
        }

    # ---- accumulation ----------------------------------------------------------
    def update(
        self,
        now: float,
        *,
        speed_kmh: float,
        profile: Profile,
        machine_distance_m: float | None = None,
        machine_steps: float | None = None,
        heart_rate: int | None = None,
        connected: bool = True,
    ) -> None:
        """Account for the time since the previous call.

        ``now`` is epoch seconds. Gaps longer than a few seconds (app
        suspended, treadmill reconnecting) are not integrated.
        """
        dt = now - self.last_at
        self.last_at = now
        if dt <= 0:
            return
        if not connected:
            self._distance.rebase()
            self._steps.rebase()
            self.speed_kmh = 0.0
            return
        gap = dt > 5.0
        if gap:
            dt = 0.0
        self.speed_kmh = speed_kmh
        moving = speed_kmh > MOVING_EPS_KMH

        # The pad's counters. Bound the deltas by what the speed allows so a
        # counter glitch cannot add a kilometre.
        allowance = max(dt, 1.0) * max(speed_kmh, 3.0) / 3.6
        if machine_distance_m is not None:
            self._saw_distance = True
            self._distance.update(machine_distance_m, max_delta=allowance * 3 + 20)
        if machine_steps is not None:
            self._saw_steps = True
            self._steps.update(machine_steps, max_delta=max(dt, 1.0) * 5 + 10)

        # The heart-rate formula is only valid for real exertion: the heart
        # rate must have been above 60% of the age-predicted maximum for a
        # minute. At desk-walking heart rates it would read up to double.
        hr_rate = None
        threshold = metrics.hr_threshold_bpm(profile)
        if heart_rate and threshold is not None and heart_rate >= threshold:
            if self._hr_high_since is None:
                self._hr_high_since = now
            if now - self._hr_high_since >= metrics.HR_SUSTAIN_S:
                hr_rate = metrics.kcal_per_minute_from_hr(heart_rate, profile)
        else:
            self._hr_high_since = None

        if moving and dt > 0:
            minutes = dt / 60.0
            self.seconds += dt
            self.max_kmh = max(self.max_kmh, speed_kmh)
            self.distance_speed_m += speed_kmh / 3.6 * dt
            self.steps_estimate += (
                metrics.steps_per_minute(speed_kmh, profile.height_cm, profile.step_calibration) * minutes
            )
            speed_rate = metrics.kcal_per_minute(speed_kmh, profile)
            self.kcal_speed += speed_rate * minutes
            self.kcal_extra += metrics.kcal_per_minute(speed_kmh, profile, net=True) * minutes
            if hr_rate is not None:
                self.kcal_total += hr_rate * minutes
                self.hr_seconds += dt
            else:
                self.kcal_total += speed_rate * minutes

        if heart_rate and heart_rate > 0:
            self.last_hr = heart_rate
            if moving and dt > 0:
                self._hr_sum += heart_rate * dt
                self._hr_time += dt
                self.max_hr = max(self.max_hr, heart_rate)
        else:
            self.last_hr = None

        offset = now - self.started_at
        if offset >= self._next_sample:
            self._next_sample = offset + SAMPLE_EVERY_S
            self.samples.append([round(offset, 1), round(speed_kmh, 2), heart_rate or None])

    def finish(self, now: float) -> SessionRecord | None:
        """Close the walk. Returns ``None`` if it was too short to keep."""
        if self.seconds < MIN_SESSION_SECONDS:
            return None
        return self.snapshot(now)

    def snapshot(self, now: float) -> SessionRecord:
        """The walk so far as a record (used for crash-safe checkpoints)."""
        avg_hr = self.avg_hr
        return SessionRecord(
            id=time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_at)),
            start=round(self.started_at, 1),
            end=round(max(now, self.started_at), 1),
            seconds=round(self.seconds, 1),
            distance_m=round(self.distance_m, 1),
            steps=int(round(self.steps)),
            kcal=round(self.kcal, 1),
            kcal_extra=round(self.kcal_extra, 1),
            max_kmh=round(self.max_kmh, 2),
            avg_hr=round(avg_hr, 1) if avg_hr else None,
            max_hr=self.max_hr or None,
            source=self.source,
            beacon_session=self.beacon_session,
            steps_source=self.steps_source,
            distance_source=self.distance_source,
            calories_source=self.calories_source,
            treadmill=self.treadmill,
            samples=list(self.samples),
        )


def _bucket() -> dict[str, float]:
    return {"sessions": 0, "seconds": 0.0, "distance_m": 0.0, "steps": 0, "kcal": 0.0}


def _add(bucket: dict[str, float], record: SessionRecord) -> None:
    bucket["sessions"] += 1
    bucket["seconds"] += record.seconds
    bucket["distance_m"] += record.distance_m
    bucket["steps"] += record.steps
    bucket["kcal"] += record.kcal


def day_key(epoch: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def _local_noon(epoch: float) -> float:
    """Noon of the local calendar day that contains ``epoch``.

    Stepping back 24 hours at a time from noon always lands inside the
    previous calendar day, also across a daylight-saving change (a 23- or
    25-hour day); stepping back from just after midnight does not.
    """
    t = time.localtime(epoch)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))


class SessionStore:
    """Finished walks on disk: one JSON file per walk under ``sessions/``."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "sessions"
        self.active_path = Path(data_dir) / "active-session.json"
        self.records: list[SessionRecord] = []  # oldest first
        self.dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        records = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                records.append(SessionRecord.from_json(json.loads(path.read_text("utf-8"))))
            except Exception as exc:
                log.warning("skipping unreadable session file %s: %s", path.name, exc)
        records.sort(key=lambda r: r.start)
        self.records = records
        self._recover_active()

    def _recover_active(self) -> None:
        """A checkpoint left behind by a crash becomes a finished walk."""
        if not self.active_path.exists():
            return
        try:
            record = SessionRecord.from_json(json.loads(self.active_path.read_text("utf-8")))
            if record.seconds >= MIN_SESSION_SECONDS:
                self.add(record)
                log.info("recovered an unfinished walk from %s", day_key(record.start))
        except Exception as exc:
            log.warning("could not recover the unfinished walk: %s", exc)
        self.clear_checkpoint()

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
        os.replace(tmp, path)

    def add(self, record: SessionRecord) -> SessionRecord:
        """Store a finished walk (ids are made unique if two start in the same second)."""
        existing = {r.id for r in self.records}
        base, n = record.id, 1
        while record.id in existing:
            n += 1
            record.id = f"{base}-{n}"
        self._write(self.dir / f"{record.id}.json", record.to_json())
        self.records.append(record)
        self.records.sort(key=lambda r: r.start)
        return record

    def delete(self, session_id: str) -> bool:
        for record in self.records:
            if record.id == session_id:
                self.records.remove(record)
                try:
                    (self.dir / f"{session_id}.json").unlink()
                except FileNotFoundError:
                    pass
                return True
        return False

    def get(self, session_id: str) -> SessionRecord | None:
        return next((r for r in self.records if r.id == session_id), None)

    def checkpoint(self, record: SessionRecord) -> None:
        try:
            self._write(self.active_path, record.to_json())
        except OSError as exc:
            log.debug("checkpoint failed: %s", exc)

    def clear_checkpoint(self) -> None:
        try:
            self.active_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("could not remove checkpoint: %s", exc)

    # ---- queries ---------------------------------------------------------------
    def recent(self, limit: int = 100) -> list[SessionRecord]:
        """Newest first."""
        return list(reversed(self.records[-limit:])) if limit > 0 else []

    def totals(self, now: float | None = None, extra: Iterable[SessionRecord] = ()) -> dict[str, Any]:
        """Totals for today, the last 7 days and all time, plus a 7-day series."""
        now = time.time() if now is None else now
        today_key = day_key(now)
        noon = _local_noon(now)
        days = []
        index: dict[str, dict[str, Any]] = {}
        for offset in range(6, -1, -1):
            key = day_key(noon - offset * 86400)
            if key not in index:
                index[key] = {"day": key, "steps": 0, "distance_m": 0.0, "kcal": 0.0, "seconds": 0.0}
                days.append(index[key])
        today, week, all_time = _bucket(), _bucket(), _bucket()
        walked: set[str] = set()
        for record in [*self.records, *extra]:
            key = day_key(record.start)
            walked.add(key)
            _add(all_time, record)
            if key == today_key:
                _add(today, record)
            if key in index:
                _add(week, record)
                day = index[key]
                day["steps"] += record.steps
                day["distance_m"] += record.distance_m
                day["kcal"] += record.kcal
                day["seconds"] += record.seconds
        streak = 0
        cursor = noon if today_key in walked else noon - 86400
        while day_key(cursor) in walked and streak < 3650:
            streak += 1
            cursor -= 86400
        for bucket in (today, week, all_time):
            bucket["distance_m"] = round(bucket["distance_m"], 1)
            bucket["kcal"] = round(bucket["kcal"], 1)
            bucket["seconds"] = round(bucket["seconds"], 1)
        return {"today": today, "week": week, "all": all_time, "days": days, "streak": streak}
