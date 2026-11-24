"""No key of a sign-in anywhere: one whole story through a whole Walka, and then a search.

The keys of a sign-in - the verifier, the one-time code, the access
token, the refresh token, the other site's tokens - live in memory, and
the refresh token alone in the system's store for secrets. They are in no
file, not in what the dashboard serves, and not in the log, which people
attach to public issues. The Walka account's id may stand in one file
only (``walka-account.json``), and the e-mail address there and in the
snapshot, where the card shows it. And the mark that is made when the
question before sending is answered - a key to nothing, kept like one all
the same - stands in the system's store and in that one file, while
sending is on, and nowhere else.

So here Walka runs as it runs: a real ``App``, started, with its
dashboard served the way the command line serves it, and its log kept the
way ``walka -v`` keeps it - at debug level, in ``walka.log`` in the data
folder. One story goes through it, by the dashboard's own routes, and
through everything that can go wrong on the way: the person says no at
the other site, the exchange gets a gateway's page for an answer, a code
from somebody else's sign-in is planted, the first step is not taken, a
token runs out in the middle of a send; then every answer a send can get
(brief 5.3) - a walk that is refused, an id that is taken, the accounts
saying "not now" or nothing at all, a request that is not taken, a
gateway answering in their place, a full Walka account, one that is not
open; then the sign-in ends elsewhere, somebody else signs in, and signs
out. (Leaks live in the texts of errors.)

Then everything is searched for every key that was in play - each also
as an address would spell it, and as JSON would: every file under the
data folder and the game's addon folder, every snapshot and every answer
the dashboard gave, the environment, and the log.

And since the story asks the accounts everything Walka ever asks them:
what the stand-in saw is the list of the brief (5.4), whole, and nothing
else.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit

import aiohttp
import pytest

import fake_accounts
import walka_account_rig
from test_walka_account_wiring import hand_in, left_running, run, walk_on
from walka import cli, web
from walka.app import App
from walka.walka_account import ledger, signin
from walka_account_rig import PAD, Rig


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    # (Nothing outside the list of what Walka may ask - and the rig's own look for keys, after the test's.)
    walka_account_rig.after_test(caplog)
    for rig in walka_account_rig.MADE:
        assert asked_of_the_accounts(rig.fake) <= THE_BRIEFS_LIST


# The whole of the program's traffic, as the brief lists it (5.4) - written out here, not read from the
# stand-in. The first is the browser's; the rest are Python's. (GET /auth/v1/settings is not used.)
THE_BRIEFS_LIST = {
    ("GET", "/auth/v1/authorize"),
    ("POST", "/auth/v1/token"),
    ("POST", "/auth/v1/logout"),
    ("GET", "/auth/v1/user"),
    ("POST", "/rest/v1/rpc/schema_version"),
    ("GET", "/rest/v1/profiles"),
    ("GET", "/rest/v1/walks"),
    ("POST", "/rest/v1/walks"),
}


def asked_of_the_accounts(fake: fake_accounts.FakeAccounts) -> set[tuple[str, str]]:
    return {(seen.method, seen.path) for seen in fake.requests}


# ---- the search ----------------------------------------------------------------------------------


def spellings(needle: str) -> set[str]:
    """A text as it is, as an address would hold it, and as JSON would."""
    return {
        needle,
        quote(needle, safe=""),
        quote(needle),
        quote_plus(needle),
        json.dumps(needle)[1:-1],
        json.dumps(needle, ensure_ascii=False)[1:-1],
    } - {""}


def found(needles: Iterable[str], text: str) -> list[str]:
    """Which of the needles stand in the text, in any spelling."""
    return sorted({needle for needle in needles if any(spelling in text for spelling in spellings(needle))})


def places_with(needles: Iterable[str], places: dict[str, str]) -> dict[str, list[str]]:
    """Where any of the needles stands: the place -> the needles found there."""
    needles = list(needles)
    return {name: there for name, text in places.items() if (there := found(needles, text))}


def files_under(folder: Path, name: str) -> dict[str, str]:
    """Every file under a folder, as text: ``name/<its path>`` -> what stands in it. And their names."""
    paths = [path for path in sorted(folder.rglob("*")) if path.is_file()]
    texts = {
        f"{name}/{path.relative_to(folder).as_posix()}": path.read_bytes().decode("utf-8", "replace") for path in paths
    }
    texts[f"the names of the files under {name}"] = "\n".join(path.as_posix() for path in sorted(folder.rglob("*")))
    return texts


def test_the_search_finds_a_text_as_it_is_as_an_address_spells_it_and_as_json_does():
    key, email = "ab+c/d=e", "walker@mail.example"
    assert found([key], "it stands here: ab+c/d=e.") == [key]
    assert found([key], "http://127.0.0.1/?code=ab%2Bc%2Fd%3De") == [key]
    assert found([key], "path/ab%2Bc/d%3De") == [key]  # (as a path holds it: the stroke stays)
    assert found([email], "redirect_to=walker%40mail.example&x=1") == [email]
    assert found(["two words"], "q=two+words") == ["two words"]
    assert found(['say "no"'], json.dumps({"error": 'they say "no" here'})) == ['say "no"']
    assert found(["schlüssel"], json.dumps("ein schlüssel")) == ["schlüssel"]
    assert found([key, email, ""], "nothing of the kind: ab+c, d=e, walker at mail.example") == []
    assert places_with([key], {"one": "ab+c/d=e", "two": "clean"}) == {"one": [key]}


# ---- Walka, served and looked at -----------------------------------------------------------------


class Served:
    """Walka's dashboard, served the way the command line serves it (``web.serve``), and somebody who
    looks at it: everything it ever answers is kept, to be searched afterwards."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.states: list[str] = []  # every snapshot read from /api/state
        self.pushed: list[str] = []  # every line of the event stream
        self.answers: dict[str, list[str]] = {}  # route -> what it answered, each time
        self.read: dict[str, list[str]] = {}  # every other GET route -> what it gave, each time

    async def __aenter__(self) -> Served:
        self._runner = await web.serve(self.app, "127.0.0.1", 0)
        self.port = self._runner.addresses[0][1]
        self.there = f"http://127.0.0.1:{self.port}"
        self._session = aiohttp.ClientSession()
        self._stream = asyncio.ensure_future(self._listen())
        return self

    async def __aexit__(self, *exc: object) -> None:
        # (The stream ends by itself when Walka is closing; cut off otherwise.)
        closing = self.app._stopping.is_set()
        if not closing:
            self._stream.cancel()
        (ended,) = await asyncio.gather(self._stream, return_exceptions=True)
        await self._session.close()
        await self._runner.cleanup()
        # Had the stream broken off before its time, what the page itself gets would have gone unsearched.
        assert exc[0] is not None or not closing or ended is None, f"the event stream broke off: {type(ended).__name__}"

    async def _listen(self) -> None:
        """The page's own way: the event stream, for as long as Walka runs."""
        async with self._session.get(self.there + "/api/events") as stream:
            async for line in stream.content:
                self.pushed.append(line.decode("utf-8"))

    async def state(self) -> dict:
        async with self._session.get(self.there + "/api/state") as response:
            assert response.status == 200
            self.states.append(await response.text())
        return json.loads(self.states[-1])

    async def piece(self) -> dict:
        """The Walka account's piece of the snapshot."""
        return (await self.state())["walka_account"]

    async def pushed_piece(self) -> dict:
        """The same, as the event stream brings it next (it brings a snapshot twice a second)."""
        seen = len(self.pushed)
        for _ in range(2000):
            new = [line for line in self.pushed[seen:] if line.startswith("data: ")]
            if new:
                return json.loads(new[-1][len("data: ") :])["walka_account"]
            await asyncio.sleep(0.01)
        raise AssertionError("the event stream brought nothing")

    async def press(self, button: str, body: Any = None) -> tuple[int, Any]:
        """A button of the Walka-account card: the status, and the answer."""
        return await self.post("/api/walka-account/" + button, body)

    async def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        async with self._session.post(self.there + path, json={} if body is None else body) as response:
            text = await response.text()
            self.answers.setdefault(path, []).append(text)
            return response.status, json.loads(text)

    async def read_everything(self) -> None:
        """Every GET route of the dashboard, once (the event stream is read all along)."""
        walk = self.app.store.records[-1].id
        await self.state()
        for route in web.create_app(self.app).router.routes():
            if route.method != "GET" or route.resource is None:
                continue
            if route.resource.canonical in ("/api/events", "/api/state"):
                continue
            path = route.resource.canonical.replace("{session_id}", walk)
            path += {"/api/road": "?character=0123abcd", "/static": "/index.html"}.get(path, "")
            async with self._session.get(self.there + path) as response:
                assert response.status == 200, path
                self.read.setdefault(path, []).append((await response.read()).decode("utf-8", "replace"))


