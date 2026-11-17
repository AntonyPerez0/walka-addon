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
7.  **A stop is not done until the belt says so.** After a pause or stop of
    its own the governor watches the reported speed, and sends a stop every
    few seconds for as long as the belt still moves. (Start and speed
    commands are never repeated on a guess.)
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
    # Research treadmills that change speed under a walker limit themselves
    # to about 0.1-0.2 m/s^2; these are well inside that (0.06 and 0.11).
    accel_kmh_per_s: float = 0.2
    decel_kmh_per_s: float = 0.4
    command_interval_s: float = 1.0
    signal_timeout_s: float = 3.0  # a reading older than this counts as lost
    hold_s: float = 5.0  # after loss: keep the last speed this long
    safe_speed_kmh: float = 1.3  # then slow to this (0 = pause straight away)
    loss_pause_s: float = 120.0  # then, after this long at safe speed, pause
    resume_requires_movement: bool = True
    start_grace_s: float = 12.0  # how long a belt may take to start moving
    stall_s: float = 4.0  # belt reporting zero this long = it has stopped
    # A pad that has just been stopped is still closing its workout and
    # acknowledges - but ignores - a start (seen on the UREVO Strol 2E).
    restart_holdoff_s: float = 10.0


@dataclass(frozen=True)
class Reading:
    """Latest state from the speed source."""

    age_s: float = math.inf  # seconds since the source last produced valid data
    active: bool = False  # a session is running
    paused: bool = False  # ... but paused
    moving: bool = False  # the character is moving
    target_kmh: float = 0.0  # belt speed wanted
    session: int = 0  # session counter (changes when a new session starts)
    afk: bool = False  # the player is away: a pause now is automatic, not something they asked for


