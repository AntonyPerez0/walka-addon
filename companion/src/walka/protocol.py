"""Walka beacon protocol: frame packing/unpacking and CRC.

The addon (``Walka/Core/Protocol.lua``) packs its state into 10-byte frames
and paints them on screen; this module is the matching codec. The format is
specified in ``docs/PROTOCOL.md`` - keep all three in sync.

A frame is 8 payload bytes followed by a big-endian CRC-16/CCITT-FALSE of the
payload. There are two kinds, told apart by the frame type.

The live frame (``Frame``) is what the belt is driven by. Payload bit layout,
most-significant bit first::

    bits  0..3   version         (4)   = VERSION
    bits  4..5   frame type      (2)   = TYPE_LIVE
    bits  6..13  seq             (8)   heartbeat counter, wraps at 256
    bits 14..21  session         (8)   session counter, 0 = no session
    bits 22..37  flags           (16)  see Flags
    bits 38..49  target speed    (12)  belt speed wanted, 0.01 km/h units
    bits 50..61  character speed (12)  0.02 yd/s units
    bits 62..63  reserved        (2)   = 0

The stat frame (``StatFrame``) carries one number of the character's journey
(level, time played, distance on foot, ...) for the picture the dashboard
makes::

    bits  0..3   version         (4)   = VERSION
    bits  4..5   frame type      (2)   = TYPE_STAT
    bits  6..13  seq             (8)   the same counter as in live frames
    bits 14..19  stat id         (6)   see Stat
    bits 20..59  value           (40)  unsigned
    bits 60..63  reserved        (4)   = 0

A stat frame says nothing about what the belt should do, so it must never be
read as a live frame: the two are separate types with nothing in common,
``decode`` is the one place that tells them apart, and ``Frame.unpack`` /
``Frame.from_bits`` refuse everything that is not a live frame.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import ClassVar, NamedTuple

VERSION = 1
FRAME_BYTES = 10
PAYLOAD_BYTES = 8
FRAME_BITS = FRAME_BYTES * 8

TYPE_LIVE = 0  # live state: the only kind that may steer the belt
TYPE_STAT = 1  # one number of the character's journey
# (Types 2 and 3 are not assigned: such a frame is refused.)

TARGET_UNIT_KMH = 0.01
TARGET_MAX_RAW = 4095
CHAR_UNIT_YPS = 0.02
CHAR_MAX_RAW = 4095

STAT_MAX_ID = 63
STAT_MAX_VALUE = (1 << 40) - 1
# The addon sends a stat frame in place of every STAT_EVERY-th live frame.
STAT_EVERY = 5
# A character's name travels as NAME_BYTES bytes of UTF-8, NAME_CHUNK_BYTES
# per stat frame.
NAME_BYTES = 20
NAME_CHUNK_BYTES = 5

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
_STAT_FIELDS: tuple[tuple[str, int], ...] = (
    ("version", 4),
    ("frame_type", 2),
    ("seq", 8),
    ("stat_id", 6),
    ("value", 40),
    ("reserved", 4),
)
# Both layouts start the same way, which is how a frame's kind is found.
_VERSION_SHIFT = PAYLOAD_BYTES * 8 - 4
_TYPE_SHIFT = _VERSION_SHIFT - 2


class Flags(IntFlag):
    """State flags carried in every live frame (bit 0 = least significant)."""

    ACTIVE = 1  # a walking session is running: the belt may be driven
    PAUSED = 2  # session paused: the belt must be paused
    MOVING = 4  # character speed > 0
    MOUNTED = 8
    TAXI = 16  # on a flight path
    SWIMMING = 32
    FLYING = 64
    DEAD = 128  # (no longer sent: see LEGACY_FLAGS)
    COMBAT = 256  # (no longer sent)
    AFK = 512
    FALLING = 1024  # (no longer sent)
    RESTING = 2048  # (no longer sent)
    ESTIMATED = 4096  # character speed is an estimate (hidden from addons in combat)


# Flags that the addon set until version 0.5 and has not sent since: nothing
# here ever acted on them, and what the belt does not need stays in the
# game. A frame from an older addon that carries one is still a frame of
# the addon's (not_from_the_addon), but the flag is shown nowhere.
LEGACY_FLAGS = Flags.DEAD | Flags.COMBAT | Flags.FALLING | Flags.RESTING


class Stat(IntEnum):
    """What the value of a stat frame is (the 6-bit stat id).

    Ids that are not listed here are not assigned; a frame that carries one
    is valid but means nothing to this build (``StatFrame.stat`` is None).
    """

    CHAR_KEY = 1  # djb2("Name-Realm"): tells characters apart, starts a cycle
    CHAR_INFO = 2  # level, level cap, class and faction: see CharInfo
    PLAYED_S = 3  # total /played seconds; 0 = not known yet
    FOOT_YD = 4  # yards travelled on foot since tracking began
    TRAVEL_YD = 5  # yards travelled by any means
    REAL_STEPS = 6  # real-world steps walked while playing this character
    REAL_M = 7  # real-world metres walked
    REAL_KCAL = 8  # kilocalories (gross, whole number)
    REAL_S = 9  # seconds on the moving belt
    WALKS = 10  # number of walks counted
    SINCE = 11  # unix time (seconds) when tracking began
    INFO_FLAGS = 12  # see InfoFlags
    NAME_0 = 16  # the character's name, bytes 1-5 ...
    NAME_1 = 17
    NAME_2 = 18
    NAME_3 = 19  # ... bytes 16-20


NAME_STATS: tuple[Stat, ...] = (Stat.NAME_0, Stat.NAME_1, Stat.NAME_2, Stat.NAME_3)

# The order the addon sends the stats in, over and over. CHAR_KEY comes first:
# whatever follows it belongs to that character.
STAT_CYCLE: tuple[Stat, ...] = (
    Stat.CHAR_KEY,
    *NAME_STATS,
    Stat.CHAR_INFO,
    Stat.PLAYED_S,
    Stat.FOOT_YD,
    Stat.TRAVEL_YD,
    Stat.REAL_STEPS,
    Stat.REAL_M,
    Stat.REAL_KCAL,
    Stat.REAL_S,
    Stat.WALKS,
    Stat.SINCE,
    Stat.INFO_FLAGS,
)


class InfoFlags(IntFlag):
    """The value of ``Stat.INFO_FLAGS`` (bit 0 = least significant)."""

    ESTIMATED = 1  # the real-world totals include estimates the companion has not confirmed yet


class Faction(IntEnum):
    """The faction byte of ``Stat.CHAR_INFO``."""

    UNKNOWN = 0
    ALLIANCE = 1
    HORDE = 2


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


def _pack_fields(frame: object, fields: tuple[tuple[str, int], ...]) -> bytes:
    value = 0
    for name, width in fields:
        field = int(getattr(frame, name))
        if not 0 <= field < (1 << width):
            raise FrameError(f"field {name}={field} does not fit in {width} bits")
        value = (value << width) | field
    return value.to_bytes(PAYLOAD_BYTES, "big")


def _split_fields(value: int, fields: tuple[tuple[str, int], ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    shift = PAYLOAD_BYTES * 8
    for name, width in fields:
        shift -= width
        out[name] = (value >> shift) & ((1 << width) - 1)
    return out


@dataclass(frozen=True)
class Frame:
    """One decoded live frame: the addon's state, which the belt follows."""

    seq: int = 0
    session: int = 0
    flags: Flags = Flags(0)
    target_raw: int = 0
    char_speed_raw: int = 0
    version: int = VERSION
    reserved: int = 0

    # Not a field: a Frame cannot be given another type, so no Frame can
    # ever stand for (or be packed as) a stat frame.
    frame_type: ClassVar[int] = TYPE_LIVE

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
        return _pack_fields(self, _FIELDS)

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
        """Decode a 10-byte live frame.

        Raises FrameError on bad length/CRC/version - and for a frame of any
        other type: whoever asks for a live frame must never be handed
        something else that merely decodes.
        """
        frame = decode(data)
        if not isinstance(frame, Frame):
            raise FrameError(f"not a live frame (frame type {frame.frame_type})")
        return frame

    @classmethod
    def from_bits(cls, bits: Sequence[int]) -> Frame:
        """Decode 80 bits (most-significant first) that carry a live frame."""
        if len(bits) != FRAME_BITS:
            raise FrameError(f"expected {FRAME_BITS} bits, got {len(bits)}")
        return cls.unpack(bits_to_bytes(bits))

    @classmethod
    def _from_payload(cls, value: int) -> Frame:
        fields = _split_fields(value, _FIELDS)
        return cls(
            seq=fields["seq"],
            session=fields["session"],
            flags=Flags(fields["flags"]),
            target_raw=fields["target_raw"],
            char_speed_raw=fields["char_speed_raw"],
            version=fields["version"],
            reserved=fields["reserved"],
        )


