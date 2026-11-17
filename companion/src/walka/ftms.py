"""Bluetooth Fitness Machine Service (FTMS) codec for treadmills.

Pure data encoding/decoding - no Bluetooth I/O happens here, which keeps it
unit-testable. The BLE transport lives in ``walka.treadmill.ftms_ble``.

References: Bluetooth SIG "Fitness Machine Service 1.0" and the GATT
Specification Supplement (Treadmill Data, Fitness Machine Control Point,
Fitness Machine Status, Supported Speed Range, Training Status).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

# ---- UUIDs ---------------------------------------------------------------------

_BASE = "0000{:04x}-0000-1000-8000-00805f9b34fb"


def uuid16(short: int) -> str:
    """Expand a 16-bit Bluetooth SIG UUID to its 128-bit string form."""
    return _BASE.format(short)


FTMS_SERVICE = uuid16(0x1826)
FITNESS_MACHINE_FEATURE = uuid16(0x2ACC)
TREADMILL_DATA = uuid16(0x2ACD)
TRAINING_STATUS = uuid16(0x2AD3)
SUPPORTED_SPEED_RANGE = uuid16(0x2AD4)
SUPPORTED_INCLINATION_RANGE = uuid16(0x2AD5)
CONTROL_POINT = uuid16(0x2AD9)
FITNESS_MACHINE_STATUS = uuid16(0x2ADA)

HEART_RATE_SERVICE = uuid16(0x180D)
HEART_RATE_MEASUREMENT = uuid16(0x2A37)

RSC_SERVICE = uuid16(0x1814)
RSC_MEASUREMENT = uuid16(0x2A53)

DEVICE_INFORMATION_SERVICE = uuid16(0x180A)


# ---- Treadmill Data (0x2ACD) ---------------------------------------------------

VENDOR_STEPS_FLAG = 0x2000  # reserved in the spec; UREVO uses it for steps


@dataclass
class TreadmillData:
    """Fields of one Treadmill Data notification. Absent fields are ``None``.

    A treadmill may split its data over several notifications; merge them with
    :meth:`merge`.
    """

    speed_kmh: float | None = None
    average_speed_kmh: float | None = None
    total_distance_m: int | None = None
    inclination_pct: float | None = None
    ramp_angle_deg: float | None = None
    positive_elevation_gain_m: float | None = None
    negative_elevation_gain_m: float | None = None
    instantaneous_pace_km_per_min: float | None = None
    average_pace_km_per_min: float | None = None
    # The same two fields in the layout of the current GATT supplement
    # (uint16, seconds per 500 m) - see _pace_width().
    instantaneous_pace_s_per_500m: int | None = None
    average_pace_s_per_500m: int | None = None
    total_energy_kcal: int | None = None
    energy_per_hour_kcal: int | None = None
    energy_per_minute_kcal: int | None = None
    heart_rate_bpm: int | None = None
    metabolic_equivalent: float | None = None
    elapsed_time_s: int | None = None
    remaining_time_s: int | None = None
    force_on_belt_n: int | None = None
    power_output_w: int | None = None
    # Vendor extension seen on UREVO pads: reserved flag bit 13 plus a
    # trailing uint24 step counter after the standard fields.
    vendor_steps: int | None = None
    flags: int = 0
    truncated: bool = False

    def merge(self, newer: TreadmillData) -> TreadmillData:
        """Return a copy where fields present in ``newer`` override ours."""
        merged = TreadmillData(**self.__dict__)
        for name, value in newer.__dict__.items():
            if name in ("flags", "truncated"):
                continue
            if value is not None:
                setattr(merged, name, value)
        merged.flags = newer.flags
        merged.truncated = newer.truncated
        return merged


# Sizes of the optional fields other than speed and pace, by flag bit.
_FIELD_SIZES = (
    (0x0002, 2),  # average speed
    (0x0004, 3),  # total distance
    (0x0008, 4),  # inclination + ramp angle
    (0x0010, 4),  # elevation gain
    (0x0080, 5),  # expended energy
    (0x0100, 1),  # heart rate
    (0x0200, 1),  # metabolic equivalent
    (0x0400, 2),  # elapsed time
    (0x0800, 2),  # remaining time
    (0x1000, 4),  # force on belt + power output
)


def _pace_width(flags: int, length: int) -> int:
    """Width in bytes of each pace field in a packet of this length.

    The specifications disagree: FTMS 1.0 (and every treadmill firmware seen
    so far) uses one byte in 0.1 km/min, the current GATT Specification
    Supplement two bytes in seconds per 500 m. The packet length settles it;
    when it does not, the one-byte layout is assumed.
    """
    paces = bool(flags & 0x0020) + bool(flags & 0x0040)
    if not paces:
        return 1
    base = 2 + (0 if flags & 0x0001 else 2) + sum(size for bit, size in _FIELD_SIZES if flags & bit)
    base += 3 if flags & VENDOR_STEPS_FLAG else 0
    if length == base + paces:
        return 1
    if length == base + 2 * paces:
        return 2
    return 1


def parse_treadmill_data(data: bytes) -> TreadmillData:
    """Decode a Treadmill Data characteristic value.

    The first two bytes are a little-endian flags field. Bit 0 ("More Data")
    is inverted: instantaneous speed is present when the bit is **0**.
    Fields the packet is too short to contain are left as ``None`` and
    ``truncated`` is set - cheap treadmills are not always spec-perfect.
    """
    out = TreadmillData()
    if len(data) < 2:
        out.truncated = True
        return out
    flags = int.from_bytes(data[0:2], "little")
    out.flags = flags
    pos = 2

    def take(fmt: str) -> tuple | None:
        nonlocal pos
        size = struct.calcsize(fmt)
        if pos + size > len(data):
            out.truncated = True
            pos = len(data)
            return None
        values = struct.unpack_from(fmt, data, pos)
        pos += size
        return values

    def u24() -> int | None:
        nonlocal pos
        if pos + 3 > len(data):
            out.truncated = True
            pos = len(data)
            return None
        value = int.from_bytes(data[pos : pos + 3], "little")
        pos += 3
        return value

    if not flags & 0x0001:  # More Data == 0 -> instantaneous speed present
        if (v := take("<H")) is not None:
            out.speed_kmh = v[0] / 100.0
    if flags & 0x0002:  # average speed
        if (v := take("<H")) is not None:
            out.average_speed_kmh = v[0] / 100.0
    if flags & 0x0004:  # total distance, uint24 metres
        if (d := u24()) is not None:
            out.total_distance_m = d
    if flags & 0x0008:  # inclination (0.1 %) + ramp angle setting (0.1 deg)
        if (v := take("<hh")) is not None:
            out.inclination_pct = v[0] / 10.0
            out.ramp_angle_deg = v[1] / 10.0
    if flags & 0x0010:  # positive + negative elevation gain (0.1 m)
        if (v := take("<HH")) is not None:
            out.positive_elevation_gain_m = v[0] / 10.0
            out.negative_elevation_gain_m = v[1] / 10.0
    wide_pace = _pace_width(flags, len(data)) == 2
    if flags & 0x0020:  # instantaneous pace
        if wide_pace:
            if (v := take("<H")) is not None:
                out.instantaneous_pace_s_per_500m = v[0]
        elif (v := take("<B")) is not None:
            out.instantaneous_pace_km_per_min = v[0] / 10.0
    if flags & 0x0040:  # average pace
        if wide_pace:
            if (v := take("<H")) is not None:
                out.average_pace_s_per_500m = v[0]
        elif (v := take("<B")) is not None:
            out.average_pace_km_per_min = v[0] / 10.0
    if flags & 0x0080:  # expended energy: total, per hour, per minute (kcal)
        if (v := take("<HHB")) is not None:
            out.total_energy_kcal = None if v[0] == 0xFFFF else v[0]
            out.energy_per_hour_kcal = None if v[1] == 0xFFFF else v[1]
            out.energy_per_minute_kcal = None if v[2] == 0xFF else v[2]
    if flags & 0x0100:  # heart rate (bpm)
        if (v := take("<B")) is not None:
            out.heart_rate_bpm = v[0]
    if flags & 0x0200:  # metabolic equivalent (0.1)
        if (v := take("<B")) is not None:
            out.metabolic_equivalent = v[0] / 10.0
    if flags & 0x0400:  # elapsed time (s)
        if (v := take("<H")) is not None:
            out.elapsed_time_s = v[0]
    if flags & 0x0800:  # remaining time (s)
        if (v := take("<H")) is not None:
            out.remaining_time_s = v[0]
    if flags & 0x1000:  # force on belt (N) + power output (W)
        if (v := take("<hh")) is not None:
            out.force_on_belt_n = v[0]
            out.power_output_w = v[1]
    if flags & VENDOR_STEPS_FLAG and not out.truncated and len(data) - pos >= 3:
        out.vendor_steps = int.from_bytes(data[pos : pos + 3], "little")
    return out


def build_treadmill_data(
    speed_kmh: float | None = None,
    *,
    total_distance_m: int | None = None,
    total_energy_kcal: int | None = None,
    elapsed_time_s: int | None = None,
    heart_rate_bpm: int | None = None,
    vendor_steps: int | None = None,
) -> bytes:
    """Encode a Treadmill Data value (used by tests and fakes)."""
    flags = 0
    body = bytearray()
    if speed_kmh is None:
        flags |= 0x0001
    else:
        body += struct.pack("<H", max(0, min(0xFFFF, round(speed_kmh * 100))))
    if total_distance_m is not None:
        flags |= 0x0004
        body += int(max(0, min(0xFFFFFF, total_distance_m))).to_bytes(3, "little")
    if total_energy_kcal is not None:
        flags |= 0x0080
        body += struct.pack("<HHB", max(0, min(0xFFFE, total_energy_kcal)), 0xFFFF, 0xFF)
    if heart_rate_bpm is not None:
        flags |= 0x0100
        body += struct.pack("<B", max(0, min(255, heart_rate_bpm)))
    if elapsed_time_s is not None:
        flags |= 0x0400
        body += struct.pack("<H", max(0, min(0xFFFF, elapsed_time_s)))
    if vendor_steps is not None:
        flags |= VENDOR_STEPS_FLAG
        body += int(max(0, min(0xFFFFFF, vendor_steps))).to_bytes(3, "little")
    return struct.pack("<H", flags) + bytes(body)


# ---- Supported Speed Range (0x2AD4) --------------------------------------------


@dataclass(frozen=True)
class SpeedRange:
    min_kmh: float
    max_kmh: float
    step_kmh: float


def parse_supported_speed_range(data: bytes) -> SpeedRange | None:
    """Decode Supported Speed Range: three uint16 in 0.01 km/h (min, max, step)."""
    if len(data) < 6:
        return None
    lo, hi, step = struct.unpack_from("<HHH", data, 0)
    return SpeedRange(lo / 100.0, hi / 100.0, step / 100.0)


# ---- Fitness Machine Feature (0x2ACC) ------------------------------------------


@dataclass(frozen=True)
class MachineFeatures:
    features: int
    target_settings: int

    @property
    def speed_target_supported(self) -> bool:
        """Target Setting Features bit 0: Speed Target Setting Supported."""
        return bool(self.target_settings & 0x0001)

    @property
    def step_count_supported(self) -> bool:
        """Fitness Machine Features bit 6: Step Count Supported."""
        return bool(self.features & 0x0040)


def parse_features(data: bytes) -> MachineFeatures | None:
    if len(data) < 8:
        return None
    features, targets = struct.unpack_from("<II", data, 0)
    return MachineFeatures(features, targets)


# ---- Control Point (0x2AD9) ----------------------------------------------------


class Op(IntEnum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_SPEED = 0x02
    SET_TARGET_INCLINATION = 0x03
    START_OR_RESUME = 0x07
    STOP_OR_PAUSE = 0x08
    RESPONSE_CODE = 0x80


class ResultCode(IntEnum):
    SUCCESS = 0x01
    OP_CODE_NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    OPERATION_FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


STOP_PARAM = 0x01
PAUSE_PARAM = 0x02


def cmd_request_control() -> bytes:
    return bytes([Op.REQUEST_CONTROL])


def cmd_reset() -> bytes:
    return bytes([Op.RESET])


def cmd_start_or_resume() -> bytes:
    return bytes([Op.START_OR_RESUME])


def cmd_stop() -> bytes:
    return bytes([Op.STOP_OR_PAUSE, STOP_PARAM])


def cmd_pause() -> bytes:
    return bytes([Op.STOP_OR_PAUSE, PAUSE_PARAM])


def cmd_set_target_speed(kmh: float) -> bytes:
    """Set Target Speed: opcode 0x02 + uint16 little-endian in 0.01 km/h."""
    raw = max(0, min(0xFFFF, round(kmh * 100)))
    return struct.pack("<BH", Op.SET_TARGET_SPEED, raw)


@dataclass(frozen=True)
class ControlResponse:
    request_op: int
    result: int

    @property
    def ok(self) -> bool:
        return self.result == ResultCode.SUCCESS

    def describe(self) -> str:
        try:
            op = Op(self.request_op).name
        except ValueError:
            op = f"0x{self.request_op:02X}"
        try:
            result = ResultCode(self.result).name
        except ValueError:
            result = f"0x{self.result:02X}"
        return f"{op}: {result}"


def parse_control_response(data: bytes) -> ControlResponse | None:
    """Decode a Control Point indication: 0x80, request opcode, result code."""
    if len(data) < 3 or data[0] != Op.RESPONSE_CODE:
        return None
    return ControlResponse(data[1], data[2])


# ---- Fitness Machine Status (0x2ADA) -------------------------------------------


class StatusOp(IntEnum):
    RESET = 0x01
    STOPPED_OR_PAUSED_BY_USER = 0x02
    STOPPED_BY_SAFETY_KEY = 0x03
    STARTED_OR_RESUMED_BY_USER = 0x04
    TARGET_SPEED_CHANGED = 0x05
    TARGET_INCLINE_CHANGED = 0x06
    CONTROL_PERMISSION_LOST = 0xFF


@dataclass(frozen=True)
class MachineStatus:
    op: int
    params: bytes = b""
    target_speed_kmh: float | None = field(default=None)

    @property
    def stopped(self) -> bool:
        """Belt fully stopped (by the user at the machine, or the safety key)."""
        if self.op == StatusOp.STOPPED_BY_SAFETY_KEY:
            return True
        return self.op == StatusOp.STOPPED_OR_PAUSED_BY_USER and self.params[:1] != bytes([PAUSE_PARAM])

    @property
    def paused(self) -> bool:
        return self.op == StatusOp.STOPPED_OR_PAUSED_BY_USER and self.params[:1] == bytes([PAUSE_PARAM])

    @property
    def started(self) -> bool:
        return self.op == StatusOp.STARTED_OR_RESUMED_BY_USER

    def describe(self) -> str:
        try:
            name = StatusOp(self.op).name
        except ValueError:
            name = f"0x{self.op:02X}"
        if self.target_speed_kmh is not None:
            return f"{name} ({self.target_speed_kmh:.2f} km/h)"
        if self.params:
            return f"{name} ({self.params.hex()})"
        return name


def parse_machine_status(data: bytes) -> MachineStatus | None:
    if not data:
        return None
    op, params = data[0], bytes(data[1:])
    target = None
    if op == StatusOp.TARGET_SPEED_CHANGED and len(params) >= 2:
        target = int.from_bytes(params[:2], "little") / 100.0
    return MachineStatus(op, params, target)


# ---- Heart Rate Measurement (0x2A37) -------------------------------------------


def parse_heart_rate(data: bytes) -> int | None:
    """Decode Heart Rate Measurement and return beats per minute."""
    if len(data) < 2:
        return None
    if data[0] & 0x01:  # 16-bit heart-rate value
        if len(data) < 3:
            return None
        return int.from_bytes(data[1:3], "little")
    return data[1]


# ---- Running Speed and Cadence Measurement (0x2A53) ----------------------------


@dataclass(frozen=True)
class RscMeasurement:
    speed_kmh: float
    cadence_spm: int  # steps per minute, both feet
    stride_length_m: float | None = None
    total_distance_m: float | None = None
    running: bool = False


def parse_rsc_measurement(data: bytes) -> RscMeasurement | None:
    """Decode RSC Measurement.

    Layout: flags(u8), instantaneous speed (u16, 1/256 m/s), instantaneous
    cadence (u8, strides... per minute as reported by the sensor), then
    optional stride length (u16, 0.01 m) and total distance (u32, 0.1 m).
    """
    if len(data) < 4:
        return None
    flags = data[0]
    speed_raw, cadence = struct.unpack_from("<HB", data, 1)
    pos = 4
    stride = None
    distance = None
    if flags & 0x01 and len(data) >= pos + 2:
        stride = struct.unpack_from("<H", data, pos)[0] / 100.0
        pos += 2
    if flags & 0x02 and len(data) >= pos + 4:
        distance = struct.unpack_from("<I", data, pos)[0] / 10.0
        pos += 4
    return RscMeasurement(
        speed_kmh=speed_raw / 256.0 * 3.6,
        cadence_spm=cadence,
        stride_length_m=stride,
        total_distance_m=distance,
        running=bool(flags & 0x04),
    )
