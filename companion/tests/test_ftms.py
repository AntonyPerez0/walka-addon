"""The FTMS codec against hand-built packets."""

from __future__ import annotations

import itertools
import struct

import pytest

from walka import ftms, probe
from walka.ftms import (
    MachineStatus,
    Op,
    ResultCode,
    StatusOp,
    TreadmillData,
    build_treadmill_data,
    parse_control_response,
    parse_features,
    parse_heart_rate,
    parse_machine_status,
    parse_rsc_measurement,
    parse_supported_speed_range,
    parse_treadmill_data,
)
from walka.hr import FRESH_S, HeartRateMonitor

# Treadmill Data optional fields in wire order: flag bit, struct format,
# (attribute, raw value, decoded value) for each value in the field.
# Written from the GATT Specification Supplement, independently of the parser.
OPTIONAL_FIELDS = [
    (0x0002, "<H", [("average_speed_kmh", 287, 2.87)]),
    (0x0004, "u24", [("total_distance_m", 70_123, 70_123)]),
    (0x0008, "<hh", [("inclination_pct", -15, -1.5), ("ramp_angle_deg", 23, 2.3)]),
    (0x0010, "<HH", [("positive_elevation_gain_m", 105, 10.5), ("negative_elevation_gain_m", 7, 0.7)]),
    (0x0020, "<B", [("instantaneous_pace_km_per_min", 62, 6.2)]),
    (0x0040, "<B", [("average_pace_km_per_min", 58, 5.8)]),
    (
        0x0080,
        "<HHB",
        [("total_energy_kcal", 345, 345), ("energy_per_hour_kcal", 410, 410), ("energy_per_minute_kcal", 6, 6)],
    ),
    (0x0100, "<B", [("heart_rate_bpm", 117, 117)]),
    (0x0200, "<B", [("metabolic_equivalent", 34, 3.4)]),
    (0x0400, "<H", [("elapsed_time_s", 3599, 3599)]),
    (0x0800, "<H", [("remaining_time_s", 61, 61)]),
    (0x1000, "<hh", [("force_on_belt_n", -12, -12), ("power_output_w", 150, 150)]),
]
ALL_ATTRS = ["speed_kmh"] + [name for _, _, values in OPTIONAL_FIELDS for name, _, _ in values] + ["vendor_steps"]


def encode(flag_bits: int, *, speed_raw: int | None, steps: int | None = None) -> tuple[bytes, dict]:
    """Reference encoder: returns the packet and the values it should decode to."""
    flags = flag_bits
    body = b""
    expected: dict = dict.fromkeys(ALL_ATTRS)
    if speed_raw is None:
        flags |= 0x0001  # "More Data": no instantaneous speed in this packet
    else:
        body += struct.pack("<H", speed_raw)
        expected["speed_kmh"] = speed_raw / 100
    for bit, fmt, values in OPTIONAL_FIELDS:
        if not flag_bits & bit:
            continue
        raws = [raw for _, raw, _ in values]
        body += raws[0].to_bytes(3, "little") if fmt == "u24" else struct.pack(fmt, *raws)
        for name, _, decoded in values:
            expected[name] = decoded
    if steps is not None:
        flags |= 0x2000
        body += steps.to_bytes(3, "little")
        expected["vendor_steps"] = steps
    return struct.pack("<H", flags) + body, expected


def assert_decodes(packet: bytes, expected: dict) -> TreadmillData:
    data = parse_treadmill_data(packet)
    for name in ALL_ATTRS:
        value = getattr(data, name)
        if expected[name] is None:
            assert value is None, f"{name} should be absent, got {value!r} ({packet.hex()})"
        else:
            assert value == pytest.approx(expected[name]), f"{name} ({packet.hex()})"
    assert not data.truncated
    assert data.flags == int.from_bytes(packet[:2], "little")
    return data


# ---- Treadmill Data -----------------------------------------------------------------


