"""The journey numbers: put together from stat frames, checked, remembered.

``JourneyCollector`` receives ``(stat id, value)`` pairs the way the screen
reader delivers them - twice, late, or not at all - and must only ever pass
on a set of numbers that belongs to one character.
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
    GAP_MAX_S,
    LIVE_FOR_S,
    MAX_CHARACTERS,
    ROUND_MAX_S,
    Journey,
    JourneyBook,
    JourneyCollector,
    character_steps,
    clean_name,
)
from walka.protocol import (
    NAME_STATS,
    STAT_CYCLE,
    STAT_MAX_VALUE,
    Faction,
    Flags,
    Frame,
    InfoFlags,
    Stat,
    StatFrame,
    djb2,
    name_to_chunks,
    pack_char_info,
)
from walka.sources import BeaconSource

NOW = 1_800_000_000.0  # the wall clock of these tests
SINCE = int(NOW) - 20 * 86400


class Clock:
    """Wall and monotonic time, moved by hand."""

    def __init__(self) -> None:
        self.mono_now = 500.0
        self.wall_now = NOW

    def mono(self) -> float:
        return self.mono_now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, seconds: float) -> None:
        self.mono_now += seconds
        self.wall_now += seconds


def stats_of(name: str = "Thrall", realm: str = "Durotar", **changes: int) -> dict[Stat, int]:
    """One round of stat values, in the order the addon sends them."""
    values: dict[Stat, int] = {Stat.CHAR_KEY: djb2(f"{name}-{realm}")}
    values.update(zip(NAME_STATS, name_to_chunks(name), strict=True))
    values.update(
        {
            Stat.CHAR_INFO: pack_char_info(17, 60, 7, Faction.HORDE),
            Stat.PLAYED_S: 77_100,
            Stat.FOOT_YD: 82_025,
            Stat.TRAVEL_YD: 190_000,
            Stat.REAL_STEPS: 105_937,
            Stat.REAL_M: 85_300,
            Stat.REAL_KCAL: 8_200,
            Stat.REAL_S: 61_000,
            Stat.WALKS: 14,
            Stat.SINCE: SINCE,
            Stat.INFO_FLAGS: 0,
        }
    )
    for stat_name, value in changes.items():
        values[Stat[stat_name]] = value
    assert list(values) == list(STAT_CYCLE)
    return values


class Feed:
    """A collector with its clock, fed the way the addon sends: a stat every half second."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.published: list[Journey] = []
        self.collector = JourneyCollector(self.published.append, wall=self.clock.wall, mono=self.clock.mono)

    def send(self, stat: int, value: int, *, after: float = 0.5, times: int = 1) -> None:
        self.clock.advance(after)
        for _ in range(times):
            self.collector.on_stat(stat, value)

    def round(self, values: dict[Stat, int], *, skip: tuple[Stat, ...] = (), times: int = 1) -> None:
        for stat, value in values.items():
            if stat not in skip:
                self.send(stat, value, times=times)

    def key(self, values: dict[Stat, int]) -> None:
        self.send(Stat.CHAR_KEY, values[Stat.CHAR_KEY])


THRALL = stats_of()
JAINA = stats_of(
    "Jaina",
    "Theramore",
    CHAR_INFO=pack_char_info(42, 60, 8, Faction.ALLIANCE),
    PLAYED_S=300_000,
    FOOT_YD=500_000,
    TRAVEL_YD=900_000,
    REAL_STEPS=11,
    REAL_M=9,
    REAL_KCAL=1,
    REAL_S=7,
    WALKS=1,
    INFO_FLAGS=1,
)


def is_thrall(found: Journey) -> bool:
    return (found.name, found.level, found.real_steps, found.foot_yd) == ("Thrall", 17, 105_937, 82_025)


def is_jaina(found: Journey) -> bool:
    return (found.name, found.level, found.real_steps, found.foot_yd) == ("Jaina", 42, 11, 500_000)


# ---- a round ---------------------------------------------------------------------------------


