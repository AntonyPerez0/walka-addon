"""Behavioural tests for the belt-safety governor, run against a toy belt."""

from __future__ import annotations

import math

import pytest

from walka.governor import Action, Belt, Command, Governor, Limits, Phase, Reading


class ToyBelt:
    """A belt that starts after a countdown and ramps towards its target."""

    def __init__(self, *, min_kmh=1.0, max_kmh=6.0, step=0.1, countdown=3.0, ramp=0.5):
        self.min_kmh, self.max_kmh, self.step = min_kmh, max_kmh, step
        self.countdown, self.ramp = countdown, ramp
        self.connected = True
        self.running = False
        self.speed = 0.0
        self.target = 0.0
        self.count_left = 0.0
        self.log: list[Command] = []

    def view(self) -> Belt:
        return Belt(self.connected, self.speed, self.min_kmh, self.max_kmh, self.step)

    def apply(self, commands: list[Command]) -> None:
        for c in commands:
            self.log.append(c)
            if c.action is Action.START:
                if not self.running:
                    self.running = True
                    self.count_left = self.countdown
                    self.target = self.min_kmh
            elif c.action is Action.SET_SPEED:
                assert c.speed_kmh is not None
                self.target = c.speed_kmh
            else:
                self.running = False
                self.target = 0.0

    def advance(self, dt: float) -> None:
        if self.running and self.count_left > 0:
            self.count_left -= dt
            return
        goal = self.target if self.running else 0.0
        rate = self.ramp if self.running else 2.0
        if self.speed < goal:
            self.speed = min(goal, self.speed + rate * dt)
        else:
            self.speed = max(goal, self.speed - rate * dt)


class Rig:
    def __init__(self, limits: Limits | None = None, **belt_kwargs):
        self.gov = Governor(limits or Limits())
        self.belt = ToyBelt(**belt_kwargs)
        self.now = 0.0
        self.last_good = -math.inf
        self.max_seen = 0.0
        self.speeds: list[float] = []

    def run(self, seconds: float, reading: Reading | None, dt: float = 0.25) -> None:
        """Advance time. ``reading=None`` simulates a lost signal."""
        steps = int(round(seconds / dt))
        for _ in range(steps):
            self.now += dt
            if reading is None:
                r = Reading(age_s=self.now - self.last_good, active=True, target_kmh=99.0)
            else:
                self.last_good = self.now
                r = reading
            self.belt.apply(self.gov.step(self.now, r, self.belt.view()))
            self.belt.advance(dt)
            self.max_seen = max(self.max_seen, self.belt.speed)
            self.speeds.append(self.belt.speed)

    def actions(self) -> list[Action]:
        return [c.action for c in self.belt.log]


IDLE = Reading(age_s=0.0, active=False)


def live(target: float, *, moving: bool = True, session: int = 1, paused: bool = False) -> Reading:
    return Reading(age_s=0.0, active=True, paused=paused, moving=moving, target_kmh=target, session=session)


def test_hands_off_without_session():
    rig = Rig()
    rig.run(30, IDLE)
    assert rig.belt.log == []
    assert rig.gov.phase is Phase.IDLE


def test_no_treadmill():
    rig = Rig()
    rig.belt.connected = False
    rig.run(5, live(3.0))
    assert rig.belt.log == []
    assert rig.gov.phase is Phase.NO_TREADMILL


def test_session_start_ramps_to_target_and_stop_ends_it():
    rig = Rig()
    rig.run(2, IDLE)
    rig.run(30, live(3.2))
    assert rig.actions()[0] is Action.START
    assert rig.belt.speed == pytest.approx(3.2)
    assert rig.gov.phase is Phase.FOLLOWING
    # No command is sent while the belt is still counting down.
    first_speed = next(c for c in rig.belt.log if c.action is Action.SET_SPEED)
    assert first_speed.speed_kmh is not None and first_speed.speed_kmh <= 1.5 + 1e-9
    rig.run(3, IDLE)
    assert rig.actions()[-1] is Action.STOP
    assert rig.gov.phase is Phase.IDLE
    rig.run(10, IDLE)
    assert rig.belt.speed == 0


