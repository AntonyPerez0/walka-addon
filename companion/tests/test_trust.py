"""Believing the strip (trust.py) and where the reader looks for it (capture.py).

One decoded frame is never enough: a place on the screen is believed only
once it has been seen ticking like the addon ticks. And where Windows can
say where the game window is, nothing but its corner is looked at.
"""

from __future__ import annotations

import random

import pytest

from strip_sim import Clock, StripGame, Watch, watch
from walka import trust
from walka.beacon import Decoded
from walka.capture import LOCK_STALL_S, STRIP_HEIGHT, STRIP_WIDTH, BeaconReader, GameWindow, ReadResult
from walka.protocol import Flags, Frame, Stat, StatFrame
from walka.sources import BeaconSource
from walka_fakes import FakeScreen

WINDOW_AT = (10, 20)


def live(seq: int, *, session: int = 4, flags: Flags = Flags.ACTIVE | Flags.MOVING) -> Frame:
    return Frame(seq=seq % 256, session=session, flags=flags, target_raw=300, char_speed_raw=350)


def stat(seq: int) -> StatFrame:
    return StatFrame(seq=seq % 256, stat_id=Stat.FOOT_YD, value=1234)


def seen(
    frame: Frame | StatFrame, *, origin: tuple[int, int] = WINDOW_AT, where: str = "window", cell: float = 4.0
) -> ReadResult:
    """What the reader hands over for a strip showing ``frame``."""
    if isinstance(frame, StatFrame):
        return ReadResult(Decoded(None, 0, cell, 255, 0, stat=frame), origin, where)
    return ReadResult(Decoded(frame, 0, cell, 255, 0), origin, where)


def believed(gate: trust.TrustGate, now: float, result: ReadResult) -> bool:
    return gate.admit(now, result).decoded is not None


def feed(gate: trust.TrustGate, frames, *, start: float = 0.0, every: float = 0.1, **where) -> list[bool]:
    return [believed(gate, start + i * every, seen(frame, **where)) for i, frame in enumerate(frames)]


# ---- the gate ----------------------------------------------------------------------------------


def test_nothing_decoded_passes_straight_through():
    gate = trust.TrustGate()
    nothing = ReadResult(None, None, "")
    assert gate.admit(0.0, nothing) is nothing
    assert gate.admit(0.0, None) is None


def test_one_frame_is_never_believed():
    assert feed(trust.TrustGate(), [live(7)]) == [False]


def test_three_frames_that_count_are_believed_at_the_game_window():
    assert feed(trust.TrustGate(), [live(7), live(8), live(9), live(10)]) == [False, False, True, True]


def test_a_place_found_by_searching_has_to_tick_for_longer():
    frames = [live(seq) for seq in range(1, 9)]
    verdicts = feed(trust.TrustGate(), frames, origin=(333, 46), where="scan")
    assert verdicts == [False] * 5 + [True] * 3
    assert trust.ADVANCES_ELSEWHERE > trust.ADVANCES_AT_WINDOW


def test_an_unbelieved_frame_looks_exactly_like_no_strip():
    gate = trust.TrustGate()
    out = gate.admit(0.0, seen(live(1)))
    assert out == ReadResult(None, None, "")


def test_a_still_picture_is_never_believed():
    gate = trust.TrustGate()
    assert not any(feed(gate, [live(42)] * 300, every=0.07))


def test_two_pictures_taking_turns_are_never_believed():
    """A slide show of two frames: to come back to the first the counter
    would have to run backwards."""
    gate = trust.TrustGate()
    for every in (0.07, 0.1, 0.5, 1.0, 2.0, 30.0):
        frames = [live(10), live(11)] * 100
        assert not any(feed(gate, frames, every=every, start=1e6 * every)), every


@pytest.mark.parametrize("step", [0, 5, 100, 250, 255])
def test_a_counter_that_stands_still_jumps_or_runs_backwards_starts_over(step: int):
    gate = trust.TrustGate()
    frames = [live(10 + i * step) for i in range(40)]
    assert not any(feed(gate, frames))


def test_a_counter_cannot_run_fast_a_little_at_a_time():
    """Four steps in a tenth of a second can be a hitch. Four steps every
    tenth of a second is not the addon."""
    gate = trust.TrustGate()
    assert not any(feed(gate, [live(4 * i) for i in range(60)]))


