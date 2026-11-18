"""The road map window (UI/RoadMap.lua): the game's map art, the road drawn
over it, zooming and dragging - against the mock's small world
(tests/addon/lua/wow_env.lua: Kalimdor is map 1414 / continent 1, the
Eastern Kingdoms 1415 / continent 0; art is 1002 x 668 in 256-pixel tiles)."""

from __future__ import annotations

import math

import pytest

from . import ui_driver as ui
from .harness import lua_list

# Kalimdor in the mock: world x (north) 12000 .. -12000 top to bottom,
# world y (west) 4000 .. -12000 left to right.
ART_W, ART_H = 1002, 668


def on_kalimdor(x: float, y: float) -> tuple[float, float]:
    """World yards -> where that is on the continent's art (x right, y down)."""
    return (y - 4000) / -16000 * ART_W, (x - 12000) / -24000 * ART_H


def travel(rt, legs, *, start=(-600, -4200), **state):
    """Move the character along straight legs of (north, west) yards."""
    x, y = start
    for north, west in legs:
        looks = max(1, int(math.hypot(north, west) / 1.75))
        for index in range(1, looks + 1):
            rt.set(worldX=x + north * index / looks, worldY=y + west * index / looks, speed=7.0, **state)
            rt.advance(0.25)
        x, y = x + north, y + west
    return x, y


def open_map(rt):
    rt.slash("/walka map")
    rt.advance(0.5)
    frame = rt.global_("WalkaRoadMapFrame")
    assert frame is not None and ui.is_visible(rt, frame)
    return frame


def lines(rt, frame) -> list[dict]:
    """The lines on show, with their colour as [r, g, b, a]."""
    found = rt.M.descendants(frame, "Line", True)
    out = []
    for widget in lua_list(found):
        line = dict(rt.inspect(widget))
        line["color"] = list(lua_list(line["color"]))
        out.append(line)
    return out


def tiles(rt, frame) -> list[dict]:
    found = rt.M.descendants(frame, "Texture", True)
    shown = [rt.inspect(widget) for widget in lua_list(found)]
    return [tile for tile in shown if isinstance(tile.get("texture"), (int, float))]


def content_scale(rt, frame) -> float:
    """How large the map is drawn (the art is always there to ask)."""
    return tiles(rt, frame)[0]["scale"]


# ---- opening it ---------------------------------------------------------------------------------


