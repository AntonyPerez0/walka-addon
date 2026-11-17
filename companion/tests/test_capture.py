"""Finding the strip on a (virtual) screen."""

from __future__ import annotations

import sys

import pytest

from walka import capture
from walka.capture import FULL_SCAN_INTERVAL_S, STRIP_HEIGHT, STRIP_WIDTH, BeaconReader, GameWindow, Shot
from walka.protocol import Flags, Frame, Stat, StatFrame
from walka_fakes import FakeScreen

FRAME = Frame(seq=10, session=2, flags=Flags.ACTIVE | Flags.MOVING, target_raw=322, char_speed_raw=350)


def frame(seq: int) -> Frame:
    return Frame(seq=seq % 256, session=2, flags=Flags.ACTIVE, target_raw=300 + seq % 50, char_speed_raw=350)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Finder:
    """A window finder whose answer the test controls."""

    def __init__(self, window: GameWindow | None = None) -> None:
        self.window = window
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self) -> GameWindow | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.window


def wow_window(left: int, top: int, width: int = 800, height: int = 90, **extra) -> GameWindow:
    return GameWindow("World of Warcraft", left, top, width, height, r"C:\Games\WoW\_forever_\WowForever.exe", **extra)


def strip_grabs(screen: FakeScreen) -> list[tuple[int, int, int, int]]:
    return [g for g in screen.grabs if (g[2], g[3]) == (STRIP_WIDTH, STRIP_HEIGHT)]


# ---- Shot --------------------------------------------------------------------------


def test_green_row_picks_the_green_channel_of_the_right_row():
    width, height = 5, 3
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes([10 + x, 100 + 10 * y + x, 200 + x, 255])  # B, G, R, A
    shot = Shot(width, height, bytes(pixels))
    assert shot.green_row(0) == bytes([100, 101, 102, 103, 104])
    assert shot.green_row(1) == bytes([110, 111, 112, 113, 114])
    assert shot.green_row(2) == bytes([120, 121, 122, 123, 124])
    assert len(shot.green_row(2)) == width


def test_fake_screen_grab_matches_what_was_painted():
    screen = FakeScreen([(0, 0, 400, 20)], background=33)
    screen.paint_strip(FRAME, 0, 0, 3)
    shot = screen.grab(0, 0, 300, 4)
    assert shot is not None and shot.width == 300 and shot.height == 4
    assert shot.green_row(0)[:6] == bytes([255, 255, 255, 0, 0, 0])
    assert shot.green_row(3)[:6] == bytes(6)  # the quiet zone below the cells
    outside = screen.grab(390, 15, 20, 10)
    assert outside is not None
    assert outside.green_row(0) == bytes([33] * 10 + [0] * 10)
    assert outside.green_row(9) == bytes(20)


# ---- where the reader looks ----------------------------------------------------------


@pytest.mark.parametrize("cell", [2, 3, 4, 5, 6, 8])
def test_finds_the_strip_at_the_game_windows_client_origin(cell: int):
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 137, 31, cell)
    finder = Finder(wow_window(137, 31))
    reader = BeaconReader(screen, finder, clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == FRAME
    assert result.where == "window" and result.origin == (137, 31)
    assert reader.locked == (137, 31)
    assert reader.window is finder.window
    assert result.decoded.cell_px == pytest.approx(cell, abs=0.4)
    assert screen.full_grabs() == 0  # no need for the slow scan


def test_finds_the_strip_at_a_monitor_origin_without_a_window_finder():
    """Borderless full-screen on the second monitor, no Win32 (or it failed)."""
    screen = FakeScreen([(0, 0, 800, 100), (800, 0, 900, 120)])
    screen.paint_strip(FRAME, 800, 0, 3)
    reader = BeaconReader(screen, None, clock=Clock())
    assert not reader.window_supported
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == FRAME
    assert result.where == "monitor" and result.origin == (800, 0)
    assert screen.full_grabs() == 0


def test_window_origin_is_tried_before_the_monitors():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 0, 0, 3)  # something else's strip at the screen corner
    other = frame(99)
    screen.paint_strip(other, 300, 50, 3)
    reader = BeaconReader(screen, Finder(wow_window(300, 50)), clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == other and result.where == "window"


def test_minimised_or_empty_windows_are_not_candidates():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 0, 0, 3)
    for window in (wow_window(500, 50, minimized=True), wow_window(500, 50, width=0)):
        screen.grabs.clear()
        reader = BeaconReader(screen, Finder(window), clock=Clock())
        result = reader.read()
        assert result.where == "monitor"
        assert (500, 50, STRIP_WIDTH, STRIP_HEIGHT) not in screen.grabs


