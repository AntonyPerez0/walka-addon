"""What Walka asks of the accounts (walka_account.calls), tried against the stand-in.

Each kind of request once as it should go, then every answer the brief
(5.3) and the record of what was tried name, and answers nobody recorded
because no honest server gives them. After EVERY test three things are
looked at again: that the stand-in saw nothing outside the list of what
Walka may ask, that no error said a key, the address, the e-mail address
or the account's id, and that nothing of them stands in the log.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import inspect
import logging
import socket
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

import fake_accounts
from fake_accounts import ADULTS, DAYS_TO_COME, PREFER, FakeAccounts
from walka.walka_account import calls
from walka.walka_account.calls import CallError, Signed
from walka.walka_account.https import Answer, Client, NetError, Origin, parse_origin
from walka.walka_account.row import ROW_KEYS

NOW = 1795300000.0  # 21 November 2026, 22:26:40 UTC
START = datetime(2026, 11, 19, 20, 30, tzinfo=timezone.utc)
VERIFIER = "v" * 43  # the shortest a verifier may be
SOME_ID = "0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21"
TOKEN = "an.access-token_of~some+length/=="  # shaped like one; nobody gave it out

MADE: list[FakeAccounts] = []  # every stand-in of the test that runs
PEOPLE: list[str] = []  # e-mail addresses and ids of its accounts
CAUGHT: list[CallError] = []  # every refusal it saw
NEEDLES: list[str] = []  # whatever else must be said nowhere


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), 30))


def says(error: BaseException) -> str:
    """Everything an error tells whoever prints it, logs it or looks into it."""
    printed = "".join(traceback.format_exception_only(type(error), error))
    return " | ".join([str(error), repr(error), repr(error.args), repr(vars(error)), printed])


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    fake_accounts.collect_violations()  # what an earlier test left
    MADE.clear()
    PEOPLE.clear()
    CAUGHT.clear()
    NEEDLES.clear()
    caplog.set_level(logging.DEBUG)
    yield
    assert fake_accounts.collect_violations() == []
    needles = set(PEOPLE) | set(NEEDLES) | {TOKEN}
    for fake in MADE:
        needles |= set(fake.secrets_issued()) | {fake.key}
        if fake.origin:
            needles |= {fake.origin, fake.origin.split("//")[1]}
    for error in CAUGHT:
        assert error.__context__ is None and error.__cause__ is None  # no other error rides along, out of sight
        told = says(error)
        assert [needle for needle in needles if needle in told] == []
        assert "http" not in told and "/v1" not in told
    records = caplog.get_records("setup") + caplog.get_records("call")
    assert [record.name for record in records if record.name.startswith("walka")] == []
    # Nor did anybody else have to say that something was left lying: a task nobody waited for, an error nobody read.
    assert [record.getMessage() for record in records if record.levelno >= logging.WARNING] == []
    written = "\n".join(logging.Formatter().format(record) for record in records)
    assert [needle for needle in needles if needle in written] == []


class Clock:
    """The stand-in's clock. It moves only when a test moves it."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class Accounts:
    """The stand-in, a client that asks it, and one person with an account."""

    def __init__(self, **person: Any) -> None:
        self.clock = Clock()
        self.fake = FakeAccounts(wall=self.clock)
        MADE.append(self.fake)
        self.key = self.fake.key
        self._person = person
        self._token: str | None = None

    async def __aenter__(self) -> Accounts:
        await self.fake.start()
        origin = parse_origin(self.fake.origin)
        assert origin is not None
        self.origin = origin
        self.client = Client(origin)
        self.user = self.person(**self._person)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.fake.close()

    def person(self, email: str = "walker@mail.example", **how: Any) -> str:
        user = self.fake.add_user(email, **how)
        PEOPLE.extend([email, user])
        return user

    def sign_in(self, user: str | None = None) -> dict:
        """A sign-in without the browser: what the exchange would have answered."""
        return self.fake.new_sign_in(user or self.user)

    @property
    def token(self) -> str:
        """An access token of the person's."""
        if self._token is None:
            self._token = self.sign_in()["access_token"]
        return self._token

    async def a_code(self, verifier: str = VERIFIER) -> str:
        """The person signs in in "the browser": the one-time code it comes back with."""
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii")
        back = "http://127.0.0.1:9/" + "p" * 43
        address = calls.browser_address(self.origin, way="github", back=back, challenge=challenge.rstrip("="))
        where = await self.fake.authorize(address)
        return dict(parse_qsl(urlsplit(where).query))["code"]


class Double:
    """A client that asks nobody: it writes down what it was told to ask, and gives what the test says."""

    def __init__(self, gives: Any) -> None:
        self.gives = gives
        self.asked: list[dict] = []

    async def request(self, method, path, *, query=(), headers=None, json_body=None, timeout_s=20.0):
        self.asked.append(
            {
                "method": method,
                "path": path,
                "query": list(query),
                "headers": dict(headers or {}),
                "body": json_body,
                "timeout_s": timeout_s,
            }
        )
        if isinstance(self.gives, BaseException):
            raise self.gives
        return self.gives


async def refused(asking) -> CallError:
    """The refusal that ``asking`` ends in. (Kept: the fixture reads what it says.)"""
    try:
        await asking
    except CallError as error:
        CAUGHT.append(error)
        return error
    raise AssertionError("it was not refused")


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def a_row(number: int = 0, **changes: Any) -> dict:
    """A walk as it is sent: half an hour, each ``number`` an hour earlier than the one before."""
    start = START - timedelta(hours=number)
    row = {
        "id": str(uuid.uuid4()),
        "origin": "walka",
        "client_id": start.strftime("%Y%m%d-%H%M%S"),
        "started_at": stamp(start),
        "ended_at": stamp(start + timedelta(minutes=30)),
        "local_day": start.strftime("%Y-%m-%d"),
        "utc_offset_min": 60,
        "seconds": 1800.0,
        "distance_m": 2000.0,
        "steps": 2600,
        "kcal": 110.0,
        "kcal_extra": 80.0,
        "session": "wow",
        "steps_source": "estimate",
        "distance_source": "speed",
        "calories_source": "speed",
    }
    row.update(changes)
    assert tuple(row) == ROW_KEYS
    return row


def start_of(row: dict) -> str:
    return row["started_at"][:19]


def a_sign_in(**changes: Any) -> dict:
    """What the token calls answer, as the sign-in service writes it - with the other site's keys in it."""
    said = {
        "access_token": "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmVk",
        "token_type": "bearer",
        "expires_in": 3600,
        "expires_at": 1,
        "refresh_token": "abcdefghijkl",
        "user": {"id": SOME_ID, "email": "walker@mail.example", "user_metadata": {"full_name": "A Real Name"}},
        "provider_token": "the-other-sites-key",
        "provider_refresh_token": "the-other-sites-second-key",
    }
    for name, value in changes.items():
        if name in ("id", "email"):
            said["user"] = {**said["user"], name: value}
        else:
            said[name] = value
    NEEDLES.extend(["the-other-sites-key", "A Real Name"])
    return said


def unused_port() -> int:
    with socket.socket() as plug:
        plug.bind(("127.0.0.1", 0))
        return plug.getsockname()[1]


async def settled(before: set) -> bool:
    """Has every task that began since ``before`` ended? (Waits a little: the stand-in's own end last.)"""
    for _ in range(200):
        if set(asyncio.all_tasks()) <= before:
            return True
        await asyncio.sleep(0.01)
    return False


# ---- the list itself -----------------------------------------------------------------------------

BEARER = {"apikey": "test-key", "Authorization": "Bearer " + TOKEN}
ROWS = [a_row(0), a_row(1)]
THE_LIST = [
    (
        lambda c: calls.exchange(c, "test-key", code="the-code", verifier=VERIFIER),
        ("POST", "/auth/v1/token", [("grant_type", "pkce")], {"apikey": "test-key"}),
        {"auth_code": "the-code", "code_verifier": VERIFIER},
        10.0,
    ),
    (
        lambda c: calls.refresh(c, "test-key", refresh_token="abcdefghijkl"),
        ("POST", "/auth/v1/token", [("grant_type", "refresh_token")], {"apikey": "test-key"}),
        {"refresh_token": "abcdefghijkl"},
        20.0,
    ),
    (
        lambda c: calls.logout(c, "test-key", access_token=TOKEN),
        ("POST", "/auth/v1/logout", [("scope", "local")], BEARER),
        None,
        3.0,
    ),
    (lambda c: calls.who(c, "test-key", access_token=TOKEN), ("GET", "/auth/v1/user", [], BEARER), None, 20.0),
    (
        lambda c: calls.schema_version(c, "test-key", access_token=TOKEN),
        ("POST", "/rest/v1/rpc/schema_version", [], BEARER),
        {},
        20.0,
    ),
    (
        lambda c: calls.first_step(c, "test-key", access_token=TOKEN),
        ("GET", "/rest/v1/profiles", [("select", "adult_confirmed_at")], BEARER),
        None,
        20.0,
    ),
    (
        lambda c: calls.page(c, "test-key", access_token=TOKEN),
        ("GET", "/rest/v1/profiles", [("select", "handle,is_public,show_days,show_walks")], BEARER),
        None,
        20.0,
    ),
    (
        lambda c: calls.push(c, "test-key", access_token=TOKEN, rows=ROWS),
        (
            "POST",
            "/rest/v1/walks",
            [("on_conflict", "user_id,start_key"), ("select", "id")],
            {**BEARER, "Prefer": "resolution=ignore-duplicates,return=representation"},
        ),
        ROWS,
        20.0,
    ),
    (
        lambda c: calls.find(c, "test-key", access_token=TOKEN, start_keys=["2026-11-19T20:30:00"]),
        (
            "GET",
            "/rest/v1/walks",
            [("start_key", "in.(2026-11-19T20:30:00)"), ("select", "id,start_key,deleted_at")],
            BEARER,
        ),
        None,
        20.0,
    ),
]


THE_NAMES = ["exchange", "refresh", "logout", "who", "schema_version", "first_step", "page", "push", "find"]


@pytest.mark.parametrize(("ask", "address", "body", "timeout_s"), THE_LIST, ids=THE_NAMES)
def test_each_request_is_exactly_the_one_of_the_list(ask, address, body, timeout_s):
    async def scenario():
        double = Double(Answer(500, None, b""))
        try:
            await ask(double)
        except CallError as error:
            assert error.kind == "later"
        assert len(double.asked) == 1
        asked = double.asked[0]
        assert (asked["method"], asked["path"], asked["query"], asked["headers"]) == address
        assert asked["body"] == body
        assert asked["timeout_s"] == timeout_s

    run(scenario)


def test_the_module_names_these_paths_and_no_other():
    tree = ast.parse(inspect.getsource(calls))
    named = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/")
    }
    assert named == {
        "/auth/v1/authorize",
        "/auth/v1/token",
        "/auth/v1/logout",
        "/auth/v1/user",
        "/rest/v1/rpc/schema_version",
        "/rest/v1/profiles",
        "/rest/v1/walks",
    }
    # And no way of asking but the two: there is no PATCH and no DELETE to be found in it.
    words = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert words & {"GET", "POST", "PATCH", "DELETE", "PUT", "HEAD"} == {"GET", "POST"}
    assert calls.PREFER == PREFER
    assert (calls.EXCHANGE_S, calls.LOGOUT_S, calls.MOST_ROWS, calls.MOST_KEYS) == (10.0, 3.0, 200, 50)


def test_it_writes_nothing_to_the_log_by_itself():
    names = {node.id for node in ast.walk(ast.parse(inspect.getsource(calls))) if isinstance(node, ast.Name)}
    assert "logging" not in names and "log" not in names and "print" not in names


