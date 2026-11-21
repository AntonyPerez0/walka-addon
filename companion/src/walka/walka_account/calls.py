"""Everything Walka asks of the accounts, and what each answer means.

This is the only module that names a path of the accounts. There is one
function for each kind of request Walka may make at all, with its method,
path, query and ``Prefer`` written down here and nowhere else:

- ``browser_address`` GET  /auth/v1/authorize - asked by the person's browser, not by Walka
- ``exchange``        POST /auth/v1/token?grant_type=pkce
- ``refresh``         POST /auth/v1/token?grant_type=refresh_token
- ``logout``          POST /auth/v1/logout?scope=local
- ``who``             GET  /auth/v1/user
- ``schema_version``  POST /rest/v1/rpc/schema_version
- ``first_step``      GET  /rest/v1/profiles?select=adult_confirmed_at
- ``page``            GET  /rest/v1/profiles?select=handle,is_public,show_days,show_walks
- ``push``            POST /rest/v1/walks?on_conflict=user_id,start_key&select=id
- ``find``            GET  /rest/v1/walks?start_key=in.(...)&select=id,start_key,deleted_at

There is no PATCH and no DELETE, and nothing here writes to a table
other than the walks: the accounts' key could tick "I am 18 or older" or
make a page public, so this short list is what stands in the way.

Whatever goes wrong comes out as a ``CallError`` with one word, ``kind``,
that says what to do next. Nothing else of an answer leaves this module
unlooked at:

- of a refusal, only the accounts' own ``code`` and ``message`` are
  kept, cut short and cleaned. Never ``details`` or ``hint``: for a row
  that was refused, ``details`` holds the whole row, the account's id
  among it.
- of a sign-in, only the two tokens, how long the first is good for, the
  account's id and its e-mail address - each checked before it is handed
  on. The other site's own tokens are never read.
- nothing is written to the log here, and a ``CallError`` says its
  ``kind`` and nothing else when it is printed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .https import Answer, Client, NetError, Origin, key_is_usable, parse_origin
from .row import ROW_KEYS

# What a CallError can say. Each word is an instruction to the caller:
#   later          no answer, a 5xx or a 429: try again after a pause
#   certificate    the accounts' certificate could not be checked: nothing was asked
#   token_ran_out  the access token is too old: refresh, try once more
#   sign_in_over   the refresh was refused for good: nobody is signed in any more
#   bad_code       the browser's code was not taken: this sign-in failed
#   not_open       the account has not taken its first step - or is gone
#   full           the account has no room left
#   row            the accounts refused the rows that were sent, because of one of them
#   stop           anything else: it says nothing about any walk. Stop this run.
KINDS = (
    "later",
    "certificate",
    "token_ran_out",
    "sign_in_over",
    "bad_code",
    "not_open",
    "full",
    "row",
    "stop",
)

PREFER = "resolution=ignore-duplicates,return=representation"
MOST_ROWS = 200  # rows in one request
MOST_KEYS = 50  # starts looked up in one request
ASK_S = 20.0  # an answer is waited for this long
EXCHANGE_S = 10.0  # the browser waits while the code is exchanged
LOGOUT_S = 3.0  # a courtesy: nobody waits for it
MOST_LIFETIME_S = 3600.0  # an access token is never believed for longer

ADULTS = "Walka is for adults."
DAYS_TO_COME = "A walk cannot start in the days to come."
FULL_BEGINS = "There is room for "

# The other sites a person can sign in through.
WAYS = ("discord", "github", "google")

# The paths. Nothing outside this module names one.
_AUTHORIZE = "/auth/v1/authorize"  # the browser's: Walka itself never asks it
_TOKEN = "/auth/v1/token"
_LOGOUT = "/auth/v1/logout"
_USER = "/auth/v1/user"
_SCHEMA = "/rest/v1/rpc/schema_version"
_PROFILES = "/rest/v1/profiles"
_WALKS = "/rest/v1/walks"

_SELECT_FIRST_STEP = "adult_confirmed_at"
_SELECT_PAGE = "handle,is_public,show_days,show_walks"
_SELECT_FOUND = "id,start_key,deleted_at"
_ON_CONFLICT = "user_id,start_key"

# ([0-9], not \d: Python's \d takes digits from every alphabet.)
_A_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")
_A_CODE = re.compile(r"[A-Za-z0-9._~+/=-]{1,512}")  # what the browser brings back
_A_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_AN_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")  # one spelling for one id
_ANY_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_OWN_CODE = re.compile(r"[A-Za-z0-9_]{1,32}")
_A_START = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_A_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of a SHA-256, without "="
_A_WAY_BACK = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/[A-Za-z0-9_-]{32,128}")
# A moment as the database writes one: "2026-11-20T10:00:00.123456+00:00". (The end of it is not gone by.)
_A_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}[0-9:.+Zz-]{0,48}")

MOST_MESSAGE = 300
_MOST_READ = 4096  # of a message, before it is cleaned
_MOST_EMAIL = 254
_MOST_HANDLE = 64

# Letters that take no room on the screen - or show as a blank - but are
# not "format" characters, nor blanks, to Unicode.
_UNSEEN = frozenset(
    "\u034f\u115f\u1160\u17b4\u17b5\u180b\u180c\u180d\u180f\u2800\u3164\uffa0\U00016fe4\U0001d159"
    + "".join(map(chr, range(0xFE00, 0xFE10)))
    + "".join(map(chr, range(0xE0100, 0xE01F0)))
)

# What became of a request that got no answer -> the word for the caller.
# ("too_big" and "address" are not here: an answer longer than anything the
# accounts send, and a request the client will not make, say nothing about
# any walk and will not get better by waiting.)
_NO_ANSWER = {"certificate": "certificate", "no_answer": "later"}

_NOT_JSON: Any = object()
_MISSING: Any = object()


# ---- what is kept of an answer -------------------------------------------------------------------


def _plain(char: str) -> bool:
    """Can this character be shown? Not a control character, not one that
    turns the line around or takes no room, not a separator of lines."""
    return unicodedata.category(char)[0] != "C" and char not in _UNSEEN and char not in "\u2028\u2029"


def _own_code(code: object) -> str:
    return code if isinstance(code, str) and _OWN_CODE.fullmatch(code) else ""


def _cleaned(message: str) -> str:
    return "".join(char for char in message[:_MOST_READ] if _plain(char))


def _sentence(message: object) -> str:
    """The accounts' own message, fit to be kept and shown: short, and nothing in it but text.

    Anything shaped like an id is taken out: no message Walka knows has
    one, and the account's id must not ride into the dashboard on one.
    """
    if not isinstance(message, str):
        return ""
    return _ANY_ID.sub("...", _cleaned(message))[:MOST_MESSAGE].strip()


def _pause(seconds: object) -> float | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    return float(seconds) if 0 <= seconds <= 3600 else None


class CallError(Exception):
    """A request that did not give what was asked for. ``kind`` (one of ``KINDS``) says what to do.

    ``code`` and ``message`` are the accounts' own, cleaned, or empty.
    ``retry_after``: with "later", how long the accounts asked Walka to
    wait, in seconds, if they did. For "row" only: ``pkey`` - the row's id
    is taken, so a new one and one more try will do; ``days_to_come`` -
    this computer's clock was ahead, so the walk can go at a later start.

    Printed, it says its kind and nothing else.
    """

    def __init__(
        self,
        kind: str,
        code: str = "",
        message: str = "",
        *,
        retry_after: float | None = None,
        pkey: bool = False,
        days_to_come: bool = False,
    ) -> None:
        if kind not in KINDS:
            raise ValueError("not a kind of CallError")
        super().__init__(kind)
        self.kind = kind
        self.code = _own_code(code)
        self.message = _sentence(message)
        self.retry_after = _pause(retry_after) if kind == "later" else None
        self.pkey = kind == "row" and pkey is True
        self.days_to_come = kind == "row" and days_to_come is True


@dataclass(frozen=True)
class Signed:
    """What a sign-in gives, checked. (Shown, it tells none of it but the time.)

    ``lifetime_s`` is counted from the moment the answer came, on the
    caller's own clock: the accounts' "expires at" is by THEIR clock, and
    this computer's may be wrong. ``email`` is empty if the answer had
    none that can be shown.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    lifetime_s: float
    user_id: str = field(repr=False)
    email: str = field(repr=False)


