"""The file walka-account.json (walka_account.ledger): lost, damaged, newer, two writers, and what it never does.

No test here sleeps or reads a clock: the waits of the ledger go to a
list, and every time in a book is a number written out below.
"""

from __future__ import annotations

import ast
import contextlib
import errno
import json
import os
import re
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from walka.walka_account import ledger
from walka.walka_account.ledger import FILE_NAME, LOCK_NAME, Ledger, LedgerError, walk_key

ORIGIN = "https://accounts.example"
ANNA = "0b1f6a52-7a3c-4c0e-9a51-3f0c2d9e8a11"
BEN = "7d2e9c40-1b5f-4e77-8f02-6a4d1c3b9e22"
ROW = "5c0a7e1e-3b1d-4d8a-9c55-0e2f4a6b8c10"
ALL = {"what": "all", "at": 1800000000.0}
EMPTY = {"version": 1, "addresses": {}}
THERE = {"state": "there", "row": None, "deleted": False}


class Naps:
    """Stands in for time.sleep: writes the waits down, and may do something at each."""

    def __init__(self, then=None) -> None:
        self.taken: list[float] = []
        self.then = then

    def __call__(self, seconds: float) -> None:
        self.taken.append(seconds)
        if self.then is not None:
            self.then(len(self.taken))


def never(_book):
    raise AssertionError("the book was handed out")


def signed_in_and_on(folder: Path, user: str = ANNA) -> Ledger:
    book = Ledger(folder, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, user, "github", "a@b.example"))
    book.change(lambda b: ledger.set_sending(b, ORIGIN, user, True, dict(ALL)))
    return book


def account_of(book: dict, user: str = ANNA) -> dict:
    return book["addresses"][ORIGIN]["accounts"][user]


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def raw_account(**changes) -> bytes:
    """A file as another computer, or a hand, might have left it: ANNA signed in, with what ``changes`` say."""
    account = {"way": "github", "email": "a@b.example", "sending": True, "chosen": dict(ALL), "walks": {}}
    account.update(changes)
    place = {"signed_in": ANNA, "notice": None, "accounts": {ANNA: account}}
    return json.dumps({"version": 1, "addresses": {ORIGIN: place}}).encode()


# ---------------------------------------------------------------- the key of a walk


def test_a_whole_number_and_the_same_moment_with_a_point_are_one_walk():
    assert walk_key("20251121-213000", 1763760600) == walk_key("20251121-213000", 1763760600.0)
    assert walk_key("20251121-213000", 1763760600) == "20251121-213000|1763760600.0"


def test_the_same_id_with_another_start_is_another_walk(tmp_path):
    # An id is given out again after a walk is deleted: the start tells the two apart.
    book = signed_in_and_on(tmp_path)
    first, second = walk_key("20261119-213000", 1795210200.0), walk_key("20261119-213000", 1795210200.5)
    assert first != second
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {first: {"state": "added", "row": ROW, "at": 1.0}}))
    walks = account_of(book.read())["walks"]
    assert first in walks and second not in walks


# ---------------------------------------------------------------- lost, damaged, newer


def test_a_lost_file_is_an_empty_book_and_reading_makes_nothing(tmp_path):
    assert Ledger(tmp_path).read() == EMPTY
    assert names(tmp_path) == []
    nowhere = tmp_path / "not" / "there"
    assert Ledger(nowhere).read() == EMPTY
    assert not nowhere.exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b"[1,2]",
        b'"text"',
        b"\xff\xfe\x00",
        b'{"version":1}',
        b'{"version":1,"addresses":[]}',
        b'{"version":"1","addresses":{}}',
        b'{"version":true,"addresses":{}}',
        b'{"version":0,"addresses":{}}',
        b'{"addresses":{}}',
        b'{"version":1,"addresses":{"https://accounts.example":NaN}}',
        b'{"version":1,"addresses":{"https://accounts.example":{"signed_in":Infinity}}}',
        b"[" * 200_000,
    ],
)
def test_a_damaged_file_is_an_empty_book(tmp_path, raw):
    (tmp_path / FILE_NAME).write_bytes(raw)
    assert Ledger(tmp_path).read() == EMPTY


def test_wrong_things_inside_are_dropped_and_the_rest_stays(tmp_path):
    key = walk_key("20261119-213000", 1795210200)
    good = {"state": "added", "row": ROW, "at": 1800000000}
    walks = {
        key: good,
        "there|1.0": {"state": "there", "row": 7, "deleted": "yes"},
        "aside|2.0": {"state": "aside", "code": 23514, "message": "It lasts\n too long." + "x" * 400, "version": None},
        "unknown|3.0": {"state": "queued"},
        "list|4.0": [],
        "no row|5.0": {"state": "added", "at": 1.0},
        "no time|6.0": {"state": "added", "row": ROW, "at": "now"},
        "flag|7.0": {"state": "added", "row": ROW, "at": True},
        "": dict(good),
    }
    raw = {
        "version": 1,
        "surprise": {"token": "x"},
        "addresses": {
            ORIGIN: {
                "signed_in": ANNA,
                "notice": "something else",
                "extra": 1,
                "accounts": {
                    ANNA: {"way": 5, "email": ["a"], "sending": "yes", "chosen": dict(ALL), "walks": walks, "more": 1},
                    BEN: "not an account",
                    "": {"way": "github"},
                },
            },
            "https://other.example": "not an address",
            "https://third.example": {"signed_in": BEN, "accounts": []},
        },
    }
    (tmp_path / FILE_NAME).write_text(json.dumps(raw), "utf-8")
    assert Ledger(tmp_path).read() == {
        "version": 1,
        "addresses": {
            ORIGIN: {
                "signed_in": ANNA,
                "notice": None,
                "accounts": {
                    ANNA: {
                        "way": "",
                        "email": "",
                        "sending": False,
                        "chosen": {"what": "all", "at": 1800000000.0},
                        "walks": {
                            key: {"state": "added", "row": ROW, "at": 1800000000.0},
                            "there|1.0": {"state": "there", "row": None, "deleted": False},
                            "aside|2.0": {
                                "state": "aside",
                                "code": "",
                                "message": ("It lasts too long." + "x" * 400)[:300],
                                "version": "",
                            },
                        },
                    }
                },
            }
        },
    }


