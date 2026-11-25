"""The FitShow reader, by replaying recordings put together from the FitShow
protocol as public sources describe it (see docs/HARDWARE.md). None comes
from a machine tried with Walka."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.recording import Recording, ReplayBackend
from walka.treadmill import ftms_ble, proprietary, readers
from walka.treadmill.base import BeltState, TreadmillError
from walka.treadmill.fitshow import NOTIFY, FitShowTreadmill, decode, xor

ADDRESS = "C8:00:00:00:00:01"
QUERY = bytes.fromhex("02515103")
# A running frame from another project's tests: running, 3.5, incline 2, 300 s, distance 500, kcal 25, 528 steps, 90 bpm.
SAMPLE = bytes.fromhex("02 51 03 23 02 2C 01 F4 01 19 00 10 02 5A 00 FA 03")


def status(state: int, speed_tenths: int = 0, seconds: int = 0, steps: int = 0) -> bytes:
    if state in (1, 3, 4, 10):
        body = bytes([0x51, state, speed_tenths, 0]) + seconds.to_bytes(2, "little") + bytes(4)
        body += steps.to_bytes(2, "little") + bytes([0, 0])
    else:
        body = bytes([0x51, state])
    return b"\x02" + body + bytes([xor(body)]) + b"\x03"


def test_a_running_frame():
    r = decode(SAMPLE)
    assert r.state is BeltState.RUNNING and r.speed_kmh == 3.5 and r.elapsed_s == 300 and r.steps == 528
    assert r.distance_m is None and r.energy_kcal is None  # units disputed: Walka works them out itself
    assert decode(SAMPLE, mph=True).speed_kmh == pytest.approx(3.5 * 1.609344)


@pytest.mark.parametrize(
    "frame,state",
    [(status(0), BeltState.STOPPED), (bytes.fromhex("02 51 02 03 50 03"), BeltState.STARTING), (status(10, 30, 9, 9), BeltState.PAUSED)],
)
def test_states(frame, state):
    reading = decode(frame)
    assert reading.state is state and reading.speed_kmh == 0.0


@pytest.mark.parametrize(
    "frame",
    [SAMPLE[:-2] + b"\x00\x03", b"\x02\x50\x02\x3c\x0a\x00\x66\x03", SAMPLE[:-1] + b"\x00", b"\x02\x51"],
)
def test_other_and_damaged_frames_are_ignored(frame):
    assert decode(frame) is None


def a_walk(name="FS-3D6CD7") -> Recording:
    rec = Recording(name=name, address=ADDRESS)
    rec.add_characteristic(ftms.uuid16(0xFFF0), NOTIFY, ["notify"])
    rec.add_characteristic(ftms.uuid16(0xFFF0), ftms.uuid16(0xFFF2), ["write-without-response"])
    rec.add_notification(0, NOTIFY, status(0))
    rec.add_notification(1, NOTIFY, bytes.fromhex("02 51 02 03 50 03"))
    for second in range(1, 21):
        rec.add_notification(1 + second, NOTIFY, status(3, 30, second, second * 2))
    rec.add_notification(22, NOTIFY, status(1, 0, 20, 40))
    return rec


@pytest.fixture
def clock(monkeypatch):
    fake = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: fake.now))
    monkeypatch.setattr(proprietary, "time", SimpleNamespace(monotonic=lambda: fake.now))
    return fake


def test_a_walk_replayed_and_only_the_query_sent(clock):
    backend = ReplayBackend(a_walk(), time_scale=0, on_packet=lambda t: setattr(clock, "now", 1000.0 + t))
    reader = FitShowTreadmill(ADDRESS, backend=backend)

    async def go():
        await reader.connect()
        client = backend.clients[-1]
        await asyncio.wait_for(client.finished.wait(), 5)
        return client

    client = asyncio.run(go())
    assert reader.status.steps == 40 and reader.status.elapsed_s == 20  # (then the recording ends: link lost)
    assert client.asked and set(client.asked) == {QUERY}


def test_the_query_goes_only_to_a_machine_named_fitshow(clock):
    """0xFFF1/0xFFF2 are on many devices (a UREVO pad's vendor channel among them)."""
    backend = ReplayBackend(a_walk(name="URTM030"), time_scale=0)
    with pytest.raises(TreadmillError, match="not a treadmill this reader is for"):
        asyncio.run(FitShowTreadmill(ADDRESS, backend=backend).connect())
    assert backend.clients == []  # not even connected to
    assert readers.reader_for([NOTIFY], "URTM030") is None
    assert readers.reader_for([NOTIFY, ftms.TREADMILL_DATA], "FS-846344").key == "ftms"  # FTMS when it has it
    assert readers.reader_for([NOTIFY], "FS-846344").key == "fitshow"
    assert isinstance(readers.build("fitshow", ADDRESS), FitShowTreadmill)
