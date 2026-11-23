"""A stand-in for the accounts, for the tests of Walka's sending of walks.

It answers on 127.0.0.1 the way the real thing was seen to answer, and it
keeps the list of what Walka may ask at all. Every later test of the
feature runs against it, so it is written from the record alone and
imports nothing of the feature: a mistake there must not be mirrored here.

Where each answer comes from (all in the website's repository):

- the brief, ``docs/brief-walka-sends-walks.md``, sections 5.1 to 5.4;
- ``docs/calls-tried.md``, the requests tried with plain HTTP ("item 9"
  below is its ninth numbered item);
- ``docs/data-model.md``, "Syncing" and "Signing in from a program";
- ``supabase/migrations/20261120000000_accounts.sql``, the table of walks,
  its rules and its triggers.

How it is used::

    async with FakeAccounts(wall=clock) as fake:
        user = fake.add_user("a@b.example")
        ...                              # point the program at fake.origin, with fake.key
        assert fake.violations == []     # in a fixture, after EVERY test

``violations`` is the point of it. A request outside the list of brief 5.4
is answered 400, which a program may well take in its stride - so the
handler does not fail anything itself. It writes the request down, and the
test's fixture fails on it. ``collect_violations()`` gives a fixture those
of every stand-in a test made, without the test having to hand them over::

    @pytest.fixture(autouse=True)
    def nothing_outside_the_list():
        fake_accounts.collect_violations()            # what an earlier test left
        yield
        assert fake_accounts.collect_violations() == []

What a test can set up and ask for: ``add_user``, ``take_first_step``,
``set_page``, ``delete_user``, ``sign_out_everywhere``, ``new_sign_in`` (a
sign-in without the browser), ``next_browser_user``, ``walks``,
``add_walk``, ``fill``, ``mark_deleted``, ``expire_access_tokens``,
``fail_next``, ``drop_next``, ``hold`` (a ``Gate``), ``secrets_issued``,
and the browser: ``browser``, ``authorize``, ``visit``. What it wrote down:
``requests`` (each a ``Seen``), ``violations``, ``sent_to``, ``landed``.

Things a test writer trips over:

- ``reuse_window_s`` is 10, as in the real sign-in service (item 9): an old
  refresh token used again within ten seconds of its change still gives
  the current one back. With a clock that stands still, nobody ever sees
  ``refresh_token_already_used``. Move the clock on, or set the window to 0.
- ``rest_grace_s`` is 30 (item 2): the database lets an access token
  through for half a minute after it ran out; the sign-in service does not.
  ``expire_access_tokens()`` puts them all an hour past.
- An access token that was signed out, or whose account is gone, still
  opens the database until it runs out (item 10, item 27).
- The message of a full account names ``full_at``, as the trigger does.
- ``collect_violations()`` empties ``violations`` of every stand-in it asks.
  A test that MEANS a request outside the list clears them itself first.
- A line cut with ``drop_next`` or ``gate.drop()``: aiohttp's plain session
  asks a GET once more by itself on a line it had kept open, and that try
  is answered. Walka opens a line for each request.
- A header sent twice is outside the list. Some names (User-Agent, Host)
  aiohttp's own reader refuses twice before the stand-in is asked: 400, and
  nothing written down.

Where it is NOT the real thing, knowingly:

- It is stricter about numbers: a number written as text ("66") is taken
  by Postgres and refused here. Walka sends numbers.
- It reads a time only in the ISO form. Postgres reads many more.
- The names of the table's rules (``walks_check1`` and so on) follow
  Postgres's way of naming them, worked out from the SQL. They were not
  read off a live database. Neither were the sign-in service's sentences
  (its codes were), nor the gateway's words for a missing key.
- A return address is allowed only as ``http://127.0.0.1:<port>/<path>``.
  The real service also takes other spellings of this computer.
- A sign-in that was begun in the browser never grows too old here.
- Access tokens are random text, not JWTs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import string
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

# The keys of a row, exactly: the website's 16. Not "device", not "user_id".
ROW_KEYS = (
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

# Every column of public.walks, in the table's order (the SQL, lines 443-501).
COLUMNS = (
    "id",
    "user_id",
    "origin",
    "client_id",
    "device",
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
    "created_at",
    "updated_at",
    "deleted_at",
    "start_key",
)

# "not null" in the table, in its order. (user_id fills itself in.)
REQUIRED = (
    "id",
    "origin",
    "client_id",
    "started_at",
    "ended_at",
    "local_day",
    "seconds",
    "distance_m",
    "steps",
    "kcal",
)

PREFER = "resolution=ignore-duplicates,return=representation"
ON_CONFLICT = "user_id,start_key"
SELECT_FIRST_STEP = "adult_confirmed_at"
SELECT_PAGE = "handle,is_public,show_days,show_walks"
SELECT_FIND = "id,start_key,deleted_at"
MOST_ROWS = 200

ADULTS = "Walka is for adults."
DAYS_TO_COME = "A walk cannot start in the days to come."
FULL = "There is room for {} rows of this kind in one account, and it is full."

# What the browser is sent back with when the person says no. A sign-in page
# must never show it: a test can look for these words.
DENIED_DESCRIPTION = "The person said no at the other site"

WAYS = ("discord", "github", "google")

# Header names Walka may send. Nothing about the computer, no cookie.
HEADERS = frozenset(
    name.lower()
    for name in (
        "Host",
        "User-Agent",
        "Accept",
        "Accept-Encoding",
        "Content-Type",
        "Content-Length",
        "Connection",
        "apikey",
        "Authorization",
        "Prefer",
    )
)

_AGENT = re.compile(r"Walka/\S+")
_RETURN = re.compile(r"http://127\.0\.0\.1:\d{1,5}/[^?#\s]*")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43,128}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-?(?:[0-9a-fA-F]{4}-?){3}[0-9a-fA-F]{12}")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?"
)
_IN = re.compile(r"in\.\((.*)\)", re.DOTALL)
_UTC = timezone.utc
_YEAR_2000 = datetime(2000, 1, 1, tzinfo=_UTC)
_TWO_DAYS = timedelta(days=2)
_UNSET: Any = object()

# Every stand-in made and not yet done with, for a fixture: see collect_violations().
_EVERYWHERE: list[FakeAccounts] = []


def collect_violations() -> list[str]:
    """Every request outside the list that any stand-in saw since this was last called.

    It forgets them: each stand-in's ``violations`` is empty afterwards. A
    stand-in that is closed is let go of; one that still runs, or was made
    and not started yet, is kept and asked again the next time - so one
    made before a test's fixture began is not lost sight of.
    """
    found: list[str] = []
    for fake in list(_EVERYWHERE):
        found += fake.violations
        fake.violations.clear()
        if fake._closed:
            _EVERYWHERE.remove(fake)
    return found


def waits_for_a_caller(task: asyncio.Task) -> bool:
    """Is ``task`` the loop's own, in which a server that listens waits for whoever connects next?

    On Windows the loop keeps one such task for every server that listens
    - the stand-in's among them - and begins a new one each time somebody
    connects. It ends when the server is closed. Nothing of Walka's began
    it: a test that looks at which tasks are still running must not take
    it for one left behind. (On other systems a server waits without a
    task. The name is the one Python gives it: asyncio's
    ``IocpProactor.accept``.)
    """
    return getattr(task.get_coro(), "__qualname__", "").endswith("accept.<locals>.accept_coro")


# (method, path) -> what it is. Anything else is outside the list of brief 5.4.
_ROUTES = {
    ("GET", "/auth/v1/authorize"): "authorize",
    ("GET", "/landed"): "landed",
    ("POST", "/auth/v1/token"): "token",
    ("POST", "/auth/v1/logout"): "logout",
    ("GET", "/auth/v1/user"): "user",
    ("POST", "/rest/v1/rpc/schema_version"): "schema_version",
    ("GET", "/rest/v1/profiles"): "profiles",
    ("POST", "/rest/v1/walks"): "push",
    ("GET", "/rest/v1/walks"): "find",
}
_BROWSER = ("authorize", "landed")


@dataclass
class Seen:
    """One request, as it arrived."""

    method: str
    path: str
    query: list[tuple[str, str]]
    headers: dict[str, str]  # names as they were sent
    json: Any  # the body read as JSON; None if there was none, or it was not JSON
    body: bytes = b""

    def header(self, name: str) -> str | None:
        """A header's value, whatever the case of its name."""
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