def test_falls_back_to_a_full_scan_when_the_strip_is_somewhere_else():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 333, 47, 3)
    reader = BeaconReader(screen, None, clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == FRAME
    assert result.where == "scan"
    assert screen.full_grabs() == 1
    # Locked one row above the strip so that row 1 of the next grab is on it.
    assert reader.locked == (333, 46) and result.origin == (333, 46)

    # From now on: one small grab per read, no more scans.
    screen.grabs.clear()
    for seq in range(5):
        screen.paint_strip(frame(seq), 333, 47, 3)
        again = reader.read()
        assert again.decoded is not None and again.decoded.frame == frame(seq)
        assert again.where == "scan" and again.origin == (333, 46)
    assert screen.grabs == [(333, 46, STRIP_WIDTH, STRIP_HEIGHT)] * 5


def test_a_stat_frame_on_the_strip_keeps_the_lock():
    """Twice a second the strip shows a number of the character's journey
    instead of the live state. The reader hands it over as what it is and
    stays where it is: taking it for "no strip" would mean searching the
    whole screen again every time."""
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 333, 47, 3)
    reader = BeaconReader(screen, None, clock=Clock())
    assert reader.read().where == "scan"
    screen.grabs.clear()

    stat = StatFrame(seq=11, stat_id=Stat.FOOT_YD, value=2**40 - 1)
    screen.paint_strip(stat, 333, 47, 3)
    result = reader.read()
    assert result.decoded is not None and result.decoded.stat == stat
    assert result.decoded.frame is None  # not a live frame
    assert result.where == "scan" and result.origin == (333, 46)

    screen.paint_strip(frame(12), 333, 47, 3)
    again = reader.read()
    assert again.decoded is not None and again.decoded.frame == frame(12) and again.decoded.stat is None
    assert screen.grabs == [(333, 46, STRIP_WIDTH, STRIP_HEIGHT)] * 2
    assert screen.full_grabs() == 0


