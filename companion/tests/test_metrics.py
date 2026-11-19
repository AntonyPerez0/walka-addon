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
    assert metrics.JOULES_PER_KCAL == 4184.0
    assert metrics.ENERGY_BLEND_MS == (2.0, 2.2) and metrics.CADENCE_BLEND_MS == (1.9, 2.2)
    assert metrics.STEP_CALIBRATION_RANGE == (0.7, 1.5)
    assert Profile().weight_kg == 75.0 and Profile().height_cm == 172.0 and Profile().height_m == 1.72
    assert Profile().age is None and Profile().sex is None and Profile().step_calibration == 1.0


def test_standing_still_burns_nothing_extra_and_takes_no_steps():
    profile = Profile()
    for kmh in (0.0, -1.0, -100.0):
        assert metrics.kcal_per_minute(kmh, profile) == 0.0
        assert metrics.kcal_per_minute(kmh, profile, net=True) == 0.0
        assert metrics.steps_per_minute(kmh, 172) == 0.0
        assert metrics.watts_per_kg(kmh) == 0.0


# Measured on treadmills (CADENCE-Adults, ages 21-40, n=76, mean height 170.7 cm):
# speed in mph -> (oxygen uptake ml/kg/min, hand-counted steps/min).
MEASURED = {
    0.5: (7.4, 45.4),
    1.0: (8.3, 67.8),
    1.5: (9.2, 83.8),
    2.0: (10.2, 96.1),
    2.5: (11.8, 105.8),
    3.0: (14.2, 113.6),
    3.5: (17.3, 121.5),
    4.0: (21.5, 129.0),
}
J_PER_ML_O2 = 20.9


def test_calories_match_measured_oxygen_uptake_at_desk_walking_speeds():
    """The reason for the LCDA equation: within a few percent of measured
    values where the textbook (ACSM) walking equation reads 11-35% low."""
    for mph, (vo2, _cadence) in MEASURED.items():
        measured = vo2 * J_PER_ML_O2 / 60.0  # watts per kg
        ours = metrics.watts_per_kg(mph * KMH_PER_MPH)
        tolerance = 0.08 if mph <= 2.5 else 0.17  # it under-reads somewhat at brisk speeds
        assert ours == pytest.approx(measured, rel=tolerance), (mph, ours, measured)
        acsm = (0.1 * mph * 26.82 + 3.5) * J_PER_ML_O2 / 60.0
        if mph <= 2.5:
            assert abs(ours - measured) < abs(acsm - measured), mph


def test_calories_agree_with_the_compendium_and_published_totals():
    """2024 Compendium of Physical Activities, treadmill desk: 2.0-2.8 MET
    (1 MET = 1 kcal/kg/h). And the worked figure from the paper's authors:
    about 217 kcal an hour for 80 kg at 2.5 km/h."""
    kcal_per_kg_hour = lambda kmh: metrics.watts_per_kg(kmh) * 3600 / metrics.JOULES_PER_KCAL  # noqa: E731
    assert 2.0 <= kcal_per_kg_hour(1.0 * KMH_PER_MPH) <= 2.8
    assert 2.4 <= kcal_per_kg_hour(2.0 * KMH_PER_MPH) <= 3.2
    assert kcal_per_kg_hour(1.0) == pytest.approx(2.20, abs=0.01)
    assert kcal_per_kg_hour(3.0) == pytest.approx(2.88, abs=0.01)
    assert kcal_per_kg_hour(5.0) == pytest.approx(3.93, abs=0.01)
    assert metrics.kcal_per_minute(2.5, Profile(weight_kg=80)) * 60 == pytest.approx(217, abs=2)


def test_cadence_matches_hand_counted_steps():
    for mph, (_vo2, cadence) in MEASURED.items():
        ours = metrics.steps_per_minute(mph * KMH_PER_MPH, 170.7)
        assert ours == pytest.approx(cadence, abs=3.2 if mph >= 1.0 else 4.5), (mph, ours, cadence)
    # Hoeger et al. 2008: about 2252 steps per mile at 3 mph, 1935 at 4 mph (1.75 m).
    assert metrics.steps_per_minute(3.0 * KMH_PER_MPH, 175) * 20 == pytest.approx(2252, rel=0.02)
    assert metrics.steps_per_minute(4.0 * KMH_PER_MPH, 175) * 15 == pytest.approx(1935, rel=0.02)
    # Step length grows with speed: short shuffles at 1 km/h, a normal stride at 5.
    step_m = lambda kmh: kmh / 3.6 * 60 / metrics.steps_per_minute(kmh, 175)  # noqa: E731
    assert step_m(1.0) == pytest.approx(0.31, abs=0.02)
    assert step_m(5.0) == pytest.approx(0.72, abs=0.02)
    # Running (Hoeger): about 155-160 steps a minute at 7-10 km/h for 1.75 m.
    assert 150 < metrics.steps_per_minute(8.0, 175) < 162
    assert 155 < metrics.steps_per_minute(10.0, 175) < 165


