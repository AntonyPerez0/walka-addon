"""The dashboard in a real browser (Chromium, through Playwright).

The other tests of the page run its scripts on stand-in DOMs. These load
the page as it is served, from a real App, in Chromium, and look at what
it shows. Skipped where Node.js, Playwright or Chromium is not there (the
build machines need none of them for the rest of the tests).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from walka.app import App
from walka.config import Config
from walka.treadmill.simulated import SimulatedTreadmill
from walka.treadmill_check import CheckState

NODE = shutil.which("node")

DRIVER = r"""
const { chromium } = require("playwright");
const [url, locale, steps] = [process.argv[2], process.argv[3], JSON.parse(process.argv[4])];
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ locale });
  const out = {};
  await page.goto(url);
  for (const [name, code] of steps) {
    out[name] = await page.evaluate(`(async () => { ${code} })()`);
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((error) => { console.error(String(error)); process.exit(3); });
"""


def _browser_works() -> str:
    if NODE is None:
        return "Node.js is not installed"
    probe = subprocess.run(
        [NODE, "-e", "const {chromium}=require('playwright');chromium.launch().then(b=>b.close())"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return "" if probe.returncode == 0 else "Playwright with Chromium is not available"


@pytest.fixture(scope="module")
def browser_ready():
    why = _browser_works()
    if why:
        pytest.skip(why)


class Served:
    """An App and its dashboard, served on a port of this computer from a thread of its own."""

    def __init__(self, tmp_path: Path, treadmill=None) -> None:
        self.app = App(Config(), tmp_path / "data", treadmill=treadmill or SimulatedTreadmill(auto_run=False))
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            self.port = taken.getsockname()[1]
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        from walka import web

        asyncio.set_event_loop(self.loop)
        self.runner = self.loop.run_until_complete(web.serve(self.app, "127.0.0.1", self.port))
        self.ready.set()
        self.loop.run_forever()

    def __enter__(self) -> Served:
        self.thread.start()
        assert self.ready.wait(20)
        return self

    def __exit__(self, *exc) -> None:
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(20)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(20)

    def look(self, tmp_path: Path, steps: list[tuple[str, str]], *, locale: str = "en-US", hash_: str = "") -> dict:
        script = tmp_path / "driver.cjs"
        script.write_text(DRIVER, "utf-8")
        url = f"http://127.0.0.1:{self.port}/{hash_}"
        done = subprocess.run(
            [NODE, str(script), url, locale, json.dumps(steps)], capture_output=True, text=True, timeout=120
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])


# Waits in the page, then gives what is asked for.
def until(condition: str, then: str, seconds: float = 10) -> str:
    return (
        f"const end = Date.now() + {seconds * 1000};"
        f"while (!({condition}) && Date.now() < end) await new Promise((r) => setTimeout(r, 100));"
        f"return {then};"
    )


def test_numbers_are_read_as_the_browsers_language_writes_them(browser_ready, tmp_path):
    steps = [
        ("parse", "const m = await import('/static/settings.js');"
                  "return [m.parseNumber('10,000', {whole: true}), m.parseNumber('12.000', {whole: true}),"
                  " m.parseNumber('12.000'), m.parseNumber('82,5'), m.parseNumber('1.234,5')];"),
    ]  # fmt: skip
    with Served(tmp_path) as served:
        german = served.look(tmp_path, steps, locale="de-DE")["parse"]
        english = served.look(tmp_path, steps, locale="en-US")["parse"]
    assert german == [10000, 12000, 12000, 82.5, 1234.5]
    assert english == [10000, 12000, 12.0, 82.5, 1234.5]


def test_the_check_says_nothing_of_null_while_it_starts(browser_ready, tmp_path):
    with Served(tmp_path, treadmill=type("Pad", (SimulatedTreadmill,), {"kind": "ftms"})(auto_run=False)) as served:
        served.app.check = CheckState(running=True, started=None, line="Letting go of the treadmill ...")
        shown = served.look(
            tmp_path, [("out", until("document.getElementById('tm-check-out') && !document.getElementById("
                                     "'tm-check-out').hidden", "document.getElementById('tm-check-out').textContent"))],
        )["out"]  # fmt: skip
    assert "Checking" in shown and "null" not in shown and "undefined" not in shown


def test_the_meter_calls_a_top_speed_the_pads_only_when_the_pad_said_it(browser_ready, tmp_path):
    class Silent(SimulatedTreadmill):
        kind = "ftms"
        speed_range_known = False

    pad = Silent(auto_run=False)
    pad.status.connected, pad.status.name = True, "Some pad"
    with Served(tmp_path, treadmill=pad) as served:
        shown = served.look(
            tmp_path, [("max", until("document.getElementById('meter-max').textContent",
                                     "document.getElementById('meter-max').textContent"))],
        )["max"]  # fmt: skip
    assert shown and "top speed" not in shown


def test_the_network_heart_rate_port_is_no_number_of_steps(browser_ready, tmp_path):
    with Served(tmp_path) as served:
        shown = served.look(
            tmp_path,
            [("unit", until("document.querySelector('form[data-form=hrnet] .unit')",
                            "[...document.querySelectorAll('form[data-form=hrnet] .unit')].map((u) => u.textContent)"))],
            hash_="#settings",
        )["unit"]  # fmt: skip
    assert shown and "steps" not in shown


def test_reopening_the_guide_shows_the_units_that_are_set(browser_ready, tmp_path):
    with Served(tmp_path) as served:
        served.app.config.units = "kmh"
        served.app.config.welcome_done = True
        served.app.system_units = lambda: "mph"
        before = served.app.config.units
        steps = [
            ("opened", until("document.getElementById('wz-next') && document.getElementById('wz-next').offsetParent",
                             "true")),
            ("next", "document.getElementById('wz-next').click(); return true;"),
            ("picked", until("document.querySelector('input[name=wz-units]:checked')",
                             "document.querySelector('input[name=wz-units]:checked').value")),
            # Next again, without touching the choice: nothing is changed by it.
            ("next again", "document.getElementById('wz-next').click();"
                           "await new Promise((r) => setTimeout(r, 1000)); return true;"),
        ]  # fmt: skip
        shown = served.look(tmp_path, steps, hash_="#welcome")["picked"]
    assert shown == "kmh" and served.app.config.units == before


def test_a_failing_list_is_asked_for_again_less_and_less_often(browser_ready, tmp_path):
    steps = [
        ("waits", "const { Backoff } = await import('/static/util.js'); const b = new Backoff();"
                  "const gaps = []; let now = 0;"
                  "for (let i = 0; i < 7; i++) { b.failed(now); gaps.push(b.at - now); now = b.at; }"
                  "const due = [b.due(now - 1), b.due(now)]; b.ok(); return [gaps, due, b.due(0)];"),
    ]  # fmt: skip
    with Served(tmp_path) as served:
        gaps, due, after = served.look(tmp_path, steps)["waits"]
    assert gaps == [1000, 2000, 4000, 8000, 16000, 30000, 30000] and due == [False, True] and after is True
