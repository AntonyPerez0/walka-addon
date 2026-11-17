"""CompanionData.lua: what the companion app writes and the addon picks up
at login or /reload (body profile, treadmill limits, measured sessions)."""

from __future__ import annotations

import pytest
from walka import addon_sync
from walka.session import SessionRecord

from .conftest import local_noon
from .harness import lua_dict, lua_list, lua_plain

DAY = 86400
NOW = local_noon(2026, 11, 17)

COMPANION = f"""
WalkaCompanionData = {{
    version = 1,
    written = {NOW - 3600},
    profile = {{ weightKg = 90.5, heightCm = 185 }},
    limits = {{ minKmh = 1.0, maxKmh = 3.0, stepKmh = 0.1, accelKmhPerS = 0.25, decelKmhPerS = 0.8 }},
    treadmill = "UREVO Strol 2E",
    sessions = {{
        {{ start = {NOW - 2 * 3600}, ["end"] = {NOW - 2 * 3600 + 1800}, seconds = 1790, distanceM = 1502,
          steps = 2710, kcal = 118.4, maxKmh = 3.3, avgHr = 104, maxHr = 121, source = "wow",
          beaconSession = 3, stepsSource = "treadmill" }},
        {{ start = {NOW - 3 * DAY}, ["end"] = {NOW - 3 * DAY + 900}, seconds = 900, distanceM = 700,
          steps = 1300, kcal = 55.0, maxKmh = 3.0, source = "manual", stepsSource = "treadmill" }},
    }},
    older = {{ sessions = 12, seconds = 36000, distanceM = 30000, steps = 50000, kcal = 2400.5 }},
}}
"""

SAVED = (
    "{sessions={"
    # The same walk as the companion's first session, as the addon estimated it.
    f"{{id=3,start={NOW - 2 * 3600 + 15},['end']={NOW - 2 * 3600 + 1790},seconds=1760,distanceM=1410,steps=2500,"
    "kcal=101.2,yards=11800,maxKmh=3.22,character='Tester-Azeroth'},"
    # A walk the companion never saw.
    f"{{id=4,start={NOW - 5 * DAY},['end']={NOW - 5 * DAY + 1200},seconds=1190,distanceM=950,steps=1700,"
    "kcal=70.0,yards=8000,maxKmh=3.22,character='Alt-Azeroth'},"
    "}}"
)


@pytest.fixture
def crt(make_rt):
    """A client where the companion has written its data file."""
    return make_rt(files={"CompanionData.lua": COMPANION}, saved_variables={"WalkaDB": SAVED}, state={"clock": NOW})


# ---- profile ----------------------------------------------------------------------------


def test_profile_comes_from_the_companion(crt):
    profile = lua_dict(crt.walka.GetProfile(crt.walka))
    assert profile == {"weightKg": 90.5, "heightCm": 185, "fromCompanion": True}
    # The addon's own setting is untouched (it is the fallback).
    assert lua_plain(crt.db.settings.profile) == {"weightKg": 75, "heightCm": 172}


def test_estimates_use_the_companions_profile(crt):
    crt.slash("/walka start")
    crt.tick(600)
    session = lua_dict(crt.walka.GetSession(crt.walka))
    metrics = crt.ns.Metrics
    belt = crt.walka.GetLiveSpeed(crt.walka)[0]
    crt.tick(600)
    later = lua_dict(crt.walka.GetSession(crt.walka))
    assert later["kcal"] - session["kcal"] == pytest.approx(metrics.kcalPerMinute(belt, 90.5, 185), rel=0.01)
    assert later["steps"] - session["steps"] == pytest.approx(metrics.stepsPerMinute(belt, 185), rel=0.01)


def test_own_profile_without_companion_data(rt):
    rt.slash("/walka weight 82kg")
    profile = lua_dict(rt.walka.GetProfile(rt.walka))
    assert profile == {"weightKg": 82, "heightCm": 172}
    assert rt.walka.GetCompanionData(rt.walka) is None


