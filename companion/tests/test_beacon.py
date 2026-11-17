"""Decoding the on-screen strip from rows of pixel brightness."""

from __future__ import annotations

import random

import pytest

from walka import beacon
from walka.beacon import TOTAL_CELLS, decode_row, render_row, row_may_contain_beacon
from walka.protocol import Flags, Frame, FrameError

FRAME = Frame(seq=41, session=7, flags=Flags.ACTIVE | Flags.MOVING, target_raw=322, char_speed_raw=350)

INTEGER_CELLS = [2, 3, 4, 5, 6, 7, 8]
FRACTIONAL_CELLS = [2.25, 2.5, 3.2, 3.75, 4.5, 5.33, 6.4, 7.7]


def frames(seed: int, count: int) -> list[Frame]:
    rng = random.Random(seed)
    return [
        Frame(
            seq=rng.randrange(256),
            session=rng.randrange(256),
            flags=Flags(rng.randrange(1 << 13)),
            target_raw=rng.randrange(4096),
            char_speed_raw=rng.randrange(4096),
        )
        for _ in range(count)
    ]


def on_desktop(strip: bytes, offset: int, width: int, background: int = 0) -> bytes:
    """Place a rendered strip in a wider row."""
    row = bytearray([background]) * max(width, offset + len(strip))
    row[offset : offset + len(strip)] = strip
    return bytes(row)


# ---- cells -----------------------------------------------------------------------


def test_cell_layout():
    cells = beacon.frame_to_cells(FRAME)
    assert len(cells) == TOTAL_CELLS == 86
    assert tuple(cells[:4]) == (1, 0, 1, 0)
    assert tuple(cells[-2:]) == (1, 0)
    assert cells[4:84] == FRAME.to_bits()
    assert beacon.cells_to_frame(cells) == FRAME


def test_bad_guard_cells_are_refused():
    cells = beacon.frame_to_cells(FRAME)
    for index in (0, 1, 2, 3, 84, 85):
        broken = list(cells)
        broken[index] ^= 1
        with pytest.raises(FrameError):
            beacon.cells_to_frame(broken)
    with pytest.raises(FrameError):
        beacon.cells_to_frame(cells[:-1])
    with pytest.raises(FrameError):
        beacon.cells_to_frame(cells + [0])


def test_render_row_geometry():
    row = render_row(FRAME, 3)
    assert len(row) == 86 * 3
    assert set(row) == {0, 255}
    assert row[:12] == bytes([255] * 3 + [0] * 3 + [255] * 3 + [0] * 3)
    shifted = render_row(FRAME, 3, offset=5, width=400, background=9)
    assert len(shifted) == 400
    assert shifted[:5] == bytes([9] * 5) and shifted[5 : 5 + 258] == row and set(shifted[263:]) == {9}
    dim = render_row(FRAME, 4, white=180, black=70)
    assert set(dim) == {70, 180}
    assert len(render_row(FRAME, 2.5)) == 215


# ---- cell sizes, pitches, offsets -------------------------------------------------


@pytest.mark.parametrize("cell", INTEGER_CELLS + FRACTIONAL_CELLS)
def test_decodes_every_cell_size(cell: float):
    for frame in frames(int(cell * 100), 25):
        row = render_row(frame, cell)
        decoded = decode_row(row + bytes(int(cell) + 2))  # the addon's black quiet zone
        assert decoded is not None, f"cell {cell}: {frame}"
        assert decoded.frame == frame
        assert decoded.x0 == 0
        assert decoded.cell_px == pytest.approx(cell, abs=0.45)
        assert decoded.white == 255 and decoded.black == 0


@pytest.mark.parametrize("cell", [2, 2.5, 3, 3.2, 3.75, 4, 6, 8])
@pytest.mark.parametrize("offset", [0, 1, 2, 3, 5, 8, 37, 500])
def test_decodes_at_any_start_offset(cell: float, offset: int):
    strip = render_row(FRAME, cell)
    row = on_desktop(strip, offset, offset + len(strip) + 40)
    decoded = decode_row(row)
    assert decoded is not None
    assert decoded.frame == FRAME
    assert decoded.x0 == offset


