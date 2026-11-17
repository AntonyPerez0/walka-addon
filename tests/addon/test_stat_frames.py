"""Stat frames as the addon really sends them (docs/PROTOCOL.md, section 4).

Every fifth redraw of the beacon carries one number of the character's
journey in place of the live frame. Here the strip is read back from the
mock client - the cells, and the rendered pixels - and decoded with the
companion's own decoder, through a full round and more.

(The packing of a single stat frame is in test_protocol_stat.py; what the
numbers are made of in test_journey.py.)
"""

from __future__ import annotations

import pytest
from walka import beacon, protocol
from walka.protocol import Flags, Frame, Stat, StatFrame
from walka.sources import BeaconSource

from .harness import lua_plain
from .test_journey import ME, NOW, companion, measured, own, saved

CYCLE = list(protocol.STAT_CYCLE)
ROUND = len(CYCLE) * protocol.STAT_EVERY        # redraws in one round of all sixteen stats


def summary(rt) -> dict:
    return lua_plain(rt.ns.Tracker.GetSummary())


def watch(rt, redraws: int) -> list[Frame | StatFrame]:
    """Tick `redraws` times; what the strip showed after each."""
    shown = []
    for _ in range(redraws):
        rt.tick()
        shown.append(rt.shown_frame())
    return shown


def one_round(rt) -> dict[Stat, int]:
    """Watch the strip through the next whole round of stats: from its
    CHAR_KEY to its last frame. (Called again, it reads the round after.)"""
    stats: dict[Stat, int] = {}
    for _ in range(2 * ROUND):
        rt.tick()
        frame = rt.shown_frame()
        if isinstance(frame, StatFrame) and (stats or frame.stat is Stat.CHAR_KEY):
            stats[frame.stat] = frame.value
            if frame.stat is CYCLE[-1]:
                break
    assert list(stats) == CYCLE, "a round carries every stat, in order"
    return stats


def name_of(stats: dict[Stat, int]) -> str:
    return protocol.name_from_chunks([stats[stat] for stat in protocol.NAME_STATS])


# ---- the schedule ---------------------------------------------------------------------------------


def test_every_fifth_redraw_is_a_stat_frame_and_they_go_round_in_order(rt):
    shown = watch(rt, 2 * ROUND + 7)
    for index, frame in enumerate(shown, start=1):
        assert frame.seq == index % 256, "one counter for both kinds, +1 per redraw"
        assert isinstance(frame, StatFrame) == (index % protocol.STAT_EVERY == 0)
    stats = [frame.stat for frame in shown if isinstance(frame, StatFrame)]
    assert stats == CYCLE + CYCLE + CYCLE[:1], "CHAR_KEY opens every round"
    assert rt.ns.Protocol.STAT_EVERY == protocol.STAT_EVERY == 5


def test_never_two_stat_frames_in_a_row_however_long_it_runs(rt):
    shown = watch(rt, 1300)                         # past five wraps of the 8-bit counter
    kinds = [isinstance(frame, StatFrame) for frame in shown]
    assert not any(a and b for a, b in zip(kinds, kinds[1:], strict=False))
    assert kinds.count(True) == 260, "one in five, exactly"
    assert [frame.seq for frame in shown] == [index % 256 for index in range(1, 1301)]
    assert all(frame.stat is not None and frame.reserved == 0 for frame in shown if isinstance(frame, StatFrame))


def test_stat_frames_are_sent_without_a_session_and_with_one(rt):
    assert not rt.walka.IsActive(rt.walka)
    assert sum(isinstance(frame, StatFrame) for frame in watch(rt, 50)) == 10
    rt.set(speed=7.0)
    rt.slash("/walka start")
    assert sum(isinstance(frame, StatFrame) for frame in watch(rt, 50)) == 10
    rt.slash("/walka pause")
    assert sum(isinstance(frame, StatFrame) for frame in watch(rt, 50)) == 10
    rt.slash("/walka stop")
    assert sum(isinstance(frame, StatFrame) for frame in watch(rt, 50)) == 10


