"""A character's journey: the numbers behind the picture on the Share page.

The addon keeps, for every character, its level and time played, how far it
has travelled on foot, and the real-world walking done while playing it. It
sends those numbers one at a time, in stat frames (``protocol``), and
``BeaconSource`` hands each one to ``JourneyCollector.on_stat``. This module
puts them back together:

* ``JourneyCollector`` - one consistent set of numbers per character, taken
  from one full round of stat frames. Two characters are never mixed.
* ``Journey`` - that set, checked: a number that cannot be true is left out,
  and the name is treated as text from a stranger.
* ``JourneyBook`` - the latest journey of every character seen, kept on disk
  next to the companion's other state so a picture can be made when the game
  is closed.
* ``DemoJourney`` - the stat frames of a made-up character for ``--demo``.

The collector only ever receives ``(stat id, value)`` pairs (see
``stat_route``), and it knows nothing about the treadmill or the live
reading.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import (
    NAME_STATS,
    STAT_CYCLE,
    STAT_EVERY,
    STAT_MAX_VALUE,
    Faction,
    InfoFlags,
    Stat,
    StatFrame,
    djb2,
    name_from_chunks,
    name_to_chunks,
    pack_char_info,
    unpack_char_info,
)

log = logging.getLogger(__name__)

JOURNEY_FILE = "journey.json"
FILE_VERSION = 1
# An account holds many characters; the ones played lately are the ones a
# picture is made of. The cap also bounds what a stream of made-up characters
# (anything on screen that looks like the strip is decoded) can fill the
# file with.
MAX_CHARACTERS = 30
SAVE_EVERY_S = 15.0

# The addon sends a round of sixteen stats in about eight seconds, one every
# half second. A longer silence means the strip was gone (a loading screen,
# the game minimised, another character logging in): what comes next may
# belong to somebody else, so the round is given up.
GAP_MAX_S = 3.0
# ... and a round that is still open after this long has missed several of its
# CHAR_KEY frames (or the game is drawing a few frames a second); it is not
# trusted either.
ROUND_MAX_S = 30.0
# A character counts as "in the game right now" while rounds keep arriving.
LIVE_FOR_S = 25.0

# The convention behind "character steps": 2,000 steps to an in-game mile of
# 1,760 yards, so one step is 0.88 yards (the addon uses the same numbers).
YARDS_PER_MILE = 1760
STEPS_PER_MILE = 2000

# Numbers beyond these cannot be true; such a value is left out (None).
_CENTURY_S = 100 * 366 * 86400
MAX_PLAYED_S = _CENTURY_S
MAX_YARDS = 10**10  # 5.7 million in-game miles: decades of running without a pause
MAX_REAL_STEPS = 10**9
MAX_REAL_M = 10**9  # a million kilometres
MAX_REAL_KCAL = 10**8
MAX_REAL_S = _CENTURY_S
MAX_WALKS = 10**6
SINCE_MIN = 1_577_836_800  # 1 January 2020: before there was anything to track
SINCE_SLACK_S = 2 * 86400  # ... and not later than "now", give or take a clock change
NAME_MAX_CHARS = 24

_ROUND_STATS = frozenset(STAT_CYCLE) - {Stat.CHAR_KEY}
_KEY_MAX = 0xFFFFFFFF  # CHAR_KEY is a 32-bit hash
_FACTIONS = {Faction.ALLIANCE: "alliance", Faction.HORDE: "horde"}


def clean_name(text: str) -> str:
    """A character name as it may be shown.

    The name arrives through a screen capture, so it is text from a stranger:
    anything on screen that looks like the strip gets decoded. Control and
    formatting characters (line breaks, direction overrides, zero-width
    marks) are removed, runs of blanks become one space, and the length is
    capped. It is still only *text*: whoever shows it must not interpret it
    (the page writes it with ``textContent`` and draws it on a canvas).
    """
    kept = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(kept.split())[:NAME_MAX_CHARS]


def character_steps(foot_yd: int) -> int:
    """On-foot yards as steps, by the 0.88-yards-a-step convention."""
    return (foot_yd * STEPS_PER_MILE + YARDS_PER_MILE // 2) // YARDS_PER_MILE


def _at_most(value: int | None, limit: int) -> int | None:
    """``value`` if it is a believable count, else None."""
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= limit else None


@dataclass(frozen=True)
class Journey:
    """One character's numbers, as of ``seen_at`` (wall-clock seconds).

    None means "not known": the game has not said yet (time played), or what
    arrived could not be true.
    """

    key: int  # the CHAR_KEY hash: tells characters apart, means nothing else
    name: str
    level: int | None
    max_level: int | None
    class_id: int
    faction: str | None  # "alliance" | "horde" | None
    played_s: int | None
    foot_yd: int | None
    travel_yd: int | None
    real_steps: int | None
    real_m: int | None
    real_kcal: int | None
    real_s: int | None
    walks: int | None
    since: int | None
    estimated: bool  # the real-world totals include numbers the companion has not confirmed
    seen_at: float
    stats: Mapping[Stat, int]  # what the game sent, as it was sent (this is what is saved)

    @property
    def id(self) -> str:
        """The key as the dashboard names it."""
        return f"{self.key:08x}"

    @classmethod
    def from_stats(cls, key: int, stats: Mapping[Stat, int], seen_at: float) -> Journey:
        """Build a journey from one round of stat values, checking each."""
        get = stats.get
        info = unpack_char_info(get(Stat.CHAR_INFO) or 0)
        if (get(Stat.CHAR_INFO) or 0) > _KEY_MAX:  # more than the four bytes it is made of
            info = unpack_char_info(0)
        level = info.level or None
        max_level = info.max_level or None
        if level is not None and max_level is not None and level > max_level:
            max_level = None  # "70 / 60" helps nobody
        since = _at_most(get(Stat.SINCE), int(seen_at + SINCE_SLACK_S))
        if since is not None and since < SINCE_MIN:
            since = None
        try:
            name = name_from_chunks([get(stat) or 0 for stat in NAME_STATS])
        except ValueError:
            name = ""
        return cls(
            key=key,
            name=clean_name(name),
            level=level,
            max_level=max_level,
            class_id=info.class_id,
            faction=_FACTIONS.get(info.faction),
            played_s=_at_most(get(Stat.PLAYED_S), MAX_PLAYED_S) or None,  # 0 = the game has not said yet
            foot_yd=_at_most(get(Stat.FOOT_YD), MAX_YARDS),
            travel_yd=_at_most(get(Stat.TRAVEL_YD), MAX_YARDS),
            real_steps=_at_most(get(Stat.REAL_STEPS), MAX_REAL_STEPS),
            real_m=_at_most(get(Stat.REAL_M), MAX_REAL_M),
            real_kcal=_at_most(get(Stat.REAL_KCAL), MAX_REAL_KCAL),
            real_s=_at_most(get(Stat.REAL_S), MAX_REAL_S),
            walks=_at_most(get(Stat.WALKS), MAX_WALKS),
            since=since,
            estimated=bool((get(Stat.INFO_FLAGS) or 0) & InfoFlags.ESTIMATED),
            seen_at=float(seen_at),
            stats=dict(stats),
        )

    # ---- the cache file ----------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "seen_at": round(self.seen_at, 1),
            "stats": {stat.name: value for stat, value in self.stats.items()},
        }

    @classmethod
    def from_json(cls, data: Any) -> Journey | None:
        """Read one entry of the cache file; None if it is not usable.

        The file could have been edited or damaged, so it goes through the
        same checks as numbers arriving from the game.
        """
        if not isinstance(data, dict) or not isinstance(data.get("stats"), dict):
            return None
        key, seen_at = data.get("key"), data.get("seen_at")
        if _at_most(key, _KEY_MAX) is None:
            return None
        if isinstance(seen_at, bool) or not isinstance(seen_at, (int, float)) or not 0 < seen_at < 1e11:
            return None
        stats: dict[Stat, int] = {}
        for name, value in data["stats"].items():
            stat = Stat.__members__.get(name) if isinstance(name, str) else None
            if stat is not None and stat is not Stat.CHAR_KEY and _at_most(value, STAT_MAX_VALUE) is not None:
                stats[stat] = value
        return cls.from_stats(key, stats, float(seen_at))

    # ---- the dashboard -----------------------------------------------------------
    def to_api(self, now: float) -> dict[str, Any]:
        foot = self.foot_yd
        return {
            "id": self.id,
            "name": self.name,
            "level": self.level,
            "max_level": self.max_level,
            "class_id": self.class_id,
            "faction": self.faction,
            "played_s": self.played_s,
            "foot_yd": foot,
            "travel_yd": self.travel_yd,
            "character_steps": None if foot is None else character_steps(foot),
            "character_miles": None if foot is None else round(foot / YARDS_PER_MILE, 2),
            "steps": self.real_steps,
            "distance_m": self.real_m,
            "kcal": self.real_kcal,
            "seconds": self.real_s,
            "walks": self.walks,
            "estimated": self.estimated,
            "since": self.since,
            "seen_at": self.seen_at,
            "live": 0 <= now - self.seen_at <= LIVE_FOR_S,
        }


class JourneyCollector:
    """Puts the stat frames of one character back together.

    The addon sends the stats round and round, CHAR_KEY first. A round is
    everything between two CHAR_KEY frames, and it is passed on (``publish``)
    only when

    * the CHAR_KEY that closes it is the one that opened it,
    * every other stat was seen in between, and
    * the strip never went away for long while it was open.

    That is what keeps two characters apart. The companion sees the strip
    through a screen capture: frames arrive twice, and now and then one is
    missed - also the CHAR_KEY of a character who has just logged in. If
    numbers were taken as they come, that character's numbers would be filed
    under the one before. With these rules a round that could hold two
    characters' numbers is thrown away, and the next one is waited for; the
    price is that fresh numbers are about eight seconds old.

    ``on_stat`` runs on the thread that reads the screen: it only fills a
    small table.
    """

    def __init__(
        self,
        publish: Callable[[Journey], None],
        *,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publish = publish
        self._wall = wall
        self._mono = mono
        self._lock = threading.Lock()
        self._key: int | None = None  # the character of the round that is open
        self._seen: dict[Stat, int] = {}
        self._opened_at = 0.0
        self._news_at = 0.0  # when the strip last showed something new
        self._last: tuple[int, int] | None = None

    def on_stat(self, stat_id: int, value: int) -> None:
        """Take one stat frame's content (``BeaconSource.on_stat``)."""
        try:
            stat = Stat(stat_id)
        except ValueError:
            return  # a stat this build does not know
        if _at_most(value, STAT_MAX_VALUE) is None:
            return
        now = self._mono()
        with self._lock:
            done = self._take(stat, value, now)
        if done is not None:
            self._publish(done)

    def _take(self, stat: Stat, value: int, now: float) -> Journey | None:
        if (stat, value) == self._last:
            # The same frame again: captured twice, or a frozen picture. It is
            # not news, so a picture that stays frozen runs into the gap rule.
            return None
        if self._key is not None and now - self._news_at > GAP_MAX_S:
            self._key = None  # the strip was gone: whose numbers follow is not known
        self._last = (stat, value)
        self._news_at = now
        if stat is not Stat.CHAR_KEY:
            if self._key is not None:
                self._seen[stat] = value
            return None
        done = self._close(value, now)
        self._key = value if value <= _KEY_MAX else None
        self._seen = {}
        self._opened_at = now
        return done

    def _close(self, key: int, now: float) -> Journey | None:
        """The round that ``key`` ends, if it is whole and all one character's."""
        if self._key is None or key != self._key:
            return None
        if now - self._opened_at > ROUND_MAX_S or not _ROUND_STATS <= self._seen.keys():
            return None
        return Journey.from_stats(key, self._seen, self._wall())


