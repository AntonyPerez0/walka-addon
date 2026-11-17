"""Regression tests for the belt-safety review of the governor.

Each section pins one way the belt could do something the walker did not
ask for: surge back after being slowed with the remote, run on after a stop
that got lost, start on a stale picture of the game. The toy belt and rig
are the ones from ``test_governor``.
"""

from __future__ import annotations

import dataclasses
import itertools
import random

import pytest

from test_governor import HALT, IDLE, Rig, ToyBelt, afk_pause, live, reconnect, stale, started_rig
from walka.governor import (
    ABANDONED_AFTER_S,
    HALT_GIVE_UP_S,
    RESUME_VALID_S,
    Action,
    Belt,
    Limits,
    Phase,
    Reading,
)

UP = 0.2  # one increment: the default 0.2 km/h per second, one command a second


def speeds_since(rig: Rig, mark: int) -> list[float]:
    return [c.speed_kmh for c in rig.belt.log[mark:] if c.action is Action.SET_SPEED]


def remote(rig: Rig, kmh: float) -> None:
    """The walker's + and - on the pad's remote: nothing announces it, the
    belt just goes to another speed."""
    rig.belt.target = kmh


def one_packet(rig: Rig, reading: Reading, speed: float) -> list:
    """One tick in which the treadmill's data packet carries ``speed`` -
    whatever the belt is really doing."""
    real = rig.belt.speed
    rig.now += 0.25
    rig.belt.clock = rig.now
    rig.belt.speed = speed
    commands = rig.gov.step(rig.now, reading, rig.belt.view())
    rig.belt.speed = real
    rig.belt.apply(commands)
    rig.belt.advance(0.25)
    return commands


# ---- [0a] speed-ups are measured from what the belt reports --------------------------------


def test_lost_speed_commands_are_not_built_on():
    """Speed writes that silently get lost (or time out) used to push the
    commanded speed up a step a second with nothing applied; the first write
    to get through was then a jump of several steps."""
    rig = Rig()
    rig.run(1, IDLE)
    rig.run(8, live(4.0))  # on its way up
    rig.belt.deaf = True
    rig.run(3, live(4.0))  # the belt arrives at the last speed it heard
    stuck_at, mark = rig.belt.speed, len(rig.belt.log)
    rig.run(30, live(4.0))
    assert rig.belt.speed == pytest.approx(stuck_at)
    lost = speeds_since(rig, mark)
    assert lost, "a command that evidently did not take is asked again"
    assert max(lost) <= stuck_at + UP + 1e-9, "the commanded speed ran away from the belt"
    assert len(lost) <= 16  # ... but not every second: only once the belt has settled
    rig.belt.deaf = False
    mark = len(rig.belt.log)
    rig.run(40, live(4.0))
    ups = speeds_since(rig, mark)
    assert ups[0] <= stuck_at + UP + 1e-9, "the first command to get through was a jump"
    assert all(b - a <= UP + 1e-9 for a, b in itertools.pairwise(ups))
    assert rig.belt.speed == pytest.approx(4.0)


@pytest.mark.parametrize("seed", range(6))
def test_no_speed_up_is_more_than_one_increment_above_the_reported_speed(seed: int):
    """Property: whatever is lost on the way - commands, for seconds at a
    time - no speed command is more than one increment above what the belt
    reported when it was sent."""
    rng = random.Random(seed)
    rig = Rig(Limits(max_speed_kmh=6.0))
    rig.run(1, IDLE)
    target = 3.0
    for _ in range(400):
        if rng.random() < 0.05:
            rig.belt.deaf = not rig.belt.deaf
        if rng.random() < 0.05:
            target = rng.choice([1.0, 2.2, 3.0, 4.4, 6.0])
        before, mark = max(rig.belt.speed, rig.belt.min_kmh), len(rig.belt.log)
        rig.run(0.25, live(target))
        for speed in speeds_since(rig, mark):
            assert speed <= before + UP + 1e-9, f"{speed:.2f} asked for with the belt at {before:.2f}"


def test_a_belt_slowed_at_the_pad_does_not_surge_back_when_the_target_moves_a_notch():
    """The review's headline case: 4.5 km/h, the walker holds '-' on the
    remote down to the minimum, the game target moves from 4.5 to 4.3 - and
    the belt was sent to 4.3 in one go."""
    rig = started_rig(4.5)
    remote(rig, 1.0)
    rig.run(20, live(4.5))
    assert rig.belt.speed == pytest.approx(1.0)
    mark, start = len(rig.belt.log), len(rig.speeds)
    rig.run(30, live(4.3))
    assert max(rig.speeds[start:]) <= 1.0 + 1e-9, "the belt sped up again by itself"
    assert all(s <= 1.0 + 1e-9 for s in speeds_since(rig, mark))


