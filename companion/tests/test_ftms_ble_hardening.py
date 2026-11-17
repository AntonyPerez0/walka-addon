"""The Bluetooth driver when things go wrong at the worst moment: a stop
behind a command in flight, late or stale answers, a link that drops while
connecting, buttons pressed at the pad right after one of our own commands.

Same emulated pad as test_ftms_ble.py. Where a test must show that the
driver does *not* wait, the answer time-out is set far above the test's own
deadline: waiting would fail the test instead of merely slowing it down.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ftms_ble import PAUSE, REQUEST_CONTROL, START, STOP, FakeTime, rig, run, running
from walka import ftms
from walka.app import App
from walka.config import Config
from walka.governor import Action, Command
from walka.treadmill import ftms_ble
from walka.treadmill.base import KMH_PER_MPH, BeltState, CommandSuperseded, TreadmillError, TreadmillEvent
from walka.treadmill.ftms_ble import OWN_HALT_EVENT_WINDOW_S, FtmsTreadmill, describe_bluetooth_error
from walka_fakes import FakeBackend, FakePad

SPEED_2_MPH = bytes.fromhex("024a01")  # 3.3 km/h on the wire
NEVER = 30.0  # an answer time-out no test waits for
SOON = 2.0  # ... and the deadline they are held to instead
STOP_LATENCY_BOUND_S = 1.0


@pytest.fixture
def clock(monkeypatch) -> FakeTime:
    """The driver's notion of time (as in test_ftms_ble.py)."""
    fake = FakeTime()
    monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=fake.monotonic))
    return fake


