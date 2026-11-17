"""Combat on a Midnight-family client: unit stats are "secret values".

GetUnitSpeed() still returns something in combat, but arithmetic or an
ordering comparison on it raises. Walka/Game.lua must notice, estimate the
speed from what is still public, flag the frame ESTIMATED - and never keep a
secret anywhere.

(See tests/addon/__init__.py for what Lua 5.1 lets this mock catch: it
cannot see `==` against a plain value, truth tests or table-key use.)
"""

from __future__ import annotations

import pytest
from walka.protocol import Flags

from .conftest import MPH_STEP, nearest_step
from .harness import lua_dict, lua_list, lua_plain


def frame(rt):
    """The live frame on the strip (one tick later if a stat frame is showing)."""
    return rt.live_frame()


def tick_checked(rt, count=1):
    """Tick and, after every tick, look for leaked secrets everywhere the
    addon could have put one."""
    for _ in range(count):
        rt.tick()
        assert rt.find_secrets() == [], "a secret value is being kept"
        state = rt.ns.Game.Read()
        for key, value in state.items():
            assert not rt.M.is_secret(value), f"Game.Read().{key} is secret"
        assert isinstance(state.speed, (int, float))


def remembered(rt):
    return lua_dict(rt.ns.Game.remembered)


# ---- the mock itself: what a secret is -------------------------------------------------


def test_the_mock_really_hands_out_secrets_in_combat(rt):
    rt.set(speed=7.0)
    rt.enter_combat()
    results = lua_list(rt.run("return { GetUnitSpeed('player') }"))
    assert len(results) == 4
    assert all(rt.M.is_secret(value) for value in results)
    assert rt.run("return C_Secrets.ShouldUnitStatsBeSecret()") is True
    assert rt.run("return issecretvalue((GetUnitSpeed('player')))") is True
    assert rt.run("return type((GetUnitSpeed('player')))") == "number"     # a secret keeps its Lua type


@pytest.mark.parametrize(
    "expression",
    [
        "s + 1", "1 + s", "s - s", "s * 2", "s / 2", "s % 2", "s ^ 2", "-s",       # arithmetic
        "s < 1", "1 < s", "s <= s", "s > 0", "s >= 0",                             # ordering
        "s .. ''", "'' .. s",                                                      # concatenation
        "#s", "s.x", "s[1]", "s()",                                                # length, index, call
        "s == GetUnitSpeed('player')",                                             # comparing two secrets
        "tostring(s)", "math.floor(s)", "string.format('%d', s)", "math.max(s, 0)",
        "print(s)", "tostring(nil) .. tostring(s)",
    ],
)
def test_operations_on_a_secret_raise(rt, expression):
    rt.enter_combat()
    with pytest.raises(Exception) as error:
        rt.run(f"local s = GetUnitSpeed('player'); local function f(s) return {expression} end; return f(s)")
    assert "secret" in str(error.value) or "expected" in str(error.value) or "compare" in str(error.value)


def test_documented_limits_of_the_mock(rt):
    # What the real client rejects but Lua 5.1 cannot intercept. If one of
    # these ever starts raising, the note in tests/addon/__init__.py is stale.
    rt.enter_combat()
    assert rt.run("local s = GetUnitSpeed('player'); return s == 0") is False
    assert rt.run("local s = GetUnitSpeed('player'); return s ~= 0") is True
    assert rt.run("local s = GetUnitSpeed('player'); if s then return 'truthy' end") == "truthy"
    assert rt.run("local s = GetUnitSpeed('player'); local t = {}; t[s] = 1; return next(t) == s") is True


def test_widgets_refuse_secrets(rt):
    rt.enter_combat()
    with pytest.raises(Exception, match="secret value passed to"):
        rt.run("local f = CreateFrame('Frame'); f:SetWidth((GetUnitSpeed('player')))")
    with pytest.raises(Exception, match="secret value passed to"):
        rt.run("local f = CreateFrame('Frame'); f:CreateFontString():SetText((GetUnitSpeed('player')))")


