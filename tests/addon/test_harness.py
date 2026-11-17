"""The mock client itself: it must be strict where the game is strict, and
it must not offer anything the game does not have."""

from __future__ import annotations

import pytest

from .harness import WowRuntime, lua_dict, lua_list


@pytest.fixture
def bare(make_rt) -> WowRuntime:
    """A mock client without the addon."""
    return make_rt(load=False)


def raises(runtime, source, match):
    with pytest.raises(Exception, match=match):
        runtime.run(source)


# ---- the mock offers nothing the client lacks ---------------------------------------------

# Globals the mock provides that the snapshot cannot confirm, with the reason.
UNVERIFIED_GLOBALS = {
    "rawequal": "Lua 5.1 base library; Blizzard's own code happens not to use it",
}


def test_every_global_the_mock_defines_exists_in_the_client(bare, snapshot):
    unknown = sorted(
        name for name in bare.M.defined.keys()
        if not snapshot.knows_global(name) and name not in UNVERIFIED_GLOBALS
    )
    assert unknown == [], f"the mock defines globals this client does not have: {unknown}"
    for name in UNVERIFIED_GLOBALS:
        assert not snapshot.knows_global(name), f"{name} is in the snapshot now: drop it from UNVERIFIED_GLOBALS"


def test_every_widget_method_the_mock_implements_exists_on_that_widget_type(bare):
    assert lua_list(bare.M.invalid_implementations()) == []


def test_namespace_implementations_exist_in_the_client(bare, snapshot):
    for namespace, functions in bare.M.namespace_impl.items():
        assert namespace in snapshot.namespaces, namespace
        for name in functions.keys():
            assert name in snapshot.namespaces[namespace], f"{namespace}.{name}"


def test_mixin_behaviour_matches_real_mixin_methods(bare, snapshot):
    for mixin, implementation in bare.M.mixin_impl.items():
        members = snapshot.mixin_members(mixin)
        assert members is not None, f"{mixin} is not a mixin of this client"
        for name in implementation.keys():
            if name == "__init" or name in ("Event", "Label"):
                continue        # set-up hook; constants the real mixin builds at load time
            assert name in members, f"{mixin}:{name} does not exist in this client"


def test_settings_functions_exist_in_the_client(bare, snapshot):
    settings = bare.global_("Settings") or bare.run("return Settings")
    for name in ("RegisterCanvasLayoutCategory", "RegisterAddOnCategory", "OpenToCategory"):
        assert name in snapshot.members["Settings"]
        assert settings[name] is not None
    category_methods = snapshot.mixin_members("SettingsCategoryMixin")
    assert "GetID" in category_methods and "GetName" in category_methods


# ---- globals --------------------------------------------------------------------------------


def test_reading_an_unknown_global_gives_nil_and_is_recorded(bare):
    assert bare.run("return NoSuchGlobalAnywhere") is None
    assert bare.run("return type(InterfaceOptions_AddCategory)") == "nil"      # Classic-era API
    assert bare.run("return GetAddOnMetadata == nil") is True                   # removed in favour of C_AddOns
    recorded = bare.undefined_globals()
    assert recorded["NoSuchGlobalAnywhere"] == 1
    assert "InterfaceOptions_AddCategory" in recorded and "GetAddOnMetadata" in recorded


def test_blizzard_globals_appear_on_first_use(bare):
    assert bare.run("return GameFontNormal:GetObjectType()") == "Font"
    assert bare.run("return GameTooltip:GetObjectType()") == "GameTooltip"
    assert bare.run("return UIParent:GetObjectType()") == "Frame"
    assert bare.run("return type(C_Timer.After)") == "function"
    assert bare.run("return type(NORMAL_FONT_COLOR.r)") == "number"
    assert bare.run("return type(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)") == "number"
    assert bare.run("return type(Enum.SecretAspect)") == "table"
    assert bare.undefined_globals() == {}


def test_known_but_unimplemented_functions_fail_loudly_when_called(bare):
    assert bare.run("return type(UnitHealth)") == "function"           # exists in the client ...
    raises(bare, "UnitHealth('player')", "no implementation")          # ... but not in the mock
    assert "UnitHealth" in bare.keys(bare.log.unimplemented)
    assert bare.run("return type(C_Map.GetBestMapForUnit)") == "function"
    raises(bare, "C_Map.GetBestMapForUnit('player')", "no implementation")
    assert bare.run("return C_Timer.NoSuchFunction") is None
    assert "C_Timer.NoSuchFunction" in bare.undefined_globals()


