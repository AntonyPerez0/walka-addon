"""The beacon source: staleness, the game-closed rule, threading."""

from __future__ import annotations

import json
import math
import threading
import time

import pytest

from walka import trust
from walka.beacon import Decoded
from walka.capture import GameWindow, ReadResult
from walka.protocol import Flags, Frame
from walka.sources import WINDOW_GONE_S, BeaconSource, BeaconStatus, Reading

WINDOW = GameWindow("World of Warcraft", 10, 20, 1280, 720, r"C:\WoW\_forever_\WowForever.exe")


@pytest.fixture(autouse=True)
def believed(monkeypatch):
    """These tests are about what the source does with frames it believes.
    Whether a frame read off the screen is believed at all is trust.py's
    business and has its own tests (test_trust.py), so here the gate lets
    everything through."""
    monkeypatch.setattr(trust.TrustGate, "admit", lambda self, now, result: result)


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now


def frame(seq: int, *, flags: Flags = Flags.ACTIVE | Flags.MOVING, target: int = 322, session: int = 4) -> Frame:
    return Frame(seq=seq % 256, session=session, flags=flags, target_raw=target, char_speed_raw=350)


def result_for(f: Frame | None) -> ReadResult:
    if f is None:
        return ReadResult(None, None, "")
    return ReadResult(Decoded(f, 0, 3.0, 250, 4), (10, 20), "window")


class FakeReader:
    """Stands in for capture.BeaconReader."""

    def __init__(self) -> None:
        self.window: GameWindow | None = None
        self.seq = 0
        self.reads = 0
        self.closed = False
        self.fail = False
        self.blank = False
        self.delay = 0.0

    def read(self) -> ReadResult:
        self.reads += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise OSError("capture exploded")
        if self.blank:
            return result_for(None)
        self.seq += 1
        return result_for(frame(self.seq))

    def close(self) -> None:
        self.closed = True


# ---- readings ----------------------------------------------------------------------


def test_no_frame_yet_is_a_stale_inactive_reading():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    reading = source.reading()
    assert reading == Reading()
    assert math.isinf(reading.age_s) and not reading.active
    assert source.status.state == "no_signal"
    assert source.frame is None and source.window is None


@pytest.mark.parametrize(
    "flags,expected",
    [
        (Flags(0), dict(active=False, paused=False, moving=False)),
        (Flags.ACTIVE, dict(active=True, paused=False, moving=False)),
        (Flags.ACTIVE | Flags.MOVING, dict(active=True, paused=False, moving=True)),
        (Flags.ACTIVE | Flags.PAUSED, dict(active=True, paused=True, moving=False)),
        (Flags.ACTIVE | Flags.PAUSED | Flags.MOVING, dict(active=True, paused=True, moving=True)),
        (Flags.MOVING | Flags.MOUNTED | Flags.COMBAT, dict(active=False, paused=False, moving=True)),
        (Flags.PAUSED, dict(active=False, paused=True, moving=False)),
        # The display-only flags do not leak into the three that say what the session is doing.
        (
            Flags.MOUNTED | Flags.TAXI | Flags.SWIMMING | Flags.FLYING | Flags.DEAD | Flags.COMBAT | Flags.FALLING,
            dict(active=False, paused=False, moving=False),
        ),
        (Flags.RESTING | Flags.ESTIMATED | Flags(0xE000), dict(active=False, paused=False, moving=False)),
    ],
)
def test_reading_fields_come_from_the_frame(flags: Flags, expected: dict):
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(Frame(seq=1, session=9, flags=flags, target_raw=483, char_speed_raw=700))
    reading = source.reading()
    assert reading.age_s == 0.0
    assert not hasattr(reading, "target_kmh")  # what the frame asks of a belt is not passed on
    assert reading.session == 9
    for name, value in expected.items():
        assert getattr(reading, name) is value, name
    assert source.status.state == "ok"
    assert source.frame is not None and source.frame.flags == flags


def test_afk_flag_reaches_the_reading():
    """The addon pauses a session by itself when the player goes AFK; the
    reading says which kind of pause it is."""
    source = BeaconSource(None, clock=Clock())
    source.inject(frame(1, flags=Flags.ACTIVE | Flags.PAUSED | Flags.AFK))
    assert source.reading().afk is True
    source.inject(frame(2, flags=Flags.ACTIVE | Flags.PAUSED))
    assert source.reading().afk is False
    assert Reading().afk is False