OK = (200, {"ok": True})
NOT_SIGNED_IN = (409, {"ok": False, "error": "not signed in"})


async def buttons_that_refuse(page: Served, *, signed_in: bool) -> None:
    """What the card's buttons answer when they are not for pressing: each answer is among what is
    searched afterwards, and none of them may say who is signed in."""
    for body in ({}, {"way": "email"}, {"way": ["github"]}):
        assert (await page.press("sign-in", body))[0] == 400
    for body in ({}, {"question": "not-the-question", "send": "all"}, {"send": "all"}):
        assert (await page.press("sending", body))[0] == 400
    if signed_in:
        assert await page.press("sign-in", {"way": "github"}) == (409, {"ok": False, "error": "signed in already"})
    else:
        for button in ("look-again", "question", "sending/off"):
            assert await page.press(button) == NOT_SIGNED_IN
        assert await page.press("sign-out") == OK
    assert await page.press("notice/ok") == OK


def refusal(code: str, message: str, details: str | None = None) -> dict:
    """A refusal in the database's own shape."""
    return {"code": code, "details": details, "hint": None, "message": message}


def utc_text(start: float) -> str:
    return datetime.fromtimestamp(start, timezone.utc).isoformat()


def with_another_challenge(opened: str, challenge: str) -> str:
    """The address of somebody else's sign-in, sent to THIS sign-in's listener: they know where Walka
    listens, and not its verifier."""
    address = urlsplit(opened)
    asked = [(name, challenge if name == "code_challenge" else value) for name, value in parse_qsl(address.query)]
    return f"{address.scheme}://{address.netloc}{address.path}?{urlencode(asked)}"


