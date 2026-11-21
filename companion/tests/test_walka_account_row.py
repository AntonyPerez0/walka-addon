"""A walk's file -> a row for the Walka account (walka_account.row), tried on the website's own records.

The three records below are those of the website's tests/import.test.ts
(EVENING, MIDNIGHT, OLD), letter for letter. The rows expected for them
are the one written out in that file and the two written out in the brief
(docs/brief-walka-sends-walks.md, section 8, step 4). They were written
here before the module was. If the website's reader
(src/lib/account/import.ts) changes, these change first.

Where a comment says "the website's reader gave", its code was run on that
record on 21 November 2026 and the answer written down.
"""

from __future__ import annotations

import json
import os
import random
import re
import time

import pytest

from walka.walka_account import row as row_module
from walka.walka_account.row import ROW_KEYS, load_record, outside, start_key, walk_row, with_id

# ---- what Walka wrote --------------------------------------------------------------

# 21:30:00 on 19 November 2026 in Berlin, with a walking session in the game, counted by the treadmill
EVENING = '{"id":"20261119-213000","start":1795120200.4,"end":1795120244.6,"seconds":40.0,"distance_m":34.7,"steps":66,"kcal":2.5,"kcal_extra":1.4,"max_kmh":3.2,"avg_hr":null,"max_hr":null,"source":"wow","beacon_session":7,"steps_source":"treadmill","distance_source":"treadmill","calories_source":"speed","treadmill":"UREVO-E1 <pad>","samples":[[1.0,3.2,null],[6.0,3.2,null],[11.0,3.2,null],[16.0,3.2,null],[21.0,3.2,null],[26.0,3.2,null],[31.0,3.2,null],[36.0,3.2,null]]}'

# 23:59:40 on 14 November 2026 in Berlin: it ends on the 15th. Started by hand, with a heart rate.
MIDNIGHT = '{"id":"20261114-235940","start":1794697180,"end":1794697229.2,"seconds":45.0,"distance_m":51.2,"steps":79,"kcal":3.1,"kcal_extra":1.9,"max_kmh":4.1,"avg_hr":131.0,"max_hr":131,"source":"manual","beacon_session":null,"steps_source":"estimate","distance_source":"speed","calories_source":"speed","treadmill":"UREVO-E1 <pad>","samples":[[1.0,4.1,131],[6.0,4.1,131],[11.0,4.1,131],[16.0,4.1,131],[21.0,4.1,131],[26.0,4.1,131],[31.0,4.1,131],[36.0,4.1,131],[41.0,4.1,131]]}'

# an older record: it never knew the calories on top of sitting, and has no samples
OLD = '{"id":"20250302-081500","start":1740899700,"end":1740901500,"seconds":1700.0,"distance_m":1500.0,"steps":2100,"kcal":101.0,"kcal_extra":0.0,"max_kmh":0.0,"avg_hr":null,"max_hr":null,"source":"manual","beacon_session":null,"steps_source":"estimate","distance_source":"speed","calories_source":"speed","treadmill":"","samples":[]}'

# ---- the rows expected ---------------------------------------------------------------

# tests/import.test.ts:130-146, key for key
EVENING_ROW = {
    "origin": "walka",
    "client_id": "20261119-213000",
    "started_at": "2026-11-19T20:30:00.400Z",
    "ended_at": "2026-11-19T20:30:44.600Z",
    "local_day": "2026-11-19",
    "utc_offset_min": 60,
    "seconds": 40,
    "distance_m": 34.7,
    "steps": 66,
    "kcal": 2.5,
    "kcal_extra": 1.4,
    "session": "wow",
    "steps_source": "treadmill",
    "distance_source": "treadmill",
    "calories_source": "speed",
}
# ... and as the browser writes that row down: 40, not 40.0
EVENING_TEXT = '{"origin":"walka","client_id":"20261119-213000","started_at":"2026-11-19T20:30:00.400Z","ended_at":"2026-11-19T20:30:44.600Z","local_day":"2026-11-19","utc_offset_min":60,"seconds":40,"distance_m":34.7,"steps":66,"kcal":2.5,"kcal_extra":1.4,"session":"wow","steps_source":"treadmill","distance_source":"treadmill","calories_source":"speed"}'

# the brief, section 8, step 4
MIDNIGHT_TEXT = '{"origin":"walka","client_id":"20261114-235940","started_at":"2026-11-14T22:59:40.000Z","ended_at":"2026-11-14T23:00:29.200Z","local_day":"2026-11-14","utc_offset_min":60,"seconds":45,"distance_m":51.2,"steps":79,"kcal":3.1,"kcal_extra":1.9,"session":"manual","steps_source":"estimate","distance_source":"speed","calories_source":"speed"}'
OLD_TEXT = '{"origin":"walka","client_id":"20250302-081500","started_at":"2025-03-02T07:15:00.000Z","ended_at":"2025-03-02T07:45:00.000Z","local_day":"2025-03-02","utc_offset_min":60,"seconds":1700,"distance_m":1500,"steps":2100,"kcal":101,"kcal_extra":null,"session":"manual","steps_source":"estimate","distance_source":"speed","calories_source":"speed"}'

# The website's sixteen (src/lib/account/walks.ts): the fifteen of the reader, and the id in front.
SIXTEEN = (
    "id",
    "origin",
    "client_id",
    "started_at",
    "ended_at",
    "local_day",
    "utc_offset_min",
    "seconds",
    "distance_m",
    "steps",
    "kcal",
    "kcal_extra",
    "session",
    "steps_source",
    "distance_source",
    "calories_source",
)

