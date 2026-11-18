"""The road a character has travelled, for the picture on the Share page.

The addon records where the character goes (``Walka/Core/Road.lua``). That is
far too much for the strip on the screen, so it reaches the companion the
slow way: the game writes the addon's saved variables to a file when the
player logs out or reloads the interface, and this module reads that file -
``WTF/Account/<account>/<realm>/<character>/SavedVariables/Walka.lua`` under
the game's folder, which holds one table, ``WalkaCharDB``.

* ``decode`` - that table, checked, as a ``Road``.
* ``Road.to_api`` - the road thinned to a few thousand points and grouped
  the way the picture draws it.
* ``RoadBook`` - finds the files, reads each one again only when the game
  has rewritten it, and answers "the road of this character".

The files belong to the game: they are only ever read, in one go, and never
held open. Nothing read here leaves the computer, and neither the account's
folder name nor the character's realm is passed on or logged. Nothing here
can reach the belt.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import savedvars
from .protocol import djb2

log = logging.getLogger(__name__)

ROAD_VARIABLE = "WalkaCharDB"
SAVED_FILE = "Walka.lua"
VERSION = 1

MAX_FILE_BYTES = 32 * 1024 * 1024  # a road from level 1 to 60 is about one megabyte
MAX_POINTS = 1_000_000
MAX_FILES = 200  # saved files looked at (one per character of every account)
POINT_BUDGET = 6000  # points handed to the page: plenty for a picture 1080 pixels wide
WORLD_LIMIT_YD = 1_000_000  # no place in the game is further from the middle of its map

# The game writes these files when the player logs out or reloads. One that
# changed a moment ago may still be being written.
SETTLE_S = 2.0
RESCAN_S = 3.0  # how often the folders are looked at, at most
GAME_DIRS_S = 60.0  # ... and how often the game is looked for

MODES = frozenset("wrtg")  # on foot, mounted, flight path, ghost
# The two continents every character starts on (world map ids of this client).
CONTINENT_NAMES = {0: "Eastern Kingdoms", 1: "Kalimdor"}

_STEP = re.compile(r"-?\d+,(-?\d+),(-?\d+)")


def character_id(key: str) -> str:
    """The id the dashboard knows a character by: the hash the addon sends
    as CHAR_KEY (``journey.Journey.id``), made from its "Name-Realm"."""
    return f"{djb2(key):08x}"


def _whole(value: Any, limit: float) -> int | None:
    """``value`` as a whole number within ``limit`` either way, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or abs(value) > limit:
        return None
    return int(round(value))


@dataclass(frozen=True)
class Stretch:
    """One unbroken piece of road, travelled the same way."""

    continent: int
    mode: str  # "w" | "r" | "t" | "g"
    belt: bool  # the treadmill belt was moving
    level: int
    points: tuple[tuple[int, int], ...]  # world yards: x to the north, y to the west

    @property
    def style(self) -> str:
        if self.mode == "t":
            return "flight"
        return "walked" if self.belt else "road"

    @property
    def yards(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:], strict=False))


@dataclass(frozen=True)
class Road:
    key: str  # "Name-Realm": used for the id only, never passed on
    stretches: tuple[Stretch, ...]
    truncated: bool = False  # more points than this reader takes

    @property
    def id(self) -> str:
        return character_id(self.key)

    def to_api(self, as_of: float, budget: int = POINT_BUDGET) -> dict[str, Any]:
        """The road as the page draws it."""
        ground = belt = flight = 0.0
        per_continent: dict[int, float] = {}
        for stretch in self.stretches:
            yards = stretch.yards
            if stretch.mode == "t":
                flight += yards
                continue
            ground += yards
            per_continent[stretch.continent] = per_continent.get(stretch.continent, 0.0) + yards
            if stretch.belt:
                belt += yards

        paths = _thin(_join(self.stretches), budget)
        continents: dict[int, dict[str, Any]] = {}
        for continent, style, points in paths:
            entry = continents.get(continent)
            if entry is None:
                entry = continents[continent] = {
                    "id": continent,
                    "name": CONTINENT_NAMES.get(continent, ""),
                    "yards": round(per_continent.get(continent, 0.0)),
                    "bounds": [math.inf, math.inf, -math.inf, -math.inf],
                    "paths": [],
                }
            bounds = entry["bounds"]
            flat: list[int] = []
            for x, y in points:
                flat += (x, y)
                bounds[0], bounds[1] = min(bounds[0], x), min(bounds[1], y)
                bounds[2], bounds[3] = max(bounds[2], x), max(bounds[3], y)
            entry["paths"].append({"style": style, "p": flat})

        first, last = self.stretches[0], self.stretches[-1]
        return {
            "id": self.id,
            "as_of": as_of,
            "yards": round(ground),
            "belt_yards": round(belt),
            "flight_yards": round(flight),
            "level": last.level,
            "start": _place(first.continent, first.points[0]),
            "end": _place(last.continent, last.points[-1]),
            "milestones": _milestones(self.stretches),
            "crossings": _crossings(self.stretches),
            # Where most of the road is comes first.
            "continents": sorted(continents.values(), key=lambda entry: entry["yards"], reverse=True),
            "truncated": self.truncated,
        }


