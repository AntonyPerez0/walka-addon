""""Show all Bluetooth devices" in the treadmill scan, and checking one that was picked."""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from test_app_ftms import PadRig
from walka import ftms, web
from walka.treadmill.readers import reader_for, service_words
from walka_fakes import FakePad

JSON = {"Content-Type": "application/json"}

STRAP = "AA:00:00:00:00:01"


def a_strap() -> FakePad:
    """A heart-rate strap: in range, but nothing Walka reads a treadmill from."""
    strap = FakePad(name="Polar H10", address=STRAP, advertises_ftms=False, has_ftms=False)
    strap.extra_services = [ftms.HEART_RATE_SERVICE, ftms.uuid16(0x180F), "0000feee-0000-1000-8000-00805f9b34fb"]
    strap.extra_characteristics = [ftms.HEART_RATE_MEASUREMENT]
    return strap


def test_what_walka_can_read():
    assert reader_for([ftms.TREADMILL_DATA.upper()]).key == "ftms"
    assert reader_for([ftms.HEART_RATE_MEASUREMENT]) is None
    assert service_words([ftms.HEART_RATE_SERVICE, ftms.uuid16(0x180F), "0000fff0-0000-1000-8000-00805f9b34fb"]) == [
        "Heart Rate",
        "Battery",
        "vendor service fff0",
    ]


def test_all_devices_are_listed_and_a_picked_one_is_checked(tmp_path, monkeypatch):
    pad = FakePad(name="Mystery pad", address="BB:00:00:00:00:02", advertises_ftms=False)  # no known name, no advert
    rig = PadRig(tmp_path, monkeypatch, pad=pad)
    rig.backend.pads.append(a_strap())

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            async def post(path, body):
                response = await client.post(path, data=json.dumps(body), headers=JSON)
                return response.status, await response.json() if response.content_type == "application/json" else None

            status, data = await post("/api/treadmill/scan", {})
            assert status == 200 and data["devices"] == []  # neither looks like a treadmill
            status, data = await post("/api/treadmill/scan", {"all": True})
            assert {d["name"]: d["candidate"] for d in data["devices"]} == {"Mystery pad": False, "Polar H10": False}

            status, data = await post("/api/treadmill/identify", {"address": STRAP})
            assert status == 200 and data["device"]["readable"] is False
            assert data["device"]["services"] == ["Heart Rate", "Battery", "vendor service feee"]
            assert not rig.app.want_connected or rig.app.config.treadmill.address != STRAP

            status, data = await post("/api/treadmill/identify", {"address": pad.address})
            assert data["device"]["readable"] is True and data["device"]["reader"] == "ftms"
            assert rig.app.config.treadmill.address == pad.address and rig.app.connection_wanted()

            response = await client.post("/api/treadmill/identify", data=json.dumps({}), headers=JSON)
            assert response.status == 400

    asyncio.run(asyncio.wait_for(main(), 30))
    assert pad.writes == [] and rig.backend.pads[1].writes == []