# Every sentence import.ts outside() can say, in its order.
NOT_A_TIME = "Its start or its end is not a time."
NOT_A_DAY = "Its day is not a day."
NAME = "Its name is longer than 64 characters."
BACKWARDS = "It ends before it starts."
TWO_DAYS = "It lasts longer than two days."
BEFORE_2000 = "It starts before the year 2000."
TO_COME = "It starts in the days to come."
SECONDS = "The time walked is not a number from zero up."
SECONDS_MOST = "The time walked is more than a day."
SECONDS_LONGER = "The time walked is longer than the walk lasted."
DISTANCE = "The distance is not a number from zero up."
DISTANCE_MOST = "The distance is more than 200 km (124 miles)."
STEPS = "The steps are not a whole number from zero up."
STEPS_MOST = "There are more than 400,000 steps."
KCAL = "The calories are not a number from zero up."
KCAL_MOST = "There are more than 20,000 kcal."
ON_TOP = "The calories on top of sitting are not a number from 0 to 20,000."
ZONE = "Its time zone is not one there is."
DAY_FIT = "Its day does not fit the time it started."
REASONS = (
    NOT_A_TIME,
    NOT_A_DAY,
    NAME,
    BACKWARDS,
    TWO_DAYS,
    BEFORE_2000,
    TO_COME,
    SECONDS,
    SECONDS_MOST,
    SECONDS_LONGER,
    DISTANCE,
    DISTANCE_MOST,
    STEPS,
    STEPS_MOST,
    KCAL,
    KCAL_MOST,
    ON_TOP,
    ZONE,
    DAY_FIT,
)

# "Now" for every test: 21 November 2026, 00:00 UTC, in milliseconds. Never the computer's clock.
NOW_MS = 1_795_219_200_000
DAY_S = 86_400
EVENING_START = 1795120200.4

WRITTEN_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")


def record(text: str, **change):
    return {**json.loads(text), **change}


def without(text: str, *keys: str):
    made = json.loads(text)
    for key in keys:
        del made[key]
    return made


def written(row) -> str:
    """The row as it goes over the wire: so that 1500.0 for 1500, or 66.0 steps, is caught."""
    return json.dumps(row, separators=(",", ":"), allow_nan=False)


# ---- the three records ---------------------------------------------------------------


def test_a_walk_as_walka_writes_it_becomes_the_row_the_website_makes():
    row = walk_row(json.loads(EVENING))
    assert row == EVENING_ROW
    assert written(row) == EVENING_TEXT
    assert outside(row, NOW_MS) is None


def test_the_other_two_records_give_the_rows_written_out_in_the_brief():
    assert written(walk_row(json.loads(MIDNIGHT))) == MIDNIGHT_TEXT
    assert written(walk_row(json.loads(OLD))) == OLD_TEXT
    for text in (MIDNIGHT, OLD):
        assert outside(walk_row(json.loads(text)), NOW_MS) is None


def test_the_same_rows_come_out_of_the_files_text():
    for text, expected in ((EVENING, EVENING_TEXT), (MIDNIGHT, MIDNIGHT_TEXT), (OLD, OLD_TEXT)):
        assert written(walk_row(load_record(text))) == expected


def test_whole_numbers_are_whole_in_the_row_and_the_record_is_left_as_it_was():
    for text in (EVENING, MIDNIGHT, OLD):
        before = json.loads(text)
        row = walk_row(before)
        assert before == json.loads(text)
        assert type(row["steps"]) is int
        assert type(row["utc_offset_min"]) is int
    old = walk_row(json.loads(OLD))
    assert [type(old[key]) for key in ("seconds", "distance_m", "kcal")] == [int, int, int]
    assert old["kcal_extra"] is None


# ---- the keys --------------------------------------------------------------------------


def test_a_row_has_the_websites_keys_in_the_websites_order():
    assert ROW_KEYS == SIXTEEN
    for text in (EVENING, MIDNIGHT, OLD):
        assert tuple(walk_row(json.loads(text))) == SIXTEEN[1:]


def test_with_id_puts_the_id_in_front_and_keeps_exactly_the_sixteen():
    row = walk_row(json.loads(EVENING))
    row_id = "3f2b8a52-6c1d-4e0f-9a47-0d5c1e2b7a90"
    sent = with_id(row, row_id)
    assert tuple(sent) == SIXTEEN
    assert sent == {"id": row_id, **EVENING_ROW}
    assert written(sent) == '{"id":"' + row_id + '",' + EVENING_TEXT[1:]
    # the row itself is not changed, and nothing rides along
    assert "id" not in row
    assert tuple(with_id({**row, "treadmill": "UREVO-E1 <pad>", "device": "this-pc"}, row_id)) == SIXTEEN
    # the order is the website's, whatever order the row came in - and an id that rode along does not win
    backwards = {"id": "another", **dict(reversed(list(row.items())))}
    assert tuple(with_id(backwards, row_id)) == SIXTEEN
    assert written(with_id(backwards, row_id)) == written(sent)
    with pytest.raises(KeyError):
        with_id({key: value for key, value in row.items() if key != "session"}, row_id)


def test_start_key_is_the_start_cut_to_the_second():
    assert start_key(walk_row(json.loads(EVENING))) == "2026-11-19T20:30:00"
    assert start_key(walk_row(json.loads(MIDNIGHT))) == "2026-11-14T22:59:40"
    assert start_key(walk_row(json.loads(OLD))) == "2025-03-02T07:15:00"


def test_nothing_the_database_has_no_place_for_gets_through():
    for text in (EVENING, MIDNIGHT, OLD):
        # ... not even when the file holds more than Walka writes today
        row = walk_row(
            record(text, character="Thrall-Draenor", realm="Draenor", email="someone@example.com", device="this-pc", id_token="secret")
        )
        assert tuple(row) == SIXTEEN[1:]
        for key in ("samples", "avg_hr", "max_hr", "max_kmh", "treadmill", "beacon_session", "source", "start", "end", "device"):
            assert key not in row
        assert not re.search(r"UREVO|131|3\.2,|4\.1|Thrall|example\.com|secret|this-pc", written(row))


# ---- what the record does not say ----------------------------------------------------------