def _place(continent: int, point: tuple[int, int]) -> dict[str, int]:
    return {"c": continent, "x": point[0], "y": point[1]}


def _milestones(stretches: Iterable[Stretch]) -> list[dict[str, int]]:
    """Where every tenth level was reached - on the recorded road, so a
    character that was level 23 when the recording began has no "10"."""
    marks: list[dict[str, int]] = []
    reached: int | None = None
    for stretch in stretches:
        tens = stretch.level // 10
        if reached is None:
            reached = tens
        while reached < tens:
            reached += 1
            marks.append({"level": reached * 10, **_place(stretch.continent, stretch.points[0])})
    return marks


def _crossings(stretches: tuple[Stretch, ...], most: int = 3) -> list[dict[str, dict[str, int]]]:
    """Where the road goes from one continent to another (a boat, a
    zeppelin, a portal): from where to where. The same passage made ten
    times is one crossing; the ones made most often come first, and only
    ``most`` are given - the picture draws an arc for each."""
    seen: dict[tuple[Any, ...], list[Any]] = {}
    for before, after in zip(stretches, stretches[1:], strict=False):
        if before.continent == after.continent:
            continue
        ends = (_place(before.continent, before.points[-1]), _place(after.continent, after.points[0]))
        # (Either way round, and to the nearest quarter mile: the same boat.)
        key = tuple(sorted((end["c"], round(end["x"] / 440), round(end["y"] / 440)) for end in ends))
        entry = seen.setdefault(key, [0, ends])
        entry[0] += 1
    often = sorted(seen.values(), key=lambda entry: entry[0], reverse=True)[:most]
    return [{"from": ends[0], "to": ends[1]} for _count, ends in often]


def decode(table: Any) -> Road | None:
    """``WalkaCharDB`` as the game saved it -> a ``Road``; None if it holds
    no road (or is not what the addon writes). Whatever is wrong with one
    stretch costs that stretch, not the road."""
    if not isinstance(table, dict) or table.get("version") != VERSION:
        return None
    key = table.get("key")
    if not isinstance(key, str) or not key or len(key) > 200:
        return None
    stretches: list[Stretch] = []
    total = 0
    truncated = False
    for segment in savedvars.sequence(table.get("segments")):
        if not isinstance(segment, dict):
            continue
        continent = _whole(segment.get("c"), 10**6)
        x, y = _whole(segment.get("x"), WORLD_LIMIT_YD), _whole(segment.get("y"), WORLD_LIMIT_YD)
        mode = segment.get("m")
        if continent is None or x is None or y is None or mode not in MODES:
            continue
        points = [(x, y)]
        steps = segment.get("d")
        if isinstance(steps, str):
            for dx, dy in _STEP.findall(steps):
                x, y = x + int(dx), y + int(dy)
                points.append((x, y))
        if len(points) < 2 or any(abs(px) > WORLD_LIMIT_YD or abs(py) > WORLD_LIMIT_YD for px, py in points):
            continue
        if total + len(points) > MAX_POINTS:
            truncated = True
            break
        total += len(points)
        level = _whole(segment.get("l"), 1000) or 0
        stretches.append(Stretch(continent, mode, segment.get("b") == 1, max(0, level), tuple(points)))
    if not stretches:
        return None
    return Road(key, tuple(stretches), truncated)


# ---- fewer points -------------------------------------------------------------------------------


