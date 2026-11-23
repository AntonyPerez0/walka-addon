"""The Walka account's part, handed to Walka: the ``App``, the dashboard's routes, the command line.

By default the part is not there, and then Walka is as it was: no key in
the snapshot, routes that answer 404, nothing asked of anybody. Handed in,
the ``App`` only ever tells it things - that a walk was saved, that Walka
is closing - and nothing of it can cost the person their walk. And the
command line hands it in only to a Walka that runs from its source code,
with both variables set, on a treadmill that is not the simulated one.

The accounts are the stand-in (``fake_accounts``), the store for secrets
is one in memory, and no test opens a real browser. After EVERY test the
rig's fixture looks again: nothing outside the list of what Walka may
ask, no key of a sign-in in any file, in the snapshot or in the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

import walka_account_rig
from conftest import HERE, SRC
from fake_accounts import FakeAccounts
from test_app import Rig as HouseRig
from test_app_ftms import PadRig
from walka import app as app_module
from walka import cli, web
from walka.app import FREE_WALK_GRACE_S, TICK_S, App
from walka.config import Config
from walka.session import SessionStore
from walka.sources import BeaconSource
from walka.treadmill import TreadmillStatus
from walka.treadmill.simulated import SimulatedTreadmill
from walka.walka_account import keystore, ledger
from walka.walka_account.service import WalkaAccount
from walka_account_rig import NOW, OK, PAD, CountingKeyStore, Rig

URL_VARIABLE, KEY_VARIABLE = "WALKA_ACCOUNT_URL", "WALKA_ACCOUNT_KEY"


@pytest.fixture(scope="module", autouse=True)
def somebody_has_set_both_variables_for_a_trial_of_their_own():
    """As on the computer of whoever tries the real thing: both variables
    are in the environment the tests are started from. conftest.py takes
    them away for each test - or every Walka started here would have the
    part. (They point at a port where nothing listens.)"""
    before = {name: os.environ.get(name) for name in (URL_VARIABLE, KEY_VARIABLE)}
    os.environ[URL_VARIABLE], os.environ[KEY_VARIABLE] = "http://127.0.0.1:9", "left-in-the-environment"
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def nothing_outside_the_list_and_nothing_told(caplog):
    walka_account_rig.before_test(caplog)
    yield
    walka_account_rig.after_test(caplog)


THE_TEST_ITSELF: set[asyncio.Task] = set()


def run(scenario, limit_s: float = 30):
    """The house way: one scenario, one loop, half a minute at most."""

    async def main():
        # (Before Python 3.11 ``wait_for`` runs what it is given in a task of its own: two tasks are the test's.)
        THE_TEST_ITSELF.clear()
        THE_TEST_ITSELF.update(asyncio.all_tasks())
        return await scenario()

    return asyncio.run(asyncio.wait_for(main(), limit_s))


JSON = {"Content-Type": "application/json"}
ROUTES = (
    "/api/walka-account/look-again",
    "/api/walka-account/notice/ok",
    "/api/walka-account/question",
    "/api/walka-account/sending",
    "/api/walka-account/sending/off",
    "/api/walka-account/sign-in",
    "/api/walka-account/sign-out",
)
NOT_THERE = {"ok": False, "error": "not available"}
CLOSING = {"ok": False, "error": "closing"}
NOT_SIGNED_IN = {"ok": False, "error": "not signed in"}
# What a page that is not Walka's, or a broken one, might send.
NO_OBJECT = (list, int, str)
JUNK: tuple[Any, ...] = (
    {},
    {"button": "start"},
    {"button": "faster"},
    {"action": "start", "speed_kmh": 3.0},
    [1, 2],
    ["github"],
    7,
    "github",
    {"way": 7},
    {"way": None},
    {"way": ["github"]},
    {"way": {"way": "github"}},
    {"way": "email"},
    {"way": "GitHub"},
    {"way": "github "},
    {"button": "github"},
    {"action": "sign-in", "provider": "github"},
    {"question": "", "send": "all"},
    {"question": None, "send": "all"},
    {"question": 7, "send": "from_now_on"},
    {"send": "all"},
    {"question": "\ud800", "send": "all"},
    {"sending": True},
    {"on": True, "send": "all"},
)
# The keys of the snapshot without the part - as test_app.py pins them - and of the part's own piece.
SNAPSHOT_KEYS = {
    "version",
    "time",
    "units",
    "treadmill",
    "source",
    "session",
    "last_session",
    "totals",
    "heart_rate",
    "addon",
    "setup",
    "config",
    "events",
    "journey",
}
PART_KEYS = {
    "state",
    "host",
    "store",
    "problem",
    "notice",
    "email",
    "way",
    "first_step",
    "can_send",
    "sending",
    "chosen",
    "from",
    "busy",
    "counts",
    "last",
    "aside",
    "held",
    "question",
    "rev",
}


class Pad:
    """A treadmill that is listened to and is not the simulated one - and
    that would notice a command: it has the ones an older Walka used, each
    of them a tripwire (as in test_web.py)."""

    kind = "ftms"

    def __init__(self) -> None:
        self.status = TreadmillStatus(name=PAD)
        self.address = None
        self.units = "auto"
        self.on_event = None
        self.tripped: list[str] = []
        self.let_go = 0

    async def connect(self) -> None:
        self.status.connected = True

    async def disconnect(self) -> None:
        self.let_go += 1
        self.status.connected = False

    async def discover(self, timeout: float):
        return []

    def __getattr__(self, name: str):
        if name.startswith(("start", "stop", "pause", "resume", "set_", "press_", "write", "command", "halt")):
            self.tripped.append(name)
            raise AssertionError(f"the companion reached for treadmill.{name}")
        raise AttributeError(name)


def a_config(tmp_path: Path) -> Config:
    config = Config()
    addon = tmp_path / "AddOns" / "Walka"
    addon.mkdir(parents=True, exist_ok=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    config.wow.addon_dir = str(addon)
    return config


def hand_in(rig: Rig, tmp_path: Path, *, treadmill: Any = None, part: bool = True) -> App:
    """A real ``App`` on the rig's folder and clock - with a part of the rig's handed in, or with none.

    From here on the rig's buttons (``rig.sign_in`` and the rest) press
    the part that was handed in, and ``rig.walk`` saves into the App's own list.
    """
    account = rig.build(attach=False) if part else None
    app = App(
        a_config(tmp_path),
        rig.dir,
        treadmill=treadmill if treadmill is not None else Pad(),
        beacon=BeaconSource(None, clock=rig.clock.mono),
        walka_account=account,
        wall_clock=rig.clock.wall,
        mono_clock=rig.clock.mono,
    )
    rig.app = app  # type: ignore[assignment]
    if account is not None:
        rig.account = account
    return app


@contextlib.asynccontextmanager
async def dashboard(app: App):
    async with TestClient(TestServer(web.create_app(app))) as client:
        yield client


async def post(client: TestClient, path: str, body: Any = None, **headers: str):
    sent = {**JSON, **headers}
    return await client.post(path, data=json.dumps({} if body is None else body), headers=sent)


async def said(client: TestClient, path: str, body: Any = None) -> tuple[int, Any]:
    """Press a button of the card: the status, and the answer (as JSON where it is JSON)."""
    response = await post(client, path, body)
    text = await response.text()
    try:
        return response.status, json.loads(text)
    except ValueError:
        return response.status, text


async def shown(client: TestClient) -> dict:
    """The snapshot, as the page gets it."""
    return await (await client.get("/api/state")).json()


async def walks_shown(client: TestClient) -> list[dict]:
    return (await (await client.get("/api/sessions")).json())["sessions"]


async def walk_on(rig: Rig, app: App, seconds: float = 60.0, *, then_stand: bool = False) -> None:
    """Somebody walks on the pad, and Walka listens: one tick after the other, by the made-up clock."""
    pad = app.treadmill
    pad.status.connected = True
    for _ in range(int(round(seconds / TICK_S))):
        rig.clock.advance(TICK_S)
        pad.status.speed_kmh, pad.status.updated_at = 3.0, rig.clock.mono()
        await app.tick()
    if then_stand:
        for _ in range(int(round((FREE_WALK_GRACE_S + 2) / TICK_S))):
            rig.clock.advance(TICK_S)
            pad.status.speed_kmh, pad.status.updated_at = 0.0, rig.clock.mono()
            await app.tick()


def names_in(thing: Any) -> set[str]:
    """Every key of a JSON object, however deep."""
    if isinstance(thing, dict):
        return set(thing).union(*(names_in(value) for value in thing.values()))
    if isinstance(thing, list):
        return set().union(*(names_in(value) for value in thing))
    return set()


def of_the_part(names: set[str]) -> set[str]:
    return {name for name in names if "account" in name.lower() or "sending" in name.lower()}


def leftovers() -> list[asyncio.Task]:
    """The house's look at what is still running, but for the test itself."""
    return [task for task in asyncio.all_tasks() if task not in THE_TEST_ITSELF]


def parts_tasks() -> list[str]:
    return [task.get_name() for task in leftovers() if "walka-account" in task.get_name()]


def a_servers_connection(task: asyncio.Task) -> bool:
    """A connection that a server on 127.0.0.1 still holds open - the stand-in's, say (aiohttp's own task)."""
    return getattr(task.get_coro(), "__qualname__", "").startswith("RequestHandler.")