def test_monotonic_in_speed_including_across_the_walk_run_blend():
    profile = Profile()
    speeds = [0.1 + (20.0 - 0.1) * i / 2000 for i in range(2001)]
    for height in (150, 172, 195):
        kcal = [metrics.kcal_per_minute(v, profile) for v in speeds]
        net = [metrics.kcal_per_minute(v, profile, net=True) for v in speeds]
        steps = [metrics.steps_per_minute(v, height) for v in speeds]
        assert all(b > a for a, b in itertools.pairwise(kcal))
        assert all(b >= a for a, b in itertools.pairwise(net))
        assert all(b > a for a, b in itertools.pairwise(steps)), height
    # No jump at either end of the blends.
    for edge in (*metrics.ENERGY_BLEND_MS, *metrics.CADENCE_BLEND_MS):
        kmh = edge * 3.6
        assert metrics.watts_per_kg(kmh + 1e-6) == pytest.approx(metrics.watts_per_kg(kmh - 1e-6), rel=1e-4)
        assert metrics.steps_per_minute(kmh + 1e-6, 172) == pytest.approx(
            metrics.steps_per_minute(kmh - 1e-6, 172), rel=1e-4
        )


def test_weight_and_height_effects():
    for kmh in (2.0, 4.8, 10.0):
        light, heavy = Profile(weight_kg=50), Profile(weight_kg=100)
        assert metrics.kcal_per_minute(kmh, heavy) == pytest.approx(2 * metrics.kcal_per_minute(kmh, light))
        assert (
            metrics.steps_per_minute(kmh, 150) > metrics.steps_per_minute(kmh, 172) > metrics.steps_per_minute(kmh, 195)
        )
        # The calorie model has no height term.
        assert metrics.kcal_per_minute(kmh, Profile(height_cm=150)) == metrics.kcal_per_minute(
            kmh, Profile(height_cm=195)
        )


def test_net_is_what_the_walk_adds_to_sitting():
    """Treadmill-desk studies: walking adds roughly 100-120 kcal an hour over sitting."""
    for kmh in (1.0, 3.2, 6.4, 9.0):
        for profile in (Profile(), Profile(weight_kg=95, height_cm=188)):
            gross = metrics.kcal_per_minute(kmh, profile)
            net = metrics.kcal_per_minute(kmh, profile, net=True)
            sitting = 1.3 * profile.weight_kg / 60  # 1.3 kcal per kg per hour
            assert 0 < net < gross
            assert gross - net == pytest.approx(sitting)
    extra_per_hour = metrics.kcal_per_minute(2.0, Profile(weight_kg=80), net=True) * 60
    assert extra_per_hour == pytest.approx(100, abs=8)


def test_step_calibration_scales_and_is_clamped():
    base = metrics.steps_per_minute(3.2, 172)
    assert metrics.steps_per_minute(3.2, 172, 1.1) == pytest.approx(base * 1.1)
    assert metrics.steps_per_minute(3.2, 172, 0.9) == pytest.approx(base * 0.9)
    assert metrics.steps_per_minute(3.2, 172, 5.0) == pytest.approx(base * 1.5)
    assert metrics.steps_per_minute(3.2, 172, 0.0) == pytest.approx(base * 0.7)


def test_heart_rate_threshold():
    assert metrics.hr_threshold_bpm(Profile()) is None
    assert metrics.hr_threshold_bpm(Profile(age=40)) == pytest.approx(108)
    assert metrics.hr_threshold_bpm(Profile(age=20)) == pytest.approx(120)
    assert metrics.HR_SUSTAIN_S == 60.0


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
    assert {6.84, 7.2, 7.92, 8.0} <= speeds  # the edges and the middle of the walk/run blends
    assert len(vectors) >= 150


