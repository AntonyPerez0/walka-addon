"""The stand-in for the accounts (tests/fake_accounts.py), tried by itself.

Every later test of Walka's sending runs against the stand-in, so what it
answers is pinned here, answer by answer, with aiohttp's plain client and
nothing of the feature. The answers are those of the website's
docs/calls-tried.md ("item 9" is its ninth numbered item), its
docs/data-model.md and the table of walks in its migration.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
import pytest
from aiohttp import test_utils, web
from multidict import CIMultiDict

import fake_accounts
from fake_accounts import (
    ADULTS,
    DAYS_TO_COME,
    DENIED_DESCRIPTION,
    PREFER,
    ROW_KEYS,
    FakeAccounts,
    Seen,
    waits_for_a_caller,
)

NOW = 1795300000.0  # 21 November 2026, 22:26:40 UTC
PUSH = {"on_conflict": "user_id,start_key", "select": "id"}
FIND = "id,start_key,deleted_at"
VERIFIER = "v" * 43  # the shortest a verifier may be
_NOTHING: Any = object()


class Clock:
    """The stand-in's clock. It moves only when a test moves it."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class Walka:
    """Asks the way Walka is to ask: its name, the project's key, JSON."""

    def __init__(self, fake: FakeAccounts, session: aiohttp.ClientSession) -> None:
        self.fake = fake
        self.session = session

    async def ask(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        bearer: str | None = None,
        body: Any = _NOTHING,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        key: str | None = "test-key",
        agent: str = "Walka/0.8.0-test",
    ) -> tuple[int, Any, dict[str, str]]:
        sent = {"User-Agent": agent}
        if key is not None:
            sent["apikey"] = key
        if bearer is not None:
            sent["Authorization"] = "Bearer " + bearer
        sent.update(headers or {})
        more: dict[str, Any] = {} if body is _NOTHING else {"json": body}
        if data is not None:
            more = {"data": data}  # as it is: aiohttp's json= would not write a broken body
        url = self.fake.origin + path
        async with self.session.request(
            method, url, params=query, headers=sent, allow_redirects=False, **more
        ) as answer:
            text = await answer.text()
            try:
                said = json.loads(text) if text else None
            except ValueError:
                said = text
            return answer.status, said, dict(answer.headers)

    async def push(self, bearer: str | None, rows: Any, **more: Any) -> tuple[int, Any, dict[str, str]]:
        more.setdefault("query", PUSH)
        more.setdefault("headers", {"Prefer": PREFER})
        return await self.ask("POST", "/rest/v1/walks", bearer=bearer, body=rows, **more)

    async def find(self, bearer: str, *keys: str) -> tuple[int, Any, dict[str, str]]:
        query = {"start_key": "in.(" + ",".join(keys) + ")", "select": FIND}
        return await self.ask("GET", "/rest/v1/walks", bearer=bearer, query=query)

    async def refresh(self, token: str) -> tuple[int, Any, dict[str, str]]:
        query = {"grant_type": "refresh_token"}
        return await self.ask("POST", "/auth/v1/token", query=query, body={"refresh_token": token})

    async def sign_out(self, bearer: str | None) -> tuple[int, Any, dict[str, str]]:
        return await self.ask("POST", "/auth/v1/logout", query={"scope": "local"}, bearer=bearer)

    async def first_step(self, bearer: str | None) -> tuple[int, Any, dict[str, str]]:
        return await self.ask("GET", "/rest/v1/profiles", bearer=bearer, query={"select": "adult_confirmed_at"})

    async def exchange(self, code: str, verifier: str) -> tuple[int, Any, dict[str, str]]:
        body = {"auth_code": code, "code_verifier": verifier}
        return await self.ask("POST", "/auth/v1/token", query={"grant_type": "pkce"}, body=body)


def play(scenario):
    """Run one scenario against a stand-in of its own."""

    async def whole():
        clock = Clock()
        async with FakeAccounts(wall=clock) as fake, aiohttp.ClientSession() as session:
            return await scenario(fake, Walka(fake, session), clock)

    return asyncio.run(asyncio.wait_for(whole(), 30))


