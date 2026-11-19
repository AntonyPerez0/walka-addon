"""Where stat frames part from the live reading.

The beacon shows two kinds of frame (see ``protocol``): live frames, which
say what the character and its walking session are doing right now, and now
and then a stat frame, which carries one number of the character's journey
for the picture the dashboard makes. ``BeaconSource``
sends everything it reads through here first, and this module keeps one
rule (docs/PROTOCOL.md, "Stat frames"):

    A stat frame is never part of the live reading.

It does not make the reading fresh, it does not count as "a frame was seen"
for the frozen-picture check, and it does not prove that the game is still
running. To the live reading, a moment in which the strip shows a stat
frame is a moment without news - so a picture that froze on a stat frame is
a lost signal like any other.

The numbers themselves go to whoever asked for them (``BeaconSource.on_stat``)
and to nobody else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .capture import ReadResult
from .protocol import StatFrame

log = logging.getLogger(__name__)

# Called with (stat id, value) for every stat frame that carries a stat this
# build knows; the id is a ``protocol.Stat``. It runs on the thread that reads
# the screen, so it must be quick and must not assume it is on the event loop.
StatHandler = Callable[[int, int], None]

_complained = False  # a failing handler is reported once, not twice a second


def live_only(result: Any, handler: StatHandler | None) -> Any:
    """What one read of the screen (a ``capture.ReadResult`` or None) means to
    the live reading.

    A read that decoded a live frame, or nothing, is returned as it is. A
    read that decoded a stat frame is handed to ``handler`` and replaced by
    exactly what the reader returns when there is no strip on screen.
    """
    decoded = result.decoded if result is not None else None
    if decoded is None or decoded.frame is not None:
        return result
    deliver(decoded.stat, handler)
    return ReadResult(None, None, "")


def take(frame: object, handler: StatHandler | None) -> bool:
    """True if ``frame`` is a stat frame - which is then handed to ``handler``
    and must go no further. False for anything else (nothing is done)."""
    if not isinstance(frame, StatFrame):
        return False
    deliver(frame, handler)
    return True


def deliver(stat: StatFrame | None, handler: StatHandler | None) -> None:
    """Hand one stat frame to ``handler``.

    Stats this build does not know are dropped: a newer addon may send some.
    Whatever the handler does wrong stays here - a fault in the journey
    numbers must not stop the thread that reads the screen.
    """
    known = stat.stat if stat is not None else None
    if handler is None or stat is None or known is None:
        return
    try:
        handler(known, stat.value)
    except Exception:
        global _complained
        if _complained:
            log.debug("stat frame handler failed again", exc_info=True)
        else:
            _complained = True
            log.exception("the journey numbers could not be recorded (reported once; walking is not affected)")
