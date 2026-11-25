"""Recordings: ``walka probe --record``, the file, and playing it back.

A recording is posted in public like the probe's report, so it follows the
same rules (shortened address, no serial number, nothing about other
devices). Played back through the real reader, it must come out the way the
live walk did.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

from test_probe import SERIAL, radio, run_probe  # noqa: F401 - radio is a fixture
from walka import ftms, recording
from walka.recording import Recording, RecordingError, ReplayBackend, ReplayClient
from walka.treadmill import ftms_ble
from walka.treadmill.base import KMH_PER_MPH, BeltState, TreadmillEvent
from walka.treadmill.ftms_ble import BleClient, FtmsTreadmill
from walka.treadmill.replay import ReplayTreadmill
from walka_fakes import FakeBackend, FakePad

# ---- the probe records --------------------------------------------------------------------


def test_the_probe_records_every_notification_with_its_time(radio):  # noqa: F811
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    radio.add("22:22:22:22:22:22", "Sam's AirPods", rssi=-40)
    rec = Recording()
    run_probe(recording=rec)
    assert rec.name == "URTM030" and rec.address == "F4:12:FA:..:..:.."
    assert [n.uuid for n in rec.notifications] == [ftms.TREADMILL_DATA]
    assert rec.notifications[0].data == bytes.fromhex("8404600000000000ffffff0000")
    assert rec.notifications[0].t >= 0
    assert ftms.CONTROL_POINT in rec.characteristics()  # the layout, as the report lists it
    assert rec.reads[ftms.SUPPORTED_SPEED_RANGE] == bytes.fromhex("600083021000")
    assert radio.written == []


def test_the_recording_is_as_safe_to_post_as_the_report(radio, monkeypatch, tmp_path):  # noqa: F811
    import bleak

    from test_probe import MODEL_NUMBER, PAD_SERVICES, READS, characteristic, service

    services = [
        service(ftms.DEVICE_INFORMATION_SERVICE, characteristic(MODEL_NUMBER, "read"), characteristic(SERIAL, "read")),
        *PAD_SERVICES[1:],
    ]
    monkeypatch.setattr(bleak.BleakClient, "services", services)
    monkeypatch.setitem(READS, SERIAL, b"SN20260114000731")
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    radio.add("22:22:22:22:22:22", "Sam's AirPods", rssi=-40)
    rec = Recording()
    run_probe(recording=rec)
    text = rec.save(tmp_path / "r.json").read_text("utf-8")
    for private in ("SN20260114000731", b"SN20260114000731".hex(), "AirPods", "22:22:22", "F4:12:FA:00:30:AB"):
        assert private not in text
    assert "URTM030" in text and "F4:12:FA:..:..:.." in text
    # A serial number that arrives as a notification is left out too.
    rec.add_notification(1.0, SERIAL, b"SN1")
    assert all(n.uuid != SERIAL for n in rec.notifications)


def test_a_probe_without_record_fills_nothing(radio):  # noqa: F811
    radio.add("F4:12:FA:00:30:AB", "URTM030", fitness_machine=True)
    run_probe()  # (no recording argument: nothing to fill, nothing breaks)


# ---- the file -----------------------------------------------------------------------------


def sample() -> Recording:
    rec = Recording(name="URTM030", address="F4:12:FA:..:..:..", recorded="2026-11-25", walka="0.9.2")
    rec.add_characteristic(ftms.FTMS_SERVICE, ftms.TREADMILL_DATA, ["notify"])
    rec.add_characteristic(ftms.FTMS_SERVICE, ftms.SUPPORTED_SPEED_RANGE, ["read"])
    rec.add_read(ftms.SUPPORTED_SPEED_RANGE, bytes.fromhex("600083021000"))
    rec.add_notification(0.0, ftms.TREADMILL_DATA, bytes.fromhex("8404600000000000ffffff0000"))
    rec.add_notification(1.0, ftms.TREADMILL_DATA, bytes.fromhex("8404410101000000ffffff0100"))
    return rec


def test_a_recording_survives_the_round_trip(tmp_path):
    path = sample().save(tmp_path / "walk.json")
    back = recording.load(path)
    assert back.to_dict() == sample().to_dict()
    assert back.duration_s == 1.0
    assert recording.recording_path(tmp_path / "walka-probe-1.txt").name == "walka-probe-1.json"


@pytest.mark.parametrize(
    "content,says",
    [
        ("not json", "not JSON"),
        ('{"format": "something else"}', "not a Walka recording"),
        ('{"format": "walka-recording", "version": 99}', "version 99"),
        ('{"format": "walka-recording", "version": 1, "notifications": [[1, "2acd", "zz"]]}', "damaged"),
        (
            '{"format": "walka-recording", "version": 1, "notifications": [[2, "a", "00"], [1, "a", "00"]]}',
            "order",
        ),
    ],
)
def test_a_file_that_is_not_a_recording_is_refused_in_words(tmp_path, content, says):
    path = tmp_path / "x.json"
    path.write_text(content)
    with pytest.raises(RecordingError, match=says):
        recording.load(path)


# ---- playing back -------------------------------------------------------------------------


def test_the_replay_client_is_as_mute_as_the_real_one():
    """Same door as on a real link (``BleClient``): nothing that writes."""
    names = {name for name in vars(ReplayClient) if not name.startswith("_")}
    allowed = {name for name in vars(BleClient) if not name.startswith("_")}
    assert allowed <= names
    assert not {name for name in names - allowed if "write" in name or name.startswith(("set_", "send"))}
    for module in (recording, inspect.getmodule(ReplayTreadmill)):
        source = inspect.getsource(module)
        for word in ("CONTROL_POINT", "write(", "write_gatt"):
            assert word not in source, f"{module.__name__} mentions {word}"


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def record_a_walk(clock: FakeTime) -> tuple[Recording, FtmsTreadmill]:
    """Walk on the emulated pad with the real reader listening, and record
    everything the pad sends, the way the probe would."""
    pad = FakePad(steps_in_data=True)
    rec = Recording(name="URTM030", address="F4:12:FA:..:..:..")
    for uuid, properties in pad.characteristics().items():
        rec.add_characteristic(ftms.FTMS_SERVICE, uuid, properties)
    rec.add_read(ftms.SUPPORTED_SPEED_RANGE, pad.read(ftms.SUPPORTED_SPEED_RANGE))
    start = clock.now
    send = pad._send

    def recording_send(uuid: str, data: bytes) -> None:
        rec.add_notification(clock.now - start, uuid, data)
        send(uuid, data)

    pad._send = recording_send
    live = FtmsTreadmill(backend=FakeBackend(pad))

    def tick(seconds: float) -> None:
        for _ in range(int(seconds * 10)):
            clock.now += 0.1
            pad.advance(0.1)

    async def walk() -> None:
        await live.connect()
        pad.press_start()
        tick(4)
        pad.press_speed(2.0 * KMH_PER_MPH)
        tick(60)
        pad.press_pause()
        tick(3)

    asyncio.run(walk())
    return rec, live


def test_a_walk_played_back_comes_out_as_it_was_heard(monkeypatch, tmp_path):
    clock = FakeTime()
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=clock.monotonic))
    rec, live = record_a_walk(clock)
    assert len(rec.notifications) > 100
    rec = recording.load(rec.save(tmp_path / "walk.json"))  # through the file

    start = clock.now
    played = FtmsTreadmill(
        backend=ReplayBackend(rec, time_scale=0, on_packet=lambda t: setattr(clock, "now", start + t)),
    )
    events: list[TreadmillEvent] = []
    played.on_event = events.append

    async def replay() -> None:
        await played.connect()
        client = played._client
        assert client is not None
        await asyncio.wait_for(client.finished.wait(), 5)

    asyncio.run(replay())
    for field in ("distance_m", "steps", "elapsed_s", "energy_kcal", "min_kmh", "max_kmh", "mph_grid"):
        assert getattr(played.status, field) == getattr(live.status, field), field
    assert played.status.steps and played.status.steps > 50
    assert live.status.state is BeltState.PAUSED
    # The recording ended: the link is lost, as when the pad is switched off.
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]


def test_the_replay_treadmill_counts_as_simulated_and_stores_no_address():
    """Its walks are made up: a Walka account takes none (it checks the
    kind), and no Bluetooth address is pinned to the settings for it."""
    treadmill = ReplayTreadmill(sample(), time_scale=0)
    assert treadmill.kind == "simulated" and treadmill.replay
    assert not hasattr(treadmill, "address")

    async def go() -> None:
        await treadmill.connect()
        assert treadmill.status.connected and "playing back" in treadmill.status.detail
        assert treadmill.status.mph_grid  # read from the recorded speed range
        await treadmill.disconnect()

    asyncio.run(go())


def test_run_replay_is_on_the_command_line(tmp_path):
    from walka import cli

    path = sample().save(tmp_path / "walk.json")
    args = cli.build_parser().parse_args(["run", "--replay", str(path)])
    assert args.replay == str(path)
    assert cli.build_parser().parse_args(["probe", "--record"]).record
    cfg = SimpleNamespace(treadmill=SimpleNamespace(units="auto", kind="ftms"))
    assert isinstance(cli._build_replay(cfg, str(path)), ReplayTreadmill)
    assert json.loads(path.read_text())["format"] == "walka-recording"
