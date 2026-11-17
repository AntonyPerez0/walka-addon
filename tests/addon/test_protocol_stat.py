"""Stat frames: Core/Protocol.lua packs, the companion's codec decodes.

A stat frame carries one number of the character's journey (docs/PROTOCOL.md,
"Stat frames"). The addon has no bit library and only doubles to count with,
so the vectors here go to the edges of the 40-bit value on purpose.
"""

from __future__ import annotations

import math
import random

import pytest
from walka import beacon, protocol
from walka.protocol import Frame, FrameError, Stat, StatFrame
from walka.sources import BeaconSource

from .harness import lua_dict, lua_list

# 0 and 1, the largest hash (CHAR_KEY is 32 bits), and the largest value there is.
EDGE_VALUES = [0, 1, 2**32 - 1, 2**40 - 1]
MORE_VALUES = [255, 256, 65535, 2**24, 2**31, 2**32, 2**32 + 1, 2**39, 2**40 - 2, 1_763_424_000]

NAMES = [
    "Thrall",
    "A",
    "",
    "Ñandú",
    "Þórr",
    "Абвгдежзик",  # 10 Cyrillic letters: exactly 20 bytes
    "Абвгдежзиклм",  # 12: the longest name the game allows, 24 bytes
    "阿尔萨斯",
    "阿尔萨斯王子殿下",  # 24 bytes of 3-byte characters
    "아서스왕자님",
    "abcdefghijklmnopqrst",  # exactly 20 bytes
    "abcdefghijklmnopqrstuvwxyz",
    "a" * 19 + "é",  # a 2-byte letter across the limit
    "a" * 18 + "€",  # a 3-byte one
    "a" * 17 + "😀",  # a 4-byte one
    "a" * 16 + "😀b",  # ... and one that ends exactly at the limit
    "😀" * 6,
]


def packed(ns, seq, stat_id, value) -> bytes:
    return bytes(int(byte) for byte in lua_list(ns.Protocol.packStatBytes(seq, stat_id, value)))


def unpacked(ns, seq, stat_id, value) -> StatFrame:
    frame = protocol.decode(packed(ns, seq, stat_id, value))
    assert isinstance(frame, StatFrame)
    return frame


# ---- constants ----------------------------------------------------------------------------------


def test_constants_agree_with_the_companion(ns):
    lua = ns.Protocol
    assert lua.TYPE_LIVE == protocol.TYPE_LIVE == 0
    assert lua.TYPE_STAT == protocol.TYPE_STAT == 1
    assert {str(name): int(value) for name, value in lua.STAT.items()} == {stat.name: stat.value for stat in Stat}
    assert [int(stat) for stat in lua_list(lua.STAT_CYCLE)] == [stat.value for stat in protocol.STAT_CYCLE]
    assert lua.STAT_MAX_ID == protocol.STAT_MAX_ID
    assert lua.STAT_MAX_VALUE == protocol.STAT_MAX_VALUE == 2**40 - 1
    assert lua.STAT_EVERY == protocol.STAT_EVERY
    assert lua.NAME_BYTES == protocol.NAME_BYTES and lua.NAME_CHUNK_BYTES == protocol.NAME_CHUNK_BYTES
    assert lua_dict(lua.INFO_FLAG) == {flag.name: flag.value for flag in protocol.InfoFlags}
    assert lua_dict(lua.FACTION) == {faction.name: faction.value for faction in protocol.Faction}
    widths = [(str(field[1]), int(field[2])) for field in lua_list(lua.STAT_FIELDS)]
    assert widths == [("version", 4), ("frameType", 2), ("seq", 8), ("statId", 6), ("value", 40), ("reserved", 4)]


# ---- packing -------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", EDGE_VALUES + MORE_VALUES)
def test_every_stat_id_carries_every_edge_value_exactly(ns, value):
    for stat_id in range(64):
        seq = (stat_id * 37 + 11) % 256
        frame = unpacked(ns, seq, stat_id, value)
        assert frame == StatFrame(seq=seq, stat_id=stat_id, value=value)
        assert frame.stat is (Stat(stat_id) if stat_id in set(Stat) else None)
        assert packed(ns, seq, stat_id, value) == frame.pack()


def test_random_stat_frames_pack_like_python_and_decode_back(rt, ns):
    rng = random.Random(21)
    for _ in range(300):
        frame = StatFrame(seq=rng.randrange(256), stat_id=rng.randrange(64), value=rng.getrandbits(40))
        assert packed(ns, frame.seq, frame.stat_id, frame.value) == frame.pack()
        bits = [int(bit) for bit in lua_list(ns.Protocol.packStatBits(frame.seq, frame.stat_id, frame.value, rt.lua.table()))]
        assert bits == frame.to_bits()
        assert set(bits) <= {0, 1}
        assert protocol.decode_bits(bits) == frame