def test_kcal_extra_0_in_a_record_with_calories_means_not_known():
    old = walk_row(json.loads(OLD))
    assert old["kcal"] == 101
    assert old["kcal_extra"] is None
    # a record older still does not have the field at all
    assert walk_row(without(OLD, "kcal_extra"))["kcal_extra"] is None
    assert walk_row(record(OLD, kcal_extra=None))["kcal_extra"] is None
    assert walk_row(record(OLD, kcal_extra=-0.0))["kcal_extra"] is None
    # no calories at all, and none on top: that is a nought, not a gap
    nought = walk_row(record(OLD, kcal=0, kcal_extra=0))
    assert nought["kcal_extra"] == 0 and type(nought["kcal_extra"]) is int
    assert walk_row(json.loads(MIDNIGHT))["kcal_extra"] == 1.9
    # a little on top that rounds to nothing is still a number (the website's reader gave 0)
    assert written(walk_row(record(EVENING, kcal_extra=0.04))).count('"kcal_extra":0,') == 1
    # and what is not a number is not a record
    assert walk_row(record(EVENING, kcal_extra="none")) is None
    assert walk_row(record(EVENING, kcal_extra=False)) is None


def test_a_record_without_its_optional_keys_is_still_a_walk():
    bare = walk_row(
        without(
            EVENING,
            "kcal_extra",
            "max_kmh",
            "avg_hr",
            "max_hr",
            "source",
            "beacon_session",
            "steps_source",
            "distance_source",
            "calories_source",
            "treadmill",
            "samples",
        )
    )
    assert bare == {
        "origin": "walka",
        "client_id": "20261119-213000",
        "started_at": "2026-11-19T20:30:00.400Z",
        "ended_at": "2026-11-19T20:30:44.600Z",
        "local_day": "2026-11-19",
        "utc_offset_min": 60,
        "seconds": 40,
        "distance_m": 34.7,
        "steps": 66,
        "kcal": 2.5,
        "kcal_extra": None,
        "session": None,
        "steps_source": None,
        "distance_source": None,
        "calories_source": None,
    }
    assert tuple(bare) == SIXTEEN[1:]
    assert outside(bare, NOW_MS) is None
    # a key it cannot do without
    for key in ("id", "start", "end", "seconds", "distance_m", "steps", "kcal"):
        assert walk_row(without(EVENING, key)) is None, key


def test_a_word_the_database_does_not_know_is_sent_as_empty_and_the_walk_is_kept():
    later = walk_row(record(EVENING, source="android", steps_source="watch", calories_source=7))
    assert [later["session"], later["steps_source"], later["distance_source"], later["calories_source"]] == [
        None,
        None,
        "treadmill",
        None,
    ]
    assert outside(later, NOW_MS) is None
    assert walk_row(record(EVENING, source="free"))["session"] == "free"
    assert walk_row(record(EVENING, calories_source="heart_rate"))["calories_source"] == "heart_rate"
    assert walk_row(json.loads(MIDNIGHT))["session"] == "manual"
    for odd in ("WOW", " wow", "", 7, ["wow"], {"wow": 1}, True, None):
        assert walk_row(record(EVENING, source=odd))["session"] is None, odd
    # a word the database knows, but for another column
    crossed = walk_row(record(EVENING, source="treadmill", steps_source="speed", distance_source="estimate", calories_source="treadmill"))
    assert [crossed["session"], crossed["steps_source"], crossed["distance_source"], crossed["calories_source"]] == [None] * 4
    assert outside(crossed, NOW_MS) is None


# ---- when, and on which day ------------------------------------------------------------------


def test_a_walk_across_midnight_counts_for_the_day_it_started_on_wherever_the_file_is_read():
    before = os.environ.get("TZ")
    try:
        for zone in ("Europe/Berlin", "America/Los_Angeles", "Pacific/Auckland", "UTC"):
            os.environ["TZ"] = zone
            if hasattr(time, "tzset"):  # (not on Windows: there the one zone it runs in has to do)
                time.tzset()
            row = walk_row(json.loads(MIDNIGHT))
            assert row["local_day"] == "2026-11-14", zone
            assert row["started_at"] == "2026-11-14T22:59:40.000Z"
            assert row["ended_at"] == "2026-11-14T23:00:29.200Z"
            assert row["utc_offset_min"] == 60
            assert outside(row, NOW_MS) is None
    finally:
        if before is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = before
        if hasattr(time, "tzset"):
            time.tzset()
    # In Tokyo it is already the 20th at 05:30 when the EVENING walk starts: the day is the id's, not UTC's.
    tokyo = walk_row(record(EVENING, id="20261120-053000"))
    assert [tokyo["local_day"], tokyo["started_at"], tokyo["utc_offset_min"]] == ["2026-11-20", "2026-11-19T20:30:00.400Z", 540]
    assert outside(tokyo, NOW_MS) is None
    # and in Honolulu it is still the 14th, at 12:59:40, when the MIDNIGHT walk starts on the 14th at 22:59:40 UTC
    assert walk_row(record(MIDNIGHT, id="20261114-125940"))["utc_offset_min"] == -600
    # a clock far from UTC, and one that is not on the hour
    assert walk_row(record(MIDNIGHT, id="20261115-115940"))["utc_offset_min"] == 13 * 60
    assert walk_row(record(MIDNIGHT, id="20261115-042940"))["utc_offset_min"] == 330
    # an id that does not agree with the start to the second says nothing about the clock
    assert walk_row(record(MIDNIGHT, id="20261114-235917"))["utc_offset_min"] is None
    # The clocks there are: from 12 hours behind UTC to 14 ahead. One minute beyond says nothing either
    # (the website's reader gave these four), and the walk is still a walk.
    assert walk_row(record(MIDNIGHT, id="20261115-125940"))["utc_offset_min"] == 840
    assert walk_row(record(MIDNIGHT, id="20261114-105940"))["utc_offset_min"] == -720
    for beyond in ("20261115-130040", "20261114-105840"):
        row = walk_row(record(MIDNIGHT, id=beyond))
        assert row["utc_offset_min"] is None and row["client_id"] == beyond, beyond
        assert outside(row, NOW_MS) is None