def test_a_lost_signal_never_commands_more_than_the_belt_is_doing():
    """... and the same belt, with the game signal lost right after: the
    slow-down to the safe speed used to start from the 4.5 commanded, which
    took the belt from 1 km/h up to over 3."""
    limits = Limits(hold_s=5, safe_speed_kmh=1.3, loss_pause_s=60)
    rig = started_rig(4.5, limits)
    remote(rig, 1.0)
    rig.run(1, live(4.5))  # still coasting down when the picture goes
    mark, start = len(rig.belt.log), len(rig.speeds)
    before = rig.belt.speed
    rig.run(40, None)
    assert max(rig.speeds[start:]) <= before + 1e-9
    assert rig.belt.speed == pytest.approx(1.0)
    for a, b in itertools.pairwise([before] + speeds_since(rig, mark)):
        assert b <= a + 1e-9, "a speed-up was commanded on a lost signal"


def test_one_garbled_low_packet_does_not_yank_the_belt_down():
    rig = started_rig(4.0)
    mark = len(rig.belt.log)
    assert one_packet(rig, live(3.0), 0.5) == []  # the game asks for less in that very tick
    rig.run(10, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)
    for a, b in itertools.pairwise([4.0] + speeds_since(rig, mark)):
        assert 0 < a - b <= 0.4 + 1e-9  # the ordinary gentle way down
    assert rig.gov.ceiling_kmh is None


def test_one_garbled_high_packet_does_not_allow_a_bigger_step_up():
    rig = started_rig(2.0)
    mark = len(rig.belt.log)
    rig.run(0.75, live(5.0))
    one_packet(rig, live(5.0), 12.0)
    rig.run(1, live(5.0))
    for a, b in itertools.pairwise([2.0] + speeds_since(rig, mark)):
        assert b - a <= UP + 1e-9


def test_a_failed_speed_command_goes_back_to_the_speed_before_it():
    """What the app does when the treadmill rejects a speed write."""
    rig = started_rig(2.0)
    rig.run(0.25, live(3.0))
    assert rig.belt.log[-1].speed_kmh == pytest.approx(2.2)
    rig.belt.target = 2.0  # ... which the pad refused
    rig.gov.speed_command_failed()
    assert rig.gov.commanded_kmh == pytest.approx(2.0)
    # A single zero packet right after it is a glitch, not a stalled belt
    # (it used to be measured against the time the belt was started).
    assert one_packet(rig, live(3.0), 0.0) == []
    assert rig.gov.phase is Phase.FOLLOWING and not rig.gov.user_stopped
    rig.run(10, live(3.0))
    assert rig.belt.speed == pytest.approx(3.0)


# ---- [0b] a speed chosen at the pad is a ceiling ----------------------------------------------


def ceiling_rig(at: float = 2.0, target: float = 4.5, limits: Limits | None = None) -> Rig:
    rig = started_rig(target, limits)
    remote(rig, at)
    rig.run((target - at) / 0.5 + 4, live(target))  # the belt gets there, then two seconds of it
    assert rig.gov.ceiling_kmh == pytest.approx(at) and rig.belt.speed == pytest.approx(at)
    return rig


def test_speed_lowered_at_the_pad_becomes_a_ceiling():
    rig = started_rig(4.5)
    remote(rig, 2.0)
    mark, start = len(rig.belt.log), len(rig.speeds)
    rig.run(12, live(4.5))
    assert rig.gov.ceiling_kmh == pytest.approx(2.0) and "ceiling" in rig.gov.notes
    assert rig.gov.phase is Phase.FOLLOWING and rig.gov.wanted_kmh == pytest.approx(2.0)
    assert all(s <= 2.0 + 1e-9 for s in speeds_since(rig, mark))
    assert rig.speeds[start:] == sorted(rig.speeds[start:], reverse=True), "the belt was sped up on its way down"
    # The belt still follows the game below the ceiling ...
    rig.run(10, live(1.5))
    assert rig.belt.speed == pytest.approx(1.5)
    # ... and comes back up to it, never above - however long the game asks for more.
    start = len(rig.speeds)
    rig.run(120, live(6.0))
    assert rig.belt.speed == pytest.approx(2.0) and max(rig.speeds[start:]) <= 2.0 + 1e-9
    assert rig.gov.ceiling_kmh == pytest.approx(2.0)


def test_one_increment_down_at_the_pad_is_not_a_ceiling():
    """One increment below the command is what a lost write looks like. It
    is asked for again, not honoured."""
    rig = started_rig(3.0)
    remote(rig, 2.8)
    rig.run(10, live(3.0))
    assert rig.gov.ceiling_kmh is None and rig.belt.speed == pytest.approx(3.0)


