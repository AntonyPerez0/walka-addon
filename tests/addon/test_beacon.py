"""The beacon end to end: what the addon paints is what the companion decodes.

The cells are read back from the mock client's textures (colour, size,
position, scale), i.e. from what would be on screen - not from the addon's
own bookkeeping - and decoded with the companion's real decoder.
"""

from __future__ import annotations

import random

import pytest
from walka import beacon, protocol
from walka.protocol import Flags, Frame, FrameError

from .conftest import MPH_STEP, nearest_step
from .harness import lua_list


def decode(rt) -> Frame:
    """The live frame on the strip. Every fifth redraw shows a stat frame
    instead; then this lets one more tick pass (WowRuntime.live_frame)."""
    return rt.live_frame()


# ---- cells ------------------------------------------------------------------------


def test_strip_has_guard_cells_and_86_cells(rt):
    rt.tick()
    cells = rt.beacon_cells()
    assert len(cells) == beacon.TOTAL_CELLS == 86
    assert tuple(cells[:4]) == beacon.PREAMBLE
    assert tuple(cells[-2:]) == beacon.POSTAMBLE
    # The addon's own view of the cells agrees with the textures.
    assert [int(value) for value in lua_list(rt.ns.Beacon.GetCells())] == cells


def test_no_valid_frame_before_the_first_tick(rt):
    # Guard cells are painted at once, the payload only with the first tick:
    # until then the companion must see "no signal", not a frame.
    with pytest.raises(FrameError):
        decode(rt)


def test_every_tick_is_a_valid_frame_and_seq_advances_by_one(rt):
    previous = None
    kinds = []
    for _ in range(300):                    # past the wrap at 256
        rt.tick()
        frame = rt.shown_frame()            # checks preamble, postamble, CRC, version
        assert frame.frame_type in (protocol.TYPE_LIVE, protocol.TYPE_STAT) and frame.reserved == 0
        if previous is not None:
            assert frame.seq == (previous + 1) % 256, "live and stat frames share one counter"
        previous = frame.seq
        kinds.append(frame.frame_type)
    # Four live frames, then one that carries a number of the journey.
    assert kinds == ([protocol.TYPE_LIVE] * 4 + [protocol.TYPE_STAT]) * 60


def test_seq_does_not_advance_between_ticks(rt):
    rt.tick()
    first = decode(rt).seq
    rt.frame(0.03)                          # a rendered frame, but not yet a tick
    rt.frame(0.03)
    assert decode(rt).seq == first
    rt.frame(0.05)
    assert decode(rt).seq == first + 1


def test_idle_frame(rt):
    rt.tick()
    frame = decode(rt)
    assert frame.flags == Flags(0)
    assert frame.session == 0 and frame.target_raw == 0 and frame.char_speed_raw == 0


@pytest.mark.parametrize(
    "state, flag",
    [
        ({"speed": 7.0}, Flags.MOVING),
        ({"mounted": True}, Flags.MOUNTED),
        ({"taxi": True}, Flags.TAXI),
        ({"swimming": True}, Flags.SWIMMING),
        ({"flying": True}, Flags.FLYING),
        ({"dead": True}, Flags.DEAD),
        ({"combat": True}, Flags.COMBAT),
        ({"afk": True}, Flags.AFK),
        ({"falling": True}, Flags.FALLING),
        ({"resting": True}, Flags.RESTING),
        ({"statsSecret": True}, Flags.ESTIMATED),
    ],
)
def test_flags_follow_the_game_state(rt, state, flag):
    rt.tick()
    assert decode(rt).flags == Flags(0)
    rt.set(**state)
    rt.tick()
    assert decode(rt).flags == flag
    for key in state:
        rt.set(**{key: 0 if key == "speed" else False})
    rt.tick()
    assert decode(rt).flags == Flags(0)


def test_all_state_flags_at_once(rt):
    rt.set(speed=20.0, mounted=True, taxi=True, swimming=True, flying=True, dead=True, combat=True,
           afk=True, falling=True, resting=True)
    rt.tick()
    everything = (Flags.MOVING | Flags.MOUNTED | Flags.TAXI | Flags.SWIMMING | Flags.FLYING | Flags.DEAD
                  | Flags.COMBAT | Flags.AFK | Flags.FALLING | Flags.RESTING)
    assert decode(rt).flags == everything


def test_session_flags_and_session_number(rt):
    rt.slash("/walka start")
    rt.tick()
    frame = decode(rt)
    assert frame.flags == Flags.ACTIVE and frame.active and not frame.paused
    assert frame.session == 1

    rt.slash("/walka pause")
    rt.tick()
    frame = decode(rt)
    assert frame.flags == Flags.ACTIVE | Flags.PAUSED
    assert frame.target_raw == 0, "a paused session must not ask for belt speed"
    assert frame.session == 1

    rt.slash("/walka resume")
    rt.tick()
    assert decode(rt).flags == Flags.ACTIVE

    rt.slash("/walka stop")
    rt.tick()
    frame = decode(rt)
    assert frame.flags == Flags(0) and frame.session == 0 and frame.target_raw == 0

    rt.slash("/walka start")
    rt.tick()
    assert decode(rt).session == 2


