"""End-to-end: beacon frames -> governor -> simulated treadmill -> recorded
walk -> data file for the addon. Time is fully simulated."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from walka import addon_sync
from walka.app import TICK_S, App
from walka.config import Config
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
        assert rig.treadmill.status.speed_kmh == pytest.approx(1.6, abs=0.09)
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
