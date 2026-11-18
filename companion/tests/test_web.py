"""The dashboard's HTTP API: the guard against other web pages, and every
endpoint's happy path and bad input."""

from __future__ import annotations

import asyncio
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app import Rig
from walka import web
from walka.config import MAX_SPEED_CAP_KMH
from walka.governor import Phase
from walka.protocol import djb2
from walka.session import SessionRecord
from walka.treadmill import BeltState, TreadmillError

JSON = {"Content-Type": "application/json"}


def serve(rig: Rig, scenario):
    """Run ``scenario(client)`` against the dashboard server."""

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            return await scenario(client)

    return asyncio.run(asyncio.wait_for(main(), 30))


async def post(client: TestClient, path: str, body=None, **kwargs):
    return await client.post(path, data=json.dumps({} if body is None else body), headers=JSON, **kwargs)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def add_record(rig: Rig, start: float, **overrides) -> SessionRecord:
    fields = dict(
        id=f"walk-{int(start)}",
        start=start,
        end=start + 630,
        seconds=600.0,
        distance_m=530.0,
        steps=900,
        kcal=38.0,
        max_kmh=3.2,
        source="wow",
        samples=[[float(t), 3.2, None] for t in range(0, 601, 5)],
    )
    fields.update(overrides)
    return rig.app.store.add(SessionRecord(**fields))


# ---- the guard ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8787",
        "LOCALHOST:8787",
        "127.0.0.1",
        "127.0.0.1:8787",
        "127.0.0.1:1",
        "[::1]",
        "[::1]:8787",
        "::1",
        "192.168.1.20:8787",
        "10.0.0.5",
        "[fe80::1]:8787",
    ],
)
def test_host_header_accepted(host: str):
    assert web._host_is_local(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example.com",
        "evil.example.com:8787",
        "127.0.0.1.evil.example.com",
        "localhost.evil.example.com:8787",
        "localhost.",
        "walka.local",
        "127.0.0.1:8787.evil.example.com",
        "evil.example.com:127.0.0.1",
        "[evil.example.com]",
        "[::1].evil.example.com",
        "0x7f.0.0.1",
        "2130706433",
        "127.1",
        "localhost:8787:8787",
        "a" * 300,
    ],
)
def test_host_header_refused(host):
    assert not web._host_is_local(host)


def test_dns_rebinding_style_requests_are_refused(rig: Rig):
    """Another site's page whose domain was re-pointed at 127.0.0.1: the
    browser sends that site's name as Host."""

    async def scenario(client: TestClient):
        for host in ("attacker.example.com", "attacker.example.com:8787", "127.0.0.1.attacker.example.com"):
            for method, path in (
                ("GET", "/api/state"),
                ("GET", "/"),
                ("GET", "/api/events"),
                ("GET", "/static/app.js"),
                ("POST", "/api/stop"),
                ("POST", "/api/manual"),
                ("DELETE", "/api/sessions/x"),
            ):
                response = await client.request(
                    method, path, headers={"Host": host, **JSON}, data=json.dumps({"action": "start"})
                )
                assert response.status == 403, (host, method, path)
        assert not rig.app.manual.active

    serve(rig, scenario)


def test_local_hosts_are_served(rig: Rig):
    async def scenario(client: TestClient):
        for host in ("localhost:8787", "127.0.0.1:8787", "[::1]:8787", "192.168.1.20:8787"):
            response = await client.get("/api/state", headers={"Host": host})
            assert response.status == 200, host
            response = await client.post(
                "/api/resume", headers={"Host": host, "Origin": f"http://{host}", **JSON}, data="{}"
            )
            assert response.status == 200, host

    serve(rig, scenario)


def test_cross_origin_posts_are_refused(rig: Rig):
    async def scenario(client: TestClient):
        host = f"127.0.0.1:{client.port}"
        for origin in (
            "http://evil.example.com",
            "https://evil.example.com",
            "null",  # a sandboxed frame or a local file
            "http://127.0.0.1:9999",  # another local server
            "http://localhost.evil.example.com",
            f"http://{host}.evil.example.com",
            f"ftp://{host}",
        ):
            response = await client.post(
                "/api/manual", data=json.dumps({"action": "start", "speed_kmh": 4}), headers={"Origin": origin, **JSON}
            )
            assert response.status == 403, origin
        assert not rig.app.manual.active
        # The dashboard's own origin, and clients that send none (curl), are fine.
        for headers in ({"Origin": f"http://{host}"}, {}):
            response = await client.post(
                "/api/manual", data=json.dumps({"action": "pause"}), headers={**headers, **JSON}
            )
            assert response.status == 200

    serve(rig, scenario)