async def left_running(within_s: float = 10.0) -> list[str]:
    """What is still running now that Walka has stopped: the house's "no task left".

    Asked at once, without a wait, of everything of Walka's own. Only a
    server's connection that was just closed from the other end gets time
    to notice it - a turn of the loop here, more on a slow machine.
    """
    at_once = [task.get_name() for task in leftovers() if not a_servers_connection(task)]
    if at_once:
        return at_once
    loop = asyncio.get_running_loop()
    until = loop.time() + within_s
    while leftovers() and loop.time() < until:
        await asyncio.sleep(0.01)
    return [task.get_name() for task in leftovers()]


# How long closing Walka may take here, in real seconds. A second would do
# (it takes a few thousandths); the room is for a slow machine. Still well
# inside what whoever ends Walka waits for the walk (cli.SAVE_WAIT_S).
CLOSES_WITHIN_S = cli.SAVE_WAIT_S - 1.0


def closed_without_running_out_of_time(caplog) -> bool:
    """The part's close() has a second for what is left, and says so when that was not enough: it never was."""
    return not any("did not close in time" in record.getMessage() for record in caplog.records)


def quiet(rig: Rig) -> bool:
    """Nothing was asked of anybody: no request, no look into the store for secrets, no browser, no file."""
    return (
        rig.fake.requests == []
        and rig.keystore.calls == []
        and rig.opened == []
        and not (rig.dir / ledger.FILE_NAME).exists()
    )


# ---- without the part: Walka as it was ---------------------------------------------------------


def test_without_the_part_every_route_answers_404_and_nothing_else_happens(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path, part=False)
            assert app.walka_account is None
            rig.walk(3600)
            before = sorted(path.name for path in rig.dir.rglob("*"))
            async with dashboard(app) as client:
                for path in ROUTES:
                    for body in (*JUNK, {"way": "github"}, {"question": "q", "send": "all"}):
                        response = await post(client, path, body)
                        assert (response.status, await response.json()) == (404, NOT_THERE), (path, body)
                        assert response.headers["Cache-Control"] == "no-store"  # under the guard, like every answer
                    # ... and the guard stands before them all the same.
                    assert (await post(client, path, {"way": "github"}, Origin="https://evil.example")).status == 403
                    assert (await client.post(path, data="way=github")).status == 415
                assert set(await shown(client)) == SNAPSHOT_KEYS
                assert set(app.snapshot()) == SNAPSHOT_KEYS
                (walk,) = await walks_shown(client)
                assert "in_walka_account" not in walk
                assert of_the_part(names_in(await (await client.get("/api/config")).json())) == set()
                await app.tick()
            assert quiet(rig) and parts_tasks() == []
            assert sorted(path.name for path in rig.dir.rglob("*")) == before  # no file of the part's, no lock

    run(scenario)


def test_the_parts_routes_are_these_seven_posts_whether_it_is_there_or_not(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            for part in (False, True):
                app = hand_in(rig, tmp_path, part=part)
                routes = sorted(
                    (route.method, route.resource.canonical)
                    for route in web.create_app(app).router.routes()
                    if route.resource is not None and "account" in route.resource.canonical
                )
                assert routes == [("POST", path) for path in ROUTES]

    run(scenario)


def test_a_real_app_without_the_part_starts_and_stops_as_it_did(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path, part=False)
            await app.start()
            await walk_on(rig, app, 60)
            await app.stop()
            assert await left_running() == []
            assert len(SessionStore(rig.dir).records) == 1 and quiet(rig)

    run(scenario)


# ---- the snapshot --------------------------------------------------------------------------------


def test_the_snapshot_gains_one_key_with_the_part_and_none_without(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            without = hand_in(rig, tmp_path, part=False)
            assert set(json.loads(json.dumps(without.snapshot()))) == SNAPSHOT_KEYS

            app = hand_in(rig, tmp_path)
            rig.person()
            snapshot = json.loads(json.dumps(app.snapshot()))
            assert set(snapshot) == SNAPSHOT_KEYS | {"walka_account"}
            assert set(snapshot["walka_account"]) == PART_KEYS
            assert snapshot["walka_account"]["state"] == "signed_out"
            assert snapshot["walka_account"] == rig.account.snapshot()
            # Everything else is as it is without the part.
            for key in SNAPSHOT_KEYS - {"time", "totals"}:
                assert snapshot[key] == json.loads(json.dumps(without.snapshot()))[key], key
            assert quiet(rig)  # looking asks nobody

            await rig.sign_in()
            snapshot = app.snapshot()
            assert (snapshot["walka_account"]["state"], snapshot["walka_account"]["email"]) == (
                "signed_in",
                "walker@mail.example",
            )
            # The e-mail address is in the part's piece and nowhere else; the settings know nothing of it.
            assert "walker@mail.example" not in json.dumps({key: snapshot[key] for key in SNAPSHOT_KEYS})
            assert set(snapshot["config"]) == {"units", "profile", "treadmill", "heart_rate", "wow", "web"}

    run(scenario)


def test_the_event_stream_carries_the_parts_piece_too(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            async with dashboard(app) as client:
                async with client.get("/api/events") as stream:
                    line = await asyncio.wait_for(stream.content.readline(), 5)
                    app._stopping.set()  # (the stream ends when Walka is closing)
                assert json.loads(line[len(b"data: ") :])["walka_account"]["state"] == "signed_out"
            assert quiet(rig)

    run(scenario)


# ---- the routes, with the part -------------------------------------------------------------------


def test_the_guard_stands_before_the_parts_routes(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            rig.person()
            async with dashboard(app) as client:
                good = {"way": "github"}
                for path in ROUTES:
                    # Another site's page, open in the same browser.
                    assert (await post(client, path, good, Origin="https://evil.example")).status == 403
                    assert (await post(client, path, good, Origin="null")).status == 403
                    assert (await post(client, path, good, Host="evil.example")).status == 403
                    # What a form on such a page can send without asking leave.
                    for kind in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
                        response = await client.post(path, data=json.dumps(good), headers={"Content-Type": kind})
                        assert response.status == 415, (path, kind)
                    assert (await client.post(path, data=json.dumps(good))).status == 415  # no type at all
                    # None of them is a GET: following a link changes nothing.
                    for method in ("GET", "HEAD", "PUT", "DELETE", "PATCH"):
                        assert (await client.request(method, path, headers=JSON)).status == 405, (method, path)
                assert quiet(rig) and app.snapshot()["walka_account"]["state"] == "signed_out"
                assert rig.account._listeners == set() and rig.account._tasks == set()

                # Walka's own page says where it comes from, and is let through.
                here = f"http://{client.server.host}:{client.server.port}"
                response = await post(client, "/api/walka-account/sign-in", good, Origin=here)
                assert (response.status, await response.json()) == (200, {"ok": True})
                await rig.idle()
                assert len(rig.opened) == 1 and app.snapshot()["walka_account"]["state"] == "signed_in"

    run(scenario)


def test_every_button_of_the_card_through_its_route(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            rig.account.start()
            user = rig.person("walker@mail.example", way="github")
            sent, waits = rig.walk(7200), rig.walk(3600)
            async with dashboard(app) as client:
                # Sign in: the route answers at once, the rest shows in the snapshot.
                assert await said(client, "/api/walka-account/sign-in", {"way": "github"}) == (200, {"ok": True})
                assert (await shown(client))["walka_account"]["state"] == "signing_in"
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["email"], piece["way"]) == ("signed_in", "walker@mail.example", "github")
                assert (piece["first_step"], piece["sending"], piece["counts"]["waiting"]) == ("taken", False, 2)
                assert len(rig.opened) == 1 and rig.pushes() == []
                assert await said(client, "/api/walka-account/sign-in", {"way": "github"}) == (
                    409,
                    {"ok": False, "error": "signed in already"},
                )

                # Look again: asks the Walka account, once.
                before = len(rig.asked("/rest/v1/profiles"))
                assert await said(client, "/api/walka-account/look-again") == (200, {"ok": True})
                await rig.idle()
                assert len(rig.asked("/rest/v1/profiles")) == before + 1

                # The question, and its answer spelled out.
                assert await said(client, "/api/walka-account/question") == (200, {"ok": True})
                await rig.idle()
                question = (await shown(client))["walka_account"]["question"]
                assert (question["email"], question["waiting"]) == ("walker@mail.example", 2)
                rig.store.delete(waits.id)  # (deleted before it went: it is never sent)
                waits = rig.walk(1800)
                answer = {"question": question["id"], "send": "all"}
                assert await said(client, "/api/walka-account/sending", answer) == (200, {"ok": True})
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["sending"], piece["chosen"], piece["question"]) == (True, "all", None)
                assert (piece["counts"]["sent"], piece["counts"]["waiting"], piece["last"]["word"]) == (2, 0, "sent")
                assert [row["client_id"] for row in rig.fake.walks(user)] == [sent.id, waits.id]
                # A question is answered once.
                assert (await said(client, "/api/walka-account/sending", answer))[0] == 400

                # History is told which walks have a copy in the Walka account.
                later = rig.walk(60)  # (nobody woke the sender: it waits)
                flags = {walk["id"]: walk["in_walka_account"] for walk in await walks_shown(client)}
                assert flags == {sent.id: True, waits.id: True, later.id: False}
                one = await (await client.get(f"/api/sessions/{sent.id}")).json()
                assert "in_walka_account" not in one  # (a walk's own page is the file, as it was)

                # Stop sending: at once, and nothing more goes.
                assert await said(client, "/api/walka-account/sending/off") == (200, {"ok": True})
                assert (await shown(client))["walka_account"]["sending"] is False
                await rig.saved()
                assert len(rig.fake.walks(user)) == 2

                # Sign out: said at once; the key leaves the store.
                assert await said(client, "/api/walka-account/sign-out") == (200, {"ok": True})
                assert (await shown(client))["walka_account"]["state"] == "signed_out"
                await rig.idle()
                assert rig.keystore.secrets() == []
                assert {walk["in_walka_account"] for walk in await walks_shown(client)} == {False}
                for path in ("look-again", "question", "sending/off"):
                    assert await said(client, f"/api/walka-account/{path}") == (409, NOT_SIGNED_IN)

                # The sign-in ends elsewhere: said in the card until "OK".
                assert await said(client, "/api/walka-account/sign-in", {"way": "github"}) == (200, {"ok": True})
                await rig.idle()
                rig.fake.sign_out_everywhere(user)
                await rig.advance(3700)
                assert await said(client, "/api/walka-account/look-again") == (200, {"ok": True})
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["notice"]) == ("signed_out", "ended")
                assert await said(client, "/api/walka-account/notice/ok") == (200, {"ok": True})
                assert (await shown(client))["walka_account"]["notice"] == ""
                await rig.idle()
            assert rig.book()["addresses"][rig.origin.text]["notice"] is None
            # Nothing of all this went through the Live view's list, which is also the log.
            told = json.dumps(list(app.events)).lower()
            assert "account" not in told and "walker@" not in told and "sign" not in told
            # And the game's addon hears nothing of it: what Walka writes into the addon's folder is, byte
            # for byte, what a Walka without the part writes there.
            written = Path(app.config.wow.addon_dir) / "CompanionData.lua"
            assert app.sync_addon()
            with_the_part = written.read_bytes()
            assert b"WalkaCompanionData = {" in with_the_part and b"walker@" not in with_the_part
            assert [key for key in rig.needles() if key and key.encode() in with_the_part] == []
            written.unlink()
            assert hand_in(rig, tmp_path, part=False).sync_addon() and written.read_bytes() == with_the_part

    run(scenario)


def test_junk_at_the_parts_routes_does_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            rig.account.start()
            await rig.idle()
            rig.person()
            rig.walk(3600)
            # (Nothing at all is what a button sends that needs nothing said; the rest is no JSON object.)
            raw = (b"", b"{", b"\xff\xfe", b'{"way": "github"', b'{"way":"github"}{"way":"github"}', b"NaN", b"null")
            async with dashboard(app) as client:
                for path in ROUTES:
                    for body in JUNK:
                        status, _answer = await said(client, path, body)
                        assert status < 500, (path, body)
                        if path.endswith(("/sign-in", "/sending")) or isinstance(body, NO_OBJECT):
                            assert status == 400, (path, body)
                    for body in raw:
                        response = await client.post(path, data=body, headers=JSON)
                        assert response.status < 500, (path, body)
                        if path.endswith(("/sign-in", "/sending")) or body:
                            assert response.status == 400, (path, body)
                    await rig.idle()
                    await app.tick()
                # Nobody is signed in, and none of it changed that: no listener, no browser, no request, no
                # look into the store for secrets, no file.
                assert quiet(rig)
                assert rig.account._listeners == set() and rig.account._signin is None
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["problem"]) == ("signed_out", False, "")

                # Signed in, with the question open: junk never answers it, and the right answer still does.
                await rig.sign_in()
                question = await rig.question()
                assert question is not None
                wrong = (
                    *JUNK,
                    {"question": question["id"]},
                    {"question": question["id"], "send": "ALL"},
                    {"question": question["id"], "send": "everything"},
                    {"question": question["id"], "send": True},
                    {"question": question["id"], "send": ["all"]},
                    {"question": question["id"] + "x", "send": "all"},
                    {"question": question["id"][:-1], "send": "all"},
                    {"question": [question["id"]], "send": "all"},
                    {"id": question["id"], "send": "all"},
                    {"question": question["id"], "choice": "all"},
                    {"question": question["id"], "send": None, "all": True},
                )
                for body in wrong:
                    assert (await said(client, "/api/walka-account/sending", body))[0] == 400, body
                    assert (await said(client, "/api/walka-account/sign-in", body))[0] in (400, 409), body
                # What is no JSON object presses no button at all - not "Sign out" and not "Stop sending" either.
                requests, store_calls = len(rig.fake.requests), len(rig.keystore.calls)
                for path in ROUTES:
                    for body in (thing for thing in JUNK if isinstance(thing, NO_OBJECT)):
                        assert (await said(client, path, body))[0] == 400, (path, body)
                    for body in raw[1:]:
                        assert (await client.post(path, data=body, headers=JSON)).status == 400, (path, body)
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["question"]) == ("signed_in", False, question)
                assert rig.pushes() == [] and len(rig.opened) == 1
                assert (len(rig.fake.requests), len(rig.keystore.calls)) == (requests, store_calls)
                assert rig.account_in_book(next(iter(rig.people)))["sending"] is False
                # /api/config cannot switch it on either: it is no setting.
                settings = {"walka_account": {"sending": True}, "sending": True, "web": {"walka_account": True}}
                assert (await post(client, "/api/config", settings)).status == 200
                await rig.idle()
                assert (await shown(client))["walka_account"]["sending"] is False and rig.pushes() == []
                assert of_the_part(names_in(json.loads((rig.dir / "config.json").read_text("utf-8")))) == set()
                assert of_the_part(names_in(await (await client.get("/api/config")).json())) == set()

                answer = {"question": question["id"], "send": "from_now_on"}
                assert await said(client, "/api/walka-account/sending", answer) == (200, {"ok": True})
                await rig.idle()
                piece = (await shown(client))["walka_account"]
                assert (piece["sending"], piece["chosen"]) == (True, "from")
                assert rig.pushes() == []  # only walks from now on

    run(scenario)


