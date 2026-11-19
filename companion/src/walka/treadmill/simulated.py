"""A simulated walking pad.

It behaves like the UREVO pads the real reader listens to, so the whole
companion can be exercised (and demoed) without hardware:

* Start runs a 3-second countdown, then the belt starts at minimum speed.
* The belt ramps towards its target instead of jumping.
* With ``us_firmware=True`` speeds live on a 0.1 mph grid.
* Stop ends the workout and zeroes the machine's counters; pause keeps them.

Like a real pad it is worked with its remote and with nothing else: the
``press_*`` methods are that remote (tests, the demo's made-up walker and
the dashboard's simulator buttons press them). The companion itself has no
way to move this belt either - the reader interface has none.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .base import KMH_PER_MPH, BeltState, Treadmill, TreadmillEvent, kmh_to_mph_tenths


class SimulatedTreadmill(Treadmill):
    kind = "simulated"

    def __init__(
        self,
        *,
        us_firmware: bool = True,
        countdown_s: float = 3.0,
        ramp_kmh_per_s: float = 0.8,
        tick_s: float = 0.2,
        steps_per_km: float = 1700.0,
        kcal_per_km: float = 50.0,
        clock: Callable[[], float] = time.monotonic,
        auto_run: bool = True,
    ) -> None:
        super().__init__()
        self.clock = clock
        self.auto_run = auto_run  # False: tests drive time with advance()
        self.us_firmware = us_firmware
        self.countdown_s = countdown_s
        self.ramp_kmh_per_s = ramp_kmh_per_s
        self.tick_s = tick_s
        self.steps_per_km = steps_per_km
        self.kcal_per_km = kcal_per_km

        self._task: asyncio.Task | None = None
        self._count_left = 0.0
        self._distance_m = 0.0
        self._energy = 0.0
        self._steps = 0.0
        self._elapsed = 0.0

        status = self.status
        status.name = "Simulated pad"
        status.address = "SIM"
        status.mph_grid = us_firmware
        if us_firmware:
            status.min_kmh = 0.6 * KMH_PER_MPH
            status.max_kmh = 4.0 * KMH_PER_MPH
            status.step_kmh = 0.1 * KMH_PER_MPH
        else:
            status.min_kmh, status.max_kmh, status.step_kmh = 1.0, 6.0, 0.1

    # ---- connection ------------------------------------------------------------
    async def connect(self) -> None:
        if self.status.connected:
            return
        self.status.connected = True
        self.status.state = BeltState.STOPPED
        self.status.detail = "simulated treadmill"
        self.status.updated_at = self.clock()
        if self.auto_run and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name="walka-sim-treadmill")
        self._emit(TreadmillEvent.CONNECTED)

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self.status.connected:
            self.status.connected = False
            self._emit(TreadmillEvent.DISCONNECTED)

    async def _run(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(self.tick_s)
            now = time.monotonic()
            self.advance(now - last)
            last = now

    # ---- the remote (tests, the demo and the dashboard's simulator buttons) -----
    def press_start(self) -> None:
        """Simulate the user pressing start on the remote."""
        if self.status.connected:
            self._start()

    def press_pause(self) -> None:
        """Simulate the user pressing pause on the remote."""
        if self.status.state in (BeltState.RUNNING, BeltState.STARTING):
            self._halt(BeltState.PAUSED)

    def press_stop(self) -> None:
        """Simulate the user pressing stop on the remote."""
        if self.status.state is not BeltState.STOPPED:
            self._halt(BeltState.STOPPED)

    def press_speed(self, kmh: float) -> None:
        """Simulate the user changing the speed with the remote's + and -
        buttons: the belt goes to the step nearest ``kmh``. Like a real pad,
        nothing announces it (but the pad's own target says so)."""
        status = self.status
        if status.state not in (BeltState.RUNNING, BeltState.STARTING):
            return
        if self.us_firmware:
            kmh = kmh_to_mph_tenths(kmh) / 10.0 * KMH_PER_MPH
        else:
            kmh = round(kmh, 1)
        status.target_kmh = max(status.min_kmh, min(status.max_kmh, kmh))

    def press_faster(self) -> None:
        """The remote's + button: two firmware steps, like the pad's console."""
        self._nudge(+2)

    def press_slower(self) -> None:
        self._nudge(-2)

    def _nudge(self, steps: int) -> None:
        status = self.status
        if status.target_kmh is not None:
            self.press_speed(status.target_kmh + steps * status.step_kmh)

    # ---- behaviour -------------------------------------------------------------
    def _start(self) -> None:
        status = self.status
        if status.state in (BeltState.RUNNING, BeltState.STARTING):
            return
        if status.state is not BeltState.PAUSED:
            self._distance_m = self._energy = self._steps = self._elapsed = 0.0
        status.state = BeltState.STARTING
        status.target_kmh = status.min_kmh
        self._count_left = self.countdown_s

    def _halt(self, state: BeltState) -> None:
        status = self.status
        status.state = state
        status.target_kmh = None
        if state is BeltState.STOPPED:
            self._distance_m = self._energy = self._steps = self._elapsed = 0.0

    def advance(self, dt: float) -> None:
        """Advance the simulation by ``dt`` seconds (called by the run loop,
        or directly by tests for deterministic time)."""
        status = self.status
        if not status.connected or dt <= 0:
            return
        if status.state is BeltState.STARTING:
            self._count_left -= dt
            if self._count_left <= 0:
                status.state = BeltState.RUNNING
        goal = status.target_kmh if status.state is BeltState.RUNNING and status.target_kmh else 0.0
        rate = self.ramp_kmh_per_s if status.state is BeltState.RUNNING else 2.5
        if status.speed_kmh < goal:
            status.speed_kmh = min(goal, status.speed_kmh + rate * dt)
        elif status.speed_kmh > goal:
            status.speed_kmh = max(goal, status.speed_kmh - rate * dt)

        if status.speed_kmh > 0:
            km = status.speed_kmh * dt / 3600.0
            self._distance_m += km * 1000.0
            self._energy += km * self.kcal_per_km
            self._steps += km * self.steps_per_km
        if status.state is BeltState.RUNNING:
            self._elapsed += dt

        status.distance_m = float(int(self._distance_m))
        status.energy_kcal = float(int(self._energy))
        status.steps = int(self._steps)
        status.elapsed_s = int(self._elapsed)
        status.updated_at = self.clock()
