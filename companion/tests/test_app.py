"""End-to-end: a simulated treadmill and the game's saved file -> recorded
walk, filed under its game session -> data file for the addon. Time is
fully simulated.

The belt is worked by the walker - ``rig.treadmill.press_*`` is the pad's
remote - and by nobody else. Whatever the game saved, the companion's part
is to listen and to write down."""

from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path

import pytest

from walka import addon_sync
from walka import app as app_module
from walka.app import FREE_WALK_GRACE_S, TICK_S, App
from walka.config import Config
from walka.session import SessionStore
from walka.treadmill import BeltState, TreadmillEvent
from walka.treadmill.simulated import SimulatedTreadmill

MPH = 1.609344


class Clock:
    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_800_000_000.0

    def advance(self, dt: float) -> None:
        self.mono += dt
        self.wall += dt


ME = "Tester-Azeroth"


def game_db(sessions=(), *, active=None, journeys=None) -> dict:
    """``WalkaDB`` as the addon saves it. ``sessions``: (start, end, number,
    character) of finished sessions; ``active``: (start, saved at, number,
    character) of the one still running when the game saved."""
    db: dict = {
        "version": 1,
        "sessions": [
            {"id": number, "start": start, "end": end, "seconds": end - start, "distanceM": 0, "steps": 0,
             "kcal": 0, "yards": 100, "maxKmh": 0, "character": character, "auto": True}
            for start, end, number, character in sessions
        ],
    }  # fmt: skip
    if active is not None:
        start, saved_at, number, character = active
        db["active"] = {
            "savedAt": saved_at,
            "session": {"id": number, "start": start, "seconds": saved_at - start, "distanceM": 0, "steps": 0,
                        "kcal": 0, "yards": 0, "character": character, "auto": True},
        }  # fmt: skip
    if journeys is not None:
        db["journeys"] = journeys
    return db


class Rig:
    def __init__(self, tmp_path: Path, *, us_firmware: bool = True) -> None:
        self.clock = Clock()
        self.config = Config()
        self.treadmill = SimulatedTreadmill(us_firmware=us_firmware, clock=lambda: self.clock.mono, auto_run=False)
        # A game folder: <game>/Interface/AddOns/Walka and <game>/WTF
        self.game_dir = tmp_path / "World of Warcraft" / "_forever_"
        self.addon = self.game_dir / "Interface" / "AddOns" / "Walka"
        self.addon.mkdir(parents=True)
        (self.addon / "Walka.toc").write_text("## Interface: 16001\n")
        self.saved_file = self.game_dir / "WTF" / "Account" / "ACCOUNT" / "SavedVariables" / "Walka.lua"
        self.config.wow.addon_dir = str(self.addon)
        self.app = App(
            self.config,
            tmp_path / "data",
            treadmill=self.treadmill,
            wall_clock=lambda: self.clock.wall,
            mono_clock=lambda: self.clock.mono,
        )

    async def connect(self) -> None:
        await self.treadmill.connect()

    async def run(self, seconds: float) -> None:
        """Advance simulated time."""
        for _ in range(int(round(seconds / TICK_S))):
            self.clock.advance(TICK_S)
            await self.app.tick()
            self.treadmill.advance(TICK_S)
        await self.app.synced()  # (the addon's file is written on a worker thread)

    async def walk(self, kmh: float = 3.2) -> None:
        """The walker presses start on the remote, waits out the countdown
        and sets a speed. (Six seconds pass.)"""
        self.treadmill.press_start()
        await self.run(4)
        self.treadmill.press_speed(kmh)
        await self.run(2)

    async def step_off(self) -> None:
        """The walker stops the belt and the walk ends a minute later."""
        self.treadmill.press_stop()
        await self.run(FREE_WALK_GRACE_S + 5)

    def game_saves(self, db: dict) -> bool:
        """The game writes the addon's saved file (a logout or /reload) and
        Walka next looks at it. True if anything was new."""
        self.saved_file.parent.mkdir(parents=True, exist_ok=True)
        self.saved_file.write_text(
            "\r\nWalkaDB = " + addon_sync.to_lua(db) + "\r\n", "utf-8", newline=""
        )
        written = self.clock.wall - 30  # (settled: written a while ago)
        os.utime(self.saved_file, (written, written))
        self.app.game.scan(force=True)
        return self.app.take_game_file()


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def run(coro):
    return asyncio.run(coro)


def load_companion_data(rig: Rig):
    from lupa.lua51 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute((rig.addon / "CompanionData.lua").read_text("utf-8"))
    return lua.globals().WalkaCompanionData


def drop_link(rig: Rig) -> float:
    """The Bluetooth link dies. Like the real reader, the status then says
    "not connected, speed unknown (0)". Returns the speed the belt had."""
    status = rig.treadmill.status
    physical = status.speed_kmh
    status.connected = False
    status.speed_kmh = 0.0
    rig.treadmill._emit(TreadmillEvent.DISCONNECTED)
    return physical


def restore_link(rig: Rig, speed_kmh: float) -> None:
    status = rig.treadmill.status
    status.connected = True
    status.speed_kmh = speed_kmh
    status.updated_at = rig.clock.mono
    rig.treadmill._emit(TreadmillEvent.CONNECTED)


def belt(treadmill: SimulatedTreadmill) -> tuple:
    status = treadmill.status
    return (status.state, round(status.speed_kmh, 6), status.target_kmh, status.distance_m, status.steps)


# ---- a walk ---------------------------------------------------------------------------------


