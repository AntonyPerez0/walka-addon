"""Feeding data back into the game.

A WoW addon cannot read files or receive anything from outside while the
game runs. What it *can* do is load Lua files from its own folder at login
and on ``/reload`` - the same trick TradeSkillMaster, WeakAuras Companion
and Raider.IO use. So the companion writes its measured sessions and the
body profile to ``Walka/CompanionData.lua`` as one global table, and the
addon merges it into its history next time the UI loads.

This module also finds the game's AddOns folder and installs the addon.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .session import SessionRecord, SessionStore

log = logging.getLogger(__name__)

ADDON_NAME = "Walka"
DATA_FILE = "CompanionData.lua"
DATA_GLOBAL = "WalkaCompanionData"
DATA_VERSION = 1

# How many recent walks are written out in full; older ones are summarised.
MAX_SESSIONS_IN_FILE = 300

# Game folders ("flavours") inside a World of Warcraft install, most
# relevant first. WoW: Forever lives in _forever_.
FLAVOURS = ("_forever_", "_retail_", "_classic_era_", "_classic_", "_anniversary_")

_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in",
    "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while",
}  # fmt: skip


# ---- Lua serialisation ---------------------------------------------------------


def _lua_string(text: str) -> str:
    out = ['"']
    # (A Bluetooth name can hold what UTF-8 cannot: a lone surrogate from
    # the operating system's stack. It must not stop the file being written.)
    for byte in text.encode("utf-8", errors="replace"):
        char = chr(byte)
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif byte < 32 or byte == 127:
            out.append(f"\\{byte:03d}")
        elif byte < 128:
            out.append(char)
        else:
            # Lua strings are byte strings: keep UTF-8 bytes as escapes so the
            # file stays pure ASCII whatever the editor or code page.
            out.append(f"\\{byte:03d}")
    out.append('"')
    return "".join(out)


def _lua_number(value: float | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "0"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 6))


def _lua_key(key: Any) -> str:
    if isinstance(key, str):
        if key.isidentifier() and key.isascii() and key not in _LUA_KEYWORDS:
            return key
        return f"[{_lua_string(key)}]"
    if isinstance(key, bool):
        raise TypeError("boolean table keys are not supported")
    if isinstance(key, (int, float)):
        return f"[{_lua_number(key)}]"
    raise TypeError(f"unsupported Lua table key: {key!r}")


def to_lua(value: Any, indent: int = 0) -> str:
    """Serialise Python data as a Lua literal. ``None`` values are omitted."""
    pad = "\t" * indent
    inner = "\t" * (indent + 1)
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _lua_number(value)
    if isinstance(value, str):
        return _lua_string(value)
    if isinstance(value, dict):
        items = [(k, v) for k, v in value.items() if v is not None]
        if not items:
            return "{}"
        lines = [f"{inner}{_lua_key(k)} = {to_lua(v, indent + 1)}," for k, v in items]
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "{}"
        # A nil inside an array would leave a hole; use false instead.
        lines = [f"{inner}{to_lua(False if v is None else v, indent + 1)}," for v in value]
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    raise TypeError(f"cannot serialise {type(value).__name__} to Lua")


# ---- payload -------------------------------------------------------------------


def _session_entry(record: SessionRecord) -> dict[str, Any]:
    return {
        "start": int(record.start),
        "end": int(math.ceil(record.end)),
        "seconds": int(round(record.seconds)),
        "distanceM": int(round(record.distance_m)),
        "steps": int(record.steps),
        "kcal": round(record.kcal, 1),
        "maxKmh": round(record.max_kmh, 2),
        "avgHr": round(record.avg_hr) if record.avg_hr else None,
        "maxHr": record.max_hr,
        "source": record.source,
        "gameSession": record.game_session,
        "stepsSource": record.steps_source,
    }


def build_payload(
    records: Iterable[SessionRecord],
    *,
    profile: dict[str, Any],
    treadmill_name: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Build the table written to ``CompanionData.lua``.

    ``records`` are oldest first. The newest ``MAX_SESSIONS_IN_FILE`` are
    listed; the rest are folded into ``older`` so all-time totals stay right.
    """
    ordered = sorted(records, key=lambda r: r.start)
    listed = ordered[-MAX_SESSIONS_IN_FILE:]
    older = ordered[: len(ordered) - len(listed)]
    payload: dict[str, Any] = {
        "version": DATA_VERSION,
        "written": int(time.time() if now is None else now),
        "profile": profile,
        "treadmill": treadmill_name or None,
        "sessions": [_session_entry(r) for r in reversed(listed)],  # newest first
    }
    if older:
        payload["older"] = {
            "sessions": len(older),
            "seconds": int(round(sum(r.seconds for r in older))),
            "distanceM": int(round(sum(r.distance_m for r in older))),
            "steps": int(sum(r.steps for r in older)),
            "kcal": round(sum(r.kcal for r in older), 1),
            # Everything that began before this moment is in the sum above:
            # the addon leaves its own records of those walks out, instead
            # of counting them a second time as estimates.
            "until": int(listed[0].start),
        }
    return payload


