"""The whole addon at once.

* Ten simulated minutes of mixed play with every window in use: running,
  standing, mounting, combat with secret values, a flight path, AFK, pause
  and resume, windows opened and closed, settings changed, a /reload - and
  not one Lua error, secret leak, undefined global or leaked widget.
* The windows against whatever the companion's data file might contain.
* The windows across UI-scale and resolution changes.
"""

from __future__ import annotations

import math

import pytest
from walka import beacon
from walka.protocol import Flags

from . import ui_driver as ui
from .harness import ALLOWED_GLOBALS, ALLOWED_UNDEFINED, lua_dict, lua_list, lua_plain
from .test_ui import COMPANION, NOW, history_saved


@pytest.fixture
def full_rt(ui_rt, make_rt):
    return make_rt(
        files={"CompanionData.lua": COMPANION},
        saved_variables={"WalkaDB": history_saved(120)},
        state={"clock": NOW},
    )


def settings_are_finite(rt) -> None:
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


class Play:
    """Drives a client through a scripted session and checks it as it goes."""

    def __init__(self, rt):
        self.rt = rt
        self.seconds = 0.0
        self.last_session = None
        self.frames_decoded = 0

    def check(self) -> None:
        rt = self.rt
        rt.check_errors()
        assert rt.find_secrets() == [], f"a secret value is being kept (t={self.seconds:.0f}s)"
        settings_are_finite(rt)
        walka = rt.walka
        belt, target = walka.GetLiveSpeed(walka)
        limits = lua_dict(walka.GetBeltLimits(walka))
        assert target == 0 or limits["minKmh"] - 1e-6 <= target <= limits["maxKmh"] + 1e-6
        assert 0 <= belt <= limits["maxKmh"] + 1e-6
        # What the companion would read off the screen right now.
        if rt.db.settings.beacon.enabled:
            decoded = beacon.decode_row(rt.render_beacon_row(0))
            assert decoded is not None, f"the beacon is unreadable (t={self.seconds:.0f}s)"
            frame = decoded.frame
            assert frame.has(Flags.ACTIVE) == bool(walka.IsActive(walka))
            assert frame.has(Flags.PAUSED) == bool(walka.IsPaused(walka))
            assert frame.target_kmh == pytest.approx(target, abs=0.006)
            # AFK: the game's own state (hidden during a chat lockdown), or
            # a session still in its automatic AFK pause.
            current = walka.GetSession(walka)
            afk_pause = current is not None and current.paused and current.pauseReason == "afk"
            assert frame.has(Flags.AFK) == bool((rt.game.afk and not rt.game.chatLockdown) or afk_pause)
            self.frames_decoded += 1
        # A session only ever grows.
        session = walka.GetSession(walka)
        if session is not None:
            now = {key: session[key] for key in ("seconds", "distanceM", "steps", "kcal", "yards")}
            assert all(math.isfinite(value) for value in now.values())
            if self.last_session is not None and self.last_session[0] == session.id:
                for key, value in now.items():
                    assert value >= self.last_session[1][key] - 1e-9, key
            self.last_session = (session.id, now)
        else:
            self.last_session = None

    def run(self, seconds: float, step: float = 0.1) -> None:
        """Let time pass, checking once a second."""
        rt = self.rt
        whole = int(seconds)
        for _ in range(whole):
            rt.advance(1.0, step)
            self.seconds += 1.0
            self.check()
        if seconds > whole:
            rt.advance(seconds - whole, step)
            self.seconds += seconds - whole
            self.check()


