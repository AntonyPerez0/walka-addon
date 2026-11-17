"""Core/SpeedModel.lua: character speed -> belt-speed target."""

from __future__ import annotations

import math
import random

import pytest

KMH_PER_MPH = 1.609344

# Belt limits the addon assumes for a US pad (0.6-4.0 mph in 0.1 mph steps).
MPH_GRID = {"minBeltKmh": 0.6 * KMH_PER_MPH, "stepKmh": 0.1 * KMH_PER_MPH}


class Model:
    """A SpeedModel instance driven from Python."""

    def __init__(self, rt, **settings):
        self.rt = rt
        self.sm = rt.ns.SpeedModel
        self.settings = rt.to_lua(settings)
        self.model = self.sm.new(self.settings)

    def setting(self, key):
        value = self.settings[key]
        return self.sm.DEFAULTS[key] if value is None else value

    def update(self, dt, speed=0.0, **flags):
        state = self.rt.to_lua({"speed": speed, **flags})
        return self.model.update(self.model, dt, state)

    def run(self, seconds, speed=0.0, dt=0.1, **flags):
        """Hold an input for a while; returns every output."""
        return [self.update(dt, speed, **flags) for _ in range(int(round(seconds / dt)))]

    @property
    def smoothed(self):
        return self.model.smoothed


# The cap is compared at half the beacon's resolution (see SpeedModel.lua).
CAP_TOLERANCE = 0.005


def on_grid(value, step):
    return abs(value / step - round(value / step)) < 1e-6


# ---- the curve ----------------------------------------------------------------


@pytest.mark.parametrize(
    "yps, kmh",
    [
        (0, 1.6),       # standing
        (2.5, 2.4),     # walking
        (7.0, 3.2),     # running
        (14.0, 4.0),    # 100% mount
        (30.0, 4.0),    # faster than that changes nothing
        (1.25, 2.0),    # halfway stand -> walk
        (4.75, 2.8),    # halfway walk -> run
        (10.5, 3.6),    # halfway run -> mount (e.g. a 50% speed buff)
        (-3, 1.6),      # nonsense input
    ],
)
def test_curve_anchors(ns, yps, kmh):
    assert ns.SpeedModel.curve(None, yps) == pytest.approx(kmh)


def test_curve_uses_the_given_settings_and_defaults_for_the_rest(rt, ns):
    settings = rt.to_lua({"runKmh": 5.0})
    assert ns.SpeedModel.curve(settings, 7.0) == pytest.approx(5.0)
    assert ns.SpeedModel.curve(settings, 2.5) == pytest.approx(2.4)
    assert ns.SpeedModel.curve(settings, None) == pytest.approx(1.6)


def test_curve_never_decreases_with_speed(ns):
    values = [ns.SpeedModel.curve(None, yps / 10) for yps in range(0, 400)]
    assert values == sorted(values)


def test_default_anchor_speeds_are_whole_tenths_of_a_mph(ns):
    # 1.0 / 1.5 / 2.0 / 2.5 / 3.0 mph, as the comments in DEFAULTS say.
    defaults = ns.SpeedModel.DEFAULTS
    for key, mph in [("standKmh", 1.0), ("walkKmh", 1.5), ("runKmh", 2.0), ("mountKmh", 2.5), ("maxKmh", 3.0)]:
        assert defaults[key] / KMH_PER_MPH == pytest.approx(mph, abs=0.02)


# ---- update: range, grid, smoothing ---------------------------------------------


def test_starts_at_the_standing_pace(rt):
    model = Model(rt)
    assert model.update(0.1, 0) == pytest.approx(1.6)


def test_converges_on_the_running_pace_and_stays_there(rt):
    model = Model(rt)
    outputs = model.run(40, speed=7.0)
    assert outputs[-1] == pytest.approx(3.2)
    # Once there it does not flicker between steps.
    assert set(round(value, 6) for value in outputs[-100:]) == {3.2}
    # On the way up it never overshoots and never goes back down.
    assert outputs == sorted(outputs)
    assert max(outputs) <= 3.2 + 1e-9