def render_data_file(payload: dict[str, Any]) -> str:
    written = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(payload.get("written", time.time())))
    return (
        "-- Written by the Walka companion app. Do not edit: it is replaced after\n"
        "-- every walk. The game loads it at login and on /reload.\n"
        f"-- {written}\n"
        f"{DATA_GLOBAL} = {to_lua(payload)}\n"
    )


def write_data_file(addon_dir: Path, payload: dict[str, Any]) -> Path:
    """Atomically replace ``<addon_dir>/CompanionData.lua``."""
    addon_dir = Path(addon_dir)
    if not (addon_dir / f"{ADDON_NAME}.toc").exists():
        raise FileNotFoundError(f"{addon_dir} does not look like the Walka addon folder (no {ADDON_NAME}.toc)")
    target = addon_dir / DATA_FILE
    tmp = addon_dir / (DATA_FILE + ".tmp")
    tmp.write_text(render_data_file(payload), encoding="utf-8", newline="\n")
    last_error: OSError | None = None
    for _ in range(5):
        try:
            os.replace(tmp, target)
            return target
        except OSError as exc:  # the game may have the file open for a moment
            last_error = exc
            time.sleep(0.2)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last_error if last_error else OSError("could not write the data file")


def sync(
    addon_dir: Path,
    store: SessionStore,
    *,
    profile: dict[str, Any],
    treadmill_name: str = "",
) -> Path:
    """Write the current history and profile for the addon."""
    payload = build_payload(store.records, profile=profile, treadmill_name=treadmill_name)
    return write_data_file(addon_dir, payload)


# ---- finding the game ----------------------------------------------------------


def _registry_roots() -> list[Path]:
    if sys.platform != "win32":
        return []
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        return roots
    # WoW: Forever registers its own key; the others are retail's (a shared
    # install has the same root folder).
    keys = (
        r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft Forever",
        r"SOFTWARE\Blizzard Entertainment\World of Warcraft Forever",
        r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
        r"SOFTWARE\Blizzard Entertainment\World of Warcraft",
    )
    for key_path in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "InstallPath")
        except OSError:
            continue
        path = Path(str(value).rstrip("\\/"))
        # InstallPath points at a flavour folder (…\World of Warcraft\_retail_).
        roots.append(path.parent if path.name.startswith("_") and path.name.endswith("_") else path)
    return roots


def _common_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        for drive in "CDEFG":
            for base in (
                r"Program Files (x86)\World of Warcraft",
                r"Program Files\World of Warcraft",
                r"World of Warcraft",
                r"Games\World of Warcraft",
                r"Blizzard\World of Warcraft",
            ):
                roots.append(Path(f"{drive}:\\{base}"))
    else:
        # Where Battle.net puts the game on a Mac, for everyone or for one user.
        roots.append(Path("/Applications/World of Warcraft"))
        roots.append(Path.home() / "Applications" / "World of Warcraft")
    return roots


def game_dir_from_exe(exe_path: str | os.PathLike[str]) -> Path:
    """The game folder (the one with ``Interface`` and ``WTF``) of a running
    game, from its executable's path.

    On Windows the executable is in that folder
    (``…\\World of Warcraft\\_forever_\\WowForever.exe``). On a Mac it is
    inside an application bundle that is
    (``…/World of Warcraft/_retail_/World of Warcraft.app/Contents/MacOS/World of Warcraft``).
    """
    path = Path(exe_path)
    for parent in path.parents:
        if parent.suffix.lower() == ".app":
            return parent.parent
    return path.parent


def addons_dir_from_exe(exe_path: str | os.PathLike[str] | None) -> Path | None:
    """AddOns folder of a running game, from its executable's path."""
    if not exe_path:
        return None
    candidate = game_dir_from_exe(exe_path) / "Interface" / "AddOns"
    return candidate if candidate.is_dir() else None


