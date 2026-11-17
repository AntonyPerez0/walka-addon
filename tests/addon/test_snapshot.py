"""The API snapshot (data/forever_api.json.gz) and its generator
(tools/gen_api_snapshot.py)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .harness import REPO_ROOT, SNAPSHOT_PATH


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("gen_api_snapshot", REPO_ROOT / "tools" / "gen_api_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the committed snapshot ---------------------------------------------------------------


def test_snapshot_is_for_this_client(snapshot):
    assert snapshot.meta["client_version"] == "1.60.1.70959"
    assert snapshot.meta["game_type"] == "camelot" and snapshot.meta["family"] == "mainline"
    assert SNAPSHOT_PATH.stat().st_size < 500_000, "keep the snapshot small"


def test_snapshot_has_the_modern_api(snapshot):
    assert "After" in snapshot.namespaces["C_Timer"]
    assert "GetAddOnMetadata" in snapshot.namespaces["C_AddOns"]
    assert "ShouldUnitStatsBeSecret" in snapshot.namespaces["C_Secrets"]
    assert {"GetUnitSpeed", "IsPlayerMoving", "issecretvalue", "UnitAffectingCombat"} <= snapshot.api_globals
    assert {"RegisterCanvasLayoutCategory", "RegisterAddOnCategory", "OpenToCategory"} <= set(snapshot.members["Settings"])
    assert {"CreateFrame", "hooksecurefunc", "geterrorhandler", "date", "time", "strsplit"} <= snapshot.c_globals
    for name in ("print", "tinsert", "wipe", "format", "strtrim", "GetTime", "UIParent", "SlashCmdList", "Mixin"):
        assert snapshot.knows_global(name), name
    assert {"ADDON_LOADED", "PLAYER_LOGIN", "PLAYER_LOGOUT", "DISPLAY_SIZE_CHANGED", "UI_SCALE_CHANGED"} <= snapshot.events
    assert {"BackdropTemplate", "UIPanelButtonTemplate", "UICheckButtonTemplate", "InputBoxTemplate"} <= set(snapshot.templates)
    assert {"GameFontNormal", "GameFontHighlightSmall", "NumberFontNormal"} <= snapshot.fonts
    assert {"GameTooltip", "SettingsPanel", "AddonCompartmentFrame"} <= set(snapshot.frames)
    assert {"NORMAL_FONT_COLOR", "HIGHLIGHT_FONT_COLOR"} <= snapshot.referenced


def test_snapshot_does_not_have_what_this_client_lacks(snapshot):
    # Classic-era and pre-Dragonflight names that addons still reach for.
    for name in (
        "InterfaceOptions_AddCategory", "InterfaceOptionsFrame_OpenToCategory", "GetAddOnMetadata", "IsAddOnLoaded",
        "LoadAddOn", "UnitBuff", "GetSpellInfo", "InterfaceOptionsFrame", "LibStub", "os", "io", "require",
    ):
        assert not snapshot.knows_global(name), name
    for template in ("OptionsBoxTemplate", "AccountLoginBackdropTemplate"):
        assert template not in snapshot.templates


def test_secret_tags(snapshot):
    assert snapshot.secret["GetUnitSpeed"] == ["SecretWhenUnitStatsRestricted"]
    assert snapshot.secret["UnitIsAFK"] == ["SecretInChatMessagingLockdown"]
    assert snapshot.secret["UnitName"] == ["SecretWhenUnitNameIdentityRestricted"]
    # The public state the addon falls back on is never secret.
    for name in ("IsPlayerMoving", "IsMounted", "IsSwimming", "IsFalling", "IsFlying", "UnitOnTaxi", "UnitIsDeadOrGhost",
                 "UnitAffectingCombat", "IsResting", "GetPhysicalScreenSize", "GetRealmName", "GetLocale"):
        assert name in snapshot.api_globals and name not in snapshot.secret, name
    assert "C_SettingsUtil.OpenSettingsPanel" in snapshot.restricted


def test_widget_methods_follow_the_widget_hierarchy(snapshot):
    methods = snapshot.type_methods
    assert {"SetPoint", "SetSize", "Show", "SetScript", "GetName", "GetParent"} <= methods("Frame") & methods("Texture") & methods("FontString")
    assert {"RegisterEvent", "CreateTexture", "SetFrameStrata", "SetMovable"} <= methods("Frame")
    assert not {"RegisterEvent", "CreateTexture", "SetFrameStrata"} & methods("Texture")
    assert {"SetColorTexture", "SetVertexColor", "SetSnapToPixelGrid", "SetTexelSnappingBias"} <= methods("Texture")
    assert not {"SetColorTexture", "SetVertexColor", "SetText"} & methods("Frame")
    assert {"SetText", "SetFontObject", "GetStringWidth"} <= methods("FontString")
    assert {"SetText", "Click", "RegisterEvent"} <= methods("Button") and "SetChecked" not in methods("Button")
    assert {"SetChecked", "SetText"} <= methods("CheckButton")
    assert {"SetMinMaxValues", "SetValue"} <= methods("Slider") & methods("StatusBar")
    assert {"SetOwner", "AddLine", "ClearLines", "SetText"} <= methods("GameTooltip")
    assert snapshot.type_chain("CheckButton") == ["CheckButton", "Button", "Frame", "ScriptRegion", "Object", "FrameScriptObject"]
    # Removed long ago; still common in old addon code.
    assert not {"SetMinResize", "SetMaxResize", "SetBackdrop"} & methods("Frame")


def test_script_handlers_per_widget_type(snapshot):
    scripts = snapshot.type_scripts
    assert {"OnUpdate", "OnEvent", "OnShow", "OnDragStart", "OnMouseWheel"} <= scripts("Frame")
    assert "OnClick" not in scripts("Frame") and "OnClick" in scripts("Button") and "OnClick" in scripts("CheckButton")
    assert "OnValueChanged" in scripts("Slider") and "OnValueChanged" not in scripts("Button")
    assert "OnTextChanged" in scripts("EditBox") and "OnEnterPressed" in scripts("EditBox")
    assert "OnUpdate" not in scripts("Texture") and "OnEnter" in scripts("Texture")
    assert "OnFinished" in scripts("AnimationGroup")


def test_templates_keep_their_shape(snapshot):
    assert snapshot.templates["BackdropTemplate"]["m"] == ["BackdropTemplateMixin"]
    assert {"SetBackdrop", "SetBackdropColor", "SetBackdropBorderColor"} <= set(snapshot.mixin_members("BackdropTemplateMixin"))
    slider = snapshot.templates["MinimalSliderWithSteppersTemplate"]
    assert {child.get("k") for child in slider["c"]} >= {"Slider", "Back", "Forward", "RightText"}
    members = snapshot.mixin_members("MinimalSliderWithSteppersMixin")
    assert members["Init"] == "MinimalSliderWithSteppersMixin"
    assert members["RegisterCallback"] == "CallbackRegistryMixin", "methods inherited through CreateFromMixins"
    assert snapshot.native_type("EventFrame") == "Frame" and snapshot.native_type("DropdownButton") == "Button"
    assert snapshot.native_type("NoSuchWidget") is None


def test_load_on_demand_names_are_kept_apart(snapshot):
    assert snapshot.lod, "some Blizzard addons load on demand"
    name, addon = next(iter(snapshot.lod.items()))
    assert not snapshot.knows_global(name)
    assert snapshot.global_kind(name) == "lod:" + addon


# ---- the generator ------------------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fake_source(tmp_path, generator) -> Path:
    """A tiny stand-in for Blizzard's Interface/AddOns folder."""
    root = tmp_path / "Interface" / "AddOns"
    docs = root / "Blizzard_APIDocumentationGenerated"
    tables = sorted({table for _parent, apis in generator.WIDGET_TYPES.values() for table in apis})
    files = []
    for table in tables:
        extra = ""
        if table == "SimpleFrameAPI":
            extra = '{ Name = "RegisterEvent", Type = "Function" },'
        write(
            docs / f"{table}Documentation.lua",
            f'local T = {{ Name = "{table}", Type = "ScriptObject", Environment = "All",\n'
            f'  Functions = {{ {{ Name = "Method_{table}", Type = "Function", SecretReturnsForAspect = {{ Enum.SecretAspect.Hierarchy }} }}, {extra} }},\n'
            "  Events = {}, Tables = {} };\nAPIDocumentation:AddDocumentationTable(T);\n",
        )
        files.append(f"{table}Documentation.lua")
    write(
        docs / "TimerDocumentation.lua",
        'local T = { Name = "Timer", Type = "System", Namespace = "C_Test",\n'
        "  Functions = {\n"
        '    { Name = "After", Type = "Function" },\n'
        '    { Name = "Hidden", Type = "Function", SecretWhenUnitStatsRestricted = true, HasRestrictions = true },\n'
        "  },\n"
        '  Events = { { Name = "TestEvent", Type = "Event", LiteralName = "TEST_EVENT" } },\n'
        '  Tables = { { Name = "TestEnum", Type = "Enumeration", Fields = { { Name = "A", EnumValue = Constants.X.Y + 1 } } } } };\n'
        "APIDocumentation:AddDocumentationTable(T);\n",
    )
    write(
        docs / "GlobalDocumentation.lua",
        'local T = { Name = "Unit", Type = "System",\n'
        '  Functions = { { Name = "GetSpeed", Type = "Function", SecretInChatMessagingLockdown = true },\n'
        '                { Name = "PlainGlobal", Type = "Function" },\n'
        '                { Name = "ProtectedThing", Type = "Function", IsProtectedFunction = true } },\n'
        "  Events = {}, Tables = {} };\nAPIDocumentation:AddDocumentationTable(T);\n",
    )
    write(
        docs / "SecureDocumentation.lua",
        'local T = { Name = "Secure", Type = "System", Environment = "SecureOnly",\n'
        '  Functions = { { Name = "SecureOnlyFunction", Type = "Function" } }, Events = {}, Tables = {} };\n'
        "APIDocumentation:AddDocumentationTable(T);\n",
    )
    files += ["TimerDocumentation.lua", "GlobalDocumentation.lua", "SecureDocumentation.lua"]
    write(docs / "Blizzard_APIDocumentationGenerated.toc", "## Title: docs\n## LoadOnDemand: 1\n" + "\n".join(files) + "\n")

    game = root / "Blizzard_Game"
    write(
        game / "Blizzard_Game.toc",
        "## Title: Game\n## AllowLoad: Both\n# comment\n"
        "Shared.lua\n"
        "[Family]\\Family.lua\n"
        "[Game]\\Game.lua [AllowLoadGameType camelot]\n"
        "Classic\\Old.lua [AllowLoadGameType classic]\n"
        "Retail.lua [AllowLoadGameType standard]\n"
        "Mainline.lua [AllowLoadGameType mainline, mists]\n"
        "NotHere.lua [ExcludeLoadGameType camelot]\n"
        "Glue.lua [AllowLoad glue]\n"
        "Russian.lua [AllowLoadTextLocale ruRU]\n"
        "SecureEnv.lua [LoadIntoEnvironment secure]\n"
        "frames.XML\n",
    )
    write(
        game / "Shared.lua",
        "local hidden = 1\n"
        "local hiddenIsLocal\n"
        "function SharedFunction(a)\n\tlocal inner = 2\n\tInnerGlobal = 3\nend\n\n"
        "function hiddenIsLocal() end\n"
        "local function reallyLocal() end\n"
        "SharedTable = {\n\tALPHA = 1,\n\tBETA = 2,\n};\n"
        "SharedUtil = {};\n"
        "function SharedUtil.DoThing() end\n"
        "function SharedUtil:Method() end\n"
        "SharedUtil.Constant = 5\n"
        "BaseMixin = {};\nfunction BaseMixin:BaseMethod() end\n"
        "ChildMixin = CreateFromMixins(BaseMixin);\nfunction ChildMixin:ChildMethod() end\n"
        "UnusedMixin = {};\nfunction UnusedMixin:Nothing() end\n"
        "--[[\nfunction CommentedOut() end\n]]\n"
        "local text = [[\nfunction InAString() end\n]]\n"
        "if hidden == 1 then\n\tfunction InsideABlock() end\nend\n"
        "local color = SOME_GLOBAL_STRING .. NORMAL_FONT_COLOR:GenerateHexColor()\n"
        "local frame = CreateFrame('Frame'); hooksecurefunc('x', print); GameTooltip:SetOwner(frame); GameTooltip:AddLine('x')\n"
        "local icon = C_AddOns.GetAddOnMetadata(index, \"IconTexture\")\n",
    )
    write(game / "Mainline" / "Family.lua", "function FamilyFunction() end\n")
    write(game / "Camelot" / "Game.lua", "function CamelotFunction() end\n")
    write(game / "Classic" / "Old.lua", "function ClassicFunction() end\n")
    for name in ("Retail", "NotHere", "Glue", "Russian", "SecureEnv"):
        write(game / f"{name}.lua", f"function {name}Function() end\n")
    write(game / "Mainline.lua", "function MainlineFunction() end\n")
    write(game / "Extra.lua", "function FromXmlScript() end\n")
    write(
        game / "Frames.xml",
        '<Ui xmlns="http://www.blizzard.com/wow/ui/">\n'
        '  <Script file="Extra.lua"/>\n'
        '  <Font name="TestFont" font="x.ttf"/>\n'
        '  <Frame name="BaseTemplate" mixin="BaseMixin" virtual="true"/>\n'
        '  <Button name="TestButtonTemplate" inherits="BaseTemplate" mixin="ChildMixin" virtual="true">\n'
        '    <Layers><Layer level="ARTWORK">\n'
        '      <FontString parentKey="Text" name="$parentText" inherits="TestFont" hidden="true"/>\n'
        '      <Texture/>\n'
        "    </Layer></Layers>\n"
        '    <Frames><Frame parentKey="Inner"><Layers><Layer><Texture parentKey="Deep" parentArray="Parts"/></Layer></Layers></Frame></Frames>\n'
        '    <NormalTexture parentKey="Normal"/>\n'
        "    <Scripts><OnClick/><OnLoad/></Scripts>\n"
        "  </Button>\n"
        '  <Frame name="EventFrame" intrinsic="true"/>\n'
        '  <ScopedModifier forbidden="true">\n'
        '    <Frame name="TestParent" inherits="BaseTemplate">\n'
        '      <Frames><Button name="$parentClose" inherits="TestButtonTemplate"/></Frames>\n'
        "    </Frame>\n"
        "  </ScopedModifier>\n"
        "</Ui>\n",
    )

    write(root / "Blizzard_Lod" / "Blizzard_Lod.toc", "## LoadOnDemand: 1\nLod.lua\nBoot.lua [Bootstrap]\nLod.xml\n")
    write(root / "Blizzard_Lod" / "Lod.lua", "function LodFunction() end\nfunction SharedFunction() end\n")
    write(root / "Blizzard_Lod" / "Boot.lua", "function BootstrapFunction() end\n")
    write(root / "Blizzard_Lod" / "Lod.xml", '<Ui><Frame name="LodTemplate" virtual="true"/><Frame name="LodFrame"/></Ui>')
    write(root / "Blizzard_ClassicOnly" / "Blizzard_ClassicOnly.toc", "## AllowLoadGameType: classic, vanilla\nC.lua\n")
    write(root / "Blizzard_ClassicOnly" / "C.lua", "function ClassicAddonFunction() end\n")
    write(root / "Blizzard_NotCamelot" / "Blizzard_NotCamelot.toc", "## ExcludeLoadGameType: camelot\nN.lua\n")
    write(root / "Blizzard_NotCamelot" / "N.lua", "function NotCamelotAddonFunction() end\n")
    write(root / "Blizzard_Secure" / "Blizzard_Secure.toc", "## UseSecureEnvironment: 1\nS.lua\n")
    write(root / "Blizzard_Secure" / "S.lua", "function SecureAddonFunction() end\n")
    write(root / "Blizzard_GlueOnly" / "Blizzard_GlueOnly.toc", "## AllowLoad: Glue\nG.lua\n")
    write(root / "Blizzard_GlueOnly" / "G.lua", "function GlueAddonFunction() end\n")
    write(root / "Blizzard_Game" / "Blizzard_Game_Vanilla.toc", "VanillaToc.lua\n")
    write(root / "Blizzard_Game" / "VanillaToc.lua", "function VanillaTocFunction() end\n")
    write(tmp_path / "version.txt", "9.9.9.1\n")
    return root


