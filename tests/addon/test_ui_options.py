"""The options page (Walka/UI/Options.lua): registered with the game's
Settings window, and every control reads and writes WalkaDB.settings.

Controls are found by the text next to them (ui_driver.py)."""

from __future__ import annotations

import time

import pytest

from . import ui_driver as ui
from .conftest import KMH_PER_MPH
from .harness import AddonError, lua_list, lua_plain
from .test_ui import COMPANION, NOW, SYNCED

SPEED_ROWS = {
    "standing still": "standKmh",
    "walking": "walkKmh",
    "running": "runKmh",
    "mounted": "mountKmh",
    "Never faster than": "maxKmh",
}


@pytest.fixture
def rt(ui_rt, make_rt):
    return make_rt(state={"clock": NOW})


@pytest.fixture
def crt(ui_rt, make_rt):
    """With companion data: its body profile and the treadmill's limits."""
    return make_rt(files={"CompanionData.lua": COMPANION}, state={"clock": NOW})


def setting(rt, path):
    return rt.walka.Get(rt.walka, path)


def speeds_shown(rt, panel) -> dict[str, str]:
    return {key: ui.slider_text(rt, ui.slider(rt, panel, row)) for row, key in SPEED_ROWS.items()}


def default_shown(rt, key: str, units: str = "mph") -> str:
    """A default of the speed mapping ("runKmh", ...) as its slider shows
    it: in the display unit, to one decimal."""
    kmh = rt.ns.SpeedModel.DEFAULTS[key]
    return f"{kmh / KMH_PER_MPH:.1f} mph" if units == "mph" else f"{kmh:.1f} km/h"


def scroll_frame(rt, panel):
    frames = rt.descendants(panel, "ScrollFrame")
    assert len(frames) == 1
    return frames[0]


def scroll_bar(rt, panel):
    """(track, thumb) of the page's scroll bar."""
    tracks = [
        widget for widget in lua_list(rt.M.children(panel))
        if rt.inspect(widget)["type"] == "Frame" and rt.M.script_set(widget, "OnMouseDown")
    ]
    assert len(tracks) == 1, "the options page should have one scroll bar"
    return tracks[0], lua_list(rt.M.children(tracks[0]))[0]


# ---- registration ---------------------------------------------------------------------------


def test_page_is_registered_with_the_settings_window(rt):
    categories = list(rt.M.settings_categories.values())
    assert len(categories) == 1
    category = categories[0]
    assert category.name == "Walka" and category.registered, "listed under Options > AddOns as Walka"
    panel = category.frame
    assert rt.inspect(panel)["name"] == "WalkaOptionsPanel"
    assert not ui.is_visible(rt, panel)
    assert rt.inspect(panel).get("parent") is None, "the Settings window adopts the page when it shows it"
    assert rt.descendants(panel, "Slider") == [], "the page is only built when it is first shown"


def test_page_fills_the_settings_window_s_page_area(rt):
    panel = ui.options(rt)
    box = ui.rect(rt, panel)
    assert (box[2], box[3]) == pytest.approx((665, 601)), "the size of the Settings window's canvas"
    assert rt.descendants(panel, "Slider"), "built on first show"


@pytest.mark.parametrize("command", ["options", "config", "settings", "OPTIONS"])
def test_slash_commands_open_it(rt, command):
    rt.slash("/walka " + command)
    assert len(lua_list(rt.log.settings_opened)) == 1
    assert ui.is_visible(rt, ui.options(rt, open_it=False))


def test_opening_is_refused_politely_in_combat(rt):
    rt.enter_combat()
    rt.clear_printed()
    rt.ns.Options.Open()
    rt.check_errors()
    assert lua_list(rt.log.settings_opened) == []
    assert lua_list(rt.log.blocked) == [], "the addon must not even try: the client blocks it"
    assert len(rt.printed) == 1 and "combat" in rt.printed[0] and "Walka" in rt.printed[0]
    assert not ui.is_visible(rt, ui.options(rt, open_it=False))
    # In-combat lockdown can outlast (or precede) the fight itself.
    rt.leave_combat()
    rt.set(lockdown=True)
    rt.ns.Options.Open()
    assert lua_list(rt.log.settings_opened) == [] and lua_list(rt.log.blocked) == []
    rt.set(lockdown=None)
    rt.ns.Options.Open()
    assert len(lua_list(rt.log.settings_opened)) == 1


def test_page_opened_by_hand_in_combat_works(rt):
    """The player can still open Esc > Options > AddOns > Walka in a fight."""
    rt.enter_combat()
    rt.set(speed=7.0, chatLockdown=True)
    rt.slash("/walka start")
    rt.M.settings_select(1)
    rt.check_errors()
    panel = ui.options(rt, open_it=False)
    assert ui.is_visible(rt, panel)
    ui.slide(rt, ui.slider(rt, panel, "running"), 2.4)
    assert setting(rt, "speed.runKmh") == pytest.approx(2.4 * KMH_PER_MPH, abs=0.001)
    rt.click(ui.checkbox(rt, panel, "Lock its position"))
    assert setting(rt, "hud.locked") is True
    rt.tick(50)
    assert rt.find_secrets() == [] and lua_list(rt.log.blocked) == []


def test_settings_window_closed_reopened_and_switched(rt):
    panel = ui.options(rt)
    assert ui.slider_text(rt, ui.slider(rt, panel, "running")) == default_shown(rt, "runKmh")
    rt.M.settings_close()
    assert not ui.is_visible(rt, panel)
    # Things change while the window is closed ...
    rt.slash("/walka units kmh")
    rt.walka.Set(rt.walka, "speed.runKmh", 3.5)
    rt.tick(10)
    # ... the player opens the window by hand (it shows Blizzard's first page) ...
    rt.M.settings_open()
    rt.check_errors()
    assert not ui.is_visible(rt, panel)
    # ... and picks Walka in the list.
    rt.M.settings_select(1)
    rt.check_errors()
    assert ui.is_visible(rt, panel)
    assert ui.slider_text(rt, ui.slider(rt, panel, "running")) == "3.5 km/h"
    # Another category and back.
    rt.M.settings_select_other()
    assert not ui.is_visible(rt, panel)
    rt.walka.Set(rt.walka, "speed.runKmh", 3.0)
    rt.tick(5)
    rt.ns.Options.Open()
    rt.check_errors()
    assert ui.is_visible(rt, panel) and ui.slider_text(rt, ui.slider(rt, panel, "running")) == "3.0 km/h"
    assert rt.inspect(panel)["width"] == pytest.approx(665)