def moment(epoch: float) -> str:
    """Seconds since 1970 -> the text Walka sends: UTC, to the millisecond."""
    when = datetime.fromtimestamp(round(epoch * 1000) / 1000, UTC)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def row(start: float = 1795120200.4, **changes: Any) -> dict[str, Any]:
    """A row as Walka makes it - the website's own example - with a new id."""
    made = {
        "id": str(uuid.uuid4()),
        "origin": "walka",
        "client_id": "20261119-213000",
        "started_at": moment(start),
        "ended_at": moment(start + 44.2),
        "local_day": moment(start)[:10],
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
    made.update(changes)
    return made


def challenge_of(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def sign_in_address(fake: FakeAccounts, back: str, verifier: str = VERIFIER, **changes: str) -> str:
    query = {
        "provider": "github",
        "redirect_to": back,
        "code_challenge": challenge_of(verifier),
        "code_challenge_method": "s256",
    }
    query.update(changes)
    return fake.origin + "/auth/v1/authorize?" + urlencode(query)


class Listener:
    """Stands where Walka's own listener will: it writes down what the browser brings."""

    def __init__(self) -> None:
        self.came: list[dict[str, str]] = []
        self.address = ""
        self._server: Any = None

    async def _answer(self, request: web.Request) -> web.Response:
        self.came.append(dict(request.query))
        return web.Response(text="the program's own page", headers={"Cache-Control": "no-store"})

    async def __aenter__(self) -> Listener:
        app = web.Application()
        app.router.add_get("/{anything:.*}", self._answer)
        self._server = test_utils.TestServer(app, host="127.0.0.1")
        await self._server.start_server()
        self.address = f"http://127.0.0.1:{self._server.port}/a-path-nobody-can-guess-0123456789abcdef"
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._server.close()


@pytest.fixture(autouse=True)
def nothing_outside_the_list():
    """After every test: no stand-in saw a request that Walka may not make (and was not told it was meant)."""
    fake_accounts.collect_violations()
    yield
    assert fake_accounts.collect_violations() == []


# ---- what it is ----------------------------------------------------------------------


def test_it_imports_nothing_of_the_feature():
    # A stand-in that borrows the feature's own code would repeat its mistakes.
    tree = ast.parse(Path(fake_accounts.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names and not [name for name in names if name.split(".")[0] == "walka"]


def test_it_listens_on_this_computer_and_can_be_started_and_closed_by_hand():
    async def scenario():
        fake = FakeAccounts(wall=Clock())
        assert fake.origin == "" and fake.key == "test-key" and fake.schema == 1 and fake.full_at == 20000
        await fake.start()
        await fake.start()  # twice is once
        origin = fake.origin
        assert origin.startswith("http://127.0.0.1:") and int(origin.rsplit(":", 1)[1]) > 0
        async with aiohttp.ClientSession() as session:
            status, said, _ = await Walka(fake, session).ask("POST", "/rest/v1/rpc/schema_version", body={})
            assert (status, said) == (200, 1)
        await fake.close()
        await fake.close()
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.ClientConnectorError):
                await session.get(origin + "/auth/v1/user")
        assert fake.violations == []

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_every_request_is_written_down_as_it_came():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        await walka.find(token, "2026-11-19T20:30:00", "2026-11-19T21:30:00")
        await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
        first, second = fake.requests
        assert isinstance(first, Seen)
        assert (first.method, first.path) == ("GET", "/rest/v1/walks")
        assert first.query == [("start_key", "in.(2026-11-19T20:30:00,2026-11-19T21:30:00)"), ("select", FIND)]
        assert first.headers["Authorization"] == "Bearer " + token and first.header("APIKEY") == "test-key"
        assert first.header("Cookie") is None and first.json is None and first.body == b""
        assert (second.method, second.path) == ("POST", "/rest/v1/rpc/schema_version")
        assert (second.query, second.json) == ([], {})
        assert second.body == b"{}"
        assert fake.violations == []

    play(scenario)


# ---- the key --------------------------------------------------------------------------


def test_without_the_projects_key_nothing_a_program_asks_is_answered():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        asked = (
            ("POST", "/rest/v1/rpc/schema_version", {"body": {}}),
            ("GET", "/rest/v1/profiles", {"query": {"select": "adult_confirmed_at"}}),
            ("GET", "/auth/v1/user", {}),
            ("POST", "/auth/v1/logout", {"query": {"scope": "local"}}),
        )
        for method, path, more in asked:
            status, said, _ = await walka.ask(method, path, bearer=token, key=None, **more)
            assert status == 401 and "code" not in said and said["message"] == "No API key found in request", path
            status, said, _ = await walka.ask(method, path, bearer=token, key="another-key", **more)
            assert status == 401 and said["message"] == "Invalid API key", path
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=token)
        assert status == 200  # nothing was signed out by the refused request
        assert fake.violations == []  # refused, but not outside the list

    play(scenario)


# ---- signing in through the browser (item 7) -----------------------------------------


def test_a_sign_in_through_the_browser_comes_back_with_a_code_that_buys_the_keys():
    async def scenario(fake, walka, clock):
        user = fake.add_user("walker@mail.example", way="google")
        async with Listener() as listener:
            status, headers, text = await fake.browser(sign_in_address(fake, listener.address, provider="google"))
            assert (status, text, headers["Cache-Control"]) == (200, "the program's own page", "no-store")
            assert list(listener.came[0]) == ["code"]  # the code, and nothing else that ties it to the question
            code = listener.came[0]["code"]
            assert fake.sent_to == [listener.address + "?code=" + code]

        status, said, _ = await walka.exchange(code, VERIFIER)
        assert status == 200
        assert set(said) == {
            "access_token",
            "token_type",
            "expires_in",
            "expires_at",
            "refresh_token",
            "user",
            "provider_token",
            "provider_refresh_token",
        }
        assert (said["token_type"], said["expires_in"], said["expires_at"]) == ("bearer", 3600, int(NOW) + 3600)
        assert len(said["refresh_token"]) == 12 and said["refresh_token"].isalnum()
        assert said["user"]["id"] == user and said["user"]["email"] == "walker@mail.example"
        assert said["user"]["app_metadata"]["provider"] == "google"
        assert {"full_name", "avatar_url"} <= set(said["user"]["user_metadata"])  # more than Walka may keep

        status, me, _ = await walka.ask("GET", "/auth/v1/user", bearer=said["access_token"])
        assert (status, me["id"], me["email"]) == (200, user, "walker@mail.example")

        issued = fake.secrets_issued()
        for name in ("access_token", "refresh_token", "provider_token", "provider_refresh_token"):
            assert said[name] in issued
        assert code in issued

        # /authorize was the browser's request: no key, no Walka in its name, and that is in order
        browser_request = fake.requests[0]
        assert browser_request.path == "/auth/v1/authorize" and browser_request.header("apikey") is None
        assert not browser_request.header("User-Agent").startswith("Walka/")
        assert fake.violations == []

    play(scenario)


def test_a_wrong_verifier_is_refused_and_the_code_stays_good_until_it_is_used_once():
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        async with Listener() as listener:
            await fake.browser(sign_in_address(fake, listener.address))
        code = listener.came[0]["code"]

        status, said, _ = await walka.exchange(code, "w" * 43)
        assert (status, said["error_code"], said["code"]) == (400, "bad_code_verifier", 400) and "msg" in said

        status, said, _ = await walka.exchange(code, VERIFIER)
        assert status == 200 and said["access_token"]

        status, said, _ = await walka.exchange(code, VERIFIER)
        assert (status, said["error_code"]) == (404, "flow_state_not_found")
        status, said, _ = await walka.exchange(str(uuid.uuid4()), VERIFIER)
        assert (status, said["error_code"]) == (404, "flow_state_not_found")
        assert fake.violations == []

    play(scenario)


def test_a_code_from_somebody_elses_sign_in_buys_nothing_with_my_verifier():
    async def scenario(fake, walka, clock):
        mine = fake.add_user("me@mail.example")
        theirs = fake.add_user("them@mail.example")
        async with Listener() as listener:
            fake.next_browser_user(mine)
            await fake.browser(sign_in_address(fake, listener.address, "m" * 64))
            fake.next_browser_user(theirs)
            await fake.browser(sign_in_address(fake, listener.address, "t" * 64))
        my_code, their_code = (came["code"] for came in listener.came)

        status, said, _ = await walka.exchange(their_code, "m" * 64)
        assert (status, said["error_code"]) == (400, "bad_code_verifier")
        status, said, _ = await walka.exchange(my_code, "m" * 64)
        assert status == 200 and said["user"]["id"] == mine
        status, said, _ = await walka.exchange(their_code, "t" * 64)
        assert status == 200 and said["user"]["id"] == theirs
        assert fake.violations == []

    play(scenario)


def test_who_signs_in_is_the_user_added_last_until_a_test_says_otherwise():
    async def scenario(fake, walka, clock):
        first = fake.add_user("first@mail.example")
        second = fake.add_user("second@mail.example")
        async with Listener() as listener:
            await fake.browser(sign_in_address(fake, listener.address))
            fake.next_browser_user(first)
            await fake.browser(sign_in_address(fake, listener.address))
            await fake.browser(sign_in_address(fake, listener.address))  # it holds until it is changed
        who = []
        for came in listener.came:
            _, said, _ = await walka.exchange(came["code"], VERIFIER)
            who.append(said["user"]["id"])
        assert who == [second, first, first]
        with pytest.raises(KeyError):
            fake.next_browser_user(str(uuid.uuid4()))  # a slip in a test: nobody of that id was added

    play(scenario)


def test_an_account_deleted_between_the_browser_and_the_exchange_buys_no_keys():
    async def scenario(fake, walka, clock):
        gone = fake.add_user("gone@mail.example")
        fake.next_browser_user(gone)
        async with Listener() as listener:
            await fake.browser(sign_in_address(fake, listener.address))
            fake.delete_user(gone)
            status, said, _ = await walka.exchange(listener.came[0]["code"], VERIFIER)
            assert (status, said["error_code"]) == (404, "flow_state_not_found")
            # ... and whoever comes to sign in as that account now is not let in
            await fake.browser(sign_in_address(fake, listener.address))
            assert list(listener.came[1]) == ["error", "error_description"]
        assert fake.violations == []

    play(scenario)


def test_when_the_person_says_no_the_browser_comes_back_with_an_error_and_no_code():
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        fake.next_browser_user(None)
        async with Listener() as listener:
            status, _, _ = await fake.browser(sign_in_address(fake, listener.address))
        assert status == 200
        assert listener.came == [{"error": "access_denied", "error_description": DENIED_DESCRIPTION}]
        assert not [seen for seen in fake.requests if seen.path == "/auth/v1/token"]
        assert fake.violations == []

    play(scenario)


def test_a_return_address_that_is_not_127_0_0_1_lands_on_the_website_with_the_code():
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        backs = ("http://localhost:53124/callback", "https://127.0.0.1:53124/callback", "http://127.0.0.1/callback")
        for back in backs:
            status, _, text = await fake.browser(sign_in_address(fake, back))
            assert (status, text) == (200, "The website's account page."), back
        assert len(fake.landed) == 3 and all(came[0][0] == "code" for came in fake.landed)
        assert all(where.startswith(fake.origin + "/landed?code=") for where in fake.sent_to)
        assert fake.violations == []  # no error is shown for it, there or here

    play(scenario)


def test_the_browser_is_only_ever_opened_at_the_accounts_own_sign_in_address():
    async def scenario(fake, walka, clock):
        with pytest.raises(AssertionError):
            await fake.browser("https://website.example/account/")
        with pytest.raises(AssertionError):
            await fake.browser(fake.origin + "/auth/v1/user")
        assert len(fake.violations) == 2 and "browser" in fake.violations[0]
        assert fake.requests == []
        fake.violations.clear()  # meant, here

    play(scenario)


@pytest.mark.parametrize(
    "changes, fault",
    [
        ({"provider": "facebook"}, "provider"),
        ({"provider": "email"}, "provider"),
        ({"code_challenge_method": "S256"}, "code_challenge_method"),
        ({"code_challenge_method": "plain"}, "code_challenge_method"),
        ({"code_challenge": challenge_of(VERIFIER) + "="}, "code_challenge"),
        ({"code_challenge": "short"}, "code_challenge"),
        ({"scopes": "repo"}, "scopes"),
        ({"state": "abc"}, "state"),
    ],
)
def test_a_sign_in_address_with_anything_else_in_it_is_outside_the_list(changes, fault):
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        status, headers, _ = await fake.visit(sign_in_address(fake, "http://127.0.0.1:1/x", **changes))
        assert status == 400 and "Location" not in headers
        assert len(fake.violations) == 1 and fault in fake.violations[0]
        fake.violations.clear()

    play(scenario)


def test_a_sign_in_address_without_a_challenge_is_outside_the_list():
    # Without it the keys come after a "#", which a listener never sees (item 7).
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        query = urlencode({"provider": "github", "redirect_to": "http://127.0.0.1:1/x"})
        status, _, _ = await fake.visit(fake.origin + "/auth/v1/authorize?" + query)
        assert status == 400 and len(fake.violations) == 2  # the challenge and its method
        fake.violations.clear()

    play(scenario)


# ---- refreshing (item 9) ---------------------------------------------------------------


def test_every_refresh_gives_a_new_refresh_token_and_a_new_access_token():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        first = fake.new_sign_in(user)
        clock.now += 60
        status, second, _ = await walka.refresh(first["refresh_token"])
        assert status == 200 and set(second) == {
            "access_token",
            "token_type",
            "expires_in",
            "expires_at",
            "refresh_token",
            "user",
        }  # the other site's tokens come only with the code
        assert second["refresh_token"] != first["refresh_token"] and len(second["refresh_token"]) == 12
        assert second["access_token"] != first["access_token"]
        assert second["expires_at"] == int(NOW) + 60 + 3600 and second["user"]["id"] == user
        for answer in (first, second):  # the old access token runs until it runs out
            status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
            assert status == 200
        assert fake.violations == []

    play(scenario)


def test_the_token_before_the_current_one_gives_the_current_one_back_however_late():
    async def scenario(fake, walka, clock):
        first = fake.new_sign_in(fake.add_user("a@b.example"))["refresh_token"]
        _, said, _ = await walka.refresh(first)
        second = said["refresh_token"]
        clock.now += 86400
        status, said, _ = await walka.refresh(first)
        assert status == 200 and said["refresh_token"] == second  # nothing changed hands
        status, said, _ = await walka.refresh(second)
        assert status == 200 and said["refresh_token"] not in (first, second)

    play(scenario)


def test_an_older_token_used_again_ends_the_whole_sign_in_its_newest_token_too():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        other_computer = fake.new_sign_in(user)["refresh_token"]
        answer = fake.new_sign_in(user)
        first = answer["refresh_token"]
        clock.now += 60
        _, said, _ = await walka.refresh(first)
        clock.now += 60
        _, said, _ = await walka.refresh(said["refresh_token"])
        newest = said["refresh_token"]
        clock.now += 60

        status, said, _ = await walka.refresh(first)
        assert (status, said["error_code"], said["code"]) == (400, "refresh_token_already_used", 400)
        status, said, _ = await walka.refresh(newest)
        assert (status, said["error_code"]) == (400, "refresh_token_already_used")
        # the keys were used twice, and the account is there (data-model.md): /user still answers
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert status == 200
        # another sign-in of the same account lives on
        status, said, _ = await walka.refresh(other_computer)
        assert status == 200
        assert fake.violations == []

    play(scenario)


def test_within_ten_seconds_an_older_token_still_gives_the_current_one_back():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        first = fake.new_sign_in(user)["refresh_token"]
        _, said, _ = await walka.refresh(first)
        _, said, _ = await walka.refresh(said["refresh_token"])
        newest = said["refresh_token"]
        clock.now = NOW + 9.5
        status, said, _ = await walka.refresh(first)
        assert status == 200 and said["refresh_token"] == newest
        clock.now = NOW + 10
        status, said, _ = await walka.refresh(first)
        assert (status, said["error_code"]) == (400, "refresh_token_already_used")

        # a test whose clock stands still sets the window to nought
        fake.reuse_window_s = 0
        first = fake.new_sign_in(user)["refresh_token"]
        _, said, _ = await walka.refresh(first)
        await walka.refresh(said["refresh_token"])
        status, said, _ = await walka.refresh(first)
        assert (status, said["error_code"]) == (400, "refresh_token_already_used")

    play(scenario)


def test_a_refresh_token_that_is_not_there_is_not_found():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        status, said, _ = await walka.refresh("abcdefghijkl")
        assert (status, said["error_code"], said["code"]) == (400, "refresh_token_not_found", 400)

        signed_out = fake.new_sign_in(user)
        status, _, _ = await walka.sign_out(signed_out["access_token"])
        assert status == 204
        status, said, _ = await walka.refresh(signed_out["refresh_token"])
        assert (status, said["error_code"]) == (400, "refresh_token_not_found")

        gone = fake.new_sign_in(user)
        fake.delete_user(user)
        status, said, _ = await walka.refresh(gone["refresh_token"])
        assert (status, said["error_code"]) == (400, "refresh_token_not_found")
        assert fake.violations == []

    play(scenario)


# ---- signing out, and who is there (items 10, 11) ---------------------------------------


def test_signing_out_ends_this_sign_in_only_and_its_access_token_runs_on():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        here = fake.new_sign_in(user)
        elsewhere = fake.new_sign_in(user)

        status, said, _ = await walka.sign_out(here["access_token"])
        assert (status, said) == (204, None)

        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=here["access_token"])
        assert (status, said["error_code"]) == (403, "session_not_found")
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=elsewhere["access_token"])
        assert status == 200
        status, _, _ = await walka.refresh(elsewhere["refresh_token"])
        assert status == 200

        # the database goes on taking the token until it runs out (item 10)
        status, said, _ = await walka.push(here["access_token"], [row()])
        assert status == 201 and len(said) == 1
        assert fake.violations == []

    play(scenario)