def test_urevo_packet_layout_0x0484():
    """Speed, total distance (u24), expended energy, elapsed time - as sent by UREVO pads."""
    packet = bytes.fromhex("84044101d204003800fffffff202")
    data = parse_treadmill_data(packet)
    assert data.flags == 0x0484
    assert data.speed_kmh == pytest.approx(3.21)
    assert data.total_distance_m == 1234
    assert data.total_energy_kcal == 56
    assert data.energy_per_hour_kcal is None  # 0xFFFF = "not available"
    assert data.energy_per_minute_kcal is None  # 0xFF = "not available"
    assert data.elapsed_time_s == 754
    assert data.vendor_steps is None and data.heart_rate_bpm is None and data.average_speed_kmh is None
    assert not data.truncated


def test_urevo_packet_with_trailing_step_counter():
    packet = bytes.fromhex("842460000a00000100ffffff1e0039 30 01".replace(" ", ""))
    data = parse_treadmill_data(packet)
    assert data.flags == 0x2484
    assert data.speed_kmh == pytest.approx(0.96)
    assert data.total_distance_m == 10 and data.total_energy_kcal == 1 and data.elapsed_time_s == 30
    assert data.vendor_steps == 0x013039  # 77,881
    assert not data.truncated


def test_idle_pad_packet():
    data = parse_treadmill_data(bytes.fromhex("840400000000000000ffffff0000"))
    assert data.speed_kmh == 0 and data.total_distance_m == 0 and data.total_energy_kcal == 0
    assert data.elapsed_time_s == 0


def test_more_data_bit_is_inverted():
    # Bit 0 clear: speed present.
    assert parse_treadmill_data(bytes.fromhex("0000c800")).speed_kmh == pytest.approx(2.0)
    # Bit 0 set: no speed field - the next field starts right after the flags.
    data = parse_treadmill_data(bytes.fromhex("0500e80300"))
    assert data.speed_kmh is None
    assert data.total_distance_m == 1000
    assert not data.truncated
    # A continuation packet carrying only elapsed time.
    data = parse_treadmill_data(bytes.fromhex("01043c00"))
    assert data.speed_kmh is None and data.elapsed_time_s == 60


def test_every_combination_of_standard_fields():
    """All 4096 combinations of the twelve optional fields, with and without
    speed: every field must land on the right bytes."""
    bits = [bit for bit, _, _ in OPTIONAL_FIELDS]
    for count in range(len(bits) + 1):
        for combo in itertools.combinations(bits, count):
            flag_bits = sum(combo)
            for speed_raw in (None, 643):
                packet, expected = encode(flag_bits, speed_raw=speed_raw)
                assert_decodes(packet, expected)


def test_vendor_steps_after_any_combination_of_fields():
    for flag_bits in (0, 0x0004, 0x0484, 0x0584, 0x1FFE, 0x0400, 0x0080):
        for speed_raw in (None, 96):
            packet, expected = encode(flag_bits, speed_raw=speed_raw, steps=123_456)
            assert_decodes(packet, expected)


def test_vendor_step_flag_without_the_bytes_is_not_misread():
    packet, expected = encode(0x0484, speed_raw=321)
    flagged = struct.pack("<H", 0x2484) + packet[2:]
    data = parse_treadmill_data(flagged)
    assert data.vendor_steps is None
    assert data.elapsed_time_s == expected["elapsed_time_s"]
    # One or two stray bytes are not a 24-bit counter either.
    assert parse_treadmill_data(flagged + b"\x07").vendor_steps is None
    assert parse_treadmill_data(flagged + b"\x07\x08").vendor_steps is None


def test_trailing_bytes_without_the_vendor_flag_are_ignored():
    packet, expected = encode(0x0484, speed_raw=321)
    data = assert_decodes(packet + b"\x39\x30\x01\xaa\xbb", expected)
    assert data.vendor_steps is None