def test_acceleration_is_rate_limited():
    rig = Rig(Limits(accel_kmh_per_s=0.5, command_interval_s=1.0))
    rig.run(1, IDLE)
    rig.run(40, live(6.0))
    speeds = [c.speed_kmh for c in rig.belt.log if c.action is Action.SET_SPEED]
    assert speeds[-1] == pytest.approx(6.0)
    for a, b in zip(speeds, speeds[1:]):
        assert b - a <= 0.5 + 1e-9
    # Commands are spaced at least command_interval apart: bounded count.
    assert len(speeds) <= 41


def test_hard_cap_wins_over_source():
    rig = Rig(Limits(max_speed_kmh=4.0))
    rig.run(1, IDLE)
    rig.run(60, live(40.95))
    assert rig.max_seen <= 4.0 + 1e-9
    assert rig.belt.speed == pytest.approx(4.0)


def test_treadmill_range_wins_over_limit():
    rig = Rig(Limits(max_speed_kmh=10.0), max_kmh=3.0)
    rig.run(1, IDLE)
    rig.run(60, live(9.0))
    assert rig.max_seen <= 3.0 + 1e-9


def test_target_below_belt_minimum_is_raised_to_minimum():
    rig = Rig(min_kmh=1.0)
    rig.run(1, IDLE)
    rig.run(20, live(0.4))
    assert rig.belt.speed == pytest.approx(1.0)


def test_off_grid_target_settles_on_nearest_step():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(30, live(3.2))
    rig.run(30, live(1.61))
    assert rig.belt.speed == pytest.approx(1.6)
    rig.run(30, live(2.46))
    assert rig.belt.speed == pytest.approx(2.5)


def test_pause_and_resume():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.run(5, live(3.0, paused=True))
    assert rig.actions()[-1] is Action.PAUSE
    assert rig.gov.phase is Phase.PAUSED
    assert rig.belt.speed == 0
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_zero_target_pauses_then_resumes():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.run(10, live(0.0, moving=False))
    assert rig.gov.phase is Phase.STANDING
    assert rig.belt.speed == 0
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_signal_loss_holds_then_slows_then_pauses():
    limits = Limits(hold_s=5, safe_speed_kmh=1.6, loss_pause_s=60, signal_timeout_s=3)
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(30, live(4.0))
    assert rig.belt.speed == pytest.approx(4.0)
    rig.run(6, None)  # timeout (3 s) + part of the hold
    assert rig.gov.phase is Phase.HOLDING
    assert rig.belt.speed == pytest.approx(4.0)
    rig.run(20, None)
    assert rig.gov.phase is Phase.SAFE
    assert rig.belt.speed == pytest.approx(1.6)
    rig.run(60, None)
    assert rig.gov.phase is Phase.WAITING
    assert rig.belt.speed == 0
    assert rig.actions()[-1] is Action.PAUSE


def test_signal_loss_never_speeds_up():
    limits = Limits(hold_s=2, safe_speed_kmh=3.0, loss_pause_s=30)
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(20, live(1.5))
    before = rig.belt.speed
    start = len(rig.speeds)
    rig.run(20, None)
    assert max(rig.speeds[start:]) <= before + 1e-9


def test_safe_speed_zero_pauses_after_hold():
    limits = Limits(hold_s=2, safe_speed_kmh=0.0)
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.run(8, None)
    assert rig.belt.speed == 0
    assert rig.gov.phase is Phase.WAITING


def test_signal_returning_during_hold_resumes_following():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.run(5, None)
    rig.run(15, live(2.0))
    assert rig.gov.phase is Phase.FOLLOWING
    assert rig.belt.speed == pytest.approx(2.0)


def test_after_loss_pause_belt_waits_for_movement():
    limits = Limits(hold_s=2, safe_speed_kmh=1.6, loss_pause_s=10)
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(20, live(3.0, moving=False))
    rig.run(40, None)
    assert rig.belt.speed == 0
    n = len(rig.belt.log)
    # Signal returns but the character just stands there: no restart.
    rig.run(20, live(3.0, moving=False))
    assert rig.gov.phase is Phase.WAITING
    assert len(rig.belt.log) == n
    # The character starts moving: belt restarts.
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_companion_started_mid_session_waits_for_movement():
    rig = Rig()
    # First reading ever is already active: we did not watch the session begin.
    rig.run(10, live(3.0, moving=False))
    assert rig.belt.log == []
    assert rig.gov.phase is Phase.WAITING
    rig.run(20, live(3.0, moving=True))
    assert rig.actions()[0] is Action.START
    assert rig.belt.speed == pytest.approx(3.0)