@dataclass(frozen=True)
class StatFrame:
    """One decoded stat frame: one number of the character's journey.

    Deliberately not a kind of ``Frame`` and without any of its accessors
    (no session, no flags, no speeds): code written for live frames cannot
    use one by accident.
    """

    seq: int = 0
    stat_id: int = 0
    value: int = 0
    version: int = VERSION
    reserved: int = 0

    frame_type: ClassVar[int] = TYPE_STAT

    @property
    def stat(self) -> Stat | None:
        """Which stat this is, or None for an id this build does not know."""
        try:
            return Stat(self.stat_id)
        except ValueError:
            return None

    # ---- packing (tests and the demo; in real use only the addon packs) --------
    def payload(self) -> bytes:
        return _pack_fields(self, _STAT_FIELDS)

    def pack(self) -> bytes:
        """Return the 10-byte frame (payload + CRC)."""
        payload = self.payload()
        return payload + crc16(payload).to_bytes(2, "big")

    def to_bits(self) -> list[int]:
        """Return the frame as 80 bits, most-significant bit first."""
        return bytes_to_bits(self.pack())

    @classmethod
    def _from_payload(cls, value: int) -> StatFrame:
        fields = _split_fields(value, _STAT_FIELDS)
        return cls(
            seq=fields["seq"],
            stat_id=fields["stat_id"],
            value=fields["value"],
            version=fields["version"],
            reserved=fields["reserved"],
        )