def as_a_number(port: int) -> re.Pattern:
    """The port where it stands as a number of its own - not as a part of a longer one."""
    return re.compile(rf"(?<![\d.]){port}(?!\d)")


ZERO = {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}


def refreshes(rig: Rig) -> int:
    return sum(1 for seen in rig.asked("/auth/v1/token") if ("grant_type", "refresh_token") in seen.query)


# What the accounts say, and Walka must repeat nowhere: a gateway's own page, the words of a 500, what a
# refusal says of the row it refused (the whole row, the Walka account's id in it), what the other site
# said when the person said no, and what a sign-in's answer holds of the person beyond the e-mail address.
GATEWAYS_PAGE = b"<html><body><h1>502 Bad Gateway</h1>the-gateways-own-page-4d1f</body></html>"
WORDS_OF_A_500 = "the-words-of-a-500-93ab"
ABOUT_THE_ROW = "the-failing-row-in-full-77c2"
SAID_TO_WALKA_ONLY = "said-to-walka-and-to-nobody-else-51e8"
NEVER_REPEATED = (
    "the-gateways-own-page-4d1f",
    WORDS_OF_A_500,
    ABOUT_THE_ROW,
    SAID_TO_WALKA_ONLY,
    "elsewhere.example",
    fake_accounts.DENIED_DESCRIPTION,
    "Somebody's Real Name",
    "pictures.example",
    "other-site-",
)
# What the database says of a walk it does not take, by its code. This the card shows, and the file keeps.
NOT_TAKEN = {
    "23514": 'new row for relation "walks" violates check constraint "walks_check1"',
    "22P02": 'invalid input syntax for type integer: "66.0"',
}


def said(code: str, sentence: str) -> dict:
    """A refusal that is not about a walk: nothing of it is for anybody but Walka."""
    return refusal(code, f"{sentence} ({SAID_TO_WALKA_ONLY})", f"{ABOUT_THE_ROW}: {SAID_TO_WALKA_ONLY}")


# "Not now": no answer at all (None), or one that only says so. Each: status, body, headers.
NOT_NOW: tuple[tuple[Any, Any, Any], ...] = (
    (500, refusal("XX000", WORDS_OF_A_500, WORDS_OF_A_500), None),
    (503, GATEWAYS_PAGE, None),
    (429, said("over_request_rate_limit", "Too many requests"), {"Retry-After": "120"}),
    (None, None, None),
)
# Answers that say nothing about any walk: status, body, headers, and the one word the card then has.
NOTHING_ABOUT_A_WALK: tuple[tuple[int, Any, Any, str], ...] = (
    (400, said("PGRST102", "Empty or invalid json"), None, "stopped"),
    (400, said("PGRST204", "Could not find the 'device' column of 'walks' in the schema cache"), None, "stopped"),
    (428, said("428C9", 'cannot insert a non-DEFAULT value into column "start_key"'), None, "stopped"),
    (401, said("PGRST301", "No suitable key or wrong key type"), None, "stopped"),
    (403, said("42501", "permission denied for table walks"), None, "stopped"),
    (404, said("PGRST205", "Could not find the table 'public.walks' in the schema cache"), None, "stopped"),
    (403, GATEWAYS_PAGE, None, "stopped"),
    (200, [], None, "stopped"),
    (302, None, {"Location": f"https://elsewhere.example/{SAID_TO_WALKA_ONLY}"}, "stopped"),
    (400, refusal("23514", fake_accounts.FULL.format(20000), ABOUT_THE_ROW), None, "full"),
    (400, refusal("23514", fake_accounts.ADULTS, ABOUT_THE_ROW), None, "not_open"),
)

ANNA, BEN = "anna.walker@mail.example", "ben@other.example"