# ---- units ---------------------------------------------------------------------------------------


def test_units_switch_converts_what_is_shown_not_what_is_stored(rt):
    panel = ui.options(rt)
    stored = lua_plain(rt.db.settings.speed)
    assert stored == lua_plain(rt.ns.SpeedModel.DEFAULTS)
    # The defaults are whole tenths of a mph, stored in km/h to 0.001 - a
    # hair off them (the maximum, 4.023 for 4.02336, a hair under). Shown,
    # they are the whole tenths again.
    in_mph = {
        "standKmh": "0.8 mph", "walkKmh": "1.2 mph", "runKmh": "1.6 mph", "mountKmh": "2.0 mph", "maxKmh": "2.5 mph",
    }
    assert speeds_shown(rt, panel) == in_mph
    for row, key in SPEED_ROWS.items():
        control = ui.slider(rt, panel, row)
        assert control.GetValue(control) == pytest.approx(float(in_mph[key].split()[0])), "the thumb is where the text says"
        assert in_mph[key] == default_shown(rt, key)
    assert ui.box_text(ui.edit_box(rt, panel, "Weight")) == "165" and "lb" in ui.texts(rt, panel)
    assert ui.box_text(ui.edit_box(rt, panel, "Height")) == "5'8\""
    assert "Treadmill range: 0.6 to 4.0 mph (assumed until the companion app has synced)." in ui.texts(rt, panel)

    rt.click(ui.button(rt, panel, "km/h / km"))
    assert setting(rt, "units") == "kmh"
    in_kmh = {
        "standKmh": "1.3 km/h", "walkKmh": "1.9 km/h", "runKmh": "2.6 km/h", "mountKmh": "3.2 km/h", "maxKmh": "4.0 km/h",
    }
    assert speeds_shown(rt, panel) == in_kmh
    assert all(in_kmh[key] == default_shown(rt, key, "kmh") for key in in_kmh)
    assert lua_plain(rt.db.settings.speed) == stored, "speeds are stored in km/h whatever the display unit"
    assert ui.box_text(ui.edit_box(rt, panel, "Weight")) == "75" and "kg" in ui.texts(rt, panel)
    assert ui.box_text(ui.edit_box(rt, panel, "Height")) == "172" and "cm" in ui.texts(rt, panel)
    assert "Treadmill range: 1.0 to 6.0 km/h (assumed until the companion app has synced)." in ui.texts(rt, panel)
    low, high = ui.slider(rt, panel, "running").GetMinMaxValues(ui.slider(rt, panel, "running"))
    assert (low, high) == pytest.approx((1.0, 6.0))
    assert "mph" not in ui.page(rt, panel).replace("mph / miles", "")

    rt.click(ui.button(rt, panel, "km/h / km"))         # already selected: nothing happens
    assert setting(rt, "units") == "kmh"
    rt.click(ui.button(rt, panel, "mph / miles"))
    assert setting(rt, "units") == "mph" and lua_plain(rt.db.settings.speed) == stored
    low, high = ui.slider(rt, panel, "running").GetMinMaxValues(ui.slider(rt, panel, "running"))
    assert (low, high) == pytest.approx((0.6, 4.0))
    assert speeds_shown(rt, panel) == in_mph


# ---- speed mapping ------------------------------------------------------------------------------


@pytest.mark.parametrize("units", ["mph", "kmh"])
def test_speed_sliders_round_trip(rt, units):
    rt.walka.Set(rt.walka, "units", units)
    panel = ui.options(rt)
    factor = KMH_PER_MPH if units == "mph" else 1.0
    suffix = " mph" if units == "mph" else " km/h"
    # Raise the maximum first so that every pace can be set freely below it.
    values = {"Never faster than": 3.7, "mounted": 3.4, "running": 2.9, "walking": 2.1, "standing still": 1.4}
    for row, value in values.items():
        key = SPEED_ROWS[row]
        control = ui.slider(rt, panel, row)
        assert ui.slider_text(rt, control) != f"{value:.1f}{suffix}", "a value the slider is not at already"
        assert ui.slide(rt, control, value)
        assert setting(rt, "speed." + key) == pytest.approx(value * factor, abs=0.0006), row
        assert setting(rt, "speed." + key) == round(setting(rt, "speed." + key), 3), "stored rounded, not as float noise"
        assert ui.slider_text(rt, control) == f"{value:.1f}{suffix}"
        assert rt.walka.modelSettings[key] == setting(rt, "speed." + key), "the speed model sees it at once"
    # A value between two steps snaps to the 0.1 grid.
    control = ui.slider(rt, panel, "running")
    ui.slide(rt, control, 2.5678)
    assert ui.slider_text(rt, control) == f"2.6{suffix}"
    assert setting(rt, "speed.runKmh") == pytest.approx(2.6 * factor, abs=0.0006)
    # The arrows step by 0.1.
    back, forward = ui.steppers(rt, control)
    rt.click(forward)
    assert ui.slider_text(rt, control) == f"2.7{suffix}"
    rt.click(back)
    rt.click(back)
    assert ui.slider_text(rt, control) == f"2.5{suffix}"
    assert setting(rt, "speed.runKmh") == pytest.approx(2.5 * factor, abs=0.0006)
    fresh = rt.reload()
    assert fresh.walka.Get(fresh.walka, "speed.runKmh") == pytest.approx(2.5 * factor, abs=0.0006)


