"""The Bluetooth driver against an emulated UREVO pad (GATT level)."""

from __future__ import annotations

import asyncio
import math
from time import perf_counter
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.treadmill import ftms_ble, known
from walka.treadmill.base import (
    KMH_PER_MPH,
    BeltState,
    TreadmillError,
    TreadmillEvent,
    kmh_to_mph_tenths,
    mph_tenths_to_command_kmh,
)
from walka.treadmill.ftms_ble import OWN_EVENT_WINDOW_S, FtmsTreadmill, is_treadmill_candidate
from walka_fakes import FakeBackend, FakePad, mph_tenths_to_centi_kmh, us_firmware_mph_tenths

REQUEST_CONTROL, START, STOP, PAUSE = b"\x00", b"\x07", b"\x08\x01", b"\x08\x02"


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> FakeTime:
    """The driver's notion of time (its own-command windows, data age)."""
    fake = FakeTime()
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


def rig(pad: FakePad | None = None, *pads: FakePad, **driver_kwargs):
    pad = pad or FakePad()
    backend = FakeBackend(pad, *pads)
    driver_kwargs.setdefault("command_timeout_s", 0.05)
    driver = FtmsTreadmill(backend=backend, **driver_kwargs)
    events: list[TreadmillEvent] = []
    driver.on_event = events.append
    return driver, pad, backend, events


def run(coro):
    return asyncio.run(coro)


async def running(driver: FtmsTreadmill, pad: FakePad, clock: FakeTime | None = None) -> None:
    """Connect, start and get the belt past its countdown."""
    await driver.connect()
    await driver.start()
    for _ in range(40):
        pad.advance(0.1)
    assert pad.state == "running"
    if clock is not None:
        clock.now += OWN_EVENT_WINDOW_S + 1


def speed_writes(pad: FakePad) -> list[int]:
    return [int.from_bytes(w[1:3], "little") for w in pad.writes if w[:1] == b"\x02"]


# ---- the firmware arithmetic ------------------------------------------------------


def test_command_value_survives_the_firmwares_double_truncation():
    """For every 0.1 mph step, what we send must land exactly on it - checked
    against an integer model of the firmware, not against the driver's own maths."""
    for tenths in range(1, 126):  # 0.1 .. 12.5 mph
        kmh = mph_tenths_to_command_kmh(tenths)
        raw = int.from_bytes(ftms.cmd_set_target_speed(kmh)[1:], "little")
        assert raw % 10 == 0, "the command must already be on the firmware's 0.1 km/h grid"
        assert us_firmware_mph_tenths(raw) == tenths, f"{tenths / 10} mph: sent {kmh} km/h"
        # It is also the smallest value that works (never more belt than asked for).
        assert us_firmware_mph_tenths(raw - 10) == tenths - 1
        assert raw / 100 - tenths * KMH_PER_MPH / 10 < 0.1


def test_naive_rounding_would_miss_which_is_why_the_workaround_exists():
    misses = [
        t for t in range(6, 41) if us_firmware_mph_tenths(int(round(t * KMH_PER_MPH / 10 * 100))) != t
    ]  # sending round(km/h, 2)
    assert 20 in misses  # the documented example: 3.22 km/h arrives as 1.9 mph
    assert len(misses) >= 30  # in fact nearly every step is missed


def test_kmh_to_mph_tenths():
    for tenths in range(0, 126):
        exact = tenths * KMH_PER_MPH / 10
        assert kmh_to_mph_tenths(exact) == tenths
        # ... and from the truncated km/h value a pad reports for that speed.
        assert kmh_to_mph_tenths(mph_tenths_to_centi_kmh(tenths) / 100) == tenths
    assert kmh_to_mph_tenths(0.96) == 6 and kmh_to_mph_tenths(6.43) == 40 and kmh_to_mph_tenths(3.21) == 20
    assert kmh_to_mph_tenths(3.3) == 21  # nearest step: 2.05 mph rounds to 2.1 (ties to even are fine)
    assert kmh_to_mph_tenths(0.0) == 0


# ---- connecting ------------------------------------------------------------------------


def test_connect_discovers_the_us_pad_and_its_exact_mph_grid():
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()

    run(scenario())
    status = driver.status
    assert status.connected and status.name == "URTM030" and status.address == pad.address
    assert driver.raw_speed_range == ftms.SpeedRange(0.96, 6.43, 0.16)  # what the pad says ...
    assert status.mph_grid  # ... is recognised as 0.1 mph steps
    assert status.min_kmh == pytest.approx(0.6 * KMH_PER_MPH, abs=1e-9)
    assert status.max_kmh == pytest.approx(4.0 * KMH_PER_MPH, abs=1e-9)
    assert status.step_kmh == pytest.approx(0.1 * KMH_PER_MPH, abs=1e-12)
    assert status.state is BeltState.UNKNOWN and status.speed_kmh == 0
    assert driver.features is not None and driver.features.speed_target_supported
    assert events == [TreadmillEvent.CONNECTED]
    # Connecting must not touch the belt or even ask for control.
    assert pad.writes == []
    assert set(pad.subscriptions) == {
        ftms.CONTROL_POINT,
        ftms.TREADMILL_DATA,
        ftms.FITNESS_MACHINE_STATUS,
        ftms.TRAINING_STATUS,
    }


