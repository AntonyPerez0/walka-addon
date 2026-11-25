"""Reconnecting by itself after the treadmill is switched off and on.

Once the link is lost with no walk to listen to, Walka only *listens* for
the treadmill it is tied to (advertisements; no connection attempt, which
could wake a pad in standby). Heard again after a silence of RETURN_GAP_S or
more, the pad was switched off and on, and Walka connects. A pad that keeps
advertising - standby - is never connected to by this.
"""

from __future__ import annotations

import asyncio

from test_app_ftms import PadRig
from walka.app import RETURN_GAP_S, ReturnDetector


def test_the_gap_rule():
    detector = ReturnDetector(100.0, gap_s=20.0)
    assert not detector.seen(105.0)  # standby: advertising on and on
    assert not detector.seen(110.0)
    assert detector.seen(131.0)  # 21 s of silence, then a word: switched off and on
    assert not detector.seen(132.0)


async def lose_the_pad(rig: PadRig) -> None:
    """Connected (and so tied to the pad), then the link goes with no walk around."""
    rig.app.connect()
    await rig.treadmill.connect()
    await rig.run(1, active=False, signal=False)
    assert rig.app.config.treadmill.address == rig.pad.address
    rig.app._eager_connect = False  # what a connection does (see App._on_treadmill_event)
    rig.pad.drop_link()
    await rig.run(1, active=False, signal=False)
    assert not rig.treadmill.status.connected and not rig.app.connection_wanted()


async def listen(rig: PadRig, rounds: list[tuple[float, bool]]) -> bool:
    """Run watch_once while the clock moves: each round is (seconds later, pad advertising?)."""
    task = asyncio.create_task(rig.app.watch_once())
    await asyncio.sleep(0)
    for seconds, advertising in rounds:
        rig.clock.advance(seconds)
        rig.pad.powered = advertising
        if advertising:
            rig.backend.advertise()
        await asyncio.sleep(0.01)
        if task.done():
            break
    # Let the listener answer (it is woken by the advertisement, not by a clock), then end the listening.
    done, _ = await asyncio.wait({task}, timeout=2.0)
    if not done:
        rig.app.want_connected = False
    return await asyncio.wait_for(task, 5)


def test_a_pad_switched_off_and_on_is_connected_again(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await lose_the_pad(rig)
        assert rig.app.should_watch()
        finds = rig.backend.finds
        came_back = await listen(rig, [(5, False), (10, False), (RETURN_GAP_S, True)])
        assert came_back and rig.app.connection_wanted()
        assert rig.backend.finds == finds, "listening never looked for the pad to connect"
        await rig.treadmill.connect()  # (what the connection loop then does)

    asyncio.run(scenario())
    assert rig.treadmill.status.connected
    assert any("switched on again" in event["text"] for event in rig.app.events)
    assert rig.heard_nothing_from_walka()


def test_a_pad_in_standby_is_left_alone(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await lose_the_pad(rig)
        connects = rig.pad.connects
        came_back = await listen(rig, [(2, True)] * 60)  # two minutes of advertising, never silent
        assert not came_back and not rig.app.connection_wanted()
        assert rig.pad.connects == connects

    asyncio.run(scenario())


def test_nothing_is_listened_for_without_a_treadmill_walka_is_tied_to(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await lose_the_pad(rig)
        rig.app.forget_treadmill()
        assert not rig.app.should_watch()
        rig.app.config.treadmill.address = rig.pad.address
        rig.app.want_connected = False  # "Stop searching" / Disconnect
        assert not rig.app.should_watch()

    asyncio.run(scenario())


def test_listening_that_fails_raises_for_the_loop_to_retry(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def broken(address, on_seen):
        raise OSError("Bluetooth is switched off")

    async def scenario():
        await lose_the_pad(rig)
        rig.backend.watch = broken
        try:
            await rig.app.watch_once()
        except OSError as exc:
            return str(exc)
        return ""

    assert asyncio.run(scenario()) == "Bluetooth is switched off"
    assert not rig.app.watching
