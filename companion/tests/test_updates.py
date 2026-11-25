"""The update notice: off by default, and nothing asked until it is switched on."""

from __future__ import annotations

import asyncio

import pytest

from test_app import Rig
from walka import updates
from walka.config import Config


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_it_is_off_by_default():
    assert Config().updates.check is False


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("v0.9.3", "0.9.2", True),
        ("v0.10.0", "0.9.2", True),
        ("v1.0", "0.9.2", True),
        ("v0.9.2", "0.9.2", False),
        ("v0.9.1", "0.9.2", False),
        ("v1.0.0-rc1", "0.9.2", False),  # a trial is never "the newest"
        ("", "0.9.2", False),
        ("nonsense", "0.9.2", False),
    ],
)
def test_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def counting_fetch(answers: list):
    async def fetch():
        answers.append("asked")
        return {"tag": "v99.0.0", "url": "https://github.com/AntonyPerez0/walka-addon/releases/tag/v99.0.0"}

    return fetch


def test_switched_off_nothing_is_ever_asked(rig: Rig):
    asked: list = []
    rig.app.update_fetch = counting_fetch(asked)

    async def scenario():
        for _ in range(50):
            rig.clock.advance(3600)
            await rig.app.tick()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert asked == [] and rig.app.snapshot()["update"] == {"enabled": False}


def test_switched_on_it_asks_at_once_then_once_a_day(rig: Rig):
    asked: list = []
    rig.app.update_fetch = counting_fetch(asked)

    async def scenario():
        rig.app.update_config({"updates": {"check": True}})
        await rig.app.tick()
        await rig.app._update_task
        for _ in range(23):
            rig.clock.advance(3600)
            await rig.app.tick()
            await asyncio.sleep(0)
        assert len(asked) == 1
        rig.clock.advance(3600)
        await rig.app.tick()
        await rig.app._update_task
        assert len(asked) == 2

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert update["enabled"] and update["newer"] and update["latest"] == "v99.0.0"
    assert update["url"].endswith("/releases/tag/v99.0.0")
    rig.app.update_config({"updates": {"check": False}})
    assert rig.app.snapshot()["update"] == {"enabled": False}


def test_no_answer_is_said_and_tried_again_later(rig: Rig):
    async def offline():
        raise OSError("no network")

    rig.app.update_fetch = offline

    async def scenario():
        rig.app.update_config({"updates": {"check": True}})
        await rig.app.tick()
        await rig.app._update_task

    asyncio.run(scenario())
    update = rig.app.snapshot()["update"]
    assert "Could not reach GitHub" in update["error"] and not update["newer"]
    assert rig.app.update.next_at - rig.clock.mono == pytest.approx(updates.RETRY_AFTER_S, abs=1)


def test_the_request_names_nobody():
    """What goes to GitHub: the program and its version - nothing about the person."""
    import inspect

    source = inspect.getsource(updates.fetch_latest)
    assert 'f"Walka/{__version__}"' in source and "DummyCookieJar" in source
    assert updates.LATEST_URL == "https://api.github.com/repos/AntonyPerez0/walka-addon/releases/latest"