BUTTONS = {
    "/api/walka-account/look-again": "look_again",
    "/api/walka-account/notice/ok": "notice_ok",
    "/api/walka-account/question": "prepare_question",
    "/api/walka-account/sending": "switch_on",
    "/api/walka-account/sending/off": "switch_off",
    "/api/walka-account/sign-in": "sign_in",
    "/api/walka-account/sign-out": "sign_out",
}


def test_each_route_presses_its_own_button_and_one_that_breaks_leaves_none_of_its_words_in_the_log(tmp_path, caplog):
    """The part's buttons do not raise. Should one ever, the dashboard's
    server would write the error into the log with all its words, and the
    log is what people attach to public issues: so the route says what kind
    of error it was, and no more."""
    assert tuple(BUTTONS) == ROUTES
    pressed: list[tuple] = []
    breaks: list[bool] = []

    def button(name: str):
        def press(*told: object) -> tuple[int, dict]:
            pressed.append((name, *told))
            if breaks:
                raise RuntimeError("walker@mail.example at https://accounts.example said no to test-key")
            return 202, {"ok": True, "pressed": name}

        return press

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            for name in BUTTONS.values():
                setattr(rig.account, name, button(name))
            body = {"way": "github", "question": "q", "send": "all", "more": 1}
            async with dashboard(app) as client:
                # Each route presses its own button, with what the body spells out and nothing else of it,
                # and answers what the button answers.
                for path, name in BUTTONS.items():
                    assert await said(client, path, body) == (202, {"ok": True, "pressed": name})
                assert pressed == [
                    ("look_again",),
                    ("notice_ok",),
                    ("prepare_question",),
                    ("switch_on", "q", "all"),
                    ("switch_off",),
                    ("sign_in", "github"),
                    ("sign_out",),
                ]
                breaks.append(True)
                for path in ROUTES:
                    assert await said(client, path, body) == (500, {"ok": False, "error": "failed"})
                    # (A body that is no JSON is still refused before any button is pressed.)
                    if path.endswith(("/sign-in", "/sending")):
                        assert (await client.post(path, data=b"{", headers=JSON)).status == 400
                assert len(pressed) == 14

    run(scenario)
    loud = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert [(record.name, record.levelno, record.exc_info) for record in loud] == [("walka.web", logging.WARNING, None)] * 7
    assert caplog.text.count("RuntimeError") == 7
    for word in ("walker@", "accounts.example", "test-key", "said no", "Traceback"):
        assert word not in caplog.text


def every_route_that_changes_something(client: TestClient) -> list[tuple[str, str]]:
    return sorted(
        (route.method, route.resource.canonical)
        for route in client.app.router.routes()
        if route.method in ("POST", "DELETE") and route.resource is not None
    )


