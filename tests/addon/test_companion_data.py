"""CompanionData.lua: what the companion app writes and the addon picks up
at login or /reload (body profile, measured sessions)."""

from __future__ import annotations

import pytest
from walka import addon_sync
from walka.session import SessionRecord

from .conftest import DEFAULT_PACE_KMH, local_noon
from .harness import lua_dict, lua_list, lua_plain

DAY = 86400
NOW = local_noon(2026, 11, 17)

COMPANION = f"""
WalkaCompanionData = {{
    version = 1,
    written = {NOW - 3600},
    profile = {{ weightKg = 90.5, heightCm = 185 }},
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
    belt = crt.walka.GetLiveSpeed(crt.walka)
    assert belt == DEFAULT_PACE_KMH
    crt.tick(600)
    later = lua_dict(crt.walka.GetSession(crt.walka))
    assert later["kcal"] - session["kcal"] == pytest.approx(metrics.kcalPerMinute(belt, 90.5), rel=0.01)
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


# ---- treadmill limits: a thing of the past -----------------------------------------------------
# Until 0.7 the companion wrote what the treadmill could do (its speed
# range and step, the companion's ramp rates) and the addon shaped the belt
# speeds it asked for with them. The addon asks for nothing now. A file
# written by an older companion still has the table: it is not read.

OLD_LIMITS = [
    "{ minKmh = 1.0, maxKmh = 3.0, stepKmh = 0.1, accelKmhPerS = 0.25, decelKmhPerS = 0.8 }",
    "{ minKmh = 0.9656, maxKmh = 4.83, stepKmh = 0.16093, accelKmhPerS = 0.2, decelKmhPerS = 0.4, mphGrid = true }",
    "{ minKmh = 6.0, maxKmh = 1.0, stepKmh = 0 }",
    "{ minKmh = 0/0, maxKmh = 1/0, stepKmh = -0.1, accelKmhPerS = 'x' }",
    "{}", "'broken'", "5",
]


def walk_a_minute(rt) -> dict:
    """A minute's session through every way of moving; what it counted."""
    rt.slash("/walka start")
    for speed in (7.0, 0, 0.3, 14.0, 7.0, 0):
        rt.set(speed=speed)
        rt.tick(100)
    session = lua_plain(rt.walka.GetSession(rt.walka))
    rt.slash("/walka stop")
    return session


@pytest.mark.parametrize("limits", OLD_LIMITS)
def test_a_limits_table_from_an_older_companion_is_ignored(make_rt, rt, limits):
    with_limits = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {{ limits = {limits} }}"})
    assert with_limits.walka.GetBeltLimits is None, "nothing in the addon asks what the treadmill can do"
    assert with_limits.walka.GetPace(with_limits.walka) == DEFAULT_PACE_KMH
    # The walk is counted exactly as with no file at all - at the walker's
    # pace, whatever range, step or ramp the table claims.
    counted = walk_a_minute(with_limits)
    assert counted == walk_a_minute(rt)
    assert counted["maxKmh"] == DEFAULT_PACE_KMH
    assert counted["distanceM"] == pytest.approx(DEFAULT_PACE_KMH / 3.6 * 60, rel=1e-6)
    assert set(decoded_targets(with_limits)) == {0}, "and nothing of it reaches the strip"
    with_limits.reload()


def decoded_targets(rt) -> list[int]:
    rt.slash("/walka start")
    rt.set(speed=14.0, mounted=True)
    targets = []
    for _ in range(40):
        rt.tick()
        targets.append(rt.live_frame().target_raw)
    rt.slash("/walka stop")
    return targets


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
    # The companion has synced since that session and knows of no walk then:
    # it is listed, but it does not count.
    assert estimated["unconfirmed"] is True and "unconfirmed" not in newest
    assert [entry["start"] for entry in entries] == sorted((entry["start"] for entry in entries), reverse=True)
    # Merging is a view: the addon's own records are not rewritten.
    assert lua_plain(crt.db.sessions)[0]["steps"] == 2500


