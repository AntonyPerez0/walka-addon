"""The journey window (Walka/UI/Journey.lua): one character's journey so
far, as a card - level, time played, the player's steps next to the
character's, calories - and how to share it.

Controls and texts are found the way a player finds them (ui_driver.py):
a number by the caption above it, a button by what it says.
"""

from __future__ import annotations

import time

import pytest

from . import ui_driver as ui
from .harness import lua_list, lua_plain
from .test_journey import ME, NOW, companion, measured, own, saved

WIDTH, HEIGHT = 440, 310
SHARE_LINE = "The companion app's Share page turns these numbers\ninto a picture ready to post."
LEGEND = "~  includes estimates the treadmill has not confirmed yet"


@pytest.fixture
def rt(ui_rt, make_rt):
    """A character with no walks yet."""
    return make_rt()


@pytest.fixture
def full_rt(ui_rt, make_rt):
    """Level 42, three walks - one of them measured by the treadmill - and
    some distance behind it."""
    journeys = f"journeys = {{ ['{ME}'] = {{ since = {NOW - 20 * 86400}, footYd = 82026.4, travelYd = 373824.9 }} }}"
    return make_rt(
        saved_variables=saved(
            own(NOW - 90000, 1800, steps=2500, distance=1400, kcal=100.4),
            own(NOW - 80000, 900, steps=1200, distance=700, kcal=50.3),
            own(NOW - 70000, 600, steps=800, distance=450, kcal=33.0, character="Other-Azeroth"),
            more=", " + journeys,
        ),
        files=companion(measured(NOW - 90010, 1810, steps=2610, distance=1503, kcal=111.6)),
        state={"level": 42},
    )


def redraw(rt):
    rt.walka.Fire(rt.walka, "TICK")
    rt.check_errors()


def day(rt, epoch) -> str:
    return rt.run("return date('%b %d, %Y', ...)", epoch)


# ---- reading the window -----------------------------------------------------------------------------


def block(rt, frame, caption: str) -> list[str]:
    """The texts under a caption, top to bottom, left to right (empty ones
    included): what belongs to the same block of the card."""
    holder = rt.inspect(ui.label(rt, frame, caption))["parent"]
    strings = []
    for region in lua_list(rt.M.regions(holder)):
        info = rt.inspect(region)
        if info["type"] == "FontString" and info["visible"]:
            strings.append((-info["bottom"] - info["height"], info["left"], ui.plain(info.get("text"))))
    return [text for _, _, text in sorted(strings)]


def tile(rt, frame, caption: str) -> str:
    """The value under a small caption."""
    texts = block(rt, frame, caption)
    assert texts[0] == caption and len(texts) == 2
    return texts[1]


def column(rt, frame, caption: str) -> dict:
    """One of the two big columns: the word in front of the number, the
    number, the line under it."""
    texts = block(rt, frame, caption)
    assert texts[0] == caption and len(texts) == 4
    return {"mark": texts[1], "value": texts[2], "detail": texts[3]}


def bar(rt, frame, caption: str) -> float:
    """Length of the bar at the foot of a column (0 when it is not drawn)."""
    holder = rt.inspect(ui.label(rt, frame, caption))["parent"]
    textures = [rt.inspect(region) for region in lua_list(rt.M.regions(holder)) if rt.inspect(region)["type"] == "Texture"]
    assert len(textures) == 2, "a track and a bar"
    track, fill = sorted(textures, key=lambda texture: texture["serial"])
    assert track["visible"] and track["width"] == pytest.approx((WIDTH - 2 * 16 - 16) / 2)
    return fill["width"] if fill["visible"] else 0


def buttons(rt, frame) -> list:
    return [button for button in rt.descendants(frame, "Button", visible=True) if rt.M.script_set(button, "OnClick")]


def close_button(rt, frame):
    crosses = [button for button in buttons(rt, frame) if not ui.plain(rt.inspect(button).get("text"))]
    assert len(crosses) == 1, "one close button"
    return crosses[0]


def summary(rt) -> dict:
    return lua_plain(rt.ns.Tracker.GetSummary())


# ---- opening and closing --------------------------------------------------------------------------------


