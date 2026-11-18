"""The low-poly maps of the continents (static/lands.js, made by
tools/gen_lands.py): sound data, the right way round, with the places a
character walks on the land and the open sea off it."""

from __future__ import annotations

import itertools
import json
import math
from typing import Any

import pytest

from walka import web
from walka.journey import DEMO_KEY
from walka.road import CONTINENT_NAMES, demo_road

KALIMDOR, EASTERN_KINGDOMS = 1, 0


def load() -> dict[int, dict[str, Any]]:
    text = (web.STATIC_DIR / "lands.js").read_text("utf-8")
    data = json.loads(text.split("export const LANDS = ", 1)[1].rstrip().rstrip(";"))
    return {int(key): land for key, land in data.items()}


LANDS = load()


def corners(land: dict[str, Any]) -> list[tuple[int, int]]:
    points = land["points"]
    return list(zip(points[0::2], points[1::2], strict=True))


def area(ring: list[tuple[float, float]]) -> float:
    return abs(sum(ax * by - bx * ay for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1], strict=True))) / 2


def on_land(continent: int, x: float, y: float) -> bool:
    """Is the place (world yards: x north, y west) inside the drawn outline?"""
    land = LANDS[continent]
    at = corners(land)
    for ring in land["coast"]:
        inside = False
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            (ax, ay), (bx, by) = at[a], at[b]
            if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
                inside = not inside
        if inside:
            return True
    return False