def test_rise_and_fall_time_constants(rt):
    model = Model(rt, stopHoldSeconds=0)
    model.run(4, speed=7.0, dt=0.01)        # one rise time constant
    assert model.smoothed == pytest.approx(1.6 + (3.2 - 1.6) * (1 - math.exp(-1)), abs=0.01)
    model.run(120, speed=7.0)
    model.run(8, speed=0, dt=0.01)          # one fall time constant
    assert model.smoothed == pytest.approx(1.6 + (3.2 - 1.6) * math.exp(-1), abs=0.01)


def test_slowing_down_is_gentler_than_speeding_up(rt):
    up = Model(rt, stopHoldSeconds=0)
    rising = up.run(60, speed=7.0)
    seconds_up = next(i for i, value in enumerate(rising) if value >= 3.2 - 1e-9) / 10
    falling = up.run(60, speed=0)
    seconds_down = next(i for i, value in enumerate(falling) if value <= 1.6 + 1e-9) / 10
    assert seconds_down > seconds_up * 1.5


@pytest.mark.parametrize("grid", [{}, MPH_GRID], ids=["kmh-grid", "mph-grid"])
@pytest.mark.parametrize("seed", range(6))
def test_output_is_zero_or_within_the_belt_range_and_on_the_step_grid(rt, grid, seed):
    rng = random.Random(seed)
    settings = dict(grid)
    if rng.random() < 0.5:
        settings["standKmh"] = 0            # "stop the belt when I stand"
    if rng.random() < 0.5:
        # (0.5 is below what any belt can hold: then the only answer is 0)
        settings["maxKmh"] = rng.choice([0.5, 2.0, 3.0, 3.5, 4.8, 6.0])
    settings["taxiMode"] = rng.choice(["run", "stand", "follow"])
    settings["combatHold"] = rng.random() < 0.5
    model = Model(rt, **settings)
    low, high, step = model.setting("minBeltKmh"), model.setting("maxKmh"), model.setting("stepKmh")
    speed = 0.0
    for _ in range(4000):
        if rng.random() < 0.02:
            speed = rng.choice([0, 0, 0.3, 2.5, 4.9, 7.0, 8.75, 11.9, 14.0, 27.0, 60.0])
        output = model.update(
            rng.choice([0.016, 0.1, 0.1, 0.1, 0.25, 1.0]),
            speed,
            taxi=rng.random() < 0.03,
            falling=rng.random() < 0.05,
            combat=rng.random() < 0.2,
        )
        assert model.smoothed <= high + 1e-9, "the smoothed speed exceeded the cap"
        assert model.smoothed >= 0
        if output != 0:
            assert low - 1e-9 <= output <= high + CAP_TOLERANCE + 1e-9
            assert on_grid(output, step), f"{output} is not a multiple of {step}"


def test_cap_applies_at_once_when_lowered(rt):
    model = Model(rt)
    model.run(60, speed=14.0)
    assert model.update(0.1, 14.0) == pytest.approx(4.0)
    model.settings.maxKmh = 3.0             # the options panel edits the live table
    assert model.update(0.1, 14.0) <= 3.0 + 1e-9
    assert all(value <= 3.0 + 1e-9 for value in model.run(30, speed=14.0))


@pytest.mark.parametrize("tenths", range(6, 41))
def test_a_cap_chosen_in_mph_allows_exactly_that_speed(rt, tenths):
    # The options page stores the cap in km/h rounded to 0.001: "1.0 mph"
    # becomes 1.609, a hair under the grid value 1.609344. That must not
    # cost a whole 0.1 mph step.
    mph = tenths / 10
    stored = round(mph * KMH_PER_MPH, 3)
    model = Model(rt, maxKmh=stored, mountKmh=9.0, **MPH_GRID)
    outputs = model.run(120, speed=14.0)
    assert outputs[-1] == pytest.approx(mph * KMH_PER_MPH, abs=1e-6)
    assert max(outputs) <= stored + CAP_TOLERANCE


