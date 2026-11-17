"""Calories and cadence: sanity, and agreement between the companion (Python)
and the addon (Lua) pinned by tests/shared/metrics_vectors.json."""

from __future__ import annotations

import itertools
import json
import math

import pytest

import gen_metrics_vectors
from walka import metrics
from walka.metrics import KMH_PER_MPH, Profile
from walka.session import SessionRecorder
from walka_fakes import load_addon_file

VECTORS_PATH = gen_metrics_vectors.VECTORS_PATH
REL = 1e-9


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=REL, abs_tol=1e-12)


@pytest.fixture(scope="module")
def vectors() -> list[dict]:
    data = json.loads(VECTORS_PATH.read_text("utf-8"))
    assert data["tolerance_rel"] == REL
    return data["vectors"]


@pytest.fixture
def lua_metrics(lua, addon_source):
    return load_addon_file(lua, addon_source / "Core" / "Metrics.lua").Metrics


# ---- sanity -------------------------------------------------------------------------


def test_constants():
    assert KMH_PER_MPH == 1.609344
    assert metrics.METERS_PER_MILE == 1609.344 and metrics.METERS_PER_YARD == 0.9144
    assert metrics.HR_FLOOR_BPM == 90
    assert Profile().weight_kg == 75.0 and Profile().height_cm == 172.0 and Profile().height_m == 1.72
    assert Profile().age is None and Profile().sex is None


def test_standing_still_burns_nothing_extra_and_takes_no_steps():
    profile = Profile()
    for kmh in (0.0, -1.0, -100.0):
        assert metrics.kcal_per_minute(kmh, profile) == 0.0
        assert metrics.kcal_per_minute(kmh, profile, net=True) == 0.0
        assert metrics.steps_per_minute(kmh, 172) == 0.0
        assert metrics.vo2(kmh, 1.72) == metrics.VO2_REST


def test_walking_numbers_have_the_right_magnitude():
    """Published ballparks for a 75 kg adult: 2 mph is ~2.5-3 MET, 3 mph ~3.3-3.8, 4 mph ~5."""
    profile = Profile()
    for mph, low_met, high_met in ((1.0, 1.8, 2.6), (2.0, 2.4, 3.2), (3.0, 3.2, 4.2), (4.0, 4.4, 6.0)):
        met = metrics.vo2(mph * KMH_PER_MPH, profile.height_m) / metrics.VO2_REST
        assert low_met < met < high_met, (mph, met)
    hour = metrics.kcal_per_minute(3.0 * KMH_PER_MPH, profile) * 60
    assert 230 < hour < 340
    assert 4500 < metrics.steps_per_minute(2.0 * KMH_PER_MPH, 172) * 60 < 6500
    # The cadence anchors quoted in the source: 93 steps/min at 2 mph, 125 at 4 mph (1.70 m).
    assert metrics.steps_per_minute(2.0 * KMH_PER_MPH, 170) == pytest.approx(93, abs=0.5)
    assert metrics.steps_per_minute(4.0 * KMH_PER_MPH, 170) == pytest.approx(125, abs=0.5)
    # Stride length implied at a normal pace is human (0.5-0.9 m).
    for kmh in (2.0, 3.2, 4.8, 6.4):
        stride = kmh * 1000 / 60 / metrics.steps_per_minute(kmh, 172)
        assert 0.3 < stride < 0.95, (kmh, stride)


def test_monotonic_in_speed_within_walking_and_within_running():
    profile = Profile()
    for lo, hi in ((0.1, 7.9), (8.0, 20.0)):
        speeds = [lo + (hi - lo) * i / 200 for i in range(201)]
        kcal = [metrics.kcal_per_minute(v, profile) for v in speeds]
        net = [metrics.kcal_per_minute(v, profile, net=True) for v in speeds]
        steps = [metrics.steps_per_minute(v, 172) for v in speeds]
        for series in (kcal, net, steps):
            assert all(b > a for a, b in itertools.pairwise(series))
    # Switching to the running equations never drops the estimate.
    assert metrics.kcal_per_minute(8.0, profile) > metrics.kcal_per_minute(7.999, profile)
    assert metrics.steps_per_minute(8.0, 172) > metrics.steps_per_minute(7.999, 172)


