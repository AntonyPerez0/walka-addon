"""A character's journey: Core/Journey.lua (the record and its sums) and
Tracker.lua (what the game feeds into it).

Per character the addon keeps how far it travelled - on foot and in all -
its level, its /played time, and the real-world walking done while playing
it. The stat frames that carry these numbers to the companion are in
test_stat_frames.py, the window that shows them in test_ui_journey.py.
"""

from __future__ import annotations

import math

import pytest

from . import ui_driver as ui
from .harness import lua_dict, lua_list, lua_plain

NOW = 1795000000                # the mock client's clock at login
ME = "Tester-Azeroth"


def own(start, seconds=1800, *, steps=2500, distance=1400, kcal=100.0, yards=9000, character=ME, extra="") -> str:
    """Lua source of one of the addon's session records."""
    return (
        f"{{id=1,start={start},['end']={start + seconds},seconds={seconds},distanceM={distance},steps={steps},"
        f"kcal={kcal},yards={yards},maxKmh=3.2,character={character!r}{extra}}}"
    )


def measured(start, seconds=1800, *, steps=2600, distance=1500, kcal=110.0) -> str:
    """Lua source of one walk in the companion's file."""
    return (
        f"{{start={start},['end']={start + seconds},seconds={seconds},distanceM={distance},steps={steps},"
        f"kcal={kcal},maxKmh=3.3,source='wow',stepsSource='treadmill'}}"
    )


def saved(*records: str, more: str = "") -> dict[str, str]:
    return {"WalkaDB": "{sessions={" + ",".join(records) + "}" + more + "}"}


def companion(*walks: str, more: str = "") -> dict[str, str]:
    return {"CompanionData.lua": f"WalkaCompanionData = {{ written = {NOW - 60}, sessions = {{ {','.join(walks)} }}{more} }}"}


def summary(rt) -> dict:
    """What the Journey window and the stat frames are made from."""
    result = rt.ns.Tracker.GetSummary()
    rt.check_errors()
    return lua_plain(result)


def real(rt) -> dict:
    """The real-world part of the summary."""
    journey = summary(rt)
    return {key: journey[key] for key in ("steps", "distanceM", "kcal", "seconds", "walks", "estimated")}


def record(rt, key: str = ME) -> dict:
    """A journey record as it is in the saved variables."""
    return lua_plain(rt.db.journeys[key])


def walk(rt, seconds: float) -> None:
    """A walking session of `seconds` on the clock, started and stopped."""
    rt.slash("/walka start")
    rt.advance(seconds)
    rt.slash("/walka stop")


# ---- Core/Journey.lua: the record ----------------------------------------------------------------


def test_a_new_character_starts_at_zero(rt):
    journey = rt.ns.Journey
    journeys = rt.lua.table()
    opened = journey.open(journeys, "Newbie-Azeroth", NOW)
    assert lua_plain(opened) == {
        "since": NOW, "footYd": 0, "travelYd": 0, "level": 0, "maxLevel": 0, "classId": 0, "faction": 0, "played": 0,
        "real": {"walks": 0, "seconds": 0, "distanceM": 0, "steps": 0, "kcal": 0, "estimated": False},
    }
    assert ui.same(rt, journeys["Newbie-Azeroth"], opened)
    opened.footYd = 12.5
    assert ui.same(rt, journey.open(journeys, "Newbie-Azeroth", NOW + 500), opened), "opened again, not started again"
    assert opened.footYd == 12.5 and opened.since == NOW


@pytest.mark.parametrize(
    "damaged",
    [
        "5", "'text'", "{}", "{ since = 'x', footYd = 0/0, travelYd = 1/0, level = -3, played = {}, real = 7 }",
        "{ since = -1, footYd = -20, travelYd = 'far', level = 1e9, maxLevel = 0/0, classId = 'rogue', faction = true,"
        " played = -1/0, playedHeard = 'yes', real = { walks = 0/0, seconds = -5, steps = 1/0, kcal = {}, estimated = 1 } }",
    ],
)
def test_a_damaged_record_is_made_safe_to_count_with(rt, damaged):
    journeys = rt.run(f"return {{ ['Tester-Azeroth'] = {damaged} }}")
    opened = lua_plain(rt.ns.Journey.open(journeys, ME, NOW))
    assert opened["since"] == NOW, "a time that cannot be shown is replaced"
    for key in ("footYd", "travelYd", "played"):
        assert opened[key] == 0
    for key in ("level", "maxLevel", "classId", "faction"):
        assert opened[key] in (0, 255) and isinstance(opened[key], int)
    assert "playedHeard" not in opened
    assert opened["real"] == {"walks": 0, "seconds": 0, "distanceM": 0, "steps": 0, "kcal": 0, "estimated": False}


def test_what_is_a_character(rt):
    is_character = rt.ns.Journey.isCharacter
    assert is_character("Tester-Azeroth") and is_character("Tester")
    # A session begun while the game hid the name is filed under "?".
    for nobody in ("?", "?-Azeroth", "", None, 5, rt.lua.table()):
        assert not is_character(nobody)