def test_sliders_cover_the_treadmills_range(crt):
    rt = crt
    panel = ui.options(rt)
    # The companion reports 1.0 - 6.0 km/h: 0.62 - 3.73 mph, on the 0.1 grid
    # inwards, so that nothing can be set that the belt cannot do.
    for row in SPEED_ROWS:
        control = ui.slider(rt, panel, row)
        assert control.GetMinMaxValues(control) == pytest.approx((0.7, 3.7)), row
    assert "Treadmill range: 0.6 to 3.7 mph (reported by the companion app)." in ui.texts(rt, panel)
    synced = time.strftime("%b %d, %H:%M", time.localtime(SYNCED))
    assert f"Last synced {synced}." in ui.texts(rt, panel)


def test_the_lowest_maximum_still_lets_the_belt_run(crt):
    """In mph a 1.0 km/h belt minimum is 0.62 mph. A maximum of "0.6 mph"
    would be below it, and the belt would never start."""
    rt = crt
    panel = ui.options(rt)
    cap = ui.slider(rt, panel, "Never faster than")
    low, _ = cap.GetMinMaxValues(cap)
    ui.slide(rt, cap, low - 5)
    assert ui.slider_text(rt, cap) == "0.7 mph"
    assert setting(rt, "speed.maxKmh") >= 1.0
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    belt, target = rt.walka.GetLiveSpeed(rt.walka)
    assert target >= 1.0 and belt >= 1.0, "the belt runs at the slowest speed the treadmill can hold"
    assert "will not start" not in ui.page(rt, panel)


def test_a_maximum_below_the_treadmills_minimum_is_pointed_out(crt):
    rt = crt
    panel = ui.options(rt)
    rt.walka.Set(rt.walka, "speed.maxKmh", 0.8)         # from an older treadmill, say
    warning = "The maximum is below the slowest speed your treadmill can hold (0.6 mph), so the belt will not start."
    assert warning in ui.page(rt, panel)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    assert rt.walka.GetLiveSpeed(rt.walka) == (0, 0), "(what the warning says)"
    ui.slide(rt, ui.slider(rt, panel, "Never faster than"), 2.0)
    assert "will not start" not in ui.page(rt, panel)
    rt.tick(100)
    assert rt.walka.GetLiveSpeed(rt.walka)[1] > 0


def test_a_pace_cannot_be_set_above_the_maximum(rt):
    panel = ui.options(rt)
    walking = ui.slider(rt, panel, "walking")
    maximum = rt.ns.SpeedModel.DEFAULTS.maxKmh
    assert 3.8 * KMH_PER_MPH > maximum + 1.0, "well above the (default) maximum"
    ui.slide(rt, walking, 3.8)
    assert setting(rt, "speed.walkKmh") == setting(rt, "speed.maxKmh") == maximum
    assert ui.slider_text(rt, walking) == default_shown(rt, "maxKmh"), "the slider snaps back to the maximum"
    assert ui.slider_text(rt, walking) == ui.slider_text(rt, ui.slider(rt, panel, "Never faster than"))
    assert walking.GetValue(walking) == pytest.approx(maximum / KMH_PER_MPH, abs=0.001)
    assert "limited" not in ui.page(rt, panel), "a pace at the maximum is not above it"
    # A pace below the maximum is taken as it is.
    ui.slide(rt, walking, 2.4)
    assert setting(rt, "speed.walkKmh") == pytest.approx(2.4 * KMH_PER_MPH, abs=0.001) and ui.slider_text(rt, walking) == "2.4 mph"


def test_lowering_the_maximum_leaves_the_paces_and_says_so(rt):
    panel = ui.options(rt)
    assert "limited" not in ui.page(rt, panel)
    defaults = rt.ns.SpeedModel.DEFAULTS
    cap = ui.slider(rt, panel, "Never faster than")
    # Below the running and the mounted pace, above the others.
    assert defaults.walkKmh < 1.4 * KMH_PER_MPH < defaults.runKmh < defaults.mountKmh
    ui.slide(rt, cap, 1.4)
    assert setting(rt, "speed.maxKmh") == pytest.approx(1.4 * KMH_PER_MPH, abs=0.001)
    for key in ("standKmh", "walkKmh", "runKmh", "mountKmh"):
        assert setting(rt, "speed." + key) == defaults[key], "the paces are not touched"
    assert speeds_shown(rt, panel)["runKmh"] == default_shown(rt, "runKmh"), "... and are still shown as they are set"
    assert "Paces above the maximum are limited to it: the belt will not go faster than 1.4 mph." in ui.page(rt, panel)
    # Between the two: the mounted pace is still above it.
    assert defaults.runKmh < 1.8 * KMH_PER_MPH < defaults.mountKmh
    ui.slide(rt, cap, 1.8)
    assert "Paces above the maximum are limited to it: the belt will not go faster than 1.8 mph." in ui.page(rt, panel)
    # A maximum equal to the fastest pace limits nothing.
    ui.slide(rt, cap, float(default_shown(rt, "mountKmh").split()[0]))
    assert ui.slider_text(rt, cap) == default_shown(rt, "mountKmh")
    assert "limited" not in ui.page(rt, panel)
    ui.slide(rt, cap, 3.0)
    assert "limited" not in ui.page(rt, panel)


def test_odd_orders_are_pointed_out(rt):
    panel = ui.options(rt)
    ui.slide(rt, ui.slider(rt, panel, "standing still"), 1.8)
    assert "Standing is set faster than walking." in ui.page(rt, panel)
    ui.slide(rt, ui.slider(rt, panel, "walking"), 2.4)
    shown = ui.page(rt, panel)
    assert "Standing is set faster" not in shown and "Walking is set faster than running." in shown
    rt.click(ui.button(rt, panel, "Reset speed settings"))
    assert "faster than" not in ui.page(rt, panel).replace("Never faster than", "")


