"""Behavioural tests for the belt-safety governor, run against a toy belt."""

from __future__ import annotations

import dataclasses
import itertools
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
        self.deaf = False  # commands are logged but never reach the belt
        self.blind = False  # the belt runs but reports no speed (stale data)

    def view(self) -> Belt:
        speed = 0.0 if self.blind else self.speed
        return Belt(self.connected, speed, self.min_kmh, self.max_kmh, self.step)

    def apply(self, commands: list[Command]) -> None:
        for c in commands:
            self.log.append(c)
            if self.deaf:
                continue
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
        self.last_reading = Reading()
        self.max_seen = 0.0
        self.speeds: list[float] = []

    def run(self, seconds: float, reading: Reading | None, dt: float = 0.25) -> None:
        """Advance time. ``reading=None`` simulates a lost signal: the source
        keeps reporting its last frame, only older - which is what
        ``BeaconSource`` does. (Stale readings with made-up contents are
        covered by ``test_contents_of_a_stale_reading_are_ignored``.)"""
        steps = int(round(seconds / dt))
        for _ in range(steps):
            self.now += dt
            if reading is None:
                r = dataclasses.replace(self.last_reading, age_s=self.now - self.last_good)
            else:
                self.last_good = self.now
                self.last_reading = reading
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
    for a, b in itertools.pairwise(speeds):
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


# ======================================================================================
# Gaps found by reading the state machine critically. Each test below failed
# against the governor as first written, or pins a rule that had no test.
# ======================================================================================

HALT = (Action.PAUSE, Action.STOP)


def started_rig(target: float = 3.0, limits: Limits | None = None, **belt_kwargs) -> Rig:
    rig = Rig(limits, **belt_kwargs)
    rig.run(1, IDLE)
    rig.run(20, live(target))
    assert rig.belt.speed == pytest.approx(target)
    return rig


def stale(age: float, **fields) -> Reading:
    return Reading(age_s=age, **fields)


# ---- the cap ---------------------------------------------------------------------------


def test_cap_below_the_belts_minimum_means_the_belt_does_not_run():
    """A UREVO pad cannot go slower than 0.6 mph (0.97 km/h); the settings
    allow a cap of 0.8 km/h. "Your speed cap always wins" then has only one
    honest outcome: the belt stays off - not "runs at its minimum, above the cap"."""
    rig = Rig(Limits(max_speed_kmh=0.8), min_kmh=0.9656, step=0.1609)
    rig.run(1, IDLE)
    rig.run(30, live(3.0))
    assert rig.belt.log == []
    assert rig.max_seen == 0.0
    assert rig.gov.phase is Phase.STANDING and "cap" in rig.gov.reason
    # Raising the cap above the minimum lets it run.
    rig.gov.limits.max_speed_kmh = 2.0
    rig.run(30, live(3.0))
    assert rig.actions()[0] is Action.START
    assert 0.9656 <= rig.belt.speed <= 2.0
    # Lowering it below the minimum again pauses the belt.
    rig.gov.limits.max_speed_kmh = 0.9
    rig.run(10, live(3.0))
    assert rig.belt.speed == 0 and rig.actions()[-1] is Action.PAUSE


def test_cap_exactly_at_the_belts_minimum_runs_at_the_minimum():
    rig = Rig(Limits(max_speed_kmh=1.0), min_kmh=1.0)
    rig.run(1, IDLE)
    rig.run(30, live(5.0))
    assert rig.belt.speed == pytest.approx(1.0) and rig.max_seen <= 1.0 + 1e-9


def test_treadmill_advertising_max_below_min_never_runs():
    rig = Rig(min_kmh=2.0, max_kmh=1.0)
    rig.run(1, IDLE)
    rig.run(30, live(3.0))
    assert rig.belt.log == [] and rig.max_seen == 0.0


def test_cap_off_the_grid_rounds_down_never_up():
    # 3.0 mph cap stored as 4.83 km/h on a 0.1 mph grid: 3.0 mph exactly, not 3.1.
    rig = Rig(Limits(max_speed_kmh=4.83), min_kmh=0.6 * 1.609344, max_kmh=4.0 * 1.609344, step=0.1609344)
    rig.run(1, IDLE)
    rig.run(60, live(40.95))
    assert rig.belt.speed == pytest.approx(3.0 * 1.609344)
    # A cap between two steps: the lower one.
    rig = Rig(Limits(max_speed_kmh=4.0), min_kmh=0.6 * 1.609344, max_kmh=4.0 * 1.609344, step=0.1609344)
    rig.run(1, IDLE)
    rig.run(60, live(40.95))
    assert rig.belt.speed == pytest.approx(2.4 * 1.609344)
    assert rig.max_seen <= 4.0


def test_cap_lowered_at_runtime_takes_effect_while_following():
    rig = started_rig(5.0, Limits(max_speed_kmh=6.0))
    rig.gov.limits.max_speed_kmh = 2.0  # the dashboard mutates the same Limits object
    before = len(rig.belt.log)
    rig.run(1.25, live(5.0))
    # The very next command is already inside the new cap: the cap outranks
    # the gentle-deceleration rule (the belt itself ramps down).
    new = [c.speed_kmh for c in rig.belt.log[before:] if c.action is Action.SET_SPEED]
    assert new == [2.0]
    rig.run(10, live(5.0))
    assert rig.belt.speed == pytest.approx(2.0)
    assert all(c.speed_kmh <= 2.0 + 1e-9 for c in rig.belt.log[before:] if c.action is Action.SET_SPEED)
    # Raising it again lets the belt climb back, gently.
    before = len(rig.belt.log)
    rig.gov.limits.max_speed_kmh = 4.0
    rig.run(10, live(5.0))
    assert rig.belt.speed == pytest.approx(4.0)
    ups = [c.speed_kmh for c in rig.belt.log[before:] if c.action is Action.SET_SPEED]
    assert all(b - a <= 0.5 + 1e-9 for a, b in itertools.pairwise([2.0] + ups))


