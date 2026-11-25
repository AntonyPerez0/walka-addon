"""Treadmills that speak a maker's own protocol, not FTMS - read-only.

Some pads (older KingSmith / Xiaomi WalkingPads, the PitPat / SupeRun
family, FitShow machines) report their numbers only on their own vendor
characteristic, and some only when asked. For those - and only those -
Walka may send one fixed "report your numbers" query per protocol, and
nothing else (decision 1 in docs/SAFETY.md):

* The bytes are fixed, in one frozen table (``QUERIES`` below). A reader
  cannot choose them, nor where they go: the one method that sends them,
  ``QueryClient.ask()``, takes no arguments. Nothing in the table starts,
  stops or steers a belt; the tests pin every byte.
* The only code that writes to a characteristic is ``query_link.py``, and
  it writes nothing but an entry of this table. The FTMS reader's door to
  the link (``ftms_ble.BleClient``) still has no write at all.

Each protocol is a subclass of ``ProprietaryTreadmill`` that says where its
data comes from and how to decode it (``decode``). Finding the pad,
connecting and a lost link are ``FtmsTreadmill``'s. None of these readers
has been tried on hardware: they are written from public open-source
projects (cited in docs/HARDWARE.md) and tested against recordings put
together from those.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .base import BeltState, TreadmillError
from .ftms_ble import BleClient, FoundDevice, FtmsTreadmill, describe_bluetooth_error, name_matches

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Query:
    """The one "report your numbers" query of a protocol."""

    protocol: str
    service: str
    characteristic: str  # where it is written
    payload: bytes  # what is written: these bytes, always
    every_s: float  # how often (the pad answers once per query)
    source: str  # where the bytes come from


def _vendor(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# Every byte Walka may ever send a treadmill, besides switching notifications on. One entry per
# protocol, each a "report your numbers" status query; tests/test_vendor_queries.py pins them.
QUERIES = MappingProxyType(
    {
        "walkingpad": Query(
            protocol="walkingpad",
            service=_vendor(0xFE00),
            characteristic=_vendor(0xFE02),
            # header F7, type A2 (status), command 00 (stats), parameter 00, checksum A2, footer FD
            payload=bytes.fromhex("F7A20000A2FD"),
            every_s=1.0,
            source="ph4r05/ph4-walkingpad ask_stats; also qdomyos-zwift, klassm/walkingpad_macos_client",
        ),
    }
)


class QueryClient(BleClient, Protocol):
    """``BleClient`` and one more thing: send this protocol's fixed query."""

    async def ask(self) -> None: ...


@dataclass
class PadReading:
    """What one notification of a vendor protocol says. None: it does not say."""

    state: BeltState | None = None
    speed_kmh: float | None = None
    distance_m: float | None = None
    elapsed_s: int | None = None
    steps: int | None = None
    energy_kcal: float | None = None


class ProprietaryTreadmill(FtmsTreadmill):
    """A pad read through its maker's protocol. Subclasses set PROTOCOL,
    DATA_CHARACTERISTIC (the notifications) and NOT_READABLE, and decode()."""

    kind = "proprietary"
    PROTOCOL = ""
    NAME_PREFIXES: tuple[str, ...] = ()
    NEEDS_QUERY = True  # the pad reports only when asked

    def __init__(self, address: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("name_prefixes", self.NAME_PREFIXES)
        super().__init__(address, **kwargs)
        self._poll: asyncio.Task | None = None
        self.asked = 0

    # ---- what a protocol says ------------------------------------------------------
    @staticmethod
    def decode(data: bytes) -> PadReading | None:  # pragma: no cover - each protocol has its own
        raise NotImplementedError

    # ---- connecting ------------------------------------------------------------------
    async def connect(self) -> None:
        if not self.address:
            # By name only: never the nearest standard fitness machine, as an FTMS scan would settle for.
            if not self.name_prefixes:
                self.status.detail = "no treadmill chosen: pick it under Treadmill -> Scan -> Show all Bluetooth devices"
                raise TreadmillError(self.status.detail)
            try:
                found = await self._get_backend().discover(self.scan_timeout_s)
            except Exception as exc:
                self.status.detail = f"Bluetooth scan failed: {describe_bluetooth_error(exc)}"
                raise TreadmillError(self.status.detail) from exc
            named = [d for d in found if name_matches(d.name, self.name_prefixes)]
            if not named:
                self.status.detail = "treadmill not found - is it switched on, in range, and is its own phone app closed?"
                raise TreadmillError(self.status.detail)
            self.address = max(named, key=lambda d: d.rssi if d.rssi is not None else -999).address
        await super().connect()

    def _make_client(self, backend: Any, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleClient:
        if not self.NEEDS_QUERY:
            return backend.client(device, on_disconnect)
        return backend.query_client(device, on_disconnect, self.PROTOCOL)

    async def _setup(self, client: BleClient) -> None:
        self.raw_speed_range = None
        self._apply_speed_range(None)
        await client.subscribe(self.DATA_CHARACTERISTIC, self._on_vendor)
        if self.NEEDS_QUERY:
            self._poll = asyncio.create_task(self._ask_on(client), name=f"walka-{self.PROTOCOL}-query")

    async def _ask_on(self, client: QueryClient) -> None:
        """Ask the pad for its numbers, at the protocol's pace, while connected."""
        every = QUERIES[self.PROTOCOL].every_s
        while self._client is client and client.is_connected:
            try:
                await client.ask()
                self.asked += 1
            except Exception as exc:
                log.debug("asking the pad for its numbers failed: %s", exc)
            await asyncio.sleep(every)

    async def disconnect(self) -> None:
        self._stop_poll()
        await super().disconnect()

    def _mark_disconnected(self, detail: str) -> None:
        self._stop_poll()
        super()._mark_disconnected(detail)

    def _stop_poll(self) -> None:
        task, self._poll = self._poll, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    # ---- what the pad says ------------------------------------------------------------
    def _on_vendor(self, data: bytes) -> None:
        reading = self.decode(bytes(data))
        if reading is None:
            return
        status = self.status
        status.updated_at = time.monotonic()
        if reading.speed_kmh is not None:
            status.speed_kmh = reading.speed_kmh
        if reading.state is not None:
            status.state = reading.state
        elif reading.speed_kmh is not None:
            status.state = BeltState.RUNNING if reading.speed_kmh > 0.05 else BeltState.STOPPED
        if status.state is not BeltState.RUNNING and reading.speed_kmh is None:
            status.speed_kmh = 0.0
        if reading.distance_m is not None:
            status.distance_m = reading.distance_m
        if reading.elapsed_s is not None:
            status.elapsed_s = reading.elapsed_s
        if reading.steps is not None:
            status.steps = reading.steps
        if reading.energy_kcal is not None:
            status.energy_kcal = reading.energy_kcal
