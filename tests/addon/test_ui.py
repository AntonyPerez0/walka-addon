"""Smoke tests for the addon's windows (Walka/UI/*.lua).

These do not judge how the windows look. They load the UI into the mock
client, use every control the way a player would - click, hover, drag,
type, scroll - and require that nothing raises, nothing secret is kept and
the saved variables stay writable. They are skipped until the UI files
exist (``ui_rt`` fixture).
"""

from __future__ import annotations

import math
import random

import pytest
from walka import beacon
from walka.protocol import Flags

from .conftest import KMH_PER_MPH, local_noon
from .harness import lua_dict, lua_list, lua_plain

NOW = local_noon(2026, 11, 17)
DAY = 86400

# The windows are found by what they are, not by their names, so that a
# rename in the UI does not break these tests: every named frame the addon
# created is a window, except the beacon strip.


def windows(rt):
    """Every top-level frame the addon has created so far."""
    found = []
    for name in sorted(rt.created_globals()):
        frame = rt.global_(name)
        if name == "WalkaBeaconFrame" or frame is None or not rt.M.is_widget(frame):
            continue
        if "Frame" in rt.snapshot.type_chain(rt.inspect(frame)["type"]):
            found.append(frame)
    return found


def same(rt, a, b) -> bool:
    return bool(rt.lua.globals().rawequal(a, b))


def window(rt, which):
    """The HUD, the history window, the journey window or the options page.

    The options page is the frame registered with the Settings window; of
    the others, the HUD is the one created first (it exists from login),
    the history window the one created after it and the journey window the
    one after that (the tests here open them in that order).
    """
    categories = list(rt.M.settings_categories.values())
    options = categories[0].frame if categories else None
    if which == "WalkaOptionsPanel":
        return options
    others = [frame for frame in windows(rt) if options is None or not same(rt, frame, options)]
    others.sort(key=lambda frame: rt.inspect(frame)["serial"])
    index = {"WalkaHUD": 0, "WalkaHistoryFrame": 1, "WalkaJourneyFrame": 2}[which]
    return others[index] if len(others) > index else None


def visible(rt, name) -> bool:
    frame = window(rt, name)
    return frame is not None and rt.inspect(frame)["visible"]


def settings_are_sane(rt) -> None:
    """Every number in the saved settings is finite, and the speed model
    still produces a target the belt can run."""

    def walk(value, path):
        if isinstance(value, float):
            assert math.isfinite(value), f"{path} is {value}"
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index + 1}]")

    walk(lua_plain(rt.db.settings), "settings")
    limits = lua_dict(rt.walka.GetBeltLimits(rt.walka))
    target = rt.walka.GetLiveSpeed(rt.walka)[1]
    assert target == 0 or limits["minKmh"] - 1e-6 <= target <= limits["maxKmh"] + 1e-6


def history_saved(count: int) -> str:
    records = ",".join(
        f"{{id={index % 255 + 1},start={NOW - index * 20000 - 5000},['end']={NOW - index * 20000 - 3200},"
        f"seconds=1750,distanceM=1400,steps={2000 + index},kcal=100.5,yards=9000,maxKmh=3.2,"
        f"character='Tester-Azeroth'}}"
        for index in range(count)
    )
    return "{sessions={" + records + "}}"


COMPANION = f"""
WalkaCompanionData = {{
    version = 1, written = {NOW - 600},
    profile = {{ weightKg = 80, heightCm = 178 }},
    limits = {{ minKmh = 1.0, maxKmh = 6.0, stepKmh = 0.1, accelKmhPerS = 0.5, decelKmhPerS = 1.0 }},
    treadmill = "UREVO Strol 2E",
    sessions = {{
        {{ start = {NOW - 5000}, ["end"] = {NOW - 3200}, seconds = 1790, distanceM = 1502, steps = 2710,
          kcal = 118.4, maxKmh = 3.3, avgHr = 104, maxHr = 121, source = "wow", stepsSource = "treadmill" }},
        {{ start = {NOW - 9 * DAY}, ["end"] = {NOW - 9 * DAY + 900}, seconds = 900, distanceM = 700, steps = 1300,
          kcal = 55.0, maxKmh = 3.0, source = "manual", stepsSource = "estimate" }},
    }},
    older = {{ sessions = 12, seconds = 36000, distanceM = 30000, steps = 50000, kcal = 2400.5 }},
}}
"""


