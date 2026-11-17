"""Stat frames: the second kind of beacon frame, and the wall between it and
the live frame.

A stat frame carries one number of a character's journey. It says nothing
about the belt, so the most important tests here are the ones that prove a
stat frame can never come out of the codec as a live ``Frame``.
"""

from __future__ import annotations

import random

import pytest

from walka import beacon, protocol
from walka.protocol import (
    STAT_CYCLE,
    STAT_MAX_VALUE,
    CharInfo,
    Faction,
    Flags,
    Frame,
    FrameError,
    InfoFlags,
    Stat,
    StatFrame,
    bytes_to_bits,
    crc16,
    decode,
    decode_bits,
    djb2,
    name_from_chunks,
    name_to_chunks,
    pack_char_info,
    unpack_char_info,
)
from walka_fakes import load_addon_file, lua_to_py

STAT_FIELD_WIDTHS = {"version": 4, "frame_type": 2, "seq": 8, "stat_id": 6, "value": 40, "reserved": 4}
SETTABLE = {name: width for name, width in STAT_FIELD_WIDTHS.items() if name not in ("version", "frame_type")}

# Values at the edges of what the 40-bit field, a 32-bit hash and a double can hold.
VALUES = [0, 1, 255, 256, 2**31, 2**32 - 1, 2**32, 2**39, 2**40 - 2, 2**40 - 1]


def random_stat_frame(rng: random.Random) -> StatFrame:
    return StatFrame(
        seq=rng.randrange(256),
        stat_id=rng.randrange(64),
        value=rng.getrandbits(40),
        reserved=rng.randrange(16),
    )


def reference_bits(frame: StatFrame) -> str:
    """The documented layout (docs/PROTOCOL.md), written out independently."""
    payload = f"{frame.version:04b}{1:02b}{frame.seq:08b}{frame.stat_id:06b}{frame.value:040b}{frame.reserved:04b}"
    assert len(payload) == 64
    crc = 0xFFFF
    for bit in payload:
        top = (crc >> 15) & 1
        crc = (crc << 1) & 0xFFFF
        if top ^ int(bit):
            crc ^= 0x1021
    return payload + f"{crc:016b}"


def raw_frame(version: int, frame_type: int, rest: int = 0) -> bytes:
    """Ten bytes with a correct CRC and any header: what the two documented
    kinds of frame cannot produce (other versions, the unassigned types)."""
    assert 0 <= rest < (1 << 58)
    payload = ((version << 60) | (frame_type << 58) | rest).to_bytes(8, "big")
    return payload + crc16(payload).to_bytes(2, "big")


def retyped(packed: bytes, frame_type: int) -> bytes:
    """The same payload under another frame type, CRC made right again."""
    payload = int.from_bytes(packed[:8], "big")
    return raw_frame(payload >> 60, frame_type, payload & ((1 << 58) - 1))


class RawStrip:
    """Something to draw (``beacon.render_row``) that is not one of our frames."""

    def __init__(self, packed: bytes) -> None:
        self.packed = packed

    def to_bits(self) -> list[int]:
        return bytes_to_bits(self.packed)


# ---- the format ------------------------------------------------------------------------


def test_constants_match_the_documented_format():
    assert protocol.TYPE_LIVE == 0 and protocol.TYPE_STAT == 1
    assert sum(STAT_FIELD_WIDTHS.values()) == 64
    assert STAT_MAX_VALUE == 2**40 - 1 == 1099511627775
    assert protocol.STAT_MAX_ID == 63
    assert protocol.STAT_EVERY == 5
    assert protocol.NAME_BYTES == 20 and protocol.NAME_CHUNK_BYTES == 5
    # Stat ids are part of the wire format.
    assert {stat.name: stat.value for stat in Stat} == {
        "CHAR_KEY": 1,
        "CHAR_INFO": 2,
        "PLAYED_S": 3,
        "FOOT_YD": 4,
        "TRAVEL_YD": 5,
        "REAL_STEPS": 6,
        "REAL_M": 7,
        "REAL_KCAL": 8,
        "REAL_S": 9,
        "WALKS": 10,
        "SINCE": 11,
        "INFO_FLAGS": 12,
        "NAME_0": 16,
        "NAME_1": 17,
        "NAME_2": 18,
        "NAME_3": 19,
    }
    assert {flag.name: flag.value for flag in InfoFlags} == {"ESTIMATED": 1}
    assert {faction.name: faction.value for faction in Faction} == {"UNKNOWN": 0, "ALLIANCE": 1, "HORDE": 2}


