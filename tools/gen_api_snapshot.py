"""Build tests/addon/data/forever_api.json.gz from Blizzard's UI source.

The addon tests cannot run inside the game, so they check every global,
widget method, template, font object and event the addon touches against a
snapshot of what this exact client really has. This script makes that
snapshot from a checkout of Blizzard's interface code (the ``Interface/AddOns``
folder exported from the game client, or a mirror of it):

    python tools/gen_api_snapshot.py /path/to/Interface/AddOns

Nothing from Blizzard's code is copied except *names* (and, for XML
templates, their shape: which keyed children and mixins they have).

What is read, and how
---------------------
* ``Blizzard_APIDocumentationGenerated/*.lua`` are Lua tables registered with
  ``APIDocumentation:AddDocumentationTable``. They are executed under lupa
  (Lua 5.1) with a stub collector - not regex-parsed - which yields the
  documented global functions, namespaced functions (``C_Timer.After``), widget
  ("ScriptObject") methods, events and enumerations, including the tags that
  say a function can return secret values.
* Every Blizzard addon's ``.toc`` is followed the way the client does for the
  chosen game type, so files that only load in other flavours (Classic,
  retail-only) or on the login screen are left out. Lua files are scanned for
  top-level definitions; XML files are parsed for named frames, templates,
  fonts, intrinsics and ``<Scripts>`` handlers.
* A short hand-maintained list of C-implemented globals that the generated
  documentation does not cover (``CreateFrame``, ``hooksecurefunc``, ...) is
  added - but only names that Blizzard's own code can be seen to use.

The output is deterministic: everything is sorted and the gzip header has no
timestamp, so regenerating from the same source gives the same bytes.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

SNAPSHOT_FORMAT = 1

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "tests" / "addon" / "data" / "forever_api.json.gz"

# --------------------------------------------------------------------------
# Game type
# --------------------------------------------------------------------------
# WoW: Forever is the "camelot" game type of the "mainline" client family.
# TOC lines use [Family]/[Game] as folder placeholders and
# [AllowLoadGameType ...] / [ExcludeLoadGameType ...] as conditions; a
# condition naming the family applies to every game type in it.

GAME_TYPES = {
    "camelot": {"family": "mainline", "family_dir": "Mainline", "game_dir": "Camelot"},
}

# --------------------------------------------------------------------------
# Widget types
# --------------------------------------------------------------------------
# The documentation describes widget methods as "ScriptObject" tables
# (SimpleFrameAPI, SimpleButtonAPI, ...) but does not say which widget type
# has which tables. This is that mapping: each widget type lists its parent
# type and the documentation tables it adds. It follows the client's widget
# hierarchy:
#
#   FrameScriptObject > Object > ScriptRegion > Region  > TextureBase > Texture ...
#                                             |         > FontString
#                                             > Frame   > Button > CheckButton
#                                                       > Slider, EditBox, ...
#
# gen() fails if a table named here is missing from the documentation, and
# reports documentation tables that are not mapped to anything.

WIDGET_TYPES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    # abstract bases
    "FrameScriptObject": (None, ("SimpleFrameScriptObjectAPI",)),
    "Object": ("FrameScriptObject", ("SimpleObjectAPI",)),
    "ScriptRegion": ("Object", ("SimpleScriptRegionAPI", "SimpleScriptRegionResizingAPI")),
    "Region": ("ScriptRegion", ("SimpleRegionAPI", "SimpleAnimatableObjectAPI")),
    "TextureBase": ("Region", ("SimpleTextureBaseAPI",)),
    # regions
    "Texture": ("TextureBase", ("SimpleTextureAPI",)),
    "MaskTexture": ("TextureBase", ("SimpleMaskTextureAPI",)),
    "Line": ("TextureBase", ("SimpleLineAPI",)),
    "FontString": ("Region", ("SimpleFontStringAPI",)),
    # frames
    "Frame": ("ScriptRegion", ("SimpleFrameAPI", "SimpleAnimatableObjectAPI")),
    "Button": ("Frame", ("SimpleButtonAPI",)),
    "CheckButton": ("Button", ("SimpleCheckboxAPI",)),
    "EditBox": ("Frame", ("SimpleEditBoxAPI",)),
    "Slider": ("Frame", ("SimpleSliderAPI",)),
    "StatusBar": ("Frame", ("SimpleStatusBarAPI",)),
    "ScrollFrame": ("Frame", ("SimpleScrollFrameAPI",)),
    "MessageFrame": ("Frame", ("SimpleMessageFrameAPI",)),
    "SimpleHTML": ("Frame", ("SimpleHTMLAPI",)),
    "ColorSelect": ("Frame", ("SimpleColorSelectAPI",)),
    "Cooldown": ("Frame", ("FrameAPICooldown",)),
    "GameTooltip": ("Frame", ("FrameAPITooltip",)),
    "Model": ("Frame", ("SimpleModelAPI",)),
    "PlayerModel": ("Model", ("FrameAPICharacterModelBase",)),
    "CinematicModel": ("PlayerModel", ("FrameAPICinematicModel",)),
    "DressUpModel": ("PlayerModel", ("FrameAPIDressUpModel",)),
    "TabardModel": ("PlayerModel", ("FrameAPITabardModelBase", "FrameAPITabardModel")),
    "ModelScene": ("Frame", ("FrameAPIModelSceneFrame",)),
    "Minimap": ("Frame", ("MinimapFrameAPI",)),
    "MovieFrame": ("Frame", ("SimpleMovieAPI",)),
    "Browser": ("Frame", ("SimpleBrowserAPI",)),
    "OffScreenFrame": ("Frame", ("SimpleOffScreenFrameAPI",)),
    "Checkout": ("Frame", ("FrameAPISimpleCheckout",)),
    "FogOfWarFrame": ("Frame", ("FrameAPIFogOfWarFrame",)),
    "QuestPOIFrame": ("Frame", ("FrameAPIBlob", "FrameAPIQuestPOI")),
    "ScenarioPOIFrame": ("Frame", ("FrameAPIBlob", "FrameAPIScenarioPOI")),
    "ArchaeologyDigSiteFrame": ("Frame", ("FrameAPIBlob", "FrameAPIArchaeologyDigSiteFrame")),
    "UnitPositionFrame": ("Frame", ("FrameAPIUnitPositionFrame",)),
    # fonts and animations
    "Font": ("FrameScriptObject", ("SimpleFontAPI",)),
    "AnimationGroup": ("Object", ("SimpleAnimGroupAPI",)),
    "Animation": ("Object", ("SimpleAnimAPI",)),
    "Alpha": ("Animation", ("SimpleAnimAlphaAPI",)),
    "Translation": ("Animation", ("SimpleAnimTranslationAPI",)),
    "Scale": ("Animation", ("SimpleAnimScaleAPI",)),
    "Rotation": ("Animation", ("SimpleAnimRotationAPI",)),
    "Path": ("Animation", ("SimpleAnimPathAPI",)),
    "FlipBook": ("Animation", ("SimpleAnimFlipBookAPI",)),
    "VertexColor": ("Animation", ("SimpleAnimVertexColorAPI",)),
    "TextureCoordTranslation": ("Animation", ("SimpleAnimTextureCoordTranslationAPI",)),
    "LineScale": ("Scale", ("SimpleAnimScaleLineAPI",)),
    "LineTranslation": ("Translation", ("SimpleAnimTranslationLineAPI",)),
    "RadialProgress": ("Animation", ("SimpleAnimRadialProgressAPI",)),
    "ControlPoint": ("Object", ("SimpleControlPointAPI",)),
}

# Widget types that CreateFrame() accepts (the non-abstract frames above).
FRAME_BASE = "Frame"

# Script handlers each widget type adds to its parent's. The XML schema
# (UI.xsd) lists handler names without saying which widget takes which, and
# the client rejects SetScript() with a handler the widget does not have
# (a plain Frame has no OnClick), so the mapping is kept here. gen() checks
# every name against the schema and reports handlers that Blizzard's own
# <Scripts> blocks use on a widget type but that are missing from this table.
WIDGET_SCRIPTS: dict[str, tuple[str, ...]] = {
    "ScriptRegion": ("OnLoad", "OnShow", "OnHide", "OnEnter", "OnLeave", "OnMouseDown", "OnMouseUp"),
    "Frame": (
        "OnUpdate", "OnEvent", "OnSizeChanged", "OnMouseWheel", "OnDragStart", "OnDragStop",
        "OnReceiveDrag", "OnKeyDown", "OnKeyUp", "OnChar", "OnAttributeChanged", "OnEnable",
        "OnDisable", "OnHyperlinkClick", "OnHyperlinkEnter", "OnHyperlinkLeave",
        "OnGamePadButtonDown", "OnGamePadButtonUp", "OnGamePadStick",
    ),
    "Button": ("OnClick", "OnDoubleClick", "PreClick", "PostClick"),
    "Slider": ("OnValueChanged", "OnMinMaxChanged"),
    "StatusBar": ("OnValueChanged", "OnMinMaxChanged"),
    "EditBox": (
        "OnArrowPressed", "OnCharComposition", "OnCursorChanged", "OnEditFocusGained",
        "OnEditFocusLost", "OnEnterPressed", "OnEscapePressed", "OnInputLanguageChanged",
        "OnSpacePressed", "OnTabPressed", "OnTextChanged", "OnTextSet",
    ),
    "ScrollFrame": ("OnHorizontalScroll", "OnVerticalScroll", "OnScrollRangeChanged"),
    "ColorSelect": ("OnColorSelect",),
    "Cooldown": ("OnCooldownDone",),
    "GameTooltip": (
        "OnTooltipSetDefaultAnchor", "OnTooltipCleared", "OnTooltipAddMoney", "OnTooltipSetUnit",
        "OnTooltipSetItem", "OnTooltipSetSpell", "OnTooltipSetQuest", "OnTooltipSetAchievement",
        "OnTooltipSetFramestack", "OnTooltipSetEquipmentSet",
    ),
    "Model": ("OnUpdateModel", "OnModelCleared", "OnModelLoaded", "OnAnimStarted", "OnAnimFinished"),
    "DressUpModel": ("OnDressModel",),
    "ModelScene": ("OnDressModel",),
    "MovieFrame": ("OnMovieFinished", "OnMovieShowSubtitle", "OnMovieHideSubtitle"),
    "Browser": ("OnButtonUpdate", "OnError", "OnExternalLink", "OnEscapePressed"),
    "FogOfWarFrame": ("OnUiMapChanged",),
    "AnimationGroup": ("OnLoad", "OnPlay", "OnPause", "OnStop", "OnFinished", "OnUpdate", "OnLoop"),
    "Animation": ("OnLoad", "OnPlay", "OnPause", "OnStop", "OnFinished", "OnUpdate"),
}

# XML elements that create a region/frame held by their parent under a
# different widget type than the tag name.
SPECIAL_CHILD_TYPES = {
    "NormalTexture": "Texture",
    "PushedTexture": "Texture",
    "DisabledTexture": "Texture",
    "HighlightTexture": "Texture",
    "CheckedTexture": "Texture",
    "DisabledCheckedTexture": "Texture",
    "ThumbTexture": "Texture",
    "BarTexture": "Texture",
    "SwipeTexture": "Texture",
    "EdgeTexture": "Texture",
    "BlingTexture": "Texture",
    "ColorWheelTexture": "Texture",
    "ColorWheelThumbTexture": "Texture",
    "ColorValueTexture": "Texture",
    "ColorValueThumbTexture": "Texture",
    "ColorAlphaTexture": "Texture",
    "ColorAlphaThumbTexture": "Texture",
    "ButtonText": "FontString",
    "FontStringHeader": "FontString",
}
REGION_TAGS = {"Texture", "FontString", "Line", "MaskTexture"}
ANIMATION_TAGS = {
    "Animation", "Alpha", "Translation", "Scale", "Rotation", "Path", "FlipBook", "VertexColor",
    "TextureCoordTranslation", "LineScale", "LineTranslation", "RadialProgress",
}
# XML elements that are never widgets.
NON_WIDGET_TAGS = {
    "Ui", "Script", "Include", "Size", "Anchors", "Anchor", "Scripts", "KeyValues", "KeyValue",
    "Layers", "Layer", "Frames", "Animations", "Color", "TexCoords", "Offset", "AbsDimension",
    "RelDimension", "Shadow", "Gradient", "MinColor", "MaxColor", "HitRectInsets", "AbsInset",
    "RelInset", "TextInsets", "PushedTextOffset", "NormalFont", "HighlightFont", "DisabledFont",
    "NormalColor", "HighlightColor", "DisabledColor", "BarColor", "Attributes", "Attribute",
    "Mixins", "Mixin", "ForbiddenAspects", "ForbiddenAspect", "ResizeBounds", "minResize",
    "maxResize", "Origin", "ControlPoints", "MaskedTextures", "MaskedTexture", "Rect", "ViewInsets",
    "FogColor", "FontHeight", "AbsValue", "RelValue", "Bindings", "Binding", "ModifiedClick",
    "Member", "ScopedModifier", "ScrollChild", "RadialProgressBar", "KeyValye",
}

# Blizzard addons whose Lua mixins are kept even when no XML applies them.
FOUNDATION_ADDONS = {
    "Blizzard_SharedXMLBase", "Blizzard_SharedXML", "Blizzard_SharedXMLGame", "Blizzard_Colors",
    "Blizzard_Settings_Shared", "Blizzard_Menu", "Blizzard_FrameXMLBase", "Blizzard_FrameXMLUtil",
}

# --------------------------------------------------------------------------
# C-implemented globals missing from the generated documentation
# --------------------------------------------------------------------------
# The generated docs cover the game API but not the Lua standard library,
# Blizzard's aliases for it, or a handful of old C functions. Each name below
# is only written to the snapshot if Blizzard's own Lua code uses it, so a
# name that this client dropped would be reported instead of silently kept.

C_GLOBAL_CANDIDATES = (
    # Lua 5.1 base library (as shipped in the client: no os/io/debug.* for addons)
    "assert collectgarbage error getfenv getmetatable ipairs loadstring next pairs pcall print "
    "rawequal rawget rawset select setfenv setmetatable tonumber tostring type unpack xpcall "
    "newproxy gcinfo "
    # library tables
    "string table math coroutine bit "
    # Blizzard's global aliases of library functions
    "abs ceil cos deg exp floor frexp ldexp log log10 max min mod rad random sin sqrt tan "
    "format gmatch gsub strbyte strchar strfind strlen strlower strmatch strrep strrev strsub "
    "strupper foreach foreachi getn sort tinsert tremove wipe tContains date time difftime "
    "debugstack debuglocals debugprofilestart debugprofilestop "
    # WoW additions implemented in C and not (or not fully) documented
    "CreateFrame CreateFont hooksecurefunc geterrorhandler seterrorhandler issecure issecurevariable "
    "securecall securecallfunction forceinsecure scrub strsplit strjoin strtrim strconcat "
    "strsplittable tostringall GetTime GetFramerate "
    "issecretvalue issecrettable canaccessvalue canaccesstable hasanysecretvalues "
    "scrubsecretvalues secretwrap secretunwrap "
    "GetLocale GetBuildInfo GetRealmName UnitName "
    "Mixin CreateFromMixins CreateAndInitFromMixin CreateColor "
    "RunScript InCombatLockdown GetCVar SetCVar GetCVarBool PlaySound PlaySoundFile "
    "UIParent WorldFrame SlashCmdList Enum Constants _G"
).split()

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


class CaseInsensitiveFiles:
    """Resolve TOC/XML paths the way Windows does (case-insensitively)."""

    def __init__(self, root: Path):
        self.root = root
        self._dirs: dict[Path, dict[str, str]] = {}

    def _listing(self, directory: Path) -> dict[str, str]:
        listing = self._dirs.get(directory)
        if listing is None:
            try:
                listing = {entry.name.lower(): entry.name for entry in directory.iterdir()}
            except OSError:
                listing = {}
            self._dirs[directory] = listing
        return listing

    def resolve(self, base: Path, relative: str) -> Path | None:
        current = base
        for part in re.split(r"[\\/]+", relative.strip()):
            if part in ("", "."):
                continue
            if part == "..":
                current = current.parent
                continue
            actual = self._listing(current).get(part.lower())
            if actual is None:
                return None
            current = current / actual
        return current if current.is_file() else None


# --------------------------------------------------------------------------
# TOC handling
# --------------------------------------------------------------------------

_BRACKET = re.compile(r"\[([^\]]*)\]")


def _split_names(text: str) -> set[str]:
    return {name.lower() for name in re.split(r"[,\s]+", text.strip()) if name}


class LoadRules:
    def __init__(self, game_type: str):
        info = GAME_TYPES[game_type]
        self.game_type = game_type
        self.family = info["family"]
        self.family_dir = info["family_dir"]
        self.game_dir = info["game_dir"]
        self.mine = {game_type, self.family}

    def conditions_met(self, directives: list[str]) -> tuple[bool, set[str]]:
        """Evaluate TOC bracket directives. Returns (load?, flags)."""
        flags: set[str] = set()
        for directive in directives:
            words = directive.replace(":", " ").split(None, 1)
            if not words:
                continue
            key = words[0].lower()
            argument = words[1] if len(words) > 1 else ""
            if key == "allowloadgametype":
                if not (self.mine & _split_names(argument)):
                    return False, flags
            elif key == "excludeloadgametype":
                if self.mine & _split_names(argument):
                    return False, flags
            elif key == "allowload":
                if argument.strip().lower() == "glue":
                    return False, flags
            elif key == "allowloadtextlocale":
                if "enus" not in _split_names(argument):
                    return False, flags
            elif key == "loadintoenvironment":
                if argument.strip().lower() != "global":
                    return False, flags
            elif key == "allowloadenvironment":
                if argument.strip().lower() not in ("global", "both", "all"):
                    return False, flags
            elif key == "bootstrap":
                flags.add("bootstrap")
            else:
                flags.add("unknown:" + key)
        return True, flags

    def expand(self, path: str) -> str:
        path = re.sub(r"\[family\]", self.family_dir, path, flags=re.IGNORECASE)
        return re.sub(r"\[game\]", self.game_dir, path, flags=re.IGNORECASE)


class TocFile:
    def __init__(self, path: Path, rules: LoadRules):
        self.path = path
        self.addon = path.parent.name
        self.headers: dict[str, str] = {}
        self.files: list[tuple[str, set[str]]] = []  # (relative path, flags)
        self.unknown_directives: set[str] = set()
        for raw in read_text(path).splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("##"):
                match = re.match(r"##\s*([^:]+?)\s*:\s*(.*)$", line)
                if not match:
                    continue
                key, value = match.group(1).lower(), match.group(2)
                load, _ = rules.conditions_met(_BRACKET.findall(value))
                if load:
                    self.headers[key] = _BRACKET.sub("", value).strip()
                continue
            if line.startswith("#"):
                continue
            expanded = rules.expand(line)
            load, flags = rules.conditions_met(_BRACKET.findall(expanded))
            self.unknown_directives |= {flag for flag in flags if flag.startswith("unknown:")}
            if load:
                self.files.append((_BRACKET.sub("", expanded).strip(), flags))

        headers = self.headers
        self.load_on_demand = headers.get("loadondemand", "0").strip() == "1"
        self.secure_environment = headers.get("usesecureenvironment", "0").strip() == "1"
        self.loads = True
        if "allowloadgametype" in headers and not (rules.mine & _split_names(headers["allowloadgametype"])):
            self.loads = False
        if "excludeloadgametype" in headers and (rules.mine & _split_names(headers["excludeloadgametype"])):
            self.loads = False
        if headers.get("allowload", "both").strip().lower() == "glue":
            self.loads = False
        if headers.get("onlybetaandptr", "0").strip() == "1":
            self.loads = False


# --------------------------------------------------------------------------
# Lua scanning (regex: top-level definitions only)
# --------------------------------------------------------------------------

_LONG_BRACKET = re.compile(r"(--)?\[(=*)\[.*?\]\2\]", re.DOTALL)
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_RE_LOCAL = re.compile(rf"^[ \t]*local\s+(?:function\s+)?({_IDENT}(?:[ \t]*,[ \t]*{_IDENT})*)", re.MULTILINE)
_RE_FUNCTION = re.compile(rf"^([ \t]*)function\s+({_IDENT})((?:\.{_IDENT})*)(?::({_IDENT}))?\s*\(", re.MULTILINE)
_RE_ASSIGN = re.compile(rf"^({_IDENT})((?:\.{_IDENT})*)\s*=(?!=)", re.MULTILINE)
_RE_G_ASSIGN = re.compile(rf"^_G(?:\.({_IDENT})|\[\s*[\"']({_IDENT})[\"']\s*\])\s*=(?!=)", re.MULTILINE)
_RE_TABLE_OPEN = re.compile(rf"^({_IDENT})\s*=\s*(?:{_IDENT}\s*\()?\s*\{{\s*(?:--.*)?$", re.MULTILINE)
_RE_TABLE_KEY = re.compile(rf"^\t({_IDENT})\s*=(?!=)")
_RE_MIXIN_PARENTS = re.compile(rf"^({_IDENT})\s*=\s*CreateFromMixins\(([^()]*)\)", re.MULTILINE)
_RE_METHOD_CALL = re.compile(rf"\b({_IDENT}):({_IDENT})\s*\(")
_RE_SCRIPT_NAME = re.compile(r":(?:SetScript|HookScript)\(\s*[\"'](\w+)[\"']")
_RE_WORD = re.compile(_IDENT)
_RE_TOC_FIELD = re.compile(r"GetAddOnMetadata\([^(),]+,\s*[\"']([A-Za-z][\w-]*)[\"']\s*\)")
_RE_LINE_COMMENT = re.compile(r"--.*")
_RE_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_RE_CAPS = re.compile(r"(?<![.:\w])([A-Z][A-Z0-9_]{3,})\b(?!\s*=[^=])")
_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
    "nil", "not", "or", "repeat", "return", "then", "true", "until", "while",
}


def _blank_long_brackets(text: str) -> str:
    """Remove block comments and long strings but keep the line count."""

    def blank(match: re.Match) -> str:
        return "\n" * match.group(0).count("\n")

    return _LONG_BRACKET.sub(blank, text)


class LuaScan:
    """Global names one Lua file defines at file scope."""

    def __init__(self, text: str):
        text = _blank_long_brackets(text)
        self.text = text
        # ALL_CAPS identifiers used as bare names (not fields, not assigned):
        # how Blizzard's code refers to global strings and colour constants,
        # which the client defines from its database rather than in Lua.
        code = _RE_STRING.sub('""', _RE_LINE_COMMENT.sub("", text))
        self.caps: set[str] = set(_RE_CAPS.findall(code))
        locals_: set[str] = set()
        for match in _RE_LOCAL.finditer(text):
            for name in re.split(r"\s*,\s*", match.group(1)):
                locals_.add(name)
        self.functions: set[str] = set()
        self.values: set[str] = set()
        self.members: dict[str, set[str]] = defaultdict(set)
        # XMixin = CreateFromMixins(A, B): XMixin also has A's and B's methods.
        self.mixin_parents: dict[str, list[str]] = {}
        for match in _RE_MIXIN_PARENTS.finditer(text):
            if match.group(1) not in locals_:
                parents = [name for name in re.split(r"[\s,]+", match.group(2)) if re.fullmatch(_IDENT, name)]
                if parents:
                    self.mixin_parents[match.group(1)] = parents

        for match in _RE_FUNCTION.finditer(text):
            indent, root, path, method = match.groups()
            if root in locals_:
                continue
            if not path and not method:
                self.functions.add(root)
            elif not indent:
                # function A.b.c() / function A:m(): record the first member.
                first = path.split(".")[1] if path else method
                self.members[root].add(first)
        for match in _RE_ASSIGN.finditer(text):
            root, path = match.groups()
            if root in locals_ or root in _LUA_KEYWORDS:
                continue
            if path:
                self.members[root].add(path.split(".")[1])
            else:
                self.values.add(root)
        for match in _RE_G_ASSIGN.finditer(text):
            self.values.add(match.group(1) or match.group(2))

        # Keys of top-level table constructors (SOUNDKIT = { ... }).
        lines = text.split("\n")
        for match in _RE_TABLE_OPEN.finditer(text):
            name = match.group(1)
            if name in locals_:
                continue
            index = text.count("\n", 0, match.end()) + 1
            while index < len(lines) and not lines[index].startswith("}"):
                key = _RE_TABLE_KEY.match(lines[index])
                if key:
                    self.members[name].add(key.group(1))
                index += 1


# --------------------------------------------------------------------------
# XML scanning
# --------------------------------------------------------------------------


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _names(value: str | None) -> list[str]:
    return [name for name in re.split(r"[,\s]+", value or "") if name]


class XmlCollector:
    """Named frames, templates, fonts, intrinsics and script handlers."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}       # name -> {kind, type, ...}
        self.scripts: dict[str, set[str]] = defaultdict(set)
        self.mixins_used: set[str] = set()       # mixins applied by always-loaded XML
        self.parse_errors: list[str] = []

    # ---- element classification --------------------------------------------
    @staticmethod
    def widget_type(tag: str) -> str | None:
        if tag in NON_WIDGET_TAGS or tag.startswith("On") or tag in ("PreClick", "PostClick"):
            return None
        return SPECIAL_CHILD_TYPES.get(tag, tag)

    def _child_elements(self, element: ET.Element):
        """Yield (child element, widget type) for regions/frames/animations
        that an element creates."""
        for child in element:
            tag = _local_tag(child)
            if tag in ("Layers",):
                for layer in child:
                    for region in layer:
                        region_type = self.widget_type(_local_tag(region))
                        if region_type:
                            yield region, region_type
            elif tag in ("Frames", "ScrollChild"):
                for frame in child:
                    frame_type = self.widget_type(_local_tag(frame))
                    if frame_type:
                        yield frame, frame_type
            elif tag == "Animations":
                for group in child:
                    if _local_tag(group) == "AnimationGroup":
                        yield group, "AnimationGroup"
            elif tag in ANIMATION_TAGS and _local_tag(element) == "AnimationGroup":
                yield child, tag
            elif tag in SPECIAL_CHILD_TYPES or (tag == "FontString" and _local_tag(element) != "Layer"):
                yield child, SPECIAL_CHILD_TYPES.get(tag, tag)

    def _shape(self, element: ET.Element, widget: str, depth: int = 0, with_children: bool = True) -> dict:
        """The part of a template (or frame) the mock needs to instantiate it."""
        shape: dict = {"t": widget}
        inherits = _names(element.get("inherits"))
        if inherits:
            shape["i"] = inherits
        mixins = _names(element.get("mixin")) + _names(element.get("secureMixin"))
        if mixins:
            shape["m"] = mixins
        children = []
        if depth < 6 and with_children:
            for child, child_type in self._child_elements(element):
                child_shape = self._shape(child, child_type, depth + 1)
                key = child.get("parentKey")
                array = child.get("parentArray")
                name = child.get("name")
                if (child.get("hidden") or "").lower() == "true":
                    child_shape["h"] = 1
                if key:
                    child_shape["k"] = key
                if array:
                    child_shape["a"] = array
                if name:
                    child_shape["n"] = name
                # Children nobody can reach by key or name are of no use.
                if key or array or name or "c" in child_shape:
                    children.append(child_shape)
        if children:
            shape["c"] = children
        return shape

    def _record_scripts(self, element: ET.Element, widget: str) -> None:
        for child in element:
            if _local_tag(child) == "Scripts":
                for handler in child:
                    self.scripts[widget].add(_local_tag(handler))
        for child, child_type in self._child_elements(element):
            self._record_scripts(child, child_type)

    def _register_names(self, element: ET.Element, widget: str, parent_name: str | None, lod: str | None) -> None:
        """Global names created when a non-virtual element is instantiated."""
        name = element.get("name")
        resolved = parent_name
        if name:
            if "$parent" in name:
                resolved = name.replace("$parent", parent_name) if parent_name else None
            else:
                resolved = name
            if resolved and resolved not in self.objects:
                entry = self._shape(element, widget, with_children=False)
                entry["kind"] = "frame"
                if lod:
                    entry["lod"] = lod
                self.objects[resolved] = entry
        for child, child_type in self._child_elements(element):
            self._register_names(child, child_type, resolved, lod)

    def top_level(self, element: ET.Element, lod: str | None) -> None:
        tag = _local_tag(element)
        if tag in ("Font", "FontFamily"):
            name = element.get("name")
            if name:
                entry = {"kind": "font"}
                inherits = _names(element.get("inherits"))
                if inherits:
                    entry["i"] = inherits
                if lod:
                    entry["lod"] = lod
                self.objects[name] = entry
            return
        widget = self.widget_type(tag)
        if not widget:
            return
        self._record_scripts(element, widget)
        if not lod:
            for node in element.iter():
                self.mixins_used.update(_names(node.get("mixin")))
                self.mixins_used.update(_names(node.get("secureMixin")))
        name = element.get("name")
        virtual = (element.get("virtual") or "").lower() == "true"
        intrinsic = (element.get("intrinsic") or "").lower() == "true"
        if name and (virtual or intrinsic):
            entry = self._shape(element, widget)
            entry["kind"] = "intrinsic" if intrinsic else "template"
            if lod:
                entry["lod"] = lod
            self.objects[name] = entry
        else:
            self._register_names(element, widget, None, lod)


