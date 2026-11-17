"""Fixtures for the addon tests."""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from .harness import ADDON_DIR, WowRuntime, load_snapshot


@pytest.fixture(scope="session")
def snapshot():
    """The API snapshot of the WoW: Forever client."""
    return load_snapshot()


@pytest.fixture
def make_rt():
    """Factory for mock clients: ``make_rt(load=True, files=..., state=...)``."""

    def make(*, load: bool = True, **kwargs) -> WowRuntime:
        runtime = WowRuntime(**kwargs)
        if load:
            runtime.load_addon()
        return runtime

    return make


@pytest.fixture
def rt(make_rt) -> WowRuntime:
    """A mock client with the addon loaded and no saved variables."""
    return make_rt()


@pytest.fixture
def ns(rt):
    """The addon's private table (ns.Protocol, ns.SpeedModel, ...)."""
    return rt.ns


def ui_files_present() -> bool:
    names = ("Widgets.lua", "HUD.lua", "History.lua", "Journey.lua", "Options.lua")
    return all((ADDON_DIR / "UI" / name).is_file() for name in names)


@pytest.fixture
def ui_rt(make_rt) -> WowRuntime:
    """A mock client with the addon and its windows; skipped until the UI
    files exist."""
    if not ui_files_present():
        pytest.skip("Walka/UI/*.lua are not written yet")
    return make_rt()


KMH_PER_MPH = 1.609344

# The speed grid of the treadmill the addon assumes with speeds in mph (the
# default for an enUS client): 0.1 mph steps. With km/h it is 0.1 km/h.
MPH_STEP = 0.1 * KMH_PER_MPH


def nearest_step(kmh: float, step: float = 0.1) -> float:
    """The value on a treadmill's speed grid (multiples of ``step`` km/h)
    nearest to ``kmh``, halves rounding up as in Core/SpeedModel.lua.

    This is the target the speed model settles on for a pace that neither
    the cap nor the belt's minimum limits, so tests can say "the running
    pace" without repeating the numbers of SpeedModel.DEFAULTS. (The
    defaults themselves, and what they come to on both grids, are pinned in
    test_speed_model.py.)"""
    return math.floor(kmh / step + 0.5) * step


def local_noon(year: int, month: int, day: int) -> int:
    """Epoch seconds of 12:00 local time on a date (tests must not depend
    on the time zone of the machine that runs them)."""
    return int(time.mktime((year, month, day, 12, 0, 0, 0, 0, -1)))


@pytest.fixture
def addon_dir() -> Path:
    return ADDON_DIR