def test_a_round_is_published_when_its_own_key_comes_round_again():
    feed = Feed()
    feed.round(THRALL)
    assert feed.published == []  # the round is whole, but nothing has closed it yet
    feed.key(THRALL)
    (found,) = feed.published
    assert found.key == djb2("Thrall-Durotar") and found.id == f"{found.key:08x}"
    assert (found.name, found.level, found.max_level, found.class_id, found.faction) == ("Thrall", 17, 60, 7, "horde")
    assert (found.played_s, found.foot_yd, found.travel_yd) == (77_100, 82_025, 190_000)
    assert (found.real_steps, found.real_m, found.real_kcal, found.real_s, found.walks) == (
        105_937,
        85_300,
        8_200,
        61_000,
        14,
    )
    assert found.since == SINCE and found.estimated is False
    assert found.seen_at == feed.clock.wall_now


def test_every_following_round_is_published_with_its_newer_numbers():
    feed = Feed()
    feed.round(THRALL)
    feed.round(stats_of(REAL_STEPS=105_950, PLAYED_S=77_108))
    feed.round(stats_of(REAL_STEPS=105_963, PLAYED_S=77_116))
    assert [found.real_steps for found in feed.published] == [105_937, 105_950]
    feed.key(THRALL)
    assert [found.played_s for found in feed.published] == [77_100, 77_108, 77_116]


def test_frames_seen_twice_change_nothing():
    """The screen is read faster than the strip is redrawn: most frames arrive twice."""
    feed = Feed()
    feed.round(THRALL, times=2)
    feed.send(Stat.CHAR_KEY, THRALL[Stat.CHAR_KEY], times=3)
    assert len(feed.published) == 1 and is_thrall(feed.published[0])


@pytest.mark.parametrize("missing", [stat for stat in STAT_CYCLE if stat is not Stat.CHAR_KEY])
def test_a_round_with_a_missed_frame_is_not_published(missing: Stat):
    feed = Feed()
    feed.round(THRALL, skip=(missing,))
    feed.key(THRALL)
    assert feed.published == []
    # ... and the next whole round is.
    feed.round(THRALL, skip=(Stat.CHAR_KEY,))
    feed.key(THRALL)
    assert len(feed.published) == 1 and is_thrall(feed.published[0])


def test_a_missed_closing_key_only_delays_the_numbers():
    feed = Feed()
    feed.round(THRALL)
    feed.round(stats_of(REAL_STEPS=106_000), skip=(Stat.CHAR_KEY,))  # the key between the rounds was not seen
    assert feed.published == []
    feed.key(THRALL)
    assert [found.real_steps for found in feed.published] == [106_000]


def test_stats_before_any_key_belong_to_nobody():
    """The companion starts while the game is running, in the middle of a round."""
    feed = Feed()
    feed.round(THRALL, skip=(Stat.CHAR_KEY, *NAME_STATS))
    feed.key(THRALL)
    assert feed.published == []
    feed.round(THRALL, skip=(Stat.CHAR_KEY,))
    feed.key(THRALL)
    assert len(feed.published) == 1


# ---- two characters are never mixed ----------------------------------------------------------


def test_another_character_starts_afresh():
    feed = Feed()
    feed.round(THRALL)
    feed.key(THRALL)
    feed.round(THRALL, skip=(Stat.CHAR_KEY,))
    feed.round(JAINA)
    # Thrall's second round was whole, but it was not his own key that ended
    # it: it stays unpublished (his numbers from eight seconds earlier stand).
    assert len(feed.published) == 1 and is_thrall(feed.published[0])
    feed.key(JAINA)
    assert len(feed.published) == 2 and is_jaina(feed.published[1])
    assert feed.published[1].estimated is True and feed.published[1].faction == "alliance"


def test_a_switch_in_the_middle_of_a_round_publishes_neither_half():
    feed = Feed()
    half = list(THRALL.items())[:8]
    for stat, value in half:
        feed.send(stat, value)
    feed.round(JAINA)  # opens with Jaina's key: Thrall's half round is dropped
    feed.key(JAINA)
    assert len(feed.published) == 1 and is_jaina(feed.published[0])


