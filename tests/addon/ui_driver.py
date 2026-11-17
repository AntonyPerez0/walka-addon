"""Finding and using the windows' controls the way a player does.

The tests in test_ui_*.py do not reach into the UI files' local tables:
they look for a control by what a player sees - the label next to a slider,
the text on a button, the title of a button's tooltip - and use it through
the mock client's input helpers. A control that is renamed or moved in the
code keeps passing; one that loses its label, its tooltip or its effect
does not.
"""

from __future__ import annotations

import re
from typing import Any

from .harness import WowRuntime, lua_list

COLOR_CODE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r")


def plain(text: Any) -> str:
    """Text as displayed: without colour codes."""
    return COLOR_CODE.sub("", str(text or ""))


def same(rt: WowRuntime, a: Any, b: Any) -> bool:
    return bool(rt.lua.globals().rawequal(a, b))


# ---- windows -----------------------------------------------------------------------------


def hud(rt: WowRuntime) -> Any:
    frame = rt.global_("WalkaHUD")
    assert frame is not None, "the HUD does not exist"
    return frame


def history(rt: WowRuntime, *, open_it: bool = True) -> Any:
    if open_it and not is_visible(rt, rt.global_("WalkaHistoryFrame")):
        rt.ns.History.Show()
        rt.check_errors()
    frame = rt.global_("WalkaHistoryFrame")
    assert frame is not None, "the history window does not exist"
    return frame


def journey(rt: WowRuntime, *, open_it: bool = True) -> Any:
    if open_it and not is_visible(rt, rt.global_("WalkaJourneyFrame")):
        rt.ns.JourneyWindow.Show()
        rt.check_errors()
    frame = rt.global_("WalkaJourneyFrame")
    assert frame is not None, "the journey window does not exist"
    return frame


def options(rt: WowRuntime, *, open_it: bool = True) -> Any:
    """The options page (opened through the Settings window)."""
    if open_it:
        rt.ns.Options.Open()
        rt.check_errors()
    categories = list(rt.M.settings_categories.values())
    assert categories, "no category was registered with the Settings window"
    panel = categories[0].frame
    if open_it:
        assert is_visible(rt, panel), "the options page did not open"
    return panel


def is_visible(rt: WowRuntime, widget: Any) -> bool:
    return widget is not None and bool(rt.inspect(widget)["visible"])


def texts(rt: WowRuntime, root: Any) -> list[str]:
    """What can be read in a window, colour codes removed."""
    return [plain(text) for text in rt.texts(root)]


def page(rt: WowRuntime, root: Any) -> str:
    return " | ".join(texts(rt, root))


# ---- geometry ----------------------------------------------------------------------------


def rect(rt: WowRuntime, widget: Any) -> tuple[float, float, float, float] | None:
    """left, bottom, width, height in screen units (None if not laid out)."""
    info = rt.inspect(widget)
    if info.get("left") is None:
        return None
    return info["left"], info["bottom"], info["width"], info["height"]


def middle(rt: WowRuntime, widget: Any) -> float:
    box = rect(rt, widget)
    assert box is not None, "the widget has no position"
    return box[1] + box[3] / 2


def inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], slack: float = 0.5) -> bool:
    return (
        inner[0] >= outer[0] - slack
        and inner[1] >= outer[1] - slack
        and inner[0] + inner[2] <= outer[0] + outer[2] + slack
        and inner[1] + inner[3] <= outer[1] + outer[3] + slack
    )


def overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[0] + b[2] - 0.5 and b[0] < a[0] + a[2] - 0.5 and a[1] < b[1] + b[3] - 0.5 and b[1] < a[1] + a[3] - 0.5


# ---- finding controls ------------------------------------------------------------------


def font_strings(rt: WowRuntime, root: Any, *, visible: bool | None = None) -> list[Any]:
    return rt.descendants(root, "FontString", visible=visible)


def label(rt: WowRuntime, root: Any, text: str, *, visible: bool | None = True) -> Any:
    """The font string that reads exactly `text`."""
    found = [fs for fs in font_strings(rt, root, visible=visible) if plain(rt.inspect(fs).get("text")) == text]
    assert found, f"nothing in the window reads {text!r}; it shows: {texts(rt, root)}"
    return found[0]


def own_texts(rt: WowRuntime, widget: Any) -> list[str]:
    """Texts of the font strings that belong directly to a widget."""
    out = []
    for region in lua_list(rt.M.regions(widget)):
        info = rt.inspect(region)
        if info["type"] == "FontString" and info.get("text"):
            out.append(plain(info["text"]))
    return out


def button(rt: WowRuntime, root: Any, text: str) -> Any:
    """The button (or check button, or segment of a choice) labelled `text`."""
    for kind in ("Button", "CheckButton"):
        for candidate in rt.descendants(root, kind):
            if text in own_texts(rt, candidate) or plain(rt.inspect(candidate).get("text")) == text:
                return candidate
    raise AssertionError(f"no button labelled {text!r}")


def checkbox(rt: WowRuntime, root: Any, text: str) -> Any:
    for candidate in rt.descendants(root, "CheckButton"):
        if text in own_texts(rt, candidate):
            return candidate
    raise AssertionError(f"no checkbox labelled {text!r}")


def checked(rt: WowRuntime, box: Any) -> bool:
    return bool(rt.inspect(box)["checked"])