def test_state_changing_requests_must_be_json(rig: Rig):
    """A cross-site form or fetch() can only send "simple" content types
    without a preflight - none of which is accepted."""

    async def scenario(client: TestClient):
        body = json.dumps({"action": "start", "speed_kmh": 4})
        for content_type in (
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=x",
            None,
        ):
            headers = {"Content-Type": content_type} if content_type else {}
            for path in ("/api/manual", "/api/stop", "/api/resume", "/api/config", "/api/treadmill/connect"):
                response = await client.post(path, data=body, headers=headers)
                assert response.status == 415, (content_type, path)
        response = await client.delete("/api/sessions/anything")
        assert response.status == 415
        response = await client.put("/api/config", data=body, headers={"Content-Type": "text/plain"})
        assert response.status in (405, 415)
        assert not rig.app.manual.active
        # With a charset parameter it is still JSON.
        response = await client.post(
            "/api/manual",
            data=json.dumps({"action": "pause"}),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        assert response.status == 200

    serve(rig, scenario)


def test_preflight_is_never_granted(rig: Rig):
    async def scenario(client: TestClient):
        response = await client.options(
            "/api/stop",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status >= 400
        assert "Access-Control-Allow-Origin" not in response.headers
        response = await client.get("/api/state", headers={"Origin": "http://evil.example.com"})
        assert "Access-Control-Allow-Origin" not in response.headers  # readable only same-origin

    serve(rig, scenario)


def test_responses_are_not_cached_or_sniffed(rig: Rig):
    async def scenario(client: TestClient):
        for path in ("/api/state", "/api/config", "/api/sessions"):
            response = await client.get(path)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.content_type == "application/json"

    serve(rig, scenario)


# ---- state, events --------------------------------------------------------------------


def test_state_snapshot(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=3.2)
        response = await client.get("/api/state")
        assert response.status == 200
        state = await response.json()
        assert {
            "version",
            "time",
            "units",
            "treadmill",
            "governor",
            "source",
            "session",
            "totals",
            "config",
            "events",
        } <= set(state)
        assert state["governor"]["phase"] == "following"
        assert state["treadmill"]["connected"] and state["treadmill"]["kind"] == "simulated"
        assert state["treadmill"]["speed_kmh"] > 2.5
        assert state["source"]["kind"] == "wow" and state["source"]["frame"]["active"] is True
        assert state["session"]["source"] == "wow"
        assert state["treadmill"]["cap_kmh"] == rig.config.limits.max_speed_kmh

    serve(rig, scenario)


def test_event_stream_delivers_snapshots(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        response = await client.get("/api/events")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert response.headers["Cache-Control"] == "no-store"
        first = await asyncio.wait_for(response.content.readuntil(b"\n\n"), 5)
        assert first.startswith(b"data: ")
        snapshot = json.loads(first[len(b"data: ") :])
        assert snapshot["treadmill"]["connected"] is True and snapshot["governor"]["phase"]
        # State changes show up in the following events.
        rig.app.manual_start(3.0)
        second = await asyncio.wait_for(response.content.readuntil(b"\n\n"), 5)
        assert json.loads(second[len(b"data: ") :])["source"]["manual"]["active"] is True
        response.close()

    serve(rig, scenario)


def test_index_page_and_unknown_routes(rig: Rig):
    async def scenario(client: TestClient):
        response = await client.get("/")
        if (web.STATIC_DIR / "index.html").exists():
            assert response.status == 200 and response.content_type == "text/html"
        else:
            assert response.status == 404
        assert (await client.get("/api/nope")).status == 404
        assert (await client.get("/api/stop")).status == 405  # stopping needs a POST
        assert (await client.get("/static/../web.py")).status in (403, 404)
        assert (await client.get("/static/%2e%2e/web.py")).status in (403, 404)

    serve(rig, scenario)


# ---- manual sessions ---------------------------------------------------------------------


def test_manual_session_over_http(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        response = await post(client, "/api/manual", {"action": "start", "speed_kmh": 3.0})
        assert response.status == 200 and (await response.json()) == {"ok": True}
        await rig.run(40, signal=False)
        assert rig.treadmill.status.speed_kmh > 2.5

        assert (await post(client, "/api/manual", {"action": "speed", "speed_kmh": 2.0})).status == 200
        await rig.run(10, signal=False)
        assert rig.treadmill.status.speed_kmh < 2.2

        assert (await post(client, "/api/manual", {"action": "pause"})).status == 200
        await rig.run(5, signal=False)
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.PAUSED

        assert (await post(client, "/api/manual", {"action": "resume"})).status == 200
        await rig.run(30, signal=False)
        assert rig.treadmill.status.speed_kmh > 1.5

        assert (await post(client, "/api/manual", {"action": "stop"})).status == 200
        await rig.run(5, signal=False)
        assert rig.treadmill.status.speed_kmh == 0 and not rig.app.manual.active

        # Start without a speed uses the remembered one.
        assert (await post(client, "/api/manual", {"action": "start"})).status == 200
        assert rig.app.manual.active and rig.app.manual.speed_kmh == 2.0

    serve(rig, scenario)


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "start", "speed_kmh": "fast"}',
        '{"action": "start", "speed_kmh": [3]}',
        '{"action": "start", "speed_kmh": {"kmh": 3}}',
        '{"action": "start", "speed_kmh": NaN}',
        '{"action": "start", "speed_kmh": Infinity}',
        '{"action": "start", "speed_kmh": -Infinity}',
        '{"action": "start", "speed_kmh": 1e999}',
        '{"action": "start", "speed_kmh": "nan"}',
        '{"action": "start", "speed_kmh": "inf"}',
        '{"action": "speed", "speed_kmh": NaN}',
        '{"action": "speed"}',
        '{"action": "speed", "speed_kmh": null}',
        '{"action": "warp", "speed_kmh": 3}',
        '{"speed_kmh": 3}',
        "{}",
        '{"action": null}',
        '{"action": ["start"]}',
        "[1, 2]",
        '"start"',
        "null",
        "{not json",
        "",
    ],
)
def test_manual_bad_input_is_refused_and_changes_nothing(rig: Rig, raw: str):
    async def scenario(client: TestClient):
        await rig.connect()
        response = await client.post("/api/manual", data=raw, headers=JSON)
        assert response.status == 400, await response.text()
        assert not rig.app.manual.active
        assert rig.app.manual.speed_kmh == rig.config.manual.speed_kmh
        await rig.run(5, signal=False)
        assert rig.treadmill.status.state is BeltState.STOPPED

    serve(rig, scenario)


def test_manual_extreme_speeds_are_capped_not_obeyed(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        assert (await post(client, "/api/manual", {"action": "start", "speed_kmh": 1e9})).status == 200
        await rig.run(60, signal=False)
        assert rig.treadmill.status.speed_kmh <= rig.config.limits.max_speed_kmh + 1e-6
        assert (await post(client, "/api/manual", {"action": "speed", "speed_kmh": -50})).status == 200
        await rig.run(10, signal=False)
        assert rig.treadmill.status.speed_kmh == 0  # a non-positive speed pauses, never reverses
        json.dumps(rig.app.snapshot())

    serve(rig, scenario)


def test_manual_speed_as_a_numeric_string_is_accepted(rig: Rig):
    async def scenario(client: TestClient):
        assert (await post(client, "/api/manual", {"action": "speed", "speed_kmh": "2.5"})).status == 200
        assert rig.app.manual.speed_kmh == 2.5

    serve(rig, scenario)


# ---- stop / resume ------------------------------------------------------------------------


def test_emergency_stop_and_resume_over_http(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=3.2)
        response = await post(client, "/api/stop")
        assert response.status == 200 and (await response.json())["ok"] is True
        await rig.run(10, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0 and rig.app.governor.phase is Phase.USER_STOPPED
        # An empty body is fine too (the button sends "{}" or nothing).
        response = await client.post("/api/stop", headers=JSON)
        assert response.status == 200
        response = await post(client, "/api/resume")
        assert response.status == 200
        await rig.run(30, target=3.2)
        assert rig.treadmill.status.speed_kmh > 2.5

    serve(rig, scenario)


def test_emergency_stop_answers_ok_even_when_the_treadmill_command_fails(rig: Rig):
    async def scenario(client: TestClient):
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=3.2)
        rig.treadmill.fail_commands = True
        response = await post(client, "/api/stop")
        assert response.status == 200  # the latch is set whatever the radio does
        assert rig.app.governor.user_stopped
        state = await (await client.get("/api/state")).json()
        assert state["governor"]["user_stopped"] is True
        assert any(e["level"] == "error" and "stop button" in e["text"] for e in state["events"])
        # Nothing restarts it, and once commands work again the stop is repeated.
        rig.treadmill.fail_commands = False
        await rig.run(10, target=3.2)
        assert rig.treadmill.status.speed_kmh == 0

    serve(rig, scenario)


def test_emergency_stop_survives_an_unexpected_driver_error(rig: Rig):
    """Even a bug in a driver must not leave the latch unset."""

    async def scenario(client: TestClient):
        await rig.connect()
        await rig.run(1, active=False)
        await rig.run(30, target=3.2)

        async def broken_stop():
            raise TreadmillError("radio on fire")

        rig.treadmill.stop = broken_stop
        response = await post(client, "/api/stop")
        assert response.status == 200
        assert rig.app.governor.user_stopped and not rig.app.manual.active

    serve(rig, scenario)


# ---- treadmill -----------------------------------------------------------------------------


def test_treadmill_endpoints(rig: Rig):
    async def scenario(client: TestClient):
        # The simulated pad has nothing to scan for.
        response = await post(client, "/api/treadmill/scan")
        assert response.status == 200 and (await response.json()) == {"ok": True, "devices": []}

        response = await post(client, "/api/treadmill/connect", {"address": "AA:BB:CC:DD:EE:FF"})
        assert response.status == 200
        assert rig.config.treadmill.address == "AA:BB:CC:DD:EE:FF" and rig.app.want_connected
        saved = json.loads((rig.app.data_dir / "config.json").read_text("utf-8"))
        assert saved["treadmill"]["address"] == "AA:BB:CC:DD:EE:FF"
        # No body: reconnect to whatever is configured.
        response = await client.post("/api/treadmill/connect", headers=JSON)
        assert response.status == 200 and rig.config.treadmill.address == "AA:BB:CC:DD:EE:FF"

        await rig.connect()
        response = await post(client, "/api/treadmill/press", {"button": "start"})
        assert response.status == 200 and rig.treadmill.status.state is BeltState.STARTING
        response = await post(client, "/api/treadmill/press", {"button": "stop"})
        assert response.status == 200 and rig.treadmill.status.state is BeltState.STOPPED
        for bad in ({"button": "eject"}, {}, {"button": None}):
            assert (await post(client, "/api/treadmill/press", bad)).status == 400

        response = await post(client, "/api/treadmill/disconnect")
        assert response.status == 200
        assert not rig.treadmill.status.connected and not rig.app.want_connected

    serve(rig, scenario)


def test_scan_lists_devices_and_reports_failures(rig: Rig):
    from walka.treadmill.ftms_ble import FoundDevice

    async def scenario(client: TestClient):
        async def discover(timeout: float):
            return [FoundDevice("URTM030", "F4:12:FA:00:30:AB", -58, True)]

        rig.treadmill.discover = discover
        response = await post(client, "/api/treadmill/scan")
        assert (await response.json()) == {
            "ok": True,
            "devices": [{"name": "URTM030", "address": "F4:12:FA:00:30:AB", "rssi": -58, "ftms": True}],
        }

        async def broken(timeout: float):
            raise OSError("Bluetooth is switched off")

        rig.treadmill.discover = broken
        response = await post(client, "/api/treadmill/scan")
        assert response.status == 500
        body = await response.json()
        assert body["ok"] is False and "switched off" in body["error"]

    serve(rig, scenario)


def test_press_is_not_available_on_a_real_treadmill(rig: Rig):
    class NoButtons:
        status = rig.treadmill.status
        kind = "ftms"

    async def scenario(client: TestClient):
        rig.app.treadmill = NoButtons()
        assert (await post(client, "/api/treadmill/press", {"button": "start"})).status == 400

    serve(rig, scenario)


# ---- config ---------------------------------------------------------------------------------


def test_config_read_and_clamped_update(rig: Rig):
    async def scenario(client: TestClient):
        config = await (await client.get("/api/config")).json()
        assert config["limits"]["max_speed_kmh"] == rig.config.limits.max_speed_kmh
        response = await post(
            client,
            "/api/config",
            {
                "limits": {"max_speed_kmh": 99, "accel_kmh_per_s": 50, "signal_timeout_s": 0},
                "profile": {"weight_kg": 82.5, "age": 41, "sex": "female"},
                "units": "kmh",
                "bogus": {"x": 1},
            },
        )
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        assert body["config"]["limits"]["max_speed_kmh"] == MAX_SPEED_CAP_KMH  # clamped, not obeyed
        assert body["config"]["limits"]["accel_kmh_per_s"] == 1.0
        assert body["config"]["limits"]["signal_timeout_s"] == 1.0
        assert body["config"]["profile"] == {
            "weight_kg": 82.5,
            "height_cm": 172.0,
            "age": 41,
            "sex": "female",
            "step_calibration": 1.0,
        }
        assert body["config"]["units"] == "kmh" and "bogus" not in body["config"]
        assert rig.app.governor.limits.max_speed_kmh == MAX_SPEED_CAP_KMH  # the governor sees it at once
        saved = json.loads((rig.app.data_dir / "config.json").read_text("utf-8"))
        assert saved == body["config"]

    serve(rig, scenario)


@pytest.mark.parametrize(
    "raw,status",
    [
        ("{not json", 400),
        ("[1, 2, 3]", 400),
        ('"text"', 400),
        ('{"limits": {"max_speed_kmh": NaN}}', 200),
        ('{"limits": {"max_speed_kmh": Infinity}}', 200),
        ('{"web": {"port": 1e999}}', 200),
        ('{"profile": {"age": 1e999}}', 200),
        ('{"limits": "fast", "profile": 7, "wow": {"origin": "x"}, "treadmill": {"name_prefixes": 3}}', 200),
        ("{}", 200),
        ("", 200),
    ],
)
def test_config_update_with_bad_input_never_breaks_the_limits(rig: Rig, raw: str, status: int):
    async def scenario(client: TestClient):
        response = await client.post("/api/config", data=raw, headers=JSON)
        assert response.status == status, await response.text()
        limits = rig.config.limits
        assert 0.8 <= limits.max_speed_kmh <= MAX_SPEED_CAP_KMH
        assert isinstance(rig.config.web.port, int) and 1 <= rig.config.web.port <= 65535
        assert rig.config.profile.age is None
        assert rig.config.wow.origin is None and rig.config.treadmill.name_prefixes == ["URTM"]
        json.dumps(rig.app.snapshot())

    serve(rig, scenario)


# ---- history -----------------------------------------------------------------------------------


def test_sessions_list_detail_tcx_delete(rig: Rig):
    old = add_record(rig, 1_800_000_000.0, steps=111)
    new = add_record(rig, 1_800_050_000.0, steps=222, avg_hr=104.0, max_hr=120)

    async def scenario(client: TestClient):
        listing = await (await client.get("/api/sessions")).json()
        assert [s["id"] for s in listing["sessions"]] == [new.id, old.id]  # newest first
        assert "samples" not in listing["sessions"][0] and listing["sessions"][0]["avg_kmh"] == pytest.approx(3.18)
        assert len((await (await client.get("/api/sessions?limit=1")).json())["sessions"]) == 1
        for odd in ("abc", "-5", "0", "99999999", "1.5", ""):
            response = await client.get(f"/api/sessions?limit={odd}")
            assert response.status == 200 and 1 <= len((await response.json())["sessions"]) <= 2

        detail = await (await client.get(f"/api/sessions/{old.id}")).json()
        assert detail["steps"] == 111 and len(detail["samples"]) == 121

        response = await client.get(f"/api/sessions/{new.id}/tcx")
        assert response.status == 200
        assert response.content_type == "application/vnd.garmin.tcx+xml"
        assert response.headers["Content-Disposition"] == f'attachment; filename="walka-{new.id}.tcx"'
        root = ET.fromstring(await response.read())
        assert root.tag.endswith("TrainingCenterDatabase")

        for path in (f"/api/sessions/{old.id}x", "/api/sessions/nope/tcx", "/api/sessions/%2e%2e%2f%2e%2e%2fconfig"):
            assert (await client.get(path)).status == 404

        totals = (await (await client.get("/api/state")).json())["totals"]
        assert totals["all"]["sessions"] == 2
        response = await client.delete(f"/api/sessions/{old.id}", headers=JSON)
        assert response.status == 200
        assert (await client.delete(f"/api/sessions/{old.id}", headers=JSON)).status == 404
        assert (await client.delete("/api/sessions/..%2Fconfig", headers=JSON)).status == 404
        assert [r.id for r in rig.app.store.records] == [new.id]
        assert not (rig.app.data_dir / "sessions" / f"{old.id}.json").exists()
        assert (rig.app.data_dir / "sessions" / f"{new.id}.json").exists()
        # Totals and the addon's file follow straight away.
        totals = (await (await client.get("/api/state")).json())["totals"]
        assert totals["all"]["sessions"] == 1 and totals["all"]["steps"] == 222
        text = (rig.addon / "CompanionData.lua").read_text("utf-8")
        assert "steps = 222" in text and "steps = 111" not in text

    serve(rig, scenario)


def test_empty_history(rig: Rig):
    async def scenario(client: TestClient):
        assert (await (await client.get("/api/sessions")).json()) == {"sessions": []}

    serve(rig, scenario)


# ---- addon ---------------------------------------------------------------------------------------


def test_addon_sync_and_locations(rig: Rig, tmp_path: Path):
    async def scenario(client: TestClient):
        locations = await (await client.get("/api/addon/locations")).json()
        assert isinstance(locations["addons_dirs"], list) and locations["bundled"] is True

        response = await post(client, "/api/addon/sync")
        assert response.status == 200 and (await response.json()) == {"ok": True, "error": ""}
        assert (rig.addon / "CompanionData.lua").exists()

        rig.config.wow.addon_dir = str(tmp_path / "gone")
        response = await post(client, "/api/addon/sync")
        assert response.status == 409
        body = await response.json()
        assert body["ok"] is False and "not found" in body["error"]

    serve(rig, scenario)


def test_addon_install(rig: Rig, tmp_path: Path):
    addons = tmp_path / "Game" / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True)

    async def scenario(client: TestClient):
        response = await post(client, "/api/addon/install", {"addons_dir": str(addons)})
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True and Path(body["path"]) == addons / "Walka"
        assert (addons / "Walka" / "Walka.toc").exists()
        assert rig.config.wow.addon_dir == str(addons / "Walka")
        # ... and the history is synced into the fresh install.
        assert "WalkaCompanionData = {" in (addons / "Walka" / "CompanionData.lua").read_text("utf-8")

        response = await post(client, "/api/addon/install", {"addons_dir": str(tmp_path / "missing")})
        assert response.status == 409
        assert (await response.json())["ok"] is False

    serve(rig, scenario)


# ---- the road for the picture ----------------------------------------------------------------------


def install_game(rig: Rig, tmp_path: Path, monkeypatch) -> Path:
    """A game folder with the addon in it, which the companion knows of -
    and no other (the computer the tests run on may have the game)."""
    monkeypatch.setattr("walka.addon_sync.find_addons_dirs", lambda *_args, **_kwargs: [])
    game = tmp_path / "Game" / "_forever_"
    addon = game / "Interface" / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n")
    rig.config.wow.addon_dir = str(addon)
    return game


def save_road(rig: Rig, game: Path, key: str, yards: int) -> None:
    path = game / "WTF" / "Account" / "ACCOUNT" / "Realm" / "Name" / "SavedVariables" / "Walka.lua"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'\r\nWalkaCharDB = {{\r\n\t["version"] = 1,\r\n\t["key"] = "{key}",\r\n\t["segments"] = {{\r\n\t\t{{\r\n'
        f'\t\t\t["c"] = 1,\r\n\t\t\t["m"] = "w",\r\n\t\t\t["b"] = 1,\r\n\t\t\t["l"] = 12,\r\n\t\t\t["x"] = 100,\r\n'
        f'\t\t\t["y"] = -200,\r\n\t\t\t["d"] = "3,{yards},0",\r\n\t\t}}, -- [1]\r\n\t}},\r\n}}\r\n',
        encoding="utf-8",
        newline="",
    )
    os.utime(path, (rig.clock.wall - 60, rig.clock.wall - 60))


