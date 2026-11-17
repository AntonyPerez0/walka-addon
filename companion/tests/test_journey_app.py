"""The journey numbers in the running companion: wired to the beacon, served
to the dashboard, saved with the companion's state, made up by the demo -
and never part of what the belt is driven by."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from test_journey import JAINA, NOW, THRALL, Clock, journey_of
from walka import cli, journey, web
from walka.app import TICK_S, App
from walka.config import Config
from walka.governor import Phase
from walka.journey import DemoJourney, JourneyBook, JourneyCollector
from walka.protocol import STAT_CYCLE, STAT_EVERY, Flags, Frame, Stat, StatFrame, kmh_to_raw
from walka.sources import BeaconSource
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


def send_round(rig: Rig, values: dict[Stat, int], *, close: bool = True) -> None:
    """Show one round of stat frames on the strip (and the key that closes it)."""
    frames = list(values.items())
    if close:
        frames.append((Stat.CHAR_KEY, values[Stat.CHAR_KEY]))
    for stat, value in frames:
        rig.clock.advance(0.5)
        rig.seq = (rig.seq + 1) % 256
        rig.beacon.inject(StatFrame(seq=rig.seq, stat_id=stat, value=value))


# ---- wiring ----------------------------------------------------------------------------------------


def test_the_app_collects_stat_frames_from_its_beacon(rig: Rig):
    assert rig.app.snapshot()["journey"] == {"rev": 0, "characters": 0, "demo": False}
    send_round(rig, THRALL, close=False)
    assert rig.app.snapshot()["journey"]["characters"] == 0  # not before the round is closed
    send_round(rig, THRALL)
    assert rig.app.snapshot()["journey"] == {"rev": 2, "characters": 1, "demo": False}
    (found,) = rig.app.journeys.journeys()
    assert (found.name, found.level, found.real_steps) == ("Thrall", 17, 105_937)
    assert found.seen_at == rig.clock.wall  # the app's own clock, not the computer's


def test_an_app_without_a_beacon_still_has_a_book(tmp_path: Path):
    app = App(Config(), tmp_path, treadmill=SimulatedTreadmill(auto_run=False), beacon=None)
    assert app.snapshot()["journey"] == {"rev": 0, "characters": 0, "demo": False}
    assert app.journeys.to_api()["characters"] == []


# ---- the guard: a stat frame is never part of the live reading ---------------------------------------


def belt_view(rig: Rig) -> dict:
    """Everything that decides, or shows, what the belt does."""
    snapshot = rig.app.snapshot()
    return {
        "reading": rig.app._last_reading,
        "frame": rig.beacon.frame,
        "beacon": rig.beacon.status.to_dict(),
        "governor": snapshot["governor"],
        "source": snapshot["source"],
        "belt": (rig.treadmill.status.state, rig.treadmill.status.speed_kmh),
        "session": snapshot["session"],
        "recorder": None if rig.app.recorder is None else rig.app.recorder.beacon_session,
    }


def test_stat_frames_never_change_what_the_belt_does(tmp_path: Path):
    """Two companions get the same live frames. One of them also gets stat
    frames - before, between and instead of the live ones. At every tick the
    two must agree on everything about the belt."""
    plain, mixed = Rig(tmp_path / "plain"), Rig(tmp_path / "mixed")
    rounds = [*THRALL.items(), *THRALL.items()]
    phases: set[Phase] = set()
    sent = 0

    def stat_frame(seq: int) -> StatFrame:
        nonlocal sent
        stat, value = rounds[sent % len(rounds)]
        sent += 1
        return StatFrame(seq=seq % 256, stat_id=stat, value=value)

    async def step(live: Frame | None, *, stats_before: int = 0, stats_after: int = 0) -> None:
        for rig in (plain, mixed):
            rig.clock.advance(TICK_S)
        seq = live.seq if live is not None else sent
        for i in range(stats_before):
            mixed.beacon.inject(stat_frame(seq + 100 + i))
        if live is not None:
            plain.beacon.inject(live)
            mixed.beacon.inject(live)
        for _ in range(stats_after):
            mixed.beacon.inject(stat_frame(seq))  # also with the very seq of the live frame
        for rig in (plain, mixed):
            await rig.app.tick()
            rig.treadmill.advance(TICK_S)
        assert belt_view(mixed) == belt_view(plain)
        phases.add(mixed.app.governor.phase)

    def live(seq: int, *, active: bool = True, target: float = 3.2, moving: bool = True) -> Frame:
        flags = (Flags.ACTIVE if active else Flags(0)) | (Flags.MOVING if moving else Flags(0))
        return Frame(seq=seq % 256, session=1 if active else 0, flags=flags, target_raw=kmh_to_raw(target))

    async def scenario():
        await plain.connect()
        await mixed.connect()
        seq = 0
        for _ in range(10):  # standing in the world, no session
            seq += 1
            await step(live(seq, active=False), stats_before=1, stats_after=1)
        for i in range(300):  # a session: the belt starts and follows
            seq += 1
            await step(live(seq, target=3.2 if i < 150 else 2.4), stats_before=i % 2, stats_after=1)
        assert mixed.treadmill.status.speed_kmh > 2.0
        # The live frames stop. One companion sees nothing at all, the other
        # a healthy stream of stat frames: to the belt that is the same.
        # (Long enough for the whole of it: hold, slow down, and at last pause.)
        for _ in range(700):
            await step(None, stats_before=1, stats_after=2)
        assert mixed.treadmill.status.speed_kmh == 0.0
        for i in range(100):  # the signal is back
            seq += 1
            await step(live(seq, moving=i > 20), stats_after=1)

    asyncio.run(scenario())
    # The run really went through a start and every stage of a lost signal ...
    assert {Phase.IDLE, Phase.FOLLOWING, Phase.HOLDING, Phase.SAFE} <= phases and len(phases) >= 5
    # ... and the stat frames really arrived, and were collected.
    assert sent > 1000
    assert [found.name for found in mixed.app.journeys.journeys()] == ["Thrall"]
    assert plain.app.journeys.journeys() == []
    assert mixed.beacon.status.frames == plain.beacon.status.frames


def test_a_stream_of_stat_frames_alone_starts_nothing(rig: Rig):
    async def scenario():
        await rig.connect()
        for _ in range(4):
            send_round(rig, THRALL)
            await rig.app.tick()
        assert rig.app.governor.phase is Phase.IDLE
        assert rig.treadmill.status.speed_kmh == 0.0
        assert rig.app.recorder is None
        assert rig.beacon.frame is None and rig.app.source_kind() == "none"

    asyncio.run(scenario())
    assert len(rig.app.journeys.journeys()) == 1


def test_the_journey_code_has_no_way_to_the_belt():
    """journey.py is given numbers and nothing else: it must not even import
    the parts that drive the treadmill."""
    source = Path(journey.__file__).read_text("utf-8")
    for module in ("governor", "treadmill", "sources", "app", "session", "ftms"):
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

        send_round(rig, THRALL)
        send_round(rig, JAINA)
        data = await (await client.get("/api/journey")).json()
        assert data["rev"] == 2 and data["demo"] is False
        assert [entry["name"] for entry in data["characters"]] == ["Jaina", "Thrall"]
        jaina, thrall = data["characters"]
        assert data["current"] == jaina["id"] and jaina["live"] and thrall["live"]
        assert (thrall["level"], thrall["max_level"], thrall["steps"], thrall["character_steps"]) == (
            17,
            60,
            105_937,
            93_210,
        )
        assert jaina["estimated"] is True and thrall["estimated"] is False
        # The state snapshot only says that there is something new to fetch.
        state = await (await client.get("/api/state")).json()
        assert state["journey"] == {"rev": 2, "characters": 2, "demo": False}

        rig.clock.advance(3600)  # the game was closed an hour ago
        data = await (await client.get("/api/journey")).json()
        assert data["current"] is None
        assert [entry["live"] for entry in data["characters"]] == [False, False]

        # Like every other route it only answers this computer, and only to a GET.
        assert (await client.get("/api/journey", headers={"Host": "evil.example.com"})).status == 403
        refused = await client.post("/api/journey", data="{}", headers={"Content-Type": "application/json"})
        assert refused.status == 405

    serve(rig, scenario)


def test_a_name_with_markup_is_served_as_the_text_it_is(rig: Rig):
    """The name comes off the screen. The API hands it over as a JSON string;
    the page only ever writes it as text (see test_share_page.py)."""
    values = dict(THRALL)
    raw = b'<b onclick="x()">&amp;'.ljust(20, b"\0")
    for i, stat in enumerate((Stat.NAME_0, Stat.NAME_1, Stat.NAME_2, Stat.NAME_3)):
        values[stat] = int.from_bytes(raw[i * 5 : i * 5 + 5], "big")

    async def scenario(client: TestClient):
        send_round(rig, values)
        response = await client.get("/api/journey")
        assert response.content_type == "application/json"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        data = json.loads(await response.text())
        assert data["characters"][0]["name"] == '<b onclick="x()">&amp;'[:20]

    serve(rig, scenario)


# ---- kept with the companion's state ------------------------------------------------------------------


def test_the_cache_is_written_on_exit_and_read_at_the_next_start(tmp_path: Path):
    rig = Rig(tmp_path)
    send_round(rig, THRALL)

    async def scenario():
        await rig.app.start()
        names = {task.get_name() for task in asyncio.all_tasks()}
        assert "walka-journey" in names  # the periodic saver
        await rig.app.stop()
        await asyncio.sleep(0.05)
        assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []

    asyncio.run(asyncio.wait_for(scenario(), 20))
    path = tmp_path / "data" / journey.JOURNEY_FILE
    assert path.exists() and (tmp_path / "data" / "state.json").parent == path.parent

    # The next day, with the game closed: the character is still there.
    later = App(
        Config(),
        tmp_path / "data",
        treadmill=SimulatedTreadmill(auto_run=False),
        beacon=BeaconSource(None),
        wall_clock=lambda: rig.clock.wall + 86400,
    )
    data = later.journeys.to_api()
    assert [(entry["name"], entry["steps"], entry["live"]) for entry in data["characters"]] == [
        ("Thrall", 105_937, False)
    ]
    assert data["current"] is None
    assert later.snapshot()["journey"]["characters"] == 1


# ---- the demo -----------------------------------------------------------------------------------------


def test_the_demo_sends_stat_frames_the_way_the_addon_does():
    demo = DemoJourney(wall=lambda: NOW)
    frames = [demo.frame(seq, seq / 10) for seq in range(1, 1 + STAT_EVERY * len(STAT_CYCLE) * 2)]
    stat_turns = [i for i, frame in enumerate(frames, start=1) if frame is not None]
    assert stat_turns == list(range(STAT_EVERY, len(frames) + 1, STAT_EVERY))  # every 5th redraw, never two in a row
    stats = [frame for frame in frames if frame is not None]
    assert all(isinstance(frame, StatFrame) for frame in stats)
    assert [frame.stat for frame in stats[: len(STAT_CYCLE)]] == list(STAT_CYCLE)
    assert [frame.stat for frame in stats[len(STAT_CYCLE) :]] == list(STAT_CYCLE)[: len(stats) - len(STAT_CYCLE)]
    assert [frame.seq for frame in stats] == stat_turns  # the seq it was given
    for frame in stats:
        frame.pack()  # every value fits its field


def test_the_demo_character_is_plausible_and_moves():
    clock = Clock()
    demo = DemoJourney(wall=clock.wall)
    published = []
    collector = JourneyCollector(published.append, wall=clock.wall, mono=clock.mono)
    seq = 0
    while len(published) < 2:
        seq += 1
        clock.advance(0.1)
        frame = demo.frame(seq, clock.mono_now - 500.0)
        if frame is not None:
            collector.on_stat(frame.stat, frame.value)
        assert seq < 1000, "the demo never completed a round"
    first, second = published
    assert first.name == journey.DEMO_NAME and first.key == second.key
    assert (first.level, first.max_level, first.faction) == (23, 60, "alliance")
    assert 1 <= first.class_id <= 13
    assert 20 * 3600 < first.played_s < 60 * 3600
    assert 0 < first.foot_yd < first.travel_yd
    assert 0.4 < first.real_m / first.real_steps < 0.9  # a believable stride
    assert 1.5 < (first.real_m / 1000) / (first.real_s / 3600) < 5.0  # ... and walking pace, km/h
    assert 0.02 < first.real_kcal / first.real_steps < 0.08
    assert first.walks > 0 and not first.estimated
    assert NOW - 60 * 86400 < first.since < NOW
    # While the demo runs the numbers go up, as they would in the game.
    assert second.played_s > first.played_s and second.foot_yd > first.foot_yd
    assert second.real_steps > first.real_steps and second.travel_yd > first.travel_yd


def test_the_scripted_demo_feeds_the_journey_book(monkeypatch):
    """cli._demo_character, with its clock sped up."""
    monkeypatch.setattr(cli, "DEMO_REDRAW_S", 0.0)
    source = BeaconSource(None)
    book = JourneyBook(None)
    source.on_stat = book.collector.on_stat

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(cli._demo_character(source, stop))
        for _ in range(100_000):
            await asyncio.sleep(0)
            if book.journeys():
                break
        stop.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(asyncio.wait_for(scenario(), 30))
    (found,) = book.journeys()
    assert found.name == journey.DEMO_NAME
    # Live frames were shown in between: four for every stat frame.
    assert source.frame is not None
    assert source.status.frames >= 4 * len(STAT_CYCLE)


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

    monkeypatch.setattr(cli, "DEMO_REDRAW_S", 0.005)
    monkeypatch.setattr(cli, "DEMO_SESSION_STARTS_AFTER_S", 3600.0)  # no walking session: this is about the numbers
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