class Story:
    """What the story left behind, for the search."""

    def __init__(self) -> None:
        self.outside_the_card: list[str] = []  # every snapshot that was served, but for the Walka account's piece
        self.walk_files: dict[str, bytes] = {}  # the files of the walks, byte for byte
        self.keys: set[str] = set()  # every key of a sign-in that was in play: allowed nowhere
        self.marks: set[str] = set()  # every mark that was made for an answer to the question, or written over one
        self.book_while_on = ""  # walka-account.json as it was while sending was on: there a mark does stand
        self.users: list[str] = []  # the Walka accounts' ids: in walka-account.json only
        self.emails: list[str] = []  # in walka-account.json and in the snapshot only
        self.accounts_at: list[str] = []  # where the accounts are: in the file and the snapshot only
        self.listened_at: list[int] = []  # the ports Walka listened at for the browser: in no line of the log
        self.places: dict[str, str] = {}  # what the dashboard served, by route
        self.walks_sent: dict[str, list[str]] = {}
        self.asked: set[tuple[str, str]] = set()


def keys_in_play(rig: Rig) -> set[str]:
    """Every key of every sign-in of the story - gathered from where a key passes: what the accounts
    gave out, the address the browser was opened at, where the browser was sent back to, and what
    Walka itself asked the accounts with."""
    keys = {key for key in rig.needles() if key}
    for opened in rig.opened:
        asked = dict(parse_qsl(urlsplit(opened).query))
        back = urlsplit(asked["redirect_to"])
        # The address itself, the challenge in it, and this sign-in's listener: its address, its path, its port.
        keys |= {opened, asked["code_challenge"], asked["redirect_to"], back.path.lstrip("/"), f"127.0.0.1:{back.port}"}
        assert len(back.path) > 32 and back.hostname == "127.0.0.1"
    for sent in rig.fake.sent_to:
        keys |= {sent, *(value for name, value in parse_qsl(urlsplit(sent).query) if name == "code")}
    for seen in rig.fake.requests:
        bearer = seen.header("Authorization")
        if bearer is not None:
            keys |= {bearer, bearer.split(" ", 1)[-1]}
        if seen.path == "/auth/v1/token" and isinstance(seen.json, dict):
            keys |= {value for value in seen.json.values() if isinstance(value, str)}  # the code, the verifier, the key
        if seen.path == "/auth/v1/authorize":
            keys |= {value for name, value in seen.query if name in ("code_challenge", "redirect_to")}
    keys.add(rig.fake.key)  # which project is asked: no secret, and no business of a file or a log all the same
    return {key for key in keys if key}