@pytest.mark.parametrize("version", ["2", "1.5", "7e3", "1" + "0" * 400])
def test_a_file_of_a_newer_walka_is_left_alone(tmp_path, version):
    raw = b'{"version":%s,"addresses":{},"something":"new"}' % version.encode()
    (tmp_path / FILE_NAME).write_bytes(raw)
    book = Ledger(tmp_path, sleep=Naps())
    with pytest.raises(LedgerError) as told:
        book.read()
    assert told.value.code == "newer"
    with pytest.raises(LedgerError) as told:
        book.change(never)
    assert told.value.code == "newer"
    assert (tmp_path / FILE_NAME).read_bytes() == raw
    with pytest.raises(LedgerError):
        ledger.validate({"version": 2, "addresses": {}})


# ---------------------------------------------------------------- there, but not to be opened


def test_a_file_that_cannot_be_opened_is_never_an_empty_book(tmp_path):
    # A folder where the file should be: the plainest thing that is there and cannot be opened.
    (tmp_path / FILE_NAME).mkdir()
    naps = Naps()
    book = Ledger(tmp_path, sleep=naps)
    with pytest.raises(LedgerError) as told:
        book.read()
    assert told.value.code == "cannot_read"
    assert naps.taken == [ledger.PAUSE_S] * (ledger.READ_TRIES - 1)
    with pytest.raises(LedgerError) as told:
        book.change(never)
    assert told.value.code == "cannot_read"
    assert (tmp_path / FILE_NAME).is_dir()
    # The lock was let go all the same.
    (tmp_path / FILE_NAME).rmdir()
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert ledger.signed_in(book.read(), ORIGIN) is not None


def test_a_file_that_is_busy_for_a_moment_is_read_at_the_next_try(tmp_path, monkeypatch):
    signed_in_and_on(tmp_path)
    real, tries = ledger._slurp, []

    def busy_twice(path):
        tries.append(path)
        if len(tries) <= 2:
            raise PermissionError(errno.EACCES, "in use", str(path))
        return real(path)

    monkeypatch.setattr(ledger, "_slurp", busy_twice)
    naps = Naps()
    assert account_of(Ledger(tmp_path, sleep=naps).read())["sending"] is True
    assert naps.taken == [0.05, 0.05]


def test_a_file_that_stays_shut_is_not_written_over(tmp_path, monkeypatch):
    book = signed_in_and_on(tmp_path)
    before = (tmp_path / FILE_NAME).read_bytes()

    def shut(path):
        raise PermissionError(errno.EACCES, "in use", str(path))

    monkeypatch.setattr(ledger, "_slurp", shut)
    with pytest.raises(LedgerError) as told:
        book.change(lambda b: ledger.sign_out(b, ORIGIN))
    assert told.value.code == "cannot_read"
    assert (tmp_path / FILE_NAME).read_bytes() == before
    # The system's own words name the folder, and with it the person: none of them come along.
    assert str(told.value) == "cannot_read" and told.value.args == ("cannot_read",)
    assert told.value.__cause__ is None and told.value.__context__ is None


# ---------------------------------------------------------------- writing


def test_the_file_is_written_as_agreed(tmp_path):
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    choice = {"what": "from", "from": 1800000000, "at": 1800000000}
    book.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, True, choice))
    entries = {
        walk_key("20261119-213000", 1795210200): {"state": "added", "row": ROW, "at": 1800000100},
        walk_key("20261120-070000", 1795244400): {"state": "there", "row": None, "deleted": False},
        walk_key("20261120-190000", 1795287600): {
            "state": "aside",
            "code": "23514",
            "message": "It lasts longer than two days.",
            "version": "0.8.0",
        },
    }
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, entries))
    text = (tmp_path / FILE_NAME).read_text("ascii")
    assert json.loads(text) == {
        "version": 1,
        "addresses": {
            ORIGIN: {
                "signed_in": ANNA,
                "notice": None,
                "accounts": {
                    ANNA: {
                        "way": "github",
                        "email": "a@b.example",
                        "sending": True,
                        "chosen": {"what": "from", "from": 1800000000.0, "at": 1800000000.0},
                        "walks": {
                            "20261119-213000|1795210200.0": {"state": "added", "row": ROW, "at": 1800000100.0},
                            "20261120-070000|1795244400.0": {"state": "there", "row": None, "deleted": False},
                            "20261120-190000|1795287600.0": {
                                "state": "aside",
                                "code": "23514",
                                "message": "It lasts longer than two days.",
                                "version": "0.8.0",
                            },
                        },
                    }
                },
            }
        },
    }
    assert '"from":1800000000.0' in text and "\n" not in text
    assert json.loads(text) == book.read()


def test_an_entry_stays_when_the_file_is_read_again(tmp_path):
    key = walk_key("20261119-213000", 1795210200)
    signed_in_and_on(tmp_path).change(
        lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: {"state": "added", "row": ROW, "at": 1800000100.0}})
    )
    again = Ledger(tmp_path, sleep=Naps())
    assert account_of(again.read())["walks"][key] == {"state": "added", "row": ROW, "at": 1800000100.0}
    # Another change, of another thing, by another object: it is still there.
    again.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, False, None))
    assert account_of(Ledger(tmp_path).read())["walks"][key]["row"] == ROW


def test_what_is_read_is_a_copy(tmp_path):
    book = signed_in_and_on(tmp_path)
    account_of(book.read())["sending"] = False
    assert account_of(book.read())["sending"] is True


def test_when_the_change_fails_nothing_is_written(tmp_path):
    class Mine(Exception):
        pass

    def spoil(b):
        ledger.sign_out(b, ORIGIN)
        raise Mine()

    fresh = Ledger(tmp_path / "fresh", sleep=Naps())
    with pytest.raises(Mine):
        fresh.change(spoil)
    assert names(tmp_path / "fresh") == [LOCK_NAME]

    book = signed_in_and_on(tmp_path)
    before = (tmp_path / FILE_NAME).read_bytes()
    with pytest.raises(Mine):
        book.change(spoil)
    assert (tmp_path / FILE_NAME).read_bytes() == before
    assert account_of(book.read())["sending"] is True
    assert sorted({FILE_NAME, LOCK_NAME, "fresh"}) == names(tmp_path)