def _is_token(text: object) -> bool:
    return isinstance(text, str) and _A_TOKEN.fullmatch(text) is not None


def _email(value: object) -> str:
    """The e-mail address, if it can be shown as what it is. It is all that tells the person WHICH account."""
    if not isinstance(value, str) or not 3 <= len(value) <= _MOST_EMAIL:
        return ""
    if "@" not in value[1:-1]:
        return ""
    if any(not _plain(char) or unicodedata.category(char)[0] == "Z" for char in value):
        return ""  # a blank, or a letter that hides or turns the others around
    return value


def _json(answer: Answer) -> Any:
    try:
        return answer.json()
    except ValueError:
        return _NOT_JSON


def _told(code: str, raw: str, keys: Iterable[object]) -> bool:
    """Do the words of a refusal hold one of the keys this request carried? (Then they are not kept:
    they would reach a file.) Looked for in the message as it came AND as it is kept: a key
    with a letter that does not show in its middle is whole again once the message is cleaned."""
    texts = (code + raw, _cleaned(raw), _sentence(raw))
    return any(isinstance(key, str) and len(key) >= 6 and key in text for key in keys for text in texts)


def _database_words(answer: Answer, keys: Iterable[object]) -> tuple[str, str, str, bool]:
    """Of a refusal by the database: its code, its message cleaned, its message as it came, and
    whether it names the rule that an id is new in the whole table. ``details`` goes no further than here."""
    said = _json(answer)
    if not isinstance(said, dict):
        return "", "", "", False
    code = _own_code(said.get("code"))
    raw = said.get("message")
    if not isinstance(raw, str):
        raw = ""
    details = said.get("details")
    pkey = "walks_pkey" in raw or (isinstance(details, str) and "walks_pkey" in details)
    if _told(code, raw, keys):
        return "", "", "", False
    return code, _sentence(raw), raw, pkey


