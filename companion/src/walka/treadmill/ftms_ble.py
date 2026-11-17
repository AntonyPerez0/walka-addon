"""Bluetooth FTMS treadmill driver (bleak).

Written for UREVO walking pads (Strol 2E "URTM030" and siblings), which
speak plain FTMS, but it should drive any treadmill that implements the
Fitness Machine Service with a speed target.

How UREVO pads behave (from hardware captures by other open-source
projects - see docs/HARDWARE.md):

* They advertise their model code as the name ("URTM030") and need no
  pairing. Only one central can be connected, so the UREVO phone app must be
  closed and the pad must not be paired in Windows Settings.
* Sequence: enable Control Point indications, write Request Control (00),
  Start (07), wait for the belt to move (3-2-1 countdown, then minimum
  speed), then Set Target Speed (02 + uint16 0.01 km/h).
* Speed writes are ignored while the belt is stopped; 0 is not a valid speed.
* US firmware thinks in 0.1 mph steps (see ``mph_tenths_to_command_kmh``).
* A timed-out command may still have been applied, so motor commands are
  never blindly retried.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .. import ftms
from .base import (
    KMH_PER_MPH,
    BeltState,
    Treadmill,
    TreadmillError,
    TreadmillEvent,
    kmh_to_mph_tenths,
    mph_tenths_to_command_kmh,
)

log = logging.getLogger(__name__)

DEFAULT_NAME_PREFIXES: tuple[str, ...] = ("URTM",)

# After one of our own start/stop commands, status events for this long are
# attributed to us rather than to someone pressing a button on the machine.
OWN_EVENT_WINDOW_S = 6.0


@dataclass(frozen=True)
class FoundDevice:
    """A treadmill candidate seen while scanning."""

    name: str
    address: str
    rssi: int | None
    ftms: bool  # advertises the Fitness Machine Service
    handle: Any = None  # backend-specific device object

    def to_dict(self) -> dict:
        return {"name": self.name, "address": self.address, "rssi": self.rssi, "ftms": self.ftms}


class BleClient(Protocol):
    """The subset of bleak.BleakClient the driver uses."""

    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> Any: ...

    async def disconnect(self) -> Any: ...

    def has_characteristic(self, uuid: str) -> bool: ...

    def characteristic_properties(self, uuid: str) -> Sequence[str]: ...

    async def read(self, uuid: str) -> bytes: ...

    async def write(self, uuid: str, data: bytes, response: bool) -> None: ...

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None: ...


class BleBackend(Protocol):
    async def discover(self, timeout: float) -> list[FoundDevice]: ...

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None: ...

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleClient: ...


def is_treadmill_candidate(name: str | None, service_uuids: Sequence[str], prefixes: Sequence[str]) -> bool:
    """True for devices worth offering: FTMS advertisers and known names."""
    if any(u.lower() == ftms.FTMS_SERVICE for u in service_uuids):
        return True
    upper = (name or "").upper()
    return any(upper.startswith(p.upper()) for p in prefixes if p)


class FtmsTreadmill(Treadmill):
    kind = "ftms"

    def __init__(
        self,
        address: str | None = None,
        *,
        name_prefixes: Sequence[str] = DEFAULT_NAME_PREFIXES,
        units: str = "auto",  # "auto" | "mph" | "kmh": the firmware's speed grid
        scan_timeout_s: float = 15.0,
        command_timeout_s: float = 3.0,
        backend: BleBackend | None = None,
    ) -> None:
        super().__init__()
        self.address = address or None
        self.name_prefixes = tuple(name_prefixes)
        self.units = units
        self.scan_timeout_s = scan_timeout_s
        self.command_timeout_s = command_timeout_s
        self._backend = backend
        self._client: BleClient | None = None
        self._lock = asyncio.Lock()
        self._response: asyncio.Future[ftms.ControlResponse] | None = None
        self._has_control = False
        self._write_with_response = True
        self._own_stop_until = 0.0
        self._own_start_until = 0.0
        self._data = ftms.TreadmillData()
        self.features: ftms.MachineFeatures | None = None
        self.raw_speed_range: ftms.SpeedRange | None = None

    # ---- backend ---------------------------------------------------------------
    def _get_backend(self) -> BleBackend:
        if self._backend is None:
            from .bleak_backend import BleakBackend  # imported lazily: needs bleak

            self._backend = BleakBackend()
        return self._backend

    async def discover(self, timeout: float = 6.0) -> list[FoundDevice]:
        """Scan for treadmill candidates (does not connect)."""
        found = await self._get_backend().discover(timeout)
        return [d for d in found if d.ftms or is_treadmill_candidate(d.name, (), self.name_prefixes)]

    # ---- connection ------------------------------------------------------------
    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        status = self.status
        status.detail = "scanning"
        backend = self._get_backend()
        try:
            device = await backend.find(self.address, self.name_prefixes, self.scan_timeout_s)
        except Exception as exc:  # bleak raises assorted OS errors
            status.detail = f"Bluetooth scan failed: {exc}"
            raise TreadmillError(status.detail) from exc
        if device is None:
            status.detail = (
                "treadmill not found - is it switched on, in range, and is the "
                "UREVO phone app closed? (only one device can connect at a time)"
            )
            raise TreadmillError(status.detail)

        status.detail = f"connecting to {device.name or device.address}"
        client = backend.client(device, self._on_disconnect)
        try:
            await client.connect()
            if not client.has_characteristic(ftms.CONTROL_POINT) or not client.has_characteristic(ftms.TREADMILL_DATA):
                raise TreadmillError(f"{device.name or device.address} does not expose the FTMS treadmill service")
            self._client = client
            await self._setup(client)
        except Exception as exc:
            self._client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            status.connected = False
            status.detail = f"connection failed: {exc}"
            if isinstance(exc, TreadmillError):
                raise
            raise TreadmillError(status.detail) from exc

        status.name = device.name or "treadmill"
        status.address = device.address
        status.connected = True
        status.state = BeltState.UNKNOWN
        status.detail = "connected"
        status.updated_at = time.monotonic()
        log.info(
            "connected to %s (%s): speed %.2f-%.2f km/h step %.3f%s",
            status.name,
            status.address,
            status.min_kmh,
            status.max_kmh,
            status.step_kmh,
            " (mph firmware)" if status.mph_grid else "",
        )
        self._emit(TreadmillEvent.CONNECTED)

    async def _setup(self, client: BleClient) -> None:
        self._has_control = False
        self._data = ftms.TreadmillData()
        props = [p.lower() for p in client.characteristic_properties(ftms.CONTROL_POINT)]
        # Prefer write-with-response when the characteristic offers it.
        self._write_with_response = "write" in props or "write-without-response" not in props

        if client.has_characteristic(ftms.FITNESS_MACHINE_FEATURE):
            try:
                self.features = ftms.parse_features(await client.read(ftms.FITNESS_MACHINE_FEATURE))
            except Exception as exc:
                log.debug("could not read FTMS features: %s", exc)
        speed_range = None
        if client.has_characteristic(ftms.SUPPORTED_SPEED_RANGE):
            try:
                speed_range = ftms.parse_supported_speed_range(await client.read(ftms.SUPPORTED_SPEED_RANGE))
            except Exception as exc:
                log.debug("could not read the supported speed range: %s", exc)
        self.raw_speed_range = speed_range
        self._apply_speed_range(speed_range)

        # Register the indication handler before the first write: some
        # devices indicate before the write call returns.
        await client.subscribe(ftms.CONTROL_POINT, self._on_control_response)
        await client.subscribe(ftms.TREADMILL_DATA, self._on_data)
        for uuid, handler in (
            (ftms.FITNESS_MACHINE_STATUS, self._on_status),
            (ftms.TRAINING_STATUS, self._on_training_status),
        ):
            if client.has_characteristic(uuid):
                try:
                    await client.subscribe(uuid, handler)
                except Exception as exc:
                    log.debug("could not subscribe to %s: %s", uuid, exc)

    def _apply_speed_range(self, speed_range: ftms.SpeedRange | None) -> None:
        status = self.status
        mph = self.units == "mph"
        if self.units == "auto" and speed_range is not None:
            # 0.1 mph is 0.16 km/h: the tell-tale of US firmware.
            mph = 0.15 <= speed_range.step_kmh <= 0.17
        status.mph_grid = mph
        if speed_range is not None and speed_range.max_kmh > speed_range.min_kmh > 0:
            lo, hi = speed_range.min_kmh, speed_range.max_kmh
            step = speed_range.step_kmh if speed_range.step_kmh > 0 else 0.1
        elif mph:
            lo, hi, step = 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH, 0.1 * KMH_PER_MPH
        else:
            lo, hi, step = 1.0, 6.0, 0.1
        if mph:
            # The range is reported as truncated km/h (0.96 for 0.6 mph):
            # recover the exact mph grid.
            status.min_kmh = round(lo / KMH_PER_MPH, 1) * KMH_PER_MPH
            status.max_kmh = round(hi / KMH_PER_MPH, 1) * KMH_PER_MPH
            status.step_kmh = 0.1 * KMH_PER_MPH
        else:
            status.min_kmh, status.max_kmh, status.step_kmh = lo, hi, step

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:
                log.debug("disconnect failed: %s", exc)
        self._mark_disconnected("disconnected")

    def _on_disconnect(self) -> None:
        self._client = None
        self._mark_disconnected("connection lost")

    def _mark_disconnected(self, detail: str) -> None:
        status = self.status
        was = status.connected
        status.connected = False
        status.state = BeltState.UNKNOWN
        status.speed_kmh = 0.0
        status.target_kmh = None
        status.detail = detail
        self._has_control = False
        if self._response is not None and not self._response.done():
            self._response.cancel()
        if was:
            self._emit(TreadmillEvent.DISCONNECTED)

    # ---- notifications ---------------------------------------------------------
    def _on_control_response(self, data: bytes) -> None:
        response = ftms.parse_control_response(bytes(data))
        if response is None:
            return
        if self._response is not None and not self._response.done():
            self._response.set_result(response)

    def _on_data(self, data: bytes) -> None:
        packet = ftms.parse_treadmill_data(bytes(data))
        self._data = self._data.merge(packet)
        status = self.status
        now = time.monotonic()
        status.updated_at = now
        if packet.speed_kmh is not None:
            status.speed_kmh = packet.speed_kmh
            if packet.speed_kmh > 0.05:
                status.state = BeltState.RUNNING
            elif status.state is BeltState.RUNNING and now > self._own_start_until:
                # Moving -> not moving without a status event; the governor
                # decides whether this is a stall.
                pass
        if packet.total_distance_m is not None:
            status.distance_m = float(packet.total_distance_m)
        if packet.total_energy_kcal is not None:
            status.energy_kcal = float(packet.total_energy_kcal)
        if packet.elapsed_time_s is not None:
            status.elapsed_s = packet.elapsed_time_s
        if packet.vendor_steps is not None:
            status.steps = packet.vendor_steps
        if packet.heart_rate_bpm:
            status.heart_rate_bpm = packet.heart_rate_bpm

    def _on_status(self, data: bytes) -> None:
        event = ftms.parse_machine_status(bytes(data))
        if event is None:
            return
        log.debug("machine status: %s", event.describe())
        status = self.status
        now = time.monotonic()
        if event.op == ftms.StatusOp.TARGET_SPEED_CHANGED and event.target_speed_kmh is not None:
            status.target_kmh = event.target_speed_kmh
        elif event.started:
            if status.state is not BeltState.RUNNING:
                status.state = BeltState.STARTING
            if now > self._own_start_until:
                self._emit(TreadmillEvent.EXTERNAL_START)
        elif event.paused or event.stopped:
            status.state = BeltState.PAUSED if event.paused else BeltState.STOPPED
            status.target_kmh = None
            if now > self._own_stop_until:
                self._emit(TreadmillEvent.EXTERNAL_STOP)
        elif event.op == ftms.StatusOp.RESET:
            status.state = BeltState.STOPPED
            status.target_kmh = None
        elif event.op == ftms.StatusOp.CONTROL_PERMISSION_LOST:
            self._has_control = False

    def _on_training_status(self, data: bytes) -> None:
        if len(data) < 2:
            return
        value = data[1]
        status = self.status
        if value == 0x0E and status.state is not BeltState.RUNNING:  # pre-workout: countdown
            status.state = BeltState.STARTING
        elif value == 0x01 and status.speed_kmh <= 0.05 and status.state is BeltState.UNKNOWN:  # idle
            status.state = BeltState.STOPPED

    # ---- commands --------------------------------------------------------------
    def _require_client(self) -> BleClient:
        client = self._client
        if client is None or not client.is_connected:
            raise TreadmillError("treadmill is not connected")
        return client

    async def _write(self, payload: bytes) -> ftms.ControlResponse | None:
        """Write one control-point command and wait for its indication.

        Returns ``None`` if no indication arrives in time: the command may or
        may not have been applied, and the caller must not retry it blindly.
        """
        client = self._require_client()
        loop = asyncio.get_running_loop()
        self._response = loop.create_future()
        try:
            await client.write(ftms.CONTROL_POINT, payload, self._write_with_response)
        except Exception as exc:
            raise TreadmillError(f"Bluetooth write failed: {exc}") from exc
        try:
            return await asyncio.wait_for(self._response, self.command_timeout_s)
        except asyncio.TimeoutError:
            log.debug("no response to control point command %s", payload.hex())
            return None
        except asyncio.CancelledError:
            if self._client is None:
                raise TreadmillError("treadmill disconnected") from None
            raise

    async def _ensure_control(self) -> None:
        if self._has_control:
            return
        response = await self._write(ftms.cmd_request_control())
        # Some pads never acknowledge Request Control; carry on regardless.
        if response is None or response.ok:
            self._has_control = True
        else:
            raise TreadmillError(f"treadmill refused control: {response.describe()}")

    async def _command(self, payload: bytes) -> None:
        async with self._lock:
            await self._ensure_control()
            response = await self._write(payload)
            if response is not None and response.result == ftms.ResultCode.CONTROL_NOT_PERMITTED:
                # Definitely not executed: safe to take control and retry once.
                self._has_control = False
                await self._ensure_control()
                response = await self._write(payload)
            if response is not None and not response.ok:
                raise TreadmillError(f"treadmill rejected command: {response.describe()}")

    async def start(self) -> None:
        self._own_start_until = time.monotonic() + OWN_EVENT_WINDOW_S
        await self._command(ftms.cmd_start_or_resume())
        self._own_start_until = time.monotonic() + OWN_EVENT_WINDOW_S
        if self.status.state is not BeltState.RUNNING:
            self.status.state = BeltState.STARTING

    async def pause(self) -> None:
        self._own_stop_until = time.monotonic() + OWN_EVENT_WINDOW_S
        await self._command(ftms.cmd_pause())
        self._own_stop_until = time.monotonic() + OWN_EVENT_WINDOW_S
        self.status.state = BeltState.PAUSED
        self.status.target_kmh = None

    async def stop(self) -> None:
        self._own_stop_until = time.monotonic() + OWN_EVENT_WINDOW_S
        await self._command(ftms.cmd_stop())
        self._own_stop_until = time.monotonic() + OWN_EVENT_WINDOW_S
        self.status.state = BeltState.STOPPED
        self.status.target_kmh = None

    def quantise(self, kmh: float) -> tuple[float, float]:
        """Return ``(speed the belt will run at, value to send)`` for a target."""
        status = self.status
        kmh = max(status.min_kmh, min(status.max_kmh, kmh))
        if status.mph_grid:
            tenths = kmh_to_mph_tenths(kmh)
            tenths = max(kmh_to_mph_tenths(status.min_kmh), min(kmh_to_mph_tenths(status.max_kmh), tenths))
            return tenths / 10.0 * KMH_PER_MPH, mph_tenths_to_command_kmh(tenths)
        step = status.step_kmh if status.step_kmh > 0 else 0.1
        snapped = round(round(kmh / step) * step, 2)
        snapped = max(status.min_kmh, min(status.max_kmh, snapped))
        return snapped, snapped

    async def set_speed(self, kmh: float) -> None:
        actual, send = self.quantise(kmh)
        await self._command(ftms.cmd_set_target_speed(send))
        self.status.target_kmh = actual
