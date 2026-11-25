"""The walking session: start, pause, resume, stop, AFK, /reload, history."""

from __future__ import annotations

import pytest

from .conftest import DEFAULT_PACE_KMH
from .harness import State, lua_dict, lua_list, lua_plain

PACE = DEFAULT_PACE_KMH     # what a fresh install counts with


def session(rt):
    current = rt.walka.GetSession(rt.walka)
    return None if current is None else lua_dict(current)


def history(rt):
    return [lua_dict(record) for record in lua_list(rt.db.sessions)]


def live_speed(rt):
    """The speed the session is counting with: the pace, or 0."""
    return rt.walka.GetLiveSpeed(rt.walka)


def flags(rt):
    """The state of the session and the character after the last tick."""
    return rt.state()


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

    assert live_speed(rt) == PACE, "counted at the walker's pace from the first moment"

    # Whatever the character does, the walk is counted at that one pace.
    samples = []
    for _ in range(600):
        rt.tick()
        assert live_speed(rt) == PACE
        samples.append(session(rt))
    # Steps, distance, calories and time only ever grow.
    for key in ("seconds", "distanceM", "steps", "kcal", "yards"):
        values = [sample[key] for sample in samples]
        assert values == sorted(values), key
        assert values[-1] > values[100] > 0, key
    after_minute = session(rt)
    assert after_minute["seconds"] == pytest.approx(60)
    assert after_minute["yards"] == pytest.approx(7.0 * 60, rel=0.01)
    metrics = rt.ns.Metrics
    profile = metrics.DEFAULT_PROFILE
    assert after_minute["distanceM"] == pytest.approx(PACE / 3.6 * 60)
    assert after_minute["steps"] == pytest.approx(metrics.stepsPerMinute(PACE, profile.heightCm))
    assert after_minute["kcal"] == pytest.approx(metrics.kcalPerMinute(PACE, profile.weightKg))
    assert after_minute["maxKmh"] == PACE

    # Pause: nothing is counted.
    rt.slash("/walka pause")
    assert walka.IsPaused(walka)
    assert ("SESSION", "pause") in events
    assert live_speed(rt) == 0
    rt.tick(30)
    assert flags(rt) & State.PAUSED
    frozen = session(rt)
    rt.tick(100)
    assert session(rt) == frozen, "nothing accumulates while paused"

    # Resume: counting again, at once.
    rt.slash("/walka resume")
    assert not walka.IsPaused(walka)
    assert ("SESSION", "resume") in events
    assert live_speed(rt) == PACE
    rt.tick(300)
    assert session(rt)["seconds"] == pytest.approx(frozen["seconds"] + 30)

    # Stop: one record in the saved history.
    before_stop = session(rt)
    rt.clear_printed()
    rt.slash("/walka stop")
    assert not walka.IsActive(walka) and session(rt) is None
    assert ("SESSION", "stop") in events and ("HISTORY",) in events
    assert live_speed(rt) == 0
    records = history(rt)
    assert len(records) == 1
    record = records[0]
    assert record["id"] == 1
    assert record["start"] == started["start"]
    assert record["end"] == int(rt.game.clock)      # (the addon's clock counts whole seconds)
    assert record["end"] - record["start"] == pytest.approx(103, abs=1)
    assert record["seconds"] == round(before_stop["seconds"])
    assert record["distanceM"] == round(before_stop["distanceM"])
    assert record["steps"] == round(before_stop["steps"])
    assert record["kcal"] == pytest.approx(before_stop["kcal"], abs=0.051)
    assert record["yards"] == round(before_stop["yards"])
    assert record["maxKmh"] == pytest.approx(PACE, abs=0.005)           # stored to two decimals
    assert record["character"] == "Tester-Azeroth"
    # Everything stored is a whole number except kcal (one decimal) and maxKmh (two).
    for key in ("seconds", "distanceM", "steps", "yards", "start", "end"):
        assert record[key] == int(record[key]), key
    assert record["kcal"] * 10 == pytest.approx(round(record["kcal"] * 10))
    assert any("session finished" in line for line in rt.printed)
    assert rt.find_secrets() == []