class MutePad(FakePad):
    """A pad that carries out some commands without acknowledging them."""

    def __init__(self, *mute_ops: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mute_ops = set(mute_ops)

    def _indicate(self, op: int, result: int) -> None:
        if op not in self.mute_ops:
            super()._indicate(op, result)


class TaciturnPad(FakePad):
    """A pad that does not announce what it does on Bluetooth's orders: the
    test sends the status events itself, when and as it likes."""

    obeying = False

    def control_write(self, data: bytes) -> None:
        self.obeying = True
        try:
            super().control_write(data)
        finally:
            self.obeying = False

    def _status(self, *payload: int) -> None:
        if not self.obeying:
            super()._status(*payload)


def hold_writes(backend: FakeBackend) -> asyncio.Event:
    """Make the connected client's Bluetooth writes hang until the returned
    event is set (a write the radio has accepted but not yet completed)."""
    gate = asyncio.Event()
    client = backend.clients[-1]
    real_write = client.write

    async def held_write(uuid: str, data: bytes, response: bool) -> None:
        await gate.wait()
        await real_write(uuid, data, response)

    client.write = held_write
    return gate


# ---- a stop goes first (review finding 2) -------------------------------------------


@pytest.mark.parametrize("halt,wire,state", [("stop", STOP, "idle"), ("pause", PAUSE, "paused")])
def test_a_stop_does_not_wait_for_the_answer_to_the_command_in_flight(halt: str, wire: bytes, state: str):
    driver, pad, *_ = rig(MutePad(0x02), command_timeout_s=NEVER)

    async def scenario():
        await running(driver, pad)
        speed = asyncio.create_task(driver.set_speed(2.0 * KMH_PER_MPH))
        await asyncio.sleep(0.05)
        assert pad.writes[-1] == SPEED_2_MPH and not speed.done()  # written, waiting for an answer
        await asyncio.wait_for(getattr(driver, halt)(), SOON)
        assert pad.writes[-1] == wire and pad.state == state
        # The speed command comes back as "outcome unknown", not as an error.
        await asyncio.wait_for(speed, SOON)

    run(scenario())
    assert pad.writes[2:] == [SPEED_2_MPH, wire]


@pytest.mark.parametrize("queued", ["start", "set_speed"])
def test_a_command_not_yet_written_is_dropped_when_a_stop_arrives(queued: str):
    driver, pad, backend, _ = rig(command_timeout_s=NEVER)

    async def scenario():
        await running(driver, pad)
        gate = hold_writes(backend)
        first = asyncio.create_task(driver.set_speed(2.0 * KMH_PER_MPH))  # in the radio's hands
        await asyncio.sleep(0.05)
        behind = asyncio.create_task(driver.start() if queued == "start" else driver.set_speed(3.0 * KMH_PER_MPH))
        await asyncio.sleep(0.05)
        stop = asyncio.create_task(driver.stop())
        await asyncio.sleep(0.05)
        assert not stop.done() and pad.writes[2:] == []
        gate.set()
        await asyncio.wait_for(stop, SOON)
        with pytest.raises(CommandSuperseded, match="not sent"):
            await asyncio.wait_for(behind, SOON)
        await asyncio.wait_for(first, SOON)  # it was already on its way: unknown outcome, no error

    run(scenario())
    # Nothing was written between the command in flight and the stop.
    assert pad.writes[2:] == [SPEED_2_MPH, STOP]
    assert pad.state == "idle" and driver.status.state is BeltState.STOPPED


def test_a_stop_never_waits_for_request_control():
    """Right after connecting, a pad that does not answer Request Control
    must not cost a stop the whole answer time-out."""
    driver, pad, *_ = rig(MutePad(0x00), command_timeout_s=NEVER)
    pad.state, pad.speed_kmh = "running", 3.0  # the belt was left running by an earlier connection

    async def scenario():
        await driver.connect()
        await asyncio.wait_for(driver.stop(), SOON)

    run(scenario())
    assert pad.writes == [REQUEST_CONTROL, STOP] and pad.state == "idle"


def test_a_stop_overtakes_the_first_command_of_a_connection():
    """The first command asks for control first. A stop that arrives while
    that request is unanswered is written next; the command is dropped."""
    driver, pad, *_ = rig(MutePad(0x00), command_timeout_s=NEVER)
    pad.state, pad.speed_kmh = "running", 3.0

    async def scenario():
        await driver.connect()
        speed = asyncio.create_task(driver.set_speed(2.0 * KMH_PER_MPH))
        await asyncio.sleep(0.05)
        assert pad.writes == [REQUEST_CONTROL]
        await asyncio.wait_for(driver.stop(), SOON)
        with pytest.raises(CommandSuperseded):
            await asyncio.wait_for(speed, SOON)

    run(scenario())
    assert pad.writes == [REQUEST_CONTROL, STOP]  # control is not asked for twice


def test_a_stop_does_not_wait_for_an_earlier_pause_to_be_answered_either():
    driver, pad, *_ = rig(MutePad(0x08), command_timeout_s=NEVER)

    async def scenario():
        await running(driver, pad)
        pause = asyncio.create_task(driver.pause())
        await asyncio.sleep(0.05)
        assert pad.writes[-1] == PAUSE and not pause.done()
        pad.mute_ops.clear()
        await asyncio.wait_for(driver.stop(), SOON)
        await asyncio.wait_for(pause, SOON)

    run(scenario())
    assert pad.writes[2:] == [PAUSE, STOP]  # both were sent: a stop is never the one that gives way
    assert pad.state == "idle" and driver.status.state is BeltState.STOPPED


def test_commands_after_a_stop_are_sent_normally():
    """Giving way to a stop is not a latch in the driver: what the governor
    sends afterwards goes out."""
    driver, pad, *_ = rig(MutePad(0x02), command_timeout_s=NEVER)

    async def scenario():
        await running(driver, pad)
        speed = asyncio.create_task(driver.set_speed(2.0 * KMH_PER_MPH))
        await asyncio.sleep(0.05)
        await asyncio.wait_for(driver.pause(), SOON)
        await speed
        pad.mute_ops.clear()
        await asyncio.wait_for(driver.start(), SOON)
        for _ in range(40):
            pad.advance(0.1)
        await asyncio.wait_for(driver.set_speed(2.0 * KMH_PER_MPH), SOON)

    run(scenario())
    assert pad.writes[2:] == [SPEED_2_MPH, PAUSE, START, SPEED_2_MPH]
    assert pad.target_tenths == 20 and driver._halts_waiting == 0


def test_a_bluetooth_write_that_never_completes_is_given_up_on(monkeypatch):
    monkeypatch.setattr(ftms_ble, "WRITE_TIMEOUT_S", 0.05)
    driver, pad, backend, _ = rig()

    async def scenario():
        await running(driver, pad)
        gate = hold_writes(backend)
        with pytest.raises(TreadmillError, match="timed out"):
            await asyncio.wait_for(driver.set_speed(2.0 * KMH_PER_MPH), SOON)
        # The driver is not left holding its lock: the next command gets through.
        gate.set()
        await asyncio.wait_for(driver.stop(), SOON)

    run(scenario())
    assert pad.writes[2:] == [STOP] and pad.state == "idle"


class StampedWrites(list):
    """``FakePad.writes`` that also notes when each write arrived."""

    def __init__(self) -> None:
        super().__init__()
        self.stamps: list[tuple[float, bytes]] = []

    def append(self, item: bytes) -> None:
        self.stamps.append((asyncio.get_running_loop().time(), item))
        super().append(item)

    def when(self, payload: bytes) -> float | None:
        return next((at for at, write in self.stamps if write == payload), None)


async def stop_latency(pad: FakePad, in_flight, stop) -> float:
    """Start ``in_flight``, ask for ``stop`` 0.1 s later; seconds until the
    stop command is written to the pad."""
    loop = asyncio.get_running_loop()
    busy = asyncio.create_task(in_flight())
    await asyncio.sleep(0.1)
    asked = loop.time()
    stopping = asyncio.create_task(stop())
    while pad.writes.when(STOP) is None and loop.time() - asked < 1.5:
        await asyncio.sleep(0.05)
    written = pad.writes.when(STOP)
    # (Cancelled rather than awaited: a silent pad leaves the stop waiting
    # three seconds for its own answer, which is of no interest here.)
    for task in (busy, stopping):
        task.cancel()
    await asyncio.gather(busy, stopping, return_exceptions=True)
    assert written is not None, "the stop was not written within 1.5 s"
    return written - asked


def silent_running_pad() -> FakePad:
    pad = FakePad(silent=True)  # carries commands out, never acknowledges one
    pad.writes = StampedWrites()
    pad.state, pad.speed_kmh = "running", 3.0
    return pad


@pytest.mark.parametrize("first_command", [False, True])
def test_stop_is_written_at_once_whatever_is_in_flight(monkeypatch, first_command: bool):
    """With the real 0.4 s gap between writes and the real 3 s answer
    time-out, on a pad that never acknowledges anything: the worst case."""
    monkeypatch.setattr(ftms_ble, "WRITE_GAP_S", 0.4)
    pad = silent_running_pad()
    driver = FtmsTreadmill(backend=FakeBackend(pad))  # command_timeout_s = 3 s, as shipped

    async def scenario() -> float:
        await driver.connect()
        pad.advance(0.1)
        if not first_command:
            driver._has_control = True  # an earlier command has asked for control
        return await stop_latency(pad, lambda: driver.set_speed(3.2), driver.stop)

    latency = run(scenario())
    # About 0.3 s on an idle machine (what is left of the gap between two
    # writes). The bound leaves room for a busy one; without the stop going
    # first it would be 2.9 s here, and 6 s for the first command.
    assert latency < STOP_LATENCY_BOUND_S, f"stop written {latency:.2f} s after it was asked for"
    if first_command:
        assert list(pad.writes) == [REQUEST_CONTROL, STOP]  # the speed command gave way
    else:
        assert list(pad.writes) == [bytes.fromhex("024a01"), STOP]


def test_the_dashboard_stop_is_written_at_once_too(tmp_path: Path, monkeypatch):
    """The same through App.emergency_stop (hotkey, dashboard STOP)."""
    monkeypatch.setattr(ftms_ble, "WRITE_GAP_S", 0.4)
    pad = silent_running_pad()
    driver = FtmsTreadmill(backend=FakeBackend(pad))
    config = Config()
    config.stop_hotkey = ""
    config.wow.addon_dir = str(tmp_path / "nowhere")
    app = App(config, tmp_path / "data", treadmill=driver)

    async def scenario() -> float:
        await driver.connect()
        pad.advance(0.1)
        driver._has_control = True
        speed = Command(Action.SET_SPEED, speed_kmh=3.2)
        return await stop_latency(pad, lambda: app._execute(speed), app.emergency_stop)

    latency = run(scenario())
    assert latency < STOP_LATENCY_BOUND_S, f"stop written {latency:.2f} s after STOP was pressed"


# ---- answers are matched to their command (review finding 6) ---------------------------


def test_a_late_answer_to_an_earlier_command_is_not_taken_for_the_stops():
    driver, pad, *_ = rig(MutePad(0x02))

    async def scenario():
        await running(driver, pad)
        await driver.set_speed(2.0 * KMH_PER_MPH)  # no answer in time: outcome unknown
        real_write = pad.control_write

        def write_with_a_late_answer_first(data: bytes) -> None:
            if data == STOP:
                pad.send_stray_indication(0x02, 0x04)  # "set speed: operation failed", at last
            real_write(data)

        pad.control_write = write_with_a_late_answer_first
        await driver.stop()  # must not raise "rejected: SET_TARGET_SPEED"

    run(scenario())
    assert pad.writes[2:] == [SPEED_2_MPH, STOP]
    assert driver.status.state is BeltState.STOPPED and driver.status.target_kmh is None


def test_a_stale_control_not_permitted_does_not_make_a_start_go_out_twice():
    driver, pad, *_ = rig()

    async def scenario():
        await driver.connect()
        real_write = pad.control_write

        def write_with_a_late_answer_first(data: bytes) -> None:
            if data == START:
                pad.send_stray_indication(0x02, 0x05)
            real_write(data)

        pad.control_write = write_with_a_late_answer_first
        await driver.start()

    run(scenario())
    assert pad.writes == [REQUEST_CONTROL, START]


def test_a_rejection_of_the_command_itself_still_counts():
    driver, pad, *_ = rig()

    async def scenario():
        await running(driver, pad)
        pad.reject[0x08] = 0x04
        with pytest.raises(TreadmillError, match="STOP_OR_PAUSE: OPERATION_FAILED"):
            await driver.stop()

    run(scenario())


# ---- a link that drops while connecting (review finding 0) --------------------------------


def test_a_link_lost_during_the_optional_subscriptions_is_a_failed_connection():
    driver, pad, backend, events = rig()

    async def scenario():
        make_client = backend.client

        def unlucky_client(device, on_disconnect):
            client = make_client(device, on_disconnect)
            real_subscribe = client.subscribe

            async def subscribe(uuid, callback):
                if uuid == ftms.FITNESS_MACHINE_STATUS:
                    pad.drop_link()  # the backend reports the loss ...
                    raise OSError("not connected (simulated)")  # ... and this optional step fails quietly
                await real_subscribe(uuid, callback)

            client.subscribe = subscribe
            return client

        backend.client = unlucky_client
        with pytest.raises(TreadmillError, match="lost while it was being set up"):
            await driver.connect()
        assert not driver.status.connected and events == []
        assert "connection failed" in driver.status.detail

        # The app's connection loop retries for as long as status.connected is False.
        backend.client = make_client
        await driver.connect()
        await driver.start()

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]
    assert pad.writes == [REQUEST_CONTROL, START] and pad.connects == 2