def test_a_second_walk_in_the_same_second_keeps_its_own_name():
    second = walk_row(record(MIDNIGHT, id="20261114-235940-2"))
    assert second["client_id"] == "20261114-235940-2"
    assert second["local_day"] == "2026-11-14" and second["utc_offset_min"] == 60
    assert walk_row(record(EVENING, id="20261119-213000-123456789"))["client_id"] == "20261119-213000-123456789"
    assert walk_row(record(EVENING, id="20261119-213000-1234567890")) is None
    assert walk_row(record(EVENING, id="20261119-213000-")) is None


def test_a_start_that_rounds_into_the_next_second():
    # The id is made from the start before it is rounded, the file's "start" after: 21:30:00.96 is written 1795120201.0.
    for start in (1795120201.0, 1795120200.9996):
        row = walk_row(record(EVENING, start=start))
        assert row["started_at"] == "2026-11-19T20:30:01.000Z"
        assert row["utc_offset_min"] == 60  # the two still agree to the second: the offset is kept
        assert row["local_day"] == "2026-11-19"
        assert start_key(row) == "2026-11-19T20:30:01"
        assert outside(row, NOW_MS) is None
    # up to a second and a half apart the clock is believed, beyond that it is not (the website's reader gave these)
    assert walk_row(record(EVENING, start=1795120201.5))["utc_offset_min"] == 60
    assert walk_row(record(EVENING, start=1795120198.5))["utc_offset_min"] == 60
    assert walk_row(record(EVENING, start=1795120201.6))["utc_offset_min"] is None
    # On a clock that shows UTC the sum comes out at a second and a half exactly, and that is still believed:
    # 0, a whole number, not "nothing" (the website's reader gave 0 for both, and nothing for the third).
    for start in (1795123801.5, 1795123798.5):
        utc = walk_row(record(EVENING, start=start, end=1795123844.6))
        assert utc["utc_offset_min"] == 0 and type(utc["utc_offset_min"]) is int, start
        assert '"utc_offset_min":0,' in written(utc)
    assert walk_row(record(EVENING, start=1795123801.6, end=1795123844.6))["utc_offset_min"] is None
    # thousandths: halves go up
    assert walk_row(record(EVENING, start=1795120200.0005))["started_at"] == "2026-11-19T20:30:00.001Z"
    assert walk_row(record(EVENING, start=1795120200.4995))["started_at"] == "2026-11-19T20:30:00.500Z"


def test_numbers_are_rounded_as_the_browser_rounds_them():
    # (each of these is what the website's reader gave)
    for seconds, text in ((-0.0, "0"), (-0.04, "0"), (-0.05, "0"), (40.05, "40.1"), (40.15, "40.2"), (40.25, "40.3"), (0.25, "0.3"), (2.675, "2.7")):
        assert f'"seconds":{text},' in written(walk_row(record(EVENING, seconds=seconds))), seconds
    # just under a half is not a half: floor(x + 0.5) would say 0.1 here
    for kcal in (0.049999999999999996, 0.04999999999999999):
        assert '"kcal":0,' in written(walk_row(record(EVENING, kcal=kcal))), kcal


# ---- steps ----------------------------------------------------------------------------------------


def test_steps_written_with_a_point_are_sent_without_one():
    for steps in ("66.0", "6.6e1", "66"):
        row = walk_row(load_record(EVENING.replace('"steps":66,', f'"steps":{steps},')))
        assert type(row["steps"]) is int
        assert written(row) == EVENING_TEXT
    assert walk_row(record(EVENING, steps=66.0)) == EVENING_ROW
    assert written(walk_row(record(EVENING, steps=-0.0))).count('"steps":0,') == 1
    assert walk_row(record(EVENING, steps=66.5)) is None
    assert walk_row(record(EVENING, steps=None)) is None
    assert walk_row(record(EVENING, steps="66")) is None


def test_true_and_false_are_not_numbers():
    for key in ("start", "end", "seconds", "distance_m", "steps", "kcal", "kcal_extra"):
        assert walk_row(record(EVENING, **{key: True})) is None, key
        assert walk_row(record(EVENING, **{key: False})) is None, key


# ---- what is not one of Walka's walks ---------------------------------------------------------------


def test_what_is_not_one_of_walkas_walks_is_refused():
    others = [
        None,
        42,
        "20261119-213000",
        [],
        [json.loads(EVENING)],
        {},
        # another program's JSON
        {"name": "walka-website", "version": "0.1.0", "private": True},
        {"id": 17, "start": "2026-11-19T20:30:00Z", "end": "2026-11-19T21:00:00Z", "steps": 3000},
        {"type": "Run", "id": "20261119-213000", "start_date": "2026-11-19T20:30:00Z", "distance": 5000},
        # Walka's own settings file, from the same folder
        {"version": 3, "profile": {"weight_kg": 80, "height_cm": 180}, "wow": {"capture_fps": 10}},
        # a number that is text, or no number at all
        record(EVENING, distance_m="34.7"),
        record(EVENING, start="1795120200.4"),
        record(EVENING, kcal=float("nan")),
        record(EVENING, end=float("inf")),
        record(EVENING, seconds=float("-inf")),
        record(EVENING, seconds=[40.0]),
        # an id that is no time
        record(EVENING, id="walk-1"),
        record(EVENING, id="20261340-256199"),
        record(EVENING, id="2026-11-19T21:30:00"),
        record(EVENING, id=20261119),
        record(EVENING, id=None),
        record(EVENING, id="20261119-240000"),
        record(EVENING, id="20261119-213060"),
        record(EVENING, id="20250229-213000"),
        record(EVENING, id="00001119-213000"),
        # (the browser reads the years 0 to 99 as 1900 to 1999: such an id does not come back the same)
        record(EVENING, id="00991119-213000"),
        # digits that are not 0 to 9 (the same id in Arabic-Indic digits)
        record(EVENING, id="\u0662\u0660\u0662\u0666\u0661\u0661\u0661\u0669-\u0662\u0661\u0663\u0660\u0660\u0660"),
        record(EVENING, id=" 20261119-213000"),
    ]
    for other in others:
        assert walk_row(other) is None, repr(other)[:80]


