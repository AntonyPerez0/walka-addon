"""Fixtures for the addon tests."""

from __future__ import annotations

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
    return all((ADDON_DIR / "UI" / name).is_file() for name in ("Widgets.lua", "HUD.lua", "History.lua", "Options.lua"))


@pytest.fixture
def ui_rt(make_rt) -> WowRuntime:
    """A mock client with the addon and its windows; skipped until the UI
    files exist."""
    if not ui_files_present():
        pytest.skip("Walka/UI/*.lua are not written yet")
    return make_rt()


def local_noon(year: int, month: int, day: int) -> int:
    """Epoch seconds of 12:00 local time on a date (tests must not depend
    on the time zone of the machine that runs them)."""
    return int(time.mktime((year, month, day, 12, 0, 0, 0, 0, -1)))


@pytest.fixture
def addon_dir() -> Path:
    return ADDON_DIR