def test_connect_is_idempotent():
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.connect()
        await driver.connect()

    run(scenario())
    assert backend.finds == 1 and pad.connects == 1
    assert events == [TreadmillEvent.CONNECTED]


def test_unfolded_pad_with_the_6_2_mph_range():
    driver, pad, *_ = rig(FakePad(max_tenths=62))

    async def scenario():
        await driver.connect()

    run(scenario())
    assert driver.raw_speed_range == ftms.SpeedRange(0.96, 9.97, 0.16)
    assert driver.status.mph_grid
    assert driver.status.max_kmh == pytest.approx(6.2 * KMH_PER_MPH, abs=1e-9)


def test_kmh_firmware_pad():
    driver, pad, *_ = rig(FakePad(us_firmware=False, kmh_range=(100, 600, 10)))

    async def scenario():
        await driver.connect()

    run(scenario())
    status = driver.status
    assert not status.mph_grid
    assert (status.min_kmh, status.max_kmh, status.step_kmh) == (1.0, 6.0, 0.1)


@pytest.mark.parametrize(
    "units,pad_kwargs,expected",
    [
        ("mph", dict(us_firmware=False, kmh_range=(100, 600, 10)), (True, 0.6 * KMH_PER_MPH, 3.7 * KMH_PER_MPH)),
        ("kmh", dict(), (False, 0.96, 6.43)),
        ("auto", dict(has_speed_range=False), (False, 1.0, 6.0)),
        ("mph", dict(has_speed_range=False), (True, 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH)),
        ("kmh", dict(has_speed_range=False), (False, 1.0, 6.0)),
    ],
)
def test_units_setting_and_missing_speed_range(units, pad_kwargs, expected):
    driver, pad, *_ = rig(FakePad(**pad_kwargs), units=units)

    async def scenario():
        await driver.connect()

    run(scenario())
    mph, lo, hi = expected
    assert driver.status.mph_grid is mph
    assert driver.status.min_kmh == pytest.approx(lo, abs=1e-9)
    assert driver.status.max_kmh == pytest.approx(hi, abs=1e-9)


@pytest.mark.parametrize("raw", [b"", b"\x00\x00", bytes.fromhex("000083021000"), bytes.fromhex("830260001000")])
def test_unusable_speed_range_falls_back_to_safe_defaults(raw: bytes):
    pad = FakePad()
    pad.speed_range_override = raw
    driver, *_ = rig(pad)

    async def scenario():
        await driver.connect()

    run(scenario())
    status = driver.status
    assert 0.9 <= status.min_kmh <= 1.0
    assert status.max_kmh <= 4.0 * KMH_PER_MPH + 1e-9
    assert status.step_kmh > 0


def test_connects_by_address_when_given():
    first, second = FakePad(address="AA:AA:AA:AA:AA:01"), FakePad(address="AA:AA:AA:AA:AA:02", name="URTM059")
    driver, _, backend, _ = rig(first, second, address="aa:aa:aa:aa:aa:02")

    async def scenario():
        await driver.connect()

    run(scenario())
    assert second.client is not None and first.client is None
    assert driver.status.name == "URTM059"


def test_device_not_found_leaves_the_driver_reusable():
    driver, pad, backend, events = rig()
    pad.powered = False

    async def scenario():
        with pytest.raises(TreadmillError, match="not found"):
            await driver.connect()
        assert not driver.status.connected and events == []
        assert "phone app" in driver.status.detail  # the hint that matters for UREVO pads
        with pytest.raises(TreadmillError):
            await driver.start()
        # The phone app holds the pad's single connection: same symptom.
        pad.powered, pad.busy = True, True
        with pytest.raises(TreadmillError, match="not found"):
            await driver.connect()
        pad.busy = False
        await driver.connect()
        await driver.start()

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]
    assert pad.state == "countdown"


def test_device_without_ftms_is_refused_and_released():
    driver, pad, backend, events = rig(FakePad(has_ftms=False))

    async def scenario():
        with pytest.raises(TreadmillError, match="FTMS"):
            await driver.connect()
        assert pad.client is None  # we let go of it
        assert not driver.status.connected and events == []
        pad.has_ftms = True
        await driver.connect()

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]


def test_unrelated_devices_are_not_connected_to():
    driver, pad, backend, events = rig(FakePad(name="Living Room TV", advertises_ftms=False))

    async def scenario():
        with pytest.raises(TreadmillError, match="not found"):
            await driver.connect()

    run(scenario())
    assert pad.connects == 0


