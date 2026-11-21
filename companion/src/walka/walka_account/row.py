"""A walk as Walka keeps it (one file of its "sessions" folder) -> a row for the Walka account's "walks" table.

Where this comes from: the website (the repository walka-website) keeps
its own reader of Walka's files in Python, docs/walka_row.py, for
whoever writes a program that sends walks. That file is its browser
code, src/lib/account/import.ts (walkaRecord and outside), said again in
Python, so that a program sends the SAME row the website makes from the
same file. This is that file, taken over. Standard library only.

    record = load_record(text)         # the file's text. None: not JSON as a browser reads it
    row = walk_row(record)             # None: not one of Walka's records. It never raises
    why = outside(row, now_ms)         # None: within the database's limits; else the reason, in the website's words
    key = start_key(row)               # how the database tells two walks apart: the start, to the second
    sent = with_id(row, row_id)        # the sixteen keys of a row as it is sent, the id first

On which records it was checked. The website checked docs/walka_row.py
on 21 November 2026 against its browser code on 564 records (503 made by
Walka's own recorder in eleven time zones, 61 awkward ones): the same
answer for each. The three records in the website's tests/import.test.ts
(EVENING, MIDNIGHT, OLD) and the rows expected for them stand in this
repository's tests, letter for letter (tests/test_walka_account_row.py).
This copy was tried the same day beside a copy of import.ts, run by
node 22, on 360,000 awkward files: every walk that would be sent was the
same row, to the letter, and no walk would be sent from here that the
website would not send. Nothing in this repository runs that again; its
tests hold what was learned.

If import.ts changes, this must change with it - and those three records
are the first thing to try.

Where this copy differs from docs/walka_row.py: only at edges that no
file written by Walka reaches, and there it goes by import.ts, because
the browser is what people's walks have gone through so far.

- An id with a line end after it is not an id (Python's "$" lets one
  pass; JavaScript's does not).
- NaN and Infinity in a file make the whole file unreadable, as in the
  browser (Python's json takes them): load_record().
- Every number is a double, as in JavaScript. So a number too large for
  one is no number (and no error), and steps no double can hold are the
  steps the browser would read.
- Rounding is Math.round exactly, also just under a half.
- A year below 1000 is written with four digits on every system
  ("0999-..."), and outside() reads such a time.
- outside() has all of import.ts's checks, and does not raise. A name's
  length is counted as JavaScript counts it (in UTF-16 units).
- walk_row() never raises.

Where it still differs from import.ts, knowingly. None of these lets a
walk through that the website would not send.

- A start or an end outside the years 1 to 9999, or a number that
  rounds to more than a double holds, is no record here. The browser
  makes a row of it ("+010000-01-01T...", Infinity) and its limits then
  refuse the row. So a row made here can always be written as JSON.
- A whole number of 2^53 or more is written with other digits than the
  browser writes. The limits refuse such a row here and there.
- A file that begins with a byte order mark is not read here, nor one
  with other blank characters around it than a space, a tab and line
  ends (a no-break space, a form feed); the website reads both. Walka
  does not load such a file as a walk either, so none comes this way.
- load_record() takes text and nothing else. Handed bytes, Python's json
  would guess at how they are written (UTF-16, say), which neither the
  browser nor Walka does: so bytes are not read.
- outside() reads a time only as this file writes it. Browsers read
  more ways of writing one, and not all the same ways.

Before a row is sent as JSON: "steps" and "utc_offset_min" are whole
numbers (int), never 66.0; and every row of one request has the same
keys, with None where there is nothing.
"""

from __future__ import annotations

import calendar
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

MOST_SECONDS = 86_400
MOST_METRES = 200_000
MOST_STEPS = 400_000
MOST_KCAL = 20_000
DAY_MS = 86_400_000
TWO_DAYS_MS = 2 * DAY_MS
YEAR_2000_MS = 946_684_800_000

# The keys of a row as it is sent: the website's sixteen (src/lib/account/walks.ts), in its order.
# The first is the row's own id; the other fifteen are what walk_row() gives.
ROW_KEYS: tuple[str, ...] = (
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

# import.ts WALKA_ID. [0-9], not \d: JavaScript's \d is ASCII only, Python's is not.
# Used with fullmatch: Python's "$" would let a line end after the id pass, JavaScript's does not.
WALKA_ID = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})(?:-[0-9]{1,9})?")
# A time as _utc() writes it, and a day as walk_row() writes it.
WRITTEN_TIME = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})Z")
WRITTEN_DAY = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _no_such_number(word: str) -> Any:
    raise ValueError("not JSON")  # ("NaN", "Infinity", "-Infinity": Python's own additions to JSON)