def test_a_character_standing_still_does_not_stop_the_count(rt):
    """The character stands - at a vendor, in a queue - and the walker
    walks on: the session counts the walk, not the character."""
    rt.slash("/walka start")
    rt.tick(200)
    assert live_speed(rt) == PACE
    assert session(rt)["yards"] == 0
    assert session(rt)["seconds"] == pytest.approx(20)
    assert session(rt)["distanceM"] == pytest.approx(PACE / 3.6 * 20)


def test_a_session_started_with_announcements_says_what_it_counts_with(rt):
    rt.clear_printed()
    rt.slash("/walka start")
    assert rt.printed == [
        rt.walka.PREFIX + "session started - your walk counts for this character. Start the belt at the treadmill; "
        "the Walka app shows the walk (until its numbers arrive, Walka estimates at 1.5 mph). /walka stop to finish."
    ]


def test_nothing_a_session_says_claims_to_move_the_belt(rt):
    """Walka reads; it does not drive. No message of a session may say that
    the belt follows the character, or that Walka starts, stops or pauses
    it."""
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(250)
    rt.slash("/walka pause")
    rt.slash("/walka resume")
    rt.set(afk=True, speed=0)
    rt.tick(5)
    rt.set(afk=False, speed=7.0)
    rt.tick(5)
    rt.slash("/walka status")
    rt.slash("/walka stop")
    said = " ".join(rt.printed).lower()
    for claim in ("follows your character", "belt is stopped", "stops the belt", "starts the belt", "target",
                  "own risk", "countdown"):
        assert claim not in said, claim


def test_short_sessions_are_discarded(rt):
    events = collect_events(rt)
    rt.slash("/walka start")
    rt.tick(195)                    # a little under twenty seconds
    assert session(rt)["seconds"] < rt.walka.MIN_SESSION_SECONDS
    rt.clear_printed()
    rt.slash("/walka stop")
    assert history(rt) == []
    assert ("HISTORY",) not in events
    assert ("SESSION", "stop") in events
    assert any("too short" in line for line in rt.printed)


def test_a_session_just_long_enough_is_kept(rt):
    rt.slash("/walka start")
    rt.tick(201)
    assert rt.walka.MIN_SESSION_SECONDS <= session(rt)["seconds"] < rt.walka.MIN_SESSION_SECONDS + 0.2
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
    assert seen == [255, 1, 2]              # 0 is never a session's number


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


def test_the_first_session_has_no_notice_of_its_own(make_rt):
    """Until 0.7 the first start ever said that the belt is moved at the
    walker's own risk, and the saved file noted that it had been said. Walka
    moves no belt now: nothing is said, and the note is dropped."""
    rt = make_rt(saved_variables={"WalkaDB": "{riskNoticeShown=true}"})
    assert rt.db.riskNoticeShown is None and rt.walka.RISK_NOTICE is None
    rt.walka.Set(rt.walka, "announce", False)
    rt.clear_printed()
    rt.slash("/walka start")
    assert rt.printed == []
    assert rt.reload().db.riskNoticeShown is None


# ---- AFK ------------------------------------------------------------------------------


AFK_PAUSED = State.ACTIVE | State.PAUSED | State.AFK


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
    assert live_speed(rt) == 0
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
    assert flags(rt) == State.ACTIVE | State.MOVING
    assert live_speed(rt) == PACE


