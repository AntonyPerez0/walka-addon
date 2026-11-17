"""The history window (Walka/UI/History.lua): the merged session list,
the totals cards, streak, the seven-day chart, scrolling and the empty state.

Controls and texts are found the way a player finds them (ui_driver.py)."""

from __future__ import annotations

import re
import time

import pytest

from . import ui_driver as ui
from .conftest import local_noon
from .harness import lua_list, lua_plain
from .test_ui import COMPANION, DAY, NOW, history_saved

ROWS = 8
COLUMNS = ["When", "Time", "Distance", "Steps", "kcal", "Avg speed", "Character"]


@pytest.fixture
def rt(ui_rt, make_rt):
    """No walks at all."""
    return make_rt(state={"clock": NOW})


@pytest.fixture
def full_rt(ui_rt, make_rt):
    """Sixty addon sessions over two weeks plus companion data: two measured
    walks (the newest is also among the addon's) and an older summary."""
    return make_rt(
        files={"CompanionData.lua": COMPANION},
        saved_variables={"WalkaDB": history_saved(60)},
        state={"clock": NOW},
    )


@pytest.fixture
def long_rt(ui_rt, make_rt):
    """350 sessions."""
    return make_rt(saved_variables={"WalkaDB": history_saved(350)}, state={"clock": NOW})


def redraw(rt):
    rt.walka.Fire(rt.walka, "TICK")
    rt.check_errors()


def merged(rt) -> list[dict]:
    return [lua_plain(entry) for entry in lua_list(rt.walka.GetHistory(rt.walka))]


# ---- reading the window ---------------------------------------------------------------------


def row_buttons(rt, frame) -> list:
    """The list's rows: the buttons that carry a line of cells."""
    return [
        button for button in rt.descendants(frame, "Button")
        if sum(1 for region in lua_list(rt.M.regions(button)) if rt.inspect(region)["type"] == "FontString") >= len(COLUMNS)
    ]


def column_positions(rt, frame) -> dict[float, str]:
    """Left edge of each column, from the list's header."""
    positions = {}
    for title in COLUMNS:
        header = [fs for fs in ui.font_strings(rt, frame, visible=True) if ui.plain(rt.inspect(fs)["text"]) == title]
        # "kcal" also appears in the cards; the header is the one whose own
        # text is just the title and that sits in the header line.
        assert header, f"no column titled {title!r}"
        positions[title] = header
    line = None
    for candidate in positions["When"]:
        line = ui.middle(rt, candidate)
    out = {}
    for title, candidates in positions.items():
        on_line = [fs for fs in candidates if abs(ui.middle(rt, fs) - line) < 2]
        assert len(on_line) == 1, f"column header {title!r} not found in the header line"
        out[round(ui.rect(rt, on_line[0])[0], 1)] = title
    return out


def rows(rt, frame) -> list[dict]:
    """The visible rows, top to bottom: a dict of the cells by column title,
    plus how the row is marked ("measured" / "estimated")."""
    columns = column_positions(rt, frame)
    out = []
    for button in row_buttons(rt, frame):
        if not ui.is_visible(rt, button):
            continue
        row = {"button": button, "top": ui.rect(rt, button)[1]}
        for region in lua_list(rt.M.regions(button)):
            info = rt.inspect(region)
            if info["type"] != "FontString":
                continue
            title = columns.get(round(info["left"], 1)) if info.get("left") is not None else None
            if title:
                row[title] = ui.plain(info.get("text"))
            elif ui.plain(info.get("text")) == "~":
                row["estimated"] = bool(info["visible"])
        dots = lua_list(rt.M.children(button))
        assert len(dots) == 1, "a row has one marker for measured sessions"
        row["measured"] = bool(rt.inspect(dots[0])["visible"])
        out.append(row)
    out.sort(key=lambda row: -row["top"])
    return out


