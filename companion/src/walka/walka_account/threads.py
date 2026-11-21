"""Work that may block, kept off the loop that records the walk.

Not ``asyncio.to_thread``: that runs on the loop's own threads, and
Python waits for those when the program closes - a look into the Mac's
keychain that is waiting for a click, or a look-up of a name that never
answers, would then keep Walka from quitting. A daemon thread of its own
is simply left behind.
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
        await asyncio.wait_for(done, timeout)
    except asyncio.TimeoutError:
        raise OffLoopTimeout() from None
    ok, value = box[0]
    if ok:
        return value
    raise value