def test_stop_the_belt_when_standing(rt):
    panel = ui.options(rt)
    box = ui.checkbox(rt, panel, "Stop the belt when my character stands still")
    standing = ui.slider(rt, panel, "standing still")
    ui.slide(rt, standing, 1.2)
    stored = setting(rt, "speed.standKmh")
    assert stored == pytest.approx(1.2 * KMH_PER_MPH, abs=0.001) and not ui.checked(rt, box)

    rt.click(box)
    assert setting(rt, "speed.standKmh") == 0 and ui.checked(rt, box)
    assert not ui.slider_enabled(rt, standing), "nothing to set while the belt stops"
    assert ui.slide(rt, standing, 2.0) is False and setting(rt, "speed.standKmh") == 0
    back, forward = ui.steppers(rt, standing)
    assert rt.click(back) is False and rt.click(forward) is False
    assert ui.slider_text(rt, standing) == "1.2 mph", "it shows the pace that unticking brings back"

    rt.click(box)
    assert setting(rt, "speed.standKmh") == stored and not ui.checked(rt, box)
    assert ui.slider_enabled(rt, standing) and ui.slider_text(rt, standing) == "1.2 mph"
    ui.slide(rt, standing, 0.9)
    assert setting(rt, "speed.standKmh") == pytest.approx(0.9 * KMH_PER_MPH, abs=0.001)


def test_unticking_stop_when_standing_after_a_reload_restores_a_usable_pace(rt):
    rt.walka.Set(rt.walka, "speed.standKmh", 0)
    fresh = rt.reload()
    panel = ui.options(fresh)
    box = ui.checkbox(fresh, panel, "Stop the belt when my character stands still")
    assert ui.checked(fresh, box) and not ui.slider_enabled(fresh, ui.slider(fresh, panel, "standing still"))
    fresh.click(box)
    limits = lua_plain(fresh.walka.GetBeltLimits(fresh.walka))
    # The pace before the reload is not known any more: the default one.
    assert setting(fresh, "speed.standKmh") == fresh.ns.SpeedModel.DEFAULTS.standKmh
    assert setting(fresh, "speed.standKmh") >= limits["minKmh"]
    assert ui.slider_text(fresh, ui.slider(fresh, panel, "standing still")) == default_shown(fresh, "standKmh")


def test_live_line_follows_the_session(rt):
    panel = ui.options(rt)
    assert "Start a session (/walka start) to try these settings live." in ui.texts(rt, panel)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    # (The default paces are whole tenths of a mph, which is this belt's grid.)
    standing, running = default_shown(rt, "standKmh"), default_shown(rt, "runKmh")
    assert f"Right now: target {standing}, belt about 0.0 mph." in ui.texts(rt, panel)
    rt.tick(700)
    rt.walka.Fire(rt.walka, "TICK")
    assert f"Right now: target {running}, belt about {running}." in ui.texts(rt, panel)
    assert running != "2.3 mph" != default_shown(rt, "maxKmh")
    ui.slide(rt, ui.slider(rt, panel, "running"), 2.3)      # try a setting live
    rt.tick(700)
    rt.walka.Fire(rt.walka, "TICK")
    assert "Right now: target 2.3 mph, belt about 2.3 mph." in ui.texts(rt, panel)
    rt.slash("/walka pause")
    assert "The session is paused." in ui.texts(rt, panel)
    rt.slash("/walka stop")
    assert "Start a session (/walka start) to try these settings live." in ui.texts(rt, panel)


# ---- feel, flight paths, combat ------------------------------------------------------------------


def test_feel_presets_set_the_three_timings(rt):
    panel = ui.options(rt)
    timings = lambda: tuple(setting(rt, "speed." + key) for key in ("riseSeconds", "fallSeconds", "stopHoldSeconds"))  # noqa: E731
    assert timings() == (4, 8, 4)
    assert "A middle course between smooth and quick." in ui.texts(rt, panel), "Balanced is the default"

    settings_events = []
    rt.walka.On(rt.walka, "SETTINGS", lambda *args: settings_events.append(timings()))
    rt.click(ui.button(rt, panel, "Steady"))
    assert timings() == (8, 14, 6)
    assert any(text.startswith("Slow, smooth changes") for text in ui.texts(rt, panel))
    assert rt.walka.modelSettings.riseSeconds == 8

    rt.click(ui.button(rt, panel, "Responsive"))
    assert timings() == (2, 4, 2)
    assert any(text.startswith("Follows your character quickly") for text in ui.texts(rt, panel))

    rt.click(ui.button(rt, panel, "Balanced"))
    assert timings() == (4, 8, 4)
    assert len(settings_events) == 9 and settings_events[-1] == (4, 8, 4)

    # Timings set some other way are shown as what they are.
    rt.walka.Set(rt.walka, "speed.riseSeconds", 5)
    assert "Custom timings: 5 s to speed up, 8 s to slow down, 4 s hold after a stop." in ui.texts(rt, panel)
    rt.click(ui.button(rt, panel, "Steady"))
    assert timings() == (8, 14, 6) and not any(text.startswith("Custom timings") for text in ui.texts(rt, panel))
    fresh = rt.reload()
    assert fresh.walka.Get(fresh.walka, "speed.fallSeconds") == 14


def test_flight_path_choice(rt):
    panel = ui.options(rt)
    assert setting(rt, "speed.taxiMode") == "run"
    for text, mode in (("Standing pace", "stand"), ("Follow real speed", "follow"), ("Running pace", "run")):
        rt.click(ui.button(rt, panel, text))
        assert setting(rt, "speed.taxiMode") == mode
        assert rt.walka.modelSettings.taxiMode == mode
    assert ui.tooltip(rt, ui.button(rt, panel, "Follow real speed"))[0] == "Follow real speed"