def test_reload_ui_is_recorded_and_the_lua_state_lives_on(bare, snapshot):
    assert snapshot.knows_global("ReloadUI")
    assert bare.reload_requests == 0
    bare.run("KeptAcrossTheCall = 5 ReloadUI()")
    assert bare.reload_requests == 1
    bare.run("ReloadUI()")
    assert bare.reload_requests == 2
    assert lua_list(bare.log.reloads) == [bare.game.clock] * 2, "when it was asked for"
    # Nothing was torn down: a test can look at what the addon did before.
    assert bare.run("return KeptAcrossTheCall") == 5
    assert "ReloadUI" not in bare.keys(bare.log.unimplemented)
    # ReloadUI() is Blizzard Lua around the client's own C_UI.Reload().
    bare.run("C_UI.Reload()")
    assert bare.reload_requests == 3
    assert bare.keys(bare.log.unimplemented) == set() and bare.undefined_globals() == {}


def test_load_on_demand_names_are_not_there_at_login(bare, snapshot):
    name = next(name for name, addon in snapshot.lod.items() if addon == "Blizzard_AuctionHouseUI")
    assert bare.run(f"return {name} == nil") is True
    assert lua_dict(bare.log.lod)[name] == "Blizzard_AuctionHouseUI"


def test_globals_created_by_addon_code_are_recorded(bare):
    bare.run("MyLeak = 1; local hidden = 2")
    bare.run("CreateFrame('Frame', 'MyNamedFrame')")
    assert {"MyLeak", "MyNamedFrame"} <= bare.created_globals()
    assert "hidden" not in bare.created_globals()


def test_no_os_io_or_require(bare):
    for name in ("os", "io", "require", "dofile", "package", "debug", "load"):
        assert bare.run(f"return {name} == nil") is True


# ---- widgets --------------------------------------------------------------------------------


def test_methods_are_checked_per_widget_type(bare):
    bare.run("local f = CreateFrame('Button'); f:SetText('ok')")
    raises(bare, "local f = CreateFrame('Frame'); f:SetText('no')", "attempt to call method 'SetText'")
    raises(bare, "local f = CreateFrame('Frame'); f:SetVertexColor(1, 1, 1)", "attempt to call method")
    raises(bare, "local f = CreateFrame('Frame'); f:CreateTexture():SetFrameStrata('LOW')", "attempt to call method")
    raises(bare, "local f = CreateFrame('Frame'); f:SetBackdrop({})", "attempt to call method 'SetBackdrop'")
    raises(bare, "local f = CreateFrame('Frame'); f:SetMinResize(1, 1)", "attempt to call method")      # removed API
    assert bare.run("local f = CreateFrame('Frame'); return f.SetText == nil and f.anything == nil") is True


def test_frame_types_templates_and_fonts_must_exist(bare):
    raises(bare, "CreateFrame('Fraem')", "unknown frame type")
    raises(bare, "CreateFrame('Texture')", "unknown frame type")
    raises(bare, "CreateFrame('Frame', nil, UIParent, 'NoSuchTemplate')", "no template named")
    # In Blizzard's source, but only loaded on the login screen:
    raises(bare, "CreateFrame('Frame', nil, UIParent, 'AccountLoginBackdropTemplate')", "no template named")
    raises(bare, "CreateFrame('Frame', nil, UIParent, 'UIPanelButtonTemplate')", "cannot be used on a Frame")
    raises(bare, "CreateFrame('Frame', nil, 'NoSuchParent')", "no frame named")
    raises(bare, "CreateFrame('Frame'):CreateFontString(nil, 'OVERLAY', 'GameFontNormel')", "no template named")
    raises(bare, "CreateFrame('Frame'):CreateFontString():SetFontObject('GameFontNormel')", "no font object named")
    raises(bare, "CreateFrame('Frame'):CreateTexture(nil, 'FOREGROUND')", "unknown draw layer")
    bare.run("CreateFrame('Frame'):CreateFontString(nil, 'OVERLAY', 'GameFontNormal'):SetFontObject(GameFontHighlight)")
    bare.run("CreateFrame('EventFrame')")           # an intrinsic frame type


def test_load_on_demand_templates_are_refused(bare, snapshot):
    template = next(
        name for name, addon in snapshot.lod.items()
        if addon == "Blizzard_AuctionHouseUI" and name.endswith("Template")
    )
    raises(bare, f"CreateFrame('Frame', nil, UIParent, '{template}')", "load-on-demand")