def test_signing_out_with_a_token_that_has_run_out_is_refused_and_changes_nothing():
    async def scenario(fake, walka, clock):
        answer = fake.new_sign_in(fake.add_user("a@b.example"))
        clock.now += 3600  # to the second: the sign-in service gives no grace
        status, said, _ = await walka.sign_out(answer["access_token"])
        assert (status, said["error_code"], said["code"]) == (403, "bad_jwt", 403)
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert (status, said["error_code"]) == (403, "bad_jwt")
        status, _, _ = await walka.refresh(answer["refresh_token"])
        assert status == 200  # the sign-in itself is still there
        status, said, _ = await walka.ask("POST", "/auth/v1/logout", query={"scope": "local"})
        assert (status, said["error_code"]) == (401, "no_authorization")
        assert fake.violations == []

    play(scenario)


def test_signing_out_without_scope_local_is_outside_the_list_and_ends_nothing():
    # Without it EVERY sign-in of the account would end (item 10).
    async def scenario(fake, walka, clock):
        answer = fake.new_sign_in(fake.add_user("a@b.example"))
        for query in (None, {"scope": "global"}, {"scope": "others"}, {"scope": "local", "more": "1"}):
            status, _, _ = await walka.ask("POST", "/auth/v1/logout", query=query, bearer=answer["access_token"])
            assert status == 400
        assert len(fake.violations) == 4
        fake.violations.clear()
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert status == 200

    play(scenario)


def test_what_is_done_to_one_account_leaves_the_other_alone():
    async def scenario(fake, walka, clock):
        one, two = fake.add_user("one@mail.example"), fake.add_user("two@mail.example")
        first, second = fake.new_sign_in(one), fake.new_sign_in(two)
        for answer, user, email in ((first, one, "one@mail.example"), (second, two, "two@mail.example")):
            status, me, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
            assert (status, me["id"], me["email"]) == (200, user, email)
            sent = row()
            status, said, _ = await walka.push(answer["access_token"], [sent])
            assert (status, said) == (201, [{"id": sent["id"]}])

        with pytest.raises(KeyError):
            fake.mark_deleted(one, fake.walks(two)[0]["id"])  # not that account's walk
        assert fake.walks(two)[0]["deleted_at"] is None

        fake.sign_out_everywhere(one)
        status, said, _ = await walka.refresh(first["refresh_token"])
        assert (status, said["error_code"]) == (400, "refresh_token_not_found")
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=second["access_token"])
        assert status == 200
        status, _, _ = await walka.refresh(second["refresh_token"])
        assert status == 200

        fake.delete_user(one)
        assert fake.walks(one) == [] and len(fake.walks(two)) == 1
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=second["access_token"])
        assert status == 200
        assert fake.violations == []

    play(scenario)


def test_who_is_there_says_signed_out_and_says_gone():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        answer = fake.new_sign_in(user)
        fake.sign_out_everywhere(user)
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert (status, said["error_code"]) == (403, "session_not_found")
        status, said, _ = await walka.refresh(answer["refresh_token"])
        assert (status, said["error_code"]) == (400, "refresh_token_not_found")

        answer = fake.new_sign_in(user)
        fake.delete_user(user)
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert (status, said["error_code"]) == (403, "user_not_found")
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer="made-up")
        assert (status, said["error_code"]) == (403, "bad_jwt")
        assert fake.violations == []

    play(scenario)


# ---- the bearer, at the database (items 1, 2) -------------------------------------------


def test_without_a_bearer_a_request_is_a_visitors_and_a_visitor_reads_no_table():
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        for bearer in (None, "test-key"):
            status, said, _ = await walka.first_step(bearer)
            assert (status, said["code"], said["message"]) == (401, "42501", "permission denied for table profiles")
            status, said, _ = await walka.push(bearer, [row()])
            assert (status, said["code"], said["message"]) == (401, "42501", "permission denied for table walks")
            assert set(said) == {"code", "details", "hint", "message"}
            # the version is a visitor's to read (item 26)
            status, said, _ = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=bearer, body={})
            assert (status, said) == (200, 1)
        assert fake.violations == []

    play(scenario)


def test_a_bearer_nobody_gave_out_is_refused_and_is_not_outside_the_list():
    async def scenario(fake, walka, clock):
        fake.add_user("a@b.example")
        status, said, _ = await walka.push("made-up", [row()])
        assert (status, said["code"]) == (401, "PGRST301")
        status, said, _ = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer="made-up", body={})
        assert (status, said["code"]) == (401, "PGRST301")
        assert fake.violations == []

    play(scenario)


def test_an_access_token_that_ran_out_is_answered_jwt_expired_after_half_a_minute_of_grace():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        clock.now = NOW + 3600 + 29
        status, said, _ = await walka.push(token, [row()])
        assert status == 201  # the database lets it through for 30 seconds more (item 2)
        clock.now = NOW + 3600 + 30
        status, said, headers = await walka.push(token, [row(start=1795130000)])
        assert (status, said["code"], said["message"]) == (401, "PGRST303", "JWT expired")
        assert headers["WWW-Authenticate"] == 'Bearer error="invalid_token", error_description="JWT expired"'
        for method, path, more in (
            ("POST", "/rest/v1/rpc/schema_version", {"body": {}}),
            ("GET", "/rest/v1/profiles", {"query": {"select": "adult_confirmed_at"}}),
            ("GET", "/rest/v1/walks", {"query": {"start_key": "in.(2026-11-19T20:30:00)", "select": FIND}}),
        ):
            status, said, _ = await walka.ask(method, path, bearer=token, **more)
            assert (status, said["code"]) == (401, "PGRST303"), path
        assert len(fake.walks(user)) == 1
        assert fake.violations == []

    play(scenario)


def test_a_test_can_make_every_access_token_run_out_at_once():
    async def scenario(fake, walka, clock):
        answer = fake.new_sign_in(fake.add_user("a@b.example"))
        fake.expire_access_tokens()
        status, said, _ = await walka.push(answer["access_token"], [row()])
        assert (status, said["code"]) == (401, "PGRST303")
        status, said, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert (status, said["error_code"]) == (403, "bad_jwt")
        # refresh, and try once more
        _, fresh, _ = await walka.refresh(answer["refresh_token"])
        status, said, _ = await walka.push(fresh["access_token"], [row()])
        assert status == 201 and len(said) == 1
        assert fake.violations == []

    play(scenario)


# ---- the version, and the profile -----------------------------------------------------


def test_the_schema_version_is_a_bare_number():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        status, said, headers = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
        assert (status, said) == (200, 1) and headers["Content-Type"].startswith("application/json")
        fake.schema = 2
        status, said, _ = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
        assert (status, said) == (200, 2)
        assert fake.violations == []

    play(scenario)


def test_the_first_step_reads_as_a_time_as_nothing_or_as_no_row_at_all():
    async def scenario(fake, walka, clock):
        async def first_step(user):
            token = fake.new_sign_in(user)["access_token"]
            status, said, _ = await walka.first_step(token)
            assert status == 200
            return said

        taken = fake.add_user("taken@mail.example")
        new = fake.add_user("new@mail.example", adult=False)
        half = fake.add_user("half@mail.example", adult=False, profile_row=True)
        assert await first_step(taken) == [{"adult_confirmed_at": "2026-11-21T22:26:40+00:00"}]
        assert await first_step(new) == []
        assert await first_step(half) == [{"adult_confirmed_at": None}]

        # the person ticks the box on the website; Walka looks again
        clock.now += 90.25
        fake.take_first_step(new)
        fake.take_first_step(half)
        ticked = [{"adult_confirmed_at": "2026-11-21T22:28:10.25+00:00"}]
        assert await first_step(new) == ticked and await first_step(half) == ticked

        # a deleted account reads like a new one, with its old token (item 27)
        token = fake.new_sign_in(taken)["access_token"]
        fake.delete_user(taken)
        status, said, _ = await walka.first_step(token)
        assert (status, said) == (200, [])
        assert fake.violations == []

    play(scenario)


