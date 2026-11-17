"""A game on a virtual screen, in simulated time.

``StripGame`` is the addon's side of the beacon: it redraws the strip on a
``walka_fakes.FakeScreen`` the way ``Walka.lua`` does - at most ten times a
second, on a game frame, the counter one up with every redraw, every fifth
redraw a stat frame. ``watch`` is the companion's side: it drives a real
``capture.BeaconReader`` and ``sources.BeaconSource`` the way the source's
own thread does (read, then ingest), one capture at a time on a clock the
test owns. Nothing here sleeps or depends on how fast the test runs.

Used by the tests about trust (does the real strip get believed quickly,
and stay believed, through everything a session does?) and by the
measurement scripts that produced the numbers in docs/PROTOCOL.md.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from walka.capture import BeaconReader, GameWindow, ReadResult
from walka.governor import Reading
from walka.protocol import STAT_CYCLE, STAT_EVERY, Flags, Frame, StatFrame
from walka.sources import BeaconSource
from walka_fakes import FakeScreen

TICK_S = 0.1  # Walka.TICK_SECONDS


class Clock:
    """The one clock of a simulation (reader, source and game share it)."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class StripGame:
    """The game window and the addon's strip in it."""

    def __init__(
        self,
        screen: FakeScreen,
        *,
        origin: tuple[int, int] = (0, 0),
        cell: int = 4,
        game_fps: float = 60.0,
        start: float = 1000.0,
        seq: int = 0,
        stats: bool = True,
    ) -> None:
        self.screen = screen
        self.origin = origin
        self.cell = cell
        self.stats = stats
        self.seq = seq
        # What the addon would send next (the test changes these).
        self.session = 0
        self.flags = Flags(0)
        self.target_raw = 0
        self.char_speed_raw = 0
        self.visible = True  # False: a loading screen, the strip is not drawn
        self.covered = False  # True: another window is in front of the corner
        self.shown: Frame | StatFrame | None = None
        self.drawn: list[tuple[float, Frame | StatFrame]] = []  # every redraw, with its time
        self._frame_s = 1.0 / game_fps
        # OnUpdate adds up frame times and redraws on the first frame at or
        # after a tenth of a second - so not exactly ten times a second.
        self._frames_per_redraw = max(1, math.ceil(TICK_S / self._frame_s - 1e-6))
        self._next_redraw = start
        self._redraws = 0
        self._stat_index = 0

    @property
    def redraw_s(self) -> float:
        """Seconds from one redraw to the next."""
        return self._frames_per_redraw * self._frame_s

    def window(self, **extra) -> GameWindow:
        return GameWindow(
            "World of Warcraft", self.origin[0], self.origin[1], 1280, 720, r"C:\WoW\_forever_\Wow.exe", **extra
        )

    # ---- time ------------------------------------------------------------------
    def run_until(self, now: float) -> None:
        """Do every redraw that is due by ``now``."""
        while self._next_redraw <= now:
            self._redraw(self._next_redraw)
            self._next_redraw += self.redraw_s

    def hitch(self, seconds: float) -> None:
        """The game stalls: the next redraw comes this much later."""
        self._next_redraw += seconds

    def _redraw(self, at: float) -> None:
        self._redraws += 1
        self.seq = (self.seq + 1) % 256
        frame: Frame | StatFrame
        if self.stats and self._redraws % STAT_EVERY == 0:
            stat = STAT_CYCLE[self._stat_index % len(STAT_CYCLE)]
            self._stat_index += 1
            frame = StatFrame(seq=self.seq, stat_id=stat, value=1000 + self._stat_index)
        else:
            frame = Frame(
                seq=self.seq,
                session=self.session,
                flags=self.flags,
                target_raw=self.target_raw,
                char_speed_raw=self.char_speed_raw,
            )
        self.shown = frame
        self.drawn.append((at, frame))
        self._paint()

    # ---- what is on screen -----------------------------------------------------
    def _area(self) -> tuple[int, int, int, int]:
        return (self.origin[0], self.origin[1], 87 * self.cell, 2 * self.cell)

    def _paint(self) -> None:
        if self.covered:
            return
        if self.visible and self.shown is not None:
            self.screen.paint_strip(self.shown, self.origin[0], self.origin[1], self.cell)
        else:
            self.screen.fill(*self._area(), self.screen.background)

    def hide(self) -> None:
        """A loading screen: the addon does not draw the strip."""
        self.visible = False
        self._paint()

    def show(self) -> None:
        self.visible = True
        self._paint()

    def cover(self, seed: int = 1) -> None:
        """Another window in front of the game's corner: a few lines of
        something text-like where the strip was."""
        self.covered = True
        rng = random.Random(seed)
        x, y, width, height = self._area()
        for row in range(height + 6):
            values = bytearray()
            while len(values) < width + 40:
                values += bytes([rng.choice((30, 212))]) * rng.randint(1, 6)
            self.screen.paint_row(x, y + row, bytes(values[: width + 40]))

    def uncover(self) -> None:
        x, y, width, height = self._area()
        self.screen.fill(x, y, width + 40, height + 6, self.screen.background)
        self.covered = False
        self._paint()

    def move(self, origin: tuple[int, int]) -> None:
        """The window is dragged somewhere else."""
        self.screen.fill(*self._area(), self.screen.background)
        self.origin = origin
        self._paint()

    def reload(self) -> None:
        """/reload: the addon starts over, and so does its counter."""
        self.seq = 0
        self._redraws = 0