def _join(stretches: Iterable[Stretch]) -> list[tuple[int, str, list[tuple[int, int]]]]:
    """Stretches that follow one another without a gap and are drawn alike
    (a new one starts at every level, for one thing) as one line."""
    paths: list[tuple[int, str, list[tuple[int, int]]]] = []
    for stretch in stretches:
        if paths:
            continent, style, points = paths[-1]
            if continent == stretch.continent and style == stretch.style and points[-1] == stretch.points[0]:
                points.extend(stretch.points[1:])
                continue
        paths.append((stretch.continent, stretch.style, list(stretch.points)))
    return paths


def simplify(points: list[tuple[int, int]], tolerance: float) -> list[tuple[int, int]]:
    """The points needed to draw the line to within ``tolerance`` yards.
    The first and the last are always kept."""
    if len(points) <= 2 or tolerance <= 0:
        return points
    # First the cheap part: points that sit almost on top of the last one kept.
    near = (tolerance / 2) ** 2
    spaced = [points[0]]
    last_x, last_y = points[0]
    for x, y in points[1:-1]:
        if (x - last_x) ** 2 + (y - last_y) ** 2 >= near:
            spaced.append((x, y))
            last_x, last_y = x, y
    spaced.append(points[-1])
    points = spaced
    count = len(points)
    if count <= 2:
        return points
    # Then Ramer-Douglas-Peucker, without recursion.
    keep = bytearray(count)
    keep[0] = keep[-1] = 1
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, index = tolerance, -1
        for i in range(first + 1, last):
            px, py = points[i]
            off = abs(dx * (py - ay) - dy * (px - ax)) / length if length else math.hypot(px - ax, py - ay)
            if off > worst:
                worst, index = off, i
        if index >= 0:
            keep[index] = 1
            stack.append((first, index))
            stack.append((index, last))
    return [point for point, kept in zip(points, keep, strict=True) if kept]


def _thin(
    paths: list[tuple[int, str, list[tuple[int, int]]]], budget: int
) -> list[tuple[int, str, list[tuple[int, int]]]]:
    """Thin all paths alike until together they fit the budget."""
    xs = [x for _c, _s, points in paths for x, _y in points]
    ys = [y for _c, _s, points in paths for _x, y in points]
    if not xs:
        return paths
    # Start at about a pixel of a picture that shows the whole road.
    tolerance = max(1.0, max(max(xs) - min(xs), max(ys) - min(ys)) / 1500)
    while True:
        paths = [(continent, style, simplify(points, tolerance)) for continent, style, points in paths]
        if sum(len(points) for _c, _s, points in paths) <= budget or tolerance > WORLD_LIMIT_YD:
            return paths
        tolerance *= 1.6


# ---- the files ----------------------------------------------------------------------------------


