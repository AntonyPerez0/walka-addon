"""One sign-in through the browser (walka_account.signin), tried against the stand-in.

The "browser" is the stand-in's own: it asks the address Walka would have
opened, goes where it is sent - to the listener - and gives the page it
ends on. Where the accounts must not answer at all, a client that hangs
or fails stands in for them.

After EVERY test: the stand-in saw nothing outside the list of what Walka
may ask; the log holds no key, no code, no verifier, not the listener's
path, no address and nothing about the person; and no task is left.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import re
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import fake_accounts
from fake_accounts import DENIED_DESCRIPTION, FakeAccounts
from walka.walka_account import calls, signin
from walka.walka_account.calls import Signed
from walka.walka_account.https import Client, NetError, Origin, parse_origin
from walka.walka_account.signin import (
    EXCHANGE_WITHIN_S,
    LISTEN_FOR_S,
    PAGE_FAILED,
    PAGE_NOTHING,
    PAGE_SIGNED_IN,
    SignIn,
)

NOW = 1795300000.0  # 21 November 2026, 22:26:40 UTC
ELSEWHERE = "https://accounts.example"  # for the tests in which the accounts are never asked
STARTED = "Walka account: waiting for the web browser to come back from signing in."

MADE: list[FakeAccounts] = []  # every stand-in of the test that runs
PEOPLE: list[str] = []  # e-mail addresses and ids of its accounts
NEEDLES: list[str] = []  # whatever else must be said nowhere: paths, challenges, whole addresses
OUTER: list[Any] = []  # the task that runs the test


def others() -> list[Any]:
    """Every task but the test's own."""
    return [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and task not in OUTER]


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    async def whole() -> None:
        OUTER[:] = [asyncio.current_task()]
        await asyncio.wait_for(scenario(), 30)
        assert others() == []  # nothing is left behind, whatever the test did

    asyncio.run(whole())


async def turns(count: int = 30) -> None:
    """Let everything that can go on without the network go on."""
    for _ in range(count):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    fake_accounts.collect_violations()  # what an earlier test left
    MADE.clear()
    PEOPLE.clear()
    NEEDLES.clear()
    caplog.set_level(logging.DEBUG)
    yield
    assert fake_accounts.collect_violations() == []
    needles = set(PEOPLE) | set(NEEDLES)
    for fake in MADE:
        needles |= set(fake.secrets_issued()) | {fake.origin, fake.origin.split("//")[-1]}
        for seen in fake.requests:
            if isinstance(seen.json, dict) and isinstance(seen.json.get("code_verifier"), str):
                needles.add(seen.json["code_verifier"])
    needles.discard("")
    records = caplog.get_records("setup") + caplog.get_records("call")
    written = "\n".join(logging.Formatter().format(record) for record in records)
    assert [needle for needle in needles if needle in written] == []
    assert DENIED_DESCRIPTION not in written
    ours = [record for record in records if record.name.startswith("walka")]
    # Walka's own lines: no address of any kind, and nothing of a query.
    told = "\n".join(record.getMessage() for record in ours)
    assert "127.0.0.1" not in told and "http" not in told and "/" not in told and "?" not in told
    assert [record.getMessage() for record in ours if record.exc_info or record.stack_info] == []  # never a traceback
    # Nor did anybody else have to say that something was left lying: a task nobody waited for, a line left open.
    elses = [record for record in records if not record.name.startswith("walka")]
    assert [record.getMessage() for record in elses if record.levelno >= logging.WARNING] == []


def said(caplog, level: int = logging.INFO) -> list[str]:
    """What the sign-in wrote to the log, from ``level`` up."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "walka.walka_account.signin" and record.levelno >= level
    ]


class Time:
    """A clock that moves only when a test moves it, and a wait that goes by it."""

    def __init__(self) -> None:
        self.now = 5000.0
        self.waits: list[float | None] = []  # every wait that was asked for
        self._moved: list[asyncio.Future[None]] = []
        self._up = False

    def mono(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
        moved, self._moved = self._moved, []
        for future in moved:
            if not future.done():
                future.set_result(None)

    def say_the_time_is_up(self) -> None:
        """End the wait that waits now, as a wait does whose time is up - without moving the clock."""
        self._up = True
        self.advance(0)

    async def wait(self, event: asyncio.Event, seconds: float | None) -> bool:
        self.waits.append(seconds)
        until = None if seconds is None else self.now + seconds
        while not event.is_set() and (until is None or self.now < until):
            if self._up and seconds is not None:
                self._up = False
                break
            moved: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._moved.append(moved)
            came = asyncio.ensure_future(event.wait())
            try:
                await asyncio.wait({moved, came}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                moved.cancel()
                came.cancel()
                await asyncio.gather(came, return_exceptions=True)
        return event.is_set()


class Try:
    """One sign-in under test: what it opened, and what it handed over to be kept."""

    def __init__(
        self, client: Any, key: str, origin: Any, way: Any = "github", *, keeps: Any = True, **how: Any
    ) -> None:
        self.time = Time()
        self.opened: list[str] = []
        self.handed: list[Signed] = []
        self.keeps = keeps
        how.setdefault("opener", self._open)
        how.setdefault("finish", self._finish)
        how.setdefault("mono", self.time.mono)
        how.setdefault("wait", self.time.wait)
        self.signin = SignIn(client, key, origin, way, **how)

    def _open(self, url: str) -> None:
        self.opened.append(url)
        self.socket = self.signin._server.sockets[0]  # the listener's own, to see later that it is closed
        asked = dict(parse_qsl(urlsplit(url).query))
        back = asked.get("redirect_to", "")
        NEEDLES.extend([url, back, urlsplit(back).path.lstrip("/"), asked.get("code_challenge", "")])

    async def _finish(self, signed: Signed) -> Any:
        self.handed.append(signed)
        return self.keeps

    @property
    def url(self) -> str:
        """The address the browser was opened at - once."""
        assert len(self.opened) == 1
        return self.opened[0]

    @property
    def asked(self) -> dict[str, str]:
        return dict(parse_qsl(urlsplit(self.url).query))

    @property
    def back(self) -> str:
        """Where the browser is sent afterwards: the listener."""
        return self.asked["redirect_to"]

    @property
    def port(self) -> int:
        port = urlsplit(self.back).port
        assert port is not None
        return port

    @property
    def path(self) -> str:
        return urlsplit(self.back).path

    def stopped(self) -> bool:
        """Is the listener's socket closed? (Seen on the socket itself: quicker than asking
        a port nobody listens on, which takes Windows seconds.)"""
        return self.socket.fileno() == -1 and self.signin._server is None

    def forgot(self) -> bool:
        """Is nothing of the keys left in the object? (The path is one of them.)"""
        kept = repr(vars(self.signin))
        return self.path.lstrip("/") not in kept and not self.signin._path and not self.signin._verifier


class Accounts:
    """The stand-in, a client that asks it, and people with accounts."""

    def __init__(self) -> None:
        self.fake = FakeAccounts(wall=lambda: NOW)
        MADE.append(self.fake)

    async def __aenter__(self) -> Accounts:
        await self.fake.start()
        origin = parse_origin(self.fake.origin)
        assert origin is not None
        self.origin = origin
        self.client = Client(origin)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.fake.close()

    def person(self, email: str = "walker@mail.example", **how: Any) -> str:
        user = self.fake.add_user(email, **how)
        PEOPLE.extend([email, user])
        return user

    def a_try(self, way: Any = "github", **how: Any) -> Try:
        return Try(self.client, self.fake.key, self.origin, way, **how)

    def exchanges(self) -> list[Any]:
        """Every exchange of a code that was asked of the accounts."""
        return [seen for seen in self.fake.requests if seen.path == "/auth/v1/token"]


class Hangs:
    """The accounts, never answering."""

    def __init__(self) -> None:
        self.asked = asyncio.Event()
        self.times = 0
        self.left = False

    async def request(self, *_what: Any, **_how: Any) -> Any:
        self.times += 1
        self.asked.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.left = True  # whoever asked went away


class NoCertificate:
    """The accounts, with a certificate that cannot be checked: nothing is asked."""

    async def request(self, *_what: Any, **_how: Any) -> Any:
        raise NetError("certificate")


def alone(client: Any, way: Any = "github", **how: Any) -> Try:
    """A sign-in whose accounts are not the stand-in."""
    return Try(client, "test-key", parse_origin(ELSEWHERE), way, **how)


async def ask(
    port: int, target: str, method: str = "GET", *, first: bytes | None = None
) -> tuple[int, dict[str, str], str]:
    """One request to the listener, written by hand, read to the END of the line.

    (So every answer also shows that the listener closes the line after it.)
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        line = first if first is not None else f"{method} {target} HTTP/1.1".encode("ascii")
        writer.write(line + f"\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode("ascii"))
        await writer.drain()
        raw = await reader.read()
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    top, _, body = raw.partition(b"\r\n\r\n")
    lines = top.decode("latin-1").split("\r\n")
    headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(":") for line in lines[1:])}
    return int(lines[0].split()[1]), headers, body.decode("utf-8")


