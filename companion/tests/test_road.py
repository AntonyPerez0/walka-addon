"""The road for the Share page: decoding what the addon saved, thinning it
for a picture, and finding and re-reading the game's files."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import pytest

from walka import road, savedvars
from walka.journey import DEMO_KEY, DemoJourney
from walka.protocol import Stat, djb2
from walka.road import RoadBook, Stretch, character_id, decode, demo_road, simplify

KEY = "Tester-Azeroth"
NOW = 1_795_000_000.0


def lua(value: Any, indent: int = 0) -> str:
    """``value`` the way the game writes it into a saved-variables file."""
    tabs = "\t" * indent
    if isinstance(value, dict):
        lines = [f'{tabs}\t["{key}"] = {lua(item, indent + 1)},' for key, item in value.items()]
        return "{\r\n" + "\r\n".join(lines) + ("\r\n" if lines else "") + tabs + "}"
    if isinstance(value, list):
        lines = [f"{tabs}\t{lua(item, indent + 1)}, -- [{index}]" for index, item in enumerate(value, 1)]
        return "{\r\n" + "\r\n".join(lines) + ("\r\n" if lines else "") + tabs + "}"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


def saved(db: Any) -> bytes:
    return f"\r\nWalkaCharDB = {lua(db)}\r\n".encode()


def segment(x=0, y=0, steps=((20, 0), (20, 0)), *, c=1, m="w", b=None, level=1, t=1000) -> dict[str, Any]:
    out: dict[str, Any] = {"c": c, "m": m, "l": level, "t": t, "x": x, "y": y, "n": len(steps) + 1}
    if b:
        out["b"] = 1
    if steps:
        out["d"] = ";".join(f"3,{dx},{dy}" for dx, dy in steps)
    return out


def database(*segments: Any, key: Any = KEY, version: Any = 1) -> dict[str, Any]:
    return {"version": version, "guid": "Player-1-AAAA", "key": key, "yards": 0, "segments": list(segments)}


def read(db: Any) -> road.Road | None:
    """Through the file and back, as the companion gets it."""
    return decode(savedvars.parse(saved(db)).get("WalkaCharDB"))


# ---- whose road ---------------------------------------------------------------------------------


def test_a_road_is_known_by_the_id_the_journey_has():
    assert character_id(KEY) == f"{djb2(KEY):08x}"
    assert character_id("A") == "0002b5e6"  # 5381 * 33 + 65, leading zeros and all
    # The demo's road belongs to the demo's character.
    assert demo_road(DEMO_KEY).id == f"{DemoJourney(wall=lambda: NOW).value(Stat.CHAR_KEY, 0):08x}"


# ---- decoding -----------------------------------------------------------------------------------


def test_a_saved_road_is_decoded():
    got = read(
        database(
            segment(-600, 4200, [(20, 0), (19, -4), (0, 21)], level=7),
            segment(-541, 4217, [(60, 30), (70, 20)], m="t", level=7),
            segment(100, 100, [(0, -20), (-5, -20)], c=0, m="r", b=1, level=8),
            segment(95, 60, [(1, 1)], m="g", level=8),
        )
    )
    assert got is not None and got.key == KEY and got.id == character_id(KEY) and not got.truncated
    assert got.stretches == (
        Stretch(1, "w", False, 7, ((-600, 4200), (-580, 4200), (-561, 4196), (-561, 4217))),
        Stretch(1, "t", False, 7, ((-541, 4217), (-481, 4247), (-411, 4267))),
        Stretch(0, "r", True, 8, ((100, 100), (100, 80), (95, 60))),
        Stretch(1, "g", False, 8, ((95, 60), (96, 61))),
    )
    assert [stretch.style for stretch in got.stretches] == ["road", "flight", "walked", "road"]
    assert got.stretches[0].yards == pytest.approx(20 + math.hypot(19, 4) + 21)


@pytest.mark.parametrize(
    "db",
    [
        None,
        "text",
        {},
        database(segment(), version=2),
        database(segment(), version=None),
        database(segment(), key=None),
        database(segment(), key=""),
        database(segment(), key=7),
        database(segment(), key="x" * 201),
        database(),
        {"version": 1, "key": KEY},
        {"version": 1, "key": KEY, "segments": "none"},
        database(segment(steps=())),  # a lone point is no road
    ],
)
def test_what_holds_no_road_gives_none(db):
    assert decode(db) is None
    assert read(db) is None


def test_a_damaged_stretch_costs_that_stretch_only():
    good = segment(0, 0, [(20, 0)])
    got = read(
        database(
            "text",
            7,
            {},
            segment(c="one"),
            segment(x="far"),
            segment(y=float("1e9")),
            segment(m="z"),
            segment(m=5),
            {**segment(), "d": 12},
            {**segment(), "d": "no steps here"},
            segment(999_990, 0, [(20, 0)]),  # walks off the edge of every world
            good,
            {**segment(5, 5), "d": "3,10,0;junk;3,x,1;3,0,10;;3,-1"},  # what can be read of it
            {**segment(7, 7, [(1, 1)]), "l": "high", "b": "yes"},
        )
    )
    assert got is not None
    assert [stretch.points for stretch in got.stretches] == [
        ((0, 0), (20, 0)),
        ((5, 5), (15, 5), (15, 15)),
        ((7, 7), (8, 8)),
    ]
    assert got.stretches[2].level == 0 and got.stretches[2].belt is False


def test_fractions_in_the_saved_numbers_are_rounded():
    got = read(database({**segment(0.4, -0.6, [(20, 0)]), "c": 1.0, "l": 3.0}))
    assert got is not None and got.stretches == (Stretch(1, "w", False, 3, ((0, -1), (20, -1))),)


def test_a_road_too_long_to_take_is_cut_and_says_so(monkeypatch):
    monkeypatch.setattr(road, "MAX_POINTS", 7)
    got = decode(savedvars.parse(saved(database(*[segment(i * 100, 0) for i in range(5)])))["WalkaCharDB"])
    assert got is not None and len(got.stretches) == 2 and got.truncated
    assert got.to_api(NOW)["truncated"] is True


# ---- for the page -------------------------------------------------------------------------------


def test_the_road_as_the_page_gets_it():
    got = read(
        database(
            segment(0, 0, [(0, 100), (100, 0)], level=9),  # on foot, belt off
            segment(100, 100, [(100, 0)], b=1, level=9),  # the same road, now walked for real
            segment(200, 100, [(100, 0)], b=1, level=10),  # (a new stretch at every level)
            segment(300, 100, [(1000, 500)], m="t", level=10),  # a flight
            segment(-50, -50, [(0, 30), (40, 0)], c=0, m="r", level=10),  # a ride on the other continent
        )
    )
    assert got is not None
    api = got.to_api(NOW)
    assert api == {
        "id": character_id(KEY),
        "as_of": NOW,
        "yards": 470,  # the ground travelled; the flight is apart
        "belt_yards": 200,
        "flight_yards": round(math.hypot(1000, 500)),
        "level": 10,
        "start": {"c": 1, "x": 0, "y": 0},
        "end": {"c": 0, "x": -10, "y": -20},
        "milestones": [{"level": 10, "c": 1, "x": 200, "y": 100}],
        # From the end of the flight to where the ride begins: another continent.
        "crossings": [{"from": {"c": 1, "x": 1300, "y": 600}, "to": {"c": 0, "x": -50, "y": -50}}],
        "continents": [
            {
                "id": 1,
                "name": "Kalimdor",
                "yards": 400,
                "bounds": [0, 0, 1300, 600],
                "paths": [
                    {"style": "road", "p": [0, 0, 0, 100, 100, 100]},
                    {"style": "walked", "p": [100, 100, 300, 100]},  # two stretches, one line
                    {"style": "flight", "p": [300, 100, 1300, 600]},
                ],
            },
            {
                "id": 0,
                "name": "Eastern Kingdoms",
                "yards": 70,
                "bounds": [-50, -50, -10, -20],
                "paths": [{"style": "road", "p": [-50, -50, -50, -20, -10, -20]}],
            },
        ],
        "truncated": False,
    }


def test_milestones_are_only_the_tens_reached_on_the_recorded_road():
    def at(level: int, x: int) -> dict[str, Any]:
        return segment(x, 0, [(20, 0)], level=level)

    marks = lambda *levels: [  # noqa: E731
        (mark["level"], mark["x"])
        for mark in read(database(*[at(level, 100 * i) for i, level in enumerate(levels)])).to_api(NOW)["milestones"]
    ]
    assert marks(8, 9, 10, 11, 19, 20, 21) == [(10, 200), (20, 500)]
    assert marks(23, 24) == []  # recording began at 23: no "10", no "20"
    assert marks(28, 31, 58, 60) == [(30, 100), (40, 200), (50, 200), (60, 300)]
    assert marks(0, 12) == [(10, 100)]  # (a level the game would not say counts as none)
    assert marks(30, 29, 30) == []


def test_passages_between_continents():
    def crossings(*legs: tuple[int, int, int]) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
        got = read(database(*[segment(x, y, [(20, 0)], c=c) for c, x, y in legs])).to_api(NOW)["crossings"]
        return [
            ((a["c"], a["x"], a["y"]), (b["c"], b["x"], b["y"]))
            for a, b in ((entry["from"], entry["to"]) for entry in got)
        ]

    # One continent: none. A jump within a continent (a hearthstone) is none either.
    assert crossings((1, 0, 0), (1, 5000, 5000)) == []
    # There: from the last place on the one to the first on the other.
    assert crossings((1, 0, 0), (0, 900, 900)) == [((1, 20, 0), (0, 900, 900))]
    # There and back by the same boat, ten times over: still one passage.
    boat = [(1, 0, 0), (0, 880, 900)] * 10
    assert crossings(*boat) == [((1, 20, 0), (0, 880, 900))]
    # Several: the ones travelled most come first, and three are enough for a picture.
    often = [(1, 0, 0), (0, 5000, 0)] * 3
    twice = [(1, 3000, 0), (0, -5000, 0)] * 2
    once = [(1, 6000, 0), (0, -9000, 0)]
    rare = [(1, -6000, 0), (0, 9000, 0)]
    got = crossings(*(rare + once + twice + often))
    assert len(got) == 3
    assert got[0] == ((1, 20, 0), (0, 5000, 0))  # there three times and back twice
    assert got[1] == ((1, 3020, 0), (0, -5000, 0))


def test_an_unknown_continent_has_no_name_and_the_longest_comes_first():
    got = read(database(segment(0, 0, [(10, 0)], c=530), segment(0, 0, [(500, 0)], c=571)))
    assert [(entry["id"], entry["name"]) for entry in got.to_api(NOW)["continents"]] == [(571, ""), (530, "")]


def spiral(turns: int, points: int) -> list[tuple[int, int]]:
    return [
        (round(40 * t * math.cos(t)), round(40 * t * math.sin(t)))
        for t in (turns * 2 * math.pi * i / points for i in range(points))
    ]


def test_simplify_keeps_what_the_line_needs():
    straight = [(i, 0) for i in range(100)]
    assert simplify(straight, 0.5) == [(0, 0), (99, 0)]
    corner = [(min(i, 50), max(0, i - 50)) for i in range(100)]
    assert simplify(corner, 0.5) == [(0, 0), (50, 0), (50, 49)]
    assert simplify(corner, 0) == corner
    assert simplify([(0, 0), (5, 5)], 100) == [(0, 0), (5, 5)]
    assert simplify([(3, 3)] * 50, 1) == [(3, 3), (3, 3)]  # going nowhere: its two ends
    there_and_back = [(i, 0) for i in range(50)] + [(49 - i, 0) for i in range(50)]
    assert simplify(there_and_back, 0.5) == [(0, 0), (49, 0), (0, 0)]
    # Every point left out is within the tolerance of the line that is drawn.
    curve = spiral(4, 4000)
    for tolerance in (1.0, 5.0, 25.0):
        kept = simplify(curve, tolerance)
        assert kept[0] == curve[0] and kept[-1] == curve[-1] and len(kept) < len(curve) / 4
        for point in curve[::37]:
            assert min(_off(point, a, b) for a, b in zip(kept, kept[1:], strict=False)) <= tolerance + 0.75


def _off(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if not span else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def test_a_long_road_reaches_the_page_within_its_budget():
    stretches = []
    for index in range(60):  # sixty levels of wandering, 180,000 points
        points = [(x + index * 3000, y) for x, y in spiral(6, 3000)]
        stretches.append(Stretch(index % 2, "w", index % 3 == 0, index + 1, tuple(points)))
    long_road = road.Road(KEY, tuple(stretches))
    api = long_road.to_api(NOW)
    sent = sum(len(path["p"]) // 2 for entry in api["continents"] for path in entry["paths"])
    assert 500 < sent <= road.POINT_BUDGET
    assert api["yards"] == round(sum(stretch.yards for stretch in stretches))  # measured before the thinning
    assert api["start"] == {"c": 0, "x": 0, "y": 0} and len(api["milestones"]) == 6
    tight = long_road.to_api(NOW, budget=300)
    paths = [path for entry in tight["continents"] for path in entry["paths"]]
    assert len(paths) == 60 and all(len(path["p"]) >= 4 for path in paths)  # every stretch keeps its two ends


def test_the_demo_road():
    api = demo_road(DEMO_KEY).to_api(NOW)
    assert [entry["name"] for entry in api["continents"]] == ["Kalimdor", "Eastern Kingdoms"]
    assert [mark["level"] for mark in api["milestones"]] == [10, 20] and api["level"] == 23
    assert api["start"] == {"c": 1, "x": 10311, "y": 832}  # Shadowglen: a night elf's first steps
    assert [(entry["from"]["c"], entry["to"]["c"]) for entry in api["crossings"]] == [(1, 0)]
    assert 0 < api["belt_yards"] < api["yards"] and api["flight_yards"] > 0
    styles = {path["style"] for entry in api["continents"] for path in entry["paths"]}
    assert styles == {"walked", "road", "flight"}


# ---- the game's files ---------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.wall = NOW
        self.mono = 5000.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


class Game:
    """A game folder with saved files in it, and a RoadBook looking at it."""

    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.dir = tmp_path / "World of Warcraft" / "_forever_"
        (self.dir / "WTF" / "Account").mkdir(parents=True)
        self.dirs = [self.dir]
        self.asked = 0
        self.book = RoadBook(self.game_dirs, wall=lambda: self.clock.wall, mono=lambda: self.clock.mono)

    def game_dirs(self) -> list[Path]:
        self.asked += 1
        return list(self.dirs)

    def path(self, account="ACCOUNT1", realm="Azeroth", name="Tester") -> Path:
        return self.dir / "WTF" / "Account" / account / realm / name / "SavedVariables" / "Walka.lua"

    def save(self, db: Any, *, age: float = 100.0, **where: str) -> Path:
        """The game writes a character's file, ``age`` seconds ago."""
        path = self.path(**where)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(db if isinstance(db, bytes) else saved(db))
        os.utime(path, (self.clock.wall - age, self.clock.wall - age))
        return path

    def ask(self, key: str = KEY, *, wait: float = road.RESCAN_S) -> dict[str, Any]:
        self.clock.advance(wait)
        return self.book.for_character(character_id(key))