async def the_whole_story(rig: Rig, tmp_path: Path, story: Story) -> None:
    log = logging.getLogger("walka")
    app = hand_in(rig, tmp_path)
    anna = rig.person(ANNA, way="github", adult=False)  # (a sign-in that has never been to the website)
    async with Served(app) as page:
        await app.start()
        await rig.idle()
        old = [rig.walk(3 * 86400), rig.walk(2 * 86400)]
        ago = iter(range(80000, 0, -1000))
        gone, aside = 0, []  # walks that went to Anna's Walka account, and walks it did not take

        def walks_are_as_they_were_written() -> None:
            """Every walk's file, byte for byte, is as it was when it was first looked at here - which is
            before the sender next has its turn - and none is gone."""
            now = rig.walk_files()
            assert {name: now.get(name) for name in story.walk_files} == story.walk_files
            story.walk_files = now

        async def a_walk():
            """A walk from earlier today is saved, and the sender has its turn."""
            record = rig.walk(next(ago))
            walks_are_as_they_were_written()
            await rig.saved()
            return record

        walks_are_as_they_were_written()

        rig.fake.add_walk(anna, utc_text(old[0].start))  # (the first is in the Walka account already, from the website)
        assert (await page.piece())["state"] == "signed_out"
        await buttons_that_refuse(page, signed_in=False)
        # (The walker's weight is saved, as on a first day: from then on Walka keeps state.json too.)
        assert (await page.post("/api/config", {"profile": {"weight_kg": 81.5}}))[0] == 200

        # The person presses "Sign in" - and says no at the other site.
        rig.fake.next_browser_user(None)
        assert await page.press("sign-in", {"way": "github"}) == OK
        await rig.idle()
        assert signin.PAGE_NOTHING in rig.pages[-1][2] and (await page.piece())["problem"] == "refused"
        rig.fake.next_browser_user(anna)

        # Again - and where the accounts should answer the exchange, a gateway answers with a page of its own.
        rig.fake.fail_next("/auth/v1/token", 502, GATEWAYS_PAGE)
        assert await page.press("sign-in", {"way": "google"}) == OK
        await rig.idle()
        assert signin.PAGE_FAILED in rig.pages[-1][2] and (await page.piece())["problem"] in ("failed", "no_answer")

        # Again - and before the person comes back, somebody else's sign-in is sent to Walka's listener.
        rig.browser_comes = False
        assert await page.press("sign-in", {"way": "discord"}) == OK
        await rig.idle()
        theirs = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).decode("ascii").rstrip("=")
        planted = with_another_challenge(rig.opened[-1], theirs)
        story.keys |= {planted, theirs}
        status, _headers, text = await rig.fake.browser(planted)
        await rig.idle()
        assert (status, signin.PAGE_FAILED in text) == (200, True) and (await page.piece())["state"] == "signed_out"
        rig.browser_comes = True

        # And now it works. The first step of the Walka account is not taken: nothing can be switched on.
        assert await page.press("sign-in", {"way": "github"}) == OK
        assert (await page.piece())["state"] == "signing_in"
        await rig.idle()
        piece = await page.piece()
        assert (piece["state"], piece["email"], piece["way"]) == ("signed_in", ANNA, "github")
        assert piece["first_step"] == "not_taken"
        assert signin.PAGE_SIGNED_IN in rig.pages[-1][2] and len(rig.keystore.secrets()) == 1
        assert await page.pushed_piece() == piece  # (the page itself reads the stream: it shows the same)
        assert await page.press("question") == OK
        await rig.idle()
        assert (await page.piece())["question"] is None and rig.pushes() == []
        rig.fake.take_first_step(anna)
        assert await page.press("look-again") == OK
        await rig.idle()
        assert (await page.piece())["first_step"] == "taken"

        # The question, and its answer: the walks go. One was there already.
        assert await page.press("question") == OK
        await rig.idle()
        question = (await page.piece())["question"]
        assert (question["email"], question["waiting"]) == (ANNA, 2)
        assert (await page.pushed_piece())["question"] == question
        assert await page.press("sending", {"question": question["id"], "send": "all"}) == OK
        await rig.idle()
        piece = await page.piece()
        gone += 2
        assert (piece["sending"], piece["counts"]["sent"], piece["last"]["word"]) == (True, gone, "sent")
        assert rig.entry_of(anna, old[0])["state"] == "there" and rig.entry_of(anna, old[1])["state"] == "added"
        # (The answer made a mark: it is in the store, and in the file beside "sending is on".)
        story.book_while_on = (rig.dir / ledger.FILE_NAME).read_text("utf-8")
        assert rig.mark() is not None and rig.mark() in story.book_while_on

        # Somebody walks; the walk is recorded, saved, and goes by itself.
        await walk_on(rig, app, 60, then_stand=True)
        walks_are_as_they_were_written()
        await rig.idle()
        recorded = app.store.records[-1]
        gone += 1
        assert recorded.treadmill == PAD and rig.entry_of(anna, recorded)["state"] == "added"
        await buttons_that_refuse(page, signed_in=True)
        assert (await page.piece())["sending"] is True

        # The access token runs out in the middle of a send: a new one is made, and the walk goes.
        rig.fake.expire_access_tokens()
        await a_walk()
        gone += 1
        assert refreshes(rig) == 1 and (await page.piece())["counts"] == {**ZERO, "sent": gone}

        # Then every answer a send can get (brief 5.3), one walk after the other. First the answers
        # about ONE walk. Two are for good: the walk is set aside, and shown with the database's own
        # sentence - which is all of the answer that is kept.
        about_the_row = f"Failing row contains ({anna}, {ANNA}, {ABOUT_THE_ROW})."
        for code, sentence in NOT_TAKEN.items():
            rig.fake.fail_next("/rest/v1/walks", 400, refusal(code, sentence, about_the_row), method="POST")
            aside.append((await a_walk()).id)
            piece = await page.piece()
            assert (piece["counts"]["aside"], piece["aside"][0]["id"]) == (len(aside), aside[-1])
            assert (piece["aside"][0]["code"], piece["aside"][0]["message"]) == (code, sentence)
        assert (await page.pushed_piece())["aside"] == piece["aside"]
        # Two are not for good. An id that is taken: a new one is made, and the walk goes ...
        taken = refusal("23505", 'duplicate key value violates unique constraint "walks_pkey"', about_the_row)
        rig.fake.fail_next("/rest/v1/walks", 409, taken, method="POST")
        await a_walk()
        gone += 1
        # ... and a walk "in the days to come": it stays here, and waits for the next start of Walka.
        to_come = refusal("23514", fake_accounts.DAYS_TO_COME, about_the_row)
        rig.fake.fail_next("/rest/v1/walks", 400, to_come, method="POST")
        put_off = await a_walk()
        counts = {**ZERO, "sent": gone, "aside": len(aside), "waiting": 1}
        assert (await page.piece())["counts"] == counts

        # No answer, or one that only says "not now": later. And later the walk goes.
        for status, body, headers in NOT_NOW:
            if status is None:
                rig.fake.drop_next("/rest/v1/walks", method="POST")
            else:
                rig.fake.fail_next("/rest/v1/walks", status, body, headers=headers, method="POST")
            await a_walk()
            assert (await page.piece())["last"]["word"] == "later", (status, body)
            rig.wait.time_is_up()
            await rig.idle()
            gone += 1
            assert (await page.piece())["counts"] == {**counts, "sent": gone}, (status, body)

        # An answer that says nothing about any walk - the request was not taken, or a gateway answered
        # in the accounts' place: this run stops, and nothing is set aside. Sending is switched off and,
        # through the question, on again: the walk that waited goes.
        for status, body, headers, word in NOTHING_ABOUT_A_WALK:
            rig.fake.fail_next("/rest/v1/walks", status, body, headers=headers, method="POST")
            await a_walk()
            piece = await page.piece()
            assert piece["last"]["word"] == word, (status, body)
            assert piece["counts"] == {**counts, "sent": gone, "waiting": 2}, (status, body)
            if word == "not_open":
                # ("Walka is for adults.": Walka asked who is there, and now thinks the first step is not taken.)
                assert piece["first_step"] == "not_taken" and len(rig.asked("/auth/v1/user")) == 3
                assert await page.press("look-again") == OK
                await rig.idle()
                assert (await page.piece())["first_step"] == "taken"
            assert await page.press("sending/off") == OK
            await rig.idle()
            assert await page.press("question") == OK
            await rig.idle()
            question = (await page.piece())["question"]
            assert await page.press("sending", {"question": question["id"], "send": "all"}) == OK
            await rig.idle()
            gone += 1
            assert (await page.piece())["counts"] == {**counts, "sent": gone}, (status, body)
        assert rig.entry_of(anna, put_off) is None and len(rig.account_in_book(anna)["walks"]) == gone + len(aside)

        # The sign-in ends elsewhere. The next walk finds that out: Walka forgets the keys, and says so.
        rig.fake.sign_out_everywhere(anna)
        await rig.advance(3700)
        unsent = rig.walk(60)
        walks_are_as_they_were_written()
        await rig.saved()
        piece = await page.piece()
        assert (piece["state"], piece["notice"], piece["sending"]) == ("signed_out", "ended", False)
        assert rig.keystore.secrets() == [] and refreshes(rig) == 2
        assert len([row for row in rig.fake.walks(anna) if row["origin"] == "walka"]) == gone - 1 == 19
        await buttons_that_refuse(page, signed_in=False)
        await rig.idle()
        assert (await page.piece())["notice"] == ""  # ("OK" was among them)

        # Somebody else signs in on this computer, with a public page. Not one walk goes before the
        # question is answered again - and then only walks from now on.
        ben = rig.person(BEN, way="google", public=True, handle="ben", show_walks=True)
        rig.fake.next_browser_user(ben)
        assert await page.press("sign-in", {"way": "google"}) == OK
        await rig.idle()
        piece = await page.piece()
        assert (piece["state"], piece["email"]) == ("signed_in", BEN)
        assert (piece["sending"], piece["first_step"]) == (False, "taken")
        rig.walk(30)
        walks_are_as_they_were_written()
        await rig.saved()
        assert rig.fake.walks(ben) == []
        assert await page.press("question") == OK
        await rig.idle()
        question = (await page.piece())["question"]
        assert (question["email"], question["public"], question["waiting"]) == (BEN, True, len(app.store.records))
        assert (await page.pushed_piece())["question"] == question
        assert await page.press("sending", {"question": question["id"], "send": "from_now_on"}) == OK
        await rig.idle()
        assert rig.fake.walks(ben) == []
        await walk_on(rig, app, 60, then_stand=True)
        walks_are_as_they_were_written()
        await rig.idle()
        assert [row["client_id"] for row in rig.fake.walks(ben)] == [app.store.records[-1].id]

        # Whatever the accounts said of a walk, its file is as it was written: sent, there already, set
        # aside, put off or never sent - not one was changed, and not one is gone.
        walks_are_as_they_were_written()
        assert len(story.walk_files) == len(app.store.records) >= 26

        # And signs out: the key leaves the store, and the accounts are told.
        await page.read_everything()
        assert await page.press("sign-out") == OK
        assert (await page.piece())["state"] == "signed_out"
        await rig.idle()
        assert rig.keystore.secrets() == [] and len(rig.asked("/auth/v1/logout")) == 1
        await page.read_everything()
        assert app.sync_addon()  # (what the game's addon is given, written once more at the very end)
        log.info("the story is told")
        await app.stop()
    assert await left_running() == []
    assert rig.walk_files() == story.walk_files  # (signing out and closing change no walk either)
    assert len(story.walk_files) == 26

    story.users = [anna, ben]
    story.emails = [ANNA, BEN]
    story.accounts_at = [rig.fake.origin, rig.fake.origin.split("//")[-1]]
    story.listened_at = [urlsplit(dict(parse_qsl(urlsplit(opened).query))["redirect_to"]).port for opened in rig.opened]
    assert len(set(story.listened_at)) == 5 and page.port not in story.listened_at
    story.keys |= keys_in_play(rig)
    story.marks = {mark for mark in rig.marks_seen() if mark}
    assert rig.keystore.marks() == []  # (everybody has signed out: no mark is left in the store)
    story.places = {
        "/api/state": "\n".join(page.states),
        "/api/events": "".join(page.pushed),
        **{f"GET {path}": "\n".join(texts) for path, texts in page.read.items()},
        **{f"POST {path}": "\n".join(texts) for path, texts in page.answers.items()},
    }
    snapshots = page.states + [line[len("data: ") :] for line in page.pushed if line.startswith("data: ")]
    for snapshot in map(json.loads, snapshots):
        assert snapshot.pop("walka_account")["host"] == story.accounts_at[1]
        story.outside_the_card.append(json.dumps(snapshot, ensure_ascii=False))
    story.walks_sent = {
        ANNA: sorted(row["client_id"] for row in rig.fake.walks(anna) if row["origin"] == "walka"),
        BEN: sorted(row["client_id"] for row in rig.fake.walks(ben)),
    }
    story.asked = asked_of_the_accounts(rig.fake)
    assert not {unsent.id, put_off.id, *aside} & set(story.walks_sent[ANNA] + story.walks_sent[BEN])
    assert len(page.pushed) >= 8 and len(page.states) >= 20 and len(page.read) >= 8