class JourneyBook:
    """The latest journey of every character seen, remembered across restarts.

    ``path`` None keeps everything in memory only (the demo, tests).
    """

    def __init__(
        self,
        path: Path | None,
        *,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        limit: int = MAX_CHARACTERS,
    ) -> None:
        self.path = path
        self.limit = max(1, limit)
        self.demo = False  # the numbers are made up (--demo): the page says so
        self._wall = wall
        self._lock = threading.Lock()
        self._journeys: dict[int, Journey] = {}
        self._rev = 0  # goes up whenever the content changes
        self._dirty = False
        self._writing = threading.Lock()  # one writer at a time (the saver, and the save on exit)
        self.collector = JourneyCollector(self.add, wall=wall, mono=mono)
        self._load()

    # ---- content -------------------------------------------------------------------
    def add(self, journey: Journey) -> None:
        """Remember ``journey`` as the latest of its character."""
        with self._lock:
            self._journeys[journey.key] = journey
            self._trim()
            self._rev += 1
            self._dirty = True

    def _trim(self) -> None:
        """Forget the characters seen longest ago, down to the limit."""
        extra = len(self._journeys) - self.limit
        if extra > 0:
            for journey in sorted(self._journeys.values(), key=lambda j: j.seen_at)[:extra]:
                del self._journeys[journey.key]

    def journeys(self) -> list[Journey]:
        """Every character known, the one seen last first."""
        with self._lock:
            return sorted(self._journeys.values(), key=lambda j: j.seen_at, reverse=True)

    def use_memory_only(self, *, demo: bool = False) -> None:
        """Forget what was loaded and never write: for ``--demo``, whose
        made-up character must not turn up among the real ones later."""
        with self._lock:
            self.path = None
            self.demo = demo
            self._journeys.clear()
            self._rev += 1
            self._dirty = False

    # ---- the dashboard -------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """The small part that travels in every state snapshot: enough for the
        page to know when to fetch ``to_api`` again."""
        with self._lock:
            return {"rev": self._rev, "characters": len(self._journeys), "demo": self.demo}

    def to_api(self) -> dict[str, Any]:
        now = self._wall()
        with self._lock:
            rev, demo = self._rev, self.demo
            journeys = sorted(self._journeys.values(), key=lambda j: j.seen_at, reverse=True)
        characters = [journey.to_api(now) for journey in journeys]
        current = next((entry["id"] for entry in characters if entry["live"]), None)
        return {
            "rev": rev,
            "demo": demo,
            "current": current,
            "characters": characters,
            "steps_per_mile": STEPS_PER_MILE,
        }

    # ---- the cache file ------------------------------------------------------------
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s) - starting without remembered characters", self.path, exc)
            return
        entries = data.get("characters") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            journey = Journey.from_json(entry)
            if journey is not None:
                self._journeys[journey.key] = journey
        self._trim()

    def save(self) -> bool:
        """Write the cache if it changed since the last save. True if written."""
        with self._lock:
            path = self.path
            if path is None or not self._dirty:
                return False
            self._dirty = False
            journeys = sorted(self._journeys.values(), key=lambda j: j.seen_at, reverse=True)
        data = {"version": FILE_VERSION, "characters": [journey.to_json() for journey in journeys]}
        try:
            with self._writing:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
                os.replace(tmp, path)
        except OSError as exc:
            log.debug("could not save %s: %s", path, exc)
            with self._lock:
                self._dirty = True  # try again next time
            return False
        return True

    async def run(self, every_s: float = SAVE_EVERY_S) -> None:
        """Save now and then, for as long as the companion runs.

        New numbers arrive every eight seconds on the screen-reader thread.
        Writing the file is kept off that thread, and off the event loop
        that records the walk: a slow disk must not hold up either.
        """
        while True:
            await asyncio.sleep(every_s)
            await asyncio.to_thread(self.save)


def attach(data_dir: Path, beacon: Any, **clocks: Callable[[], float]) -> JourneyBook:
    """The app's journey book, fed by ``beacon``'s stat frames (if there is a beacon)."""
    book = JourneyBook(Path(data_dir) / JOURNEY_FILE, **clocks)
    if beacon is not None:
        beacon.on_stat = book.collector.on_stat
    return book


# ---- the demo ----------------------------------------------------------------------------------

DEMO_NAME = "Maerwyn"
DEMO_KEY = f"{DEMO_NAME}-Walka Demo"  # its "Name-Realm"


class DemoJourney:
    """``--demo``: a made-up character, sent the way the addon sends a real one.

    The scripted demo asks for a frame on every redraw; like the addon, every
    STAT_EVERY-th redraw gets a stat frame, going round STAT_CYCLE. The
    numbers are those of somebody three weeks into a slow walk to the level
    cap, and they move while the demo runs.
    """

    def __init__(self, wall: Callable[[], float] = time.time) -> None:
        self._started = wall()
        self._redraws = 0
        self._sent = 0
        self._key = djb2(DEMO_KEY)
        self._name = name_to_chunks(DEMO_NAME)

    def frame(self, seq: int, elapsed_s: float) -> StatFrame | None:
        """The stat frame for this redraw, or None when it is a live frame's turn."""
        self._redraws += 1
        if self._redraws % STAT_EVERY:
            return None
        stat = STAT_CYCLE[self._sent % len(STAT_CYCLE)]
        self._sent += 1
        return StatFrame(seq=seq % 256, stat_id=stat, value=self.value(stat, elapsed_s))

    def value(self, stat: Stat, elapsed_s: float) -> int:
        t = max(0.0, elapsed_s)
        if stat in NAME_STATS:
            return self._name[NAME_STATS.index(stat)]
        values = {
            Stat.CHAR_KEY: self._key,
            Stat.CHAR_INFO: pack_char_info(23, 60, 3, Faction.ALLIANCE),
            Stat.PLAYED_S: 31 * 3600 + 12 * 60 + t,
            Stat.FOOT_YD: 314_600 + 3.5 * t,  # on foot about half of the time
            Stat.TRAVEL_YD: 671_300 + 8.0 * t,
            Stat.REAL_STEPS: 131_240 + 1.7 * t,
            Stat.REAL_M: 70_310 + 0.85 * t,
            Stat.REAL_KCAL: 5_460 + 0.07 * t,
            Stat.REAL_S: 78_600 + t,
            Stat.WALKS: 19,
            Stat.SINCE: self._started - 22 * 86400,
            Stat.INFO_FLAGS: 0,
        }
        return int(values[stat])