@pytest.mark.parametrize("how", ["pause_resume", "dashboard_resume", "new_session", "manual_session"])
def test_ceiling_is_lifted_by_an_explicit_act(how: str):
    rig = ceiling_rig(2.0, 4.5)
    session = 1
    if how == "pause_resume":
        rig.run(2, live(4.5, paused=True))
    elif how == "dashboard_resume":
        rig.gov.resume()
    elif how == "new_session":
        rig.run(3, IDLE)
        session = 2
    else:
        rig.gov.resume()  # what the dashboard's Start does, followed by its own session id
        session = 1001
    rig.run(60, live(4.5, session=session))
    assert rig.gov.ceiling_kmh is None
    assert rig.belt.speed == pytest.approx(4.5)


def test_ceiling_survives_what_is_not_an_explicit_act():
    rig = ceiling_rig(2.0, 4.5)
    rig.run(20, afk_pause(4.5))  # the addon's automatic pause: nobody asked for anything
    rig.run(40, live(4.5))
    assert rig.belt.speed == pytest.approx(2.0)
    rig.run(10, live(0.0, moving=False))  # standing still, belt paused, and off again
    rig.run(40, live(4.5))
    assert rig.belt.speed == pytest.approx(2.0)
    rig.run(8, None)  # a short signal loss
    rig.run(20, live(4.5))
    assert rig.belt.speed == pytest.approx(2.0) and rig.gov.ceiling_kmh == pytest.approx(2.0)
    assert rig.max_seen <= 4.5 + 1e-9


def test_raising_the_speed_at_the_pad_moves_the_ceiling_up_and_then_away():
    rig = ceiling_rig(2.0, 4.5)
    remote(rig, 3.0)
    rig.run(8, live(4.5))
    assert rig.gov.ceiling_kmh == pytest.approx(3.0) and rig.belt.speed == pytest.approx(3.0)
    rig.run(30, live(4.5))
    assert rig.belt.speed == pytest.approx(3.0)  # still held, at the new speed
    remote(rig, 4.5)  # all the way up to what the game asks for
    rig.run(10, live(4.5))
    assert rig.gov.ceiling_kmh is None
    rig.run(20, live(5.0))
    assert rig.belt.speed == pytest.approx(5.0)  # following freely again


def test_speed_raised_at_the_pad_above_the_command_is_walked_back_gently():
    """Not a ceiling, and not left alone either: the reported speed becomes
    the starting point, and the belt comes back to the target at the normal
    deceleration rate - not in one drop the next time the target twitches."""
    rig = started_rig(2.0)
    remote(rig, 4.0)
    mark = len(rig.belt.log)
    rig.run(4.5, live(2.0))  # the belt has just arrived at 4.0
    assert speeds_since(rig, mark) == []
    rig.run(15, live(2.0))
    downs = speeds_since(rig, mark)
    assert rig.gov.ceiling_kmh is None and rig.belt.speed == pytest.approx(2.0)
    assert downs[0] == pytest.approx(3.6) and downs[-1] == pytest.approx(2.0)
    assert all(0 < a - b <= 0.4 + 1e-9 for a, b in itertools.pairwise([4.0] + downs))


def test_speed_raised_at_the_pad_above_the_cap_meets_the_cap_at_once():
    rig = started_rig(2.0, Limits(max_speed_kmh=3.0))
    remote(rig, 5.0)
    mark = len(rig.belt.log)
    rig.run(25, live(2.0))
    downs = speeds_since(rig, mark)
    assert downs and max(downs) <= 3.0 + 1e-9, "a command above the cap"
    assert rig.belt.speed == pytest.approx(2.0)


class TellingBelt(ToyBelt):
    """A pad that says what speed it is set to (FTMS "target speed changed")."""

    def view(self) -> Belt:
        return dataclasses.replace(super().view(), target_kmh=self.target if self.running else None)


def test_a_pad_that_reports_its_target_gets_the_ceiling_at_once():
    rig = Rig()
    rig.belt = TellingBelt()
    rig.run(1, IDLE)
    rig.run(45, live(4.5))
    assert rig.belt.speed == pytest.approx(4.5) and rig.gov.ceiling_kmh is None  # our own commands are no ceiling
    remote(rig, 2.0)
    mark, start = len(rig.belt.log), len(rig.speeds)
    rig.run(0.5, live(4.3))
    assert rig.gov.ceiling_kmh == pytest.approx(2.0)
    rig.run(20, live(4.3))
    assert rig.belt.speed == pytest.approx(2.0)
    assert all(s <= 2.0 + 1e-9 for s in speeds_since(rig, mark))
    assert rig.speeds[start:] == sorted(rig.speeds[start:], reverse=True)


def test_a_belt_that_never_settles_far_below_the_command_is_still_honoured():
    """A noisy speed report must not park the governor: after a few seconds
    of "far slower than asked" the speed at that moment is taken."""
    rig = started_rig(4.5)
    remote(rig, 2.0)
    rig.run(5, live(4.5))
    mark = len(rig.belt.log)
    for i in range(40):  # 10 s of a report that flickers by 0.1 km/h around 2.0
        one_packet(rig, live(4.5), 2.0 + (0.1 if i % 2 else -0.1))
    assert rig.gov.ceiling_kmh is not None and rig.gov.ceiling_kmh <= 2.1 + 1e-9
    sent = speeds_since(rig, mark)
    assert sent and max(sent) <= 2.1 + 1e-9


