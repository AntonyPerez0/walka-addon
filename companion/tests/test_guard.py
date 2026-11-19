"""The guard: the helper process that stops the belt when the companion is
killed, and the companion's end of the pipe to it."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_app import Rig, commands_sent, drop_link, run, session_running
from walka import guard
from walka.guard import (
    ALREADY_STOPPED,
    REPORT_FILE,
    STAND_DOWN_FILE,
    STOOD_DOWN,
    STOPPED,
    UNREACHABLE,
    GuardLink,
    order_line,
    read_orders,
    run_guard,
    stop_belt,
    take_report,
)
from walka.treadmill.base import BeltState, Treadmill, TreadmillError

# ---- what the companion says ---------------------------------------------------------


def test_the_last_word_decides():
    assert read_orders([]) is None, "the companion died before it drove anything"
    assert read_orders(["idle\n"]) is None
    assert read_orders(["belt AA:BB\n"]) == "AA:BB"
    assert read_orders(["belt AA:BB\n", "idle\n"]) is None, "the belt was stopped before the companion went"
    assert read_orders(["idle\n", "belt AA:BB\n", "belt CC:DD\n"]) == "CC:DD"
    assert read_orders(["belt AA:BB\n", "bye\n"]) is None, "an orderly exit: the companion saw to the belt"
    assert read_orders(["belt AA:BB\n", "bye\n", "belt CC:DD\n"]) is None, "nothing counts after bye"


def test_a_torn_or_unknown_line_changes_nothing():
    assert read_orders(["belt AA:BB\n", "bel"]) == "AA:BB"
    assert read_orders(["belt AA:BB\n", "\n", "by"]) == "AA:BB"
    assert read_orders(["belt \n"]) is None, "no address is no belt"
    assert read_orders(["idle\n", "what\n"]) is None
    # A Mac names a treadmill by a long identifier, not by six bytes.
    assert read_orders(["belt 6F0E7C1A-98A3-4C2B-B7A6-0C7A2C3D4E5F\n"]) == "6F0E7C1A-98A3-4C2B-B7A6-0C7A2C3D4E5F"


def test_what_the_companion_writes_is_what_the_guard_reads():
    assert read_orders([order_line("AA:BB")]) == "AA:BB"
    assert read_orders([order_line("AA:BB"), order_line(None)]) is None
    assert read_orders([order_line("")]) is None


# ---- stopping a belt -----------------------------------------------------------------


class Pad(Treadmill):
    """A treadmill as the guard meets it: perhaps out of reach at first,
    perhaps deaf to the first stop, with a belt that takes a while."""

    kind = "fake"

    def __init__(self, clock, *, speed=3.0, reachable_after=0.0, deaf_stops=0, reports=True, coast_s=2.0):
        super().__init__()
        self.clock = clock
        self.speed = speed
        self.reachable_after = reachable_after
        self.deaf_stops = deaf_stops
        self.reports = reports
        self.coast_s = coast_s
        self.calls: list[str] = []
        self._stopped_at: float | None = None

    def tick(self) -> None:
        if self._stopped_at is not None and self.clock() - self._stopped_at >= self.coast_s:
            self.speed = 0.0
        if self.status.connected and self.reports:
            self.status.speed_kmh = self.speed
            self.status.updated_at = self.clock()
            if self.speed > 0 and self.status.state is BeltState.UNKNOWN:
                self.status.state = BeltState.RUNNING

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.clock() < self.reachable_after:
            raise TreadmillError("treadmill not found")
        self.status.connected = True
        self.status.state = BeltState.UNKNOWN
        self.status.updated_at = self.clock() - 0.001  # (the driver stamps the moment of connecting)

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.status.connected = False

    async def start(self) -> None:
        raise AssertionError("the guard must never start a belt")

    async def pause(self) -> None:
        raise AssertionError("the guard only stops")

    async def set_speed(self, kmh: float) -> None:
        raise AssertionError("the guard must never set a speed")

    async def stop(self) -> None:
        self.calls.append("stop")
        if self.deaf_stops > 0:
            self.deaf_stops -= 1
            raise TreadmillError("no answer")
        self.status.state = BeltState.STOPPED
        if self._stopped_at is None:
            self._stopped_at = self.clock()


class Time:
    def __init__(self) -> None:
        self.now = 500.0
        self.pad: Pad | None = None

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.pad is not None:
            self.pad.tick()


def take_over(pad_options: dict, **options):
    t = Time()
    pad = t.pad = Pad(t.clock, **pad_options)
    result = asyncio.run(stop_belt(pad, clock=t.clock, sleep=t.sleep, **options))
    return result, pad, t


def test_a_running_belt_is_stopped():
    result, pad, t = take_over({})
    assert result == STOPPED
    assert pad.calls == ["connect", "stop", "disconnect"]
    assert pad.speed == 0.0
    assert t.now - 500.0 < 10


def test_a_belt_found_standing_is_left_alone():
    result, pad, _ = take_over({"speed": 0.0})
    assert result == ALREADY_STOPPED
    assert pad.calls == ["connect", "disconnect"], "no stop is sent to a belt that stands"


def test_the_guard_keeps_trying_to_reach_the_treadmill():
    result, pad, t = take_over({"reachable_after": 500.0 + 40})
    assert result == STOPPED
    assert pad.calls.count("connect") > 5 and pad.calls[-2:] == ["stop", "disconnect"]
    assert 40 <= t.now - 500.0 < 60


def test_a_stop_that_fails_is_sent_again():
    result, pad, _ = take_over({"deaf_stops": 2})
    assert result == STOPPED
    assert pad.calls.count("stop") == 3


def test_a_belt_that_does_not_slow_down_is_told_again():
    result, pad, _ = take_over({"coast_s": 12.0})
    assert result == STOPPED
    assert pad.calls.count("stop") >= 2


def test_a_treadmill_out_of_reach_is_given_up_on_in_the_end():
    result, pad, t = take_over({"reachable_after": 1e9}, budget_s=60)
    assert result == UNREACHABLE
    assert "stop" not in pad.calls
    assert 60 <= t.now - 500.0 < 70


def test_a_treadmill_that_never_reports_is_stopped_once():
    result, pad, _ = take_over({"reports": False})
    assert result == STOPPED
    assert pad.calls.count("stop") == 1


def test_the_guard_lets_go_when_the_companion_is_back():
    asked = []

    def stand_down() -> bool:
        asked.append(1)
        return len(asked) > 3

    result, pad, _ = take_over({"reachable_after": 1e9}, stand_down=stand_down)
    assert result == STOOD_DOWN
    assert "stop" not in pad.calls and pad.calls[-1] == "disconnect"


# ---- the guard process, whole --------------------------------------------------------


def test_nothing_to_do_leaves_no_report(tmp_path: Path):
    made = []
    assert run_guard(tmp_path, orders=["belt AA\n", "bye\n"], make_treadmill=made.append) == 0
    assert run_guard(tmp_path, orders=["idle\n"], make_treadmill=made.append) == 0
    assert made == [] and take_report(tmp_path) is None


def test_the_companion_vanishing_with_the_belt_running_stops_it(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(guard, "RETRY_S", 0.01)
    pads: list[Pad] = []

    class LivePad(Pad):
        """Reports by itself: here real time passes."""

        async def connect(self) -> None:
            await super().connect()
            asyncio.get_running_loop().call_later(0.05, self._report)

        def _report(self) -> None:
            self.tick()
            if self.status.connected:
                asyncio.get_running_loop().call_later(0.05, self._report)

    def make(address: str) -> Treadmill:
        pads.append(LivePad(time.monotonic, coast_s=0.2))
        pads[-1].status.address = address
        return pads[-1]

    assert run_guard(tmp_path, orders=["idle\n", "belt AA:BB\n"], make_treadmill=make) == 0
    assert pads[0].calls == ["connect", "stop", "disconnect"]
    report = json.loads((tmp_path / REPORT_FILE).read_text("utf-8"))
    assert report["result"] == STOPPED and report["address"] == "AA:BB"
    assert take_report(tmp_path) == report
    assert take_report(tmp_path) is None, "said once"
    assert "the belt of AA:BB running" in (tmp_path / guard.LOG_FILE).read_text("utf-8")


def test_bluetooth_failing_altogether_is_reported_not_raised(tmp_path: Path):
    def make(address: str) -> Treadmill:
        raise RuntimeError("no Bluetooth adapter")

    assert run_guard(tmp_path, orders=["belt AA\n"], make_treadmill=make) == 1
    assert take_report(tmp_path)["result"] == UNREACHABLE


def test_a_damaged_report_is_dropped(tmp_path: Path):
    (tmp_path / REPORT_FILE).write_text("{not json", "utf-8")
    assert take_report(tmp_path) is None and not (tmp_path / REPORT_FILE).exists()
    (tmp_path / REPORT_FILE).write_text('{"result": "something else"}', "utf-8")
    assert take_report(tmp_path) is None


def test_every_result_has_words_for_the_dashboard():
    assert set(guard.REPORT_TEXT) == {STOPPED, ALREADY_STOPPED, UNREACHABLE, STOOD_DOWN}


# ---- the companion's end of the pipe ---------------------------------------------------

# A stand-in for the guard process: writes down what it is told, and how it ended.
ECHO = (
    "import sys, pathlib\n"
    "out = pathlib.Path(sys.argv[1])\n"
    "lines = []\n"
    "for line in sys.stdin:\n"
    "    lines.append(line.strip())\n"
    "    out.write_text(' | '.join(lines))\n"
    "out.write_text(' | '.join(lines + ['EOF']))\n"
)


def heard(path: Path, wanted: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text = path.read_text("utf-8") if path.exists() else ""
        if text == wanted:
            break
        time.sleep(0.02)
    return text


def echo_link(tmp_path: Path) -> tuple[GuardLink, Path]:
    out = tmp_path / "heard.txt"
    return GuardLink(tmp_path / "data", [sys.executable, "-c", ECHO, str(out)]), out


def test_only_changes_are_passed_on_and_an_orderly_exit_says_bye(tmp_path: Path):
    link, out = echo_link(tmp_path)
    assert link.start() and link.active
    link.watch(None)
    link.watch(None)
    link.watch("AA:BB")
    link.watch("AA:BB")
    link.watch(None)
    link.release()
    assert heard(out, "idle | belt AA:BB | idle | bye | EOF") == "idle | belt AA:BB | idle | bye | EOF"
    assert not link.active
    link.watch("AA:BB")  # after the end: nothing, and no error
    link.release()


def test_a_belt_that_could_not_be_stopped_is_handed_over_without_a_bye(tmp_path: Path):
    link, out = echo_link(tmp_path)
    assert link.start()
    link.watch("AA:BB")
    link.release(handover=True)
    assert heard(out, "belt AA:BB | EOF") == "belt AA:BB | EOF"


SRC = str(Path(__file__).resolve().parent.parent / "src")


def with_walka(monkeypatch) -> None:
    """Another Python process finds the package the way this one did."""
    monkeypatch.setenv("PYTHONPATH", SRC)


def test_the_guard_notices_the_companion_being_killed(tmp_path: Path, monkeypatch):
    with_walka(monkeypatch)
    """The real thing, as far as it goes without a treadmill: a companion
    process that is killed outright, and what its guard is left knowing."""
    out = tmp_path / "heard.txt"
    companion = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from walka.guard import GuardLink\n"
        f"link = GuardLink(Path({str(tmp_path / 'data')!r}), [sys.executable, '-c', {ECHO!r}, {str(out)!r}])\n"
        "assert link.start()\n"
        "link.watch('AA:BB')\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", companion], stdout=subprocess.PIPE)
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == b"ready"
        assert heard(out, "belt AA:BB") == "belt AA:BB"
        process.kill()
        process.wait(timeout=10)
        assert heard(out, "belt AA:BB | EOF") == "belt AA:BB | EOF", "the guard was not told that the companion died"
    finally:
        process.kill()


def test_a_guard_that_cannot_be_started_is_an_error_not_a_crash(tmp_path: Path):
    link = GuardLink(tmp_path, [str(tmp_path / "no-such-program")])
    assert link.start() is False and "could not be started" in link.error and not link.active
    link.watch("AA:BB")
    link.release()


def test_a_guard_that_has_died_is_noticed(tmp_path: Path):
    link = GuardLink(tmp_path, [sys.executable, "-c", "pass"])
    assert link.start()
    deadline = time.monotonic() + 10
    while link.active and time.monotonic() < deadline:
        time.sleep(0.02)
    for address in ("AA", None, "BB", None, "CC"):  # (a pipe takes a write or two before it says so)
        link.watch(address)
    assert not link.active and "not running" in link.error


def test_starting_the_companion_tells_an_older_guard_to_stand_down(tmp_path: Path):
    began = time.time() - 5
    asked = guard._stand_down_since(tmp_path, began)
    assert asked() is False
    link = GuardLink(tmp_path, [sys.executable, "-c", "import sys; sys.stdin.read()"])
    assert link.start()
    try:
        assert (tmp_path / STAND_DOWN_FILE).exists() and asked() is True
        assert guard._stand_down_since(tmp_path, time.time() + 5)() is False, "an older start does not count"
    finally:
        link.release()


def test_the_real_guard_starts_and_leaves_on_bye(tmp_path: Path, monkeypatch):
    """``python -m walka guard``: it loads the Bluetooth driver before it
    waits (the packaged program's files may be gone later), then goes when
    told to."""
    with_walka(monkeypatch)
    link = GuardLink(tmp_path)
    assert link.command[-3:] == ["--data-dir", str(tmp_path), "guard"]
    assert link.start()
    process = link._process
    link.watch(None)
    link.release()
    assert process is not None and process.wait(timeout=30) == 0
    assert "watching" in (tmp_path / guard.LOG_FILE).read_text("utf-8")
    assert take_report(tmp_path) is None


# ---- the companion keeps its guard informed -------------------------------------------


class Told:
    """Stands in for the GuardLink."""

    def __init__(self, starts: bool = True) -> None:
        self.error = "" if starts else "the guard could not be started: no"
        self.starts = starts
        self.said: list[object] = []

    def start(self) -> bool:
        self.said.append("start")
        return self.starts

    def watch(self, address) -> None:
        if not self.said or self.said[-1] != address:
            self.said.append(address)

    def release(self, *, handover: bool = False) -> None:
        self.said.append("handover" if handover else "bye")


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


async def looped(rig: Rig, seconds: float = 0.5) -> None:
    """Let the app's own control loop run for a moment of real time."""
    task = asyncio.create_task(rig.app._control_loop())
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_the_guard_is_told_while_a_belt_of_ours_turns_and_no_longer_after(rig: Rig):
    told = rig.app.guard = Told()

    async def scenario():
        await rig.connect()
        await rig.run(2, active=False)
        await looped(rig, 0.3)
        assert told.said == [None], "no session: nothing to guard"
        await session_running(rig, 3.2)
        assert rig.app._guarded_address() == "SIM"
        await looped(rig, 0.3)
        assert told.said == [None, "SIM"]
        await rig.app.stop()
        assert told.said == [None, "SIM", "bye"], "the belt was stopped here: the guard can go"

    run(scenario())


def test_a_walk_with_the_remote_is_not_guarded(rig: Rig):
    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(30, active=False)
        assert rig.treadmill.status.speed_kmh > 0
        assert rig.app._guarded_address() is None

    run(scenario())


def test_no_guard_where_the_belt_is_not_to_be_stopped_on_exit(rig: Rig):
    rig.config.treadmill.stop_on_exit = False

    async def scenario():
        await session_running(rig, 3.2)
        assert rig.app._guarded_address() is None

    run(scenario())


def test_a_stop_that_fails_at_exit_is_handed_to_the_guard(rig: Rig):
    told = rig.app.guard = Told()

    async def scenario():
        await session_running(rig, 3.2, seconds=60)
        rig.treadmill.fail_commands = True
        await rig.app.stop()

    run(scenario())
    assert told.said == ["SIM", "handover"]


def test_a_belt_out_of_reach_at_exit_is_handed_to_the_guard(rig: Rig):
    told = rig.app.guard = Told()

    async def scenario():
        await session_running(rig, 3.2, seconds=60)
        drop_link(rig, belt_keeps_running=True)
        await rig.run(3, target=3.2)
        sent = commands_sent(rig)
        await rig.app.stop()
        assert sent == []

    run(scenario())
    assert told.said == ["SIM", "handover"]


def test_what_the_last_guard_did_is_said_at_the_next_start(rig: Rig):
    told = rig.app.guard = Told()
    rig.app.data_dir.mkdir(parents=True, exist_ok=True)
    (rig.app.data_dir / REPORT_FILE).write_text(json.dumps({"result": STOPPED, "address": "SIM", "time": 1.0}))
    rig.app._start_guard()
    assert told.said == ["start"]
    assert rig.app.events[0]["level"] == "error"
    assert "did not close properly" in rig.app.events[0]["text"] and "stopped the belt" in rig.app.events[0]["text"]
    assert not (rig.app.data_dir / REPORT_FILE).exists()


def test_a_guard_that_does_not_start_is_said_on_the_dashboard(rig: Rig):
    rig.app.guard = Told(starts=False)
    rig.app._start_guard()
    assert rig.app.events[0]["level"] == "warning" and rig.app.events[0]["text"].startswith("No guard")


def test_selfcheck_can_try_the_guard_for_real(monkeypatch):
    """What the packaged program's self-check does: start the guard the way
    the companion does, and see it leave."""
    from walka import cli

    with_walka(monkeypatch)
    assert cli._guard_problem() == ""
    monkeypatch.setattr(guard, "guard_command", lambda data_dir: [sys.executable, "-c", "raise SystemExit(3)"])
    assert cli._guard_problem() == "the guard ended with exit code 3"
