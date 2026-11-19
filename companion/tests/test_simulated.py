"""The simulated pad must behave like the hardware notes say a real one does,
because every higher-level test (and the demo) stands on it.

Like a real pad it is worked with its remote - ``press_*`` - and with
nothing else: the companion has no way to move it."""

from __future__ import annotations

import asyncio
import itertools
import time

import pytest

from walka.treadmill.base import KMH_PER_MPH, BeltState, TreadmillEvent
from walka.treadmill.simulated import SimulatedTreadmill

MPH = KMH_PER_MPH


def make(**kwargs):
    now = [50.0]
    kwargs.setdefault("auto_run", False)
    sim = SimulatedTreadmill(clock=lambda: now[0], **kwargs)
    events: list[TreadmillEvent] = []
    sim.on_event = events.append
    return sim, events, now


def run(coro):
    return asyncio.run(coro)


def advance(sim: SimulatedTreadmill, seconds: float, dt: float = 0.1) -> list[float]:
    speeds = []
    for _ in range(int(round(seconds / dt))):
        sim.advance(dt)
        speeds.append(sim.status.speed_kmh)
    return speeds


async def started(sim: SimulatedTreadmill) -> None:
    await sim.connect()
    sim.press_start()
    advance(sim, 5)
    assert sim.status.state is BeltState.RUNNING


def test_initial_status_us_and_metric():
    us, _, _ = make()
    assert us.kind == "simulated"
    assert us.status.mph_grid and not us.status.connected
    assert us.status.min_kmh == pytest.approx(0.6 * MPH)
    assert us.status.max_kmh == pytest.approx(4.0 * MPH)
    assert us.status.step_kmh == pytest.approx(0.1 * MPH)
    metric, _, _ = make(us_firmware=False)
    assert not metric.status.mph_grid
    assert (metric.status.min_kmh, metric.status.max_kmh, metric.status.step_kmh) == (1.0, 6.0, 0.1)


def test_connect_and_disconnect_events_are_emitted_once():
    sim, events, now = make()

    async def scenario():
        await sim.connect()
        await sim.connect()
        assert sim.status.connected and sim.status.state is BeltState.STOPPED
        assert sim.status.updated_at == now[0]
        await sim.disconnect()
        await sim.disconnect()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED, TreadmillEvent.DISCONNECTED]
    assert not sim.status.connected


def test_connecting_moves_nothing():
    """A pad that is listened to stands still until somebody presses start
    on it - for as long as it takes."""
    sim, events, _ = make()

    async def scenario():
        await sim.connect()
        speeds = advance(sim, 600)
        assert set(speeds) == {0.0} and sim.status.state is BeltState.STOPPED
        assert sim.status.target_kmh is None and sim.status.distance_m == 0

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED]


def test_the_remote_does_nothing_to_a_pad_that_is_switched_off():
    sim, events, _ = make()
    sim.press_start()
    sim.press_speed(3.0)
    sim.press_faster()
    advance(sim, 5)
    assert sim.status.state is BeltState.UNKNOWN and sim.status.speed_kmh == 0 and events == []


def test_start_counts_down_then_runs_at_minimum_speed():
    sim, _, _ = make()

    async def scenario():
        await sim.connect()
        sim.press_start()
        assert sim.status.state is BeltState.STARTING
        speeds = advance(sim, 2.9)
        assert set(speeds) == {0.0}  # 3-2-1: nothing moves
        assert sim.status.state is BeltState.STARTING
        advance(sim, 0.2)
        assert sim.status.state is BeltState.RUNNING
        speeds = advance(sim, 3)
        assert speeds[-1] == pytest.approx(0.6 * MPH)  # settles at the minimum
        assert max(speeds) <= 0.6 * MPH + 1e-9
        assert sim.status.target_kmh == pytest.approx(0.6 * MPH)
        sim.press_start()  # already running: nothing changes
        assert sim.status.state is BeltState.RUNNING and sim.status.target_kmh == pytest.approx(0.6 * MPH)

    run(scenario())


def test_the_speed_buttons_do_nothing_while_stopped_or_paused():
    sim, _, _ = make()

    async def scenario():
        await sim.connect()
        sim.press_speed(4.0)
        sim.press_faster()
        assert sim.status.target_kmh is None and sim.status.state is BeltState.STOPPED
        advance(sim, 5)
        assert sim.status.speed_kmh == 0
        await started(sim)
        sim.press_pause()
        sim.press_speed(4.0)
        sim.press_slower()
        assert sim.status.target_kmh is None and sim.status.state is BeltState.PAUSED
        advance(sim, 5)
        assert sim.status.speed_kmh == 0

    run(scenario())


def test_belt_ramps_instead_of_jumping():
    sim, _, _ = make(ramp_kmh_per_s=0.8)

    async def scenario():
        await started(sim)
        sim.press_speed(3.0 * MPH)
        speeds = [sim.status.speed_kmh] + advance(sim, 10)
        steps = [b - a for a, b in itertools.pairwise(speeds)]
        assert max(steps) <= 0.8 * 0.1 + 1e-9
        assert speeds[-1] == pytest.approx(3.0 * MPH)
        sim.press_speed(1.0 * MPH)
        speeds = [sim.status.speed_kmh] + advance(sim, 10)
        steps = [b - a for a, b in itertools.pairwise(speeds)]
        assert min(steps) >= -0.8 * 0.1 - 1e-9
        assert speeds[-1] == pytest.approx(1.0 * MPH)

    run(scenario())