def test_scan_and_connect_errors_become_treadmill_errors():
    driver, pad, backend, events = rig()

    async def scenario():
        backend.scan_error = OSError("Bluetooth adapter is off")
        with pytest.raises(TreadmillError, match="adapter is off"):
            await driver.connect()
        backend.scan_error = None

        client_connect = backend.client

        def failing_client(device, on_disconnect):
            client = client_connect(device, on_disconnect)

            async def boom():
                raise OSError("connection attempt timed out")

            client.connect = boom
            return client

        backend.client = failing_client
        with pytest.raises(TreadmillError, match="timed out"):
            await driver.connect()
        assert not driver.status.connected

        def half_working_client(device, on_disconnect):
            client = client_connect(device, on_disconnect)
            client.subscribe_errors.add(ftms.CONTROL_POINT)
            return client

        backend.client = half_working_client
        with pytest.raises(TreadmillError, match="subscribe failed"):
            await driver.connect()
        assert pad.client is None and not driver.status.connected

        backend.client = client_connect
        await driver.connect()

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]


def test_discover_lists_treadmill_candidates_only():
    urevo = FakePad()
    generic = FakePad(name="Treadmill X", address="11:22:33:44:55:66")
    tv = FakePad(name="TV", address="99:88:77:66:55:44", advertises_ftms=False)
    silent_name = FakePad(name="urtm054", address="12:12:12:12:12:12", advertises_ftms=False)
    driver, *_ = rig(urevo, generic, tv, silent_name)

    async def scenario():
        return await driver.discover(1.0)

    found = run(scenario())
    assert {d.name for d in found} == {"URTM030", "Treadmill X", "urtm054"}
    assert all(d.treadmill is None for d in found)  # none of them says what kind of machine it is
    assert found[0].to_dict() == {"name": "URTM030", "address": urevo.address, "rssi": -60, "ftms": True}


def test_is_treadmill_candidate():
    assert is_treadmill_candidate("URTM030", [], ("URTM",))
    assert is_treadmill_candidate("urtm030", [], ("URTM",))
    assert is_treadmill_candidate(None, [ftms.FTMS_SERVICE.upper()], ("URTM",))
    assert is_treadmill_candidate("Whatever", [ftms.FTMS_SERVICE], ())
    assert not is_treadmill_candidate("Whatever", [ftms.HEART_RATE_SERVICE], ("URTM",))
    assert not is_treadmill_candidate(None, [], ("URTM",))
    assert not is_treadmill_candidate("URTM030", [], ("",))  # an empty prefix matches nothing
    assert not is_treadmill_candidate("MY-URTM", [], ("URTM",))


# ---- the command sequence ----------------------------------------------------------------