def test_templates_build_their_keyed_children_and_mixins(bare):
    assert bare.run(
        "local b = CreateFrame('Button', 'WalkaTestButton', UIParent, 'UIPanelButtonTemplate') "
        "b:SetText('Go') return b.Text:GetText(), b:GetFontString() == b.Text, WalkaTestButtonText == b.Text"
    ) == ("Go", True, True)
    assert bare.run(
        "local f = CreateFrame('Frame', nil, UIParent, 'BackdropTemplate') "
        "f:SetBackdrop({ edgeSize = 14 }) f:SetBackdropBorderColor(0.1, 0.2, 0.3, 1) "
        "return f:GetBackdrop().edgeSize, (select(2, f:GetBackdropBorderColor()))"
    ) == (14, 0.2)
    assert bare.run(
        "local c = CreateFrame('CheckButton', nil, UIParent, 'UICheckButtonTemplate') "
        "return c.Text:GetObjectType(), c:GetChecked()"
    ) == ("FontString", False)


def test_a_template_with_a_size_of_its_own_gives_it_to_the_frame(bare, snapshot):
    # The close button is 24 x 24 in Blizzard's XML; other frames are placed against it.
    made = "local b = CreateFrame('Button', nil, UIParent, '{}') "
    assert bare.run(made.format("UIPanelCloseButtonNoScripts") + "return b:GetWidth(), b:GetHeight()") == (24, 24)
    assert bare.run(made.format("UIPanelCloseButton") + "return b:GetSize()") == (24, 24), "inherited as well"
    # A size the addon sets replaces it.
    assert bare.run(made.format("UIPanelCloseButtonNoScripts") + "b:SetSize(30, 20) return b:GetSize()") == (30, 20)
    # It has a rectangle as soon as it is anchored, like any sized frame.
    assert bare.run(
        made.format("UIPanelCloseButtonNoScripts")
        + "b:SetPoint('TOPRIGHT', UIParent, 'TOPRIGHT', -3, -3) return UIParent:GetRight() - b:GetLeft(), b:GetRight() - b:GetLeft()"
    ) == (27, 24)
    for name in bare.M.template_size.keys():
        assert name in snapshot.templates, f"{name} is not a template of this client"


def test_script_handlers_are_checked_per_widget_type(bare):
    bare.run("CreateFrame('Button'):SetScript('OnClick', function() end)")
    raises(bare, "CreateFrame('Frame'):SetScript('OnClick', function() end)", 'no "OnClick" script')
    raises(bare, "CreateFrame('Frame'):SetScript('OnUpdte', function() end)", "no \"OnUpdte\" script")
    raises(bare, "CreateFrame('Slider'):HookScript('OnTextChanged', function() end)", "OnTextChanged")
    raises(bare, "CreateFrame('Frame'):SetScript('OnUpdate', 'not a function')", "must be a function")
    assert bare.run("return CreateFrame('Frame'):HasScript('OnClick'), CreateFrame('Button'):HasScript('OnClick')") == (False, True)


def test_events_must_exist(bare):
    bare.run("CreateFrame('Frame'):RegisterEvent('PLAYER_LOGOUT')")
    raises(bare, "CreateFrame('Frame'):RegisterEvent('PLAYER_LOGOFF')", "unknown event")
    with pytest.raises(Exception, match="unknown event"):
        bare.fire_event("NOT_AN_EVENT")


def test_events_reach_registered_frames_in_order(bare):
    bare.run(
        "seen = {} "
        "for i = 1, 3 do local f = CreateFrame('Frame') f:RegisterEvent('PLAYER_LOGIN') "
        "f:SetScript('OnEvent', function(self, event, ...) seen[#seen + 1] = i .. event .. select('#', ...) end) end "
        "local quiet = CreateFrame('Frame') quiet:RegisterEvent('PLAYER_LOGIN') quiet:UnregisterEvent('PLAYER_LOGIN') "
        "quiet:SetScript('OnEvent', function() seen[#seen + 1] = 'quiet' end)"
    )
    assert bare.fire_event("PLAYER_LOGIN") == 3
    assert lua_list(bare.global_("seen")) == ["1PLAYER_LOGIN0", "2PLAYER_LOGIN0", "3PLAYER_LOGIN0"]