# --------------------------------------------------------------------------
# Walking the interface source
# --------------------------------------------------------------------------


class SourceWalker:
    def __init__(self, root: Path, rules: LoadRules):
        self.root = root
        self.rules = rules
        self.files = CaseInsensitiveFiles(root)
        self.xml = XmlCollector()
        self.lua_functions: dict[str, str | None] = {}   # name -> lod addon or None
        self.lua_values: dict[str, str | None] = {}
        self.members: dict[str, set[str]] = defaultdict(set)
        self.member_addons: dict[str, set[str]] = defaultdict(set)   # table -> defining addons
        self.mixin_parents: dict[str, list[str]] = {}
        self.addon = ""                      # addon whose TOC is being followed
        self.tooltip_methods: set[str] = set()
        self.words: set[str] = set()         # every identifier Blizzard's code mentions
        self.caps: set[str] = set()          # ALL_CAPS names used by always-loaded code
        self.toc_fields: set[str] = set()    # TOC metadata the client's own UI reads
        self.seen: set[Path] = set()
        self.missing: list[str] = []
        self.stats = defaultdict(int)
        self.unknown_directives: set[str] = set()

    def _note(self, table: dict[str, str | None], name: str, lod: str | None) -> None:
        # "Always loaded" wins over "load on demand".
        if name not in table or (table[name] is not None and lod is None):
            table[name] = lod

    def load_lua(self, path: Path, lod: str | None) -> None:
        if path in self.seen:
            return
        self.seen.add(path)
        self.stats["lua_files"] += 1
        text = read_text(path)
        scan = LuaScan(text)
        for name in scan.functions:
            self._note(self.lua_functions, name, lod)
        for name in scan.values:
            self._note(self.lua_values, name, lod)
        if lod is None:
            for table, members in scan.members.items():
                self.members[table] |= members
                self.member_addons[table].add(self.addon)
            self.mixin_parents.update(scan.mixin_parents)
            self.caps |= scan.caps
        for owner, method in _RE_METHOD_CALL.findall(scan.text):
            if owner == "GameTooltip":
                self.tooltip_methods.add(method)
        self.words.update(_RE_WORD.findall(scan.text))
        self.toc_fields.update(_RE_TOC_FIELD.findall(text))

    def load_xml(self, path: Path, lod: str | None) -> None:
        if path in self.seen:
            return
        self.seen.add(path)
        self.stats["xml_files"] += 1
        text = read_text(path)
        try:
            root = ET.parse(io.StringIO(text)).getroot()
        except ET.ParseError as error:
            self.xml.parse_errors.append(f"{path.relative_to(self.root)}: {error}")
            return
        self._walk_ui(root, path.parent, lod)

    def _walk_ui(self, container: ET.Element, directory: Path, lod: str | None) -> None:
        for element in container:
            tag = _local_tag(element)
            if tag in ("Script", "Include"):
                reference = element.get("file")
                if reference:
                    self.load_file(directory, reference, lod)
            elif tag == "ScopedModifier":
                self._walk_ui(element, directory, lod)
            else:
                self.xml.top_level(element, lod)

    def load_file(self, base: Path, relative: str, lod: str | None) -> None:
        path = self.files.resolve(base, relative)
        if path is None and base.parent != self.root:
            # <Script file="..."> may also be given relative to the addon folder.
            addon_root = self.root / base.relative_to(self.root).parts[0]
            path = self.files.resolve(addon_root, relative)
        if path is None:
            self.missing.append(f"{base.relative_to(self.root)}: {relative}")
            return
        suffix = path.suffix.lower()
        if suffix == ".lua":
            self.load_lua(path, lod)
        elif suffix == ".xml":
            self.load_xml(path, lod)

    def walk(self) -> None:
        for toc_path in sorted(self.root.glob("*/*.toc")):
            # Flavour-specific TOCs (Addon_Vanilla.toc ...) do not apply.
            if toc_path.stem.lower() != toc_path.parent.name.lower():
                self.stats["tocs_other_flavour"] += 1
                continue
            toc = TocFile(toc_path, self.rules)
            self.unknown_directives |= toc.unknown_directives
            if not toc.loads:
                self.stats["addons_not_for_this_client"] += 1
                continue
            if toc.secure_environment:
                # Runs in its own environment: its globals are invisible to addons.
                self.stats["addons_secure_environment"] += 1
                continue
            self.stats["addons_load_on_demand" if toc.load_on_demand else "addons_loaded_at_login"] += 1
            self.addon = toc.addon
            for relative, flags in toc.files:
                lod = toc.addon if (toc.load_on_demand and "bootstrap" not in flags) else None
                self.load_file(toc_path.parent, relative, lod)