def test_the_games_folder_is_where_the_addon_is_installed(rig: Rig, tmp_path: Path, monkeypatch):
    assert tmp_path not in rig.app.game_dirs()  # (<tmp>/AddOns/Walka is no game's folder)
    game = install_game(rig, tmp_path, monkeypatch)
    assert rig.app.game_dirs() == [game]


def test_the_road_of_a_character(rig: Rig, tmp_path: Path, monkeypatch):
    game = install_game(rig, tmp_path, monkeypatch)
    save_road(rig, game, "Tester-Azeroth", 250)
    mine = f"{djb2('Tester-Azeroth'):08x}"

    async def scenario(client: TestClient):
        response = await client.get(f"/api/road?character={mine}")
        assert response.status == 200
        body = await response.json()
        assert body["game_found"] is True and body["files"] == 1 and body["demo"] is False
        road = body["road"]
        assert road["id"] == mine and road["yards"] == 250 and road["belt_yards"] == 250 and road["level"] == 12
        assert road["as_of"] == pytest.approx(rig.clock.wall - 60, abs=1)
        assert road["continents"] == [
            {
                "id": 1,
                "name": "Kalimdor",
                "yards": 250,
                "bounds": [100, -200, 350, -200],
                "paths": [{"style": "walked", "p": [100, -200, 350, -200]}],
            }
        ]
        # Nothing of where the file was found goes to the page.
        assert "ACCOUNT" not in json.dumps(body) and "Realm" not in json.dumps(body)

        # A character without a saved road: an answer, and no road.
        response = await client.get("/api/road?character=0badf00d")
        assert response.status == 200
        assert await response.json() == {"road": None, "game_found": True, "files": 1, "demo": False}

        # The game rewrites the file (a /reload): the next look has the new road.
        rig.clock.advance(10)
        save_road(rig, game, "Tester-Azeroth", 900)
        body = await (await client.get(f"/api/road?character={mine}")).json()
        assert body["road"]["yards"] == 900

    serve(rig, scenario)


