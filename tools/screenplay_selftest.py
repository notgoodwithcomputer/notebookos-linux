#!/usr/bin/env python3
"""screenplay_selftest — the exported script is actually ON the page.

    DISPLAY=:0 FONTCONFIG_FILE=<abs>/tools/guest-fonts.conf \
        python3 tools/screenplay_selftest.py

THE BUG THIS EXISTS FOR. Compile to PDF drew the title, the page numbers and
EVERY LINE OF THE SCRIPT with cairo's toy text API bound to "monospace". That
API binds one FreeType face and does no per-character fallback, and the face it
resolves to carries no CJK, no Devanagari and no Hebrew. A screenplay written
in any of those exported a correct-looking title page followed by BLANK SHEETS:
.notdef in that face draws nothing at all, so there was no tofu, no warning and
no way to tell the export had dropped the work — the page numbers, being
digits, came out fine and made the file look healthy.

The format is a monospace grid and has to stay one, so the checks below measure
both things: that non-Latin scripts leave ink, and that the Latin grid is
unchanged.

Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME",
                      tempfile.mkdtemp(prefix="screenplay-selftest-"))
# ABSOLUTE, or fontconfig quietly answers about the HOST's fonts instead.
os.environ.setdefault("FONTCONFIG_FILE",
                      os.path.join(REPO, "tools", "guest-fonts.conf"))

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo                                                  # noqa: E402
import nbprint                                                # noqa: E402
import screenplay                                             # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def render(lines, title, page=2, scale=1):
    """Draw one exported page and return (surface, width, height)."""
    w = screenplay.Screenplay()
    w.body.get_buffer().set_text("\n".join(lines))
    w.scripttitle.set_text(title)
    _n, draw = w._build_pages()
    PW, PH = nbprint.HALF_W_PT, nbprint.HALF_H_PT
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                              int(PW * scale), int(PH * scale))
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    draw(cr, page, PW, PH)
    surf.flush()
    return surf, int(PW * scale), int(PH * scale)


def ink(surf, w, h, x0=0, y0=0, x1=None, y1=None):
    """Pixels darker than the paper inside a box."""
    x1 = w if x1 is None else x1
    y1 = h if y1 is None else y1
    data, stride = surf.get_data(), surf.get_stride()
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = y * stride + x * 4
            b, g, r = data[i], data[i + 1], data[i + 2]
            if r < 200 and g < 200 and b < 200:
                n += 1
    return n


SCRIPTS = {
    "latin": ["INT. KITCHEN - NIGHT",
              "A kettle screams. MARGA does not move to take it off.",
              "MARGA",
              "You said you would call."],
    "japanese": ["室内 - 台所 - 夜",
                 "やかんが鳴っている。マルガは動こうとしない。",
                 "マルガ",
                 "電話すると言ったでしょう。"],
    "chinese": ["内景 - 厨房 - 夜",
                "水壶尖叫着。玛尔加没有去关它。",
                "玛尔加",
                "你说过你会打电话的。"],
    "korean": ["실내 - 부엌 - 밤",
               "주전자가 비명을 지른다. 마르가는 움직이지 않는다.",
               "마르가",
               "전화한다고 했잖아요."],
    "hindi": ["आंतरिक - रसोई - रात",
              "केतली चीखती है। मार्गा हिलती नहीं।",
              "मार्गा",
              "तुमने कहा था कि फ़ोन करोगे।"],
    "yiddish": ["אינעווייניק - קיך - נאַכט",
                "אַ טשייַניק שרייַט. מאַרגאַ רירט זיך נישט.",
                "מאַרגאַ",
                "דו האָסט געזאָגט אַז דו וועסט אָנקלינגען."],
}

print("--- 1. every script leaves ink on the exported page ------------")
base = None
for name in ("latin", "japanese", "chinese", "korean", "hindi", "yiddish"):
    surf, w, h = render(SCRIPTS[name], "THE KETTLE")
    got = ink(surf, w, h)
    if name == "latin":
        base = got
    check("the body of a %s script is on the page" % name, got > 0, got)

print("--- 2. the non-Latin body is not just the page number ----------")
# The page number is digits and always rendered, even when the toy API dropped
# every word of the script — so measure BELOW the page-number line.
for name in ("japanese", "chinese", "korean", "hindi", "yiddish"):
    surf, w, h = render(SCRIPTS[name], "THE KETTLE")
    body = ink(surf, w, h, y0=int(screenplay.PDF_MT))
    check("a %s script has body text, not only a page number" % name,
          body > 0, body)

print("--- 3. the title page carries the title ------------------------")
for name, title in (("latin", "THE KETTLE"), ("japanese", "やかん"),
                    ("hindi", "केतली")):
    surf, w, h = render(SCRIPTS["latin"], title, page=1)
    check("the title page shows a %s title" % name, ink(surf, w, h) > 0)

print("--- 4. the Latin monospace grid is unchanged -------------------")
surf, w, h = render(SCRIPTS["latin"], "THE KETTLE")
# left margin is clear, and the text starts at PDF_ML
left_gutter = ink(surf, w, h, x0=0, x1=int(screenplay.PDF_ML) - 2)
check("nothing is drawn left of the text margin", left_gutter == 0, left_gutter)
right_gutter = ink(surf, w, h, x0=int(w - screenplay.PDF_MR) + 2)
check("nothing spills past the right margin", right_gutter == 0, right_gutter)
check("the page has a sensible amount of ink on it",
      100 < base < (w * h) // 4, base)

# a monospace face still advances every character by the same width
import cairo as _c                                            # noqa: E402
_s = _c.ImageSurface(_c.FORMAT_ARGB32, 10, 10)
_cr = _c.Context(_s)
w1 = screenplay._pdf_w(_cr, "M", screenplay.PDF_FS)
w2 = screenplay._pdf_w(_cr, "i", screenplay.PDF_FS)
w10 = screenplay._pdf_w(_cr, "M" * 10, screenplay.PDF_FS)
check("the export face is monospaced", w1 == w2, (w1, w2))
check("ten columns are ten times one column", abs(w10 - w1 * 10) <= 1,
      (w10, w1 * 10))

print("--- 5. the toy API is gone from the export path ----------------")
import re                                                     # noqa: E402
TOY = re.compile(r"\b(?:cr|ctx|c)\.(?:show_text|text_path|text_extents"
                 r"|select_font_face|set_font_size)\s*\(")
src = open(os.path.join(DE, "screenplay.py"), encoding="utf-8").read()
hits = [i + 1 for i, ln in enumerate(src.splitlines())
        if TOY.search(ln.split("#", 1)[0])]
check("screenplay.py calls no cairo toy text function", not hits, hits)

print("")
print("%d checks, %d passed, %d FAILED"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS)))
print("RESULT: %s" % ("ALL PASS" if not FAILS
                      else "FAILED: %s" % ", ".join(FAILS)))
sys.exit(len(FAILS))