@pytest.mark.parametrize("cell", [2, 3, 4, 8])
def test_decodes_bytes_lists_and_bytearrays(cell: int):
    row = render_row(FRAME, cell) + bytes(8)
    for variant in (row, bytearray(row), list(row), tuple(row)):
        decoded = decode_row(variant)
        assert decoded is not None and decoded.frame == FRAME


def test_strip_filling_the_row_exactly_needs_one_trailing_pixel():
    """The addon always paints a quiet zone, so a strip that ends exactly at
    the edge of the capture is the one geometry we do not require."""
    row = render_row(FRAME, 3)
    assert decode_row(row + b"\x00") is not None


# ---- brightness, contrast, gamma, noise -------------------------------------------


@pytest.mark.parametrize(
    "white,black,background",
    [
        (255, 0, 0),
        (255, 0, 60),  # a brighter game world next to the strip
        (200, 30, 30),
        (180, 90, 90),  # washed out
        (120, 20, 0),  # dim (night-light / HDR tone-mapping)
        (100, 40, 40),
        (255, 140, 140),  # raised black level
        (250, 200, 200),  # barely any contrast, but above the minimum
        (80, 20, 255),  # dim strip next to a bright game world
    ],
)
@pytest.mark.parametrize("cell", [2, 3, 3.75, 5])
def test_decodes_with_reduced_contrast(white: int, black: int, background: int, cell: float):
    strip = render_row(FRAME, cell, white=white, black=black) + bytes([black]) * 6
    row = on_desktop(strip, 4, 700, background=background)
    row = bytes([black]) * 4 + row[4:]
    decoded = decode_row(row, max_start=8)
    if background > white:
        # Bright content elsewhere in the row lifts the row's midpoint above
        # the strip's white level: a documented limit of the edge finder.
        assert decoded is None or decoded.frame == FRAME
        return
    assert decoded is not None, (white, black, background, cell)
    assert decoded.frame == FRAME
    assert decoded.white == white and decoded.black == black


def test_too_little_contrast_is_not_a_beacon():
    assert decode_row(render_row(FRAME, 3, white=140, black=100) + bytes([100]) * 6) is None
    assert decode_row(render_row(FRAME, 3, white=47, black=0) + bytes(6)) is None
    assert decode_row(render_row(FRAME, 3, white=48, black=0) + bytes(6)) is not None


@pytest.mark.parametrize("gamma", [0.45, 0.8, 1.0, 1.8, 2.4])
@pytest.mark.parametrize("gain", [0.5, 0.8, 1.0])
def test_decodes_after_gamma_and_brightness_changes(gamma: float, gain: float):
    """Monotonic tone curves keep black below white, which is all we need."""
    lut = bytes(min(255, int(round(255 * gain * (v / 255) ** gamma))) for v in range(256))
    for cell in (2, 3, 4.5):
        strip = render_row(FRAME, cell, white=235, black=16) + bytes([16]) * 8
        row = on_desktop(strip, 0, 600, background=16).translate(lut)
        decoded = decode_row(row, max_start=8)
        assert decoded is not None, (gamma, gain, cell)
        assert decoded.frame == FRAME


@pytest.mark.parametrize("cell", [2, 3, 3.2, 4, 6])
@pytest.mark.parametrize("amplitude", [8, 20, 40])
def test_decodes_through_pixel_noise(cell: float, amplitude: int):
    rng = random.Random(int(cell * 10) + amplitude)
    for frame in frames(amplitude, 15):
        strip = render_row(frame, cell, white=215, black=40) + bytes([40]) * 8
        noisy = bytes(max(0, min(255, v + rng.randint(-amplitude, amplitude))) for v in on_desktop(strip, 3, 720, 40))
        decoded = decode_row(noisy, max_start=8)
        assert decoded is not None, (cell, amplitude)
        assert decoded.frame == frame


