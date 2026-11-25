"""Core/SessionLog.lua: merging the addon's and the companion's sessions, and totals."""

from __future__ import annotations

import os
import time

import pytest

from .conftest import local_noon
from .harness import lua_dict, lua_list, lua_plain

DAY = 86400
NOW = local_noon(2026, 11, 17)


def own(start, seconds=1800, *, end=None, steps=2500, distance=1400, kcal=100.0, yards=9000, character="Tester-Azeroth"):
    return {
        "start": start,
        "end": start + seconds if end is None else end,
        "seconds": seconds,
        "distanceM": distance,
        "steps": steps,
        "kcal": kcal,
        "yards": yards,
        "maxKmh": 3.2,
        "character": character,
    }


def measured(start, seconds=1800, *, steps=2600, distance=1500, kcal=110.0, **extra):
    record = {
        "start": start,
        "end": start + seconds,
        "seconds": seconds,
        "distanceM": distance,
        "steps": steps,
        "kcal": kcal,
        "maxKmh": 3.3,
    }
    record.update(extra)
    return record


def merge(rt, own_sessions, companion_sessions=None):
    data = None if companion_sessions is None else rt.to_lua({"sessions": companion_sessions})
    own_table = None if own_sessions is None else rt.to_lua(own_sessions)
    return [lua_dict(entry) for entry in lua_list(rt.ns.SessionLog.merge(own_table, data))]


def totals(rt, entries, now=NOW, live=None, older=None):
    result = rt.ns.SessionLog.totals(
        rt.to_lua(entries),
        now,
        None if live is None else rt.to_lua(live),
        None if older is None else rt.to_lua(older),
    )
    return lua_plain(result)


# ---- sameWalk ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, same",
    [
        ((1000, 2800), (1000, 2800), True),      # identical
        ((1000, 2800), (1030, 2790), True),      # clocks a little apart
        ((1000, 2800), (1900, 3700), True),      # overlap exactly half
        ((1000, 2800), (1901, 3701), False),     # just under half
        ((1000, 2800), (2800, 4600), False),     # back to back
        ((1000, 2800), (5000, 6800), False),     # different walks
        ((1000, 9000), (3000, 3600), True),      # a short one inside a long one
        ((1000, 9000), (8900, 9500), False),     # short one mostly outside
        ((1000, 1000), (1000, 2800), False),     # zero length
        ((2800, 1000), (1000, 2800), False),     # ends before it starts
    ],
)
def test_same_walk(rt, a, b, same):
    session_log = rt.ns.SessionLog
    first = rt.to_lua({"start": a[0], "end": a[1]})
    second = rt.to_lua({"start": b[0], "end": b[1]})
    assert session_log.sameWalk(first, second) is same
    assert session_log.sameWalk(second, first) is same


def test_same_walk_needs_both_ends(rt):
    session_log = rt.ns.SessionLog
    complete = rt.to_lua({"start": 1000, "end": 2800})
    assert session_log.sameWalk(rt.to_lua({"start": 1000}), complete) is False
    assert session_log.sameWalk(complete, rt.lua.table()) is False


# ---- merge ------------------------------------------------------------------------


def test_a_walk_recorded_by_both_is_listed_once_with_the_companions_numbers(rt):
    start = NOW - 3600
    entries = merge(rt, [own(start + 20)], [measured(start, avgHr=104, maxHr=121, stepsSource="garmin", source="wow")])
    assert len(entries) == 1
    entry = entries[0]
    # measured values ...
    assert entry["measured"] is True
    assert (entry["start"], entry["seconds"]) == (start, 1800)
    assert (entry["steps"], entry["distanceM"], entry["kcal"], entry["maxKmh"]) == (2600, 1500, 110.0, 3.3)
    assert (entry["avgHr"], entry["maxHr"], entry["stepsSource"], entry["game"]) == (104, 121, "garmin", "wow")
    # ... plus what only the addon knows.
    assert entry["yards"] == 9000
    assert entry["character"] == "Tester-Azeroth"


