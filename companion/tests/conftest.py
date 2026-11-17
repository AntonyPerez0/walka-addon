"""Shared set-up for the companion test-suite.

Makes the suite runnable from any working directory and in any pytest import
mode: the helper modules next to this file (``walka_fakes``) and the
``walka`` package itself are put on ``sys.path`` if they are not already.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = HERE.parent / "src"

for path in (str(HERE), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def addon_source() -> Path:
    """The addon's source folder in this checkout (read-only for tests)."""
    return REPO / "Walka"


@pytest.fixture
def lua():
    """A fresh Lua 5.1 runtime (the game's Lua version)."""
    from walka_fakes import new_lua

    return new_lua()
