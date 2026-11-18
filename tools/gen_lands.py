"""Build companion/src/walka/static/lands.js: low-poly maps of the continents.

The Share page draws the road a character has travelled. A line on an empty
panel does not say much, so behind it goes a map - not the game's map art,
but a drawing of our own: each continent as a faceted ("low-poly") shape in
the picture's colours. This script makes those shapes.

Where the coast is
------------------
An addon cannot ask the game where land ends, and nothing of the game's art
may be used. What can be known is *where things are*: the world positions of
creatures and objects are public knowledge (every database site lists them),
and they are in the same coordinates the addon records the road in - world
yards, x to the north and y to the west. Where there are creatures there is
land (or shallow water just off it), so:

1. every position becomes a dot on a grid of 40-yard cells - but not what
   is at sea: creatures that only live in water, fishing spots, boats;
2. the dots are grown into blobs, gaps between zones (mountain ranges, where
   nothing lives) are closed, and what is enclosed (lakes, a crater) is
   filled in;
3. closing gaps also fills every bay narrower than about a mile, and the
   positions cannot tell a bay from a mountain range. So the bays that give
   the coasts their shape are named by hand (``BAYS``) and opened again;
4. the outline of that is traced, pushed outwards a little and straightened
   until it is a polygon of about a hundred corners that still holds all of
   the land the dots showed. A road can therefore leave the drawn land only
   where the character really was at sea (boats, swimming).

The positions themselves are not kept: what comes out is the generalised
outline, a few hundred numbers per continent.

    python tools/gen_lands.py points  DUMP.sql.gz  spawns.csv
    python tools/gen_lands.py build   spawns.csv   [lands.js]
    python tools/gen_lands.py preview [lands.js]   preview.png

``points`` reads a world database dump of an open server project (CMaNGOS'
classic-db was used: its ``creature`` and ``gameobject`` tables, and their
templates to tell what lives in water) as text - nothing in it is run - and
writes "map,x,y" lines for the two continents. That file is not kept in the
repository; see THIRD-PARTY-NOTICES.md. ``build`` writes the module. ``preview`` draws what is in the
module, to look at.

Facets
------
Inside the outline, points are scattered evenly and joined into triangles
(a Delaunay triangulation that keeps the outline's edges). Each corner is
given a made-up height - low at the coast, a gentle random relief inland -
and each triangle a shade from 0 to 9 by how it would catch light from the
north-west. The relief is invented for looks; only the outline is real.

Needs numpy, scipy, scikit-image, shapely and Pillow (none of which the
companion itself uses).
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LANDS_JS = REPO / "companion" / "src" / "walka" / "static" / "lands.js"

CELL = 40.0  # yards
# The continents: id (the game's own), name, and the part of the world its
# map covers - (west edge y, north edge x, width, height) in world yards.
CONTINENTS = {
    1: ("Kalimdor", (17066.6, 12799.9, 36799.81, 24533.2)),
    0: ("Eastern Kingdoms", (18171.97, 11176.34, 40741.18, 27149.69)),
}

GROW_YD = 240.0  # how far a dot counts as land
CLOSE_YD = 520.0  # gaps narrower than twice this are bridged
BAY_CLOSE_YD = 120.0  # ... except in the bays named below, where only this much is
SHRINK_YD = 150.0  # ... and this much of the growing is taken back
MIN_ISLAND_YD2 = 1.5e6  # smaller pieces are left out
TOLERANCE_YD = 130.0  # how far the straightened outline may stray
FACET_YD = 1100.0  # the size of a facet
ROUND_YD = 10  # coordinates are written to the nearest ten yards
SHADES = 10


# Bridging gaps is what makes the land whole - most gaps are mountains where
# nothing lives - but it also fills every bay narrower than about a mile.
# These are the bays worth keeping, each named by a place in its water
# (x north, y west). Which gaps are sea and which are mountain cannot be
# told from the positions; this list is from knowing the maps.
BAYS = {
    1: [
        (3500, -7000, "the Bay of Storms in Azshara"),
        (2000, -5200, "the mouth of the Southfury River, between Azshara and Durotar"),
        (-2000, -5000, "the bay south of Durotar, off Ratchet"),
        (-3000, -5000, "Dustwallow Bay, between Alcaz Island and Theramore"),
        (-5600, 3000, "the strait behind the Isle of Dread, off Feralas"),
    ],
    0: [
        (-2200, -800, "Baradin Bay, north of Menethil Harbor"),
        (-2200, -3300, "the sea east of the Thandol Span"),
        (-900, -4300, "the coast between the Hinterlands and Arathi"),
    ],
}

# ---- points ---------------------------------------------------------------------------------------


# A row of an SQL "INSERT ... VALUES (...),(...)" and the values in it. Text
# values are quoted and may hold commas and brackets.
_ROW = re.compile(r"\((?:[^()']|'(?:[^'\\]|\\.|'')*')*\)")
_VALUE = re.compile(r"'(?:[^'\\]|\\.|'')*'|[^,]+")

WATER_ONLY = 2  # creature_template.InhabitType: lives in water and nowhere else
# gameobject_template.type: boats and lifts, a fishing float, a school of fish.
NOT_ON_LAND = {11, 15, 17, 25}


def read_points(dump: Path, out: Path) -> None:
    """(map, x, y) of every spawn on the two continents that stands on land,
    from the SQL text.

    Left out: creatures that only live in water, fishing spots and boats -
    they would draw land where there is sea. (How deep something lies says
    nothing: the floor of a crater is far below sea level, and dry.)
    """
    create = re.compile(r"CREATE TABLE `(\w+)`")
    column = re.compile(r"^\s*`(\w+)`")
    insert = re.compile(r"INSERT INTO `(\w+)`(?: \(([^)]*)\))? VALUES")
    wanted = {
        "creature": ("id", "map", "position_x", "position_y"),
        "gameobject": ("id", "map", "position_x", "position_y"),
        "creature_template": ("Entry", "InhabitType"),
        "gameobject_template": ("entry", "type"),
    }
    schema: dict[str, list[str]] = {}
    rows: dict[str, list[tuple[str, ...]]] = {name: [] for name in wanted}
    current = None
    opener = gzip.open if dump.suffix == ".gz" else open
    with opener(dump, "rt", encoding="utf-8", errors="replace") as source:
        for line in source:
            match = create.search(line)
            if match:
                current = match.group(1)
                schema[current] = []
                continue
            if current:
                if line.startswith(")"):
                    current = None
                else:
                    name = column.match(line)
                    if name:
                        schema[current].append(name.group(1))
                continue
            match = insert.match(line)
            if not match or match.group(1) not in wanted:
                continue
            table = match.group(1)
            columns = [c.strip(" `") for c in match.group(2).split(",")] if match.group(2) else schema[table]
            at = [columns.index(name) for name in wanted[table]]
            for row in _ROW.finditer(line, match.end()):
                values = _VALUE.findall(row.group()[1:-1])
                if len(values) == len(columns):
                    rows[table].append(tuple(values[index].strip() for index in at))
    inhabit = {int(entry): int(kind) for entry, kind in rows["creature_template"]}
    kinds = {int(entry): int(kind) for entry, kind in rows["gameobject_template"]}
    count = skipped = 0
    with open(out, "w", encoding="ascii") as sink:
        for table in ("creature", "gameobject"):
            for entry, continent, x, y in rows[table]:
                if int(continent) not in CONTINENTS:
                    continue
                if table == "creature":
                    at_sea = inhabit.get(int(entry)) == WATER_ONLY
                else:
                    at_sea = kinds.get(int(entry)) in NOT_ON_LAND
                if at_sea:
                    skipped += 1
                    continue
                sink.write(f"{int(continent)},{float(x):.0f},{float(y):.0f}\n")
                count += 1
    print(f"{count} positions written to {out} ({skipped} at sea left out)")


# ---- the outline ----------------------------------------------------------------------------------


def disc(radius_yd: float):
    import numpy as np

    r = max(1, int(round(radius_yd / CELL)))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= r * r


def _blobs(grid, close_yd: float):
    from scipy import ndimage as ndi

    mask = ndi.binary_dilation(grid, disc(GROW_YD))
    mask = ndi.binary_closing(mask, disc(close_yd))
    mask = ndi.binary_fill_holes(mask)
    return ndi.binary_erosion(mask, disc(SHRINK_YD))


def land_mask(points, frame, bays=()):
    """Dots -> a grid that is True where there is land."""
    import numpy as np
    from scipy import ndimage as ndi

    west, north, width, height = frame
    cols, rows = int(width / CELL) + 1, int(height / CELL) + 1
    col = ((west - points[:, 1]) / CELL).astype(int)
    row = ((north - points[:, 0]) / CELL).astype(int)
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    pad = int((GROW_YD + CLOSE_YD) / CELL) + 6
    grid = np.zeros((rows + 2 * pad, cols + 2 * pad), bool)
    grid[row[inside] + pad, col[inside] + pad] = True
    mask = _blobs(grid, CLOSE_YD)
    if bays:
        # What the bridging closed that a lighter hand would have left open:
        # of those gaps, the ones named as bays are opened again.
        gaps, _count = ndi.label(mask & ~_blobs(grid, BAY_CLOSE_YD))
        reach = int(400 / CELL)
        for x, y, name in bays:
            r, c = int((north - x) / CELL) + pad, int((west - y) / CELL) + pad
            near = gaps[r - reach : r + reach + 1, c - reach : c + reach + 1]
            found = np.unique(near[near > 0])
            if not len(found):
                raise SystemExit(f"no closed gap at {name} ({x}, {y}): the list of bays no longer fits the positions")
            for gap in found:
                bay = gaps == gap
                if not (ndi.binary_dilation(bay, disc(2 * CELL)) & ~mask).any():
                    raise SystemExit(f"{name}: the gap there is not open to the sea")
                mask &= ~bay
                print(f"    opened {name}: {bay.sum() * CELL * CELL / 1e6:.2f} million square yards")
    labels, count = ndi.label(mask)
    areas = ndi.sum(mask, labels, range(1, count + 1)) * CELL * CELL
    mask = np.isin(labels, [index + 1 for index, area in enumerate(areas) if area >= MIN_ISLAND_YD2])
    return mask, pad


def outline(points, frame, bays=()):
    """The land as polygons in world yards (x north, y west), largest first."""
    import numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from skimage import measure

    west, north, _width, _height = frame
    mask, pad = land_mask(points, frame, bays)
    # A soft edge, so that the traced line has no steps in it.
    from scipy import ndimage as ndi

    soft = ndi.gaussian_filter(mask.astype(float), 2.0)
    rings = []
    for contour in measure.find_contours(soft, 0.5):
        if len(contour) < 4:
            continue
        x = north - (contour[:, 0] - pad + 0.5) * CELL
        y = west - (contour[:, 1] - pad + 0.5) * CELL
        ring = Polygon(np.column_stack([x, y]))
        if ring.is_valid and ring.area >= MIN_ISLAND_YD2:
            rings.append(Polygon(ring.exterior))  # (no lakes)
    land = unary_union(rings)
    # Outwards by the tolerance, then straightened by no more than it: the
    # result holds everything the mask did.
    low = land.buffer(TOLERANCE_YD, join_style="mitre", mitre_limit=2.0).simplify(TOLERANCE_YD, preserve_topology=True)
    parts = list(low.geoms) if hasattr(low, "geoms") else [low]
    parts = [Polygon(part.exterior) for part in parts if part.area >= MIN_ISLAND_YD2]
    parts.sort(key=lambda part: part.area, reverse=True)
    assert all(part.is_valid for part in parts)
    missed = land.difference(unary_union(parts)).area
    assert missed < 1e-6 * land.area, f"the outline lost {missed:.0f} square yards of land"
    return parts, land


# ---- facets ---------------------------------------------------------------------------------------


def _noise(seed: int):
    """A smooth made-up relief: a few waves of random direction and length."""
    import numpy as np

    rng = np.random.default_rng(seed)
    waves = [
        (rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi), length, weight)
        for length, weight in ((9000, 1.0), (5200, 0.8), (3100, 0.6), (1900, 0.45), (1200, 0.3))
        for _ in range(3)
    ]

    def height(x, y):
        total = 0.0
        for angle, phase, length, weight in waves:
            total += weight * math.sin((x * math.cos(angle) + y * math.sin(angle)) * 2 * math.pi / length + phase)
        return total

    return height


def facets(parts, seed: int):
    """Triangles over the polygons: (vertices, rings, faces with a shade)."""
    import numpy as np
    from scipy.spatial import Delaunay
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.prepared import prep

    rng = np.random.default_rng(seed)
    land = unary_union(parts)
    inner = land.buffer(-0.42 * FACET_YD)
    inner_ok = prep(inner)

    vertices: list[tuple[float, float]] = []
    index: dict[tuple[float, float], int] = {}

    def vertex(x: float, y: float) -> int:
        key = (round(x / ROUND_YD) * ROUND_YD, round(y / ROUND_YD) * ROUND_YD)
        if key not in index:
            index[key] = len(vertices)
            vertices.append(key)
        return index[key]

    # The outline first, long edges cut so that no facet is a sliver along one.
    rings: list[list[int]] = []
    for part in parts:
        coords = list(part.exterior.coords)[:-1]
        ring: list[int] = []
        for (ax, ay), (bx, by) in zip(coords, coords[1:] + coords[:1], strict=True):
            pieces = max(1, round(math.hypot(bx - ax, by - ay) / (0.85 * FACET_YD)))
            for step in range(pieces):
                t = step / pieces
                at = vertex(ax + (bx - ax) * t, ay + (by - ay) * t)
                if not ring or ring[-1] != at:
                    ring.append(at)
        if ring[0] == ring[-1]:
            ring.pop()
        rings.append(ring)
    coast = len(vertices)

    # Then the inside: a jittered honeycomb.
    min_x, min_y, max_x, max_y = land.bounds
    row_step = FACET_YD * math.sqrt(3) / 2
    row = 0
    y = min_y
    while y <= max_y:
        x = min_x + (FACET_YD / 2 if row % 2 else 0.0)
        while x <= max_x:
            px = x + rng.uniform(-0.24, 0.24) * FACET_YD
            py = y + rng.uniform(-0.24, 0.24) * FACET_YD
            if inner_ok.contains(Point(px, py)):
                vertex(px, py)
            x += FACET_YD
        y += row_step
        row += 1

    # Triangles that keep the outline: an edge of it that the triangulation
    # crosses gets a point in its middle, until none is crossed.
    for _ in range(40):
        mesh = Delaunay(np.array(vertices, float))
        edges = set()
        for a, b, c in mesh.simplices:
            edges.update(((a, b), (b, a), (b, c), (c, b), (a, c), (c, a)))
        split = False
        for ring in rings:
            at = 0
            while at < len(ring):
                a, b = ring[at], ring[(at + 1) % len(ring)]
                if (a, b) not in edges:
                    (ax, ay), (bx, by) = vertices[a], vertices[b]
                    middle = vertex((ax + bx) / 2, (ay + by) / 2)
                    if middle not in (a, b) and middle not in ring:
                        ring.insert(at + 1, middle)
                        split = True
                        at += 1
                at += 1
        if not split:
            break
    else:
        raise SystemExit("the outline could not be kept in the triangulation")
    on_coast = {at for ring in rings for at in ring}

    land_ok = prep(land.buffer(1.0))
    points = np.array(vertices, float)
    relief = _noise(seed)
    boundary = land.boundary
    heights = np.zeros(len(vertices))
    for at, (x, y) in enumerate(vertices):
        if at in on_coast:
            continue
        inland = min(1.0, Point(x, y).distance(boundary) / 2600.0)
        heights[at] = (0.5 + 0.5 * inland) * 260.0 * inland + 210.0 * inland * relief(x, y)

    light = np.array([0.52, 0.48, 0.70])  # from the north-west, and above
    light /= np.linalg.norm(light)
    faces = []
    area = 0.0
    raw = []
    for a, b, c in mesh.simplices:
        pa, pb, pc = points[a], points[b], points[c]
        twice = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        if abs(twice) < 1.0:
            continue
        centre = Point(*((pa + pb + pc) / 3))
        if not land_ok.contains(centre):
            continue
        area += abs(twice) / 2
        # The facet's slope, as a normal: x north, y west, z up.
        u = np.array([pb[0] - pa[0], pb[1] - pa[1], heights[b] - heights[a]])
        v = np.array([pc[0] - pa[0], pc[1] - pa[1], heights[c] - heights[a]])
        normal = np.cross(u, v)
        if normal[2] < 0:
            normal = -normal
        normal /= np.linalg.norm(normal)
        raw.append((int(a), int(b), int(c), float(normal @ light)))
    assert abs(area - land.area) < 0.004 * land.area, f"facets cover {area:.0f} of {land.area:.0f} square yards"
    lit = sorted(face[3] for face in raw)
    low, high = lit[len(lit) // 50], lit[-1 - len(lit) // 50]
    for a, b, c, value in raw:
        shade = int(round((SHADES - 1) * min(1.0, max(0.0, (value - low) / (high - low)))))
        faces.append((a, b, c, shade))
    return vertices, rings, faces, coast


# ---- the module -----------------------------------------------------------------------------------

HEADER = """\
// Low-poly maps of the continents, for the road on the Share picture.
//
// Made by tools/gen_lands.py - do not edit by hand. These are drawings of our
// own (no game art): each continent's outline, generalised to straight
// edges, and facets with a made-up relief. World yards, x to the north and
// y to the west - the coordinates the addon records the road in.
//
//   bounds: [min x, min y, max x, max y]
//   points: [x0, y0, x1, y1, ...]            the corners
//   coast:  [[corner, corner, ...], ...]     the outline: one ring per island
//   faces:  [corner, corner, corner, shade, ...]   triangles; shade 0 (dark) to 9 (lit)
//
// The object below is plain JSON (the tests read it as such).
"""


def build(spawns: Path, out: Path) -> None:
    import numpy as np

    by_continent: dict[int, list[tuple[float, float]]] = {key: [] for key in CONTINENTS}
    for line in open(spawns, encoding="ascii"):
        parts = line.strip().split(",")
        continent, x, y = int(parts[-3]), float(parts[-2]), float(parts[-1])
        if continent in by_continent:
            by_continent[continent].append((x, y))
    lands = {}
    for continent, (name, frame) in CONTINENTS.items():
        points = np.array(by_continent[continent])
        print(f"{name}:")
        parts, mask = outline(points, frame, BAYS[continent])
        vertices, rings, faces, coast = facets(parts, seed=1000 + continent)
        xs = [x for x, _y in vertices]
        ys = [y for _x, y in vertices]
        lands[str(continent)] = {
            "name": name,
            "bounds": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "points": [int(value) for vertex in vertices for value in vertex],
            "coast": rings,
            "faces": [value for face in faces for value in face],
        }
        held = sum(1 for x, y in points[:: max(1, len(points) // 4000)] if _inside(x, y, vertices, rings))
        print(
            f"    {len(parts)} piece(s), {sum(len(ring) for ring in rings)} corners on the coast,"
            f" {len(vertices)} corners, {len(faces)} facets; mask {mask.area / 1e6:.1f} -> outline"
            f" {sum(part.area for part in parts) / 1e6:.1f} million square yards;"
            f" {held} of {len(points[:: max(1, len(points) // 4000)])} sampled positions on the land"
        )
    body = json.dumps(lands, separators=(",", ":"))
    # One continent per line, and its lists on lines of their own: diffs stay readable.
    body = body.replace('},"', '},\n"').replace(',"points"', ',\n"points"').replace(',"coast"', ',\n"coast"')
    body = body.replace(',"faces"', ',\n"faces"')
    out.write_text(f"{HEADER}export const LANDS = {body};\n", encoding="utf-8", newline="\n")
    print(f"{out} written ({out.stat().st_size} bytes)")


def _inside(x: float, y: float, vertices, rings) -> bool:
    for ring in rings:
        inside = False
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            (ax, ay), (bx, by) = vertices[a], vertices[b]
            if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
                inside = not inside
        if inside:
            return True
    return False


def load(path: Path) -> dict:
    """The module's data, as the tests read it."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("export const LANDS = ") + len("export const LANDS = ") :].rstrip().rstrip(";"))