def test_walks_known_to_one_side_only_are_kept(rt):
    entries = merge(
        rt,
        [own(NOW - 5 * DAY)],                               # the companion was not running
        [measured(NOW - 2 * DAY, source="manual")],         # a walk outside the game
    )
    assert [entry["measured"] for entry in entries] == [True, False]
    outside, estimated = entries
    assert outside.get("character") is None and outside.get("yards") is None
    assert estimated["character"] == "Tester-Azeroth" and estimated["yards"] == 9000
    assert estimated["steps"] == 2500


def test_newest_first(rt):
    starts = [NOW - 9 * DAY, NOW - DAY, NOW - 4 * DAY]
    entries = merge(rt, [own(starts[0]), own(starts[1])], [measured(starts[2])])
    assert [entry["start"] for entry in entries] == sorted(starts, reverse=True)


def test_a_reload_split_into_two_addon_records_folds_into_one_measured_walk(rt):
    start = NOW - 7200
    entries = merge(
        rt,
        [
            own(start, 1500, yards=5000, character="Alpha-Azeroth"),
            own(start + 1600, 1900, yards=7000, character="Beta-Azeroth"),
        ],
        [measured(start, 3600)],
    )
    assert len(entries) == 1
    assert entries[0]["yards"] == 12000
    assert entries[0]["character"] == "Alpha-Azeroth"


def test_one_session_can_hold_several_measured_walks(rt):
    """The app records a walk from the belt alone: it ends when the belt has
    stood for a minute, while the session in the game runs on. Each walk is
    the character's, and the character's yards are shared out among them by
    their time in the session - never counted twice."""
    start = NOW - 7200
    session = own(start, 3600, yards=4000)
    entries = merge(rt, [session], [measured(start, 900), measured(start + 1800, 1800)])
    assert len(entries) == 2, "both walks are listed, and the session is not listed again"
    assert sorted(entry["yards"] for entry in entries) == [pytest.approx(4000 / 3), pytest.approx(8000 / 3)]
    assert {entry["character"] for entry in entries} == {session["character"]}


def test_a_session_with_one_walk_gives_it_all_its_yards(rt):
    start = NOW - 7200
    entries = merge(rt, [own(start, 3600, yards=4000)], [measured(start + 600, 1200)])
    assert [entry["yards"] for entry in entries] == [4000]


def test_broken_companion_records_are_ignored(rt):
    data = rt.to_lua({"sessions": [measured(NOW - DAY)]})
    data.sessions[2] = "not a table"
    data.sessions[3] = rt.to_lua({"start": 500, "end": 400})        # ends before it starts
    data.sessions[4] = rt.to_lua({"start": "x"})
    entries = lua_list(rt.ns.SessionLog.merge(rt.to_lua([own(NOW - 3 * DAY)]), data))
    assert len(entries) == 2


def test_merge_without_either_source(rt):
    assert merge(rt, None, None) == []
    assert merge(rt, [], []) == []
    assert len(merge(rt, [own(NOW - DAY)], None)) == 1
    assert len(merge(rt, None, [measured(NOW - DAY)])) == 1
    assert lua_list(rt.ns.SessionLog.merge(rt.to_lua([own(NOW - DAY)]), rt.lua.table())) != []


def test_text_numbers_from_saved_files_are_accepted(rt):
    entries = merge(rt, [], [{"start": str(NOW - 600), "end": str(NOW), "seconds": "600", "steps": "900"}])
    assert entries[0]["seconds"] == 600 and entries[0]["steps"] == 900 and entries[0]["kcal"] == 0


# ---- totals -----------------------------------------------------------------------


def entry(days_ago, hour_offset=-2, **values):
    record = own(NOW - days_ago * DAY + hour_offset * 3600, **values)
    record["measured"] = False
    return record