def test_totals_merge_both_sources_the_older_summary_and_the_live_session(crt):
    totals = lua_plain(crt.walka.GetTotals(crt.walka))
    assert totals["today"] == {"sessions": 1, "seconds": 1790, "distanceM": 1502, "steps": 2710, "kcal": 118.4, "yards": 11800}
    # (The session the companion never saw is not in these: see above.)
    assert totals["week"]["sessions"] == 2
    assert totals["week"]["steps"] == 2710 + 1300
    assert totals["all"]["sessions"] == 2 + 12
    assert totals["all"]["steps"] == 2710 + 1300 + 50000
    assert totals["all"]["kcal"] == pytest.approx(118.4 + 55.0 + 2400.5)
    assert totals["best"]["steps"] == 2710
    assert totals["streak"] == 1

    crt.slash("/walka start")
    crt.tick(300)
    live = lua_dict(crt.walka.GetSession(crt.walka))
    totals = lua_plain(crt.walka.GetTotals(crt.walka))
    assert totals["today"]["sessions"] == 2
    assert totals["today"]["steps"] == round(2710 + live["steps"])
    assert totals["all"]["sessions"] == 2 + 12 + 1


def test_a_walk_from_after_the_last_sync_counts_as_an_estimate(make_rt):
    """The companion cannot have told the addon about a walk that ended
    after it last wrote its file: that one is an estimate waiting for the
    treadmill's numbers, and counts."""
    saved = (
        "{sessions={{id=9,start=%d,['end']=%d,seconds=1190,distanceM=950,steps=1700,"
        "kcal=70.0,yards=8000,maxKmh=3.22,character='Tester-Azeroth'}}}"
    ) % (NOW - 1800, NOW - 600)
    rt = make_rt(files={"CompanionData.lua": COMPANION}, saved_variables={"WalkaDB": saved}, state={"clock": NOW})
    newest = lua_dict(lua_list(rt.walka.GetHistory(rt.walka))[0])
    assert newest["steps"] == 1700 and newest["measured"] is False and "unconfirmed" not in newest
    assert lua_plain(rt.walka.GetTotals(rt.walka))["today"]["steps"] == 2710 + 1700


def test_the_addons_records_of_walks_in_the_older_summary_are_not_counted_twice(make_rt):
    """The companion lists its newest walks and sums up the rest. The
    addon's own records of walks from before the list begins are in that
    sum already."""
    older = "older = { sessions = 12, seconds = 36000, distanceM = 30000, steps = 50000, kcal = 2400.5 }"
    companion = COMPANION.replace(older, older[:-2] + f", [\"until\"] = {NOW - 4 * DAY} }}")
    assert companion != COMPANION
    rt = make_rt(files={"CompanionData.lua": companion}, saved_variables={"WalkaDB": SAVED}, state={"clock": NOW})
    entries = [lua_dict(entry) for entry in lua_list(rt.walka.GetHistory(rt.walka))]
    assert len(entries) == 2, "the five-day-old record is in the summary, not in the list"
    assert lua_plain(rt.walka.GetTotals(rt.walka))["all"]["sessions"] == 2 + 12


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
        treadmill_name='UREVO "Strol" 2E \\ Größe',
        now=NOW - 60,
    )
    source = addon_sync.render_data_file(payload)
    rt = make_rt(files={"CompanionData.lua": source}, saved_variables={"WalkaDB": SAVED}, state={"clock": NOW})

    data = rt.walka.GetCompanionData(rt.walka)
    assert data.version == 1 and data.written == NOW - 60
    assert data.treadmill.encode() == 'UREVO "Strol" 2E \\ Größe'.encode()
    assert lua_dict(rt.walka.GetProfile(rt.walka)) == {"weightKg": 90.5, "heightCm": 185.0, "fromCompanion": True}
    assert data.limits is None, "the companion no longer says what the treadmill can do"

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
    payload = addon_sync.build_payload(records, profile={"weightKg": 75, "heightCm": 172}, now=NOW)
    rt = make_rt(files={"CompanionData.lua": addon_sync.render_data_file(payload)}, state={"clock": NOW})
    assert len(lua_list(rt.walka.GetHistory(rt.walka))) == addon_sync.MAX_SESSIONS_IN_FILE
    totals = lua_plain(rt.walka.GetTotals(rt.walka))
    assert totals["all"]["sessions"] == count
    assert totals["all"]["steps"] == count * 2600
    assert totals["all"]["kcal"] == pytest.approx(count * 110.0)


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
# Whatever it says, the addon must not put a number into its saved variables
# that the client cannot write back.


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
