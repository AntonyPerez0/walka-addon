"""Optional heart-rate input from any Bluetooth heart-rate sensor.

Works with chest straps and arm bands, and with watches that can broadcast
heart rate as a standard Bluetooth sensor (the standard Heart Rate service,
0x180D). Heart rate is shown on the dashboard, stored with each walk, and -
when age and sex are set in the profile - used for the calorie estimate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from . import ftms
from .treadmill.ftms_ble import describe_bluetooth_error

log = logging.getLogger(__name__)

FRESH_S = 10.0  # a reading older than this is not used
RETRY_S = 15.0
SENSOR_TIMEOUT_HINT = "is the sensor on, in range and broadcasting its heart rate?"

# Name fragments of watches and straps that can act as a Bluetooth
# heart-rate sensor (lower case).
_SENSOR_NAMES = (
    "instinct", "fenix", "forerunner", "venu", "vivoactive", "garmin", "hrm",
    "polar", "h10", "h9", "oh1", "verity", "wahoo", "tickr", "coospo", "hw9", "h808",
    "magene", "scosche", "rhythm", "heart",
)  # fmt: skip


def looks_like_heart_rate_sensor(name: str) -> bool:
    lowered = (name or "").lower()
    return any(fragment in lowered for fragment in _SENSOR_NAMES)


class HeartRateMonitor:
    def __init__(
        self,
        *,
        enabled: bool = False,
        address: str = "",
        name: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.address = address  # the chosen sensor, remembered (also while turned off)
        self.clock = clock
        self.bpm: int | None = None
        self.name = name
        self.contact: bool | None = None  # touching the skin, if the sensor says
        self.battery: int | None = None  # percent, if the sensor says
        self.connected = False
        self.detail = "off"
        self._updated_at = 0.0
        self._network_at: float | None = None
        self._client: Any = None
        self._changed = asyncio.Event()
        self._closing = False

    # ---- state -----------------------------------------------------------------
    @property
    def network_live(self) -> bool:
        """Readings are arriving over the local network (hr_network.py)."""
        return self._network_at is not None and self.clock() - self._network_at <= FRESH_S

    @property
    def fresh(self) -> bool:
        """A recent, plausible reading is available."""
        return (
            (self.connected or self.network_live)
            and self.bpm is not None
            and 25 <= self.bpm <= 240
            and self.clock() - self._updated_at <= FRESH_S
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "name": self.name,
            "address": self.address,
            "bpm": self.bpm if self.fresh else None,
            "detail": self.detail,
            "contact": self.contact,
            "battery": self.battery,
            "source": "bluetooth" if self.connected else "network" if self.network_live else None,
        }

    def configure(self, *, enabled: bool, address: str) -> None:
        """Change the sensor (from the dashboard); the run loop reconnects."""
        self.enabled = enabled
        self.address = address
        self._changed.set()

    def feed(self, data: bytes) -> None:
        """Handle a Heart Rate Measurement notification. A reading the sensor
        marks as taken without skin contact is not one: it is dropped."""
        measurement = ftms.parse_heart_rate_measurement(bytes(data))
        if measurement is None:
            return
        self.contact = measurement.contact
        if measurement.contact is False:
            self.bpm = None
            if self.connected:
                self.detail = "connected, but the sensor is not touching the skin"
            return
        if self.connected and self.detail != "connected":
            self.detail = "connected"
        if measurement.bpm > 0:
            self.bpm = measurement.bpm
            self._updated_at = self.clock()

    def feed_network(self, bpm: int) -> None:
        """A heart rate from a watch app, over the local network. A Bluetooth
        sensor that is connected and reading comes first."""
        if self.connected and self.bpm is not None and self.clock() - self._updated_at <= FRESH_S:
            return
        self.bpm = bpm
        now = self.clock()
        self._updated_at = self._network_at = now

    def feed_battery(self, data: bytes) -> None:
        if data:
            self.battery = max(0, min(100, data[0]))

    # ---- Bluetooth -------------------------------------------------------------
    async def discover(self, timeout: float = 6.0, *, all_devices: bool = False) -> list[dict[str, Any]]:
        """Nearby heart-rate sensors: devices that advertise the Heart Rate
        service, plus ones whose name says they are a watch or strap (some
        only reveal the service once connected). ``all_devices``: every
        Bluetooth device heard. The remembered sensor is marked, and first."""
        from bleak import BleakScanner

        from .treadmill.bleak_backend import prepare_windows_threading

        prepare_windows_threading()
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        except Exception as exc:  # Bluetooth switched off, no adapter: say so in plain words
            raise RuntimeError(describe_bluetooth_error(exc, SENSOR_TIMEOUT_HINT)) from exc
        devices = []
        for device, adv in found.values():
            name = adv.local_name or device.name or ""
            advertises = ftms.HEART_RATE_SERVICE in [u.lower() for u in adv.service_uuids]
            likely = advertises or looks_like_heart_rate_sensor(name)
            if likely or all_devices:
                devices.append(
                    {
                        "name": name,
                        "address": device.address,
                        "rssi": adv.rssi,
                        "hr": advertises,
                        "candidate": likely,
                        "remembered": bool(self.address) and device.address.upper() == self.address.upper(),
                    }
                )
        devices.sort(key=lambda d: (not d["remembered"], not d["hr"], not d["candidate"], -(d["rssi"] or -999)))
        return devices

    async def peek(self, address: str, seconds: float = 5.0) -> int | None:
        """One sensor's heart rate right now, for the scan list: connect,
        listen for a few seconds, let go. (The sensor in use: its reading.)"""
        if self.connected and address.upper() == (self.address or "").upper():
            return self.bpm if self.fresh else None
        from bleak import BleakClient

        from .treadmill.bleak_backend import prepare_windows_threading

        prepare_windows_threading()
        heard: list[int] = []
        got = asyncio.Event()

        def take(_char: Any, data: bytearray) -> None:
            measurement = ftms.parse_heart_rate_measurement(bytes(data))
            if measurement is not None and measurement.contact is not False and measurement.bpm > 0:
                heard.append(measurement.bpm)
                got.set()

        async with BleakClient(address, timeout=15.0) as client:
            if client.services.get_characteristic(ftms.HEART_RATE_MEASUREMENT) is None:
                return None
            await client.start_notify(ftms.HEART_RATE_MEASUREMENT, take)
            try:
                await asyncio.wait_for(got.wait(), seconds)
            except TimeoutError:
                pass
        return heard[-1] if heard else None

    async def _connect_once(self) -> None:
        from bleak import BleakClient, BleakScanner

        from .treadmill.bleak_backend import prepare_windows_threading

        prepare_windows_threading()
        wanted = self.address.upper()

        def match(device: Any, adv: Any) -> bool:
            if wanted:
                return device.address.upper() == wanted
            return ftms.HEART_RATE_SERVICE in [u.lower() for u in adv.service_uuids]

        self.detail = "searching for the heart-rate sensor"
        device = await BleakScanner.find_device_by_filter(match, timeout=12.0)
        if device is None:
            self.detail = "heart-rate sensor not found - is it broadcasting?"
            return
        dropped = asyncio.Event()
        client = BleakClient(device, disconnected_callback=lambda _c: dropped.set(), timeout=20.0)
        self.detail = f"connecting to {device.name or device.address}"
        await client.connect()
        self._client = client
        try:
            await client.start_notify(ftms.HEART_RATE_MEASUREMENT, lambda _char, data: self.feed(bytes(data)))
            await self._battery(client)
            self.connected = True
            self.name = device.name or device.address
            self.detail = "connected"
            log.info("heart-rate sensor connected: %s", self.name)
            waiters = [asyncio.create_task(dropped.wait()), asyncio.create_task(self._changed.wait())]
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
        finally:
            self.connected = False
            self.bpm = None
            self.contact = None
            self._client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            self.detail = "disconnected"

    async def _battery(self, client: Any) -> None:
        """The sensor's battery level, if it has one: read once, then followed."""
        battery = client.services.get_characteristic(ftms.BATTERY_LEVEL)
        if battery is None:
            return
        try:
            if "read" in battery.properties:
                self.feed_battery(bytes(await client.read_gatt_char(battery)))
            if "notify" in battery.properties:
                await client.start_notify(battery, lambda _char, data: self.feed_battery(bytes(data)))
        except Exception as exc:  # a battery level is a nicety
            log.debug("reading the heart-rate sensor's battery failed: %s", exc)

    async def run(self) -> None:
        """Keep the sensor connected while enabled. Runs until cancelled."""
        while not self._closing:
            self._changed.clear()
            if not self.enabled:
                self.detail = "off"
                await self._changed.wait()
                continue
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.detail = f"heart-rate sensor error: {describe_bluetooth_error(exc, SENSOR_TIMEOUT_HINT)}"
                log.debug("heart-rate connection failed: %s", self.detail)
            if self._closing:
                break
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=RETRY_S)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self._closing = True
        self._changed.set()
        client = self._client
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