async def ended(reader: asyncio.StreamReader) -> bool:
    """Was the line closed from the other side? (Some systems say so with an error.)"""
    try:
        return await reader.read() == b""
    except ConnectionError:
        return True


async def gone(port: int) -> bool:
    """Does nobody listen there any more?"""
    try:
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except ConnectionRefusedError:
        return True
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return False


def page(sentence: str) -> str:
    """The whole page, as it must come - written out here once more, not taken from the module."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Walka</title>\n"
        "</head>\n"
        "<body>\n"
        f"<p>{sentence}</p>\n"
        "</body>\n"
        "</html>\n"
    )


def the_three_headers(headers: dict[str, str]) -> bool:
    names = {name.lower(): value for name, value in headers.items()}
    return (
        names.get("cache-control") == "no-store"
        and names.get("referrer-policy") == "no-referrer"
        and names.get("content-security-policy") == "default-src 'none'"
        and names.get("connection") == "close"
        and "set-cookie" not in names
    )


# ---- the words and the socket ---------------------------------------------------------------------


def test_the_three_pages_say_what_the_brief_says():
    assert PAGE_SIGNED_IN == "Walka is signed in. You can close this tab."
    assert PAGE_NOTHING == "Nothing was signed in. You can close this tab."
    assert PAGE_FAILED == "Walka could not finish signing in. Go back to Walka and try again."
    assert signin.WAYS == ("discord", "github", "google")
    assert LISTEN_FOR_S == 300.0 and EXCHANGE_WITHIN_S == 10.0


@pytest.mark.parametrize("sentence", [PAGE_SIGNED_IN, PAGE_NOTHING, PAGE_FAILED])
def test_a_page_is_whole_and_loads_nothing(sentence):
    text = signin._page(sentence)
    assert text == page(sentence)
    assert text.count("<p>") == 1
    low = text.lower()
    for word in ("<script", "<link", "<img", "<a ", "<style", "<form", "<iframe", "href", "src", "http", "url("):
        assert word not in low


class _Socket:
    def __init__(self, *made: Any, fails: bool = False) -> None:
        self.made = made
        self.options: list[tuple[Any, ...]] = []
        self.bound: list[Any] = []
        self.closed = False
        self._fails = fails

    def setsockopt(self, *option: Any) -> None:
        self.options.append(option)

    def bind(self, where: Any) -> None:
        if self._fails:
            raise OSError("no port to give")
        self.bound.append(where)

    def close(self) -> None:
        self.closed = True


def test_on_windows_the_port_is_this_sockets_alone():
    sock: Any = signin._listening_socket("win32", make=_Socket)
    assert sock.made == (socket.AF_INET, socket.SOCK_STREAM)
    # SO_EXCLUSIVEADDRUSE, and nothing else: above all not SO_REUSEADDR.
    assert sock.options == [(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)]
    assert sock.bound == [("127.0.0.1", 0)] and not sock.closed


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_elsewhere_no_option_is_set_at_all(platform):
    sock: Any = signin._listening_socket(platform, make=_Socket)
    assert sock.options == []
    assert sock.bound == [("127.0.0.1", 0)]


def test_a_socket_that_gets_no_port_is_closed():
    made: list[_Socket] = []

    def make(*how: Any) -> Any:
        made.append(_Socket(*how, fails=True))
        return made[-1]

    with pytest.raises(OSError):
        signin._listening_socket("win32", make=make)
    assert made[0].closed


def test_the_real_socket_is_on_this_computer_and_shares_its_port_with_nobody():
    sock = signin._listening_socket()
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1" and port != 0
        assert sock.type == socket.SOCK_STREAM and sock.family == socket.AF_INET
        if sys.platform == "win32":
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) != 0
        else:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        if hasattr(socket, "SO_REUSEPORT"):
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0
    finally:
        sock.close()


def test_the_listener_itself_listens_without_sharing():
    """The socket as it is once aiohttp and asyncio have had it: still no sharing of the port."""

    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        (sock,) = one.signin._server.sockets
        assert sock.getsockname() == ("127.0.0.1", one.port)
        if sys.platform == "win32":
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) != 0
        else:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        await one.signin.close()
        assert one.stopped()

    run(scenario)


def test_the_ordinary_wait_waits_for_the_event_or_the_time():
    async def scenario():
        event = asyncio.Event()
        assert await signin._wait_for(event, 0.01) is False
        event.set()
        assert await signin._wait_for(event, 0.01) is True
        assert await signin._wait_for(event, None) is True

    run(scenario)


# ---- signing in -----------------------------------------------------------------------------------


def test_signing_in(caplog):
    async def scenario():
        async with Accounts() as accounts:
            user = accounts.person("walker@mail.example", way="github")
            one = accounts.a_try("github")
            await one.signin.start()
            assert one.signin.outcome == "" and not one.signin.done.is_set()

            status, headers, text = await accounts.fake.browser(one.url)

            assert (status, text) == (200, page(PAGE_SIGNED_IN))
            assert the_three_headers(headers)
            assert headers["Content-Type"] == "text/html; charset=utf-8"
            assert one.signin.done.is_set() and one.signin.outcome == "signed_in"
            # What was handed over to be kept: the sign-in, checked, and nothing of the other site's.
            (signed,) = one.handed
            assert isinstance(signed, Signed)
            assert (signed.user_id, signed.email, signed.lifetime_s) == (user, "walker@mail.example", 3600.0)
            issued = accounts.fake.secrets_issued()
            assert signed.access_token in issued and signed.refresh_token in issued
            assert not [key for key in issued if key.startswith("other-site-") and key in repr(vars(signed))]
            # One exchange, with the verifier whose challenge the browser carried.
            (exchange,) = accounts.exchanges()
            verifier = exchange.json["code_verifier"]
            assert len(verifier) == 86 and re.fullmatch(r"[A-Za-z0-9_-]+", verifier)
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
            assert one.asked["code_challenge"] == base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
            # Gone, by itself: nobody listens, and nothing of the keys is kept.
            assert await gone(one.port)
            await asyncio.wait({one.signin._life}, timeout=5)
            assert one.signin._life.done() and one.signin._work.done()
            assert one.forgot()
            await one.signin.close()
            assert one.signin.outcome == "signed_in"

    run(scenario)
    assert said(caplog) == [STARTED, "Walka account: the sign-in is over (signed_in)."]


def test_the_address_the_browser_is_opened_at():
    async def scenario():
        async with Accounts() as accounts:
            seen = []
            for way in ("discord", "github", "google"):
                one = accounts.a_try(way)
                await one.signin.start()
                where = urlsplit(one.url)
                assert f"{where.scheme}://{where.netloc}" == accounts.fake.origin
                assert where.path == "/auth/v1/authorize" and where.fragment == ""
                assert sorted(one.asked) == ["code_challenge", "code_challenge_method", "provider", "redirect_to"]
                assert one.asked["provider"] == way and one.asked["code_challenge_method"] == "s256"
                assert re.fullmatch(r"[A-Za-z0-9_-]{43}", one.asked["code_challenge"])
                assert re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/[A-Za-z0-9_-]{43}", one.back)
                assert one.time.waits == [LISTEN_FOR_S]  # one wait, for as long as the browser is given
                seen.append((one.path, one.asked["code_challenge"]))
                await one.signin.close()
                assert one.signin.outcome == "cancelled"
            assert len(set(seen)) == 3  # a path and a verifier of its own for each sign-in

    run(scenario)


def test_the_sign_in_is_kept_before_the_browser_is_answered():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            entered, keep = asyncio.Event(), asyncio.Event()

            async def finish(_signed: Signed) -> bool:
                entered.set()
                await keep.wait()
                return True

            one = accounts.a_try(finish=finish)
            await one.signin.start()
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()
            await turns()
            assert not browser.done() and one.signin.outcome == ""  # the browser waits; nothing is said yet
            keep.set()
            assert (await browser)[2] == page(PAGE_SIGNED_IN)
            assert one.signin.outcome == "signed_in"
            await one.signin.close()

    run(scenario)


def test_whoever_signs_in_in_the_browser_is_who_is_handed_over():
    """The path travels in the same address as the challenge: whoever sees that address can
    sign in as THEMSELVES and bring the code. Nothing here can tell - so the one who keeps
    the sign-in is told whose it is, whole, and shows it."""

    async def scenario():
        async with Accounts() as accounts:
            accounts.person("walker@mail.example")
            other = accounts.person("somebody.else@mail.example")
            accounts.fake.next_browser_user(other)
            one = accounts.a_try()
            await one.signin.start()
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_SIGNED_IN)
            assert [(signed.user_id, signed.email) for signed in one.handed] == [(other, "somebody.else@mail.example")]
            await one.signin.close()

    run(scenario)


def test_the_person_says_no(caplog):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            accounts.fake.next_browser_user(None)
            one = accounts.a_try()
            await one.signin.start()

            status, headers, text = await accounts.fake.browser(one.url)

            assert "error_description" in accounts.fake.sent_to[-1]  # it was there to be shown - and is not
            assert (status, text) == (200, page(PAGE_NOTHING))
            assert the_three_headers(headers)
            assert one.signin.done.is_set() and one.signin.outcome == "refused"
            assert accounts.exchanges() == [] and one.handed == []
            assert one.stopped()
            await one.signin.close()
            assert one.forgot()

    run(scenario)
    assert said(caplog) == [STARTED, "Walka account: the sign-in is over (refused)."]


# ---- only a GET on exactly the path, and only once ------------------------------------------------


def test_any_other_request_gets_404_and_uses_nothing_up():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            port, path = one.port, one.path
            code = "?code=0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21"
            wrong = [
                ("GET", "/"),
                ("GET", "/" + "x" * 43 + code),  # another path, as long
                ("GET", path[:-1] + code),  # all of it but the last letter
                ("GET", path + "x" + code),
                ("GET", path + "/" + code),
                ("GET", "/x" + path + code),
                ("GET", "/" + path + code),
                ("GET", f"/%{ord(path[1]):02X}{path[2:]}{code}"),  # the same letters, written another way
                ("GET", path.swapcase() + code),
                ("POST", path + code),
                ("PUT", path + code),
                ("DELETE", path + code),
                ("OPTIONS", path + code),
                ("PATCH", path + "?error=access_denied"),
            ]
            for method, target in wrong:
                status, headers, text = await ask(port, target, method)
                assert (status, text) == (404, "Not found"), (method, target)
                assert the_three_headers(headers), (method, target)
                assert headers["content-type"] == "text/plain; charset=utf-8"
            status, headers, text = await ask(port, path + code, "HEAD")
            assert (status, text) == (404, "") and the_three_headers(headers)
            await turns()
            assert one.signin.outcome == "" and not one.signin.done.is_set()
            assert accounts.exchanges() == [] and one.handed == []

            # And after all of them the sign-in still works.
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_SIGNED_IN)
            assert one.signin.outcome == "signed_in" and len(one.handed) == 1
            await one.signin.close()

    run(scenario)


def test_a_second_time_gets_404_while_the_first_still_waits():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            gate = accounts.fake.hold("/auth/v1/token")
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await gate.arrived.wait()

            assert one.signin._verifier == ""  # in use, and already nowhere but there
            again = urlsplit(accounts.fake.sent_to[-1])  # the very same address, code and all
            status, headers, text = await ask(one.port, f"{again.path}?{again.query}")
            assert (status, text) == (404, "Not found") and the_three_headers(headers)
            assert one.signin.outcome == "" and not browser.done()

            gate.release()
            assert (await browser)[2] == page(PAGE_SIGNED_IN)
            assert len(accounts.exchanges()) == 1 and len(one.handed) == 1
            await one.signin.close()

    run(scenario)


def test_requests_a_web_server_likes_to_answer_itself_get_the_same_404():
    """aiohttp's application answers these in its own words, without the three headers, and
    leaves the line open (``ask`` reads to the end of the line: it would wait for ever).
    The listener has no application: they come to the one function that answers everything."""

    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            path = one.path
            code = "?code=0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21"
            firsts = [
                "OPTIONS * HTTP/1.1",
                "CONNECT elsewhere.example:443 HTTP/1.1",
                "GET /x HTTP/1.1\r\nExpect: nonsense",
                f"POST {path}{code} HTTP/1.1\r\nExpect: nonsense",
                f"POST {path}{code} HTTP/1.1\r\nExpect: 100-continue\r\nContent-Length: 3",
                "GET /x HTTP/1.0",  # (aiohttp writes "Connection: close" by itself only for HTTP/1.1)
                f"POST {path}{code} HTTP/1.0\r\nConnection: keep-alive",
                f"GET {path}#{code[1:]} HTTP/1.1",  # the path, and then not the end of it
                f"GET {path}#x{code} HTTP/1.1",
                f"GET {path}%3Fcode=a-code HTTP/1.1",
                f"GET {one.back}{code} HTTP/1.1",  # a whole address where the path belongs
                f"TRACE {path}{code} HTTP/1.1\r\nConnection: Upgrade\r\nUpgrade: websocket",
            ]
            for first in firsts:
                status, headers, text = await asyncio.wait_for(ask(one.port, "", first=first.encode("ascii")), 5)
                assert (status, text) == (404, "Not found"), first.split()[0]
                assert the_three_headers(headers), first.split()[0]
            await turns()
            assert one.signin.outcome == "" and accounts.exchanges() == [] and one.handed == []
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_SIGNED_IN)
            await one.signin.close()

    run(scenario)


@pytest.mark.parametrize(
    "more",
    ["Expect: nonsense", "Expect: 100-continue", "Connection: Upgrade\r\nUpgrade: websocket", "HTTP/1.0"],
)
def test_a_get_on_exactly_the_path_counts_whatever_else_it_says(more):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            where = urlsplit(await accounts.fake.authorize(one.url))
            assert where.path == one.path
            first = f"GET {where.path}?{where.query} HTTP/1.1\r\n{more}"
            if more == "HTTP/1.0":
                first = f"GET {where.path}?{where.query} HTTP/1.0"
            status, headers, text = await asyncio.wait_for(ask(one.port, "", first=first.encode("ascii")), 5)
            assert (status, text) == (200, page(PAGE_SIGNED_IN)) and the_three_headers(headers)
            assert one.signin.outcome == "signed_in" and len(one.handed) == 1
            await one.signin.close()

    run(scenario)


def test_what_is_not_a_request_at_all_changes_nothing_and_is_written_nowhere():
    """aiohttp writes a request it cannot read into its own log, first line and all. Not this listener's."""

    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            broken = f"GET {one.path}?code=0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21 NOT-HTTP/9".encode("ascii")
            for first in (b"\x16\x03\x01 not a request", broken):
                status, _headers, _text = await ask(one.port, "", first=first)
                assert status == 400
            await turns()
            assert one.signin.outcome == "" and accounts.exchanges() == []
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_SIGNED_IN)
            await one.signin.close()

    run(scenario)