# ---- [1] a stop that is owed survives a Bluetooth drop -----------------------------------------


@pytest.mark.parametrize("what", ["pause", "afk_pause", "stop_button", "session_end"])
def test_stop_that_was_not_obeyed_before_the_link_dropped_is_delivered_after_it(what: str):
    rig = started_rig(4.0)
    rig.belt.deaf = True  # the link is dying: nothing gets through
    if what == "stop_button":
        rig.gov.stop_pressed(rig.now)  # (the app's own stop command is lost as well)
        reading = live(4.0)
    else:
        reading = {"pause": live(4.0, paused=True), "afk_pause": afk_pause(4.0), "session_end": IDLE}[what]
    rig.run(1, reading)
    assert rig.belt.speed == pytest.approx(4.0) and rig.gov.halt_pending and rig.gov.halt_owed
    rig.belt.connected = False
    rig.run(5, reading)
    assert rig.gov.halt_owed, "the debt was forgotten when the link dropped"
    rig.belt.deaf = False
    mark = len(rig.belt.log)
    reconnect(rig, reading)
    rig.run(8, reading)
    assert [c.action for c in rig.belt.log[mark:]] == [Action.STOP]
    assert rig.belt.speed == 0 and not rig.gov.halt_pending
    rig.run(60, reading)
    assert len(rig.belt.log) == mark + 1  # and nothing more once it has stopped


def test_owed_stop_is_repeated_after_the_reconnect_until_the_belt_obeys():
    rig = started_rig(4.0)
    rig.belt.deaf = True
    rig.run(1, IDLE)
    rig.belt.connected = False
    rig.run(5, IDLE)
    reconnect(rig, IDLE)  # the link is back, the commands still do not get through
    mark = len(rig.belt.log)
    rig.run(16, IDLE)
    assert [c.action for c in rig.belt.log[mark:]].count(Action.STOP) >= 3
    rig.belt.deaf = False
    rig.run(8, IDLE)
    assert rig.belt.speed == 0


@pytest.mark.parametrize("data_after", [0.5, 4.0, 9.0])
def test_session_ended_during_a_link_loss_is_stopped_however_late_the_first_packet(data_after: float):
    """There used to be a fixed three-second window after the reconnect; a
    first data packet that took four was too late, and the belt ran on."""
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(5, IDLE)  # the session ends while the belt is out of reach
    assert rig.gov.halt_owed and not rig.gov.driving
    mark = len(rig.belt.log)
    reconnect(rig, IDLE, data_after=data_after)
    assert rig.belt.log[mark:] == []  # nothing is known about the belt yet
    rig.run(8, IDLE)
    assert [c.action for c in rig.belt.log[mark:]] == [Action.STOP] and rig.belt.speed == 0


@pytest.mark.parametrize("data_after", [0.5, 4.0, 9.0])
def test_stop_pressed_during_a_link_loss_is_applied_however_late_the_first_packet(data_after: float):
    rig = started_rig(3.0)
    rig.belt.connected = False
    rig.run(2, live(3.0))
    rig.gov.stop_pressed(rig.now)
    rig.run(20, live(3.0))
    mark = len(rig.belt.log)
    reconnect(rig, live(3.0), data_after=data_after)
    rig.run(8, live(3.0))
    assert [c.action for c in rig.belt.log[mark:]] == [Action.STOP] and rig.belt.speed == 0
    assert rig.gov.phase is Phase.USER_STOPPED


def test_running_belt_is_adopted_however_late_the_first_packet():
    rig = started_rig(3.0)
    mark = len(rig.belt.log)
    rig.belt.connected = False
    rig.run(5, live(4.0, moving=False))
    reconnect(rig, live(4.0, moving=False), data_after=9.0)
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING
    rig.run(10, live(4.0, moving=False))
    assert Action.START not in [c.action for c in rig.belt.log[mark:]]
    assert rig.belt.speed == pytest.approx(4.0) and rig.gov.phase is Phase.FOLLOWING


def test_owed_stop_is_given_up_after_half_an_hour_out_of_reach():
    rig = started_rig(3.0)
    rig.belt.deaf = True
    rig.run(1, IDLE)
    rig.belt.connected = False
    rig.run(HALT_GIVE_UP_S - 10, IDLE, dt=1.0)
    assert rig.gov.halt_owed
    rig.run(20, IDLE, dt=1.0)
    assert not rig.gov.halt_owed and not rig.gov.halt_pending
    assert "halt_abandoned" in rig.gov.notes