def test_it_brings_in_no_clock_no_log_and_nothing_that_starts_a_program():
    tree = ast.parse(inspect.getsource(calls))
    brought = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            brought |= {name.name for name in node.names}
        elif isinstance(node, ast.ImportFrom):
            brought.add("." * node.level + (node.module or ""))
    assert brought == {
        "__future__",
        "re",
        "unicodedata",
        "collections.abc",
        "dataclasses",
        "typing",
        "urllib.parse",
        ".https",
        ".row",
    }
    # It waits for nothing but the answer: one place asks, and nothing else is awaited.
    waited = [node for node in ast.walk(tree) if isinstance(node, ast.Await)]
    asks = [node for node in waited if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)]
    assert [node.value.func.attr for node in asks] == ["request"]
    others = {node.value.func.id for node in waited if node not in asks}
    assert others == {"_ask", "_profile"}


# ---- signing in ----------------------------------------------------------------------------------


def test_the_browsers_address_is_the_accounts_own_and_takes_only_a_way_back_to_this_computer():
    origin = Origin("https", "accounts.example", None)
    back = "http://127.0.0.1:50123/" + "p" * 43
    address = calls.browser_address(origin, way="google", back=back, challenge="c" * 43)
    assert address == (
        "https://accounts.example/auth/v1/authorize?provider=google"
        "&redirect_to=http%3A%2F%2F127.0.0.1%3A50123%2F" + "p" * 43 + "&code_challenge=" + "c" * 43
        + "&code_challenge_method=s256"
    )
    assert calls.WAYS == ("discord", "github", "google")
    good = {"way": "github", "back": back, "challenge": "c" * 43}
    for change in (
        {"way": "email"},
        {"way": "GitHub"},
        {"way": None},
        {"back": "http://localhost:50123/" + "p" * 43},
        {"back": "https://website.example/account/"},
        {"back": "http://127.0.0.1:50123/short"},
        {"back": "http://127.0.0.1:50123/" + "p" * 43 + "?next=x"},
        {"back": "http://127.0.0.1:50123@website.example/" + "p" * 43},
        {"back": "http://127.0.0.1/" + "p" * 43},
        {"back": "http://127.0.0.1:65536/" + "p" * 43},
        {"back": "http://127.0.0.1:99999/" + "p" * 43},
        {"back": "http://127.0.0.1:0/" + "p" * 43},
        {"back": "http://127.0.0.1:050123/" + "p" * 43},
        {"back": "http://127.0.0.1:50123/" + "p" * 31},
        {"back": "http://127.0.0.1:50123/" + "p" * 129},
        {"back": "http://127.0.0.1:50123/" + "p" * 43 + "/"},
        {"back": "http://127.0.0.1:50123/" + "p" * 43 + "\n"},
        {"back": ["http://127.0.0.1:50123/" + "p" * 43]},
        {"challenge": "c" * 42},
        {"challenge": "c" * 44},
        {"challenge": "c" * 42 + "="},
        {"challenge": None},
    ):
        with pytest.raises(ValueError):
            calls.browser_address(origin, **{**good, **change})
    for elsewhere in (Origin("http", "accounts.example", None), Origin("https", "a@b.example", None), "https://x"):
        with pytest.raises(ValueError):
            calls.browser_address(elsewhere, **good)
    for port in ("1", "65535"):
        there = f"http://127.0.0.1:{port}/" + "p" * 32
        assert parse_qsl(urlsplit(calls.browser_address(origin, **{**good, "back": there})).query)[1] == (
            "redirect_to",
            there,
        )
    # What it says when it refuses holds nothing of what it was given.
    with pytest.raises(ValueError) as refusal:
        calls.browser_address(origin, way="github", back="http://127.0.0.1:50123/secret-path", challenge="c" * 43)
    assert "secret-path" not in repr(refusal.value) and "50123" not in repr(refusal.value)


def test_a_code_from_the_browser_buys_a_sign_in_and_nothing_of_the_other_site_is_kept():
    async def scenario():
        async with Accounts() as a:
            code = await a.a_code()
            signed = await calls.exchange(a.client, a.key, code=code, verifier=VERIFIER)
            assert isinstance(signed, Signed)
            assert (signed.user_id, signed.email, signed.lifetime_s) == (a.user, "walker@mail.example", 3600.0)
            issued = a.fake.secrets_issued()
            assert signed.access_token in issued and signed.refresh_token in issued
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path, asked.query) == ("POST", "/auth/v1/token", [("grant_type", "pkce")])
            assert asked.json == {"auth_code": code, "code_verifier": VERIFIER}
            assert asked.header("apikey") == "test-key" and asked.header("Authorization") is None
            # What is kept is five things. The other site's own keys are not among them...
            assert sorted(vars(signed)) == ["access_token", "email", "lifetime_s", "refresh_token", "user_id"]
            other = [key for key in issued if key.startswith("other-site-")]
            assert len(other) == 2 and not any(key in repr(vars(signed)) for key in other)
            # ...and shown by accident, it tells no key, and not who.
            assert repr(signed) == str(signed) == "Signed(lifetime_s=3600.0)"
            with pytest.raises(AttributeError):
                signed.user_id = SOME_ID  # (what was checked stays as it was checked)
            # The access token is good: the account answers to it.
            assert await calls.who(a.client, a.key, access_token=signed.access_token) == "there"

    run(scenario)


def test_a_wrong_verifier_is_a_bad_code_and_the_code_stays_good_for_the_right_one():
    async def scenario():
        async with Accounts() as a:
            code = await a.a_code()
            error = await refused(calls.exchange(a.client, a.key, code=code, verifier="w" * 43))
            assert (error.kind, error.code) == ("bad_code", "bad_code_verifier")
            assert error.message == "code challenge does not match previously saved code verifier"
            signed = await calls.exchange(a.client, a.key, code=code, verifier=VERIFIER)
            assert signed.user_id == a.user
            # Used once, it is used up.
            error = await refused(calls.exchange(a.client, a.key, code=code, verifier=VERIFIER))
            assert (error.kind, error.code) == ("bad_code", "flow_state_not_found")

    run(scenario)


def test_any_other_refusal_of_the_exchange_is_a_bad_code_too():
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 422, {"code": 422, "error_code": "validation_failed", "msg": "no"})
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert (error.kind, error.code, error.message) == ("bad_code", "validation_failed", "no")
            a.fake.fail_next("/auth/v1/token", 403, b"<html>Forbidden</html>")
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert (error.kind, error.code, error.message) == ("bad_code", "", "")

    run(scenario)


@pytest.mark.parametrize("code", ["", "a code", "a\ncode", "c" * 513, "kód", None, 7, b"code"])
def test_what_is_not_shaped_like_a_code_is_never_sent(code):
    async def scenario():
        async with Accounts() as a:
            error = await refused(calls.exchange(a.client, a.key, code=code, verifier=VERIFIER))
            assert error.kind == "bad_code"
            assert a.fake.requests == []

    run(scenario)


@pytest.mark.parametrize("verifier", ["", "v" * 42, "v" * 129, "v" * 42 + " ", None])
def test_a_verifier_that_is_not_one_is_never_sent(verifier):
    async def scenario():
        async with Accounts() as a:
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=verifier))
            assert error.kind == "stop"
            assert a.fake.requests == []

    run(scenario)


def test_a_refresh_gives_new_keys_and_spends_the_old_refresh_token():
    async def scenario():
        async with Accounts() as a:
            first = a.sign_in()
            signed = await calls.refresh(a.client, a.key, refresh_token=first["refresh_token"])
            assert (signed.user_id, signed.email, signed.lifetime_s) == (a.user, "walker@mail.example", 3600.0)
            assert signed.refresh_token != first["refresh_token"]
            assert signed.access_token != first["access_token"]
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path) == ("POST", "/auth/v1/token")
            assert asked.query == [("grant_type", "refresh_token")]
            assert asked.json == {"refresh_token": first["refresh_token"]}
            assert asked.header("Authorization") is None
            again = await calls.refresh(a.client, a.key, refresh_token=signed.refresh_token)
            assert again.refresh_token not in (first["refresh_token"], signed.refresh_token)

    run(scenario)


def test_a_refresh_token_the_accounts_do_not_know_means_the_sign_in_is_over():
    async def scenario():
        async with Accounts() as a:
            first = a.sign_in()
            a.fake.sign_out_everywhere(a.user)
            error = await refused(calls.refresh(a.client, a.key, refresh_token=first["refresh_token"]))
            assert (error.kind, error.code) == ("sign_in_over", "refresh_token_not_found")
            assert error.retry_after is None and not error.pkey and not error.days_to_come

    run(scenario)


def test_an_old_refresh_token_used_again_means_the_sign_in_is_over():
    async def scenario():
        async with Accounts() as a:
            first = a.sign_in()
            second = await calls.refresh(a.client, a.key, refresh_token=first["refresh_token"])
            third = await calls.refresh(a.client, a.key, refresh_token=second.refresh_token)
            a.clock.now += 60.0  # (within ten seconds the old one would still be taken)
            error = await refused(calls.refresh(a.client, a.key, refresh_token=first["refresh_token"]))
            assert (error.kind, error.code) == ("sign_in_over", "refresh_token_already_used")
            # The whole sign-in went with it: its newest token says the same.
            error = await refused(calls.refresh(a.client, a.key, refresh_token=third.refresh_token))
            assert (error.kind, error.code) == ("sign_in_over", "refresh_token_already_used")

    run(scenario)


def test_any_other_refusal_of_a_refresh_does_not_end_the_sign_in():
    async def scenario():
        async with Accounts() as a:
            for status, body in (
                (400, {"code": 400, "error_code": "validation_failed", "msg": "no"}),
                (401, {"message": "Invalid API key", "hint": "Double check"}),
                (403, {"code": 403, "error_code": "bad_jwt", "msg": "no"}),
                (404, b"<html>Not here</html>"),
                # The two words count where the sign-in service puts them, and only as a refusal.
                (400, {"code": "refresh_token_not_found", "message": "refresh_token_not_found"}),
                (200, {"error_code": "refresh_token_not_found"}),
                (302, {"code": 302, "error_code": "refresh_token_already_used", "msg": "no"}),
            ):
                a.fake.fail_next("/auth/v1/token", status, body)
                error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
                assert error.kind == "stop", (status, body)

    run(scenario)


@pytest.mark.parametrize(
    ("changes", "lifetime_s"),
    [
        ({}, 3600.0),
        ({"expires_in": 59.5}, 59.5),
        ({"expires_in": 0}, 0.0),
        ({"expires_in": -5}, 0.0),
        ({"expires_in": 7200}, 3600.0),
        ({"expires_in": 10**400}, 3600.0),
        ({"expires_at": 99999999999}, 3600.0),  # the accounts' clock is not gone by
        ({"access_token": "x" * 4096}, 3600.0),
        ({"token_type": "something"}, 3600.0),
    ],
)
def test_how_long_a_token_is_good_for_is_cut_to_an_hour_at_most(changes, lifetime_s):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 200, a_sign_in(**changes))
            signed = await calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")
            assert signed.lifetime_s == lifetime_s and isinstance(signed.lifetime_s, float)
            assert (signed.user_id, signed.email) == (SOME_ID, "walker@mail.example")

    run(scenario)


