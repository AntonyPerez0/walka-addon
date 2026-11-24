"""Walka as a whole, with its Walka account's part: closing, starting again, and made-up walks.

Three things the part must never do to the program around it:

- Recording a walk never waits for the Walka account, and closing Walka
  never does either - not while the accounts sit on an answer, not while
  the system's store for secrets waits for a click nobody gives, and not
  while a name is being looked up that never answers. Here the walk is
  recorded by Walka's own loop, as it runs, and closing is timed: of the
  ``App``, of the loop it ran on, and of Walka as a process of its own.
- The walk that is still going (``active-session.json``) is never sent
  and never so much as opened by the part. After a crash Walka makes a
  finished walk of it when it next starts, before anything else has
  begun - and that walk is found, and goes.
- A walk made with the simulated treadmill never reaches a Walka account.
  Here such walks are made by each of the three ways to that treadmill,
  through the command line's own code, and one more is recovered after a
  "crash" - and then they lie among real walks, with a real treadmill
  and "all of them" chosen: not one is counted, and not one is sent.

(What test_walka_account_wiring.py has already, and is not said twice
here: closing in the middle of a send and of a sign-in, a walk saved
while closing, a walk saved during a pass, and the part handed to a
Walka on the simulated treadmill, where every button refuses.)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp
import pytest

import walka_account_rig
from conftest import SRC
from test_app import Rig as HouseRig
from test_walka_account_nokeys import THE_BRIEFS_LIST, asked_of_the_accounts
from test_walka_account_wiring import (
    CLOSES_WITHIN_S,
    KEY_VARIABLE,
    URL_VARIABLE,
    closed_without_running_out_of_time,
    dashboard,
    free_port,
    hand_in,
    left_running,
    run,
    until_served,
    walk_on,
    walks_shown,
)
from walka import cli
from walka import session as session_module
from walka.app import CHECKPOINT_EVERY_S, TICK_S, App
from walka.session import SessionRecord, SessionStore
from walka.treadmill.simulated import SimulatedTreadmill
from walka.walka_account import keystore, ledger, service
from walka.walka_account.https import parse_origin
from walka_account_rig import OK, PAD, CountingKeyStore, Rig, walk_id


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)
    for rig in walka_account_rig.MADE:
        assert asked_of_the_accounts(rig.fake) <= THE_BRIEFS_LIST


MADE_UP = "Simulated pad"
ZERO = {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}
# Where the part's own code lies.
THE_PART = str(Path(service.__file__).parent)


async def until(happened, what: str, within_s: float = 20.0) -> None:
    """Wait - in real time, a hundredth of a second at a time - until something has happened."""
    loop = asyncio.get_running_loop()
    give_up = loop.time() + within_s
    while not happened():
        assert loop.time() < give_up, f"it never came to this: {what}"
        await asyncio.sleep(0.01)


def sent_to(rig: Rig, user: str) -> list[str]:
    """The walks that are in the Walka account, by their names on this computer."""
    return sorted(row["client_id"] for row in rig.fake.walks(user))


# ---- recording and closing, while somebody sits on an answer ---------------------------------------


def somebody_walks(rig: Rig, app: App) -> list[tuple[float, float]]:
    """From now on somebody is on the pad, and Walka's own loop records them: with each of its ticks
    a fifth of a second passes by the made-up clock. Gives the list that every tick is written into:
    (when it ran, by the loop's clock; the seconds of the walk it then showed)."""
    ticks: list[tuple[float, float]] = []
    loop = asyncio.get_running_loop()
    pad, tick = app.treadmill, app.tick
    pad.status.connected = True

    async def a_tick() -> None:
        rig.clock.advance(TICK_S)
        pad.status.speed_kmh, pad.status.updated_at = 3.0, rig.clock.mono()
        await tick()
        if app.recorder is not None:
            ticks.append((loop.time(), app.recorder.seconds))

    app.tick = a_tick  # type: ignore[method-assign]
    return ticks


# What counts as the loop having been held up, in real seconds. (Walka's loop ticks many times a second;
# the room is for a slow machine. The store of this test sits on its answer for ten.)
HELD_UP_S = 8.0

THE_STORED_SIGN_IN = "the-stored-sign-in-1"
# The mark that was made when the question before sending was answered: in the file beside "sending is
# on", and in the store for secrets beside the sign-in. (Without it the file's "on" is not believed.)
THE_MARK = "the-mark-of-that-answer-1"
SOMEBODY = "5a1c0b6e-0d6f-4f0e-9d53-6b1d5b9e7a11"
# Accounts that are reached by a name - which no computer has. (Every other test asks 127.0.0.1, and
# no name is looked up for that.)
BY_NAME = "https://accounts.example"


def an_earlier_run_signed_in_and_switched_on(data: Path, address: str, when: float) -> None:
    """What an earlier run of Walka left in its file: somebody signed in there, with sending on for all walks."""

    def an_earlier_run(book: dict) -> None:
        ledger.sign_in(book, address, SOMEBODY, "github", "walker@mail.example")
        ledger.set_sending(book, address, SOMEBODY, True, {"what": "all", "at": when}, THE_MARK)

    ledger.Ledger(data).change(an_earlier_run)


@pytest.mark.parametrize("who", ["the store for secrets", "the accounts", "the look-up of a name"])
def test_the_walk_is_recorded_tick_by_tick_and_walka_closes_at_once_while_an_answer_is_sat_on(
    tmp_path, monkeypatch, caplog, who
):
    """Somebody walks, a walk is on its way to the Walka account - and
    whoever was asked last never answers. Walka's own loop goes on
    recording, tick by tick; and when Walka is closed, the walk is saved
    and nothing waits: not ``App.stop()``, and not the loop's own end."""
    stuck = threading.Event()  # (the answer of the store, or of the look-up: it never comes while the test runs)
    stopped: list[float] = []
    looked_up: list[tuple[str, bool]] = []  # the name, and whether the thread that asked is one Python waits for
    look_up = socket.getaddrinfo

    def a_look_up_that_never_answers(host, *args, **how):
        if host != "accounts.example":
            return look_up(host, *args, **how)
        looked_up.append((host, not threading.current_thread().daemon))
        stuck.wait(60)
        raise socket.gaierror("no computer has this name")  # (and nobody is ever asked for it: not by a test)

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            if who == "the look-up of a name":
                # An earlier run left a sign-in at accounts that are reached by a name, and sending on.
                origin = parse_origin(BY_NAME)
                assert origin is not None
                rig.origin, rig.entry = origin, keystore.entry_name(origin.text)
                rig.mark_entry = keystore.mark_name(origin.text)
                an_earlier_run_signed_in_and_switched_on(rig.dir, BY_NAME, rig.clock.wall() - 60.0)
                rig.keystore._entries[rig.entry] = THE_STORED_SIGN_IN
                rig.keystore._entries[rig.mark_entry] = THE_MARK
                monkeypatch.setattr(socket, "getaddrinfo", a_look_up_that_never_answers)
                app = hand_in(rig, tmp_path)
                await app.start()
                await rig.idle()
                user = SOMEBODY
                piece = app.snapshot()["walka_account"]
                assert (piece["state"], piece["sending"], piece["host"]) == ("signed_in", True, "accounts.example")
                assert rig.keystore.calls == [] and looked_up == []  # (nothing waits: nobody was asked)
            else:
                app = hand_in(rig, tmp_path)
                await app.start()
                user = rig.person()
                await rig.sign_in()
                await rig.switch_on("all")
            waits = rig.walk(7200)
            if who == "the store for secrets":
                # The key Walka asks with has run out, so the next send begins in the store for secrets -
                # which sits on its answer, as a Mac's keychain does while it waits for a click.
                await rig.advance(3700)
                rig.keystore.block["read"] = stuck
            elif who == "the accounts":
                gate = rig.fake.hold("/rest/v1/walks", method="POST")
            loop = asyncio.get_running_loop()
            # Somebody has been on the pad for half a minute already (a walk shorter than twenty seconds
            # is not kept) - and from here on it is Walka's own loop that records them, in real time.
            await walk_on(rig, app, 30)
            ticks = somebody_walks(rig, app)
            await until(lambda: len(ticks) >= 5, "Walka's own loop records the walk")

            asked_at = len(ticks)
            rig.account.walk_saved()
            if who == "the store for secrets":
                await until(lambda: ("read", rig.entry) in rig.keystore.calls, "the store for secrets is asked")
            elif who == "the accounts":
                await gate.arrived.wait()
            else:
                # The stored sign-in is read, a new key is to be fetched with it - and the name of the
                # accounts is looked up first, by somebody who never answers.
                await until(lambda: looked_up != [], "the name of the accounts is looked up")
            # Ten more seconds of walking, by Walka's clock, while that answer is sat on ... (In real time
            # that is a second or so. Whoever sits on the answer does so for ten at least - the store of
            # this test - and that is the room a slow machine has here.)
            await until(lambda: len(ticks) >= asked_at + 50, "the walk goes on being recorded")
            assert app.snapshot()["walka_account"]["busy"] == "sending" and rig.fake.walks(user) == []
            assert not stuck.is_set()
            if who == "the look-up of a name":
                # (Asked on a thread that Python does not wait for when the program ends.)
                assert set(looked_up) == {("accounts.example", False)} and rig.fake.requests == []
                # (The stored sign-in, and the mark beside it: once, before the first walk of a run goes.)
                assert rig.keystore.calls == [("read", rig.entry), ("read", rig.mark_entry)]
            # ... tick by tick: each added its fifth of a second to the walk, and none was left out.
            seconds = [walked for _when, walked in ticks]
            assert all(abs(after - before - TICK_S) < 1e-6 for before, after in zip(seconds, seconds[1:], strict=False))
            assert app.snapshot()["session"]["seconds"] == pytest.approx(seconds[-1], abs=2 * TICK_S)
            # And the loop was never held up for it: the ticks came one after the other. (Had it waited
            # for the store, nothing would have run for ten seconds; for the accounts, nothing ever.)
            times = [when for when, _walked in ticks]
            assert max(after - before for before, after in zip(times, times[1:], strict=False)) < HELD_UP_S

            began = loop.time()
            await app.stop()
            assert loop.time() - began < CLOSES_WITHIN_S and closed_without_running_out_of_time(caplog)
            # The walk that was being recorded is on disk, whole; no task of Walka's is left.
            kept = [record for record in SessionStore(rig.dir).records if record.id != waits.id]
            assert len(kept) == 1 and kept[0].treadmill == PAD and kept[0].seconds >= 30 + len(ticks) * TICK_S - 1.0
            assert not (rig.dir / "active-session.json").exists()
            if who == "the accounts":
                await asyncio.wait_for(gate.left.wait(), 10)  # whoever asked has gone away
                gate.release()
            assert await left_running() == []
            # Nothing went, nothing was written down as gone, and the sign-in is still there for the next start.
            assert rig.fake.walks(user) == [] and rig.account_in_book(user)["walks"] == {}
            assert rig.account_in_book(user)["sending"] is True and len(rig.keystore.secrets()) == 1
            assert app.treadmill.tripped == []
            # (And of where the accounts are, the log says nothing - not even when their name leads nowhere.)
            assert not [record for record in caplog.records if "accounts.example" in record.getMessage()]
        # (The stand-in for the accounts is closed by now, too: what is left is the loop's own end.)
        stopped.append(time.monotonic())

    try:
        run(scenario)
        # The loop itself ended at once, too: nothing of the part's is on a thread that Python waits
        # for when a loop ends. (The store's thread still sits there, on its question.)
        assert time.monotonic() - stopped[0] < CLOSES_WITHIN_S
        assert who == "the accounts" or not stuck.is_set()
    finally:
        stuck.set()


# ---- Walka as a process of its own, with something that never answers ------------------------------

# What runs in the child: Walka's command line, whole - with a treadmill that is listened to and has
# somebody on it (no test has a Bluetooth radio), and with the one thing that never answers. A walk of
# a second is kept there, so that the test need not walk for twenty.
CHILD = r'''
import socket
import sys
import threading
import time
from pathlib import Path

from walka import cli
from walka import session
from walka.treadmill import TreadmillStatus
from walka.walka_account import keystore

what, marks = sys.argv[1], Path(sys.argv[2])
never = threading.Event()
session.MIN_SESSION_SECONDS = 1.0


class Pad:
    kind = "ftms"

    def __init__(self):
        self._status = TreadmillStatus(name="UREVO SpaceWalk E3", connected=True)
        self.address = None
        self.units = "auto"
        self.on_event = None

    @property
    def status(self):
        if self._status.connected:
            self._status.speed_kmh, self._status.updated_at = 3.0, time.monotonic()
        return self._status

    async def connect(self):
        self._status.connected = True

    async def disconnect(self):
        self._status.connected = False

    async def discover(self, timeout):
        return []


class Store(keystore.MemoryKeyStore):
    """The system's store for secrets, with a sign-in in it - and, if that is what is tried, a
    question to the person that nobody answers."""

    kind = "test"
    lasting = True

    def read(self, name):
        (marks / "the store was asked").write_text("")
        if what == "store":
            never.wait()
            (marks / "the store answered").write_text("")
        return "the-mark-of-that-answer-1" if name.endswith(" - sending") else "the-stored-sign-in-1"


look_up = socket.getaddrinfo


def a_look_up_that_never_answers(host, *args, **how):
    if host == "accounts.example":
        (marks / "the name was asked").write_text("")
        never.wait()
        (marks / "the name answered").write_text("")
    return look_up(host, *args, **how)


socket.getaddrinfo = a_look_up_that_never_answers
cli._build_treadmill = lambda cfg, simulate: Pad()
keystore.open_keystore = Store
sys.exit(cli.main(sys.argv[3:]))
'''

# How long the child may take to end, in real seconds, once it is told to. It takes a fraction of one;
# the room is for a slow machine. What a close that waited would take: the store's own time limit is
# fifteen seconds, and the look-up's thread never ends at all.
ENDS_WITHIN_S = 10.0


def a_saved_walk(data: Path, start: float) -> SessionRecord:
    """A walk as Walka saves one, in a data folder that no Walka is running on."""
    record = SessionRecord(
        id=walk_id(start),
        start=start,
        end=start + 630.0,
        seconds=600.0,
        distance_m=540.0,
        steps=960,
        kcal=50.0,
        kcal_extra=30.0,
        max_kmh=3.5,
        treadmill=PAD,
        samples=[[0.0, 3.2, None], [5.0, 3.5, None]],
    )
    return SessionStore(data).add(record)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX signals: on Windows nothing ends a child the way a log-off does, so this is not tried there",
)
@pytest.mark.parametrize("what", ["store", "name"])
def test_walka_as_a_process_ends_at_once_with_the_walk_saved_though_something_never_answers(tmp_path: Path, what: str):
    """The real thing: Walka's command line in a process of its own,
    signed in, sending on, a walk waiting - and the store for secrets, or
    the look-up of the accounts' name, never answers. Told to end, it
    saves the walk that is being recorded and is gone: the thread that
    still waits is simply left behind."""
    data, marks = tmp_path / "data", tmp_path / "marks"
    marks.mkdir()
    (tmp_path / "child.py").write_text(CHILD, "utf-8")
    # (With the store stuck nothing is ever asked; the address is one where nobody listens. The look-up
    # is of a name that no computer has, and the child's own stand-in for the look-up never lets it out.)
    address = f"http://127.0.0.1:{free_port()}" if what == "store" else BY_NAME
    port = free_port()

    # What an earlier run left: somebody signed in, sending on for all walks, and one walk not gone yet.
    # (The child runs on the computer's own clock: so does this.)
    now = time.time()
    waiting = a_saved_walk(data, now - 3600.0)

    an_earlier_run_signed_in_and_switched_on(data, address, now - 60.0)
    written = (data / ledger.FILE_NAME).read_bytes()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env[URL_VARIABLE], env[KEY_VARIABLE] = address, "test-key"
    command = [sys.executable, str(tmp_path / "child.py"), what, str(marks)]
    command += ["-v", "--no-browser", "--no-capture", "--port", str(port), "--data-dir", str(data)]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    asked = marks / f"the {what} was asked"
    state: dict[str, Any] = {}
    with open(tmp_path / "out.txt", "w+") as out, open(tmp_path / "err.txt", "w+") as err:
        process = subprocess.Popen(command, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 60  # (a few seconds are needed; the rest is room for a slow machine)
            while time.monotonic() < deadline:
                assert process.poll() is None, f"exited early: {(tmp_path / 'err.txt').read_text()}"
                try:
                    with opener.open(f"http://127.0.0.1:{port}/api/state", timeout=2) as response:
                        state = json.load(response)
                except OSError:
                    time.sleep(0.1)
                    continue
                if asked.exists() and state["session"] and state["session"]["seconds"] >= 2.0:
                    break
                time.sleep(0.1)
            # Walka is up, somebody walks, the walk that waited is on its way - and stuck.
            assert state and state["treadmill"]["kind"] == "ftms" and state["session"]["seconds"] >= 2.0
            piece = state["walka_account"]
            assert (piece["state"], piece["sending"], piece["busy"]) == ("signed_in", True, "sending")
            assert asked.exists() and not (marks / f"the {what} answered").exists()

            began = time.monotonic()
            process.send_signal(signal.SIGTERM)  # a log-out, a shutdown
            process.wait(timeout=30)
            took = time.monotonic() - began
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        out.seek(0)
        err.seek(0)
        said = out.read() + err.read()
    assert process.returncode == 0, said
    assert took < ENDS_WITHIN_S, f"Walka took {took:.1f} s to end"
    assert "Traceback" not in said and "did not close in time" not in said, said
    assert not (marks / f"the {what} answered").exists()  # (it never did: Walka did not wait for it)
    assert (marks / "the store was asked").exists() is True

    # The walk that was being recorded is saved, whole, and Walka got to the end of its close-down.
    earlier, kept = SessionStore(data).records
    assert earlier.id == waiting.id and kept.treadmill == PAD and kept.seconds >= 2.0
    assert not (data / "active-session.json").exists() and (data / "config.json").exists()
    # Nothing went and nothing changed: the file of the Walka account is as the earlier run left it.
    assert (data / ledger.FILE_NAME).read_bytes() == written
    # And the stored sign-in, which the child did read from its store, stands in nothing it wrote or said.
    kept_log = (data / cli.LOG_FILE).read_text("utf-8")
    assert "Walka has its Walka account's part" in kept_log and " DEBUG " in kept_log
    files = [path for path in sorted(data.rglob("*")) if path.is_file()]
    everything = "\n".join(path.read_bytes().decode("utf-8", "replace") for path in files)
    for word in (THE_STORED_SIGN_IN, THE_MARK, SOMEBODY, "walker@mail.example", "accounts.example", "test-key"):
        assert word not in said and word not in kept_log, word
    assert THE_STORED_SIGN_IN not in everything and "test-key" not in everything


# ---- the walk that is still going ------------------------------------------------------------------


class Opens:
    """Every file under one folder that is opened while this stands, by anybody, on any thread - and
    whether code of the part's was at work when it was. (Python tells an "audit hook" of every file it
    opens. A hook cannot be taken away again, so there is one, and it looks only while somebody asks.)"""

    _watching: list[Opens] = []
    _hooked = False

    def __init__(self, folder: Path) -> None:
        self.folder = str(folder)
        self.seen: list[tuple[str, bool]] = []  # (the file, under the folder; was it the part?)

    def __enter__(self) -> Opens:
        if not Opens._hooked:
            sys.addaudithook(Opens._hook)
            Opens._hooked = True
        Opens._watching.append(self)
        return self

    def __exit__(self, *_exc: object) -> None:
        Opens._watching.remove(self)

    @staticmethod
    def _hook(event: str, args: tuple) -> None:
        if event != "open":
            return
        try:
            if not Opens._watching:
                return
            path = args[0]
            if not isinstance(path, (str, bytes)):
                return
            name = os.path.abspath(os.fsdecode(path))
            for watcher in Opens._watching:
                if name.startswith(watcher.folder + os.sep):
                    watcher.seen.append((Path(name).relative_to(watcher.folder).as_posix(), Opens._by_the_part()))
        except Exception:
            # An error here would be an error of whoever opened the file - in any later test, and to
            # the very end of Python, when the names this needs are no longer there.
            pass

    @staticmethod
    def _by_the_part() -> bool:
        frame = sys._getframe(2)
        while frame is not None:
            if frame.f_code.co_filename.startswith(THE_PART):
                return True
            frame = frame.f_back
        return False

    def by_the_part(self) -> list[str]:
        return [name for name, the_part in self.seen if the_part]

    def by_others(self) -> list[str]:
        return [name for name, the_part in self.seen if not the_part]


async def walka_is_gone_from_one_moment_to_the_next(app: App) -> None:
    """A crash, a kill, the power: nothing is saved and nobody is told. (Its tasks are ended here by
    hand, as the end of the process ends them.)"""
    for task in app._tasks:
        task.cancel()
    await asyncio.gather(*app._tasks, return_exceptions=True)
    app._tasks.clear()
    app.walka_account.cancel()
    await app.walka_account.close()


def test_the_walk_that_is_still_going_is_never_opened_by_the_part_and_recovered_after_a_crash_it_goes(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            active = rig.dir / "active-session.json"
            app = hand_in(rig, tmp_path)
            await app.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            with Opens(rig.dir) as opens:
                earlier = rig.walk(7200)
                # Somebody is on the pad. After half a minute Walka writes the walk down, unfinished.
                await walk_on(rig, app, CHECKPOINT_EVERY_S + 15)
                unfinished = json.loads(active.read_text("utf-8"))
                assert unfinished["treadmill"] == PAD and unfinished["seconds"] >= CHECKPOINT_EVERY_S
                # While it lies there the part does all it ever does with files: a walk is sent, the
                # question counts the walks from their files, sending is switched off and on.
                await rig.saved()
                assert sent_to(rig, user) == [earlier.id]
                assert rig.account.switch_off() == OK
                await rig.idle()
                await rig.switch_on("all")
                assert rig.snapshot()["counts"] == {**ZERO, "sent": 1}
                await walk_on(rig, app, CHECKPOINT_EVERY_S + 15)
                assert json.loads(active.read_text("utf-8"))["seconds"] >= 2 * CHECKPOINT_EVERY_S
                await walka_is_gone_from_one_moment_to_the_next(app)
                assert active.is_file() and len(list((rig.dir / "sessions").glob("*.json"))) == 1

                # Walka is started again. It makes a finished walk of what it finds - before anything
                # else has begun - and that walk is found, and goes.
                again = hand_in(rig, tmp_path)
                recovered = again.store.records[-1]
                assert not active.exists() and (recovered.id, recovered.treadmill) == (unfinished["id"], PAD)
                assert recovered.seconds >= 2 * CHECKPOINT_EVERY_S and sent_to(rig, user) == [earlier.id]
                assert again.snapshot()["walka_account"]["counts"] == {**ZERO, "sent": 1, "waiting": 1}
                await again.start()
                await rig.idle()
                assert sent_to(rig, user) == sorted([earlier.id, recovered.id])
                assert rig.entry_of(user, recovered)["state"] == "added"
                (row,) = [row for row in rig.fake.walks(user) if row["client_id"] == recovered.id]
                assert row["seconds"] == pytest.approx(recovered.seconds, abs=0.5)

                # And once more with a walk under way, to the very end.
                await walk_on(rig, again, CHECKPOINT_EVERY_S + 15)
                assert active.is_file()
                await rig.saved()
                await again.stop()
                assert not active.exists() and len(SessionStore(rig.dir).records) == 3
            assert await left_running() == []

            # The part opened the files of the walks it sent and counted, and its own book ...
            mine = opens.by_the_part()
            assert {f"sessions/{earlier.id}.json", f"sessions/{recovered.id}.json", ledger.FILE_NAME} <= set(mine)
            # ... and never the walk that was still going - which Walka itself wrote and read all the while.
            assert [name for name in mine if "active-session" in name] == []
            assert [name for name in opens.by_others() if name.startswith("active-session.json")]
            # (Only finished walks ever went: the two, each once.)
            assert sorted(sent["client_id"] for rows in rig.pushes() for sent in rows) == sent_to(rig, user)

    run(scenario)


# ---- made-up walks ---------------------------------------------------------------------------------


async def a_made_up_walk_by_way_of(how: str, data: Path, monkeypatch) -> None:
    """Walka, run by its command line's own code on the simulated treadmill - reached ``how`` - with
    the accounts' address in the environment all the while. Somebody "walks" for a moment, Walka is
    closed, and the walk is saved."""
    flags = {"--demo": ["--demo"], "--simulate": ["--simulate", "--no-capture"], "config.json": ["--no-capture"]}[how]
    settings = data / "config.json"
    if how == "config.json":
        # As config.json can say, with neither switch.
        kept = json.loads(settings.read_text("utf-8")) if settings.exists() else {}
        kept.setdefault("treadmill", {})["kind"] = "simulated"
        settings.write_text(json.dumps(kept), "utf-8")
    port = free_port()
    args = cli.build_parser().parse_args([*flags, "--no-browser", "--port", str(port), "--data-dir", str(data)])
    there = f"http://127.0.0.1:{port}"
    before = len(list((data / "sessions").glob("*.json")))
    task = asyncio.create_task(cli._run(args))
    async with aiohttp.ClientSession() as session:
        state = await until_served(session, port)
        assert state["treadmill"]["kind"] == "simulated" and "walka_account" not in state
        if how != "--demo":
            # (The demo has a made-up walker. Here the simulated pad's remote is pressed, on the dashboard.)
            async with session.post(f"{there}/api/treadmill/press", json={"button": "start"}) as response:
                assert response.status == 200
        for _ in range(1500):
            async with session.get(f"{there}/api/state") as response:
                state = await response.json()
            if state["session"] and state["session"]["seconds"] >= 0.8:
                break
            await asyncio.sleep(0.02)
        assert state["session"] and state["session"]["seconds"] >= 0.8, "nobody got the simulated belt moving"
    task.cancel()  # what Ctrl+C does
    assert await asyncio.wait_for(task, 30) == 0
    assert len(list((data / "sessions").glob("*.json"))) == before + 1


def test_made_up_walks_by_every_way_to_the_simulated_treadmill_lie_among_real_ones_and_none_is_counted_or_sent(
    tmp_path, monkeypatch
):
    # (A made-up walk of a second is kept here, so that the test need not "walk" for twenty - three times.)
    monkeypatch.setattr(session_module, "MIN_SESSION_SECONDS", 0.5)
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 0.1)
    monkeypatch.setattr(cli, "DEMO_WALK_STARTS_AFTER_S", 0.2)
    treadmills: list[Any] = []
    parts: list[Any] = []
    stores: list[CountingKeyStore] = []
    build_treadmill, build_part = cli._build_treadmill, cli._build_walka_account

    def treadmill(cfg, simulate):
        treadmills.append(build_treadmill(cfg, simulate))
        treadmills[-1].countdown_s = 0.1  # (the real countdown is three seconds)
        treadmills[-1].ramp_kmh_per_s = 20.0
        return treadmills[-1]

    def part(*args, **how):
        parts.append(build_part(*args, **how))
        return parts[-1]

    def open_keystore():
        stores.append(CountingKeyStore(lasting=True))
        return stores[-1]

    monkeypatch.setattr(cli, "_build_treadmill", treadmill)
    monkeypatch.setattr(cli, "_build_walka_account", part)
    monkeypatch.setattr(keystore, "open_keystore", open_keystore)

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            data, sessions = rig.dir, rig.dir / "sessions"
            monkeypatch.setenv(URL_VARIABLE, rig.fake.origin)
            monkeypatch.setenv(KEY_VARIABLE, rig.fake.key)

            # The three ways to the simulated treadmill, each through the command line's own code.
            for how in ("--demo", "--simulate", "config.json"):
                await a_made_up_walk_by_way_of(how, data, monkeypatch)
            assert len(treadmills) == 3 and all(isinstance(made, SimulatedTreadmill) for made in treadmills)
            # Not one of those Walkas had the part, whatever the environment said; nobody was asked.
            assert parts == [None, None, None] and stores == [] and rig.fake.requests == []
            assert not (data / ledger.FILE_NAME).exists()
            monkeypatch.delenv(URL_VARIABLE)
            monkeypatch.delenv(KEY_VARIABLE)

            # A fourth: Walka crashes in the middle of a walk on the simulated treadmill, and makes a
            # finished walk of what it finds when it is next started.
            house = HouseRig(tmp_path)
            await house.connect()
            await house.walk()
            await house.run(CHECKPOINT_EVERY_S + 10, signal=False)
            left_behind = json.loads((data / "active-session.json").read_text("utf-8"))
            assert left_behind["treadmill"] == MADE_UP and left_behind["seconds"] >= 20

            # Now the treadmill is a real one - config.json no longer says otherwise - and the part is
            # there. The made-up walks lie among real ones: two from earlier days, one walked now.
            kept = json.loads((data / "config.json").read_text("utf-8"))
            kept["treadmill"]["kind"] = "ftms"
            (data / "config.json").write_text(json.dumps(kept), "utf-8")
            app = hand_in(rig, tmp_path)
            made_up = [record for record in app.store.records if record.treadmill == MADE_UP]
            assert len(made_up) == 4 == len(app.store.records) and left_behind["id"] in {walk.id for walk in made_up}
            assert {walk.source for walk in made_up} == {"wow", "free"}  # (the demo's has a game session to it)
            for walk in made_up:
                assert json.loads((sessions / f"{walk.id}.json").read_text("utf-8"))["treadmill"] == MADE_UP
            await app.start()
            real = [rig.walk(2 * 86400), rig.walk(86400)]
            await walk_on(rig, app, 60, then_stand=True)
            real.append(app.last_record)
            real_ids = sorted(walk.id for walk in real)
            assert len(app.store.records) == 7 and all(walk.treadmill == PAD for walk in real)

            user = rig.person()
            await rig.sign_in()
            # Not counted: three walks wait, and the question says three - with nothing set apart.
            assert rig.snapshot()["counts"] == {**ZERO, "waiting": 3}
            with Opens(data) as opens:
                question = await rig.question()
                assert (question["waiting"], question["unnamed"], question["held"]) == (3, 0, 0)
                # "All of them" - and not one made-up walk goes.
                assert rig.account.switch_on(question["id"], "all") == OK
                await rig.idle()
            # The part did not so much as open their files: Walka's own list says which treadmill made a walk.
            assert {name for name in opens.by_the_part() if name.startswith("sessions/")} == {
                f"sessions/{walk.id}.json" for walk in real
            }
            assert sent_to(rig, user) == real_ids
            assert sorted(sent["client_id"] for rows in rig.pushes() for sent in rows) == real_ids
            assert MADE_UP not in json.dumps(rig.pushes())
            assert rig.snapshot()["counts"] == {**ZERO, "sent": 3}
            # Nothing is written down about them either, and History does not say they have a copy there.
            written_down = sorted(rig.account_in_book(user)["walks"])
            assert written_down == sorted(ledger.walk_key(walk.id, walk.start) for walk in real)
            async with dashboard(app) as client:
                shown = {walk["id"]: walk["in_walka_account"] for walk in await walks_shown(client)}
            assert shown == {**{walk.id: False for walk in made_up}, **{walk.id: True for walk in real}}

            # However often the sender is woken, and at the next start: still not one.
            before = len(rig.fake.requests)
            for _ in range(3):
                await rig.saved()
            await app.stop()
            again = hand_in(rig, tmp_path)
            await again.start()
            await rig.idle()
            assert again.snapshot()["walka_account"]["counts"] == {**ZERO, "sent": 3}
            assert len(rig.fake.requests) == before and sent_to(rig, user) == real_ids
            await again.stop()
            assert await left_running() == []

    run(scenario, 90)