def test_a_change_that_changes_nothing_writes_nothing(tmp_path):
    book = Ledger(tmp_path, sleep=Naps())
    assert book.change(lambda b: ledger.signed_in(b, ORIGIN)) is None
    assert book.change(lambda b: ledger.address(b, ORIGIN)["signed_in"]) is None
    assert names(tmp_path) == [LOCK_NAME]
    # A damaged file is not tidied away for nothing either.
    (tmp_path / FILE_NAME).write_bytes(b"not json")
    book.change(lambda b: ledger.sign_out(b, ORIGIN))
    assert (tmp_path / FILE_NAME).read_bytes() == b"not json"


def test_a_change_gives_back_what_the_function_gives(tmp_path):
    book = signed_in_and_on(tmp_path)
    assert book.change(lambda b: ledger.signed_in(b, ORIGIN)[0]) == ANNA


def test_every_temp_file_has_a_name_of_its_own_and_none_is_left(tmp_path, monkeypatch):
    seen: list[str] = []
    real = os.replace

    def watch(source, target):
        seen.append(Path(source).name)
        assert Path(target) == tmp_path / FILE_NAME
        real(source, target)

    monkeypatch.setattr(ledger.os, "replace", watch)
    one, two = Ledger(tmp_path, sleep=Naps()), Ledger(tmp_path, sleep=Naps())
    one.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    two.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, True, dict(ALL)))
    one.change(lambda b: ledger.sign_out(b, ORIGIN))
    assert len(seen) == 3 and len(set(seen)) == 3
    for name in seen:
        assert re.fullmatch(rf"walka-account\.json\.{os.getpid()}\.\d+\.tmp", name)
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])


def test_a_temp_file_left_by_a_walka_that_died_is_harmless(tmp_path):
    left = tmp_path / f"{FILE_NAME}.{os.getpid()}.1.tmp"
    left.write_bytes(b'{"version":1,"addresses":{"https://accounts.example":{"signed_in":"x"')
    book = Ledger(tmp_path, sleep=Naps())
    assert book.read() == EMPTY
    assert left.exists()  # reading touches nothing
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert ledger.signed_in(book.read(), ORIGIN)[0] == ANNA
    # It held an e-mail address, perhaps: the next write, under the lock, takes it away.
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])


@pytest.mark.skipif(os.name != "posix", reason="Windows has no such modes")
def test_the_file_is_for_its_owner_only(tmp_path):
    old = os.umask(0)  # with the widest mask, so that 0600 is the ledger's doing
    try:
        folder = tmp_path / "data"
        signed_in_and_on(folder)
    finally:
        os.umask(old)
    assert (folder / FILE_NAME).stat().st_mode & 0o777 == 0o600
    assert (folder / LOCK_NAME).stat().st_mode & 0o777 == 0o600


def test_replacing_is_tried_again_while_somebody_has_the_file_open(tmp_path, monkeypatch):
    book = signed_in_and_on(tmp_path)
    real, tries = os.replace, []

    def open_elsewhere(source, target):
        tries.append(source)
        if len(tries) <= 3:
            raise PermissionError(errno.EACCES, "in use")
        real(source, target)

    monkeypatch.setattr(ledger.os, "replace", open_elsewhere)
    naps = Naps()
    Ledger(tmp_path, sleep=naps).change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, False, None))
    assert naps.taken == [0.05] * 3 and len(set(tries)) == 1
    assert account_of(book.read())["sending"] is False


def test_a_write_that_fails_says_so_and_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    book = signed_in_and_on(tmp_path)
    before = (tmp_path / FILE_NAME).read_bytes()

    def always_open(source, target):
        raise PermissionError(errno.EACCES, "in use")

    naps = Naps()
    with monkeypatch.context() as patch:
        patch.setattr(ledger.os, "replace", always_open)
        with pytest.raises(LedgerError) as told:
            Ledger(tmp_path, sleep=naps).change(lambda b: ledger.sign_out(b, ORIGIN))
    assert told.value.code == "cannot_write" and told.value.__cause__ is None and told.value.__suppress_context__
    assert naps.taken == [0.05] * (ledger.REPLACE_TRIES - 1)

    def disk_full(source, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    naps = Naps()
    with monkeypatch.context() as patch:
        patch.setattr(ledger.os, "replace", disk_full)
        with pytest.raises(LedgerError) as told:
            Ledger(tmp_path, sleep=naps).change(lambda b: ledger.sign_out(b, ORIGIN))
    assert told.value.code == "cannot_write"
    assert naps.taken == []  # only "somebody has it open" is waited for: a full disk stays full
    assert (tmp_path / FILE_NAME).read_bytes() == before
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])
    # And the lock is free again.
    book.change(lambda b: ledger.sign_out(b, ORIGIN))
    assert ledger.signed_in(book.read(), ORIGIN) is None


def test_a_folder_that_cannot_be_made_is_cannot_write(tmp_path):
    (tmp_path / "file").write_bytes(b"")
    with pytest.raises(LedgerError) as told:
        Ledger(tmp_path / "file" / "data", sleep=Naps()).change(never)
    assert told.value.code == "cannot_write"


def test_the_temp_file_is_new_and_is_on_the_disk_before_it_takes_the_place(tmp_path, monkeypatch):
    done: list[str] = []
    how_opened: list[int] = []
    real_open, real_fsync, real_replace = os.open, os.fsync, os.replace

    def watch_open(path, how, *more, **kind):
        if str(path).endswith(".tmp"):
            done.append("open")
            how_opened.append(how)
        return real_open(path, how, *more, **kind)

    def watch_fsync(fd):
        done.append("fsync")
        real_fsync(fd)

    def watch_replace(source, target):
        done.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(ledger.os, "open", watch_open)
    monkeypatch.setattr(ledger.os, "fsync", watch_fsync)
    monkeypatch.setattr(ledger.os, "replace", watch_replace)
    Ledger(tmp_path, sleep=Naps()).change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    # A power cut between the two must not leave an empty file where "off" was written.
    assert done == ["open", "fsync", "replace"]
    # Never a file that was lying there already: it could be a link to somewhere else.
    assert how_opened[0] & os.O_EXCL and how_opened[0] & os.O_CREAT


