"""The floating window: a small Walka panel on top of the game.

Its setting (config ``overlay``), what the App does with it, the window as
pywebview is asked to make it (no window is opened here: pywebview is stood
in for), and the page it shows.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import cli, web, window
from walka import config as config_module

STATIC = Path(web.__file__).parent / "static"


def eventually(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# ---- the setting ---------------------------------------------------------------------------------


def test_the_floating_window_is_off_until_it_is_switched_on():
    overlay = config_module.Config().overlay
    assert overlay.enabled is False
    assert overlay.step_goal == 6000
    assert (overlay.x, overlay.y) == (None, None)


def test_the_setting_is_checked_like_every_other(tmp_path: Path):
    config = config_module.Config()
    config_module.apply_update(config, {"overlay": {"enabled": True, "step_goal": 8000, "x": 1500, "y": 120.7}})
    assert (config.overlay.enabled, config.overlay.step_goal, config.overlay.x, config.overlay.y) == (True, 8000, 1500, 120)
    # A goal out of range is brought into it; a place that is no place is not taken.
    config_module.apply_update(config, {"overlay": {"step_goal": -5, "x": "left", "y": True}})
    assert (config.overlay.step_goal, config.overlay.x, config.overlay.y) == (0, 1500, 120)
    config_module.apply_update(config, {"overlay": {"step_goal": 10**9, "x": 10**9}})
    assert (config.overlay.step_goal, config.overlay.x) == (100_000, 1500)
    # Negative places are real (a screen left of the main one), and a place can be forgotten.
    config_module.apply_update(config, {"overlay": {"x": -1800}})
    assert config.overlay.x == -1800
    config_module.apply_update(config, {"overlay": {"x": None, "y": None}})
    assert (config.overlay.x, config.overlay.y) == (None, None)
    # ... and it is kept in the file.
    config_module.apply_update(config, {"overlay": {"x": 40, "y": 50}})
    config_module.save(config, tmp_path)
    again = config_module.load(tmp_path).overlay
    assert (again.enabled, again.step_goal, again.x, again.y) == (True, 100_000, 40, 50)


# ---- the App -------------------------------------------------------------------------------------


class Overlay:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_shown(self, shown: bool) -> None:
        self.calls.append(shown)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_without_a_window_of_its_own_walka_says_there_is_no_floating_window(rig: Rig):
    assert rig.app.snapshot()["overlay"] == {"available": False}
    rig.app.update_config({"overlay": {"enabled": True}})  # (nothing to show: no error either)
    assert rig.config.overlay.enabled is True


def test_the_app_shows_and_hides_it_as_the_setting_says(rig: Rig, tmp_path: Path):
    overlay = Overlay()
    rig.app.attach_overlay(overlay)
    assert overlay.calls == [False], "off until switched on"
    assert rig.app.snapshot()["overlay"] == {"available": True}

    rig.app.update_config({"overlay": {"enabled": True}})
    assert overlay.calls[-1] is True
    saved = json.loads((tmp_path / "data" / "config.json").read_text("utf-8"))
    assert saved["overlay"]["enabled"] is True

    rig.app.update_config({"units": "kmh"})  # another setting: the window is left alone
    assert overlay.calls == [False, True]

    rig.app.update_config({"overlay": {"enabled": False}})
    assert overlay.calls[-1] is False

    # Switched on when Walka starts: it opens with it.
    rig.app.update_config({"overlay": {"enabled": True}})
    again = Overlay()
    rig.app.attach_overlay(again)
    assert again.calls == [True]


def test_where_it_is_dragged_to_is_remembered(rig: Rig, tmp_path: Path):
    rig.app.overlay_moved(1620, 140)
    assert (rig.config.overlay.x, rig.config.overlay.y) == (1620, 140)
    saved = json.loads((tmp_path / "data" / "config.json").read_text("utf-8"))
    assert (saved["overlay"]["x"], saved["overlay"]["y"]) == (1620, 140)


def test_closed_from_outside_settings_it_stays_closed(rig: Rig):
    overlay = Overlay()
    rig.app.attach_overlay(overlay)
    rig.app.update_config({"overlay": {"enabled": True}})
    rig.app.overlay_closed()
    assert rig.config.overlay.enabled is False
    assert overlay.calls == [False, True], "it is closed already: nobody is asked to close it again"


def test_a_floating_window_that_fails_does_not_take_walka_with_it(rig: Rig, caplog):
    class Broken:
        def set_shown(self, shown: bool) -> None:
            raise RuntimeError("no window today")

    rig.app.attach_overlay(Broken())
    rig.app.update_config({"overlay": {"enabled": True}})
    assert rig.config.overlay.enabled is True
    assert "could not show or hide the floating window" in caplog.text


def test_the_snapshot_names_the_character_playing_not_its_speed(rig: Rig):
    snapshot = rig.app.snapshot()
    assert snapshot["character"] is None
    json.dumps(snapshot)


# ---- the window ----------------------------------------------------------------------------------


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args) -> list:
        return [handler(*args) for handler in self.handlers]


class FakeWindow:
    def __init__(self, title: str, url: str, options: dict) -> None:
        self.title, self.url, self.options = title, url, options
        self.events = types.SimpleNamespace(
            closing=FakeEvent(), shown=FakeEvent(), loaded=FakeEvent(), moved=FakeEvent(), closed=FakeEvent()
        )
        self.destroyed = threading.Event()

    def destroy(self) -> None:
        self.destroyed.set()
        self.events.closed.fire()


class FakeWebview(types.ModuleType):
    """pywebview, as far as the two windows use it."""

    def __init__(self) -> None:
        super().__init__("webview")
        self.settings: dict = {}
        self.renderer = "fake"
        self.windows: list[FakeWindow] = []
        self.screens = [types.SimpleNamespace(x=0, y=0, width=1920, height=1080)]

    def create_window(self, title, url, **options):
        made = FakeWindow(title, url, options)
        self.windows.append(made)
        return made

    def start(self, **options):
        self.windows[0].events.shown.fire()
        self.windows[0].events.loaded.fire()


@pytest.fixture
def webview(monkeypatch) -> FakeWebview:
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


class Heard:
    def __init__(self) -> None:
        self.moved: list[tuple[int, int]] = []
        self.closed = 0

    def window(self, place=None) -> window.FloatingWindow:
        def on_closed() -> None:
            self.closed += 1

        return window.FloatingWindow(
            "http://127.0.0.1:8787/floating",
            place=place or (lambda: None),
            on_moved=lambda x, y: self.moved.append((x, y)),
            on_closed=on_closed,
        )


def test_it_opens_on_top_of_everything_and_out_of_the_way(webview: FakeWebview):
    heard = Heard()
    floating = heard.window()
    floating.set_shown(True)
    time.sleep(0.1)
    assert webview.windows == [], "not before the main window is up"
    floating.can_open()
    assert eventually(lambda: floating.shown)
    (made,) = webview.windows
    assert made.url == "http://127.0.0.1:8787/floating"
    options = made.options
    assert options["on_top"] is True and options["frameless"] is True
    assert options["focus"] is False, "a click on it must leave the keyboard with the game"
    assert options["easy_drag"] is False, "dragged by its drag region, so that its close button can be pressed"
    assert (options["width"], options["height"]) == window.FLOATING_SIZE
    # The top-right corner of the screen, out of the way.
    assert options["x"] == 1920 - window.FLOATING_SIZE[0] - window.FLOATING_MARGIN[0]
    assert options["y"] == window.FLOATING_MARGIN[1]
    assert options["x"] > 1000

    floating.set_shown(True)  # already open: not a second one
    floating.set_shown(False)
    assert eventually(lambda: made.destroyed.is_set() and not floating.shown)
    assert len(webview.windows) == 1
    assert heard.closed == 0, "closed because it was asked to: that is no news"
    floating.finish()


def test_it_opens_where_it_was_left(webview: FakeWebview):
    webview.screens.append(types.SimpleNamespace(x=-1920, y=0, width=1920, height=1080))  # a screen to the left
    floating = Heard().window(place=lambda: (-1500, 300))
    floating.can_open()
    floating.set_shown(True)
    assert eventually(lambda: floating.shown)
    assert (webview.windows[0].options["x"], webview.windows[0].options["y"]) == (-1500, 300)
    floating.finish()


def test_without_a_screen_the_system_places_it():
    assert window.default_floating_place([]) is None
    assert window.default_floating_place([object()]) is None
    screen = types.SimpleNamespace(x=-1920, y=0, width=1920, height=1080)
    assert window.default_floating_place([screen]) == (-1920 + 1920 - 240 - 24, 96)


def test_on_a_scaled_display_it_still_opens_on_the_screen():
    """Windows at 150 %: Walka is aware of scaling, so the screen is listed
    in pixels (3840 wide), but pywebview multiplies a window's place by 1.5
    itself. Worked out in pixels, the corner landed at x = 5364 - off the
    screen, where the window was open and could not be seen."""
    screen = types.SimpleNamespace(x=0, y=0, width=3840, height=2160)
    x, y = window.default_floating_place([screen], scale=1.5)
    assert (x, y) == (2560 - 240 - 24, 96)
    assert x * 1.5 + window.FLOATING_SIZE[0] * 1.5 <= 3840, "on the screen once pywebview has scaled it"
    assert window.on_a_screen((x, y), [screen], scale=1.5)
    assert not window.on_a_screen((3576, 96), [screen], scale=1.5), "the place the bug put it"


def test_it_opens_on_the_main_screen():
    left = types.SimpleNamespace(x=-1920, y=0, width=1920, height=1080)
    main = types.SimpleNamespace(x=0, y=0, width=2560, height=1440)
    assert window.default_floating_place([left, main]) == (2560 - 240 - 24, 96)


def test_a_place_on_no_screen_is_not_where_it_opens(webview: FakeWebview, monkeypatch):
    """Remembered on a screen that is gone (or worked out wrong): the corner
    of the main screen instead, so that it can be seen and dragged."""
    monkeypatch.setattr(window, "pywebview_scale", lambda: 1.5)
    webview.screens = [types.SimpleNamespace(x=0, y=0, width=3840, height=2160)]
    floating = Heard().window(place=lambda: (3576, 96))
    floating.can_open()
    floating.set_shown(True)
    assert eventually(lambda: floating.shown)
    assert (webview.windows[0].options["x"], webview.windows[0].options["y"]) == (2560 - 264, 96)
    floating.finish()


def test_which_places_count_as_on_a_screen():
    screens = [types.SimpleNamespace(x=0, y=0, width=1920, height=1080),
               types.SimpleNamespace(x=-1920, y=0, width=1920, height=1080)]
    assert window.on_a_screen((100, 100), screens)
    assert window.on_a_screen((-1500, 300), screens), "a screen left of the main one"
    assert window.on_a_screen((1800, 50), screens), "half off the edge is still reachable"
    assert not window.on_a_screen((2000, 100), screens)
    assert not window.on_a_screen((100, -400), screens)
    assert not window.on_a_screen((100, 1070), screens), "its top strip below the bottom edge"
    assert window.on_a_screen((5000, 5000), []), "no screens known: nothing to judge by"
    assert window.pywebview_scale("darwin") == 1.0 and window.pywebview_scale("linux") == 1.0


def test_where_it_is_dragged_is_told_once_the_drag_is_over(webview: FakeWebview, monkeypatch):
    monkeypatch.setattr(window, "MOVED_SETTLE_S", 0.2)
    heard = Heard()
    floating = heard.window()
    floating.can_open()
    floating.set_shown(True)
    assert eventually(lambda: floating.shown)
    made = webview.windows[0]
    for x in range(100, 200, 10):
        made.events.moved.fire(x, 50)
    assert heard.moved == []
    assert eventually(lambda: heard.moved == [(190, 50)])
    made.events.moved.fire("nowhere", None)  # (nothing to remember)
    time.sleep(0.3)
    assert heard.moved == [(190, 50)]
    floating.finish()


def test_closed_by_the_system_it_says_so_and_stays_closed(webview: FakeWebview):
    heard = Heard()
    floating = heard.window()
    floating.can_open()
    floating.set_shown(True)
    assert eventually(lambda: floating.shown)
    webview.windows[0].events.closed.fire()  # Alt+F4
    assert heard.closed == 1 and not floating.shown
    time.sleep(0.1)
    assert len(webview.windows) == 1, "not opened again by itself"
    floating.set_shown(True)  # switched on again in Settings
    assert eventually(lambda: len(webview.windows) == 2 and floating.shown)
    floating.finish()


def test_it_goes_when_walkas_window_does(webview: FakeWebview, tmp_path: Path):
    floating = Heard().window()
    dashboard = window.DashboardWindow(
        "http://127.0.0.1:8787/", tmp_path, on_closing=lambda: None, floating=floating
    )
    floating.set_shown(True)
    dashboard.run()  # the stand-in returns at once, with the main window "shown"
    assert eventually(lambda: floating.shown)
    small = webview.windows[1]
    webview.windows[0].events.closing.fire()  # the user closes Walka's window
    assert eventually(lambda: small.destroyed.is_set())
    floating.set_shown(True)
    time.sleep(0.1)
    assert len(webview.windows) == 2, "never again once Walka is closing"


def test_a_floating_window_that_cannot_be_made_is_not_tried_again_and_again(webview: FakeWebview, monkeypatch, caplog):
    tries: list[int] = []

    def broken(*args, **kwargs):
        tries.append(1)
        raise RuntimeError("no window today")

    monkeypatch.setattr(webview, "create_window", broken)
    floating = Heard().window()
    floating.can_open()
    floating.set_shown(True)
    assert eventually(lambda: tries == [1])
    time.sleep(0.2)
    assert tries == [1]
    assert "the floating window could not be opened" in caplog.text
    floating.finish()


def test_the_companion_hears_of_it_on_its_own_loop(webview: FakeWebview, tmp_path: Path):
    """cli: the floating window is handed to the App, and what it reports
    reaches the App on the companion's loop."""
    rig = Rig(tmp_path)
    rig.config.overlay.x, rig.config.overlay.y = 300, 400
    loop = asyncio.new_event_loop()
    ran = threading.Thread(target=loop.run_forever, daemon=True)
    ran.start()
    try:
        running = cli._Running("http://127.0.0.1:8787/", loop, asyncio.Event(), threading.Event(), rig.app)
        floating = cli._floating_window(window, running)
        assert eventually(lambda: rig.app.overlay is floating)
        assert floating.url == "http://127.0.0.1:8787/floating"
        running.call(rig.app.update_config, {"overlay": {"enabled": True}})
        floating.can_open()
        assert eventually(lambda: floating.shown)
        assert (webview.windows[0].options["x"], webview.windows[0].options["y"]) == (300, 400)
        floating._on_moved(10, 20)
        assert eventually(lambda: (rig.config.overlay.x, rig.config.overlay.y) == (10, 20))
        webview.windows[0].events.closed.fire()
        assert eventually(lambda: rig.config.overlay.enabled is False)
        floating.finish()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        ran.join(5)
        loop.close()
    # Once Walka has stopped, nothing more is handed to it.
    running.stopped.set()
    running.call(lambda: 1 / 0)


