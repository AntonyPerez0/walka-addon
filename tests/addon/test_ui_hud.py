"""The HUD (Walka/UI/HUD.lua): what it shows in every session state, what
its buttons do, where it sits and how the HUD settings are applied.

Controls are found the way a player finds them (see ui_driver.py)."""

from __future__ import annotations

import re

import pytest

from . import ui_driver as ui
from .harness import lua_list, lua_plain
from .test_ui import COMPANION, NOW, history_saved

KMH_PER_MPH = 1.609344
STATES = {"Off", "Starting", "Waiting", "Walking", "Paused", "Paused (AFK)"}
HUD_WIDTH = 224
BAR_WIDTH = HUD_WIDTH - 18


@pytest.fixture
def rt(ui_rt, make_rt):
    """A client at a known time of day, so that "today" is unambiguous."""
    return make_rt(state={"clock": NOW})


@pytest.fixture
def full_rt(ui_rt, make_rt):
    """With a long history and companion data."""
    return make_rt(
        files={"CompanionData.lua": COMPANION},
        saved_variables={"WalkaDB": history_saved(60)},
        state={"clock": NOW},
    )


def redraw(rt):
    """Let the windows catch up with the core (they redraw on its UI tick)."""
    rt.walka.Fire(rt.walka, "TICK")
    rt.check_errors()


def view(rt) -> dict:
    """What the HUD says, by what each piece of text is."""
    shown = ui.texts(rt, ui.hud(rt))
    out: dict = {"all": shown, "estimate_mark": "~" in shown}
    numbers = [text for text in shown if re.fullmatch(r"\d+\.\d", text)]
    out["speed"] = numbers[0] if numbers else None
    out["heading_for"] = numbers[1] if len(numbers) > 1 else None
    for text in shown:
        if text in STATES:
            out["state"] = text
        elif text in ("Today", "This session"):
            out["caption"] = text
        elif text in ("mph", "km/h"):
            out["unit"] = text
        elif text.endswith(" steps"):
            out["steps"] = text
        elif text.endswith(" kcal"):
            out["kcal"] = text
        elif text.endswith((" mi", " km")):
            out["distance"] = text
        elif re.fullmatch(r"\d+:\d\d(:\d\d)?", text):
            out["time"] = text
    return out


def session_texts(rt) -> dict:
    """The session's numbers, formatted as the HUD should show them."""
    walka = rt.walka
    session = walka.GetSession(walka)
    return {
        "steps": "~" + walka.FormatNumber(walka, session.steps) + " steps",
        "kcal": "~" + walka.FormatNumber(walka, session.kcal) + " kcal",
        "distance": "~" + walka.FormatDistance(walka, session.distanceM),
        "time": walka.FormatDuration(walka, session.seconds),
    }


def goal_bar(rt) -> tuple[dict, dict]:
    """The daily-goal bar: (its track, its fill)."""
    frame = ui.hud(rt)
    scale = rt.inspect(frame)["scale"]
    bars = [
        rt.inspect(region) for region in lua_list(rt.M.regions(frame))
        if rt.inspect(region)["type"] == "Texture" and rt.inspect(region)["height"] == pytest.approx(5 * scale)
    ]
    assert len(bars) == 2, "the HUD should have one goal bar: a track and a fill"
    track, fill = sorted(bars, key=lambda bar: bar["serial"])
    return track, fill


# ---- what it shows ------------------------------------------------------------------------


def test_idle_hud_shows_todays_totals(rt):
    shown = view(rt)
    assert shown["state"] == "Off" and shown["caption"] == "Today"
    assert shown["speed"] == "0.0" and shown["unit"] == "mph"
    assert (shown["steps"], shown["kcal"], shown["distance"], shown["time"]) == ("0 steps", "0 kcal", "0.00 mi", "0:00")
    assert not shown["estimate_mark"], "no belt estimate to mark without a session"
    assert shown["heading_for"] is None


def test_idle_hud_adds_up_todays_walks_from_both_sources(full_rt):
    rt = full_rt
    today = lua_plain(rt.walka.GetTotals(rt.walka))["today"]
    shown = view(rt)
    assert today["steps"] == 2710 + 2001, "the fixture: one measured and one estimated walk today"
    # One of today's walks is an addon estimate, so the sums are marked "~".
    assert shown["steps"] == "~4,711 steps"
    assert shown["kcal"] == "~" + rt.walka.FormatNumber(rt.walka, today["kcal"]) + " kcal"
    assert shown["distance"] == "~" + rt.walka.FormatDistance(rt.walka, today["distanceM"])
    assert shown["time"] == rt.walka.FormatDuration(rt.walka, today["seconds"])


