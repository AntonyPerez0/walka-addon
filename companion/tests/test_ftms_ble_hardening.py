"""The Bluetooth reader when things go wrong at an awkward moment: a link
that drops while connecting, a speed range that makes no sense, a failure
that has to be put into words.

Same emulated pad as test_ftms_ble.py.
"""

from __future__ import annotations

import asyncio

import pytest

from test_ftms_ble import rig, run
from walka import ftms
from walka.treadmill import ftms_ble
from walka.treadmill.base import KMH_PER_MPH, TreadmillError, TreadmillEvent
from walka.treadmill.ftms_ble import describe_bluetooth_error
from walka_fakes import FakePad

SOON = 2.0  # the deadline a test that must not hang is held to


# ---- a link that drops while connecting ----------------------------------------------------


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

    run(scenario())
    assert driver.status.connected and events == [TreadmillEvent.CONNECTED]
    assert pad.writes == [] and pad.connects == 2


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
        assert driver.status.connected and new.is_connected  # the current connection is untouched
        new.on_disconnect()
        assert not driver.status.connected

    run(scenario())
    assert events == [
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
        TreadmillEvent.CONNECTED,
        TreadmillEvent.DISCONNECTED,
    ]


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
        with pytest.raises(TreadmillError, match="timed out - is the treadmill switched on"):
            await asyncio.wait_for(driver.connect(), SOON)
        assert pad.client is None  # let go of, so that the next attempt can connect

    run(scenario())
    assert not driver.status.connected and events == []


# ---- the advertised speed range ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,units,expected",
    [
        # A pad that reports a minimum of zero: its maximum is still its maximum.
        ((0, 400, 10), "auto", (False, 1.0, 4.0, 0.1)),
        ((0, 643, 16), "auto", (True, 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH, 0.1 * KMH_PER_MPH)),
        ((0, 400, 0), "auto", (False, 1.0, 4.0, 0.1)),
        # A maximum below the default minimum: the minimum is never shown above it.
        ((0, 80, 10), "auto", (False, 0.8, 0.8, 0.1)),
        # Nonsense (minimum above maximum): it can only mean the maximum.
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


# ---- reasons a person can act on ----------------------------------------------------------------


def test_bluetooth_switched_off_reads_as_a_sentence_not_a_tuple():
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

    off = BleakBluetoothNotAvailableError(
        "Bluetooth radio is not powered on. Turn on Bluetooth and try again.",
        BleakBluetoothNotAvailableReason.POWERED_OFF,
    )
    assert "POWERED_OFF" in str(off)  # what used to be shown
    assert describe_bluetooth_error(off) == "Bluetooth radio is not powered on. Turn on Bluetooth and try again."
    assert describe_bluetooth_error(TimeoutError()).startswith("timed out - is the treadmill switched on")
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
                raise TimeoutError()  # what bleak raises: no text at all

            client.connect = connect
            return client

        backend.client = slow_client
        with pytest.raises(TreadmillError) as info:
            await driver.connect()
        assert str(info.value) == (
            "connection failed: timed out - is the treadmill switched on and in range, and is its own phone app closed?"
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
        raise TimeoutError()

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