def test_level_class_and_faction_keep_what_is_known(rt):
    journey = rt.ns.Journey
    opened = journey.open(rt.lua.table(), ME, NOW)
    journey.setInfo(opened, 0, 0, 0, 0)                 # the game has said nothing yet
    assert (opened.level, opened.maxLevel, opened.classId, opened.faction) == (0, 60, 0, 0), "60 until the client says otherwise"
    journey.setInfo(opened, 17, 70, 4, 2)
    assert (opened.level, opened.maxLevel, opened.classId, opened.faction) == (17, 70, 4, 2)
    journey.setInfo(opened, 0, 0, 0, 0)                 # hidden for a moment
    assert (opened.level, opened.maxLevel, opened.classId, opened.faction) == (17, 70, 4, 2)
    journey.setInfo(opened, 71, 0, 0, 0)                # past the cap we knew
    assert (opened.level, opened.maxLevel) == (71, 71), "never 71 / 70"
    journey.setInfo(opened, 18.9, 1e9, -4, None)
    assert (opened.level, opened.maxLevel, opened.classId, opened.faction) == (18, 255, 4, 2)


# ---- Core/Journey.lua: distance ---------------------------------------------------------------------


def state(rt, speed, **flags):
    table = rt.to_lua({"speed": speed, "mounted": False, "taxi": False, "flying": False, "swimming": False, **flags})
    return table


@pytest.mark.parametrize(
    "flags, on_foot",
    [
        ({}, True),
        ({"dead": True}, True),                         # running back as a ghost
        ({"falling": True}, True),                      # jumping
        ({"combat": True, "estimated": True}, True),
        ({"mounted": True}, False),
        ({"taxi": True}, False),
        ({"flying": True}, False),                      # also a flight form: flying without a mount
        ({"swimming": True}, False),
        ({"mounted": True, "flying": True, "taxi": True, "swimming": True}, False),
    ],
)
def test_on_foot_is_moving_without_mount_flight_or_water(rt, flags, on_foot):
    journey = rt.ns.Journey
    opened = journey.open(rt.lua.table(), ME, NOW)
    journey.travel(opened, state(rt, 7.0, **flags), 0.5)
    assert opened.travelYd == 3.5
    assert opened.footYd == (3.5 if on_foot else 0)


@pytest.mark.parametrize("speed", [0, -3, math.nan, math.inf, -math.inf, 1000, 1e300])
def test_a_speed_that_is_not_movement_adds_nothing(rt, speed):
    journey = rt.ns.Journey
    opened = journey.open(rt.lua.table(), ME, NOW)
    journey.travel(opened, state(rt, speed), 0.1)
    journey.travel(opened, state(rt, 7.0), 0)           # ... nor does no time at all
    journey.travel(opened, state(rt, 7.0), -1)
    assert (opened.footYd, opened.travelYd) == (0, 0)


@pytest.mark.parametrize("yards, steps", [(0, 0), (1, 1), (11, 13), (88, 100), (1760, 2000), (82026, 93211), (2**40 - 1, 1249445031563)])
def test_character_steps_are_two_thousand_to_the_in_game_mile(rt, yards, steps):
    journey = rt.ns.Journey
    assert (journey.STEPS_PER_MILE, journey.YARDS_PER_MILE) == (2000, 1760)
    assert journey.steps(yards) == steps
    # The companion is sent the yards and does the same sum in whole numbers.
    assert steps == (yards * 2000 + 880) // 1760


def test_character_steps_of_nonsense(rt):
    steps = rt.ns.Journey.steps
    assert [steps(value) for value in (None, "x", -5, math.nan, math.inf)] == [0, 0, 0, 0, 0]
    assert steps(10.9) == steps(10), "whole yards, as they are shown and sent"


# ---- Tracker.lua: distance in the game ----------------------------------------------------------------


def test_tracking_starts_at_login_without_a_session(rt):
    journey = summary(rt)
    assert journey["name"] == "Tester" and journey["since"] == NOW
    assert (journey["footYd"], journey["travelYd"], journey["characterSteps"]) == (0, 0, 0)
    rt.set(speed=7.0)
    rt.advance(10)
    assert not rt.walka.IsActive(rt.walka)
    journey = summary(rt)
    assert (journey["footYd"], journey["travelYd"]) == (70, 70)
    assert journey["characterSteps"] == 80          # 70 / 0.88, to the nearest step
    assert set(lua_dict(rt.db.journeys)) == {ME}, "kept under the character's Name-Realm"