def test_the_strip_is_found_while_it_shows_a_stat_frame():
    screen = FakeScreen([(0, 0, 1000, 140)])
    stat = StatFrame(seq=200, stat_id=Stat.CHAR_KEY, value=3_129_773_777)
    screen.paint_strip(stat, 137, 31, 4)
    reader = BeaconReader(screen, Finder(wow_window(137, 31)), clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.stat == stat and result.decoded.frame is None
    assert result.where == "window" and reader.locked == (137, 31)


@pytest.mark.parametrize("cell", [2, 4, 8])
def test_full_scan_finds_any_cell_size_on_any_monitor(cell: int):
    screen = FakeScreen([(0, 0, 600, 80), (600, 0, 1000, 130)])
    screen.paint_strip(FRAME, 700, 60, cell)
    reader = BeaconReader(screen, None, clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == FRAME and result.where == "scan"
    assert reader.read().decoded is not None  # and the lock works
    assert screen.full_grabs() == 2  # one pass over both monitors, once


def test_full_scan_finds_a_dim_strip():
    """A tone-mapped / night-light screen: the strip's white is below mid-grey."""
    screen = FakeScreen([(0, 0, 1000, 100)], background=20)
    screen.paint_strip(FRAME, 410, 30, 3, white=110, black=20)
    reader = BeaconReader(screen, None, clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == FRAME and result.where == "scan"


def test_full_scan_locks_correctly_on_a_monitor_with_negative_coordinates():
    """Windows gives monitors left of / above the primary negative coordinates."""
    screen = FakeScreen([(0, 0, 800, 100), (-900, -200, 900, 150)])
    screen.paint_strip(FRAME, -640, -120, 3)
    clock = Clock()
    reader = BeaconReader(screen, None, clock=clock)
    first = reader.read()
    assert first.decoded is not None and first.where == "scan"
    assert reader.locked == (-640, -121)
    # The very next read must decode from the lock, not wait for the next scan.
    screen.grabs.clear()
    clock.now += 0.1
    second = reader.read()
    assert second.decoded is not None and second.decoded.frame == FRAME
    assert screen.full_grabs() == 0


def test_full_scan_lock_when_the_strip_is_on_the_top_row_of_a_monitor():
    screen = FakeScreen([(0, 0, 1000, 100)])
    screen.paint_strip(FRAME, 222, 0, 3)
    clock = Clock()
    reader = BeaconReader(screen, None, clock=clock)
    assert reader.read().where == "scan"
    assert reader.locked == (222, 0)
    screen.grabs.clear()
    assert reader.read().decoded is not None
    assert screen.full_grabs() == 0


def test_the_full_scan_is_rate_limited():
    screen = FakeScreen([(0, 0, 700, 60), (700, 0, 700, 60)])
    clock = Clock()
    reader = BeaconReader(screen, None, clock=clock)
    for _ in range(40):  # 4 seconds at 10 reads per second
        assert reader.read().decoded is None
        clock.now += 0.1
    assert screen.full_grabs() == 2  # both monitors, exactly once
    clock.now += FULL_SCAN_INTERVAL_S
    reader.read()
    assert screen.full_grabs() == 4
    for _ in range(20):
        clock.now += 0.1
        reader.read()
    assert screen.full_grabs() == 4


def test_a_strip_that_is_cut_off_by_the_screen_edge_is_not_decoded():
    screen = FakeScreen([(0, 0, 600, 60)])
    screen.paint_strip(FRAME, 400, 10, 3)  # 258 px wide: runs off the right edge
    reader = BeaconReader(screen, Finder(wow_window(400, 10)), clock=Clock())
    assert reader.read().decoded is None
    assert reader.locked is None


# ---- staying locked, re-acquiring ---------------------------------------------------


def test_stays_locked_and_follows_new_frames():
    screen = FakeScreen([(0, 0, 1000, 140)])
    finder = Finder(wow_window(50, 20))
    clock = Clock()
    reader = BeaconReader(screen, finder, clock=clock)
    screen.paint_strip(frame(0), 50, 20, 3)
    assert reader.read().where == "window"
    calls = finder.calls
    screen.grabs.clear()
    for seq in range(1, 30):
        clock.now += 0.07
        screen.paint_strip(frame(seq), 50, 20, 3)
        result = reader.read()
        assert result.decoded is not None and result.decoded.frame == frame(seq)
    assert len(screen.grabs) == 29  # one grab per read
    assert finder.calls == calls  # the window is not looked up while the lock holds


def test_lock_survives_a_momentary_dropout():
    screen = FakeScreen([(0, 0, 1000, 140)])
    clock = Clock()
    reader = BeaconReader(screen, Finder(wow_window(50, 20)), clock=clock)
    screen.paint_strip(FRAME, 50, 20, 3)
    assert reader.read().decoded is not None
    screen.clear()  # loading screen
    clock.now += 0.1
    assert reader.read().decoded is None
    screen.paint_strip(frame(5), 50, 20, 3)
    clock.now += 0.1
    screen.grabs.clear()
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == frame(5)
    assert len(screen.grabs) == 1  # straight from the lock


def test_reacquires_after_the_window_moves():
    screen = FakeScreen([(0, 0, 1200, 200)])
    finder = Finder(wow_window(50, 20))
    clock = Clock()
    reader = BeaconReader(screen, finder, clock=clock)
    screen.paint_strip(FRAME, 50, 20, 3)
    assert reader.read().origin == (50, 20)

    # The user drags the window.
    screen.clear()
    screen.paint_strip(frame(1), 320, 95, 3)
    finder.window = wow_window(320, 95)
    clock.now += 1.5  # the window position is re-read at most once a second
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == frame(1)
    assert result.where == "window" and result.origin == (320, 95)
    assert reader.locked == (320, 95)
    # ... and it is the new place that is polled from now on.
    screen.grabs.clear()
    assert reader.read().decoded is not None
    assert screen.grabs == [(320, 95, STRIP_WIDTH, STRIP_HEIGHT)]


def test_window_position_is_cached_for_a_second():
    screen = FakeScreen([(0, 0, 600, 60)])
    finder = Finder(wow_window(10, 10))
    clock = Clock()
    reader = BeaconReader(screen, finder, clock=clock)
    for _ in range(9):
        reader.read()
        clock.now += 0.1
    assert finder.calls == 1
    clock.now += 0.2
    reader.read()
    assert finder.calls == 2


# ---- manual origin ---------------------------------------------------------------------


def test_manual_origin_is_the_only_place_looked_at():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 0, 0, 3)  # a strip at the default place ...
    other = frame(7)
    screen.paint_strip(other, 400, 60, 3)  # ... and one where the user pointed us
    finder = Finder(wow_window(0, 0))
    reader = BeaconReader(screen, finder, manual_origin=[400, 60], clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.decoded.frame == other
    assert result.where == "manual" and result.origin == (400, 60)
    assert screen.grabs == [(400, 60, STRIP_WIDTH, STRIP_HEIGHT)]
    assert finder.calls == 0

    screen.clear()
    miss = reader.read()
    assert miss.decoded is None and miss.origin is None and miss.where == "manual"
    assert screen.full_grabs() == 0  # never scans


def test_manual_origin_zero_zero_is_honoured():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 0, 0, 3)
    reader = BeaconReader(screen, None, manual_origin=[0, 0], clock=Clock())
    assert reader.manual_origin == (0, 0)
    assert reader.read().where == "manual"


# ---- failures --------------------------------------------------------------------------


def test_survives_grab_exceptions_and_none():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 60, 30, 3)
    clock = Clock()
    reader = BeaconReader(screen, Finder(wow_window(60, 30)), clock=clock)

    screen.fail_next = 50  # every grab of this read raises
    result = reader.read()
    assert result.decoded is None and result.origin is None
    screen.fail_next = 0
    screen.none_next = 50  # ... and now returns nothing
    clock.now += 6
    assert reader.read().decoded is None
    screen.none_next = 0
    assert reader.read().decoded is not None

    # A failure while locked falls through to the other candidates and recovers.
    screen.fail_next = 1
    clock.now += 0.1
    assert reader.read().decoded is None or reader.locked == (60, 30)
    assert reader.read().decoded is not None


def test_survives_monitor_enumeration_failure():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 60, 30, 3)
    reader = BeaconReader(screen, None, clock=Clock())
    screen.fail_monitors = True
    result = reader.read()  # must not raise, whichever stage asks for the monitors
    assert result.decoded is None
    screen.fail_monitors = False
    reader2 = BeaconReader(screen, Finder(wow_window(60, 30)), clock=Clock())
    screen.fail_monitors = True
    assert reader2.read().decoded is not None  # the window origin still works


