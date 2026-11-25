""""Check my treadmill": the probe run from the dashboard.

The probe itself is tested in test_probe.py; here, what the companion does
around it: let go of the treadmill, run it, save the filtered report and the
recording, hand them out for download (and nothing else), and connect again.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_app_ftms import PadRig
from walka import ftms, web
from walka.treadmill_check import CheckState, report_file, run_check

JSON = {"Content-Type": "application/json"}


def stub_probe(seen: dict):
    """Stands in for probe.probe: fills the report and the recording as the
    real one does, and notes whether Walka still held the treadmill."""

    async def probe(*, address, name_prefixes, listen_s, scan_s, emit, recording, report):
        seen["address"] = address
        seen["treadmill_connected"] = seen["rig"].treadmill.status.connected
        report.append("Walka Bluetooth probe - test")
        # As in the probe: the line goes into the report, then the screen's version is shown.
        report.append("Connecting to F4:12:FA:..:..:.. (URTM030) ...")
        emit("Connecting to F4:12:FA:00:30:AB (URTM030) ...")  # (the screen's line: not kept)
        recording.name, recording.address = "URTM030", "F4:12:FA:..:..:.."
        recording.add_characteristic(ftms.FTMS_SERVICE, ftms.TREADMILL_DATA, ["notify"])
        recording.add_notification(0.5, ftms.TREADMILL_DATA, bytes.fromhex("8404600000000000ffffff0000"))
        await asyncio.sleep(0)
        seen["progress"] = seen["rig"].app.check.line
        report.append("Done.")
        return report

    return probe


def test_the_check_lets_go_of_the_treadmill_runs_and_connects_again(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)
    seen: dict = {"rig": rig}
    rig.app.probe_fn = stub_probe(seen)

    async def scenario():
        rig.app.connect()
        await rig.app.treadmill.connect()
        assert rig.treadmill.status.connected
        rig.app.start_check()
        assert rig.app.snapshot()["check"]["running"]
        assert not rig.app.connection_wanted(), "the probe holds the one connection while it runs"
        with pytest.raises(ValueError, match="already running"):
            rig.app.start_check()
        await rig.app._check_task

    asyncio.run(scenario())
    assert seen["treadmill_connected"] is False
    assert seen["progress"] == "Connecting to F4:12:FA:..:..:.. (URTM030) ...", "progress is the filtered report"
    check = rig.app.snapshot()["check"]
    assert not check["running"] and not check["error"]
    assert check["report"].startswith("walka-probe-") and check["report"].endswith(".txt")
    assert check["recording"] == check["report"].replace(".txt", ".json") and check["notifications"] == 1
    report = (rig.app.reports_dir / check["report"]).read_text("utf-8")
    assert "F4:12:FA:00:30:AB" not in report and "URTM030" in report
    assert rig.app.want_connected and rig.app.connection_wanted(), "Walka looks for the treadmill again"
    assert rig.pad.writes == []


def test_the_check_is_for_a_real_treadmill(tmp_path):
    from test_app import Rig

    rig = Rig(tmp_path)
    with pytest.raises(ValueError, match="simulated"):
        rig.app.start_check()


def test_a_failed_check_says_why_and_still_connects_again(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)

    async def broken(**kwargs):
        kwargs["report"].append("Walka Bluetooth probe - test")
        raise TimeoutError()

    rig.app.probe_fn = broken

    async def scenario():
        rig.app.connect()
        rig.app.start_check()
        await rig.app._check_task

    asyncio.run(scenario())
    check = rig.app.check
    assert "did not finish" in check.error and "timed out" in check.error
    assert check.report, "what was heard before the failure is kept"
    assert rig.app.connection_wanted()


def test_nothing_found_is_an_error_in_words(tmp_path):
    async def probe(**kwargs):
        kwargs["report"].append("No treadmill found. Is it switched on and in range?")
        return kwargs["report"]

    state = CheckState()
    asyncio.run(run_check(state, tmp_path, address=None, name_prefixes=(), probe_fn=probe))
    assert state.error.startswith("No treadmill was found") and not state.recording


@pytest.mark.parametrize(
    "name",
    ["", "../config.json", "walka-probe-1/../../x.txt", "config.json", "walka-probe-1.exe", "walka-probe-none.txt"],
)
def test_only_saved_reports_are_handed_out(tmp_path, name):
    (tmp_path / "config.json").write_text("{}")
    assert report_file(tmp_path, name) is None


def test_the_reports_can_be_downloaded_and_nothing_else(tmp_path, monkeypatch):
    rig = PadRig(tmp_path, monkeypatch)
    seen: dict = {"rig": rig}
    rig.app.probe_fn = stub_probe(seen)

    async def main():
        async with TestClient(TestServer(web.create_app(rig.app))) as client:
            response = await client.post("/api/treadmill/check", data="{}", headers=JSON)
            assert response.status == 200
            await rig.app._check_task
            check = rig.app.check
            response = await client.get(f"/api/treadmill/check/{check.report}")
            assert response.status == 200 and "attachment" in response.headers["Content-Disposition"]
            assert "URTM030" in await response.text()
            response = await client.get(f"/api/treadmill/check/{check.recording}")
            assert json.loads(await response.text())["format"] == "walka-recording"
            for bad in ("config.json", "..%2Fconfig.json", "walka-probe-x.txt"):
                assert (await client.get(f"/api/treadmill/check/{bad}")).status == 404

    asyncio.run(asyncio.wait_for(main(), 30))
