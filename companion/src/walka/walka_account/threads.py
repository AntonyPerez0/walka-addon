"""Work that may block, kept off the loop that records the walk.

Not ``asyncio.to_thread``: that runs on the loop's own threads, and
Python waits for those when the program closes - a look into the Mac's
keychain that is waiting for a click, or a look-up of a name that never
answers, would then keep Walka from quitting. A daemon thread of its own
is simply left behind.

And the waits of this part that have a time limit. Not ``asyncio.wait_for``:
before Python 3.12 it keeps a cancellation to itself when what it waits
for is done in the same turn of the loop. A task of this part that is
cancelled must end - Walka is closing, and closing waits for nobody.
``asyncio.wait`` always lets a cancellation through.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class OffLoopTimeout(Exception):
    """The work took longer than it was given. (It may still finish, on its thread; nobody hears of it.)"""


async def off_loop(fn: Callable[..., T], *args: Any, timeout: float | None = None, name: str = "walka-account") -> T:
    """Run ``fn(*args)`` on a daemon thread of its own and wait for what it gives.

    What ``fn`` gives - which may be a key - goes into a box that only
    this function reads. The loop is woken with a callback that takes no
    arguments: asyncio writes a callback's arguments to the log when the
    callback fails or is slow, and a key must never be one of them.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()
    box: list[tuple[bool, Any]] = []

    def wake() -> None:
        if not done.done():
            done.set_result(None)

    def work() -> None:
        try:
            box.append((True, fn(*args)))
        except BaseException as exc:  # handed over, not swallowed: the one who waits raises it
            box.append((False, exc))
        try:
            loop.call_soon_threadsafe(wake)
        except RuntimeError:
            pass  # the loop is closed: Walka is quitting, and nobody waits any more

    threading.Thread(target=work, name=name, daemon=True).start()
    try:
        # (asyncio.wait: a cancellation that comes in the same turn as the answer still ends the caller.)
        finished, _ = await asyncio.wait({done}, timeout=timeout)
    finally:
        done.cancel()  # (nothing, if it is done. Else: nobody waits any more, and wake() finds it so)
    if not finished:
        raise OffLoopTimeout()
    ok, value = box[0]
    if ok:
        return value
    raise value


async def wait_for_event(event: asyncio.Event, seconds: float | None) -> bool:
    """Wait for ``event``, ``seconds`` at most (None: for as long as it takes). Did it come?

    A cancellation of the caller always passes, whatever comes with it in
    the same turn of the loop; and no task is left behind.
    """
    if event.is_set():
        return True
    if seconds is None:
        await event.wait()
        return True
    waiter = asyncio.ensure_future(event.wait())
    try:
        finished, _ = await asyncio.wait({waiter}, timeout=seconds)
        # (It came, though somebody may have cleared it again before this task got its turn.)
        return bool(finished) or event.is_set()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)  # (gone before this returns: no task is left)
