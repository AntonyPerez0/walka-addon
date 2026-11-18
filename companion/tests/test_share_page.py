"""The Share page: its place in the dashboard, and the picture it draws.

The page's files are checked as text (they must fit the dashboard's content
policy and must never interpret a character's name). The two modules that
decide what the picture says and where it goes - ``cardmodel.js`` and
``card.js`` - have no DOM in them, so they are run under Node when Node is
installed (``share_harness.mjs``); those tests are skipped without it.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import web
from walka.road import Road, Stretch, demo_road

STATIC = web.STATIC_DIR
HARNESS = Path(__file__).with_name("share_harness.mjs")
NEW_SCRIPTS = ("share.js", "card.js", "cardmodel.js", "lands.js")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

DASH = "\N{EN DASH}"
MIDDOT = "\N{MIDDLE DOT}"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"

# Noon UTC on 16 November 2026, and three weeks before.
SEEN = 1_794_830_400
SINCE = SEEN - 33 * 86400


def read(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


# ---- the page's files ----------------------------------------------------------------------------


def test_the_share_view_is_part_of_the_dashboard():
    page = read("index.html")
    assert '<a href="#share" data-view="share">Share</a>' in page
    assert 'id="view-share"' in page and 'id="share-canvas"' in page
    app = read("app.js")
    assert '"share"' in app and "initShare()" in app and "updateShare(s)" in app


def test_every_element_the_share_script_looks_up_exists():
    page = read("index.html")
    ids = set(re.findall(r'\$\("([\w-]+)"\)', read("share.js")))
    assert len(ids) > 15  # the pattern still matches how the script looks things up
    missing = sorted(i for i in ids if f'id="{i}"' not in page)
    assert missing == []


def test_the_page_fits_the_content_policy():
    """default-src 'self': no inline script or style, nothing from elsewhere."""
    page = read("index.html")
    assert not re.search(r"<[^>]+\sstyle\s*=", page)
    assert not re.search(r"<[^>]+\son\w+\s*=", page)
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", page)
    assert "default-src 'self'" in web.PAGE_HEADERS["Content-Security-Policy"]
    for name in NEW_SCRIPTS:
        text = read(name)
        assert not re.search(r"""["'`](https?:)?//""", text), name
        for banned in ("eval(", "new Function", 'setAttribute("style"', "@import"):
            assert banned not in text, (name, banned)


def test_the_continents_reach_the_picture_as_data():
    """The page hands the maps to the two modules that draw: those stay free
    of imports (the tests run them alone), and the maps are nothing but data."""
    assert 'import { LANDS } from "./lands.js";' in read("share.js")
    assert "lands: LANDS," in read("share.js")
    text = read("lands.js")
    code = "\n".join(line for line in text.splitlines() if not line.startswith("//")).strip()
    assert code.startswith("export const LANDS = {") and code.endswith("};")
    assert set(json.loads(code[len("export const LANDS = ") : -1])) == {"0", "1"}
    for banned in ("function", "=>", "import", "fetch", "(", "`"):
        assert banned not in code, banned


def test_the_picture_needs_no_files_and_no_network():
    """Drawn by hand: no pictures to load, no fonts to fetch, nothing sent."""
    for name in ("card.js", "cardmodel.js"):
        text = read(name)
        assert not re.search(r"^\s*import\b|\bimport\(", text, re.MULTILINE), name  # stands alone
        for banned in ("fetch(", "XMLHttpRequest", "new Image", "drawImage", "FontFace", "document.", "url("):
            assert banned not in text, (name, banned)
    css = read("app.css")
    assert "@font-face" not in css and "url(" not in css


def test_a_characters_name_is_only_ever_written_as_text():
    for name in NEW_SCRIPTS:
        text = read(name)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser"):
            assert banned not in text, (name, banned)


def test_choices_are_kept_in_the_browser_but_the_page_works_without():
    text = read("share.js")
    uses = [match.start() for match in re.finditer(r"localStorage\.", text)]
    assert len(uses) == 2  # one read, one write
    for position in uses:
        before = text[:position]
        assert before.rfind("try {") > before.rfind("catch"), "localStorage is used outside try/catch"


def test_the_new_files_are_served(tmp_path: Path):
    rig = Rig(tmp_path)

    async def scenario():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            for name in NEW_SCRIPTS:
                response = await client.get(f"/static/{name}")
                assert response.status == 200, name
                assert response.content_type == "text/javascript", name
                assert response.headers["X-Content-Type-Options"] == "nosniff"
            page = await client.get("/")
            assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
            assert 'id="view-share"' in await page.text()

    asyncio.run(asyncio.wait_for(scenario(), 30))


# ---- the modules, under Node ---------------------------------------------------------------------


