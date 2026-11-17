"""Core/SpeedModel.lua: character speed -> belt-speed target."""

from __future__ import annotations

import math
import random

import pytest

from .conftest import KMH_PER_MPH, MPH_STEP, nearest_step

# Belt limits the addon assumes for a US pad (0.6-4.0 mph in 0.1 mph steps).
MPH_GRID = {"minBeltKmh": 0.6 * KMH_PER_MPH, "stepKmh": MPH_STEP}

# The paces of the speed mapping, slowest movement first.
PACES = ("standKmh", "walkKmh", "runKmh", "mountKmh")


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

    def pace(self, key):
        """What the model settles on for one of the paces ("runKmh", ...)
        when nothing limits it: the pace on the belt's speed grid."""
        return nearest_step(self.setting(key), self.setting("stepKmh"))

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
    "yps, lower, upper, share",
    [
        (0, "standKmh", "standKmh", 0),         # standing
        (2.5, "walkKmh", "walkKmh", 0),         # walking
        (7.0, "runKmh", "runKmh", 0),           # running
        (14.0, "mountKmh", "mountKmh", 0),      # 100% mount
        (30.0, "mountKmh", "mountKmh", 0),      # faster than that changes nothing
        (1.25, "standKmh", "walkKmh", 0.5),     # halfway stand -> walk
        (4.75, "walkKmh", "runKmh", 0.5),       # halfway walk -> run
        (10.5, "runKmh", "mountKmh", 0.5),      # halfway run -> mount (e.g. a 50% speed buff)
        (8.75, "runKmh", "mountKmh", 0.25),     # a 25% speed buff
        (-3, "standKmh", "standKmh", 0),        # nonsense input
    ],
)
@pytest.mark.parametrize(
    "settings",
    [None, {"standKmh": 0, "walkKmh": 2.0, "runKmh": 3.5, "mountKmh": 5.0}, {"runKmh": 4.4}],
    ids=["defaults", "own-paces", "one-own-pace"],
)
def test_curve_anchors(rt, ns, settings, yps, lower, upper, share):
    """The paces at 0 / 2.5 / 7 / 14 yd/s and straight lines between them,
    whatever the paces are set to (the defaults are pinned further down)."""
    paces = {key: (settings or {}).get(key, ns.SpeedModel.DEFAULTS[key]) for key in PACES}
    expected = paces[lower] + (paces[upper] - paces[lower]) * share
    assert ns.SpeedModel.curve(settings and rt.to_lua(settings), yps) == pytest.approx(expected)


def test_curve_uses_the_given_settings_and_defaults_for_the_rest(rt, ns):
    defaults = ns.SpeedModel.DEFAULTS
    settings = rt.to_lua({"runKmh": 5.0})
    assert ns.SpeedModel.curve(settings, 7.0) == pytest.approx(5.0)
    assert ns.SpeedModel.curve(settings, 2.5) == pytest.approx(defaults.walkKmh)
    assert ns.SpeedModel.curve(settings, None) == pytest.approx(defaults.standKmh)


def test_curve_never_decreases_with_speed(ns):
    values = [ns.SpeedModel.curve(None, yps / 10) for yps in range(0, 400)]
    assert values == sorted(values)


# The default paces: gentle ones, in the range people settle at on a
# treadmill desk. {setting: (mph, km/h as stored, on the 0.1 km/h grid)}
DEFAULT_PACES = {
    "standKmh": (0.8, 1.287, 1.3),
    "walkKmh": (1.2, 1.931, 1.9),
    "runKmh": (1.6, 2.575, 2.6),
    "mountKmh": (2.0, 3.219, 3.2),
    "maxKmh": (2.5, 4.023, 4.0),
}


def test_default_anchor_speeds_are_whole_tenths_of_a_mph(ns):
    # 0.8 / 1.2 / 1.6 / 2.0 / 2.5 mph, as the comments in DEFAULTS say,
    # stored as the options page stores a speed: km/h rounded to 0.001.
    defaults = ns.SpeedModel.DEFAULTS
    for key, (mph, stored, _) in DEFAULT_PACES.items():
        assert defaults[key] == stored
        assert stored == round(mph * KMH_PER_MPH, 3)
        assert defaults[key] / KMH_PER_MPH == pytest.approx(mph, abs=0.0005 / KMH_PER_MPH)
    # Slowest movement first, and all of them below the cap.
    values = [defaults[key] for key in (*PACES, "maxKmh")]
    assert values == sorted(values) and len(set(values)) == len(values)
    assert defaults.standKmh >= defaults.minBeltKmh, "the belt can hold the standing pace"