def test_afk_ending_alone_does_not_resume_the_session(rt):
    """Back at the keyboard, but the character stands still - typing a chat
    line, say. That is not walking on: the session stays paused, and the
    pause stays the automatic (AFK) one, although the game's own AFK state
    is gone."""
    events = collect_events(rt)
    afk_paused_session(rt, events)
    frozen = session(rt)

    rt.set(afk=False)                       # the first key press clears AFK
    for _ in range(300):
        rt.tick()
        assert rt.walka.IsPaused(rt.walka)
        assert flags(rt) == AFK_PAUSED, "still paused, still flagged as the AFK pause"
    assert session(rt) == frozen and frozen["pauseReason"] == "afk"
    assert live_speed(rt) == 0
    assert ("SESSION", "resume") not in events
    assert not any("resumed" in line for line in rt.printed)
    # Things that are not the character moving leave it paused as well.
    rt.set(combat=True, resting=True, mounted=True)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert flags(rt) == AFK_PAUSED | State.MOUNTED


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
    assert flags(rt) == State.ACTIVE | State.MOVING, "neither PAUSED nor AFK any more"
    assert live_speed(rt) == PACE
    # ... and it is an ordinary session again: it counts on,
    before = session(rt)["seconds"]
    rt.tick(300)
    assert session(rt)["seconds"] == pytest.approx(before + 30)
    # also when the character stops (no second automatic pause without AFK).
    rt.set(speed=0)
    rt.tick(300)
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == State.ACTIVE
    assert session(rt)["seconds"] == pytest.approx(before + 60)


def test_moving_while_still_afk_does_not_resume(rt):
    # Being pushed, or on a boat, with the AFK state still set is not coming
    # back ...
    afk_paused_session(rt)
    rt.set(speed=7.0)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert flags(rt) == AFK_PAUSED | State.MOVING, "the AFK flag is set once, not twice"
    # ... but the moment the game says the player is back, a character that
    # is moving is all it takes. (Until 0.7 it had to be seen standing
    # first: that rule was there so that a belt would not start under
    # nobody. Now only the count starts.)
    rt.set(afk=False)
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == State.ACTIVE | State.MOVING


def test_an_afk_pause_on_a_flight_path_ends_when_the_player_is_back(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.set(taxi=True, speed=20.0)
    rt.tick(50)
    rt.set(afk=True)
    rt.tick(5)
    assert rt.walka.IsPaused(rt.walka)
    rt.tick(300)
    assert rt.walka.IsPaused(rt.walka), "the flight alone does not end it"
    rt.set(afk=False)
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == State.ACTIVE | State.MOVING | State.TAXI


def test_an_afk_paused_session_restored_after_a_reload_still_ends_by_moving(rt):
    afk_paused_session(rt)
    fresh = rt.reload()
    fresh.set(afk=False, speed=0)
    fresh.tick(100)
    assert fresh.walka.IsPaused(fresh.walka)
    fresh.set(speed=7.0)
    fresh.tick()
    assert not fresh.walka.IsPaused(fresh.walka)


def test_where_the_game_hides_the_afk_flag_the_addon_keeps_its_own_clock(rt):
    """Inside dungeons, raids and battlegrounds addons cannot read the AFK
    flag. Hidden must not mean "not AFK": five minutes without movement or
    a fight count as away there."""
    game = rt.ns.Game
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    rt.set(chatLockdown=True, afk=True, speed=0)  # (the flag itself cannot be read)
    rt.advance(game.AFK_IDLE_SECONDS - 20)
    assert not rt.walka.IsPaused(rt.walka)
    rt.advance(30)
    assert rt.walka.IsPaused(rt.walka) and session(rt)["pauseReason"] == "afk"
    assert rt.walka.GetGameState(rt.walka).afkHidden
    assert rt.find_secrets() == []
    # Back: the first movement ends it.
    rt.set(speed=7.0)
    rt.tick(2)
    assert not rt.walka.IsPaused(rt.walka)
    # A fight counts as being there, however long the character stands.
    rt.set(speed=0, combat=True)
    rt.advance(game.AFK_IDLE_SECONDS + 60)
    assert not rt.walka.IsPaused(rt.walka)


def away_for(rt, seconds: float) -> None:
    """The clock moves on while the session stays paused."""
    rt.set(clock=rt.game.clock + seconds, uptime=rt.game.uptime + seconds)
    rt.tick()


def test_an_afk_pause_has_no_time_limit(rt):
    """Until 0.7 an automatic pause that had lasted ten minutes could only
    be ended by pressing Resume: by then nobody could say what stood on a
    belt that Walka was about to restart. Walka restarts no belt now. The
    pause only stops the count, and however long it lasted, it ends when the
    player is back and the character moves."""
    events = collect_events(rt)
    afk_paused_session(rt, events)
    assert rt.walka.AFK_RESUME_SECONDS is None
    frozen = session(rt)
    assert "pausedAt" not in frozen and "afkStill" not in frozen
    rt.clear_printed()
    away_for(rt, 3 * 3600)
    assert session(rt) == frozen and rt.printed == []
    assert events.count(("SESSION", "pause")) == 1
    assert flags(rt) == AFK_PAUSED
    rt.set(afk=False, speed=7.0)
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == State.ACTIVE | State.MOVING


def test_a_paused_session_saved_by_an_older_version_loads_without_its_belt_rules(rt):
    """Older versions kept when an automatic pause began and whether the
    character had been seen standing since. Neither is read any more."""
    afk_paused_session(rt)
    rt.run("local walka = ...; local s = walka:GetSession(); s.pausedAt, s.afkStill = 1794000000, true", rt.walka)
    fresh = rt.reload(gap=30)
    restored = session(fresh)
    assert restored["pauseReason"] == "afk" and "pausedAt" not in restored and "afkStill" not in restored
    saved = lua_plain(rt.db.active)
    assert set(saved) == {"savedAt", "session"}, "nothing of a belt is saved with a session"
    fresh.set(afk=False, speed=7.0)
    fresh.tick()
    assert not fresh.walka.IsPaused(fresh.walka)


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
    assert flags(fresh) == State.ACTIVE | State.MOVING


def test_resuming_by_hand_ends_an_afk_pause_without_moving(rt):
    afk_paused_session(rt)
    rt.set(afk=False)
    rt.tick(10)
    rt.slash("/walka resume")
    rt.tick()
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) == State.ACTIVE, "the AFK flag goes with the pause"
    assert live_speed(rt) == PACE


