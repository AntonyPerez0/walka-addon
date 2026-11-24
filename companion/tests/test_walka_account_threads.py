"""Work off the loop (walka_account.threads): what it gives, and what it must never write down."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from walka.walka_account.threads import OffLoopTimeout, off_loop, wait_for_event

SECRET = "v4xk2j3h5l6m"
# "At once", on a build machine that is busy: the work the caller walks away from sits for ten
# seconds, so three tell "at once" from "waited for it" as well as one would.
AT_ONCE_S = 3.0


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), 30))


def test_it_gives_what_the_work_gives_from_a_daemon_thread():
    seen: list[threading.Thread] = []

    def work(a: int, b: int) -> int:
        seen.append(threading.current_thread())
        return a + b

    async def scenario():
        return await off_loop(work, 2, 3, name="walka-test")

    assert run(scenario) == 5
    assert seen[0].daemon and seen[0].name == "walka-test" and seen[0] is not threading.main_thread()


def test_what_the_work_raises_is_raised_by_the_one_who_waits():
    class Mine(Exception):
        pass

    def work():
        raise Mine()

    async def scenario():
        with pytest.raises(Mine):
            await off_loop(work)

    run(scenario)


def test_work_that_takes_too_long_is_given_up_and_its_late_answer_is_written_nowhere(caplog, capfd):
    release = threading.Event()
    finished = threading.Event()

    def work() -> str:
        release.wait(10)
        finished.set()
        return SECRET

    async def scenario():
        with pytest.raises(OffLoopTimeout):
            await off_loop(work, timeout=0.05)
        release.set()
        while not finished.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # the thread's wake-up call arrives, and finds nobody waiting

    with caplog.at_level(logging.DEBUG):
        run(scenario)
    out, err = capfd.readouterr()
    assert SECRET not in caplog.text and SECRET not in out and SECRET not in err
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_the_one_who_waits_can_be_cancelled_at_once():
    release = threading.Event()

    def work() -> str:
        release.wait(10)
        return SECRET

    async def scenario():
        task = asyncio.ensure_future(off_loop(work))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        return time.monotonic() - started

    assert run(scenario) < AT_ONCE_S


def test_a_cancellation_that_meets_the_answer_in_the_same_turn_still_ends_the_one_who_waits():
    # Before Python 3.12, asyncio.wait_for kept such a cancellation to itself and gave the
    # answer instead: a task that Walka cancelled while closing went on with its work.
    release = threading.Event()

    def work() -> str:
        release.wait(10)
        return SECRET

    async def scenario():
        task = asyncio.ensure_future(off_loop(work, timeout=30, name="walka-test-same-turn"))
        await asyncio.sleep(0.05)
        release.set()
        for thread in threading.enumerate():
            if thread.name == "walka-test-same-turn":
                thread.join(10)  # the loop is busy while the work ends: its answer is in, and nobody has looked yet
        await asyncio.sleep(0)  # the answer reaches the loop ...
        task.cancel()  # ... and in that same turn the one who waits is cancelled
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario)


def test_waiting_for_an_event_with_and_without_a_time_limit():
    async def scenario():
        tasks = asyncio.all_tasks()  # (this one - and before Python 3.12 the one that waits for it)
        event = asyncio.Event()
        assert await wait_for_event(event, 0.01) is False  # the time was up
        asyncio.get_running_loop().call_later(0.01, event.set)
        assert await wait_for_event(event, 30) is True
        assert await wait_for_event(event, 30) is True and await wait_for_event(event, None) is True  # it is set
        event.clear()
        asyncio.get_running_loop().call_later(0.01, event.set)
        assert await wait_for_event(event, None) is True
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == tasks  # no task is left behind by any of them

    run(scenario)


def test_an_event_that_came_and_was_cleared_again_before_the_one_who_waits_looked_did_come():
    async def scenario():
        event = asyncio.Event()
        task = asyncio.ensure_future(wait_for_event(event, 30))
        await asyncio.sleep(0.01)
        event.set()
        await asyncio.sleep(0)  # the event has come, and the one who waits has not looked yet ...
        event.clear()  # ... when somebody clears it again (the sender does, after every wake)
        assert await task is True

    run(scenario)


def test_a_cancellation_that_meets_the_event_in_the_same_turn_still_ends_the_wait():
    async def scenario():
        tasks = asyncio.all_tasks()
        event = asyncio.Event()
        task = asyncio.ensure_future(wait_for_event(event, 30))
        await asyncio.sleep(0.01)
        event.set()
        await asyncio.sleep(0)  # the event has come, and the one who waits has not looked yet ...
        task.cancel()  # ... when it is cancelled
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.all_tasks() == tasks

        # Cancelled while it simply waits: at once, and nothing is left.
        event.clear()
        task = asyncio.ensure_future(wait_for_event(event, 30))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert asyncio.all_tasks() == tasks

    run(scenario)


def test_a_loop_that_is_closed_before_the_work_ends_hears_nothing_and_nothing_is_printed(caplog, capfd):
    release = threading.Event()
    finished = threading.Event()

    def work() -> str:
        release.wait(10)
        try:
            return SECRET
        finally:
            finished.set()

    async def scenario():
        task = asyncio.ensure_future(off_loop(work))
        await asyncio.sleep(0.05)
        task.cancel()

    with caplog.at_level(logging.DEBUG):
        run(scenario)  # the loop is closed when this returns
        release.set()
        assert finished.wait(5)
        time.sleep(0.1)  # the thread meets the closed loop
    out, err = capfd.readouterr()
    assert "Traceback" not in err and SECRET not in err and SECRET not in out and SECRET not in caplog.text