def test_no_request_counts_before_there_is_a_path_or_after_the_end():
    """Two locks on one door: the path is forgotten at the end, AND the end itself refuses."""

    async def scenario():
        one = alone(Hangs())
        assert one.signin._path == ""
        assert not one.signin._counts(make_mocked_request("GET", "/"))  # "/" plus no path at all is not the path
        await one.signin.start()
        assert one.signin._counts(make_mocked_request("GET", one.path + "?code=a-code"))
        assert one.signin._counts(make_mocked_request("GET", one.path))
        assert one.signin._counts(make_mocked_request("GET", one.path + "?"))
        assert not one.signin._counts(make_mocked_request("GET", one.path + "x"))
        assert not one.signin._counts(make_mocked_request("GET", one.path + "#x"))
        assert not one.signin._counts(make_mocked_request("GET", one.path + "#x?code=a-code"))
        assert not one.signin._counts(make_mocked_request("GET", one.back + "?code=a-code"))
        assert not one.signin._counts(make_mocked_request("GET", "/?" + one.path))
        assert not one.signin._counts(make_mocked_request("POST", one.path))
        path = one.path
        one.signin.cancel()
        one.signin._path = path.lstrip("/")  # even if it were still known
        assert not one.signin._counts(make_mocked_request("GET", path + "?code=a-code"))
        one.signin._path = ""
        await one.signin.close()

    run(scenario)


