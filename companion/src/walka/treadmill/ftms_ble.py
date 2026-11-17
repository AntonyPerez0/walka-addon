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

Somebody is standing on the belt, so a pause or stop never queues behind
another command: it cuts short the wait for that command's answer, a start
or speed change that has not been written yet is dropped, and the stop goes
out as soon as the pad can take another write.
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
    CommandRejected,
    CommandSuperseded,
    Treadmill,
    TreadmillError,
    TreadmillEvent,
    kmh_to_mph_tenths,
    mph_tenths_to_command_kmh,
)

log = logging.getLogger(__name__)

DEFAULT_NAME_PREFIXES: tuple[str, ...] = ("URTM",)

# After one of our own start commands, "started" events for this long are
# attributed to us rather than to someone pressing a button on the machine.
# It is also how long a belt is taken to be running down after a halt.
OWN_EVENT_WINDOW_S = 6.0

# The pad announces a pause or stop of ours once, as "stopped or paused by
# the user" with the parameter we sent. Some firmware may only say so when
# the belt has come to rest, so that one announcement is waited for this
# long. Any other stop event is somebody at the pad (see ``_is_own_halt``).
OWN_HALT_EVENT_WINDOW_S = 10.0

# Cheap Bluetooth modules drop requests that arrive back to back: leave a
# gap between subscriptions and between control-point writes.
SUBSCRIBE_GAP_S = 0.15
WRITE_GAP_S = 0.4

# A Bluetooth write normally completes within a connection interval or two.
# One that has not after this long (a dying link) is given up on, so that it
# cannot hold up the commands behind it - a stop above all - for ever.
WRITE_TIMEOUT_S = 2.0
# The same for the reads and subscriptions made while connecting.
SETUP_TIMEOUT_S = 20.0

PAD_TIMEOUT_HINT = "is the pad switched on and in range, and is the UREVO phone app closed?"

# Training Status values (second byte of 0x2AD3).
TRAINING_IDLE = 0x01
TRAINING_PRE_WORKOUT = 0x0E  # countdown
TRAINING_POST_WORKOUT = 0x0F  # closing a workout: a start is ignored


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

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        """The treadmill to connect to: the device with ``address`` if one is
        given, otherwise one whose name starts with a prefix - and only if no
        such name is heard during the whole ``timeout``, a device that merely
        advertises the Fitness Machine Service."""
        ...

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleClient: ...


def name_matches(name: str | None, prefixes: Sequence[str]) -> bool:
    """Does a Bluetooth name start with one of the configured prefixes?"""
    upper = (name or "").upper()
    return any(upper.startswith(p.upper()) for p in prefixes if p)


def is_treadmill_candidate(name: str | None, service_uuids: Sequence[str], prefixes: Sequence[str]) -> bool:
    """True for devices worth offering: FTMS advertisers and known names."""
    if any(u.lower() == ftms.FTMS_SERVICE for u in service_uuids):
        return True
    return name_matches(name, prefixes)


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