@pytest.fixture
def full_rt(ui_rt, make_rt):
    """The UI with a long history and companion data to show."""
    return make_rt(
        files={"CompanionData.lua": COMPANION},
        saved_variables={"WalkaDB": history_saved(60)},
        state={"clock": NOW},
    )


# ---- loading ----------------------------------------------------------------------------


def test_every_file_in_the_toc_loads(ui_rt):
    assert ui_rt.skipped_files == []
    modules = ("Widgets", "HUD", "History", "JourneyWindow", "Options")
    assert [name for name in modules if ui_rt.ns[name] is None] == []
    ui_rt.tick(20)


def test_windows_have_walka_names_and_sit_under_uiparent(ui_rt):
    ui_rt.slash("/walka history")
    ui_rt.slash("/walka journey")
    ui_rt.slash("/walka options")
    assert len(windows(ui_rt)) >= 4, "HUD, history window, journey window and options page"
    for name in ("WalkaHUD", "WalkaHistoryFrame", "WalkaJourneyFrame"):
        frame = window(ui_rt, name)
        assert frame is not None, name
        assert ui_rt.inspect(frame)["name"].startswith("Walka")
        assert rt_parent_chain_has_uiparent(ui_rt, frame), f"{name} must hide with the UI (Alt+Z)"


def rt_parent_chain_has_uiparent(rt, frame) -> bool:
    ui_parent = rt.global_("UIParent")
    cursor = frame
    for _ in range(20):
        cursor = rt.inspect(cursor).get("parent")
        if cursor is None:
            return False
        if same(rt, cursor, ui_parent):
            return True
    return False


def test_frames_closed_with_escape_exist(ui_rt):
    ui_rt.slash("/walka history")
    ui_rt.slash("/walka journey")
    listed = lua_list(ui_rt.global_("UISpecialFrames"))
    assert len(listed) == 2
    for name in listed:
        assert ui_rt.global_(name) is not None, f"UISpecialFrames lists {name}, which is not a frame"


# ---- HUD ----------------------------------------------------------------------------------


def test_hud_is_shown_by_default_and_slash_toggles_it(ui_rt):
    hud = ui_rt.ns.HUD
    assert hud.IsShown() is True and visible(ui_rt, "WalkaHUD")
    ui_rt.slash("/walka")
    assert hud.IsShown() is False and not visible(ui_rt, "WalkaHUD")
    assert ui_rt.db.settings.hud.shown is False
    ui_rt.slash("/walka")
    assert hud.IsShown() is True
    ui_rt.slash("/walka hide")
    ui_rt.slash("/walka hide")
    assert hud.IsShown() is False
    ui_rt.slash("/walka show")
    assert hud.IsShown() is True and ui_rt.db.settings.hud.shown is True


def test_hud_visibility_survives_a_reload(ui_rt):
    ui_rt.slash("/walka hide")
    fresh = ui_rt.reload()
    assert fresh.ns.HUD.IsShown() is False
    fresh.tick(10)
    fresh.slash("/walka show")
    assert fresh.reload().ns.HUD.IsShown() is True


def test_hud_follows_the_session(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    idle = rt.texts(hud)
    assert idle, "the HUD shows something even without a session"
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)
    early = rt.texts(hud)
    rt.tick(500)
    late = rt.texts(hud)
    assert idle != early != late, "the HUD text must change as the session goes on"
    assert any("mph" in text for text in late)
    rt.slash("/walka units kmh")
    rt.tick(5)
    metric = rt.texts(hud)
    assert any("km/h" in text for text in metric) and not any("mph" in text for text in metric)
    rt.slash("/walka pause")
    rt.tick(30)
    paused = rt.texts(hud)
    rt.slash("/walka stop")
    rt.tick(5)
    assert rt.texts(hud) != paused