def preview(path: Path, out: Path) -> None:
    from PIL import Image, ImageDraw

    lands = load(path)
    scale = 0.035  # pixels a yard
    tiles = []
    for key in ("1", "0"):
        land = lands[key]
        min_x, min_y, max_x, max_y = land["bounds"]
        width, height = int((max_y - min_y) * scale) + 40, int((max_x - min_x) * scale) + 40
        image = Image.new("RGB", (width, height), (16, 22, 60))
        draw = ImageDraw.Draw(image)
        points = land["points"]

        def at(i, max_x=max_x, max_y=max_y, points=points):
            return (20 + (max_y - points[2 * i + 1]) * scale, 20 + (max_x - points[2 * i]) * scale)

        faces = land["faces"]
        for i in range(0, len(faces), 4):
            a, b, c, shade = faces[i : i + 4]
            tone = 70 + shade * 12
            draw.polygon([at(a), at(b), at(c)], fill=(tone - 20, tone, tone + 30))
        for ring in land["coast"]:
            draw.line([at(i) for i in ring + ring[:1]], fill=(240, 220, 150), width=2)
        tiles.append(image)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 20, max(t.height for t in tiles)), (16, 22, 60))
    left = 0
    for tile in tiles:
        sheet.paste(tile, (left, 0))
        left += tile.width + 20
    sheet.save(out)
    print(f"{out} written")


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "points":
        read_points(Path(argv[1]), Path(argv[2]))
    elif len(argv) >= 2 and argv[0] == "build":
        build(Path(argv[1]), Path(argv[2]) if len(argv) > 2 else LANDS_JS)
    elif len(argv) >= 2 and argv[0] == "preview":
        preview(Path(argv[1]) if len(argv) > 2 else LANDS_JS, Path(argv[-1]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
