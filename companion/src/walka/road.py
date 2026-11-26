"""The road a character has travelled, for the picture on the Share page.

The addon records where the character goes (``Walka/Core/Road.lua``). It
reaches the companion the way everything from the game does: the game
writes the addon's saved variables to a file when the player logs out or
reloads the interface, and this module reads that file -
``WTF/Account/<account>/<realm>/<character>/SavedVariables/Walka.lua`` under
the game's folder, which holds one table, ``WalkaCharDB``.

* ``decode`` - that table, checked, as a ``Road``.
* ``Road.to_api`` - the road thinned to a few thousand points and grouped
  the way the picture draws it. A land the page has no map of (the
  Skyborne's Zephras Isle, in Skywall) gets the name the game gives its
  map, and a sketch of its ground made from the road (``sketch.py``).
* ``RoadBook`` - finds the files, reads each one again only when the game
  has rewritten it, and answers "the road of this character".

The files belong to the game: they are only ever read, in one go, and never
held open. Nothing read here leaves the computer, and neither the account's
folder name nor the character's realm is passed on or logged.
"""

from __future__ import annotations

import bisect
import logging
import math
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import savedvars, sketch
from .journey import djb2

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
# The two continents the page has a map of (static/lands.js), by the game's
# continent ids. Any other land is named by the game (the addon saves the
# name of its map with the road) and drawn as far as the road has charted it.
CONTINENT_NAMES = {0: "Eastern Kingdoms", 1: "Kalimdor"}
MAX_NAME_CHARS = 40  # of a land's name, as the game gave it
MAX_LANDS = 64  # lands whose names are taken from one file

# (Each part at most nine digits: a step of more is no step, and Python
# makes no int of more than 4,300 digits.)
_STEP = re.compile(r"(?<![\d-])(-?\d{1,9}),(-?\d{1,9}),(-?\d{1,9})(?![\d])")
TIME_LIMIT_S = 10**11  # no saved time is this far from 1970

# The walks the app measured, as spans of time the belt was moving: a
# sample of a walk's speed series stands for at most this long.
SAMPLE_SPAN_S = 10.0
MOVING_KMH = 0.05