@pytest.mark.parametrize(
    "query",
    ["", "?character=", "?character=Tester-Azeroth", "?character=0BADF00D", "?character=../../x", "?character=" + "a" * 17,
     "?character=12%0034", "?character=12 34", "?id=0badf00d"],
)  # fmt: skip
def test_the_road_is_asked_for_by_a_characters_id_only(rig: Rig, query: str):
    async def scenario(client: TestClient):
        response = await client.get("/api/road" + query)
        assert response.status == 400

    serve(rig, scenario)


def test_the_road_without_a_game(rig: Rig, monkeypatch):
    monkeypatch.setattr(rig.app, "game_dirs", lambda: [])
    rig.app.roads.game_dirs = rig.app.game_dirs

    async def scenario(client: TestClient):
        response = await client.get("/api/road?character=0badf00d")
        assert await response.json() == {"road": None, "game_found": False, "files": 0, "demo": False}

    serve(rig, scenario)


# ---- heart rate ------------------------------------------------------------------------------------


def test_heart_rate_endpoints_without_a_monitor(rig: Rig):
    async def scenario(client: TestClient):
        response = await post(client, "/api/heart-rate/scan")
        assert (await response.json()) == {"ok": True, "devices": []}
        assert (await post(client, "/api/heart-rate/connect", {"address": "AA"})).status == 400

    serve(rig, scenario)


