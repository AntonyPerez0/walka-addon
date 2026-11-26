"""Updates: Walka says when a new version is out, and installs it in one click.

**The check.** Walka asks GitHub for the newest release of Walka - at
start, then once a day while it runs - and the dashboard says when there is
a newer one. That is the only thing asked, and the only place it goes:
``api.github.com``, with no cookie, no account and nothing about the
person (the request says "Walka" and the version, as GitHub asks every
program to). It is on in the packaged program unless switched off in
Settings; switched off, nothing is asked, ever. (Run from its source code,
Walka asks only when it is switched on there: such a copy is updated with
git, and its tests and demos ask nobody.)

**The update.** Nothing is downloaded until "Update now" is pressed. Then
Walka downloads, from that release on GitHub, the one file for this kind of
computer and the release's ``SHA256SUMS.txt``, and checks the file against
its checksum. Only a file that matches is used:

* on Windows, ``Walka.exe`` is renamed ``Walka.exe.old`` (Windows lets a
  running program be renamed, not overwritten) and the new one is put in
  its place;
* on a Mac, the zip is unpacked beside ``Walka.app``, its signature
  checked, and the two applications are swapped once Walka has stopped.

Walka then closes the way it always does - the walk being recorded is saved
first - and starts again as the new version, which tidies away the old
file. It only offers this while the belt is standing still.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

REPOSITORY = "AntonyPerez0/walka-addon"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
# Where a release's files are, and the only place they are taken from.
DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"
# GitHub hands a release's file out from one of its own hosts.
DOWNLOAD_HOSTS = ("github.com", ".githubusercontent.com")
CHECK_EVERY_S = 24 * 3600.0
RETRY_AFTER_S = 3600.0  # after a failed check
TIMEOUT_S = 15.0
DOWNLOAD_READ_TIMEOUT_S = 60.0  # (no byte for this long: the download has stalled)
MAC_ROOTS = "/etc/ssl/cert.pem"

# The files of a release (the "Publish release" job of .github/workflows/ci.yml).
WINDOWS_FILE = "Walka.exe"
MAC_FILES = {"arm64": "Walka-mac-apple-silicon.zip", "x86_64": "Walka-mac-intel.zip"}
SUMS_FILE = "SHA256SUMS.txt"
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024

# In the data folder: written just before the restart, read by the new version.
DONE_FILE = "updated.json"
# Beside Walka.app: where the new one is unpacked.
MAC_WORK_DIR = ".walka-update"

Fetch = Callable[[], Awaitable[dict[str, Any]]]
Progress = Callable[[int], None]
Download = Callable[[str, Path, int, Progress], Awaitable[None]]


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """ "v0.9.2" / "0.10.0" -> (0, 9, 2) / (0, 10, 0). A trial ("-rc1") or anything else: None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (text or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(latest: str | None, current: str = __version__) -> bool:
    """Is ``latest`` (never a trial: see parse_version) newer than this
    Walka? A trial that runs ("0.13.0-rc1") is older than its own final
    release, which is offered to it."""
    new = parse_version(latest)
    trial = re.fullmatch(r"v?(\d+\.\d+(?:\.\d+)?)-[0-9A-Za-z.]+", (current or "").strip())
    now = parse_version(trial.group(1) if trial else current)
    if new is None or now is None:
        return False
    return new > now or (trial is not None and new == now)


def is_packed() -> bool:
    """The packaged program (Walka.exe, Walka.app), not Walka run from its source code."""
    return bool(getattr(sys, "frozen", False))


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


def _headers() -> dict[str, str]:
    return {"User-Agent": f"Walka/{__version__}"}


def _assets(data: Any) -> dict[str, dict[str, Any]]:
    """A release's files, by name: only those under this repository's own
    download address, with their size."""
    found: dict[str, dict[str, Any]] = {}
    for asset in data if isinstance(data, list) else []:
        if not isinstance(asset, dict):
            continue
        name, url, size = asset.get("name"), asset.get("browser_download_url"), asset.get("size")
        if isinstance(name, str) and isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX):
            found[name] = {"url": url, "size": size if isinstance(size, int) and size > 0 else 0}
    return found


