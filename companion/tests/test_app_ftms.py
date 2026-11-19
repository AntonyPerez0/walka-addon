"""End to end with the real Bluetooth reader: beacon frames and an emulated
UREVO pad (GATT level) -> FtmsTreadmill -> recorded walk. What matters here
is what reaches the *pad*: nothing. ``FakePad.writes`` holds every byte
written to it over Bluetooth, and every scenario ends with it empty."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_app import Clock
from walka import ftms
from walka.app import FREE_WALK_GRACE_S, TICK_S, App
from walka.config import Config
from walka.protocol import Flags, Frame, kmh_to_raw
from walka.sources import BeaconSource
from walka.treadmill import BeltState, ftms_ble
from walka.treadmill.ftms_ble import FtmsTreadmill
from walka_fakes import FakeBackend, FakePad

MPH = 1.609344


class PadRig:
    def __init__(self, tmp_path: Path, monkeypatch, pad: FakePad | None = None, **driver_kwargs) -> None:
        self.clock = Clock()
        # The reader stamps the pad's data with time.monotonic().
        monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: self.clock.mono))
        self.pad = pad or FakePad(steps_in_data=True)
        self.backend = FakeBackend(self.pad)
        self.treadmill = FtmsTreadmill(backend=self.backend, **driver_kwargs)
        self.config = Config()
        self.beacon = BeaconSource(None, clock=lambda: self.clock.mono)
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

    async def run(
        self, seconds: float, *, active=True, paused=False, moving=True, signal=True, session=1, legacy_target=0.0
    ) -> None:
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
                        target_raw=kmh_to_raw(legacy_target),
                    )
                )
            await self.app.tick()
            self.pad.advance(TICK_S)

    async def walking(self, kmh: float = 2.0 * MPH, seconds: float = 40) -> None:
        """Connected, a session marked in the game, and the walker on the
        belt: start on the remote, the countdown, then ``kmh``."""
        await self.treadmill.connect()
        await self.run(1, active=False)
        self.pad.press_start()
        await self.run(4)
        self.pad.press_speed(kmh)
        await self.run(seconds)

    def heard_nothing_from_walka(self) -> bool:
        return self.pad.writes == [] and ftms.CONTROL_POINT not in self.pad.subscriptions


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_walk_on_the_emulated_urevo_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(2.0 * MPH, seconds=300)
        assert rig.pad.target_tenths == 20 and rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        assert rig.treadmill.status.state is BeltState.RUNNING
        assert rig.app.snapshot()["treadmill"]["moving"] is True
        rig.pad.press_stop()
        await rig.run(5)
        await rig.run(3, active=False)

    run(scenario())
    (record,) = rig.app.store.records
    assert record.source == "wow" and record.treadmill == "URTM030"
    assert 290 <= record.seconds <= 310
    assert record.distance_m == pytest.approx(2.0 * MPH / 3.6 * 300, rel=0.05)
    assert record.steps_source == "treadmill" and record.distance_source == "treadmill"
    assert rig.heard_nothing_from_walka()


def test_the_first_pad_is_the_only_pad(tmp_path: Path, monkeypatch):
    """Walka remembers the treadmill it first listened to, so that a second
    one in Bluetooth range - a neighbour's - is never counted as yours."""
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        assert rig.config.treadmill.address == ""
        await rig.treadmill.connect()
        assert rig.config.treadmill.address == rig.pad.address == rig.treadmill.address
        assert rig.app.snapshot()["treadmill"]["pinned"] is True
        rig.app.forget_treadmill()
        assert rig.config.treadmill.address == "" and rig.treadmill.address is None
        assert rig.app.snapshot()["treadmill"]["pinned"] is False

    run(scenario())
    assert any("only reconnects to this treadmill" in event["text"] for event in rig.app.events)
    assert rig.heard_nothing_from_walka()


@pytest.mark.parametrize("how", ["press_stop", "press_pause", "pull_safety_key"])
def test_the_walker_stops_the_belt_and_walka_writes_it_down(tmp_path: Path, monkeypatch, how: str):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(seconds=60)
        getattr(rig.pad, how)()
        await rig.run(300)  # the game goes on showing a session, a moving character ...
        assert rig.pad.speed_kmh == 0 and rig.pad.state in ("idle", "paused")  # ... and the belt stays as it was left
        assert rig.app.snapshot()["treadmill"]["moving"] is False
        await rig.run(3, active=False)

    run(scenario())
    (record,) = rig.app.store.records
    assert 55 <= record.seconds <= 70
    assert rig.heard_nothing_from_walka()


