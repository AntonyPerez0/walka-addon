"""The addon's saved file (game_file.py): what it holds, how it is found and
read, and which game session a walk was in."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from walka import addon_sync, game_file
from walka.game_file import GameFiles, GameSession, account_files, decode, file_walk
from walka.journey import djb2

T = 1_800_000_000.0


def session(start, end, number=1, character="Tester-Azeroth", **more) -> dict:
    seconds = None if end is None else end - start
    return {"id": number, "start": start, "end": end, "seconds": seconds, "character": character, **more}


# ---- what the file holds -------------------------------------------------------------------------


def test_sessions_and_the_running_one_are_read():
    saved = decode(
        {
            "sessions": [session(T, T + 600, 3), session(T - 7200, T - 3600, 2, "Other-Realm")],
            "active": {"savedAt": T + 4000, "session": session(T + 1000, None, 4)},
            "settings": {"units": "mph"},
        }
    )
    assert saved is not None
    assert [(s.start, s.end, s.number, s.character, s.running) for s in saved.sessions] == [
        (T - 7200, T - 3600, 2, "Other", False),
        (T, T + 600, 3, "Tester", False),
        (T + 1000, T + 4000, 4, "Tester", True),
    ]
    assert saved.sessions[1].key == djb2("Tester-Azeroth")
    assert saved.journeys == ()


@pytest.mark.parametrize(
    "record",
    [
        None,
        "x",
        {"start": T},
        {"start": T, "end": T},  # no length
        {"start": T, "end": T - 5},  # ends before it starts
        {"start": T, "end": T + 30 * 86400},  # longer than any session
        {"start": "today", "end": T},
        {"start": float("nan"), "end": T},
        {"start": 5, "end": 10},  # before there was Walka
        {"start": T, "end": float("inf")},
        {"start": True, "end": T},
    ],
)
def test_what_is_no_session_is_left_out(record):
    saved = decode({"sessions": [record, session(T, T + 60)]})
    assert len(saved.sessions) == 1


def test_a_session_whose_character_the_game_hid_belongs_to_nobody():
    (found,) = decode({"sessions": [session(T, T + 60, character="?")]}).sessions
    assert (found.character, found.key) == ("", 0)
    (found,) = decode({"sessions": [session(T, T + 60, number="x", character=5)]}).sessions
    assert (found.number, found.character) == (0, "")


@pytest.mark.parametrize("table", [None, 5, "WalkaDB", [1, 2]])
def test_what_is_not_the_addons_table_is_nothing(table):
    assert decode(table) is None


@pytest.mark.parametrize(
    "active",
    [5, {}, {"savedAt": "x", "session": session(T, None)}, {"savedAt": T + 60, "session": 7}, {"session": session(T, None)}],
)
def test_a_damaged_running_session_is_left_out(active):
    assert decode({"active": active}).sessions == ()


def test_the_journeys_are_read_with_the_files_date():
    saved = decode({"journeys": {"Tester-Azeroth": {"level": 9}, "?": {"level": 3}, "x": 5}}, saved_at=T)
    (journey,) = saved.journeys
    assert (journey.name, journey.level, journey.seen_at) == ("Tester", 9, T)


# ---- which session a walk was in -------------------------------------------------------------------


def test_a_walk_is_filed_under_the_session_it_shares_the_most_time_with():
    first = GameSession(T, T + 1000, 1, 0, "A")
    second = GameSession(T + 900, T + 3000, 2, 0, "B")
    assert file_walk(T + 100, T + 500, [first, second]) is first
    assert file_walk(T + 850, T + 1250, [first, second]) is second  # 100 s in the first, 350 in the second
    assert file_walk(T + 5000, T + 5100, [first, second]) is None


def test_half_of_the_shorter_one_is_enough_and_less_is_not():
    login = GameSession(T, T + 300, 1, 0, "A")  # five minutes in the game
    assert file_walk(T - 300, T + 300, [login]) is login  # a ten-minute walk with the whole login in it
    assert file_walk(T + 150, T + 450, [login]) is login  # half of the walk
    assert file_walk(T + 151, T + 451, [login]) is None


# ---- finding and reading the files ---------------------------------------------------------------------


class Game:
    def __init__(self, root: Path) -> None:
        self.dir = root / "World of Warcraft" / "_forever_"
        self.dir.mkdir(parents=True)
        self.now = time.time()

    def save(self, account: str, db, *, age: float = 30.0, raw: str | None = None) -> Path:
        path = self.dir / "WTF" / "Account" / account / "SavedVariables" / "Walka.lua"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else "\r\nWalkaDB = " + addon_sync.to_lua(db) + "\r\n"
        path.write_text(text, "utf-8", newline="")
        os.utime(path, (self.now - age, self.now - age))
        return path


def test_the_files_of_every_account_are_found_and_not_the_characters(tmp_path: Path):
    game = Game(tmp_path)
    game.save("ONE", {})
    game.save("TWO", {})
    character = game.dir / "WTF" / "Account" / "ONE" / "Realm" / "Tester" / "SavedVariables" / "Walka.lua"
    character.parent.mkdir(parents=True)
    character.write_text("WalkaCharDB = {}")
    assert [path.parts[-3] for path in account_files(game.dir)] == ["ONE", "TWO"]
    assert account_files(tmp_path / "nowhere") == []


def test_what_every_account_saved_is_put_together(tmp_path: Path):
    game = Game(tmp_path)
    game.save("ONE", {"sessions": [session(T, T + 60, 1)], "journeys": {"A-R": {"level": 3}}})
    game.save("TWO", {"sessions": [session(T - 600, T - 300, 9, "B-R")], "journeys": {"B-R": {"level": 4}}})
    files = GameFiles(lambda: [game.dir])
    assert files.scan() is True and files.rev == 1
    assert [s.number for s in files.sessions()] == [9, 1]
    assert sorted(j.name for j in files.journeys()) == ["A", "B"]
    status = files.status()
    assert (status["game_found"], status["files"], status["sessions"], status["characters"]) == (True, 2, 2, 2)
    assert status["saved_at"] == pytest.approx(game.now - 30, abs=1) and status["demo"] is False


def test_a_file_is_read_again_only_when_the_game_has_rewritten_it(tmp_path: Path, monkeypatch):
    game = Game(tmp_path)
    game.save("ONE", {"sessions": [session(T, T + 60, 1)]})
    files = GameFiles(lambda: [game.dir])
    assert files.scan(force=True)
    reads = []
    real = game_file.read_shared
    monkeypatch.setattr(game_file, "read_shared", lambda *args: reads.append(1) or real(*args))
    assert not files.scan(force=True) and reads == []
    game.save("ONE", {"sessions": [session(T, T + 60, 1), session(T + 100, T + 200, 2)]}, age=20)
    assert files.scan(force=True) and reads == [1] and len(files.sessions()) == 2
    # Rewritten with the same content: read, and no news.
    game.save("ONE", {"sessions": [session(T, T + 60, 1), session(T + 100, T + 200, 2)]}, age=10)
    assert not files.scan(force=True) and reads == [1, 1]


def test_a_file_written_a_moment_ago_is_left_until_it_has_settled(tmp_path: Path):
    game = Game(tmp_path)
    clock = {"mono": 0.0}
    files = GameFiles(lambda: [game.dir], mono=lambda: clock["mono"])
    game.save("ONE", {"sessions": [session(T, T + 60)]}, age=0.0)
    assert not files.scan(force=True)
    clock["mono"] += game_file.SETTLE_S + 0.5
    assert files.scan(force=True) and len(files.sessions()) == 1


def test_the_disk_is_looked_at_now_and_then_not_on_every_call(tmp_path: Path):
    game = Game(tmp_path)
    clock = {"mono": 0.0}
    asked = []
    files = GameFiles(lambda: asked.append(1) or [game.dir], mono=lambda: clock["mono"])
    files.scan()
    files.scan()
    assert len(asked) == 1
    game.save("ONE", {"sessions": [session(T, T + 60)]})
    assert not files.scan(), "not again so soon"
    clock["mono"] += game_file.RESCAN_S
    assert files.scan() and len(asked) == 1, "the game folders are looked for less often still"


@pytest.mark.parametrize(
    "raw",
    [
        "WalkaDB = { sessions = { os.execute('rm -rf /') } }",
        "WalkaDB = { sessions = { {start = 1800000000, ['end'] = 18000",  # cut short while the game wrote it
        "\x00\xff garbage",
        "WalkaDB = 5",
        "",
        "WalkaDB = { sessions = { { start = " + "9" * 5000 + " } } }",  # more digits than Python makes an int of
    ],
)
def test_a_file_that_cannot_be_read_files_nothing_and_breaks_nothing(tmp_path: Path, raw: str):
    game = Game(tmp_path)
    game.save("ONE", {}, raw=raw)
    files = GameFiles(lambda: [game.dir])
    files.scan(force=True)
    assert files.sessions() == [] and files.journeys() == []


def test_what_was_read_before_stands_when_a_file_goes_bad(tmp_path: Path):
    game = Game(tmp_path)
    game.save("ONE", {"sessions": [session(T, T + 60)]})
    files = GameFiles(lambda: [game.dir])
    files.scan(force=True)
    game.save("ONE", {}, raw="WalkaDB = { sessions = ", age=20)
    assert not files.scan(force=True) and len(files.sessions()) == 1


def test_a_file_that_is_gone_takes_its_sessions_with_it(tmp_path: Path):
    game = Game(tmp_path)
    path = game.save("ONE", {"sessions": [session(T, T + 60)]})
    files = GameFiles(lambda: [game.dir])
    files.scan(force=True)
    path.unlink()
    assert files.scan(force=True) and files.sessions() == []


def test_no_game_is_no_error(tmp_path: Path):
    def broken():
        raise OSError("no such registry key")

    for finder in (lambda: [], broken, lambda: [tmp_path / "nowhere"]):
        files = GameFiles(finder)
        assert not files.scan(force=True)
        assert files.sessions() == [] and files.status()["files"] == 0


def test_the_demo_reads_no_disk(tmp_path: Path):
    files = GameFiles(lambda: (_ for _ in ()).throw(AssertionError("looked at the disk")))
    files.use_demo(game_file.SavedGame((GameSession(T, T + 60, 1, 0, "Demo"),), ()))
    assert not files.scan(force=True)
    assert [s.character for s in files.sessions()] == ["Demo"] and files.status()["demo"] is True


def test_one_damaged_account_does_not_stop_the_others_being_read(tmp_path: Path):
    game = Game(tmp_path)
    game.save("ONE", {}, raw="WalkaDB = { sessions = { { start = " + "9" * 5000 + " } } }")
    game.save("TWO", {"sessions": [session(T, T + 60)]})
    files = GameFiles(lambda: [game.dir])
    files.scan(force=True)
    assert len(files.sessions()) == 1