@pytest.mark.parametrize(
    "changes",
    [
        {"access_token": "two words"},
        {"access_token": "line\nend"},
        {"access_token": ""},
        {"access_token": "x" * 4097},
        {"access_token": "tökén"},
        {"access_token": None},
        {"access_token": 12345},
        {"access_token": ["a.b.c"]},
        {"refresh_token": "two words"},
        {"refresh_token": "tab\there"},
        {"refresh_token": ""},
        {"refresh_token": "x" * 4097},
        {"refresh_token": None},
        {"refresh_token": {"token": "abcdefghijkl"}},
        {"expires_in": "3600"},
        {"expires_in": True},
        {"expires_in": None},
        {"expires_in": [3600]},
        {"user": None},
        {"user": [SOME_ID]},
        {"user": SOME_ID},
        {"id": "not-an-id"},
        {"id": SOME_ID.upper()},
        {"id": SOME_ID[:8].upper() + SOME_ID[8:]},
        {"id": SOME_ID + "\n"},
        {"id": " " + SOME_ID},
        {"id": SOME_ID.replace("-", "")},
        {"id": "../../" + SOME_ID},
        {"id": None},
        {"id": 7},
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_a_sign_in_with_anything_unfit_in_it_is_no_sign_in(changes):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 200, a_sign_in(**changes), times=2)
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


@pytest.mark.parametrize("status", [201, 202, 206, 302, 400, 401, 403, 404, 409, 422])
def test_a_sign_in_is_read_only_from_a_200(status):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", status, a_sign_in(), times=2)
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert (error.kind, error.code, error.message) == ("bad_code" if status >= 400 else "stop", "", "")

    run(scenario)


def test_of_an_answer_that_is_no_refusal_no_word_is_kept():
    # A sign-in that does not fit has NEW keys in it, and whatever else it says may be one of them.
    async def scenario():
        async with Accounts() as a:
            new_key = "a-new-refresh-key"
            NEEDLES.append(new_key)
            said = a_sign_in(access_token="two words", refresh_token=new_key, error_code=new_key, msg=new_key)
            for status in (200, 201, 204, 302):
                a.fake.fail_next("/auth/v1/token", status, {**said, "code": status}, times=2)
                error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
                assert (error.kind, error.code, error.message) == ("stop", "", ""), status
                error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
                assert (error.kind, error.code, error.message) == ("stop", "", ""), status
                a.fake.fail_next("/auth/v1/user", status, {"code": status, "error_code": "user_not_found", "msg": "x"})
                error = await refused(calls.who(a.client, a.key, access_token=a.token))
                assert (error.kind, error.code, error.message) == ("stop", "", ""), status
            # Of a refusal they are kept, as everywhere.
            a.fake.fail_next("/auth/v1/token", 400, {"code": 400, "error_code": "validation_failed", "msg": "no"})
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert (error.kind, error.code, error.message) == ("stop", "validation_failed", "no")

    run(scenario)


@pytest.mark.parametrize(
    "body",
    [[], [a_sign_in()], "a sign-in", 3600, None, b"", b"<html>Signed in</html>", b'{"access_token": "a.b.c"', {}],
    ids=lambda body: type(body).__name__,
)
def test_a_200_that_is_not_a_sign_in_at_all_is_no_sign_in(body):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 200, body)
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert error.kind == "stop"

    run(scenario)


@pytest.mark.parametrize(
    "email",
    [
        "walker@mail.example",
        "wälker@mail.example",
        "w" * 241 + "@mail.example",  # 254 characters
    ],
)
def test_an_e_mail_address_that_can_be_shown_is_kept_as_it_is(email):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 200, a_sign_in(email=email))
            signed = await calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")
            assert signed.email == email

    run(scenario)


@pytest.mark.parametrize(
    "email",
    [
        "w" * 242 + "@mail.example",  # 255 characters
        "w" * 287 + "@mail.example",  # 300
        "walker\x07@mail.example",
        "walker\n@mail.example",
        "walker@mail.example\r\nSigned in as somebody.else@mail.example",
        "walker\x00@mail.example",
        "walker\x7f@mail.example",
        "walker\x9b@mail.example",
        "elpmaxe.liam@\u202eevil",  # written from right to left: it would READ as another address
        "walker\u202d@mail.example",
        "walker\u2066@mail.example",
        "walker\u200f@mail.example",
        "wal\u200bker@mail.example",  # takes no room
        "wal\u200dker@mail.example",
        "wal\u2060ker@mail.example",
        "\ufeffwalker@mail.example",
        "wal\xadker@mail.example",
        "wal\u034fker@mail.example",
        "wal\u3164ker@mail.example",
        "walker@mail.example\u2800\u2800\u2800and.another@mail.example",  # a blank that Unicode calls a sign
        "wal\ufe0fker@mail.example",
        "wal\ufe00ker@mail.example",
        "wal\U000e0100ker@mail.example",
        "wal\U000e01efker@mail.example",
        "wal\u180bker@mail.example",
        "wal\u180eker@mail.example",
        "wal\U000e0041ker@mail.example",
        "walker@mail.example\u2028",
        "wal ker@mail.example",
        "walker@mail.example ",
        "wal\xa0ker@mail.example",
        "wal\ue000ker@mail.example",
        "wal\ud800ker@mail.example",
        "walker",
        "@mail.example",
        "walker@",
        "@",
        "",
        None,
        12345,
        ["walker@mail.example"],
        {"address": "walker@mail.example"},
    ],
    ids=lambda email: ascii(email)[:30],
)
def test_an_e_mail_address_that_cannot_be_shown_as_what_it_is_reads_as_none(email):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/token", 200, a_sign_in(email=email))
            signed = await calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")
            assert signed.email == ""
            assert signed.user_id == SOME_ID  # signed in all the same: the caller says why nothing can be sent

    run(scenario)


def test_a_sign_in_without_an_e_mail_address_reads_as_none():
    async def scenario():
        async with Accounts() as a:
            said = a_sign_in()
            del said["user"]["email"]
            a.fake.fail_next("/auth/v1/token", 200, said)
            assert (await calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")).email == ""

    run(scenario)


# ---- signing out, and who is there ---------------------------------------------------------------


def test_signing_out_ends_this_sign_in_only():
    async def scenario():
        async with Accounts() as a:
            here, elsewhere = a.sign_in(), a.sign_in()
            assert await calls.logout(a.client, a.key, access_token=here["access_token"]) is None
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path, asked.query) == ("POST", "/auth/v1/logout", [("scope", "local")])
            assert asked.header("Authorization") == "Bearer " + here["access_token"]
            assert asked.body == b"" and asked.header("Content-Type") is None
            assert await calls.who(a.client, a.key, access_token=here["access_token"]) == "signed_out"
            assert await calls.who(a.client, a.key, access_token=elsewhere["access_token"]) == "there"

    run(scenario)


def test_signing_out_never_fails_whatever_the_answer():
    async def scenario():
        async with Accounts() as a:
            # A token that has run out: refused, and that is that.
            ran_out = a.sign_in()["access_token"]
            a.fake.expire_access_tokens()
            assert await calls.logout(a.client, a.key, access_token=ran_out) is None
            assert len(a.fake.requests) == 1
            for status, body in ((500, None), (429, None), (404, b"<html></html>"), (302, None), (200, [1, 2])):
                a.fake.fail_next("/auth/v1/logout", status, body)
                assert await calls.logout(a.client, a.key, access_token=a.token) is None
            a.fake.drop_next("/auth/v1/logout")
            assert await calls.logout(a.client, a.key, access_token=a.token) is None
            assert len(a.fake.requests) == 7
            # What cannot be sent is not sent, and still nothing is raised.
            for token in ("", "two words", None, 7):
                assert await calls.logout(a.client, a.key, access_token=token) is None
            assert await calls.logout(a.client, "sb_secret_never", access_token=a.token) is None
            assert len(a.fake.requests) == 7

    run(scenario)


def test_signing_out_waits_three_seconds_at_most_and_not_for_ever(monkeypatch):
    monkeypatch.setattr(calls, "LOGOUT_S", 0.5)

    async def scenario():
        async with Accounts() as a:
            gate = a.fake.hold("/auth/v1/logout")
            began = asyncio.get_running_loop().time()
            assert await calls.logout(a.client, a.key, access_token=a.token) is None
            assert gate.arrived.is_set()
            assert asyncio.get_running_loop().time() - began < 5.0
            await asyncio.wait_for(gate.left.wait(), 5)

    run(scenario)


@pytest.mark.parametrize("gives", [RuntimeError("Bearer " + TOKEN), NetError("certificate"), "no answer", None])
def test_signing_out_never_fails_whatever_the_client_does(gives):
    async def scenario():
        double = Double(gives)
        assert await calls.logout(double, "test-key", access_token=TOKEN) is None
        assert len(double.asked) == 1

    run(scenario)


def test_who_says_there_signed_out_or_gone():
    async def scenario():
        async with Accounts() as a:
            token = a.token
            assert await calls.who(a.client, a.key, access_token=token) == "there"
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path, asked.query) == ("GET", "/auth/v1/user", [])
            assert asked.header("Authorization") == "Bearer " + token and asked.header("apikey") == "test-key"
            a.fake.sign_out_everywhere(a.user)
            assert await calls.who(a.client, a.key, access_token=token) == "signed_out"
            a.fake.delete_user(a.user)
            assert await calls.who(a.client, a.key, access_token=token) == "gone"

    run(scenario)


def test_who_with_a_token_that_ran_out_stops_and_guesses_nothing():
    async def scenario():
        async with Accounts() as a:
            token = a.token
            a.fake.expire_access_tokens()
            error = await refused(calls.who(a.client, a.key, access_token=token))
            assert (error.kind, error.code) == ("stop", "bad_jwt")

    run(scenario)


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (200, {"email": "walker@mail.example"}, "stop"),
        (200, {"id": "not-an-id"}, "stop"),
        (200, [], "stop"),
        (200, b"<html>You</html>", "stop"),
        # Only a 200 says that somebody is there.
        (201, {"id": SOME_ID}, "stop"),
        (206, {"id": SOME_ID}, "stop"),
        (302, {"id": SOME_ID}, "stop"),
        (403, {"id": SOME_ID}, "stop"),
        (404, {"id": SOME_ID}, "stop"),
        # The two words count as the sign-in service says them: with a 403, under "error_code".
        (404, {"code": 404, "error_code": "user_not_found", "msg": "no"}, "stop"),
        (404, {"code": 404, "error_code": "session_not_found", "msg": "no"}, "stop"),
        (401, {"code": 401, "error_code": "session_not_found", "msg": "no"}, "stop"),
        (200, {"code": 403, "error_code": "session_not_found", "msg": "no"}, "stop"),
        (200, {"code": 403, "error_code": "user_not_found", "msg": "no"}, "stop"),
        (403, {"code": "user_not_found", "message": "user_not_found"}, "stop"),
        (403, {"code": 403, "error_code": "something_new", "msg": "no"}, "stop"),
        (401, {"message": "Invalid API key"}, "stop"),
        (302, None, "stop"),
        (500, {"code": 500, "error_code": "user_not_found", "msg": "no"}, "later"),
        (503, b"<html>Busy</html>", "later"),
        (429, None, "later"),
    ],
)
def test_who_takes_nothing_else_for_an_answer(status, body, kind):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/auth/v1/user", status, body)
            error = await refused(calls.who(a.client, a.key, access_token=a.token))
            assert error.kind == kind

    run(scenario)


# ---- the version, the first step, the page -------------------------------------------------------


def test_the_schema_version_is_a_whole_number():
    async def scenario():
        async with Accounts() as a:
            assert await calls.schema_version(a.client, a.key, access_token=a.token) == 1
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path, asked.query) == ("POST", "/rest/v1/rpc/schema_version", [])
            assert asked.json == {} and asked.header("Authorization") == "Bearer " + a.token
            assert asked.header("Prefer") is None
            a.fake.schema = 2
            assert await calls.schema_version(a.client, a.key, access_token=a.token) == 2

    run(scenario)


