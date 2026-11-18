"""The road on the game's own world map (UI/WorldMapRoad.lua): on Blizzard's
map - zone, continent and world - and on OpenMap's stitched map, through a
stand-in for OpenMap's interface for other addons.

The mock's world (tests/addon/lua/wow_env.lua): Kalimdor is map 1414 /
continent 1 with the zone Durotar (1411) in it, the Eastern Kingdoms 1415 /
continent 0 with Elwynn Forest (1429); every map's art is 1002 x 668, which
is also the size of the world map's canvas. The world map itself is not in
the client snapshot, so a test asks for one (M.world_map())."""

from __future__ import annotations

import math

import pytest

from . import ui_driver as ui
from .harness import lua_list

ART_W, ART_H = 1002, 668
DUROTAR, ELWYNN, KALIMDOR, EASTERN, AZEROTH = 1411, 1429, 1414, 1415, 947


def on_map(rt, map_id: int, x: float, y: float) -> tuple[float, float]:
    """World yards -> canvas units on that map (x right, y down)."""
    m = rt.M.maps[map_id]
    return (y - m.left) / (m.right - m.left) * ART_W, (x - m.top) / (m.bottom - m.top) * ART_H


def on_world(rt, continent_map: int, x: float, y: float) -> tuple[float, float]:
    """World yards on a continent -> canvas units on the map of the world."""
    m = rt.M.maps[continent_map]
    u, v = (y - m.left) / (m.right - m.left), (x - m.top) / (m.bottom - m.top)
    min_x, max_x, min_y, max_y = lua_list(m.place)
    return (min_x + u * (max_x - min_x)) * ART_W, (min_y + v * (max_y - min_y)) * ART_H


def travel(rt, legs, *, start=(-600, -4200), **state):
    """Move the character along straight legs of (north, west) yards."""
    x, y = start
    state.setdefault("speed", 7.0)
    for north, west in legs:
        looks = max(1, int(math.hypot(north, west) / 1.75))
        for index in range(1, looks + 1):
            rt.set(worldX=x + north * index / looks, worldY=y + west * index / looks, **state)
            rt.advance(0.25)
        x, y = x + north, y + west
    return x, y


@pytest.fixture
def map_rt(make_rt):
    """A client that has a world map, with the addon loaded."""
    rt = make_rt(load=False)
    rt.M.world_map()
    rt.load_addon()
    return rt


def world_map(rt):
    return rt.global_("WorldMapFrame")


def overlay(rt):
    return rt.global_("WalkaWorldMapRoad")


def road_button(rt):
    return rt.global_("WalkaWorldMapRoadButton")


def road(rt) -> list[dict]:
    """The lines on show on the world map, colour as [r, g, b, a]."""
    frame = overlay(rt)
    if frame is None:
        return []
    out = []
    for widget in lua_list(rt.M.descendants(frame, "Line", True)):
        line = dict(rt.inspect(widget))
        line["color"] = list(lua_list(line["color"]))
        out.append(line)
    assert rt.ns.WorldMapRoad.problem is None, rt.ns.WorldMapRoad.problem
    return out


def cores(lines: list[dict]) -> list[dict]:
    """The coloured lines of the recorded road (not the dark line under
    each, nor the glow, nor the road's end - see tail())."""
    return [line for line in lines if line["subLevel"] == 2]


def tail(lines: list[dict]) -> list[dict]:
    """The coloured line from the last recorded point to where the
    character stands (drawn above the road: draw layers 3 to 5)."""
    return [line for line in lines if line["subLevel"] == 5]


def stop_recording_here(rt):
    """End the stretch being recorded where the character stands (as a
    loading screen does), so that the whole road is recorded road."""
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    rt.advance(0.3)