# --------------------------------------------------------------------------
# API documentation (executed under lupa)
# --------------------------------------------------------------------------

_DOC_COLLECTOR = r"""
local emit = ...
local docs = {}

-- Documentation files reference Enum.X.Y and Constants.X.Y while they are
-- being defined (sometimes in arithmetic); any such lookup yields a
-- placeholder that tolerates being indexed and calculated with.
local placeholderMeta = {}
local function placeholder()
    return setmetatable({}, placeholderMeta)
end
placeholderMeta.__index = function(t, k)
    local v = placeholder()
    rawset(t, k, v)
    return v
end
for _, op in ipairs({ "__add", "__sub", "__mul", "__div", "__mod", "__pow", "__unm" }) do
    placeholderMeta[op] = function() return 0 end
end
placeholderMeta.__concat = function() return "" end

local env = setmetatable({
    APIDocumentation = { AddDocumentationTable = function(_, doc) docs[#docs + 1] = doc end },
    Enum = placeholder(),
    Constants = placeholder(),
}, { __index = function(_, k) return nil end })

local function secretTags(fn)
    local tags = {}
    for key, value in pairs(fn) do
        if type(key) == "string" and value and
            (key:find("^SecretWhen") or key:find("^SecretIn") or key == "SecretReturns"
                or key == "HasRestrictions" or key == "IsProtectedFunction") then
            tags[#tags + 1] = key
        end
    end
    table.sort(tags)
    return table.concat(tags, ",")
end

local function run(source, chunkName)
    local chunk, err = loadstring(source, chunkName)
    if not chunk then
        return err
    end
    setfenv(chunk, env)
    local ok, runErr = pcall(chunk)
    if not ok then
        return runErr
    end
end

local function finish()
    for _, doc in ipairs(docs) do
        local docType = doc.Type or "System"
        local namespace = doc.Namespace or ""
        local environment = doc.Environment or "All"
        emit("table", docType, doc.Name or "", namespace, "", "", environment)
        for _, fn in ipairs(doc.Functions or {}) do
            emit("function", docType, doc.Name or "", namespace, fn.Name, secretTags(fn), fn.Environment or environment)
        end
        for _, event in ipairs(doc.Events or {}) do
            emit("event", docType, doc.Name or "", namespace, event.LiteralName or event.Name, "", environment)
        end
        for _, tbl in ipairs(doc.Tables or {}) do
            if tbl.Type == "Enumeration" then
                for _, field in ipairs(tbl.Fields or {}) do
                    emit("enum", tbl.Name, field.Name, tostring(field.EnumValue), "", "", environment)
                end
            elseif tbl.Type == "Constants" then
                for _, field in ipairs(tbl.Values or {}) do
                    emit("constant", tbl.Name, field.Name, "", "", "", environment)
                end
            end
        end
    end
    return #docs
end

return run, finish
"""