def test_heart_rate_endpoints_with_a_monitor(rig: Rig):
    class Monitor:
        def __init__(self) -> None:
            self.configured = None
            self.fail = False

        async def discover(self, timeout: float):
            if self.fail:
                raise OSError("no adapter")
            return [{"name": "HRM-Dual", "address": "11:22:33:44:55:66", "rssi": -50}]

        def configure(self, *, enabled: bool, address: str) -> None:
            self.configured = (enabled, address)

        def to_dict(self) -> dict:
            return {"enabled": True}

    monitor = Monitor()
    rig.app.heart_rate = monitor

    async def scenario(client: TestClient):
        response = await post(client, "/api/heart-rate/scan")
        assert (await response.json())["devices"][0]["name"] == "HRM-Dual"
        monitor.fail = True
        assert (await post(client, "/api/heart-rate/scan")).status == 500

        response = await post(client, "/api/heart-rate/connect", {"address": "11:22:33:44:55:66", "name": "HRM-Dual"})
        assert response.status == 200
        assert monitor.configured == (True, "11:22:33:44:55:66")
        assert rig.config.heart_rate.enabled is True and rig.config.heart_rate.name == "HRM-Dual"
        response = await post(client, "/api/heart-rate/connect", {"enabled": False})
        assert response.status == 200 and monitor.configured == (False, "")
        assert rig.config.heart_rate.enabled is False

    serve(rig, scenario)