def test_the_page_reads_as_its_four_columns():
    async def scenario(fake, walka, clock):
        page = "handle,is_public,show_days,show_walks"
        quiet = fake.add_user("quiet@mail.example")
        loud = fake.add_user("loud@mail.example", public=True, handle="loud", show_days=True, show_walks=True)
        token = fake.new_sign_in(quiet)["access_token"]
        status, said, _ = await walka.ask("GET", "/rest/v1/profiles", bearer=token, query={"select": page})
        assert said == [{"handle": None, "is_public": False, "show_days": False, "show_walks": False}]
        assert list(said[0]) == page.split(",")
        token = fake.new_sign_in(loud)["access_token"]
        status, said, _ = await walka.ask("GET", "/rest/v1/profiles", bearer=token, query={"select": page})
        assert said == [{"handle": "loud", "is_public": True, "show_days": True, "show_walks": True}]
        fake.set_page(loud, is_public=False)
        status, said, _ = await walka.ask("GET", "/rest/v1/profiles", bearer=token, query={"select": page})
        assert said[0]["is_public"] is False
        assert fake.violations == []

    play(scenario)


# ---- sending walks (items 14 to 17) -----------------------------------------------------


def test_the_sixteen_keys_are_the_websites():
    assert ROW_KEYS == (
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
    assert tuple(row()) == ROW_KEYS


def test_rows_that_are_added_come_back_with_the_ids_sent_in_the_order_sent():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        rows = [row(start=1795120200.4 + 3600 * hour) for hour in (2, 0, 1)]
        status, said, _ = await walka.push(token, rows)
        assert status == 201 and said == [{"id": sent["id"]} for sent in rows]

        kept = fake.walks(user)
        assert [walk["id"] for walk in kept] == [sent["id"] for sent in rows]
        first = kept[1]
        assert first["user_id"] == user and first["device"] is None and first["deleted_at"] is None
        assert first["started_at"] == "2026-11-19T20:30:00.4+00:00"
        assert first["ended_at"] == "2026-11-19T20:30:44.6+00:00"
        assert first["start_key"] == "2026-11-19T20:30:00"  # cut to the second, in UTC, no zone
        assert first["local_day"] == "2026-11-19" and first["utc_offset_min"] == 60
        assert json.dumps(first["seconds"]) == "40.0"  # 40 went, and comes back as 40.0 (item 17)
        assert first["created_at"] == first["updated_at"] == "2026-11-21T22:26:40+00:00"
        assert set(first) == set(fake_accounts.COLUMNS)
        assert fake.violations == []

    play(scenario)


def test_one_row_and_two_hundred_rows_both_go():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        status, said, _ = await walka.push(token, [row()])
        assert status == 201 and len(said) == 1
        status, said, _ = await walka.push(token, [row(start=1795200000 + number) for number in range(200)])
        assert status == 201 and len(said) == 200 and len(fake.walks(user)) == 201
        assert fake.violations == []

    play(scenario)


def test_numbers_are_rounded_to_one_decimal_half_away_from_nought():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        sent = row(seconds=39.95, distance_m=34.74, kcal=2.45, kcal_extra=None, utc_offset_min=None, session=None)
        status, _, _ = await walka.push(token, [sent])
        assert status == 201
        kept = fake.walks(user)[0]
        assert (kept["seconds"], kept["distance_m"], kept["kcal"]) == (40.0, 34.7, 2.5)
        assert kept["kcal_extra"] is None and kept["utc_offset_min"] is None and kept["session"] is None
        # 86400.04 seconds is 86400.0 by the time the table's limit looks at it
        long = row(start=1795000000, seconds=86400.04, ended_at=moment(1795000000 + 86400), local_day="2026-11-18")
        status, _, _ = await walka.push(token, [long])
        assert status == 201

    play(scenario)


def test_a_walk_that_is_there_already_does_not_come_back_and_keeps_the_id_it_got_first():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        first = row()
        await walka.push(token, [first])

        again = row(seconds=12, steps=5)  # another id, other numbers, the same start second
        new = row(start=1795130000)
        status, said, _ = await walka.push(token, [again, new])
        assert status == 201 and said == [{"id": new["id"]}]
        kept = fake.walks(user)
        assert [walk["id"] for walk in kept] == [first["id"], new["id"]]
        assert kept[0]["steps"] == 66  # the account's row is not written over

        # nothing at all added is 201 with an empty list (item 14)
        status, said, _ = await walka.push(token, [again, row(start=1795130000.9)])
        assert (status, said) == (201, [])

        # the very same row sent twice - the answer was lost - is quiet too: no 409 for its id
        status, said, _ = await walka.push(token, [first])
        assert (status, said) == (201, [])
        assert fake.violations == []

    play(scenario)


def test_the_start_is_cut_to_the_second_in_utc_whatever_zone_it_was_written_in():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        await walka.push(token, [row(started_at="2026-11-19T20:30:00.999Z")])
        same = [
            row(started_at="2026-11-19T20:30:00.000Z"),
            row(started_at="2026-11-19T21:30:00.5+01:00"),
            row(started_at="2026-11-19T15:30:00-05:00"),
            row(started_at="2026-11-19T20:30:00"),  # no zone is UTC (item 17)
            row(started_at="2026-11-19 20:30:00+00"),
        ]
        status, said, _ = await walka.push(token, same)
        assert (status, said) == (201, [])
        other = row(started_at="2026-11-19T20:30:01.000Z")  # the next second is another walk
        status, said, _ = await walka.push(token, [other])
        assert said == [{"id": other["id"]}]
        assert [walk["start_key"] for walk in fake.walks(user)] == ["2026-11-19T20:30:00", "2026-11-19T20:30:01"]

    play(scenario)


def test_two_rows_of_one_batch_with_the_same_start_are_one_walk():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        one, two = row(), row()
        status, said, _ = await walka.push(token, [one, two])
        assert (status, said) == (201, [{"id": one["id"]}])
        assert len(fake.walks(user)) == 1

    play(scenario)


def test_the_same_start_in_two_accounts_is_two_walks():
    async def scenario(fake, walka, clock):
        one, two = fake.add_user("one@mail.example"), fake.add_user("two@mail.example")
        for user in (one, two):
            status, said, _ = await walka.push(fake.new_sign_in(user)["access_token"], [row()])
            assert status == 201 and len(said) == 1
        assert len(fake.walks(one)) == len(fake.walks(two)) == 1

    play(scenario)


def test_an_id_that_any_walk_of_any_account_has_refuses_the_batch_whole():
    async def scenario(fake, walka, clock):
        one, two = fake.add_user("one@mail.example"), fake.add_user("two@mail.example")
        taken = row()
        await walka.push(fake.new_sign_in(one)["access_token"], [taken])

        token = fake.new_sign_in(two)["access_token"]
        fine = row(start=1795130000)
        clash = row(start=1795140000, id=taken["id"])
        status, said, _ = await walka.push(token, [fine, clash])
        assert (status, said["code"]) == (409, "23505")
        assert "walks_pkey" in said["message"] and taken["id"] in said["details"]
        assert fake.walks(two) == []  # the good row of the batch was not added either

        # twice in one batch, with two starts, is the same refusal
        twin = row(start=1795150000, id=fine["id"])
        status, said, _ = await walka.push(token, [fine, twin])
        assert (status, said["code"]) == (409, "23505")

        # a new id, and it goes (5.3: make a new id and send once more)
        clash["id"] = str(uuid.uuid4())
        status, said, _ = await walka.push(token, [fine, clash])
        assert status == 201 and len(said) == 2

        # once the account that had it is gone, the id is free again
        fake.delete_user(one)
        status, said, _ = await walka.push(token, [row(start=1795160000, id=taken["id"])])
        assert status == 201 and len(said) == 1
        assert fake.violations == []

    play(scenario)


def test_a_walk_deleted_in_the_account_stays_deleted_when_it_is_sent_again():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        sent = row()
        await walka.push(token, [sent])
        clock.now += 100.5
        fake.mark_deleted(user, sent["id"])

        marker = fake.walks(user)[0]
        assert marker["deleted_at"] == marker["updated_at"] == "2026-11-21T22:28:20.5+00:00"
        assert (marker["seconds"], marker["distance_m"], marker["steps"], marker["kcal"]) == (0.0, 0.0, 0, 0.0)
        assert marker["kcal_extra"] is None and marker["session"] is None and marker["calories_source"] is None
        assert marker["started_at"] == "2026-11-19T20:30:00.4+00:00" and marker["client_id"] == "20261119-213000"

        status, said, _ = await walka.push(token, [row()])
        assert (status, said) == (201, [])  # answered like a walk that is there already (item 21)
        assert fake.walks(user) == [marker]

        status, said, _ = await walka.find(token, "2026-11-19T20:30:00")
        key, when = "2026-11-19T20:30:00", "2026-11-21T22:28:20.5+00:00"
        assert said == [{"id": sent["id"], "start_key": key, "deleted_at": when}]
        with pytest.raises(KeyError):
            fake.mark_deleted(user, str(uuid.uuid4()))
        assert fake.violations == []

    play(scenario)


def test_looking_walks_up_by_their_start_gives_the_id_each_has_and_whether_it_was_deleted():
    async def scenario(fake, walka, clock):
        user, other = fake.add_user("a@b.example"), fake.add_user("other@mail.example")
        token = fake.new_sign_in(user)["access_token"]
        first, second = row(), row(start=1795130000.7)
        await walka.push(token, [first, second])
        from_website = fake.add_walk(user, "2026-11-01T08:00:00Z")
        deleted = fake.add_walk(user, "2026-11-02T08:00:00.25Z", deleted=True)
        fake.add_walk(other, "2026-11-19T20:30:00Z")  # somebody else's walk of the same second

        status, said, _ = await walka.find(
            token, "2026-11-02T08:00:00", "2026-11-19T20:30:00", "2026-11-01T08:00:00", "2031-01-01T00:00:00"
        )
        assert status == 200 and [list(found) for found in said] == [["id", "start_key", "deleted_at"]] * 3
        by_key = {found["start_key"]: found for found in said}
        key = "2026-11-19T20:30:00"
        assert by_key[key] == {"id": first["id"], "start_key": key, "deleted_at": None}
        assert by_key["2026-11-01T08:00:00"]["id"] == from_website
        assert by_key["2026-11-02T08:00:00"]["id"] == deleted and by_key["2026-11-02T08:00:00"]["deleted_at"]
        # the rows come in the table's order, not in the order asked
        assert [found["id"] for found in said] == [first["id"], from_website, deleted]

        # a zone written on the value is ignored (item 15); a fraction of a second finds nothing
        status, said, _ = await walka.find(token, "2026-11-19T20:30:00+05:00", "2026-11-19T23:13:20.7")
        assert [found["id"] for found in said] == [first["id"]]
        status, said, _ = await walka.find(token, "2026-11-19 23:13:20")
        assert [found["id"] for found in said] == [second["id"]]
        status, said, _ = await walka.find(token)
        assert (status, said) == (200, [])
        status, said, _ = await walka.find(token, "yesterday")
        assert (status, said["code"]) == (400, "22007")
        assert fake.violations == []

    play(scenario)


def test_an_account_that_has_not_taken_its_first_step_or_is_gone_is_for_adults():
    async def scenario(fake, walka, clock):
        refused = {"code": "23514", "details": None, "hint": None, "message": ADULTS}
        new = fake.add_user("new@mail.example", adult=False)
        half = fake.add_user("half@mail.example", adult=False, profile_row=True)
        for user in (new, half):
            token = fake.new_sign_in(user)["access_token"]
            status, said, _ = await walka.push(token, [row(), row(start=1795130000)])
            assert (status, said) == (400, refused)
            assert fake.walks(user) == []
            fake.take_first_step(user)
            status, said, _ = await walka.push(token, [row()])
            assert status == 201 and len(said) == 1

        # a deleted account answers the same with its old token - not 23503 (item 27) - even for a walk it had
        token = fake.new_sign_in(new)["access_token"]
        fake.delete_user(new)
        status, said, _ = await walka.push(token, [row()])
        assert (status, said) == (400, refused)
        status, said, _ = await walka.find(token, "2026-11-19T20:30:00")
        assert (status, said) == (200, [])
        assert fake.violations == []

    play(scenario)


def test_a_walk_cannot_start_more_than_two_days_from_now():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        edge = NOW + 2 * 86400
        status, said, _ = await walka.push(token, [row(), row(start=edge + 0.001)])
        assert (status, said) == (400, {"code": "23514", "details": None, "hint": None, "message": DAYS_TO_COME})
        assert fake.walks(user) == []
        status, said, _ = await walka.push(token, [row(start=edge)])  # a clock that is a little off is fine
        assert status == 201
        clock.now += 1  # tried again later, the same walk goes
        status, said, _ = await walka.push(token, [row(start=edge + 0.001)])
        assert (status, said) == (201, [])  # ... and is the walk of that second, which is there
        assert fake.violations == []

    play(scenario)


def at(start: float, **changes: Any) -> dict[str, Any]:
    return {"started_at": moment(start), "ended_at": moment(start + 44.2), "local_day": moment(start)[:10], **changes}


LIMITS = [
    ({"calories_source": "pulse"}, "walks_calories_source_check"),
    ({"ended_at": "2026-11-19T20:29:59.000Z"}, "walks_check"),
    ({"ended_at": "2026-11-21T20:30:00.401Z", "local_day": "2026-11-20"}, "walks_check1"),
    ({"seconds": 104.3}, "walks_check2"),  # the belt moved a minute longer than the walk lasted, and a bit
    ({"local_day": "2026-11-17"}, "walks_check3"),
    ({"local_day": "2026-11-21"}, "walks_check3"),
    ({"client_id": ""}, "walks_client_id_check"),
    ({"client_id": "x" * 65}, "walks_client_id_check"),
    ({"distance_m": 200000.1}, "walks_distance_m_check"),
    ({"distance_m": 1000000}, "walks_distance_m_check"),  # it still fits the column (eight digits), not the limit
    ({"distance_m": -0.1}, "walks_distance_m_check"),
    ({"distance_source": "gps"}, "walks_distance_source_check"),
    ({"kcal": 20000.1}, "walks_kcal_check"),
    ({"kcal_extra": -1}, "walks_kcal_extra_check"),
    ({"kcal_extra": 20000.1}, "walks_kcal_extra_check"),
    ({"origin": "desktop"}, "walks_origin_check"),
    ({"seconds": -1}, "walks_seconds_check"),
    ({"seconds": 86400.1, "ended_at": "2026-11-21T08:30:00.000Z", "local_day": "2026-11-20"}, "walks_seconds_check"),
    ({"session": "raid"}, "walks_session_check"),
    (at(946684799.0), "walks_started_at_check"),  # the last second of 1999
    (
        {"started_at": "0001-01-01T00:00:00.000Z", "ended_at": "0001-01-01T00:00:44.000Z", "local_day": "0001-01-01"},
        "walks_started_at_check",  # the first day there is
    ),
    ({"steps": 400001}, "walks_steps_check"),
    ({"steps": -1}, "walks_steps_check"),
    ({"steps_source": "watch"}, "walks_steps_source_check"),
    ({"utc_offset_min": 841}, "walks_utc_offset_min_check"),
    ({"utc_offset_min": -721}, "walks_utc_offset_min_check"),
]


@pytest.mark.parametrize("changes, rule", LIMITS, ids=[f"{rule}:{','.join(changes)}" for changes, rule in LIMITS])
def test_a_row_outside_the_tables_limits_refuses_the_batch_and_shows_the_whole_row(changes, rule):
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        status, said, _ = await walka.push(token, [row(start=1795130000), row(**changes)])
        assert (status, said["code"], said["hint"]) == (400, "23514", None)
        assert said["message"] == f'new row for relation "walks" violates check constraint "{rule}"'
        # Postgres shows the failing row, the account's id among it: Walka must keep none of this
        assert said["details"].startswith("Failing row contains (") and user in said["details"]
        assert fake.walks(user) == []
        assert fake.violations == []  # a refusal of the database is on the list

    play(scenario)


@pytest.mark.parametrize(
    "changes",
    [
        {"ended_at": "2026-11-21T20:30:00.400Z", "local_day": "2026-11-22", "seconds": 86400},  # two days to the dot
        {"seconds": 104.2},  # a minute longer than the walk, to the dot
        {"ended_at": moment(1795120200.4), "seconds": 60},  # a walk that ends as it starts
        {"local_day": "2026-11-18"},
        {"local_day": "2026-11-20"},
        {"client_id": "x" * 64, "steps": 400000, "kcal": 20000, "distance_m": 200000, "utc_offset_min": 840},
        {"steps": 0, "kcal": 0, "distance_m": 0, "seconds": 0, "kcal_extra": 0, "utc_offset_min": -720},
        {"origin": "file", "session": "free", "steps_source": "estimate", "calories_source": "heart_rate"},
        at(946684800.0),  # the first second of 2000
        {"session": None, "steps_source": None, "distance_source": None, "calories_source": None},
    ],
)
def test_a_row_on_the_edge_of_the_tables_limits_is_taken(changes):
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        sent = row(**changes)
        status, said, _ = await walka.push(fake.new_sign_in(user)["access_token"], [sent])
        assert (status, said) == (201, [{"id": sent["id"]}])

    play(scenario)


# "not null" in the table (the SQL, lines 445-472), written out here and not borrowed from the stand-in
NOT_EMPTY = ("id", "origin", "client_id", "started_at", "ended_at", "local_day")
NOT_EMPTY += ("seconds", "distance_m", "steps", "kcal")


@pytest.mark.parametrize("name", NOT_EMPTY)
def test_a_value_the_table_must_have_cannot_be_empty(name):
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        status, said, _ = await walka.push(fake.new_sign_in(user)["access_token"], [row(**{name: None})])
        assert (status, said["code"]) == (400, "23502")
        assert said["message"] == f'null value in column "{name}" of relation "walks" violates not-null constraint'
        assert user in said["details"] and fake.walks(user) == []
        assert fake.violations == []

    play(scenario)


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"steps": 66.0}, "22P02"),  # a whole number in the JSON itself (item 17)
        ({"steps": 66.5}, "22P02"),
        ({"steps": True}, "22P02"),
        ({"steps": "many"}, "22P02"),
        ({"steps": 2**31}, "22003"),
        ({"utc_offset_min": 60.0}, "22P02"),
        ({"utc_offset_min": 40000}, "22003"),
        ({"seconds": "forty"}, "22P02"),
        ({"seconds": 1000000}, "22003"),
        ({"seconds": 1e30}, "22003"),
        ({"kcal": 999999.96}, "22003"),  # it rounds up to a million
        ({"distance_m": 10000000}, "22003"),
        ({"kcal": False}, "22P02"),
        ({"started_at": 1795120200}, "22008"),  # times are text, not seconds since 1970
        ({"started_at": 1795120200.4}, "22007"),
        ({"ended_at": "yesterday"}, "22007"),
        ({"ended_at": "2026-13-40T00:00:00Z"}, "22008"),
        ({"local_day": "19.11.2026"}, "22007"),
        ({"local_day": "2026-02-30"}, "22008"),
        ({"id": "20261119-213000"}, "22P02"),
        ({"id": 7}, "22P02"),
    ],
)
def test_a_value_of_the_wrong_kind_refuses_the_batch_before_anything_else_is_asked(changes, code):
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        # even a row AFTER one that breaks a limit: the whole body is read first
        status, said, _ = await walka.push(token, [row(steps=-1), row(start=1795130000, **changes)])
        assert (status, said["code"]) == (400, code), said
        assert set(said) == {"code", "details", "hint", "message"} and fake.walks(user) == []

        # ... and even from an account that has not taken its first step
        closed = fake.new_sign_in(fake.add_user("new@mail.example", adult=False))["access_token"]
        status, said, _ = await walka.push(closed, [row(**changes)])
        assert (status, said["code"]) == (400, code)
        assert fake.violations == []

    play(scenario)