def test_walking_running_riding_flying_swimming(rt):
    legs = [
        ({"speed": 2.5}, 25, True),                                     # walking
        ({"speed": 7.0}, 70, True),                                     # running
        ({"speed": 9.8}, 98, True),                                     # sprinting
        ({"speed": 14.0, "mounted": True}, 140, False),                 # riding
        ({"speed": 28.0, "mounted": True, "flying": True}, 280, False), # on a flying mount
        ({"speed": 32.0, "taxi": True}, 320, False),                    # a flight path
        ({"speed": 4.7222, "swimming": True}, 47.222, False),           # swimming
        ({"speed": 8.75, "dead": True}, 87.5, True),                    # running back as a ghost
        ({"speed": 7.0, "falling": True}, 70, True),                    # jumping along
        ({"speed": 0}, 0, False),                                       # standing
    ]
    foot = travel = 0.0
    for flags, yards, on_foot in legs:
        rt.set(mounted=False, flying=False, taxi=False, swimming=False, dead=False, falling=False)
        rt.set(**flags)
        rt.advance(10)
        travel += yards
        foot += yards if on_foot else 0
        journey = record(rt)
        assert journey["travelYd"] == pytest.approx(travel, abs=1e-6), flags
        assert journey["footYd"] == pytest.approx(foot, abs=1e-6), flags
    journey = summary(rt)
    assert (journey["footYd"], journey["travelYd"]) == (350, 1137)      # whole yards, never rounded up
    assert journey["footYd"] == math.floor(foot + 1e-6) and journey["travelYd"] == math.floor(travel + 1e-6)


def test_the_same_with_a_session_running(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(30)
    rt.slash("/walka pause")                # the belt is off; the character runs on
    rt.advance(10)
    rt.slash("/walka stop")
    rt.advance(10)
    assert summary(rt)["footYd"] == 350


def test_in_combat_the_estimated_speed_is_counted(rt):
    """The game hides the real speed in combat. The journey goes by the
    estimate the addon already makes - moving or not, at the run speed it
    last could read - and never touches the hidden value."""
    rt.set(speed=8.05, runSpeed=8.05)       # a run-speed bonus, seen before the fight
    rt.advance(1)
    rt.enter_combat()
    rt.advance(10)
    assert rt.walka.GetGameState(rt.walka).estimated is True
    assert record(rt)["footYd"] == pytest.approx(8.05 * 11, abs=1e-6)
    rt.set(speed=0)                         # standing in the fight
    rt.advance(10)
    assert record(rt)["footYd"] == pytest.approx(8.05 * 11, abs=1e-6)
    rt.set(speed=14.0, mounted=True)        # mounted in combat: the estimate is a mounted one
    rt.advance(10)
    after = record(rt)
    assert after["footYd"] == pytest.approx(8.05 * 11, abs=1e-6)
    assert after["travelYd"] == pytest.approx(8.05 * 11 + 8.05 * 1.6 * 10, abs=1e-6)
    assert rt.find_secrets() == []
    rt.leave_combat()
    rt.reload()                             # nothing hidden reached the saved variables


def test_a_long_hitch_counts_as_one_second_at_most(rt):
    rt.set(speed=7.0)
    rt.tick()
    before = record(rt)["footYd"]
    rt.frame(30.0)                          # a loading screen: one frame of half a minute
    assert record(rt)["footYd"] - before == pytest.approx(7.0), "not 210 yards in one step"


def test_distance_survives_a_reload_and_a_logout(rt):
    rt.set(speed=7.0)
    rt.advance(10)
    fresh = rt.reload()
    assert record(fresh)["footYd"] == pytest.approx(70) and summary(fresh)["since"] == NOW
    fresh.advance(10)
    later = fresh.reload(gap=86400)         # logged out for a day
    assert record(later)["footYd"] == pytest.approx(140)
    later.advance(5)
    journey = summary(later)
    assert (journey["footYd"], journey["since"]) == (175, NOW), "tracking began at the first login"


def test_every_character_has_its_own_journey(rt):
    rt.set(speed=7.0)
    rt.advance(10)
    rt.set(name="Other", level=3, classId=8, faction="Horde")
    other = rt.reload(gap=120)              # log out, log in on another character
    journey = summary(other)
    assert journey["name"] == "Other" and journey["since"] == NOW + 10 + 120
    assert (journey["footYd"], journey["level"], journey["classId"], journey["faction"]) == (0, 3, 8, 2)
    other.set(speed=14.0, mounted=True)
    other.advance(10)
    assert set(lua_dict(other.db.journeys)) == {ME, "Other-Azeroth"}
    assert record(other, ME)["footYd"] == pytest.approx(70), "the first character's journey is untouched"
    assert record(other, ME)["level"] == 17
    assert record(other, "Other-Azeroth")["travelYd"] == pytest.approx(140)

    other.set(name="Tester", level=17, classId=4, faction="Alliance", mounted=False, speed=7.0)
    back = other.reload(gap=120)
    back.advance(10)
    assert summary(back)["footYd"] == 140 and summary(back)["since"] == NOW


def test_the_same_name_on_another_realm_is_another_character(rt):
    rt.set(speed=7.0)
    rt.advance(10)
    rt.set(realm="Azjol-Nerub")
    other = rt.reload(gap=60)
    assert summary(other)["name"] == "Tester" and summary(other)["footYd"] == 0
    assert set(lua_dict(other.db.journeys)) == {ME, "Tester-Azjol-Nerub"}


def test_no_journey_until_the_game_says_who_the_character_is(make_rt):
    rt = make_rt(state={"nameSecret": True})        # the name is hidden at login
    assert rt.ns.Tracker.GetSummary() is None
    assert rt.ns.Tracker.GetRecord() == (None, None)
    rt.set(speed=7.0)
    rt.advance(5)
    assert lua_dict(rt.db.journeys) == {}, "nothing is filed under '?'"
    rt.set(nameSecret=False)
    rt.advance(1)                                   # found out on its own, within a second
    assert summary(rt)["name"] == "Tester"
    rt.advance(10)
    assert summary(rt)["footYd"] == pytest.approx(70, abs=7)
    assert set(lua_dict(rt.db.journeys)) == {ME}
    assert rt.find_secrets() == []


def test_a_name_hidden_later_does_not_matter(rt):
    rt.set(speed=7.0)
    rt.advance(2)
    rt.set(nameSecret=True)
    rt.advance(8)
    assert summary(rt)["footYd"] == 70 and summary(rt)["name"] == "Tester"
    assert rt.find_secrets() == []


def test_tracking_costs_no_history_merge(make_rt):
    rt = make_rt(saved_variables=saved(*[own(NOW - 90000 + index * 100, 60) for index in range(50)]),
                 files=companion(measured(NOW - 90000, 60)))
    merges = ui.merge_counter(rt)
    totals = ui.Counter(rt, rt.ns.SessionLog, "totals")
    rt.slash("/walka hide")                         # (the HUD keeps its own count of the history)
    merges.reset()
    rt.set(speed=7.0)
    for _ in range(3):
        walk(rt, 60)
        rt.advance(30)
        summary(rt)
    assert merges.calls == 0 and totals.calls == 0, "the history is merged once, at start-up"


# ---- level, class, faction ---------------------------------------------------------------------------


def test_level_class_and_faction_are_read_at_login(make_rt):
    rt = make_rt(state={"level": 42, "maxLevel": 60, "classId": 11, "className": "Druid", "classFile": "DRUID",
                        "faction": "Horde"})
    journey = summary(rt)
    assert (journey["level"], journey["maxLevel"], journey["classId"], journey["faction"]) == (42, 60, 11, 2)
    assert rt.ns.Protocol.FACTION.HORDE == 2


@pytest.mark.parametrize("tag, faction", [("Alliance", 1), ("Horde", 2), ("Neutral", 0), ("", 0)])
def test_factions(make_rt, tag, faction):
    assert summary(make_rt(state={"faction": tag}))["faction"] == faction


def test_level_up(rt):
    assert summary(rt)["level"] == 17
    # The event carries the new level; UnitLevel() may still say the old one.
    rt.fire_event("PLAYER_LEVEL_UP", 18, 30, 12, 0, 0, 1, 1, 1, 1)
    assert rt.game.level == 17 and summary(rt)["level"] == 18
    rt.set(level=18)
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)        # a loading screen later
    assert summary(rt)["level"] == 18
    fresh = rt.reload()
    assert summary(fresh)["level"] == 18 and record(fresh)["level"] == 18