def test_window_is_built_on_first_use_and_toggles(rt):
    assert rt.global_("WalkaJourneyFrame") is None, "not built until it is asked for"
    rt.slash("/walka journey")
    frame = rt.global_("WalkaJourneyFrame")
    assert ui.is_visible(rt, frame)
    assert ui.rect(rt, frame)[2:] == (WIDTH, HEIGHT)
    rt.slash("/walka journey")
    assert not ui.is_visible(rt, frame)
    rt.slash("/walka card")                         # the other name for it
    assert ui.is_visible(rt, frame)
    rt.slash("/walka CARD")
    assert not ui.is_visible(rt, frame)
    assert ui.same(rt, rt.global_("WalkaJourneyFrame"), frame), "one window, built once"
    assert rt.printed == [line for line in rt.printed if "ime played" in line], "no chatter of our own"


def test_it_opens_from_the_hud_menu(rt):
    assert "button:Journey" in ui.context_menu(rt, ui.hud(rt))
    ui.menu_click(rt, "Journey")
    frame = ui.journey(rt, open_it=False)
    assert ui.is_visible(rt, frame)
    ui.context_menu(rt, ui.hud(rt))
    ui.menu_click(rt, "Journey")                    # the menu opens it; it does not toggle
    assert ui.is_visible(rt, frame)


def test_it_opens_from_the_history_window(rt):
    history = ui.history(rt)
    button = ui.button(rt, history, "Journey")
    tip = ui.tooltip(rt, button)
    assert tip[0] == "Your character's journey" and "share" in tip[1]
    assert rt.global_("WalkaJourneyFrame") is None, "looking at the button is not pressing it"
    rt.click(button)
    assert ui.is_visible(rt, ui.journey(rt, open_it=False))
    assert ui.is_visible(rt, history), "the history window stays open"
    assert rt.reload_requests == 0


def test_the_history_windows_journey_button_has_its_place(rt):
    history = ui.history(rt)
    journey, sync = ui.button(rt, history, "Journey"), ui.button(rt, history, "Sync")
    window, box, other = ui.rect(rt, history), ui.rect(rt, journey), ui.rect(rt, sync)
    assert ui.inside(box, window, slack=0) and not ui.overlap(box, other)
    assert box[0] + box[2] <= other[0], "left of Sync"
    assert ui.middle(rt, journey) == pytest.approx(ui.middle(rt, sync), abs=1)
    assert not journey.GetFontString(journey).IsTruncated(journey.GetFontString(journey))
    for text in ("Walka", "walking history"):
        title = ui.rect(rt, ui.label(rt, history, text))
        assert title[0] + title[2] <= box[0], "right of the title"


def test_escape_and_the_close_button_close_it(rt):
    frame = ui.journey(rt)
    name = rt.inspect(frame)["name"]
    assert name == "WalkaJourneyFrame"
    assert lua_list(rt.global_("UISpecialFrames")).count(name) == 1, "the game closes windows listed here on Escape"
    rt.run("_G[...]:Hide()", name)                  # what the game does on Escape
    assert not ui.is_visible(rt, frame)
    rt.ns.JourneyWindow.Show()
    rt.ns.JourneyWindow.Hide()
    rt.ns.JourneyWindow.Show()
    assert lua_list(rt.global_("UISpecialFrames")).count(name) == 1, "listed once, however often it is opened"

    close = close_button(rt, frame)
    box, window = ui.rect(rt, close), ui.rect(rt, frame)
    assert box[0] + box[2] > window[0] + window[2] - 40 and box[1] + box[3] > window[1] + window[3] - 40, "top right"
    rt.click(close)
    assert not ui.is_visible(rt, frame)


def test_the_window_sits_under_uiparent_and_above_the_game(rt):
    frame = ui.journey(rt)
    info = rt.inspect(frame)
    assert ui.same(rt, info["parent"], rt.global_("UIParent")), "hidden with the rest of the interface (Alt+Z)"
    assert info["strata"] == "HIGH"


# ---- what the card says ------------------------------------------------------------------------------------


def test_the_card_of_a_new_character(rt):
    frame = ui.journey(rt)
    texts = ui.texts(rt, frame)
    assert texts[:2] == ["Walka", "journey"]
    assert "Tester" in texts and f"tracked since {day(rt, NOW)}" in texts
    assert tile(rt, frame, "LEVEL") == "17 / 60"
    assert column(rt, frame, "MY STEPS") == {"mark": "", "value": "0", "detail": "0.0 mi walked"}
    assert column(rt, frame, "CHARACTER STEPS") == {"mark": "about", "value": "0", "detail": "0.0 in-game miles on foot"}
    assert tile(rt, frame, "CALORIES") == "0 kcal"
    assert tile(rt, frame, "TIME ON THE BELT") == "0:00"
    assert tile(rt, frame, "WALKS") == "0"
    assert f"Totals through {day(rt, NOW)}." in texts
    assert SHARE_LINE in texts
    assert LEGEND not in texts, "nothing here is an estimate"
    assert bar(rt, frame, "MY STEPS") == 0 and bar(rt, frame, "CHARACTER STEPS") == 0