def test_argument_checks(bare):
    raises(bare, "CreateFrame('Frame'):SetPoint('TOPLEFFT')", "unknown anchor point")
    raises(bare, "CreateFrame('Frame'):SetPoint('TOPLEFT', UIParent, 'MIDDLE')", "unknown relative point")
    raises(bare, "CreateFrame('Frame'):SetPoint('TOPLEFT', 'NoSuchFrame', 'TOPLEFT')", "no region named")
    raises(bare, "CreateFrame('Frame'):SetSize(0/0, 10)", "finite number")
    raises(bare, "CreateFrame('Frame'):SetWidth('10px')", "finite number")
    raises(bare, "CreateFrame('Frame'):SetScale(0)", "greater than 0")
    raises(bare, "CreateFrame('Frame'):SetFrameStrata('TOP')", "unknown strata")
    raises(bare, "CreateFrame('Frame'):SetFrameLevel(20000)", "0..10000")
    raises(bare, "CreateFrame('Frame'):StartMoving()", "not movable")
    raises(bare, "local f = CreateFrame('Frame'); f.SetSize(10, 10)", "not a widget")
    raises(bare, "CreateFrame('Frame'):CreateTexture():SetColorTexture(1, 1)", "finite number")
    raises(bare, "GameTooltip:SetOwner(UIParent, 'ANCHOR_MIDDLE')", "unknown anchor")
    raises(bare, "PlaySound('click')", "must be a number")
    raises(bare, "C_Timer.After('soon', print)", "non-negative number")
    raises(bare, "UnitName()", "Usage")


def test_anchor_cycles_are_refused(bare):
    raises(bare, "local f = CreateFrame('Frame'); f:SetPoint('LEFT', f, 'RIGHT')", "to itself")
    raises(
        bare,
        "local a, b = CreateFrame('Frame'), CreateFrame('Frame') "
        "a:SetPoint('LEFT', b, 'RIGHT') b:SetPoint('LEFT', a, 'RIGHT')",
        "depends on it",
    )


def test_geometry(bare):
    # A 1920x1080 window is 1365.33 x 768 screen units.
    assert bare.run("return UIParent:GetWidth(), UIParent:GetHeight()") == (pytest.approx(1365.333, abs=0.01), 768)
    result = bare.run(
        "local f = CreateFrame('Frame', nil, UIParent) f:SetSize(200, 100) f:SetPoint('TOPLEFT', 10, -20) "
        "local t = f:CreateTexture() t:SetPoint('TOPLEFT', f, 'BOTTOMLEFT', 5, -5) t:SetPoint('BOTTOMRIGHT', f, 'BOTTOMRIGHT', -5, -25) "
        "local c = CreateFrame('Frame', nil, f) c:SetScale(2) c:SetSize(10, 10) c:SetPoint('CENTER') "
        "return f:GetLeft(), f:GetTop(), f:GetRight(), f:GetBottom(), t:GetWidth(), t:GetHeight(), t:GetTop(), "
        "c:GetEffectiveScale(), c:GetLeft(), f:GetNumPoints(), select(4, f:GetPoint(1)), (f:GetCenter())"
    )
    assert result == (10, 748, 210, 648, 190, 20, 643, 2, pytest.approx(50), 1, 10, 110)


def test_visibility_and_show_hide_scripts(bare):
    bare.run(
        "log = {} "
        "local function watch(frame, name) "
        "  frame:SetScript('OnShow', function() log[#log + 1] = name .. '+' end) "
        "  frame:SetScript('OnHide', function() log[#log + 1] = name .. '-' end) end "
        "parent = CreateFrame('Frame', nil, UIParent) child = CreateFrame('Frame', nil, parent) "
        "sleeper = CreateFrame('Frame', nil, parent) sleeper:Hide() "
        "watch(parent, 'parent') watch(child, 'child') watch(sleeper, 'sleeper')"
    )
    bare.run("parent:Hide()")
    assert bare.run("return child:IsShown(), child:IsVisible()") == (True, False)
    bare.run("parent:Hide() parent:Show() sleeper:Show() child:Show()")
    assert lua_list(bare.global_("log")) == ["parent-", "child-", "parent+", "child+", "sleeper+"]