def crossing(p, q, r, s) -> bool:
    """Do the segments pq and rs cross (not merely touch at an end)?"""

    def side(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    return side(p, q, r) * side(p, q, s) < 0 and side(r, s, p) * side(r, s, q) < 0


# ---- sound data ---------------------------------------------------------------------------------


def test_the_two_continents_of_the_game_are_there():
    assert set(LANDS) == set(CONTINENT_NAMES) == {KALIMDOR, EASTERN_KINGDOMS}
    for continent, land in LANDS.items():
        assert land["name"] == CONTINENT_NAMES[continent]
        assert set(land) == {"name", "bounds", "points", "coast", "faces"}


@pytest.mark.parametrize("continent", [KALIMDOR, EASTERN_KINGDOMS])
def test_the_numbers_are_whole_and_the_bounds_are_theirs(continent: int):
    land = LANDS[continent]
    assert all(isinstance(value, int) and value % 10 == 0 for value in land["points"])
    at = corners(land)
    assert len(set(at)) == len(at) >= 100  # no corner twice
    xs, ys = [x for x, _y in at], [y for _x, y in at]
    assert land["bounds"] == [min(xs), min(ys), max(xs), max(ys)]
    # Small enough to ship with the page, detailed enough to be told apart.
    assert len(json.dumps(land)) < 12_000


@pytest.mark.parametrize("continent", [KALIMDOR, EASTERN_KINGDOMS])
def test_the_coast_is_a_simple_outline(continent: int):
    land = LANDS[continent]
    at = corners(land)
    used: set[int] = set()
    edges = []
    for ring in land["coast"]:
        assert len(ring) >= 3 and len(set(ring)) == len(ring)
        assert all(isinstance(corner, int) and 0 <= corner < len(at) for corner in ring)
        assert not used & set(ring)  # islands share no corner
        used |= set(ring)
        assert area([at[corner] for corner in ring]) > 1.0e6  # nothing smaller than a village
        edges += [(at[a], at[b]) for a, b in zip(ring, ring[1:] + ring[:1], strict=True)]
    # No edge of the coast crosses another (of its own island or the next one).
    for (p, q), (r, s) in itertools.combinations(edges, 2):
        assert not crossing(p, q, r, s), (p, q, r, s)
    # Low-poly: a hundred corners or so, not a thousand.
    assert 40 <= len(used) <= 160


@pytest.mark.parametrize("continent", [KALIMDOR, EASTERN_KINGDOMS])
def test_the_facets_cover_the_land_exactly(continent: int):
    land = LANDS[continent]
    at = corners(land)
    faces = land["faces"]
    assert len(faces) % 4 == 0 and len(faces) // 4 >= 150
    total = 0.0
    shades = set()
    seen = set()
    for a, b, c, shade in zip(faces[0::4], faces[1::4], faces[2::4], faces[3::4], strict=True):
        assert all(isinstance(corner, int) and 0 <= corner < len(at) for corner in (a, b, c))
        assert isinstance(shade, int) and 0 <= shade <= 9
        assert len({a, b, c}) == 3 and tuple(sorted((a, b, c))) not in seen
        seen.add(tuple(sorted((a, b, c))))
        size = area([at[a], at[b], at[c]])
        assert size > 100, "a facet with no area"
        total += size
        shades.add(shade)
        # Each facet lies on the land.
        cx, cy = (at[a][0] + at[b][0] + at[c][0]) / 3, (at[a][1] + at[b][1] + at[c][1]) / 3
        assert on_land(continent, cx, cy)
    # Together they are the land: no gap, nothing twice.
    outline = sum(area([at[corner] for corner in ring]) for ring in land["coast"])
    assert total == pytest.approx(outline, rel=1e-6)
    # Every corner is used, and the relief has light and shadow in it.
    assert {corner for face in seen for corner in face} == set(range(len(at)))
    assert len(shades) >= 8


# ---- the right shape, the right way round -----------------------------------------------------------


def test_the_continents_have_the_size_they_have_in_the_game():
    for continent, tall, wide in ((KALIMDOR, 22_000, 12_200), (EASTERN_KINGDOMS, 18_900, 8_400)):
        min_x, min_y, max_x, max_y = LANDS[continent]["bounds"]
        assert max_x - min_x == pytest.approx(tall, rel=0.05)
        assert max_y - min_y == pytest.approx(wide, rel=0.06)


def test_teldrassil_is_an_island_off_the_north_west_of_kalimdor():
    land = LANDS[KALIMDOR]
    at = corners(land)
    mainland, island = sorted(land["coast"], key=lambda ring: area([at[corner] for corner in ring]), reverse=True)
    assert len(land["coast"]) == 2
    xs = [at[corner][0] for corner in island]
    ys = [at[corner][1] for corner in island]
    # North of everything else, and on the western (larger y) side.
    assert min(xs) > max(at[corner][0] for corner in mainland) - 200
    assert min(ys) > 0 and max(ys) < 3_500
    assert not on_land(KALIMDOR, 7_900, 1_100)  # the water between it and Darkshore


# Where a character walks. World yards as the game gives them (x to the north,
# y to the west): towns, flight masters and harbours of every zone, the ones
# right on the coast above all.
PLACES = {
    KALIMDOR: {
        "Orgrimmar": (1676, -4315),
        "Thunder Bluff": (-1277, 124),
        "Darnassus": (9947, 2482),
        "Shadowglen": (10311, 832),
        "Dolanaar": (9850, 960),
        "Rut'theran Village": (8640, 880),
        "Auberdine": (6439, 614),
        "Astranaar": (2745, -372),
        "Nighthaven": (7795, -2400),
        "Everlook": (6726, -4645),
        "Talrendis Point": (2717, -3880),
        "Razor Hill": (304, -4735),
        "Sen'jin Village": (-825, -4920),
        "The Crossroads": (-455, -2652),
        "Ratchet": (-956, -3754),
        "Nijel's Point": (164, 1325),
        "Shadowprey Village": (-1658, 3098),
        "Feathermoon Stronghold": (-4370, 3340),
        "Camp Mojache": (-4420, 200),
        "Theramore": (-3680, -4375),
        "Freewind Post": (-5440, -2440),
        "Marshal's Refuge": (-6160, -1090),
        "Cenarion Hold": (-6815, 730),
        "Gadgetzan": (-7139, -3766),
        "Steamwheedle Port": (-6950, -4830),
    },
    EASTERN_KINGDOMS: {
        "Stormwind": (-8913, 554),
        "Goldshire": (-9464, 62),
        "Sentinel Hill": (-10628, 1037),
        "Darkshire": (-10559, -1189),
        "Lakeshire": (-9282, -2269),
        "Morgan's Vigil": (-8364, -2738),
        "Stonard": (-10446, -3279),
        "Nethergarde Keep": (-11110, -3436),
        "Grom'gol": (-12384, 207),
        "Booty Bay": (-14297, 518),
        "Ironforge": (-4981, -881),
        "Kharanos": (-5582, -483),
        "Thelsamar": (-5349, -2975),
        "Thorium Point": (-6504, -1182),
        "Kargath": (-6676, -2192),
        "Menethil Harbor": (-3740, -830),
        "Refuge Pointe": (-1223, -2525),
        "Hammerfall": (-917, -3496),
        "Southshore": (-846, -520),
        "Tarren Mill": (-38, -900),
        "Aerie Peak": (327, -1959),
        "The Sepulcher": (505, 1587),
        "Undercity": (1586, 239),
        "Brill": (2275, 323),
        "Light's Hope Chapel": (2280, -5275),
    },
}
# Open water: well off every coast, and in the bays that give the coasts their shape.
SEA = {
    KALIMDOR: {
        "west of Feralas": (-4000, 9000),
        "east of Durotar": (0, -9000),
        "north of Winterspring": (13500, -2000),
        "south of Tanaris": (-13500, -2000),
        "west of Darkshore": (5000, 5000),
        "west of Silithus": (-9000, 4500),
        "north-east of Azshara": (9000, -6500),
        "east of Dustwallow": (-3000, -7000),
        "the channel between Teldrassil and Darkshore": (7900, 1100),
    },
    EASTERN_KINGDOMS: {
        "west of Dun Morogh": (-5000, 5000),
        "east of the Badlands": (-5000, -8000),
        "north of Tirisfal": (6000, -1000),
        "south of Booty Bay": (-17000, 0),
        "west of Silverpine": (0, 4500),
        "west of Stranglethorn": (-12000, 4000),
        "south-east of the Blasted Lands": (-13500, -3500),
        "east of the Plaguelands": (2000, -7500),
        "Baradin Bay": (-2000, -200),
    },
}


@pytest.mark.parametrize("continent", [KALIMDOR, EASTERN_KINGDOMS])
def test_where_a_character_walks_is_land(continent: int):
    """A road must not be drawn out at sea: the towns of every zone - the
    harbours too - are inside the outline."""
    at_sea = [name for name, (x, y) in PLACES[continent].items() if not on_land(continent, x, y)]
    assert at_sea == []


@pytest.mark.parametrize("continent", [KALIMDOR, EASTERN_KINGDOMS])
def test_the_open_sea_is_not_land(continent: int):
    ashore = [name for name, (x, y) in SEA[continent].items() if on_land(continent, x, y)]
    assert ashore == []


def test_north_is_up_and_west_is_left():
    """Nothing mirrored: what lies north, south, east and west on each
    continent is on that side of its map."""
    for continent, north, south, east, west in (
        (KALIMDOR, "Everlook", "Gadgetzan", "Orgrimmar", "Feathermoon Stronghold"),
        (EASTERN_KINGDOMS, "Undercity", "Booty Bay", "Light's Hope Chapel", "Sentinel Hill"),
    ):
        places = PLACES[continent]
        min_x, min_y, max_x, max_y = LANDS[continent]["bounds"]
        middle_x, middle_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        assert places[north][0] > middle_x > places[south][0]
        assert places[west][1] > middle_y > places[east][1]  # y grows to the west


def test_the_bays_that_shape_the_coasts_are_open():
    # The bay south of Durotar: Ratchet on its shore, Sen'jin and Theramore either side of it.
    assert not on_land(KALIMDOR, -2000, -5300)
    assert on_land(KALIMDOR, -956, -3754) and on_land(KALIMDOR, -825, -4920) and on_land(KALIMDOR, -3680, -4375)
    # The Eastern Kingdoms narrow to a neck at the Thandol Span.
    land = LANDS[EASTERN_KINGDOMS]
    widest = land["bounds"][3] - land["bounds"][1]

    def width_at(x: float) -> int:
        return sum(40 for y in range(land["bounds"][1], land["bounds"][3], 40) if on_land(EASTERN_KINGDOMS, x, y))

    assert width_at(-2200) < 0.45 * widest < 0.9 * widest < width_at(1500) + 1500


# ---- the demo's road ------------------------------------------------------------------------------


def test_the_demo_road_runs_on_the_land():
    """The made-up road goes through places that exist: on the map it lies
    on the continents, apart from the boat from Teldrassil."""
    road = demo_road(DEMO_KEY)
    afloat = 0
    total = 0
    for stretch in road.stretches:
        off = [point for point in stretch.points if not on_land(stretch.continent, *point)]
        total += len(stretch.points)
        if off:
            # Only the boat: not walked for real, and over the water between Rut'theran and Auberdine.
            assert stretch.style == "road", (stretch.mode, stretch.level, off[0])
            assert all(6_400 < x < 8_700 and 500 < y < 1_300 for x, y in off), off[0]
            afloat += len(off)
    assert 0 < afloat < 0.06 * total
    # Start, end and the tens of levels are on land.
    api = road.to_api(0.0)
    for place in [api["start"], api["end"], *api["milestones"]]:
        assert on_land(place["c"], place["x"], place["y"]), place
    # One passage between the continents: from Auberdine to Menethil Harbor.
    (passage,) = api["crossings"]
    assert math.dist((passage["from"]["x"], passage["from"]["y"]), PLACES[KALIMDOR]["Auberdine"]) < 50
    assert math.dist((passage["to"]["x"], passage["to"]["y"]), PLACES[EASTERN_KINGDOMS]["Menethil Harbor"]) < 50
