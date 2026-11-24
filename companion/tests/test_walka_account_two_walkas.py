"""Walka opened twice on one data folder, while a walk is going.

``active-session.json`` is the walk that is still going: the Walka that
records it writes it again every half minute. A Walka that is started
makes a finished walk of what it finds there - that is how a walk comes
back after a crash. But nothing stops anybody from opening Walka a second
time while the first one records. Then the second Walka made a finished
walk of the first one's ten minutes, and its Walka account's part sent
those ten minutes. The whole walk, saved later under the same name and
start, was "there already": the Walka account kept the walk cut short,
for good.

So the Walka that records holds a lock of the system's own on an empty
file beside the checkpoint (``active-session.lock``) for as long as the
checkpoint is its own, and a Walka that is started asks that lock before
it recovers anything. Here:

- the recorder alone, with the other Walka as a process of its own (the
  lock is the system's: only another process asks it as another Walka
  does);
- a Walka that leaves another one's checkpoint alone at its start leaves
  it alone when it is closed, too: it clears the checkpoint it wrote
  itself and no other. And what a Walka left by crashing while this one
  ran is recovered before this one writes its own walk over it;
- the whole story, with two real ``App`` objects, signed in, sending on;
- a crash is recovered as it always was - the system lets go of a dead
  process's lock - and the recovered walk goes at that start;
- and a folder where no lock is to be had at all: there everything is as
  it was before there were locks. The lock never costs anybody a walk.

Two stores in ONE process on one folder are Walka "crashed and started
again" as every other test plays it: a process knows its own lock. Where
a test here needs a second Walka inside this process, it takes that
knowledge away for the moment the second one starts (``as_another_process``);
the lock itself is then asked for real, on Windows as on a Mac.
"""

from __future__ import annotations

import errno
import gc
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import walka_account_rig
from conftest import SRC
from test_walka_account_wiring import Pad, a_config, hand_in, left_running, run, walk_on
from walka import session as session_module
from walka.app import CHECKPOINT_EVERY_S, App
from walka.session import LOCK_NAME, SessionRecord, SessionStore
from walka.sources import BeaconSource
from walka_account_rig import NOW, PAD, Rig, walk_id


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)


ACTIVE = "active-session.json"
LEFT_ALONE = "a walk is still going in another Walka on this data folder: its checkpoint is left alone"


def so_far(seconds: float, start: float = NOW - 3600.0) -> SessionRecord:
    """The walk that began at ``start``, as Walka writes it down after ``seconds`` on the belt."""
    return SessionRecord(
        id=walk_id(start),
        start=start,
        end=start + seconds + 10.0,
        seconds=seconds,
        distance_m=round(seconds * 0.9, 1),
        steps=int(seconds * 1.6),
        kcal=round(seconds / 12.0, 1),
        kcal_extra=round(seconds / 20.0, 1),
        max_kmh=3.5,
        treadmill=PAD,
        samples=[[0.0, 3.2, None], [5.0, 3.5, None]],
    )


def walks_in(data: Path) -> dict[str, float]:
    """The finished walks that lie in the folder: file name -> seconds."""
    return {path.name: json.loads(path.read_text("utf-8"))["seconds"] for path in sorted((data / "sessions").glob("*"))}


class Elsewhere:
    """Another process, played inside this one: what is done ``with`` it is done by another Walka.

    A process knows which locks are its own (``session._HELD``). Another
    process knows nothing of those and has to ask the system - so in here
    this process's knowledge is put aside, and the lock is asked for real.
    What the other process comes to hold itself, it knows the next time.
    """

    def __init__(self) -> None:
        self.own: dict = {}

    def __enter__(self) -> Elsewhere:
        self._ours = session_module._HELD
        session_module._HELD = self.own
        return self

    def __exit__(self, *_exc: object) -> None:
        session_module._HELD = self._ours


def as_another_process() -> Elsewhere:
    """One more Walka, of which nothing is known yet."""
    return Elsewhere()


def held_by_this_process(data: Path) -> bool:
    """Does this process hold the lock of the walk that is still going in ``data``?"""
    hold = session_module._HELD.get(os.path.normcase(os.path.realpath(data / LOCK_NAME)))
    return hold is not None and hold.held


