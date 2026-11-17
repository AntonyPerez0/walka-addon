"""Core/Metrics.lua: calories, cadence and the in-game belt estimate."""

from __future__ import annotations

import pytest

from .harness import lua_dict

KMH_PER_MPH = 1.609344
SPEEDS = [step / 10 for step in range(1, 141)]      # 0.1 .. 14.0 km/h


# ---- calories -------------------------------------------------------------------


def test_calories_rise_with_speed(ns):
    values = [ns.Metrics.kcalPerMinute(kmh, 75, 172) for kmh in SPEEDS]
    assert all(later > earlier for earlier, later in zip(values, values[1:], strict=False))


def test_calorie_magnitudes_are_sensible(ns):
    kcal = ns.Metrics.kcalPerMinute
    # A 75 kg adult strolling at 3 km/h burns very roughly 3 kcal a minute.
    assert 2.0 <= kcal(3.0, 75, 172) <= 4.0
    # Desk-walking pace for an hour: 150-250 kcal.
    assert 150 <= kcal(2.0 * KMH_PER_MPH, 75, 172) * 60 <= 250
    # A brisk 6 km/h walk: 4-7 kcal a minute.
    assert 4.0 <= kcal(6.0, 75, 172) <= 7.0
    # Running at 10 km/h: 10-15.
    assert 10.0 <= kcal(10.0, 75, 172) <= 15.0


def test_standing_still_counts_nothing(ns):
    assert ns.Metrics.kcalPerMinute(0, 75, 172) == 0
    assert ns.Metrics.kcalPerMinute(-1, 75, 172) == 0
    assert ns.Metrics.kcalPerMinute(None, 75, 172) == 0


def test_calories_scale_with_weight_and_shrink_with_height(ns):
    kcal = ns.Metrics.kcalPerMinute
    assert kcal(3.0, 150, 172) == pytest.approx(2 * kcal(3.0, 75, 172))
    assert kcal(3.0, 75, 160) > kcal(3.0, 75, 190)


def test_net_calories_leave_out_resting_metabolism(ns):
    kcal = ns.Metrics.kcalPerMinute
    for kmh in (1.0, 3.0, 6.0, 10.0):
        gross, net = kcal(kmh, 75, 172), kcal(kmh, 75, 172, True)
        assert 0 < net < gross
        # Resting: 3.5 ml O2/kg/min at 5 kcal per litre.
        assert gross - net == pytest.approx(3.5 * 75 / 1000 * 5)


def test_profile_defaults(ns):
    kcal = ns.Metrics.kcalPerMinute
    assert kcal(3.0) == pytest.approx(kcal(3.0, 75, 172))
    assert ns.Metrics.DEFAULT_PROFILE.weightKg == 75
    assert ns.Metrics.DEFAULT_PROFILE.heightCm == 172


# ---- cadence ----------------------------------------------------------------------


def test_cadence_rises_with_speed(ns):
    values = [ns.Metrics.stepsPerMinute(kmh, 172) for kmh in SPEEDS]
    assert all(later > earlier for earlier, later in zip(values, values[1:], strict=False))


def test_cadence_anchors_and_magnitudes(ns):
    steps = ns.Metrics.stepsPerMinute
    # The anchors named in the source: 93 steps/min at 2 mph, 125 at 4 mph (1.70 m).
    assert steps(2 * KMH_PER_MPH, 170) == pytest.approx(93, abs=1.5)
    assert steps(4 * KMH_PER_MPH, 170) == pytest.approx(125, abs=1.5)
    assert 60 <= steps(1.6, 172) <= 85
    assert 80 <= steps(3.0, 172) <= 100
    assert 150 <= steps(10.0, 172) <= 175
    assert steps(0, 172) == 0
    assert steps(None, 172) == 0


def test_shorter_people_take_more_steps(ns):
    steps = ns.Metrics.stepsPerMinute
    assert steps(3.0, 155) > steps(3.0, 172) > steps(3.0, 195)
    assert steps(10.0, 155) > steps(10.0, 195)
    assert steps(3.0) == pytest.approx(steps(3.0, 172))


@pytest.mark.parametrize("kmh", [1.0, 2.0, 3.2, 4.8, 6.4])
def test_implied_stride_is_human(ns, kmh):
    stride_m = (kmh * 1000 / 60) / ns.Metrics.stepsPerMinute(kmh, 172)
    assert 0.20 <= stride_m <= 0.95


# ---- the belt estimator ---------------------------------------------------------------


class Belt:
    def __init__(self, rt, **limits):
        self.rt = rt
        self.limits = rt.to_lua(limits)
        self.estimator = rt.ns.Metrics.Estimator.new(self.limits)
        self.totals = rt.to_lua({"seconds": 0, "distanceM": 0, "steps": 0, "kcal": 0})
        self.profile = rt.to_lua({"weightKg": 75, "heightCm": 172})

    def run(self, seconds, target, dt=0.1):
        speeds = []
        for _ in range(int(round(seconds / dt))):
            speeds.append(self.estimator.update(self.estimator, dt, target, self.totals, self.profile))
        return speeds