# ---- a code that is not this sign-in's ------------------------------------------------------------


def test_a_code_from_another_sign_in_is_not_taken(caplog):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            # Somebody who knows where the listener is, but not the verifier: a sign-in of their own.
            theirs = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 43).digest()).decode("ascii").rstrip("=")
            planted = calls.browser_address(accounts.origin, way="github", back=one.back, challenge=theirs)
            NEEDLES.extend([planted, theirs])

            status, headers, text = await accounts.fake.browser(planted)

            assert (status, text) == (200, page(PAGE_FAILED)) and the_three_headers(headers)
            assert one.signin.outcome == "failed" and one.handed == []
            assert len(accounts.exchanges()) == 1  # asked once, and refused
            assert one.stopped()
            await one.signin.close()

    run(scenario)
    assert "Walka account: the sign-in was not finished (bad_code)." in said(caplog)


@pytest.mark.parametrize(
    "query, sentence, outcome, asked",
    [
        ("?code=0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21", PAGE_FAILED, "failed", 1),  # a code nobody gave
        ("?code=not%20a%20code", PAGE_FAILED, "failed", 0),  # not shaped like one: it is not even asked
        ("?code=", PAGE_FAILED, "failed", 0),
        ("", PAGE_FAILED, "failed", 0),
        ("?state=1&error_description=said+nowhere", PAGE_FAILED, "failed", 0),
        ("?error=access_denied", PAGE_NOTHING, "refused", 0),
        ("?error=", PAGE_NOTHING, "refused", 0),
        ("?code=0b6f0a52-6d6f-4a8e-9d1c-3a1f0e5b7c21&error=server_error", PAGE_NOTHING, "refused", 0),
    ],
)
def test_what_the_browser_brings(query, sentence, outcome, asked):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            status, headers, text = await ask(one.port, one.path + query)
            assert (status, text) == (200, page(sentence)) and the_three_headers(headers)
            assert one.signin.done.is_set() and one.signin.outcome == outcome and one.stopped()
            assert len(accounts.exchanges()) == asked and one.handed == []
            await one.signin.close()

    run(scenario)


def test_without_a_code_nothing_is_begun(caplog):
    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        assert (await ask(one.port, one.path + "?code="))[2] == page(PAGE_FAILED)
        assert one.signin.outcome == "failed" and one.signin._work is None and hangs.times == 0
        await one.signin.close()

    run(scenario)
    assert said(caplog) == [STARTED, "Walka account: the sign-in is over (failed)."]


# ---- the exchange fails ---------------------------------------------------------------------------