@pytest.mark.parametrize(
    "label, path, default",
    [
        ("In combat, never drop below running pace", "speed.combatHold", False),
        ("Pause automatically when I go AFK", "autoPauseAFK", True),
        ("Show session messages in chat", "announce", True),
        ("Show the beacon (the companion app needs it)", "beacon.enabled", True),
        ("Show the HUD", "hud.shown", True),
        ("Lock its position", "hud.locked", False),
    ],
)
def test_checkboxes_round_trip(rt, label, path, default):
    panel = ui.options(rt)
    box = ui.checkbox(rt, panel, label)
    assert setting(rt, path) is default and ui.checked(rt, box) is default
    rt.click(box)
    assert setting(rt, path) is (not default) and ui.checked(rt, box) is (not default)
    # Changed elsewhere (a slash command, the HUD's menu): the box follows.
    rt.walka.Set(rt.walka, path, default)
    assert ui.checked(rt, box) is default
    rt.click(box)
    fresh = rt.reload()
    assert fresh.walka.Get(fresh.walka, path) is (not default)
    assert ui.checked(fresh, ui.checkbox(fresh, ui.options(fresh), label)) is (not default)


def test_a_click_on_a_checkbox_label_counts(rt):
    panel = ui.options(rt)
    box = ui.checkbox(rt, panel, "Pause automatically when I go AFK")
    left, right, top, bottom = box.GetHitRectInsets(box)
    assert right < -100, "the clickable area reaches over the label"
    assert (left, top, bottom) == (0, 0, 0)


def test_checkboxes_do_what_they_say(rt):
    panel = ui.options(rt)
    hud = ui.hud(rt)
    rt.click(ui.checkbox(rt, panel, "Show the HUD"))
    assert not ui.is_visible(rt, hud)
    rt.click(ui.checkbox(rt, panel, "Show the HUD"))
    assert ui.is_visible(rt, hud)
    rt.click(ui.checkbox(rt, panel, "Lock its position"))
    assert rt.M.drag(hud, 50, 50) is False
    rt.click(ui.checkbox(rt, panel, "Show the beacon (the companion app needs it)"))
    assert rt.inspect(rt.beacon_frame())["alpha"] == 0
    rt.click(ui.checkbox(rt, panel, "Show the beacon (the companion app needs it)"))
    assert rt.inspect(rt.beacon_frame())["alpha"] == 1


# ---- session -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, goal", [("7500", 7500), ("7,500", 7500), ("12 000", 12000), ("0", 0), ("200000", 200000)])
def test_daily_goal_accepts_numbers(rt, text, goal):
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, "Daily step goal")
    assert ui.box_text(box) == "6000"
    ui.type_into(rt, box, text)
    assert setting(rt, "dailyStepGoal") == goal and ui.box_text(box) == str(goal)
    fresh = rt.reload()
    assert fresh.walka.Get(fresh.walka, "dailyStepGoal") == goal


@pytest.mark.parametrize("text", ["abc", "", "-5", "1e9", "200001", "nan", "6000 steps", "6.5.1x"])
def test_daily_goal_refuses_nonsense(rt, text):
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, "Daily step goal")
    rt.clear_printed()
    ui.type_into(rt, box, text)
    assert setting(rt, "dailyStepGoal") == 6000 and ui.box_text(box) == "6000", "the box goes back to the stored goal"
    assert len(rt.printed) == 1 and "daily step goal" in rt.printed[0]


def test_edit_box_commit_and_cancel(rt):
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, "Daily step goal")
    # Clicking elsewhere commits, like Enter.
    ui.type_into(rt, box, "8000", enter=False)
    assert setting(rt, "dailyStepGoal") == 8000
    # Escape abandons the edit.
    rt.M.type_text(box, "9999", False)
    rt.M.fire_script(box, "OnEscapePressed")
    rt.check_errors()
    assert setting(rt, "dailyStepGoal") == 8000 and ui.box_text(box) == "8000"
    assert not box.HasFocus(box)
    # Leaving the text as it was commits nothing.
    events = []
    rt.walka.On(rt.walka, "SETTINGS", lambda *args: events.append(1))
    ui.type_into(rt, box, "8000")
    assert events == []
    # A setting that changes while the box is being typed in does not eat the typing.
    rt.M.type_text(box, "12", False)
    rt.walka.Set(rt.walka, "hud.alpha", 0.8)
    assert ui.box_text(box) == "12"
    rt.M.type_text(box, "1234", True)
    assert setting(rt, "dailyStepGoal") == 1234


def test_goal_shows_up_on_the_hud(rt):
    panel = ui.options(rt)
    ui.type_into(rt, ui.edit_box(rt, panel, "Daily step goal"), "0")
    assert ui.rect(rt, ui.hud(rt))[3] == pytest.approx(104), "no goal: the HUD drops its goal bar"
    ui.type_into(rt, ui.edit_box(rt, panel, "Daily step goal"), "9000")
    assert ui.rect(rt, ui.hud(rt))[3] == pytest.approx(116)
    assert any("of 9,000 steps" in line for line in ui.tooltip(rt, ui.hud(rt)))


# ---- body ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "units, text, kg, shown",
    [
        ("mph", "200", 90.7, "200"), ("mph", "180 lb", 81.6, "180"), ("mph", "82kg", 82, "181"), ("mph", "150lbs", 68.0, "150"),
        ("kmh", "82", 82, "82"), ("kmh", "81,5", 81.5, "81.5"), ("kmh", "180lb", 81.6, "81.6"), ("kmh", " 70 kg ", 70, "70"),
    ],
)
def test_weight(rt, units, text, kg, shown):
    rt.walka.Set(rt.walka, "units", units)
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, "Weight")
    ui.type_into(rt, box, text)
    assert setting(rt, "profile.weightKg") == pytest.approx(kg, abs=0.05)
    assert ui.box_text(box) == shown
    assert rt.walka.GetProfile(rt.walka).weightKg == setting(rt, "profile.weightKg")