def test_a_link_that_dies_silently_during_set_up_is_noticed_too():
    """No callback at all (it may still be on its way from the Bluetooth
    thread): the client itself says it is no longer connected."""
    driver, pad, backend, events = rig()

    async def scenario():
        make_client = backend.client

        def unlucky_client(device, on_disconnect):
            client = make_client(device, on_disconnect)
            real_subscribe = client.subscribe

            async def subscribe(uuid, callback):
                await real_subscribe(uuid, callback)
                if uuid == ftms.TRAINING_STATUS:
                    pad.client = None  # gone, and nobody has been told yet

            client.subscribe = subscribe
            return client

        backend.client = unlucky_client
        with pytest.raises(TreadmillError, match="lost while"):
            await driver.connect()

    run(scenario())
    assert not driver.status.connected and events == []


def test_a_late_disconnect_report_from_an_old_connection_is_ignored():
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        await driver.disconnect()
        await driver.connect()
        old, new = backend.clients
        old.on_disconnect()  # the first connection's loss, reported late
        assert driver.status.connected and new.is_connected
        await driver.start()  # the current connection is untouched
        new.on_disconnect()
        assert not driver.status.connected

    run(scenario())
    assert events == [
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
    ]
    assert pad.writes == [REQUEST_CONTROL, START]


