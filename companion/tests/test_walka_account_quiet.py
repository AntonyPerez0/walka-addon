"""When Walka asks nobody: the three quiet cases, each over many hours of a whole Walka.

Walka may open a connection to another machine only when somebody is
signed in, sending is switched on for that Walka account, and there is a
walk to send - or the person has just pressed a button that says it asks.
So here is Walka as it runs - a real ``App``, started, on a treadmill that
is not the simulated one, with its dashboard served and the Walka
account's part handed in - in the three cases where none of that holds:

- nobody has ever signed in (which is how Walka comes);
- somebody is signed in and sending is off;
- sending is on and there is nothing to send.

Hours pass by the made-up clock. Walks are walked, recorded and saved,
every page of the dashboard is read, and every button that does not say
it asks the Walka account is pressed. And all the while there are
tripwires on every way out of the program, set on the very loop Walka
runs on: opening a connection, looking up a name, opening the web
browser, looking into the store for secrets. The accounts' address is a
port where nobody listens. Nothing may trip - and the one wait the part
is given must never have been asked for with a time limit: that is the
proof that there is no timer.

At the end of the third case a walk IS there to send, and the wires trip:
they were live all along.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient

import walka_account_rig
from test_walka_account_nokeys import THE_BRIEFS_LIST, asked_of_the_accounts
from test_walka_account_wiring import (
    JSON,
    JUNK,
    ROUTES,
    dashboard,
    every_route_that_changes_something,
    hand_in,
    left_running,
    run,
    said,
    shown,
    walk_on,
)
from walka.app import App
from walka.session import SessionStore
from walka.walka_account import ledger, service
from walka_account_rig import PAD, Rig


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)
    # (What was asked before the quiet began - a sign-in, a send - is on the brief's list, like everything.)
    for rig in walka_account_rig.MADE:
        assert asked_of_the_accounts(rig.fake) <= THE_BRIEFS_LIST


# Where the part's own code lies: a timer set from there would be one of the part's.
THE_PART = str(Path(service.__file__).parent)

OK = (200, {"ok": True})
NOT_SIGNED_IN = (409, {"ok": False, "error": "not signed in"})
SIGNED_IN_ALREADY = (409, {"ok": False, "error": "signed in already"})


class Wires:
    """Tripwires on every way out of the program, on the loop that is running.

    ``tripped`` holds what was reached for. Each wire also refuses what it
    caught, so that nothing leaves this computer even then. The one
    connection that is let through is the test's own, to Walka's dashboard
    (``dashboard_is_at``: exactly that port, and no other).
    ``timers`` holds every timer that code of the part's set on the loop.

    Set them once in a test: a second set would stand behind the first.
    """

    def __init__(self, monkeypatch, own_port: int) -> None:
        self.tripped: list[tuple] = []
        self.timers: list[str] = []
        self._own = ("127.0.0.1", own_port)
        self.let_through = 0  # how often the test itself came through: the wires see every connection
        self._within_call_later = False
        loop = asyncio.get_running_loop()
        sock_connect, create_connection = loop.sock_connect, loop.create_connection
        call_later, call_at = loop.call_later, loop.call_at
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

        async def tripping_sock_connect(sock, address):
            self._check("loop.sock_connect", address)
            return await sock_connect(sock, address)

        async def tripping_create_connection(protocol_factory, host=None, port=None, **how):
            sock = how.get("sock")
            self._check("loop.create_connection", sock.getpeername() if sock is not None else (host, port))
            return await create_connection(protocol_factory, host, port, **how)

        def tripping_connect(sock, address):
            self._check("socket.connect", address)
            return connect(sock, address)

        def tripping_connect_ex(sock, address):
            self._check("socket.connect_ex", address)
            return connect_ex(sock, address)

        def tripping_getaddrinfo(host, *_more, **_how):
            self.tripped.append(("socket.getaddrinfo", host))
            raise socket.gaierror("a tripwire: Walka looked up a name")

        def tripping_browser(*_told, **_how):
            self.tripped.append(("webbrowser.open",))
            raise OSError("a tripwire: Walka opened the web browser")

        def noting_call_later(delay, callback, *args, **how):
            self._note_timer(f"call_later({delay})")
            # (The loop sets it by way of its own call_at: one timer, written down once.)
            self._within_call_later = True
            try:
                return call_later(delay, callback, *args, **how)
            finally:
                self._within_call_later = False

        def noting_call_at(when, callback, *args, **how):
            if not self._within_call_later:
                self._note_timer("call_at")
            return call_at(when, callback, *args, **how)

        monkeypatch.setattr(loop, "sock_connect", tripping_sock_connect)
        monkeypatch.setattr(loop, "create_connection", tripping_create_connection)
        monkeypatch.setattr(loop, "call_later", noting_call_later)
        monkeypatch.setattr(loop, "call_at", noting_call_at)
        # (Under the loop, too: whatever connects a socket of Python's on any thread comes through here.)
        monkeypatch.setattr(socket.socket, "connect", tripping_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", tripping_connect_ex)
        monkeypatch.setattr(socket, "getaddrinfo", tripping_getaddrinfo)
        monkeypatch.setattr(webbrowser, "open", tripping_browser)

    def dashboard_is_at(self, port: int) -> None:
        """Walka was started anew, and its dashboard is served at another port."""
        self._own = ("127.0.0.1", port)

    def _check(self, how: str, address: Any) -> None:
        if isinstance(address, tuple) and tuple(address[:2]) == self._own:
            self.let_through += 1
            return
        self.tripped.append((how, address))
        raise OSError("a tripwire: Walka opened a connection")

    def _note_timer(self, what: str) -> None:
        frame = sys._getframe(2)
        while frame is not None:
            if frame.f_code.co_filename.startswith(THE_PART):
                self.timers.append(f"{what} from {Path(frame.f_code.co_filename).name}:{frame.f_lineno}")
                return
            frame = frame.f_back


async def nobody_listens_at(rig: Rig) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(rig.origin.host, rig.origin.port), 10)
    except OSError:
        return True
    writer.close()
    return False


async def the_accounts_are_gone(rig: Rig) -> None:
    """From here on the accounts' address is a port where nobody listens."""
    await rig.fake.close()
    assert await nobody_listens_at(rig)