def _in_row(rt: WowRuntime, root: Any, row_label: str, candidates: list[tuple[Any, Any]], what: str) -> Any:
    """Of `candidates` ((control, widget that has its position)), the one on
    the same line as the label that reads `row_label`."""
    target = middle(rt, label(rt, root, row_label, visible=None))
    best, best_distance = None, 1e9
    for control, positioned in candidates:
        if rect(rt, positioned) is None:
            continue
        distance = abs(middle(rt, positioned) - target)
        if distance < best_distance:
            best, best_distance = control, distance
    assert best is not None and best_distance <= 12, f"no {what} on the line that reads {row_label!r}"
    return best


def slider(rt: WowRuntime, root: Any, row_label: str) -> Any:
    """The slider on the line that reads `row_label`. (The slider itself is
    a child of the stock stepper control; that control carries the position.)"""
    candidates = [(s, rt.inspect(s)["parent"]) for s in rt.descendants(root, "Slider")]
    return _in_row(rt, root, row_label, candidates, "slider")


def slider_text(rt: WowRuntime, slider_widget: Any) -> str:
    """The value shown next to a slider."""
    shown = [
        plain(rt.inspect(region)["text"])
        for region in lua_list(rt.M.regions(rt.inspect(slider_widget)["parent"]))
        if rt.inspect(region)["type"] == "FontString" and rt.inspect(region)["shown"] and rt.inspect(region).get("text")
    ]
    assert len(shown) == 1, f"a slider should show exactly one value, found {shown}"
    return shown[0]


def slider_enabled(rt: WowRuntime, slider_widget: Any) -> bool:
    return not rt.inspect(slider_widget)["disabled"]


def slide(rt: WowRuntime, slider_widget: Any, value: float) -> bool:
    moved = rt.M.slide(slider_widget, value)
    rt.check_errors()
    return bool(moved)


def steppers(rt: WowRuntime, slider_widget: Any) -> tuple[Any, Any]:
    """The slider's "less" and "more" arrow buttons."""
    control = rt.inspect(slider_widget)["parent"]
    return control.Back, control.Forward


def edit_box(rt: WowRuntime, root: Any, row_label: str) -> Any:
    candidates = [(box, box) for box in rt.descendants(root, "EditBox")]
    return _in_row(rt, root, row_label, candidates, "edit box")


def box_text(box: Any) -> str:
    return str(box.GetText(box))


def type_into(rt: WowRuntime, box: Any, text: str, *, enter: bool = True) -> bool:
    typed = rt.M.type_text(box, text, enter)
    rt.check_errors()
    if not enter and typed is not False:
        box.ClearFocus(box)         # click elsewhere
        rt.check_errors()
    return typed is not False


# ---- tooltips and menus ---------------------------------------------------------------


def tooltip(rt: WowRuntime, widget: Any) -> list[str]:
    """The lines of the tooltip a widget shows under the mouse ([] if none)."""
    game_tooltip = rt.run("return GameTooltip")
    rt.M.hover(widget, True)
    rt.check_errors()
    lines = [plain(line) for line in lua_list(rt.M.tooltip_lines(game_tooltip))] if is_visible(rt, game_tooltip) else []
    rt.M.hover(widget, False)
    rt.check_errors()
    assert not is_visible(rt, game_tooltip), "the tooltip stayed open after the mouse left"
    return lines


def button_by_tooltip(rt: WowRuntime, root: Any, *titles: str) -> Any:
    """The visible button whose tooltip is titled one of `titles`."""
    seen = []
    for candidate in rt.descendants(root, "Button", visible=True):
        lines = tooltip(rt, candidate)
        if lines:
            seen.append(lines[0])
            if lines[0] in titles:
                return candidate
    raise AssertionError(f"no button with a tooltip titled {titles}; found {seen}")


def click(rt: WowRuntime, widget: Any, mouse_button: str = "LeftButton") -> bool:
    return rt.click(widget, mouse_button)


def context_menu(rt: WowRuntime, frame: Any) -> list[str]:
    """Right-click a frame; the entries of the menu that opens."""
    before = len(lua_list(rt.log.menus))
    rt.M.fire_script(frame, "OnMouseUp", "RightButton")
    rt.check_errors()
    assert len(lua_list(rt.log.menus)) == before + 1, "right-click did not open a menu"
    return [str(entry) for entry in lua_list(rt.M.menu_entries())]


def menu_click(rt: WowRuntime, text: str) -> None:
    rt.M.menu_click(text)
    rt.check_errors()


# ---- counting work ---------------------------------------------------------------------


class Counter:
    """Counts calls of a function in one of the addon's tables."""

    def __init__(self, rt: WowRuntime, table: Any, name: str):
        self._read = rt.lua.eval(
            "function(t, name) local real, calls = t[name], 0 "
            "t[name] = function(...) calls = calls + 1 return real(...) end "
            "return function() return calls end end"
        )(table, name)
        self._base = 0

    @property
    def calls(self) -> int:
        return int(self._read()) - self._base

    def reset(self) -> None:
        self._base = int(self._read())


def merge_counter(rt: WowRuntime) -> Counter:
    """Counts history merges - the expensive part of every totals refresh."""
    return Counter(rt, rt.ns.SessionLog, "merge")


def widget_count(rt: WowRuntime) -> int:
    """How many frames, textures and font strings exist in the client."""
    return len(rt.M.widgets)
