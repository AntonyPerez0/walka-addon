"""End to end with the real Bluetooth driver: beacon frames -> governor ->
FtmsTreadmill -> an emulated UREVO pad (GATT level). What matters here is
what the *pad* ends up doing, in its own units."""

from __future__ import annotations

import asyncio
import itertools
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_app import Clock
from walka.app import TICK_S, App
from walka.config import MAX_SPEED_CAP_KMH, Config, apply_update
from walka.governor import Phase
from walka.protocol import Flags, Frame, kmh_to_raw
from walka.sources import BeaconSource
from walka.treadmill import BeltState, ftms_ble
from walka.treadmill.ftms_ble import FtmsTreadmill
from walka_fakes import FakeBackend, FakePad

MPH = 1.609344


class PadRig:
    def __init__(self, tmp_path: Path, monkeypatch, pad: FakePad | None = None, **driver_kwargs) -> None:
        self.clock = Clock()
        # The driver stamps data and its own commands with time.monotonic().
        monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: self.clock.mono))
        self.pad = pad or FakePad(steps_in_data=True)
        self.backend = FakeBackend(self.pad)
        driver_kwargs.setdefault("command_timeout_s", 0.02)
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
        self.max_pad_kmh = 0.0

    async def run(self, seconds: float, *, target=None, active=True, paused=False, moving=True, signal=True) -> None:
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
                    Frame(seq=self.seq, session=1 if active else 0, flags=flags, target_raw=kmh_to_raw(target or 0.0))
                )
            await self.app.tick()
            self.pad.advance(TICK_S)
            await asyncio.sleep(0)  # let indications scheduled by the pad be delivered
            self.max_pad_kmh = max(self.max_pad_kmh, self.pad.speed_kmh)

    async def session(self, target: float, seconds: float = 40) -> None:
        await self.treadmill.connect()
        await self.run(1, active=False)
        await self.run(seconds, target=target)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_walk_on_the_emulated_urevo_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2, seconds=300)
        # Connect touched nothing; the first writes are Request Control and Start.
        assert rig.pad.writes[:2] == [b"\x00", b"\x07"]
        assert rig.pad.writes.count(b"\x00") == 1 and rig.pad.writes.count(b"\x07") == 1
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.pad.target_tenths == 20  # 3.2 km/h asked: exactly 2.0 mph on the pad
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        assert rig.treadmill.status.speed_kmh == pytest.approx(3.21)  # as the pad reports it
        # Every speed write was on the firmware's 0.1 km/h grid and climbed gently.
        raws = [int.from_bytes(w[1:3], "little") for w in rig.pad.writes if w[:1] == b"\x02"]
        assert raws and all(raw % 10 == 0 for raw in raws)
        assert all(b - a <= 60 for a, b in itertools.pairwise(raws))  # <= 0.5 km/h per command (+ grid rounding)
        await rig.run(3, active=False)
        assert rig.pad.writes[-1] == b"\x08\x01" and rig.pad.state == "idle"
        await rig.run(5, active=False)
        assert rig.pad.speed_kmh == 0

    run(scenario())
    record = rig.app.store.records[0]
    assert record.treadmill == "URTM030" and record.source == "wow"
    assert 270 <= record.seconds <= 300
    assert record.distance_source == "treadmill" and record.steps_source == "treadmill"
    assert record.distance_m == pytest.approx(2.0 * MPH / 3.6 * record.seconds, rel=0.05)
    assert record.steps == pytest.approx(record.distance_m * 1.7, rel=0.05)
    assert record.max_kmh == pytest.approx(3.21)


@pytest.mark.parametrize("cap_kmh", [1.0, 1.7, 2.5, 3.2187, 4.0, 4.83, 5.5, 6.4, 6.5, 12.0])
def test_cap_holds_at_the_pad_in_its_own_units(tmp_path: Path, monkeypatch, cap_kmh: float):
    """The governor thinks in km/h, the driver sends rounded-up km/h, the
    firmware truncates to 0.1 mph: after all of that the belt must be on the
    highest 0.1 mph step that does not exceed the cap - and never above it."""
    rig = PadRig(tmp_path, monkeypatch)
    rig.config.limits.max_speed_kmh = cap_kmh

    async def scenario():
        await rig.session(40.95, seconds=60)

    run(scenario())
    expected_tenths = min(40, int(cap_kmh / MPH * 10 + 1e-9))
    assert rig.pad.target_tenths == expected_tenths
    assert rig.pad.speed_kmh == pytest.approx(expected_tenths * MPH / 10)
    assert rig.max_pad_kmh <= cap_kmh + 1e-9