async def read_every_page(client: TestClient, app: App) -> int:
    """Every GET route of the dashboard, and a piece of the event stream. Gives how many were read."""
    walk = app.store.records[-1].id if app.store.records else "nothing"
    read = 0
    for route in client.app.router.routes():
        if route.method != "GET" or route.resource is None:
            continue
        path = route.resource.canonical.replace("{session_id}", walk)
        if path == "/api/events":
            async with client.get(path) as stream:
                line = await asyncio.wait_for(stream.content.readline(), 10)
                assert line.startswith(b"data: ") and "walka_account" in json.loads(line[len(b"data: ") :])
        else:
            if path == "/api/road":
                path += "?character=0123abcd"
            elif path == "/static":
                path += "/index.html"
            response = await client.get(path)
            await response.read()
            assert response.status < 500 and (response.status == 200 or walk == "nothing"), (path, response.status)
        read += 1
    return read


# The buttons of the Walka-account card that ask nobody, by where the card stands: what is sent, and what
# is answered. (Not among them, ever: a sign-in with one of the three ways while nobody is signed in,
# "Look again" and the question while somebody is - those say that they ask.)
QUIET_BUTTONS: dict[str, tuple[tuple[str, Any, tuple[int, dict]], ...]] = {
    "never signed in": (
        ("/api/walka-account/sign-out", {}, OK),
        ("/api/walka-account/look-again", {}, NOT_SIGNED_IN),
        ("/api/walka-account/question", {}, NOT_SIGNED_IN),
        ("/api/walka-account/sending/off", {}, NOT_SIGNED_IN),
        ("/api/walka-account/notice/ok", {}, OK),
    ),
    "signed in, sending off": (
        ("/api/walka-account/sign-in", {"way": "github"}, SIGNED_IN_ALREADY),
        ("/api/walka-account/sign-in", {"way": "google"}, SIGNED_IN_ALREADY),
        ("/api/walka-account/sending/off", {}, OK),
        ("/api/walka-account/notice/ok", {}, OK),
    ),
    "sending on, nothing to send": (
        ("/api/walka-account/sign-in", {"way": "discord"}, SIGNED_IN_ALREADY),
        ("/api/walka-account/notice/ok", {}, OK),
    ),
}