def test_the_cycle_sends_every_stat_once_and_starts_with_the_character():
    assert [stat.name for stat in STAT_CYCLE] == [
        "CHAR_KEY",
        "NAME_0",
        "NAME_1",
        "NAME_2",
        "NAME_3",
        "CHAR_INFO",
        "PLAYED_S",
        "FOOT_YD",
        "TRAVEL_YD",
        "REAL_STEPS",
        "REAL_M",
        "REAL_KCAL",
        "REAL_S",
        "WALKS",
        "SINCE",
        "INFO_FLAGS",
    ]
    assert sorted(STAT_CYCLE) == sorted(Stat)
    assert protocol.NAME_STATS == (Stat.NAME_0, Stat.NAME_1, Stat.NAME_2, Stat.NAME_3)
    # One stat frame every 5th redraw, ten redraws a second: the documented 8 seconds.
    assert len(STAT_CYCLE) * protocol.STAT_EVERY / 10 == 8.0


def test_known_stat_frame_bytes():
    frame = StatFrame(seq=0xAB, stat_id=Stat.FOOT_YD, value=0x123456789A)
    packed = frame.pack()
    assert len(packed) == 10
    # version 1, type 1, seq AB, id 4 (000100), value 12 3456 789A, reserved 0
    assert packed[:8].hex() == "16ac4123456789a0"
    assert int.from_bytes(packed[8:], "big") == crc16(packed[:8])
    assert "".join(map(str, frame.to_bits())) == reference_bits(frame)
    assert frame.payload() == packed[:8]


def test_round_trip_random_stat_frames():
    rng = random.Random(11)
    for _ in range(500):
        frame = random_stat_frame(rng)
        packed = frame.pack()
        assert decode(packed) == frame
        assert decode(list(packed)) == frame
        bits = frame.to_bits()
        assert len(bits) == 80 and set(bits) <= {0, 1}
        assert "".join(map(str, bits)) == reference_bits(frame)
        assert decode_bits(bits) == frame
        assert beacon.decode_cells(beacon.frame_to_cells(frame)) == frame


@pytest.mark.parametrize("name,width", list(SETTABLE.items()))
def test_every_field_round_trips_at_its_limits(name: str, width: int):
    top = (1 << width) - 1
    for value in (0, 1, top - 1, top):
        back = decode(StatFrame(**{name: value}).pack())
        assert isinstance(back, StatFrame)
        assert getattr(back, name) == value
        # ... and no other field is disturbed.
        for other in SETTABLE:
            if other != name:
                assert getattr(back, other) == getattr(StatFrame(), other)


@pytest.mark.parametrize("value", VALUES)
def test_values_up_to_40_bits_survive_exactly(value: int):
    for stat in Stat:
        back = decode(StatFrame(seq=200, stat_id=stat, value=value).pack())
        assert back == StatFrame(seq=200, stat_id=stat, value=value)
        assert back.stat is stat and back.value == value


@pytest.mark.parametrize("name,width", [*SETTABLE.items(), ("version", 4)])
def test_out_of_range_fields_are_refused(name: str, width: int):
    for value in (-1, 1 << width, (1 << width) + 7):
        with pytest.raises(FrameError):
            StatFrame(**{name: value}).pack()


def test_unknown_stat_ids_decode_but_name_no_stat():
    known = {stat.value for stat in Stat}
    for stat_id in range(64):
        back = decode(StatFrame(seq=1, stat_id=stat_id, value=77).pack())
        assert isinstance(back, StatFrame) and back.stat_id == stat_id and back.value == 77
        if stat_id in known:
            assert back.stat is Stat(stat_id)
        else:
            assert back.stat is None  # a newer addon's stat: valid, and meaningless to us
    assert StatFrame().stat is None  # id 0 is never sent


# ---- never a live frame ------------------------------------------------------------------


def test_decode_tells_the_two_kinds_apart():
    live = decode(Frame(seq=5, session=2, flags=Flags.ACTIVE, target_raw=320).pack())
    stat = decode(StatFrame(seq=6, stat_id=Stat.WALKS, value=12).pack())
    assert type(live) is Frame and live.frame_type == protocol.TYPE_LIVE
    assert type(stat) is StatFrame and stat.frame_type == protocol.TYPE_STAT
    assert decode_bits(Frame(seq=5).to_bits()) == Frame(seq=5)
    assert decode_bits(StatFrame(seq=6).to_bits()) == StatFrame(seq=6)


