"""The global STOP hotkey: which key combinations are accepted. (Registering
one is Windows-only - see the checklist in the test report.)"""

from __future__ import annotations

import sys

import pytest

from walka import hotkey
from walka.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, GlobalHotkey, parse_hotkey


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ctrl+alt+x", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("Ctrl + Alt + X", (MOD_CONTROL | MOD_ALT, ord("X"))),
        ("control+shift+f9", (MOD_CONTROL | MOD_SHIFT, 0x78)),
        ("win+space", (MOD_WIN, 0x20)),
        ("alt+7", (MOD_ALT, ord("7"))),
        ("ctrl+numpad0", (MOD_CONTROL, 0x60)),
        ("ctrl+f24", (MOD_CONTROL, 0x87)),
        ("pause", (0, 0x13)),  # keys nobody types with may be used bare
        ("scrolllock", (0, 0x91)),
        ("f13", (0, 0x7C)),
        ("shift+pause", (MOD_SHIFT, 0x13)),
    ],
)
def test_usable_combinations(text: str, expected: tuple[int, int]):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "x",  # would swallow ordinary typing in the game
        "shift+x",
        "space",
        "f5",
        "enter",
        "ctrl",
        "ctrl+alt",
        "ctrl+x+y",
        "ctrl++",
        "+",
        "ctrl+",
        "ctrl+f0",
        "ctrl+f25",
        "ctrl+numpad10",
        "ctrl+banana",
        "ctrl+é",
        "ctrl+xx",
    ],
)
def test_unusable_combinations_are_refused(text):
    assert parse_hotkey(text) is None


def test_describe():
    assert hotkey.describe("ctrl+alt+x") == "Ctrl+Alt+X"
    assert hotkey.describe(" control + shift + f9 ") == "Ctrl+Shift+F9"
    assert hotkey.describe("") == ""


def test_a_hotkey_that_cannot_be_registered_reports_why_and_never_raises():
    calls: list[int] = []
    disabled = GlobalHotkey("", lambda: calls.append(1))
    assert disabled.start() is False and disabled.error == "disabled" and not disabled.active
    unusable = GlobalHotkey("x", lambda: calls.append(1))
    assert unusable.start() is False and "not a usable" in unusable.error
    for hk in (disabled, unusable):
        hk.stop()  # stopping what never started is fine
        assert hk.to_dict()["active"] is False
    if sys.platform != "win32":
        elsewhere = GlobalHotkey("ctrl+alt+x", lambda: calls.append(1))
        assert elsewhere.start() is False and "Windows" in elsewhere.error
        assert elsewhere.to_dict() == {"combination": "Ctrl+Alt+X", "active": False, "error": elsewhere.error}
        elsewhere.stop()
    assert calls == []
