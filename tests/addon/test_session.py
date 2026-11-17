"""The walking session: start, pause, resume, stop, AFK, /reload, history."""

from __future__ import annotations

import pytest
from walka import beacon
from walka.protocol import Flags

from .harness import lua_dict, lua_list, lua_plain

KMH_PER_MPH = 1.609344


def session(rt):
    current = rt.walka.GetSession(rt.walka)
    return None if current is None else lua_dict(current)


def history(rt):
    return [lua_dict(record) for record in lua_list(rt.db.sessions)]


def live_speed(rt):
    return rt.walka.GetLiveSpeed(rt.walka)


def flags(rt):
    return beacon.cells_to_frame(rt.beacon_cells()).flags


def collect_events(rt):
    """Subscribe to the addon's callbacks; returns the list they append to."""
    events = []
    for name in ("SESSION", "HISTORY", "SETTINGS", "TICK"):
        rt.walka.On(rt.walka, name, lambda *args, name=name: events.append((name, *[a for a in args if isinstance(a, str)])))
    return events


# ---- a whole session ------------------------------------------------------------------


def test_full_session_lifecycle(rt):
    events = collect_events(rt)
    walka = rt.walka
    assert not walka.IsActive(walka)
    assert session(rt) is None

    rt.set(speed=7.0)
    rt.slash("/walka start")
    assert walka.IsActive(walka) and not walka.IsPaused(walka)
    assert ("SESSION", "start") in events
    started = session(rt)
    assert started["id"] == 1 and started["character"] == "Tester-Azeroth"
    assert started["start"] == rt.game.clock
    assert any("session started" in line for line in rt.printed)

    # The belt estimate: countdown, then a ramp towards the target.
    belts, targets, samples = [], [], []
    for _ in range(600):
        rt.tick()
        belt, target = live_speed(rt)
        belts.append(belt)
        targets.append(target)
        samples.append(session(rt))
    assert set(belts[:28]) == {0}, "the treadmill counts down for three seconds first"
    assert belts[40] == pytest.approx(0.6 * KMH_PER_MPH + 0.5 * 1.0, abs=0.3)
    assert belts == sorted(belts)
    assert belts[-1] == pytest.approx(2.0 * KMH_PER_MPH, abs=0.01)
    assert targets[-1] == pytest.approx(2.0 * KMH_PER_MPH, abs=0.01)
    assert all(belt <= target + 1e-9 for belt, target in zip(belts, targets, strict=True))
    # Steps, distance, calories and time only ever grow.
    for key in ("seconds", "distanceM", "steps", "kcal", "yards"):
        values = [sample[key] for sample in samples]
        assert values == sorted(values), key
        assert values[-1] > values[100] > 0, key
    after_minute = session(rt)
    assert after_minute["seconds"] == pytest.approx(57, abs=0.5)        # 60 s less the countdown
    assert after_minute["yards"] == pytest.approx(7.0 * 60, rel=0.01)
    assert 35 <= after_minute["distanceM"] <= 55                        # ~57 s at up to 2 mph
    assert 70 <= after_minute["steps"] <= 100
    assert 2.5 <= after_minute["kcal"] <= 4.5
    assert after_minute["maxKmh"] == pytest.approx(2.0 * KMH_PER_MPH, abs=0.01)

    # Pause: the target drops at once, the belt estimate coasts to zero.
    rt.slash("/walka pause")
    assert walka.IsPaused(walka)
    assert ("SESSION", "pause") in events
    assert live_speed(rt)[1] == 0
    rt.tick(30)
    assert live_speed(rt) == (0, 0)
    assert flags(rt) & Flags.PAUSED
    frozen = session(rt)
    rt.tick(100)
    assert session(rt) == frozen, "nothing accumulates while paused"

    # Resume: countdown again, then walking again.
    rt.slash("/walka resume")
    assert not walka.IsPaused(walka)
    assert ("SESSION", "resume") in events
    rt.tick(20)
    assert live_speed(rt)[0] == 0 and live_speed(rt)[1] > 0
    rt.tick(280)
    assert live_speed(rt)[0] == pytest.approx(2.0 * KMH_PER_MPH, abs=0.01)
    assert session(rt)["seconds"] > frozen["seconds"] + 20

    # Stop: one record in the saved history.
    before_stop = session(rt)
    rt.clear_printed()
    rt.slash("/walka stop")
    assert not walka.IsActive(walka) and session(rt) is None
    assert ("SESSION", "stop") in events and ("HISTORY",) in events
    assert live_speed(rt) == (0, 0)
    records = history(rt)
    assert len(records) == 1
    record = records[0]
    assert record["id"] == 1
    assert record["start"] == started["start"]
    assert record["end"] == rt.game.clock
    assert record["end"] - record["start"] == pytest.approx(103, abs=1)
    assert record["seconds"] == round(before_stop["seconds"])
    assert record["distanceM"] == round(before_stop["distanceM"])
    assert record["steps"] == round(before_stop["steps"])
    assert record["kcal"] == pytest.approx(before_stop["kcal"], abs=0.051)
    assert record["yards"] == round(before_stop["yards"])
    assert record["maxKmh"] == pytest.approx(2.0 * KMH_PER_MPH, abs=0.01)
    assert record["character"] == "Tester-Azeroth"
    # Everything stored is a whole number except kcal (one decimal) and maxKmh (two).
    for key in ("seconds", "distanceM", "steps", "yards", "start", "end"):
        assert record[key] == int(record[key]), key
    assert record["kcal"] * 10 == pytest.approx(round(record["kcal"] * 10))
    assert any("session finished" in line for line in rt.printed)
    assert rt.find_secrets() == []