def expected_row(rt, entry: dict) -> dict:
    walka = rt.walka
    speed = entry["distanceM"] / entry["seconds"] * 3.6 if entry["seconds"] > 0 else None
    return {
        "When": rt.run("return date('%b %d %H:%M', ...)", entry["start"]),
        "Time": walka.FormatDuration(walka, entry["seconds"]),
        "Distance": walka.FormatDistance(walka, entry["distanceM"]),
        "Steps": walka.FormatNumber(walka, entry["steps"]),
        "kcal": walka.FormatNumber(walka, entry["kcal"]),
        "Avg speed": walka.FormatSpeed(walka, speed) if speed else "-",
        "Character": (entry.get("character") or "").split("-")[0],
        "measured": bool(entry["measured"]),
        "estimated": not entry["measured"],
    }


def cells(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in ("button", "top")}


def card(rt, frame, title: str) -> dict:
    """One of the three summary cards, by its heading."""
    for child in lua_list(rt.M.children(frame)):
        own = ui.own_texts(rt, child)
        if title in own:
            out = {"all": own}
            for text in own:
                if re.fullmatch(r"[\d,]+ sessions?", text):
                    out["sessions"] = text
                elif text.endswith(" kcal"):
                    out["kcal"] = text
                elif text.endswith(" active"):
                    out["time"] = text
                elif text.endswith((" mi", " km")):
                    out["distance"] = text
                elif re.fullmatch(r"[\d,]+", text):
                    out["steps"] = text
            return out
    raise AssertionError(f"no card titled {title!r}")


def expected_card(rt, bucket: dict) -> dict:
    walka = rt.walka
    count = bucket["sessions"]
    return {
        "sessions": "1 session" if count == 1 else walka.FormatNumber(walka, count) + " sessions",
        "steps": walka.FormatNumber(walka, bucket["steps"]),
        "distance": walka.FormatDistance(walka, bucket["distanceM"]),
        "kcal": walka.FormatNumber(walka, bucket["kcal"]) + " kcal",
        "time": walka.FormatDuration(walka, bucket["seconds"]) + " active",
    }


def chart(rt, frame) -> list[dict]:
    """The seven-day chart, oldest day first: label, value shown, bar height."""
    slots = [
        widget for widget in rt.descendants(frame, "Frame", visible=True)
        if rt.M.script_set(widget, "OnEnter") and not ui.same(rt, widget, frame)
    ]
    out = []
    for slot in slots:
        own = ui.own_texts(rt, slot)
        bars = [rt.inspect(region) for region in lua_list(rt.M.regions(slot)) if rt.inspect(region)["type"] == "Texture"]
        assert len(bars) == 1
        out.append({
            "slot": slot,
            "left": ui.rect(rt, slot)[0],
            "label": own[-1] if own else "",
            "value": own[0] if len(own) > 1 else "",
            "bar": bars[0]["height"] if bars[0]["visible"] else 0,
            "color": [bars[0]["color"][index] for index in (1, 2, 3)],
        })
    out.sort(key=lambda slot: slot["left"])
    return out


def position_text(rt, frame) -> str:
    """The "1-8 of 61" (or "3 sessions") text under the list. (The cards
    count sessions too, but their texts belong to the cards.)"""
    for text in ui.own_texts(rt, frame):
        if re.fullmatch(r"\d+-\d+ of [\d,]+", text) or re.fullmatch(r"[\d,]+ sessions?", text):
            return text
    return ""


def wheel_target(rt, frame):
    for widget in rt.descendants(frame, visible=True):
        if rt.M.script_set(widget, "OnMouseWheel"):
            return widget
    raise AssertionError("nothing in the history window takes the mouse wheel")


def wheel(rt, frame, notches: int):
    target = wheel_target(rt, frame)
    for _ in range(abs(notches)):
        rt.M.scroll_wheel(target, 1 if notches > 0 else -1)
    rt.check_errors()


def scroll_bar(rt, frame):
    """(track, thumb) of the list's scroll bar."""
    tracks = [
        widget for widget in rt.descendants(frame, "Frame")
        if rt.M.script_set(widget, "OnMouseDown") and not ui.same(rt, widget, frame)
    ]
    assert len(tracks) == 1, "the history window should have one scroll bar"
    thumbs = lua_list(rt.M.children(tracks[0]))
    assert len(thumbs) == 1
    return tracks[0], thumbs[0]


