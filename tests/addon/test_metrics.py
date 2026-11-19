"""Core/Metrics.lua: calories, cadence and the in-game estimate of a walk."""

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


# ---- the estimate of a walk ---------------------------------------------------------------
# Metrics.walk adds a stretch of walking at one pace to a table of totals.
# The pace is what the walker says the belt is set to: there is no model of
# a belt in the addon - no countdown, no ramp, nothing that follows a target.


def walk(rt, totals, seconds, kmh, dt=0.1, profile=None):
    profile = rt.to_lua({"weightKg": 75, "heightCm": 172} if profile is None else profile)
    for _ in range(int(round(seconds / dt))):
        rt.ns.Metrics.walk(totals, dt, kmh, profile)


def test_the_pace_walka_counts_with(ns):
    metrics = ns.Metrics
    assert metrics.DEFAULT_PACE_KMH == 2.414, "1.5 mph, where people settle at a treadmill desk"
    assert metrics.DEFAULT_PACE_KMH / KMH_PER_MPH == pytest.approx(1.5, abs=0.0005)
    assert (metrics.MIN_PACE_KMH, metrics.MAX_PACE_KMH) == (0.5, 10)
    assert metrics.Estimator is None, "the model of a belt that follows a target is gone"


def test_a_walk_counts_from_its_first_moment_at_the_pace_given(rt):
    totals = rt.lua.table()
    walk(rt, totals, 0.1, 3.0)
    first = lua_dict(totals)
    assert first["seconds"] == pytest.approx(0.1), "no countdown, no ramp"
    assert first["distanceM"] == pytest.approx(3.0 / 3.6 * 0.1)
    assert first["maxKmh"] == 3.0

    walk(rt, totals, 599.9, 3.0)
    totals = lua_dict(totals)
    metrics = rt.ns.Metrics
    assert totals["seconds"] == pytest.approx(600)
    assert totals["distanceM"] == pytest.approx(500)
    assert totals["steps"] == pytest.approx(10 * metrics.stepsPerMinute(3.0, 172))
    assert totals["kcal"] == pytest.approx(10 * metrics.kcalPerMinute(3.0, 75))
    assert totals["maxKmh"] == 3.0


def test_a_change_of_pace_counts_from_that_moment(rt):
    totals = rt.lua.table()
    walk(rt, totals, 60, 2.0)
    walk(rt, totals, 60, 5.0)
    walk(rt, totals, 60, 3.0)
    totals = lua_dict(totals)
    assert totals["seconds"] == pytest.approx(180)
    assert totals["distanceM"] == pytest.approx((2.0 + 5.0 + 3.0) / 3.6 * 60)
    assert totals["maxKmh"] == 5.0, "the fastest pace of the walk"


@pytest.mark.parametrize("kmh", [0, -3.0, None])
def test_nothing_is_counted_at_no_pace(rt, kmh):
    """A paused session is a pace of zero: no time, no distance."""
    totals = rt.lua.table()
    walk(rt, totals, 60, 3.0)
    before = lua_dict(totals)
    walk(rt, totals, 60, kmh)
    assert lua_dict(totals) == before


def test_the_profile_decides_steps_and_calories(rt):
    metrics = rt.ns.Metrics
    small, tall = rt.lua.table(), rt.lua.table()
    walk(rt, small, 60, 3.0, profile={"weightKg": 55, "heightCm": 158})
    walk(rt, tall, 60, 3.0, profile={"weightKg": 95, "heightCm": 192, "stepCalibration": 1.2})
    assert small.distanceM == pytest.approx(tall.distanceM)
    assert small.steps == pytest.approx(metrics.stepsPerMinute(3.0, 158))
    assert tall.steps == pytest.approx(metrics.stepsPerMinute(3.0, 192, 1.2))
    assert small.kcal == pytest.approx(metrics.kcalPerMinute(3.0, 55))
    assert tall.kcal == pytest.approx(metrics.kcalPerMinute(3.0, 95))


def test_walk_tolerates_missing_arguments(rt):
    metrics = rt.ns.Metrics
    totals = rt.lua.table()
    metrics.walk(totals, None, 3.0)
    metrics.walk(totals, -1, 3.0)
    metrics.walk(totals, 0, 3.0)
    assert lua_dict(totals) == {}, "no time, nothing counted - not even a top speed"
    for _ in range(100):
        metrics.walk(totals, 0.1, 3.0)                  # default profile, empty totals
    assert totals.seconds == pytest.approx(10)
    assert totals.steps == pytest.approx(metrics.stepsPerMinute(3.0, 172) / 6)
    assert totals.kcal == pytest.approx(metrics.kcalPerMinute(3.0, 75) / 6)