@dataclass(eq=False)
class _OwnHalt:
    """A pause or stop we sent that the pad has not announced yet."""

    param: int  # ftms.STOP_PARAM or ftms.PAUSE_PARAM, as it will appear in the event
    until: float  # time.monotonic() after which we stop expecting it


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
        self._response: asyncio.Future[ftms.ControlResponse | None] | None = None
        self._response_op: int | None = None  # the opcode that answer is for
        self._halts_waiting = 0  # pause/stop calls waiting for their turn to write
        self._has_control = False
        self._write_with_response = True
        self._own_halts: list[_OwnHalt] = []
        self._own_start_until = 0.0
        self._coast_until = 0.0  # after any stop/pause: speed reports are the belt running down
        self._next_write_at = 0.0  # pacing of control-point writes
        self._top_step_refused = False  # the pad rejected the rounded-up value for its top speed
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
        try:
            found = await self._get_backend().discover(timeout)
        except Exception as exc:  # no adapter, Bluetooth switched off, ...
            raise TreadmillError(describe_bluetooth_error(exc)) from exc
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
            status.detail = f"Bluetooth scan failed: {describe_bluetooth_error(exc)}"
            raise TreadmillError(status.detail) from exc
        if device is None:
            status.detail = (
                "treadmill not found - is it switched on, in range, and is the "
                "UREVO phone app closed? (only one device can connect at a time)"
            )
            raise TreadmillError(status.detail)

        status.detail = f"connecting to {device.name or device.address}"
        client: BleClient | None = None

        def link_lost() -> None:
            # Tied to this client: a late callback from an earlier connection
            # must not be taken for the loss of the current one.
            self._on_disconnect(client)

        client = backend.client(device, link_lost)
        try:
            await client.connect()
            if not client.has_characteristic(ftms.CONTROL_POINT) or not client.has_characteristic(ftms.TREADMILL_DATA):
                raise TreadmillError(f"{device.name or device.address} does not expose the FTMS treadmill service")
            self._client = client
            await asyncio.wait_for(self._setup(client), SETUP_TIMEOUT_S)
            # The link can drop during set-up without any step failing (the
            # optional subscriptions are allowed to). Saying "connected" then
            # would leave a driver that can send nothing - not even a stop -
            # and that nobody reconnects.
            if self._client is not client or not client.is_connected:
                raise TreadmillError("the connection was lost while it was being set up")
        except Exception as exc:
            self._client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            status.connected = False
            if isinstance(exc, TreadmillError):
                status.detail = f"connection failed: {exc}"
                raise
            status.detail = f"connection failed: {describe_bluetooth_error(exc)}"
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
        self._own_halts.clear()
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
        await asyncio.sleep(SUBSCRIBE_GAP_S)
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
        self._next_write_at = time.monotonic() + SUBSCRIBE_GAP_S
        self._top_step_refused = False
        self.status.busy = False

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
            # The advertised maximum is a promise never to command more, so
            # it is kept even when the rest of the range is odd: some pads
            # report a minimum of zero (the default minimum is used then),
            # and a minimum above the maximum can only mean the maximum.
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
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
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
        status.busy = False
        status.detail = detail
        self._has_control = False
        self._own_halts.clear()
        if self._response is not None and not self._response.done():
            self._response.cancel()
        if was:
            self._emit(TreadmillEvent.DISCONNECTED)

    # ---- notifications ---------------------------------------------------------
    def _on_control_response(self, data: bytes) -> None:
        response = ftms.parse_control_response(bytes(data))
        if response is None:
            return
        pending = self._response
        if pending is None or pending.done():
            return
        if response.request_op != self._response_op:
            # A late answer to an earlier command (or to a Request Control we
            # did not wait for). Taking it for the answer to this one could
            # turn a stop that worked into "rejected", or hide a refusal.
            # (Two commands with the same opcode cannot be told apart: the
            # protocol has no sequence numbers.)
            log.debug("ignoring a stale control point answer: %s", response.describe())
            return
        pending.set_result(response)

    def _on_data(self, data: bytes) -> None:
        packet = ftms.parse_treadmill_data(bytes(data))
        self._data = self._data.merge(packet)
        status = self.status
        now = time.monotonic()
        status.updated_at = now
        if packet.speed_kmh is not None:
            status.speed_kmh = packet.speed_kmh
            if packet.speed_kmh > 0.05:
                # A belt coasting down after a stop still reports speed for a
                # moment; that does not make it "running" again.
                if status.state not in (BeltState.PAUSED, BeltState.STOPPED) or now > self._coast_until:
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
            self._coast_until = now + OWN_EVENT_WINDOW_S
            if not self._is_own_halt(event, now):
                self._emit(TreadmillEvent.EXTERNAL_STOP)
        elif event.op == ftms.StatusOp.RESET:
            status.state = BeltState.STOPPED
            status.target_kmh = None
        elif event.op == ftms.StatusOp.CONTROL_PERMISSION_LOST:
            self._has_control = False

    def _is_own_halt(self, event: ftms.MachineStatus, now: float) -> bool:
        """Is this stop/pause event the pad announcing a command of ours?

        Decided by what the event says, and once per command: a pause of
        ours is announced as "paused", a stop as "stopped", one time each.
        A second event, one of the other kind, and anything from the safety
        key is somebody at the pad - however soon after our own command it
        comes - and a stop at the pad is final.
        """
        self._own_halts = [halt for halt in self._own_halts if now <= halt.until]
        if event.op != ftms.StatusOp.STOPPED_OR_PAUSED_BY_USER:
            return False
        for halt in self._own_halts:
            if event.params[:1] == bytes([halt.param]):
                self._own_halts.remove(halt)
                return True
        return False

    def _on_training_status(self, data: bytes) -> None:
        if len(data) < 2:
            return
        value = data[1]
        status = self.status
        # After a stop the pad spends a while "post-workout"; a start sent
        # then is acknowledged and ignored. Tell the governor to wait.
        status.busy = value == TRAINING_POST_WORKOUT
        if value == TRAINING_PRE_WORKOUT and status.state is not BeltState.RUNNING:
            status.state = BeltState.STARTING
        elif value == TRAINING_IDLE and status.speed_kmh <= 0.05 and status.state is BeltState.UNKNOWN:
            status.state = BeltState.STOPPED

    # ---- commands --------------------------------------------------------------
    def _require_client(self) -> BleClient:
        client = self._client
        if client is None or not client.is_connected:
            raise TreadmillError("treadmill is not connected")
        return client

    def _interrupt_wait(self) -> None:
        """Stop waiting for the answer to the command in flight, so that a
        stop does not queue behind it. That command's outcome is then
        unknown, exactly as if its answer had been lost."""
        if self._response is not None and not self._response.done():
            self._response.set_result(None)

    async def _write(self, payload: bytes, *, halt: bool = False, wait: bool = True) -> ftms.ControlResponse | None:
        """Write one control-point command and wait for its indication.

        Returns ``None`` if no indication arrives in time (or the wait was
        cut short by a stop): the command may or may not have been applied,
        and the caller must not retry it blindly. With ``wait=False`` it
        returns as soon as the command is written.
        """
        client = self._require_client()
        gap = self._next_write_at - time.monotonic()
        if gap > 0:
            await asyncio.sleep(min(gap, WRITE_GAP_S))
            client = self._require_client()
        if self._halts_waiting and not halt:
            # Not written yet, so it certainly was not carried out - and the
            # stop behind it gets the pad's next write slot instead.
            raise CommandSuperseded("not sent - a stop was asked for first")
        loop = asyncio.get_running_loop()
        self._response = loop.create_future()
        self._response_op = payload[0]
        try:
            await asyncio.wait_for(
                client.write(ftms.CONTROL_POINT, payload, self._write_with_response), WRITE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            raise TreadmillError("Bluetooth write timed out") from None
        except Exception as exc:
            raise TreadmillError(f"Bluetooth write failed: {exc}") from exc
        finally:
            self._next_write_at = time.monotonic() + WRITE_GAP_S
        if not wait or self._halts_waiting:
            return None  # a stop is waiting for its turn: do not make it wait for our answer
        try:
            return await asyncio.wait_for(self._response, self.command_timeout_s)
        except asyncio.TimeoutError:
            log.debug("no response to control point command %s", payload.hex())
            return None
        except asyncio.CancelledError:
            if self._client is None:
                raise TreadmillError("treadmill disconnected") from None
            raise

    async def _ensure_control(self, *, halt: bool = False) -> None:
        if self._has_control:
            return
        # A stop does not wait for the answer to Request Control: the pad
        # gets the request and, one write gap later, the stop itself.
        response = await self._write(ftms.cmd_request_control(), halt=halt, wait=not halt)
        # Some pads never acknowledge Request Control, and some answer it with
        # a failure yet obey every command that follows. Either way the next
        # command tells us: "control not permitted" makes us ask again.
        if response is not None and not response.ok:
            log.debug("request control answered %s - carrying on", response.describe())
        self._has_control = True

    async def _command(self, payload: bytes, *, halt: bool = False) -> None:
        """Send one command; ``halt`` marks a pause or stop, which goes first.

        Commands are written one at a time. A halt does not wait for the one
        in flight to be answered, and a start or speed command that is still
        waiting to be written when a halt arrives is dropped
        (``CommandSuperseded``) rather than sent ahead of it.
        """
        if halt:
            self._halts_waiting += 1
            self._interrupt_wait()
            try:
                await self._lock.acquire()
            finally:
                self._halts_waiting -= 1
        else:
            await self._lock.acquire()
        try:
            await self._ensure_control(halt=halt)
            response = await self._write(payload, halt=halt)
            if response is not None and response.result == ftms.ResultCode.CONTROL_NOT_PERMITTED:
                # Definitely not executed: safe to take control and retry once.
                self._has_control = False
                await self._ensure_control(halt=halt)
                response = await self._write(payload, halt=halt)
            if response is not None and not response.ok:
                raise CommandRejected(f"treadmill rejected command: {response.describe()}", response.result)
        finally:
            self._lock.release()

    async def start(self) -> None:
        earlier_halts = list(self._own_halts)
        window_before = self._own_start_until
        self._own_start_until = time.monotonic() + OWN_EVENT_WINDOW_S
        try:
            await self._command(ftms.cmd_start_or_resume())
        except BaseException:
            # Nothing was started (or not that we know): a "started" event
            # now is the remote's, and the governor needs to hear of it.
            self._own_start_until = window_before
            raise
        self._own_start_until = time.monotonic() + OWN_EVENT_WINDOW_S
        # The belt has been told to move again: a stop or pause event from
        # now on is somebody saying no, not the late announcement of a halt
        # we sent before this start. (A halt asked for while the start was
        # in flight is newer, and is still expected.)
        self._own_halts = [halt for halt in self._own_halts if halt not in earlier_halts]
        if self.status.state is not BeltState.RUNNING:
            self.status.state = BeltState.STARTING

    async def _halt(self, payload: bytes, param: int, state: BeltState) -> None:
        """Pause or stop the belt (``param`` is how the pad will announce it)."""
        expected = _OwnHalt(param, time.monotonic() + OWN_HALT_EVENT_WINDOW_S)
        self._own_halts.append(expected)  # before writing: the event can beat the answer
        try:
            await self._command(payload, halt=True)
        except BaseException:
            # Refused, not sent, or not known to have arrived: if a stop
            # event does come, the careful reading is "somebody at the pad".
            if expected in self._own_halts:
                self._own_halts.remove(expected)
            raise
        now = time.monotonic()
        expected.until = now + OWN_HALT_EVENT_WINDOW_S
        self._coast_until = now + OWN_EVENT_WINDOW_S
        self.status.state = state
        self.status.target_kmh = None

    async def pause(self) -> None:
        await self._halt(ftms.cmd_pause(), ftms.PAUSE_PARAM, BeltState.PAUSED)

    async def stop(self) -> None:
        await self._halt(ftms.cmd_stop(), ftms.STOP_PARAM, BeltState.STOPPED)

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
        raw = self.raw_speed_range
        try:
            await self._command(ftms.cmd_set_target_speed(send))
        except CommandRejected as exc:
            # On mph firmware the value for the top step is rounded up past
            # the range the pad advertises (4.0 mph is sent as 6.5 km/h,
            # advertised maximum 6.43). A pad that refuses it gets the
            # advertised maximum instead, and one step less from then on.
            out_of_range = exc.result in (ftms.ResultCode.INVALID_PARAMETER, ftms.ResultCode.OPERATION_FAILED)
            if raw is None or send <= raw.max_kmh + 1e-9 or not out_of_range or self.status.speed_kmh <= 0.05:
                raise
            await self._command(ftms.cmd_set_target_speed(raw.max_kmh))
            if not self._top_step_refused:
                self._top_step_refused = True
                self.status.max_kmh = max(self.status.min_kmh, self.status.max_kmh - self.status.step_kmh)
                log.info("the treadmill refused its top speed step; maximum is now %.2f km/h", self.status.max_kmh)
            actual = self.status.max_kmh
        self.status.target_kmh = actual