def test_a_frozen_picture_goes_stale_although_frames_keep_arriving():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(frame(10))
    stuck = frame(11)
    states = []
    for _ in range(60):  # 6 s of the very same frame, decoded ten times a second
        clock.now += 0.1
        source.inject(stuck)
        states.append((source.reading().age_s, source.status.state))
    ages = [age for age, _ in states]
    assert ages == sorted(ages)  # the age only grows
    assert ages[-1] == pytest.approx(5.9, abs=0.01)
    assert states[10][1] == "ok"  # up to 2 s it may be a slow frame
    assert states[-1][1] == "frozen"
    assert "not updating" in source.status.detail
    assert source.status.frames == 61  # they did keep arriving
    # The frame's content is still reported, but as old.
    reading = source.reading()
    assert reading.active and reading.age_s > 3.0

    # The heartbeat moves again: fresh at once.
    clock.now += 0.1
    source.inject(frame(12))
    assert source.reading().age_s == 0.0
    assert source.status.state == "ok"


def test_same_seq_with_new_content_is_still_stale():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(frame(5, target=300))
    clock.now += 4.0
    source.inject(frame(5, target=900))
    reading = source.reading()
    assert reading.age_s == pytest.approx(4.0)


def test_seq_wrapping_counts_as_advancing():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    for seq in (254, 255, 0, 1):
        clock.now += 0.1
        source.inject(frame(seq))
        assert source.reading().age_s == 0.0


def test_frames_stopping_is_no_signal():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(frame(1))
    clock.now += 1.9
    assert source.reading().age_s == pytest.approx(1.9) and source.status.state == "ok"
    clock.now += 0.5
    assert source.status.state == "ok"  # (not re-evaluated until asked)
    reading = source.reading()
    assert reading.age_s == pytest.approx(2.4) and source.status.state == "no_signal"
    assert reading.active  # the app decides what a stale "active" means
    clock.now += 3600
    assert source.reading().age_s == pytest.approx(3602.4)


def test_explicit_now_overrides_the_clock():
    clock = Clock()
    source = BeaconSource(None, clock=clock)
    source.inject(frame(1), now=100.0)
    assert source.reading(now=101.5).age_s == pytest.approx(1.5)


def test_status_is_json_serialisable():
    source = BeaconSource(None, clock=Clock())
    json.dumps(source.status.to_dict())
    reader = FakeReader()
    reader.window = WINDOW
    source._ingest(1.0, reader, result_for(frame(1)))
    source.reading(1.0)
    data = source.status.to_dict()
    json.dumps(data)
    assert data["state"] == "ok" and data["frames"] == 1
    assert data["window"] == "World of Warcraft" and data["exe_path"].endswith("WowForever.exe")
    assert data["where"] == "window" and data["origin"] == [10, 20]
    assert data["cell_px"] == 3.0 and data["contrast"] == 246
    assert BeaconStatus().to_dict()["age_s"] is None


# ---- "the game window closed" ----------------------------------------------------------


def ingest(source: BeaconSource, reader: FakeReader, now: float, f: Frame | None) -> None:
    source._ingest(now, reader, result_for(f))


def test_game_window_closing_ends_the_session():
    source = BeaconSource(None, clock=Clock())
    reader = FakeReader()
    reader.window = WINDOW
    t = 1000.0
    for i in range(20):
        t += 0.1
        ingest(source, reader, t, frame(i))
    assert source.reading(t).active
    assert source.window == WINDOW

    # The game is closed: no window, no frames.
    reader.window = None
    closed_at = t
    while t < closed_at + WINDOW_GONE_S - 0.2:
        t += 0.1
        ingest(source, reader, t, None)
        reading = source.reading(t)
        # Until then it is just a lost signal: still "active", only old.
        assert reading.active and reading.age_s == pytest.approx(t - closed_at)
    t = closed_at + WINDOW_GONE_S
    ingest(source, reader, t, None)
    reading = source.reading(t)
    assert reading == Reading(age_s=0.0, active=False)  # a *fresh* "no session": the walk is filed
    assert source.status.state == "game_closed"
    # It is a state, not a moment: said again on every reading.
    for _ in range(50):
        t += 60.0
        ingest(source, reader, t, None)
        assert source.reading(t) == Reading(age_s=0.0, active=False)
        assert source.status.state == "game_closed"
    assert source.frame is None
    # ... until the game is seen again.
    reader.window = WINDOW
    t += 1.0
    ingest(source, reader, t, frame(77))
    assert source.reading(t).active and source.status.state == "ok"


def test_game_closed_rule_needs_a_window_finder_that_has_proven_itself():
    """Where the window finder never worked (borderless on Linux, a Win32
    oddity) a missing window means nothing: it must stay a plain signal loss
    (hold, slow, pause) and not end the session."""
    source = BeaconSource(None, clock=Clock())
    reader = FakeReader()  # reader.window stays None throughout
    t = 1000.0
    for i in range(20):
        t += 0.1
        ingest(source, reader, t, frame(i))
    for _ in range(600):
        t += 0.1
        ingest(source, reader, t, None)
        reading = source.reading(t)
        assert reading.active and reading.age_s > 0
        assert source.status.state != "game_closed"


