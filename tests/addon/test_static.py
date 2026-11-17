"""Checks on the addon's source text.

A few rules cannot be tested by running the code in the mock client - most
of all the secret-value rules that Lua 5.1 cannot observe (see the package
docstring). These tests read the Lua files instead. They are deliberately
simple pattern checks, not a Lua parser: they catch the careless cases.
"""

from __future__ import annotations

import re

import pytest

from .harness import ADDON_DIR, parse_toc

LUA_FILES = sorted(ADDON_DIR.rglob("*.lua"))

# TOC directives the client itself interprets (not read through
# C_AddOns.GetAddOnMetadata by Blizzard's Lua, so not in the snapshot).
CLIENT_TOC_DIRECTIVES = {
    "Interface", "Title", "Notes", "Author", "Version", "SavedVariables", "SavedVariablesPerCharacter",
    "Dependencies", "RequiredDeps", "OptionalDeps", "LoadOnDemand", "DefaultState",
}


def code_of(path) -> str:
    """The file without comments and string contents (line structure kept)."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"--\[(=*)\[.*?\]\1\]", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'', '""', text)
    return text


def relative(path) -> str:
    return path.relative_to(ADDON_DIR).as_posix()


# ---- secret values ------------------------------------------------------------------------


def test_only_game_lua_calls_apis_that_can_return_secrets(snapshot):
    offenders = []
    for path in LUA_FILES:
        if relative(path) == "Game.lua":
            continue
        code = code_of(path)
        for name in snapshot.secret:
            if re.search(r"(?<![\w.:])" + re.escape(name) + r"\s*\(", code):
                offenders.append(f"{relative(path)} calls {name}()")
    assert offenders == [], "secret-capable APIs belong in Game.lua, behind its issecretvalue() checks"


def test_game_lua_never_tests_or_combines_a_possibly_secret_result_directly(snapshot):
    """`UnitName("player") or "?"`, `if UnitIsAFK("player") then`, `GetUnitSpeed("player") > 0`:
    each is an operation on the raw result, which raises in the game when
    the result is secret - and which the mock cannot see. The result has to
    go into a local and through issecretvalue() first."""
    code = code_of(ADDON_DIR / "Game.lua")
    problems = []
    for name in snapshot.secret:
        if "." in name:
            continue
        for match in re.finditer(r"(?<![\w.:])" + re.escape(name) + r"\s*\([^()]*\)", code):
            before = code[: match.start()].rsplit("\n", 1)[-1]
            after = code[match.end():].split("\n", 1)[0]
            line = code.count("\n", 0, match.start()) + 1
            if re.match(r"\s*(or\b|and\b|==|~=|<=|>=|<|>|\.\.|[-+*/%^])", after):
                problems.append(f"Game.lua:{line}: {name}(...) is used in an expression: `{after.strip()[:20]}`")
            if re.search(r"\b(not|if|elseif|while|until)\s+\(?\s*$", before):
                problems.append(f"Game.lua:{line}: {name}(...) is tested directly")
    assert problems == []


def test_game_lua_checks_for_secrets_before_using_api_results():
    code = code_of(ADDON_DIR / "Game.lua")
    assert "issecretvalue" in code
    assert "C_Secrets" in code and "ShouldUnitStatsBeSecret" in code


# ---- globals and API names ----------------------------------------------------------------------


@pytest.mark.parametrize("path", LUA_FILES, ids=relative)
def test_every_file_starts_with_the_addon_varargs(path):
    if relative(path) == "CompanionData.lua":
        pytest.skip("a data file, rewritten by the companion app")
    code = code_of(path)
    assert re.search(r"^local\s+ADDON_NAME\s*,\s*ns\s*=\s*\.\.\.", code, re.M), "local ADDON_NAME, ns = ... is missing"


@pytest.mark.parametrize("path", LUA_FILES, ids=relative)
def test_no_api_that_this_client_does_not_have(path, snapshot):
    # Global functions of older clients that addon code is often copied
    # with. Whichever of them this client lacks must not be called, also
    # on code paths the other tests never reach.
    code = code_of(path)
    old_names = (
        "InterfaceOptions_AddCategory", "InterfaceOptionsFrame_OpenToCategory", "GetAddOnMetadata", "IsAddOnLoaded",
        "LoadAddOn", "GetAddOnInfo", "UnitBuff", "UnitDebuff", "GetSpellInfo", "GetItemInfo", "GetMouseFocus",
        "EasyMenu", "UIDropDownMenu_Initialize", "UIFrameFlash", "getglobal", "setglobal",
    )
    gone = [name for name in old_names if not snapshot.knows_global(name)]
    assert len(gone) >= 6, "the list no longer tests anything"
    for name in gone:
        used = re.search(r"(?<![\w.:])" + re.escape(name) + r"\s*\(", code)
        assert not used, f"{relative(path)} calls {name}(), which this client does not have"


@pytest.mark.parametrize("path", LUA_FILES, ids=relative)
def test_lua_51_only(path):
    code = code_of(path)
    assert not re.search(r"\bgoto\b|::\w+::", code), "goto is Lua 5.2+"
    assert "//" not in code, "integer division is Lua 5.3+"
    assert not re.search(r"<\s*(const|close)\s*>", code), "attributes are Lua 5.4"
    assert not re.search(r"[^.]\b(table\.unpack|table\.pack|utf8\.|math\.tointeger)\b", code), "Lua 5.2+ library"


# ---- TOC ----------------------------------------------------------------------------------------------


def test_toc_lists_every_lua_file_and_nothing_else():
    toc = parse_toc(ADDON_DIR / "Walka.toc")
    listed = {name.replace("\\", "/") for name in toc.files}
    present = {relative(path) for path in LUA_FILES}
    assert present <= listed, f"not loaded by the TOC: {sorted(present - listed)}"
    missing = listed - present
    assert all(name.startswith("UI/") for name in missing), f"listed but missing: {sorted(missing)}"
    assert len(toc.files) == len(listed), "a file is listed twice"


def test_toc_uses_backslashes_and_known_fields(snapshot):
    toc = parse_toc(ADDON_DIR / "Walka.toc")
    assert not any("/" in name for name in toc.files), "TOC paths use backslashes"
    for key in toc.metadata:
        assert key in CLIENT_TOC_DIRECTIVES or key in snapshot.toc_fields or key.startswith("X-"), (
            f"## {key}: is not a TOC field this client reads"
        )
    major, minor, patch = (int(part) for part in snapshot.meta["client_version"].split(".")[:3])
    assert toc.metadata["Interface"] == str(major * 10000 + minor * 100 + patch)


def test_toc_load_order_respects_dependencies():
    # Each file takes what it needs from the shared table at load time, so
    # a file must come after the files whose modules it reads at file scope.
    toc = parse_toc(ADDON_DIR / "Walka.toc")
    order = [name.replace("\\", "/") for name in toc.files]
    defined: dict[str, str] = {}
    for name in order:
        path = ADDON_DIR / name
        if not path.is_file():
            continue
        code = code_of(path)
        for module in re.findall(r"^local\s+\w+\s*=\s*ns\.(\w+)\s*$", code, re.M):
            assert module in defined, f"{name} reads ns.{module} at load time, before any file has set it"
        for module in re.findall(r"^ns\.(\w+)\s*=", code, re.M):
            defined.setdefault(module, name)
