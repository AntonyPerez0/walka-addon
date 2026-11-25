"""Configuration: defaults, bounds, tolerance for bad input."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from walka import config as config_module
from walka.config import _BOUNDS, CONFIG_FILE, Config, apply_update, load, save


def test_defaults():
    config = Config()
    assert config.units == "mph"
    assert config.profile.weight_kg == 75.0 and config.profile.height_cm == 172.0
    assert config.profile.age is None and config.profile.sex is None
    assert config.treadmill.kind == "ftms" and config.treadmill.units == "auto"
    assert config.treadmill.name_prefixes == ["URTM", "WELLFIT TM Linker"]
    assert config.treadmill.auto_connect is True
    assert config.heart_rate.enabled is False
    assert config.wow.origin is None and config.wow.addon_dir == "" and config.wow.capture_fps == 15.0
    assert config.web.host == "127.0.0.1" and config.web.port == 8787  # local only
    json.dumps(config.to_dict())


def test_there_is_nothing_to_set_about_the_belt():
    """No speed limit, no STOP hotkey, no manual speed, no disclaimer to
    accept before a belt is moved: the companion moves no belt."""
    assert set(Config().to_dict()) == {"units", "profile", "treadmill", "heart_rate", "wow", "web", "overlay"}
    assert set(Config().to_dict()["overlay"]) == {"enabled", "step_goal", "x", "y"}  # a window, and where it is
    assert set(Config().to_dict()["treadmill"]) == {"kind", "address", "name_prefixes", "units", "auto_connect"}
    assert {section for section, _name in _BOUNDS} == {"profile", "wow", "web", "overlay"}


def test_defaults_are_inside_their_own_bounds():
    config = Config()
    for (section, name), (lo, hi) in _BOUNDS.items():
        value = getattr(getattr(config, section), name)
        assert lo <= value <= hi, (section, name, value)


def test_instances_do_not_share_mutable_defaults():
    a, b = Config(), Config()
    a.treadmill.name_prefixes.append("X")
    a.profile.weight_kg = 99.0
    assert b.treadmill.name_prefixes == ["URTM", "WELLFIT TM Linker"] and b.profile.weight_kg != 99.0


@pytest.mark.parametrize("section,name", sorted(_BOUNDS))
def test_every_bounded_field_is_clamped(section: str, name: str):
    lo, hi = _BOUNDS[(section, name)]
    for sent, expected in ((lo - 1000, lo), (hi + 1000, hi), (-1e12, lo), (1e12, hi)):
        config = Config()
        apply_update(config, {section: {name: sent}})
        value = getattr(getattr(config, section), name)
        assert value == pytest.approx(expected), (sent, value)
        assert isinstance(value, type(getattr(getattr(Config(), section), name)))
    inside = (lo + hi) / 2
    config = Config()
    apply_update(config, {section: {name: inside}})
    assert getattr(getattr(config, section), name) == pytest.approx(inside, abs=1)


def test_a_typo_cannot_put_nonsense_into_the_numbers():
    config = apply_update(
        Config(),
        {"profile": {"weight_kg": 7500, "height_cm": 1.72, "step_calibration": 100}, "wow": {"capture_fps": 0}},
    )
    assert config.profile.weight_kg == 250.0 and config.profile.height_cm == 120.0
    assert config.profile.step_calibration == _BOUNDS[("profile", "step_calibration")][1]
    assert config.wow.capture_fps == 2.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_made_safe(bad: float):
    config = apply_update(
        Config(),
        {"profile": {"weight_kg": bad, "height_cm": bad}, "wow": {"capture_fps": bad}, "web": {"port": bad}},
    )
    lo, hi = _BOUNDS[("profile", "weight_kg")]
    assert lo <= config.profile.weight_kg <= hi
    assert 120.0 <= config.profile.height_cm <= 230.0
    assert 2.0 <= config.wow.capture_fps <= 60.0
    assert isinstance(config.web.port, int) and 1 <= config.web.port <= 65535


@pytest.mark.parametrize(
    "patch",
    [
        {"profile": {"weight_kg": "heavy"}},
        {"profile": {"weight_kg": [80.0]}},
        {"profile": {"weight_kg": {"kg": 80}}},
        {"profile": "not a table"},
        {"profile": None},
        {"profile": [1, 2, 3]},
        # What a version that drove the belt kept in the same file: not read.
        {"limits": {"max_speed_kmh": 4.0, "safe_speed_kmh": 1.3}},
        {"manual": {"speed_kmh": 3.2}},
        {"terms": {"accepted_version": 1}},
        {"stop_hotkey": "ctrl+alt+x"},
        {"treadmill": {"stop_on_exit": False}},
        {"web": {"port": "eighty"}},
        {"web": {"port": [8787]}},
        {"treadmill": 5},
        {"units": None},
    ],
)
def test_bad_types_are_ignored_and_leave_the_defaults(patch: dict):
    config = apply_update(Config(), patch)
    assert config.to_dict() == Config().to_dict()


def test_numeric_strings_and_ints_are_coerced():
    config = apply_update(
        Config(),
        {"wow": {"capture_fps": "20.5"}, "web": {"port": "9000"}, "profile": {"weight_kg": 80, "height_cm": "181"}},
    )
    assert config.wow.capture_fps == 20.5 and isinstance(config.wow.capture_fps, float)
    assert config.profile.height_cm == 181.0 and isinstance(config.profile.height_cm, float)
    assert config.web.port == 9000 and isinstance(config.web.port, int)
    assert config.profile.weight_kg == 80.0 and isinstance(config.profile.weight_kg, float)


@pytest.mark.parametrize(
    "value,expected",
    [(False, False), (True, True), (0, False), (1, True), ("false", False), ("False", False), ("no", False),
     ("off", False), ("0", False), ("true", True), ("YES", True), (" on ", True)],
)  # fmt: skip
def test_booleans_are_read_as_meant(value, expected: bool):
    """bool("false") is True in Python - a hand-edited file must not fall for that."""
    for start in (True, False):
        config = Config()
        config.treadmill.auto_connect = start
        config.web.open_browser = start
        apply_update(config, {"treadmill": {"auto_connect": value}, "web": {"open_browser": value}})
        assert config.treadmill.auto_connect is expected
        assert config.web.open_browser is expected


@pytest.mark.parametrize("value", ["maybe", "", 2, -1, 0.5, [], [False], {}, "nope"])
def test_unreadable_booleans_keep_the_default(value):
    config = apply_update(Config(), {"treadmill": {"auto_connect": value}, "web": {"open_browser": value}})
    assert config.treadmill.auto_connect is True
    assert config.web.open_browser is True


def test_unknown_keys_are_ignored():
    config = apply_update(
        Config(),
        {
            "surprise": 1,
            "wow": {"capture_fps": 20.0, "warp_factor": 9},
            "profile": {"shoe_size": 44},
            "new_section": {"a": 1},
        },
    )
    assert config.wow.capture_fps == 20.0
    data = config.to_dict()
    assert "surprise" not in data and "new_section" not in data
    assert "warp_factor" not in data["wow"] and "shoe_size" not in data["profile"]


def test_enumerated_settings_fall_back_when_invalid():
    config = apply_update(Config(), {"units": "furlongs", "treadmill": {"kind": "rocket", "units": "knots"}})
    assert config.units == "mph" and config.treadmill.kind == "ftms" and config.treadmill.units == "auto"
    config = apply_update(Config(), {"units": "kmh", "treadmill": {"kind": "simulated", "units": "mph"}})
    assert config.units == "kmh" and config.treadmill.kind == "simulated" and config.treadmill.units == "mph"


def test_partial_updates_leave_everything_else_alone():
    config = Config()
    apply_update(config, {"profile": {"weight_kg": 82.5}})
    apply_update(config, {"wow": {"capture_fps": 30.0}})
    apply_update(config, {})
    assert config.profile.weight_kg == 82.5 and config.profile.height_cm == 172.0
    assert config.wow.capture_fps == 30.0 and config.wow.origin is None


def test_update_mutates_the_profile_object_in_place():
    """The recorder is handed ``config.profile`` on every tick: a settings
    change must reach it, not a copy."""
    config = Config()
    profile = config.profile
    apply_update(config, {"profile": {"weight_kg": 90}})
    assert config.profile is profile and profile.weight_kg == 90.0


# ---- optional fields ----------------------------------------------------------------


def test_age_and_sex_can_be_set_and_cleared():
    config = Config()
    apply_update(config, {"profile": {"age": 41, "sex": "Female"}})
    assert config.profile.age == 41 and config.profile.sex == "female"
    apply_update(config, {"profile": {"age": "38", "sex": "MALE"}})
    assert config.profile.age == 38 and config.profile.sex == "male"
    apply_update(config, {"profile": {"age": None, "sex": None}})
    assert config.profile.age is None and config.profile.sex is None


@pytest.mark.parametrize("age", [0, 4, 12, 17, 111, 1000, -30, "old", [40], float("nan"), float("inf")])
def test_implausible_ages_and_those_of_children_are_refused(age):
    config = apply_update(Config(), {"profile": {"age": age}})
    assert config.profile.age is None
    config = apply_update(Config(), {"profile": {"age": 40}})
    apply_update(config, {"profile": {"age": age}})
    assert config.profile.age == 40  # a bad value does not wipe a good one


@pytest.mark.parametrize("sex", ["other", "", "m", 1, ["male"]])
def test_unknown_sex_values_are_refused(sex):
    config = apply_update(Config(), {"profile": {"sex": sex}})
    assert config.profile.sex is None
    config = apply_update(Config(), {"profile": {"sex": "male"}})
    apply_update(config, {"profile": {"sex": sex}})
    assert config.profile.sex == "male"


def test_capture_origin_can_be_set_and_cleared():
    config = Config()
    apply_update(config, {"wow": {"origin": [1920, 0]}})
    assert config.wow.origin == [1920, 0]
    apply_update(config, {"wow": {"origin": [-1280, 300]}})  # a monitor to the left
    assert config.wow.origin == [-1280, 300]
    apply_update(config, {"wow": {"origin": None}})
    assert config.wow.origin is None


@pytest.mark.parametrize(
    "origin", ["0,0", 5, [1], [1, 2, 3], ["a", "b"], {"x": 1, "y": 2}, [None, None], [1.5e400, 0], True]
)
def test_malformed_capture_origin_is_refused(origin):
    """It ends up as screen coordinates for the capture thread: anything but
    two numbers would break (or silently disable) reading the beacon."""
    config = apply_update(Config(), {"wow": {"origin": origin}})
    assert config.wow.origin is None
    config = apply_update(Config(), {"wow": {"origin": [10, 20]}})
    apply_update(config, {"wow": {"origin": origin}})
    assert config.wow.origin == [10, 20]


def test_capture_origin_is_stored_as_whole_pixels():
    config = apply_update(Config(), {"wow": {"origin": [10.0, "20"]}})
    assert config.wow.origin == [10, 20] and all(isinstance(v, int) for v in config.wow.origin)


@pytest.mark.parametrize("prefixes", ["URTM", 5, [1, 2], [["URTM"]], {"a": 1}, [None]])
def test_malformed_name_prefixes_are_refused(prefixes):
    """A bare string would be taken apart into letters - and the companion
    would try to connect to anything whose name starts with U, R, T or M."""
    config = apply_update(Config(), {"treadmill": {"name_prefixes": prefixes}})
    assert config.treadmill.name_prefixes == ["URTM", "WELLFIT TM Linker"]


def test_name_prefixes_can_be_changed():
    config = apply_update(Config(), {"treadmill": {"name_prefixes": ["URTM", "KS-"]}})
    assert config.treadmill.name_prefixes == ["URTM", "KS-"]
    config = apply_update(Config(), {"treadmill": {"name_prefixes": []}})
    assert config.treadmill.name_prefixes == []  # FTMS advertisers only


def test_a_settings_file_from_before_other_makes_were_known_gets_todays_names(tmp_path: Path):
    """The file holds every setting, so one written when UREVO was the only
    known make pins ["URTM"] - and a WELLFIT would never be recognised."""
    (tmp_path / CONFIG_FILE).write_text(json.dumps({"treadmill": {"name_prefixes": ["URTM"]}}), "utf-8")
    assert load(tmp_path).treadmill.name_prefixes == ["URTM", "WELLFIT TM Linker"]
    # A list somebody made themselves is theirs - an empty one too.
    for own in (["URTM", "KS-"], ["KS-"], []):
        (tmp_path / CONFIG_FILE).write_text(json.dumps({"treadmill": {"name_prefixes": own}}), "utf-8")
        assert load(tmp_path).treadmill.name_prefixes == own


# ---- load / save ----------------------------------------------------------------------


def test_missing_file_gives_defaults(tmp_path: Path):
    assert load(tmp_path).to_dict() == Config().to_dict()
    assert load(tmp_path / "does" / "not" / "exist").to_dict() == Config().to_dict()


def test_save_load_round_trip(tmp_path: Path):
    config = Config()
    apply_update(
        config,
        {
            "units": "kmh",
            "profile": {"weight_kg": 68.5, "height_cm": 181.0, "age": 33, "sex": "female"},
            "treadmill": {"address": "AA:BB:CC:DD:EE:FF", "units": "mph", "auto_connect": False},
            "heart_rate": {"enabled": True, "address": "11:22", "name": "HRM-Dual"},
            "wow": {"addon_dir": r"C:\Games\WoW\_forever_\Interface\AddOns\Walka", "origin": [100, 50]},
            "web": {"port": 9123, "open_browser": False},
        },
    )
    path = save(config, tmp_path)
    assert path == tmp_path / CONFIG_FILE
    assert not list(tmp_path.glob("*.tmp"))
    assert load(tmp_path).to_dict() == config.to_dict()
    # Saving creates the folder if needed and overwrites atomically.
    nested = tmp_path / "a" / "b"
    save(config, nested)
    save(Config(), nested)
    assert load(nested).to_dict() == Config().to_dict()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{ not json",
        b"[1, 2, 3]",
        b'"just a string"',
        b"null",
        b"\xff\xfe\x00\x01 binary",
        b'{"profile": {"weight_kg": ',
    ],
)
def test_corrupt_file_gives_defaults(tmp_path: Path, content: bytes):
    (tmp_path / CONFIG_FILE).write_bytes(content)
    assert load(tmp_path).to_dict() == Config().to_dict()


def test_hand_edited_file_with_out_of_range_and_junk_values(tmp_path: Path):
    (tmp_path / CONFIG_FILE).write_text(
        json.dumps(
            {
                "units": "kmh",
                "limits": {"max_speed_kmh": 99, "accel_kmh_per_s": "quick", "stall_s": 1},
                "profile": {"weight_kg": 900, "height_cm": "tall", "age": 300},
                "web": {"port": 1e400, "host": 12345},
                "wow": {"capture_fps": 1000, "origin": "top-left"},
                "treadmill": {"name_prefixes": "URTM"},
                "obsolete": {"x": 1},
            }
        ).replace("1e+400", "1e400"),
        "utf-8",
    )
    config = load(tmp_path)  # must not raise: a typo cannot stop the companion from starting
    assert config.units == "kmh"
    assert not hasattr(config, "limits")  # (from a version that drove the belt: not read)
    assert config.profile.weight_kg == 250.0 and config.profile.height_cm == 172.0 and config.profile.age is None
    assert isinstance(config.web.port, int) and 1 <= config.web.port <= 65535
    assert config.web.host == "127.0.0.1", "anything that is not this computer itself is refused"
    assert config.wow.capture_fps == 60.0 and config.wow.origin is None
    assert config.treadmill.name_prefixes == ["URTM", "WELLFIT TM Linker"]


def test_on_a_mac_the_data_is_kept_where_mac_programs_keep_theirs(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WALKA_DATA_DIR", raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "darwin")
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: tmp_path))
    assert config_module.default_data_dir() == tmp_path / "Library" / "Application Support" / "Walka"


def test_data_dir_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WALKA_DATA_DIR", str(tmp_path / "custom"))
    assert config_module.default_data_dir() == tmp_path / "custom"
    monkeypatch.delenv("WALKA_DATA_DIR")
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert config_module.default_data_dir() == tmp_path / "Roaming" / "Walka"
    elif sys.platform == "darwin":
        assert config_module.default_data_dir() == Path.home() / "Library" / "Application Support" / "Walka"
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert config_module.default_data_dir() == tmp_path / "xdg" / "walka"
        monkeypatch.delenv("XDG_CONFIG_HOME")
        assert config_module.default_data_dir() == Path.home() / ".config" / "walka"


@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.1.20", "::", "example.com", 12345, None])
def test_the_dashboard_is_only_ever_served_to_this_computer(host):
    """It has no password: bound to the network it would show the walker's
    walks, weight and height to every device on the Wi-Fi, and let any of
    them change the settings."""
    config = Config()
    apply_update(config, {"web": {"host": host}})
    assert config.web.host == "127.0.0.1"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_hosts_are_kept(host):
    config = Config()
    apply_update(config, {"web": {"host": host}})
    assert config.web.host == host


def test_eighteen_is_the_youngest_age_the_profile_takes():
    """Walka is for adults: the disclaimer says so, and the profile agrees."""
    assert apply_update(Config(), {"profile": {"age": 18}}).profile.age == 18
    assert apply_update(Config(), {"profile": {"age": 17}}).profile.age is None
