"""Core behaviour the windows rely on, found or pinned down while the UI
was integrated: timestamps the game cannot format, non-finite numbers in
the data files, the target right after a session starts, totals from a
history that is already merged, and garbage on the tick."""

from __future__ import annotations

import pytest
from walka import beacon

from .conftest import KMH_PER_MPH, MPH_STEP, local_noon, nearest_step
from .harness import lua_dict, lua_list, lua_plain

NOW = local_noon(2026, 11, 17)
DAY = 86400


def merge(rt, own_source: str, companion_source: str = "nil"):
    entries = rt.run(f"local log = ... return log.merge({own_source}, {companion_source})", rt.ns.SessionLog)
    return [lua_plain(entry) for entry in lua_list(entries)]


# ---- SessionLog: timestamps ---------------------------------------------------------------


def test_records_with_a_start_the_game_cannot_show_are_left_out(rt):
    good = f"{{ start = {NOW - 3000}, ['end'] = {NOW - 1200}, seconds = 1750, steps = 2000 }}"
    own = (
        f"{{ {good}, {{}}, {{ start = 'x' }}, {{ start = -500, ['end'] = -100 }}, {{ start = 1e12, ['end'] = 1e12 + 5 }},"
        f" {{ start = 0/0, ['end'] = {NOW} }}, {{ start = 1/0 }}, {{ start = -1/0, ['end'] = 5 }}, 7, 'text',"
        f" {{ start = 99999999999, ['end'] = 99999999999 + 60 }} }}"
    )
    companion = (
        f"{{ sessions = {{ {{ start = {NOW - 9000}, ['end'] = {NOW - 8000}, steps = 1200 }},"
        f" {{ start = -900, ['end'] = -100 }}, {{ start = {NOW - 500}, ['end'] = 1e300 }}, {{ start = 0/0, ['end'] = 1/0 }},"
        f" {{ start = 1e11, ['end'] = 1e11 + 600 }}, {{ start = {NOW}, ['end'] = {NOW} }} }} }}"
    )
    entries = merge(rt, own, companion)
    assert [(entry["start"], entry["measured"]) for entry in entries] == [(NOW - 3000, False), (NOW - 9000, True)]
    # Everything that is listed can be formatted - which is what the windows do with it.
    for entry in entries:
        assert rt.run("return date('%b %d %H:%M', ...)", entry["start"]) is not None
        assert rt.run("return date('%H:%M', ...)", entry["end"]) is not None
        assert entry["end"] >= entry["start"]


def test_the_epoch_itself_is_a_usable_start(rt):
    # 1970 is a strange day for a walk, but the game can show it.
    entries = merge(rt, "{ { start = 0, ['end'] = 600, steps = 10 } }")
    assert len(entries) == 1 and rt.run("return date('%Y', 0)") in ("1969", "1970")


def test_an_own_record_with_an_unusable_end_is_kept_as_a_moment(rt):
    entries = merge(rt, f"{{ {{ start = {NOW - 100}, ['end'] = 1e300, steps = 5 }}, {{ start = {NOW - 900}, ['end'] = 4 }},"
                        f" {{ start = {NOW - 1900} }} }}")
    assert [(entry["start"], entry["end"]) for entry in entries] == [
        (NOW - 100, NOW - 100), (NOW - 900, NOW - 900), (NOW - 1900, NOW - 1900),
    ]


