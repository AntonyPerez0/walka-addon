"""The one maker of requests (walka_account.https): which address it takes,
what it puts on the wire, what it keeps of an answer - and that nothing of
a request, a key least of all, is in what it says when something goes wrong."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import socket
import ssl
import subprocess
import sys
import threading
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from walka import __version__
from walka.walka_account import https
from walka.walka_account.https import Answer, Client, DaemonResolver, NetError, Origin, key_is_usable, parse_origin

SECRET = "v4xk2j3h5l6m-the-bearer"


def run(scenario, **how):
    return asyncio.run(asyncio.wait_for(scenario(), 30), **how)


def tasks() -> set:
    """Every task there is now. (Before Python 3.11 the 30 seconds above are a task too: so, compared with before.)"""
    return set(asyncio.all_tasks())


class Accounts:
    """A server on 127.0.0.1 that writes down what it is asked, and answers what it is told to."""

    def __init__(self, answer=None) -> None:
        self.seen: list[dict] = []
        self.answer = answer or (lambda request: web.json_response({"ok": True}))
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self.server = TestServer(app, host="127.0.0.1")

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.seen.append(
            {
                "method": request.method,
                "target": request.path_qs,
                "raw": request.raw_path,
                "query": list(request.query.items()),
                "headers": list(request.headers.items()),
                "body": await request.read(),
            }
        )
        answer = self.answer(request)
        return await answer if asyncio.iscoroutine(answer) else answer

    async def __aenter__(self) -> Accounts:
        await self.server.start_server()
        self.origin = Origin("http", "127.0.0.1", self.server.port)
        self.client = Client(self.origin)
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.server.close()


class Bare:
    """A bare socket on 127.0.0.1: it reads what it is sent and then does as it was told."""

    def __init__(self, behave: str) -> None:
        self.behave = behave
        self.connections = 0
        self.heard = b""
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self._writers: list[asyncio.StreamWriter] = []

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            if self.behave != "hangs up":
                self.heard += await reader.readuntil(b"\r\n\r\n")
                self.arrived.set()
            if self.behave == "silent":
                await self.release.wait()
            elif self.behave == "not HTTP":
                writer.write(b"this is no answer at all\r\n\r\n")
            elif self.behave in ("half", "stalls"):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nonly this")
            if self.behave != "silent":
                await writer.drain()
            if self.behave == "stalls":
                await self.release.wait()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> Bare:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.origin = Origin("http", "127.0.0.1", self.port)
        return self

    async def __aexit__(self, *_exc) -> None:
        self.release.set()
        for writer in self._writers:
            writer.close()
        self.server.close()
        await self.server.wait_closed()


def closed_port() -> int:
    """A port of this computer where nothing listens."""
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        return taken.getsockname()[1]


async def failure(coroutine) -> NetError:
    with pytest.raises(NetError) as caught:
        await coroutine
    return caught.value


# ---- the address ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, origin, spelled",
    [
        ("https://accounts.example", Origin("https", "accounts.example", None), "https://accounts.example"),
        ("https://accounts.example/", Origin("https", "accounts.example", None), "https://accounts.example"),
        ("https://accounts.example:8443", Origin("https", "accounts.example", 8443), "https://accounts.example:8443"),
        ("https://accounts.example:8443/", Origin("https", "accounts.example", 8443), "https://accounts.example:8443"),
        ("https://accounts.example:443", Origin("https", "accounts.example", None), "https://accounts.example"),
        ("HTTPS://Accounts.Example", Origin("https", "accounts.example", None), "https://accounts.example"),
        ("https://a-b.c1.example", Origin("https", "a-b.c1.example", None), "https://a-b.c1.example"),
        ("https://xn--bcher-kva.example", Origin("https", "xn--bcher-kva.example", None), "https://xn--bcher-kva.example"),
        ("https://1a.example.x1", Origin("https", "1a.example.x1", None), "https://1a.example.x1"),
        ("https://192.0.2.7", Origin("https", "192.0.2.7", None), "https://192.0.2.7"),
        ("https://192.0.2.7:8443/", Origin("https", "192.0.2.7", 8443), "https://192.0.2.7:8443"),
        ("https://127.0.0.1:8443", Origin("https", "127.0.0.1", 8443), "https://127.0.0.1:8443"),
        ("http://127.0.0.1:54321", Origin("http", "127.0.0.1", 54321), "http://127.0.0.1:54321"),
        ("http://127.0.0.1:54321/", Origin("http", "127.0.0.1", 54321), "http://127.0.0.1:54321"),
        ("http://127.0.0.1:80", Origin("http", "127.0.0.1", 80), "http://127.0.0.1:80"),
    ],
)
def test_an_address_that_is_taken(text: str, origin: Origin, spelled: str):
    assert parse_origin(text) == origin
    assert origin.text == spelled
    # One address, one spelling: read again, it is itself.
    assert parse_origin(origin.text) == origin


@pytest.mark.parametrize(
    "text",
    [
        None,
        b"https://accounts.example",
        ["https://accounts.example"],
        "",
        "accounts.example",
        "https://",
        "https:///",
        "https://:8443",
        # somebody before the host
        "https://user@accounts.example",
        "https://user:word@accounts.example",
        "https://accounts.example@elsewhere.example",
        "http://127.0.0.1:1@evil.example",
        "http://127.0.0.1:1@evil.example/",
        # more than an address
        "https://accounts.example/rest",
        "https://accounts.example//",
        "https://accounts.example/?a=b",
        "https://accounts.example?a=b",
        "https://accounts.example/#top",
        "https://accounts.example#top",
        "https://accounts.example\\@elsewhere.example",
        # not plain ASCII, or not a name
        "https://b\u00fccher.example",
        "https://accounts.ex\u0430mple",  # a Cyrillic letter that looks like a Latin one
        "https://account\u017f.example",  # the long s: "s" to whoever ignores case
        "https://\u212aaccounts.example",  # the sign for kelvin: "k" likewise
        "http\u017f://accounts.example",
        "https://accounts_example.test",
        "https://-accounts.example",
        "https://accounts..example",
        "https://accounts.example.",
        "https://[::1]",
        "https://[::1]:8443",
        "https://%61ccounts.example",
        "https://" + "a" * 64 + ".example",
        "https://" + ".".join(["a" * 60] * 5),
        # numbers that are not a whole address
        "https://127.1",
        "https://2130706433",
        "https://1.2.3.4.5",
        "https://256.1.1.1",
        "https://1.2.3.04",
        "https://0177.0.0.1",
        "https://0x7f.0.0.1",
        "https://0x7f000001",
        "https://0X7F000001",
        "https://accounts.example.1",
        "https://accounts.example.0x1f",
        # spaces and ends of lines
        " https://accounts.example",
        "https://accounts.example ",
        "https://accounts.example\n",
        "https://accounts .example",
        "https://accounts.example\t",
        # ports that are none
        "https://accounts.example:",
        "https://accounts.example:0",
        "https://accounts.example:65536",
        "https://accounts.example:123456",
        "https://accounts.example:+80",
        "https://accounts.example:8443x",
        "http://127.0.0.1:0",
        "http://127.0.0.1:99999",
        # other schemes, and plain http anywhere but on this computer by number
        "http://accounts.example",
        "http://accounts.example:8080",
        "http://localhost:1",
        "http://localhost",
        "http://127.0.0.1",
        "http://127.0.0.1/",
        "http://127.0.0.2:8000",
        "http://127.0.0.1.example:8000",
        "http://0.0.0.0:8000",
        "http://[::1]:8000",
        "ftp://accounts.example",
        "file:///etc/passwd",
        "//accounts.example",
        "https:accounts.example",
        "https:/accounts.example",
        "javascript:alert(1)",
    ],
)
def test_an_address_that_is_refused(text: object):
    assert parse_origin(text) is None


def made_up_jwt(payload: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(payload)}.c2lnbmVkLWJ5LW5vYm9keQ"


@pytest.mark.parametrize(
    "key, usable",
    [
        ("test-key", True),
        ("sb_publishable_made_up", True),
        (made_up_jwt({"role": "anon", "iss": "made-up"}), True),
        ("a.b.c", True),  # three parts, but nothing a key of the older kind says
        ("x" * 512, True),
        # a secret key, of either kind
        ("sb_secret_made_up", False),
        ("SB_SECRET_made_up", False),
        (made_up_jwt({"role": "service_role", "iss": "made-up"}), False),
        (made_up_jwt({"iss": "made-up", "ref": "abc", "role": "service_role", "exp": 2000000000}), False),
        # not a key at all
        ("", False),
        (None, False),
        (b"test-key", False),
        (12345, False),
        ("x" * 513, False),
        ("test key", False),
        ("test-key\n", False),
        ("test-key\r\nX-Other: 1", False),
        ("test\tkey", False),
        ("t\u00e9st-key", False),
        ("test-key\x00", False),
    ],
)
def test_which_key_the_program_may_carry(key: object, usable: bool):
    assert key_is_usable(key) is usable


# ---- what goes on the wire ---------------------------------------------------------------------------


def test_what_a_request_says_and_nothing_else():
    async def scenario():
        async with Accounts() as accounts:
            await accounts.client.request("GET", "/rest/v1/walks", headers={"apikey": "test-key"})
            await accounts.client.request(
                "POST",
                "/rest/v1/walks",
                headers={"apikey": "test-key", "Authorization": "Bearer " + SECRET, "Prefer": "return=representation"},
                json_body=[{"steps": 66, "kcal_extra": None}],
            )
            await accounts.client.request("POST", "/auth/v1/logout", query=[("scope", "local")])
            return accounts.seen, accounts.server.port

    (get, post, bare_post), port = run(scenario)
    assert dict(get["headers"]) == {
        "Host": f"127.0.0.1:{port}",
        "User-Agent": f"Walka/{__version__}",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "apikey": "test-key",
    }
    assert len(get["headers"]) == 6 and get["body"] == b""
    assert dict(post["headers"]) == {
        "Host": f"127.0.0.1:{port}",
        "User-Agent": f"Walka/{__version__}",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Type": "application/json",
        "Content-Length": str(len(post["body"])),
        "apikey": "test-key",
        "Authorization": "Bearer " + SECRET,
        "Prefer": "return=representation",
    }
    assert len(post["headers"]) == 10
    assert post["body"] == b'[{"steps":66,"kcal_extra":null}]'
    # A request with no body names no kind of body.
    assert dict(bare_post["headers"]) == {
        "Host": f"127.0.0.1:{port}",
        "User-Agent": f"Walka/{__version__}",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Length": "0",
    }
    assert https.USER_AGENT == f"Walka/{__version__}"


def test_the_address_asked_is_the_one_address_the_path_and_the_query():
    pairs = [("start_key", "in.(2026-11-19T20:30:00,2026-11-14T22:59:40)"), ("select", "id,start_key,deleted_at")]

    async def scenario():
        async with Accounts() as accounts:
            await accounts.client.request("GET", "/rest/v1/walks", query=pairs)
            await accounts.client.request("GET", "/rest/v1/walks", query=[("a", "b c&d=e#f?g/h%i+j"), ("a", "\u00e9")])
            await accounts.client.request("GET", "/auth/v1/user")
            return accounts.seen

    first, awkward, plain = run(scenario)
    assert first["query"] == pairs
    # ... written so that no sign in a value can end the value, or the query.
    assert first["raw"] == (
        "/rest/v1/walks?start_key=in.%282026-11-19T20%3A30%3A00%2C2026-11-14T22%3A59%3A40%29"
        "&select=id%2Cstart_key%2Cdeleted_at"
    )
    assert awkward["query"] == [("a", "b c&d=e#f?g/h%i+j"), ("a", "\u00e9")]
    assert plain["raw"] == "/auth/v1/user" and plain["query"] == []


def test_no_cookie_is_kept():
    # (Seen from outside only. Each request has a memory of its own, and
    # nobody keeps a cookie for an address by number: this alone would not
    # notice a client that kept them. test_the_settings_of_every_connection
    # pins that it keeps none on purpose.)
    def answer(_request):
        return web.json_response({}, headers={"Set-Cookie": "tracked=yes; Path=/"})

    async def scenario():
        async with Accounts(answer) as accounts:
            await accounts.client.request("GET", "/rest/v1/walks")
            await accounts.client.request("GET", "/rest/v1/walks")
            return accounts.seen

    for seen in run(scenario):
        assert "cookie" not in {name.lower() for name, _value in seen["headers"]}


def test_a_redirect_comes_back_as_the_answer_it_is_and_nobody_else_is_asked():
    async def scenario():
        async with Accounts() as elsewhere:
            there = f"http://127.0.0.1:{elsewhere.server.port}/landed"

            def answer(request):
                return web.Response(status=int(request.query["status"]), headers={"Location": there})

            async with Accounts(answer) as accounts:
                answers = [
                    await accounts.client.request(
                        method, "/auth/v1/token", query=[("status", str(status))], headers={"Authorization": SECRET}
                    )
                    for status in (301, 302, 303, 307, 308)
                    for method in ("GET", "POST")
                ]
                return answers, len(accounts.seen), elsewhere.seen

    answers, asked, elsewhere = run(scenario)
    assert [answer.status for answer in answers] == [301, 301, 302, 302, 303, 303, 307, 307, 308, 308]
    assert asked == 10 and elsewhere == []


def test_the_settings_of_every_connection(monkeypatch):
    """Said once more as settings, beside what the other tests see happen."""
    sessions: list[dict] = []
    connectors: list[dict] = []
    real_session, real_connector = aiohttp.ClientSession, aiohttp.TCPConnector

    def session(**settings):
        sessions.append(settings)
        return real_session(**settings)

    def connector(**settings):
        connectors.append(settings)
        return real_connector(**settings)

    monkeypatch.setattr(aiohttp, "ClientSession", session)
    monkeypatch.setattr(aiohttp, "TCPConnector", connector)

    async def scenario():
        async with Accounts() as accounts:
            await accounts.client.request("GET", "/a", timeout_s=3.0)
            await accounts.client.request("GET", "/a")
            return [made.closed for made in (settings["connector"] for settings in sessions)]

    closed = run(scenario)
    assert len(sessions) == 2 and len(connectors) == 2 and closed == [True, True]  # one for each request, and gone
    first, second = sessions
    assert first["trust_env"] is False and first["auto_decompress"] is False
    assert isinstance(first["cookie_jar"], aiohttp.DummyCookieJar)
    assert (first["timeout"].total, first["timeout"].sock_connect) == (3.0, 3.0)
    assert (second["timeout"].total, second["timeout"].sock_connect) == (20.0, 10.0)
    assert connectors[0]["force_close"] is True and isinstance(connectors[0]["resolver"], DaemonResolver)
    # (Nothing to remember between two requests - and no task of aiohttp's own for a look-up.)
    assert connectors[0]["use_dns_cache"] is False
    assert "ssl" not in connectors[0]  # plain http, to this computer only
    assert set(connectors[0]) == {"force_close", "use_dns_cache", "resolver"}


def test_a_proxy_named_in_the_environment_is_not_asked(monkeypatch):
    async def scenario():
        async with Bare("hangs up") as proxy, Accounts() as accounts:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.port}")
            for name in ("NO_PROXY", "no_proxy"):
                monkeypatch.delenv(name, raising=False)
            answer = await accounts.client.request("GET", "/rest/v1/walks")
            return answer.status, len(accounts.seen), proxy.connections

    assert run(scenario) == (200, 1, 0)


def test_a_request_that_is_answered_leaves_nothing_behind():
    async def scenario():
        before = tasks()
        async with Accounts() as accounts:
            await accounts.client.request("POST", "/rest/v1/walks", json_body=[{}])
            await failure(accounts.client.request("GET", "/../x"))
        for _ in range(200):
            left = tasks() - before
            if not left:
                break
            await asyncio.sleep(0.01)
        return left

    assert run(scenario) == set()


# ---- what is kept of an answer -----------------------------------------------------------------------


def test_an_answer_is_its_status_its_body_and_how_long_to_wait():
    def answer(request):
        return web.Response(
            status=int(request.query["status"]), body=b'{"code":"PGRST303"}', headers={"Retry-After": "120"}
        )

    async def scenario():
        async with Accounts(answer) as accounts:
            return [
                await accounts.client.request("GET", "/x", query=[("status", str(status))])
                for status in (200, 201, 204, 400, 401, 404, 429, 500, 503)
            ]

    answers = run(scenario)
    assert [answer.status for answer in answers] == [200, 201, 204, 400, 401, 404, 429, 500, 503]
    assert answers[3].body == b'{"code":"PGRST303"}' and answers[3].json() == {"code": "PGRST303"}
    assert answers[6].retry_after == 120.0 and isinstance(answers[6].retry_after, float)
    assert answers[2].body == b""


@pytest.mark.parametrize(
    "said, taken",
    [
        ("0", 0.0),
        ("7", 7.0),
        (" 30 ", 30.0),
        ("3600", 3600.0),
        ("3601", None),
        ("99999", None),
        ("-1", None),
        ("1.5", None),
        ("1e3", None),
        ("", None),
        ("soon", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("\u0663", None),  # a digit, but not one of these ten
    ],
)
def test_how_long_to_wait_is_taken_only_as_whole_seconds_up_to_an_hour(said: str, taken: float | None):
    assert https._retry_after(said) == taken
    assert https._retry_after(None) is None


def test_an_answer_is_read_up_to_a_limit_and_no_further():
    def answer(request):
        size = https.MOST_BYTES + int(request.query["over"])
        if request.query.get("how") == "piece by piece":

            async def stream():
                response = web.StreamResponse()
                await response.prepare(request)  # no length said beforehand
                for _ in range(size // 1024):
                    await response.write(b"x" * 1024)
                await response.write(b"x" * (size % 1024))
                return response

            return stream()
        return web.Response(body=b"x" * size)

    async def scenario():
        async with Accounts(answer) as accounts:
            fits = await accounts.client.request("GET", "/x", query=[("over", "0")])
            fits_in_pieces = await accounts.client.request("GET", "/x", query=[("over", "0"), ("how", "piece by piece")])
            too_long = await failure(accounts.client.request("GET", "/x", query=[("over", "1")]))
            too_long_in_pieces = await failure(
                accounts.client.request("GET", "/x", query=[("over", "1"), ("how", "piece by piece")])
            )
            return len(fits.body), len(fits_in_pieces.body), too_long.kind, too_long_in_pieces.kind

    assert https.MOST_BYTES == 262_144
    assert run(scenario) == (https.MOST_BYTES, https.MOST_BYTES, "too_big", "too_big")


def test_an_answer_that_comes_packed_is_not_unpacked():
    """Walka asks for answers as they are. One that comes packed all the
    same - a few thousand bytes that would unpack into millions - is kept
    as it came, and is then simply not JSON."""
    packed = gzip.compress(b'{"a":"' + b"x" * 20_000_000 + b'"}')
    assert len(packed) < https.MOST_BYTES

    def answer(_request):
        return web.Response(body=packed, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})

    async def scenario():
        async with Accounts(answer) as accounts:
            return await accounts.client.request("GET", "/x")

    got = run(scenario)
    assert got.status == 200 and got.body == packed
    with pytest.raises(ValueError):
        got.json()


def test_the_body_as_json():
    assert Answer(200, None, b'[{"id":"a"}]').json() == [{"id": "a"}]
    assert Answer(200, None, b"1").json() == 1
    assert Answer(200, None, b"[1.5, 2e3, -0.25, 1e308]").json() == [1.5, 2000.0, -0.25, 1e308]
    assert Answer(200, None, '{"a":"\u00e9"}'.encode()).json() == {"a": "\u00e9"}
    for body in (
        b"",
        b"<html>502 Bad Gateway</html>",
        b'{"a":',
        b"\xff\xfe{}",
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"a":1e999}',  # JSON by the letter, and no number: Python would make infinity of it
        b"[-1E400]",
        b"[" * 100_000 + b"]" * 100_000,
        b'{"a":' + b"9" * 5000 + b"}",
    ):
        with pytest.raises(ValueError) as caught:
            Answer(200, None, body).json()
        # The body may hold keys: the error says only that it is not JSON.
        assert type(caught.value) is ValueError and caught.value.args == ("the answer is not JSON",)
        assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_an_answer_shown_by_accident_does_not_show_its_body():
    answer = Answer(200, None, json.dumps({"refresh_token": SECRET}).encode())
    assert SECRET not in repr(answer) and SECRET not in str(answer) and SECRET not in f"{answer}"
    assert "200" in repr(answer)


# ---- when there is no answer -------------------------------------------------------------------------


def test_nobody_there_or_no_answer_in_time_or_no_http_is_no_answer():
    async def scenario():
        kinds = {}
        kinds["nobody listens"] = (await failure(Client(Origin("http", "127.0.0.1", closed_port())).request("GET", "/x"))).kind
        for behave in ("silent", "hangs up", "not HTTP", "half", "stalls"):
            async with Bare(behave) as bare:
                kinds[behave] = (await failure(Client(bare.origin).request("GET", "/x", timeout_s=0.3))).kind
        # ... and one that takes the connection and then never says its part of a checked one.
        async with Bare("silent") as bare:
            checked = Client(Origin("https", "127.0.0.1", bare.port))
            kinds["silent, checked"] = (await failure(checked.request("GET", "/x", timeout_s=0.3))).kind
        return kinds

    assert run(scenario) == {
        "nobody listens": "no_answer",
        "silent": "no_answer",
        "hangs up": "no_answer",
        "not HTTP": "no_answer",
        "half": "no_answer",
        "stalls": "no_answer",  # the beginning of an answer, and then nothing: the time is for all of it
        "silent, checked": "no_answer",
    }


def test_waiting_for_an_answer_ends_at_once_when_cancelled_and_leaves_nothing_behind():
    async def scenario():
        before = tasks()
        async with Bare("silent") as bare:
            task = asyncio.ensure_future(
                Client(bare.origin).request("POST", "/x", headers={"Authorization": "Bearer " + SECRET}, json_body={})
            )
            await asyncio.wait_for(bare.arrived.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)  # at once: not when the 20 seconds are up (room for a slow machine)
        # The same while the two sides are still agreeing on a checked connection.
        async with Bare("silent") as bare:
            task = asyncio.ensure_future(Client(Origin("https", "127.0.0.1", bare.port)).request("GET", "/x"))
            while not bare.connections:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        for _ in range(200):
            left = tasks() - before
            if not left:
                break
            await asyncio.sleep(0.01)
        return left

    assert run(scenario) == set()


def test_a_request_this_client_does_not_make_is_not_made():
    async def scenario():
        async with Accounts() as accounts:
            client = accounts.client
            refused = []
            for path in (
                "",
                "rest/v1/walks",
                "//elsewhere.example/x",
                "/rest//v1",
                "https://elsewhere.example/x",
                "/x/https://elsewhere.example",
                "/rest/v1/walks?select=*",
                "/rest/v1/walks#top",
                "/rest\\v1",
                "/rest/../auth/v1/user",
                "/..",
                "/rest/v1/%2e%2e/x",
                "/rest/v1/walks ",
                "/rest/v1/wa lks",
                "/rest/v1/walks\r\nX-Other: 1",
                "/rest/v1/w\u00e4lks",
                "/@elsewhere.example",
                None,
                b"/rest/v1/walks",
            ):
                refused.append(await failure(client.request("GET", path)))
            for method in ("PATCH", "DELETE", "PUT", "HEAD", "OPTIONS", "get", "GET /x HTTP/1.1\r\nX-Other: 1", ""):
                refused.append(await failure(client.request(method, "/rest/v1/walks")))
            for headers in (
                {"Authorization": "Bearer x\r\nX-Other: 1"},
                {"Authorization": "Bearer x\ny"},
                {"Authorization": "Bearer \u00e9"},
                {"Authorization": 7},
                {"X-Other\r\nY": "1"},
                {"": "1"},
                {"User-Agent": "Mozilla/5.0"},
                {"user-agent": "Mozilla/5.0"},
                {"Host": "elsewhere.example"},
                {"Cookie": "a=b"},
                {"Accept-Encoding": "gzip"},
                {"Connection": "keep-alive"},
                {"Content-Length": "5"},
                {"Transfer-Encoding": "chunked"},
            ):
                refused.append(await failure(client.request("GET", "/rest/v1/walks", headers=headers)))
            for query in ([("a", 1)], [("a", None)], [(1, "a")], [("a",)], ["ab", "c"], 5):
                refused.append(await failure(client.request("GET", "/rest/v1/walks", query=query)))
            for body in (float("nan"), {"a": float("inf")}, {"a": object()}, {"a": {1, 2}}):
                refused.append(await failure(client.request("POST", "/rest/v1/walks", json_body=body)))
            # (To aiohttp a limit of 0 is no limit at all.)
            for limit in (0, 0.0, -1, None, float("nan"), float("inf"), True, "20"):
                refused.append(await failure(client.request("GET", "/rest/v1/walks", timeout_s=limit)))
            # ... and an address put together by hand, of a kind parse_origin would not give.
            port = accounts.server.port
            for origin in (
                Origin("http", "localhost", port),
                Origin("http", "127.0.0.1", None),
                Origin("http", f"127.0.0.1:{port}@elsewhere.example", port),
                Origin("https", f"elsewhere.example/@127.0.0.1:{port}", None),
                Origin("https", "accounts.example", 443),
                Origin("ftp", "127.0.0.1", port),
                Origin("HTTP", "127.0.0.1", port),
                f"http://127.0.0.1:{port}",
            ):
                refused.append(await failure(Client(origin).request("GET", "/rest/v1/walks")))
            return [error.kind for error in refused], accounts.seen

    kinds, seen = run(scenario)
    assert set(kinds) == {"address"} and len(kinds) == 67
    assert seen == []


def test_nothing_of_a_request_is_in_what_is_said_when_it_fails(caplog, capfd):
    """An error of aiohttp's carries the request's headers, the bearer
    among them. None of them gets out - not as the error, not as its
    cause, not in a line of the log."""

    def raises_with_the_secret() -> ssl.SSLContext:
        raise RuntimeError(SECRET)

    async def scenario():
        mine = {"apikey": "test-key", "Authorization": "Bearer " + SECRET}
        errors: dict[str, NetError] = {}
        heard = b""

        def too_long(_request):
            return web.Response(body=b"x" * (https.MOST_BYTES + 1))

        errors["nobody listens"] = await failure(
            Client(Origin("http", "127.0.0.1", closed_port())).request("GET", "/x", headers=mine)
        )
        for behave in ("silent", "hangs up", "not HTTP", "half", "stalls"):
            async with Bare(behave) as bare:
                errors[behave] = await failure(
                    Client(bare.origin).request("POST", "/x", headers=mine, json_body={"k": SECRET}, timeout_s=0.3)
                )
                heard += bare.heard
        async with Accounts(too_long) as accounts:
            client = accounts.client
            errors["too long"] = await failure(client.request("GET", "/x", headers=mine))
            errors["a path"] = await failure(client.request("GET", "/x?token=" + SECRET, headers=mine))
            errors["a header"] = await failure(client.request("GET", "/x", headers={"Authorization": SECRET + "\r\n"}))
            errors["a body"] = await failure(client.request("POST", "/x", headers=mine, json_body={SECRET: float("nan")}))
        errors["no way to check"] = await failure(
            Client(Origin("https", "127.0.0.1", closed_port()), context_factory=raises_with_the_secret).request(
                "GET", "/x", headers=mine
            )
        )
        await asyncio.sleep(0.05)
        return errors, heard

    with caplog.at_level(logging.DEBUG):
        errors, heard = run(scenario, debug=True)
    # (The key was really sent, where anything was sent: this test can fail.)
    assert SECRET.encode() in heard
    assert {name: error.kind for name, error in errors.items()} == {
        "nobody listens": "no_answer",
        "silent": "no_answer",
        "hangs up": "no_answer",
        "not HTTP": "no_answer",
        "half": "no_answer",
        "stalls": "no_answer",
        "too long": "too_big",
        "a path": "address",
        "a header": "address",
        "a body": "address",
        "no way to check": "certificate",
    }
    for error in errors.values():
        assert type(error) is NetError and error.args == (error.kind,) and vars(error) == {"kind": error.kind}
        assert error.__cause__ is None and error.__context__ is None
        assert SECRET not in str(error) and SECRET not in repr(error)
    out, err = capfd.readouterr()
    logged = caplog.text + "".join(str(record.__dict__) for record in caplog.records)
    assert SECRET not in logged and SECRET not in out and SECRET not in err
    assert "test-key" not in logged and "test-key" not in err


# ---- the certificates --------------------------------------------------------------------------------


def test_the_context_checks_the_certificate_and_the_name():
    context = https.make_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    report = https.roots_report(context)
    assert set(report) == {"source", "count", "named"}
    assert isinstance(report["count"], int) and set(report["named"]) <= set(https.WELL_KNOWN_ROOTS)


def test_where_the_roots_come_from_is_said_by_the_system():
    empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert https.roots_report(empty, "darwin") == {"source": "macOS's own list", "count": 0, "named": []}
    assert https.roots_report(empty, "win32") == {"source": "the system's store", "count": 0, "named": []}
    assert https.roots_report(empty, "linux") == {"source": "OpenSSL's default paths", "count": 0, "named": []}


def test_on_a_mac_the_roots_are_macos_own_list_and_nothing_else(monkeypatch, tmp_path: Path):
    """A packed Mac program would look where the build machine's Python
    kept its roots. Walka names macOS's own file - and without that file
    there is no context at all, not one that checks against nothing."""
    assert https.MAC_ROOTS == "/etc/ssl/cert.pem"
    monkeypatch.setattr(https, "MAC_ROOTS", str(tmp_path / "not-there.pem"))
    with pytest.raises(OSError):
        https.make_ssl_context("darwin")
    bundle = ssl.get_default_verify_paths().cafile
    if bundle is None or not Path(bundle).is_file():
        pytest.skip("this system keeps no file of roots to stand in for macOS's")
    monkeypatch.setattr(https, "MAC_ROOTS", bundle)
    # (With a file named, nothing the environment says is looked at.)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "not-there.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    context = https.make_ssl_context("darwin")
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert https.roots_report(context, "darwin")["count"] > 0


def test_a_folder_of_roots_is_counted_too(monkeypatch, tmp_path: Path):
    """Some systems keep one root to a file. OpenSSL reads such a folder
    only when it needs to, so a count would say none."""
    bundle = ssl.get_default_verify_paths().cafile
    if bundle is None or not Path(bundle).is_file():
        pytest.skip("this system keeps no file of roots to make a folder from")
    text = Path(bundle).read_text("utf-8", "replace")
    end = "-----END CERTIFICATE-----"
    certificates = [part + end for part in text.split(end) if "-----BEGIN CERTIFICATE-----" in part][:3]
    folder = tmp_path / "certs"
    folder.mkdir()
    for number, certificate in enumerate(certificates):
        (folder / f"root{number}.pem").write_text(certificate[certificate.index("-----BEGIN") :] + "\n", "ascii")
    (folder / "README").write_text("not a certificate", "ascii")
    (folder / "again.0").write_bytes((folder / "root0.pem").read_bytes())
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "not-there.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(folder))
    context = https.make_ssl_context("linux")
    assert https.roots_report(context, "linux")["count"] == len(certificates) == 3
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True


def test_a_client_takes_no_context_that_does_not_check():
    """Whoever makes the context: one that would take any certificate is
    not used, and nobody is asked."""

    def takes_anything() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def checks_no_name() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        return context

    def breaks() -> ssl.SSLContext:
        raise FileNotFoundError("/etc/ssl/cert.pem")

    async def scenario():
        kinds = []
        async with Bare("hangs up") as bare:
            origin = Origin("https", "127.0.0.1", bare.port)
            for factory in (takes_anything, checks_no_name, breaks, lambda: None, lambda: "a context"):
                kinds.append((await failure(Client(origin, context_factory=factory).request("GET", "/x"))).kind)
            return kinds, bare.connections

    assert run(scenario) == (["certificate"] * 5, 0)


def test_the_context_is_made_when_first_needed_off_the_loop_and_once():
    made: list[threading.Thread] = []

    def factory() -> ssl.SSLContext:
        made.append(threading.current_thread())
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    async def scenario():
        loop_thread = threading.current_thread()
        async with Bare("hangs up") as bare:
            client = Client(Origin("https", "127.0.0.1", bare.port), context_factory=factory)
            assert made == []  # not when the client is made: that may be on the loop
            first = await failure(client.request("GET", "/x"))
            second = await failure(client.request("GET", "/x"))
            # (This server speaks no TLS at all: no answer, which is not a certificate refused.)
            return first.kind, second.kind, loop_thread, bare.connections

    first, second, loop_thread, connections = run(scenario)
    assert (first, second, connections) == ("no_answer", "no_answer", 2)
    assert len(made) == 1 and made[0] is not loop_thread and made[0].daemon

    async def plain():
        async with Accounts() as accounts:
            client = Client(accounts.origin, context_factory=factory)
            return (await client.request("GET", "/x")).status

    # ... and never for an address on this computer that has no certificate to check.
    assert run(plain) == 200 and len(made) == 1


# ---- looking a name up -------------------------------------------------------------------------------


def test_a_name_is_looked_up_on_a_thread_of_its_own(monkeypatch):
    threads: list[threading.Thread] = []
    real = socket.getaddrinfo

    def watched(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", watched)

    async def scenario():
        resolver = DaemonResolver()
        found = await resolver.resolve("localhost", 8443, socket.AF_INET)
        await resolver.close()
        return found, threading.current_thread()

    found, loop_thread = run(scenario)
    assert found and found[0] == {
        "hostname": "localhost",
        "host": "127.0.0.1",
        "port": 8443,
        "family": socket.AF_INET,
        "proto": found[0]["proto"],
        "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
    }
    assert threads and all(thread.daemon and thread is not loop_thread for thread in threads)
    assert threads[0].name == "walka-account-names"


def test_a_name_that_cannot_be_looked_up_is_an_error_of_the_system_and_no_answer_for_a_client(monkeypatch):
    asked: list[str] = []

    def nobody(host, *_args, **_kwargs):
        asked.append(host)
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", nobody)

    async def scenario():
        with pytest.raises(OSError):
            await DaemonResolver().resolve("accounts.example", 443)
        return await failure(Client(Origin("https", "accounts.example", None)).request("GET", "/x", timeout_s=5))

    assert run(scenario).kind == "no_answer"
    assert set(asked) == {"accounts.example"}


def test_addresses_that_cannot_be_used_are_left_out(monkeypatch):
    def found(host, port, **_kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", port, 0, 3)),  # of one cable only
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::7", port)),  # a Python without IPv6
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::7", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", port)),
        ]

    async def scenario():
        monkeypatch.setattr(socket, "getaddrinfo", found)
        hosts = await DaemonResolver().resolve("accounts.example", 443, socket.AF_UNSPEC)
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **_kwargs: found(host, port)[:1])
        with pytest.raises(OSError):
            await DaemonResolver().resolve("accounts.example", 443, socket.AF_UNSPEC)
        return hosts

    assert [(host["host"], host["port"], host["family"]) for host in run(scenario)] == [
        ("2001:db8::7", 443, socket.AF_INET6),
        ("192.0.2.7", 443, socket.AF_INET),
    ]


def test_a_look_up_that_never_answers_holds_nobody(monkeypatch):
    """Not the one who waits (cancelled: at once), not a request (it ends
    when its time is up), and not the program when it closes: the thread
    is a daemon."""
    release = threading.Event()
    threads: list[threading.Thread] = []

    def hangs(*_args, **_kwargs):
        threads.append(threading.current_thread())
        release.wait(20)
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", hangs)

    async def scenario():
        before = tasks()
        task = asyncio.ensure_future(DaemonResolver().resolve("accounts.example", 443))
        while not threads:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        in_time = await failure(Client(Origin("https", "accounts.example", None)).request("GET", "/x", timeout_s=0.2))
        monkeypatch.setattr(https, "LOOK_UP_S", 0.05)
        with pytest.raises(OSError):
            await DaemonResolver().resolve("accounts.example", 443)
        return in_time.kind, tasks() - before

    try:
        assert run(scenario) == ("no_answer", set())
        assert len(threads) == 3 and all(thread.daemon for thread in threads)
    finally:
        release.set()


ASKS_WHILE_NO_NAME_IS_EVER_LOOKED_UP = """\
import asyncio
import socket
import sys
import threading

