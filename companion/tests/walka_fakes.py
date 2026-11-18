"""Test doubles shared by the companion tests.

* ``FakeScreen`` - a virtual desktop a ``capture.Grabber`` can photograph.
* ``FakePad`` / ``FakeBackend`` / ``FakeClient`` - a UREVO walking pad
  emulated at the GATT level, for the Bluetooth driver.
* Lua helpers - run the addon's pure-Lua modules under an embedded Lua 5.1.

The pad model is written from the hardware notes (docs/HARDWARE.md) and
deliberately shares no code with the driver or the simulator, so the driver
is checked against an independent statement of what the firmware does.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from walka import beacon, ftms
from walka.capture import Shot
from walka.protocol import Frame
from walka.treadmill.ftms_ble import FoundDevice, name_matches

# ---- Lua ---------------------------------------------------------------------


def new_lua():
    from lupa.lua51 import LuaRuntime

    return LuaRuntime(unpack_returned_tuples=True)


def load_addon_file(lua, path: Path, ns=None, addon_name: str = "Walka"):
    """Run one of the addon's Lua files the way the game does: the chunk
    receives ``(addonName, namespaceTable)`` as varargs. Returns the namespace."""
    loader = lua.eval(
        "function(src, chunkname, addon, ns)"
        "  local chunk = assert(loadstring(src, chunkname))"
        "  return chunk(addon, ns)"
        " end"
    )
    ns = lua.table() if ns is None else ns
    loader(Path(path).read_text("utf-8"), "@" + Path(path).name, addon_name, ns)
    return ns


def lua_to_py(value: Any) -> Any:
    """Convert a Lua value to plain Python (tables become lists or dicts;
    an empty table becomes ``{}``)."""
    from lupa import lua51

    if lua51.lua_type(value) != "table":
        return value
    keys = list(value.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [lua_to_py(value[i]) for i in range(1, len(keys) + 1)]
    return {k: lua_to_py(v) for k, v in value.items()}


# ---- screen ------------------------------------------------------------------


class FakeScreen:
    """A virtual desktop made of grey pixels, with one or more monitors.

    It doubles as the ``Grabber``: ``grab`` returns BGRA like python-mss.
    Everything outside the monitors is black, as on a real desktop.
    """

    def __init__(self, monitors: Sequence[tuple[int, int, int, int]] = ((0, 0, 1000, 120),), background: int = 40):
        self._monitors = [tuple(m) for m in monitors]
        self.left = min(m[0] for m in self._monitors)
        self.top = min(m[1] for m in self._monitors)
        right = max(m[0] + m[2] for m in self._monitors)
        bottom = max(m[1] + m[3] for m in self._monitors)
        self.width, self.height = right - self.left, bottom - self.top
        self.background = background
        self.rows = [bytearray([0]) * self.width for _ in range(self.height)]
        self.clear()
        self.grabs: list[tuple[int, int, int, int]] = []
        self.fail_next = 0  # raise on this many grabs
        self.none_next = 0  # return None on this many grabs
        self.fail_monitors = False
        self.closed = False

    # -- painting ---------------------------------------------------------------
    def clear(self) -> None:
        for row in self.rows:
            row[:] = bytes(len(row))
        for left, top, width, height in self._monitors:
            for y in range(top - self.top, top - self.top + height):
                x = left - self.left
                self.rows[y][x : x + width] = bytes([self.background]) * width

    def fill(self, x: int, y: int, width: int, height: int, value: int) -> None:
        for yy in range(y - self.top, y - self.top + height):
            if 0 <= yy < self.height:
                x0 = max(0, x - self.left)
                x1 = min(self.width, x - self.left + width)
                if x1 > x0:
                    self.rows[yy][x0:x1] = bytes([value]) * (x1 - x0)

    def paint_row(self, x: int, y: int, values: bytes) -> None:
        yy = y - self.top
        if not 0 <= yy < self.height:
            return
        x0 = x - self.left
        for i, value in enumerate(values):
            if 0 <= x0 + i < self.width:
                self.rows[yy][x0 + i] = value

    def paint_strip(
        self,
        frame: Frame,
        x: int,
        y: int,
        cell_px: float = 3,
        *,
        white: int = 255,
        black: int = 0,
    ) -> None:
        """Paint the beacon the way the addon does: the cells, plus one cell
        of black quiet zone to the right and below."""
        height = max(1, int(round(cell_px)))
        strip = beacon.render_row(frame, cell_px, white=white, black=black)
        quiet = bytes([black]) * height
        for row in range(height):
            self.paint_row(x, y + row, strip + quiet)
        for row in range(height, 2 * height):
            self.paint_row(x, y + row, bytes([black]) * (len(strip) + height))

    # -- Grabber ----------------------------------------------------------------
    def grab(self, left: int, top: int, width: int, height: int) -> Shot | None:
        self.grabs.append((left, top, width, height))
        if self.fail_next > 0:
            self.fail_next -= 1
            raise OSError("screen capture failed (simulated)")
        if self.none_next > 0:
            self.none_next -= 1
            return None
        out = bytearray(width * height * 4)
        for row in range(height):
            y = top + row - self.top
            grey = bytearray(width)
            if 0 <= y < self.height:
                x0 = left - self.left
                src0, src1 = max(0, x0), min(self.width, x0 + width)
                if src1 > src0:
                    grey[src0 - x0 : src1 - x0] = self.rows[y][src0:src1]
            base = row * width * 4
            for channel in range(3):
                out[base + channel : base + width * 4 : 4] = grey
            out[base + 3 : base + width * 4 : 4] = b"\xff" * width
        return Shot(width, height, bytes(out))

    def monitors(self) -> list[tuple[int, int, int, int]]:
        if self.fail_monitors:
            raise OSError("monitor enumeration failed (simulated)")
        return list(self._monitors)

    def close(self) -> None:
        self.closed = True

    def full_grabs(self) -> int:
        """How many whole-monitor captures were taken (the slow scan)."""
        sizes = {(m[2], m[3]) for m in self._monitors}
        return sum(1 for g in self.grabs if (g[2], g[3]) in sizes)


# ---- the walking pad ---------------------------------------------------------

KMH_PER_MPH_MICRO = 1_609_344  # km/h per mph, times 1e6 (exact)


def us_firmware_mph_tenths(raw_centi_kmh: int) -> int:
    """What UREVO's US firmware does with a Set Target Speed value.

    ``raw_centi_kmh`` is the uint16 on the wire (0.01 km/h). The firmware
    truncates it to 0.1 km/h, converts to mph and truncates again to 0.1 mph.
    Pure integer arithmetic; returns tenths of a mph.
    """
    tenth_kmh = raw_centi_kmh // 10
    return tenth_kmh * 1_000_000 // KMH_PER_MPH_MICRO


def mph_tenths_to_centi_kmh(tenths: int) -> int:
    """How the pad reports a speed on its mph grid: km/h truncated to 0.01."""
    return tenths * KMH_PER_MPH_MICRO // 100_000


class FakePad:
    """A UREVO-style FTMS walking pad.

    The test drives time with :meth:`advance`; every call emits a Treadmill
    Data notification (flags 0x0484, as captured from real pads).
    """

    def __init__(
        self,
        *,
        name: str = "URTM030",
        address: str = "F4:12:FA:00:30:AB",
        us_firmware: bool = True,
        min_tenths: int = 6,  # mph tenths (US) - 0.6 mph
        max_tenths: int = 40,  # 4.0 mph with the handle folded
        kmh_range: tuple[int, int, int] = (100, 600, 10),  # km/h firmware: min, max, step in 0.01 km/h
        advertises_ftms: bool = True,
        has_ftms: bool = True,
        has_speed_range: bool = True,
        has_features: bool = True,
        speed_target: bool = True,  # what the features say; the pad obeys either way
        says_treadmill: bool | None = None,  # the machine type in its advertisement, if any
        has_status: bool = True,
        require_control: bool = False,
        silent: bool = False,
        write_without_response_only: bool = False,
        inline_indications: bool = False,
        steps_in_data: bool = False,
        countdown_s: float = 3.0,
        ramp_kmh_per_s: float = 1.0,
    ) -> None:
        self.name, self.address = name, address
        self.us_firmware = us_firmware
        self.min_tenths, self.max_tenths = min_tenths, max_tenths
        self.kmh_range = kmh_range
        self.advertises_ftms = advertises_ftms
        self.has_ftms = has_ftms
        self.has_speed_range = has_speed_range
        self.has_features = has_features
        self.speed_target = speed_target
        self.says_treadmill = says_treadmill
        self.has_status = has_status
        self.require_control = require_control
        self.silent = silent
        self.write_without_response_only = write_without_response_only
        self.inline_indications = inline_indications
        self.steps_in_data = steps_in_data
        self.countdown_s = countdown_s
        self.ramp_kmh_per_s = ramp_kmh_per_s

        self.powered = True
        self.busy = False  # another central (the phone app) holds the connection
        self.fail_writes = False  # the GATT write itself errors
        self.lose_writes = False  # the write "succeeds" but never reaches the pad
        self.reject: dict[int, int] = {}  # opcode -> result code to answer with
        self.reject_payloads: dict[bytes, int] = {}  # exact command -> result code
        self.speed_range_override: bytes | None = None

        self.client: FakeClient | None = None
        self.has_control = False
        self.state = "idle"  # idle | countdown | running | paused
        self.count_left = 0.0
        self.speed_kmh = 0.0  # what the belt is really doing
        self.target_centi: int | None = None  # 0.01 km/h
        self.target_tenths: int | None = None  # mph tenths (US firmware only)
        self.distance_m = 0.0
        self.kcal = 0.0
        self.steps = 0.0
        self.elapsed_s = 0.0
        self.writes: list[bytes] = []  # every control point write, in order
        self.write_modes: list[bool] = []  # ... and whether it was write-with-response
        self.subscriptions: dict[str, Callable[[bytes], None]] = {}
        self.connects = 0

    # -- GATT table --------------------------------------------------------------
    def characteristics(self) -> dict[str, list[str]]:
        table: dict[str, list[str]] = {}
        if self.has_ftms:
            table[ftms.TREADMILL_DATA] = ["notify"]
            table[ftms.CONTROL_POINT] = (
                ["write-without-response", "indicate"] if self.write_without_response_only else ["write", "indicate"]
            )
            if self.has_features:
                table[ftms.FITNESS_MACHINE_FEATURE] = ["read"]
            if self.has_speed_range:
                table[ftms.SUPPORTED_SPEED_RANGE] = ["read"]
            if self.has_status:
                table[ftms.FITNESS_MACHINE_STATUS] = ["notify"]
                table[ftms.TRAINING_STATUS] = ["read", "notify"]
        return table

    def read(self, uuid: str) -> bytes:
        if uuid == ftms.SUPPORTED_SPEED_RANGE:
            if self.speed_range_override is not None:
                return self.speed_range_override
            if self.us_firmware:
                lo, hi = mph_tenths_to_centi_kmh(self.min_tenths), mph_tenths_to_centi_kmh(self.max_tenths)
                return struct.pack("<HHH", lo, hi, 16)
            return struct.pack("<HHH", *self.kmh_range)
        if uuid == ftms.FITNESS_MACHINE_FEATURE:
            # features: total distance, step count, expended energy, elapsed time; targets: speed
            return struct.pack("<II", 0x0004 | 0x0040 | 0x0200 | 0x1000, 0x0001 if self.speed_target else 0)
        raise OSError(f"characteristic {uuid} is not readable")

    # -- notifications -----------------------------------------------------------
    def _send(self, uuid: str, data: bytes, *, inline: bool = True) -> None:
        callback = self.subscriptions.get(uuid)
        if callback is None or self.client is None:
            return
        if inline:
            callback(data)
        else:
            asyncio.get_running_loop().call_soon(callback, data)

    def _indicate(self, op: int, result: int) -> None:
        if self.silent:
            return
        self._send(ftms.CONTROL_POINT, bytes([0x80, op, result]), inline=self.inline_indications)

    def _status(self, *payload: int) -> None:
        self._send(ftms.FITNESS_MACHINE_STATUS, bytes(payload))

    def send_training_status(self, status: int) -> None:
        """Training Status notification: flags byte, then the status code
        (0x01 idle, 0x0D manual mode, 0x0E pre-workout, 0x0F post-workout)."""
        self._send(ftms.TRAINING_STATUS, bytes([0x00, status]))

    def send_stray_indication(self, op: int, result: int) -> None:
        """An indication nobody asked for (e.g. a late answer to an old command)."""
        self._send(ftms.CONTROL_POINT, bytes([0x80, op, result]))

    def data_packet(self) -> bytes:
        flags = 0x0484  # speed (More Data = 0), total distance, expended energy, elapsed time
        body = struct.pack("<H", int(self.speed_kmh * 100 + 1e-6))
        body += int(self.distance_m).to_bytes(3, "little")
        body += struct.pack("<HHB", min(0xFFFE, int(self.kcal)), 0xFFFF, 0xFF)
        body += struct.pack("<H", min(0xFFFF, int(self.elapsed_s)))
        if self.steps_in_data:
            flags |= 0x2000
            body += int(self.steps).to_bytes(3, "little")
        return struct.pack("<H", flags) + body

    # -- the control point -------------------------------------------------------
    def control_write(self, data: bytes) -> None:
        self.writes.append(bytes(data))
        if self.lose_writes or not data:
            return
        op = data[0]
        if op in self.reject or bytes(data) in self.reject_payloads:
            self._indicate(op, self.reject_payloads.get(bytes(data), self.reject.get(op, 0x04)))
            return
        if op == 0x00:  # request control
            self.has_control = True
            self._indicate(op, 0x01)
            return
        if self.require_control and not self.has_control:
            self._indicate(op, 0x05)  # control not permitted
            return
        if op == 0x07:
            self._begin()
            self._indicate(op, 0x01)
            self._status(0x04)
        elif op == 0x08:
            if len(data) < 2 or data[1] not in (0x01, 0x02):
                self._indicate(op, 0x03)
                return
            self._halt(paused=data[1] == 0x02)
            self._indicate(op, 0x01)
            self._status(0x02, data[1])
        elif op == 0x02:
            if len(data) < 3:
                self._indicate(op, 0x03)
                return
            raw = int.from_bytes(data[1:3], "little")
            if self.state == "running":
                self._set_target(raw)
                assert self.target_centi is not None
                self._status(0x05, *self.target_centi.to_bytes(2, "little"))
            # Speed writes while the belt is not moving are acknowledged and ignored.
            self._indicate(op, 0x01)
        elif op == 0x01:  # reset
            self._halt(paused=False)
            self._indicate(op, 0x01)
            self._status(0x01)
        else:
            self._indicate(op, 0x02)  # op code not supported

    def _set_target(self, raw: int) -> None:
        if self.us_firmware:
            tenths = max(self.min_tenths, min(self.max_tenths, us_firmware_mph_tenths(raw)))
            self.target_tenths = tenths
            self.target_centi = mph_tenths_to_centi_kmh(tenths)
        else:
            lo, hi, _step = self.kmh_range
            self.target_centi = max(lo, min(hi, raw // 10 * 10))

    def _begin(self) -> None:
        if self.state in ("running", "countdown"):
            return
        if self.state == "idle":
            self.distance_m = self.kcal = self.steps = self.elapsed_s = 0.0
        self.state = "countdown"
        self.count_left = self.countdown_s
        if self.us_firmware:
            self.target_tenths = self.min_tenths
            self.target_centi = mph_tenths_to_centi_kmh(self.min_tenths)
        else:
            self.target_centi = self.kmh_range[0]

    def _halt(self, *, paused: bool) -> None:
        self.state = "paused" if paused else "idle"
        self.target_centi = self.target_tenths = None
        if not paused:
            self.distance_m = self.kcal = self.steps = self.elapsed_s = 0.0

    # -- the remote / the buttons on the pad --------------------------------------
    def press_start(self) -> None:
        self._begin()
        self._status(0x04)

    def press_stop(self) -> None:
        self._halt(paused=False)
        self._status(0x02, 0x01)

    def press_pause(self) -> None:
        self._halt(paused=True)
        self._status(0x02, 0x02)

    def pull_safety_key(self) -> None:
        self._halt(paused=False)
        self._status(0x03)

    def take_control_away(self) -> None:
        self.has_control = False
        self._status(0xFF)

    def drop_link(self) -> None:
        """The Bluetooth link dies (out of range, pad switched off)."""
        client, self.client = self.client, None
        self.has_control = False
        self.subscriptions.clear()
        if client is not None:
            client.on_disconnect()

    # -- time --------------------------------------------------------------------
    @property
    def target_kmh(self) -> float:
        if self.target_centi is None:
            return 0.0
        if self.us_firmware and self.target_tenths is not None:
            return self.target_tenths * KMH_PER_MPH_MICRO / 1e7
        return self.target_centi / 100.0

    @property
    def mph_tenths(self) -> float:
        """Belt speed in tenths of a mph."""
        return self.speed_kmh * 1e7 / KMH_PER_MPH_MICRO

    def advance(self, dt: float, *, notify: bool = True) -> None:
        if self.state == "countdown":
            self.count_left -= dt
            if self.count_left <= 0:
                self.state = "running"
        goal = self.target_kmh if self.state == "running" else 0.0
        rate = self.ramp_kmh_per_s if self.state == "running" else 2.5
        if self.speed_kmh < goal:
            self.speed_kmh = min(goal, self.speed_kmh + rate * dt)
        elif self.speed_kmh > goal:
            self.speed_kmh = max(goal, self.speed_kmh - rate * dt)
        if self.speed_kmh > 0:
            km = self.speed_kmh * dt / 3600.0
            self.distance_m += km * 1000.0
            self.kcal += km * 50.0
            self.steps += km * 1700.0
        if self.state == "running":
            self.elapsed_s += dt
        if notify:
            self._send(ftms.TREADMILL_DATA, self.data_packet())


class FakeClient:
    """The driver's view of one connection to a :class:`FakePad`."""

    def __init__(self, pad: FakePad, on_disconnect: Callable[[], None]) -> None:
        self.pad = pad
        self.on_disconnect = on_disconnect
        self.subscribe_errors: set[str] = set()

    @property
    def is_connected(self) -> bool:
        return self.pad.client is self

    async def connect(self) -> None:
        await asyncio.sleep(0)
        if not self.pad.powered or self.pad.busy:
            raise OSError("device unreachable (simulated)")
        self.pad.client = self
        self.pad.has_control = False
        self.pad.connects += 1

    async def disconnect(self) -> None:
        await asyncio.sleep(0)
        if self.pad.client is self:
            self.pad.drop_link()

    def has_characteristic(self, uuid: str) -> bool:
        return uuid in self.pad.characteristics()

    def characteristic_properties(self, uuid: str) -> Sequence[str]:
        return self.pad.characteristics().get(uuid, [])

    async def read(self, uuid: str) -> bytes:
        await asyncio.sleep(0)
        return self.pad.read(uuid)

    async def write(self, uuid: str, data: bytes, response: bool) -> None:
        await asyncio.sleep(0)
        if not self.is_connected:
            raise OSError("not connected (simulated)")
        if self.pad.fail_writes:
            raise OSError("GATT write failed (simulated)")
        if response and self.pad.write_without_response_only:
            raise OSError("characteristic does not support write-with-response (simulated)")
        assert uuid == ftms.CONTROL_POINT
        self.pad.write_modes.append(response)
        self.pad.control_write(bytes(data))

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None:
        await asyncio.sleep(0)
        if uuid in self.subscribe_errors:
            raise OSError("subscribe failed (simulated)")
        self.pad.subscriptions[uuid] = callback


