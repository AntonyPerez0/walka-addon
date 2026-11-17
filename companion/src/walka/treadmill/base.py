"""Treadmill driver interface shared by the real (FTMS) and simulated belts."""

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
    EXTERNAL_STOP = "external_stop"  # stopped/paused at the machine itself
    EXTERNAL_START = "external_start"  # started/resumed at the machine itself


@dataclass
class TreadmillStatus:
    """Everything a driver knows about its treadmill.

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
    target_kmh: float | None = None
    min_kmh: float = 1.0
    max_kmh: float = 6.0
    step_kmh: float = 0.1
    mph_grid: bool = False  # firmware works in 0.1 mph steps (US models)
    distance_m: float | None = None
    energy_kcal: float | None = None
    steps: int | None = None
    elapsed_s: int | None = None
    heart_rate_bpm: int | None = None
    busy: bool = False  # the machine is still closing a workout and will ignore a start
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
    """A treadmill that can be started, paused, stopped and set to a speed.

    All command coroutines raise ``TreadmillError`` on failure. Drivers must
    never retry a motor command whose outcome is unknown.
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

    @abc.abstractmethod
    async def start(self) -> None:
        """Start or resume the belt."""

    @abc.abstractmethod
    async def pause(self) -> None:
        """Pause the belt, keeping the machine's workout counters."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop the belt and end the machine's workout."""

    @abc.abstractmethod
    async def set_speed(self, kmh: float) -> None:
        """Set the belt's target speed. Only valid while the belt is running."""


class TreadmillError(RuntimeError):
    """A treadmill command or connection failed."""


class CommandRejected(TreadmillError):
    """The treadmill answered a command with a failure code (so the command
    was definitely not carried out)."""

    def __init__(self, message: str, result: int) -> None:
        super().__init__(message)
        self.result = result


def mph_tenths_to_command_kmh(mph_tenths: int) -> float:
    """Speed to send so that mph-firmware lands on exactly ``mph_tenths``/10 mph.

    UREVO's US firmware truncates an FTMS target to 0.1 km/h, converts it to
    mph and truncates again to 0.1 mph - so the naive 3.22 km/h for 2.0 mph
    arrives as 1.9 mph. Rounding the km/h value *up* to the next 0.1 km/h
    survives both truncations (2.0 mph -> 3.3 km/h -> 2.05 mph -> 2.0 mph).
    """
    return math.ceil(mph_tenths * KMH_PER_MPH - 1e-9) / 10.0


def kmh_to_mph_tenths(kmh: float) -> int:
    """Nearest 0.1 mph step for a km/h value."""
    return int(round(kmh / KMH_PER_MPH * 10))
