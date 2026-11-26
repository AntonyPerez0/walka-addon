"""The road: Core/Road.lua (which points of a character's path are kept, and
how) and RoadRecorder.lua (asking the game where the character is)."""

from __future__ import annotations

import math
import re

import pytest

from walka import road as companion_road
from walka import savedvars

from .harness import lua_list, lua_plain

T0 = 1_800_000_000


class Walk:
    """Feeds a Road.Recorder the way the game glue does: a look every
    quarter of a second."""

    def __init__(self, rt, db=None):
        self.rt = rt
        self.road = rt.ns.Road
        self.db = self.road.database(db if db is not None else rt.lua.table(), "Player-1-AAAA", "Tester-Azeroth")
        self.rec = self.road.Recorder.new(self.db)
        self.now = float(T0)
        self.x, self.y = 0.0, 0.0
        self.continent = 1
        self.mode, self.belt, self.level = "w", False, 10

    def look(self):
        self.rec.sample(self.rec, self.now, self.continent, round(self.x), round(self.y), self.mode, self.belt, self.level)
        self.now += 0.25

    def go(self, dx, dy, yards_per_second=7.0):
        """Travel in a straight line by (dx, dy) yards."""
        length = math.hypot(dx, dy)
        looks = max(1, int(length / (yards_per_second * 0.25)))
        for _ in range(looks):
            self.x += dx / looks
            self.y += dy / looks
            self.look()

    def stand(self, seconds):
        for _ in range(int(seconds * 4)):
            self.look()

    def segments(self):
        self.rec.flush(self.rec)
        return [lua_plain(segment) for segment in lua_list(self.db.segments)]

    def points(self, index):
        self.rec.flush(self.rec)
        segment = lua_list(self.db.segments)[index]
        xs, ys, count = self.road.points(segment)
        return [(xs[i], ys[i]) for i in range(1, count + 1)]


# ---- which points are kept ---------------------------------------------------------------------