def test_standing_still_walks_at_the_standing_pace(rt):
    rt.slash("/walka start")
    rt.tick(200)
    belt, target = live_speed(rt)
    assert target == pytest.approx(1.0 * KMH_PER_MPH, abs=0.01)
    assert belt == pytest.approx(target)
    assert session(rt)["yards"] == 0
    assert session(rt)["steps"] > 10


def test_short_sessions_are_discarded(rt):
    events = collect_events(rt)
    rt.slash("/walka start")
    rt.tick(200)                    # 20 s on the clock, but only ~17 s of belt time
    assert session(rt)["seconds"] < rt.walka.MIN_SESSION_SECONDS
    rt.clear_printed()
    rt.slash("/walka stop")
    assert history(rt) == []
    assert ("HISTORY",) not in events
    assert ("SESSION", "stop") in events
    assert any("too short" in line for line in rt.printed)


def test_a_session_just_long_enough_is_kept(rt):
    rt.slash("/walka start")
    rt.tick(235)
    assert session(rt)["seconds"] >= rt.walka.MIN_SESSION_SECONDS
    rt.slash("/walka stop")
    assert len(history(rt)) == 1


def test_stop_without_a_session_says_so(rt):
    rt.slash("/walka stop")
    assert any("no session" in line for line in rt.printed)
    assert rt.walka.StopSession(rt.walka) is None
    assert history(rt) == []


def test_start_twice_and_pause_resume_out_of_order_are_harmless(rt):
    events = collect_events(rt)
    walka = rt.walka
    walka.PauseSession(walka)               # nothing to pause
    walka.ResumeSession(walka)              # nothing to resume
    assert events == []
    rt.slash("/walka start")
    rt.slash("/walka start")                # already running: no second session
    assert session(rt)["id"] == 1
    assert events.count(("SESSION", "start")) == 1
    rt.slash("/walka resume")               # not paused
    assert events.count(("SESSION", "resume")) == 0
    rt.slash("/walka pause")
    rt.slash("/walka pause")
    assert events.count(("SESSION", "pause")) == 1
    rt.slash("/walka start")                # start while paused resumes
    assert not walka.IsPaused(walka)
    assert events.count(("SESSION", "resume")) == 1


def test_toggle_pause_cycles_start_pause_resume(rt):
    walka = rt.walka
    walka.TogglePause(walka)
    assert walka.IsActive(walka) and not walka.IsPaused(walka)
    walka.TogglePause(walka)
    assert walka.IsPaused(walka)
    walka.TogglePause(walka)
    assert walka.IsActive(walka) and not walka.IsPaused(walka)


def test_session_ids_run_from_1_to_255_and_never_0(rt):
    rt.db.sessionCounter = 254
    seen = []
    for _ in range(3):
        rt.slash("/walka start")
        seen.append(session(rt)["id"])
        rt.slash("/walka stop")
    assert seen == [255, 1, 2]              # 0 means "no session" in the beacon


