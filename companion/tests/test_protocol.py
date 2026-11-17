"""The beacon frame codec: layout, range limits and what the CRC catches.

These are the tests of the live frame. The stat frame, and everything that
keeps the two kinds apart, is in test_protocol_stat.py.
"""

from __future__ import annotations

import random

import pytest

from walka import beacon, protocol
from walka.protocol import Flags, Frame, FrameError, bits_to_bytes, bytes_to_bits, crc16
from walka_fakes import load_addon_file, lua_to_py

FIELD_WIDTHS = {
    "version": 4,
    "frame_type": 2,
    "seq": 8,
    "session": 8,
    "flags": 16,
    "target_raw": 12,
    "char_speed_raw": 12,
    "reserved": 2,
}
# What a caller may choose. The version is 1 and the type of a live frame is
# "live": neither is a matter of choice.
SETTABLE = {name: width for name, width in FIELD_WIDTHS.items() if name not in ("version", "frame_type")}


def random_frame(rng: random.Random) -> Frame:
    return Frame(
        seq=rng.randrange(256),
        session=rng.randrange(256),
        flags=Flags(rng.randrange(1 << 16)),
        target_raw=rng.randrange(4096),
        char_speed_raw=rng.randrange(4096),
        reserved=rng.randrange(4),
    )


def reference_bits(frame: Frame) -> str:
    """The documented layout (docs/PROTOCOL.md), written out independently."""
    payload = (
        f"{frame.version:04b}{frame.frame_type:02b}{frame.seq:08b}{frame.session:08b}"
        f"{int(frame.flags):016b}{frame.target_raw:012b}{frame.char_speed_raw:012b}{frame.reserved:02b}"
    )
    assert len(payload) == 64
    data = int(payload, 2).to_bytes(8, "big")
    # CRC-16/CCITT-FALSE, bit by bit.
    crc = 0xFFFF
    for bit in payload:
        top = (crc >> 15) & 1
        crc = (crc << 1) & 0xFFFF
        if top ^ int(bit):
            crc ^= 0x1021
    assert crc == crc16(data)
    return payload + f"{crc:016b}"


# ---- CRC -----------------------------------------------------------------------


def test_crc16_check_value():
    assert crc16(b"123456789") == 0x29B1
    assert crc16(b"") == 0xFFFF
    assert crc16(bytes(8)) == crc16([0] * 8)


def test_constants_match_the_documented_format():
    assert protocol.VERSION == 1
    assert protocol.FRAME_BYTES == 10 and protocol.PAYLOAD_BYTES == 8 and protocol.FRAME_BITS == 80
    assert sum(FIELD_WIDTHS.values()) == 64
    assert protocol.TARGET_UNIT_KMH == 0.01 and protocol.CHAR_UNIT_YPS == 0.02
    # Flag values are part of the wire format.
    assert {f.name: f.value for f in Flags} == {
        "ACTIVE": 1,
        "PAUSED": 2,
        "MOVING": 4,
        "MOUNTED": 8,
        "TAXI": 16,
        "SWIMMING": 32,
        "FLYING": 64,
        "DEAD": 128,
        "COMBAT": 256,
        "AFK": 512,
        "FALLING": 1024,
        "RESTING": 2048,
        "ESTIMATED": 4096,
    }


# ---- layout and round trips ------------------------------------------------------


def test_known_frame_bytes():
    frame = Frame(seq=0xAB, session=0x12, flags=Flags.ACTIVE | Flags.MOVING, target_raw=320, char_speed_raw=350)
    packed = frame.pack()
    assert len(packed) == 10
    # version 1, type 0, seq AB, session 12, flags 0005, target 0x140, char 0x15E, reserved 0
    assert packed[:8].hex() == "12ac480014500578"
    assert int.from_bytes(packed[8:], "big") == crc16(packed[:8])
    assert "".join(map(str, frame.to_bits())) == reference_bits(frame)


def test_round_trip_random_frames():
    rng = random.Random(1)
    for _ in range(500):
        frame = random_frame(rng)
        packed = frame.pack()
        assert Frame.unpack(packed) == frame
        assert Frame.unpack(list(packed)) == frame
        bits = frame.to_bits()
        assert len(bits) == 80 and set(bits) <= {0, 1}
        assert "".join(map(str, bits)) == reference_bits(frame)
        assert Frame.from_bits(bits) == frame
        assert beacon.cells_to_frame(beacon.frame_to_cells(frame)) == frame


@pytest.mark.parametrize("name,width", list(SETTABLE.items()))
def test_every_field_round_trips_at_its_limits(name: str, width: int):
    top = (1 << width) - 1
    for value in (0, 1, top - 1, top):
        frame = Frame(**{name: Flags(value) if name == "flags" else value})
        back = Frame.unpack(frame.pack())
        assert int(getattr(back, name)) == value
        # ... and no other field is disturbed.
        for other in FIELD_WIDTHS:
            if other != name:
                assert int(getattr(back, other)) == int(getattr(Frame(), other))


