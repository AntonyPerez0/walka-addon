"""Core/Protocol.lua against the companion's codec (walka.protocol)."""

from __future__ import annotations

import random

import pytest
from walka import beacon, protocol
from walka.protocol import Flags, Frame

from .harness import lua_list


def lua_frame(rt, frame: Frame):
    return rt.to_lua(
        {
            "seq": frame.seq,
            "session": frame.session,
            "flags": int(frame.flags),
            "target": frame.target_raw,
            "charSpeed": frame.char_speed_raw,
        }
    )


def test_crc_check_value(rt, ns):
    data = rt.to_lua(list(b"123456789"))
    assert ns.Protocol.crc16(data) == 0x29B1


def test_crc_matches_python_on_random_data(rt, ns):
    rng = random.Random(1)
    for _ in range(200):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 24)))
        assert ns.Protocol.crc16(rt.to_lua(list(data))) == protocol.crc16(data)


def test_crc_respects_the_count_argument(rt, ns):
    data = rt.to_lua(list(b"123456789xyz"))
    assert ns.Protocol.crc16(data, 9) == 0x29B1


def test_constants_agree_with_the_companion(ns):
    lua = ns.Protocol
    assert lua.VERSION == protocol.VERSION
    assert lua.FRAME_BYTES == protocol.FRAME_BYTES
    assert lua.PAYLOAD_BYTES == protocol.PAYLOAD_BYTES
    assert lua.FRAME_BITS == protocol.FRAME_BITS
    assert lua.TARGET_UNIT_KMH == protocol.TARGET_UNIT_KMH
    assert lua.CHAR_UNIT_YPS == protocol.CHAR_UNIT_YPS
    assert lua.TARGET_MAX_RAW == protocol.TARGET_MAX_RAW
    assert lua.CHAR_MAX_RAW == protocol.CHAR_MAX_RAW
    assert {str(name): int(value) for name, value in lua.FLAG.items()} == {flag.name: flag.value for flag in Flags}


def test_random_frames_pack_like_python_and_decode_back(rt, ns):
    rng = random.Random(2)
    for _ in range(300):
        frame = Frame(
            seq=rng.randrange(256),
            session=rng.randrange(256),
            flags=Flags(rng.randrange(1 << 16)),
            target_raw=rng.randrange(4096),
            char_speed_raw=rng.randrange(4096),
        )
        packed = bytes(int(value) for value in lua_list(ns.Protocol.packBytes(lua_frame(rt, frame))))
        assert packed == frame.pack()
        assert Frame.unpack(packed) == frame
        bits = [int(bit) for bit in lua_list(ns.Protocol.packBits(lua_frame(rt, frame), rt.lua.table()))]
        assert bits == frame.to_bits()
        assert set(bits) <= {0, 1}


def test_pack_bits_reuses_the_output_table(rt, ns):
    out = rt.lua.table()
    first = Frame(seq=1, flags=Flags(0xFFFF), target_raw=4095, char_speed_raw=4095, session=255)
    second = Frame(seq=2)
    returned = ns.Protocol.packBits(lua_frame(rt, first), out)
    assert rt.lua.globals().rawequal(returned, out)
    ns.Protocol.packBits(lua_frame(rt, second), out)
    assert len(out) == 80
    assert [int(bit) for bit in lua_list(out)] == second.to_bits()


def test_out_of_range_fields_are_clamped_or_wrapped_not_corrupting(rt, ns):
    # Speeds clamp, counters wrap; whatever goes in, the frame stays valid.
    packed = ns.Protocol.packBytes(
        rt.to_lua({"seq": 256 + 7, "session": 300, "flags": 65536 + 5, "target": 99999, "charSpeed": -4})
    )
    frame = Frame.unpack(bytes(int(value) for value in lua_list(packed)))
    assert frame == Frame(seq=7, session=300 % 256, flags=Flags(5), target_raw=4095, char_speed_raw=0)


def test_missing_fields_default_to_zero(rt, ns):
    packed = ns.Protocol.packBytes(rt.lua.table())
    assert Frame.unpack(bytes(int(value) for value in lua_list(packed))) == Frame()


@pytest.mark.parametrize("kmh", [0, 0.6, 0.97, 1.0, 1.6, 2.4, 3.2, 4.0, 4.8, 6.4, 12.34, 40.95, 41.0, 500, -3])
def test_kmh_to_raw_matches_the_companion(ns, kmh):
    assert ns.Protocol.kmhToRaw(kmh) == protocol.kmh_to_raw(kmh)


@pytest.mark.parametrize("yps", [0, 2.5, 4.7222, 7.0, 8.75, 14.0, 26.6, 81.9, 82.0, 1000, -1])
def test_yps_to_raw_matches_the_companion(ns, yps):
    assert ns.Protocol.ypsToRaw(yps) == protocol.yps_to_raw(yps)


def test_raw_conversions_tolerate_nil(ns):
    assert ns.Protocol.kmhToRaw(None) == 0
    assert ns.Protocol.ypsToRaw(None) == 0


def test_beacon_layout_constants_agree(ns):
    assert ns.Beacon.TOTAL_CELLS == beacon.TOTAL_CELLS
    assert ns.Beacon.DEFAULT_CELL_PX == beacon.DEFAULT_CELL_PX
    assert ns.Beacon.MIN_CELL_PX >= beacon.MIN_CELL_PX
    assert ns.Beacon.MAX_CELL_PX <= beacon.MAX_CELL_PX