def test_the_database_asks_in_its_own_order_and_names_the_first_thing_that_is_wrong():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        closed = fake.new_sign_in(fake.add_user("new@mail.example", adult=False))["access_token"]
        ahead = NOW + 3 * 86400

        # adults, before the days to come, before a limit
        status, said, _ = await walka.push(closed, [row(start=ahead, steps=-1)])
        assert said["message"] == ADULTS
        # the days to come, before an empty value and before a limit
        status, said, _ = await walka.push(token, [row(start=ahead, steps=-1, kcal=None)])
        assert said["message"] == DAYS_TO_COME
        # an empty value, before a limit
        status, said, _ = await walka.push(token, [row(steps=-1, kcal=None)])
        assert said["code"] == "23502"
        # of two empty values, the column that comes first in the table
        status, said, _ = await walka.push(token, [row(kcal=None, origin=None)])
        assert (said["code"], '"origin"' in said["message"]) == ("23502", True)
        # of two broken limits in one row, the one whose name comes first
        status, said, _ = await walka.push(token, [row(steps=-1, kcal=-1)])
        assert "walks_kcal_check" in said["message"]
        # of two broken rows, the one sent first
        status, said, _ = await walka.push(token, [row(steps=-1), row(start=ahead)])
        assert "walks_steps_check" in said["message"]
        # a limit is asked even of a walk that is there already; only then is it left out
        await walka.push(token, [row()])
        status, said, _ = await walka.push(token, [row(steps=-1)])
        assert (status, said["code"]) == (400, "23514")
        # a walk that is there already is left out before its id is looked at
        taken = fake.walks(user)[0]["id"]
        status, said, _ = await walka.push(token, [row(id=taken)])
        assert (status, said) == (201, [])
        assert fake.violations == []

    play(scenario)