@pytest.mark.parametrize("state", ["never signed in", "signed in, sending on"])
def test_no_route_of_the_api_reaches_a_real_treadmill_with_the_part_handed_in(tmp_path, state):
    """The run of test_web.py over the whole API, asked everything it can be
    asked, once more with the Walka account's part there - against a reader
    that would notice a command. (docs/SAFETY.md counts on these runs.)"""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            pad = Pad()
            app = hand_in(rig, tmp_path, treadmill=pad)
            rig.account.start()
            user = rig.person()
            first = rig.walk(7200)
            if state != "never signed in":
                await rig.sign_in()
                await rig.switch_on("all")
                assert [row["client_id"] for row in rig.fake.walks(user)] == [first.id]
                assert app.snapshot()["walka_account"]["sending"] is True
            rig.walk(3600)  # one more, and nobody has woken the sender for it
            opened, switched_on = len(rig.opened), []
            really = rig.account.switch_on

            def switch_on(question: object, send: object):
                answer = really(question, send)
                switched_on.append(answer[0])
                return answer

            rig.account.switch_on = switch_on  # type: ignore[method-assign]
            async with dashboard(app) as client:
                routes = every_route_that_changes_something(client)
                assert ("POST", "/api/treadmill/press") in routes and len(routes) >= 17
                assert {path for _method, path in routes} >= set(ROUTES)
                for method, path in routes:
                    for body in ({}, {"button": "start"}, {"button": "faster"}, {"action": "start", "speed_kmh": 3.0}):
                        response = await client.request(
                            method, path.replace("{session_id}", "nothing"), data=json.dumps(body), headers=JSON
                        )
                        assert response.status < 500, (method, path, body)
                    await rig.idle()
                    await app.tick()
                assert (await post(client, "/api/treadmill/press", {"button": "start"})).status == 400
            assert pad.tripped == []
            # Junk opened no browser and never switched sending on; the walk that waited was not sent.
            assert len(rig.opened) == opened
            assert switched_on and set(switched_on) == {400}
            assert len(rig.fake.walks(user)) == (0 if state == "never signed in" else 1)
            if state == "never signed in":
                assert quiet(rig)
            else:
                # (An empty body at "stop sending" and at "sign out" does what those buttons do: it stops.)
                piece = app.snapshot()["walka_account"]
                assert (piece["state"], piece["sending"]) == ("signed_out", False)
                await rig.idle()
                assert rig.keystore.secrets() == []

    run(scenario)


def test_the_house_rigs_simulated_treadmill_keeps_the_part_out_of_everything(tmp_path):
    """The treadmill in use is the simulated one - here with a part handed
    in all the same, as nothing in Walka does: it says "unavailable", every
    button refuses, and a made-up walk goes nowhere."""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            house = HouseRig(tmp_path)
            part = rig.build(attach=False)
            app = App(
                house.config,
                rig.dir,
                treadmill=house.treadmill,
                beacon=house.beacon,
                walka_account=part,
                wall_clock=lambda: house.clock.wall,
                mono_clock=lambda: house.clock.mono,
            )
            house.app = app
            assert isinstance(app.treadmill, SimulatedTreadmill) and part.usable is False
            part.start()
            await house.connect()
            await house.walk()
            await house.run(60, signal=False)
            house.treadmill.press_stop()
            await house.run(FREE_WALK_GRACE_S + 5, signal=False)
            (record,) = app.store.records
            assert record.treadmill == "Simulated pad"
            await part.idle()
            assert app.snapshot()["walka_account"]["state"] == "unavailable"
            async with dashboard(app) as client:
                for path in ROUTES:
                    for body in ({}, {"way": "github"}, {"question": "q", "send": "all"}):
                        assert await said(client, path, body) == (409, NOT_THERE), (path, body)
                (walk,) = await walks_shown(client)
                assert walk["in_walka_account"] is False
            await part.idle()
            assert quiet(rig) and part._listeners == set()

    run(scenario)


def test_which_treadmill_is_in_use_is_looked_at_each_time(tmp_path):
    """cli builds the part once, for the treadmill Walka starts with. Should
    the treadmill in use ever be another, it is the one in use that counts."""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            pad = Pad()
            app = hand_in(rig, tmp_path, treadmill=pad)
            rig.account.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            rig.walk(3600)
            async with dashboard(app) as client:
                app.treadmill = SimulatedTreadmill()
                assert (await shown(client))["walka_account"]["state"] == "unavailable"
                assert (await shown(client))["walka_account"]["email"] == ""
                for path in ROUTES:
                    assert await said(client, path, {"way": "github"}) == (409, NOT_THERE)
                await rig.saved()
                assert rig.fake.walks(user) == []  # nothing goes while it is the simulated one
                app.treadmill = pad
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["counts"]["waiting"]) == ("signed_in", True, 1)
                await rig.saved()
                assert len(rig.fake.walks(user)) == 1

    run(scenario)


def on_the_emulated_pad(rig: Rig, tmp_path: Path, monkeypatch) -> tuple[PadRig, App, WalkaAccount]:
    """The house's rig with the real Bluetooth reader on a made-up radio (test_app_ftms.py), and on it a
    real ``App`` with a part of ``rig``'s handed in. One clock for both: the house rig's."""
    house = PadRig(tmp_path, monkeypatch)

    class HouseClock:  # the rig's clock, reading the house rig's: the two must agree on what time it is
        wall = staticmethod(lambda: house.clock.wall)
        mono = staticmethod(lambda: house.clock.mono)
        advance = staticmethod(house.clock.advance)

    rig.clock = rig.wait._clock = HouseClock()  # type: ignore[assignment]
    part = rig.build(attach=False)
    app = App(
        house.config,
        rig.dir,
        treadmill=house.treadmill,
        beacon=house.beacon,
        walka_account=part,
        wall_clock=HouseClock.wall,
        mono_clock=HouseClock.mono,
    )
    house.app, rig.app, rig.account = app, app, part  # type: ignore[assignment]
    return house, app, part


def test_a_walk_on_the_emulated_pad_reaches_the_walka_account_and_nothing_reaches_the_pad(tmp_path, monkeypatch):
    """End to end with the real Bluetooth reader on a made-up radio: the
    walk is recorded, saved, and - sending being on - sent, its row made
    from the file. The pad hears nothing from Walka, as ever."""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            house, app, part = on_the_emulated_pad(rig, tmp_path, monkeypatch)
            part.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("from_now_on")
            assert rig.pushes() == []

            await house.walking(seconds=300)
            house.pad.press_stop()
            await house.run(5)
            await house.run(3, active=False)
            (record,) = app.store.records
            assert record.treadmill == "URTM030" and record.source == "wow"
            assert rig.fake.walks(user) == []  # (the walk is saved; the sender has not had its turn yet)
            await rig.idle()

            (row,) = rig.fake.walks(user)
            assert (row["client_id"], row["origin"], row["session"]) == (record.id, "walka", "wow")
            assert row["steps"] == record.steps and row["seconds"] == pytest.approx(record.seconds, abs=0.5)
            assert app.snapshot()["walka_account"]["counts"] == {**ZERO, "sent": 1}
            assert part.in_account(record.id, record.start) is True
            assert house.heard_nothing_from_walka()
            # What was sent holds nothing of the pad, the samples or the heart rate.
            (sent,) = rig.pushes()[0]
            assert "URTM030" not in json.dumps(sent) and not {"treadmill", "samples", "max_kmh", "avg_hr"} & set(sent)

    run(scenario)


ZERO = {"waiting": 0, "sent": 0, "aside": 0, "held": 0, "unnamed": 0, "unreadable": 0}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_soak_on_the_emulated_pad_with_the_part_handed_in(tmp_path, monkeypatch, seed):
    """The long run of test_app_ftms.py once more, with the part there:
    anything the game, the radio and the Walka-account card can do, in any
    order, walks being recorded, saved and sent meanwhile - and at the end
    the pad has still not been sent a single byte. (docs/SAFETY.md counts
    on these runs.)"""
    rng = random.Random(seed)
    pad_does = ["start", "speed", "speed", "pause", "stop", "key", "drop", "connect"]
    card_does = ["sign-in", "sign-in", "switch-on", "switch-on", "switch-on", "look-again", "sending/off", "sign-out"]
    card_does += ["notice/ok", "junk"]

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            house, app, part = on_the_emulated_pad(rig, tmp_path, monkeypatch)
            part.start()
            user = rig.person()
            # First a walk that is sent for certain: whatever comes after, this was no idle run.
            await rig.sign_in()
            await rig.switch_on("all")
            await house.walking(seconds=120)
            house.pad.press_stop()
            await house.run(5)
            await house.run(3, active=False)
            await rig.idle()
            assert len(rig.fake.walks(user)) == 1 and house.heard_nothing_from_walka()

            async with dashboard(app) as client:
                for _ in range(80):
                    event = rng.choice([*pad_does, *pad_does, *card_does, None])
                    if event in ("start", "pause", "stop"):
                        getattr(house.pad, f"press_{event}")()
                    elif event == "speed":
                        house.pad.press_speed(rng.uniform(0.5, 6.0))
                    elif event == "key":
                        house.pad.pull_safety_key()
                    elif event == "drop":
                        house.pad.drop_link()
                    elif event == "connect":
                        app.connect()
                        await house.treadmill.connect()
                    elif event == "sign-in":
                        await said(client, "/api/walka-account/sign-in", {"way": rng.choice(["discord", "github", "google"])})
                    elif event == "switch-on":
                        await said(client, "/api/walka-account/question")
                        await rig.idle()
                        question = app.snapshot()["walka_account"]["question"]
                        answer = {"question": question["id"] if question else "", "send": rng.choice(["all", "from_now_on"])}
                        await said(client, "/api/walka-account/sending", answer)
                    elif event == "junk":
                        await said(client, rng.choice(ROUTES), rng.choice(JUNK))
                    elif event is not None:
                        await said(client, f"/api/walka-account/{event}")
                    await rig.idle()
                    game = rng.choice(["session", "paused", "none", "lost"])
                    await house.run(
                        rng.uniform(0.2, 30),
                        active=game != "none",
                        paused=game == "paused",
                        moving=rng.random() < 0.7,
                        signal=game != "lost",
                        session=rng.randint(1, 3),
                    )
                    await rig.idle()
                    assert house.heard_nothing_from_walka()
                await house.run(FREE_WALK_GRACE_S + 5, active=False)
                await rig.idle()
                await app.stop()
            assert house.heard_nothing_from_walka()
            assert await left_running() == []
            # Whatever went to the Walka account is a walk that is on this computer, and holds nothing of the pad.
            here = {record.id for record in app.store.records}
            assert {row["client_id"] for row in rig.fake.walks(user)} <= here
            # (With these seeds some five walks go during the run, after three or four sign-ins. Counted on
            # is only the first: which numbers a seed gives is Python's business.)
            assert len(rig.fake.walks(user)) >= 1
            assert "URTM030" not in json.dumps(rig.pushes())

    run(scenario, 120)


