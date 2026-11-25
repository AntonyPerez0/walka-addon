"""A character's journey: read from the record the addon keeps in its saved
variables (``WalkaDB.journeys``), checked number by number, and remembered
in the journey book.

The game writes the record at logout and on /reload; ``game_file`` finds it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from walka import journey
from walka.journey import (
    MAX_CHARACTERS,
    Journey,
    JourneyBook,
    character_id,
    character_steps,
    clean_name,
    demo_journey,
    djb2,
    name_of,
)

NOW = 1_800_000_000.0
SINCE = 1_790_000_000


class Clock:
    def __init__(self) -> None:
        self.now = NOW
        self.mono_now = 100.0

    def wall(self) -> float:
        return self.now

    def mono(self) -> float:
        return self.mono_now

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.mono_now += seconds


def record_of(**changes) -> dict:
    """One record of WalkaDB.journeys as the addon saves it."""
    record = {
        "since": SINCE,
        "footYd": 82_025.4,
        "travelYd": 190_000,
        "level": 17,
        "maxLevel": 60,
        "classId": 7,
        "faction": 2,
        "played": 70_000,
        "playedHeard": True,
        "real": {"walks": 10, "seconds": 50_000, "distanceM": 70_000, "steps": 90_000, "kcal": 7_000, "estimated": False},
        "shared": {
            "savedAt": NOW - 30,
            "played": 77_100,
            "walks": 14,
            "seconds": 61_000,
            "distanceM": 85_300,
            "steps": 105_937,
            "kcal": 8_200,
            "estimated": False,
        },
    }
    for key, value in changes.items():
        if key in record["shared"] and key not in ("played",):
            record["shared"][key] = value
        else:
            record[key] = value
    return record


def one(key: str = "Thrall-Durotar", **changes) -> Journey:
    found = Journey.from_record(key, record_of(**changes), NOW)
    assert found is not None
    return found


# ---- one record ----------------------------------------------------------------------------------


def test_a_record_is_read():
    found = one()
    assert (found.key, found.id) == (djb2("Thrall-Durotar"), character_id("Thrall-Durotar"))
    assert found.name == "Thrall", "the name alone: the realm is never kept"
    assert (found.level, found.max_level, found.class_id, found.faction) == (17, 60, 7, "horde")
    assert (found.foot_yd, found.travel_yd, found.played_s) == (82_025, 190_000, 77_100)
    assert (found.real_steps, found.real_m, found.real_kcal, found.real_s, found.walks) == (105_937, 85_300, 8_200, 61_000, 14)
    assert found.since == SINCE and found.estimated is False
    assert found.seen_at == NOW - 30, "when the addon saved it"


def test_a_record_from_before_the_addon_saved_its_totals_has_the_settled_ones():
    record = record_of()
    del record["shared"]
    found = Journey.from_record("Jaina-Theramore", record, NOW - 500)
    assert found is not None and found.name == "Jaina"
    assert (found.real_steps, found.walks, found.played_s) == (90_000, 10, 70_000)
    assert found.seen_at == NOW - 500, "the file's own date"


@pytest.mark.parametrize("key", ["", "?", "?-Realm", 7, None, "-Realm"])
def test_a_record_of_nobody_is_no_journey(key):
    assert Journey.from_record(key, record_of(), NOW) is None


@pytest.mark.parametrize("record", [None, 5, "x", [1, 2]])
def test_what_is_no_record_is_no_journey(record):
    assert Journey.from_record("Thrall-Durotar", record, NOW) is None


@pytest.mark.parametrize(
    "field, attribute",
    [
        ("footYd", "foot_yd"),
        ("travelYd", "travel_yd"),
        ("steps", "real_steps"),
        ("distanceM", "real_m"),
        ("kcal", "real_kcal"),
        ("seconds", "real_s"),
        ("walks", "walks"),
    ],
)
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), 10**20, "far", True, None, {}])
def test_a_number_that_cannot_be_true_is_left_out(field: str, attribute: str, value):
    assert getattr(one(**{field: value}), attribute) is None


def test_zero_is_a_number_except_where_it_means_not_known():
    found = one(footYd=0, steps=0, walks=0, level=0, maxLevel=0, played=0)
    assert found.foot_yd == 0 and found.real_steps == 0 and found.walks == 0
    assert found.level is None and found.max_level is None, "the game had not said"
    record = record_of()
    record["shared"]["played"] = 0
    assert Journey.from_record("Thrall-Durotar", record, NOW).played_s is None


def test_a_level_above_the_cap_drops_the_cap():
    found = one(level=70, maxLevel=60)
    assert found.level == 70 and found.max_level is None


@pytest.mark.parametrize("since, kept", [(SINCE, True), (1_500_000_000, False), (NOW + 10 * 86400, False), (-5, False)])
def test_the_start_of_tracking_must_be_a_believable_date(since: int, kept: bool):
    assert (one(since=since).since == since) is kept


@pytest.mark.parametrize("faction, word", [(1, "alliance"), (2, "horde"), (0, None), (3, None), ("Horde", None)])
def test_the_faction(faction, word):
    assert one(faction=faction).faction == word


def test_estimated_is_only_ever_a_plain_true():
    assert one(estimated=True).estimated is True
    assert one(estimated=1).estimated is False and one(estimated="yes").estimated is False


def test_a_name_is_text_from_a_stranger():
    """The name comes from a file: it is kept as plain text, without what
    could disturb whoever shows it."""
    assert name_of("Ab\x07c\x1bd\x7f-Realm") == "Abcd"
    assert name_of("ab‮cd​-Realm") == "abcd"  # a direction override, a zero-width space
    assert clean_name("  Two \t\n words  ") == "Two words"
    assert clean_name("a b") == "a b"
    # Markup is not interpreted here or anywhere else: it stays the text it is.
    assert name_of("<img src=x onerror=1>-x") == "<img src=x onerror=1>"
    assert clean_name("x" * 100) == "x" * journey.NAME_MAX_CHARS
    assert name_of("Ñandú-Ragnaros") == "Ñandú"


@pytest.mark.parametrize(
    "yards, steps", [(0, 0), (1, 1), (88, 100), (1760, 2000), (82_025, 93_210), (10**10, 11_363_636_364)]
)
def test_character_steps_are_yards_over_point_eight_eight(yards: int, steps: int):
    assert character_steps(yards) == steps
    assert steps == round(yards / 0.88)


def test_djb2():
    assert djb2("A") == 5381 * 33 + 65
    assert djb2(b"A") == djb2("A")
    assert character_id("A") == "0002b5e6"


def test_what_the_dashboard_gets():
    found = one()
    data = found.to_api()
    assert data == {
        "id": f"{djb2('Thrall-Durotar'):08x}",
        "name": "Thrall",
        "level": 17,
        "max_level": 60,
        "class_id": 7,
        "faction": "horde",
        "played_s": 77_100,
        "foot_yd": 82_025,
        "travel_yd": 190_000,
        "character_steps": 93_210,
        "character_miles": 46.61,
        "steps": 105_937,
        "distance_m": 85_300,
        "kcal": 8_200,
        "seconds": 61_000,
        "walks": 14,
        "estimated": False,
        "since": SINCE,
        "seen_at": found.seen_at,
    }
    json.dumps(data)  # plain JSON
    unknown = one(footYd=-1).to_api()
    assert unknown["character_steps"] is None and unknown["character_miles"] is None


def test_the_demo_character_is_plausible():
    found = demo_journey(wall=lambda: NOW)
    assert found.name == "Maerwyn" and found.id == character_id(journey.DEMO_KEY)
    assert found.level == 23 and found.max_level == 60 and found.faction == "alliance"
    assert found.real_steps > 100_000 and found.walks == 19 and found.since < NOW


# ---- the book: every character's latest journey ------------------------------------------------------


def journey_of(name: str, seen_at: float, **changes) -> Journey:
    record = record_of(**changes)
    record["shared"]["savedAt"] = seen_at
    found = Journey.from_record(f"{name}-Realm", record, seen_at)
    assert found is not None
    return found


def test_the_book_keeps_the_latest_journey_of_each_character(tmp_path: Path):
    clock = Clock()
    book = JourneyBook(tmp_path / "journey.json", wall=clock.wall, mono=clock.mono)
    assert book.summary() == {"rev": 0, "characters": 0, "demo": False}
    assert book.to_api() == {"rev": 0, "demo": False, "current": None, "characters": [], "steps_per_mile": 2000}
    assert book.latest() is None

    assert book.update([journey_of("Thrall", NOW - 86400), journey_of("Jaina", NOW - 5)])
    assert book.update([journey_of("Thrall", NOW - 3600, steps=200_000)])
    data = book.to_api()
    assert data["rev"] == 2
    assert [(entry["name"], entry["steps"]) for entry in data["characters"]] == [("Jaina", 105_937), ("Thrall", 200_000)]
    assert data["current"] == data["characters"][0]["id"], "the one saved last"
    assert book.summary() == {"rev": 2, "characters": 2, "demo": False}
    assert book.latest() == {"name": "Jaina", "level": 17, "max_level": 60, "seen_at": NOW - 5}


def test_an_older_record_does_not_replace_a_newer_one(tmp_path: Path):
    """Two game folders (an old install) can hold the same character: the
    numbers saved last win, and the same numbers again are no change."""
    book = JourneyBook(tmp_path / "journey.json")
    newer = journey_of("Thrall", NOW, steps=200_000)
    assert book.update([newer])
    assert not book.update([journey_of("Thrall", NOW - 3600)])
    assert not book.update([newer])
    assert book.journeys() == [newer] and book.summary()["rev"] == 1


def test_the_book_survives_a_restart(tmp_path: Path):
    path = tmp_path / "data" / "journey.json"  # the folder does not exist yet
    clock = Clock()
    book = JourneyBook(path, wall=clock.wall, mono=clock.mono)
    assert book.save() is False and not path.exists()  # nothing to write
    book.add(journey_of("Thrall", NOW - 50))
    book.add(journey_of("Ñandú", NOW - 20, played=0, kcal=-1, estimated=True))
    assert book.save() is True
    assert book.save() is False  # unchanged since
    assert not list(path.parent.glob("*.tmp"))

    again = JourneyBook(path, wall=clock.wall, mono=clock.mono)
    assert again.to_api()["characters"] == book.to_api()["characters"]
    assert [found.name for found in again.journeys()] == ["Ñandú", "Thrall"]
    assert again.journeys() == book.journeys()
    assert again.save() is False  # loading is not a change


def test_the_number_of_remembered_characters_is_capped(tmp_path: Path):
    path = tmp_path / "journey.json"
    book = JourneyBook(path)
    for i in range(MAX_CHARACTERS + 8):
        book.add(journey_of(f"Alt{i}", NOW + i))
    names = [found.name for found in book.journeys()]
    assert len(names) == MAX_CHARACTERS
    assert names[0] == f"Alt{MAX_CHARACTERS + 7}" and names[-1] == "Alt8"  # the ones seen longest ago went
    # An old character who logs in again is back, at the expense of the then-oldest.
    book.add(journey_of("Alt0", NOW + 1000))
    names = [found.name for found in book.journeys()]
    assert names[0] == "Alt0" and "Alt8" not in names and len(names) == MAX_CHARACTERS

    # A file with too many entries (written by hand, or by a build with a higher cap) is cut down too.
    book.save()
    small = JourneyBook(path, limit=5)
    assert [found.name for found in small.journeys()] == [
        "Alt0",
        *(f"Alt{i}" for i in range(MAX_CHARACTERS + 7, MAX_CHARACTERS + 3, -1)),
    ]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        "{}",
        '{"version": 2, "characters": "many"}',
        '{"version": 2, "characters": [null, 7, "x", {}, {"key": 1}, {"key": "1", "seen_at": 5, "name": "A"}]}',
        '{"version": 2, "characters": [{"key": -4, "seen_at": 1700000000, "name": "A"}, {"key": 5, "seen_at": "now"}]}',
        '{"version": 2, "characters": [{"key": 5, "seen_at": true, "name": "A"}, {"key": 5, "seen_at": 1e300}]}',
        # What 0.10 wrote: the stat frames read off the screen. Not read: the game's file says it all again.
        '{"version": 1, "characters": [{"key": 5, "seen_at": 1700000000, "stats": {"FOOT_YD": 5}}]}',
    ],
)
def test_a_damaged_or_old_cache_file_is_ignored(tmp_path: Path, content: str):
    path = tmp_path / "journey.json"
    path.write_text(content, "utf-8")
    assert JourneyBook(path).journeys() == []


def test_a_cache_file_goes_through_the_same_checks_as_the_game(tmp_path: Path):
    path = tmp_path / "journey.json"
    entry = {
        "key": 77,
        "seen_at": NOW,
        "name": "Ed\x07it",
        "level": 60,
        "max_level": 300,
        "class_id": "x",
        "faction": "pirates",
        "real_steps": 10**15,  # edited by hand
        "real_m": "far",
        "walks": -3,
        "foot_yd": 1760,
        "made_up": 5,
        "estimated": 1,
    }
    path.write_text(json.dumps({"version": 2, "characters": [entry, {"key": 78}]}), "utf-8")
    (found,) = JourneyBook(path).journeys()
    assert (found.key, found.name, found.level, found.max_level, found.foot_yd) == (77, "Edit", 60, None, 1760)
    assert (found.real_steps, found.real_m, found.walks, found.played_s, found.class_id) == (None, None, None, None, 0)
    assert found.faction is None and found.estimated is False


def test_a_failed_save_is_tried_again(tmp_path: Path, monkeypatch):
    path = tmp_path / "journey.json"
    book = JourneyBook(path)
    book.add(journey_of("Thrall", NOW))

    def refuse(*_args):
        raise PermissionError("the file is locked")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        assert book.save() is False
    assert not path.exists()
    assert book.save() is True
    assert [found.name for found in JourneyBook(path).journeys()] == ["Thrall"]


def test_memory_only_forgets_and_never_writes(tmp_path: Path):
    path = tmp_path / "journey.json"
    first = JourneyBook(path)
    first.add(journey_of("Thrall", NOW))
    first.save()
    before = path.read_bytes()

    book = JourneyBook(path)
    assert len(book.journeys()) == 1
    book.use_memory_only(demo=True)
    assert book.journeys() == [] and book.summary()["demo"] is True
    book.add(journey_of("Jaina", NOW))
    assert book.save() is False
    assert path.read_bytes() == before
    assert book.to_api()["demo"] is True


def test_the_saver_writes_while_the_companion_runs(tmp_path: Path):
    path = tmp_path / "journey.json"
    book = JourneyBook(path)

    async def scenario():
        task = asyncio.create_task(book.run(every_s=0.05))
        book.add(journey_of("Thrall", NOW))
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert [found.name for found in JourneyBook(path).journeys()] == ["Thrall"]


def test_attach_keeps_the_book_in_the_data_folder(tmp_path: Path):
    book = journey.attach(tmp_path)
    assert book.path == tmp_path / journey.JOURNEY_FILE
    assert book.journeys() == []