@pytest.mark.parametrize(
    "units, text, cm, shown",
    [
        ("mph", "5'11", 180, "5'11\""), ("mph", "5'11\"", 180, "5'11\""), ("mph", "6'", 183, "6'0\""), ("mph", "70", 178, "5'10\""),
        ("mph", "180cm", 180, "5'11\""), ("kmh", "180", 180, "180"), ("kmh", "1.8m", 180, "180"), ("kmh", "5'6", 168, "168"),
        ("kmh", "64in", 163, "163"),
    ],
)
def test_height(rt, units, text, cm, shown):
    rt.walka.Set(rt.walka, "units", units)
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, "Height")
    ui.type_into(rt, box, text)
    assert setting(rt, "profile.heightCm") == cm
    assert ui.box_text(box) == shown


@pytest.mark.parametrize("row, text", [
    ("Weight", "abc"), ("Weight", "20"), ("Weight", "900"), ("Weight", "80 stone"), ("Weight", "nan"), ("Weight", "-80"),
    ("Weight", ""), ("Height", "abc"), ("Height", "30"), ("Height", "9'9"), ("Height", "170 furlongs"), ("Height", "inf"),
])
def test_body_fields_refuse_nonsense(rt, row, text):
    panel = ui.options(rt)
    box = ui.edit_box(rt, panel, row)
    before, profile = ui.box_text(box), lua_plain(rt.db.settings.profile)
    rt.clear_printed()
    ui.type_into(rt, box, text)
    assert lua_plain(rt.db.settings.profile) == profile and ui.box_text(box) == before
    assert len(rt.printed) == 1 and "looks wrong" in rt.printed[0]


def test_body_fields_are_read_only_when_the_companion_supplies_the_profile(crt):
    rt = crt
    panel = ui.options(rt)
    weight, height = ui.edit_box(rt, panel, "Weight"), ui.edit_box(rt, panel, "Height")
    # The companion's 80 kg and 178 cm, not the addon's own 75 kg and 172 cm.
    assert ui.box_text(weight) == "176" and ui.box_text(height) == "5'10\""
    assert "Set in the companion app." in ui.texts(rt, panel)
    own = lua_plain(rt.db.settings.profile)
    for box in (weight, height):
        assert rt.inspect(box)["disabled"]
        assert ui.type_into(rt, box, "99") is False
    assert lua_plain(rt.db.settings.profile) == own
    assert ui.box_text(weight) == "176"
    rt.click(ui.button(rt, panel, "km/h / km"))
    assert ui.box_text(weight) == "80" and ui.box_text(height) == "178"


def test_body_fields_can_be_edited_without_companion_profile(rt):
    panel = ui.options(rt)
    assert "Set in the companion app." not in ui.texts(rt, panel)
    assert not rt.inspect(ui.edit_box(rt, panel, "Weight"))["disabled"]
    assert not rt.inspect(ui.edit_box(rt, panel, "Height"))["disabled"]


# ---- beacon and HUD sliders ----------------------------------------------------------------------------------


def test_beacon_square_size(rt):
    panel = ui.options(rt)
    control = ui.slider(rt, panel, "Square size")
    default = rt.ns.Beacon.DEFAULT_CELL_PX
    assert control.GetMinMaxValues(control) == (2, 8) and ui.slider_text(rt, control) == f"{default} px"
    assert control.GetValue(control) == default == setting(rt, "beacon.cellPx") == rt.ns.Beacon.GetCellSize()
    for value, pixels in ((5, 5), (8, 8), (2, 2), (4.4, 4), (3, 3), (99, 8), (-3, 2)):
        ui.slide(rt, control, value)
        assert setting(rt, "beacon.cellPx") == pixels and rt.ns.Beacon.GetCellSize() == pixels
        assert ui.slider_text(rt, control) == f"{pixels} px"
    rt.slash("/walka beacon size 6")
    assert ui.slider_text(rt, control) == "6 px" and control.GetValue(control) == 6


def test_hud_size_and_opacity(rt):
    panel = ui.options(rt)
    hud = ui.hud(rt)
    size, opacity = ui.slider(rt, panel, "Size"), ui.slider(rt, panel, "Opacity")
    assert (ui.slider_text(rt, size), ui.slider_text(rt, opacity)) == ("100%", "90%")
    assert size.GetMinMaxValues(size) == pytest.approx((0.6, 1.6)) and opacity.GetMinMaxValues(opacity) == pytest.approx((0.3, 1.0))

    ui.slide(rt, size, 1.25)
    assert setting(rt, "hud.scale") == pytest.approx(1.25) and ui.slider_text(rt, size) == "125%"
    assert rt.inspect(hud)["scale"] == pytest.approx(1.25)
    ui.slide(rt, size, 1.2649)          # between steps: the 5% grid
    assert setting(rt, "hud.scale") == pytest.approx(1.25)
    ui.slide(rt, size, 9)
    assert setting(rt, "hud.scale") == pytest.approx(1.6) and ui.slider_text(rt, size) == "160%"

    ui.slide(rt, opacity, 0.5)
    assert setting(rt, "hud.alpha") == pytest.approx(0.5) and ui.slider_text(rt, opacity) == "50%"
    assert rt.inspect(hud)["alpha"] == pytest.approx(0.5)
    ui.slide(rt, opacity, 0)
    assert setting(rt, "hud.alpha") == pytest.approx(0.3), "never invisible"
    # Stored as tidy numbers.
    assert setting(rt, "hud.scale") == 1.6 and setting(rt, "hud.alpha") == 0.3
    rt.reload()