@pytest.mark.parametrize("body", [1.0, "1", True, None, [1], {"schema_version": 1}, 0, -1, 1.5, b"1.", b"<html>1</html>"])
def test_a_schema_version_that_is_no_whole_number_stops(body):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/rpc/schema_version", 200, body)
            error = await refused(calls.schema_version(a.client, a.key, access_token=a.token))
            assert error.kind == "stop"

    run(scenario)


@pytest.mark.parametrize("status", [201, 202, 206, 302, 400, 404])
def test_the_version_the_first_step_and_the_page_are_read_only_from_a_200(status):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/rpc", status, 1)
            error = await refused(calls.schema_version(a.client, a.key, access_token=a.token))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            a.fake.fail_next("/rest/v1/profiles", status, [{"adult_confirmed_at": "2026-11-20T10:00:00+00:00"}])
            error = await refused(calls.first_step(a.client, a.key, access_token=a.token))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            row = {"handle": "walker", "is_public": True, "show_days": True, "show_walks": False}
            for body in ([row], []):
                a.fake.fail_next("/rest/v1/profiles", status, body)
                error = await refused(calls.page(a.client, a.key, access_token=a.token))
                assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


def test_the_first_step_reads_as_taken_not_taken_or_no_row_at_all():
    async def scenario():
        async with Accounts(adult=False) as a:
            assert await calls.first_step(a.client, a.key, access_token=a.token) is None
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path) == ("GET", "/rest/v1/profiles")
            assert asked.query == [("select", "adult_confirmed_at")]
            assert asked.header("Authorization") == "Bearer " + a.token and asked.body == b""
            other = a.person("other@mail.example", adult=False, profile_row=True)
            token = a.sign_in(other)["access_token"]
            assert await calls.first_step(a.client, a.key, access_token=token) is False
            a.fake.take_first_step(other)
            assert await calls.first_step(a.client, a.key, access_token=token) is True
            # An account that is gone reads like one that never was: "who" tells them apart.
            a.fake.delete_user(other)
            assert await calls.first_step(a.client, a.key, access_token=token) is None
            assert await calls.who(a.client, a.key, access_token=token) == "gone"

    run(scenario)


@pytest.mark.parametrize(
    ("body", "reads"),
    [
        ([{"adult_confirmed_at": ""}], False),
        ([{"adult_confirmed_at": None, "user_id": SOME_ID}], False),
        ([{"adult_confirmed_at": "2026-11-20T10:00:00.123456+00:00"}], True),
        ([{"adult_confirmed_at": True}], "stop"),
        ([{"adult_confirmed_at": False}], "stop"),
        ([{"adult_confirmed_at": 0}], "stop"),
        ([{"adult_confirmed_at": []}], "stop"),
        ([{"adult_confirmed_at": {}}], "stop"),
        ([{"adult_confirmed_at": 1795300000}], "stop"),
        ([{"adult_confirmed_at": ["2026-11-20"]}], "stop"),
        ([{"adult_confirmed_at": "x" * 65}], "stop"),
        ([{"adult_confirmed_at": "2026-11-20 10:00:00+00"}], True),
        ([{"adult_confirmed_at": "2026-11-20T10:00:00Z"}], True),
        # Only a moment says "taken": no other word does, whatever it reads like.
        ([{"adult_confirmed_at": " "}], "stop"),
        ([{"adult_confirmed_at": "false"}], "stop"),
        ([{"adult_confirmed_at": "null"}], "stop"),
        ([{"adult_confirmed_at": "no"}], "stop"),
        ([{"adult_confirmed_at": "0"}], "stop"),
        ([{"adult_confirmed_at": "2026-11-20"}], "stop"),
        ([{"adult_confirmed_at": "2026-11-20T10:00:00+00:00 or never"}], "stop"),
        ([{"adult_confirmed_at": "2026-11-20T10:00:00+00:00\n"}], "stop"),
        ([{"adult_confirmed_at": "2026-11-20T10:00:00." + "0" * 60}], "stop"),
        ([{"adult_confirmed_at": "२०२६-११-२०T१०:००:००"}], "stop"),
        ([{}], "stop"),
        ([{"handle": "walker"}], "stop"),
        ([["adult_confirmed_at"]], "stop"),
        ([{"adult_confirmed_at": "2026-11-20"}, {"adult_confirmed_at": None}], "stop"),
        ({"adult_confirmed_at": "2026-11-20T10:00:00+00:00"}, "stop"),
        ("2026-11-20T10:00:00+00:00", "stop"),
        (None, "stop"),
        (b"<html>Yes</html>", "stop"),
    ],
)
def test_the_first_step_is_read_only_from_an_answer_shaped_as_the_database_gives_it(body, reads):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/profiles", 200, body)
            if reads == "stop":
                error = await refused(calls.first_step(a.client, a.key, access_token=a.token))
                assert error.kind == "stop"
            else:
                assert await calls.first_step(a.client, a.key, access_token=a.token) is reads

    run(scenario)


def test_the_page_reads_as_its_four_columns_or_as_no_row():
    async def scenario():
        async with Accounts(adult=False) as a:
            assert await calls.page(a.client, a.key, access_token=a.token) is None
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path) == ("GET", "/rest/v1/profiles")
            assert asked.query == [("select", "handle,is_public,show_days,show_walks")]
            quiet = a.person("quiet@mail.example")
            token = a.sign_in(quiet)["access_token"]
            assert await calls.page(a.client, a.key, access_token=token) == {
                "handle": None,
                "is_public": False,
                "show_days": False,
                "show_walks": False,
            }
            a.fake.set_page(quiet, handle="quiet-walker", is_public=True, show_walks=True)
            assert await calls.page(a.client, a.key, access_token=token) == {
                "handle": "quiet-walker",
                "is_public": True,
                "show_days": False,
                "show_walks": True,
            }

    run(scenario)


@pytest.mark.parametrize(
    "changes",
    [
        {"is_public": "true"},
        {"is_public": 1},
        {"is_public": None},
        {"show_days": "no"},
        {"show_walks": 0},
        {"show_walks": []},
        {"handle": 7},
        {"handle": ""},
        {"handle": "w" * 65},
        {"handle": "walk\u202eer"},
        {"handle": "walk\ner"},
        {"handle": ["walker"]},
        {"drop": "handle"},
        {"drop": "is_public"},
        {"drop": "show_days"},
        {"drop": "show_walks"},
    ],
    ids=lambda changes: "-".join(map(str, changes)),
)
def test_a_page_with_anything_unfit_in_it_stops(changes):
    async def scenario():
        async with Accounts() as a:
            row = {"handle": "walker", "is_public": True, "show_days": True, "show_walks": False}
            row.update(changes)
            row.pop(row.pop("drop", ""), None)
            a.fake.fail_next("/rest/v1/profiles", 200, [row])
            error = await refused(calls.page(a.client, a.key, access_token=a.token))
            assert error.kind == "stop"

    run(scenario)


def test_of_a_page_only_the_four_columns_are_handed_on():
    async def scenario():
        async with Accounts() as a:
            row = {"handle": "walker", "is_public": True, "show_days": True, "show_walks": False}
            a.fake.fail_next("/rest/v1/profiles", 200, [{**row, "user_id": SOME_ID, "display_name": "A Real Name"}])
            assert await calls.page(a.client, a.key, access_token=a.token) == row

    run(scenario)


# ---- sending walks -------------------------------------------------------------------------------


def test_rows_that_are_added_come_back_with_the_ids_sent():
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(0), a_row(1), a_row(2)]
            sent_as = [dict(row) for row in rows]
            assert await calls.push(a.client, a.key, access_token=a.token, rows=rows) == [row["id"] for row in rows]
            assert rows == sent_as  # the caller's rows are left as they were
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path) == ("POST", "/rest/v1/walks")
            assert asked.query == [("on_conflict", "user_id,start_key"), ("select", "id")]
            assert asked.header("Prefer") == "resolution=ignore-duplicates,return=representation"
            assert asked.header("Authorization") == "Bearer " + a.token and asked.header("apikey") == "test-key"
            assert asked.header("Content-Type") == "application/json"
            assert asked.json == rows and [tuple(row) for row in asked.json] == [ROW_KEYS] * 3
            assert [walk["id"] for walk in a.fake.walks(a.user)] == [row["id"] for row in rows]
            assert {walk["user_id"] for walk in a.fake.walks(a.user)} == {a.user}

    run(scenario)


def test_a_row_goes_as_exactly_the_sixteen_keys_in_their_order_however_it_was_handed_over():
    async def scenario():
        async with Accounts() as a:
            row = a_row(0)
            backwards = {name: row[name] for name in reversed(ROW_KEYS)}
            assert await calls.push(a.client, a.key, access_token=a.token, rows=[backwards]) == [row["id"]]
            assert tuple(a.fake.requests[-1].json[0]) == ROW_KEYS
            assert tuple(backwards) == tuple(reversed(ROW_KEYS))

    run(scenario)


def test_a_walk_that_is_there_already_does_not_come_back():
    async def scenario():
        async with Accounts() as a:
            first = [a_row(0), a_row(1)]
            assert len(await calls.push(a.client, a.key, access_token=a.token, rows=first)) == 2
            # The same walks under new ids, and a new one between them.
            again = [a_row(0), a_row(5), a_row(1)]
            assert await calls.push(a.client, a.key, access_token=a.token, rows=again) == [again[1]["id"]]
            assert await calls.push(a.client, a.key, access_token=a.token, rows=again) == []
            assert len(a.fake.walks(a.user)) == 3

    run(scenario)


def test_one_row_and_two_hundred_rows_both_go():
    async def scenario():
        async with Accounts() as a:
            one = [a_row(0)]
            assert await calls.push(a.client, a.key, access_token=a.token, rows=one) == [one[0]["id"]]
            many = [a_row(number) for number in range(1, 201)]
            assert await calls.push(a.client, a.key, access_token=a.token, rows=many) == [row["id"] for row in many]
            assert len(a.fake.walks(a.user)) == 201

    run(scenario)


def bad_rows() -> list[tuple[str, Any]]:
    seventeen = {**a_row(0), "device": "this-computer"}
    with_user = {**a_row(0), "user_id": SOME_ID}
    fifteen = a_row(0)
    del fifteen["kcal_extra"]
    swapped = a_row(0)
    swapped["treadmill"] = swapped.pop("session")
    twin = a_row(0)
    return [
        ("no rows", []),
        ("201 rows", [a_row(number) for number in range(201)]),
        ("a 17th key", [seventeen]),
        ("the account's id as a key", [with_user]),
        ("15 keys", [fifteen]),
        ("another key for one of the 16", [swapped]),
        ("one row among good ones", [a_row(0), seventeen, a_row(1)]),
        ("an id that is none", [a_row(0, id="20261119-213000")]),
        ("an id in capitals", [a_row(0, id=SOME_ID.upper())]),
        ("an id that is no text", [a_row(0, id=7)]),
        ("no id", [a_row(0, id=None)]),
        ("the same id twice", [twin, {**a_row(1), "id": twin["id"]}]),
        ("a row that is no row", [a_row(0), "a row"]),
        # A value is a word or a number. A list or a table in its place could carry anything at all.
        ("a table for a value", [a_row(0, session={"character": "Somebody", "realm": "Somewhere"})]),
        ("a list for a value", [a_row(0, kcal_extra=[61, 62, 64, 70])]),
        ("a tuple for a value", [a_row(0), a_row(1, steps=(2600,))]),
        ("bytes for a value", [a_row(0, client_id=b"20261119-213000")]),
        ("a tuple of rows", (a_row(0),)),
        ("one row, bare", a_row(0)),
        ("nothing", None),
    ]


@pytest.mark.parametrize(("_what", "rows"), bad_rows(), ids=[what for what, _rows in bad_rows()])
def test_rows_that_are_not_exactly_the_sixteen_keys_are_never_sent(_what, rows):
    async def scenario():
        async with Accounts() as a:
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=rows))
            assert error.kind == "stop"
            assert a.fake.requests == []

    run(scenario)