def test_the_other_defaults(ns):
    defaults = ns.SpeedModel.DEFAULTS
    assert (defaults.minBeltKmh, defaults.stepKmh) == (1.0, 0.1)
    assert (defaults.riseSeconds, defaults.fallSeconds, defaults.stopHoldSeconds) == (4, 8, 4)
    assert defaults.taxiMode == "run"
    assert defaults.combatHold is False and defaults.keepWalking is False


@pytest.mark.parametrize("key", DEFAULT_PACES)
@pytest.mark.parametrize("grid", [{}, MPH_GRID], ids=["kmh-grid", "mph-grid"])
def test_default_paces_on_the_treadmills_grid(rt, grid, key):
    """What the belt is asked for with the default paces. On a 0.1 mph belt
    that is the whole tenth of a mph each default stands for - although it
    is stored a hair off it, and for the cap a hair *under* it (4.023 for
    4.02336): that must not cost a step and give 2.4 mph."""
    mph, stored, kmh_grid = DEFAULT_PACES[key]
    speed = {"standKmh": 0, "walkKmh": 2.5, "runKmh": 7.0, "mountKmh": 14.0, "maxKmh": 14.0}[key]
    # For the cap: a mounted pace far above it, so that the cap decides.
    settings = dict(grid, mountKmh=9.0) if key == "maxKmh" else grid
    model = Model(rt, **settings)
    outputs = model.run(120, speed=speed)
    expected = mph * KMH_PER_MPH if grid else kmh_grid
    assert outputs[-1] == pytest.approx(expected, abs=1e-6)
    assert expected == pytest.approx(nearest_step(stored, model.setting("stepKmh")), abs=1e-9)
    assert max(outputs) <= model.setting("maxKmh") + CAP_TOLERANCE + 1e-9


# ---- update: range, grid, smoothing ---------------------------------------------


def test_starts_at_the_standing_pace(rt):
    model = Model(rt)
    assert model.update(0.1, 0) == pytest.approx(model.pace("standKmh"))


def test_converges_on_the_running_pace_and_stays_there(rt):
    model = Model(rt)
    running = model.pace("runKmh")
    outputs = model.run(40, speed=7.0)
    assert outputs[-1] == pytest.approx(running)
    # Once there it does not flicker between steps.
    assert set(round(value, 6) for value in outputs[-100:]) == {round(running, 6)}
    # On the way up it never overshoots and never goes back down.
    assert outputs == sorted(outputs)
    assert max(outputs) <= running + 1e-9
    # It is a ramp through the steps in between, not a jump.
    assert len(set(round(value, 6) for value in outputs)) > 4


def test_rise_and_fall_time_constants(rt):
    model = Model(rt, stopHoldSeconds=0)
    stand, run = model.setting("standKmh"), model.setting("runKmh")
    assert (model.setting("riseSeconds"), model.setting("fallSeconds")) == (4, 8)
    model.run(4, speed=7.0, dt=0.01)        # one rise time constant
    assert model.smoothed == pytest.approx(stand + (run - stand) * (1 - math.exp(-1)), abs=0.01)
    model.run(120, speed=7.0)
    model.run(8, speed=0, dt=0.01)          # one fall time constant
    assert model.smoothed == pytest.approx(stand + (run - stand) * math.exp(-1), abs=0.01)


def test_slowing_down_is_gentler_than_speeding_up(rt):
    up = Model(rt, stopHoldSeconds=0)
    rising = up.run(60, speed=7.0)
    seconds_up = next(i for i, value in enumerate(rising) if value >= up.pace("runKmh") - 1e-9) / 10
    falling = up.run(60, speed=0)
    seconds_down = next(i for i, value in enumerate(falling) if value <= up.pace("standKmh") + 1e-9) / 10
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
    if rng.random() < 0.3:
        # Paces above the default cap, as when only the paces were raised.
        settings.update(runKmh=4.5, mountKmh=5.5)
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
    mounted = model.pace("mountKmh")
    assert model.update(0.1, 14.0) == pytest.approx(mounted)
    cap = 2.0
    assert cap < mounted - 1.0, "a cap well below the pace the belt is at"
    model.settings.maxKmh = cap             # the options panel edits the live table
    assert model.update(0.1, 14.0) <= cap + 1e-9
    after = model.run(30, speed=14.0)
    assert all(value <= cap + 1e-9 for value in after)
    assert after[-1] == pytest.approx(cap), "... and the belt stays at the cap, it does not stop"


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