async def fetch_latest() -> dict[str, Any]:
    """The newest release, as GitHub describes it: {"tag", "url", "assets"}."""
    import aiohttp

    context = await asyncio.to_thread(_ssl_context)
    headers = {"Accept": "application/vnd.github+json", **_headers()}
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
    return {"tag": tag, "url": url, "assets": _assets(data.get("assets"))}


def _from_github(url: Any) -> bool:
    """Is ``url`` (a yarl URL, where a download ended up) one of GitHub's own hosts, over https?"""
    host = (getattr(url, "host", None) or "").lower()
    return getattr(url, "scheme", "") == "https" and any(
        host == h if not h.startswith(".") else host.endswith(h) for h in DOWNLOAD_HOSTS
    )


async def download(url: str, path: Path, max_bytes: int, progress: Progress) -> None:
    """Fetch one file of a release into ``path``. GitHub sends it on to its
    file host: that is followed, and nowhere else."""
    import aiohttp

    if not url.startswith(DOWNLOAD_PREFIX):
        raise RuntimeError("not a file of Walka's releases")
    context = await asyncio.to_thread(_ssl_context)
    timeout = aiohttp.ClientTimeout(total=None, connect=TIMEOUT_S, sock_read=DOWNLOAD_READ_TIMEOUT_S)
    async with aiohttp.ClientSession(
        timeout=timeout, headers=_headers(), cookie_jar=aiohttp.DummyCookieJar()
    ) as session:
        async with session.get(url, ssl=context, proxy=_proxy(), max_redirects=5) as response:
            if not _from_github(response.url) or any(not _from_github(r.url) for r in response.history):
                raise RuntimeError("the download was sent somewhere other than GitHub")
            if response.status != 200:
                raise RuntimeError(f"GitHub answered {response.status}")
            received = 0
            with open(path, "wb") as file:
                async for chunk in response.content.iter_chunked(256 * 1024):
                    received += len(chunk)
                    if received > max_bytes:
                        raise RuntimeError("the download is larger than it should be")
                    await asyncio.to_thread(file.write, chunk)
                    progress(received)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """``sha256sum``'s lines - "<64 hex>  <name>" - as {name: checksum}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if match:
            sums[match.group(2).strip()] = match.group(1).lower()
    return sums


# ---- this copy of Walka, and how it is replaced ---------------------------------------------


def release_file(system: str = sys.platform, machine: str | None = None) -> str | None:
    """The file of a release that is this kind of computer's Walka."""
    if system == "win32":
        return WINDOWS_FILE
    if system == "darwin":
        machine = machine if machine is not None else platform.machine()
        return MAC_FILES.get(machine.lower())
    return None


@dataclass
class Installed:
    """Where this Walka is: the program file on Windows, the application on a Mac."""

    system: str
    path: Path


def this_install(
    *, packed: bool | None = None, executable: str | None = None, system: str = sys.platform
) -> tuple[Installed | None, str]:
    """This copy of Walka - or, if it cannot update itself, why not (in words)."""
    if not (is_packed() if packed is None else packed):
        return None, "This Walka runs from its source code: update it with git, or download the new version."
    exe = Path(executable or sys.executable).resolve()
    if system == "win32":
        return Installed("win32", exe), ""
    if system == "darwin":
        # .../Walka.app/Contents/MacOS/Walka
        app = exe.parents[2] if len(exe.parents) > 2 else exe
        if app.suffix != ".app" or exe.parent.name != "MacOS":
            return None, "Walka is not in an application folder (Walka.app) that can be replaced."
        if "AppTranslocation" in app.parts:
            return None, (
                "macOS runs this Walka from a hidden copy, so it cannot replace it. Move Walka to the "
                "Applications folder and open it from there - or download the new version."
            )
        return Installed("darwin", app), ""
    return None, "Walka updates itself on Windows and on a Mac only."


def _check_writable(folder: Path) -> None:
    probe = folder / f".walka-write-check-{os.getpid()}"
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise RuntimeError(
            f"Walka cannot write to its own folder ({folder}): download the new version instead."
        ) from exc
    probe.unlink(missing_ok=True)


def _windows_paths(exe: Path) -> tuple[Path, Path]:
    """Where the new program is downloaded to, and where the old one goes."""
    return exe.with_name(exe.name + ".update"), exe.with_name(exe.name + ".old")