def _service_words(answer: Answer, keys: Iterable[object]) -> tuple[str, str]:
    """Of a refusal by the sign-in service: its code and its message, cleaned.

    Only of a refusal (a 4xx). An answer that is none may be a sign-in
    that did not fit, with new keys in it: no word of that is kept.
    """
    if not 400 <= answer.status <= 499:
        return "", ""
    said = _json(answer)
    if not isinstance(said, dict):
        return "", ""
    code = _own_code(said.get("error_code"))
    raw = said.get("msg")
    if not isinstance(raw, str):
        raw = ""
    if _told(code, raw, keys):
        return "", ""
    return code, _sentence(raw)


def _later(answer: Answer) -> CallError | None:
    """The accounts are busy or broken: that says nothing of what was asked."""
    if answer.status == 429 or 500 <= answer.status <= 599:
        return CallError("later", retry_after=answer.retry_after)
    return None


def _database_refusal(answer: Answer, keys: Iterable[object], *, rows: bool = False) -> CallError:
    """What an answer of the database means that is not the one hoped for. ``rows``: walks were sent."""
    later = _later(answer)
    if later is not None:
        return later
    code, message, raw, pkey = _database_words(answer, keys)
    if answer.status == 401 and code == "PGRST303":
        return CallError("token_ran_out", code, message)
    # (Five characters: the database's own codes. PostgREST's begin with letters.)
    if rows and answer.status in (400, 409) and len(code) == 5 and code[:2] in ("22", "23"):
        if code == "23514" and raw == ADULTS:
            return CallError("not_open", code, message)
        if code == "23514" and raw.startswith(FULL_BEGINS):
            return CallError("full", code, message)
        ahead = code == "23514" and raw == DAYS_TO_COME
        return CallError("row", code, message, pkey=code == "23505" and pkey, days_to_come=ahead)
    # A request built wrongly, a bearer that is not taken, a redirect, a
    # gateway's own page, an answer of the wrong shape: none of it is about a walk.
    return CallError("stop", code, message)


def _signed(answer: Answer) -> Signed | None:
    """The sign-in in a 200 of the token calls, or ``None`` if it is not one Walka can use."""
    said = _json(answer)
    if not isinstance(said, dict):
        return None
    access, again, life, user = (said.get(name) for name in ("access_token", "refresh_token", "expires_in", "user"))
    if not _is_token(access) or not _is_token(again):
        return None
    if isinstance(life, bool) or not isinstance(life, (int, float)):
        return None
    if not isinstance(user, dict):
        return None
    user_id = user.get("id")
    if not isinstance(user_id, str) or not _AN_ID.fullmatch(user_id):
        return None
    # (Cut to size before it becomes a float: a whole number can be too large to be one.)
    lifetime_s = float(min(max(life, 0), MOST_LIFETIME_S))
    return Signed(access, again, lifetime_s, user_id, _email(user.get("email")))