@pytest.mark.parametrize("cell", [3, 4, 5, 8])
def test_decodes_a_slightly_blurred_strip(cell: int):
    """Bilinear filtering (a scaled window) softens the cell edges."""
    strip = on_desktop(render_row(FRAME, cell), 2, 720)
    blurred = bytes(
        (strip[max(0, i - 1)] + 2 * strip[i] + strip[min(len(strip) - 1, i + 1)]) // 4 for i in range(len(strip))
    )
    decoded = decode_row(blurred, max_start=8)
    assert decoded is not None and decoded.frame == FRAME


# ---- things that must NOT decode --------------------------------------------------


def test_inverted_strip_is_not_decoded():
    """A colour-inverted screen flips every bit; the guards and CRC must
    catch it rather than hand the governor a made-up frame."""
    for frame in frames(11, 150):
        for cell in (2, 3, 4):
            row = render_row(frame, cell, white=0, black=255)
            for background in (0, 255):
                assert decode_row(on_desktop(row, 0, len(row) + 30, background)) is None
                assert decode_row(on_desktop(row, 6, len(row) + 30, background)) is None


def test_junk_rows_do_not_decode():
    rng = random.Random(12)
    rows = [
        bytes(600),
        bytes([255]) * 600,
        bytes([128]) * 600,
        bytes(i % 256 for i in range(600)),  # gradient
        bytes(255 if (i // 3) % 2 == 0 else 0 for i in range(600)),  # an endless preamble
        bytes(255 if (i // 2) % 2 == 0 else 0 for i in range(600)),
        bytes(255 if (i // 5) % 2 == 0 else 0 for i in range(600)),
        bytes(255 if i % 7 < 3 else 0 for i in range(600)),
        b"",
        b"\xff",
        bytes([255, 0] * 40),
    ]
    rows += [bytes(rng.randrange(256) for _ in range(700)) for _ in range(150)]
    rows += [bytes(rng.choice((0, 255)) for _ in range(700)) for _ in range(150)]
    # Random black/white cells at a clean 3 px pitch: looks like a strip, CRC says no.
    rows += [bytes(v for _ in range(230) for v in [rng.choice((0, 255))] * 3) for _ in range(300)]
    for row in rows:
        assert decode_row(row) is None
        assert decode_row(row, max_start=8) is None


def test_corrupted_cells_do_not_decode():
    rng = random.Random(13)
    for frame in frames(13, 60):
        cells = beacon.frame_to_cells(frame)
        index = rng.randrange(4, 84)
        cells[index] ^= 1
        row = bytes(v for c in cells for v in [255 if c else 0] * 3) + bytes(6)
        assert decode_row(row) is None


@pytest.mark.parametrize("cell", [2, 3, 4, 8])
def test_strip_cut_off_at_the_right_edge_does_not_decode(cell: int):
    """A window hanging off the right edge of the screen: the strip is
    incomplete and must not be guessed at."""
    full = render_row(FRAME, cell)
    assert decode_row(full + bytes(cell)) is not None
    # (Losing less than a whole cell still leaves every cell readable.)
    for missing_cells in (1, 1.5, 2, 3, 10, 40, 80):
        cut = full[: len(full) - int(missing_cells * cell)]
        assert decode_row(cut) is None, missing_cells
        assert decode_row(on_desktop(cut, 5, 5 + len(cut))) is None


def test_two_strips_in_one_row_decodes_the_first():
    first, second = frames(14, 2)
    row = on_desktop(render_row(first, 3), 4, 300) + on_desktop(render_row(second, 3), 10, 300)
    decoded = decode_row(row)
    assert decoded is not None and decoded.frame == first and decoded.x0 == 4
    # If the first one is damaged the second is found instead.
    cells = beacon.frame_to_cells(first)
    cells[20] ^= 1
    damaged = bytes(v for c in cells for v in [255 if c else 0] * 3)
    row = on_desktop(damaged, 4, 300) + on_desktop(render_row(second, 3), 10, 300)
    decoded = decode_row(row)
    assert decoded is not None and decoded.frame == second and decoded.x0 == 310


def test_max_start_limits_where_a_strip_may_begin():
    strip = render_row(FRAME, 3)
    for offset in range(0, 9):
        decoded = decode_row(on_desktop(strip, offset, 400), max_start=8)
        assert decoded is not None and decoded.x0 == offset
    for offset in (9, 10, 50):
        assert decode_row(on_desktop(strip, offset, 400), max_start=8) is None
        assert decode_row(on_desktop(strip, offset, 400)) is not None
    assert decode_row(on_desktop(strip, 0, 400), max_start=0) is not None
    assert decode_row(on_desktop(strip, 1, 400), max_start=0) is None


def test_short_rows_are_rejected_quickly():
    assert decode_row(bytes([255, 0] * 30)) is None
    assert decode_row(render_row(FRAME, 3)[:100]) is None


def test_a_strip_behind_bright_clutter_within_the_attempt_budget():
    """A few unrelated bright blobs before the strip do not hide it."""
    strip = render_row(FRAME, 3)
    clutter = bytes([0] * 10 + [255] * 9 + [0] * 30 + [200] * 2 + [0] * 12 + [255] * 40 + [0] * 20)
    row = clutter + strip + bytes(20)
    decoded = decode_row(row)
    assert decoded is not None and decoded.frame == FRAME and decoded.x0 == len(clutter)


# ---- the whole-screen pre-filter ---------------------------------------------------


def _strip_rows():
    """Rows that decode_row accepts, across the supported geometry and tone range."""
    rng = random.Random(15)
    for cell in INTEGER_CELLS + [2.5, 3.2, 3.75, 5.33]:
        for white, black, background in (
            (255, 0, 0),
            (255, 0, 90),
            (235, 16, 16),
            (120, 20, 20),  # dim: white below mid-grey
            (100, 30, 0),
            (255, 150, 150),  # washed out: black above mid-grey
            (220, 135, 200),
            (90, 10, 40),
        ):
            for offset in (0, 3, 250):
                frame = frames(rng.randrange(1 << 30), 1)[0]
                strip = render_row(frame, cell, white=white, black=black) + bytes([black]) * 8
                row = on_desktop(strip, offset, 900, background)
                yield (cell, white, black, background, offset), frame, row


def test_prefilter_never_rejects_a_row_the_decoder_accepts():
    """row_may_contain_beacon gates the whole-screen search: a row it drops
    is never decoded, so it must be a superset of decode_row."""
    checked = 0
    for label, frame, row in _strip_rows():
        decoded = decode_row(row)
        if decoded is None:
            continue
        assert decoded.frame == frame
        checked += 1
        assert row_may_contain_beacon(row), f"pre-filter dropped a decodable row: {label}"
    assert checked > 200


def test_prefilter_agrees_with_decoder_on_noisy_strips():
    rng = random.Random(16)
    for frame in frames(16, 60):
        cell = rng.choice([2, 3, 4, 6])
        strip = render_row(frame, cell, white=210, black=45) + bytes([45]) * 8
        row = bytes(max(0, min(255, v + rng.randint(-15, 15))) for v in on_desktop(strip, rng.randrange(300), 900, 45))
        if decode_row(row) is not None:
            assert row_may_contain_beacon(row)


def test_prefilter_rejects_flat_rows():
    assert not row_may_contain_beacon(bytes(1920))
    assert not row_may_contain_beacon(bytes([255]) * 1920)
    assert not row_may_contain_beacon(bytes([77]) * 1920)
    assert not row_may_contain_beacon(b"")
    # A single bright blob is not a preamble.
    assert not row_may_contain_beacon(bytes(100) + bytes([255]) * 50 + bytes(100))