def test_totals_today_week_all(rt):
    entries = [
        entry(0, steps=1000, distance=600, kcal=40.5, seconds=900, yards=3000),
        entry(0, -5, steps=500, distance=300, kcal=20.0, seconds=450, yards=1000),
        entry(1, steps=2000, distance=1200, kcal=80.0, seconds=1800, yards=5000),
        entry(6, steps=3000, distance=1800, kcal=120.0, seconds=2700, yards=7000),
        entry(7, steps=4000, distance=2400, kcal=160.0, seconds=3600, yards=9000),      # a week ago today
        entry(40, steps=5000, distance=3000, kcal=200.0, seconds=4500, yards=11000),
    ]
    result = totals(rt, entries)
    assert result["today"] == {"sessions": 2, "seconds": 1350, "distanceM": 900, "steps": 1500, "kcal": 60.5, "yards": 4000}
    assert result["week"] == {"sessions": 4, "seconds": 5850, "distanceM": 3900, "steps": 6500, "kcal": 260.5, "yards": 16000}
    assert result["all"] == {"sessions": 6, "seconds": 13950, "distanceM": 9300, "steps": 15500, "kcal": 620.5, "yards": 36000}
    assert result["best"] == {"steps": 5000, "distanceM": 3000, "seconds": 4500}


def test_totals_include_the_live_session_and_the_older_summary(rt):
    entries = [entry(1, steps=2000, distance=1200, kcal=80.0, seconds=1800, yards=5000)]
    live = {"start": NOW - 600, "seconds": 540.4, "distanceM": 400.2, "steps": 800.6, "kcal": 31.25, "yards": 2500.5}
    older = {"sessions": 12, "seconds": 36000, "distanceM": 30000, "steps": 50000, "kcal": 2400}
    result = totals(rt, entries, live=live, older=older)
    assert result["today"]["sessions"] == 1
    assert result["today"]["steps"] == 801                   # whole steps
    assert result["today"]["seconds"] == pytest.approx(540.4)
    assert result["week"]["sessions"] == 2
    assert result["week"]["steps"] == 2801
    assert result["all"]["sessions"] == 14
    assert result["all"]["steps"] == 52801
    assert result["all"]["distanceM"] == pytest.approx(31600.2)
    assert result["all"]["kcal"] == pytest.approx(2511.25)
    # The live session counts as "walked today" for the streak.
    assert result["streak"] == 2
    # "older" is a summary: it cannot hold a personal best.
    assert result["best"]["steps"] == 2000


def test_days_are_the_last_seven_calendar_days_oldest_first(rt):
    entries = [
        entry(0, steps=1000, distance=600, kcal=40.0, seconds=900),
        entry(2, steps=700, distance=400, kcal=30.0, seconds=600),
        entry(2, -6, steps=300, distance=200, kcal=10.0, seconds=300),
        entry(9, steps=9999),
    ]
    days = totals(rt, entries)["days"]
    expected_keys = [time.strftime("%Y-%m-%d", time.localtime(NOW - offset * DAY)) for offset in range(6, -1, -1)]
    assert [day["day"] for day in days] == expected_keys
    assert [day["steps"] for day in days] == [0, 0, 0, 0, 1000, 0, 1000]
    assert days[4] == {"day": expected_keys[4], "steps": 1000, "distanceM": 600, "kcal": 40.0, "seconds": 900}


def test_empty_history(rt):
    result = totals(rt, [])
    empty = {"sessions": 0, "seconds": 0, "distanceM": 0, "steps": 0, "kcal": 0, "yards": 0}
    assert result["today"] == empty and result["week"] == empty and result["all"] == empty
    assert result["streak"] == 0
    assert len(result["days"]) == 7
    assert lua_plain(rt.ns.SessionLog.totals(None, NOW))["all"] == empty


@pytest.mark.parametrize(
    "days_walked, streak",
    [
        ([], 0),
        ([0], 1),
        ([0, 1, 2], 3),
        ([1, 2, 3], 3),         # nothing yet today: yesterday's streak still stands
        ([2, 3], 0),            # a day was missed
        ([0, 2, 3], 1),
        ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 11),
    ],
)
def test_streak(rt, days_walked, streak):
    assert totals(rt, [entry(days_ago) for days_ago in days_walked])["streak"] == streak


def test_day_key_is_the_local_calendar_day(rt):
    assert rt.ns.SessionLog.dayKey(NOW) == "2026-11-17"
    assert rt.ns.SessionLog.dayKey(NOW + 11 * 3600) == "2026-11-17"
    assert rt.ns.SessionLog.dayKey(NOW + 13 * 3600) == "2026-11-18"