def test_the_companion_never_reads_a_packed_stat_frame_as_live(ns):
    rng = random.Random(22)
    for _ in range(100):
        data = packed(ns, rng.randrange(256), rng.choice(list(Stat)), rng.getrandbits(40))
        with pytest.raises(FrameError, match="not a live frame"):
            Frame.unpack(data)


def test_the_live_packer_cannot_be_talked_into_another_frame_type(rt, ns):
    for frame_type in (1, 2, 3):
        table = rt.to_lua({"frameType": frame_type, "seq": 7, "session": 2, "flags": 5, "target": 320, "charSpeed": 350})
        data = bytes(int(byte) for byte in lua_list(ns.Protocol.packBytes(table)))
        assert Frame.unpack(data) == Frame(seq=7, session=2, flags=protocol.Flags(5), target_raw=320, char_speed_raw=350)


def test_pack_stat_bits_reuses_the_output_table(rt, ns):
    out = rt.lua.table()
    returned = ns.Protocol.packStatBits(1, Stat.FOOT_YD, 2**40 - 1, out)
    assert rt.lua.globals().rawequal(returned, out)
    ns.Protocol.packStatBits(2, Stat.WALKS, 0, out)
    assert len(out) == 80
    assert [int(bit) for bit in lua_list(out)] == StatFrame(seq=2, stat_id=Stat.WALKS, value=0).to_bits()
    # Without a table to reuse it makes one.
    assert len(ns.Protocol.packStatBits(3, Stat.WALKS, 9)) == 80


def test_live_and_stat_frames_share_one_buffer_without_leaking_into_each_other(rt, ns):
    """The beacon paints both kinds from the same scratch table."""
    out = rt.lua.table()
    live = Frame(seq=4, session=255, flags=protocol.Flags(0xFFFF), target_raw=4095, char_speed_raw=4095)
    live_table = rt.to_lua({"seq": 4, "session": 255, "flags": 0xFFFF, "target": 4095, "charSpeed": 4095})
    for _ in range(3):
        ns.Protocol.packBits(live_table, out)
        assert [int(bit) for bit in lua_list(out)] == live.to_bits()
        ns.Protocol.packStatBits(5, Stat.CHAR_KEY, 0, out)
        assert [int(bit) for bit in lua_list(out)] == StatFrame(seq=5, stat_id=Stat.CHAR_KEY, value=0).to_bits()


def test_packing_a_stat_frame_allocates_nothing(rt):
    grown = rt.run(
        "local protocol = ... "
        "local out = {} "
        "protocol.packStatBits(0, 4, 1, out) "
        "collectgarbage('collect') collectgarbage('collect') collectgarbage('stop') "
        "local before = collectgarbage('count') "
        "for i = 1, 2000 do protocol.packStatBits(i, 1 + i % 19, i * 536870923, out) end "
        "local after = collectgarbage('count') "
        "collectgarbage('restart') "
        "return (after - before) * 1024",
        rt.ns.Protocol,
    )
    assert grown == 0, f"packStatBits left {grown:.0f} bytes of garbage in 2000 calls"


# ---- whatever goes in, the frame stays valid -----------------------------------------------------


@pytest.mark.parametrize(
    "value,sent",
    [
        (-1, 0),
        (-1e30, 0),
        (-math.inf, 0),
        (math.nan, 0),
        (None, 0),
        ("many", 0),
        (0.4, 0),
        (0.5, 1),  # rounded like the fields of the live frame: halves go up
        (1234.49, 1234),
        (1234.5, 1235),
        ("42", 42),
        (2**40 - 1.6, 2**40 - 2),
        (2**40 - 0.5, 2**40 - 1),
        (2**40, 2**40 - 1),
        (2**53, 2**40 - 1),
        (1e300, 2**40 - 1),
        (math.inf, 2**40 - 1),
    ],
)
def test_values_are_rounded_and_clamped_to_the_field(ns, value, sent):
    assert unpacked(ns, 9, Stat.FOOT_YD, value) == StatFrame(seq=9, stat_id=Stat.FOOT_YD, value=sent)


@pytest.mark.parametrize("stat_id", [None, -1, 64, 65, 1000, 2.5, math.nan, math.inf, "x"])
def test_an_impossible_stat_id_is_sent_as_zero_which_means_nothing(ns, stat_id):
    """Never as some other stat: 65 must not wrap round to CHAR_KEY."""
    frame = unpacked(ns, 9, stat_id, 123)
    assert frame.stat_id == 0 and frame.stat is None and frame.value == 123