def test_the_tick_does_all_its_work_on_a_stat_redraw_too(rt):
    """Only the picture differs on a stat redraw: the character is still
    sampled, the session still counts, the target still follows."""
    walka = rt.walka
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.tick(100)                                    # the belt is moving and still speeding up
    for _ in range(20):
        before = walka.GetSession(walka).seconds
        target_before = walka.GetLiveSpeed(walka)[1]
        rt.tick()
        frame = rt.shown_frame()
        assert walka.GetSession(walka).seconds == pytest.approx(before + 0.1)
        if isinstance(frame, Frame):
            # The live frame after a stat frame is up to date, not a repeat.
            assert frame.target_raw == protocol.kmh_to_raw(walka.GetLiveSpeed(walka)[1])
            assert frame.has(Flags.ACTIVE | Flags.MOVING)
        else:
            assert walka.GetLiveSpeed(walka)[1] >= target_before
    # A pause that falls on a stat redraw is on the strip one redraw later.
    while not isinstance(rt.shown_frame(), StatFrame):
        rt.tick()
    rt.tick(4)
    rt.slash("/walka pause")
    rt.tick()
    assert isinstance(rt.shown_frame(), StatFrame) and walka.GetLiveSpeed(walka)[1] == 0
    rt.tick()
    frame = rt.shown_frame()
    assert isinstance(frame, Frame) and frame.paused and frame.target_raw == 0


def test_state_seen_only_on_a_stat_redraw_still_reaches_the_journey(rt):
    # Four redraws standing, the fifth - the stat frame's - moving: the yard
    # travelled in that tenth of a second counts.
    for _ in range(10):
        rt.set(speed=0)
        rt.tick(4)
        rt.set(speed=10.0)
        rt.tick()
        assert isinstance(rt.shown_frame(), StatFrame)
    assert summary(rt)["footYd"] == 10


# ---- what a round says ----------------------------------------------------------------------------------


def test_a_round_carries_the_journey(make_rt):
    rt = make_rt(
        saved_variables=saved(own(NOW - 90000), own(NOW - 80000, 900, steps=1200, distance=700, kcal=50.3)),
        files=companion(measured(NOW - 90000, 1800, steps=2610, distance=1503, kcal=111.6)),
        state={"level": 42, "classId": 11, "faction": "Horde"},
    )
    rt.run("RequestTimePlayed()")                   # the player's own /played
    rt.set(speed=7.0)
    rt.advance(30)
    rt.set(speed=14.0, mounted=True)
    rt.advance(7)
    rt.set(speed=0, mounted=False)
    rt.advance(5)
    stats = one_round(rt)
    journey = summary(rt)

    assert stats[Stat.CHAR_KEY] == protocol.djb2(ME)
    assert name_of(stats) == "Tester" == journey["name"]
    info = protocol.unpack_char_info(stats[Stat.CHAR_INFO])
    assert info == protocol.CharInfo(42, 60, 11, protocol.Faction.HORDE)
    assert stats[Stat.PLAYED_S] == pytest.approx(journey["played"], abs=ROUND / 10)
    assert stats[Stat.FOOT_YD] == journey["footYd"] == 210
    assert stats[Stat.TRAVEL_YD] == journey["travelYd"] == 308
    assert stats[Stat.REAL_STEPS] == journey["steps"] == 3810
    assert stats[Stat.REAL_M] == journey["distanceM"] == 2203
    assert stats[Stat.REAL_KCAL] == journey["kcal"] == 161
    assert stats[Stat.REAL_S] == journey["seconds"] == 2700
    assert stats[Stat.WALKS] == journey["walks"] == 2
    assert stats[Stat.SINCE] == journey["since"] == NOW - 90000
    assert protocol.InfoFlags(stats[Stat.INFO_FLAGS]) == protocol.InfoFlags.ESTIMATED


def test_a_new_character_sends_zeros_and_no_estimate_flag(rt):
    stats = one_round(rt)
    assert stats[Stat.CHAR_KEY] == protocol.djb2(ME)
    assert stats[Stat.PLAYED_S] == 0, "0 is 'the game has not said yet'"
    assert [stats[stat] for stat in (Stat.FOOT_YD, Stat.TRAVEL_YD, Stat.REAL_STEPS, Stat.REAL_M, Stat.REAL_KCAL,
                                     Stat.REAL_S, Stat.WALKS, Stat.INFO_FLAGS)] == [0] * 8
    assert stats[Stat.SINCE] == NOW
    assert protocol.unpack_char_info(stats[Stat.CHAR_INFO]) == protocol.CharInfo(17, 60, 4, protocol.Faction.ALLIANCE)