# ---- the server itself ------------------------------------------------------------------------------


def test_serve_binds_and_cleans_up(rig: Rig):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def scenario():
        import aiohttp

        runner = await web.serve(rig.app, "127.0.0.1", port)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/api/state") as response:
                    assert response.status == 200
        finally:
            await runner.cleanup()
        # The port is free again.
        with socket.socket() as again:
            again.bind(("127.0.0.1", port))

    asyncio.run(asyncio.wait_for(scenario(), 15))


# ---- quitting with a dashboard tab open ---------------------------------------------------------


def test_event_stream_ends_when_the_companion_is_stopping(rig: Rig):
    """App.stop() marks the app as stopping before anything else; the open
    streams must finish by themselves rather than wait to be killed."""

    async def scenario(client: TestClient):
        response = await client.get("/api/events")
        await asyncio.wait_for(response.content.readuntil(b"\n\n"), 5)
        rig.app._stopping.set()
        rest = await asyncio.wait_for(response.content.read(), 5)  # reads to the end of the stream
        assert rest.count(b"data: ") <= 1

    serve(rig, scenario)


@pytest.mark.parametrize("app_is_stopping", [True, False])
def test_the_server_shuts_down_promptly_with_a_dashboard_tab_open(rig: Rig, app_is_stopping: bool):
    """Ctrl+C used to take two minutes to return while a tab held the event
    stream open: a minute waiting for the request to finish and another
    after the handler swallowed its cancellation."""
    import socket

    import aiohttp

    async def scenario():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        runner = await web.serve(rig.app, "127.0.0.1", port)
        async with aiohttp.ClientSession() as session:
            response = await session.get(f"http://127.0.0.1:{port}/api/events")
            await asyncio.wait_for(response.content.readuntil(b"\n\n"), 5)
            if app_is_stopping:
                rig.app._stopping.set()  # the normal order: App.stop(), then the server
            # Otherwise the server has to end the stream itself, after its
            # (now short) grace period - and the handler must let it.
            await asyncio.wait_for(runner.cleanup(), 10)
            response.close()

    asyncio.run(asyncio.wait_for(scenario(), 30))
    assert web.SHUTDOWN_TIMEOUT_S <= 2.0


def test_no_page_of_the_dashboard_can_be_framed(rig: Rig):
    """The page is served at "/" and - being a file in the static folder -
    at /static/index.html too. Both must refuse to be framed: a framed
    dashboard could be used to trick a click onto a button that moves the
    belt."""

    async def scenario(client: TestClient):
        for path in ("/", "/static/index.html", "/static/app.js", "/api/state"):
            response = await client.get(path)
            assert response.status == 200, path
            assert response.headers["X-Frame-Options"] == "DENY", path
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"], path

    serve(rig, scenario)
