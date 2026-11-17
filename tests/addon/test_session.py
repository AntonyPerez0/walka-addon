"""The walking session: start, pause, resume, stop, AFK, /reload, history."""

from __future__ import annotations

import pytest
from walka import beacon
from walka.protocol import Flags

from .conftest import KMH_PER_MPH, nearest_step
from .harness import lua_dict, lua_list, lua_plain


def pace(rt, key: str) -> float:
    """What a session settles at for one of the paces of the speed mapping
    ("standKmh", "runKmh", ...) as it is set right now: the pace on the
    treadmill's speed grid (0.1 mph unless a test changes the units)."""
    walka = rt.walka
    return nearest_step(walka.Get(walka, "speed." + key), walka.GetBeltLimits(walka).stepKmh)


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
    running, standing = pace(rt, "runKmh"), pace(rt, "standKmh")
    assert running > standing + 1.0, "running asks for a clearly faster belt than standing"
    assert set(belts[:28]) == {0}, "the treadmill counts down for three seconds first"
    accel = rt.ns.Metrics.Estimator.DEFAULTS.accelKmhPerS
    assert belts[40] == pytest.approx(0.6 * KMH_PER_MPH + accel * 1.0, abs=0.1)
    assert belts == sorted(belts)
    assert belts[-1] == pytest.approx(running, abs=1e-6)
    assert targets[0] == pytest.approx(standing, abs=1e-6), "the target starts at the standing pace"
    assert targets == sorted(targets) and len(set(round(target, 6) for target in targets)) > 3, "and ramps up"
    assert targets[-1] == pytest.approx(running, abs=1e-6)
    assert all(belt <= target + 1e-9 for belt, target in zip(belts, targets, strict=True))
    # Steps, distance, calories and time only ever grow.
    for key in ("seconds", "distanceM", "steps", "kcal", "yards"):
        values = [sample[key] for sample in samples]
        assert values == sorted(values), key
        assert values[-1] > values[100] > 0, key
    after_minute = session(rt)
    assert after_minute["seconds"] == pytest.approx(57, abs=0.5)        # 60 s less the countdown
    assert after_minute["yards"] == pytest.approx(7.0 * 60, rel=0.01)
    # ~57 s of belt time: about ten seconds of getting up to speed, the
    # rest at the running pace. So a little less than a minute at that pace
    # would give - and more than a minute at the standing pace.
    metrics = rt.ns.Metrics
    profile = metrics.DEFAULT_PROFILE
    for key, per_minute in [
        ("distanceM", lambda kmh: kmh / 3.6 * 60),
        ("steps", lambda kmh: metrics.stepsPerMinute(kmh, profile.heightCm)),
        ("kcal", lambda kmh: metrics.kcalPerMinute(kmh, profile.weightKg)),
    ]:
        at_running_pace = per_minute(running) * after_minute["seconds"] / 60
        assert 0.85 * at_running_pace <= after_minute[key] < at_running_pace, key
        assert after_minute[key] > per_minute(standing) * after_minute["seconds"] / 60, key
    assert after_minute["maxKmh"] == pytest.approx(running, abs=1e-6)

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
    assert live_speed(rt)[0] == pytest.approx(running, abs=1e-6)
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
    assert record["maxKmh"] == pytest.approx(running, abs=0.005)        # stored to two decimals
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
    assert target == pytest.approx(pace(rt, "standKmh"), abs=1e-6)
    assert target >= rt.walka.GetBeltLimits(rt.walka).minKmh, "a pace the belt can hold"
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


AFK_PAUSED = Flags.ACTIVE | Flags.PAUSED | Flags.AFK


def afk_paused_session(rt, events=None):
    """A session that paused itself because the player went AFK."""
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.clear_printed()
    rt.set(afk=True, speed=0)
    rt.tick()
    assert rt.walka.IsPaused(rt.walka)
    assert session(rt)["pauseReason"] == "afk"
    if events is not None:
        assert events.count(("SESSION", "pause")) == 1
    assert rt.printed == [
        rt.walka.PREFIX + "you are AFK - session paused. It resumes when your character moves."
    ]
    assert flags(rt) == AFK_PAUSED
    rt.tick(100)
    assert live_speed(rt) == (0, 0)
    assert flags(rt) == AFK_PAUSED


