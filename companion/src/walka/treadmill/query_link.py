"""The one write Walka makes to a treadmill besides switching notifications
on: a vendor protocol's fixed "report your numbers" query (decision 1 in
docs/SAFETY.md; the table of queries is ``proprietary.QUERIES``).

This is the only place in Walka that writes to a characteristic. The
adapter below is given a protocol, looks up that protocol's one query, and
``ask()`` - which takes no arguments - writes exactly those bytes to exactly
that characteristic. There is no way to hand it other bytes.
"""

from __future__ import annotations

from collections.abc import Callable

from bleak.backends.device import BLEDevice

from .bleak_backend import BleakClientAdapter
from .proprietary import QUERIES


class QueryClientAdapter(BleakClientAdapter):
    def __init__(self, device: BLEDevice | str, on_disconnect: Callable[[], None], protocol: str) -> None:
        super().__init__(device, on_disconnect)
        self._query = QUERIES[protocol]  # (KeyError for a protocol with no query: nothing to send)

    async def ask(self) -> None:
        """Send this protocol's fixed status query. Nothing else can be sent."""
        await self._client.write_gatt_char(self._query.characteristic, self._query.payload, response=False)
