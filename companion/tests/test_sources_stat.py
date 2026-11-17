"""Stat frames at the beacon source: handed to ``on_stat``, invisible to the belt.

The rule (docs/PROTOCOL.md, "Stat frames"): only live frames feed the
governor's reading. A stat frame does not make the reading fresh, does not
count for the frozen-picture check and does not prove that the game is still
running - to the belt it is as if nothing had been decoded at that moment.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time

import pytest

from walka import stat_route
from walka.beacon import Decoded
from walka.capture import GameWindow, ReadResult
from walka.governor import Reading
from walka.protocol import STAT_CYCLE, STAT_EVERY, Flags, Frame, Stat, StatFrame
from walka.sources import WINDOW_GONE_S, BeaconSource

WINDOW = GameWindow("World of Warcraft", 10, 20, 1280, 720, r"C:\WoW\_forever_\WowForever.exe")


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


class Reader:
    """Stands in for capture.BeaconReader where frames are fed by hand."""

    def __init__(self, window: GameWindow | None = None) -> None:
        self.window = window


def live(seq: int, *, flags: Flags = Flags.ACTIVE | Flags.MOVING, target: int = 322) -> Frame:
    return Frame(seq=seq % 256, session=4, flags=flags, target_raw=target, char_speed_raw=350)


def stat(seq: int, stat_id: int = Stat.FOOT_YD, value: int = 1000) -> StatFrame:
    return StatFrame(seq=seq % 256, stat_id=stat_id, value=value)


def result_for(frame: Frame | StatFrame | None) -> ReadResult:
    """What the screen reader returns for a strip showing ``frame`` (None: no strip)."""
    if frame is None:
        return ReadResult(None, None, "")
    if isinstance(frame, StatFrame):
        return ReadResult(Decoded(None, 0, 3.0, 250, 4, stat=frame), (10, 20), "window")
    return ReadResult(Decoded(frame, 0, 3.0, 250, 4), (10, 20), "window")


def collect(source: BeaconSource) -> list[tuple[int, int]]:
    """Record what the source hands to ``on_stat``."""
    received: list[tuple[int, int]] = []
    source.on_stat = lambda stat_id, value: received.append((stat_id, value))
    return received


def view(source: BeaconSource, now: float) -> tuple:
    """Everything the rest of the app can see of the live signal."""
    reading = source.reading(now)
    return reading, source.status.to_dict(), source.frame, source.window


# ---- a stat frame is not a sign of life ------------------------------------------------------


def test_a_stream_of_only_stat_frames_makes_the_reading_go_stale():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    received = collect(source)
    source.inject(live(10))
    assert source.reading().age_s == 0.0 and source.status.state == "ok"

    # Six seconds of stat frames, ten a second, each with a new seq: valid
    # frames keep arriving and the counter keeps moving - but none is live.
    started = clock.now
    for tenth in range(1, 61):
        clock.now = started + tenth / 10
        source.inject(stat(10 + tenth))
        reading = source.reading()
        assert reading.age_s == pytest.approx(tenth / 10)
        assert reading.active  # the last live frame's content, reported as old
    assert source.status.state == "no_signal"  # not "ok", and not merely "frozen"
    assert source.status.frames == 1
    assert source.frame == live(10)
    assert len(received) == 60

    # The next live frame is news again.
    clock.now += 0.1
    source.inject(live(72))
    assert source.reading().age_s == 0.0 and source.status.state == "ok"


def test_the_same_through_the_screen_reader():
    source = BeaconSource(None, clock=Clock())
    received = collect(source)
    reader = Reader(WINDOW)
    t = 1000.0
    source._ingest(t, reader, result_for(live(1)))
    source.reading(t)
    before = source.status.to_dict()
    last_live = t
    for i in range(40):
        t += 0.1
        source._ingest(t, reader, result_for(stat(2 + i, Stat.REAL_STEPS, i)))
        assert source.reading(t).age_s == pytest.approx(t - last_live)
    assert source.status.state == "no_signal"
    assert source.frame == live(1)
    after = source.status.to_dict()
    # Nothing about the live signal moved, only its age and what that means.
    for key in ("frames", "where", "origin", "cell_px", "contrast", "window", "exe_path"):
        assert after[key] == before[key], key
    assert received == [(Stat.REAL_STEPS, i) for i in range(40)]


def test_only_stat_frames_from_the_start_is_no_signal_at_all():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    received = collect(source)
    reader = Reader(WINDOW)
    for i in range(30):
        clock.now += 0.1
        source._ingest(clock.now, reader, result_for(stat(i)))
        source.inject(stat(i))
        reading = source.reading()
        assert reading == Reading() and math.isinf(reading.age_s) and not reading.active
    assert source.frame is None
    assert source.status.state == "no_signal" and source.status.frames == 0
    assert source.status.age_s is None
    assert len(received) == 60


def test_a_picture_frozen_on_a_stat_frame_is_a_lost_signal():
    """The game hangs (or a loading screen freezes the picture) just while a
    stat frame is on the strip. A valid frame is decoded on every read; the
    belt must still see the signal age and go."""
    source = BeaconSource(None, clock=Clock())
    reader = Reader(WINDOW)
    t = 1000.0
    for seq in range(1, 5):
        t += 0.1
        source._ingest(t, reader, result_for(live(seq)))
    assert source.reading(t).age_s == 0.0
    frozen_at = t
    stuck = stat(5, Stat.PLAYED_S, 86_400)
    ages = []
    for tenth in range(1, 61):
        t = frozen_at + tenth / 10
        source._ingest(t, reader, result_for(stuck))
        reading = source.reading(t)
        ages.append(reading.age_s)
        assert reading.age_s == pytest.approx(tenth / 10)
    assert ages == sorted(ages) and ages[-1] == pytest.approx(6.0)
    assert source.status.state == "no_signal"


def test_stat_frames_do_not_keep_a_closed_game_open():
    """The game-closed rule ends a session when the game window has been gone
    for a while. Stat frames that still decode (a frozen picture, a
    screenshot left on screen) are no evidence that the game is running."""
    source = BeaconSource(None, clock=Clock())
    reader = Reader(WINDOW)
    t = 1000.0
    for seq in range(20):
        t += 0.1
        source._ingest(t, reader, result_for(live(seq)))
    assert source.reading(t).active

    reader.window = None  # the window is gone; only stat frames are still seen
    closed_at = t
    while t < closed_at + WINDOW_GONE_S - 0.2:
        t += 0.1
        source._ingest(t, reader, result_for(stat(20)))
        assert source.status.state != "game_closed"
        assert source.reading(t).active
    t = closed_at + WINDOW_GONE_S
    source._ingest(t, reader, result_for(stat(20)))
    assert source.reading(t) == Reading(age_s=0.0, active=False)
    assert source.status.state == "game_closed"


# ---- interleaved with live frames ------------------------------------------------------------


def addon_stream(redraws: int):
    """What the addon draws: one counter for every redraw, and every
    STAT_EVERY-th redraw a stat frame in place of the live frame."""
    cycle = 0
    for redraw in range(1, redraws + 1):
        if redraw % STAT_EVERY == 0:
            yield stat(redraw, STAT_CYCLE[cycle % len(STAT_CYCLE)], 1000 + cycle)
            cycle += 1
        else:
            yield live(redraw, target=300 + redraw % 50)


def test_interleaved_stat_frames_do_not_disturb_the_live_reading():
    source = BeaconSource(None, clock=Clock())
    received = collect(source)
    reader = Reader(WINDOW)
    t = 1000.0
    last_live: Frame | None = None
    live_count = 0
    for frame in addon_stream(600):  # a minute of play, past two wraps of the counter
        t += 0.1
        source._ingest(t, reader, result_for(frame))
        reading = source.reading(t)
        if isinstance(frame, Frame):
            last_live, live_count = frame, live_count + 1
            assert reading.age_s == 0.0
        else:
            # One redraw without news: the reading is the previous live frame, 0.1 s old.
            assert reading.age_s == pytest.approx(0.1)
        assert source.frame == last_live
        assert reading.target_kmh == pytest.approx(last_live.target_kmh)
        assert reading.active and source.status.state == "ok"
    assert live_count == 480 and source.status.frames == 480
    # Every stat arrived, in the order sent, cycle after cycle.
    assert len(received) == 120
    assert [stat_id for stat_id, _ in received[:16]] == list(STAT_CYCLE)
    assert [value for _, value in received] == list(range(1000, 1120))


def test_a_stat_frame_does_not_use_up_a_seq():
    """Freshness is "the live frame's counter moved". The counter in a stat
    frame is neither remembered nor compared."""
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(live(5))
    clock.now += 0.1
    source.inject(stat(6))
    clock.now += 0.1
    source.inject(live(6))  # same number as the stat frame before it: still an advance
    assert source.reading().age_s == 0.0

    # And the other way round: a live picture that stands still stays stale,
    # whatever the stat frames in between count.
    clock.now += 2.0
    source.inject(stat(7))
    clock.now += 2.0
    source.inject(live(6))
    assert source.reading().age_s == pytest.approx(4.0)
    assert source.status.state == "frozen"  # (the live frame was seen just now, unchanged)


@pytest.mark.parametrize("seed", range(8))
def test_to_the_belt_a_stat_frame_is_as_if_nothing_was_decoded(seed: int):
    """The whole rule in one property: feed two sources the same eventful
    minutes - live frames, frozen pictures, gaps, the game window coming and
    going - but give one of them stat frames where the other sees nothing.
    Everything the app can observe of the live signal must stay identical."""
    rng = random.Random(seed)
    with_stats = BeaconSource(None, clock=Clock())
    without = BeaconSource(None, clock=Clock())
    received = collect(with_stats)
    reader = Reader(WINDOW)
    t = 1000.0
    seq = 0
    sent = 0
    for _ in range(1500):
        t += rng.choice((0.05, 0.1, 0.1, 0.1, 0.4, 1.5))
        if rng.random() < 0.01:
            reader.window = None if reader.window is not None else WINDOW
        kind = rng.choice(("live", "live", "live", "same", "stat", "stat", "none"))
        if kind == "stat":
            seq += rng.randrange(2)  # the counter moves, or the picture is frozen on a stat frame
            sent += 1
            with_stats._ingest(t, reader, result_for(stat(seq, rng.choice(list(Stat)), sent)))
            without._ingest(t, reader, result_for(None))
        else:
            if kind == "live":
                seq += 1
            flags = rng.choice((Flags.ACTIVE | Flags.MOVING, Flags.ACTIVE, Flags.ACTIVE | Flags.PAUSED, Flags(0)))
            frame = None if kind == "none" else live(seq, flags=flags)
            with_stats._ingest(t, reader, result_for(frame))
            without._ingest(t, reader, result_for(frame))
        assert view(with_stats, t) == view(without, t)
    assert [value for _, value in received] == list(range(1, sent + 1))
    assert sent > 100


def test_injected_stat_frames_are_as_if_nothing_was_injected():
    rng = random.Random(3)
    clock_a, clock_b = Clock(), Clock()
    with_stats = BeaconSource(None, clock=clock_a)
    without = BeaconSource(None, clock=clock_b)
    seq = 0
    for _ in range(500):
        step = rng.choice((0.1, 0.1, 0.7, 2.5))
        clock_a.now += step
        clock_b.now += step
        if rng.random() < 0.4:
            with_stats.inject(stat(rng.randrange(256), rng.randrange(64), rng.getrandbits(40)))
        else:
            seq += rng.randrange(2)
            with_stats.inject(live(seq))
            without.inject(live(seq))
        assert view(with_stats, clock_a.now) == view(without, clock_b.now)


# ---- delivery ----------------------------------------------------------------------------------


def test_on_stat_gets_the_stat_and_its_value():
    source = BeaconSource(None, clock=Clock())
    assert source.on_stat is None
    received = collect(source)
    source.inject(stat(1, Stat.CHAR_KEY, 3_129_773_777))
    source._ingest(1.0, Reader(), result_for(stat(2, Stat.FOOT_YD, 2**40 - 1)))
    source.inject(stat(3, Stat.NAME_0, 0))
    assert received == [(Stat.CHAR_KEY, 3_129_773_777), (Stat.FOOT_YD, 2**40 - 1), (Stat.NAME_0, 0)]
    assert all(isinstance(stat_id, Stat) for stat_id, _ in received)


def test_stats_this_build_does_not_know_are_dropped():
    source = BeaconSource(None, clock=Clock())
    received = collect(source)
    known = {member.value for member in Stat}
    for stat_id in range(64):
        source.inject(stat(stat_id, stat_id, 5))
        source._ingest(1.0, Reader(), result_for(stat(stat_id, stat_id, 6)))
    assert [stat_id for stat_id, _ in received] == [i for i in range(64) if i in known for _ in range(2)]
    assert source.frame is None  # and none of them was taken for a live frame instead


def test_without_a_handler_stat_frames_simply_go_nowhere():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(live(1))
    clock.now += 0.3
    source.inject(stat(2))
    source._ingest(clock.now, Reader(), result_for(stat(3)))
    assert source.frame == live(1) and source.status.frames == 1
    assert source.reading().age_s == pytest.approx(0.3)


def test_a_failing_handler_cannot_hurt_the_live_signal(monkeypatch, caplog):
    monkeypatch.setattr(stat_route, "_complained", False)
    clock = Clock()
    source = BeaconSource(None, clock=clock)

    def broken(stat_id: int, value: int) -> None:
        raise RuntimeError("collector fell over")

    source.on_stat = broken
    reader = Reader(WINDOW)
    with caplog.at_level(logging.DEBUG, logger="walka.stat_route"):
        for seq in range(1, 40):
            clock.now += 0.1
            frame = stat(seq) if seq % 5 == 0 else live(seq)
            source._ingest(clock.now, reader, result_for(frame))  # must not raise
            source.inject(stat(seq))
        assert source.reading().active and source.reading().age_s == 0.0
        assert source.status.frames == 32
    # Said once, loudly enough to be noticed; not twice a second for hours.
    errors = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(errors) == 1 and "journey" in errors[0].getMessage()
    assert errors[0].exc_info is not None and "collector fell over" in str(errors[0].exc_info[1])


def test_the_handler_runs_outside_the_sources_lock():
    """A collector may well ask the source something (which game window? what
    is the live frame?). That must not deadlock the thread reading the screen."""
    source = BeaconSource(None, clock=Clock())
    seen = []

    def nosy(stat_id: int, value: int) -> None:
        seen.append((source.frame, source.window, source.reading(1000.0).active))

    source.on_stat = nosy
    source.inject(live(1))

    def feed() -> None:
        source._ingest(1000.0, Reader(WINDOW), result_for(stat(2)))
        source.inject(stat(3))

    worker = threading.Thread(target=feed, daemon=True)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "on_stat was called with the source's lock held"
    assert seen == [(live(1), None, True), (live(1), WINDOW, True)]


# ---- the reader thread --------------------------------------------------------------------------


class StreamReader:
    """A screen reader showing the addon's stream, one redraw per read."""

    def __init__(self, only_stats: bool = False) -> None:
        self.window: GameWindow | None = WINDOW
        self.redraw = 0
        self.only_stats = only_stats
        self.closed = False

    def read(self) -> ReadResult:
        self.redraw += 1
        if self.only_stats or self.redraw % STAT_EVERY == 0:
            return result_for(stat(self.redraw, STAT_CYCLE[self.redraw % len(STAT_CYCLE)], self.redraw))
        return result_for(live(self.redraw))

    def close(self) -> None:
        self.closed = True


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_the_reader_thread_delivers_stats_and_keeps_the_live_reading():
    reader = StreamReader()
    source = BeaconSource(lambda: reader, fps=200)
    received: list[tuple[int, int, str]] = []
    source.on_stat = lambda stat_id, value: received.append((stat_id, value, threading.current_thread().name))
    source.start()
    try:
        assert wait_for(lambda: len(received) >= 3 and source.status.frames >= 10)
        reading = source.reading()
        assert reading.active and reading.age_s < 1.0
        assert source.status.state == "ok"
    finally:
        source.stop()
    assert reader.closed
    # Every stat frame shown was delivered, on the reader's thread, and none was counted as live.
    assert [value for _, value, _ in received] == [n for n in range(1, reader.redraw + 1) if n % STAT_EVERY == 0]
    assert {name for _, _, name in received} == {"walka-beacon"}
    assert source.status.frames == reader.redraw - len(received)


