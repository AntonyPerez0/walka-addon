"""The PitPat / SupeRun reader, by replaying recordings put together from the
protocol as public open-source projects describe it (see docs/HARDWARE.md).
None comes from a pad tried with Walka. This reader writes nothing at all."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.recording import Recording, ReplayBackend
from walka.treadmill import ftms_ble, proprietary, readers
from walka.treadmill.base import BeltState
from walka.treadmill.pitpat import NOTIFY, NOTIFY_ESP32, PitPatTreadmill, decode, xor

ADDRESS = "D0:00:00:00:00:01"
FLAGS = {"stopped": 0x00, "running": 0x08, "paused": 0x10, "starting": 0x18}


def frame(*, kmh=0.0, metres=0, kcal=0, ms=0, state="running", steps14=0, steps43=None, n=50, firmware=34, start=0x68):
    f = bytearray(n)
    f[0], f[1], f[-1] = start, n, 0x43
    f[3:5] = round(kmh * 1000).to_bytes(2, "big")
    f[7:11] = metres.to_bytes(4, "big")
    f[14:18] = steps14.to_bytes(4, "big")
    f[18:20] = kcal.to_bytes(2, "big")
    f[20:24] = ms.to_bytes(4, "big")
    f[25] = firmware
    f[26] = FLAGS[state]
    if steps43 is not None:
        f[43:45] = steps43.to_bytes(2, "big")
    f[-2] = xor(f[1:-2])
    return bytes(f)


def test_a_status_frame():
    r = decode(frame(kmh=4.0, metres=120, kcal=9, ms=95_000, steps43=170))
    assert r.state is BeltState.RUNNING and r.speed_kmh == 4.0
    assert (r.distance_m, r.energy_kcal, r.elapsed_s, r.steps) == (120.0, 9.0, 95, 170)


def test_steps_and_time_where_the_firmware_puts_them():
    assert decode(frame(steps14=33, steps43=170)).steps == 33  # the maker's app's place, when it is used
    assert decode(frame(n=52, steps43=None)).steps is None  # a frame with the serial number there: keep the last
    assert decode(frame(ms=95, firmware=12)).elapsed_s == 95  # old firmware counts seconds


def test_the_esp32_transport_is_unwrapped():
    inner = frame(kmh=2.5, steps43=10)
    wrapped = bytes([0x4D, 0x04, 0x07, len(inner)]) + inner
    assert decode(wrapped).speed_kmh == 2.5


@pytest.mark.parametrize("damage", ["xor", "end", "length", "start", "short"])
def test_damaged_frames_are_ignored(damage):
    f = bytearray(frame(kmh=3.0))
    if damage == "xor":
        f[-2] ^= 1
    elif damage == "end":
        f[-1] = 0x00
    elif damage == "length":
        f[1] = 49
    elif damage == "start":
        f[0] = 0x6A  # (what an app sends the pad, not what it says)
    else:
        f = f[:30]
    assert decode(bytes(f)) is None


def a_walk(notify=NOTIFY) -> Recording:
    rec = Recording(name="PitPat-T01", address=ADDRESS)
    service = ftms.uuid16(0xFBA0) if notify == NOTIFY else ftms.uuid16(0xFFFF)
    rec.add_characteristic(service, notify, ["read", "notify"])
    t = 0.0
    rec.add_notification(t, notify, frame(state="stopped", n=52))
    rec.add_notification(t + 1, notify, frame(state="starting"))
    for second in range(1, 21):
        rec.add_notification(t + 1 + second, notify, frame(kmh=3.0, metres=second, ms=second * 1000, steps43=second * 2))
    rec.add_notification(t + 23, notify, frame(state="paused", metres=20, ms=20_000, steps43=40))
    return rec


@pytest.fixture
def clock(monkeypatch):
    fake = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: fake.now))
    monkeypatch.setattr(proprietary, "time", SimpleNamespace(monotonic=lambda: fake.now))
    return fake


@pytest.mark.parametrize("notify", [NOTIFY, NOTIFY_ESP32])
def test_a_walk_replayed_and_nothing_written(clock, notify):
    backend = ReplayBackend(a_walk(notify), time_scale=0, on_packet=lambda t: setattr(clock, "now", 1000.0 + t))
    reader = PitPatTreadmill(ADDRESS, backend=backend)
    states = []

    async def go():
        await reader.connect()
        client = backend.clients[-1]
        original = reader._on_vendor

        def watch(data):
            original(data)
            states.append(reader.status.state)

        client._callbacks[notify] = watch
        await asyncio.wait_for(client.finished.wait(), 5)
        return client

    client = asyncio.run(go())
    assert states[0] is BeltState.STOPPED and states[1] is BeltState.STARTING and states[-1] is BeltState.PAUSED
    assert reader.status.steps == 40 and reader.status.distance_m == 20.0 and reader.status.elapsed_s == 20
    assert not hasattr(client, "asked"), "the plain door: it cannot even ask"
    assert reader.asked == 0


def test_no_query_is_in_the_table_for_it_and_it_never_asks():
    assert "pitpat" not in proprietary.QUERIES
    assert PitPatTreadmill.NEEDS_QUERY is False
    backend = SimpleNamespace(client=lambda device, cb: "plain", query_client=lambda *a: "query")
    assert PitPatTreadmill()._make_client(backend, None, lambda: None) == "plain"


def test_registry_and_names():
    from walka.treadmill.known import known_treadmill

    assert readers.reader_for([NOTIFY]).key == "pitpat"
    assert isinstance(readers.build("pitpat", ADDRESS), PitPatTreadmill)
    assert known_treadmill("PITPAT-T01").protocol == "pitpat"
