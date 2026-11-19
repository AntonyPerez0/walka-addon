"""Land drawn from the road itself, for a land the companion has no map of.

The Share picture draws the road over a low-poly map of its continent
(``static/lands.js``). Those maps exist for Kalimdor and the Eastern
Kingdoms. A character can be somewhere else - the Skyborne begin on Zephras
Isle, an island in Skywall - and where the coast of such a land runs is
known to nobody here: an addon cannot ask the game, and no map of it is
shipped.

What *is* known is where the character has walked, and where it has walked
there is ground. So such a land is charted the way a traveller charts one:
the ground along the road, a little to either side, and whatever the road
goes around. The picture of it grows with the road. It is a sketch, not the
island's true outline, and it is made of the same things as the shipped
maps, so the page draws it the same way:

    bounds: [min x, min y, max x, max y]
    points: [x0, y0, x1, y1, ...]                   the corners
    coast:  [[corner, corner, ...], ...]            the outline: one ring per piece of land
    faces:  [corner, corner, corner, shade, ...]    triangles; shade 0 (dark) to 9 (lit)

World yards, x to the north and y to the west - the road's own coordinates.

How: a honeycomb of triangles is laid over the world (always the same one
for a given size of facet, so that the sketch does not jump about as the
road grows), and every triangle whose middle is within reach of the road is
land. What the land encloses is land too, and where two pieces would touch
at a single corner they are joined, so the coast is a plain outline. The
relief is made up, as it is on the shipped maps.

Pure Python, no dependencies; a road of a few thousand points takes a few
milliseconds.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from typing import Any

Point = tuple[float, float]

# A facet's size in yards: about a dozen of them across the road, from
# this ladder (a road that grows a little keeps the honeycomb it had).
FACET_SIZES = (70, 91, 118, 154, 200, 260, 338, 439, 571, 742, 965)
ACROSS = 11
REACH = 1.25  # how far from the road is land, in facets
JITTER = 0.22  # how far a corner of the honeycomb is moved, in facets
SHADES = 10
MAX_TRIANGLES = 60_000  # of the honeycomb looked at (a road across a whole world needs a few thousand)

Triangle = tuple[int, int, int]  # column, row, 0 | 1
Corner = tuple[int, int]  # column, row


def _facet(extent: float) -> int:
    size = FACET_SIZES[0]
    for candidate in FACET_SIZES:
        if candidate * ACROSS <= extent:
            size = candidate
    return size


def _mix(i: int, j: int, salt: int) -> float:
    """A number from -1 to 1 that depends on nothing but (i, j, salt)."""
    h = (i * 0x9E3779B1 + j * 0x85EBCA77 + salt * 0xC2B2AE3D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    h = (h * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 2**31 - 1.0


def _relief() -> list[tuple[float, float, float, float]]:
    rng = random.Random(1160)
    return [
        (rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi), length, weight)
        for length, weight in ((8.2, 1.0), (4.7, 0.8), (2.8, 0.6), (1.7, 0.45), (1.1, 0.3))
        for _ in range(3)
    ]


_WAVES = _relief()


def _height(x: float, y: float) -> float:
    """A smooth made-up relief over the honeycomb (x, y in facets)."""
    total = 0.0
    for angle, phase, length, weight in _WAVES:
        total += weight * math.sin((x * math.cos(angle) + y * math.sin(angle)) * 2 * math.pi / length + phase)
    return total


def _corners(triangle: Triangle) -> tuple[Corner, Corner, Corner]:
    """The corners of a triangle of the honeycomb. Rows lie `row` yards
    apart; every other row is moved half a facet along."""
    i, j, half = triangle
    if j % 2 == 0:
        return ((i, j), (i + 1, j), (i, j + 1)) if half == 0 else ((i + 1, j), (i + 1, j + 1), (i, j + 1))
    return ((i, j), (i + 1, j + 1), (i, j + 1)) if half == 0 else ((i, j), (i + 1, j), (i + 1, j + 1))


def land(lines: Iterable[Sequence[Point]]) -> dict[str, Any] | None:
    """The land along `lines` (each a run of points, world yards), as a map
    the Share page can draw; None if there is no road to chart."""
    lines = [line for line in lines if line]
    xs = [x for line in lines for x, _y in line]
    ys = [y for line in lines for _x, y in line]
    if not xs or not all(math.isfinite(value) for value in xs + ys):
        return None
    facet = _facet(max(max(xs) - min(xs), max(ys) - min(ys)))
    row = facet * math.sqrt(3) / 2
    reach = REACH * facet

    # The road as dots no further apart than half the reach, filed by where they are.
    cells: dict[tuple[int, int], list[Point]] = {}

    def dot(x: float, y: float) -> None:
        cells.setdefault((math.floor(x / reach), math.floor(y / reach)), []).append((x, y))

    for line in lines:
        dot(*line[0])
        for (ax, ay), (bx, by) in zip(line, line[1:], strict=False):
            pieces = max(1, math.ceil(math.hypot(bx - ax, by - ay) / (reach / 2)))
            for step in range(1, pieces + 1):
                dot(ax + (bx - ax) * step / pieces, ay + (by - ay) * step / pieces)

    def near_road(x: float, y: float) -> bool:
        cx, cy = math.floor(x / reach), math.floor(y / reach)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in cells.get((cx + dx, cy + dy), ()):
                    if (px - x) ** 2 + (py - y) ** 2 <= reach * reach:
                        return True
        return False

    # The part of the honeycomb around the road, with room to spare.
    spare = reach + 4 * facet
    first_j, last_j = math.floor((min(xs) - spare) / row), math.ceil((max(xs) + spare) / row)
    first_i, last_i = math.floor((min(ys) - spare) / facet) - 1, math.ceil((max(ys) + spare) / facet)
    if 2 * (last_j - first_j) * (last_i - first_i) > MAX_TRIANGLES:
        return None

    places: dict[Corner, Point] = {}

    def place(corner: Corner) -> Point:
        known = places.get(corner)
        if known is None:
            i, j = corner
            known = places[corner] = (
                j * row + JITTER * facet * _mix(i, j, 1),
                (i + 0.5 * (j % 2)) * facet + JITTER * facet * _mix(i, j, 2),
            )
        return known

    triangles = [(i, j, half) for j in range(first_j, last_j) for i in range(first_i, last_i) for half in (0, 1)]
    across: dict[tuple[Corner, Corner], list[Triangle]] = {}  # an edge -> the triangles on it
    around: dict[Corner, list[Triangle]] = {}  # a corner -> the triangles at it
    ground: set[Triangle] = set()
    for triangle in triangles:
        a, b, c = _corners(triangle)
        for edge in ((a, b), (b, c), (c, a)):
            across.setdefault((min(edge), max(edge)), []).append(triangle)
        for corner in (a, b, c):
            around.setdefault(corner, []).append(triangle)
        (ax, ay), (bx, by), (cx, cy) = place(a), place(b), place(c)
        if near_road((ax + bx + cx) / 3, (ay + by + cy) / 3):
            ground.add(triangle)
    if not ground:
        return None

    def neighbours(triangle: Triangle) -> list[Triangle | None]:
        a, b, c = _corners(triangle)
        out: list[Triangle | None] = []
        for edge in ((a, b), (b, c), (c, a)):
            pair = across[(min(edge), max(edge))]
            out.append(next((other for other in pair if other != triangle), None))
        return out

    def fill_what_is_enclosed() -> None:
        """Water that cannot be reached from outside is not water."""
        sea = [t for t in triangles if t not in ground and None in neighbours(t)]
        open_water = set(sea)
        while sea:
            for other in neighbours(sea.pop()):
                if other is not None and other not in ground and other not in open_water:
                    open_water.add(other)
                    sea.append(other)
        ground.update(t for t in triangles if t not in open_water)

    def turned(triangle: Triangle) -> tuple[Corner, Corner, Corner]:
        """Its corners, always the same way round."""
        a, b, c = _corners(triangle)
        (ax, ay), (bx, by), (cx, cy) = place(a), place(b), place(c)
        return (a, b, c) if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0 else (a, c, b)

    def coast_edges() -> dict[Corner, list[Corner]]:
        """The edges with land on one side only, each from corner to corner
        with the land on the same hand."""
        out: dict[Corner, list[Corner]] = {}
        for triangle in ground:
            a, b, c = turned(triangle)
            for start, end in ((a, b), (b, c), (c, a)):
                pair = across[(min(start, end), max(start, end))]
                other = next((t for t in pair if t != triangle), None)
                if other is None or other not in ground:
                    out.setdefault(start, []).append(end)
        return out

    fill_what_is_enclosed()
    edges = coast_edges()
    for _ in range(12):
        # Two pieces of land that touch at one corner only: joined there.
        pinched = [corner for corner, ends in edges.items() if len(ends) > 1]
        if not pinched:
            break
        for corner in pinched:
            ground.update(around[corner])
        fill_what_is_enclosed()
        edges = coast_edges()

    # The coast: one ring per piece of land, the largest first.
    rings: list[list[Corner]] = []
    left = {corner: list(ends) for corner, ends in edges.items()}
    for start in sorted(left):
        while left[start]:
            ring = [start]
            at = left[start].pop()
            while at != start:
                ring.append(at)
                at = left[at].pop()
            rings.append(ring)
    rings.sort(key=len, reverse=True)

    # How high each corner stands: nothing at the coast, more inland.
    inland: dict[Corner, int] = {corner: 0 for ring in rings for corner in ring}
    joined: dict[Corner, set[Corner]] = {}
    for triangle in ground:
        a, b, c = _corners(triangle)
        for one, other in ((a, b), (b, c), (c, a)):
            joined.setdefault(one, set()).add(other)
            joined.setdefault(other, set()).add(one)
    wave = list(inland)
    while wave:
        further = []
        for corner in wave:
            for other in joined[corner]:
                if other not in inland:
                    inland[other] = inland[corner] + 1
                    further.append(other)
        wave = further

    def height(corner: Corner) -> float:
        steps = inland.get(corner, 0)
        if steps == 0:
            return 0.0
        rise = min(1.0, steps / 2.5)
        x, y = place(corner)
        return facet * rise * (0.24 * (0.5 + 0.5 * rise) + 0.19 * _height(x / facet, y / facet))

    # The corners that are used, numbered; the coast's first.
    number: dict[Corner, int] = {}
    points: list[int] = []

    def numbered(corner: Corner) -> int:
        if corner not in number:
            number[corner] = len(number)
            x, y = place(corner)
            points.extend((round(x), round(y)))
        return number[corner]

    coast = [[numbered(corner) for corner in ring] for ring in rings]

    light = (0.52, 0.48, 0.70)  # from the north-west, and above
    length = math.sqrt(sum(part * part for part in light))
    light = (light[0] / length, light[1] / length, light[2] / length)
    lit: list[tuple[int, int, int, float]] = []
    for triangle in sorted(ground):
        a, b, c = turned(triangle)
        (ax, ay), (bx, by), (cx, cy) = place(a), place(b), place(c)
        ha, hb, hc = height(a), height(b), height(c)
        # The facet's slope, as a normal: x north, y west, z up.
        ux, uy, uz = bx - ax, by - ay, hb - ha
        vx, vy, vz = cx - ax, cy - ay, hc - ha
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if nz < 0:
            nx, ny, nz = -nx, -ny, -nz
        size = math.sqrt(nx * nx + ny * ny + nz * nz)
        lit.append((numbered(a), numbered(b), numbered(c), (nx * light[0] + ny * light[1] + nz * light[2]) / size))
    values = sorted(value for _a, _b, _c, value in lit)
    low, high = values[len(values) // 50], values[-1 - len(values) // 50]
    faces: list[int] = []
    for a, b, c, value in lit:
        shade = (SHADES - 1) * min(1.0, max(0.0, (value - low) / (high - low))) if high > low else (SHADES - 1) / 2
        faces.extend((a, b, c, int(round(shade))))

    xs_out, ys_out = points[0::2], points[1::2]
    return {
        "bounds": [min(xs_out), min(ys_out), max(xs_out), max(ys_out)],
        "points": points,
        "coast": coast,
        "faces": faces,
    }