class FakeBackend:
    """Stands in for bleak: knows which pads are in radio range."""

    def __init__(self, *pads: FakePad) -> None:
        self.pads = list(pads)
        self.finds = 0
        self.scan_error: Exception | None = None
        self.clients: list[FakeClient] = []

    def _visible(self) -> list[FakePad]:
        # A UREVO pad stops advertising while a central is connected to it.
        return [p for p in self.pads if p.powered and not p.busy and p.client is None]

    def _found(self, pad: FakePad) -> FoundDevice:
        return FoundDevice(
            name=pad.name,
            address=pad.address,
            rssi=-60,
            ftms=pad.advertises_ftms,
            handle=pad,
            treadmill=pad.says_treadmill,
        )

    async def discover(self, timeout: float) -> list[FoundDevice]:
        await asyncio.sleep(0)
        if self.scan_error is not None:
            raise self.scan_error
        return [self._found(p) for p in self._visible()]

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        await asyncio.sleep(0)
        self.finds += 1
        if self.scan_error is not None:
            raise self.scan_error
        visible = self._visible()
        if address:
            for pad in visible:
                if pad.address.upper() == address.upper():
                    return self._found(pad)
            return None
        # As the real backend: a known name first, and a device that only
        # advertises the Fitness Machine Service when no such name is around.
        for wanted in (
            lambda pad: name_matches(pad.name, name_prefixes),
            lambda pad: pad.advertises_ftms and pad.says_treadmill is not False,
        ):
            for pad in visible:
                if wanted(pad):
                    return self._found(pad)
        return None

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> FakeClient:
        client = FakeClient(device.handle, on_disconnect)
        self.clients.append(client)
        return client