def test_numbers_that_are_not_finite_do_not_spread(rt):
    own = (
        f"{{ {{ start = {NOW - 4000}, ['end'] = {NOW - 3000}, seconds = 0/0, steps = 1/0, distanceM = -1/0, kcal = 'x',"
        f" maxKmh = {{}}, yards = 1/0 }},"
        f" {{ start = {NOW - 2000}, ['end'] = {NOW - 1000}, seconds = 900, steps = 1500, distanceM = 700, kcal = 55.5,"
        f" yards = 4000, maxKmh = 3.1 }} }}"
    )
    companion = (
        f"{{ sessions = {{ {{ start = {NOW - 9000}, ['end'] = {NOW - 8000}, seconds = 1/0, steps = 0/0, avgHr = 0/0,"
        f" maxHr = 1/0, distanceM = 800 }}, {{ start = {NOW - 7000}, ['end'] = {NOW - 6000}, avgHr = '104', maxHr = 121.0 }} }} }}"
    )
    entries = merge(rt, own, companion)
    assert len(entries) == 4
    broken = entries[1]
    assert (broken["seconds"], broken["steps"], broken["distanceM"], broken["kcal"], broken["maxKmh"]) == (0, 0, 0, 0, 0)
    assert "yards" not in broken, "an unusable optional field is absent, not zero or infinite"
    assert "avgHr" not in entries[3] and "maxHr" not in entries[3]
    assert (entries[2]["avgHr"], entries[2]["maxHr"]) == (104, 121)

    totals = lua_plain(rt.run(
        "local log, entries, now = ... return log.totals(entries, now, nil, "
        "{ sessions = 0/0, steps = 1/0, seconds = -1/0, distanceM = 'far', kcal = {} })",
        rt.ns.SessionLog, rt.run(f"local log = ... return log.merge({own}, {companion})", rt.ns.SessionLog), NOW,
    ))
    assert totals["all"] == {"sessions": 4, "seconds": 900, "distanceM": 1500, "steps": 1500, "kcal": 55.5, "yards": 4000}
    assert totals["today"]["steps"] == 1500 and totals["best"] == {"steps": 1500, "distanceM": 800, "seconds": 900}


def test_sorting_survives_a_history_full_of_garbage(rt):
    # NaN in a sort key makes Lua's table.sort raise "invalid order function".
    records = ", ".join(
        f"{{ start = {value}, ['end'] = {value} + 600 }}"
        for value in ["0/0", str(NOW - 5), "0/0", "1/0", str(NOW - 900), "-1/0", "0/0", str(NOW - 50), "-3", "0/0", str(NOW - 7)] * 6
    )
    entries = merge(rt, "{" + records + "}", "{ sessions = {" + records + "} }")
    starts = [entry["start"] for entry in entries]
    assert starts == sorted(starts, reverse=True) and len(starts) == 4 * 6


def test_totals_do_not_fail_on_an_entry_without_a_calendar_day(rt):
    """Entries normally come from merge(), which leaves such records out;
    totals() must still not raise if handed one."""
    result = lua_plain(rt.run(
        "local log, now = ... return log.totals({ { start = 1e300, steps = 100, seconds = 60 }, "
        "{ start = -5, steps = 50 }, { start = now - 60, steps = 7 } }, now)",
        rt.ns.SessionLog, NOW,
    ))
    assert result["all"]["steps"] == 157 and result["today"]["steps"] == 7 and result["streak"] == 1
    assert rt.run("local log = ... return log.dayKey(1e300), log.dayKey(-1)", rt.ns.SessionLog) == ("0000-00-00", "0000-00-00")
    assert rt.run("local log, now = ... return log.dayKey(now)", rt.ns.SessionLog, NOW) == "2026-11-17"


def test_a_damaged_history_does_not_raise_at_login_on_windows(make_rt):
    """Before: 'table index is nil' in SessionLog.totals from the HUD's
    first redraw - and again on every redraw after it."""
    saved = "{sessions={{start=1e12,['end']=1e12+5,seconds=5,steps=3},{start=-7,['end']=5}}}"
    rt = make_rt(saved_variables={"WalkaDB": saved}, state={"clock": NOW})
    rt.tick(20)
    assert lua_list(rt.walka.GetHistory(rt.walka)) == []
    assert lua_plain(rt.walka.GetTotals(rt.walka))["all"]["sessions"] == 0


# ---- companion data timestamp ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, usable",
    [
        ("1795000000", True), ("'1795000000'", True), ("0", True), ("nil", False), ("'yesterday'", False), ("-5", False),
        ("0/0", False), ("1/0", False), ("1e300", False), ("99999999999", False), ("{}", False), ("true", False),
    ],
)
def test_sync_time_is_only_reported_if_the_game_can_format_it(make_rt, written, usable):
    rt = make_rt(files={"CompanionData.lua": f"WalkaCompanionData = {{ written = {written} }}"})
    synced = rt.walka.GetSyncTime(rt.walka)
    assert (synced is not None) is usable
    if usable:
        assert rt.run("return date('%Y-%m-%d %H:%M', ...)", synced) is not None
    rt.clear_printed()
    rt.slash("/walka status")
    assert any(("companion data from" in line) is usable for line in rt.printed[1:])
    assert any(("no companion data yet" in line) is not usable for line in rt.printed[1:])