def test_not_available_markers():
    data = parse_treadmill_data(struct.pack("<HHHHB", 0x0080, 100, 0xFFFF, 0xFFFF, 0xFF))
    assert data.total_energy_kcal is None and data.energy_per_hour_kcal is None
    assert data.energy_per_minute_kcal is None
    data = parse_treadmill_data(struct.pack("<HHHHB", 0x0080, 100, 0, 0, 0))
    assert data.total_energy_kcal == 0 and data.energy_per_hour_kcal == 0 and data.energy_per_minute_kcal == 0
    data = parse_treadmill_data(struct.pack("<HHHHB", 0x0080, 100, 0xFFFE, 1, 0xFE))
    assert data.total_energy_kcal == 0xFFFE and data.energy_per_minute_kcal == 0xFE


def test_extreme_values():
    data = parse_treadmill_data(struct.pack("<HH", 0x0004, 0xFFFF) + b"\xff\xff\xff")
    assert data.speed_kmh == pytest.approx(655.35)
    assert data.total_distance_m == 0xFFFFFF
    data = parse_treadmill_data(struct.pack("<HHhh", 0x0008, 0, -32768, 32767))
    assert data.inclination_pct == pytest.approx(-3276.8) and data.ramp_angle_deg == pytest.approx(3276.7)


@pytest.mark.parametrize("flag_bits,steps", [(0x0484, None), (0x0484, 4321), (0x1FFE, None), (0x1FFE, 99)])
def test_truncated_packets_never_raise_and_never_invent_values(flag_bits: int, steps: int | None):
    packet, expected = encode(flag_bits, speed_raw=321, steps=steps)
    full = parse_treadmill_data(packet)
    for length in range(len(packet)):
        data = parse_treadmill_data(packet[:length])
        if length < 2:
            assert data.truncated and data.flags == 0
            assert all(getattr(data, name) is None for name in ALL_ATTRS)
            continue
        # Whatever is reported must be what the complete packet says.
        for name in ALL_ATTRS:
            value = getattr(data, name)
            assert value is None or value == getattr(full, name), (name, length)
        if steps is not None and length >= len(packet) - 3:
            # Only the vendor trailer is cut: the standard fields are complete.
            assert not data.truncated and data.vendor_steps is None
        else:
            assert data.truncated, length
            assert data.vendor_steps is None


def test_garbage_never_raises():
    import random

    rng = random.Random(21)
    for _ in range(3000):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
        parse_treadmill_data(blob)
        parse_machine_status(blob)
        parse_control_response(blob)
        parse_heart_rate(blob)
        parse_rsc_measurement(blob)
        parse_supported_speed_range(blob)
        parse_features(blob)
        probe.describe_value(ftms.TREADMILL_DATA, blob)
        probe.describe_value(ftms.FITNESS_MACHINE_STATUS, blob)


def test_merge_keeps_old_fields_and_takes_new_ones():
    first = parse_treadmill_data(bytes.fromhex("84044101d204003800fffffff202"))
    second = parse_treadmill_data(bytes.fromhex("01015a"))  # continuation: heart rate only
    merged = first.merge(second)
    assert merged.speed_kmh == pytest.approx(3.21) and merged.total_distance_m == 1234
    assert merged.heart_rate_bpm == 90
    assert merged.flags == 0x0101
    assert first.heart_rate_bpm is None  # merge does not mutate
    third = parse_treadmill_data(bytes.fromhex("00000000"))
    assert merged.merge(third).speed_kmh == 0.0  # zero is a value, not "absent"
    cut = parse_treadmill_data(bytes.fromhex("840441"))
    assert merged.merge(cut).truncated and merged.merge(cut).speed_kmh == pytest.approx(3.21)