@pytest.fixture
def game(tmp_path: Path) -> Game:
    return Game(tmp_path)


def count_reads(monkeypatch) -> list[Path]:
    reads: list[Path] = []
    real = road.read_shared

    def counting(path: Path, limit: int = road.MAX_FILE_BYTES) -> bytes:
        reads.append(path)
        return real(path, limit)

    monkeypatch.setattr(road, "read_shared", counting)
    return reads


def test_the_road_of_a_character_is_found_in_its_saved_file(game: Game):
    game.save(database(segment(0, 0, [(0, 100)], level=4)), age=100)
    answer = game.ask()
    assert answer["game_found"] is True and answer["files"] == 1 and answer["demo"] is False
    assert answer["road"]["id"] == character_id(KEY) and answer["road"]["yards"] == 100
    assert answer["road"]["as_of"] == pytest.approx(NOW - 100, abs=1)  # when the game wrote it
    # Another character: nothing, but there was somewhere to look.
    assert game.ask("Other-Azeroth") == {"road": None, "game_found": True, "files": 1, "demo": False}


def test_without_a_game_there_is_nowhere_to_look(game: Game):
    game.dirs = []
    assert game.ask() == {"road": None, "game_found": False, "files": 0, "demo": False}
    game.dirs = [game.dir / "gone"]
    game.clock.advance(road.GAME_DIRS_S)
    assert game.ask() == {"road": None, "game_found": True, "files": 0, "demo": False}

    def broken() -> list[Path]:
        raise OSError("the registry will not say")

    book = RoadBook(broken, wall=lambda: NOW, mono=lambda: 1.0)
    assert book.for_character("0badf00d") == {"road": None, "game_found": False, "files": 0, "demo": False}