def test_measured_only_totals_are_not_marked_as_estimates(ui_rt, make_rt):
    rt = make_rt(files={"CompanionData.lua": COMPANION}, state={"clock": NOW})
    assert view(rt)["steps"] == "2,710 steps"


def test_a_session_from_start_to_stop(rt):
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    # Straight away - before any tick - the HUD knows the belt is about to
    # start; it must not claim to be "waiting" for the character.
    started = view(rt)
    assert started["state"] == "Starting" and started["caption"] == "This session"
    assert started["estimate_mark"] and started["speed"] == "0.0"
    assert started["heading_for"] == "1.0", "the pace the belt will start at is shown as the target"
    assert (started["steps"], started["time"]) == ("~0 steps", "0:00")

    rt.tick(20)
    assert view(rt)["state"] == "Starting", "still in the treadmill's 3-second countdown"

    rt.tick(40)                 # 6 s in: the belt is moving and speeding up
    moving = view(rt)
    assert moving["state"] == "Walking"
    assert 0.6 <= float(moving["speed"]) < 2.0
    assert moving["heading_for"] is not None and float(moving["heading_for"]) > float(moving["speed"])

    rt.tick(600)                # a minute later it has settled at the running pace
    redraw(rt)
    settled = view(rt)
    assert settled["state"] == "Walking" and settled["speed"] == "2.0"
    assert settled["heading_for"] is None, "no target hint once the belt is where it should be"
    assert {key: settled[key] for key in ("steps", "kcal", "distance", "time")} == session_texts(rt)
    assert settled["time"] == "1:03"

    # It keeps counting on the UI tick.
    before = settled
    rt.tick(100)
    redraw(rt)
    after = view(rt)
    assert after["time"] == "1:13"
    assert int(after["steps"][1:].split()[0]) > int(before["steps"][1:].split()[0])
    assert after == {**after, **session_texts(rt)}

    rt.slash("/walka stop")
    stopped = view(rt)
    assert stopped["state"] == "Off" and stopped["caption"] == "Today" and stopped["speed"] == "0.0"
    record = lua_list(rt.db.sessions)[0]
    assert stopped["steps"] == "~" + walka.FormatNumber(walka, record.steps) + " steps", "today's total is the walk just finished"
    assert stopped["time"] == walka.FormatDuration(walka, record.seconds)