def test_a_switch_whose_first_key_was_missed_is_not_filed_under_the_character_before():
    """The dangerous one: Thrall's round is open, Jaina logs in, and the frame
    that says so is the one the capture missed. Jaina's numbers arrive under
    Thrall's open key - and complete his round."""
    feed = Feed()
    for stat, value in list(THRALL.items())[:3]:
        feed.send(stat, value)
    feed.round(JAINA, skip=(Stat.CHAR_KEY,))  # no gap at all between the two
    feed.key(JAINA)  # the first key that is seen: it does not match the round it closes
    assert feed.published == []
    feed.round(JAINA, skip=(Stat.CHAR_KEY,))
    feed.key(JAINA)
    assert len(feed.published) == 1 and is_jaina(feed.published[0])


def test_after_a_silence_the_open_round_is_given_up():
    """Logging out shows a loading screen: the strip is gone for a while. Even
    if the same key then closes the round, what came after the silence may be
    somebody else's (the other character's key could have been missed twice)."""
    feed = Feed()
    half = list(THRALL.items())
    for stat, value in half[:6]:
        feed.send(stat, value)
    feed.send(*list(JAINA.items())[6], after=GAP_MAX_S + 0.5)
    for stat, value in list(JAINA.items())[7:]:
        feed.send(stat, value)
    feed.key(THRALL)
    assert feed.published == []


def test_a_gap_just_inside_the_limit_is_tolerated():
    feed = Feed()
    for stat, value in THRALL.items():
        feed.send(stat, value, after=1.5)  # a slow game: a redraw every 0.3 s
    feed.send(Stat.CHAR_KEY, THRALL[Stat.CHAR_KEY], after=GAP_MAX_S - 0.1)
    assert len(feed.published) == 1


def test_a_frozen_picture_counts_as_silence():
    """The picture freezes on one stat frame (the same frame is decoded over
    and over), and when it moves again another character is in the game."""
    feed = Feed()
    items = list(THRALL.items())
    for stat, value in items[:5]:
        feed.send(stat, value)
    for _ in range(100):
        feed.send(*items[4], after=0.1)  # ten seconds of the same frame
    for stat, value in list(JAINA.items())[5:]:
        feed.send(stat, value)
    feed.key(THRALL)
    assert feed.published == []


def test_a_round_that_drags_on_is_not_trusted():
    feed = Feed()
    spacing = (ROUND_MAX_S + 1.0) / len(STAT_CYCLE)
    assert spacing < GAP_MAX_S  # no single silence: only the round as a whole is too long
    for stat, value in THRALL.items():
        feed.send(stat, value, after=spacing)
    feed.send(Stat.CHAR_KEY, THRALL[Stat.CHAR_KEY], after=spacing)
    assert feed.published == []


def test_interleaved_with_live_frames_through_the_beacon_source():
    """The real path: live frames and stat frames share the strip."""
    clock = Clock()
    source = BeaconSource(None, clock=clock.mono)
    published: list[Journey] = []
    source.on_stat = JourneyCollector(published.append, wall=clock.wall, mono=clock.mono).on_stat
    seq = 0
    queue = [*THRALL.items(), *THRALL.items(), (Stat.CHAR_KEY, THRALL[Stat.CHAR_KEY])]
    while queue:
        for _ in range(4):
            seq += 1
            clock.advance(0.1)
            source.inject(Frame(seq=seq % 256, session=1, flags=Flags.ACTIVE | Flags.MOVING, target_raw=300))
        seq += 1
        clock.advance(0.1)
        stat, value = queue.pop(0)
        source.inject(StatFrame(seq=seq % 256, stat_id=stat, value=value))
        source.inject(StatFrame(seq=seq % 256, stat_id=stat, value=value))  # captured twice
    assert len(published) == 2 and all(is_thrall(found) for found in published)


# ---- what arrives is checked --------------------------------------------------------------------


def one(**changes: int) -> Journey:
    feed = Feed()
    values = stats_of(**changes)
    feed.round(values)
    feed.key(values)
    (found,) = feed.published
    return found