def test_partial_companion_profile(make_rt):
    rt = make_rt(files={"CompanionData.lua": "WalkaCompanionData = { profile = { weightKg = '88' } }"})
    assert lua_dict(rt.walka.GetProfile(rt.walka)) == {"weightKg": 88, "heightCm": 172, "fromCompanion": True}
    rt = make_rt(files={"CompanionData.lua": "WalkaCompanionData = { profile = { heightCm = 190 } }"})
    assert lua_dict(rt.walka.GetProfile(rt.walka)) == {"weightKg": 75, "heightCm": 172}


# ---- belt limits ----------------------------------------------------------------------------


def test_belt_limits_come_from_the_companion(crt):
    limits = lua_dict(crt.walka.GetBeltLimits(crt.walka))
    assert limits == {
        "minKmh": 1.0, "maxKmh": 3.0, "stepKmh": 0.1, "accelKmhPerS": 0.25, "decelKmhPerS": 0.8,
        "fromCompanion": True,
    }


def test_targets_respect_the_treadmills_range_and_grid(crt):
    assert crt.db.settings.units == "mph"       # the display unit no longer decides the grid
    crt.set(speed=14.0, mounted=True)
    crt.slash("/walka start")
    targets = set()
    for _ in range(900):
        crt.tick()
        targets.add(round(crt.walka.GetLiveSpeed(crt.walka)[1], 6))
    assert max(targets) == 3.0, "never ask for more than the treadmill can do"
    assert all(abs(target * 10 - round(target * 10)) < 1e-6 for target in targets), "0.1 km/h steps"
    assert crt.db.settings.speed.maxKmh == 4.8, "the user's own cap is kept for a faster treadmill"


def test_belt_estimate_uses_the_companions_ramp_rates(crt):
    crt.set(speed=7.0)
    crt.slash("/walka start")
    crt.tick(50)                                # 3 s countdown, then 2 s of ramp
    assert crt.walka.GetLiveSpeed(crt.walka)[0] == pytest.approx(1.0 + 0.25 * 2.0, abs=0.06)


def test_assumed_limits_without_companion_data(rt):
    limits = lua_dict(rt.walka.GetBeltLimits(rt.walka))
    assert "fromCompanion" not in limits
    assert limits["minKmh"] == pytest.approx(0.6 * 1.609344)
    assert limits["maxKmh"] == pytest.approx(4.0 * 1.609344)


@pytest.mark.parametrize(
    "limits",
    ["nil", "{}", "{ minKmh = 1.0 }", "{ maxKmh = 6.0 }", "'broken'", "{ minKmh = 'a', maxKmh = 'b' }"],
)
def test_incomplete_limits_fall_back_to_the_assumed_ones(make_rt, limits):
    rt = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {{ limits = {limits} }}"})
    assert "fromCompanion" not in lua_dict(rt.walka.GetBeltLimits(rt.walka))
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    assert rt.walka.GetLiveSpeed(rt.walka)[1] == pytest.approx(2.0 * 1.609344, abs=0.01)


# ---- history and totals -----------------------------------------------------------------------


def test_history_merges_both_sources(crt):
    entries = [lua_dict(entry) for entry in lua_list(crt.walka.GetHistory(crt.walka))]
    assert len(entries) == 3, "the walk recorded by both sides is listed once"
    newest, manual, estimated = entries
    assert newest["measured"] is True
    assert (newest["steps"], newest["distanceM"], newest["kcal"]) == (2710, 1502, 118.4)
    assert (newest["avgHr"], newest["stepsSource"], newest["game"]) == (104, "treadmill", "wow")
    assert (newest["yards"], newest["character"]) == (11800, "Tester-Azeroth")
    assert manual["measured"] is True and manual["game"] == "manual" and manual.get("character") is None
    assert estimated["measured"] is False and estimated["character"] == "Alt-Azeroth"
    assert [entry["start"] for entry in entries] == sorted((entry["start"] for entry in entries), reverse=True)
    # Merging is a view: the addon's own records are not rewritten.
    assert lua_plain(crt.db.sessions)[0]["steps"] == 2500