def character_id(key: str) -> str:
    """The id the dashboard knows a character by (``journey.Journey.id``),
    made from its "Name-Realm"."""
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
    belt: bool  # the treadmill belt was moving (as the app measured it: see walked_spans)
    level: int
    points: tuple[tuple[int, int], ...]  # world yards: x to the north, y to the west
    # When the character was at each point (epoch seconds), or () if the
    # saved road did not say. (Not part of what the road is.)
    times: tuple[int, ...] = field(default=(), compare=False)

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
    names: dict[int, str] = field(default_factory=dict)  # continent id -> what the game calls that land

    @property
    def id(self) -> str:
        return character_id(self.key)

    def to_api(
        self, as_of: float, budget: int = POINT_BUDGET, walked: Sequence[tuple[float, float]] = ()
    ) -> dict[str, Any]:
        """The road as the page draws it. ``walked``: when the belt was
        moving, as the app measured it (``walked_spans``) - the road the
        character travelled on foot or mounted then is "walked on the
        treadmill", and no other."""
        stretches = on_the_belt(self.stretches, walked)
        ground = belt = flight = 0.0
        per_continent: dict[int, float] = {}
        for stretch in stretches:
            yards = stretch.yards
            if stretch.mode == "t":
                flight += yards
                continue
            ground += yards
            per_continent[stretch.continent] = per_continent.get(stretch.continent, 0.0) + yards
            if stretch.belt:
                belt += yards

        paths = _thin(_join(stretches), budget)
        continents: dict[int, dict[str, Any]] = {}
        for continent, style, points in paths:
            entry = continents.get(continent)
            if entry is None:
                entry = continents[continent] = {
                    "id": continent,
                    "name": CONTINENT_NAMES.get(continent) or self.names.get(continent, ""),
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
        for continent, entry in continents.items():
            if continent in CONTINENT_NAMES:
                continue
            # A land the page has no map of: its ground as far as the road
            # has charted it (where the character went on foot or mounted;
            # a flight says nothing about what lies under it).
            walked = [points for land, style, points in paths if land == continent and style != "flight"]
            flown = [points for land, style, points in paths if land == continent]
            drawn = sketch.land(walked or flown)
            if drawn:
                entry["land"] = drawn

        first, last = stretches[0], stretches[-1]
        return {
            "id": self.id,
            "as_of": as_of,
            "yards": round(ground),
            "belt_yards": round(belt),
            "flight_yards": round(flight),
            "level": last.level,
            "start": _place(first.continent, first.points[0]),
            "end": _place(last.continent, last.points[-1]),
            "milestones": _milestones(stretches),
            "crossings": _crossings(stretches),
            # Where most of the road is comes first.
            "continents": sorted(continents.values(), key=lambda entry: entry["yards"], reverse=True),
            "truncated": self.truncated,
        }


def walked_spans(walks: Iterable[Any]) -> list[tuple[float, float]]:
    """When the treadmill's belt was moving, from the walks the app
    measured (``session.SessionRecord``: ``start``, ``seconds`` and the
    speed series ``samples``, one ``[offset, km/h, bpm]`` every few
    seconds). Sorted spans of epoch seconds that do not overlap. A walk
    without a series counts from its start for the time the belt moved.
    Whatever is damaged in a walk is left out: this never claims more."""
    spans: list[tuple[float, float]] = []
    for walk in walks:
        start = getattr(walk, "start", None)
        if not _finite(start):
            continue
        samples = [
            (sample[0], sample[1])
            for sample in (getattr(walk, "samples", None) or [])
            if isinstance(sample, (list, tuple)) and len(sample) >= 2 and _finite(sample[0]) and _finite(sample[1])
        ]
        if not samples:
            seconds = getattr(walk, "seconds", None)
            if _finite(seconds) and seconds > 0:
                spans.append((float(start), float(start + seconds)))
            continue
        end = getattr(walk, "end", None)
        end_offset = end - start if _finite(end) else math.inf
        for index, (offset, kmh) in enumerate(samples):
            if kmh <= MOVING_KMH or offset < 0:
                continue
            until = samples[index + 1][0] if index + 1 < len(samples) else offset + SAMPLE_SPAN_S / 2
            until = min(until, offset + SAMPLE_SPAN_S, end_offset)
            if until > offset:
                spans.append((float(start + offset), float(start + until)))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for begin, finish in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
        else:
            merged.append((begin, finish))
    return merged


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def on_the_belt(stretches: Sequence[Stretch], walked: Sequence[tuple[float, float]]) -> list[Stretch]:
    """The stretches, marked walked on the treadmill where the character
    went on foot or mounted while the belt was moving (``walked``: sorted
    spans that do not overlap) and nowhere else. A stretch the belt
    started or stopped on is cut there (the point in between is where the
    character was then, along the line). A stretch whose times are not
    known keeps the mark it has (only the demo's have one)."""
    edges = [moment for span in walked for moment in span]  # start, end, start, end, ...
    out: list[Stretch] = []
    for stretch in stretches:
        times = stretch.times
        if not times or len(times) != len(stretch.points):
            out.append(stretch)
        elif stretch.mode == "t" or not edges or times[-1] < edges[0] or times[0] > edges[-1]:
            out.append(replace(stretch, belt=False))
        else:
            out += _cut(stretch, edges)
    return out


def _moving(edges: Sequence[float], moment: float) -> bool:
    return bisect.bisect_right(edges, moment) % 2 == 1


def _cut(stretch: Stretch, edges: Sequence[float]) -> list[Stretch]:
    """``stretch`` in pieces on and off the belt (``edges``: the starts and
    ends of the spans it was moving, in order)."""
    times = stretch.times
    pieces: list[tuple[bool, list[tuple[int, int]], list[int]]] = []
    for index in range(1, len(times)):
        (ax, ay), (bx, by) = stretch.points[index - 1], stretch.points[index]
        begin, finish = times[index - 1], times[index]
        if finish <= begin:
            steps = [((bx, by), finish, _moving(edges, finish))]
        else:
            cuts = edges[bisect.bisect_right(edges, begin) : bisect.bisect_left(edges, finish)]
            moments = [begin, *cuts, finish]
            steps = []
            for low, high in zip(moments, moments[1:], strict=False):
                share = (high - begin) / (finish - begin)
                point = (round(ax + (bx - ax) * share), round(ay + (by - ay) * share))
                steps.append((point, round(high), _moving(edges, (low + high) / 2)))
        for point, moment, belt in steps:
            if pieces and pieces[-1][0] == belt:
                pieces[-1][1].append(point)
                pieces[-1][2].append(moment)
            elif pieces:
                pieces.append((belt, [pieces[-1][1][-1], point], [pieces[-1][2][-1], moment]))
            else:
                pieces.append((belt, [stretch.points[0], point], [times[0], moment]))
    return [
        replace(stretch, belt=belt, points=tuple(points), times=tuple(moments)) for belt, points, moments in pieces
    ]


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


SAME_PASSAGE_YD = 440  # two passages that begin and end within a quarter mile of each other are one


def _crossings(stretches: tuple[Stretch, ...], most: int = 12) -> list[dict[str, dict[str, int]]]:
    """Where the road goes from one continent to another (a boat, a
    zeppelin, a portal): from where to where. The same passage made ten
    times - in either direction - is one crossing; the ones made most often
    come first, and at most ``most`` are given (the picture draws an arc
    for the first few between the continents it shows)."""
    found: list[list[Any]] = []  # [times, from, to]
    for before, after in zip(stretches, stretches[1:], strict=False):
        if before.continent == after.continent:
            continue
        ends = (_place(before.continent, before.points[-1]), _place(after.continent, after.points[0]))
        for entry in found:
            for a, b in ((entry[1], entry[2]), (entry[2], entry[1])):
                if (
                    a["c"] == ends[0]["c"]
                    and b["c"] == ends[1]["c"]
                    and math.dist((a["x"], a["y"]), (ends[0]["x"], ends[0]["y"])) <= SAME_PASSAGE_YD
                    and math.dist((b["x"], b["y"]), (ends[1]["x"], ends[1]["y"])) <= SAME_PASSAGE_YD
                ):
                    entry[0] += 1
                    break
            else:
                continue
            break
        else:
            if len(found) < 500:  # (far more than any character has; keeps a strange file from costing time)
                found.append([1, ends[0], ends[1]])
    found.sort(key=lambda entry: entry[0], reverse=True)
    return [{"from": start, "to": end} for _times, start, end in found[:most]]


def land_name(value: Any) -> str:
    """A land's name as the game gave it, fit to print: one line of plain
    text, not too long; "" if it is not a name."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(char if char.isprintable() else " " for char in value).split())
    if "\ufffd" in text or "|" in text:  # damaged, or the game's colour codes
        return ""
    return text if len(text) <= MAX_NAME_CHARS else ""


def _names(maps: Any) -> dict[int, str]:
    """``WalkaCharDB.maps`` -> what the game calls each land (by continent id)."""
    names: dict[int, str] = {}
    if not isinstance(maps, dict):
        return names
    for key, entry in maps.items():
        continent = _whole(key, 10**6)
        if continent is None or continent != key or not isinstance(entry, dict):
            continue
        name = land_name(entry.get("name"))
        if name and len(names) < MAX_LANDS:
            names[continent] = name
    return names


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
    # From the newest back: past MAX_POINTS it is the oldest road that is
    # left out, not where the character is now. (Put in order again below.)
    for segment in reversed(list(savedvars.sequence(table.get("segments")))):
        if not isinstance(segment, dict):
            continue
        continent = _whole(segment.get("c"), 10**6)
        x, y = _whole(segment.get("x"), WORLD_LIMIT_YD), _whole(segment.get("y"), WORLD_LIMIT_YD)
        mode = segment.get("m")
        if continent is None or x is None or y is None or mode not in MODES:
            continue
        points = [(x, y)]
        began = _whole(segment.get("t"), TIME_LIMIT_S)
        moment = began or 0
        times = [moment]
        steps = segment.get("d")
        if isinstance(steps, str):
            for seconds, dx, dy in _STEP.findall(steps):
                x, y = x + int(dx), y + int(dy)
                moment += max(0, int(seconds))
                points.append((x, y))
                times.append(moment)
        if len(points) < 2 or any(abs(px) > WORLD_LIMIT_YD or abs(py) > WORLD_LIMIT_YD for px, py in points):
            continue
        if total + len(points) > MAX_POINTS:
            truncated = True
            break
        total += len(points)
        level = _whole(segment.get("l"), 1000) or 0
        # (The addon's "b" is not read: the addon cannot see the belt, so it
        # only says that a session was running. Which road was walked on the
        # treadmill is known from the walks the app measured: to_api.)
        stretches.append(
            Stretch(continent, mode, False, max(0, level), tuple(points), tuple(times) if began is not None else ())
        )
    if not stretches:
        return None
    stretches.reverse()
    return Road(key, tuple(stretches), truncated, _names(table.get("maps")))


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
    """Thin all paths alike until together they fit the budget.

    Every path keeps its two ends however coarse the tolerance. A road made
    of very many short stretches (a walk paused and resumed a thousand
    times) could therefore never fit: when even two points a path are too
    many, the smallest paths - specks on a picture of the whole road - are
    left out first."""
    if 2 * len(paths) > budget:

        def size(index: int) -> int:
            points = paths[index][2]
            xs = [x for x, _y in points]
            ys = [y for _x, y in points]
            return max(max(xs) - min(xs), max(ys) - min(ys))

        largest = sorted(range(len(paths)), key=size, reverse=True)[: max(1, budget // 2)]
        paths = [paths[index] for index in sorted(largest)]
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
    road: Road | None = None
    # The road as the page gets it, and the walks it was drawn with.
    api: dict[str, Any] | None = None
    api_walked: tuple[tuple[float, float], ...] | None = None


@dataclass
class RoadBook:
    """The roads found in the game's saved files, each read again only when
    its file has changed. ``game_dirs`` says where games are installed
    (folders that hold ``WTF``); it is asked now and then, on the thread
    that asks for a road - never on the one that records the walk."""

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

    def for_character(self, wanted: str, walked: Sequence[tuple[float, float]] = ()) -> dict[str, Any]:
        """What the Share page asks: the road of the character with this
        id, and whether there was anywhere to look. ``walked``: when the
        treadmill's belt was moving (``walked_spans``).

        ``road`` is None if no saved file holds one for that character -
        most often because the game has not written its files since the
        addon began recording (it does when the player logs out or types
        /reload).
        """
        with self._lock:
            if wanted in self._demo:
                return {"road": self._demo[wanted], "game_found": True, "files": 0, "demo": True}
            self._scan()
            mine = [entry for entry in self._files.values() if entry.id == wanted and entry.road is not None]
            newest = max(mine, key=lambda entry: entry.mtime, default=None)
            if newest is not None and newest.road is not None:
                spans = tuple(walked)
                if newest.api is None or newest.api_walked != spans:
                    newest.api, newest.api_walked = newest.road.to_api(newest.mtime, walked=spans), spans
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
        except (OSError, ValueError, savedvars.SavedVariablesError) as exc:  # (whatever a damaged file holds)
            problem = f"{type(exc).__name__}: {exc}"
        entry.stamp = stamp  # (a file that cannot be read is not tried again until it changes)
        if problem:
            # The road read from it before stands. (No path here: it holds
            # the name of the player's account.)
            log.debug("a saved file of the addon could not be read (%s)", problem)
            return
        entry.mtime = status.st_mtime
        entry.id = road.id if road else ""
        entry.road, entry.api, entry.api_walked = road, None, None


# ---- the demo -------------------------------------------------------------------------------------


def demo_road(key: str) -> Road:
    """A made-up road for ``--demo``: a night elf's first twenty-three
    levels, through places that exist - down Teldrassil and Darkshore into
    Ashenvale, a flight back, then (after the boat) from Menethil Harbor
    over Loch Modan to Ironforge, and from Stormwind to Lakeshire. Around
    every village the errands of a dozen quests; most of it walked on the
    treadmill, some of it not. About as long as the demo character's time
    on foot makes it (``journey.DemoJourney``)."""
    stretches: list[Stretch] = []
    at = (0.0, 0.0)
    continent = 1
    level = 1
    laps = 0  # errands run so far: every fifth is done sitting down

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

    def quests(radius: float, rounds: int = 6, turn: float = 0.0) -> None:
        """Errands around a village: out and back again, round after round,
        each a few loops in another direction and not as far as the last."""
        nonlocal laps
        for index in range(rounds):
            laps += 1
            loops = 3 + (laps % 3)
            reach = radius * (1.0 - 0.55 * index / max(1, rounds - 1))
            twist = turn + 2.399963 * index  # (the golden angle: no two rounds alike)
            steps = max(12, round(loops * 2.5 * reach / 20))
            points = []
            for step in range(steps + 1):
                angle = math.pi * step / steps
                out = reach * math.sin(loops * angle)
                points.append((at[0] + out * math.cos(angle + twist), at[1] + out * math.sin(angle + twist)))
            add(points, "w", laps % 5 != 0)

    def reach_level(value: int) -> None:
        nonlocal level
        level = value

    # Kalimdor: Teldrassil ...
    start((10311, 832), on=1)  # Shadowglen
    quests(330, rounds=7, turn=0.4)
    reach_level(4)
    go((9850, 960))  # Dolanaar
    reach_level(6)
    quests(520, rounds=9, turn=1.9)
    go((9690, 1560))
    reach_level(8)
    quests(430, rounds=8, turn=0.8)
    go((9950, 2280), belt=False)  # Darnassus
    quests(170, rounds=3, turn=2.0)
    # ... the portal down to Rut'theran, and the boat across.
    start((8640, 880))
    go((6460, 800), belt=False)
    # Darkshore.
    go((6440, 614))  # Auberdine
    reach_level(10)
    go((6400, 250))
    quests(330, rounds=8, turn=2.6)
    go((5700, 200))
    reach_level(12)
    quests(420, rounds=9, turn=0.3)
    go((4980, 80))
    reach_level(14)
    quests(430, rounds=9, turn=1.1)
    go((4400, 330), belt=False)
    reach_level(16)
    quests(360, rounds=8, turn=1.2)
    # Ashenvale.
    go((3350, 150))
    reach_level(18)
    quests(520, rounds=9, turn=2.9)
    go((2745, -372))  # Astranaar
    reach_level(19)
    quests(640, rounds=10, turn=0.9)
    go((6440, 614), mode="t")  # the hippogryph back to Auberdine

    # The Eastern Kingdoms, off the boat at Menethil Harbor.
    start((-3740, -830), on=0)
    reach_level(20)
    go((-3600, -1250))
    quests(330, rounds=8, turn=2.2)
    go((-3330, -1650))
    go((-3150, -2450))
    quests(480, rounds=9, turn=0.5)
    go((-4050, -2700), belt=False)  # Dun Algaz
    go((-4780, -2700))
    reach_level(21)
    go((-5349, -2975))  # Thelsamar
    quests(620, rounds=10, turn=0.2)
    go((-5800, -2580))
    go((-5650, -1900), mode="r")
    go((-5700, -1450), mode="r")
    reach_level(22)
    quests(480, rounds=8, turn=1.4)
    go((-5582, -483))  # Kharanos
    quests(380, rounds=7, turn=2.5)
    go((-5050, -800), belt=False)  # up to Ironforge
    # ... the tram, and out of Stormwind.
    start((-8913, 554))
    go((-9464, 62))  # Goldshire
    quests(520, rounds=9, turn=0.7)
    go((-9450, -1300))
    quests(480, rounds=9, turn=2.1)
    go((-9600, -1900), belt=False)
    reach_level(23)
    go((-9282, -2269))  # Lakeshire
    quests(520, rounds=10, turn=1.6)
    return Road(key, tuple(stretches))