def test_weight_and_height_effects():
    for kmh in (2.0, 4.8, 10.0):
        light, heavy = Profile(weight_kg=50), Profile(weight_kg=100)
        assert metrics.kcal_per_minute(kmh, heavy) == pytest.approx(2 * metrics.kcal_per_minute(kmh, light))
        assert (
            metrics.steps_per_minute(kmh, 150) > metrics.steps_per_minute(kmh, 172) > metrics.steps_per_minute(kmh, 195)
        )
    # Walking: shorter people work harder at the same speed (Ludlow & Weyand).
    assert metrics.kcal_per_minute(4.8, Profile(height_cm=150)) > metrics.kcal_per_minute(4.8, Profile(height_cm=195))


def test_net_is_gross_minus_resting():
    for kmh in (1.0, 3.2, 6.4, 9.0):
        for profile in (Profile(), Profile(weight_kg=95, height_cm=188)):
            gross = metrics.kcal_per_minute(kmh, profile)
            net = metrics.kcal_per_minute(kmh, profile, net=True)
            resting = metrics.VO2_REST * profile.weight_kg / 1000 * metrics.KCAL_PER_LITRE_O2
            assert 0 < net < gross
            assert gross - net == pytest.approx(resting)


def test_heart_rate_calories():
    man, woman = Profile(weight_kg=80, age=40, sex="male"), Profile(weight_kg=65, age=30, sex="female")
    # Keytel et al. 2005, worked by hand.
    assert metrics.kcal_per_minute_from_hr(120, man) == pytest.approx(
        (-55.0969 + 0.6309 * 120 + 0.1988 * 80 + 0.2017 * 40) / 4.184
    )
    assert metrics.kcal_per_minute_from_hr(120, man) == pytest.approx(10.656, abs=0.001)
    assert metrics.kcal_per_minute_from_hr(120, woman) == pytest.approx(
        (-20.4022 + 0.4472 * 120 - 0.1263 * 65 + 0.074 * 30) / 4.184
    )
    assert metrics.kcal_per_minute_from_hr(120, woman) == pytest.approx(6.518, abs=0.001)
    for profile in (man, woman):
        rates = [metrics.kcal_per_minute_from_hr(bpm, profile) for bpm in range(60, 200, 5)]
        assert all(b >= a for a, b in itertools.pairwise(rates))
        assert metrics.kcal_per_minute_from_hr(30, profile) == 0.0  # clamped, never negative
    for incomplete in (Profile(), Profile(age=40), Profile(sex="male"), Profile(age=40, sex="x")):
        assert metrics.kcal_per_minute_from_hr(120, incomplete) is None
    assert metrics.kcal_per_minute_from_hr(0, man) is None
    assert metrics.kcal_per_minute_from_hr(-5, man) is None


# ---- the shared vectors ----------------------------------------------------------------


def test_vector_file_is_in_its_canonical_form(vectors):
    """Stable formatting: regenerating must not reshuffle the file."""
    text = VECTORS_PATH.read_text("utf-8")
    data = json.loads(text)
    assert gen_metrics_vectors.render(data) == text
    assert "\r" not in text and text.endswith("}\n")


def test_vector_file_covers_what_the_generator_describes(vectors):
    fresh = gen_metrics_vectors.build()
    assert [(v["kmh"], v["weight_kg"], v["height_cm"]) for v in vectors] == [
        (v["kmh"], v["weight_kg"], v["height_cm"]) for v in fresh["vectors"]
    ]
    speeds = {v["kmh"] for v in vectors}
    assert 0.0 in speeds and min(s for s in speeds if s > 0) <= 0.5 and max(speeds) >= 12
    assert {7.99, 8.0, 8.01} <= speeds  # both sides of the walk/run switch
    assert len(vectors) >= 150


def test_python_reproduces_every_vector(vectors):
    for v in vectors:
        profile = Profile(weight_kg=v["weight_kg"], height_cm=v["height_cm"])
        assert close(metrics.vo2(v["kmh"], v["height_cm"] / 100.0), v["vo2"]), v
        assert close(metrics.kcal_per_minute(v["kmh"], profile), v["kcal_per_min"]), v
        assert close(metrics.kcal_per_minute(v["kmh"], profile, net=True), v["kcal_per_min_net"]), v
        assert close(metrics.steps_per_minute(v["kmh"], v["height_cm"]), v["steps_per_min"]), v