def test_a_stat_frame_has_nothing_a_live_frame_has():
    """Code written for live frames must fail loudly if it is ever handed a
    stat frame, not find a session, a flag or a speed in it."""
    assert not issubclass(StatFrame, Frame) and not issubclass(Frame, StatFrame)
    stat = StatFrame(seq=1, stat_id=Stat.FOOT_YD, value=5)
    for name in ("session", "flags", "target_raw", "char_speed_raw", "target_kmh", "char_speed_yps"):
        assert not hasattr(stat, name), name
    for name in ("active", "paused", "has"):
        assert not hasattr(stat, name), name
    assert stat != Frame(seq=1)
    with pytest.raises(TypeError):
        StatFrame(frame_type=protocol.TYPE_LIVE)  # nor can one be relabelled


def test_asking_for_a_live_frame_never_yields_a_stat_frame():
    rng = random.Random(12)
    for _ in range(300):
        frame = random_stat_frame(rng)
        assert isinstance(decode(frame.pack()), StatFrame)
        with pytest.raises(FrameError, match="not a live frame"):
            Frame.unpack(frame.pack())
        with pytest.raises(FrameError, match="not a live frame"):
            Frame.from_bits(frame.to_bits())
        with pytest.raises(FrameError, match="not a live frame"):
            beacon.cells_to_frame(beacon.frame_to_cells(frame))


def test_bits_that_would_start_the_belt_are_not_a_command_under_the_stat_type():
    """The worst case spelled out: the very bits of "session running, 40 km/h"
    arrive under frame type 1. They are a number for the picture."""
    go = Frame(seq=9, session=1, flags=Flags.ACTIVE | Flags.MOVING, target_raw=4095, char_speed_raw=350)
    assert Frame.unpack(go.pack()).active
    as_stat = retyped(go.pack(), protocol.TYPE_STAT)
    decoded = decode(as_stat)
    assert isinstance(decoded, StatFrame) and decoded.seq == 9
    with pytest.raises(FrameError):
        Frame.unpack(as_stat)
    with pytest.raises(FrameError):
        Frame.from_bits(bytes_to_bits(as_stat))


@pytest.mark.parametrize("frame_type", [2, 3])
def test_unassigned_frame_types_are_refused_everywhere(frame_type: int):
    rng = random.Random(13 + frame_type)
    samples = [raw_frame(1, frame_type, rng.getrandbits(58)) for _ in range(50)]
    samples.append(retyped(Frame(seq=9, session=1, flags=Flags.ACTIVE, target_raw=4095).pack(), frame_type))
    samples.append(retyped(StatFrame(seq=9, stat_id=Stat.FOOT_YD, value=5).pack(), frame_type))
    for packed in samples:
        assert int.from_bytes(packed[8:], "big") == crc16(packed[:8])  # the CRC itself is fine
        bits = bytes_to_bits(packed)
        cells = [*beacon.PREAMBLE, *bits, *beacon.POSTAMBLE]
        for refuse, argument in (
            (decode, packed),
            (decode_bits, bits),
            (Frame.unpack, packed),
            (Frame.from_bits, bits),
            (beacon.decode_cells, cells),
            (beacon.cells_to_frame, cells),
        ):
            with pytest.raises(FrameError, match="frame type"):
                refuse(argument)
        # On screen such a strip is no beacon at all.
        assert beacon.decode_row(beacon.render_row(RawStrip(packed), 3) + bytes(8)) is None


@pytest.mark.parametrize("version", [0, 2, 3, 15])
def test_other_protocol_versions_are_refused_even_with_a_valid_crc(version: int):
    packed = StatFrame(seq=9, stat_id=Stat.WALKS, value=3, version=version).pack()
    assert int.from_bytes(packed[8:], "big") == crc16(packed[:8])
    with pytest.raises(FrameError, match="version"):
        decode(packed)
    with pytest.raises(FrameError, match="version"):
        decode(raw_frame(version, protocol.TYPE_LIVE))


def test_wrong_length_is_refused():
    good = StatFrame(seq=3, stat_id=Stat.WALKS, value=1).pack()
    for bad in (b"", good[:-1], good + b"\x00", good * 2):
        with pytest.raises(FrameError):
            decode(bad)
    for count in (0, 79, 81):
        with pytest.raises(FrameError):
            decode_bits([0] * count)


