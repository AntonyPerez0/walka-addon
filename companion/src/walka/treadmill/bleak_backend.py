"""bleak adapter for the FTMS driver and the heart-rate monitor.

Kept separate so that everything else can be imported and tested on
machines without a Bluetooth stack.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .. import ftms
from .ftms_ble import FoundDevice, is_treadmill_candidate


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


def _found(device: BLEDevice, adv: AdvertisementData | None) -> FoundDevice:
    uuids = [u.lower() for u in (adv.service_uuids if adv else [])]
    name = (adv.local_name if adv and adv.local_name else device.name) or ""
    return FoundDevice(
        name=name,
        address=device.address,
        rssi=adv.rssi if adv else None,
        ftms=ftms.FTMS_SERVICE in uuids,
        handle=device,
    )


class BleakClientAdapter:
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

    def characteristic_properties(self, uuid: str) -> Sequence[str]:
        char = self._char(uuid)
        return list(char.properties) if char is not None else []

    async def read(self, uuid: str) -> bytes:
        return bytes(await self._client.read_gatt_char(uuid))

    async def write(self, uuid: str, data: bytes, response: bool) -> None:
        await self._client.write_gatt_char(uuid, data, response=response)

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None:
        # start_notify enables notifications or indications, whichever the
        # characteristic supports.
        await self._client.start_notify(uuid, lambda _char, data: callback(bytes(data)))

    @property
    def raw(self) -> BleakClient:
        return self._client


class BleakBackend:
    async def discover(self, timeout: float) -> list[FoundDevice]:
        prepare_windows_threading()
        results = await BleakScanner.discover(timeout=timeout, return_adv=True)
        found = [_found(device, adv) for device, adv in results.values()]
        found.sort(key=lambda d: (not d.ftms, -(d.rssi or -999)))
        return found

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        prepare_windows_threading()
        wanted = address.upper() if address else None
        seen: dict[str, AdvertisementData] = {}

        def match(device: BLEDevice, adv: AdvertisementData) -> bool:
            if wanted is not None:
                ok = device.address.upper() == wanted
            else:
                ok = is_treadmill_candidate(adv.local_name or device.name, adv.service_uuids, name_prefixes)
            if ok:
                seen[device.address] = adv
            return ok

        device = await BleakScanner.find_device_by_filter(match, timeout=timeout)
        if device is None:
            return None
        return _found(device, seen.get(device.address))

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> BleakClientAdapter:
        return BleakClientAdapter(device.handle or device.address, on_disconnect)