def test_target_speed_is_walkas_target(rt):
    rt.set(speed=7.0)
    rt.slash("/walka start")
    seen = set()
    for _ in range(400):
        rt.tick()
        frame = decode(rt)
        _, target = rt.walka.GetLiveSpeed(rt.walka)
        assert frame.target_raw == protocol.kmh_to_raw(target)
        assert frame.target_kmh == pytest.approx(target, abs=0.005)
        seen.add(frame.target_raw)
    assert len(seen) > 3, "the target should ramp, not jump"
    # Default (enUS) settings: the running pace, on the belt's 0.1 mph grid.
    running = nearest_step(rt.ns.SpeedModel.DEFAULTS.runKmh, MPH_STEP)
    assert decode(rt).target_kmh == pytest.approx(running, abs=0.005)


def test_no_target_without_a_session(rt):
    rt.set(speed=7.0)
    rt.tick(50)
    assert decode(rt).target_raw == 0


@pytest.mark.parametrize("speed", [0, 2.5, 4.7222, 7.0, 8.75, 9.8, 14.0, 26.6, 60.0, 81.9, 120.0])
def test_character_speed_encoding(rt, speed):
    rt.set(speed=speed)
    rt.tick()
    frame = decode(rt)
    assert frame.char_speed_raw == protocol.yps_to_raw(speed)
    if speed <= 81.9:
        assert frame.char_speed_yps == pytest.approx(speed, abs=0.01)
    else:
        assert frame.char_speed_raw == protocol.CHAR_MAX_RAW       # clamped, not wrapped


def test_only_changed_cells_are_repainted_but_the_result_is_always_right(rt):
    # The addon skips SetColorTexture for cells whose value did not change;
    # a mistake there would leave stale cells. Random states shake it out.
    rng = random.Random(7)
    rt.slash("/walka start")
    for _ in range(200):
        rt.set(speed=rng.choice([0, 3.1, 7.0, 14.0]), mounted=rng.random() < 0.5, combat=rng.random() < 0.5,
               resting=rng.random() < 0.5, falling=rng.random() < 0.3)
        rt.tick()
        cells = rt.beacon_cells()
        assert [int(value) for value in lua_list(rt.ns.Beacon.GetCells())] == cells
        frame = beacon.decode_cells(cells)
        if isinstance(frame, Frame):        # (a stat frame says nothing of the state)
            assert frame.has(Flags.MOUNTED) == bool(rt.game.mounted)
            assert frame.has(Flags.COMBAT) == bool(rt.game.combat)


# ---- pixels -------------------------------------------------------------------------


def busy_background(width: int, seed: int = 3) -> list[int]:
    rng = random.Random(seed)
    return [rng.randrange(256) for _ in range(width)]


@pytest.mark.parametrize("height, width", [(1080, 1920), (1440, 2560), (2160, 3840), (768, 1024), (900, 1600), (1200, 1920)])
@pytest.mark.parametrize("cell_px", [2, 3, 4, 5, 8])
def test_rendered_strip_decodes_at_any_resolution(make_rt, height, width, cell_px):
    rt = make_rt(state={"physicalWidth": width, "physicalHeight": height})
    rt.walka.Set(rt.walka, "beacon.cellPx", cell_px)
    rt.set(speed=7.0, mounted=True)
    rt.slash("/walka start")
    rt.tick(25)
    expected = decode(rt)

    for background in (0, 255, 90, busy_background(width)):
        for row in (0, cell_px // 2, cell_px - 1):
            pixels = rt.render_beacon_row(row, background=background)
            assert len(pixels) == width
            decoded = beacon.decode_row(pixels)
            assert decoded is not None, f"row {row} did not decode over background {background!r:.20}"
            assert decoded.frame == expected
            assert decoded.x0 == 0
            assert decoded.cell_px == pytest.approx(cell_px, abs=0.02)
            assert (decoded.white, decoded.black) == (255, 0)
            assert beacon.row_may_contain_beacon(pixels)


@pytest.mark.parametrize("height, width", [(1080, 1920), (1440, 2560), (2160, 3840)])
@pytest.mark.parametrize("cell_px", [2, 3, 4, 8])
def test_one_cell_is_exactly_cell_px_physical_pixels(make_rt, height, width, cell_px):
    rt = make_rt(state={"physicalWidth": width, "physicalHeight": height})
    rt.walka.Set(rt.walka, "beacon.cellPx", cell_px)
    rt.tick()
    cells = rt.beacon_cells()
    pixels = rt.render_beacon_row(0, background=128)
    for index, value in enumerate(cells):
        run = pixels[index * cell_px:(index + 1) * cell_px]
        assert set(run) == {255 if value else 0}, f"cell {index} is not a clean {cell_px}px run"
    # One cell of black quiet zone to the right, then the game world.
    strip_end = len(cells) * cell_px
    assert set(pixels[strip_end:strip_end + cell_px]) == {0}
    assert pixels[strip_end + cell_px] == 128
    # ... and one cell of black below, over the whole strip.
    below = rt.render_beacon_row(cell_px, background=128)
    assert set(below[:strip_end + cell_px]) == {0}
    assert set(rt.render_beacon_row(2 * cell_px - 1, background=128)[:strip_end + cell_px]) == {0}
    # Nothing is painted beneath that.
    assert set(rt.render_beacon_row(2 * cell_px, background=128)) == {128}


def test_strip_is_independent_of_the_ui_scale_and_survives_alt_z(rt):
    rt.tick()
    before = rt.render_beacon_row(0)
    rt.M.set_ui_scale(0.64)
    rt.tick()
    frame = rt.inspect(rt.beacon_frame())
    assert frame.get("parent") is None, "the strip must not be parented to UIParent"
    assert frame["scale"] == pytest.approx(768 / 1080)
    assert beacon.decode_row(rt.render_beacon_row(0)) is not None
    assert len(rt.render_beacon_row(0)) == len(before)
    rt.global_("UIParent").Hide(rt.global_("UIParent"))        # Alt+Z hides the UI
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0)) is not None


