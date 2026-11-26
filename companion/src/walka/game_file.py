"""What the game tells the companion: the addon's saved file.

A World of Warcraft addon cannot write files, and nothing can reach it while
the game runs. But the game writes one file for it: what the addon keeps in
its saved variables is stored when the player logs out or reloads the
interface - ``WTF/Account/<account>/SavedVariables/Walka.lua`` under the
game's folder, which holds one table, ``WalkaDB``. This module reads it:

* ``decode`` - the table, checked, as a ``SavedGame``: the walking sessions
  the addon marked (start, end, the character), the one still running when
  the game saved, and every character's journey (``journey.Journey``).
* ``GameFiles`` - finds the files of every account, reads each one again
  only when the game has rewritten it, and puts together what they hold.
* ``file_walk`` - which of those sessions a walk on the treadmill was in.

The companion does not watch the game at all: what it learns, it learns from
this file, after the fact. A walk is recorded from the treadmill alone and
filed under the game session it overlaps once the file shows that session.

The files belong to the game: they are only ever read, in one go, and never
held open. Nothing read here leaves the computer, and neither the account's
folder name nor a character's realm is passed on or logged.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import savedvars
from .journey import Journey, djb2, name_of
from .road import SAVED_FILE, read_shared

log = logging.getLogger(__name__)

DB_VARIABLE = "WalkaDB"

MAX_FILE_BYTES = 16 * 1024 * 1024  # 400 sessions and a few dozen journeys are well under a megabyte
MAX_FILES = 50  # accounts looked at
MAX_SESSIONS = 2000  # of one file (the addon keeps 400)
MAX_JOURNEYS = 200  # of one file

# The game writes the file when the player logs out or reloads. One that
# changed a moment ago may still be being written.
SETTLE_S = 2.0
RESCAN_S = 5.0  # how often the folders are looked at, at most
GAME_DIRS_S = 60.0  # ... and how often the game is looked for

# Times a session can have: not before 2020 (there was no Walka) and not
# after the year 3000 (what the game's date() can format).
MIN_EPOCH = 1_577_836_800
MAX_EPOCH = 32_503_680_000
MAX_SESSION_S = 7 * 86400  # no session of the addon's runs longer than this

# A walk is filed under a game session when they overlap for at least this
# share of the shorter one - the rule the addon matches the two by, too
# (SessionLog.sameWalk).
SAME_WALK_SHARE = 0.5


def _time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not MIN_EPOCH <= value < MAX_EPOCH:
        return None
    return float(value)


@dataclass(frozen=True)
class GameSession:
    """A walking session the addon marked in the game."""

    start: float
    end: float  # for the one still running when the game saved: that moment
    number: int  # the addon's counter, 1..255
    key: int  # whose: djb2 of the character's "Name-Realm" (journey.djb2)
    character: str  # ... and the character's name alone
    running: bool = False  # it was still going when the game saved the file

    def overlap(self, start: float, end: float) -> float:
        return max(0.0, min(self.end, end) - max(self.start, start))


def _session(record: Any, end: Any = None, *, running: bool = False) -> GameSession | None:
    """One of the addon's session records, checked; None if it is not one."""
    if not isinstance(record, Mapping):
        return None
    start = _time(record.get("start"))
    finish = _time(record.get("end") if end is None else end)
    if start is None or finish is None or not start < finish <= start + MAX_SESSION_S:
        return None
    number = record.get("id")
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 < number < 256:
        number = 0
    key = record.get("character")
    if not isinstance(key, str) or not key or key.startswith("?"):
        # (The game hid the name as the session began: nobody's.)
        return GameSession(start, finish, int(number), 0, "", running)
    return GameSession(start, finish, int(number), djb2(key), name_of(key), running)


@dataclass(frozen=True)
class SavedGame:
    """What one saved file holds."""

    sessions: tuple[GameSession, ...] = ()
    journeys: tuple[Journey, ...] = ()


