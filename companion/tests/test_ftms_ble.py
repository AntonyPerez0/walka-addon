"""The Bluetooth reader against an emulated UREVO pad (GATT level).

The pad is worked the way its owner works it - with its remote - and the
reader is expected to hear all of it and to say nothing: ``FakePad.writes``
(everything written to the pad over Bluetooth) is empty at the end of every
scenario here, and the reader has no method that could fill it.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from walka import ftms
from walka.treadmill import base, bleak_backend, ftms_ble, known, simulated
from walka.treadmill.base import KMH_PER_MPH, BeltState, Treadmill, TreadmillError, TreadmillEvent, kmh_to_mph_tenths
from walka.treadmill.ftms_ble import COAST_S, BleClient, FtmsTreadmill, is_treadmill_candidate
from walka_fakes import FakeBackend, FakePad


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> FakeTime:
    """The reader's notion of time (the age of its data, a belt running down)."""
    fake = FakeTime()
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


def rig(pad: FakePad | None = None, *pads: FakePad, **driver_kwargs):
    pad = pad or FakePad()
    backend = FakeBackend(pad, *pads)
    driver = FtmsTreadmill(backend=backend, **driver_kwargs)
    events: list[TreadmillEvent] = []
    driver.on_event = events.append
    return driver, pad, backend, events


def run(coro):
    return asyncio.run(coro)


async def walking(driver: FtmsTreadmill, pad: FakePad, clock: FakeTime | None = None, kmh: float | None = None) -> None:
    """Connect; then the walker presses start on the remote, waits out the
    countdown and - if ``kmh`` is given - sets that speed."""
    await driver.connect()
    pad.press_start()
    for _ in range(40):
        pad.advance(0.1)
    assert pad.state == "running"
    if kmh is not None:
        pad.press_speed(kmh)
        for _ in range(60):
            pad.advance(0.1)
    if clock is not None:
        clock.now += COAST_S + 1


def test_kmh_to_mph_tenths():
    assert kmh_to_mph_tenths(0.6 * KMH_PER_MPH) == 6
    assert kmh_to_mph_tenths(4.0 * KMH_PER_MPH) == 40
    assert kmh_to_mph_tenths(3.2) == 20
    assert kmh_to_mph_tenths(0.0) == 0


# ---- it only listens --------------------------------------------------------------------
#
# The point of this version of Walka. These tests are about what is *not*
# there, so they look at the code as well as at what it does.

MOTOR_VERBS = ("start", "stop", "pause", "resume", "halt", "set_speed", "set_target", "command", "write", "request_control")


def test_no_reader_has_a_way_to_move_a_belt():
    """Not the interface, not the Bluetooth reader, not the simulator."""
    for reader in (Treadmill, FtmsTreadmill, simulated.SimulatedTreadmill):
        public = {name for name, _ in inspect.getmembers(reader) if not name.startswith("_")}
        # (The simulator's press_* are its remote: tests and the demo press them, the companion cannot.)
        offending = {name for name in public if name in MOTOR_VERBS or name.startswith(("set_", "cmd_"))}
        assert not offending, f"{reader.__name__} can {sorted(offending)}"
    assert set(Treadmill.__abstractmethods__) == {"connect", "disconnect"}


def test_the_door_to_the_bluetooth_link_has_no_write():
    """Everything the reader does to a connection goes through ``BleClient``
    - and through its bleak adapter on a real computer. Neither can write a
    characteristic."""
    for door in (BleClient, bleak_backend.BleakClientAdapter):
        names = {name for name in vars(door) if not name.startswith("_")}
        assert names == {"is_connected", "connect", "disconnect", "has_characteristic", "read", "subscribe"}, door
    source = inspect.getsource(bleak_backend)
    assert "write_gatt" not in source


def test_there_is_no_command_to_put_together():
    """The decoder knows what a treadmill says. It has no encoder for what
    one could be told: no opcodes, no command builders, no control-point
    answers to wait for."""
    for name in ("Op", "ResultCode", "ControlResponse", "parse_control_response"):
        assert not hasattr(ftms, name), name
    assert not [name for name in dir(ftms) if name.startswith("cmd_")]
    for module in (base, ftms_ble, simulated):
        source = inspect.getsource(module)
        for word in ("CONTROL_POINT", "write(", "set_speed", "request_control"):
            assert word not in source, f"{module.__name__} mentions {word}"
    # The characteristic is named for one purpose: so that the probe can label it in its report.
    assert ftms.CONTROL_POINT == "00002ad9-0000-1000-8000-00805f9b34fb"


