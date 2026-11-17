"""Regression tests for the belt-safety review, end to end: the app, the
governor and a treadmill (simulated, or the real driver over an emulated
pad). What matters is what the *belt* ends up doing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from test_app import MPH, Rig, commands_sent, drop_link, restore_link, run, session_running
from test_app_ftms import PadRig
from test_app_ftms import run as run_pad
from test_web import post, serve
from walka.app import CHECKPOINT_EVERY_S, TICK_S
from walka.governor import HALT_GIVE_UP_S, Action, Command, Phase
from walka.session import SessionStore
from walka.treadmill import BeltState
from walka_fakes import FakePad


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def events(rig) -> str:
    return " | ".join(e["text"] for e in rig.app.events)


# ---- [0] a speed chosen at the pad ------------------------------------------------------------


def hide_the_pads_target(rig: Rig) -> None:
    """A pad that does not say when its speed is changed with the remote:
    the governor then has only the reported speed to go on."""
    belt = rig.app._belt
    rig.app._belt = lambda mono: __import__("dataclasses").replace(belt(mono), target_kmh=None)


@pytest.mark.parametrize("pad_tells", [True, False])
def test_belt_slowed_with_the_remote_is_held_there_until_resume(rig: Rig, pad_tells: bool):
    if not pad_tells:
        hide_the_pads_target(rig)

    async def scenario():
        await session_running(rig, 2.8 * MPH, seconds=60)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.8 * MPH, abs=0.01)
        rig.treadmill.press_speed(1.0 * MPH)  # the walker: too fast for me
        sent = commands_sent(rig)
        peak = 0.0
        for target in (2.8, 2.7, 3.0, 2.5, 3.0):  # the game goes on asking for more, in notches
            for _ in range(40):
                await rig.run(TICK_S, target=target * MPH)
                peak = max(peak, rig.treadmill.status.target_kmh or 0.0)
        assert peak <= 1.0 * MPH + 1e-6, "the belt was sped up again after the walker slowed it"
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.0 * MPH, abs=0.01)
        assert all(float(c.split(":")[1]) <= 1.0 * MPH + 1e-6 for c in sent if c.startswith("set_speed"))
        snapshot = rig.app.snapshot()
        assert snapshot["governor"]["ceiling_kmh"] == pytest.approx(1.0 * MPH, abs=0.01)
        assert snapshot["governor"]["phase"] == "following"
        json.dumps(snapshot)
        assert "lowered at the treadmill" in events(rig) and "1.0 mph" in events(rig)
        # Slower than the ceiling is still followed.
        await rig.run(10, target=0.6 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(0.6 * MPH, abs=0.01)
        # Resume on the dashboard lifts it.
        rig.app.resume()
        await rig.run(40, target=2.8 * MPH)
        assert rig.app.snapshot()["governor"]["ceiling_kmh"] is None
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.8 * MPH, abs=0.01)

    run(scenario())


def test_belt_slowed_with_the_remote_then_signal_lost_never_speeds_up(rig: Rig):
    async def scenario():
        await session_running(rig, 2.8 * MPH, seconds=60)
        rig.treadmill.press_speed(0.6 * MPH)
        await rig.run(0.6, target=2.8 * MPH)
        low = rig.treadmill.status.speed_kmh
        peak = 0.0
        for _ in range(int(30 / TICK_S)):
            await rig.run(TICK_S, signal=False)
            peak = max(peak, rig.treadmill.status.speed_kmh)
        assert peak <= low + 1e-6
        assert rig.treadmill.status.speed_kmh == pytest.approx(0.6 * MPH, abs=0.01)

    run(scenario())


def test_manual_session_respects_a_speed_lowered_at_the_pad_until_it_is_started_again(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.app.manual_start(2.5 * MPH)
        await rig.run(40, signal=False)
        rig.treadmill.press_speed(1.0 * MPH)
        await rig.run(20, signal=False)
        rig.app.manual_set_speed(3.0 * MPH)
        await rig.run(20, signal=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.0 * MPH, abs=0.01)
        rig.app.manual_stop()
        await rig.run(15, signal=False)
        rig.app.manual_start(2.0 * MPH)  # a new session is a new start
        await rig.run(40, signal=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)

    run(scenario())


# ---- [1] a stop that is owed --------------------------------------------------------------------


@pytest.mark.parametrize("what", ["stop_button", "hud_pause", "session_end"])
def test_stop_that_failed_before_the_link_dropped_reaches_the_belt_after_the_reconnect(rig: Rig, what: str):
    frames = {
        "stop_button": dict(target=3.2),
        "hud_pause": dict(target=3.2, paused=True),
        "session_end": dict(active=False),
    }[what]

    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True  # the link is dying: writes fail
        if what == "stop_button":
            await rig.app.emergency_stop()
        await rig.run(1, **frames)
        assert rig.treadmill.status.speed_kmh > 2.5
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(5, **frames)
        # Reason enough to reach for the treadmill again - also with the session over.
        assert rig.app.connection_wanted(), "nothing would reconnect to stop the belt"
        rig.treadmill.fail_commands = False
        restore_link(rig)
        rig.treadmill.status.speed_kmh = speed  # it ran on
        await rig.run(8, **frames)
        assert rig.treadmill.status.state is BeltState.STOPPED, "the belt ran on after the reconnect"
        assert rig.treadmill.status.speed_kmh == 0
        await rig.run(5, **frames)
        assert rig.app.connection_wanted() == (what != "session_end")  # (a live session wants the pad anyway)

    run(scenario())


def test_reconnecting_for_an_owed_stop_is_given_up_after_half_an_hour(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        await rig.run(1, active=False)
        drop_link(rig, belt_keeps_running=True)
        await rig.run(5, active=False)
        assert rig.app.connection_wanted()
        rig.clock.advance(HALT_GIVE_UP_S)
        await rig.run(1, active=False)
        assert not rig.app.connection_wanted()
        assert any("stop it at the treadmill itself" in e["text"] and e["level"] == "error" for e in rig.app.events)

    run(scenario())


def test_stop_pressed_with_nothing_running_does_not_keep_reaching_for_the_pad(rig: Rig):
    """STOP with no belt of ours anywhere must not wake a switched-off pad
    for the next half hour."""

    async def scenario():
        await rig.connect()
        await rig.run(2, active=False)
        await rig.treadmill.disconnect()
        await rig.app.emergency_stop()
        await rig.run(2, active=False)
        assert not rig.app.connection_wanted()

    run(scenario())


# ---- [2] ending a dashboard session ------------------------------------------------------------


@pytest.mark.parametrize("gap_ticks", [0, 1, 5])
def test_end_session_with_the_game_covered_and_back_a_moment_later(rig: Rig, gap_ticks: int):
    """The dashboard window covers the game when End session is pressed; a
    tick later the game is visible again. The "session over" that the app
    says itself used to count as having watched the game's session begin -
    and the belt restarted by itself ten seconds later."""

    async def scenario():
        await rig.connect()
        await rig.run(2, active=False)
        await rig.run(5, target=1.3, moving=False, session=7)
        rig.app.manual_start(2.0)
        await rig.run(20, signal=False)
        rig.app.manual_stop()
        await rig.run(TICK_S * (1 + gap_ticks), signal=False)
        sent = commands_sent(rig)
        await rig.run(40, target=1.3, moving=False, session=7)
        assert sent == [] and rig.app.governor.phase is Phase.WAITING
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_end_session_while_the_game_character_runs_does_not_count_as_setting_off(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0 * MPH)
        rig.app.manual_start(1.5 * MPH)
        await rig.run(20, target=3.0 * MPH, moving=True)
        sent = commands_sent(rig)
        rig.app.manual_stop()
        await rig.run(60, target=3.0 * MPH, moving=True)  # well past the pad's restart hold-off
        assert sent == ["stop"] and rig.app.governor.phase is Phase.WAITING
        rig.app.resume()  # Resume is the other way back
        await rig.run(40, target=3.0 * MPH, moving=True)
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.0 * MPH, abs=0.01)

    run(scenario())


# ---- [6] [11] the game out of view ---------------------------------------------------------------


def test_belt_started_with_the_remote_while_the_game_is_minimised_is_left_alone(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0)
        await rig.run(300, signal=False)  # minimised: held, slowed, paused
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.WAITING
        sent = commands_sent(rig)
        for _ in range(3):
            rig.treadmill.press_start()  # fine, I will just walk with the remote
            await rig.run(60, signal=False)
            assert sent == [], "the belt was paused again under the walker"
            assert rig.treadmill.status.state is BeltState.RUNNING
            rig.treadmill.press_stop()
            await rig.run(5, signal=False)
        rig.treadmill.press_start()
        await rig.run(30, signal=False)
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        assert not rig.app.governor.driving and not rig.app.connection_wanted()
        assert "started at the treadmill" in events(rig)

    run(scenario())


def test_resume_pressed_with_the_game_out_of_view_is_not_good_an_hour_later(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0)
        await rig.run(5, target=1.3, moving=False)
        await rig.run(200, signal=False)
        assert rig.app.governor.phase is Phase.WAITING
        rig.app.resume()
        await rig.run(600, signal=False)
        assert "no longer counts" in events(rig)
        rig.clock.advance(3000)
        sent = commands_sent(rig)
        await rig.run(30, target=1.3, moving=False)  # someone uncovers the game; nobody touches a key
        assert sent == [] and rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.WAITING

    run(scenario())


def test_resume_pressed_just_before_going_back_to_the_game_works(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0)
        await rig.run(5, target=1.3, moving=False)
        await rig.run(200, signal=False)
        rig.app.resume()
        await rig.run(20, signal=False)
        await rig.run(30, target=1.3, moving=False)
        assert rig.treadmill.status.speed_kmh > 0.9

    run(scenario())


# ---- [8] closing the companion ---------------------------------------------------------------------


@pytest.mark.parametrize("what", ["hud_pause", "stop_button", "session_end"])
def test_app_stop_stops_a_belt_whose_stop_did_not_get_through(rig: Rig, what: str):
    """The user closes the companion *because* STOP did not work."""

    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        if what == "stop_button":
            await rig.app.emergency_stop()
        frames = {"hud_pause": dict(target=3.2, paused=True), "session_end": dict(active=False)}.get(what, dict(target=3.2))
        await rig.run(2, **frames)
        assert rig.treadmill.status.speed_kmh > 2.5
        rig.treadmill.fail_commands = False
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == ["stop"]
        assert rig.treadmill.status.state is BeltState.STOPPED

    run(scenario())


def test_app_stop_sends_the_stop_before_anything_else(rig: Rig):
    """Windows gives a console program a few seconds when its window is
    closed: the belt comes first, tidying up second."""
    order: list[str] = []

    class Hotkey:
        def stop(self) -> None:
            order.append("hotkey thread joined")

        def to_dict(self) -> dict:
            return {}

    async def background() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("task cancelled")
            raise

    async def scenario():
        await session_running(rig, 3.2)
        stop = rig.treadmill.stop

        async def recorded_stop() -> None:
            order.append("belt stopped")
            await stop()

        rig.treadmill.stop = recorded_stop
        rig.app.hotkey = Hotkey()
        rig.app._tasks.append(asyncio.create_task(background()))
        await asyncio.sleep(0)
        await rig.app.stop()

    run(scenario())
    assert order[0] == "belt stopped", order
    assert set(order[1:]) == {"task cancelled", "hotkey thread joined"}


def test_nothing_that_moves_the_belt_is_sent_while_closing(rig: Rig):
    async def scenario():
        await rig.connect()
        sent = commands_sent(rig)
        rig.app._stopping.set()
        await rig.app._execute(Command(Action.START))
        await rig.app._execute(Command(Action.SET_SPEED, speed_kmh=3.0))
        assert sent == []
        await rig.app._execute(Command(Action.STOP))
        assert sent == ["stop"]

    run(scenario())


# ---- [9] Disconnect on the dashboard ----------------------------------------------------------------


def test_disconnect_during_the_countdown_stops_the_belt_first(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(1, target=3.2)  # START sent; the pad counts down, the belt reports 0
        assert rig.treadmill.status.state is BeltState.STARTING and rig.treadmill.status.speed_kmh == 0
        sent = commands_sent(rig)
        assert await rig.app.disconnect() is True
        assert sent == ["stop"] and rig.treadmill.status.state is BeltState.STOPPED
        assert not rig.treadmill.status.connected and not rig.app.want_connected
        assert rig.app.governor.user_stopped  # and it stays stopped when the pad is connected again
        assert "belt stopped first" in events(rig)

    run(scenario())


@pytest.mark.parametrize("who", ["session", "remote"])
def test_disconnect_under_a_moving_belt_stops_it_first(rig: Rig, who: str):
    async def scenario():
        if who == "session":
            await session_running(rig, 3.2)
        else:
            await rig.connect()
            rig.treadmill.press_start()
            await rig.run(20, active=False)
        assert rig.treadmill.status.speed_kmh > 0.9
        sent = commands_sent(rig)
        assert await rig.app.disconnect() is True
        assert sent == ["stop"] and rig.treadmill.status.state is BeltState.STOPPED

    run(scenario())


def test_disconnect_is_refused_when_the_belt_cannot_be_stopped(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        assert await rig.app.disconnect() is False
        assert rig.treadmill.status.connected and rig.app.want_connected
        assert "Not disconnecting" in events(rig)
        rig.treadmill.fail_commands = False
        await rig.run(10, target=3.2)  # the latched stop is repeated by the governor
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_disconnect_with_the_belt_at_rest_sends_nothing(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        await rig.run(8, active=False)
        sent = commands_sent(rig)
        assert await rig.app.disconnect() is True
        assert sent == [] and not rig.app.governor.user_stopped

    run(scenario())


def test_disconnect_and_simulator_buttons_over_http(rig: Rig):
    async def scenario(client):
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        response = await post(client, "/api/treadmill/disconnect")
        assert response.status == 409 and "could not be stopped" in (await response.json())["error"]
        assert rig.treadmill.status.connected
        rig.treadmill.fail_commands = False
        target = rig.treadmill.status.target_kmh
        assert (await post(client, "/api/treadmill/press", {"button": "slower"})).status == 200
        assert rig.treadmill.status.target_kmh == pytest.approx(target - 0.2 * MPH)
        assert (await post(client, "/api/treadmill/press", {"button": "faster"})).status == 200
        assert rig.treadmill.status.target_kmh == pytest.approx(target)
        response = await post(client, "/api/treadmill/disconnect")
        assert response.status == 200 and (await response.json()) == {"ok": True}
        assert rig.treadmill.status.state is BeltState.STOPPED and not rig.treadmill.status.connected

    serve(rig, scenario)


# ---- [10] one treadmill, and only that one --------------------------------------------------------


def test_first_connection_ties_walka_to_that_treadmill(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)
    other = FakePad(address="AA:AA:AA:00:00:02")

    async def scenario():
        assert rig.config.treadmill.address == "" and rig.treadmill.address is None
        await rig.session(3.2)
        assert rig.config.treadmill.address == rig.pad.address == rig.treadmill.address
        saved = json.loads((rig.app.data_dir / "config.json").read_text("utf-8"))
        assert saved["treadmill"]["address"] == rig.pad.address
        assert rig.app.snapshot()["treadmill"]["pinned"] is True
        # A second pad comes into range, and is the one a scan now hears first.
        rig.backend.pads.insert(0, other)
        rig.pad.drop_link()
        await rig.run(5, target=3.2)
        await rig.treadmill.connect()  # what the connection loop does while a session wants the belt
        await rig.run(20, target=4.0)
        assert rig.treadmill.status.address == rig.pad.address
        assert other.writes == [] and other.connects == 0, "Walka drove somebody else's treadmill"
        assert rig.pad.target_tenths == 25

    run_pad(scenario())


def test_forget_this_treadmill(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)
    other = FakePad(address="AA:AA:AA:00:00:02")

    async def scenario():
        await rig.session(3.2)
        # Not while the belt is in use - with or without a link to it.
        assert rig.app.forget_treadmill() is False
        rig.pad.drop_link()
        await rig.run(2, target=3.2)
        assert rig.app.forget_treadmill() is False
        rig.app.connect(other.address)  # ... nor may another pad be picked now
        assert rig.config.treadmill.address == rig.pad.address == rig.treadmill.address
        rig.app.update_config({"treadmill": {"address": other.address, "units": "mph"}})
        assert rig.config.treadmill.address == rig.pad.address == rig.treadmill.address
        assert "Stop the belt" in events(rig)
        await rig.treadmill.connect()
        await rig.run(3, active=False)
        await rig.run(8, active=False)
        assert rig.pad.speed_kmh == 0
        # At rest it can be forgotten; the next pad found is taken, and remembered in turn.
        assert rig.app.forget_treadmill() is True
        assert rig.config.treadmill.address == "" and rig.treadmill.address is None
        assert rig.app.snapshot()["treadmill"]["pinned"] is False
        await rig.app.disconnect()
        rig.backend.pads.insert(0, other)
        await rig.treadmill.connect()
        assert rig.config.treadmill.address == other.address == rig.treadmill.address

    run_pad(scenario())


def test_forget_over_http(rig: Rig):
    async def scenario(client):
        await session_running(rig, 3.2)
        response = await post(client, "/api/treadmill/forget")
        assert response.status == 409 and "Stop the belt" in (await response.json())["error"]
        await rig.run(3, active=False)
        await rig.run(8, active=False)
        response = await post(client, "/api/treadmill/forget")
        assert response.status == 200 and (await response.json()) == {"ok": True}

    serve(rig, scenario)


def test_simulated_treadmill_is_not_pinned(rig: Rig):
    """Its made-up address must not end up in a config that is later used
    with a real pad."""

    async def scenario():
        await rig.connect()

    run(scenario())
    assert rig.config.treadmill.address == ""
    assert rig.app.snapshot()["treadmill"]["pinned"] is False


# ---- [13] the game is closed while the treadmill is out of reach ---------------------------------


def test_game_closed_during_a_link_loss_stops_the_belt_on_reconnect(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0)
        rig.beacon._finder_works = True  # the window finder has seen the game
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(15, signal=False)  # Alt+F4: no window, no frames
        assert rig.app.snapshot()["source"]["beacon"]["state"] == "game_closed"
        assert not rig.app.governor.driving
        restore_link(rig)
        rig.treadmill.status.speed_kmh = speed
        sent = commands_sent(rig)
        await rig.run(10, signal=False)
        assert sent == ["stop"], "the belt went through hold and safe speed instead of stopping"
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.IDLE

    run(scenario())


# ---- [14] a walk that cannot be written -----------------------------------------------------------


def test_walk_that_cannot_be_written_is_kept_and_saved_later(rig: Rig):
    write = SessionStore._write
    full = {"disk": True}

    def write_or_fail(path, data):
        if full["disk"] and path.parent.name == "sessions":
            raise OSError("disk full")
        write(path, data)

    rig.app.store._write = write_or_fail

    async def scenario():
        await session_running(rig, 3.0, seconds=120)
        await rig.run(3, active=False)  # the walk ends: must not raise out of tick()
        assert rig.app.recorder is None and rig.app.store.records == []
        assert "could not be saved (disk full)" in events(rig)
        # Still there for the next start of the companion ...
        checkpoint = json.loads((rig.app.data_dir / "active-session.json").read_text("utf-8"))
        assert 100 <= checkpoint["seconds"] <= 125
        assert len(SessionStore(rig.app.data_dir / "..").records) == 0  # (another folder: nothing touched)
        # ... and while a new walk is being recorded it is not overwritten.
        await rig.run(15, active=False)
        await rig.run(CHECKPOINT_EVERY_S + 5, target=3.0, session=2)
        assert json.loads((rig.app.data_dir / "active-session.json").read_text("utf-8"))["seconds"] == checkpoint["seconds"]
        full["disk"] = False
        await rig.run(CHECKPOINT_EVERY_S + 5, target=3.0, session=2)
        assert [round(r.seconds) for r in rig.app.store.records] == [round(checkpoint["seconds"])]
        assert "Walk saved" in events(rig)
        await rig.run(3, active=False)

    run(scenario())
    assert len(rig.app.store.records) == 2
    assert not (rig.app.data_dir / "active-session.json").exists()
    assert len(SessionStore(rig.app.data_dir).records) == 2  # on disk, once each


def test_walk_that_cannot_be_written_is_recovered_by_the_next_start(rig: Rig):
    def fail(path, data):
        if path.parent.name == "sessions":
            raise OSError("folder locked")
        SessionStore._write(path, data)

    rig.app.store._write = fail

    async def scenario():
        await session_running(rig, 3.0, seconds=120)
        await rig.app.stop()  # must not raise, and must finish shutting down

    run(scenario())
    assert not rig.treadmill.status.connected and (rig.app.data_dir / "config.json").exists()
    assert rig.treadmill.status.state is BeltState.STOPPED
    recovered = SessionStore(rig.app.data_dir).records
    assert len(recovered) == 1 and recovered[0].seconds > 100