# ---- daylight saving ------------------------------------------------------------------
# Stepping back "one day" as 86400 seconds skips or repeats a calendar day
# around a clock change: for an hour after midnight on the day after the
# clocks go forward, and for the last hour of the day they go back.


@pytest.fixture
def us_eastern():
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset() (not available on Windows)")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def local(year, month, day, hour, minute=0):
    return int(time.mktime((year, month, day, hour, minute, 0, 0, 0, -1)))


def test_week_after_the_clocks_go_forward(rt, us_eastern):
    # Sunday 14 March 2027 has 23 hours. Half past midnight on Monday:
    now = local(2027, 3, 15, 0, 30)
    sunday_walk = own(local(2027, 3, 14, 15), steps=1234)
    result = totals(rt, [sunday_walk], now=now)
    assert [day["day"] for day in result["days"]] == [f"2027-03-{day:02d}" for day in range(9, 16)]
    assert result["days"][5]["steps"] == 1234
    assert result["week"]["steps"] == 1234
    assert result["streak"] == 1


def test_week_on_the_day_the_clocks_go_back(rt, us_eastern):
    # Sunday 7 November 2027 has 25 hours. Half past eleven that night:
    now = local(2027, 11, 7, 23, 30)
    monday_walk = own(local(2027, 11, 1, 18), steps=4321)
    result = totals(rt, [monday_walk, own(local(2027, 11, 6, 9)), own(local(2027, 11, 7, 9))], now=now)
    assert [day["day"] for day in result["days"]] == [f"2027-11-{day:02d}" for day in range(1, 8)]
    assert result["days"][0]["steps"] == 4321
    assert result["streak"] == 2


# ---- merge at scale -------------------------------------------------------------------------
# The merge runs in the game's UI thread whenever a window refreshes its
# totals. A year of daily walks is ~400 addon records against ~300 measured
# ones; comparing every pair in full took ~80 ms, a visible stutter.


def reference_merge(own_sessions, companion_sessions):
    """The documented behaviour, written the obvious way."""

    def same_walk(a, b):
        if a["end"] <= a["start"] or b["end"] <= b["start"]:
            return False
        shared = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
        return shared >= min(a["end"] - a["start"], b["end"] - b["start"]) * 0.5

    def overlap(a, b):
        return max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))

    valid = [record for record in companion_sessions if record["end"] > record["start"]]
    # Every session of the addon's goes with every measured walk it is the
    # same walk as - one walk, or several.
    walks_of = {
        index: [number for number, record in enumerate(valid) if same_walk(session, record)]
        for index, session in enumerate(own_sessions)
    }
    yards = {}
    for index, numbers in walks_of.items():
        whole = sum(overlap(own_sessions[index], valid[number]) for number in numbers)
        for number in numbers:
            if len(numbers) == 1:
                share = 1
            else:
                share = overlap(own_sessions[index], valid[number]) / whole if whole > 0 else 1 / len(numbers)
            yards[number] = yards.get(number, 0) + own_sessions[index]["yards"] * share
    merged = []
    for number, record in enumerate(valid):
        character = next(
            (own_sessions[index]["character"] for index, numbers in walks_of.items() if number in numbers), None
        )
        merged.append((record["start"], True, record["steps"], rounded(yards.get(number)), character))
    for index, session in enumerate(own_sessions):
        if not walks_of[index]:
            merged.append((session["start"], False, session["steps"], rounded(session["yards"]), session["character"]))
    return sorted(merged, key=repr)


def rounded(value):
    return None if value is None else round(value, 6)


