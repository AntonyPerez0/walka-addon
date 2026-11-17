"""The "keep walking" setting: standing still must not slow the belt below
the walking pace."""

from __future__ import annotations

import pytest

from .test_speed_model import MPH_GRID, Model


def default_walk(rt) -> float:
    return rt.ns.SpeedModel.DEFAULTS.walkKmh


def default_stand(rt) -> float:
    return rt.ns.SpeedModel.DEFAULTS.standKmh


def test_off_by_default(rt):
    assert rt.ns.SpeedModel.DEFAULTS.keepWalking is False
    # (The setting only means something while standing is slower than walking.)
    assert 0 < default_stand(rt) < default_walk(rt)
    model = Model(rt)
    assert model.sm.curve(model.settings, 0) == pytest.approx(default_stand(rt))


def test_curve_floor_is_the_walking_pace(rt):
    model = Model(rt, keepWalking=True)
    curve = lambda yps: model.sm.curve(model.settings, yps)  # noqa: E731
    walk = model.setting("walkKmh")
    assert curve(0) == pytest.approx(walk)
    assert curve(1.0) == pytest.approx(walk)  # slowed/snared: still the floor
    assert curve(2.5) == pytest.approx(walk)
    # Above walking speed the curve is unchanged.
    assert curve(7.0) == pytest.approx(model.setting("runKmh"))
    assert curve(14.0) == pytest.approx(model.setting("mountKmh"))
    assert curve(4.75) > walk


def test_belt_stays_at_walking_pace_when_the_character_stops(rt):
    model = Model(rt, keepWalking=True)
    model.run(60, speed=7.0)
    outputs = model.run(120, speed=0.0)
    walk = model.setting("walkKmh")
    assert outputs[-1] == pytest.approx(walk, abs=0.051)
    # It eases down to the walking pace and never goes below it.
    assert min(outputs) >= walk - 0.051


def test_without_it_the_belt_drops_to_the_standing_pace(rt):
    model = Model(rt)
    model.run(60, speed=7.0)
    outputs = model.run(120, speed=0.0)
    assert outputs[-1] == pytest.approx(default_stand(rt), abs=0.051)


def test_overrides_stop_the_belt_when_standing(rt):
    # standKmh = 0 normally stops the belt; keep walking wins.
    model = Model(rt, keepWalking=True, standKmh=0)
    model.run(30, speed=7.0)
    outputs = model.run(120, speed=0.0)
    assert min(outputs) > 0
    assert outputs[-1] == pytest.approx(model.setting("walkKmh"), abs=0.051)


def test_session_starts_at_the_walking_pace(rt):
    model = Model(rt, keepWalking=True)
    assert model.smoothed == pytest.approx(model.setting("walkKmh"))
    first = model.update(0.1, 0.0)
    assert first == pytest.approx(model.setting("walkKmh"), abs=0.051)


def test_floor_respects_the_maximum(rt):
    model = Model(rt, keepWalking=True, walkKmh=3.0, maxKmh=2.0)
    outputs = model.run(60, speed=0.0)
    assert max(outputs) <= 2.0 + 1e-6


def test_on_the_mph_grid(rt):
    mph = 1.609344
    model = Model(rt, keepWalking=True, walkKmh=1.5 * mph, standKmh=1.0 * mph, runKmh=2.0 * mph, **MPH_GRID)
    model.run(60, speed=7.0)
    outputs = model.run(120, speed=0.0)
    assert outputs[-1] == pytest.approx(1.5 * mph, abs=1e-6)


def test_taxi_stand_mode_and_combat_use_the_floor(rt):
    model = Model(rt, keepWalking=True, taxiMode="stand")
    outputs = model.run(60, speed=30.0, taxi=True)
    assert outputs[-1] == pytest.approx(model.setting("walkKmh"), abs=0.051)
    outputs = model.run(60, speed=0.0, combat=True)
    assert outputs[-1] == pytest.approx(model.setting("walkKmh"), abs=0.051)


def test_setting_reaches_the_live_model_through_the_addon(rt):
    """Walka:Set("speed.keepWalking", true) changes the running session."""
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(60)
    rt.set(speed=0.0)
    rt.advance(120)
    _, stand_target = walka.GetLiveSpeed(walka)
    walka.Set(walka, "speed.keepWalking", True)
    rt.advance(60)
    _, walk_target = walka.GetLiveSpeed(walka)
    walk_kmh = walka.Get(walka, "speed.walkKmh")
    assert stand_target < walk_kmh - 0.2
    assert walk_target == pytest.approx(walk_kmh, abs=0.09)
    rt.check_errors()


