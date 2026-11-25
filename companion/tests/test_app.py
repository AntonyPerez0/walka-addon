"""End-to-end: beacon frames and a simulated treadmill -> recorded walk ->
data file for the addon. Time is fully simulated.

The belt is worked by the walker - ``rig.treadmill.press_*`` is the pad's
remote - and by nobody else. Whatever the game shows, the companion's part
is to listen and to write down."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import pytest

from walka import addon_sync
from walka import app as app_module
from walka.app import ABANDONED_AFTER_S, FREE_WALK_GRACE_S, TICK_S, App
from walka.config import Config
from walka.protocol import Flags, Frame, kmh_to_raw
from walka.session import SessionStore
from walka.sources import BeaconSource
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


class Rig:
    def __init__(self, tmp_path: Path, *, us_firmware: bool = True) -> None:
        self.clock = Clock()
        self.config = Config()
        self.treadmill = SimulatedTreadmill(us_firmware=us_firmware, clock=lambda: self.clock.mono, auto_run=False)
        self.beacon = BeaconSource(None, clock=lambda: self.clock.mono)  # frames are injected
        self.addon = tmp_path / "AddOns" / "Walka"
        self.addon.mkdir(parents=True)
        (self.addon / "Walka.toc").write_text("## Interface: 16001\n")
        self.config.wow.addon_dir = str(self.addon)
        self.app = App(
            self.config,
            tmp_path / "data",
            treadmill=self.treadmill,
            beacon=self.beacon,
            wall_clock=lambda: self.clock.wall,
            mono_clock=lambda: self.clock.mono,
        )
        self.seq = 0

    async def connect(self) -> None:
        await self.treadmill.connect()

    async def run(
        self,
        seconds: float,
        *,
        active: bool = True,
        paused: bool = False,
        moving: bool = True,
        afk: bool = False,
        session: int = 1,
        signal: bool = True,
        legacy_target: float = 0.0,
    ) -> None:
        """Advance simulated time; each tick optionally injects a fresh
        frame. (``legacy_target``: the belt speed an addon older than 0.7
        asks for in its frames - the companion must take no notice.)"""
        for _ in range(int(round(seconds / TICK_S))):
            self.clock.advance(TICK_S)
            if signal:
                self.seq = (self.seq + 1) % 256
                flags = Flags(0)
                if active:
                    flags |= Flags.ACTIVE
                if paused:
                    flags |= Flags.PAUSED
                if moving:
                    flags |= Flags.MOVING
                if afk:
                    flags |= Flags.AFK
                self.beacon.inject(
                    Frame(
                        seq=self.seq,
                        session=session if active else 0,
                        flags=flags,
                        target_raw=kmh_to_raw(legacy_target),
                        char_speed_raw=350 if moving else 0,
                    )
                )
            await self.app.tick()
            self.treadmill.advance(TICK_S)

    async def walk(self, kmh: float = 3.2) -> None:
        """The walker presses start on the remote, waits out the countdown
        and sets a speed. (Six seconds pass, with no game in view.)"""
        self.treadmill.press_start()
        await self.run(4, signal=False)
        self.treadmill.press_speed(kmh)
        await self.run(2, signal=False)


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
        await rig.run(2, active=False)
        assert rig.app.recorder is None
        await rig.run(2)  # "/walka start" in the game ...
        rig.treadmill.press_start()  # ... and start on the pad's remote
        await rig.run(4)
        rig.treadmill.press_speed(2.0 * MPH)
        await rig.run(600)  # ten minutes at 2 mph
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        live = rig.app.snapshot()["session"]
        assert live["source"] == "wow" and live["seconds"] > 500
        rig.treadmill.press_stop()
        await rig.run(5)
        await rig.run(3, active=False)  # "/walka stop"

    run(scenario())
    records = rig.app.store.records
    assert len(records) == 1
    record = records[0]
    assert record.source == "wow" and record.beacon_session == 1
    assert 560 <= record.seconds <= 610
    # ~3.2 km/h for ~10 minutes
    assert 480 <= record.distance_m <= 560
    assert 800 <= record.steps <= 1000
    assert 25 <= record.kcal <= 60
    assert record.steps_source == "treadmill"
    # The addon data file was written and is valid Lua.
    data_file = rig.addon / "CompanionData.lua"
    text = data_file.read_text("utf-8")
    assert text.count("WalkaCompanionData = {") == 1
    data = load_companion_data(rig)
    assert data.version == addon_sync.DATA_VERSION
    assert data.sessions[1].steps == record.steps


def test_a_walk_with_no_game_is_recorded_all_the_same(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120, signal=False)
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 30, signal=False)

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["free"]
    assert rig.app.store.records[0].beacon_session is None


def test_a_short_stop_does_not_split_a_walk_and_a_long_one_does(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60, signal=False)
        rig.treadmill.press_pause()  # the doorbell
        await rig.run(FREE_WALK_GRACE_S - 20, signal=False)
        rig.treadmill.press_start()
        await rig.run(60, signal=False)
        assert rig.app.store.records == []  # still the same walk
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10, signal=False)
        assert len(rig.app.store.records) == 1
        await rig.walk()
        await rig.run(60, signal=False)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10, signal=False)

    run(scenario())
    first, second = rig.app.store.records
    assert first.seconds > 110 and 55 < second.seconds < 75


def test_new_game_session_splits_recordings(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk(3.0)
        await rig.run(60, session=4)
        await rig.run(2, active=False)
        await rig.run(60, session=5)
        await rig.run(2, active=False)

    run(scenario())
    records = rig.app.store.records
    # (The first seconds, before the game showed a session, were too short to keep.)
    assert [(r.source, r.beacon_session) for r in records[:2]] == [("wow", 4), ("wow", 5)]


def test_a_game_session_with_nobody_on_the_belt_is_not_a_walk(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(600)  # a session marked in the game, and the pad standing still
        assert rig.app.snapshot()["session"]["seconds"] == 0
        await rig.run(3, active=False)

    run(scenario())
    assert rig.app.store.records == []


def test_a_pause_in_the_game_does_not_stop_the_count(rig: Rig):
    """The walk is whatever the belt does. "Paused" in the game - by hand,
    or by itself when the player goes AFK - only marks the addon's own
    estimate; a belt that turns on is walked on."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60)
        before = rig.app.recorder.seconds
        await rig.run(60, paused=True, afk=True, moving=False)
        assert rig.app.recorder.seconds == pytest.approx(before + 60, abs=1)
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)
        await rig.run(3, active=False)

    run(scenario())
    assert [r.beacon_session for r in rig.app.store.records if r.source == "wow"] == [1]