def test_an_id_with_a_line_end_after_it_is_not_an_id():
    # Python's "$" lets one line end pass; the browser's does not, and such a name would be sent with it.
    for odd in ("20261119-213000\n", "20261119-213000-2\n", "20261119-213000\r\n", "20261119-213000\n\n", "\n20261119-213000"):
        assert walk_row(record(EVENING, id=odd)) is None, repr(odd)


def test_an_id_from_the_year_100_is_a_walk_the_limits_then_refuse():
    # (the website's reader gave this row and this reason)
    row = walk_row(record(EVENING, id="01001119-213000"))
    assert row["client_id"] == "01001119-213000"
    assert row["local_day"] == "0100-11-19"
    assert row["utc_offset_min"] is None
    assert outside(row, NOW_MS) == DAY_FIT


# ---- the file's text ---------------------------------------------------------------------------------


def test_load_record_reads_what_a_browser_reads():
    assert load_record(EVENING) == json.loads(EVENING)
    assert load_record("  \n" + OLD + "\n") == json.loads(OLD)
    # JSON that is no record is still JSON: the mapping says no to it
    assert load_record("[1]") == [1]
    assert walk_row(load_record("[1]")) is None
    assert walk_row(load_record('{"version":3,"profile":{"weight_kg":80}}')) is None
    # two keys of one name: the last one counts, as in the browser
    assert walk_row(load_record(EVENING[:-1] + ',"steps":67}'))["steps"] == 67


def test_load_record_refuses_what_a_browser_refuses():
    for text in ("", "   ", "walked a lot today", "{", EVENING[:-1], EVENING + "}", "{'id': 1}", "\ufeff" + EVENING):
        assert load_record(text) is None, text[:40]
    # Blank characters JSON does not know (the website cuts them off first; Walka itself does not load such a file).
    for blank in ("\u00a0", "\x0b", "\x0c", "\u2028", "\u3000", "\x00"):
        assert load_record(EVENING + blank) is None, repr(blank)
        assert load_record(blank + EVENING) is None, repr(blank)
    # Text, and nothing else: Python would guess at how bytes are written, and read UTF-16 or past a byte order mark.
    for no_text in (EVENING.encode("utf-8"), EVENING.encode("utf-16"), b"\xef\xbb\xbf" + EVENING.encode("utf-8"), bytearray(b"{}"), None, 7, json.loads(EVENING)):
        assert load_record(no_text) is None, type(no_text)
    # so deep that Python gives up: not a record either
    assert load_record("[" * 200_000 + "]" * 200_000) is None
    assert walk_row(load_record("")) is None


def test_nan_and_infinity_make_the_whole_file_unreadable():
    # Python's json takes these three words. The browser refuses the file whole - also when the
    # word stands in a place that is never sent, where Python would have made the row and sent it.
    for word in ("NaN", "Infinity", "-Infinity"):
        for key, old in (("start", "1795120200.4"), ("kcal", "2.5"), ("kcal_extra", "1.4"), ("max_kmh", "3.2"), ("avg_hr", "null")):
            text = EVENING.replace(f'"{key}":{old},', f'"{key}":{word},')
            assert text != EVENING
            assert load_record(text) is None, (word, key)
        assert load_record(EVENING.replace("[1.0,3.2,null]", f"[1.0,{word},null]")) is None
        assert load_record(word) is None
    # the same values handed over as numbers are no record
    for bad in (float("nan"), float("inf"), float("-inf")):
        for key in ("start", "end", "seconds", "distance_m", "steps", "kcal", "kcal_extra"):
            assert walk_row(record(EVENING, **{key: bad})) is None, (key, bad)


def test_a_number_of_400_digits_is_no_record_and_no_error():
    huge = "1" + "0" * 400
    for key, old in (("start", "1795120200.4"), ("end", "1795120244.6"), ("seconds", "40.0"), ("steps", "66"), ("kcal_extra", "1.4")):
        text = EVENING.replace(f'"{key}":{old},', f'"{key}":{huge},')
        assert text != EVENING
        loaded = load_record(text)
        assert type(loaded[key]) is int  # (Python keeps every digit, where the browser has "Infinity")
        assert walk_row(loaded) is None, key
        assert walk_row(record(EVENING, **{key: -(10**400)})) is None, key
    # a number too large written with an exponent
    assert walk_row(load_record(EVENING.replace('"start":1795120200.4,', '"start":1e400,'))) is None
    # in a place that is never sent it changes nothing, here as in the browser
    assert written(walk_row(load_record(EVENING.replace('"max_kmh":3.2,', f'"max_kmh":{huge},')))) == EVENING_TEXT
    # numbers a double can only just hold: no row, and no error either
    for key in ("seconds", "distance_m", "kcal", "kcal_extra"):
        assert walk_row(record(EVENING, **{key: 1.7e308})) is None, key
        assert walk_row(record(EVENING, **{key: 10**308})) is None, key


def test_steps_no_double_can_hold_are_the_steps_the_browser_reads():
    # (the website's reader gave 9007199254740992 and this reason)
    row = walk_row(load_record(EVENING.replace('"steps":66,', '"steps":9007199254740993,')))
    assert row["steps"] == 9007199254740992 and type(row["steps"]) is int
    assert outside(row, NOW_MS) == STEPS_MOST
    assert outside(walk_row(record(EVENING, steps=1e23)), NOW_MS) == STEPS_MOST


# ---- the limits ----------------------------------------------------------------------------------------


