"""Recording walks: live accumulation, storage and history.

The treadmill's own counters cannot be trusted as totals: the pad zeroes
them when a workout ends or after a long pause, its display wraps, and its
calorie figure ignores your weight. So the recorder integrates everything
itself, using the pad's counters only as *deltas* (see ``CounterTracker``).
"""

from __future__ import annotations

import errno
import json
import logging
import os
import sys
import time
import weakref
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
    # "free": walked with no session of the game's known (yet) - "wow": in a
    # session marked in the game, as its saved file showed afterwards
    # (game_file) - "manual" and others: from elsewhere.
    source: str = "manual"
    game_session: int | None = None  # the number the addon gave that session
    character: str = ""  # whose session it was: the character's name (never the realm)
    steps_source: str = "estimate"  # "treadmill" | "estimate"
    distance_source: str = "speed"  # "treadmill" | "speed"
    calories_source: str = "speed"  # "speed" | "heart_rate"
    treadmill: str = ""
    samples: list[list[float | None]] = field(default_factory=list)  # [t, kmh, bpm]
    # Walked on no treadmill: the simulated one (--simulate, --demo) or a
    # recording played back (--replay). Marked when it is recorded, from the
    # kind of treadmill, never from a name. Never sent to a Walka account.
    made_up: bool = False

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
        values = {k: v for k, v in data.items() if k in known}
        if "game_session" not in values and "beacon_session" in data:
            # (Up to 0.10 the session's number was read off the screen.)
            values["game_session"] = data["beacon_session"]
        return cls(**values)


# What the simulated treadmill calls itself (treadmill/simulated.py): the
# walks of Walkas before 0.13 that were made up are known by this name.
SIMULATED_NAME = "Simulated pad"


def is_made_up(record: object) -> bool:
    """Was this walk walked on no treadmill (``SessionRecord.made_up``, or
    the simulated pad's name in a walk saved before there was the mark)?"""
    return getattr(record, "made_up", False) is True or getattr(record, "treadmill", "") == SIMULATED_NAME


def _plausible(counter_total: float, integrated: float) -> bool:
    """Is a counter-derived total believable next to our own integration?"""
    if integrated <= 0:
        return counter_total <= 0
    return 0.6 * integrated <= counter_total <= 1.6 * integrated


class SessionRecorder:
    """Accumulates one walk from once-per-tick treadmill readings."""

    def __init__(self, *, treadmill: str, started_at: float, source: str = "free", made_up: bool = False) -> None:
        # (Which game session the walk was in is known only once the game
        # has saved its file: game_file, App.file_walks.)
        self.source = source
        self.treadmill = treadmill
        self.made_up = made_up
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
            steps_source=self.steps_source,
            distance_source=self.distance_source,
            calories_source=self.calories_source,
            treadmill=self.treadmill,
            samples=list(self.samples),
            made_up=self.made_up,
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


# ---- whose walk is still going -------------------------------------------------
#
# ``active-session.json`` is the walk that is still going, written again
# every half minute. A Walka that is started finds it there and cannot tell
# from the file whether the Walka that wrote it crashed or is still at it:
# Walka can be opened twice on one data folder. So the Walka that writes it
# holds a lock of the system's own on an empty file beside it, from the
# walk's first checkpoint until the checkpoint is cleared. The system lets
# go of the lock when the process ends, however it ends: after a crash the
# lock is free, and the walk is recovered as it always was.
#
# The lock only ever answers one question: is that walk still going
# elsewhere? It is asked at a start, and before a walk's first checkpoint
# is written where another Walka's lies. It never keeps a checkpoint from
# being written, and where a folder has no locks at all a start recovers
# what it finds, as it did without them.
#
# And a Walka clears the checkpoint it wrote itself, no other: one that it
# left alone at its start is not its to take away when it is closed.

LOCK_NAME = "active-session.lock"