def test_survives_a_failing_window_finder():
    screen = FakeScreen([(0, 0, 1000, 140)])
    screen.paint_strip(FRAME, 0, 0, 3)
    finder = Finder()
    finder.error = OSError("EnumWindows failed")
    reader = BeaconReader(screen, finder, clock=Clock())
    result = reader.read()
    assert result.decoded is not None and result.where == "monitor"
    assert reader.window is None


def test_close_closes_the_grabber_and_swallows_errors():
    screen = FakeScreen()
    reader = BeaconReader(screen, None)
    reader.close()
    assert screen.closed

    class Broken(FakeScreen):
        def close(self) -> None:
            raise RuntimeError("already closed")

    BeaconReader(Broken(), None).close()


# ---- the game window ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,exe,expected",
    [
        ("World of Warcraft", r"C:\Program Files (x86)\World of Warcraft\_forever_\WowForever.exe", True),
        ("World of Warcraft", r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe", True),
        ("World of Warcraft", r"D:\Games\WoW\_classic_\WowClassic.exe", True),
        ("World of Warcraft", "C:/Games/WoW/_retail_/wow-64.EXE", True),
        ("World of Warcraft", "", True),  # executable unknown (no access): trust the title
        ("World of Warcraft", r"C:\Program Files\Mozilla Firefox\firefox.exe", False),
        ("World of Warcraft", r"C:\Program Files (x86)\Battle.net\Battle.net.exe", False),
        ("World of Warcraft", r"C:\Tools\wow.exe.txt", False),
        ("World of Warcraft - Mozilla Firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe", False),
        ("World of Warcraft Forums", r"C:\x\Wow.exe", False),
        ("world of warcraft", r"C:\x\Wow.exe", False),
        ("", r"C:\x\Wow.exe", False),
        ("Battle.net", r"C:\x\Wow.exe", False),
    ],
)
def test_is_wow_window(title: str, exe: str, expected: bool):
    assert capture.is_wow_window(title, exe) is expected


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_window_finder_is_absent_off_windows():
    assert capture.find_wow_window() is None
    assert capture.default_window_finder() is None
    capture.enable_dpi_awareness()  # a no-op, must not raise