def test_cap_lowered_while_the_signal_is_lost_is_not_ignored():
    """While holding the last speed after a signal loss, "hold" must not
    outrank the cap."""
    rig = started_rig(5.0, Limits(max_speed_kmh=6.0, hold_s=60))
    rig.run(4, None)
    assert rig.gov.phase is Phase.HOLDING and rig.belt.speed == pytest.approx(5.0)
    rig.gov.limits.max_speed_kmh = 2.0
    rig.run(8, None)
    assert rig.gov.phase is Phase.HOLDING
    assert rig.belt.speed == pytest.approx(2.0)


def test_belt_without_a_speed_grid():
    rig = Rig(Limits(max_speed_kmh=4.0), step=0.0)
    rig.run(1, IDLE)
    rig.run(40, live(3.333))
    assert rig.belt.speed == pytest.approx(3.333)
    rig.run(40, live(9.0))
    assert rig.belt.speed == pytest.approx(4.0) and rig.max_seen <= 4.0 + 1e-9
    speeds = [c.speed_kmh for c in rig.belt.log if c.action is Action.SET_SPEED]
    assert all(b - a <= 0.5 + 1e-9 for a, b in itertools.pairwise(speeds))


@pytest.mark.parametrize("target", [float("inf"), 1e300, 4095 * 0.01])
def test_absurd_targets_are_capped(target: float):
    rig = Rig(Limits(max_speed_kmh=3.0))
    rig.run(1, IDLE)
    rig.run(40, live(target))
    assert rig.belt.speed == pytest.approx(3.0) and rig.max_seen <= 3.0 + 1e-9


@pytest.mark.parametrize("target", [-5.0, -0.0, 0.0])
def test_non_positive_targets_stop_the_belt(target: float):
    rig = started_rig(3.0)
    rig.run(5, live(target))
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.STANDING


def test_every_step_of_the_mph_grid_is_reachable_from_below_and_above():
    """UREVO's US firmware runs on a 0.1 mph grid. Every step must be
    reachable - including those whose km/h value has awkward decimals (3.0 mph
    is 4.828032 km/h): the belt used to stop one step short on the way up."""
    mph = 1.609344
    for tenths in range(6, 41):
        target = tenths * mph / 10
        rig = Rig(Limits(max_speed_kmh=7.0), min_kmh=0.6 * mph, max_kmh=4.0 * mph, step=0.1 * mph)
        rig.run(1, IDLE)
        rig.run(25, live(target))
        assert rig.belt.speed == pytest.approx(target, abs=1e-6), f"{tenths / 10} mph not reached from below"
        rig.run(12, live(4.0 * mph))
        rig.run(12, live(target))
        assert rig.belt.speed == pytest.approx(target, abs=1e-6), f"{tenths / 10} mph not reached from above"
        # A target a little off the step settles on the nearest step.
        rig.run(5, live(target + 0.05))
        assert rig.belt.speed == pytest.approx(target, abs=1e-6)
        assert rig.max_seen <= 4.0 * mph + 1e-9


def test_default_stand_speed_and_cap_land_on_their_mph_steps():
    """The shipped defaults on the shipped pad: 1.6 km/h is 1.0 mph and the
    3.0 mph cap is stored as 4.83 km/h."""
    mph = 1.609344
    rig = Rig(Limits(max_speed_kmh=4.83), min_kmh=0.6 * mph, max_kmh=4.0 * mph, step=0.1 * mph)
    rig.run(1, IDLE)
    rig.run(20, live(1.6))
    assert rig.belt.speed == pytest.approx(1.0 * mph, abs=1e-6)
    rig.run(20, live(9.0))
    assert rig.belt.speed == pytest.approx(3.0 * mph, abs=1e-6)
    assert rig.max_seen <= 4.83


def test_cap_between_the_minimum_and_the_next_grid_point():
    """A pad whose minimum is not a multiple of its step (0.8 km/h, 0.5 steps)
    with a cap of 0.9: the only allowed speed is the minimum itself - not the
    next grid point above the cap."""
    rig = Rig(Limits(max_speed_kmh=0.9), min_kmh=0.8, max_kmh=6.0, step=0.5)
    rig.run(1, IDLE)
    rig.run(30, live(3.0))
    assert rig.belt.speed == pytest.approx(0.8)
    assert rig.max_seen <= 0.9 + 1e-9
    speeds = [c.speed_kmh for c in rig.belt.log if c.action is Action.SET_SPEED]
    assert speeds and max(speeds) <= 0.9 + 1e-9


def test_lowering_the_cap_does_not_wait_for_the_command_interval():
    """The belt is still accelerating towards the last commanded speed; a
    lower cap must be sent now, not up to command_interval_s later."""
    rig = Rig(Limits(max_speed_kmh=6.0, command_interval_s=5.0, accel_kmh_per_s=0.5))
    rig.run(1, IDLE)
    rig.run(7, live(6.0))  # one speed command so far: 1.0 -> 3.5, and the belt is on its way there
    assert [c.speed_kmh for c in rig.belt.log if c.action is Action.SET_SPEED] == [3.5]
    assert 1.0 < rig.belt.speed < 2.5
    rig.gov.limits.max_speed_kmh = 2.5
    rig.run(0.25, live(6.0))
    assert rig.belt.log[-1] == Command(Action.SET_SPEED, speed_kmh=2.5, reason="following")
    mark = len(rig.speeds)
    rig.run(10, live(6.0))
    assert rig.belt.speed == pytest.approx(2.5)
    assert max(rig.speeds[mark:]) <= 2.5 + 1e-9  # it never climbed past the new cap


# ---- gentle changes --------------------------------------------------------------------


@pytest.mark.parametrize("interval,accel,decel", [(0.5, 0.5, 1.0), (1.0, 1.0, 2.0), (2.5, 0.05, 0.1), (5.0, 0.5, 1.0)])
def test_rate_limits_hold_at_the_edges_of_the_allowed_settings(interval: float, accel: float, decel: float):
    rig = Rig(Limits(command_interval_s=interval, accel_kmh_per_s=accel, decel_kmh_per_s=decel))
    rig.run(1, IDLE)
    times: list[tuple[float, float]] = []
    for target, seconds in ((6.0, 150), (1.0, 150), (4.0, 120)):
        for _ in range(int(seconds / 0.25)):
            before = len(rig.belt.log)
            rig.run(0.25, live(target))
            for c in rig.belt.log[before:]:
                if c.action is Action.SET_SPEED:
                    times.append((rig.now, c.speed_kmh))
    assert len(times) >= 4
    for (t0, v0), (t1, v1) in itertools.pairwise(times):
        assert t1 - t0 >= interval - 1e-9, "speed commands closer together than the interval"
        # Never more than the rate limit allows for the time since the last command.
        assert v1 - v0 <= accel * (t1 - t0) + 1e-9
        assert v0 - v1 <= decel * (t1 - t0) + 1e-9
    assert rig.belt.speed == pytest.approx(4.0, abs=0.11)