def test_the_accounts_are_broken():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            accounts.fake.fail_next("/auth/v1/token", 500, b"<html>a gateway's own page</html>")
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_FAILED)
            assert one.signin.outcome == "failed" and one.handed == []
            await one.signin.close()

    run(scenario)


def test_a_certificate_that_cannot_be_checked(caplog):
    async def scenario():
        one = alone(NoCertificate())
        await one.signin.start()
        status, headers, text = await ask(one.port, one.path + "?code=a-code")
        assert (status, text) == (200, page(PAGE_FAILED)) and the_three_headers(headers)
        assert one.signin.outcome == "certificate" and one.handed == []
        await one.signin.close()

    run(scenario)
    assert "Walka account: the sign-in was not finished (certificate)." in said(caplog)


def test_an_exchange_that_gets_no_answer_fails_after_ten_seconds():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            accounts.fake.hold("/auth/v1/token")
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await asyncio.wait_for(_until(lambda: one.signin._work is not None), 20)

            await turns()
            assert one.time.waits == [LISTEN_FOR_S, EXCHANGE_WITHIN_S]  # a new limit, by the same clock

            one.time.advance(EXCHANGE_WITHIN_S - 0.5)
            await turns()
            assert one.signin.outcome == "" and not browser.done()
            one.time.advance(0.5)
            await one.signin.done.wait()

            assert one.signin.outcome == "failed"
            assert (await browser)[2] == page(PAGE_FAILED)
            assert one.handed == []
            await one.signin.close()

    run(scenario)


async def _until(holds: Callable[[], bool]) -> None:
    while not holds():
        await asyncio.sleep(0)


def test_ten_seconds_by_the_ordinary_clock_too(monkeypatch):
    """Without a wait handed in, the limit is waited out with asyncio's own. (The limit is cut short here.)"""
    monkeypatch.setattr(signin, "EXCHANGE_WITHIN_S", 0.05)

    async def scenario():
        hangs = Hangs()
        opened: list[str] = []
        handed: list[Signed] = []

        async def finish(signed: Signed) -> bool:
            handed.append(signed)
            return True

        one = SignIn(
            hangs,
            "test-key",
            parse_origin(ELSEWHERE),
            "google",
            opener=opened.append,
            finish=finish,
            mono=time.monotonic,
        )
        await one.start()
        back = urlsplit(dict(parse_qsl(urlsplit(opened[0]).query))["redirect_to"])
        NEEDLES.extend([opened[0], back.path.lstrip("/")])
        status, _headers, text = await ask(back.port, back.path + "?code=a-code")
        assert (status, text) == (200, page(PAGE_FAILED))
        assert one.outcome == "failed" and hangs.times == 1 and hangs.left and handed == []
        await one.close()

    run(scenario)


def test_the_limit_of_the_exchange_is_not_the_limit_of_the_keeping():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            entered, keep = asyncio.Event(), asyncio.Event()

            async def finish(_signed: Signed) -> bool:
                entered.set()
                await keep.wait()
                return True

            one = accounts.a_try(finish=finish)
            await one.signin.start()
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()
            one.time.advance(EXCHANGE_WITHIN_S + 5)  # the accounts answered in time: this is the store's time
            await turns()
            assert one.signin.outcome == ""
            assert one.time.waits == [LISTEN_FOR_S, EXCHANGE_WITHIN_S, LISTEN_FOR_S - EXCHANGE_WITHIN_S - 5]
            keep.set()
            assert (await browser)[2] == page(PAGE_SIGNED_IN)
            await one.signin.close()

    run(scenario)


# ---- the sign-in is not kept ----------------------------------------------------------------------


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_a_sign_in_that_could_not_be_kept(answer):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try(keeps=answer)  # only True is a yes
            await one.signin.start()
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_FAILED)
            assert one.signin.outcome == "no_store" and len(one.handed) == 1
            await one.signin.close()

    run(scenario)


def test_a_keeper_that_fails_is_named_by_its_kind_only(caplog):
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            NEEDLES.append("what-the-error-said")

            async def finish(signed: Signed) -> bool:
                raise RuntimeError("what-the-error-said " + signed.refresh_token)

            one = accounts.a_try(finish=finish)
            await one.signin.start()
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_FAILED)
            assert one.signin.outcome == "failed"
            await one.signin.close()

    run(scenario)
    assert said(caplog, logging.WARNING) == ["Walka account: the sign-in was not finished (RuntimeError)."]


# ---- cancel and close -----------------------------------------------------------------------------


def test_cancel_while_the_exchange_hangs_answers_at_once_and_close_is_quick():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            gate = accounts.fake.hold("/auth/v1/token")
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await gate.arrived.wait()

            one.signin.cancel()
            # Synchronous: over, forgotten, and nobody else is let in - before anything else runs.
            assert one.signin.done.is_set() and one.signin.outcome == "cancelled"
            assert one.forgot() and one.stopped()
            one.signin.cancel()  # twice is as good as once

            status, headers, text = await asyncio.wait_for(browser, 5)
            assert (status, text) == (200, page(PAGE_FAILED)) and the_three_headers(headers)
            await asyncio.wait_for(one.signin.close(), 1.0)
            assert one.signin._life.done() and one.signin._work.done()
            assert await gone(one.port)
            assert one.handed == [] and one.signin.outcome == "cancelled"
            gate.release()

    run(scenario)


def test_nothing_is_left_the_moment_close_returns():
    """Counted without the stand-in, so that every task there is, is the sign-in's or the test's."""

    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", one.port)  # a line nothing is said on
        browser = asyncio.create_task(ask(one.port, one.path + "?code=a-code"))
        await hangs.asked.wait()

        one.signin.cancel()
        status, _headers, text = await browser
        assert (status, text) == (200, page(PAGE_FAILED))
        await asyncio.wait_for(one.signin.close(), 1.0)

        assert others() == []
        assert hangs.left and hangs.times == 1 and one.handed == []
        assert await ended(idle_reader)  # the idle line was closed with the rest
        idle_writer.close()
        with contextlib.suppress(OSError):
            await idle_writer.wait_closed()

    run(scenario)


def test_closed_straight_after_cancel_the_browser_still_gets_its_page():
    """As when Walka closes, or another sign-in begins: nobody waits for the page - and it leaves all the same."""

    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        browser = asyncio.create_task(ask(one.port, one.path + "?code=a-code"))
        await hangs.asked.wait()
        one.signin.cancel()
        await asyncio.wait_for(one.signin.close(), 1.0)
        status, headers, text = await browser
        assert (status, text) == (200, page(PAGE_FAILED)) and the_three_headers(headers)
        assert others() == []

    run(scenario)


def test_cancel_while_nobody_has_come(caplog):
    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        assert not await gone(one.port)
        one.signin.cancel()
        assert one.signin.done.is_set() and one.signin.outcome == "cancelled" and one.forgot()
        assert await gone(one.port)  # at once: nothing had to be waited for
        await asyncio.wait_for(one.signin.close(), 1.0)
        assert others() == [] and hangs.times == 0
        await one.signin.close()  # twice is as good as once

    run(scenario)
    assert said(caplog) == [STARTED, "Walka account: the sign-in is over (cancelled)."]


