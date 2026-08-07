#!/usr/bin/env python3
"""Generate the GBA SDK runtime's built-in 8x8 text font as 4bpp GBA tile data.

Rasterises ASCII 32..126 from DejaVu Sans Mono into 8x8 1-bit cells, packs each
as a 4bpp GBA tile (pixel index 1 = ink, 0 = transparent), and prints a C array
`nb_font[]` (char c uses tile index c-32) for pasting into runtime.c. Also writes
a scaled preview PNG. Run once at authoring time; the output is committed.

    tools/gen_gba_font.py [preview.png]
"""
import sys
from PIL import Image, ImageFont, ImageDraw

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
# PROVEN VIABLE, NOT YET SWITCHED: DejaVuSansCondensed passes the audit with one
# extra override (a hand-drawn `j`, which the rasteriser otherwise draws as a
# bare stem identical to both `i` and `|`), and makes a typical line 34%
# narrower -- a 26-cell dialogue box would hold about 40 characters.
#
# It is not switched on because the runtime still draws text one glyph per 8x8
# CELL. Proportional glyphs in fixed cells look worse than monospaced ones: an
# `i` three pixels wide leaves five pixels of gap. The swap belongs with the
# variable-width renderer, not before it.
#
#   FONT_PROPORTIONAL = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"
#   plus OVERRIDES["j"] = ("..#", "...", "..#", "..#", "..#", "..#", "..#", "##.")
FIRST, LAST = 32, 126

# Rasterising into an 8x8 cell is destructive, and two characters that come out
# as the same 64 pixels are a real bug in a game that shows a score or a line of
# dialogue. The baseline sits one row higher than the obvious choice so that p,
# y, g, q, j and the comma have a row left for their descender — without it
# `o` and `p`, `v` and `y`, `,` and `.`, and `:` and `;` were byte-identical.
# The proportional face, used only by the variable-width renderer. Condensed
# rather than plain DejaVu Sans: the plain face collides on seven character
# pairs at 8x8, the condensed one on three, and all three are `j` -- which the
# rasteriser draws as a bare stem indistinguishable from `i` and `|`.
FONT_PROP = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"
PROP_OVERRIDES = {
    "j": ("..#",            # a hook below the baseline, and a stem one column
          "...",            # right of i's, so neither i nor | can be confused
          "..#",            # with it
          "..#",
          "..#",
          "..#",
          "..#",
          "##."),
}

BASELINE_DY = -2
THRESHOLD = 90

# The characters the rasteriser still cannot separate at this size, drawn by
# hand. `0` and `8` matter most: a digit that reads as D or B ruins every score
# read-out. Rows are top to bottom, '#' = ink; row 7 is below the baseline.
OVERRIDES = {
    "0": ("....",           # a slashed zero, so it can never read as D or O
          ".###.",
          "#...#",
          "#..##",
          "#.#.#",
          "##..#",
          "#...#",
          ".###."),
    "8": (".###.",          # two bowls, every corner round: not a B
          "#...#",
          "#...#",
          ".###.",
          "#...#",
          "#...#",
          ".###."),
    "B": ("####.",          # one straight stem, both bowls to its right
          ".#..#",
          ".#..#",
          ".###.",
          ".#..#",
          ".#..#",
          "####."),
    "Q": (".###.",          # an O with a tail that leaves the ring
          "##..#",
          "##..#",
          "##..#",
          "##..#",
          "##.##",
          ".###.",
          "...###"),
    "_": ("", "", "", "", "", "", "", "######"),
    ".": ("", "", "", "", "", ".##", ".##"),
    ",": ("", "", "", "", "", ".##", ".##", "##"),
    ":": ("", ".##", ".##", "", "", ".##", ".##"),
    ";": ("", ".##", ".##", "", "", ".##", ".##", "##"),
    "'": (".##", ".##"),
    "`": ("##", ".#"),
    # curved brackets against square ones, and the lowercase l against both
    "(": ("..#.", ".#..", "#...", "#...", "#...", ".#..", "..#."),
    ")": (".#..", "..#.", "...#", "...#", "...#", "..#.", ".#.."),
    "[": (".##.", ".#..", ".#..", ".#..", ".#..", ".#..", ".##."),
    "]": ("..##", "...#", "...#", "...#", "...#", "...#", "..##"),
    "l": (".##.", "..#.", "..#.", "..#.", "..#.", "..#.", "..##"),
    # g curls its descender left, q drops a straight stem: the only thing that
    # separates them at this size
    "g": ("", "", ".####", "#...#", "#...#", ".####", "....#", "####."),
    "q": ("", "", ".####", "#...#", "#...#", ".####", "....#", "....#"),
}


def _pack(art):
    """Hand-drawn rows ('#' = ink) -> the 8 row bitmasks of an 8x8 cell."""
    rows = []
    for y in range(8):
        line = art[y] if y < len(art) else ""
        rows.append(sum(1 << x for x, c in enumerate(line[:8]) if c == "#"))
    return rows


def glyph_rows(font, ch):
    if ch in OVERRIDES:
        return _pack(OVERRIDES[ch])
    img = Image.new("L", (8, 8), 0)
    ImageDraw.Draw(img).text((0, BASELINE_DY), ch, fill=255, font=font)
    px = img.load()
    return [sum((1 << x) for x in range(8) if px[x, y] > THRESHOLD)
            for y in range(8)]