def decode(table: Any, saved_at: float = 0.0) -> SavedGame | None:
    """``WalkaDB`` as read from the file, checked; None if it is not the
    addon's table. ``saved_at`` is when the file was written (its date): a
    journey from an addon that did not stamp its own is taken to be as old."""
    if not isinstance(table, Mapping):
        return None
    sessions: list[GameSession] = []
    records = table.get("sessions")
    # (As the file is read, a Lua array is a table of 1, 2, ...; as it is handed in, a list.)
    listed = records if isinstance(records, list) else savedvars.sequence(records)
    for record in listed[:MAX_SESSIONS]:
        session = _session(record)
        if session is not None:
            sessions.append(session)
    active = table.get("active")
    if isinstance(active, Mapping):
        # The session that was going when the game saved: it ran at least
        # until then (if the player comes back within minutes, the addon
        # carries it on and the next file says how long).
        running = _session(active.get("session"), active.get("savedAt"), running=True)
        if running is not None:
            sessions.append(running)
    journeys: list[Journey] = []
    records = table.get("journeys")
    if isinstance(records, Mapping):
        for key, record in list(records.items())[:MAX_JOURNEYS]:
            journey = Journey.from_record(key, record, saved_at)
            if journey is not None:
                journeys.append(journey)
    sessions.sort(key=lambda s: s.start)
    return SavedGame(tuple(sessions), tuple(journeys))


def file_walk(start: float, end: float, sessions: Iterable[GameSession]) -> GameSession | None:
    """The game session a walk from ``start`` to ``end`` was in: the one it
    shares the most time with, if that is at least half of the shorter of
    the two. None if it was in none."""
    best: GameSession | None = None
    best_shared = 0.0
    for session in sessions:
        shared = session.overlap(start, end)
        shorter = min(end - start, session.end - session.start)
        if shorter > 0 and shared >= shorter * SAME_WALK_SHARE and shared > best_shared:
            best, best_shared = session, shared
    return best


def account_files(game_dir: Path) -> list[Path]:
    """The addon's saved file of every account under one game folder."""
    accounts = Path(game_dir) / "WTF" / "Account"
    try:
        # <account>/SavedVariables/Walka.lua (the per-character files, a
        # level further down, hold the road: road.py)
        return sorted(accounts.glob(f"*/SavedVariables/{SAVED_FILE}"))
    except OSError:
        return []


@dataclass
class _File:
    stamp: tuple[int, int] | None = None  # (size, mtime in ns) of what was last read
    waiting: tuple[int, int] | None = None  # ... of what looked too fresh to read
    waiting_since: float = 0.0
    mtime: float = 0.0
    saved: SavedGame | None = None