def test_a_pause_by_hand_is_not_undone_by_afk_ending(rt):
    rt.slash("/walka start")
    rt.slash("/walka pause")
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "user"
    # On a paused frame the AFK flag means "this is the automatic pause". A
    # pause made by hand goes out without it, even while the game shows the
    # player AFK.
    assert flags(rt) == State.ACTIVE | State.PAUSED
    rt.set(afk=False)
    rt.tick(5)
    assert rt.walka.IsPaused(rt.walka), "only an AFK pause resumes by itself"
    assert flags(rt) == State.ACTIVE | State.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka), "... and moving does not undo it either"
    assert flags(rt) == State.ACTIVE | State.PAUSED | State.MOVING
    assert live_speed(rt) == 0


def test_afk_pause_can_be_switched_off(rt):
    rt.walka.Set(rt.walka, "autoPauseAFK", False)
    rt.slash("/walka start")
    rt.set(afk=True)
    rt.tick(20)
    assert not rt.walka.IsPaused(rt.walka)
    assert flags(rt) & State.AFK and not flags(rt) & State.PAUSED


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

    fresh = rt.reload()
    assert fresh.walka.IsActive(fresh.walka)
    restored = session(fresh)
    assert restored == before
    # The walker did not stop for the reload, so neither does the count.
    assert live_speed(fresh) == PACE
    assert fresh.db.active is None, "the saved copy is consumed"
    fresh.tick()
    assert flags(fresh) == State.ACTIVE | State.MOVING
    assert fresh.session_id() == before["id"]
    fresh.tick(100)
    assert session(fresh)["seconds"] == pytest.approx(before["seconds"] + 10.1)
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
    assert live_speed(fresh) == 0
    assert flags(fresh) & State.PAUSED
    fresh.slash("/walka resume")
    assert live_speed(fresh) == PACE


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
    assert live_speed(fresh) == 0
    fresh.tick()
    assert flags(fresh) == State.MOVING


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
    rt.slash("/walka pace 3.5")
    rt.slash("/walka start")
    rt.tick(300)
    rt.slash("/walka stop")
    fresh = rt.reload().reload(gap=5000)
    assert fresh.db.settings.profile.weightKg == 82
    assert fresh.db.settings.units == "kmh"
    assert fresh.db.settings.paceKmh == 3.5
    assert len(history(fresh)) == 1
    assert fresh.db.version == 1