def test_mid_session_with_belt_already_running_is_adopted_without_start():
    rig = Rig()
    rig.belt.running, rig.belt.speed, rig.belt.target = True, 2.0, 2.0
    rig.run(20, live(3.0, moving=False))
    assert Action.START not in rig.actions()
    assert rig.belt.speed == pytest.approx(3.0)


def test_stop_at_machine_is_respected_until_explicit_resume():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    # User hits stop on the remote.
    rig.belt.running = False
    rig.gov.notify_external_stop()
    n = len(rig.belt.log)
    rig.run(30, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED
    assert len(rig.belt.log) == n
    assert rig.belt.speed == 0
    # Signal loss must not restart it either.
    rig.run(10, None)
    assert len(rig.belt.log) == n
    # Pause -> resume in the addon is the explicit restart.
    rig.run(2, live(3.0, paused=True))
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_silent_stop_is_detected_from_zero_speed():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.belt.running = False  # treadmill stops by itself, no notification
    rig.run(15, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED
    starts = rig.actions().count(Action.START)
    rig.run(30, live(3.0))
    assert rig.actions().count(Action.START) == starts


def test_brief_zero_speed_glitch_is_ignored():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    real = rig.belt.speed
    rig.belt.speed = 0.0
    rig.belt.apply(rig.gov.step(rig.now + 0.25, live(3.0), rig.belt.view()))
    rig.belt.speed = real
    rig.run(10, live(3.0))
    assert rig.gov.phase is Phase.FOLLOWING


def test_start_that_never_moves_gives_up():
    rig = Rig(countdown=1e9)
    rig.run(1, IDLE)
    rig.run(30, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED
    assert rig.actions().count(Action.START) == 1


def test_new_session_clears_user_stop():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0, session=1))
    rig.belt.running = False
    rig.gov.notify_external_stop()
    rig.run(5, live(3.0, session=1))
    rig.run(3, IDLE)
    rig.run(25, live(2.0, session=2))
    assert rig.belt.speed == pytest.approx(2.0)


def test_external_start_resumes():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    rig.belt.running = False
    rig.gov.notify_external_stop()
    rig.run(10, live(3.0))
    # User presses start on the remote.
    rig.belt.apply([Command(Action.START)])
    rig.gov.notify_external_start(rig.now)
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_treadmill_reconnect_mid_session_requires_act_if_belt_stopped():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0, moving=False))
    rig.belt.connected = False
    rig.belt.running = False
    rig.run(5, live(3.0, moving=False))
    rig.belt.speed = 0.0
    rig.belt.connected = True
    n = len(rig.belt.log)
    rig.run(10, live(3.0, moving=False))
    assert len(rig.belt.log) == n
    assert rig.gov.phase is Phase.WAITING
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_dashboard_resume_clears_waiting():
    rig = Rig()
    rig.run(10, live(3.0, moving=False))
    assert rig.gov.phase is Phase.WAITING
    rig.gov.resume()
    rig.run(25, live(3.0, moving=False))
    assert rig.belt.speed == pytest.approx(3.0)


def test_shutdown_stops_a_belt_we_drive():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(20, live(3.0))
    cmds = rig.gov.shutdown(rig.now)
    assert [c.action for c in cmds] == [Action.STOP]
    assert Governor().shutdown() == []


def test_speed_never_exceeds_limit_under_random_input():
    import random

    rng = random.Random(11)
    rig = Rig(Limits(max_speed_kmh=5.0))
    rig.run(1, IDLE)
    for _ in range(400):
        choice = rng.random()
        if choice < 0.1:
            rig.run(rng.uniform(0.25, 8), None)
        elif choice < 0.2:
            rig.run(rng.uniform(0.25, 3), IDLE)
        else:
            rig.run(
                rng.uniform(0.25, 5),
                live(
                    rng.uniform(0, 40),
                    moving=rng.random() < 0.7,
                    paused=rng.random() < 0.1,
                    session=rng.randrange(1, 4),
                ),
            )
    assert rig.max_seen <= 5.0 + 1e-9
    speeds = [c.speed_kmh for c in rig.belt.log if c.action is Action.SET_SPEED]
    assert all(s is not None and 1.0 - 1e-9 <= s <= 5.0 + 1e-9 for s in speeds)