def test_a_full_account_takes_no_new_walk_and_still_answers_for_the_ones_it_has():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        fake.fill(user, 19997)
        known = row()
        await walka.push(token, [known])
        fake.mark_deleted(user, known["id"])  # a deleted walk still counts (item 23)
        assert len(fake.walks(user)) == 19998

        full = {
            "code": "23514",
            "details": None,
            "hint": None,
            "message": "There is room for 20000 rows of this kind in one account, and it is full.",
        }
        three = [row(start=1795130000 + number) for number in range(3)]
        status, said, _ = await walka.push(token, three)
        assert (status, said) == (400, full)
        assert len(fake.walks(user)) == 19998  # a batch that would go over is refused whole

        status, said, _ = await walka.push(token, three[:2])
        assert status == 201 and len(said) == 2 and len(fake.walks(user)) == 20000
        status, said, _ = await walka.push(token, three[2:])
        assert (status, said) == (400, full)  # the 20,001st walk
        # walks that are there already can still be sent (item 23)
        status, said, _ = await walka.push(token, [row(), three[0]])
        assert (status, said) == (201, [])
        # ... but not together with a new one
        status, said, _ = await walka.push(token, [row(), three[2]])
        assert (status, said) == (400, full)

        # another account has its own room
        other = fake.new_sign_in(fake.add_user("other@mail.example"))["access_token"]
        status, said, _ = await walka.push(other, [row(start=1795130000 + number) for number in range(3)])
        assert status == 201 and len(said) == 3
        assert fake.violations == []

    play(scenario)


def test_the_message_of_a_full_account_names_the_room_as_the_trigger_does():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        fake.full_at = 2
        status, said, _ = await walka.push(token, [row(start=1795130000 + number) for number in range(3)])
        assert status == 400
        assert said["message"] == "There is room for 2 rows of this kind in one account, and it is full."
        assert fake.walks(user) == []

        # an account that holds more than there is room for (the room was made smaller since): the
        # question is asked only when a row was really added, so its own walks can still be sent
        two = [row(start=1795130000 + number) for number in range(2)]
        status, said, _ = await walka.push(token, two)
        assert status == 201 and len(said) == 2
        fake.full_at = 1
        status, said, _ = await walka.push(token, two)
        assert (status, said) == (201, [])
        assert fake.violations == []

    play(scenario)


# ---- outside the list of brief 5.4 -------------------------------------------------------


GOOD = row()
SENT = {"query": PUSH, "headers": {"Prefer": PREFER}}
REFRESH = {"query": {"grant_type": "refresh_token"}, "bearer": None}
AS_JSON = {"Prefer": PREFER, "Content-Type": "application/json"}
ONE_START = "in.(2026-11-19T20:30:00)"


def without(name: str) -> dict[str, Any]:
    return {key: value for key, value in GOOD.items() if key != name}


# Requests Walka must never make: name -> (method, path, the rest). The
# access token goes with each unless it says "bearer": None.
OUTSIDE: dict[str, tuple[str, str, dict[str, Any]]] = {
    "a PATCH of a walk": ("PATCH", "/rest/v1/walks", {"query": {"id": "eq.x"}, "body": {"deleted_at": "now"}}),
    "a DELETE of a walk": ("DELETE", "/rest/v1/walks", {"query": {"id": "eq.x"}}),
    "a PUT": ("PUT", "/auth/v1/user", {"body": {"email": "x@y.example"}}),
    "a HEAD": ("HEAD", "/rest/v1/walks", {}),
    "ticking the first step": ("POST", "/rest/v1/profiles", {"body": {"adult_confirmed_at": "2026-11-21T00:00:00Z"}}),
    "making the page public": (
        "PATCH",
        "/rest/v1/profiles",
        {"query": {"user_id": "eq.x"}, "body": {"is_public": True}},
    ),
    "the body numbers": ("POST", "/rest/v1/body", {"body": {"weight_kg": 80}}),
    "reading the body numbers": ("GET", "/rest/v1/body", {}),
    "a food": ("POST", "/rest/v1/foods", {"body": {}}),
    "a diary entry": ("POST", "/rest/v1/food_entries", {"body": {}}),
    "the food goal": ("POST", "/rest/v1/food_settings", {"body": {}}),
    "a report": ("POST", "/rest/v1/reports", {"body": {}}),
    "deleting the account": ("POST", "/rest/v1/rpc/delete_my_account", {"body": {}}),
    "another function": ("POST", "/rest/v1/rpc/ping", {"body": {}}),
    "the sign-in service's settings": ("GET", "/auth/v1/settings", {"bearer": None}),
    "a code by e-mail": ("POST", "/auth/v1/otp", {"bearer": None, "body": {"email": "a@b.example"}}),
    "the token call as a GET": ("GET", "/auth/v1/token", REFRESH),
    "a password": (
        "POST",
        "/auth/v1/token",
        {"query": {"grant_type": "password"}, "bearer": None, "body": {"refresh_token": "x"}},
    ),
    "no grant type": ("POST", "/auth/v1/token", {"bearer": None, "body": {"refresh_token": "abcdefghijkl"}}),
    "a bearer on the refresh": (
        "POST",
        "/auth/v1/token",
        {"query": {"grant_type": "refresh_token"}, "body": {"refresh_token": "x"}},
    ),
    "a third key on the refresh": (
        "POST",
        "/auth/v1/token",
        {**REFRESH, "body": {"refresh_token": "x", "scope": "all"}},
    ),
    "a refresh token that is not text": ("POST", "/auth/v1/token", {**REFRESH, "body": {"refresh_token": 7}}),
    "a third key on the exchange": (
        "POST",
        "/auth/v1/token",
        {
            "query": {"grant_type": "pkce"},
            "bearer": None,
            "body": {"auth_code": "c", "code_verifier": VERIFIER, "redirect_to": "http://127.0.0.1:1/"},
        },
    ),
    "the exchange with the refresh's body": (
        "POST",
        "/auth/v1/token",
        {"query": {"grant_type": "pkce"}, "bearer": None, "body": {"refresh_token": "abcdefghijkl"}},
    ),
    "a body on signing out": ("POST", "/auth/v1/logout", {"query": {"scope": "local"}, "body": {"scope": "global"}}),
    "a query on /user": ("GET", "/auth/v1/user", {"query": {"x": "1"}}),
    "the version with an argument": ("POST", "/rest/v1/rpc/schema_version", {"body": {"a": 1}}),
    "the version with no body": ("POST", "/rest/v1/rpc/schema_version", {}),
    "the version with a query": ("POST", "/rest/v1/rpc/schema_version", {"query": {"select": "x"}, "body": {}}),
    "the version as a GET": ("GET", "/rest/v1/rpc/schema_version", {}),
    "the whole profile": ("GET", "/rest/v1/profiles", {"query": {"select": "*"}}),
    "the profile without select": ("GET", "/rest/v1/profiles", {}),
    "a profile column too many": (
        "GET",
        "/rest/v1/profiles",
        {"query": {"select": "adult_confirmed_at,body_food_consent_at"}},
    ),
    "a filter on the profile": (
        "GET",
        "/rest/v1/profiles",
        {"query": {"select": "adult_confirmed_at", "user_id": "eq.x"}},
    ),
    "a Prefer on a read": (
        "GET",
        "/rest/v1/profiles",
        {"query": {"select": "adult_confirmed_at"}, "headers": {"Prefer": "count=exact"}},
    ),
    "walks written over": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "headers": {"Prefer": "resolution=merge-duplicates,return=representation"}},
    ),
    "walks sent without Prefer": ("POST", "/rest/v1/walks", {**SENT, "body": [GOOD], "headers": {}}),
    "walks sent with return=minimal": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "headers": {"Prefer": "resolution=ignore-duplicates,return=minimal"}},
    ),
    "Prefer in another order": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "headers": {"Prefer": "return=representation,resolution=ignore-duplicates"}},
    ),
    "walks sent without on_conflict": ("POST", "/rest/v1/walks", {**SENT, "body": [GOOD], "query": {"select": "id"}}),
    "walks sent with another on_conflict": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "query": {"on_conflict": "id", "select": "id"}},
    ),
    "walks sent for whole rows back": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "query": {"on_conflict": "user_id,start_key"}},
    ),
    "walks sent for every column back": ("POST", "/rest/v1/walks", {**SENT, "body": [GOOD], "query": {**PUSH, "select": "*"}}),
    "walks sent for the account's id back": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [GOOD], "query": {**PUSH, "select": "id,user_id"}},
    ),
    "walks sent with columns": ("POST", "/rest/v1/walks", {**SENT, "body": [GOOD], "query": {**PUSH, "columns": "id"}}),
    "one walk, not a list": ("POST", "/rest/v1/walks", {**SENT, "body": GOOD}),
    "no walk at all": ("POST", "/rest/v1/walks", {**SENT, "body": []}),
    "201 walks at once": (
        "POST",
        "/rest/v1/walks",
        {**SENT, "body": [row(start=1795200000 + number) for number in range(201)]},
    ),
    "a 17th key, the installation": ("POST", "/rest/v1/walks", {**SENT, "body": [{**GOOD, "device": "0123"}]}),
    "a 17th key, the account": ("POST", "/rest/v1/walks", {**SENT, "body": [{**GOOD, "user_id": str(uuid.uuid4())}]}),
    "a 17th key, deleted_at": ("POST", "/rest/v1/walks", {**SENT, "body": [{**GOOD, "deleted_at": None}]}),
    "a 17th key, the treadmill": ("POST", "/rest/v1/walks", {**SENT, "body": [{**GOOD, "treadmill": "UREVO-E1"}]}),
    "a 17th key, the heart rate": ("POST", "/rest/v1/walks", {**SENT, "body": [{**GOOD, "avg_hr": 131}]}),
    "a key missing": ("POST", "/rest/v1/walks", {**SENT, "body": [without("kcal_extra")]}),
    "no id": ("POST", "/rest/v1/walks", {**SENT, "body": [without("id")]}),
    "a list of numbers": ("POST", "/rest/v1/walks", {**SENT, "body": [1, 2, 3]}),
    "a body that is not JSON": ("POST", "/rest/v1/walks", {"query": PUSH, "headers": AS_JSON, "data": b"[{"}),
    "NaN in the body": (
        "POST",
        "/rest/v1/walks",
        {"query": PUSH, "headers": AS_JSON, "data": json.dumps([{**GOOD, "kcal": float("nan")}]).encode()},
    ),
    "a body as plain text": (
        "POST",
        "/rest/v1/walks",
        {
            "query": PUSH,
            "headers": {"Prefer": PREFER, "Content-Type": "text/plain"},
            "data": json.dumps([GOOD]).encode(),
        },
    ),
    "the look-up by one start": (
        "GET",
        "/rest/v1/walks",
        {"query": {"start_key": "eq.2026-11-19T20:30:00", "select": FIND}},
    ),
    "the look-up for whole rows": ("GET", "/rest/v1/walks", {"query": {"start_key": ONE_START, "select": "*"}}),
    "the look-up without select": ("GET", "/rest/v1/walks", {"query": {"start_key": ONE_START}}),
    "the look-up asked twice over": (
        "GET",
        "/rest/v1/walks",
        {"query": [("start_key", ONE_START), ("start_key", ONE_START), ("select", FIND)]},
    ),
    "every walk of the account": ("GET", "/rest/v1/walks", {"query": {"select": FIND}}),
    "walks by another filter": ("GET", "/rest/v1/walks", {"query": {"started_at": "gte.2026-01-01", "select": FIND}}),
    "a body on a look-up": ("GET", "/rest/v1/walks", {"query": {"start_key": "in.()", "select": FIND}, "body": {}}),
    "a cookie": ("GET", "/auth/v1/user", {"headers": {"Cookie": "sb=1"}}),
    "the library's name": ("GET", "/auth/v1/user", {"headers": {"X-Client-Info": "walka"}}),
    "a word about the computer": ("GET", "/auth/v1/user", {"headers": {"X-Device": "PC-7"}}),
    "the sign-in service's version": ("GET", "/auth/v1/user", {"headers": {"X-Supabase-Api-Version": "2024-01-01"}}),
    "a language": ("GET", "/auth/v1/user", {"headers": {"Accept-Language": "de-DE"}}),
    "the library's own name as the agent": ("GET", "/auth/v1/user", {"agent": "Python/3.12 aiohttp/3.14.5"}),
    "the computer in the agent": ("GET", "/auth/v1/user", {"agent": "Walka/0.8.0 (Windows NT 10.0)"}),
    "walka in small letters": ("GET", "/auth/v1/user", {"agent": "walka/0.8.0"}),
}