def test_level_up_with_nothing_usable_in_the_event(rt):
    rt.set(level=19)
    for payload in (None, "x", math.nan, rt.M.make_secret("number")):
        rt.fire_event("PLAYER_LEVEL_UP", payload)
        assert summary(rt)["level"] == 19, "then the game is asked"
    assert rt.find_secrets() == []


def test_the_level_cap_is_what_the_client_says_and_60_otherwise(make_rt):
    assert summary(make_rt(state={"maxLevel": 70}))["maxLevel"] == 70
    for nothing in ("nil", "0", "'sixty'", "0/0", "-1", "1e9"):
        rt = make_rt(load=False)
        rt.M.set_global("GetMaxPlayerLevel", rt.lua.eval(f"function() return {nothing} end"))
        rt.load_addon()
        assert summary(rt)["maxLevel"] == 60, nothing
    # A character above the cap we assume: the cap follows.
    assert summary(make_rt(state={"level": 63, "maxLevel": 60}))["maxLevel"] == 63


def test_a_level_the_game_does_not_know_yet_keeps_the_saved_one(rt):
    rt.set(level=0)                             # as UnitLevel() can be while the interface loads
    fresh = rt.reload()
    assert summary(fresh)["level"] == 17
    fresh.set(level=18)
    fresh.fire_event("PLAYER_ENTERING_WORLD", False, False)
    assert summary(fresh)["level"] == 18


def test_hidden_level_class_and_faction_are_never_touched(make_rt):
    rt = make_rt()
    secret = rt.lua.eval("function(value) return function() return value, value, value end end")
    for name in ("UnitLevel", "GetMaxPlayerLevel", "UnitClass", "UnitFactionGroup"):
        rt.M.set_global(name, secret(rt.M.make_secret("number")))
    info = lua_dict(rt.ns.Game.CharacterInfo())
    assert info == {"level": 0, "maxLevel": 0, "classId": 0, "faction": 0}
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)
    rt.advance(10)
    journey = summary(rt)
    assert (journey["level"], journey["maxLevel"], journey["classId"], journey["faction"]) == (17, 60, 4, 1)
    assert rt.find_secrets() == []
    rt.reload()


# ---- /played ------------------------------------------------------------------------------------------------


def played_lines(rt) -> list[str]:
    return [line for line in rt.printed if "ime played" in line]