def test_the_reader_thread_seeing_only_stat_frames_reports_no_signal():
    reader = StreamReader(only_stats=True)
    source = BeaconSource(lambda: reader, fps=200)
    received = collect(source)
    source.start()
    try:
        assert wait_for(lambda: len(received) >= 10)
        assert source.reading() == Reading()
        assert source.status.state == "no_signal" and source.status.frames == 0
        assert source.frame is None
        assert source.window == WINDOW  # the window is still tracked: that is not the frame's doing
    finally:
        source.stop()


# ---- the fork itself ------------------------------------------------------------------------------


def test_live_only_passes_live_reads_through_untouched():
    received: list[tuple[int, int]] = []
    handler = lambda stat_id, value: received.append((stat_id, value))  # noqa: E731
    for result in (None, result_for(None), result_for(live(1))):
        assert stat_route.live_only(result, handler) is result
    assert received == []


def test_live_only_turns_a_stat_read_into_no_beacon_seen():
    received: list[tuple[int, int]] = []
    result = stat_route.live_only(result_for(stat(1, Stat.WALKS, 7)), lambda *args: received.append(args))
    assert result == ReadResult(None, None, "")  # exactly what the reader returns with no strip on screen
    assert received == [(Stat.WALKS, 7)]
    assert stat_route.live_only(result_for(stat(1)), None) == ReadResult(None, None, "")


def test_take_only_takes_stat_frames():
    received: list[tuple[int, int]] = []
    handler = lambda stat_id, value: received.append((stat_id, value))  # noqa: E731
    for other in (live(1), None, object(), 5):
        assert stat_route.take(other, handler) is False
    assert received == []
    assert stat_route.take(stat(1, Stat.SINCE, 1_763_424_000), handler) is True
    assert stat_route.take(stat(1, 0, 1), handler) is True  # unknown stat: still not a live frame
    assert stat_route.take(stat(1), None) is True
    assert received == [(Stat.SINCE, 1_763_424_000)]