@pytest.mark.parametrize("cap", [1.0, 1.04, 1.05, 2.449, 2.45, 3.0, 4.8, 5.999, 6.0])
def test_cap_on_the_kmh_grid(rt, cap):
    model = Model(rt, maxKmh=cap, mountKmh=9.0)
    outputs = model.run(120, speed=14.0)
    expected = math.floor((cap + CAP_TOLERANCE) * 10 + 1e-9) / 10
    assert outputs[-1] == pytest.approx(expected, abs=1e-6)
    assert max(outputs) <= cap + CAP_TOLERANCE + 1e-9


def test_cap_that_is_off_the_grid_rounds_down(rt):
    # 4.8 km/h is 2.98 mph: the nearest 0.1 mph that does not exceed it is 2.9.
    model = Model(rt, mountKmh=6.0, **MPH_GRID)
    outputs = model.run(90, speed=14.0)
    assert outputs[-1] == pytest.approx(2.9 * KMH_PER_MPH)
    assert max(outputs) <= 4.8


@pytest.mark.parametrize("grid", [{}, MPH_GRID], ids=["kmh-grid", "mph-grid"])
def test_a_cap_below_the_belts_minimum_means_no_belt_speed_at_all(rt, grid):
    # "Never faster than 0.8 km/h" on a belt that cannot go slower than
    # 1.0: the cap is the safety limit, so it wins.
    model = Model(rt, maxKmh=0.8, **grid)
    assert set(model.run(30, speed=7.0)) == {0}
    assert set(model.run(10, speed=0)) == {0}
    model.settings.maxKmh = 3.0             # raised again: back to normal
    step = model.setting("stepKmh")
    assert 3.0 - step < model.run(60, speed=7.0)[-1] <= 3.0 + 1e-9


def test_zero_and_negative_dt_change_nothing(rt):
    model = Model(rt)
    model.run(5, speed=7.0)
    before = model.smoothed
    first = model.update(0, 7.0)
    assert model.update(-5, 7.0) == first
    assert model.smoothed == before
    assert not math.isnan(model.smoothed)


def test_missing_input_fields_are_treated_as_standing(rt):
    model = Model(rt)
    assert model.model.update(model.model, 0.1, rt.lua.table()) == pytest.approx(1.6)
    assert model.model.update(model.model, None, rt.lua.table()) == pytest.approx(1.6)


def test_reset_forgets_history_and_can_seed_the_speed(rt):
    model = Model(rt)
    model.run(60, speed=7.0)
    model.model.reset(model.model, None)
    assert model.update(0.1, 0) == pytest.approx(1.6)
    model.model.reset(model.model, 3.0)
    assert model.update(0.1, 7.0) == pytest.approx(3.0, abs=0.06)


def test_instant_response_when_time_constants_are_zero(rt):
    model = Model(rt, riseSeconds=0, fallSeconds=0, stopHoldSeconds=0)
    assert model.update(0.1, 7.0) == pytest.approx(3.2)
    assert model.update(0.1, 0) == pytest.approx(1.6)


# ---- stop hold --------------------------------------------------------------------


def test_stop_hold_keeps_the_pace_through_a_two_second_stop(rt):
    model = Model(rt)
    model.run(40, speed=7.0)
    during = model.run(2, speed=0)          # looting
    assert during == [pytest.approx(3.2)] * 20
    after = model.run(3, speed=7.0)
    assert after == [pytest.approx(3.2)] * 30


def test_pace_falls_once_the_hold_runs_out(rt):
    model = Model(rt)
    model.run(40, speed=7.0)
    outputs = model.run(60, speed=0)
    assert outputs[35] == pytest.approx(3.2)                # still held at 3.5 s
    assert outputs[60] < 3.2                                # falling by 6 s
    assert outputs[-1] == pytest.approx(1.6)
    assert outputs == sorted(outputs, reverse=True)