def read_shared(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    """All of ``path`` (at most ``limit`` + 1 bytes), without getting in the
    game's way.

    On Windows a file that is open the ordinary way cannot be replaced by
    another program meanwhile - and replacing the file is how a program
    saves safely. So there it is opened with every kind of sharing allowed,
    as a backup tool would.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        generic_read, share_all, open_existing, normal = 0x80000000, 0x1 | 0x2 | 0x4, 3, 0x80
        handle = kernel32.CreateFileW(str(path), generic_read, share_all, None, open_existing, normal, None)
        if handle is None or handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # (Binary: a descriptor made this way is a text one otherwise,
            # which ends at the first Ctrl-Z byte.)
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "rb") as file:
            return file.read(limit + 1)
    with open(path, "rb") as file:
        return file.read(limit + 1)


def saved_files(game_dir: Path) -> list[Path]:
    """The addon's per-character saved files under one game folder."""
    accounts = Path(game_dir) / "WTF" / "Account"
    try:
        # <account>/<realm>/<character>/SavedVariables/Walka.lua - the names
        # of the two folders in between differ from client to client, so
        # they are not relied on: the file says whose it is.
        return sorted(accounts.glob(f"*/*/*/SavedVariables/{SAVED_FILE}"))
    except OSError:
        return []


@dataclass
class _File:
    stamp: tuple[int, int] | None = None  # (size, mtime in ns) of what was last read
    waiting: tuple[int, int] | None = None  # ... of what looked too fresh to read
    waiting_since: float = 0.0
    mtime: float = 0.0
    id: str = ""  # whose road it is
    api: dict[str, Any] | None = None


@dataclass
class RoadBook:
    """The roads found in the game's saved files, each read again only when
    its file has changed. ``game_dirs`` says where games are installed
    (folders that hold ``WTF``); it is asked now and then, on the thread
    that asks for a road - never on the one that drives the belt."""

    game_dirs: Callable[[], Iterable[Path]]
    wall: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    _files: dict[Path, _File] = field(default_factory=dict)
    _dirs: list[Path] = field(default_factory=list)
    _dirs_at: float = -math.inf
    _scanned_at: float = -math.inf
    _demo: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def use_demo(self, road: Road) -> None:
        """``--demo``: a made-up road for the made-up character."""
        with self._lock:
            self._demo[road.id] = road.to_api(self.wall())

    def for_character(self, wanted: str) -> dict[str, Any]:
        """What the Share page asks: the road of the character with this
        id, and whether there was anywhere to look.

        ``road`` is None if no saved file holds one for that character -
        most often because the game has not written its files since the
        addon began recording (it does when the player logs out or types
        /reload).
        """
        with self._lock:
            if wanted in self._demo:
                return {"road": self._demo[wanted], "game_found": True, "files": 0, "demo": True}
            self._scan()
            mine = [entry for entry in self._files.values() if entry.id == wanted and entry.api is not None]
            newest = max(mine, key=lambda entry: entry.mtime, default=None)
            return {
                "road": newest.api if newest else None,
                "game_found": bool(self._dirs),
                "files": len(self._files),
                "demo": False,
            }

    # ---- looking -------------------------------------------------------------------------------
    def _scan(self) -> None:
        now = self.mono()
        if now - self._scanned_at < RESCAN_S:
            return
        self._scanned_at = now
        if now - self._dirs_at >= GAME_DIRS_S or not self._dirs:
            self._dirs_at = now
            try:
                self._dirs = [Path(folder) for folder in self.game_dirs()]
            except Exception:
                log.debug("could not look for the game", exc_info=True)
                self._dirs = []
        found: list[Path] = []
        for folder in self._dirs:
            found += saved_files(folder)
        found = found[:MAX_FILES]
        for path in list(self._files):
            if path not in found:
                del self._files[path]  # the character was deleted, or the folder is gone
        for path in found:
            self._read(path)

    def _read(self, path: Path) -> None:
        try:
            status = path.stat()
        except OSError:
            return
        stamp = (status.st_size, status.st_mtime_ns)
        entry = self._files.setdefault(path, _File())
        if entry.stamp == stamp:
            return  # read already, whatever came of it
        if self.wall() - status.st_mtime < SETTLE_S:
            # Written a moment ago, so perhaps not finished - or its date is
            # ahead of this computer's clock (another drive, another time
            # zone), which must not hide it for hours: either way it is read
            # once it has stayed as it is for a while.
            now = self.mono()
            if entry.waiting != stamp:
                entry.waiting, entry.waiting_since = stamp, now
                return
            if now - entry.waiting_since < SETTLE_S:
                return
        entry.waiting = None
        problem = ""
        road: Road | None = None
        try:
            data = read_shared(path)
            if path.stat().st_mtime_ns != stamp[1]:
                return  # rewritten while it was read: next time
            if len(data) > MAX_FILE_BYTES:
                raise savedvars.SavedVariablesError("the file is too big")
            road = decode(savedvars.parse(data).get(ROAD_VARIABLE))
        except (OSError, savedvars.SavedVariablesError) as exc:
            problem = f"{type(exc).__name__}: {exc}"
        entry.stamp = stamp  # (a file that cannot be read is not tried again until it changes)
        if problem:
            # The road read from it before stands. (No path here: it holds
            # the name of the player's account.)
            log.debug("a saved file of the addon could not be read (%s)", problem)
            return
        entry.mtime = status.st_mtime
        entry.id = road.id if road else ""
        entry.api = road.to_api(status.st_mtime) if road else None


# ---- the demo -------------------------------------------------------------------------------------


def demo_road(key: str) -> Road:
    """A made-up road for ``--demo``: a night elf's first twenty-three
    levels, through places that exist - down Teldrassil and Darkshore into
    Ashenvale, a flight back, then (after the boat) from Menethil Harbor
    over Loch Modan to Ironforge, and from Stormwind to Lakeshire. Most of
    it walked on the treadmill, some of it not."""
    stretches: list[Stretch] = []
    at = (0.0, 0.0)
    continent = 1
    level = 1

    def add(points: list[tuple[float, float]], mode: str, belt: bool) -> None:
        whole = [(round(x), round(y)) for x, y in points]
        kept = [whole[0]] + [b for a, b in zip(whole, whole[1:], strict=False) if a != b]
        if len(kept) >= 2:
            stretches.append(Stretch(continent, mode, belt and mode != "t", level, tuple(kept)))

    def start(where: tuple[float, float], on: int | None = None) -> None:
        """Arrive somewhere without a road to it: a portal, a boat, the tram."""
        nonlocal at, continent
        at = where
        continent = continent if on is None else on

    def go(to: tuple[float, float], mode: str = "w", belt: bool = True) -> None:
        """A stretch to ``to``: a road that sways a little, or a flight (straight)."""
        nonlocal at
        (ax, ay), (bx, by) = at, to
        length = math.dist(at, to)
        steps = max(1, round(length / (75 if mode == "t" else 20)))
        sway = 0.0 if mode == "t" else min(110.0, 0.06 * length)
        bends = max(1, round(length / 650))
        points = []
        for step in range(steps + 1):
            t = step / steps
            off = sway * math.sin(math.pi * t) * math.sin(math.pi * bends * t + 0.002 * ax)
            points.append(
                (ax + (bx - ax) * t - (by - ay) / length * off, ay + (by - ay) * t + (bx - ax) / length * off)
            )
        add(points, mode, belt)
        at = to

    def quests(radius: float, loops: int = 3, turn: float = 0.0, belt: bool = True) -> None:
        """Errands around a village: a few loops out and back again."""
        steps = max(12, round(loops * 2.5 * radius / 20))
        points = []
        for step in range(steps + 1):
            angle = math.pi * step / steps
            reach = radius * math.sin(loops * angle)
            points.append((at[0] + reach * math.cos(angle + turn), at[1] + reach * math.sin(angle + turn)))
        add(points, "w", belt)

    def reach_level(value: int) -> None:
        nonlocal level
        level = value

    # Kalimdor: Teldrassil ...
    start((10311, 832), on=1)  # Shadowglen
    quests(210, turn=0.4)
    reach_level(4)
    go((9850, 960))  # Dolanaar
    reach_level(6)
    quests(300, turn=1.9)
    go((9690, 1560))
    reach_level(8)
    quests(190, loops=2, turn=0.8, belt=False)
    go((9950, 2280), belt=False)  # Darnassus
    # ... the portal down to Rut'theran, and the boat across.
    start((8640, 880))
    go((6460, 800), belt=False)
    # Darkshore.
    go((6440, 614))  # Auberdine
    reach_level(10)
    quests(260, turn=2.6)
    go((5700, 250))
    reach_level(12)
    quests(230, loops=2, turn=0.3)
    go((4980, 130))
    reach_level(14)
    go((4400, 430), belt=False)
    reach_level(16)
    quests(200, loops=2, turn=1.2)
    # Ashenvale.
    go((3350, 250))
    reach_level(18)
    go((2745, -372))  # Astranaar
    reach_level(19)
    quests(280, turn=0.9)
    go((6440, 614), mode="t")  # the hippogryph back to Auberdine

    # The Eastern Kingdoms, off the boat at Menethil Harbor.
    start((-3740, -830), on=0)
    reach_level(20)
    quests(240, turn=2.2)
    go((-3330, -1650))
    go((-3150, -2450))
    go((-4050, -2700), belt=False)  # Dun Algaz
    go((-4780, -2700))
    reach_level(21)
    go((-5349, -2975))  # Thelsamar
    quests(260, turn=0.2)
    go((-5800, -2580))
    go((-5650, -1900), mode="r")
    go((-5700, -1450), mode="r")
    reach_level(22)
    go((-5582, -483))  # Kharanos
    go((-5050, -800), belt=False)  # up to Ironforge
    # ... the tram, and out of Stormwind.
    start((-8913, 554))
    go((-9464, 62))  # Goldshire
    go((-9450, -1300))
    go((-9600, -1900), belt=False)
    reach_level(23)
    go((-9282, -2269))  # Lakeshire
    quests(230, loops=2, turn=1.6)
    return Road(key, tuple(stretches))