@pytest.mark.parametrize("name", sorted(OUTSIDE))
def test_a_request_outside_the_list_is_written_down_and_answered_400(name):
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        answer = fake.new_sign_in(user)
        await walka.push(answer["access_token"], [row(start=1795130000)])
        walks, asked = fake.walks(user), len(fake.requests)

        method, path, rest = OUTSIDE[name]
        rest = {"bearer": answer["access_token"], **rest}
        status, said, _ = await walka.ask(method, path, **rest)

        assert status == 400, said
        assert len(fake.violations) >= 1 and all(text.startswith(f"{method} {path}: ") for text in fake.violations)
        # nothing happened: the walks are as they were, nobody was signed out, nothing was made public
        assert fake.walks(user) == walks and len(fake.requests) == asked + 1
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=answer["access_token"])
        assert status == 200
        status, _, _ = await walka.refresh(answer["refresh_token"])
        assert status == 200
        fake.violations.clear()

    play(scenario)


def test_the_websites_landing_page_is_the_browsers_and_nothing_is_asked_of_its_headers():
    async def scenario(fake, walka, clock):
        status, said, _ = await walka.ask("GET", "/landed", query={"code": "x"}, headers={"X-Extra": "1"}, key=None)
        assert status == 200 and fake.violations == []

    play(scenario)


def test_the_refusals_a_wrongly_built_request_really_gets_have_their_own_codes():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        good = row()
        status, said, _ = await walka.push(token, [{**good, "start_key": "2026-11-19T20:30:00"}])
        assert (status, said["code"]) == (400, "428C9")
        status, said, _ = await walka.push(token, [{**good, "treadmill": "UREVO-E1"}])
        assert (status, said["code"]) == (400, "PGRST204") and "treadmill" in said["message"]
        status, said, _ = await walka.push(token, [good, {**row(start=1795130000), "device": None}])
        assert (status, said["code"], said["message"]) == (400, "PGRST102", "All object keys must match")
        status, said, _ = await walka.push(token, [{**good, "device": "0123"}])
        assert (status, said["code"]) == (400, "WALKA0")  # the real database would take it: the list does not
        assert len(fake.violations) == 4 and "16" in fake.violations[0]
        fake.violations.clear()

    play(scenario)


def test_the_list_takes_the_header_names_in_any_case_and_content_type_with_its_charset():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        headers = {"PREFER": PREFER, "content-type": "application/json; charset=utf-8", "ACCEPT": "application/json"}
        status, said, _ = await walka.push(token, [row()], headers=headers)
        assert status == 201 and fake.violations == []

    play(scenario)


def test_a_header_sent_twice_is_outside_the_list():
    # Only one value of a name is written down, so a second one must not slip by behind the first.
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        # (aiohttp's own reader refuses some names twice - User-Agent, Host - before the stand-in is asked)
        for twice in ("apikey", "Authorization", "Accept"):
            headers = CIMultiDict({"User-Agent": "Walka/0.8.0", "apikey": "test-key", "Authorization": "Bearer " + token})
            headers["Accept"] = "application/json"
            headers.add(twice, headers[twice])
            async with walka.session.get(fake.origin + "/auth/v1/user", headers=headers) as answer:
                assert answer.status == 400
            assert fake.violations == [f"GET /auth/v1/user: the header {twice.lower()} is there twice"]
            fake.violations.clear()

    play(scenario)


def test_a_mistake_of_the_stand_ins_own_is_written_down_too(monkeypatch):
    # Its own failure would be a 500, which a program takes for "later" - and the test would pass.
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        monkeypatch.setattr(fake, "_do_user", lambda seen: 1 / 0)
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=token)
        assert status == 500
        assert len(fake.violations) == 1 and "the stand-in itself failed" in fake.violations[0]
        fake.violations.clear()

    play(scenario)


def test_a_fixture_can_collect_what_every_stand_in_of_a_test_wrote_down():
    async def scenario():
        async with FakeAccounts(wall=Clock()) as one, FakeAccounts(wall=Clock()) as two:
            async with aiohttp.ClientSession() as session:
                await Walka(one, session).ask("DELETE", "/rest/v1/walks")
                await Walka(two, session).ask("GET", "/auth/v1/settings")
                await Walka(two, session).ask("GET", "/auth/v1/user")

    asyncio.run(asyncio.wait_for(scenario(), 30))
    # the stand-ins are gone by now; what they wrote down is not
    found = fake_accounts.collect_violations()
    assert len(found) == 2 and found[0].startswith("DELETE /rest/v1/walks: ") and "/auth/v1/settings" in found[1]
    assert fake_accounts.collect_violations() == []


def test_a_stand_in_made_before_the_fixture_first_looked_is_still_asked_afterwards():
    fake = FakeAccounts(wall=Clock())  # made early, by a fixture say, and started only in the test
    assert fake_accounts.collect_violations() == []  # the look before the test

    async def scenario():
        async with fake, aiohttp.ClientSession() as session:
            await Walka(fake, session).ask("DELETE", "/rest/v1/walks")
            assert len(fake_accounts.collect_violations()) == 1  # while it runs
            await Walka(fake, session).ask("PATCH", "/rest/v1/walks")

    asyncio.run(asyncio.wait_for(scenario(), 30))
    found = fake_accounts.collect_violations()  # the look after the test
    assert len(found) == 1 and found[0].startswith("PATCH /rest/v1/walks: ")
    assert fake.violations == [] and fake_accounts.collect_violations() == []
    assert not [kept for kept in fake_accounts._EVERYWHERE if kept is fake]  # closed, and let go of


def test_what_a_test_cleared_itself_is_not_found_by_the_fixture():
    async def scenario(fake, walka, clock):
        await walka.ask("DELETE", "/rest/v1/walks")
        assert len(fake.violations) == 1
        fake.violations.clear()  # meant, here
        assert fake_accounts.collect_violations() == []

    play(scenario)


