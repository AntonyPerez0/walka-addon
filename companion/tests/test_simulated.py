"""The simulated pad must behave like the hardware notes say a real one does,
because every higher-level test (and the demo) stands on it."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from walka.treadmill.base import KMH_PER_MPH, BeltState, TreadmillError, TreadmillEvent
from walka.treadmill.simulated import SimulatedTreadmill
from walka_fakes import us_firmware_mph_tenths

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
    await sim.start()
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


def test_commands_need_a_connection():
    sim, _, _ = make()

    async def scenario():
        for call in (sim.start(), sim.pause(), sim.stop(), sim.set_speed(3.0)):
            with pytest.raises(TreadmillError, match="not connected"):
                await call

    run(scenario())


def test_start_counts_down_then_runs_at_minimum_speed():
    sim, _, _ = make()

    async def scenario():
        await sim.connect()
        await sim.start()
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

    run(scenario())


def test_speed_commands_are_ignored_while_stopped_or_paused():
    sim, _, _ = make()

    async def scenario():
        await sim.connect()
        await sim.set_speed(4.0)
        assert sim.status.target_kmh is None and sim.status.state is BeltState.STOPPED
        advance(sim, 5)
        assert sim.status.speed_kmh == 0
        await started(sim)
        await sim.pause()
        await sim.set_speed(4.0)
        assert sim.status.target_kmh is None and sim.status.state is BeltState.PAUSED
        advance(sim, 5)
        assert sim.status.speed_kmh == 0

    run(scenario())


def test_belt_ramps_instead_of_jumping():
    sim, _, _ = make(ramp_kmh_per_s=0.8)

    async def scenario():
        await started(sim)
        await sim.set_speed(3.0 * MPH)
        speeds = [sim.status.speed_kmh] + advance(sim, 10)
        steps = [b - a for a, b in itertools.pairwise(speeds)]
        assert max(steps) <= 0.8 * 0.1 + 1e-9
        assert speeds[-1] == pytest.approx(3.0 * MPH)
        await sim.set_speed(1.0 * MPH)
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
                await sim.set_speed(tenths * MPH / 10 + nudge)
                assert sim.status.target_kmh == pytest.approx(tenths * MPH / 10, abs=1e-9)
        await sim.set_speed(0.1)
        assert sim.status.target_kmh == pytest.approx(0.6 * MPH)
        await sim.set_speed(50.0)
        assert sim.status.target_kmh == pytest.approx(4.0 * MPH)

    run(scenario())


def test_us_firmware_truncation_matches_the_independent_model():
    """The simulator's idea of the firmware == the integer model used to
    check the real driver."""
    sim, _, _ = make()
    for raw in range(0, 700):
        expected = max(6, min(40, us_firmware_mph_tenths(raw)))
        assert sim._firmware_speed(raw / 100) == pytest.approx(expected * MPH / 10, abs=1e-9), raw
    # The documented trap: 3.22 km/h is 1.9 mph to the firmware, 3.3 km/h is 2.0.
    assert sim._firmware_speed(3.22) == pytest.approx(1.9 * MPH)
    assert sim._firmware_speed(3.3) == pytest.approx(2.0 * MPH)


def test_metric_firmware_truncates_to_tenths():
    sim, _, _ = make(us_firmware=False)

    async def scenario():
        await started(sim)
        for asked, expected in ((3.14, 3.1), (3.19, 3.1), (3.2, 3.2), (0.3, 1.0), (9.0, 6.0)):
            await sim.set_speed(asked)
            assert sim.status.target_kmh == pytest.approx(expected)

    run(scenario())


def test_counters_survive_a_pause_but_not_a_stop():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        await sim.set_speed(3.0 * MPH)
        advance(sim, 120)
        status = sim.status
        distance, steps, energy, elapsed = status.distance_m, status.steps, status.energy_kcal, status.elapsed_s
        assert distance == pytest.approx(3.0 * MPH / 3.6 * 120, rel=0.08)
        assert steps == pytest.approx(distance / 1000 * 1700, abs=3)
        assert energy == pytest.approx(distance / 1000 * 50, abs=1.01)
        assert 120 <= elapsed <= 123  # the two seconds at minimum speed before, plus these two minutes

        await sim.pause()
        advance(sim, 30)
        assert status.speed_kmh == 0 and status.state is BeltState.PAUSED
        assert status.distance_m >= distance and status.steps >= steps  # (it coasted a little)
        assert status.elapsed_s == elapsed
        paused_distance = status.distance_m

        await sim.start()  # resume
        advance(sim, 30)
        assert status.distance_m > paused_distance  # carries on counting, no reset
        assert status.elapsed_s > elapsed

        await sim.stop()
        advance(sim, 0.1)
        assert status.state is BeltState.STOPPED
        assert status.elapsed_s == 0 and status.steps <= 1 and status.distance_m <= 1  # "END": zeroed

        advance(sim, 10)
        await sim.start()
        advance(sim, 10)
        assert status.distance_m < 5  # a new workout counts from zero

    run(scenario())


def test_halting_coasts_to_zero_quickly():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        await sim.set_speed(4.0 * MPH)
        advance(sim, 15)
        await sim.stop()
        speeds = advance(sim, 4)
        assert speeds[0] > 5.0 and speeds[-1] == 0.0
        assert all(b <= a for a, b in itertools.pairwise(speeds))

    run(scenario())


def test_buttons_on_the_machine_emit_external_events():
    sim, events, _ = make()

    async def scenario():
        await sim.connect()
        events.clear()
        sim.press_stop()  # already stopped: nothing to report
        assert events == []
        sim.press_start()
        assert events == [TreadmillEvent.EXTERNAL_START] and sim.status.state is BeltState.STARTING
        sim.press_start()  # already starting
        assert len(events) == 1
        advance(sim, 5)
        sim.press_stop()
        assert events[-1] is TreadmillEvent.EXTERNAL_STOP and sim.status.state is BeltState.STOPPED
        await sim.start()
        advance(sim, 5)
        await sim.pause()
        sim.press_stop()  # paused -> stopped is a real change
        assert events.count(TreadmillEvent.EXTERNAL_STOP) == 2

    run(scenario())


def test_our_own_commands_emit_no_external_events():
    sim, events, _ = make()

    async def scenario():
        await started(sim)
        await sim.set_speed(3.0)
        await sim.pause()
        await sim.start()
        await sim.stop()

    run(scenario())
    assert events == [TreadmillEvent.CONNECTED]


def test_fail_commands_raises_and_changes_nothing():
    sim, _, _ = make()

    async def scenario():
        await started(sim)
        await sim.set_speed(2.0 * MPH)
        sim.fail_commands = True
        for call in (sim.start(), sim.pause(), sim.stop(), sim.set_speed(4.0 * MPH)):
            with pytest.raises(TreadmillError, match="simulated command failure"):
                await call
        assert sim.status.state is BeltState.RUNNING
        assert sim.status.target_kmh == pytest.approx(2.0 * MPH)
        sim.fail_commands = False
        await sim.stop()
        assert sim.status.state is BeltState.STOPPED

    run(scenario())


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
    sim = SimulatedTreadmill(countdown_s=0.05, tick_s=0.01, ramp_kmh_per_s=50.0)

    async def scenario():
        await sim.connect()
        await sim.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sim.status.speed_kmh > 0.5:
                break
        assert sim.status.state is BeltState.RUNNING and sim.status.speed_kmh > 0.5
        await sim.disconnect()
        assert sim._task is None
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "walka-sim-treadmill"]

    run(asyncio.wait_for(scenario(), 5.0))
