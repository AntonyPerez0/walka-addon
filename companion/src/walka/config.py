"""Companion configuration: a small JSON file in the user's data folder."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .metrics import STEP_CALIBRATION_RANGE, Profile
from .treadmill.known import DEFAULT_NAME_PREFIXES, LEGACY_NAME_PREFIXES
from .treadmill.readers import KINDS as READER_KINDS

log = logging.getLogger(__name__)

CONFIG_FILE = "config.json"

def default_data_dir() -> Path:
    """Where config and recorded walks live.

    ``%APPDATA%\\Walka`` on Windows, ``~/Library/Application Support/Walka``
    on macOS, ``~/.config/walka`` elsewhere; ``WALKA_DATA_DIR`` overrides
    them all.
    """
    override = os.environ.get("WALKA_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Walka"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Walka"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "walka"


@dataclass
class TreadmillConfig:
    kind: str = "ftms"  # a reader of treadmill/readers.py ("ftms", "rsc", "walkingpad", ...) | "simulated"
    # Bluetooth address. Empty = the first matching treadmill; the companion
    # then stores that pad's address here, so that every later reconnect can
    # only reach the same pad.
    address: str = ""
    # Bluetooth names (their first letters) that are taken for a treadmill as
    # soon as they are heard. Any other treadmill is found by the Fitness
    # Machine Service it advertises, at the end of the scan - so an empty
    # list means "whatever advertises that service".
    name_prefixes: list[str] = field(default_factory=lambda: list(DEFAULT_NAME_PREFIXES))
    units: str = "auto"  # firmware speed grid: "auto" | "mph" | "kmh"
    auto_connect: bool = True


@dataclass
class HeartRateConfig:
    enabled: bool = False
    address: str = ""  # Bluetooth address; empty = first heart-rate sensor found
    name: str = ""
    # Heart rate over the local network, from a watch app (hr_network.py). Off unless switched on;
    # its own port, and a pairing code that is made when it is first switched on.
    network: bool = False
    network_port: int = 8788
    network_code: str = ""


@dataclass
class WowConfig:
    addon_dir: str = ""  # Walka addon folder; empty = find automatically
    # (Up to 0.10 also capture_fps and origin, for reading the addon's strip
    # off the screen. A config that still has them loads: they are ignored.)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    # Show the dashboard when Walka starts? (The name is from when that
    # meant a tab in the web browser.)
    open_browser: bool = True
    # ... in a window of Walka's own (False: in the web browser).
    window: bool = True


@dataclass
class OverlayConfig:
    """The floating window: a small Walka panel that stays on top of the
    game (``window.FloatingWindow``). Only where Walka has a window of its
    own; off until it is switched on in Settings."""

    enabled: bool = False
    # Today's steps against this, as a bar along the bottom. 0: no bar.
    step_goal: int = 6000
    # Where it was last dragged to, in screen coordinates. None: the
    # top-right corner of the main screen.
    x: int | None = None
    y: int | None = None


def _packed() -> bool:
    return bool(getattr(sys, "frozen", False))


@dataclass
class UpdatesConfig:
    """Updates (updates.py): asks GitHub only when switched on. On in the
    packaged program unless it is switched off; run from source, off unless
    switched on (such a copy is updated with git)."""

    check: bool = field(default_factory=_packed)
    # Switched on or off in Settings by the person (and not just the default)?
    # Up to 0.11 the file held "check": false for everybody who had not
    # switched it on: that was the default then, not a choice (see load()).
    chosen: bool = False


@dataclass
class Config:
    """Everything the companion can be told. (A file written by a version
    that could still drive a belt holds more - speed limits, a STOP hotkey,
    the acceptance of that version's disclaimer. Those keys are not read:
    there is nothing left for them to set.)"""

    units: str = "mph"  # dashboard display: "mph" | "kmh"
    profile: Profile = field(default_factory=Profile)
    treadmill: TreadmillConfig = field(default_factory=TreadmillConfig)
    heart_rate: HeartRateConfig = field(default_factory=HeartRateConfig)
    wow: WowConfig = field(default_factory=WowConfig)
    web: WebConfig = field(default_factory=WebConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    # The first-run guide has been finished (or skipped). A settings file
    # from before there was a guide counts as finished: see load().
    welcome_done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Sanity bounds applied to anything loaded from disk or sent by the dashboard,
# so that a typo cannot put nonsense into the numbers of a walk.
_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    ("profile", "weight_kg"): (30.0, 250.0),
    ("profile", "height_cm"): (120.0, 230.0),
    ("profile", "step_calibration"): STEP_CALIBRATION_RANGE,
    ("web", "port"): (1.0, 65535.0),
    ("overlay", "step_goal"): (0.0, 100000.0),
    ("heart_rate", "network_port"): (1024.0, 65535.0),
}


_TRUE_WORDS = ("true", "yes", "on", "1")
_FALSE_WORDS = ("false", "no", "off", "0")


def _coerce(current: Any, value: Any) -> Any:
    """Convert a JSON value to the type of the existing field value.

    Raises TypeError/ValueError/OverflowError for a value that cannot be one.
    """
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUE_WORDS + _FALSE_WORDS:
            return value.strip().lower() in _TRUE_WORDS  # a hand-edited "false" must not read as True
        raise ValueError(f"not a boolean: {value!r}")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, str):
        return str(value)
    return value


# Walka is for adults (DISCLAIMER.md): the profile does not take a child's age.
MIN_AGE = 18


def _valid_age(value: Any) -> int:
    age = int(value)
    if isinstance(value, bool) or not MIN_AGE <= age <= 110:
        raise ValueError(f"not an age Walka is for: {value!r}")
    return age


def _valid_sex(value: Any) -> str:
    sex = value.strip().lower() if isinstance(value, str) else ""
    if sex not in ("male", "female"):
        raise ValueError(f"sex must be 'male' or 'female', not {value!r}")
    return sex


# A window's place on the screen: far out of any screen means a damaged file.
_COORD_LIMIT = 100_000


def _valid_coord(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a screen position: {value!r}")
    coord = int(value)
    if not -_COORD_LIMIT <= coord <= _COORD_LIMIT:
        raise ValueError(f"not a screen position: {value!r}")
    return coord


def _valid_name_prefixes(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"name_prefixes must be a list of strings, not {value!r}")
    return list(value)


# Fields whose value is checked the same way whatever they held before.
_VALIDATORS = {
    ("profile", "age"): _valid_age,
    ("profile", "sex"): _valid_sex,
    ("treadmill", "name_prefixes"): _valid_name_prefixes,
    ("overlay", "x"): _valid_coord,
    ("overlay", "y"): _valid_coord,
}


def _update(target: Any, data: dict[str, Any], section: str = "") -> None:
    """Copy known keys from ``data`` into a (nested) dataclass, ignoring the rest."""
    for f in fields(target):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(target, f.name)
        if is_dataclass(current):
            if isinstance(value, dict):
                _update(current, value, f.name)
            continue
        if value is None:
            # Optional fields (age, sex, origin, a window's place) may be cleared.
            if current is None or f.name in ("age", "sex", "origin", "x", "y"):
                setattr(target, f.name, None)
            continue
        try:
            validator = _VALIDATORS.get((section, f.name))
            if validator is not None:
                coerced = validator(value)
            elif current is None:
                coerced = value
            else:
                coerced = _coerce(current, value)
        except (TypeError, ValueError, OverflowError):
            log.warning("ignoring bad config value %s.%s=%r", section, f.name, value)
            continue
        bounds = _BOUNDS.get((section, f.name))
        if bounds and isinstance(coerced, (int, float)) and not isinstance(coerced, bool):
            coerced = type(coerced)(min(bounds[1], max(bounds[0], coerced)))
        setattr(target, f.name, coerced)


def is_loopback_host(host: object) -> bool:
    """Does ``host`` name this computer only (127.x.x.x, ::1, localhost)?"""
    if not isinstance(host, str):
        return False
    text = host.strip().lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def apply_update(config: Config, data: dict[str, Any]) -> Config:
    """Merge a partial dict (from disk or the dashboard) into ``config``."""
    _update(config, data)
    if config.units not in ("mph", "kmh"):
        config.units = "mph"
    if not is_loopback_host(config.web.host):
        # The dashboard has no password. On this computer that is fine;
        # reachable from the network it would show your walks, weight and
        # height to every device on the Wi-Fi - and let any of them change
        # the settings.
        log.warning("web.host %r is not this computer itself - using 127.0.0.1", config.web.host)
        config.web.host = "127.0.0.1"
    if config.treadmill.kind not in (*READER_KINDS, "simulated"):
        config.treadmill.kind = "ftms"
    if config.treadmill.units not in ("auto", "mph", "kmh"):
        config.treadmill.units = "auto"
    return config


def _set_aside(path: Path, why: str) -> None:
    """A config.json that cannot be read: defaults are used, and the next
    save would write over it. So it is kept beside it first, as
    config.json.unreadable (with a number, if that is taken), for whoever
    wants to see what was in it."""
    kept = path.with_name(path.name + ".unreadable")
    number = 1
    while kept.exists():
        number += 1
        kept = path.with_name(f"{path.name}.unreadable-{number}")
    try:
        os.replace(path, kept)
    except OSError as exc:
        log.warning("could not read %s (%s) - using defaults; nor could it be set aside (%s)", path, why, exc)
        return
    log.warning("could not read %s (%s) - using defaults. What it held is kept in %s", path, why, kept.name)


def load(data_dir: Path | None = None) -> Config:
    """Load the config, falling back to defaults for anything missing or bad."""
    config = Config()
    path = (data_dir or default_data_dir()) / CONFIG_FILE
    try:
        # (utf-8-sig: Notepad and PowerShell begin UTF-8 with a byte order mark.)
        raw = json.loads(path.read_text("utf-8-sig"))
    except FileNotFoundError:
        return config
    except (OSError, ValueError) as exc:
        _set_aside(path, str(exc))
        return config
    if not isinstance(raw, dict):
        _set_aside(path, "not a table of settings")
        return config
    if isinstance(raw, dict):
        apply_update(config, raw)
        if "welcome_done" not in raw:
            config.welcome_done = True  # set up before there was a first-run guide
        written = raw.get("updates")
        if not (isinstance(written, dict) and "chosen" in written):
            # Written up to 0.11, when the check was off unless switched on:
            # a "false" there was the default of the time. Today's default, then -
            # and anybody who had switched it on keeps it on.
            config.updates.check = config.updates.check or _packed()
    if tuple(config.treadmill.name_prefixes) == LEGACY_NAME_PREFIXES:
        # Written when UREVO was the only make known by name (the file holds
        # every setting, changed or not): move on to today's list.
        config.treadmill.name_prefixes = list(DEFAULT_NAME_PREFIXES)
    return config


def save(config: Config, data_dir: Path | None = None) -> Path:
    directory = data_dir or default_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config.to_dict(), indent=2), "utf-8")
    os.replace(tmp, path)
    return path