def test_set_up_that_hangs_is_given_up_on(monkeypatch):
    monkeypatch.setattr(ftms_ble, "SETUP_TIMEOUT_S", 0.05)
    driver, pad, backend, events = rig()

    async def scenario():
        make_client = backend.client

        def stuck_client(device, on_disconnect):
            client = make_client(device, on_disconnect)

            async def subscribe(uuid, callback):
                await asyncio.Event().wait()

            client.subscribe = subscribe
            return client

        backend.client = stuck_client
        with pytest.raises(TreadmillError, match="timed out - is the pad switched on"):
            await asyncio.wait_for(driver.connect(), SOON)
        assert pad.client is None  # let go of, so that the next attempt can connect

    run(scenario())
    assert not driver.status.connected and events == []


# ---- who stopped the belt? (review finding 5) --------------------------------------------------


@pytest.mark.parametrize("ours", ["pause", "stop"])
@pytest.mark.parametrize("press", ["press_stop", "press_pause", "pull_safety_key"])
def test_a_button_at_the_pad_right_after_our_own_halt_is_still_reported(clock, ours: str, press: str):
    """The pad has already announced our command; whatever comes next is a
    person, however soon it comes - and a stop at the pad is final."""
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        await getattr(driver, ours)()
        assert events == [TreadmillEvent.CONNECTED]  # our own halt is not reported
        clock.now += 2.0
        getattr(pad, press)()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_STOP]