def test_link_loss_and_reconnect_with_the_pad_still_running(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(seconds=60)
        rig.pad.drop_link()
        await rig.run(30)  # the pad runs on at 2.0 mph, out of hearing
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        assert rig.app.connection_wanted()
        await rig.treadmill.connect()
        await rig.run(60)
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH) and rig.pad.target_tenths == 20
        await rig.run(3, active=False)

    run(scenario())
    (record,) = [r for r in rig.app.store.records if r.source == "wow"]
    assert 115 <= record.seconds <= 130  # the half minute nobody heard is not made up
    assert rig.pad.connects == 2 and rig.heard_nothing_from_walka()


def test_closing_walka_changes_nothing_at_the_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(3.0 * MPH, seconds=60)
        await rig.app.stop()

    run(scenario())
    assert rig.pad.state == "running" and rig.pad.target_tenths == 30 and rig.pad.speed_kmh == pytest.approx(3.0 * MPH)
    assert rig.pad.client is None and len(rig.app.store.records) == 1
    assert rig.heard_nothing_from_walka()


@pytest.mark.parametrize("seed", range(4))
def test_soak_on_the_emulated_pad(tmp_path: Path, monkeypatch, seed: int):
    """Anything the game, the dashboard or the radio can do, for a long
    time - and at the end the pad has still not been sent a single byte."""
    rng = random.Random(seed)
    rig = PadRig(tmp_path, monkeypatch, FakePad(us_firmware=bool(seed % 2), steps_in_data=True))

    async def scenario():
        await rig.treadmill.connect()
        for _ in range(120):
            event = rng.choice(
                ["start", "speed", "speed", "pause", "stop", "key", "drop", "connect", "forget", "config", None, None]
            )
            if event in ("start", "pause", "stop"):
                getattr(rig.pad, f"press_{event}")()
            elif event == "speed":
                rig.pad.press_speed(rng.uniform(0.5, 9.0))
            elif event == "key":
                rig.pad.pull_safety_key()
            elif event == "drop":
                rig.pad.drop_link()
            elif event == "connect":
                rig.app.connect()
                await rig.treadmill.connect()
            elif event == "forget":
                rig.app.forget_treadmill()
            elif event == "config":
                rig.app.update_config({"profile": {"weight_kg": rng.uniform(50, 100)}, "limits": {"max_speed_kmh": 1}})
            game = rng.choice(["session", "paused", "none", "lost", "asks"])
            await rig.run(
                rng.uniform(0.2, 30),
                active=game != "none",
                paused=game == "paused",
                moving=rng.random() < 0.7,
                signal=game != "lost",
                session=rng.randint(1, 3),
                legacy_target=rng.uniform(0, 40) if game == "asks" else 0.0,
            )
            assert rig.heard_nothing_from_walka()
        await rig.run(FREE_WALK_GRACE_S + 5, active=False)
        await rig.app.stop()

    run(scenario())
    assert rig.heard_nothing_from_walka()
    for record in rig.app.store.records:
        assert record.seconds >= 20 and record.distance_m >= 0 and record.steps >= 0


# ---- treadmills of other makes ----------------------------------------------------------


def test_walk_on_an_emulated_wellfit(tmp_path: Path, monkeypatch):
    """A full-size treadmill that goes to 10 mph: found by its name and read
    like any pad. How fast it goes is its owner's business - Walka has no
    speed limit of its own any more, because it sets no speed."""
    pad = FakePad(name="WELLFIT TM Linker", address="A4:C1:38:00:29:01", max_tenths=100, says_treadmill=True)
    rig = PadRig(tmp_path, monkeypatch, pad)

    async def scenario():
        await rig.walking(6.0 * MPH, seconds=120)
        assert rig.pad.target_tenths == 60
        await rig.run(3, active=False)

    run(scenario())
    (record,) = rig.app.store.records
    assert record.treadmill == "WELLFIT TM Linker"
    assert record.max_kmh == pytest.approx(6.0 * MPH, abs=0.02)
    assert rig.heard_nothing_from_walka()


def test_a_treadmill_that_takes_no_commands_at_all_is_just_as_good(tmp_path: Path, monkeypatch):
    """Plenty of treadmills show their numbers to apps and cannot be driven
    by them. For a companion that only listens, that is the whole job."""
    rig = PadRig(tmp_path, monkeypatch, FakePad(name="Treadmill X", has_control_point=False))

    async def scenario():
        await rig.walking(seconds=60)
        await rig.run(3, active=False)

    run(scenario())
    assert len(rig.app.store.records) == 1
    assert not [event for event in rig.app.events if event["level"] != "info"]
    assert rig.heard_nothing_from_walka()
