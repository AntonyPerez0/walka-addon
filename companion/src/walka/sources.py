"""The game, as the companion sees it: ``BeaconSource`` reads the WoW
addon's strip off the screen in a background thread.

What it hands on is a ``Reading``: is a walking session marked in the game,
and which one. The companion uses that for one thing - to say which game
session a walk it records belongs to. Nothing read from the game reaches
the treadmill: the companion only listens to that.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import stat_route, trust
from .capture import BeaconReader, GameWindow
from .protocol import Flags, Frame

log = logging.getLogger(__name__)

# A beacon whose heartbeat counter stops advancing is a frozen picture (the
# game hung or is showing a loading screen over a stale frame), not a signal.
WINDOW_GONE_S = 10.0  # game window missing this long = the session is over


@dataclass(frozen=True)
class Reading:
    """What the game last said about its walking session."""

    age_s: float = math.inf  # seconds since the strip was last seen ticking
    active: bool = False  # a session is marked in the game
    paused: bool = False  # ... but paused there
    moving: bool = False  # the character is moving
    session: int = 0  # session counter (changes when a new session starts)
    afk: bool = False  # the player is away


@dataclass
class BeaconStatus:
    """Diagnostics for the dashboard."""

    state: str = "starting"  # "ok" | "no_signal" | "frozen" | "game_closed" | "error" | "starting"
    detail: str = ""
    window: str = ""
    exe_path: str = ""
    where: str = ""  # how the strip was found
    origin: tuple[int, int] | None = None
    cell_px: float | None = None
    contrast: int | None = None
    fps: float = 0.0
    frames: int = 0
    age_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "window": self.window,
            "exe_path": self.exe_path,
            "where": self.where,
            "origin": list(self.origin) if self.origin else None,
            "cell_px": round(self.cell_px, 2) if self.cell_px else None,
            "contrast": self.contrast,
            "fps": round(self.fps, 1),
            "frames": self.frames,
            "age_s": None if self.age_s is None else round(self.age_s, 2),
        }


class BeaconSource:
    """Reads the addon's beacon in a background thread."""

    kind = "wow"

    def __init__(
        self,
        reader_factory: Callable[[], BeaconReader] | None,
        *,
        fps: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader_factory = reader_factory
        self.fps = fps
        self.clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame: Frame | None = None
        self._fresh_at = -math.inf  # when the heartbeat last advanced
        self._seen_at = -math.inf  # when any valid frame was last decoded
        self._last_seq: int | None = None
        self._window_seen_at: float | None = None
        self._window: GameWindow | None = None
        self._finder_works = False  # the window finder has found the game at least once
        self._had_active = False
        self._closed = False  # the game was closed, and no frame has been seen since
        self.status = BeaconStatus()
        # What is read off the screen is believed only once it has been seen
        # ticking (trust.py). Frames fed with ``inject`` do not pass here.
        self._trust = trust.TrustGate()
        # Stat frames (journey numbers) go to this callback as (stat id, value)
        # and nowhere else; the live reading never sees them. See stat_route.py.
        self.on_stat: stat_route.StatHandler | None = None

    # ---- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        """Start reading the screen. Without a reader factory (tests, the
        demo) frames only arrive through ``inject``."""
        if self._reader_factory is None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="walka-beacon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        # The reader (and its screen-capture handles) must live on this thread.
        assert self._reader_factory is not None
        try:
            reader = self._reader_factory()
        except Exception as exc:
            log.error("screen capture is unavailable: %s", exc)
            with self._lock:
                self.status.state = "error"
                self.status.detail = f"screen capture is unavailable: {exc}"
            return
        period = 1.0 / max(1.0, self.fps)
        count, window_start = 0, self.clock()
        try:
            while not self._stop.is_set():
                started = self.clock()
                try:
                    result = reader.read()
                except Exception as exc:
                    log.debug("beacon read failed: %s", exc)
                    result = None
                self._ingest(started, reader, result)
                count += 1
                if started - window_start >= 2.0:
                    with self._lock:
                        self.status.fps = count / (started - window_start)
                    count, window_start = 0, started
                self._stop.wait(max(0.0, period - (self.clock() - started)))
        finally:
            reader.close()

    def _ingest(self, now: float, reader: BeaconReader, result: Any) -> None:
        result = self._trust.admit(now, result)  # one decoded frame proves nothing: see trust.py
        result = stat_route.live_only(result, self.on_stat)  # a stat frame says nothing about the session
        with self._lock:
            self._window = reader.window
            if reader.window is not None:
                self._finder_works = True
                self._window_seen_at = now
                self.status.window = reader.window.title
                self.status.exe_path = reader.window.exe_path
            decoded = result.decoded if result is not None else None
            if decoded is None:
                return
            frame = decoded.frame
            self._seen_at = now
            if frame.seq != self._last_seq:
                self._last_seq = frame.seq
                self._fresh_at = now
            self._frame = frame
            self._closed = False
            self._window_seen_at = now  # a decoded beacon proves the game is up
            status = self.status
            status.frames += 1
            status.where = result.where
            status.origin = result.origin
            status.cell_px = decoded.cell_px
            status.contrast = decoded.white - decoded.black

    def inject(self, frame: Frame, now: float | None = None) -> None:
        """Feed a frame directly (tests and the simulator)."""
        if stat_route.take(frame, self.on_stat):  # a stat frame says nothing about the session
            return
        now = self.clock() if now is None else now
        with self._lock:
            self._seen_at = now
            if frame.seq != self._last_seq:
                self._last_seq = frame.seq
                self._fresh_at = now
            self._frame = frame
            self._closed = False
            self._window_seen_at = now
            self.status.frames += 1

    # ---- output ----------------------------------------------------------------
    @property
    def frame(self) -> Frame | None:
        with self._lock:
            return self._frame

    @property
    def window(self) -> GameWindow | None:
        with self._lock:
            return self._window

    def reading(self, now: float | None = None) -> Reading:
        """What the game says about its session right now."""
        now = self.clock() if now is None else now
        with self._lock:
            frame = self._frame
            age = now - self._fresh_at
            seen_age = now - self._seen_at
            status = self.status
            status.age_s = None if math.isinf(age) else age

            if self._closed:
                # "The game was closed" is a state, not a moment: it is said
                # on every reading until a frame is seen again.
                return Reading(age_s=0.0, active=False)

            if frame is None:
                if status.state not in ("error",):
                    status.state = "no_signal"
                    status.detail = "no beacon seen yet - is the game running with the Walka addon enabled?"
                return Reading()

            if frame.active:
                self._had_active = True

            # The game window disappearing means the game was closed: that
            # ends the session rather than just losing the signal. Only
            # trusted once the window finder has proven it can see the game.
            if (
                self._finder_works
                and self._had_active
                and self._window_seen_at is not None
                and now - self._window_seen_at >= WINDOW_GONE_S
            ):
                self._had_active = False
                self._frame = None
                self._closed = True
                status.state = "game_closed"
                status.detail = "the game window closed"
                return Reading(age_s=0.0, active=False)

            if age <= 2.0:
                status.state = "ok"
                status.detail = ""
            elif seen_age <= 2.0:
                status.state = "frozen"
                status.detail = "the beacon is visible but not updating (loading screen?)"
            else:
                status.state = "no_signal"
                status.detail = "beacon not visible (game minimised, covered, or on a loading screen)"

            return Reading(
                age_s=age,
                active=frame.active,
                paused=frame.paused,
                moving=frame.has(Flags.MOVING),
                session=frame.session,
                afk=frame.has(Flags.AFK),
            )