def test_cap_holds_on_a_metric_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch, pad=FakePad(us_firmware=False, kmh_range=(100, 600, 10)))
    rig.config.limits.max_speed_kmh = 4.83

    async def scenario():
        await rig.session(40.95, seconds=60)

    run(scenario())
    assert rig.pad.target_centi == 480 and rig.max_pad_kmh <= 4.83


def test_stop_on_the_pads_remote_is_final(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        writes = len(rig.pad.writes)
        rig.pad.press_stop()
        await rig.run(60, target=3.2)
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert rig.pad.state == "idle" and rig.pad.speed_kmh == 0
        assert rig.pad.writes[writes:] == [], "something was sent to a belt stopped at the pad"
        await rig.run(30, signal=False)
        await rig.run(30, target=3.2)
        assert rig.pad.writes[writes:] == []
        # Pause -> resume in the game is the explicit restart.
        await rig.run(2, target=3.2, paused=True)
        await rig.run(40, target=3.2)
        assert rig.pad.target_tenths == 20 and rig.pad.speed_kmh == pytest.approx(2.0 * MPH)

    run(scenario())


def test_start_on_the_pads_remote_resumes_following(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        rig.pad.press_stop()
        await rig.run(20, target=3.2)
        writes = len(rig.pad.writes)
        rig.pad.press_start()
        await rig.run(40, target=3.2)
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert b"\x07" not in rig.pad.writes[writes:]  # no second start from us
        assert rig.pad.target_tenths == 20

    run(scenario())


def test_safety_key_is_final_too(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        writes = len(rig.pad.writes)
        rig.pad.pull_safety_key()
        await rig.run(60, target=3.2)
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert rig.pad.writes[writes:] == [] and rig.pad.speed_kmh == 0

    run(scenario())


def test_link_loss_and_reconnect_with_the_pad_still_running(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        rig.pad.drop_link()
        await rig.run(10, target=4.0)  # the pad runs on at 2.0 mph, unreachable
        assert rig.app.governor.phase is Phase.NO_TREADMILL
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        rig.pad.writes.clear()
        await rig.treadmill.connect()
        await rig.run(30, target=4.0)
        assert b"\x07" not in rig.pad.writes  # adopted: no start, no countdown
        assert rig.pad.writes[0] == b"\x00"  # control is requested again on the new connection
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.pad.target_tenths == 25  # 4.0 km/h -> 2.5 mph

    run(scenario())


def test_link_loss_and_reconnect_with_the_pad_stopped(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        rig.pad.drop_link()
        rig.pad._halt(paused=False)  # switched off and on again
        await rig.run(10, target=3.2, moving=True)
        await rig.treadmill.connect()
        rig.pad.writes.clear()
        await rig.run(60, target=3.2, moving=True)
        assert rig.pad.writes == [], "the belt was restarted without a deliberate act"
        # Found stopped: as final as a stop pressed on the pad itself.
        assert rig.app.governor.phase is Phase.USER_STOPPED
        await rig.run(2, target=3.2, moving=False)
        await rig.run(40, target=3.2, moving=True)
        assert rig.pad.writes == [] and rig.pad.speed_kmh == 0
        await rig.run(2, target=3.2, paused=True)
        await rig.run(40, target=3.2)
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)

    run(scenario())


@pytest.mark.parametrize("meanwhile", ["session_ended", "paused", "standing"])
def test_what_happened_during_a_link_loss_is_applied_on_reconnect(tmp_path: Path, monkeypatch, meanwhile: str):
    """The link drops, the pad runs on, and in the game the session is
    ended / paused / the character stops (stand speed 0). When the link is
    back the belt must not be left running under a session that says otherwise."""
    rig = PadRig(tmp_path, monkeypatch)
    frames = {
        "session_ended": dict(active=False),
        "paused": dict(target=3.2, paused=True),
        "standing": dict(target=0.0, moving=False),
    }[meanwhile]

    async def scenario():
        await rig.session(3.2)
        rig.pad.drop_link()
        await rig.run(8, **frames)
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)  # out of reach, still running
        rig.pad.writes.clear()
        await rig.treadmill.connect()
        await rig.run(8, **frames)
        assert rig.pad.speed_kmh == 0, f"belt left running although the session is {meanwhile}"
        expected = b"\x08\x01" if meanwhile == "session_ended" else b"\x08\x02"
        assert rig.pad.writes == [b"\x00", expected]

    run(scenario())


def test_pause_that_never_reaches_the_pad_is_repeated(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        rig.pad.lose_writes = True  # written, lost on the air: no answer, no effect
        await rig.run(3, target=3.2, paused=True)
        assert rig.pad.state == "running"
        assert rig.pad.writes.count(b"\x08\x02") == 1  # not hammered either
        rig.pad.lose_writes = False
        await rig.run(8, target=3.2, paused=True)
        assert rig.pad.speed_kmh == 0
        assert rig.pad.writes[-1] == b"\x08\x01" and rig.pad.state == "idle"  # the repeat is a full stop
        await rig.run(40, target=3.2)  # and the session resumes normally afterwards
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)

    run(scenario())


def test_pad_that_does_not_do_pause_is_stopped_instead(tmp_path: Path, monkeypatch):
    """Unknown firmware: if "pause" (08 02) is refused, the belt must not
    simply run on through a paused session."""
    rig = PadRig(tmp_path, monkeypatch)
    rig.pad.reject_payloads[b"\x08\x02"] = 0x02  # op code not supported

    async def scenario():
        await rig.session(3.2)
        await rig.run(4, target=3.2, paused=True)
        assert rig.pad.state == "running"  # the pause was refused
        assert any("pause failed" in e["text"] for e in rig.app.events)
        await rig.run(6, target=3.2, paused=True)
        assert rig.pad.state == "idle" and rig.pad.speed_kmh == 0
        assert rig.pad.writes[-1] == b"\x08\x01"
        await rig.run(40, target=3.2)
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)

    run(scenario())


def test_pad_that_never_acknowledges_is_still_driven_and_stopped(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch, pad=FakePad(silent=True), command_timeout_s=0.005)

    async def scenario():
        await rig.session(3.2, seconds=60)
        assert rig.pad.target_tenths == 20
        assert rig.pad.writes.count(b"\x07") == 1  # an unanswered start is not repeated
        await rig.app.emergency_stop()
        await rig.run(5, target=3.2)
        assert rig.pad.state == "idle" and rig.pad.speed_kmh == 0

    run(scenario())


def test_rejected_speed_commands_do_not_stall_the_session(tmp_path: Path, monkeypatch):
    """A pad that refuses its top speed with the handle folded (possible on
    the real one): the walk goes on at what it accepts, and stop still works."""
    rig = PadRig(tmp_path, monkeypatch)
    rig.config.limits.max_speed_kmh = 12.0

    async def scenario():
        await rig.session(3.2)
        rig.pad.reject[0x02] = 0x03  # INVALID_PARAMETER for every further speed
        await rig.run(20, target=6.0)
        assert rig.pad.target_tenths == 20  # unchanged
        assert any("set_speed failed" in e["text"] for e in rig.app.events)
        rig.pad.reject.clear()
        await rig.run(20, target=6.0)
        assert rig.pad.target_tenths == 37  # 6.0 km/h -> 3.7 mph
        await rig.run(3, active=False)
        assert rig.pad.state == "idle"

    run(scenario())


def test_emergency_stop_and_exit_reach_the_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.session(3.2)
        await rig.app.emergency_stop()
        assert rig.pad.writes[-1] == b"\x08\x01" and rig.pad.state == "idle"
        await rig.run(10, target=3.2)
        assert rig.app.governor.phase is Phase.USER_STOPPED
        assert rig.treadmill.status.state is BeltState.STOPPED
        rig.app.resume()
        await rig.run(30, target=3.2)
        assert rig.pad.state == "running"
        await rig.app.stop()
        assert rig.pad.writes[-1] == b"\x08\x01" and rig.pad.state == "idle"
        assert rig.pad.client is None  # and the pad is released for the phone app

    run(scenario())


@pytest.mark.parametrize("seed", range(4))
def test_soak_on_the_emulated_pad(tmp_path: Path, monkeypatch, seed: int):
    """Random play against the driver and the firmware model: the pad's own
    target (in mph steps) never goes above the cap."""
    rng = random.Random(seed)
    rig = PadRig(tmp_path, monkeypatch, pad=FakePad(max_tenths=rng.choice([40, 62]), require_control=seed == 1))
    limits = rig.config.limits
    limits.max_speed_kmh = rng.uniform(1.2, 7.0)

    async def scenario():
        await rig.treadmill.connect()
        await rig.run(1, active=False)
        external = False
        ramping_until = 0.0  # a lowered cap is approached at the deceleration limit, not in one jump
        for _ in range(140):
            previous_cap = limits.max_speed_kmh
            roll = rng.random()
            if roll < 0.5:
                await rig.run(
                    rng.uniform(0.2, 8),
                    target=rng.choice([0.0, 1.6, rng.uniform(0, 8), 40.95]),
                    moving=rng.random() < 0.7,
                    paused=rng.random() < 0.1,
                )
            elif roll < 0.6:
                await rig.run(rng.uniform(0.2, 15), signal=False)
            elif roll < 0.68:
                await rig.run(rng.uniform(0.2, 3), active=False)
            elif roll < 0.72:
                rig.pad.press_stop()
            elif roll < 0.75:
                if rig.pad.state in ("idle", "paused"):
                    rig.pad.press_start()
                    external = True
            elif roll < 0.79:
                if rig.pad.client is not None:
                    rig.pad.drop_link()
                else:
                    await rig.treadmill.connect()
            elif roll < 0.83:
                rig.pad.take_control_away()
            elif roll < 0.87:
                await rig.app.emergency_stop()
            elif roll < 0.9:
                rig.app.resume()
            else:
                rig.app.update_config({"limits": {"max_speed_kmh": rng.uniform(0.5, 8.0)}})
                over = max(0.0, rig.pad.target_kmh - limits.max_speed_kmh)
                ramping_until = rig.clock.mono + over / limits.decel_kmh_per_s + 2 * limits.command_interval_s + 1.0
            if rig.pad.state not in ("running", "countdown"):
                external = False
            ticked = roll < 0.68 and rig.clock.mono > ramping_until  # (a cap change needs time to reach the pad)
            if ticked and rig.pad.client is not None and rig.pad.target_tenths is not None and not external:
                allowed = max(limits.max_speed_kmh, previous_cap)
                if rig.app.governor.belt_on and rig.app.governor.commanded_kmh > 0:
                    assert rig.pad.target_tenths * MPH / 10 <= allowed + 1e-9, "the pad was set above the cap"
        if rig.pad.client is None:
            await rig.treadmill.connect()
        await rig.app.stop()
        assert rig.pad.client is None

    run(scenario())


# ---- treadmills of other makes ----------------------------------------------------------


def test_walk_on_an_emulated_wellfit(tmp_path: Path, monkeypatch):
    """A full-size treadmill that goes to 10 mph: found by its name, driven
    over the same FTMS commands, and held under Walka's own speed cap."""
    pad = FakePad(name="WELLFIT TM Linker", address="A4:C1:38:00:29:01", max_tenths=100, says_treadmill=True)
    rig = PadRig(tmp_path, monkeypatch, pad)
    apply_update(rig.config, {"limits": {"max_speed_kmh": 99.0}})  # whatever a settings file asks for...
    assert rig.config.limits.max_speed_kmh == MAX_SPEED_CAP_KMH  # ... the cap stays at walking pace

    async def scenario():
        await rig.session(16.0, seconds=120)  # the game asks for the treadmill's own top speed
        assert rig.pad.writes[:3] == [b"\x00", b"\x00", b"\x07"]
        assert rig.app.governor.phase is Phase.FOLLOWING
        assert rig.pad.target_tenths == 40  # 6.5 km/h cap: 4.0 mph on the belt, not 10
        await rig.run(3, active=False)
        assert rig.pad.writes[-1] == b"\x08\x01" and rig.pad.state == "idle"

    run(scenario())
    assert rig.max_pad_kmh <= MAX_SPEED_CAP_KMH
    assert rig.app.store.records[0].treadmill == "WELLFIT TM Linker"
    assert not any("speed commands" in event["text"] for event in rig.app.events)


def test_a_treadmill_that_says_it_takes_no_speed_commands_is_pointed_out(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch, FakePad(name="Treadmill X", speed_target=False))

    async def scenario():
        await rig.treadmill.connect()

    run(scenario())
    warnings = [event["text"] for event in rig.app.events if event["level"] == "warning"]
    assert len(warnings) == 1 and "does not take speed commands" in warnings[0]