@pytest.mark.parametrize("cap", [1.0, 1.04, 1.05, 2.449, 2.45, 3.0, 4.023, 4.8, 5.999, 6.0])
def test_cap_on_the_kmh_grid(rt, cap):
    model = Model(rt, maxKmh=cap, mountKmh=9.0)
    outputs = model.run(120, speed=14.0)
    expected = math.floor((cap + CAP_TOLERANCE) * 10 + 1e-9) / 10
    assert outputs[-1] == pytest.approx(expected, abs=1e-6)
    assert max(outputs) <= cap + CAP_TOLERANCE + 1e-9


def test_cap_that_is_off_the_grid_rounds_down(rt):
    # 4.8 km/h is 2.98 mph: the nearest 0.1 mph that does not exceed it is 2.9.
    model = Model(rt, maxKmh=4.8, mountKmh=6.0, **MPH_GRID)
    outputs = model.run(90, speed=14.0)
    assert outputs[-1] == pytest.approx(2.9 * KMH_PER_MPH)
    assert max(outputs) <= 4.8


def test_paces_above_the_default_cap_are_limited_to_it(rt):
    # The cap is a setting of its own: raising the paces on the options
    # page without raising "never faster than" changes nothing above it.
    mph, _, kmh_grid = DEFAULT_PACES["maxKmh"]
    for grid, cap in [({}, kmh_grid), (MPH_GRID, mph * KMH_PER_MPH)]:
        model = Model(rt, runKmh=4.5, mountKmh=5.5, **grid)
        for speed in (7.0, 14.0, 40.0):
            outputs = model.run(90, speed=speed)
            assert outputs[-1] == pytest.approx(cap, abs=1e-6)
            assert max(outputs) <= model.setting("maxKmh") + CAP_TOLERANCE + 1e-9


@pytest.mark.parametrize("grid", [{}, MPH_GRID], ids=["kmh-grid", "mph-grid"])
def test_a_cap_below_the_belts_minimum_means_no_belt_speed_at_all(rt, grid):
    # "Never faster than 0.8 km/h" on a belt that cannot go slower than
    # 1.0: the cap is the safety limit, so it wins.
    model = Model(rt, maxKmh=0.8, **grid)
    assert set(model.run(30, speed=7.0)) == {0}
    assert set(model.run(10, speed=0)) == {0}
    model.settings.maxKmh = 3.0             # raised again: back to normal
    step = model.setting("stepKmh")
    assert model.run(60, speed=7.0)[-1] == pytest.approx(model.pace("runKmh"))
    # ... and still the limit for a pace above it (the mounted one).
    assert model.setting("mountKmh") > 3.0 + step
    assert 3.0 - step < model.run(60, speed=14.0)[-1] <= 3.0 + 1e-9


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
    standing = model.pace("standKmh")
    assert model.model.update(model.model, 0.1, rt.lua.table()) == pytest.approx(standing)
    assert model.model.update(model.model, None, rt.lua.table()) == pytest.approx(standing)


def test_reset_forgets_history_and_can_seed_the_speed(rt):
    model = Model(rt)
    model.run(60, speed=7.0)
    model.model.reset(model.model, None)
    assert model.update(0.1, 0) == pytest.approx(model.pace("standKmh"))
    model.model.reset(model.model, 3.0)
    assert model.update(0.1, 7.0) == pytest.approx(3.0, abs=0.06)


def test_instant_response_when_time_constants_are_zero(rt):
    model = Model(rt, riseSeconds=0, fallSeconds=0, stopHoldSeconds=0)
    assert model.update(0.1, 7.0) == pytest.approx(model.pace("runKmh"))
    assert model.update(0.1, 14.0) == pytest.approx(model.pace("mountKmh"))
    assert model.update(0.1, 0) == pytest.approx(model.pace("standKmh"))


# ---- stop hold --------------------------------------------------------------------


def test_stop_hold_keeps_the_pace_through_a_two_second_stop(rt):
    model = Model(rt)
    running = model.pace("runKmh")
    model.run(40, speed=7.0)
    during = model.run(2, speed=0)          # looting
    assert during == [pytest.approx(running)] * 20
    after = model.run(3, speed=7.0)
    assert after == [pytest.approx(running)] * 30