def collect_documentation(doc_dir: Path, files: CaseInsensitiveFiles, rules: LoadRules) -> dict:
    from lupa.lua51 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    result = {
        "globals": {},          # name -> secret tags
        "namespaces": defaultdict(dict),
        "script_objects": defaultdict(set),
        "events": set(),
        "enums": defaultdict(dict),
        "constants": defaultdict(set),
        "errors": [],
    }

    def emit(kind, a, b, c, d, e, environment):
        if environment == "SecureOnly":
            return
        if kind == "table":
            if a == "ScriptObject":
                result["script_objects"][b].update(())
        elif kind == "function":
            doc_type, doc_name, namespace, name, tags = a, b, c, d, e
            tag_list = tags.split(",") if tags else []
            if doc_type == "ScriptObject":
                result["script_objects"][doc_name].add(name)
            elif namespace:
                result["namespaces"][namespace][name] = tag_list
            else:
                result["globals"][name] = tag_list
        elif kind == "event":
            result["events"].add(d)
        elif kind == "enum":
            try:
                result["enums"][a][b] = int(c)
            except ValueError:
                pass
        elif kind == "constant":
            result["constants"][a].add(b)

    run, finish = lua.execute(_DOC_COLLECTOR, emit)

    toc = TocFile(doc_dir / (doc_dir.name + ".toc"), rules)
    count = 0
    for relative, _flags in toc.files:
        path = files.resolve(doc_dir, relative)
        if path is None or path.suffix.lower() != ".lua":
            continue
        error = run(read_text(path), "@" + path.name)
        if error:
            result["errors"].append(f"{path.name}: {error}")
        count += 1
    result["doc_files"] = count
    result["doc_tables"] = finish()
    return result


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------


