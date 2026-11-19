"""The disclaimer has to be accepted before the companion starts or steers a
belt that someone can stand on.

Until then the governor is given no session to follow, the dashboard's
controls that move the belt are refused, and nothing but a stop is ever
sent to the treadmill. What the treadmill does by itself (a walk with the
remote) is shown and recorded as always, and STOP works.
"""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

from test_app import Rig
from test_web import post, serve
from walka import config as config_module
from walka.app import TERMS_NEEDED, App
from walka.config import TERMS_VERSION, Config
from walka.governor import Phase
from walka.sources import BeaconSource
from walka.treadmill.simulated import SimulatedTreadmill

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "companion" / "src" / "walka" / "static" / "index.html"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch) -> Rig:
    """The usual rig, with a pad that says it is a real one (to the
    companion, a belt is real unless it is the simulated treadmill)."""
    monkeypatch.setattr(SimulatedTreadmill, "kind", "ftms")
    rig = Rig(tmp_path)
    rig.sent = []
    for name in ("start", "set_speed"):
        original = getattr(rig.treadmill, name)

        async def spy(*args, _name=name, _original=original):
            rig.sent.append(_name)
            return await _original(*args)

        setattr(rig.treadmill, name, spy)
    return rig


def test_a_real_belt_is_not_started_or_steered_before_the_disclaimer_is_accepted(rig: Rig):
    async def scenario():
        await rig.connect()
        terms = rig.app.snapshot()["terms"]
        assert terms == {"required": True, "accepted": False, "version": TERMS_VERSION}
        # A session in the game, a character that stands, then runs: the
        # deliberate act that starts a belt on any other day.
        await rig.run(2, active=False)
        await rig.run(3, target=3.2, moving=False)
        await rig.run(60, target=3.2)
        assert rig.sent == []
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.IDLE and not rig.app.governor.driving
        assert rig.app.recorder is None, "no walk is recorded for a session that moves nothing"

    run(scenario())


def test_the_dashboards_own_ways_to_move_the_belt_are_refused_too(rig: Rig):
    async def scenario():
        await rig.connect()
        assert rig.app.manual_start(3.0) is False
        assert not rig.app.manual.active
        assert rig.app.manual_resume() is False
        assert rig.app.resume() is False
        await rig.run(30, signal=False)
        assert rig.sent == [] and rig.treadmill.status.speed_kmh == 0
        assert rig.app.events[0]["text"] == TERMS_NEEDED

    run(scenario())


def test_a_walk_with_the_remote_is_left_alone_and_recorded_and_stop_works(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(60, target=4.0)  # the game asks for more: nobody is listening
        assert rig.sent == []
        assert rig.treadmill.status.speed_kmh > 0
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        await rig.app.emergency_stop()
        await rig.run(10, active=False)
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_accepting_lets_the_belt_follow_but_a_session_already_under_way_waits_for_a_deliberate_act(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(10, target=3.2)  # running along, in a session, before the disclaimer is accepted
        rig.app.accept_terms()
        assert rig.app.snapshot()["terms"]["accepted"] is True
        # Accepting is not pressing Start: the character is already moving,
        # and nobody watched this session begin.
        await rig.run(20, target=3.2)
        assert rig.sent == [] and rig.treadmill.status.speed_kmh == 0
        # It stops, and goes again.
        await rig.run(2, target=3.2, moving=False)
        await rig.run(40, target=3.2)
        assert rig.sent[0] == "start"
        assert rig.treadmill.status.speed_kmh > 1.0
        assert rig.app.governor.phase is Phase.FOLLOWING

    run(scenario())


def test_the_acceptance_is_kept_with_its_date_and_asked_again_for_a_newer_disclaimer(rig: Rig):
    rig.app.accept_terms()
    saved = config_module.load(rig.app.data_dir)
    assert saved.terms.accepted_version == TERMS_VERSION
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", saved.terms.accepted_at)

    again = App(saved, rig.app.data_dir, treadmill=SimulatedTreadmill(auto_run=False), beacon=BeaconSource(None))
    assert again.terms_accepted()

    # What was accepted was an older text.
    saved.terms.accepted_version = TERMS_VERSION - 1
    older = App(saved, rig.app.data_dir, treadmill=SimulatedTreadmill(auto_run=False), beacon=BeaconSource(None))
    assert not older.terms_accepted()


def test_it_cannot_be_accepted_as_a_setting(rig: Rig):
    rig.app.update_config({"terms": {"accepted_version": 99, "accepted_at": "now"}, "units": "kmh"})
    assert rig.app.config.units == "kmh"
    assert rig.app.config.terms.accepted_version == 0 and not rig.app.terms_accepted()


def test_the_simulated_treadmill_asks_for_nothing(tmp_path: Path):
    """Nobody stands on it - and the demo, and the build's own check that
    the belt moves, run on it."""
    app = App(Config(), tmp_path, treadmill=SimulatedTreadmill(auto_run=False), beacon=None)
    assert app.terms_accepted()
    assert app.snapshot()["terms"] == {"required": False, "accepted": True, "version": TERMS_VERSION}


def test_over_http(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        for body in ({"action": "start", "speed_kmh": 3.0}, {"action": "resume"}):
            response = await post(client, "/api/manual", body)
            assert response.status == 403 and (await response.json()) == {"ok": False, "error": TERMS_NEEDED}
        assert (await post(client, "/api/resume")).status == 403
        # The settings door is shut as well.
        assert (await post(client, "/api/config", {"terms": {"accepted_version": TERMS_VERSION}})).status == 200
        assert not rig.app.terms_accepted()

        # The page must say which text was on the screen.
        for wrong in ({}, {"version": TERMS_VERSION + 1}, {"version": str(TERMS_VERSION)}, {"version": True}):
            response = await post(client, "/api/terms/accept", wrong)
            assert response.status == 409, wrong
            assert not rig.app.terms_accepted()

        response = await post(client, "/api/terms/accept", {"version": TERMS_VERSION})
        assert response.status == 200 and (await response.json()) == {"ok": True}
        state = await (await client.get("/api/state")).json()
        assert state["terms"]["accepted"] is True

        assert (await post(client, "/api/manual", {"action": "start", "speed_kmh": 3.0})).status == 200
        await rig.run(40, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.5

    serve(rig, scenario)


# ---- what is on the page is what the repository says ----------------------------------


def _words(text: str) -> str:
    return " ".join(text.split())


def test_the_card_on_the_dashboard_is_the_current_disclaimer():
    page = PAGE.read_text("utf-8")
    card = re.search(r'<section class="card terms" id="terms"[^>]*>(.*?)</section>', page, re.S)
    assert card is not None
    assert f'data-version="{TERMS_VERSION}"' in card.group(0)
    shown = _words(html.unescape(re.sub(r"<[^>]+>", " ", card.group(1))))

    disclaimer = (REPO / "DISCLAIMER.md").read_text("utf-8")
    for heading in ("No warranty", "Limit of liability"):
        section = re.search(rf"^## {heading}\n\n(.*?)\n\n", disclaimer, re.S | re.M)
        assert section is not None, heading
        paragraph = _words(section.group(1))
        assert paragraph.isupper(), "the operative paragraph is the one in capitals"
        assert paragraph in shown, f"the card's '{heading}' is not DISCLAIMER.md's: change both, and TERMS_VERSION"
    assert 'id="terms-check"' in card.group(1) and 'id="terms-accept"' in card.group(1)