# ---- the controls: options checkbox, HUD menu, slash command ------------------------------------

from . import ui_driver as ui  # noqa: E402

KEEP = "Keep walking when my character stands still"
STOP_STANDING = "Stop the belt when my character stands still"


def test_options_checkbox_round_trips_and_greys_out_the_standing_controls(rt):
    walka = rt.walka
    panel = ui.options(rt)
    box = ui.checkbox(rt, panel, KEEP)
    stand_slider = ui.slider(rt, panel, "standing still")
    stop_box = ui.checkbox(rt, panel, STOP_STANDING)
    assert not ui.checked(rt, box)
    assert ui.slider_enabled(rt, stand_slider) and stop_box.IsEnabled(stop_box)

    ui.click(rt, box)
    assert walka.Get(walka, "speed.keepWalking") is True
    assert ui.checked(rt, box)
    # The standing pace no longer applies: its slider shows the walking pace, disabled.
    assert not ui.slider_enabled(rt, stand_slider)
    walk_slider = ui.slider(rt, panel, "walking")
    assert ui.slider_text(rt, stand_slider) == ui.slider_text(rt, walk_slider)
    assert not stop_box.IsEnabled(stop_box) and not ui.checked(rt, stop_box)

    ui.click(rt, box)
    assert walka.Get(walka, "speed.keepWalking") is False
    assert ui.slider_enabled(rt, stand_slider) and stop_box.IsEnabled(stop_box)
    rt.check_errors()


def test_options_keep_walking_overrides_a_ticked_stop_when_standing(rt):
    walka = rt.walka
    panel = ui.options(rt)
    ui.click(rt, ui.checkbox(rt, panel, STOP_STANDING))
    assert walka.Get(walka, "speed.standKmh") == 0
    ui.click(rt, ui.checkbox(rt, panel, KEEP))
    stop_box = ui.checkbox(rt, panel, STOP_STANDING)
    assert not ui.checked(rt, stop_box) and not stop_box.IsEnabled(stop_box)
    # Turning keep walking off again brings the earlier choice back.
    ui.click(rt, ui.checkbox(rt, panel, KEEP))
    assert ui.checked(rt, stop_box) and stop_box.IsEnabled(stop_box)
    assert walka.Get(walka, "speed.standKmh") == 0


def test_hud_menu_toggles_keep_walking(rt):
    walka = rt.walka
    frame = ui.hud(rt)
    assert "checkbox:Keep walking when standing:false" in ui.context_menu(rt, frame)
    ui.menu_click(rt, "Keep walking when standing")
    assert walka.Get(walka, "speed.keepWalking") is True
    assert "checkbox:Keep walking when standing:true" in ui.context_menu(rt, frame)
    assert any("Keep walking is on" in line for line in ui.tooltip(rt, frame))
    ui.menu_click(rt, "Keep walking when standing")
    assert walka.Get(walka, "speed.keepWalking") is False
    assert not any("Keep walking is on" in line for line in ui.tooltip(rt, frame))
    rt.check_errors()


def test_slash_command(rt):
    walka = rt.walka
    rt.clear_printed()
    rt.slash("/walka keepwalking on")
    assert walka.Get(walka, "speed.keepWalking") is True
    assert any("keep walking is on" in line for line in rt.printed)
    rt.slash("/walka keepwalking off")
    assert walka.Get(walka, "speed.keepWalking") is False
    rt.slash("/walka keepwalking")  # no argument: toggle
    assert walka.Get(walka, "speed.keepWalking") is True
    rt.slash("/walka keep")
    assert walka.Get(walka, "speed.keepWalking") is False
    rt.clear_printed()
    rt.slash("/walka keepwalking maybe")
    assert walka.Get(walka, "speed.keepWalking") is False
    assert any("usage" in line for line in rt.printed)


def test_setting_survives_a_reload_and_a_reset_turns_it_off(rt):
    rt.slash("/walka keepwalking on")
    again = rt.reload()
    walka = again.walka
    assert walka.Get(walka, "speed.keepWalking") is True
    walka.ResetSpeedSettings(walka)
    assert walka.Get(walka, "speed.keepWalking") is False