def type_played(rt) -> None:
    """The player types /played: Blizzard's slash command does just this."""
    rt.run("RequestTimePlayed()")
    rt.tick()


def test_login_asks_nothing_and_the_chat_stays_quiet(rt):
    rt.advance(60)
    assert lua_list(rt.log.played_requests) == [], "the game prints every answer in the chat: Walka does not ask at login"
    assert rt.printed == []
    assert "played" not in summary(rt), "not known yet"
    assert record(rt)["played"] == 0


def test_the_players_own_played_is_heard(rt):
    rt.advance(5)
    type_played(rt)
    assert len(played_lines(rt)) == 2, "the game's own two lines, as always"
    assert len(lua_list(rt.log.played_requests)) == 1, "Walka did not ask a second time"
    assert summary(rt)["played"] == int(rt.game.played)
    assert record(rt)["played"] == 77105


def test_time_played_counts_on_between_answers(rt):
    type_played(rt)
    known = summary(rt)["played"]
    rt.advance(600)
    assert summary(rt)["played"] == known + 600 == pytest.approx(rt.game.played, abs=1)
    assert len(lua_list(rt.log.played_requests)) == 1, "no further questions"
    type_played(rt)                         # another answer is taken as it comes
    assert summary(rt)["played"] == int(rt.game.played)


def test_time_played_is_saved_at_logout_and_counted_on_from_there(rt):
    type_played(rt)
    rt.advance(100)
    expected = summary(rt)["played"]
    fresh = rt.reload(gap=7200)             # two hours logged out are not played time
    assert lua_list(fresh.log.played_requests) == [] and fresh.printed == []
    assert summary(fresh)["played"] == expected
    assert record(fresh)["played"] == expected
    fresh.advance(50)
    assert summary(fresh)["played"] == expected + 50 == pytest.approx(fresh.game.played, abs=1)


def test_time_played_of_another_character_is_its_own(rt):
    type_played(rt)
    rt.set(name="Other", played=120)
    other = rt.reload(gap=60)
    assert "played" not in summary(other)
    type_played(other)
    assert summary(other)["played"] == 120
    assert record(other, ME)["played"] == 77100


@pytest.mark.parametrize("payload", ["nil", "'x'", "0", "-5", "0/0", "1/0", "{}"])
def test_a_nonsense_answer_is_ignored(rt, payload):
    type_played(rt)
    known = summary(rt)["played"]
    rt.M.fire_event("TIME_PLAYED_MSG", rt.run(f"return {payload}"), 5)
    rt.check_errors()
    assert summary(rt)["played"] == known
    rt.reload()


def test_a_hidden_answer_is_ignored(rt):
    rt.M.fire_event("TIME_PLAYED_MSG", rt.M.make_secret("number"), rt.M.make_secret("number"))
    rt.check_errors()
    assert "played" not in summary(rt) and rt.find_secrets() == []


def test_request_played_asks_once_per_login(rt):
    tracker = rt.ns.Tracker
    assert tracker.RequestPlayed() is True
    assert tracker.RequestPlayed() is False, "not again while the answer is on its way"
    rt.tick()
    assert len(played_lines(rt)) == 2 and summary(rt)["played"] == int(rt.game.played)
    rt.advance(120)
    assert tracker.RequestPlayed() is False, "the game has answered during this login"
    assert len(lua_list(rt.log.played_requests)) == 1

    reloaded = rt.reload()                  # a /reload is the same login
    assert reloaded.ns.Tracker.RequestPlayed() is False
    reloaded.fire_event("PLAYER_ENTERING_WORLD", False, False)      # a loading screen
    assert reloaded.ns.Tracker.RequestPlayed() is False
    assert lua_list(reloaded.log.played_requests) == []

    again = reloaded.reload(gap=600)        # a new login: the saved figure may be stale
    assert again.ns.Tracker.RequestPlayed() is True
    again.tick()
    assert summary(again)["played"] == int(again.game.played)


def test_an_unanswered_request_is_not_repeated_at_once(make_rt):
    rt = make_rt(state={"playedAnswers": False})
    tracker = rt.ns.Tracker
    assert tracker.RequestPlayed() is True
    rt.advance(29)
    assert tracker.RequestPlayed() is False
    rt.advance(2)
    assert tracker.RequestPlayed() is True, "half a minute later it may ask again"
    assert len(lua_list(rt.log.played_requests)) == 2 and rt.printed == []


def test_request_played_before_the_character_is_known(make_rt):
    rt = make_rt(state={"nameSecret": True})
    assert rt.ns.Tracker.RequestPlayed() is False
    assert lua_list(rt.log.played_requests) == []


# ---- the real-world totals ------------------------------------------------------------------------------


def test_no_walks_no_totals(rt):
    assert real(rt) == {"steps": 0, "distanceM": 0, "kcal": 0, "seconds": 0, "walks": 0, "estimated": False}