@dataclass(frozen=True)
class Belt:
    """What the treadmill reports."""

    connected: bool = False
    speed_kmh: float = 0.0  # reported belt speed
    min_kmh: float = 1.0
    max_kmh: float = 6.0
    step_kmh: float = 0.1
    busy: bool = False  # the treadmill says it is still finishing a workout


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
# A belt that still moves this long after we asked it to stop - and has not
# slowed by at least this much meanwhile - is asked again.
_HALT_RETRY_S = 5.0
_HALT_SLOWING_KMH = 0.3
# After a reconnect the belt's speed takes a moment to come in.
_RECONNECT_SETTLE_S = 3.0


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
    _prev_moving: bool | None = None  # None: not seen yet, so nothing can count as "started moving"
    _was_connected: bool = False
    _saw_inactive: bool = False
    _want_kmh: float = 0.0
    _halting: Action | None = None  # a PAUSE/STOP the belt has not been seen to obey yet
    _halt_at: float = -math.inf
    _halt_speed: float | None = None  # belt speed when that stop was asked for
    _orphan: bool = False  # the link dropped while we were driving a running belt
    _settle_until: float | None = None  # reconnected: waiting to learn whether that belt ran on
    _stop_at: float = -math.inf  # when the belt was last stopped (not paused)
    _now: float = -math.inf  # time of the latest step

    # ---- external events from the treadmill driver ------------------------------
    def notify_external_stop(self) -> None:
        """The belt was stopped/paused at the treadmill (button, remote, key)."""
        if self.driving or self.belt_on:
            self.user_stopped = True
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None
        # We cannot tell a stop from a pause here; assume the slower case.
        self._stop_at = self._now

    def notify_external_start(self, now: float) -> None:
        """The belt was started at the treadmill: an explicit resume."""
        self.user_stopped = False
        self.needs_act = False
        self._halting = None  # whoever pressed start wants it running
        if self.driving:
            self.belt_on = True
            self._belt_on_since = now
            self._zero_since = None

    def notify_disconnected(self) -> None:
        """The treadmill link dropped; we no longer know the belt state."""
        if self.driving:
            self.needs_act = True
            self._orphan = self._orphan or self.belt_on
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None
        self._halting = None

    def resume(self) -> None:
        """Explicit resume (dashboard button)."""
        self.user_stopped = False
        self.needs_act = False
        # ... and a session that begins right now (a dashboard session) is
        # one the user started, not one we stumbled into.
        self._saw_inactive = True

    def end_session(self) -> None:
        """The session was ended by a STOP on the dashboard. The belt is no
        longer ours to drive - but unlike a session that simply ends, the
        stop stays latched, so that another session still running in the
        background (the game, minimised) cannot bring the belt back."""
        self.driving = False
        self._session = None
        self.needs_act = False
        self._saw_inactive = False

    @property
    def halt_pending(self) -> bool:
        """A stop of ours is on its way: the belt may still be coasting down."""
        return self._halting is not None

    def expect_halt(self, now: float, action: Action = Action.STOP) -> None:
        """A stop was sent to the belt from outside the governor (the
        dashboard's STOP button): keep asking until the belt has stopped."""
        self._halting = action
        self._halt_at = now
        self._halt_speed = None
        if action is Action.STOP:
            self._stop_at = now

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
        if top < belt.min_kmh - 1e-9:
            # The cap is below the slowest this belt can go: the only speed
            # that respects it is "not moving".
            return 0.0
        kmh = max(belt.min_kmh, min(kmh, top))
        step = belt.step_kmh
        if step > 0:
            snapped = round(kmh / step) * step
            if snapped > top + 1e-9:
                snapped -= step
            if snapped < belt.min_kmh - 1e-9:
                # No grid point between the minimum and the cap: the minimum
                # itself is a speed the belt can run at (stepping up a notch
                # instead could land above the cap).
                snapped = belt.min_kmh
            kmh = snapped
        # (Rounded only to tidy float noise. Coarser rounding moves a grid
        # value like 3.0 mph = 4.828032 km/h just below its own grid point,
        # and "snap down while speeding up" then never reaches it.)
        return round(kmh, 9)

    @staticmethod
    def _toward(kmh: float, step: float, *, rising: bool) -> float:
        """Snap an intermediate speed to the grid without overshooting the
        rate limit: round down while speeding up, up while slowing down."""
        if step <= 0:
            return kmh
        units = kmh / step
        snapped = math.floor(units + 1e-6) if rising else math.ceil(units - 1e-6)
        return round(snapped * step, 9)

    def _belt_off(self, now: float) -> None:
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._belt_off_at = now
        self._zero_since = None

    def _halt(self, now: float, commands: list[Command], action: Action, reason: str) -> None:
        """Ask the belt to stop, and remember to check that it did: the
        command can fail or get lost, and a belt left running is the one
        failure nobody is watching for."""
        commands.append(Command(action, reason=reason))
        self._halting = action
        self._halt_at = now
        self._halt_speed = None
        if action is Action.STOP:
            self._stop_at = now

    def _pause(self, now: float, commands: list[Command], reason: str) -> None:
        if self.belt_on:
            self._halt(now, commands, Action.PAUSE, reason)
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
        self._now = now

        if not belt.connected:
            if self._was_connected:
                self.notify_disconnected()
            self._was_connected = False
            # A session that begins while there is no treadmill is not one
            # that may start the belt the moment a treadmill appears.
            self._saw_inactive = False
            self._idle(Phase.NO_TREADMILL, "treadmill not connected")
            return commands
        self._was_connected = True

        # Back after a link loss. A pad keeps running when the link drops, so
        # the belt we were driving is probably still turning - but its speed
        # only arrives with the first data packet. Once it does, the belt is
        # ours again and is followed, paused or stopped like any belt of ours
        # (whatever happened to the session while we could not reach it).
        if self._orphan:
            self._orphan = False
            self._settle_until = now + _RECONNECT_SETTLE_S if (self.driving or self.user_stopped) else None
        if self._settle_until is not None:
            if self.belt_on or now >= self._settle_until or not (self.driving or self.user_stopped):
                self._settle_until = None
            elif belt.speed_kmh > _MOVING_EPS_KMH:
                self._settle_until = None
                if self.user_stopped:
                    # STOP was pressed while the belt was out of reach.
                    self._halt(now, commands, Action.STOP, "stop requested while the treadmill was out of reach")
                else:
                    self.belt_on = True
                    self.needs_act = False
                    self._belt_on_since = now
                    self._zero_since = None
                    self._last_command_at = now - lim.command_interval_s

        # Did the belt obey our last stop? This is feedback, not a blind
        # retry: it is only asked again while it reports that it still moves.
        # A pause that did not take is followed by a full stop - the one
        # command every treadmill understands.
        if self._halting is not None:
            if self._halt_speed is None:
                self._halt_speed = belt.speed_kmh
            if now - self._halt_at >= _HALT_RETRY_S:
                if belt.speed_kmh <= _MOVING_EPS_KMH:
                    self._halting = None
                elif belt.speed_kmh < self._halt_speed - _HALT_SLOWING_KMH:
                    # Coasting down, just slowly: give it another period.
                    self._halt_at, self._halt_speed = now, belt.speed_kmh
                else:
                    self._halt(now, commands, Action.STOP, "belt has not stopped - asking again")

        fresh = reading.age_s <= lim.signal_timeout_s

        if fresh:
            self._lost_since = None
            # "Started moving" needs a reading in which the character stood
            # still: one that is already moving when we first look (a flight
            # path, auto-run) pressed no key for us.
            moving_edge = reading.moving and self._prev_moving is False
            self._prev_moving = reading.moving

            if not reading.active:
                # Session over (or never started).
                self._saw_inactive = True
                if self._settle_until is not None:
                    # Just reconnected: do not conclude "nothing to stop"
                    # before the belt has had a chance to say that it runs.
                    self._idle(Phase.IDLE, "no active session")
                    return commands
                still_turning = belt.speed_kmh > _MOVING_EPS_KMH and self._halting is None
                if self.driving and (self.belt_on or still_turning) and not commands:
                    # (also a belt we think is off but that is turning, unless
                    # a stop of ours is already on its way: the session is
                    # over, it must not be left running)
                    self._halt(now, commands, Action.STOP, "session ended")
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
                # watched it begin (the reading before it said "no session",
                # which also cleared any stop at the machine). Otherwise - the
                # id simply changed under us - a stop at the machine stands,
                # and a belt that is not running waits for a deliberate act.
                # A belt that is running just keeps following: needs_act is
                # about surprise starts, and must never park a moving belt
                # out of the governor's control.
                self._session = reading.session
                self.needs_act = not self._saw_inactive and not self.belt_on
            self._saw_inactive = False
            self.driving = True

            if reading.paused:
                self._pause(now, commands, "paused")
                if not reading.afk:
                    # A pause the player asked for also acknowledges any
                    # earlier stop, so pause -> resume is always a way to
                    # restart the belt. The addon's automatic AFK pause is
                    # not that: nobody was there to ask for anything.
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
                self._halting = None
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
            # Signal lost. Whatever starts while we cannot see is not a
            # session we watched begin.
            self._saw_inactive = False
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
                # Hold - but the cap still wins if it was lowered meanwhile.
                want = min(current, self._clamp(current, belt))
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
            # (character standing with a stand speed of 0) - or no speed the
            # belt can run at is allowed by the cap.
            capped = self._top(belt) < belt.min_kmh - 1e-9
            self._pause(now, commands, "speed cap is below the belt's minimum" if capped else "target speed is zero")
            self.phase = Phase.STANDING
            self.reason = "the speed cap is below the treadmill's minimum speed" if capped else "standing still"
            return commands

        if not self.belt_on:
            # Only reachable with a live signal (see the lost branch above).
            since_stop = now - self._stop_at
            if since_stop < lim.restart_holdoff_s or (belt.busy and since_stop < 3 * lim.restart_holdoff_s):
                # Too soon after a stop: the start would be acknowledged and
                # ignored, and we would then wait for a belt that never moves.
                self.phase = Phase.STARTING
                self.reason = "waiting for the treadmill to finish its previous workout"
                return commands
            commands.append(Command(Action.START, reason="session active"))
            self._halting = None
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
                # We only know that no movement is reported - the data may
                # have dried up under a belt that still runs, or a start may
                # yet come through. Make it true before treating it as fact.
                self._halt(now, commands, Action.PAUSE, "belt is not moving")
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

        # Rate-limited speed command - except that a speed we commanded
        # before the cap was lowered is corrected at once, not an interval
        # later (the belt may still be accelerating towards it).
        over_cap = self.commanded_kmh > self._top(belt) + 1e-9
        if now - self._last_command_at < lim.command_interval_s and not over_cap:
            return commands
        first = self.commanded_kmh <= 0
        current = self._current_kmh(belt)
        delta = want - current
        if not first and abs(delta) < max(belt.step_kmh, 0.01) / 2:
            return commands
        window = lim.command_interval_s
        # At least one grid step per command, or a rate limit smaller than
        # the belt's step would mean the speed could never change at all.
        up = max(lim.accel_kmh_per_s * window, belt.step_kmh)
        down = max(lim.decel_kmh_per_s * window, belt.step_kmh)
        if delta > 0:
            nxt = self._toward(current + min(delta, up), belt.step_kmh, rising=True)
            nxt = min(nxt, want)
            if not first and nxt <= current:
                return commands
        else:
            nxt = self._toward(current + max(delta, -down), belt.step_kmh, rising=False)
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
        self._halting = None
        self._belt_off(now)
        self._idle(Phase.IDLE, "stopped")
        return commands