def test_sync_time_without_companion_data(rt):
    assert rt.walka.GetSyncTime(rt.walka) is None


# ---- the target right after a start ------------------------------------------------------------------


def test_target_is_known_as_soon_as_a_session_starts(rt):
    walka = rt.walka
    seen = []
    walka.On(walka, "SESSION", lambda *args: seen.append((str(args[0]), walka.GetLiveSpeed(walka)[1])))
    rt.set(speed=7.0)
    rt.tick(3)
    rt.slash("/walka start")
    # The windows redraw on this event - before any tick. They must see the
    # pace the belt is about to be asked for (a session starts out at the
    # standing pace, on the belt's 0.1 mph grid), not a stopped belt.
    standing = nearest_step(rt.ns.SpeedModel.DEFAULTS.standKmh, MPH_STEP)
    assert standing >= 0.6 * KMH_PER_MPH
    assert seen == [("start", pytest.approx(standing, abs=1e-6))]
    primed = walka.GetLiveSpeed(walka)[1]
    rt.tick(1)
    sent = beacon.cells_to_frame(rt.beacon_cells()).target_kmh
    assert sent == pytest.approx(primed, abs=0.17), "the first frame asks for (about) what was announced"

    rt.tick(100)
    rt.slash("/walka pause")
    assert seen[-1] == ("pause", 0)
    rt.tick(50)
    rt.slash("/walka resume")
    assert seen[-1][0] == "resume" and seen[-1][1] >= 0.6 * KMH_PER_MPH - 0.01


def test_no_target_is_announced_when_the_belt_will_stay_stopped(rt):
    walka = rt.walka
    walka.Set(walka, "speed.standKmh", 0)
    rt.set(speed=0)
    rt.tick(3)
    rt.slash("/walka start")
    assert walka.GetLiveSpeed(walka) == (0, 0)
    rt.tick(50)
    assert walka.GetLiveSpeed(walka) == (0, 0)


def test_afk_resume_also_knows_its_target(rt):
    walka = rt.walka
    seen = []
    walka.On(walka, "SESSION", lambda *args: seen.append((str(args[0]), walka.GetLiveSpeed(walka)[1])))
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.set(afk=True, speed=0)
    rt.tick(20)
    rt.set(afk=False)                       # back, but not moving yet: still paused
    rt.tick(20)
    assert [name for name, _ in seen] == ["start", "pause"]
    assert seen[-1] == ("pause", 0)
    rt.set(speed=7.0)                       # the character moves: the session resumes
    rt.tick(1)
    assert [name for name, _ in seen] == ["start", "pause", "resume"]
    assert seen[-1][1] >= 0.6 * KMH_PER_MPH - 0.01


def test_starting_while_falling_or_on_a_flight_path(rt):
    # Odd moments to start, but the zero-length model update must cope.
    walka = rt.walka
    for state in ({"falling": True, "speed": 7.0}, {"taxi": True, "speed": 30.0}, {"dead": True, "speed": 0}):
        rt.set(falling=False, taxi=False, dead=False)
        rt.set(**state)
        rt.tick(2)
        rt.slash("/walka start")
        target = walka.GetLiveSpeed(walka)[1]
        limits = lua_dict(walka.GetBeltLimits(walka))
        assert target == 0 or limits["minKmh"] - 1e-6 <= target <= limits["maxKmh"] + 1e-6
        rt.tick(30)
        rt.slash("/walka stop")


# ---- totals from a merged history ------------------------------------------------------------------------


COMPANION = f"""
WalkaCompanionData = {{
    written = {NOW - 600}, profile = {{ weightKg = 80, heightCm = 178 }},
    sessions = {{ {{ start = {NOW - 5000}, ["end"] = {NOW - 3200}, seconds = 1790, distanceM = 1502, steps = 2710, kcal = 118.4 }} }},
    older = {{ sessions = 12, seconds = 36000, distanceM = 30000, steps = 50000, kcal = 2400.5 }},
}}
"""