def test_only_the_addons_own_per_character_files_are_read(game: Game, monkeypatch):
    reads = count_reads(monkeypatch)
    mine = game.save(database(segment()))
    account = game.dir / "WTF" / "Account" / "ACCOUNT1"
    decoys = [
        account / "SavedVariables" / "Walka.lua",  # the account-wide file: sessions, not the road
        mine.with_name("Walka.lua.bak"),
        mine.with_name("OtherAddon.lua"),
        account / "Azeroth" / "Tester" / "Walka.lua",
        game.dir / "WTF" / "Walka.lua",
        game.dir / "Interface" / "AddOns" / "Walka" / "Walka.lua",
    ]
    for decoy in decoys:
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_bytes(saved(database(segment(0, 0, [(5000, 0)]))))
        os.utime(decoy, (NOW - 100, NOW - 100))
    assert road.saved_files(game.dir) == [mine]
    assert game.ask()["road"]["yards"] == 40
    assert reads == [mine]
    assert road.saved_files(game.dir / "nowhere") == []


def test_a_file_is_read_again_only_when_the_game_has_rewritten_it(game: Game, monkeypatch):
    reads = count_reads(monkeypatch)
    game.save(database(segment(0, 0, [(0, 100)])))
    assert game.ask()["road"]["yards"] == 100
    for _ in range(20):
        assert game.ask()["road"]["yards"] == 100
    assert len(reads) == 1 and game.asked == 2  # (the game is looked for once a minute)
    game.save(database(segment(0, 0, [(0, 100), (0, 150)])))
    assert game.ask()["road"]["yards"] == 250
    assert len(reads) == 2


