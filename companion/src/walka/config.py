"""Companion configuration: a small JSON file in the user's data folder."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .governor import Limits
from .metrics import KMH_PER_MPH, Profile

log = logging.getLogger(__name__)

CONFIG_FILE = "config.json"


def default_data_dir() -> Path:
    """Where config and recorded walks live.

    ``%APPDATA%\\Walka`` on Windows, ``~/.config/walka`` elsewhere;
    ``WALKA_DATA_DIR`` overrides both.
    """
    override = os.environ.get("WALKA_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Walka"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "walka"


@dataclass
class TreadmillConfig:
    kind: str = "ftms"  # "ftms" | "simulated"
    address: str = ""  # Bluetooth address; empty = first matching treadmill
    name_prefixes: list[str] = field(default_factory=lambda: ["URTM"])
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Sanity bounds applied to anything loaded from disk or sent by the dashboard,
# so a typo cannot configure a dangerous belt.
_BOUNDS: dict[tuple[str, str], tuple[float, float]] = {
    ("limits", "max_speed_kmh"): (0.8, 12.0),
    ("limits", "accel_kmh_per_s"): (0.05, 1.0),
    ("limits", "decel_kmh_per_s"): (0.1, 2.0),
    ("limits", "command_interval_s"): (0.5, 5.0),
    ("limits", "signal_timeout_s"): (1.0, 15.0),
    ("limits", "hold_s"): (0.0, 60.0),
    ("limits", "safe_speed_kmh"): (0.0, 4.0),
    ("limits", "loss_pause_s"): (0.0, 3600.0),
    ("limits", "start_grace_s"): (5.0, 60.0),
    ("limits", "stall_s"): (2.0, 30.0),
    ("profile", "weight_kg"): (30.0, 250.0),
    ("profile", "height_cm"): (120.0, 230.0),
    ("manual", "speed_kmh"): (0.5, 12.0),
    ("wow", "capture_fps"): (2.0, 60.0),
    ("web", "port"): (1.0, 65535.0),
}


def _coerce(current: Any, value: Any) -> Any:
    """Convert a JSON value to the type of the existing field value."""
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, str):
        return str(value)
    return value


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
            if current is None:
                coerced = value
                if f.name == "age":
                    coerced = int(value)
                    if not 5 <= coerced <= 110:
                        continue
                elif f.name == "sex":
                    coerced = str(value).lower()
                    if coerced not in ("male", "female"):
                        continue
            else:
                coerced = _coerce(current, value)
        except (TypeError, ValueError):
            log.warning("ignoring bad config value %s.%s=%r", section, f.name, value)
            continue
        bounds = _BOUNDS.get((section, f.name))
        if bounds and isinstance(coerced, (int, float)) and not isinstance(coerced, bool):
            coerced = type(coerced)(min(bounds[1], max(bounds[0], coerced)))
        setattr(target, f.name, coerced)


def apply_update(config: Config, data: dict[str, Any]) -> Config:
    """Merge a partial dict (from disk or the dashboard) into ``config``."""
    _update(config, data)
    if config.units not in ("mph", "kmh"):
        config.units = "mph"
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
    return config


def save(config: Config, data_dir: Path | None = None) -> Path:
    directory = data_dir or default_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config.to_dict(), indent=2), "utf-8")
    os.replace(tmp, path)
    return path