# ---- App.start() and App.stop() ------------------------------------------------------------------


def test_start_makes_the_parts_task_wakes_it_once_and_a_saved_walk_wakes_it_again(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            assert rig.account._app is app and parts_tasks() == []  # attached, and nothing begun
            await app.start()
            assert parts_tasks() == ["walka-account"]
            assert all("account" not in task.get_name() for task in app._tasks)  # its task is its own
            await rig.idle()
            # Woken once, it looked, and waits for the next wake - without a time limit. Nobody was asked.
            assert [call.came for call in rig.wait.calls] == [True, None] and rig.wait.limits() == []
            assert quiet(rig)

            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            assert rig.pushes() == []

            # A walk is walked, the walker stands still, the walk is saved: that wakes the sender.
            await walk_on(rig, app, 60, then_stand=True)
            (record,) = app.store.records
            assert rig.fake.walks(user) == []
            await rig.idle()
            assert [row["client_id"] for row in rig.fake.walks(user)] == [record.id]
            assert rig.entry_of(user, record)["state"] == "added"

            # A walk that cannot be saved wakes nobody; saved at the next try, it does.
            waits = len(rig.wait.calls)
            again = rig.walk(30, add=False)
            really, full = app.store.add, [True]

            def add(walk):
                if full:
                    raise OSError("No space left on device")
                return really(walk)

            app.store.add = add  # type: ignore[method-assign]
            app._unsaved.append(again)
            app._save_unsaved()
            await rig.idle()
            assert len(rig.wait.calls) == waits and len(rig.fake.walks(user)) == 1
            full.clear()
            app._save_unsaved()
            await rig.idle()
            assert len(rig.fake.walks(user)) == 2

            await app.stop()
            assert parts_tasks() == [] and all(task.done() for task in rig.account._tasks)
            assert await left_running() == [] and rig.account._tasks == set()
            assert app.treadmill.tripped == []

    run(scenario)


def test_stop_with_an_answer_held_at_the_accounts_returns_at_once_and_the_walk_is_on_disk(tmp_path, caplog):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            await app.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            earlier = rig.walk(7200)
            gate = rig.fake.hold("/rest/v1/walks", method="POST")
            rig.account.walk_saved()
            await gate.arrived.wait()  # a walk is on its way, and the accounts sit on their answer
            assert app.snapshot()["walka_account"]["busy"] == "sending"
            await walk_on(rig, app, 60)  # ... and somebody is on the pad
            assert app.recorder is not None and len(app.store.records) == 1

            loop = asyncio.get_running_loop()
            began = loop.time()
            await app.stop()
            # The answer never came, and nothing waited for it: not the walk, and not the part's own close().
            assert loop.time() - began < CLOSES_WITHIN_S and closed_without_running_out_of_time(caplog)
            assert parts_tasks() == [] and all(task.done() for task in rig.account._tasks)

            # The walk that was being recorded is on disk, whole.
            (kept,) = [record for record in SessionStore(rig.dir).records if record.id != earlier.id]
            assert kept.seconds >= 55 and kept.treadmill == PAD and (rig.dir / "sessions" / f"{kept.id}.json").is_file()
            assert not (rig.dir / "active-session.json").exists()
            await asyncio.wait_for(gate.left.wait(), 10)  # whoever asked has gone away
            gate.release()
            assert await left_running() == []
            assert rig.fake.walks(user) == [] and app.treadmill.tripped == []
            # Nothing was sent while closing, and the sign-in is still in the store for the next start.
            assert len(rig.pushes()) == 1 and len(rig.keystore.secrets()) == 1

            # At the next start both walks go: the one that was held, and the one saved while closing.
            again = hand_in(rig, tmp_path)
            await again.start()
            await rig.idle()
            assert sorted(row["client_id"] for row in rig.fake.walks(user)) == sorted([earlier.id, kept.id])
            await again.stop()
            assert await left_running() == []

    run(scenario)


def test_stop_in_the_middle_of_a_sign_in_takes_the_listener_down_and_saves_the_walk(tmp_path, caplog):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            await app.start()
            rig.person()
            rig.browser_comes = False  # the browser is open, and the person has not come back yet
            assert rig.account.sign_in("github") == OK
            await rig.idle()
            (opened,) = rig.opened
            listener = urlsplit(dict(parse_qsl(urlsplit(opened).query))["redirect_to"]).port
            await walk_on(rig, app, 60)

            loop = asyncio.get_running_loop()
            began = loop.time()
            await app.stop()
            assert loop.time() - began < CLOSES_WITHIN_S and closed_without_running_out_of_time(caplog)
            assert len(SessionStore(rig.dir).records) == 1
            assert await left_running() == []
            with pytest.raises(OSError):  # nobody listens there any more
                await asyncio.open_connection("127.0.0.1", listener)
            assert rig.keystore.calls == [] and rig.asked() == []

    run(scenario)


class Spy:
    """Something handed to the App in the part's place, that writes down
    what it is told, and what Walka had done by then."""

    def __init__(self, data_dir: Path, *, breaks: tuple[str, ...] = ()) -> None:
        self.dir = Path(data_dir)
        self.breaks = breaks
        self.told: list[tuple] = []
        self.app: Any = None

    def _note(self, what: str) -> None:
        app = self.app
        self.told.append(
            (
                what,
                len(list((self.dir / "sessions").glob("*.json"))),  # walks on disk
                app.recorder is not None,  # a walk still being recorded
                len(app._tasks),  # Walka's own loops
                getattr(app.treadmill, "let_go", 0),
                (self.dir / "config.json").exists(),
            )
        )
        if what in self.breaks:
            raise RuntimeError("https://accounts.example said no to test-key")  # (what no log may quote)

    def attach(self, app: Any) -> None:
        self.app = app
        self.told.append(("attach",))

    def start(self) -> None:
        self._note("start")

    def walk_saved(self) -> None:
        self._note("walk_saved")

    def cancel(self) -> None:
        self._note("cancel")

    async def close(self) -> None:
        await asyncio.sleep(0)
        self._note("close")

    def snapshot(self) -> dict:
        return {"state": "signed_out"}

    def in_account(self, walk_id: str, start: float) -> bool:
        return False


def test_stop_tells_the_part_before_the_walk_is_saved_and_waits_for_it_only_when_everything_is_done(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            spy = Spy(rig.dir)
            app = App(
                a_config(tmp_path),
                rig.dir,
                treadmill=Pad(),
                beacon=BeaconSource(None, clock=rig.clock.mono),
                walka_account=spy,
                wall_clock=rig.clock.wall,
                mono_clock=rig.clock.mono,
            )
            rig.app = app  # type: ignore[assignment]
            assert spy.told == [("attach",)] and spy.app is app
            await app.start()
            assert [told[0] for told in spy.told] == ["attach", "start"]
            assert spy.told[1][3] == 3  # (the loops of Walka's own are made by then)
            await walk_on(rig, app, 60)
            assert len(spy.told) == 2  # (a walk that is still going is nobody's news)

            await app.stop()
            assert [told[0] for told in spy.told] == ["attach", "start", "cancel", "walk_saved", "close"]
            cancel, saved, close = spy.told[2:]
            # cancel(): with the tasks, BEFORE the walk is saved - it is still being recorded, nothing on disk.
            assert cancel[1:] == (0, True, 3, 0, False)
            # The walk saved at exit is told like any other (the real part does nothing with it by then).
            assert saved[1:3] == (1, False)
            # close(): awaited when all of Walka's own is done - the walk and the settings on disk, the
            # loops gone, the treadmill let go of.
            assert close[1:] == (1, False, 0, 1, True)
            assert app.snapshot()["walka_account"] == {"state": "signed_out"}

    run(scenario)


@pytest.mark.parametrize(
    "breaks", [("start",), ("cancel",), ("walk_saved",), ("close",), ("start", "cancel", "walk_saved", "close")]
)
def test_a_part_that_breaks_costs_nobody_their_walk_and_its_words_are_not_in_the_log(tmp_path, caplog, breaks):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            spy = Spy(rig.dir, breaks=breaks)
            app = App(
                a_config(tmp_path),
                rig.dir,
                treadmill=Pad(),
                beacon=BeaconSource(None, clock=rig.clock.mono),
                walka_account=spy,
                wall_clock=rig.clock.wall,
                mono_clock=rig.clock.mono,
            )
            rig.app = app  # type: ignore[assignment]
            await app.start()
            assert len(app._tasks) == 3
            await walk_on(rig, app, 60)
            # An earlier walk could not be written yet (a full disk, say) and waits in the list: closing
            # Walka saves two walks, one after the other - and the part is told of each.
            earlier = rig.walk(3600, add=False)
            app._unsaved.append(earlier)
            await app.stop()
            assert [told[0] for told in spy.told] == ["attach", "start", "cancel", "walk_saved", "walk_saved", "close"]
            first, kept = SessionStore(rig.dir).records
            assert first.id == earlier.id and kept.seconds >= 55 and not (rig.dir / "active-session.json").exists()
            assert app._unsaved == [] and app.treadmill.let_go == 1 and (rig.dir / "config.json").exists()
            assert await left_running() == []

    run(scenario)
    written = "\n".join(record.getMessage() + str(record.exc_info or "") for record in caplog.records)
    assert "accounts.example" not in written and "test-key" not in written and "said no" not in written
    # The kind of error, once for each time it broke (two walks were saved), and no more.
    assert written.count("RuntimeError") == sum(2 if what == "walk_saved" else 1 for what in breaks)
    assert "Traceback" not in caplog.text


def test_after_stop_the_dashboard_still_answers_and_the_part_refuses_and_starts_nothing(tmp_path):
    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            await app.start()
            user = rig.person()
            await rig.sign_in()
            question = await rig.question()
            record = rig.walk(3600)
            requests, store_calls = len(rig.fake.requests), len(rig.keystore.calls)
            written = (rig.dir / ledger.FILE_NAME).read_bytes()
            async with dashboard(app) as client:
                await app.stop()  # (the dashboard's server is closed after it: cli._run)
                await asyncio.sleep(0.05)
                for _ in range(2):
                    for path in ROUTES:
                        for body in ({}, {"way": "github"}, {"question": question["id"], "send": "all"}):
                            assert await said(client, path, body) == (409, CLOSING), (path, body)
                    app.walka_account.walk_saved()
                    app.walka_account.start()
                    await asyncio.sleep(0.02)
                    assert parts_tasks() == [] and rig.account._tasks == set() and rig.account._listeners == set()
                # The page can still be looked at: the snapshot and the walks answer.
                piece = (await shown(client))["walka_account"]
                assert (piece["state"], piece["sending"], piece["question"]) == ("signed_in", False, None)
                (walk,) = await walks_shown(client)
                assert (walk["id"], walk["in_walka_account"]) == (record.id, False)
            assert len(rig.fake.requests) == requests and len(rig.keystore.calls) == store_calls
            assert len(rig.opened) == 1 and rig.fake.walks(user) == []
            assert (rig.dir / ledger.FILE_NAME).read_bytes() == written
            assert rig.account_in_book(user)["sending"] is False

    run(scenario)


def test_recording_goes_on_without_a_gap_while_the_accounts_sit_on_an_answer(tmp_path):
    """Recording a walk never waits for the network: the part has tasks of
    its own, and the App only sets an event."""

    async def scenario():
        async with Rig(tmp_path, start=False) as rig:
            app = hand_in(rig, tmp_path)
            rig.account.start()
            user = rig.person()
            await rig.sign_in()
            await rig.switch_on("all")
            gates = [rig.fake.hold("/rest/v1/walks", method="POST")]
            await walk_on(rig, app, 60, then_stand=True)  # saved: the sender is woken, and its request is held
            await gates[0].arrived.wait()
            await walk_on(rig, app, 120, then_stand=True)  # the next walk, recorded while that answer waits
            first, second = app.store.records
            assert second.seconds == pytest.approx(120, abs=1.0) and len(second.samples) >= 20
            assert rig.fake.walks(user) == []
            gates[0].release()
            await rig.idle()
            assert sorted(row["client_id"] for row in rig.fake.walks(user)) == sorted([first.id, second.id])
            assert len(rig.pushes()) == 2  # (the walk saved during the pass went in the next one)

    run(scenario)


# ---- the command line: when Walka has the part ---------------------------------------------------


class NeverRead(dict):
    """An environment that must not be looked at."""

    def get(self, *_args):  # type: ignore[override]
        raise AssertionError("the environment was read")

    __getitem__ = __contains__ = get  # type: ignore[assignment]


def a_service_role_key() -> str:
    import base64

    def part(thing: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(thing).encode()).rstrip(b"=").decode()

    return ".".join((part({"alg": "HS256", "typ": "JWT"}), part({"role": "service_role", "iss": "supabase"}), "c2ln"))


URL, KEY = "https://accounts.example", "test-key"
BOTH = {URL_VARIABLE: URL, KEY_VARIABLE: KEY}
REFUSED_ADDRESSES = (
    "http://accounts.example",
    "http://localhost:54321",
    "http://127.0.0.1",
    "http://127.0.0.2:54321",
    "https://walker:secret-word@accounts.example",
    "http://127.0.0.1:1@evil.example",
    "https://accounts.example/rest/v1",
    "https://accounts.example?apikey=secret-word",
    "https://accounts.example#secret-word",
    "https://accounts.example evil.example",
    " https://accounts.example",
    "https://accounts.example\n",
    "https://",
    "https:accounts.example",
    "ftp://accounts.example",
    "file:///etc/passwd",
    "accounts.example",
    "//accounts.example",
    "https://аccounts.example",
)
REFUSED_KEYS = (
    "sb_secret_abcdefghijklmnopqrstuvwx",
    a_service_role_key(),
    "two words",
    "k" * 513,
    "schlüssel",
)


@pytest.fixture
def store(monkeypatch) -> CountingKeyStore:
    """The store for secrets that cli would open, in memory. (Without this, conftest.py fails the test.)"""
    made = CountingKeyStore(lasting=True)
    monkeypatch.setattr(keystore, "open_keystore", lambda: made)
    return made


def lines(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "walka"]


def test_from_source_with_both_variables_and_a_real_treadmill_walka_has_the_part(tmp_path, caplog, store):
    for address in (URL, "https://accounts.example/", "https://accounts.example:8443", "http://127.0.0.1:54321"):
        caplog.clear()
        data = tmp_path / str(len(address))
        part = cli._build_walka_account(data, Pad(), {URL_VARIABLE: address, KEY_VARIABLE: KEY, "PATH": "/bin"})
        assert isinstance(part, WalkaAccount)
        assert part.origin.text == address.rstrip("/") and part.key == KEY
        assert part.keys.kind == "memory" and part._opener is cli._open_sign_in_page
        # Made, and nothing more: no file, no folder, no look into the store, nobody asked.
        assert not data.exists() and store.calls == []
        # One line says so, with neither value.
        (line,) = lines(caplog)
        assert line.levelno == logging.INFO and URL_VARIABLE in line.getMessage() and KEY_VARIABLE in line.getMessage()
        assert "accounts.example" not in caplog.text and "54321" not in caplog.text and KEY not in caplog.text
    # By default the environment it reads is the program's own.
    assert cli._build_walka_account(tmp_path, Pad()) is None and lines(caplog) == [line]


@pytest.mark.parametrize("how", ["sys.frozen", "sys._MEIPASS", "both"])
def test_the_packaged_program_has_no_part_whatever_the_environment_says(tmp_path, monkeypatch, caplog, how):
    if how in ("sys.frozen", "both"):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    if how in ("sys._MEIPASS", "both"):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "unpacked"), raising=False)
    assert cli._is_packed()
    assert cli._build_walka_account(tmp_path, Pad(), dict(BOTH)) is None
    # ... and it does not so much as look: nothing is read, nothing is said, no store is opened.
    assert cli._build_walka_account(tmp_path, Pad(), NeverRead(BOTH)) is None
    assert caplog.records == [] and list(tmp_path.iterdir()) == []
    monkeypatch.undo()
    assert not cli._is_packed()