def test_build_parse_round_trip():
    cases = [
        {},
        {"speed_kmh": 0.0},
        {"speed_kmh": 3.22},
        {"speed_kmh": 6.43, "total_distance_m": 5000, "total_energy_kcal": 250, "elapsed_time_s": 3600},
        {"speed_kmh": 1.0, "heart_rate_bpm": 99},
        {"speed_kmh": 2.0, "total_distance_m": 0, "vendor_steps": 0},
        {"total_distance_m": 12, "vendor_steps": 77},
        {
            "speed_kmh": 4.83,
            "total_distance_m": 0xFFFFFF,
            "total_energy_kcal": 0xFFFE,
            "elapsed_time_s": 0xFFFF,
            "heart_rate_bpm": 255,
            "vendor_steps": 0xFFFFFF,
        },
    ]
    for fields in cases:
        packet = build_treadmill_data(fields.get("speed_kmh"), **{k: v for k, v in fields.items() if k != "speed_kmh"})
        data = parse_treadmill_data(packet)
        assert not data.truncated
        for name in ("speed_kmh", "total_distance_m", "total_energy_kcal", "elapsed_time_s", "heart_rate_bpm"):
            want = fields.get(name)
            assert getattr(data, name) == (pytest.approx(want) if want is not None else None), (name, fields)
        assert data.vendor_steps == fields.get("vendor_steps")
    # The UREVO layout comes out with the UREVO flags.
    urevo = build_treadmill_data(3.21, total_distance_m=1234, total_energy_kcal=56, elapsed_time_s=754)
    assert urevo == bytes.fromhex("84044101d204003800fffffff202")


def test_build_clamps_out_of_range_values():
    data = parse_treadmill_data(
        build_treadmill_data(
            -5.0, total_distance_m=-1, total_energy_kcal=10**9, elapsed_time_s=-4, heart_rate_bpm=999, vendor_steps=-1
        )
    )
    assert data.speed_kmh == 0 and data.total_distance_m == 0 and data.elapsed_time_s == 0
    assert data.total_energy_kcal == 0xFFFE and data.heart_rate_bpm == 255 and data.vendor_steps == 0
    assert parse_treadmill_data(build_treadmill_data(1e6)).speed_kmh == pytest.approx(655.35)


# ---- speed range, features -------------------------------------------------------------


def test_supported_speed_range():
    urevo = parse_supported_speed_range(bytes.fromhex("600083021000"))
    assert urevo == ftms.SpeedRange(0.96, 6.43, 0.16)
    metric = parse_supported_speed_range(struct.pack("<HHH", 100, 600, 10))
    assert metric is not None and (metric.min_kmh, metric.max_kmh, metric.step_kmh) == (1.0, 6.0, 0.1)
    assert parse_supported_speed_range(struct.pack("<HHH", 100, 600, 10) + b"extra") == metric
    assert parse_supported_speed_range(b"\x60\x00\x83\x02\x10") is None
    assert parse_supported_speed_range(b"") is None


def test_features():
    features = parse_features(struct.pack("<II", 0x0040 | 0x0004, 0x0001))
    assert features is not None
    assert features.speed_target_supported and features.step_count_supported
    plain = parse_features(struct.pack("<II", 0x0004, 0x0002))
    assert plain is not None and not plain.speed_target_supported and not plain.step_count_supported
    assert parse_features(b"\x00" * 7) is None


def test_uuids():
    assert ftms.uuid16(0x1826) == "00001826-0000-1000-8000-00805f9b34fb"
    assert ftms.FTMS_SERVICE == "00001826-0000-1000-8000-00805f9b34fb"
    assert ftms.TREADMILL_DATA.startswith("00002acd-")
    assert ftms.CONTROL_POINT.startswith("00002ad9-")
    assert ftms.FITNESS_MACHINE_STATUS.startswith("00002ada-")
    assert ftms.SUPPORTED_SPEED_RANGE.startswith("00002ad4-")
    assert ftms.HEART_RATE_MEASUREMENT.startswith("00002a37-")


# ---- control point ------------------------------------------------------------------------


def test_command_bytes():
    assert ftms.cmd_request_control() == b"\x00"
    assert ftms.cmd_reset() == b"\x01"
    assert ftms.cmd_start_or_resume() == b"\x07"
    assert ftms.cmd_stop() == b"\x08\x01"
    assert ftms.cmd_pause() == b"\x08\x02"
    assert ftms.cmd_stop() != ftms.cmd_pause()