@dataclass
class Look:
    """One capture: what the reader returned and what the source made of it."""

    at: float
    result: ReadResult | None  # None: this capture was dropped
    reading: Reading
    state: str
    frame: Frame | None  # the live frame the source holds
    believed: int  # how many frames have got past the trust gate so far


@dataclass
class Watch:
    """A reader and a source looking at one screen."""

    reader: BeaconReader
    source: BeaconSource
    clock: Clock
    stats: list[tuple[int, int]] = field(default_factory=list)
    looks: list[Look] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source.on_stat = lambda stat_id, value: self.stats.append((int(stat_id), value))

    def believed(self) -> int:
        """Frames, live or stat, that the trust gate has let through."""
        return self.source.status.frames + len(self.stats)

    def look(self, *, dropped: bool = False) -> Look:
        """One turn of the source's capture loop at the clock's time."""
        now = self.clock.now
        result = None
        if not dropped:
            result = self.reader.read()
            self.source._ingest(now, self.reader, result)
        reading = self.source.reading(now)
        look = Look(now, result, reading, self.source.status.state, self.source.frame, self.believed())
        self.looks.append(look)
        return look


def watch(
    w: Watch,
    game: StripGame | None,
    seconds: float,
    *,
    capture_fps: float = 15.0,
    jitter_s: float = 0.0,
    drop: float = 0.0,
    rng: random.Random | None = None,
    script: list[tuple[float, Callable[[], None]]] | None = None,
) -> list[Look]:
    """Run the capture loop for ``seconds`` of simulated time.

    ``script`` is a list of (time, action): things that happen to the game
    or the screen at that time. ``jitter_s`` moves every capture by up to
    that much either way; ``drop`` is the chance that a capture is missed.
    """
    rng = rng or random.Random(0)
    pending = sorted(script or [], key=lambda item: item[0])
    period = 1.0 / capture_fps
    end = w.clock.now + seconds
    due = w.clock.now
    looks: list[Look] = []
    while due < end:
        at = max(w.clock.now, due + (rng.uniform(-jitter_s, jitter_s) if jitter_s else 0.0))
        while pending and pending[0][0] <= at:
            when, action = pending.pop(0)
            if game is not None:
                game.run_until(when)
            action()
        if game is not None:
            game.run_until(at)
        w.clock.now = at
        looks.append(w.look(dropped=bool(drop) and rng.random() < drop))
        due += period
    return looks
