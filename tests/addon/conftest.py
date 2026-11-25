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
    """Factory for mock clients: ``make_rt(load=True, files=..., state=...)``.
    The session the addon starts by itself at login is off unless a test
    asks for it (``auto_session=True``)."""

    def make(*, load: bool = True, auto_session: bool = False, **kwargs) -> WowRuntime:
        runtime = WowRuntime(auto_session=auto_session, **kwargs)
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
    """The addon's private table (ns.Protocol, ns.Metrics, ...)."""
    return rt.ns


KMH_PER_MPH = 1.609344

# The pace a fresh install counts with: Metrics.DEFAULT_PACE_KMH (1.5 mph).
DEFAULT_PACE_KMH = 2.414


def local_noon(year: int, month: int, day: int) -> int:
    """Epoch seconds of 12:00 local time on a date (tests must not depend
    on the time zone of the machine that runs them)."""
    return int(time.mktime((year, month, day, 12, 0, 0, 0, 0, -1)))


@pytest.fixture
def addon_dir() -> Path:
    return ADDON_DIR