# ---- the page ------------------------------------------------------------------------------------


def test_the_floating_page_is_served_like_the_dashboard(rig: Rig):
    async def scenario(client: TestClient):
        response = await client.get("/floating")
        assert response.status == 200
        text = await response.text()
        assert "/static/floating.js" in text and "/static/floating.css" in text
        assert "pywebview-drag-region" in text
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        for name in ("floating.js", "floating.css"):
            response = await client.get(f"/static/{name}")
            assert response.status == 200, name

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            await scenario(client)

    asyncio.run(asyncio.wait_for(main(), 30))


def test_the_floating_page_has_nothing_inline_the_page_policy_would_block():
    html = (STATIC / "floating.html").read_text("utf-8")
    assert "<style" not in html and " style=" not in html
    assert "<script>" not in html and "onclick" not in html
    script = (STATIC / "floating.js").read_text("utf-8")
    # Its close button switches the setting off: the one thing it changes.
    assert '"/api/config", { overlay: { enabled: false } }' in script


def test_the_floating_page_says_what_the_treadmill_is_doing_not_the_game():
    """Nothing about the game is live: the panel's words come from the belt alone."""
    script = (STATIC / "floating.js").read_text("utf-8")
    assert "s.source" not in script and "beacon" not in script
    for stale in ("Paused in game", "no game session", "Ready - start the belt"):
        assert stale not in script, stale
    for words in ('"Walking"', '"Belt stopped"', '"Paused at the treadmill"'):
        assert words in script, words