def test_lua_reproduces_every_vector(vectors, lua_metrics):
    for v in vectors:
        kmh, weight, height = v["kmh"], v["weight_kg"], v["height_cm"]
        assert close(lua_metrics.vo2(kmh, height / 100.0), v["vo2"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight, height), v["kcal_per_min"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight, height, False), v["kcal_per_min"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight, height, True), v["kcal_per_min_net"]), v
        assert close(lua_metrics.stepsPerMinute(kmh, height), v["steps_per_min"]), v


def test_lua_and_python_share_constants_and_defaults(lua_metrics):
    assert lua_metrics.KMH_PER_MPH == metrics.KMH_PER_MPH
    assert lua_metrics.METERS_PER_MILE == metrics.METERS_PER_MILE
    assert lua_metrics.METERS_PER_YARD == metrics.METERS_PER_YARD
    assert lua_metrics.RUN_THRESHOLD_KMH == metrics.RUN_THRESHOLD_KMH
    assert lua_metrics.DEFAULT_PROFILE.weightKg == Profile().weight_kg
    assert lua_metrics.DEFAULT_PROFILE.heightCm == Profile().height_cm
    # With no profile given the addon assumes the same default body.
    assert close(lua_metrics.kcalPerMinute(4.0), metrics.kcal_per_minute(4.0, Profile()))
    assert close(lua_metrics.stepsPerMinute(4.0), metrics.steps_per_minute(4.0, Profile().height_cm))
    assert lua_metrics.kcalPerMinute(None) == 0 and lua_metrics.stepsPerMinute(None) == 0


# ---- the addon's live estimate against the companion's recorder ----------------------------


def test_lua_estimator_accumulates_like_the_python_recorder(lua, lua_metrics):
    """Feed the addon's belt estimator a scripted target profile, then give
    the belt speeds it produced to the companion's recorder: the in-game
    estimate and the companion's speed-based numbers must be the same sums."""
    profile = Profile(weight_kg=82.0, height_cm=178.0)
    lua_profile = lua.table(weightKg=profile.weight_kg, heightCm=profile.height_cm)
    estimator = lua_metrics.Estimator.new(lua.table(minBeltKmh=0.6 * KMH_PER_MPH))
    totals = lua.table()
    recorder = SessionRecorder(source="wow", beacon_session=1, treadmill="", started_at=1_000_000.0)

    dt = 0.25  # exactly representable: both sides add identical floats
    script = [  # (seconds, target km/h)
        (30, 3.2),  # start: countdown, minimum speed, ramp up
        (60, 4.8),
        (20, 1.6),  # slow down
        (15, 0.0),  # stand speed 0: belt stops
        (45, 3.2),  # and starts again (another countdown)
        (120, 6.4),
        (10, 0.0),
    ]
    now = recorder.started_at
    belts = []
    for seconds, target in script:
        for _ in range(int(seconds / dt)):
            belt = estimator.update(estimator, dt, target, totals, lua_profile)
            belts.append(belt)
            now += dt
            recorder.update(now, speed_kmh=belt, profile=profile)

    assert max(belts) == pytest.approx(6.4)
    assert belts[0] == 0 and belts[int(3 / dt) - 2] == 0  # the 3 s countdown
    assert belts[int(3 / dt) - 1] == pytest.approx(0.6 * KMH_PER_MPH)  # then the belt appears at its minimum
    assert not any(0 < b <= 0.05 for b in belts)  # (the two sides only differ on a creeping belt)

    assert totals.seconds == pytest.approx(recorder.seconds, rel=1e-9)
    assert totals.distanceM == pytest.approx(recorder.distance_speed_m, rel=1e-9)
    assert totals.steps == pytest.approx(recorder.steps_estimate, rel=1e-9)
    assert totals.kcal == pytest.approx(recorder.kcal_speed, rel=1e-9)
    assert totals.maxKmh == pytest.approx(recorder.max_kmh, rel=1e-12)
    assert 250 < recorder.seconds < 300 and 300 < recorder.distance_speed_m < 420


def test_lua_estimator_ramps_with_the_companions_default_limits(lua, lua_metrics):
    """The addon replays the governor's rate limits; its defaults must be the
    governor's defaults or the in-game numbers drift from the real ones."""
    from walka.governor import Limits

    defaults = lua_metrics.Estimator.DEFAULTS
    assert defaults.accelKmhPerS == Limits().accel_kmh_per_s
    assert defaults.decelKmhPerS == Limits().decel_kmh_per_s
    assert defaults.countdownSeconds == 3