class Gate:
    """Holds one answer back until the test lets it go."""

    def __init__(self) -> None:
        self.arrived = asyncio.Event()  # the request is here, and waits
        self.left = asyncio.Event()  # whoever asked went away while it waited
        self._go = asyncio.Event()
        self._drop = False

    def release(self) -> None:
        """Let the answer go on its way."""
        self._go.set()

    def drop(self) -> None:
        """Cut the line instead: nothing is done, and no answer comes."""
        self._drop = True
        self._go.set()


@dataclass
class _Plan:
    kind: str  # "answer" | "drop" | "hold"
    prefix: str
    method: str | None
    after: bool
    times: int
    status: int = 0
    body: Any = None
    headers: dict[str, str] | None = None
    gate: Gate | None = None


@dataclass
class _User:
    id: str
    email: str
    way: str
    profile: dict[str, Any] | None  # the row in "profiles", if there is one


@dataclass
class _SignIn:
    user: str
    tokens: list[str]  # its refresh tokens, oldest first; the last is the current one
    changed: dict[str, float]  # refresh token -> when it was changed for the next
    revoked: bool = False


@dataclass
class _Flow:
    challenge: str
    user: str


class _Refused(Exception):
    """The database says no, in PostgREST's shape."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers
        self.body = {"code": code, "details": details, "hint": None, "message": message}


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> web.Response:
    return web.Response(status=status, text=json.dumps(body), content_type="application/json", headers=headers)


def _rest_error(status: int, code: str, message: str, details: str | None = None) -> web.Response:
    return _json(status, {"code": code, "details": details, "hint": None, "message": message})


def _auth_error(status: int, code: str, message: str) -> web.Response:
    # The sign-in service's shape for a program that names no API version.
    return _json(status, {"code": status, "error_code": code, "msg": message})


def _no_constants(word: str) -> Any:
    raise ValueError(word)  # NaN and Infinity are not JSON


def _db_time(when: datetime) -> str:
    """A moment as the database writes it: none to six decimals, zeros at the end dropped."""
    text = f"{when.year:04d}-{when.month:02d}-{when.day:02d}T{when.hour:02d}:{when.minute:02d}:{when.second:02d}"
    if when.microsecond:
        text += f".{when.microsecond:06d}".rstrip("0")
    return text + "+00:00"


def _key_text(when: datetime) -> str:
    """start_key: the start cut to the second, in UTC, no zone (the SQL, line 491)."""
    return f"{when.year:04d}-{when.month:02d}-{when.day:02d}T{when.hour:02d}:{when.minute:02d}:{when.second:02d}"


def _as_written(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _read_time(value: Any, kind: str = "timestamp with time zone", keep_zone: bool = True) -> datetime:
    """Text -> a moment. No zone is UTC. A number is refused (item 17)."""
    if isinstance(value, int) and not isinstance(value, bool):
        raise _Refused(400, "22008", f'date/time field value out of range: "{value}"')
    if not isinstance(value, str):  # a number with a fraction among them
        raise _Refused(400, "22007", f'invalid input syntax for type {kind}: "{_as_written(value)}"')
    found = _TIME.fullmatch(value.strip())
    if found is None:
        raise _Refused(400, "22007", f'invalid input syntax for type {kind}: "{value}"')
    year, month, day, hour, minute, second = (int(part) for part in found.groups()[:6])
    micro = round(int((found.group(7) or "0").ljust(9, "0")) / 1000)
    zone = found.group(8)
    offset = timedelta(0)
    if keep_zone and zone and zone not in ("Z", "z"):
        digits = zone[1:].replace(":", "").ljust(4, "0")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        if zone[0] == "-":
            offset = -offset
    try:
        when = datetime(year, month, day, hour, minute, second, tzinfo=_UTC) + timedelta(microseconds=micro) - offset
    except (ValueError, OverflowError):
        raise _Refused(400, "22008", f'date/time field value out of range: "{value}"') from None
    return when


def _read_day(value: Any) -> date:
    if not isinstance(value, str) or _DATE.fullmatch(value.strip()) is None:
        raise _Refused(400, "22007", f'invalid input syntax for type date: "{_as_written(value)}"')
    year, month, day = (int(part) for part in _DATE.fullmatch(value.strip()).groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise _Refused(400, "22008", f'date/time field value out of range: "{value}"') from None


def _read_whole(value: Any, kind: str, most: int) -> int:
    """A whole number in the JSON itself: 66, not 66.0 (item 17)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Refused(400, "22P02", f'invalid input syntax for type {kind}: "{_as_written(value)}"')
    if not -most - 1 <= value <= most:
        raise _Refused(400, "22003", f'value "{value}" is out of range for type {kind}')
    return value


