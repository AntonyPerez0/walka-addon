"""A rig for the tests of Walka's sending of walks to a Walka account.

It puts together, on this computer and without any network but
127.0.0.1, everything the part (``walka.walka_account.service.WalkaAccount``)
is handed when Walka runs:

- ``rig.clock`` - a made-up clock, wall and monotonic. It moves only when
  a test moves it (``rig.advance``, ``clock.jump_wall``).
- ``rig.app`` - a stand-in for the ``App``: a real ``SessionStore`` on a
  folder under ``tmp_path``, a treadmill that only has a ``kind``, and
  the clock. ``rig.walk(...)`` saves a walk the way Walka does (its id is
  the LOCAL time of its start; its file holds the eighteen keys);
  ``rig.rewrite(...)`` changes a walk's file by hand.
- ``rig.keystore`` - a store for secrets in memory that writes down every
  call, and can be told to refuse. ``lasting=True`` stands for Windows'
  Credential Manager or a Mac's keychain (it outlives ``rig.restart()``);
  ``lasting=False`` for a system without one.
  It holds two kinds of thing, and tells them apart by the entry's name:
  the key of a sign-in (``secrets()``, under ``rig.entry``) and the mark
  that is made when the question before sending is answered (``marks()``,
  under ``rig.mark_entry``; ``rig.mark()`` is the one of this address).
  ``calls`` are the calls for both.
  ``LatestFirst`` is a lock to put into a ``Keys`` (``keys._lock``) for the
  tests of what waits behind a call the store sits on: of those who wait,
  the one who came last goes first.
- ``rig.fake`` - the stand-in for the accounts (``fake_accounts``).
- the opener: it writes the address down (``rig.opened``) and, unless
  ``rig.browser_comes`` is False, lets the stand-in's "browser" follow it
  to Walka's listener (``rig.pages`` holds the pages that came back).
- ``rig.wait`` - the one wait the part is given. It writes down every
  call with its time limit, and a wait with a limit ends only when the
  test says so: ``rig.advance(seconds)`` moves the clock and ends the
  waits that are due, ``rig.wait.time_is_up()`` ends one by hand.
  ``rig.wait.limits()`` are the limits asked for so far: an empty list is
  the proof that there was no timer.
- ``await rig.idle()`` - wait until the part's tasks have nothing left to
  do. After every press of a button and every wake, await this.

How it is used, the house way::

    def test_something(tmp_path):
        async def scenario():
            async with Rig(tmp_path) as rig:
                user = rig.person("walker@mail.example")
                rig.walk(ago_s=3600)
                await rig.sign_in()
                await rig.switch_on("all")
                assert len(rig.fake.walks(user)) == 1

        run(scenario)

And in the test file, once, so that EVERY test ends with the same look
at what must never happen::

    @pytest.fixture(autouse=True)
    def nothing_outside_the_list_and_nothing_told(caplog):
        walka_account_rig.before_test(caplog)
        yield
        walka_account_rig.after_test(caplog)

``after_test`` fails the test if the stand-in saw a request outside the
list of what Walka may ask, if a key of a sign-in stands in any file of
the data folder, in the snapshot or in the log, if a mark stands anywhere
but in ``walka-account.json`` (it is a key to nothing, and is kept like
one all the same), if the log names an e-mail address, a Walka account's
id or the accounts' address, or if a task was left with an error nobody
read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fake_accounts
from fake_accounts import FakeAccounts, Seen
from walka.session import SessionRecord, SessionStore
from walka.walka_account import ledger
from walka.walka_account.https import parse_origin
from walka.walka_account.keystore import KeyStoreError, MemoryKeyStore, entry_name, mark_name
from walka.walka_account.service import WalkaAccount

NOW = 1795300000.0  # 21 November 2026, 22:26:40 UTC
OK = (200, {"ok": True})
PAD = "UREVO SpaceWalk E3"  # what a real pad calls itself

MADE: list[Rig] = []  # every rig of the test that runs

# How the name of a mark's entry ends: the sign-in's entry is named after the address alone.
MARK_ENDS = mark_name("x")[len(entry_name("x")) :]


def is_mark(name: str) -> bool:
    """Is this the name of an entry for a mark - not for the key of a sign-in?"""
    return name.endswith(MARK_ENDS)


def run(scenario):
    """The house way: one scenario, one loop, half a minute at most."""
    return asyncio.run(asyncio.wait_for(scenario(), 30))


class Clock:
    """Both clocks of the program. They move only when a test moves them."""

    def __init__(self, wall: float = NOW, mono: float = 5000.0) -> None:
        self._wall = wall
        self._mono = mono

    def wall(self) -> float:
        return self._wall

    def mono(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        """Time passes: both clocks move."""
        self._wall += seconds
        self._mono += seconds

    def jump_wall(self, seconds: float) -> None:
        """Somebody sets the computer's clock: the wall clock alone moves."""
        self._wall += seconds