def test_full_walk_is_recorded_and_synced(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(2)
        assert rig.app.recorder is None
        started = rig.clock.wall
        rig.treadmill.press_start()  # start on the pad's remote
        await rig.run(4)
        rig.treadmill.press_speed(2.0 * MPH)
        await rig.run(600)  # ten minutes at 2 mph
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        live = rig.app.snapshot()["session"]
        # Which game session it is in is not known while it runs.
        assert live["source"] == "free" and live["seconds"] > 500
        await rig.step_off()
        return started

    started = run(scenario())
    records = rig.app.store.records
    assert len(records) == 1
    record = records[0]
    assert record.source == "free" and record.game_session is None
    assert 560 <= record.seconds <= 610
    # ~3.2 km/h for ~10 minutes
    assert 480 <= record.distance_m <= 560
    assert 800 <= record.steps <= 1000
    assert 25 <= record.kcal <= 60
    assert record.steps_source == "treadmill"
    # The player logs out: the game saves the session the walk was in.
    assert rig.game_saves(game_db([(started - 300, rig.clock.wall, 1, ME)]))
    assert (record.source, record.game_session, record.character) == ("wow", 1, "Tester")
    assert SessionStore(rig.app.data_dir).records[0].source == "wow", "filed on disk too"
    # The addon data file was written and is valid Lua.
    data_file = rig.addon / "CompanionData.lua"
    text = data_file.read_text("utf-8")
    assert text.count("WalkaCompanionData = {") == 1
    data = load_companion_data(rig)
    assert data.version == addon_sync.DATA_VERSION
    assert data.sessions[1].steps == record.steps
    assert data.sessions[1].source == "wow" and data.sessions[1].gameSession == 1


def test_a_walk_with_no_game_is_recorded_all_the_same(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 30)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["free"]
    assert rig.app.store.records[0].game_session is None
    assert rig.app.snapshot()["game"]["files"] == 0


def test_a_short_stop_does_not_split_a_walk_and_a_long_one_does(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        rig.treadmill.press_pause()  # the doorbell
        await rig.run(FREE_WALK_GRACE_S - 20)
        rig.treadmill.press_start()
        await rig.run(60)
        assert rig.app.store.records == []  # still the same walk
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10)
        assert len(rig.app.store.records) == 1
        await rig.walk()
        await rig.run(60)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10)

    run(scenario())
    first, second = rig.app.store.records
    assert first.seconds > 110 and 55 < second.seconds < 75


def test_walks_are_filed_under_the_game_sessions_they_were_in(rig: Rig):
    """Three walks: one while Alva played, one while Brin did, and one with
    the game closed. Only the saved file says which is which."""
    times = []

    async def scenario():
        await rig.connect()
        for _ in range(3):
            start = rig.clock.wall
            await rig.walk()
            await rig.run(300)
            await rig.step_off()
            times.append((start, rig.clock.wall))
            await rig.run(600)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["free"] * 3
    (a_start, a_end), (b_start, b_end), _ = times
    sessions = [(a_start - 60, a_end + 100, 7, "Alva-Somewhere"), (b_start - 5, b_end + 300, 8, "Brin-Elsewhere")]
    assert rig.game_saves(game_db(sessions))
    assert [(r.source, r.game_session, r.character) for r in rig.app.store.records] == [
        ("wow", 7, "Alva"),
        ("wow", 8, "Brin"),
        ("free", None, ""),
    ]
    assert any("filed under them" in event["text"] for event in rig.app.events)
    # The same file again changes nothing.
    assert not rig.app.take_game_file() and rig.app.file_walks() == 0


def test_one_game_session_can_hold_several_walks(rig: Rig):
    """The session the addon starts by itself lasts the whole login: every
    walk in it is that character's."""

    async def scenario():
        await rig.connect()
        login = rig.clock.wall
        for _ in range(2):
            await rig.walk()
            await rig.run(200)
            await rig.step_off()
            await rig.run(300)
        return login

    login = run(scenario())
    rig.game_saves(game_db([(login - 10, rig.clock.wall, 3, ME)]))
    assert [(r.source, r.game_session) for r in rig.app.store.records] == [("wow", 3), ("wow", 3)]


def test_the_session_still_running_when_the_game_saved_counts_until_then(rig: Rig):
    """A /reload in the middle of a session: the file says it was running,
    and until when. The walk that ends after it is filed under it."""

    async def scenario():
        await rig.connect()
        login = rig.clock.wall
        await rig.walk()
        await rig.run(300)
        rig.game_saves(game_db(active=(login, rig.clock.wall, 5, ME)))  # /reload
        await rig.run(30)
        await rig.step_off()

    run(scenario())
    (record,) = rig.app.store.records
    assert (record.source, record.game_session) == ("wow", 5), "filed as soon as it ended"


def test_a_walk_mostly_outside_the_session_is_not_filed_under_it(rig: Rig):
    async def scenario():
        await rig.connect()
        start = rig.clock.wall
        await rig.walk()
        await rig.run(600)
        await rig.step_off()
        return start

    start = run(scenario())
    # The character logged in for the last two minutes of a ten-minute walk.
    rig.game_saves(game_db([(start + 480, start + 1200, 2, ME)]))
    assert rig.app.store.records[0].source == "free"


