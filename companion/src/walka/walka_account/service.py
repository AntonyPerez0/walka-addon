"""The Walka account's part of Walka: the object that is handed to the ``App``.

``WalkaAccount`` holds what the part knows - who is signed in, whether
sending is on, what became of each walk - and does what the buttons of
the Walka-account card ask for. The ``App`` calls ``attach``, ``start``,
``walk_saved``, ``snapshot``, ``in_account``, ``in_other_account``,
``cancel`` and ``close``;
the dashboard's routes call the actions (``sign_in``, ``sign_out``,
``look_again``, ``prepare_question``, ``switch_on``, ``switch_off``,
``notice_ok``).

The rules it keeps, each with its reason:

- An action answers at once. The work goes on in a task of this object's
  own and shows in the snapshot: the dashboard must never wait for the
  network, and neither must the walk that is being recorded.
- Every task is in one set, and ``cancel()`` ends them all - but for the
  few that only forget: a sign-out that takes the key out of the store,
  "off" on its way into the file. Those ask nobody, and ``close()`` gives
  them its one second. Closing Walka never waits for the Walka account,
  and after ``cancel()`` every action refuses.
- ``epoch`` is a number that goes up whenever somebody signs in or out,
  the sign-in ends, or sending is switched on or off. Every piece of work
  remembers the number it began under and looks at it again after every
  wait, before every request, and - inside the thread, under the store's
  own lock - before the key of a new sign-in is written. Work that began
  under an older number does nothing more: no walk goes to a Walka account
  that was signed out of a moment ago.
- One thing does not go by that number: a refresh token the accounts have
  just renewed. The old one is spent, so the new one is written to the
  store whenever its sign-in still stands - also when the work that asked
  for it is not wanted any more, because sending was switched off
  meanwhile. Only a sign-out, or another sign-in, makes that key unwanted
  (looked at inside the thread, under the store's lock, in memory and in
  the file).
- The access token lives in memory, with the moment it runs out by the
  monotonic clock (the wall clock of a computer may be wrong). The
  refresh token is read from the system's store for secrets immediately
  before each refresh and written back at once: none is kept here.
- Signing in never switches sending on. Only the question does, and the
  question belongs to one Walka account and one sign-in.
- Signing out says "signed out" at once and never fails. Whether the
  sign-in then really left the system's store is another matter: the
  file says "it may still be there" from before the store is asked until
  the store has said that it is gone. When the store says no - or Walka
  was closed before it answered - the card says so, with a button that
  tries once more. Nothing tries by itself, and a start never touches
  the store: the entry is named after the address, not the data folder,
  and may by then be the sign-in of another one. For the same reason the
  button looks at the file before it deletes: the other Walka on this
  data folder may have signed in since.
- A copy of a walk stays in the Walka account it was sent to, whoever
  is signed in afterwards. Which walks have a copy in a Walka account of
  this address that is not the one signed in now is kept in memory,
  from the file as it was last read or written - so that the question
  before a walk is deleted can say so without asking anybody.
- What went wrong is said with a word from a fixed list, never with an
  error's own text: that could hold an address or a key.
- Nothing here writes to the Live view or names the Walka account, its
  e-mail address or a key in the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .. import __version__
from . import calls, ledger
from .calls import WAYS, CallError, Signed
from .https import Client, Origin
from .keystore import Keys, KeyStoreError, entry_name
from .ledger import Ledger, LedgerError
from .sender import MADE_UP, Halt, Sender, Walk, halt_of, read_walks
from .signin import SignIn
from .threads import off_loop, wait_for_event

log = logging.getLogger(__name__)

# An access token is taken for run out this long before it does: a request
# that is on its way when the token ends would be refused.
ACCESS_MARGIN_S = 30.0
# How long a question can be answered.
QUESTION_GOOD_FOR_S = 600.0
# How long close() waits for what is left.
CLOSE_WITHIN_S = 1.0
# Walks named one by one in the snapshot, at most.
MOST_SHOWN = 20

THREAD_NAME = "walka-account-file"

# What the snapshot can say of the last piece of work. Nothing else is ever said.
LAST_WORDS = ("", "sent", "nothing", "later", "stopped", "too_old", "full", "not_open", "certificate", "no_answer")
# Those of them that are true of the Walka account itself, whoever asked and whenever: they stay
# said when sending is stopped. (The others are about one try, and go with it.)
STANDS = ("too_old", "full")
# What it can say of the last try to sign in, or of the stored sign-in.
PROBLEMS = (
    "",
    "refused",
    "failed",
    "timed_out",
    "no_store",
    "certificate",
    "no_answer",
    "no_stored_sign_in",
    "store_refused",
)
# What it can say of a sign-in that this Walka took out of the system's store: "sign_in" - it may
# still be there.
LEFT = ("", "sign_in")
# How a sign-in ended -> the word for the card.
_PROBLEM_OF = {
    "signed_in": "",
    "cancelled": "",
    "refused": "refused",
    "failed": "failed",
    "timed_out": "timed_out",
    "no_store": "no_store",
    "certificate": "certificate",
    "no_answer": "no_answer",
}

_OK = (200, {"ok": True})

Wait = Callable[[asyncio.Event, "float | None"], Awaitable[bool]]


async def _wait(event: asyncio.Event, limit_s: float | None) -> bool:
    """Wait for the event, for at most ``limit_s`` seconds (None: for as long as it takes). True: it came."""
    return await wait_for_event(event, limit_s)


class _Stale(Exception):
    """A change that is not wanted any more: somebody signed out, or Walka is closing."""


@dataclass(frozen=True)
class View:
    """What ``walka-account.json`` says of this address, as this Walka last saw it."""

    user: str | None = None  # the Walka account's id. It leaves this object for the file only.
    way: str = ""
    email: str = ""
    sending: bool = False
    chosen: dict | None = None
    notice: str = ""
    walks: dict = field(default_factory=dict)


def _view_of(book: dict, origin_text: str) -> View:
    place = book.get("addresses", {}).get(origin_text)
    notice = place.get("notice") if isinstance(place, dict) else None
    found = ledger.signed_in(book, origin_text)
    if found is None:
        return View(notice=notice if isinstance(notice, str) else "")
    user, account = found
    chosen = account.get("chosen") if isinstance(account.get("chosen"), dict) else None
    email = account.get("email") if isinstance(account.get("email"), str) else ""
    way = account.get("way") if isinstance(account.get("way"), str) else ""
    walks = account.get("walks") if isinstance(account.get("walks"), dict) else {}
    # Without an e-mail address nobody could tell WHICH Walka account the
    # walks go to: then sending is off, whatever the file says.
    sending = account.get("sending") is True and chosen is not None and bool(email)
    return View(user, way, email, sending, chosen, "", walks)


def _copies_of(view: View) -> frozenset[str]:
    """The walks of which a copy is in the Walka account this view is of."""
    return frozenset(key for key, entry in view.walks.items() if ledger.a_copy(entry))


def _choice(view: View) -> tuple[Any, Any] | None:
    """What was chosen, while sending is on: ("all", None) or ("from", the moment)."""
    if not view.sending or view.chosen is None:
        return None
    return view.chosen.get("what"), view.chosen.get("from")


def _same_text(given: str, kept: str) -> bool:
    """Are the two the same? Told in a time that does not depend on where they differ."""
    try:
        return secrets.compare_digest(given.encode("utf-8"), kept.encode("utf-8"))
    except UnicodeEncodeError:
        return False  # (half a character, as a request's JSON can hold one: that is nobody's id)


def walk_key(walk_id: Any, start: Any) -> str | None:
    """A walk's name in the file, or None for an id or a start that is none (a file can hold anything)."""
    if not isinstance(walk_id, str) or isinstance(start, bool) or not isinstance(start, (int, float)):
        return None
    try:
        if not math.isfinite(start):
            return None
        return ledger.walk_key(walk_id, start)
    except OverflowError:
        return None