def decode(data: bytes | Sequence[int]) -> Frame | StatFrame:
    """Decode a 10-byte frame of either kind.

    Returns a ``Frame`` (live state) or a ``StatFrame``; check which with
    ``isinstance``. Raises FrameError on bad length, CRC or version, and for
    the frame types that are not assigned (2 and 3).
    """
    data = bytes(data)
    if len(data) != FRAME_BYTES:
        raise FrameError(f"expected {FRAME_BYTES} bytes, got {len(data)}")
    payload, received = data[:PAYLOAD_BYTES], int.from_bytes(data[PAYLOAD_BYTES:], "big")
    if crc16(payload) != received:
        raise FrameError("CRC mismatch")
    value = int.from_bytes(payload, "big")
    version = value >> _VERSION_SHIFT
    if version != VERSION:
        raise FrameError(f"unsupported protocol version {version}")
    frame_type = (value >> _TYPE_SHIFT) & 0b11
    if frame_type == TYPE_LIVE:
        return Frame._from_payload(value)
    if frame_type == TYPE_STAT:
        return StatFrame._from_payload(value)
    raise FrameError(f"unknown frame type {frame_type}")


def decode_bits(bits: Sequence[int]) -> Frame | StatFrame:
    """``decode`` for 80 bits (most-significant first)."""
    if len(bits) != FRAME_BITS:
        raise FrameError(f"expected {FRAME_BITS} bits, got {len(bits)}")
    return decode(bits_to_bytes(bits))


# Every flag an addon can have set (bits 0-12; four of them only an addon
# before 0.6: LEGACY_FLAGS). The three bits above them are not assigned.
FLAGS_DEFINED = sum(int(flag) for flag in Flags)


