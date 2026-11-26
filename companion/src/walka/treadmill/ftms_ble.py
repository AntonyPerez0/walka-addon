"""Bluetooth FTMS treadmill reader (bleak).

Listens to any treadmill that implements the Fitness Machine Service: its
Treadmill Data (speed, distance, and on some pads steps), its status events
and its training status. It was written around UREVO walking pads (Strol 2E
"URTM030" and siblings); a treadmill of another make is found by the
service it advertises (see ``known.py`` for the names that are recognised
at once, and docs/HARDWARE.md).

**It only listens.** This reader never writes to a characteristic: it does
not ask the treadmill for control, and it has no start, stop, pause or
speed command to send. (Two readers built on it, for vendor protocols
whose pads report only when asked, send one fixed status query each, and
nothing else: proprietary.py, query_link.py.) The Fitness Machine Control Point is not even
subscribed to. What the reader does on a connection is read one value
(the speed range) and switch notifications on for the three things it
listens to - and ``BleClient`` below, the only door to the Bluetooth
link, has no method that could write anything else.

How UREVO pads behave (from hardware captures by other open-source
projects - see docs/HARDWARE.md):

* They advertise their model code as the name ("URTM030") and need no
  pairing. Only one central can be connected, so the maker's phone app must
  be closed and the pad must not be paired in Windows Settings.
* US firmware thinks in 0.1 mph steps, and reports its speed range in
  truncated km/h.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .. import ftms
from .base import KMH_PER_MPH, BeltState, Treadmill, TreadmillError, TreadmillEvent
from .known import DEFAULT_NAME_PREFIXES

log = logging.getLogger(__name__)

# After a stop or pause the belt runs down for a moment and still reports
# a speed: for this long that does not make it "running" again.
COAST_S = 6.0

# Cheap Bluetooth modules drop requests that arrive back to back: leave a
# gap between subscriptions.
SUBSCRIBE_GAP_S = 0.15

# The reads and subscriptions made while connecting are given up on after
# this long (a dying link).
SETUP_TIMEOUT_S = 20.0

# What a connect says when it was given up on: Disconnect was pressed, or
# another reader was chosen, while the connection was being made.
LET_GO = "let go of while the connection was being made"

PAD_TIMEOUT_HINT = "is the treadmill switched on and in range, and is its own phone app closed?"

# Training Status values (second byte of 0x2AD3).
TRAINING_IDLE = 0x01
TRAINING_PRE_WORKOUT = 0x0E  # countdown


@dataclass(frozen=True)
class FoundDevice:
    """A treadmill candidate seen while scanning."""

    name: str
    address: str
    rssi: int | None
    ftms: bool  # advertises the Fitness Machine Service
    handle: Any = None  # backend-specific device object
    # What kind of fitness machine the advertisement says it is: True for a
    # treadmill, False for something else (a bike, a rower), None if it does
    # not say.
    treadmill: bool | None = None

    def to_dict(self, candidate: bool | None = None) -> dict:
        data = {"name": self.name, "address": self.address, "rssi": self.rssi, "ftms": self.ftms}
        if candidate is not None:
            data["candidate"] = candidate  # (in a scan of all devices: one Walka would take for a treadmill)
        return data


class BleClient(Protocol):
    """The subset of bleak.BleakClient the reader uses. There is no write
    here, and that is the point: see the top of this file."""

    @property
    def is_connected(self) -> bool: ...

    async def connect(self) -> Any: ...

    async def disconnect(self) -> Any: ...

    def has_characteristic(self, uuid: str) -> bool: ...

    async def read(self, uuid: str) -> bytes: ...

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None: ...


class BleBackend(Protocol):
    async def discover(self, timeout: float) -> list[FoundDevice]: ...

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        """The treadmill to connect to: the device with ``address`` if one is
        given, otherwise one whose name starts with a prefix - and only if no
        such name is heard during the whole ``timeout``, a device that merely
        advertises the Fitness Machine Service (and does not say that it is
        some other kind of machine than a treadmill)."""
        ...

    async def identify(self, device: FoundDevice) -> tuple[list[str], list[str]]:
        """Connect, list the services and characteristics, let go. Writes nothing."""
        ...

    async def watch(self, address: str, on_seen: Callable[[], None]) -> None:
        """Call ``on_seen`` for every advertisement from ``address`` until
        cancelled. Listens only: connects to nothing."""
        ...

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleClient: ...


def name_matches(name: str | None, prefixes: Sequence[str]) -> bool:
    """Does a Bluetooth name start with one of the configured prefixes?"""
    upper = (name or "").upper()
    return any(upper.startswith(p.upper()) for p in prefixes if p)


def is_treadmill_candidate(
    name: str | None,
    service_uuids: Sequence[str],
    prefixes: Sequence[str],
    treadmill: bool | None = None,
) -> bool:
    """True for devices worth offering: known names, and FTMS advertisers
    that do not say they are another kind of machine (``treadmill`` is what
    the advertisement says - see ``FoundDevice``)."""
    if name_matches(name, prefixes):
        return True
    if treadmill is False:
        return False  # a bike trainer, a rower: there is no belt to read
    return any(u.lower() == ftms.FTMS_SERVICE for u in service_uuids)


def describe_bluetooth_error(exc: BaseException, timeout_hint: str = PAD_TIMEOUT_HINT) -> str:
    """The reason for a Bluetooth failure, in words a person can act on.

    Two failures are far more common than all others and read badly when
    simply printed: a time-out has no text at all, and bleak's "Bluetooth is
    not available" (radio switched off, no adapter) prints as a Python tuple.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"timed out - {timeout_hint}" if timeout_hint else "timed out"
    args = getattr(exc, "args", ())
    if type(exc).__name__ == "BleakBluetoothNotAvailableError" and args and isinstance(args[0], str):
        return args[0]  # (message, reason): the message is plain English
    return str(exc).strip() or type(exc).__name__