def test_settings_of_a_fresh_install(make_rt):
    rt = make_rt(auto_session=True)
    assert lua_plain(rt.db.settings) == {
        "units": "mph",                                     # an enUS client
        "paceKmh": 2.414,                                   # 1.5 mph
        "profile": {"weightKg": 75, "heightCm": 172},
        "autoPauseAFK": True,
        "autoSession": True,
        "recordRoad": True,
        "announce": True,
    }, "exactly these settings: nothing that no code reads (such as the old netCalories)"
    assert rt.walka.GetPace(rt.walka) == rt.ns.Metrics.DEFAULT_PACE_KMH == PACE
    assert rt.ns.SpeedModel is None, "there is no model of belt speeds any more"


OLD_SPEED_SETTINGS = (
    "speed={standKmh=1.287,walkKmh=1.931,runKmh=2.575,mountKmh=3.219,maxKmh=4.023,minBeltKmh=1.0,stepKmh=0.1,"
    "riseSeconds=4,fallSeconds=8,stopHoldSeconds=4,taxiMode='run',combatHold=false,keepWalking=true}"
)


def test_the_speed_settings_of_an_older_version_are_dropped(make_rt):
    """Until 0.7 the addon asked the companion for belt speeds and kept a
    page of settings for it. A saved file still has them: they load without
    a word, change nothing, and are gone from the file at the next logout."""
    rt = make_rt(saved_variables={"WalkaDB": "{riskNoticeShown=true,settings={units='kmh'," + OLD_SPEED_SETTINGS + "}}"})
    assert rt.printed == [] and rt.find_secrets() == []
    settings = lua_plain(rt.db.settings)
    assert "speed" not in settings and settings["units"] == "kmh"
    assert settings["paceKmh"] == PACE, "no old belt speed becomes the pace"
    assert rt.db.riskNoticeShown is None
    walka = rt.walka
    assert walka.Get(walka, "speed.keepWalking") is None
    assert walka.SetKeepWalking is None and walka.ResetSpeedSettings is None and walka.GetBeltLimits is None
    rt.set(speed=14.0, mounted=True)
    rt.slash("/walka start")
    rt.tick(300)
    assert live_speed(rt) == PACE
    assert session(rt)["distanceM"] == pytest.approx(PACE / 3.6 * 30)
    rt.slash("/walka stop")
    fresh = rt.reload()
    assert "speed" not in lua_plain(fresh.db.settings) and fresh.db.riskNoticeShown is None


@pytest.mark.parametrize(
    "saved, expected",
    [
        ("nil", PACE), ("'fast'", PACE), ("true", PACE), ("{}", PACE), ("0", PACE), ("-3", PACE), ("0.49", PACE),
        ("10.01", PACE), ("0/0", PACE), ("1/0", PACE), ("0.5", 0.5), ("10", 10), ("3.219", 3.219),
    ],
)
def test_a_saved_pace_that_is_no_pace_is_the_default_again(make_rt, saved, expected):
    rt = make_rt(saved_variables={"WalkaDB": f"{{settings={{paceKmh={saved}}}}}"})
    assert rt.db.settings.paceKmh == expected
    rt.slash("/walka start")
    rt.tick(250)
    assert session(rt)["distanceM"] == pytest.approx(expected / 3.6 * 25)
    rt.slash("/walka stop")
    rt.reload()