def not_from_the_addon(frame: Frame | StatFrame) -> str | None:
    """Why the addon cannot have drawn this frame, or None if it can have.

    ``decode`` reads any ten bytes with the right CRC, version and type: it
    is a codec. A strip read off a screen has to pass this as well. Text,
    tables and window decorations pass a 16-bit CRC by chance - about one
    screenful of source code in a hundred did - and what comes out of such
    an accident has random bits everywhere. The addon, though, always writes
    zeros into the reserved bits, sets only the flags it knows and sends
    only the stats it knows (``Protocol.packBits`` / ``packStatBits``), so
    most accidents are told apart by looking.

    The price: a new flag or stat needs a companion that knows it. An older
    companion takes such a frame for a picture without a strip in it.
    """
    if frame.reserved != 0:
        return "reserved bits are set"
    if isinstance(frame, StatFrame):
        if frame.stat is None:
            return f"stat id {frame.stat_id} is not assigned"
        return None
    if int(frame.flags) & ~FLAGS_DEFINED:
        return "flag bits that are not assigned are set"
    return None


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


# ---- what the values of some stats mean --------------------------------------------


def djb2(text: str | bytes) -> int:
    """The hash behind ``Stat.CHAR_KEY``: djb2 over the UTF-8 bytes, 32 bits.

    ``h = 5381``, then ``h = (h * 33 + byte) mod 2^32`` for every byte. The
    addon hashes the character's "Name-Realm" so that the realm never has to
    be drawn on screen.
    """
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    h = 5381
    for byte in data:
        h = (h * 33 + byte) & 0xFFFFFFFF
    return h


class CharInfo(NamedTuple):
    """The value of ``Stat.CHAR_INFO``, taken apart."""

    level: int
    max_level: int  # the level cap of the game at the time
    class_id: int  # the game's class id (1 = warrior, ...); 0 = unknown
    faction: int  # see Faction; anything else is to be read as unknown


def pack_char_info(level: int, max_level: int, class_id: int, faction: int) -> int:
    """``level | max_level << 8 | class_id << 16 | faction << 24``, each one byte."""
    value = 0
    for shift, part in enumerate((level, max_level, class_id, faction)):
        if not 0 <= part <= 255:
            raise ValueError(f"{part} does not fit in one byte")
        value |= part << (8 * shift)
    return value


def unpack_char_info(value: int) -> CharInfo:
    """Take a ``Stat.CHAR_INFO`` value apart (bits above the four bytes are ignored)."""
    return CharInfo(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF)


def name_to_chunks(name: str) -> tuple[int, ...]:
    """The four values of ``Stat.NAME_0``..``NAME_3`` for a character name.

    The name's UTF-8 bytes, cut to NAME_BYTES without splitting a character
    and padded with zeros, five bytes per value with the first byte in the
    most significant position.
    """
    data = name.encode("utf-8")
    if len(data) > NAME_BYTES:
        cut = NAME_BYTES
        # Bytes 0b10xxxxxx continue a character: do not start the cut on one.
        while cut > 0 and data[cut] & 0xC0 == 0x80:
            cut -= 1
        data = data[:cut]
    data = data.ljust(NAME_BYTES, b"\0")
    return tuple(int.from_bytes(data[i : i + NAME_CHUNK_BYTES], "big") for i in range(0, NAME_BYTES, NAME_CHUNK_BYTES))


def name_from_chunks(chunks: Sequence[int]) -> str:
    """The character name carried by the values of ``NAME_0``..``NAME_3``.

    The name ends at the first zero byte. Bytes that are not UTF-8 (which
    the addon never sends) come out as U+FFFD rather than as an error: this
    is text for display.
    """
    if len(chunks) != len(NAME_STATS):
        raise ValueError(f"expected {len(NAME_STATS)} name values, got {len(chunks)}")
    if not all(0 <= chunk <= STAT_MAX_VALUE for chunk in chunks):
        raise ValueError("a name value does not fit in a stat frame")
    data = b"".join(int(chunk).to_bytes(NAME_CHUNK_BYTES, "big") for chunk in chunks)
    return data.split(b"\0", 1)[0].decode("utf-8", errors="replace")