def test_generator_on_a_small_source_tree(generator, fake_source):
    snapshot, report = generator.build_snapshot(fake_source)
    lua = snapshot["lua"]

    # Documentation: executed, not regex-parsed.
    assert snapshot["api"]["namespaces"] == {"C_Test": ["After", "Hidden"]}
    assert snapshot["api"]["globals"] == ["GetSpeed", "PlainGlobal", "ProtectedThing"]
    assert snapshot["api"]["secret"] == {
        "C_Test.Hidden": ["SecretWhenUnitStatsRestricted"],
        "GetSpeed": ["SecretInChatMessagingLockdown"],
    }
    assert snapshot["api"]["restricted"] == ["C_Test.Hidden"] and snapshot["api"]["protected"] == ["ProtectedThing"]
    assert snapshot["events"] == ["TEST_EVENT"]
    assert snapshot["toc_fields"] == ["IconTexture"]
    assert snapshot["enums"] == ["TestEnum"]
    assert "SecureOnlyFunction" not in snapshot["api"]["globals"]
    assert snapshot["widgets"]["apis"]["SimpleFrameAPI"] == ["Method_SimpleFrameAPI", "RegisterEvent"]
    assert snapshot["widgets"]["types"]["Button"] == {"parent": "Frame", "apis": ["SimpleButtonAPI"]}
    assert "OnClick" in snapshot["widgets"]["scripts"]["Button"] and "OnClick" not in snapshot["widgets"]["scripts"]["Frame"]
    assert snapshot["widgets"]["tooltip_methods"] == ["AddLine", "SetOwner"]
    assert snapshot["meta"]["client_version"] == "9.9.9.1"

    # TOC conditions: only what this game type loads at login.
    functions = set(lua["functions"])
    assert {"SharedFunction", "FamilyFunction", "CamelotFunction", "MainlineFunction", "FromXmlScript",
            "InsideABlock", "BootstrapFunction"} <= functions
    for absent in ("ClassicFunction", "RetailFunction", "NotHereFunction", "GlueFunction", "RussianFunction",
                   "SecureEnvFunction", "ClassicAddonFunction", "NotCamelotAddonFunction", "SecureAddonFunction",
                   "GlueAddonFunction", "VanillaTocFunction", "CommentedOut", "InAString", "hiddenIsLocal",
                   "reallyLocal", "LodFunction"):
        assert absent not in functions, absent
    assert {"SharedTable", "SharedUtil", "BaseMixin", "ChildMixin", "UnusedMixin"} <= set(lua["values"])
    assert "hidden" not in lua["values"] and "InnerGlobal" not in lua["values"]

    # Load-on-demand names are kept apart, unless something always loaded defines them too.
    assert snapshot["lod"] == {"LodFunction": "Blizzard_Lod", "LodFrame": "Blizzard_Lod", "LodTemplate": "Blizzard_Lod"}

    # Members, mixins and their parents.
    members = snapshot["members"]
    assert members["SharedTable"] == ["ALPHA", "BETA"]
    assert members["SharedUtil"] == ["Constant", "DoThing", "Method"]
    assert members["BaseMixin"] == ["BaseMethod"] and members["ChildMixin"] == ["ChildMethod"]
    assert "UnusedMixin" not in members, "a mixin no XML applies is left out"
    assert snapshot["mixin_parents"] == {"ChildMixin": ["BaseMixin"]}

    # XML: fonts, templates with their shape, intrinsics, frames with $parent names.
    assert snapshot["fonts"] == ["TestFont"]
    template = snapshot["templates"]["TestButtonTemplate"]
    assert template["t"] == "Button" and template["i"] == ["BaseTemplate"] and template["m"] == ["ChildMixin"]
    children = {child.get("k"): child for child in template["c"]}
    assert children["Text"] == {"t": "FontString", "i": ["TestFont"], "k": "Text", "n": "$parentText", "h": 1}
    assert children["Normal"]["t"] == "Texture"
    assert children["Inner"]["c"] == [{"t": "Texture", "k": "Deep", "a": "Parts"}]
    assert len(template["c"]) == 3, "children nobody can reach are dropped"
    assert snapshot["templates"]["EventFrame"] == {"t": "Frame", "x": 1}
    assert snapshot["frames"] == {
        "TestParent": {"t": "Frame", "i": ["BaseTemplate"]},
        "TestParentClose": {"t": "Button", "i": ["TestButtonTemplate"]},
    }

    # Names Blizzard uses but does not define in Lua (global strings, colours).
    assert snapshot["referenced"] == ["NORMAL_FONT_COLOR", "SOME_GLOBAL_STRING"]
    # C globals: only those Blizzard's code can be seen to use.
    assert snapshot["c_globals"] == ["Constants", "CreateFrame", "CreateFromMixins", "Enum", "hooksecurefunc", "print"]
    assert "geterrorhandler" in report["c_globals_not_seen_in_source"]
    assert report["missing_files"] == [] and report["xml_parse_errors"] == []
    assert report["files"]["tocs_other_flavour"] == 1