def test_the_game_out_of_view_does_not_end_the_walk(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(60, session=7)
        await rig.run(300, signal=False)  # a loading screen, the game minimised, a window on top
        assert rig.app.recorder is not None and rig.app.recorder.beacon_session == 7
        assert rig.treadmill.status.speed_kmh == pytest.approx(2.0 * MPH, abs=0.01)  # nothing slowed it down
        await rig.run(60, session=7)
        await rig.run(3, active=False)

    run(scenario())
    game_walks = [r for r in rig.app.store.records if r.source == "wow"]
    assert len(game_walks) == 1 and game_walks[0].seconds > 400


def test_abandoned_session_is_closed_and_saved(rig: Rig):
    """Game gone, belt stopped, nobody comes back: after a quarter of an
    hour the walk is filed instead of staying open for ever."""

    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(120)
        rig.treadmill.press_stop()
        await rig.run(ABANDONED_AFTER_S - 60, signal=False)
        assert rig.app.recorder is not None
        await rig.run(120, signal=False)
        assert rig.app.recorder is None

    run(scenario())
    assert [r.source for r in rig.app.store.records][-1] == "wow"
    assert rig.app.store.records[-1].seconds > 100


def test_closing_the_game_ends_the_session_and_leaves_the_belt_alone(rig: Rig):
    from walka.beacon import Decoded
    from walka.capture import GameWindow, ReadResult

    class Reader:
        window = GameWindow("World of Warcraft", 0, 0, 1920, 1080, r"C:\WoW\_forever_\WowForever.exe")

    reader = Reader()

    async def tick(frame: Frame | None) -> None:
        rig.clock.advance(TICK_S)
        result = ReadResult(Decoded(frame, 0, 3.0, 255, 0), (0, 0), "window") if frame else ReadResult(None, None, "")
        rig.beacon._ingest(rig.clock.mono, reader, result)
        await rig.app.tick()
        rig.treadmill.advance(TICK_S)

    async def scenario():
        await rig.connect()
        rig.treadmill.press_start()
        for seq in range(5):
            await tick(Frame(seq=seq, session=0))
        for seq in range(5, 205):
            await tick(Frame(seq=seq % 256, session=3, flags=Flags.ACTIVE | Flags.MOVING))
        rig.treadmill.press_speed(3.2)
        for seq in range(205, 305):
            await tick(Frame(seq=seq % 256, session=3, flags=Flags.ACTIVE | Flags.MOVING))
        speed = rig.treadmill.status.speed_kmh
        assert speed > 2.5
        reader.window = None  # Alt+F4
        for _ in range(40):  # 8 s: so far the game is only out of view
            await tick(None)
        assert rig.app.recorder is not None and rig.app.recorder.source == "wow"
        for _ in range(30):
            await tick(None)
        # The game session is over and filed. The belt is the walker's: it
        # turns as before, and what is walked from here is a walk of its own.
        assert rig.app.snapshot()["source"]["beacon"]["state"] in ("game_closed", "no_signal")
        assert rig.treadmill.status.speed_kmh == pytest.approx(speed)
        assert rig.treadmill.status.state is BeltState.RUNNING
        assert rig.app.recorder is not None and rig.app.recorder.source == "free"

    run(scenario())
    assert [r.source for r in rig.app.store.records] == ["wow"]


# ---- it only listens --------------------------------------------------------------------------


def test_a_session_in_the_game_does_not_start_the_belt(rig: Rig):
    """The one thing earlier versions did that this one must never do."""

    async def scenario():
        await rig.connect()
        await rig.run(2, active=False)
        await rig.run(300, legacy_target=3.2)  # Start pressed in the game - by an addon that still asks for a speed
        await rig.run(60, paused=True)
        await rig.run(300, moving=False)
        await rig.run(30, session=2)
        await rig.run(3, active=False)

    run(scenario())
    status = rig.treadmill.status
    assert status.state is BeltState.STOPPED and status.speed_kmh == 0 and status.target_kmh is None
    assert status.distance_m == 0 and status.elapsed_s == 0


@pytest.mark.parametrize("seed", range(6))
def test_nothing_the_game_shows_changes_what_the_belt_does(tmp_path: Path, seed: int):
    """Two identical pads get the same presses on their remotes. One of
    them is listened to by a companion that is shown a game doing anything
    at all - sessions starting and ending, pauses, AFK, a frozen picture,
    frames that ask for a speed, the link dropping, settings changing, the
    dashboard's buttons. Tick for tick, the two belts do the same."""
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
            game = rng.choice(["session", "session", "paused", "afk", "none", "lost", "new", "asks"])
            if rng.random() < 0.15:
                rig.app.connect()
            if rng.random() < 0.1:
                rig.app.update_config({"units": rng.choice(["mph", "kmh"]), "limits": {"max_speed_kmh": 9.9}})
            if rng.random() < 0.05:
                rig.app.forget_treadmill()
            for _tick in range(rng.randint(1, 60)):
                await rig.run(
                    TICK_S,
                    active=game != "none",
                    paused=game in ("paused", "afk"),
                    afk=game == "afk",
                    moving=rng.random() < 0.7,
                    session=1 + (game == "new") * rng.randint(1, 200),
                    signal=game != "lost",
                    legacy_target=rng.uniform(0.0, 40.0) if game == "asks" else 0.0,
                )
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
        await rig.run(30, signal=False)
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
        "source",
        "session",
        "last_session",
        "totals",
        "heart_rate",
        "addon",
        "check",
        "setup",
        "config",
        "events",
        "journey",
        "character",
        "overlay",
    }
    assert snapshot["treadmill"]["moving"] is True and snapshot["treadmill"]["kind"] == "simulated"
    assert "cap_kmh" not in snapshot["treadmill"] and "in_use" not in snapshot["treadmill"]
    assert snapshot["source"]["kind"] == "wow" and "manual" not in snapshot["source"]
    assert "target_kmh" not in snapshot["source"]["frame"]
    assert set(snapshot["config"]) == {
        "units", "profile", "treadmill", "heart_rate", "wow", "web", "overlay", "welcome_done"
    }  # fmt: skip
    assert "stop_on_exit" not in snapshot["config"]["treadmill"]