def _read_number(value: Any, digits: int) -> float:
    """numeric(digits, 1): rounded to one decimal, half away from nought."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Refused(400, "22P02", f'invalid input syntax for type numeric: "{_as_written(value)}"')
    exact = Decimal(str(value))
    if abs(exact) < 10**digits:  # (a far larger one cannot even be rounded here)
        exact = exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if abs(exact) >= 10 ** (digits - 1):
        raise _Refused(
            400,
            "22003",
            "numeric field overflow",
            f"A field with precision {digits}, scale 1 must round to an absolute value less than 10^{digits - 1}.",
        )
    return float(exact)


def _read_text(value: Any) -> str:
    return _as_written(value)


def _typed(row: dict[str, Any]) -> dict[str, Any]:
    """One row of the body, read into the columns' types. Nothing is checked yet but the types."""
    new: dict[str, Any] = {}
    for name in ROW_KEYS:
        value = row[name]
        if value is None:
            new[name] = None
        elif name == "id":
            if not isinstance(value, str) or _UUID.fullmatch(value) is None:
                raise _Refused(400, "22P02", f'invalid input syntax for type uuid: "{_as_written(value)}"')
            new[name] = str(uuid.UUID(value))
        elif name in ("started_at", "ended_at"):
            new[name] = _read_time(value)
        elif name == "local_day":
            new[name] = _read_day(value)
        elif name == "utc_offset_min":
            new[name] = _read_whole(value, "smallint", 32767)
        elif name == "steps":
            new[name] = _read_whole(value, "integer", 2147483647)
        elif name == "distance_m":
            new[name] = _read_number(value, 8)
        elif name in ("seconds", "kcal", "kcal_extra"):
            new[name] = _read_number(value, 7)
        else:
            new[name] = _read_text(value)
    return new


def _within(value: Any, low: float, high: float) -> bool:
    return value is None or low <= value <= high


def _one_of(value: Any, *words: str) -> bool:
    return value is None or value in words


def _tenths(seconds: float) -> int:
    return round(seconds * 10)  # it has one decimal by now, so this is exact


def _tenths_between(start: datetime, end: datetime) -> float:
    # Whole microseconds, so that "a minute longer, to the dot" is not at the mercy of a float.
    return (end - start) // timedelta(microseconds=1) / 100000


# The table's rules, in the order Postgres tries them: by name. A rule that
# names one column is called walks_<column>_check, one that names several is
# walks_check, walks_check1, ... in the order they are written.
_CHECKS: tuple[tuple[str, Callable[[dict[str, Any]], bool]], ...] = (
    ("walks_calories_source_check", lambda new: _one_of(new["calories_source"], "speed", "heart_rate")),
    ("walks_check", lambda new: new["ended_at"] >= new["started_at"]),
    ("walks_check1", lambda new: new["ended_at"] - new["started_at"] <= _TWO_DAYS),
    ("walks_check2", lambda new: _tenths(new["seconds"]) <= _tenths_between(new["started_at"], new["ended_at"]) + 600),
    (
        "walks_check3",  # in days counted from year 1, so that the first day there is has a day before it
        lambda new: (
            new["started_at"].date().toordinal() - 1
            <= new["local_day"].toordinal()
            <= new["ended_at"].date().toordinal() + 1
        ),
    ),
    ("walks_client_id_check", lambda new: 1 <= len(new["client_id"]) <= 64),
    ("walks_distance_m_check", lambda new: _within(new["distance_m"], 0, 200000)),
    ("walks_distance_source_check", lambda new: _one_of(new["distance_source"], "treadmill", "speed")),
    ("walks_kcal_check", lambda new: _within(new["kcal"], 0, 20000)),
    ("walks_kcal_extra_check", lambda new: _within(new["kcal_extra"], 0, 20000)),
    ("walks_origin_check", lambda new: _one_of(new["origin"], "walka", "android", "web", "file")),
    ("walks_seconds_check", lambda new: _within(new["seconds"], 0, 86400)),
    ("walks_session_check", lambda new: _one_of(new["session"], "wow", "manual", "free")),
    ("walks_started_at_check", lambda new: new["started_at"] >= _YEAR_2000),
    ("walks_steps_check", lambda new: _within(new["steps"], 0, 400000)),
    ("walks_steps_source_check", lambda new: _one_of(new["steps_source"], "treadmill", "estimate")),
    ("walks_utc_offset_min_check", lambda new: _within(new["utc_offset_min"], -720, 840)),
)