def test_generator_output_is_deterministic(generator, fake_source, tmp_path):
    first, second = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    generator.write_snapshot(generator.build_snapshot(fake_source)[0], first)
    generator.write_snapshot(generator.build_snapshot(fake_source)[0], second)
    assert first.read_bytes() == second.read_bytes()
    assert generator.main([str(fake_source), "-o", str(tmp_path / "c.json.gz")]) == 0
    assert (tmp_path / "c.json.gz").read_bytes() == first.read_bytes()


def test_generator_reports_missing_files(generator, fake_source):
    toc = fake_source / "Blizzard_Game" / "Blizzard_Game.toc"
    toc.write_text(toc.read_text(encoding="utf-8") + "DoesNotExist.lua\n", encoding="utf-8")
    _, report = generator.build_snapshot(fake_source)
    assert report["missing_files"] == ["Blizzard_Game: DoesNotExist.lua"]


def test_generator_rejects_a_folder_that_is_not_the_ui_source(generator, tmp_path, capsys):
    with pytest.raises(SystemExit):
        generator.main([str(tmp_path)])
    assert "does not look like Interface/AddOns" in capsys.readouterr().err


def test_committed_snapshot_matches_the_ui_source(generator, tmp_path):
    """Regenerate from Blizzard's UI source when it is available
    (WALKA_UI_SOURCE=/path/to/Interface/AddOns) and compare byte for byte."""
    source = os.environ.get("WALKA_UI_SOURCE")
    if not source:
        pytest.skip("set WALKA_UI_SOURCE to Blizzard's Interface/AddOns folder to check the committed snapshot")
    output = tmp_path / "regenerated.json.gz"
    snapshot, report = generator.build_snapshot(Path(source))
    generator.write_snapshot(snapshot, output)
    assert report["missing_files"] == [] and report["xml_parse_errors"] == []
    assert output.read_bytes() == SNAPSHOT_PATH.read_bytes(), "run tools/gen_api_snapshot.py and commit the result"
