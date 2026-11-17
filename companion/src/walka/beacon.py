"""Walka pixel beacon: the on-screen strip that carries a protocol frame.

The addon paints the 80 frame bits as a single row of square black/white
cells in the top-left corner of the game window:

    cell:   0 1 2 3 | 4 ............ 83 | 84 85
            W B W B |   80 frame bits   |  W  B
            preamble   white=1 black=0    postamble

Only black and white are used (never colour), so the strip survives gamma,
contrast, HDR tone-mapping and colour-blind filters: the decoder only needs
"brighter or darker than the midpoint", and it measures that midpoint from
the preamble of every frame.

Every cell is ``cell`` physical pixels wide and tall (3 by default). The
decoder does not need to know the size: it measures the pitch from the
preamble and refines it until the postamble and CRC agree.

This module is pure Python (no numpy) and operates on rows of 8-bit
brightness values, so it can be tested without a screen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .protocol import FRAME_BITS, Frame, FrameError

PREAMBLE: tuple[int, ...] = (1, 0, 1, 0)
POSTAMBLE: tuple[int, ...] = (1, 0)
TOTAL_CELLS = len(PREAMBLE) + FRAME_BITS + len(POSTAMBLE)  # 86

DEFAULT_CELL_PX = 3
MIN_CELL_PX = 1.5
MAX_CELL_PX = 16.0

# Minimum brightness difference (0-255) between the white and black preamble
# cells for a row to be considered a beacon at all.
MIN_CONTRAST = 48


def frame_to_cells(frame: Frame) -> list[int]:
    """Return the 86 cell values (1 = white, 0 = black) for a frame."""
    return [*PREAMBLE, *frame.to_bits(), *POSTAMBLE]


def cells_to_frame(cells: Sequence[int]) -> Frame:
    """Decode 86 cell values. Raises FrameError if the guards or CRC are wrong."""
    if len(cells) != TOTAL_CELLS:
        raise FrameError(f"expected {TOTAL_CELLS} cells, got {len(cells)}")
    if tuple(cells[: len(PREAMBLE)]) != PREAMBLE:
        raise FrameError("bad preamble")
    if tuple(cells[-len(POSTAMBLE) :]) != POSTAMBLE:
        raise FrameError("bad postamble")
    return Frame.from_bits(cells[len(PREAMBLE) : len(PREAMBLE) + FRAME_BITS])


def render_row(
    frame: Frame,
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
    """A successfully decoded strip."""

    frame: Frame
    x0: int  # pixel column where the strip starts
    cell_px: float  # measured cell pitch in pixels
    white: int  # measured brightness of white cells
    black: int  # measured brightness of black cells


def _candidate_pitches(base: float) -> Iterable[float]:
    """Yield pitch guesses, nearest to the measured ``base`` first.

    ``base`` comes from three measured runs, so it is within a third of a
    pixel of the truth. Sampling stays inside the right cell across the whole
    strip only if the pitch is right to about 1/170 of a cell, hence the
    fine steps.
    """
    yield base
    step = 0.0125
    for i in range(1, 33):
        delta = i * step
        yield base + delta
        if base - delta >= MIN_CELL_PX * 0.9:
            yield base - delta


# Cell indices of the guard cells, checked before sampling a whole strip.
_GUARDS: tuple[tuple[int, int], ...] = tuple(
    [(i, v) for i, v in enumerate(PREAMBLE)] + [(TOTAL_CELLS - len(POSTAMBLE) + i, v) for i, v in enumerate(POSTAMBLE)]
)


def _sample_cells(row: Sequence[int], x0: float, pitch: float, threshold: float) -> list[int] | None:
    last = x0 + (TOTAL_CELLS - 0.5) * pitch
    if last >= len(row) or x0 + 0.5 * pitch < 0:
        return None
    # Cheap rejection: the six guard cells must match before we bother
    # sampling all 86.
    for index, expected in _GUARDS:
        if (row[int(x0 + (index + 0.5) * pitch)] > threshold) != bool(expected):
            return None
    return [1 if row[int(x0 + (i + 0.5) * pitch)] > threshold else 0 for i in range(TOTAL_CELLS)]


def _try_decode_at(row: Sequence[int], x0: int, runs: tuple[int, int, int]) -> Decoded | None:
    """Try to decode a strip whose first three cells have the given run lengths."""
    base = sum(runs) / 3.0
    if not MIN_CELL_PX <= base <= MAX_CELL_PX:
        return None
    if max(runs) - min(runs) > max(1.0, base * 0.5):
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
            cells = _sample_cells(row, x0 + shift, pitch, threshold)
            if cells is None:
                continue
            try:
                frame = cells_to_frame(cells)
            except FrameError:
                continue
            return Decoded(frame, x0, pitch, int(round(white)), int(round(black)))
    return None


def decode_row(row: Sequence[int] | bytes, *, max_start: int | None = None) -> Decoded | None:
    """Find and decode a beacon strip in one row of brightness values (0-255).

    ``max_start`` limits how far from the left edge the strip may begin
    (``None`` = anywhere). Returns ``None`` if no valid frame is found; a
    frame is only returned if its preamble, postamble and CRC-16 all check out.
    """
    n = len(row)
    if n < TOTAL_CELLS * MIN_CELL_PX:
        return None
    lo, hi = min(row), max(row)
    if hi - lo < MIN_CONTRAST:
        return None
    mid = (lo + hi) / 2.0

    # A strip cannot start so far right that it would not fit in the row.
    limit = n - int(TOTAL_CELLS * MIN_CELL_PX) + 1
    if max_start is not None:
        limit = min(limit, max_start + 1)
    x = 0
    attempts = 0
    while x < limit and attempts < 24:
        # Next rising edge: first bright pixel at or after x that follows a
        # dark pixel (or the row start).
        while x < limit and not (row[x] > mid and (x == 0 or row[x - 1] <= mid)):
            x += 1
        if x >= limit:
            break
        # Measure the W, B, W runs that open the preamble.
        runs = []
        pos = x
        want_bright = True
        while len(runs) < 3 and pos < n:
            start = pos
            while pos < n and (row[pos] > mid) == want_bright:
                pos += 1
            runs.append(pos - start)
            want_bright = not want_bright
        if len(runs) == 3 and pos < n:
            attempts += 1
            decoded = _try_decode_at(row, x, (runs[0], runs[1], runs[2]))
            if decoded is not None:
                return decoded
        x += 1
    return None


# ---- fast pre-filter for whole-screen searches ---------------------------------

_THRESHOLD_TABLES: dict[int, bytes] = {}


def _threshold_table(threshold: int) -> bytes:
    table = _THRESHOLD_TABLES.get(threshold)
    if table is None:
        table = bytes(1 if value > threshold else 0 for value in range(256))
        _THRESHOLD_TABLES[threshold] = table
    return table


_PREAMBLE_RE = re.compile(rb"\x01{2,16}\x00{2,16}\x01{2,16}\x00{2,}")


def row_may_contain_beacon(row: bytes, threshold: int | None = None) -> bool:
    """Cheap C-speed test used to skip rows when scanning a whole screen.

    It must never drop a row ``decode_row`` would decode, so by default it
    splits bright from dark at the same point ``decode_row`` does: halfway
    between the darkest and the brightest pixel of the row (a dim or
    washed-out strip has no pixel on one side of a fixed mid-grey).
    """
    if threshold is None:
        if not row:
            return False
        lo, hi = min(row), max(row)
        if hi - lo < MIN_CONTRAST:
            return False
        threshold = (lo + hi) // 2  # value > (lo + hi) / 2  <=>  value > (lo + hi) // 2
    return _PREAMBLE_RE.search(row.translate(_threshold_table(threshold))) is not None