def test_saved_variables_from_an_older_or_damaged_file_are_repaired(make_rt):
    rt = make_rt(saved_variables={"WalkaDB": '{settings={units="kmh",speed="oops",hud=false},sessions=7,sessionCounter="x"}'})
    settings = lua_plain(rt.db.settings)
    assert settings["units"] == "kmh"                       # kept
    assert "speed" not in settings                          # (a setting of older versions: dropped)
    assert settings["paceKmh"] == PACE
    assert "hud" not in settings                            # (the addon has no windows any more)
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
        '{settings={paceKmh="fast"},riskNoticeShown="yes"}',
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
    # Whatever was damaged is a default again: the default pace.
    assert rt.db.settings.paceKmh == PACE == live_speed(rt)
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
    assert current["seconds"] == pytest.approx(60, abs=0.6)
    assert current["yards"] == pytest.approx(420, rel=0.01)
    assert current["distanceM"] == pytest.approx(PACE / 3.6 * 60, rel=0.01)


def test_tick_callback_fires_about_four_times_a_second(rt):
    events = collect_events(rt)
    rt.tick(100)
    assert 30 <= events.count(("TICK",)) <= 45


def test_an_error_in_a_subscriber_reaches_the_error_handler_and_does_not_stop_the_tick(rt):
    from .harness import AddonError

    def broken(*_):
        raise RuntimeError("boom")

    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.walka.On(rt.walka, "TICK", rt.lua.eval("function() error('boom from a window') end"))
    with pytest.raises(AddonError, match="boom from a window"):
        rt.tick(10)
    # The count kept going regardless.
    assert session(rt)["seconds"] == pytest.approx(1.0)


def test_pausing_by_hand_during_an_afk_pause_makes_it_stick(rt):
    # Away from the keyboard the session pauses itself. If the player then
    # pauses it explicitly, coming back must not start the count again - not
    # when AFK ends and not when the character moves.
    events = collect_events(rt)
    rt.slash("/walka start")
    rt.tick(50)
    rt.set(afk=True)
    rt.tick(5)
    assert session(rt)["pauseReason"] == "afk"
    rt.slash("/walka pause")
    assert session(rt)["pauseReason"] == "user"
    # One for the automatic pause, one when it became the player's own (the
    # HUD's "Paused (AFK)" turns into "Paused" at once).
    assert events.count(("SESSION", "pause")) == 2
    rt.set(afk=False)
    rt.tick(20)
    assert rt.walka.IsPaused(rt.walka)
    assert live_speed(rt) == 0
    # The player's own pause now: no longer the AFK one.
    assert flags(rt) == State.ACTIVE | State.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka), "moving does not resume a pause made by hand"
    assert session(rt)["pauseReason"] == "user"
    assert live_speed(rt) == 0
    assert flags(rt) == State.ACTIVE | State.PAUSED | State.MOVING
    assert ("SESSION", "resume") not in events
    rt.slash("/walka resume")
    assert not rt.walka.IsPaused(rt.walka)
    rt.tick()
    assert flags(rt) == State.ACTIVE | State.MOVING


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
    assert flags(rt) == State.ACTIVE | State.PAUSED
    rt.set(speed=7.0)
    rt.tick(50)
    assert rt.walka.IsPaused(rt.walka)
    assert live_speed(rt) == 0


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
    assert walka.Get(walka, "profile.weightKg") == 75
    walka.Set(walka, "profile.weightKg", 80)
    assert walka.Get(walka, "profile.weightKg") == 80
    assert rt.db.settings.profile.weightKg == 80
    assert walka.Get(walka, "profile") is not None
    assert walka.Get(walka, "no.such.setting") is None
    assert walka.Get(walka, "units.deeper") is None
    walka.Set(walka, "brand.new.key", 1)
    assert walka.Get(walka, "brand.new.key") == 1
    assert events.count(("SETTINGS",)) == 2


# ---- the pace -------------------------------------------------------------------------------------


