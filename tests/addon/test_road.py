"""The road: Core/Road.lua (which points of a character's path are kept, and
how) and RoadRecorder.lua (asking the game where the character is)."""

from __future__ import annotations

import math
import re

import pytest

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
    assert fresh == {"version": 1, "guid": "Player-1-AAAA", "key": "Tester-Azeroth", "yards": 0, "beltYards": 0, "segments": {}}
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


def test_an_error_in_the_recorder_does_not_stop_the_beacon(rt):
    rt.run("local road = ... road.Recorder.sample = function() error('boom') end", rt.ns.Road)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(1)
    before = rt.live_frame().seq
    rt.advance(5)
    assert rt.live_frame().seq != before, "the beacon ticks on"
    assert "boom" in rt.ns.RoadRecorder.problem