def test_pace_falls_once_the_hold_runs_out(rt):
    model = Model(rt)
    running = model.pace("runKmh")
    model.run(40, speed=7.0)
    outputs = model.run(60, speed=0)
    assert outputs[35] == pytest.approx(running)            # still held at 3.5 s
    assert outputs[60] < running - 1e-9                     # falling by 6 s
    assert outputs[-1] == pytest.approx(model.pace("standKmh"))
    assert outputs == sorted(outputs, reverse=True)


def test_hold_length_is_a_setting(rt):
    model = Model(rt, stopHoldSeconds=0)
    model.run(40, speed=7.0)
    assert model.run(1.5, speed=0)[-1] < model.pace("runKmh") - 1e-9


def test_hold_remembers_the_pace_before_the_stop_not_a_higher_one(rt):
    model = Model(rt)
    walking = model.pace("walkKmh")
    assert model.pace("standKmh") < walking < model.pace("runKmh")
    model.run(60, speed=2.5)                # walking
    assert model.run(3, speed=0) == [pytest.approx(walking)] * 30


# ---- state rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, pace",
    [("run", "runKmh"), ("stand", "standKmh"), ("follow", "mountKmh")],
)
def test_taxi_modes(rt, mode, pace):
    model = Model(rt, taxiMode=mode, stopHoldSeconds=0)
    outputs = model.run(90, speed=32.0, taxi=True)      # a flight path is fast
    assert outputs[-1] == pytest.approx(model.pace(pace))


def test_taxi_default_is_a_running_pace(rt):
    model = Model(rt)
    assert model.run(60, speed=32.0, taxi=True)[-1] == pytest.approx(model.pace("runKmh"))


def test_falling_keeps_the_pace_whatever_the_speed_reads(rt):
    model = Model(rt, stopHoldSeconds=0)
    model.run(40, speed=7.0)
    # A long fall: the reported speed drops to 0 but the intent has not changed.
    assert model.run(10, speed=0, falling=True) == [pytest.approx(model.pace("runKmh"))] * 100
    # Jumping on the spot does not start the belt either.
    still = Model(rt, standKmh=0)
    still.run(30, speed=0)
    assert set(still.run(5, speed=0, falling=True)) == {0}


def test_combat_hold_keeps_at_least_the_running_pace(rt):
    held = Model(rt, combatHold=True, stopHoldSeconds=0)
    standing, running, mounted = held.pace("standKmh"), held.pace("runKmh"), held.pace("mountKmh")
    assert standing < running < mounted
    held.run(10, speed=7.0, combat=True)                                           # ran into a fight ...
    assert held.run(60, speed=0, combat=True)[-1] == pytest.approx(running)        # ... and stands in it
    assert held.run(60, speed=14.0, combat=True)[-1] == pytest.approx(mounted)     # faster is not slowed
    assert held.run(60, speed=0, combat=False)[-1] == pytest.approx(standing)      # only in combat
    default = Model(rt, stopHoldSeconds=0)
    assert default.run(60, speed=0, combat=True)[-1] == pytest.approx(standing)    # off by default


def test_combat_hold_never_speeds_up_a_character_nobody_is_steering(rt):
    """Something attacks a character that stands still: no key was pressed,
    so the belt must not speed up - and one that is stopped must not start."""
    idle = Model(rt, combatHold=True, stopHoldSeconds=0)
    standing = idle.pace("standKmh")
    idle.run(30, speed=0)
    assert idle.run(120, speed=0, combat=True)[-1] == pytest.approx(standing)
    stopped = Model(rt, combatHold=True, standKmh=0, stopHoldSeconds=0)
    stopped.run(30, speed=0)
    assert set(stopped.run(120, speed=0, combat=True)) == {0}
    # Once the character has moved in that fight, the hold applies ...
    stopped.run(5, speed=7.0, combat=True)
    assert stopped.run(60, speed=0, combat=True)[-1] == pytest.approx(stopped.pace("runKmh"))
    # ... and it ends with the fight: the next one starts from scratch.
    stopped.run(60, speed=0, combat=False)
    assert set(stopped.run(60, speed=0, combat=True)[-100:]) == {0}


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
    assert restart[-1] == pytest.approx(model.pace("runKmh"))


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