def test_afk_pauses_and_moving_again_resumes(rt):
    events = collect_events(rt)
    afk_paused_session(rt, events)

    rt.set(afk=False, speed=7.0)            # back, and off again at once
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert session(rt).get("pauseReason") is None
    assert events.count(("SESSION", "resume")) == 1
    assert any("session resumed" in line for line in rt.printed)
    assert flags(rt) == Flags.ACTIVE | Flags.MOVING
    assert live_speed(rt)[1] > 0


def test_afk_ending_alone_does_not_resume_the_session(rt):
    """Back at the keyboard, but the character stands still - typing a chat
    line, say. That must not start the belt: the session stays paused, and
    the beacon goes on calling it the automatic (AFK) pause, although the
    game's own AFK state is gone."""
    events = collect_events(rt)
    afk_paused_session(rt, events)
    frozen = session(rt)

    rt.set(afk=False)                       # the first key press clears AFK
    for _ in range(300):
        rt.tick()
        assert rt.walka.IsPaused(rt.walka)
        assert flags(rt) == AFK_PAUSED, "still paused, still flagged as the AFK pause"
        assert beacon.cells_to_frame(rt.beacon_cells()).target_raw == 0
    assert session(rt) == frozen and frozen["pauseReason"] == "afk"
    assert live_speed(rt) == (0, 0)
    assert ("SESSION", "resume") not in events
    assert not any("resumed" in line for line in rt.printed)
    # Things that are not the character moving leave it paused as well.
    rt.set(combat=True, resting=True, mounted=True)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert flags(rt) == AFK_PAUSED | Flags.COMBAT | Flags.RESTING | Flags.MOUNTED


def test_the_session_resumes_when_the_character_moves_after_afk(rt):
    events = collect_events(rt)
    afk_paused_session(rt, events)
    rt.set(afk=False)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka)

    rt.set(speed=2.5)                       # a few steps are enough
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert session(rt).get("pauseReason") is None
    assert events.count(("SESSION", "resume")) == 1
    assert any("session resumed" in line for line in rt.printed)
    assert flags(rt) == Flags.ACTIVE | Flags.MOVING, "neither PAUSED nor AFK any more"
    assert live_speed(rt)[1] > 0
    # ... and it is an ordinary session again: it follows the character,
    rt.tick(300)
    belt, target = live_speed(rt)
    assert belt == pytest.approx(target) and target == pytest.approx(pace(rt, "walkKmh"), abs=1e-6)
    # also when the character stops (no second automatic pause without AFK).
    rt.set(speed=0)
    rt.tick(300)
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == Flags.ACTIVE
    assert live_speed(rt)[1] == pytest.approx(pace(rt, "standKmh"), abs=1e-6)


def test_moving_while_still_afk_does_not_resume(rt):
    # Both conditions are needed: AFK over, and the character moving. (Being
    # pushed, or on a boat, with the AFK state still set is not coming back.)
    afk_paused_session(rt)
    rt.set(speed=7.0)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert flags(rt) == AFK_PAUSED | Flags.MOVING, "the AFK flag is set once, not twice"
    rt.set(afk=False)
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == Flags.ACTIVE | Flags.MOVING


def test_a_second_afk_pauses_again(rt):
    events = collect_events(rt)
    afk_paused_session(rt, events)
    rt.set(afk=False, speed=7.0)
    rt.tick(50)
    assert not rt.walka.IsPaused(rt.walka)
    rt.set(afk=True, speed=0)
    rt.tick()
    assert rt.walka.IsPaused(rt.walka) and session(rt)["pauseReason"] == "afk"
    assert events.count(("SESSION", "pause")) == 2
    assert flags(rt) == AFK_PAUSED