def test_totals_merge_both_sources_the_older_summary_and_the_live_session(crt):
    totals = lua_plain(crt.walka.GetTotals(crt.walka))
    assert totals["today"] == {"sessions": 1, "seconds": 1790, "distanceM": 1502, "steps": 2710, "kcal": 118.4, "yards": 11800}
    assert totals["week"]["sessions"] == 3
    assert totals["week"]["steps"] == 2710 + 1300 + 1700
    assert totals["all"]["sessions"] == 3 + 12
    assert totals["all"]["steps"] == 2710 + 1300 + 1700 + 50000
    assert totals["all"]["kcal"] == pytest.approx(118.4 + 55.0 + 70.0 + 2400.5)
    assert totals["best"]["steps"] == 2710
    assert totals["streak"] == 1

    crt.slash("/walka start")
    crt.tick(300)
    live = lua_dict(crt.walka.GetSession(crt.walka))
    totals = lua_plain(crt.walka.GetTotals(crt.walka))
    assert totals["today"]["sessions"] == 2
    assert totals["today"]["steps"] == round(2710 + live["steps"])
    assert totals["all"]["sessions"] == 16


def test_a_finished_session_shows_up_in_the_merged_history(crt):
    crt.slash("/walka start")
    crt.tick(300)
    crt.slash("/walka stop")
    entries = [lua_dict(entry) for entry in lua_list(crt.walka.GetHistory(crt.walka))]
    assert len(entries) == 4
    assert entries[0]["measured"] is False and entries[0]["start"] == NOW


@pytest.mark.parametrize(
    "data",
    [
        "nil", "5", "'text'", "true", "{}", "{ sessions = 'x' }", "{ sessions = { 1, 'two', {} } }",
        "{ profile = 3, limits = 4, older = 5, written = 'never' }",
        "{ sessions = { { start = 10, ['end'] = 5 } }, older = { sessions = 'many' } }",
    ],
)
def test_any_companion_data_value_is_survivable(make_rt, data):
    rt = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {data}"}, saved_variables={"WalkaDB": SAVED},
                 state={"clock": NOW})
    assert len(lua_list(rt.walka.GetHistory(rt.walka))) == 2
    assert lua_plain(rt.walka.GetTotals(rt.walka))["all"]["sessions"] == 2
    rt.slash("/walka status")
    rt.slash("/walka start")
    rt.tick(30)


def test_shipped_data_file_is_empty_but_defines_the_global(make_rt, addon_dir):
    source = (addon_dir / "CompanionData.lua").read_text(encoding="utf-8")
    assert "WalkaCompanionData" in source
    rt = make_rt()
    assert rt.walka.GetCompanionData(rt.walka) is None
    assert "WalkaCompanionData" in rt.created_globals()


# ---- the companion's real writer ------------------------------------------------------------------


def test_file_written_by_the_companion_loads_and_merges(make_rt, tmp_path):
    records = [
        SessionRecord(id="a", start=NOW - 2 * 3600 + 0.4, end=NOW - 2 * 3600 + 1799.2, seconds=1790.3,
                      distance_m=1502.4, steps=2710, kcal=118.44, max_kmh=3.301, avg_hr=104.4, max_hr=121,
                      source="wow", beacon_session=3, steps_source="treadmill"),
        SessionRecord(id="b", start=NOW - 3 * DAY, end=NOW - 3 * DAY + 900, seconds=900, distance_m=700,
                      steps=1300, kcal=55.0, max_kmh=3.0, source="manual"),
    ]
    payload = addon_sync.build_payload(
        records,
        profile={"weightKg": 90.5, "heightCm": 185.0},
        limits={"minKmh": 1.0, "maxKmh": 6.0, "stepKmh": 0.1, "accelKmhPerS": 0.5, "decelKmhPerS": 1.0},
        treadmill_name='UREVO "Strol" 2E \\ Größe',
        now=NOW - 60,
    )
    source = addon_sync.render_data_file(payload)
    rt = make_rt(files={"CompanionData.lua": source}, saved_variables={"WalkaDB": SAVED}, state={"clock": NOW})

    data = rt.walka.GetCompanionData(rt.walka)
    assert data.version == 1 and data.written == NOW - 60
    assert data.treadmill.encode() == 'UREVO "Strol" 2E \\ Größe'.encode()
    assert lua_dict(rt.walka.GetProfile(rt.walka)) == {"weightKg": 90.5, "heightCm": 185.0, "fromCompanion": True}
    assert lua_dict(rt.walka.GetBeltLimits(rt.walka))["fromCompanion"] is True

    entries = [lua_dict(entry) for entry in lua_list(rt.walka.GetHistory(rt.walka))]
    assert len(entries) == 3
    assert entries[0]["measured"] is True and entries[0]["yards"] == 11800
    assert entries[0]["avgHr"] == 104 and entries[0]["maxHr"] == 121
    assert entries[1].get("avgHr") is None
    rt.slash("/walka status")
    assert "companion data from 2026-11-17" in rt.printed[-1]