def test_a_token_that_ran_out_says_so_on_every_request_to_the_database():
    async def scenario():
        async with Accounts() as a:
            token = a.token
            a.fake.expire_access_tokens()
            for asking in (
                calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]),
                calls.schema_version(a.client, a.key, access_token=token),
                calls.first_step(a.client, a.key, access_token=token),
                calls.page(a.client, a.key, access_token=token),
            ):
                error = await refused(asking)
                assert (error.kind, error.code, error.message) == ("token_ran_out", "PGRST303", "JWT expired")
            assert a.fake.walks(a.user) == []
            # As the real answer is written (the record, item 2): two keys and no more.
            a.fake.fail_next("/rest/v1/walks", 401, {"code": "PGRST303", "message": "JWT expired"})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert error.kind == "token_ran_out"

    run(scenario)


def test_an_account_that_has_not_taken_its_first_step_is_not_open():
    async def scenario():
        async with Accounts(adult=False) as a:
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0), a_row(1)]))
            assert (error.kind, error.code, error.message) == ("not_open", "23514", ADULTS)
            assert not error.pkey and not error.days_to_come and error.retry_after is None
            assert a.fake.walks(a.user) == []

    run(scenario)


def test_an_account_that_is_gone_reads_as_not_open_and_who_tells():
    async def scenario():
        async with Accounts() as a:
            token = a.token
            a.fake.delete_user(a.user)
            error = await refused(calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]))
            assert error.kind == "not_open"
            assert await calls.who(a.client, a.key, access_token=token) == "gone"

    run(scenario)


def test_a_full_account_says_full_and_takes_nothing():
    async def scenario():
        async with Accounts() as a:
            a.fake.full_at = 3
            a.fake.fill(a.user, 2)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0), a_row(1)]))
            assert (error.kind, error.code) == ("full", "23514")
            assert error.message == "There is room for 3 rows of this kind in one account, and it is full."
            assert len(a.fake.walks(a.user)) == 2
            # As the live database says it.
            said = "There is room for 20000 rows of this kind in one account, and it is full."
            a.fake.fail_next("/rest/v1/walks", 400, {"code": "23514", "details": None, "hint": None, "message": said})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.message) == ("full", said)

    run(scenario)


def test_a_refusal_about_one_row_keeps_the_code_and_the_message_and_never_the_row():
    async def scenario():
        async with Accounts() as a:
            too_many = a_row(1, steps=400001)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0), too_many]))
            assert (error.kind, error.code) == ("row", "23514")
            assert error.message == 'new row for relation "walks" violates check constraint "walks_steps_check"'
            assert not error.pkey and not error.days_to_come and error.retry_after is None
            assert a.fake.walks(a.user) == []  # refused whole
            # The database shows the whole row with it - the account's id among it. None of that is kept.
            told = says(error)
            assert a.user not in told and "Failing row" not in told and too_many["id"] not in told
            assert sorted(vars(error)) == ["code", "days_to_come", "kind", "message", "pkey", "retry_after"]
            assert str(error) == "row" and repr(error) == "CallError('row')"

    run(scenario)


def test_the_stand_in_does_put_the_accounts_id_into_the_details_of_a_refusal():
    # (Or the test above would prove nothing.)
    async def scenario():
        async with Accounts() as a:
            answer = await a.client.request(
                "POST",
                "/rest/v1/walks",
                query=(("on_conflict", "user_id,start_key"), ("select", "id")),
                headers={"apikey": a.key, "Authorization": "Bearer " + a.token, "Prefer": PREFER},
                json_body=[a_row(0, steps=400001)],
            )
            assert answer.status == 400 and a.user in answer.json()["details"]

    run(scenario)


@pytest.mark.parametrize(
    ("changes", "code", "begins"),
    [
        ({"steps": 2600.5}, "22P02", "invalid input syntax for type integer"),
        ({"seconds": "many"}, "22P02", "invalid input syntax for type numeric"),
        ({"distance_m": 1e9}, "22003", "numeric field overflow"),
        ({"started_at": 1795300000}, "22008", "date/time field value out of range"),
        ({"local_day": "yesterday"}, "22007", "invalid input syntax for type date"),
        ({"kcal": None}, "23502", 'null value in column "kcal"'),
        ({"seconds": 86401.0}, "23514", 'new row for relation "walks" violates check constraint'),
        ({"session": "run"}, "23514", 'new row for relation "walks" violates check constraint "walks_session_check"'),
    ],
)
def test_every_refusal_whose_code_begins_22_or_23_is_about_a_row(changes, code, begins):
    async def scenario():
        async with Accounts() as a:
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0, **changes)]))
            assert (error.kind, error.code) == ("row", code)
            assert error.message.startswith(begins)
            assert not error.pkey and not error.days_to_come

    run(scenario)


def test_an_id_that_some_walk_has_already_says_pkey_so_that_a_new_id_can_be_tried():
    async def scenario():
        async with Accounts() as a:
            somebody = a.person("somebody.else@mail.example")
            taken = a.fake.add_walk(somebody, "2026-11-01T10:00:00Z")
            row = a_row(0, id=taken)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert (error.kind, error.code, error.pkey, error.days_to_come) == ("row", "23505", True, False)
            assert error.message == 'duplicate key value violates unique constraint "walks_pkey"'
            assert taken not in says(error)  # (the id stands in "details")
            # A new id, and it goes.
            again = {**row, "id": str(uuid.uuid4())}
            assert await calls.push(a.client, a.key, access_token=a.token, rows=[again]) == [again["id"]]

    run(scenario)


@pytest.mark.parametrize(
    ("status", "body", "pkey"),
    [
        (409, {"code": "23505", "details": "Key (id)=(x) already exists.", "message": 'unique "walks_pkey"'}, True),
        (409, {"code": "23505", "details": 'constraint "walks_pkey"', "message": "duplicate key"}, True),
        # The other rule of the table that an insert can break: the same start twice. No new id helps there.
        (409, {"code": "23505", "details": None, "message": 'unique constraint "walks_one_start"'}, False),
        (409, {"code": "23505", "details": ["walks_pkey"], "hint": "walks_pkey", "message": "duplicate key"}, False),
        (409, {"code": "23514", "details": "walks_pkey", "message": "walks_pkey"}, False),
    ],
)
def test_pkey_is_said_only_of_the_rule_that_an_id_is_new_in_the_whole_table(status, body, pkey):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", status, body)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.pkey) == ("row", pkey)

    run(scenario)


def test_a_walk_that_starts_in_the_days_to_come_says_so():
    async def scenario():
        async with Accounts() as a:
            ahead = datetime.fromtimestamp(NOW, timezone.utc) + timedelta(days=3)
            row = a_row(0, started_at=stamp(ahead), ended_at=stamp(ahead + timedelta(minutes=30)))
            row["local_day"] = ahead.strftime("%Y-%m-%d")
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert (error.kind, error.code, error.message) == ("row", "23514", DAYS_TO_COME)
            assert error.days_to_come is True and error.pkey is False
            # The clock is put right: the same walk goes.
            a.clock.now += 3 * 86400
            token = a.sign_in()["access_token"]  # (the first one ran out meanwhile)
            assert await calls.push(a.client, a.key, access_token=token, rows=[row]) == [row["id"]]

    run(scenario)


@pytest.mark.parametrize(
    ("status", "code", "message", "kind"),
    [
        (400, "23514", ADULTS, "not_open"),
        (409, "23514", ADULTS, "not_open"),
        (400, "23514", "There is room for 20000 rows of this kind in one account, and it is full.", "full"),
        # The two sentences count with the code the database gives them, and word for word.
        (400, "22000", ADULTS, "row"),
        (400, "23505", ADULTS, "row"),
        (400, "23514", ADULTS + " ", "row"),
        (400, "23514", "walka is for adults.", "row"),
        (400, "23514", "No. " + ADULTS, "row"),
        (400, "23505", "There is room for 20000 rows of this kind in one account, and it is full.", "row"),
        (400, "23514", "Sorry. There is room for 20000 rows of this kind in one account, and it is full.", "row"),
        (400, "23514", DAYS_TO_COME + " ", "row"),
        (400, "23505", DAYS_TO_COME, "row"),
        (409, "22008", DAYS_TO_COME, "row"),
        (400, "23514", "No. " + DAYS_TO_COME, "row"),
    ],
)
def test_the_two_refusals_that_are_about_the_account_are_told_from_those_about_a_row(status, code, message, kind):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", status, {"code": code, "details": None, "hint": None, "message": message})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code, error.days_to_come) == (kind, code, False)

    run(scenario)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"code": "23514", "details": None, "hint": None, "message": ADULTS}),
        (400, {"code": "23514", "details": None, "hint": None, "message": "There is room for 20000 rows"}),
        (409, {"code": "23505", "details": "walks_pkey", "hint": None, "message": "walks_pkey"}),
        (400, {"code": "22P02", "details": None, "hint": None, "message": "invalid input syntax"}),
        (400, {"code": "23514", "details": None, "hint": None, "message": DAYS_TO_COME}),
    ],
)
def test_where_no_walk_was_sent_no_answer_is_about_a_row(status, body):
    async def scenario():
        async with Accounts() as a:
            for path, ask in (
                ("/rest/v1/rpc", lambda: calls.schema_version(a.client, a.key, access_token=a.token)),
                ("/rest/v1/profiles", lambda: calls.first_step(a.client, a.key, access_token=a.token)),
                ("/rest/v1/profiles", lambda: calls.page(a.client, a.key, access_token=a.token)),
            ):
                a.fake.fail_next(path, status, body)
                error = await refused(ask())
                assert (error.kind, error.code, error.pkey, error.days_to_come) == ("stop", body["code"], False, False)

    run(scenario)


STOPS = [
    (400, {"code": "PGRST102", "details": None, "hint": None, "message": "All object keys must match"}, "PGRST102"),
    (400, {"code": "PGRST204", "details": None, "hint": None, "message": "Could not find the column"}, "PGRST204"),
    (400, {"code": "428C9", "details": "generated", "hint": None, "message": "cannot insert"}, "428C9"),
    (400, {"code": "21000", "details": None, "hint": None, "message": "UPDATE requires a WHERE clause"}, "21000"),
    (401, {"code": "PGRST301", "details": None, "hint": None, "message": "No suitable key"}, "PGRST301"),
    (401, {"code": "42501", "details": None, "hint": None, "message": "permission denied"}, "42501"),
    (401, {"message": "Invalid API key", "hint": "Double check your key."}, ""),
    (403, {"code": "42501", "details": None, "hint": None, "message": "permission denied"}, "42501"),
    (403, b"<html>Forbidden</html>", ""),
    (404, {"code": "PGRST205", "details": None, "hint": None, "message": "Could not find the table"}, "PGRST205"),
    (404, b"<html>Not found</html>", ""),
    (400, b"<html>Bad request</html>", ""),
    (400, None, ""),
    (400, [{"code": "23514", "message": ADULTS}], ""),
    (400, "Walka is for adults.", ""),
    # The codes of a row count with the two statuses the database gives them, and with no other.
    (403, {"code": "23514", "details": None, "hint": None, "message": ADULTS}, "23514"),
    (422, {"code": "23505", "details": "walks_pkey", "hint": None, "message": "walks_pkey"}, "23505"),
    (200, {"code": "23514", "details": None, "hint": None, "message": ADULTS}, "23514"),
    # The token ran out only when the database says so with a 401.
    (403, {"code": "PGRST303", "message": "JWT expired"}, "PGRST303"),
    (400, {"code": "PGRST303", "message": "JWT expired"}, "PGRST303"),
    (301, None, ""),
    (302, {"code": "23514", "message": ADULTS}, "23514"),
    (307, b"<html>Moved</html>", ""),
    (204, None, ""),
    (202, [], ""),
    (206, [], ""),
    (418, {"code": "PGRST000", "message": "tea"}, "PGRST000"),
]