def test_us_firmware_grid_every_step_is_reachable():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        for tenths in range(6, 41):
            for nudge in (0.0, 0.05, -0.05):
                sim.press_speed(tenths * MPH / 10 + nudge)
                assert sim.status.target_kmh == pytest.approx(tenths * MPH / 10, abs=1e-9)
        sim.press_speed(0.1)
        assert sim.status.target_kmh == pytest.approx(0.6 * MPH)
        sim.press_speed(50.0)
        assert sim.status.target_kmh == pytest.approx(4.0 * MPH)

    run(scenario())


def test_metric_firmware_keeps_to_tenths_and_to_its_range():
    sim, _, _ = make(us_firmware=False)

    async def scenario():
        await started(sim)
        for asked, expected in ((3.14, 3.1), (3.16, 3.2), (3.2, 3.2), (0.3, 1.0), (9.0, 6.0)):
            sim.press_speed(asked)
            assert sim.status.target_kmh == pytest.approx(expected)

    run(scenario())


def test_plus_and_minus_move_two_steps_like_the_pads_console():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        sim.press_speed(2.0 * MPH)
        sim.press_faster()
        assert sim.status.target_kmh == pytest.approx(2.2 * MPH)
        sim.press_slower()
        sim.press_slower()
        assert sim.status.target_kmh == pytest.approx(1.8 * MPH)
        for _ in range(40):
            sim.press_faster()
        assert sim.status.target_kmh == pytest.approx(4.0 * MPH)  # the pad's own top speed
        for _ in range(40):
            sim.press_slower()
        assert sim.status.target_kmh == pytest.approx(0.6 * MPH)  # ... and its slowest: never a stop

    run(scenario())


def test_counters_survive_a_pause_but_not_a_stop():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        sim.press_speed(3.0 * MPH)
        advance(sim, 120)
        status = sim.status
        distance, steps, energy, elapsed = status.distance_m, status.steps, status.energy_kcal, status.elapsed_s
        assert distance == pytest.approx(3.0 * MPH / 3.6 * 120, rel=0.08)
        assert steps == pytest.approx(distance / 1000 * 1700, abs=3)
        assert energy == pytest.approx(distance / 1000 * 50, abs=1.01)
        assert 120 <= elapsed <= 123  # the two seconds at minimum speed before, plus these two minutes

        sim.press_pause()
        advance(sim, 30)
        assert status.speed_kmh == 0 and status.state is BeltState.PAUSED
        assert status.distance_m >= distance and status.steps >= steps  # (it coasted a little)
        assert status.elapsed_s == elapsed
        paused_distance = status.distance_m

        sim.press_start()  # resume
        advance(sim, 30)
        assert status.distance_m > paused_distance  # carries on counting, no reset
        assert status.elapsed_s > elapsed

        sim.press_stop()
        advance(sim, 0.1)
        assert status.state is BeltState.STOPPED
        assert status.elapsed_s == 0 and status.steps <= 1 and status.distance_m <= 1  # "END": zeroed

        advance(sim, 10)
        sim.press_start()
        advance(sim, 10)
        assert status.distance_m < 5  # a new workout counts from zero

    run(scenario())


def test_halting_coasts_to_zero_quickly():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        sim.press_speed(4.0 * MPH)
        advance(sim, 15)
        sim.press_stop()
        speeds = advance(sim, 4)
        assert speeds[0] > 5.0 and speeds[-1] == 0.0
        assert all(b <= a for a, b in itertools.pairwise(speeds))

    run(scenario())


def test_the_buttons_announce_nothing_to_the_companion():
    """What the walker does at the pad is theirs. The reader reports the
    link coming and going; the belt is read from the status."""
    sim, events, _ = make()

    async def scenario():
        await sim.connect()
        events.clear()
        sim.press_stop()  # already stopped
        sim.press_start()
        assert sim.status.state is BeltState.STARTING
        advance(sim, 5)
        sim.press_pause()
        assert sim.status.state is BeltState.PAUSED
        sim.press_stop()  # paused -> stopped is a real change
        assert sim.status.state is BeltState.STOPPED

    run(scenario())
    assert events == []


def test_nothing_advances_while_disconnected_or_with_no_time():
    sim, _, now = make()

    async def scenario():
        await started(sim)
        stamp = sim.status.updated_at
        now[0] += 1
        sim.advance(0)
        sim.advance(-1)
        assert sim.status.updated_at == stamp
        sim.advance(0.1)
        assert sim.status.updated_at == now[0]
        await sim.disconnect()
        distance = sim.status.distance_m
        advance(sim, 10)
        assert sim.status.distance_m == distance

    run(scenario())


def test_auto_run_advances_in_real_time_and_stops_on_disconnect():
    # Sleeps well above 16 ms and a wall-clock deadline: on Windows the event
    # loop's clock ticks every 15.6 ms, and two tasks that both sleep for less
    # than a tick wake each other up without any time passing.
    sim = SimulatedTreadmill(countdown_s=0.05, tick_s=0.03, ramp_kmh_per_s=50.0)

    async def scenario():
        await sim.connect()
        sim.press_start()
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.03)
            if sim.status.speed_kmh > 0.5:
                break
        assert sim.status.state is BeltState.RUNNING and sim.status.speed_kmh > 0.5
        await sim.disconnect()
        assert sim._task is None
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "walka-sim-treadmill"]

    run(asyncio.wait_for(scenario(), 5.0))