sys.path.insert(0, {src!r})
from walka.walka_account.https import Client, Origin

looking = threading.Event()


def hangs(*_args, **_kwargs):
    looking.set()
    threading.Event().wait(40)
    raise socket.gaierror(socket.EAI_AGAIN, "never")


socket.getaddrinfo = hangs


async def main():
    asking = asyncio.ensure_future(Client(Origin("https", "accounts.example", None)).request("GET", "/x"))
    while not looking.is_set():
        await asyncio.sleep(0.01)
    asking.cancel()
    try:
        await asking
    except asyncio.CancelledError:
        print("cancelled")


asyncio.run(main())
print("closed")
"""


def test_the_program_closes_while_a_look_up_still_hangs(tmp_path: Path):
    """The whole of it, in a process of its own: a request is cancelled
    while the system still looks its name up - and the program is gone
    long before the look-up would have ended. (With aiohttp's own way of
    looking up, Python waits for it at the end of ``asyncio.run``.)"""
    script = tmp_path / "program.py"
    src = Path(https.__file__).resolve().parents[2]
    script.write_text(ASKS_WHILE_NO_NAME_IS_EVER_LOOKED_UP.format(src=str(src)), "utf-8")
    # (The look-up hangs for 40 seconds; whoever waits for it runs into this limit.)
    ran = subprocess.run([sys.executable, str(script)], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")
    assert ran.stdout.decode("ascii").split() == ["cancelled", "closed"]
    assert b"Traceback" not in ran.stderr and b"Exception" not in ran.stderr