def test_a_stop_that_was_obeyed_before_the_link_dropped_is_not_owed():
    """... so that a belt the walker starts with the remote during the
    outage is not stopped under them on the reconnect."""
    rig = started_rig(3.0)
    rig.run(3, IDLE)  # stopped, and seen to stand still
    assert not rig.gov.halt_pending
    rig.belt.connected = False
    rig.belt.apply([c for c in [dataclasses.replace(rig.belt.log[0])]])  # START pressed on the remote
    rig.belt.log.pop()
    rig.belt.target = 2.0
    rig.run(15, IDLE)
    mark = len(rig.belt.log)
    reconnect(rig, IDLE)
    rig.run(30, IDLE)
    assert rig.belt.log[mark:] == [] and rig.belt.speed == pytest.approx(2.0)


# ---- [3] no speed-ups on a stale picture -----------------------------------------------------


def test_a_picture_more_than_a_second_old_does_not_speed_the_belt_up():
    limits = Limits(signal_timeout_s=15, hold_s=5)  # the longest timeout the settings allow
    rig = Rig(limits)
    rig.run(1, IDLE)
    rig.run(8, live(4.8))  # ramping up
    mark, before = len(rig.belt.log), rig.gov.commanded_kmh
    rig.run(14, None)  # the last frame keeps "counting" for 15 s
    assert rig.gov.phase is Phase.FOLLOWING
    late = [s for s in speeds_since(rig, mark)]
    assert all(s <= before + UP + 1e-9 for s in late), "the belt went on accelerating with no picture"
    assert len([s for s in late if s > before]) <= 1  # (the frame was under a second old for one more command)
    assert rig.belt.speed <= before + UP + 1e-9


def test_a_stale_picture_may_still_slow_the_belt_down():
    rig = started_rig(4.0)
    mark = len(rig.belt.log)
    rig.run(8, Reading(age_s=2.0, active=True, moving=True, target_kmh=2.0, session=1))
    assert rig.gov.phase is Phase.FOLLOWING
    assert rig.belt.speed == pytest.approx(2.0)
    assert speeds_since(rig, mark)[-1] == pytest.approx(2.0)


def test_a_stale_picture_does_not_start_the_belt():
    rig = Rig()
    rig.run(2, IDLE)
    rig.run(10, Reading(age_s=1.5, active=True, moving=True, target_kmh=3.0, session=1))
    assert rig.belt.log == [] and rig.gov.phase is Phase.HOLDING
    rig.run(1, live(3.0))
    assert rig.actions() == [Action.START]


def test_hold_and_pause_times_count_from_the_last_frame():
    """SAFETY.md: holds for 5 seconds, slows, pauses after 2 minutes of
    that. Measured from the last frame - not from three seconds later, when
    the signal is declared lost."""
    limits = Limits(signal_timeout_s=3, hold_s=5, safe_speed_kmh=1.3, loss_pause_s=120)
    rig = started_rig(4.0, limits)
    phases: dict[Phase, float] = {}
    for tick in range(1, int(130 / 0.25) + 1):
        rig.run(0.25, None)
        phases.setdefault(rig.gov.phase, tick * 0.25)
    assert phases[Phase.HOLDING] == pytest.approx(3.25, abs=0.26)  # declared lost
    assert phases[Phase.SAFE] == pytest.approx(5.0, abs=0.26)  # slowing begins
    assert phases[Phase.WAITING] == pytest.approx(125.0, abs=0.26)  # paused


# ---- [5] the first speed command -----------------------------------------------------------------


@pytest.mark.parametrize("garbage", [12.0, 655.35])
def test_one_garbled_packet_as_the_belt_starts_does_not_send_it_to_the_cap(garbage: float):
    rig = Rig(Limits(max_speed_kmh=6.0))
    rig.run(1, IDLE)
    rig.run(2, live(1.3))  # START sent, the pad is counting down
    assert rig.actions() == [Action.START]
    one_packet(rig, live(1.3), garbage)  # the first packet that says "moving" is garbage
    rig.run(20, live(1.3))
    speeds = speeds_since(rig, 0)
    assert speeds and max(speeds) <= 1.3 + 1e-9, f"commanded {max(speeds)} with the game asking for 1.3"
    assert rig.max_seen <= 1.3 + 1e-9
    assert rig.belt.speed == pytest.approx(1.3)


def test_restart_while_the_belt_still_reports_its_old_speed_ramps_from_the_minimum():
    """Pause, and resume half a second later: the belt is still coasting.
    The first speed command used to be "what it reports plus a step" - a
    restart at nearly the old speed instead of the pad's minimum."""
    rig = started_rig(4.5, coast=0.4)
    rig.run(0.5, live(4.5, paused=True))
    mark = len(rig.belt.log)
    rig.run(1.25, live(4.5))
    assert rig.belt.log[mark].action is Action.START and rig.belt.speed > 3.5
    rig.run(30, live(4.5))
    ups = speeds_since(rig, mark)
    assert ups[0] <= 1.0 + UP + 1e-9, f"the first command after the restart was {ups[0]}"
    assert all(0 < b - a <= UP + 1e-9 for a, b in itertools.pairwise(ups))
    assert rig.belt.speed == pytest.approx(4.5)


