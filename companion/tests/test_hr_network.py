"""Heart rate over the local network (decision 2): off by default, its own
port, a pairing code, and nothing but a heart rate taken."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import hr_network
from walka.config import Config
from walka.hr import HeartRateMonitor


@pytest.mark.parametrize(
    "text,bpm",
    [
        ("72", 72),
        ("72.6", 73),
        ('{"bpm": 81}', 81),
        ('{"heartRate": 90}', 90),
        ('{"heart_rate": {"value": 64}}', 64),
        ('[{"hr": 60}, {"hr": 61}]', 61),
        ("", None),
        ("abc", None),
        ('{"steps": 100}', None),
        ("300", None),
        ("0", None),
    ],
)
def test_what_a_watch_app_may_send(text, bpm):
    assert hr_network.parse_bpm(text) == bpm


def test_codes():
    codes = {hr_network.new_code() for _ in range(50)}
    assert all(len(code) == 6 and code.isdigit() for code in codes) and len(codes) > 40


def test_it_is_off_by_default():
    config = Config()
    assert config.heart_rate.network is False and config.heart_rate.network_code == ""


def run_server(receiver, scenario):
    async def main():
        async with TestClient(TestServer(receiver.make_app())) as client:
            await scenario(client)

    asyncio.run(asyncio.wait_for(main(), 20))


def test_the_receiver_takes_a_heart_rate_with_the_code_and_nothing_else():
    got: list[int] = []
    now = [1000.0]
    receiver = hr_network.NetworkHeartRate(got.append, code="123456", clock=lambda: now[0])

    async def scenario(client):
        assert (await client.post("/123456/hr", data='{"bpm": 77}')).status == 200
        assert (await client.post("/123456/hr", data="78", headers={"Content-Type": "text/plain"})).status == 200
        assert (await client.get("/123456/hr?bpm=79")).status == 200
        assert (await client.post("/123456/hr", data="nonsense")).status == 400
        assert (await client.post("/123456/hr", data="7" * 2000)).status == 413
        for path in ("/", "/api/state", "/api/config", "/123456", "/123456/other", "/static/index.html"):
            assert (await client.get(path)).status in (404, 405), path
        assert got == [77, 78, 79]

    run_server(receiver, scenario)
    assert receiver.received == 3


def test_wrong_codes_lock_an_address_out():
    got: list[int] = []
    now = [1000.0]
    receiver = hr_network.NetworkHeartRate(got.append, code="123456", clock=lambda: now[0])

    async def scenario(client):
        for guess in range(hr_network.MAX_WRONG):
            assert (await client.post(f"/{guess:06d}/hr", data="70")).status == 404
        # Locked out now - the right code too.
        assert (await client.post("/123456/hr", data="70")).status == 429
        now[0] += hr_network.LOCKOUT_S + 1
        assert (await client.post("/123456/hr", data="70")).status == 200

    run_server(receiver, scenario)
    assert got == [70]


def test_a_network_reading_counts_like_a_sensors():
    now = [100.0]
    monitor = HeartRateMonitor(clock=lambda: now[0])
    monitor.feed_network(95)
    assert monitor.to_dict()["bpm"] == 95 and monitor.to_dict()["source"] == "network"
    now[0] += 11
    assert monitor.to_dict()["bpm"] is None  # stale
    # A connected Bluetooth sensor that is reading comes first.
    monitor.connected = True
    monitor.feed(bytes([0x00, 70]))
    monitor.feed_network(120)
    assert monitor.to_dict()["bpm"] == 70


def test_the_app_listens_only_when_switched_on_and_makes_a_code(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    rig.app.heart_rate = HeartRateMonitor()
    started: list[int] = []

    async def start(self):
        started.append(self.port)
        self._runner = object()

    async def stop(self):
        self._runner = None

    monkeypatch.setattr(hr_network, "local_addresses", lambda: ["192.168.1.20"])
    monkeypatch.setattr(hr_network.NetworkHeartRate, "start", start)
    monkeypatch.setattr(hr_network.NetworkHeartRate, "stop", stop)

    async def scenario():
        await rig.app.apply_hr_network()
        assert rig.app.hr_network is None and started == []  # off by default: nothing listens
        rig.app.config.heart_rate.network = True
        await rig.app.apply_hr_network()
        code = rig.app.config.heart_rate.network_code
        assert len(code) == 6 and started == [8788] and rig.app.hr_network.code == code
        rig.app.new_hr_network_code()
        assert rig.app.hr_network.code == rig.app.config.heart_rate.network_code
        snapshot = rig.app.snapshot()["heart_rate"]["network"]
        assert snapshot["listening"] and snapshot["port"] == 8788 and snapshot["addresses"] == ["192.168.1.20"]
        rig.app.config.heart_rate.network = False
        await rig.app.apply_hr_network()
        assert rig.app.hr_network is None

    asyncio.run(scenario())


def test_the_real_listener_answers_on_its_own_port(tmp_path):
    got: list[int] = []
    receiver = hr_network.NetworkHeartRate(got.append, code="654321", port=0, host="127.0.0.1")

    async def scenario():
        import aiohttp

        await receiver.start()
        try:
            port = receiver._runner.addresses[0][1]
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{port}/654321/hr", data="66") as response:
                    assert response.status == 200
        finally:
            await receiver.stop()

    asyncio.run(asyncio.wait_for(scenario(), 20))
    assert got == [66] and not receiver.running


@pytest.mark.parametrize("text", ["1e999", "-1e999", '{"bpm": 1e999}', "[1e999]", "nan", "inf"])
def test_a_number_too_large_to_be_one_is_no_heart_rate(text):
    assert hr_network.parse_bpm(text) is None


def test_a_code_in_other_letters_is_a_wrong_code_not_a_crash():
    got: list[int] = []
    receiver = hr_network.NetworkHeartRate(got.append, code="123456", clock=lambda: 1000.0)

    async def scenario(client):
        assert (await client.post("/%C3%A9%C3%A9%C3%A9123/hr", data="70")).status == 404
        assert (await client.post("/123456/hr", data="1e999")).status == 400
        assert (await client.post("/123456/hr", data="70")).status == 200

    run_server(receiver, scenario)
    assert got == [70]