@pytest.mark.parametrize(("status", "body", "code"), STOPS, ids=[f"{status}-{code}" for status, _body, code in STOPS])
def test_anything_else_says_nothing_about_a_walk_and_stops(status, body, code):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", status, body, headers={"Location": "/rest/v1/walks"})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code) == ("stop", code)
            assert not error.pkey and not error.days_to_come and error.retry_after is None
            assert len(a.fake.requests) == 1  # asked once: a redirect is not followed, nothing is tried again
            assert a.fake.walks(a.user) == []

    run(scenario)


def test_the_requests_built_wrongly_stop_as_the_stand_in_itself_answers_them():
    # (The stand-in answers a bearer nobody gave out, and a key that is not the project's, by itself.)
    async def scenario():
        async with Accounts() as a:
            error = await refused(calls.push(a.client, a.key, access_token=TOKEN, rows=[a_row(0)]))
            assert (error.kind, error.code) == ("stop", "PGRST301")
            error = await refused(calls.push(a.client, "another-key", access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code, error.message) == ("stop", "", "Invalid API key")
            error = await refused(calls.first_step(a.client, a.key, access_token=TOKEN))
            assert (error.kind, error.code) == ("stop", "PGRST301")

    run(scenario)


def test_a_200_where_a_201_belongs_stops_though_the_ids_are_the_ones_sent():
    async def scenario():
        async with Accounts() as a:
            row = a_row(0)
            a.fake.fail_next("/rest/v1/walks", 200, [{"id": row["id"]}])
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert error.kind == "stop"

    run(scenario)


@pytest.mark.parametrize(
    "answer",
    [
        lambda sent: [{"id": SOME_ID}],  # an id that was not sent
        lambda sent: [{"id": sent[0]}, {"id": SOME_ID}],
        lambda sent: [{"id": sent[0]}, {"id": sent[0]}],  # the same one twice
        lambda sent: [{"id": sent[0].upper()}],
        lambda sent: [{"id": sent[0]}, {"id": sent[1]}, {"id": sent[0]}],  # more than were sent
        lambda sent: [sent[0]],
        lambda sent: [[sent[0]]],
        lambda sent: [{"ID": sent[0]}],
        lambda sent: [{"id": None}],
        lambda sent: [{"id": 7}],
        lambda sent: [{}],
        lambda sent: {"id": sent[0]},
        lambda sent: sent[0],
        lambda sent: None,
        lambda sent: 2,
        lambda sent: b"<html>Created</html>",
        lambda sent: b'[{"id": "' + sent[0].encode() + b'"}',
    ],
    ids=lambda answer: "",
)
def test_a_201_with_anything_but_ids_that_were_sent_stops(answer):
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(0), a_row(1)]
            a.fake.fail_next("/rest/v1/walks", 201, answer([row["id"] for row in rows]))
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=rows))
            assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


def test_of_a_row_that_comes_back_only_its_id_is_read():
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(0), a_row(1)]
            back = [{"id": rows[1]["id"], "user_id": a.user, "start_key": "2026-11-19T19:30:00", "steps": 2600}]
            a.fake.fail_next("/rest/v1/walks", 201, back)
            assert await calls.push(a.client, a.key, access_token=a.token, rows=rows) == [rows[1]["id"]]

    run(scenario)


@pytest.mark.parametrize(
    ("status", "headers", "retry_after"),
    [
        (500, None, None),
        (502, None, None),
        (503, {"Retry-After": "120"}, 120.0),
        (504, None, None),
        (599, None, None),
        (429, None, None),
        (429, {"Retry-After": "30"}, 30.0),
        (429, {"Retry-After": "0"}, 0.0),
        (429, {"Retry-After": "3600"}, 3600.0),
        (429, {"Retry-After": "3601"}, None),  # longer than an hour is not believed
        (429, {"Retry-After": "Sat, 21 Nov 2026 23:00:00 GMT"}, None),  # a date would be read by this computer's clock
        (429, {"Retry-After": "-5"}, None),
    ],
)
def test_a_busy_or_broken_server_means_later(status, headers, retry_after):
    async def scenario():
        async with Accounts() as a:
            # Whatever such an answer says besides is not gone by.
            body = {"code": "23514", "details": None, "hint": None, "message": ADULTS}
            for path, ask in (
                ("/rest/v1/walks", lambda: calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)])),
                ("/rest/v1/rpc", lambda: calls.schema_version(a.client, a.key, access_token=a.token)),
                ("/rest/v1/profiles", lambda: calls.first_step(a.client, a.key, access_token=a.token)),
                ("/rest/v1/profiles", lambda: calls.page(a.client, a.key, access_token=a.token)),
                ("/auth/v1/user", lambda: calls.who(a.client, a.key, access_token=a.token)),
                ("/auth/v1/token", lambda: calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")),
                ("/auth/v1/token", lambda: calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER)),
            ):
                a.fake.fail_next(path, status, body, headers=headers)
                error = await refused(ask())
                assert (error.kind, error.retry_after, error.code, error.message) == ("later", retry_after, "", "")
            assert a.fake.walks(a.user) == []

    run(scenario)


def test_no_answer_means_later():
    async def scenario():
        async with Accounts() as a:
            a.fake.drop_next("/rest/v1/walks")
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.retry_after) == ("later", None)
            a.fake.drop_next("/auth/v1/token")
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert error.kind == "later"
            a.fake.drop_next("/auth/v1/token")
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert error.kind == "later"
            # The rows were added, and the answer was lost on its way: later, and then they are "there".
            row = a_row(0)
            a.fake.drop_next("/rest/v1/walks", after=True)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert error.kind == "later"
            assert await calls.push(a.client, a.key, access_token=a.token, rows=[{**row, "id": str(uuid.uuid4())}]) == []
            a.fake.drop_next("/auth/v1/user", times=2)  # (twice: aiohttp asks a GET once more by itself)
            error = await refused(calls.who(a.client, a.key, access_token=a.token))
            assert error.kind == "later"
        # Nobody is there at all.
        nobody = Client(Origin("http", "127.0.0.1", unused_port()))
        error = await refused(calls.schema_version(nobody, "test-key", access_token=TOKEN))
        assert error.kind == "later"

    run(scenario)


def test_an_answer_that_takes_too_long_means_later(monkeypatch):
    monkeypatch.setattr(calls, "ASK_S", 0.5)
    monkeypatch.setattr(calls, "EXCHANGE_S", 0.5)

    async def scenario():
        async with Accounts() as a:
            gate = a.fake.hold("/auth/v1/token")
            error = await refused(calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER))
            assert error.kind == "later" and gate.arrived.is_set()
            gate = a.fake.hold("/rest/v1/walks")
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert error.kind == "later" and gate.arrived.is_set()
            await asyncio.wait_for(gate.left.wait(), 5)
            assert a.fake.walks(a.user) == []

    run(scenario)


def test_a_certificate_that_cannot_be_checked_means_nothing_is_asked():
    async def scenario():
        # (A client for https whose list of root certificates cannot be made: it asks nobody.)
        unchecked = Client(Origin("https", "accounts.example", None), context_factory=lambda: None)
        for asking in (
            calls.push(unchecked, "test-key", access_token=TOKEN, rows=[a_row(0)]),
            calls.schema_version(unchecked, "test-key", access_token=TOKEN),
            calls.first_step(unchecked, "test-key", access_token=TOKEN),
            calls.page(unchecked, "test-key", access_token=TOKEN),
            calls.who(unchecked, "test-key", access_token=TOKEN),
            calls.refresh(unchecked, "test-key", refresh_token="abcdefghijkl"),
            calls.exchange(unchecked, "test-key", code="some-code", verifier=VERIFIER),
        ):
            error = await refused(asking)
            assert (error.kind, error.code, error.message, error.retry_after) == ("certificate", "", "", None)
        assert await calls.find(unchecked, "test-key", access_token=TOKEN, start_keys=["2026-11-19T20:30:00"]) == {}
        assert await calls.logout(unchecked, "test-key", access_token=TOKEN) is None

    run(scenario)


@pytest.mark.parametrize(
    ("gives", "kind"),
    [
        (NetError("certificate"), "certificate"),
        (NetError("no_answer"), "later"),
        (NetError("too_big"), "stop"),
        (NetError("address"), "stop"),
        (NetError("something new"), "stop"),
        # The client raises nothing else. If it ever does, nothing of that error gets out.
        (RuntimeError("GET https://accounts.example/x Authorization: Bearer " + TOKEN), "later"),
        (OSError(111, "Connect call failed ('127.0.0.1', 54321)"), "later"),
        (asyncio.TimeoutError(), "later"),
        (ValueError(TOKEN), "later"),
        # And what is no answer is not read as one.
        ({"status": 201, "body": []}, "stop"),
        (None, "stop"),
    ],
    ids=lambda value: value if isinstance(value, str) else type(value).__name__,
)
def test_what_the_client_says_instead_of_an_answer_becomes_one_word(gives, kind):
    async def scenario():
        double = Double(gives)
        for asking in (
            calls.push(double, "test-key", access_token=TOKEN, rows=[a_row(0)]),
            calls.schema_version(double, "test-key", access_token=TOKEN),
            calls.first_step(double, "test-key", access_token=TOKEN),
            calls.page(double, "test-key", access_token=TOKEN),
            calls.who(double, "test-key", access_token=TOKEN),
            calls.refresh(double, "test-key", refresh_token="abcdefghijkl"),
            calls.exchange(double, "test-key", code="some-code", verifier=VERIFIER),
        ):
            error = await refused(asking)
            assert (error.kind, error.code, error.message) == (kind, "", "")
            assert error.args == (kind,)
        assert await calls.find(double, "test-key", access_token=TOKEN, start_keys=["2026-11-19T20:30:00"]) == {}
        assert len(double.asked) == 8

    run(scenario)


def test_a_huge_answer_is_not_read_into_memory_and_stops():
    async def scenario():
        async with Accounts() as a:
            row = a_row(0)
            # Longer than Walka reads: 400,000 bytes of ids.
            long = b"[" + (b'{"id":"' + row["id"].encode() + b'"},') * 8000 + b"0]"
            assert len(long) > 262_144
            a.fake.fail_next("/rest/v1/walks", 201, long)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert error.kind == "stop"
            # Short enough to be read, and nested without end.
            deep = b"[" * 100_000 + b"]" * 100_000
            for status in (201, 400):
                a.fake.fail_next("/rest/v1/walks", status, deep)
                error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
                assert error.kind == "stop"
            a.fake.fail_next("/auth/v1/token", 200, long)
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert error.kind == "stop"
            a.fake.fail_next("/auth/v1/token", 400, deep)
            error = await refused(calls.refresh(a.client, a.key, refresh_token="abcdefghijkl"))
            assert error.kind == "stop"
            a.fake.fail_next("/rest/v1/walks", 200, long)
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=[start_of(row)]) == {}
            # Many more ids than rows were sent, within what is read: not gone through one by one.
            many = b"[" + b'{"id":1},' * 25000 + b'{"id":1}]'
            a.fake.fail_next("/rest/v1/walks", 201, many)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[row]))
            assert error.kind == "stop"

    run(scenario)


# ---- what is kept of a refusal -------------------------------------------------------------------