class WalkaAccount:
    """Signing in to a Walka account and sending walks to it. See the top of this file."""

    def __init__(
        self,
        origin: Origin,
        key: str,
        data_dir: Path,
        *,
        keystore: Any,
        opener: Callable[[str], None],
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        wait: Wait | None = None,
        client_factory: Callable[[Origin], Client] = Client,
        version: str = __version__,
    ) -> None:
        # Nothing here belongs to a loop: the object is made before one runs.
        self.origin = origin
        self.key = key
        self.version = version
        self.client = client_factory(origin)
        self.keys = keystore if isinstance(keystore, Keys) else Keys(keystore)
        self.wait: Wait = wait if wait is not None else _wait
        self.sender = Sender(self)
        self._entry = entry_name(origin.text)
        self._opener = opener
        self._own_wall = wall
        self._own_mono = mono
        self._ledger = Ledger(Path(data_dir))
        self._app: Any = None

        # For the sender (and nobody else outside this file):
        self.view = View()
        self.epoch = 0
        # Which sign-in this is: it goes up whenever who is signed in here changes (somebody signs in or
        # out, the sign-in ends or is lost) - and not when sending is switched. A key that was renewed
        # for an earlier sign-in is not written to the store; one for this sign-in always is.
        self._sign_in_no = 0
        # How many changes of the file this Walka has made: a read of the file that was under way
        # while one of them was written may show the file as it was before, and is made again.
        self._wrote = 0
        self.stopping = False
        self.latch = ""  # "stop this run": the word why, until Walka is started again
        self.schema_known = False  # the database's version was asked in this run
        self.wake: asyncio.Event | None = None

        self._access: tuple[str, float] | None = None  # the access token, and when it runs out (monotonic)
        self._signin: SignIn | None = None  # the sign-in that waits for the browser
        self._listeners: set[SignIn] = set()  # every sign-in whose listener is not closed yet
        self._question: dict | None = None
        self._first = "unknown"
        self._problem = ""
        # A sign-in that this Walka took out of the system's store may still be in it: "sign_in".
        # Said in the card, from the file at a start and from the store's own answer afterwards.
        self._left = ""
        self._marked = False  # the file says so, as far as this Walka knows
        self._trying_again = False  # the card's "Try again" is at work: one delete at a time
        # The walks of which a copy is in a Walka account of this address that is not signed in now.
        self._elsewhere: frozenset[str] = frozenset()
        self._asking = 0
        self._sending = False
        self._last: dict[str, Any] = {"word": "", "at": 0.0, "added": 0}
        self._apart: dict[str, Walk] = {}  # walks that are not sent, and why: for this run only
        self._skipped: set[str] = set()  # walks to try again when Walka is next started
        self._rev = 0
        self._cache: tuple[Any, dict] | None = None
        self._trouble = ""  # the file is a newer Walka's, or cannot be read: the part is off
        self._tasks: set[asyncio.Task] = set()
        self._forgetting: set[asyncio.Task] = set()
        self._resting: dict[asyncio.Task, asyncio.Event] = {}
        self._idle_waiters: list[asyncio.Future] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._sender_task: asyncio.Task | None = None
        self._closed = False
        self._read_at_start()

    def _read_at_start(self) -> None:
        """The file, once. (Blocking, and small.) Nothing is asked of anybody."""
        try:
            book = self._ledger.read()
        except LedgerError as error:
            self._trouble = error.code
            return
        view = _view_of(book, self.origin.text)
        if view.user is not None and not self.keys.lasting:
            # The sign-in was kept in memory, by a Walka that has been
            # closed since: nobody is signed in. Said quietly - it is
            # what the card said would happen.
            try:
                self._ledger.change(lambda book: ledger.sign_out(book, self.origin))
            except LedgerError:
                pass  # in memory it is so all the same
            view = View()
        self.view = view
        # Read off the file, and no more: the store is not asked whether the entry is still there.
        # (A store in memory holds nothing of a Walka that was closed: there the word says nothing.)
        self._marked = ledger.is_left_in_store(book, self.origin)
        if self._marked and view.user is None and self.keys.lasting:
            self._left = "sign_in"
        self._elsewhere = ledger.copies_elsewhere(book, self.origin, but=view.user)

    # ---- what the App calls ------------------------------------------------------------------

    def attach(self, app: Any) -> None:
        """Keep the app: its walks, its treadmill and its clocks are looked at afresh each time."""
        self._app = app

    @property
    def usable(self) -> bool:
        """False while the treadmill is the simulated one, or the file is a newer Walka's: then the part does nothing."""
        app = self._app
        if app is None or self._trouble:
            return False
        return getattr(getattr(app, "treadmill", None), "kind", None) != "simulated"

    def start(self) -> None:
        """Make the sender's task and wake it once: at every start the walks are compared with the file."""
        if self.stopping or self._sender_task is not None:
            return
        self.wake = asyncio.Event()
        self._spawn(self.sender.run, name="walka-account")
        self.wake.set()

    def cancel(self) -> None:
        """Walka is closing. Before the walk is saved: nothing here is waited for."""
        self.stopping = True
        self._question = None
        for attempt in list(self._listeners):
            with contextlib.suppress(Exception):
                attempt.cancel()
        for task in list(self._tasks):
            # (Forgetting a sign-in and writing "off" are let run on until close(): signing out must take
            # the key away if it can, and sending that was switched off must not be on at the next start.)
            if task not in self._forgetting:
                task.cancel()
        self._touch()

    async def close(self) -> None:
        """After the walk is saved: gather what is left, close the listener. A second at most; never raises."""
        self.cancel()
        try:
            await asyncio.wait_for(self._wind_up(), CLOSE_WITHIN_S)
        except asyncio.TimeoutError:
            log.warning("the Walka account's part did not close in time: what was left was dropped")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("closing the Walka account's part broke off with an error of the kind %s", type(error).__name__)
        finally:
            self._closed = True  # from here on no task is made any more: none may be left behind

    async def _wind_up(self) -> None:
        while True:
            left: list[Any] = [task for task in self._tasks if not task.done()]
            left += [self._close_listener(attempt) for attempt in list(self._listeners)]
            if not left:
                return
            # (Once more afterwards: a task that ends may have begun another - one that forgets a key, say.)
            await asyncio.gather(*left, return_exceptions=True)

    def walk_saved(self) -> None:
        """A walk was saved: wake the sender. (Only an event is set: this runs inside the code that saves the walk.)"""
        if self.stopping or not self.usable:
            return
        self._wake_up()

    def in_account(self, walk_id: str, start: float) -> bool:
        """Is this walk in the Walka account that is signed in - added by this Walka, or there already and not deleted there?"""
        key = walk_key(walk_id, start)
        return key is not None and ledger.a_copy(self.view.walks.get(key))

    def in_other_account(self, walk_id: str, start: float) -> bool:
        """Was a copy of this walk sent from this computer to a Walka account that is NOT the one signed in now?

        Any Walka account of this address counts - one that was signed
        out of, one whose sign-in ended, another one than is signed in -
        but for one the accounts said was deleted: its copies went with
        it. Answered from memory: no file is read and nobody is asked.
        """
        key = walk_key(walk_id, start)
        return key is not None and key in self._elsewhere

    def snapshot(self) -> dict:
        """What the card shows: ready-made words and numbers. Never a key, never the Walka account's id.

        Cheap: made anew only when something changed. Do not change what it gives.
        """
        asked = self._question
        if asked is not None and (self.mono() >= asked["until"] or asked["epoch"] != self.epoch):
            self._question = None
            self._touch()
        usable = self.usable
        mark = (self._rev, usable, self._store_mark() if usable else None)
        if self._cache is not None and self._cache[0] == mark:
            return self._cache[1]
        made = self._snapshot(usable)
        self._cache = (mark, made)
        return made

    # ---- the actions behind the routes: each answers at once ---------------------------------

    def sign_in(self, way: object) -> tuple[int, dict]:
        """Start a sign-in through the person's own browser. Only one of the three ways does anything."""
        refused = self._refused()
        if refused is not None:
            return refused
        if not isinstance(way, str) or way not in WAYS:
            return 400, {"ok": False, "error": "not a way to sign in"}
        if self.view.user is not None:
            return 409, {"ok": False, "error": "signed in already"}
        if self._signin is not None:
            self._signin.cancel()  # one sign-in at a time: the first one's listener is closed by its own task
        self._bump()
        epoch = self.epoch
        self._problem = ""

        async def finish(signed: Signed) -> bool:
            return await self._finish(signed, way, epoch)

        attempt = SignIn(
            self.client, self.key, self.origin, way, opener=self._opener, finish=finish, mono=self.mono, wait=self.wait
        )
        self._signin = attempt
        self._listeners.add(attempt)
        self._spawn(self._signing_in, attempt)
        return _OK

    def sign_out(self) -> tuple[int, dict]:
        """Forget the sign-in. It never waits and never fails: the card says "signed out" at once."""
        refused = self._refused()
        if refused is not None:
            return refused
        attempt = self._signin
        if attempt is not None:
            attempt.cancel()
            self._bump()  # (a sign-in that is being finished this instant must not sign anybody in)
        view = self.view
        if view.user is None:
            # Nobody was signed in: the store for secrets is not touched - unless the card says
            # that a sign-in may still be in it, and this is its button "Try again".
            if self._left and attempt is None and not self._trying_again:
                self._trying_again = True
                self._spawn(self._take_out_again, forgets=True)
            return _OK
        held, self._access = self._access, None
        self._bump()
        self._sign_in_no += 1
        self.view = View()
        self._elsewhere |= _copies_of(view)  # (the copies stay in that Walka account)
        self._first = "unknown"
        self._problem = ""
        self._last = {"word": "", "at": 0.0, "added": 0}
        self._spawn(self._forget, held, self.epoch, forgets=True)
        self._wake_up()  # a pause that waits for another try is dropped
        return _OK

    def look_again(self) -> tuple[int, dict]:
        """Ask the Walka account whether its first step is taken. (The button says that it asks.)"""
        refused = self._refused() or self._not_signed_in() or self._busy()
        if refused is not None:
            return refused
        self._spawn(self._look, self.epoch)
        return _OK

    def prepare_question(self) -> tuple[int, dict]:
        """Make the question that stands before sending. It shows in the snapshot when it is ready."""
        refused = self._refused() or self._not_signed_in()
        if refused is not None:
            return refused
        if not self.view.email:
            # The sign-in gave no e-mail address: nobody could tell which Walka account the walks would go to.
            return 409, {"ok": False, "error": "no e-mail address"}
        refused = self._busy()
        if refused is not None:
            return refused
        self._question = None
        self._touch()
        self._spawn(self._prepare, self.epoch)
        return _OK

    def switch_on(self, question: object, send: object) -> tuple[int, dict]:
        """Answer the question: switch sending on, for "all" walks or "from_now_on". Anything else switches nothing."""
        refused = self._refused()
        if refused is not None:
            return refused
        asked = self._question
        fits = (
            asked is not None
            and isinstance(question, str)
            and isinstance(send, str)
            and send in ("all", "from_now_on")
            and _same_text(question, asked["id"])
            and self.mono() < asked["until"]
            and asked["epoch"] == self.epoch
            and asked["user"] == self.view.user
            and bool(self.view.email)
        )
        if not fits or asked is None:
            return 400, {"ok": False, "error": "not an answer to the question"}
        self._question = None  # a question is answered once
        now = self.wall()
        chosen = {"what": "all", "at": now} if send == "all" else {"what": "from", "from": now, "at": now}
        self._bump()
        self._spawn(self._switch_on, asked["user"], chosen, self.epoch)
        return _OK

    def switch_off(self) -> tuple[int, dict]:
        """Stop sending, at once. A batch that waits for another try is dropped. Nothing is deleted anywhere."""
        refused = self._refused() or self._not_signed_in()
        if refused is not None:
            return refused
        view = self.view
        self._bump()
        self.view = replace(view, sending=False)
        if self._last["word"] not in STANDS:
            # What the last pass or the last button came to is not said of a card on which
            # sending was stopped since: a page loaded afresh would show it as news.
            self._last = {"word": "", "at": 0.0, "added": 0}
        # (Let run on while Walka closes, like forgetting a sign-in: "off" must reach the file.)
        self._spawn(self._switch_off, view.user, self.epoch, forgets=True)
        self._wake_up()
        return _OK

    def notice_ok(self) -> tuple[int, dict]:
        """The person has read that the sign-in ended: it is not said again."""
        refused = self._refused()
        if refused is not None:
            return refused
        if self.view.notice:
            self.view = replace(self.view, notice="")
            self._touch()
            self._spawn(self._clear_notice, forgets=True)  # (said once: not again after a start)
        return _OK

    def _refused(self) -> tuple[int, dict] | None:
        if self.stopping:
            return 409, {"ok": False, "error": "closing"}
        if not self.usable:
            return 409, {"ok": False, "error": "not available"}
        return None

    def _not_signed_in(self) -> tuple[int, dict] | None:
        return None if self.view.user is not None else (409, {"ok": False, "error": "not signed in"})

    def _busy(self) -> tuple[int, dict] | None:
        return (409, {"ok": False, "error": "busy"}) if self._asking else None

    # ---- signing in --------------------------------------------------------------------------

    async def _signing_in(self, attempt: SignIn) -> None:
        """Open the listener and the browser, and wait for what comes of it."""
        try:
            await attempt.start()
            with self.resting(attempt.done):
                await self.wait(attempt.done, None)
            word = _PROBLEM_OF.get(attempt.outcome, "failed")
        except asyncio.CancelledError:
            raise  # (Walka is closing: close() closes the listener)
        except Exception as error:
            log.warning("signing in to the Walka account broke off with an error of the kind %s", type(error).__name__)
            word = "failed"
        if self._signin is attempt:
            self._signin = None
            if self.view.user is None:
                self._problem = word
            self._touch()
        await self._close_listener(attempt)

    async def _close_listener(self, attempt: SignIn) -> None:
        if attempt not in self._listeners:
            return
        try:
            attempt.cancel()
            await attempt.close()
        except asyncio.CancelledError:
            # (Cut off while it closed - Walka is closing. It stays on the list: close() waits for it to be gone.)
            raise
        except Exception as error:
            log.warning("the sign-in's listener did not close cleanly: an error of the kind %s", type(error).__name__)
        self._listeners.discard(attempt)

    async def _finish(self, signed: Signed, way: str, epoch: int) -> bool:
        """The browser came back and the code was taken. Keep the sign-in - or say that it could not be kept."""
        got = self.mono()
        begun: list[bool] = []
        try:
            return await self._keep(signed, way, epoch, got, begun)
        except asyncio.CancelledError:
            # Cut off in the middle (another sign-in was started, or Walka
            # is closing). What was begun must not stay: a key in the store
            # that no card shows and no "Sign out" removes.
            if begun:
                self._bump()
                self._spawn(self._unkeep, forgets=True)
            raise
        except Exception as error:
            log.warning("the sign-in could not be kept: an error of the kind %s", type(error).__name__)
            return False

    async def _unkeep(self) -> None:
        """Take back a sign-in that was cut off while it was being kept."""
        async with self._lock("order"):
            if self.view.user is not None:
                return  # (somebody has signed in since: that key is theirs)
            # (Both at once, and the file says "it may still be in the store" until the store has said
            # that it is gone - as when signing out: Walka may be closing, and not wait for the store.)
            taken, book = await asyncio.gather(self.keys.delete(self._entry), self._change(self._signs_out()))
            self._mark_went(book)
            await self._store_said(taken)

    async def _keep(self, signed: Signed, way: str, epoch: int, got: float, begun: list[bool]) -> bool:
        if self._stale(epoch):
            return False
        async with self._lock("order"):
            if self._stale(epoch):
                return False
            # The store first. If it will not keep the key there is no sign-in: never a file instead.
            begun.append(True)
            try:
                written = await self.keys.write(
                    self._entry, signed.refresh_token, if_still=lambda: not self._stale(epoch)
                )
            except KeyStoreError as error:
                taken = await self.keys.delete(self._entry)  # (after a timeout it may be written all the same)
                if taken or error.code == "timeout":
                    # (A store that said no outright wrote nothing: what it holds is what it held before.)
                    await self._store_said(taken)
                return False
            if not written:
                return False

            def signs_in(book: dict) -> None:
                # Sending is off afterwards for EVERY Walka account of this address: no road past the question.
                ledger.sign_in(book, self.origin, signed.user_id, way, signed.email)

            book = await self._change(signs_in, only_if=epoch)
            if book is None or self._stale(epoch):
                taken = await self.keys.delete(self._entry)
                if book is not None:
                    await self._change(lambda book: ledger.sign_out(book, self.origin))
                await self._store_said(taken)
                return False
            self._bump()
            self._sign_in_no += 1
            self._access = (signed.access_token, got + signed.lifetime_s - ACCESS_MARGIN_S)
            self._apply(book)
            self._first = "unknown"
            self._problem = ""
            self._left, self._marked = "", False  # (this sign-in's key was written over the entry)
            self.latch = ""
            self._last = {"word": "", "at": 0.0, "added": 0}
        # Straight after signing in: has this Walka account taken its first step? (Decision 4.)
        self._spawn(self._look, self.epoch)
        return True

    async def _forget(self, held: tuple[str, float] | None, epoch: int) -> None:
        """What signing out does after the card has said "signed out": the key leaves the store, the file says so."""
        async with self._lock("order"):
            # Both at once: a store that sits on its answer (a Mac's keychain can) must not keep
            # "signed out, sending off" from the file - Walka may be closed before the store answers.
            # And with them the file says that the sign-in may still be in the store: that stands
            # until the store has said that it is gone.
            taken, book = await asyncio.gather(
                self.keys.delete(self._entry),
                self._change(self._signs_out()),
            )
            self._mark_went(book)
            if book is not None and self.epoch == epoch and not self.stopping:
                self._apply(book)
            await self._store_said(taken)
        # A courtesy to the accounts, with a short time limit, and only with
        # a token that has not run out: nobody refreshes in order to sign out.
        if held is not None and self.mono() < held[1] and not self.stopping:
            self._spawn(self._courtesy, held[0])

    async def _courtesy(self, token: str) -> None:
        await calls.logout(self.client, self.key, access_token=token)

    def _signs_out(self, *, notice: str | None = None, gone: str | None = None) -> Callable[[dict], None]:
        """The change of the file for a sign-out that also takes the sign-in out of the store."""
        lasting = self.keys.lasting  # (a store in memory is gone with Walka: nothing can be left in it)
        if lasting:
            self._marked = True

        def signs_out(book: dict) -> None:
            ledger.sign_out(book, self.origin, notice=notice, left_in_store=lasting, gone=gone)

        return signs_out

    def _mark_went(self, book: dict | None) -> None:
        """``book``: what came of the change that ``_signs_out`` made. Not written: the file does not say so."""
        if book is None:
            self._marked = False

    async def _store_said(self, taken: bool) -> None:
        """What the store answered when a sign-in was taken out of it. (Under the "order" lock.)

        Gone: the file no longer says that it may be there. Not gone, as
        far as anybody knows (the store said no, or did not answer in
        time): the file says so, and the card - when nobody is signed
        in. With a store in memory there is nothing to say.
        """
        if not self.keys.lasting:
            return
        if taken:
            if self._marked:
                await self._change(lambda book: ledger.sign_in_left(book, self.origin, False))
                self._marked = False
            left = ""
        else:
            book = await self._change(lambda book: ledger.sign_in_left(book, self.origin, True))
            if book is not None:
                self._marked = ledger.is_left_in_store(book, self.origin)
            left = "sign_in" if self.view.user is None else ""
        if left != self._left:
            self._left = left
            self._touch()

    async def _take_out_again(self) -> None:
        """The card's "Try again": ONE delete. Nothing is read from the store, and nobody is asked.

        The file is looked at first. The entry is named after the
        address, so the OTHER Walka on this data folder shares it: if
        somebody has signed in there since, the entry is that sign-in's
        key by now, and wanted. The file then says that somebody is
        signed in - and nothing is deleted here; the word goes from this
        card. Only a Walka that knows its own sign-out reached the file
        goes by the file: otherwise the sign-in that stands there is its
        own, written before a sign-out that the file never took. (What
        stays: the instant between this look and the delete.)
        """
        try:
            async with self._lock("order"):
                if self.view.user is not None:
                    return  # (somebody has signed in since: that key is wanted)
                if self._marked and await self._signed_in_by_the_file():
                    self._marked = False
                    if self._left:
                        self._left = ""
                        self._touch()
                    return
                await self._store_said(await self.keys.delete(self._entry))
        finally:
            self._trying_again = False

    async def _signed_in_by_the_file(self) -> bool:
        """Does the file say that somebody is signed in at this address? (Not to be read just now: no.)"""
        try:
            book = await off_loop(self._ledger.read, name=THREAD_NAME)
        except LedgerError:
            return False
        return ledger.signed_in(book, self.origin) is not None

    async def sign_in_ended(self, epoch: int, *, gone: bool = False) -> None:
        """The sign-in is over - a refresh was refused for good, or the Walka account is gone (decision 11).

        Forget the keys, switch sending off, keep everything on the
        computer, and say so once, in the card, until "OK" is pressed.

        ``gone``: the accounts said that the Walka account was deleted.
        That is written down for it: the copies of its walks are gone
        too. (A refresh that was refused cannot tell: then they count.)
        """
        if self._stale(epoch):
            return
        was = self.view
        self._access = None
        self._bump()
        self._sign_in_no += 1
        mine = self.epoch
        self.view = View(notice="ended")
        if not gone:
            self._elsewhere |= _copies_of(was)
        self._first = "unknown"
        async with self._lock("order"):
            # (Both at once, as when signing out: the file does not wait for the store.)
            taken, book = await asyncio.gather(
                self.keys.delete(self._entry),
                self._change(self._signs_out(notice="ended", gone=was.user if gone else None)),
            )
            self._mark_went(book)
            if book is not None and self.epoch == mine and not self.stopping:
                self._apply(book)
            await self._store_said(taken)

    async def _lost_sign_in(self, epoch: int, problem: str, *, taken: bool | None = None) -> None:
        """The stored sign-in is not there, or is not this folder's: quietly "not signed in". Not decision 11:
        nobody signed out and no Walka account was deleted. The entry in the store is left alone - it may be
        another data folder's.

        ``taken``: whoever calls did take the entry out, because its key
        could not be kept - and this is what the store answered."""
        if self._stale(epoch):
            return
        was = self.view
        self._access = None
        self._bump()
        self._sign_in_no += 1
        mine = self.epoch
        self.view = View()
        self._elsewhere |= _copies_of(was)  # (the Walka account is there as before, and so are the copies)
        self._first = "unknown"
        self._problem = problem
        async with self._lock("order"):
            book = await self._change(lambda book: ledger.sign_out(book, self.origin))
            if book is not None and self.epoch == mine and not self.stopping:
                self._apply(book)
            if taken is not None:
                await self._store_said(taken)
                if self._left and self._problem == problem:
                    # ("Not on this computer any more" would be more than Walka knows: the card
                    # says that the sign-in may still be in the store, and that says enough.)
                    self._problem = ""
                    self._touch()

    # ---- keys --------------------------------------------------------------------------------

    def access_token(self) -> str | None:
        """The access token, if there is one that has not run out by the monotonic clock. Nothing is asked."""
        held = self._access
        if held is None or self.mono() >= held[1]:
            return None
        return held[0]

    def drop_access(self, token: str) -> None:
        """The accounts said this access token ran out: it is not used again.

        Only this one: other work may have been told the same a moment
        ago and made a new one already, and that one stays.
        """
        if self._access is not None and self._access[0] == token:
            self._access = None

    async def fresh_token(self, epoch: int, *, wanted: Callable[[], bool] | None = None) -> str:
        """An access token to make a request with - a new one if the old one has run out.

        ``wanted`` (the sender's: is there still a walk to send?) is asked
        on the loop, without waiting, as the last thing before the store
        for secrets is read and again before the request for a new token
        is made. When it says no, neither happens: ``Halt("nothing")``.

        Raises ``Halt`` ("over": nobody is signed in any more; "dropped";
        "stopped": the store could not be asked; "nothing") and
        ``CallError`` (the refresh got no answer, or one that says nothing).
        """
        token = self.access_token()
        if token is not None:
            return token
        async with self._lock("refreshing"):
            self.check(epoch)
            token = self.access_token()
            if token is not None:
                return token
            return await self._refresh(epoch, wanted)

    async def _refresh(self, epoch: int, wanted: Callable[[], bool] | None = None) -> str:
        user, sign_in_no = self.view.user, self._sign_in_no
        if wanted is not None and not wanted():
            raise Halt("nothing")  # no walk is left to send: the store is not looked into
        # The refresh token is read from the store immediately before it is
        # used and never kept: two Walkas can run on one data folder, and
        # an old one used again later ends the whole sign-in.
        try:
            stored = await self.keys.read(self._entry)
        except KeyStoreError as error:
            self.check(epoch)
            if error.code in ("refused", "unavailable"):
                await self._lost_sign_in(epoch, "store_refused")
                raise Halt("over") from None
            raise Halt(
                "stopped", "stopped"
            ) from None  # a store that did not answer this once: nobody is signed out for it
        self.check(epoch)
        if stored is None:
            await self._lost_sign_in(epoch, "no_stored_sign_in")
            raise Halt("over")
        if wanted is not None and not wanted():
            raise Halt("nothing")  # the walk was deleted while the store was read: nothing is asked
        over = False
        try:
            signed = await calls.refresh(self.client, self.key, refresh_token=stored)
        except CallError as error:
            if error.kind != "sign_in_over":
                raise
            over = True
        if over:
            await self.sign_in_ended(epoch)
            raise Halt("over")
        got = self.mono()

        def still_this_sign_in() -> bool:
            # Inside the store's thread, under its lock, as the last thing before the key is written.
            if self._sign_in_no != sign_in_no:
                return False  # somebody signed out here meanwhile, or signed in: the entry is not this key's
            # And the file: the OTHER Walka on this data folder may have signed out, or signed in to
            # another Walka account, while the key was renewed. (A plain read: it takes no lock and
            # waits for nothing of the loop's. What stays is the instant between this look and the
            # write, in which the other Walka can still do both.)
            try:
                found = ledger.signed_in(self._ledger.read(), self.origin)
            except LedgerError:
                return True  # not to be read just now: the key is this sign-in's as far as anybody knows
            return found is not None and found[0] == user

        # The NEW refresh token goes to the store before the access token is
        # used for anything, and at once: the old one is spent. It is written
        # whenever this sign-in still stands - also when the work that asked
        # for it is not wanted any more (sending was switched off meanwhile,
        # or Walka is closing): only a sign-out, or another sign-in, makes
        # the key unwanted, and a sign-out's delete must not be undone by it.
        try:
            written = await self.keys.write(self._entry, signed.refresh_token, if_still=still_this_sign_in)
        except KeyStoreError:
            # A sign-in whose key cannot be kept is no sign-in: never in memory instead.
            if self._stale(epoch):
                raise Halt("dropped") from None
            taken = await self.keys.delete(self._entry)
            await self._lost_sign_in(epoch, "no_stored_sign_in", taken=taken)
            raise Halt("over") from None
        if not written:
            # The key is not this sign-in's to write any more, and the access token that came with it is
            # dropped. Somebody signed out HERE: that sign-out takes the entry away itself. Or the file
            # says so (the other Walka signed out, or the file is lost): then what it says is taken
            # over now, and if nobody is signed in by it, the old key does not stay in the store.
            here = self._sign_in_no != sign_in_no
            try:
                await self.still(epoch)
            except Halt:
                if not here and self.view.user is None and not self.stopping:
                    await self._take_out_late()
                raise
            raise Halt("dropped")
        # Whether the ACCESS token is used goes by the epoch, as everything does: work that began before
        # sending was switched off uses nothing (the key above is kept all the same, for the next refresh).
        # And the file once more. The OTHER Walka on this data folder may have signed out in the
        # instant between the look inside the store's thread and the write: its sign-out took the
        # entry out of the store, and the write above has just put one back.
        try:
            await self.still(epoch)
        except Halt:
            if self.view.user is None and not self.stopping:
                await self._take_out_late()
            raise
        if signed.user_id != self.view.user:
            # The entry in the store is another data folder's sign-in. Its
            # key is in the store again, renewed; here nothing is done with it.
            await self._lost_sign_in(epoch, "no_stored_sign_in")
            raise Halt("over")
        self._access = (signed.access_token, got + signed.lifetime_s - ACCESS_MARGIN_S)
        return signed.access_token

    async def _take_out_late(self) -> None:
        """The file says that nobody is signed in any more, and a key is in the store all the same: it goes."""
        async with self._lock("order"):
            if self.view.user is None:
                await self._store_said(await self.keys.delete(self._entry))

    # ---- asking, for the sender and for the buttons ------------------------------------------

    def check(self, epoch: int) -> None:
        """Is this piece of work still wanted? If not: ``Halt("dropped")``."""
        if self._stale(epoch):
            raise Halt("dropped")

    def _stale(self, epoch: int) -> bool:
        return self.stopping or self.epoch != epoch

    async def still(self, epoch: int) -> None:
        """Read the file again, before a request: still signed in, the same Walka account, sending as it was?

        The other Walka on this data folder may have signed out or
        switched sending off: that counts here too, at once. Nothing read
        from the file switches sending ON in a Walka that is running.
        """
        while True:
            self.check(epoch)
            wrote = self._wrote
            try:
                book = await off_loop(self._ledger.read, name=THREAD_NAME)
            except LedgerError:
                self.check(epoch)
                raise Halt("stopped", "stopped") from None  # not to be read: then nothing is sent
            self.check(epoch)
            if self._wrote == wrote:
                break
            # This Walka changed the file itself while it was being read (sending was switched on, a
            # walk was noted): the picture may be the older one, and what was just written must not be
            # undone by it. Read again - the look before a request is not skipped.
        seen, mine = _view_of(book, self.origin.text), self.view
        if seen.user == mine.user:
            adopted = replace(seen, sending=seen.sending and mine.sending)
        else:
            adopted = replace(seen, sending=False)
        if (adopted.user, adopted.sending, _choice(adopted)) == (mine.user, mine.sending, _choice(mine)):
            if adopted.walks != mine.walks:
                self.view = replace(mine, walks=adopted.walks)
                self._touch()
            self._see_copies(book)
            return
        if adopted.user != mine.user:
            self._access = None
            self._first = "unknown"
            self._sign_in_no += 1
        self.view = adopted
        self._see_copies(book)
        self._bump()
        raise Halt("dropped")

    async def ask(
        self, epoch: int, call: Callable[..., Awaitable[Any]], *, wanted: Callable[[], bool] | None = None, **said: Any
    ) -> Any:
        """One request that bears the person's access token: ``call`` is a function of ``calls``.

        Before it the file is read again. A token that ran out is made
        anew once - never twice for one request. ``wanted``: see
        ``fresh_token``; it is asked once more as the last thing before
        the request itself. Raises ``CallError`` and ``Halt``.
        """
        for again in (False, True):
            await self.still(epoch)
            held = self.access_token()
            token = await self.fresh_token(epoch, wanted=wanted)
            self.check(epoch)
            if wanted is not None and not wanted():
                raise Halt("nothing")
            try:
                return await call(self.client, self.key, access_token=token, **said)
            except CallError as error:
                # (token != held: it was made anew for this very request, and is refused all the same.)
                if error.kind != "token_ran_out" or again or token != held:
                    raise
                self.drop_access(token)
        raise Halt("stopped", "stopped")  # (not reached)

    async def note(self, user: str, entries: dict[str, dict]) -> None:
        """Write down what became of some walks, now. ``Halt("stopped")`` if the file cannot be written:
        then nothing more is sent in this run (the same walks would go again and again)."""
        book = await self._change(lambda book: ledger.note_walks(book, self.origin, user, entries))
        if book is None:
            raise Halt("stopped", "stopped")
        seen = _view_of(book, self.origin.text)
        if seen.user == user and self.view.user == user:
            self.view = replace(self.view, walks=seen.walks)
        self._see_copies(book)
        self._touch()

    async def _first_step(self, epoch: int) -> bool:
        """Ask whether the Walka account has taken its first step, on the website (decision 4)."""
        taken = await self.ask(epoch, calls.first_step)
        self.check(epoch)
        if taken is None:
            # No row: a sign-in that has never been to the website - or a
            # Walka account that was deleted. The two read the same there,
            # so ask who is there before saying "no Walka account yet".
            there = await self.ask(epoch, calls.who)
            self.check(epoch)
            if there != "there":
                await self.sign_in_ended(epoch, gone=there == "gone")
                raise Halt("over")
        self.first_step_is(taken is True)
        return taken is True

    async def _look(self, epoch: int) -> None:
        with self._asking_now():
            try:
                await self._first_step(epoch)
            except Halt as halt:
                self._told(halt, epoch)
            except CallError as error:
                self._told(halt_of(error), epoch)

    async def _prepare(self, epoch: int) -> None:
        """Everything the question says, asked and counted now. Anything missing: no question."""
        with self._asking_now():
            try:
                if not await self._first_step(epoch):
                    return  # the first step is taken on the website and nowhere else
                page = await self.ask(epoch, calls.page)
                self.check(epoch)
                if page is None:
                    raise Halt("stopped", "stopped")
                # Counted from the files themselves, off the loop: only a
                # file says whether it names a treadmill.
                wanted = self.candidates(everything=True)
                now_ms = int(self.wall() * 1000)
                found = await off_loop(read_walks, self.sessions_dir(), wanted, now_ms, name=THREAD_NAME)
                self.set_apart(found)
                self.check(epoch)
                view = self.view
                public = page.get("is_public") is True
                self._question = {
                    "id": secrets.token_urlsafe(16),
                    "until": self.mono() + QUESTION_GOOD_FOR_S,
                    "epoch": epoch,
                    "user": view.user,
                    "shown": {
                        "email": view.email,
                        "way": view.way,
                        "waiting": sum(1 for walk in found if walk.kind == "send"),
                        "unnamed": sum(1 for walk in found if walk.kind == "unnamed"),
                        "held": sum(1 for walk in found if walk.kind == "held"),
                        "public": public,
                        # The page lists days or walks: with "all of them" the whole history shows there at once.
                        "all_shows": public and (page.get("show_days") is True or page.get("show_walks") is True),
                    },
                }
                self._touch()
            except Halt as halt:
                self._told(halt, epoch)
            except CallError as error:
                self._told(halt_of(error), epoch)

    def _told(self, halt: Halt, epoch: int) -> None:
        """A button's work ended early: say why, with one of the fixed words. (It does not stop the run.)"""
        if self._stale(epoch):
            return
        if halt.outcome == "later":
            self.say("no_answer")
        elif halt.outcome == "stopped":
            self.say("certificate" if halt.word == "certificate" else "stopped")

    # ---- switching sending on and off --------------------------------------------------------

    async def _switch_on(self, user: str, chosen: dict, epoch: int) -> None:
        def on(book: dict) -> None:
            ledger.set_sending(book, self.origin, user, True, chosen)

        async with self._lock("order"):
            book = await self._change(on, only_if=epoch)
            if self._stale(epoch):
                return
            if book is None:
                self.say("stopped")  # not written: then it is not on
                return
            self._apply(book)
            self.latch = ""
            self._last = {"word": "", "at": 0.0, "added": 0}
        self._wake_up()

    async def _switch_off(self, user: str | None, epoch: int) -> None:
        def off(book: dict) -> None:
            found = ledger.signed_in(book, self.origin)
            kept = found[1].get("chosen") if found is not None and found[0] == user else None
            ledger.set_sending(book, self.origin, user or "", False, kept)

        async with self._lock("order"):
            book = await self._change(off)
            if book is None:
                # "Off" could not be written, and the file would say "on"
                # again at the next start. So the sign-in itself goes: then
                # nothing can be sent, whatever the file says.
                self._access = None
                self._sign_in_no += 1  # (a key that is being renewed this instant is not written back either)
                await self.keys.delete(self._entry)
                return
            if self.epoch == epoch and not self.stopping:
                self._apply(book)

    async def _clear_notice(self) -> None:
        async with self._lock("order"):
            await self._change(lambda book: ledger.clear_notice(book, self.origin))

    # ---- the file ----------------------------------------------------------------------------

    async def _change(self, change: Callable[[dict], None], *, only_if: int | None = None) -> dict | None:
        """One change of ``walka-account.json``, off the loop: read, change, replace, under the file's lock.

        Gives the book as it was written, or None if it was not written.
        ``only_if``: the change is made only while the epoch is still this
        one - looked at inside the thread, under the lock, so that a change
        that waited behind a sign-out cannot undo it.
        """

        def work() -> dict:
            def fn(book: dict) -> dict:
                if only_if is not None and self._stale(only_if):
                    raise _Stale()
                change(book)
                return ledger.validate(book)

            return self._ledger.change(fn)

        try:
            return await off_loop(work, name=THREAD_NAME)
        except _Stale:
            return None
        except LedgerError as error:
            log.warning("the Walka account's file could not be changed: %s", error.code)
            return None
        except ValueError:
            # (The other Walka signed out meanwhile, say: then the change does not fit, and is not made.)
            return None
        finally:
            # On the loop, the moment the write is back, whatever came of it - and nothing is waited
            # for between this and the caller taking the new book over. See ``still``.
            self._wrote += 1

    def _apply(self, book: dict) -> None:
        self.view = _view_of(book, self.origin.text)
        self._see_copies(book)
        self._touch()

    def _see_copies(self, book: dict) -> None:
        """Which walks have a copy in a Walka account that is not signed in now: from the file, as it is here."""
        found = ledger.copies_elsewhere(book, self.origin, but=self.view.user)
        if found != self._elsewhere:
            self._elsewhere = found
            self._touch()  # (History looks again when the part's number moves)

    # ---- for the sender ----------------------------------------------------------------------

    def wall(self) -> float:
        app = self._app
        return app.wall() if app is not None else self._own_wall()

    def mono(self) -> float:
        app = self._app
        return app.mono() if app is not None else self._own_mono()

    def sessions_dir(self) -> Path:
        return Path(self._app.store.dir)

    def _records(self) -> list[Any]:
        app = self._app
        return list(app.store.records) if app is not None else []

    def keys_in_store(self) -> set[str]:
        """The names (id and start) of the walks that are in Walka's list this instant."""
        found = set()
        for record in self._records():
            key = walk_key(getattr(record, "id", None), getattr(record, "start", None))
            if key is not None:
                found.add(key)
        return found

    def dealt_with(self, key: str) -> bool:
        """Is this walk in the file as added, there already, or set aside by THIS version of Walka?"""
        entry = self.view.walks.get(key)
        if not isinstance(entry, dict):
            return False
        # (Set aside by another version: that Walka may have built the row wrongly. Tried again.)
        return entry.get("state") != "aside" or entry.get("version") == self.version

    def candidates(self, *, everything: bool = False) -> list[tuple[str, float, str]]:
        """The walks that wait: (id, start, key). On the loop, cheap; no file is opened.

        Not the simulated treadmill's, not those dealt with, not those
        this run found unfit or put off, and - after "only walks from now
        on" - not those that started before that moment. ``everything``:
        for the question, which counts afresh and offers "all of them".
        """
        since = None
        chosen = self.view.chosen
        if not everything and chosen is not None and chosen.get("what") == "from":
            since = chosen.get("from")
        waiting = []
        for record in self._records():
            if getattr(record, "treadmill", "") == MADE_UP:
                continue
            walk_id, start = getattr(record, "id", None), getattr(record, "start", None)
            key = walk_key(walk_id, start)
            if key is None or self.dealt_with(key):
                continue
            if not everything:
                if key in self._apart or key in self._skipped:
                    continue
                if since is not None and start < since:
                    continue
            waiting.append((walk_id, start, key))
        return waiting

    def set_apart(self, found: list[Walk]) -> None:
        """What the files said: walks that are not sent, and why. Kept in memory, for this run."""
        for walk in found:
            if walk.kind == "send":
                self._apart.pop(walk.key, None)
            else:
                self._apart[walk.key] = walk
        self._touch()

    def skip(self, key: str) -> None:
        """Not sent in this run, and not written down: tried again when Walka is next started."""
        self._skipped.add(key)
        self._touch()

    def first_step_is(self, taken: bool) -> None:
        self._first = "taken" if taken else "not_taken"
        self._touch()

    def say(self, word: str, added: int = 0) -> None:
        """What the last piece of work came to, for the card: one of ``LAST_WORDS``."""
        if word not in LAST_WORDS:
            word = "stopped"
        if word == "nothing" and self._last["word"] == "sent":
            return  # (walks did go a moment ago: that stays said)
        self._last = {"word": word, "at": self.wall(), "added": int(added)}
        self._touch()

    def stop_run(self, word: str, added: int = 0) -> None:
        """Stop this run: nothing more is asked until Walka is started again (or somebody signs in, or switches on)."""
        self.latch = word if word in LAST_WORDS and word else "stopped"
        self.say(self.latch, added)

    def sending_now(self, sending: bool) -> None:
        if self._sending != sending:
            self._sending = sending
            self._touch()

    @contextlib.contextmanager
    def resting(self, event: asyncio.Event | None) -> Iterator[None]:
        """The task that runs this has nothing to do until ``event`` comes (see ``idle``)."""
        task = asyncio.current_task()
        if task is not None and event is not None:
            self._resting[task] = event
            self._settle()
        try:
            yield
        finally:
            if task is not None:
                self._resting.pop(task, None)

    # ---- tasks -------------------------------------------------------------------------------

    def _lock(self, name: str) -> asyncio.Lock:
        """ "order": the changes of who is signed in and whether sending is on, one after the other, as they
        were asked for. "refreshing": one refresh at a time. (Made when first needed: locks belong to a loop.)"""
        lock = self._locks.get(name)
        if lock is None:
            lock = self._locks[name] = asyncio.Lock()
        return lock

    def _spawn(
        self, work: Callable[..., Awaitable[None]], *args: Any, name: str = "walka-account-work", forgets: bool = False
    ) -> None:
        if self._closed:
            return
        task = asyncio.get_running_loop().create_task(self._guarded(work, *args), name=name)
        self._tasks.add(task)
        if forgets:
            self._forgetting.add(task)
        if name == "walka-account":
            self._sender_task = task
        task.add_done_callback(self._done)

    async def _guarded(self, work: Callable[..., Awaitable[None]], *args: Any) -> None:
        try:
            await work(*args)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Only what kind of error: its words could hold an address, a key or the e-mail address.
            log.warning(
                "a piece of the Walka account's work broke off with an error of the kind %s", type(error).__name__
            )

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        self._forgetting.discard(task)
        self._resting.pop(task, None)
        self._settle()

    def _settle(self) -> None:
        for waiter in self._idle_waiters:
            if not waiter.done():
                waiter.set_result(None)

    def at_work(self) -> bool:
        """Is any task of this object's doing something - not merely waiting to be woken, or for the browser?"""
        for task in self._tasks:
            if task.done():
                continue
            event = self._resting.get(task)
            if event is None or event.is_set():
                return True
        return False

    async def idle(self) -> None:
        """Wait until the tasks have nothing left to do. (For tests: Walka itself never waits for this.)"""
        while self.at_work():
            waiter = asyncio.get_running_loop().create_future()
            self._idle_waiters.append(waiter)
            try:
                await waiter
            finally:
                self._idle_waiters.remove(waiter)

    @contextlib.contextmanager
    def _asking_now(self) -> Iterator[None]:
        self._asking += 1
        self._touch()
        try:
            yield
        finally:
            self._asking -= 1
            self._touch()

    def _wake_up(self) -> None:
        if self.wake is not None:
            self.wake.set()

    def _bump(self) -> None:
        """Whatever work is under way belongs to a time that is over."""
        self.epoch += 1
        self._question = None
        self._touch()

    def _touch(self) -> None:
        self._rev += 1

    # ---- the snapshot ------------------------------------------------------------------------

    def _store_mark(self) -> tuple:
        """Something that changes when a walk is saved or deleted - without walking the list."""
        records = self._app.store.records
        if not records:
            return (0,)
        first, last = records[0], records[-1]
        return len(records), getattr(first, "id", None), getattr(last, "id", None), getattr(last, "start", None)

    def _count(self) -> tuple[dict[str, int], list[dict], list[dict]]:
        counts = {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}
        aside: list[dict] = []
        held: list[dict] = []
        view = self.view
        if view.user is None:
            return counts, aside, held
        since = None
        if view.sending and view.chosen is not None and view.chosen.get("what") == "from":
            since = view.chosen.get("from")
        for record in reversed(self._records()):  # the newest first: they are the ones named
            if getattr(record, "treadmill", "") == MADE_UP:
                continue  # made-up walks are never counted
            walk_id, start = getattr(record, "id", None), getattr(record, "start", None)
            key = walk_key(walk_id, start)
            if key is None:
                counts["unreadable"] += 1
                continue
            entry = view.walks.get(key)
            if isinstance(entry, dict) and self.dealt_with(key):
                if entry.get("state") != "aside":
                    counts["sent"] += 1
                    continue
                counts["aside"] += 1
                if len(aside) < MOST_SHOWN:
                    told = {"id": walk_id, "start": float(start)}
                    told.update(code=str(entry.get("code", "")), message=str(entry.get("message", "")))
                    aside.append(told)
                continue
            apart = self._apart.get(key)
            if apart is not None:
                if apart.kind == "held":
                    counts["held"] += 1
                    if len(held) < MOST_SHOWN:
                        held.append({"id": walk_id, "start": float(start), "why": apart.why})
                elif apart.kind == "unnamed":
                    counts["unnamed"] += 1
                elif apart.kind != "made_up":
                    counts["unreadable"] += 1
                continue
            if since is not None and start < since:
                continue  # before "from now on": it does not wait, it stays
            counts["waiting"] += 1
        return counts, aside, held

    def _snapshot(self, usable: bool) -> dict:
        view = self.view if usable else View()
        if not usable:
            state = "unavailable"
        elif view.user is not None:
            state = "signed_in"
        elif self._signin is not None:
            state = "signing_in"
        else:
            state = "signed_out"
        counts, aside, held = self._count() if usable else ({name: 0 for name in _COUNTS}, [], [])
        chosen = view.chosen if view.sending and view.chosen is not None else None
        asked = self._question if usable else None
        host = self.origin.host + (f":{self.origin.port}" if self.origin.port is not None else "")
        return {
            "state": state,
            "host": host,
            "store": "lasting" if self.keys.lasting else "until_closed",
            "problem": self._problem if usable and self._problem in PROBLEMS else "",
            "notice": view.notice if view.notice in ledger.NOTICES else "",
            "left_in_store": self._left if usable and view.user is None and self._left in LEFT else "",
            "email": view.email,
            "way": view.way,
            "first_step": self._first if view.user is not None else "unknown",
            "can_send": view.user is not None and bool(view.email),
            "sending": view.sending,
            "chosen": str(chosen.get("what")) if chosen is not None else "",
            "from": chosen.get("from") if chosen is not None and chosen.get("what") == "from" else None,
            "busy": "" if not usable else "sending" if self._sending else "asking" if self._asking else "",
            "counts": counts,
            "last": dict(self._last) if usable else {"word": "", "at": 0.0, "added": 0},
            "aside": aside,
            "held": held,
            "question": {"id": asked["id"], **asked["shown"]} if asked is not None else None,
            "rev": self._rev,
        }


_COUNTS = ("waiting", "sent", "aside", "held", "unnamed", "unreadable")
