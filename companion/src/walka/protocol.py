"""Walka beacon protocol: frame packing/unpacking and CRC.

The addon (``Walka/Core/Protocol.lua``) packs its live state into a 10-byte
frame and paints it on screen; this module is the matching codec. The format
is specified in ``docs/PROTOCOL.md`` - keep all three in sync.

A frame is 8 payload bytes followed by a big-endian CRC-16/CCITT-FALSE of the
payload. Payload bit layout, most-significant bit first::

    bits  0..3   version         (4)   = VERSION
    bits  4..5   frame type      (2)   0 = live state
    bits  6..13  seq             (8)   heartbeat counter, wraps at 256
    bits 14..21  session         (8)   session counter, 0 = no session
    bits 22..37  flags           (16)  see Flags
    bits 38..49  target speed    (12)  belt speed wanted, 0.01 km/h units
    bits 50..61  character speed (12)  0.02 yd/s units
    bits 62..63  reserved        (2)   = 0
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntFlag

VERSION = 1
FRAME_BYTES = 10
PAYLOAD_BYTES = 8
FRAME_BITS = FRAME_BYTES * 8

TYPE_LIVE = 0

TARGET_UNIT_KMH = 0.01
TARGET_MAX_RAW = 4095
CHAR_UNIT_YPS = 0.02
CHAR_MAX_RAW = 4095

# (name, bit width) in transmission order.
_FIELDS: tuple[tuple[str, int], ...] = (
    ("version", 4),
    ("frame_type", 2),
    ("seq", 8),
    ("session", 8),
    ("flags", 16),
    ("target_raw", 12),
    ("char_speed_raw", 12),
    ("reserved", 2),
)


class Flags(IntFlag):
    """State flags carried in every frame (bit 0 = least significant)."""

    ACTIVE = 1  # a walking session is running: the belt may be driven
    PAUSED = 2  # session paused: the belt must be paused
    MOVING = 4  # character speed > 0
    MOUNTED = 8
    TAXI = 16  # on a flight path
    SWIMMING = 32
    FLYING = 64
    DEAD = 128  # dead or ghost
    COMBAT = 256
    AFK = 512
    FALLING = 1024
    RESTING = 2048  # inn / city rest state
    ESTIMATED = 4096  # character speed is an estimate (hidden from addons in combat)


class FrameError(ValueError):
    """Raised when bytes/bits do not form a valid Walka frame."""


def crc16(data: Iterable[int]) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF. ``crc16(b"123456789") == 0x29B1``."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    """One decoded beacon frame."""

    seq: int = 0
    session: int = 0
    flags: Flags = Flags(0)
    target_raw: int = 0
    char_speed_raw: int = 0
    frame_type: int = TYPE_LIVE
    version: int = VERSION
    reserved: int = 0

    # ---- convenience accessors -------------------------------------------------
    @property
    def target_kmh(self) -> float:
        """Belt speed requested by the addon, km/h."""
        return self.target_raw * TARGET_UNIT_KMH

    @property
    def char_speed_yps(self) -> float:
        """Character movement speed, yards per second."""
        return self.char_speed_raw * CHAR_UNIT_YPS

    @property
    def active(self) -> bool:
        return bool(self.flags & Flags.ACTIVE)

    @property
    def paused(self) -> bool:
        return bool(self.flags & Flags.PAUSED)

    def has(self, flag: Flags) -> bool:
        return bool(self.flags & flag)

    # ---- packing ---------------------------------------------------------------
    def payload(self) -> bytes:
        value = 0
        for name, width in _FIELDS:
            field = int(getattr(self, name))
            if not 0 <= field < (1 << width):
                raise FrameError(f"field {name}={field} does not fit in {width} bits")
            value = (value << width) | field
        return value.to_bytes(PAYLOAD_BYTES, "big")

    def pack(self) -> bytes:
        """Return the 10-byte frame (payload + CRC)."""
        payload = self.payload()
        return payload + crc16(payload).to_bytes(2, "big")

    def to_bits(self) -> list[int]:
        """Return the frame as 80 bits, most-significant bit first."""
        return bytes_to_bits(self.pack())

    # ---- unpacking -------------------------------------------------------------
    @classmethod
    def unpack(cls, data: bytes | Sequence[int]) -> Frame:
        """Decode a 10-byte frame. Raises FrameError on bad length/CRC/version."""
        data = bytes(data)
        if len(data) != FRAME_BYTES:
            raise FrameError(f"expected {FRAME_BYTES} bytes, got {len(data)}")
        payload, received = data[:PAYLOAD_BYTES], int.from_bytes(data[PAYLOAD_BYTES:], "big")
        if crc16(payload) != received:
            raise FrameError("CRC mismatch")
        value = int.from_bytes(payload, "big")
        fields: dict[str, int] = {}
        shift = PAYLOAD_BYTES * 8
        for name, width in _FIELDS:
            shift -= width
            fields[name] = (value >> shift) & ((1 << width) - 1)
        if fields["version"] != VERSION:
            raise FrameError(f"unsupported protocol version {fields['version']}")
        return cls(
            seq=fields["seq"],
            session=fields["session"],
            flags=Flags(fields["flags"]),
            target_raw=fields["target_raw"],
            char_speed_raw=fields["char_speed_raw"],
            frame_type=fields["frame_type"],
            version=fields["version"],
            reserved=fields["reserved"],
        )

    @classmethod
    def from_bits(cls, bits: Sequence[int]) -> Frame:
        """Decode 80 bits (most-significant first)."""
        if len(bits) != FRAME_BITS:
            raise FrameError(f"expected {FRAME_BITS} bits, got {len(bits)}")
        return cls.unpack(bits_to_bytes(bits))


def bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def bits_to_bytes(bits: Sequence[int]) -> bytes:
    if len(bits) % 8:
        raise FrameError("bit count is not a multiple of 8")
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | (1 if bit else 0)
        out.append(byte)
    return bytes(out)


def kmh_to_raw(kmh: float) -> int:
    return max(0, min(TARGET_MAX_RAW, int(round(kmh / TARGET_UNIT_KMH))))


def yps_to_raw(yps: float) -> int:
    return max(0, min(CHAR_MAX_RAW, int(round(yps / CHAR_UNIT_YPS))))