def test_an_afk_pause_survives_a_reload_and_still_waits_for_movement(rt):
    afk_paused_session(rt)
    rt.set(afk=False)
    fresh = rt.reload()
    assert fresh.walka.IsPaused(fresh.walka) and session(fresh)["pauseReason"] == "afk"
    fresh.tick(30)
    assert fresh.walka.IsPaused(fresh.walka)
    assert flags(fresh) == AFK_PAUSED
    fresh.set(speed=7.0)
    fresh.tick()
    assert not fresh.walka.IsPaused(fresh.walka)
    assert flags(fresh) == Flags.ACTIVE | Flags.MOVING


def test_resuming_by_hand_ends_an_afk_pause_without_moving(rt):
    afk_paused_session(rt)
    rt.set(afk=False)
    rt.tick(10)
    rt.slash("/walka resume")
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == Flags.ACTIVE, "the AFK flag goes with the pause"
    assert live_speed(rt)[1] == pytest.approx(pace(rt, "standKmh"), abs=1e-6)


def test_a_pause_by_hand_is_not_undone_by_afk_ending(rt):
    rt.slash("/walka start")
    rt.slash("/walka pause")
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "user"
    assert flags(rt) == AFK_PAUSED, "the game's own AFK state is still reported"
    rt.set(afk=False)
    rt.tick(5)
    assert rt.walka.IsPaused(rt.walka), "only an AFK pause resumes by itself"
    assert flags(rt) == Flags.ACTIVE | Flags.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka), "... and moving does not undo it either"
    assert flags(rt) == Flags.ACTIVE | Flags.PAUSED | Flags.MOVING
    assert live_speed(rt) == (0, 0)


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


def test_settings_of_a_fresh_install(rt):
    assert lua_plain(rt.db.settings) == {
        "units": "mph",                                     # an enUS client
        "speed": lua_plain(rt.ns.SpeedModel.DEFAULTS),      # (pinned in test_speed_model.py)
        "profile": {"weightKg": 75, "heightCm": 172},
        "beacon": {"enabled": True, "cellPx": 4},
        "hud": {"shown": True, "locked": False, "scale": 1.0, "alpha": 0.9},
        "autoPauseAFK": True,
        "dailyStepGoal": 6000,
        "announce": True,
    }, "exactly these settings: nothing that no code reads (such as the old netCalories)"
    # The speed settings are a copy: changing one leaves the model's defaults alone.
    rt.db.settings.speed.runKmh = 9.9
    assert rt.ns.SpeedModel.DEFAULTS.runKmh != 9.9


def test_saved_variables_from_an_older_or_damaged_file_are_repaired(make_rt):
    rt = make_rt(saved_variables={"WalkaDB": '{settings={units="kmh",speed="oops",hud=false},sessions=7,sessionCounter="x"}'})
    settings = lua_plain(rt.db.settings)
    assert settings["units"] == "kmh"                       # kept
    assert settings["speed"] == lua_plain(rt.ns.SpeedModel.DEFAULTS)    # rebuilt from defaults
    assert settings["speed"]["runKmh"] > 0
    assert settings["hud"]["shown"] is True
    assert settings["profile"] == {"weightKg": 75, "heightCm": 172}
    assert lua_plain(rt.db.sessions) in ([], {})
    assert rt.db.sessionCounter == 0
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    assert len(history(rt)) == 1