class CountingKeyStore(MemoryKeyStore):
    """A store for secrets in memory that writes down every call made to it.

    ``calls``: each call as (what, name). ``writes``: each key of a
    sign-in written, with how many requests the accounts had seen by then
    (so a test can tell that a key was stored BEFORE a request was made).
    ``marks_seen``: every mark ever written. ``refuse``: what -> one of
    the store's four words; while it is there, that call raises it - for
    every entry, or only for those whose names are in ``only`` (if it is
    not empty). ``block``: what -> a ``threading.Event``; while it is
    there and not set, that call sits in the store, as a Mac's keychain can.
    """

    def __init__(self, *, lasting: bool = True, requests_seen: Any = lambda: 0) -> None:
        super().__init__()
        self.lasting = lasting
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, int]] = []
        self.marks_seen: set[str] = set()
        self.refuse: dict[str, str] = {}
        self.only: set[str] = set()
        self.block: dict[str, threading.Event] = {}
        self._requests_seen = requests_seen

    def _note(self, what: str, name: str) -> None:
        self.calls.append((what, name))
        if self.only and name not in self.only:
            return
        gate = self.block.get(what)
        if gate is not None:
            gate.wait(10)
        if what in self.refuse:
            raise KeyStoreError(self.refuse[what])

    def read(self, name: str) -> str | None:
        self._note("read", name)
        return super().read(name)

    def write(self, name: str, secret: str) -> None:
        self._note("write", name)
        if is_mark(name):
            self.marks_seen.add(secret)
        else:
            self.writes.append((secret, self._requests_seen()))
        super().write(name, secret)

    def delete(self, name: str) -> None:
        self._note("delete", name)
        super().delete(name)

    def secrets(self) -> list[str]:
        """The keys of sign-ins that are stored this instant. (Not the marks: see ``marks``.)"""
        return [secret for name, secret in self._entries.items() if not is_mark(name)]

    def marks(self) -> list[str]:
        """The marks that are stored this instant."""
        return [secret for name, secret in self._entries.items() if is_mark(name)]


class LatestFirst:
    """A lock that lets whoever came LAST go first. (A real lock promises no order at all among
    those who wait: this one takes the order that hurts.)"""

    def __init__(self) -> None:
        self._turn = threading.Condition()
        self._held = False
        self.waiting: list[object] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        me = object()
        with self._turn:
            self.waiting.append(me)
            try:
                mine = self._turn.wait_for(
                    lambda: not self._held and self.waiting[-1] is me, None if timeout < 0 else timeout
                )
                if mine:
                    self._held = True
                return mine
            finally:
                self.waiting.remove(me)
                self._turn.notify_all()

    def release(self) -> None:
        with self._turn:
            self._held = False
            self._turn.notify_all()

    def locked(self) -> bool:
        return self._held


class Waiting:
    """One call of the wait: its time limit, and how it ended (``came``: None while it waits)."""

    def __init__(self, limit: float | None, due: float | None) -> None:
        self.limit = limit
        self.due = due  # by the made-up monotonic clock
        self.came: bool | None = None
        self.up: asyncio.Future[None] = asyncio.get_running_loop().create_future()