def test_totals_can_be_computed_from_a_history_already_merged(make_rt):
    saved = f"{{sessions={{{{id=1,start={NOW - 20000},['end']={NOW - 19000},seconds=900,distanceM=700,steps=1100,kcal=50,yards=500}}}}}}"
    rt = make_rt(files={"CompanionData.lua": COMPANION}, saved_variables={"WalkaDB": saved}, state={"clock": NOW})
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    count = rt.lua.eval(
        "function(log) local real, calls = log.merge, 0 "
        "log.merge = function(...) calls = calls + 1 return real(...) end "
        "return function() return calls end end"
    )(rt.ns.SessionLog)
    history = walka.GetHistory(walka)
    assert count() == 1
    with_list = lua_plain(walka.GetTotals(walka, history))
    assert count() == 1, "passing the merged list saves the second merge"
    assert with_list == lua_plain(walka.GetTotals(walka))
    assert count() == 2
    assert with_list["all"]["sessions"] == 2 + 12 + 1, "listed walks, the older summary and the session in progress"


# ---- garbage on the tick ----------------------------------------------------------------------------------


def test_packing_a_frame_allocates_nothing(rt):
    """Ten frames a second, for hours: the packer must not leave a table
    behind each time."""
    grown = rt.run(
        "local protocol = ... "
        "local frame, out = { seq = 0, session = 3, flags = 5, target = 320, charSpeed = 350 }, {} "
        "protocol.packBits(frame, out) "
        "collectgarbage('collect') collectgarbage('collect') collectgarbage('stop') "
        "local before = collectgarbage('count') "
        "for i = 1, 2000 do frame.seq = i % 256 frame.target = i protocol.packBits(frame, out) end "
        "local after = collectgarbage('count') "
        "collectgarbage('restart') "
        "return (after - before) * 1024",
        rt.ns.Protocol,
    )
    assert grown == 0, f"packBits left {grown:.0f} bytes of garbage in 2000 calls"


def test_packing_still_gives_independent_results(rt):
    protocol = rt.ns.Protocol
    first = lua_list(protocol.packBytes(rt.to_lua({"seq": 1, "session": 2, "flags": 3, "target": 100, "charSpeed": 350})))
    second = lua_list(protocol.packBytes(rt.to_lua({"seq": 9})))
    again = lua_list(protocol.packBytes(rt.to_lua({"seq": 1, "session": 2, "flags": 3, "target": 100, "charSpeed": 350})))
    assert first == again and first != second
    assert second[2:8] == [0] * 6, "fields left out of one frame do not inherit the previous frame's values"


def test_the_companions_profile_is_not_rebuilt_on_every_tick(make_rt):
    rt = make_rt(files={"CompanionData.lua": COMPANION})
    walka = rt.walka
    first = walka.GetProfile(walka)
    assert lua_dict(first) == {"weightKg": 80, "heightCm": 178, "fromCompanion": True}
    assert rt.lua.globals().rawequal(first, walka.GetProfile(walka)), "one table, refilled - not a new one per call"
    # It still follows what it is made from.
    rt.run("WalkaCompanionData.profile.heightCm = nil")
    walka.Set(walka, "profile.heightCm", 190)
    assert lua_dict(walka.GetProfile(walka)) == {"weightKg": 80, "heightCm": 190, "fromCompanion": True}
    rt.run("WalkaCompanionData.profile.weightKg = 'unknown'")
    assert lua_dict(walka.GetProfile(walka)) == {"weightKg": 75, "heightCm": 190}, "back to the addon's own profile"


# ---- date formats -----------------------------------------------------------------------------------------


def test_date_formats_use_only_conversions_every_c_library_has(addon_dir):
    """date() hands its format to the C library. On Windows - where the
    game runs - an unknown conversion (%e, %T, %F ...) is a fatal error in
    the runtime, not a wrong string. This reads the source, so that formats
    on paths the other tests never reach are covered too."""
    import re

    found = []
    for path in sorted(addon_dir.rglob("*.lua")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""\bdate\(\s*(["'])(.*?)\1""", text):
            pattern = match.group(2)
            found.append(pattern)
            body = pattern[1:] if pattern.startswith("!") else pattern
            if body.startswith("*t"):
                continue
            for specifier in re.findall(r"%(.?)", body):
                assert specifier and specifier in "aAbBcdHIjmMpSUwWxXyYZ%", (
                    f"{path.name}: date format {pattern!r} uses %{specifier}"
                )
    assert len(found) >= 8, "the scan no longer finds the addon's date() calls"
