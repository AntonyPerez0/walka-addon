"""The addon's footprint in the global environment.

Reading a global that does not exist returns nil in the game - which is how
an addon probes for optional APIs - so the mock allows it, but writes every
such name down. After a session that touches everything the addon has, each
name must be on a short allowlist; anything else is a typo, an API this
client does not have, or something the mock has yet to learn.
"""

from __future__ import annotations

import re

import pytest

from .conftest import ui_files_present
from .harness import ALLOWED_GLOBALS, ALLOWED_UNDEFINED, lua_list


@pytest.fixture
def busy_rt(make_rt):
    """A client that has been through everything: session, pause, combat
    with secrets, slash commands, every window (when present), a reload."""
    companion = (
        "WalkaCompanionData = { written = 1795000000, profile = { weightKg = 80, heightCm = 180 },\n"
        "  limits = { minKmh = 1, maxKmh = 6, stepKmh = 0.1 },\n"
        "  sessions = { { start = 1794990000, ['end'] = 1794991800, seconds = 1790, steps = 2700, distanceM = 1500, kcal = 110 } } }"
    )
    rt = make_rt(files={"CompanionData.lua": companion})
    rt.set(speed=7.0)
    for command in ("start", "status", "pause", "resume", "history", "options", "weight 80kg", "height 180cm",
                    "units kmh", "units mph", "beacon size 4", "beacon off", "beacon on", "help", "hide", "show"):
        rt.slash("/walka " + command)
        rt.tick(3)
    rt.tick(250)
    rt.enter_combat()
    rt.set(chatLockdown=True, nameSecret=True, mounted=True, afk=True)
    rt.tick(30)
    rt.slash("/walka options")          # refused in combat
    rt.leave_combat()
    rt.set(chatLockdown=False, nameSecret=False, mounted=False, afk=False, taxi=True)
    rt.tick(30)
    rt.M.set_screen(2560, 1440)
    rt.M.set_ui_scale(0.8)
    if ui_files_present():
        frames = [rt.global_(name) for name in sorted(rt.created_globals()) if name != "WalkaBeaconFrame"]
        for frame in [frame for frame in frames if rt.M.is_widget(frame)]:
            if "Frame" not in rt.snapshot.type_chain(rt.inspect(frame)["type"]):
                continue
            for widget in rt.descendants(frame, visible=True):
                if rt.M.script_set(widget, "OnEnter"):
                    rt.hover(widget)
            for button in rt.descendants(frame, "Button", visible=True) + rt.descendants(frame, "CheckButton", visible=True):
                if rt.inspect(button)["visible"]:
                    rt.click(button)
                    rt.tick(1)
            for slider in rt.descendants(frame, "Slider", visible=True):
                low, high = slider.GetMinMaxValues(slider)
                rt.M.slide(slider, (low + high) / 2)
            for box in rt.descendants(frame, "EditBox", visible=True):
                rt.M.type_text(box, "75", True)
        hud = rt.global_("WalkaHUD")
        if hud is not None and rt.inspect(hud)["visible"]:
            rt.M.fire_script(hud, "OnMouseUp", "RightButton")
        click = rt.global_(rt.toc.metadata["AddonCompartmentFunc"])
        rt.M.call(click, "Walka", "LeftButton")
        rt.M.call(click, "Walka", "RightButton")
    rt.slash("/walka stop")
    fresh = rt.reload()
    fresh.slash("/walka start")
    fresh.tick(50)
    return rt, fresh


def test_no_undefined_globals_are_read(busy_rt, snapshot):
    problems = []
    for rt in busy_rt:
        for name in sorted(rt.undefined_globals()):
            if name in ALLOWED_UNDEFINED:
                continue
            known = snapshot.knows_member(name) if "." in name else snapshot.knows_global(name)
            if known:
                problems.append(f"{name}: exists in this client, but the mock returned nil - give the mock an implementation")
            else:
                problems.append(f"{name}: does not exist in this client (a typo, or an API from another WoW version?)")
    assert problems == []