# ---- empty ------------------------------------------------------------------------------------


def test_window_is_built_on_first_use_and_toggles(rt):
    before = ui.widget_count(rt)
    assert rt.global_("WalkaHistoryFrame") is None
    rt.slash("/walka history")
    frame = rt.global_("WalkaHistoryFrame")
    assert ui.is_visible(rt, frame) and ui.widget_count(rt) > before
    built = ui.widget_count(rt)
    rt.slash("/walka history")
    assert not ui.is_visible(rt, frame)
    rt.slash("/walka log")
    assert ui.is_visible(rt, frame) and ui.widget_count(rt) == built, "reopening must not build the window again"
    box, (screen_width, screen_height) = ui.rect(rt, frame), rt.M.screen_size()
    assert box[2:] == (560, 440)
    assert box[0] + box[2] / 2 == pytest.approx(screen_width / 2), "it opens in the middle of the screen"
    assert box[1] + box[3] / 2 == pytest.approx(screen_height / 2 + 30), "... a little above the centre"


def test_empty_state(rt):
    frame = ui.history(rt)
    shown = ui.page(rt, frame)
    assert "No walks yet." in shown and "/walka start" in shown
    assert rows(rt, frame) == []
    assert position_text(rt, frame) == ""
    assert "measured by the treadmill" not in shown, "no legend for an empty list"
    for title in ("Today", "Last 7 days", "All time"):
        assert card(rt, frame, title)["steps"] == "0" and card(rt, frame, title)["sessions"] == "0 sessions"
    assert "not yet" in next(text for text in ui.texts(rt, frame) if text.startswith("Streak"))
    assert not any(text.startswith("Session bests") for text in ui.texts(rt, frame))
    days = chart(rt, frame)
    assert len(days) == 7 and all(day["bar"] == 0 and day["value"] == "" for day in days)
    assert days[-1]["label"] == "Today"
    track, _ = scroll_bar(rt, frame)
    assert not ui.is_visible(rt, track), "nothing to scroll: no scroll bar"
    wheel(rt, frame, -5)                # harmless
    assert all(ui.tooltip(rt, day["slot"])[1] == "No walk recorded." for day in days)