def test_on_update_runs_for_visible_frames_only_and_timers_fire(bare):
    bare.run(
        "ticks, fired = 0, {} "
        "shown = CreateFrame('Frame') shown:SetScript('OnUpdate', function(_, elapsed) ticks = ticks + elapsed end) "
        "hidden = CreateFrame('Frame') hidden:Hide() hidden:SetScript('OnUpdate', function() error('hidden frame updated') end) "
        "C_Timer.After(0.25, function() fired[#fired + 1] = 'after' end) "
        "ticker = C_Timer.NewTicker(0.1, function() fired[#fired + 1] = 'tick' end, 3) "
        "local cancelled = C_Timer.NewTimer(0.1, function() fired[#fired + 1] = 'cancelled' end) cancelled:Cancel()"
    )
    start = bare.run("return GetTime(), time()")
    bare.advance(1.0, step=0.05)
    assert bare.global_("ticks") == pytest.approx(1.0)
    assert lua_list(bare.global_("fired")) == ["tick", "tick", "after", "tick"]
    assert bare.run("return GetTime(), time()") == (start[0] + 1.0, start[1] + 1)


def test_widget_interaction_helpers(bare):
    bare.run(
        "clicks = {} "
        "button = CreateFrame('CheckButton', nil, UIParent) "
        "button:SetScript('OnClick', function(self, mouse) clicks[#clicks + 1] = mouse .. tostring(self:GetChecked()) end) "
        "box = CreateFrame('EditBox', nil, UIParent) typed = {} "
        "box:SetScript('OnTextChanged', function(self, user) typed[#typed + 1] = self:GetText() .. tostring(user) end) "
        "box:SetScript('OnEnterPressed', function(self) self:ClearFocus() end) "
        "box:SetScript('OnEditFocusLost', function() typed[#typed + 1] = 'lost' end) "
        "slider = CreateFrame('Slider', nil, UIParent) slider:SetMinMaxValues(0, 10) values = {} "
        "slider:SetScript('OnValueChanged', function(_, value, user) values[#values + 1] = value .. tostring(user) end)"
    )
    bare.M.click(bare.global_("button"))
    bare.M.click(bare.global_("button"), "RightButton")
    bare.run("button:Disable()")
    assert bare.M.click(bare.global_("button")) is False
    assert lua_list(bare.global_("clicks")) == ["LeftButtontrue", "RightButtonfalse"]

    bare.M.type_text(bare.global_("box"), "82", True)
    bare.run("box:SetText('90')")
    assert lua_list(bare.global_("typed")) == ["82true", "lost", "90false"]

    bare.M.slide(bare.global_("slider"), 4)
    bare.run("slider:SetValue(4) slider:SetValue(99)")
    assert lua_list(bare.global_("values")) == ["4true", "10false"]


# ---- saved variables ---------------------------------------------------------------------------


def test_saved_variables_arrive_after_the_files_run_and_before_addon_loaded(make_rt):
    probe = (
        "local addonName, ns = ...\n"
        "ns.atFileLoad = WalkaDB\n"
        "local f = CreateFrame('Frame') f:RegisterEvent('ADDON_LOADED')\n"
        "f:SetScript('OnEvent', function(_, _, name) if name == addonName then ns.atAddonLoaded = WalkaDB end end)\n"
    )
    rt = make_rt(files={"CompanionData.lua": probe}, saved_variables={"WalkaDB": "{marker=42}"})
    assert rt.ns.atFileLoad is None, "saved variables are not there while the files run"
    assert rt.ns.atAddonLoaded.marker == 42


def test_serialiser_keeps_plain_data_only(bare):
    bare.run(
        "WalkaDB = { n = 1.5, s = 'a\"b\\n', b = false, list = { 1, 2, 3 }, nested = { deep = { x = 1 } }, "
        "fn = print, frame = CreateFrame('Frame'), [5] = 'five', [true] = 'yes' }"
    )
    restored = bare.M.deserialize(bare.M.serialize("WalkaDB"))
    assert restored.n == 1.5 and restored.s == 'a"b\n' and restored.b is False
    assert lua_list(restored.list) == [1, 2, 3] and restored.nested.deep.x == 1
    assert restored[5] == "five" and restored[True] == "yes"
    assert restored.fn is None
    assert lua_dict(restored.frame) == {}, "a widget is saved as an empty table (its userdata is dropped)"


@pytest.mark.parametrize("value", ["0/0", "math.huge", "-math.huge"])
def test_serialiser_refuses_numbers_the_client_cannot_write(bare, value):
    bare.run(f"WalkaDB = {{ settings = {{ bad = {value} }} }}")
    with pytest.raises(Exception, match="non-finite number .* WalkaDB.settings.bad"):
        bare.M.serialize("WalkaDB")