def _swap_windows(exe: Path, new: Path) -> None:
    """The new program into the old one's place. The old one is running:
    Windows does not let it be overwritten or removed, but renamed."""
    _, old = _windows_paths(exe)
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)  # put back as it was
        raise


def _run_quietly(command: Sequence[str]) -> None:
    done = subprocess.run(list(command), capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise RuntimeError(f"{Path(command[0]).name} failed: {(done.stderr or done.stdout).strip()[:300]}")


def _unpack_mac(app: Path, archive: Path, work: Path) -> Path:
    """The new Walka.app, unpacked beside the old one (``ditto`` keeps what
    the signature covers) and its signature checked."""
    unpacked = work / "new"
    shutil.rmtree(unpacked, ignore_errors=True)
    _run_quietly(["/usr/bin/ditto", "-x", "-k", str(archive), str(unpacked)])
    apps = [p for p in unpacked.iterdir() if p.suffix == ".app" and p.is_dir()]
    if len(apps) != 1 or not (apps[0] / "Contents" / "MacOS").is_dir():
        raise RuntimeError("the download does not hold a Walka application")
    _run_quietly(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(apps[0])])
    return apps[0]


def _swap_mac(app: Path, new_app: Path, work: Path) -> None:
    """Once Walka has stopped: the new application into the old one's place."""
    old = work / "old.app"
    shutil.rmtree(old, ignore_errors=True)
    os.rename(app, old)
    try:
        os.rename(new_app, app)
    except OSError:
        os.rename(old, app)
        raise


def clean_up(installed: Installed | None = None) -> None:
    """At start: what an update left behind (the old program, a download
    that did not finish). The old program may take a moment to exit."""
    if installed is None:
        installed, _ = this_install()
    if installed is None:
        return
    if installed.system == "win32":
        stale = list(_windows_paths(installed.path))
    else:
        stale = [installed.path.parent / MAC_WORK_DIR]
    for _ in range(30):
        for path in list(stale):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                stale.remove(path)
            except OSError:
                pass
        if not stale:
            return
        time.sleep(1.0)


# ---- the restart ------------------------------------------------------------------------------


@dataclass
class Restart:
    """What is left to do once Walka has stopped: swap (on a Mac) and start the new version."""

    installed: Installed
    data_dir: Path
    from_version: str
    to_version: str
    new_app: Path | None = None  # (a Mac's)
    argv: list[str] = field(default_factory=lambda: list(sys.argv[1:]))


_pending: list[Restart] = []


def restart_when_stopped(restart: Restart) -> None:
    _pending[:] = [restart]


def pending_restart() -> Restart | None:
    return _pending[0] if _pending else None


def restart_if_updated(popen: Callable[..., Any] = subprocess.Popen) -> bool:
    """Called last, once Walka has stopped: start the new version, if an
    update is waiting for that. True if it was started."""
    restart = pending_restart()
    _pending.clear()
    if restart is None:
        return False
    installed = restart.installed
    try:
        if installed.system == "darwin" and restart.new_app is not None:
            _swap_mac(installed.path, restart.new_app, installed.path.parent / MAC_WORK_DIR)
        _write_done(restart)
        if installed.system == "win32":
            env = dict(os.environ)
            # A program PyInstaller packed, starting another copy of itself: the new one must
            # unpack itself afresh, not reuse what this one unpacked (it is removed as this one exits).
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            popen(
                [str(installed.path), *restart.argv],
                env=env,
                cwd=str(installed.path.parent),
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            command = ["/usr/bin/open", "-n", str(installed.path)]
            if restart.argv:
                command += ["--args", *restart.argv]
            popen(
                command, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception:
        log.exception("the new version of Walka could not be started: open Walka again")
        return False
    log.info("Walka %s is starting", restart.to_version)
    return True


def _write_done(restart: Restart) -> None:
    try:
        restart.data_dir.mkdir(parents=True, exist_ok=True)
        (restart.data_dir / DONE_FILE).write_text(
            json.dumps({"from": restart.from_version, "to": restart.to_version}), "utf-8"
        )
    except OSError as exc:
        log.warning("could not note the update: %s", exc)


def note_done(restart: Restart) -> None:
    """The new version is in place, and Walka is closing without restarting
    (it was closed while it waited for a walk to end): the next start says
    that it was updated, as after a restart."""
    _write_done(restart)


def take_done(data_dir: Path) -> dict[str, str] | None:
    """At start: was this Walka just updated, and from what? (Read once.)"""
    path = data_dir / DONE_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if not isinstance(data, dict) or not isinstance(data.get("from"), str):
        return None
    return {"from": data["from"], "to": str(data.get("to") or __version__)}


# ---- what the dashboard is told ----------------------------------------------------------------


@dataclass
class UpdateState:
    latest: str = ""
    url: str = ""
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)
    checked_at: float | None = None  # wall clock of the last answer
    error: str = ""
    next_at: float = 0.0  # monotonic: not before this
    # The update itself: "" (not begun) | "downloading" | "checking" | "restarting" | "failed"
    stage: str = ""
    received: int = 0
    size: int = 0
    install_error: str = ""

    def to_dict(self, enabled: bool, *, cannot_install: str = "", self_update: bool = False) -> dict[str, Any]:
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
            # Can "Update now" be pressed? If not, why not (and the page's link is the way).
            "can_install": not cannot_install,
            "cannot_install": cannot_install,
            # Can this copy of Walka replace itself at all? (Not now, perhaps: the belt is moving.)
            "self_update": self_update,
            "install": {
                "stage": self.stage,
                "progress": round(self.received / self.size, 3) if self.size else None,
                "error": self.install_error,
            },
        }