def test_all_zero_and_all_one_frames_are_refused():
    for bits in ([0] * 80, [1] * 80):
        with pytest.raises(FrameError):
            decode_bits(bits)


# ---- what the CRC catches -----------------------------------------------------------------


def test_every_single_bit_flip_is_caught():
    """Including the two type bits: one wrong pixel cannot turn a stat frame
    into a live frame, or a live frame into a stat frame."""
    rng = random.Random(14)
    frames: list[Frame | StatFrame] = [random_stat_frame(rng) for _ in range(40)]
    frames.append(Frame(seq=77, session=3, flags=Flags.ACTIVE | Flags.MOVING, target_raw=322, char_speed_raw=350))
    for frame in frames:
        bits = frame.to_bits()
        for i in range(80):
            flipped = list(bits)
            flipped[i] ^= 1
            with pytest.raises(FrameError):
                decode_bits(flipped)


def test_every_double_bit_flip_is_caught():
    bits = StatFrame(seq=77, stat_id=Stat.FOOT_YD, value=123_456_789).to_bits()
    for i in range(80):
        for j in range(i + 1, 80):
            flipped = list(bits)
            flipped[i] ^= 1
            flipped[j] ^= 1
            with pytest.raises(FrameError):
                decode_bits(flipped)


def test_random_corruption_never_changes_the_kind_of_a_frame():
    rng = random.Random(15)
    changed_kind = 0
    for _ in range(20_000):
        frame: Frame | StatFrame = (
            random_stat_frame(rng)
            if rng.randrange(2)
            else Frame(seq=rng.randrange(256), target_raw=rng.randrange(4096))
        )
        bits = frame.to_bits()
        for _ in range(rng.randrange(1, 12)):
            bits[rng.randrange(80)] ^= 1
        try:
            decoded = decode_bits(bits)
        except FrameError:
            continue
        if type(decoded) is not type(frame):
            changed_kind += 1
    # Expected: 20000 * 2^-16 CRC false accepts, a quarter of them of the other kind = 0.08.
    assert changed_kind <= 2


# ---- what the values mean -------------------------------------------------------------------


def test_djb2_reference_values():
    assert djb2("") == 5381
    assert djb2("a") == 5381 * 33 + 97 == 177670
    assert djb2("hello") == 261238937  # the value every djb2 write-up gives
    assert djb2(b"hello") == djb2("hello") == djb2(bytearray(b"hello"))
    # Text is hashed as its UTF-8 bytes.
    assert djb2("Ёжик-Гордунни") == djb2("Ёжик-Гордунни".encode())
    assert djb2("é") == (5381 * 33 * 33 + 0xC3 * 33 + 0xA9) % 2**32
    # 32 bits, however long the text.
    assert 0 <= djb2("Thrall-Argent Dawn" * 50) < 2**32
    assert djb2("Thrall-Argent Dawn") != djb2("Thrall-ArgentDawn")
    assert decode(StatFrame(stat_id=Stat.CHAR_KEY, value=djb2("x" * 99)).pack()).value == djb2("x" * 99)


def test_char_info_packs_four_bytes():
    assert pack_char_info(60, 70, 11, Faction.HORDE) == 60 | 70 << 8 | 11 << 16 | 2 << 24 == 0x020B463C
    info = unpack_char_info(0x020B463C)
    assert info == CharInfo(level=60, max_level=70, class_id=11, faction=2)
    assert info.faction == Faction.HORDE
    assert unpack_char_info(0) == CharInfo(0, 0, 0, Faction.UNKNOWN)
    assert unpack_char_info(pack_char_info(255, 255, 255, 255)) == CharInfo(255, 255, 255, 255)
    # Bits above the four bytes are not ours to interpret.
    assert unpack_char_info(0xAB_020B463C) == info
    for bad in ((256, 1, 1, 1), (1, -1, 1, 1), (1, 1, 300, 1), (1, 1, 1, 256)):
        with pytest.raises(ValueError):
            pack_char_info(*bad)


@pytest.mark.parametrize(
    "name",
    ["Thrall", "A", "", "Абвгдежзик", "Ñandú", "Þórr", "阿尔萨斯", "a" * 20, "é" * 10, "日本語" * 2 + "ab"],
)
def test_names_that_fit_round_trip(name: str):
    assert len(name.encode()) <= 20
    chunks = name_to_chunks(name)
    assert len(chunks) == 4 and all(0 <= chunk <= STAT_MAX_VALUE for chunk in chunks)
    assert name_from_chunks(chunks) == name
    assert name_from_chunks(list(chunks)) == name