def test_the_lock_file_is_closed_again_whatever_happens(tmp_path, monkeypatch):
    open_now: set[int] = set()
    times: list[int] = []
    real_open, real_close = os.open, os.close

    def watch_open(path, how, *more, **kind):
        fd = real_open(path, how, *more, **kind)
        if Path(path).name == LOCK_NAME:
            open_now.add(fd)
            times.append(fd)
        return fd

    def watch_close(fd):
        open_now.discard(fd)
        real_close(fd)

    monkeypatch.setattr(ledger.os, "open", watch_open)
    monkeypatch.setattr(ledger.os, "close", watch_close)
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert len(times) == 1 and not open_now
    with pytest.raises(ZeroDivisionError):  # the change fails
        book.change(lambda b: 1 // 0)
    assert len(times) == 2 and not open_now
    holder = real_open(tmp_path / LOCK_NAME, os.O_RDWR)
    try:
        ledger._lock(holder)
        with pytest.raises(LedgerError):  # the lock is never had
            book.change(never)
    finally:
        real_close(holder)
    assert len(times) == 3 and not open_now
    (tmp_path / FILE_NAME).unlink()
    (tmp_path / FILE_NAME).mkdir()
    with pytest.raises(LedgerError):  # the file cannot be read
        book.change(never)
    assert len(times) == 4 and not open_now


def test_what_is_not_plain_ascii_is_written_as_plain_ascii_and_comes_back_whole(tmp_path):
    # The file is read on Windows and on a Mac alike: nothing in it depends on how a system writes text.
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "j\u00fcrgen@b.example"))
    key = walk_key("20261119-213000", 1795210200)
    aside = {"state": "aside", "code": "23514", "message": "Zu gro\u00df f\u00fcr \u6771\u4eac.", "version": "0.8.0"}
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: aside}))
    raw = (tmp_path / FILE_NAME).read_bytes()
    assert max(raw) < 128 and b"\r" not in raw and b"\n" not in raw
    account = account_of(book.read())
    assert account["email"] == "j\u00fcrgen@b.example" and account["walks"][key] == aside


# ---------------------------------------------------------------- two Walkas on one folder


def test_one_switches_off_and_the_other_then_notes_a_walk_sending_stays_off(tmp_path):
    a = signed_in_and_on(tmp_path)
    b = Ledger(tmp_path, sleep=Naps())
    seen_by_b = b.read()
    assert account_of(seen_by_b)["sending"] is True
    a.change(lambda book: ledger.set_sending(book, ORIGIN, ANNA, False, None))
    # B still holds its old picture, and writes down a walk it has just sent.
    key = walk_key("20261119-213000", 1795210200)
    b.change(lambda book: ledger.note_walks(book, ORIGIN, ANNA, {key: {"state": "added", "row": ROW, "at": 2.0}}))
    for reader in (a, b):
        account = account_of(reader.read())
        assert account["sending"] is False and account["chosen"] is None
        assert account["walks"][key]["state"] == "added"


def test_one_signs_out_and_the_other_then_notes_a_walk_nobody_is_signed_in(tmp_path):
    a = signed_in_and_on(tmp_path)
    b = Ledger(tmp_path, sleep=Naps())
    a.change(lambda book: ledger.sign_out(book, ORIGIN, notice="ended"))
    key = walk_key("20261119-213000", 1795210200)
    b.change(lambda book: ledger.note_walks(book, ORIGIN, ANNA, {key: dict(THERE)}))
    after = b.read()
    assert ledger.signed_in(after, ORIGIN) is None
    assert after["addresses"][ORIGIN]["notice"] == "ended"
    assert account_of(after)["sending"] is False and key in account_of(after)["walks"]


def test_while_one_changes_the_file_the_other_waits_and_then_gives_up(tmp_path):
    a = signed_in_and_on(tmp_path)
    naps = Naps()
    b = Ledger(tmp_path, sleep=naps)

    def meanwhile(book):
        with pytest.raises(LedgerError) as told:
            b.change(never)
        assert told.value.code == "cannot_write"
        assert account_of(b.read())["sending"] is True  # reading waits for nobody
        ledger.set_sending(book, ORIGIN, ANNA, False, None)

    a.change(meanwhile)
    assert naps.taken == [0.05] * (ledger.LOCK_TRIES - 1)  # about five seconds, and no longer
    assert account_of(b.read())["sending"] is False
    b.change(lambda book: ledger.sign_out(book, ORIGIN))  # free again


def test_the_one_who_waits_goes_on_when_the_lock_is_let_go(tmp_path):
    signed_in_and_on(tmp_path)
    holder = os.open(tmp_path / LOCK_NAME, os.O_RDWR)
    try:
        ledger._lock(holder)

        def let_go(nap: int) -> None:
            if nap == 3:
                ledger._unlock(holder)

        naps = Naps(then=let_go)
        Ledger(tmp_path, sleep=naps).change(lambda b: ledger.sign_out(b, ORIGIN))
        assert len(naps.taken) == 3
    finally:
        os.close(holder)
    assert ledger.signed_in(Ledger(tmp_path).read(), ORIGIN) is None


def windows_stand_in(monkeypatch, unlocking_fails: bool = False) -> tuple[list[tuple[int, int, int]], set[int]]:
    """Windows' own lock, played: what was asked (how, at which byte, how many) and who holds it."""
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK, fake.LK_UNLCK = 2, 0  # the numbers of Windows' own msvcrt
    calls: list[tuple[int, int, int]] = []
    held: set[int] = set()

    def locking(fd: int, mode: int, nbytes: int) -> None:
        calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))
        if mode == fake.LK_NBLCK:
            if held:
                raise OSError(errno.EACCES, "Permission denied")
            held.add(fd)
        else:
            held.discard(fd)
            if unlocking_fails:
                raise OSError(errno.EACCES, "Permission denied")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(ledger, "_ON_WINDOWS", True)
    return calls, held