def test_with_neither_variable_there_is_no_part_and_not_a_word(tmp_path, caplog):
    for environ in ({}, {"PATH": "/bin"}, {URL_VARIABLE: "", KEY_VARIABLE: ""}, {"SUPABASE_URL": URL, "SUPABASE_KEY": KEY}):
        assert cli._build_walka_account(tmp_path, Pad(), environ) is None
    assert caplog.records == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "environ",
    [{URL_VARIABLE: URL}, {KEY_VARIABLE: KEY}, {URL_VARIABLE: URL, KEY_VARIABLE: ""}, {URL_VARIABLE: "", KEY_VARIABLE: KEY}],
)
def test_one_variable_alone_is_no_part_and_one_line_that_holds_no_value(tmp_path, caplog, environ):
    assert cli._build_walka_account(tmp_path, Pad(), environ) is None
    (line,) = lines(caplog)
    assert line.levelno == logging.WARNING and URL_VARIABLE in line.getMessage() and KEY_VARIABLE in line.getMessage()
    assert "accounts.example" not in caplog.text and KEY not in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("address", REFUSED_ADDRESSES)
def test_an_address_walka_does_not_take_is_no_part_and_one_line_without_it(tmp_path, caplog, address):
    assert cli._build_walka_account(tmp_path, Pad(), {URL_VARIABLE: address, KEY_VARIABLE: KEY}) is None
    (line,) = lines(caplog)
    assert line.levelno == logging.WARNING and URL_VARIABLE in line.getMessage()
    for value in (address, address.strip(), "accounts.example", "evil.example", "secret-word", "passwd", KEY):
        assert value not in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", REFUSED_KEYS)