def test_asking_often_does_not_mean_looking_often(game: Game, monkeypatch):
    game.save(database(segment(0, 0, [(0, 100)])))
    assert game.ask()["road"]["yards"] == 100
    looks: list[Path] = []
    real = road.saved_files
    monkeypatch.setattr(road, "saved_files", lambda folder: looks.append(folder) or real(folder))
    for _ in range(50):
        game.ask(wait=0.1)  # five seconds of a page asking ten times a second
    assert 1 <= len(looks) <= 2


def test_a_file_written_a_moment_ago_is_left_until_it_has_settled(game: Game, monkeypatch):
    reads = count_reads(monkeypatch)
    game.save(database(segment(0, 0, [(0, 100)])), age=0.0)
    assert game.ask(wait=0.5)["road"] is None and reads == []  # the game may still be writing
    assert game.ask()["road"]["yards"] == 100  # three seconds on
    assert len(reads) == 1


def test_a_file_dated_ahead_of_the_clock_is_still_read(game: Game):
    # (A drive whose times are off by a time zone must not hide the road for hours.)
    game.save(database(segment(0, 0, [(0, 100)])), age=-7200.0)
    assert game.ask()["road"] is None  # looked at once: fresh, as far as anyone can tell
    assert game.ask()["road"]["yards"] == 100  # ... and unchanged since