def test_default_cell_size(rt):
    # 4 physical pixels: what the companion expects when it is not told.
    assert rt.ns.Beacon.DEFAULT_CELL_PX == beacon.DEFAULT_CELL_PX == 4
    assert rt.walka.Get(rt.walka, "beacon.cellPx") == 4 and rt.ns.Beacon.GetCellSize() == 4
    rt.tick()
    pixels = rt.render_beacon_row(0, background=128)
    assert beacon.decode_row(pixels).cell_px == pytest.approx(4)
    # The whole strip, its quiet zone included, and then the game world.
    assert set(pixels[: (beacon.TOTAL_CELLS + 1) * 4]) <= {0, 255}
    assert pixels[(beacon.TOTAL_CELLS + 1) * 4] == 128


def test_strip_follows_a_resolution_change(rt):
    cell_px = rt.ns.Beacon.DEFAULT_CELL_PX
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0)).cell_px == pytest.approx(cell_px)
    rt.M.set_screen(3840, 2160)             # fires DISPLAY_SIZE_CHANGED
    rt.tick()
    decoded = beacon.decode_row(rt.render_beacon_row(0))
    assert decoded is not None and decoded.cell_px == pytest.approx(cell_px, abs=0.02)
    assert rt.inspect(rt.beacon_frame())["scale"] == pytest.approx(768 / 2160)


def test_strip_is_drawn_on_top_and_ignores_the_mouse(rt):
    frame = rt.inspect(rt.beacon_frame())
    assert frame["strata"] == "TOOLTIP"
    assert frame["mouse"] is False
    assert frame["visible"] is True


def test_cell_size_setting_is_clamped(rt):
    walka = rt.walka
    for asked, got in [(1, 2), (2, 2), (2.6, 3), (8, 8), (50, 8), (-3, 2)]:
        walka.Set(walka, "beacon.cellPx", asked)
        assert rt.ns.Beacon.GetCellSize() == got
        assert walka.Get(walka, "beacon.cellPx") == got        # the stored value is the clamped one
        rt.tick()
        assert beacon.decode_row(rt.render_beacon_row(0)).cell_px == pytest.approx(got, abs=0.02)


def test_beacon_off_shows_nothing_but_the_addon_keeps_ticking(rt):
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0, background=40)) is not None
    rt.slash("/walka beacon off")
    seq = decode(rt).seq
    rt.tick(5)
    pixels = rt.render_beacon_row(0, background=40)
    assert set(pixels) == {40}, "a hidden beacon must leave the screen untouched"
    assert beacon.decode_row(pixels) is None
    assert decode(rt).seq == (seq + 5) % 256, "ticks must go on while the strip is invisible"
    rt.slash("/walka beacon on")
    rt.tick()
    assert beacon.decode_row(rt.render_beacon_row(0, background=40)) is not None


def test_beacon_setting_survives_a_reload(rt):
    rt.slash("/walka beacon off")
    rt.slash("/walka beacon size 5")
    fresh = rt.reload()
    fresh.tick()
    assert fresh.ns.Beacon.GetCellSize() == 5
    assert set(fresh.render_beacon_row(0, background=40)) == {40}