def test_a_secret_key_or_one_that_is_none_is_no_part_and_one_line_without_it(tmp_path, caplog, key):
    assert cli._build_walka_account(tmp_path, Pad(), {URL_VARIABLE: URL, KEY_VARIABLE: key}) is None
    (line,) = lines(caplog)
    assert line.levelno == logging.WARNING and KEY_VARIABLE in line.getMessage()
    for value in (key, key[:24], key[:8], key[:5], key[-5:], "accounts.example", "sb_secret", "service_role"):
        assert value not in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_with_the_simulated_treadmill_there_is_no_part(tmp_path, caplog):
    for treadmill in (SimulatedTreadmill(), cli._build_treadmill(Config(), True)):
        caplog.clear()
        assert cli._build_walka_account(tmp_path, treadmill, dict(BOTH)) is None
        (line,) = lines(caplog)
        assert "simulated" in line.getMessage() and line.levelno == logging.INFO
        assert "accounts.example" not in caplog.text and KEY not in caplog.text
    config = Config()
    config.treadmill.kind = "simulated"  # as config.json can say, with neither switch
    assert cli._build_walka_account(tmp_path, cli._build_treadmill(config, False), dict(BOTH)) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("version", "new_enough"),
    [
        ("3.9.5", True),
        ("3.9.6", True),
        ("3.10.0", True),
        ("3.14.5", True),
        ("4.0.0a1", True),
        ("3.9.5.post1", True),
        ("3.9.4", False),
        ("3.9.0", False),
        ("3.9", False),
        ("3.8.6", False),
        ("2.3.10", False),
        ("3", False),
        ("", False),
        ("the newest", False),
    ],
)
def test_an_aiohttp_older_than_the_one_the_client_was_tried_on_is_no_part(
    tmp_path, monkeypatch, caplog, store, version, new_enough
):
    monkeypatch.setattr(aiohttp, "__version__", version)
    part = cli._build_walka_account(tmp_path, Pad(), dict(BOTH))
    assert (part is not None) is new_enough
    (line,) = lines(caplog)
    assert ("aiohttp 3.9.5" in line.getMessage()) is (not new_enough)
    assert "accounts.example" not in caplog.text and KEY not in caplog.text
    assert store.calls == []


def test_a_part_that_cannot_be_made_does_not_keep_walka_from_starting(tmp_path, monkeypatch, caplog):
    def broken():
        raise RuntimeError(f"{URL} would not take {KEY}")

    monkeypatch.setattr(keystore, "open_keystore", broken)
    assert cli._build_walka_account(tmp_path, Pad(), dict(BOTH)) is None
    (line,) = lines(caplog)
    assert "RuntimeError" in line.getMessage() and line.exc_info is None
    assert "accounts.example" not in caplog.text and KEY not in caplog.text


def test_the_address_and_the_key_stand_in_no_file_of_walkas(repo_root: Path):
    """They are read from the environment when Walka starts. (The names of the variables are not the values.)"""
    source = (repo_root / "companion" / "src" / "walka" / "cli.py").read_text("utf-8")
    assert source.count(f'"{URL_VARIABLE}"') == 1 and source.count(f'"{KEY_VARIABLE}"') == 1
    for path in sorted((repo_root / "companion" / "src" / "walka").rglob("*.py")):
        text = path.read_text("utf-8")
        assert "supabase.co" not in text and "sb_publishable_" not in text, path.name
        if path.name != "cli.py":
            assert "WALKA_ACCOUNT_" not in text, path.name
        if path.parent.name == "walka_account" or path.name in ("app.py", "web.py"):
            assert "os.environ" not in text and "getenv" not in text, path.name