def build_snapshot(source: Path, game_type: str = "camelot") -> tuple[dict, dict]:
    rules = LoadRules(game_type)
    walker = SourceWalker(source, rules)
    walker.walk()

    docs = collect_documentation(source / "Blizzard_APIDocumentationGenerated", walker.files, rules)
    if docs["errors"]:
        raise SystemExit("documentation files failed to run:\n  " + "\n  ".join(docs["errors"][:20]))

    # ---- widgets -----------------------------------------------------------
    script_objects = docs["script_objects"]
    used_tables: set[str] = set()
    for widget, (parent, tables) in WIDGET_TYPES.items():
        if parent is not None and parent not in WIDGET_TYPES:
            raise SystemExit(f"widget type {widget}: unknown parent {parent}")
        for table in tables:
            if table not in script_objects:
                raise SystemExit(f"widget type {widget}: documentation table {table} not found")
            used_tables.add(table)
    unmapped_tables = sorted(set(script_objects) - used_tables)

    schema_scripts: set[str] = set()
    xsd = walker.files.resolve(source, "Blizzard_SharedXML/UI.xsd")
    if xsd is not None:
        schema_scripts = set(re.findall(r'<xs:element name="((?:On|Pre|Post)\w+)"', read_text(xsd)))
    for widget, handlers in WIDGET_SCRIPTS.items():
        if widget not in WIDGET_TYPES:
            raise SystemExit(f"script table: unknown widget type {widget}")
        for handler in handlers:
            if schema_scripts and handler not in schema_scripts:
                raise SystemExit(f"script handler {widget}.{handler} is not in UI.xsd")

    widget_scripts: dict[str, list[str]] = {}
    for widget in WIDGET_TYPES:
        handlers: set[str] = set()
        current = widget
        while current is not None:
            handlers |= set(WIDGET_SCRIPTS.get(current, ()))
            current = WIDGET_TYPES[current][0]
        if handlers:
            widget_scripts[widget] = sorted(handlers)

    # Review aid: handlers Blizzard's XML uses that the table does not allow.
    intrinsics = {name: entry for name, entry in walker.xml.objects.items() if entry.get("kind") == "intrinsic"}

    def native_type(tag: str, hops: int = 0) -> str | None:
        if tag in WIDGET_TYPES:
            return tag
        if tag in intrinsics and hops < 8:
            return native_type(intrinsics[tag]["t"], hops + 1)
        return None

    script_gaps: list[str] = []
    for tag, handlers in sorted(walker.xml.scripts.items()):
        native = native_type(tag)
        if native is None:
            continue
        extra = sorted(handlers - set(widget_scripts.get(native, ())))
        if extra:
            script_gaps.append(f"<{tag}> ({native}): {', '.join(extra)}")

    # GameTooltip's own methods (SetOwner, AddLine ...) are not in the
    # generated documentation; take the ones Blizzard's code calls on it.
    documented_tooltip: set[str] = set()
    current = "GameTooltip"
    while current is not None:
        for table in WIDGET_TYPES[current][1]:
            documented_tooltip |= script_objects[table]
        current = WIDGET_TYPES[current][0]
    tooltip_extra = sorted(walker.tooltip_methods - documented_tooltip)

    # ---- names -------------------------------------------------------------
    # XML objects: templates (and intrinsics) keep their shape so the mock can
    # instantiate them; fonts and frames are globals at login.
    templates, fonts, frames, lod_names = {}, set(), {}, {}
    for name, entry in walker.xml.objects.items():
        entry = dict(entry)
        lod = entry.pop("lod", None)
        kind = entry.pop("kind")
        if lod:
            lod_names[name] = lod
        elif kind in ("template", "intrinsic"):
            if kind == "intrinsic":
                entry["x"] = 1
            templates[name] = entry
        elif kind == "font":
            fonts.add(name)
        else:
            frames[name] = entry

    # Lua definitions. A name can be both (a frame created in XML and a
    # function of the same name is not unusual); every kind is kept.
    lua_functions = {name for name, lod in walker.lua_functions.items() if lod is None}
    lua_values = {name for name, lod in walker.lua_values.items() if lod is None} - lua_functions
    for table in (walker.lua_functions, walker.lua_values):
        for name, lod in table.items():
            if lod is not None:
                lod_names.setdefault(name, lod)
    defined = set(templates) | fonts | set(frames) | lua_functions | lua_values
    for name in list(lod_names):
        if name in defined:
            del lod_names[name]

    documented = set(docs["globals"]) | set(docs["namespaces"])
    c_globals = sorted(
        name for name in C_GLOBAL_CANDIDATES
        if name in walker.words and name not in documented and name not in defined
    )
    c_unverified = sorted(name for name in C_GLOBAL_CANDIDATES if name not in walker.words)

    # Global strings and colour constants: used by Blizzard's always-loaded
    # code as bare ALL_CAPS names but defined by the client, not in Lua.
    referenced = sorted(
        name for name in walker.caps
        if name not in defined and name not in documented
        and name not in c_globals and name not in lod_names
    )

    # Members of Lua-defined global tables (Settings.RegisterAddOnCategory,
    # BackdropTemplateMixin:SetBackdrop, SOUNDKIT.IG_MAINMENU_OPEN ...) and
    # Lua additions to documented namespaces (C_Timer.NewTicker). To keep the
    # snapshot small, mixins are only kept if some XML template or frame
    # applies them (the mock needs those to instantiate widgets) or if they
    # come from the shared foundation addons that third-party addons build on
    # (ColorMixin, CallbackRegistryMixin ...).
    members = {}
    for table, found in walker.members.items():
        if not found:
            continue
        if table in docs["namespaces"]:
            members[table] = sorted(found)
        elif table in lua_values:
            foundation = any(addon in FOUNDATION_ADDONS for addon in walker.member_addons[table])
            if table.endswith("Mixin") and table not in walker.xml.mixins_used and not foundation:
                continue
            members[table] = sorted(found)

    # Per-function tags: which can return secret values (and when), which
    # are protected (addons may not call them) or restricted (blocked for
    # addons in some situations, e.g. combat lockdown).
    secret, protected, restricted = {}, [], []

    def note_tags(full_name: str, tags: list[str]) -> None:
        secret_tags = [tag for tag in tags if tag.startswith("Secret")]
        if secret_tags:
            secret[full_name] = secret_tags
        if "IsProtectedFunction" in tags:
            protected.append(full_name)
        if "HasRestrictions" in tags:
            restricted.append(full_name)

    for name, tags in docs["globals"].items():
        note_tags(name, tags)
    for namespace, functions in docs["namespaces"].items():
        for name, tags in functions.items():
            note_tags(f"{namespace}.{name}", tags)

    version = ""
    version_file = source.parent.parent / "version.txt"
    if version_file.is_file():
        version = read_text(version_file).strip()

    snapshot = {
        "meta": {
            "format": SNAPSHOT_FORMAT,
            "client_version": version,
            "game_type": game_type,
            "family": rules.family,
        },
        "api": {
            "globals": sorted(docs["globals"]),
            "namespaces": {ns: sorted(fns) for ns, fns in sorted(docs["namespaces"].items())},
            "secret": dict(sorted(secret.items())),
            "protected": sorted(protected),
            "restricted": sorted(restricted),
        },
        "c_globals": c_globals,
        "events": sorted(docs["events"]),
        "toc_fields": sorted(walker.toc_fields),
        "enums": sorted(docs["enums"]),
        "widgets": {
            "apis": {name: sorted(methods) for name, methods in sorted(script_objects.items())},
            "types": {
                widget: {"parent": parent, "apis": list(tables)}
                for widget, (parent, tables) in sorted(WIDGET_TYPES.items())
            },
            "scripts": dict(sorted(widget_scripts.items())),
            "tooltip_methods": tooltip_extra,
        },
        "lua": {"functions": sorted(lua_functions), "values": sorted(lua_values)},
        "fonts": sorted(fonts),
        "frames": dict(sorted(frames.items())),
        "referenced": referenced,
        "templates": dict(sorted(templates.items())),
        "members": dict(sorted(members.items())),
        "mixin_parents": {
            name: parents for name, parents in sorted(walker.mixin_parents.items()) if name in members
        },
        "lod": dict(sorted(lod_names.items())),
    }

    report = {
        "client_version": version,
        "doc_files": docs["doc_files"],
        "doc_tables": docs["doc_tables"],
        "global_functions": len(docs["globals"]),
        "namespaces": len(docs["namespaces"]),
        "namespaced_functions": sum(len(fns) for fns in docs["namespaces"].values()),
        "secret_tagged_functions": len(secret),
        "protected_functions": len(protected),
        "restricted_functions": len(restricted),
        "events": len(docs["events"]),
        "toc_fields_read_by_blizzard_ui": sorted(walker.toc_fields),
        "enums": len(docs["enums"]),
        "widget_api_tables": len(script_objects),
        "widget_types": len(WIDGET_TYPES),
        "unmapped_widget_api_tables": unmapped_tables,
        "tooltip_methods_from_usage": len(tooltip_extra),
        "c_globals": len(c_globals),
        "c_globals_not_seen_in_source": c_unverified,
        "templates": sum(1 for entry in templates.values() if "x" not in entry),
        "intrinsics": sum(1 for entry in templates.values() if "x" in entry),
        "fonts": len(fonts),
        "xml_frames": len(frames),
        "lua_functions": len(lua_functions),
        "lua_values": len(lua_values),
        "tables_with_members": len(members),
        "member_names": sum(len(found) for found in members.values()),
        "load_on_demand_names": len(lod_names),
        "referenced_caps_names": len(referenced),
        "xml_script_handlers_not_in_table": script_gaps,
        "files": dict(sorted(walker.stats.items())),
        "missing_files": walker.missing,
        "xml_parse_errors": walker.xml.parse_errors,
        "unknown_toc_directives": sorted(walker.unknown_directives),
    }
    return snapshot, report


def write_snapshot(snapshot: dict, output: Path) -> int:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    # mtime=0 and no file name in the header keep the output reproducible.
    with gzip.GzipFile(filename="", fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write(payload)
    output.write_bytes(buffer.getvalue())
    return len(buffer.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=Path, help="path to Blizzard's Interface/AddOns folder")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--game-type", default="camelot", choices=sorted(GAME_TYPES))
    args = parser.parse_args(argv)

    source = args.source
    if not (source / "Blizzard_APIDocumentationGenerated").is_dir():
        parser.error(f"{source} does not look like Interface/AddOns (no Blizzard_APIDocumentationGenerated)")

    snapshot, report = build_snapshot(source, args.game_type)
    size = write_snapshot(snapshot, args.output)

    print(f"wrote {args.output} ({size / 1024:.0f} KiB)")
    for key, value in report.items():
        if isinstance(value, list):
            print(f"  {key}: {len(value)}")
            for item in value[:12]:
                print(f"      {item}")
            if len(value) > 12:
                print(f"      ... and {len(value) - 12} more")
        else:
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