def test_a_belt_found_running_is_given_a_speed_only_when_two_packets_agree():
    rig = Rig(Limits(max_speed_kmh=5.0))
    rig.belt.running, rig.belt.speed, rig.belt.target = True, 2.0, 2.0
    rig.run(0.5, live(1.2, moving=False))  # the first look, and the first packet
    assert rig.belt.log == []
    assert one_packet(rig, live(1.2, moving=False), 12.0) == []  # garbage
    assert one_packet(rig, live(1.2, moving=False), 2.0) == []  # contradicts the one before it
    rig.run(10, live(1.2, moving=False))
    assert Action.START not in rig.actions()
    speeds = speeds_since(rig, 0)
    assert speeds[0] == pytest.approx(1.6) and max(speeds) <= 2.0 + 1e-9  # down from where it was, gently
    assert rig.belt.speed == pytest.approx(1.2)


def test_a_belt_found_running_above_the_cap_is_taken_over_at_the_cap():
    rig = Rig(Limits(max_speed_kmh=3.0))
    rig.belt.running, rig.belt.speed, rig.belt.target = True, 5.0, 5.0
    rig.run(10, live(3.0, moving=False))
    speeds = speeds_since(rig, 0)
    assert speeds and max(speeds) <= 3.0 + 1e-9
    assert rig.belt.speed == pytest.approx(3.0)


# ---- [6] a belt started at the pad while the game is out of view -------------------------------


def test_remote_start_during_a_long_signal_loss_is_the_walkers_own_belt():
    limits = Limits(hold_s=5, safe_speed_kmh=1.3, loss_pause_s=20)
    rig = started_rig(3.0, limits)
    rig.run(60, None)  # held, slowed, paused
    assert rig.gov.phase is Phase.WAITING and rig.belt.speed == 0
    mark = len(rig.belt.log)
    for _ in range(3):  # it used to be paused again within a tick, every time
        rig.belt.apply([dataclasses.replace(rig.belt.log[0])])
        rig.belt.log.pop()
        rig.gov.notify_external_start(rig.now)
        rig.belt.target = 2.5
        rig.run(30, None)
        assert rig.belt.log[mark:] == [], "Walka interfered with a belt started at the pad"
        assert rig.belt.speed == pytest.approx(2.5)
        assert not rig.gov.driving and rig.gov.phase is Phase.IDLE
        assert rig.gov.shutdown(rig.now) == []  # not ours to stop on exit either
        rig.belt.running = False
        rig.gov.notify_external_stop()
        rig.run(5, None)
    assert "released" in rig.gov.notes
    # When the game is back in view the session is followed again.
    rig.belt.apply([dataclasses.replace(rig.belt.log[0])])
    rig.belt.log.pop()
    rig.gov.notify_external_start(rig.now)
    rig.run(10, None)
    rig.run(30, live(3.0, moving=False))
    assert rig.belt.speed == pytest.approx(3.0) and rig.gov.phase is Phase.FOLLOWING
    assert Action.START not in [c.action for c in rig.belt.log[mark:]]  # adopted, no countdown


def test_a_session_whose_signal_stays_away_is_let_go_of():
    limits = Limits(hold_s=5, safe_speed_kmh=1.3, loss_pause_s=20)
    rig = started_rig(3.0, limits)
    rig.run(ABANDONED_AFTER_S - 20, None, dt=1.0)
    assert rig.gov.driving and rig.gov.phase is Phase.WAITING
    rig.run(40, None, dt=1.0)
    assert not rig.gov.driving and rig.gov.phase is Phase.IDLE and "abandoned" in rig.gov.notes
    # The same session coming back into view is one we did not watch begin.
    mark = len(rig.belt.log)
    rig.run(20, live(3.0, moving=False))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING


