"""Sending walks to the Walka account: one pass over what waits, and the task that makes passes.

A pass goes in this order, and the order is the point of it. Walka may
open a connection to another machine only when somebody is signed in,
sending is switched on for that Walka account, and there is a walk to
send. So everything that can be found out without asking anybody comes
first, and each step ends the pass when nothing is left:

1. On the loop, without waiting for anything: is somebody signed in, is
   sending on, was this run not stopped - and which walks are not dealt
   with yet? None: the pass ends. Nothing was asked, and the system's
   store for secrets was not looked into.
2. Off the loop: the files of those walks themselves (never the record
   as Walka loaded it: loading fills in what a file lacks). A walk that
   is gone, cannot be read, does not say which treadmill made it, or is
   outside what the Walka account takes, is not sent. None left: the
   pass ends, and still nothing was asked.
3. Only now a key: the access token, made anew if it has run out.
4. Once in a run, before the first walk goes: which version of its rules
   the database is at. A higher one than Walka knows: nothing is sent.
5. The walks, at most 200 in a request. Before EVERY request the file
   ``walka-account.json`` is read again - still signed in, the same
   Walka account, sending still on - and walks deleted meanwhile are
   left out.
6. What the answer says is written down before anything else is asked.

A pass never raises (a cancellation passes through it), and every wait in
it can be cancelled: closing Walka must never wait for the network.

There is no timer here. The task sleeps until it is woken - at a start,
and when a walk was saved. Only after "no answer" does it wait with a
time limit, through the one ``wait`` the service was given, and only
while a walk still waits and sending is still on.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import calls, row
from .calls import CallError
from .threads import off_loop

if TYPE_CHECKING:
    from .service import WalkaAccount

log = logging.getLogger(__name__)

# How long the task waits after "no answer" before it tries again: longer each time, an hour at most.
PAUSES_S = (60.0, 120.0, 300.0, 900.0, 1800.0, 3600.0)
LONGEST_PAUSE_S = 3600.0
# Rows in one request.
BATCH = 200
# The version of the database's rules this Walka knows.
KNOWN_SCHEMA = 1
# What the simulated treadmill calls itself (treadmill/simulated.py). A walk
# that bears this name was made up, and never reaches a Walka account.
MADE_UP = "Simulated pad"
# A walk's file is small: a day of walking, a sample every five seconds, is
# half a megabyte. What is larger than this was written by no Walka.
MOST_FILE_BYTES = 16 * 1024 * 1024

THREAD_NAME = "walka-account-files"


class Halt(Exception):
    """A piece of work ends here. ``outcome`` says how; nothing of an answer is carried.

    ``outcome`` is one of: "later" (no answer: try again after a pause),
    "stopped" (stop this run; ``word`` says why, from the fixed list of the
    snapshot), "over" (nobody is signed in any more), "dropped" (sending
    was switched off, somebody signed out, or Walka is closing: nothing
    more is sent), "nothing".
    """

    def __init__(self, outcome: str, word: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.word = word
        self.retry_after = retry_after


def halt_of(error: CallError) -> Halt:
    """What a refusal means for the work that met it, where it has no meaning of its own.

    Never the refusal's own words: only one of the fixed ones.
    """
    if error.kind == "later":
        return Halt("later", "later", retry_after=error.retry_after)
    if error.kind == "certificate":
        return Halt("stopped", "certificate")
    if error.kind == "full":
        return Halt("stopped", "full")
    # A request built wrongly, a bearer that is not taken, a gateway's own
    # page, a token that ran out twice running: none of it is about a walk.
    return Halt("stopped", "stopped")


@dataclass
class Walk:
    """One walk on its way: what Walka's list says of it, and what its file gave.

    ``kind`` is one of: "send" (``row`` is the row), "gone" (no file: it
    was deleted), "made_up" (the simulated treadmill's), "unnamed" (the
    file does not say which treadmill made it), "unreadable", "held"
    (outside what the Walka account takes; ``why`` says so, in the
    website's words).
    """

    id: str
    start: float
    key: str  # its name in walka-account.json: the id and the start
    kind: str = "unreadable"
    row: dict[str, Any] | None = None
    why: str = ""


def _same_moment(written: Any, start: float) -> bool:
    if isinstance(written, bool) or not isinstance(written, (int, float)):
        return False
    try:
        return float(written) == float(start)
    except OverflowError:
        return False


def _read_walk(folder: Path, walk: Walk, now_ms: int) -> None:
    """Fill in ``walk.kind`` (and its row, or why not) from the walk's own file."""
    # The id comes out of a file's content: only one shaped like Walka's ids names a file here.
    if row.WALKA_ID.fullmatch(walk.id) is None:
        return
    path = folder / f"{walk.id}.json"
    try:
        if os.path.getsize(path) > MOST_FILE_BYTES:
            return
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        walk.kind = "gone"
        return
    except (OSError, ValueError):  # (ValueError: not UTF-8)
        return
    record = row.load_record(text)
    if not isinstance(record, dict):
        return
    # The file must be this walk's own: after a walk is deleted its id is
    # given out again, and a file can be copied under another name.
    if record.get("id") != walk.id or not _same_moment(record.get("start"), walk.start):
        return
    treadmill = record.get("treadmill", None)
    if "treadmill" not in record or not isinstance(treadmill, str):
        # Every Walka writes the key. An EMPTY name is a real walk (a game
        # session marked before the pad's first connection); no key at all
        # was written by no Walka, and cannot be told from a made-up walk.
        walk.kind = "unnamed"
        return
    if treadmill == MADE_UP:
        walk.kind = "made_up"
        return
    made = row.walk_row(record)
    if made is None:
        return
    why = row.outside(made, now_ms)
    if why is not None:
        # Shown, and not sent: nothing is cut down to fit. Not written down
        # either: one of the reasons depends on the day.
        walk.kind, walk.why = "held", why
        return
    walk.kind, walk.row = "send", made


def read_walks(folder: Path, wanted: list[tuple[str, float, str]], now_ms: int) -> list[Walk]:
    """The files of these walks (id, start, key), each mapped to a row or to the reason it is not sent.

    Blocking: call it off the loop. It never raises.
    """
    walks = [Walk(walk_id, start, key) for walk_id, start, key in wanted]
    for walk in walks:
        try:
            _read_walk(folder, walk, now_ms)
        except Exception:
            walk.kind, walk.row = "unreadable", None  # a file can hold anything: whatever it was, it is no walk to send
    return walks


class Sender:
    """Sends what waits. One object for one service; the service starts ``run`` as its task."""

    def __init__(self, service: WalkaAccount) -> None:
        self.service = service
        self.retry_after: float | None = None  # what the accounts asked for with the last "later"
        self._began = -1  # the service's epoch when the pass in hand began
        self._added = 0  # walks the pass in hand added to the Walka account
        self._dealt = 0  # walks it has dealt with: added, there already, or set aside

    # ---- the task ------------------------------------------------------------------------------

    async def run(self) -> None:
        """Wait to be woken; make a pass; after "no answer" wait and make another. For ever."""
        service = self.service
        pause: float | None = None
        tries = 0
        while True:
            with service.resting(service.wake):
                await service.wait(service.wake, pause)
            service.wake.clear()
            outcome = await self.one_pass()
            if outcome == "later" and self._worth_another_try():
                asked = self.retry_after or 0.0
                pause = min(max(PAUSES_S[min(tries, len(PAUSES_S) - 1)], asked), LONGEST_PAUSE_S)
                tries += 1
            else:
                # Nothing waits, or sending is off: no pause is kept, and
                # the task sleeps until a walk is saved.
                pause, tries = None, 0

    def _worth_another_try(self) -> bool:
        service = self.service
        view = service.view
        if not service.usable or service.stopping or service.latch:
            return False
        return view.user is not None and view.sending and bool(service.candidates())

    # ---- one pass ------------------------------------------------------------------------------

    async def one_pass(self) -> str:
        """Send what waits. "nothing" | "sent" | "later" | "stopped" | "over" | "dropped".

        Do not call it while ``run`` is at work on the same service.
        """
        service = self.service
        self.retry_after = None
        self._began = service.epoch
        self._added = self._dealt = 0
        try:
            return await self._pass()
        except Halt as halt:
            return self._ended(halt)
        except CallError as error:
            return self._ended(halt_of(error))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Only the kind of error: its words could hold an address or a key.
            log.warning("sending to the Walka account broke off with an error of the kind %s", type(error).__name__)
            return self._ended(Halt("stopped", "stopped"))
        finally:
            service.sending_now(False)

    def _ended(self, halt: Halt) -> str:
        service = self.service
        if service.stopping or service.epoch != self._began:
            # Somebody signed out or switched sending off meanwhile: what
            # the old sign-in was answered says nothing any more.
            return "dropped" if halt.outcome != "over" else "over"
        if halt.outcome == "stopped":
            service.stop_run(halt.word or "stopped", self._added)
        elif halt.outcome == "later":
            self.retry_after = halt.retry_after
            service.say("later", self._added)
        return halt.outcome

    async def _pass(self) -> str:
        service = self.service
        # 1. On the loop, and nothing is waited for.
        if not service.usable or service.stopping:
            return "nothing"
        view = service.view
        if view.user is None or not view.sending:
            return "nothing"
        if service.latch:
            return "stopped"
        epoch, user = service.epoch, view.user
        wanted = service.candidates()
        if not wanted:
            service.say("nothing")
            return "nothing"

        # 2. The files themselves, off the loop. Still nothing is asked.
        found = await off_loop(read_walks, service.sessions_dir(), wanted, int(service.wall() * 1000), name=THREAD_NAME)
        service.set_apart(found)  # (what a file is like is true whoever is signed in)
        service.check(epoch)
        ready = [walk for walk in found if walk.kind == "send"]
        if not ready:
            service.say("nothing")
            return "nothing"

        # 3. and 4. A key, and the version of the database's rules - once in a run.
        service.sending_now(True)
        if not service.schema_known:
            version = await service.ask(epoch, calls.schema_version)
            service.check(epoch)
            if version > KNOWN_SCHEMA:
                raise Halt("stopped", "too_old")
            service.schema_known = True

        # 5. and 6. The walks.
        try:
            for first in range(0, len(ready), BATCH):
                await self._send(epoch, user, ready[first : first + BATCH])
        except CallError as error:
            if error.kind == "not_open":
                await self._not_open(epoch)
            raise
        outcome = "sent" if self._dealt else "nothing"
        service.say(outcome, self._added)
        return outcome

    async def _not_open(self, epoch: int) -> None:
        """ "Walka is for adults.": the first step is not taken - or the Walka account is gone. Ask which."""
        service = self.service
        there = await service.ask(epoch, calls.who)
        service.check(epoch)
        if there != "there":
            await service.sign_in_ended(epoch)
            raise Halt("over")
        service.first_step_is(False)
        raise Halt("stopped", "not_open")

    def _still_there(self, walks: list[Walk]) -> list[Walk]:
        """Those that still wait: a walk deleted while its send waited is not sent - and neither is one
        that the other Walka on this data folder has sent meanwhile (the file was just read again)."""
        service = self.service
        there = service.keys_in_store()
        return [walk for walk in walks if walk.key in there and not service.dealt_with(walk.key)]

    async def _push(self, epoch: int, walks: list[Walk]) -> tuple[list[Walk], list[str], list[str]]:
        """One request with these walks: (the walks that went, the ids made for them, the ids that came back).

        The file is read again first, and walks deleted meanwhile are left
        out at the last moment. A token that ran out is made anew once.
        """
        service = self.service
        for again in (False, True):
            await service.still(epoch)
            held = service.access_token()
            token = await service.fresh_token(epoch)
            service.check(epoch)
            going = self._still_there(walks)
            if not going:
                return [], [], []
            # A random id for each row, new in the whole table: never worked
            # out from the walk, and never the same for two Walka accounts.
            ids = [str(uuid.uuid4()) for _ in going]
            rows = [row.with_id(walk.row or {}, row_id) for walk, row_id in zip(going, ids, strict=True)]
            try:
                back = await calls.push(service.client, service.key, access_token=token, rows=rows)
            except CallError as error:
                # Refresh, and try once more - but never refresh twice for one
                # request, and not when the token was only just made for this
                # one (token != held) and is refused all the same.
                if error.kind != "token_ran_out" or again or token != held:
                    raise
                service.drop_access(token)
                continue
            return going, ids, back
        raise Halt("stopped", "stopped")  # (not reached: the second round returns or raises)

    async def _send(self, epoch: int, user: str, batch: list[Walk]) -> None:
        """Send one batch and write down what became of it."""
        try:
            going, ids, back = await self._push(epoch, batch)
        except CallError as error:
            if error.kind != "row":
                raise
            # Refused whole, because of one of its rows.
            if len(batch) == 1:
                missing = [batch[0]] if await self._alone(epoch, user, batch[0], refused=error) else []
            else:
                # One by one, to find which.
                missing = [walk for walk in batch if await self._alone(epoch, user, walk)]
            if missing:
                await self._look_up(epoch, user, missing)
            return
        await self._answered(epoch, user, going, ids, back)

    async def _answered(self, epoch: int, user: str, going: list[Walk], ids: list[str], back: list[str]) -> None:
        service = self.service
        if not going:
            return
        now = service.wall()
        came = set(back)
        entries: dict[str, dict] = {}
        missing: list[Walk] = []
        for walk, row_id in zip(going, ids, strict=True):
            if row_id in came:
                entries[walk.key] = {"state": "added", "row": row_id, "at": now}
            else:
                # In the Walka account already, or deleted there: both mean "do
                # not send again". The id made for it names nothing.
                entries[walk.key] = {"state": "there", "row": None, "deleted": False}
                missing.append(walk)
        # Written NOW, before anything else is asked - and whoever is signed
        # in by now: the walks did go to that Walka account.
        await service.note(user, entries)
        self._added += len(came)
        self._dealt += len(going)
        service.check(epoch)
        if missing:
            await self._look_up(epoch, user, missing)

    async def _look_up(self, epoch: int, user: str, walks: list[Walk]) -> None:
        """Which rows the Walka account has for walks that did not come back. Only told, never needed:
        a failure changes nothing, and it is not asked again."""
        service = self.service
        by_start: dict[str, list[Walk]] = {}
        for walk in walks:
            by_start.setdefault(row.start_key(walk.row or {"started_at": ""}), []).append(walk)
        starts = list(by_start)
        for first in range(0, len(starts), calls.MOST_KEYS):
            await service.still(epoch)
            token = service.access_token()
            if token is None:
                return  # (not worth a new key: the walk is "there", its row unknown)
            found = await calls.find(
                service.client, service.key, access_token=token, start_keys=starts[first : first + calls.MOST_KEYS]
            )
            entries = {
                walk.key: {"state": "there", "row": row_id, "deleted": deleted}
                for start, (row_id, deleted) in found.items()
                for walk in by_start.get(start, [])
            }
            if entries:
                await service.note(user, entries)
            service.check(epoch)
            if not found:
                return

    async def _alone(self, epoch: int, user: str, walk: Walk, refused: CallError | None = None) -> bool:
        """Send one walk by itself, and write down what became of it. ``refused``: it was sent alone
        already, and this was the answer. True: it did not come back - it is in the Walka account already."""
        service = self.service
        new_id_tried = False
        while True:
            error, refused = refused, None
            going: list[Walk] = []
            ids: list[str] = []
            back: list[str] = []
            if error is None:
                try:
                    going, ids, back = await self._push(epoch, [walk])
                except CallError as caught:
                    if caught.kind != "row":
                        raise
                    error = caught
            if error is None:
                if not going:
                    return False  # deleted on this computer meanwhile
                if back:
                    entry = {"state": "added", "row": ids[0], "at": service.wall()}
                else:
                    entry = {"state": "there", "row": None, "deleted": False}
                await service.note(user, {walk.key: entry})
                self._added += 1 if back else 0
                self._dealt += 1
                service.check(epoch)
                return not back
            if error.pkey and not new_id_tried:
                new_id_tried = True  # the id was taken: a new one is made, and once more
                continue
            if error.pkey or error.days_to_come:
                # Not for good. (The days to come: this computer's clock was
                # ahead.) Not written down: the walk is tried again when
                # Walka is next started.
                service.skip(walk.key)
                return False
            # Set aside, with what the accounts said and which Walka asked:
            # this version does not send it again. Never cut down to fit.
            aside = {"state": "aside", "code": error.code, "message": error.message, "version": service.version}
            await service.note(user, {walk.key: aside})
            self._dealt += 1
            service.check(epoch)
            return False