def test_python_reproduces_every_vector(vectors):
    for v in vectors:
        profile = Profile(weight_kg=v["weight_kg"], height_cm=v["height_cm"])
        assert close(metrics.watts_per_kg(v["kmh"]), v["w_per_kg"]), v
        assert close(metrics.kcal_per_minute(v["kmh"], profile), v["kcal_per_min"]), v
        assert close(metrics.kcal_per_minute(v["kmh"], profile, net=True), v["kcal_per_min_net"]), v
        assert close(metrics.steps_per_minute(v["kmh"], v["height_cm"]), v["steps_per_min"]), v


def test_lua_reproduces_every_vector(vectors, lua_metrics):
    for v in vectors:
        kmh, weight, height = v["kmh"], v["weight_kg"], v["height_cm"]
        assert close(lua_metrics.wattsPerKg(kmh), v["w_per_kg"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight), v["kcal_per_min"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight, False), v["kcal_per_min"]), v
        assert close(lua_metrics.kcalPerMinute(kmh, weight, True), v["kcal_per_min_net"]), v
        assert close(lua_metrics.stepsPerMinute(kmh, height), v["steps_per_min"]), v


def test_lua_and_python_share_constants_and_defaults(lua_metrics):
    assert lua_metrics.KMH_PER_MPH == metrics.KMH_PER_MPH
    assert lua_metrics.METERS_PER_MILE == metrics.METERS_PER_MILE
    assert lua_metrics.METERS_PER_YARD == metrics.METERS_PER_YARD
    assert lua_metrics.JOULES_PER_KCAL == metrics.JOULES_PER_KCAL
    assert (lua_metrics.STEP_CALIBRATION_MIN, lua_metrics.STEP_CALIBRATION_MAX) == metrics.STEP_CALIBRATION_RANGE
    for factor in (0.5, 0.85, 1.0, 1.2, 3.0):
        assert close(lua_metrics.stepsPerMinute(3.2, 172, factor), metrics.steps_per_minute(3.2, 172, factor))
    assert lua_metrics.DEFAULT_PROFILE.weightKg == Profile().weight_kg
    assert lua_metrics.DEFAULT_PROFILE.heightCm == Profile().height_cm
    # With no profile given the addon assumes the same default body.
    assert close(lua_metrics.kcalPerMinute(4.0), metrics.kcal_per_minute(4.0, Profile()))
    assert close(lua_metrics.stepsPerMinute(4.0), metrics.steps_per_minute(4.0, Profile().height_cm))
    assert lua_metrics.kcalPerMinute(None) == 0 and lua_metrics.stepsPerMinute(None) == 0


# ---- the addon's live estimate against the companion's recorder ----------------------------


def test_lua_walk_accumulates_like_the_python_recorder(lua, lua_metrics):
    """Give the addon's count of a walk and the companion's recorder the
    same belt speeds: the in-game estimate and the companion's speed-based
    numbers must be the same sums."""
    profile = Profile(weight_kg=82.0, height_cm=178.0)
    lua_profile = lua.table(weightKg=profile.weight_kg, heightCm=profile.height_cm)
    totals = lua.table()
    recorder = SessionRecorder(source="wow", beacon_session=1, treadmill="", started_at=1_000_000.0)

    dt = 0.25  # exactly representable: both sides add identical floats
    script = [  # (seconds, the pace the walker told the addon, km/h)
        (30, 3.2),
        (60, 4.8),
        (20, 1.6),
        (15, 0.0),  # paused: nothing is counted
        (45, 3.2),
        (120, 6.4),
        (10, 0.0),
    ]
    now = recorder.started_at
    for seconds, pace in script:
        for _ in range(int(seconds / dt)):
            lua_metrics.walk(totals, dt, pace, lua_profile)
            now += dt
            recorder.update(now, speed_kmh=pace, profile=profile)

    assert totals.seconds == pytest.approx(recorder.seconds, rel=1e-9)
    assert totals.distanceM == pytest.approx(recorder.distance_speed_m, rel=1e-9)
    assert totals.steps == pytest.approx(recorder.steps_estimate, rel=1e-9)
    assert totals.kcal == pytest.approx(recorder.kcal_speed, rel=1e-9)
    assert totals.maxKmh == pytest.approx(recorder.max_kmh, rel=1e-12)
    assert recorder.seconds == 275 and 300 < recorder.distance_speed_m < 420