def test_close_alone_cancels_and_waits_until_all_is_gone():
    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", one.port)
        assert (await ask(one.port, "/"))[0] == 404  # (so that the idle line has been taken up for sure)
        await asyncio.wait_for(one.signin.close(), 1.0)
        assert one.signin.outcome == "cancelled" and others() == []
        assert one.stopped() and one.signin._life.done() and not one.signin._life.cancelled()
        assert await ended(idle_reader)
        idle_writer.close()
        with contextlib.suppress(OSError):
            await idle_writer.wait_closed()

    run(scenario)


def test_cancelled_while_the_listener_is_put_up_no_browser_opens():
    async def scenario():
        one = alone(Hangs())
        starting = asyncio.create_task(one.signin.start())
        while one.signin._server is None:  # (looked at turn by turn, so that not one turn is missed)
            await asyncio.sleep(0)
        one.signin.cancel()  # the listener is there, and start() has not got to the browser yet
        await starting
        assert one.opened == [] and one.signin.outcome == "cancelled"
        await one.signin.close()
        assert others() == []

    run(scenario)


def test_a_request_whose_body_never_comes_does_not_hold_the_close():
    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", one.port)
        writer.write(
            f"POST {one.path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 100000\r\n\r\nabc".encode("ascii")
        )
        assert await reader.readexactly(12) == b"HTTP/1.1 404"
        with contextlib.suppress(ConnectionError):
            await asyncio.wait_for(reader.read(), 5)  # the line ends with the answer: the body is not waited for
        await asyncio.wait_for(one.signin.close(), 1.0)
        assert others() == []
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    run(scenario)


def test_a_request_that_comes_after_cancel_starts_nothing():
    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", one.port)
        writer.write(f"GET {one.path}?code=a-code HTTP/1.1\r\nHost: 127.0.0.1:{one.port}\r\n\r\n".encode("ascii"))
        one.signin.cancel()  # the request is on its way, and not read yet
        try:
            answer = await reader.read()
        except ConnectionError:
            answer = b""
        assert answer == b"" or answer.startswith(b"HTTP/1.1 404 ")  # cut, or refused: never taken
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        await one.signin.close()
        assert hangs.times == 0 and one.handed == [] and one.signin.outcome == "cancelled"

    run(scenario)


def test_cancel_while_the_sign_in_is_being_kept():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            entered = asyncio.Event()
            ended: list[str] = []

            async def finish(_signed: Signed) -> bool:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    ended.append("cancelled")
                    raise
                return True

            one = accounts.a_try(finish=finish)
            await one.signin.start()
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()
            one.signin.cancel()
            assert (await browser)[2] == page(PAGE_FAILED)
            await asyncio.wait_for(one.signin.close(), 1.0)
            assert ended == ["cancelled"] and one.signin.outcome == "cancelled"

    run(scenario)


def _stuck_keeper() -> tuple[Callable[[Signed], Awaitable[bool]], asyncio.Event, asyncio.Event]:
    """A keeper that does not stop when it is told to - only when it is let go."""
    entered, let_go = asyncio.Event(), asyncio.Event()

    async def finish(_signed: Signed) -> bool:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await let_go.wait()
            raise
        return True

    return finish, entered, let_go


def test_a_keeper_that_is_slow_to_stop_holds_neither_the_listener_nor_the_close(monkeypatch):
    monkeypatch.setattr(signin, "SHUTDOWN_S", 0.05)  # (how long such a keeper is waited for)

    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            finish, entered, let_go = _stuck_keeper()
            one = accounts.a_try(finish=finish)
            await one.signin.start()
            idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", one.port)
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()

            one.signin.cancel()
            assert (await browser)[2] == page(PAGE_FAILED)
            await asyncio.wait_for(one.signin.close(), 1.0)

            # Everything of the listener is gone, and the task that kept it ended by itself.
            assert one.signin._life.done() and not one.signin._life.cancelled()
            assert one.stopped() and one.signin._runner is None
            assert await asyncio.wait_for(ended(idle_reader), 5)
            assert not one.signin._work.done()  # the keeper alone goes on: that is its own business
            let_go.set()
            await asyncio.wait({one.signin._work}, timeout=5)
            assert one.signin.outcome == "cancelled"
            idle_writer.close()
            with contextlib.suppress(OSError):
                await idle_writer.wait_closed()

    run(scenario)


def test_close_gives_up_waiting_and_the_listener_is_gone_all_the_same(monkeypatch):
    """The last resort of close(): what does not end in time is cut. By then no line is open any more."""
    monkeypatch.setattr(signin, "CLOSE_WITHIN_S", 0.05)
    monkeypatch.setattr(signin, "SHUTDOWN_S", 30.0)  # (so long would the keeper be waited for)

    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            finish, entered, let_go = _stuck_keeper()
            one = accounts.a_try(finish=finish)
            await one.signin.start()
            idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", one.port)
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()
            one.signin.cancel()
            assert (await browser)[2] == page(PAGE_FAILED)

            await asyncio.wait_for(one.signin.close(), 5)

            assert one.signin._life.cancelled()  # cut: it did not end by itself
            assert one.stopped() and one.signin._runner is None
            assert await asyncio.wait_for(ended(idle_reader), 5)
            let_go.set()
            await asyncio.wait({one.signin._work}, timeout=5)
            idle_writer.close()
            with contextlib.suppress(OSError):
                await idle_writer.wait_closed()

    run(scenario)


def test_a_wait_that_breaks_ends_the_sign_in_and_the_listener_still_goes(caplog):
    async def scenario():
        hangs = Hangs()
        breaks = asyncio.Event()
        NEEDLES.append("what-the-wait-said")

        async def wait(_event: asyncio.Event, _seconds: float | None) -> bool:
            await breaks.wait()
            raise RuntimeError("what-the-wait-said")

        one = alone(hangs, wait=wait)
        await one.signin.start()
        idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", one.port)
        browser = asyncio.create_task(ask(one.port, one.path + "?code=a-code"))
        await hangs.asked.wait()

        breaks.set()
        await asyncio.wait_for(one.signin.done.wait(), 5)

        assert one.signin.outcome == "failed" and one.forgot()
        assert (await asyncio.wait_for(browser, 5))[2] == page(PAGE_FAILED)
        await asyncio.wait({one.signin._life}, timeout=5)  # by itself, without close()
        assert one.signin._life.done() and one.stopped() and one.signin._runner is None
        assert await asyncio.wait_for(ended(idle_reader), 5)  # taken down, not just left
        assert others() == [] and hangs.left and one.handed == []
        await one.signin.close()
        idle_writer.close()
        with contextlib.suppress(OSError):
            await idle_writer.wait_closed()

    run(scenario)
    assert said(caplog, logging.WARNING) == ["Walka account: the sign-in's listener ended on an error (RuntimeError)."]