def test_leak_scan_finds_secrets_in_tables_widgets_and_upvalues(make_rt):
    # The scan must be able to fail: plant secrets the way a buggy addon would.
    planted = (
        "local addonName, ns = ...\n"
        "local hidden\n"
        "local frame = CreateFrame('Frame', 'WalkaLeakTest')\n"
        "function ns.Leak()\n"
        "    local speed = GetUnitSpeed('player')\n"
        "    hidden = speed\n"                       # an upvalue
        "    ns.lastSpeed = speed\n"                 # the addon's private table
        "    frame.speed = speed\n"                  # a field on a widget
        "    WalkaDB.leak = { deep = { speed } }\n"  # the saved variables
        "    frame:SetScript('OnUpdate', function() return speed end)\n"   # a closure in a script
        "end\n"
        "function ns.Peek() return hidden end\n"
    )
    rt = make_rt(files={"CompanionData.lua": planted})
    assert rt.find_secrets() == []
    rt.enter_combat()
    rt.ns.Leak()
    found = "\n".join(rt.find_secrets())
    assert "ns.lastSpeed" in found
    assert "WalkaDB.leak.deep.1" in found
    assert "^hidden" in found
    assert ".speed" in found
    assert "<OnUpdate>^speed" in found
    with pytest.raises(Exception, match="secret value in SavedVariables"):
        rt.reload()


# ---- the addon in combat ----------------------------------------------------------------


