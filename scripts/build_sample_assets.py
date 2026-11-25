"""Generate the binary/static assets of the sample site (images and icon font).

The generated files are committed, so this script only needs to run again if
you want to change the artwork. Requires Pillow and fontTools (dev only).
"""

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image, ImageDraw

SITE = Path(__file__).resolve().parent.parent / "sample_site"

LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <circle cx="32" cy="32" r="30" fill="#f4efe6"/>
  <path d="M8 46 L24 22 L33 34 L41 24 L56 46 Z" fill="#1f4d3a"/>
</svg>
"""

HERO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 300">
  <rect width="480" height="300" fill="#f7d9a8"/>
  <circle cx="360" cy="90" r="42" fill="#f2a65a"/>
  <path d="M0 260 L120 110 L200 200 L290 80 L480 260 Z" fill="#6b8f71"/>
  <path d="M0 300 L0 250 L160 170 L300 240 L480 190 L480 300 Z" fill="#1f4d3a"/>
</svg>
"""

TOPO = """<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">
  <g fill="none" stroke="#c9d8cc" stroke-width="1">
    <ellipse cx="60" cy="60" rx="50" ry="34"/>
    <ellipse cx="60" cy="60" rx="34" ry="22"/>
    <ellipse cx="60" cy="60" rx="16" ry="10"/>
  </g>
</svg>
"""

GUIDE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
  <rect width="96" height="96" rx="48" fill="{bg}"/>
  <circle cx="48" cy="38" r="18" fill="#f4efe6"/>
  <path d="M18 86 C22 62 74 62 78 86 Z" fill="#f4efe6"/>
</svg>
"""


def write_svgs() -> None:
    images = SITE / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / "logo.svg").write_text(LOGO, encoding="utf-8")
    (images / "hero-ridge.svg").write_text(HERO, encoding="utf-8")
    (images / "topo-pattern.svg").write_text(TOPO, encoding="utf-8")
    for name, colour in (("maya", "#1f4d3a"), ("tomas", "#6b8f71"), ("ines", "#3f6f8f")):
        (images / f"guide-{name}.svg").write_text(GUIDE.format(bg=colour), encoding="utf-8")


def write_trail_map() -> None:
    img = Image.new("RGB", (640, 360), "#e8efe6")
    draw = ImageDraw.Draw(img)
    for i in range(6):
        draw.ellipse((60 + i * 30, 40 + i * 18, 580 - i * 30, 320 - i * 18), outline="#b5c8b8", width=2)
    draw.line([(80, 300), (200, 220), (320, 240), (430, 120), (560, 80)], fill="#c0392b", width=5)
    draw.line([(120, 80), (260, 150), (380, 300)], fill="#1f4d3a", width=5)
    img.save(SITE / "images" / "trail-map.png", optimize=True)


def _polygon_glyph(points):
    pen = TTGlyphPen(None)
    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()
    return pen.glyph()


def write_icon_font() -> None:
    """A tiny icon font: U+E001 mountain, U+E002 tent."""
    glyph_order = [".notdef", "mountain", "tent"]
    empty = TTGlyphPen(None).glyph()
    glyphs = {
        ".notdef": empty,
        "mountain": _polygon_glyph([(50, 0), (400, 700), (550, 450), (950, 0)]),
        "tent": _polygon_glyph([(80, 0), (500, 750), (920, 0)]),
    }
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({0xE001: "mountain", 0xE002: "tent"})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (1000, 0) for name in glyph_order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Ridgeline Icons", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fonts = SITE / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    fb.save(str(fonts / "ridgeline-icons.ttf"))


if __name__ == "__main__":
    write_svgs()
    write_trail_map()
    write_icon_font()
    print(f"Assets written to {SITE}")