def test_empty_state_while_the_first_session_is_running(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    frame = ui.history(rt)
    assert "Your first session is under way." in ui.page(rt, frame)
    rt.tick(300)
    redraw(rt)
    assert card(rt, frame, "Today")["sessions"] == "1 session", "the session in progress counts towards today"
    assert card(rt, frame, "Today")["steps"] != "0"
    rt.slash("/walka stop")
    rt.tick(3)
    shown = ui.page(rt, frame)
    assert "No walks yet." not in shown and "under way" not in shown
    assert len(rows(rt, frame)) == 1 and position_text(rt, frame) == "1 session"


# ---- the list -------------------------------------------------------------------------------------


def test_sessions_are_listed_newest_first_with_the_merged_numbers(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    entries = merged(rt)
    assert len(entries) == 61 and [e["start"] for e in entries] == sorted((e["start"] for e in entries), reverse=True)
    shown = rows(rt, frame)
    assert len(shown) == ROWS
    assert [cells(row) for row in shown] == [expected_row(rt, entry) for entry in entries[:ROWS]]
    assert position_text(rt, frame) == "1-8 of 61"

    # The newest walk was recorded by both sides: listed once, as measured,
    # with the treadmill's numbers and the character from the addon's record.
    first = cells(shown[0])
    assert first["measured"] and not first["estimated"]
    assert (first["Steps"], first["Time"], first["Character"]) == ("2,710", "29:50", "Tester")
    assert sum(1 for entry in entries if entry["steps"] == 2000) == 0, "the addon's estimate of that walk is gone"
    # The others are the addon's estimates.
    assert all(cells(row)["estimated"] and not cells(row)["measured"] for row in shown[1:])
    assert "measured by the treadmill" in ui.page(rt, frame)


def test_row_tooltips(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    shown = rows(rt, frame)
    measured_tip = ui.tooltip(rt, shown[0]["button"])
    assert re.fullmatch(r"\w{3} Nov 17, 10:36 - 11:06", measured_tip[0])
    assert measured_tip[1].startswith("Measured by the treadmill")
    assert "Top speed: 2.1 mph" in measured_tip
    assert "Average heart rate: 104 bpm" in measured_tip and "Peak heart rate: 121 bpm" in measured_tip
    assert "Steps counted by: treadmill" in measured_tip
    assert "Character: Tester-Azeroth" in measured_tip
    assert any(line.startswith("Your character travelled ") for line in measured_tip)

    estimated_tip = ui.tooltip(rt, shown[1]["button"])
    assert estimated_tip[1].startswith("Estimated by the addon")
    assert not any("heart rate" in line for line in estimated_tip)


def test_scrolling_a_long_history(long_rt):
    rt = long_rt
    frame = ui.history(rt)
    entries = merged(rt)
    assert len(entries) == 350
    track, thumb = scroll_bar(rt, frame)
    assert ui.is_visible(rt, track)
    assert position_text(rt, frame) == "1-8 of 350"

    def check(offset):
        assert position_text(rt, frame) == f"{offset + 1}-{offset + ROWS} of 350"
        assert [cells(row) for row in rows(rt, frame)] == [expected_row(rt, entry) for entry in entries[offset:offset + ROWS]]

    check(0)
    wheel(rt, frame, -1)            # one notch down: two rows
    check(2)
    wheel(rt, frame, -10)
    check(22)
    wheel(rt, frame, 3)
    check(16)
    wheel(rt, frame, 100)           # past the top: stays at the top
    check(0)
    wheel(rt, frame, -400)          # past the end: the last eight
    check(350 - ROWS)
    assert cells(rows(rt, frame)[-1]) == expected_row(rt, entries[-1])

    # The thumb is at the bottom of its track now, and at the top after scrolling back.
    track_box, thumb_box = ui.rect(rt, track), ui.rect(rt, thumb)
    assert thumb_box[1] == pytest.approx(track_box[1], abs=0.5)
    assert ui.inside(thumb_box, track_box)
    wheel(rt, frame, 400)
    thumb_box = ui.rect(rt, thumb)
    assert thumb_box[1] + thumb_box[3] == pytest.approx(track_box[1] + track_box[3], abs=0.5)
    assert thumb_box[3] >= 18, "the thumb stays big enough to grab"


def test_dragging_and_paging_with_the_scroll_bar(long_rt):
    rt = long_rt
    frame = ui.history(rt)
    entries = merged(rt)
    track, thumb = scroll_bar(rt, frame)
    track_box, thumb_box = ui.rect(rt, track), ui.rect(rt, thumb)
    travel = track_box[3] - thumb_box[3]

    def first_row():
        return int(position_text(rt, frame).split("-")[0])

    # Grab the thumb and pull it halfway down.
    grab = thumb_box[1] + thumb_box[3] / 2
    rt.set(cursorX=track_box[0] + 4, cursorY=grab)
    rt.M.fire_script(thumb, "OnMouseDown", "LeftButton")
    rt.set(cursorY=grab - travel / 2)
    rt.frame(0.02)
    assert first_row() == pytest.approx((350 - ROWS) / 2 + 1, abs=1)
    assert [cells(row) for row in rows(rt, frame)][0] == expected_row(rt, entries[first_row() - 1])
    # ... all the way down and beyond.
    rt.set(cursorY=grab - travel - 500)
    rt.frame(0.02)
    assert first_row() == 350 - ROWS + 1
    # ... back up past the top.
    rt.set(cursorY=grab + 500)
    rt.frame(0.02)
    assert first_row() == 1
    rt.M.fire_script(thumb, "OnMouseUp", "LeftButton")
    rt.set(cursorY=grab - travel)       # the button is up: moving the mouse does nothing now
    rt.frame(0.02)
    assert first_row() == 1

    # A click on the track below the thumb pages down, above it pages up.
    thumb_box = ui.rect(rt, thumb)
    rt.set(cursorY=thumb_box[1] - 20)
    rt.M.fire_script(track, "OnMouseDown", "LeftButton")
    assert first_row() == 1 + ROWS
    rt.M.fire_script(track, "OnMouseDown", "LeftButton")
    assert first_row() == 1 + 2 * ROWS
    rt.set(cursorY=track_box[1] + track_box[3] - 1)
    rt.M.fire_script(track, "OnMouseDown", "LeftButton")
    assert first_row() == 1 + ROWS
    rt.M.fire_script(track, "OnMouseDown", "RightButton")     # other buttons do nothing
    assert first_row() == 1 + ROWS
    rt.check_errors()


def test_a_short_list_needs_no_scrolling(ui_rt, make_rt):
    rt = make_rt(saved_variables={"WalkaDB": history_saved(5)}, state={"clock": NOW})
    frame = ui.history(rt)
    assert len(rows(rt, frame)) == 5 and position_text(rt, frame) == "5 sessions"
    track, _ = scroll_bar(rt, frame)
    assert not ui.is_visible(rt, track)
    wheel(rt, frame, -3)
    assert len(rows(rt, frame)) == 5


def test_the_list_costs_the_same_for_ten_sessions_as_for_four_hundred(ui_rt, make_rt):
    counts = []
    for sessions in (10, 400):
        rt = make_rt(saved_variables={"WalkaDB": history_saved(sessions)}, state={"clock": NOW})
        frame = ui.history(rt)
        wheel(rt, frame, -30)
        wheel(rt, frame, 30)
        for row in rows(rt, frame):
            ui.tooltip(rt, row["button"])
        counts.append(ui.widget_count(rt))
        assert len(row_buttons(rt, frame)) == ROWS, "a fixed pool of rows, refilled when the list scrolls"
    assert counts[0] == counts[1]


def test_reopening_starts_at_the_top_again(long_rt):
    rt = long_rt
    frame = ui.history(rt)
    wheel(rt, frame, -20)
    assert position_text(rt, frame) == "41-48 of 350"
    rt.ns.History.Hide()
    rt.ns.History.Show()
    assert position_text(rt, frame) == "1-8 of 350"


# ---- totals ---------------------------------------------------------------------------------------


def test_cards_show_today_week_and_all_time(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    totals = lua_plain(rt.walka.GetTotals(rt.walka))
    for title, key in (("Today", "today"), ("Last 7 days", "week"), ("All time", "all")):
        shown = card(rt, frame, title)
        assert {name: shown[name] for name in ("sessions", "steps", "distance", "kcal", "time")} == expected_card(rt, totals[key])
    # All time includes the companion's summary of walks too old to list.
    assert totals["all"]["sessions"] == 61 + 12
    assert card(rt, frame, "All time")["sessions"] == "73 sessions"
    assert card(rt, frame, "Today")["steps"] == "4,711"


def test_streak_and_bests(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    totals = lua_plain(rt.walka.GetTotals(rt.walka))
    assert totals["streak"] >= 14
    assert f"Streak  {totals['streak']} days in a row" in ui.texts(rt, frame)
    assert "Session bests  2,710 steps, 0.93 mi, 29:50" in ui.texts(rt, frame)


def test_streak_of_one_day(ui_rt, make_rt):
    rt = make_rt(saved_variables={"WalkaDB": history_saved(1)}, state={"clock": NOW})
    assert "Streak  1 day in a row" in ui.texts(rt, ui.history(rt))


def test_seven_day_chart(full_rt):
    rt = full_rt
    walka = rt.walka
    frame = ui.history(rt)
    totals = lua_plain(walka.GetTotals(walka))
    days = chart(rt, frame)
    assert len(days) == 7
    weekdays = [time.strftime("%a", time.localtime(NOW - offset * DAY)) for offset in range(6, 0, -1)]
    assert [day["label"] for day in days] == weekdays + ["Today"]
    assert [day["value"] for day in days] == [walka.FormatNumber(walka, day["steps"]) for day in totals["days"]]

    # Bars are in proportion, the tallest (or the goal, if higher) at full height.
    steps = [day["steps"] for day in totals["days"]]
    top = max(max(steps), 6000)
    assert [day["bar"] for day in days] == pytest.approx([50 * value / top for value in steps])
    assert max(day["bar"] for day in days) == pytest.approx(50)
    # Days that reached the goal are drawn in another colour.
    reached = {tuple(day["color"]) for day, value in zip(days, steps, strict=True) if value >= 6000}
    missed = {tuple(day["color"]) for day, value in zip(days, steps, strict=True) if value < 6000}
    assert len(reached) == 1 and len(missed) == 1 and reached != missed
    assert "goal" in ui.texts(rt, frame)

    tip = ui.tooltip(rt, days[-1]["slot"])
    assert tip[0] == time.strftime("%A, %b %d", time.localtime(NOW))
    assert tip[1:] == ["4,711 steps", walka.FormatDistance(walka, totals["today"]["distanceM"]),
                       walka.FormatNumber(walka, totals["today"]["kcal"]) + " kcal",
                       walka.FormatDuration(walka, totals["today"]["seconds"]) + " active", "79% of the daily goal"]

    # Without a goal: no goal line, the tallest day at full height.
    walka.Set(walka, "dailyStepGoal", 0)
    rt.tick(3)
    days = chart(rt, frame)
    assert "goal" not in ui.texts(rt, frame)
    assert [day["bar"] for day in days] == pytest.approx([50 * value / max(steps) for value in steps])
    assert not any("goal" in line for line in ui.tooltip(rt, days[-1]["slot"]))


def test_the_chart_fits_its_window(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    window = ui.rect(rt, frame)
    boxes = []
    for day in chart(rt, frame):
        box = ui.rect(rt, day["slot"])
        assert ui.inside(box, window)
        boxes.append(box)
    assert not any(ui.overlap(a, b) for index, a in enumerate(boxes) for b in boxes[index + 1:])


def test_fun_line(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    walka = rt.walka
    entries = [entry for entry in merged(rt) if (entry.get("yards") or 0) > 0]
    ran = sum(entry["yards"] for entry in entries) * 0.9144
    walked = sum(entry["distanceM"] for entry in entries)
    expected = (
        f"Your characters ran {walka.FormatDistance(walka, ran)} while you walked {walka.FormatDistance(walka, walked)}."
        f" That is {ran / walked:.0f} times as far."
    )
    assert expected in ui.texts(rt, frame)


# ---- staying current -----------------------------------------------------------------------------------


def test_a_session_that_ends_appears_at_the_top(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(3)
    assert card(rt, frame, "Today")["sessions"] == "3 sessions", "two finished walks and the one in progress"
    rt.tick(600)
    rt.slash("/walka stop")
    rt.tick(3)
    assert position_text(rt, frame) == "1-8 of 62"
    top = cells(rows(rt, frame)[0])
    record = lua_list(rt.db.sessions)[-1]
    assert top["estimated"] and top["Steps"] == walka.FormatNumber(walka, record.steps)
    assert card(rt, frame, "Today")["sessions"] == "3 sessions"
    assert card(rt, frame, "Today") == {**card(rt, frame, "Today"), **expected_card(rt, lua_plain(walka.GetTotals(walka))["today"])}

    walka.ClearHistory(walka)
    rt.tick(3)
    assert position_text(rt, frame) == "2 sessions", "only the companion's measured walks are left"
    assert all(cells(row)["measured"] for row in rows(rt, frame))


def test_totals_move_while_a_session_runs(rt):
    frame = ui.history(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    seen = []
    for _ in range(6):
        rt.tick(100)
        seen.append(card(rt, frame, "Today")["steps"])
    assert len(set(seen)) >= 4, "today's steps should be refreshed about every ten seconds"
    assert [int(text.replace(",", "")) for text in seen] == sorted(int(text.replace(",", "")) for text in seen)


def test_units_change_what_is_shown(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    assert cells(rows(rt, frame)[0])["Distance"].endswith(" mi") and cells(rows(rt, frame)[0])["Avg speed"].endswith(" mph")
    rt.slash("/walka units kmh")
    rt.tick(3)
    assert cells(rows(rt, frame)[0])["Distance"] == "1.50 km" and cells(rows(rt, frame)[0])["Avg speed"] == "3.0 km/h"
    assert card(rt, frame, "Today")["distance"].endswith(" km")
    assert " mi" not in ui.page(rt, frame).replace(" min", "")


def test_midnight_moves_today_on(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    assert card(rt, frame, "Today")["steps"] == "4,711"
    midnight = local_noon(2026, 11, 18) - 12 * 3600
    rt.set(clock=midnight - 30)
    rt.tick(100)
    assert card(rt, frame, "Today")["steps"] == "4,711", "half a minute before midnight"
    rt.tick(900)                # 00:01:10 the next day; the window refreshes once a minute
    assert card(rt, frame, "Today")["steps"] == "0" and card(rt, frame, "Today")["sessions"] == "0 sessions"
    assert chart(rt, frame)[-1]["value"] == "" and chart(rt, frame)[-2]["value"] == "4,711"


# ---- cost -------------------------------------------------------------------------------------------------


def test_history_is_merged_once_per_refresh_and_not_while_idle(full_rt):
    rt = full_rt
    rt.slash("/walka hide")             # (the HUD keeps its own, separately tested, cache)
    merges = ui.merge_counter(rt)
    frame = ui.history(rt)
    assert merges.calls == 1, "opening the window merges the history once, for the totals and the list together"

    merges.reset()
    rt.tick(1200)                       # two minutes with the window open
    wheel(rt, frame, -10)
    for row in rows(rt, frame):
        ui.tooltip(rt, row["button"])
    assert merges.calls == 0

    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(600)                        # the totals move every ten seconds - from the list already merged
    assert merges.calls <= 1
    merges.reset()
    rt.slash("/walka stop")             # "HISTORY" and "SESSION" in one burst
    rt.tick(3)
    assert merges.calls == 1

    merges.reset()
    for step in range(40):              # a slider being dragged in the options
        rt.walka.Set(rt.walka, "hud.alpha", 0.5 + step / 100)
    rt.walka.Set(rt.walka, "units", "kmh")
    rt.walka.Set(rt.walka, "dailyStepGoal", 7000)
    rt.tick(3)
    assert merges.calls == 1, "only the unit and the goal matter to this window, and they redraw it once"

    merges.reset()
    rt.ns.History.Hide()
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(600)
    rt.slash("/walka stop")
    rt.tick(600)
    assert merges.calls == 0, "a closed window does nothing"
    rt.ns.History.Show()
    assert merges.calls == 1 and position_text(rt, frame) == "1-8 of 63"


def test_refreshing_creates_no_widgets(full_rt):
    rt = full_rt
    frame = ui.history(rt)
    for day in chart(rt, frame):
        ui.tooltip(rt, day["slot"])
    rt.set(speed=7.0)
    rt.slash("/walka start")            # (the HUD draws its pause icon the first time it is needed)
    rt.slash("/walka stop")
    count = ui.widget_count(rt)
    for _ in range(3):
        rt.slash("/walka start")
        rt.tick(300)
        rt.slash("/walka stop")
        rt.tick(5)
        wheel(rt, frame, -40)
        wheel(rt, frame, 40)
        rt.slash("/walka units kmh")
        rt.slash("/walka units mph")
        rt.walka.Set(rt.walka, "dailyStepGoal", 0)
        rt.walka.Set(rt.walka, "dailyStepGoal", 6000)
        rt.ns.History.Hide()
        rt.ns.History.Show()
        for row in rows(rt, frame):
            ui.tooltip(rt, row["button"])
    assert ui.widget_count(rt) == count


# ---- the window itself ---------------------------------------------------------------------------------------


def test_escape_and_the_close_button_close_it(rt):
    frame = ui.history(rt)
    name = rt.inspect(frame)["name"]
    assert name in lua_list(rt.global_("UISpecialFrames")), "the game closes windows listed here on Escape"
    assert lua_list(rt.global_("UISpecialFrames")).count(name) == 1
    # What the game does on Escape:
    rt.run("_G[...]:Hide()", name)
    assert not ui.is_visible(rt, frame)
    rt.ns.History.Show()
    rt.ns.History.Hide()
    rt.ns.History.Show()
    assert lua_list(rt.global_("UISpecialFrames")).count(name) == 1, "listed once, however often it is opened"

    close = [button for button in rt.descendants(frame, "Button", visible=True) if not row_buttons(rt, frame).count(button)]
    close = [button for button in close if rt.M.script_set(button, "OnClick")]
    assert len(close) == 1, "one close button"
    box, window = ui.rect(rt, close[0]), ui.rect(rt, frame)
    assert box[0] + box[2] > window[0] + window[2] - 40 and box[1] + box[3] > window[1] + window[3] - 40, "top right"
    rt.click(close[0])
    assert not ui.is_visible(rt, frame)


def test_window_can_be_moved_and_stays_on_screen(rt):
    frame = ui.history(rt)
    before = ui.rect(rt, frame)
    assert rt.M.drag(frame, -200, 60) is True
    moved = ui.rect(rt, frame)
    assert (moved[0], moved[1]) == pytest.approx((before[0] - 200, before[1] + 60), abs=1)
    rt.M.drag(frame, 5000, 5000)
    screen_width, screen_height = rt.M.screen_size()
    edge = ui.rect(rt, frame)
    assert edge[0] + edge[2] <= screen_width + 0.01 and edge[1] + edge[3] <= screen_height + 0.01
    rt.tick(5)


def test_history_window_in_combat_with_secret_values(full_rt):
    rt = full_rt
    rt.enter_combat()
    rt.set(speed=7.0, chatLockdown=True, nameSecret=True)
    rt.slash("/walka start")
    frame = ui.history(rt)
    rt.tick(300)
    wheel(rt, frame, -6)
    for row in rows(rt, frame):
        ui.tooltip(rt, row["button"])
    rt.slash("/walka stop")
    rt.tick(3)
    assert position_text(rt, frame) == "13-20 of 62", "a refresh keeps the place in the list"
    wheel(rt, frame, 6)
    assert cells(rows(rt, frame)[0])["Character"] == "?", "the character's name was hidden when the session began"
    assert rt.find_secrets() == []


def test_no_text_is_cut_off_with_worst_case_values(ui_rt, make_rt):
    """Largest plausible numbers in every column (text widths are the mock's estimate)."""
    record = (
        f"{{id=1,start={NOW - 4000},['end']={NOW - 100},seconds=86399,distanceM=99990,steps=123456,kcal=9999.9,"
        f"yards=999999,maxKmh=12.5,character='Wwwwwwwwwwww-Realmname'}}"
    )
    rt = make_rt(saved_variables={"WalkaDB": "{sessions={" + record + "}}"}, state={"clock": NOW})
    rt.slash("/walka units kmh")
    frame = ui.history(rt)
    window = ui.rect(rt, frame)
    for font_string in ui.font_strings(rt, frame, visible=True):
        text = rt.inspect(font_string).get("text")
        assert not font_string.IsTruncated(font_string), text
        box = ui.rect(rt, font_string)
        if box is not None and text:
            assert ui.inside(box, window, slack=1), f"{text!r} sticks out of the window"
    row = cells(rows(rt, frame)[0])
    assert (row["Time"], row["Steps"], row["kcal"], row["Character"]) == ("23:59:59", "123,456", "10,000", "Wwwwwwwwwwww")