# ---- asking --------------------------------------------------------------------------------------


async def _ask(
    client: Client,
    key: str,
    method: str,
    path: str,
    *,
    query: Sequence[tuple[str, str]] = (),
    bearer: object = _MISSING,
    prefer: bool = False,
    body: Any = None,
    timeout_s: float | None = None,
) -> Answer:
    """One request with the accounts' key - and the person's, where there is a bearer.

    Gives the answer whatever its status. No answer at all is a
    ``CallError``. A key that is not the accounts' publishable one, or a
    token that is not shaped like one, is never sent - and a request that
    wants a bearer is not made without one.
    """
    if not key_is_usable(key) or (bearer is not _MISSING and not _is_token(bearer)):
        raise CallError("stop")
    headers = {"apikey": key}
    if isinstance(bearer, str):
        headers["Authorization"] = "Bearer " + bearer
    if prefer:
        headers["Prefer"] = PREFER
    waits = ASK_S if timeout_s is None else timeout_s
    word = "no_answer"
    try:
        answer = await client.request(method, path, query=query, headers=headers, json_body=body, timeout_s=waits)
    except NetError as error:
        word = error.kind
    except Exception:
        # The client raises nothing else. If one day it does: an error of
        # aiohttp's carries the request's headers, so it stops here.
        pass
    else:
        if isinstance(answer, Answer):
            return answer
        word = "address"
    # Raised here, outside every "except": the error that led to it is not kept, not even out of sight.
    raise CallError(_NO_ANSWER.get(word, "stop"))


def browser_address(origin: Origin, *, way: str, back: str, challenge: str) -> str:
    """The address the person's own browser is opened at to sign in (brief 5.2, step 3).

    Not a request of Walka's: the browser asks it. ``back`` is where the
    browser is sent afterwards - this sign-in's own listener on this
    computer, ``http://127.0.0.1:<port>/<path>`` and nothing else.
    ``ValueError`` for anything that is not such a sign-in: then no browser is opened.
    """
    way_back = _A_WAY_BACK.fullmatch(back) if isinstance(back, str) else None
    fits = (
        isinstance(origin, Origin)
        and parse_origin(origin.text) == origin
        and way in WAYS
        and way_back is not None
        and int(way_back.group(1)) <= 65535
        and isinstance(challenge, str)
        and _A_CHALLENGE.fullmatch(challenge) is not None
    )
    if not fits:
        raise ValueError("not a sign-in Walka starts")
    asked = (("provider", way), ("redirect_to", back), ("code_challenge", challenge), ("code_challenge_method", "s256"))
    return origin.text + _AUTHORIZE + "?" + urlencode(asked)


async def exchange(client: Client, key: str, *, code: str, verifier: str) -> Signed:
    """The browser's one-time code for a sign-in (brief 5.2, step 5). Ten seconds at most.

    ``bad_code``: the code was not taken - it is another sign-in's, or
    used, or run out. The other site's own tokens in the answer are not read.
    """
    if not isinstance(code, str) or not _A_CODE.fullmatch(code):
        raise CallError("bad_code")  # not a code the accounts ever gave: it is not even asked
    if not isinstance(verifier, str) or not _A_VERIFIER.fullmatch(verifier):
        raise CallError("stop")
    answer = await _ask(
        client,
        key,
        "POST",
        _TOKEN,
        query=(("grant_type", "pkce"),),
        body={"auth_code": code, "code_verifier": verifier},
        timeout_s=EXCHANGE_S,
    )
    signed = _signed(answer) if answer.status == 200 else None
    if signed is not None:
        return signed
    later = _later(answer)
    if later is not None:
        raise later
    said, message = _service_words(answer, (key, code, verifier))
    raise CallError("bad_code" if 400 <= answer.status <= 499 else "stop", said, message)


async def refresh(client: Client, key: str, *, refresh_token: str) -> Signed:
    """A new access token - and a NEW refresh token: the old one is spent.

    ``sign_in_over``: the accounts know the sign-in no longer, or the
    token was used twice. Nobody is signed in any more.
    """
    if not _is_token(refresh_token):
        raise CallError("stop")
    answer = await _ask(
        client,
        key,
        "POST",
        _TOKEN,
        query=(("grant_type", "refresh_token"),),
        body={"refresh_token": refresh_token},
    )
    signed = _signed(answer) if answer.status == 200 else None
    if signed is not None:
        return signed
    later = _later(answer)
    if later is not None:
        raise later
    code, message = _service_words(answer, (key, refresh_token))
    over = 400 <= answer.status <= 499 and code in ("refresh_token_not_found", "refresh_token_already_used")
    raise CallError("sign_in_over" if over else "stop", code, message)


