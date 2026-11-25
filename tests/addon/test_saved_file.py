"""What the game tells the companion app: the addon's saved file.

The addon draws nothing on the screen. What the app needs - the walking
sessions, the one still running, every character's journey - is in the saved
variables, which the game writes to a file at logout and on /reload. These
tests run the real addon, write the file the way the game does (the mock
client's own writer), and read it with the companion's real reader
(game_file.py).
"""

from __future__ import annotations

import pytest
from walka import game_file, savedvars
from walka.journey import character_id

from .harness import lua_plain
from .test_journey import ME


def logout_file(rt) -> bytes:
    """Log out, and the file the game then writes for the account."""
    rt.fire_event("PLAYER_LEAVING_WORLD")
    rt.fire_event("PLAYER_LOGOUT")
    return b"\r\nWalkaDB = " + rt.M.serialize("WalkaDB").encode("utf-8") + b"\r\n"


def read(file: bytes, saved_at: float = 0.0) -> game_file.SavedGame:
    saved = game_file.decode(savedvars.parse(file)["WalkaDB"], saved_at)
    assert saved is not None
    return saved


def test_the_addon_draws_nothing(rt):
    """No strip, no window: the frames the addon makes have nothing to draw."""
    assert rt.global_("WalkaBeaconFrame") is None
    ticker = rt.global_("WalkaTickFrame")
    assert ticker is not None and rt.M.regions(ticker) is not None and len(list(rt.M.regions(ticker).values())) == 0
    rt.tick(20)
    for name in rt.created_globals():
        widget = rt.global_(name)
        if rt.lua.eval("function(w) return type(w) == 'table' and w.GetObjectType ~= nil end")(widget):
            assert len(list(rt.M.regions(widget).values())) == 0, f"{name} draws something"


def test_a_finished_session_is_in_the_file_with_its_character(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(120)
    rt.slash("/walka stop")
    (found,) = read(logout_file(rt)).sessions
    (record,) = [lua_plain(r) for r in rt.db.sessions.values()]
    assert (found.start, found.end, found.number) == (record["start"], record["end"], record["id"])
    assert found.character == "Tester" and not found.running
    assert found.key == game_file.djb2(ME)


def test_the_session_the_addon_starts_by_itself_is_in_the_file_while_it_runs(make_rt):
    """The usual case: the addon marks a session at login, and the player
    logs out with it running. The file says it ran until then."""
    rt = make_rt(auto_session=True)
    rt.tick()
    start = rt.walka.session.start
    rt.advance(600)
    now = rt.game.clock
    (found,) = read(logout_file(rt)).sessions
    assert found.running and found.start == start and found.end == pytest.approx(now, abs=1)
    assert found.character == "Tester"


def test_the_journey_is_saved_for_the_app_at_logout(make_rt):
    rt = make_rt(auto_session=True)
    rt.set(speed=7.0)
    rt.advance(60)
    summary = lua_plain(rt.ns.Tracker.GetSummary())
    file = logout_file(rt)
    (journey,) = read(file).journeys
    assert journey.name == "Tester" and journey.id == character_id(ME)
    assert journey.foot_yd == summary["footYd"] and journey.travel_yd == summary["travelYd"]
    assert (journey.real_steps, journey.walks, journey.estimated) == (summary["steps"], summary["walks"], summary["estimated"])
    assert journey.seen_at == pytest.approx(rt.game.clock, abs=1), "stamped with the moment it was saved"
    assert b"Azeroth" in file, "(the file has the realm, as the game names it ...)"
    assert "Azeroth" not in repr(journey), "(... and what the app keeps of it does not)"


@pytest.mark.parametrize("gap", [0.0, 3600.0])
def test_a_reload_or_a_new_login_keeps_what_the_app_reads(make_rt, gap):
    rt = make_rt(auto_session=True)
    rt.advance(300)
    fresh = rt.reload(gap=gap)
    fresh.advance(300)
    sessions = read(logout_file(fresh)).sessions
    if gap:
        # The old session was closed when it was saved; a new one started at login.
        assert [s.running for s in sessions] == [False, True]
    else:
        # The same session carries on through a /reload.
        (running,) = sessions
        assert running.running and running.end - running.start == pytest.approx(600, abs=1)