def test_name_bytes_are_laid_out_first_byte_first():
    assert name_to_chunks("A") == (0x41 << 32, 0, 0, 0)
    assert name_to_chunks("ABCDEF") == (0x4142434445, 0x46 << 32, 0, 0)
    twenty = "abcdefghijklmnopqrst"
    assert name_to_chunks(twenty) == tuple(int.from_bytes(twenty[i : i + 5].encode(), "big") for i in (0, 5, 10, 15))
    assert name_to_chunks("") == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "name,kept",
    [
        ("abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrst"),  # 26 bytes: plain cut
        ("Абвгдежзиклм", "Абвгдежзик"),  # 12 Cyrillic letters = 24 bytes: 10 fit
        ("a" * 19 + "é", "a" * 19),  # the 2-byte letter would end at byte 21
        ("a" * 18 + "€", "a" * 18),  # a 3-byte character straddling the limit
        ("a" * 17 + "😀", "a" * 17),  # a 4-byte one
        ("a" * 16 + "😀b", "a" * 16 + "😀"),  # ... which is kept whole when it ends at byte 20
        ("阿尔萨斯王子殿下", "阿尔萨斯王子"),  # 24 bytes of 3-byte characters: 6 fit
        ("😀" * 6, "😀" * 5),
    ],
)
def test_long_names_are_cut_between_characters(name: str, kept: str):
    assert len(name.encode()) > 20
    chunks = name_to_chunks(name)
    assert name_from_chunks(chunks) == kept
    assert len(kept.encode()) <= 20
    assert "�" not in name_from_chunks(chunks)  # never half a character


def test_name_from_chunks_is_lenient_about_content_and_strict_about_shape():
    # The name ends at the first zero byte, wherever the padding starts.
    assert name_from_chunks((0x4142000000, 0x4300000000, 0, 0)) == "AB"
    # Bytes that are not UTF-8 do not raise: this is text for a picture.
    assert name_from_chunks((0x41FF420000, 0, 0, 0)) == "A�B"
    for bad in ((), (1, 2, 3), (1, 2, 3, 4, 5)):
        with pytest.raises(ValueError):
            name_from_chunks(bad)
    for bad in ((-1, 0, 0, 0), (0, 0, 0, STAT_MAX_VALUE + 1)):
        with pytest.raises(ValueError):
            name_from_chunks(bad)


# ---- the addon's encoder ----------------------------------------------------------------------


def test_python_decodes_the_stat_frames_the_addon_packs(lua, addon_source):
    """Walka/Core/Protocol.lua (the addon side) against this decoder. (The
    addon's own test-suite, tests/addon/test_protocol_stat.py, goes further.)"""
    lua_protocol = load_addon_file(lua, addon_source / "Core" / "Protocol.lua").Protocol
    assert lua_protocol.TYPE_STAT == protocol.TYPE_STAT
    assert lua_to_py(lua_protocol.STAT) == {stat.name: stat.value for stat in Stat}
    assert lua_to_py(lua_protocol.STAT_CYCLE) == [stat.value for stat in STAT_CYCLE]

    rng = random.Random(16)
    for _ in range(300):
        frame = StatFrame(seq=rng.randrange(256), stat_id=rng.randrange(64), value=rng.getrandbits(40))
        packed = bytes(lua_to_py(lua_protocol.packStatBytes(frame.seq, frame.stat_id, frame.value)))
        assert packed == frame.pack()
        bits = lua_to_py(lua_protocol.packStatBits(frame.seq, frame.stat_id, frame.value, lua.table()))
        assert decode_bits(bits) == frame
        with pytest.raises(FrameError):
            Frame.from_bits(bits)
    for text in ("", "Thrall-Argent Dawn", "Ёжик-Гордунни", "阿尔萨斯-安苏"):
        assert lua_protocol.djb2(text) == djb2(text)
    for name in ("Thrall", "Абвгдежзиклм", "阿尔萨斯王子殿下", "a" * 19 + "é"):
        assert tuple(lua_to_py(lua_protocol.nameValues(name))) == name_to_chunks(name)
    assert lua_protocol.charInfo(60, 70, 11, 2) == pack_char_info(60, 70, 11, 2)
