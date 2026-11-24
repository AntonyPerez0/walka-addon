"""The file ``walka-account.json``: who is signed in, whether sending is on, what became of each walk.

It lies in the data folder, beside the walks. Under the address of the
accounts, and then under the Walka account's id: how the person signed
in, the e-mail address (so that the card can say WHICH Walka account
without asking the network), whether sending is on, what was chosen when
it was switched on, the mark that was made then, and for each walk dealt
with - under its id AND its start, because an id is given out again
after a walk is deleted - whether it was added, was there already, or
was set aside. A walk that is not in the file is waiting. The e-mail
address and the id are in this file only.

Two more things are written down, each a plain yes or no. Under the
address: that this Walka signed out and the system's store for secrets
has not yet said that the sign-in is gone from it (``left_in_store``) -
written BEFORE the store is asked, and taken back when it has answered,
so that it also stands when Walka is closed in between. Under a Walka
account: that the accounts said it was deleted (``gone``) - then the
copies of its walks are gone with it.

No key of a sign-in is ever in it: those live in memory and in the
system's store for secrets (``keystore``). The mark is no key: it opens
nothing. It is a random text, made anew each time the question before
sending is answered, and kept twice - here, beside "sending is on", and
in the system's store.

Four rules, each with its reason:

- Every write is: take the lock, read the file again, change ONE thing,
  replace the file. Two Walkas can run on one data folder. If each wrote
  what it had read a moment ago, one that notes a sent walk would put
  "sending on" back after the other had switched it off.
- A file that is there but cannot be opened is never taken for an empty
  one. Writing an empty one back would lose the other Walka's entries and
  the moment "from now on" was chosen.
- Sending counts only for the Walka account that is signed in, and only
  with a choice and a mark beside it. Finishing ANY sign-in writes "off"
  for every Walka account of that address: no sign-in leads past the
  question. A Walka that is running never takes "on" from the file, and
  a file without the sign-in in the system's store means "not signed
  in".
- The file alone does not say that sending is on. At a start its "on" is
  shown as it stands - no store is read for that - but before a walk
  goes, Walka reads the mark in the system's store, where it reads the
  sign-in anyway. Only if that is the mark of this file is anything
  sent. If the store holds another mark or none, "off" is written here
  and nothing is sent. The mark is taken out of the store whenever "off"
  is written by a button or by a sign-in that ends, and a new one is
  made at every "on": so an older copy of the file put back (from a
  backup, say), or a file changed by hand to say "on", bears a mark
  that the store does not hold, and the person is asked again. What
  that leaves, each pinned by a test that names it:
  - a store that would let the mark be neither taken out nor written
    over when sending was stopped;
  - a Walka that is ended between writing "off" and taking the mark out
    (the computer goes off; or the store sits on the delete until Walka
    is closed, which waits a second for it and no longer);
  - two Walkas on one data folder: sending is stopped in one, and the
    older copy is put back before the other has read the file again -
    that one looked into the store earlier in its run, and does not
    look a second time;
  - a backup that puts back the system's store together with the file.
  And it is no guard against whoever can write the file WHILE sending
  is on: the mark stands in it, and can be kept while the rest is
  changed (the choice, say). That is the program on the same computer
  of SECURITY.md, not an older copy.

Lost, or damaged: sending is off; switched on again, everything is sent
anew and answered "there already". A file of a newer Walka is left alone.

No clock is read here: every time is handed in by the caller.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

FILE_NAME = "walka-account.json"
LOCK_NAME = "walka-account.lock"
VERSION = 1

NOTICES = ("ended",)
# The longest mark taken, in characters. (Walka's own are 32.)
MOST_MARK = 128

# How long a busy file is waited for. Counted in tries, not read off a
# clock: about a fifth of a second to open the file, a second to replace
# it (on Windows a file cannot be replaced while somebody has it open),
# five seconds for the other Walka to let go of the lock.
PAUSE_S = 0.05
READ_TRIES = 5
REPLACE_TRIES = 20
LOCK_TRIES = 100

_ON_WINDOWS = sys.platform == "win32"
_BINARY = getattr(os, "O_BINARY", 0)  # Windows would otherwise rewrite line ends
_NUMBERS = itertools.count(1)  # a temp file's name is this process's own: its id and a number


class LedgerError(Exception):
    """The file could not be used.

    ``code`` is one of: "newer" (a newer Walka wrote it: left alone),
    "cannot_read" (it is there and could not be opened), "cannot_write"
    (the lock or the write failed). Nothing else is carried: the system's
    own words hold the path, and with it the person's name.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def walk_key(walk_id: str, start: float) -> str:
    """The name of a walk in the file: its id and its start (1763760600 and 1763760600.0 are one walk)."""
    return f"{walk_id}|{float(start)!r}"