def test_serialiser_refuses_cycles(bare):
    bare.run("WalkaDB = {} WalkaDB.self = WalkaDB")
    with pytest.raises(Exception, match="cycle"):
        bare.M.serialize("WalkaDB")


def test_missing_files_listed_in_the_toc_are_skipped(tmp_path, make_rt):
    addon = tmp_path / "Walka"
    addon.mkdir()
    (addon / "Walka.toc").write_text(
        "## Interface: 16001\n## Title: Walka\n## SavedVariables: WalkaDB\n# a comment\n\nOne.lua\nUI\\Missing.lua\nTwo.lua\n",
        encoding="utf-8",
    )
    (addon / "One.lua").write_text("local name, ns = ... ns.order = { name }", encoding="utf-8")
    (addon / "Two.lua").write_text("local name, ns = ... ns.order[#ns.order + 1] = 'two'", encoding="utf-8")
    rt = make_rt(addon_dir=addon)
    assert rt.loaded_files == ["One.lua", "Two.lua"]
    assert rt.skipped_files == ["UI\\Missing.lua"]
    assert lua_list(rt.ns.order) == ["Walka", "two"]
    assert rt.run("return C_AddOns.GetAddOnMetadata('Walka', 'Title')") == "Walka"
    assert rt.run("return C_AddOns.GetAddOnMetadata('Walka', 'Nope')") is None
    assert rt.run("return C_AddOns.GetAddOnMetadata('SomeOtherAddon', 'Title')") is None


def test_a_syntax_error_names_the_file(tmp_path, make_rt):
    addon = tmp_path / "Walka"
    addon.mkdir()
    (addon / "Walka.toc").write_text("## Interface: 16001\nBroken.lua\n", encoding="utf-8")
    (addon / "Broken.lua").write_text("local x = = 1\n", encoding="utf-8")
    with pytest.raises(Exception, match="Walka/Broken.lua:1"):
        make_rt(addon_dir=addon)


@pytest.mark.parametrize(
    "code, message",
    [
        ("goto done ::done::", "Walka/Bad.lua"),                # Lua 5.2+
        ("local x = 7 // 2", "Walka/Bad.lua"),                  # Lua 5.3 integer division
        ("local x = 1 & 3", "Walka/Bad.lua"),                   # Lua 5.3 bitwise operators
        ("local t <const> = 1", "Walka/Bad.lua"),               # Lua 5.4
    ],
)
def test_newer_lua_syntax_is_rejected(tmp_path, make_rt, code, message):
    addon = tmp_path / "Walka"
    addon.mkdir()
    (addon / "Walka.toc").write_text("## Interface: 16001\nBad.lua\n", encoding="utf-8")
    (addon / "Bad.lua").write_text(code + "\n", encoding="utf-8")
    with pytest.raises(Exception, match=message):
        make_rt(addon_dir=addon)


def test_the_core_runs_without_the_ui_files(tmp_path, make_rt, addon_dir):
    """The UI may not exist yet (or fail to load): the TOC lists it, the
    loader skips what is missing, and the core must be complete without it."""
    import shutil

    copy = tmp_path / "Walka"
    shutil.copytree(addon_dir, copy, ignore=shutil.ignore_patterns("UI"))
    rt = make_rt(addon_dir=copy)
    assert rt.skipped_files == [name for name in rt.toc.files if name.startswith("UI")]
    assert len(rt.skipped_files) >= 1
    assert rt.ns.HUD is None and rt.ns.History is None and rt.ns.Options is None
    rt.set(speed=7.0)
    for command in ("", "show", "hide", "history", "options", "start", "status", "pause", "resume", "help"):
        rt.slash("/walka " + command)
        rt.tick(5)
    rt.tick(250)
    click = rt.global_(rt.toc.metadata["AddonCompartmentFunc"])
    rt.M.call(click, "Walka", "LeftButton")
    rt.M.call(click, "Walka", "RightButton")
    rt.slash("/walka stop")
    assert len(lua_list(rt.db.sessions)) == 1
    fresh = rt.reload()
    fresh.tick(5)
    assert fresh.undefined_globals() == {} and rt.undefined_globals() == {}


# ---- behaviour the windows depend on --------------------------------------------------------------


