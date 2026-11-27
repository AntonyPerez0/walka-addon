"""What every reader writes to a treadmill, watched at the Bluetooth library.

bleak itself is replaced here by a recording stand-in (its client and its
scanner): Walka's own backend, adapters and readers run as they do on a
computer. Every reader kind connects to a pad that offers what that kind
reads, runs for a while (the vendor queries at a faster pace, so that a
test takes a moment) and lets go. What reaches the stand-in is everything
Walka would have sent over the air.

The rule (docs/SAFETY.md): the only bytes ever written are the two fixed
status queries, each to its own characteristic; nothing is written to or
subscribed on the FTMS Control Point.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from walka import ftms
from walka.treadmill import bleak_backend, proprietary, readers
from walka.treadmill.base import TreadmillError

U = ftms.uuid16
ALLOWED_WRITES = {
    (U(0xFE02), bytes.fromhex("F7A20000A2FD")),  # WalkingPad: "your current status"
    (U(0xFFF2), bytes.fromhex("02515103")),  # FitShow: SYS_STATUS
}


@dataclass
class Pad:
    name: str
    address: str
    services: dict[str, list[str]]
    advertised: list[str] = field(default_factory=list)


FTMS_PAD = Pad(
    "URTM030",
    "AA:00:00:00:00:01",
    {
        ftms.FTMS_SERVICE: [ftms.TREADMILL_DATA, ftms.SUPPORTED_SPEED_RANGE, ftms.FITNESS_MACHINE_STATUS,
                            ftms.TRAINING_STATUS, ftms.CONTROL_POINT],
    },  # fmt: skip
    [ftms.FTMS_SERVICE],
)
PADS = {
    "ftms": FTMS_PAD,
    "rsc": Pad("Foot pod", "AA:00:00:00:00:02", {ftms.RSC_SERVICE: [ftms.RSC_MEASUREMENT]}),
    "walkingpad": Pad("KS-BLC2", "AA:00:00:00:00:03", {U(0xFE00): [U(0xFE01), U(0xFE02)]}),
    "pitpat": Pad("PitPat-T01", "AA:00:00:00:00:04", {U(0xFBA0): [U(0xFBA1), U(0xFBA2)]}),
    "fitshow": Pad("FS-AB12CD", "AA:00:00:00:00:05", {U(0xFFF0): [U(0xFFF1), U(0xFFF2)]}),
}


class Air:
    """Everything that went to the stand-in for bleak."""

    def __init__(self, pads: list[Pad]) -> None:
        self.pads = {pad.address: pad for pad in pads}
        self.writes: list[tuple[str, str, bytes]] = []  # (address, characteristic, bytes)
        self.subscribed: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str]] = []
        self.connected: set[str] = set()
        self.unexpected: list[str] = []
        self.hold_connect: asyncio.Event | None = None  # set: a connection takes until this is set
        self.connecting = 0

    def device(self, pad: Pad) -> tuple[BLEDevice, AdvertisementData]:
        adv = AdvertisementData(pad.name, {}, {}, list(pad.advertised), None, -50, ())
        return BLEDevice(pad.address, pad.name, None), adv


class _Char:
    def __init__(self, uuid: str) -> None:
        self.uuid = uuid


class _Service:
    def __init__(self, uuid: str, chars: list[str]) -> None:
        self.uuid = uuid
        self.characteristics = [_Char(c) for c in chars]


class _Services:
    def __init__(self, pad: Pad) -> None:
        self._services = [_Service(uuid, chars) for uuid, chars in pad.services.items()]

    def __iter__(self):
        return iter(self._services)

    def get_characteristic(self, uuid: str) -> Any:
        return next((c for s in self._services for c in s.characteristics if c.uuid == uuid.lower()), None)


def fake_bleak(air: Air):
    class FakeBleakClient:
        def __init__(self, device: Any, disconnected_callback: Callable | None = None, timeout: float = 0, **kw) -> None:
            self._address = device.address if hasattr(device, "address") else str(device)
            self._pad = air.pads[self._address]
            self._on_lost = disconnected_callback
            self._up = False
            self.services = _Services(self._pad)

        def __getattr__(self, name: str) -> Any:
            air.unexpected.append(name)
            raise AttributeError(name)

        @property
        def is_connected(self) -> bool:
            return self._up

        async def connect(self, **kw) -> bool:
            air.connecting += 1
            if air.hold_connect is not None:
                await air.hold_connect.wait()
            self._up = True
            air.connected.add(self._address)
            return True

        async def disconnect(self) -> bool:
            was, self._up = self._up, False
            air.connected.discard(self._address)
            if was and self._on_lost is not None:
                self._on_lost(self)
            return True

        async def __aenter__(self):
            await self.connect()
            return self

        async def __aexit__(self, *exc) -> None:
            await self.disconnect()

        async def read_gatt_char(self, uuid: Any, **kw) -> bytearray:
            air.reads.append((self._address, str(uuid).lower()))
            if str(uuid).lower() == ftms.SUPPORTED_SPEED_RANGE:
                return bytearray.fromhex("640058020a00")  # 1.00 - 6.00 km/h, 0.10 steps
            return bytearray()

        async def start_notify(self, uuid: Any, callback: Callable, **kw) -> None:
            air.subscribed.append((self._address, str(uuid).lower()))

        async def stop_notify(self, uuid: Any) -> None:
            pass

        async def write_gatt_char(self, uuid: Any, data: Any, response: bool | None = None) -> None:
            air.writes.append((self._address, str(uuid).lower(), bytes(data)))

        async def write_gatt_descriptor(self, handle: Any, data: Any) -> None:
            air.writes.append((self._address, f"descriptor {handle}", bytes(data)))

    class FakeScanner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            pass

        async def advertisement_data(self):
            for pad in air.pads.values():
                yield air.device(pad)
            await asyncio.Event().wait()

        @classmethod
        async def discover(cls, timeout: float = 5.0, return_adv: bool = False, **kw):
            return {pad.address: air.device(pad) for pad in air.pads.values()}

    return FakeBleakClient, FakeScanner


@pytest.fixture
def air(monkeypatch) -> Air:
    air = Air(list(PADS.values()))
    client, scanner = fake_bleak(air)
    monkeypatch.setattr(bleak_backend, "BleakClient", client)
    monkeypatch.setattr(bleak_backend, "BleakScanner", scanner)
    # The pads that answer only when asked are asked every 20 ms here, not every second.
    fast = {name: replace(query, every_s=0.02) for name, query in proprietary.QUERIES.items()}
    monkeypatch.setattr(proprietary, "QUERIES", MappingProxyType(fast))
    return air


def reader(kind: str, address: str | None = None):
    return readers.build(kind, address if address is not None else PADS[kind].address, backend=bleak_backend.BleakBackend())


def test_every_reader_writes_nothing_but_the_two_status_queries(air: Air):
    """Connect, listen for a while, let go - every reader kind there is."""

    async def scenario():
        for kind in readers.KINDS:
            treadmill = reader(kind)
            await treadmill.connect()
            assert treadmill.status.connected, kind
            await asyncio.sleep(0.25)
            await treadmill.disconnect()
            await asyncio.sleep(0.05)
            await treadmill.identify(PADS[kind].address)  # ("show all Bluetooth devices")

    asyncio.run(scenario())
    assert {kind for kind in readers.KINDS} == set(PADS), "a reader kind this test does not know"
    written = {(char, data) for _address, char, data in air.writes}
    assert written == ALLOWED_WRITES
    by_pad = {address: {(char, data) for a, char, data in air.writes if a == address} for address in air.pads}
    assert by_pad[PADS["walkingpad"].address] == {(U(0xFE02), bytes.fromhex("F7A20000A2FD"))}
    assert by_pad[PADS["fitshow"].address] == {(U(0xFFF2), bytes.fromhex("02515103"))}
    for kind in ("ftms", "rsc", "pitpat"):
        assert by_pad[PADS[kind].address] == set(), f"{kind} wrote to the pad"
    assert len(air.writes) >= 10, "the queries were sent while connected"
    assert all(char != ftms.CONTROL_POINT for _a, char, _d in air.writes)
    assert all(char != ftms.CONTROL_POINT for _a, char in air.subscribed)
    assert air.unexpected == [], f"the readers used more of bleak than the stand-in has: {air.unexpected}"
    assert air.connected == set(), "every reader let go"


def test_nothing_is_written_once_a_reader_has_let_go(air: Air):
    async def scenario():
        for kind in ("walkingpad", "fitshow"):
            treadmill = reader(kind)
            await treadmill.connect()
            await asyncio.sleep(0.1)
            await treadmill.disconnect()
            before = len(air.writes)
            await asyncio.sleep(0.2)
            assert len(air.writes) == before, kind

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["walkingpad", "fitshow", "ftms"])
def test_disconnect_while_a_connection_is_being_made_cancels_it(air: Air, kind: str):
    """Disconnect pressed (or another reader chosen) while the pad is still
    being connected to: the connection is not finished, nothing is asked."""
    air.hold_connect = asyncio.Event()

    async def scenario():
        treadmill = reader(kind)
        connecting = asyncio.create_task(treadmill.connect())
        while air.connecting == 0:
            await asyncio.sleep(0.01)
        await treadmill.disconnect()
        air.hold_connect.set()
        with pytest.raises(TreadmillError):
            await connecting
        await asyncio.sleep(0.2)
        assert not treadmill.status.connected
        return treadmill

    asyncio.run(scenario())
    assert air.writes == [] and air.connected == set()


def test_choosing_another_reader_while_connecting_lets_go_of_the_pad(air: Air, tmp_path):
    """Through the app, as /api/treadmill/identify does it: the old reader's
    connection under way is given up, and it writes nothing after."""
    from walka.app import App
    from walka.config import Config

    air.hold_connect = asyncio.Event()

    async def scenario():
        config = Config()
        config.treadmill.address = PADS["walkingpad"].address
        app = App(config, tmp_path / "data", treadmill=reader("walkingpad"))
        old = app.treadmill
        connecting = asyncio.create_task(old.connect())
        while air.connecting == 0:
            await asyncio.sleep(0.01)
        await app.use_reader("fitshow")
        air.hold_connect.set()
        with pytest.raises(TreadmillError):
            await connecting
        await asyncio.sleep(0.2)
        assert app.treadmill is not old and not old.status.connected

    asyncio.run(scenario())
    assert air.writes == [] and air.connected == set()


def test_the_walkingpad_reader_asks_only_a_pad_named_as_one(air: Air):
    """0xFE01 / 0xFE02 could be on another device: "Check" on it must not
    start writing the WalkingPad query to it."""
    stranger = Pad("Desk lamp", "AA:00:00:00:00:09", {U(0xFE00): [U(0xFE01), U(0xFE02)]})
    air.pads[stranger.address] = stranger

    async def scenario():
        with pytest.raises(TreadmillError, match="not a treadmill this reader is for"):
            await reader("walkingpad", stranger.address).connect()
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert air.writes == [] and air.connected == set()
    assert readers.reader_for([U(0xFE01)], "Desk lamp") is None
    assert readers.reader_for([U(0xFE01)], "WalkingPad").key == "walkingpad"
    assert readers.reader_for([U(0xFE01)], "KS-ST-A1P").key == "walkingpad"


@pytest.mark.parametrize("hold", ["connect", "never"])
def test_closing_walka_while_a_connection_is_being_made_leaves_the_pad_alone(air: Air, tmp_path, hold: str):
    """app.stop() while the connection loop is connecting to a pad that
    answers only when asked: nothing stays connected, nothing is asked."""
    from walka.app import App
    from walka.config import Config

    air.hold_connect = asyncio.Event()

    async def scenario():
        config = Config()
        config.treadmill.address = PADS["walkingpad"].address
        (tmp_path / "AddOns" / "Walka").mkdir(parents=True)
        config.wow.addon_dir = str(tmp_path / "AddOns" / "Walka")
        app = App(config, tmp_path / "data", treadmill=reader("walkingpad"))
        await app.start()
        while air.connecting == 0:
            await asyncio.sleep(0.01)
        await app.stop()
        if hold == "connect":
            air.hold_connect.set()  # (the radio answers after all, late)
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert air.writes == [] and air.connected == set()


# ---- finding the pad -----------------------------------------------------------------------------


def scanner_hearing(monkeypatch, heard: list[tuple[float, str, str, int]]):
    """A scanner that hears (after seconds, address, name, rssi), in order, and then nothing."""

    class Scanner:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            pass

        async def advertisement_data(self):
            for after, address, name, rssi in heard:
                await asyncio.sleep(after)
                yield BLEDevice(address, name or None, None), AdvertisementData(name or None, {}, {}, [], None, rssi, ())
            await asyncio.Event().wait()

    monkeypatch.setattr(bleak_backend, "BleakScanner", Scanner)


def test_of_two_pads_with_a_known_name_the_stronger_is_taken(monkeypatch):
    """A neighbour's pad, heard first and faintly, must not be taken over
    the one under the desk, heard a moment later and loud."""
    scanner_hearing(monkeypatch, [(0.0, "AA:00:00:00:00:21", "URTM030", -92), (0.3, "AA:00:00:00:00:22", "URTM030", -45)])
    found = asyncio.run(bleak_backend.BleakBackend().find(None, ("URTM",), 10.0))
    assert found is not None and found.address == "AA:00:00:00:00:22"


def test_a_pad_whose_name_comes_in_a_later_packet_is_found_with_its_name(monkeypatch):
    """Asked for by address: the first packet heard may have no name (the
    name is in the scan response). FitShow is read only with its name."""
    scanner_hearing(monkeypatch, [(0.0, "AA:00:00:00:00:05", "", -50), (0.3, "AA:00:00:00:00:05", "FS-AB12CD", -50)])
    found = asyncio.run(bleak_backend.BleakBackend().find("AA:00:00:00:00:05", (), 10.0))
    assert found is not None and found.name == "FS-AB12CD"


def test_a_pad_that_never_says_its_name_is_still_found_by_its_address(monkeypatch):
    scanner_hearing(monkeypatch, [(0.0, "AA:00:00:00:00:06", "", -50)])
    found = asyncio.run(bleak_backend.BleakBackend().find("AA:00:00:00:00:06", (), 10.0))
    assert found is not None and found.address == "AA:00:00:00:00:06" and found.name == ""