class Waits:
    """The wait handed to the part: ``await wait(event, limit)`` -> did the event come?

    A wait without a limit ends when its event comes. A wait WITH a limit
    also ends when the test says its time is up - never by itself.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.calls: list[Waiting] = []

    async def __call__(self, event: asyncio.Event, limit: float | None) -> bool:
        call = Waiting(limit, None if limit is None else self._clock.mono() + limit)
        self.calls.append(call)
        if event.is_set():
            call.came = True
            return True
        waiter = asyncio.ensure_future(event.wait())
        came = False  # (and so it stays if the wait is cancelled)
        try:
            await asyncio.wait({waiter, call.up}, return_when=asyncio.FIRST_COMPLETED)
            came = event.is_set()
        finally:
            try:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)  # no task is left behind
            finally:
                # Said last, with nothing waited for after it: once a call
                # has "come back", whoever called is at work again.
                call.came = came
        return came

    def mark(self) -> int:
        """How many calls there were so far: hand it to ``limits`` to look only at what came after."""
        return len(self.calls)

    def limits(self, since: int = 0) -> list[float]:
        """Every time limit a wait was asked with, oldest first. Empty: nothing ever waited on a clock."""
        return [call.limit for call in self.calls[since:] if call.limit is not None]

    def waiting(self) -> list[Waiting]:
        """The calls that wait this instant."""
        return [call for call in self.calls if call.came is None]

    def returning(self) -> bool:
        """Was a wait told that its time is up, and has not come back yet?"""
        return any(call.came is None and call.up.done() for call in self.calls)

    def time_is_up(self, limit: float | None = None) -> Waiting:
        """End the newest wait that has a time limit (this one, if given): its time is up."""
        for call in reversed(self.calls):
            if call.came is None and call.limit is not None and limit in (None, call.limit) and not call.up.done():
                call.up.set_result(None)
                return call
        raise AssertionError("no wait with such a time limit is waiting")

    def end_due(self) -> int:
        """End every wait whose time is up by the made-up clock. Gives how many."""
        ended = 0
        for call in self.calls:
            if call.came is None and call.due is not None and call.due <= self._clock.mono() and not call.up.done():
                call.up.set_result(None)
                ended += 1
        return ended


class StubApp:
    """What the part reads of the ``App``: the walks, the treadmill's kind, the clocks."""

    def __init__(self, data_dir: Path, clock: Clock, kind: str) -> None:
        self.data_dir = data_dir
        self.store = SessionStore(data_dir)
        self.treadmill = SimpleNamespace(kind=kind)
        self.wall = clock.wall
        self.mono = clock.mono


def walk_id(start: float) -> str:
    """A walk's id as Walka makes it: the LOCAL time of its start (session.py, ``snapshot``)."""
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(start))


def strings_in(thing: Any, _seen: set[int] | None = None, _depth: int = 0) -> list[str]:
    """Every text an object holds, however deep: its attributes, and what lists, sets and dicts in it hold.

    For "no key stays in memory". (Loops, tasks and modules are not looked into.)
    """
    seen = _seen if _seen is not None else set()
    if id(thing) in seen or _depth > 12:
        return []
    seen.add(id(thing))
    if isinstance(thing, str):
        return [thing]
    if isinstance(thing, bytes):
        return [thing.decode("utf-8", "replace")]
    if isinstance(thing, (int, float, bool, type(None), asyncio.AbstractEventLoop, asyncio.Future, type)):
        return []
    found: list[str] = []
    if isinstance(thing, dict):
        for key, value in thing.items():
            found += strings_in(key, seen, _depth + 1) + strings_in(value, seen, _depth + 1)
        return found
    if isinstance(thing, (list, tuple, set, frozenset)):
        for item in thing:
            found += strings_in(item, seen, _depth + 1)
        return found
    if type(thing).__module__.split(".")[0] in ("builtins", "asyncio", "threading", "logging", "ssl", "socket"):
        return []
    for value in getattr(thing, "__dict__", {}).values():
        found += strings_in(value, seen, _depth + 1)
    for name in getattr(type(thing), "__slots__", ()):
        found += strings_in(getattr(thing, name, None), seen, _depth + 1)
    return found