@pytest.mark.parametrize(
    "kmh,payload",
    [
        (3.3, "024a01"),
        (0.96, "026000"),
        (1.0, "026400"),
        (6.5, "028a02"),
        (3.218688, "024201"),  # rounds to 3.22
        (0.0, "020000"),
        (-2.0, "020000"),
        (655.35, "02ffff"),
        (9999.0, "02ffff"),
    ],
)
def test_set_target_speed_encoding(kmh: float, payload: str):
    assert ftms.cmd_set_target_speed(kmh).hex() == payload


def test_set_target_speed_is_exact_on_the_tenth_grid():
    """Every 0.1 km/h value the driver sends must arrive as exactly that."""
    for tenths in range(0, 200):
        payload = ftms.cmd_set_target_speed(tenths / 10.0)
        assert payload[0] == 0x02 and int.from_bytes(payload[1:], "little") == tenths * 10


def test_control_responses():
    ok = parse_control_response(bytes([0x80, 0x07, 0x01]))
    assert ok is not None and ok.ok and ok.request_op == Op.START_OR_RESUME and ok.result == ResultCode.SUCCESS
    assert ok.describe() == "START_OR_RESUME: SUCCESS"
    for result in (0x02, 0x03, 0x04, 0x05):
        response = parse_control_response(bytes([0x80, 0x02, result]))
        assert response is not None and not response.ok and response.request_op == Op.SET_TARGET_SPEED
    denied = parse_control_response(bytes([0x80, 0x08, 0x05, 0xAA]))
    assert denied is not None and denied.result == ResultCode.CONTROL_NOT_PERMITTED
    assert denied.describe() == "STOP_OR_PAUSE: CONTROL_NOT_PERMITTED"
    odd = parse_control_response(bytes([0x80, 0x42, 0x99]))
    assert odd is not None and not odd.ok and odd.describe() == "0x42: 0x99"
    assert parse_control_response(bytes([0x80, 0x07])) is None
    assert parse_control_response(bytes([0x07, 0x07, 0x01])) is None
    assert parse_control_response(b"") is None


# ---- machine status -------------------------------------------------------------------------


def status(*payload: int) -> MachineStatus:
    parsed = parse_machine_status(bytes(payload))
    assert parsed is not None
    return parsed


def test_machine_status_stop_pause_start():
    stopped = status(0x02, 0x01)
    assert stopped.stopped and not stopped.paused and not stopped.started
    paused = status(0x02, 0x02)
    assert paused.paused and not paused.stopped and not paused.started
    # No parameter at all: treat as a stop (the conservative reading).
    bare = status(0x02)
    assert bare.stopped and not bare.paused
    key = status(0x03)
    assert key.stopped and not key.paused and not key.started
    started = status(0x04)
    assert started.started and not started.stopped and not started.paused
    reset = status(0x01)
    assert not reset.stopped and not reset.paused and not reset.started
    lost = status(0xFF)
    assert lost.op == StatusOp.CONTROL_PERMISSION_LOST and not lost.stopped and not lost.started


def test_machine_status_target_speed_changed():
    changed = status(0x05, 0x4A, 0x01)
    assert changed.target_speed_kmh == pytest.approx(3.3)
    assert not changed.stopped and not changed.paused and not changed.started
    assert changed.describe() == "TARGET_SPEED_CHANGED (3.30 km/h)"
    assert status(0x05, 0x4A).target_speed_kmh is None  # too short to carry a speed
    assert status(0x05).target_speed_kmh is None
    # Parameters of other events are never read as a speed.
    assert status(0x06, 0x4A, 0x01).target_speed_kmh is None
    assert status(0x02, 0x02, 0x01).target_speed_kmh is None