@pytest.mark.parametrize("name,width", [*SETTABLE.items(), ("version", 4)])
def test_out_of_range_fields_are_refused(name: str, width: int):
    for value in (-1, 1 << width, (1 << width) + 7):
        with pytest.raises(FrameError):
            Frame(**{name: value}).pack()


def test_a_live_frame_cannot_be_given_another_type():
    """The type is not a field: there is no way to build a Frame that would
    be packed - or passed around - as anything but a live frame."""
    assert Frame.frame_type == protocol.TYPE_LIVE == 0
    assert Frame(seq=3).frame_type == protocol.TYPE_LIVE
    with pytest.raises(TypeError):
        Frame(frame_type=protocol.TYPE_STAT)
    with pytest.raises(AttributeError):  # frozen, like every other attribute
        Frame().frame_type = protocol.TYPE_STAT
    assert (Frame(seq=3).pack()[0] >> 2) & 0b11 == protocol.TYPE_LIVE


def test_unknown_flag_bits_survive():
    frame = Frame(flags=Flags(0xE000 | Flags.ACTIVE))
    back = Frame.unpack(frame.pack())
    assert int(back.flags) == 0xE001 and back.active


def test_accessors():
    frame = Frame(flags=Flags.ACTIVE | Flags.PAUSED | Flags.AFK, target_raw=322, char_speed_raw=350)
    assert frame.active and frame.paused and frame.has(Flags.AFK) and not frame.has(Flags.MOVING)
    assert frame.target_kmh == pytest.approx(3.22)
    assert frame.char_speed_yps == pytest.approx(7.0)
    idle = Frame()
    assert not idle.active and not idle.paused and idle.target_kmh == 0 and idle.char_speed_yps == 0
    # The widest values the fields can carry.
    assert Frame(target_raw=4095).target_kmh == pytest.approx(40.95)
    assert Frame(char_speed_raw=4095).char_speed_yps == pytest.approx(81.9)


def test_raw_conversions_round_and_clamp():
    assert protocol.kmh_to_raw(3.2) == 320
    assert protocol.kmh_to_raw(3.204) == 320 and protocol.kmh_to_raw(3.206) == 321
    assert protocol.kmh_to_raw(-1) == 0 and protocol.kmh_to_raw(0) == 0
    assert protocol.kmh_to_raw(40.95) == 4095 and protocol.kmh_to_raw(1e9) == 4095
    assert protocol.yps_to_raw(7.0) == 350
    assert protocol.yps_to_raw(-3) == 0 and protocol.yps_to_raw(1e6) == 4095
    for raw in range(0, 4096, 37):
        assert protocol.kmh_to_raw(Frame(target_raw=raw).target_kmh) == raw
        assert protocol.yps_to_raw(Frame(char_speed_raw=raw).char_speed_yps) == raw