def test_a_listener_that_does_not_close_cleanly_is_named_by_its_kind_only(caplog):
    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        runner = one.signin._runner
        NEEDLES.append("what-the-listener-said")

        class Fails:
            async def cleanup(self) -> None:
                await runner.cleanup()
                raise OSError("what-the-listener-said")

        one.signin._runner = Fails()
        await asyncio.wait_for(one.signin.close(), 1.0)  # and nothing is raised
        assert one.signin.outcome == "cancelled" and one.signin._life.done() and others() == []

    run(scenario)
    assert said(caplog, logging.WARNING) == [
        "Walka account: the sign-in's listener did not close cleanly (OSError)."
    ]


def _the_page_leaves_when(monkeypatch, leaving: Callable[[], Awaitable[Any]]) -> None:
    """Hold the first answer back on its way out until ``leaving()`` is through."""
    really = web.Response.write_eof
    held: list[bool] = []

    async def write_eof(self: web.Response, data: bytes = b"") -> None:
        if not held:
            held.append(True)
            await leaving()
        await really(self, data)

    monkeypatch.setattr(web.Response, "write_eof", write_eof)


def test_a_page_that_cannot_leave_does_not_hold_the_close(monkeypatch):
    monkeypatch.setattr(signin, "ANSWER_WITHIN_S", 0.05)
    monkeypatch.setattr(signin, "SHUTDOWN_S", 0.05)

    async def scenario():
        _the_page_leaves_when(monkeypatch, asyncio.Event().wait)  # never
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", one.port)
        writer.write(f"GET {one.path}?code=a-code HTTP/1.1\r\nHost: 127.0.0.1:{one.port}\r\n\r\n".encode("ascii"))
        await hangs.asked.wait()
        one.signin.cancel()
        await asyncio.wait_for(one.signin.close(), 5)  # the request is given a moment, and then cut
        assert one.signin._life.done() and not one.signin._life.cancelled()  # (well within what close() waits)
        assert others() == []
        try:
            answer = await asyncio.wait_for(reader.read(), 5)
        except ConnectionError:
            answer = b""
        assert b"</html>" not in answer  # (the page never left whole: the line was cut)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    run(scenario)


def test_the_page_on_its_way_out_leaves_before_the_listener_goes(monkeypatch):
    monkeypatch.setattr(signin, "ANSWER_WITHIN_S", 30.0)
    monkeypatch.setattr(signin, "SHUTDOWN_S", 0.01)  # what aiohttp itself would give the request: next to nothing

    async def scenario():
        leave = asyncio.Event()
        _the_page_leaves_when(monkeypatch, leave.wait)
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        browser = asyncio.create_task(ask(one.port, one.path + "?code=a-code"))
        await hangs.asked.wait()
        one.signin.cancel()
        closing = asyncio.create_task(one.signin.close())
        await asyncio.wait({closing}, timeout=0.1)
        assert not closing.done()  # it waits for the page
        leave.set()
        await asyncio.wait_for(closing, 5)
        status, headers, text = await asyncio.wait_for(browser, 5)
        assert (status, text) == (200, page(PAGE_FAILED)) and the_three_headers(headers)
        assert others() == []

    run(scenario)


def test_cancelled_before_the_listener_is_up_it_never_listens(monkeypatch):
    """Whatever aiohttp may one day wait for while the listener is put up: a cancel in that
    time is not lost, and the socket that was bound is closed, not left listening."""
    sockets: list[socket.socket] = []
    really = signin._listening_socket

    def listening_socket() -> socket.socket:
        sockets.append(really())
        return sockets[-1]

    monkeypatch.setattr(signin, "_listening_socket", listening_socket)

    async def scenario():
        reached, go_on = asyncio.Event(), asyncio.Event()

        class Slow(web.ServerRunner):
            async def setup(self) -> None:
                await super().setup()
                reached.set()
                await go_on.wait()

        monkeypatch.setattr(web, "ServerRunner", Slow)
        one = alone(Hangs())
        starting = asyncio.create_task(one.signin.start())
        await reached.wait()
        port = sockets[0].getsockname()[1]
        one.signin.cancel()
        go_on.set()
        await starting
        assert one.opened == [] and one.signin.outcome == "cancelled"
        assert sockets[0].fileno() == -1 and one.signin._server is None
        assert not one.signin._path and not one.signin._verifier
        assert await gone(port)
        await asyncio.wait_for(one.signin.close(), 1.0)
        assert others() == [] and one.signin._runner is None

    run(scenario)


@pytest.mark.parametrize("way", ["email", "github"])
def test_a_socket_is_never_left_open_when_the_listener_cannot_be_put_up(monkeypatch, way):
    sockets: list[socket.socket] = []
    really = signin._listening_socket

    def listening_socket() -> socket.socket:
        sockets.append(really())
        return sockets[-1]

    monkeypatch.setattr(signin, "_listening_socket", listening_socket)

    async def scenario():
        class Broken(web.ServerRunner):
            async def setup(self) -> None:
                raise OSError("no listener today")

        if way == "github":  # (a way that is none fails before: at the address)
            monkeypatch.setattr(web, "ServerRunner", Broken)
        one = alone(Hangs(), way)
        await one.signin.start()
        assert one.signin.outcome == "failed" and one.opened == []
        assert len(sockets) == 1 and sockets[0].fileno() == -1
        await one.signin.close()
        assert others() == []

    run(scenario)


def test_ended_from_outside_the_tasks_say_so_and_leave_nothing_listening():
    """As when the loop itself ends its tasks: no cancel() came first, and still nothing is left to be found."""

    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        one.signin._life.cancel()
        await asyncio.wait({one.signin._life}, timeout=5)
        assert one.signin.done.is_set() and one.signin.outcome == "cancelled"
        assert one.stopped() and one.forgot()
        await one.signin.close()

        two = alone(hangs)
        starting = asyncio.create_task(two.signin.start())
        await asyncio.sleep(0)  # the listener is being put up
        two.signin._life.cancel()
        await asyncio.wait_for(starting, 5)  # start() is told, and does not wait for ever
        assert two.opened == [] and two.signin.outcome == "cancelled" and two.signin._server is None
        await two.signin.close()

        three = alone(hangs)
        await three.signin.start()
        browser = asyncio.create_task(ask(three.port, three.path + "?code=a-code"))
        await hangs.asked.wait()
        three.signin._work.cancel()
        status, _headers, text = await asyncio.wait_for(browser, 5)
        assert (status, text) == (200, page(PAGE_FAILED))
        assert three.signin.outcome == "cancelled" and three.stopped() and three.handed == []
        await three.signin.close()
        assert others() == []

    run(scenario)


def test_a_keeper_that_cancels_the_sign_in_is_not_cut_by_its_own_word():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            through: list[str] = []
            box: list[Try] = []

            async def finish(_signed: Signed) -> bool:
                box[0].signin.cancel()
                await asyncio.sleep(0)  # (were it cancelled, it would be here)
                through.append("to the end")
                return True

            one = accounts.a_try(finish=finish)
            box.append(one)
            await one.signin.start()
            assert (await accounts.fake.browser(one.url))[2] == page(PAGE_FAILED)
            assert through == ["to the end"] and one.signin.outcome == "cancelled"  # the first word stands
            await one.signin.close()

    run(scenario)


