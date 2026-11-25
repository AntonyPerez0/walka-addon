"""Which units the computer's region uses: the first-run guide's suggestion.

Only a suggestion - the guide shows it already chosen, and the person picks.
Read from the system, never from the network:

* Windows: the user's locale name (``GetUserDefaultLocaleName``, "en-US").
* macOS: ``AppleMetricUnits`` and ``AppleLocale`` in the global defaults.
* elsewhere: ``LC_ALL`` / ``LC_MEASUREMENT`` / ``LANG``.

Walka's two choices are "mph, miles, lb, ft/in" and "km/h, km, kg, cm". The
first goes to the regions that measure roads in miles: the United States,
the United Kingdom, Liberia and Myanmar.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

MILE_REGIONS = frozenset({"US", "GB", "LR", "MM", "PR", "VI", "AS", "GU", "MP", "UM"})


def region_of(locale_name: str | None) -> str | None:
    """"en-US", "en_GB.UTF-8", "English_United States.1252" -> "US", "GB", None."""
    if not locale_name:
        return None
    text = locale_name.split(".")[0].split("@")[0]
    match = re.match(r"^[A-Za-z]{2,3}[-_]([A-Za-z]{4}[-_])?([A-Za-z]{2})$", text)
    return match.group(2).upper() if match else None


def units_for_region(region: str | None) -> str | None:
    if not region:
        return None
    return "mph" if region.upper() in MILE_REGIONS else "kmh"


def _windows_locale() -> str | None:
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):  # type: ignore[attr-defined]
            return buffer.value
    except Exception:
        pass
    return None


def _mac_default(key: str) -> str | None:
    try:
        done = subprocess.run(["defaults", "read", "-g", key], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def system_units(platform: str | None = None) -> str | None:
    """"mph", "kmh", or None when the system does not say."""
    platform = platform or sys.platform
    if platform == "win32":
        return units_for_region(region_of(_windows_locale()))
    if platform == "darwin":
        # The measurement system chosen in System Settings, if one was; else the region's.
        metric = _mac_default("AppleMetricUnits")
        if metric in ("0", "1"):
            return "kmh" if metric == "1" else "mph"
        return units_for_region(region_of(_mac_default("AppleLocale")))
    for name in ("LC_ALL", "LC_MEASUREMENT", "LANG"):
        value = os.environ.get(name)
        if value:
            return units_for_region(region_of(value))
    return None
