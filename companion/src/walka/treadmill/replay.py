"""A treadmill that plays back a recording (``walka run --replay FILE``).

The recording - made with ``walka probe --record`` - goes through the real
Bluetooth reader (``FtmsTreadmill``), so the dashboard shows what Walka
would have made of that pad on that day. To the rest of the companion it
is a simulated treadmill: what it records are not real walks, so they are
never sent anywhere (a Walka account takes no walk from a simulated
treadmill), and no Bluetooth address is stored for it.

Like every reader here it only listens. When the recording ends the link
is lost, as when a pad is switched off; the companion then reconnects and
the recording starts again.
"""

from __future__ import annotations

from ..recording import Recording, ReplayBackend
from .base import Treadmill, TreadmillEvent
from .ftms_ble import FtmsTreadmill
from .readers import build, reader_for


class ReplayTreadmill(Treadmill):
    kind = "simulated"  # made-up walks: see the top of this file
    replay = True

    def __init__(self, recording: Recording, *, units: str = "auto", time_scale: float = 1.0) -> None:
        super().__init__()
        self.recording = recording
        self.backend = ReplayBackend(recording, time_scale=time_scale)
        # Read the way the recorded device is read: FTMS, RSC, ... (treadmill/readers.py).
        reader = reader_for(recording.characteristics(), recording.name)
        kind = reader.key if reader else "ftms"
        address = self.backend.find_address()
        self._reader: FtmsTreadmill = build(
            kind, address, name_prefixes=(recording.name or "",), units=units, backend=self.backend
        )
        self.status = self._reader.status  # one status: the reader's
        self._reader.on_event = self._relay

    def _relay(self, event: TreadmillEvent) -> None:
        self._emit(event)

    async def connect(self) -> None:
        await self._reader.connect()
        self.status.detail = f"playing back a recording of {self.recording.name or 'a treadmill'}"

    async def disconnect(self) -> None:
        await self._reader.disconnect()