def test_the_pace_is_what_a_session_counts_with_and_changes_live(rt):
    walka = rt.walka
    events = collect_events(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    first = session(rt)
    assert first["distanceM"] == pytest.approx(PACE / 3.6 * 10)
    assert walka.SetPace(walka, 4.0) == 4.0
    assert events.count(("SETTINGS",)) == 1, "whoever listens hears of it"
    assert walka.GetPace(walka) == rt.db.settings.paceKmh == live_speed(rt) == 4.0
    rt.tick(100)
    second = session(rt)
    assert second["distanceM"] - first["distanceM"] == pytest.approx(4.0 / 3.6 * 10)
    metrics = rt.ns.Metrics
    assert second["steps"] - first["steps"] == pytest.approx(metrics.stepsPerMinute(4.0, 172) / 6)
    assert second["kcal"] - first["kcal"] == pytest.approx(metrics.kcalPerMinute(4.0, 75) / 6)
    assert second["maxKmh"] == 4.0
    walka.SetPace(walka, 2.0)               # slower again: the top speed stays what it was
    rt.tick(100)
    assert session(rt)["maxKmh"] == 4.0
    assert session(rt)["distanceM"] - second["distanceM"] == pytest.approx(2.0 / 3.6 * 10)


@pytest.mark.parametrize(
    "asked, stored",
    [(0.5, 0.5), (10, 10), (2.4140160, 2.414), (3.2186880, 3.219), (0.1, 0.5), (0, 0.5), (-4, 0.5), (55, 10),
     (float("inf"), 10), ("3.5", 3.5), ("fast", 2.414), (None, 2.414), (float("nan"), 2.414)],
)
def test_set_pace_keeps_to_a_walking_pace(rt, asked, stored):
    """Whatever is handed in, the pace is a finite speed from 0.5 to 10
    km/h, stored to a thousandth."""
    walka = rt.walka
    assert walka.SetPace(walka, asked) == stored == rt.db.settings.paceKmh
    rt.slash("/walka start")
    rt.tick(250)
    assert session(rt)["distanceM"] == pytest.approx(stored / 3.6 * 25)
    rt.slash("/walka stop")
    rt.reload()


def test_the_pace_does_nothing_without_a_session_or_while_paused(rt):
    walka = rt.walka
    walka.SetPace(walka, 5.0)
    rt.tick(50)
    assert live_speed(rt) == 0 and session(rt) is None
    rt.slash("/walka start")
    rt.tick(10)
    rt.slash("/walka pause")
    frozen = session(rt)
    walka.SetPace(walka, 8.0)
    rt.tick(50)
    assert live_speed(rt) == 0 and session(rt) == frozen


# ---- follow-ups to the AFK rules ---------------------------------------------------------------


def test_afk_pause_still_ends_on_movement_after_the_setting_is_switched_off(rt):
    """The pause was automatic, so its end is too - even if "pause when AFK"
    has been unticked while it lasted."""
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(20)
    rt.set(afk=True, speed=0)
    rt.advance(2)
    assert walka.IsPaused(walka)
    walka.Set(walka, "autoPauseAFK", False)
    rt.set(afk=False)
    rt.advance(2)
    assert walka.IsPaused(walka), "back, but standing still"
    rt.set(speed=7.0)
    rt.advance(1)
    assert not walka.IsPaused(walka)
    # ... and with the setting off, going AFK again does not pause.
    rt.set(afk=True, speed=0)
    rt.advance(2)
    assert not walka.IsPaused(walka)


def test_manual_pause_during_an_afk_pause_tells_the_windows(rt):
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(10)
    rt.set(afk=True, speed=0)
    rt.advance(2)
    events = rt.run(
        """
        local walka, seen = ..., {}
        walka:On("SESSION", function(what) seen[#seen + 1] = what end)
        walka:PauseSession("user")
        walka:PauseSession("user")
        return table.concat(seen, ",")
        """,
        walka,
    )
    assert events == "pause", "one event when the pause becomes the player's own, none when repeated"