def test_the_card_shows_the_journey(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    rt.tick(3)                                      # the game answers the question about /played; a redraw
    journey = summary(rt)
    texts = ui.texts(rt, frame)
    assert "Tester" in texts and f"tracked since {day(rt, NOW - 20 * 86400)}" in texts
    assert tile(rt, frame, "LEVEL") == "42 / 60"
    assert tile(rt, frame, "TIME PLAYED") == "21h 25m"
    # Two walks of this character: one measured (2,610 steps), one estimated (1,200).
    assert journey["steps"] == 3810 and journey["estimated"] is True
    assert column(rt, frame, "MY STEPS") == {"mark": "~", "value": "3,810", "detail": "~1.4 mi walked"}
    # 82,026 yards on foot: 2,000 steps to the mile of 1,760 yards.
    assert journey["footYd"] == 82026 and journey["characterSteps"] == 93211
    assert column(rt, frame, "CHARACTER STEPS") == {"mark": "about", "value": "93,211", "detail": "46.6 in-game miles on foot"}
    assert tile(rt, frame, "CALORIES") == "~161 kcal"
    assert tile(rt, frame, "TIME ON THE BELT") == "~45:10"
    assert tile(rt, frame, "WALKS") == "2"
    assert f"Totals through {day(rt, NOW)}." in texts
    assert LEGEND in texts
    assert SHARE_LINE in texts


def test_only_measured_walks_carry_no_marks(ui_rt, make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), files=companion(measured(NOW - 90000)))
    frame = ui.journey(rt)
    assert column(rt, frame, "MY STEPS") == {"mark": "", "value": "2,600", "detail": "0.9 mi walked"}
    assert tile(rt, frame, "CALORIES") == "110 kcal" and tile(rt, frame, "TIME ON THE BELT") == "30:00"
    assert LEGEND not in ui.texts(rt, frame)
    assert not any("~" in text for text in ui.texts(rt, frame))


def test_character_steps_are_always_about(full_rt):
    # A convention, not a measurement: marked "about" whether or not the
    # real-world numbers are estimates.
    frame = ui.journey(full_rt)
    assert column(full_rt, frame, "CHARACTER STEPS")["mark"] == "about"
    assert "~" not in column(full_rt, frame, "CHARACTER STEPS")["detail"]