NOT_THE_REPOSITORYS = (".git", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", ".venv", "venv", "build", "dist")


def test_nothing_shaped_like_a_real_address_or_a_real_key_stands_anywhere_in_the_repository(repo_root: Path):
    """The live project's address and its key are in no file here - not in
    code, tests, documents or a workflow - until the website has said what
    Walka does with them. (Made-up ones are short, and end in ``.example``.)"""
    shaped = re.compile(rb"sb_publishable_[A-Za-z0-9_-]{20,}|sb_secret_[A-Za-z0-9_-]{30,}|[a-z0-9]{20}\.supabase\.(co|in)\b")
    looked, found = 0, []
    for folder, folders, files in os.walk(repo_root):
        if "pyvenv.cfg" in files:
            folders[:] = []  # somebody's Python environment, whatever its name: other people's libraries
            continue
        folders[:] = [name for name in folders if name not in NOT_THE_REPOSITORYS and not name.endswith(".egg-info")]
        for name in files:
            path = Path(folder) / name
            if path.suffix.lower() in (".png", ".ico", ".icns", ".tga", ".blp", ".ttf", ".pyc", ".exe", ".zip"):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            looked += 1
            if shaped.search(data):
                found.append(str(path.relative_to(repo_root)))
    assert looked > 150 and found == []
    assert (repo_root / ".github" / "workflows" / "ci.yml").is_file()  # (the workflows are among what was looked at)
    # ... and the look would find one of each kind.
    for made_up in (b"https://" + b"a1" * 10 + b".supabase.co", b"sb_publishable_" + b"x" * 31, b"sb_secret_" + b"x" * 31):
        assert shaped.search(made_up)


def test_the_sign_in_page_is_opened_on_a_thread_of_its_own_and_its_address_is_never_logged(monkeypatch, caplog):
    seen: list[tuple[str, bool, str]] = []
    go, done = threading.Event(), threading.Event()

    def slow_browser(url: str) -> bool:
        thread = threading.current_thread()
        seen.append((thread.name, thread.daemon, url))
        go.wait(10)  # a browser that takes its time to come up
        done.set()
        raise OSError(f"no browser for {url}")

    monkeypatch.setattr(webbrowser, "open", slow_browser)
    address = "https://accounts.example/auth/v1/authorize?provider=github&code_challenge=a-secret-challenge"
    cli._open_sign_in_page(address)  # comes back at once: the caller is the loop that records the walk
    assert not done.is_set()
    go.set()
    assert done.wait(10)
    assert seen == [("walka-browser", True, address)] and threading.current_thread().name != "walka-browser"
    assert "accounts.example" not in caplog.text and "a-secret-challenge" not in caplog.text


def test_selfcheck_still_says_one_line_and_imports_every_module_of_the_part(capsys):
    package = Path(cli.__file__).parent / "walka_account"
    modules = {f"walka.walka_account.{path.stem}" for path in package.glob("*.py") if path.stem != "__init__"}
    assert len(modules) >= 9 and modules <= set(cli.RUNTIME_MODULES) and "ssl" in cli.RUNTIME_MODULES
    assert {name for name in cli.RUNTIME_MODULES if name.startswith("walka.walka_account")} == modules
    # In the list for every system: they import everywhere (and a Mac's build wants none that names Windows).
    for platform in ("linux", "win32", "darwin"):
        assert modules <= set(cli.runtime_modules(platform))
    assert not any("windows" in name or "winrt" in name for name in modules)
    assert len(cli.RUNTIME_MODULES) == len(set(cli.RUNTIME_MODULES))
    assert cli.main(["selfcheck"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("selfcheck OK") and f"{len(cli.runtime_modules())} modules" in out[0]


# ---- the command line: Walka itself, run in this process ------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def until_served(session: aiohttp.ClientSession, port: int) -> dict:
    for _ in range(500):
        try:
            async with session.get(f"http://127.0.0.1:{port}/api/state") as response:
                return await response.json()
        except aiohttp.ClientError:
            await asyncio.sleep(0.02)  # the server is not up yet
    raise AssertionError("the dashboard never came up")


class Run:
    """``cli._run`` in this process, as test_cli.py runs it - with what cli builds written down."""

    def __init__(self, tmp_path: Path, monkeypatch, *flags: str, pad: bool) -> None:
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.port = free_port()
        self.treadmills: list[Any] = []
        self.parts: list[Any] = []
        self.stores: list[CountingKeyStore] = []
        build_treadmill, build_part = cli._build_treadmill, cli._build_walka_account

        def treadmill(cfg, simulate):
            # (A real reader would look for a Bluetooth radio: here a pad that is simply not the simulated one.)
            self.treadmills.append(Pad() if pad else build_treadmill(cfg, simulate))
            return self.treadmills[-1]

        def part(*args, **how):
            self.parts.append(build_part(*args, **how))
            return self.parts[-1]

        def open_keystore():
            self.stores.append(CountingKeyStore(lasting=True))
            return self.stores[-1]

        monkeypatch.setattr(cli, "_build_treadmill", treadmill)
        monkeypatch.setattr(cli, "_build_walka_account", part)
        monkeypatch.setattr(keystore, "open_keystore", open_keystore)
        self.args = cli.build_parser().parse_args(
            [*flags, "--no-browser", "--port", str(self.port), "--data-dir", str(self.data)]
        )

    def files_text(self) -> str:
        found = [path for path in sorted(self.data.rglob("*")) if path.is_file()]
        return "\n".join(path.read_bytes().decode("utf-8", "replace") for path in found)


def test_walka_run_from_source_with_both_variables_has_the_part_and_signs_in(tmp_path, monkeypatch, caplog):
    """The whole of it, through cli's own code: the part is built from the
    environment, handed to the App, reached through the dashboard - and the
    sign-in page is opened by cli's own opener, on its own thread. With the
    log kept as ``walka -v`` keeps it: in ``walka.log``, in the data folder."""
    walka = Run(tmp_path, monkeypatch, "--no-capture", pad=True)
    never_in_the_log: list[str] = []

    async def scenario():
        loop = asyncio.get_running_loop()
        opened: asyncio.Queue = asyncio.Queue()
        served: list[str] = []

        def browser(url: str) -> bool:
            thread = threading.current_thread()
            loop.call_soon_threadsafe(opened.put_nowait, (thread.name, thread.daemon, url))
            return True

        monkeypatch.setattr(webbrowser, "open", browser)
        async with FakeAccounts(wall=lambda: NOW) as fake:
            user = fake.add_user("walker@mail.example")
            monkeypatch.setenv(URL_VARIABLE, fake.origin)
            monkeypatch.setenv(KEY_VARIABLE, fake.key)
            task = asyncio.create_task(cli._run(walka.args))
            there = f"http://127.0.0.1:{walka.port}"
            async with aiohttp.ClientSession() as session:
                state = await until_served(session, walka.port)
                (part,) = walka.parts
                assert isinstance(part, WalkaAccount) and part.usable
                piece = state["walka_account"]
                assert (piece["state"], piece["host"], piece["store"]) == ("signed_out", fake.origin[7:], "lasting")
                # Never signed in: Walka is up, and has asked nobody and looked into no store.
                (store,) = walka.stores
                assert fake.requests == [] and store.calls == [] and opened.empty()
                assert not (walka.data / ledger.FILE_NAME).exists()

                async with session.post(f"{there}/api/walka-account/sign-in", json={"way": "github"}) as response:
                    assert (response.status, await response.json()) == (200, {"ok": True})
                name, daemon, url = await asyncio.wait_for(opened.get(), 10)
                assert (name, daemon) == ("walka-browser", True) and url.startswith(fake.origin + "/auth/v1/authorize?")
                status, _headers, page = await fake.browser(url)
                assert status == 200 and "Walka is signed in" in page
                for _ in range(500):
                    piece = (await (await session.get(f"{there}/api/state")).json())["walka_account"]
                    if piece["state"] == "signed_in" and piece["first_step"] != "unknown" and not piece["busy"]:
                        break
                    await asyncio.sleep(0.02)
                assert (piece["state"], piece["email"], piece["first_step"]) == (
                    "signed_in",
                    "walker@mail.example",
                    "taken",
                )
                assert piece["sending"] is False and len(store.secrets()) == 1 and opened.empty()
                # What any program on this computer can read of the dashboard: the snapshot, the settings, the walks.
                for path in ("/api/state", "/api/config", "/api/sessions"):
                    async with session.get(there + path) as response:
                        served.append(await response.text())

            task.cancel()  # what asyncio.run does on Ctrl+C
            assert await asyncio.wait_for(task, 30) == 0
            # The dashboard's own server is closed: nothing is left running but the stand-in.
            assert await left_running() == []
            assert walka.treadmills[0].tripped == [] and walka.treadmills[0].let_go == 1
            # No key of the sign-in stands in a file, in what the dashboard serves, or in the log; the log
            # names neither the accounts nor the person.
            keys = {key for key in (*fake.secrets_issued(), *store.secrets()) if key}
            assert len(keys) >= 3 and [key for key in keys if key in walka.files_text()] == []
            assert len(served) == 3 and "walker@mail.example" in served[0]  # (the e-mail address is shown: decision 10)
            assert [key for key in (*keys, fake.key, user) if key in "\n".join(served)] == []
            assert "walker@mail.example" not in served[1] and of_the_part(names_in(json.loads(served[1]))) == set()
            written = "\n".join(logging.Formatter().format(record) for record in caplog.records)
            never_in_the_log.extend((*keys, fake.origin, fake.origin[7:], fake.key, user, "walker@mail.example", "authorize"))
            for word in never_in_the_log:
                assert word not in written, word
            assert "Walka has its Walka account's part" in written
            assert user in (walka.data / ledger.FILE_NAME).read_text("utf-8")  # (the file is where the id may stand)

    stop_logging = cli._start_logging(True, stream=io.StringIO(), file=walka.data / cli.LOG_FILE)
    try:
        run(scenario, 60)
    finally:
        stop_logging()
    # The file people attach to public issues, as it was written: it says that the part is there, and
    # holds no key, no address of the accounts, and nothing of who signed in.
    kept = "\n".join(path.read_text("utf-8") for path in sorted(walka.data.glob(cli.LOG_FILE + "*")))
    assert "Walka has its Walka account's part" in kept and "is running: dashboard at" in kept
    assert len(never_in_the_log) >= 9 and [word for word in never_in_the_log if word in kept] == []


@pytest.mark.parametrize("how", ["--simulate", "--demo", "config.json", "packed"])
def test_walka_run_has_no_part_with_the_simulated_treadmill_or_packed_whatever_the_environment_says(
    tmp_path, monkeypatch, how
):
    """A made-up walk never reaches a Walka account: all three ways to the
    simulated treadmill, through cli's own code - and the packaged program."""
    flags = {"--simulate": ["--simulate", "--no-capture"], "--demo": ["--demo"]}.get(how, ["--no-capture"])
    walka = Run(tmp_path, monkeypatch, *flags, pad=how == "packed")
    if how == "config.json":
        (walka.data / "config.json").write_text(json.dumps({"treadmill": {"kind": "simulated"}}), "utf-8")
    if how == "packed":
        monkeypatch.setattr(sys, "frozen", True, raising=False)

    async def scenario():
        async with FakeAccounts(wall=lambda: NOW) as fake:
            fake.add_user("walker@mail.example")
            monkeypatch.setenv(URL_VARIABLE, fake.origin)
            monkeypatch.setenv(KEY_VARIABLE, fake.key)
            task = asyncio.create_task(cli._run(walka.args))
            there = f"http://127.0.0.1:{walka.port}"
            async with aiohttp.ClientSession() as session:
                state = await until_served(session, walka.port)
                assert walka.parts == [None] and "walka_account" not in state
                assert state["treadmill"]["kind"] == ("ftms" if how == "packed" else "simulated")
                for path in ROUTES:
                    for body in ({}, {"way": "github"}, {"question": "q", "send": "all"}):
                        async with session.post(there + path, json=body) as response:
                            assert (response.status, await response.json()) == (404, NOT_THERE), (path, body)
                async with session.get(f"{there}/api/sessions") as response:
                    assert all("in_walka_account" not in walk for walk in (await response.json())["sessions"])
            task.cancel()
            assert await asyncio.wait_for(task, 10) == 0
            assert fake.requests == [] and walka.stores == []
            assert not (walka.data / ledger.FILE_NAME).exists()

    run(scenario, 60)


# ---- conftest.py: what no test may reach by accident -----------------------------------------------


def test_the_variables_of_whoever_runs_the_tests_are_taken_away():
    assert URL_VARIABLE not in os.environ and KEY_VARIABLE not in os.environ
    assert cli._build_walka_account(Path("nowhere"), Pad()) is None


def test_the_real_store_for_secrets_and_the_real_browser_fail_a_test(_no_real_keystore_and_no_real_browser):
    reached = _no_real_keystore_and_no_real_browser
    with pytest.raises(AssertionError, match="store for secrets"):
        keystore.open_keystore()
    with pytest.raises(AssertionError, match="web browser"):
        webbrowser.open("http://127.0.0.1:9/")
    # ... and again when the test is over, for the places where an error is swallowed:
    assert cli._build_walka_account(Path("nowhere"), Pad(), dict(BOTH)) is None
    cli._open_browser("http://127.0.0.1:9/")
    assert len(reached) == 4
    reached.clear()  # (or this test would fail, as it should)


INNER_TESTS = '''
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import pytest

from walka import cli
from walka.walka_account import keystore

BOTH = {"WALKA_ACCOUNT_URL": "https://accounts.example", "WALKA_ACCOUNT_KEY": "test-key"}


def test_that_reaches_nothing():
    assert cli._build_walka_account(Path("nowhere"), SimpleNamespace(kind="ftms"), {}) is None


def test_whose_walka_swallows_the_browsers_error():
    cli._open_browser("http://127.0.0.1:9/")


def test_whose_walka_goes_on_without_the_part():
    assert cli._build_walka_account(Path("nowhere"), SimpleNamespace(kind="ftms"), dict(BOTH)) is None


@pytest.mark.real_keystore
def test_that_is_marked_for_the_store():
    assert keystore.open_keystore("linux").kind == "memory"


@pytest.mark.real_browser
def test_that_is_marked_for_the_browser():
    assert webbrowser.open.__module__ == "webbrowser"
'''


def test_a_test_that_reached_them_fails_though_walka_swallowed_the_error(tmp_path):
    """The look at the end of each test (conftest.py), tried on tests of its
    own in a pytest of their own: two of the five reach what they must not,
    in places where Walka goes on regardless."""
    (tmp_path / "test_inner.py").write_text(INNER_TESTS, "utf-8")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(HERE), str(SRC))))
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest", "-rE", "test_inner.py"]
    done = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=300)
    assert done.returncode == 1, done.stdout + done.stderr
    # (The last line counts them; another pytest may count a warning in between.)
    assert re.search(r"\b5 passed\b", done.stdout) and re.search(r"\b2 errors\b", done.stdout), done.stdout
    assert not re.search(r"\b\d+ failed\b", done.stdout), done.stdout
    failed = sorted(line.split("::")[1].split(" ")[0] for line in done.stdout.splitlines() if line.startswith("ERROR "))
    assert failed == ["test_whose_walka_goes_on_without_the_part", "test_whose_walka_swallows_the_browsers_error"]
    assert "PytestUnknownMarkWarning" not in done.stdout


@pytest.mark.real_keystore
@pytest.mark.real_browser
def test_a_test_that_is_marked_for_them_gets_the_real_ones():
    assert keystore.open_keystore.__module__ == "walka.walka_account.keystore"
    assert keystore.open_keystore("linux").kind == "memory"  # (nothing of this computer's is opened by that)
    assert webbrowser.open.__module__ == "webbrowser"


def test_app_module_reads_no_environment_and_says_nothing_of_the_account_in_the_live_view():
    source = Path(app_module.__file__).read_text("utf-8")
    assert "environ" not in source
    for line in source.splitlines():
        if "log_event(" in line:
            assert "account" not in line.lower()
