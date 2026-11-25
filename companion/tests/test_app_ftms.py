"""End to end with the real Bluetooth reader: an emulated
UREVO pad (GATT level) -> FtmsTreadmill -> recorded walk. What matters here
is what reaches the *pad*: nothing. ``FakePad.writes`` holds every byte
written to it over Bluetooth, and every scenario ends with it empty."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_app import Clock
from walka import ftms
from walka.app import FREE_WALK_GRACE_S, TICK_S, App
from walka.config import Config
from walka.treadmill import BeltState, ftms_ble
from walka.treadmill.ftms_ble import FtmsTreadmill
from walka_fakes import FakeBackend, FakePad

MPH = 1.609344


class PadRig:
    def __init__(self, tmp_path: Path, monkeypatch, pad: FakePad | None = None, **driver_kwargs) -> None:
        self.clock = Clock()
        # The reader stamps the pad's data with time.monotonic().
        monkeypatch.setattr(ftms_ble, "time", SimpleNamespace(monotonic=lambda: self.clock.mono))
        self.pad = pad or FakePad(steps_in_data=True)
        self.backend = FakeBackend(self.pad)
        self.treadmill = FtmsTreadmill(backend=self.backend, **driver_kwargs)
        self.config = Config()
        self.addon = tmp_path / "AddOns" / "Walka"
        self.addon.mkdir(parents=True)
        (self.addon / "Walka.toc").write_text("## Interface: 16001\n")
        self.config.wow.addon_dir = str(self.addon)
        self.app = App(
            self.config,
            tmp_path / "data",
            treadmill=self.treadmill,
            wall_clock=lambda: self.clock.wall,
            mono_clock=lambda: self.clock.mono,
        )

    async def run(self, seconds: float) -> None:
        for _ in range(int(round(seconds / TICK_S))):
            self.clock.advance(TICK_S)
            await self.app.tick()
            self.pad.advance(TICK_S)

    async def end_walk(self) -> None:
        """The walker stops the belt (if it is not stopped yet), and the walk
        ends a minute later."""
        self.pad.press_stop()
        await self.run(FREE_WALK_GRACE_S + 5)

    async def walking(self, kmh: float = 2.0 * MPH, seconds: float = 40) -> None:
        """Connected, and the walker on the belt: start on the remote, the
        countdown, then ``kmh``."""
        await self.treadmill.connect()
        await self.run(1)
        self.pad.press_start()
        await self.run(4)
        self.pad.press_speed(kmh)
        await self.run(seconds)

    def heard_nothing_from_walka(self) -> bool:
        return self.pad.writes == [] and ftms.CONTROL_POINT not in self.pad.subscriptions


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_walk_on_the_emulated_urevo_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(2.0 * MPH, seconds=300)
        assert rig.pad.target_tenths == 20 and rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        assert rig.treadmill.status.state is BeltState.RUNNING
        assert rig.app.snapshot()["treadmill"]["moving"] is True
        rig.pad.press_stop()
        await rig.run(5)
        await rig.end_walk()

    run(scenario())
    (record,) = rig.app.store.records
    assert record.source == "free" and record.treadmill == "URTM030"
    assert 290 <= record.seconds <= 310
    assert record.distance_m == pytest.approx(2.0 * MPH / 3.6 * 300, rel=0.05)
    assert record.steps_source == "treadmill" and record.distance_source == "treadmill"
    assert rig.heard_nothing_from_walka()


def test_the_first_pad_is_the_only_pad(tmp_path: Path, monkeypatch):
    """Walka remembers the treadmill it first listened to, so that a second
    one in Bluetooth range - a neighbour's - is never counted as yours."""
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        assert rig.config.treadmill.address == ""
        await rig.treadmill.connect()
        assert rig.config.treadmill.address == rig.pad.address == rig.treadmill.address
        assert rig.app.snapshot()["treadmill"]["pinned"] is True
        rig.app.forget_treadmill()
        assert rig.config.treadmill.address == "" and rig.treadmill.address is None
        assert rig.app.snapshot()["treadmill"]["pinned"] is False

    run(scenario())
    assert any("only reconnects to this treadmill" in event["text"] for event in rig.app.events)
    assert rig.heard_nothing_from_walka()


@pytest.mark.parametrize("how", ["press_stop", "press_pause", "pull_safety_key"])
def test_the_walker_stops_the_belt_and_walka_writes_it_down(tmp_path: Path, monkeypatch, how: str):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(seconds=60)
        getattr(rig.pad, how)()
        await rig.run(300)
        assert rig.pad.speed_kmh == 0 and rig.pad.state in ("idle", "paused")  # ... and the belt stays as it was left
        assert rig.app.snapshot()["treadmill"]["moving"] is False
        await rig.end_walk()

    run(scenario())
    (record,) = rig.app.store.records
    assert 55 <= record.seconds <= 70
    assert rig.heard_nothing_from_walka()