@pytest.mark.parametrize("seed", range(8))
def test_merge_matches_the_reference_on_random_histories(rt, seed):
    import random

    rng = random.Random(seed)
    base = NOW - 400 * DAY

    def random_sessions(count, measured_side):
        sessions = []
        for index in range(count):
            start = base + rng.randrange(0, 60 * DAY)
            length = rng.choice([0, 30, 600, 1800, 1800, 5400, 14400])
            record = {"start": start, "end": start + length, "seconds": length, "steps": 1000 + index,
                      "distanceM": 100, "kcal": 10.0, "maxKmh": 3.0}
            if not measured_side:
                record.update(yards=100 + index, character=f"Char{index % 3}-Realm")
            sessions.append(record)
        return sessions

    own_sessions = random_sessions(rng.randrange(0, 120), False)
    companion_sessions = random_sessions(rng.randrange(0, 120), True)
    # Some walks really are the same on both sides, a little offset.
    for session in rng.sample(own_sessions, len(own_sessions) // 2):
        companion_sessions.append({**session, "start": session["start"] + rng.randrange(-40, 40),
                                   "end": session["end"] + rng.randrange(-40, 40), "steps": 5000})
    rng.shuffle(companion_sessions)

    got = sorted(
        (
            (entry["start"], entry["measured"], entry["steps"], rounded(entry.get("yards")), entry.get("character"))
            for entry in merge(rt, own_sessions, companion_sessions)
        ),
        key=repr,
    )
    assert got == reference_merge(own_sessions, companion_sessions)


def test_merging_a_long_history_does_not_compare_every_pair_in_full(rt):
    own_sessions = [own(NOW - index * 7200 - 5000, 1750) for index in range(400)]
    companion_sessions = [measured(NOW - index * 7200 - 5010, 1790) for index in range(300)]
    counter = rt.lua.eval(
        "function(log) local real, calls = log.sameWalk, 0 "
        "log.sameWalk = function(a, b) calls = calls + 1 return real(a, b) end "
        "return function() return calls end end"
    )(rt.ns.SessionLog)
    entries = merge(rt, own_sessions, companion_sessions)
    assert len(entries) == 400
    assert sum(1 for entry in entries if entry["measured"]) == 300
    assert counter() <= 3 * (400 + 300), f"{counter()} full comparisons for 400 x 300 records"


# ---- a walk that runs past midnight -------------------------------------------------------


def test_a_walk_past_midnight_is_shared_between_the_two_days(rt):
    """Started at 23:40, ended at 00:20: a third... half of it is today's.
    (Booked whole on the day it began, today's goal and streak would show
    nothing for the steps walked after midnight.)"""
    midnight = NOW - 12 * 3600
    walk = rt.to_lua({
        "start": midnight - 1200, "end": midnight + 1200, "seconds": 2400,
        "distanceM": 2000, "steps": 3000, "kcal": 120, "yards": 9000, "measured": True,
    })
    result = lua_plain(rt.ns.SessionLog.totals(rt.to_lua([walk]), NOW))
    assert result["today"]["steps"] == pytest.approx(1500) and result["today"]["sessions"] == 0
    assert result["today"]["kcal"] == pytest.approx(60)
    yesterday, today = result["days"][-2], result["days"][-1]
    assert yesterday["steps"] == pytest.approx(1500) and today["steps"] == pytest.approx(1500)
    # One walk, counted once; its whole length is still the best on record.
    assert result["week"]["sessions"] == 1 and result["all"]["sessions"] == 1
    assert result["all"]["steps"] == 3000 and result["best"]["steps"] == 3000
    assert result["streak"] == 2, "both days were walked on"


def test_the_session_in_progress_past_midnight_counts_for_today(rt):
    midnight = NOW - 12 * 3600
    now = midnight + 600
    live = rt.to_lua({"start": midnight - 1800, "seconds": 2400, "distanceM": 2000, "steps": 3000, "kcal": 120, "yards": 0})
    result = lua_plain(rt.ns.SessionLog.totals(rt.to_lua([]), now, live))
    assert result["today"]["steps"] == pytest.approx(3000 * 600 / 2400)
    assert result["all"]["steps"] == 3000 and result["all"]["sessions"] == 1


def test_a_walk_within_one_day_is_booked_whole(rt):
    walk = rt.to_lua({"start": NOW - 3600, "end": NOW - 1800, "seconds": 1800, "steps": 2000, "distanceM": 1500, "kcal": 80})
    result = lua_plain(rt.ns.SessionLog.totals(rt.to_lua([walk]), NOW))
    assert result["today"] == {"sessions": 1, "seconds": 1800, "distanceM": 1500, "steps": 2000, "kcal": 80, "yards": 0}
