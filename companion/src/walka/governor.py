"""The governor: decides what the treadmill belt may do.

This is the safety core of the companion. It is deliberately a small,
synchronous, deterministic state machine with no I/O: every tick it is given
the latest reading from the speed source (the WoW beacon, or the dashboard's
manual control) plus what the treadmill reports, and it returns the commands
to send. That makes every rule below unit-testable.

Rules, in priority order:

1.  **Hands off unless a session is active.** With no active session the
    governor never touches the belt (you can still use the remote).
2.  **Hard limits always win.** The commanded speed never exceeds
    ``Limits.max_speed_kmh`` nor the range the treadmill advertises, whatever
    the source asks for.
3.  **Gentle changes.** Speed is commanded at most once per
    ``command_interval_s`` and changes by at most ``accel``/``decel`` per
    second, so even a wild target cannot yank the belt.
4.  **Signal loss degrades, never accelerates.** If the signal goes stale
    (game minimised, loading screen, window covered) the belt holds its speed
    briefly, then slows to a safe speed, then pauses.
5.  **A stop at the machine is final.** If the belt is stopped with the
    treadmill's own button, remote or safety key, the governor will not
    restart it until the user explicitly resumes (pause -> resume in the
    addon or dashboard, a new session, or the treadmill's own start button).
6.  **The belt only starts on a deliberate act.** Starting a session starts
    the belt. But if the governor did not watch the session begin (the
    companion was started mid-session, the treadmill reconnected, or the belt
    was paused after a long signal loss), it waits for the character to start
    moving - or an explicit resume - before starting the belt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    """What the governor is doing, for display."""

    NO_TREADMILL = "no_treadmill"  # treadmill not connected
    IDLE = "idle"  # no active session: hands off
    STARTING = "starting"  # belt start requested, waiting for it to move
    FOLLOWING = "following"  # driving the belt from a live signal
    HOLDING = "holding"  # signal just lost: holding the last speed
    SAFE = "safe"  # signal lost: slowed to the safe speed
    STANDING = "standing"  # target is 0: belt paused until the target rises
    PAUSED = "paused"  # paused on request
    WAITING = "waiting"  # waiting for a deliberate act before starting the belt
    USER_STOPPED = "user_stopped"  # stopped at the treadmill itself


@dataclass
class Limits:
    """Safety limits enforced by the governor (all speeds km/h)."""

    max_speed_kmh: float = 6.0
    accel_kmh_per_s: float = 0.5
    decel_kmh_per_s: float = 1.0
    command_interval_s: float = 1.0
    signal_timeout_s: float = 3.0  # a reading older than this counts as lost
    hold_s: float = 5.0  # after loss: keep the last speed this long
    safe_speed_kmh: float = 1.6  # then slow to this (0 = pause straight away)
    loss_pause_s: float = 300.0  # then, after this long at safe speed, pause
    resume_requires_movement: bool = True
    start_grace_s: float = 12.0  # how long a belt may take to start moving
    stall_s: float = 4.0  # belt reporting zero this long = it has stopped


@dataclass(frozen=True)
class Reading:
    """Latest state from the speed source."""

    age_s: float = math.inf  # seconds since the source last produced valid data
    active: bool = False  # a session is running
    paused: bool = False  # ... but paused
    moving: bool = False  # the character is moving
    target_kmh: float = 0.0  # belt speed wanted
    session: int = 0  # session counter (changes when a new session starts)


@dataclass(frozen=True)
class Belt:
    """What the treadmill reports."""

    connected: bool = False
    speed_kmh: float = 0.0  # reported belt speed
    min_kmh: float = 1.0
    max_kmh: float = 6.0
    step_kmh: float = 0.1


class Action(str, Enum):
    START = "start"  # start or resume the belt
    SET_SPEED = "set_speed"
    PAUSE = "pause"
    STOP = "stop"


@dataclass(frozen=True)
class Command:
    action: Action
    speed_kmh: float | None = None
    reason: str = ""


_MOVING_EPS_KMH = 0.05


@dataclass
class Governor:
    limits: Limits = field(default_factory=Limits)

    # ---- state -----------------------------------------------------------------
    phase: Phase = Phase.NO_TREADMILL
    reason: str = ""
    driving: bool = False  # the belt is ours for the current session
    belt_on: bool = False  # we believe the belt is running
    commanded_kmh: float = 0.0  # last speed we commanded (0 = none since start)
    user_stopped: bool = False  # stopped at the machine: needs explicit resume
    needs_act: bool = False  # belt may only start on a deliberate act

    _last_command_at: float = -math.inf
    _belt_on_since: float = -math.inf
    _belt_off_at: float = -math.inf
    _zero_since: float | None = None
    _lost_since: float | None = None
    _session: int | None = None
    _prev_moving: bool = False
    _was_connected: bool = False
    _saw_inactive: bool = False
    _want_kmh: float = 0.0

    # ---- external events from the treadmill driver ------------------------------
    def notify_external_stop(self) -> None:
        """The belt was stopped/paused at the treadmill (button, remote, key)."""
        if self.driving or self.belt_on:
            self.user_stopped = True
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None

    def notify_external_start(self, now: float) -> None:
        """The belt was started at the treadmill: an explicit resume."""
        self.user_stopped = False
        self.needs_act = False
        if self.driving:
            self.belt_on = True
            self._belt_on_since = now
            self._zero_since = None

    def notify_disconnected(self) -> None:
        """The treadmill link dropped; we no longer know the belt state."""
        if self.driving:
            self.needs_act = True
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None

    def resume(self) -> None:
        """Explicit resume (dashboard button)."""
        self.user_stopped = False
        self.needs_act = False

    # ---- helpers ---------------------------------------------------------------
    @property
    def wanted_kmh(self) -> float:
        """The clamped speed the governor is steering towards (for display)."""
        return self._want_kmh

    def _top(self, belt: Belt) -> float:
        return min(self.limits.max_speed_kmh, belt.max_kmh)

    def _clamp(self, kmh: float, belt: Belt) -> float:
        """Clamp to the limits and snap to the belt's speed grid. 0 stays 0."""
        if kmh <= 0:
            return 0.0
        top = self._top(belt)
        kmh = max(belt.min_kmh, min(kmh, top))
        step = belt.step_kmh
        if step > 0:
            snapped = round(kmh / step) * step
            if snapped > top + 1e-9:
                snapped -= step
            if snapped < belt.min_kmh - 1e-9:
                snapped += step
            kmh = snapped
        return round(kmh, 4)

    @staticmethod
    def _toward(kmh: float, step: float, *, rising: bool) -> float:
        """Snap an intermediate speed to the grid without overshooting the
        rate limit: round down while speeding up, up while slowing down."""
        if step <= 0:
            return kmh
        units = kmh / step
        snapped = math.floor(units + 1e-9) if rising else math.ceil(units - 1e-9)
        return round(snapped * step, 4)

    def _belt_off(self, now: float) -> None:
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._belt_off_at = now
        self._zero_since = None

    def _pause(self, now: float, commands: list[Command], reason: str) -> None:
        if self.belt_on:
            commands.append(Command(Action.PAUSE, reason=reason))
        self._belt_off(now)

    def _idle(self, phase: Phase, reason: str) -> None:
        self.phase = phase
        self.reason = reason
        self._want_kmh = 0.0

    def _current_kmh(self, belt: Belt) -> float:
        """Best estimate of the speed the belt is being held at."""
        if self.commanded_kmh > 0:
            return self.commanded_kmh
        return max(belt.speed_kmh, belt.min_kmh)

    # ---- the tick --------------------------------------------------------------
    def step(self, now: float, reading: Reading, belt: Belt) -> list[Command]:
        """Advance the state machine; returns the commands to send, in order."""
        commands: list[Command] = []
        lim = self.limits

        if not belt.connected:
            if self._was_connected:
                self.notify_disconnected()
            self._was_connected = False
            self._idle(Phase.NO_TREADMILL, "treadmill not connected")
            return commands
        self._was_connected = True

        fresh = reading.age_s <= lim.signal_timeout_s

        if fresh:
            self._lost_since = None
            moving_edge = reading.moving and not self._prev_moving
            self._prev_moving = reading.moving

            if not reading.active:
                # Session over (or never started).
                self._saw_inactive = True
                if self.driving and self.belt_on:
                    commands.append(Command(Action.STOP, reason="session ended"))
                if self.driving:
                    self._belt_off(now)
                self.driving = False
                self.user_stopped = False
                self.needs_act = False
                self._session = None
                self._idle(Phase.IDLE, "no active session")
                return commands

            if reading.session != self._session:
                # A new session. It counts as an explicit start only if we
                # watched it begin; otherwise wait for a deliberate act.
                self._session = reading.session
                self.user_stopped = False
                self.needs_act = not self._saw_inactive
            self._saw_inactive = False
            self.driving = True

            if reading.paused:
                # An explicit pause also acknowledges any earlier stop, so
                # pause -> resume is always a way to restart the belt.
                self._pause(now, commands, "paused")
                self.user_stopped = False
                self.needs_act = False
                self._idle(Phase.PAUSED, "paused")
                return commands

            if self.user_stopped:
                self._idle(Phase.USER_STOPPED, "stopped at the treadmill - pause and resume to continue")
                return commands

            # A belt that is already turning (and that we did not just switch
            # off ourselves) can be adopted without any surprise start.
            if not self.belt_on and belt.speed_kmh > _MOVING_EPS_KMH and now - self._belt_off_at > lim.start_grace_s:
                self.belt_on = True
                self.needs_act = False
                self._belt_on_since = now
                self._last_command_at = now - lim.command_interval_s

            if self.needs_act:
                if lim.resume_requires_movement and not moving_edge:
                    self._idle(Phase.WAITING, "move your character to start the belt")
                    return commands
                self.needs_act = False

            want = self._clamp(reading.target_kmh, belt)
            run_phase = Phase.FOLLOWING
            self.reason = "following"
        else:
            # Signal lost.
            if not self.driving:
                self._idle(Phase.IDLE, "no signal")
                return commands
            if self.user_stopped:
                self._idle(Phase.USER_STOPPED, "stopped at the treadmill")
                return commands
            if self._lost_since is None:
                self._lost_since = now
            lost_for = now - self._lost_since
            if not self.belt_on:
                # Nothing is moving; stay put until the signal returns.
                self._idle(Phase.WAITING if self.needs_act else Phase.HOLDING, "signal lost")
                return commands
            current = self._current_kmh(belt)
            if lost_for < lim.hold_s:
                want = current
                run_phase = Phase.HOLDING
                self.reason = "signal lost - holding speed"
            elif lim.safe_speed_kmh > 0 and lost_for < lim.hold_s + lim.loss_pause_s:
                # Never speed up because of a lost signal.
                want = min(current, self._clamp(lim.safe_speed_kmh, belt))
                run_phase = Phase.SAFE
                self.reason = "signal lost - slowed to safe speed"
            else:
                self._pause(now, commands, "signal lost")
                self.needs_act = True
                self._idle(Phase.WAITING, "signal lost - belt paused")
                return commands

        self._want_kmh = want

        if want <= 0:
            # The source wants the belt stopped while the session stays active
            # (character standing with a stand speed of 0).
            self._pause(now, commands, "target speed is zero")
            self.phase = Phase.STANDING
            self.reason = "standing still"
            return commands

        if not self.belt_on:
            # Only reachable with a live signal (see the lost branch above).
            commands.append(Command(Action.START, reason="session active"))
            self.belt_on = True
            self._belt_on_since = now
            self._zero_since = None
            # Treadmills start at their own minimum speed; ramp up from there.
            self.commanded_kmh = 0.0
            self._last_command_at = now
            self.phase = Phase.STARTING
            self.reason = "starting the belt"
            return commands

        # Belt believed running: check that it really is.
        if belt.speed_kmh <= _MOVING_EPS_KMH:
            if self._zero_since is None:
                self._zero_since = now
            starting = self.commanded_kmh <= 0
            allowed = lim.start_grace_s if starting else lim.stall_s
            since = self._belt_on_since if starting else self._zero_since
            if now - since > allowed:
                self.notify_external_stop()
                self._idle(Phase.USER_STOPPED, "belt is not moving - pause and resume to restart")
                return commands
            if starting:
                self.phase = Phase.STARTING
                self.reason = "waiting for the belt to start"
                return commands
        else:
            self._zero_since = None

        self.phase = run_phase

        # Rate-limited speed command.
        if now - self._last_command_at < lim.command_interval_s:
            return commands
        first = self.commanded_kmh <= 0
        current = self._current_kmh(belt)
        delta = want - current
        if not first and abs(delta) < max(belt.step_kmh, 0.01) / 2:
            return commands
        window = lim.command_interval_s
        if delta > 0:
            nxt = self._toward(current + min(delta, lim.accel_kmh_per_s * window), belt.step_kmh, rising=True)
            nxt = min(nxt, want)
            if not first and nxt <= current:
                return commands
        else:
            nxt = self._toward(current + max(delta, -lim.decel_kmh_per_s * window), belt.step_kmh, rising=False)
            nxt = max(nxt, want)
            if not first and nxt >= current:
                return commands
        nxt = self._clamp(nxt, belt)
        commands.append(Command(Action.SET_SPEED, speed_kmh=nxt, reason=self.reason))
        self.commanded_kmh = nxt
        self._last_command_at = now
        return commands

    def shutdown(self, now: float = 0.0) -> list[Command]:
        """Commands that leave the treadmill safe when the companion exits."""
        commands: list[Command] = []
        if self.driving and self.belt_on:
            commands.append(Command(Action.STOP, reason="companion exiting"))
        self.driving = False
        self._belt_off(now)
        self._idle(Phase.IDLE, "stopped")
        return commands