def test_the_activity_log_says_when_the_belt_moves_and_stops(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.walk()
        await rig.run(30, signal=False)
        rig.treadmill.press_stop()
        await rig.run(10, signal=False)

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
        await rig.run(120)
        assert rig.app.recorder is not None and rig.app.recorder.seconds == pytest.approx(before, abs=0.3)
        restore_link(rig, speed)
        await rig.run(60)
        assert rig.app.recorder.seconds == pytest.approx(before + 60, abs=1.5)
        await rig.run(3, active=False)

    run(scenario())
    assert len([r for r in rig.app.store.records if r.source == "wow"]) == 1  # one walk, not two
    texts = [event["text"] for event in rig.app.events]
    assert any("disconnected" in text.lower() for text in texts)
    assert "The belt has stopped." not in texts  # a belt out of reach was not heard to stop


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
        await rig.run(2, active=False)
        assert not app._eager_connect
        await rig.treadmill.disconnect()  # the user switched the pad off
        await rig.run(2, active=False)
        assert not app.connection_wanted()
        # A session marked in the game is a reason to reconnect ...
        await rig.run(1)
        assert app.connection_wanted()
        await rig.run(2, active=False)
        assert not app.connection_wanted()
        # ... and so is pressing Connect.
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
        await rig.run(30, signal=False)
        assert not rig.app._eager_connect
        drop_link(rig)
        await rig.run(5, signal=False)
        assert rig.app.connection_wanted()  # there is a walk to hear the rest of
        await rig.run(FREE_WALK_GRACE_S + 5, signal=False)
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
    app = App(config, tmp_path / "data", treadmill=treadmill, beacon=BeaconSource(None))

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
    assert saved["source"] == "wow" and 60 <= saved["seconds"] <= 100
    assert rig.app.store.records == []  # the walk is still in progress

    # The process dies here. A new companion starts on the same data folder:
    restarted_app = App(
        Config(),
        rig.app.data_dir,
        treadmill=SimulatedTreadmill(auto_run=False),
        beacon=BeaconSource(None),
        wall_clock=lambda: rig.clock.wall + 3600,
        mono_clock=lambda: 5.0,
    )
    records = restarted_app.store.records
    assert len(records) == 1
    assert records[0].source == "wow" and records[0].seconds == saved["seconds"]
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
        await rig.run(3, active=False)
        await rig.run(FREE_WALK_GRACE_S + 5, active=False)

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
    app = App(config, data, treadmill=SimulatedTreadmill(auto_run=False), beacon=BeaconSource(None))
    app.update_config({"limits": {"max_speed_kmh": 6.0}, "stop_hotkey": "f13", "terms": {"accepted_version": 9}})
    saved = json.loads((data / "config.json").read_text("utf-8"))
    assert set(saved) == {"units", "profile", "treadmill", "heart_rate", "wow", "web", "overlay", "welcome_done"}
    assert "stop_on_exit" not in saved["treadmill"]


# ---- what the addon gets ------------------------------------------------------------------------


def test_addon_data_after_several_walks(rig: Rig):
    async def scenario():
        await rig.connect()
        await rig.run(1, active=False)
        rig.treadmill.press_start()
        await rig.run(4, session=11)
        rig.treadmill.press_speed(3.2)
        await rig.run(120, session=11)  # a game walk
        await rig.run(5, active=False)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10, active=False)
        await rig.walk(2.4)  # a walk with no game at all
        await rig.run(90, signal=False)
        rig.treadmill.press_stop()
        await rig.run(FREE_WALK_GRACE_S + 10, signal=False)
        rig.treadmill.press_start()
        await rig.run(12, session=12)  # too short to keep
        rig.treadmill.press_stop()
        await rig.run(5, session=12)
        await rig.run(5, active=False)

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
    assert sessions[1].beaconSession == 11 and sessions[0].beaconSession is None
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
        await rig.run(3, active=False)

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

    monkeypatch.setattr("walka.app.addon_sync.sync", explode)
    assert rig.app.sync_addon() is False
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
        await rig.run(3, active=False)

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
