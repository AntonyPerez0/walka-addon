"""A character's journey: the numbers behind the picture on the Share page.

The addon keeps, for every character, its level and time played, how far it
has travelled on foot, and the real-world walking done while playing it, in
its saved variables (``WalkaDB.journeys``). The game writes those to a file
when the player logs out or reloads the interface, and ``game_file`` reads
that file. This module turns one character's record into numbers that can be
shown:

* ``Journey`` - one character's numbers, checked: a number that cannot be
  true is left out, and the name is treated as text from a stranger.
* ``JourneyBook`` - the latest journey of every character seen, kept on disk
  next to the companion's other state so a picture can be made when the
  game's files are out of reach.
* ``demo_journey`` - a made-up character for ``--demo``.

Of a character's "Name-Realm" only the name is kept and shown; the whole of
it is used to tell characters apart (``djb2``), and the realm itself is
never passed on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

JOURNEY_FILE = "journey.json"
# 2: the numbers themselves. (1 held the stat frames read off the screen by
# versions up to 0.10; such a file is not read: the game's saved file has
# every character's numbers anyway.)
FILE_VERSION = 2
# An account holds many characters; the ones played lately are the ones a
# picture is made of.
MAX_CHARACTERS = 30
SAVE_EVERY_S = 15.0

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
MAX_SEEN_AT = 1e11

# The faction as the addon saves it (Journey.FACTION in Core/Journey.lua).
_FACTIONS = {1: "alliance", 2: "horde"}


def djb2(text: str | bytes) -> int:
    """How characters are told apart: djb2 over the UTF-8 bytes of the
    character's "Name-Realm", 32 bits.

    ``h = 5381``, then ``h = (h * 33 + byte) mod 2^32`` for every byte.
    """
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    h = 5381
    for byte in data:
        h = (h * 33 + byte) & 0xFFFFFFFF
    return h


def character_id(key: str) -> str:
    """The id the dashboard knows a character by, made from its "Name-Realm"."""
    return f"{djb2(key):08x}"


def name_of(key: str) -> str:
    """The name alone, of a "Name-Realm" (a name has no dash in it, a realm may)."""
    return clean_name(key.split("-", 1)[0])


def clean_name(text: str) -> str:
    """A character name as it may be shown.

    The name comes from a file another program wrote, so it is text from a
    stranger. Control and formatting characters (line breaks, direction
    overrides, zero-width marks) are removed, runs of blanks become one
    space, and the length is capped. It is still only *text*: whoever shows
    it must not interpret it (the page writes it with ``textContent`` and
    draws it on a canvas).
    """
    kept = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(kept.split())[:NAME_MAX_CHARS]


def character_steps(foot_yd: int) -> int:
    """On-foot yards as steps, by the 0.88-yards-a-step convention."""
    return (foot_yd * STEPS_PER_MILE + YARDS_PER_MILE // 2) // YARDS_PER_MILE


def _count(value: Any, limit: float) -> int | None:
    """``value`` as a whole count if it is a believable one, else None.
    (Counts are never rounded up: a step counts once it is finished.)"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0 <= value <= limit:
        return None
    return int(math.floor(value + 1e-6))


def _byte(value: Any) -> int:
    return _count(value, 255) or 0