def test_a_stop_latch_outlives_the_session_being_let_go_of():
    limits = Limits(hold_s=5, safe_speed_kmh=1.3, loss_pause_s=20)
    rig = started_rig(3.0, limits)
    rig.gov.stop_pressed(rig.now)
    rig.belt.running = False
    rig.run(ABANDONED_AFTER_S + 60, None, dt=1.0)
    assert not rig.gov.driving and rig.gov.user_stopped
    mark = len(rig.belt.log)
    rig.run(10, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.USER_STOPPED


# ---- [7] "started moving" needs a standing reading seen after the blind period -----------------


def test_first_frame_after_a_long_signal_loss_already_moving_is_no_deliberate_act():
    """The character stood when the picture went. Two minutes later it is
    back and the character is on a flight path: no key was pressed now."""
    limits = Limits(hold_s=2, safe_speed_kmh=1.3, loss_pause_s=10)
    rig = started_rig(3.0, limits)
    rig.run(5, live(1.3, moving=False))  # standing when the picture goes
    rig.run(60, None)
    assert rig.gov.phase is Phase.WAITING and rig.belt.speed == 0
    mark = len(rig.belt.log)
    rig.run(30, live(3.0, moving=True))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING
    rig.run(1, live(1.3, moving=False))
    rig.run(25, live(3.0, moving=True))  # seen standing, then moving: that is a key
    assert rig.belt.speed == pytest.approx(3.0)


def test_reconnect_does_not_take_an_old_standing_reading_for_a_fresh_one():
    """The belt was paused (character standing, stand speed 0) when the link
    dropped; the character has been running for a minute when it is back."""
    rig = started_rig(3.0)
    rig.run(5, live(0.0, moving=False))
    assert rig.gov.phase is Phase.STANDING
    rig.belt.connected = False
    rig.run(60, live(3.0, moving=True))
    mark = len(rig.belt.log)
    reconnect(rig, live(3.0, moving=True))
    rig.run(30, live(3.0, moving=True))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING
    rig.run(1, live(0.0, moving=False))
    rig.run(25, live(3.0, moving=True))
    assert rig.belt.speed == pytest.approx(3.0)


def test_belt_switched_off_at_the_pad_during_a_link_loss_stays_off():
    """The review's case F: the strongest "stop at the treadmill" there is,
    and it cannot be seen."""
    rig = started_rig(3.0)
    rig.run(3, live(1.3, moving=False))  # the character happens to stand when the link drops
    rig.belt.connected = False
    rig.belt.running = False
    rig.belt.speed = 0.0
    rig.run(60, live(3.0, moving=True))  # ... and runs for a minute meanwhile
    mark = len(rig.belt.log)
    reconnect(rig, live(3.0, moving=True))
    rig.run(20, live(3.0, moving=True))
    rig.run(2, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.USER_STOPPED


# ---- [8] exit -----------------------------------------------------------------------------------


@pytest.mark.parametrize("what", ["pause", "stop_button", "session_end"])
def test_shutdown_stops_a_belt_whose_stop_has_not_been_obeyed(what: str):
    rig = started_rig(4.0)
    rig.belt.deaf = True
    if what == "stop_button":
        rig.gov.stop_pressed(rig.now)
    rig.run(2, {"pause": live(4.0, paused=True), "stop_button": live(4.0), "session_end": IDLE}[what])
    assert rig.belt.speed == pytest.approx(4.0)
    assert [c.action for c in rig.gov.shutdown(rig.now, rig.belt.view())] == [Action.STOP]
    assert rig.gov.shutdown(rig.now, rig.belt.view()) == []


def test_shutdown_leaves_a_belt_alone_whose_stop_was_obeyed():
    for reading in (live(4.0, paused=True), IDLE):
        rig = started_rig(4.0)
        rig.run(3, reading)
        assert rig.belt.speed == 0
        assert rig.gov.shutdown(rig.now, rig.belt.view()) == []


# ---- [11] a Resume that cannot be acted on expires ----------------------------------------------


def loss_paused_rig() -> Rig:
    limits = Limits(hold_s=2, safe_speed_kmh=1.3, loss_pause_s=10)
    rig = started_rig(3.0, limits)
    rig.run(1, live(1.3, moving=False))
    rig.run(40, None)
    assert rig.gov.phase is Phase.WAITING and rig.gov.needs_act and rig.belt.speed == 0
    return rig


def test_resume_pressed_while_the_game_is_out_of_view_expires():
    rig = loss_paused_rig()
    rig.gov.resume()  # on the dashboard - the window that covers the game
    assert not rig.gov.needs_act
    rig.run(300, None, dt=1.0)  # ... and nobody goes back to the game
    assert rig.gov.needs_act and "resume_expired" in rig.gov.notes
    rig.run(3300, None, dt=1.0)
    mark = len(rig.belt.log)
    rig.run(30, live(1.3, moving=False))  # an hour later someone uncovers the game window
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING


def test_resume_is_good_for_a_minute():
    rig = loss_paused_rig()
    rig.gov.resume()
    rig.run(RESUME_VALID_S - 15, None, dt=1.0)
    rig.run(25, live(1.3, moving=False))
    assert rig.belt.speed == pytest.approx(1.3)


def test_stop_latch_lifted_by_a_resume_that_expired_is_back():
    rig = started_rig(3.0)
    rig.gov.stop_pressed(rig.now)
    rig.belt.running = False
    rig.run(10, None)
    rig.gov.resume()
    assert not rig.gov.user_stopped
    rig.run(RESUME_VALID_S + 30, None, dt=1.0)
    assert rig.gov.user_stopped
    mark = len(rig.belt.log)
    rig.run(10, live(3.0, moving=False))
    rig.run(20, live(3.0, moving=True))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.USER_STOPPED


def test_resume_pressed_while_the_treadmill_is_out_of_reach_expires_too():
    rig = started_rig(3.0)
    rig.run(5, live(0.0, moving=False))  # belt paused: nothing is owed
    rig.belt.connected = False
    rig.run(5, live(3.0, moving=False))
    rig.gov.resume()
    rig.run(RESUME_VALID_S + 30, live(3.0, moving=False), dt=1.0)
    mark = len(rig.belt.log)
    reconnect(rig, live(3.0, moving=False))
    rig.run(30, live(3.0, moving=False))
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING


# ---- [2] [13] sessions that end where the governor is not looking ------------------------------


def test_a_made_up_session_over_is_not_watching_the_next_session_begin():
    """The app says "session over" itself when a dashboard session ends. A
    game session showing through on the next tick was there all along."""
    rig = started_rig(2.0)  # (stands in for the dashboard session)
    rig.run(0.25, Reading(age_s=0.0, active=False, synthetic=True))
    assert rig.actions()[-1] is Action.STOP and not rig.gov.driving
    mark = len(rig.belt.log)
    rig.run(40, live(4.5, session=7, moving=True))  # the game character runs on throughout
    assert rig.belt.log[mark:] == [] and rig.gov.phase is Phase.WAITING
    rig.run(1, live(4.5, session=7, moving=False))
    rig.run(40, live(4.5, session=7, moving=True))
    assert rig.belt.speed == pytest.approx(4.5)


def test_session_over_while_the_treadmill_is_disconnected_is_not_lost():
    """The game is closed while the link is down. The governor used to see
    that only if it was connected in the very tick it was said."""
    rig = started_rig(3.0)
    rig.belt.connected = False
    old_frame = stale(30, active=True, moving=True, target_kmh=3.0, session=1)
    rig.run(0.25, IDLE)  # said once ...
    rig.run(10, old_frame)  # ... then there is only an old frame
    assert not rig.gov.driving and rig.gov.halt_owed
    mark = len(rig.belt.log)
    reconnect(rig, old_frame)
    rig.run(8, old_frame)
    assert [c.action for c in rig.belt.log[mark:]] == [Action.STOP] and rig.belt.speed == 0
    assert rig.gov.phase is Phase.IDLE


# ---- [15] and the cap --------------------------------------------------------------------------


def test_cap_lowered_far_below_a_fast_belt_comes_down_at_the_deceleration_limit():
    rig = started_rig(6.0, Limits(max_speed_kmh=6.0))
    rig.gov.limits.max_speed_kmh = 2.0
    mark, start = len(rig.belt.log), len(rig.speeds)
    rig.run(0.25, live(6.0))
    assert speeds_since(rig, mark) == [pytest.approx(5.6)], "the first reaction must come at once, and be one step"
    rig.run(12, live(6.0))
    downs = speeds_since(rig, mark)
    assert downs[-1] == pytest.approx(2.0) and rig.belt.speed == pytest.approx(2.0)
    assert all(0 < a - b <= 0.4 + 1e-9 for a, b in itertools.pairwise([6.0] + downs))
    # No faster than 0.4 km/h in any second, for the walker.
    window = rig.speeds[start:]
    assert all(a - b <= 0.4 + 0.13 for a, b in zip(window, window[4:], strict=False))


def test_cap_lowered_while_the_walker_is_slowing_the_belt_does_not_send_it_up_to_the_cap():
    """The belt is on its way down to a speed set with the remote, far below
    the command, when the cap is lowered to something in between. "React to
    the cap at once" must not mean: bring the belt up to the cap."""
    rig = started_rig(6.0, Limits(max_speed_kmh=6.0))
    remote(rig, 1.0)
    rig.run(8, live(6.0))  # 2.0 km/h by now, still slowing
    rig.gov.limits.max_speed_kmh = 3.0
    mark, start = len(rig.belt.log), len(rig.speeds)
    rig.run(30, live(6.0))
    assert rig.speeds[start:] == sorted(rig.speeds[start:], reverse=True), "the belt was sped up"
    assert all(s <= 1.0 + 1e-9 for s in speeds_since(rig, mark))
    assert rig.belt.speed == pytest.approx(1.0) and rig.gov.ceiling_kmh == pytest.approx(1.0)


def test_only_a_step_down_is_ever_commanded_above_the_cap():
    rng = random.Random(5)
    rig = started_rig(6.0, Limits(max_speed_kmh=6.0))
    last = 6.0
    for _ in range(300):
        if rng.random() < 0.1:
            rig.gov.limits.max_speed_kmh = rng.choice([1.0, 2.0, 3.5, 5.0, 6.0])
        mark = len(rig.belt.log)
        rig.run(0.25, live(rng.choice([1.0, 3.0, 6.0])) if rng.random() < 0.9 else None)
        for c in rig.belt.log[mark:]:
            if c.action is Action.SET_SPEED:
                if c.speed_kmh > rig.gov.limits.max_speed_kmh + 1e-9:
                    assert c.speed_kmh < last - 1e-9, "a speed-up, or a hold, above the cap"
                last = c.speed_kmh
            elif c.action in HALT or c.action is Action.START:
                last = 0.0