def test_game_closed_rule_needs_an_active_frame():
    source = BeaconSource(None, clock=Clock())
    reader = FakeReader()
    reader.window = WINDOW
    t = 1000.0
    for i in range(20):
        t += 0.1
        ingest(source, reader, t, frame(i, flags=Flags.MOVING))  # no session running
    reader.window = None
    for _ in range(300):
        t += 0.1
        ingest(source, reader, t, None)
        reading = source.reading(t)
        assert source.status.state != "game_closed"
        assert not reading.active and reading.age_s > 0


def test_minimised_game_is_a_signal_loss_not_a_closed_game():
    source = BeaconSource(None, clock=Clock())
    reader = FakeReader()
    reader.window = WINDOW
    t = 1000.0
    for i in range(20):
        t += 0.1
        ingest(source, reader, t, frame(i))
    # Minimised: the window still exists, the strip cannot be seen.
    for _ in range(300):
        t += 0.1
        ingest(source, reader, t, None)
        assert source.reading(t).active
    assert source.status.state == "no_signal"


def test_game_restart_after_close_is_picked_up():
    source = BeaconSource(None, clock=Clock())
    reader = FakeReader()
    reader.window = WINDOW
    t = 1000.0
    ingest(source, reader, t, frame(1))
    source.reading(t)
    reader.window = None
    t += WINDOW_GONE_S + 1
    ingest(source, reader, t, None)
    assert source.reading(t) == Reading(age_s=0.0, active=False)
    # The game comes back, logged in, but no session is running.
    reader.window = WINDOW
    t += 60
    ingest(source, reader, t, frame(1, flags=Flags(0), session=0))
    assert not source.reading(t).active
    reader.window = None
    t += WINDOW_GONE_S + 1
    ingest(source, reader, t, None)
    source.reading(t)
    assert source.status.state != "game_closed"  # nothing was active, nothing to end


# ---- the reader thread -------------------------------------------------------------------


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def beacon_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "walka-beacon")


def test_thread_reads_frames_and_stops_cleanly():
    reader = FakeReader()
    reader.window = WINDOW
    source = BeaconSource(lambda: reader, fps=200)
    assert beacon_threads() == 0
    source.start()
    try:
        assert wait_for(lambda: source.status.frames >= 5)
        reading = source.reading()
        assert reading.active and reading.age_s < 1.0
        assert source.window == WINDOW
        assert beacon_threads() == 1
        source.start()  # starting twice does not start a second thread
        assert beacon_threads() == 1
    finally:
        source.stop()
    assert beacon_threads() == 0
    assert reader.closed
    reads = reader.reads
    time.sleep(0.05)
    assert reader.reads == reads  # really stopped


def test_repeated_start_stop_does_not_leak_threads_or_hang():
    readers: list[FakeReader] = []

    def factory() -> FakeReader:
        readers.append(FakeReader())
        return readers[-1]

    source = BeaconSource(factory, fps=100)
    before = threading.active_count()
    started = time.monotonic()
    for _ in range(25):
        source.start()
        source.stop()
    assert time.monotonic() - started < 5.0
    assert wait_for(lambda: threading.active_count() == before)
    assert beacon_threads() == 0
    assert wait_for(lambda: all(r.closed for r in readers))
    source.stop()  # stopping when stopped is fine


def test_stop_does_not_wait_for_the_frame_period():
    reader = FakeReader()
    source = BeaconSource(lambda: reader, fps=1.0)  # one read per second
    source.start()
    assert wait_for(lambda: reader.reads >= 1)
    started = time.monotonic()
    source.stop()
    assert time.monotonic() - started < 0.5
    assert beacon_threads() == 0


def test_without_a_reader_factory_no_thread_is_started():
    source = BeaconSource(None)
    source.start()
    assert beacon_threads() == 0
    source.stop()


def test_reader_factory_failure_is_reported_not_raised():
    def factory():
        raise RuntimeError("no display")

    source = BeaconSource(factory)
    source.start()
    assert wait_for(lambda: source.status.state == "error")
    assert "no display" in source.status.detail
    assert source.reading() == Reading()
    assert source.status.state == "error"  # not papered over by "no signal"
    source.stop()
    assert beacon_threads() == 0


def test_read_errors_do_not_kill_the_thread():
    reader = FakeReader()
    reader.fail = True
    source = BeaconSource(lambda: reader, fps=200)
    source.start()
    try:
        assert wait_for(lambda: reader.reads >= 5)
        assert source.reading() == Reading()
        reader.fail = False
        assert wait_for(lambda: source.status.frames >= 3)
        assert source.reading().active
        reader.blank = True
        time.sleep(0.05)
        assert beacon_threads() == 1
    finally:
        source.stop()
    assert reader.closed