@pytest.mark.parametrize(
    "stat, field, limit",
    [
        ("PLAYED_S", "played_s", journey.MAX_PLAYED_S),
        ("FOOT_YD", "foot_yd", journey.MAX_YARDS),
        ("TRAVEL_YD", "travel_yd", journey.MAX_YARDS),
        ("REAL_STEPS", "real_steps", journey.MAX_REAL_STEPS),
        ("REAL_M", "real_m", journey.MAX_REAL_M),
        ("REAL_KCAL", "real_kcal", journey.MAX_REAL_KCAL),
        ("REAL_S", "real_s", journey.MAX_REAL_S),
        ("WALKS", "walks", journey.MAX_WALKS),
    ],
)
def test_a_number_that_cannot_be_true_is_left_out(stat: str, field: str, limit: int):
    assert getattr(one(**{stat: limit}), field) == limit
    for absurd in (limit + 1, STAT_MAX_VALUE):  # the addon clamps what overflows to the largest value
        found = one(**{stat: absurd})
        assert getattr(found, field) is None
        # ... and only that one: the rest of the round is fine.
        assert found.name == "Thrall" and found.level == 17
        others = {"real_steps", "foot_yd", "walks"} - {field}
        assert all(getattr(found, other) is not None for other in others)


def test_zero_is_a_number_except_where_it_means_not_known():
    found = one(PLAYED_S=0, FOOT_YD=0, REAL_STEPS=0, REAL_KCAL=0, WALKS=0, SINCE=0)
    assert found.played_s is None and found.since is None
    assert (found.foot_yd, found.real_steps, found.real_kcal, found.walks) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "since, kept",
    [
        (SINCE, True),
        (journey.SINCE_MIN, True),
        (journey.SINCE_MIN - 1, False),  # before there was anything to track
        (int(NOW) + 86400, True),  # a clock that was set back since
        (int(NOW) + 30 * 86400, False),
        (STAT_MAX_VALUE, False),
    ],
)
def test_the_start_of_tracking_must_be_a_believable_date(since: int, kept: bool):
    assert (one(SINCE=since).since == since) is kept


@pytest.mark.parametrize(
    "info, level, cap, faction",
    [
        (pack_char_info(1, 60, 1, Faction.ALLIANCE), 1, 60, "alliance"),
        (pack_char_info(60, 60, 11, Faction.HORDE), 60, 60, "horde"),
        (pack_char_info(0, 60, 1, Faction.UNKNOWN), None, 60, None),  # the game has not said yet
        (pack_char_info(17, 0, 1, Faction.UNKNOWN), 17, None, None),
        (pack_char_info(70, 60, 1, 9), 70, None, None),  # a level above the cap, a faction that does not exist
        (2**32 + 17, None, None, None),  # more than the four bytes it is made of
        (STAT_MAX_VALUE, None, None, None),
    ],
)
def test_character_info_is_taken_apart_carefully(info: int, level, cap, faction):
    found = one(CHAR_INFO=info)
    assert (found.level, found.max_level, found.faction) == (level, cap, faction)


def test_only_the_flags_this_build_knows_are_read():
    assert one(INFO_FLAGS=InfoFlags.ESTIMATED).estimated is True
    assert one(INFO_FLAGS=0b1110).estimated is False
    assert one(INFO_FLAGS=STAT_MAX_VALUE).estimated is True


@pytest.mark.parametrize("value", [-1, STAT_MAX_VALUE + 1, 2.5, "7", None, True])
def test_values_that_are_not_stat_values_are_ignored(value):
    feed = Feed()
    feed.round(THRALL, skip=(Stat.WALKS,))
    feed.clock.advance(0.5)
    feed.collector.on_stat(Stat.WALKS, value)
    feed.key(THRALL)
    assert feed.published == []  # WALKS was never really seen


@pytest.mark.parametrize("stat_id", [0, 13, 15, 20, 63, 64, -1])
def test_stat_ids_this_build_does_not_know_are_ignored(stat_id: int):
    feed = Feed()
    feed.round(THRALL)
    feed.send(stat_id, 123)  # a newer addon may send more
    feed.key(THRALL)
    assert len(feed.published) == 1 and is_thrall(feed.published[0])