def test_the_map_opens_from_the_slash_command_and_the_hud_menu(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    frame = open_map(rt)
    assert "road - Kalimdor" in ui.texts(rt, frame)
    assert "WalkaRoadMapFrame" in lua_list(rt.global_("UISpecialFrames")), "Escape closes it"
    rt.slash("/walka map")
    assert not ui.is_visible(rt, frame)
    assert "button:Road map" in ui.context_menu(rt, ui.hud(rt))
    ui.menu_click(rt, "Road map")
    assert ui.is_visible(rt, frame)
    rt.slash("/walka road")           # the same window
    assert not ui.is_visible(rt, frame)


def test_the_continents_own_picture_is_drawn_as_tiles(ui_rt):
    rt = ui_rt
    travel(rt, [(200, 0)])
    frame = open_map(rt)
    art = tiles(rt, frame)
    assert sorted(tile["texture"] for tile in art) == [141400 + index for index in range(1, 13)]
    # Four columns by three rows of 256-pixel tiles, edge to edge.
    scale = art[0]["scale"]
    assert all(tile["width"] == pytest.approx(256 * scale) for tile in art)
    assert len({round(tile["left"], 1) for tile in art}) == 4
    assert len({round(tile["bottom"], 1) for tile in art}) == 3


def test_the_road_is_drawn_where_the_character_went(ui_rt):
    rt = ui_rt
    start = (-600, -4200)
    corner = (start[0] + 2400, start[1])
    end = travel(rt, [(2400, 0), (0, 2400)], start=start)
    frame = open_map(rt)
    drawn = lines(rt, frame)
    assert 2 <= len(drawn) <= 12, "an L: two legs, a little slack at the corner"
    first, last = drawn[0], drawn[-1]
    sx, sy = on_kalimdor(*start)
    ex, ey = on_kalimdor(*end)
    cx, cy = on_kalimdor(*corner)
    # (Line offsets are from the art's top-left corner, y pointing up.)
    assert (first["startX"], -first["startY"]) == pytest.approx((sx, sy), abs=1.0)
    assert (last["endX"], -last["endY"]) == pytest.approx((ex, ey), abs=1.5)
    assert any(math.dist((line["endX"], -line["endY"]), (cx, cy)) < 1.5 for line in drawn), "the corner is kept"
    # Not on the treadmill: dim.
    assert all(line["color"][3] < 0.8 for line in drawn)
    # The lines keep their width on screen whatever the zoom: two pixels.
    assert all(line["thickness"] * line["scale"] == pytest.approx(2 * drawn[0]["scale"] / content_scale(rt, frame), rel=0.01) for line in drawn)
    assert "Road recorded" in " ".join(ui.texts(rt, frame))


def test_stretches_walked_on_the_treadmill_are_bright(ui_rt):
    rt = ui_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka start")
    travel(rt, [(1200, 0)], start=(0, -4200))
    rt.slash("/walka stop")
    frame = open_map(rt)
    drawn = lines(rt, frame)
    bright = [line for line in drawn if line["color"][3] == 1]
    glow = [line for line in drawn if line["thickness"] > 2 * bright[0]["thickness"]]
    assert bright and glow and len(glow) == len(bright), "a soft wider line under each bright one"
    assert any(0.5 < line["color"][3] < 0.6 for line in drawn), "and the walk before it is dim"
    text = " ".join(ui.texts(rt, frame))
    assert "walked on the treadmill" in text and " 0% " not in text


def test_levels_have_their_own_colours(ui_rt):
    rt = ui_rt
    rt.set(level=9)
    travel(rt, [(600, 0)])
    rt.set(level=25)
    travel(rt, [(600, 0)], start=(0, -4200))
    frame = open_map(rt)
    colours = {tuple(round(part, 2) for part in line["color"][:3]) for line in lines(rt, frame)}
    palette = [tuple(lua_list(colour)) for colour in lua_list(rt.ns.RoadMap.LEVEL_COLORS)]
    assert colours == {tuple(round(p, 2) for p in palette[0]), tuple(round(p, 2) for p in palette[2])}


# ---- the view -------------------------------------------------------------------------------------


def canvas_of(rt, frame):
    """The frame that takes the mouse wheel."""
    for widget in lua_list(rt.M.descendants(frame, "Frame", True)):
        if rt.M.has_script(widget, "OnMouseWheel"):
            return widget
    raise AssertionError("no canvas")


def test_the_wheel_zooms_and_stops_at_both_ends(ui_rt):
    rt = ui_rt
    travel(rt, [(2400, 0), (0, 2400)])
    frame = open_map(rt)
    canvas = canvas_of(rt, frame)
    fitted = content_scale(rt, frame)
    rt.M.scroll_wheel(canvas, -1)
    rt.advance(0.5)
    assert content_scale(rt, frame) == pytest.approx(fitted), "never smaller than the whole continent"
    rt.M.scroll_wheel(canvas, 1)
    rt.advance(0.5)
    assert content_scale(rt, frame) == pytest.approx(fitted * 1.4)
    for _ in range(30):
        rt.M.scroll_wheel(canvas, 1)
    rt.advance(0.5)
    top = content_scale(rt, frame)
    assert 10 < top / fitted < 30
    rt.M.scroll_wheel(canvas, 1)
    rt.advance(0.5)
    assert content_scale(rt, frame) == pytest.approx(top), "and no closer than the limit"
    rt.click(ui.button(rt, frame, "Fit"))
    rt.advance(0.5)
    assert content_scale(rt, frame) == pytest.approx(fitted)


def test_lines_are_redrawn_finer_when_zoomed_in(ui_rt):
    """A wiggly road: from afar a few lines do, close up it takes more."""
    rt = ui_rt
    legs = [(60, 25 if index % 2 else -25) for index in range(60)]
    travel(rt, legs)
    frame = open_map(rt)
    far = len(lines(rt, frame))
    canvas = canvas_of(rt, frame)
    rt.click(ui.button(rt, frame, "Me"))
    for _ in range(8):
        rt.M.scroll_wheel(canvas, 1)
    rt.advance(0.5)
    rt.click(ui.button(rt, frame, "Me"))
    rt.advance(0.5)
    assert len(lines(rt, frame)) > far


def test_the_other_continent_and_a_place_with_no_road(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    frame = open_map(rt)
    rt.click(ui.button(rt, frame, ">"))
    rt.advance(0.3)
    texts = ui.texts(rt, frame)
    assert "road - Eastern Kingdoms" in texts and "No road on this continent yet." in texts
    assert lines(rt, frame) == []
    assert sorted(tile["texture"] for tile in tiles(rt, frame))[0] == 141501
    rt.click(ui.button(rt, frame, "<"))
    rt.advance(0.3)
    assert "road - Kalimdor" in ui.texts(rt, frame) and lines(rt, frame)


def test_you_are_here(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    frame = open_map(rt)
    arrows = [
        rt.inspect(widget)
        for widget in lua_list(rt.M.descendants(frame, "Texture", None))
        if rt.inspect(widget).get("texture") == "Interface\\WorldMap\\WorldMapArrow"
    ]
    assert len(arrows) == 1 and arrows[0]["visible"]
    rt.set(facing=1.25)
    rt.advance(0.3)
    arrow = next(
        rt.inspect(widget)
        for widget in lua_list(rt.M.descendants(frame, "Texture", None))
        if rt.inspect(widget).get("texture") == "Interface\\WorldMap\\WorldMapArrow"
    )
    assert arrow["rotation"] == pytest.approx(1.25)
    # In a dungeon the game cannot place the character: no mark.
    rt.set(instance=True)
    rt.advance(0.3)
    arrow = next(
        rt.inspect(widget)
        for widget in lua_list(rt.M.descendants(frame, "Texture", None))
        if rt.inspect(widget).get("texture") == "Interface\\WorldMap\\WorldMapArrow"
    )
    assert not arrow["visible"]


# ---- when things are not as expected -----------------------------------------------------------


def test_a_very_long_road_is_drawn_with_a_bounded_number_of_lines(make_rt, ui_rt):
    """Tens of thousands of points (a road to level 60 has over a hundred
    thousand): the window thins them to what can be told apart, and never
    creates more than its budget of lines."""
    # A spiral that winds outwards for 30,000 points, twenty yards apart.
    steps = []
    radius, angle = 500.0, 0.0
    x, y = round(radius), -4000
    for _ in range(30000):
        angle += 20.0 / radius
        radius += 0.1
        nx, ny = round(radius * math.cos(angle)), round(-4000 + radius * math.sin(angle))
        steps.append(f"3,{nx - x},{ny - y}")
        x, y = nx, ny
    saved = (
        "{version=1,guid='Player-4321-0ABCDEF1',key='Tester-Azeroth',yards=600000,beltYards=200000,"
        "segments={{c=1,m='w',l=12,t=1800000000,x=500,y=-4000,n=30000,d='" + ";".join(steps) + "'}}}"
    )
    rt = make_rt(saved_variables={"WalkaCharDB": saved})
    frame = open_map(rt)
    drawn = lines(rt, frame)
    assert 50 < len(drawn) <= 2500
    canvas = canvas_of(rt, frame)
    for _ in range(6):
        rt.M.scroll_wheel(canvas, 1)
        rt.advance(0.4)
        assert len(lines(rt, frame)) <= 2500
    assert len(lua_list(rt.M.descendants(frame, "Line", None))) <= 2501, "lines are reused, not piled up"


def test_a_client_that_says_nothing_about_its_maps(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    rt.set(mapsBroken=True)
    frame = open_map(rt)
    page = " ".join(ui.texts(rt, frame))
    assert "no map to draw on" in page and "Your road is still being recorded" in page
    assert lines(rt, frame) == []
    rt.slash("/walka mapcheck")
    assert any("0 continent map(s) found" in line for line in rt.printed)


def test_hidden_positions_do_not_break_the_window(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    rt.set(positionSecret=True)
    frame = open_map(rt)
    rt.advance(2)
    assert ui.is_visible(rt, frame) and lines(rt, frame)
    assert rt.find_secrets() == []


def test_mapcheck_prints_what_the_game_answers(ui_rt):
    rt = ui_rt
    travel(rt, [(400, 0)])
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    text = "\n".join(rt.printed)
    assert "2 continent map(s) found" in text
    assert "Kalimdor (map 1414, continent 1): art 1002x668 in 256x256 tiles, 12 picture file(s)." in text
    assert "you are on continent 1" in text and "recording is on" in text


def test_recording_can_be_switched_off_and_on_again(ui_rt):
    rt = ui_rt
    rt.slash("/walka road off")
    travel(rt, [(400, 0)])
    assert rt.walka.Get(rt.walka, "recordRoad") is False
    assert len(lua_list(rt.global_("WalkaCharDB").segments)) == 0
    rt.slash("/walka road on")
    travel(rt, [(400, 0)], start=(-200, -4200))
    rt.ns.RoadRecorder.Flush()
    assert len(lua_list(rt.global_("WalkaCharDB").segments)) == 1