_ON_WINDOWS = sys.platform == "win32"
_BINARY = getattr(os, "O_BINARY", 0)
# Windows locks single bytes, and no other program can read a byte that is
# locked. So the byte lies far behind the end of the file, which is empty:
# whoever reads the file (a backup, a virus scanner) never comes near it.
_LOCK_AT = 1 << 30
# A second byte, one for each process: see ``_held_elsewhere``.
_PROBE_AT = _LOCK_AT + 1 + (os.getpid() & 0xFFFFF)

_OURS, _THEIRS, _NO_LOCK = "ours", "theirs", "no lock"


def _lock(fd: int, at: int = _LOCK_AT) -> None:
    """Take the lock, or raise OSError at once: this never waits."""
    if _ON_WINDOWS:
        import msvcrt

        os.lseek(fd, at, os.SEEK_SET)  # Windows locks from where the file stands
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        # flock, not lockf: lockf is let go when ANY descriptor of the file
        # is closed in this process, and would not hold between two stores
        # of one process either.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int, at: int = _LOCK_AT) -> None:
    try:
        if _ON_WINDOWS:
            import msvcrt

            os.lseek(fd, at, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:  # closing the descriptor lets go of it anyway
        pass


def _held_elsewhere(fd: int, refusal: OSError) -> bool:
    """The lock was refused. Because somebody holds it - or because this folder has no locks?

    Only the first may keep a walk from being recovered.
    """
    if not _ON_WINDOWS:
        return refusal.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
    # Windows says the same of both. A byte that nobody else locks tells
    # them apart: if that one can be taken, locks work here, and the other
    # one is held.
    try:
        _lock(fd, _PROBE_AT)
    except Exception:
        return False
    _unlock(fd, _PROBE_AT)
    return True


class _Hold:
    """One open descriptor that holds the lock. Letting go twice, or from two threads, is fine."""

    def __init__(self, fd: int) -> None:
        self._fd = [fd]

    @property
    def held(self) -> bool:
        return bool(self._fd)

    def let_go(self) -> None:
        try:
            fd = self._fd.pop()  # (one step: a second caller finds nothing)
        except IndexError:
            return
        _unlock(fd)
        try:
            os.close(fd)
        except OSError:
            pass


# Held by a running Walka for as long as it runs, on the data folder it
# runs on: a second Walka on the same folder would write over the first's
# config.json and the addon's file. Empty, like the walk's lock.
FOLDER_LOCK_NAME = "walka.lock"


def hold_data_folder(data_dir: Path) -> tuple[str, _Hold | None]:
    """Take the lock that says a Walka runs on ``data_dir``. Never waits.

    ``("ours", hold)``: taken, until ``hold.let_go()`` or the process ends.
    ``("theirs", None)``: another Walka runs on this folder.
    ``("no lock", None)``: this folder has no locks to be had (then nothing
    can be told, and nothing is refused).
    """
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        fd = os.open(Path(data_dir) / FOLDER_LOCK_NAME, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
    except OSError:
        return _NO_LOCK, None
    try:
        _lock(fd)
    except OSError as refusal:
        theirs = _held_elsewhere(fd, refusal)
        os.close(fd)
        return (_THEIRS if theirs else _NO_LOCK), None
    return _OURS, _Hold(fd)


# The locks THIS process holds, by the real path of the lock file. The
# system's lock belongs to one open descriptor: a second store on the same
# folder in the same process would be refused by the first one's lock, and
# could not tell itself from another Walka. With this it can. (That second
# store is Walka "crashed and started again" as the tests play it.)
_HELD: dict[str, _Hold] = {}


def _let_go_of(taken: list[_Hold]) -> None:
    """A store is gone that never cleared its checkpoint: its descriptor must not stay open."""
    while taken:
        taken.pop().let_go()


class SessionStore:
    """Finished walks on disk: one JSON file per walk under ``sessions/``."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "sessions"
        self.active_path = Path(data_dir) / "active-session.json"
        self.lock_path = Path(data_dir) / LOCK_NAME
        try:
            self._lock_key = os.path.normcase(os.path.realpath(self.lock_path))
        except (OSError, ValueError):
            self._lock_key = os.path.normcase(os.path.abspath(self.lock_path))
        self._taken: list[_Hold] | None = None  # the hold this store took, for the day the store is dropped
        self._own_checkpoint = False  # the checkpoint that lies there is this store's to clear
        # A checkpoint lies there that could not be written as a walk (a full
        # disk): the only copy of that walk. It is kept, and tried again.
        self.recovery_failed = False
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
        """A checkpoint left behind by a crash becomes a finished walk.

        Not one whose walk is still going in another Walka on this folder:
        made into a finished walk now, it would be cut short for good.
        """
        if not self.active_path.exists():
            return
        if self._claim() == _THEIRS:
            log.info("a walk is still going in another Walka on this data folder: its checkpoint is left alone")
            return
        if self._walk_of_the_checkpoint():
            self._remove_checkpoint()

    def _walk_of_the_checkpoint(self) -> bool:
        """Make a finished walk of the checkpoint that lies there. Never raises.

        True once the checkpoint can go: its walk is on file now, or it
        holds none (too short, or not a walk at all). False while the walk
        could not be written (a full disk, a folder that cannot be written,
        a checkpoint that cannot be read just now): then the checkpoint is
        the only copy of the walk, and must stay where it is.
        """
        try:
            text = self.active_path.read_text("utf-8")
        except FileNotFoundError:
            self.recovery_failed = False
            return True
        except (OSError, UnicodeDecodeError) as exc:
            if isinstance(exc, UnicodeDecodeError):
                log.warning("the unfinished walk's checkpoint is damaged: %s", exc)
                self.recovery_failed = False
                return True
            log.warning("could not read the unfinished walk's checkpoint (it is kept): %s", exc)
            self.recovery_failed = True
            return False
        try:
            record = SessionRecord.from_json(json.loads(text))
        except Exception as exc:
            log.warning("the unfinished walk's checkpoint is damaged: %s", exc)
            self.recovery_failed = False
            return True
        if self._on_file(record):
            # Saved already: Walka was closed (or killed) after it wrote the
            # walk and before it cleared the checkpoint, which is a snapshot
            # of that walk from a little earlier.
            log.info("the unfinished walk from %s is on file already", day_key(record.start))
        elif record.seconds >= MIN_SESSION_SECONDS:
            try:
                self.add(record)
            except Exception as exc:
                log.warning("could not save the unfinished walk (its checkpoint is kept): %s", exc)
                self.recovery_failed = True
                return False
            log.info("recovered an unfinished walk from %s", day_key(record.start))
        self.recovery_failed = False
        return True

    def _on_file(self, record: SessionRecord) -> bool:
        """Is the walk of this checkpoint on file already? The same walk:
        the same start, from the same treadmill, and on file at least as
        long (a checkpoint is a snapshot taken before the walk ended)."""
        return any(
            kept.start == record.start and kept.treadmill == record.treadmill and kept.seconds >= record.seconds
            for kept in self.records
        )

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

    def update(self, record: SessionRecord) -> None:
        """Write a walk that is on file already again, changed (the game
        session it was in has become known). Raises OSError."""
        if record not in self.records:
            raise KeyError(record.id)
        self._write(self.dir / f"{record.id}.json", record.to_json())

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

    def _claim(self) -> str:
        """Say whose the walk still going is, taking the lock if it is free. Never waits, never raises.

        ``_OURS``: this process holds the lock (now, or already).
        ``_THEIRS``: another Walka holds it. ``_NO_LOCK``: there is no
        lock to be had in this folder - then everything is as it was
        before there were locks.
        """
        try:
            hold = _HELD.get(self._lock_key)
            if hold is not None and hold.held:
                return _OURS
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
            kept = False
            try:
                try:
                    _lock(fd)
                except OSError as refusal:
                    return _THEIRS if _held_elsewhere(fd, refusal) else _NO_LOCK
                hold = _Hold(fd)
                kept = True
            finally:
                if not kept:
                    os.close(fd)
            _HELD[self._lock_key] = hold
            if self._taken is None:
                # (Made once for each store, and it knows the list only: not the store.)
                self._taken = []
                weakref.finalize(self, _let_go_of, self._taken)
            self._taken[:] = [hold]
            return _OURS
        except Exception as exc:
            log.debug("no lock for the walk that is still going: %s", type(exc).__name__)
            return _NO_LOCK

    def checkpoint(self, record: SessionRecord) -> None:
        # The lock first, then the file: a checkpoint of a Walka that runs
        # never lies there without it. (Refused or not, the walk is written.)
        whose = self._claim()
        if whose == _OURS and not self._own_checkpoint and not self._recover_what_was_left_meanwhile():
            # The walk of the checkpoint that lies there is not on file yet,
            # and it is the only copy of it: it is not written over. (Its
            # writing failed for want of room, as this one's would.)
            log.debug("checkpoint not written: an earlier walk's checkpoint still waits to be saved")
            return
        self._own_checkpoint = True
        try:
            self._write(self.active_path, record.to_json())
        except OSError as exc:
            log.debug("checkpoint failed: %s", exc)

    def retry_recovery(self) -> bool:
        """Try again to write the walk of a checkpoint that could not be
        written at the start (``recovery_failed``). True once it is on file.
        Never raises."""
        if not self.recovery_failed or self._own_checkpoint:
            return False
        try:
            if self._claim() != _OURS:
                return False
            if self._walk_of_the_checkpoint():
                self._remove_checkpoint()
                return True
        except Exception as exc:
            log.debug("the unfinished walk was not tried again: %s", type(exc).__name__)
        return False

    def _recover_what_was_left_meanwhile(self) -> bool:
        """Before this store writes a walk's first checkpoint, with the lock in its hand.

        A checkpoint that lies there now is not this store's, and nobody
        records it any more: another Walka on this folder was recording a
        walk while this one ran, and crashed. No start has recovered it -
        this Walka's own start left it alone, if the walk was going then.
        Written over now, it would be gone: so it is recovered first, as a
        start does it. (Only with the lock in hand: without one, nothing
        says that the other Walka is gone.) Never raises.

        False if a walk lies there that could not be written: the
        checkpoint must not be written over then.
        """
        try:
            hold = _HELD.get(self._lock_key)
            if not self._taken or self._taken[0] is not hold:
                return True  # the lock is held through another store of this process: nothing is known of that one's walk
            if self.active_path.exists():
                return self._walk_of_the_checkpoint()
        except Exception as exc:
            log.debug("what was left of another walk was not looked at: %s", type(exc).__name__)
        return True

    def clear_checkpoint(self) -> None:
        """This Walka's walk is saved, or came to nothing: its checkpoint goes.

        Its own, and no other. A Walka that was started while another one
        records leaves that one's checkpoint where it is at its start - and
        must not take it away when it is closed either: nothing would be
        left of that walk until it is written again, and nothing at all if
        its Walka crashed before that. So a checkpoint this store did not
        write stays. (If nobody records any more, the next start finds it.)
        """
        if self._own_checkpoint:
            self._remove_checkpoint()

    def _remove_checkpoint(self) -> None:
        """Take the checkpoint away: this store wrote it, or has just made a walk of it."""
        self._own_checkpoint = False
        try:
            self.active_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("could not remove checkpoint: %s", exc)
            # (Still this store's to take away: the next clearing tries again, as it always did.)
            self._own_checkpoint = True
        hold = _HELD.pop(self._lock_key, None)
        if hold is not None:
            hold.let_go()

    # ---- queries ---------------------------------------------------------------
    def recent(self, limit: int = 100) -> list[SessionRecord]:
        """Newest first."""
        return list(reversed(self.records[-limit:])) if limit > 0 else []

    def totals(
        self, now: float | None = None, extra: Iterable[SessionRecord] = (), *, made_up: bool = True
    ) -> dict[str, Any]:
        """Totals for today, the last 7 days and all time, plus a 7-day series.
        ``made_up=False``: walks walked on no treadmill (``is_made_up``) count for nothing."""
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
            if not made_up and is_made_up(record):
                continue
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