@pytest.mark.parametrize(
    "seq,sent",
    [(0, 0), (255, 255), (256, 0), (256 + 7, 7), (-1, 255), (7.9, 7), (None, 0), (math.nan, 0), (math.inf, 0), (-math.inf, 0)],
)
def test_seq_wraps_like_the_live_frames_counter(ns, seq, sent):
    assert unpacked(ns, seq, Stat.WALKS, 5).seq == sent


# ---- CHAR_KEY ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a",
        "hello",
        "Thrall-Argent Dawn",
        "Thrall-ArgentDawn",
        "Ёжик-Гордунни",
        "阿尔萨斯-安苏",
        "Þórr-Kil'jaeden",
        "x" * 500,  # the hash has long since wrapped round 2^32
        "😀" * 40,
    ],
)
def test_djb2_matches_the_companion(ns, text):
    assert ns.Protocol.djb2(text) == protocol.djb2(text)
    assert 0 <= ns.Protocol.djb2(text) < 2**32


def test_djb2_reference_values_and_odd_input(ns):
    assert ns.Protocol.djb2("") == 5381
    assert ns.Protocol.djb2("hello") == 261238937
    assert ns.Protocol.djb2(None) == 5381  # nothing to hash
    assert ns.Protocol.djb2(12) == protocol.djb2("12")


def test_the_character_key_survives_the_trip(ns):
    for text in ("Thrall-Argent Dawn", "Ёжик-Гордунни", "x" * 500):
        key = ns.Protocol.djb2(text)
        assert unpacked(ns, 1, ns.Protocol.STAT.CHAR_KEY, key) == StatFrame(seq=1, stat_id=Stat.CHAR_KEY, value=protocol.djb2(text))


# ---- CHAR_INFO -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts",
    [(1, 60, 1, 1), (60, 60, 11, 2), (80, 80, 13, 0), (255, 255, 255, 255), (0, 0, 0, 0), (37, 70, 4, 2)],
)
def test_char_info_matches_the_companion(ns, parts):
    value = ns.Protocol.charInfo(*parts)
    assert value == protocol.pack_char_info(*parts)
    assert tuple(protocol.unpack_char_info(unpacked(ns, 1, Stat.CHAR_INFO, value).value)) == parts


def test_char_info_tolerates_what_the_game_may_not_tell(ns):
    info = protocol.unpack_char_info(int(ns.Protocol.charInfo(None, None, None, None)))
    assert info == protocol.CharInfo(0, 0, 0, protocol.Faction.UNKNOWN)
    # Each part stays inside its own byte, whatever it is given.
    info = protocol.unpack_char_info(int(ns.Protocol.charInfo(300, -4, math.nan, 2.4)))
    assert info == protocol.CharInfo(255, 0, 0, protocol.Faction.HORDE)
    assert ns.Protocol.charInfo(60, 60, 11, ns.Protocol.FACTION.ALLIANCE) == protocol.pack_char_info(60, 60, 11, 1)


# ---- NAME_0 .. NAME_3 ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_name_values_match_the_companion(ns, name):
    values = tuple(int(value) for value in lua_list(ns.Protocol.nameValues(name)))
    assert len(values) == 4
    assert values == protocol.name_to_chunks(name)


@pytest.mark.parametrize("name", NAMES)
def test_a_name_survives_the_trip_cut_only_between_characters(ns, name):
    """The whole path: split in Lua, four stat frames, put together in Python."""
    values = ns.Protocol.nameValues(name)
    received = []
    for index, stat in enumerate(protocol.NAME_STATS, start=1):
        frame = unpacked(ns, index, stat, values[index])
        assert frame.stat is stat
        received.append(frame.value)
    shown = protocol.name_from_chunks(received)
    assert name.startswith(shown)
    assert "�" not in shown, "a character was cut in half"
    encoded = name.encode()
    if len(encoded) <= 20:
        assert shown == name
    else:
        # As much as fits: the next character would not have.
        assert len(shown.encode()) <= 20 < len((shown + name[len(shown)]).encode())


def test_name_values_reuse_the_output_table_and_tolerate_no_name(rt, ns):
    out = rt.lua.table()
    returned = ns.Protocol.nameValues("abcdefghijklmnopqrst", out)
    assert rt.lua.globals().rawequal(returned, out)
    ns.Protocol.nameValues("Bo", out)
    assert [int(value) for value in lua_list(out)] == list(protocol.name_to_chunks("Bo"))  # nothing left of the longer name
    for nothing in (None, 12, True):
        assert [int(value) for value in lua_list(ns.Protocol.nameValues(nothing))] == [0, 0, 0, 0]