def test_strip_grab_is_wide_enough_for_the_largest_cell():
    assert STRIP_WIDTH >= 86 * 8 + 8  # 8 px cells plus the allowed start offset
    assert STRIP_HEIGHT >= 2
    assert capture.ROW_ORDER[0] == 1  # row 1 is inside the strip for every cell size >= 2
    assert sorted(capture.ROW_ORDER) == list(range(STRIP_HEIGHT))


@pytest.mark.parametrize(
    ("exe", "expected"),
    [
        (r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe", True),
        (r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe", True),
        ("D:/Games/World of Warcraft/_forever_/Wow-arm64.exe", True),
        (r"C:\x\Wow.exe", False),  # not inside a game folder
        (r"C:\Program Files (x86)\World of Warcraft\_forever_\Utils\WowVoiceProxy.exe", False),
        (r"C:\Program Files (x86)\World of Warcraft\_forever_\BlizzardError.exe", False),
        (r"C:\__\Wow.exe", False),
        ("Wow.exe", False),
        ("", False),
    ],
)
def test_is_game_executable(exe: str, expected: bool):
    """The game is recognised by where its executable lives, so that it is
    found even if its window class or title is not what we expect."""
    assert capture.is_game_executable(exe) is expected


# ---- the process-id -> executable cache --------------------------------------------------

GAME_EXE = r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe"
OTHER_EXE = r"C:\Program Files\Notes\notes.exe"
GAME_CLASS = "GxWindowClassD3d"


class Processes:
    """Which executable each process id belongs to right now."""

    def __init__(self, **exes: str) -> None:
        self.exes = {int(pid.lstrip("p")): exe for pid, exe in exes.items()}
        self.asked: list[int] = []

    def query(self, pid: int) -> str:
        self.asked.append(pid)
        return self.exes.get(pid, "")


@pytest.fixture
def exe_cache(monkeypatch) -> dict[int, str]:
    cache: dict[int, str] = {}
    monkeypatch.setattr(capture, "_EXE_CACHE", cache)
    return cache


def test_executables_are_looked_up_once_per_process(exe_cache):
    processes = Processes(p100=OTHER_EXE, p200=GAME_EXE)
    for _ in range(5):
        assert capture._exe_for_window("Notes", "NotesWindow", 100, processes.query) == OTHER_EXE
        assert capture._exe_for_window("World of Warcraft", GAME_CLASS, 200, processes.query) == GAME_EXE
    assert processes.asked == [100, 200]  # not once a second for every window on the desktop


def test_the_game_is_recognised_when_it_gets_a_recycled_process_id(exe_cache):
    """A program with a window exits; hours later Windows gives its process
    id to the game. The remembered path must not make the game's window
    look like somebody else's."""
    processes = Processes(p4242=OTHER_EXE)
    assert capture._exe_for_window("Notes", "NotesWindow", 4242, processes.query) == OTHER_EXE
    processes.exes[4242] = GAME_EXE  # the same id, a new process
    for title, class_name in (("World of Warcraft", GAME_CLASS), ("World of Warcraft", ""), ("", GAME_CLASS)):
        exe_cache[4242] = OTHER_EXE
        exe = capture._exe_for_window(title, class_name, 4242, processes.query)
        assert exe == GAME_EXE and capture.is_wow_window(title, exe, class_name)
    assert exe_cache[4242] == GAME_EXE


def test_another_program_is_not_taken_for_the_game_because_of_its_recycled_process_id(exe_cache):
    """The other way round: the game exits and some other program gets its
    id. A window is taken for the game on the strength of its executable
    alone when its class and title are unknown - so that executable is
    checked afresh."""
    processes = Processes(p4242=GAME_EXE)
    assert capture._exe_for_window("", "UnknownClass", 4242, processes.query) == GAME_EXE
    processes.exes[4242] = OTHER_EXE
    exe = capture._exe_for_window("Notes", "NotesWindow", 4242, processes.query)
    assert exe == OTHER_EXE and not capture.is_game_executable(exe)
    assert exe_cache[4242] == OTHER_EXE


def test_a_window_that_only_looks_like_the_game_is_still_rejected(exe_cache):
    """Asking again does not turn a "no" into a "yes"."""
    processes = Processes(p7=OTHER_EXE)
    exe = capture._exe_for_window("World of Warcraft", "", 7, processes.query)
    assert exe == OTHER_EXE and not capture.is_wow_window("World of Warcraft", exe, "")
    # A process that cannot be opened (no path) is left alone: nothing to disagree with.
    assert capture._exe_for_window("World of Warcraft", GAME_CLASS, 8, processes.query) == ""
    assert processes.asked.count(8) == 1


def test_the_exe_cache_does_not_grow_without_bound(exe_cache):
    processes = Processes()
    for pid in range(1, 2000):
        capture._exe_for_window("", "", pid, processes.query)
    assert len(exe_cache) <= 513


@pytest.mark.skipif(sys.platform != "win32", reason="walks the windows of a real desktop")
def test_the_window_finder_runs_on_a_real_desktop(exe_cache):
    """No game on a test machine - but every visible window there is has
    been looked at, and its process asked for its executable."""
    window = capture.find_wow_window()
    assert window is None or isinstance(window, GameWindow)
    assert all(isinstance(pid, int) and isinstance(path, str) for pid, path in exe_cache.items())
    again = capture.find_wow_window()  # (this time from the cache)
    assert (again is None) == (window is None)


# ---- the mss backend ----------------------------------------------------------------------


def test_mss_grabber_uses_the_class_and_falls_back_to_the_old_factory(monkeypatch):
    """mss 10.2 deprecates ``mss.mss()`` in favour of ``mss.MSS``; either
    spelling must work, and the class is preferred when there is one."""
    import types

    made: list[str] = []

    class Capture:
        monitors = [{"left": 0, "top": 0, "width": 30, "height": 20}, {"left": 0, "top": 0, "width": 30, "height": 20}]

        def grab(self, box):
            return types.SimpleNamespace(
                width=box["width"], height=box["height"], bgra=bytes(4 * box["width"] * box["height"])
            )

        def close(self):
            made.append("closed")

    def new_style():
        made.append("MSS")
        return Capture()

    def old_style():
        made.append("mss")
        return Capture()

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(MSS=new_style, mss=old_style))
    grabber = capture.MssGrabber()
    assert made == ["MSS"]
    assert grabber.monitors() == [(0, 0, 30, 20)]
    shot = grabber.grab(1, 2, 3, 4)
    assert (shot.width, shot.height, len(shot.bgra)) == (3, 4, 48)
    grabber.close()

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=old_style))
    capture.MssGrabber().close()
    assert made == ["MSS", "closed", "mss", "closed"]


def test_the_installed_mss_has_the_class():
    """... so that the deprecated factory is not what actually gets used."""
    import mss

    assert callable(getattr(mss, "MSS", None))