def test_start_sequence_request_control_then_start_then_speed(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.start()
        assert pad.writes == [REQUEST_CONTROL, START]
        assert driver.status.state is BeltState.STARTING
        assert pad.state == "countdown"
        # During the 3-2-1 countdown the belt reports zero.
        for _ in range(29):
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.STARTING
        for _ in range(11):
            pad.advance(0.1)
        # It comes up at its minimum speed: 0.6 mph, reported as 0.96 km/h.
        assert driver.status.state is BeltState.RUNNING
        assert driver.status.speed_kmh == pytest.approx(0.96)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        assert pad.writes[2] == bytes.fromhex("024a01")  # 3.3 km/h on the wire for 2.0 mph
        assert pad.target_tenths == 20
        assert driver.status.target_kmh == pytest.approx(2.0 * KMH_PER_MPH)
        for _ in range(60):
            pad.advance(0.1)
        assert driver.status.speed_kmh == pytest.approx(3.21)  # truncated km/h of 2.0 mph
        await driver.pause()
        await driver.start()
        await driver.stop()

    run(scenario())
    # Control is requested once per connection, not once per command.
    assert pad.writes.count(REQUEST_CONTROL) == 1
    assert pad.writes == [REQUEST_CONTROL, START, bytes.fromhex("024a01"), PAUSE, START, STOP]
    assert all(pad.write_modes)  # write-with-response when the characteristic offers it
    assert driver.status.state is BeltState.STOPPED and driver.status.target_kmh is None


@pytest.mark.parametrize("max_tenths", [40, 62])
def test_every_mph_step_lands_exactly_on_the_emulated_firmware(max_tenths: int):
    driver, pad, *_ = rig(FakePad(max_tenths=max_tenths))

    async def scenario():
        await running(driver, pad)
        top = max_tenths * KMH_PER_MPH / 10
        for tenths in list(range(6, max_tenths + 1)) + list(range(max_tenths, 5, -1)):
            exact = tenths * KMH_PER_MPH / 10
            # Exactly on the step, and a little either side of it (but nearer to it than to a neighbour).
            for asked in (exact, exact - 0.07, exact + 0.07, round(exact, 2), math.floor(exact * 100) / 100):
                asked = max(0.6 * KMH_PER_MPH, min(top, asked))
                await driver.set_speed(asked)
                assert pad.target_tenths == tenths, f"asked {asked:.4f} km/h, wanted {tenths / 10} mph"
                assert driver.status.target_kmh == pytest.approx(exact, abs=1e-9)
                assert driver.quantise(asked)[0] == pytest.approx(exact, abs=1e-9)
        assert all(raw % 10 == 0 for raw in speed_writes(pad))

    run(scenario())


def test_requests_outside_the_range_are_clamped_not_sent_raw():
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        for asked, tenths in ((0.0, 6), (-5.0, 6), (0.3, 6), (40.95, 40), (1e9, 40), (6.5, 40)):
            await driver.set_speed(asked)
            assert pad.target_tenths == tenths
        assert max(speed_writes(pad)) == 650  # 6.5 km/h: what 4.0 mph needs, nothing higher
        assert min(speed_writes(pad)) == 100

    run(scenario())


def test_kmh_firmware_quantises_to_the_pads_step():
    driver, pad, *_ = rig(FakePad(us_firmware=False, kmh_range=(100, 600, 10)))

    async def scenario():
        await running(driver, pad)
        for asked, centi in ((3.14, 310), (3.16, 320), (3.0, 300), (0.2, 100), (99.0, 600), (5.96, 600), (1.04, 100)):
            await driver.set_speed(asked)
            assert pad.target_centi == centi, asked
            assert speed_writes(pad)[-1] == centi  # what we sent is what the belt does
            assert driver.status.target_kmh == pytest.approx(centi / 100)
        assert driver.quantise(3.14) == (pytest.approx(3.1), pytest.approx(3.1))

    run(scenario())


def test_coarser_kmh_step():
    driver, pad, *_ = rig(FakePad(us_firmware=False, kmh_range=(50, 800, 50)))

    async def scenario():
        await driver.connect()
        assert driver.status.step_kmh == 0.5
        for asked, expected in ((3.2, 3.0), (3.3, 3.5), (0.1, 0.5), (8.4, 8.0), (20.0, 8.0)):
            assert driver.quantise(asked) == (pytest.approx(expected), pytest.approx(expected))

    run(scenario())


def test_speed_writes_are_ignored_by_a_stopped_belt():
    """The pad ignores Set Target Speed until the belt moves - which is why the
    governor waits for movement before its first speed command."""
    driver, pad, *_ = rig()

    async def scenario():
        await driver.connect()
        await driver.set_speed(3.0)
        assert pad.state == "idle" and pad.target_centi is None
        await driver.start()
        await driver.set_speed(3.0)  # still counting down
        assert pad.target_tenths == 6
        for _ in range(40):
            pad.advance(0.1)
        assert driver.status.speed_kmh == pytest.approx(0.96)

    run(scenario())


def test_inline_indications_are_not_missed():
    """Some stacks deliver the indication before the write call returns."""
    driver, pad, *_ = rig(FakePad(inline_indications=True), command_timeout_s=5.0)

    async def scenario():
        await running(driver, pad)
        await driver.set_speed(3.0)
        await driver.stop()

    run(asyncio.wait_for(scenario(), 2.0))
    assert pad.writes[-1] == STOP and pad.state == "idle"


def test_write_without_response_only_control_point():
    driver, pad, *_ = rig(FakePad(write_without_response_only=True))

    async def scenario():
        await running(driver, pad)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        await driver.stop()

    run(scenario())
    assert pad.write_modes and not any(pad.write_modes)
    assert pad.writes == [REQUEST_CONTROL, START, bytes.fromhex("024a01"), STOP]


# ---- control permission ----------------------------------------------------------------------


def test_control_not_permitted_requests_control_and_retries_once(clock):
    driver, pad, *_ = rig(FakePad(require_control=True))

    async def scenario():
        await running(driver, pad, clock)
        assert pad.writes == [REQUEST_CONTROL, START]
        # The pad silently drops our control (e.g. someone used the remote).
        pad.has_control = False
        await driver.set_speed(2.0 * KMH_PER_MPH)
        assert pad.writes[2:] == [bytes.fromhex("024a01"), REQUEST_CONTROL, bytes.fromhex("024a01")]
        assert pad.target_tenths == 20
        await driver.set_speed(2.5 * KMH_PER_MPH)
        assert len(pad.writes) == 6  # no further control requests

    run(scenario())


def test_control_permission_lost_status_triggers_a_new_request(clock):
    driver, pad, *_ = rig(FakePad(require_control=True))

    async def scenario():
        await running(driver, pad, clock)
        pad.take_control_away()  # status 0xFF
        await driver.set_speed(2.0 * KMH_PER_MPH)
        assert pad.writes[2:] == [REQUEST_CONTROL, bytes.fromhex("024a01")]
        assert pad.target_tenths == 20

    run(scenario())


def test_control_still_refused_after_retry_is_an_error():
    driver, pad, *_ = rig(FakePad(require_control=True))

    async def scenario():
        await running(driver, pad)
        pad.reject[0x02] = 0x05  # keeps saying "control not permitted"
        with pytest.raises(TreadmillError, match="CONTROL_NOT_PERMITTED"):
            await driver.set_speed(3.0)
        # Tried, re-requested control, tried once more - and stopped there.
        assert [w[:1] for w in pad.writes[2:]] == [b"\x02", b"\x00", b"\x02"]

    run(scenario())


def test_refused_request_control_is_tolerated():
    """Some pads answer Request Control with a failure and then obey every
    command anyway (the vendor apps ignore the answer). So a refusal is not
    fatal: the command is still sent, and only "control not permitted" on
    the command itself makes the driver ask again."""
    driver, pad, *_ = rig()
    pad.reject[0x00] = 0x04

    async def scenario():
        await driver.connect()
        await driver.start()
        assert pad.writes == [REQUEST_CONTROL, START]
        assert pad.state == "countdown"

    run(scenario())


# ---- timeouts and rejections -------------------------------------------------------------------


@pytest.mark.parametrize("command", ["start", "set_speed", "pause", "stop"])
def test_a_command_that_times_out_is_not_retried(command: str):
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        before = len(pad.writes)
        pad.lose_writes = True  # written, never answered
        if command == "set_speed":
            await driver.set_speed(3.0)
        else:
            await getattr(driver, command)()
        assert len(pad.writes) == before + 1, "an unanswered command must be sent exactly once"

    run(scenario())


def test_silent_pad_that_never_indicates_is_still_driven():
    driver, pad, *_ = rig(FakePad(silent=True))

    async def scenario():
        await running(driver, pad)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        await driver.set_speed(2.5 * KMH_PER_MPH)
        assert pad.target_tenths == 25
        await driver.stop()

    run(scenario())
    assert pad.state == "idle"
    # One control request (unanswered, not repeated), then each command once.
    assert pad.writes == [REQUEST_CONTROL, START, bytes.fromhex("024a01"), bytes.fromhex("029a01"), STOP]


@pytest.mark.parametrize(
    "op,result,name",
    [(0x02, 0x03, "INVALID_PARAMETER"), (0x02, 0x04, "OPERATION_FAILED"), (0x02, 0x02, "OP_CODE_NOT_SUPPORTED")],
)
def test_rejected_speed_command_raises(op: int, result: int, name: str):
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        pad.reject[op] = result
        with pytest.raises(TreadmillError, match=name):
            await driver.set_speed(3.0 * KMH_PER_MPH)
        assert pad.target_tenths == 20
        assert driver.status.target_kmh == pytest.approx(2.0 * KMH_PER_MPH)  # not updated by a failed command
        assert pad.writes.count(bytes.fromhex("02ea01")) == 1  # 4.9 km/h, sent once: no retry after a rejection

    run(scenario())


def test_rejected_start_and_stop_raise_and_do_not_fake_the_state():
    driver, pad, *_ = rig()

    async def scenario():
        await driver.connect()
        pad.reject[0x07] = 0x04
        with pytest.raises(TreadmillError, match="OPERATION_FAILED"):
            await driver.start()
        assert driver.status.state is not BeltState.STARTING
        del pad.reject[0x07]
        await running(driver, pad)
        pad.reject[0x08] = 0x04
        with pytest.raises(TreadmillError):
            await driver.stop()
        with pytest.raises(TreadmillError):
            await driver.pause()
        assert driver.status.state is BeltState.RUNNING  # it is still running and we say so

    run(scenario())


def test_write_failure_is_a_treadmill_error():
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        pad.fail_writes = True
        with pytest.raises(TreadmillError, match="write failed"):
            await driver.stop()
        pad.fail_writes = False
        await driver.stop()
        assert pad.state == "idle"

    run(scenario())


def test_commands_need_a_connection():
    driver, *_ = rig()

    async def scenario():
        for call in (driver.start(), driver.stop(), driver.pause(), driver.set_speed(3.0)):
            with pytest.raises(TreadmillError, match="not connected"):
                await call

    run(scenario())


def test_commands_are_serialised():
    """Commands issued together are written one after the other, each
    waiting for its own answer. (A pause or stop is the exception: it goes
    first - see test_ftms_ble_hardening.py.)"""
    driver, pad, *_ = rig(command_timeout_s=1.0)

    async def scenario():
        await running(driver, pad)
        await asyncio.gather(
            driver.set_speed(2.0 * KMH_PER_MPH),
            driver.set_speed(3.0 * KMH_PER_MPH),
            driver.set_speed(2.5 * KMH_PER_MPH),
        )
        await driver.pause()

    run(scenario())
    assert pad.writes[2:] == [bytes.fromhex("024a01"), bytes.fromhex("02ea01"), bytes.fromhex("029a01"), PAUSE]
    assert pad.state == "paused"


# ---- who pressed the button? -------------------------------------------------------------------


def test_our_own_start_and_stop_are_not_reported_as_external(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.start()  # the pad announces "started by user" for this too
        for _ in range(40):
            pad.advance(0.1)
        await driver.set_speed(3.0)
        await driver.pause()
        clock.now += 1
        await driver.start()
        clock.now += 1
        await driver.stop()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED]
    assert driver.status.state is BeltState.STOPPED


def test_buttons_on_the_pad_are_reported(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        events.clear()

        pad.press_stop()
        assert events == [TreadmillEvent.EXTERNAL_STOP]
        assert driver.status.state is BeltState.STOPPED and driver.status.target_kmh is None

        pad.press_start()
        assert events[-1] is TreadmillEvent.EXTERNAL_START
        assert driver.status.state is BeltState.STARTING
        for _ in range(40):
            pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING

        pad.press_pause()
        assert events[-1] is TreadmillEvent.EXTERNAL_STOP
        assert driver.status.state is BeltState.PAUSED

        pad.press_start()
        pad.pull_safety_key()
        assert events[-2:] == [TreadmillEvent.EXTERNAL_START, TreadmillEvent.EXTERNAL_STOP]
        assert driver.status.state is BeltState.STOPPED

    run(scenario())
    assert len(events) == 5


def test_a_stop_at_the_pad_right_after_our_start_is_still_reported(clock):
    """Our start opens a window in which *start* events are ours - a stop
    pressed in that window is somebody saying no, and must get through."""
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.start()
        clock.now += 1.0
        pad.press_stop()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_STOP]


def test_a_start_at_the_pad_after_our_stop_is_reported(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        await driver.stop()
        clock.now += 1.0
        pad.press_start()

    run(scenario())
    assert events[-1] is TreadmillEvent.EXTERNAL_START


def test_target_speed_changed_at_the_pad_is_tracked_without_events(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        events.clear()
        pad._set_target(500)  # the user presses "+" on the remote
        pad._status(0x05, *pad.target_centi.to_bytes(2, "little"))
        assert driver.status.target_kmh == pytest.approx(pad.target_centi / 100)

    run(scenario())
    assert events == []


# ---- data ------------------------------------------------------------------------------------------


def test_data_notifications_update_the_status(clock):
    driver, pad, *_ = rig(FakePad(steps_in_data=True))

    async def scenario():
        await running(driver, pad, clock)
        await driver.set_speed(3.0 * KMH_PER_MPH)
        for _ in range(600):
            pad.advance(0.1)
        clock.now += 5
        pad.advance(0.1)

    run(scenario())
    status = driver.status
    assert status.state is BeltState.RUNNING
    assert status.speed_kmh == pytest.approx(4.82)  # 3.0 mph, truncated km/h
    assert status.distance_m == int(pad.distance_m) and status.distance_m > 60
    assert status.energy_kcal == int(pad.kcal)
    assert status.steps == int(pad.steps) and status.steps > 100
    assert status.elapsed_s == int(pad.elapsed_s)
    assert status.updated_at == clock.now and status.age_s(clock.now + 2.5) == pytest.approx(2.5)
    data = status.to_dict()
    assert data["state"] == "running" and "updated_at" not in data


def test_belt_coasting_down_after_a_stop_is_not_reported_as_running(clock):
    """After stop/pause the pad still sends a few packets with speed > 0."""
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad, clock)
        await driver.set_speed(3.0 * KMH_PER_MPH)
        for _ in range(60):
            pad.advance(0.1)
        await driver.pause()
        pad.advance(0.1)
        assert driver.status.speed_kmh > 1.0  # still coasting ...
        assert driver.status.state is BeltState.PAUSED  # ... but paused, not running
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.PAUSED

        await driver.start()
        for _ in range(60):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING
        await driver.stop()
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.STOPPED

        # The same when the stop comes from the pad's own button.
        clock.now += 20
        await driver.start()
        for _ in range(60):
            clock.now += 0.1
            pad.advance(0.1)
        clock.now += 20
        pad.press_stop()
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.STOPPED

    run(scenario())


def test_belt_seen_moving_long_after_a_stop_is_running_again(clock):
    """No status event (a pad without the status characteristic), but the
    data says the belt is turning: believe the data."""
    driver, pad, *_ = rig(FakePad(has_status=False))

    async def scenario():
        await running(driver, pad, clock)
        await driver.stop()
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.state is BeltState.STOPPED
        clock.now += 60
        pad.press_start()  # nobody tells us
        for _ in range(50):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh > 0 and driver.status.state is BeltState.RUNNING

    run(scenario())


def test_pad_without_step_counter_reports_none():
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        for _ in range(50):
            pad.advance(0.1)

    run(scenario())
    assert driver.status.steps is None
    assert driver.status.distance_m is not None


# ---- disconnects -------------------------------------------------------------------------------------


def test_link_loss_marks_status_and_emits_disconnected_once(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        await driver.set_speed(3.0)
        pad.advance(1.0)
        assert driver.status.speed_kmh > 0
        events.clear()
        pad.drop_link()
        status = driver.status
        assert not status.connected and status.state is BeltState.UNKNOWN
        assert status.speed_kmh == 0 and status.target_kmh is None
        assert events == [TreadmillEvent.DISCONNECTED]
        for call in (driver.set_speed(3.0), driver.stop(), driver.start()):
            with pytest.raises(TreadmillError, match="not connected"):
                await call
        await driver.disconnect()  # already gone: no second event
        assert events == [TreadmillEvent.DISCONNECTED]

    run(scenario())


def test_explicit_disconnect_emits_disconnected_once():
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.disconnect()
        await driver.disconnect()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]
    assert pad.client is None and not driver.status.connected


@pytest.mark.parametrize("how", ["link_loss", "disconnect_call"])
def test_a_pending_command_fails_cleanly_when_the_link_goes(how: str):
    driver, pad, backend, events = rig(command_timeout_s=30.0)

    async def scenario():
        await running(driver, pad)
        pad.lose_writes = True  # the answer will never come
        pending = asyncio.create_task(driver.set_speed(3.0))
        await asyncio.sleep(0.02)
        assert not pending.done()
        if how == "link_loss":
            pad.drop_link()
        else:
            await driver.disconnect()
        with pytest.raises(TreadmillError):
            await asyncio.wait_for(pending, 1.0)  # neither hangs nor leaks CancelledError
        assert not driver.status.connected

    run(scenario())
    assert events[-1] is TreadmillEvent.DISCONNECTED


def test_reconnect_after_link_loss_starts_from_scratch(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        pad.drop_link()
        pad.writes.clear()
        await driver.connect()
        assert driver.status.connected and driver.status.state is BeltState.UNKNOWN
        assert pad.writes == []  # reconnecting sends nothing to the belt
        # The pad kept running meanwhile; the next data packet says so.
        pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING and driver.status.speed_kmh > 0
        await driver.set_speed(2.0 * KMH_PER_MPH)
        assert pad.writes == [REQUEST_CONTROL, bytes.fromhex("024a01")]  # control is per connection

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED, TreadmillEvent.CONNECTED]


# ---- behaviour learnt from other UREVO clients ---------------------------------------------------


def test_post_workout_training_status_marks_the_pad_busy(clock):
    """After a stop the Strol 2E spends a while "post-workout", during which
    it acknowledges a start and ignores it. The driver reports that so the
    governor can wait."""
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad, clock)
        assert driver.status.busy is False
        await driver.stop()
        pad.send_training_status(0x0F)
        await asyncio.sleep(0)
        assert driver.status.busy is True
        pad.send_training_status(0x01)
        await asyncio.sleep(0)
        assert driver.status.busy is False
        # A disconnect must not leave the flag set.
        pad.send_training_status(0x0F)
        await asyncio.sleep(0)
        await driver.disconnect()
        assert driver.status.busy is False

    run(scenario())


def test_top_step_refused_by_the_pad_falls_back_to_the_advertised_maximum(clock):
    """4.0 mph has to be sent as 6.5 km/h to survive the firmware's rounding,
    which is above the 6.43 km/h the pad advertises. A pad that refuses the
    out-of-range value gets its advertised maximum instead (one step less),
    and the driver stops offering the unreachable step."""
    driver, pad, *_ = rig()
    pad.reject_payloads[ftms.cmd_set_target_speed(6.5)] = 0x03  # invalid parameter

    async def scenario():
        await running(driver, pad, clock)
        top = 4.0 * KMH_PER_MPH
        assert driver.status.max_kmh == pytest.approx(top)
        await driver.set_speed(top)
        assert pad.writes[-2:] == [ftms.cmd_set_target_speed(6.5), ftms.cmd_set_target_speed(6.43)]
        assert pad.target_tenths == 39  # 6.43 km/h truncates to 3.9 mph
        assert driver.status.max_kmh == pytest.approx(3.9 * KMH_PER_MPH)
        assert driver.status.target_kmh == pytest.approx(3.9 * KMH_PER_MPH)
        # From now on the top of the range is 3.9 mph and is sent directly.
        n = len(pad.writes)
        await driver.set_speed(top)
        assert len(pad.writes) == n + 1 and pad.target_tenths == 39

    run(scenario())


def test_a_rejection_inside_the_range_is_not_papered_over(clock):
    driver, pad, *_ = rig()
    pad.reject_payloads[ftms.cmd_set_target_speed(3.3)] = 0x04

    async def scenario():
        await running(driver, pad, clock)
        with pytest.raises(TreadmillError):
            await driver.set_speed(2.0 * KMH_PER_MPH)  # sent as 3.3 km/h
        assert driver.status.max_kmh == pytest.approx(4.0 * KMH_PER_MPH)

    run(scenario())


def test_control_point_writes_are_paced(monkeypatch):
    """Cheap modules drop writes that arrive back to back."""
    monkeypatch.setattr(ftms_ble, "WRITE_GAP_S", 0.05)
    driver, pad, *_ = rig()
    stamps: list[float] = []

    class StampedWrites(list):
        def append(self, item):
            stamps.append(perf_counter())
            super().append(item)

    pad.writes = StampedWrites()

    async def scenario():
        await driver.connect()
        await driver.start()  # request control, then start

    run(scenario())
    assert list(pad.writes) == [REQUEST_CONTROL, START]
    assert stamps[1] - stamps[0] >= 0.045


# ---- treadmills of other makes ----------------------------------------------------------


def wellfit(**kwargs) -> FakePad:
    """A WELLFIT as its app sees it: mph firmware up to 10 mph, and it says
    in its advertisement that it is a treadmill."""
    kwargs.setdefault("max_tenths", 100)
    return FakePad(name="WELLFIT TM Linker", address="A4:C1:38:00:29:01", says_treadmill=True, **kwargs)


def test_known_names():
    assert known.DEFAULT_NAME_PREFIXES == ("URTM", "WELLFIT TM Linker")
    assert known.known_treadmill("URTM030").brand == "UREVO"
    assert known.known_treadmill("wellfit tm linker").brand == "WELLFIT"
    for other in ("WELLFIT TM Audio", "WELLFIT", "Treadmill X", "", None):
        assert known.known_treadmill(other) is None
    # Only firmware that is known to need it gets the second request.
    assert [k.brand for k in known.KNOWN_TREADMILLS if k.request_control_twice] == ["WELLFIT"]


def test_discover_leaves_out_machines_that_say_they_are_not_treadmills():
    bike = FakePad(name="KICKR BIKE", address="11:11:11:11:11:11", says_treadmill=False)
    declared = FakePad(name="FS-A1B2C3", address="22:22:22:22:22:22", says_treadmill=True)
    undeclared = FakePad(name="Treadmill X", address="33:33:33:33:33:33")
    # A known name counts for more than the kind its firmware claims.
    misdeclared = FakePad(name="URTM054", address="44:44:44:44:44:44", says_treadmill=False)
    driver, *_ = rig(bike, declared, undeclared, misdeclared, wellfit())

    found = run(driver.discover(1.0))
    assert {d.name for d in found} == {"FS-A1B2C3", "Treadmill X", "URTM054", "WELLFIT TM Linker"}


def test_a_bike_trainer_is_not_connected_to_for_want_of_a_treadmill():
    driver, pad, backend, events = rig(FakePad(name="KICKR BIKE", says_treadmill=False))

    async def scenario():
        with pytest.raises(TreadmillError, match="treadmill not found"):
            await driver.connect()

    run(scenario())
    assert pad.connects == 0 and events == []


def test_a_wellfit_is_asked_for_control_twice_and_then_driven_like_any_pad(clock):
    """What qdomyos-zwift found it took to make a WELLFIT obey speed
    commands: Request Control twice at the start, and not again after."""
    driver, pad, backend, events = rig(wellfit())

    async def scenario():
        await driver.connect()
        assert driver.status.name == "WELLFIT TM Linker" and driver.status.mph_grid
        assert driver.status.max_kmh == pytest.approx(10.0 * KMH_PER_MPH)  # the cap is the governor's business
        assert pad.writes == []  # connecting sends nothing to the belt
        await driver.start()
        assert pad.writes == [REQUEST_CONTROL, REQUEST_CONTROL, START]
        for _ in range(40):
            pad.advance(0.1)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        await driver.set_speed(2.5 * KMH_PER_MPH)
        assert pad.target_tenths == 25
        await driver.pause()
        await driver.start()
        await driver.stop()

    run(scenario())
    assert pad.writes.count(REQUEST_CONTROL) == 2
    assert pad.writes[3:] == [bytes.fromhex("024a01"), bytes.fromhex("029a01"), PAUSE, START, STOP]


def test_a_stop_is_not_held_up_by_the_second_request(clock):
    """A belt found moving after a reconnect: the stop goes out after one
    Request Control, as on any pad - not one write gap later."""
    driver, pad, backend, events = rig(wellfit())

    async def scenario():
        await running(driver, pad, clock)
        pad.drop_link()
        pad.writes.clear()
        await driver.connect()
        pad.advance(0.1)
        await driver.stop()
        assert pad.writes == [REQUEST_CONTROL, STOP]
        assert pad.state == "idle"

    run(scenario())


def test_other_pads_are_asked_for_control_once(clock):
    for pad in (FakePad(), FakePad(name="Treadmill X", us_firmware=False), FakePad(name="WELLFIT TM Audio")):
        driver, pad, *_ = rig(pad, address=pad.address)
        run(running(driver, pad))
        assert pad.writes == [REQUEST_CONTROL, START]


def test_a_treadmill_that_says_it_takes_no_speed_target_is_still_tried():
    """Said in the features, and noted for the dashboard - but firmware that
    says so and obeys all the same exists, so the driver does not refuse."""
    driver, pad, *_ = rig(FakePad(name="Treadmill X", speed_target=False))

    async def scenario():
        await driver.connect()
        assert driver.features is not None and not driver.features.speed_target_supported
        await driver.start()
        for _ in range(40):
            pad.advance(0.1)
        await driver.set_speed(2.0 * KMH_PER_MPH)
        assert pad.target_tenths == 20

    run(scenario())