def test_a_walk_once_filed_stays_filed(rig: Rig):
    async def scenario():
        await rig.connect()
        start = rig.clock.wall
        await rig.walk()
        await rig.run(120)
        await rig.step_off()
        return start

    start = run(scenario())
    rig.game_saves(game_db([(start, rig.clock.wall, 9, ME)]))
    rig.clock.advance(3600)
    # The addon keeps its newest 400 sessions: one day this one is gone from the file.
    rig.game_saves(game_db([(rig.clock.wall - 600, rig.clock.wall - 60, 10, ME)]))
    assert rig.app.store.records[0].game_session == 9


def test_a_damaged_saved_file_files_nothing_and_breaks_nothing(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        await rig.step_off()

    run(scenario())
    rig.saved_file.parent.mkdir(parents=True)
    rig.saved_file.write_text("WalkaDB = { sessions = { os.execute('x') } }", "utf-8")
    written = rig.clock.wall - 30
    os.utime(rig.saved_file, (written, written))
    rig.app.game.scan(force=True)
    assert not rig.app.take_game_file()
    assert rig.app.store.records[0].source == "free"


def test_the_journeys_come_from_the_saved_file(rig: Rig):
    journeys = {
        ME: {
            "since": 1_790_000_000, "footYd": 17600, "travelYd": 40000, "level": 12, "maxLevel": 60,
            "classId": 4, "faction": 2, "played": 3600,
            "shared": {"savedAt": rig.clock.wall - 40, "steps": 5000, "distanceM": 3500, "kcal": 210,
                       "seconds": 2400, "walks": 2, "estimated": False},
        }
    }  # fmt: skip
    assert rig.game_saves(game_db(journeys=journeys))
    (character,) = rig.app.journeys.to_api()["characters"]
    assert character["name"] == "Tester" and character["level"] == 12 and character["faction"] == "horde"
    assert character["character_steps"] == 20000 and character["steps"] == 5000 and character["walks"] == 2
    assert rig.app.snapshot()["character"]["name"] == "Tester"
    game = rig.app.snapshot()["game"]
    assert game["files"] == 1 and game["characters"] == 1 and game["game_found"] is True


# ---- it only listens --------------------------------------------------------------------------


def test_a_session_in_the_game_does_not_start_the_belt(rig: Rig):
    """The one thing earlier versions did that this one must never do."""

    async def scenario():
        await rig.connect()
        await rig.run(2)
        now = rig.clock.wall
        rig.game_saves(game_db([(now - 600, now - 60, 1, ME)], active=(now - 30, now, 2, ME)))
        await rig.run(300)
        rig.game_saves(game_db(active=(now, rig.clock.wall, 3, ME)))
        await rig.run(30)

    run(scenario())
    status = rig.treadmill.status
    assert status.state is BeltState.STOPPED and status.speed_kmh == 0 and status.target_kmh is None
    assert status.distance_m == 0 and status.elapsed_s == 0
    assert rig.app.store.records == []


@pytest.mark.parametrize("seed", range(6))
def test_nothing_the_game_saves_changes_what_the_belt_does(tmp_path: Path, seed: int):
    """Two identical pads get the same presses on their remotes. One of
    them is listened to by a companion that is shown a game saving anything
    at all - sessions starting and ending, sessions still running, damaged
    files, the link dropping, settings changing, the dashboard's buttons.
    Tick for tick, the two belts do the same."""
    rng = random.Random(seed)
    rig = Rig(tmp_path, us_firmware=bool(seed % 2))
    twin = SimulatedTreadmill(us_firmware=bool(seed % 2), clock=lambda: rig.clock.mono, auto_run=False)

    async def scenario():
        await rig.connect()
        await twin.connect()
        for _ in range(150):
            press = rng.choice(["start", "start", "speed", "speed", "faster", "slower", "pause", "stop", None, None])
            for pad in (rig.treadmill, twin):
                if press == "speed":
                    pad.press_speed(1.0 + (seed * 7 + _ * 13) % 50 / 10)
                elif press is not None:
                    getattr(pad, f"press_{press}")()
            game = rng.choice(["saves", "running", "nothing", "nothing", "damaged"])
            now = rig.clock.wall
            if game == "saves":
                rig.game_saves(game_db([(now - rng.randint(60, 900), now - rng.randint(0, 50), rng.randint(1, 255), ME)]))
            elif game == "running":
                rig.game_saves(game_db(active=(now - rng.randint(60, 900), now, rng.randint(1, 255), ME)))
            elif game == "damaged":
                rig.saved_file.parent.mkdir(parents=True, exist_ok=True)
                rig.saved_file.write_text("WalkaDB = { sessions = 5, active = { savedAt = 'x' } " * rng.randint(0, 2))
                rig.app.game.scan(force=True)
                rig.app.take_game_file()
            if rng.random() < 0.15:
                rig.app.connect()
            if rng.random() < 0.1:
                rig.app.update_config({"units": rng.choice(["mph", "kmh"]), "limits": {"max_speed_kmh": 9.9}})
            if rng.random() < 0.05:
                rig.app.forget_treadmill()
            for _tick in range(rng.randint(1, 60)):
                await rig.run(TICK_S)
                twin.advance(TICK_S)
                assert belt(rig.treadmill) == belt(twin)
        await rig.app.stop()
        assert belt(rig.treadmill) == belt(twin)

    run(scenario())


def test_the_engine_has_nothing_to_move_a_belt_with():
    """No governor, no manual session, no STOP, no resume: there is nothing
    in the App for a dashboard button, a hotkey or a game to call."""
    names = {name for name in dir(App) if not name.startswith("__")}
    for gone in (
        "emergency_stop",
        "resume",
        "manual_start",
        "manual_stop",
        "manual_pause",
        "manual_resume",
        "manual_set_speed",
        "stop_for_sleep",
        "register_hotkey",
        "accept_terms",
        "terms_accepted",
        "belt_limits_for_addon",
        "_execute",
        "_stop_now",
    ):
        assert gone not in names, gone
    source = Path(app_module.__file__).read_text("utf-8")
    for word in ("governor", "hotkey", "guard", "manual_", "set_speed", "press_", "treadmill.start", "treadmill.stop(", "treadmill.pause"):
        assert word not in source, word


def test_app_stop_saves_the_walk_and_leaves_the_belt_running(rig: Rig):
    """Closing Walka is closing a notebook. The belt was never its to stop:
    the walker is still on it, and the remote is in their hand."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        before = belt(rig.treadmill)
        await rig.app.stop()
        return before

    before = run(scenario())
    assert len(rig.app.store.records) == 1 and rig.app.recorder is None
    status = rig.treadmill.status
    assert not status.connected  # the companion let go of the pad ...
    assert (status.state, round(status.speed_kmh, 6), status.target_kmh) == before[:3]  # ... which runs as it did
    assert (rig.app.data_dir / "config.json").exists()


def test_disconnect_leaves_the_belt_running_too(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(30)
        await rig.app.disconnect()
        assert not rig.app.want_connected and not rig.treadmill.status.connected
        assert rig.treadmill.status.state is BeltState.RUNNING and rig.treadmill.status.target_kmh is not None

    run(scenario())


# ---- the snapshot ----------------------------------------------------------------------------------


def test_snapshot_is_json_serialisable_and_says_nothing_about_control(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(5)
        return json.loads(json.dumps(rig.app.snapshot()))

    snapshot = run(scenario())
    assert set(snapshot) == {
        "version",
        "time",
        "units",
        "treadmill",
        "game",
        "session",
        "last_session",
        "walks_rev",
        "totals",
        "heart_rate",
        "addon",
        "check",
        "update",
        "setup",
        "config",
        "events",
        "journey",
        "character",
        "overlay",
    }
    assert snapshot["treadmill"]["moving"] is True and snapshot["treadmill"]["kind"] == "simulated"
    assert "cap_kmh" not in snapshot["treadmill"] and "in_use" not in snapshot["treadmill"]
    assert snapshot["game"] == {
        "game_found": False, "files": 0, "saved_at": None, "read_at": None, "sessions": 0, "characters": 0,
        "demo": False,
    }  # fmt: skip
    assert set(snapshot["config"]) == {
        "units", "profile", "treadmill", "heart_rate", "wow", "web", "overlay", "updates", "welcome_done"
    }  # fmt: skip
    assert "stop_on_exit" not in snapshot["config"]["treadmill"]


def test_the_activity_log_says_when_the_belt_moves_and_stops(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(30)
        rig.treadmill.press_stop()
        await rig.run(10)

    run(scenario())
    texts = [event["text"] for event in reversed(rig.app.events)]
    # ("Walka only listens" is said once on the Live screen, under the belt's state - not in every log line.)
    assert texts[0] == "Treadmill connected: Simulated pad."
    assert texts[1:3] == ["The belt is moving.", "The belt has stopped."]


# ---- the treadmill link -----------------------------------------------------------------------


def test_disconnected_time_is_not_recorded(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        before = rig.app.recorder.seconds
        speed = drop_link(rig)
        await rig.run(40)  # (a dropout shorter than the minute that ends a walk)
        assert rig.app.recorder is not None and rig.app.recorder.seconds == pytest.approx(before, abs=0.3)
        restore_link(rig, speed)
        await rig.run(60)
        assert rig.app.recorder.seconds == pytest.approx(before + 60, abs=1.5)
        texts = [event["text"] for event in rig.app.events]
        assert any("disconnected" in text.lower() for text in texts)
        assert "The belt has stopped." not in texts  # a belt out of reach was not heard to stop
        await rig.step_off()

    run(scenario())
    assert len(rig.app.store.records) == 1  # one walk, not two


def test_treadmill_data_going_stale_is_not_counted_as_walking(rig: Rig):
    """Notifications stop arriving while the link stays up. What the belt
    does then is not known, and what is not known is not written down."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        treadmill = rig.treadmill
        advance = treadmill.advance
        frozen = treadmill.status.updated_at

        def advance_without_reporting(dt: float) -> None:
            advance(dt)
            treadmill.status.updated_at = frozen

        treadmill.advance = advance_without_reporting
        await rig.run(4)  # a short gap is tolerated
        seconds = rig.app.recorder.seconds
        await rig.run(60)
        assert rig.app.recorder.seconds == pytest.approx(seconds, abs=1.5)
        assert rig.app.snapshot()["treadmill"]["moving"] is False
        assert treadmill.status.state is BeltState.RUNNING  # and the belt itself was left as it was
        treadmill.advance = advance
        await rig.run(30)
        assert rig.app.recorder.seconds == pytest.approx(seconds + 30, abs=1.5)

    run(scenario())


def test_connection_is_wanted_eagerly_at_start_then_only_while_there_is_a_walk(rig: Rig):
    """A connection attempt wakes a pad that was switched off, so after the
    first connection a dropped link is only chased while something needs it."""

    async def scenario():
        app = rig.app
        assert app.connection_wanted()  # start-up: keep trying
        await rig.connect()
        await rig.run(2)
        assert not app._eager_connect
        await rig.treadmill.disconnect()  # the user switched the pad off
        await rig.run(2)
        assert not app.connection_wanted()
        # Whatever the game saves is no reason to reconnect ...
        now = rig.clock.wall
        rig.game_saves(game_db(active=(now - 60, now, 1, ME)))
        await rig.run(1)
        assert not app.connection_wanted()
        # ... pressing Connect is.
        app.connect()
        assert app.connection_wanted()
        # "Disconnect" on the dashboard wins over everything.
        await app.disconnect()
        await rig.run(1)
        assert not app.connection_wanted()

    run(scenario())


def test_link_lost_mid_walk_keeps_trying(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(30)
        assert not rig.app._eager_connect
        drop_link(rig)
        await rig.run(5)
        assert rig.app.connection_wanted()  # there is a walk to hear the rest of
        await rig.run(FREE_WALK_GRACE_S + 5)
        assert rig.app.recorder is None and not rig.app.connection_wanted()

    run(scenario())
    assert len(rig.app.store.records) == 1


def test_start_and_stop_with_the_real_loops(tmp_path: Path):
    """The background tasks: the treadmill is connected, a walk on it is
    recorded, and stop() leaves nothing running."""
    config = Config()
    addon = tmp_path / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    config.wow.addon_dir = str(addon)
    treadmill = SimulatedTreadmill(countdown_s=0.1, tick_s=0.02, ramp_kmh_per_s=30.0)
    app = App(config, tmp_path / "data", treadmill=treadmill)

    async def scenario():
        await app.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if treadmill.status.connected:
                break
        assert treadmill.status.connected
        await asyncio.sleep(0.3)
        assert treadmill.status.speed_kmh == 0 and treadmill.status.state is BeltState.STOPPED  # nobody pressed start
        treadmill.press_start()
        for _ in range(300):
            await asyncio.sleep(0.01)
            if app.recorder is not None and treadmill.status.speed_kmh > 0.5:
                break
        assert treadmill.status.speed_kmh > 0.5 and app.recorder is not None and app.recorder.source == "free"
        await app.stop()
        assert treadmill.status.state is BeltState.RUNNING and not treadmill.status.connected
        await asyncio.sleep(0.05)
        leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert leftovers == []

    run(asyncio.wait_for(scenario(), 10))
    assert (addon / "CompanionData.lua").exists()


# ---- crash safety --------------------------------------------------------------------------


def test_checkpoint_and_recovery_after_a_crash(rig: Rig, tmp_path: Path):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(100)

    run(scenario())
    checkpoint = rig.app.data_dir / "active-session.json"
    assert checkpoint.exists()
    saved = json.loads(checkpoint.read_text("utf-8"))
    assert saved["source"] == "free" and 60 <= saved["seconds"] <= 100
    assert rig.app.store.records == []  # the walk is still in progress

    # The process dies here. A new companion starts on the same data folder:
    restarted_app = App(
        Config(),
        rig.app.data_dir,
        treadmill=SimulatedTreadmill(auto_run=False),
        wall_clock=lambda: rig.clock.wall + 3600,
        mono_clock=lambda: 5.0,
    )
    records = restarted_app.store.records
    assert len(records) == 1
    assert records[0].source == "free" and records[0].seconds == saved["seconds"]
    assert records[0].steps == saved["steps"] and records[0].distance_m == saved["distance_m"]
    assert not checkpoint.exists()
    assert restarted_app.totals(rig.clock.wall + 3600)["all"]["sessions"] == 1
    # ... and a third start does not recover it again.
    assert len(SessionStore(rig.app.data_dir).records) == 1


def test_checkpoint_is_cleared_when_the_walk_is_saved_normally(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(100)
        assert (rig.app.data_dir / "active-session.json").exists()
        rig.treadmill.press_stop()
        await rig.run(3)
        await rig.run(FREE_WALK_GRACE_S + 5)

    run(scenario())
    assert not (rig.app.data_dir / "active-session.json").exists()
    assert len(SessionStore(rig.app.data_dir).records) == 1  # saved once, not twice


# ---- settings left over from a version that drove the belt ----------------------------------------------


def test_settings_of_an_older_version_are_ignored_not_obeyed(tmp_path: Path):
    """A config.json written by 0.6 holds speed limits, a STOP hotkey, the
    acceptance of that version's disclaimer. They load without a complaint,
    set nothing, and are gone from the file the next time it is saved."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.json").write_text(
        json.dumps(
            {
                "units": "kmh",
                "limits": {"max_speed_kmh": 4.83, "safe_speed_kmh": 1.3},
                "manual": {"speed_kmh": 3.2},
                "terms": {"accepted_version": 1, "accepted_at": "2026-10-01T10:00:00+00:00"},
                "stop_hotkey": "ctrl+alt+x",
                "treadmill": {"address": "F4:12:FA:00:30:AB", "stop_on_exit": True},
            }
        ),
        "utf-8",
    )
    from walka import config as config_module

    config = config_module.load(data)
    assert config.units == "kmh" and config.treadmill.address == "F4:12:FA:00:30:AB"
    for gone in ("limits", "manual", "terms", "stop_hotkey"):
        assert not hasattr(config, gone)
    app = App(config, data, treadmill=SimulatedTreadmill(auto_run=False))
    app.update_config({"limits": {"max_speed_kmh": 6.0}, "stop_hotkey": "f13", "terms": {"accepted_version": 9}})
    saved = json.loads((data / "config.json").read_text("utf-8"))
    assert set(saved) == {"units", "profile", "treadmill", "heart_rate", "wow", "web", "overlay", "updates", "welcome_done"}
    assert "stop_on_exit" not in saved["treadmill"]


# ---- what the addon gets ------------------------------------------------------------------------


def test_addon_data_after_several_walks(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1)
        login = rig.clock.wall
        rig.treadmill.press_start()
        await rig.run(4)
        rig.treadmill.press_speed(3.2)
        await rig.run(120)  # a game walk
        await rig.run(5)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10)
        rig.game_saves(game_db([(login - 5, rig.clock.wall, 11, ME)]))  # logout
        await rig.walk(2.4)  # a walk with no game at all
        await rig.run(90)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10)
        rig.treadmill.press_start()
        await rig.run(12)  # too short to keep
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10)

    run(scenario())
    records = rig.app.store.records
    assert [r.source for r in records] == ["wow", "free"]
    data = load_companion_data(rig)
    sessions = [data.sessions[i] for i in range(1, 3)]
    assert data.sessions[3] is None
    assert [s.source for s in sessions] == ["free", "wow"]  # newest first
    for entry, record in zip(sessions, reversed(records), strict=True):
        assert entry.steps == record.steps
        assert entry.seconds == round(record.seconds)
        assert entry.distanceM == round(record.distance_m)
        assert entry.kcal == pytest.approx(record.kcal, abs=0.051)
        assert entry["end"] > entry.start
    assert sessions[1].gameSession == 11 and sessions[0].gameSession is None
    assert data.treadmill == "Simulated pad"
    assert data.profile.weightKg == 75 and data.profile.heightCm == 172
    assert data.limits is None  # nothing in the game needs to know what the belt can do any more
    assert data.older is None
    # The dashboard's totals agree with what was synced - and the belt
    # coasting down after the last stop is not a "walk in progress".
    assert rig.app.snapshot()["session"] is None
    totals = rig.app.snapshot()["totals"]
    assert totals["all"]["sessions"] == 2
    assert totals["all"]["steps"] == sum(r.steps for r in records)


def test_missing_addon_folder_is_reported_not_fatal(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.config.wow.addon_dir = str(tmp_path / "nowhere")

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        await rig.step_off()

    run(scenario())
    assert len(rig.app.store.records) == 1  # the walk is saved regardless
    snapshot = rig.app.snapshot()
    assert snapshot["addon"]["dir"] is None and "not found" in snapshot["addon"]["error"]
    assert rig.app.sync_addon() is False


def test_the_treadmills_name_is_remembered_for_the_addon(rig: Rig):
    async def scenario():
        await rig.connect()

    run(scenario())
    state = json.loads((rig.app.data_dir / "state.json").read_text("utf-8"))
    assert state == {"treadmill": {"name": "Simulated pad"}}


def test_a_sync_that_blows_up_is_reported_not_raised(rig: Rig, monkeypatch):
    """The sync is a convenience: one damaged walk on file, or a treadmill
    name that cannot be encoded, must not stop the companion starting."""

    def explode(*_args, **_kwargs):
        raise ValueError("cannot convert float NaN to integer")

    monkeypatch.setattr("walka.app.addon_sync.build_payload", explode)
    assert rig.app.sync_addon() is False
    assert "NaN" in rig.app.addon_error

    async def from_the_loop():
        rig.app.addon_error = ""
        rig.app.request_sync()
        await rig.app.synced()

    run(from_the_loop())
    assert "NaN" in rig.app.addon_error


# ---- heart rate -------------------------------------------------------------------------------------


class FakeHeartRate:
    def __init__(self) -> None:
        self.fresh = True
        self.bpm = 118
        self.closed = False

    def to_dict(self) -> dict:
        return {"enabled": True, "connected": True, "bpm": self.bpm if self.fresh else None}

    async def run(self) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


def test_heart_rate_reaches_the_recording(tmp_path: Path):
    rig = Rig(tmp_path)
    rig.config.profile.age, rig.config.profile.sex = 40, "male"
    monitor = FakeHeartRate()
    rig.app.heart_rate = monitor

    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        await rig.run(4)
        rig.treadmill.press_speed(3.2)
        await rig.run(200)  # 118 bpm is above the threshold for a 40-year-old (108)
        live = rig.app.snapshot()
        assert live["session"]["hr"] == 118 and live["session"]["calories_source"] == "heart_rate"
        assert live["heart_rate"]["bpm"] == 118
        monitor.fresh = False  # the strap falls off
        await rig.run(10)
        assert rig.app.snapshot()["session"]["hr"] is None
        await rig.step_off()

    run(scenario())
    record = rig.app.store.records[0]
    assert record.avg_hr == pytest.approx(118, abs=0.5) and record.max_hr == 118
    assert record.calories_source == "heart_rate"
    assert load_companion_data(rig).sessions[1].avgHr == 118


def test_the_snapshot_says_whether_the_first_run_guide_is_due_and_which_units_to_suggest(rig: Rig, monkeypatch):
    from walka import locale_units

    monkeypatch.setattr(locale_units, "system_units", lambda: "kmh")
    snapshot = rig.app.snapshot()
    assert snapshot["setup"]["system_units"] == "kmh"
    assert snapshot["config"]["welcome_done"] is False  # a new install
    rig.app.update_config({"welcome_done": True})
    assert rig.app.snapshot()["config"]["welcome_done"] is True


def test_a_broken_locale_lookup_suggests_nothing(rig: Rig, monkeypatch):
    from walka import locale_units

    def broken():
        raise OSError("no locale")

    monkeypatch.setattr(locale_units, "system_units", broken)
    assert rig.app.snapshot()["setup"]["system_units"] is None


# ---- a walk that cannot be written -----------------------------------------------------------------


def full_disk(monkeypatch) -> dict:
    """Every write of a walk fails, as on a full disk, until switched off."""
    real_write = SessionStore._write
    state = {"full": True}

    def write(path, data):
        if state["full"]:
            raise OSError(28, "No space left on device")
        real_write(path, data)

    monkeypatch.setattr(SessionStore, "_write", staticmethod(write))
    return state


def walk_files(rig: Rig) -> list[Path]:
    return sorted((rig.app.data_dir / "sessions").glob("*.json"))


def test_a_walk_that_cannot_be_written_is_kept_and_written_once_there_is_room(rig: Rig, monkeypatch):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(300)  # (checkpoints written: the disk still had room)
        full = full_disk(monkeypatch)
        await rig.step_off()
        assert walk_files(rig) == [] and rig.app.store.records == []
        assert (rig.app.data_dir / "active-session.json").exists(), "the checkpoint keeps it meanwhile"
        assert any("could not be saved" in event["text"] for event in rig.app.events)
        full["full"] = False
        await rig.run(app_module.CHECKPOINT_EVERY_S + 2)  # with no walk going: tried again all the same
        assert len(walk_files(rig)) == 1 and rig.app.store.records[0].seconds > 290
        assert not (rig.app.data_dir / "active-session.json").exists()
        assert any(event["text"].startswith("Walk saved") for event in rig.app.events)

    run(scenario())


def test_a_walk_still_unwritten_survives_a_crash_and_a_start_on_a_full_disk(rig: Rig, tmp_path: Path, monkeypatch):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(300)
        full_disk(monkeypatch)
        await rig.step_off()
        # Walka is killed here, and started again with the disk still full.

    run(scenario())
    checkpoint = rig.app.data_dir / "active-session.json"
    seconds = json.loads(checkpoint.read_text("utf-8"))["seconds"]
    rig.app.store._remove_checkpoint = lambda: None  # (the killed one clears nothing)
    del rig.app
    import gc

    gc.collect()
    again = App(Config(), tmp_path / "data", treadmill=SimulatedTreadmill(auto_run=False))
    assert again.store.records == [] and checkpoint.exists(), "the only copy of the walk"
    assert any(event["level"] == "error" and "could not be saved" in event["text"] for event in again.events)
    del again
    gc.collect()
    monkeypatch.undo()
    later = App(Config(), tmp_path / "data", treadmill=SimulatedTreadmill(auto_run=False))
    assert [record.seconds for record in later.store.records] == [seconds]
    assert not checkpoint.exists()


def test_one_touch_of_the_hand_grips_does_not_last_the_walk(rig: Rig):
    """A heart rate the pad reported once, long ago, is not this walk's."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        rig.treadmill.status.heart_rate_bpm = 140
        rig.treadmill.status.heart_rate_at = rig.clock.mono
        await rig.run(3)
        assert rig.app.recorder.last_hr == 140, "fresh: it counts"
        await rig.run(120)  # the pad never says it again
        assert rig.app.recorder.last_hr is None
        await rig.step_off()

    run(scenario())
    record = rig.app.store.records[-1]
    assert record.calories_source == "speed" and record.max_hr == 140 and record.avg_hr == pytest.approx(140)


def test_the_checkpoint_goes_as_soon_as_the_walk_is_written(rig: Rig, monkeypatch):
    """Not after the addon's file and the Walka account: a Walka killed in
    between would find the checkpoint at its next start."""
    seen: list[bool] = []
    real_sync = rig.app.sync_addon

    def sync(*args, **kwargs):
        seen.append((rig.app.data_dir / "active-session.json").exists())
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(rig.app, "sync_addon", sync)

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        assert (rig.app.data_dir / "active-session.json").exists()
        await rig.step_off()

    run(scenario())
    assert len(rig.app.store.records) == 1 and seen and seen[-1] is False


# ---- nothing on the loop waits on the disk --------------------------------------------------------


def test_a_slow_look_at_the_games_folders_does_not_hold_up_the_loop(rig: Rig, monkeypatch):
    """The scan runs on a worker thread. What the loop asks of it meanwhile
    (rev, sessions, the status) is answered at once."""
    import threading
    import time as real_time

    from walka import game_file

    inside = threading.Event()
    real_files = game_file.account_files

    def slow(folder):
        inside.set()
        real_time.sleep(1.0)  # a network drive, a disk that is spinning up
        return real_files(folder)

    monkeypatch.setattr(game_file, "account_files", slow)
    scanning = threading.Thread(target=rig.app.game.scan, kwargs={"force": True})
    scanning.start()
    assert inside.wait(5)
    began = real_time.monotonic()
    rig.app.game.rev, rig.app.game.sessions(), rig.app.game.status(), rig.app.game.journeys()
    assert real_time.monotonic() - began < 0.2
    scanning.join()


def test_the_addons_file_is_written_off_the_loop(rig: Rig, monkeypatch):
    """Finding the addon's folder looks at drives, and writing the file may
    wait for the game to let go of it: a walk's end never waits for that."""
    import threading
    import time as real_time

    from walka import addon_sync

    loop_thread: list[int] = []
    wrote_on: list[int] = []
    real_write = addon_sync.write_data_file

    def slow_write(directory, payload):
        wrote_on.append(threading.get_ident())
        real_time.sleep(0.5)
        return real_write(directory, payload)

    monkeypatch.setattr(addon_sync, "write_data_file", slow_write)

    async def scenario():
        loop_thread.append(threading.get_ident())
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        rig.treadmill.press_stop()
        slowest = 0.0
        for _ in range(int((FREE_WALK_GRACE_S + 5) / TICK_S)):  # the walk ends, and the file is to be written
            rig.clock.advance(TICK_S)
            began = real_time.monotonic()
            await rig.app.tick()
            slowest = max(slowest, real_time.monotonic() - began)
            rig.treadmill.advance(TICK_S)
        assert rig.app.recorder is None and slowest < 0.3, "the loop waited for the file"
        await rig.app.synced()
        assert wrote_on and loop_thread[0] not in wrote_on
        assert rig.app.last_sync is not None

    run(scenario())
    assert load_companion_data(rig).sessions[1] is not None


# ---- made-up walks among real ones -------------------------------------------------------------------


class RealPad(SimulatedTreadmill):
    """The simulated pad's workings under a real reader's kind."""

    kind = "ftms"


def test_made_up_walks_are_kept_but_count_for_nothing_with_a_real_treadmill(tmp_path: Path):
    from walka.session import SessionRecord

    data = tmp_path / "data"
    store = SessionStore(data)
    common = {"distance_m": 1000.0, "steps": 1500, "kcal": 60.0, "seconds": 900.0}
    store.add(SessionRecord(id="real", start=1_799_990_000.0, end=1_799_991_000.0, treadmill="URTM030", **common))
    store.add(SessionRecord(id="demo", start=1_799_992_000.0, end=1_799_993_000.0, treadmill="URTM030",
                            made_up=True, **common))  # fmt: skip
    store.add(SessionRecord(id="old", start=1_799_994_000.0, end=1_799_995_000.0, treadmill="Simulated pad", **common))
    del store
    config = Config()
    addon = tmp_path / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    config.wow.addon_dir = str(addon)
    real = App(config, data, treadmill=RealPad(auto_run=False), wall_clock=lambda: 1_800_000_000.0)
    assert [r.id for r in real.store.records] == ["real", "demo", "old"], "kept: History lists them"
    assert real.totals(1_800_000_000.0)["all"]["sessions"] == 1
    assert real.sync_addon()
    text = (addon / "CompanionData.lua").read_text("utf-8")
    assert text.count("steps = 1500") == 1
    # On the simulated treadmill itself, what it made up is shown.
    made_up = App(config, data, treadmill=SimulatedTreadmill(auto_run=False), wall_clock=lambda: 1_800_000_000.0)
    assert made_up.totals(1_800_000_000.0)["all"]["sessions"] == 3


def test_a_made_up_run_does_not_write_the_installed_addons_file(tmp_path: Path):
    config = Config()
    addon = tmp_path / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    config.wow.addon_dir = str(addon)
    app = App(config, tmp_path / "data", treadmill=SimulatedTreadmill(auto_run=False), writes_addon=False)
    assert app.sync_addon() is False and "made-up" in app.addon_error
    assert not (addon / "CompanionData.lua").exists()


def test_a_clock_set_back_in_the_middle_of_a_walk_changes_nothing_of_it(rig: Rig):
    """The computer's clock is set back an hour (by hand, or by a time
    server): the walk goes on being counted and checkpointed, and is saved
    as long as it was, so that it can still be filed under a game session."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        rig.clock.wall -= 3600
        checkpoint = rig.app.data_dir / "active-session.json"
        before = json.loads(checkpoint.read_text("utf-8"))["seconds"]
        await rig.run(300)
        assert json.loads(checkpoint.read_text("utf-8"))["seconds"] > before + 250, "checkpoints go on"
        assert rig.app.recorder.seconds > 400
        await rig.step_off()

    run(scenario())
    (record,) = rig.app.store.records
    assert record.seconds > 400
    assert record.end - record.start == pytest.approx(record.seconds + FREE_WALK_GRACE_S + 6, abs=15)


def test_state_json_is_never_left_half_written(rig: Rig, monkeypatch):
    rig.app._state["treadmill"] = {"name": "URTM030"}
    rig.app._save_state()
    path = rig.app.data_dir / app_module.STATE_FILE
    assert json.loads(path.read_text("utf-8"))["treadmill"]["name"] == "URTM030"
    real_write = Path.write_text

    def cut_short(self, text, *args, **kwargs):
        real_write(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", cut_short)
    rig.app._state["treadmill"] = {"name": "another pad with a longer name"}
    rig.app._save_state()
    monkeypatch.undo()
    assert json.loads(path.read_text("utf-8"))["treadmill"]["name"] == "URTM030"


def test_filing_a_walk_under_a_game_session_is_news_to_the_walk_list(rig: Rig):
    """The History list asks for the walks again only when the snapshot says
    something changed: a walk filed under a character must be such news."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        await rig.step_off()

    run(scenario())
    (walk,) = rig.app.store.records
    before = rig.app.snapshot()["walks_rev"]
    assert rig.game_saves(game_db([(walk.start - 60, walk.end + 60, 3, ME)]))
    assert rig.app.store.records[0].source == "wow"
    after = rig.app.snapshot()
    assert after["walks_rev"] > before
    assert after["totals"]["all"]["sessions"] == 1, "(the count of walks alone did not change)"