def test_a_whole_walk_is_heard_and_nothing_is_said(clock):
    driver, pad, backend, events = rig(FakePad(steps_in_data=True))

    async def scenario():
        await walking(driver, pad, clock, kmh=3.0 * KMH_PER_MPH)
        pad.press_speed(1.5 * KMH_PER_MPH)
        for _ in range(100):
            clock.now += 0.1
            pad.advance(0.1)
        pad.press_pause()
        for _ in range(50):
            clock.now += 0.1
            pad.advance(0.1)
        pad.press_start()
        for _ in range(100):
            clock.now += 0.1
            pad.advance(0.1)
        pad.pull_safety_key()
        for _ in range(50):
            clock.now += 0.1
            pad.advance(0.1)
        pad.drop_link()
        await driver.connect()
        await driver.disconnect()

    run(scenario())
    assert pad.writes == []
    assert ftms.CONTROL_POINT not in backend.clients[-1].pad.subscriptions
    assert events == [
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
    ]


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
    assert events == [TreadmillEvent.CONNECTED]
    # Connecting writes nothing to the pad, and the control point - the one
    # characteristic a belt can be moved through - is not even listened to.
    assert pad.writes == []
    assert set(pad.subscriptions) == {ftms.TREADMILL_DATA, ftms.FITNESS_MACHINE_STATUS, ftms.TRAINING_STATUS}
    assert pad.reads == [ftms.SUPPORTED_SPEED_RANGE]  # ... and this is the whole of what is read


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
        # The phone app holds the pad's single connection: same symptom.
        pad.powered, pad.busy = True, True
        with pytest.raises(TreadmillError, match="not found"):
            await driver.connect()
        pad.busy = False
        await driver.connect()

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]


def test_a_pad_that_only_shows_its_numbers_is_read_like_any_other():
    """A treadmill with no control point at all - one that apps can watch
    and not drive - is all Walka needs."""
    driver, pad, backend, events = rig(FakePad(has_control_point=False))

    async def scenario():
        await driver.connect()
        pad.press_start()
        for _ in range(60):
            pad.advance(0.1)

    run(scenario())
    assert driver.status.connected and driver.status.state is BeltState.RUNNING and driver.status.speed_kmh > 0
    assert pad.writes == []


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
            client.subscribe_errors.add(ftms.TREADMILL_DATA)
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


# ---- what the pad says ---------------------------------------------------------------------------


def test_data_notifications_update_the_status(clock):
    driver, pad, *_ = rig(FakePad(steps_in_data=True))

    async def scenario():
        await walking(driver, pad, clock, kmh=3.0 * KMH_PER_MPH)
        for _ in range(600):
            pad.advance(0.1)
        clock.now += 5
        pad.advance(0.1)

    run(scenario())
    status = driver.status
    assert status.state is BeltState.RUNNING
    assert status.speed_kmh == pytest.approx(4.82)  # 3.0 mph, truncated km/h
    assert status.target_kmh == pytest.approx(4.82)  # what the pad said it was set to
    assert status.distance_m == int(pad.distance_m) and status.distance_m > 60
    assert status.energy_kcal == int(pad.kcal)
    assert status.steps == int(pad.steps) and status.steps > 100
    assert status.elapsed_s == int(pad.elapsed_s)
    assert status.updated_at == clock.now and status.age_s(clock.now + 2.5) == pytest.approx(2.5)
    data = status.to_dict()
    assert data["state"] == "running" and "updated_at" not in data and "busy" not in data
    assert pad.writes == []