def test_hud_buttons_drive_the_session(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    walka = rt.walka
    buttons = rt.descendants(hud, "Button", visible=True)
    assert buttons

    def state():
        return bool(walka.IsActive(walka)), bool(walka.IsPaused(walka))

    # What does each button do to an idle addon, and to a running session?
    starters, pausers, stoppers = [], [], []
    for button in buttons:
        walka.StopSession(walka)
        rt.hover(button)
        rt.click(button)
        rt.tick(2)
        if state() == (True, False):
            starters.append(button)
        walka.StopSession(walka)
        walka.StartSession(walka)
        rt.click(button)
        rt.tick(2)
        if state() == (True, True):
            pausers.append(button)
        elif state() == (False, False):
            stoppers.append(button)
        settings_are_sane(rt)
    assert starters, "a button on the HUD starts a session"
    assert pausers and stoppers, "... one pauses it and one stops it"

    # Start, pause, and (by one of the two) resume; stop files the session.
    walka.StopSession(walka)
    start, pause, stop = starters[0], pausers[0], stoppers[0]
    rt.click(start)
    rt.tick(2)
    assert state() == (True, False)
    rt.click(pause)
    rt.tick(2)
    assert state() == (True, True)
    rt.click(pause)
    rt.tick(2)
    if state() == (True, True):
        rt.click(start)
        rt.tick(2)
    assert state() == (True, False), "a paused session can be resumed from the HUD"
    rt.set(speed=7.0)
    rt.tick(300)
    rt.click(stop)
    assert state() == (False, False)
    assert len(lua_list(rt.db.sessions)) == 1
    rt.click(stop)                  # nothing to stop: harmless
    rt.check_errors()


def test_hud_tooltip(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    tooltip = rt.run("return GameTooltip")
    rt.M.hover(hud, True)
    rt.check_errors()
    assert rt.inspect(tooltip)["visible"], "hovering the HUD should explain it"
    lines = lua_list(rt.M.tooltip_lines(tooltip))
    assert lines and all(isinstance(line, str) for line in lines)
    rt.M.hover(hud, False)
    assert not rt.inspect(tooltip)["visible"]
    # A tooltip left open must not outlive the HUD.
    rt.M.hover(hud, True)
    rt.slash("/walka hide")
    assert not rt.inspect(tooltip)["visible"]


def test_hud_right_click_menu(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    rt.M.fire_script(hud, "OnMouseUp", "RightButton")
    rt.check_errors()
    entries = [str(entry) for entry in lua_list(rt.M.menu_entries())]
    assert entries, "right-click should open a menu"
    for text in [entry.split(":")[1] for entry in entries if entry.split(":")[0] in ("button", "checkbox")]:
        rt.M.fire_script(hud, "OnMouseUp", "RightButton")
        rt.M.menu_click(text)
        rt.check_errors()
        rt.tick(2)
    settings_are_sane(rt)


def test_hud_can_be_dragged_and_stays_put(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    before = rt.inspect(hud)
    assert rt.M.drag(hud, 150, -80) is True
    rt.check_errors()
    moved = rt.inspect(hud)
    assert moved["left"] == pytest.approx(before["left"] + 150, abs=1)
    assert moved["bottom"] == pytest.approx(before["bottom"] - 80, abs=1)

    fresh = rt.reload()
    restored = fresh.inspect(window(fresh, "WalkaHUD"))
    assert restored["left"] == pytest.approx(moved["left"], abs=1)
    assert restored["bottom"] == pytest.approx(moved["bottom"], abs=1)

    # Locked: a drag does nothing.
    fresh.walka.Set(fresh.walka, "hud.locked", True)
    assert fresh.M.drag(window(fresh, "WalkaHUD"), 300, 300) is False
    assert fresh.inspect(window(fresh, "WalkaHUD"))["left"] == pytest.approx(restored["left"], abs=1)


def test_hud_dragged_off_screen_comes_back(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    rt.M.drag(hud, 99999, 99999)
    rt.check_errors()
    fresh = rt.reload()
    info = fresh.inspect(window(fresh, "WalkaHUD"))
    screen_width, screen_height = fresh.M.screen_size()
    assert 0 <= info["left"] and info["left"] + info["width"] <= screen_width + 1
    assert 0 <= info["bottom"] and info["bottom"] + info["height"] <= screen_height + 1


def test_hud_scale_and_alpha_settings(ui_rt):
    rt = ui_rt
    hud = window(rt, "WalkaHUD")
    rt.walka.Set(rt.walka, "hud.scale", 1.5)
    rt.walka.Set(rt.walka, "hud.alpha", 0.4)
    info = rt.inspect(hud)
    assert info["scale"] == pytest.approx(1.5 * rt.inspect(rt.global_("UIParent"))["scale"])
    assert info["alpha"] == pytest.approx(0.4)
    # Nonsense from a damaged file is survivable.
    for bad in (0, -1, 1e9, float("nan")):
        rt.walka.Set(rt.walka, "hud.scale", bad)
        rt.walka.Set(rt.walka, "hud.alpha", bad)
        rt.tick(3)
        assert rt.inspect(hud)["scale"] > 0
    rt.walka.Set(rt.walka, "hud.scale", 1.0)
    rt.walka.Set(rt.walka, "hud.alpha", 0.9)
    rt.reload()


# ---- history ------------------------------------------------------------------------------


def test_history_window_opens_and_closes(ui_rt):
    rt = ui_rt
    assert not visible(rt, "WalkaHistoryFrame")
    rt.slash("/walka history")
    assert visible(rt, "WalkaHistoryFrame")
    assert rt.texts(window(rt, "WalkaHistoryFrame"))
    rt.tick(10)
    rt.slash("/walka history")
    assert not visible(rt, "WalkaHistoryFrame")
    rt.ns.History.Show()
    rt.ns.History.Show()
    assert visible(rt, "WalkaHistoryFrame")
    rt.ns.History.Hide()
    rt.ns.History.Hide()
    assert not visible(rt, "WalkaHistoryFrame")


def test_history_shows_merged_sessions_and_totals(full_rt):
    rt = full_rt
    rt.slash("/walka history")
    rt.tick(5)
    frame = window(rt, "WalkaHistoryFrame")
    texts = " | ".join(rt.texts(frame))
    totals = lua_plain(rt.walka.GetTotals(rt.walka))
    # The all-time step count (addon + companion + older) is on the page.
    assert rt.walka.FormatNumber(rt.walka, totals["all"]["steps"]) in texts
    assert "2,710" in texts, "the newest walk shows the companion's measured steps"

    # Scroll all the way down and back up.
    for delta in [-1] * 80 + [1] * 80:
        for target in rt.descendants(frame, visible=True):
            if rt.M.script_set(target, "OnMouseWheel"):
                rt.M.scroll_wheel(target, delta)
                break
    rt.check_errors()
    # Hover every row and control (tooltips), click every button.
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            rt.hover(widget)
    for button in rt.descendants(frame, "Button", visible=True):
        if visible(rt, "WalkaHistoryFrame") and rt.inspect(button)["visible"]:
            rt.click(button)
            rt.tick(2)
    rt.check_errors()


def test_history_updates_when_a_session_ends_while_it_is_open(ui_rt):
    rt = ui_rt
    rt.slash("/walka history")
    frame = window(rt, "WalkaHistoryFrame")
    before = rt.texts(frame)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(400)
    during = rt.texts(frame)
    rt.slash("/walka stop")
    rt.tick(10)
    after = rt.texts(frame)
    assert before != during, "the session in progress counts towards today's totals"
    assert during != after
    rt.walka.ClearHistory(rt.walka)
    rt.tick(10)
    assert rt.texts(frame) != after


def test_history_with_damaged_records(make_rt, ui_rt):
    saved = "{sessions={{},{start='x'},{start=5,['end']=2,steps=-4},{start=1e12,['end']=1e12+5,seconds=1/0 < 0 and 1 or 5}}}"
    rt = make_rt(saved_variables={"WalkaDB": saved}, files={"CompanionData.lua": "WalkaCompanionData = { sessions = { {}, 5 } }"})
    rt.slash("/walka history")
    rt.tick(10)
    for widget in rt.descendants(window(rt, "WalkaHistoryFrame"), visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            rt.hover(widget)


# ---- options -------------------------------------------------------------------------------


def open_options(rt):
    rt.slash("/walka options")
    panel = window(rt, "WalkaOptionsPanel")
    assert panel is not None and rt.inspect(panel)["visible"]
    return panel


def test_options_open_through_the_settings_window(ui_rt):
    rt = ui_rt
    assert lua_list(rt.log.settings_opened) == []
    panel = open_options(rt)
    assert len(lua_list(rt.log.settings_opened)) == 1
    assert rt.texts(panel), "the page has content"
    rt.tick(10)
    # Opening it again is fine.
    rt.ns.Options.Open()
    rt.check_errors()
    assert len(lua_list(rt.log.settings_opened)) == 2


def test_options_cannot_open_in_combat_and_say_so(ui_rt):
    rt = ui_rt
    rt.enter_combat()
    rt.clear_printed()
    rt.slash("/walka options")
    assert lua_list(rt.log.settings_opened) == []
    assert lua_list(rt.log.blocked) == [], "the addon must not even try: the client would block it"
    assert any("combat" in line for line in rt.printed)
    rt.leave_combat()
    open_options(rt)


def test_every_checkbox_can_be_toggled_twice(ui_rt):
    rt = ui_rt
    panel = open_options(rt)
    before = lua_plain(rt.db.settings)
    boxes = rt.descendants(panel, "CheckButton", visible=True)
    assert boxes
    for box in boxes:
        rt.hover(box)
        if rt.click(box):
            rt.tick(2)
            settings_are_sane(rt)
            # A second click on a box that is still there puts it back.
            if rt.inspect(box)["visible"]:
                rt.click(box)
                rt.tick(2)
    settings_are_sane(rt)
    after = lua_plain(rt.db.settings)
    assert after["units"] == before["units"] and after["profile"] == before["profile"]


def test_every_slider_can_be_moved_end_to_end(ui_rt):
    rt = ui_rt
    panel = open_options(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    sliders = rt.descendants(panel, "Slider", visible=True)
    assert sliders
    for slider in sliders:
        low, high = slider.GetMinMaxValues(slider)
        assert high > low
        for value in (low, high, (low + high) / 2, low - 5, high + 5, low + (high - low) * 0.3333):
            rt.M.slide(slider, value)
            rt.check_errors()
            rt.tick(3)
            settings_are_sane(rt)
            assert beacon.decode_row(rt.render_beacon_row(0)) is not None or not rt.db.settings.beacon.enabled
    rt.reload()         # every value the sliders produced can be saved


def test_edit_boxes_accept_good_input_and_survive_bad_input(ui_rt):
    rt = ui_rt
    panel = open_options(rt)
    boxes = rt.descendants(panel, "EditBox", visible=True)
    assert boxes
    for box in boxes:
        for text in ("82", "170", "5'11", "abc", "", "nan", "-5", "1e99", "999999999999", "0", "6000", "12.5", "  70 kg "):
            rt.M.type_text(box, text, True)
            rt.check_errors()
            rt.tick(2)
            settings_are_sane(rt)
            # Clicking elsewhere ends the edit too.
            rt.M.type_text(box, text, False)
            box.ClearFocus(box)
            rt.check_errors()
            settings_are_sane(rt)
    profile = lua_plain(rt.db.settings.profile)
    assert 30 <= profile["weightKg"] <= 250 and 120 <= profile["heightCm"] <= 230
    assert rt.db.settings.dailyStepGoal >= 0
    rt.reload()


def test_every_button_on_the_options_page(ui_rt):
    rt = ui_rt
    panel = open_options(rt)
    for _ in range(2):
        for button in rt.descendants(panel, "Button", visible=True):
            if rt.inspect(button)["visible"]:
                rt.hover(button)
                rt.click(button)
                rt.tick(2)
                settings_are_sane(rt)
    rt.reload()


def test_options_page_reflects_changes_made_elsewhere(ui_rt):
    rt = ui_rt
    panel = open_options(rt)
    before = rt.texts(panel)
    rt.slash("/walka units kmh")
    rt.tick(3)
    metric = rt.texts(panel)
    assert before != metric and any("km/h" in text for text in metric)
    rt.slash("/walka weight 91kg")
    rt.slash("/walka beacon size 6")
    rt.tick(3)
    assert rt.texts(panel) != metric


# ---- minimap compartment ----------------------------------------------------------------------


def test_addon_compartment_click(ui_rt):
    rt = ui_rt
    click = rt.global_(rt.toc.metadata["AddonCompartmentFunc"])
    assert click is not None, "the TOC names a compartment function that does not exist"
    shown = rt.ns.HUD.IsShown()
    rt.M.call(click, "Walka", "LeftButton")
    assert rt.ns.HUD.IsShown() is not shown
    rt.M.call(click, "Walka", "RightButton")
    rt.check_errors()
    assert len(lua_list(rt.log.settings_opened)) == 1


# ---- combat -----------------------------------------------------------------------------------


def test_ui_keeps_working_with_secret_values(full_rt):
    rt = full_rt
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.slash("/walka history")
    rt.slash("/walka journey")
    open_options(rt)
    rt.tick(50)
    rt.enter_combat()
    rt.set(chatLockdown=True, nameSecret=True, identitySecret=True)
    for step in range(120):
        rt.set(speed=[7.0, 0, 3.0, 14.0][step % 4], mounted=step % 4 == 3)
        rt.tick(5)
        assert rt.find_secrets() == [], "the UI is holding on to a secret value"
    for frame in windows(rt):
        for widget in rt.descendants(frame, visible=True):
            if rt.M.script_set(widget, "OnEnter"):
                rt.hover(widget)
    for name in ("WalkaHUD", "WalkaHistoryFrame", "WalkaJourneyFrame"):
        for button in rt.descendants(window(rt, name), "Button", visible=True):
            if rt.inspect(button)["visible"]:
                rt.click(button)
                rt.tick(2)
    assert rt.find_secrets() == []
    assert lua_list(rt.log.blocked) == []
    rt.reload()


# ---- everything at once --------------------------------------------------------------------------


SLASHES = [
    "/walka", "/walka start", "/walka pause", "/walka resume", "/walka stop", "/walka history", "/walka journey",
    "/walka options",
    "/walka status", "/walka units mph", "/walka units kmh", "/walka beacon off", "/walka beacon on",
    "/walka beacon size 2", "/walka beacon size 8", "/walka weight 60kg", "/walka height 190cm", "/walka show",
]
TYPED = ["82", "abc", "", "nan", "5'11", "999999", "-1", "0", "7500", "180"]


@pytest.mark.parametrize("seed", range(4))
def test_random_use_never_breaks_anything(full_rt, seed):
    rt = full_rt
    rng = random.Random(seed)
    rt.slash("/walka history")
    rt.slash("/walka journey")
    rt.slash("/walka options")
    rt.tick()
    used: set[str] = set()

    def act():
        choice = rng.random()
        if choice < 0.15:
            rt.slash(rng.choice(SLASHES))
            return
        if choice < 0.25:
            rt.set(
                speed=rng.choice([0, 0, 2.5, 7.0, 14.0, 40.0]), mounted=rng.random() < 0.3,
                taxi=rng.random() < 0.1, afk=rng.random() < 0.1, falling=rng.random() < 0.2,
                swimming=rng.random() < 0.1, dead=rng.random() < 0.05,
            )
            if rng.random() < 0.3:
                rt.enter_combat()
            elif rng.random() < 0.5:
                rt.leave_combat()
            return
        candidates = [w for frame in windows(rt) for w in lua_list(rt.M.interactive(frame))]
        if not candidates:
            rt.slash("/walka show")
            return
        widget = rng.choice(candidates)
        kind = rt.inspect(widget)["type"]
        used.add(kind)
        if rt.M.script_set(widget, "OnEnter") and rng.random() < 0.5:
            rt.hover(widget)
        if kind in ("Button", "CheckButton"):
            rt.click(widget, rng.choice(["LeftButton", "LeftButton", "RightButton"]))
        elif kind == "Slider":
            low, high = widget.GetMinMaxValues(widget)
            rt.M.slide(widget, rng.uniform(low - 1, high + 1))
        elif kind == "EditBox":
            rt.M.type_text(widget, rng.choice(TYPED), rng.random() < 0.7)
            widget.ClearFocus(widget)
        elif rt.M.script_set(widget, "OnMouseWheel") and rng.random() < 0.5:
            used.add("wheel")
            rt.M.scroll_wheel(widget, rng.choice([-1, 1]))
        elif rt.M.script_set(widget, "OnMouseUp"):
            rt.M.fire_script(widget, "OnMouseUp", rng.choice(["LeftButton", "RightButton"]))
            entries = [str(e) for e in lua_list(rt.M.menu_entries())]
            pressable = [e.split(":")[1] for e in entries if e.split(":")[0] in ("button", "checkbox")]
            if pressable and rng.random() < 0.7:
                used.add("menu")
                rt.M.menu_click(rng.choice(pressable))
        elif rt.M.script_set(widget, "OnDragStart") and rng.random() < 0.5:
            used.add("drag")
            rt.M.drag(widget, rng.uniform(-400, 400), rng.uniform(-300, 300))

    for step in range(350):
        act()
        rt.check_errors()
        rt.tick(rng.choice([0, 1, 1, 3, 10]))
        if step % 25 == 0:
            assert rt.find_secrets() == []
            settings_are_sane(rt)
            if rt.db.settings.beacon.enabled:
                assert rt.live_frame().has(Flags.ACTIVE) == bool(rt.walka.IsActive(rt.walka))
    assert lua_list(rt.log.blocked) == []
    # The run really did use the controls, not just look at them.
    assert {"Button", "CheckButton", "Slider", "EditBox"} <= used, used
    fresh = rt.reload()
    fresh.tick(20)
    assert fresh.find_secrets() == []


def test_the_cap_set_on_the_options_page_is_the_cap_the_belt_gets(ui_rt):
    # The page shows mph, stores km/h, and the speed model works on the
    # treadmill's 0.1 mph grid: "never faster than 1.0 mph" must mean 1.0.
    rt = ui_rt
    panel = open_options(rt)
    walka = rt.walka
    cap_slider = None
    for slider in rt.descendants(panel, "Slider", visible=True):
        before = walka.Get(walka, "speed.maxKmh")
        low, high = slider.GetMinMaxValues(slider)
        rt.M.slide(slider, low + (high - low) * 0.37)
        rt.check_errors()
        if walka.Get(walka, "speed.maxKmh") != before:
            cap_slider = slider
            break
    assert cap_slider is not None, "no slider on the options page changes the speed cap"
    walka.ResetSpeedSettings(walka)         # undo what the search moved
    rt.tick(3)
    defaults = rt.ns.SpeedModel.DEFAULTS
    default_cap_mph = round(defaults.maxKmh / KMH_PER_MPH, 1)       # what the page shows for it
    assert cap_slider.GetValue(cap_slider) == pytest.approx(default_cap_mph)

    # A mounted pace above every cap tried here, so that the cap decides.
    walka.Set(walka, "speed.mountKmh", 6.0)
    rt.set(speed=14.0, mounted=True)
    rt.slash("/walka start")
    rt.tick(900)
    target_mph = rt.walka.GetLiveSpeed(walka)[1] / KMH_PER_MPH
    assert target_mph == pytest.approx(default_cap_mph, abs=0.005), "the default cap, as the page shows it"
    for mph in (1.0, 1.5, 2.3, 0.8, 3.1):
        rt.M.slide(cap_slider, mph)
        rt.check_errors()
        rt.tick(900)
        assert rt.walka.GetLiveSpeed(walka)[1] / KMH_PER_MPH == pytest.approx(mph, abs=0.005), f"cap {mph} mph"

    # With the default paces the cap only matters once it is below one.
    walka.ResetSpeedSettings(walka)
    mounted_mph = round(defaults.mountKmh / KMH_PER_MPH, 1)
    assert mounted_mph < default_cap_mph
    rt.tick(900)
    assert rt.walka.GetLiveSpeed(walka)[1] / KMH_PER_MPH == pytest.approx(mounted_mph, abs=0.005)
    rt.M.slide(cap_slider, mounted_mph - 0.3)
    rt.check_errors()
    rt.tick(900)
    assert rt.walka.GetLiveSpeed(walka)[1] / KMH_PER_MPH == pytest.approx(mounted_mph - 0.3, abs=0.005)