class Rig:
    """Everything around one ``WalkaAccount``. Use it with ``async with``, inside a scenario."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        lasting: bool = True,
        kind: str = "ftms",
        start: bool = True,
        accounts_behind_s: float = 0.0,
    ) -> None:
        self.dir = Path(tmp_path) / "data"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = Clock()
        # ``accounts_behind_s``: how far the accounts' clock is behind this
        # computer's - for a walk that is "in the days to come" over there.
        self.accounts_behind_s = accounts_behind_s
        self.fake = FakeAccounts(wall=lambda: self.clock.wall() - self.accounts_behind_s)
        self.keystore = CountingKeyStore(lasting=lasting, requests_seen=lambda: len(self.fake.requests))
        self.wait = Waits(self.clock)
        self.app = StubApp(self.dir, self.clock, kind)
        self.opened: list[str] = []  # every address the browser was opened at
        self.pages: list[tuple[int, dict[str, str], str]] = []  # what the browser ended on
        self.browser_comes = True  # False: the browser is opened and nobody ever comes back
        # An event: the person takes their time in the browser, and comes back when it is set. (For a
        # look at Walka while it waits: without it, whether the look or the browser is first is luck.)
        self.browser_waits: asyncio.Event | None = None
        self.people: dict[str, str] = {}  # user id -> e-mail address
        self.account: WalkaAccount = None  # type: ignore[assignment]  (made when the stand-in has its port)
        self.accounts: list[WalkaAccount] = []  # every one made, restarts included
        self.keys_seen: set[str] = set()  # every secret the store ever held
        self._start = start
        self._browsers: list[asyncio.Future] = []
        MADE.append(self)

    async def __aenter__(self) -> Rig:
        await self.fake.start()
        origin = parse_origin(self.fake.origin)
        assert origin is not None
        self.origin = origin
        self.entry = entry_name(origin.text)  # the name of the stored sign-in
        self.mark_entry = mark_name(origin.text)  # the name of the mark beside it, while sending is on
        self.account = self.build()
        if self._start:
            self.account.start()
            await self.idle()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for browser in self._browsers:
            browser.cancel()
        await asyncio.gather(*self._browsers, return_exceptions=True)
        for account in self.accounts:
            account.cancel()
            await account.close()
        await self.fake.close()

    # ---- the parts ---------------------------------------------------------------------------

    @property
    def store(self) -> SessionStore:
        return self.app.store

    def build(self, *, attach: bool = True, **changes: Any) -> WalkaAccount:
        """A new ``WalkaAccount`` on the same data folder, attached to the rig's app. Not started.

        ``attach=False``: for a test of the whole program, which hands the
        part to a real ``App`` - that attaches it itself, and starts it.
        ``changes`` go to ``WalkaAccount`` in place of what the rig would hand in.
        """
        how: dict[str, Any] = dict(
            keystore=self.keystore,
            opener=self.opener,
            wall=self.clock.wall,
            mono=self.clock.mono,
            wait=self.wait,
        )
        how.update(changes)
        account = WalkaAccount(self.origin, self.fake.key, self.dir, **how)
        if attach:
            account.attach(self.app)
        self.accounts.append(account)
        return account

    async def restart(self, *, start: bool = True, kind: str | None = None, **changes: Any) -> WalkaAccount:
        """Walka is closed and started again: the walks are read from the folder anew, the part is made anew.

        A lasting store keeps the sign-in; one that is not lasting is emptied, as memory is.
        """
        await self.idle()
        self.account.cancel()
        await self.account.close()
        if not self.keystore.lasting:
            self.keystore._entries.clear()
        self.app = StubApp(self.dir, self.clock, kind or self.app.treadmill.kind)
        self.account = self.build(**changes)
        if start:
            self.account.start()
            await self.idle()
        return self.account

    def opener(self, url: str) -> None:
        self.opened.append(url)
        if self.browser_comes:
            self._browsers.append(asyncio.ensure_future(self._browse(url)))

    async def _browse(self, url: str) -> None:
        if self.browser_waits is not None:
            await self.browser_waits.wait()
        self.pages.append(await self.fake.browser(url))

    async def idle(self) -> None:
        """Wait until nothing is left to do: the browser is back, and every task of the part rests."""
        for _ in range(100_000):
            pending = [browser for browser in self._browsers if not browser.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self.account.idle()
            await asyncio.sleep(0)
            busy = self.account.at_work() or self.wait.returning()
            if not busy and all(browser.done() for browser in self._browsers):
                for secret in self.keystore.secrets():
                    self.keys_seen.add(secret)
                return
        raise AssertionError("the part never came to rest")

    async def advance(self, seconds: float) -> None:
        """Time passes: both clocks move, the waits that are due end, and what follows is waited for."""
        self.clock.advance(seconds)
        self.wait.end_due()
        await self.idle()

    # ---- people and walks --------------------------------------------------------------------

    def person(self, email: str = "walker@mail.example", **how: Any) -> str:
        """Somebody who can sign in (see ``FakeAccounts.add_user``). Gives the Walka account's id."""
        user = self.fake.add_user(email, **how)
        self.people[user] = email
        return user

    def walk(
        self,
        ago_s: float = 3600.0,
        *,
        treadmill: str = PAD,
        seconds: float = 600.0,
        add: bool = True,
        **changes: Any,
    ) -> SessionRecord:
        """Save a walk that began ``ago_s`` seconds ago, the way Walka saves one. Gives its record.

        Walks of one test need starts of their own (the account keeps one
        walk for each second). ``add=False``: only the record is made.
        """
        start = round(self.clock.wall() - ago_s, 1)
        record = SessionRecord(
            id=walk_id(start),
            start=start,
            end=round(start + seconds + 30.0, 1),
            seconds=seconds,
            distance_m=round(seconds * 0.9, 1),
            steps=int(seconds * 1.6),
            kcal=round(seconds / 12.0, 1),
            kcal_extra=round(seconds / 20.0, 1),
            max_kmh=3.5,
            avg_hr=None,
            max_hr=None,
            source="manual",
            beacon_session=None,
            steps_source="estimate",
            distance_source="speed",
            calories_source="speed",
            treadmill=treadmill,
            samples=[[0.0, 3.2, None], [5.0, 3.5, None]],
        )
        for name, value in changes.items():
            setattr(record, name, value)
        return self.store.add(record) if add else record

    def file_of(self, record: SessionRecord) -> Path:
        return self.store.dir / f"{record.id}.json"

    def rewrite(self, record: SessionRecord, *, drop: tuple[str, ...] = (), **changes: Any) -> None:
        """Change a walk's FILE by hand - the record Walka has loaded stays as it is."""
        path = self.file_of(record)
        data = json.loads(path.read_text("utf-8"))
        for name in drop:
            data.pop(name, None)
        data.update(changes)
        path.write_text(json.dumps(data, separators=(",", ":")), "utf-8")

    def walk_files(self) -> dict[str, bytes]:
        """Every file of the walks' folder, byte for byte."""
        return {path.name: path.read_bytes() for path in sorted(self.store.dir.iterdir())}

    # ---- the buttons -------------------------------------------------------------------------

    async def sign_in(self, user: str | None = None, way: str = "github") -> None:
        """Press "Sign in", let the browser come back, and wait for what follows."""
        if user is not None:
            self.fake.next_browser_user(user)
        assert self.account.sign_in(way) == OK
        await self.idle()

    async def question(self) -> dict | None:
        """Press the button that prepares the question; gives the question as the snapshot shows it."""
        assert self.account.prepare_question() == OK
        await self.idle()
        return self.snapshot()["question"]

    async def switch_on(self, send: str = "all") -> None:
        """Ask the question and answer it. What is then sent has gone when this returns."""
        question = await self.question()
        assert question is not None, self.snapshot()
        assert self.account.switch_on(question["id"], send) == OK
        await self.idle()

    async def saved(self) -> None:
        """A walk was saved: wake the sender, and wait for what it does."""
        self.account.walk_saved()
        await self.idle()

    # ---- what a test looks at ----------------------------------------------------------------

    def snapshot(self) -> dict:
        return self.account.snapshot()

    def book(self) -> dict:
        """The file ``walka-account.json`` as it is now."""
        return ledger.Ledger(self.dir).read()

    def account_in_book(self, user: str) -> dict:
        return self.book()["addresses"][self.origin.text]["accounts"][user]

    def entry_of(self, user: str, record: SessionRecord) -> dict | None:
        """What the file says became of a walk, or None: it waits."""
        return self.account_in_book(user)["walks"].get(ledger.walk_key(record.id, record.start))

    def asked(self, path: str = "", *, method: str | None = None) -> list[Seen]:
        """The requests the PROGRAM made (not the browser's), those to such a path."""
        browser = ("/auth/v1/authorize", "/landed")
        return [
            seen
            for seen in self.fake.requests
            if seen.path not in browser and path in seen.path and method in (None, seen.method)
        ]

    def pushes_seen(self) -> list[Seen]:
        """Every request that sent walks, oldest first, as the stand-in saw it (its headers too)."""
        return self.asked("/rest/v1/walks", method="POST")

    def pushes(self) -> list[list[dict]]:
        """The rows of every request that sent walks, oldest first."""
        return [seen.json for seen in self.pushes_seen()]

    def mark(self) -> str | None:
        """The mark that the store holds for this address this instant, or None."""
        return self.keystore._entries.get(self.mark_entry)

    def marks_seen(self) -> set[str]:
        """Every mark that was in play: those the store ever held, and those it holds (however they got there)."""
        return self.keystore.marks_seen | set(self.keystore.marks())

    def files_but_the_book(self) -> str:
        """Everything that stands in any file under the data folder - but for ``walka-account.json`` itself."""
        return "\n".join(
            path.read_bytes().decode("utf-8", "replace")
            for path in sorted(self.dir.rglob("*"))
            if path.is_file() and path.name != ledger.FILE_NAME
        )

    def needles(self) -> set[str]:
        """Every key of a sign-in that was in play: codes, access and refresh tokens, the other site's tokens."""
        return set(self.fake.secrets_issued()) | self.keys_seen | set(self.keystore.secrets())

    def files_text(self) -> str:
        """Everything that stands in any file under the data folder."""
        return "\n".join(
            path.read_bytes().decode("utf-8", "replace") for path in sorted(self.dir.rglob("*")) if path.is_file()
        )