def _table(value: Any) -> Mapping[Any, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class Journey:
    """One character's numbers, as of ``seen_at`` (wall-clock seconds: when
    the game saved them).

    None means "not known": the game has not said yet (time played), or what
    was saved could not be true.
    """

    key: int  # tells characters apart (djb2 of "Name-Realm"), means nothing else
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

    @property
    def id(self) -> str:
        """The key as the dashboard names it."""
        return f"{self.key:08x}"

    @classmethod
    def from_record(cls, key: str, record: Any, seen_at: float) -> Journey | None:
        """One record of ``WalkaDB.journeys`` (``key`` is its "Name-Realm"),
        checked number by number. None if it is no record at all.

        The real-world totals are those the addon saved for the app at the
        last logout or /reload (``shared``); a record from before it did
        has only the settled ones (``real``)."""
        if not isinstance(key, str) or not key or key.startswith("?") or not isinstance(record, Mapping):
            return None
        name = name_of(key)
        if not name:
            return None
        shared = _table(record.get("shared"))
        totals = shared if shared else _table(record.get("real"))
        saved_at = shared.get("savedAt")
        if not isinstance(saved_at, bool) and isinstance(saved_at, (int, float)) and 0 < saved_at < MAX_SEEN_AT:
            seen_at = float(saved_at)
        level = _byte(record.get("level")) or None
        max_level = _byte(record.get("maxLevel")) or None
        if level is not None and max_level is not None and level > max_level:
            max_level = None  # "70 / 60" helps nobody
        since = _count(record.get("since"), seen_at + SINCE_SLACK_S)
        if since is not None and since < SINCE_MIN:
            since = None
        played = shared.get("played", record.get("played"))
        return cls(
            key=djb2(key),
            name=name,
            level=level,
            max_level=max_level,
            class_id=_byte(record.get("classId")),
            faction=_FACTIONS.get(_byte(record.get("faction"))),
            played_s=_count(played, MAX_PLAYED_S) or None,  # 0 = the game has not said yet
            foot_yd=_count(record.get("footYd"), MAX_YARDS),
            travel_yd=_count(record.get("travelYd"), MAX_YARDS),
            real_steps=_count(totals.get("steps"), MAX_REAL_STEPS),
            real_m=_count(totals.get("distanceM"), MAX_REAL_M),
            real_kcal=_count(totals.get("kcal"), MAX_REAL_KCAL),
            real_s=_count(totals.get("seconds"), MAX_REAL_S),
            walks=_count(totals.get("walks"), MAX_WALKS),
            since=since,
            estimated=totals.get("estimated") is True,
            seen_at=float(seen_at),
        )

    # ---- the cache file ----------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["seen_at"] = round(self.seen_at, 1)
        return data

    @classmethod
    def from_json(cls, data: Any) -> Journey | None:
        """Read one entry of the cache file; None if it is not usable.

        The file could have been edited or damaged, so every number is
        checked again."""
        if not isinstance(data, dict):
            return None
        key, seen_at, name = data.get("key"), data.get("seen_at"), data.get("name")
        if _count(key, 0xFFFFFFFF) is None or not isinstance(name, str) or not clean_name(name):
            return None
        if isinstance(seen_at, bool) or not isinstance(seen_at, (int, float)) or not 0 < seen_at < MAX_SEEN_AT:
            return None
        limits = {
            "level": 255, "max_level": 255, "played_s": MAX_PLAYED_S, "foot_yd": MAX_YARDS,
            "travel_yd": MAX_YARDS, "real_steps": MAX_REAL_STEPS, "real_m": MAX_REAL_M,
            "real_kcal": MAX_REAL_KCAL, "real_s": MAX_REAL_S, "walks": MAX_WALKS,
            "since": seen_at + SINCE_SLACK_S,
        }  # fmt: skip
        values: dict[str, Any] = {name_: _count(data.get(name_), limit) for name_, limit in limits.items()}
        if values["since"] is not None and values["since"] < SINCE_MIN:
            values["since"] = None
        faction = data.get("faction")
        return cls(
            key=int(key),
            name=clean_name(name),
            class_id=_byte(data.get("class_id")),
            faction=faction if faction in _FACTIONS.values() else None,
            estimated=data.get("estimated") is True,
            seen_at=float(seen_at),
            **values,
        )

    # ---- the dashboard -----------------------------------------------------------
    def to_api(self) -> dict[str, Any]:
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
        }


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
        self._load()

    # ---- content -------------------------------------------------------------------
    def add(self, journey: Journey) -> None:
        """Remember ``journey`` as the latest of its character."""
        self.update([journey])

    def update(self, journeys: list[Journey]) -> bool:
        """Take the journeys read from the game's files: each one that is
        newer than what is known of its character (or differs from it, when
        saved at the same moment) replaces it. True if anything changed."""
        changed = False
        with self._lock:
            for journey in journeys:
                known = self._journeys.get(journey.key)
                if known is not None and (known.seen_at > journey.seen_at or known == journey):
                    continue
                self._journeys[journey.key] = journey
                changed = True
            if changed:
                self._trim()
                self._rev += 1
                self._dirty = True
        return changed

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

    def latest(self) -> dict[str, Any] | None:
        """The character the game saved last - its name and level, and when -
        or ``None`` before any is known."""
        with self._lock:
            journey = max(self._journeys.values(), key=lambda j: j.seen_at, default=None)
        if journey is None:
            return None
        return {
            "name": journey.name,
            "level": journey.level,
            "max_level": journey.max_level,
            "seen_at": journey.seen_at,
        }

    def to_api(self) -> dict[str, Any]:
        with self._lock:
            rev, demo = self._rev, self.demo
            journeys = sorted(self._journeys.values(), key=lambda j: j.seen_at, reverse=True)
        characters = [journey.to_api() for journey in journeys]
        # The character played last is the one a picture is most likely wanted of.
        current = characters[0]["id"] if characters else None
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
        if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
            return  # (an older file: the game's saved file says it all again)
        entries = data.get("characters")
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

        Writing the file is kept off the event loop that records the walk:
        a slow disk must not hold it up.
        """
        while True:
            await asyncio.sleep(every_s)
            await asyncio.to_thread(self.save)


def attach(data_dir: Path, **clocks: Callable[[], float]) -> JourneyBook:
    """The app's journey book, kept in the data folder. ``game_file`` fills it."""
    return JourneyBook(Path(data_dir) / JOURNEY_FILE, **clocks)


# ---- the demo ----------------------------------------------------------------------------------

DEMO_NAME = "Maerwyn"
DEMO_KEY = f"{DEMO_NAME}-Walka Demo"  # its "Name-Realm"


def demo_journey(wall: Callable[[], float] = time.time) -> Journey:
    """``--demo``: a made-up character, somebody three weeks into a slow walk
    to the level cap."""
    now = wall()
    record = {
        "since": now - 22 * 86400,
        "footYd": 314_600,
        "travelYd": 671_300,
        "level": 23,
        "maxLevel": 60,
        "classId": 3,
        "faction": 1,
        "played": 31 * 3600 + 12 * 60,
        "shared": {
            "savedAt": now,
            "steps": 131_240,
            "distanceM": 70_310,
            "kcal": 5_460,
            "seconds": 78_600,
            "walks": 19,
            "estimated": False,
        },
    }
    journey = Journey.from_record(DEMO_KEY, record, now)
    assert journey is not None
    return journey