def test_a_number_outside_what_the_database_takes_is_named_and_not_cut_down_to_fit():
    start = EVENING_START
    refused = [
        ({"seconds": 86_400.1, "end": start + 2 * DAY_S}, SECONDS_MOST),
        ({"seconds": -1}, SECONDS),
        ({"distance_m": 200_000.1}, DISTANCE_MOST),
        ({"distance_m": -0.1}, DISTANCE),
        ({"steps": 400_001}, STEPS_MOST),
        ({"steps": -1}, STEPS),
        ({"kcal": 20_000.1}, KCAL_MOST),
        ({"kcal": -5}, KCAL),
        ({"kcal_extra": -0.1}, ON_TOP),
        ({"kcal_extra": 20_000.1}, ON_TOP),
        ({"end": start - 60}, BACKWARDS),
        ({"end": 1795120100}, BACKWARDS),
        ({"end": start + 2 * DAY_S + 1}, TWO_DAYS),
        ({"seconds": 200}, SECONDS_LONGER),
        ({"seconds": 104.3}, SECONDS_LONGER),
        ({"id": "19991231-230000", "start": 946_681_200 - 3600, "end": 946_681_200 - 3000}, BEFORE_2000),
        ({"id": "20991119-213000", "start": 4_098_889_800, "end": 4_098_889_860}, TO_COME),
        ({"id": "20261122-213000"}, DAY_FIT),
    ]
    for change, why in refused:
        row = walk_row(record(EVENING, **change))
        assert row is not None, change
        assert outside(row, NOW_MS) == why, change
        # nothing was cut down: what the file says is what the row says
        for key in ("seconds", "distance_m", "steps", "kcal"):
            if key in change:
                assert row[key] == change[key]


def test_right_at_the_limits_a_walk_is_taken():
    most = walk_row(
        record(EVENING, seconds=86_400, end=EVENING_START + DAY_S, distance_m=200_000, steps=400_000, kcal=20_000, kcal_extra=20_000)
    )
    assert [most["seconds"], most["distance_m"], most["steps"], most["kcal"], most["kcal_extra"]] == [86_400, 200_000, 400_000, 20_000, 20_000]
    assert outside(most, NOW_MS) is None
    assert outside(walk_row(record(EVENING, seconds=0, distance_m=0, steps=0, kcal=0)), NOW_MS) is None
    # a minute of room between the time walked and the time the walk lasted (it lasted 44.2 s)
    assert outside(walk_row(record(EVENING, seconds=104.2)), NOW_MS) is None
    # a walk that ends in the moment it starts is still within the limits (the website's reader took it)
    assert outside(walk_row(record(EVENING, end=EVENING_START)), NOW_MS) is None
    assert outside(walk_row(record(EVENING, end=EVENING_START, seconds=0, distance_m=0, steps=0, kcal=0, kcal_extra=0)), NOW_MS) is None
    # exactly two days long
    assert outside(walk_row(record(EVENING, end=EVENING_START + 2 * DAY_S)), NOW_MS) is None
    # the first second of the year 2000
    assert outside(walk_row(record(EVENING, id="20000101-000000", start=946_684_800, end=946_684_860)), NOW_MS) is None
    assert outside(walk_row(record(EVENING, id="19991231-235959", start=946_684_799.9, end=946_684_860)), NOW_MS) == BEFORE_2000


def test_a_clock_that_is_a_little_ahead_is_fine_and_two_days_is_the_room():
    # an hour after "now", on a clock an hour ahead of UTC
    soon = walk_row(record(EVENING, id="20261121-020000", start=1_795_222_800, end=1_795_222_860))
    assert soon["utc_offset_min"] == 60
    assert outside(soon, NOW_MS) is None
    # two days after "now" to the millisecond, and one millisecond more
    edge = walk_row(record(EVENING, id="20261123-000000", start=1_795_392_000, end=1_795_392_060))
    assert outside(edge, NOW_MS) is None
    over = walk_row(record(EVENING, id="20261123-000000", start=1_795_392_000.001, end=1_795_392_060))
    assert over["started_at"] == "2026-11-23T00:00:00.001Z"
    assert outside(over, NOW_MS) == TO_COME
    # the verdict goes by the day it is asked on: the same walk, asked about three weeks before it
    old = walk_row(json.loads(OLD))
    assert outside(old, NOW_MS) is None
    assert outside(old, 1_738_368_000_000) == TO_COME  # 1 February 2025


def test_outside_gives_each_of_the_websites_reasons():
    old = walk_row(json.loads(OLD))
    assert outside(old, NOW_MS) is None
    tried = [
        ({"started_at": "yesterday"}, NOT_A_TIME),
        ({"ended_at": "2025-03-02T07:45:00.000Z\n"}, NOT_A_TIME),
        ({"ended_at": None}, NOT_A_TIME),
        ({"local_day": "2025-02-30"}, NOT_A_DAY),
        ({"local_day": "2025-3-2"}, NOT_A_DAY),
        ({"local_day": "2025-03-02\n"}, NOT_A_DAY),
        ({"local_day": "0099-03-02"}, NOT_A_DAY),
        ({"local_day": None}, NOT_A_DAY),
        ({"client_id": "x" * 65}, NAME),
        ({"client_id": ""}, NAME),
        ({"ended_at": "2025-03-01T07:15:00.000Z"}, BACKWARDS),
        ({"ended_at": "2025-03-04T07:15:00.001Z"}, TWO_DAYS),
        ({"started_at": "1999-12-31T23:59:59.999Z", "ended_at": "2000-01-01T00:20:00.000Z", "local_day": "1999-12-31"}, BEFORE_2000),
        ({"started_at": "2026-11-23T00:00:00.001Z", "ended_at": "2026-11-23T00:30:00.000Z", "local_day": "2026-11-23"}, TO_COME),
        ({"seconds": -0.1}, SECONDS),
        ({"seconds": None}, SECONDS),
        ({"seconds": "1700"}, SECONDS),
        ({"seconds": True}, SECONDS),
        ({"seconds": 90_000}, SECONDS_MOST),
        ({"seconds": 1900}, SECONDS_LONGER),
        ({"distance_m": -1}, DISTANCE),
        ({"distance_m": None}, DISTANCE),
        ({"distance_m": 250_000}, DISTANCE_MOST),
        ({"steps": 12.5}, STEPS),
        ({"steps": -1}, STEPS),
        ({"steps": None}, STEPS),
        ({"steps": True}, STEPS),
        ({"steps": 500_000}, STEPS_MOST),
        ({"kcal": -0.1}, KCAL),
        ({"kcal": float("nan")}, KCAL),
        ({"kcal": 30_000}, KCAL_MOST),
        ({"kcal_extra": -0.1}, ON_TOP),
        ({"kcal_extra": 20_000.1}, ON_TOP),
        ({"kcal_extra": "none"}, ON_TOP),
        ({"kcal_extra": False}, ON_TOP),
        ({"kcal_extra": ""}, ON_TOP),
        ({"utc_offset_min": 841}, ZONE),
        ({"utc_offset_min": -721}, ZONE),
        ({"utc_offset_min": 60.5}, ZONE),
        ({"utc_offset_min": "60"}, ZONE),
        ({"utc_offset_min": False}, ZONE),
        ({"utc_offset_min": ""}, ZONE),
        ({"local_day": "2025-03-09"}, DAY_FIT),
        ({"local_day": "2025-02-28"}, DAY_FIT),
        ({"local_day": "2025-03-04"}, DAY_FIT),
    ]
    seen = set()
    for change, why in tried:
        assert outside({**old, **change}, NOW_MS) == why, change
        seen.add(why)
    assert seen == set(REASONS)
    # the first thing that is wrong is the one named, in the website's order
    assert outside({**old, "started_at": "yesterday", "local_day": "2025-02-30", "steps": -1}, NOW_MS) == NOT_A_TIME
    assert outside({**old, "local_day": "2025-02-30", "client_id": "", "steps": -1}, NOW_MS) == NOT_A_DAY
    assert outside({**old, "seconds": 90_000, "steps": -1, "utc_offset_min": 900}, NOW_MS) == SECONDS_MOST
    # what is allowed: the day before and the day after are both possible, somewhere on Earth
    for fine in (
        {"local_day": "2025-03-01"},
        {"local_day": "2025-03-03"},
        {"utc_offset_min": 840},
        {"utc_offset_min": -720},
        {"utc_offset_min": None},
        {"utc_offset_min": 0},
        {"kcal_extra": None},
        {"kcal_extra": 0},
        {"kcal_extra": 20_000},
        {"client_id": "x" * 64},
        {"steps": 2100.0},
    ):
        assert outside({**old, **fine}, NOW_MS) is None, fine