def test_after_a_whole_story_no_key_of_a_sign_in_stands_in_any_file_any_answer_or_the_log(tmp_path, caplog):
    data, addon = tmp_path / "data", tmp_path / "AddOns"
    story = Story()
    console = io.StringIO()

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            assert rig.dir == data
            await the_whole_story(rig, tmp_path, story)

    # The log as ``walka -v`` keeps it: at debug level, in walka.log, in the data folder.
    stop_logging = cli._start_logging(True, stream=console, file=data / cli.LOG_FILE)
    try:
        logging.getLogger("walka").info("the story begins")
        run(scenario, 90)
    finally:
        stop_logging()

    # It was the whole story: everything on the brief's list was asked, and nothing else; walks went.
    assert story.asked == THE_BRIEFS_LIST
    assert len(story.walks_sent[ANNA]) == 19 and len(story.walks_sent[BEN]) == 1
    assert len(story.keys) >= 40 and all(isinstance(key, str) and len(key) >= 8 for key in story.keys)

    # Everything that is written down or given out anywhere.
    kept_log = {name: text for name, text in files_under(data, "data").items() if name.startswith("data/walka.log")}
    assert sorted(kept_log) == ["data/walka.log"]  # (nothing of it was turned over into walka.log.1 and lost)
    whole = kept_log["data/walka.log"]
    assert "the story begins" in whole and "the story is told" in whole and " DEBUG " in whole
    # (Walka's own lines are in it, and asyncio's at debug level - which names what the loop is built on.)
    assert "Walk saved" in whole and re.search(r" DEBUG +Using (selector|proactor)", whole)
    written = "\n".join(logging.Formatter().format(record) for record in caplog.records)
    assert "the story is told" in written and "the story is told" in console.getvalue()
    places = {
        **files_under(data, "data"),
        **files_under(addon, "AddOns"),
        **story.places,
        "the environment": "\n".join(f"{name}={value}" for name, value in os.environ.items()),
        "the log, as it was written": written,
        "the console": console.getvalue(),
    }
    book, shown = f"data/{ledger.FILE_NAME}", {"/api/state", "/api/events"}
    for name in ("data/config.json", "data/state.json", book, "AddOns/Walka/CompanionData.lua", "GET /api/config"):
        assert places[name], name
    assert sum(1 for name in places if name.startswith("data/sessions/")) == 26 and "GET /api/sessions" in places
    # No file is left half-written, no walk is left unfinished, and none of the part's but its book and
    # its lock. (The recorder's own lock stays where a walk was written down: an empty file.)
    names = places["the names of the files under data"]
    assert ".tmp" not in names and "active-session.json" not in names
    assert places["data/active-session.lock"] == ""
    of_the_part = sorted(name for name in places if "account" in name and name.startswith("data/"))
    assert of_the_part == [book, "data/walka-account.lock"]

    # The canaries: the search does find what IS there.
    assert set(places_with(story.emails, places)) == {book, *shown}
    assert places_with(story.users, places) == {book: sorted(story.users)}
    assert found(story.emails, places[book]) == sorted(story.emails) == found(story.emails, places["/api/state"])
    # (The database's sentence about the walk it did not take has quotation marks in it: in the file
    # and in the snapshot it stands as JSON spells it, and is found there all the same - and only there.)
    for sentence in NOT_TAKEN.values():
        assert sentence not in places[book] and set(places_with([sentence], places)) == {book, *shown}

    # No key of any sign-in, anywhere. Not what the accounts said either.
    assert places_with(story.keys, places) == {}
    # The marks: each stood in walka-account.json while sending was on under it (the canary: one is
    # found in the file as it was then) - and in nothing else that is written down or given out, ever.
    # Now that everybody has signed out, the file bears none either.
    assert len(story.marks) >= 2 and all(len(mark) == 32 for mark in story.marks)
    assert len(found(story.marks, story.book_while_on)) == 1
    assert places_with(story.marks, places) == {}
    assert places_with(NEVER_REPEATED, places) == {}
    # The Walka account's id: in its file only. The e-mail address: there and in the snapshot only
    # (seen above). Where the accounts are: there and in the snapshot only - never in the log.
    assert set(places_with(story.accounts_at, places)) == {book, *shown}
    # Outside the card of the Walka account the dashboard was given no word of one, signed in or not:
    # not in the Live view's lines, the first-run list or the settings - and the e-mail address and
    # where the accounts are stood in the card's own piece of the snapshot, and in no other.
    rest = "\n".join(story.outside_the_card)
    assert len(story.outside_the_card) >= 28 and '"events"' in rest and "Walk saved" in rest and '"setup"' in rest
    assert "account" not in rest.lower() and found((*story.emails, *story.accounts_at), rest) == []
    # The settings, what Walka remembers between runs and what the game's addon is given hold nothing
    # of it at all: sending cannot be switched on by a file that is copied, or through /api/config.
    for name in ("data/config.json", "GET /api/config", "data/state.json", "AddOns/Walka/CompanionData.lua"):
        assert not [word for word in ("account", "sending", "signed") if word in places[name].lower()], name
    # And of the Walka account the log says no word that names it.
    said = (whole, written, console.getvalue())
    named = (*story.users, *story.emails, *story.accounts_at)
    for word in (*named, "authorize", "redirect_to", "code_challenge", "Bearer"):
        assert not [text for text in said if word in text], word
    # Nor where Walka listened for the browser - not even the port by itself, as a number.
    for port in story.listened_at:
        assert not [text for text in said if as_a_number(port).search(text)], port
    assert as_a_number(40123).search("listening at port 40123.") and not as_a_number(40123).search("0.40123 s")