def test_date_and_time_have_the_limits_of_the_windows_c_runtime(bare):
    """The game runs on Windows: date() returns nil - not a string - for a
    time before 1970 or after the year 3000, whatever this machine can do."""
    assert bare.run("return date('%Y', 0)") in ("1969", "1970")
    assert bare.run("return date('%Y', 1795000000)") == "2026"
    assert bare.run("return date('%Y', '1795000000')") == "2026"            # numeric strings are numbers
    for when in ("-1", "-1e9", "1e300", "99999999999", "0/0", "1/0", "-1/0"):
        assert bare.run(f"return date('%Y-%m-%d', {when})") is None, when
        assert bare.run(f"return date('*t', {when})") is None, when
    raises(bare, "return 'synced ' .. date('%H:%M', 1e300)", "attempt to concatenate")
    raises(bare, "return date('%Y', 'soon')", "number expected")
    raises(bare, "return date('%Y', {})", "number expected")
    assert bare.run("return type(date())") == "string"
    assert bare.run("return time({ year = 2026, month = 11, day = 17, hour = 12 })") is not None
    assert bare.run("return time({ year = 1900, month = 1, day = 1 })") is None
    assert bare.run("return time({ year = 5000, month = 1, day = 1 })") is None
    raises(bare, "return time({ year = 2026 })", "missing")
    assert bare.run("return time()") == 1795000000
    # Only conversions every C library has: an unknown one is fatal on Windows.
    assert bare.run("return date('%a %A %b %B %d %H:%M:%S %p %Y-%m-%d %y %j %%', 1795000000)") is not None
    assert bare.run("return date('!%H:%M', 0)") == "00:00"
    assert bare.run("return date('!*t', 86400).day") == 2
    for bad in ("%e", "%T", "%F", "%s", "%k", "%-d", "%D", "100%"):
        raises(bare, f"return date('{bad}')", "not an ISO C89 strftime conversion")


def test_callback_registry_owners_as_in_the_client(bare):
    result = bare.run(
        "local slider = CreateFrame('Frame', nil, UIParent, 'MinimalSliderWithSteppersTemplate') "
        "local seen = {} "
        "local event = MinimalSliderWithSteppersMixin.Event.OnValueChanged "
        # With an owner: the callback gets (owner, value).
        "slider:RegisterCallback(event, function(owner, value) seen[#seen + 1] = (owner == slider) and value end, slider) "
        # Registering again for the same owner replaces the first callback.
        "slider:RegisterCallback(event, function(owner, value) seen[#seen + 1] = 'second' .. value end, slider) "
        # Without an owner the client makes one up - and still passes it first.
        "slider:RegisterCallback(event, function(owner, value) seen[#seen + 1] = type(owner) .. tostring(value) end) "
        # Extra arguments come between the owner and the event's.
        "local other = {} "
        "slider:RegisterCallback(event, function(owner, extra, value) seen[#seen + 1] = extra .. value end, other, 'x') "
        "slider:Init(1, 0, 10, 10) slider.Slider:SetValue(4) "
        "table.sort(seen) return table.concat(seen, ' ')"
    )
    assert result == "number4 second4 x4"
    raises(bare, "CreateFrame('Frame', nil, UIParent, 'MinimalSliderWithSteppersTemplate')"
                 ":RegisterCallback('OnValueChanged', print, 5)", "reserved")
    raises(bare, "CreateFrame('Frame', nil, UIParent, 'MinimalSliderWithSteppersTemplate')"
                 ":RegisterCallback('OnValueChnaged', print)", "no \"OnValueChnaged\" callback event")