@dataclass
class GameFiles:
    """The game's saved files of the addon, each read again only when the
    game has rewritten it. ``game_dirs`` says where games are installed
    (folders that hold ``WTF``). ``scan`` reads the disk: call it on a
    worker thread, never on the loop that records the walk."""

    game_dirs: Callable[[], Iterable[Path]]
    wall: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    _files: dict[Path, _File] = field(default_factory=dict)
    _dirs: list[Path] = field(default_factory=list)
    _dirs_at: float = -math.inf
    _scanned_at: float = -math.inf
    _demo: SavedGame | None = None
    _rev: int = 0
    _read_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)  # what was read: held for moments only
    _scan_lock: threading.Lock = field(default_factory=threading.Lock)  # one look at the disk at a time

    def use_demo(self, saved: SavedGame) -> None:
        """``--demo``: what a made-up game would have saved. The disk is not looked at."""
        with self._lock:
            self._demo = saved
            self._rev += 1
            self._read_at = self.wall()

    # ---- what the files hold --------------------------------------------------------
    @property
    def rev(self) -> int:
        """Goes up whenever what the files hold has changed."""
        with self._lock:
            return self._rev

    def sessions(self) -> list[GameSession]:
        """Every game session found, oldest first."""
        with self._lock:
            found = [s for saved in self._saved() for s in saved.sessions]
        return sorted(set(found), key=lambda s: s.start)

    def journeys(self) -> list[Journey]:
        """Every character's journey found (one file per account)."""
        with self._lock:
            return [j for saved in self._saved() for j in saved.journeys]

    def _saved(self) -> list[SavedGame]:
        if self._demo is not None:
            return [self._demo]
        return [entry.saved for entry in self._files.values() if entry.saved is not None]

    def status(self) -> dict[str, Any]:
        """For the dashboard: where the companion has looked and what it found."""
        with self._lock:
            saved = self._saved()
            written = [entry.mtime for entry in self._files.values() if entry.saved is not None]
            demo = self._demo is not None
            return {
                "game_found": demo or bool(self._dirs),
                "files": len(saved),
                "saved_at": self.wall() if demo else max(written, default=None),
                "read_at": self._read_at,
                "sessions": sum(len(s.sessions) for s in saved),
                "characters": len({j.key for s in saved for j in s.journeys}),
                "demo": demo,
            }

    # ---- looking --------------------------------------------------------------------
    def scan(self, *, force: bool = False) -> bool:
        """Look for files the game has written since the last look, and read
        them. True if what the files hold has changed.

        The disk is looked at without the lock that guards what was read:
        what the loop asks meanwhile (``rev``, ``sessions``, ``status``) is
        answered at once from the last look. One look at a time."""
        with self._scan_lock:
            with self._lock:
                if self._demo is not None:
                    return False
                now = self.mono()
                if not force and now - self._scanned_at < RESCAN_S:
                    return False
                self._scanned_at = now
                look_for_game = force or now - self._dirs_at >= GAME_DIRS_S or not self._dirs
                if look_for_game:
                    self._dirs_at = now
                dirs = list(self._dirs)
                # (Copies: the entries are changed out here, and put back below.)
                files = {path: replace(entry) for path, entry in self._files.items()}
            if look_for_game:
                try:
                    dirs = [Path(folder) for folder in self.game_dirs()]
                except Exception:
                    log.debug("could not look for the game", exc_info=True)
                    dirs = []
            found: list[Path] = []
            for folder in dirs:
                found += account_files(folder)
            found = found[:MAX_FILES]
            changed = False
            for path in list(files):
                if path not in found:
                    changed = changed or files[path].saved is not None
                    del files[path]  # the account's folder is gone
            for path in found:
                changed = self._read(path, files.setdefault(path, _File())) or changed
            with self._lock:
                self._dirs = dirs
                self._files = files
                self._read_at = self.wall()
                if changed:
                    self._rev += 1
            return changed

    def _read(self, path: Path, entry: _File) -> bool:
        try:
            status = path.stat()
        except OSError:
            return False
        stamp = (status.st_size, status.st_mtime_ns)
        if entry.stamp == stamp:
            return False  # read already, whatever came of it
        if self.wall() - status.st_mtime < SETTLE_S:
            # Written a moment ago, so perhaps not finished - or its date is
            # ahead of this computer's clock: either way it is read once it
            # has stayed as it is for a while.
            now = self.mono()
            if entry.waiting != stamp:
                entry.waiting, entry.waiting_since = stamp, now
                return False
            if now - entry.waiting_since < SETTLE_S:
                return False
        entry.waiting = None
        problem = ""
        saved: SavedGame | None = None
        try:
            data = read_shared(path, MAX_FILE_BYTES)
            if path.stat().st_mtime_ns != stamp[1]:
                return False  # rewritten while it was read: next time
            if len(data) > MAX_FILE_BYTES:
                raise savedvars.SavedVariablesError("the file is too big")
            saved = decode(savedvars.parse(data).get(DB_VARIABLE), status.st_mtime)
        except (OSError, savedvars.SavedVariablesError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        entry.stamp = stamp  # (a file that cannot be read is not tried again until it changes)
        if problem:
            # What was read from it before stands. (No path here: it holds
            # the name of the player's account.)
            log.debug("the addon's saved file could not be read (%s)", problem)
            return False
        changed = saved != entry.saved
        entry.mtime = status.st_mtime
        entry.saved = saved
        return changed