def test_estimates_from_the_history_count_for_the_character_that_walked_them(make_rt):
    rt = make_rt(saved_variables=saved(
        own(NOW - 90000, 1800, steps=2500, distance=1400, kcal=100.4),
        own(NOW - 80000, 900, steps=1200, distance=700, kcal=50.3),
        own(NOW - 70000, 600, steps=800, distance=450, kcal=33.0, character="Other-Azeroth"),
        own(NOW - 60000, 600, steps=700, distance=400, kcal=30.0, character="?-Azeroth"),     # name hidden as it began
    ))
    assert real(rt) == {"steps": 3700, "distanceM": 2100, "kcal": 150, "seconds": 2700, "walks": 2, "estimated": True}
    assert summary(rt)["since"] == NOW - 90000, "the journey goes back to the first walk that counts"
    # A database from before journeys existed needs no conversion: nothing is settled yet.
    assert record(rt)["real"]["walks"] == 0
    assert set(lua_dict(rt.db.journeys)) == {ME}, "nothing is opened for other characters, or for nobody"


def test_measured_walks_replace_the_estimates_and_are_settled(make_rt):
    rt = make_rt(
        saved_variables=saved(
            own(NOW - 90000, 1800, steps=2500, distance=1400, kcal=100.4),
            own(NOW - 80000, 900, steps=1200, distance=700, kcal=50.3),
        ),
        files=companion(
            measured(NOW - 90010, 1810, steps=2610, distance=1503, kcal=111.6),
            measured(NOW - 50000, 600, steps=900, distance=500, kcal=40.0),        # a walk outside the game
        ),
    )
    assert real(rt) == {"steps": 3810, "distanceM": 2203, "kcal": 161, "seconds": 2710, "walks": 2, "estimated": True}
    settled = record(rt)["real"]
    assert settled == {"walks": 1, "seconds": 1810, "distanceM": 1503, "steps": 2610, "kcal": 111.6, "estimated": False}
    sessions = lua_plain(rt.db.sessions)
    assert sessions[0].get("banked") is True and "banked" not in sessions[1]
    # The history window lists what it always did: the mark is the journey's own.
    merged = [lua_plain(entry) for entry in lua_list(rt.walka.GetHistory(rt.walka))]
    assert [(entry["measured"], entry["steps"]) for entry in merged] == [(True, 900), (False, 1200), (True, 2610)]
    assert not any("banked" in entry for entry in merged)


def test_all_measured_means_not_estimated(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), files=companion(measured(NOW - 90000)))
    assert real(rt) == {"steps": 2600, "distanceM": 1500, "kcal": 110, "seconds": 1800, "walks": 1, "estimated": False}
    assert summary(rt)["since"] == NOW - 90000


def test_a_settled_walk_is_not_counted_again_at_the_next_login(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000), own(NOW - 80000)), files=companion(measured(NOW - 90000)))
    before = real(rt)
    for _ in range(3):
        rt = rt.reload(gap=3600)
        assert real(rt) == before
    assert record(rt)["real"]["walks"] == 1


def test_totals_do_not_shrink_when_the_companions_file_drops_old_walks(make_rt):
    """The companion lists its newest walks and folds the rest into a
    summary. A walk that was measured stays measured."""
    rt = make_rt(saved_variables=saved(own(NOW - 90000), own(NOW - 80000)),
                 files=companion(measured(NOW - 90000), measured(NOW - 80000, steps=2700)))
    before = real(rt)
    assert before["steps"] == 5300 and not before["estimated"]
    rt.files["CompanionData.lua"] = companion(more=", older = { sessions = 2, steps = 5300, seconds = 3600 }")["CompanionData.lua"]
    fresh = rt.reload(gap=3600)
    assert lua_list(fresh.walka.GetCompanionData(fresh.walka).sessions) == []
    assert real(fresh) == before
    # ... and not when the file is gone altogether (the addon folder replaced by an update).
    fresh.files["CompanionData.lua"] = "WalkaCompanionData = nil"
    assert real(fresh.reload(gap=60)) == before


def test_totals_do_not_shrink_when_the_history_is_pruned(rt):
    rt.walka.MAX_SESSIONS = 3               # (400 in the game)
    rt.set(speed=7.0)
    seen = []
    for index in range(7):
        walk(rt, 60 + index * 10)
        seen.append(real(rt))
        assert len(lua_list(rt.db.sessions)) == min(index + 1, 3)
        if index > 0:
            for key in ("steps", "distanceM", "kcal", "seconds", "walks"):
                assert seen[-1][key] > seen[-2][key], f"{key} after walk {index + 1}"
    assert seen[-1]["walks"] == 7 and seen[-1]["estimated"]
    settled = record(rt)["real"]
    assert settled["walks"] == 4 and settled["estimated"] is True, "the four pruned walks were never confirmed"
    assert real(rt.reload(gap=100)) == seen[-1]


def test_pruning_a_measured_walk_keeps_the_measured_numbers(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000), own(NOW - 80000)), files=companion(measured(NOW - 90000)))
    rt.walka.MAX_SESSIONS = 2
    before = real(rt)
    rt.set(speed=7.0)
    walk(rt, 60)                            # pushes the measured walk out of the list
    after = real(rt)
    assert len(lua_list(rt.db.sessions)) == 2
    assert after["walks"] == before["walks"] + 1 and after["steps"] > before["steps"]
    settled = record(rt)["real"]
    assert (settled["walks"], settled["steps"], settled["estimated"]) == (1, 2600, False)


