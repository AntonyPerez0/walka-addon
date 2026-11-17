"""Slash commands: /walka ..."""

from __future__ import annotations

import pytest
from walka import beacon

from .conftest import KMH_PER_MPH, MPH_STEP, nearest_step
from .harness import lua_plain

LB = 0.45359237


def settings(rt):
    return lua_plain(rt.db.settings)


def last(rt) -> str:
    return rt.printed[-1]


# ---- weight ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, kg",
    [
        ("82kg", 82.0),
        ("82 kg", 82.0),
        ("82.5kg", 82.5),
        ("82 KG", 82.0),
        ("180lb", 81.6),
        ("180 lbs", 81.6),
        ("180LB", 81.6),
        ("  95.4kg  ", 95.4),
    ],
)
def test_weight_with_a_unit(rt, text, kg):
    rt.slash("/walka weight " + text)
    assert settings(rt)["profile"]["weightKg"] == pytest.approx(kg, abs=0.05)
    assert "weight set to" in last(rt)


def test_weight_without_a_unit_uses_the_display_units(rt):
    assert settings(rt)["units"] == "mph"           # enUS default
    rt.slash("/walka weight 180")
    assert settings(rt)["profile"]["weightKg"] == pytest.approx(180 * LB, abs=0.05)
    rt.slash("/walka units kmh")
    rt.slash("/walka weight 70")
    assert settings(rt)["profile"]["weightKg"] == 70


@pytest.mark.parametrize(
    "text",
    [
        "",                 # nothing
        "heavy",            # not a number
        "-80kg",            # negative
        "1.2.3kg",          # not a number either
        "20kg",             # too light to be an adult
        "400kg",            # too heavy
        "900lb",
        "nan",
        "inf",
        "180 pounds",       # a unit the command does not know must not be read as kg
        "12 stone",
        "5'11",             # a height
    ],
)
def test_bad_weight_is_refused_and_changes_nothing(rt, text):
    rt.slash("/walka weight " + text)
    assert settings(rt)["profile"]["weightKg"] == 75
    assert "weight set" not in last(rt)
    assert "weight" in last(rt), "the user should be told how to use the command"


def test_weight_feeds_the_calorie_estimate(rt):
    light = rt
    light.slash("/walka weight 50kg")
    light.slash("/walka start")
    light.tick(300)
    kcal_light = light.walka.GetSession(light.walka).kcal

    rt.slash("/walka stop")
    rt.slash("/walka weight 100kg")
    rt.slash("/walka start")
    rt.tick(300)
    assert rt.walka.GetSession(rt.walka).kcal == pytest.approx(2 * kcal_light, rel=0.01)


# ---- height ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, cm",
    [
        ("180cm", 180),
        ("180 cm", 180),
        ("1.8m", 180),
        ("71in", 180),
        ("71 inches", 180),
        ("5'11", 180),
        ("5' 11", 180),
        ("5'11\"", 180),
        ("6'", 183),
        ("6'0", 183),
        ("165.4cm", 165),
    ],
)
def test_height_with_a_unit(rt, text, cm):
    rt.slash("/walka height " + text)
    assert settings(rt)["profile"]["heightCm"] == cm
    assert f"height set to {cm} cm" in last(rt)


def test_height_without_a_unit_uses_the_display_units(rt):
    rt.slash("/walka height 70")                    # inches for mph users
    assert settings(rt)["profile"]["heightCm"] == 178
    rt.slash("/walka units kmh")
    rt.slash("/walka height 168")
    assert settings(rt)["profile"]["heightCm"] == 168


@pytest.mark.parametrize("text", ["", "tall", "50cm", "300cm", "3in", "12'", "-180cm", "nan", "180", "180lb", "170 kg", "6 ft"])
def test_bad_height_is_refused_and_changes_nothing(rt, text):
    # ("180" is refused for an mph user: 180 inches is not a height.)
    rt.slash("/walka height " + text)
    assert settings(rt)["profile"]["heightCm"] == 172
    assert "height set" not in last(rt)
    assert "height" in last(rt)


# ---- units ----------------------------------------------------------------------------


def test_units(rt):
    walka = rt.walka
    assert walka.IsMph(walka) and walka.SpeedUnit(walka) == "mph"
    rt.slash("/walka units kmh")
    assert settings(rt)["units"] == "kmh" and "km/h" in last(rt)
    assert walka.FormatSpeed(walka, 3.2) == "3.2 km/h"
    rt.slash("/walka units MPH")
    assert settings(rt)["units"] == "mph" and "mph" in last(rt)
    assert walka.FormatSpeed(walka, 3.218688) == "2.0 mph"
    rt.slash("/walka units km/h")
    assert settings(rt)["units"] == "kmh"


@pytest.mark.parametrize("text", ["", "miles", "furlongs per fortnight", "kph"])
def test_bad_units_are_refused(rt, text):
    rt.slash("/walka units " + text)
    assert settings(rt)["units"] == "mph"
    assert "usage" in last(rt)