def test_all_measured_walks_clear_the_estimate_flag(make_rt):
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), files=companion(measured(NOW - 90000)))
    stats = one_round(rt)
    assert stats[Stat.INFO_FLAGS] == 0 and stats[Stat.REAL_STEPS] == 2600 and stats[Stat.WALKS] == 1


@pytest.mark.parametrize(
    "name, realm, sent",
    [
        ("Thrall", "Azeroth", "Thrall"),
        ("Ñandú", "Azjol-Nerub", "Ñandú"),
        ("Абвгдежзиклм", "Гордунни", "Абвгдежзик"),          # 24 bytes: the first ten letters fit
        ("阿尔萨斯", "Burning Legion", "阿尔萨斯"),
        ("A", "", "A"),
    ],
)
def test_name_and_key(make_rt, name, realm, sent):
    rt = make_rt(state={"name": name, "realm": realm})
    stats = one_round(rt)
    assert name_of(stats) == sent
    full = f"{name}-{realm}" if realm else name
    assert rt.ns.Game.CharacterName() == full
    assert stats[Stat.CHAR_KEY] == protocol.djb2(full), "a hash of the Name-Realm the journey is kept under"
    assert full in rt.db.journeys


def bit_text(data: bytes) -> str:
    return "".join(f"{byte:08b}" for byte in data)


def test_the_realm_is_never_on_the_strip(make_rt):
    realm = "Zzyzx"
    rt = make_rt(state={"realm": realm})
    # Everything painted in two rounds, as one long run of bits: text shows
    # up in it wherever a field happens to begin.
    painted = "".join(bit_text(frame.pack()) for frame in watch(rt, 2 * ROUND))
    assert bit_text(b"Teste") in painted, "the name is sent (five bytes to a frame)"
    for piece in (realm[:3], realm[1:4], realm[2:]):
        assert bit_text(piece.encode()) not in painted, f"{piece!r} of the realm is on the strip"


def test_time_played_and_distance_move_from_round_to_round(rt):
    rt.run("RequestTimePlayed()")
    rt.set(speed=7.0)
    first = one_round(rt)
    second = one_round(rt)
    assert second[Stat.PLAYED_S] - first[Stat.PLAYED_S] == ROUND / 10       # eight seconds a round
    assert second[Stat.FOOT_YD] - first[Stat.FOOT_YD] == 7 * ROUND / 10
    assert second[Stat.CHAR_KEY] == first[Stat.CHAR_KEY] and second[Stat.SINCE] == first[Stat.SINCE]


def test_the_real_world_numbers_of_one_round_belong_together(rt):
    """Steps, metres, calories and seconds are taken once, as CHAR_KEY opens
    the round - not each at the moment its own frame is painted."""
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(60)
    taken = None
    stats: dict[Stat, int] = {}
    for _ in range(2 * ROUND):
        rt.tick()
        frame = rt.shown_frame()
        if not isinstance(frame, StatFrame):
            continue
        if frame.stat is Stat.CHAR_KEY:
            if taken is not None:
                break
            taken = summary(rt)
        elif taken is not None:
            stats[frame.stat] = frame.value
    assert taken is not None and taken["steps"] > 50
    assert stats[Stat.REAL_STEPS] == taken["steps"]
    assert stats[Stat.REAL_M] == taken["distanceM"]
    assert stats[Stat.REAL_KCAL] == taken["kcal"]
    assert stats[Stat.REAL_S] == taken["seconds"]
    assert stats[Stat.WALKS] == 1 and stats[Stat.INFO_FLAGS] == protocol.InfoFlags.ESTIMATED
    assert summary(rt)["steps"] > taken["steps"], "the walk went on meanwhile"


def test_level_up_reaches_the_next_round(rt):
    assert protocol.unpack_char_info(one_round(rt)[Stat.CHAR_INFO]).level == 17
    rt.fire_event("PLAYER_LEVEL_UP", 18)
    assert protocol.unpack_char_info(one_round(rt)[Stat.CHAR_INFO]).level == 18