def load_record(text: str) -> Any | None:
    """A file's text -> what is in it, as import.ts readFile() reads it with JSON.parse. None if it cannot be read.

    Python's json takes NaN and Infinity; the browser refuses a file that
    holds one, whole - also when it stands in a place that is never sent,
    where Python alone would make the row and send it.
    """
    if not isinstance(text, str):  # (bytes: see the top of this file)
        return None
    try:
        return json.loads(text, parse_constant=_no_such_number)
    except Exception:  # not JSON, nested too deep, a number of 5,000 digits: whatever it is, it is no record
        return None


def _number(value: Any) -> float | None:
    """import.ts number(): a JSON number that is finite - as JavaScript has it, a double. Else None.

    (true/false are not numbers in JavaScript. A number too large for a
    double is Infinity there, so it is none here.)
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _js_round(x: float) -> int:
    """Math.round: halves go up (towards +infinity). Not floor(x + 0.5): that sum is itself rounded."""
    below = math.floor(x)
    return below + 1 if x - below >= 0.5 else below


def _tenth(n: float) -> float | int:
    """import.ts tenth(): Math.round(n * 10) / 10. A whole number comes back whole, as the browser writes it (40, not 40.0)."""
    value = _js_round(n * 10) / 10
    return int(value) if value == int(value) else value


def _utc(seconds: float) -> str | None:
    """import.ts utc(): seconds since 1970 -> "2026-11-19T20:30:00.400Z" (milliseconds, rounded)."""
    ms = seconds * 1000
    if not math.isfinite(ms) or abs(ms) > 8.64e15:
        return None
    try:
        at = EPOCH + timedelta(milliseconds=_js_round(ms))
    except OverflowError:  # (outside the years 1 to 9999: see the top of this file)
        return None
    # Not strftime("%Y"): on Linux that writes the year 999 as "999", and the browser writes "0999".
    return (
        f"{at.year:04d}-{at.month:02d}-{at.day:02d}"
        f"T{at.hour:02d}:{at.minute:02d}:{at.second:02d}.{at.microsecond // 1000:03d}Z"
    )


def _word(value: Any, known: tuple[str, ...]) -> str | None:
    """import.ts word(): one of the words the database knows, or None. A word it does not know is no reason to refuse a walk."""
    return value if isinstance(value, str) and value in known else None


def _walk_row(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    found = WALKA_ID.fullmatch(record["id"]) if isinstance(record.get("id"), str) else None
    if not found:
        return None
    start, end, seconds, distance_m, steps, kcal = (
        _number(record.get(key)) for key in ("start", "end", "seconds", "distance_m", "steps", "kcal")
    )
    if start is None or end is None or seconds is None or distance_m is None or steps is None or kcal is None:
        return None
    if not steps.is_integer():  # Number.isInteger: Walka writes steps as a whole number, always
        return None
    extra = record.get("kcal_extra")
    extra = 0.0 if extra is None else _number(extra)  # (older records do not have it at all)
    if extra is None:
        return None
    started_at, ended_at = _utc(start), _utc(end)
    if not started_at or not ended_at:
        return None

    y, mo, d, h, mi, s = (int(part) for part in found.groups())
    try:
        datetime(y, mo, d, h, mi, s)  # "20261340-256199" is no time
    except ValueError:
        return None
    if y < 100:  # JavaScript's Date.UTC reads the years 0-99 as 1900-1999: the id does not come back the same
        return None
    wall = calendar.timegm((y, mo, d, h, mi, s))  # the id read as if it were UTC, in seconds

    # How far that clock was ahead of UTC then. Kept only when id and start agree to the second
    # (the id drops the tenths, "start" rounds them).
    ahead = (wall - start) / 60
    minutes = _js_round(ahead)
    offset = minutes if abs(ahead - minutes) * 60 <= 1.5 and -720 <= minutes <= 840 else None

    return {
        "origin": "walka",
        "client_id": record["id"],
        "started_at": started_at,
        "ended_at": ended_at,
        # The day where the walk was: the id says it. It is not worked out again from the time,
        # which would give the day of whoever reads the file.
        "local_day": f"{found.group(1)}-{found.group(2)}-{found.group(3)}",
        "utc_offset_min": offset,
        "seconds": _tenth(seconds),
        "distance_m": _tenth(distance_m),
        "steps": int(steps),
        "kcal": _tenth(kcal),
        # 0 in a record with calories is an older record that never knew: empty, not "none"
        "kcal_extra": None if extra == 0 and kcal > 0 else _tenth(extra),
        "session": _word(record.get("source"), ("wow", "manual", "free")),
        "steps_source": _word(record.get("steps_source"), ("treadmill", "estimate")),
        "distance_source": _word(record.get("distance_source"), ("treadmill", "speed")),
        "calories_source": _word(record.get("calories_source"), ("speed", "heart_rate")),
    }


def walk_row(record: Any) -> dict[str, Any] | None:
    """import.ts walkaRecord(). None if it is not one of Walka's records. The limits are not looked at: see outside().

    It never raises: a file can hold anything. Where Python's arithmetic
    gives up (a number that rounds to more than a double holds, say), that
    is no record.
    """
    try:
        return _walk_row(record)
    except (OverflowError, ValueError, TypeError):
        return None


def start_key(row: dict[str, Any]) -> str:
    """The start cut to the second, in UTC, without a zone: "2026-11-19T20:30:00".

    The database makes its "start_key" so, and one account has one walk
    for each: it is how a row that was there already is matched to its walk.
    """
    return row["started_at"][:19]


def with_id(row: dict[str, Any], row_id: str) -> dict[str, Any]:
    """The row as it is sent: exactly the sixteen keys of ROW_KEYS, in that order, and nothing that rode along."""
    return {"id": row_id, **{key: row[key] for key in ROW_KEYS[1:]}}


def _ms(text: Any) -> int | None:
    """A time as _utc() writes it -> milliseconds since 1970. None for anything else.

    (The browser's Date.parse reads more ways of writing a time. A row
    made by walk_row() has this one, so no more is read here.)
    """
    found = WRITTEN_TIME.fullmatch(text) if isinstance(text, str) else None
    if not found:
        return None
    y, mo, d, h, mi, s, ms = (int(part) for part in found.groups())
    try:
        datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    return calendar.timegm((y, mo, d, h, mi, s)) * 1000 + ms


def _day_ms(text: Any) -> int | None:
    """import.ts dayAsUtc(): "2026-11-19" -> that day at midnight UTC in milliseconds, or None if there is no such day."""
    found = WRITTEN_DAY.fullmatch(text) if isinstance(text, str) else None
    if not found:
        return None
    y, mo, d = (int(part) for part in found.groups())
    if y < 100:  # (Date.UTC again: such a day does not come back the same)
        return None
    try:
        datetime(y, mo, d)
    except ValueError:
        return None
    return calendar.timegm((y, mo, d, 0, 0, 0)) * 1000


def _js_length(text: str) -> int:
    """A string's length as JavaScript counts it: in UTF-16 units, so a character beyond U+FFFF counts twice."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def outside(row: dict[str, Any], now_ms: int) -> str | None:
    """import.ts outside(): what the database would refuse, in the website's words - or None.

    now_ms is the moment it is asked, in milliseconds since 1970: one of
    the answers depends on the day, so the verdict is not one to keep.
    Nothing is cut down to fit: a walk outside the limits is not sent.
    """
    start, end = _ms(row.get("started_at")), _ms(row.get("ended_at"))
    day = _day_ms(row.get("local_day"))
    if start is None or end is None:
        return "Its start or its end is not a time."
    if day is None:
        return "Its day is not a day."
    name = row.get("client_id")
    if not isinstance(name, str) or not name or _js_length(name) > 64:
        return "Its name is longer than 64 characters."

    if end < start:
        return "It ends before it starts."
    if end - start > TWO_DAYS_MS:
        return "It lasts longer than two days."
    if start < YEAR_2000_MS:
        return "It starts before the year 2000."
    if start > now_ms + TWO_DAYS_MS:
        return "It starts in the days to come."

    seconds = _number(row.get("seconds"))
    if seconds is None or seconds < 0:
        return "The time walked is not a number from zero up."
    if seconds > MOST_SECONDS:
        return "The time walked is more than a day."
    # the belt did not move for longer than the walk lasted (a minute of room, as in the database)
    if seconds > (end - start) / 1000 + 60:
        return "The time walked is longer than the walk lasted."
    distance_m = _number(row.get("distance_m"))
    if distance_m is None or distance_m < 0:
        return "The distance is not a number from zero up."
    if distance_m > MOST_METRES:
        return "The distance is more than 200 km (124 miles)."
    steps = _number(row.get("steps"))
    if steps is None or not steps.is_integer() or steps < 0:
        return "The steps are not a whole number from zero up."
    if steps > MOST_STEPS:
        return "There are more than 400,000 steps."
    kcal = _number(row.get("kcal"))
    if kcal is None or kcal < 0:
        return "The calories are not a number from zero up."
    if kcal > MOST_KCAL:
        return "There are more than 20,000 kcal."
    if row.get("kcal_extra") is not None:
        extra = _number(row["kcal_extra"])
        if extra is None or extra < 0 or extra > MOST_KCAL:
            return "The calories on top of sitting are not a number from 0 to 20,000."
    if row.get("utc_offset_min") is not None:
        offset = _number(row["utc_offset_min"])
        if offset is None or not offset.is_integer() or offset < -720 or offset > 840:
            return "Its time zone is not one there is."

    # the day is the day the walk was on, give or take a time zone
    first_day = (start // DAY_MS) * DAY_MS - DAY_MS
    last_day = (end // DAY_MS) * DAY_MS + DAY_MS
    if day < first_day or day > last_day:
        return "Its day does not fit the time it started."
    return None