def test_a_message_is_cut_short_and_cleaned_and_details_and_hint_are_never_kept():
    async def scenario():
        async with Accounts() as a:
            said = {
                "code": "23514",
                "details": "D-E-T-A-I-L-S " + a.user,
                "hint": "H-I-N-T",
                "message": "bad\x1b[31m row\r\n\x00\u202e\u200b\u2028 " + "x" * 1000,
                "more": "M-O-R-E",
            }
            a.fake.fail_next("/rest/v1/walks", 400, said)
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code) == ("row", "23514")
            assert len(error.message) == 300 and error.message.startswith("bad[31m row " + "xxx")
            told = says(error)
            assert "D-E-T-A-I-L-S" not in told and "H-I-N-T" not in told and "M-O-R-E" not in told

    run(scenario)


@pytest.mark.parametrize(
    ("code", "kept"),
    [
        ("23514", "23514"),
        ("PGRST_1", "PGRST_1"),
        ("2" * 32, "2" * 32),
        ("2" * 33, ""),
        # A refusal is about a row only with a code of the database's own kind: five characters.
        ("22", "22"),
        ("2351", "2351"),
        ("23514x", "23514x"),
        ("22_or_so", "22_or_so"),
        ("22P02", "22P02"),
        ("23514 or so", ""),
        ("23514\n", ""),
        ("23514; drop", ""),
        ("२३५१४", ""),
        ("", ""),
        (23514, ""),
        (None, ""),
        (["23514"], ""),
    ],
    ids=lambda value: ascii(value)[:12],
)
def test_a_code_is_kept_only_if_it_is_shaped_like_one(code, kept):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", 400, {"code": code, "details": None, "hint": None, "message": "no"})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert error.code == kept
            assert error.kind == ("row" if len(kept) == 5 and kept.startswith(("22", "23")) else "stop")

    run(scenario)


@pytest.mark.parametrize("message", [None, 7, ["Walka is for adults."], {"text": "no"}, True])
def test_a_message_that_is_no_text_is_no_message(message):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", 400, {"code": "23514", "message": message})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code, error.message) == ("row", "23514", "")

    run(scenario)


def test_an_answer_that_holds_a_key_of_the_request_is_not_kept_at_all():
    # (No honest server says a key back. If one does, the words would end in a file: so they are dropped.)
    async def scenario():
        async with Accounts() as a:
            token = a.token
            for message in (f"the bearer {token} is not taken", f"apikey {a.key}", f"{a.key} and {token}"):
                said = {"code": "23514", "details": None, "hint": None, "message": message}
                a.fake.fail_next("/rest/v1/walks", 400, said)
                error = await refused(calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]))
                assert (error.kind, error.code, error.message) == ("stop", "", "")
            # A short token would fit where the code stands.
            short = "shorttoken12"
            NEEDLES.append(short)
            a.fake.fail_next("/rest/v1/walks", 400, {"code": short, "details": None, "hint": None, "message": "no"})
            error = await refused(calls.push(a.client, a.key, access_token=short, rows=[a_row(0)]))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            # The sign-in service's refusals alike: the refresh token, the code, the verifier.
            again, code = "abcdefghijkl", "one-time-code"
            NEEDLES.extend([again, code, VERIFIER])
            a.fake.fail_next("/auth/v1/token", 400, {"error_code": "refresh_token_not_found", "msg": f"{again} is gone"})
            error = await refused(calls.refresh(a.client, a.key, refresh_token=again))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            a.fake.fail_next("/auth/v1/token", 400, {"error_code": again, "msg": "gone"})
            error = await refused(calls.refresh(a.client, a.key, refresh_token=again))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            for message in (f"not {VERIFIER}", f"not {code}"):
                a.fake.fail_next("/auth/v1/token", 400, {"error_code": "bad_code_verifier", "msg": message})
                error = await refused(calls.exchange(a.client, a.key, code=code, verifier=VERIFIER))
                assert (error.kind, error.code, error.message) == ("bad_code", "", "")
            a.fake.fail_next("/auth/v1/user", 403, {"error_code": "user_not_found", "msg": f"{token} names nobody"})
            error = await refused(calls.who(a.client, a.key, access_token=token))
            assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


@pytest.mark.parametrize("unseen", ["\u200b", "\x00", "\u202e", "\n", "\u2800", "\ufe0f", "\x1b"])
def test_a_key_with_a_letter_that_does_not_show_in_its_middle_is_not_kept_either(unseen):
    # (Cleaned, such a message would hold the key whole again - and a message that is kept reaches a file.)
    async def scenario():
        async with Accounts() as a:
            token = a.token
            broken = token[:20] + unseen + token[20:]
            assert token not in broken
            said = {"code": "23514", "details": None, "hint": None, "message": f"the bearer {broken} is not taken"}
            a.fake.fail_next("/rest/v1/walks", 400, said)
            error = await refused(calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            again = "abcdefghijkl"
            NEEDLES.append(again)
            said = {"code": 400, "error_code": "refresh_token_not_found", "msg": f"abcdef{unseen}ghijkl is gone"}
            a.fake.fail_next("/auth/v1/token", 400, said)
            error = await refused(calls.refresh(a.client, a.key, refresh_token=again))
            assert (error.kind, error.code, error.message) == ("stop", "", "")
            said = {"code": 403, "error_code": "bad_jwt", "msg": f"{broken} has run out"}
            a.fake.fail_next("/auth/v1/user", 403, said)
            error = await refused(calls.who(a.client, a.key, access_token=token))
            assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


def test_a_key_that_is_whole_only_once_an_id_is_taken_out_of_the_message_is_not_kept_either():
    async def scenario():
        async with Accounts() as a:
            token = "a-token-with...dots-in-it"
            NEEDLES.append(token)
            message = f"a-token-with{SOME_ID}dots-in-it"
            a.fake.fail_next("/rest/v1/walks", 400, {"code": "23514", "details": None, "hint": None, "message": message})
            error = await refused(calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]))
            assert (error.kind, error.code, error.message) == ("stop", "", "")

    run(scenario)


def test_an_id_in_a_message_is_taken_out():
    async def scenario():
        async with Accounts() as a:
            said = f"Key (user_id)=({a.user}) is not present, nor is {a.user.upper()}."
            a.fake.fail_next("/rest/v1/walks", 409, {"code": "23503", "details": None, "hint": None, "message": said})
            error = await refused(calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)]))
            assert (error.kind, error.code) == ("row", "23503")
            assert error.message == "Key (user_id)=(...) is not present, nor is ...."

    run(scenario)


def test_a_call_error_says_its_kind_and_keeps_nothing_it_was_not_made_to_keep():
    error = CallError("later", retry_after=30)
    assert (error.kind, error.code, error.message, error.retry_after) == ("later", "", "", 30.0)
    assert (error.pkey, error.days_to_come) == (False, False)
    assert str(error) == "later" and repr(error) == "CallError('later')"
    # Made by hand, it is held to the same rules.
    error = CallError("row", "23505; --", "one\x00 two\u202e " + SOME_ID, retry_after=30, pkey=True, days_to_come=1)
    assert (error.code, error.message, error.retry_after) == ("", "one two ...", None)
    assert (error.pkey, error.days_to_come) == (True, False)
    error = CallError("stop", "PGRST204", "x" * 301, pkey=True, days_to_come=True)
    assert (error.code, len(error.message), error.pkey, error.days_to_come) == ("PGRST204", 300, False, False)
    for unfit in (3601, -1, True, "30", float("nan"), float("inf")):
        assert CallError("later", retry_after=unfit).retry_after is None
    for kind in calls.KINDS:
        assert CallError(kind).kind == kind
    for unknown in ("", "Later", "sign-in over", None):
        with pytest.raises(ValueError):
            CallError(unknown)
    assert set(calls.KINDS) == {
        "later",
        "certificate",
        "token_ran_out",
        "sign_in_over",
        "bad_code",
        "not_open",
        "full",
        "row",
        "stop",
    }


# ---- keys that are never sent --------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "two words", "sb_secret_abcdefghijklmnop", "k" * 513, "schlüssel", None, 7])
def test_a_key_that_is_not_the_publishable_one_is_never_sent(key):
    async def scenario():
        async with Accounts() as a:
            token = a.token
            for asking in (
                calls.push(a.client, key, access_token=token, rows=[a_row(0)]),
                calls.schema_version(a.client, key, access_token=token),
                calls.first_step(a.client, key, access_token=token),
                calls.page(a.client, key, access_token=token),
                calls.who(a.client, key, access_token=token),
                calls.refresh(a.client, key, refresh_token="abcdefghijkl"),
                calls.exchange(a.client, key, code="some-code", verifier=VERIFIER),
            ):
                assert (await refused(asking)).kind == "stop"
            assert await calls.find(a.client, key, access_token=token, start_keys=["2026-11-19T20:30:00"]) == {}
            assert await calls.logout(a.client, key, access_token=token) is None
            assert a.fake.requests == []

    run(scenario)


@pytest.mark.parametrize("token", ["", "two words", "line\nend", "x" * 4097, "tökén", None, 7, b"a.b.c"])
def test_a_token_that_is_not_shaped_like_one_is_never_sent(token):
    async def scenario():
        async with Accounts() as a:
            for asking in (
                calls.push(a.client, a.key, access_token=token, rows=[a_row(0)]),
                calls.schema_version(a.client, a.key, access_token=token),
                calls.first_step(a.client, a.key, access_token=token),
                calls.page(a.client, a.key, access_token=token),
                calls.who(a.client, a.key, access_token=token),
                calls.refresh(a.client, a.key, refresh_token=token),
            ):
                assert (await refused(asking)).kind == "stop"
            assert await calls.find(a.client, a.key, access_token=token, start_keys=["2026-11-19T20:30:00"]) == {}
            assert await calls.logout(a.client, a.key, access_token=token) is None
            assert a.fake.requests == []

    run(scenario)


# ---- looking up walks that did not come back -----------------------------------------------------


def test_a_look_up_tells_which_row_a_walk_has_and_whether_it_was_deleted_there():
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(0), a_row(1), a_row(2)]
            ids = await calls.push(a.client, a.key, access_token=a.token, rows=rows)
            a.fake.mark_deleted(a.user, ids[1])
            never_sent = start_of(a_row(7))
            keys = [start_of(row) for row in rows] + [never_sent]
            found = await calls.find(a.client, a.key, access_token=a.token, start_keys=keys)
            assert found == {keys[0]: (ids[0], False), keys[1]: (ids[1], True), keys[2]: (ids[2], False)}
            asked = a.fake.requests[-1]
            assert (asked.method, asked.path) == ("GET", "/rest/v1/walks")
            assert asked.query == [("start_key", "in.(" + ",".join(keys) + ")"), ("select", "id,start_key,deleted_at")]
            assert asked.header("Authorization") == "Bearer " + a.token and asked.header("Prefer") is None
            assert asked.body == b""
            # Somebody else's walk at the same start is not this account's.
            other = a.person("other@mail.example")
            token = a.sign_in(other)["access_token"]
            assert await calls.find(a.client, a.key, access_token=token, start_keys=keys) == {}

    run(scenario)


def test_a_look_up_asks_for_fifty_starts_at_a_time_and_for_each_once():
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(number) for number in range(120)]
            ids = await calls.push(a.client, a.key, access_token=a.token, rows=rows)
            keys = [start_of(row) for row in rows]
            found = await calls.find(a.client, a.key, access_token=a.token, start_keys=keys + keys[:10])
            assert found == {key: (row_id, False) for key, row_id in zip(keys, ids, strict=True)}
            asked = [seen for seen in a.fake.requests if seen.method == "GET"]
            sizes = [len(dict(seen.query)["start_key"][4:-1].split(",")) for seen in asked]
            assert sizes == [50, 50, 20]
            # Nothing to look up: nothing is asked.
            before = len(a.fake.requests)
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=[]) == {}
            assert len(a.fake.requests) == before

    run(scenario)