def test_link_loss_and_reconnect_with_the_pad_still_running(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(seconds=60)
        rig.pad.drop_link()
        await rig.run(30)  # the pad runs on at 2.0 mph, out of hearing
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH)
        assert rig.app.connection_wanted()
        await rig.treadmill.connect()
        await rig.run(60)
        assert rig.pad.speed_kmh == pytest.approx(2.0 * MPH) and rig.pad.target_tenths == 20
        await rig.end_walk()

    run(scenario())
    (record,) = rig.app.store.records
    assert 115 <= record.seconds <= 130  # the half minute nobody heard is not made up
    assert rig.pad.connects == 2 and rig.heard_nothing_from_walka()


def test_closing_walka_changes_nothing_at_the_pad(tmp_path: Path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def scenario():
        await rig.walking(3.0 * MPH, seconds=60)
        await rig.app.stop()

    run(scenario())
    assert rig.pad.state == "running" and rig.pad.target_tenths == 30 and rig.pad.speed_kmh == pytest.approx(3.0 * MPH)
    assert rig.pad.client is None and len(rig.app.store.records) == 1
    assert rig.heard_nothing_from_walka()


@pytest.mark.parametrize("seed", range(4))
def test_soak_on_the_emulated_pad(tmp_path: Path, monkeypatch, seed: int):
    """Anything the dashboard or the radio can do, for a long
    time - and at the end the pad has still not been sent a single byte."""
    rng = random.Random(seed)
    rig = PadRig(tmp_path, monkeypatch, FakePad(us_firmware=bool(seed % 2), steps_in_data=True))

    async def scenario():
        await rig.treadmill.connect()
        for _ in range(120):
            event = rng.choice(
                ["start", "speed", "speed", "pause", "stop", "key", "drop", "connect", "forget", "config", None, None]
            )
            if event in ("start", "pause", "stop"):
                getattr(rig.pad, f"press_{event}")()
            elif event == "speed":
                rig.pad.press_speed(rng.uniform(0.5, 9.0))
            elif event == "key":
                rig.pad.pull_safety_key()
            elif event == "drop":
                rig.pad.drop_link()
            elif event == "connect":
                rig.app.connect()
                await rig.treadmill.connect()
            elif event == "forget":
                rig.app.forget_treadmill()
            elif event == "config":
                rig.app.update_config({"profile": {"weight_kg": rng.uniform(50, 100)}, "limits": {"max_speed_kmh": 1}})
            await rig.run(rng.uniform(0.2, 30))
            assert rig.heard_nothing_from_walka()
        await rig.run(FREE_WALK_GRACE_S + 5)
        await rig.app.stop()

    run(scenario())
    assert rig.heard_nothing_from_walka()
    for record in rig.app.store.records:
        assert record.seconds >= 20 and record.distance_m >= 0 and record.steps >= 0


@pytest.mark.parametrize("seed", [2, 8])
def test_soak_on_the_emulated_pad_through_every_route_of_the_dashboard_with_a_walka_account(
    tmp_path: Path, monkeypatch, caplog, seed: int
):
    """The same long run, by the dashboard's own routes: every POST and
    DELETE it has, in any order, with what a page might send - the buttons
    of the Walka-account card among them, with the part handed in and a
    stand-in for the accounts behind it. Walks are recorded, saved and sent
    meanwhile. And at the end the pad has still not been sent a single
    byte. (docs/SAFETY.md counts on these runs.)"""
    # (Here, not at the top: that file's rig is built on this one's.)
    import walka_account_rig
    from test_walka_account_wiring import (
        JUNK,
        ROUTES,
        dashboard,
        every_route_that_changes_something,
        left_running,
        on_the_emulated_pad,
        said,
    )
    from test_walka_account_wiring import run as run_scenario

    rng = random.Random(seed)
    pad_does = ["start", "speed", "speed", "stop", "drop", "connect"]
    card_does = ["sign-in", "sign-in", "switch-on", "switch-on", "switch-on", "switch-on"]
    walka_account_rig.before_test(caplog)
    pressed: dict[tuple[str, str], set[int]] = {}
    walked: set[str] = set()  # every walk that ever lay on this computer, deleted since or not

    async def scenario():
        async with walka_account_rig.Rig(tmp_path, start=False) as rig:
            house, app, part = on_the_emulated_pad(rig, tmp_path, monkeypatch)
            part.start()
            user = rig.person()

            def what_a_page_might_send(path: str):
                question = app.snapshot()["walka_account"]["question"]
                weight, send = rng.uniform(50, 100), rng.choice(["all", "from_now_on"])
                return rng.choice(
                    {
                        "/api/treadmill/connect": [{}, {"address": house.pad.address}],
                        "/api/treadmill/press": [{"button": "start"}, {"button": "faster"}, {"button": "stop"}, {}],
                        "/api/config": [{"profile": {"weight_kg": weight}}, {"limits": {"max_speed_kmh": 1}}, {}],
                        # (Into a folder of the test's own: on a computer with the game on it, the real one is found.)
                        "/api/addon/install": [{"addons_dir": str(tmp_path / "AddOns")}],
                        "/api/walka-account/sign-in": [{"way": "discord"}, {"way": "github"}, {"way": "google"}, {}],
                        "/api/walka-account/sending": [
                            {"question": question["id"] if question else "", "send": send},
                            rng.choice(JUNK),
                        ],
                    }.get(path, [{}, {"button": "start"}, {"action": "start", "speed_kmh": 3.0}])
                )

            async with dashboard(app) as client:
                routes = every_route_that_changes_something(client)
                assert len(routes) >= 17 and {path for _method, path in routes} >= set(ROUTES)

                async def press(method: str, path: str) -> int:
                    walk = rng.choice([record.id for record in app.store.records] or ["nothing"])
                    there, body = path.replace("{session_id}", walk), what_a_page_might_send(path)
                    response = await client.request(
                        method, there, data=json.dumps(body), headers={"Content-Type": "application/json"}
                    )
                    await response.read()
                    assert response.status < 500, (method, path, body)
                    pressed.setdefault((method, path), set()).add(response.status)
                    await rig.idle()
                    return response.status

                # First a walk that is sent for certain: whatever comes after, this was no idle run.
                await rig.sign_in()
                await rig.switch_on("all")
                await house.walking(seconds=120)
                house.pad.press_stop()
                await house.run(5)
                await house.end_walk()
                await rig.idle()
                assert len(rig.fake.walks(user)) == 1 and house.heard_nothing_from_walka()
                # Then every route once, in its turn: which of them chance picks below is Python's business.
                for method, path in routes:
                    walked.update(record.id for record in app.store.records)
                    await press(method, path)
                    await house.run(rng.uniform(0.2, 5))
                    assert house.heard_nothing_from_walka()

                for _ in range(150):
                    walked.update(record.id for record in app.store.records)
                    event = rng.choice([*pad_does, *card_does, *routes, *routes])
                    if event in ("start", "stop"):
                        getattr(house.pad, f"press_{event}")()
                    elif event == "speed":
                        house.pad.press_speed(rng.uniform(0.5, 6.0))
                    elif event == "drop":
                        house.pad.drop_link()
                    elif event == "connect":
                        app.connect(house.pad.address)
                        await house.treadmill.connect()
                    elif event == "sign-in":
                        await press("POST", "/api/walka-account/sign-in")
                    elif event == "switch-on":
                        # (The question, and its answer straight after: by chance alone the two seldom meet.)
                        await said(client, "/api/walka-account/question")
                        await rig.idle()
                        await press("POST", "/api/walka-account/sending")
                    else:
                        await press(*event)
                    await house.run(rng.uniform(0.2, 30))
                    await rig.idle()
                    assert house.heard_nothing_from_walka()
                await house.run(FREE_WALK_GRACE_S + 5)
                await rig.idle()
                walked.update(record.id for record in app.store.records)
                await app.stop()
            assert house.heard_nothing_from_walka()
            assert await left_running() == []
            # Every route was pressed, and the buttons of the card did what they do: some were let through.
            assert set(pressed) == set(routes)
            assert 200 in pressed[("POST", "/api/walka-account/sign-out")] and 200 in pressed[("POST", "/api/config")]
            # What went to the Walka account are walks that lay on this computer, and nothing of the pad.
            assert len(rig.fake.walks(user)) >= 1
            assert {row["client_id"] for row in rig.fake.walks(user)} <= walked
            assert "URTM030" not in json.dumps(rig.pushes())

    run_scenario(scenario, 120)
    walka_account_rig.after_test(caplog)


# ---- treadmills of other makes ----------------------------------------------------------


def test_walk_on_an_emulated_wellfit(tmp_path: Path, monkeypatch):
    """A full-size treadmill that goes to 10 mph: found by its name and read
    like any pad. How fast it goes is its owner's business - Walka has no
    speed limit of its own any more, because it sets no speed."""
    pad = FakePad(name="WELLFIT TM Linker", address="A4:C1:38:00:29:01", max_tenths=100, says_treadmill=True)
    rig = PadRig(tmp_path, monkeypatch, pad)

    async def scenario():
        await rig.walking(6.0 * MPH, seconds=120)
        assert rig.pad.target_tenths == 60
        await rig.end_walk()

    run(scenario())
    (record,) = rig.app.store.records
    assert record.treadmill == "WELLFIT TM Linker"
    assert record.max_kmh == pytest.approx(6.0 * MPH, abs=0.02)
    assert rig.heard_nothing_from_walka()


def test_a_treadmill_that_takes_no_commands_at_all_is_just_as_good(tmp_path: Path, monkeypatch):
    """Plenty of treadmills show their numbers to apps and cannot be driven
    by them. For a companion that only listens, that is the whole job."""
    rig = PadRig(tmp_path, monkeypatch, FakePad(name="Treadmill X", has_control_point=False))

    async def scenario():
        await rig.walking(seconds=60)
        await rig.end_walk()

    run(scenario())
    assert len(rig.app.store.records) == 1
    assert not [event for event in rig.app.events if event["level"] != "info"]
    assert rig.heard_nothing_from_walka()