def test_missed_looks_and_slow_games_are_fine():
    # A look every 0.2 s sees every other redraw.
    assert feed(trust.TrustGate(), [live(2 * i) for i in range(4)], every=0.2)[-1]
    # A game at 30 fps redraws every 0.133 s; a capture every 0.066 s sees most redraws twice.
    frames = [live(i // 2) for i in range(12)]
    assert feed(trust.TrustGate(), frames, every=0.066)[-1]


def test_stat_frames_tick_like_any_other():
    gate = trust.TrustGate()
    assert feed(gate, [live(1), stat(2), live(3), stat(4)]) == [False, False, True, True]


def test_two_stories_at_once_are_not_believed():
    """Until a place is believed, its live frames must agree on the session."""
    gate = trust.TrustGate()
    frames = [live(i, session=4 if i % 2 else 9) for i in range(1, 40)]
    assert not any(feed(gate, frames))


def test_a_believed_strip_may_start_a_new_session():
    gate = trust.TrustGate()
    frames = [live(1), live(2), live(3), live(4, session=5), live(5, session=5)]
    assert feed(gate, frames) == [False, False, True, True, True]


def test_another_place_or_another_cell_size_has_to_prove_itself_again():
    gate = trust.TrustGate()
    assert feed(gate, [live(1), live(2), live(3)])[-1]
    assert not believed(gate, 0.3, seen(live(4), origin=(700, 20)))
    gate = trust.TrustGate()
    assert feed(gate, [live(1), live(2), live(3)])[-1]
    assert not believed(gate, 0.3, seen(live(4), cell=6.0))


def test_belief_is_lost_when_the_counter_stops_and_has_to_be_earned_again():
    gate = trust.TrustGate()
    assert feed(gate, [live(1), live(2), live(3)])[-1]
    # The picture freezes. It is still passed on - that is how the source
    # tells "frozen" from "gone" - but it says nothing new.
    for i in range(1, 40):
        assert believed(gate, 0.2 + i * 0.1, seen(live(3)))
    # When it moves again after the silence, it is a new chain.
    later = 0.2 + 4.0
    assert trust.STALL_S < 4.0
    assert feed(gate, [live(4), live(5), live(6), live(7)], start=later) == [False, False, True, True]


def test_a_short_gap_does_not_cost_the_belief():
    gate = trust.TrustGate()
    assert feed(gate, [live(1), live(2), live(3)])[-1]
    assert believed(gate, 0.2 + 1.0, seen(live(13)))  # one second later, ten redraws on


def test_the_counter_wraps():
    gate = trust.TrustGate()
    assert feed(gate, [live(seq) for seq in (253, 254, 255, 0, 1)]) == [False, False, True, True, True]


# ---- where the reader looks ------------------------------------------------------------------


class Finder:
    def __init__(self, window: GameWindow | None = None) -> None:
        self.window = window

    def __call__(self) -> GameWindow | None:
        return self.window


def game_window(left: int, top: int, **extra) -> GameWindow:
    return GameWindow("World of Warcraft", left, top, 800, 90, r"C:\Games\WoW\_forever_\Wow.exe", **extra)


def small_grabs(screen: FakeScreen) -> list[tuple[int, int]]:
    return [(g[0], g[1]) for g in screen.grabs if (g[2], g[3]) == (STRIP_WIDTH, STRIP_HEIGHT)]


def test_with_a_game_window_only_its_corner_is_looked_at():
    """A perfectly good strip elsewhere on the screen - somebody's video of
    the game, say - is not even seen while the game window is known."""
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(live(1), 0, 0, 4)  # in the monitor's corner
    screen.paint_strip(live(1), 300, 100, 4)  # and in the middle of the screen
    clock = Clock(100.0)
    reader = BeaconReader(screen, Finder(game_window(500, 50)), clock=clock)
    for _ in range(100):
        clock.now += 0.07
        assert reader.read().decoded is None
    assert set(small_grabs(screen)) == {(500, 50)}
    assert screen.full_grabs() == 0


def test_once_the_game_has_been_seen_a_missing_window_means_no_strip():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(live(1), 500, 50, 4)
    clock = Clock(100.0)
    finder = Finder(game_window(500, 50))
    reader = BeaconReader(screen, finder, clock=clock)
    assert reader.read().decoded is not None
    # The game is closed. Its last picture is still "on screen" here, and so
    # is a strip in the monitor's corner: neither is read.
    finder.window = None
    screen.paint_strip(live(1), 0, 0, 4)
    # (For as long as a picture may stand still before it counts as frozen,
    # the reader does not ask where the window is - and what it then shows
    # is one unmoving frame, which nothing believes anew.)
    clock.now += LOCK_STALL_S + 0.1
    reader.read()
    screen.grabs.clear()
    for _ in range(100):
        clock.now += 0.2
        assert reader.read().decoded is None
    assert screen.full_grabs() == 0 and (0, 0) not in small_grabs(screen)


def test_without_any_way_to_find_the_window_the_screen_is_searched():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(live(1), 333, 47, 4)
    reader = BeaconReader(screen, None, clock=Clock(100.0))
    result = reader.read()
    assert result.decoded is not None and result.where == "scan"


def test_a_picture_that_does_not_tick_is_given_up_for_the_one_that_does():
    """No window finder (the search is all there is). A still strip sits in
    the monitor's corner; the game's strip is ticking further down."""
    screen = FakeScreen([(0, 0, 1000, 200)])
    screen.paint_strip(live(99), 0, 0, 4)
    clock = Clock(100.0)
    game = StripGame(screen, origin=(200, 120), start=clock.now)
    game.session, game.flags, game.target_raw = 3, Flags.ACTIVE | Flags.MOVING, 250
    w = Watch(BeaconReader(screen, None, clock=clock), BeaconSource(None, clock=clock), clock)
    looks = watch(w, game, 12.0)
    # The still picture never produced a reading ...
    assert all(look.frame is None or look.frame.session == 3 for look in looks)
    # ... and the reader left it for the real strip.
    assert looks[-1].state == "ok" and looks[-1].reading.active and looks[-1].reading.session == 3
    assert w.reader.locked is not None and w.reader.locked[1] >= 100
    assert LOCK_STALL_S <= 2.0


# ---- the real strip, end to end -------------------------------------------------------------


def rig(*, finder: bool = True, origin: tuple[int, int] = WINDOW_AT, **game_options):
    screen = FakeScreen([(0, 0, 1400, 220)])
    clock = Clock(1000.0)
    game = StripGame(screen, origin=origin, start=clock.now, **game_options)
    game.session, game.flags, game.target_raw = 6, Flags.ACTIVE | Flags.MOVING, 322
    window_finder = (lambda: game.window()) if finder else None
    w = Watch(BeaconReader(screen, window_finder, clock=clock), BeaconSource(None, clock=clock), clock)
    return screen, clock, game, w


def first_ok(looks) -> float | None:
    start = looks[0].at
    for look in looks:
        if look.state == "ok" and look.reading.active:
            return look.at - start
    return None


@pytest.mark.parametrize("cell", [2, 3, 4, 6, 8])
@pytest.mark.parametrize("capture_fps", [10.0, 15.0, 30.0, 60.0])
def test_the_real_strip_is_believed_within_half_a_second(cell: int, capture_fps: float):
    _screen, _clock, game, w = rig(cell=cell)
    looks = watch(w, game, 3.0, capture_fps=capture_fps)
    took = first_ok(looks)
    assert took is not None and took <= 0.5, took
    # ... and stays believed: every look after that has a fresh reading.
    after = [look for look in looks if look.at - looks[0].at > took]
    assert all(look.reading.active and look.reading.age_s <= 0.35 for look in after)
    assert w.stats, "stat frames are delivered too"


@pytest.mark.parametrize("game_fps", [20.0, 30.0, 144.0])
def test_slow_and_fast_games(game_fps: float):
    _screen, _clock, game, w = rig(game_fps=game_fps)
    looks = watch(w, game, 3.0)
    took = first_ok(looks)
    assert took is not None and took <= 0.7, took


def test_a_slow_jittery_capture_loop_with_missed_captures():
    _screen, _clock, game, w = rig()
    looks = watch(w, game, 20.0, capture_fps=8.0, jitter_s=0.03, drop=0.2, rng=random.Random(5))
    took = first_ok(looks)
    assert took is not None and took <= 1.5, took
    good = [look for look in looks if look.at - looks[0].at > took and look.reading.active and look.reading.age_s < 1.0]
    assert len(good) > 0.9 * len([look for look in looks if look.at - looks[0].at > took])


def test_a_game_that_never_ticks_is_never_believed():
    """The strip is there but the counter stands (a frozen client from the
    start, a screenshot of the game shown in its place)."""
    screen, clock, game, w = rig()
    game.run_until(clock.now)  # one redraw, then nothing
    looks = watch(w, None, 20.0)
    assert all(look.frame is None and not look.reading.active for look in looks)
    assert w.source.status.frames == 0 and not w.stats


def test_loading_screens_alt_tab_and_reload():
    _screen, clock, game, w = rig()
    start = clock.now
    script = [
        (start + 3.0, game.hide),  # a loading screen ...
        (start + 8.0, game.show),  # ... of five seconds
        (start + 12.0, lambda: game.cover(seed=3)),  # another window over the corner
        (start + 15.0, game.uncover),
        (start + 18.0, game.reload),  # /reload: the counter starts over
    ]
    looks = watch(w, game, 24.0, script=script)

    def state_at(t: float):
        return next(look for look in looks if look.at >= start + t)

    assert state_at(2.5).state == "ok"
    assert not state_at(7.5).reading.active or state_at(7.5).reading.age_s > 3.0  # signal gone
    assert state_at(8.6).state == "ok" and state_at(8.6).reading.age_s < 0.4  # back within half a second or so
    assert state_at(14.5).reading.age_s > 2.0  # covered: nothing new, and the text over it is not a strip
    assert state_at(15.6).state == "ok"
    assert state_at(18.7).state == "ok" and state_at(18.7).reading.age_s < 0.4  # the restart costs a moment, no more
    assert looks[-1].state == "ok"
    # Nothing but the game's own frames ever got through.
    drawn = {frame for _at, frame in game.drawn if isinstance(frame, Frame)}
    assert all(look.frame is None or look.frame in drawn for look in looks)


def test_the_window_is_moved():
    _screen, clock, game, w = rig()
    start = clock.now
    looks = watch(w, game, 8.0, script=[(start + 3.0, lambda: game.move((400, 90)))])
    moved = [look for look in looks if look.at > start + 3.0]
    back = next((look.at - (start + 3.0) for look in moved if look.state == "ok" and look.reading.age_s < 0.3), None)
    assert back is not None and back <= 1.6, back  # the window is looked up once a second


def test_a_new_session_and_a_pause_are_followed_at_once():
    _screen, clock, game, w = rig()
    start = clock.now

    def new_session():
        game.session, game.target_raw = 7, 150

    def pause():
        game.flags = Flags.ACTIVE | Flags.PAUSED

    looks = watch(w, game, 8.0, script=[(start + 3.0, new_session), (start + 5.0, pause)])

    def at(t: float):
        return next(look for look in looks if look.at >= start + t)

    assert at(2.9).reading.session == 6
    assert at(3.35).reading.session == 7
    assert not at(4.9).reading.paused and at(5.35).reading.paused


# ---- things that are not the strip ------------------------------------------------------------


def text_like_row(rng: random.Random, width: int, dark: int, bright: int) -> bytes:
    values = bytearray()
    while len(values) < width:
        values += bytes([rng.choice((dark, bright))]) * rng.randint(1, 7)
    return bytes(values[:width])


@pytest.mark.parametrize("finder", [True, False])
def test_screens_full_of_text_like_patterns_never_produce_a_reading(finder: bool):
    """Rows of light and dark runs, a new screenful every few looks, some of
    it scrolling - with and without a game window to anchor the search."""
    rng = random.Random(1234 + finder)
    screen = FakeScreen([(0, 0, 900, 160)], background=30)
    clock = Clock(50.0)
    window_finder = (lambda: game_window(0, 0)) if finder else None
    w = Watch(BeaconReader(screen, window_finder, clock=clock), BeaconSource(None, clock=clock), clock)
    rows = [text_like_row(rng, 900, 30, 212) for _ in range(160)]
    for screenful in range(120):
        if screenful % 3 == 0:
            rows = [text_like_row(rng, 900, 30, 212) for _ in range(160)]
        else:
            rows = rows[1:] + [text_like_row(rng, 900, 30, 212)]  # scrolls by one row
        for y, row in enumerate(rows):
            screen.paint_row(0, y, row)
        for _ in range(3):
            clock.now += 1 / 15
            look = w.look()
            assert look.frame is None and not look.reading.active
    assert w.source.status.frames == 0 and not w.stats