def test_hud_redraws_on_the_ui_tick_not_only_on_events(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    seen = set()
    for _ in range(40):
        rt.tick(1)
        seen.add(view(rt)["steps"])
    assert len(seen) >= 4, "four seconds of walking must change the step count on the HUD several times"


def test_paused_by_hand(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka pause")
    paused = view(rt)
    assert paused["state"] == "Paused" and paused["caption"] == "This session"
    assert paused["heading_for"] is None
    rt.tick(100)
    redraw(rt)
    later = view(rt)
    assert later["state"] == "Paused" and later["speed"] == "0.0", "the belt estimate coasts to a stop"
    # The belt coasted for a moment after the pause; then nothing moves.
    rt.tick(100)
    redraw(rt)
    assert {key: view(rt)[key] for key in ("steps", "kcal", "distance", "time")} == {
        key: later[key] for key in ("steps", "kcal", "distance", "time")
    }
    rt.slash("/walka resume")
    assert view(rt)["state"] == "Starting"
    rt.tick(60)
    assert view(rt)["state"] == "Walking"


def test_paused_because_afk(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.set(afk=True, speed=0)
    rt.tick(3)
    assert view(rt)["state"] == "Paused (AFK)"
    assert any("AFK" in line for line in ui.tooltip(rt, ui.hud(rt)))
    rt.tick(50)
    assert view(rt)["state"] == "Paused (AFK)"
    rt.set(afk=False, speed=7.0)
    rt.tick(3)
    assert view(rt)["state"] in ("Starting", "Walking")
    rt.tick(60)
    assert view(rt)["state"] == "Walking"


def test_waiting_when_the_belt_stops_for_a_standing_character(rt):
    rt.walka.Set(rt.walka, "speed.standKmh", 0)
    rt.set(speed=0)
    rt.slash("/walka start")
    assert view(rt)["state"] == "Waiting"
    rt.tick(100)
    waiting = view(rt)
    assert waiting["state"] == "Waiting" and waiting["speed"] == "0.0" and waiting["heading_for"] is None
    assert any("standing still" in line for line in ui.tooltip(rt, ui.hud(rt)))
    rt.set(speed=7.0)
    rt.tick(10)
    assert view(rt)["state"] == "Starting"
    rt.tick(50)
    assert view(rt)["state"] == "Walking"
    rt.set(speed=0)
    rt.tick(600)
    assert view(rt)["state"] == "Waiting"


def test_waiting_because_of_the_settings_is_not_blamed_on_standing_still(ui_rt, make_rt):
    # A maximum below what the treadmill can do keeps the belt stopped
    # however fast the character runs.
    rt = make_rt(files={"CompanionData.lua": COMPANION}, state={"clock": NOW})
    rt.walka.Set(rt.walka, "speed.maxKmh", 0.8)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    assert view(rt)["state"] == "Waiting"
    explanation = ui.tooltip(rt, ui.hud(rt))[1]
    assert "standing still" not in explanation and "speed settings" in explanation
    # The same on a flight path ridden at a standing pace of zero.
    rt.walka.Set(rt.walka, "speed.maxKmh", 4.8)
    rt.walka.Set(rt.walka, "speed.standKmh", 0)
    rt.walka.Set(rt.walka, "speed.taxiMode", "stand")
    rt.set(taxi=True, speed=30.0)
    rt.tick(600)
    assert view(rt)["state"] == "Waiting"
    assert "speed settings" in ui.tooltip(rt, ui.hud(rt))[1]
    rt.set(taxi=False, speed=0)
    rt.tick(30)
    assert "standing still" in ui.tooltip(rt, ui.hud(rt))[1]


def test_units(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(700)
    redraw(rt)
    assert (view(rt)["unit"], view(rt)["speed"]) == ("mph", "2.0")
    assert view(rt)["distance"].endswith(" mi")
    rt.slash("/walka units kmh")
    metric = view(rt)       # at once: a settings change redraws the HUD
    assert (metric["unit"], metric["speed"]) == ("km/h", "3.2")
    assert metric["distance"].endswith(" km")
    assert "mph" not in ui.page(rt, ui.hud(rt))


def test_live_today_figure_does_not_count_the_session_twice(full_rt):
    rt = full_rt
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    for _ in range(5):
        rt.tick(137)
        redraw(rt)
        today = lua_plain(walka.GetTotals(walka))["today"]
        goal_line = next(line for line in ui.tooltip(rt, ui.hud(rt)) if line.startswith("Daily goal"))
        assert goal_line.startswith(f"Daily goal: {walka.FormatNumber(walka, today['steps'])} of 6,000 steps")


# ---- buttons ---------------------------------------------------------------------------------


def test_buttons_are_what_their_tooltips_say(rt):
    frame = ui.hud(rt)
    titles = [ui.tooltip(rt, button)[0] for button in rt.descendants(frame, "Button", visible=True)]
    assert sorted(titles) == ["History", "Options", "Start walking", "Stop"]
    for button in rt.descendants(frame, "Button", visible=True):
        box, window = ui.rect(rt, button), ui.rect(rt, frame)
        assert ui.inside(box, window), "a HUD button sticks out of the HUD"
    boxes = [ui.rect(rt, button) for button in rt.descendants(frame, "Button", visible=True)]
    assert not any(ui.overlap(a, b) for index, a in enumerate(boxes) for b in boxes[index + 1:])


def test_play_pause_resume_and_stop_buttons(rt):
    frame = ui.hud(rt)
    walka = rt.walka
    rt.set(speed=7.0)

    stop = ui.button_by_tooltip(rt, frame, "Stop")
    assert rt.inspect(stop)["disabled"], "nothing to stop yet: the button is greyed out"
    assert rt.click(stop) is False
    assert ui.tooltip(rt, stop) == ["Stop", "No session is running."], "a disabled button still explains itself"

    play = ui.button_by_tooltip(rt, frame, "Start walking")
    rt.click(play)
    assert walka.IsActive(walka) and not walka.IsPaused(walka)
    assert view(rt)["state"] == "Starting"
    assert not rt.inspect(stop)["disabled"]
    assert ui.tooltip(rt, play)[0] == "Pause", "the same button now pauses"

    rt.tick(100)
    rt.click(play)
    assert walka.IsPaused(walka) and view(rt)["state"] == "Paused"
    assert ui.tooltip(rt, play)[0] == "Resume"

    rt.click(play)
    assert walka.IsActive(walka) and not walka.IsPaused(walka)
    assert ui.tooltip(rt, play)[0] == "Pause"

    rt.tick(300)
    rt.click(stop)
    assert not walka.IsActive(walka) and view(rt)["state"] == "Off"
    assert len(lua_list(rt.db.sessions)) == 1
    assert rt.inspect(stop)["disabled"] and ui.tooltip(rt, play)[0] == "Start walking"


def test_stop_also_ends_a_paused_session(rt):
    frame = ui.hud(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(400)
    rt.slash("/walka pause")
    rt.click(ui.button_by_tooltip(rt, frame, "Stop"))
    assert not rt.walka.IsActive(rt.walka)
    assert len(lua_list(rt.db.sessions)) == 1


def test_history_button_toggles_the_history_window(rt):
    button = ui.button_by_tooltip(rt, ui.hud(rt), "History")
    assert rt.global_("WalkaHistoryFrame") is None, "the history window is only built when first opened"
    rt.click(button)
    assert ui.is_visible(rt, rt.global_("WalkaHistoryFrame"))
    rt.click(button)
    assert not ui.is_visible(rt, rt.global_("WalkaHistoryFrame"))


def test_options_button_opens_the_settings_window(rt):
    button = ui.button_by_tooltip(rt, ui.hud(rt), "Options")
    rt.click(button)
    assert len(lua_list(rt.log.settings_opened)) == 1
    assert ui.is_visible(rt, ui.options(rt, open_it=False))


def test_options_button_in_combat_says_why_not(rt):
    button = ui.button_by_tooltip(rt, ui.hud(rt), "Options")
    rt.enter_combat()
    rt.clear_printed()
    rt.click(button)
    assert lua_list(rt.log.settings_opened) == [] and lua_list(rt.log.blocked) == []
    assert any("combat" in line for line in rt.printed)


def test_session_buttons_work_in_combat(rt):
    frame = ui.hud(rt)
    rt.enter_combat()
    rt.set(speed=7.0, chatLockdown=True, nameSecret=True)
    play = ui.button_by_tooltip(rt, frame, "Start walking")
    rt.click(play)
    rt.tick(100)
    assert view(rt)["state"] == "Walking"
    assert any("estimating" in line for line in ui.tooltip(rt, frame)), "the tooltip says the speed is an estimate"
    rt.click(play)
    assert view(rt)["state"] == "Paused"
    rt.click(ui.button_by_tooltip(rt, frame, "Stop"))
    assert view(rt)["state"] == "Off"
    assert rt.find_secrets() == []


# ---- tooltip and menu ---------------------------------------------------------------------------


def test_tooltip_explains_the_state_and_the_data(full_rt):
    rt = full_rt
    lines = ui.tooltip(rt, ui.hud(rt))
    assert lines[0] == "Walka"
    assert "No session is running" in lines[1]
    assert "Daily goal: 4,711 of 6,000 steps (79%)." in lines
    assert any(line.startswith("Companion data from Nov 17") for line in lines)
    assert lines[-1] == "Drag to move. Right-click for the menu."
    rt.walka.Set(rt.walka, "hud.locked", True)
    assert ui.tooltip(rt, ui.hud(rt))[-1] == "Locked. Right-click for the menu."
    rt.walka.Set(rt.walka, "dailyStepGoal", 0)
    assert not any("Daily goal" in line for line in ui.tooltip(rt, ui.hud(rt)))
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    lines = ui.tooltip(rt, ui.hud(rt))
    assert "the belt follows your character" in lines[1]
    assert any(line.startswith("Target speed: ") and line.endswith(" mph.") for line in lines)


def test_tooltip_opens_towards_the_middle_of_the_screen(rt):
    frame = ui.hud(rt)
    tooltip = rt.run("return GameTooltip")
    # On the left half it opens to the right; dragged to the right half, to the left.
    watch = rt.lua.eval(
        "function(tooltip, out) local real = tooltip.SetOwner "
        "tooltip.SetOwner = function(self, owner, anchor, ...) out[#out + 1] = anchor return real(self, owner, anchor, ...) end end"
    )
    seen = rt.lua.table()
    watch(tooltip, seen)
    rt.M.hover(frame, True)
    rt.M.hover(frame, False)
    rt.M.drag(frame, 900, 0)
    rt.M.hover(frame, True)
    rt.M.hover(frame, False)
    anchors = lua_list(seen)
    assert anchors == ["ANCHOR_RIGHT", "ANCHOR_LEFT"]


def test_right_click_menu(rt):
    frame = ui.hud(rt)
    walka = rt.walka
    assert ui.context_menu(rt, frame) == [
        "title:Walka", "checkbox:Lock position:false", "button:History", "button:Options", "divider:", "button:Hide",
    ]
    ui.menu_click(rt, "Lock position")
    assert walka.Get(walka, "hud.locked") is True
    assert "checkbox:Lock position:true" in ui.context_menu(rt, frame)
    ui.menu_click(rt, "Lock position")
    assert walka.Get(walka, "hud.locked") is False

    ui.context_menu(rt, frame)
    ui.menu_click(rt, "History")
    assert ui.is_visible(rt, rt.global_("WalkaHistoryFrame"))

    ui.context_menu(rt, frame)
    ui.menu_click(rt, "Options")
    assert len(lua_list(rt.log.settings_opened)) == 1

    ui.context_menu(rt, frame)
    rt.clear_printed()
    ui.menu_click(rt, "Hide")
    assert not ui.is_visible(rt, frame) and walka.Get(walka, "hud.shown") is False
    assert any("/walka" in line for line in rt.printed), "hiding the HUD says how to get it back"


def test_left_click_does_not_open_the_menu(rt):
    before = len(lua_list(rt.log.menus))
    rt.M.fire_script(ui.hud(rt), "OnMouseUp", "LeftButton")
    rt.check_errors()
    assert len(lua_list(rt.log.menus)) == before


# ---- position -------------------------------------------------------------------------------------


def saved_point(rt):
    return lua_plain(rt.db.settings.hud.point)


def edges(rt, frame=None):
    """Distances of the HUD from the screen's left, right, top and bottom."""
    left, bottom, width, height = ui.rect(rt, frame or ui.hud(rt))
    screen_width, screen_height = rt.M.screen_size()
    return {"left": left, "right": screen_width - left - width, "top": screen_height - bottom - height, "bottom": bottom}


def test_default_position_and_size(rt):
    assert rt.db.settings.hud.point is None, "nothing is saved until the HUD is moved"
    left, bottom, width, height = ui.rect(rt, ui.hud(rt))
    assert (left, width, height) == (40, HUD_WIDTH, 116)
    assert bottom + height / 2 == pytest.approx(768 / 2 + 80)


def test_dragging_saves_the_position_against_the_nearest_corner(rt):
    frame = ui.hud(rt)
    assert rt.M.drag(frame, 900, -300) is True
    rt.check_errors()
    after = edges(rt)
    assert after["left"] == pytest.approx(940, abs=1) and after["bottom"] == pytest.approx(106, abs=1)
    point = saved_point(rt)
    assert point[:2] == ["BOTTOMRIGHT", "BOTTOMRIGHT"]
    assert point[2] == pytest.approx(-after["right"], abs=1) and point[3] == pytest.approx(after["bottom"], abs=1)
    assert point[2] == int(point[2]) and point[3] == int(point[3]), "whole numbers: nothing fuzzy in the saved variables"

    # Each region of the screen anchors to its own edge or corner.
    expected = {
        (-900, 240): "TOPLEFT", (-450, 240): "TOP", (0, 240): "TOPRIGHT",
        (-900, 0): "LEFT", (-450, 0): "CENTER", (0, 0): "RIGHT",
        (-900, -300): "BOTTOMLEFT", (-450, -300): "BOTTOM", (0, -300): "BOTTOMRIGHT",
    }
    rt.M.drag(frame, 0, 300)        # back to the right edge, mid-height
    for (dx, dy), anchor in expected.items():
        home = ui.rect(rt, frame)
        rt.M.drag(frame, dx, dy)
        rt.check_errors()
        moved = ui.rect(rt, frame)
        assert saved_point(rt)[0] == anchor, (dx, dy)
        assert moved[0] == pytest.approx(home[0] + dx, abs=1) and moved[1] == pytest.approx(home[1] + dy, abs=1)
        rt.M.drag(frame, -dx, -dy)


def test_position_survives_reload_resolution_and_ui_scale_changes(rt):
    rt.M.drag(ui.hud(rt), 900, -300)
    before = edges(rt)
    fresh = rt.reload()
    assert edges(fresh) == pytest.approx(before, abs=1)

    # A narrower window (5:4): still the same distance from the bottom-right corner.
    fresh.M.set_screen(1280, 1024)
    narrow = edges(fresh)
    assert narrow["right"] == pytest.approx(before["right"], abs=1)
    assert narrow["bottom"] == pytest.approx(before["bottom"], abs=1)

    # A smaller UI scale shrinks the HUD and its distance from the corner alike.
    fresh.M.set_ui_scale(0.5)
    small = edges(fresh)
    left, bottom, width, height = ui.rect(fresh, ui.hud(fresh))
    assert (width, height) == pytest.approx((HUD_WIDTH * 0.5, 116 * 0.5))
    assert small["right"] == pytest.approx(before["right"] * 0.5, abs=1)
    assert small["bottom"] == pytest.approx(before["bottom"] * 0.5, abs=1)
    again = fresh.reload()
    again.M.set_ui_scale(0.5)
    assert edges(again) == pytest.approx(small, abs=1)


def test_a_position_from_a_bigger_screen_is_brought_back_on_screen(ui_rt, make_rt):
    saved = "{settings={hud={point={'LEFT','LEFT',3000,-2000}}}}"
    rt = make_rt(saved_variables={"WalkaDB": saved}, state={"clock": NOW})
    screen_width, screen_height = rt.M.screen_size()
    left, bottom, width, height = ui.rect(rt, ui.hud(rt))
    assert 0 <= left and left + width <= screen_width + 0.01
    assert 0 <= bottom and bottom + height <= screen_height + 0.01


def test_locked_hud_cannot_be_dragged(rt):
    frame = ui.hud(rt)
    rt.walka.Set(rt.walka, "hud.locked", True)
    before = ui.rect(rt, frame)
    assert rt.M.drag(frame, 300, 100) is False
    assert ui.rect(rt, frame) == before and rt.db.settings.hud.point is None
    rt.walka.Set(rt.walka, "hud.locked", False)
    assert rt.M.drag(frame, 300, 100) is True
    assert rt.db.settings.hud.point is not None


def test_hiding_the_hud_in_mid_drag_ends_the_drag(rt):
    frame = ui.hud(rt)
    rt.M.fire_script(frame, "OnDragStart", "LeftButton")
    assert frame.IsDragging(frame)
    rt.slash("/walka hide")
    assert not frame.IsDragging(frame), "a hidden frame must not stay glued to the cursor"
    rt.slash("/walka show")
    rt.tick(5)


@pytest.mark.parametrize(
    "point",
    [
        "'nonsense'", "{}", "{'LEFT'}", "{'LEFT','LEFT','far','up'}", "{'MIDDLE','LEFT',1,2}", "{'LEFT','LEFT',0/0,5}",
        "{'LEFT','LEFT',5,1/0}", "{'LEFT','LEFT',-1/0,5}", "{'LEFT','LEFT',1e300,1e300}", "{1,2,3,4}", "true",
    ],
)
def test_a_damaged_saved_position_falls_back_to_the_default(rt, point):
    rt.run(f"Walka:Set('hud.point', {point})")
    left, bottom, width, height = ui.rect(rt, ui.hud(rt))
    assert (left, bottom + height / 2) == pytest.approx((40, 768 / 2 + 80))
    rt.tick(5)


# ---- settings ---------------------------------------------------------------------------------------


def test_hud_shown_setting(rt):
    frame = ui.hud(rt)
    walka = rt.walka
    walka.Set(walka, "hud.shown", False)
    assert not ui.is_visible(rt, frame) and rt.ns.HUD.IsShown() is False
    fresh = rt.reload()
    assert not ui.is_visible(fresh, ui.hud(fresh))
    fresh.walka.Set(fresh.walka, "hud.shown", True)
    assert ui.is_visible(fresh, ui.hud(fresh))
    assert view(fresh)["state"] == "Off", "a HUD that comes back is drawn at once, not on the next tick"


def test_a_hidden_hud_does_no_work(full_rt):
    rt = full_rt
    merges = ui.merge_counter(rt)
    rt.slash("/walka hide")
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(900)
    rt.slash("/walka stop")
    rt.tick(900)
    assert merges.calls == 0, "nothing reads the history while no window shows it"
    rt.slash("/walka show")
    assert view(rt)["state"] == "Off" and merges.calls == 1


def test_hud_scale_keeps_the_anchor(rt):
    frame = ui.hud(rt)
    walka = rt.walka
    before = ui.rect(rt, frame)
    walka.Set(walka, "hud.scale", 1.5)
    bigger = ui.rect(rt, frame)
    assert (bigger[2], bigger[3]) == pytest.approx((before[2] * 1.5, before[3] * 1.5))
    assert bigger[0] == pytest.approx(before[0]), "anchored by its left edge: that edge stays put"
    assert bigger[1] + bigger[3] / 2 == pytest.approx(before[1] + before[3] / 2)

    # ... also for a HUD the player has placed, here in the bottom-right corner.
    rt.M.drag(frame, 900, -300)
    placed = edges(rt)
    walka.Set(walka, "hud.scale", 0.6)
    shrunk = edges(rt)
    assert (shrunk["right"], shrunk["bottom"]) == pytest.approx((placed["right"], placed["bottom"]), abs=1)
    assert ui.rect(rt, frame)[2] == pytest.approx(HUD_WIDTH * 0.6)
    # Dragging a scaled HUD saves where it really is.
    rt.M.drag(frame, -100, 50)
    moved = edges(rt)
    assert (moved["right"], moved["bottom"]) == pytest.approx((placed["right"] + 100, placed["bottom"] + 50), abs=1)
    walka.Set(walka, "hud.scale", 1.0)
    assert (edges(rt)["right"], edges(rt)["bottom"]) == pytest.approx((moved["right"], moved["bottom"]), abs=1)


def test_hud_scale_and_alpha_are_limited_to_a_usable_range(rt):
    frame = ui.hud(rt)
    walka = rt.walka
    for value, scale, alpha in [(0.05, 0.6, 0.3), (50, 1.6, 1.0), (0.8, 0.8, 0.8), ("text", 1.0, 0.9)]:
        walka.Set(walka, "hud.scale", value)
        walka.Set(walka, "hud.alpha", value)
        info = rt.inspect(frame)
        assert (info["scale"], info["alpha"]) == pytest.approx((scale, alpha)), value


def test_hud_alpha_setting(rt):
    rt.walka.Set(rt.walka, "hud.alpha", 0.45)
    assert rt.inspect(ui.hud(rt))["alpha"] == pytest.approx(0.45)
    fresh = rt.reload()
    assert fresh.inspect(ui.hud(fresh))["alpha"] == pytest.approx(0.45)


def test_daily_goal_bar(full_rt):
    rt = full_rt
    walka = rt.walka
    frame = ui.hud(rt)
    track, fill = goal_bar(rt)
    assert track["visible"] and fill["visible"]
    assert track["width"] == pytest.approx(BAR_WIDTH)
    assert fill["width"] == pytest.approx(BAR_WIDTH * 4711 / 6000)
    assert fill["left"] == pytest.approx(track["left"]) and fill["bottom"] == pytest.approx(track["bottom"])
    below_goal = [fill["color"][index] for index in (1, 2, 3)]

    walka.Set(walka, "dailyStepGoal", 4000)          # goal reached: full bar, another colour
    track, fill = goal_bar(rt)
    assert fill["width"] == pytest.approx(BAR_WIDTH)
    assert [fill["color"][index] for index in (1, 2, 3)] != below_goal

    walka.Set(walka, "dailyStepGoal", 0)             # goal off: no bar, a shorter HUD
    track, fill = goal_bar(rt)
    assert not track["visible"] and not fill["visible"]
    assert ui.rect(rt, frame)[3] == pytest.approx(104)
    walka.Set(walka, "dailyStepGoal", 6000)
    assert ui.rect(rt, frame)[3] == pytest.approx(116)
    assert goal_bar(rt)[1]["width"] == pytest.approx(BAR_WIDTH * 4711 / 6000)


def test_goal_bar_grows_while_walking(rt):
    rt.walka.Set(rt.walka, "dailyStepGoal", 200)
    rt.set(speed=7.0)
    assert not goal_bar(rt)[1]["visible"], "no steps yet: no fill"
    rt.slash("/walka start")
    widths = []
    for _ in range(8):
        rt.tick(100)
        redraw(rt)
        widths.append(goal_bar(rt)[1]["width"])
    assert widths == sorted(widths) and widths[0] < widths[-1]
    assert widths[-1] <= BAR_WIDTH + 0.01
    steps = rt.walka.GetSession(rt.walka).steps
    assert widths[-1] == pytest.approx(BAR_WIDTH * min(1, steps / 200), abs=0.5)


@pytest.mark.parametrize("goal", ["'lots'", "-5", "0/0", "1/0", "{}", "false"])
def test_a_nonsense_goal_switches_the_bar_off_or_fills_it(rt, goal):
    rt.run(f"Walka:Set('dailyStepGoal', {goal})")
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    ui.tooltip(rt, ui.hud(rt))
    track, fill = goal_bar(rt)
    assert 0 <= fill["width"] <= BAR_WIDTH + 0.01


# ---- cost ---------------------------------------------------------------------------------------------


def test_totals_are_cached_between_redraws(full_rt):
    rt = full_rt
    merges = ui.merge_counter(rt)
    totals = ui.Counter(rt, rt.ns.SessionLog, "totals")
    rt.tick(550)                    # 55 s idle, some 180 redraws
    assert merges.calls == 0 and totals.calls == 0
    rt.tick(100)                    # ... past the one-minute refresh (for midnight)
    assert merges.calls == 1 and totals.calls == 1

    merges.reset()
    rt.set(speed=7.0)
    rt.slash("/walka start")
    assert merges.calls == 1, "a session starting changes what 'today' means: one refresh"
    rt.tick(1200)                   # two minutes of walking: one refresh a minute
    assert merges.calls <= 3
    rt.slash("/walka pause")
    rt.slash("/walka resume")
    rt.tick(10)
    assert merges.calls <= 3, "pausing and resuming do not change the totals"

    merges.reset()
    rt.slash("/walka stop")
    assert merges.calls == 1, "stopping fires two events but needs one refresh"

    merges.reset()
    for step in range(40):          # a slider being dragged in the options
        rt.walka.Set(rt.walka, "hud.alpha", 0.5 + step / 100)
    assert merges.calls == 0


def test_hud_creates_no_widgets_after_start_up(full_rt):
    rt = full_rt
    frame = ui.hud(rt)
    rt.set(speed=7.0)
    # Warm up: every state and both glyphs of the play button once.
    rt.slash("/walka start")
    rt.tick(50)
    rt.slash("/walka pause")
    rt.slash("/walka resume")
    ui.tooltip(rt, frame)
    rt.slash("/walka stop")
    count = ui.widget_count(rt)
    for _ in range(3):
        rt.slash("/walka start")
        rt.tick(300)
        rt.set(afk=True)
        rt.tick(20)
        rt.set(afk=False)
        rt.slash("/walka pause")
        rt.tick(20)
        rt.slash("/walka resume")
        rt.walka.Set(rt.walka, "hud.scale", 1.2)
        rt.walka.Set(rt.walka, "dailyStepGoal", 0)
        rt.walka.Set(rt.walka, "dailyStepGoal", 5000)
        rt.M.drag(frame, 30, 10)
        ui.tooltip(rt, frame)
        rt.slash("/walka hide")
        rt.slash("/walka show")
        rt.slash("/walka stop")
    assert ui.widget_count(rt) == count, "the HUD must reuse its frames, textures and font strings"


def test_no_text_is_cut_off_at_the_hud_s_size(full_rt):
    """Worst-case texts still fit (text widths are the mock's estimate)."""
    rt = full_rt
    frame = ui.hud(rt)
    walka = rt.walka
    rt.set(speed=7.0, afk=True)
    walka.Set(walka, "units", "kmh")
    rt.slash("/walka start")
    rt.tick(5)
    session = walka.GetSession(walka)
    session.steps, session.kcal, session.distanceM, session.seconds = 123456, 12345, 99990, 86399
    redraw(rt)
    assert view(rt)["state"] == "Paused (AFK)"
    window = ui.rect(rt, frame)
    for font_string in ui.font_strings(rt, frame, visible=True):
        assert not font_string.IsTruncated(font_string), rt.inspect(font_string)["text"]
        box = ui.rect(rt, font_string)
        if box is not None and rt.inspect(font_string).get("text"):
            assert ui.inside(box, window), f"{rt.inspect(font_string)['text']!r} sticks out of the HUD"
    # Left and right columns of numbers do not run into each other.
    by_text = {ui.plain(rt.inspect(fs)["text"]): ui.rect(rt, fs) for fs in ui.font_strings(rt, frame, visible=True)}
    steps = next(box for text, box in by_text.items() if text.endswith(" steps"))
    kcal = next(box for text, box in by_text.items() if text.endswith(" kcal"))
    assert not ui.overlap(steps, kcal)
