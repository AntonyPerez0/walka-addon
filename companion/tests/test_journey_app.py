"""The journeys in the app: read from the game's saved file, kept in the
book, served to the Share page - and never part of what is recorded."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import ME, Rig, game_db
from test_journey import NOW, journey_of, record_of
from walka import cli, journey, web
from walka.app import App
from walka.config import Config
from walka.journey import JourneyBook
from walka.treadmill.simulated import SimulatedTreadmill


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def serve(rig: Rig, scenario):
    """Run ``scenario(client)`` against the dashboard server."""

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            return await scenario(client)

    return asyncio.run(asyncio.wait_for(main(), 30))


def journeys(rig: Rig, **records: dict) -> dict:
    """``WalkaDB.journeys``, stamped as saved a moment before the game wrote the file."""
    for record in records.values():
        record["shared"]["savedAt"] = rig.clock.wall - 40
    return records


# ---- wiring ----------------------------------------------------------------------------------------


def test_the_app_takes_the_journeys_from_the_saved_file(rig: Rig):
    assert rig.app.snapshot()["journey"] == {"rev": 0, "characters": 0, "demo": False}
    assert rig.game_saves(game_db(journeys=journeys(rig, **{ME: record_of()})))
    assert rig.app.snapshot()["journey"] == {"rev": 1, "characters": 1, "demo": False}
    (found,) = rig.app.journeys.journeys()
    assert (found.name, found.level, found.real_steps) == ("Tester", 17, 105_937)
    assert found.seen_at == rig.clock.wall - 40
    # The same file again is no news.
    rig.app.game.scan(force=True)
    assert not rig.app.take_game_file() and rig.app.snapshot()["journey"]["rev"] == 1


def test_an_app_with_no_game_still_has_a_book(tmp_path: Path):
    app = App(Config(), tmp_path, treadmill=SimulatedTreadmill(auto_run=False))
    assert app.snapshot()["journey"] == {"rev": 0, "characters": 0, "demo": False}
    assert app.journeys.to_api()["characters"] == []


def test_journeys_never_change_what_is_recorded(tmp_path: Path):
    """Two companions, the same walk on their pads. One of them also reads a
    game file full of journeys, again and again. They record the same walk."""
    plain, mixed = Rig(tmp_path / "plain"), Rig(tmp_path / "mixed")

    async def scenario():
        for rig in (plain, mixed):
            await rig.connect()
            await rig.walk()
        for minute in range(10):
            mixed.game_saves(game_db(journeys=journeys(mixed, **{f"Alt{minute}-Realm": record_of(), ME: record_of()})))
            for rig in (plain, mixed):
                await rig.run(60)
        for rig in (plain, mixed):
            await rig.step_off()

    asyncio.run(scenario())
    assert len(mixed.app.journeys.journeys()) == 11
    walks = [[(r.source, r.game_session, r.seconds, r.steps) for r in rig.app.store.records] for rig in (plain, mixed)]
    assert walks[0] == walks[1] and len(walks[0]) == 1


def test_the_journey_code_is_given_numbers_and_nothing_else():
    """journey.py must not even import the parts that listen to the
    treadmill or record the walk."""
    source = Path(journey.__file__).read_text("utf-8")
    for module in ("treadmill", "app", "session", "ftms", "game_file"):
        assert f"from .{module}" not in source and f"import {module}" not in source, module


# ---- the API ----------------------------------------------------------------------------------------


def test_the_journey_endpoint(rig: Rig):
    async def scenario(client: TestClient):
        response = await client.get("/api/journey")
        assert response.status == 200 and response.content_type == "application/json"
        assert await response.json() == {
            "rev": 0,
            "demo": False,
            "current": None,
            "characters": [],
            "steps_per_mile": 2000,
        }

        thrall, jaina = record_of(), record_of(estimated=True)
        rig.game_saves(game_db(journeys=journeys(rig, **{"Thrall-Durotar": thrall, "Jaina-Theramore": jaina})))
        jaina["shared"]["savedAt"] = rig.clock.wall - 5  # Jaina was played last
        rig.clock.advance(60)
        rig.game_saves(game_db(journeys={"Thrall-Durotar": thrall, "Jaina-Theramore": jaina}))
        data = await (await client.get("/api/journey")).json()
        assert data["demo"] is False
        assert [entry["name"] for entry in data["characters"]] == ["Jaina", "Thrall"]
        jaina_api, thrall_api = data["characters"]
        assert data["current"] == jaina_api["id"]
        assert (thrall_api["level"], thrall_api["max_level"], thrall_api["steps"], thrall_api["character_steps"]) == (
            17,
            60,
            105_937,
            93_210,
        )
        assert jaina_api["estimated"] is True and thrall_api["estimated"] is False
        # The state snapshot only says that there is something new to fetch.
        state = await (await client.get("/api/state")).json()
        assert state["journey"] == {"rev": data["rev"], "characters": 2, "demo": False}

        # Like every other route it only answers this computer, and only to a GET.
        assert (await client.get("/api/journey", headers={"Host": "evil.example.com"})).status == 403
        refused = await client.post("/api/journey", data="{}", headers={"Content-Type": "application/json"})
        assert refused.status == 405

    serve(rig, scenario)


def test_a_name_with_markup_is_served_as_the_text_it_is(rig: Rig):
    """The name comes from a file. The API hands it over as a JSON string;
    the page only ever writes it as text (see test_share_page.py)."""

    async def scenario(client: TestClient):
        rig.game_saves(game_db(journeys=journeys(rig, **{'<b onclick="x()">&amp;-Realm': record_of()})))
        response = await client.get("/api/journey")
        assert response.content_type == "application/json"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        data = json.loads(await response.text())
        assert data["characters"][0]["name"] == '<b onclick="x()">&amp;'

    serve(rig, scenario)


# ---- kept with the companion's state ------------------------------------------------------------------


def test_the_cache_is_written_on_exit_and_read_at_the_next_start(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.game_saves(game_db(journeys=journeys(rig, **{ME: record_of()})))

    async def scenario():
        await rig.app.start()
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert {"walka-journey", "walka-game-file"} <= names  # the periodic saver, and the look at the game's file
        await rig.app.stop()
        await asyncio.sleep(0.05)
        assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []

    asyncio.run(asyncio.wait_for(scenario(), 20))
    path = tmp_path / "data" / journey.JOURNEY_FILE
    assert path.exists() and (tmp_path / "data" / "state.json").parent == path.parent

    # The next day, with the game uninstalled: the character is still there.
    later = App(
        Config(),
        tmp_path / "data",
        treadmill=SimulatedTreadmill(auto_run=False),
        wall_clock=lambda: rig.clock.wall + 86400,
    )
    data = later.journeys.to_api()
    assert [(entry["name"], entry["steps"]) for entry in data["characters"]] == [("Tester", 105_937)]
    assert later.snapshot()["journey"]["characters"] == 1


# ---- the demo -----------------------------------------------------------------------------------------


def test_the_scripted_demo_saves_a_game_that_files_the_walk(tmp_path: Path, monkeypatch):
    """cli._demo_game: a made-up game "saves its file" now and then - a
    running session and a made-up character - and the walk is filed under it."""
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 0.0)
    monkeypatch.setattr(cli, "DEMO_SAVES_EVERY_S", 0.01)
    rig = Rig(tmp_path)
    rig.app.journeys.use_memory_only(demo=True)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(cli._demo_game(rig.app, stop))
        await asyncio.sleep(0.05)
        assert [found.name for found in rig.app.journeys.journeys()] == [journey.DEMO_NAME]
        assert rig.app.snapshot()["game"]["demo"] is True
        stop.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(asyncio.wait_for(scenario(), 30))
    (session,) = rig.app.game.sessions()
    assert session.character == journey.DEMO_NAME and session.running


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_walka_demo_serves_a_made_up_character_and_keeps_it_off_the_disk(tmp_path: Path, monkeypatch):
    """``walka run --demo``: the Share page has a character to try - and the
    user's real, remembered characters are neither shown nor overwritten."""
    real = JourneyBook(tmp_path / journey.JOURNEY_FILE)
    real.add(journey_of("Thrall", NOW))
    real.save()
    before = (tmp_path / journey.JOURNEY_FILE).read_bytes()

    port = free_port()
    args = cli.build_parser().parse_args(["--demo", "--no-browser", "--port", str(port), "--data-dir", str(tmp_path)])

    async def scenario():
        task = asyncio.create_task(cli._run(args))
        data = None
        async with aiohttp.ClientSession() as session:
            for _ in range(600):
                await asyncio.sleep(0.05)
                try:
                    async with session.get(f"http://127.0.0.1:{port}/api/journey") as response:
                        data = await response.json()
                except aiohttp.ClientError:
                    continue  # the server is not up yet
                if data["characters"]:
                    break
        task.cancel()
        assert await asyncio.wait_for(task, 10) == 0
        return data

    data = asyncio.run(asyncio.wait_for(scenario(), 60))
    assert data is not None and data["demo"] is True
    assert [entry["name"] for entry in data["characters"]] == [journey.DEMO_NAME]
    assert data["current"] == data["characters"][0]["id"]
    assert (tmp_path / journey.JOURNEY_FILE).read_bytes() == before
