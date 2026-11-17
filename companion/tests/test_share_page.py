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

STATIC = web.STATIC_DIR
HARNESS = Path(__file__).with_name("share_harness.mjs")
NEW_SCRIPTS = ("share.js", "card.js", "cardmodel.js")
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
        [NODE, str(HARNESS), str(STATIC / "cardmodel.js"), str(STATIC / "card.js")],
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
    assert options["tiles"] == {"steps": True, "level": True, "played": True, "charSteps": True, "calories": True}
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
    assert options["tiles"] == {"steps": False, "level": False, "played": True, "charSteps": True, "calories": True}
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
}
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
                assert upper["bottom"] <= lower["top"] + 0.01, (where, tile)


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
