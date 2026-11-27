"""The WalkingPad (KingSmith "WiLink") reader, by replaying recordings.

The recordings are put together from the protocol as public open-source
projects describe it (ph4r05/ph4-walkingpad, MIT, and others: see
docs/HARDWARE.md); the first packet is one of ph4-walkingpad's captured
samples. None comes from a pad tried with Walka.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.recording import Recording, ReplayBackend
from walka.treadmill import ftms_ble, proprietary, readers
from walka.treadmill.base import BeltState, TreadmillEvent
from walka.treadmill.replay import ReplayTreadmill
from walka.treadmill.walkingpad import NOTIFY, WalkingPadTreadmill, checksum, decode

ADDRESS = "E8:00:00:00:00:01"
QUERY = bytes.fromhex("F7A20000A2FD")
SAMPLE = bytes.fromhex("f8a2013c0100022a00004f0003d1b4000000e3fd")  # from ph4-walkingpad's README


def status(state: int, speed_tenths: int, seconds: int, tens_of_m: int, steps: int) -> bytes:
    body = bytes([0xF8, 0xA2, state, speed_tenths, 1]) + seconds.to_bytes(3, "big") + tens_of_m.to_bytes(3, "big")
    body += steps.to_bytes(3, "big") + bytes([speed_tenths * 3, 0, 0, 0])
    packet = body + b"\x00\xfd"
    return body + bytes([checksum(packet)]) + b"\xfd"


def test_a_captured_sample_decodes():
    reading = decode(SAMPLE)
    assert reading.state is BeltState.RUNNING and reading.speed_kmh == 6.0
    assert (reading.elapsed_s, reading.distance_m, reading.steps) == (554, 790.0, 977)


@pytest.mark.parametrize(
    "packet",
    [
        SAMPLE[:-2] + bytes([SAMPLE[-2] ^ 1]) + b"\xfd",  # checksum wrong
        b"\xf8\xa7" + SAMPLE[2:],  # "last record", not the status
        SAMPLE[:19],  # cut short
        b"",
    ],
)
def test_what_is_not_a_good_status_is_ignored(packet):
    assert decode(packet) is None


def a_walk() -> Recording:
    rec = Recording(name="WalkingPad", address=ADDRESS)
    rec.add_characteristic(ftms.uuid16(0xFE00), NOTIFY, ["notify"])
    rec.add_characteristic(ftms.uuid16(0xFE00), ftms.uuid16(0xFE02), ["write-without-response", "write"])
    t = 0.0
    rec.add_notification(t, NOTIFY, status(5, 0, 0, 0, 0))  # standby
    for countdown in (9, 8, 7):
        t += 1
        rec.add_notification(t, NOTIFY, status(countdown, 0, 0, 0, 0))
    for second in range(1, 31):
        t += 1
        rec.add_notification(t, NOTIFY, status(1, 30, second, second // 12, second * 2))
    t += 1
    rec.add_notification(t, NOTIFY, b"\xf8\xa7" + bytes(17) + b"\xfd")  # a "last record": ignored
    t += 1
    rec.add_notification(t, NOTIFY, status(0, 0, 30, 2, 60))
    return rec


@pytest.fixture
def clock(monkeypatch):
    fake = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: fake.now))
    monkeypatch.setattr(proprietary, "time", SimpleNamespace(monotonic=lambda: fake.now))
    return fake


def test_a_walk_replayed(clock):
    seen: list[tuple] = []
    backend = ReplayBackend(a_walk(), time_scale=0, on_packet=lambda t: setattr(clock, "now", 1000.0 + t))
    reader = WalkingPadTreadmill(ADDRESS, backend=backend)
    events: list[TreadmillEvent] = []
    reader.on_event = events.append

    async def go():
        await reader.connect()
        client = backend.clients[-1]
        original = reader._on_vendor

        def watch(data):
            original(data)
            seen.append(reader.status.state)

        client._callbacks[NOTIFY] = watch
        await asyncio.wait_for(client.finished.wait(), 5)
        return client

    client = asyncio.run(go())
    assert seen[0] is BeltState.STOPPED and seen[1] is BeltState.STARTING and BeltState.RUNNING in seen
    assert seen[-1] is BeltState.STOPPED
    assert reader.status.steps == 60 and reader.status.distance_m == 20.0 and reader.status.elapsed_s == 30
    assert reader.status.speed_kmh == 0.0
    # What was sent to the pad: its one query, and nothing else.
    assert client.asked and set(client.asked) == {QUERY}
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]


def test_the_registry_and_a_replay_pick_the_walkingpad_reader():
    assert readers.reader_for([ftms.uuid16(0xFE01)], "WalkingPad").key == "walkingpad"
    # Only a device named as a KingSmith pad: it is sent the query.
    assert readers.reader_for([ftms.uuid16(0xFE01)]) is None
    # A pad with FTMS too is read the standard way.
    assert readers.reader_for([ftms.uuid16(0xFE01), ftms.TREADMILL_DATA], "WalkingPad").key == "ftms"
    assert isinstance(readers.build("walkingpad", ADDRESS), WalkingPadTreadmill)
    assert isinstance(ReplayTreadmill(a_walk(), time_scale=0)._reader, WalkingPadTreadmill)


def test_without_an_address_it_is_found_by_name_and_never_settles_for_another_machine():
    from walka.treadmill.known import known_treadmill

    assert known_treadmill("WalkingPad").protocol == "walkingpad"
    assert known_treadmill("KS-ST-A1P").protocol == "walkingpad"
    assert "WalkingPad" in WalkingPadTreadmill.NAME_PREFIXES

    class Backend:
        async def discover(self, timeout):
            return [ftms_ble.FoundDevice("URTM030", "AA", -40, True)]

    reader = WalkingPadTreadmill(backend=Backend())
    with pytest.raises(Exception, match="not found"):
        asyncio.run(reader.connect())
