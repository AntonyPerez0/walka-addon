"""Work off the loop (walka_account.threads): what it gives, and what it must never write down."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from walka.walka_account.threads import OffLoopTimeout, off_loop

SECRET = "v4xk2j3h5l6m"


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

    assert run(scenario) < 1.0


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