def test_on_windows_the_first_byte_of_the_lock_file_is_locked(tmp_path, monkeypatch):
    # The real call runs on Windows only. Here: that it is asked for the
    # right byte, without waiting, and is given back.
    calls, held = windows_stand_in(monkeypatch)
    naps = Naps()
    a, b = Ledger(tmp_path, sleep=Naps()), Ledger(tmp_path, sleep=naps)
    a.change(lambda book: ledger.sign_in(book, ORIGIN, ANNA, "github", "a@b.example"))
    assert calls == [(2, 0, 1), (0, 0, 1)] and not held

    def meanwhile(book):
        with pytest.raises(LedgerError) as told:
            b.change(never)
        assert told.value.code == "cannot_write"

    a.change(meanwhile)
    assert len(naps.taken) == ledger.LOCK_TRIES - 1 and not held
    assert ledger.signed_in(b.read(), ORIGIN)[0] == ANNA


def test_on_windows_it_is_the_first_byte_wherever_the_file_stands(tmp_path, monkeypatch):
    # Windows locks from where the file stands. Two Walkas that stood at
    # different bytes would each lock a byte of their own, and both go in.
    calls, held = windows_stand_in(monkeypatch)
    fd = os.open(tmp_path / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"moved")
        ledger._lock(fd)
        os.lseek(fd, 3, os.SEEK_SET)
        ledger._unlock(fd)
    finally:
        os.close(fd)
    assert calls == [(2, 0, 1), (0, 0, 1)] and not held


def test_a_lock_that_cannot_be_given_back_spoils_nothing(tmp_path, monkeypatch):
    # Closing the lock file lets go of it in any case: the change is done and its answer is given.
    windows_stand_in(monkeypatch, unlocking_fails=True)
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert book.change(lambda b: ledger.signed_in(b, ORIGIN)[0]) == ANNA


def test_many_threads_lose_no_entry(tmp_path, monkeypatch):
    # Patient: this is about losing nothing, not about how fast a CI machine's disk is.
    for tries in ("LOCK_TRIES", "REPLACE_TRIES", "READ_TRIES"):
        monkeypatch.setattr(ledger, tries, 1200)
    signed_in_and_on(tmp_path)
    failures: list[BaseException] = []

    def write(who: int) -> None:
        try:
            book = Ledger(tmp_path)
            for n in range(6):
                key = walk_key(f"walk-{who}-{n}", 1795210200 + n)
                book.change(lambda b, key=key: ledger.note_walks(b, ORIGIN, ANNA, {key: dict(THERE)}))
        except BaseException as exc:  # noqa: BLE001 - told to the test below
            failures.append(exc)

    def read() -> None:
        try:
            book = Ledger(tmp_path)
            for _ in range(60):
                assert account_of(book.read())["sending"] is True
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=write, args=(who,)) for who in range(8)]
    threads += [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not failures and not any(thread.is_alive() for thread in threads)
    assert len(account_of(Ledger(tmp_path).read())["walks"]) == 48
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])


CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from walka.walka_account import ledger
ledger.LOCK_TRIES = ledger.REPLACE_TRIES = 1200  # patient: a slow disk is not what this is about
book = ledger.Ledger(Path(sys.argv[2]))
for n in range(25):
    key = ledger.walk_key(sys.argv[3] + "-" + str(n), 1795210200 + n)
    entry = {key: {"state": "there", "row": None, "deleted": False}}
    book.change(lambda b: ledger.note_walks(b, "https://accounts.example", sys.argv[4], entry))
"""


def test_two_programs_on_one_folder_lose_no_entry(tmp_path):
    # The real thing: two processes, the system's own lock between them
    # (on Windows this is the only place where that lock is really taken).
    signed_in_and_on(tmp_path)
    source = str(Path(ledger.__file__).resolve().parents[2])
    children = [
        subprocess.Popen([sys.executable, "-c", CHILD, source, str(tmp_path), name, ANNA], stderr=subprocess.PIPE)
        for name in ("one", "two")
    ]
    for child in children:
        _, said = child.communicate(timeout=120)
        assert child.returncode == 0, said.decode("utf-8", "replace")[-400:]
    account = account_of(Ledger(tmp_path).read())
    assert len(account["walks"]) == 50 and account["sending"] is True
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])


HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from walka.walka_account import ledger

def hold(book):
    print("holding", flush=True)
    sys.stdin.readline()  # until the test says go on - or ends this program
    entry = {sys.argv[4]: {"state": "there", "row": None, "deleted": False}}
    ledger.note_walks(book, "https://accounts.example", sys.argv[3], entry)

ledger.Ledger(Path(sys.argv[2])).change(hold)
print("done", flush=True)
"""

KNOCKER = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from walka.walka_account import ledger

ledger.LOCK_TRIES = 3
try:
    ledger.Ledger(Path(sys.argv[2]), sleep=lambda seconds: None).change(lambda book: None)
    print("got in", flush=True)
except ledger.LedgerError as told:
    print("refused " + told.code, flush=True)
