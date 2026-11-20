"""Draw Walka's icon for the packaged program: companion/icons/walka.ico
(Windows) and companion/icons/walka.icns (Mac).

    python -m pip install pillow
    python tools/gen_icons.py

The picture is the logo, docs/images/logo.svg, drawn again here from the
same numbers - a blue tile, a white zigzag (two peaks: a W, and a road over
hills) and the belt under it - so that nothing has to read an SVG. Change
one, change the other.

The build (companion/build_exe.py) takes the two files as they are in the
repository: nothing is drawn while a release is built, and Pillow is not
among the packages a build installs. Run this by hand when the logo changes,
look at the result, and commit it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "companion" / "icons"

# The logo, in the units of its 32 x 32 viewBox.
VIEW = 32.0
BLUE = (0x25, 0x6A, 0xBF)
WHITE = (255, 255, 255)
TILE_RADIUS = 8.0
ZIGZAG = ((8.2, 15.6), (12.4, 8.0), (16.0, 13.6), (19.6, 9.0), (23.8, 15.6))
ZIGZAG_WIDTH = 2.5
BELT = (6.0, 20.0, 26.0, 24.6)  # left, top, right, bottom
BELT_RADIUS = 2.3
BELT_OPACITY = 0.55

DRAWN_AT = 4096  # drawn large and scaled down: that is what smooths the edges
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
# macOS draws no tile behind an icon: the picture brings its own, with some
# air around it (Apple's template: 824 of 1024 points).
MAC_TILE = 824 / 1024


def draw(size: int, tile: float = 1.0) -> Image.Image:
    """The logo as a ``size`` x ``size`` picture, its tile filling ``tile``
    of the width (the rest is transparent)."""
    big = Image.new("RGBA", (DRAWN_AT, DRAWN_AT), (0, 0, 0, 0))
    pen = ImageDraw.Draw(big)
    scale = DRAWN_AT * tile / VIEW
    offset = DRAWN_AT * (1 - tile) / 2

    def at(value: float) -> float:
        return offset + value * scale

    pen.rounded_rectangle((at(0), at(0), at(VIEW), at(VIEW)), radius=TILE_RADIUS * scale, fill=(*BLUE, 255))
    # The belt: white at 55 % over the blue, mixed here (the tile is opaque).
    belt = tuple(round(b + (w - b) * BELT_OPACITY) for b, w in zip(BLUE, WHITE, strict=True))
    pen.rounded_rectangle(tuple(at(v) for v in BELT), radius=BELT_RADIUS * scale, fill=(*belt, 255))
    # The zigzag, with round ends and corners: a line, and a dot on every point.
    points = [(at(x), at(y)) for x, y in ZIGZAG]
    width = ZIGZAG_WIDTH * scale
    pen.line(points, fill=(*WHITE, 255), width=round(width))
    for x, y in points:
        pen.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=(*WHITE, 255))
    return big.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Every size is drawn for itself, not scaled from the largest by the file format.
    sizes = [draw(size) for size in ICO_SIZES]
    sizes[-1].save(OUT / "walka.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=sizes[:-1])
    draw(1024, MAC_TILE).save(OUT / "walka.icns", format="ICNS")
    for name in ("walka.ico", "walka.icns"):
        print(f"wrote {OUT / name} ({(OUT / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