def test_announcements_can_be_turned_off(rt):
    rt.walka.Set(rt.walka, "announce", False)
    rt.clear_printed()
    rt.slash("/walka start")
    rt.slash("/walka pause")
    rt.slash("/walka resume")
    rt.tick(300)
    rt.slash("/walka stop")
    assert rt.printed == []
    assert len(history(rt)) == 1


# ---- AFK ------------------------------------------------------------------------------


def test_afk_pauses_and_coming_back_resumes(rt):
    events = collect_events(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.set(afk=True, speed=0)
    rt.tick()
    assert rt.walka.IsPaused(rt.walka)
    assert session(rt)["pauseReason"] == "afk"
    assert ("SESSION", "pause") in events
    assert any("AFK" in line for line in rt.printed)
    assert flags(rt) & Flags.PAUSED and flags(rt) & Flags.AFK
    rt.tick(100)
    assert live_speed(rt) == (0, 0)

    rt.set(afk=False, speed=7.0)
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert session(rt).get("pauseReason") is None
    assert ("SESSION", "resume") in events
    assert not flags(rt) & Flags.PAUSED


def test_a_pause_by_hand_is_not_undone_by_afk_ending(rt):
    rt.slash("/walka start")
    rt.slash("/walka pause")
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "user"
    rt.set(afk=False)
    rt.tick(5)
    assert rt.walka.IsPaused(rt.walka), "only an AFK pause resumes by itself"


def test_afk_pause_can_be_switched_off(rt):
    rt.walka.Set(rt.walka, "autoPauseAFK", False)
    rt.slash("/walka start")
    rt.set(afk=True)
    rt.tick(20)
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) & Flags.AFK and not flags(rt) & Flags.PAUSED


def test_no_afk_handling_without_a_session(rt):
    rt.set(afk=True)
    rt.tick(5)
    assert not rt.walka.IsActive(rt.walka)
    assert rt.printed == []


# ---- /reload ----------------------------------------------------------------------------