def test_companion_writes_many_sessions_and_an_older_summary(make_rt):
    count = addon_sync.MAX_SESSIONS_IN_FILE + 25
    records = [
        SessionRecord(id=str(index), start=NOW - (count - index) * 7200, end=NOW - (count - index) * 7200 + 1800,
                      seconds=1800, distance_m=1500, steps=2600, kcal=110.0, max_kmh=3.2)
        for index in range(count)
    ]
    payload = addon_sync.build_payload(records, profile={"weightKg": 75, "heightCm": 172}, limits=None, now=NOW)
    rt = make_rt(files={"CompanionData.lua": addon_sync.render_data_file(payload)}, state={"clock": NOW})
    assert len(lua_list(rt.walka.GetHistory(rt.walka))) == addon_sync.MAX_SESSIONS_IN_FILE
    totals = lua_plain(rt.walka.GetTotals(rt.walka))
    assert totals["all"]["sessions"] == count
    assert totals["all"]["steps"] == count * 2600
    assert totals["all"]["kcal"] == pytest.approx(count * 110.0)
    assert "fromCompanion" not in lua_dict(rt.walka.GetBeltLimits(rt.walka))     # limits = nil: not connected yet


def test_damaged_own_records_do_not_break_the_merge(make_rt):
    # A hand-edited or half-written SavedVariables file.
    saved = f"{{sessions={{5,'x',{{start={NOW - 2 * 3600},['end']={NOW - 2 * 3600 + 1800},seconds=1700,yards=500}},false}}}}"
    rt = make_rt(files={"CompanionData.lua": COMPANION}, saved_variables={"WalkaDB": saved}, state={"clock": NOW})
    entries = [lua_dict(entry) for entry in lua_list(rt.walka.GetHistory(rt.walka))]
    assert [entry["measured"] for entry in entries] == [True, True]
    assert entries[0]["yards"] == 500
    assert lua_plain(rt.walka.GetTotals(rt.walka))["all"]["sessions"] == 14


# ---- nonsense in the data file -------------------------------------------------------------------
# The file is written by another program (possibly another version of it).
# Whatever it says, the addon must not paint garbage into the beacon, ask
# for a speed above the user's cap, or put a number into its saved variables
# that the client cannot write back.

KMH_PER_MPH = 1.609344
ASSUMED = (0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH)


def drive(rt):
    """A varied walk; returns every target and belt estimate seen."""
    rt.slash("/walka start")
    targets, belts = [], []
    for speed in (7.0, 0, 0.3, 14.0, 7.0):
        rt.set(speed=speed)
        for _ in range(120):
            rt.tick()
            belt, target = rt.walka.GetLiveSpeed(rt.walka)
            belts.append(belt)
            targets.append(target)
    return targets, belts