def test_combat_never_errors_and_frames_are_flagged_estimated(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    tick_checked(rt, 50)
    assert not frame(rt).has(Flags.ESTIMATED)

    rt.enter_combat()
    tick_checked(rt, 100)
    current = frame(rt)
    assert current.has(Flags.ESTIMATED) and current.has(Flags.COMBAT) and current.has(Flags.MOVING)
    assert rt.walka.GetGameState(rt.walka).estimated is True

    rt.leave_combat()
    tick_checked(rt, 5)
    assert not frame(rt).has(Flags.ESTIMATED)
    assert rt.walka.GetGameState(rt.walka).estimated is False


def test_speed_is_zero_when_standing_and_the_remembered_run_speed_when_moving(rt):
    rt.set(speed=7.0)
    tick_checked(rt, 3)
    rt.enter_combat()

    rt.set(speed=0)                         # standing: IsPlayerMoving() is false
    tick_checked(rt, 3)
    assert frame(rt).char_speed_raw == 0
    assert not frame(rt).has(Flags.MOVING)

    rt.set(speed=5.3)                       # moving, real speed hidden (a slow, say)
    tick_checked(rt, 3)
    assert frame(rt).char_speed_yps == pytest.approx(7.0), "the estimate is the remembered run speed"
    assert frame(rt).has(Flags.MOVING)


def test_remembered_speeds_come_only_from_readable_samples(rt):
    assert remembered(rt)["run"] == 7.0                     # the stock run speed until told otherwise
    rt.set(speed=8.05, runSpeed=8.05, swimSpeed=5.2)        # a 15% speed enchant, read out of combat
    tick_checked(rt, 2)
    assert remembered(rt) == {"run": 8.05, "swim": 5.2}

    rt.enter_combat()
    rt.set(speed=11.9, runSpeed=11.9, swimSpeed=9.9)        # Sprint in combat: hidden from the addon
    tick_checked(rt, 20)
    assert remembered(rt) == {"run": 8.05, "swim": 5.2}, "hidden values must not be remembered"
    assert frame(rt).char_speed_yps == pytest.approx(8.05, abs=0.011)

    rt.leave_combat()                                       # readable again
    tick_checked(rt, 2)
    assert remembered(rt)["run"] == 11.9
    assert frame(rt).char_speed_yps == pytest.approx(11.9, abs=0.011)


def test_mounted_and_swimming_estimates(rt):
    # Never seen mounted: guess from the run speed.
    rt.enter_combat()
    rt.set(speed=14.0, mounted=True)
    tick_checked(rt, 2)
    assert frame(rt).char_speed_yps == pytest.approx(7.0 * 1.6, abs=0.011)
    assert frame(rt).flags & Flags.MOUNTED

    # Seen mounted out of combat: use that.
    rt.leave_combat()
    rt.set(speed=14.0, runSpeed=14.0, mounted=True)
    tick_checked(rt, 2)
    assert remembered(rt)["mounted"] == 14.0
    assert remembered(rt)["run"] == 7.0, "a mounted reading is not the run speed"
    rt.enter_combat()
    tick_checked(rt, 2)
    assert frame(rt).char_speed_yps == pytest.approx(14.0)

    rt.set(mounted=False, swimming=True, speed=4.0, runSpeed=7.0)
    tick_checked(rt, 2)
    assert frame(rt).char_speed_yps == pytest.approx(4.7222, abs=0.011)
    assert frame(rt).flags & Flags.SWIMMING


def test_session_keeps_working_through_combat(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    tick_checked(rt, 300)
    rt.enter_combat()
    before = lua_dict(rt.walka.GetSession(rt.walka))
    tick_checked(rt, 300)
    after = lua_dict(rt.walka.GetSession(rt.walka))
    assert after["yards"] - before["yards"] == pytest.approx(7.0 * 30, rel=0.01)
    assert after["steps"] > before["steps"] and after["kcal"] > before["kcal"]
    # The running pace (on the belt's 0.1 mph grid), fight or no fight.
    running = nearest_step(rt.ns.SpeedModel.DEFAULTS.runKmh, MPH_STEP)
    assert rt.walka.GetLiveSpeed(rt.walka)[1] == pytest.approx(running, abs=1e-6)
    rt.slash("/walka status")
    assert "estimated" in rt.printed[-2]
    record = rt.walka.StopSession(rt.walka)
    assert all(isinstance(value, (int, float, str)) for value in lua_dict(record).values())
    assert rt.find_secrets() == []


def test_reload_in_combat_writes_clean_saved_variables(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.enter_combat()
    tick_checked(rt, 100)
    fresh = rt.reload()                     # the serialiser refuses secrets
    assert fresh.walka.IsActive(fresh.walka)
    tick_checked(fresh, 20)
    assert frame(fresh).has(Flags.ESTIMATED)


def test_login_in_combat(make_rt):
    # /reload during a fight: the very first read is already secret.
    rt = make_rt(state={"combat": True, "statsSecret": True, "speed": 7.0})
    assert rt.find_secrets() == []
    tick_checked(rt, 5)
    assert frame(rt).char_speed_yps == pytest.approx(7.0)
    assert frame(rt).has(Flags.ESTIMATED)


@pytest.mark.parametrize(
    "state",
    [
        {"predicateErrors": True},                      # the predicate itself fails
        {"predicateLies": True},                        # it says "readable" but the values are secret
        {"statsSecret": True, "combat": False},         # secret outside combat (an instance rule)
    ],
    ids=["predicate-errors", "predicate-wrong", "secret-out-of-combat"],
)
def test_defence_in_depth(rt, state):
    rt.set(speed=7.0)
    tick_checked(rt, 2)
    rt.set(**{"statsSecret": True, **state})
    rt.slash("/walka start")
    tick_checked(rt, 30)
    assert frame(rt).has(Flags.ESTIMATED)
    assert frame(rt).char_speed_yps == pytest.approx(7.0)
    rt.set(speed=0)
    tick_checked(rt, 2)
    assert frame(rt).char_speed_raw == 0


def test_a_failing_predicate_with_readable_values_is_treated_as_hidden(rt):
    # Cautious: if the client will not say, do not touch the values.
    rt.set(speed=7.0, predicateErrors=True)
    tick_checked(rt, 3)
    assert frame(rt).has(Flags.ESTIMATED)


def test_afk_status_hidden_by_chat_lockdown(rt):
    # UnitIsAFK is secret during a "chat messaging lockdown" (instances).
    rt.slash("/walka start")
    rt.set(afk=True, chatLockdown=True)
    tick_checked(rt, 10)
    assert not frame(rt).has(Flags.AFK), "a hidden AFK state reads as not AFK"
    assert not rt.walka.IsPaused(rt.walka)
    rt.set(chatLockdown=False)
    tick_checked(rt, 2)
    assert rt.walka.IsPaused(rt.walka)


def test_character_name_hidden(rt):
    rt.set(nameSecret=True)
    assert rt.ns.Game.CharacterName() == "?-Azeroth"
    rt.slash("/walka start")
    # The session is this character's all the same: its name was read at login.
    assert rt.walka.GetSession(rt.walka).character == "Tester-Azeroth"
    tick_checked(rt, 3)
    rt.slash("/walka stop")
    rt.set(nameSecret=False, realm="")
    assert rt.ns.Game.CharacterName() == "Tester"


def test_character_name_hidden_from_login_on(make_rt):
    rt = make_rt(state={"nameSecret": True})
    rt.slash("/walka start")
    assert rt.walka.GetSession(rt.walka).character == "?-Azeroth", "nobody's walk, rather than a guess"
    tick_checked(rt, 3)
    rt.set(nameSecret=False)
    tick_checked(rt, 10)
    rt.slash("/walka stop")
    rt.slash("/walka start")
    assert rt.walka.GetSession(rt.walka).character == "Tester-Azeroth"


def test_character_class_hidden(rt):
    # UnitClass is secret when "unit identity" is restricted. The class is
    # only read for the journey record (Game.CharacterInfo).
    game = rt.ns.Game
    assert lua_dict(game.CharacterInfo()) == {"level": 17, "maxLevel": 60, "classId": 4, "faction": 1}
    rt.set(identitySecret=True)
    assert all(rt.M.is_secret(value) for value in lua_list(rt.run("return { UnitClass('player') }")))
    info = lua_dict(game.CharacterInfo())
    assert info == {"level": 17, "maxLevel": 60, "classId": 0, "faction": 1}, "a hidden class reads as not known"
    rt.fire_event("PLAYER_ENTERING_WORLD", False, False)         # the tracker reads the character again
    tick_checked(rt, 20)
    record, _ = rt.ns.Tracker.GetRecord()
    assert record.classId == 4, "what the journey already knew is kept"
    rt.reload()                                                 # nothing hidden reached the saved variables


def test_everything_secret_at_once_for_a_long_fight(rt):
    rt.set(speed=7.0, mounted=False)
    rt.slash("/walka start")
    rt.enter_combat()
    rt.set(chatLockdown=True, nameSecret=True, identitySecret=True)
    states = [
        {"speed": 7.0}, {"speed": 0}, {"speed": 9.0, "falling": True}, {"speed": 4.0, "swimming": True},
        {"speed": 14.0, "mounted": True, "swimming": False, "falling": False}, {"speed": 0, "dead": True},
        {"speed": 8.75, "dead": True, "mounted": False}, {"dead": False, "afk": True}, {"afk": False},
    ]
    for index in range(180):
        rt.set(**states[index % len(states)])
        tick_checked(rt, 5)
    rt.slash("/walka status")
    rt.slash("/walka pause")
    rt.slash("/walka resume")
    rt.slash("/walka stop")
    assert lua_plain(rt.db.sessions)[0]["character"] == "Tester-Azeroth"
    fresh = rt.reload()
    assert fresh.find_secrets() == []


def test_every_secret_capable_api_the_addon_calls_is_exercised_here(rt, snapshot):
    # If Game.lua starts calling another API that can return secrets, this
    # test names it so that the mock (and the tests above) learn about it.
    source = (rt.addon_dir / "Game.lua").read_text(encoding="utf-8")
    covered = {"GetUnitSpeed", "UnitIsAFK", "UnitName", "UnitClass"}
    for name, tags in snapshot.secret.items():
        bare = name.split(".")[-1]
        if bare + "(" in source and "." not in name:
            assert name in covered, f"{name} can return secret values ({', '.join(tags)}) but no test makes it secret"
    for name in covered:
        assert name in snapshot.secret, f"{name} is not tagged as secret-capable in this client any more"


def test_ghost_speed_is_not_remembered_for_the_combat_estimate(rt):
    """A ghost runs faster than the living character; that speed must not
    become the estimate used once the real speed is hidden in combat."""
    game = rt.ns.Game
    rt.set(speed=7.0, runSpeed=7.0)
    rt.tick(5)
    assert game.remembered.run == 7.0
    rt.set(dead=True, speed=8.75, runSpeed=8.75)
    rt.tick(5)
    assert game.remembered.run == 7.0
    rt.set(dead=False, speed=7.0, runSpeed=7.0)
    rt.enter_combat()
    rt.tick(5)
    state = rt.walka.GetGameState(rt.walka)
    assert state.estimated and state.speed == 7.0