def test_a_straight_road_gets_a_point_every_twenty_yards(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(200, 0)
    (segment,) = walk.segments()
    assert segment["c"] == 1 and segment["m"] == "w" and segment["l"] == 10 and "b" not in segment
    assert (segment["x"], segment["y"], segment["t"]) == (0, 0, T0)
    points = walk.points(0)
    assert 9 <= len(points) <= 12
    gaps = [math.dist(a, b) for a, b in zip(points, points[1:], strict=False)]
    assert all(19 <= gap <= 23 for gap in gaps)
    assert all(abs(y) <= 1 for _x, y in points)
    assert walk.db.yards == pytest.approx(sum(gaps))
    # The saved text: whole numbers, "seconds,yards,yards" per point.
    assert re.fullmatch(r"(-?\d+,-?\d+,-?\d+;)*-?\d+,-?\d+,-?\d+", segment["d"])
    assert segment["n"] == len(points) - 1


def test_standing_still_stores_nothing(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(100, 0)
    before = walk.segments()
    walk.stand(600)
    assert walk.segments() == before


def test_a_bend_is_kept(rt):
    """Fifteen yards one way, fifteen the other: less than twenty in all
    from the last point, but the corner must not be cut."""
    walk = Walk(rt)
    walk.look()
    walk.go(100, 0)
    corner = (walk.x, walk.y)
    walk.go(0, 100)
    points = walk.points(0)
    assert min(math.dist(point, corner) for point in points) <= 4
    # ... and the drawn line never strays far from where the character went.
    for a, b in zip(points, points[1:], strict=False):
        middle = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        on_first_leg = abs(middle[1]) <= 3 and -1 <= middle[0] <= 101
        on_second_leg = abs(middle[0] - 100) <= 3 and -1 <= middle[1] <= 101
        assert on_first_leg or on_second_leg, middle


def test_a_winding_road_stays_within_a_few_yards(rt):
    walk = Walk(rt)
    walk.look()
    path = []
    for step in range(2000):
        angle = step / 40.0
        walk.x += 1.5 * math.cos(angle)
        walk.y += 1.5 * math.sin(angle) + 0.4
        walk.look()
        path.append((walk.x, walk.y))
    points = [point for index in range(len(walk.segments())) for point in walk.points(index)]
    assert len(points) < len(path) / 3, "far fewer points than looks"

    def off(point):
        best = 1e9
        for a, b in zip(points, points[1:], strict=False):
            ax, ay, bx, by = *a, *b
            length = math.dist(a, b) or 1.0
            t = max(0.0, min(1.0, ((point[0] - ax) * (bx - ax) + (point[1] - ay) * (by - ay)) / length**2))
            best = min(best, math.dist(point, (ax + t * (bx - ax), ay + t * (by - ay))))
        return best

    assert max(off(point) for point in path[::7]) <= 5


# ---- where a segment ends ----------------------------------------------------------------------


def test_a_jump_starts_a_new_segment(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(100, 0)
    walk.x += 5000          # a hearthstone
    walk.look()
    walk.go(100, 0)
    first, second = walk.segments()
    assert walk.points(0)[-1][0] == pytest.approx(100, abs=2), "the road runs up to where the character stood"
    assert second["x"] == pytest.approx(5100, abs=2)
    # (The last few yards of the open stretch are stored when it ends.)
    assert walk.db.yards == pytest.approx(200, abs=22), "the jump itself is not road"


def test_another_continent_starts_a_new_segment(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(60, 0)
    walk.continent = 0
    walk.look()
    walk.go(60, 0)
    assert [segment["c"] for segment in walk.segments()] == [1, 0]


def test_where_the_game_cannot_place_the_character_the_segment_ends(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(50, 0)
    walk.continent = None       # a dungeon
    walk.stand(30)
    walk.continent = 1
    walk.x, walk.y = 900.0, 40.0
    walk.look()
    walk.go(50, 0)
    first, second = walk.segments()
    assert walk.points(0)[-1] == (50, 0)
    assert (second["x"], second["y"]) == (900, 40)


def test_a_single_point_is_not_a_road(rt):
    walk = Walk(rt)
    walk.look()
    walk.continent = None
    walk.look()
    assert walk.segments() == []


@pytest.mark.parametrize("change", ["mode", "level"])
def test_mounting_up_or_levelling_carries_on_in_a_new_segment_without_a_gap(rt, change):
    walk = Walk(rt)
    walk.look()
    walk.go(70, 0)
    if change == "mode":
        walk.mode = "r"
    else:
        walk.level = 11
    walk.go(70, 0, yards_per_second=14.0)
    first, second = walk.segments()
    assert walk.points(0)[-1] == walk.points(1)[0], "the line is in one piece"
    assert (first["m"], first["l"]) == ("w", 10)
    assert (second["m"], second["l"]) == (("r", 10) if change == "mode" else ("w", 11))


def test_a_flight_is_stored_more_coarsely(rt):
    walk = Walk(rt)
    walk.mode = "t"
    walk.look()
    walk.go(3000, 0, yards_per_second=30.0)
    (segment,) = walk.segments()
    gaps = [math.dist(a, b) for a, b in zip(walk.points(0), walk.points(0)[1:], strict=False)]
    assert segment["m"] == "t" and all(70 <= gap <= 85 for gap in gaps)


def test_stretches_walked_on_the_treadmill_are_marked(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(200, 0)
    walk.belt = True
    walk.go(200, 0)
    walk.belt = False
    walk.go(200, 0)
    segments = walk.segments()
    assert [segment.get("b") for segment in segments] == [None, 1, None]
    assert walk.db.beltYards == pytest.approx(200, abs=25)
    assert walk.db.yards == pytest.approx(600, abs=22)
    for index in range(len(segments) - 1):
        assert walk.points(index)[-1] == walk.points(index + 1)[0]


def test_a_very_long_stretch_is_cut_into_segments(rt):
    walk = Walk(rt)
    rt.ns.Road.MAX_SEGMENT_POINTS = 50
    walk.look()
    walk.go(5000, 0)
    segments = walk.segments()
    assert len(segments) >= 4 and all(segment["n"] <= 50 for segment in segments)
    assert walk.points(0)[-1] == walk.points(1)[0]


# ---- the saved table ---------------------------------------------------------------------------


def test_flush_puts_the_open_segment_into_the_saved_table(rt):
    walk = Walk(rt)
    walk.look()
    walk.go(100, 0)
    segment = lua_list(walk.db.segments)[0]
    assert segment.d is None, "nothing joined yet"
    walk.rec.flush(walk.rec)
    assert segment.d.count(";") == segment.n - 1 and segment.n >= 4


def test_a_damaged_segment_text_yields_the_points_before_the_damage(rt):
    road = rt.ns.Road
    segment = rt.to_lua({"c": 1, "m": "w", "l": 3, "t": T0, "x": 10, "y": 20, "d": "3,20,0;3,0,-20;oops;3,20,0"})
    xs, ys, count = road.points(segment)
    assert [(xs[i], ys[i]) for i in range(1, count + 1)] == [(10, 20), (30, 20), (30, 0), (50, 0)]


def test_database_is_made_sound(rt):
    road = rt.ns.Road
    fresh = lua_plain(road.database(None, "Player-1-AAAA", "Tester-Azeroth"))
    assert fresh == {
        "version": 1, "guid": "Player-1-AAAA", "key": "Tester-Azeroth", "yards": 0, "beltYards": 0,
        "segments": {}, "maps": {},
    }
    damaged = rt.run(
        """
        return { version = 1, guid = "Player-1-AAAA", yards = "far", beltYards = 0/0, segments = {
            { c = 1, m = "w", l = 3, t = 5, x = 1, y = 2, d = "1,2,3", n = 1 },
            "junk", { c = "one" }, { c = 1, m = "x", t = 5, x = 1, y = 2 },
            { c = 1, m = "r", l = 3, t = 5, x = 1, y = 2, d = {} },
        } }
        """
    )
    db = lua_plain(road.database(damaged, "Player-1-AAAA", "Tester-Azeroth"))
    assert db["yards"] == 0 and db["beltYards"] == 0
    assert len(db["segments"]) == 1


def test_the_map_of_each_land_is_remembered(rt):
    """Which of the game's maps a land's road is drawn on, and its name: how
    a land the game lists under no continent is found again."""
    road = rt.ns.Road
    db = road.database(None, "Player-1-AAAA", "Tester-Azeroth")
    assert road.rememberMap(db, 2600, 2500, "Zephras Isle") is True
    assert road.rememberMap(db, 2600, 2500, "Zephras Isle") is False, "known already"
    assert road.rememberMap(db, 2600, 2500, None) is False and db.maps[2600].name == "Zephras Isle"
    assert road.rememberMap(db, 1, 1414, "x" * 500) is True and db.maps[1].name is None, "no name is better than a strange one"
    assert road.rememberMap(db, 2600, 2501, "Skywall") is True and db.maps[2600].id == 2501
    assert road.rememberMap(db, None, 2501, "Skywall") is False and road.rememberMap(db, 3, "map", "Skywall") is False
    # Kept over a reload, and made sound should the file be damaged.
    again = lua_plain(road.database(db, "Player-1-AAAA", "Tester-Azeroth"))
    assert again["maps"] == {2600: {"id": 2501, "name": "Skywall"}, 1: {"id": 1414}}
    damaged = rt.run(
        """
        return { version = 1, guid = "Player-1-AAAA", segments = {}, maps = {
            [2600] = { id = 2500, name = "Zephras Isle" }, [7] = "junk", [8] = { id = "nine" },
            somewhere = { id = 4 }, [9] = { id = 5, name = { "a table" } },
        } }
        """
    )
    kept = lua_plain(road.database(damaged, "Player-1-AAAA", "Tester-Azeroth"))["maps"]
    assert kept == {2600: {"id": 2500, "name": "Zephras Isle"}, 9: {"id": 5}}
    assert lua_plain(road.database(rt.run("return { version = 1, segments = {}, maps = 'none' }"), None, None))["maps"] == {}
    # Another character with this one's name does not inherit its lands.
    other = lua_plain(road.database(db, "Player-1-BBBB", "Tester-Azeroth"))
    assert other["maps"] == {}


def test_a_new_character_with_an_old_name_starts_afresh(rt):
    """The game keeps per-character files by name: a character created
    with the name of a deleted one inherits its file."""
    road = rt.ns.Road
    walk = Walk(rt)
    walk.look()
    walk.go(100, 0)
    walk.segments()
    same = road.database(walk.db, "Player-1-AAAA", "Tester-Azeroth")
    assert len(lua_list(same.segments)) == 1
    other = road.database(walk.db, "Player-1-BBBB", "Tester-Azeroth")
    assert len(lua_list(other.segments)) == 0 and other.yards == 0 and other.guid == "Player-1-BBBB"


# ---- drawing fewer points --------------------------------------------------------------------


def test_simplify_keeps_what_the_line_needs(rt):
    road = rt.ns.Road
    xs = rt.to_lua([float(i) for i in range(100)])
    ys = rt.to_lua([0.0] * 100)
    keep, kept = road.simplify(xs, ys, 100, 0.5)
    assert kept == 2 and keep[1] and keep[100]
    # An L: the corner stays.
    xs = rt.to_lua([float(min(i, 50)) for i in range(100)])
    ys = rt.to_lua([float(max(0, i - 50)) for i in range(100)])
    keep, kept = road.simplify(xs, ys, 100, 0.5)
    assert kept == 3 and keep[51]
    # No tolerance: everything.
    keep, kept = road.simplify(xs, ys, 100, 0)
    assert kept == 100


# ---- lines for a map ---------------------------------------------------------------------------

# How world yards become map units in these tests: a tenth of a unit a
# yard, x to the right and y down, nothing turned.
PLACE = [0.1, 0.0, 0.0, 0.0, 0.1, 0.0]
WIDE = dict(left=-1e9, top=-1e9, right=1e9, bottom=1e9)


def segment_of(points, *, continent=1, mode="w", belt=False, level=10):
    """A saved segment through `points` (whole world yards). (Continent 1
    is the one the tests' map shows; see lines_of.)"""
    steps = [f"1,{bx - ax},{by - ay}" for (ax, ay), (bx, by) in zip(points, points[1:])]
    belt_text = ",b=1" if belt else ""
    return (
        f"{{c={continent},m='{mode}',l={level},t={T0}{belt_text},x={points[0][0]},y={points[0][1]},"
        f"n={len(steps)},d='{';'.join(steps)}'}}"
    )


def database(rt, *segments):
    return rt.run("return { version = 1, yards = 0, beltYards = 0, segments = {" + ",".join(segments) + "} }")


def lines_of(rt, db, *, place=PLACE, tolerance=0.5, most=1000, **view):
    """Road.lines for `db`: the pieces as ((x1, y1), (x2, y2), look) and the table it filled."""
    road = rt.ns.Road
    chapters = road.chapters(db)[0]
    box = rt.to_lua({**WIDE, **view, "tolerance": tolerance, "most": most})
    coefficients = rt.to_lua(place)
    out = rt.run(
        "local road, chapters, place, box = ...\n"
        "return road.lines(chapters, function(chapter) return chapter.c == 1 and place or nil end, box)",
        road, chapters, coefficients, box,
    )
    pieces = [((out.x1[i], out.y1[i]), (out.x2[i], out.y2[i]), out["from"][i]) for i in range(1, out.count + 1)]
    return pieces, out


def chapters_of(rt, db):
    chapters, whole = rt.ns.Road.chapters(db)
    assert whole is True
    return lua_list(chapters)


def path_of(rt, chapter):
    """The points of a chapter (read again from its segments if they are not held)."""
    xs, ys, _, count = rt.ns.Road.thinned(chapter, 0)
    assert count == chapter.count
    return [(xs[i], ys[i]) for i in range(1, count + 1)]


def same_table(rt, a, b) -> bool:
    return bool(rt.run("return rawequal(...)", a, b))


def test_the_road_is_read_into_chapters_that_carry_on_from_one_another(rt):
    db = database(
        rt,
        segment_of([(0, 0), (100, 0), (100, 100)], belt=True, level=12),
        segment_of([(100, 100), (300, 100)], mode="r", level=13),   # carries on where the first ends
        segment_of([(5000, 5000), (5100, 5000)]),                   # a jump away
        "{c=1,m='w',l=1,t=1,x=7,y=7,n=0}",                          # a single point is no road
        segment_of([(5100, 5000), (5100, 5200)], continent=0),      # the same numbers on another continent
    )
    first, second, third = chapters_of(rt, db)
    # The first two segments are one chapter: the point they share is there once.
    assert path_of(rt, first) == [(0, 0), (100, 0), (100, 100), (300, 100)]
    assert (first.minX, first.maxX, first.minY, first.maxY) == (0, 300, 0, 100)
    assert (first.startX, first.startY, first.endX, first.endY) == (0, 0, 300, 100)
    looks = [lua_plain(look) for look in lua_list(first.looks)]
    assert looks == [{"mode": "w", "belt": True, "level": 12}, {"mode": "r", "belt": False, "level": 13}]
    own = rt.ns.Road.thinned(first, 0)[2]
    assert [own[i] for i in range(2, 5)] == [1, 1, 2], "which look each piece of the chapter has"
    assert (first.joined, second.joined, third.joined) == (False, False, False)
    assert (second.c, third.c) == (1, 0) and path_of(rt, third) == [(5100, 5000), (5100, 5200)]


def test_a_long_run_of_road_is_cut_into_chapters_that_join(rt):
    road = rt.ns.Road
    segments, x = [], 0
    for _ in range(40):
        segments.append(segment_of([(x + 20 * i, 0) for i in range(51)]))
        x += 1000
    chapters = chapters_of(rt, database(rt, *segments))
    assert len(chapters) == 14, "2000 points in chapters of about 200 (three segments of fifty each)"
    assert all(chapter.count <= road.CHAPTER_POINTS for chapter in chapters)
    assert [chapter.joined for chapter in chapters] == [False] + [True] * 13
    assert sum(chapter.count for chapter in chapters) == 2001 + 13, "each begins on the last point of the one before"
    assert path_of(rt, chapters[1])[0] == path_of(rt, chapters[0])[-1]


def test_only_the_end_of_a_growing_road_is_read_again(rt):
    road = rt.ns.Road
    segments = [segment_of([(x, 0), (x + 20, 0), (x + 40, 0)]) for x in range(0, 40000, 2000)]   # twenty, apart
    db = database(rt, *segments)
    before = chapters_of(rt, db)
    assert len(before) == 20
    # The segment being recorded grows: its chapter is read anew, the others are the same tables.
    rt.run("local s = ...; s.n = 3; s.d = s.d .. ';1,20,0'", lua_list(db.segments)[19])
    after = chapters_of(rt, db)
    assert all(same_table(rt, a, b) for a, b in zip(before[:19], after[:19]))
    assert not same_table(rt, before[19], after[19]) and after[19].count == 4
    # A new segment that carries on is added to the last chapter; an empty one at the end comes and goes.
    rt.run(
        "local db = ...; local s = db.segments\n"
        "s[#s + 1] = { c = 1, m = 'r', l = 11, t = 5, x = 38060, y = 0, n = 1, d = '1,0,50' }\n"
        "s[#s + 1] = { c = 1, m = 'w', l = 11, t = 6, x = 38060, y = 50, n = 0 }",
        db,
    )
    grown = chapters_of(rt, db)
    assert len(grown) == 20 and path_of(rt, grown[19])[-1] == (38060, 50)
    rt.run("local db = ...; db.segments[#db.segments] = nil", db)
    assert len(chapters_of(rt, db)) == 20
    # Nothing changed: nothing is read again.
    assert all(same_table(rt, a, b) for a, b in zip(grown, chapters_of(rt, db)))


def test_a_long_road_can_be_read_a_little_at_a_time(rt):
    road = rt.ns.Road
    segments = [segment_of([(x, 0), (x + 20, 0)]) for x in range(0, 30000, 1000)]
    db = database(rt, *segments)
    calls = rt.run("local n = 0; return function() n = n + 1; return n % 7 == 0 end")
    rounds, whole = 0, False
    while not whole:
        chapters, whole = road.chapters(db, calls)
        rounds += 1
        assert rounds < 20
    assert rounds == 5 and len(lua_list(chapters)) == 30
    assert len(chapters_of(rt, db)) == 30


def test_damaged_segments_do_not_break_the_reading(rt):
    db = database(
        rt,
        segment_of([(0, 0), (100, 0)]),
        "{c=1,m='w',l=1,t=1,x=500,y=500,n=4,d='rubbish'}",
        "{c=1,m='w',l=1,t=1,x=900,y=900,n=3,d='1,10,0;1,10,0;oops;1,10,0'}",
    )
    chapters = chapters_of(rt, db)
    assert [path_of(rt, chapter) for chapter in chapters] == [[(0, 0), (100, 0)], [(900, 900), (910, 900), (920, 900), (930, 900)]]
    assert chapters_of(rt, rt.run("return { segments = 7 }")) == [] and chapters_of(rt, None) == []


def test_only_the_points_of_chapters_looked_at_of_late_are_held_in_memory(rt):
    """A long road keeps its coarse copies; the points themselves are read
    again from the saved segments when a map looks closely."""
    road = rt.ns.Road
    segments, x = [], 0
    for index in range(400):                                  # 400 chapters of 101 points, each a jump apart
        points = [(x + 20 * i, (index * 7 + i * i) % 50) for i in range(101)]
        segments.append(segment_of(points, level=1 + index // 10))
        x += 5000
    db = database(rt, *segments)
    chapters = chapters_of(rt, db)
    assert len(chapters) == 400
    held = [chapter for chapter in chapters if chapter.xs is not None]
    assert 0 < sum(chapter.count for chapter in held) <= road.POINTS_HELD + 101
    let_go = next(chapter for chapter in chapters if chapter.xs is None)
    assert let_go.ready is True and len(lua_list(let_go.rungs)) == road.LADDER_RUNGS, "its coarse copies were made first"
    # Asked for, the points are read again - the same points.
    index = chapters.index(let_go)
    expected = [(index * 5000 + 20 * i, (index * 7 + i * i) % 50) for i in range(101)]
    assert path_of(rt, let_go) == expected
    assert let_go.xs is not None
    # A map of all of it draws from the coarse copies and reads nothing again.
    held_before = [chapter.xs is not None for chapter in chapters]
    pieces, out = lines_of(rt, db, tolerance=5, most=3000)
    assert len(pieces) >= 400 and [chapter.xs is not None for chapter in chapters] == held_before
    # A close look at one place reads that chapter, and lets others go to make room.
    place = 200 * 500 + 100
    close, _ = lines_of(rt, db, tolerance=0.01, most=3000, left=place - 20, right=place + 20, top=-10, bottom=10)
    assert close and chapters[200].xs is not None
    assert sum(chapter.count for chapter in chapters if chapter.xs is not None) <= road.POINTS_HELD + 101


def test_a_chapter_read_again_is_what_it_was_when_the_segment_has_grown_since(rt):
    road = rt.ns.Road
    db = database(rt, segment_of([(0, 0), (20, 0), (40, 0)]))
    (chapter,) = chapters_of(rt, db)
    rt.run("local c = ...; c.xs, c.ys, c.own = nil, nil, nil", chapter)       # as when let go
    rt.run("local s = ...; s.n = 3; s.d = s.d .. ';1,20,0'", lua_list(db.segments)[0])
    assert path_of(rt, chapter) == [(0, 0), (20, 0), (40, 0)], "the list a window holds stays whole"
    (again,) = chapters_of(rt, db)
    assert path_of(rt, again) == [(0, 0), (20, 0), (40, 0), (60, 0)] and not same_table(rt, again, chapter)


def test_coarser_copies_of_a_chapter_stay_close_to_it(rt):
    """The ladder of thinned copies: each stays within its tolerance of the
    road, and they are made once."""
    road = rt.ns.Road
    points = [(round(20 * i), round(300 * math.sin(i / 9))) for i in range(1500)]
    (chapter,) = chapters_of(rt, database(rt, segment_of(points)))

    def distance_to_path(px, py, path):
        best = float("inf")
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
        return best

    counts = []
    for tolerance in (10, 30, 100, 400, 1600):
        xs, ys, own, count = road.thinned(chapter, tolerance)
        path = [(xs[i], ys[i]) for i in range(1, count + 1)]
        counts.append(count)
        assert path[0] == points[0] and path[-1] == points[-1]
        assert max(distance_to_path(px, py, path) for px, py in points[::3]) <= tolerance
        assert all(own[i] == 1 for i in range(1, count + 1))
    assert counts[0] == 1500, "below the first rung the chapter itself is used"
    assert counts == sorted(counts, reverse=True) and counts[-1] < 100
    xs, _, _, _ = road.thinned(chapter, 400)
    assert same_table(rt, xs, road.thinned(chapter, 400)[0]), "a copy is made once"
    road.prepare(chapter)
    assert len(lua_list(chapter.rungs)) >= 4 and chapter.ready is True


def test_a_coarse_piece_has_the_look_of_most_of_the_road_it_stands_for(rt):
    road = rt.ns.Road
    db = database(
        rt,
        segment_of([(0, 0), (20, 1), (40, 0)], level=10),
        segment_of([(40, 0), (60, 1), (80, 0), (100, 1), (2000, 0)], level=20, belt=True),
        segment_of([(2000, 0), (2020, 1), (2040, 600)], level=30),
    )
    (chapter,) = chapters_of(rt, db)
    xs, ys, own, count = road.thinned(chapter, 30)
    path = [(xs[i], ys[i]) for i in range(1, count + 1)]
    assert path == [(0, 0), (2000, 0), (2040, 600)] or path == [(0, 0), (2020, 1), (2040, 600)]
    looks = lua_list(chapter.looks)
    assert looks[own[2] - 1].level == 20 and looks[own[3] - 1].level == 30


def test_lines_follow_the_road_and_cut_bends_too_small_to_see(rt):
    db = database(rt, segment_of([(0, 0), (500, 0), (500, 2), (1000, 2), (1000, 800)], belt=True))
    pieces, out = lines_of(rt, db, tolerance=0.5)
    # A tenth of a unit a yard: the two-yard jog is 0.2 units, under the tolerance.
    assert [(a, b) for a, b, _ in pieces] == [((0, 0), (100, 0.2)), ((100, 0.2), (100, 80))]
    assert all(look.belt is True for _, _, look in pieces)
    assert (out.count, out.cut, out.passes) == (2, 0, 1) and out.tolerance == 0.5
    # Drawn finer, the jog is there.
    fine, _ = lines_of(rt, db, tolerance=0.05)
    assert len(fine) == 4


def test_lines_out_of_sight_are_left_out(rt):
    db = database(
        rt,
        segment_of([(0, 0), (1000, 0)]),
        segment_of([(0, 5000), (1000, 5000)]),
        segment_of([(0, 100), (300, 100), (600, 100), (900, 100), (1200, 100)], continent=0),
    )
    pieces, _ = lines_of(rt, db, left=-10, top=-10, right=200, bottom=50)
    assert [(a, b) for a, b, _ in pieces] == [((0, 0), (100, 0))]
    # A stretch that crosses the view is drawn where it does: only the piece in sight.
    zig = database(rt, segment_of([(0, 0), (1000, 300), (2000, 0), (3000, 300), (4000, 0)]))
    pieces, _ = lines_of(rt, zig, left=190, top=-10, right=210, bottom=40)
    assert [(a, b) for a, b, _ in pieces] == [((100, 30), (200, 0)), ((200, 0), (300, 30))]
    # (The stretch on continent 0 is not on this map at all.)
    everything, _ = lines_of(rt, db)
    assert len(everything) == 2


def test_a_road_in_thousands_of_short_segments_is_drawn_as_a_few_lines(rt):
    """Each is a segment of its own (the level, the mount, the belt
    changed); together they are one long road. Too short to see one by one,
    they are joined - else there would be a line each, without end."""
    segments, x = [], 0
    for index in range(5000):
        segments.append(segment_of([(x, 0), (x + 4, 0)], belt=index % 2 == 0, level=1 + index // 100))
        x += 4
    db = database(rt, *segments)
    pieces, out = lines_of(rt, db, tolerance=0.5, most=300)
    # 20000 yards are 2000 units, in a straight line: a handful of pieces do.
    assert 1 <= len(pieces) <= 300 and out.cut == 0
    assert pieces[0][0] == (0, 0) and pieces[-1][1] == (2000, 0)
    assert all(a[1] == b[0] for a, b in zip([p[:2] for p in pieces], [p[:2] for p in pieces][1:])), "one unbroken line"
    assert out.passes <= 3
    # A road in one piece fits the smallest budget.
    few, out = lines_of(rt, db, tolerance=0.5, most=3)
    assert len(few) <= 3 and out.cut == 0
    # Pieces of road that do not join (jumps) cannot be made fewer than they are: what does
    # not fit is left out and counted, and drawing more coarsely is given up when it stops helping.
    apart = database(rt, *[segment_of([(i * 1000, 0), (i * 1000 + 600, 0)]) for i in range(50)])
    cut, out = lines_of(rt, apart, tolerance=0.5, most=10)
    assert len(cut) == out.count == 10 and out.cut == 40
    assert out.passes <= 3, "not sixteen passes over the same fifty pieces"


def test_a_winding_road_far_too_long_for_the_budget_is_drawn_from_its_coarse_copies(rt):
    """Twenty thousand points winding over a stretch of country, seen
    whole: the lines come from the coarse copies of its chapters, in a pass
    or two - not from thinning all twenty thousand points."""
    road = rt.ns.Road
    points, x, y, heading = [(0, 0)], 0.0, 0.0, 0.0
    for index in range(20000):
        heading += 0.35 * math.sin(index / 7.0) + 0.002 * math.sin(index / 900.0)
        x, y = x + 20 * math.cos(heading), y + 20 * math.sin(heading)
        points.append((round(x), round(y)))
    segments = [segment_of(points[i:i + 201], level=1 + i // 400) for i in range(0, 20000, 200)]
    db = database(rt, *segments)
    pieces, out = lines_of(rt, db, tolerance=0.05, most=1000)
    assert 50 < len(pieces) <= 1000 and out.cut == 0
    assert out.passes <= 2 and out.tolerance > 0.05
    assert pieces[0][0] == (0, 0)
    assert pieces[-1][1] == pytest.approx((points[-1][0] / 10, points[-1][1] / 10)), "to the very end"
    # Drawn again (the map was moved): the coarse copies are there, and little is thinned.
    thinned = rt.run(
        "local road = ...; local seen = 0; local inner = road.simplify\n"
        "road.simplify = function(xs, ys, count, ...) seen = seen + count; return inner(xs, ys, count, ...) end\n"
        "return function() return seen end",
        road,
    )
    lines_of(rt, db, tolerance=0.05, most=1000)
    assert thinned() < 3000, "points looked at for a drawing of a thousand pieces"
    # Seen close up, a corner of it is drawn from the points themselves.
    x0, y0 = points[300][0] / 10, points[300][1] / 10
    close, out = lines_of(rt, db, tolerance=0.05, most=1000, left=x0 - 20, top=y0 - 20, right=x0 + 20, bottom=y0 + 20)
    assert close and out.tolerance == 0.05 and out.passes == 1


def test_close_up_on_a_road_travelled_many_times_it_is_drawn_from_its_points(rt):
    """Thirty journeys along the same four thousand yards, seen close up at
    one place. What decides how finely to draw is the road in view - not
    the length of the chapters that pass through it."""
    segments = []
    for trip in range(30):
        segments.append(segment_of([(20 * i, trip * 3 + (i * 7 + trip) % 5) for i in range(200)]))
    db = database(rt, *segments)
    pieces, out = lines_of(rt, db, tolerance=0.05, most=1000, left=195, right=205, top=-5, bottom=20)
    assert pieces and out.tolerance == 0.05 and out.passes == 1, "from the points themselves, true to a pixel"
    assert max(math.dist(a, b) for a, b, _ in pieces) < 5, "every little bend of it"
    # Seen whole, the same road is drawn from coarse copies.
    whole, out = lines_of(rt, db, tolerance=0.05, most=1000)
    assert out.tolerance > 0.05 and len(whole) <= 1000


def test_a_speck_of_road_far_from_any_other_is_not_drawn(rt):
    db = database(rt, segment_of([(0, 0), (3, 0)]), segment_of([(5000, 0), (9000, 0)]))
    pieces, _ = lines_of(rt, db, tolerance=0.5)
    assert [(a, b) for a, b, _ in pieces] == [((500, 0), (900, 0))], "0.3 units long, with nothing to join"


def test_a_joined_piece_takes_the_look_of_its_longest_part(rt):
    db = database(
        rt,
        segment_of([(0, 0), (2, 0)], level=10),
        segment_of([(2, 0), (4, 0)], level=20, belt=True),
        segment_of([(4, 0), (10, 0)], level=30),
        segment_of([(10, 0), (5000, 0)], level=40),
    )
    pieces, _ = lines_of(rt, db, tolerance=0.5)
    assert [(a, b, look.level) for a, b, look in pieces] == [((0, 0), (1, 0), 30), ((1, 0), (500, 0), 40)]
    # ... and where the parts are told apart, each has its own.
    fine, _ = lines_of(rt, db, tolerance=0.01)
    assert [look.level for _, _, look in fine] == [10, 20, 30, 40]


def test_lines_turn_with_the_map(rt):
    """The game's maps have world x running up the map and world y to the left."""
    db = database(rt, segment_of([(0, 0), (1000, 0), (1000, -500)]))
    pieces, _ = lines_of(rt, db, place=[0.0, -0.1, 50.0, -0.1, 0.0, 200.0])
    assert [(a, b) for a, b, _ in pieces] == [((50, 200), (50, 100)), ((50, 100), (100, 100))]


# ---- with the game -----------------------------------------------------------------------------


def run_north(rt, yards, seconds, start=None):
    """Move the character `yards` to the north over `seconds`."""
    looks = int(seconds * 4)
    if start is None:
        start = getattr(rt, "north", -600)     # (where the mock's character begins)
    for index in range(1, looks + 1):
        rt.set(worldX=start + yards * index / looks, speed=7.0)
        rt.advance(0.25)
    rt.north = start + yards


def char_db(rt):
    rt.ns.RoadRecorder.Flush()
    db = lua_plain(rt.global_("WalkaCharDB"))
    db["segments"] = list(db["segments"].values()) if isinstance(db["segments"], dict) else db["segments"]
    return db


def test_the_recorder_follows_the_character(rt):
    run_north(rt, 300, 43)
    db = char_db(rt)
    assert db["guid"] == "Player-4321-0ABCDEF1" and db["key"] == "Tester-Azeroth" and db["version"] == 1
    (segment,) = db["segments"]
    assert segment["c"] == 1 and segment["m"] == "w" and segment["l"] == 17
    assert segment["n"] >= 13 and db["yards"] == pytest.approx(300, abs=25)
    assert rt.ns.RoadRecorder.problem is None


def test_a_hidden_name_never_replaces_the_one_the_road_is_saved_under(rt, make_rt):
    # (The companion finds a character's road by this name: it is what the
    # journey's CHAR_KEY is a hash of.)
    run_north(rt, 100, 15)
    rt.set(nameSecret=True)
    fresh = rt.reload()                         # the game hides the name at this login
    run_north(fresh, 100, 15, start=rt.north)
    assert char_db(fresh)["key"] == "Tester-Azeroth"
    assert fresh.ns.RoadRecorder.problem is None and fresh.find_secrets() == []

    # A first login with the name hidden: no name until the game says.
    hidden = make_rt(state={"nameSecret": True})
    run_north(hidden, 100, 15)
    assert "key" not in char_db(hidden)
    hidden.set(nameSecret=False)
    hidden.advance(1)
    assert char_db(hidden)["key"] == "Tester-Azeroth"
    assert hidden.ns.RoadRecorder.problem is None and hidden.find_secrets() == []


def test_nothing_is_recorded_inside_an_instance_and_the_road_resumes_outside(rt):
    run_north(rt, 100, 15)
    rt.set(instance=True)
    run_north(rt, 500, 60)
    rt.set(instance=False, worldX=3000, worldY=-9000)
    run_north(rt, 100, 15, start=3000)
    db = char_db(rt)
    assert len(db["segments"]) == 2
    assert db["yards"] == pytest.approx(200, abs=30)


def test_the_road_survives_a_reload_and_carries_on(rt):
    run_north(rt, 100, 15)
    before = char_db(rt)["yards"]
    fresh = rt.reload()
    # (Logging out stores the last few yards of the open stretch.)
    assert before <= lua_plain(fresh.global_("WalkaCharDB"))["yards"] <= 101
    run_north(fresh, 100, 15, start=rt.north)
    db = char_db(fresh)
    assert len(db["segments"]) == 2 and db["yards"] == pytest.approx(200, abs=30)


def test_mounts_flights_and_the_treadmill_are_told_apart(rt):
    run_north(rt, 60, 9)
    rt.set(mounted=True)
    run_north(rt, 60, 5)
    rt.set(mounted=False, taxi=True)
    run_north(rt, 600, 20)
    rt.set(taxi=False)
    rt.slash("/walka start")
    run_north(rt, 300, 43)
    db = char_db(rt)
    modes = [segment["m"] for segment in db["segments"]]
    assert modes[:3] == ["w", "r", "t"] and modes[-1] == "w"
    assert db["segments"][-1].get("b") == 1
    assert 150 <= db["beltYards"] <= 300


def test_hidden_positions_record_nothing_and_raise_nothing(rt):
    rt.set(positionSecret=True)
    run_north(rt, 300, 43)
    assert char_db(rt)["segments"] == []
    assert rt.ns.RoadRecorder.problem is None
    assert rt.find_secrets() == []


def test_recording_can_be_switched_off(rt):
    rt.ns.RoadRecorder.SetEnabled(False)
    run_north(rt, 300, 43)
    assert char_db(rt)["segments"] == []


def test_an_error_in_the_recorder_does_not_stop_the_count(rt):
    rt.run("local road = ... road.Recorder.sample = function() error('boom') end", rt.ns.Road)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(1)
    before = rt.walka.session.seconds
    rt.advance(5)
    assert rt.walka.session.seconds > before, "the session counts on"
    assert "boom" in rt.ns.RoadRecorder.problem


# ---- to the companion ------------------------------------------------------------------------------


def test_the_companion_reads_the_road_out_of_the_saved_file(rt):
    """The road has one way out of the game: the file the game writes at a
    logout or /reload, which the companion reads for the picture."""
    run_north(rt, 300, 43)
    rt.set(taxi=True)
    run_north(rt, 600, 20)
    rt.set(taxi=False)
    rt.slash("/walka start")
    began = float(rt.game.clock)
    run_north(rt, 300, 43)
    walked = [(began, float(rt.game.clock))]   # when the app measured the belt moving
    db = char_db(rt)
    # (What the game would write, by the mock client's own writer.)
    file = b"\r\nWalkaCharDB = " + rt.M.serialize("WalkaCharDB").encode("utf-8") + b"\r\n"
    road = companion_road.decode(savedvars.parse(file)["WalkaCharDB"])
    assert road is not None and not road.truncated
    # The companion knows the character by a hash of its "Name-Realm", as it
    # knows its journey (read from the account's saved file).
    assert road.id == companion_road.character_id("Tester-Azeroth")
    assert [stretch.mode for stretch in road.stretches] == [segment["m"] for segment in db["segments"]]
    assert [len(stretch.points) for stretch in road.stretches] == [segment["n"] + 1 for segment in db["segments"]]
    api = road.to_api(1_800_000_000.0, walked=walked)
    # The same yards the addon counts (it measures as it goes, the companion from the points).
    assert api["yards"] + api["flight_yards"] == pytest.approx(db["yards"], abs=6)
    # Walked on the treadmill: what the app measured, which here is the
    # time of the session the player started.
    assert api["belt_yards"] == pytest.approx(db["beltYards"], abs=6)
    assert road.to_api(1_800_000_000.0)["belt_yards"] == 0, "and nothing without a measured walk"
    assert api["start"]["c"] == 1 and api["end"]["c"] == 1
    assert api["end"]["x"] == pytest.approx(rt.north, abs=20)
    assert {path["style"] for entry in api["continents"] for path in entry["paths"]} == {"road", "flight", "walked"}


def test_the_starting_isle_of_the_skyborne_reaches_the_companion_with_its_name(rt):
    """Zephras Isle is no continent of Azeroth. From the game to the
    picture: the recorder notes which map shows the isle and what the game
    calls it, the game writes that with the road, and the companion gives
    the land its name and a sketch of the ground the road has charted."""
    rt.set(continent=2600, worldX=100, worldY=300, speed=0.0)
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    rt.advance(0.5)
    run_north(rt, 400, 57, start=100)
    rt.set(continent=1, worldX=-600, worldY=-4200, speed=0.0)   # the airship to Mulgore
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    rt.advance(0.5)
    run_north(rt, 300, 43, start=-600)
    db = char_db(rt)
    assert db["maps"] == {2600: {"id": 2500, "name": "Zephras Isle"}, 1: {"id": 1414, "name": "Kalimdor"}}
    assert [segment["c"] for segment in db["segments"]] == [2600, 1]

    file = b"\r\nWalkaCharDB = " + rt.M.serialize("WalkaCharDB").encode("utf-8") + b"\r\n"
    road = companion_road.decode(savedvars.parse(file)["WalkaCharDB"])
    assert road is not None and road.names == {2600: "Zephras Isle", 1: "Kalimdor"}
    api = road.to_api(1_800_000_000.0)
    by_id = {entry["id"]: entry for entry in api["continents"]}
    assert by_id[2600]["name"] == "Zephras Isle" and by_id[1]["name"] == "Kalimdor"
    assert api["start"]["c"] == 2600 and api["end"]["c"] == 1 and len(api["crossings"]) == 1
    # The page has a map of Kalimdor; of the isle, the ground along the road.
    assert "land" not in by_id[1]
    min_x, min_y, max_x, max_y = by_id[2600]["land"]["bounds"]
    assert min_x < 100 and max_x > 500 and min_y < 300 < max_y


# ---- with the session the addon starts by itself (the default) -------------------------------------


def test_the_automatic_session_marks_no_road_walked_on_the_treadmill(make_rt):
    """The automatic session runs from login to logout, with or without a
    treadmill: it is not the walker saying the belt is moving."""
    rt = make_rt(auto_session=True)
    rt.tick()
    assert rt.walka.IsActive(rt.walka) and rt.walka.session.auto is True
    run_north(rt, 300, 43)
    db = char_db(rt)
    assert db["segments"] and all("b" not in segment for segment in db["segments"])
    assert db["beltYards"] == 0
    # Nor does the picture claim any, without a walk the app measured.
    file = b"\r\nWalkaCharDB = " + rt.M.serialize("WalkaCharDB").encode("utf-8") + b"\r\n"
    api = companion_road.decode(savedvars.parse(file)["WalkaCharDB"]).to_api(1_800_000_000.0)
    assert api["yards"] > 250 and api["belt_yards"] == 0


def test_with_the_automatic_session_the_picture_marks_what_the_app_measured(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick()
    run_north(rt, 300, 43)
    began = rt.game.clock
    run_north(rt, 300, 43)
    walked = [(float(began), float(rt.game.clock))]   # the belt moved for the second half
    run_north(rt, 300, 43)
    char_db(rt)
    file = b"\r\nWalkaCharDB = " + rt.M.serialize("WalkaCharDB").encode("utf-8") + b"\r\n"
    api = companion_road.decode(savedvars.parse(file)["WalkaCharDB"]).to_api(1_800_000_000.0, walked=walked)
    assert api["yards"] == pytest.approx(900, abs=40)
    assert api["belt_yards"] == pytest.approx(300, abs=30)


def test_a_road_too_long_for_the_saved_file_has_its_oldest_stretches_thinned(rt):
    """The saved file must not grow without end: past the most points the
    addon keeps, the oldest stretches keep every other point, and the
    newest are left as they are. The road still begins and ends where it did."""
    road = rt.ns.Road
    rt.run("local road = ... road.MAX_POINTS = 600", road)
    steps = ";".join(["3,20,0"] * 99)
    segments = ",".join(f"{{c=1,m='w',l=1,t={T0 + i * 1000},x={i * 5000},y=0,n=99,d='{steps}'}}" for i in range(10))
    db = rt.run("local road = ... return road.database({ version = 1, yards = 0, beltYards = 0, segments = {"
                + segments + "} }, nil, 'Tester-Azeroth')", road)
    kept = lua_plain(db)["segments"]
    kept = list(kept.values()) if isinstance(kept, dict) else kept
    assert len(kept) == 10
    assert sum(segment["n"] + 1 for segment in kept) <= 600
    assert kept[-1]["n"] == 99 and kept[0]["n"] < 99
    for segment in kept:
        total = sum(int(part.split(",")[1]) for part in segment["d"].split(";"))
        assert total == 99 * 20, "every stretch ends where it ended"
        assert sum(int(part.split(",")[0]) for part in segment["d"].split(";")) == 99 * 3
