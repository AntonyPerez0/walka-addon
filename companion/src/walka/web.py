"""The local dashboard: a small web server on 127.0.0.1.

The page (``static/index.html``) shows live state over Server-Sent Events
and talks to the app through a JSON API.

Nothing here can move a treadmill belt - the companion has no way to. But
the API shows the walker's walks, weight and height, and can change
settings and delete walks, so it is defended against other web pages open
in the same browser:

* requests must carry a ``Host`` header naming localhost or an IP address
  (blocks DNS-rebinding);
* state-changing requests must be ``application/json`` (a cross-site page
  cannot send that without a CORS preflight, which is never granted) and, if
  the browser sends ``Origin``, it must be this server.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from . import addon_sync
from .app import App
from .export import record_to_tcx

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
PUSH_INTERVAL_S = 0.5
SHUTDOWN_TIMEOUT_S = 1.0
SIMULATOR_BUTTONS = ("start", "pause", "stop", "faster", "slower")

# The dashboard is a self-contained page: it may load and talk to this server
# only, and no other page may frame it (a framed dashboard could be used to
# trick a click onto a button that deletes a walk).
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# Some Windows machines map .js or .css to text/plain in the registry. Together
# with the nosniff header below, a browser would then refuse the dashboard's own
# script and stylesheet - so pin the types the static files are served with.
for _content_type, _extension in (("text/javascript", ".js"), ("text/css", ".css"), ("image/svg+xml", ".svg")):
    mimetypes.add_type(_content_type, _extension)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _host_is_local(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip()
    port = ""
    if host.startswith("["):  # [::1]:8787
        host, _, rest = host[1:].partition("]")
        if rest and not rest.startswith(":"):
            return False  # something other than a port after the address
        port = rest[1:]
    elif host.count(":") == 1:
        host, port = host.split(":")
    if port and not port.isdigit():
        return False
    if host.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@web.middleware
async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
    if not _host_is_local(request.headers.get("Host")):
        raise web.HTTPForbidden(text="bad Host header")
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("Origin")
        if origin and origin not in (f"http://{request.headers.get('Host')}", f"https://{request.headers.get('Host')}"):
            raise web.HTTPForbidden(text="cross-origin request refused")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="send application/json")
    response = await handler(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # On every response, not only on "/": the same page is also reachable
    # as /static/index.html, and a page that can be framed can be used to
    # trick a click onto one of its buttons.
    for name, value in PAGE_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


async def _body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return data


def ok(**extra: Any) -> web.Response:
    return web.json_response({"ok": True, **extra})


def create_app(app: App) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html", headers=PAGE_HEADERS)

    @routes.get("/api/state")
    async def state(_request: web.Request) -> web.Response:
        return web.json_response(app.snapshot())

    @routes.get("/api/events")
    async def events(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)
        # The stream ends when the companion is stopping (or the server
        # cancels it): a handler that never finished would keep the program
        # from quitting for as long as a dashboard tab stayed open.
        stopping = app._stopping
        try:
            while not stopping.is_set():
                payload = json.dumps(app.snapshot(), separators=(",", ":"))
                await response.write(f"data: {payload}\n\n".encode())
                try:
                    await asyncio.wait_for(stopping.wait(), PUSH_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass
        except (ConnectionResetError, ConnectionError):
            pass
        return response

    # ---- treadmill -------------------------------------------------------------
    @routes.post("/api/treadmill/scan")
    async def scan(_request: web.Request) -> web.Response:
        discover = getattr(app.treadmill, "discover", None)
        if discover is None:
            return ok(devices=[])
        try:
            found = await discover(6.0)
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"Bluetooth scan failed: {exc}"}, status=500)
        return ok(devices=[device.to_dict() for device in found])

    @routes.post("/api/treadmill/connect")
    async def connect(request: web.Request) -> web.Response:
        data = await _body(request)
        address = data.get("address")
        app.connect(str(address) if address is not None else None)
        return ok()

    @routes.post("/api/treadmill/disconnect")
    async def disconnect(_request: web.Request) -> web.Response:
        await app.disconnect()
        return ok()

    @routes.post("/api/treadmill/forget")
    async def forget(_request: web.Request) -> web.Response:
        app.forget_treadmill()
        return ok()

    @routes.post("/api/treadmill/press")
    async def press(request: web.Request) -> web.Response:
        """The simulated treadmill's remote (--simulate, --demo), and only
        that: with a real treadmill this is refused before the reader is
        so much as looked at - and the reader has nothing to press."""
        data = await _body(request)
        button = data.get("button")
        treadmill = app.treadmill
        if treadmill.kind != "simulated" or button not in SIMULATOR_BUTTONS:
            raise web.HTTPBadRequest(text="not available on this treadmill")
        getattr(treadmill, f"press_{button}")()
        return ok()

    # ---- config ----------------------------------------------------------------
    @routes.get("/api/config")
    async def get_config(_request: web.Request) -> web.Response:
        return web.json_response(app.config.to_dict())

    @routes.post("/api/config")
    async def set_config(request: web.Request) -> web.Response:
        app.update_config(await _body(request))
        return ok(config=app.config.to_dict())

    # ---- history ---------------------------------------------------------------
    @routes.get("/api/sessions")
    async def sessions(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(2000, int(request.query.get("limit", "200"))))
        except ValueError:
            limit = 200
        return web.json_response({"sessions": [r.summary() for r in app.store.recent(limit)]})

    @routes.get("/api/sessions/{session_id}")
    async def session(request: web.Request) -> web.Response:
        record = app.store.get(request.match_info["session_id"])
        if record is None:
            raise web.HTTPNotFound()
        return web.json_response(record.to_json())

    @routes.get("/api/sessions/{session_id}/tcx")
    async def session_tcx(request: web.Request) -> web.Response:
        record = app.store.get(request.match_info["session_id"])
        if record is None:
            raise web.HTTPNotFound()
        return web.Response(
            text=record_to_tcx(
                record,
                height_cm=app.config.profile.height_cm,
                step_calibration=app.config.profile.step_calibration,
            ),
            content_type="application/vnd.garmin.tcx+xml",
            headers={"Content-Disposition": f'attachment; filename="walka-{record.id}.tcx"'},
        )

    @routes.delete("/api/sessions/{session_id}")
    async def delete_session(request: web.Request) -> web.Response:
        if not app.store.delete(request.match_info["session_id"]):
            raise web.HTTPNotFound()
        app._totals_cache = None
        app.sync_addon()
        return ok()

    # ---- journey (the Share page) ------------------------------------------------
    @routes.get("/api/journey")
    async def journey(_request: web.Request) -> web.Response:
        return web.json_response(app.journeys.to_api())

    # ---- addon -----------------------------------------------------------------
    @routes.get("/api/road")
    async def road(request: web.Request) -> web.Response:
        """The road of one character, for the picture (see road.py)."""
        wanted = request.query.get("character", "")
        if not re.fullmatch(r"[0-9a-f]{1,16}", wanted):
            raise web.HTTPBadRequest(text="character: the id of a character from /api/journey")
        # Finding and reading the game's files can take a moment: never on
        # the loop that records the walk.
        return web.json_response(await asyncio.to_thread(app.roads.for_character, wanted))

    @routes.get("/api/addon/locations")
    async def addon_locations(_request: web.Request) -> web.Response:
        found = [str(path) for path in addon_sync.find_addons_dirs()]
        return web.json_response({"addons_dirs": found, "bundled": addon_sync.bundled_addon_dir() is not None})

    @routes.post("/api/addon/sync")
    async def addon_sync_now(_request: web.Request) -> web.Response:
        success = app.sync_addon()
        return web.json_response({"ok": success, "error": app.addon_error}, status=200 if success else 409)

    @routes.post("/api/addon/install")
    async def addon_install(request: web.Request) -> web.Response:
        data = await _body(request)
        target = data.get("addons_dir")
        try:
            installed = app.install_addon(str(target) if target else None)
        except (OSError, FileNotFoundError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=409)
        return ok(path=str(installed))

    # ---- heart rate ------------------------------------------------------------
    @routes.post("/api/heart-rate/scan")
    async def hr_scan(_request: web.Request) -> web.Response:
        monitor = app.heart_rate
        if monitor is None:
            return ok(devices=[])
        try:
            found = await monitor.discover(6.0)
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"Bluetooth scan failed: {exc}"}, status=500)
        return ok(devices=found)

    @routes.post("/api/heart-rate/connect")
    async def hr_connect(request: web.Request) -> web.Response:
        data = await _body(request)
        monitor = app.heart_rate
        if monitor is None:
            raise web.HTTPBadRequest(text="heart-rate support is not available")
        address = str(data.get("address") or "")
        enabled = bool(data.get("enabled", True))
        app.update_config({"heart_rate": {"enabled": enabled, "address": address, "name": str(data.get("name") or "")}})
        monitor.configure(enabled=enabled, address=address)
        return ok()

    application = web.Application(middlewares=[guard])
    application.add_routes(routes)
    if STATIC_DIR.is_dir():
        application.router.add_static("/static/", STATIC_DIR, show_index=False)
    return application


async def serve(app: App, host: str, port: int) -> web.AppRunner:
    """Start the dashboard server; returns the runner (call ``cleanup()`` to stop)."""
    # shutdown_timeout: how long quitting waits for requests in flight (the
    # default is a minute; nothing here takes that long to finish).
    runner = web.AppRunner(create_app(app), access_log=None, shutdown_timeout=SHUTDOWN_TIMEOUT_S)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