def test_countdown_then_minimum_then_ramp(rt):
    belt = Belt(rt)
    speeds = belt.run(12, 3.2)
    # Three seconds of countdown with the belt still ...
    assert set(speeds[:29]) == {0}
    started = next(index for index, value in enumerate(speeds) if value > 0)
    assert 29 <= started <= 31
    # ... then it appears at its minimum speed ...
    assert speeds[started] == pytest.approx(1.0)
    # ... and speeds up at 0.5 km/h per second to the target.
    assert speeds[started + 10] == pytest.approx(1.5, abs=0.06)
    assert speeds[started + 20] == pytest.approx(2.0, abs=0.06)
    assert speeds[-1] == pytest.approx(3.2)
    assert max(speeds) <= 3.2 + 1e-9
    assert speeds == sorted(speeds)


def test_slows_at_the_deceleration_limit_and_coasts_to_a_stop(rt):
    belt = Belt(rt)
    belt.run(20, 4.0)
    slowing = belt.run(1, 2.0)
    assert slowing[-1] == pytest.approx(3.0, abs=0.01)          # 1 km/h per second
    belt.run(5, 2.0)
    stopping = belt.run(2, 0)
    assert stopping[3] == pytest.approx(2.0 - 2.5 * 0.4, abs=0.01)   # 2.5 km/h per second
    assert stopping[-1] == 0
    assert min(stopping) >= 0


def test_a_target_below_the_belt_minimum_starts_at_the_target(rt):
    belt = Belt(rt)
    assert belt.run(5, 0.6)[-1] == pytest.approx(0.6)


def test_stopping_during_the_countdown_restarts_it(rt):
    belt = Belt(rt)
    belt.run(2, 3.0)            # two of the three seconds
    belt.run(0.5, 0)            # paused
    speeds = belt.run(4, 3.0)
    assert set(speeds[:29]) == {0}
    assert speeds[-1] > 0


def test_limits_can_be_overridden_and_are_read_live(rt):
    belt = Belt(rt, countdownSeconds=0, accelKmhPerS=2.0, minBeltKmh=0.6)
    speeds = belt.run(2, 3.0)
    assert speeds[0] == pytest.approx(0.6)
    assert speeds[10] == pytest.approx(0.6 + 2.0 * 1.0, abs=0.01)
    belt.limits.decelKmhPerS = 3.0
    assert belt.run(0.5, 1.0)[-1] == pytest.approx(3.0 - 3.0 * 0.5, abs=0.01)


def test_totals_accumulate_only_while_the_belt_moves(rt):
    belt = Belt(rt)
    belt.run(3, 3.0)
    assert lua_dict(belt.totals)["seconds"] <= 0.2      # the countdown does not count
    belt.run(600, 3.0)
    totals = lua_dict(belt.totals)
    assert totals["seconds"] == pytest.approx(600, abs=1)
    # Ten minutes at 3 km/h (less a little for the ramp).
    assert totals["distanceM"] == pytest.approx(500, rel=0.02)
    assert totals["steps"] == pytest.approx(10 * rt.ns.Metrics.stepsPerMinute(3.0, 172), rel=0.02)
    assert totals["kcal"] == pytest.approx(10 * rt.ns.Metrics.kcalPerMinute(3.0, 75, 172), rel=0.02)
    assert totals["maxKmh"] == pytest.approx(3.0)

    before = lua_dict(belt.totals)
    belt.run(60, 0)                                     # stopped: coasts for a second, then nothing
    after = lua_dict(belt.totals)
    assert after["seconds"] - before["seconds"] <= 1.5
    assert after["distanceM"] - before["distanceM"] <= 1.0
    assert after["maxKmh"] == before["maxKmh"]


def test_estimator_tolerates_missing_arguments(rt):
    estimator = rt.ns.Metrics.Estimator.new()
    assert estimator.update(estimator, 0.1, None, None, None) == 0
    assert estimator.update(estimator, None, 3.0) == 0
    assert estimator.update(estimator, -1, 3.0) == 0
    totals = rt.lua.table()
    for _ in range(100):
        estimator.update(estimator, 0.1, 3.0, totals)   # default profile, empty totals
    assert totals.seconds > 0 and totals.steps > 0 and totals.kcal > 0


def test_reset(rt):
    belt = Belt(rt)
    belt.run(20, 3.0)
    belt.estimator.reset(belt.estimator, 0)
    assert belt.estimator.beltKmh == 0
    assert belt.run(1, 3.0)[-1] == 0                    # a new countdown
    belt.estimator.reset(belt.estimator, 2.5)           # e.g. after /reload
    assert belt.run(0.1, 3.0)[-1] == pytest.approx(2.55)
