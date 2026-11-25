"""Recordings of what a treadmill sent, and playing them back.

``walka probe --record`` saves every Bluetooth notification it hears, with
the time it arrived, next to its report. A recording is made to be posted
in public, like the report (see ``probe``): it holds the probed device's
name and a shortened address, the layout of its services, the values the
probe read - except the ones that name this one machine (serial number,
system ID) - and the notifications. Nothing about other devices in range.

A recording is played back by ``ReplayBackend``: a stand-in for the
Bluetooth backend that the real reader (``ftms_ble.FtmsTreadmill``) is
given, so the bytes go through the same decoder and the same logic as on
the day they were recorded. Tests use it, and so does
``walka run --replay FILE`` (see ``treadmill.replay``).

Playing back writes nothing anywhere: the client below has the same few
methods as the one used on a real link (``ftms_ble.BleClient``), and no
write among them.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .treadmill.ftms_ble import FoundDevice

FORMAT = "walka-recording"
VERSION = 1

# Characteristics that identify one particular machine rather than its make.
# (The same list as the probe's; kept here so that this module stands alone.)
SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
SYSTEM_ID = "00002a23-0000-1000-8000-00805f9b34fb"
IDENTIFYING = frozenset({SERIAL_NUMBER, SYSTEM_ID})


class RecordingError(ValueError):
    """The file is not a recording this Walka can play."""


@dataclass
class Notification:
    t: float  # seconds since the recording started
    uuid: str
    data: bytes


@dataclass
class Recording:
    name: str = ""
    address: str = ""  # shortened, as in the probe's report
    recorded: str = ""  # the day, YYYY-MM-DD
    walka: str = ""  # the version that recorded it
    note: str = ""
    # service UUID -> {characteristic UUID: [properties]}
    services: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    reads: dict[str, bytes] = field(default_factory=dict)
    notifications: list[Notification] = field(default_factory=list)

    # ---- building one (the probe) --------------------------------------------------
    def add_characteristic(self, service_uuid: str, uuid: str, properties: Sequence[str]) -> None:
        self.services.setdefault(service_uuid.lower(), {})[uuid.lower()] = list(properties)

    def add_read(self, uuid: str, value: bytes) -> None:
        if uuid.lower() not in IDENTIFYING:
            self.reads[uuid.lower()] = bytes(value)

    def add_notification(self, t: float, uuid: str, data: bytes) -> None:
        if uuid.lower() not in IDENTIFYING:
            self.notifications.append(Notification(round(t, 3), uuid.lower(), bytes(data)))

    def characteristics(self) -> set[str]:
        return {uuid for chars in self.services.values() for uuid in chars}

    @property
    def duration_s(self) -> float:
        return self.notifications[-1].t if self.notifications else 0.0

    # ---- the file --------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "walka": self.walka,
            "recorded": self.recorded,
            "note": self.note,
            "device": {"name": self.name, "address": self.address},
            "services": self.services,
            "reads": {uuid: value.hex() for uuid, value in sorted(self.reads.items())},
            "notifications": [[n.t, n.uuid, n.data.hex()] for n in self.notifications],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Recording:
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise RecordingError("not a Walka recording")
        if data.get("version") != VERSION:
            raise RecordingError(f"a recording of version {data.get('version')!r}; this Walka plays version {VERSION}")
        try:
            device = data.get("device") or {}
            recording = cls(
                name=str(device.get("name", "")),
                address=str(device.get("address", "")),
                recorded=str(data.get("recorded", "")),
                walka=str(data.get("walka", "")),
                note=str(data.get("note", "")),
            )
            for service_uuid, chars in (data.get("services") or {}).items():
                for uuid, properties in chars.items():
                    recording.add_characteristic(service_uuid, uuid, [str(p) for p in properties])
            for uuid, value in (data.get("reads") or {}).items():
                recording.add_read(uuid, bytes.fromhex(value))
            last = 0.0
            for t, uuid, value in data.get("notifications") or []:
                t = float(t)
                if t < last:
                    raise RecordingError("the notifications are not in the order they arrived")
                last = t
                recording.add_notification(t, uuid, bytes.fromhex(value))
        except RecordingError:
            raise
        except (AttributeError, TypeError, ValueError) as exc:
            raise RecordingError(f"the recording is damaged: {exc}") from exc
        return recording

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1) + "\n", "utf-8")
        return path


def load(path: Path | str) -> Recording:
    try:
        text = Path(path).read_text("utf-8")
    except OSError as exc:
        raise RecordingError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RecordingError(f"{path} is not a Walka recording (not JSON)") from exc
    return Recording.from_dict(data)


def recording_path(report_path: Path) -> Path:
    """Where the recording goes that belongs to a saved probe report."""
    return report_path.with_suffix(".json")


def today() -> str:
    return time.strftime("%Y-%m-%d")


# ---- playing back ----------------------------------------------------------------------


class ReplayClient:
    """A connection to a recorded treadmill (see ``ftms_ble.BleClient``).

    Once connected it sends the recorded notifications to whoever has
    subscribed to them, ``time_scale`` times as slow as they came (1.0: as
    they came; 0: one after the other at once). ``on_packet`` is told the
    recording's time before each one (tests set their clock with it). At the
    end of the recording the link is lost, as when a pad is switched off.
    """

    def __init__(
        self,
        recording: Recording,
        on_disconnect: Callable[[], None],
        *,
        time_scale: float = 1.0,
        on_packet: Callable[[float], None] | None = None,
        end_with_disconnect: bool = True,
    ) -> None:
        self.recording = recording
        self._on_disconnect = on_disconnect
        self.time_scale = time_scale
        self.on_packet = on_packet
        self.end_with_disconnect = end_with_disconnect
        self._connected = False
        self._callbacks: dict[str, Callable[[bytes], None]] = {}
        self._task: asyncio.Task | None = None
        self.subscribed: list[str] = []
        self.finished = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def has_characteristic(self, uuid: str) -> bool:
        return uuid.lower() in self.recording.characteristics()

    async def read(self, uuid: str) -> bytes:
        try:
            return self.recording.reads[uuid.lower()]
        except KeyError:
            raise OSError(f"{uuid} was not read when this was recorded") from None

    async def subscribe(self, uuid: str, callback: Callable[[bytes], None]) -> None:
        if not self.has_characteristic(uuid):
            raise OSError(f"{uuid} is not on this treadmill")
        self._callbacks[uuid.lower()] = callback
        self.subscribed.append(uuid.lower())
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._play(), name="walka-replay")

    async def _play(self) -> None:
        # Let the reader finish setting up before the first packet.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        elapsed = 0.0
        for packet in self.recording.notifications:
            wait = (packet.t - elapsed) * self.time_scale
            elapsed = packet.t
            await asyncio.sleep(wait if wait > 0 else 0)
            if not self._connected:
                return
            callback = self._callbacks.get(packet.uuid)
            if callback is None:
                continue  # recorded, but this reader does not listen to it
            if self.on_packet is not None:
                self.on_packet(packet.t)
            callback(packet.data)
        self.finished.set()
        if self.end_with_disconnect and self._connected:
            self._connected = False
            self._on_disconnect()


class ReplayQueryClient(ReplayClient):
    """A recorded pad of a vendor protocol that answers only when asked: the
    recording holds its answers, so asking only counts the questions (and
    only this protocol's one question can be asked - see treadmill/proprietary.py)."""

    def __init__(self, recording: Recording, on_disconnect: Callable[[], None], protocol: str, **kwargs: Any) -> None:
        from .treadmill.proprietary import QUERIES

        super().__init__(recording, on_disconnect, **kwargs)
        self.query = QUERIES[protocol]
        self.asked: list[bytes] = []

    async def ask(self) -> None:
        self.asked.append(self.query.payload)


class ReplayBackend:
    """A Bluetooth backend with one recorded treadmill in range."""

    def __init__(self, recording: Recording, **client_kwargs: Any) -> None:
        self.recording = recording
        self.client_kwargs = client_kwargs
        self.clients: list[ReplayClient] = []

    def find_address(self) -> str:
        return self.recording.address or "REPLAY"

    def _device(self) -> FoundDevice:
        from . import ftms

        return FoundDevice(
            name=self.recording.name,
            address=self.find_address(),
            rssi=-50,
            ftms=ftms.FTMS_SERVICE in self.recording.services,
        )

    async def discover(self, timeout: float) -> list[FoundDevice]:
        return [self._device()]

    async def find(self, address: str | None, name_prefixes: Sequence[str], timeout: float) -> FoundDevice | None:
        return self._device()

    def query_client(self, device: FoundDevice, on_disconnect: Callable[[], None], protocol: str) -> ReplayQueryClient:
        client = ReplayQueryClient(self.recording, on_disconnect, protocol, **self.client_kwargs)
        self.clients.append(client)
        return client

    def client(self, device: FoundDevice, on_disconnect: Callable[[], None]) -> ReplayClient:
        client = ReplayClient(self.recording, on_disconnect, **self.client_kwargs)
        self.clients.append(client)
        return client