async def press_what_asks_nobody(client: TestClient, rig: Rig, app: App, state: str) -> int:
    """Every POST and DELETE of the dashboard that does not say it asks the Walka account. Gives how many."""
    pressed = 0
    for method, path in every_route_that_changes_something(client):
        if path in ROUTES:
            continue
        for body in ({}, {"button": "start"}, {"action": "start", "speed_kmh": 3.0}):
            there = path.replace("{session_id}", "nothing")
            response = await client.request(method, there, data=json.dumps(body), headers=JSON)
            assert response.status < 500, (method, path, body)
            pressed += 1
    # Junk never presses "Sign in" and never answers the question, wherever the card stands.
    for body in JUNK:
        assert (await said(client, "/api/walka-account/sending", body))[0] == 400, body
        assert (await said(client, "/api/walka-account/sign-in", body))[0] in (400, 409), body
        pressed += 2
    for path, body, answer in QUIET_BUTTONS[state]:
        assert await said(client, path, body) == answer, (state, path, body)
        pressed += 1
    await rig.idle()
    await app.tick()
    return pressed


async def time_passes(rig: Rig, app: App, seconds: float) -> None:
    """Walka just runs, with nobody on the pad: a look at the game and the belt every few seconds."""
    pad = app.treadmill
    for _ in range(int(seconds // 5)):
        rig.clock.advance(5.0)
        pad.status.speed_kmh, pad.status.updated_at = 0.0, rig.clock.mono()
        await app.tick()
    await rig.idle()


async def an_hour(rig: Rig, app: App, client: TestClient, state: str, *, somebody_walks: bool = True) -> None:
    """One hour by the made-up clock: a walk of four minutes is walked and saved, the whole dashboard is
    read and every quiet button pressed, and the rest of the hour Walka runs on."""
    began = rig.clock.mono()
    if somebody_walks:
        walks = len(app.store.records)
        await walk_on(rig, app, 240, then_stand=True)
        assert len(app.store.records) == walks + 1 and app.store.records[-1].treadmill == PAD
        await rig.idle()  # (the sender was woken by the saved walk: it has had its turn)
    assert await read_every_page(client, app) >= 9
    assert await press_what_asks_nobody(client, rig, app, state) >= 30
    await time_passes(rig, app, 3600 - (rig.clock.mono() - began))


class Marks:
    """How much had been asked of anybody when the quiet began."""

    def __init__(self, rig: Rig) -> None:
        self.requests = len(rig.fake.requests)
        self.store_calls = len(rig.keystore.calls)
        self.opened = len(rig.opened)
        self.waits = rig.wait.mark()


def asked_nobody(rig: Rig, wires: Wires, since: Marks) -> bool:
    """Since the marks were taken: no wire tripped, no request reached the accounts, the store for
    secrets was not looked into, no browser was opened - and nothing waited on a clock."""
    assert wires.tripped == []
    assert len(rig.fake.requests) == since.requests
    assert rig.keystore.calls[since.store_calls :] == []
    assert rig.opened[since.opened :] == []
    assert rig.wait.limits(since.waits) == [], "the part waited with a time limit: that is a timer"
    assert wires.timers == [], "code of the part's set a timer on the loop"
    # (The wires stand where connections are made: the test's own, to the dashboard, came past them.)
    assert wires.let_through >= 1
    return True


HOURS = 8


def test_never_signed_in_walka_asks_nobody_and_looks_into_no_store_however_long_it_runs(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            await the_accounts_are_gone(rig)
            began = Marks(rig)
            app = hand_in(rig, tmp_path)
            async with dashboard(app) as client:
                wires = Wires(monkeypatch, client.server.port)
                await app.start()
                await rig.idle()
                for _ in range(HOURS):
                    await an_hour(rig, app, client, "never signed in")
                    assert asked_nobody(rig, wires, began)
                # The computer's clock is set a year on, and a walk is saved after that.
                rig.clock.jump_wall(365 * 86400.0)
                await an_hour(rig, app, client, "never signed in")
                assert asked_nobody(rig, wires, began)

                # It was a whole Walka all the while: the walks are on disk, and each of them woke the
                # sender, which looked, found nobody signed in, and went back to sleep - without a clock.
                assert len(app.store.records) == HOURS + 1 == len(SessionStore(rig.dir).records)
                assert [call.limit for call in rig.wait.calls] == [None] * (HOURS + 3)
                assert [call.came for call in rig.wait.calls] == [True] * (HOURS + 2) + [None]
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["email"], piece["sending"], piece["last"]["word"]) == (
                    "signed_out",
                    "",
                    False,
                    "",
                )
                assert piece["counts"]["waiting"] == 0  # (nobody is signed in: nothing waits for anybody)
                await app.stop()
            assert await left_running() == []
            assert wires.tripped == [] and rig.fake.requests == [] and rig.keystore.calls == [] and rig.opened == []
            # Not a file of the part's either: no walka-account.json, no lock.
            assert sorted(path.name for path in rig.dir.iterdir() if "account" in path.name) == []
            assert app.treadmill.tripped == []

    run(scenario, 120)


def test_signed_in_with_sending_off_walka_asks_nobody_at_a_start_or_after(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            # An earlier run: somebody signed in, and did not switch sending on.
            earlier = hand_in(rig, tmp_path)
            await earlier.start()
            user = rig.person()
            rig.walk(7200)
            await rig.sign_in()
            assert earlier.snapshot()["walka_account"]["state"] == "signed_in"
            await earlier.stop()
            assert await left_running() == [] and len(rig.keystore.secrets()) == 1
            written = (rig.dir / ledger.FILE_NAME).read_bytes()

            await the_accounts_are_gone(rig)
            began = Marks(rig)
            app = hand_in(rig, tmp_path)
            async with dashboard(app) as client:
                wires = Wires(monkeypatch, client.server.port)
                await app.start()
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["email"], piece["sending"]) == ("signed_in", "walker@mail.example", False)
                for hour in range(HOURS):
                    await an_hour(rig, app, client, "signed in, sending off")
                    assert asked_nobody(rig, wires, began)
                    # The walks are counted - Walka sees them - and none of them goes anywhere.
                    assert app.snapshot()["walka_account"]["counts"]["waiting"] == hour + 2
                rig.clock.jump_wall(365 * 86400.0)
                await an_hour(rig, app, client, "signed in, sending off")
                assert asked_nobody(rig, wires, began)
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["first_step"]) == ("signed_in", False, "unknown")
                assert rig.fake.walks(user) == [] and rig.account_in_book(user)["walks"] == {}
                # ("Stop sending" was pressed while it was off: the file says what it said, and no more.)
                assert json.loads((rig.dir / ledger.FILE_NAME).read_text("utf-8")) == json.loads(written)

                # Signing out now asks nobody either: the access token of the earlier run is long gone,
                # and nobody makes a new one in order to sign out. The key leaves the store, and that is all.
                assert await said(client, "/api/walka-account/sign-out") == OK
                await rig.idle()
                assert wires.tripped == [] and len(rig.fake.requests) == began.requests
                assert rig.keystore.calls[began.store_calls :] == [("delete", rig.entry)]
                assert rig.keystore.secrets() == []
                assert rig.wait.limits(began.waits) == []
                await app.stop()
            assert await left_running() == []
            assert wires.tripped == [] and len(rig.fake.requests) == began.requests and len(rig.opened) == 1

    run(scenario, 120)


def test_with_sending_on_and_nothing_to_send_walka_asks_nobody_and_makes_no_new_key(tmp_path, monkeypatch):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            # Somebody signs in and switches sending on: two walks go. From then on nothing waits.
            app = hand_in(rig, tmp_path)
            user = rig.person()
            first, second = rig.walk(7200), rig.walk(3600)
            async with dashboard(app) as client:
                await app.start()
                await rig.sign_in()
                await rig.switch_on("all")
                assert sorted(row["client_id"] for row in rig.fake.walks(user)) == sorted([first.id, second.id])
                assert rig.account.access_token() is not None  # the key to ask with is in memory, good for an hour

                await the_accounts_are_gone(rig)
                began = Marks(rig)
                wires = Wires(monkeypatch, client.server.port)
                state = "sending on, nothing to send"
                # Walks that lie there and are nothing to send, whenever Walka looks: a file that does not
                # say which treadmill made it, a walk the Walka account would not take, and a file that
                # is no walk at all. (Their files are read, at this start and at the next. Nobody is asked.)
                unnamed, _held, torn = rig.walk(9000), rig.walk(8000, steps=500_000), rig.walk(5000)
                rig.rewrite(unnamed, drop=("treadmill",))
                rig.file_of(torn).write_text('{"id": "' + torn.id + '", "start": ', "utf-8")
                unfit = {"unnamed": 1, "held": 1, "unreadable": 1}
                for hour in range(HOURS):
                    await an_hour(rig, app, client, state, somebody_walks=False)
                    # Things that wake the sender, and are nothing to send: a walk too short to be kept ...
                    await walk_on(rig, app, 10, then_stand=True)
                    # ... a made-up walk that lies among the real ones ...
                    rig.walk(1800 - hour, treadmill="Simulated pad")
                    rig.account.walk_saved()
                    await rig.idle()
                    # ... and a wake for nothing at all.
                    await rig.saved()
                    assert asked_nobody(rig, wires, began)
                # The access token ran out hours ago, by the clock that only runs forwards. Nobody made a
                # new one: that is done when a request is about to be made, and none was.
                assert rig.account.access_token() is None and rig.account._access is not None
                # Then the computer's clock is set a day on, and a day back.
                for jump in (86400.0, -2 * 86400.0):
                    rig.clock.jump_wall(jump)
                    await an_hour(rig, app, client, state, somebody_walks=False)
                    await rig.saved()
                    assert asked_nobody(rig, wires, began)
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["chosen"]) == ("signed_in", True, "all")
                assert piece["counts"] == {"waiting": 0, "sent": 2, "aside": 0, **unfit}
                assert len(rig.wait.calls) - began.waits >= 2 * HOURS  # (the sender did have its turns)
                await app.stop()
                # (Closing gives what is left of the part one second, by the loop's clock: the one timer
                # of the part's in all these hours, and it asks nobody.)
                assert len(wires.timers) == 1 and "service.py" in wires.timers[0] and wires.tripped == []
                wires.timers.clear()
            assert await left_running() == []

            # The next start, with sending on and nothing to send: nobody is asked, and the store for
            # secrets is not looked into - not at the start, and not in the hours after it.
            app = hand_in(rig, tmp_path)
            async with dashboard(app) as client:
                wires.dashboard_is_at(client.server.port)
                await app.start()
                await rig.idle()
                for _ in range(3):
                    await an_hour(rig, app, client, state, somebody_walks=False)
                    await rig.saved()
                    assert asked_nobody(rig, wires, began)
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"]) == ("signed_in", True)
                # (The file that is no walk at all is not in the list of a Walka that was started anew.)
                assert piece["counts"] == {"waiting": 0, "sent": 2, "aside": 0, **unfit, "unreadable": 0}

                # And now there IS a walk to send. Walka reads the stored sign-in and sets out for the
                # accounts - into the wires: they were live all the while. Nothing leaves this computer,
                # the walk waits, and only now is there a wait with a time limit.
                await walk_on(rig, app, 240, then_stand=True)
                await rig.idle()
                assert rig.keystore.calls[began.store_calls :] == [("read", rig.entry)]
                assert [how for how, *_rest in wires.tripped] == ["loop.sock_connect"] or [
                    how for how, *_rest in wires.tripped
                ] == ["loop.create_connection"]
                assert wires.tripped[0][1][:2] == (rig.origin.host, rig.origin.port)
                assert rig.wait.limits(began.waits) == [60.0] and len(rig.fake.requests) == began.requests
                # (The wire on timers was live too: the store for secrets is asked with a time limit.)
                assert wires.timers and "threads.py" in wires.timers[0]
                assert wires.let_through >= 1  # ... and the test's own looks at the dashboard came past the wires
                piece = (await shown(client))["walka_account"]
                assert (piece["counts"]["waiting"], piece["last"]["word"]) == (1, "later")
                assert len(rig.fake.walks(user)) == 2 and rig.opened[1:] == []
                await app.stop()
            assert await left_running() == []

    run(scenario, 120)
