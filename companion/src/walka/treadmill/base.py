"""Treadmill reader interface shared by the real (FTMS) and simulated belts.

Walka only listens to a treadmill. A reader connects, takes in what the
machine reports about itself and lets go again: it has no way to start,
stop or pause a belt or to change its speed, and nothing in the companion
asks it to. The belt is the walker's, by the treadmill's own remote and
buttons.
"""

from __future__ import annotations

import abc
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum

KMH_PER_MPH = 1.609344


class BeltState(str, Enum):
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"  # countdown before the belt moves
    RUNNING = "running"
    PAUSED = "paused"


class TreadmillEvent(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class TreadmillStatus:
    """Everything a reader knows about its treadmill.

    The counters (distance, energy, steps, elapsed) are the *machine's own*
    session counters. They reset whenever the machine feels like it (end of
    workout, long pause), so consumers must integrate deltas - see
    ``walka.session.CounterTracker``.
    """

    connected: bool = False
    name: str = ""
    address: str = ""
    state: BeltState = BeltState.UNKNOWN
    speed_kmh: float = 0.0
    target_kmh: float | None = None  # the speed the machine says it is set to, if it says
    min_kmh: float = 1.0  # the range the machine says it has (for display only)
    max_kmh: float = 6.0
    step_kmh: float = 0.1
    mph_grid: bool = False  # firmware works in 0.1 mph steps (US models)
    distance_m: float | None = None
    energy_kcal: float | None = None
    steps: int | None = None
    elapsed_s: int | None = None
    heart_rate_bpm: int | None = None
    updated_at: float = 0.0  # time.monotonic() of the last data packet
    detail: str = ""  # human-readable connection detail / last error

    def age_s(self, now: float | None = None) -> float:
        if not self.updated_at:
            return math.inf
        return (time.monotonic() if now is None else now) - self.updated_at

    def to_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        data.pop("updated_at")
        age = self.age_s()
        data["age_s"] = None if math.isinf(age) else round(age, 2)
        return data


EventHandler = Callable[[TreadmillEvent], None]


class Treadmill(abc.ABC):
    """A treadmill that is listened to. ``status`` is what it last said.

    There is deliberately nothing here that could move a belt: no start, no
    stop, no speed. A reader that grew one would be a different program.
    """

    kind = "abstract"

    def __init__(self) -> None:
        self.status = TreadmillStatus()
        self.on_event: EventHandler | None = None

    def _emit(self, event: TreadmillEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)

    @abc.abstractmethod
    async def connect(self) -> None:
        """Connect (idempotent). Raises TreadmillError if it cannot."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release the device (idempotent)."""


class TreadmillError(RuntimeError):
    """The treadmill could not be found, reached or listened to."""


def kmh_to_mph_tenths(kmh: float) -> int:
    """Nearest 0.1 mph step for a km/h value."""
    return int(round(kmh / KMH_PER_MPH * 10))