def test_reload_in_mid_session_restores_it(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(600)
    before = session(rt)
    belt_before, target_before = live_speed(rt)

    fresh = rt.reload()
    assert fresh.walka.IsActive(fresh.walka)
    restored = session(fresh)
    assert restored == before
    # The belt did not stop for the reload, so neither does the estimate.
    belt, _ = live_speed(fresh)
    assert belt == pytest.approx(belt_before)
    assert fresh.db.active is None, "the saved copy is consumed"
    fresh.tick()
    assert live_speed(fresh)[1] == pytest.approx(target_before)
    assert flags(fresh) == Flags.ACTIVE | Flags.MOVING
    assert beacon.cells_to_frame(fresh.beacon_cells()).session == before["id"]
    fresh.tick(100)
    assert session(fresh)["seconds"] == pytest.approx(before["seconds"] + 10, abs=0.3)
    fresh.slash("/walka stop")
    assert len(history(fresh)) == 1
    assert history(fresh)[0]["start"] == before["start"]


def test_reload_keeps_a_paused_session_paused(rt):
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka pause")
    rt.tick(30)
    fresh = rt.reload()
    assert fresh.walka.IsPaused(fresh.walka)
    assert session(fresh)["pauseReason"] == "user"
    fresh.tick(50)
    assert live_speed(fresh) == (0, 0)
    assert flags(fresh) & Flags.PAUSED
    fresh.slash("/walka resume")
    fresh.tick(100)
    assert live_speed(fresh)[0] > 0


def test_logging_back_in_within_the_window_resumes(rt):
    rt.slash("/walka start")
    rt.tick(300)
    fresh = rt.reload(gap=rt.walka.RESUME_WINDOW_SECONDS - 5)
    assert fresh.walka.IsActive(fresh.walka)


def test_a_stale_session_is_closed_into_the_history(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(600)
    before = session(rt)
    logout_time = rt.game.clock

    fresh = rt.reload(gap=rt.walka.RESUME_WINDOW_SECONDS + 60)      # logged out for four minutes
    assert not fresh.walka.IsActive(fresh.walka)
    assert fresh.db.active is None
    records = history(fresh)
    assert len(records) == 1
    assert records[0]["end"] == int(logout_time), "it ended when the game closed, not now"
    assert records[0]["seconds"] == round(before["seconds"])
    assert live_speed(fresh) == (0, 0)
    fresh.tick()
    assert flags(fresh) == Flags.MOVING


def test_a_stale_session_that_was_too_short_just_disappears(rt):
    rt.slash("/walka start")
    rt.tick(50)
    fresh = rt.reload(gap=1000)
    assert not fresh.walka.IsActive(fresh.walka)
    assert history(fresh) == []


def test_reload_without_a_session_leaves_nothing_behind(rt):
    rt.tick(10)
    fresh = rt.reload()
    assert fresh.db.active is None
    assert not fresh.walka.IsActive(fresh.walka)
    assert fresh.find_secrets() == []


def test_settings_and_history_survive_reloads(rt):
    rt.slash("/walka weight 82kg")
    rt.slash("/walka units kmh")
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    fresh = rt.reload().reload(gap=5000)
    assert fresh.db.settings.profile.weightKg == 82
    assert fresh.db.settings.units == "kmh"
    assert len(history(fresh)) == 1
    assert fresh.db.version == 1


def test_saved_variables_from_an_older_or_damaged_file_are_repaired(make_rt):
    rt = make_rt(saved_variables={"WalkaDB": '{settings={units="kmh",speed="oops",hud=false},sessions=7,sessionCounter="x"}'})
    settings = lua_plain(rt.db.settings)
    assert settings["units"] == "kmh"                       # kept
    assert settings["speed"]["runKmh"] == 3.2               # rebuilt from defaults
    assert settings["hud"]["shown"] is True
    assert settings["profile"] == {"weightKg": 75, "heightCm": 172}
    assert lua_plain(rt.db.sessions) in ([], {})
    assert rt.db.sessionCounter == 0
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    assert len(history(rt)) == 1


@pytest.mark.parametrize("saved", ["7", '"text"', "false", "{}"])
def test_any_saved_value_loads(make_rt, saved):
    rt = make_rt(saved_variables={"WalkaDB": saved})
    assert rt.db.settings.units in ("mph", "kmh")
    rt.tick(3)


# ---- history size -------------------------------------------------------------------------


def test_history_is_capped(make_rt):
    cap = 400
    records = ",".join(
        f"{{id={index % 255 + 1},start={1700000000 + index * 4000},['end']={1700000000 + index * 4000 + 1800},"
        f"seconds=1800,distanceM=1400,steps=2500,kcal=100,yards=9000,maxKmh=3.2,character='Old-Realm'}}"
        for index in range(cap)
    )
    rt = make_rt(saved_variables={"WalkaDB": "{sessions={" + records + "}}"})
    assert rt.walka.MAX_SESSIONS == cap
    assert len(history(rt)) == cap
    oldest_start = history(rt)[0]["start"]

    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    after = history(rt)
    assert len(after) == cap
    assert after[0]["start"] == oldest_start + 4000, "the oldest record made room"
    assert after[-1]["character"] == "Tester-Azeroth"


def test_clear_history(rt):
    events = collect_events(rt)
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    rt.walka.ClearHistory(rt.walka)
    assert history(rt) == []
    assert events.count(("HISTORY",)) == 2
    assert lua_list(rt.walka.GetHistory(rt.walka)) == []


# ---- the tick ---------------------------------------------------------------------------------


def test_a_long_hitch_is_not_integrated_as_one_giant_step(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    before = session(rt)
    rt.frame(30.0)                  # a loading screen
    after = session(rt)
    assert after["seconds"] - before["seconds"] <= 1.0 + 1e-9
    assert after["yards"] - before["yards"] <= 7.0 + 1e-9


@pytest.mark.parametrize("fps", [15, 60, 144])
def test_frame_rate_does_not_change_the_result(make_rt, fps):
    rt = make_rt()
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(60, step=1 / fps)
    current = session(rt)
    assert current["seconds"] == pytest.approx(57, abs=0.6)
    assert current["yards"] == pytest.approx(420, rel=0.01)
    assert 35 <= current["distanceM"] <= 55


def test_tick_callback_fires_about_four_times_a_second(rt):
    events = collect_events(rt)
    rt.tick(100)
    assert 30 <= events.count(("TICK",)) <= 45


def test_an_error_in_a_subscriber_reaches_the_error_handler_and_does_not_stop_the_tick(rt):
    from .harness import AddonError

    def broken(*_):
        raise RuntimeError("boom")

    rt.walka.On(rt.walka, "TICK", rt.lua.eval("function() error('boom from a window') end"))
    with pytest.raises(AddonError, match="boom from a window"):
        rt.tick(10)
    # The beacon kept going regardless.
    assert beacon.cells_to_frame(rt.beacon_cells()).seq == 10


def test_pausing_by_hand_during_an_afk_pause_makes_it_stick(rt):
    # Away from the keyboard the session pauses itself. If the player then
    # pauses it explicitly, coming back must not restart the belt.
    rt.slash("/walka start")
    rt.tick(50)
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "afk"
    rt.slash("/walka pause")
    assert session(rt)["pauseReason"] == "user"
    rt.set(afk=False)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert live_speed(rt) == (0, 0)
    rt.slash("/walka resume")
    assert not rt.walka.IsPaused(rt.walka)


# ---- formatting helpers (used by every window) ----------------------------------------------------


def test_format_helpers_in_mph(rt):
    walka = rt.walka
    assert walka.FormatSpeed(walka, 3.218688) == "2.0 mph"
    assert walka.FormatSpeed(walka, 3.218688, True) == "2.0"
    assert walka.FormatSpeed(walka, None) == "0.0 mph"
    assert walka.FormatDistance(walka, 1609.344) == "1.00 mi"
    assert walka.FormatDistance(walka, None) == "0.00 mi"
    assert walka.ToKmh(walka, 2.0) == pytest.approx(3.218688)
    assert walka.FromKmh(walka, 3.218688) == pytest.approx(2.0)
    assert walka.SpeedUnit(walka) == "mph"


def test_format_helpers_in_kmh(rt):
    walka = rt.walka
    walka.Set(walka, "units", "kmh")
    assert walka.FormatSpeed(walka, 3.25) in ("3.2 km/h", "3.3 km/h")
    assert walka.FormatSpeed(walka, 4.0) == "4.0 km/h"
    assert walka.FormatDistance(walka, 1234) == "1.23 km"
    assert walka.ToKmh(walka, 2.0) == 2.0 and walka.FromKmh(walka, 2.0) == 2.0
    assert walka.SpeedUnit(walka) == "km/h"


@pytest.mark.parametrize(
    "seconds, text",
    [(0, "0:00"), (None, "0:00"), (5, "0:05"), (59.4, "0:59"), (59.5, "1:00"), (61, "1:01"), (3599, "59:59"),
     (3599.6, "1:00:00"), (3600, "1:00:00"), (3725, "1:02:05"), (86400 + 61, "24:01:01")],
)
def test_format_duration(rt, seconds, text):
    assert rt.walka.FormatDuration(rt.walka, seconds) == text


@pytest.mark.parametrize(
    "value, text",
    [(0, "0"), (None, "0"), (7, "7"), (999, "999"), (999.5, "1,000"), (1000, "1,000"), (12345, "12,345"),
     (1234567.4, "1,234,567"), (-1234, "-1,234"), (-999, "-999"), (100000, "100,000")],
)
def test_format_number(rt, value, text):
    assert rt.walka.FormatNumber(rt.walka, value) == text


def test_set_and_get_by_path(rt):
    walka = rt.walka
    events = collect_events(rt)
    walka.Set(walka, "speed.runKmh", 3.5)
    assert walka.Get(walka, "speed.runKmh") == 3.5
    assert rt.db.settings.speed.runKmh == 3.5
    assert walka.Get(walka, "speed") is not None
    assert walka.Get(walka, "no.such.setting") is None
    assert walka.Get(walka, "units.deeper") is None
    walka.Set(walka, "brand.new.key", 1)
    assert walka.Get(walka, "brand.new.key") == 1
    assert events.count(("SETTINGS",)) == 2
    walka.ResetSpeedSettings(walka)
    assert walka.Get(walka, "speed.runKmh") == 3.2
    assert lua_plain(rt.db.settings.speed) == lua_plain(rt.ns.SpeedModel.DEFAULTS)
    # The defaults table itself must not be handed out for editing.
    walka.Set(walka, "speed.runKmh", 9.9)
    assert rt.ns.SpeedModel.DEFAULTS.runKmh == 3.2


def test_settings_reach_the_speed_model_live(rt):
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka units kmh")
    rt.slash("/walka start")
    rt.tick(300)
    assert live_speed(rt)[1] == pytest.approx(3.2)
    walka.Set(walka, "speed.runKmh", 2.5)
    rt.tick(600)
    assert live_speed(rt)[1] == pytest.approx(2.5)
    walka.Set(walka, "speed.maxKmh", 2.0)
    rt.tick(5)
    assert live_speed(rt)[1] <= 2.0 + 1e-9