# ---- end to end -----------------------------------------------------------------------------------


class Strip:
    """80 bits from the addon's packer, to be drawn by the companion's renderer."""

    def __init__(self, bits) -> None:
        self.bits = [int(bit) for bit in lua_list(bits)]

    def to_bits(self) -> list[int]:
        return self.bits


def test_a_whole_cycle_travels_from_the_packer_to_the_companions_handler(rt, ns):
    """Packed in Lua, drawn as pixels, read back by the companion's decoder
    and routed by its beacon source: live frames to the reading the belt
    follows, stat frames to ``on_stat`` - in the documented rhythm of one
    stat frame every fifth redraw."""
    lua = ns.Protocol
    stat_ids = lua.STAT
    name = "Абвгдежзиклм"
    name_values = lua.nameValues(name)
    journey = {
        Stat.CHAR_KEY: lua.djb2(name + "-Гордунни"),
        Stat.NAME_0: name_values[1],
        Stat.NAME_1: name_values[2],
        Stat.NAME_2: name_values[3],
        Stat.NAME_3: name_values[4],
        Stat.CHAR_INFO: lua.charInfo(47, 60, 8, lua.FACTION.HORDE),
        Stat.PLAYED_S: 9 * 86400 + 3600,
        Stat.FOOT_YD: 2_345_678.4,
        Stat.TRAVEL_YD: 2**40 + 5,  # further than the field can say
        Stat.REAL_STEPS: 123_456,
        Stat.REAL_M: 98_765.5,
        Stat.REAL_KCAL: 4321,
        Stat.REAL_S: 86_400,
        Stat.WALKS: 37,
        Stat.SINCE: 1_763_424_000,
        Stat.INFO_FLAGS: lua.INFO_FLAG.ESTIMATED,
    }
    assert {int(stat_ids[stat.name]) for stat in journey} == {stat.value for stat in Stat}

    now = [1000.0]
    source = BeaconSource(None, clock=lambda: now[0])
    received: dict[Stat, int] = {}
    order: list[Stat] = []

    def on_stat(stat_id: int, value: int) -> None:
        received[Stat(stat_id)] = value
        order.append(Stat(stat_id))

    source.on_stat = on_stat
    bits = rt.lua.table()  # one buffer for both kinds, as in Beacon.lua
    cycle = [int(stat_id) for stat_id in lua_list(lua.STAT_CYCLE)]
    sent = 0
    live_seen = 0
    for redraw in range(1, len(cycle) * int(lua.STAT_EVERY) + 1):
        now[0] += 0.1
        if redraw % lua.STAT_EVERY == 0:
            stat_id = cycle[sent % len(cycle)]
            sent += 1
            lua.packStatBits(redraw, stat_id, journey[Stat(stat_id)], bits)
        else:
            lua.packBits(rt.to_lua({"seq": redraw, "session": 3, "flags": 5, "target": 320, "charSpeed": 350}), bits)
        decoded = beacon.decode_row(beacon.render_row(Strip(bits), 4) + bytes(8))
        assert decoded is not None
        if decoded.frame is not None:
            source.inject(decoded.frame)
            live_seen += 1
        else:
            source.inject(decoded.stat)
        # The belt's reading is never more than one redraw old, and always a live frame's.
        reading = source.reading()
        assert reading.active and reading.target_kmh == pytest.approx(3.2)
        assert reading.age_s <= 0.1 + 1e-9
        assert source.frame is not None and source.frame.seq == (redraw if decoded.frame is not None else redraw - 1)

    assert order == list(protocol.STAT_CYCLE)
    assert live_seen == source.status.frames == 64
    assert received[Stat.CHAR_KEY] == protocol.djb2("Абвгдежзиклм-Гордунни")
    assert protocol.name_from_chunks([received[stat] for stat in protocol.NAME_STATS]) == "Абвгдежзик"
    assert protocol.unpack_char_info(received[Stat.CHAR_INFO]) == protocol.CharInfo(47, 60, 8, protocol.Faction.HORDE)
    assert received[Stat.PLAYED_S] == 781_200
    assert received[Stat.FOOT_YD] == 2_345_678
    assert received[Stat.TRAVEL_YD] == 2**40 - 1
    assert received[Stat.REAL_STEPS] == 123_456
    assert received[Stat.REAL_M] == 98_766
    assert received[Stat.REAL_KCAL] == 4321 and received[Stat.REAL_S] == 86_400 and received[Stat.WALKS] == 37
    assert received[Stat.SINCE] == 1_763_424_000
    assert protocol.InfoFlags(received[Stat.INFO_FLAGS]) == protocol.InfoFlags.ESTIMATED
