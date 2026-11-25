"""The session the addon starts by itself.

Nobody has to type a command: once the character is known after login the
addon marks a session, so that the walk the app records while it runs is
filed under this character. It estimates nothing - it cannot know whether
anybody is on the pad - so it counts only through the treadmill's measured
walk, once the app's file brings it.
"""

from __future__ import annotations

from walka.protocol import Flags

from .harness import lua_list, lua_plain
from .test_journey import ME, NOW, companion, measured, real, saved


def frame(rt):
    rt.tick()
    return rt.live_frame()


def test_it_starts_by_itself_at_login(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick()
    session = lua_plain(rt.walka.session)
    assert session["auto"] is True and session["character"] == ME
    assert Flags.ACTIVE in frame(rt).flags and frame(rt).session == session["id"]
    assert any("connected to the Walka app" in line for line in rt.printed)
    assert rt.walka.GetLiveSpeed(rt.walka) == 0, "it counts nothing by itself"


def test_it_estimates_nothing_and_alone_is_no_walk(make_rt):
    rt = make_rt(auto_session=True)
    rt.set(speed=7.0)
    rt.advance(600)
    session = lua_plain(rt.walka.session)
    assert session["seconds"] >= 599 and session["steps"] == 0 and session["distanceM"] == 0
    assert real(rt)["walks"] == 0 and real(rt)["steps"] == 0, "not counted while it runs"
    rt.slash("/walka stop")
    (kept,) = lua_list(rt.db.sessions)
    assert kept.auto is True
    assert lua_list(rt.walka.GetHistory(rt.walka)) == [], "without a measured walk in it, it is no walk"
    assert real(rt)["walks"] == 0
    assert any("session finished - the Walka app has" in line for line in rt.printed)


def test_the_measured_walk_in_it_counts_for_the_character(make_rt):
    start = NOW - 7200
    record = (
        f"{{id=3,start={start},['end']={start + 3600},seconds=3600,distanceM=0,steps=0,kcal=0,yards=500,"
        f"maxKmh=0,character={ME!r},auto=true}}"
    )
    rt = make_rt(auto_session=True, saved_variables=saved(record), files=companion(measured(start + 600, 1200)))
    totals = real(rt)
    assert totals["walks"] == 1 and totals["steps"] == 2600 and totals["seconds"] == 1200
    assert totals["estimated"] is False


def test_it_is_started_once_a_login_and_a_stop_is_respected(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick()
    rt.slash("/walka stop")
    rt.tick(50)
    assert rt.walka.session is None, "stopped by the player: it stays stopped"
    fresh = rt.reload(gap=600)
    fresh.tick()
    assert fresh.walka.session is not None and fresh.walka.session.auto is True, "the next login starts it again"


def test_a_reload_keeps_it(make_rt):
    rt = make_rt(auto_session=True)
    rt.advance(30)
    first = lua_plain(rt.walka.session)
    fresh = rt.reload()
    fresh.tick()
    again = lua_plain(fresh.walka.session)
    assert (again["id"], again["auto"], again["start"]) == (first["id"], True, first["start"])


def test_it_waits_for_the_game_to_name_the_character(make_rt):
    rt = make_rt(auto_session=True, state={"nameSecret": True})
    rt.tick(5)
    assert rt.walka.session is None
    rt.set(nameSecret=False)
    rt.tick()
    assert rt.walka.session is not None and rt.walka.session.character == ME


def test_it_can_be_switched_off_and_on(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick()
    rt.slash("/walka stop")
    rt.slash("/walka auto off")
    assert rt.db.settings.autoSession is False
    fresh = rt.reload(gap=600)
    fresh.tick(5)
    assert fresh.walka.session is None
    fresh.slash("/walka auto on")
    assert fresh.walka.session is not None and fresh.walka.session.auto is True


def test_the_addons_menu_pauses_and_stops_it(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick()
    click = rt.global_(rt.toc.metadata["AddonCompartmentFunc"])
    rt.M.call(click, "Walka", "LeftButton")
    assert rt.walka.session.paused
    assert Flags.PAUSED in frame(rt).flags
    rt.M.call(click, "Walka", "LeftButton")
    assert not rt.walka.session.paused
    rt.M.call(click, "Walka", "RightButton")
    assert rt.walka.session is None


def test_it_asks_for_the_time_played_only_while_it_is_not_known(make_rt):
    rt = make_rt(auto_session=True)
    rt.tick(2)
    assert len(lua_list(rt.log.played_requests)) == 1, "not known yet: asked once"
    fresh = rt.reload(gap=600)
    fresh.tick(2)
    assert lua_list(fresh.log.played_requests) == [], "known, and counted on since: not asked again"
