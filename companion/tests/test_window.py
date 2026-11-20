"""Walka's own window: when there can be one, what it is asked to be, and
the arrangement around it - the window on the main thread, the companion on
another, and the walk saved whichever of the two is told to stop.

No window is opened here: pywebview is stood in for (``FakeWebview``, and
the ``webview`` package written by ``stand_in_for_pywebview`` for the tests
that start the real program). The real thing is opened by the build's smoke
test, on the machines it is built for (.github/workflows/ci.yml).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

from conftest import SRC
from walka import cli, window
from walka import config as config_module

# ---- can there be a window? ------------------------------------------------------------------


def test_webview2_is_found_wherever_it_is_installed():
    runtime = window.WEBVIEW2_CLIENT
    for_all_users = ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{runtime}")
    for_this_user = ("HKEY_CURRENT_USER", rf"Software\Microsoft\EdgeUpdate\Clients\{runtime}")

    def registry(values: dict):
        return lambda root, key, name: values.get((root, key)) if name == "pv" else None

    assert window.webview2_version(registry({})) is None
    assert window.webview2_version(registry({for_all_users: "131.0.2903.86"})) == "131.0.2903.86"
    assert window.webview2_version(registry({for_this_user: "120.0.1"})) == "120.0.1"
    # What an uninstalled or half-installed runtime leaves behind is not one.
    assert window.webview2_version(registry({for_all_users: "0.0.0.0", for_this_user: ""})) is None
    assert window.webview2_version(registry({for_all_users: "0.0.0.0", for_this_user: "120.0.1"})) == "120.0.1"


def test_no_window_without_what_draws_it(monkeypatch):
    there = {"webview", "qtpy"}
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object() if name in there else None)
    monkeypatch.setattr(window, "webview2_version", lambda: "131.0")
    monkeypatch.setenv("DISPLAY", ":0")
    assert window.unavailable("win32") is None
    assert window.unavailable("darwin") is None
    assert window.unavailable("linux") is None

    # A Windows without WebView2: pywebview would fall back to Internet
    # Explorer's engine, which cannot show the dashboard.
    monkeypatch.setattr(window, "webview2_version", lambda: None)
    assert "WebView2" in window.unavailable("win32")
    assert window.unavailable("darwin") is None

    # Elsewhere: only with a screen, and with a toolkit for pywebview.
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert "no screen" in window.unavailable("linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert window.unavailable("linux") is None
    there.discard("qtpy")
    assert "Qt nor GTK" in window.unavailable("linux")

    there.discard("webview")
    for platform in ("win32", "darwin", "linux"):
        assert "pywebview" in window.unavailable(platform)


def parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def test_where_the_dashboard_goes(monkeypatch, caplog):
    monkeypatch.setattr(window, "unavailable", lambda: None)
    cfg = config_module.Config()
    assert cli._dashboard_goes(parse(), cfg) == "window"
    assert cli._dashboard_goes(parse("--demo"), cfg) == "window"
    assert cli._dashboard_goes(parse("--browser"), cfg) == "browser"
    assert cli._dashboard_goes(parse("--no-window"), cfg) == "nowhere"
    # The name this had when the dashboard was a browser tab still works.
    assert cli._dashboard_goes(parse("--no-browser"), cfg) == "nowhere"
    assert cli._dashboard_goes(parse("--no-window", "--browser"), cfg) == "nowhere"

    # ... and so do the settings.
    cfg.web.window = False
    assert cli._dashboard_goes(parse(), cfg) == "browser"
    cfg.web.window, cfg.web.open_browser = True, False
    assert cli._dashboard_goes(parse(), cfg) == "nowhere"
    assert cli._dashboard_goes(parse("--browser"), cfg) == "nowhere"

    # No window to be had: the browser, as before - and the log says why.
    cfg = config_module.Config()
    monkeypatch.setattr(window, "unavailable", lambda: "Microsoft Edge WebView2 is not on this computer")
    with caplog.at_level(logging.INFO, logger="walka"):
        assert cli._dashboard_goes(parse(), cfg) == "browser"
    assert "WebView2 is not on this computer" in caplog.text


def test_the_window_setting_is_kept_and_read(tmp_path: Path):
    cfg = config_module.Config()
    assert cfg.web.window is True and cfg.to_dict()["web"]["window"] is True
    cfg.web.window = False
    config_module.save(cfg, tmp_path)
    assert config_module.load(tmp_path).web.window is False
    # A file written before there was a window has no such key: a window it is.
    saved = json.loads((tmp_path / "config.json").read_text("utf-8"))
    del saved["web"]["window"]
    (tmp_path / "config.json").write_text(json.dumps(saved), "utf-8")
    assert config_module.load(tmp_path).web.window is True


# ---- what the window is asked to be ------------------------------------------------------------


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self) -> list:
        return [handler() for handler in self.handlers]


class FakeWebview(types.ModuleType):
    """pywebview, as far as ``window.DashboardWindow`` uses it. ``start()``
    returns when the window is destroyed - or at once, with ``open_for=0``."""

    def __init__(self, open_for: float | None = None) -> None:
        super().__init__("webview")
        self.settings: dict = {}
        self.renderer = "fake"
        self.created: list[tuple] = []
        self.started: list[dict] = []
        self.destroyed = threading.Event()
        self.open_for = open_for
        self.window = None

    def create_window(self, title, url, **options):
        self.created.append((title, url, options))
        events = types.SimpleNamespace(closing=FakeEvent(), shown=FakeEvent(), loaded=FakeEvent())
        self.window = types.SimpleNamespace(events=events, destroy=self.destroyed.set)
        return self.window

    def start(self, **options):
        self.started.append(options)
        self.window.events.shown.fire()
        self.window.events.loaded.fire()
        self.destroyed.wait(self.open_for)


@pytest.fixture
def webview(monkeypatch) -> FakeWebview:
    fake = FakeWebview(open_for=0)
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


def test_the_window_shows_the_dashboard_and_nothing_else(webview: FakeWebview, tmp_path: Path, caplog):
    closing: list[str] = []
    dashboard = window.DashboardWindow(
        "http://127.0.0.1:8787/", tmp_path / "window", on_closing=lambda: closing.append("asked")
    )
    with caplog.at_level(logging.INFO, logger="walka"):
        dashboard.run()

    ((title, url, options),) = webview.created
    assert (title, url) == ("Walka", "http://127.0.0.1:8787/")
    assert (options["width"], options["height"]) == window.SIZE and options["min_size"] == window.MIN_SIZE
    assert options["text_select"] is True  # (pywebview's default makes a page's text unselectable)
    # The page's two downloads work, and a link that leads elsewhere opens in the browser.
    assert webview.settings["ALLOW_DOWNLOADS"] is True
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    # What the page stores is kept, in Walka's own data folder.
    (started,) = webview.started
    assert started["private_mode"] is False and started["storage_path"] == str(tmp_path / "window")
    assert (tmp_path / "window").is_dir()
    # The log says that the window came up: without a console it is the only place that can.
    assert "Walka's window is open (drawn by fake)" in caplog.text
    assert "the dashboard is loaded in Walka's window" in caplog.text

    # Closing: whoever closes the window, Walka is told first ...
    assert closing == []
    webview.window.events.closing.fire()
    assert closing == ["asked"]
    # ... and nothing it does then keeps the window from closing (False would).
    dashboard = window.DashboardWindow("http://127.0.0.1:8787/", tmp_path, on_closing=lambda: 1 / 0)
    dashboard.run()
    assert webview.window.events.closing.fire() == [None]


def test_the_window_can_be_closed_from_another_thread(monkeypatch, tmp_path: Path):
    fake = FakeWebview()  # stays open until it is destroyed
    monkeypatch.setitem(sys.modules, "webview", fake)
    dashboard = window.DashboardWindow("http://127.0.0.1:8787/", tmp_path, on_closing=lambda: None)
    threading.Timer(0.2, dashboard.close).start()
    started = time.monotonic()
    dashboard.run()
    assert fake.destroyed.is_set() and time.monotonic() - started < 5

    # A window that is closing by itself - its user closed it - is left to it.
    fake = FakeWebview(open_for=0)
    monkeypatch.setitem(sys.modules, "webview", fake)
    dashboard = window.DashboardWindow("http://127.0.0.1:8787/", tmp_path, on_closing=lambda: None)
    dashboard.run()
    fake.window.events.closing.fire()
    dashboard.close()
    assert not fake.destroyed.is_set()

    # Asked to close before it was ever opened: it does not open.
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    dashboard = window.DashboardWindow("http://127.0.0.1:8787/", tmp_path, on_closing=lambda: None)
    dashboard.close()
    dashboard.run()
    assert fake.created == [] and fake.started == []


def test_pywebview_chooses_what_draws_the_window_where_there_is_one_choice(monkeypatch):
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: None)
    assert window._toolkit("win32") is None and window._toolkit("darwin") is None
    assert window._toolkit("linux") == "qt"
    monkeypatch.setattr(window.importlib.util, "find_spec", lambda name: object())
    assert window._toolkit("linux") is None


def test_without_a_console_or_a_window_a_message_box_is_the_way_to_quit(monkeypatch, message_boxes: list[str]):
    """The packaged program on Windows, started with a double-click, its
    dashboard in the browser: nothing of Walka itself is on screen."""
    if sys.platform != "win32":
        assert window.no_console() is False  # only Windows has programs without one

    class Running:
        url = "http://127.0.0.1:8787/"
        asked = threading.Event()

        def ask_to_stop(self, wait_s: float = 4.0) -> None:
            self.asked.set()

    monkeypatch.setattr(window, "no_console", lambda: False)
    cli._offer_a_way_to_quit(Running())
    cli._could_not_start("Could not open a port near 8787 for the dashboard.")
    assert message_boxes == [] and not Running.asked.wait(0.2)  # there is a console: Ctrl+C, and it can read

    monkeypatch.setattr(window, "no_console", lambda: True)
    cli._offer_a_way_to_quit(Running())
    assert Running.asked.wait(5), "dismissing the box did not close Walka"
    (box,) = message_boxes
    assert "http://127.0.0.1:8787/" in box and "OK closes Walka" in box
    # ... and it is where a program without a console says that it cannot start.
    cli._could_not_start("Could not open a port near 8787 for the dashboard.")
    assert message_boxes[-1] == "Could not open a port near 8787 for the dashboard."


# ---- the companion behind the window -----------------------------------------------------------


class FakeDashboardWindow:
    """Stands in for ``window.DashboardWindow`` under ``cli._run_in_window``:
    ``run()`` hands itself to the test (``opened``) and stays "open" until
    it is closed - by the user (``user_closes``) or by Walka (``close``)."""

    opened: list[FakeDashboardWindow] = []
    fails = False

    def __init__(self, url: str, storage: Path, *, on_closing) -> None:
        self.url, self.storage, self.on_closing = url, storage, on_closing
        self.is_open = threading.Event()
        self.gone = threading.Event()
        self.closed_by: list[str] = []

    def run(self) -> None:
        assert threading.current_thread() is threading.main_thread(), "the window needs the main thread"
        if self.fails:
            raise RuntimeError("no window today")
        self.opened.append(self)
        self.is_open.set()
        assert self.gone.wait(30), "nobody closed the window"

    def user_closes(self) -> None:
        self.closed_by.append("user")
        self.on_closing()  # (what the real window does first: see DashboardWindow)
        self.gone.set()

    def close(self) -> None:
        self.closed_by.append("walka")
        self.gone.set()


@pytest.fixture
def fake_window(monkeypatch) -> type[FakeDashboardWindow]:
    monkeypatch.setattr(FakeDashboardWindow, "opened", [])
    monkeypatch.setattr(FakeDashboardWindow, "fails", False)
    monkeypatch.setattr(window, "DashboardWindow", FakeDashboardWindow)
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 0.2)
    monkeypatch.setattr(cli, "DEMO_WALK_STARTS_AFTER_S", 0.3)
    return FakeDashboardWindow


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def state_of(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + "api/state", timeout=5) as response:
        return json.load(response)


def when_open(fake_window: type[FakeDashboardWindow], then) -> threading.Thread:
    """Run ``then(window)`` on another thread once the window is open: the
    test's own thread - the main one - is the window's."""
    failures: list[BaseException] = []

    def wait_and_do() -> None:
        try:
            deadline = time.monotonic() + 20
            while not fake_window.opened and time.monotonic() < deadline:
                time.sleep(0.01)
            then(fake_window.opened[0])
        except BaseException as exc:  # shown by the test, not lost on a thread
            failures.append(exc)
            for opened in fake_window.opened:
                opened.gone.set()

    thread = threading.Thread(target=wait_and_do, name="test-user", daemon=True)
    thread.failures = failures  # type: ignore[attr-defined]
    thread.start()
    return thread


def companion_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate() if thread.name == "walka-companion"]


def run_in_window(tmp_path: Path, *extra: str) -> int:
    args = parse("--demo", "--port", str(free_port()), "--data-dir", str(tmp_path), *extra)
    return cli._run_in_window(args, config_module.load(tmp_path))


def test_closing_the_window_saves_the_walk_and_closes_walka(fake_window, tmp_path: Path, capsys):
    seen: dict = {}

    def user(opened: FakeDashboardWindow) -> None:
        # The dashboard is served while the window is open - from a thread
        # that is not the window's.
        for _ in range(200):
            seen["state"] = state_of(opened.url)
            if seen["state"]["treadmill"]["speed_kmh"] > 0:
                break
            time.sleep(0.05)
        seen["companion"] = companion_threads()
        seen["storage"] = opened.storage
        opened.user_closes()
        # By the time the window is allowed to close, the walk is saved:
        # macOS may end the program the moment it has (Quit in the menu).
        seen["saved_before_the_window_closed"] = (tmp_path / "config.json").exists()

    thread = when_open(fake_window, user)
    assert run_in_window(tmp_path) == 0
    thread.join(10)
    assert not thread.failures, thread.failures
    assert seen["state"]["treadmill"]["kind"] == "simulated" and seen["state"]["treadmill"]["speed_kmh"] > 0
    assert seen["companion"] == ["walka-companion"]
    assert seen["storage"] == tmp_path / cli.WINDOW_DATA
    assert seen["saved_before_the_window_closed"]
    assert not (tmp_path / "active-session.json").exists()
    assert companion_threads() == []  # it has stopped, not been left behind
    (opened,) = fake_window.opened
    assert opened.closed_by[0] == "user"
    out = capsys.readouterr().out
    assert "Close Walka's window to quit" in out and "Ctrl+C" not in out


def test_the_window_closes_when_walka_is_stopped_some_other_way(fake_window, tmp_path: Path):
    """Windows logging off, a kill: the companion is asked to stop without
    the window having been touched. The window goes with it."""

    def system(opened: FakeDashboardWindow) -> None:
        running = opened.on_closing.__self__  # what the session-end watcher and the signals hold too
        running.loop.call_soon_threadsafe(running.stop.set)

    thread = when_open(fake_window, system)
    assert run_in_window(tmp_path) == 0
    thread.join(10)
    assert not thread.failures, thread.failures
    (opened,) = fake_window.opened
    assert opened.closed_by == ["walka"]
    assert (tmp_path / "config.json").exists() and companion_threads() == []


def test_no_window_is_opened_for_a_companion_that_could_not_start(fake_window, tmp_path: Path, monkeypatch, capsys):
    from walka import web

    async def no_port(*_args, **_kwargs):
        raise OSError("address in use")

    monkeypatch.setattr(web, "serve", no_port)
    assert run_in_window(tmp_path) == 2
    assert fake_window.opened == [] and companion_threads() == []
    assert "Could not open a port" in capsys.readouterr().err


def test_a_window_that_cannot_be_opened_becomes_a_browser_tab(fake_window, tmp_path: Path, monkeypatch, caplog):
    """Whatever unavailable() did not foresee: Walka still runs, the
    dashboard still gets shown, and there is still a way to close it."""
    fake_window.fails = True
    opened_in_browser: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened_in_browser.append)

    def offered(running) -> None:  # (on Windows, without a console: the message box)
        threading.Timer(0.3, running.ask_to_stop).start()

    monkeypatch.setattr(cli, "_offer_a_way_to_quit", offered)
    with caplog.at_level(logging.ERROR, logger="walka"):
        assert run_in_window(tmp_path) == 0
    assert len(opened_in_browser) == 1 and opened_in_browser[0].startswith("http://127.0.0.1:")
    assert "could not be opened" in caplog.text and "no window today" in caplog.text
    assert (tmp_path / "config.json").exists() and companion_threads() == []


def test_run_picks_the_window_or_the_event_loop(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_in_window", lambda args, cfg: calls.append("window") or 0)

    async def run(args, *, cfg=None, goes=None, started=None):
        calls.append(goes)
        return 0

    monkeypatch.setattr(cli, "_run", run)
    monkeypatch.setattr(window, "unavailable", lambda: None)
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    assert cli.main(["--data-dir", str(tmp_path), "--browser"]) == 0
    assert cli.main(["--data-dir", str(tmp_path), "--no-window"]) == 0
    monkeypatch.setattr(window, "unavailable", lambda: "not here")
    assert cli.main(["--data-dir", str(tmp_path)]) == 0
    assert calls == ["window", "browser", "nowhere", "browser"]


# ---- signals, with the main thread in the window's event loop ---------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_signal_is_heard_without_the_main_thread_running_any_python(name: str):
    number = getattr(signal, name)
    before = signal.getsignal(number)
    heard = threading.Event()
    undo = cli._hear_signals(heard.set)
    try:
        assert signal.getsignal(number) is not before
        os.kill(os.getpid(), number)
        assert heard.wait(5), f"{name} was not heard"
    finally:
        undo()
    # Put back as it was: what comes after the window has its own handling.
    assert signal.getsignal(number) is before
    assert signal.set_wakeup_fd(-1) == -1


def test_signals_are_left_alone_off_the_main_thread():
    before = {name: signal.getsignal(getattr(signal, name)) for name in ("SIGINT", "SIGTERM")}
    results: list = []
    thread = threading.Thread(target=lambda: results.append(cli._hear_signals(lambda: None)))
    thread.start()
    thread.join(5)
    (undo,) = results
    undo()
    assert {name: signal.getsignal(getattr(signal, name)) for name in before} == before


# ---- the real program, with a stand-in for pywebview --------------------------------------------------

STAND_IN = '''
"""A stand-in for pywebview: a "window" that is open until it is destroyed -
or, with WALKA_TEST_CLOSE_AFTER set, until its user closes it after that
many seconds."""
import os, threading, types

settings = {}
renderer = "stand-in"
_windows = []


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class _Window:
    def __init__(self):
        self.events = types.SimpleNamespace(closing=_Event(), shown=_Event(), loaded=_Event())
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


def create_window(title, url, **options):
    _windows.append(_Window())
    return _windows[-1]


def start(**options):
    window = _windows[0]
    window.events.shown.fire()
    window.events.loaded.fire()
    after = float(os.environ.get("WALKA_TEST_CLOSE_AFTER") or 0)
    if not window.destroyed.wait(after or None):
        window.events.closing.fire()  # the user closes the window
'''


def stand_in_for_pywebview(folder: Path) -> Path:
    (folder / "webview").mkdir(parents=True)
    (folder / "webview" / "__init__.py").write_text(STAND_IN, "utf-8")
    (folder / "qtpy.py").write_text("", "utf-8")  # (what unavailable() looks for off Windows and the Mac)
    return folder


def start_walka(tmp_path: Path, port: int, **env_extra: str) -> subprocess.Popen:
    env = dict(os.environ)
    stand_in = stand_in_for_pywebview(tmp_path / "stand-in")
    env["PYTHONPATH"] = os.pathsep.join([str(stand_in), str(SRC), env.get("PYTHONPATH", "")])
    env["WALKA_DATA_DIR"] = str(tmp_path / "data")
    env["PYTHONUNBUFFERED"] = "1"
    env["DISPLAY"] = ":walka-test"  # never connected to: the stand-in opens nothing
    env.update(env_extra)
    return subprocess.Popen(
        [sys.executable, "-m", "walka", "--demo", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for_the_belt(process: subprocess.Popen, port: int) -> dict:
    deadline = time.monotonic() + 25
    state = None
    while time.monotonic() < deadline:
        assert process.poll() is None, f"exited early: {process.stderr.read()}"
        try:
            state = state_of(f"http://127.0.0.1:{port}/")
        except OSError:
            time.sleep(0.2)
            continue
        if state["treadmill"]["speed_kmh"] > 0.5:
            return state
        time.sleep(0.2)
    raise AssertionError(f"the demo's walker never started the belt: {state}")


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="started as it is where a window is optional")
@pytest.mark.parametrize("how", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_walka_in_its_window_exits_cleanly_when_told_to(tmp_path: Path, how: str):
    """``python -m walka --demo`` - no --no-window: it opens its window (a
    stand-in) - ended from outside. The walk is saved and the program ends
    by itself, window and all."""
    port = free_port()
    process = start_walka(tmp_path, port)
    try:
        wait_for_the_belt(process, port)
        process.send_signal(getattr(signal, how))
        out, err = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 0, err
    assert "Traceback" not in err, err
    assert "Close Walka's window to quit" in out
    data = tmp_path / "data"
    assert (data / "config.json").exists() and not (data / "active-session.json").exists()
    # What a program without a console has instead of one.
    log = (data / cli.LOG_FILE).read_text("utf-8")
    assert "Walka's window is open (drawn by stand-in)" in log and "is running: dashboard at" in log


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="started as it is where a window is optional")
def test_walka_closes_when_its_window_is_closed(tmp_path: Path):
    port = free_port()
    process = start_walka(tmp_path, port, WALKA_TEST_CLOSE_AFTER="3")
    try:
        out, err = process.communicate(timeout=40)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 0, err
    assert "Traceback" not in err, err
    assert f"dashboard: http://127.0.0.1:{port}/" in out
    data = tmp_path / "data"
    assert (data / "config.json").exists() and not (data / "active-session.json").exists()


# ---- the command prompt a windowed program was started from (Windows) --------------------------------


def test_the_console_is_looked_for_up_the_line_of_descent():
    """Packed into one file, Walka.exe is started by a copy of itself, which
    has no console: the command prompt is its grandparent."""
    parents = {400: 300, 300: 200, 200: 100, 100: 4, 4: 0}
    assert cli._line_of_descent(parents, 400) == [300, 200, 100]
    assert cli._line_of_descent(parents, 400, generations=1) == [300]
    assert cli._line_of_descent(parents, 100) == [4]  # (0 is nobody)
    assert cli._line_of_descent({}, 400) == []
    # Process ids are used again: a "family" that goes round in a circle ends.
    assert cli._line_of_descent({400: 300, 300: 400}, 400) == [300]
    assert cli._line_of_descent({400: 400}, 400) == []


def test_streams_that_are_there_are_left_alone(monkeypatch):
    before = (sys.stdin, sys.stdout, sys.stderr)
    monkeypatch.setattr(cli, "_attach_to_a_console_it_descends_from", lambda: 1 / 0)
    cli._use_the_console_it_was_started_from(["probe"])
    assert (sys.stdin, sys.stdout, sys.stderr) == before


def test_a_windowed_program_gets_somewhere_to_print(monkeypatch):
    """Windows gives a windowed program no streams at all. Started with a
    command it takes the console of whoever started it; in any case, what
    is missing is replaced - nothing in Walka checks before it prints."""
    attached: list[bool] = []

    def attach() -> bool:
        attached.append(True)
        return False  # nobody up the line has a console

    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli, "_attach_to_a_console_it_descends_from", attach)
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(cli.sys, name, None)
    cli._use_the_console_it_was_started_from([])  # a double-click: the prompt, if any, is left alone
    assert attached == []
    try:
        print("into the void", file=cli.sys.stdout)
        print("and its errors", file=cli.sys.stderr)
        assert cli.sys.stdin.read() == ""
    finally:
        for stream in (cli.sys.stdin, cli.sys.stdout, cli.sys.stderr):
            stream.close()

    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(cli.sys, name, None)
    cli._use_the_console_it_was_started_from(["probe"])
    assert attached == [True]
    for stream in (cli.sys.stdin, cli.sys.stdout, cli.sys.stderr):
        stream.close()