def test_an_announcement_that_comes_when_the_belt_has_run_down_is_still_ours(clock):
    driver, pad, backend, events = rig(TaciturnPad())

    async def scenario():
        await running(driver, pad, clock)
        await driver.pause()
        clock.now += 7.0
        pad._status(0x02, 0x02)  # "paused", seven seconds after the command
        assert events == [TreadmillEvent.CONNECTED]
        assert driver.status.state is BeltState.PAUSED
        pad._status(0x02, 0x02)  # a second one is not: we only paused once
        assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_STOP]

    run(scenario())


@pytest.mark.parametrize(
    "ours,event",
    [
        ("pause", (0x02, 0x01)),  # we paused; "stopped" is the stop button
        ("stop", (0x02, 0x02)),  # we stopped; "paused" is the pause button
        ("pause", (0x03,)),  # the safety key is never ours
        ("stop", (0x03,)),
        ("stop", (0x02,)),  # no parameter: cannot be matched to anything
    ],
)
def test_an_event_that_does_not_match_our_command_is_external(clock, ours: str, event: tuple[int, ...]):
    driver, pad, backend, events = rig(TaciturnPad())

    async def scenario():
        await running(driver, pad, clock)
        await getattr(driver, ours)()
        clock.now += 1.0
        pad._status(*event)

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_STOP]