@pytest.mark.parametrize(
    "saved",
    [
        "7", '"text"', "false", "{}",
        # values of the wrong type, as a hand-edited file might have
        '{settings={speed={maxKmh="fast",standKmh=false,riseSeconds="x",taxiMode=3}}}',
        '{settings={profile={weightKg="x",heightCm={}}}}',
        '{settings={units=5,beacon={cellPx="big",enabled="yes"},hud={scale="x",alpha=false,shown=1}}}',
        "{settings={autoPauseAFK=0,announce='no',dailyStepGoal='many'}}",
        # settings an older version saved and this one no longer has
        "{settings={netCalories=true,speed={sprintKmh=5.5}}}",
        # a half-written session in progress
        "{active={savedAt=1794999990,session={}}}",
        '{active={savedAt=1794999990,session={id=1,start=1794999000,seconds="a"}}}',
        "{active={savedAt='x',session=5}}",
        "{active=true,sessionCounter={}}",
    ],
)
def test_any_saved_value_loads(make_rt, saved):
    rt = make_rt(saved_variables={"WalkaDB": saved})
    assert rt.db.settings.units in ("mph", "kmh")
    assert not rt.walka.IsActive(rt.walka), "a damaged session must not be resumed"
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka status")
    # Whatever was damaged is a default again: the default running pace.
    assert rt.db.settings.speed.runKmh == rt.ns.SpeedModel.DEFAULTS.runKmh
    assert live_speed(rt)[1] == pytest.approx(pace(rt, "runKmh"), abs=1e-6)
    rt.slash("/walka stop")
    assert len(history(rt)) == 1
    rt.reload()


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
    # pauses it explicitly, coming back must not restart the belt - not when
    # AFK ends and not when the character moves.
    events = collect_events(rt)
    rt.slash("/walka start")
    rt.tick(50)
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "afk"
    rt.slash("/walka pause")
    assert session(rt)["pauseReason"] == "user"
    assert events.count(("SESSION", "pause")) == 1, "it was paused already"
    rt.set(afk=False)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert live_speed(rt) == (0, 0)
    # The player's own pause now: the beacon no longer calls it the AFK one.
    assert flags(rt) == Flags.ACTIVE | Flags.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka), "moving does not resume a pause made by hand"
    assert session(rt)["pauseReason"] == "user"
    assert live_speed(rt) == (0, 0)
    assert flags(rt) == Flags.ACTIVE | Flags.PAUSED | Flags.MOVING
    assert ("SESSION", "resume") not in events
    rt.slash("/walka resume")
    assert not rt.walka.IsPaused(rt.walka)
    rt.tick()
    assert flags(rt) == Flags.ACTIVE | Flags.MOVING


def test_pausing_by_hand_after_afk_has_ended_also_makes_it_stick(rt):
    # The same, with the pause typed after coming back (AFK already over,
    # the character not yet moving).
    afk_paused_session(rt)
    rt.set(afk=False)
    rt.tick(10)
    assert flags(rt) == AFK_PAUSED
    rt.slash("/walka pause")
    rt.tick()
    assert session(rt)["pauseReason"] == "user"
    assert flags(rt) == Flags.ACTIVE | Flags.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka)
    assert live_speed(rt) == (0, 0)


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
    default_run = rt.ns.SpeedModel.DEFAULTS.runKmh
    assert walka.Get(walka, "speed.runKmh") == default_run != 3.5
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
    assert walka.Get(walka, "speed.runKmh") == default_run
    assert lua_plain(rt.db.settings.speed) == lua_plain(rt.ns.SpeedModel.DEFAULTS)
    # The defaults table itself must not be handed out for editing.
    walka.Set(walka, "speed.runKmh", 9.9)
    assert rt.ns.SpeedModel.DEFAULTS.runKmh == default_run


def test_settings_reach_the_speed_model_live(rt):
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka units kmh")
    rt.slash("/walka start")
    rt.tick(300)
    before = pace(rt, "runKmh")
    assert live_speed(rt)[1] == pytest.approx(before)
    assert before + 0.5 < 3.5 < walka.Get(walka, "speed.maxKmh"), "a clearly faster pace, still below the cap"
    walka.Set(walka, "speed.runKmh", 3.5)
    rt.tick(600)
    assert live_speed(rt)[1] == pytest.approx(3.5)
    walka.Set(walka, "speed.maxKmh", 2.0)
    rt.tick(5)
    assert live_speed(rt)[1] <= 2.0 + 1e-9
    rt.tick(100)
    assert live_speed(rt)[1] == pytest.approx(2.0), "at the cap, not stopped"