def test_units_change_the_distances(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    rt.slash("/walka units kmh")
    assert column(rt, frame, "MY STEPS")["detail"] == "~2.2 km walked"
    assert column(rt, frame, "CHARACTER STEPS")["detail"] == "75.0 in-game km on foot"
    assert column(rt, frame, "CHARACTER STEPS")["value"] == "93,211", "steps are steps in any unit"
    rt.slash("/walka units mph")
    assert column(rt, frame, "MY STEPS")["detail"] == "~1.4 mi walked"
    assert column(rt, frame, "CHARACTER STEPS")["detail"] == "46.6 in-game miles on foot"


def test_the_bars_compare_the_two_distances(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    full = (WIDTH - 2 * 16 - 16) / 2
    # The character has gone 75 km on foot, the player 2.2 km: the longer bar is full.
    assert bar(rt, frame, "CHARACTER STEPS") == pytest.approx(full)
    assert bar(rt, frame, "MY STEPS") == pytest.approx(full * 2203 / (82026 * 0.9144))
    assert bar(rt, frame, "MY STEPS") >= 2, "never too thin to see"


def test_the_numbers_move_while_the_window_is_open(rt):
    frame = ui.journey(rt)
    rt.set(speed=7.0)
    rt.advance(10)
    shown = int(column(rt, frame, "CHARACTER STEPS")["value"])
    assert 75 <= shown <= 80, "redrawn about four times a second, by itself"
    redraw(rt)
    assert column(rt, frame, "CHARACTER STEPS")["value"] == "80"
    assert bar(rt, frame, "CHARACTER STEPS") > 0 and bar(rt, frame, "MY STEPS") == 0
    rt.slash("/walka start")
    rt.advance(60)
    walking = column(rt, frame, "MY STEPS")
    assert walking["mark"] == "~" and int(walking["value"]) > 50, "the session in progress is on the card"
    assert tile(rt, frame, "WALKS") == "1"
    assert LEGEND in ui.texts(rt, frame)
    rt.slash("/walka stop")
    rt.advance(1)
    assert abs(int(column(rt, frame, "MY STEPS")["value"]) - int(walking["value"])) <= 2
    assert tile(rt, frame, "WALKS") == "1"


def test_level_up_shows(rt):
    frame = ui.journey(rt)
    rt.fire_event("PLAYER_LEVEL_UP", 18)
    redraw(rt)
    assert tile(rt, frame, "LEVEL") == "18 / 60"


def test_the_date_is_todays(ui_rt, make_rt):
    rt = make_rt(state={"clock": NOW + 40 * 86400 + 300})
    frame = ui.journey(rt)
    today = time.strftime("%b %d, %Y", time.localtime(NOW + 40 * 86400 + 300))
    assert f"Totals through {today}." in ui.texts(rt, frame)
    assert f"tracked since {today}" in ui.texts(rt, frame)


# ---- time played ---------------------------------------------------------------------------------------------


def played_lines(rt) -> list[str]:
    return [line for line in rt.printed if "ime played" in line]


def test_opening_the_window_asks_the_game_for_the_time_played_once(rt):
    rt.advance(30)
    assert lua_list(rt.log.played_requests) == [] and rt.printed == []
    frame = ui.journey(rt)
    assert tile(rt, frame, "TIME PLAYED") == "not known yet"
    assert len(lua_list(rt.log.played_requests)) == 1
    rt.tick(3)                                      # the answer, and a redraw
    assert tile(rt, frame, "TIME PLAYED") == "21h 25m"
    assert len(played_lines(rt)) == 2, "the game prints its answer in the chat: once"

    for _ in range(3):                              # closing and opening it again asks nothing
        rt.slash("/walka journey")
        rt.slash("/walka journey")
        rt.tick(5)
    assert len(lua_list(rt.log.played_requests)) == 1 and len(played_lines(rt)) == 2

    rt.advance(3600)
    assert tile(rt, frame, "TIME PLAYED") == "22h 25m", "counted on from the answer"


def test_time_played_after_a_reload_and_after_a_new_login(rt):
    frame = ui.journey(rt)
    rt.tick(3)
    fresh = rt.reload()
    frame = ui.journey(fresh)
    assert tile(fresh, frame, "TIME PLAYED") == "21h 25m"
    assert lua_list(fresh.log.played_requests) == [], "a /reload is the same login"
    later = fresh.reload(gap=86400)
    frame = ui.journey(later)
    assert tile(later, frame, "TIME PLAYED") == "21h 25m", "the saved figure, until the game answers"
    assert len(lua_list(later.log.played_requests)) == 1, "a new login: asked once more"


def test_time_played_formats(ui_rt, make_rt):
    for played, text in [(59, "0m"), (60, "1m"), (3599, "59m"), (3600, "1h 00m"), (86400 * 3 + 125, "72h 02m"),
                         (3600 * 12345 + 59 * 60, "12,345h 59m")]:
        rt = make_rt(state={"played": played})
        frame = ui.journey(rt)
        rt.M.time_played_message()                  # (as if asked for at this very moment)
        redraw(rt)
        assert tile(rt, frame, "TIME PLAYED") == text, played


def test_time_played_tooltip_says_where_the_figure_comes_from(rt):
    frame = ui.journey(rt)
    holder = rt.inspect(ui.label(rt, frame, "TIME PLAYED"))["parent"]
    before = ui.tooltip(rt, holder)
    assert before[0] == "Time played" and before[1] == "The game has not said yet."
    assert "prints its answer in the chat" in before[2] and "/played" in before[2]
    rt.tick(3)
    after = ui.tooltip(rt, holder)
    assert after[:2] == ["Time played", "The game's own /played figure for this character."]
    assert "once per login" in after[2]


# ---- tooltips ---------------------------------------------------------------------------------------------------


def test_my_steps_tooltip(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    holder = rt.inspect(ui.label(rt, frame, "MY STEPS"))["parent"]
    tip = ui.tooltip(rt, holder)
    assert tip[0] == "My steps"
    assert tip[1] == "The steps you walked on the treadmill while playing Tester: 2 walks, the one in progress included."
    assert tip[2].startswith("~ Part of it is the addon's estimate.") and "/reload" in tip[2]


def test_my_steps_tooltip_when_everything_is_measured(ui_rt, make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), files=companion(measured(NOW - 90000)))
    frame = ui.journey(rt)
    tip = ui.tooltip(rt, rt.inspect(ui.label(rt, frame, "MY STEPS"))["parent"])
    assert "1 walk," in tip[1] and tip[2] == "All of it measured by the treadmill."


def test_character_steps_tooltip_states_the_convention(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    tip = ui.tooltip(rt, rt.inspect(ui.label(rt, frame, "CHARACTER STEPS"))["parent"])
    assert tip[0] == "Character steps"
    assert tip[1] == (
        f"How far Tester has gone on foot since {day(rt, NOW - 20 * 86400)}, "
        "counted as 2,000 steps to the in-game mile (1,760 yards)."
    )
    assert tip[2] == "The game does not count steps: this is Walka's own rule of thumb."
    assert tip[-1] == "Mounts, flight paths, flying and swimming are left out. With them: 212.4 in-game miles."


# ---- the Screenshot button -------------------------------------------------------------------------------------


def test_screenshot_button_takes_the_games_own_screenshot(rt):
    frame = ui.journey(rt)
    button = ui.button(rt, frame, "Screenshot")
    tip = ui.tooltip(rt, button)
    assert tip[0] == "Screenshot"
    assert "whole screen" in tip[1] and "Screenshots folder" in tip[1]
    assert lua_list(rt.log.screenshots) == [], "looking at it is not pressing it"
    assert rt.click(button) is True
    assert len(lua_list(rt.log.screenshots)) == 1
    assert ui.is_visible(rt, frame), "the window stays: it is what the picture is of"
    game_tooltip = rt.run("return GameTooltip")
    rt.M.hover(button, True)
    rt.click(button)
    assert not ui.is_visible(rt, game_tooltip), "the button's own tooltip is not in the picture"
    assert len(lua_list(rt.log.screenshots)) == 2
    rt.M.hover(button, False)
    rt.tick(20)
    assert len(lua_list(rt.log.screenshots)) == 2 and rt.reload_requests == 0


def test_no_screenshot_button_in_a_client_without_the_api(ui_rt, make_rt):
    rt = make_rt()
    rt.M.set_global("Screenshot", False)            # (as if the client had no such function)
    frame = ui.journey(rt)
    assert [ui.plain(rt.inspect(button).get("text")) for button in buttons(rt, frame)] == [""], "only the close button"
    assert SHARE_LINE in ui.texts(rt, frame)


def test_screenshot_button_and_share_line_have_their_places(rt):
    frame = ui.journey(rt)
    window = ui.rect(rt, frame)
    button = ui.rect(rt, ui.button(rt, frame, "Screenshot"))
    line = ui.rect(rt, ui.label(rt, frame, SHARE_LINE))
    assert ui.inside(button, window, slack=0) and ui.inside(line, window, slack=0)
    assert not ui.overlap(button, line) and line[0] + line[2] <= button[0], "the line ends before the button"
    assert button[0] + button[2] == pytest.approx(window[0] + window[2] - 16), "bottom right"
    assert button[1] == pytest.approx(window[1] + 12)


# ---- position ---------------------------------------------------------------------------------------------------


def test_it_opens_in_the_middle_until_it_is_moved(rt):
    frame = ui.journey(rt)
    left, bottom, width, height = ui.rect(rt, frame)
    screen_width, screen_height = rt.M.screen_size()
    assert left + width / 2 == pytest.approx(screen_width / 2)
    assert bottom + height / 2 == pytest.approx(screen_height / 2 + 40)
    assert rt.walka.Get(rt.walka, "journey.point") is None, "nothing is saved until the window is moved"


def test_the_position_is_remembered(rt):
    frame = ui.journey(rt)
    before = ui.rect(rt, frame)
    assert rt.M.drag(frame, -300, 120) is True
    moved = ui.rect(rt, frame)
    assert (moved[0], moved[1]) == pytest.approx((before[0] - 300, before[1] + 120), abs=1)
    saved_point = lua_plain(rt.walka.Get(rt.walka, "journey.point"))
    assert saved_point[0] == saved_point[1] and all(isinstance(value, int) for value in saved_point[2:])

    rt.slash("/walka journey")                      # closed and opened again: same place
    rt.slash("/walka journey")
    assert ui.rect(rt, frame) == pytest.approx(moved, abs=1)

    fresh = rt.reload()
    assert fresh.global_("WalkaJourneyFrame") is None
    assert ui.rect(fresh, ui.journey(fresh)) == pytest.approx(moved, abs=1), "and after a reload"
    assert lua_plain(fresh.db.settings.hud).get("point") is None, "the HUD's own position is another matter"


def test_it_cannot_be_dragged_off_the_screen(rt):
    frame = ui.journey(rt)
    rt.M.drag(frame, 5000, 5000)
    screen_width, screen_height = rt.M.screen_size()
    edge = ui.rect(rt, frame)
    assert edge[0] + edge[2] <= screen_width + 0.01 and edge[1] + edge[3] <= screen_height + 0.01
    rt.M.drag(frame, -9000, -9000)
    edge = ui.rect(rt, frame)
    assert edge[0] >= -0.01 and edge[1] >= -0.01
    rt.tick(5)
    assert ui.inside(ui.rect(rt, ui.button(rt, frame, "Screenshot")), ui.rect(rt, frame), slack=0)


@pytest.mark.parametrize("point", ["5", "'left'", "{}", "{'NOWHERE','CENTER',0,0}", "{'CENTER','CENTER',0/0,1/0}",
                                   "{'CENTER','CENTER','far',{}}", "{'TOPLEFT','TOPLEFT',1e9,-1e9}"])
def test_a_damaged_saved_position_falls_back_to_the_middle(ui_rt, make_rt, point):
    rt = make_rt(saved_variables={"WalkaDB": f"{{settings={{journey={{point={point}}}}}}}"})
    left, bottom, width, height = ui.rect(rt, ui.journey(rt))
    screen_width, screen_height = rt.M.screen_size()
    assert left + width / 2 == pytest.approx(screen_width / 2)
    assert bottom + height / 2 == pytest.approx(screen_height / 2 + 40)
    rt.tick(5)


def test_hiding_the_window_in_mid_drag_ends_the_drag(rt):
    frame = ui.journey(rt)
    rt.M.fire_script(frame, "OnDragStart", "LeftButton")
    rt.slash("/walka journey")
    rt.slash("/walka journey")
    assert rt.M.drag(frame, 50, 0) is True, "it is not glued to the mouse"


# ---- combat, hidden values, nobody ---------------------------------------------------------------------------------


def test_the_window_in_combat_with_everything_hidden(full_rt):
    rt = full_rt
    rt.enter_combat()
    rt.set(speed=7.0, chatLockdown=True, nameSecret=True, identitySecret=True)
    rt.slash("/walka start")
    rt.slash("/walka journey")                      # opened in combat
    frame = ui.journey(rt, open_it=False)
    assert ui.is_visible(rt, frame)
    rt.advance(40)
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    assert "Tester" in ui.texts(rt, frame) and tile(rt, frame, "LEVEL") == "42 / 60"
    assert int(column(rt, frame, "CHARACTER STEPS")["value"].replace(",", "")) > 93211
    rt.M.drag(frame, 40, 40)
    rt.click(ui.button(rt, frame, "Screenshot"))
    rt.click(close_button(rt, frame))
    assert not ui.is_visible(rt, frame)
    assert rt.find_secrets() == [] and lua_list(rt.log.blocked) == []
    rt.leave_combat()
    rt.reload()


def test_before_the_game_says_who_the_character_is(ui_rt, make_rt):
    rt = make_rt(state={"nameSecret": True})
    frame = ui.journey(rt)
    texts = ui.texts(rt, frame)
    assert "Your character" in texts and not any(text.startswith("tracked since") for text in texts)
    assert tile(rt, frame, "LEVEL") == "-" and tile(rt, frame, "TIME PLAYED") == "not known yet"
    assert column(rt, frame, "MY STEPS")["value"] == "0"
    assert lua_list(rt.log.played_requests) == [], "nothing is asked on nobody's behalf"
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    rt.set(nameSecret=False)
    rt.advance(1)
    assert "Tester" in ui.texts(rt, frame) and tile(rt, frame, "LEVEL") == "17 / 60"
    assert rt.find_secrets() == []


# ---- cost ---------------------------------------------------------------------------------------------------------


def test_a_closed_window_does_no_work(full_rt):
    rt = full_rt
    asked = ui.Counter(rt, rt.ns.Tracker, "GetSummary")
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    assert asked.calls == 0, "not built, not drawn"
    frame = ui.journey(rt)
    rt.tick(40)
    assert 5 <= asked.calls <= 25, "redrawn with the other windows, about four times a second"
    rt.ns.JourneyWindow.Hide()
    asked.reset()
    rt.tick(300)
    rt.slash("/walka stop")
    rt.slash("/walka units kmh")
    assert asked.calls == 0 and not ui.is_visible(rt, frame)


def test_an_open_window_merges_no_history_and_creates_no_widgets(full_rt):
    rt = full_rt
    rt.slash("/walka hide")                         # (the HUD keeps its own count of the history)
    frame = ui.journey(rt)
    rt.tick(5)
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    merges = ui.merge_counter(rt)
    count = ui.widget_count(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(600)
    rt.slash("/walka stop")
    rt.tick(100)
    rt.slash("/walka units kmh")
    rt.M.drag(frame, 30, 30)
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    assert merges.calls == 0
    assert ui.widget_count(rt) == count


# ---- layout -------------------------------------------------------------------------------------------------------


def laid_out_texts(rt, frame) -> list[tuple[tuple[float, float, float, float], str]]:
    found = []
    for font_string in ui.font_strings(rt, frame, visible=True):
        text = ui.plain(rt.inspect(font_string).get("text"))
        box = ui.rect(rt, font_string)
        if text and box is not None:
            assert not font_string.IsTruncated(font_string), text
            found.append((box, text))
    return found


@pytest.mark.parametrize("units", ["mph", "kmh"])
def test_nothing_is_cut_off_or_overlaps_with_worst_case_values(ui_rt, make_rt, units):
    """The longest name the game allows, level 60, years of play, millions
    of steps (text widths are the mock's estimate)."""
    name = "Wwwwwwwwwwww"
    record = (
        f"{{id=1,start={NOW - 4000},['end']={NOW - 100},seconds=3599990,distanceM=9999000,steps=12345678,"
        f"kcal=987654.9,yards=999999,maxKmh=12.5,character='{name}-Realmname'}}"
    )
    journeys = f"journeys={{['{name}-Realmname']={{since=1600000000,footYd=98765432,travelYd=987654321}}}}"
    rt = make_rt(saved_variables=saved(record, more=", " + journeys),
                 state={"name": name, "realm": "Realmname", "level": 60, "played": 3600 * 99999 + 3599})
    rt.slash("/walka units " + units)
    frame = ui.journey(rt)
    rt.tick(3)
    assert tile(rt, frame, "TIME PLAYED") == "99,999h 59m" and tile(rt, frame, "LEVEL") == "60 / 60"
    assert column(rt, frame, "MY STEPS")["value"] == "12,345,678"
    assert column(rt, frame, "CHARACTER STEPS")["value"] == "112,233,445"
    window = ui.rect(rt, frame)
    texts = laid_out_texts(rt, frame)
    assert len(texts) >= 20
    for box, text in texts:
        assert ui.inside(box, window, slack=1), f"{text!r} sticks out of the window"
    for index, (box, text) in enumerate(texts):
        for other, other_text in texts[index + 1:]:
            assert not ui.overlap(box, other), f"{text!r} overlaps {other_text!r}"
    for button in buttons(rt, frame):
        box = ui.rect(rt, button)
        assert ui.inside(box, window, slack=0)
        for other, text in texts:
            if text != "Screenshot":
                assert not ui.overlap(box, other), f"a button overlaps {text!r}"


def test_the_two_columns_line_up(full_rt):
    rt = full_rt
    frame = ui.journey(rt)
    mine, theirs = ui.label(rt, frame, "MY STEPS"), ui.label(rt, frame, "CHARACTER STEPS")
    assert ui.middle(rt, mine) == pytest.approx(ui.middle(rt, theirs))
    window = ui.rect(rt, frame)
    left, right = ui.rect(rt, rt.inspect(mine)["parent"]), ui.rect(rt, rt.inspect(theirs)["parent"])
    assert left[2] == right[2] and left[3] == right[3], "two columns of one size"
    assert left[0] - window[0] == pytest.approx(window[0] + window[2] - right[0] - right[2]), "the same margin on both sides"
    assert not ui.overlap(left, right)