def test_machine_status_describe_and_edge_cases():
    assert parse_machine_status(b"") is None
    assert status(0x02, 0x01).describe() == "STOPPED_OR_PAUSED_BY_USER (01)"
    assert status(0x04).describe() == "STARTED_OR_RESUMED_BY_USER"
    assert status(0x77, 0x01).describe() == "0x77 (01)"
    unknown = status(0x77)
    assert not unknown.stopped and not unknown.paused and not unknown.started


# ---- heart rate, RSC --------------------------------------------------------------------------


def test_heart_rate_8_and_16_bit():
    assert parse_heart_rate(bytes([0x00, 72])) == 72
    assert parse_heart_rate(bytes([0x06, 141, 0x12, 0x34])) == 141  # contact bits + RR interval ignored
    assert parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300
    assert parse_heart_rate(bytes([0x11, 0x50, 0x00, 0xAA, 0xBB])) == 80
    assert parse_heart_rate(bytes([0x01, 0x50])) is None
    assert parse_heart_rate(bytes([0x00])) is None
    assert parse_heart_rate(b"") is None


def test_rsc_measurement():
    # 1.25 m/s = 320/256, 55 strides/min; no optional fields.
    basic = parse_rsc_measurement(struct.pack("<BHB", 0x00, 320, 55))
    assert basic is not None
    assert basic.speed_kmh == pytest.approx(4.5) and basic.cadence_spm == 55
    assert basic.stride_length_m is None and basic.total_distance_m is None and not basic.running

    full = parse_rsc_measurement(struct.pack("<BHBHI", 0x07, 768, 80, 123, 54_321))
    assert full is not None
    assert full.speed_kmh == pytest.approx(10.8) and full.cadence_spm == 80
    assert full.stride_length_m == pytest.approx(1.23) and full.total_distance_m == pytest.approx(5432.1)
    assert full.running

    only_distance = parse_rsc_measurement(struct.pack("<BHBI", 0x02, 256, 60, 1000))
    assert only_distance is not None
    assert only_distance.stride_length_m is None and only_distance.total_distance_m == pytest.approx(100.0)

    cut = parse_rsc_measurement(struct.pack("<BHB", 0x03, 256, 60) + b"\x01")
    assert cut is not None and cut.stride_length_m is None and cut.total_distance_m is None
    assert parse_rsc_measurement(b"\x00\x00\x01") is None


# ---- heart-rate monitor state -------------------------------------------------------------------


def test_heart_rate_monitor_freshness():
    now = [100.0]
    monitor = HeartRateMonitor(enabled=True, clock=lambda: now[0])
    assert not monitor.fresh and monitor.to_dict()["bpm"] is None
    monitor.feed(bytes([0x00, 96]))
    assert monitor.bpm == 96
    assert not monitor.fresh  # not connected: a stray value is not trusted
    monitor.connected = True
    assert monitor.fresh and monitor.to_dict()["bpm"] == 96
    now[0] += FRESH_S + 0.1
    assert not monitor.fresh and monitor.to_dict()["bpm"] is None
    monitor.feed(bytes([0x01, 0x64, 0x00]))
    assert monitor.fresh and monitor.bpm == 100
    # Implausible or unusable values.
    monitor.feed(bytes([0x00, 0]))  # "no contact": keeps the last value, which then ages out
    assert monitor.bpm == 100
    monitor.feed(bytes([0x00, 20]))
    assert not monitor.fresh
    monitor.feed(bytes([0x01, 0xFA, 0x00]))  # 250 bpm
    assert not monitor.fresh
    monitor.feed(b"\x00")  # malformed: ignored
    assert monitor.bpm == 250


def test_heart_rate_monitor_configure_and_dict():
    import json

    monitor = HeartRateMonitor()
    assert monitor.to_dict() == {
        "enabled": False,
        "connected": False,
        "name": "",
        "address": "",
        "bpm": None,
        "detail": "off",
    }
    monitor.configure(enabled=True, address="AA:BB")
    assert monitor.enabled and monitor.address == "AA:BB"
    json.dumps(monitor.to_dict())


# ---- the probe's decoding --------------------------------------------------------------------------