def empty_book() -> dict:
    return {"version": VERSION, "addresses": {}}


# ---------------------------------------------------------------- the shape


def _number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # a whole number too large to be a moment
        return False


def _text(value: Any, most: int) -> bool:
    """A name or an id: not empty, not long, nothing in it that cannot be seen."""
    return isinstance(value, str) and 0 < len(value) <= most and value.isprintable()


def _word(value: Any, most: int) -> str:
    return value if _text(value, most) else ""


def _sentence(value: Any, most: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable())[:most]


def _origin_text(origin: Any) -> str:
    """The address as text. An ``Origin`` of ``https`` is taken too: its ``text`` is the name."""
    text = origin if isinstance(origin, str) else getattr(origin, "text", None)
    if not _text(text, 300):
        raise ValueError("not an address")
    return text


def valid_mark(value: Any) -> bool:
    """A mark as Walka makes one: plain characters that can be seen, no space, not long."""
    return isinstance(value, str) and 0 < len(value) <= MOST_MARK and all("!" <= ch <= "~" for ch in value)


def _valid_chosen(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    what = raw.get("what")
    if what == "all":
        chosen: dict[str, Any] = {"what": "all"}
    elif what == "from" and _number(raw.get("from")):
        chosen = {"what": "from", "from": float(raw["from"])}
    else:
        return None
    if _number(raw.get("at")):
        chosen["at"] = float(raw["at"])
    return chosen


def _valid_entry(raw: Any) -> dict | None:
    """What became of one walk, or None for anything this Walka does not know."""
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    if state == "added":
        if not _text(raw.get("row"), 64) or not _number(raw.get("at")):
            return None
        return {"state": "added", "row": raw["row"], "at": float(raw["at"])}
    if state == "there":
        row = raw.get("row")
        return {"state": "there", "row": row if _text(row, 64) else None, "deleted": raw.get("deleted") is True}
    if state == "aside":
        return {
            "state": "aside",
            "code": _word(raw.get("code"), 32),
            "message": _sentence(raw.get("message"), 300),
            "version": _word(raw.get("version"), 32),
        }
    return None


def _valid_account(raw: Any, is_signed_in: bool) -> dict | None:
    if not isinstance(raw, dict):
        return None
    chosen = _valid_chosen(raw.get("chosen"))
    # The one place where the file's "on" is taken: said outright, with a
    # choice and a mark beside it, and for the Walka account that is
    # signed in. (Whether the mark is the one in the system's store
    # cannot be told from the file: the sender looks before a walk goes.
    # See the top.)
    mark = raw.get("mark")
    sending = raw.get("sending") is True and chosen is not None and valid_mark(mark) and is_signed_in
    walks: dict[str, dict] = {}
    raw_walks = raw.get("walks")
    if isinstance(raw_walks, dict):
        for key, entry in raw_walks.items():
            fit = _valid_entry(entry)
            if _text(key, 200) and fit is not None:
                walks[key] = fit
    return {
        "way": _word(raw.get("way"), 32),
        "email": _word(raw.get("email"), 254),
        "sending": sending,
        "chosen": chosen,
        # A mark says something only beside "on": without it there is none.
        "mark": mark if sending else None,
        # The accounts said that this Walka account was deleted. (Nobody is signed in to one
        # that is gone - and a file that says both is believed about the sign-in.)
        "gone": raw.get("gone") is True and not is_signed_in,
        "walks": walks,
    }


def _valid_address(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    raw_accounts = raw.get("accounts")
    if not isinstance(raw_accounts, dict):
        raw_accounts = {}
    who = raw.get("signed_in")
    if not (_text(who, 200) and isinstance(raw_accounts.get(who), dict)):
        who = None
    accounts: dict[str, dict] = {}
    for user_id, raw_account in raw_accounts.items():
        fit = _valid_account(raw_account, user_id == who) if _text(user_id, 200) else None
        if fit is not None:
            accounts[user_id] = fit
    notice = raw.get("notice") if who is None and raw.get("notice") in NOTICES else None
    # Said outright, and only while nobody is signed in: a sign-in writes its own key over the entry.
    # (A file of before this was written down has no such word: nothing is left, as far as it knows.)
    left = raw.get("left_in_store") is True and who is None
    if who is None and notice is None and not left and not accounts:
        return None  # nothing is known of this address: no line for it
    return {"signed_in": who, "notice": notice, "left_in_store": left, "accounts": accounts}


def validate(book: Any) -> dict:
    """The book again, as a new dict, with everything dropped that is not of the shape.

    Unknown states and wrong types go. ``"sending": true`` survives only
    with a choice and a mark beside it, and only for the Walka account
    that is signed in. A book of a newer Walka raises
    ``LedgerError("newer")``.
    """
    if not isinstance(book, dict):
        return empty_book()
    version = book.get("version")
    if isinstance(version, (int, float)) and not isinstance(version, bool) and version > VERSION:
        raise LedgerError("newer")
    raw_addresses = book.get("addresses")
    if isinstance(version, bool) or not isinstance(version, int) or version != VERSION:
        return empty_book()
    if not isinstance(raw_addresses, dict):
        return empty_book()
    addresses: dict[str, dict] = {}
    for origin, raw_address in raw_addresses.items():
        fit = _valid_address(raw_address) if _text(origin, 300) else None
        if fit is not None:
            addresses[origin] = fit
    return {"version": VERSION, "addresses": addresses}


def _refuse(_constant: str) -> Any:
    raise ValueError("not a number")


def _parse(raw: bytes | None) -> dict:
    """The file's bytes as a book. Missing, or damaged in any way: an empty book."""
    if raw is None:
        return empty_book()
    try:
        return validate(json.loads(raw.decode("utf-8"), parse_constant=_refuse))
    except (ValueError, RecursionError):  # not JSON, not UTF-8, NaN, a number without end, nested without end
        return empty_book()


def _dump(book: dict) -> str:
    return json.dumps(book, separators=(",", ":"), allow_nan=False)


# ------------------------------------------- changes to a book, for change()
#
# Each takes a book that came through ``validate`` (``read`` and ``change``
# give no other) and changes it in place.


def address(book: dict, origin: Any) -> dict:
    """What is known under one address of the accounts. Made if missing."""
    addresses = book.setdefault("addresses", {})
    place = addresses.get(_origin_text(origin))
    if not isinstance(place, dict):
        place = addresses[_origin_text(origin)] = {
            "signed_in": None,
            "notice": None,
            "left_in_store": False,
            "accounts": {},
        }
    return place


def _account(place: dict, user_id: Any) -> dict:
    if not _text(user_id, 200):
        raise ValueError("not the id of a Walka account")
    return place["accounts"].setdefault(
        user_id, {"way": "", "email": "", "sending": False, "chosen": None, "mark": None, "gone": False, "walks": {}}
    )


def signed_in(book: dict, origin: Any) -> tuple[str, dict] | None:
    """Who is signed in at this address: the id and what is kept under it. Changes nothing."""
    place = book.get("addresses", {}).get(_origin_text(origin))
    if not isinstance(place, dict):
        return None
    who = place.get("signed_in")
    account = place.get("accounts", {}).get(who) if isinstance(who, str) else None
    return (who, account) if isinstance(account, dict) else None


def sign_in(book: dict, origin: Any, user_id: str, way: str, email: str) -> None:
    """A sign-in is finished. Sending is off afterwards for EVERY Walka account of the address.

    Whatever the file said before - copied from another computer, changed
    by hand - a sign-in never leaves sending on: only the question does.
    What became of each walk stays. The word that an earlier sign-in may
    still be in the system's store goes: this sign-in's key was written
    over that entry.
    """
    if not isinstance(way, str) or not isinstance(email, str):
        raise ValueError("the way and the e-mail address are text")
    place = address(book, origin)
    account = _account(place, user_id)
    for other in place["accounts"].values():
        other["sending"] = False
        other["chosen"] = None
        other["mark"] = None
    account["way"] = way
    account["email"] = email
    account["gone"] = False  # (somebody has just signed in to it)
    place["signed_in"] = user_id
    place["notice"] = None
    place["left_in_store"] = False


def sign_out(
    book: dict, origin: Any, *, notice: str | None = None, left_in_store: bool = False, gone: str | None = None
) -> None:
    """Nobody is signed in at this address, and sending is off for everybody under it.

    ``left_in_store``: this Walka held a sign-in in the system's store
    for secrets and is taking it out now. Until the store has said that
    it is gone (``sign_in_left(book, origin, False)``), the file says
    that it may still be there. Without it the word stays as it was: a
    sign-out that does not touch the store knows nothing new about it.

    ``gone``: the id of a Walka account that the accounts said was
    deleted. That is written down for it.
    """
    if notice is not None and notice not in NOTICES:
        raise ValueError("not a notice")
    if not isinstance(left_in_store, bool):
        raise ValueError("left in the store or not: yes or no")
    place = address(book, origin)
    place["signed_in"] = None
    for account in place["accounts"].values():
        account["sending"] = False
        account["mark"] = None
    place["notice"] = notice
    if left_in_store:
        place["left_in_store"] = True
    if gone is not None:
        account = place["accounts"].get(gone) if isinstance(gone, str) else None
        if isinstance(account, dict):
            account["gone"] = True


def set_sending(book: dict, origin: Any, user_id: str, on: bool, chosen: dict | None, mark: str | None = None) -> None:
    """Switch sending on or off for one Walka account.

    On needs the Walka account to be the one signed in, a choice ("all",
    or "from" a moment) and the mark that was made for this answer to
    the question: anything else raises ValueError, and ``change`` then
    writes nothing. Off is always taken, and no mark stays beside it.
    """
    place = address(book, origin)
    if on is not True:
        account = place["accounts"].get(user_id) if isinstance(user_id, str) else None
        if isinstance(account, dict):
            account["sending"] = False
            account["chosen"] = _valid_chosen(chosen)
            account["mark"] = None
        return
    fit = _valid_chosen(chosen)
    if fit is None:
        raise ValueError("sending is switched on only with a choice")
    if not valid_mark(mark):
        raise ValueError("sending is switched on only with a mark")
    if signed_in(book, origin) is None or place["signed_in"] != user_id:
        raise ValueError("sending is switched on only for the Walka account that is signed in")
    account = place["accounts"][user_id]
    account["sending"] = True
    account["chosen"] = fit
    account["mark"] = mark


def stop_if_marked(book: dict, origin: Any, user_id: str, mark: str) -> bool:
    """Switch sending off - but only if the file still says "on" for this Walka account with this very mark.

    For the sender, when the system's store does not hold the mark it
    read here. True: the file said so, and says "off" now (what was
    chosen stays, as when sending is stopped). False: the file has
    changed since - the other Walka on this data folder stopped sending,
    or the question was answered there anew - and nothing was changed:
    whoever asked looks again.
    """
    found = signed_in(book, origin)
    if found is None or found[0] != user_id:
        return False
    account = found[1]
    if account.get("sending") is not True or account.get("mark") != mark:
        return False
    account["sending"] = False
    account["mark"] = None
    return True


def note_walks(book: dict, origin: Any, user_id: str, entries: dict[str, dict]) -> None:
    """Write down what became of some walks (``walk_key`` -> added / there / aside). Touches nothing else."""
    fits: dict[str, dict] = {}
    for key, entry in entries.items():
        fit = _valid_entry(entry)
        if not _text(key, 200) or fit is None:
            raise ValueError("not an entry for a walk")
        fits[key] = fit
    walks = _account(address(book, origin), user_id)["walks"]
    for key, fit in fits.items():
        was = walks.get(key)
        if isinstance(was, dict) and was.get("state") == "added" and fit["state"] == "there" and not fit["deleted"]:
            # The other Walka on this folder added it; this one was told
            # "there already". Both are true, and the first knows the row.
            continue
        walks[key] = fit


def clear_notice(book: dict, origin: Any) -> None:
    place = book.get("addresses", {}).get(_origin_text(origin))
    if isinstance(place, dict):
        place["notice"] = None


def sign_in_left(book: dict, origin: Any, left: bool) -> None:
    """What the system's store for secrets said when a sign-in was taken out of it.

    False: it is gone. True: it may still be there. Counts only while
    nobody is signed in at this address: a sign-in that was finished
    meanwhile wrote its own key over the entry, and that one is wanted.
    """
    if not isinstance(left, bool):
        raise ValueError("left in the store or not: yes or no")
    place = book.get("addresses", {}).get(_origin_text(origin))
    if not isinstance(place, dict):
        if not left:
            return
        place = address(book, origin)
    place["left_in_store"] = left and place.get("signed_in") is None


def is_left_in_store(book: dict, origin: Any) -> bool:
    """Does the file say that a sign-in may still be in the system's store? Changes nothing."""
    place = book.get("addresses", {}).get(_origin_text(origin))
    return isinstance(place, dict) and place.get("left_in_store") is True and place.get("signed_in") is None


def a_copy(entry: Any) -> bool:
    """Is a copy of the walk in the Walka account, as far as the file knows: added, or there and not deleted there?"""
    if not isinstance(entry, dict):
        return False
    return entry.get("state") == "added" or (entry.get("state") == "there" and entry.get("deleted") is not True)


def copies_elsewhere(book: dict, origin: Any, but: str | None = None) -> frozenset[str]:
    """The walks (``walk_key``) of which a copy is in a Walka account of this address OTHER than ``but``.

    ``but`` is the Walka account that is signed in, or None. A Walka
    account that the accounts said was deleted does not count: its
    copies are gone with it. Changes nothing, and names nobody.
    """
    place = book.get("addresses", {}).get(_origin_text(origin))
    accounts = place.get("accounts") if isinstance(place, dict) else None
    found: set[str] = set()
    if not isinstance(accounts, dict):
        return frozenset()
    for user_id, account in accounts.items():
        if user_id == but or not isinstance(account, dict) or account.get("gone") is True:
            continue
        walks = account.get("walks")
        if isinstance(walks, dict):
            found.update(key for key, entry in walks.items() if a_copy(entry))
    return frozenset(found)


# ------------------------------------------------------------------ the file


def _slurp(path: Path) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def _lock(fd: int) -> None:
    """Take the lock or raise OSError at once. It is let go when ``fd`` is closed, however that comes about."""
    if _ON_WINDOWS:
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)  # Windows locks bytes from where the file stands: always the first one
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        # flock, not lockf: it also holds between two threads of one Walka.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    with contextlib.suppress(OSError):
        if _ON_WINDOWS:
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)


