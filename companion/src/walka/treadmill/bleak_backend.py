"""bleak adapter for the FTMS driver and the heart-rate monitor.

Kept separate so that everything else can be imported and tested on
machines without a Bluetooth stack.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Sequence
from contextlib import aclosing
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .. import ftms
from .ftms_ble import FoundDevice, name_matches


def prepare_windows_threading() -> None:
    """Undo a single-threaded-apartment initialisation, if any.

    bleak's WinRT backend hangs forever when the calling thread is an STA
    without a message loop (which GUI/tray libraries can set up on import).
    Call this once before using Bluetooth.
    """
    if sys.platform != "win32":
        return
    try:
        from bleak.backends.winrt.util import uninitialize_sta

        uninitialize_sta()
    except Exception:
        pass


def _advertised_services(adv: AdvertisementData | None) -> list[str]:
    """Service UUIDs from an advertisement: the UUID list and, for devices
    that only mention a service in their service data, those keys too."""
    if adv is None:
        return []
    return [u.lower() for u in [*adv.service_uuids, *adv.service_data.keys()]]


def _found(device: BLEDevice, adv: AdvertisementData | None) -> FoundDevice:
    uuids = _advertised_services(adv)
    name = (adv.local_name if adv and adv.local_name else device.name) or ""
    service_data = adv.service_data if adv else {}
    machine_type = next(
        (ftms.advertised_machine_type(bytes(v)) for k, v in service_data.items() if k.lower() == ftms.FTMS_SERVICE),
        None,
    )
    return FoundDevice(
        name=name,
        address=device.address,
        rssi=adv.rssi if adv else None,
        ftms=ftms.FTMS_SERVICE in uuids,
        handle=device,
        treadmill=ftms.says_treadmill(machine_type),
    )


class BleakClientAdapter:
    """One connection, as the reader may use it (``ftms_ble.BleClient``):
    read a value, switch notifications on, and nothing else. bleak's own
    client, which could also write, does not leave this class."""

    def __init__(self, device: BLEDevice | str, on_disconnect: Callable[[], None], timeout: float = 20.0) -> None:
        self._client = BleakClient(
            device,
            disconnected_callback=lambda _client: on_disconnect(),
            timeout=timeout,
        )

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    def _char(self, uuid: str) -> Any:
        return self._client.services.get_characteristic(uuid)

    def has_characteristic(self, uuid: str) -> bool:
        return self._char(uuid) is not None

    async def read(self, uuid: str) -> bytes:
        return bytes(await self._client.read_gatt_char(uuid))

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None:
        # start_notify enables notifications or indications, whichever the
        # characteristic supports.
        await self._client.start_notify(uuid, lambda _char, data: callback(bytes(data)))


class BleakBackend:
    async def discover(self, timeout: float) -> list[FoundDevice]:
        prepare_windows_threading()
        results = await BleakScanner.discover(timeout=timeout, return_adv=True)
        found = [_found(device, adv) for device, adv in results.values()]
        found.sort(key=lambda d: (not d.ftms, -(d.rssi if d.rssi is not None else -999)))
        return found

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        """Scan for the treadmill to connect to (see ``BleBackend.find``).

        With an address, or once a device with a known name is heard, the
        scan ends at once. A device that only advertises the Fitness Machine
        Service could just as well be a bike trainer or the neighbours'
        treadmill - whose walk would then be counted as yours - so it is
        only settled for when the whole scan has turned up no known name,
        and then the one with the strongest signal is taken. One that says
        in its advertisement that it is another kind of machine (a bike, a
        rower) is never taken.
        """
        prepare_windows_threading()
        wanted = address.upper() if address else None
        nameless: dict[str, FoundDevice] = {}  # Fitness Machine advertisers without a known name

        async def listen(scanner: BleakScanner) -> FoundDevice:
            async with aclosing(scanner.advertisement_data()) as advertisements:
                async for device, adv in advertisements:
                    found = _found(device, adv)
                    if wanted is not None:
                        if device.address.upper() == wanted:
                            return found
                    elif name_matches(found.name, name_prefixes):
                        return found
                    elif found.ftms and found.treadmill is not False:
                        nameless[found.address] = found
            raise RuntimeError("the Bluetooth scan ended by itself")

        async with BleakScanner() as scanner:
            try:
                return await asyncio.wait_for(listen(scanner), timeout)
            except asyncio.TimeoutError:
                pass
        if not nameless:
            return None
        return max(nameless.values(), key=lambda d: d.rssi if d.rssi is not None else -999)

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleakClientAdapter:
        return BleakClientAdapter(device.handle or device.address, on_disconnect)