def test_the_buttons_on_the_pad_are_followed(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        events.clear()
        pad.press_start()
        assert driver.status.state is BeltState.STARTING  # the pad's own countdown
        for _ in range(40):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING and driver.status.speed_kmh > 0
        pad.press_speed(2.0 * KMH_PER_MPH)
        assert driver.status.target_kmh == pytest.approx(3.21)
        pad.press_pause()
        assert driver.status.state is BeltState.PAUSED and driver.status.target_kmh is None
        pad.press_start()
        for _ in range(40):
            clock.now += 0.1
            pad.advance(0.1)
        clock.now += COAST_S + 1
        pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING
        pad.pull_safety_key()
        assert driver.status.state is BeltState.STOPPED

    run(scenario())
    assert events == []  # the buttons are the walker's business: nothing is announced, nothing answered
    assert pad.writes == []


def test_belt_coasting_down_after_a_stop_is_not_reported_as_running(clock):
    """After stop/pause the pad still sends a few packets with speed > 0."""
    driver, pad, *_ = rig()

    async def scenario():
        await walking(driver, pad, clock, kmh=3.0 * KMH_PER_MPH)
        pad.press_pause()
        pad.advance(0.1)
        assert driver.status.speed_kmh > 1.0  # still coasting ...
        assert driver.status.state is BeltState.PAUSED  # ... but paused, not running
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.PAUSED

        pad.press_start()
        for _ in range(60):
            clock.now += 0.1
            pad.advance(0.1)
        clock.now += COAST_S + 1
        pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING
        pad.press_stop()
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.STOPPED

    run(scenario())


def test_a_pad_that_announces_nothing_is_read_from_its_data_alone(clock):
    """No status characteristic: the data says whether the belt is turning."""
    driver, pad, *_ = rig(FakePad(has_status=False))

    async def scenario():
        await walking(driver, pad, clock)
        assert driver.status.speed_kmh > 0 and driver.status.state is BeltState.RUNNING
        pad.press_stop()  # nobody tells us
        for _ in range(30):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh == 0 and driver.status.state is BeltState.STOPPED
        clock.now += 60
        pad.press_start()
        for _ in range(50):
            clock.now += 0.1
            pad.advance(0.1)
        assert driver.status.speed_kmh > 0 and driver.status.state is BeltState.RUNNING

    run(scenario())


def test_pad_without_step_counter_reports_none():
    driver, pad, *_ = rig()

    async def scenario():
        await walking(driver, pad)
        for _ in range(50):
            pad.advance(0.1)

    run(scenario())
    assert driver.status.steps is None
    assert driver.status.distance_m is not None


def test_training_status_shows_the_countdown():
    driver, pad, *_ = rig()

    async def scenario():
        await driver.connect()
        pad.send_training_status(0x01)  # idle, and no speed: the belt stands still
        assert driver.status.state is BeltState.STOPPED
        pad.send_training_status(0x0E)  # pre-workout: the 3-2-1
        assert driver.status.state is BeltState.STARTING
        pad.send_training_status(0x0F)  # post-workout: nothing the reader has to wait for
        assert driver.status.state is BeltState.STARTING
        driver._on_training_status(b"\x00")  # too short to say anything

    run(scenario())
    assert pad.writes == []


# ---- disconnects -------------------------------------------------------------------------------------


def test_link_loss_marks_status_and_emits_disconnected_once(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await walking(driver, pad, clock, kmh=3.0)
        assert driver.status.speed_kmh > 0
        events.clear()
        pad.drop_link()
        status = driver.status
        assert not status.connected and status.state is BeltState.UNKNOWN
        assert status.speed_kmh == 0 and status.target_kmh is None
        assert events == [TreadmillEvent.DISCONNECTED]
        await driver.disconnect()  # already gone: no second event
        assert events == [TreadmillEvent.DISCONNECTED]

    run(scenario())
    assert pad.speed_kmh > 0  # the belt is the walker's: losing the listener changes nothing about it


def test_explicit_disconnect_emits_disconnected_once_and_leaves_the_belt_alone():
    driver, pad, backend, events = rig()

    async def scenario():
        await walking(driver, pad, kmh=3.0)
        await driver.disconnect()
        await driver.disconnect()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]
    assert pad.client is None and not driver.status.connected
    assert pad.state == "running" and pad.speed_kmh > 0 and pad.writes == []


def test_reconnect_after_link_loss_starts_from_scratch(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await walking(driver, pad, clock)
        pad.drop_link()
        await driver.connect()
        assert driver.status.connected and driver.status.state is BeltState.UNKNOWN
        # The pad kept running meanwhile; the next data packet says so.
        pad.advance(0.1)
        assert driver.status.state is BeltState.RUNNING and driver.status.speed_kmh > 0

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED, TreadmillEvent.CONNECTED]
    assert pad.writes == []


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


def test_a_wellfit_is_read_like_any_pad(clock):
    driver, pad, backend, events = rig(wellfit())

    async def scenario():
        await walking(driver, pad, clock, kmh=3.0 * KMH_PER_MPH)

    run(scenario())
    status = driver.status
    assert status.name == "WELLFIT TM Linker" and status.mph_grid
    assert status.max_kmh == pytest.approx(10.0 * KMH_PER_MPH, abs=1e-9)  # what it says it can do: shown, nothing more
    assert status.state is BeltState.RUNNING and status.speed_kmh == pytest.approx(4.82)
    assert pad.writes == []


# ---- heart rate from the hand grips ---------------------------------------------------------------


def grips(driver: FtmsTreadmill, bpm: int | None, kmh: float = 3.0) -> None:
    driver._on_data(ftms.build_treadmill_data(kmh, heart_rate_bpm=bpm))


def test_a_heart_rate_the_pad_stops_reporting_is_not_kept(clock):
    driver, pad, _backend, _events = rig()
    run(driver.connect())
    grips(driver, 112)
    assert driver.status.heart_rate_bpm == 112 and driver.status.heart_rate_age_s(clock.now) == 0
    grips(driver, 0)  # hands off the grips: the pad says 0
    assert driver.status.heart_rate_bpm is None
    grips(driver, 108)
    grips(driver, None)  # ... or leaves the field out: it grows old
    clock.now += 30
    assert driver.status.heart_rate_age_s(clock.now) == 30
    assert driver.status.fresh_heart_rate(clock.now) is None


def test_a_heart_rate_does_not_outlive_the_connection(clock):
    driver, pad, _backend, _events = rig()
    run(driver.connect())
    grips(driver, 120)
    run(driver.disconnect())
    assert driver.status.heart_rate_bpm is None
    run(driver.connect())
    assert driver.status.heart_rate_bpm is None and driver.status.fresh_heart_rate(clock.now) is None


def test_packets_without_a_speed_do_not_keep_an_old_speed_fresh(clock):
    """The speed counts as current only as long as packets that carry one
    keep coming; packets of distance or time alone say nothing of the belt."""
    driver, pad, _backend, _events = rig()
    run(driver.connect())
    driver._on_data(ftms.build_treadmill_data(3.2))
    assert driver.status.age_s(clock.now) == 0
    for _ in range(30):
        clock.now += 1
        driver._on_data(ftms.build_treadmill_data(None, total_distance_m=100))
    assert driver.status.age_s(clock.now) == 30 and driver.status.distance_m == 100