def test_probe_describes_what_it_understands():
    line = probe.describe_value(ftms.TREADMILL_DATA, bytes.fromhex("842460000a00000100ffffff1e00393001"))
    assert "flags=0x2484" in line and "speed=0.96" in line and "vendor_steps=77881" in line
    assert "TRUNCATED" in probe.describe_value(ftms.TREADMILL_DATA, bytes.fromhex("840441"))
    line = probe.describe_value(ftms.SUPPORTED_SPEED_RANGE, bytes.fromhex("600083021000"))
    assert "min=0.96 max=6.43 step=0.16" in line and "US firmware" in line
    assert "US firmware" not in probe.describe_value(ftms.SUPPORTED_SPEED_RANGE, struct.pack("<HHH", 100, 600, 10))
    assert "speed_target=yes" in probe.describe_value(ftms.FITNESS_MACHINE_FEATURE, struct.pack("<II", 4, 1))
    assert probe.describe_value(ftms.FITNESS_MACHINE_STATUS, b"\x04") == "STARTED_OR_RESUMED_BY_USER"
    assert probe.describe_value(ftms.TRAINING_STATUS, b"\x00\x0e") == "pre-workout (countdown)"
    assert probe.describe_value(ftms.HEART_RATE_MEASUREMENT, b"\x00\x50") == "bpm=80"
    assert probe.describe_value("0000ffff-0000-1000-8000-00805f9b34fb", b"URTM030") == "'URTM030'"
    assert probe.describe_value("0000ffff-0000-1000-8000-00805f9b34fb", b"\x01\x02") == ""


def test_probe_report_is_saved(tmp_path):
    path = probe.save_report(["line one", "line two"], tmp_path / "reports")
    assert path.read_text("utf-8") == "line one\nline two\n"
    assert path.name.startswith("walka-probe-") and path.suffix == ".txt"


# ---- the pace fields: one byte (FTMS 1.0) or two (current GATT supplement) -----------------------


def test_pace_fields_in_the_two_byte_layout_do_not_shift_what_follows():
    import struct

    from walka import ftms

    # speed, instantaneous pace, average pace, elapsed time
    flags = 0x0020 | 0x0040 | 0x0400
    narrow = struct.pack("<HHBBH", flags, 320, 62, 58, 754)
    wide = struct.pack("<HHHHH", flags, 320, 120, 125, 754)
    a, b = ftms.parse_treadmill_data(narrow), ftms.parse_treadmill_data(wide)
    assert a.speed_kmh == b.speed_kmh == 3.2
    assert a.elapsed_time_s == b.elapsed_time_s == 754
    assert not a.truncated and not b.truncated
    assert a.instantaneous_pace_km_per_min == 6.2 and a.average_pace_km_per_min == 5.8
    assert a.instantaneous_pace_s_per_500m is None
    assert b.instantaneous_pace_s_per_500m == 120 and b.average_pace_s_per_500m == 125
    assert b.instantaneous_pace_km_per_min is None


def test_pace_width_with_vendor_steps_and_with_an_ambiguous_length():
    import struct

    from walka import ftms

    flags = 0x0020 | 0x2000  # speed, instantaneous pace, UREVO trailing steps
    narrow = struct.pack("<HHB", flags, 250, 40) + (1234).to_bytes(3, "little")
    wide = struct.pack("<HHH", flags, 250, 180) + (1234).to_bytes(3, "little")
    assert ftms.parse_treadmill_data(narrow).vendor_steps == 1234
    assert ftms.parse_treadmill_data(wide).vendor_steps == 1234
    assert ftms.parse_treadmill_data(wide).instantaneous_pace_s_per_500m == 180
    # A length that fits neither layout falls back to the one-byte layout.
    odd = struct.pack("<HHB", 0x0020, 250, 40) + b"\x00\x00\x00\x00\x00"
    assert ftms.parse_treadmill_data(odd).instantaneous_pace_km_per_min == 4.0