def test_ten_minutes_of_mixed_play(full_rt):
    rt = full_rt
    play = Play(rt)
    walka = rt.walka
    hud = ui.hud(rt)
    merges = ui.merge_counter(rt)

    # -- login, a look at the HUD, start walking --------------------------------------------
    play.run(3)
    assert ui.tooltip(rt, hud)[0] == "Walka"
    rt.set(speed=7.0)
    rt.click(ui.button_by_tooltip(rt, hud, "Start walking"))
    play.run(20)
    assert "Walking" in ui.texts(rt, hud)

    # -- loot (stand still), run on, look at the history ----------------------------------------
    rt.set(speed=0)
    play.run(6)
    rt.set(speed=7.0)
    rt.click(ui.button_by_tooltip(rt, hud, "History"))
    history = ui.history(rt, open_it=False)
    assert ui.is_visible(rt, history)
    play.run(15)

    # -- mount up (the frame rate dips to 20 fps in town) --------------------------------------
    rt.set(speed=14.0, mounted=True, resting=True)
    play.run(25, step=0.05)

    # -- a fight: unit stats, AFK state and the character name become secret -------------------
    rt.enter_combat()
    rt.set(mounted=False, resting=False, chatLockdown=True, nameSecret=True)
    for index in range(12):
        rt.set(speed=[7.0, 0, 7.0, 3.5][index % 4], falling=index % 5 == 4)
        play.run(4)
    assert ui.tooltip(rt, hud), "the HUD tooltip works in combat"
    rt.clear_printed()
    rt.click(ui.button_by_tooltip(rt, hud, "Options"))          # refused in combat
    assert any("combat" in line for line in rt.printed) and lua_list(rt.log.blocked) == []
    ui.context_menu(rt, hud)
    ui.menu_click(rt, "Lock position")
    ui.context_menu(rt, hud)
    ui.menu_click(rt, "Lock position")
    for widget in rt.descendants(history, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    rt.set(falling=False)
    rt.leave_combat()
    rt.set(chatLockdown=False, nameSecret=False, speed=7.0)
    play.run(8)

    # -- the options, with a session running ----------------------------------------------------
    panel = ui.options(rt)
    play.run(5)
    ui.slide(rt, ui.slider(rt, panel, "running"), 2.4)
    ui.slide(rt, ui.slider(rt, panel, "mounted"), 2.8)
    play.run(10)
    rt.click(ui.button(rt, panel, "Responsive"))
    rt.click(ui.checkbox(rt, panel, "In combat, never drop below running pace"))
    ui.type_into(rt, ui.edit_box(rt, panel, "Daily step goal"), "8000")
    ui.slide(rt, ui.slider(rt, panel, "Size"), 1.2)
    ui.slide(rt, ui.slider(rt, panel, "Opacity"), 0.7)
    ui.slide(rt, ui.slider(rt, panel, "Square size"), 4)
    play.run(10)
    scroll = rt.descendants(panel, "ScrollFrame")[0]
    for _ in range(12):
        rt.M.scroll_wheel(scroll, -1)
    play.run(5, step=1 / 60)                                     # a stretch at 60 fps

    # -- a flight path ---------------------------------------------------------------------------
    rt.set(taxi=True, flying=True, speed=32.0)
    play.run(40)
    rt.click(ui.button(rt, panel, "Follow real speed"))
    play.run(10)
    rt.click(ui.button(rt, panel, "Running pace"))
    rt.set(taxi=False, flying=False, speed=7.0)
    play.run(10)
    rt.M.settings_close()

    # -- away from the keyboard ---------------------------------------------------------------
    rt.set(speed=0, afk=True)
    play.run(30)
    assert walka.IsPaused(walka) and "Paused (AFK)" in ui.texts(rt, hud)
    rt.set(afk=False)                                             # back: a line typed in chat, standing
    play.run(5)
    assert walka.IsPaused(walka) and "Paused (AFK)" in ui.texts(rt, hud), "the belt stays off until the character moves"
    rt.set(speed=7.0)
    play.run(10)
    assert not walka.IsPaused(walka)

    # -- pause by hand, a long hitch (loading screen), resume ----------------------------------
    play_button = ui.button_by_tooltip(rt, hud, "Pause")
    rt.click(play_button)
    play.run(12)
    rt.frame(4.0)                                                 # one frame of four seconds
    play.seconds += 4
    play.check()
    rt.click(play_button)
    play.run(20)

    # -- a second fight, mounted, with the options and history in use ---------------------------
    rt.enter_combat()
    rt.set(mounted=True, speed=14.0, chatLockdown=True, nameSecret=True, afk=True)   # AFK is hidden: no pause
    play.run(15)
    assert not walka.IsPaused(walka)
    rt.set(afk=False)
    rt.M.settings_select(1)                                       # by hand: allowed in combat
    rt.check_errors()
    ui.slide(rt, ui.slider(rt, panel, "Never faster than"), 2.6)
    rt.click(ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
    rt.set(speed=0, mounted=False)
    play.run(15)
    rt.click(ui.checkbox(rt, panel, "Stop the belt when my character stands still"))
    rt.set(speed=7.0)
    play.run(10)
    rt.leave_combat()
    rt.set(chatLockdown=False, nameSecret=False)

    # -- swimming, falling, dying ---------------------------------------------------------------
    rt.set(swimming=True, speed=4.7)
    play.run(10)
    rt.set(swimming=False, falling=True, speed=7.0)
    play.run(3)
    rt.set(falling=False, dead=True, speed=8.75)
    play.run(12)
    rt.set(dead=False, speed=7.0)

    # -- windows closed and opened, HUD hidden and shown, moved, rescaled -------------------------
    rt.ns.History.Hide()
    rt.M.settings_close()
    rt.slash("/walka hide")
    play.run(10)
    rt.slash("/walka show")
    rt.M.drag(hud, 700, -200)
    rt.M.set_ui_scale(0.75)
    play.run(5)
    rt.M.set_screen(2560, 1080)
    play.run(5)
    rt.M.set_screen(1920, 1080)
    rt.M.set_ui_scale(1.0)
    rt.slash("/walka units kmh")
    rt.slash("/walka history")
    play.run(10)
    rt.slash("/walka units mph")
    rt.slash("/walka status")

    # -- stop, start again, /reload in mid-session --------------------------------------------------
    rt.click(ui.button_by_tooltip(rt, hud, "Stop"))
    sessions_before = len(lua_list(rt.db.sessions))
    play.run(5)
    rt.click(ui.button_by_tooltip(rt, hud, "Start walking"))
    play.run(40)
    widgets_before_reload = ui.widget_count(rt)
    assert merges.calls <= 30, f"{merges.calls} history merges in {play.seconds:.0f} s of play"
    assert rt.undefined_globals() == {name: count for name, count in rt.undefined_globals().items() if name in ALLOWED_UNDEFINED}
    assert sorted(rt.keys(rt.log.unimplemented)) == [] and lua_dict(rt.log.lod) == {}

    # The reload is asked for with the history window's Sync button.
    assert rt.reload_requests == 0, "nothing so far has asked the game to reload"
    rt.click(ui.button(rt, ui.history(rt), "Sync"))
    assert rt.reload_requests == 1
    rt = rt.reload()
    play.rt, play.last_session = rt, None
    walka, hud = rt.walka, ui.hud(rt)
    assert walka.IsActive(walka), "the session survives the /reload"
    assert len(lua_list(rt.db.sessions)) == sessions_before
    assert "Walking" in ui.texts(rt, hud) or "Starting" in ui.texts(rt, hud)
    play.run(30)

    # -- the rest of the ten minutes: walk, with every window open ---------------------------------
    history = ui.history(rt)
    panel = ui.options(rt)
    play.run(5)
    # Everything is built now; from here on nothing new may be created.
    for widget in rt.descendants(history, visible=True) + rt.descendants(hud, visible=True):
        if rt.M.script_set(widget, "OnEnter"):
            ui.tooltip(rt, widget)
    rt.click(ui.button_by_tooltip(rt, hud, "Pause"))
    rt.click(ui.button_by_tooltip(rt, hud, "Resume"))
    ui.context_menu(rt, hud)
    widgets = ui.widget_count(rt)
    merges = ui.merge_counter(rt)
    remaining = 600 - play.seconds
    assert remaining > 60, "the script above should leave over a minute of plain walking"
    for index in range(int(remaining // 10)):
        rt.set(speed=[7.0, 7.0, 0, 14.0, 7.0, 3.0][index % 6], mounted=index % 6 == 3)
        if index % 4 == 1:
            rt.enter_combat()
        elif index % 4 == 3:
            rt.leave_combat()
        if index % 3 == 0:
            ui.slide(rt, ui.slider(rt, panel, "walking"), 1.2 + (index % 5) / 10)
            rt.M.scroll_wheel(rt.descendants(panel, "ScrollFrame")[0], -1 if index % 2 else 1)
        if index % 5 == 2:
            for widget in rt.descendants(history, "Button", visible=True):
                if rt.M.script_set(widget, "OnEnter"):
                    ui.tooltip(rt, widget)
        play.run(10)
    rt.leave_combat()
    play.run(600 - play.seconds)
    assert play.seconds == pytest.approx(600)
    assert ui.widget_count(rt) == widgets, "frames, textures or font strings were created during plain play"
    assert merges.calls <= 12, f"{merges.calls} history merges in the last minutes"

    rt.click(ui.button_by_tooltip(rt, hud, "Stop"))
    play.run(2)
    assert len(lua_list(rt.db.sessions)) == sessions_before + 1
    assert play.frames_decoded > 500

    # -- afterwards: clean in every respect -------------------------------------------------------------
    assert (full_rt.reload_requests, rt.reload_requests) == (1, 0), "the one click on Sync, and nothing else"
    for client in (full_rt, rt):
        client.check_errors()
        assert client.find_secrets() == []
        undefined = {name: count for name, count in client.undefined_globals().items() if name not in ALLOWED_UNDEFINED}
        assert undefined == {}, f"globals read that this client does not have: {undefined}"
        assert sorted(client.keys(client.log.unimplemented)) == []
        assert lua_dict(client.log.lod) == {}
        assert lua_list(client.log.blocked) == []
        leaked = sorted(name for name in client.created_globals() if name not in ALLOWED_GLOBALS and not name.startswith("Walka"))
        assert leaked == []
        getters = sorted(key for key in client.keys(client.log.noop) if key.split(":")[1].startswith(("Get", "Is", "Has", "Can")))
        assert getters == [], f"the addon relied on getters the mock only stubs: {getters}"
    assert widgets_before_reload < 1200
    again = rt.reload()                 # the saved variables are still plain, finite data
    again.tick(10)
    assert not again.walka.IsActive(again.walka)


# ---- the companion's data file can contain anything ------------------------------------------------

MALFORMED = [
    "5",
    "'text'",
    "true",
    "{}",
    "{ written = 'yesterday' }",
    "{ written = 1e300 }",
    "{ written = 99999999999 }",                 # year 5138: Windows cannot format it
    "{ written = -5 }",
    "{ written = 0/0 }",
    "{ written = {} }",
    "{ version = 99, written = false, profile = 'thin', limits = 7, sessions = 'none', older = 3, treadmill = {} }",
    "{ profile = { weightKg = 0/0, heightCm = 1/0 } }",
    "{ profile = { weightKg = 80 } }",
    "{ limits = { minKmh = 'a', maxKmh = {}, stepKmh = 0 } }",
    "{ limits = { minKmh = 6, maxKmh = 1, stepKmh = -1 } }",
    "{ limits = { minKmh = 1e-3, maxKmh = 1e6, stepKmh = 1e-7, accelKmhPerS = 0/0, decelKmhPerS = -1 } }",
    f"""{{ sessions = {{ 1, 'two', {{}}, {{ start = 'x' }}, {{ start = 1e300, ['end'] = 1e301 }},
        {{ start = -500, ['end'] = -100 }}, {{ start = {NOW - 900}, ['end'] = 99999999999 }},
        {{ start = {NOW - 4000}, ['end'] = {NOW - 3000}, seconds = 0/0, steps = 1/0, distanceM = 'far', kcal = {{}},
           maxKmh = -1/0, avgHr = 'high', maxHr = 0/0, stepsSource = 5, source = {{}} }},
        {{ start = {NOW - 2000}, ['end'] = {NOW - 1000}, seconds = -5, steps = -100, distanceM = -1, kcal = -3,
           avgHr = 1/0, maxHr = 1e300, stepsSource = '', source = 'manual' }} }} }}""",
    "{ sessions = { [2] = { start = 5, ['end'] = 9 } } }",
    "{ older = { sessions = 'many', steps = 0/0, seconds = 1/0, distanceM = {}, kcal = 'x' } }",
    "{ older = { sessions = -3, steps = -1e300, seconds = -5, distanceM = -1, kcal = -2 } }",
]

DAMAGED_HISTORY = (
    "{sessions={5,'x',{},{start='x'},{start=5,['end']=2,steps=-4},{start=1e12,['end']=1e12+5,seconds=5},"
    f"{{start={NOW - 7000},['end']={NOW - 6000},seconds=900,steps='lots',distanceM={{}},kcal=false,yards='far',"
    "character=5,maxKmh='fast'},"
    f"{{start={NOW - 5000},['end']=1e300,seconds=1e300,steps=1e300,distanceM=1e300,kcal=1e300,yards=1e300,character={{}}}},"
    f"{{id=7,start={NOW - 3000},['end']={NOW - 1200},seconds=1750,distanceM=1400,steps=2000,kcal=100.5,yards=9000,"
    "maxKmh=3.2,character='Tester-Azeroth'}}}"
)


@pytest.mark.parametrize("data", MALFORMED, ids=[f"data{index}" for index in range(len(MALFORMED))])
@pytest.mark.parametrize("own_history", ["{}", DAMAGED_HISTORY], ids=["clean", "damaged"])
def test_windows_survive_any_companion_data(ui_rt, make_rt, data, own_history):
    rt = make_rt(
        files={"CompanionData.lua": f"WalkaCompanionData = {data}"},
        saved_variables={"WalkaDB": own_history},
        state={"clock": NOW},
    )
    hud = ui.hud(rt)
    rt.tick(5)
    assert ui.tooltip(rt, hud)[0] == "Walka"
    rt.slash("/walka status")
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(80)

    history = ui.history(rt)
    rt.tick(5)
    wheel = next(widget for widget in rt.descendants(history, visible=True) if rt.M.script_set(widget, "OnMouseWheel"))
    for delta in (-1, -1, -1, 1, 1, 1):
        rt.M.scroll_wheel(wheel, delta)
        for widget in rt.descendants(history, visible=True):
            if rt.M.script_set(widget, "OnEnter"):
                ui.tooltip(rt, widget)

    panel = ui.options(rt)
    rt.tick(5)
    for slider in rt.descendants(panel, "Slider"):
        low, high = slider.GetMinMaxValues(slider)
        assert math.isfinite(low) and math.isfinite(high) and high > low
        ui.slide(rt, slider, (low + high) / 2)
    for box in rt.descendants(panel, "CheckButton"):
        rt.click(box)
        rt.click(box)
    rt.click(ui.button(rt, panel, "km/h / km"))
    rt.tick(250)
    rt.slash("/walka stop")
    rt.tick(5)

    settings_are_finite(rt)
    assert rt.find_secrets() == []
    # Every text on screen is text: no "nan", "inf" or raw table address.
    for frame in (hud, history, panel):
        for text in ui.texts(rt, frame):
            assert not any(bad in text.lower() for bad in ("nan", "inf", "table:", "function:")), text
    fresh = rt.reload()                  # and the saved variables can be written and read back
    fresh.tick(5)
    assert ui.tooltip(fresh, ui.hud(fresh))[0] == "Walka"


# ---- UI scale and resolution -------------------------------------------------------------------------


@pytest.mark.parametrize("screen", [(1280, 720), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160), (1280, 1024)])
@pytest.mark.parametrize("ui_scale", [0.64, 1.0, 1.15])
def test_windows_at_other_resolutions_and_ui_scales(full_rt, screen, ui_scale):
    rt = full_rt
    rt.M.set_screen(*screen)
    rt.M.set_ui_scale(ui_scale)
    hud, history = ui.hud(rt), ui.history(rt)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(50)
    screen_width, screen_height = rt.M.screen_size()

    for frame, (width, height) in ((hud, (224, 116)), (history, (560, 440))):
        box = ui.rect(rt, frame)
        assert (box[2], box[3]) == pytest.approx((width * ui_scale, height * ui_scale))
        assert box[0] >= -0.01 and box[1] >= -0.01
        assert box[0] + box[2] <= screen_width + 0.01 and box[1] + box[3] <= screen_height + 0.01, "window off screen"
        # Everything inside a window scales with it and stays inside.
        for widget in rt.descendants(frame, "Button", visible=True) + ui.font_strings(rt, frame, visible=True):
            inner = ui.rect(rt, widget)
            if inner is not None and (rt.inspect(widget)["type"] == "Button" or rt.inspect(widget).get("text")):
                assert ui.inside(inner, box, slack=1.5), rt.inspect(widget).get("text")

    # The HUD is where it would be at any scale: 40 units in from the left, a little above the middle.
    box = ui.rect(rt, hud)
    assert box[0] == pytest.approx(40 * ui_scale)
    assert box[1] + box[3] / 2 == pytest.approx(screen_height / 2 + 80 * ui_scale)

    # Dragging and the scroll bar work in scaled coordinates.
    assert rt.M.drag(hud, 100, -50) is True
    moved = ui.rect(rt, hud)
    assert (moved[0], moved[1]) == pytest.approx((box[0] + 100, box[1] - 50), abs=1)
    tracks = [w for w in rt.descendants(history, "Frame") if rt.M.script_set(w, "OnMouseDown") and not ui.same(rt, w, history)]
    track, thumb = tracks[0], lua_list(rt.M.children(tracks[0]))[0]
    track_box, thumb_box = ui.rect(rt, track), ui.rect(rt, thumb)
    grab = thumb_box[1] + thumb_box[3] / 2
    rt.set(cursorX=track_box[0] + 2, cursorY=grab)
    rt.M.fire_script(thumb, "OnMouseDown", "LeftButton")
    rt.set(cursorY=grab - (track_box[3] - thumb_box[3]))
    rt.frame(0.02)
    rt.M.fire_script(thumb, "OnMouseUp", "LeftButton")
    rt.check_errors()
    assert any(text.endswith("of 121") and text.startswith("114-121") for text in ui.texts(rt, history)), (
        "dragging the thumb to the bottom of its track shows the end of the list"
    )

    # The beacon does not care about any of this: it is drawn in physical pixels.
    assert beacon.decode_row(rt.render_beacon_row(0)) is not None
    fresh = rt.reload()
    fresh.M.set_screen(*screen)
    fresh.M.set_ui_scale(ui_scale)
    assert ui.rect(fresh, ui.hud(fresh))[:2] == pytest.approx(moved[:2], abs=1)
