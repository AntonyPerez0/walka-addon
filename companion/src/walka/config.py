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

from .governor import Limits
from .hotkey import parse_hotkey
from .metrics import KMH_PER_MPH, STEP_CALIBRATION_RANGE, Profile
from .treadmill.known import DEFAULT_NAME_PREFIXES, LEGACY_NAME_PREFIXES

log = logging.getLogger(__name__)

CONFIG_FILE = "config.json"

# The disclaimer the dashboard asks to be accepted before the companion
# starts or steers a real belt: DISCLAIMER.md, and the card that shows it
# (static/index.html, which carries the same number). Raise it when what the
# disclaimer says changes in substance - everyone is then asked again.
TERMS_VERSION = 1


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
    kind: str = "ftms"  # "ftms" | "simulated"
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
    stop_on_exit: bool = True


@dataclass
class HeartRateConfig:
    enabled: bool = False
    address: str = ""  # Bluetooth address; empty = first heart-rate sensor found
    name: str = ""


@dataclass
class WowConfig:
    addon_dir: str = ""  # Walka addon folder; empty = find automatically
    capture_fps: float = 15.0
    # Optional manual capture origin [x, y] in screen pixels (top-left of the
    # game's client area) for setups the window finder cannot handle.
    origin: list[int] | None = None


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True


@dataclass
class ManualConfig:
    """Dashboard-driven sessions (any game, or no game at all)."""

    speed_kmh: float = 2.0 * KMH_PER_MPH


@dataclass
class TermsConfig:
    """The walker's acceptance of the disclaimer (see TERMS_VERSION)."""

    accepted_version: int = 0  # 0 = not accepted
    accepted_at: str = ""  # when: UTC, ISO 8601


@dataclass
class Config:
    units: str = "mph"  # dashboard display: "mph" | "kmh"
    profile: Profile = field(default_factory=Profile)
    # 3.0 mph until the user raises it: a safe ceiling for desk walking.
    limits: Limits = field(default_factory=lambda: Limits(max_speed_kmh=round(3.0 * KMH_PER_MPH, 2)))
    treadmill: TreadmillConfig = field(default_factory=TreadmillConfig)
    heart_rate: HeartRateConfig = field(default_factory=HeartRateConfig)
    wow: WowConfig = field(default_factory=WowConfig)
    web: WebConfig = field(default_factory=WebConfig)
    manual: ManualConfig = field(default_factory=ManualConfig)
    terms: TermsConfig = field(default_factory=TermsConfig)
    # System-wide key combination that stops the belt from inside a game
    # (Windows and macOS, where "alt" is the Option key). Empty = off.
    stop_hotkey: str = "ctrl+alt+x"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# The highest speed cap that can be configured: a little over 4.0 mph, the
# top of a walking pad's desk mode. Walka changes belt speed by itself, under
# someone who is looking at a game - running speeds are not for that.
MAX_SPEED_CAP_KMH = 6.5

# Sanity bounds applied to anything loaded from disk or sent by the dashboard,
# so a typo cannot configure a dangerous belt.
_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    ("limits", "max_speed_kmh"): (0.8, MAX_SPEED_CAP_KMH),
    ("limits", "accel_kmh_per_s"): (0.05, 1.0),
    ("limits", "decel_kmh_per_s"): (0.1, 2.0),
    ("limits", "command_interval_s"): (0.5, 5.0),
    ("limits", "signal_timeout_s"): (1.0, 15.0),
    ("limits", "hold_s"): (0.0, 60.0),
    ("limits", "safe_speed_kmh"): (0.0, 4.0),
    ("limits", "loss_pause_s"): (0.0, 3600.0),
    ("limits", "start_grace_s"): (5.0, 60.0),
    ("limits", "stall_s"): (2.0, 30.0),
    ("limits", "restart_holdoff_s"): (0.0, 60.0),
    ("profile", "weight_kg"): (30.0, 250.0),
    ("profile", "height_cm"): (120.0, 230.0),
    ("profile", "step_calibration"): STEP_CALIBRATION_RANGE,
    ("manual", "speed_kmh"): (0.5, MAX_SPEED_CAP_KMH),
    ("wow", "capture_fps"): (2.0, 60.0),
    ("web", "port"): (1.0, 65535.0),
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


def _valid_age(value: Any) -> int:
    age = int(value)
    if isinstance(value, bool) or not 5 <= age <= 110:
        raise ValueError(f"implausible age: {value!r}")
    return age


def _valid_sex(value: Any) -> str:
    sex = value.strip().lower() if isinstance(value, str) else ""
    if sex not in ("male", "female"):
        raise ValueError(f"sex must be 'male' or 'female', not {value!r}")
    return sex


def _valid_origin(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(isinstance(v, bool) for v in value):
        raise ValueError(f"origin must be [x, y], not {value!r}")
    return [int(value[0]), int(value[1])]


def _valid_name_prefixes(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"name_prefixes must be a list of strings, not {value!r}")
    return list(value)


# Fields whose value is checked the same way whatever they held before.
_VALIDATORS = {
    ("profile", "age"): _valid_age,
    ("profile", "sex"): _valid_sex,
    ("wow", "origin"): _valid_origin,
    ("treadmill", "name_prefixes"): _valid_name_prefixes,
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
            # Optional fields (age, sex, origin) may be cleared.
            if current is None or f.name in ("age", "sex", "origin"):
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
    previous_hotkey = config.stop_hotkey
    _update(config, data)
    config.stop_hotkey = config.stop_hotkey.strip().lower()
    if config.stop_hotkey and parse_hotkey(config.stop_hotkey) is None:
        config.stop_hotkey = previous_hotkey  # not a usable combination: keep the old one
    if config.units not in ("mph", "kmh"):
        config.units = "mph"
    if not is_loopback_host(config.web.host):
        # The dashboard can start and stop the belt and has no password. On
        # this computer that is fine; reachable from the network it would hand the
        # belt to every device on the Wi-Fi.
        log.warning("web.host %r is not this computer itself - using 127.0.0.1", config.web.host)
        config.web.host = "127.0.0.1"
    if config.treadmill.kind not in ("ftms", "simulated"):
        config.treadmill.kind = "ftms"
    if config.treadmill.units not in ("auto", "mph", "kmh"):
        config.treadmill.units = "auto"
    return config


def load(data_dir: Path | None = None) -> Config:
    """Load the config, falling back to defaults for anything missing or bad."""
    config = Config()
    path = (data_dir or default_data_dir()) / CONFIG_FILE
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return config
    except (OSError, ValueError) as exc:
        log.warning("could not read %s (%s) - using defaults", path, exc)
        return config
    if isinstance(raw, dict):
        apply_update(config, raw)
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
