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

log = logging.getLogger(__name__)

FRESH_S = 10.0  # a reading older than this is not used
RETRY_S = 15.0


class HeartRateMonitor:
    def __init__(
        self,
        *,
        enabled: bool = False,
        address: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.address = address
        self.clock = clock
        self.bpm: int | None = None
        self.name = ""
        self.connected = False
        self.detail = "off"
        self._updated_at = 0.0
        self._client: Any = None
        self._changed = asyncio.Event()
        self._closing = False

    # ---- state -----------------------------------------------------------------
    @property
    def fresh(self) -> bool:
        """A recent, plausible reading is available."""
        return (
            self.connected
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
        }

    def configure(self, *, enabled: bool, address: str) -> None:
        """Change the sensor (from the dashboard); the run loop reconnects."""
        self.enabled = enabled
        self.address = address
        self._changed.set()

    def feed(self, data: bytes) -> None:
        """Handle a Heart Rate Measurement notification."""
        bpm = ftms.parse_heart_rate(bytes(data))
        if bpm is not None and bpm > 0:
            self.bpm = bpm
            self._updated_at = self.clock()

    # ---- Bluetooth -------------------------------------------------------------
    async def discover(self, timeout: float = 6.0) -> list[dict[str, Any]]:
        """Nearby devices advertising the Heart Rate service."""
        from bleak import BleakScanner

        from .treadmill.bleak_backend import prepare_windows_threading

        prepare_windows_threading()
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        devices = []
        for device, adv in found.values():
            uuids = [u.lower() for u in adv.service_uuids]
            if ftms.HEART_RATE_SERVICE in uuids:
                devices.append(
                    {"name": adv.local_name or device.name or "", "address": device.address, "rssi": adv.rssi}
                )
        devices.sort(key=lambda d: -(d["rssi"] or -999))
        return devices

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
            self._client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            self.detail = "disconnected"

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
                self.detail = f"heart-rate sensor error: {exc}"
                log.debug("heart-rate connection failed: %s", exc)
            if self._closing:
                break
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=RETRY_S)
            except asyncio.TimeoutError:
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