def test_the_wait_for_our_own_announcement_ends(clock):
    """A pad that never announces Bluetooth commands must not make a press of
    its pause button look like ours for ever."""
    driver, pad, backend, events = rig(TaciturnPad())

    async def scenario():
        await running(driver, pad, clock)
        await driver.pause()
        clock.now += OWN_HALT_EVENT_WINDOW_S + 0.5
        pad.press_pause()

    run(scenario())
    assert events[-1] is TreadmillEvent.EXTERNAL_STOP


def test_a_refused_halt_expects_no_announcement(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await running(driver, pad, clock)
        pad.reject[0x08] = 0x04
        with pytest.raises(TreadmillError):
            await driver.pause()
        pad.press_pause()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_STOP]


def test_after_our_own_start_a_stop_event_is_somebody_saying_no(clock):
    driver, pad, backend, events = rig(TaciturnPad())

    async def scenario():
        await running(driver, pad, clock)
        await driver.pause()  # (never announced by this pad)
        clock.now += 1.0
        await driver.start()
        clock.now += 1.0
        pad.press_pause()

    run(scenario())
    assert events[-1] is TreadmillEvent.EXTERNAL_STOP


def test_a_stop_that_cuts_a_start_short_is_still_recognised_as_ours(clock):
    driver, pad, backend, events = rig(MutePad(0x07), command_timeout_s=NEVER)

    async def scenario():
        await driver.connect()
        start = asyncio.create_task(driver.start())
        await asyncio.sleep(0.05)
        await asyncio.wait_for(driver.stop(), SOON)  # the pad announces it while it is being written
        await asyncio.wait_for(start, SOON)

    run(scenario())
    assert pad.writes == [REQUEST_CONTROL, START, STOP]
    assert events == [TreadmillEvent.CONNECTED]


def test_a_start_that_was_not_sent_does_not_claim_a_start_at_the_pad(clock):
    driver, pad, backend, events = rig()

    async def scenario():
        await driver.connect()
        pad.reject[0x07] = 0x04
        with pytest.raises(TreadmillError):
            await driver.start()
        clock.now += 1.0
        pad.press_start()  # the remote, a second after our own start was refused

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.EXTERNAL_START]


def test_a_reconnect_forgets_announcements_that_were_due(clock):
    driver, pad, backend, events = rig(TaciturnPad())

    async def scenario():
        await running(driver, pad, clock)
        await driver.pause()
        pad.drop_link()
        await driver.connect()
        events.clear()
        pad.press_pause()

    run(scenario())
    assert events == [TreadmillEvent.EXTERNAL_STOP]


# ---- the advertised speed range (review finding 10) -------------------------------------------


@pytest.mark.parametrize(
    "raw,units,expected",
    [
        # A pad that reports a minimum of zero: its maximum is still its maximum.
        ((0, 400, 10), "auto", (False, 1.0, 4.0, 0.1)),
        ((0, 643, 16), "auto", (True, 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH, 0.1 * KMH_PER_MPH)),
        ((0, 400, 0), "auto", (False, 1.0, 4.0, 0.1)),
        # A maximum below the default minimum: never above it.
        ((0, 80, 10), "auto", (False, 0.8, 0.8, 0.1)),
        # Nonsense (minimum above maximum): the lower figure bounds what may be commanded.
        ((643, 96, 16), "auto", (True, 0.6 * KMH_PER_MPH, 0.6 * KMH_PER_MPH, 0.1 * KMH_PER_MPH)),
        # No usable maximum: the defaults, as before.
        ((100, 0, 10), "auto", (False, 1.0, 6.0, 0.1)),
        ((0, 0, 0), "mph", (True, 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH, 0.1 * KMH_PER_MPH)),
    ],
)
def test_the_advertised_maximum_is_kept_whenever_there_is_one(raw, units, expected):
    pad = FakePad()
    pad.speed_range_override = b"".join(value.to_bytes(2, "little") for value in raw)
    driver, *_ = rig(pad, units=units)

    async def scenario():
        await driver.connect()

    run(scenario())
    status = driver.status
    mph, lo, hi, step = expected
    assert status.mph_grid is mph
    assert status.min_kmh == pytest.approx(lo, abs=1e-9)
    assert status.max_kmh == pytest.approx(hi, abs=1e-9)
    assert status.step_kmh == pytest.approx(step, abs=1e-9)
    # Whatever is asked for, the value sent never exceeds the advertised maximum
    # (by more than the mph firmware's rounding-up to the next 0.1 km/h).
    if raw[1] > 0:
        assert driver.quantise(99.0)[1] <= raw[1] / 100 + 0.1