class FtmsTreadmill(Treadmill):
    kind = "ftms"
    # The characteristic the walk comes from; a device without it is refused.
    # (A reader of another protocol - treadmill/rsc.py - changes these two and
    # _setup, and keeps the rest: finding, connecting, a lost link.)
    DATA_CHARACTERISTIC = ftms.TREADMILL_DATA
    NOT_READABLE = "does not send FTMS treadmill data"

    def __init__(
        self,
        address: str | None = None,
        *,
        name_prefixes: Sequence[str] = DEFAULT_NAME_PREFIXES,
        units: str = "auto",  # "auto" | "mph" | "kmh": the firmware's speed grid
        scan_timeout_s: float = 15.0,
        backend: BleBackend | None = None,
    ) -> None:
        super().__init__()
        self.address = address or None
        self.name_prefixes = tuple(name_prefixes)
        self.units = units
        self.scan_timeout_s = scan_timeout_s
        self._backend = backend
        self._client: BleClient | None = None
        # A connection being made (until it is set up), and how many times
        # the reader was told to let go: a connect under way gives up when
        # that changes, so that Disconnect (or another reader) is not
        # undone by a connection that finishes afterwards.
        self._connecting: BleClient | None = None
        self._let_go = 0
        self._coast_until = 0.0  # after a stop/pause: speed reports are the belt running down
        self._data = ftms.TreadmillData()
        self.raw_speed_range: ftms.SpeedRange | None = None

    # ---- backend ---------------------------------------------------------------
    @property
    def speed_range_known(self) -> bool:  # type: ignore[override]
        return self.raw_speed_range is not None

    def _get_backend(self) -> BleBackend:
        if self._backend is None:
            from .bleak_backend import BleakBackend  # imported lazily: needs bleak

            self._backend = BleakBackend()
        return self._backend

    async def discover(self, timeout: float = 6.0, *, all_devices: bool = False) -> list[FoundDevice]:
        """Scan for treadmill candidates (does not connect). ``all_devices``:
        every Bluetooth device heard, candidates first."""
        try:
            found = await self._get_backend().discover(timeout)
        except Exception as exc:  # no adapter, Bluetooth switched off, ...
            raise TreadmillError(describe_bluetooth_error(exc)) from exc
        if all_devices:
            return sorted(found, key=lambda d: not self.is_candidate(d))
        return [d for d in found if self.is_candidate(d)]

    def is_candidate(self, device: FoundDevice) -> bool:
        """A treadmill for this reader - or a pad Walka knows by name that another reader reads."""
        from .known import known_treadmill

        services = (ftms.FTMS_SERVICE,) if device.ftms else ()
        if is_treadmill_candidate(device.name, services, self.name_prefixes, device.treadmill):
            return True
        return known_treadmill(device.name) is not None

    async def identify(self, address: str) -> dict[str, Any]:
        """Connect to any device, see whether Walka can read it as a treadmill,
        and let go. For "show all Bluetooth devices" in the dashboard."""
        from .readers import reader_for, service_words

        backend = self._get_backend()
        try:
            device = await backend.find(address, (), self.scan_timeout_s)
            if device is None:
                raise TreadmillError("not found - is it switched on and in range?")
            services, characteristics = await backend.identify(device)
        except TreadmillError:
            raise
        except Exception as exc:
            raise TreadmillError(describe_bluetooth_error(exc, "is it switched on and in range?")) from exc
        reader = reader_for(characteristics, device.name)
        return {
            "name": device.name,
            "address": device.address,
            "readable": reader is not None,
            "reader": reader.key if reader else None,
            "how": reader.words if reader else "",
            "services": service_words(services),
        }

    async def watch(self, on_seen: Callable[[], None]) -> None:
        """Listen for this treadmill's advertisements (never connecting) and
        call ``on_seen`` for each, until cancelled. Needs an address: only the
        treadmill Walka is tied to is listened for."""
        if not self.address:
            raise TreadmillError("no treadmill to listen for")
        await self._get_backend().watch(self.address, on_seen)

    # ---- connection ------------------------------------------------------------
    def _gave_up(self, attempt: int) -> None:
        """Raise if the reader was told to let go since ``attempt`` began."""
        if self._let_go != attempt:
            raise TreadmillError(LET_GO)

    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        attempt = self._let_go
        status = self.status
        status.detail = "scanning"
        backend = self._get_backend()
        try:
            device = await backend.find(self.address, self.name_prefixes, self.scan_timeout_s)
        except Exception as exc:  # bleak raises assorted OS errors
            status.detail = f"Bluetooth scan failed: {describe_bluetooth_error(exc)}"
            raise TreadmillError(status.detail) from exc
        self._gave_up(attempt)
        if device is None:
            status.detail = (
                "treadmill not found - is it switched on, in range, and is its own "
                "phone app closed? (most treadmills accept one connection at a time)"
            )
            raise TreadmillError(status.detail)

        if not self._accepts(device):
            status.detail = f"{device.name or device.address} is not a treadmill this reader is for"
            raise TreadmillError(status.detail)
        status.detail = f"connecting to {device.name or device.address}"
        client: BleClient | None = None

        def link_lost() -> None:
            # Tied to this client: a late callback from an earlier connection
            # must not be taken for the loss of the current one.
            self._on_disconnect(client)

        client = self._make_client(backend, device, link_lost)
        self._connecting = client
        try:
            await client.connect()
            self._gave_up(attempt)
            if not any(client.has_characteristic(uuid) for uuid in self.data_characteristics()):
                raise TreadmillError(f"{device.name or device.address} {self.NOT_READABLE}")
            self._client = client
            await asyncio.wait_for(self._setup(client), SETUP_TIMEOUT_S)
            self._gave_up(attempt)
            # The link can drop during set-up without any step failing (the
            # optional subscriptions are allowed to). Saying "connected" then
            # would leave a reader that hears nothing and that nobody
            # reconnects.
            if self._client is not client or not client.is_connected:
                raise TreadmillError("the connection was lost while it was being set up")
        except BaseException as exc:
            given_up = self._let_go != attempt
            if self._client is client:
                self._client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            status.connected = False
            if given_up:
                status.detail = "disconnected"
                if isinstance(exc, Exception):
                    raise TreadmillError(LET_GO) from exc
                raise
            if not isinstance(exc, Exception):
                raise  # (cancelled: the link is let go of all the same)
            if isinstance(exc, TreadmillError):
                status.detail = f"connection failed: {exc}"
                raise
            status.detail = f"connection failed: {describe_bluetooth_error(exc)}"
            raise TreadmillError(status.detail) from exc
        finally:
            if self._connecting is client:
                self._connecting = None

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

    def _accepts(self, device: FoundDevice) -> bool:
        """Is this device one to connect to at all? (A protocol on a generic
        vendor characteristic says yes only to its own names.)"""
        return True

    def data_characteristics(self) -> tuple[str, ...]:
        """Where the walk may come from; a device with none of these is refused."""
        return (self.DATA_CHARACTERISTIC,)

    def _make_client(self, backend: BleBackend, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleClient:
        """The door to the link: for FTMS, one that cannot write at all."""
        return backend.client(device, on_disconnect)

    async def _setup(self, client: BleClient) -> None:
        """Read what the treadmill says it is, and start listening to it."""
        self._data = ftms.TreadmillData()
        self._coast_until = 0.0
        self.status.heart_rate_bpm, self.status.heart_rate_at = None, 0.0

        speed_range = None
        if client.has_characteristic(ftms.SUPPORTED_SPEED_RANGE):
            try:
                speed_range = ftms.parse_supported_speed_range(await client.read(ftms.SUPPORTED_SPEED_RANGE))
            except Exception as exc:
                log.debug("could not read the supported speed range: %s", exc)
        self.raw_speed_range = speed_range
        self._apply_speed_range(speed_range)

        await client.subscribe(ftms.TREADMILL_DATA, self._on_data)
        for uuid, handler in (
            (ftms.FITNESS_MACHINE_STATUS, self._on_status),
            (ftms.TRAINING_STATUS, self._on_training_status),
        ):
            if client.has_characteristic(uuid):
                await asyncio.sleep(SUBSCRIBE_GAP_S)
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
        if mph:
            lo, hi, step = 0.6 * KMH_PER_MPH, 4.0 * KMH_PER_MPH, 0.1 * KMH_PER_MPH
        else:
            lo, hi, step = 1.0, 6.0, 0.1
        if speed_range is not None and speed_range.max_kmh > 0:
            # The advertised maximum is kept even when the rest of the
            # range is odd: some pads report a minimum of zero (the default
            # minimum is used then), and a minimum above the maximum can
            # only mean the maximum.
            hi = speed_range.max_kmh
            if speed_range.min_kmh > 0:
                lo = speed_range.min_kmh
            lo = min(lo, hi)
            if speed_range.step_kmh > 0:
                step = speed_range.step_kmh
        if mph:
            # The range is reported as truncated km/h (0.96 for 0.6 mph):
            # recover the exact mph grid.
            status.min_kmh = round(lo / KMH_PER_MPH, 1) * KMH_PER_MPH
            status.max_kmh = round(hi / KMH_PER_MPH, 1) * KMH_PER_MPH
            status.step_kmh = 0.1 * KMH_PER_MPH
        else:
            status.min_kmh, status.max_kmh, status.step_kmh = lo, hi, step

    async def disconnect(self) -> None:
        """Let go: of the connection, and of one still being made (which then
        gives up, as soon as what it waits for returns)."""
        self._let_go += 1
        client, self._client = self._client, None
        connecting, self._connecting = self._connecting, None
        for each in (client, connecting if connecting is not client else None):
            if each is None:
                continue
            try:
                await each.disconnect()
            except Exception as exc:
                log.debug("disconnect failed: %s", exc)
        self._mark_disconnected("disconnected")

    def _on_disconnect(self, client: BleClient | None) -> None:
        """The backend reports that ``client``'s link is gone."""
        if client is None or client is not self._client:
            return  # an earlier connection, or one we have already let go of
        self._client = None
        self._mark_disconnected("connection lost")

    def _mark_disconnected(self, detail: str) -> None:
        status = self.status
        was = status.connected
        status.connected = False
        status.state = BeltState.UNKNOWN
        status.speed_kmh = 0.0
        status.target_kmh = None
        status.heart_rate_bpm, status.heart_rate_at = None, 0.0
        status.detail = detail
        if was:
            self._emit(TreadmillEvent.DISCONNECTED)

    # ---- notifications ---------------------------------------------------------
    def _on_data(self, data: bytes) -> None:
        packet = ftms.parse_treadmill_data(bytes(data))
        self._data = self._data.merge(packet)
        status = self.status
        now = time.monotonic()
        if packet.speed_kmh is not None:
            # (Only a packet with the speed in it: one of distance or time
            # alone must not keep an old speed counting as the belt's now.)
            status.updated_at = now
            status.speed_kmh = packet.speed_kmh
            if packet.speed_kmh > 0.05:
                # A belt coasting down after a stop still reports speed for a
                # moment; that does not make it "running" again.
                if status.state not in (BeltState.PAUSED, BeltState.STOPPED) or now > self._coast_until:
                    status.state = BeltState.RUNNING
            elif status.state is BeltState.RUNNING:
                # Moving -> not moving without a status event (not every
                # pad announces a stop): the belt has stopped all the same.
                status.state = BeltState.STOPPED
        if packet.total_distance_m is not None:
            status.distance_m = float(packet.total_distance_m)
        if packet.total_energy_kcal is not None:
            status.energy_kcal = float(packet.total_energy_kcal)
        if packet.elapsed_time_s is not None:
            status.elapsed_s = packet.elapsed_time_s
        if packet.vendor_steps is not None:
            status.steps = packet.vendor_steps
        if packet.heart_rate_bpm is not None:
            # (0: the hands are off the grips.) A pad that stops sending the
            # field leaves a reading that grows old: fresh_heart_rate.
            status.heart_rate_bpm = packet.heart_rate_bpm or None
            status.heart_rate_at = now

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
        elif event.paused or event.stopped:
            status.state = BeltState.PAUSED if event.paused else BeltState.STOPPED
            status.target_kmh = None
            self._coast_until = now + COAST_S
        elif event.op == ftms.StatusOp.RESET:
            status.state = BeltState.STOPPED
            status.target_kmh = None

    def _on_training_status(self, data: bytes) -> None:
        if len(data) < 2:
            return
        value = data[1]
        status = self.status
        if value == TRAINING_PRE_WORKOUT and status.state is not BeltState.RUNNING:
            status.state = BeltState.STARTING
        elif value == TRAINING_IDLE and status.speed_kmh <= 0.05 and status.state is BeltState.UNKNOWN:
            status.state = BeltState.STOPPED