def test_units_change_the_belt_grid(rt):
    walka = rt.walka
    limits = lua_plain(walka.GetBeltLimits(walka))
    assert limits["stepKmh"] == pytest.approx(MPH_STEP)
    assert limits["minKmh"] == pytest.approx(0.6 * KMH_PER_MPH)
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(400)
    # The running pace in whole tenths of a mph ...
    running = walka.Get(walka, "speed.runKmh")
    in_mph, in_kmh = nearest_step(running, MPH_STEP), nearest_step(running, 0.1)
    assert abs(in_mph - in_kmh) > 0.01, "the two grids give different targets for this pace"
    assert walka.GetLiveSpeed(walka)[1] == pytest.approx(in_mph, abs=1e-6)
    rt.slash("/walka units kmh")
    limits = lua_plain(walka.GetBeltLimits(walka))
    assert (limits["minKmh"], limits["maxKmh"], limits["stepKmh"]) == (1.0, 6.0, 0.1)
    # ... and in whole tenths of a km/h; the setting itself is untouched.
    rt.tick(100)
    assert walka.GetLiveSpeed(walka)[1] == pytest.approx(in_kmh, abs=1e-6)
    assert walka.Get(walka, "speed.runKmh") == running


def test_default_units_follow_the_client_language(make_rt):
    assert settings(make_rt(state={"locale": "enUS"}))["units"] == "mph"
    assert settings(make_rt(state={"locale": "deDE"}))["units"] == "kmh"
    assert settings(make_rt(state={"locale": "frFR"}))["units"] == "kmh"


# ---- beacon ---------------------------------------------------------------------------


def test_beacon_on_off(rt):
    rt.slash("/walka beacon off")
    assert settings(rt)["beacon"]["enabled"] is False and "beacon off" in last(rt)
    rt.slash("/walka beacon ON")
    assert settings(rt)["beacon"]["enabled"] is True and "beacon on" in last(rt)


@pytest.mark.parametrize("text, size", [("2", 2), ("5", 5), ("8", 8), ("1", 2), ("99", 8), ("3.4", 3), ("0", 2), ("-4", 2)])
def test_beacon_size(rt, text, size):
    rt.slash("/walka beacon size " + text)
    assert settings(rt)["beacon"]["cellPx"] == size
    assert f"{size} px" in last(rt)
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0)).cell_px == pytest.approx(size, abs=0.02)


@pytest.mark.parametrize("text", ["", "size", "size big", "bigger", "size nan", "size -nan", "size inf", "size 1e999", "size 0x"])
def test_bad_beacon_arguments_leave_a_working_beacon(rt, text):
    rt.slash("/walka beacon " + text)
    size = settings(rt)["beacon"]["cellPx"]
    assert size == size and 2 <= size <= 8, "the stored cell size must be a real number in range"
    assert settings(rt)["beacon"]["enabled"] is True
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0)) is not None
    # ... and the saved variables must still be writable by the client.
    assert rt.reload().db.settings.beacon.cellPx == size


# ---- status, help, session commands ---------------------------------------------------------


def test_status_idle(rt):
    rt.set(speed=7.0)
    rt.tick()
    rt.clear_printed()
    rt.slash("/walka status")
    first, second = rt.printed
    assert "session off" in first
    assert "character 7.0 yd/s" in first and "estimated" not in first
    assert "target 0.0 mph" in first and "belt (est.) 0.0 mph" in first
    assert "no companion data yet" in second


def test_status_during_a_session_and_in_combat(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(300)
    rt.clear_printed()
    rt.slash("/walka status")
    running_mph = nearest_step(rt.ns.SpeedModel.DEFAULTS.runKmh, MPH_STEP) / KMH_PER_MPH
    assert "session active" in rt.printed[0]
    assert f"target {running_mph:.1f} mph | belt (est.) {running_mph:.1f} mph" in rt.printed[0]
    rt.enter_combat()
    rt.tick()
    rt.slash("/walka pause")
    rt.clear_printed()
    rt.slash("/walka status")
    assert "session paused" in rt.printed[0]
    assert "(estimated: hidden in combat)" in rt.printed[0]


def test_status_before_the_first_tick(rt):
    rt.slash("/walka status")               # right after login
    assert "session off" in rt.printed[0]


def test_status_reports_companion_data(make_rt):
    rt = make_rt(files={"CompanionData.lua": "WalkaCompanionData = { written = 1795000000, sessions = {} }"})
    rt.slash("/walka status")
    assert rt.printed[-1].startswith("|cff4fc3f7Walka|r: companion data from 20")


def test_unknown_command_prints_help(rt):
    rt.slash("/walka frobnicate")
    assert len(rt.printed) >= 8
    assert all(line.startswith("|cff4fc3f7Walka|r: /walka") for line in rt.printed)
    for word in ("start", "history", "journey", "options", "status", "weight", "height", "units", "beacon"):
        assert any(word in line for line in rt.printed), word
    assert (
        "|cff4fc3f7Walka|r: /walka journey - your character's journey so far: level, time played, "
        "your steps and its steps"
    ) in rt.printed


@pytest.mark.parametrize("command", ["help", "?"])
def test_help_commands(rt, command):
    rt.slash("/walka " + command)
    assert len(rt.printed) >= 8


def test_commands_ignore_case_and_extra_space(rt):
    rt.slash("/WALKA   START  ")
    assert rt.walka.IsActive(rt.walka)
    rt.slash("/walka PAUSE")
    assert rt.walka.IsPaused(rt.walka)
    rt.slash("/walka go")                   # alias of start
    assert not rt.walka.IsPaused(rt.walka)
    rt.slash("/walka end")                  # alias of stop
    assert not rt.walka.IsActive(rt.walka)


def test_slash_command_is_registered_the_way_the_client_expects(rt):
    assert rt.global_("SLASH_WALKA1") == "/walka"
    assert rt.global_("SlashCmdList").WALKA is not None
