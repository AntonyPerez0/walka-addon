"""macOS: which window is the game, and what the system is asked. The
choosing is plain logic and is tested everywhere; the asking is tested on a
Mac, against whatever is on its screen."""

from __future__ import annotations

import os
import sys

import pytest

from walka import capture, macos
from walka.capture import GameWindow
from walka.macos import TITLE_BAR_SLACK, WindowInfo

RETAIL = "/Applications/World of Warcraft/_retail_/World of Warcraft.app/Contents/MacOS/World of Warcraft"
CLASSIC = (
    "/Applications/World of Warcraft/_classic_/World of Warcraft Classic.app/Contents/MacOS/World of Warcraft Classic"
)
FOREVER = "/Applications/World of Warcraft/_forever_/World of Warcraft.app/Contents/MacOS/World of Warcraft"
SAFARI = "/Applications/Safari.app/Contents/MacOS/Safari"


@pytest.mark.parametrize(
    "exe,expected",
    [
        (RETAIL, True),
        (CLASSIC, True),
        (FOREVER, True),
        ("/Users/me/Games/WoW/_forever_/WowForever.app/Contents/MacOS/WowForever", True),
        ("/Volumes/Games/World of Warcraft/_retail_/World of Warcraft.app/Contents/MacOS/World of Warcraft", True),
        # The launcher of old: the game's name, but not in a game folder.
        (
            "/Applications/World of Warcraft/World of Warcraft Launcher.app/Contents/MacOS/World of Warcraft Launcher",
            False,
        ),
        ("/Applications/World of Warcraft.app/Contents/MacOS/World of Warcraft", False),
        ("/Applications/Battle.net.app/Contents/MacOS/Battle.net", False),
        ("/Applications/World of Warcraft/_retail_/Utilities/Repair.app/Contents/MacOS/Repair", False),
        (SAFARI, False),
        ("/Applications/World of Warcraft/_retail_/World of Warcraft", False),  # not an application
        ("/_retail_/x.app/World of Warcraft", True),
        ("World of Warcraft", False),
        ("", False),
        # (A Windows path is not a Mac program.)
        (r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe", False),
    ],
)
def test_is_game_executable(exe: str, expected: bool):
    assert macos.is_game_executable(exe) is expected


@pytest.mark.parametrize(
    "owner,exe,expected",
    [
        ("World of Warcraft", RETAIL, True),
        ("Anything at all", RETAIL, True),  # the executable decides, whatever the application calls itself
        ("World of Warcraft", SAFARI, False),
        ("World of Warcraft", "", True),  # executable unknown: by name
        ("World of Warcraft Classic", "", True),
        ("World of Warcraft Launcher", "", False),
        ("Safari", "", False),
        ("", "", False),
    ],
)
def test_is_wow_app(owner: str, exe: str, expected: bool):
    assert macos.is_wow_app(owner, exe) is expected


def window(owner: str, pid: int, left=0, top=25, width=1280, height=748, **extra) -> WindowInfo:
    values = {"title": "", "layer": 0, "on_screen": True, **extra}
    return WindowInfo(owner=owner, pid=pid, left=left, top=top, width=width, height=height, **values)


class Processes:
    def __init__(self, paths: dict[int, str]) -> None:
        self.paths = paths
        self.asked: list[int] = []

    def __call__(self, pid: int) -> str:
        self.asked.append(pid)
        return self.paths.get(pid, "")


def test_the_game_is_picked_out_of_everything_on_screen():
    processes = Processes({10: SAFARI, 20: RETAIL, 30: "/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock"})
    windows = [
        window("Window Server", 1, layer=25, height=24),  # the menu bar
        window("Dock", 30, layer=20, top=800, height=100),
        window("Safari", 10, title="World of Warcraft - Wowhead"),  # a web page about the game
        window("Safari", 10, left=40, top=60),
        window("World of Warcraft", 20, left=100, top=50, width=1600, height=928, title="World of Warcraft"),
        window("World of Warcraft", 20, left=0, top=0, width=200, height=40),  # a helper window of the game
    ]
    found = macos.pick_game_window(windows, processes)
    assert found == GameWindow(
        "World of Warcraft", 100, 50, 1600, 928, RETAIL, minimized=False, top_slack=TITLE_BAR_SLACK
    )
    # One question per program, and none about what is not an ordinary window.
    assert sorted(processes.asked) == [10, 20]


def test_no_game_no_window():
    processes = Processes({10: SAFARI})
    assert macos.pick_game_window([window("Safari", 10)], processes) is None
    assert macos.pick_game_window([], processes) is None


def test_without_the_screen_recording_permission_the_window_has_no_title():
    """macOS then leaves window titles out: the application's name stands in."""
    found = macos.pick_game_window([window("World of Warcraft", 20, title="")], Processes({20: RETAIL}))
    assert found is not None and found.title == "World of Warcraft"


def test_a_game_that_is_minimised_or_on_another_desktop_is_known_but_not_on_screen():
    found = macos.pick_game_window([window("World of Warcraft", 20, on_screen=False)], Processes({20: RETAIL}))
    assert found is not None and found.minimized
    # ... which the reader takes to mean: there is no strip to look for.
    from walka.capture import BeaconReader
    from walka_fakes import FakeScreen

    screen = FakeScreen([(0, 0, 1000, 140)])
    reader = BeaconReader(screen, lambda: found)
    assert reader.read().decoded is None and screen.grabs == []


def test_the_visible_window_wins_then_the_preferred_game_then_the_largest():
    processes = Processes({20: RETAIL, 21: FOREVER, 22: CLASSIC})
    hidden = window("World of Warcraft", 21, width=3000, height=2000, on_screen=False)
    retail = window("World of Warcraft", 20, width=1600, height=900)
    classic = window("World of Warcraft Classic", 22, width=1920, height=1080)
    forever = window("World of Warcraft", 21, width=800, height=600)
    assert macos.pick_game_window([hidden, retail, classic], processes).exe_path == CLASSIC  # the largest on screen
    assert macos.pick_game_window([hidden, retail, classic, forever], processes).exe_path == FOREVER
    assert macos.pick_game_window([hidden], processes).minimized


def test_a_process_that_cannot_be_asked_does_not_stop_the_search():
    def exe_of(pid: int) -> str:
        if pid == 10:
            raise OSError("no such process")
        return RETAIL

    windows = [window("Safari", 10), window("World of Warcraft", 10), window("World of Warcraft", 20, left=7)]
    found = macos.pick_game_window(windows, exe_of)
    # (The one whose executable could not be read is taken by its name.)
    assert found is not None and found.left in (0, 7)
    assert macos.pick_game_window([window("Safari", 10)], exe_of) is None


def test_process_ids_are_not_remembered_between_searches():
    """A process id is handed on to the next program: what ran under it a
    second ago says nothing."""
    processes = Processes({20: RETAIL})
    windows = [window("World of Warcraft", 20)]
    assert macos.pick_game_window(windows, processes) is not None
    processes.paths[20] = SAFARI  # the game quit, and Safari got its number
    assert macos.pick_game_window(windows, processes) is None
    assert processes.asked == [20, 20]


def test_the_strip_is_looked_for_as_far_down_as_a_title_bar_or_a_notch_can_reach():
    assert TITLE_BAR_SLACK >= 38  # the tallest: full screen under a MacBook's camera notch
    assert TITLE_BAR_SLACK <= 64  # ... and not much further: only the window's corner counts


# ---- asking the system -------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "darwin", reason="what happens off a Mac")
def test_off_a_mac_there_is_nothing_to_ask():
    assert macos.find_wow_window() is None
    assert macos.screen_recording_allowed() is None
    macos.request_screen_recording()  # does nothing, and must not raise
    assert macos.executable_path(os.getpid()) == ""
    with pytest.raises(OSError):
        macos.list_windows()
    capture.require_screen_access()


@pytest.mark.skipif(sys.platform != "darwin", reason="asks a real Mac")
def test_a_real_mac_answers():
    """No game on a test machine - but its window list has been read, entry
    by entry, and the question about the permission has an answer."""
    everything = macos.list_windows()
    shown = macos.list_windows(on_screen_only=True)
    print(f"{len(everything)} windows, {len(shown)} on screen: {sorted({w.owner for w in everything})}")
    assert isinstance(everything, list) and len(shown) <= len(everything)
    for entry in everything:
        assert isinstance(entry, WindowInfo)
        assert isinstance(entry.owner, str) and isinstance(entry.title, str)
        assert entry.pid > 0 and entry.width >= 0 and entry.height >= 0
    assert all(entry.on_screen for entry in shown)

    mine = macos.executable_path(os.getpid())
    assert mine and os.path.exists(mine) and os.path.samefile(mine, os.path.realpath(sys.executable))
    assert macos.executable_path(2**22 + 12345) == ""  # (process ids stop well below this)
    # Every process that has a window can be asked what it runs.
    paths = {entry.pid: macos.executable_path(entry.pid) for entry in everything}
    assert all(isinstance(path, str) for path in paths.values())

    found = macos.find_wow_window()
    assert found is None or isinstance(found, GameWindow)
    assert macos.screen_recording_allowed() in (True, False)
    assert capture.default_window_finder() is macos.find_wow_window