# ---- answers a test asks for -------------------------------------------------------------


def test_fail_next_answers_instead_of_the_database_as_often_as_asked_and_touches_nothing():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        fake.fail_next("/rest/v1/walks", 429, {"message": "slow down"}, times=2, headers={"Retry-After": "7"})
        for _ in range(2):
            status, said, headers = await walka.push(token, [row()])
            assert (status, said, headers["Retry-After"]) == (429, {"message": "slow down"}, "7")
        assert fake.walks(user) == []
        status, said, _ = await walka.push(token, [row()])
        assert status == 201 and len(said) == 1

        # a gateway's own page, as bytes
        fake.fail_next("/rest/v1/", 502, b"<html>Bad gateway</html>")
        status, said, headers = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
        assert (status, said) == (502, "<html>Bad gateway</html>") and headers["Content-Type"].startswith("text/html")

        # no body at all; a redirect, which nobody follows
        fake.fail_next("/auth/v1/user", 302, None, headers={"Location": "https://elsewhere.example/"})
        status, said, headers = await walka.ask("GET", "/auth/v1/user", bearer=token)
        assert (status, said, headers["Location"]) == (302, None, "https://elsewhere.example/")

        # only for one method
        fake.fail_next("/rest/v1/walks", 500, {"message": "oops"}, method="GET")
        status, _, _ = await walka.push(token, [row(start=1795130000)])
        assert status == 201
        status, _, _ = await walka.find(token, "2026-11-19T20:30:00")
        assert status == 500
        status, _, _ = await walka.find(token, "2026-11-19T20:30:00")
        assert status == 200

        # the request is still written down, and still held against the list
        assert len(fake.requests) == 8 and fake.violations == []
        fake.fail_next("/rest/v1/walks", 500, None)
        status, _, _ = await walka.ask("DELETE", "/rest/v1/walks", bearer=token)
        assert status == 400 and len(fake.violations) == 1
        fake.violations.clear()
        # ... and for a path that begins as it was said: another path is answered as ever
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=token)
        assert status == 200
        status, said, _ = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
        assert (status, said) == (200, 1)
        status, _, _ = await walka.find(token)
        assert status == 500  # the answer asked for was kept for a request that is on the list

    play(scenario)


def test_fail_next_and_drop_next_no_times_at_all_is_never():
    async def scenario(fake, walka, clock):
        token = fake.new_sign_in(fake.add_user("a@b.example"))["access_token"]
        fake.fail_next("/auth/v1/user", 500, None, times=0)
        fake.drop_next("/rest/v1/rpc/", times=0)  # (a POST: aiohttp would quietly ask a GET again)
        for _ in range(2):
            status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=token)
            assert status == 200
            status, _, _ = await walka.ask("POST", "/rest/v1/rpc/schema_version", bearer=token, body={})
            assert status == 200
        assert len(fake.requests) == 4

    play(scenario)


def test_fail_next_after_the_work_is_an_answer_that_was_lost_on_its_way():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        sent = row()
        fake.fail_next("/rest/v1/walks", 504, b"Gateway time-out", after=True)
        status, _, _ = await walka.push(token, [sent])
        assert status == 504 and [walk["id"] for walk in fake.walks(user)] == [sent["id"]]
        # sent again, with a new id: it is there already, and the look-up gives the id it got first
        status, said, _ = await walka.push(token, [row()])
        assert (status, said) == (201, [])
        _, said, _ = await walka.find(token, "2026-11-19T20:30:00")
        assert said[0]["id"] == sent["id"]

    play(scenario)


def test_drop_next_cuts_the_line_without_an_answer():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        fake.drop_next("/rest/v1/walks")
        with pytest.raises(aiohttp.ClientError):
            await walka.push(token, [row()])
        assert fake.walks(user) == [] and len(fake.requests) == 1
        status, _, _ = await walka.push(token, [row()])
        assert status == 201
        assert fake.violations == []

    play(scenario)


def test_hold_keeps_an_answer_back_until_the_gate_is_released():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        gate = fake.hold("/rest/v1/walks")
        assert not gate.arrived.is_set()
        waiting = asyncio.ensure_future(walka.push(token, [row()]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        # other requests are answered meanwhile, and nothing has been added yet
        status, _, _ = await walka.ask("GET", "/auth/v1/user", bearer=token)
        assert status == 200 and fake.walks(user) == [] and not waiting.done()
        gate.release()
        status, said, _ = await asyncio.wait_for(waiting, 5)
        assert status == 201 and len(said) == 1 and len(fake.walks(user)) == 1
        # one request was held, not the next
        status, _, _ = await asyncio.wait_for(walka.push(token, [row(start=1795130000)]), 5)
        assert status == 201

        # with after=True the work is done and only the answer waits
        gate = fake.hold("/rest/v1/walks", after=True)
        late = row(start=1795140000)
        waiting = asyncio.ensure_future(walka.push(token, [late]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        assert fake.walks(user)[-1]["id"] == late["id"] and not waiting.done()
        gate.release()
        status, said, _ = await asyncio.wait_for(waiting, 5)
        assert (status, said) == (201, [{"id": late["id"]}])

        gate = fake.hold("/auth/v1/token", after=True)
        answer = fake.new_sign_in(user)
        waiting = asyncio.ensure_future(walka.refresh(answer["refresh_token"]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        # the token has changed already: asked again, the old one gives the new one back
        _, peek, _ = await walka.refresh(answer["refresh_token"])
        assert peek["refresh_token"] != answer["refresh_token"] and not waiting.done()
        gate.release()
        status, said, _ = await asyncio.wait_for(waiting, 5)
        assert status == 200 and said["refresh_token"] == peek["refresh_token"]
        assert fake.violations == []

    play(scenario)


def test_a_held_request_can_be_answered_with_what_fail_next_says_once_it_is_let_go():
    # "It hangs, and then it fails": both asked of the same request.
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        gate = fake.hold("/rest/v1/walks")
        fake.fail_next("/rest/v1/walks", 503, b"later")
        waiting = asyncio.ensure_future(walka.push(token, [row()]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        assert not waiting.done()
        gate.release()
        status, said, _ = await asyncio.wait_for(waiting, 5)
        assert (status, said) == (503, "later") and fake.walks(user) == []
        status, _, _ = await walka.push(token, [row()])
        assert status == 201

    play(scenario)


def test_a_held_request_whose_asker_goes_away_is_dropped_where_it_stands():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        gate = fake.hold("/rest/v1/walks")
        waiting = asyncio.ensure_future(walka.push(token, [row()]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await asyncio.wait_for(gate.left.wait(), 5)
        gate.release()
        await asyncio.sleep(0)
        assert fake.walks(user) == []
        status, _, _ = await walka.push(token, [row()])
        assert status == 201

    play(scenario)


def test_a_gate_can_cut_the_line_instead_and_closing_the_stand_in_frees_whoever_waits():
    async def scenario(fake, walka, clock):
        user = fake.add_user("a@b.example")
        token = fake.new_sign_in(user)["access_token"]
        gate = fake.hold("/rest/v1/walks")
        waiting = asyncio.ensure_future(walka.push(token, [row()]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        gate.drop()
        with pytest.raises(aiohttp.ClientError):
            await asyncio.wait_for(waiting, 5)
        assert fake.walks(user) == []

        gate = fake.hold("/rest/v1/walks")
        waiting = asyncio.ensure_future(walka.push(token, [row()]))
        await asyncio.wait_for(gate.arrived.wait(), 5)
        await asyncio.wait_for(fake.close(), 5)
        with pytest.raises(aiohttp.ClientError):
            await asyncio.wait_for(waiting, 5)
        assert fake.walks(user) == []

    play(scenario)


# ---- the task in which a server waits for its next caller (Windows) -----------------------------


def test_the_task_in_which_a_server_waits_for_its_next_caller_is_known_by_its_name():
    class IocpProactor:
        """Shaped like asyncio's own for Windows, which no other system can run: the name is what is asked."""

        def accept(self, future):
            async def accept_coro(future, conn):
                await future

            return accept_coro(future, None)

    async def scenario():
        somebody_connects = asyncio.get_running_loop().create_future()
        waits = asyncio.ensure_future(IocpProactor().accept(somebody_connects))
        other = asyncio.ensure_future(asyncio.sleep(30))
        await asyncio.sleep(0)
        found = [waits_for_a_caller(task) for task in (waits, other, asyncio.current_task())]
        somebody_connects.set_result(None)
        other.cancel()
        await asyncio.wait({waits, other}, timeout=5)
        return found

    assert asyncio.run(asyncio.wait_for(scenario(), 30)) == [True, False, False]


def test_while_it_listens_the_stand_in_keeps_no_task_but_that_one_and_none_once_it_is_closed():
    """What the tests that look for tasks left behind lean on. On Windows
    the loop has one task for the stand-in that listens, and another one
    after every request; elsewhere it has none."""

    async def new_tasks(before: set, but=lambda task: False) -> list:
        loop = asyncio.get_running_loop()
        until = loop.time() + 10  # (a connection that was just closed ends a moment later)
        while True:
            left = [task for task in asyncio.all_tasks() - before if not but(task)]
            if not left or loop.time() > until:
                return left
            await asyncio.sleep(0.01)

    async def scenario():
        loop = asyncio.get_running_loop()
        before = set(asyncio.all_tasks())
        waiting = []
        async with FakeAccounts(wall=Clock()) as fake:
            for _ in range(3):
                waiting.append([task for task in asyncio.all_tasks() - before if waits_for_a_caller(task)])
                status, _headers, _text = await fake.visit(fake.origin + "/landed")
                assert status == 200
                assert await new_tasks(before, but=waits_for_a_caller) == []
        assert await new_tasks(before) == []
        return isinstance(loop, getattr(asyncio, "ProactorEventLoop", ())), waiting

    windows, waiting = asyncio.run(asyncio.wait_for(scenario(), 30))
    assert [len(tasks) for tasks in waiting] == ([1, 1, 1] if windows else [0, 0, 0])
    if windows:  # a new one each time somebody connected
        assert len({id(task) for tasks in waiting for task in tasks}) == 3