def test_clearing_the_history_keeps_the_journey(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000), own(NOW - 80000), own(NOW - 70000, character="Other-Azeroth")),
                 files=companion(measured(NOW - 90000)))
    before = real(rt)
    rt.walka.ClearHistory(rt.walka)
    rt.check_errors()
    assert lua_list(rt.db.sessions) == []
    assert real(rt) == before
    assert record(rt)["real"] == {"walks": 2, "seconds": 3600, "distanceM": 2900, "steps": 5100, "kcal": 210, "estimated": True}
    # The other character's walk went to its journey, which starts with that walk.
    other = record(rt, "Other-Azeroth")
    assert other["real"]["steps"] == 2500 and other["real"]["estimated"] is True and other["since"] == NOW - 70000
    assert real(rt.reload()) == before


def test_clearing_the_history_before_anything_else_has_read_it(make_rt):
    # The measured numbers are taken over at start-up, so a clear right
    # after login still finds them - whatever happened in between.
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), files=companion(measured(NOW - 90000)))
    rt.walka.ClearHistory(rt.walka)
    assert real(rt) == {"steps": 2600, "distanceM": 1500, "kcal": 110, "seconds": 1800, "walks": 1, "estimated": False}


def test_the_session_in_progress_counts_once_it_is_long_enough_to_keep(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000, steps=2500)))
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(15)                          # the countdown, then 12 seconds on the belt
    session = rt.walka.GetSession(rt.walka)
    assert 0 < session.seconds < rt.walka.MIN_SESSION_SECONDS
    assert real(rt)["steps"] == 2500 and real(rt)["walks"] == 1, "a start that may be thrown away does not show"
    rt.advance(15)
    session = lua_dict(rt.walka.GetSession(rt.walka))
    now = real(rt)
    assert now["walks"] == 2 and now["estimated"] is True
    assert now["steps"] == 2500 + math.floor(session["steps"] + 1e-6)
    assert now["seconds"] == 1800 + math.floor(session["seconds"] + 1e-6)
    assert now["distanceM"] == 1400 + math.floor(session["distanceM"] + 1e-6)
    assert now["kcal"] == math.floor(100 + session["kcal"] + 1e-6)
    rt.advance(60)
    assert real(rt)["steps"] > now["steps"]