def glyph_width(rows):
    """How many columns a glyph actually occupies, plus one of spacing.

    Measured from the INK, not from the font's own advance: these are
    hand-rasterised into an 8x8 cell and several are overridden by hand, so the
    source font's metrics describe a different picture than the one that ends
    up on the cartridge.

    A blank glyph -- the space, and nothing else, because audit() rejects any
    other character that renders as nothing -- is given a fixed width rather
    than zero, which would run every word together."""
    used = 0
    for r in rows:
        for x in range(8):
            if r & (1 << x):
                used = max(used, x + 1)
    if used == 0:
        return 3                      # the space
    return min(8, used + 1)           # one column of side bearing


def audit(rows_by_ch):
    """Every pair of visible characters that renders within one pixel of
    another, plus anything that renders as nothing. Both are bugs, so the
    generator refuses to emit a font that has any."""
    import itertools
    vis = {c: r for c, r in rows_by_ch.items()
           if c != ord(" ") and any(r)}
    problems = ["%r draws nothing" % chr(c) for c, r in rows_by_ch.items()
                if c != ord(" ") and not any(r)]
    for a, b in itertools.combinations(sorted(vis), 2):
        d = sum(bin(x ^ y).count("1") for x, y in zip(vis[a], vis[b]))
        if d <= 1:
            problems.append("%r and %r differ by only %d pixel(s)"
                            % (chr(a), chr(b), d))
    return problems


def rasterise(path, extra_overrides=None):
    """(tile stream, rows by char) for one source face, audited."""
    global OVERRIDES
    saved = OVERRIDES
    if extra_overrides:
        OVERRIDES = dict(OVERRIDES)
        OVERRIDES.update(extra_overrides)
    try:
        font = ImageFont.truetype(path, 9)
        tiles = []
        rows_by_ch = {}
        for code in range(FIRST, LAST + 1):
            rows = glyph_rows(font, chr(code))
            rows_by_ch[code] = rows
            for y in range(8):
                for half in range(2):
                    v = 0
                    for k in range(4):
                        if rows[y] & (1 << (half * 4 + k)):
                            v |= 1 << (k * 4)      # pixel index 1 = ink
                    tiles.append(v)
        problems = audit(rows_by_ch)
    finally:
        OVERRIDES = saved
    return tiles, rows_by_ch, problems


def main():
    tiles, rows_by_ch, problems = rasterise(FONT)
    if problems:
        sys.stderr.write("this font is not legible; fix OVERRIDES:\n  %s\n"
                         % "\n  ".join(problems))
        return 1
    # C array
    print("/* built-in 8x8 text font (ASCII %d..%d), 4bpp; char c -> tile c-%d.\n"
          "   Generated by tools/gen_gba_font.py. */" % (FIRST, LAST, FIRST))
    print("#define NB_FONT_FIRST %d" % FIRST)
    print("#define NB_FONT_COUNT %d" % (LAST - FIRST + 1))
    body = ", ".join("0x%04X" % v for v in tiles)
    print("static const u16 nb_font[] = { %s };" % body)
    # Per-glyph widths, for measuring text and for proportional drawing. The
    # tile data stays 8x8 -- this says how much of each tile is used, which is
    # what a variable-width renderer advances by.
    widths = [glyph_width(rows_by_ch[c]) for c in range(FIRST, LAST + 1)]
    print("static const u8 nb_font_w[] = { %s };"
          % ", ".join(str(w) for w in widths))

    # A SECOND, PROPORTIONAL FACE: MEASURED AND REJECTED.
    #
    # DejaVuSansCondensed passes the audit with one extra override, and the
    # obvious assumption is that it makes text much narrower. Measured against
    # the monospaced face RENDERED PROPORTIONALLY -- which is what the runtime
    # now does -- it does not:
    #
    #   fixed cells        -> proportional rendering:  33% narrower
    #   monospaced face    -> condensed face:           6% narrower
    #
    # and on all-capitals text the condensed face is WIDER. Nearly all of the
    # gain belongs to the renderer, not the face. A second face costs 3,040
    # bytes of every cartridge, and 6% of a dialogue box is not worth that.
    #
    # rasterise() still takes a path and extra overrides, so this is one call
    # away if a future face is a bigger jump. It is left unmade rather than
    # made and half-justified.

    # preview
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/font_preview.png"
    sample = "HELLO WORLD! Hero HP:20/100 Lv.3 <Press A>"
    sc = 6
    im = Image.new("RGB", (len(sample) * 8 * sc, 8 * sc), (16, 20, 30))
    pp = im.load()
    for ci, ch in enumerate(sample):
        c = ord(ch)
        if not (FIRST <= c <= LAST):
            continue
        rows = rows_by_ch[c]
        for y in range(8):
            for x in range(8):
                if rows[y] & (1 << x):
                    for sy in range(sc):
                        for sx in range(sc):
                            pp[(ci * 8 + x) * sc + sx, y * sc + sy] = (240, 240, 210)
    im.save(out)
    sys.stderr.write("preview -> %s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