def find_addons_dirs(extra_roots: Iterable[Path] = ()) -> list[Path]:
    """Every ``Interface/AddOns`` folder found, most relevant flavour first."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in [*extra_roots, *_registry_roots(), *_common_roots()]:
        for flavour in FLAVOURS:
            candidate = Path(root) / flavour / "Interface" / "AddOns"
            key = str(candidate).lower()
            if key not in seen and candidate.is_dir():
                seen.add(key)
                found.append(candidate)
    found.sort(key=lambda p: FLAVOURS.index(p.parent.parent.name) if p.parent.parent.name in FLAVOURS else 99)
    return found


def find_addon_dir(configured: str | None = None, exe_path: str | None = None) -> Path | None:
    """The installed ``Walka`` addon folder, or ``None``.

    Order: the configured path, the running game's folder, then known
    install locations.
    """
    if configured:
        path = Path(configured)
        if path.name != ADDON_NAME and (path / ADDON_NAME).is_dir():
            path = path / ADDON_NAME
        if (path / f"{ADDON_NAME}.toc").exists():
            return path
    candidates: list[Path] = []
    from_exe = addons_dir_from_exe(exe_path)
    if from_exe is not None:
        candidates.append(from_exe)
    candidates.extend(find_addons_dirs())
    for addons in candidates:
        path = addons / ADDON_NAME
        if (path / f"{ADDON_NAME}.toc").exists():
            return path
    return None


# Where the addon sits inside the packaged executable. Not at the top level:
# on a file system that ignores case, "Walka" there would be the very folder
# that holds this package's own files ("walka"), dashboard and all.
BUNDLED_ADDON_PATH = ("_addon", ADDON_NAME)

# What an addon is made of. Anything else in the source folder - a
# half-written temp file, files that are not the addon's at all - stays out
# of the game's AddOns folder.
ADDON_FILE_SUFFIXES = frozenset(
    {".toc", ".lua", ".xml", ".tga", ".blp", ".png", ".jpg", ".ttf", ".otf", ".ogg", ".mp3", ".wav", ".txt", ".md"}
)


def game_dirs(configured: str | None = None) -> list[Path]:
    """Folders of installed games (the ones that hold ``WTF``): where the
    addon is installed first, then every other install found."""
    folders: list[Path] = []
    installed = find_addon_dir(configured)
    candidates = [installed.parent] if installed is not None else []
    candidates += find_addons_dirs()
    for addons in candidates:
        # <game>/Interface/AddOns
        if addons.parent.name.lower() == "interface" and addons.parent.parent not in folders:
            folders.append(addons.parent.parent)
    return folders


def bundled_addon_dir() -> Path | None:
    """The copy of the addon shipped with the companion (for installing)."""
    candidates = []
    frozen_root = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle
    if frozen_root:
        # Only where the build puts it: no looking around the user's disk.
        candidates.append(Path(frozen_root).joinpath(*BUNDLED_ADDON_PATH))
    else:
        here = Path(__file__).resolve()
        candidates.append(here.parent.joinpath(*BUNDLED_ADDON_PATH))
        for parent in here.parents:
            candidates.append(parent / ADDON_NAME)  # running from a source checkout
    for candidate in candidates:
        if (candidate / f"{ADDON_NAME}.toc").exists():
            return candidate
    return None


def addon_files(source: Path) -> list[Path]:
    """The addon's own files under ``source``, relative to it."""
    return sorted(
        path.relative_to(source)
        for path in Path(source).rglob("*")
        if path.is_file() and path.suffix.lower() in ADDON_FILE_SUFFIXES
    )


def install_addon(addons_dir: Path, source: Path | None = None) -> Path:
    """Copy the addon into an ``Interface/AddOns`` folder.

    An existing ``CompanionData.lua`` (your synced history) is kept.
    """
    source = source or bundled_addon_dir()
    if source is None:
        raise FileNotFoundError("the addon files are not bundled with this build")
    addons_dir = Path(addons_dir)
    if not addons_dir.is_dir():
        raise FileNotFoundError(f"{addons_dir} is not a folder")
    target = addons_dir / ADDON_NAME
    kept: bytes | None = None
    data_path = target / DATA_FILE
    if data_path.exists():
        kept = data_path.read_bytes()
    target.mkdir(exist_ok=True)
    for relative in addon_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source) / relative, destination)
    if kept is not None:
        data_path.write_bytes(kept)
    return target