def ask(*requests: dict[str, Any]) -> list[Any]:
    """Run requests through share_harness.mjs (see there for the operations)."""
    done = subprocess.run(
        [NODE, str(HARNESS), str(STATIC / "cardmodel.js"), str(STATIC / "card.js"), str(STATIC / "lands.js")],
        input=json.dumps(requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "TZ": "UTC"},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def character(**changes: Any) -> dict[str, Any]:
    """An entry of /api/journey with the numbers of a level-17 character."""
    entry = {
        "id": "0badcafe",
        "name": "Thrall",
        "level": 17,
        "max_level": 60,
        "class_id": 7,
        "faction": "horde",
        "played_s": 21 * 3600 + 25 * 60 + 40,
        "foot_yd": 82_025,
        "travel_yd": 190_000,
        "character_steps": 93_210,
        "character_miles": 46.61,
        "steps": 105_937,
        "distance_m": 85_295,
        "kcal": 8_204,
        "seconds": 95_400,
        "walks": 14,
        "estimated": False,
        "since": SINCE,
        "seen_at": SEEN,
        "live": False,
    }
    entry.update(changes)
    return entry


TOTALS = {"sessions": 40, "seconds": 160_000.0, "distance_m": 140_000.0, "steps": 250_000, "kcal": 11_049.6}
HUGE = character(
    name="Абвгдежзик",
    level=60,
    played_s=9_999 * 3600 + 59 * 60,
    character_steps=9_999_999,
    character_miles=4_999.99,
    steps=9_999_999,
    distance_m=7_999_999,
    kcal=999_999,
    seconds=5_000_000,
    estimated=True,
)
EMPTY = character(
    name="", level=1, played_s=None, character_steps=0, character_miles=0, steps=0, distance_m=0, kcal=0, seconds=0
)
LONG_WORDS = {
    "overline": "An overline that is as long as it may be",
    "title": "The longest title anybody could type",
    "subtitle": "World of Warcraft Forever - Hardcore - 1-60",
    "tagline": "If I am gaming I am moving, and I never ever stop moving.",
}


def road_of(*stretches: Stretch, owner: str = "0badcafe") -> dict[str, Any]:
    """What /api/road gives as `road` (made by the companion's own code),
    for the character with the id ``owner``."""
    road = Road("Somebody-Somewhere", tuple(stretches)) if stretches else demo_road("Somebody-Somewhere")
    return {**road.to_api(float(SEEN)), "id": owner}


def stretch(
    continent: int, x: int, y: int, *steps: tuple[int, int], mode: str = "w", belt: bool = True, level: int = 1
):
    points = [(x, y)]
    for dx, dy in steps:
        points.append((points[-1][0] + dx, points[-1][1] + dy))
    return Stretch(continent, mode, belt, level, tuple(points))


ROAD = road_of()  # two continents, levels 1 to 23
ONE_LAND = road_of(
    stretch(0, -9000, 200, (400, 30), (350, -80), (10, 900), level=9), stretch(0, -8240, 1050, (700, 0), level=10)
)
# A character that has only just set out: a few yards, all on one line.
FIRST_STEPS = road_of(stretch(1, 100, 100, (3, 0)))
STANDING = road_of(stretch(1, 100, 100, (0, 0)))
# Continents this client's maps have no names for here, and a third one.
ELSEWHERE = road_of(
    stretch(530, 0, 0, (900, 100), (-200, 800), level=58),
    stretch(571, 0, 0, (300, -2000), (4000, 0), belt=False, level=59),
    stretch(1, 0, 0, (20, 0), mode="t", belt=False, level=60),
    stretch(0, 5, 5, (0, 90), level=60),
)
# One continent, and a road long enough to be seen on all of it.
LONG_LAND = road_of(
    stretch(0, -9000, 300, (3000, -800), (3200, -900), level=30), stretch(0, -2800, -1400, (4400, 1600), level=31)
)
# A few hundred yards on the west coast of the Eastern Kingdoms (Westfall).
COAST = road_of(stretch(0, -10000, 2380, (-250, -60), (-150, 40), level=12))
# A continent with a map beside one without (another client's world).
MIXED = road_of(stretch(1, 1600, -4300, (-2000, 1600), level=58), stretch(530, 0, 0, (900, 100), (-200, 800), level=59))
# Narrow and tall, wide and flat: the road must fit whatever its shape.
TALL = road_of(stretch(1, -9000, 0, (18000, 40), level=1), stretch(0, 0, 0, (30, 20), level=1))
FLAT = road_of(stretch(1, 0, -9000, (40, 18000), level=30), stretch(0, 0, -9000, (-25, 18000), belt=False, level=60))


def build(**request: Any) -> dict[str, Any]:
    request.setdefault("character", character())
    request.setdefault("totals", TOTALS)
    request.setdefault("now", SEEN + 86400)
    return ask({"op": "build", **request})[0]


def tiles_of(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tile["id"]: tile for tile in card["tiles"]}


def value(tile: dict[str, Any]) -> str:
    return " ".join(run["text"] for run in tile["value"])


@pytest.fixture(scope="module")
def about() -> dict[str, Any]:
    return ask({"op": "about"})[0]


@needs_node
def test_the_defaults(about):
    options = about["normalized"]
    # Privacy: the name and the body weight are not on the picture unless asked for.
    assert options["showName"] is False and options["showFootnote"] is False
    assert options["source"] == "character" and options["units"] is None
    assert options["tiles"] == {
        "steps": True,
        "route": True,
        "level": True,
        "played": True,
        "charSteps": True,
        "calories": True,
    }
    assert options["size"] == "portrait" and options["theme"] == about["themes"][0]["id"]
    assert all(options[key] is None for key in ("overline", "title", "subtitle", "tagline"))

    sizes = {size["id"]: (size["width"], size["height"]) for size in about["sizes"]}
    assert about["sizes"][0]["id"] == "portrait"
    assert sizes == {"portrait": (1080, 1350), "square": (1080, 1080), "story": (1080, 1920), "wide": (1200, 675)}


@needs_node
def test_there_are_at_least_four_distinct_themes(about):
    themes = about["themes"]
    assert len(themes) >= 4
    for key in ("id", "name"):
        assert len({theme[key] for theme in themes}) == len(themes)
    assert len({tuple(theme["bg"]) for theme in themes}) == len(themes)
    assert len({tuple(theme["metal"]) for theme in themes}) == len(themes)
    assert len({theme["shape"] for theme in themes}) >= 4  # the frames differ too, not only the colours


@needs_node
def test_stored_choices_are_cleaned_up(about):
    theme_ids = [theme["id"] for theme in about["themes"]]
    for junk in (None, 7, "x", [], {"tiles": "all", "theme": 5, "size": None, "units": "furlongs", "source": 1}):
        assert ask({"op": "normalize", "raw": junk})[0] == about["normalized"]

    raw = {
        "character": "0badcafe",
        "source": "all",
        "title": "  My\ttitle\nhere " + "x" * 100,
        "overline": "",
        "subtitle": 12,
        "tiles": {"steps": False, "level": False, "played": "yes", "nonsense": True},
        "units": "km",
        "theme": theme_ids[-1],
        "size": "story",
        "showName": True,
        "showFootnote": "true",
        "extra": "ignored",
    }
    options = ask({"op": "normalize", "raw": raw})[0]
    assert options["character"] == "0badcafe" and options["source"] == "all"
    assert options["title"].startswith("  My title here x") and len(options["title"]) == about["limits"]["title"]
    assert options["overline"] == "" and options["subtitle"] is None
    assert options["tiles"] == {
        "steps": False,
        "route": True,
        "level": False,
        "played": True,
        "charSteps": True,
        "calories": True,
    }
    assert (options["units"], options["theme"], options["size"]) == ("km", theme_ids[-1], "story")
    assert options["showName"] is True and options["showFootnote"] is False  # only a real yes turns it on
    assert "extra" not in options

    nothing = {"tiles": dict.fromkeys(("steps", "level", "played", "charSteps", "calories"), False)}
    assert ask({"op": "normalize", "raw": nothing})[0]["tiles"]["steps"] is True  # never an empty picture
    assert ask({"op": "normalize", "raw": {"character": "<script>"}})[0]["character"] == ""


@needs_node
def test_the_card_of_a_character():
    card = build()
    assert card["overline"] == "Walking while I play" and card["title"] == "One step at a time"
    assert card["subtitle"] == f"WoW Forever {MIDDOT} Level 1-60"  # built from the level cap
    assert card["tagline"] == "My character runs. I walk."
    assert card["footer"] == "Totals through Nov 16, 2026"
    assert card["credit"] == "Made with Walka" and card["source"] == "character"

    tiles = tiles_of(card)
    assert list(tiles) == ["steps", "level", "played", "charSteps", "calories"]
    assert tiles["steps"]["hero"] is True
    assert value(tiles["steps"]) == "105,937" and tiles["steps"]["sub"] == "53.0 miles walked"
    assert value(tiles["level"]) == "17 / 60" and tiles["level"]["sub"] == "43 levels to go"
    assert tiles["level"]["progress"] == pytest.approx(17 / 60)
    assert value(tiles["played"]) == "21 h 25 m" and tiles["played"]["sub"] == "In-game time"
    assert value(tiles["charSteps"]) == "~93,210"  # a convention, hence "about"
    assert tiles["charSteps"]["sub"] == "46.6 in-game miles"
    assert tiles["charSteps"]["note"] == "On foot since Oct 14, 2026"
    assert value(tiles["calories"]) == "~8,200"  # an estimate, rounded like one
    assert tiles["calories"]["sub"] == f"kcal {MIDDOT} approximate"
    assert "note" not in tiles["calories"]
    for number in ("105,937", "17 of 60", "21h 25m", "93,210", "8,200"):
        assert number in card["alt"]


@needs_node
def test_privacy_nothing_personal_unless_asked_for():
    card = build(weightKg=75)
    text = json.dumps(card)
    assert "Thrall" not in text  # the character's name
    assert "165" not in text and " lb" not in text and "mph" not in text  # body weight and pace

    card = build(weightKg=75, options={"showName": True, "showFootnote": True})
    tiles = tiles_of(card)
    assert tiles["level"]["sub"] == "Thrall"
    assert tiles["calories"]["note"] == f"165 lb {MIDDOT} 2.0 mph average"
    assert ask({"op": "fileName", "now": SEEN})[0] == "walka-journey-2026-11-16.png"  # never the name


@needs_node
def test_kilometres_and_the_dashboards_units():
    assert tiles_of(build(units="kmh"))["steps"]["sub"] == "85.3 km walked"
    assert tiles_of(build(units="mph"))["steps"]["sub"] == "53.0 miles walked"
    # A choice made on the Share page wins over the dashboard's setting.
    assert tiles_of(build(units="kmh", options={"units": "mi"}))["steps"]["sub"] == "53.0 miles walked"
    assert tiles_of(build(units="mph", options={"units": "km"}))["steps"]["sub"] == "85.3 km walked"
    note = tiles_of(build(units="kmh", weightKg=75.4, options={"showFootnote": True}))["calories"]["note"]
    assert note == f"75 kg {MIDDOT} 3.2 km/h average"
    # The character's own distance follows, as it does in the game's Journey window.
    assert tiles_of(build(units="kmh"))["charSteps"]["sub"] == "75.0 in-game km"
    assert tiles_of(build(units="kmh", character=character(foot_yd=None)))["charSteps"]["sub"] == "46.6 in-game miles"


@needs_node
def test_all_my_walks_uses_the_companions_totals():
    card = build(options={"source": "all"}, character=character(estimated=True))
    tiles = tiles_of(card)
    assert card["source"] == "all"
    assert value(tiles["steps"]) == "250,000" and tiles["steps"]["sub"] == "87.0 miles walked"
    assert value(tiles["calories"]) == "~11,050"
    assert card["footer"] == "Totals through Nov 17, 2026"  # measured up to now, not up to the last login
    # The game's numbers are still the character's.
    assert value(tiles["level"]) == "17 / 60" and value(tiles["charSteps"]) == "~93,210"


@needs_node
def test_estimates_from_the_game_are_marked():
    tiles = tiles_of(build(character=character(estimated=True)))
    assert value(tiles["steps"]) == "~105,937" and tiles["steps"]["sub"] == "~53.0 miles walked"
    assert "about 105,937" in build(character=character(estimated=True))["alt"]


@needs_node
def test_without_a_character_the_card_shows_the_walks_alone():
    card = build(character=None)
    assert card["source"] == "all"
    assert list(tiles_of(card)) == ["steps", "calories"]
    assert card["subtitle"] == "WoW Forever"
    assert value(tiles_of(card)["steps"]) == "250,000"
    # ... and with nothing known at all it still draws, with dashes.
    blank = build(character=None, totals=None)
    assert value(tiles_of(blank)["steps"]) == DASH and tiles_of(blank)["steps"]["sub"] == ""


@needs_node
def test_values_at_the_edges():
    tiles = tiles_of(build(character=HUGE, weightKg=75, options={"showName": True}))
    assert value(tiles["steps"]) == "~9,999,999" and tiles["steps"]["sub"] == "~4,971 miles walked"
    assert value(tiles["level"]) == "60 / 60" and tiles["level"]["progress"] == 1
    assert tiles["level"]["sub"] == "Абвгдежзик"
    assert tiles_of(build(character=HUGE))["level"]["sub"] == "Level cap reached"
    assert value(tiles["played"]) == "9,999 h 59 m"
    assert value(tiles["charSteps"]) == "~9,999,999" and tiles["charSteps"]["sub"] == "5,000 in-game miles"
    assert value(tiles["calories"]) == "~1,000,000"

    tiles = tiles_of(build(character=EMPTY))
    assert value(tiles["steps"]) == "0" and tiles["steps"]["sub"] == "0.0 miles walked"
    assert value(tiles["level"]) == "1 / 60" and tiles["level"]["sub"] == "59 levels to go"
    assert value(tiles["played"]) == DASH and tiles["played"]["sub"] == "Not known yet"  # no /played yet
    assert value(tiles["charSteps"]) == "0" and value(tiles["calories"]) == "0"  # nothing to estimate about zero

    one_to_go = tiles_of(build(character=character(level=59)))["level"]
    assert one_to_go["sub"] == "1 level to go"
    capless = tiles_of(build(character=character(max_level=None)))["level"]
    assert value(capless) == "17" and "progress" not in capless
    unknown = tiles_of(build(character=character(level=None, character_steps=None, kcal=None, steps=None)))
    assert value(unknown["level"]) == DASH and value(unknown["charSteps"]) == DASH
    assert value(unknown["calories"]) == DASH and value(unknown["steps"]) == DASH
    short = tiles_of(build(character=character(played_s=47 * 60 + 59)))["played"]
    assert value(short) == "47 m"


@needs_node
def test_words_and_tiles_can_be_changed():
    card = build(options={"title": "Road to sixty", "overline": "", "tagline": "  Left foot, right foot  "})
    assert (card["title"], card["overline"], card["tagline"]) == ("Road to sixty", "", "Left foot, right foot")
    assert card["subtitle"] == f"WoW Forever {MIDDOT} Level 1-60"
    assert "Road to sixty" in card["alt"]
    assert build(character=character(max_level=70))["subtitle"] == f"WoW Forever {MIDDOT} Level 1-70"
    assert ask({"op": "subtitle", "maxLevel": None})[0] == "WoW Forever"

    card = build(options={"tiles": {"played": False, "calories": False}})
    assert list(tiles_of(card)) == ["steps", "level", "charSteps"]
    assert "Calories" not in card["alt"]


# ---- the road --------------------------------------------------------------------------------------


@needs_node
def test_the_road_of_the_character_is_a_tile_of_its_own():
    card = build(road=ROAD)
    assert list(tiles_of(card)) == ["steps", "route", "level", "played", "charSteps", "calories"]
    tile = tiles_of(card)["route"]
    miles = ROAD["yards"] / 1760
    share = round(100 * ROAD["belt_yards"] / ROAD["yards"])
    assert tile["label"] == ["My road"] and tile["wide"] is True
    # (Said to be the road's: the tile beside it has in-game miles of its own.)
    assert tile["sub"] == f"{miles:.1f} in-game miles of road"
    # The key to the lines on the map: each kind, in full and in brief.
    assert tile["key"] == [
        {"style": "walked", "text": f"{share}% walked for real", "brief": f"{share}% walked"},
        {"style": "road", "text": f"{100 - share}% not walked", "brief": f"{100 - share}% not"},
        {"style": "flight", "text": "flight / boat", "brief": "flight, boat"},
    ]
    assert tile["plain"] == (
        f"My road on Kalimdor and Eastern Kingdoms: {miles:.1f} in-game miles, {share}% of it walked on the treadmill"
    )
    assert tile["plain"] in card["alt"]
    route = tile["route"]
    assert [land["name"] for land in route["continents"]] == ["Kalimdor", "Eastern Kingdoms"]
    assert route["start"] == ROAD["start"] and route["end"] == ROAD["end"]
    assert [mark["level"] for mark in route["milestones"]] == [10, 20]
    assert sum(len(path["p"]) for land in route["continents"] for path in land["paths"]) > 400
    # The gem carries the character's level; the boat between the continents is an arc.
    assert route["level"] == 17
    assert route["crossings"] == ROAD["crossings"] and len(route["crossings"]) == 1
    assert route["crossings"][0]["from"]["c"] == 1 and route["crossings"][0]["to"]["c"] == 0

    km = tiles_of(build(road=ROAD, options={"units": "km"}))["route"]
    assert km["sub"] == f"{ROAD['yards'] * 0.9144 / 1000:.1f} in-game km of road"
    assert tiles_of(build(road=ROAD, units="kmh"))["route"]["sub"] == km["sub"]


@needs_node
def test_the_road_lies_on_a_map_of_its_continent():
    lands = json.loads(read("lands.js").split("export const LANDS = ", 1)[1].rstrip().rstrip(";"))
    route = tiles_of(build(road=ROAD))["route"]["route"]
    for land in route["continents"]:
        own = lands[str(land["id"])]
        assert land["land"] == {key: own[key] for key in ("bounds", "points", "coast", "faces")}
    # A continent this client's maps know nothing of: its road is drawn all the same.
    assert [land["land"] for land in tiles_of(build(road=ELSEWHERE))["route"]["route"]["continents"]] == [None, None]
    mixed = tiles_of(build(road=MIXED))["route"]["route"]["continents"]
    assert [(land["id"], land["land"] is not None) for land in mixed] == [(1, True), (530, False)]
    # No maps handed over at all.
    assert all(land["land"] is None for land in tiles_of(build(road=ROAD, lands=False))["route"]["route"]["continents"])


@needs_node
@pytest.mark.parametrize(
    "damage",
    [
        {"points": [0, 0, 10]},
        {"points": [0, 0, 10, 0, 0, "ten"]},
        {"points": [0, 0, 10, 0]},
        {"bounds": [0, 0, 10]},
        {"bounds": [10, 0, 0, 10]},
        {"bounds": [0, 0, None, 10]},
        {"coast": []},
        {"coast": [[0, 1]]},
        {"coast": [[0, 1, 3]]},
        {"coast": [[0, 1, -1]]},
        {"coast": "0,1,2"},
        {"faces": [0, 1, 2]},
        {"faces": [0, 1, 7, 4]},
        {"faces": [0, 1, 2.5, 4]},
        {"faces": [0, 1, 2, None]},
        {"faces": None},
    ],
)
def test_a_damaged_map_is_left_out_and_the_road_still_drawn(about, damage: dict[str, Any]):
    sound = {"bounds": [0, 0, 10, 10], "points": [0, 0, 10, 0, 0, 10], "coast": [[0, 1, 2]], "faces": [0, 1, 2, 4]}
    road = road_of(stretch(1, 1, 1, (5, 5)))
    assert tiles_of(build(road=road, lands={"1": sound}))["route"]["route"]["continents"][0]["land"] == sound
    broken = {"1": {**sound, **damage}}
    assert tiles_of(build(road=road, lands=broken))["route"]["route"]["continents"][0]["land"] is None
    drawn = ask(
        {
            "op": "draw",
            "size": "portrait",
            "theme": about["themes"][0]["id"],
            "character": character(),
            "road": road,
            "lands": broken,
        }
    )
    (plate,) = drawn[0]["report"]["route"]["plates"]
    assert plate["mapped"] is False and plate["road"]["points"] == 2


@needs_node
def test_no_road_no_tile():
    without = ["steps", "level", "played", "charSteps", "calories"]
    assert list(tiles_of(build())) == without
    assert list(tiles_of(build(road=None))) == without
    # Somebody else's road is not this character's.
    assert list(tiles_of(build(road={**ROAD, "id": "0badf00d"}))) == without
    # No character chosen: nobody's road.
    assert "route" not in tiles_of(build(character=None, road=ROAD))
    # A road with nothing to draw, or an answer that is no road at all.
    for empty in (
        {**ROAD, "continents": []},
        {**ROAD, "continents": [{"id": 1, "name": "Kalimdor", "paths": []}]},
        {},
        [],
    ):
        assert list(tiles_of(build(road=empty))) == without
    # Switched off.
    assert list(tiles_of(build(road=ROAD, options={"tiles": {"route": False}}))) == without
    # It is the character's road whichever walks the numbers are of.
    assert "route" in tiles_of(build(road=ROAD, options={"source": "all"}))


@needs_node
def test_the_road_is_laid_out_west_to_east_and_never_more_than_two_lands():
    def lands(road: dict[str, Any]) -> list[int]:
        return [land["id"] for land in tiles_of(build(road=road))["route"]["route"]["continents"]]

    east_first = road_of(stretch(0, 0, 0, (5000, 0)), stretch(1, 0, 0, (100, 0)))
    assert [land["id"] for land in east_first["continents"]] == [0, 1]  # the longer road first, as it comes
    assert lands(east_first) == [1, 0]  # Kalimdor lies to the west
    assert lands(ROAD) == [1, 0]
    assert lands(ONE_LAND) == [0]
    assert lands(ELSEWHERE) == [571, 530]  # the two with the most road; the flight and the short walk are left out

    tile = tiles_of(build(road=ELSEWHERE))["route"]
    assert tile["plain"].startswith("My road: ")  # no names to give
    assert tiles_of(build(road=ONE_LAND))["route"]["plain"].startswith("My road on Eastern Kingdoms: ")


@needs_node
def test_a_road_nobody_walked_for_real_does_not_say_zero_percent():
    never = road_of(stretch(1, 0, 0, (1760, 0), belt=False))
    tile = tiles_of(build(road=never))["route"]
    assert tile["sub"] == "1.0 in-game miles of road"
    assert tile["key"] == [{"style": "road", "text": "not walked for real", "brief": "not walked"}]
    assert tile["plain"] == "My road on Kalimdor: 1.0 in-game miles"
    all_of_it = tiles_of(build(road=road_of(stretch(1, 0, 0, (1760, 0)))))["route"]
    assert all_of_it["key"] == [{"style": "walked", "text": "100% walked for real", "brief": "100% walked"}]
    more = tiles_of(build(road={**ROAD, "yards": 100, "belt_yards": 900}))["route"]
    assert more["key"][0]["text"] == "100% walked for real"
    assert [entry["style"] for entry in more["key"]] == ["walked", "flight"]
    # A flight within one continent is a flight; a boat is only named where the road crossed the sea.
    flown = road_of(stretch(1, 0, 0, (1760, 0)), stretch(1, 1760, 0, (3000, 400), mode="t", belt=False))
    assert tiles_of(build(road=flown))["route"]["key"][-1] == {"style": "flight", "text": "flight", "brief": "flight"}


@needs_node
def test_a_road_too_short_to_measure_says_where_it_starts():
    # The first few yards of a new character: no "0.0 miles", no percentage of them.
    for road in (
        FIRST_STEPS,
        STANDING,
        {**FIRST_STEPS, "yards": None, "belt_yards": "many"},
        {**FIRST_STEPS, "yards": 80},
    ):
        tile = tiles_of(build(road=road))["route"]
        assert tile["sub"] == "My road starts here" and tile["key"] == [], road["yards"]
        assert "0.0" not in tile["plain"] and "%" not in tile["plain"] and tile["plain"].endswith("starts here")
    assert tiles_of(build(road=FIRST_STEPS))["route"]["plain"] == "My road on Kalimdor starts here"
    # Straight onto a flight path: that is a road too, and the picture draws it.
    aloft = road_of(stretch(1, 0, -3000, (2, 0)), stretch(1, 2, -3000, (3000, 500), mode="t", belt=False))
    tile = tiles_of(build(road=aloft))["route"]
    assert tile["sub"] == "My road so far: a flight" and tile["plain"] == "My road on Kalimdor so far: a flight"
    assert tile["key"] == [{"style": "flight", "text": "flight", "brief": "flight"}]


@needs_node
def test_only_passages_between_the_continents_on_the_picture_are_drawn():
    # Kalimdor and the Eastern Kingdoms are shown; the portals to a third continent are many more.
    boat = {"from": {"c": 1, "x": -3680, "y": -4375}, "to": {"c": 0, "x": -3740, "y": -830}}
    portals = [
        {"from": {"c": 0, "x": -11900, "y": -3200 - index}, "to": {"c": 530, "x": index, "y": 0}} for index in range(5)
    ]
    route = tiles_of(build(road={**ROAD, "crossings": [*portals, boat]}))["route"]["route"]
    assert route["crossings"] == [boat]


@needs_node
def test_what_the_road_tile_is_given_is_cleaned_up():
    odd = {
        **ROAD,
        "start": {"c": 1, "x": "north"},
        "end": None,
        "level": "high",
        "milestones": [
            {"level": 10, "c": 1, "x": 5, "y": 5},
            {"level": "x", "c": 1, "x": 1, "y": 1},
            {"c": 1},
            None,
            7,
        ],
        "crossings": [None, {"from": ROAD["start"]}, {"from": ROAD["start"], "to": ROAD["end"]}]
        + [ROAD["crossings"][0]] * 9,
    }
    route = tiles_of(build(road=odd, character=character(level=None)))["route"]["route"]
    assert route["start"] is None and route["end"] is None and route["level"] is None
    assert route["milestones"] == [{"level": 10, "c": 1, "x": 5, "y": 5}]
    assert len(route["crossings"]) == 3 and route["crossings"][0] == {"from": ROAD["start"], "to": ROAD["end"]}
    # Without a level from the game, the road's own.
    assert tiles_of(build(road=ROAD, character=character(level=None)))["route"]["route"]["level"] == ROAD["level"]
    for road in ({**ROAD, "milestones": "none", "crossings": 5}, {**ROAD, "milestones": None, "crossings": None}):
        route = tiles_of(build(road=road))["route"]["route"]
        assert route["milestones"] == [] and route["crossings"] == []


# ---- the drawing: nothing ever leaves its box ---------------------------------------------------------

CASES = {
    "usual": {"character": character()},
    "huge": {"character": HUGE, "options": {"showName": True, "showFootnote": True, **LONG_WORDS}, "weightKg": 250},
    "empty": {"character": EMPTY},
    "no character": {"character": None},
    "no words": {"character": character(), "options": {"overline": "", "title": "", "subtitle": "", "tagline": ""}},
    "hero only": {
        "character": character(),
        "options": {"tiles": {"level": 0, "played": 0, "charSteps": 0, "calories": 0}},
    },
    "three tiles": {
        "character": character(),
        "options": {"tiles": {"steps": False, "played": False, "level": True, "charSteps": True, "calories": True}},
    },
    "one small tile": {
        "character": character(),
        "options": {"tiles": {"steps": False, "played": False, "level": False, "charSteps": False}},
    },
    "road": {"character": character(), "road": ROAD},
    "road, huge": {
        "character": HUGE,
        "road": {**FLAT, "yards": 99_999_999, "belt_yards": 99_999_999},
        "options": {"showName": True, "showFootnote": True, **LONG_WORDS},
        "weightKg": 250,
    },
    "road, one land": {"character": character(), "road": ONE_LAND},
    "road, first steps": {"character": EMPTY, "road": FIRST_STEPS},
    "road, standing": {"character": character(), "road": STANDING},
    "road, elsewhere": {"character": character(), "road": ELSEWHERE},
    "road, tall": {"character": character(), "road": TALL},
    "road and hero": {
        "character": character(),
        "road": ROAD,
        "options": {"tiles": {"level": 0, "played": 0, "charSteps": 0, "calories": 0}},
    },
    "road alone": {
        "character": character(),
        "road": ROAD,
        "options": {"tiles": {"steps": 0, "level": 0, "played": 0, "charSteps": 0, "calories": 0}},
    },
    "road and one tile": {
        "character": character(),
        "road": ONE_LAND,
        "options": {"tiles": {"steps": 0, "played": 0, "charSteps": 0, "calories": 0}},
    },
    "road and three tiles": {
        "character": character(),
        "road": ROAD,
        "options": {"tiles": {"steps": 0, "played": 0}},
    },
    "road, long, one land": {"character": character(), "road": LONG_LAND},
    "road, mixed": {"character": character(), "road": MIXED},
    "road, no map": {"character": character(level=23), "road": ROAD, "lands": False},
    "road, one land, no map": {"character": character(), "road": ONE_LAND, "lands": False},
    "road, no words": {
        "character": character(),
        "road": TALL,
        "options": {"overline": "", "title": "", "subtitle": "", "tagline": ""},
    },
}
ROAD_CASES = [name for name, case in CASES.items() if case.get("road")]
for _case in CASES.values():
    _tiles = _case.get("options", {}).get("tiles")
    if _tiles:
        _case["options"]["tiles"] = {key: bool(flag) for key, flag in _tiles.items()}


def inside(text: dict[str, Any], box: dict[str, float], slack: float = 0.01) -> bool:
    return (
        text["left"] >= box["x"] - slack
        and text["right"] <= box["x"] + box["w"] + slack
        and text["top"] >= box["y"] - slack
        and text["bottom"] <= box["y"] + box["h"] + slack
    )


@needs_node
@pytest.mark.parametrize("char_width", [0.45, 0.62, 0.8])
def test_text_never_leaves_its_panel(about, char_width: float):
    """Every theme, every size, every case - with narrow, usual and very wide
    fonts (which font is used depends on the computer)."""
    combos = list(itertools.product(CASES, about["themes"], about["sizes"]))
    requests = [
        {
            "op": "draw",
            "size": size["id"],
            "theme": theme["id"],
            "charWidth": char_width,
            "totals": TOTALS,
            **CASES[name],
        }
        for name, theme, size in combos
    ]
    for (name, theme, size), result in zip(combos, ask(*requests), strict=True):
        where = f"{name} / {theme['id']} / {size['id']}"
        report = result["report"]
        picture = {"x": 0, "y": 0, "w": size["width"], "h": size["height"]}
        assert report["texts"], where
        for text in report["texts"]:
            assert text["size"] > 0 and text["right"] > text["left"], (where, text)
            assert inside(text, text["box"] or picture), (where, text)
            assert inside(text, picture), (where, text)
        panels = report["panels"]
        for panel in panels:
            assert panel["x"] >= 0 and panel["y"] >= 0, (where, panel)
            assert panel["x"] + panel["w"] <= size["width"] and panel["y"] + panel["h"] <= size["height"], where
            assert panel["w"] > 100 and panel["h"] > 100, (where, panel)
        for a, b in itertools.combinations(panels, 2):
            apart = (
                a["x"] + a["w"] <= b["x"]
                or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"]
                or b["y"] + b["h"] <= a["y"]
            )
            assert apart, (where, a["id"], b["id"])
        # Within a tile the lines stand below one another.
        by_tile: dict[str, dict[str, dict]] = {}
        for text in report["texts"]:
            tile, _, part = text["id"].partition(".")
            if part:
                by_tile.setdefault(tile, {})[part] = text
        for tile, parts in by_tile.items():
            order = [parts[part] for part in ("label", "value", "sub", "note") if part in parts]
            for upper, lower in itertools.pairwise(order):
                # (Where the road's panel is wide, its two lines stand side by side.)
                beside = tile == "route" and upper["right"] <= lower["left"] + 0.01
                assert beside or upper["bottom"] <= lower["top"] + 0.01, (where, tile)


def box_of(thing: dict[str, float]) -> dict[str, float]:
    return {"left": thing["x"], "right": thing["x"] + thing["w"], "top": thing["y"], "bottom": thing["y"] + thing["h"]}


def apart(a: dict[str, float], b: dict[str, float], slack: float = 0.01) -> bool:
    return (
        a["right"] <= b["left"] + slack
        or b["right"] <= a["left"] + slack
        or a["bottom"] <= b["top"] + slack
        or b["bottom"] <= a["top"] + slack
    )


@needs_node
@pytest.mark.parametrize("char_width", [0.45, 0.8])
def test_the_road_is_drawn_inside_its_panel(about, char_width: float):
    """Whatever the road's shape - two continents, one, a few yards, none at
    all, with a map behind it or without - in every theme and size."""
    combos = list(itertools.product(ROAD_CASES, about["themes"], about["sizes"]))
    requests = [
        {
            "op": "draw",
            "size": size["id"],
            "theme": theme["id"],
            "charWidth": char_width,
            "totals": TOTALS,
            "calls": theme is about["themes"][0],  # (where things are drawn does not depend on the colours)
            **CASES[name],
        }
        for name, theme, size in combos
    ]
    for (name, theme, size), result in zip(combos, ask(*requests), strict=True):
        where = f"{name} / {theme['id']} / {size['id']}"
        report = result["report"]
        (panel,) = [panel for panel in report["panels"] if panel["id"] == "route"]
        reach = report["route"]
        assert reach is not None and reach["points"] >= 2, where
        box = box_of(reach["box"])
        assert inside(box, panel), where
        texts = {text["id"]: text for text in report["texts"] if text["id"].startswith("route.")}
        assert texts["route.label"]["text"] == "MY ROAD" and "route.sub" in texts, where

        # A plate for each continent: inside the panel, side by side, under
        # the label and over the words below.
        plates = reach["plates"]
        assert 1 <= len(plates) <= 2, where
        for plate in plates:
            sea = box_of(plate)
            assert inside(sea, reach["box"]), (where, plate["id"])
            assert plate["w"] > 30 and plate["h"] > 30, (where, plate)
            assert texts["route.label"]["bottom"] <= sea["top"] + 0.01, where
            assert texts["route.sub"]["top"] >= sea["bottom"] - 0.01, where
            # The road is on its plate, all of it.
            assert plate["road"]["points"] >= 2 and inside(plate["road"], plate), (where, plate["road"])
            name_text = texts.get(f"route.land{plate['id']}")
            if name_text:
                assert sea["bottom"] <= name_text["top"] + 0.01, where
                assert name_text["bottom"] <= texts["route.sub"]["top"] + 0.01, where
                # ... under its own continent.
                assert sea["left"] - 0.01 <= name_text["left"] and name_text["right"] <= sea["right"] + 0.01, where
        for a, b in itertools.combinations(plates, 2):
            assert a["x"] + a["w"] <= b["x"] + 0.01, (where, "plates overlap")
        # The words on the map - the start, the level at the gem, the tens -
        # are on a plate and never on top of one another.
        marks = [
            text for key, text in texts.items() if key in ("route.start", "route.now") or key.startswith("route.level")
        ]
        for text in marks:
            assert any(inside(text, plate) for plate in plates), (where, text["id"])
        for a, b in itertools.combinations(marks, 2):
            assert apart(a, b), (where, a["id"], b["id"])
        # Nothing was drawn at a place that is no place.
        for call in result.get("calls") or []:
            assert None not in call, (where, call)


def draw_road(about, road: dict[str, Any], **request: Any) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """The road's panel in every size: (size, what the map reports, its words)."""
    requests = [
        {
            "op": "draw",
            "size": size["id"],
            "theme": about["themes"][0]["id"],
            "character": character(),
            "road": road,
            **request,
        }
        for size in about["sizes"]
    ]
    out = []
    for size, result in zip(about["sizes"], ask(*requests), strict=True):
        report = result["report"]
        texts = {text["id"]: text for text in report["texts"] if text["id"].startswith("route.")}
        out.append((size["id"], report["route"], texts))
    return out


@needs_node
def test_two_continents_share_one_sea_and_one_scale(about):
    for size, reach, texts in draw_road(about, ROAD):
        kalimdor, east = reach["plates"]
        assert (kalimdor["id"], east["id"]) == (1, 0), size
        assert kalimdor["shared"] and east["shared"] and not kalimdor["zoomed"] and not east["zoomed"], size
        assert kalimdor["x"] + kalimdor["w"] == pytest.approx(east["x"]), size  # no gap: one sheet of sea
        for plate in (kalimdor, east):
            # All of the continent is there, inside its part of the sea - in facets.
            assert plate["mapped"] and inside(plate["land"], plate), (size, plate["land"])
            assert plate["land"]["faces"] > 150, size
            # ... and the road runs on it.
            land = plate["land"]
            on_land = {
                "x": land["left"],
                "y": land["top"],
                "w": land["right"] - land["left"],
                "h": land["bottom"] - land["top"],
            }
            assert inside(plate["road"], on_land, slack=1), size
        # One scale: Kalimdor is the taller of the two by as much as it is in the world.
        tall = lambda plate: plate["land"]["bottom"] - plate["land"]["top"]  # noqa: E731
        assert tall(kalimdor) / tall(east) == pytest.approx((11240 + 10670) / (3680 + 15210), rel=0.02), size
        # The map is given the room there is.
        wide = (kalimdor["land"]["right"] - kalimdor["land"]["left"]) / kalimdor["w"]
        assert max(tall(kalimdor) / kalimdor["h"], wide) > 0.78, size
        assert texts["route.start"]["text"] == "START", size
        assert texts["route.now"]["text"] in (f"NOW {MIDDOT} LV 17", "LV 17"), size
        assert {key for key in texts if key.startswith("route.level")} == {"route.level10", "route.level20"}, size
        assert texts["route.land1"]["text"] == "KALIMDOR" and texts["route.land0"]["text"] == "EASTERN KINGDOMS", size
        assert texts["route.land1"]["right"] <= texts["route.land0"]["left"], size
        # A long road, wound tight around every village, is drawn finer.
        assert reach["fine"] > 0.5, size


@needs_node
def test_a_short_road_on_one_continent_is_shown_close_up(about):
    """A new character's first evenings: the plate closes in on the road,
    and a small map in a corner says where on the continent that is."""
    for size, reach, texts in draw_road(about, ONE_LAND):
        (plate,) = reach["plates"]
        assert plate["mapped"] and plate["zoomed"] and not plate["shared"], size
        # The land runs past the plate's edge: this is a part of it ...
        land = plate["land"]
        assert land["bottom"] - land["top"] > 1.4 * plate["h"], size
        # ... and the road is a good part of what is shown.
        road = plate["road"]
        assert max((road["right"] - road["left"]) / plate["w"], (road["bottom"] - road["top"]) / plate["h"]) > 0.22, (
            size
        )
        inset = plate["inset"]
        assert inset and inside(box_of(inset), plate), size
        assert inset["w"] < 0.5 * plate["w"] and inset["h"] < 0.5 * plate["h"], size
        # The small map keeps out of the road's way.
        assert apart(box_of(inset), road), size
        assert texts["route.land0"]["text"] == "EASTERN KINGDOMS", size
        assert reach["fine"] == 0, size  # a short road is drawn at full weight
    # The very first steps: still a map of somewhere, with the gem on it.
    for size, reach, texts in draw_road(about, FIRST_STEPS, character=EMPTY):
        (plate,) = reach["plates"]
        assert plate["zoomed"] and plate["inset"], size
        assert texts["route.sub"]["text"] == "MY ROAD STARTS HERE", size
        assert not any(key.startswith("route.key") for key in texts), size
        # (Start and gem are one place: one mark, one word.)
        assert texts["route.now"]["text"] == f"NOW {MIDDOT} LV 1" and "route.start" not in texts, size
        # Never closer than a fifth of the continent's length: the coast still says where this is.
        land = plate["land"]
        assert 4.0 * plate["h"] < land["bottom"] - land["top"] <= 5.6 * plate["h"], size


@needs_node
def test_a_close_up_at_the_coast_looks_at_the_land_not_out_to_sea(about):
    """A road on the west coast: the plate shows the land east of it rather
    than open water beyond the continent's edge - and the small map goes to
    the corner away from the road, framing what the plate shows."""
    for size, reach, _texts in draw_road(about, COAST):
        (plate,) = reach["plates"]
        assert plate["zoomed"], size
        land, road, inset = plate["land"], plate["road"], plate["inset"]
        # The continent's west edge is at the plate's west edge (within the road's own margin).
        assert land["left"] <= plate["x"] + 0.16 * plate["w"], size
        assert land["right"] > plate["x"] + plate["w"], size  # ... and land runs on to the east
        # The road is therefore in the west of the plate - north up, west left - and the small map in the east.
        assert (road["left"] + road["right"]) / 2 < plate["x"] + 0.4 * plate["w"], size
        assert inset["x"] > plate["x"] + plate["w"] / 2, size
        assert apart(box_of(inset), road), size
        # Its frame marks the west coast, three quarters of the way south: Westfall.
        frame = inset["frame"]
        assert frame and inside(box_of(frame), inset, slack=1.5), size
        assert frame["x"] + frame["w"] / 2 < inset["x"] + inset["w"] / 2, size
        assert inset["y"] + 0.55 * inset["h"] < frame["y"] + frame["h"] / 2 < inset["y"] + 0.9 * inset["h"], size
        # ... a fifth of the continent's length, as the plate shows.
        assert 0.14 < frame["h"] / inset["h"] < 0.3, size


@needs_node
def test_a_long_road_on_one_continent_is_shown_on_all_of_it(about):
    for size, reach, _texts in draw_road(about, LONG_LAND):
        (plate,) = reach["plates"]
        assert plate["mapped"] and not plate["zoomed"] and plate["inset"] is None, size
        assert inside(plate["land"], plate), size


@needs_node
def test_the_picture_does_not_jump_as_a_road_grows(about):
    """From the close-up to the whole continent the road never shrinks by
    much: a yard more of road must not halve it on the picture."""

    def drawn(length: int) -> tuple[bool, float]:
        road = road_of(stretch(0, -9500, -300, (length, 0), level=20))
        (plate,) = ask(
            {
                "op": "draw",
                "size": "portrait",
                "theme": about["themes"][0]["id"],
                "character": character(),
                "road": road,
            }
        )[0]["report"]["route"]["plates"]
        return plate["zoomed"], plate["road"]["bottom"] - plate["road"]["top"]

    before = drawn(1000)
    assert before[0]
    for length in range(1500, 14001, 500):
        after = drawn(length)
        assert after[1] > 0.66 * before[1], (length, before, after)
        before = after
    assert not before[0]  # by now all of the continent


@needs_node
def test_roads_without_a_map_are_drawn_on_an_empty_chart(about):
    for size, reach, texts in draw_road(about, ROAD, lands=False):
        assert [(plate["mapped"], plate["zoomed"], plate["shared"], plate["land"]) for plate in reach["plates"]] == [
            (False, False, False, None)
        ] * 2, size
        left, right = reach["plates"]
        assert left["x"] + left["w"] < right["x"], size  # two plates, a gap between
        # Each road fills its plate.
        for plate in reach["plates"]:
            road = plate["road"]
            assert (
                max((road["right"] - road["left"]) / plate["w"], (road["bottom"] - road["top"]) / plate["h"]) > 0.6
            ), size
        assert "route.start" in texts and "route.now" in texts, size
    # A map for one continent and none for the other: each plate as it can be.
    for size, reach, _texts in draw_road(about, MIXED):
        assert [(plate["id"], plate["mapped"], plate["shared"]) for plate in reach["plates"]] == [
            (1, True, False),
            (530, False, False),
        ], size
    # A few yards without a map are not blown up to fill the plate.
    for size, reach, _texts in draw_road(about, FIRST_STEPS, lands=False):
        road = reach["plates"][0]["road"]
        assert road["right"] - road["left"] < 12 and road["bottom"] - road["top"] < 12, size


@needs_node
def test_the_three_kinds_of_road_are_drawn_differently(about):
    request = {
        "op": "draw",
        "size": "portrait",
        "theme": about["themes"][0]["id"],
        "character": character(),
        "calls": True,
    }
    walked = road_of(stretch(1, 0, -3000, (3000, 0)))
    still = road_of(stretch(1, 0, -3000, (3000, 0), belt=False))
    flown = road_of(stretch(1, 0, -3000, (3000, 0)), stretch(1, 3000, -3000, (2000, 500), mode="t", belt=False))
    crossed = road_of(stretch(1, 0, -3000, (3000, 0)), stretch(0, -9000, 0, (3000, 0)))
    results = ask(*[{**request, "road": road} for road in (walked, still, flown, crossed)])
    dashes = [[call for call in result["calls"] if call[0] == "setLineDash" and len(call) > 1] for result in results]
    strokes = [sum(1 for call in result["calls"] if call[0] == "stroke") for result in results]
    keys = [result["report"]["route"]["key"] for result in results]
    # The key under the map has a piece of each kind of line that is on it.
    assert keys == [["walked"], ["road"], ["walked", "flight"], ["walked", "flight"]]
    # Only a flight - and the passage from one continent to the other - is
    # dashed: once on the map, once in the key.
    assert [len(found) for found in dashes] == [0, 0, 2, 2]
    # A road walked for real has a glow that the same road with the belt
    # still has not: one stroke more on the map, one more in the key.
    assert strokes[0] == strokes[1] + 2
    words = [{text["id"]: text["text"] for text in result["report"]["texts"]} for result in results]
    assert words[0]["route.key-walked"] == "100% WALKED FOR REAL" and "route.key-road" not in words[0]
    assert words[1]["route.key-road"] == "NOT WALKED FOR REAL" and "route.key-walked" not in words[1]
    assert words[2]["route.key-flight"] == "FLIGHT" and words[3]["route.key-flight"] in (
        "FLIGHT / BOAT",
        "FLIGHT, BOAT",
    )


@needs_node
def test_the_passage_between_continents_is_an_arc_from_shore_to_shore(about):
    crossed = road_of(stretch(1, -3680, -4375, (300, 200)), stretch(0, -3740, -830, (-300, -400)))
    for size, reach, _texts in draw_road(about, crossed):
        kalimdor, east = reach["plates"]
        (arc,) = reach["arcs"]
        # From where the road ends on the one to where it begins on the other ...
        start = {"left": arc["from"][0], "right": arc["from"][0], "top": arc["from"][1], "bottom": arc["from"][1]}
        end = {"left": arc["to"][0], "right": arc["to"][0], "top": arc["to"][1], "bottom": arc["to"][1]}
        assert inside(start, kalimdor) and inside(end, east), size
        assert inside(start, {k: v for k, v in zip("xywh", _box(kalimdor["road"]), strict=True)}, slack=1), size
        assert inside(end, {k: v for k, v in zip("xywh", _box(east["road"]), strict=True)}, slack=1), size
        # ... bowed upwards, and never above the map.
        assert arc["top"] < min(arc["from"][1], arc["to"][1]) - 4, size
        assert arc["top"] >= kalimdor["y"], size
    # A passage to a continent that is not on the picture: no arc.
    elsewhere = {**crossed, "crossings": [{"from": crossed["end"], "to": {"c": 530, "x": 0, "y": 0}}]}
    assert all(reach["arcs"] == [] for _size, reach, _texts in draw_road(about, elsewhere))


def _box(reach: dict[str, float]) -> tuple[float, float, float, float]:
    return reach["left"], reach["top"], reach["right"] - reach["left"], reach["bottom"] - reach["top"]


@needs_node
def test_north_is_up_and_west_is_left_on_the_picture(about):
    """The map and the road go through one calculation, so a mirrored
    picture would agree with itself: these roads say which way round it is."""
    # From Feathermoon in the west of Kalimdor to Orgrimmar in the east.
    west_to_east = road_of(stretch(1, -4370, 3340, (3000, -3800), (3046, -3855)))
    # From Shadowglen in the north to Gadgetzan in the south.
    north_to_south = road_of(stretch(1, 10311, 832, (-8700, -2300), (-8750, -2298)))
    for road, axis in ((west_to_east, "left"), (north_to_south, "top")):
        for size, reach, _texts in draw_road(about, road):
            marks = {mark["kind"]: mark for mark in reach["marks"]}
            assert marks["ring"][axis] + 40 < marks["gem"][axis], (size, axis)
    # The small map in a close-up is the same way round: Teldrassil, top left.
    teldrassil = road_of(stretch(1, 10311, 832, (-200, 100), (-150, 60)))
    for size, reach, _texts in draw_road(about, teldrassil):
        (plate,) = reach["plates"]
        inset, frame = plate["inset"], plate["inset"]["frame"]
        assert frame["x"] + frame["w"] / 2 < inset["x"] + inset["w"] / 2, size
        assert frame["y"] + frame["h"] / 2 < inset["y"] + inset["h"] / 3, size


@needs_node
def test_nothing_of_a_continent_is_drawn_outside_its_sea(about):
    """In a close-up the land is several times the size of the plate: it is
    the clipping that keeps it off the rest of the picture."""
    base = {
        "op": "draw",
        "size": "portrait",
        "theme": about["themes"][0]["id"],
        "character": character(),
        "calls": True,
    }
    off = {"options": {"tiles": {"route": False}}}

    def clips(result: dict[str, Any]) -> int:
        return sum(1 for call in result["calls"] if call[0] == "clip")

    for road, seas, plates in ((ONE_LAND, 1, 1), (ROAD, 1, 2), (MIXED, 2, 2)):
        without, with_road = ask({**base, "road": road, **off}), ask({**base, "road": road})
        # (One clip for each sea's grid, one for each plate's land and road -
        # and one that the panel around them has, as every panel does.)
        assert clips(with_road[0]) - clips(without[0]) == seas + plates + 1
        # ... and each is undone again before the marks and the words are put on.
        calls = with_road[0]["calls"]
        assert sum(1 for call in calls if call[0] == "save") == sum(1 for call in calls if call[0] == "restore")


@needs_node
def test_marks_on_a_short_road_do_not_pile_up(about):
    # Start, a level reached and the gem within a few yards of one another.
    crowded = road_of(stretch(0, -9000, 200, (40, 0), level=9), stretch(0, -8960, 200, (30, 10), level=10))
    # A level-60 character: the last tens were reached close to where it stands now.
    veteran = road_of(
        stretch(1, 5931, -6199, (800, 1400), level=49),
        stretch(1, 6731, -4799, (300, 600), level=50),
        stretch(1, 7031, -4199, (-200, -300), level=59),
        stretch(1, 6831, -4499, (-108, -160), level=60),
    )
    for road, level in ((crowded, 10), (veteran, 60), (ROAD, 23), (ONE_LAND, 10), (COAST, 12)):
        for size, reach, texts in draw_road(about, road, character=character(level=level)):
            words = [
                text
                for key, text in texts.items()
                if key in ("route.start", "route.now") or key.startswith("route.level")
            ]
            # The gem and its level always - before any stud gets its number.
            assert "route.now" in texts and str(level) in texts["route.now"]["text"], size
            # No stud for the level the gem already gives.
            assert f"route.level{level}" not in texts, size
            for a, b in itertools.combinations(words, 2):
                assert apart(a, b), (size, a["id"], b["id"])
            # The marks themselves - gem, ring, studs, the small map - are apart too, and no word is on one.
            marks = reach["marks"]
            assert [mark["kind"] for mark in marks].count("gem") == 1, size
            for a, b in itertools.combinations(marks, 2):
                assert apart(a, b), (size, a["kind"], b["kind"])
            for word in words:
                for mark in marks:
                    assert apart(word, mark), (size, word["id"], mark["kind"])


@needs_node
def test_words_on_the_map_keep_off_the_road(about):
    """A number written over the line it belongs to cannot be read."""
    for road in (ONE_LAND, COAST, LONG_LAND):
        for size, reach, _texts in draw_road(about, road):
            assert reach["onRoad"] == [], size
    # The demo's road is wound tight around every village: START and the gem's
    # level still find a place off it; a level's number may have to sit on it
    # (it then gets a small plate of its own).
    for size, reach, texts in draw_road(about, ROAD):
        assert all(tag.startswith("level") for tag in reach["onRoad"]), (size, reach["onRoad"])
        assert "route.level10" in texts and "route.level20" in texts, size
    # Where there is no room off the road, the word is still written.
    knot = road_of(
        *[stretch(0, -9000 + 3 * i, 200, (0, 60), (0, -120), (0, 60), (60, 0), (-120, 0), level=9) for i in range(8)]
    )
    for size, _reach, texts in draw_road(about, knot):
        assert "route.now" in texts, size


@needs_node
def test_in_a_story_the_road_stands_beside_the_tiles(about):
    result = ask({"op": "draw", "size": "story", "theme": about["themes"][0]["id"], **CASES["road"]})[0]
    panels = {panel["id"]: panel for panel in result["report"]["panels"]}
    route = panels["route"]
    small = [panels[key] for key in ("level", "played", "charSteps", "calories")]
    # One column of tiles, to the right of the road, over the same height.
    assert len({round(tile["x"], 2) for tile in small}) == 1
    assert all(tile["x"] >= route["x"] + route["w"] for tile in small)
    assert min(tile["y"] for tile in small) == pytest.approx(route["y"], abs=1)
    assert max(tile["y"] + tile["h"] for tile in small) == pytest.approx(route["y"] + route["h"], abs=1)
    # A tall panel for tall continents.
    assert route["h"] > 1.2 * route["w"] and route["w"] > 0.45 * (route["w"] + small[0]["w"])
    # ... and with no tiles to stand beside, the road lies across the picture.
    alone = ask({"op": "draw", "size": "story", "theme": about["themes"][0]["id"], **CASES["road alone"]})[0]["report"]
    (panel,) = [panel for panel in alone["panels"] if panel["id"] == "route"]
    assert panel["w"] > 900


@needs_node
def test_the_road_gets_more_room_than_a_small_tile(about):
    requests = [
        {"op": "draw", "size": size["id"], "theme": about["themes"][0]["id"], **CASES["road"]}
        for size in about["sizes"]
    ]
    for size, result in zip(about["sizes"], ask(*requests), strict=True):
        panels = {panel["id"]: panel for panel in result["report"]["panels"]}
        assert set(panels) == {"steps", "route", "level", "played", "charSteps", "calories"}
        area = lambda panel: panel["w"] * panel["h"]  # noqa: E731
        assert area(panels["route"]) >= 1.5 * area(panels["level"]), size["id"]
        # The four small tiles stay as large as one another.
        small = [panels[key] for key in ("level", "played", "charSteps", "calories")]
        assert max(map(area, small)) <= 1.05 * min(map(area, small)), size["id"]


@needs_node
def test_a_picture_without_the_road_is_laid_out_as_before(about):
    """Switching the road off - or having none - gives the picture there was
    before roads: the same panels in the same places."""
    for size in about["sizes"]:
        base = {"op": "draw", "size": size["id"], "theme": about["themes"][0]["id"], "character": character()}
        none, off, other = ask(
            base,
            {**base, "road": ROAD, "options": {"tiles": {"route": False}}},
            {**base, "road": {**ROAD, "id": "0badf00d"}},
        )
        assert none["report"]["panels"] == off["report"]["panels"] == other["report"]["panels"]
        assert none["report"].get("route") is None and off["report"].get("route") is None


@needs_node
def test_long_words_shrink_before_they_are_cut(about):
    requests = [
        {"op": "draw", "size": size["id"], "theme": about["themes"][0]["id"], "charWidth": width, **CASES["huge"]}
        for size in about["sizes"]
        for width in (0.5, 1.4)
    ]
    for request, result in zip(requests, ask(*requests), strict=True):
        texts = {text["id"]: text for text in result["report"]["texts"]}
        title = texts["title"]
        if request["charWidth"] == 0.5:
            assert title["text"] == LONG_WORDS["title"].upper()  # shrunk if need be, but whole
        else:
            # Absurdly wide letters: only now is the title cut, and it says so.
            assert title["text"].endswith(ELLIPSIS) and LONG_WORDS["title"].upper().startswith(
                title["text"][:-1].rstrip()
            )
        # Numbers are never cut, whatever happens.
        assert texts["steps.value"]["text"] == "~9,999,999"
        assert texts["charSteps.value"]["text"] == "~9,999,999"


@needs_node
def test_a_story_keeps_clear_of_the_apps_own_bars(about):
    """Instagram and the like cover the top and the bottom of a story with
    their own controls: only the fine frame and the credit may be there."""
    requests = [{"op": "draw", "size": "story", "theme": about["themes"][0]["id"], **case} for case in CASES.values()]
    for result in ask(*requests):
        report = result["report"]
        assert all(panel["y"] >= 200 and panel["y"] + panel["h"] <= 1920 - 200 for panel in report["panels"])
        words = [text for text in report["texts"] if text["id"] != "credit"]
        assert all(text["top"] >= 200 and text["bottom"] <= 1920 - 200 for text in words)


@needs_node
def test_the_same_card_is_always_drawn_the_same(about):
    """The page redraws while numbers tick: the backdrop must not flicker."""
    request = {"op": "draw", "size": "portrait", "theme": about["themes"][0]["id"], "calls": True, **CASES["usual"]}
    first, second = ask(request, request)
    assert first["calls"] == second["calls"] and len(first["calls"]) > 500
    assert first["texts"] >= 15