def ends(line: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    return (line["startX"], -line["startY"]), (line["endX"], -line["endY"])


def show(rt, map_id=None, *, settle: float = 0.5):
    rt.M.world_map_show(map_id)
    rt.advance(settle)


def view(rt, zoom: float, x: float, y: float, *, settle: float = 0.5):
    rt.M.world_map_view(zoom, x, y)
    rt.advance(settle)


def passes_through(lines: list[dict], point: tuple[float, float], slack: float = 1.5) -> bool:
    return any(math.dist(end, point) < slack for line in lines for end in ends(line))


# ---- opening it ---------------------------------------------------------------------------------


def test_the_slash_command_opens_the_world_map_with_the_road_on_it(map_rt):
    rt = map_rt
    start = (-600, -4200)
    end = travel(rt, [(1200, 0), (0, 900)], start=start)
    assert not ui.is_visible(rt, world_map(rt))
    rt.slash("/walka map")
    rt.advance(0.3)
    assert ui.is_visible(rt, world_map(rt)), "the game's own map, not a window of Walka's"
    assert rt.global_("WalkaRoadMapFrame") is None
    assert rt.M.world_map_info().mapId == DUROTAR, "the map opens where the character is: a zone, in full detail"
    lines = cores(road(rt))
    assert 2 <= len(lines) <= 6, "an L: two legs"
    assert ends(lines[0])[0] == pytest.approx(on_map(rt, DUROTAR, *start), abs=2.5)
    assert passes_through(lines, on_map(rt, DUROTAR, start[0] + 1200, start[1])), "the corner is kept"
    assert passes_through(road(rt), on_map(rt, DUROTAR, *end), slack=0.5), "and it ends where the character stands"
    # The road is a child of the map's canvas: it moves and zooms with it.
    assert ui.same(rt, rt.inspect(overlay(rt))["parent"], world_map(rt).GetCanvas(world_map(rt)))
    # Asked again, the map stays open.
    rt.slash("/walka map")
    assert ui.is_visible(rt, world_map(rt)) and rt.M.world_map_info().opened == 1
    assert rt.printed == []


def test_the_hud_menu_and_the_bare_road_command_open_it_too(map_rt):
    rt = map_rt
    travel(rt, [(400, 0)])
    assert "button:Road map" in ui.context_menu(rt, ui.hud(rt))
    ui.menu_click(rt, "Road map")
    rt.advance(0.3)
    assert ui.is_visible(rt, world_map(rt)) and road(rt)
    rt.run("WorldMapFrame:Hide()")
    rt.slash("/walka road")
    assert ui.is_visible(rt, world_map(rt))


def test_the_road_is_on_the_map_whoever_opens_it(map_rt):
    """Pressing M is all it takes."""
    rt = map_rt
    travel(rt, [(900, 0)])
    show(rt)
    assert len(cores(road(rt))) >= 1


# ---- every map of the world ---------------------------------------------------------------------


def test_the_road_keeps_to_its_place_on_zone_continent_and_world_maps(map_rt):
    rt = map_rt
    start = (-600, -4200)
    end = travel(rt, [(1200, 0)], start=start)
    for map_id, place in ((DUROTAR, lambda x, y: on_map(rt, DUROTAR, x, y)),
                          (KALIMDOR, lambda x, y: on_map(rt, KALIMDOR, x, y)),
                          (AZEROTH, lambda x, y: on_world(rt, KALIMDOR, x, y))):
        show(rt, map_id)
        lines = cores(road(rt))
        assert len(lines) == 1, map_id
        assert ends(lines[0])[0] == pytest.approx(place(*start), abs=2.5), map_id
        assert ends(lines[0])[1] == pytest.approx(place(*end), abs=2.5), map_id
    # A map of somewhere else has no road on it.
    for map_id in (EASTERN, ELWYNN):
        show(rt, map_id)
        assert road(rt) == [], map_id
    show(rt, DUROTAR)
    assert len(cores(road(rt))) == 1, "and back"


def test_both_continents_are_on_the_map_of_the_world(map_rt):
    rt = map_rt
    travel(rt, [(1200, 0)], start=(-600, -4200))
    rt.set(continent=0)
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    travel(rt, [(0, 1500)], start=(-9000, 200), continent=0)
    show(rt, AZEROTH)
    lines = cores(road(rt))
    assert len(lines) == 2
    assert passes_through(lines, on_world(rt, KALIMDOR, 600, -4200))
    assert passes_through(lines, on_world(rt, EASTERN, -9000, 1700))
    show(rt, ELWYNN)
    lines = cores(road(rt))
    assert len(lines) == 1 and ends(lines[0])[0] == pytest.approx(on_map(rt, ELWYNN, -9000, 200), abs=2.5)


def test_a_road_that_leaves_the_zone_is_drawn_to_its_edge(map_rt):
    """Durotar's map ends at 2000 yards north; the road goes on to 2600."""
    rt = map_rt
    travel(rt, [(2000, 0)], start=(600, -4200))
    show(rt, DUROTAR)
    lines = cores(road(rt))
    assert len(lines) == 1
    (x1, y1), (x2, y2) = ends(lines[0])
    assert y1 == pytest.approx(on_map(rt, DUROTAR, 600, -4200)[1], abs=2.5)
    assert y2 < 0, "it runs off the top of the map, where the map's view cuts it"
    show(rt, KALIMDOR)
    assert ends(cores(road(rt))[0])[1] == pytest.approx(on_map(rt, KALIMDOR, 2600, -4200), abs=2.5)


# ---- how it looks -------------------------------------------------------------------------------


def test_lines_keep_their_width_on_screen_at_any_zoom(map_rt):
    rt = map_rt
    travel(rt, [(1200, 0)])
    show(rt, DUROTAR)
    ui_scale = rt.inspect(rt.global_("UIParent"))["scale"]

    def widths():
        return sorted({round(line["thickness"] * line["scale"] / ui_scale, 3) for line in road(rt)})

    assert widths() == [1.8, 3.6], "the line, and the dark line under it"
    view(rt, 3, 0.4, 0.6)
    assert widths() == [1.8, 3.6]
    # While the map is still zooming the widths follow at once, frame by frame.
    rt.M.world_map_view(5, 0.4, 0.6)
    rt.frame(0.016)
    assert widths() == [1.8, 3.6]
    rt.M.set_ui_scale(0.8)
    show(rt, KALIMDOR)
    ui_scale = 0.8
    assert widths() == [1.8, 3.6]


def test_stretches_walked_on_the_treadmill_are_bright_with_a_glow(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka start")
    travel(rt, [(1200, 0)], start=(0, -4200))
    rt.slash("/walka stop")
    show(rt, DUROTAR)
    lines = road(rt)
    bright = [line for line in cores(lines) if line["color"][3] == 1]
    dim = [line for line in cores(lines) if line["color"][3] < 1]
    glow = [line for line in lines if line["subLevel"] == 1]
    casing = [line for line in lines if line["subLevel"] == 0]
    assert bright and dim and len(glow) == len(bright), "a soft wide line under each bright one"
    assert len(casing) == len(bright) + len(dim), "and a dark line under every line, to tell it from the map"
    assert all(line["color"][:3] == [0, 0, 0] for line in casing)
    assert all(g["thickness"] > 2.5 * b["thickness"] for g, b in zip(glow, bright))
    assert bright[0]["thickness"] > dim[0]["thickness"]


def test_levels_have_their_colours_and_flights_are_thin(map_rt):
    rt = map_rt
    rt.set(level=9)
    travel(rt, [(600, 0)])
    rt.set(level=10)
    travel(rt, [(600, 0)], start=(0, -4200))
    travel(rt, [(900, 0)], start=(600, -4200), taxi=True, speed=30.0)
    show(rt, DUROTAR)
    lines = road(rt)
    colors = rt.ns.RoadMap.LEVEL_COLORS
    first, second = list(lua_list(colors[1])), list(lua_list(colors[2]))
    drawn = cores(lines)
    assert drawn[0]["color"][:3] == first and any(line["color"][:3] == second for line in drawn)
    flight = [line for line in drawn if line["color"][:3] == list(lua_list(rt.ns.RoadMap.FLIGHT_COLOR))]
    assert flight and all(line["thickness"] < drawn[0]["thickness"] for line in flight)
    assert len([line for line in lines if line["subLevel"] == 0]) == len(drawn) - len(flight), "no dark line under a flight"


def test_the_road_lies_over_the_map_art_and_under_what_the_map_marks(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt)
    info = rt.inspect(overlay(rt))
    assert info["level"] == 2001, "one above the explored places' art (2000 in the mock)"
    assert info["mouse"] is False, "clicks and the wheel go to the map"
    assert info.get("strata") is None, "the map's own strata"
    assert rt.M.has_script(overlay(rt), "OnMouseDown") is False


# ---- the view -----------------------------------------------------------------------------------


def spiral(points: int, continent: int = 1, level: int = 12) -> str:
    """A saved road: a spiral winding out from (500, -4000), twenty yards a point."""
    steps = []
    radius, angle = 500.0, 0.0
    x, y = round(radius), -4000
    for _ in range(points):
        angle += 20.0 / radius
        radius += 0.1
        nx, ny = round(radius * math.cos(angle)), round(-4000 + radius * math.sin(angle))
        steps.append(f"3,{nx - x},{ny - y}")
        x, y = nx, ny
    return (
        f"{{c={continent},m='w',l={level},t=1800000000,x=500,y=-4000,n={points},d='" + ";".join(steps) + "'}"
    )


def saved_road(*segments: str) -> dict[str, str]:
    return {"WalkaCharDB": (
        "{version=1,guid='Player-4321-0ABCDEF1',key='Tester-Azeroth',yards=600000,beltYards=200000,"
        "segments={" + ",".join(segments) + "}}"
    )}


def wiggle(points: int, *, start=(-1500, -4500), amplitude=30) -> str:
    """A saved road running north in bends `amplitude` yards wide, twenty yards a point."""
    steps = []
    x, y = start
    for index in range(1, points + 1):
        nx, ny = start[0] + 20 * index, round(start[1] + amplitude * math.sin(index / 3))
        steps.append(f"3,{nx - x},{ny - y}")
        x, y = nx, ny
    return f"{{c=1,m='w',l=12,t=1800000000,x={start[0]},y={start[1]},n={points},d='" + ";".join(steps) + "'}"


def test_zooming_in_draws_the_road_finer_and_only_what_is_in_sight(make_rt):
    """A road of small bends, 3000 yards long: from afar the bends are cut
    straight, close up they are drawn."""
    rt = make_rt(load=False, saved_variables=saved_road(wiggle(150, amplitude=12)))
    rt.M.world_map()
    rt.load_addon()
    show(rt, AZEROTH)
    world = cores(road(rt))
    show(rt, KALIMDOR)
    continent = cores(road(rt))
    show(rt, DUROTAR)
    zone = cores(road(rt))
    assert 1 <= len(world) <= len(continent) < len(zone)
    assert len(world) <= 3, "on the map of the world the road is a few pixels: a line"
    assert len(zone) > 25, "on the zone's map every bend is there"
    # Zoomed in on its middle, only the part in sight (and a little around it) is drawn ...
    cx, cy = on_map(rt, DUROTAR, 0, -4500)
    view(rt, 6, cx / ART_W, cy / ART_H)
    close = cores(road(rt))
    assert 0 < len(close) < len(zone)
    span = [end[1] for line in close for end in ends(line)]
    assert max(span) - min(span) < ART_H / 6 * 1.7
    # ... and on a part of the zone the road never came to, nothing.
    view(rt, 6, 0.95, 0.95)
    assert road(rt) == []
    view(rt, 1, 0.5, 0.5)
    assert len(cores(road(rt))) == len(zone)


def test_the_road_follows_when_the_map_is_dragged(map_rt):
    rt = map_rt
    travel(rt, [(3000, 0)], start=(-1800, -4200))
    show(rt, DUROTAR)
    view(rt, 6, 0.4, 0.92)      # the south end of the road
    south = cores(road(rt))
    assert south
    rt.M.world_map_view(6, 0.4, 0.1)        # dragged to the north end
    rt.frame(0.016)
    rt.advance(0.4)
    north = cores(road(rt))
    assert north and ends(north[-1])[1] == pytest.approx(on_map(rt, DUROTAR, 1200, -4200), abs=2.5)


def test_a_very_long_road_is_drawn_with_a_bounded_number_of_lines(make_rt):
    rt = make_rt(load=False, saved_variables=saved_road(spiral(30000)))
    rt.M.world_map()
    rt.load_addon()
    crowded = 0
    for map_id in (AZEROTH, KALIMDOR, DUROTAR):
        show(rt, map_id)
        lines = road(rt)
        pieces = len(cores(lines))
        assert 0 < pieces <= 3000 and len(lines) <= 4500
        edges = [line for line in lines if line["subLevel"] in (0, 1)]
        if pieces > 1500:
            # A map crowded with road is drawn in plain lines: no dark edge, no glow.
            crowded += 1
            assert edges == [], map_id
        else:
            assert len([line for line in edges if line["subLevel"] == 0]) == pieces, map_id
    assert crowded >= 1, "the test's road is long enough to crowd one of the maps"
    cx, cy = on_map(rt, DUROTAR, 500, -4000)
    for zoom in (2, 4, 8, 16):
        view(rt, zoom, cx / ART_W, cy / ART_H)
        lines = road(rt)
        assert cores(lines) and len(cores(lines)) <= 3000
        # Up to 1500 pieces have a dark edge each; more are drawn as plain lines.
        assert len(lines) <= 4500
    assert len(lua_list(rt.M.descendants(overlay(rt), "Line", None))) <= 4500, "lines are reused, not piled up"


def test_a_road_in_thousands_of_short_stretches_does_not_hang_the_map(make_rt):
    """Every level, mount, flight, death and belt start cuts the road into a
    new stretch: a long journey has thousands, each of which is at least one
    line. They are joined where they are too short to see."""
    segments = []
    x, y = -1500, -5500
    for index in range(4000):
        dx, dy = (7, 3) if index % 2 else (3, 7)
        belt = ",b=1" if index % 3 == 0 else ""
        segments.append(f"{{c=1,m='w',l={1 + index // 80},t={1800000000 + index}{belt},x={x},y={y},n=1,d='2,{dx},{dy}'}}")
        x, y = x + dx, y + dy
    rt = make_rt(load=False, saved_variables=saved_road(*segments))
    rt.M.world_map()
    rt.load_addon()
    for map_id in (AZEROTH, KALIMDOR, DUROTAR):
        show(rt, map_id)
        lines = cores(road(rt))
        assert 0 < len(lines) <= 3000, map_id
    # Walka's own window draws the same road.
    rt.slash("/walka map window")
    rt.advance(0.5)
    window = rt.global_("WalkaRoadMapFrame")
    assert 0 < len(lua_list(rt.M.descendants(window, "Line", True))) <= 2500


def test_the_road_grows_while_the_map_is_open(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    assert not passes_through(road(rt), on_map(rt, DUROTAR, 0, -3400), slack=3)
    travel(rt, [(0, 800)], start=(0, -4200))
    rt.advance(0.5)
    lines = road(rt)
    assert passes_through(cores(lines), on_map(rt, DUROTAR, 0, -3400), slack=8), "the new leg, without reopening the map"
    assert passes_through(tail(lines), on_map(rt, DUROTAR, 0, -3400), slack=1), "right up to where the character stands"


def test_the_road_reaches_the_character(map_rt):
    """Points are stored some twenty yards apart; from the last one to the
    character the road is drawn as it is walked, frame by frame."""
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    for step in range(1, 9):
        rt.set(worldX=0, worldY=-4200 + 1.75 * step, speed=7.0)
        rt.advance(0.25)
        rt.frame(0.016)
        end = tail(road(rt))
        assert len(end) == 1
        # (to where the recorder last saw the character, a quarter of a second ago at most, in whole yards)
        assert ends(end[0])[1] == pytest.approx(on_map(rt, DUROTAR, 0, -4200 + 1.75 * step), abs=0.8)
    # The same colour and width as the road it carries on, on a dark line of its own.
    lines = road(rt)
    assert end[0]["color"] == cores(lines)[-1]["color"] and end[0]["thickness"] == cores(lines)[-1]["thickness"]
    assert len([line for line in lines if line["subLevel"] == 3]) == 1
    # Where recording ends (a loading screen), the stored road ends there too: no tail.
    stop_recording_here(rt)
    lines = road(rt)
    assert tail(lines) == [] and passes_through(cores(lines), on_map(rt, DUROTAR, 0, -4186), slack=0.01)


def test_what_was_walked_while_the_map_was_closed_is_there_when_it_opens(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    rt.run("WorldMapFrame:Hide()")
    travel(rt, [(0, 800)], start=(0, -4200))
    rt.M.world_map_show(DUROTAR)
    rt.frame(0.016)
    rt.frame(0.016)
    lines = road(rt)
    assert passes_through(cores(lines), on_map(rt, DUROTAR, 0, -3400), slack=8)
    assert passes_through(tail(lines), on_map(rt, DUROTAR, 0, -3400), slack=1)


# ---- switching it off, and the button -----------------------------------------------------------


def test_the_road_can_be_hidden_and_shown(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt)
    assert road(rt)
    rt.slash("/walka map off")
    rt.advance(0.3)
    assert road(rt) == [] and rt.walka.Get(rt.walka, "worldMap.road") is False
    assert "no longer drawn on the world map (it is still being recorded)" in rt.printed[-1]
    rt.slash("/walka road off")
    rt.slash("/walka map on")
    rt.slash("/walka map off")
    assert rt.printed[-1].endswith("your road is no longer drawn on the world map.")
    rt.slash("/walka road on")
    rt.slash("/walka map on")
    rt.advance(0.3)
    assert road(rt)
    # /walka map switches it back on as well.
    rt.slash("/walka map off")
    rt.slash("/walka map")
    rt.advance(0.3)
    assert road(rt) and rt.walka.Get(rt.walka, "worldMap.road") is True
    rt.clear_printed()
    rt.slash("/walka map sideways")
    assert "use /walka map" in rt.printed[-1]


def test_the_choice_is_kept_over_a_reload(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka map off")
    rt.reload()
    show(rt)
    assert road(rt) == []
    rt.slash("/walka map on")
    rt.advance(0.3)
    assert road(rt), "and the road itself was kept"


def test_the_button_on_the_map(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka start")
    travel(rt, [(600, 0)], start=(0, -4200))
    rt.slash("/walka stop")
    show(rt)
    button = road_button(rt)
    assert ui.is_visible(rt, button) and "Walka road" in ui.own_texts(rt, button)
    # In the top-left corner of the map view, above everything on the map.
    left, bottom, width, height = ui.rect(rt, button)
    cleft, cbottom, cwidth, cheight = ui.rect(rt, world_map(rt).ScrollContainer)
    assert left - cleft == pytest.approx(8) and (cbottom + cheight) - (bottom + height) == pytest.approx(8)
    container = world_map(rt).ScrollContainer
    container_level = container.GetFrameLevel(container)
    assert rt.inspect(button)["level"] == container_level + 8700, "over the map's icons, under a view laid over the map"
    tip = ui.tooltip(rt, button)
    assert tip[0] == "Walka: your road"
    text = " ".join(tip)
    assert "of road recorded" in text and "walked on the treadmill" in text
    assert "1-9" in text and "50-60" in text and "Click to hide the road." in text
    ui.click(rt, button)
    rt.advance(0.3)
    assert road(rt) == [] and ui.is_visible(rt, button), "the button stays, to switch it back on"
    assert "Click to show the road." in " ".join(ui.tooltip(rt, button))
    ui.click(rt, button)
    rt.advance(0.3)
    assert road(rt)


def test_the_button_is_only_on_maps_that_can_show_a_road(map_rt):
    """A dungeon's map has no place for the road: the corner is left to
    what the game puts there (the list of a dungeon's floors)."""
    rt = map_rt
    rt.run("local maps = ...; maps[9001] = { name = 'Ragefire Chasm', type = 'Dungeon', parent = 1411 }", rt.M.maps)
    show(rt, DUROTAR)
    button = road_button(rt)
    assert ui.is_visible(rt, button)
    show(rt, 9001)
    assert not ui.is_visible(rt, button) and road(rt) == []
    show(rt, KALIMDOR)
    assert ui.is_visible(rt, button)
    # ... also while the road itself is switched off.
    rt.slash("/walka map off")
    show(rt, 9001)
    assert not ui.is_visible(rt, button)
    show(rt, DUROTAR)
    assert ui.is_visible(rt, button)


def test_the_options_page_has_the_road_settings(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    show(rt)
    panel = ui.options(rt)
    on_map_box = ui.checkbox(rt, panel, "Draw it on the world map")
    button_box = ui.checkbox(rt, panel, "Show the Walka road button on the world map")
    record_box = ui.checkbox(rt, panel, "Record my character's road")
    assert ui.checked(rt, on_map_box) and ui.checked(rt, button_box) and ui.checked(rt, record_box)
    ui.click(rt, button_box)
    assert not ui.is_visible(rt, road_button(rt)) and road(rt), "the button goes, the road stays"
    ui.click(rt, on_map_box)
    rt.advance(0.3)
    assert road(rt) == []
    ui.click(rt, record_box)
    assert rt.ns.RoadRecorder.IsEnabled() is False
    # Changed elsewhere, the page follows.
    rt.slash("/walka map on")
    rt.slash("/walka road on")
    assert ui.checked(rt, on_map_box) and rt.ns.RoadRecorder.IsEnabled() is True


def test_walkas_own_window_is_still_there(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka map window")
    window = rt.global_("WalkaRoadMapFrame")
    assert ui.is_visible(rt, window) and not ui.is_visible(rt, world_map(rt))
    rt.slash("/walka map window")
    assert not ui.is_visible(rt, window)


# ---- combat -------------------------------------------------------------------------------------


def test_in_combat_walka_does_not_open_the_map(map_rt):
    """Opening the map from addon code makes the map's own code run as the
    addon's, and the game then blocks parts of it in combat. Walka shows its
    own window instead, which may be opened at any time."""
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.enter_combat()
    rt.clear_printed()
    rt.slash("/walka map")
    assert not ui.is_visible(rt, world_map(rt))
    assert rt.M.world_map_blocked.toggles == 0, "not even tried (the mock counts the calls, caught or not)"
    assert "in combat Walka cannot open the world map for you" in rt.printed[-1]
    window = rt.global_("WalkaRoadMapFrame")
    assert ui.is_visible(rt, window), "the road is to be seen all the same"
    # The HUD's menu does the same.
    rt.slash("/walka map window")
    assert not ui.is_visible(rt, window)
    ui.context_menu(rt, ui.hud(rt))
    ui.menu_click(rt, "Road map")
    assert ui.is_visible(rt, window) and rt.M.world_map_blocked.toggles == 0
    # Opened by the player, the map has the road on it, combat or not - and nothing is said.
    show(rt)
    assert road(rt)
    rt.clear_printed()
    rt.slash("/walka map")
    assert rt.printed == [] and rt.M.world_map_blocked.toggles == 0
    rt.leave_combat()
    rt.run("WorldMapFrame:Hide()")
    rt.slash("/walka map")
    assert ui.is_visible(rt, world_map(rt))


def test_a_map_that_arrives_during_combat_is_left_alone_until_it_ends(make_rt):
    rt = make_rt()
    travel(rt, [(600, 0)])
    rt.enter_combat()
    rt.M.world_map()
    rt.fire_event("ADDON_LOADED", "Blizzard_WorldMap")
    assert overlay(rt) is None, "no frames made on the map in combat"
    rt.clear_printed()
    rt.slash("/walka map")
    assert "in combat Walka cannot open the world map for you" in rt.printed[-1]
    assert ui.is_visible(rt, rt.global_("WalkaRoadMapFrame")) and rt.M.world_map_blocked.toggles == 0
    rt.leave_combat()
    rt.fire_event("PLAYER_REGEN_ENABLED")
    show(rt)
    assert road(rt)


def test_a_world_map_the_game_makes_only_when_it_is_first_opened(make_rt):
    """Out of combat Walka opens the map, which makes it; in combat it does
    not touch it."""
    rt = make_rt(load=False)
    rt.M.world_map_on_demand()
    rt.load_addon()
    travel(rt, [(600, 0)])
    assert rt.global_("WorldMapFrame") is None
    rt.enter_combat()
    rt.slash("/walka map")
    assert rt.global_("WorldMapFrame") is None and rt.M.world_map_blocked.toggles == 0
    assert ui.is_visible(rt, rt.global_("WalkaRoadMapFrame"))
    rt.slash("/walka map window")
    rt.leave_combat()
    rt.slash("/walka map")
    rt.advance(0.3)
    assert ui.is_visible(rt, world_map(rt)) and road(rt)
    assert not ui.is_visible(rt, rt.global_("WalkaRoadMapFrame"))


# ---- clients and maps that are not as expected --------------------------------------------------


def test_without_a_world_map_the_command_opens_walkas_own_window(ui_rt):
    """The client snapshot has no world map: nothing breaks, and the road is
    still to be seen."""
    rt = ui_rt
    travel(rt, [(600, 0)])
    rt.slash("/walka map")
    window = rt.global_("WalkaRoadMapFrame")
    assert ui.is_visible(rt, window) and overlay(rt) is None
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("world map: Walka is not on it - this client has not made its world map yet" in line for line in rt.printed)


def test_a_world_map_made_when_it_is_first_opened(make_rt):
    """The game may load its world map only when asked for it."""
    rt = make_rt()
    travel(rt, [(600, 0)])
    rt.M.world_map()
    rt.fire_event("ADDON_LOADED", "Blizzard_WorldMap")
    assert overlay(rt) is not None
    rt.slash("/walka map")
    rt.advance(0.3)
    assert ui.is_visible(rt, world_map(rt)) and road(rt)


def test_a_world_map_of_another_kind_is_left_alone(make_rt):
    rt = make_rt(load=False)
    rt.run("WorldMapFrame = CreateFrame('Frame', nil, UIParent)")
    rt.load_addon()
    travel(rt, [(600, 0)])
    rt.slash("/walka map")
    assert ui.is_visible(rt, rt.global_("WalkaRoadMapFrame")) and overlay(rt) is None
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("not the kind Walka knows how to draw on" in line for line in rt.printed)


def test_a_map_without_the_layer_manager_still_gets_the_road(make_rt):
    rt = make_rt(load=False)
    rt.run("local M = ...; M.world_map({ manager = false })", rt.M)
    rt.load_addon()
    travel(rt, [(600, 0)])
    show(rt)
    assert road(rt) and rt.inspect(overlay(rt))["level"] == 2001


def test_a_game_that_says_nothing_about_its_maps(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.set(mapsBroken=True)
    show(rt)
    assert road(rt) == []
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("continents on this map: none" in line for line in rt.printed)
    # When the game does answer, the road comes without reopening the map.
    rt.set(mapsBroken=False)
    rt.advance(2.5)
    assert road(rt)


def test_hidden_positions_do_not_break_the_map(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.enter_combat()
    rt.set(positionSecret=True)
    show(rt)
    rt.advance(3)
    assert road(rt)
    assert rt.find_secrets() == []


def test_mapcheck_says_what_is_on_the_world_map(map_rt):
    rt = map_rt
    travel(rt, [(600, 0)])
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    text = "\n".join(rt.printed)
    assert "world map: road on, closed; frame level 2001." in text
    assert "OpenMap: not installed (the road is drawn on Blizzard's map)." in text
    stop_recording_here(rt)
    show(rt)
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    text = "\n".join(rt.printed)
    assert "world map: road on, showing map 1411, Blizzard's map; frame level 2001." in text
    assert "drawn: 1 stretches of line (2 lines), continents on this map: 1; 0 left out, 0 not placed." in text


# ---- OpenMap ------------------------------------------------------------------------------------

# A stand-in for OpenMap's interface for other addons (OpenMapAPI, version
# 4: docs/api.md in OpenMap), over the mock's world. As on the real one,
# CanvasFromWorldPos answers in the units of the map underneath the view
# (WorldMapFrame:GetMapID()): 0..1 across that map and beyond it, since
# the stitched view shows the country around.
OPEN_MAP = """
local maps = ...
local api = { major = 1, version = 4 }
local state = { stitched = true, calls = 0, toggles = {}, top = 2081, raise = false, regions = false }
api.state = state

local function onWorld(mapId, u, v)
    local minX, maxX, minY, maxY = C_Map.GetMapRectOnMap(mapId, 947)
    return minX + u * (maxX - minX), minY + v * (maxY - minY)
end

function api.IsStitched()
    return state.stitched
end

function api.CanvasFromWorldPos(continent, x, y)
    state.calls = state.calls + 1
    if state.raise then
        error("OpenMap is having a bad day")
    end
    if not state.stitched or type(continent) ~= "number" or type(x) ~= "number" or type(y) ~= "number" then
        return
    end
    local mapId = continent == 1 and 1414 or continent == 0 and 1415 or nil
    if not mapId then
        return
    end
    local map = maps[mapId]
    local u, v = onWorld(mapId, (y - map.left) / (map.right - map.left), (x - map.top) / (map.bottom - map.top))
    if state.regions and x > 0 then
        u = u + 0.01      -- another region of the world, placed by a rule of its own
    end
    local minX, maxX, minY, maxY = C_Map.GetMapRectOnMap(WorldMapFrame:GetMapID(), 947)
    return (u - minX) / (maxX - minX), (v - minY) / (maxY - minY)
end

function api.ArtTopLevel()
    return state.top
end

function api.RegisterMenuToggle(owner, spec)
    if type(owner) ~= "string" or type(spec) ~= "table" or type(spec.id) ~= "string" or type(spec.label) ~= "string"
        or type(spec.get) ~= "function" or type(spec.set) ~= "function" then
        error("OpenMapAPI.RegisterMenuToggle(owner, { id, label, get, set [, icon, tab, section] })", 2)
    end
    state.toggles[#state.toggles + 1] = { owner = owner, spec = spec }
end

return api
"""


def open_map_api(rt, **fields):
    api = rt.run(OPEN_MAP, rt.M.maps)
    for key, value in fields.items():
        api[key] = value
    rt.M.set_global("OpenMapAPI", api)
    return api


@pytest.fixture
def om_rt(make_rt):
    """A client with a world map and OpenMap (which loads before Walka)."""
    rt = make_rt(load=False)
    rt.M.world_map()
    rt.om = open_map_api(rt)
    rt.load_addon()
    return rt


def on_stitched(rt, host: int, continent_map: int, x: float, y: float) -> tuple[float, float]:
    """World yards -> canvas units on OpenMap's map with `host` underneath."""
    wx, wy = on_world(rt, continent_map, x, y)
    min_x, max_x, min_y, max_y = rt.run("return C_Map.GetMapRectOnMap(..., 947)", host)
    return (wx / ART_W - min_x) / (max_x - min_x) * ART_W, (wy / ART_H - min_y) / (max_y - min_y) * ART_H


def test_on_openmaps_map_the_road_is_where_openmap_says(om_rt):
    rt = om_rt
    start = (-600, -4200)
    end = travel(rt, [(1200, 0)], start=start)
    show(rt, DUROTAR)
    lines = cores(road(rt))
    assert len(lines) == 1
    assert ends(lines[0])[0] == pytest.approx(on_stitched(rt, DUROTAR, KALIMDOR, *start), abs=2.5)
    assert ends(lines[0])[1] == pytest.approx(on_stitched(rt, DUROTAR, KALIMDOR, *end), abs=2.5)
    # (In the mock the two agree with Blizzard's placement on a zone map; what
    # tells them apart is the country around the zone, below.)
    assert rt.inspect(overlay(rt))["level"] == 2082, "one above the top of OpenMap's map art"
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    text = "\n".join(rt.printed)
    assert "showing map 1411, OpenMap's stitched map; frame level 2082." in text
    assert "OpenMap: found (interface version 4), the road has a switch in its menu." in text


def test_openmap_shows_the_country_around_and_the_road_goes_on_into_it(om_rt):
    """With Durotar underneath, OpenMap's view can be moved far past the
    zone's edge: the road north of it is off Blizzard's map, not OpenMap's."""
    rt = om_rt
    travel(rt, [(3000, 0)], start=(600, -4200))        # 600 .. 3600 north; Durotar ends at 2000
    show(rt, DUROTAR)
    far = on_stitched(rt, DUROTAR, KALIMDOR, 3600, -4200)
    assert far[1] < -200, "well above the top of the zone's own map"
    view(rt, 1, 0.4, -0.6)      # the view moved north, off the zone's own map altogether
    lines = cores(road(rt))
    assert len(lines) == 1 and ends(lines[0])[1] == pytest.approx(far, abs=2.5)


def test_blizzards_map_ends_at_its_edges(om_rt):
    """A stretch of road wholly north of the zone, near its top edge: on
    OpenMap's map it is in the country around and is drawn; on Blizzard's
    map of the zone there is nothing beyond the edge to draw it on."""
    rt = om_rt
    travel(rt, [(2400, 0)], start=(-600, -4200))                # in the zone, up to 200 yards from its top edge
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    travel(rt, [(300, 0)], start=(2150, -4200))                 # 150 to 450 yards north of its edge
    stop_recording_here(rt)
    rt.set(worldX=0, worldY=-4200)
    show(rt, DUROTAR)
    view(rt, 3, 0.4, 0.12)      # zoomed in at the top edge: the view ends at the edge, its margin goes past
    beyond = on_stitched(rt, DUROTAR, KALIMDOR, 2450, -4200)
    assert beyond[1] < 0
    assert passes_through(cores(road(rt)), beyond, slack=2.5)
    rt.om.state.stitched = False
    rt.advance(0.5)
    lines = cores(road(rt))
    assert lines and all(end[1] >= -0.01 for line in lines for end in ends(line)), "only what is on the map"


def test_on_openmap_the_road_reaches_the_character_from_the_first_step(om_rt):
    """A new character: no point of the road is stored yet (that takes
    twenty yards), but the line from where it started is there."""
    rt = om_rt
    show(rt, DUROTAR)
    for step in range(1, 4):
        rt.set(worldX=-600 + 5 * step, worldY=-4200, speed=7.0)
        rt.advance(0.25)
    rt.advance(0.6)
    end = tail(road(rt))
    assert len(end) == 1 and cores(road(rt)) == []
    assert ends(end[0])[0] == pytest.approx(on_stitched(rt, DUROTAR, KALIMDOR, -600, -4200), abs=0.5)
    assert ends(end[0])[1] == pytest.approx(on_stitched(rt, DUROTAR, KALIMDOR, -585, -4200), abs=2)


def test_openmap_is_asked_about_a_few_places_not_about_every_point(make_rt):
    rt = make_rt(load=False, saved_variables=saved_road(spiral(6000)))
    rt.M.world_map()
    om = open_map_api(rt)
    rt.load_addon()
    show(rt, KALIMDOR)
    assert len(cores(road(rt))) > 20
    assert om.state.calls < 40
    before = om.state.calls
    view(rt, 3, 0.5, 0.5)
    assert om.state.calls == before, "zooming and panning ask nothing new: the map underneath is the same"


def test_when_the_map_underneath_changes_the_road_is_drawn_for_it_at_once(om_rt):
    """OpenMap swaps the map underneath its view as the view moves; canvas
    positions are in that map's units."""
    rt = om_rt
    travel(rt, [(1200, 0)], start=(-600, -4200))
    show(rt, DUROTAR)
    assert ends(cores(road(rt))[0])[0] == pytest.approx(on_stitched(rt, DUROTAR, KALIMDOR, -600, -4200), abs=2.5)
    rt.M.world_map_show(KALIMDOR)
    assert rt.inspect(overlay(rt))["alpha"] == 0, "hidden from the moment the map changes ..."
    rt.frame(0.016)
    assert rt.inspect(overlay(rt))["alpha"] == 1, "... until drawn for it, a frame later"
    assert ends(cores(road(rt))[0])[0] == pytest.approx(on_stitched(rt, KALIMDOR, KALIMDOR, -600, -4200), abs=2.5)


def test_with_openmap_installed_blizzards_own_maps_are_placed_as_ever(om_rt):
    """OpenMap leaves some maps to Blizzard (IsStitched() is false)."""
    rt = om_rt
    rt.om.state.stitched = False
    travel(rt, [(1200, 0)], start=(-600, -4200))
    show(rt, KALIMDOR)
    lines = cores(road(rt))
    assert ends(lines[0])[0] == pytest.approx(on_map(rt, KALIMDOR, -600, -4200), abs=2.5)
    assert rt.om.state.calls == 0
    assert rt.inspect(overlay(rt))["level"] == 2082, "still above where OpenMap's art would be"


def test_the_road_has_a_switch_in_openmaps_menu(om_rt):
    rt = om_rt
    toggles = lua_list(rt.om.state.toggles)
    assert len(toggles) == 1
    spec = toggles[0].spec
    assert toggles[0].owner == "Walka" and spec.id == "road" and spec.label == "My road (Walka)"
    assert spec.tab == "show" and spec.section == "Walka" and "Interface\\Icons\\" in spec.icon
    travel(rt, [(600, 0)])
    show(rt)
    assert spec.get() is True and road(rt)
    spec.set(False)         # the switch in OpenMap's menu, clicked
    rt.advance(0.3)
    assert spec.get() is False and road(rt) == []
    assert len(lua_list(rt.om.state.toggles)) == 1, "OpenMap changed it itself: it is not told again"
    # Changed from Walka's side, OpenMap is told (registering again is how).
    rt.slash("/walka map on")
    rt.advance(0.3)
    again = lua_list(rt.om.state.toggles)
    assert len(again) == 2 and again[1].spec.get() is True and road(rt)


def test_openmap_loaded_after_walka_is_found(make_rt):
    rt = make_rt(load=False)
    rt.M.world_map()
    rt.load_addon()
    travel(rt, [(600, 0)])
    om = open_map_api(rt)
    rt.fire_event("ADDON_LOADED", "OpenMap")
    assert len(lua_list(om.state.toggles)) == 1
    show(rt, DUROTAR)
    assert road(rt) and om.state.calls > 0
    assert rt.inspect(overlay(rt))["level"] == 2082


def test_before_openmap_has_built_its_map_the_level_is_blizzards(om_rt):
    rt = om_rt
    rt.om.state.top = None      # ArtTopLevel() answers nil until the stitched map is built
    rt.om.state.stitched = False
    travel(rt, [(600, 0)])
    show(rt)
    assert road(rt) and rt.inspect(overlay(rt))["level"] == 2001
    rt.om.state.top = 2081
    rt.om.state.stitched = True
    rt.advance(0.3)
    assert rt.inspect(overlay(rt))["level"] == 2082


def test_frame_levels_are_left_alone_in_combat(om_rt):
    rt = om_rt
    rt.om.state.top = None
    rt.om.state.stitched = False
    travel(rt, [(600, 0)])
    show(rt)
    rt.enter_combat()
    rt.om.state.top = 2081
    rt.om.state.stitched = True
    rt.advance(0.5)
    assert road(rt) and rt.inspect(overlay(rt))["level"] == 2001
    rt.leave_combat()
    rt.fire_event("PLAYER_REGEN_ENABLED")
    rt.advance(0.1)
    assert rt.inspect(overlay(rt))["level"] == 2082


def test_an_error_inside_openmap_costs_the_road_not_the_game(om_rt):
    rt = om_rt
    travel(rt, [(600, 0)])
    rt.om.state["raise"] = True
    show(rt, DUROTAR)
    assert road(rt) == []
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("OpenMapAPI.CanvasFromWorldPos raised" in line and "bad day" in line for line in rt.printed)
    rt.om.state["raise"] = False
    rt.advance(2.5)
    assert road(rt), "and it comes back by itself"


def test_an_openmap_too_old_to_place_things(make_rt):
    rt = make_rt(load=False)
    rt.M.world_map()
    open_map_api(rt, version=1)
    rt.load_addon()
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    assert road(rt) == []
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("too old to tell other addons where things are" in line for line in rt.printed)


def test_an_openmap_interface_walka_does_not_know_is_not_used(make_rt):
    rt = make_rt(load=False)
    rt.M.world_map()
    om = open_map_api(rt, major=2)
    rt.load_addon()
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    assert road(rt), "placed as on Blizzard's map"
    assert om.state.calls == 0 and len(lua_list(om.state.toggles)) == 0
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("not the one Walka knows (major version 2)" in line for line in rt.printed)


def test_a_stitched_world_that_is_not_one_straight_picture(om_rt):
    """Should OpenMap place parts of a continent by different rules, each
    stretch is placed by where OpenMap puts its own ends; a stretch that
    runs from one part into another cannot be, and is left out and counted."""
    rt = om_rt
    rt.om.state.regions = True                               # north of x = 0 is shifted
    travel(rt, [(400, 0)], start=(-900, -4200))              # wholly south
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    travel(rt, [(400, 0)], start=(300, -4200))               # wholly north
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    travel(rt, [(400, 0)], start=(-200, -4500))              # across the line
    show(rt, KALIMDOR)
    lines = cores(road(rt))
    assert len(lines) == 2
    south = on_stitched(rt, KALIMDOR, KALIMDOR, -900, -4200)
    north = on_stitched(rt, KALIMDOR, KALIMDOR, 300, -4200)
    assert ends(lines[0])[0] == pytest.approx(south, abs=2.5)
    shift = 0.01 / 0.4 * ART_W      # (0.01 of the world across; Kalimdor's map is 0.4 of it)
    assert ends(lines[1])[0] == pytest.approx((north[0] + shift, north[1]), abs=2.5)
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("1 not placed" in line for line in rt.printed)
    assert "1 stretches could not be placed on this map." in " ".join(ui.tooltip(rt, road_button(rt)))
    # The count is of this drawing, however long the map stays open and the character walks on.
    travel(rt, [(0, 300)], start=(200, -4500))
    rt.advance(1.0)
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("1 not placed" in line for line in rt.printed)
    # The road's end is drawn there too, placed by where OpenMap puts its start.
    end = tail(road(rt))
    assert len(end) == 1


def test_a_view_that_hides_the_maps_own_picture_is_said_to(om_rt):
    """OpenMap's view for controllers fades the map's canvas out and lays
    its own over it: the road goes with the canvas, and Walka says so."""
    rt = om_rt
    rt.om.state.stitched = False
    travel(rt, [(600, 0)])
    show(rt, DUROTAR)
    assert "hides the map's own picture" not in " ".join(ui.tooltip(rt, road_button(rt)))
    rt.run("WorldMapFrame:GetCanvas():SetAlpha(0)")
    rt.advance(0.3)
    assert "This view of the map hides the map's own picture, and the road with it." in ui.tooltip(rt, road_button(rt))
    rt.clear_printed()
    rt.slash("/walka mapcheck")
    assert any("the road cannot be seen on it" in line for line in rt.printed)
