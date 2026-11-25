"""The Running Speed and Cadence reader (foot pods, treadmill sensors), by replaying recordings.

The recordings are put together here from the RSC Measurement layout of the
Bluetooth specification; none comes from a real sensor yet.
"""

from __future__ import annotations

import asyncio
import inspect
import struct
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.recording import Recording, ReplayBackend
from walka.treadmill import ftms_ble, readers, rsc
from walka.treadmill.base import BeltState, TreadmillError, TreadmillEvent
from walka.treadmill.replay import ReplayTreadmill
from walka.treadmill.rsc import RscTreadmill, steps_per_minute

ADDRESS = "C0:FF:EE:00:00:01"
MOTOR_VERBS = ("start", "stop", "pause", "resume", "halt", "set_speed", "set_target", "command", "write", "request_control")


def packet(kmh: float, cadence: int, distance_m: float | None = None) -> bytes:
    flags = 0x02 if distance_m is not None else 0x00
    data = struct.pack("<BHB", flags, round(kmh / 3.6 * 256), cadence)
    if distance_m is not None:
        data += struct.pack("<I", round(distance_m * 10))
    return data


def a_walk(*, cadence: int = 110, kmh: float = 3.6, seconds: int = 60, distance: bool = True) -> Recording:
    """A sensor heard once a second: still, then walking for ``seconds``, then still."""
    rec = Recording(name="Stryd", address=ADDRESS)
    rec.add_characteristic(ftms.RSC_SERVICE, ftms.RSC_MEASUREMENT, ["notify"])
    t, metres = 0.0, 0.0
    for _ in range(3):
        rec.add_notification(t, ftms.RSC_MEASUREMENT, packet(0, 0, metres if distance else None))
        t += 1
    for _ in range(seconds):
        metres += kmh / 3.6
        rec.add_notification(t, ftms.RSC_MEASUREMENT, packet(kmh, cadence, metres if distance else None))
        t += 1
    rec.add_notification(t, ftms.RSC_MEASUREMENT, packet(0, 0, metres if distance else None))
    return rec


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> FakeTime:
    fake = FakeTime()
    for module in (ftms_ble, rsc):
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


def play(rec: Recording, clock: FakeTime) -> tuple[RscTreadmill, list[TreadmillEvent], list[tuple]]:
    start = clock.now
    seen: list[tuple] = []
    reader = RscTreadmill(ADDRESS)

    def tick(t: float) -> None:
        clock.now = start + t

    backend = ReplayBackend(rec, time_scale=0, on_packet=tick)
    reader._backend = backend
    events: list[TreadmillEvent] = []
    reader.on_event = events.append

    async def go() -> None:
        await reader.connect()
        original = reader._on_rsc

        def watch(data: bytes) -> None:
            original(data)
            seen.append((reader.status.state, reader.status.speed_kmh, reader.status.steps))

        backend.clients[-1]._callbacks[ftms.RSC_MEASUREMENT] = watch
        await asyncio.wait_for(backend.clients[-1].finished.wait(), 5)

    asyncio.run(go())
    return reader, events, seen


def test_a_walk_has_real_steps_and_the_sensors_distance(clock):
    reader, events, seen = play(a_walk(cadence=110, seconds=60), clock)
    # 110 steps a minute for a minute (the first walking second has nothing before it to count from).
    assert reader.status.steps == pytest.approx(110 * 59 / 60, abs=1)
    assert reader.status.distance_m == pytest.approx(60.0, abs=0.2)
    assert seen[0][0] is BeltState.STOPPED and seen[5][0] is BeltState.RUNNING and seen[-1][0] is BeltState.STOPPED
    assert max(speed for _, speed, _ in seen) == pytest.approx(3.6, abs=0.02)
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]


def test_a_sensor_that_counts_strides_is_doubled(clock):
    reader, _, _ = play(a_walk(cadence=55, seconds=60, distance=False), clock)
    assert reader.status.steps == pytest.approx(110 * 59 / 60, abs=1)
    assert reader.status.distance_m is None  # the companion works it out from the speed


@pytest.mark.parametrize(
    "cadence,kmh,expected",
    [(110, 3.6, 110.0), (55, 3.6, 110.0), (55, 1.0, 55.0), (0, 3.6, 0.0), (160, 9.0, 160.0)],
)
def test_steps_per_minute(cadence, kmh, expected):
    assert steps_per_minute(cadence, kmh) == expected


def test_a_sensor_is_picked_by_address_and_it_must_send_rsc(clock):
    with pytest.raises(TreadmillError, match="Show all Bluetooth devices"):
        asyncio.run(RscTreadmill().connect())
    ftms_only = Recording(name="URTM030", address=ADDRESS)
    ftms_only.add_characteristic(ftms.FTMS_SERVICE, ftms.TREADMILL_DATA, ["notify"])
    reader = RscTreadmill(ADDRESS, backend=ReplayBackend(ftms_only, time_scale=0))
    with pytest.raises(TreadmillError, match="does not send running speed and cadence"):
        asyncio.run(reader.connect())


def test_it_only_listens():
    public = {name for name, _ in inspect.getmembers(RscTreadmill) if not name.startswith("_")}
    assert not {name for name in public if name in MOTOR_VERBS or name.startswith(("set_", "cmd_"))}
    source = inspect.getsource(rsc)
    for word in ("CONTROL_POINT", "write(", "set_speed", "request_control"):
        assert word not in source


def test_the_registry_and_a_replay_pick_the_rsc_reader(clock):
    assert readers.reader_for([ftms.RSC_MEASUREMENT]).key == "rsc"
    assert isinstance(readers.build("rsc", ADDRESS), RscTreadmill)
    replay = ReplayTreadmill(a_walk(seconds=5), time_scale=0)
    assert isinstance(replay._reader, RscTreadmill)

    async def go() -> None:
        await replay.connect()
        assert replay.status.connected

    asyncio.run(go())


def test_picking_an_rsc_sensor_switches_the_reader(tmp_path, monkeypatch):
    from test_app_ftms import PadRig

    rig = PadRig(tmp_path, monkeypatch)

    async def go() -> None:
        rig.app.config.treadmill.address = ADDRESS
        await rig.app.use_reader("rsc")

    asyncio.run(go())
    assert isinstance(rig.app.treadmill, RscTreadmill) and rig.app.config.treadmill.kind == "rsc"
    assert rig.app.treadmill.on_event == rig.app._on_treadmill_event
    assert rig.app.treadmill._backend is rig.backend