async def check(state: UpdateState, *, fetch: Fetch, wall: float, mono: float) -> None:
    """One check into ``state``. Never raises."""
    try:
        answer = await fetch()
        state.latest, state.url, state.error = answer.get("tag", ""), answer.get("url", ""), ""
        state.assets = dict(answer.get("assets") or {})
        state.checked_at = wall
        state.next_at = mono + CHECK_EVERY_S
    except Exception as exc:  # offline, GitHub down, a proxy ...
        log.info("the update check did not get an answer: %s", exc)
        state.error = "Could not reach GitHub to check for a new version. Walka tries again later."
        state.next_at = mono + RETRY_AFTER_S


async def install(
    state: UpdateState,
    installed: Installed,
    data_dir: Path,
    *,
    fetch_file: Download = download,
    machine: str | None = None,
) -> Restart:
    """Download the new version for this computer, check it, and put it in
    place (on a Mac: ready to be). Returns what is left for when Walka has
    stopped. Raises RuntimeError, in words for the dashboard."""
    name = release_file(installed.system, machine)
    asset = state.assets.get(name or "")
    sums_asset = state.assets.get(SUMS_FILE)
    if asset is None or sums_asset is None:
        raise RuntimeError("This release has no download for this computer: download it from its page.")

    if installed.system == "win32":
        folder = installed.path.parent
        target, _ = _windows_paths(installed.path)
    else:
        folder = installed.path.parent / MAC_WORK_DIR
        target = folder / (name or "Walka.zip")
    await asyncio.to_thread(_check_writable, installed.path.parent)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)

    sums_path = folder / (target.name + ".sums")
    try:
        state.stage, state.received, state.size = "downloading", 0, int(asset["size"] or 0)
        await fetch_file(sums_asset["url"], sums_path, MAX_SUMS_BYTES, lambda _n: None)
        sums = parse_sums(await asyncio.to_thread(sums_path.read_text, "utf-8", "replace"))
        expected = sums.get(name or "")
        if not expected:
            raise RuntimeError("The release does not say what its download's checksum is: nothing was changed.")

        def progress(received: int) -> None:
            state.received = received

        await fetch_file(asset["url"], target, MAX_DOWNLOAD_BYTES, progress)
        state.stage = "checking"
        if await asyncio.to_thread(sha256_of, target) != expected:
            raise RuntimeError("The download does not match the release's checksum: nothing was changed.")

        restart = Restart(installed, data_dir, __version__, state.latest.lstrip("v"))
        if installed.system == "win32":
            await asyncio.to_thread(_swap_windows, installed.path, target)
        else:
            restart.new_app = await asyncio.to_thread(_unpack_mac, installed.path, target, folder)
            await asyncio.to_thread(target.unlink, missing_ok=True)
        return restart
    except BaseException:
        # Whatever was downloaded goes; the running Walka is as it was.
        await asyncio.to_thread(_forget, target if installed.system == "win32" else folder)
        raise
    finally:
        await asyncio.to_thread(_forget, sums_path)


def _forget(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
