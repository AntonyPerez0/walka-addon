"""Land drawn from the road itself (sketch.py): what the Share picture puts
behind the road on a land the page has no map of - the Skyborne's Zephras
Isle, which is no continent of Azeroth."""

from __future__ import annotations

import itertools
import json
import math
import random

import pytest

from walka import sketch

Point = tuple[float, float]


def corners(land: dict) -> list[Point]:
    return list(zip(land["points"][0::2], land["points"][1::2], strict=True))


def area(ring: list[Point]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True))) / 2


def triangles(land: dict) -> list[tuple[Point, Point, Point]]:
    at = corners(land)
    faces = land["faces"]
    return [(at[a], at[b], at[c]) for a, b, c in zip(faces[0::4], faces[1::4], faces[2::4], strict=True)]


def on_land(land: dict, x: float, y: float) -> bool:
    def side(p: Point, q: Point) -> float:
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])

    for a, b, c in triangles(land):
        signs = (side(a, b), side(b, c), side(c, a))
        if all(value >= -1e-6 for value in signs) or all(value <= 1e-6 for value in signs):
            return True
    return False


def crossing(p: Point, q: Point, r: Point, s: Point) -> bool:
    def side(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    return side(p, q, r) * side(p, q, s) < 0 and side(r, s, p) * side(r, s, q) < 0


def wander(seed: int, steps: int, *, tall: float = 1100, wide: float = 1700, stride: float = 40) -> list[Point]:
    """Errands all over an island about the size of a starting zone."""
    rng = random.Random(seed)
    out = [(0.0, 0.0)]
    for _ in range(steps):
        x, y = out[-1]
        out.append(
            (max(-tall, min(tall, x + rng.uniform(-stride, stride))), max(-wide, min(wide, y + rng.uniform(-stride, stride))))
        )
    return out


FIRST_STEPS = [[(0.0, 0.0), (20.0, 5.0)]]
A_WALK = [[(100, 300), (500, 300), (500, -200)]]
ROUND_THE_LAKE = [[(600 * math.cos(turn / 40 * 2 * math.pi), 800 * math.sin(turn / 40 * 2 * math.pi)) for turn in range(41)]]
THE_ISLE = [wander(3, 4000)]
TWO_PLACES = [[(0, 0), (300, 0)], [(5000, 5000), (5300, 5200)]]
ACROSS_A_WORLD = [wander(5, 3000, tall=9000, wide=5000, stride=900)]
ROADS = {
    "first steps": FIRST_STEPS,
    "a walk": A_WALK,
    "round the lake": ROUND_THE_LAKE,
    "the isle": THE_ISLE,
    "two places": TWO_PLACES,
    "across a world": ACROSS_A_WORLD,
}


@pytest.mark.parametrize("name", ROADS)
def test_the_sketch_is_a_map_like_the_shipped_ones(name: str):
    """The same four things as an entry of lands.js, as sound as those."""
    land = sketch.land(ROADS[name])
    assert set(land) == {"bounds", "points", "coast", "faces"}
    assert all(isinstance(value, int) for value in land["points"])
    at = corners(land)
    assert len(set(at)) == len(at) >= 3  # no corner twice
    xs, ys = [x for x, _y in at], [y for _x, y in at]
    assert land["bounds"] == [min(xs), min(ys), max(xs), max(ys)]
    # The coast: a plain outline, one ring for each piece of land.
    used: set[int] = set()
    edges = []
    for ring in land["coast"]:
        assert len(ring) >= 3 and len(set(ring)) == len(ring)
        assert all(isinstance(corner, int) and 0 <= corner < len(at) for corner in ring)
        assert not used & set(ring)  # pieces of land share no corner
        used |= set(ring)
        edges += [(at[a], at[b]) for a, b in zip(ring, ring[1:] + ring[:1], strict=True)]
    for (p, q), (r, s) in itertools.combinations(edges, 2):
        assert not crossing(p, q, r, s), (p, q, r, s)
    # The facets: together they are the land - no gap, nothing twice, every corner used.
    faces = land["faces"]
    assert len(faces) % 4 == 0 and faces
    seen = set()
    total = 0.0
    for a, b, c, shade in zip(faces[0::4], faces[1::4], faces[2::4], faces[3::4], strict=True):
        assert all(isinstance(corner, int) and 0 <= corner < len(at) for corner in (a, b, c))
        assert isinstance(shade, int) and 0 <= shade <= 9
        assert len({a, b, c}) == 3 and tuple(sorted((a, b, c))) not in seen
        seen.add(tuple(sorted((a, b, c))))
        size = area([at[a], at[b], at[c]])
        assert size > 100, "a facet with no area"
        total += size
    assert total == pytest.approx(sum(area([at[corner] for corner in ring]) for ring in land["coast"]), rel=1e-9)
    assert {corner for face in seen for corner in face} == set(range(len(at)))
    # Small enough to send to the page with the road.
    assert len(json.dumps(land)) < 40_000


@pytest.mark.parametrize("name", ROADS)
def test_the_road_runs_on_the_land_it_charted(name: str):
    land = sketch.land(ROADS[name])
    min_x, min_y, max_x, max_y = land["bounds"]
    for line in ROADS[name]:
        # (Of a very long road, every eighth stretch: enough to tell.)
        every = 8 if len(line) > 500 else 1
        for (ax, ay), (bx, by) in list(zip(line, line[1:], strict=False))[::every]:
            pieces = max(1, int(math.hypot(bx - ax, by - ay) / 25))
            for step in range(pieces + 1):
                x, y = ax + (bx - ax) * step / pieces, ay + (by - ay) * step / pieces
                assert on_land(land, x, y), (name, x, y)
                # ... and not at its very edge: there is ground to either side.
                assert min_x < x < max_x and min_y < y < max_y


def test_the_land_is_a_little_ground_to_either_side_of_the_road():
    land = sketch.land(A_WALK)
    min_x, min_y, max_x, max_y = land["bounds"]
    # 400 yards north, 500 east: the land is that and a margin - not a continent.
    assert 500 < max_x - min_x < 800 and 600 < max_y - min_y < 900
    assert len(land["coast"]) == 1 and 20 <= len(land["faces"]) // 4 <= 200
    # A character that has only just set out stands on a small piece of ground.
    first = sketch.land(FIRST_STEPS)
    min_x, min_y, max_x, max_y = first["bounds"]
    assert 120 < max_x - min_x < 400 and 120 < max_y - min_y < 400
    assert len(first["faces"]) // 4 >= 6


def test_what_the_road_goes_around_is_land_too():
    """Nobody walks around a lake to chart its shore: an island walked
    round is an island, with no hole in it."""
    land = sketch.land(ROUND_THE_LAKE)
    assert len(land["coast"]) == 1
    assert on_land(land, 0, 0)
    at = corners(land)
    assert area([at[corner] for corner in land["coast"][0]]) > math.pi * 600 * 800


def test_places_far_apart_are_islands_of_their_own():
    land = sketch.land(TWO_PLACES)
    assert len(land["coast"]) == 2
    assert on_land(land, 150, 0) and on_land(land, 5150, 5100) and not on_land(land, 2500, 2500)


def test_a_walked_isle_is_low_poly_with_light_and_shadow():
    land = sketch.land(THE_ISLE)
    count = len(land["faces"]) // 4
    assert 100 <= count <= 500  # facets, not pixels
    assert 30 <= len(land["coast"][0]) <= 120
    assert len(set(land["faces"][3::4])) >= 8
    # A road across a whole world is not a thousand times that.
    assert len(sketch.land(ACROSS_A_WORLD)["faces"]) // 4 < 1500


def test_the_sketch_does_not_jump_about_as_the_road_grows():
    """The honeycomb is the world's, not the road's: the ground charted
    yesterday has the same facets today."""
    before = sketch.land([[(100, 300), (500, 300)]])
    after = sketch.land([[(100, 300), (500, 300), (560, 240)]])
    assert set(triangles(before)) <= set(triangles(after))
    assert sketch.land(A_WALK) == sketch.land(A_WALK)
    assert sketch.land([list(reversed(A_WALK[0]))])["points"] == sketch.land(A_WALK)["points"]


def test_no_road_no_land():
    assert sketch.land([]) is None
    assert sketch.land([[]]) is None
    assert sketch.land([[(math.nan, 0.0), (1.0, 1.0)]]) is None
    assert sketch.land([[(0.0, 0.0)]]) is not None  # a place stood on is ground
    # A road no map could show (the numbers of a damaged file) is not charted.
    assert sketch.land([[(-900_000, -900_000), (900_000, 900_000)]]) is None