def test_the_day_may_be_one_before_the_start_or_one_after_the_end():
    # 23:50 to 00:10 UTC: the walk is on two days there, so four days are possible somewhere on Earth, and no more.
    old = walk_row(json.loads(OLD))
    across = {**old, "started_at": "2025-03-02T23:50:00.000Z", "ended_at": "2025-03-03T00:10:00.000Z", "seconds": 600}
    for day in ("2025-03-01", "2025-03-02", "2025-03-03", "2025-03-04"):
        assert outside({**across, "local_day": day}, NOW_MS) is None, day
    for day in ("2025-02-28", "2025-03-05"):
        assert outside({**across, "local_day": day}, NOW_MS) == DAY_FIT, day


def test_outside_reads_a_time_only_as_walk_row_writes_it():
    # A browser reads more ways of writing a time (and takes 30 February for 2 March). Here such a row is refused:
    # none comes from walk_row(), and what is refused is not sent.
    old = walk_row(json.loads(OLD))
    for odd in (
        "2025-02-30T07:15:00.000Z",
        "2025-03-02T24:00:00.000Z",
        "2025-03-02T07:60:00.000Z",
        "2025-03-02T07:15:60.000Z",
        "2025-13-02T07:15:00.000Z",
        "0000-03-02T07:15:00.000Z",
        "2025-03-02T07:15:00Z",
        "2025-03-02T07:15:00.000+00:00",
        "2025-03-02 07:15:00.000Z",
        "2025-03-02",
        " 2025-03-02T07:15:00.000Z",
        1740899700000,
    ):
        assert outside({**old, "started_at": odd}, NOW_MS) == NOT_A_TIME, odd
        assert outside({**old, "ended_at": odd}, NOW_MS) == NOT_A_TIME, odd


def test_a_names_length_is_counted_as_the_browser_counts_it():
    # JavaScript counts a character beyond U+FFFF twice: 33 of them are 66 to the website, and it refuses the name.
    old = walk_row(json.loads(OLD))
    assert outside({**old, "client_id": "\U0001f6b6" * 33}, NOW_MS) == NAME
    assert outside({**old, "client_id": "\U0001f6b6" * 32}, NOW_MS) is None
    assert outside({**old, "client_id": "\u00e9" * 64}, NOW_MS) is None
    assert outside({**old, "client_id": "\u00e9" * 65}, NOW_MS) == NAME
    assert outside({**old, "client_id": "\ud83d" * 65}, NOW_MS) == NAME  # (half a character, as a broken file may hold: no error)
    for no_text in (5, 0, True, ["a"], {}, b"20250302-081500"):
        assert outside({**old, "client_id": no_text}, NOW_MS) == NAME, no_text


def test_outside_does_not_raise_for_a_row_that_is_no_row():
    assert outside({}, NOW_MS) == NOT_A_TIME
    assert outside({"started_at": 5, "ended_at": []}, NOW_MS) == NOT_A_TIME
    old = walk_row(json.loads(OLD))
    for key in SIXTEEN[1:]:
        for junk in (None, "", "x", -1, 10**400, float("nan"), float("inf"), True, [], {}):
            why = outside({**old, key: junk}, NOW_MS)
            assert why is None or why in REASONS, (key, junk)
        missing = {name: value for name, value in old.items() if name != key}
        why = outside(missing, NOW_MS)
        assert why is None or why in REASONS, key


# ---- times a walk never has, and a file may ----------------------------------------------------------