def test_no_speed_command_before_the_belt_moves():
    """The pad ignores speed writes until its countdown is over."""
    rig = Rig(countdown=6.0)
    rig.run(1, IDLE)
    rig.run(5.75, live(4.0))
    assert rig.actions() == [Action.START]
    rig.run(10, live(4.0))
    first = next(c for c in rig.belt.log if c.action is Action.SET_SPEED)
    assert first.speed_kmh <= 1.0 + 0.5 + 1e-9  # one step up from the minimum


def test_start_is_sent_once_not_every_tick():
    rig = Rig(countdown=8.0)
    rig.run(1, IDLE)
    rig.run(30, live(3.0))
    assert rig.actions().count(Action.START) == 1


# ---- signal loss ------------------------------------------------------------------------


def test_contents_of_a_stale_reading_are_ignored():
    """Whatever a stale reading claims - a huge target, a new session, a
    pause lifted, "no session" - it is not acted on."""
    limits = Limits(hold_s=5, safe_speed_kmh=1.6, loss_pause_s=30, signal_timeout_s=3)
    rig = started_rig(3.0, limits)
    start = len(rig.speeds)
    commands = len(rig.belt.log)
    wild = [
        stale(10, active=True, moving=True, target_kmh=40.95, session=1),
        stale(10, active=True, moving=True, target_kmh=40.95, session=99),
        stale(3.01, active=True, moving=True, target_kmh=40.95, session=2),
        stale(10, active=False),
        stale(math.inf),
        stale(10, active=True, paused=True, target_kmh=40.95, session=1),
    ]
    for _ in range(4):
        for reading in wild:
            rig.run(1, reading)
    assert max(rig.speeds[start:]) <= 3.0 + 1e-9  # never faster than when the signal went
    assert Action.START not in [c.action for c in rig.belt.log[commands:]]
    assert rig.gov.phase is Phase.SAFE and rig.belt.speed == pytest.approx(1.6)
    for _ in range(4):
        for reading in wild:
            rig.run(2, reading)
    assert rig.gov.phase is Phase.WAITING and rig.belt.speed == 0
    # ... and a stale "moving" is no deliberate act either.
    rig.run(10, stale(60, active=True, moving=True, target_kmh=3.0, session=1))
    assert rig.belt.speed == 0 and rig.actions()[-1] is Action.PAUSE


def test_signal_age_exactly_at_the_timeout_is_still_fresh():
    rig = started_rig(3.0, Limits(signal_timeout_s=3))
    rig.run(2, Reading(age_s=3.0, active=True, moving=True, target_kmh=2.0, session=1))
    assert rig.gov.phase is Phase.FOLLOWING
    rig.run(2, Reading(age_s=3.0001, active=True, moving=True, target_kmh=2.0, session=1))
    assert rig.gov.phase is Phase.HOLDING


def test_signal_loss_while_paused_changes_nothing():
    rig = started_rig(3.0)
    rig.run(5, live(3.0, paused=True))
    n = len(rig.belt.log)
    rig.run(400, None)  # longer than hold + loss_pause
    assert len(rig.belt.log) == n and rig.belt.speed == 0
    rig.run(5, live(3.0, paused=True))
    assert rig.gov.phase is Phase.PAUSED and len(rig.belt.log) == n
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)  # the resume is the deliberate act


def test_signal_loss_while_standing_does_not_start_the_belt():
    rig = started_rig(3.0)
    rig.run(10, live(0.0, moving=False))
    assert rig.gov.phase is Phase.STANDING
    n = len(rig.belt.log)
    rig.run(400, None)
    assert len(rig.belt.log) == n and rig.belt.speed == 0
    rig.run(10, live(0.0, moving=False))
    assert len(rig.belt.log) == n and rig.gov.phase is Phase.STANDING


def test_signal_loss_during_the_countdown_never_goes_beyond_minimum_speed():
    limits = Limits(hold_s=5, safe_speed_kmh=1.6, loss_pause_s=20)
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(1, live(5.0))  # START sent, belt counting down
    assert rig.actions() == [Action.START]
    rig.run(60, stale(30, active=True, moving=True, target_kmh=5.0, session=1))
    assert rig.max_seen <= 1.0 + 1e-9  # the belt's minimum, nothing more
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.WAITING
    assert rig.actions().count(Action.START) == 1


def test_repeated_loss_and_return_cycles():
    limits = Limits(max_speed_kmh=5.0, hold_s=2, safe_speed_kmh=1.6, loss_pause_s=600)
    rig = started_rig(4.0, limits)
    starts = rig.actions().count(Action.START)
    for cycle in range(25):
        # The last frame stays valid for signal_timeout_s; from then on the
        # signal is lost and nothing may go up any more.
        rig.run(limits.signal_timeout_s + 0.25, None)
        assert rig.gov.phase is not Phase.FOLLOWING
        ceiling = max(rig.belt.speed, rig.gov.commanded_kmh)
        mark, log_mark = len(rig.speeds), len(rig.belt.log)
        rig.run(1 + cycle % 9, None)
        assert max(rig.speeds[mark:]) <= ceiling + 1e-9, f"sped up during loss #{cycle}"
        assert all(c.speed_kmh <= ceiling + 1e-9 for c in rig.belt.log[log_mark:] if c.action is Action.SET_SPEED)
        rig.run(2 + cycle % 5, live(4.0 if cycle % 2 else 2.5))
    rig.run(20, live(4.0))
    assert rig.gov.phase is Phase.FOLLOWING and rig.belt.speed == pytest.approx(4.0)
    assert rig.actions().count(Action.START) == starts  # the belt never had to be restarted
    assert rig.max_seen <= 5.0 + 1e-9