def test_a_key_that_is_no_hash_opens_no_round():
    feed = Feed()
    bad = dict(THRALL)
    bad[Stat.CHAR_KEY] = 2**32
    feed.round(bad)
    feed.key(bad)
    assert feed.published == []


# ---- names -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Thrall", "A", "Ñandú", "Þórr", "Абвгдежзик", "阿尔萨斯", "아서스왕자님"])
def test_names_arrive_as_they_were_sent(name: str):
    feed = Feed()
    values = stats_of(name)
    feed.round(values)
    feed.key(values)
    assert feed.published[0].name == name


def name_from_bytes(raw: bytes) -> str:
    padded = raw.ljust(20, b"\0")
    chunks = {stat: int.from_bytes(padded[i * 5 : i * 5 + 5], "big") for i, stat in enumerate(NAME_STATS)}
    return one(**{stat.name: value for stat, value in chunks.items()}).name


def test_bytes_that_are_not_text_do_not_break_anything():
    assert name_from_bytes(b"Thr\xff\xfell") == "Thr\ufffd\ufffdll"
    assert name_from_bytes(b"\xe9\x98") == "\ufffd"  # a character cut in half
    assert name_from_bytes(b"") == ""


def test_a_name_is_text_from_a_stranger():
    """Anything on screen that looks like the strip is decoded, so the name
    could be anything. It is kept as plain text, without what could disturb
    whoever shows it."""
    assert name_from_bytes(b"Ab\x07c\x1bd\x7f") == "Abcd"
    assert name_from_bytes("ab\u202ecd\u200b".encode()) == "abcd"  # a direction override, a zero-width space
    assert name_from_bytes(b"  Two \t\n words  ") == "Two words"
    assert name_from_bytes("a\u2028b".encode()) == "a b"
    # Markup is not interpreted here or anywhere else: it stays the text it is.
    assert name_from_bytes(b"<img src=x onerror=1>") == "<img src=x onerror=1>"[:20]
    assert clean_name("x" * 100) == "x" * journey.NAME_MAX_CHARS
    assert clean_name("") == ""


# ---- the convention behind "character steps" -------------------------------------------------------


@pytest.mark.parametrize(
    "yards, steps", [(0, 0), (1, 1), (88, 100), (1760, 2000), (82_025, 93_210), (10**10, 11_363_636_364)]
)
def test_character_steps_are_yards_over_point_eight_eight(yards: int, steps: int):
    assert character_steps(yards) == steps
    assert steps == round(yards / 0.88)


def test_what_the_dashboard_gets():
    found = one()
    data = found.to_api(found.seen_at + 3)
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
        "live": True,
    }
    json.dumps(data)  # plain JSON
    assert found.to_api(found.seen_at + LIVE_FOR_S + 1)["live"] is False
    unknown = one(FOOT_YD=STAT_MAX_VALUE).to_api(NOW)
    assert unknown["character_steps"] is None and unknown["character_miles"] is None


# ---- the book: every character's latest journey ------------------------------------------------------


def journey_of(name: str, seen_at: float, **changes: int) -> Journey:
    values = stats_of(name, **changes)
    key = values.pop(Stat.CHAR_KEY)
    return Journey.from_stats(key, values, seen_at)


def test_the_book_keeps_the_latest_journey_of_each_character(tmp_path: Path):
    clock = Clock()
    book = JourneyBook(tmp_path / "journey.json", wall=clock.wall, mono=clock.mono)
    assert book.summary() == {"rev": 0, "characters": 0, "demo": False}
    assert book.to_api() == {"rev": 0, "demo": False, "current": None, "characters": [], "steps_per_mile": 2000}

    book.add(journey_of("Thrall", NOW - 86400))
    book.add(journey_of("Jaina", NOW - 5))
    book.add(journey_of("Thrall", NOW - 3600, REAL_STEPS=200_000))
    data = book.to_api()
    assert data["rev"] == 3
    assert [(entry["name"], entry["steps"], entry["live"]) for entry in data["characters"]] == [
        ("Jaina", 105_937, True),
        ("Thrall", 200_000, False),
    ]
    assert data["current"] == data["characters"][0]["id"]
    assert book.summary() == {"rev": 3, "characters": 2, "demo": False}

    clock.advance(LIVE_FOR_S + 10)
    assert book.to_api()["current"] is None  # nobody is in the game any more