sys.stdin.readline()
ledger.LOCK_TRIES = 1200  # the system may take a moment to let go of a dead program's lock
entry = {sys.argv[4]: {"state": "there", "row": None, "deleted": False}}
ledger.Ledger(Path(sys.argv[2])).change(
    lambda book: ledger.note_walks(book, "https://accounts.example", sys.argv[3], entry)
)
print("done", flush=True)
"""


@pytest.mark.parametrize("the_holder", ["lets go", "dies"])
def test_a_second_program_is_kept_out_until_the_first_lets_go_or_dies(tmp_path, the_holder):
    # Step by step, so that nothing hangs on luck: one program holds the
    # lock inside its change; the other must be refused; then the first
    # finishes - or is ended where it stands, as by a crash - and the
    # other must get in. On Windows and on a Mac this runs on CI only.
    signed_in_and_on(tmp_path)
    source = str(Path(ledger.__file__).resolve().parents[2])
    one, two = walk_key("20261119-213000", 1795210200), walk_key("20261120-070000", 1795244400)
    programs: list[subprocess.Popen] = []
    alarms: list[threading.Timer] = []

    def start(code: str, key: str) -> subprocess.Popen:
        program = subprocess.Popen(
            [sys.executable, "-c", code, source, str(tmp_path), ANNA, key],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        programs.append(program)
        alarm = threading.Timer(120, program.kill)  # a program that hangs ends the test, it does not hold it
        alarm.daemon = True
        alarm.start()
        alarms.append(alarm)
        return program

    def go_on(program: subprocess.Popen) -> None:
        program.stdin.write("\n")
        program.stdin.flush()

    try:
        holder = start(HOLDER, one)
        assert holder.stdout.readline().strip() == "holding"
        knocker = start(KNOCKER, two)
        assert knocker.stdout.readline().strip() == "refused cannot_write"
        if the_holder == "dies":
            holder.kill()
            holder.wait(60)
        else:
            go_on(holder)
            assert holder.stdout.readline().strip() == "done"
            assert holder.wait(60) == 0
        go_on(knocker)
        assert knocker.stdout.readline().strip() == "done"
        assert knocker.wait(60) == 0
    finally:
        for alarm in alarms:
            alarm.cancel()
        for program in programs:
            if program.poll() is None:
                program.kill()
            program.wait(60)
            for pipe in (program.stdin, program.stdout):
                with contextlib.suppress(OSError):  # a pipe to a program that is gone: what failed is said above
                    pipe.close()
    account = account_of(Ledger(tmp_path).read())
    # The one that died had changed its book and written nothing: its walk is not there.
    assert set(account["walks"]) == ({two} if the_holder == "dies" else {one, two})
    assert account["sending"] is True
    assert names(tmp_path) == sorted([FILE_NAME, LOCK_NAME])


# ---------------------------------------------------------------- no road past the question


def test_a_sign_in_switches_sending_off_for_every_walka_account_of_the_address(tmp_path):
    book = signed_in_and_on(tmp_path)
    key = walk_key("20261119-213000", 1795210200)
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: dict(THERE)}))
    book.change(lambda b: ledger.sign_in(b, ORIGIN, BEN, "google", "ben@b.example"))
    after = book.read()
    assert ledger.signed_in(after, ORIGIN)[0] == BEN
    for user in (ANNA, BEN):
        assert account_of(after, user)["sending"] is False and account_of(after, user)["chosen"] is None
    assert key in account_of(after, ANNA)["walks"]  # what was sent is still known

    # Signing in as the SAME Walka account does not bring "on" back either.
    book = signed_in_and_on(tmp_path / "again")
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert account_of(book.read())["sending"] is False and account_of(book.read())["chosen"] is None


def test_a_file_carried_in_with_sending_on_is_off_after_any_sign_in(tmp_path):
    # A data folder from another computer: "on", with a choice, and nobody signed in here.
    place = {
        "signed_in": None,
        "notice": None,
        "accounts": {ANNA: {"way": "github", "email": "a@b.example", "sending": True, "chosen": dict(ALL)}},
    }
    (tmp_path / FILE_NAME).write_text(json.dumps({"version": 1, "addresses": {ORIGIN: place}}), "utf-8")
    book = Ledger(tmp_path, sleep=Naps())
    assert account_of(book.read())["sending"] is False
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert account_of(book.read()) == {
        "way": "github",
        "email": "a@b.example",
        "sending": False,
        "chosen": None,
        "walks": {},
    }
    assert b'"sending":true' not in (tmp_path / FILE_NAME).read_bytes()


def test_sending_on_is_believed_only_with_a_choice_and_for_the_one_signed_in(tmp_path):
    def sending(raw: bytes) -> bool:
        (tmp_path / FILE_NAME).write_bytes(raw)
        return account_of(Ledger(tmp_path).read())["sending"]

    assert sending(raw_account()) is True  # the canary: "on" can be read at all
    assert sending(raw_account(chosen=None)) is False
    assert sending(json.dumps(json.loads(raw_account()) | {"x": 1}).encode()) is True
    without = json.loads(raw_account())
    del without["addresses"][ORIGIN]["accounts"][ANNA]["chosen"]
    assert sending(json.dumps(without).encode()) is False
    assert sending(raw_account(chosen="all")) is False
    assert sending(raw_account(chosen={"what": "from"})) is False
    assert sending(raw_account(chosen={"what": "from", "from": "yesterday"})) is False
    assert sending(raw_account(chosen={"what": "some"})) is False
    assert sending(raw_account(sending=1)) is False
    assert sending(raw_account(sending="true")) is False
    assert sending(raw_account(chosen={"what": "from", "from": 1800000000})) is True
    # "On" for a Walka account that is not the one signed in.
    other = json.loads(raw_account())
    other["addresses"][ORIGIN]["signed_in"] = BEN
    other["addresses"][ORIGIN]["accounts"][BEN] = {"way": "google", "email": "ben@b.example"}
    assert sending(json.dumps(other).encode()) is False
    # Signed in as somebody of whom the file knows nothing: nobody is signed in.
    ghost = json.loads(raw_account())
    ghost["addresses"][ORIGIN]["signed_in"] = BEN
    assert sending(json.dumps(ghost).encode()) is False
    assert ledger.signed_in(Ledger(tmp_path).read(), ORIGIN) is None


def test_switching_on_needs_a_choice_and_the_one_signed_in(tmp_path):
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    for chosen in (None, {}, {"what": "from"}, {"what": "everything"}, "all"):
        with pytest.raises(ValueError):
            book.change(lambda b, chosen=chosen: ledger.set_sending(b, ORIGIN, ANNA, True, chosen))
    with pytest.raises(ValueError):
        book.change(lambda b: ledger.set_sending(b, ORIGIN, BEN, True, dict(ALL)))
    book.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, 1, dict(ALL)))  # "on" is True and nothing like it
    with pytest.raises(ValueError):
        book.change(lambda b: ledger.set_sending(b, "https://other.example", ANNA, True, dict(ALL)))
    assert account_of(book.read())["sending"] is False

    book.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, True, {"what": "from", "from": 1800000000.0}))
    assert account_of(book.read())["sending"] is True
    assert account_of(book.read())["chosen"] == {"what": "from", "from": 1800000000.0}
    book.change(lambda b: ledger.sign_out(b, ORIGIN))
    with pytest.raises(ValueError):  # signed out: no switching on
        book.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, True, dict(ALL)))
    # Off is always taken, for anybody, and makes nobody up.
    book.change(lambda b: ledger.set_sending(b, ORIGIN, ANNA, False, None))
    book.change(lambda b: ledger.set_sending(b, ORIGIN, BEN, False, None))
    assert set(book.read()["addresses"][ORIGIN]["accounts"]) == {ANNA}


def test_what_a_change_leaves_behind_goes_through_the_same_sieve(tmp_path):
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))

    def by_hand(b):
        account_of(b)["sending"] = True  # no choice beside it
        account_of(b)["refresh_token"] = "v4xk2j3h5l6m"

    book.change(by_hand)
    assert account_of(book.read())["sending"] is False
    assert b"v4xk2j3h5l6m" not in (tmp_path / FILE_NAME).read_bytes()


def test_the_changes_switch_off_by_themselves_not_only_through_the_sieve():
    # ``validate`` would hide a change that forgot to: here the book is looked at as the change left it.
    book = ledger.empty_book()
    ledger.sign_in(book, ORIGIN, ANNA, "github", "a@b.example")
    ledger.set_sending(book, ORIGIN, ANNA, True, dict(ALL))
    place = book["addresses"][ORIGIN]
    assert account_of(book)["sending"] is True
    ledger.sign_out(book, ORIGIN, notice="ended")
    assert account_of(book)["sending"] is False and place["signed_in"] is None and place["notice"] == "ended"
    account_of(book)["sending"] = True  # as a file from elsewhere might say
    ledger.sign_in(book, ORIGIN, BEN, "google", "ben@b.example")
    assert place["notice"] is None and place["signed_in"] == BEN
    assert account_of(book)["sending"] is False and account_of(book)["chosen"] is None
    ledger.set_sending(book, ORIGIN, BEN, True, dict(ALL))
    ledger.set_sending(book, ORIGIN, BEN, False, None)
    assert account_of(book, BEN)["sending"] is False and account_of(book, BEN)["chosen"] is None


def test_noting_a_walk_touches_nothing_but_the_walks():
    book = ledger.empty_book()
    ledger.sign_in(book, ORIGIN, ANNA, "github", "a@b.example")
    ledger.set_sending(book, ORIGIN, ANNA, True, dict(ALL))
    # Off, with the choice still beside it and ANNA signed in: the one case where the sieve would let "on" through.
    ledger.set_sending(book, ORIGIN, ANNA, False, dict(ALL))
    before = json.loads(json.dumps(book))
    key = walk_key("20261119-213000", 1795210200)
    ledger.note_walks(book, ORIGIN, ANNA, {key: dict(THERE)})
    assert account_of(book)["walks"] == {key: THERE}
    account_of(book)["walks"] = {}
    assert book == before and account_of(book)["sending"] is False and account_of(book)["chosen"] == ALL


def test_what_does_not_fit_in_a_name_is_dropped(tmp_path):
    def read(change) -> dict:
        raw = json.loads(raw_account(sending=False))
        change(raw["addresses"], raw["addresses"][ORIGIN], raw["addresses"][ORIGIN]["accounts"][ANNA])
        (tmp_path / FILE_NAME).write_text(json.dumps(raw), "utf-8")
        return Ledger(tmp_path).read()

    assert account_of(read(lambda addresses, place, account: None))["email"] == "a@b.example"  # the canary
    # An e-mail address is shown to say WHICH Walka account: nothing in it may be unseen or turn the line round.
    for email in ("a@b.example\u202egro.live", "a\u200b@b.example", "a@b.example\n", "a" * 250 + "@b.ex"):
        assert (
            account_of(read(lambda addresses, place, account, email=email: account.update(email=email)))["email"] == ""
        )
    assert account_of(read(lambda addresses, place, account: account.update(email="a" * 248 + "@b.ex")))["email"] != ""
    assert account_of(read(lambda addresses, place, account: account.update(way="g" * 33)))["way"] == ""
    assert account_of(read(lambda addresses, place, account: account.update(way="g\tb")))["way"] == ""
    # A notice is for when nobody is signed in.
    assert read(lambda addresses, place, account: place.update(notice="ended"))["addresses"][ORIGIN]["notice"] is None
    # The row of a walk that was there already.
    for row in ("a\nb", "r" * 65, ""):
        walks = {"w|1.0": {"state": "there", "row": row, "deleted": False}}
        after = read(lambda addresses, place, account, walks=walks: account.update(walks=walks))
        assert account_of(after)["walks"] == {"w|1.0": THERE}
    # An address that is no address, and an id that is none.
    for name in ("https://accounts.example\n", "h" * 301):
        after = read(
            lambda addresses, place, account, name=name: addresses.update({name: json.loads(json.dumps(place))})
        )
        assert set(after["addresses"]) == {ORIGIN}
    after = read(lambda addresses, place, account: place["accounts"].update({"u" * 201: dict(account), "b\x00": {}}))
    assert set(after["addresses"][ORIGIN]["accounts"]) == {ANNA}


def test_a_file_that_is_not_json_in_one_place_is_damaged_as_a_whole(tmp_path):
    good = raw_account()
    (tmp_path / FILE_NAME).write_bytes(good)
    assert ledger.signed_in(Ledger(tmp_path).read(), ORIGIN) is not None  # the canary
    # No Walka writes NaN, and the website's reader refuses a file with it whole: so does this one.
    for nan in (b"NaN", b"Infinity", b"-Infinity"):
        (tmp_path / FILE_NAME).write_bytes(
            good.replace(b'"walks": {}', b'"walks": {"w|1.0": {"state": "added", "at": ' + nan + b"}}")
        )
        assert Ledger(tmp_path).read() == EMPTY
    # A version this Walka never wrote, with a book inside: not believed either.
    for version in (b"0", b"-1", b"1.0", b'"1"', b"null", b"true"):
        raw = good.replace(b'"version": 1,', b'"version": ' + version + b",")
        assert raw != good
        (tmp_path / FILE_NAME).write_bytes(raw)
        assert Ledger(tmp_path).read() == EMPTY


# ---------------------------------------------------------------- the other changes


def test_signing_out_keeps_the_walks_and_may_leave_a_notice(tmp_path):
    book = signed_in_and_on(tmp_path)
    key = walk_key("20261119-213000", 1795210200)
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: dict(THERE)}))
    book.change(lambda b: ledger.sign_out(b, ORIGIN, notice="ended"))
    after = book.read()
    assert after["addresses"][ORIGIN]["signed_in"] is None and after["addresses"][ORIGIN]["notice"] == "ended"
    assert account_of(after)["sending"] is False and key in account_of(after)["walks"]
    assert account_of(after)["email"] == "a@b.example"

    book.change(lambda b: ledger.clear_notice(b, ORIGIN))
    assert book.read()["addresses"][ORIGIN]["notice"] is None
    book.change(lambda b: ledger.sign_out(b, ORIGIN, notice="ended"))
    book.change(lambda b: ledger.sign_in(b, ORIGIN, ANNA, "github", "a@b.example"))
    assert book.read()["addresses"][ORIGIN]["notice"] is None  # a sign-in clears it
    book.change(lambda b: ledger.sign_out(b, ORIGIN))
    assert book.read()["addresses"][ORIGIN]["notice"] is None
    with pytest.raises(ValueError):
        book.change(lambda b: ledger.sign_out(b, ORIGIN, notice="anything"))


def test_signing_out_where_nobody_ever_signed_in_leaves_no_line(tmp_path):
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_out(b, ORIGIN))
    book.change(lambda b: ledger.clear_notice(b, ORIGIN))
    assert book.read() == EMPTY and names(tmp_path) == [LOCK_NAME]


def test_each_address_has_a_part_of_its_own(tmp_path):
    book = signed_in_and_on(tmp_path)
    local = "http://127.0.0.1:54321"
    book.change(lambda b: ledger.sign_in(b, local, BEN, "google", "ben@b.example"))
    after = book.read()
    assert ledger.signed_in(after, ORIGIN)[0] == ANNA and ledger.signed_in(after, local)[0] == BEN
    assert account_of(after)["sending"] is True
    assert ledger.signed_in(after, "https://nobody.example") is None
    assert "https://nobody.example" not in after["addresses"]  # asking makes no line


def test_an_address_may_be_handed_in_as_the_thing_that_has_its_text(tmp_path):
    origin = types.SimpleNamespace(scheme="https", host="accounts.example", port=None, text=ORIGIN)
    book = Ledger(tmp_path, sleep=Naps())
    book.change(lambda b: ledger.sign_in(b, origin, ANNA, "github", "a@b.example"))
    assert ledger.signed_in(book.read(), ORIGIN)[0] == ANNA
    assert ledger.signed_in(book.read(), origin)[0] == ANNA
    for bad in (None, "", 5, types.SimpleNamespace(text=None)):
        with pytest.raises(ValueError):
            ledger.address(ledger.empty_book(), bad)


def test_noting_walks(tmp_path):
    book = signed_in_and_on(tmp_path)
    key = walk_key("20261119-213000", 1795210200)
    for bad in (
        {"state": "sent"},
        {"state": "added", "row": ROW},
        {"state": "added", "row": 5, "at": 1.0},
        "added",
        None,
    ):
        with pytest.raises(ValueError):
            book.change(lambda b, bad=bad: ledger.note_walks(b, ORIGIN, ANNA, {key: bad}))
    with pytest.raises(ValueError):
        book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {"": dict(THERE)}))
    with pytest.raises(ValueError):
        book.change(lambda b: ledger.note_walks(b, ORIGIN, "", {key: dict(THERE)}))
    assert account_of(book.read())["walks"] == {}

    def walk():
        return account_of(book.read())["walks"][key]

    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: dict(THERE)}))
    assert walk() == THERE
    # What the Walka account says of it afterwards (its row, or that it was deleted there) replaces that.
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: {"state": "there", "row": ROW, "deleted": True}}))
    assert walk() == {"state": "there", "row": ROW, "deleted": True}
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: {"state": "added", "row": ROW, "at": 3}}))
    assert walk() == {"state": "added", "row": ROW, "at": 3.0}
    # The other Walka was told "there already" for the walk this one added: the row's id is kept.
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: dict(THERE)}))
    assert walk() == {"state": "added", "row": ROW, "at": 3.0}
    book.change(lambda b: ledger.note_walks(b, ORIGIN, ANNA, {key: {"state": "there", "row": ROW, "deleted": True}}))
    assert walk()["deleted"] is True

    # For a Walka account the file has lost: written down, and nothing switched on by it.
    book.change(lambda b: ledger.note_walks(b, ORIGIN, BEN, {key: dict(THERE)}))
    assert account_of(book.read(), BEN) == {
        "way": "",
        "email": "",
        "sending": False,
        "chosen": None,
        "walks": {key: THERE},
    }
    assert ledger.signed_in(book.read(), ORIGIN)[0] == ANNA


def test_a_bad_sign_in_changes_nothing(tmp_path):
    book = signed_in_and_on(tmp_path)
    before = (tmp_path / FILE_NAME).read_bytes()
    for user, way, email in (
        ("", "github", "a@b.example"),
        (None, "github", ""),
        (BEN, None, ""),
        (BEN, "github", None),
    ):
        with pytest.raises(ValueError):
            book.change(lambda b, user=user, way=way, email=email: ledger.sign_in(b, ORIGIN, user, way, email))
    assert (tmp_path / FILE_NAME).read_bytes() == before
    # No e-mail address in the answer is a sign-in all the same (the card then says why nothing can be sent).
    book.change(lambda b: ledger.sign_in(b, ORIGIN, BEN, "discord", ""))
    assert ledger.signed_in(book.read(), ORIGIN) == (BEN, account_of(book.read(), BEN))
    assert account_of(book.read(), BEN)["email"] == ""


# ---------------------------------------------------------------- what the module is made of


def test_the_module_asks_nothing_of_the_network_reads_no_clock_and_writes_no_log():
    source = Path(ledger.__file__).read_text("utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {
        "__future__", "collections", "contextlib", "fcntl", "itertools", "json", "math", "msvcrt", "os", "pathlib",
        "sys", "time", "typing",
    }  # fmt: skip
    # Of ``time`` only the wait, and that only as what a caller may replace.
    assert re.findall(r"\btime\.\w+", source) == ["time.sleep"]
    assert "sleep: Callable[[float], None] = time.sleep" in source
    assert "—" not in source and "–" not in source  # the house writes " - "