def test_return_from_safe_speed_climbs_back_gently():
    limits = Limits(hold_s=2, safe_speed_kmh=1.6, loss_pause_s=600)
    rig = started_rig(5.0, limits)
    rig.run(15, None)
    assert rig.belt.speed == pytest.approx(1.6)
    n = len(rig.belt.log)
    rig.run(12, live(5.0))
    ups = [c.speed_kmh for c in rig.belt.log[n:]]
    assert all(c.action is Action.SET_SPEED for c in rig.belt.log[n:])
    assert all(b - a <= 0.5 + 1e-9 for a, b in itertools.pairwise([1.6] + ups))
    assert rig.belt.speed == pytest.approx(5.0)


def test_after_loss_pause_a_character_that_never_stopped_moving_is_no_deliberate_act():
    limits = Limits(hold_s=2, safe_speed_kmh=1.6, loss_pause_s=10)
    rig = started_rig(3.0, limits)  # the character is running when the signal goes
    rig.run(40, None)
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.WAITING
    n = len(rig.belt.log)
    rig.run(30, live(3.0, moving=True))  # signal back, still running (a flight path, auto-run)
    assert rig.gov.phase is Phase.WAITING and len(rig.belt.log) == n
    rig.run(2, live(3.0, moving=False))
    rig.run(25, live(3.0, moving=True))  # stopped and started: a key was pressed
    assert rig.belt.speed == pytest.approx(3.0)


def test_resume_requires_movement_can_be_switched_off():
    limits = Limits(hold_s=2, safe_speed_kmh=0.0, resume_requires_movement=False)
    rig = started_rig(3.0, limits)
    rig.run(10, None)
    assert rig.belt.speed == 0
    rig.run(25, live(3.0, moving=False))
    assert rig.belt.speed == pytest.approx(3.0)


# ---- starting only on a deliberate act --------------------------------------------------


def test_first_reading_already_moving_is_not_a_movement_start():
    """Companion launched mid-session while the character is on a flight
    path or auto-running: nobody pressed a key, the belt must wait."""
    rig = Rig()
    rig.run(30, live(3.0, moving=True))
    assert rig.belt.log == [] and rig.gov.phase is Phase.WAITING
    rig.run(2, live(3.0, moving=False))  # lands / stops
    assert rig.belt.log == []
    rig.run(25, live(3.0, moving=True))  # and sets off again
    assert rig.actions()[0] is Action.START
    assert rig.belt.speed == pytest.approx(3.0)


def test_session_that_began_while_the_signal_was_lost_is_not_an_explicit_start():
    """No session -> the strip is covered for a while -> it is uncovered and
    shows a running session. We did not watch that session begin."""
    rig = Rig()
    rig.run(5, IDLE)
    rig.run(120, stale(60, active=False))
    rig.run(30, live(3.0, moving=False))
    assert rig.belt.log == [] and rig.gov.phase is Phase.WAITING
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_brief_dropout_between_idle_and_start_still_counts_as_watched():
    """A dropout shorter than the signal timeout is not a loss."""
    rig = Rig()
    rig.run(5, IDLE)
    rig.run(2, Reading(age_s=2.5, active=False))
    rig.run(25, live(3.0, moving=False))
    assert rig.actions()[0] is Action.START


def test_treadmill_connecting_mid_session_waits_for_movement():
    rig = Rig()
    rig.belt.connected = False
    rig.run(5, IDLE)
    rig.run(30, live(3.0, moving=False))  # the session starts while the pad is off
    assert rig.gov.phase is Phase.NO_TREADMILL and rig.belt.log == []
    rig.belt.connected = True
    rig.run(30, live(3.0, moving=False))
    assert rig.belt.log == [] and rig.gov.phase is Phase.WAITING
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_dashboard_session_started_before_the_treadmill_connected_does_not_start_it_on_connect():
    """Start pressed on the dashboard while the pad was off; the pad is
    switched on (and auto-connects) minutes later. That is not the moment
    the user asked for a moving belt: wait for Resume."""
    rig = Rig()
    rig.belt.connected = False
    rig.gov.resume()  # what the dashboard's start button does
    rig.run(300, live(3.0, session=1001))
    rig.belt.connected = True
    rig.run(60, live(3.0, session=1001))
    assert rig.belt.log == [] and rig.gov.phase is Phase.WAITING
    rig.gov.resume()
    rig.run(25, live(3.0, session=1001))
    assert rig.belt.speed == pytest.approx(3.0)


def test_external_start_with_no_session_stays_hands_off():
    rig = Rig()
    rig.run(5, IDLE)
    rig.belt.apply([Command(Action.START)])  # the user uses the remote
    rig.belt.log.clear()
    rig.gov.notify_external_start(rig.now)
    rig.belt.target = 2.0
    rig.run(60, IDLE)
    assert rig.belt.log == [] and rig.gov.phase is Phase.IDLE
    assert rig.belt.speed == pytest.approx(2.0)  # their belt, their speed
    assert rig.gov.shutdown(rig.now) == []  # and it is not ours to stop on exit


def test_external_stop_with_no_session_is_not_remembered():
    rig = Rig()
    rig.run(5, IDLE)
    rig.gov.notify_external_stop()
    assert not rig.gov.user_stopped
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_dashboard_session_started_over_a_running_game_session():
    """resume() (what the dashboard's start button calls) followed by a new
    session id: an explicit start, even though no "no session" reading came
    in between and the game character keeps moving."""
    rig = started_rig(4.0)
    rig.gov.resume()
    rig.run(20, live(2.0, session=1001))  # the manual source: always "moving", own session ids
    assert rig.gov.phase is Phase.FOLLOWING and rig.belt.speed == pytest.approx(2.0)
    # The same with the belt standing still (game session paused).
    rig = started_rig(4.0)
    rig.run(5, live(4.0, paused=True))
    assert rig.belt.speed == 0
    rig.gov.resume()
    rig.run(25, live(2.0, session=1001))
    assert rig.belt.speed == pytest.approx(2.0)