# ---- how the part is made: nothing in it can put a key where others read it ------------------------


def test_the_part_starts_no_other_program_and_reads_no_environment_and_no_command_line():
    """A key on a command line can be read by every user of the computer,
    and so can a program's environment by some: the part has neither. It
    asks the system's store for secrets from inside this program, and
    opens no browser itself (it is handed something that does)."""
    package = Path(signin.__file__).parent
    modules = sorted(package.glob("*.py"))
    assert len(modules) >= 10
    never_imported = {"subprocess", "multiprocessing", "pty", "shlex", "webbrowser", "pickle", "shelve", "tempfile"}
    never_called = ("system", "popen", "exec", "spawn", "startfile", "fork", "posix_spawn")
    never_called += ("environ", "getenv", "putenv")
    for path in modules:
        tree = ast.parse(path.read_text("utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
                if node.module == "os":
                    assert not [alias.name for alias in node.names if alias.name.startswith(never_called)], path.name
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                assert not node.attr.startswith(never_called), f"{path.name}: os.{node.attr}"
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
                assert node.attr not in ("argv", "orig_argv"), f"{path.name}: sys.{node.attr}"
        assert imported & never_imported == set(), path.name
        # (Nor by a name: the tools that take a secret as an argument.)
        text = path.read_text("utf-8").lower()
        for tool in ("cmdkey", "add-generic-password", "find-generic-password", "powershell", "secret-tool"):
            assert tool not in text, f"{path.name}: {tool}"


# ---- decision 12: the live project's address and key are in no file that is kept --------------------


def test_no_file_that_git_keeps_holds_the_shape_of_the_live_address_or_of_its_key(repo_root: Path):
    """Every file git keeps for this repository (``git ls-files``) - code,
    tests, documents, workflows, whatever its folder is called: none holds
    a publishable key of the accounts' kind, nor a project's address there.
    (test_walka_account_wiring.py walks the folders as they lie on disk,
    kept or not. Made-up ones are short and end in ``.example``.)"""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not here to say which files it keeps")
    if listed.returncode != 0:
        pytest.skip("this is not a checkout that git keeps")
    names = [name for name in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if name]
    assert len(names) > 150 and ".github/workflows/ci.yml" in names and "companion/src/walka/cli.py" in names
    key = re.compile(rb"sb_publishable_[A-Za-z0-9_-]{20,}")
    address = re.compile(rb"[A-Za-z0-9]{20}\.supabase\.co", re.IGNORECASE)
    held = []
    for name in names:
        try:
            data = (repo_root / name).read_bytes()
        except OSError:
            continue  # (deleted in this working copy, and not committed yet)
        if key.search(data) or address.search(data):
            held.append(name)
    assert held == []
    # ... and the look finds one of each kind. (Put together here, so that this file holds neither.)
    assert key.search(b"sb_publishable_" + b"Ab1_" * 5) and not key.search(b"sb_publishable_" + b"x" * 19)
    assert address.search(b"https://" + b"ab" * 10 + b".supabase.co")
    assert not address.search(b"ab" * 9 + b".supabase.co")