def test_a_start_long_before_walka_is_written_as_the_website_writes_it():
    # (the website's reader gave these rows and reasons; a year below 1000 keeps its four digits on every system)
    early = walk_row(record(EVENING, start=-30_641_760_000))
    assert early["started_at"] == "0999-01-01T00:00:00.000Z"
    assert early["utc_offset_min"] is None
    assert outside(early, NOW_MS) == TWO_DAYS
    assert start_key(early) == "0999-01-01T00:00:00"
    both = walk_row(record(EVENING, start=-30_627_460_800, end=-30_627_460_740))
    assert [both["started_at"], both["ended_at"]] == ["0999-06-15T12:00:00.000Z", "0999-06-15T12:01:00.000Z"]
    assert outside(both, NOW_MS) == BEFORE_2000
    first = walk_row(record(EVENING, start=-62_135_596_800, end=-62_135_596_740))
    assert first["started_at"] == "0001-01-01T00:00:00.000Z"
    assert outside(first, NOW_MS) == BEFORE_2000
    before_1970 = walk_row(record(EVENING, start=-1.5))
    assert before_1970["started_at"] == "1969-12-31T23:59:58.500Z"
    assert outside(before_1970, NOW_MS) == TWO_DAYS
    last = walk_row(record(EVENING, end=253_402_300_799))
    assert last["ended_at"] == "9999-12-31T23:59:59.000Z"
    assert outside(last, NOW_MS) == TWO_DAYS


def test_a_time_outside_the_years_1_to_9999_is_no_record():
    # The website's reader makes a row of these ("+010000-01-01T...") and its limits then refuse it.
    # Here it is no record. Either way the walk is not sent.
    for key, value in (
        ("start", 253_402_300_800),
        ("end", 253_402_300_800),
        ("end", 253_402_300_799.9996),
        ("start", -62_135_596_801),
        ("start", 8.64e12),
        ("start", 8_640_000_000_001),
        ("end", 1e300),
        ("start", -1e300),
    ):
        assert walk_row(record(EVENING, **{key: value})) is None, (key, value)


# ---- whatever is in the file ---------------------------------------------------------------------------


def test_no_record_makes_it_raise_and_every_row_made_can_be_sent_as_json():
    dice = random.Random(20261121)
    ids = [
        "20261119-213000", "20261119-213000-2", "20261119-203000", "20261114-235940", "20250302-081500",
        "19991231-230000", "09990101-000000", "01001119-213000", "99991231-235959", "00010101-000000",
        "20261119-213000\n", "00991119-213000", "walk-1", "", None, 17,
    ]
    numbers = [
        0, -0.0, 1, 66, 66.0, 66.5, -1, -0.04, 1e-9, 0.05, 0.049999999999999996, 40.05, 2.675, 1700.0, 86_400,
        86_400.1, 200_000.1, 400_001, 20_000.1, 1795120200.4, 1795120244.6, 1795120200.9996, 1794697180,
        1740899700, 946_684_799.9, 4_098_889_800, -1.5, -30_641_760_000, -62_135_596_800, -62_135_596_801,
        253_402_300_799, 253_402_300_799.9996, 253_402_300_800, 8.64e12, 8_640_000_000_001, 2**53 + 1, 1e15,
        1e16, 1e22, 1e300, 1.7e308, 10**308, 10**400, -(10**400), float("inf"), float("-inf"), float("nan"),
        True, False, None, "40", [], {},
    ]
    words = ["wow", "manual", "free", "treadmill", "estimate", "speed", "heart_rate", "android", "", None, 7, [], True]
    made = 0
    reasons = set()
    for _ in range(40_000):
        candidate = {
            # (mostly a real id: or hardly a row would be made)
            "id": dice.choice(ids) if dice.random() < 0.3 else dice.choice(ids[:10]),
            "source": dice.choice(words),
            "steps_source": dice.choice(words),
            "distance_source": dice.choice(words),
            "calories_source": dice.choice(words),
            "treadmill": "UREVO-E1 <pad>",
            "samples": [[1.0, 3.2, None]],
        }
        for key in ("start", "end", "seconds", "distance_m", "steps", "kcal", "kcal_extra"):
            candidate[key] = dice.choice(numbers)
        if dice.random() < 0.5:
            # a real record with one or two things wrong: so that the later checks of outside() are reached too
            candidate = {**json.loads(dice.choice((EVENING, MIDNIGHT, OLD))), **dict(dice.sample(sorted(candidate.items(), key=repr), 2))}
        if dice.random() < 0.1:
            del candidate[dice.choice(sorted(candidate))]
        row = walk_row(candidate)
        if row is None:
            continue
        made += 1
        assert tuple(row) == SIXTEEN[1:]
        assert row["origin"] == "walka" and row["client_id"] == candidate["id"]
        assert type(row["steps"]) is int
        assert row["utc_offset_min"] is None or (type(row["utc_offset_min"]) is int and -720 <= row["utc_offset_min"] <= 840)
        assert WRITTEN_TIME.fullmatch(row["started_at"]) and WRITTEN_TIME.fullmatch(row["ended_at"])
        assert len(start_key(row)) == 19 and row["started_at"].startswith(start_key(row))
        assert row["session"] in ("wow", "manual", "free", None)
        # it can be written as JSON a database reads: no NaN, no Infinity
        text = json.dumps(with_id(row, "3f2b8a52-6c1d-4e0f-9a47-0d5c1e2b7a90"), allow_nan=False)
        assert load_record(text) is not None
        for now_ms in (NOW_MS, 0):
            why = outside(row, now_ms)
            assert why is None or why in REASONS
            # a time this module wrote is a time it can read
            assert why != NOT_A_TIME
            reasons.add(why)
    # (so that this test cannot pass by making no rows)
    assert made > 500
    assert None in reasons and len(reasons) > 10


def test_walk_row_never_raises_on_things_that_are_no_record_at_all():
    class Odd(dict):
        def get(self, *_):
            raise ValueError("no")

    for thing in (None, 0, 1.5, "x", b"x", [], (), set(), object(), {"id": []}, {"id": {}}, {1: 2}, {"id": "20261119-213000", "start": object()}, Odd()):
        assert walk_row(thing) is None


def test_the_module_keeps_to_the_standard_library_and_asks_no_clock():
    with open(row_module.__file__, encoding="utf-8") as file:
        source = file.read()
    assert "from __future__ import annotations" in source
    assert "aiohttp" not in source
    # the time is handed in (now_ms): the mapping itself asks nobody what time it is
    for clock in ("time.time", "datetime.now", "utcnow", "fromtimestamp", "fromisoformat", "localtime", "today()"):
        assert clock not in source, clock
    assert "import.ts" in source and "docs/walka_row.py" in source