def test_cancel_after_the_end_changes_nothing():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            await accounts.fake.browser(one.url)
            one.signin.cancel()
            await one.signin.close()
            assert one.signin.outcome == "signed_in"

    run(scenario)


def test_cancelled_before_it_begins_nothing_listens_and_no_browser_opens():
    async def scenario():
        one = alone(Hangs())
        await one.signin.close()  # before the start: nothing to close
        assert one.signin.outcome == "cancelled"
        await one.signin.start()
        assert one.opened == [] and one.signin._life is None and others() == []
        await one.signin.close()

    run(scenario)


def test_a_start_that_is_cancelled_leaves_nothing():
    async def scenario():
        one = alone(Hangs())
        starting = asyncio.create_task(one.signin.start())
        await asyncio.sleep(0)  # it has begun, and waits for its listener
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert one.signin.outcome == "cancelled" and one.opened == []
        await asyncio.wait_for(one.signin.close(), 1.0)
        assert others() == [] and one.signin._server is None
        assert not one.signin._path and not one.signin._verifier

    run(scenario)


def test_started_twice_it_opens_one_browser():
    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        await one.signin.start()
        assert len(one.opened) == 1
        await one.signin.close()

    run(scenario)


def test_the_tab_is_closed_while_the_code_is_exchanged():
    """The browser's request only waits: the sign-in is what it is, whether anybody reads the page."""

    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            one = accounts.a_try()
            await one.signin.start()
            gate = accounts.fake.hold("/auth/v1/token")
            where = urlsplit(await accounts.fake.authorize(one.url))
            _reader, writer = await asyncio.open_connection("127.0.0.1", one.port)
            writer.write(
                f"GET {where.path}?{where.query} HTTP/1.1\r\nHost: 127.0.0.1:{one.port}\r\n\r\n".encode("ascii")
            )
            await gate.arrived.wait()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            await turns()
            gate.release()
            await one.signin.done.wait()
            assert one.signin.outcome == "signed_in" and len(one.handed) == 1
            await one.signin.close()

    run(scenario)


# ---- the clock ------------------------------------------------------------------------------------


def test_nobody_comes_back(caplog):
    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        one.time.advance(LISTEN_FOR_S - 1)
        await turns()
        assert one.signin.outcome == ""
        assert (await ask(one.port, "/"))[0] == 404  # still there
        assert one.time.waits == [LISTEN_FOR_S]  # one wait, to the end: no timer ticks meanwhile

        one.time.advance(1)
        await one.signin.done.wait()

        assert one.signin.outcome == "timed_out" and one.forgot()
        assert await gone(one.port)
        await asyncio.wait({one.signin._life}, timeout=5)  # by itself, without close()
        assert others() == [] and hangs.times == 0
        await one.signin.close()

    run(scenario)
    assert said(caplog) == [STARTED, "Walka account: the sign-in is over (timed_out)."]


def test_the_time_is_up_while_the_sign_in_is_being_kept():
    async def scenario():
        async with Accounts() as accounts:
            accounts.person()
            entered = asyncio.Event()

            async def finish(_signed: Signed) -> bool:
                entered.set()
                await asyncio.Event().wait()
                return True

            one = accounts.a_try(finish=finish)
            await one.signin.start()
            browser = asyncio.create_task(accounts.fake.browser(one.url))
            await entered.wait()
            one.time.advance(LISTEN_FOR_S)
            await one.signin.done.wait()
            # Somebody did come back: so not "nobody came", but "could not finish".
            assert one.signin.outcome == "failed"
            assert (await browser)[2] == page(PAGE_FAILED)
            await one.signin.close()

    run(scenario)


def test_the_wait_that_was_handed_in_is_believed_when_it_says_the_time_is_up():
    """The caller's wait is the caller's clock: its tests end a wait without moving any other."""

    async def scenario():
        hangs = Hangs()
        one = alone(hangs)
        await one.signin.start()
        one.time.say_the_time_is_up()
        await one.signin.done.wait()
        assert one.signin.outcome == "timed_out" and one.time.waits == [LISTEN_FOR_S]
        await one.signin.close()

        two = alone(hangs)
        await two.signin.start()
        browser = asyncio.create_task(ask(two.port, two.path + "?code=a-code"))
        await hangs.asked.wait()
        await turns()
        assert two.time.waits == [LISTEN_FOR_S, EXCHANGE_WITHIN_S]
        two.time.say_the_time_is_up()
        await two.signin.done.wait()
        assert two.signin.outcome == "failed" and (await browser)[2] == page(PAGE_FAILED)
        await two.signin.close()

    run(scenario)


# ---- a sign-in that cannot begin ------------------------------------------------------------------


@pytest.mark.parametrize("way", ["email", "", "GitHub", "github ", None, 7, ("github",)])
def test_a_way_that_is_none_opens_nothing(way):
    async def scenario():
        one = alone(Hangs(), way)
        await one.signin.start()
        assert one.signin.done.is_set() and one.signin.outcome == "failed"
        assert one.opened == [] and one.signin._server is None
        await one.signin.close()
        assert others() == []

    run(scenario)


@pytest.mark.parametrize(
    "origin", [Origin("http", "elsewhere.example", 80), Origin("https", "a@b", None), None, ELSEWHERE]
)
def test_an_address_that_is_not_the_accounts_opens_nothing(origin):
    async def scenario():
        one = Try(Hangs(), "test-key", origin)
        await one.signin.start()
        assert one.signin.outcome == "failed" and one.opened == []
        await one.signin.close()
        assert others() == []

    run(scenario)


def test_no_port_to_listen_on(monkeypatch, caplog):
    def no_socket() -> Any:
        raise OSError("what-the-system-said")

    NEEDLES.append("what-the-system-said")
    monkeypatch.setattr(signin, "_listening_socket", no_socket)

    async def scenario():
        one = alone(Hangs())
        await one.signin.start()
        assert one.signin.done.is_set() and one.signin.outcome == "failed" and one.opened == []
        await one.signin.close()
        assert others() == []

    run(scenario)
    assert said(caplog, logging.WARNING) == ["Walka account: could not listen for the web browser (OSError)."]


def test_a_browser_that_cannot_be_opened(caplog):
    seen: list[str] = []

    def opener(url: str) -> None:
        seen.append(url)
        NEEDLES.append(url)
        raise RuntimeError("could not open " + url)

    async def scenario():
        one = alone(Hangs(), opener=opener)
        await one.signin.start()
        assert one.signin.done.is_set() and one.signin.outcome == "failed"
        back = urlsplit(dict(parse_qsl(urlsplit(seen[0]).query))["redirect_to"])
        NEEDLES.append(back.path.lstrip("/"))
        assert one.signin._server is None  # nothing listens for a browser that is not there
        await asyncio.wait({one.signin._life}, timeout=5)  # ... and the rest goes by itself
        assert others() == []
        await one.signin.close()
        assert others() == []

    run(scenario)
    assert said(caplog, logging.WARNING) == ["Walka account: the web browser could not be opened (RuntimeError)."]