def test_settings_window_handles_a_canvas_the_way_the_client_does(bare):
    bare.run(
        "log = {} "
        "page = CreateFrame('Frame', 'WalkaTestPage') page:Hide() "
        "page:SetScript('OnShow', function() log[#log + 1] = 'show' end) "
        "page:SetScript('OnHide', function() log[#log + 1] = 'hide' end) "
        "function page:OnRefresh() log[#log + 1] = 'refresh:' .. tostring(self:IsVisible()) end "
        "category = Settings.RegisterCanvasLayoutCategory(page, 'Test') "
    )
    raises(bare, "Settings.OpenToCategory(category:GetID())", "no registered category")
    raises(bare, "Settings.OpenToCategory(category)", "category ID")
    bare.run("Settings.RegisterAddOnCategory(category)")
    page = bare.global_("page")

    def seen():
        entries = lua_list(bare.global_("log"))
        bare.run("log = {}")
        return entries

    # Opening: every canvas is refreshed (the page is not on screen yet),
    # then the page is put into the window - refreshed before it is visible.
    bare.run("Settings.OpenToCategory(category:GetID())")
    assert seen() == ["refresh:false", "refresh:false", "show"]
    info = bare.inspect(page)
    assert (info["width"], info["height"]) == pytest.approx((665, 601)), "the Settings window's page area"
    screen_width, screen_height = bare.M.screen_size()
    assert info["left"] == pytest.approx((screen_width - 920) / 2 + 233)
    assert info["bottom"] == pytest.approx((screen_height - 724) / 2 + 47)
    assert bare.run("return page:GetParent() == SettingsPanel.Container.SettingsCanvas") is True

    bare.run("Settings.OpenToCategory(category:GetID())")       # already showing: nothing happens
    assert seen() == []

    bare.M.settings_close()
    assert seen() == ["hide"] and bare.run("return page:IsShown(), page:IsVisible()") == (True, False)
    # Reopened by hand: the window shows its own first category, which takes
    # the page off (no parent, no anchors, hidden), and refreshes all canvases.
    bare.M.settings_open()
    assert seen() == ["show", "hide", "refresh:false"]
    assert bare.run("return page:GetParent(), page:GetNumPoints(), page:IsShown()") == (None, 0, False)
    bare.M.settings_select(1)
    assert seen() == ["refresh:false", "show"]
    bare.M.settings_select_other()
    assert seen() == ["hide"]

    # In combat an addon may not open the window; the player still can.
    bare.enter_combat()
    raises(bare, "Settings.OpenToCategory(category:GetID())", "combat")
    assert lua_list(bare.log.blocked) == ["Settings.OpenToCategory"]
    bare.M.settings_select(1)
    assert seen() == ["refresh:false", "show"]
    # An error in OnRefresh goes to the error handler, as under the client's xpcall.
    bare.run("function page:OnRefresh() error('refresh failed') end")
    bare.M.settings_close()
    bare.M.settings_open()
    with pytest.raises(Exception, match="refresh failed"):
        bare.check_errors()


def test_frames_clamped_to_the_screen_stay_on_it(bare):
    result = bare.run(
        "local free = CreateFrame('Frame', nil, UIParent) free:SetSize(100, 50) free:SetPoint('LEFT', 5000, 0) "
        "local kept = CreateFrame('Frame', nil, UIParent) kept:SetSize(100, 50) kept:SetClampedToScreen(true) "
        "kept:SetPoint('BOTTOMLEFT', 5000, -3000) "
        "local child = kept:CreateTexture() child:SetAllPoints(kept) "
        "return free:GetLeft(), kept:GetRight(), kept:GetBottom(), child:GetRight()"
    )
    screen_width, _ = bare.M.screen_size()
    assert result == (5000, pytest.approx(screen_width), 0, pytest.approx(screen_width))


def test_a_scroll_child_is_placed_by_its_scroll_frame(bare):
    result = bare.run(
        "local scroll = CreateFrame('ScrollFrame', nil, UIParent) scroll:SetSize(300, 200) scroll:SetPoint('TOPLEFT', 100, -100) "
        "local content = CreateFrame('Frame', nil, scroll) content:SetSize(280, 900) scroll:SetScrollChild(content) "
        "local item = CreateFrame('Frame', nil, content) item:SetSize(50, 20) item:SetPoint('TOPLEFT', 10, -400) "
        "local before = { content:GetLeft(), content:GetTop(), item:GetTop(), scroll:GetVerticalScrollRange() } "
        "scroll:SetVerticalScroll(350) "
        "return before[1], before[2], before[3], before[4], content:GetTop(), item:GetTop(), item:GetLeft()"
    )
    assert result == (100, 668, 268, 700, 1018, 618, 110)


def test_a_disabled_edit_box_takes_no_typing(bare):
    bare.run(
        "box = CreateFrame('EditBox', nil, UIParent) typed = {} "
        "box:SetScript('OnTextChanged', function(self) typed[#typed + 1] = self:GetText() end) box:Disable()"
    )
    assert bare.M.type_text(bare.global_("box"), "82", True) is False
    assert lua_list(bare.global_("typed")) == [] and bare.run("return box:HasFocus()") is False
    bare.run("box:Enable()")
    assert bare.M.type_text(bare.global_("box"), "82", True) is True
    assert lua_list(bare.global_("typed")) == ["82"]