def test_everything_the_addon_uses_is_implemented_by_the_mock(busy_rt):
    # A name the snapshot knows but the mock only stubs would hide what the
    # addon does with it.
    for rt in busy_rt:
        assert sorted(rt.keys(rt.log.unimplemented)) == []


def test_nothing_from_a_load_on_demand_blizzard_addon_is_used(busy_rt):
    for rt in busy_rt:
        assert {str(k): str(v) for k, v in rt.log.lod.items()} == {}


def test_allowlist_is_not_stale(busy_rt, snapshot):
    read = set()
    for rt in busy_rt:
        read |= set(rt.undefined_globals())
    for name, reason in ALLOWED_UNDEFINED.items():
        assert reason.strip(), f"{name} needs a reason"
        assert name in read, f"{name} is on the allowlist but the addon no longer reads it"
        assert not snapshot.knows_global(name), f"{name} exists in this client: it does not belong on the allowlist"


def test_the_addon_creates_only_its_own_globals(busy_rt):
    for rt in busy_rt:
        created = rt.created_globals()
        unexpected = sorted(name for name in created if name not in ALLOWED_GLOBALS and not name.startswith("Walka"))
        assert unexpected == [], f"globals leaked: {unexpected}"
        assert {"Walka", "WalkaDB", "SLASH_WALKA1", "Walka_OnCompartmentClick"} <= created | {"WalkaDB"}


def test_named_frames_start_with_walka(busy_rt):
    for rt in busy_rt:
        for widget in lua_list(rt.M.widgets):
            info = rt.inspect(widget)
            name = info.get("name")
            if name and name in rt.created_globals():
                assert name.startswith("Walka"), name


def test_getters_the_addon_relies_on_are_really_implemented(busy_rt):
    # A widget method the mock accepts as a no-op returns nil. That is fine
    # for setters; a getter the addon reads must return something real.
    for rt in busy_rt:
        getters = sorted(
            key for key in rt.keys(rt.log.noop)
            if re.match(r"\w+:(Get|Is|Has|Can|Does|Num)", key)
        )
        assert getters == [], f"give these a real implementation in wow_env.lua: {getters}"


def test_toc_metadata(rt, snapshot):
    meta = rt.toc.metadata
    # The client the addon is written for, then the next patch (listed ahead
    # of its release, so that the addon is not flagged as out of date).
    assert meta["Interface"] == "16001, 16002"
    assert rt.run("return (select(4, GetBuildInfo()))") == 16001
    assert meta["SavedVariables"] == "WalkaDB"
    assert rt.global_(meta["AddonCompartmentFunc"]) is not None
    assert rt.toc.files[0] == "CompanionData.lua", "the companion's data must be loaded before the code that reads it"
    core = [name for name in rt.toc.files if not name.startswith("UI")]
    assert core == rt.loaded_files[: len(core)]
    # C_AddOns.GetAddOnMetadata is how this client exposes the TOC fields.
    assert "GetAddOnMetadata" in snapshot.namespaces["C_AddOns"]
    assert rt.run("return C_AddOns.GetAddOnMetadata('Walka', 'Version')") == meta["Version"]


def test_core_files_do_not_need_the_wow_api(addon_dir):
    # Protocol, SpeedModel, Metrics and SessionLog promise to be pure Lua 5.1
    # so that other tools can run them: load each in a bare Lua state.
    from lupa.lua51 import LuaRuntime

    for name in ("Protocol", "SpeedModel", "Metrics", "SessionLog"):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(
            "for _, name in ipairs({'CreateFrame', 'GetTime', 'date', 'time', 'print', 'wipe', 'tinsert', 'format'}) do _G[name] = nil end"
        )
        load = lua.eval("function(source, name) local chunk = assert(loadstring(source, name)) return chunk() end")
        module = load((addon_dir / "Core" / f"{name}.lua").read_text(encoding="utf-8"), f"@{name}.lua")
        assert module is not None, f"Core/{name}.lua must return its table when loaded on its own"