# ---- for the fixture of a test file --------------------------------------------------------------


def before_test(caplog: Any) -> None:
    fake_accounts.collect_violations()  # what an earlier test left
    MADE.clear()
    caplog.set_level(logging.DEBUG)


def after_test(caplog: Any) -> None:
    """What must hold after EVERY test. See the top of this file."""
    assert fake_accounts.collect_violations() == []
    records = caplog.get_records("setup") + caplog.get_records("call")
    written = "\n".join(logging.Formatter().format(record) for record in records)
    # A task nobody waited for, an error nobody read, a coroutine never run: asyncio says so in the log.
    assert [record.getMessage() for record in records if record.name == "asyncio" and record.levelno >= 30] == []
    for rig in MADE:
        keys = {needle for needle in rig.needles() if needle}
        assert [key for key in keys if key in rig.files_text()] == [], "a key of a sign-in stands in a file"
        assert [key for key in keys if key in written] == [], "a key of a sign-in stands in the log"
        told = {rig.fake.key, rig.fake.origin, rig.fake.origin.split("//")[-1], *rig.people, *rig.people.values()}
        told.discard("")
        assert [word for word in told if word in written] == [], "the log names the account or the accounts' address"
        # The mark: in walka-account.json (that is its point) and in the system's store, and nowhere else.
        marks = {mark for mark in rig.marks_seen() if mark}
        assert [mark for mark in marks if mark in rig.files_but_the_book()] == [], "a mark stands in another file"
        assert [mark for mark in marks if mark in written] == [], "a mark stands in the log"
        for account in rig.accounts:
            shown = json.dumps(account.snapshot())
            assert [key for key in keys if key in shown] == [], "a key of a sign-in stands in the snapshot"
            assert [mark for mark in marks if mark in shown] == [], "a mark stands in the snapshot"
            assert [user for user in rig.people if user in shown] == [], "the snapshot names the Walka account's id"
            assert rig.fake.key not in shown
