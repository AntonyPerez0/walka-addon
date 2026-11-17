"""End-to-end: beacon frames -> governor -> simulated treadmill -> recorded
walk -> data file for the addon. Time is fully simulated."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from walka import addon_sync
from walka.app import TICK_S, App
from walka.config import MAX_SPEED_CAP_KMH, Config
from walka.governor import Phase
from walka.protocol import Flags, Frame, kmh_to_raw
from walka.sources import BeaconSource
from walka.treadmill.simulated import SimulatedTreadmill


class Clock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_800_000_000.0

    def advance(self, dt: float) -> None:
        self.mono += dt
        self.wall += dt


class Rig:
    def __init__(self, tmp_path: Path, *, us_firmware: bool = True) -> None:
        self.clock = Clock()
        self.config = Config()
        self.treadmill = SimulatedTreadmill(us_firmware=us_firmware, clock=lambda: self.clock.mono, auto_run=False)
        self.beacon = BeaconSource(None, clock=lambda: self.clock.mono)  # frames are injected
        self.addon = tmp_path / "AddOns" / "Walka"
        self.addon.mkdir(parents=True)
        (self.addon / "Walka.toc").write_text("## Interface: 16001\n")
        self.config.wow.addon_dir = str(self.addon)
        self.app = App(
            self.config,
            tmp_path / "data",
            treadmill=self.treadmill,
            beacon=self.beacon,
            wall_clock=lambda: self.clock.wall,
            mono_clock=lambda: self.clock.mono,
        )
        self.seq = 0

    async def connect(self) -> None:
        await self.treadmill.connect()

    async def run(
        self,
        seconds: float,
        *,
        target: float | None = None,
        active: bool = True,
        paused: bool = False,
        moving: bool = True,
        session: int = 1,
        signal: bool = True,
    ) -> None:
        """Advance simulated time; each tick optionally injects a fresh frame."""
        for _ in range(int(round(seconds / TICK_S))):
            self.clock.advance(TICK_S)
            if signal:
                self.seq = (self.seq + 1) % 256
                flags = Flags(0)
                if active:
                    flags |= Flags.ACTIVE
                if paused:
                    flags |= Flags.PAUSED
                if moving:
                    flags |= Flags.MOVING
                self.beacon.inject(
                    Frame(
                        seq=self.seq,
                        session=session if active else 0,
                        flags=flags,
                        target_raw=kmh_to_raw(target or 0.0),
                        char_speed_raw=350 if moving else 0,
                    )
                )
            await self.app.tick()
            self.treadmill.advance(TICK_S)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def run(coro):
    return asyncio.run(coro)


def test_full_walk_is_recorded_and_synced(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(2, active=False)
        assert rig.app.governor.phase is Phase.IDLE
        assert rig.app.recorder is None
        await rig.run(600, target=3.2)  # ten minutes at ~2 mph
        assert rig.app.governor.phase is Phase.FOLLOWING
        # 2.0 mph on the pad's 0.1 mph grid
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * 1.609344, abs=0.01)
        live = rig.app.snapshot()["session"]
        assert live["source"] == "wow" and live["seconds"] > 500
        await rig.run(3, active=False)
        assert rig.treadmill.status.speed_kmh < rig.treadmill.status.min_kmh

    run(scenario())
    records = rig.app.store.records
    assert len(records) == 1
    record = records[0]
    assert record.source == "wow" and record.beacon_session == 1
    assert 560 <= record.seconds <= 600
    # ~3.2 km/h for ~10 minutes
    assert 480 <= record.distance_m <= 560
    assert 800 <= record.steps <= 1000
    assert 25 <= record.kcal <= 60
    assert record.steps_source == "treadmill"
    # The addon data file was written and is valid Lua.
    data_file = rig.addon / "CompanionData.lua"
    text = data_file.read_text("utf-8")
    assert text.count("WalkaCompanionData = {") == 1
    from lupa.lua51 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(text)
    data = lua.globals().WalkaCompanionData
    assert data.version == addon_sync.DATA_VERSION
    assert data.sessions[1].steps == record.steps
    assert data.sessions[1].beaconSession == 1
    assert data.profile.weightKg == 75
    assert data.limits.minKmh == pytest.approx(0.6 * 1.609344, abs=1e-3)
    assert data.limits.maxKmh == pytest.approx(rig.config.limits.max_speed_kmh, abs=1e-3)


def test_signal_loss_slows_then_game_returns(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(60, target=4.0)
        fast = rig.treadmill.status.speed_kmh
        await rig.run(30, signal=False)
        assert rig.app.governor.phase is Phase.SAFE
        assert rig.treadmill.status.speed_kmh < fast
        assert rig.treadmill.status.speed_kmh == pytest.approx(rig.config.limits.safe_speed_kmh, abs=0.09)
        await rig.run(40, target=4.0)
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.treadmill.status.speed_kmh == pytest.approx(fast, abs=0.01)
        # Still one continuous recording.
        assert rig.app.recorder is not None and rig.app.recorder.source == "wow"

    run(scenario())
    assert rig.app.store.records == []


def test_remote_stop_is_respected(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=3.0)
        rig.treadmill.press_stop()
        await rig.run(40, target=3.0)
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert rig.treadmill.status.speed_kmh == 0
        # Pause -> resume from the addon restarts it.
        await rig.run(2, target=3.0, paused=True)
        await rig.run(30, target=3.0)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_manual_session_and_emergency_stop(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(60, signal=False)
        assert rig.app.source_kind() == "manual"
        assert rig.treadmill.status.speed_kmh > 2.5
        await rig.app.emergency_stop()
        await rig.run(20, signal=False)
        assert rig.treadmill.status.speed_kmh == 0
        assert not rig.app.manual.active

    run(scenario())
    assert len(rig.app.store.records) == 1
    assert rig.app.store.records[0].source == "manual"


def test_free_walk_with_remote_is_recorded(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(120, active=False)
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        # The governor never touched a belt it does not own.
        assert rig.app.governor.phase is Phase.IDLE
        rig.treadmill.press_stop()
        await rig.run(90, active=False)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["free"]


def test_hard_cap_from_config(rig: Rig):
    rig.config.limits.max_speed_kmh = 2.5

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(120, target=6.0)
        assert rig.treadmill.status.speed_kmh <= 2.5 + 1e-6

    run(scenario())


def test_new_beacon_session_splits_recordings(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(60, target=3.0, session=4)
        await rig.run(2, active=False)
        await rig.run(60, target=3.0, session=5)
        await rig.run(2, active=False)

    run(scenario())
    assert [r.beacon_session for r in rig.app.store.records] == [4, 5]


def test_snapshot_is_json_serialisable(rig: Rig):
    import json

    async def scenario():
        await rig.connect()
        await rig.run(5, target=3.0)
        json.dumps(rig.app.snapshot())

    run(scenario())


# ======================================================================================
# More scenarios on the same rig.
# ======================================================================================

import json  # noqa: E402
import random  # noqa: E402

from walka.session import SessionStore  # noqa: E402
from walka.treadmill import BeltState, TreadmillEvent  # noqa: E402

MPH = 1.609344


async def session_running(rig: Rig, target: float = 3.2, seconds: float = 40) -> None:
    await rig.connect()
    await rig.run(1, active=False)
    await rig.run(seconds, target=target)
    assert rig.app.governor.phase is Phase.FOLLOWING
    assert rig.treadmill.status.speed_kmh > 1.0


def drop_link(rig: Rig, *, belt_keeps_running: bool) -> float:
    """The Bluetooth link dies. Like the real driver, the status then says
    "not connected, speed unknown (0)". Returns the speed the belt had."""
    status = rig.treadmill.status
    physical = status.speed_kmh
    status.connected = False
    status.speed_kmh = 0.0
    if not belt_keeps_running:
        status.state = BeltState.STOPPED
        status.target_kmh = None
    rig.treadmill._emit(TreadmillEvent.DISCONNECTED)
    return physical


def restore_link(rig: Rig) -> None:
    status = rig.treadmill.status
    status.connected = True
    status.updated_at = rig.clock.mono
    rig.treadmill._emit(TreadmillEvent.CONNECTED)


def commands_sent(rig: Rig) -> list[str]:
    """Wrap the treadmill so that every command the app sends is recorded."""
    sent: list[str] = []
    treadmill = rig.treadmill
    for name in ("start", "pause", "stop", "set_speed"):
        original = getattr(treadmill, name)

        def make(name=name, original=original):
            async def wrapper(*args):
                sent.append(name if not args else f"{name}:{args[0]:.3f}")
                return await original(*args)

            return wrapper

        setattr(treadmill, name, make())
    return sent


def load_companion_data(rig: Rig):
    from lupa.lua51 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute((rig.addon / "CompanionData.lua").read_text("utf-8"))
    return lua.globals().WalkaCompanionData


# ---- treadmill link ---------------------------------------------------------------------


def test_reconnect_with_the_belt_still_running_is_adopted_without_a_start(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        sent = commands_sent(rig)
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(8, target=3.2, moving=False)
        assert rig.app.governor.phase is Phase.NO_TREADMILL and sent == []
        restore_link(rig)
        await rig.run(1, target=3.2, moving=False)  # connected, first data packet not in yet
        assert "start" not in sent
        rig.treadmill.status.speed_kmh = speed  # the pad reports: still running
        await rig.run(30, target=4.0, moving=False)
        assert "start" not in sent  # no surprise countdown on a moving belt
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.5 * MPH, abs=0.01)  # following again

    run(scenario())


def test_reconnect_with_the_belt_stopped_waits_for_a_deliberate_act(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        sent = commands_sent(rig)
        drop_link(rig, belt_keeps_running=False)  # e.g. the pad was switched off and on
        await rig.run(8, target=3.2, moving=True)
        restore_link(rig)
        await rig.run(60, target=3.2, moving=True)  # the character runs on throughout (auto-run)
        assert sent == [], "the belt must not restart by itself after a reconnect"
        assert rig.app.governor.phase is Phase.WAITING
        assert rig.treadmill.status.speed_kmh == 0
        await rig.run(2, target=3.2, moving=False)
        await rig.run(30, target=3.2, moving=True)  # stops, then moves: a key was pressed
        assert sent[0] == "start"
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_reconnect_then_dashboard_resume_restarts(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        drop_link(rig, belt_keeps_running=False)
        await rig.run(5, target=3.2)
        restore_link(rig)
        await rig.run(10, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0
        rig.app.resume()
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_disconnected_time_is_not_recorded(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2, seconds=60)
        before = rig.app.recorder.seconds
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(120, target=3.2)
        assert rig.app.recorder is not None and rig.app.recorder.seconds == pytest.approx(before, abs=0.3)
        restore_link(rig)
        rig.treadmill.status.speed_kmh = speed
        await rig.run(60, target=3.2)
        assert rig.app.recorder.seconds == pytest.approx(before + 60, abs=1.5)
        await rig.run(3, active=False)

    run(scenario())
    assert len(rig.app.store.records) == 1  # one walk, not two
    assert any("disconnected" in e["text"].lower() for e in rig.app.events)


def test_treadmill_data_going_stale_pauses_the_belt(rig: Rig):
    """Notifications stop arriving while the link stays up. Not knowing what
    the belt does must not end with "it reports nothing, so it is stopped"
    while it runs on."""

    async def scenario():
        await session_running(rig, 3.2)
        treadmill = rig.treadmill
        advance = treadmill.advance
        frozen = treadmill.status.updated_at

        def advance_without_reporting(dt: float) -> None:
            advance(dt)
            treadmill.status.updated_at = frozen

        treadmill.advance = advance_without_reporting
        await rig.run(4, target=3.2)
        assert rig.app.governor.phase is Phase.FOLLOWING  # a short gap is tolerated
        await rig.run(12, target=3.2)
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert treadmill.status.state is BeltState.PAUSED, "the belt was left running blind"
        assert treadmill.status.speed_kmh == 0
        seconds = rig.app.recorder.seconds
        # Data comes back: nothing restarts by itself.
        treadmill.advance = advance
        await rig.run(30, target=3.2)
        assert treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.USER_STOPPED
        assert rig.app.recorder.seconds == pytest.approx(seconds, abs=1.0)
        # Pause -> resume is the way back.
        await rig.run(2, target=3.2, paused=True)
        await rig.run(30, target=3.2)
        assert treadmill.status.speed_kmh > 2.5

    run(scenario())


# ---- command failures -------------------------------------------------------------------


def test_failed_start_gives_up_and_says_so(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        rig.treadmill.fail_commands = True
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert any("start failed" in e["text"] for e in rig.app.events)
        rig.treadmill.fail_commands = False
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0  # no retry of a start on its own
        await rig.run(2, target=3.2, paused=True)
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_failed_speed_command_is_sent_again(rig: Rig):
    async def scenario():
        await session_running(rig, 2.0 * MPH)
        rig.treadmill.fail_commands = True
        await rig.run(5, target=3.0 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        rig.treadmill.fail_commands = False
        await rig.run(20, target=3.0 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.0 * MPH, abs=0.01)

    run(scenario())
    assert any("set_speed failed" in e["text"] for e in rig.app.events)


def test_failed_pause_is_repeated_until_the_belt_stops(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        await rig.run(3, target=3.2, paused=True)
        assert rig.treadmill.status.speed_kmh > 2.5  # the pause did not get through
        rig.treadmill.fail_commands = False
        await rig.run(10, target=3.2, paused=True)
        assert rig.treadmill.status.speed_kmh == 0, "the belt ran on under a session shown as paused"
        assert rig.treadmill.status.state is BeltState.STOPPED  # (the repeat is a full stop)
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_failed_stop_at_session_end_is_repeated(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        await rig.run(3, active=False)
        assert rig.treadmill.status.speed_kmh > 2.5
        rig.treadmill.fail_commands = False
        await rig.run(10, active=False)
        assert rig.treadmill.status.speed_kmh == 0, "the belt ran on after the session ended"
        assert rig.treadmill.status.state is BeltState.STOPPED
        await rig.run(90, active=False)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["wow"]


def test_nothing_escapes_tick_while_every_command_fails(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.treadmill.fail_commands = True
        await rig.run(1, active=False)
        await rig.run(20, target=3.0)
        await rig.run(5, target=3.0, paused=True)
        await rig.run(20, target=4.0)
        await rig.run(10, signal=False)
        rig.app.manual_start(2.0)
        await rig.run(10, signal=False)
        await rig.app.emergency_stop()
        await rig.run(10, active=False)
        json.dumps(rig.app.snapshot())

    run(scenario())
    assert rig.treadmill.status.speed_kmh == 0


# ---- leaving ------------------------------------------------------------------------------


def test_app_stop_stops_a_belt_it_drives_and_saves_the_walk(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2, seconds=90)
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == ["stop"]

    run(scenario())
    assert rig.treadmill.status.state is BeltState.STOPPED
    assert not rig.treadmill.status.connected
    assert len(rig.app.store.records) == 1 and rig.app.store.records[0].seconds > 60
    assert not (rig.app.data_dir / "active-session.json").exists()
    assert (rig.app.data_dir / "config.json").exists()
    assert "WalkaCompanionData" in (rig.addon / "CompanionData.lua").read_text("utf-8")


def test_app_stop_never_stops_a_belt_it_does_not_drive(rig: Rig):
    """Someone is walking with the remote, no session: closing the companion
    must not pull the belt from under them."""

    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(60, active=False)
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == []

    run(scenario())
    assert rig.treadmill.status.state is BeltState.RUNNING
    assert [r.source for r in rig.app.store.records] == ["free"]  # the walk is still saved


@pytest.mark.parametrize("situation", ["paused", "stopped_at_the_pad", "session_over"])
def test_app_stop_sends_nothing_when_our_belt_is_already_off(rig: Rig, situation: str):
    async def scenario():
        await session_running(rig, 3.2)
        if situation == "paused":
            await rig.run(5, target=3.2, paused=True)
        elif situation == "stopped_at_the_pad":
            rig.treadmill.press_stop()
            await rig.run(5, target=3.2)
        else:
            await rig.run(5, active=False)
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == []

    run(scenario())


def test_stop_on_exit_can_be_switched_off(rig: Rig):
    rig.config.treadmill.stop_on_exit = False

    async def scenario():
        await session_running(rig, 3.2)
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == []

    run(scenario())
    assert rig.treadmill.status.state is BeltState.RUNNING


def test_app_stop_when_the_stop_command_fails_still_shuts_down(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2, seconds=60)
        rig.treadmill.fail_commands = True
        await rig.app.stop()  # must not raise

    run(scenario())
    assert len(rig.app.store.records) == 1
    assert any("stop failed" in e["text"] for e in rig.app.events)


def test_start_and_stop_with_the_real_loops(tmp_path: Path):
    """The background tasks: the treadmill is connected, a manual session
    moves the belt, and stop() leaves nothing running."""
    config = Config()
    addon = tmp_path / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    config.wow.addon_dir = str(addon)
    treadmill = SimulatedTreadmill(countdown_s=0.1, tick_s=0.02, ramp_kmh_per_s=30.0)
    app = App(config, tmp_path / "data", treadmill=treadmill, beacon=BeaconSource(None))

    async def scenario():
        await app.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if treadmill.status.connected:
                break
        assert treadmill.status.connected
        app.manual_start(3.0)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if treadmill.status.speed_kmh > 0.5:
                break
        assert treadmill.status.speed_kmh > 0.5
        await app.stop()
        assert treadmill.status.state is BeltState.STOPPED and not treadmill.status.connected
        await asyncio.sleep(0.05)
        leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftovers == []

    run(asyncio.wait_for(scenario(), 10))
    assert (addon / "CompanionData.lua").exists()


# ---- crash safety --------------------------------------------------------------------------


def test_checkpoint_and_recovery_after_a_crash(rig: Rig, tmp_path: Path):
    async def scenario():
        await session_running(rig, 3.2, seconds=100)

    run(scenario())
    checkpoint = rig.app.data_dir / "active-session.json"
    assert checkpoint.exists()
    saved = json.loads(checkpoint.read_text("utf-8"))
    assert saved["source"] == "wow" and 60 <= saved["seconds"] <= 100
    assert rig.app.store.records == []  # the walk is still in progress

    # The process dies here. A new companion starts on the same data folder:
    restarted = Rig.__new__(Rig)  # (only its App matters)
    restarted_app = App(
        Config(),
        rig.app.data_dir,
        treadmill=SimulatedTreadmill(auto_run=False),
        beacon=BeaconSource(None),
        wall_clock=lambda: rig.clock.wall + 3600,
        mono_clock=lambda: 5.0,
    )
    records = restarted_app.store.records
    assert len(records) == 1
    assert records[0].source == "wow" and records[0].seconds == saved["seconds"]
    assert records[0].steps == saved["steps"] and records[0].distance_m == saved["distance_m"]
    assert not checkpoint.exists()
    assert restarted_app.totals(rig.clock.wall + 3600)["all"]["sessions"] == 1
    # ... and a third start does not recover it again.
    assert len(SessionStore(rig.app.data_dir).records) == 1
    del restarted


def test_checkpoint_is_cleared_when_the_walk_is_saved_normally(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2, seconds=100)
        assert (rig.app.data_dir / "active-session.json").exists()
        await rig.run(3, active=False)

    run(scenario())
    assert not (rig.app.data_dir / "active-session.json").exists()
    assert len(SessionStore(rig.app.data_dir).records) == 1  # saved once, not twice


# ---- manual sessions ------------------------------------------------------------------------


def test_manual_stop_stops_the_belt_when_no_game_is_running(rig: Rig):
    """Dashboard session with no game at all (the "any other game" case).
    Stop must stop - not be mistaken for a lost signal that holds the speed
    and then crawls on at the safe speed for five minutes."""

    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(60, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.5
        rig.app.manual_stop()
        await rig.run(4, signal=False)
        assert rig.treadmill.status.speed_kmh == 0, "the belt kept running after Stop"
        assert rig.treadmill.status.state is BeltState.STOPPED
        assert rig.app.governor.phase is Phase.IDLE and not rig.app.governor.driving
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["manual"]


def test_manual_stop_without_a_beacon_source_at_all(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.app.beacon = None  # started with --no-capture

    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(40, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.5
        rig.app.manual_stop()
        await rig.run(4, signal=False)
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_manual_stop_with_a_stale_game_signal(rig: Rig):
    """The game is minimised with a session still marked active: its old
    frame must not keep the belt going when the dashboard session ends."""

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(5, target=3.0)  # a game session, then the game is minimised
        rig.app.manual_start(2.5)
        await rig.run(60, signal=False)
        assert rig.app.source_kind() == "manual" and rig.treadmill.status.speed_kmh > 2.0
        rig.app.manual_stop()
        await rig.run(6, signal=False)
        assert rig.treadmill.status.speed_kmh == 0
        # The game comes back, still "active": no restart without an act.
        sent = commands_sent(rig)
        await rig.run(30, target=3.0, moving=False)
        assert sent == [] and rig.app.governor.phase is Phase.WAITING

    run(scenario())


def test_manual_session_overrides_the_game_and_hands_back(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0 * MPH, seconds=40)
        sent = commands_sent(rig)

        # The dashboard takes over while the character keeps running.
        rig.app.manual_start(1.5 * MPH)
        await rig.run(30, target=3.0 * MPH, moving=True)
        assert rig.app.source_kind() == "manual"
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.5 * MPH, abs=0.01)
        assert rig.app.recorder.source == "manual"
        assert "start" not in sent and "stop" not in sent  # the belt just changed speed

        rig.app.manual_set_speed(2.0 * MPH)
        await rig.run(10, target=3.0 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)

        # Manual session ends; the game session is still active and takes the belt back.
        rig.app.manual_stop()
        await rig.run(30, target=3.0 * MPH, moving=True)
        assert rig.app.source_kind() == "wow"
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.0 * MPH, abs=0.01)
        assert rig.app.recorder.source == "wow"
        assert "start" not in sent and "stop" not in sent and "pause" not in sent
        await rig.run(3, active=False)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["wow", "manual", "wow"]


def test_manual_start_works_whatever_the_game_character_is_doing(rig: Rig):
    """The dashboard's Start is a deliberate act in itself: it must not wait
    for the game character to "start moving"."""

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(20, target=3.0, moving=True)
        await rig.run(5, target=3.0, moving=True, paused=True)  # game session paused, character still running
        assert rig.treadmill.status.speed_kmh == 0
        rig.app.manual_start(2.5)
        await rig.run(30, target=3.0, moving=True, paused=True)
        assert rig.treadmill.status.speed_kmh > 2.0
        assert rig.app.governor.phase is Phase.FOLLOWING

    run(scenario())


def test_manual_pause_resume_and_speed(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.app.manual_start(2.0 * MPH)
        await rig.run(30, signal=False)
        rig.app.manual_pause()
        await rig.run(10, signal=False)
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.PAUSED
        assert rig.treadmill.status.state is BeltState.PAUSED
        rig.app.manual_resume()
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        rig.app.manual_set_speed(99.0)  # way beyond the cap
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh <= rig.config.limits.max_speed_kmh + 1e-6
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.0 * MPH, abs=0.01)
        rig.app.manual_set_speed(0.0)
        await rig.run(10, signal=False)
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.STANDING

    run(scenario())


def test_manual_session_started_before_the_treadmill_connected(rig: Rig):
    async def scenario():
        rig.app.manual_start(3.0)
        await rig.run(60, signal=False)
        assert rig.app.governor.phase is Phase.NO_TREADMILL
        sent = commands_sent(rig)
        await rig.connect()  # the pad is switched on a minute later
        await rig.run(30, signal=False)
        assert sent == [] and rig.app.governor.phase is Phase.WAITING
        rig.app.resume()
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_changing_an_unrelated_setting_does_not_change_the_manual_speed(rig: Rig):
    """The dashboard starts a manual session with the speed in the request.
    Toggling mph/km/h (or saving the profile) during that session must not
    snap the belt to the stored default speed."""

    async def scenario():
        await rig.connect()
        rig.app.manual_start(1.0 * MPH)
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.0 * MPH, abs=0.01)
        rig.app.update_config({"units": "kmh"})
        rig.app.update_config({"profile": {"weight_kg": 81}})
        await rig.run(30, signal=False)
        assert rig.app.manual.speed_kmh == pytest.approx(1.0 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.0 * MPH, abs=0.01), "the belt sped up by itself"
        # Changing the manual speed through the settings does apply.
        rig.app.update_config({"manual": {"speed_kmh": 2.0 * MPH}})
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)

    run(scenario())


# ---- the STOP button ------------------------------------------------------------------------


def test_emergency_stop_latches_until_resume(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        await rig.app.emergency_stop()
        sent = commands_sent(rig)
        await rig.run(10, target=3.2, moving=False)
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.USER_STOPPED
        # Nothing the game does brings the belt back.
        await rig.run(20, target=3.2, moving=True)
        await rig.run(20, target=4.5, moving=False)
        await rig.run(20, signal=False)
        await rig.run(20, target=3.2, moving=True)
        assert sent == [] and rig.treadmill.status.speed_kmh == 0
        # Resume on the dashboard does.
        rig.app.resume()
        await rig.run(30, target=3.2)
        assert sent[0] == "start" and rig.treadmill.status.speed_kmh > 2.5

    run(scenario())
    assert any("STOP pressed" in e["text"] for e in rig.app.events)


def test_emergency_stop_survives_the_addons_automatic_afk_pause(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        await rig.app.emergency_stop()
        sent = commands_sent(rig)
        afk = Flags.ACTIVE | Flags.PAUSED | Flags.AFK
        for _ in range(300):  # a minute away from the keyboard
            rig.clock.advance(TICK_S)
            rig.seq = (rig.seq + 1) % 256
            rig.beacon.inject(Frame(seq=rig.seq, session=1, flags=afk, target_raw=0))
            await rig.app.tick()
            rig.treadmill.advance(TICK_S)
        await rig.run(30, target=3.2, moving=False)  # back: the addon resumes by itself
        await rig.run(30, target=3.2, moving=True)
        assert sent == [] and rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_emergency_stop_during_a_manual_session_over_an_active_game_session(rig: Rig):
    """Both a dashboard session and a game session are active. STOP ends the
    dashboard session; the game session showing through must not count as
    the user starting a new session."""

    async def scenario():
        await session_running(rig, 3.0)
        rig.app.manual_start(2.5)
        await rig.run(20, target=3.0)
        await rig.app.emergency_stop()
        assert not rig.app.manual.active
        sent = commands_sent(rig)
        await rig.run(10, target=3.0, moving=False)
        await rig.run(30, target=3.0, moving=True)  # the character sets off
        await rig.run(5, target=3.0, moving=False)
        await rig.run(30, target=3.0, moving=True)
        assert sent == [], f"commands after STOP: {sent}"
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.USER_STOPPED
        rig.app.resume()
        await rig.run(30, target=3.0)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_emergency_stop_of_a_dashboard_session_with_the_game_minimised(rig: Rig):
    """A game session is active but the game is minimised; a dashboard
    session runs the belt; STOP. Bringing the game back and moving the
    character must not restart the belt - the game session was there all
    along, it is not something the user just started."""

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(5, target=3.0)  # game session starts ...
        rig.app.manual_start(2.5)  # ... the dashboard takes over, the game is minimised
        await rig.run(40, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.0
        await rig.app.emergency_stop()
        sent = commands_sent(rig)
        await rig.run(20, signal=False)
        assert rig.treadmill.status.speed_kmh == 0
        # The game window comes back with its session still running.
        await rig.run(10, target=3.0, moving=False)
        await rig.run(30, target=3.0, moving=True)
        await rig.run(5, target=3.0, moving=False)
        await rig.run(30, target=3.0, moving=True)
        assert sent == [], f"commands after STOP: {sent}"
        assert rig.app.governor.phase is Phase.USER_STOPPED
        rig.app.resume()
        await rig.run(30, target=3.0)
        assert sent[0] == "start" and rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_after_emergency_stop_of_a_dashboard_session_the_belt_is_not_ours(rig: Rig):
    """No game at all: after STOP the companion is hands-off again. Someone
    walking with the remote later is left alone (not slowed, not paused)."""

    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(40, signal=False)
        await rig.app.emergency_stop()
        await rig.run(10, signal=False)
        assert rig.treadmill.status.speed_kmh == 0
        assert not rig.app.governor.driving and rig.app.governor.phase is Phase.IDLE
        sent = commands_sent(rig)
        rig.treadmill.press_start()  # the remote
        await rig.run(400, signal=False)  # longer than the hold + safe-speed stages would take
        assert sent == []
        assert rig.treadmill.status.state is BeltState.RUNNING and rig.treadmill.status.speed_kmh > 0.9
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        rig.treadmill.press_stop()
        await rig.run(5, signal=False)
        # A new dashboard session is an explicit start.
        rig.app.manual_start(2.0)
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh > 1.5 and "start" in sent

    run(scenario())
    assert [r.source for r in rig.app.store.records][:2] == ["manual", "free"]


def test_new_game_session_after_emergency_stop_of_a_dashboard_session(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(30, signal=False)
        await rig.app.emergency_stop()
        await rig.run(5, signal=False)
        # The user goes to the game: no session there, then presses Start in the HUD.
        await rig.run(5, active=False)
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_emergency_stop_whose_command_fails_keeps_trying(rig: Rig):
    async def scenario():
        await session_running(rig, 3.2)
        rig.treadmill.fail_commands = True
        await rig.app.emergency_stop()  # must not raise
        assert rig.app.governor.user_stopped
        assert any("use the treadmill's own stop button" in e["text"] for e in rig.app.events)
        await rig.run(3, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5  # still running: the link is refusing commands
        rig.treadmill.fail_commands = False
        await rig.run(10, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0, "STOP was not repeated once commands worked again"
        assert rig.app.governor.phase is Phase.USER_STOPPED

    run(scenario())


@pytest.mark.parametrize("seed", range(5))
def test_after_emergency_stop_nothing_but_an_explicit_act_moves_the_belt(tmp_path: Path, seed: int):
    """Property: STOP, then anything that is not a deliberate resume - the
    game character moving or not, targets, signal losses, the addon's AFK
    pause, link drops, settings changes, failing commands, more stops at the
    pad. Not one start or speed command may follow."""
    rng = random.Random(seed)
    rig = Rig(tmp_path)

    async def afk_pause(seconds: float) -> None:
        for _ in range(int(seconds / TICK_S)):
            rig.clock.advance(TICK_S)
            rig.seq = (rig.seq + 1) % 256
            flags = Flags.ACTIVE | Flags.PAUSED | Flags.AFK
            rig.beacon.inject(Frame(seq=rig.seq, session=1, flags=flags))
            await rig.app.tick()
            rig.treadmill.advance(TICK_S)

    async def scenario():
        await session_running(rig, 3.2)
        await rig.app.emergency_stop()
        sent = commands_sent(rig)
        for _ in range(60):
            roll = rng.random()
            if roll < 0.45:
                await rig.run(
                    rng.uniform(0.2, 6), target=rng.choice([0.0, 1.6, rng.uniform(0, 8)]), moving=rng.random() < 0.6
                )
            elif roll < 0.6:
                await rig.run(rng.uniform(0.2, 12), signal=False)
            elif roll < 0.7:
                await afk_pause(rng.uniform(0.2, 10))
            elif roll < 0.78:
                if rig.treadmill.status.connected:
                    drop_link(rig, belt_keeps_running=False)
                else:
                    restore_link(rig)
            elif roll < 0.86:
                rig.app.update_config({"limits": {"max_speed_kmh": rng.uniform(1.0, 8.0)}, "units": "kmh"})
            elif roll < 0.93:
                rig.treadmill.fail_commands = not rig.treadmill.fail_commands
            else:
                rig.treadmill.press_stop()
        moves = [c for c in sent if c == "start" or c.startswith("set_speed")]
        assert moves == [], f"after STOP: {sent}"
        assert rig.treadmill.status.speed_kmh == 0
        # The explicit way back still works.
        rig.treadmill.fail_commands = False
        if not rig.treadmill.status.connected:
            restore_link(rig)
        rig.app.resume()
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 0.9

    run(scenario())


def test_emergency_stop_while_the_treadmill_is_unreachable(rig: Rig):
    """STOP cannot reach a pad whose link is down. The dashboard must say so
    (not "belt stopped"), and the stop must land as soon as the link is back."""

    async def scenario():
        await session_running(rig, 3.2)
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(2, target=3.2)
        await rig.app.emergency_stop()
        assert any("not connected" in e["text"] and e["level"] == "error" for e in rig.app.events)
        await rig.run(20, target=3.2)
        restore_link(rig)
        await rig.run(1, target=3.2)
        rig.treadmill.status.speed_kmh = speed  # first data after the reconnect: it ran on
        await rig.run(5, target=3.2)
        assert rig.treadmill.status.state is BeltState.STOPPED, "the belt ran on after STOP was pressed"
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.app.governor.phase is Phase.USER_STOPPED
        sent = commands_sent(rig)
        await rig.run(30, target=3.2, moving=False)
        await rig.run(30, target=3.2, moving=True)
        assert sent == []

    run(scenario())


def test_emergency_stop_of_a_dashboard_session_while_the_treadmill_is_unreachable(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.app.manual_start(3.0)
        await rig.run(40, signal=False)
        speed = drop_link(rig, belt_keeps_running=True)
        await rig.run(2, signal=False)
        await rig.app.emergency_stop()
        assert any("not connected" in e["text"] for e in rig.app.events)
        await rig.run(10, signal=False)
        restore_link(rig)
        await rig.run(1, signal=False)
        rig.treadmill.status.speed_kmh = speed
        await rig.run(5, signal=False)
        assert rig.treadmill.status.state is BeltState.STOPPED, "the belt ran on after STOP was pressed"
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_emergency_stop_without_a_treadmill_or_session_is_harmless(rig: Rig):
    async def scenario():
        await rig.app.emergency_stop()  # not connected
        await rig.connect()
        await rig.app.emergency_stop()  # connected, nothing running
        await rig.run(5, active=False)
        # A session started afterwards works normally.
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    run(scenario())


def test_emergency_stop_of_a_free_walk(rig: Rig):
    """STOP also stops a belt someone started with the remote."""

    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(30, active=False)
        assert rig.treadmill.status.speed_kmh > 0
        await rig.app.emergency_stop()
        await rig.run(5, active=False)
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


# ---- settings mid-session ---------------------------------------------------------------------


def test_lowering_the_cap_mid_session_takes_effect(rig: Rig):
    async def scenario():
        await session_running(rig, 6.0, seconds=60)
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.0 * MPH, abs=0.01)  # the default cap
        rig.app.update_config({"limits": {"max_speed_kmh": 2.0 * MPH}})
        assert rig.app.governor.limits is rig.app.config.limits
        await rig.run(15, target=6.0)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        assert rig.treadmill.status.target_kmh <= 2.0 * MPH + 1e-6
        rig.app.update_config({"limits": {"max_speed_kmh": 999}})  # clamped to the allowed range
        await rig.run(60, target=40.0)
        assert rig.config.limits.max_speed_kmh == MAX_SPEED_CAP_KMH
        assert rig.treadmill.status.speed_kmh == pytest.approx(4.0 * MPH, abs=0.01)  # the pad's own maximum

    run(scenario())
    # The addon is told the new limits for its in-game estimate.
    data = load_companion_data(rig)
    assert data.limits.maxKmh == pytest.approx(4.0 * MPH, abs=1e-3)
    saved = json.loads((rig.app.data_dir / "config.json").read_text("utf-8"))
    assert saved["limits"]["max_speed_kmh"] == MAX_SPEED_CAP_KMH


def test_cap_below_the_pads_minimum_keeps_the_belt_off(rig: Rig):
    rig.config.limits.max_speed_kmh = 0.8  # allowed by the settings; the pad's minimum is 0.97

    async def scenario():
        await rig.connect()
        sent = commands_sent(rig)
        await rig.run(1, active=False)
        await rig.run(60, target=3.0)
        assert sent == [] and rig.treadmill.status.speed_kmh == 0
        assert "cap" in rig.app.snapshot()["governor"]["reason"]

    run(scenario())


def test_default_stand_speed_lands_on_one_mph(rig: Rig):
    """The addon's default "standing" target is 1.6 km/h; on the US pad that
    is the 1.0 mph step, approached from the 0.6 mph the belt starts at."""

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=1.6, moving=False)
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.0 * MPH, abs=1e-6)

    run(scenario())


# ---- what the addon gets ------------------------------------------------------------------------


def test_addon_data_after_several_walks(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(120, target=3.2, session=11)  # a game walk
        await rig.run(5, active=False)
        rig.app.manual_start(2.4)  # a dashboard walk
        await rig.run(90, signal=False)
        rig.app.manual_stop()
        await rig.run(10, signal=False)
        rig.treadmill.press_start()  # a walk with the remote
        await rig.run(60, signal=False)
        rig.treadmill.press_stop()
        await rig.run(70, signal=False)
        await rig.run(10, target=3.0, session=12)  # too short to keep
        await rig.run(5, active=False)

    run(scenario())
    records = rig.app.store.records
    assert [r.source for r in records] == ["wow", "manual", "free"]
    data = load_companion_data(rig)
    sessions = [data.sessions[i] for i in range(1, 4)]
    assert data.sessions[4] is None
    assert [s.source for s in sessions] == ["free", "manual", "wow"]  # newest first
    for entry, record in zip(sessions, reversed(records), strict=True):
        assert entry.steps == record.steps
        assert entry.seconds == round(record.seconds)
        assert entry.distanceM == round(record.distance_m)
        assert entry.kcal == pytest.approx(record.kcal, abs=0.051)
        assert entry["end"] > entry.start
    assert sessions[2].beaconSession == 11 and sessions[0].beaconSession is None
    assert data.treadmill == "Simulated pad"
    assert data.profile.weightKg == 75 and data.profile.heightCm == 172
    assert data.limits.mphGrid is True
    assert data.limits.stepKmh == pytest.approx(0.1 * MPH, abs=1e-4)
    assert data.limits.accelKmhPerS == rig.config.limits.accel_kmh_per_s
    assert data.limits.decelKmhPerS == rig.config.limits.decel_kmh_per_s
    assert data.older is None
    # The dashboard's totals agree with what was synced - and the belt
    # coasting down after the last stop is not a "walk in progress".
    assert rig.app.snapshot()["session"] is None
    totals = rig.app.snapshot()["totals"]
    assert totals["all"]["sessions"] == 3
    assert totals["all"]["steps"] == sum(r.steps for r in records)


def test_missing_addon_folder_is_reported_not_fatal(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.config.wow.addon_dir = str(tmp_path / "nowhere")

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(60, target=3.2)
        await rig.run(3, active=False)

    run(scenario())
    assert len(rig.app.store.records) == 1  # the walk is saved regardless
    snapshot = rig.app.snapshot()
    assert snapshot["addon"]["dir"] is None and "not found" in snapshot["addon"]["error"]
    assert rig.app.sync_addon() is False


def test_treadmill_details_are_remembered_for_the_addon(rig: Rig):
    async def scenario():
        await rig.connect()

    run(scenario())
    state = json.loads((rig.app.data_dir / "state.json").read_text("utf-8"))
    assert state["treadmill"]["name"] == "Simulated pad" and state["treadmill"]["mph_grid"] is True
    limits = rig.app.belt_limits_for_addon()
    assert limits["minKmh"] == pytest.approx(0.6 * MPH, abs=1e-4)
    assert limits["maxKmh"] == pytest.approx(rig.config.limits.max_speed_kmh)  # the lower of pad and cap
    rig.config.limits.max_speed_kmh = 12.0
    assert rig.app.belt_limits_for_addon()["maxKmh"] == pytest.approx(4.0 * MPH, abs=1e-4)


# ---- the game going away ---------------------------------------------------------------------------


def test_closing_the_game_ends_the_session_and_stops_the_belt(rig: Rig):
    from walka.beacon import Decoded
    from walka.capture import GameWindow, ReadResult

    class Reader:
        window = GameWindow("World of Warcraft", 0, 0, 1920, 1080, r"C:\WoW\_forever_\WowForever.exe")

    reader = Reader()

    async def tick(frame: Frame | None) -> None:
        rig.clock.advance(TICK_S)
        result = ReadResult(Decoded(frame, 0, 3.0, 255, 0), (0, 0), "window") if frame else ReadResult(None, None, "")
        rig.beacon._ingest(rig.clock.mono, reader, result)
        await rig.app.tick()
        rig.treadmill.advance(TICK_S)

    async def scenario():
        await rig.connect()
        for seq in range(5):
            await tick(Frame(seq=seq, session=0))
        for seq in range(5, 205):
            await tick(Frame(seq=seq % 256, session=3, flags=Flags.ACTIVE | Flags.MOVING, target_raw=320))
        assert rig.treadmill.status.speed_kmh > 2.5
        reader.window = None  # Alt+F4
        for _ in range(40):  # 8 s: so far it is only a lost signal
            await tick(None)
        assert rig.treadmill.status.speed_kmh > 0 and rig.app.governor.driving
        for _ in range(30):
            await tick(None)
        assert rig.treadmill.status.speed_kmh == 0
        assert rig.treadmill.status.state is BeltState.STOPPED
        assert rig.app.governor.phase is Phase.IDLE and not rig.app.governor.driving
        assert rig.app.snapshot()["source"]["beacon"]["state"] in ("game_closed", "no_signal")

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["wow"]


def test_abandoned_session_is_closed_and_saved(rig: Rig):
    """Signal gone, belt paused, nobody comes back: after a quarter of an
    hour the walk is filed instead of staying open for ever."""

    async def scenario():
        await session_running(rig, 3.2, seconds=120)
        await rig.run(330, signal=False)  # hold, safe speed, pause
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.recorder is not None
        await rig.run(920, signal=False)
        assert rig.app.recorder is None

    run(scenario())
    assert len(rig.app.store.records) == 1
    # Two minutes of session plus the hold and safe-speed stages.
    assert rig.app.store.records[0].seconds > 200


# ---- heart rate -------------------------------------------------------------------------------------


class FakeHeartRate:
    def __init__(self) -> None:
        self.fresh = True
        self.bpm = 118
        self.closed = False

    def to_dict(self) -> dict:
        return {"enabled": True, "connected": True, "bpm": self.bpm if self.fresh else None}

    async def run(self) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


def test_heart_rate_reaches_the_recording(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.config.profile.age, rig.config.profile.sex = 40, "male"
    monitor = FakeHeartRate()
    rig.app.heart_rate = monitor

    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(200, target=3.2)  # 118 bpm is above the threshold for a 40-year-old (108)
        live = rig.app.snapshot()
        assert live["session"]["hr"] == 118 and live["session"]["calories_source"] == "heart_rate"
        assert live["heart_rate"]["bpm"] == 118
        monitor.fresh = False  # the strap falls off
        await rig.run(10, target=3.2)
        assert rig.app.snapshot()["session"]["hr"] is None
        await rig.run(3, active=False)

    run(scenario())
    record = rig.app.store.records[0]
    assert record.avg_hr == pytest.approx(118, abs=0.5) and record.max_hr == 118
    assert record.calories_source == "heart_rate"
    assert load_companion_data(rig).sessions[1].avgHr == 118


# ---- soak ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_soak_random_events_never_exceed_the_cap_or_crash(tmp_path: Path, seed: int):
    """Twenty-odd simulated minutes of everything at once. No exception may
    escape tick(), the belt's target never exceeds the cap in force, and the
    belt is never started while the cap is below what it can run at."""
    rng = random.Random(seed)
    rig = Rig(tmp_path, us_firmware=seed % 2 == 0)
    status = rig.treadmill.status
    limits = rig.config.limits
    session = 1
    violations: list[str] = []

    real_start = rig.treadmill.start

    async def checked_start():
        if limits.max_speed_kmh < status.min_kmh - 1e-9:
            violations.append("started with the cap below the pad's minimum")
        await real_start()

    rig.treadmill.start = checked_start

    async def scenario():
        nonlocal session
        await rig.connect()
        await rig.run(1, active=False)
        external = False
        for _ in range(260):
            previous_cap = limits.max_speed_kmh
            before = status.speed_kmh
            roll = rng.random()
            if roll < 0.40:
                await rig.run(
                    rng.uniform(0.2, 8),
                    target=rng.choice([0.0, 1.6, rng.uniform(0, 8), 40.95]),
                    moving=rng.random() < 0.7,
                    paused=rng.random() < 0.1,
                    session=session,
                )
            elif roll < 0.50:
                await rig.run(rng.uniform(0.2, 20), signal=False)
            elif roll < 0.58:
                await rig.run(rng.uniform(0.2, 4), active=False)
                session = session % 255 + 1
            elif roll < 0.62:
                rig.treadmill.press_stop()
            elif roll < 0.65:
                if status.connected and status.state not in (BeltState.RUNNING, BeltState.STARTING):
                    rig.treadmill.press_start()
                    external = True
            elif roll < 0.69:
                rig.treadmill.fail_commands = not rig.treadmill.fail_commands
            elif roll < 0.73:
                if status.connected:
                    drop_link(rig, belt_keeps_running=rng.random() < 0.5)
                else:
                    restore_link(rig)
            elif roll < 0.77:
                rig.app.manual_start(rng.choice([None, rng.uniform(0, 10)]))
            elif roll < 0.80:
                rig.app.manual_stop()
            elif roll < 0.82:
                rig.app.manual_pause() if rng.random() < 0.5 else rig.app.manual_resume()
            elif roll < 0.85:
                rig.app.manual_set_speed(rng.uniform(-1, 12))
            elif roll < 0.88:
                await rig.app.emergency_stop()
            elif roll < 0.91:
                rig.app.resume()
            elif roll < 0.96:
                rig.app.update_config({"limits": {"max_speed_kmh": rng.uniform(0.5, 13)}})
            else:
                rig.app.update_config({"units": rng.choice(["mph", "kmh"]), "profile": {"weight_kg": 80}})
                json.dumps(rig.app.snapshot())

            cap = limits.max_speed_kmh
            reachable = status.connected and not rig.treadmill.fail_commands
            if status.state not in (BeltState.RUNNING, BeltState.STARTING):
                external = False
            if reachable and status.target_kmh is not None:
                allowed = max(cap, previous_cap)
                if external:
                    allowed = max(allowed, status.min_kmh)  # started with the remote: the pad's own minimum
                if status.target_kmh > allowed + 1e-6 and rig.app.governor.driving and rig.app.governor.belt_on:
                    # The correction goes out with the next speed command
                    # (at once if it is our own command that is too high; one
                    # command interval after a failed attempt otherwise).
                    await rig.run(limits.command_interval_s + TICK_S * 2, signal=False)
                    if status.connected and status.target_kmh is not None and status.target_kmh > allowed + 1e-6:
                        violations.append(f"target {status.target_kmh:.3f} above the cap {allowed:.3f}")
            if reachable and rig.app.governor.driving and not external:
                assert status.speed_kmh <= max(cap, previous_cap, before) + 0.8 * 8 * TICK_S + 1e-6
        rig.treadmill.fail_commands = False
        if not status.connected:
            restore_link(rig)
        await rig.app.stop()

    run(scenario())
    assert violations == []
    for record in rig.app.store.records:
        assert record.seconds >= 20 and record.end >= record.start
        assert record.max_kmh <= max(12.0, 4.0 * MPH) + 1e-6


# ---- reconnect only on demand, treadmill switching, STOP hotkey ---------------------------------------


def test_connection_is_wanted_eagerly_at_start_then_only_on_demand(rig: Rig):
    """A connection attempt wakes a pad that was switched off, so after the
    first connection a dropped link is only chased while something needs it."""

    async def scenario():
        app = rig.app
        assert app.connection_wanted()  # start-up: keep trying
        await rig.connect()
        await rig.run(2, active=False)
        assert not app._eager_connect
        await rig.treadmill.disconnect()  # the user switched the pad off
        await rig.run(2, active=False)
        assert not app.connection_wanted()
        # A session starting in the game is a reason to reconnect ...
        await rig.run(1, target=3.0)
        assert app.connection_wanted()
        await rig.run(2, active=False)
        assert not app.connection_wanted()
        # ... so is a dashboard session, and so is pressing Connect.
        app.manual_start(3.0)
        assert app.connection_wanted()
        app.manual_stop()
        await rig.run(1, signal=False)
        assert not app.connection_wanted()
        app.connect()
        assert app.connection_wanted()
        # "Disconnect" on the dashboard wins over everything.
        await app.disconnect()
        app.manual_start(3.0)
        assert not app.connection_wanted()

    run(scenario())


def test_link_lost_mid_session_keeps_trying(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0, seconds=30)
        await rig.treadmill.disconnect()
        await rig.run(5, target=3.0)
        assert rig.app.connection_wanted()

    run(scenario())


def test_switching_treadmill_is_refused_while_the_belt_moves(rig: Rig):
    async def scenario():
        await session_running(rig, 3.0, seconds=30)
        rig.treadmill.status.address = "AA:AA:AA:AA:AA:AA"
        rig.app.connect("BB:BB:BB:BB:BB:BB")
        assert rig.config.treadmill.address != "BB:BB:BB:BB:BB:BB"
        assert not rig.app._switch_treadmill
        assert any("Stop the belt" in e["text"] for e in rig.app.events)
        await rig.run(3, active=False)
        await rig.run(8, active=False)
        rig.app.connect("BB:BB:BB:BB:BB:BB")
        assert rig.config.treadmill.address == "BB:BB:BB:BB:BB:BB" and rig.app._switch_treadmill

    run(scenario())


def test_hotkey_press_stops_the_belt_like_the_dashboard_button(rig: Rig):
    async def scenario():
        app = rig.app
        app._loop = asyncio.get_running_loop()
        await session_running(rig, 3.0, seconds=30)
        assert rig.treadmill.status.speed_kmh > 2.5
        app._hotkey_pressed()  # as the hotkey thread would
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await rig.run(10, target=3.0)
        assert rig.treadmill.status.speed_kmh == 0
        assert app.governor.user_stopped
        # ... and stays stopped until an explicit resume.
        await rig.run(30, target=3.0)
        assert rig.treadmill.status.speed_kmh == 0

    run(scenario())


def test_snapshot_reports_the_hotkey(rig: Rig):
    async def scenario():
        rig.app._loop = asyncio.get_running_loop()
        rig.app.register_hotkey()
        info = rig.app.snapshot()["hotkey"]
        assert info["combination"] == "Ctrl+Alt+X"
        assert info["active"] or info["error"]  # registered (Windows) or says why not
        rig.app.update_config({"stop_hotkey": "x"})  # unusable: a bare letter
        assert rig.config.stop_hotkey == "ctrl+alt+x"
        rig.app.update_config({"stop_hotkey": ""})  # off
        assert rig.app.snapshot()["hotkey"]["combination"] == ""
        if rig.app.hotkey is not None:
            rig.app.hotkey.stop()

    run(scenario())
