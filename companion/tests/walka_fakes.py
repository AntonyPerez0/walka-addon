"""Test doubles shared by the companion tests.

* ``FakePad`` / ``FakeBackend`` / ``FakeClient`` - a UREVO walking pad
  emulated at the GATT level, for the Bluetooth reader.
* Lua helpers - run the addon's pure-Lua modules under an embedded Lua 5.1.

The pad model is written from the hardware notes (docs/HARDWARE.md) and
deliberately shares no code with the reader or the simulator, so the reader
is checked against an independent statement of what the firmware does.

Like a real pad, the emulated one has a Fitness Machine Control Point: the
characteristic through which an app could start, stop and steer the belt.
Walka must never use it. So the pad is worked the way its owner works it -
``press_start``, ``press_speed``, ``press_stop`` are its remote - and it
keeps a list of everything that was ever written to it (``FakePad.writes``)
or subscribed to on its control point, which the tests expect to stay empty.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from walka import ftms
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


# ---- the walking pad ---------------------------------------------------------

KMH_PER_MPH_MICRO = 1_609_344  # km/h per mph, times 1e6 (exact)


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
        has_control_point: bool = True,  # a pad that only shows its numbers has none
        says_treadmill: bool | None = None,  # the machine type in its advertisement, if any
        has_status: bool = True,
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
        self.has_control_point = has_control_point
        self.says_treadmill = says_treadmill
        self.has_status = has_status
        self.steps_in_data = steps_in_data
        self.countdown_s = countdown_s
        self.ramp_kmh_per_s = ramp_kmh_per_s

        self.powered = True
        self.busy = False  # another central (the phone app) holds the connection
        self.speed_range_override: bytes | None = None

        self.client: FakeClient | None = None
        self.state = "idle"  # idle | countdown | running | paused
        self.count_left = 0.0
        self.speed_kmh = 0.0  # what the belt is really doing
        self.target_centi: int | None = None  # 0.01 km/h
        self.target_tenths: int | None = None  # mph tenths (US firmware only)
        self.distance_m = 0.0
        self.kcal = 0.0
        self.steps = 0.0
        self.elapsed_s = 0.0
        # Everything written to any characteristic of this pad, as (uuid,
        # bytes). Walka only listens, so this stays empty - whatever the test.
        self.writes: list[tuple[str, bytes]] = []
        self.reads: list[str] = []  # every characteristic that was read, in order
        self.subscriptions: dict[str, Callable[[bytes], None]] = {}
        self.connects = 0

    # -- GATT table --------------------------------------------------------------
    def characteristics(self) -> dict[str, list[str]]:
        table: dict[str, list[str]] = {}
        if self.has_ftms:
            table[ftms.TREADMILL_DATA] = ["notify"]
            if self.has_control_point:
                table[ftms.CONTROL_POINT] = ["write", "indicate"]
            if self.has_features:
                table[ftms.FITNESS_MACHINE_FEATURE] = ["read"]
            if self.has_speed_range:
                table[ftms.SUPPORTED_SPEED_RANGE] = ["read"]
            if self.has_status:
                table[ftms.FITNESS_MACHINE_STATUS] = ["notify"]
                table[ftms.TRAINING_STATUS] = ["read", "notify"]
        return table

    def read(self, uuid: str) -> bytes:
        self.reads.append(uuid)
        if uuid == ftms.SUPPORTED_SPEED_RANGE:
            if self.speed_range_override is not None:
                return self.speed_range_override
            if self.us_firmware:
                lo, hi = mph_tenths_to_centi_kmh(self.min_tenths), mph_tenths_to_centi_kmh(self.max_tenths)
                return struct.pack("<HHH", lo, hi, 16)
            return struct.pack("<HHH", *self.kmh_range)
        if uuid == ftms.FITNESS_MACHINE_FEATURE:
            # features: total distance, step count, expended energy, elapsed time; targets: speed
            return struct.pack("<II", 0x0004 | 0x0040 | 0x0200 | 0x1000, 0x0001 if self.has_control_point else 0)
        raise OSError(f"characteristic {uuid} is not readable")

    # -- notifications -----------------------------------------------------------
    def _send(self, uuid: str, data: bytes) -> None:
        callback = self.subscriptions.get(uuid)
        if callback is not None and self.client is not None:
            callback(data)

    def _status(self, *payload: int) -> None:
        self._send(ftms.FITNESS_MACHINE_STATUS, bytes(payload))

    def send_training_status(self, status: int) -> None:
        """Training Status notification: flags byte, then the status code
        (0x01 idle, 0x0D manual mode, 0x0E pre-workout, 0x0F post-workout)."""
        self._send(ftms.TRAINING_STATUS, bytes([0x00, status]))

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

    # -- what the firmware does --------------------------------------------------
    def _set_target(self, kmh: float) -> None:
        if self.us_firmware:
            tenths = max(self.min_tenths, min(self.max_tenths, round(kmh * 1e7 / KMH_PER_MPH_MICRO)))
            self.target_tenths = tenths
            self.target_centi = mph_tenths_to_centi_kmh(tenths)
        else:
            lo, hi, step = self.kmh_range
            self.target_centi = max(lo, min(hi, round(kmh * 100 / step) * step))

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

    def press_speed(self, kmh: float) -> None:
        """The remote's + and - buttons: the belt goes to the step nearest
        ``kmh``, and the pad says what it is now set to."""
        if self.state not in ("running", "countdown"):
            return
        self._set_target(kmh)
        assert self.target_centi is not None
        self._status(0x05, *self.target_centi.to_bytes(2, "little"))

    def pull_safety_key(self) -> None:
        self._halt(paused=False)
        self._status(0x03)

    def drop_link(self) -> None:
        """The Bluetooth link dies (out of range, pad switched off)."""
        client, self.client = self.client, None
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
    """The reader's view of one connection to a :class:`FakePad`.

    It can do more than the reader may ask of it (``BleClient`` in
    ``walka.treadmill.ftms_ble`` has no ``write``): a write is possible
    here, as on a real link, so that one would be *seen* - in
    ``FakePad.writes`` - and not fail quietly for want of a method.
    """

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
        self.pad.connects += 1

    async def disconnect(self) -> None:
        await asyncio.sleep(0)
        if self.pad.client is self:
            self.pad.drop_link()

    def has_characteristic(self, uuid: str) -> bool:
        return uuid in self.pad.characteristics()

    async def read(self, uuid: str) -> bytes:
        await asyncio.sleep(0)
        return self.pad.read(uuid)

    async def write(self, uuid: str, data: bytes, response: bool = True) -> None:
        await asyncio.sleep(0)
        self.pad.writes.append((uuid, bytes(data)))

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
        self.watchers: list[tuple[str, Callable[[], None]]] = []

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

    async def identify(self, device: FoundDevice) -> tuple[list[str], list[str]]:
        """Connect, list what the pad offers, let go (a connection that is counted)."""
        await asyncio.sleep(0)
        pad = device.handle
        pad.connects += 1
        characteristics = list(pad.characteristics())
        services = ([ftms.FTMS_SERVICE] if pad.has_ftms else []) + list(getattr(pad, "extra_services", []))
        characteristics += list(getattr(pad, "extra_characteristics", []))
        return services, characteristics

    async def watch(self, address: str, on_seen: Callable[[], None]) -> None:
        """Listening to advertisements: ``advertise()`` is a round of them."""
        self.watchers.append((address.upper(), on_seen))
        try:
            await asyncio.Event().wait()
        finally:
            self.watchers.remove((address.upper(), on_seen))

    def advertise(self) -> None:
        """Every pad that can be heard sends one advertisement."""
        for pad in self._visible():
            for address, on_seen in list(self.watchers):
                if pad.address.upper() == address:
                    on_seen()

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> FakeClient:
        client = FakeClient(device.handle, on_disconnect)
        self.clients.append(client)
        return client