def lock_is_free(data: Path) -> bool:
    """Ask the system itself, with a descriptor of this test's own, and let go again at once."""
    fd = os.open(data / LOCK_NAME, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        try:
            session_module._lock(fd)
        except OSError:
            return False
        session_module._unlock(fd)
        return True
    finally:
        os.close(fd)


# ---- another Walka, as a process of its own --------------------------------------------------------

# What the child does with the folder it is given, and says on its one line of output.
#   "start": Walka is started - the store is opened as every start opens it. It says what it holds.
#   "walk":  Walka records a walk: it writes the checkpoint it is handed, says so, and walks on until
#            its input ends - or until it is killed, which is a crash.
CHILD = r'''
import json, sys
from pathlib import Path
from walka.session import SessionRecord, SessionStore

what, data = sys.argv[1], Path(sys.argv[2])
store = SessionStore(data)
if what == "walk":
    store.checkpoint(SessionRecord.from_json(json.loads(sys.argv[3])))
print(json.dumps({
    "walks": [[record.id, record.seconds] for record in store.records],
    "checkpoint": (data / "active-session.json").exists(),
}), flush=True)
if what == "walk":
    sys.stdin.read()
'''

# In real seconds: how long a child may take for what takes it a fraction of one.
CHILD_S = 60.0


def a_walka_of_its_own(tmp_path: Path, what: str, data: Path, *more: str) -> subprocess.Popen:
    script = tmp_path / "another_walka.py"
    script.write_text(CHILD, "utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, str(script), what, str(data), *more],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def started_elsewhere(tmp_path: Path, data: Path) -> dict:
    """Walka is started as a process of its own on ``data``, and closed again. Gives what it held."""
    process = a_walka_of_its_own(tmp_path, "start", data)
    out, err = process.communicate(timeout=CHILD_S)
    assert process.returncode == 0, err
    return json.loads(out)


class WalksElsewhere:
    """A Walka of its own that records a walk: its checkpoint is on disk when this is entered.

    ``crash()``: it is gone from one moment to the next, as when it is
    killed or the power fails. Nothing is saved and nothing is cleared.
    """

    def __init__(self, tmp_path: Path, data: Path, record: SessionRecord) -> None:
        self._how = (tmp_path, "walk", data, json.dumps(record.to_json()))
        self.process: subprocess.Popen = None  # type: ignore[assignment]

    def __enter__(self) -> WalksElsewhere:
        self.process = a_walka_of_its_own(*self._how)
        # (If it never says its line, it is ended: no test waits for ever.)
        self._guard = threading.Timer(CHILD_S, self.process.kill)
        self._guard.daemon = True
        self._guard.start()
        try:
            said = self.process.stdout.readline()
            assert said, self.process.stderr.read()
            assert json.loads(said)["checkpoint"] is True
        except BaseException:
            self.__exit__()  # (nothing of it is left behind when it did not start as it should)
            raise
        return self

    def crash(self) -> None:
        self.process.kill()
        self.process.wait(timeout=CHILD_S)

    def __exit__(self, *_exc: object) -> None:
        self._guard.cancel()
        if self.process.poll() is None:
            self.crash()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()


def test_a_walka_that_is_started_leaves_the_walk_that_another_one_still_records_alone(tmp_path):
    data = tmp_path / "data"
    first = SessionStore(data)
    first.checkpoint(so_far(590.0))
    written = (data / ACTIVE).read_bytes()

    # Walka is opened a second time, and closed, and opened once more.
    for _ in range(2):
        assert started_elsewhere(tmp_path, data) == {"walks": [], "checkpoint": True}
        assert (data / ACTIVE).read_bytes() == written and walks_in(data) == {}

    # The first one walks on, and writes the walk down again and again: nothing changes for a second one.
    first.checkpoint(so_far(620.0))
    assert started_elsewhere(tmp_path, data) == {"walks": [], "checkpoint": True}
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 620.0 and walks_in(data) == {}

    # It saves the whole walk, as Walka does: the file first, then the checkpoint goes.
    whole = first.add(so_far(1780.0))
    first.clear_checkpoint()
    assert walks_in(data) == {f"{whole.id}.json": 1780.0} and not (data / ACTIVE).exists()
    assert started_elsewhere(tmp_path, data) == {"walks": [[whole.id, 1780.0]], "checkpoint": False}
    # The lock's file holds nothing, and it was let go: the next walk's Walka, whichever it is, gets it.
    assert (data / LOCK_NAME).read_bytes() == b"" and lock_is_free(data)


def test_this_walka_leaves_the_walk_of_another_process_alone_and_recovers_it_once_that_one_has_crashed(
    tmp_path, caplog
):
    data = tmp_path / "data"
    record = so_far(590.0)
    with WalksElsewhere(tmp_path, data, record) as other:
        written = (data / ACTIVE).read_bytes()
        # This process is the second Walka now. Started while the other one walks, it finds the
        # checkpoint, recovers nothing, and leaves the file where it is - however often it is started.
        for _ in range(3):
            started = SessionStore(data)
            assert started.records == [] and walks_in(data) == {}
            assert (data / ACTIVE).read_bytes() == written
            # And it is closed again. Closing clears the checkpoint - its own: this one is not, and
            # until the other Walka writes it again it is all there is of that walk.
            started.clear_checkpoint()
            assert (data / ACTIVE).read_bytes() == written
        said = [r.getMessage() for r in caplog.records if r.name == "walka.session" and r.levelno >= logging.INFO]
        assert said == [LEFT_ALONE] * 3
        # Nor did closing let go of anything that is the other one's.
        assert not lock_is_free(data)

        other.crash()
        # The system lets go of a dead process's lock (Windows says: when it gets to it - so this
        # looks more than once there, which changes nothing: a start that recovers nothing leaves
        # everything as it is).
        recovered: list[SessionRecord] = []
        for _ in range(200):
            recovered = SessionStore(data).records
            if recovered:
                break
            threading.Event().wait(0.05)
        assert [(walk.id, walk.seconds) for walk in recovered] == [(record.id, 590.0)]
        assert walks_in(data) == {f"{record.id}.json": 590.0} and not (data / ACTIVE).exists()
        assert lock_is_free(data)


def test_a_second_walka_that_records_writes_its_walk_down_all_the_same_and_holds_the_lock_once_it_is_free(tmp_path):
    data = tmp_path / "data"
    first = SessionStore(data)
    first.checkpoint(so_far(590.0))
    second_walka = Elsewhere()
    with second_walka:
        second = SessionStore(data)
        assert second.records == []
        # Refused or not, a checkpoint is written (as it was before there were locks), and nothing is raised.
        second.checkpoint(so_far(45.0, start=NOW - 60.0))
        assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 45.0
        # (Written over what the first one wrote, as two Walkas always did - and nothing was made
        # of that: the first one is still at it.)
        assert second.records == [] and walks_in(data) == {}
    assert held_by_this_process(data)
    # The first one is done with its walk.
    whole = first.add(so_far(1780.0))
    first.clear_checkpoint()
    assert lock_is_free(data)
    # The second one's next checkpoint takes the lock: from now on a third Walka leaves ITS walk alone.
    with second_walka:
        second.checkpoint(so_far(75.0, start=NOW - 60.0))
    assert not lock_is_free(data)
    with as_another_process():
        assert [walk.id for walk in SessionStore(data).records] == [whole.id]
        assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 75.0
    with second_walka:
        second.clear_checkpoint()
    assert lock_is_free(data) and not (data / ACTIVE).exists()


def test_a_walka_clears_the_checkpoint_it_wrote_itself_and_no_other(tmp_path, monkeypatch):
    data = tmp_path / "data"
    first = SessionStore(data)
    first.checkpoint(so_far(590.0))
    written = (data / ACTIVE).read_bytes()
    second_walka = Elsewhere()
    with second_walka:
        second = SessionStore(data)
        # It recorded nothing. Whatever clears a checkpoint in it - closing, a walk too short to
        # keep - clears nothing here: the file is the first one's.
        second.clear_checkpoint()
        second.clear_checkpoint()
    assert (data / ACTIVE).read_bytes() == written and held_by_this_process(data)

    # What it wrote itself, it clears - though the lock is the first one's: its walk is saved, and
    # a checkpoint left behind would come back as that walk a second time.
    with second_walka:
        second.checkpoint(so_far(45.0, start=NOW - 60.0))
        second.clear_checkpoint()
    assert not (data / ACTIVE).exists()
    assert held_by_this_process(data) and not lock_is_free(data)  # (the first one's, untouched)
    # The first one writes its own down again, half a minute later at most - and clears it at its end.
    first.checkpoint(so_far(620.0))
    with second_walka:
        second.clear_checkpoint()  # (cleared once: the second one has nothing of its own there now)
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 620.0
    first.clear_checkpoint()
    assert not (data / ACTIVE).exists() and lock_is_free(data)

    # A checkpoint that could not be taken away is still this store's: the next clearing tries again.
    first.checkpoint(so_far(30.0, start=NOW - 40.0))
    really = Path.unlink

    def not_now(path, *args, **how):
        if path.name == ACTIVE:
            raise PermissionError(errno.EACCES, "in use")
        return really(path, *args, **how)

    with monkeypatch.context() as held_open:
        held_open.setattr(Path, "unlink", not_now)
        first.clear_checkpoint()
        assert (data / ACTIVE).exists()
        # (And its own it stays: the next walk's checkpoint goes over it, and nothing is "recovered".)
        first.checkpoint(so_far(31.0, start=NOW - 35.0))
        first.clear_checkpoint()
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 31.0
    first.clear_checkpoint()
    assert not (data / ACTIVE).exists() and first.records == [] and walks_in(data) == {}


def test_a_checkpoint_that_another_walka_left_by_crashing_is_not_cleared_by_one_that_runs(tmp_path):
    """Walka runs, and has nothing going. Another Walka on the folder records a walk and crashes.
    The first one is closed: it used to take that checkpoint with it, and the walk was gone. Now
    it stays for the next start to find."""
    data = tmp_path / "data"
    runs = SessionStore(data)
    record = so_far(590.0)
    with WalksElsewhere(tmp_path, data, record) as other:
        other.crash()
        runs.clear_checkpoint()  # (as closing does, and as saving a walk of its own does)
        assert (data / ACTIVE).exists() and walks_in(data) == {}
        recovered: list[SessionRecord] = []
        for _ in range(200):  # (Windows lets go of a dead process's lock "when it gets to it")
            recovered = SessionStore(data).records
            if recovered:
                break
            threading.Event().wait(0.05)
        assert [(walk.id, walk.seconds) for walk in recovered] == [(record.id, 590.0)]
        assert not (data / ACTIVE).exists() and lock_is_free(data)


def until_the_lock_is_free(data: Path) -> None:
    """A process that was killed holds nothing any more - on Windows "when the system gets to it"."""
    for _ in range(200):
        if lock_is_free(data):
            return
        threading.Event().wait(0.05)
    raise AssertionError("the lock of a process that is gone was never let go")


def test_a_walka_that_runs_recovers_what_a_crashed_one_left_before_it_writes_its_own_walk_over_it(tmp_path, caplog):
    """Two Walkas, and the one that records crashes. The belt still moves, the other one takes the
    treadmill over and records - and half a minute later its checkpoint goes where the crashed
    one's lies. No start has recovered that one: this Walka's start left it alone, rightly. So it
    is recovered now, before it is written over."""
    data = tmp_path / "data"
    record = so_far(590.0)
    with WalksElsewhere(tmp_path, data, record) as other:
        runs = SessionStore(data)  # started while the other one walks: nothing recovered
        assert runs.records == []
        other.crash()
        until_the_lock_is_free(data)
        own = so_far(30.0, start=NOW - 40.0)
        runs.checkpoint(own)
        assert [(walk.id, walk.seconds) for walk in runs.records] == [(record.id, 590.0)]
        assert walks_in(data) == {f"{record.id}.json": 590.0}
        assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 30.0 and held_by_this_process(data)
        # Once, and only what was another's: its own checkpoints are written over as ever.
        runs.checkpoint(so_far(60.0, start=NOW - 40.0))
        assert len(runs.records) == 1 and len(walks_in(data)) == 1
        said = [r.getMessage() for r in caplog.records if r.name == "walka.session" and r.levelno >= logging.INFO]
        assert said == [LEFT_ALONE, f"recovered an unfinished walk from {session_module.day_key(record.start)}"]
        runs.clear_checkpoint()
        assert not (data / ACTIVE).exists() and lock_is_free(data)


def test_what_another_store_of_this_process_wrote_is_not_taken_for_a_crashed_walk(tmp_path):
    """Two ``App`` objects on one folder in one process - as some tests have them - share the
    process's knowledge of the lock. Neither knows of the other's walk whether it is still going:
    a checkpoint is written over, as it always was, and no walk is made of it."""
    data = tmp_path / "data"
    one, other = SessionStore(data), SessionStore(data)
    one.checkpoint(so_far(590.0))
    other.checkpoint(so_far(30.0, start=NOW - 40.0))
    assert one.records == [] and other.records == [] and walks_in(data) == {}
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 30.0


# ---- the whole story -------------------------------------------------------------------------------


def test_a_second_walka_sends_nothing_of_the_walk_that_is_still_going_and_the_whole_walk_goes_once(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            active = rig.dir / ACTIVE
            first_app = hand_in(rig, tmp_path)
            await first_app.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            first = rig.account

            # Ten minutes into a walk: the checkpoint lies in the folder.
            await walk_on(rig, first_app, 600)
            ten_minutes = json.loads(active.read_text("utf-8"))
            assert 600 - CHECKPOINT_EVERY_S <= ten_minutes["seconds"] <= 600
            asked_so_far = len(rig.fake.requests)

            async def a_second_walka() -> App:
                """Walka is opened once more on the same folder: a whole ``App`` with a part of its
                own, signed in (the stored sign-in is the computer's) and with sending on (the file
                says so). Started, and come to rest."""
                with as_another_process():
                    second_app = App(
                        a_config(tmp_path),
                        rig.dir,
                        treadmill=Pad(),
                        beacon=BeaconSource(None, clock=rig.clock.mono),
                        walka_account=rig.build(attach=False),
                        wall_clock=rig.clock.wall,
                        mono_clock=rig.clock.mono,
                    )
                await second_app.start()
                rig.account = second_app.walka_account
                await rig.idle()
                piece = rig.snapshot()
                assert (piece["state"], piece["sending"]) == ("signed_in", True)
                # It has no walk, so it has nothing to send - and asked nobody (rule 3a).
                assert second_app.store.records == [] and piece["counts"]["waiting"] == 0
                rig.account = first
                await rig.idle()
                return second_app

            second_app = await a_second_walka()
            assert json.loads(active.read_text("utf-8")) == ten_minutes and walks_in(rig.dir) == {}
            assert rig.fake.walks(user) == [] and rig.pushes() == []
            assert len(rig.fake.requests) == asked_so_far

            # The second one is closed again: the first one's checkpoint lies there as it did, and
            # its lock is held as it was.
            await second_app.stop()
            assert json.loads(active.read_text("utf-8")) == ten_minutes
            assert held_by_this_process(rig.dir) and not lock_is_free(rig.dir)
            # The first walks on, and the second is opened once more.
            await walk_on(rig, first_app, 2 * CHECKPOINT_EVERY_S)
            assert json.loads(active.read_text("utf-8"))["seconds"] > ten_minutes["seconds"]
            second_app = await a_second_walka()
            assert walks_in(rig.dir) == {} and rig.fake.walks(user) == [] and rig.pushes() == []

            # Twenty more minutes, then the person steps off: the first Walka saves the whole walk.
            await walk_on(rig, first_app, 1200, then_stand=True)
            await rig.idle()
            (whole,) = first_app.store.records
            assert whole.id == ten_minutes["id"] and whole.start == ten_minutes["start"]
            assert whole.seconds > 1800 and walks_in(rig.dir) == {f"{whole.id}.json": whole.seconds}
            assert not active.exists()

            # Exactly one row, and it is the whole walk.
            (row,) = rig.fake.walks(user)
            assert row["client_id"] == whole.id and row["seconds"] == pytest.approx(whole.seconds, abs=0.5)
            assert [[sent["client_id"] for sent in rows] for rows in rig.pushes()] == [[whole.id]]
            assert rig.entry_of(user, whole)["state"] == "added"
            assert first.snapshot()["counts"]["sent"] == 1

            await second_app.stop()
            await first_app.stop()
            assert await left_running() == []
            # At the next start of one Walka there is one walk, and one row.
            await rig.restart()
            assert [(walk.id, walk.seconds) for walk in rig.store.records] == [(whole.id, whole.seconds)]
            assert [sent["seconds"] for sent in rig.fake.walks(user)] == [row["seconds"]]
            assert len(rig.pushes()) == 1

    run(scenario, 60)


def test_through_a_whole_walk_walka_holds_the_lock_without_a_gap(tmp_path):
    """Walka writes the walk down every half minute. It used to clear the checkpoint just before each
    of those: with the lock that would be letting go and taking again, twice a minute - and a Walka
    started in such a moment would get the lock, and with it the walk that is still going."""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            active = rig.dir / ACTIVE
            app = hand_in(rig, tmp_path, part=False)
            await app.start()
            await walk_on(rig, app, CHECKPOINT_EVERY_S + 5)
            hold = session_module._HELD[app.store._lock_key]
            assert hold.held and not lock_is_free(rig.dir)
            first_written = json.loads(active.read_text("utf-8"))["seconds"]

            await walk_on(rig, app, 600)
            # Twenty checkpoints later it is the same hold, never let go in between ...
            assert json.loads(active.read_text("utf-8"))["seconds"] >= first_written + 600 - CHECKPOINT_EVERY_S
            assert session_module._HELD.get(app.store._lock_key) is hold and hold.held
            # ... until the walk is saved.
            await walk_on(rig, app, 5, then_stand=True)
            assert len(app.store.records) == 1 and not active.exists()
            assert not hold.held and not held_by_this_process(rig.dir) and lock_is_free(rig.dir)
            await app.stop()
            assert await left_running() == []

    run(scenario)


# ---- a crash is recovered as it always was ---------------------------------------------------------


def test_after_a_crash_the_walk_is_recovered_and_goes_at_that_start_as_before(tmp_path):
    async def scenario():
        async with Rig(tmp_path) as rig:
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            # Ten minutes into a walk, written down - and Walka is gone. (The store it had is still
            # in memory here, as in every test that plays a crash: this process knows the lock as
            # its own, and so the Walka that "is started again" is this Walka, started again.)
            unfinished = rig.walk(600.0, seconds=590.0, add=False)
            crashed = rig.store
            crashed.checkpoint(unfinished)
            assert held_by_this_process(rig.dir) and rig.fake.walks(user) == []

            await rig.restart()
            assert rig.store is not crashed
            assert [(walk.id, walk.seconds) for walk in rig.store.records] == [(unfinished.id, 590.0)]
            assert not (rig.dir / ACTIVE).exists()
            (row,) = rig.fake.walks(user)
            assert (row["client_id"], row["seconds"]) == (unfinished.id, 590)
            # Recovered, the lock is nobody's: not this process's, and not the system's either.
            assert not held_by_this_process(rig.dir) and lock_is_free(rig.dir)

    run(scenario)


def test_a_store_that_is_dropped_with_its_walk_still_going_lets_go_of_the_lock(tmp_path):
    """Thousands of tests make stores, and a Walka that crashes clears nothing: what a store took
    must not stay open for as long as the process lives."""
    holds = []
    for number in range(50):
        data = tmp_path / f"data-{number}"
        store = SessionStore(data)
        store.checkpoint(so_far(590.0))
        holds.append(session_module._HELD[store._lock_key])
        assert holds[-1].held and not lock_is_free(data)
        del store
        gc.collect()
        assert not holds[-1].held and lock_is_free(data)
        # What it left is recovered by the next start, here or anywhere.
        with as_another_process():
            assert len(SessionStore(data).records) == 1
    assert not any(hold.held for hold in holds)


# ---- where no lock is to be had --------------------------------------------------------------------


def no_locks_here(fd: int, at: int = 0) -> None:
    raise OSError(errno.EIO, "this folder has no locks")


def test_a_folder_without_locks_is_as_it_was_before_there_were_locks(tmp_path, monkeypatch, caplog):
    data = tmp_path / "data"
    monkeypatch.setattr(session_module, "_lock", no_locks_here)
    first = SessionStore(data)
    first.checkpoint(so_far(590.0))  # (nothing raised, and the walk is written down)
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 590.0
    assert not held_by_this_process(data)
    # A start recovers what lies there - another Walka's start too: nothing tells them apart here,
    # and a crashed walk that is never recovered would be the worse mistake.
    with as_another_process():
        started = SessionStore(data)
    assert [(walk.id, walk.seconds) for walk in started.records] == [(so_far(590.0).id, 590.0)]
    assert not (data / ACTIVE).exists()
    assert LEFT_ALONE not in caplog.text
    # And a Walka clears its own checkpoint there as it always did.
    first.checkpoint(so_far(620.0))
    first.clear_checkpoint()
    assert not (data / ACTIVE).exists()
    # A checkpoint that is another's is written over there as it always was: without a lock
    # nothing says that the other Walka is gone, and no walk is made of what it wrote.
    with as_another_process():
        SessionStore(data).checkpoint(so_far(590.0))
    first.checkpoint(so_far(30.0, start=NOW - 40.0))
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 30.0
    assert len(first.records) == 0 and len(walks_in(data)) == 1  # (the one walk that the start above recovered)


@pytest.mark.parametrize("what", ["a folder in its place", "cannot be opened"])
def test_a_lock_file_that_cannot_be_opened_stops_no_checkpoint_and_no_recovery(tmp_path, monkeypatch, what):
    data = tmp_path / "data"
    data.mkdir()
    if what == "a folder in its place":
        (data / LOCK_NAME).mkdir()
    else:
        really = os.open

        def a_folder_that_takes_no_new_file(path, *args, **how):
            if str(path).endswith(LOCK_NAME):
                raise PermissionError(errno.EACCES, "read-only")
            return really(path, *args, **how)

        monkeypatch.setattr(session_module.os, "open", a_folder_that_takes_no_new_file)
    first = SessionStore(data)
    first.checkpoint(so_far(590.0))
    first.checkpoint(so_far(620.0))
    assert json.loads((data / ACTIVE).read_text("utf-8"))["seconds"] == 620.0
    with as_another_process():
        started = SessionStore(data)
    assert [(walk.id, walk.seconds) for walk in started.records] == [(so_far(0).id, 620.0)]
    assert not (data / ACTIVE).exists()
    first.clear_checkpoint()  # (and clearing is as quiet as ever)


@pytest.mark.parametrize("locks_work", [True, False])
def test_windows_says_the_same_of_a_lock_that_is_held_and_of_a_folder_without_locks_and_walka_tells_them_apart(
    tmp_path, monkeypatch, locks_work
):
    """On Windows a refused lock is "permission denied" whatever the reason. Walka then tries a
    second byte that nobody else locks: if that one can be taken, locks work in this folder and
    the first one is held - a walk is still going. If not, there are no locks here, and the walk
    is recovered as before. (Played here with Windows' answers, on any system.)"""
    data = tmp_path / "data"
    data.mkdir()
    (data / ACTIVE).write_text(json.dumps(so_far(590.0).to_json()), "utf-8")  # (as a Walka left it, or is at it)
    tried: list[int] = []

    def as_windows_answers(fd: int, at: int = session_module._LOCK_AT) -> None:
        tried.append(at)
        if at == session_module._LOCK_AT or not locks_work:
            raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(session_module, "_ON_WINDOWS", True)
    monkeypatch.setattr(session_module, "_lock", as_windows_answers)
    monkeypatch.setattr(session_module, "_unlock", lambda fd, at=0: tried.append(-at))
    with as_another_process():
        started = SessionStore(data)
    probe = session_module._PROBE_AT
    assert probe != session_module._LOCK_AT
    if locks_work:
        assert started.records == [] and (data / ACTIVE).exists()
        assert tried == [session_module._LOCK_AT, probe, -probe]  # (and the second byte is let go at once)
    else:
        assert len(started.records) == 1 and not (data / ACTIVE).exists()
        assert tried == [session_module._LOCK_AT, probe]