def test_a_file_that_keeps_changing_is_read_when_it_stops(game: Game, monkeypatch):
    reads = count_reads(monkeypatch)
    for size in range(1, 6):
        game.clock.advance(road.RESCAN_S)
        game.save(database(*[segment(0, 0, [(0, 100)])] * size), age=0.0)
        assert game.ask(wait=0)["road"] is None
    assert reads == []
    assert game.ask()["road"]["yards"] == 500
    assert len(reads) == 1


def test_a_file_cut_short_does_not_take_the_road_away(game: Game, monkeypatch):
    reads = count_reads(monkeypatch)
    whole = saved(database(segment(0, 0, [(0, 100)])))
    game.save(whole)
    assert game.ask()["road"]["yards"] == 100
    for index, broken in enumerate((whole[: len(whole) // 2], b"", b"\x00" * 4096, b"WalkaCharDB = {", b"x = 1 + 1")):
        game.save(broken, age=50 - index)
        assert game.ask()["road"]["yards"] == 100, broken[:20]
        game.ask()
    assert len(reads) == 6  # each damaged file was tried once, not over and over
    game.save(saved(database(segment(0, 0, [(0, 300)]))), age=10)
    assert game.ask()["road"]["yards"] == 300


@pytest.mark.parametrize("db", [None, {"version": 9, "key": KEY}, database()])
def test_a_sound_file_without_a_road_means_there_is_none(game: Game, db):
    # (The addon was reset, or a newer one writes what this companion cannot read.)
    game.save(database(segment(0, 0, [(0, 100)])))
    assert game.ask()["road"]["yards"] == 100
    game.save(db, age=10)
    assert game.ask() == {"road": None, "game_found": True, "files": 1, "demo": False}


def test_a_file_too_big_is_not_taken(game: Game, monkeypatch):
    monkeypatch.setattr(road, "MAX_FILE_BYTES", 400)
    game.save(database(segment(0, 0, [(0, 100)] * 200)))
    assert game.ask() == {"road": None, "game_found": True, "files": 1, "demo": False}


def test_the_same_character_on_two_accounts_or_two_games(game: Game, tmp_path: Path):
    game.save(database(segment(0, 0, [(0, 100)])), age=500, account="ACCOUNT1")
    game.save(database(segment(0, 0, [(0, 200)])), age=100, account="ACCOUNT2")  # the one played last
    game.save(database(segment(0, 0, [(0, 300)]), key="Other-Azeroth"), name="Other")
    other_game = tmp_path / "World of Warcraft" / "_ptr_"
    game.dirs.append(other_game)
    old = other_game / "WTF" / "Account" / "ACCOUNT1" / "Azeroth" / "Tester" / "SavedVariables" / "Walka.lua"
    old.parent.mkdir(parents=True)
    old.write_bytes(saved(database(segment(0, 0, [(0, 900)]))))
    os.utime(old, (NOW - 9000, NOW - 9000))
    answer = game.ask()
    assert answer["road"]["yards"] == 200 and answer["files"] == 4
    assert game.ask("Other-Azeroth")["road"]["yards"] == 300


def test_whose_road_it_is_comes_from_the_file_not_from_the_folders(game: Game):
    # (The folders are named differently from client to client.)
    game.save(database(segment(0, 0, [(0, 100)])), realm="Some Realm", name="Sömeone")
    assert game.ask()["road"]["yards"] == 100


def test_a_deleted_character_is_forgotten(game: Game):
    path = game.save(database(segment(0, 0, [(0, 100)])))
    assert game.ask()["road"] is not None
    path.unlink()
    assert game.ask() == {"road": None, "game_found": True, "files": 0, "demo": False}


def test_the_demo_road_needs_no_game(game: Game):
    game.dirs = []
    demo = demo_road(DEMO_KEY)
    game.book.use_demo(demo)
    answer = game.book.for_character(demo.id)
    assert answer["demo"] is True and answer["road"]["id"] == demo.id and answer["road"]["as_of"] == NOW
    assert game.asked == 0
    assert game.ask()["road"] is None


def test_nothing_about_the_account_is_logged(game: Game, caplog):
    caplog.set_level("DEBUG")
    game.save(b"WalkaCharDB = {", account="SECRETACCOUNT", realm="SecretRealm", name="Secretname")
    assert game.ask("Secretname-SecretRealm")["road"] is None
    assert "could not be read" in caplog.text
    assert "secret" not in caplog.text.lower()


# ---- reading without getting in the game's way --------------------------------------------------


def test_read_shared_reads_all_of_a_file_up_to_its_limit(tmp_path: Path):
    path = tmp_path / "Wörld of Wärcraft" / "Walka.lua"
    path.parent.mkdir()
    data = bytes(range(256)) * 40  # (every byte, Ctrl-Z and line ends among them)
    path.write_bytes(data)
    assert road.read_shared(path) == data
    assert road.read_shared(path, 100) == data[:101]
    with pytest.raises(OSError):
        road.read_shared(tmp_path / "missing.lua")
    with pytest.raises(OSError):
        road.read_shared(tmp_path)


def test_the_game_can_replace_a_file_the_companion_has_just_read(tmp_path: Path):
    path = tmp_path / "Walka.lua"
    path.write_bytes(b"old")
    assert road.read_shared(path) == b"old"
    # Nothing is held open: the game's way of saving (write a new file, put
    # it in the old one's place) works straight away, on Windows too.
    fresh = tmp_path / "Walka.lua.tmp"
    fresh.write_bytes(b"new")
    os.replace(fresh, path)
    assert road.read_shared(path) == b"new"
    path.unlink()


@pytest.mark.skipif(os.name != "nt", reason="file sharing modes are a Windows matter")
def test_a_file_the_game_holds_open_can_still_be_read(tmp_path: Path):
    path = tmp_path / "Walka.lua"
    path.write_bytes(b"held")
    with open(path, "r+b") as held:
        held.seek(0, os.SEEK_END)
        held.write(b" open")
        held.flush()
        assert road.read_shared(path) == b"held open"
