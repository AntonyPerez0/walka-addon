"""The update notice: off until it is switched on in Settings.

Switched on, Walka asks GitHub for the newest release of Walka - at once,
then once a day while it runs - and the dashboard says when there is a
newer one, with a link to its page. That is the only thing asked, and the
only place it goes: ``api.github.com``, with no cookie, no account and
nothing about the person (the request says "Walka" and the version, as
GitHub asks every program to). Switched off, nothing is asked, ever.

Walka never downloads or installs anything by itself: the notice is a link.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

REPOSITORY = "AntonyPerez0/walka-addon"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
CHECK_EVERY_S = 24 * 3600.0
RETRY_AFTER_S = 3600.0  # after a failed check
TIMEOUT_S = 15.0
MAC_ROOTS = "/etc/ssl/cert.pem"

Fetch = Callable[[], Awaitable[dict[str, Any]]]


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """"v0.9.2" / "0.10.0" -> (0, 9, 2) / (0, 10, 0). A trial ("-rc1") or anything else: None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (text or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(latest: str | None, current: str = __version__) -> bool:
    new, now = parse_version(latest), parse_version(current)
    return new is not None and now is not None and new > now


def _ssl_context() -> ssl.SSLContext:
    """The other side's certificate is always checked, against the system's roots."""
    if sys.platform == "darwin":
        try:
            return ssl.create_default_context(cafile=MAC_ROOTS)
        except OSError:
            pass
    return ssl.create_default_context()


def _proxy() -> str | None:
    """The system's HTTPS proxy, if it has one. (Not aiohttp's trust_env: that
    would also read ~/.netrc, and no password of anybody's belongs in this.)"""
    import urllib.request

    proxy = urllib.request.getproxies().get("https")
    return proxy if proxy and proxy.startswith(("http://", "https://")) else None


async def fetch_latest() -> dict[str, Any]:
    """The newest release, as GitHub describes it: {"tag": ..., "url": ...}."""
    import aiohttp

    context = await asyncio.to_thread(_ssl_context)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"Walka/{__version__}"}
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, cookie_jar=aiohttp.DummyCookieJar()) as session:
        async with session.get(LATEST_URL, ssl=context, allow_redirects=False, proxy=_proxy()) as response:
            if response.status != 200:
                raise RuntimeError(f"GitHub answered {response.status}")
            data = json.loads(await response.text())
    tag = str(data.get("tag_name") or "")
    url = str(data.get("html_url") or "")
    if not url.startswith(f"https://github.com/{REPOSITORY}/"):
        url = RELEASES_PAGE
    return {"tag": tag, "url": url}


@dataclass
class UpdateState:
    latest: str = ""
    url: str = ""
    checked_at: float | None = None  # wall clock of the last answer
    error: str = ""
    next_at: float = 0.0  # monotonic: not before this

    def to_dict(self, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "current": __version__,
            "latest": self.latest,
            "newer": is_newer(self.latest),
            "url": self.url or RELEASES_PAGE,
            "checked_at": self.checked_at,
            "error": self.error,
        }


async def check(state: UpdateState, *, fetch: Fetch, wall: float, mono: float) -> None:
    """One check into ``state``. Never raises."""
    try:
        answer = await fetch()
        state.latest, state.url, state.error = answer.get("tag", ""), answer.get("url", ""), ""
        state.checked_at = wall
        state.next_at = mono + CHECK_EVERY_S
    except Exception as exc:  # offline, GitHub down, a proxy ...
        log.info("the update check did not get an answer: %s", exc)
        state.error = "Could not reach GitHub to check for a new version. Walka tries again later."
        state.next_at = mono + RETRY_AFTER_S