@pytest.mark.parametrize(
    "start_keys",
    [
        ["2026-11-19T20:30:00)", "2026-11-19T20:30:00,2026-11-19T21:30:00", "2026-11-19 20:30:00"],
        ["2026-11-19T20:30:00.000Z", "2026-11-19T20:30:00Z", "2026-11-19T20:30", "", "*", "२०२६-११-१९T२०:३०:००"],
        ['"2026-11-19T20:30:00"', "2026-11-19T20:30:00&select=*", "(2026-11-19T20:30:00"],
        [None, 7, ["2026-11-19T20:30:00"], b"2026-11-19T20:30:00"],
        [{"start": "2026-11-19T20:30:00"}],
        None,
        7,
    ],
    ids=lambda value: "",
)
def test_only_a_start_written_as_walka_writes_one_goes_into_a_look_up(start_keys):
    async def scenario():
        async with Accounts() as a:
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=start_keys) == {}
            assert a.fake.requests == []

    run(scenario)


def test_an_unfit_start_among_good_ones_is_left_out_and_the_others_are_asked():
    async def scenario():
        async with Accounts() as a:
            row = a_row(0)
            ids = await calls.push(a.client, a.key, access_token=a.token, rows=[row])
            keys = ["2026-11-19T20:30:00)", start_of(row), None, ["2026-11-19T19:30:00"], {"a": 1}, 7, start_of(row)]
            found = await calls.find(a.client, a.key, access_token=a.token, start_keys=keys)
            assert found == {start_of(row): (ids[0], False)}
            assert dict(a.fake.requests[-1].query)["start_key"] == "in.(" + start_of(row) + ")"
            # Handed over as a tuple, or as whatever gives them one by one: the same.
            before = len(a.fake.requests)
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=(start_of(row),)) == found
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=iter([start_of(row)])) == found
            assert len(a.fake.requests) == before + 2

    run(scenario)


KEY = "2026-11-19T20:30:00"
OTHER_KEY = "2026-11-19T19:30:00"


@pytest.mark.parametrize(
    ("body", "found"),
    [
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": None}], {KEY: (SOME_ID, False)}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20T10:00:00+00:00"}], {KEY: (SOME_ID, True)}),
        # However the database writes the column: with a blank, with decimals, with a zone.
        ([{"id": SOME_ID, "start_key": "2026-11-19 20:30:00", "deleted_at": None}], {KEY: (SOME_ID, False)}),
        ([{"id": SOME_ID, "start_key": KEY + ".000000", "deleted_at": None}], {KEY: (SOME_ID, False)}),
        ([{"id": SOME_ID, "start_key": KEY + "+00:00", "deleted_at": None, "user_id": "x"}], {KEY: (SOME_ID, False)}),
        ([], {}),
        # What cannot be read gives nothing - and nothing is guessed from the half that could be.
        ([{"id": SOME_ID, "start_key": KEY}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": False}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": 0}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": ""}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "x" * 65}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "false"}], {}),  # only a moment says "deleted"
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "no"}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": " "}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20"}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20T10:00:00+00:00, or not"}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20T10:00:00+00:00\n"}], {}),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20 10:00:00.5+00"}], {KEY: (SOME_ID, True)}),
        ([{"id": SOME_ID.upper(), "start_key": KEY, "deleted_at": None}], {}),
        ([{"id": "20261119-213000", "start_key": KEY, "deleted_at": None}], {}),
        ([{"id": None, "start_key": KEY, "deleted_at": None}], {}),
        ([{"start_key": KEY, "deleted_at": None}], {}),
        ([{"id": SOME_ID, "start_key": "2026-11-19T20:30", "deleted_at": None}], {}),
        ([{"id": SOME_ID, "start_key": "2026-11-18T20:30:00", "deleted_at": None}], {}),  # not asked for
        ([{"id": SOME_ID, "start_key": 1795300000, "deleted_at": None}], {}),
        ([{"id": SOME_ID, "start_key": None, "deleted_at": None}], {}),
        ([{"id": SOME_ID, "deleted_at": None}], {}),
        (
            [
                {"id": SOME_ID, "start_key": KEY, "deleted_at": None},
                {"id": SOME_ID, "start_key": KEY, "deleted_at": "2026-11-20T10:00:00+00:00"},
            ],
            {},
        ),
        (
            [
                {"id": SOME_ID, "start_key": KEY, "deleted_at": None},
                {"id": SOME_ID, "start_key": OTHER_KEY, "deleted_at": None},
                {"id": SOME_ID, "start_key": KEY, "deleted_at": None},
            ],
            {},
        ),
        ([{"id": SOME_ID, "start_key": KEY, "deleted_at": None}, "a row"], {}),
        ([[SOME_ID, KEY, None]], {}),
        ({"id": SOME_ID, "start_key": KEY, "deleted_at": None}, {}),
        ("nothing", {}),
        (None, {}),
        (b"<html>Walks</html>", {}),
    ],
    ids=lambda value: "",
)
def test_a_look_up_reads_only_an_answer_shaped_as_the_database_gives_it(body, found):
    async def scenario():
        async with Accounts() as a:
            a.fake.fail_next("/rest/v1/walks", 200, body)
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=[KEY, OTHER_KEY]) == found

    run(scenario)


def test_a_look_up_that_fails_gives_nothing_and_raises_nothing():
    async def scenario():
        async with Accounts() as a:
            row = a_row(0)
            await calls.push(a.client, a.key, access_token=a.token, rows=[row])
            keys = [start_of(row)]
            good = [{"id": SOME_ID, "start_key": keys[0], "deleted_at": None}]
            for status, body in (
                (500, None),
                (503, b"<html>Busy</html>"),
                (429, None),
                (400, {"code": "22007", "details": None, "hint": None, "message": "invalid input syntax"}),
                (401, {"code": "PGRST303", "message": "JWT expired"}),
                (403, None),
                (404, None),
                (302, None),
                (201, good),
                (206, good),
            ):
                a.fake.fail_next("/rest/v1/walks", status, body)
                assert await calls.find(a.client, a.key, access_token=a.token, start_keys=keys) == {}, status
            # (Twice: aiohttp itself asks a GET once more when the line is cut. It never does that with a POST.)
            a.fake.drop_next("/rest/v1/walks", times=2)
            assert await calls.find(a.client, a.key, access_token=a.token, start_keys=keys) == {}
            token = a.token
            a.fake.expire_access_tokens()
            assert await calls.find(a.client, a.key, access_token=token, start_keys=keys) == {}
            assert len(a.fake.walks(a.user)) == 1  # (and a look-up changes nothing)

    run(scenario)


class SecondFails:
    """The stand-in's client, but its second request gets what the test says: an error, or an answer."""

    def __init__(self, client: Client, gives: Any) -> None:
        self.client = client
        self.gives = gives
        self.count = 0

    async def request(self, method, path, **how):
        self.count += 1
        if self.count != 2:
            return await self.client.request(method, path, **how)
        if isinstance(self.gives, BaseException):
            raise self.gives
        return self.gives


@pytest.mark.parametrize(
    "gives",
    [
        NetError("no_answer"),
        Answer(500, None, b""),
        Answer(200, None, b"<html>Walks</html>"),
        Answer(200, None, b'[{"id": "' + SOME_ID.encode() + b'", "start_key": "2001-01-01T00:00:00", "deleted_at": null}]'),
        # A start that IS looked up, but by the request after this one: not the answer to this one.
        Answer(
            200,
            None,
            b'[{"id": "' + SOME_ID.encode() + b'", "start_key": "' + start_of(a_row(110)).encode() + b'", "deleted_at": null}]',
        ),
    ],
    ids=["no answer", "500", "a page", "a row nobody asked for", "a row the next request asks for"],
)
def test_a_look_up_that_fails_half_way_keeps_what_it_has_and_asks_no_further(gives):
    async def scenario():
        async with Accounts() as a:
            rows = [a_row(number) for number in range(120)]
            ids = await calls.push(a.client, a.key, access_token=a.token, rows=rows)
            keys = [start_of(row) for row in rows]
            client = SecondFails(a.client, gives)
            found = await calls.find(client, a.key, access_token=a.token, start_keys=keys)
            assert found == {key: (row_id, False) for key, row_id in zip(keys[:50], ids[:50], strict=True)}
            assert client.count == 2  # the last twenty were not asked for

    run(scenario)


# ---- several at once, and going away in the middle -----------------------------------------------


def test_requests_made_at_the_same_time_each_get_their_own_answer_and_leave_nothing_behind():
    async def scenario():
        async with Accounts() as a:
            other = a.person("other@mail.example", adult=False)
            token, others = a.token, a.sign_in(other)["access_token"]
            rows = [a_row(0), a_row(1)]
            before = set(asyncio.all_tasks())
            gate = a.fake.hold("/auth/v1/user")  # (the first asked is the last answered)
            asked = asyncio.gather(
                calls.who(a.client, a.key, access_token=token),
                calls.push(a.client, a.key, access_token=token, rows=rows),
                calls.first_step(a.client, a.key, access_token=token),
                calls.first_step(a.client, a.key, access_token=others),
                calls.schema_version(a.client, a.key, access_token=others),
                calls.push(a.client, a.key, access_token=others, rows=[a_row(2)]),
                return_exceptions=True,
            )
            await asyncio.wait_for(gate.arrived.wait(), 5)
            while len(a.fake.requests) < 6:
                await asyncio.sleep(0.01)
            gate.release()
            there, added, taken, not_taken, version, refusal = await asked
            assert (there, added, taken, not_taken, version) == ("there", [row["id"] for row in rows], True, None, 1)
            assert isinstance(refusal, CallError) and refusal.kind == "not_open"
            CAUGHT.append(refusal)
            assert len(a.fake.walks(a.user)) == 2 and a.fake.walks(other) == []
            assert await settled(before)

    run(scenario)


@pytest.mark.parametrize(
    ("path", "ask"),
    [
        ("/rest/v1/walks", lambda a: calls.push(a.client, a.key, access_token=a.token, rows=[a_row(0)])),
        ("/rest/v1/walks", lambda a: calls.find(a.client, a.key, access_token=a.token, start_keys=[KEY])),
        ("/auth/v1/logout", lambda a: calls.logout(a.client, a.key, access_token=a.token)),
        ("/auth/v1/token", lambda a: calls.exchange(a.client, a.key, code="some-code", verifier=VERIFIER)),
        ("/auth/v1/token", lambda a: calls.refresh(a.client, a.key, refresh_token="abcdefghijkl")),
        ("/rest/v1/profiles", lambda a: calls.first_step(a.client, a.key, access_token=a.token)),
        ("/rest/v1/profiles", lambda a: calls.page(a.client, a.key, access_token=a.token)),
        ("/rest/v1/rpc", lambda a: calls.schema_version(a.client, a.key, access_token=a.token)),
        ("/auth/v1/user", lambda a: calls.who(a.client, a.key, access_token=a.token)),
    ],
    ids=["push", "find", "logout", "exchange", "refresh", "first_step", "page", "schema_version", "who"],
)
def test_a_request_that_is_cancelled_ends_at_once_and_leaves_nothing_behind(path, ask):
    async def scenario():
        async with Accounts() as a:
            assert a.token  # (signed in before the count begins)
            before = set(asyncio.all_tasks())
            gate = a.fake.hold(path)
            asking = asyncio.ensure_future(ask(a))
            await asyncio.wait_for(gate.arrived.wait(), 5)
            asking.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asking, 5)
            await asyncio.wait_for(gate.left.wait(), 5)
            assert await settled(before)
            assert a.fake.walks(a.user) == []

    run(scenario)