def test_the_book_says_which_character_is_in_the_game_now(tmp_path: Path):
    """For the dashboard's "Playing" line: the character whose numbers are
    arriving, by name and level - nobody, once they stop."""
    clock = Clock()
    book = JourneyBook(tmp_path / "journey.json", wall=clock.wall, mono=clock.mono)
    assert book.playing() is None
    book.add(journey_of("Thrall", NOW - 86400))
    assert book.playing() is None, "seen yesterday is not playing now"
    book.add(journey_of("Jaina", NOW - 5))
    assert book.playing() == {"name": "Jaina", "level": 17, "max_level": 60}
    clock.advance(LIVE_FOR_S + 10)
    assert book.playing() is None


def test_the_collector_of_a_book_fills_it(tmp_path: Path):
    clock = Clock()
    book = JourneyBook(tmp_path / "journey.json", wall=clock.wall, mono=clock.mono)
    for stat, value in [*THRALL.items(), (Stat.CHAR_KEY, THRALL[Stat.CHAR_KEY])]:
        clock.advance(0.5)
        book.collector.on_stat(stat, value)
    assert [entry["name"] for entry in book.to_api()["characters"]] == ["Thrall"]


def test_the_book_survives_a_restart(tmp_path: Path):
    path = tmp_path / "data" / "journey.json"  # the folder does not exist yet
    clock = Clock()
    book = JourneyBook(path, wall=clock.wall, mono=clock.mono)
    assert book.save() is False and not path.exists()  # nothing to write
    book.add(journey_of("Thrall", NOW - 50))
    book.add(journey_of("Ñandú", NOW - 20, PLAYED_S=0, REAL_KCAL=STAT_MAX_VALUE, INFO_FLAGS=1))
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
        '{"version": 1, "characters": "many"}',
        '{"version": 1, "characters": [null, 7, "x", {}, {"key": 1}, {"key": "1", "seen_at": 5, "stats": {}}]}',
        '{"characters": [{"key": -4, "seen_at": 1700000000, "stats": {}}, {"key": 5, "seen_at": "now", "stats": {}}]}',
        '{"characters": [{"key": 5, "seen_at": true, "stats": {}}, {"key": 5, "seen_at": 1e300, "stats": {}}]}',
    ],
)
def test_a_damaged_cache_file_is_ignored(tmp_path: Path, content: str):
    path = tmp_path / "journey.json"
    path.write_text(content, "utf-8")
    assert JourneyBook(path).journeys() == []


def test_a_cache_file_goes_through_the_same_checks_as_the_game(tmp_path: Path):
    path = tmp_path / "journey.json"
    entry = {
        "key": 77,
        "seen_at": NOW,
        "stats": {
            "CHAR_KEY": 1,  # not a number of the round: the entry's own key counts
            "NAME_0": int.from_bytes(b"Ed\x07it", "big"),
            "CHAR_INFO": pack_char_info(60, 60, 4, Faction.HORDE),
            "REAL_STEPS": 10**15,  # edited by hand
            "REAL_M": "far",
            "WALKS": -3,
            "FOOT_YD": 1760,
            "MADE_UP": 5,
            "INFO_FLAGS": True,
        },
    }
    path.write_text(json.dumps({"version": 1, "characters": [entry, {"key": 78}]}), "utf-8")
    (found,) = JourneyBook(path).journeys()
    assert (found.key, found.name, found.level, found.foot_yd) == (77, "Edit", 60, 1760)
    assert (found.real_steps, found.real_m, found.walks, found.played_s) == (None, None, None, None)
    assert found.estimated is False


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


def test_attach_wires_a_beacon_and_tolerates_none(tmp_path: Path):
    source = BeaconSource(None)
    book = journey.attach(tmp_path, source)
    assert source.on_stat == book.collector.on_stat
    assert book.path == tmp_path / journey.JOURNEY_FILE
    assert journey.attach(tmp_path, None).journeys() == []