def test_session_id_changing_under_a_running_belt_keeps_it_under_control():
    """The id changes without a "no session" reading in between (a missed
    frame, a hand-over between sources). The belt is running: it must keep
    following - slow down, stop - not be parked at its old speed."""
    rig = started_rig(4.0)
    rig.run(10, live(1.5, session=2))  # the character keeps moving throughout
    assert rig.gov.phase is Phase.FOLLOWING and rig.belt.speed == pytest.approx(1.5)
    rig.run(5, live(0.0, session=3, moving=False))
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.STANDING
    assert Action.START not in rig.actions()[1:]


def test_session_id_changing_under_a_stopped_belt_waits_for_an_act():
    rig = started_rig(3.0)
    rig.run(5, live(3.0, paused=True))
    rig.run(1, live(3.0, paused=True, session=2))  # still paused, new id
    rig.gov.needs_act = True  # (e.g. the pad reconnected meanwhile)
    n = len(rig.belt.log)
    rig.run(20, live(3.0, session=3, moving=True))  # unpaused under yet another id, character already moving
    assert len(rig.belt.log) == n and rig.gov.phase is Phase.WAITING


def test_session_id_reuse_after_a_real_end_is_a_new_session():
    rig = started_rig(3.0)
    rig.belt.running = False
    rig.gov.notify_external_stop()
    rig.run(5, live(3.0, session=1))
    assert rig.gov.phase is Phase.USER_STOPPED
    rig.run(3, IDLE)
    rig.run(25, live(2.0, session=1))  # the counter wrapped: same id, new session
    assert rig.belt.speed == pytest.approx(2.0)


# ---- a stop at the machine is final ------------------------------------------------------


def machine_stopped_rig() -> Rig:
    rig = started_rig(3.0)
    rig.belt.running = False
    rig.gov.notify_external_stop()
    rig.run(10, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED and rig.belt.speed == 0
    return rig


def test_machine_stop_survives_everything_that_is_not_an_explicit_resume():
    rig = machine_stopped_rig()
    n = len(rig.belt.log)
    rig.run(20, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))  # movement is not enough here
    rig.run(20, live(0.0, moving=False))
    rig.run(20, live(40.0, moving=True))
    rig.run(400, None)  # a long signal loss
    rig.run(20, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))
    rig.belt.connected = False  # the pad drops off and comes back
    rig.run(5, live(3.0))
    rig.belt.connected = True
    rig.run(20, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))
    assert len(rig.belt.log) == n, [c.action for c in rig.belt.log[n:]]
    assert rig.belt.speed == 0 and rig.gov.user_stopped


