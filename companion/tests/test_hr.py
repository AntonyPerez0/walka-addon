"""The heart-rate sensor: skin contact, battery, the scan list and a remembered sensor."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import bleak
import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import ftms, web
from walka.hr import HeartRateMonitor

JSON = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    "packet,bpm,contact",
    [
        (bytes([0x00, 72]), 72, None),  # the sensor cannot tell
        (bytes([0x06, 72]), 72, True),  # can tell: touching
        (bytes([0x04, 72]), 72, False),  # can tell: not touching
        (bytes([0x07, 0x2C, 0x01]), 300, True),  # 16-bit value
    ],
)
def test_the_contact_bits(packet, bpm, contact):
    measurement = ftms.parse_heart_rate_measurement(packet)
    assert (measurement.bpm, measurement.contact) == (bpm, contact)


def test_a_reading_without_skin_contact_is_not_used():
    now = [100.0]
    monitor = HeartRateMonitor(enabled=True, clock=lambda: now[0])
    monitor.connected = True
    monitor.feed(bytes([0x06, 88]))
    assert monitor.to_dict()["bpm"] == 88 and monitor.contact is True
    monitor.feed(bytes([0x04, 0]))  # strap off: many send 0 or an old value
    assert monitor.to_dict()["bpm"] is None and monitor.contact is False
    assert "not touching the skin" in monitor.detail
    monitor.feed(bytes([0x06, 91]))
    assert monitor.to_dict()["bpm"] == 91 and monitor.detail == "connected"


def test_battery_level():
    monitor = HeartRateMonitor()
    monitor.feed_battery(bytes([87]))
    assert monitor.to_dict()["battery"] == 87
    monitor.feed_battery(bytes([250]))
    assert monitor.battery == 100


@pytest.fixture
def radio(monkeypatch):
    adverts = {}

    class Scanner:
        @staticmethod
        async def discover(timeout, return_adv):
            return adverts

    monkeypatch.setattr(bleak, "BleakScanner", Scanner)

    def add(address, name, *, hr=False, rssi=-60):
        adverts[address] = (
            SimpleNamespace(address=address, name=name),
            SimpleNamespace(local_name=name, service_uuids=[ftms.HEART_RATE_SERVICE] if hr else [], rssi=rssi),
        )

    return add


def test_the_scan_marks_the_remembered_sensor_and_can_list_everything(radio):
    radio("11:00:00:00:00:01", "Polar H10 1234", hr=True, rssi=-70)
    radio("11:00:00:00:00:02", "Forerunner 265", rssi=-50)
    radio("11:00:00:00:00:03", "[TV] Living room", rssi=-40)
    monitor = HeartRateMonitor(address="11:00:00:00:00:02", name="Forerunner 265")
    found = asyncio.run(monitor.discover(0.1))
    assert [d["name"] for d in found] == ["Forerunner 265", "Polar H10 1234"]
    assert found[0]["remembered"] and not found[1]["remembered"]
    everything = asyncio.run(monitor.discover(0.1, all_devices=True))
    assert [d["name"] for d in everything][-1] == "[TV] Living room" and not everything[-1]["candidate"]


def test_turning_off_remembers_the_sensor_and_on_uses_it_again(tmp_path):
    rig = Rig(tmp_path)
    monitor = HeartRateMonitor()
    rig.app.heart_rate = monitor

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            async def post(path, body):
                response = await client.post(path, data=json.dumps(body), headers=JSON)
                return response.status, await response.json()

            await post("/api/heart-rate/connect", {"address": "11:00:00:00:00:02", "name": "Forerunner 265"})
            assert (monitor.enabled, monitor.address) == (True, "11:00:00:00:00:02")
            await post("/api/heart-rate/connect", {"enabled": False})
            assert not monitor.enabled and rig.app.config.heart_rate.address == "11:00:00:00:00:02"
            assert rig.app.config.heart_rate.name == "Forerunner 265"
            await post("/api/heart-rate/connect", {"enabled": True})
            assert monitor.enabled and monitor.address == "11:00:00:00:00:02"
            await post("/api/heart-rate/connect", {"enabled": False, "forget": True})
            assert rig.app.config.heart_rate.address == ""

            async def peek(address, seconds=5.0):
                return 77

            monitor.peek = peek
            status, data = await post("/api/heart-rate/peek", {"address": "11:00:00:00:00:01"})
            assert status == 200 and data["bpm"] == 77
            response = await client.post("/api/heart-rate/peek", data="{}", headers=JSON)
            assert response.status == 400

    asyncio.run(asyncio.wait_for(main(), 30))


def test_peeking_at_the_sensor_in_use_does_not_connect_again():
    now = [100.0]
    monitor = HeartRateMonitor(enabled=True, address="AA", clock=lambda: now[0])
    monitor.connected = True
    monitor.feed(bytes([0x00, 64]))
    assert asyncio.run(monitor.peek("aa")) == 64