def test_reset_speed_settings(rt):
    panel = ui.options(rt)
    defaults = lua_plain(rt.ns.SpeedModel.DEFAULTS)
    shown = speeds_shown(rt, panel)
    ui.slide(rt, ui.slider(rt, panel, "Never faster than"), 3.5)
    ui.slide(rt, ui.slider(rt, panel, "running"), 2.7)
    assert speeds_shown(rt, panel) == {**shown, "maxKmh": "3.5 mph", "runKmh": "2.7 mph"}
    rt.click(ui.button(rt, panel, "Steady"))
    rt.click(ui.button(rt, panel, "Follow real speed"))
    rt.click(ui.checkbox(rt, panel, "In combat, never drop below running pace"))
    rt.click(ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
    ui.type_into(rt, ui.edit_box(rt, panel, "Daily step goal"), "9000")
    assert lua_plain(rt.db.settings.speed) != defaults
    reset = ui.button(rt, panel, "Reset speed settings")
    assert ui.tooltip(rt, reset)[0] == "Reset speed settings"
    rt.clear_printed()
    rt.click(reset)
    assert lua_plain(rt.db.settings.speed) == defaults
    assert any("defaults" in line for line in rt.printed)
    assert speeds_shown(rt, panel) == shown == {key: default_shown(rt, key) for key in SPEED_ROWS.values()}
    assert not ui.checked(rt, ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
    assert ui.slider_enabled(rt, ui.slider(rt, panel, "standing still"))
    assert setting(rt, "dailyStepGoal") == 9000, "only the speed settings are reset"


# ---- scrolling and layout -------------------------------------------------------------------------------------


def test_page_scrolls_and_every_control_can_be_reached(rt):
    panel = ui.options(rt)
    scroll = scroll_frame(rt, panel)
    view = ui.rect(rt, scroll)
    content = scroll.GetScrollChild(scroll)
    span = ui.rect(rt, content)[3] - view[3]
    assert span > 300, "the page is taller than the Settings window: it must scroll"
    track, thumb = scroll_bar(rt, panel)
    assert ui.is_visible(rt, track)

    def in_view(widget):
        return ui.inside(ui.rect(rt, widget), view)

    reset = ui.button(rt, panel, "Reset speed settings")
    units = ui.button(rt, panel, "mph / miles")
    assert in_view(units) and not in_view(reset)
    thumb_top = ui.rect(rt, thumb)[1] + ui.rect(rt, thumb)[3]
    assert thumb_top == pytest.approx(ui.rect(rt, track)[1] + ui.rect(rt, track)[3])

    rt.M.scroll_wheel(scroll, -1)
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(44)
    assert ui.rect(rt, thumb)[1] + ui.rect(rt, thumb)[3] < thumb_top, "the thumb follows the wheel"
    for _ in range(60):
        rt.M.scroll_wheel(scroll, -1)
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(span), "it stops at the end of the page"
    assert in_view(reset) and not in_view(units)
    assert ui.rect(rt, thumb)[1] == pytest.approx(ui.rect(rt, track)[1], abs=0.5)
    for _ in range(60):
        rt.M.scroll_wheel(scroll, 1)
    assert scroll.GetVerticalScroll(scroll) == 0 and in_view(units)

    # Every control comes into view somewhere along the way.
    controls = (
        rt.descendants(scroll, "CheckButton") + rt.descendants(scroll, "EditBox")
        + [rt.inspect(slider)["parent"] for slider in rt.descendants(scroll, "Slider")]
        + [button for button in rt.descendants(scroll, "Button") if ui.rect(rt, button) is not None]
    )
    assert len(controls) >= 25
    seen = set()
    for step in range(0, int(span) + 45, 44):
        scroll.SetVerticalScroll(scroll, min(step, span))
        seen |= {index for index, control in enumerate(controls) if in_view(control)}
    assert seen == set(range(len(controls)))
    rt.check_errors()


def test_scroll_bar_can_be_dragged_and_clicked(rt):
    panel = ui.options(rt)
    scroll = scroll_frame(rt, panel)
    span = ui.rect(rt, scroll.GetScrollChild(scroll))[3] - ui.rect(rt, scroll)[3]
    track, thumb = scroll_bar(rt, panel)
    track_box, thumb_box = ui.rect(rt, track), ui.rect(rt, thumb)
    travel = track_box[3] - thumb_box[3]
    grab = thumb_box[1] + thumb_box[3] / 2
    rt.set(cursorX=track_box[0] + 4, cursorY=grab)
    rt.M.fire_script(thumb, "OnMouseDown", "LeftButton")
    rt.set(cursorY=grab - travel / 2)
    rt.frame(0.02)
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(span / 2, abs=1)
    rt.set(cursorY=grab - 5000)
    rt.frame(0.02)
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(span)
    rt.M.fire_script(thumb, "OnMouseUp", "LeftButton")
    rt.set(cursorY=track_box[1] + track_box[3] - 2)     # click the track above the thumb: a page up
    rt.M.fire_script(track, "OnMouseDown", "LeftButton")
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(span - ui.rect(rt, scroll)[3] * 0.8)
    rt.check_errors()


def test_scroll_position_is_kept_within_range_when_the_window_is_resized(rt):
    panel = ui.options(rt)
    scroll = scroll_frame(rt, panel)
    for _ in range(60):
        rt.M.scroll_wheel(scroll, -1)
    end = scroll.GetVerticalScroll(scroll)
    # The Settings window is resizable: make the page area 200 taller.
    canvas = rt.inspect(panel)["parent"]
    container = rt.inspect(canvas)["parent"]
    rt.run("local frame = ... frame:SetPoint('BOTTOMRIGHT', frame:GetParent(), 'BOTTOMRIGHT', -22, -153)", container)
    rt.M.fire_script(scroll, "OnSizeChanged", 647, 737)
    rt.check_errors()
    assert scroll.GetVerticalScroll(scroll) == pytest.approx(end - 200), "no empty space below the page"


def test_controls_do_not_overlap_and_fit_the_page(rt):
    panel = ui.options(rt)
    scroll = scroll_frame(rt, panel)
    content = ui.rect(rt, scroll.GetScrollChild(scroll))
    controls = (
        rt.descendants(scroll, "CheckButton") + rt.descendants(scroll, "EditBox")
        + [rt.inspect(slider)["parent"] for slider in rt.descendants(scroll, "Slider")]
        + [button for button in rt.descendants(scroll, "Button") if ui.rect(rt, button) is not None]
    )
    boxes = [(control, ui.rect(rt, control)) for control in controls]
    for control, box in boxes:
        assert ui.inside(box, content), f"a control sticks out of the page: {ui.own_texts(rt, control)}"
    for index, (first, a) in enumerate(boxes):
        for second, b in boxes[index + 1:]:
            assert not ui.overlap(a, b), f"controls overlap: {ui.own_texts(rt, first)} and {ui.own_texts(rt, second)}"
    # The page is narrower than the room the Settings window gives it.
    assert content[2] <= ui.rect(rt, scroll)[2]
    for font_string in ui.font_strings(rt, scroll, visible=True):
        box, text = ui.rect(rt, font_string), rt.inspect(font_string).get("text")
        if box is not None and text:
            assert box[0] >= content[0] - 0.5 and box[0] + box[2] <= content[0] + content[2] + 0.5, text


def test_no_label_is_cut_off_in_either_unit(crt):
    rt = crt
    panel = ui.options(rt)
    rt.walka.Set(rt.walka, "speed.maxKmh", 0.8)             # the longest warning ...
    rt.walka.Set(rt.walka, "speed.standKmh", 3.0)           # ... with both order notes
    rt.walka.Set(rt.walka, "speed.walkKmh", 2.9)
    rt.walka.Set(rt.walka, "speed.runKmh", 2.0)
    rt.walka.Set(rt.walka, "speed.riseSeconds", 12.5)       # the "custom timings" sentence
    for units in ("mph", "kmh"):
        rt.walka.Set(rt.walka, "units", units)
        checked = 0
        for font_string in ui.font_strings(rt, panel, visible=True):
            info = rt.inspect(font_string)
            text, width = ui.plain(info.get("text")), info.get("width")
            if text and width:
                lines = max(1, round((info.get("height") or 12) / 12))
                needed = font_string.GetStringWidth(font_string)
                assert needed <= width * lines + 0.5, f"{text!r} needs {needed:.0f} px, has {lines} line(s) of {width:.0f}"
                checked += 1
        assert checked >= 40


# ---- cost and robustness ---------------------------------------------------------------------------------------


def test_refreshing_the_page_creates_no_widgets(rt):
    panel = ui.options(rt)
    ui.tooltip(rt, ui.button(rt, panel, "Follow real speed"))
    rt.set(speed=7.0)
    rt.slash("/walka start")            # (the HUD draws its pause icon the first time it is needed)
    rt.slash("/walka stop")
    count = ui.widget_count(rt)
    for _ in range(3):
        rt.click(ui.button(rt, panel, "km/h / km"))
        rt.click(ui.button(rt, panel, "mph / miles"))
        for row in SPEED_ROWS:
            ui.slide(rt, ui.slider(rt, panel, row), 2.2)
        rt.click(ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
        rt.click(ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
        rt.click(ui.button(rt, panel, "Steady"))
        ui.type_into(rt, ui.edit_box(rt, panel, "Weight"), "170")
        rt.slash("/walka start")
        rt.tick(100)
        rt.slash("/walka stop")
        rt.M.settings_close()
        rt.ns.Options.Open()
        rt.M.settings_select_other()
        rt.ns.Options.Open()
        rt.click(ui.button(rt, panel, "Reset speed settings"))
    assert ui.widget_count(rt) == count, "controls are built once and then only updated"


def test_a_closed_page_is_not_refreshed(rt):
    panel = ui.options(rt)
    running = ui.slider(rt, panel, "running")
    calls = ui.Counter(rt, rt.inspect(running)["parent"], "FormatValue")
    rt.M.settings_close()
    calls.reset()
    for step in range(30):
        rt.walka.Set(rt.walka, "speed.runKmh", 2.0 + step / 100)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    assert calls.calls == 0, "nothing on the page is touched while the Settings window is closed"
    rt.ns.Options.Open()
    assert calls.calls >= 1 and ui.slider_text(rt, running) == "1.4 mph"


def test_a_page_that_cannot_be_built_says_so_once(rt):
    """If a stock template changes under the addon, the page must fail
    politely: one error report, a readable sentence, working slash commands."""
    rt.M.run("local ns = ... ns.Widgets.Slider = function() error('template changed') end", rt.ns)
    with pytest.raises(AddonError, match="template changed"):
        rt.ns.Options.Open()
        rt.check_errors()
    panel = ui.options(rt, open_it=False)
    assert ui.is_visible(rt, panel)
    assert any("could not build its options page" in text for text in ui.texts(rt, panel))
    # No second report: not on a refresh, not on reopening, not on a tick.
    rt.walka.Set(rt.walka, "units", "kmh")
    rt.M.settings_close()
    rt.ns.Options.Open()
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(50)
    rt.slash("/walka stop")
    rt.check_errors()
    assert sum("could not build" in text for text in ui.texts(rt, panel)) == 1


@pytest.mark.parametrize(
    "path, value",
    [
        ("units", "'furlongs'"), ("speed.runKmh", "'fast'"), ("speed.runKmh", "0/0"), ("speed.maxKmh", "1/0"),
        ("speed.standKmh", "-3"), ("speed.taxiMode", "42"), ("speed.riseSeconds", "{}"), ("dailyStepGoal", "'many'"),
        ("profile.weightKg", "'heavy'"), ("profile.heightCm", "{}"), ("beacon.cellPx", "'big'"), ("hud.scale", "'huge'"),
        ("hud.alpha", "0/0"), ("speed.combatHold", "'yes'"), ("autoPauseAFK", "0"),
    ],
)
def test_page_survives_nonsense_settings(rt, path, value):
    """Whatever Walka:Set is given (by another addon, a macro, a damaged
    file), the page must still draw."""
    panel = ui.options(rt)
    try:
        rt.run(f"Walka:Set('{path}', {value})")
    except AddonError as error:                     # the core may object; the page must not
        assert "UI/Options.lua" not in str(error) and "UI/Widgets.lua" not in str(error), error
    rt.M.settings_close()
    try:
        rt.ns.Options.Open()
        rt.check_errors()
        rt.tick(3)
    except AddonError as error:
        assert "UI/Options.lua" not in str(error) and "UI/Widgets.lua" not in str(error), error
    assert ui.is_visible(rt, panel)
