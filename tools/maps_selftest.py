#!/usr/bin/env python3
"""maps_selftest — the Maps canvas draws text that is actually THERE.

    DISPLAY=:0 FONTCONFIG_FILE=<abs>/tools/guest-fonts.conf \
        python3 tools/maps_selftest.py

THE BUG THIS EXISTS FOR. Everything Maps puts on its canvas — the "No maps"
notice, the sentence under it, the scale bar's distance, every place name — was
drawn with cairo's TOY text API (select_font_face + show_text/text_path). That
API binds ONE FreeType face and does no per-character fallback, and the face it
resolved ("sans-serif" -> Nimbus Sans) carries no CJK, no Devanagari and no
Hebrew. Those strings came out as .notdef for five of the seventeen shipped
languages, and .notdef in that face is INVISIBLE rather than a box: a reader in
Japanese who opened Maps with no pack installed got an empty window with
nothing on it to say why, and no way to find out.

tofu_sweep.py cannot catch this — it asks whether SOME shipped face has the
glyph, which was true the whole time and has nothing to do with which single
face show_text bound. So this file measures the only thing that matters: INK on
the surface.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="maps-selftest-"))
# An ABSOLUTE path, or fontconfig silently falls back to the HOST's fonts and
# every answer below is about the wrong font tree.
os.environ.setdefault("FONTCONFIG_FILE",
                      os.path.join(REPO, "tools", "guest-fonts.conf"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo                                                  # noqa: E402
import maps                                                   # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def ink(draw, w=460, h=90):
    """Non-blank pixels left on a fresh surface by `draw(cr)`."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0, 0, 0)
    draw(cr)
    surf.flush()
    data = surf.get_data()
    stride = surf.get_stride()
    n = 0
    for y in range(h):
        for x in range(w):
            i = y * stride + x * 4
            if bytes(data[i:i + 3]) != b"\xff\xff\xff":
                n += 1
    return n


# The strings Maps actually shows, in the scripts the toy API could not draw.
SAMPLES = [
    ("english", "No maps"),
    ("japanese", "地図がありません"),
    ("chinese", "没有地图"),
    ("korean", "지도 없음"),
    ("hindi", "कोई नक्शा "
              "नहीं"),
    ("yiddish", "קיין מאפעס"),
    ("russian", "Нет карт"),
    ("greek", "Κανένας χάρτης"),
]

print("--- 1. the toy API is the bug, and it is still the bug ----------")
# Not a style preference: prove the old call path really does draw nothing, so
# that if anyone reintroduces it the reason is on the record and measurable.
toy_blank = []
for name, text in SAMPLES:
    def toy(cr, t=text):
        cr.select_font_face("sans-serif", 0, 0)
        cr.set_font_size(19)
        cr.move_to(10, 50)
        cr.show_text(t)
    if ink(toy) == 0:
        toy_blank.append(name)
check("cairo's toy API draws nothing for the non-Latin scripts",
      set(toy_blank) >= {"japanese", "chinese", "korean", "hindi", "yiddish"},
      "drew nothing for: %s" % (sorted(toy_blank) or "nothing"))

print("--- 2. what Maps draws now lands as real ink -------------------")
for name, text in SAMPLES:
    got = ink(lambda cr, t=text: maps._show_text(cr, 10, 50, t, 19))
    check("_show_text puts ink on the surface: %s" % name, got > 0, got)

# _text_w needs a context, so measure it the way the app does
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
mcr = cairo.Context(surf)
for name, text in SAMPLES:
    check("_text_w measures a real width: %s" % name,
          maps._text_w(mcr, text, 19) > 0, maps._text_w(mcr, text, 19))

print("--- 3. the empty-state notice, end to end ----------------------")


class _Stub(object):
    """Just enough of the app to call the real _draw_empty."""
    _draw_empty = maps.Maps._draw_empty


for name, text in SAMPLES:
    s = _Stub()
    s._empty = (text, text + " " + text + " " + text)
    got = ink(lambda cr, st=s: st._draw_empty(cr, 460, 90), 460, 90)
    check("the empty notice is visible: %s" % name, got > 0, got)

# A long sentence with no spaces in it at all (Chinese and Japanese are written
# that way) must still be wrapped, not run off both edges.
s = _Stub()
long_cjk = "地図" * 60
s._empty = ("地図", long_cjk)
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 460, 200)
cr = cairo.Context(surf)
cr.set_source_rgb(1, 1, 1)
cr.paint()
s._draw_empty(cr, 460, 200)
surf.flush()
data, stride = surf.get_data(), surf.get_stride()
edge = 0
for y in range(200):
    for x in (0, 1, 2, 457, 458, 459):
        i = y * stride + x * 4
        if bytes(data[i:i + 3]) != b"\xff\xff\xff":
            edge += 1
check("a spaceless sentence wraps instead of running off the edges",
      edge == 0, "%d pixels of ink in the outer 3 columns" % edge)

print("--- 4. the toy API is gone from the source ---------------------")
import re                                                     # noqa: E402
TOY = re.compile(r"\b(?:cr|ctx|c)\.(?:show_text|text_path|text_extents"
                 r"|select_font_face|set_font_size)\s*\(")
src = open(os.path.join(DE, "maps.py"), encoding="utf-8").read().splitlines()
hits = [i + 1 for i, ln in enumerate(src)
        if TOY.search(ln.split("#", 1)[0])]
check("maps.py calls no cairo toy text function", not hits, hits)

print("")
print("%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
print("RESULT: %s" % ("ALL PASS" if not FAILS
                      else "FAILED: %s" % ", ".join(FAILS)))
sys.exit(len(FAILS))