class Ledger:
    """The file in one data folder. Both calls block: use them off the loop."""

    def __init__(self, data_dir: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.dir = Path(data_dir)
        self.path = self.dir / FILE_NAME
        self.lock_path = self.dir / LOCK_NAME
        self._sleep = sleep

    def read(self) -> dict:
        """The book as it is in the file now. Makes no file and takes no lock.

        Missing or damaged: an empty book. A newer Walka's file:
        ``LedgerError("newer")``. There but not to be opened:
        ``LedgerError("cannot_read")`` - never an empty book.
        """
        return _parse(self._raw())

    def change(self, fn: Callable[[dict], T]) -> T:
        """Under the lock: read the file, let ``fn`` change the book in place, replace the file.

        Gives what ``fn`` gives. If ``fn`` raises, or changes nothing,
        nothing is written. What is written went through ``validate``.
        """
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600)
        except OSError:
            raise LedgerError("cannot_write") from None
        try:
            self._take(fd)
            try:
                book = _parse(self._raw())
                before = _dump(book)
                result = fn(book)
                after = _dump(validate(book))
                if after != before:
                    self._write(after)
                return result
            finally:
                _unlock(fd)
        finally:
            os.close(fd)

    def _raw(self) -> bytes | None:
        for attempt in range(READ_TRIES):
            try:
                return _slurp(self.path)
            except FileNotFoundError:
                return None
            except OSError:
                # On Windows the other Walka may be replacing it this instant.
                if attempt + 1 < READ_TRIES:
                    self._sleep(PAUSE_S)
        raise LedgerError("cannot_read")

    def _take(self, fd: int) -> None:
        for attempt in range(LOCK_TRIES):
            try:
                _lock(fd)
                return
            except OSError:
                if attempt + 1 < LOCK_TRIES:
                    self._sleep(PAUSE_S)
        raise LedgerError("cannot_write")

    def _write(self, text: str) -> None:
        tmp = self.dir / f"{FILE_NAME}.{os.getpid()}.{next(_NUMBERS)}.tmp"
        try:
            # Under the lock no Walka is in the middle of a write, so a temp
            # file lying here was left by one that died. It holds the e-mail
            # address: it goes.
            for left in self.dir.glob(f"{FILE_NAME}.*.tmp"):
                with contextlib.suppress(OSError):
                    left.unlink()
            # 0600: the file holds the e-mail address and the id, and the
            # data folder itself may be open to others on the computer.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(text.encode("ascii"))
                out.flush()
                os.fsync(out.fileno())  # a power cut must not leave an empty file where "off" was written
            self._replace(tmp)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise LedgerError("cannot_write") from None

    def _replace(self, tmp: Path) -> None:
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                # Windows, while a reader has the file open. It is open for a moment only.
                if attempt + 1 == REPLACE_TRIES:
                    raise
                self._sleep(PAUSE_S)
