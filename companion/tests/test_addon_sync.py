"""Writing data for the game to load: Lua serialisation, the data file, the
addon's own merge of it, and finding / installing the addon."""

from __future__ import annotations

import math
import os
import random
import sys
import time
from pathlib import Path

import pytest

from walka import addon_sync
from walka.addon_sync import (
    DATA_FILE,
    DATA_GLOBAL,
    build_payload,
    render_data_file,
    to_lua,
    write_data_file,
)
from walka.session import SessionRecord, SessionStore
from walka_fakes import load_addon_file, lua_to_py, new_lua

T0 = 1_800_000_000.0


def normalise(value):
    """What a Python value should look like after a trip through Lua."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return 0
        return round(value, 6) + 0.0  # (-0.0 becomes 0.0)
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        out = {k: normalise(v) for k, v in value.items() if v is not None}
        # Lua has one table type: {[1] = a, [2] = b} *is* the list {a, b}.
        if out and all(isinstance(k, int) and not isinstance(k, bool) for k in out):
            if sorted(out) == list(range(1, len(out) + 1)):
                return [out[i] for i in range(1, len(out) + 1)]
        return out
    if isinstance(value, (list, tuple)):
        if not value:
            return {}
        return [False if v is None else normalise(v) for v in value]
    raise TypeError(type(value))


def through_lua(lua, value):
    text = to_lua(value)
    assert text.isascii(), "the data file must be pure ASCII whatever is in it"
    lua.execute(f"__value = {text}")
    return lua_to_py(lua.globals()["__value"])


def assert_round_trip(lua, value):
    assert through_lua(lua, value) == normalise(value)


def record(start: float, **overrides) -> SessionRecord:
    fields = dict(
        id=time.strftime("%Y%m%d-%H%M%S", time.localtime(start)),
        start=start,
        end=start + 620.4,
        seconds=600.4,
        distance_m=533.6,
        steps=912,
        kcal=38.26,
        max_kmh=3.219,
        avg_hr=101.4,
        max_hr=118,
        source="wow",
        beacon_session=7,
        steps_source="treadmill",
        treadmill="URTM030",
        samples=[[0.2, 3.2, 100]],
    )
    fields.update(overrides)
    return SessionRecord(**fields)


def make_addon_dir(tmp_path: Path) -> Path:
    addon = tmp_path / "Interface" / "AddOns" / "Walka"
    addon.mkdir(parents=True)
    (addon / "Walka.toc").write_text("## Interface: 16001\n", "utf-8")
    return addon


# ---- to_lua --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        -17,
        2**40,
        True,
        False,
        1.5,
        -0.25,
        3.0,
        -0.0,
        1e-3,
        123456.789012,
        6.437376,
        1e20,
        "",
        "plain",
        'say "hi"',
        "back\\slash",
        "C:\\Program Files (x86)\\World of Warcraft\\_forever_",
        "line1\nline2\r\nline3",
        "tab\tbell\x07nul\x00del\x7fesc\x1b",
        "trailing backslash\\",
        '\\"',
        "]]--[[ ]=]",  # would end a Lua long string; must stay inert
        "-- not a comment",
        "\\065\\n",  # looks like escapes; must stay literal
        "digits after control \x01" + "23",
        "Zürich façade ñ",
        "Привет, мир",
        "日本語のテキスト",
        "emoji 🏃‍♂️🔥 done",
        "".join(chr(c) for c in range(0, 128)),
    ],
)
def test_scalars_survive_lua(lua, value):
    assert_round_trip(lua, value)


def test_non_finite_numbers_become_zero(lua):
    for value in (float("nan"), float("inf"), float("-inf")):
        assert to_lua(value) == "0"
    assert through_lua(lua, {"a": float("nan"), "b": [float("inf")]}) == {"a": 0, "b": [0]}


def test_floats_keep_six_decimals(lua):
    assert through_lua(lua, 0.1234564) == 0.123456
    assert through_lua(lua, 1.609344) == 1.609344
    assert through_lua(lua, 1e-9) == 0


def test_nested_tables_and_lists(lua):
    value = {
        "version": 1,
        "profile": {"weightKg": 75.5, "heightCm": 172, "nested": {"deep": {"deeper": [1, [2, [3, {"x": "y"}]]]}}},
        "sessions": [
            {"start": 1, "end": 2, "tags": ["a", "b"], "ok": True},
            {"start": 3, "end": 4, "tags": [], "ok": False, "empty": {}},
        ],
        "tuple": (1, 2, 3),
        "mixed": [1, "two", 3.5, True, {"k": "v"}, [9]],
    }
    assert_round_trip(lua, value)


def test_none_is_omitted_from_tables_and_false_in_lists(lua):
    value = {"keep": 1, "drop": None, "inner": {"gone": None}, "list": [1, None, 3]}
    assert through_lua(lua, value) == {"keep": 1, "inner": {}, "list": [1, False, 3]}
    assert "drop" not in to_lua(value) and "gone" not in to_lua(value)
    assert to_lua(None) == "nil"
    assert to_lua({}) == "{}" and to_lua([]) == "{}" and to_lua({"a": None}) == "{}"
    # No holes: the list keeps its length for Lua's # and ipairs.
    lua.execute(f"__list = {to_lua([1, None, None, 4])}")
    assert lua.eval("#__list") == 4


LUA_KEYWORDS = [
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil", "not",
    "or", "repeat", "return", "then", "true", "until", "while",
]  # fmt: skip


def test_keys_that_are_lua_keywords(lua):
    value = {keyword: index for index, keyword in enumerate(LUA_KEYWORDS)}
    assert_round_trip(lua, value)
    assert '["end"] = ' in to_lua({"end": 1})
    assert "start = " in to_lua({"start": 1})  # ordinary identifiers stay bare


def test_keys_that_are_not_identifiers(lua):
    value = {
        "": 1,
        "two words": 2,
        "1starts_with_digit": 3,
        "with-dash": 4,
        'quote"inside': 5,
        "back\\slash": 6,
        "new\nline": 7,
        "ключ": 8,
        "é": 9,
        "🏃": 10,
        "_ok": 11,
        "CamelCase9": 12,
        "a.b": 13,
        "nil ": 14,
    }
    assert_round_trip(lua, value)


def test_number_keys(lua):
    value = {1: "one", 2: "two", 10: "ten", -3: "minus three", 0: "zero", 2.5: "two and a half", "1": "string one"}
    result = through_lua(lua, value)
    assert result == value
    with pytest.raises(TypeError):
        to_lua({True: 1})
    with pytest.raises(TypeError):
        to_lua({None: 1})
    with pytest.raises(TypeError):
        to_lua({(1, 2): 1})


@pytest.mark.parametrize("value", [{1, 2}, b"bytes", object(), {"k": {1, 2}}, [b"x"], 1 + 2j])
def test_unserialisable_values_raise(value):
    with pytest.raises(TypeError):
        to_lua(value)


def test_random_structures_survive_lua(lua):
    rng = random.Random(31)
    alphabet = ['"', "\\", "\n", "\r", "\t", "\x00", "\x1f", "a", "Z", "0", " ", "]", "é", "я", "名", "🙂", "-", "'"]

    def text() -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))

    def key():
        roll = rng.random()
        if roll < 0.5:
            return rng.choice(["a", "b_1", "end", "nil", "x y", "", "ключ"]) + rng.choice(["", "1", "_"])
        if roll < 0.8:
            return text()
        return rng.randrange(-5, 50)

    def build(depth: int):
        roll = rng.random()
        if depth > 3 or roll < 0.45:
            return rng.choice(
                [
                    rng.randrange(-(10**9), 10**9),
                    round(rng.uniform(-1e6, 1e6), rng.randrange(0, 7)),
                    rng.random() < 0.5,
                    text(),
                    None,
                ]
            )
        if roll < 0.75:
            return {key(): build(depth + 1) for _ in range(rng.randrange(0, 6))}
        return [build(depth + 1) for _ in range(rng.randrange(0, 6))]

    for _ in range(400):
        value = {"root": build(0)}
        assert_round_trip(lua, value)


# ---- the payload -----------------------------------------------------------------------


def test_payload_fields():
    rec = record(T0 + 0.7)
    payload = build_payload(
        [rec],
        profile={"weightKg": 75.0, "heightCm": 172.0},
        treadmill_name="URTM030",
        now=T0 + 5000.9,
    )
    assert payload["version"] == addon_sync.DATA_VERSION == 1
    assert payload["written"] == int(T0 + 5000)
    assert payload["profile"] == {"weightKg": 75.0, "heightCm": 172.0}
    assert "limits" not in payload  # the game no longer needs to know what the belt can do
    assert payload["treadmill"] == "URTM030"
    assert "older" not in payload
    assert payload["sessions"] == [
        {
            "start": int(T0),  # whole seconds, as the addon's own records have them
            "end": int(T0) + 622,  # rounded up: never shorter than it was
            "seconds": 600,
            "distanceM": 534,
            "steps": 912,
            "kcal": 38.3,
            "maxKmh": 3.22,
            "avgHr": 101,
            "maxHr": 118,
            "source": "wow",
            "beaconSession": 7,
            "stepsSource": "treadmill",
        }
    ]


def test_payload_optional_fields_are_left_out(lua):
    rec = record(T0, avg_hr=None, max_hr=None, beacon_session=None, source="free", steps_source="estimate")
    payload = build_payload([rec], profile={}, treadmill_name="", now=T0)
    assert payload["treadmill"] is None
    data = through_lua(lua, payload)
    assert "limits" not in data and "treadmill" not in data
    entry = data["sessions"][0]
    assert "avgHr" not in entry and "maxHr" not in entry and "beaconSession" not in entry
    assert entry["source"] == "free" and entry["stepsSource"] == "estimate"


def test_sessions_are_listed_newest_first_whatever_the_input_order():
    records = [record(T0 + 3600 * i, steps=i) for i in (3, 0, 4, 1, 2)]
    payload = build_payload(records, profile={}, now=T0)
    assert [s["steps"] for s in payload["sessions"]] == [4, 3, 2, 1, 0]
    starts = [s["start"] for s in payload["sessions"]]
    assert starts == sorted(starts, reverse=True)


def test_older_sessions_are_folded_into_a_summary():
    limit = addon_sync.MAX_SESSIONS_IN_FILE
    assert limit == 300
    records = [
        record(T0 + 3600 * i, steps=1000 + i, seconds=600.4 + i, distance_m=500.6 + i, kcal=40.04 + i)
        for i in range(limit + 7)
    ]
    random.Random(1).shuffle(records)
    payload = build_payload(records, profile={}, now=T0)
    assert len(payload["sessions"]) == limit
    # The newest 300 are listed ...
    assert payload["sessions"][0]["steps"] == 1000 + limit + 6
    assert payload["sessions"][-1]["steps"] == 1007
    # ... and the 7 oldest are summed.
    oldest = sorted(records, key=lambda r: r.start)[:7]
    assert payload["older"] == {
        "sessions": 7,
        "seconds": round(sum(r.seconds for r in oldest)),
        "distanceM": round(sum(r.distance_m for r in oldest)),
        "steps": sum(r.steps for r in oldest),
        "kcal": round(sum(r.kcal for r in oldest), 1),
        # ... up to where the list begins, so the addon knows which of its
        # own records are in the sum already.
        "until": int(min(r.start for r in sorted(records, key=lambda r: r.start)[7:])),
    }
    # Nothing is lost between the two: all-time steps are exact.
    listed_steps = sum(s["steps"] for s in payload["sessions"])
    assert listed_steps + payload["older"]["steps"] == sum(r.steps for r in records)


def test_exactly_the_limit_has_no_older_block():
    records = [record(T0 + 60 * i) for i in range(addon_sync.MAX_SESSIONS_IN_FILE)]
    payload = build_payload(records, profile={}, now=T0)
    assert len(payload["sessions"]) == addon_sync.MAX_SESSIONS_IN_FILE and "older" not in payload
    assert build_payload([], profile={}, now=T0)["sessions"] == []


# ---- the file ----------------------------------------------------------------------------


def test_rendered_file_is_a_single_ascii_assignment(lua):
    payload = build_payload(
        [record(T0, treadmill="Pad “é” 🏃")],
        profile={"weightKg": 80.5, "heightCm": 180.0},
        treadmill_name='Mat "No. 1" \\ é',
        now=T0,
    )
    text = render_data_file(payload)
    assert text.isascii() and text.endswith("}\n") and "\r" not in text
    assert text.count(f"{DATA_GLOBAL} = {{") == 1
    lines = text.splitlines()
    assert lines[0].startswith("-- ") and lines[1].startswith("-- ") and lines[2].startswith("-- 20")
    assert lines[3].startswith("WalkaCompanionData = {")
    lua.execute(text)
    data = lua_to_py(lua.globals()[DATA_GLOBAL])
    assert data["treadmill"] == 'Mat "No. 1" \\ é'
    assert data["profile"] == {"weightKg": 80.5, "heightCm": 180}
    assert data["sessions"][0]["steps"] == 912


def test_write_data_file_is_atomic_and_leaves_no_tmp(tmp_path: Path, lua):
    addon = make_addon_dir(tmp_path)
    payload = build_payload([record(T0)], profile={"weightKg": 75.0}, now=T0)
    target = write_data_file(addon, payload)
    assert target == addon / DATA_FILE
    assert sorted(p.name for p in addon.iterdir()) == [DATA_FILE, "Walka.toc"]
    raw = target.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")  # Unix newlines on every platform
    lua.execute(raw.decode("ascii"))
    assert lua.globals()[DATA_GLOBAL].sessions[1].steps == 912

    # Replacing an existing file.
    payload2 = build_payload([record(T0), record(T0 + 9000, steps=5)], profile={}, now=T0)
    write_data_file(addon, payload2)
    lua.execute(target.read_text("ascii"))
    assert lua.eval(f"#{DATA_GLOBAL}.sessions") == 2
    assert not list(addon.glob("*.tmp"))


def test_write_refuses_a_folder_that_is_not_the_addon(tmp_path: Path):
    folder = tmp_path / "SomeOtherAddon"
    folder.mkdir()
    payload = build_payload([], profile={}, now=T0)
    with pytest.raises(FileNotFoundError, match="Walka.toc"):
        write_data_file(folder, payload)
    assert list(folder.iterdir()) == []  # nothing was written, not even a temp file
    with pytest.raises(FileNotFoundError):
        write_data_file(tmp_path / "does-not-exist", payload)


def test_write_retries_while_the_game_holds_the_file(tmp_path: Path, monkeypatch):
    addon = make_addon_dir(tmp_path)
    target = addon / DATA_FILE
    target.write_text("-- previous contents\n", "utf-8")
    payload = build_payload([record(T0)], profile={}, now=T0)
    real_replace = os.replace
    calls = {"n": 0}
    naps: list[float] = []
    monkeypatch.setattr(addon_sync.time, "sleep", naps.append)

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(addon_sync.os, "replace", flaky)
    write_data_file(addon, payload)
    assert calls["n"] == 3 and len(naps) == 2
    assert DATA_GLOBAL in target.read_text("utf-8")
    assert not list(addon.glob("*.tmp"))

    # It never becomes possible: the old file stays intact and no temp file is left.
    target.write_text("-- previous contents\n", "utf-8")

    def locked(src, dst):
        raise PermissionError("sharing violation")

    monkeypatch.setattr(addon_sync.os, "replace", locked)
    with pytest.raises(PermissionError):
        write_data_file(addon, payload)
    assert target.read_text("utf-8") == "-- previous contents\n"
    assert not list(addon.glob("*.tmp"))


def test_sync_writes_the_stores_history(tmp_path: Path, lua):
    addon = make_addon_dir(tmp_path)
    store = SessionStore(tmp_path / "data")
    store.add(record(T0, steps=100))
    store.add(record(T0 + 5000, steps=200))
    path = addon_sync.sync(
        addon, store, profile={"weightKg": 75.0, "heightCm": 172.0}, treadmill_name="URTM030"
    )
    lua.execute(path.read_text("ascii"))
    data = lua_to_py(lua.globals()[DATA_GLOBAL])
    assert [s["steps"] for s in data["sessions"]] == [200, 100]
    assert "limits" not in data and data["treadmill"] == "URTM030"
    assert abs(data["written"] - time.time()) < 60


# ---- the addon's side of the bargain -------------------------------------------------------


@pytest.fixture
def session_log(lua, addon_source):
    return load_addon_file(lua, addon_source / "Core" / "SessionLog.lua").SessionLog


def lua_session_list(lua, entries: list[dict]):
    return lua.table_from([lua.table_from(entry) for entry in entries])


def test_addon_merges_the_written_file_with_its_own_sessions(tmp_path: Path, lua, session_log):
    addon = make_addon_dir(tmp_path)
    in_game = record(T0, steps=1000, distance_m=800.0, kcal=60.0, seconds=900.0, end=T0 + 930)
    outside = record(T0 + 20_000, steps=400, source="manual", beacon_session=None, avg_hr=None, max_hr=None)
    payload = build_payload([in_game, outside], profile={"weightKg": 75.0}, now=T0 + 30_000)
    lua.execute(write_data_file(addon, payload).read_text("ascii"))
    companion = lua.globals()[DATA_GLOBAL]

    own = lua_session_list(
        lua,
        [
            # The same walk as `in_game`, as the addon estimated it.
            {"id": 7, "start": T0 + 4, "end": T0 + 925, "seconds": 890, "distanceM": 760, "steps": 1100, "kcal": 55,
             "yards": 5200, "maxKmh": 3.3, "character": "Thrall-Draenor"},
            # A walk the companion never saw (it was not running).
            {"id": 8, "start": T0 + 50_000, "end": T0 + 50_600, "seconds": 600, "distanceM": 500, "steps": 950,
             "kcal": 40, "yards": 3000, "maxKmh": 3.2, "character": "Jaina-Proudmoore"},
        ],
    )  # fmt: skip
    merged = lua_to_py(session_log.merge(own, companion))
    assert [m["start"] for m in merged] == [T0 + 50_000, T0 + 20_000, T0]  # newest first

    addon_only, manual, shared = merged
    assert addon_only["measured"] is False and addon_only["character"] == "Jaina-Proudmoore"
    assert addon_only["steps"] == 950 and addon_only["yards"] == 3000

    assert manual["measured"] is True and manual["game"] == "manual" and manual["steps"] == 400
    assert manual.get("character") is None and manual.get("avgHr") is None

    # Shown once, with the treadmill's numbers and the game's context.
    assert shared["measured"] is True and shared["game"] == "wow"
    assert shared["steps"] == 1000 and shared["distanceM"] == 800 and shared["kcal"] == 60.0
    assert shared["seconds"] == 900 and shared["maxKmh"] == 3.22
    assert shared["avgHr"] == 101 and shared["maxHr"] == 118 and shared["stepsSource"] == "treadmill"
    assert shared["yards"] == 5200 and shared["character"] == "Thrall-Draenor"
    assert shared["end"] > shared["start"]


def test_addon_totals_match_the_companions(tmp_path: Path, lua, session_log, monkeypatch):
    """Same records, same clock: the in-game history adds up to what the
    dashboard shows - including sessions folded into ``older``."""
    monkeypatch.setattr(addon_sync, "MAX_SESSIONS_IN_FILE", 9)  # this week's walks listed, older ones folded
    addon = make_addon_dir(tmp_path)
    store = SessionStore(tmp_path / "data")
    rng = random.Random(41)
    noon = time.mktime(time.localtime(T0)[:3] + (12, 0, 0, 0, 0, -1))
    now = noon + 3 * 3600
    for days_ago in (0, 0, 1, 2, 2, 3, 5, 6, 9, 15, 30, 31):
        start = noon - days_ago * 86400 + rng.randrange(-3 * 3600, 2 * 3600)
        seconds = rng.randrange(300, 3000)
        store.add(
            record(
                start,
                end=start + seconds + 30,
                seconds=float(seconds),
                steps=rng.randrange(100, 5000),
                distance_m=float(rng.randrange(100, 4000)),
                kcal=float(rng.randrange(10, 300)),
            )
        )
    path = addon_sync.sync(addon, store, profile={})
    lua.execute(path.read_text("ascii"))
    companion = lua.globals()[DATA_GLOBAL]
    assert lua.eval(f"#{DATA_GLOBAL}.sessions") == 9 and companion.older.sessions == 3

    merged = session_log.merge(lua.table(), companion)
    lua_totals = lua_to_py(session_log.totals(merged, now, None, companion.older))
    python_totals = store.totals(now)
    for bucket in ("today", "week", "all"):
        for field, lua_field in (
            ("sessions", "sessions"),
            ("steps", "steps"),
            ("seconds", "seconds"),
            ("distance_m", "distanceM"),
            ("kcal", "kcal"),
        ):
            assert lua_totals[bucket][lua_field] == pytest.approx(python_totals[bucket][field], abs=0.11), (
                bucket,
                field,
            )
    assert lua_totals["streak"] == python_totals["streak"]
    assert [d["day"] for d in lua_totals["days"]] == [d["day"] for d in python_totals["days"]]
    assert [d["steps"] for d in lua_totals["days"]] == [d["steps"] for d in python_totals["days"]]


def test_addon_copes_with_the_untouched_stub_file(lua, session_log, addon_source):
    """Before the companion has ever run, CompanionData.lua sets the global to nil."""
    lua.execute((addon_source / DATA_FILE).read_text("utf-8"))
    assert lua.globals()[DATA_GLOBAL] is None
    merged = session_log.merge(lua.table(), None)
    assert lua_to_py(merged) == {}
    totals = lua_to_py(session_log.totals(merged, T0, None, None))
    assert totals["all"]["sessions"] == 0 and totals["streak"] == 0


# ---- finding the game -------------------------------------------------------------------------


def make_install(root: Path, *flavours: str) -> Path:
    for flavour in flavours:
        (root / flavour / "Interface" / "AddOns").mkdir(parents=True)
    return root


def test_find_addons_dirs_orders_by_flavour(tmp_path: Path):
    root = make_install(tmp_path / "World of Warcraft", "_classic_", "_retail_", "_forever_", "_ptr_")
    found = addon_sync.find_addons_dirs([root])
    assert [p.parent.parent.name for p in found] == ["_forever_", "_retail_", "_classic_"]
    assert all(p.name == "AddOns" and p.is_dir() for p in found)

    second = make_install(tmp_path / "Other Drive" / "World of Warcraft", "_forever_", "_classic_era_")
    found = addon_sync.find_addons_dirs([root, second, root])  # a duplicate root is harmless
    assert [p.parent.parent.name for p in found] == ["_forever_", "_forever_", "_retail_", "_classic_era_", "_classic_"]
    assert len(found) == len({str(p) for p in found}) == 5
    assert found[0].is_relative_to(root) and found[1].is_relative_to(second)  # stable within a flavour

    assert addon_sync.find_addons_dirs([tmp_path / "nothing here"]) == addon_sync.find_addons_dirs([])


def test_addons_dir_from_exe(tmp_path: Path):
    root = make_install(tmp_path / "World of Warcraft", "_forever_")
    exe = root / "_forever_" / "WowForever.exe"
    assert addon_sync.addons_dir_from_exe(exe) == root / "_forever_" / "Interface" / "AddOns"
    assert addon_sync.addons_dir_from_exe(str(exe)) == root / "_forever_" / "Interface" / "AddOns"
    assert addon_sync.addons_dir_from_exe(root / "_retail_" / "Wow.exe") is None  # no such install
    assert addon_sync.addons_dir_from_exe(None) is None
    assert addon_sync.addons_dir_from_exe("") is None


def test_addons_dir_from_a_macs_executable(tmp_path: Path):
    """On a Mac the executable is inside an application bundle, and the
    bundle is what sits in the game folder."""
    root = make_install(tmp_path / "World of Warcraft", "_retail_")
    exe = root / "_retail_" / "World of Warcraft.app" / "Contents" / "MacOS" / "World of Warcraft"
    assert addon_sync.game_dir_from_exe(exe) == root / "_retail_"
    assert addon_sync.game_dir_from_exe(str(exe)) == root / "_retail_"
    assert addon_sync.addons_dir_from_exe(exe) == root / "_retail_" / "Interface" / "AddOns"
    classic = root / "_classic_" / "World of Warcraft Classic.app" / "Contents" / "MacOS" / "World of Warcraft Classic"
    assert addon_sync.addons_dir_from_exe(classic) is None  # no such install
    # ... and on Windows it is in the game folder itself.
    assert addon_sync.game_dir_from_exe(root / "_forever_" / "WowForever.exe") == root / "_forever_"


@pytest.mark.skipif(sys.platform == "win32", reason="where the game is looked for off Windows")
def test_the_game_is_looked_for_where_a_mac_has_it():
    roots = addon_sync._common_roots()
    assert Path("/Applications/World of Warcraft") in roots
    assert Path.home() / "Applications" / "World of Warcraft" in roots
    assert addon_sync._registry_roots() == []


def test_find_addon_dir_precedence(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(addon_sync, "_common_roots", lambda: [])
    monkeypatch.setattr(addon_sync, "_registry_roots", lambda: [])
    root = make_install(tmp_path / "World of Warcraft", "_forever_", "_retail_")
    forever_addons = root / "_forever_" / "Interface" / "AddOns"
    retail_addons = root / "_retail_" / "Interface" / "AddOns"
    exe = str(root / "_forever_" / "WowForever.exe")

    assert addon_sync.find_addon_dir(None, exe) is None  # game found, addon not installed

    for addons in (forever_addons, retail_addons):
        (addons / "Walka").mkdir()
        (addons / "Walka" / "Walka.toc").write_text("## Title: Walka\n", "utf-8")

    # From the running game's executable.
    assert addon_sync.find_addon_dir(None, exe) == forever_addons / "Walka"
    assert addon_sync.find_addon_dir("", exe) == forever_addons / "Walka"
    # A configured folder wins - given as the addon folder or as the AddOns folder.
    assert addon_sync.find_addon_dir(str(retail_addons / "Walka"), exe) == retail_addons / "Walka"
    assert addon_sync.find_addon_dir(str(retail_addons), exe) == retail_addons / "Walka"
    # A configured folder that is wrong falls back to the game's.
    assert addon_sync.find_addon_dir(str(tmp_path / "gone"), exe) == forever_addons / "Walka"
    (tmp_path / "empty").mkdir()
    assert addon_sync.find_addon_dir(str(tmp_path / "empty"), exe) == forever_addons / "Walka"
    # Nothing to go on.
    assert addon_sync.find_addon_dir(None, None) is None

    # Known install locations are the last resort.
    monkeypatch.setattr(addon_sync, "_common_roots", lambda: [root])
    assert addon_sync.find_addon_dir(None, None) == forever_addons / "Walka"


def test_a_folder_named_walka_without_a_toc_is_not_the_addon(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(addon_sync, "_common_roots", lambda: [])
    monkeypatch.setattr(addon_sync, "_registry_roots", lambda: [])
    impostor = tmp_path / "Walka"
    impostor.mkdir()
    assert addon_sync.find_addon_dir(str(impostor), None) is None
    assert addon_sync.find_addon_dir(str(tmp_path), None) is None


# ---- installing ----------------------------------------------------------------------------------


def test_bundled_addon_is_found_in_a_source_checkout(addon_source):
    assert addon_sync.bundled_addon_dir() == addon_source
    assert (addon_source / "Walka.toc").exists()


def test_install_copies_the_addon(tmp_path: Path, addon_source):
    addons = tmp_path / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    installed = addon_sync.install_addon(addons, addon_source)
    assert installed == addons / "Walka"
    for relative in ("Walka.toc", "Walka.lua", "Beacon.lua", "Core/Protocol.lua", "Core/Metrics.lua", DATA_FILE):
        assert (installed / relative).read_bytes() == (addon_source / relative).read_bytes(), relative
    # Every file the .toc lists is there (or the game refuses to load the addon cleanly).
    for line in (installed / "Walka.toc").read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and (addon_source / line.replace("\\", "/")).exists():
            assert (installed / line.replace("\\", "/")).exists(), line
    assert addon_sync.find_addon_dir(str(addons), None) == installed


def test_install_preserves_existing_history_and_updates_the_code(tmp_path: Path, addon_source):
    addons = tmp_path / "Interface" / "AddOns"
    target = addons / "Walka"
    target.mkdir(parents=True)
    history = b"-- my walks\nWalkaCompanionData = { version = 1, sessions = {} }\n"
    (target / DATA_FILE).write_bytes(history)
    (target / "Walka.lua").write_text("-- an old version\n", "utf-8")
    (target / "MyNotes.txt").write_text("keep me\n", "utf-8")

    # Stray temp files in the source are not shipped.
    source = tmp_path / "src" / "Walka"
    import shutil

    shutil.copytree(addon_source, source)
    (source / "CompanionData.lua.tmp").write_text("half-written", "utf-8")

    addon_sync.install_addon(addons, source)
    assert (target / DATA_FILE).read_bytes() == history  # synced history survives a reinstall
    assert (target / "Walka.lua").read_bytes() == (addon_source / "Walka.lua").read_bytes()
    assert (target / "MyNotes.txt").read_text("utf-8") == "keep me\n"
    assert not list(target.rglob("*.tmp"))


def test_install_copies_addon_files_and_nothing_else(tmp_path: Path, addon_source):
    """Whatever else lies in the source folder stays out of the game. (In
    the first Windows builds the bundled addon folder "Walka" and the
    package's own "walka" were one and the same folder, and installing
    copied the dashboard's page and scripts into Interface\\AddOns.)"""
    import shutil

    source = tmp_path / "bundle" / "Walka"
    shutil.copytree(addon_source, source)
    (source / "static").mkdir()
    for name in ("index.html", "app.js", "app.css", "favicon.svg"):
        (source / "static" / name).write_text("not part of the addon", "utf-8")
    (source / "addon_sync.py").write_text("# nor is this", "utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "addon_sync.cpython-312.pyc").write_bytes(b"\x00")
    (source / "Core" / "notes.tmp").write_text("half-written", "utf-8")

    addons = tmp_path / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    installed = addon_sync.install_addon(addons, source)
    copied = sorted(path.relative_to(installed).as_posix() for path in installed.rglob("*"))
    expected = sorted(path.relative_to(addon_source).as_posix() for path in addon_source.rglob("*"))
    assert copied == expected  # the addon, whole - and no "static" folder, not even an empty one


def test_every_file_of_the_addon_is_of_a_kind_that_gets_installed(addon_source):
    """Guards the list of file types: a new kind of file added to the addon
    (a texture, a sound) must be added to ADDON_FILE_SUFFIXES, or installing
    would silently leave it out."""
    files = [path.relative_to(addon_source) for path in addon_source.rglob("*") if path.is_file()]
    assert files and sorted(files) == addon_sync.addon_files(addon_source)
    assert {".toc", ".lua", ".xml", ".tga", ".blp"} <= addon_sync.ADDON_FILE_SUFFIXES
    assert not {".tmp", ".py", ".pyc", ".html", ".js", ".css", ".svg", ".exe", ".dll"} & addon_sync.ADDON_FILE_SUFFIXES


def test_bundled_addon_in_the_packaged_app(tmp_path: Path, monkeypatch, addon_source):
    """Inside Walka.exe the addon lives under _addon/Walka - a place that
    cannot be the package's own folder on a file system that ignores case."""
    import shutil

    bundle = tmp_path / "_MEI12345"
    (bundle / "walka" / "static").mkdir(parents=True)
    (bundle / "walka" / "static" / "index.html").write_text("<html>", "utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert addon_sync.bundled_addon_dir() is None  # not bundled: say so, rather than search the disk

    shutil.copytree(addon_source, bundle / "_addon" / "Walka")
    assert addon_sync.bundled_addon_dir() == bundle / "_addon" / "Walka"
    assert addon_sync.BUNDLED_ADDON_PATH[0].lower() != "walka"

    # The old location is not looked at any more: on Windows it *is* the
    # package folder, which holds a Walka.toc only by accident of the build.
    (bundle / "Walka").mkdir(exist_ok=True)
    (bundle / "Walka" / "Walka.toc").write_text("## Interface: 16001\n", "utf-8")
    assert addon_sync.bundled_addon_dir() == bundle / "_addon" / "Walka"
    shutil.rmtree(bundle / "_addon")
    assert addon_sync.bundled_addon_dir() is None


def test_install_errors(tmp_path: Path, addon_source, monkeypatch):
    with pytest.raises(FileNotFoundError, match="not a folder"):
        addon_sync.install_addon(tmp_path / "missing" / "AddOns", addon_source)
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", "utf-8")
    with pytest.raises(FileNotFoundError):
        addon_sync.install_addon(a_file, addon_source)
    monkeypatch.setattr(addon_sync, "bundled_addon_dir", lambda: None)
    (tmp_path / "AddOns").mkdir()
    with pytest.raises(FileNotFoundError, match="not bundled"):
        addon_sync.install_addon(tmp_path / "AddOns")


def test_lua_helper_runtime_is_lua_5_1():
    lua = new_lua()
    assert lua.eval("_VERSION") == "Lua 5.1"  # the game's Lua