def _stored(new: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    """The row as the table keeps it, in the text the database answers with."""
    row: dict[str, Any] = dict.fromkeys(COLUMNS)
    row.update(new)
    row["user_id"] = user_id
    for name in ("started_at", "ended_at"):
        row[name] = _db_time(new[name]) if new[name] is not None else None
    row["local_day"] = new["local_day"].isoformat() if new["local_day"] is not None else None
    row["created_at"] = row["updated_at"] = _db_time(now)
    row["start_key"] = _key_text(new["started_at"]) if new["started_at"] is not None else None
    return row


def _failing_row(new: dict[str, Any], user_id: str, now: datetime) -> str:
    """Postgres shows the WHOLE row with a refusal - the account's id among it. It must never be kept."""
    row = _stored(new, user_id, now)
    shown = ", ".join("null" if row[name] is None else str(row[name]) for name in COLUMNS)
    return f"Failing row contains ({shown})."


class FakeAccounts:
    """The accounts, on this computer, for one test."""

    key = "test-key"
    schema: int = 1
    full_at: int = 20000
    access_life_s: int = 3600
    rest_grace_s: float = 30.0
    reuse_window_s: float = 10.0

    def __init__(self, *, wall: Callable[[], float]) -> None:
        self._wall = wall
        self.origin = ""
        self.requests: list[Seen] = []
        self.violations: list[str] = []
        self._closed = False
        _EVERYWHERE.append(self)  # for collect_violations(); what a test clears itself is not found there
        self.landed: list[list[tuple[str, str]]] = []  # sign-ins that came back to "the website" instead
        self.sent_to: list[str] = []  # where the browser was sent, newest last (the code is in it)
        self._server: TestServer | None = None
        self._users: dict[str, _User] = {}
        self._walks: list[dict[str, Any]] = []
        self._access: dict[str, dict[str, Any]] = {}  # access token -> user, sign-in, when it runs out
        self._sign_ins: dict[str, _SignIn] = {}
        self._refresh: dict[str, str] = {}  # refresh token -> its sign-in
        self._flows: dict[str, _Flow] = {}  # one-time code -> the sign-in begun in the browser
        self._browser_user: Any = _UNSET
        self._plans: list[_Plan] = []
        self._gates: list[Gate] = []
        self._issued: list[str] = []

    # ---- starting and stopping -----------------------------------------------------

    async def start(self) -> None:
        if self._server is not None:
            return
        if self._closed:  # started again after it was closed: a fixture must hear of it again
            self._closed = False
            if not any(fake is self for fake in _EVERYWHERE):
                _EVERYWHERE.append(self)
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{anything:.*}", self._handle)
        self._server = TestServer(app, host="127.0.0.1")
        # No access log: a line of it holds the whole address, code and all.
        await self._server.start_server(access_log=None, shutdown_timeout=0.2)
        self.origin = f"http://127.0.0.1:{self._server.port}"

    async def close(self) -> None:
        server, self._server = self._server, None
        self._closed = True
        for gate in self._gates:
            gate.drop()  # nobody waits for ever on a stand-in that is gone
        self._gates.clear()
        self._plans.clear()
        if server is not None:
            await server.close()

    async def __aenter__(self) -> FakeAccounts:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ---- what a test sets up ------------------------------------------------------

    def add_user(
        self,
        email: str,
        *,
        way: str = "github",
        adult: bool = True,
        public: bool = False,
        handle: str | None = None,
        show_days: bool = False,
        show_walks: bool = False,
        profile_row: bool | None = None,
    ) -> str:
        """A person who can sign in. Gives the account's id.

        ``adult=False`` alone is a sign-in that has never been to the
        website: no row in "profiles" at all. ``profile_row=True`` with it
        is a row whose first step is not taken.
        """
        user_id = str(uuid.uuid4())
        has_row = profile_row
        if has_row is None:
            has_row = adult or public or handle is not None or show_days or show_walks
        profile = None
        if has_row:
            profile = {
                "handle": handle,
                "is_public": public,
                "show_days": show_days,
                "show_walks": show_walks,
                "adult_confirmed_at": _db_time(self._now()) if adult else None,
            }
        self._users[user_id] = _User(user_id, email, way, profile)
        return user_id

    def take_first_step(self, user_id: str) -> None:
        """The person ticks "I am 18 or older" on the website."""
        user = self._users[user_id]
        if user.profile is None:
            user.profile = {"handle": None, "is_public": False, "show_days": False, "show_walks": False}
        user.profile["adult_confirmed_at"] = _db_time(self._now())

    def set_page(self, user_id: str, **changes: Any) -> None:
        """Change handle, is_public, show_days or show_walks, as the website would."""
        profile = self._users[user_id].profile
        assert profile is not None, "this account has no row in profiles"
        assert set(changes) <= {"handle", "is_public", "show_days", "show_walks"}, changes
        profile.update(changes)

    def delete_user(self, user_id: str) -> None:
        """The account is deleted: its rows and sign-ins go. Access tokens given out run until they run out."""
        self._users.pop(user_id, None)
        self._walks = [walk for walk in self._walks if walk["user_id"] != user_id]
        self.sign_out_everywhere(user_id)

    def sign_out_everywhere(self, user_id: str) -> None:
        """Every sign-in of the account ends. Access tokens given out run until they run out."""
        for sign_in_id in [name for name, sign_in in self._sign_ins.items() if sign_in.user == user_id]:
            self._end_sign_in(sign_in_id)

    def new_sign_in(self, user_id: str) -> dict[str, Any]:
        """A sign-in without the browser: what the exchange would have answered."""
        return self._token_answer(self._begin_sign_in(user_id), from_browser=True)

    def walks(self, user_id: str) -> list[dict[str, Any]]:
        """The account's rows as the table keeps them (with start_key and deleted_at), oldest first."""
        return [dict(walk) for walk in self._walks if walk["user_id"] == user_id]

    def mark_deleted(self, user_id: str, row_id: str) -> None:
        """The person deletes a walk in the account: the row is emptied and stays, as a marker."""
        for walk in self._walks:
            if walk["user_id"] == user_id and walk["id"] == row_id:
                walk.update(seconds=0.0, distance_m=0.0, steps=0, kcal=0.0, kcal_extra=None)
                walk.update(session=None, steps_source=None, distance_source=None, calories_source=None, device=None)
                walk["deleted_at"] = walk["updated_at"] = _db_time(self._now())
                return
        raise KeyError(row_id)

    def add_walk(self, user_id: str, started_at: str, *, deleted: bool = False) -> str:
        """A walk that is in the account already (sent from the website, say). Gives the row's id."""
        start = _read_time(started_at)
        new = dict.fromkeys(ROW_KEYS)
        new.update(id=str(uuid.uuid4()), origin="web", client_id="made-up", started_at=start, ended_at=start)
        new.update(local_day=start.date(), seconds=0.0, distance_m=0.0, steps=0, kcal=0.0)
        self._walks.append(_stored(new, user_id, self._now()))
        if deleted:
            self.mark_deleted(user_id, new["id"])
        return new["id"]

    def fill(self, user_id: str, count: int) -> None:
        """Put ``count`` small walks into the account, quickly: for a full one."""
        first = datetime(2001, 1, 1, tzinfo=_UTC) + timedelta(seconds=len(self._walks))
        now = self._now()
        for number in range(count):
            start = first + timedelta(seconds=number)
            new = dict.fromkeys(ROW_KEYS)
            new.update(id=str(uuid.uuid4()), origin="web", client_id="made-up", started_at=start, ended_at=start)
            new.update(local_day=start.date(), seconds=0.0, distance_m=0.0, steps=0, kcal=0.0)
            self._walks.append(_stored(new, user_id, now))

    def next_browser_user(self, user_id: str | None) -> None:
        """Who signs in when the browser comes to /authorize, from now on. None: the person says no.

        Never called: the user added last. An id that was never added is a
        slip of the test's, and is said at once. (An account deleted AFTER
        this was called is a person who says no.)
        """
        if user_id is not None and user_id not in self._users:
            raise KeyError(user_id)
        self._browser_user = user_id

    def expire_access_tokens(self) -> None:
        """Every access token given out so far has run out, an hour ago."""
        for held in self._access.values():
            held["until"] = self._wall() - 3600.0

    def secrets_issued(self) -> list[str]:
        """Every key given out so far: codes, access and refresh tokens, the other site's tokens."""
        return list(self._issued)

    def fail_next(
        self,
        path_prefix: str,
        status: int,
        body: Any | bytes,
        *,
        times: int = 1,
        headers: dict[str, str] | None = None,
        method: str | None = None,
        after: bool = False,
    ) -> None:
        """The next ``times`` requests to such a path get this answer instead of the real one.

        ``body``: bytes go out as they are (a gateway's own page), None is
        no body, anything else is written as JSON. With ``after=True`` the
        work is done first - the rows are added - and only the answer is
        replaced: an answer that was lost on its way.
        """
        if times >= 1:
            self._plans.append(_Plan("answer", path_prefix, method, after, times, status, body, headers))

    def drop_next(self, path_prefix: str, *, times: int = 1, method: str | None = None, after: bool = False) -> None:
        """The next ``times`` requests to such a path get no answer: the line is cut.

        A client that keeps a line open for the next request (aiohttp's
        plain session) asks a GET once more by itself when the line is cut:
        that second try is then answered. Walka opens a line for each
        request, and a POST is never asked twice.
        """
        if times >= 1:
            self._plans.append(_Plan("drop", path_prefix, method, after, times))

    def hold(self, path_prefix: str, *, method: str | None = None, after: bool = False) -> Gate:
        """The next request to such a path waits until ``gate.release()``.

        It waits BEFORE anything is done (``after=True``: the work is done,
        the answer waits). If whoever asked goes away meanwhile, the request
        is dropped where it stands and ``gate.left`` is set. (aiohttp's test
        server tells a handler so; its first 3.9 releases did not.)
        """
        gate = Gate()
        self._gates.append(gate)
        self._plans.append(_Plan("hold", path_prefix, method, after, 1, gate=gate))
        return gate

    # ---- the browser, for tests of the sign-in -----------------------------------

    async def authorize(self, url: str, *, timeout_s: float = 20.0) -> str:
        """Open the sign-in address as a browser would, and say where it is sent next. Nothing is followed."""
        if not url.startswith(self.origin + "/auth/v1/authorize?"):
            # The one address Walka may open in a browser by itself.
            self.violations.append("the browser was opened at an address that is not the accounts' /auth/v1/authorize")
            raise AssertionError("the browser was opened at an address that is not the accounts' /auth/v1/authorize")
        status, headers, text = await self.visit(url, timeout_s=timeout_s)
        where = headers.get("Location")
        if status != 302 or where is None:
            raise AssertionError(f"the accounts did not send the browser on: {status} {text}")
        self.sent_to.append(where)
        return where

    async def visit(self, url: str, *, method: str = "GET", timeout_s: float = 20.0) -> tuple[int, dict[str, str], str]:
        """One request as a browser makes it, redirects not followed: (status, headers, text)."""
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout, cookie_jar=aiohttp.DummyCookieJar()) as session:
            async with session.request(method, url, allow_redirects=False) as answer:
                return answer.status, dict(answer.headers), await answer.text()

    async def browser(self, url: str, *, timeout_s: float = 20.0) -> tuple[int, dict[str, str], str]:
        """What a browser does with the sign-in address: ask it, go where it is sent, show that page.

        Gives (status, headers, text) of the page it ends on - the
        program's own listener, or "the website" if the return address was
        not allowed.
        """
        where = await self.authorize(url, timeout_s=timeout_s)
        return await self.visit(where, timeout_s=timeout_s)

    # ---- every request -------------------------------------------------------------

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        try:
            return await self._answer(request)
        except Exception as error:
            # A mistake in the stand-in itself would be a 500, and a program
            # takes a 500 for "try again later": the test would pass. So it
            # is written down like anything else that must not happen.
            self.violations.append(f"{request.method} {request.path}: the stand-in itself failed, {error!r}")
            raise

    async def _answer(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        body, is_json = None, False
        if raw:
            try:
                body, is_json = json.loads(raw.decode("utf-8"), parse_constant=_no_constants), True
            except (ValueError, RecursionError):
                pass
        seen = Seen(
            request.method,
            request.path,
            list(request.query.items()),
            dict(request.headers.items()),
            body,
            raw,
        )
        self.requests.append(seen)
        what = _ROUTES.get((seen.method, seen.path))
        twice = sorted({name.lower() for name in request.headers if len(request.headers.getall(name)) > 1})
        faults, answer = self._faults(what, seen, bool(raw), is_json, twice)
        for fault in faults:
            self.violations.append(f"{seen.method} {seen.path}: {fault}")
        if faults:
            # ("answer or ..." would not do: an answer is an empty mapping, and so "false", to older aiohttp.)
            if answer is None:
                answer = _rest_error(400, "WALKA0", "This is not in the list of what Walka may ask: " + faults[0])
            return answer

        planned = await self._planned(request, seen, after=False)
        if planned is not None:
            return planned
        if what not in _BROWSER:
            # The gateway wants the project's key on everything a program asks (item 3).
            sent = seen.header("apikey")
            if sent is None:
                hint = "No `apikey` request header or url param was found."
                return _json(401, {"message": "No API key found in request", "hint": hint})
            if sent != self.key:
                hint = "Double check your Supabase `anon` or `service_role` API key."
                return _json(401, {"message": "Invalid API key", "hint": hint})
        try:
            answer = getattr(self, "_do_" + str(what))(seen)
        except _Refused as refusal:
            answer = _json(refusal.status, refusal.body, refusal.headers)
        planned = await self._planned(request, seen, after=True)
        return planned if planned is not None else answer

    async def _planned(self, request: web.Request, seen: Seen, *, after: bool) -> web.StreamResponse | None:
        """What a test asked for with hold(), fail_next() or drop_next(), if it is this request's turn."""
        for kinds in (("hold",), ("answer", "drop")):
            plan = self._take(seen, kinds, after)
            if plan is None:
                continue
            if plan.kind == "hold":
                assert plan.gate is not None
                plan.gate.arrived.set()
                try:
                    await plan.gate._go.wait()
                except asyncio.CancelledError:
                    plan.gate.left.set()
                    raise
                if not plan.gate._drop:
                    continue
            if plan.kind in ("hold", "drop"):
                if request.transport is not None:
                    request.transport.abort()
                return web.Response(status=503)
            if isinstance(plan.body, bytes):
                headers = {"Content-Type": "text/html; charset=utf-8"}
                headers.update(plan.headers or {})
                return web.Response(status=plan.status, body=plan.body, headers=headers)
            if plan.body is None:
                return web.Response(status=plan.status, headers=plan.headers)
            return _json(plan.status, plan.body, plan.headers)
        return None

    def _take(self, seen: Seen, kinds: tuple[str, ...], after: bool) -> _Plan | None:
        for plan in self._plans:
            if plan.kind not in kinds or plan.after != after or not seen.path.startswith(plan.prefix):
                continue
            if plan.method is not None and plan.method.upper() != seen.method:
                continue
            plan.times -= 1
            if plan.times <= 0:
                self._plans.remove(plan)
            return plan
        return None

    # ---- the list of brief 5.4 -----------------------------------------------------

    def _faults(
        self, what: str | None, seen: Seen, has_body: bool, is_json: bool, twice: list[str]
    ) -> tuple[list[str], web.Response | None]:
        """Everything about this request that is outside what Walka may send. Empty: it is on the list."""
        if what is None:
            return [f"{seen.method} on this path is not in the list of brief 5.4"], None
        if what == "landed":
            return [], None  # "the website": not Walka's request at all
        if what == "authorize":
            # The browser's own request: its headers are its own business. The address is Walka's.
            return self._query_faults(what, seen), None
        faults: list[str] = []
        names = {name.lower(): value for name, value in seen.headers.items()}
        for name in sorted(set(names) - HEADERS):
            faults.append(f"the header {name} is not one Walka sends")
        for name in twice:  # (Seen keeps one value for a name: the second would go unseen)
            faults.append(f"the header {name} is there twice")
        if _AGENT.fullmatch(names.get("user-agent", "")) is None:
            faults.append("User-Agent is not Walka/<version> and nothing else")
        if what == "token" and "authorization" in names:
            faults.append("an Authorization on the exchange of keys")
        if what == "push":
            if names.get("prefer") != PREFER:
                faults.append("Prefer is not exactly " + PREFER)
        elif "prefer" in names:
            faults.append("a Prefer where none belongs")
        kind = names.get("content-type", "").split(";")[0].strip().lower()
        if has_body and kind not in ("", "application/json"):
            faults.append("the body is not sent as application/json")
        faults += self._query_faults(what, seen)
        more, answer = self._body_faults(what, seen, has_body, is_json)
        return faults + more, answer

    def _query_faults(self, what: str, seen: Seen) -> list[str]:
        query = dict(seen.query)
        if len(query) != len(seen.query):
            return ["a key of the query is there twice"]
        wanted: dict[str, Callable[[str], bool]]
        if what == "authorize":
            wanted = {
                "provider": lambda value: value in WAYS,
                "redirect_to": lambda value: True,  # a wrong one "lands on the website", as it really does
                "code_challenge": lambda value: _CHALLENGE.fullmatch(value) is not None,
                "code_challenge_method": lambda value: value == "s256",
            }
        elif what == "token":
            wanted = {"grant_type": lambda value: value in ("pkce", "refresh_token")}
        elif what == "logout":
            # Without it EVERY sign-in of the account would end (item 10).
            wanted = {"scope": lambda value: value == "local"}
        elif what == "profiles":
            wanted = {"select": lambda value: value in (SELECT_FIRST_STEP, SELECT_PAGE)}
        elif what == "push":
            wanted = {"on_conflict": lambda value: value == ON_CONFLICT, "select": lambda value: value == "id"}
        elif what == "find":
            wanted = {
                "start_key": lambda value: _IN.fullmatch(value) is not None,
                "select": lambda value: value == SELECT_FIND,
            }
        else:
            wanted = {}
        faults = [f"the query's {name} is not on the list" for name in sorted(set(query) - set(wanted))]
        faults += [f"the query has no {name}" for name in sorted(set(wanted) - set(query))]
        for name, fits in wanted.items():
            if name in query and not fits(query[name]):
                faults.append(f"the query's {name} has a value that is not on the list")
        return faults

    def _body_faults(
        self, what: str, seen: Seen, has_body: bool, is_json: bool
    ) -> tuple[list[str], web.Response | None]:
        body = seen.json
        if seen.method == "GET":
            return (["a body on a GET"] if has_body else []), None
        if what == "logout":
            return ([] if not has_body or body == {} else ["a body on signing out"]), None
        if not is_json:
            return ["the body is not JSON"], _rest_error(400, "PGRST102", "Empty or invalid json")
        if what == "schema_version":
            return ([] if body == {} else ["the body of schema_version is not {}"]), None
        if what == "token":
            grant = dict(seen.query).get("grant_type")
            keys = {"auth_code", "code_verifier"} if grant == "pkce" else {"refresh_token"}
            if not isinstance(body, dict) or set(body) != keys:
                return [f"the body's keys are not exactly {sorted(keys)}"], None
            if not all(isinstance(value, str) for value in body.values()):
                return ["a key in the body is not text"], None
            return [], None
        # what == "push"
        if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
            return ["the body is not a list of rows"], None
        if not 1 <= len(body) <= MOST_ROWS:
            return [f"{len(body)} rows in one request (1 to {MOST_ROWS} may go)"], None
        if any(set(row) != set(body[0]) for row in body):
            fault = "the rows do not all have the same keys"
            return [fault], _rest_error(400, "PGRST102", "All object keys must match")
        keys = set(body[0])
        if keys == set(ROW_KEYS):
            return [], None
        fault = (
            f"a row's keys are not exactly the 16: too many {sorted(keys - set(ROW_KEYS))}, "
            f"missing {sorted(set(ROW_KEYS) - keys)}"
        )
        if "start_key" in keys:
            message = 'cannot insert a non-DEFAULT value into column "start_key"'
            return [fault], _rest_error(400, "428C9", message, 'Column "start_key" is a generated column.')
        unknown = sorted(keys - set(COLUMNS))
        if unknown:
            message = f"Could not find the '{unknown[0]}' column of 'walks' in the schema cache"
            return [fault], _rest_error(400, "PGRST204", message)
        return [fault], None

    # ---- the sign-in service -------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._wall(), _UTC)

    def _mint(self, text: str) -> str:
        self._issued.append(text)
        return text

    def _begin_sign_in(self, user_id: str) -> str:
        sign_in_id = str(uuid.uuid4())
        first = self._new_refresh_token(sign_in_id)
        self._sign_ins[sign_in_id] = _SignIn(user_id, [first], {})
        return sign_in_id

    def _end_sign_in(self, sign_in_id: str) -> None:
        sign_in = self._sign_ins.pop(sign_in_id, None)
        if sign_in is not None:
            for token in sign_in.tokens:
                self._refresh.pop(token, None)

    def _new_refresh_token(self, sign_in_id: str) -> str:
        # Twelve characters, as the real ones are (item 6).
        token = self._mint("".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(12)))
        self._refresh[token] = sign_in_id
        return token

    def _user_json(self, user: _User) -> dict[str, Any]:
        # More than Walka may keep: of all this, only the id and the e-mail address are its business.
        return {
            "id": user.id,
            "aud": "authenticated",
            "role": "authenticated",
            "email": user.email,
            "email_confirmed_at": "2026-11-20T10:00:00.000000Z",
            "phone": "",
            "confirmed_at": "2026-11-20T10:00:00.000000Z",
            "last_sign_in_at": "2026-11-20T10:00:00.000000Z",
            "app_metadata": {"provider": user.way, "providers": [user.way]},
            "user_metadata": {
                "avatar_url": f"https://pictures.example/{user.id}.png",
                "email": user.email,
                "email_verified": True,
                "full_name": "Somebody's Real Name",
                "provider_id": "1234567890",
            },
            "identities": [],
            "created_at": "2026-11-20T10:00:00.000000Z",
            "updated_at": "2026-11-20T10:00:00.000000Z",
            "is_anonymous": False,
        }

    def _token_answer(self, sign_in_id: str, *, from_browser: bool = False) -> dict[str, Any]:
        sign_in = self._sign_ins[sign_in_id]
        access = self._mint(secrets.token_urlsafe(48))
        until = self._wall() + self.access_life_s
        self._access[access] = {"user": sign_in.user, "sign_in": sign_in_id, "until": until}
        answer = {
            "access_token": access,
            "token_type": "bearer",
            "expires_in": self.access_life_s,
            "expires_at": int(until),
            "refresh_token": sign_in.tokens[-1],
            "user": self._user_json(self._users[sign_in.user]),
        }
        if from_browser:
            # The person's keys to Discord, GitHub or Google. Walka has no use for them (item 6).
            answer["provider_token"] = self._mint("other-site-" + secrets.token_urlsafe(24))
            answer["provider_refresh_token"] = self._mint("other-site-" + secrets.token_urlsafe(24))
        return answer

    def _do_authorize(self, seen: Seen) -> web.Response:
        query = dict(seen.query)
        back = query["redirect_to"]
        if _RETURN.fullmatch(back) is None:
            # No error is shown for an address that is not allowed: the
            # browser simply lands on the website, code and all.
            back = self.origin + "/landed"
        who = self._browser_user
        if who is _UNSET:
            who = next(reversed(self._users), None)
        if who is None or who not in self._users:
            said = {"error": "access_denied", "error_description": DENIED_DESCRIPTION}
        else:
            code = self._mint(str(uuid.uuid4()))
            self._flows[code] = _Flow(query["code_challenge"], who)
            said = {"code": code}
        return web.Response(status=302, headers={"Location": back + "?" + urlencode(said)})

    def _do_landed(self, seen: Seen) -> web.Response:
        self.landed.append(seen.query)
        return web.Response(text="The website's account page.")

    def _do_token(self, seen: Seen) -> web.Response:
        if dict(seen.query)["grant_type"] == "pkce":
            return self._exchange(seen.json["auth_code"], seen.json["code_verifier"])
        return self._refresh_keys(seen.json["refresh_token"])

    def _exchange(self, code: str, verifier: str) -> web.Response:
        flow = self._flows.get(code)
        if flow is None or flow.user not in self._users:
            return _auth_error(404, "flow_state_not_found", "invalid flow state, no valid flow state found")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("ascii")
        if challenge.rstrip("=") != flow.challenge:
            # The code stays good (item 7): somebody else's guess must not spend it.
            return _auth_error(400, "bad_code_verifier", "code challenge does not match previously saved code verifier")
        del self._flows[code]
        return _json(200, self._token_answer(self._begin_sign_in(flow.user), from_browser=True))

    def _refresh_keys(self, token: str) -> web.Response:
        sign_in_id = self._refresh.get(token)
        sign_in = self._sign_ins.get(sign_in_id or "")
        if sign_in_id is None or sign_in is None:
            return _auth_error(400, "refresh_token_not_found", "Invalid Refresh Token: Refresh Token Not Found")
        used = _auth_error(400, "refresh_token_already_used", "Invalid Refresh Token: Already Used")
        if sign_in.revoked:
            return used
        if token == sign_in.tokens[-1]:
            sign_in.changed[token] = self._wall()
            sign_in.tokens.append(self._new_refresh_token(sign_in_id))
        elif token != sign_in.tokens[-2] and self._wall() - sign_in.changed[token] >= self.reuse_window_s:
            # An old key used again, later: the whole sign-in ends, its newest key too (item 9).
            sign_in.revoked = True
            return used
        return _json(200, self._token_answer(sign_in_id))

    def _auth_bearer(self, seen: Seen) -> dict[str, Any] | web.Response:
        """Whose access token this is, for the sign-in service - which gives no half minute of grace."""
        sent = seen.header("Authorization") or ""
        if not sent.startswith("Bearer "):
            return _auth_error(401, "no_authorization", "This endpoint requires a valid Bearer token")
        held = self._access.get(sent[len("Bearer ") :])
        if held is None:
            return _auth_error(403, "bad_jwt", "invalid JWT: unable to parse or verify signature, token is malformed")
        if self._wall() >= held["until"]:
            words = "invalid JWT: unable to parse or verify signature, token has invalid claims: token is expired"
            return _auth_error(403, "bad_jwt", words)
        if held["user"] not in self._users:
            return _auth_error(403, "user_not_found", "User from sub claim in JWT does not exist")
        if held["sign_in"] not in self._sign_ins:
            return _auth_error(403, "session_not_found", "Session from session_id claim in JWT does not exist")
        return held

    def _do_logout(self, seen: Seen) -> web.Response:
        held = self._auth_bearer(seen)
        if isinstance(held, web.Response):
            return held
        self._end_sign_in(held["sign_in"])  # this sign-in only; its access tokens run until they run out
        return web.Response(status=204)

    def _do_user(self, seen: Seen) -> web.Response:
        held = self._auth_bearer(seen)
        if isinstance(held, web.Response):
            return held
        return _json(200, self._user_json(self._users[held["user"]]))

    # ---- the database --------------------------------------------------------------

    def _rest_bearer(self, seen: Seen, table: str | None) -> str | None:
        """The account's id for this request; None for a visitor where a visitor may ask. Else it raises."""
        sent = seen.header("Authorization")
        token = sent[len("Bearer ") :] if sent is not None and sent.startswith("Bearer ") else None
        if sent is None or token == self.key:
            if table is None:
                return None
            raise _Refused(401, "42501", f"permission denied for table {table}")
        held = self._access.get(token or "")
        if held is None:
            raise _Refused(401, "PGRST301", "No suitable key or wrong key type")
        if self._wall() >= held["until"] + self.rest_grace_s:
            raise _JwtExpired()
        return held["user"]

    def _do_schema_version(self, seen: Seen) -> web.Response:
        self._rest_bearer(seen, None)  # works for a visitor too (item 26); a token that ran out is still refused
        return _json(200, self.schema)

    def _do_profiles(self, seen: Seen) -> web.Response:
        user = self._users.get(self._rest_bearer(seen, "profiles") or "")
        if user is None or user.profile is None:
            return _json(200, [])  # a new account and a deleted one read the same (items 12, 27)
        return _json(200, [{name: user.profile[name] for name in dict(seen.query)["select"].split(",")}])

    def _do_find(self, seen: Seen) -> web.Response:
        user_id = self._rest_bearer(seen, "walks")
        inner = _IN.fullmatch(dict(seen.query)["start_key"]).group(1)
        wanted = set()
        for item in (part.strip().strip('"') for part in inner.split(",") if part.strip()):
            # The column has no zone, so one written on the value is ignored (item 15).
            when = _read_time(item, "timestamp without time zone", keep_zone=False)
            if when.microsecond == 0:
                wanted.add(_key_text(when))
        rows = [walk for walk in self._walks if walk["user_id"] == user_id and walk["start_key"] in wanted]
        return _json(200, [{name: walk[name] for name in SELECT_FIND.split(",")} for walk in rows])

    def _do_push(self, seen: Seen) -> web.Response:
        user_id = self._rest_bearer(seen, "walks")
        assert user_id is not None
        now = self._now()
        # 1. The whole body is read into rows before any row is put in: a
        #    number that is not one is refused before anything else is asked.
        rows = [_typed(row) for row in seen.json]
        user = self._users.get(user_id)
        adult = user is not None and user.profile is not None and user.profile["adult_confirmed_at"] is not None
        starts = {walk["start_key"] for walk in self._walks if walk["user_id"] == user_id}
        ids = {walk["id"] for walk in self._walks}  # of every account: an id is new in the whole table
        added: list[dict[str, Any]] = []
        # 2. Row by row, in the order sent; the first refusal refuses the batch whole.
        for new in rows:
            if not adult:  # the trigger walks_adult
                raise _Refused(400, "23514", ADULTS)
            if new["started_at"] is not None and new["started_at"] > now + _TWO_DAYS:  # walks_time
                raise _Refused(400, "23514", DAYS_TO_COME)
            for name in REQUIRED:
                if new[name] is None:
                    message = f'null value in column "{name}" of relation "walks" violates not-null constraint'
                    raise _Refused(400, "23502", message, _failing_row(new, user_id, now))
            for rule, holds in _CHECKS:
                if not holds(new):
                    message = f'new row for relation "walks" violates check constraint "{rule}"'
                    raise _Refused(400, "23514", message, _failing_row(new, user_id, now))
            key = _key_text(new["started_at"])
            if key in starts:
                continue  # there already, or deleted there: it does not come back, whatever its id
            if new["id"] in ids:
                message = 'duplicate key value violates unique constraint "walks_pkey"'
                raise _Refused(409, "23505", message, f"Key (id)=({new['id']}) already exists.")
            starts.add(key)
            ids.add(new["id"])
            added.append(_stored(new, user_id, now))
        # 3. Once for the request, and only if something was added: is the account over its room?
        #    Deleted walks count. Walks that were there already can still be sent to a full account.
        if added and len(added) + sum(1 for walk in self._walks if walk["user_id"] == user_id) > self.full_at:
            raise _Refused(400, "23514", FULL.format(self.full_at))
        self._walks.extend(added)
        return _json(201, [{"id": walk["id"]} for walk in added])


class _JwtExpired(_Refused):
    """An access token that has run out (item 2)."""

    def __init__(self) -> None:
        said = 'Bearer error="invalid_token", error_description="JWT expired"'
        super().__init__(401, "PGRST303", "JWT expired", headers={"WWW-Authenticate": said})