def test_another_character_another_key(rt):
    first = one_round(rt)
    rt.set(name="Other")
    other = rt.reload(gap=60)
    second = one_round(other)
    assert second[Stat.CHAR_KEY] == protocol.djb2("Other-Azeroth") != first[Stat.CHAR_KEY]
    assert name_of(second) == "Other" and second[Stat.SINCE] == NOW + ROUND / 10 + 60


def test_a_reload_starts_a_new_round_with_char_key(rt):
    watch(rt, 37)                                   # in the middle of a round
    fresh = rt.reload()
    stats = [frame for frame in watch(fresh, 10) if isinstance(frame, StatFrame)]
    assert [frame.stat for frame in stats] == [Stat.CHAR_KEY, Stat.NAME_0]


# ---- when there is nothing to say, and when things are hidden ---------------------------------------------


def test_no_stat_frames_until_the_character_is_known(make_rt):
    rt = make_rt(state={"nameSecret": True})
    shown = watch(rt, 60)
    assert all(isinstance(frame, Frame) for frame in shown), "nobody's numbers are not sent"
    assert [frame.seq for frame in shown] == list(range(1, 61))
    rt.set(nameSecret=False)
    stats = [frame.stat for frame in watch(rt, 20) if isinstance(frame, StatFrame)]
    assert stats[0] is Stat.CHAR_KEY and len(stats) >= 3
    assert rt.find_secrets() == []


def test_stat_frames_in_combat_with_everything_hidden(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    rt.advance(30)
    rt.enter_combat()
    rt.set(chatLockdown=True, nameSecret=True, identitySecret=True)
    stats = one_round(rt)
    assert rt.find_secrets() == []
    assert name_of(stats) == "Tester" and stats[Stat.CHAR_KEY] == protocol.djb2(ME)
    assert protocol.unpack_char_info(stats[Stat.CHAR_INFO]) == protocol.CharInfo(17, 60, 4, protocol.Faction.ALLIANCE)
    assert stats[Stat.FOOT_YD] > 200
    # The live frames in between say what they always say in combat.
    assert rt.live_frame().has(Flags.ESTIMATED | Flags.COMBAT | Flags.ACTIVE)


def test_a_hidden_beacon_keeps_the_schedule(rt):
    rt.slash("/walka beacon off")
    assert set(rt.render_beacon_row(0, background=40)) == {40}
    shown = watch(rt, ROUND)                        # (the cells are still painted, just not visible)
    assert [frame.stat for frame in shown if isinstance(frame, StatFrame)] == CYCLE


# ---- through the companion's reader --------------------------------------------------------------------


@pytest.mark.parametrize("cell_px", [2, 4, 5])
def test_the_companion_reads_a_round_off_the_screen_and_the_belt_reading_stays_live(make_rt, cell_px):
    """The rendered pixels go through the companion's decoder into its
    BeaconSource: the stats come out of `on_stat`, in order, and the reading
    the belt is driven by is only ever a live frame's."""
    rt = make_rt(saved_variables=saved(own(NOW - 90000)), state={"physicalWidth": 2560, "physicalHeight": 1440})
    rt.walka.Set(rt.walka, "beacon.cellPx", cell_px)
    rt.set(speed=7.0)
    rt.slash("/walka start")

    now = [1000.0]
    source = BeaconSource(None, clock=lambda: now[0])
    received: list[tuple[Stat, int]] = []
    source.on_stat = lambda stat_id, value: received.append((Stat(stat_id), value))

    live = 0
    for redraw in range(1, ROUND + 1):
        rt.tick()
        now[0] += 0.1
        decoded = beacon.decode_row(rt.render_beacon_row(cell_px // 2, background=70))
        assert decoded is not None and decoded.cell_px == pytest.approx(cell_px, abs=0.02)
        if decoded.frame is not None:
            source.inject(decoded.frame)
            live += 1
        else:
            source.inject(decoded.stat)
        reading = source.reading()
        assert reading.active and reading.age_s <= 0.1 + 1e-9
        assert source.frame.seq == (redraw if decoded.frame is not None else redraw - 1)

    assert [stat for stat, _ in received] == CYCLE
    assert live == source.status.frames == ROUND * 4 // 5
    values = dict(received)
    assert values[Stat.CHAR_KEY] == protocol.djb2(ME) and values[Stat.REAL_STEPS] == 2500
    assert protocol.name_from_chunks([values[stat] for stat in protocol.NAME_STATS]) == "Tester"
