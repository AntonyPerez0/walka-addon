"""Heart rate over the local network - for watches that cannot broadcast it.

An Apple Watch or a Galaxy Watch has no built-in standard Bluetooth heart
rate broadcast. Some watch apps can instead send the heart rate to an
address on the home network. This is the receiver for those (decision 2 in
docs/SAFETY.md):

* **Off by default.** Nothing listens until it is switched on in Settings.
* **On its own port** (8788 unless changed), not the dashboard's: the
  dashboard stays on this computer only (127.0.0.1). This port is reachable
  from the local network - that is its point - and answers one thing only.
* **With a pairing code.** The only address it answers is
  ``http://<this computer>:<port>/<code>/hr``. Anything else is "not found".
  After ``MAX_WRONG`` wrong codes from one address, that address is locked
  out for ``LOCKOUT_S``.
* It takes a heart rate and nothing else: a number (``72``), JSON with one
  of the usual keys (``{"bpm": 72}``, ``heartRate``, ``heart_rate``, ``hr``,
  ``value``), or ``?bpm=72`` on a GET. Bodies over a few hundred bytes are
  refused. Nothing is sent back but "ok".

The readings go to the heart-rate monitor (``hr.HeartRateMonitor``) like
those of a Bluetooth sensor.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web

log = logging.getLogger(__name__)

DEFAULT_PORT = 8788
MAX_BODY = 512
MAX_WRONG = 10
LOCKOUT_S = 600.0
KEYS = ("bpm", "heartRate", "heart_rate", "hr", "heartrate", "value")


def new_code() -> str:
    """Six digits, easy to type into a watch app."""
    return f"{secrets.randbelow(1_000_000):06d}"


def parse_bpm(text: str) -> int | None:
    """A heart rate from what a watch app sends: a number, or JSON with a usual key."""
    text = (text or "").strip()
    if not text:
        return None
    value: Any = text
    if text[0] in "{[":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if isinstance(data, list) and data:
            data = data[-1]
        if not isinstance(data, dict):
            return None
        value = next((data[key] for key in KEYS if key in data), None)
        if isinstance(value, dict):  # {"heartRate": {"value": 72}}
            value = next((value[key] for key in KEYS if key in value), None)
    try:
        bpm = round(float(value))
    except (TypeError, ValueError):
        return None
    return bpm if 25 <= bpm <= 240 else None


def local_addresses() -> list[str]:
    """This computer's addresses on the local network (to show in the dashboard)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # (no packet is sent: it only picks the route)
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    return found


class NetworkHeartRate:
    def __init__(
        self,
        feed: Callable[[int], None],
        *,
        code: str,
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.feed = feed
        self.code = code
        self.port = port
        self.host = host
        self.clock = clock
        self.received = 0
        self.last_at: float | None = None
        self.error = ""
        self._wrong: dict[str, list[float]] = {}
        self._runner: web.AppRunner | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None

    def _locked(self, peer: str) -> bool:
        now = self.clock()
        tries = [t for t in self._wrong.get(peer, []) if now - t < LOCKOUT_S]
        self._wrong[peer] = tries
        return len(tries) >= MAX_WRONG

    def make_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)

        async def receive(request: web.Request) -> web.Response:
            peer = request.remote or "?"
            if self._locked(peer):
                raise web.HTTPTooManyRequests(text="too many wrong codes: try again later")
            if not secrets.compare_digest(request.match_info["code"], self.code):
                self._wrong.setdefault(peer, []).append(self.clock())
                raise web.HTTPNotFound()
            if request.method == "GET":
                text = request.query.get("bpm") or request.query.get("hr") or ""
            else:
                if (request.content_length or 0) > MAX_BODY:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
                text = (await request.read()).decode("utf-8", "replace")
            bpm = parse_bpm(text)
            if bpm is None:
                raise web.HTTPBadRequest(text="send a heart rate: a number, or JSON like {\"bpm\": 72}")
            self.received += 1
            self.last_at = self.clock()
            self.feed(bpm)
            return web.Response(text="ok")

        app.router.add_route("POST", "/{code}/hr", receive)
        app.router.add_route("PUT", "/{code}/hr", receive)
        app.router.add_route("GET", "/{code}/hr", receive)
        return app

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self.make_app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.host, self.port).start()
        except OSError as exc:
            await runner.cleanup()
            self.error = f"could not listen on port {self.port}: {exc.strerror or exc}"
            log.warning("heart rate over the network: %s", self.error)
            return
        self._runner = runner
        self.error = ""
        log.info("heart rate over the network: listening on port %s", self.port)

    async def stop(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    def to_dict(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "listening": self.running,
            "port": self.port,
            "addresses": local_addresses() if self.running else [],
            "received": self.received,
            "last_s": None if self.last_at is None else round(now - self.last_at, 1),
            "error": self.error,
        }
