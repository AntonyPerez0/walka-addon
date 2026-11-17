"""Walka pixel beacon: the on-screen strip that carries a protocol frame.

The addon paints the 80 frame bits as a single row of square black/white
cells in the top-left corner of the game window:

    cell:   0 1 2 3 | 4 ............ 83 | 84 85 | 86
            W B W B |   80 frame bits   |  W  B |  B
            preamble   white=1 black=0    postamble  quiet zone

Only black and white are used (never colour), so the strip survives gamma,
contrast, HDR tone-mapping and colour-blind filters: the decoder only needs
"brighter or darker than the midpoint", and it measures that midpoint from
the preamble of every frame.

Every cell is ``cell`` physical pixels wide and tall (4 by default). The
decoder does not need to know the size: it measures the pitch from the
preamble and refines it until the postamble and CRC agree.

The strip shows one frame at a time: usually a live frame, now and then a
stat frame (see ``protocol``). The decoder hands back both, in separate
places (``Decoded.frame`` and ``Decoded.stat``), so that a stat frame can
never be taken for news about the belt.

A CRC of 16 bits is not much against a whole screen: text, tables and window
decorations are rows of light and dark runs too, and a decoder that tries
many places and sizes per row finds a "frame" in them now and then. So a
frame is only handed back if the picture around it is what ``Walka/Beacon.lua``
draws - the checks are listed at ``decode_row`` and ``strip_in`` - and even
then one frame is not believed by itself (``trust.py``).

This module is pure Python (no numpy) and operates on rows of 8-bit
brightness values, so it can be tested without a screen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .protocol import FRAME_BITS, Frame, FrameError, StatFrame, decode_bits, not_from_the_addon

PREAMBLE: tuple[int, ...] = (1, 0, 1, 0)
POSTAMBLE: tuple[int, ...] = (1, 0)
TOTAL_CELLS = len(PREAMBLE) + FRAME_BITS + len(POSTAMBLE)  # 86

DEFAULT_CELL_PX = 4
# The addon draws cells of 2 to 8 pixels. A shade under 2 is let through for
# a window that is being scaled down a little; anything smaller is not a
# cell but the stem of a letter, and that is where most accidents came from.
MIN_CELL_PX = 1.9
MAX_CELL_PX = 16.0

# Minimum brightness difference (0-255) between the white and black preamble
# cells for a row to be considered a beacon at all.
MIN_CONTRAST = 48

# How many places in one row are searched for a frame before giving up on
# the row (each search tries a range of pitches).
MAX_ATTEMPTS = 24


def frame_to_cells(frame: Frame | StatFrame) -> list[int]:
    """Return the 86 cell values (1 = white, 0 = black) for a frame."""
    return [*PREAMBLE, *frame.to_bits(), *POSTAMBLE]


def _frame_bits(cells: Sequence[int]) -> Sequence[int]:
    """The 80 frame bits of 86 cell values, once the guard cells check out."""
    if len(cells) != TOTAL_CELLS:
        raise FrameError(f"expected {TOTAL_CELLS} cells, got {len(cells)}")
    if tuple(cells[: len(PREAMBLE)]) != PREAMBLE:
        raise FrameError("bad preamble")
    if tuple(cells[-len(POSTAMBLE) :]) != POSTAMBLE:
        raise FrameError("bad postamble")
    return cells[len(PREAMBLE) : len(PREAMBLE) + FRAME_BITS]


def cells_to_frame(cells: Sequence[int]) -> Frame:
    """Decode 86 cell values that carry a live frame.

    Raises FrameError if the guards or CRC are wrong - and if the strip is
    showing a stat frame (use ``decode_cells`` to get either kind).
    """
    return Frame.from_bits(_frame_bits(cells))


def decode_cells(cells: Sequence[int]) -> Frame | StatFrame:
    """Decode 86 cell values into the frame they carry, live or stat.

    Raises FrameError if the guards or CRC are wrong.
    """
    return decode_bits(_frame_bits(cells))


def render_row(
    frame: Frame | StatFrame,
    cell_px: float = DEFAULT_CELL_PX,
    *,
    white: int = 255,
    black: int = 0,
    offset: int = 0,
    width: int | None = None,
    background: int = 0,
) -> bytes:
    """Render one row of brightness values the way the addon paints it.

    Used by tests and by the built-in simulator. ``cell_px`` may be
    fractional to mimic a mis-scaled UI; ``offset`` shifts the strip right.
    The quiet cell to the right of the strip is not part of it: append it,
    or give a ``width`` and a black ``background``.
    """
    cells = frame_to_cells(frame)
    strip_px = int(round(len(cells) * cell_px))
    total = max(width or 0, offset + strip_px)
    row = bytearray([background]) * total
    for x in range(strip_px):
        index = min(len(cells) - 1, int(x / cell_px))
        row[offset + x] = white if cells[index] else black
    return bytes(row)


@dataclass(frozen=True)
class Decoded:
    """A successfully decoded strip.

    It carried either a live frame (``frame``) or a stat frame (``stat``);
    the other one is None. They are kept apart on purpose: ``frame`` is
    what the belt follows, and a stat frame must never turn up there.
    """

    frame: Frame | None  # the live frame; None while the strip shows a stat frame
    x0: int  # pixel column where the strip starts
    cell_px: float  # measured cell pitch in pixels
    white: int  # measured brightness of white cells
    black: int  # measured brightness of black cells
    stat: StatFrame | None = None  # the stat frame, when that is what the strip shows

    def __post_init__(self) -> None:
        if self.frame is not None and not isinstance(self.frame, Frame):
            raise TypeError("Decoded.frame must be a live Frame")
        if self.stat is not None and not isinstance(self.stat, StatFrame):
            raise TypeError("Decoded.stat must be a StatFrame")
        if (self.frame is None) == (self.stat is None):
            raise ValueError("a decoded strip carries either a live frame or a stat frame")

    @property
    def seq(self) -> int:
        """The redraw counter of whichever frame the strip shows."""
        shown = self.frame if self.frame is not None else self.stat
        assert shown is not None
        return shown.seq


# ---- where a strip may begin -----------------------------------------------------

_THRESHOLD_TABLES: dict[int, bytes] = {}


def _threshold_table(threshold: int) -> bytes:
    table = _THRESHOLD_TABLES.get(threshold)
    if table is None:
        table = bytes(1 if value > threshold else 0 for value in range(256))
        _THRESHOLD_TABLES[threshold] = table
    return table


# To find places that look like a preamble at C speed, the row is cut into
# dark and bright at each of these brightness levels in turn. They are less
# than MIN_CONTRAST apart, so one of them lies between the black and the
# white of any strip - also of one whose white is dimmer than the game world
# next to it (HDR), which a single cut at the middle of the row's brightness
# range never finds.
_LEVELS: tuple[int, ...] = (24, 68, 112, 156, 200, 244)
assert all(b - a < MIN_CONTRAST for a, b in zip(_LEVELS, _LEVELS[1:], strict=False))

# Every frame this build reads opens the same way: the preamble (W B W B),
# then protocol version 1 (black, black, black, white) and the first bit of
# the frame type, which is 0 for both kinds of frame. As runs: a white, a
# black and a white cell, four black cells, a white cell, black.
HEADER: tuple[int, ...] = (1, 0, 1, 0, 0, 0, 0, 1, 0)
_HEADER_CELLS = 8  # cells from the first white run to the end of the fourth

# Where that run pattern begins right after a dark pixel. (The row gets one
# dark pixel in front for the search, so a strip that opens the row counts.)
# The widths are generous: this only proposes places, at C speed; the exact
# test is ``_header_runs``. The lookahead keeps one match from swallowing
# the next.
_HEADER_RE = re.compile(rb"\x00(?=\x01{1,17}\x00{1,17}\x01{1,17}\x00{6,66}\x01{1,17}\x00)")
_HEADER_SPAN = 4 * 17 + 66 + 1  # the most pixels that expression looks at
_MAX_RUN = 17  # no cell is wider (MAX_CELL_PX, rounded up)

# What "bright" means at a place is decided by the pixels that follow it: the
# first three cells of the largest size. For a strip that is strip and
# nothing else, whatever the rest of the row looks like.
_LOCAL_PX = 3 * int(MAX_CELL_PX)


def _starts(row: bytes | bytearray, last_start: int) -> list[int]:
    """Columns up to ``last_start`` where a strip may begin, left to right."""
    end = min(len(row), last_start + 1 + _HEADER_SPAN)
    if end < len(row):
        row = row[:end]
    if not row:
        return []
    lo, hi = min(row), max(row)
    if hi - lo < MIN_CONTRAST:
        return []
    found: set[int] = set()
    for level in _LEVELS:
        if not lo <= level < hi:
            continue
        for match in _HEADER_RE.finditer(b"\x00" + row.translate(_threshold_table(level))):
            if match.start() > last_start:
                break
            found.add(match.start())
    return sorted(found)


def _header_runs(row: bytes | bytearray, x: int) -> tuple[int, ...] | None:
    """The widths of the five runs a strip begins with (white, black, white,
    four cells of black, white), measured from column ``x`` - or None if
    that is not what begins there.

    Bright and dark are split halfway between the darkest and the brightest
    of the pixels that follow ``x`` - not of the whole row, which may hold
    things far brighter than the strip.
    """
    window = row[x : x + _LOCAL_PX]
    lo, hi = min(window), max(window)
    if hi - lo < MIN_CONTRAST:
        return None
    mid = (lo + hi) / 2.0
    if row[x] <= mid or (x > 0 and row[x - 1] > mid):
        return None  # not where something bright begins
    n = len(row)
    runs: list[int] = []
    pos = x
    bright = True
    for longest in (_MAX_RUN, _MAX_RUN, _MAX_RUN, 4 * _MAX_RUN, _MAX_RUN):
        start = pos
        stop = min(n, start + longest + 1)
        while pos < stop and (row[pos] > mid) == bright:
            pos += 1
        if pos == stop:
            return None  # wider than any cell, or cut off by the end of the row
        runs.append(pos - start)
        bright = not bright
    # Cells of one strip are equally wide, give or take the pixel that a
    # pitch which is not a whole number costs at either end of a run.
    pitch = sum(runs) / _HEADER_CELLS
    for index, run in enumerate(runs):
        cells = 4 if index == 3 else 1
        if abs(run - cells * pitch) > 1.2 + 0.1 * cells:
            return None
    return tuple(runs)


# ---- reading the cells -----------------------------------------------------------


def _candidate_pitches(base: float) -> Iterable[float]:
    """Yield pitch guesses, nearest to the measured ``base`` first.

    ``base`` comes from runs that span eight cells, each end of which is
    known to half a pixel: it is within an eighth of a pixel of the truth.
    Sampling stays inside the right cell across the whole strip only if the
    pitch is right to about 1/170 of a cell, hence the fine steps.
    """
    if base >= MIN_CELL_PX:
        yield base
    step = 0.0125
    for i in range(1, 13):
        delta = i * step
        if base + delta >= MIN_CELL_PX:
            yield base + delta
        if base - delta >= MIN_CELL_PX:
            yield base - delta


# Cell indices checked before sampling a whole strip: the guard cells, and
# the quiet cell the addon keeps black to the right of them.
_QUIET_CELL = TOTAL_CELLS
_GUARDS: tuple[tuple[int, int], ...] = tuple(
    [(i, v) for i, v in enumerate(PREAMBLE)]
    + [(TOTAL_CELLS - len(POSTAMBLE) + i, v) for i, v in enumerate(POSTAMBLE)]
    + [(_QUIET_CELL, 0)]
)


@dataclass(frozen=True)
class _Reading:
    """One row read as a strip, with what is needed to look at that same
    strip in the rows above and below."""

    decoded: Decoded
    cells: tuple[int, ...]
    start: float  # where the first cell begins (may fall between two pixels)
    pitch: float  # the pitch the cells were sampled at
    threshold: float
    quiet: tuple[int, int]  # pixel columns [from, to) that the addon keeps black right of the last white cell


def _sample_cells(row: Sequence[int], start: float, pitch: float, threshold: float) -> list[int] | None:
    # The strip and the middle of its quiet cell have to be in the picture.
    if start + (_QUIET_CELL + 0.5) * pitch >= len(row) or start + 0.5 * pitch < 0:
        return None
    # Cheap rejection: the guard cells must match before we bother sampling
    # all 86.
    for index, expected in _GUARDS:
        if (row[int(start + (index + 0.5) * pitch)] > threshold) != bool(expected):
            return None
    return [1 if row[int(start + (i + 0.5) * pitch)] > threshold else 0 for i in range(TOTAL_CELLS)]


def _is_dark(row: Sequence[int], span: tuple[int, int], threshold: float) -> bool:
    """Is there nothing bright in these columns (and are they all in the row)?"""
    pixels = row[span[0] : span[1]]
    return len(pixels) == span[1] - span[0] and max(pixels) <= threshold


def _right_end(row: Sequence[int], x0: int, start: float, pitch: float, threshold: float) -> tuple[float, int]:
    """Where the last white cell (number 84) ends, and from that the cell
    pitch to a hundredth of a pixel.

    The pitch the cells were sampled at only has to keep every sample inside
    its cell, so at the far end it can be off by pixels - too vague to say
    where the black to the right of the strip begins and ends. The edge of
    the last white cell is 85 cells from the start: that pins it down.
    """
    edge = int(start + (TOTAL_CELLS - 1.5) * pitch)  # the sample in cell 84, which is white ...
    stop = int(start + (TOTAL_CELLS - 0.5) * pitch)  # ... and the one in cell 85, which is black
    while edge < stop and row[edge] > threshold:
        edge += 1
    return (edge - x0) / (TOTAL_CELLS - 1.0), edge


def _try_decode_at(row: bytes | bytearray, x0: int, runs: Sequence[int]) -> _Reading | None:
    """Try to decode a strip that begins at ``x0`` with the given header runs."""
    base = sum(runs) / _HEADER_CELLS
    # (The runs are whole pixels: cells of 1.9 px can measure a little less.)
    if not MIN_CELL_PX - 0.15 <= base <= MAX_CELL_PX:
        return None

    # Calibrate black/white from the centres of the first three cells.
    w1 = row[x0 + runs[0] // 2]
    b1 = row[x0 + runs[0] + runs[1] // 2]
    w2 = row[x0 + runs[0] + runs[1] + runs[2] // 2]
    white = (w1 + w2) / 2.0
    black = float(b1)
    if white - black < MIN_CONTRAST:
        return None
    threshold = (white + black) / 2.0

    for pitch in _candidate_pitches(base):
        # The strip may start up to half a pixel either side of the first
        # bright column when cells are not a whole number of pixels wide.
        for shift in (0.0, -0.5, 0.5):
            start = x0 + shift
            cells = _sample_cells(row, start, pitch, threshold)
            if cells is None:
                continue
            try:
                frame = decode_cells(cells)
            except FrameError:
                continue
            # Good CRC. Now: is it something the addon draws?
            if not_from_the_addon(frame) is not None:
                continue
            exact, edge = _right_end(row, x0, start, pitch, threshold)
            # Black from there on: the last cell and the quiet cell (less a
            # pixel, for an edge that falls between two).
            quiet = (edge, edge + int(2 * exact - 1))
            if exact < MIN_CELL_PX or not _is_dark(row, quiet, threshold):
                continue
            geometry = (x0, exact, int(round(white)), int(round(black)))
            if isinstance(frame, Frame):
                decoded = Decoded(frame, *geometry)
            else:
                decoded = Decoded(None, *geometry, stat=frame)
            return _Reading(decoded, tuple(cells), start, pitch, threshold, quiet)
    return None


def _read_row(row: Sequence[int] | bytes, max_start: int | None) -> _Reading | None:
    if not isinstance(row, (bytes, bytearray)):
        row = bytes(row)
    # A strip cannot start so far right that it and its quiet cell would not
    # fit in the row.
    last_start = len(row) - int((_QUIET_CELL + 0.5) * MIN_CELL_PX) - 1
    if max_start is not None:
        last_start = min(last_start, max_start)
    if last_start < 0:
        return None
    attempts = 0
    for x in _starts(row, last_start):
        runs = _header_runs(row, x)
        if runs is None:
            continue
        reading = _try_decode_at(row, x, runs)
        if reading is not None:
            return reading
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            break
    return None


def decode_row(row: Sequence[int] | bytes, *, max_start: int | None = None) -> Decoded | None:
    """Find and decode a beacon strip in one row of brightness values (0-255).

    ``max_start`` limits how far from the left edge the strip may begin
    (``None`` = anywhere). Returns ``None`` if no valid frame is found. The
    frame is in ``Decoded.frame`` if it is a live frame and in
    ``Decoded.stat`` if it is a stat frame.

    A frame is only returned if all of this holds, as it does for every row
    of a strip the addon draws:

    * the row opens the way every frame does (``HEADER``): cells of one
      width, at least ``MIN_CELL_PX`` wide, white-black-white, four black,
      one white;
    * preamble, postamble and CRC-16 check out, read at one steady pitch;
    * it is black to the right of the last white cell for two cells: the
      last cell of the postamble and the quiet zone;
    * the frame is a kind this build knows, its reserved bits are zero, it
      sets no flag that is not assigned and, if it is a stat frame, names a
      stat that exists (``protocol.not_from_the_addon``).

    One row cannot show that it is part of a *strip*: for what a screen
    capture is asked on top of this, see ``strip_in``.
    """
    reading = _read_row(row, max_start)
    return reading.decoded if reading is not None else None


# ---- a row of a strip ----------------------------------------------------------------


def _shows(row: Sequence[int], reading: _Reading) -> bool:
    """Does this row show the same cells, at the same place, as ``reading``?"""
    start, pitch, threshold = reading.start, reading.pitch, reading.threshold
    if start + (_QUIET_CELL + 0.5) * pitch >= len(row):
        return False
    for i, cell in enumerate(reading.cells):
        if (row[int(start + (i + 0.5) * pitch)] > threshold) != bool(cell):
            return False
    return _is_dark(row, reading.quiet, threshold)


def _is_fringe(row: Sequence[int], above: Sequence[int], span: tuple[int, int]) -> bool:
    """Is ``row`` a fainter copy of the strip row above it? That is what the
    lower edge of the strip looks like when the game window is being scaled
    and the edge falls between two rows of pixels."""
    pixels, strip = row[span[0] : span[1]], above[span[0] : span[1]]
    return len(pixels) == span[1] - span[0] and all(a <= b + 8 for a, b in zip(pixels, strip, strict=True))


def strip_in(rows: Sequence[bytes], y: int, *, max_start: int | None = None) -> Decoded | None:
    """Decode row ``y`` of a block of pixel rows (top row first) if it is a
    row of a strip - not merely a row that happens to decode.

    The addon's cells are squares, and the strip sits on a black backing
    that sticks out one cell below it (``layout`` in ``Walka/Beacon.lua``).
    So, on top of everything ``decode_row`` asks of the row itself:

    * the rows next to it show the very same cells, over a height of one
      cell (give or take a pixel) and never less than two rows;
    * under those it is black from the first cell to the end of the quiet
      cell (directly, or after one fainter row where a scaled window blurs
      the strip's lower edge).

    Lines of text decode by accident now and then, but they are not built
    like that: neighbouring rows of letters differ, and there is rarely a
    clean black line right underneath.
    """
    reading = _read_row(rows[y], max_start)
    if reading is None:
        return None
    reach = int(MAX_CELL_PX) + 2  # a strip is never taller than this
    top = y
    while top > 0 and y - top < reach and _shows(rows[top - 1], reading):
        top -= 1
    bottom = y
    while bottom + 1 < len(rows) and bottom - y < reach and _shows(rows[bottom + 1], reading):
        bottom += 1
    height = bottom - top + 1
    if height < 2:
        return None
    # What is under the strip. (If it cannot be seen, nothing is proven.)
    under = (max(0, int(reading.start)), reading.quiet[1])
    fringe = 0
    if bottom + 1 < len(rows) and not _is_dark(rows[bottom + 1], under, reading.threshold):
        if not _is_fringe(rows[bottom + 1], rows[bottom], under):
            return None
        fringe = 1
    below = bottom + 1 + fringe
    if below >= len(rows) or not _is_dark(rows[below], under, reading.threshold):
        return None
    pitch = reading.decoded.cell_px
    if not height - 1.05 <= pitch <= height + fringe + 1.05:
        return None  # not as tall as its cells are wide
    return reading.decoded


# ---- fast pre-filter for whole-screen searches ---------------------------------


def row_may_contain_beacon(row: bytes) -> bool:
    """Cheap C-speed test used to skip rows when scanning a whole screen.

    It must never drop a row ``decode_row`` would decode, so it asks the
    very question ``decode_row`` starts with: is there, at one of the
    brightness levels tried, a place that looks like the start of a frame?
    """
    if not row:
        return False
    lo, hi = min(row), max(row)
    if hi - lo < MIN_CONTRAST:
        return False
    return any(
        lo <= level < hi and _HEADER_RE.search(b"\x00" + row.translate(_threshold_table(level))) is not None
        for level in _LEVELS
    )
