"""Believing the strip: one decoded frame is never enough.

What is read off the screen decides which game session a walk is filed
under, and what the Share page says about a character - so a frame that did
not come from the running game must not be believed. The decoder cannot
promise that by itself. A frame is protected by a 16-bit CRC, and a screen is full of
rows of light and dark runs - source code, a terminal, a chat window - that
pass such a check by accident: about one screenful of code in a hundred did,
before ``beacon.py`` learned to look at the picture around the frame. That
makes accidents rare. It cannot make them impossible, and it does nothing
about a *real* strip that is not live: a screenshot of the game, a paused
video of somebody else's.

What nothing but a running game does is count. The addon redraws the strip
ten times a second and every redraw carries the next value of an 8-bit
counter (``seq``). So this module believes a place on the screen only after
watching it tick:

* frames must be decoded at the same place, with cells of the same size;
* their counter must go up, by no more than the time between two looks
  allows (one step per tenth of a second, plus a little for a hitch in the
  game or a late capture) - a counter that stands still, jumps or runs
  backwards breaks the chain, and the chain starts again with that frame;
* until the place is believed, live frames must agree on the session id;
* it takes ``ADVANCES_AT_WINDOW`` such steps (three frames, a quarter of a
  second) where the strip was found at the game window, and
  ``ADVANCES_ELSEWHERE`` where it was found by searching the screen.

Belief is lost when the counter has not moved for ``STALL_S`` or the strip
turns up somewhere else, and has to be earned again.

A still picture never gets past this: its counter does not move. Two that
alternate do not either: to come back to the first value the counter would
have to run backwards, or once round - 256 steps, 25 seconds of flawless
counting. What does get past is a moving picture of a ticking strip, which
is what the game is; ``capture.BeaconReader`` sees to it that only the
game's own window is looked at wherever the window can be found.

Until a place is believed, ``TrustGate.admit`` answers exactly as if nothing
had been decoded, so nothing behind it - the live reading, the journey
numbers, the "game is running" check - ever sees an unproven frame.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

from .capture import ReadResult
from .protocol import Frame, StatFrame

# The addon redraws - and counts - at most once in this time (TICK_SECONDS in
# Walka/Walka.lua). It can be slower (a game running at 30 frames a second
# manages 7.5 redraws), never faster.
STEP_S = 0.1
# Counter steps allowed on top of one per STEP_S. One because two looks a
# moment apart can fall either side of a redraw; two more because after a
# hitch the game shows two redraws in quick succession, and because a busy
# computer may take the picture later than it noted the time.
EXTRA_STEPS = 3
# A counter that has not moved for this long proves nothing any more: the
# game is frozen, covered or gone. (The same time after which the reader
# stops waiting at a place: capture.LOCK_STALL_S.)
STALL_S = 1.5
# Steps to watch before believing a strip at the game window: with the first
# frame that is three decodes, each one counter value on from the last.
ADVANCES_AT_WINDOW = 2
# ... and anywhere else. A place found by searching the screen is a guess
# about where the game is, so it has to prove itself for twice as long.
ADVANCES_ELSEWHERE = 5
# Every step is checked against all the steps of the last so many seconds,
# so that a counter cannot run fast a little at a time.
HISTORY_S = 2.0
# Cells of one strip keep their size. (The measured pitch wobbles by a few
# hundredths of a pixel from frame to frame when it is not a whole number.)
PITCH_TOLERANCE_PX = 0.5

_NOTHING = ReadResult(None, None, "")


def steps_allowed(seconds: float) -> int:
    """The most a strip's counter can have moved in ``seconds``."""
    return int(max(0.0, seconds) / STEP_S + 1e-9) + EXTRA_STEPS


class TrustGate:
    """Lets a decoded strip through only once its place has been seen ticking.

    One gate watches one reader. ``admit`` is called with every read of the
    screen and hands back either that read or "nothing was decoded".
    """

    def __init__(self) -> None:
        # The chain being watched: where, and what it showed last.
        self._place: tuple[Any, int] | None = None
        self._cell_px = 0.0
        self._shown: Frame | StatFrame | None = None
        self._session: int | None = None  # of its live frames, until it is believed
        self._count = 0  # the counter, without the wrap at 256
        self._history: deque[tuple[float, int]] = deque()  # (when, count) of its first frame and every step
        self._advances = 0
        self._moved_at = -math.inf
        # The last frame that was let through, and where it was: seeing that
        # very frame again tells nobody anything new.
        self._passed: tuple[tuple[Any, int], Frame | StatFrame] | None = None

    @property
    def advances(self) -> int:
        """Counter steps seen in the current chain (for tests and diagnostics)."""
        return self._advances

    def admit(self, now: float, result: Any) -> Any:
        """What one read of the screen (a ``capture.ReadResult`` or None) is
        worth: the read itself if the strip in it is believed, otherwise
        exactly what the reader returns when there is no strip on screen."""
        decoded = result.decoded if result is not None else None
        if decoded is None:
            return result
        shown = decoded.frame if decoded.frame is not None else decoded.stat
        place = (result.origin, decoded.x0)
        needed = ADVANCES_AT_WINDOW if result.where == "window" else ADVANCES_ELSEWHERE

        if not self._continues(now, place, decoded.cell_px, shown, needed):
            self._start(now, place, decoded.cell_px, shown)
        if self._advances >= needed:
            if isinstance(shown, Frame):
                self._session = shown.session  # believed: the session may change from here on
            self._passed = (place, shown)
            return result
        if self._passed == (place, shown):
            # The frozen picture of a strip that was believed. Passing it on
            # says nothing new (its counter is the one already seen), and it
            # is how the source knows "frozen" from "gone".
            return result
        return _NOTHING

    # ---- the chain -------------------------------------------------------------
    def _start(self, now: float, place: tuple[Any, int], cell_px: float, shown: Frame | StatFrame) -> None:
        self._place = place
        self._cell_px = cell_px
        self._shown = shown
        self._session = shown.session if isinstance(shown, Frame) else None
        self._count = 0
        self._history.clear()
        self._history.append((now, 0))
        self._advances = 0
        self._moved_at = now

    def _continues(
        self, now: float, place: tuple[Any, int], cell_px: float, shown: Frame | StatFrame, needed: int
    ) -> bool:
        """Is this frame the next one of the chain? If so, it is added."""
        last = self._shown
        if last is None or place != self._place or abs(cell_px - self._cell_px) > PITCH_TOLERANCE_PX:
            return False
        step = (shown.seq - last.seq) % 256
        if step == 0:
            if shown != last:
                return False  # one counter value, two pictures: the addon never draws that
            if now - self._moved_at > STALL_S:
                self._advances = 0  # the picture stands still: belief is gone
            return True
        if now - self._moved_at > STALL_S:
            return False  # it moved, but after too long a silence to be the same chain
        count = self._count + step
        if any(count - earlier > steps_allowed(now - when) for when, earlier in self._history):
            return False  # faster than the addon counts: a jump, or a counter running backwards
        if isinstance(shown, Frame):
            if self._session is None:
                self._session = shown.session
            elif shown.session != self._session and self._advances < needed:
                return False  # not yet believed, and already telling two stories
        self._shown = shown
        self._count = count
        self._advances += 1
        self._moved_at = now
        self._history.append((now, count))
        while self._history[0][0] < now - HISTORY_S:
            self._history.popleft()
        return True