def test_bit_helpers():
    assert bytes_to_bits(b"\x80\x01") == [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert bits_to_bytes([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]) == b"\x80\x01"
    assert bits_to_bytes([]) == b""
    with pytest.raises(FrameError):
        bits_to_bytes([1, 0, 1])
    data = bytes(range(256))
    assert bits_to_bytes(bytes_to_bits(data)) == data


# ---- rejection -------------------------------------------------------------------


def test_wrong_length_is_refused():
    good = Frame(seq=3).pack()
    for bad in (b"", good[:-1], good + b"\x00", good * 2):
        with pytest.raises(FrameError):
            Frame.unpack(bad)
    with pytest.raises(FrameError):
        Frame.from_bits([0] * 79)
    with pytest.raises(FrameError):
        Frame.from_bits([0] * 81)


@pytest.mark.parametrize("version", [0, 2, 3, 15])
def test_other_protocol_versions_are_refused_even_with_a_valid_crc(version: int):
    packed = Frame(seq=9, flags=Flags.ACTIVE, target_raw=300, version=version).pack()
    assert int.from_bytes(packed[8:], "big") == crc16(packed[:8])  # the CRC itself is fine
    with pytest.raises(FrameError, match="version"):
        Frame.unpack(packed)


def test_all_zero_and_all_one_frames_are_refused():
    for cells in ([0] * 80, [1] * 80):
        with pytest.raises(FrameError):
            Frame.from_bits(cells)


def test_every_single_bit_flip_is_caught():
    rng = random.Random(2)
    for _ in range(40):
        bits = random_frame(rng).to_bits()
        # (reserved is free in random_frame; version and frame type are fixed)
        for i in range(80):
            flipped = list(bits)
            flipped[i] ^= 1
            with pytest.raises(FrameError):
                Frame.from_bits(flipped)


def test_every_double_bit_flip_is_caught():
    bits = Frame(seq=77, session=3, flags=Flags.ACTIVE | Flags.MOVING, target_raw=322, char_speed_raw=350).to_bits()
    for i in range(80):
        for j in range(i + 1, 80):
            flipped = list(bits)
            flipped[i] ^= 1
            flipped[j] ^= 1
            with pytest.raises(FrameError):
                Frame.from_bits(flipped)


def test_every_burst_up_to_16_bits_is_caught():
    rng = random.Random(3)
    bits = random_frame(rng).to_bits()
    for length in range(2, 17):
        for start in range(0, 80 - length + 1):
            # A burst: first and last bit flipped, anything in between.
            for _ in range(4):
                flipped = list(bits)
                flipped[start] ^= 1
                flipped[start + length - 1] ^= 1
                for k in range(start + 1, start + length - 1):
                    flipped[k] ^= rng.randrange(2)
                with pytest.raises(FrameError):
                    Frame.from_bits(flipped)


def test_random_corruption_of_a_real_frame_is_caught():
    rng = random.Random(4)
    accepted = 0
    trials = 20_000
    for _ in range(trials):
        frame = random_frame(rng)
        bits = frame.to_bits()
        for _ in range(rng.randrange(1, 12)):
            bits[rng.randrange(80)] ^= 1
        try:
            decoded = Frame.from_bits(bits)
        except FrameError:
            continue
        if decoded != frame:
            accepted += 1
    # Expected false accepts: trials * 2^-16 = 0.3; flips that cancel out are not counted.
    assert accepted <= 4


def test_false_accept_rate_of_random_bits():
    """Random screen content must practically never look like a frame."""
    rng = random.Random(5)
    trials = 1 << 16
    crc_ok = 0
    accepted = 0
    for _ in range(trials):
        data = rng.getrandbits(80).to_bytes(10, "big")
        if crc16(data[:8]) == int.from_bytes(data[8:], "big"):
            crc_ok += 1
            try:
                Frame.unpack(data)
                accepted += 1
            except FrameError:
                pass
    # The CRC alone passes 2^-16 of random strings: 1 expected here. The
    # version nibble and the frame type cut what is finally accepted as a
    # live frame by another factor of 64.
    assert crc_ok <= 8, f"CRC false-accept rate {crc_ok / trials:.2e} is far above 2^-16"
    assert accepted <= 2


def test_guard_cells_add_protection_on_top_of_the_crc():
    """Preamble (4 cells) + postamble (2 cells) must also match: 2^-6 more."""
    rng = random.Random(6)
    accepted = 0
    guards_ok = 0
    trials = 60_000
    for _ in range(trials):
        value = rng.getrandbits(beacon.TOTAL_CELLS)
        cells = [(value >> i) & 1 for i in range(beacon.TOTAL_CELLS)]
        if tuple(cells[:4]) == beacon.PREAMBLE and tuple(cells[-2:]) == beacon.POSTAMBLE:
            guards_ok += 1
        try:
            beacon.cells_to_frame(cells)
            accepted += 1
        except FrameError:
            pass
    assert 0.5 * trials / 64 < guards_ok < 1.5 * trials / 64  # the guards really are a 1/64 filter
    assert accepted == 0  # expected: trials * 2^-26 = 0.0009


# ---- the addon's encoder ---------------------------------------------------------


def test_python_decodes_what_the_addon_packs(lua, addon_source):
    """Walka/Core/Protocol.lua (the addon side) against this decoder."""
    ns = load_addon_file(lua, addon_source / "Core" / "Protocol.lua")
    lua_protocol = ns.Protocol
    assert lua_protocol.VERSION == protocol.VERSION
    assert lua_protocol.FRAME_BITS == protocol.FRAME_BITS
    for name, value in lua_to_py(lua_protocol.FLAG).items():
        assert Flags[name].value == value
    assert lua_protocol.crc16(lua.table(*b"123456789")) == 0x29B1

    rng = random.Random(7)
    for _ in range(300):
        frame = Frame(
            seq=rng.randrange(256),
            session=rng.randrange(256),
            flags=Flags(rng.randrange(1 << 16)),
            target_raw=rng.randrange(4096),
            char_speed_raw=rng.randrange(4096),
        )
        table = lua.table(
            seq=frame.seq,
            session=frame.session,
            flags=int(frame.flags),
            target=frame.target_raw,
            charSpeed=frame.char_speed_raw,
        )
        packed = bytes(lua_to_py(lua_protocol.packBytes(table)))
        assert packed == frame.pack()
        bits = lua_to_py(lua_protocol.packBits(table, lua.table()))
        assert Frame.from_bits(bits) == frame
    # The addon's unit conversions agree with ours (including the clamp).
    # (Exact .5 ties are avoided: Lua rounds them up, Python to even. The
    # companion only decodes, so that difference never reaches the belt.)
    for kmh in (0, 0.004, 0.006, 1.6, 3.218688, 6.437376, 40.95, 41, 1000, -3):
        assert lua_protocol.kmhToRaw(kmh) == protocol.kmh_to_raw(kmh)
    for yps in (0, 2.5, 7.0, 14.0, 81.9, 82, 1e6, -1):
        assert lua_protocol.ypsToRaw(yps) == protocol.yps_to_raw(yps)