def test_machine_stop_survives_a_session_id_change_we_did_not_watch():
    """E.g. the dashboard's STOP during a dashboard session while a game
    session is also active: the hand-over back to the game shows up as a new
    session id. That is not the user resuming."""
    rig = machine_stopped_rig()
    n = len(rig.belt.log)
    rig.run(20, live(3.0, session=2, moving=False))
    rig.run(20, live(3.0, session=2, moving=True))
    assert len(rig.belt.log) == n and rig.belt.speed == 0
    assert rig.gov.phase is Phase.USER_STOPPED
    # Pause -> resume still works as the way out.
    rig.run(2, live(3.0, session=2, paused=True))
    rig.run(2, live(3.0, session=2, moving=False))
    rig.run(25, live(3.0, session=2, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def afk_pause(target: float = 3.0, session: int = 1) -> Reading:
    """What the addon sends when it pauses the session because the player went AFK."""
    return Reading(age_s=0.0, active=True, paused=True, moving=False, target_kmh=target, session=session, afk=True)


def test_automatic_afk_pause_does_not_acknowledge_a_machine_stop():
    """The belt was stopped at the pad; the player walks away, the game marks
    them AFK and the addon pauses - then un-pauses when a key is touched.
    Nobody asked for the belt back: it must stay stopped."""
    rig = machine_stopped_rig()
    n = len(rig.belt.log)
    rig.run(120, afk_pause())
    assert rig.gov.phase is Phase.PAUSED
    rig.run(30, live(3.0, moving=False))  # back at the keyboard
    rig.run(30, live(3.0, moving=True))
    assert len(rig.belt.log) == n and rig.belt.speed == 0
    assert rig.gov.phase is Phase.USER_STOPPED
    # A pause the player presses themselves is the acknowledgement.
    rig.run(2, live(3.0, paused=True))
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_automatic_afk_pause_does_not_satisfy_the_wait_for_a_deliberate_act():
    limits = Limits(hold_s=2, safe_speed_kmh=0.0)
    rig = started_rig(3.0, limits)
    rig.run(1, live(3.0, moving=False))  # the character stops ...
    rig.run(10, None)  # ... the signal goes: the belt is paused after the hold
    assert rig.gov.needs_act and rig.belt.speed == 0
    n = len(rig.belt.log)
    rig.run(60, afk_pause())
    rig.run(30, live(3.0, moving=False))
    assert len(rig.belt.log) == n and rig.gov.phase is Phase.WAITING
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_afk_pause_of_a_running_belt_pauses_and_resumes_as_the_addon_documents():
    rig = started_rig(3.0)
    rig.run(60, afk_pause())
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.PAUSED and rig.actions()[-1] is Action.PAUSE
    rig.run(25, live(3.0, moving=False))
    assert rig.belt.speed == pytest.approx(3.0)


def test_stop_during_the_countdown_is_respected():
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(1, live(3.0))
    assert rig.actions() == [Action.START]
    rig.belt.running = False  # stop pressed at the pad before the belt moved
    rig.gov.notify_external_stop()
    rig.run(60, live(3.0))
    assert rig.actions() == [Action.START] and rig.belt.speed == 0
    assert rig.gov.phase is Phase.USER_STOPPED


def test_external_stop_then_start_resumes_following_without_our_start():
    rig = started_rig(3.0)
    rig.belt.running = False
    rig.gov.notify_external_stop()
    rig.run(10, live(3.0))
    rig.belt.apply([Command(Action.START)])
    rig.belt.log.pop()
    rig.gov.notify_external_start(rig.now)
    n = len(rig.belt.log)
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)
    assert Action.START not in [c.action for c in rig.belt.log[n:]]


def test_latched_stop_never_restarts_under_random_input():
    """Property: from a stop at the machine until one of the explicit acts
    (a pause the player asked for, "no session", resume(), start at the
    machine) the governor sends no START - whatever else happens."""
    import random

    rng = random.Random(1234)
    for _ in range(60):
        rig = started_rig(3.0)
        rig.belt.running = False
        rig.gov.notify_external_stop()
        n = len(rig.belt.log)
        for _ in range(40):
            roll = rng.random()
            if roll < 0.15:
                rig.run(rng.uniform(0.25, 12), None)
            elif roll < 0.25:
                rig.run(rng.uniform(0.25, 20), afk_pause(session=rng.randrange(1, 4)))
            elif roll < 0.32:
                rig.belt.connected = not rig.belt.connected
            elif roll < 0.36:
                rig.gov.limits.max_speed_kmh = rng.uniform(0.5, 8)
            else:
                rig.run(
                    rng.uniform(0.25, 6),
                    live(rng.uniform(0, 40), moving=rng.random() < 0.6, session=rng.randrange(1, 4)),
                )
        rig.belt.connected = True
        rig.run(10, live(3.0, moving=False))
        rig.run(10, live(3.0, moving=True))
        actions = [c.action for c in rig.belt.log[n:]]
        assert Action.START not in actions and Action.SET_SPEED not in actions, actions
        assert rig.belt.speed == 0


# ---- making sure a stop really happened ----------------------------------------------------


def test_lost_pause_command_is_repeated_until_the_belt_stops():
    """The PAUSE never reached the pad (Bluetooth hiccup). The dashboard says
    "paused" - the belt must not be left running under it."""
    rig = started_rig(3.0)
    rig.belt.deaf = True
    rig.run(4.75, live(3.0, paused=True))
    assert rig.actions().count(Action.PAUSE) == 1 and rig.belt.speed == pytest.approx(3.0)
    rig.run(4, live(3.0, paused=True))
    assert rig.actions().count(Action.PAUSE) == 2  # asked again after 5 s, not every tick
    rig.belt.deaf = False
    rig.run(8, live(3.0, paused=True))
    assert rig.belt.speed == 0
    pauses = rig.actions().count(Action.PAUSE)
    rig.run(60, live(3.0, paused=True))
    assert rig.actions().count(Action.PAUSE) == pauses  # and not again once it has stopped
    assert rig.gov.phase is Phase.PAUSED


def test_lost_stop_command_at_session_end_is_repeated():
    rig = started_rig(3.0)
    rig.belt.deaf = True
    rig.run(7, IDLE)
    assert rig.gov.phase is Phase.IDLE and rig.belt.speed == pytest.approx(3.0)
    assert rig.actions().count(Action.STOP) == 2
    rig.belt.deaf = False
    rig.run(8, IDLE)
    assert rig.belt.speed == 0
    stops = rig.actions().count(Action.STOP)
    rig.run(120, IDLE)
    assert rig.actions().count(Action.STOP) == stops
    assert all(c.action is Action.STOP for c in rig.belt.log[-stops:])  # a stop stays a stop


def test_lost_pause_on_long_signal_loss_is_repeated():
    limits = Limits(hold_s=2, safe_speed_kmh=0.0)
    rig = started_rig(3.0, limits)
    rig.belt.deaf = True
    rig.run(12, None)
    assert rig.gov.phase is Phase.WAITING and rig.belt.speed == pytest.approx(3.0)
    assert rig.actions().count(Action.PAUSE) >= 2
    rig.belt.deaf = False
    rig.run(8, None)
    assert rig.belt.speed == 0


def test_a_normal_stop_is_not_repeated():
    for end in (IDLE, live(3.0, paused=True), live(0.0, moving=False)):
        rig = started_rig(3.0)
        n = len(rig.belt.log)
        rig.run(60, end)
        assert len(rig.belt.log) == n + 1 and rig.belt.log[-1].action in HALT


def test_resume_supersedes_a_pending_stop_check():
    rig = started_rig(3.0)
    rig.belt.deaf = True
    rig.run(2, live(3.0, paused=True))  # the pause is lost
    rig.belt.deaf = False
    rig.run(20, live(2.0))  # resumed before we would have asked again
    assert rig.belt.speed == pytest.approx(2.0) and rig.gov.phase is Phase.FOLLOWING
    assert rig.actions().count(Action.PAUSE) == 1  # no late pause lands on the resumed belt


def test_start_at_the_machine_cancels_a_pending_stop_check():
    rig = started_rig(3.0)
    rig.belt.deaf = True
    rig.run(2, IDLE)  # STOP lost; the user restarts with the remote anyway
    rig.gov.notify_external_start(rig.now)
    rig.run(30, IDLE)
    assert rig.actions().count(Action.STOP) == 1  # we do not fight the remote
    assert rig.belt.speed == pytest.approx(3.0)


def test_expect_halt_repeats_an_outside_stop_that_did_not_take():
    """The dashboard's STOP button sends its own command; if that fails the
    governor keeps asking while the belt still moves."""
    rig = started_rig(3.0)
    rig.gov.notify_external_stop()  # what the app does on STOP ...
    rig.gov.user_stopped = True
    rig.gov.expect_halt(rig.now)  # ... followed by a stop command that got lost
    n = len(rig.belt.log)
    rig.run(4.75, live(3.0))
    assert len(rig.belt.log) == n
    rig.run(1, live(3.0))
    assert [c.action for c in rig.belt.log[n:]] == [Action.STOP]
    rig.run(20, live(3.0))
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.USER_STOPPED
    assert [c.action for c in rig.belt.log[n:]] == [Action.STOP]


def test_session_ending_stops_a_belt_that_is_turning_although_we_thought_it_off():
    """Stopped at the pad, restarted with the remote without the pad telling
    us: when the session ends the belt we drove must not be left running."""
    rig = machine_stopped_rig()
    rig.belt.running, rig.belt.target = True, 2.0
    rig.belt.count_left = 0.0
    rig.run(10, live(3.0))
    assert rig.belt.speed == pytest.approx(2.0) and rig.gov.phase is Phase.USER_STOPPED
    rig.run(5, IDLE)
    assert rig.actions()[-1] is Action.STOP and rig.belt.speed == 0


def test_belt_reporting_no_movement_is_paused_before_it_is_called_stopped():
    """Treadmill data dries up while the belt runs on. "It reports zero, so
    it has stopped" would leave it running with nobody in control."""
    rig = started_rig(3.0)
    rig.belt.blind = True
    rig.run(10, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED
    assert rig.actions()[-1] is Action.PAUSE
    assert rig.belt.speed == 0  # it really is stopped now
    n = len(rig.belt.log)
    rig.belt.blind = False
    rig.run(30, live(3.0))
    assert len(rig.belt.log) == n  # and stays so until an explicit resume


def test_start_that_never_moves_is_cancelled_when_we_give_up():
    """If we stop waiting for a start, the start must not come through later
    with nobody following the belt."""
    rig = Rig(countdown=20.0)  # a pad that takes far longer than the grace period
    rig.run(1, IDLE)
    rig.run(60, live(3.0))
    assert rig.gov.phase is Phase.USER_STOPPED
    assert rig.actions() == [Action.START, Action.PAUSE]
    assert rig.max_seen == 0.0  # the belt never came to life behind our back


# ---- the treadmill link -------------------------------------------------------------------


def test_disconnect_sends_nothing_and_reconnect_adopts_a_running_belt():
    rig = started_rig(3.0)
    n = len(rig.belt.log)
    rig.belt.connected = False
    rig.run(10, live(4.0))
    assert rig.gov.phase is Phase.NO_TREADMILL and len(rig.belt.log) == n
    rig.belt.connected = True  # the pad kept running meanwhile
    rig.run(10, live(4.0))
    assert Action.START not in [c.action for c in rig.belt.log[n:]]
    assert rig.belt.speed == pytest.approx(4.0) and rig.gov.phase is Phase.FOLLOWING


def reconnect(rig: Rig, reading: Reading | None, *, data_after: float = 1.0) -> None:
    """The link comes back. As with the real driver, the belt's speed is
    unknown (reported as zero) until the first data packet arrives."""
    rig.belt.connected = True
    rig.belt.blind = True
    rig.run(data_after, reading)
    rig.belt.blind = False


def test_session_ending_while_disconnected_stops_the_belt_on_reconnect():
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(5, IDLE)  # the session ends while the link is down; the pad runs on
    assert rig.belt.speed == pytest.approx(3.0)
    reconnect(rig, IDLE)
    assert Action.STOP not in rig.actions()  # nothing is known about the belt yet
    rig.run(10, IDLE)
    assert rig.actions()[-1] is Action.STOP and rig.belt.speed == 0
    assert rig.gov.phase is Phase.IDLE and not rig.gov.driving
    assert rig.actions().count(Action.STOP) == 1


def test_pause_during_a_link_loss_is_applied_on_reconnect():
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(5, live(3.0, paused=True))
    reconnect(rig, live(3.0, paused=True))
    rig.run(10, live(3.0, paused=True))
    assert rig.actions()[-1] is Action.PAUSE and rig.belt.speed == 0
    assert rig.gov.phase is Phase.PAUSED
    rig.run(25, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


def test_standing_still_during_a_link_loss_is_applied_on_reconnect():
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(5, live(0.0, moving=False))
    reconnect(rig, live(0.0, moving=False))
    rig.run(10, live(0.0, moving=False))
    assert rig.actions()[-1] is Action.PAUSE and rig.belt.speed == 0
    assert rig.gov.phase is Phase.STANDING


def test_signal_lost_during_a_link_loss_degrades_as_usual_on_reconnect():
    """Game and pad both out of reach for a while (the PC slept). The belt
    that ran on must still be slowed and paused by the signal-loss rules."""
    limits = Limits(hold_s=2, safe_speed_kmh=1.6, loss_pause_s=20)
    rig = started_rig(4.0, limits)
    rig.belt.connected = False
    rig.run(4, None)
    reconnect(rig, None)
    assert rig.belt.speed == pytest.approx(4.0)
    rig.run(8, None)
    assert rig.gov.phase is Phase.SAFE and rig.belt.speed == pytest.approx(1.6)
    rig.run(30, None)
    assert rig.belt.speed == 0 and rig.gov.phase is Phase.WAITING
    assert Action.START not in rig.actions()[1:]


def test_reconnect_with_a_running_belt_and_a_live_session_just_follows():
    rig = started_rig(3.0)
    n = len(rig.belt.log)
    rig.belt.connected = False
    rig.run(5, live(4.0, moving=False))
    reconnect(rig, live(4.0, moving=False))
    rig.run(10, live(4.0, moving=False))
    assert [c.action for c in rig.belt.log[n:]].count(Action.START) == 0
    assert rig.belt.speed == pytest.approx(4.0) and rig.gov.phase is Phase.FOLLOWING


def test_reconnect_with_a_stopped_belt_sends_nothing():
    for reading in (IDLE, live(3.0, paused=True), live(0.0, moving=False), live(3.0, moving=False), None):
        rig = started_rig(3.0)
        rig.run(1, live(3.0, moving=False))
        rig.belt.connected = False
        rig.belt.running = False
        rig.belt.speed = 0.0
        rig.run(5, reading)
        n = len(rig.belt.log)
        reconnect(rig, reading)
        rig.run(20, reading)
        assert len(rig.belt.log) == n, reading


def test_stop_pressed_during_a_link_loss_is_applied_on_reconnect():
    """The dashboard's STOP while the pad is out of reach cannot be sent. If
    the belt we were driving turns out to have run on, it is stopped the
    moment it can be - not adopted, not left running under "stopped"."""
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(2, live(3.0))
    rig.gov.notify_external_stop()  # what the app does when STOP is pressed
    rig.gov.user_stopped = True
    rig.gov.expect_halt(rig.now)
    rig.run(20, live(3.0))
    reconnect(rig, live(3.0))
    rig.run(10, live(3.0))
    assert rig.actions()[-1] is Action.STOP and rig.belt.speed == 0
    assert rig.gov.phase is Phase.USER_STOPPED and not rig.gov.belt_on
    n = len(rig.belt.log)
    rig.run(30, live(3.0, moving=False))
    rig.run(30, live(3.0, moving=True))
    assert len(rig.belt.log) == n  # and it stays latched


def test_belt_that_was_off_when_the_link_dropped_is_not_claimed_on_reconnect():
    """Paused by us, link drops, someone starts the belt with the remote
    during the outage: with the session over, that belt is theirs."""
    rig = started_rig(3.0)
    rig.run(5, live(3.0, paused=True))
    rig.belt.connected = False
    rig.belt.apply([Command(Action.START)])
    rig.belt.log.pop()
    rig.belt.target = 2.0
    rig.run(10, IDLE)
    n = len(rig.belt.log)
    reconnect(rig, IDLE)
    rig.run(20, IDLE)
    assert len(rig.belt.log) == n and rig.belt.speed == pytest.approx(2.0)


def test_shutdown_only_stops_a_belt_that_is_ours_and_running():
    for prepare in (
        lambda rig: rig.run(5, live(3.0, paused=True)),
        lambda rig: rig.run(5, live(0.0, moving=False)),
        lambda rig: rig.run(5, IDLE),
        lambda rig: (setattr(rig.belt, "running", False), rig.gov.notify_external_stop()),
    ):
        rig = started_rig(3.0)
        prepare(rig)
        assert rig.gov.shutdown(rig.now) == []
    rig = started_rig(3.0)
    rig.run(10, None)  # signal lost, belt still running under our control
    assert [c.action for c in rig.gov.shutdown(rig.now)] == [Action.STOP]
    assert rig.gov.phase is Phase.IDLE and not rig.gov.driving
    assert rig.gov.shutdown(rig.now) == []  # idempotent


# ---- soak -----------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_soak_random_events_never_break_the_invariants(seed: int):
    """Random sessions, signal losses, button presses at the pad, link drops,
    lost commands and settings changes. Invariants, checked after every event:

    * no speed command above the cap in force (or outside the pad's range);
    * the belt is never started while the cap is below the pad's minimum;
    * the belt never goes faster than the cap, unless it already was faster
      when the cap was lowered or someone started it at the pad itself
      (checked whenever the belt can hear us: with the link down or a command
      lost, the governor has done its part by commanding the right thing);
    * once the signal counts as lost, nothing is commanded upwards.
    """
    import random

    rng = random.Random(seed)
    grid = rng.choice([(1.0, 6.0, 0.1), (0.9656064, 6.437376, 0.1609344), (0.5, 8.0, 0.5), (1.0, 6.0, 0.0)])
    limits = Limits(
        max_speed_kmh=rng.uniform(1.5, 6.0),
        hold_s=rng.uniform(0, 6),
        safe_speed_kmh=rng.choice([0.0, 1.6, 3.0]),
        loss_pause_s=rng.uniform(5, 40),
    )
    rig = Rig(limits, min_kmh=grid[0], max_kmh=grid[1], step=grid[2])
    rig.run(1, IDLE)
    state = {"external": False, "previous_cap": limits.max_speed_kmh, "out_of_reach": False}

    def check(before_log: int, before_speed: float) -> None:
        new = rig.belt.log[before_log:]
        for c in new:
            if c.action is Action.SET_SPEED:
                assert c.speed_kmh is not None
                assert c.speed_kmh <= limits.max_speed_kmh + 1e-9, "commanded above the cap"
                assert grid[0] - 1e-9 <= c.speed_kmh <= grid[1] + 1e-9, "commanded outside the pad's range"
                state["external"] = False  # from here on the speed is ours
            elif c.action is Action.START:
                assert limits.max_speed_kmh >= grid[0] - 1e-9, "started a belt that cannot run within the cap"
        if rig.belt.deaf or not rig.belt.connected:
            state["out_of_reach"] = True  # whatever the belt does now, we cannot change it
        elif any(c.action is not Action.START for c in new):
            state["out_of_reach"] = False  # it has heard a speed or a stop from us again
        if state["out_of_reach"]:
            return
        allowed = max(limits.max_speed_kmh, state["previous_cap"], before_speed)
        if state["external"]:
            allowed = max(allowed, grid[0])  # started at the pad: it runs at the pad's minimum
        assert rig.belt.speed <= allowed + 1e-9, "the belt went above the cap"

    for _ in range(500):
        before_log, before_speed = len(rig.belt.log), rig.belt.speed
        state["previous_cap"] = limits.max_speed_kmh
        roll = rng.random()
        if roll < 0.08:
            rig.run(limits.signal_timeout_s + 0.25, None)
            check(before_log, before_speed)
            before_log, before_speed = len(rig.belt.log), rig.belt.speed
            if rig.belt.connected and rig.gov.phase is not Phase.FOLLOWING:
                # (a belt that was started just before still comes up to its minimum speed)
                ceiling = max(rig.belt.speed, rig.gov.commanded_kmh, grid[0] if rig.belt.running else 0.0)
                mark = len(rig.speeds)
                rig.run(rng.uniform(0.25, 15), None)
                assert max(rig.speeds[mark:], default=0) <= ceiling + 1e-9, "sped up on signal loss"
                assert all(
                    c.speed_kmh <= ceiling + 1e-9 for c in rig.belt.log[before_log:] if c.action is Action.SET_SPEED
                ), "commanded upwards on signal loss"
                assert Action.START not in [c.action for c in rig.belt.log[before_log:]], "started on signal loss"
        elif roll < 0.16:
            rig.run(rng.uniform(0.25, 3), IDLE)
        elif roll < 0.20:
            rig.belt.running = False
            rig.gov.notify_external_stop()
        elif roll < 0.23:
            if not rig.belt.running:
                rig.belt.apply([Command(Action.START)])
                rig.belt.log.pop()
                if rig.belt.running:  # (a deaf belt hears its own buttons)
                    state["external"] = True
                    rig.gov.notify_external_start(rig.now)
        elif roll < 0.27:
            rig.belt.connected = not rig.belt.connected
        elif roll < 0.31:
            limits.max_speed_kmh = rng.uniform(0.5, 7.0)
        elif roll < 0.33:
            rig.gov.resume()
        elif roll < 0.36:
            rig.belt.deaf = not rig.belt.deaf
        elif roll < 0.40:
            rig.run(rng.uniform(0.25, 20), afk_pause(session=rng.randrange(1, 4)))
        else:
            rig.run(
                rng.uniform(0.25, 6),
                live(
                    rng.choice([0.0, rng.uniform(0, 8), 40.95]),
                    moving=rng.random() < 0.7,
                    paused=rng.random() < 0.1,
                    session=rng.randrange(1, 4),
                ),
            )
        check(before_log, before_speed)
