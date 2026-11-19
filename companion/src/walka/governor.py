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
    the source asks for. (A cap lowered under a moving belt is approached at
    the deceleration limit, not in one jump.)
3.  **Gentle changes.** Speed is commanded at most once per
    ``command_interval_s`` and changes by at most ``accel``/``decel`` per
    second, so even a wild target cannot yank the belt. Speed-ups are
    measured from what the belt *reports*, never from what was merely asked
    for: no command is more than one increment above the reported speed.
4.  **Signal loss degrades, never accelerates.** A picture more than a second
    old may slow the belt but not speed it up. If the signal goes stale
    (game minimised, loading screen, window covered) the belt holds its
    speed briefly, then slows to a safe speed, then pauses.
5.  **A stop at the machine is final.** If the belt is stopped with the
    treadmill's own button, remote or safety key - or is found stopped when
    the Bluetooth link comes back - the governor will not restart it until
    the user explicitly resumes (pause -> resume in the addon or dashboard,
    a new session, or the treadmill's own start button).
6.  **The belt only starts on a deliberate act.** Starting a session starts
    the belt. But if the governor did not watch the session begin (the
    companion was started mid-session, or the belt was paused after a long
    signal loss), it waits for the character to start moving - seen standing
    first, *after* any period in which it could not watch - or an explicit
    resume, which only counts for a minute.
7.  **A stop is not done until the belt says so.** After a pause or stop of
    its own the governor watches the reported speed, and sends a stop every
    few seconds for as long as the belt still moves. The debt survives a
    lost Bluetooth link. (Start and speed commands are never repeated on a
    guess.)
8.  **A speed chosen at the machine is a ceiling.** If the walker slows the
    belt with the remote, the governor follows the source below that speed
    but never above it, until an explicit resume.
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
    hold_s: float = 5.0  # after the last frame: keep the speed this long
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
    # Made up by the companion itself to say "the dashboard session is over".
    # Nothing was watched: it is no proof that whatever comes next was seen
    # to begin, nor that a character stood still.
    synthetic: bool = False


@dataclass(frozen=True)
class Belt:
    """What the treadmill reports."""

    connected: bool = False
    speed_kmh: float = 0.0  # reported belt speed
    min_kmh: float = 1.0
    max_kmh: float = 6.0
    step_kmh: float = 0.1
    busy: bool = False  # the treadmill says it is still finishing a workout
    # A stamp that changes with every data packet from the treadmill, so that
    # "two packets in a row" and "nothing heard since the link came back" can
    # be told apart from the same number seen twice. None: the driver does
    # not say, and every step counts as a packet.
    data_at: float | None = None
    target_kmh: float | None = None  # the speed the treadmill says it is set to, if it says


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
# A stop that could not be delivered because the treadmill is out of reach
# stays owed (and the companion keeps trying to reconnect) for this long.
HALT_GIVE_UP_S = 1800.0
# After a reconnect the belt's speed takes a moment to come in. Until the
# first packet (or this long) nothing is concluded and nothing is started.
_RECONNECT_SETTLE_S = 3.0
# A picture of the game older than this may slow the belt, not speed it up.
SPEEDUP_MAX_AGE_S = 1.0
# A reported speed that has not changed for this long is where the belt has
# settled: nothing of ours is still being ramped to.
_SETTLE_S = 2.0
_SAME_SPEED_KMH = 0.05
# A belt that stays far below what was asked without ever settling is taken
# at its word after this long (so the governor is never parked for good).
_MISMATCH_MAX_S = 6.0
# Two packets "agree" when their speeds are this close: a belt changes speed
# gradually, a corrupt packet does not.
_AGREE_KMH = 1.0
# Pads report their speed cut off to 0.01 km/h.
_REPORT_SLACK_KMH = 0.02
# How long a dashboard Resume stays good while it cannot be acted on.
RESUME_VALID_S = 60.0
# A session whose signal has been gone this long, with the belt off, is
# over as far as the governor is concerned (the recorder files the walk).
ABANDONED_AFTER_S = 900.0


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
    ceiling_kmh: float | None = None  # a speed chosen at the machine: follow below it, never above
    # Things worth telling the user, as short codes (the app words them).
    notes: list[str] = field(default_factory=list)

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
    _target_kmh: float = 0.0  # what the live source asks for, within the limits
    _halting: Action | None = None  # a PAUSE/STOP the belt has not been seen to obey yet
    _halt_at: float = -math.inf
    _halt_speed: float | None = None  # belt speed when that stop was asked for
    _owed_since: float | None = None  # ... and since when it could not be delivered
    _orphan: bool = False  # the link dropped while we were driving a running belt
    _linked_at: float = -math.inf  # when the link last came up
    _stop_at: float = -math.inf  # when the belt was last stopped (not paused)
    _now: float = -math.inf  # time of the latest step
    _starting: bool = False  # a start was given and the belt has not moved yet
    # Where the first speed command after a start or an adoption is measured
    # from. 0: the belt's minimum (a pad starts there, whatever one packet
    # says). None: a belt found running - not known until two packets agree.
    _ramp_from: float | None = 0.0
    _before_last_command: float = 0.0  # commanded_kmh before the latest speed command
    # Since it was started, the belt has been seen running at the speed asked
    # for. Until then a higher speed it reports is the old one it is still
    # coming down from - not a speed somebody chose.
    _in_step: bool = False
    _ours: bool = False  # if the belt turns, it is because of us
    _view_kmh: float | None = None  # belt speed in the latest step (None: out of reach)
    # The newest data packets from the treadmill.
    _stamp: float | None = None
    _packets: int = 0  # since the link came up
    _still_packets: int = 0  # in a row, newest first, that say "not moving"
    _packet_at: float = -math.inf
    _speed_prev: float | None = None
    _speed_last: float | None = None
    _steady_kmh: float = 0.0  # the speed the belt has been reporting ...
    _steady_since: float | None = None  # ... since then ...
    _steady_seen: float = -math.inf  # ... and still did at this time
    _far_since: float | None = None  # the belt runs far below what was asked, since then ...
    _far_low: float = 0.0  # ... and this is the slowest it has been seen at
    _top_seen: float = math.inf
    _cap_cut: bool = False  # the cap was lowered and no speed command has gone out since
    _resume_at: float | None = None  # an explicit resume that has not been acted on yet
    _resume_lifted_stop: bool = False

    # ---- external events from the treadmill driver ------------------------------
    def notify_external_stop(self) -> None:
        """The belt was stopped/paused at the treadmill (button, remote, key)."""
        if self.driving or self.belt_on:
            self.user_stopped = True
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None
        self._starting = False
        self._ours = False  # whoever starts it next, it will not be us
        # We cannot tell a stop from a pause here; assume the slower case.
        self._stop_at = self._now
        # ... and a belt still coasting down from it is not one to adopt.
        self._belt_off_at = self._now

    def stop_pressed(self, now: float) -> None:
        """STOP on the dashboard or the hotkey: the belt stays off until an
        explicit resume, and the stop is repeated until the belt has obeyed
        (the app sends the first one itself)."""
        ours = self._ours or self.belt_on
        self.notify_external_stop()
        self.user_stopped = True
        self._ours = ours  # still ours to stop, if the stop does not get through
        self.expect_halt(now)

    def notify_external_start(self, now: float) -> None:
        """The belt was started at the treadmill: an explicit resume."""
        self.user_stopped = False
        self.needs_act = False
        self._halting = None  # whoever pressed start wants it running
        self._orphan = False
        if self.driving and self._lost_since is not None:
            # Started at the pad while we cannot see the game. That belt is
            # the walker's own: slowing and pausing it by the signal-loss
            # rules would make the remote useless. Let go until the game is
            # back in view.
            self._release()
            self.notes.append("released")
        if self.driving:
            self.belt_on = True
            self._belt_on_since = now
            self._zero_since = None
            self._starting = True
            self._ramp_from = 0.0
            self._in_step = False
            self.commanded_kmh = 0.0
            self._ours = True
        else:
            self._ours = False

    def notify_disconnected(self) -> None:
        """The treadmill link dropped; we no longer know the belt state."""
        if self.driving:
            self.needs_act = True
            self._orphan = self._orphan or self.belt_on
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None
        self._starting = False
        # A stop the belt was not seen to obey stays owed (self._halting is
        # kept): the pad runs on when the link drops. What it did before the
        # drop says nothing about what it does when it is back.
        self._halt_speed = None
        self._was_connected = False  # so that old packets are not mistaken for news
        self._view_kmh = None

    def resume(self) -> None:
        """Explicit resume (dashboard button)."""
        self._resume_at = self._now
        self._resume_lifted_stop = self.user_stopped
        self.user_stopped = False
        self.needs_act = False
        self.ceiling_kmh = None
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
        self.ceiling_kmh = None

    @property
    def halt_pending(self) -> bool:
        """A stop of ours is on its way: the belt may still be coasting down."""
        return self._halting is not None

    @property
    def halt_owed(self) -> bool:
        """A belt that we were driving has not been seen to obey its stop:
        reason enough to reach for the treadmill again."""
        return self._halting is not None and (self._ours or self._orphan)

    @property
    def belt_ours(self) -> bool:
        """A belt that turns because of us, or may: one we are driving, one
        whose stop has not been seen to work, or one that was running when
        the link dropped. (What ``shutdown`` would stop - and what is left
        running if the companion is killed instead.)"""
        return (self.driving and self.belt_on) or self._ours or self._orphan

    @property
    def belt_out_of_sight(self) -> bool:
        """The link dropped under a belt we were driving, and the belt has
        not reported since: it is probably still running."""
        return self._orphan

    def expect_halt(self, now: float, action: Action = Action.STOP) -> None:
        """A stop was sent to the belt from outside the governor (the
        dashboard's STOP button) - or could not be sent at all: keep asking
        until the belt has stopped."""
        self._halting = action
        self._halt_at = now
        self._halt_speed = None
        self._still_packets = 0  # only packets from after the stop count
        if action is Action.STOP:
            self._stop_at = now

    def speed_command_failed(self) -> None:
        """The latest speed command did not get through: the belt is still
        set to what it was set to before, and the next attempt starts there."""
        self.commanded_kmh = self._before_last_command

    # ---- helpers ---------------------------------------------------------------
    @property
    def wanted_kmh(self) -> float:
        """The clamped speed the governor is steering towards (for display)."""
        return self._want_kmh

    def _top(self, belt: Belt) -> float:
        return min(self.limits.max_speed_kmh, belt.max_kmh)

    def _clamp(self, kmh: float, belt: Belt, top: float | None = None) -> float:
        """Clamp to the limits and snap to the belt's speed grid. 0 stays 0."""
        if kmh <= 0:
            return 0.0
        if top is None:
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

    def _at_most(self, kmh: float, belt: Belt) -> float:
        """The fastest speed the belt can run at that is not above ``kmh``
        (its minimum, if there is none)."""
        return max(self._toward(kmh, belt.step_kmh, rising=True), belt.min_kmh)

    @staticmethod
    def _grid(belt: Belt) -> float:
        return belt.step_kmh if belt.step_kmh > 0 else 0.1

    def _belt_off(self, now: float) -> None:
        if self.belt_on:
            self._belt_off_at = now  # (when it was switched off - not every tick it stays off)
        self.belt_on = False
        self.commanded_kmh = 0.0
        self._zero_since = None
        self._starting = False
        self._ramp_from = 0.0
        self._far_since = None

    def _halt(self, now: float, commands: list[Command], action: Action, reason: str) -> None:
        """Ask the belt to stop, and remember to check that it did: the
        command can fail or get lost, and a belt left running is the one
        failure nobody is watching for."""
        commands.append(Command(action, reason=reason))
        self.expect_halt(now, action)

    def _pause(self, now: float, commands: list[Command], reason: str) -> None:
        if self.belt_on:
            self._halt(now, commands, Action.PAUSE, reason)
        self._belt_off(now)

    def _idle(self, phase: Phase, reason: str) -> None:
        self.phase = phase
        self.reason = reason
        self._want_kmh = 0.0

    def _current_kmh(self, belt: Belt) -> float:
        """The speed the belt is set to, as far as we know."""
        if self.commanded_kmh > 0:
            return self.commanded_kmh
        return max(self._ramp_from or 0.0, belt.min_kmh)

    def _release(self) -> None:
        """Let go of the session: the belt is not ours until a fresh frame
        says there is one."""
        self.driving = False
        self._session = None
        self.needs_act = False
        self.ceiling_kmh = None
        self._orphan = False

    def _expire_resume(self, now: float) -> None:
        """A Resume is permission to start the belt *now*. If nothing could
        be done with it - the game out of view, the treadmill out of reach -
        it must not still be good when someone uncovers the game window an
        hour later."""
        if self._resume_at is None or now - self._resume_at <= RESUME_VALID_S:
            return
        self._resume_at = None
        if self.driving:
            self.needs_act = True
        if self._resume_lifted_stop:
            self.user_stopped = True
        self.notes.append("resume_expired")

    def _watch(self, now: float, belt: Belt, linked_now: bool) -> None:
        """Take note of a new data packet from the treadmill."""
        if linked_now:
            self._linked_at = now
            self._packets = self._still_packets = 0
            self._speed_prev = self._speed_last = None
            self._steady_since = None
            # (The stamp a driver sets when it connects is not a packet.)
            new = belt.data_at is None
        else:
            new = belt.data_at is None or belt.data_at != self._stamp
        self._stamp = belt.data_at
        if not new:
            return
        self._packets += 1
        self._packet_at = now
        self._speed_prev, self._speed_last = self._speed_last, belt.speed_kmh
        self._still_packets = self._still_packets + 1 if belt.speed_kmh <= _MOVING_EPS_KMH else 0
        if self._steady_since is None or abs(belt.speed_kmh - self._steady_kmh) > _SAME_SPEED_KMH:
            self._steady_kmh, self._steady_since = belt.speed_kmh, now
        self._steady_seen = now

    def _agreed_speed(self, belt: Belt) -> float | None:
        """The belt's speed, if the two newest packets agree that it moves:
        the slower of the two. One packet alone can be garbage."""
        a, b = self._speed_prev, self._speed_last
        if a is None or b is None or belt.speed_kmh <= _MOVING_EPS_KMH:
            return None
        if min(a, b) <= _MOVING_EPS_KMH or abs(a - b) > _AGREE_KMH:
            return None
        return min(a, b)

    def _packets_disagree(self) -> bool:
        """The newest packet says something quite different from the one
        before it: it may be garbage."""
        a, b = self._speed_prev, self._speed_last
        return a is not None and b is not None and abs(a - b) > _AGREE_KMH

    def _settled(self) -> bool:
        """The belt has reported one and the same speed for a while, and
        nothing was commanded meanwhile."""
        return self._steady_since is not None and self._steady_seen - self._steady_since >= _SETTLE_S

    def _work_from(self, kmh: float, belt: Belt) -> float:
        """The belt runs at ``kmh``, whatever we asked for: make that the
        starting point of the next speed command. Returns it, on the grid
        and inside the cap."""
        base = min(self._at_most(kmh + _REPORT_SLACK_KMH, belt), max(self._clamp(self._top(belt), belt), belt.min_kmh))
        self.commanded_kmh = 0.0
        self._ramp_from = base
        self._in_step = True
        self._steady_since = None
        self._far_since = None
        return base

    # ---- the tick --------------------------------------------------------------
    def step(self, now: float, reading: Reading, belt: Belt) -> list[Command]:
        """Advance the state machine; returns the commands to send, in order."""
        commands: list[Command] = []
        lim = self.limits
        self._now = now
        fresh = reading.age_s <= lim.signal_timeout_s

        if not belt.connected:
            if self._was_connected:
                self.notify_disconnected()
            self._view_kmh = None
            # A session that begins while there is no treadmill is not one
            # that may start the belt the moment a treadmill appears - and a
            # character seen standing before the link dropped says nothing
            # about a key pressed now.
            self._saw_inactive = False
            self._prev_moving = None
            self._expire_resume(now)
            if fresh and not reading.active:
                # The session ended while the belt is out of reach. If it was
                # running when the link dropped it is running still: the stop
                # it would have got is owed.
                if self.driving and self._orphan and self._halting is None:
                    self.expect_halt(now)
                self._orphan = self._orphan and not self.driving
                self.driving = False
                self.user_stopped = False
                self.needs_act = False
                self._session = None
                self.ceiling_kmh = None
            if self._halting is not None:
                if self._owed_since is None:
                    self._owed_since = now
                elif now - self._owed_since > HALT_GIVE_UP_S:
                    self._halting = None
                    self._orphan = self._ours = False
                    self.notes.append("halt_abandoned")
            self._idle(Phase.NO_TREADMILL, "treadmill not connected")
            return commands

        linked_now = not self._was_connected
        self._was_connected = True
        self._owed_since = None
        self._watch(now, belt, linked_now)
        self._view_kmh = belt.speed_kmh
        known = self._packets > 0  # the treadmill has reported since the link came up
        top = self._top(belt)
        if top < self._top_seen - 1e-9:
            self._cap_cut = True
        self._top_seen = top

        # Back after a link loss. A pad keeps running when the link drops, so
        # the belt we were driving is probably still turning - but its speed
        # only arrives with the first data packet. Once it does, the belt is
        # ours again and is followed, paused or stopped like any belt of ours
        # (whatever happened to the session while we could not reach it).
        if self._orphan and known:
            if belt.speed_kmh > _MOVING_EPS_KMH:
                self._orphan = False
                if self._halting is None and (self.user_stopped or not self.driving):
                    # A stop was asked for while the belt was out of reach.
                    self._halt(now, commands, Action.STOP, "stop requested while the treadmill was out of reach")
                elif self._halting is None:
                    self.belt_on = True
                    self.needs_act = False
                    self._belt_on_since = now
                    self._zero_since = None
                    self._starting = False
                    self._ours = True
                    self._ramp_from = None  # not from one packet: see the speed command below
                    self._last_command_at = now - lim.command_interval_s
            elif self._still_packets >= 2:
                # The belt we were driving has stopped while we could not see
                # it. Somebody stopped it - at the pad, or by switching it
                # off: the strongest "stop at the treadmill" there is.
                self._orphan = False
                self._ours = False
                if self.driving:
                    self.user_stopped = True
                    self.notes.append("found_stopped")
        settling = self._orphan and not known and now - self._linked_at < _RECONNECT_SETTLE_S

        # Did the belt obey our last stop? This is feedback, not a blind
        # retry: it is only asked again while it reports that it still moves.
        # A pause that did not take is followed by a full stop - the one
        # command every treadmill understands.
        if self._halting is not None and known:
            if self._halt_speed is None:
                self._halt_speed = belt.speed_kmh
            due = now - self._halt_at >= _HALT_RETRY_S
            if self._still_packets >= 2 and (due or self._halt_speed > _MOVING_EPS_KMH):
                # Two packets from after the stop say it stands still. (For a
                # belt that was not moving yet - a start still counting down -
                # that proves nothing before the retry time.)
                self._halting = None
                self._ours = False
            elif due:
                if belt.speed_kmh <= _MOVING_EPS_KMH:
                    if now - self._packet_at > _SETTLE_S:
                        self._halting = None  # the data has dried up: nothing more to go on
                    # (else: one packet says so; the next one settles it)
                elif belt.speed_kmh < self._halt_speed - _HALT_SLOWING_KMH:
                    # Coasting down, just slowly: give it another period.
                    self._halt_at, self._halt_speed = now, belt.speed_kmh
                else:
                    self._halt(now, commands, Action.STOP, "belt has not stopped - asking again")
        elif not self.belt_on and self._still_packets >= 2:
            self._ours = False  # it stands still: whoever starts it next, it was not us

        no_speedup = False
        if fresh:
            self._lost_since = None
            self._resume_at = None  # the signal is there: a Resume given meanwhile stands
            # "Started moving" needs a reading in which the character stood
            # still: one that is already moving when we first look (a flight
            # path, auto-run) pressed no key for us.
            if reading.synthetic:
                moving_edge = False
                self._prev_moving = None
            else:
                moving_edge = reading.moving and self._prev_moving is False
                self._prev_moving = reading.moving

            if not reading.active:
                # Session over (or never started).
                self._saw_inactive = not reading.synthetic
                if self.driving:
                    still_turning = belt.speed_kmh > _MOVING_EPS_KMH and self._halting is None
                    if (self.belt_on or still_turning) and not commands:
                        # (also a belt we think is off but that is turning, unless
                        # a stop of ours is already on its way: the session is
                        # over, it must not be left running)
                        self._halt(now, commands, Action.STOP, "session ended")
                    elif self._orphan and self._halting is None:
                        # Just reconnected, nothing heard from the belt yet:
                        # it may well be running. The stop is owed until the
                        # belt has said what it does.
                        self.expect_halt(now)
                    self._belt_off(now)
                    self._orphan = False
                self.driving = False
                self.user_stopped = False
                self.needs_act = False
                self._session = None
                self.ceiling_kmh = None
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
                self.ceiling_kmh = None
            self._saw_inactive = False
            self.driving = True

            if reading.paused:
                if (
                    not self.belt_on
                    and self._halting is None
                    and now - self._belt_off_at > lim.start_grace_s
                    and self._agreed_speed(belt) is not None
                ):
                    # A belt that turns under a paused session - started at
                    # the pad while we could not see the game, say - is
                    # paused like any other belt of this session.
                    self.belt_on = self._ours = True
                self._pause(now, commands, "paused")
                if not reading.afk:
                    # A pause the player asked for also acknowledges any
                    # earlier stop, so pause -> resume is always a way to
                    # restart the belt. The addon's automatic AFK pause is
                    # not that: nobody was there to ask for anything.
                    self.user_stopped = False
                    self.needs_act = False
                    self.ceiling_kmh = None
                self._idle(Phase.PAUSED, "paused")
                return commands

            if self.user_stopped:
                self._idle(Phase.USER_STOPPED, "stopped at the treadmill - pause and resume to continue")
                return commands

            want = self._target_kmh = self._clamp(reading.target_kmh, belt)

            # A belt that is already turning (and that we did not just switch
            # off ourselves) can be adopted without any surprise start - once
            # two packets in a row say that it turns. (Not while the source
            # wants it stopped anyway: it would only be paused again.)
            if (
                not self.belt_on
                and known
                and want > 0
                and belt.speed_kmh > _MOVING_EPS_KMH
                and now - self._belt_off_at > lim.start_grace_s
            ):
                agreed = self._agreed_speed(belt)
                if agreed is None:
                    # One packet says so. Until the next, do not send a start
                    # at a belt that may be moving.
                    self.phase = Phase.WAITING if self.needs_act else Phase.STARTING
                    self.reason = "move your character to start the belt" if self.needs_act else "checking the belt"
                    self._want_kmh = 0.0
                    return commands
                self.belt_on = True
                self.needs_act = False
                self._halting = None
                self._orphan = False
                self._belt_on_since = now
                self._starting = False
                self._ours = True
                self._ramp_from = min(agreed, top)
                self._in_step = True
                self._last_command_at = now - lim.command_interval_s

            if self.needs_act or settling:
                if settling or (lim.resume_requires_movement and not moving_edge):
                    self._idle(Phase.WAITING, "move your character to start the belt")
                    return commands
                self.needs_act = False

            if self.ceiling_kmh is not None:
                want = min(want, self.ceiling_kmh)
            # The frame is still good enough to hold on to, but it says what
            # the character did a second ago: no reason to go faster.
            no_speedup = reading.age_s > SPEEDUP_MAX_AGE_S
            run_phase = Phase.FOLLOWING
            self.reason = (
                "following" if self.ceiling_kmh is None else "following - held at the speed set at the treadmill"
            )
        else:
            # Signal lost. Whatever starts while we cannot see is not a
            # session we watched begin, and a character seen standing before
            # the picture went is no proof of a key pressed when it returns.
            self._saw_inactive = False
            self._prev_moving = None
            if not self.driving:
                self._idle(Phase.IDLE, "no signal")
                return commands
            if self._lost_since is None:
                # Counted from the last frame, not from the moment the loss
                # was declared: "holds for 5 seconds" means 5 seconds.
                self._lost_since = now - reading.age_s
            lost_for = now - self._lost_since
            if not self.belt_on:
                self._expire_resume(now)
                if lost_for >= ABANDONED_AFTER_S:
                    # Nobody came back. The session is as good as over: stop
                    # treating the belt as ours (the pad's remote works as if
                    # Walka were not there).
                    self._release()
                    self.notes.append("abandoned")
                    self._idle(Phase.IDLE, "no signal")
                    return commands
            if self.user_stopped:
                self._idle(Phase.USER_STOPPED, "stopped at the treadmill")
                return commands
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
            if want > 0:
                # ... and never ask for more than the belt is doing: what we
                # commanded last may not be what it runs at (a command that
                # got lost, a speed lowered with the remote).
                want = min(want, self._at_most(max(belt.speed_kmh, belt.min_kmh) + _REPORT_SLACK_KMH, belt))
            if self.ceiling_kmh is not None:
                want = min(want, self.ceiling_kmh)

        self._want_kmh = want

        if want <= 0:
            # The source wants the belt stopped while the session stays active
            # (character standing with a stand speed of 0) - or no speed the
            # belt can run at is allowed by the cap.
            capped = top < belt.min_kmh - 1e-9
            self._pause(now, commands, "speed cap is below the belt's minimum" if capped else "target speed is zero")
            self.phase = Phase.STANDING
            self.reason = "the speed cap is below the treadmill's minimum speed" if capped else "standing still"
            return commands

        if not self.belt_on:
            # Only reachable with a live signal (see the lost branch above).
            if no_speedup:
                # A start is the biggest speed-up there is: not on an old picture.
                self._idle(Phase.HOLDING, "signal lost")
                return commands
            since_stop = now - self._stop_at
            if since_stop < lim.restart_holdoff_s or (belt.busy and since_stop < 3 * lim.restart_holdoff_s):
                # Too soon after a stop: the start would be acknowledged and
                # ignored, and we would then wait for a belt that never moves.
                self.phase = Phase.STARTING
                self.reason = "waiting for the treadmill to finish its previous workout"
                return commands
            commands.append(Command(Action.START, reason="session active"))
            self._halting = None
            self._orphan = False
            self.belt_on = True
            self._belt_on_since = now
            self._zero_since = None
            self._starting = True
            self._ours = True
            # Treadmills start at their own minimum speed; ramp up from there
            # - whatever speed the belt reports while it is still coasting
            # down from before, or in one garbled packet.
            self.commanded_kmh = 0.0
            self._ramp_from = 0.0
            self._in_step = False
            self._last_command_at = now
            self.phase = Phase.STARTING
            self.reason = "starting the belt"
            return commands

        # Belt believed running: check that it really is.
        moving = belt.speed_kmh > _MOVING_EPS_KMH
        if not moving:
            if self._zero_since is None:
                self._zero_since = now
            allowed = lim.start_grace_s if self._starting else lim.stall_s
            since = self._belt_on_since if self._starting else self._zero_since
            if now - since > allowed:
                # We only know that no movement is reported - the data may
                # have dried up under a belt that still runs, or a start may
                # yet come through. Make it true before treating it as fact.
                self._halt(now, commands, Action.PAUSE, "belt is not moving")
                self.notify_external_stop()
                self._idle(Phase.USER_STOPPED, "belt is not moving - pause and resume to restart")
                return commands
            if self._starting:
                self.phase = Phase.STARTING
                self.reason = "waiting for the belt to start"
                return commands
        else:
            self._zero_since = None
            self._starting = False

        self.phase = run_phase
        follow = self._check_belt_follows(now, belt, want, fresh, may_rise=not no_speedup)
        if follow is not None:
            self._want_kmh = follow
            self._speed_command(now, belt, follow, commands, may_rise=not no_speedup)
        return commands

    def _rates(self, belt: Belt) -> tuple[float, float]:
        """How far one speed command may go up and down."""
        # A longer time between commands means a slower ramp, never a bigger
        # jump: the size of one change is that of one second's worth at
        # most. (With the interval set to five seconds - to hear fewer
        # beeps, say - each command would otherwise move the belt 0.6 mph
        # at once.)
        window = min(self.limits.command_interval_s, 1.0)
        # At least one grid step per command, or a rate limit smaller than
        # the belt's step would mean the speed could never change at all.
        return (
            max(self.limits.accel_kmh_per_s * window, belt.step_kmh),
            max(self.limits.decel_kmh_per_s * window, belt.step_kmh),
        )

    def _reach(self, belt: Belt) -> float:
        """The fastest speed that may be asked for now: one increment above
        what the belt reports. A command that got lost, or timed out, must
        not be built on - what counts is what the belt does."""
        up, _down = self._rates(belt)
        # (Pads cut the speed they report off to 0.01 km/h. Where one
        # increment is no more than one grid step, that would put the next
        # step for ever just out of reach: allow for it, there only.)
        slack = max(0.0, _REPORT_SLACK_KMH - (up - belt.step_kmh)) if belt.step_kmh > 0 else 0.0
        return self._at_most(max(belt.speed_kmh, belt.min_kmh) + up + slack, belt)

    def _check_belt_follows(self, now: float, belt: Belt, want: float, fresh: bool, *, may_rise: bool) -> float | None:
        """Does the running belt do what we asked?

        If it has settled at another speed, the walker changed it at the
        pad (or a command of ours got lost): what the belt reports is the
        truth, and the next command starts from there. A speed *lowered* at
        the pad becomes a ceiling. Returns the speed to steer for - or None
        when nothing should be sent yet."""
        # Measured against the last speed commanded - or, before the first
        # command, against the speed a belt found running was taken over at.
        asked = self.commanded_kmh if self.commanded_kmh > 0 else (self._ramp_from or 0.0)
        if asked <= 0 or belt.speed_kmh <= _MOVING_EPS_KMH:
            self._far_since = None
            return want
        grid = self._grid(belt)
        up, _down = self._rates(belt)
        off = belt.speed_kmh - asked
        if abs(off) <= grid / 2:
            self._in_step = True
        # "Far below": by more than one increment - more than one command of
        # ours that got lost can explain.
        far = max(up, grid) + grid / 2
        far_below = off < -far
        if not far_below:
            self._far_since = None
        elif self._far_since is None or belt.speed_kmh < self._far_low - _SAME_SPEED_KMH:
            # (While it is still slowing down, the clock for "this belt never
            # settles" starts again.)
            self._far_since, self._far_low = now, belt.speed_kmh
        # (A belt a little below what was asked can still block every further
        # speed-up: the next grid step is then out of reach.)
        stuck = may_rise and want > asked + grid / 2 and self._reach(belt) <= asked + 1e-9
        if belt.target_kmh is not None and belt.target_kmh < asked - far:
            # The treadmill itself says it was set to a lower speed.
            self.ceiling_kmh = self._work_from(belt.target_kmh, belt)
            self.notes.append("ceiling")
        elif (self._settled() and (off < -grid / 2 or (off > grid / 2 and self._in_step) or stuck)) or (
            self._far_since is not None and now - self._far_since >= _MISMATCH_MAX_S
        ):
            base = self._work_from(max(belt.speed_kmh, belt.min_kmh), belt)
            if far_below:
                # Far below what was asked, and not on its way anywhere:
                # slowed with the remote. Honour it.
                self.ceiling_kmh = base
                self.notes.append("ceiling")
            elif self.ceiling_kmh is not None and base > self.ceiling_kmh + grid / 2:
                # Raised with the remote: the walker's ceiling moves up with
                # it - and is gone once it no longer holds the belt below
                # what the game asks for.
                reaches_target = fresh and base >= self._target_kmh - grid / 2
                self.ceiling_kmh = None if reaches_target else base
        elif far_below:
            # Far slower than asked, and not settled: the walker may be
            # holding the remote's minus button - or this is one garbled
            # packet. Either way anything sent now would be a guess.
            self.reason = "the belt is slower than asked - waiting to see where it settles"
            return None
        else:
            return want
        if fresh:
            want = self._target_kmh
        return want if self.ceiling_kmh is None else min(want, self.ceiling_kmh)

    def _speed_command(self, now: float, belt: Belt, want: float, commands: list[Command], *, may_rise: bool) -> None:
        """Send the next speed command, if one is due: rate-limited, and
        measured from where the belt is."""
        top = self._top(belt)
        step = belt.step_kmh
        up, down = self._rates(belt)
        moving = belt.speed_kmh > _MOVING_EPS_KMH
        over_cap = self.commanded_kmh > top + 1e-9
        # The first reaction to a lowered cap does not wait for the interval
        # (the belt may still be accelerating towards the speed commanded
        # before).
        if now - self._last_command_at < self.limits.command_interval_s and not (over_cap and self._cap_cut):
            return
        first = self.commanded_kmh <= 0
        if first and self._ramp_from is None:
            # A belt found running. One packet is not enough to give it a
            # speed: a garbled one would send it to the cap.
            agreed = self._agreed_speed(belt)
            if agreed is None:
                return
            self._ramp_from = min(agreed, top)
            self._in_step = True
        current = self._current_kmh(belt)
        if over_cap:
            # A speed commanded before the cap was lowered. Come down to the
            # cap at the deceleration limit, not in one jump - from where
            # the belt really is, if it had not got there yet.
            believable = self._agreed_speed(belt) is not None  # (not on the word of one packet)
            if believable:
                current = min(current, max(belt.speed_kmh, belt.min_kmh))
            nxt = max(self._toward(current - down, step, rising=False), want)
            nxt = self._clamp(nxt, belt, top=max(top, min(nxt, belt.max_kmh)))
            if believable:
                nxt = min(nxt, self._reach(belt))  # (a belt well under the new cap is not sent up to it)
        else:
            if not may_rise:
                want = min(want, current)
            delta = want - current
            if not first and abs(delta) < max(step, 0.01) / 2:
                return
            if delta > 0:
                if not moving or self._packets_disagree():
                    # No speed is reported, or one that the packet before it
                    # contradicts: nothing to measure a speed-up from.
                    return
                nxt = min(self._toward(current + min(delta, up), step, rising=True), want)
            else:
                nxt = max(self._toward(current + max(delta, -down), step, rising=False), want)
            nxt = self._clamp(nxt, belt)
            if delta <= 0 and nxt > current + 1e-9:
                # (Snapping to the grid must not turn "hold" into "a little
                # faster" - on a belt whose minimum is off its own grid.)
                nxt = self._at_most(current, belt)
            if moving:
                nxt = min(nxt, self._reach(belt))
            change = nxt - self.commanded_kmh
            if not first and (abs(change) < 1e-9 or (change > 0) != (delta > 0)):
                return  # never the same speed twice, never against the direction wanted
        if nxt <= 0:
            return
        commands.append(Command(Action.SET_SPEED, speed_kmh=nxt, reason=self.reason))
        self._before_last_command = self.commanded_kmh
        self.commanded_kmh = nxt
        self._last_command_at = now
        self._cap_cut = False
        self._steady_since = None  # "settled" is measured from after this command

    def shutdown(self, now: float = 0.0, belt: Belt | None = None) -> list[Command]:
        """Commands that leave the treadmill safe when the companion exits."""
        commands: list[Command] = []
        speed = self._view_kmh if belt is None else (belt.speed_kmh if belt.connected else None)
        turning = speed is not None and speed > _MOVING_EPS_KMH
        if self.driving and self.belt_on:
            commands.append(Command(Action.STOP, reason="companion exiting"))
        elif self._halting is not None and (turning or speed is None or self._still_packets < 2):
            # A stop of ours that the belt has not been seen to obey: nobody
            # will be left to ask again.
            commands.append(Command(Action.STOP, reason="companion exiting - belt not seen to stop"))
        elif self._ours and turning:
            commands.append(Command(Action.STOP, reason="companion exiting - belt still moving"))
        self.driving = False
        self._halting = None
        self._orphan = self._ours = False
        self.ceiling_kmh = None
        self._belt_off(now)
        self._idle(Phase.IDLE, "stopped")
        return commands