async def logout(client: Client, key: str, *, access_token: str) -> None:
    """Tell the accounts that this sign-in is over - this one only. A courtesy: three seconds, and
    whatever comes of it changes nothing. It never raises (a cancellation passes, as everywhere)."""
    try:
        await _ask(client, key, "POST", _LOGOUT, query=(("scope", "local"),), bearer=access_token, timeout_s=LOGOUT_S)
    except Exception:
        pass  # signing out never fails: the keys are forgotten already


async def who(client: Client, key: str, *, access_token: str) -> str:
    """Is the account still there? ``"there"``, ``"signed_out"`` (this sign-in was ended) or ``"gone"`` (deleted)."""
    answer = await _ask(client, key, "GET", _USER, bearer=access_token)
    if answer.status == 200:
        said = _json(answer)
        user_id = said.get("id") if isinstance(said, dict) else None
        if isinstance(user_id, str) and _AN_ID.fullmatch(user_id):
            return "there"  # (and nothing of the answer is kept: it holds a name and a picture)
        raise CallError("stop")
    later = _later(answer)
    if later is not None:
        raise later
    code, message = _service_words(answer, (key, access_token))
    if answer.status == 403 and code == "session_not_found":
        return "signed_out"
    if answer.status == 403 and code == "user_not_found":
        return "gone"
    raise CallError("stop", code, message)


async def schema_version(client: Client, key: str, *, access_token: str) -> int:
    """Which version of its rules the database is at. Walka knows version 1."""
    answer = await _ask(client, key, "POST", _SCHEMA, bearer=access_token, body={})
    if answer.status != 200:
        raise _database_refusal(answer, (key, access_token))
    said = _json(answer)
    if isinstance(said, bool) or not isinstance(said, int) or said < 1:
        raise CallError("stop")
    return said


async def _profile(client: Client, key: str, access_token: str, select: str) -> dict | None:
    """The account's own row in "profiles", or ``None`` if it has none. Only read, never written."""
    answer = await _ask(client, key, "GET", _PROFILES, query=(("select", select),), bearer=access_token)
    if answer.status != 200:
        raise _database_refusal(answer, (key, access_token))
    said = _json(answer)
    if not isinstance(said, list) or len(said) > 1 or not all(isinstance(row, dict) for row in said):
        raise CallError("stop")
    return said[0] if said else None


async def first_step(client: Client, key: str, *, access_token: str) -> bool | None:
    """Has the account taken its first step ("I am 18 or older"), on the website?

    ``None``: it has no row at all - a sign-in that has never been to the
    website, or an account that is gone (``who`` tells which).
    """
    row = await _profile(client, key, access_token, _SELECT_FIRST_STEP)
    if row is None:
        return None
    when = row.get(_SELECT_FIRST_STEP, _MISSING)
    if when is None or when == "":
        return False
    if isinstance(when, str) and _A_TIME.fullmatch(when):
        return True
    raise CallError("stop")  # (a word, a number: not how the database says when)


async def page(client: Client, key: str, *, access_token: str) -> dict | None:
    """Whether the account's page on the website is public, and what it shows. ``None``: no row."""
    row = await _profile(client, key, access_token, _SELECT_PAGE)
    if row is None:
        return None
    handle = row.get("handle", _MISSING)
    fits = isinstance(handle, str) and 1 <= len(handle) <= _MOST_HANDLE and all(_plain(char) for char in handle)
    if handle is not None and not fits:
        raise CallError("stop")
    shows = {name: row.get(name) for name in ("is_public", "show_days", "show_walks")}
    if any(not isinstance(value, bool) for value in shows.values()):
        raise CallError("stop")
    return {"handle": handle, **shows}