def test_stopping_a_session_neither_drops_nor_doubles_it(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(120)
    before = real(rt)
    rt.slash("/walka stop")
    after = real(rt)
    assert after["walks"] == before["walks"] == 1
    for key in ("steps", "distanceM", "kcal", "seconds"):
        assert 0 <= after[key] - before[key] <= 1, f"{key}: only the rounding of the filed record, and never down"
    rt.advance(30)
    assert real(rt) == after
    assert real(rt.reload()) == after


def test_a_session_too_short_to_keep_never_counts(rt):
    rt.set(speed=7.0)
    walk(rt, 10)
    assert lua_list(rt.db.sessions) == []
    assert real(rt) == {"steps": 0, "distanceM": 0, "kcal": 0, "seconds": 0, "walks": 0, "estimated": False}


def test_a_paused_session_still_counts(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(60)
    rt.slash("/walka pause")
    rt.advance(5)                           # the belt coasts to a halt
    paused = real(rt)
    rt.advance(60)
    assert real(rt) == paused and paused["walks"] == 1


def test_a_session_begun_on_another_character_is_that_characters(rt):
    """Logging over to another character within three minutes carries the
    session along; it stays the walk of the character it began on."""
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(120)
    rt.set(name="Other")
    other = rt.reload(gap=30)
    assert other.walka.IsActive(other.walka) and other.walka.GetSession(other.walka).character == ME
    other.advance(60)
    assert real(other)["walks"] == 0 and real(other)["steps"] == 0
    other.slash("/walka stop")
    assert real(other)["walks"] == 0
    other.set(name="Tester")
    back = other.reload(gap=30)
    assert real(back)["walks"] == 1 and real(back)["steps"] > 100


def test_a_session_left_running_at_logout_is_filed_and_settled_at_the_next_login(rt):
    """Logged out in mid-walk; the companion closed the walk and wrote its
    file. The next login files the session and finds it measured."""
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(600)
    start = rt.walka.GetSession(rt.walka).start
    rt.files["CompanionData.lua"] = companion(measured(start, 600, steps=912, distance=455, kcal=41.2))["CompanionData.lua"]
    fresh = rt.reload(gap=3600)
    assert not fresh.walka.IsActive(fresh.walka) and len(lua_list(fresh.db.sessions)) == 1
    assert real(fresh) == {"steps": 912, "distanceM": 455, "kcal": 41, "seconds": 600, "walks": 1, "estimated": False}


def test_a_measured_walk_shared_by_two_records_is_split_by_their_time(make_rt):
    # One treadmill walk of an hour, during which the addon's session was
    # restarted on another character: 40 minutes and 20 minutes.
    rt = make_rt(
        saved_variables=saved(
            own(NOW - 90000, 2400, steps=3000),
            own(NOW - 87600, 1200, steps=1500, character="Other-Azeroth"),
        ),
        files=companion(measured(NOW - 90000, 3600, steps=6000, distance=3000, kcal=240.0)),
    )
    assert real(rt) == {"steps": 4000, "distanceM": 2000, "kcal": 160, "seconds": 2400, "walks": 1, "estimated": False}
    other = record(rt, "Other-Azeroth")["real"]
    assert other["steps"] == pytest.approx(2000) and other["kcal"] == pytest.approx(80) and other["walks"] == 1
    # Between them: exactly the measured walk, not more.
    assert record(rt)["real"]["steps"] + other["steps"] == pytest.approx(6000)


def test_records_without_any_time_share_a_measured_walk_evenly(make_rt):
    rt = make_rt(
        saved_variables=saved(own(NOW - 90000, 1800).replace("seconds=1800", "seconds=0"),
                              own(NOW - 89900, 1700).replace("seconds=1700", "seconds=0")),
        files=companion(measured(NOW - 90000, 1800, steps=3000, distance=1500, kcal=120.0)),
    )
    assert real(rt)["steps"] == 3000 and real(rt)["walks"] == 2


def test_damaged_history_records_do_not_break_the_totals(make_rt):
    rt = make_rt(saved_variables=saved(
        "5", "'x'", "{}", "{start='x',character='Tester-Azeroth'}",
        "{start=5,['end']=2,steps=-4,character='Tester-Azeroth'}",
        f"{{start={NOW - 5000},['end']=1e300,seconds=1/0,steps=0/0,distanceM='far',kcal={{}},character='Tester-Azeroth'}}",
        f"{{start={NOW - 3000},['end']={NOW - 1200},seconds=1750,distanceM=1400,steps=2000,kcal=100.5,character='Tester-Azeroth'}}",
        f"{{start={NOW - 1000},['end']={NOW - 100},seconds=900,distanceM=700,steps=1000,kcal=50,character={{}}}}",
    ), files=companion(measured(NOW - 3000, 1800)))
    totals = real(rt)
    assert all(math.isfinite(totals[key]) for key in ("steps", "distanceM", "kcal", "seconds", "walks"))
    assert totals["steps"] >= 2600
    assert summary(rt)["since"] == NOW - 3000, "a walk from 1970 does not move the start of the journey"
    rt.walka.ClearHistory(rt.walka)
    rt.check_errors()
    rt.reload()


@pytest.mark.parametrize("journeys", ["5", "'x'", "{ 5, 'x', ['Tester-Azeroth'] = 'gone', Other = { real = 5 } }"])
def test_a_damaged_journeys_table_is_survivable(make_rt, journeys):
    rt = make_rt(saved_variables=saved(own(NOW - 90000), more=f", journeys = {journeys}"))
    rt.set(speed=7.0)
    rt.advance(10)
    journey = summary(rt)
    assert journey["footYd"] == 70 and journey["steps"] == 2500
    rt.reload()


def test_numbers_are_whole_and_never_rounded_up(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000, 1800, steps=2500, distance=1400, kcal=100.96)))
    totals = real(rt)
    assert totals["kcal"] == 100, "a calorie counts when it is burnt"
    assert all(isinstance(totals[key], int) for key in ("steps", "distanceM", "kcal", "seconds", "walks"))


def test_a_walk_begun_while_the_name_is_hidden_still_counts(rt):
    rt.set(speed=7.0, nameSecret=True)      # (as the game may do in a fight)
    walk(rt, 60)
    assert lua_plain(rt.db.sessions)[0]["character"] == ME
    assert real(rt)["walks"] == 1 and real(rt)["steps"] > 30
    assert rt.find_secrets() == []


def test_the_totals_never_step_back_while_walking_stopping_and_pruning(make_rt):
    """Second by second through a string of sessions, with fractions in the
    settled totals (a measured walk shared with another character) and the
    history being pruned as it goes."""
    rt = make_rt(
        saved_variables=saved(
            own(NOW - 90000, 2333, steps=3000, kcal=100.37),
            own(NOW - 87600, 1111, steps=1500, character="Other-Azeroth"),
            own(NOW - 50000, 700, steps=901, kcal=33.33),
        ),
        files=companion(measured(NOW - 90000, 3600, steps=6001, distance=3001, kcal=240.7)),
    )
    rt.walka.MAX_SESSIONS = 4
    assert record(rt)["real"]["steps"] % 1 != 0, "the share of the measured walk is not a whole number"
    previous = real(rt)
    rt.set(speed=7.0)
    for length in (25, 61, 33, 47, 22, 90):
        rt.slash("/walka start")
        for second in range(length + 3):
            if second == length:
                rt.slash("/walka stop")
            rt.advance(1)
            now = real(rt)
            for key in ("steps", "distanceM", "kcal", "seconds", "walks"):
                assert now[key] >= previous[key], f"{key} went back at second {second} of a {length} s walk"
            previous = now
    # Two walks to begin with and six sessions - but the 22-second one moved
    # the belt for less than the 20 seconds it takes to count as a walk.
    assert previous["walks"] == 7 and len(lua_list(rt.db.sessions)) == 4
    assert real(rt.reload()) == previous