@pytest.mark.parametrize(
    "limits, accepted",
    [
        ("{ minKmh = 2.0, maxKmh = 6.0, stepKmh = 0 }", True),              # no step reported
        ("{ minKmh = 1.0, maxKmh = 6.0, stepKmh = -0.1 }", True),
        ("{ minKmh = 1.0, maxKmh = 6.0, stepKmh = 0/0 }", True),
        ("{ minKmh = 1.0, maxKmh = 6.0, stepKmh = 1/0 }", True),
        ("{ minKmh = 1.0, maxKmh = 6.0, stepKmh = 0.1, accelKmhPerS = 0, decelKmhPerS = -1 }", True),
        ("{ minKmh = 1.0, maxKmh = 6.0, stepKmh = 0.1, accelKmhPerS = 'x', decelKmhPerS = 0/0 }", True),
        ("{ minKmh = 6.0, maxKmh = 1.0, stepKmh = 0.1 }", False),           # range upside down
        ("{ minKmh = 0, maxKmh = 6.0, stepKmh = 0.1 }", False),             # a belt cannot hold 0
        ("{ minKmh = -1, maxKmh = 6.0, stepKmh = 0.1 }", False),
        ("{ minKmh = 0/0, maxKmh = 6.0, stepKmh = 0.1 }", False),
        ("{ minKmh = 1.0, maxKmh = 0/0, stepKmh = 0.1 }", False),
        ("{ minKmh = 1.0, maxKmh = 1/0, stepKmh = 0.1 }", False),
    ],
)
def test_nonsense_limits_never_reach_the_belt(make_rt, limits, accepted):
    rt = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {{ limits = {limits} }}"})
    found = lua_dict(rt.walka.GetBeltLimits(rt.walka))
    assert found.get("fromCompanion", False) is accepted
    low, high = (found["minKmh"], found["maxKmh"])
    assert 0 < low <= high < 100 and 0 < found["stepKmh"] < 10
    for key in ("accelKmhPerS", "decelKmhPerS"):
        assert found.get(key) is None or 0 < found[key] < 100

    targets, belts = drive(rt)
    cap = min(high, rt.db.settings.speed.maxKmh)
    for value in targets + belts:
        assert value == value and abs(value) != float("inf"), "not a number"
        assert value == 0 or low - 1e-6 <= value <= cap + 0.005 + 1e-6
    assert max(belts) > 0, "the estimate must still move"
    assert max(belts) >= min(3.0, cap) - 0.2, "... and get up to speed"
    rt.slash("/walka stop")
    rt.reload()


def test_a_step_larger_than_the_range_cannot_push_the_target_past_the_cap(make_rt):
    rt = make_rt(files={"CompanionData.lua": "WalkaCompanionData = { limits = { minKmh = 1, maxKmh = 6, stepKmh = 50 } }"})
    targets, _ = drive(rt)
    assert max(targets) <= rt.db.settings.speed.maxKmh


@pytest.mark.parametrize(
    "profile",
    [
        "{ weightKg = 0, heightCm = 0 }",
        "{ weightKg = 80, heightCm = 0 }",          # height left empty in the companion
        "{ weightKg = -80, heightCm = -180 }",
        "{ weightKg = 0/0, heightCm = 0/0 }",
        "{ weightKg = 1/0, heightCm = 1/0 }",
        "{ weightKg = 80000, heightCm = 1.8 }",     # grams; metres
        "{ weightKg = 'heavy', heightCm = 'tall' }",
    ],
)
def test_nonsense_profile_falls_back_to_the_addons_own(make_rt, profile):
    rt = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {{ profile = {profile} }}"})
    used = lua_dict(rt.walka.GetProfile(rt.walka))
    assert 30 <= used["weightKg"] <= 250 and 120 <= used["heightCm"] <= 230
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(400)
    session = lua_dict(rt.walka.GetSession(rt.walka))
    assert 1 < session["kcal"] < 10 and 30 < session["steps"] < 100
    rt.slash("/walka stop")
    record = lua_plain(rt.reload().db.sessions)[0]      # the serialiser refuses inf and nan
    assert record["steps"] == round(session["steps"])
