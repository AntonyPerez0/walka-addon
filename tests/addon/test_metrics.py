"""Core/Metrics.lua: calories, cadence and the in-game belt estimate."""

from __future__ import annotations

import pytest

from .harness import lua_dict

KMH_PER_MPH = 1.609344
SPEEDS = [step / 10 for step in range(1, 141)]      # 0.1 .. 14.0 km/h


# ---- calories -------------------------------------------------------------------


def test_calories_rise_with_speed(ns):
    values = [ns.Metrics.kcalPerMinute(kmh, 75) for kmh in SPEEDS]
    assert all(later > earlier for earlier, later in zip(values, values[1:], strict=False))


def test_calorie_magnitudes_are_sensible(ns):
    kcal = ns.Metrics.kcalPerMinute
    # 2024 Compendium of Physical Activities, treadmill desk at 1-2 mph:
    # 2.8 MET, i.e. about 210 kcal an hour for 75 kg.
    assert 180 <= kcal(2.0 * KMH_PER_MPH, 75) * 60 <= 240
    # The equation's authors: about 217 kcal an hour for 80 kg at 2.5 km/h.
    assert kcal(2.5, 80) * 60 == pytest.approx(217, abs=2)
    # A brisk 6 km/h walk: 5-7 kcal a minute.
    assert 5.0 <= kcal(6.0, 75) <= 7.0
    # Running at 10 km/h: 10-15.
    assert 10.0 <= kcal(10.0, 75) <= 15.0


def test_calories_match_measured_oxygen_uptake_where_the_textbook_equation_does_not(ns):
    """Measured on treadmills (CADENCE-Adults, n=76): ml O2 per kg per minute
    at 1.0, 1.5, 2.0 and 2.5 mph. The ACSM walking equation reads 11-35% low
    there; this one stays within 8%."""
    for mph, vo2 in ((1.0, 8.3), (1.5, 9.2), (2.0, 10.2), (2.5, 11.8)):
        measured = vo2 * 20.9 / 60  # watts per kg
        assert ns.Metrics.wattsPerKg(mph * KMH_PER_MPH) == pytest.approx(measured, rel=0.08)


def test_standing_still_counts_nothing(ns):
    assert ns.Metrics.kcalPerMinute(0, 75) == 0
    assert ns.Metrics.kcalPerMinute(-1, 75) == 0
    assert ns.Metrics.kcalPerMinute(None, 75) == 0
    assert ns.Metrics.wattsPerKg(0) == 0 and ns.Metrics.wattsPerKg(None) == 0


def test_calories_scale_with_weight(ns):
    kcal = ns.Metrics.kcalPerMinute
    assert kcal(3.0, 150) == pytest.approx(2 * kcal(3.0, 75))


def test_net_calories_leave_out_what_sitting_would_have_burned(ns):
    kcal = ns.Metrics.kcalPerMinute
    for kmh in (1.0, 3.0, 6.0, 10.0):
        gross, net = kcal(kmh, 75), kcal(kmh, 75, True)
        assert 0 < net < gross
        # Sitting at a desk: 1.3 kcal per kg per hour.
        assert gross - net == pytest.approx(1.3 * 75 / 60)


def test_profile_defaults(ns):
    kcal = ns.Metrics.kcalPerMinute
    assert kcal(3.0) == pytest.approx(kcal(3.0, 75))
    assert ns.Metrics.DEFAULT_PROFILE.weightKg == 75
    assert ns.Metrics.DEFAULT_PROFILE.heightCm == 172


# ---- cadence ----------------------------------------------------------------------


def test_cadence_rises_with_speed(ns):
    values = [ns.Metrics.stepsPerMinute(kmh, 172) for kmh in SPEEDS]
    assert all(later > earlier for earlier, later in zip(values, values[1:], strict=False))


def test_cadence_matches_hand_counted_steps(ns):
    steps = ns.Metrics.stepsPerMinute
    # Hand-counted on treadmills (CADENCE-Adults, 170.7 cm): steps a minute
    # at 1, 2, 3 and 4 mph.
    for mph, counted in ((1.0, 67.8), (2.0, 96.1), (3.0, 113.6), (4.0, 129.0)):
        assert steps(mph * KMH_PER_MPH, 170.7) == pytest.approx(counted, abs=3.2)
    assert 150 <= steps(10.0, 172) <= 175
    assert steps(0, 172) == 0
    assert steps(None, 172) == 0


def test_step_calibration(ns):
    steps = ns.Metrics.stepsPerMinute
    base = steps(3.0, 172)
    assert steps(3.0, 172, 1.2) == pytest.approx(base * 1.2)
    assert steps(3.0, 172, None) == pytest.approx(base)
    # Clamped to 0.7-1.5.
    assert steps(3.0, 172, 9) == pytest.approx(base * 1.5)
    assert steps(3.0, 172, 0.1) == pytest.approx(base * 0.7)


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


def default_rates(rt):
    """The companion's default rate limits (km/h per second up, down) - the
    estimator's defaults follow them, see Estimator.DEFAULTS."""
    defaults = rt.ns.Metrics.Estimator.DEFAULTS
    return defaults.accelKmhPerS, defaults.decelKmhPerS


def test_countdown_then_minimum_then_ramp(rt):
    belt = Belt(rt)
    accel, _ = default_rates(rt)
    speeds = belt.run(3 + (3.2 - 1.0) / accel + 2, 3.2)
    # Three seconds of countdown with the belt still ...
    assert set(speeds[:29]) == {0}
    started = next(index for index, value in enumerate(speeds) if value > 0)
    assert 29 <= started <= 31
    # ... then it appears at its minimum speed ...
    assert speeds[started] == pytest.approx(1.0)
    # ... and speeds up at the companion's rate limit to the target.
    assert speeds[started + 10] == pytest.approx(1.0 + accel * 1.0, abs=0.06)
    assert speeds[started + 20] == pytest.approx(1.0 + accel * 2.0, abs=0.06)
    assert speeds[-1] == pytest.approx(3.2)
    assert max(speeds) <= 3.2 + 1e-9
    assert speeds == sorted(speeds)


def test_slows_at_the_deceleration_limit_and_coasts_to_a_stop(rt):
    belt = Belt(rt)
    accel, decel = default_rates(rt)
    belt.run(3 + (4.0 - 1.0) / accel + 2, 4.0)
    slowing = belt.run(1, 2.0)
    assert slowing[-1] == pytest.approx(4.0 - decel * 1.0, abs=0.01)
    belt.run(2.0 / decel, 2.0)
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
    assert totals["kcal"] == pytest.approx(10 * rt.ns.Metrics.kcalPerMinute(3.0, 75), rel=0.02)
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
    assert belt.run(0.1, 3.0)[-1] == pytest.approx(2.5 + default_rates(rt)[0] * 0.1)