def test_hold_length_is_a_setting(rt):
    model = Model(rt, stopHoldSeconds=0)
    model.run(40, speed=7.0)
    assert model.run(1.5, speed=0)[-1] < 3.2


def test_hold_remembers_the_pace_before_the_stop_not_a_higher_one(rt):
    model = Model(rt)
    model.run(60, speed=2.5)                # walking: 2.4 km/h
    assert model.run(3, speed=0) == [pytest.approx(2.4)] * 30


# ---- state rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, expected",
    [("run", 3.2), ("stand", 1.6), ("follow", 4.0)],
)
def test_taxi_modes(rt, mode, expected):
    model = Model(rt, taxiMode=mode, stopHoldSeconds=0)
    outputs = model.run(90, speed=32.0, taxi=True)      # a flight path is fast
    assert outputs[-1] == pytest.approx(expected)


def test_taxi_default_is_a_running_pace(rt):
    model = Model(rt)
    assert model.run(60, speed=32.0, taxi=True)[-1] == pytest.approx(3.2)


def test_falling_keeps_the_pace_whatever_the_speed_reads(rt):
    model = Model(rt, stopHoldSeconds=0)
    model.run(40, speed=7.0)
    # A long fall: the reported speed drops to 0 but the intent has not changed.
    assert model.run(10, speed=0, falling=True) == [pytest.approx(3.2)] * 100
    # Jumping on the spot does not start the belt either.
    still = Model(rt, standKmh=0)
    still.run(30, speed=0)
    assert set(still.run(5, speed=0, falling=True)) == {0}


def test_combat_hold_keeps_at_least_the_running_pace(rt):
    held = Model(rt, combatHold=True, stopHoldSeconds=0)
    assert held.run(60, speed=0, combat=True)[-1] == pytest.approx(3.2)
    assert held.run(60, speed=14.0, combat=True)[-1] == pytest.approx(4.0)   # faster is not slowed
    assert held.run(60, speed=0, combat=False)[-1] == pytest.approx(1.6)     # only in combat
    default = Model(rt, stopHoldSeconds=0)
    assert default.run(60, speed=0, combat=True)[-1] == pytest.approx(1.6)   # off by default


# ---- standing speed 0: the belt stops -----------------------------------------------


@pytest.mark.parametrize("grid", [{}, MPH_GRID], ids=["kmh-grid", "mph-grid"])
def test_stand_speed_zero_stops_the_belt_and_restarts_at_the_minimum(rt, grid):
    model = Model(rt, standKmh=0, **grid)
    low = model.setting("minBeltKmh")
    step = model.setting("stepKmh")
    model.run(40, speed=7.0)
    stopping = model.run(60, speed=0)
    assert stopping[-1] == 0
    # It slows down through the belt's range and then stops; it never asks
    # for a speed the belt cannot hold.
    moving = [value for value in stopping if value != 0]
    assert moving and min(moving) >= low - 1e-9
    assert stopping == sorted(stopping, reverse=True)

    restart = model.run(30, speed=7.0)
    assert restart[0] == pytest.approx(low, abs=step * 0.5 + 1e-9)
    assert restart[0] >= low - 1e-9
    assert restart == sorted(restart)
    assert restart[-1] == pytest.approx(round(3.2 / step) * step)


def test_stand_speed_zero_from_a_fresh_model_asks_for_nothing(rt):
    model = Model(rt, standKmh=0)
    assert set(model.run(10, speed=0)) == {0}


def test_creeping_forward_runs_the_belt_at_its_minimum(rt):
    model = Model(rt, standKmh=0)
    outputs = model.run(20, speed=0.2)
    assert outputs[-1] == pytest.approx(1.0)


def test_conversions(ns):
    assert ns.SpeedModel.kmhToMph(KMH_PER_MPH) == pytest.approx(1.0)
    assert ns.SpeedModel.mphToKmh(2.0) == pytest.approx(3.218688)