def _to_send(rows: object) -> list[dict[str, Any]] | None:
    """The rows as they go: each exactly the sixteen keys, with an id of its own. ``None``: not sent at all."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= MOST_ROWS:
        return None
    going: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or len(row) != len(ROW_KEYS) or set(row) != set(ROW_KEYS):
            return None  # a key too many would be a number the person was never told of
        if any(value is not None and not isinstance(value, (str, int, float)) for value in row.values()):
            return None  # a list or a table as a value could carry anything: a row is words and numbers
        row_id = row["id"]
        if not isinstance(row_id, str) or not _AN_ID.fullmatch(row_id) or row_id in ids:
            return None
        ids.add(row_id)
        going.append({name: row[name] for name in ROW_KEYS})
    return going


async def push(client: Client, key: str, *, access_token: str, rows: list[dict]) -> list[str]:
    """Send walks (brief 5.3): at most 200 rows, each with exactly the sixteen keys of ``row.ROW_KEYS``.

    Gives the ids of the rows that were ADDED, as they came back. A row
    that did not come back is in the account already, or was deleted
    there: it is not to be sent again (``find`` tells which).

    A ``CallError`` of kind "row" means the request was refused whole
    because of one of its rows: nothing was added.
    """
    going = _to_send(rows)
    if going is None:
        raise CallError("stop")
    answer = await _ask(
        client,
        key,
        "POST",
        _WALKS,
        query=(("on_conflict", _ON_CONFLICT), ("select", "id")),
        bearer=access_token,
        prefer=True,
        body=going,
    )
    if answer.status != 201:
        raise _database_refusal(answer, (key, access_token), rows=True)
    said = _json(answer)
    sent = {row["id"] for row in going}
    added: list[str] = []
    fits = isinstance(said, list) and len(said) <= len(going)
    for item in said if fits else ():
        row_id = item.get("id") if isinstance(item, dict) else None
        # Only an id that was sent, and once: anything else is not the answer to this request.
        if not isinstance(row_id, str) or row_id not in sent or row_id in added:
            fits = False
            break
        added.append(row_id)
    if not fits:
        raise CallError("stop")
    return added


def _found(answer: Answer, asked: set[str]) -> dict[str, tuple[str | None, bool]] | None:
    """What a look-up answered, or ``None`` if it cannot be read."""
    said = _json(answer) if answer.status == 200 else _NOT_JSON
    if not isinstance(said, list) or len(said) > len(asked):
        return None
    found: dict[str, tuple[str | None, bool]] = {}
    for item in said:
        if not isinstance(item, dict):
            return None
        row_id, start, deleted = item.get("id"), item.get("start_key"), item.get("deleted_at", _MISSING)
        if not isinstance(row_id, str) or not _AN_ID.fullmatch(row_id) or not isinstance(start, str):
            return None
        # (How the database writes this column in an answer was never recorded: with a "T", or a blank.)
        start = start[:10] + "T" + start[11:19] if start[10:11] == " " else start[:19]
        if start not in asked or start in found:
            return None
        if deleted is not None and not (isinstance(deleted, str) and _A_TIME.fullmatch(deleted)):
            return None
        found[start] = (row_id, deleted is not None)
    return found


async def find(
    client: Client, key: str, *, access_token: str, start_keys: list[str]
) -> dict[str, tuple[str | None, bool]]:
    """Which rows the account has for these starts: start (as ``row.start_key`` gives it) -> (the row's id, deleted there?).

    For walks that did not come back from ``push``. It is only told, never
    needed: on any failure, or an answer that cannot be read, it gives
    what it has so far - nothing, mostly - and asks no further. It never
    raises (a cancellation passes).

    One request for every ``MOST_KEYS`` starts, one after the other. A
    caller that has to look before each request whether sending is still
    wanted hands over no more than ``MOST_KEYS`` at a time.
    """
    found: dict[str, tuple[str | None, bool]] = {}
    try:
        # Only a start written exactly as Walka writes one goes into the
        # address: a comma or a bracket in it would be another question.
        fit = [start for start in start_keys if isinstance(start, str) and _A_START.fullmatch(start)]
        wanted = list(dict.fromkeys(fit))  # each once
        for first in range(0, len(wanted), MOST_KEYS):
            some = wanted[first : first + MOST_KEYS]
            answer = await _ask(
                client,
                key,
                "GET",
                _WALKS,
                query=(("start_key", "in.(" + ",".join(some) + ")"), ("select", _SELECT_FOUND)),
                bearer=access_token,
            )
            rows = _found(answer, set(some))
            if rows is None:
                break
            found.update(rows)
    except Exception:
        pass  # (not looked up: the walk is "there", its row unknown)
    return found