# ---- reasons a person can act on (review finding 12) --------------------------------------------


def test_bluetooth_switched_off_reads_as_a_sentence_not_a_tuple():
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

    off = BleakBluetoothNotAvailableError(
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again.",
        BleakBluetoothNotAvailableReason.POWERED_OFF,
    )
    assert "POWERED_OFF" in str(off)  # what used to be shown
    assert describe_bluetooth_error(off) == "Bluetooth radio is not powered on. Turn on Bluetooth and try again."
    assert describe_bluetooth_error(asyncio.TimeoutError()).startswith("timed out - is the pad switched on")
    assert describe_bluetooth_error(TimeoutError(), timeout_hint="") == "timed out"
    assert describe_bluetooth_error(OSError("adapter unplugged")) == "adapter unplugged"
    assert describe_bluetooth_error(RuntimeError()) == "RuntimeError"  # never an empty reason

    driver, pad, backend, events = rig()

    async def scenario():
        backend.scan_error = off
        with pytest.raises(TreadmillError) as scan:
            await driver.connect()
        assert str(scan.value) == "Bluetooth scan failed: " + off.args[0] == driver.status.detail
        with pytest.raises(TreadmillError) as listing:
            await driver.discover(1.0)  # the dashboard's Scan button and "walka scan"
        assert str(listing.value) == off.args[0]

    run(scenario())


def test_a_connection_time_out_says_what_to_check():
    driver, pad, backend, events = rig()

    async def scenario():
        make_client = backend.client

        def slow_client(device, on_disconnect):
            client = make_client(device, on_disconnect)

            async def connect():
                raise asyncio.TimeoutError()  # what bleak raises: no text at all

            client.connect = connect
            return client

        backend.client = slow_client
        with pytest.raises(TreadmillError) as info:
            await driver.connect()
        assert str(info.value) == (
            "connection failed: timed out - is the pad switched on and in range, and is the UREVO phone app closed?"
        )
        assert driver.status.detail == str(info.value)

    run(scenario())


def test_heart_rate_sensor_failures_are_readable_too(monkeypatch):
    import bleak
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

    from walka.hr import HeartRateMonitor

    monitor = HeartRateMonitor(enabled=True)
    failed = asyncio.Event()

    async def connect_once():
        failed.set()
        raise asyncio.TimeoutError()

    monitor._connect_once = connect_once

    class Scanner:
        @staticmethod
        async def discover(timeout: float, return_adv: bool):
            raise BleakBluetoothNotAvailableError(
                "No Bluetooth adapter found", BleakBluetoothNotAvailableReason.NO_BLUETOOTH
            )

    monkeypatch.setattr(bleak, "BleakScanner", Scanner)

    async def scenario():
        keeping_connected = asyncio.create_task(monitor.run())
        await asyncio.wait_for(failed.wait(), SOON)
        await monitor.close()
        await asyncio.wait_for(keeping_connected, SOON)
        with pytest.raises(RuntimeError) as scan:
            await monitor.discover(1.0)  # the dashboard's "find my sensor"
        return str(scan.value)

    assert run(scenario()) == "No Bluetooth adapter found"
    assert monitor.detail == (
        "heart-rate sensor error: timed out - is the sensor on, in range and broadcasting its heart rate?"
    )
