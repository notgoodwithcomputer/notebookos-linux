#!/usr/bin/env python3
"""Novel's printed page: pagination, the blank cover verso, and monochrome ink.

    DISPLAY=:0 python3 tools/novel_zine_selftest.py

The reported faults were text overflowing between pages and getting cut off, a
red bar under each heading, and the closing page landing on the back of the
cover. All three are checked here against the real page model and a real
imposed PDF.
"""
import os
import sys
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                              # noqa: E402
import cairo                                               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                 "notebookos", "rootfs-overlay", "opt",
                                 "notebook", "de")),
    "/opt/notebook/de",
]
DE = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
if DE not in sys.path:
    sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbhome-zine-selftest-")

import novel                                               # noqa: E402
import nbprint                                             # noqa: E402

FAILED = []
SENT = ("She kept the lantern trimmed against a wind that never quite "
        "arrived, and counted the nights by the oil she burned. ")


def check(cond, what):
    print("%-64s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def build():
    n = novel.Novel()
    n._title = "The Lantern Keeper"
    n.chapters = []
    for num, title, body in (
            (1, "The Harbour", SENT * 6 + "\n\n" + SENT * 7 + "\n\n" + SENT * 5),
            (2, "A Long Winter", SENT * 8 + "\n\n" + SENT * 6)):
        buf = Gtk.TextBuffer()
        for t in ("heading", "quote", "bold", "italic", "underline"):
            if not buf.get_tag_table().lookup(t):
                buf.create_tag(t)
        # BODY ONLY. In format 2 the chapter heading is a field of its own
        # (novel.Novel.chapter_title) and the buffer holds prose alone; seeding
        # it the way format 1 did — heading line, then the body — hid the fault
        # this suite now checks for, because the dropped line 0 happened to be
        # a duplicate of the heading. See _chapter_paras.
        buf.set_text(body)
        n.chapters.append({"num": num, "title": title, "part": 0,
                           "buffer": buf})
    return n


def paragraphs(n):
    """Fragments grouped per paragraph, in document order. A fragment whose
    source offset is 0 starts a new paragraph."""
    out, cur = [], None
    for pg in n._render_pages:
        for it in pg["items"]:
            if it[0] != "frag":
                continue
            _, markup, font, _x, _y, w, s, h = it
            if abs(s) < 0.01:
                cur = {"markup": markup, "font": font, "w": w, "frags": [(s, h)]}
                out.append(cur)
            elif cur is not None:
                cur["frags"].append((s, h))
    return out


def one_paragraph_book():
    """The shape that exported as a heading over an empty page: chapters whose
    whole body is a single paragraph, each carrying a sentinel word."""
    n = novel.Novel()
    n._title = "Two Short Chapters"
    n.chapters = []
    for num, title, mark in ((1, "Arrival", "SENTINELONE"),
                             (2, "Departure", "SENTINELTWO")):
        buf = Gtk.TextBuffer()
        for t in ("heading", "quote", "bold", "italic", "underline"):
            if not buf.get_tag_table().lookup(t):
                buf.create_tag(t)
        buf.set_text("%s and then the rest of the only paragraph." % mark)
        n.chapters.append({"num": num, "title": title, "part": 0,
                           "buffer": buf})
    return n


def main():
    n = build()

    # ---- 0. every paragraph the writer typed reaches the page -------------
    # The renderer skipped line 0 of each chapter buffer. That was right in
    # format 1, where the chapter heading WAS the buffer's first line, and
    # silently wrong in format 2, where the heading is a field of its own: the
    # opening paragraph of every chapter fell out of BOTH publish routes, and a
    # chapter with a single paragraph printed as a heading over an empty page.
    solo = one_paragraph_book()
    solo._prepare_render()
    solo_frags = [it[1] for pg in solo._render_pages for it in pg["items"]
                  if it[0] == "frag"]
    for mark in ("SENTINELONE", "SENTINELTWO"):
        check(any(mark in f for f in solo_frags),
              "a chapter whose body is one paragraph prints it (%s)" % mark)
    # Through the real commit path, which is what File > Author... calls.
    n._commit_author("Ada Marchetti")
    count = n._prepare_render()
    pages = n._render_pages

    first_lines = []
    for ch in n.chapters:
        buf = ch["buffer"]
        end = buf.get_iter_at_line(0)
        end.forward_to_line_end()
        first_lines.append(buf.get_text(buf.get_start_iter(), end, False))
    body_frags = [it[1] for pg in pages for it in pg["items"]
                  if it[0] == "frag"]
    check(all(any(line[:40] in f for f in body_frags) for line in first_lines),
          "the first paragraph of every chapter is on a page")

    # ---- 1. the fault that cut text in half ---------------------------------
    # Pagination stores a clip window per fragment and _draw_page re-lays the
    # paragraph out on the PDF surface and clips to it. Every window edge must
    # therefore land exactly on a LINE boundary as the PDF lays it out. It did
    # not: measuring happened on a RecordingSurface (metric hinting on, 17.00pt
    # a line) while drawing happens on a PDF surface (hinting off, 15.50pt), so
    # windows drifted until they sliced through the middle of a line — 20 bad
    # edges on this very book, the worst 28pt out, nearly two whole lines.
    draw = cairo.Context(cairo.PDFSurface(None, novel.PAGE_W, novel.PAGE_H))
    sliced, worst = 0, 0.0
    for p in paragraphs(n):
        rows = n._layout_lines(n._mk_layout(draw, p["markup"], p["font"],
                                            p["w"]))
        edges = [0.0] + [b for _t, b in rows]
        for s, h in p["frags"]:
            for v in (s, s + h):
                d = min(abs(v - e) for e in edges)
                worst = max(worst, d)
                if d > 0.6:
                    sliced += 1
    check(sliced == 0,
          "every page break falls on a line boundary (worst miss %.2fpt)"
          % worst)
    check(any(len(p["frags"]) > 1 for p in paragraphs(n)),
          "...and the book really does split a paragraph across a page")

    # measuring and drawing must agree, which is what guarantees the above
    m_rows = n._layout_lines(n._mk_layout(n._measure_ctx(), SENT * 14,
                                          novel.F_BODY, novel.COL_W))
    d_rows = n._layout_lines(n._mk_layout(draw, SENT * 14, novel.F_BODY,
                                          novel.COL_W))
    check(m_rows == d_rows,
          "the measuring surface reports the same metrics as the drawing one")

    # ---- 2. the cover's other side is blank ---------------------------------
    check(len(pages) >= 2 and not pages[1]["items"],
          "page 2, the back of the cover, carries nothing")
    check(len(pages) >= 2 and pages[1]["folio"] is False,
          "...and no page number either")
    check(bool(pages[0]["items"]),
          "page 1 is still the cover and is not blank")

    # The imposition must then put that blank behind the cover, and the LAST
    # page beside it as the back cover — never behind it.
    total4 = ((max(1, count) + 3) // 4) * 4
    sheets = list(nbprint._booklet_order(total4))
    front_l, front_r, back_l, back_r = sheets[0]
    check(front_r == 1, "the cover is on the front of the first sheet")
    check(back_l == 2,
          "the blank page is what lands on the back of the cover")
    check(front_l == total4,
          "the last page sits BESIDE the cover as the back cover, not behind it")
    check(back_l != total4,
          "the last page is not printed on the reverse of the first page")

    # ---- 3. nothing on paper costs colour ink -------------------------------
    NEUTRAL = {"#FFFFFF", "#000000", "#555555", "#777777"}
    colours = set()
    for pg in pages:
        for it in pg["items"]:
            if it[0] in ("text", "rule") and isinstance(it[-1], str) \
                    and it[-1].startswith("#"):
                colours.add(it[-1].upper())
    colours.add(novel.C_BG.upper())
    stray = sorted(c for c in colours if c not in NEUTRAL)
    check(not stray, "every colour on the page is neutral (found %s)"
          % (", ".join(stray) or "none"))
    for name in ("C_BG", "C_INK", "C_SEC", "C_MUT", "C_RULE"):
        v = getattr(novel, name).lstrip("#")
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        check(r == g == b, "%s (#%s) is a true grey, not a tint" % (name, v))
    check(novel.C_BG.upper() == "#FFFFFF",
          "the page ground is bare paper, not a full-bleed wash")

    # ---- 4. chapter numerals, the author, and the fold line ----------------
    for n_, want in ((1, "I"), (2, "II"), (4, "IV"), (9, "IX"), (14, "XIV"),
                     (40, "XL"), (1987, "MCMLXXXVII")):
        check(novel.roman(n_) == want, "%d is %s" % (n_, want))
    check(novel.roman(0) == "0" and novel.roman("x") == "",
          "a number a numeral cannot express falls back rather than raising")

    texts = [it[1] for pg in pages for it in pg["items"] if it[0] == "text"]
    check(not any(t.upper().startswith("CHAPTER ") for t in texts),
          "no page still says 'CHAPTER n' above a heading that repeats it")
    check("I" in texts and "II" in texts,
          "chapter openers carry their numeral")
    # the Contents lists the same numerals beside the titles
    toc = [pg for pg in pages
           if any(it[0] == "text" and it[1] == "Contents"
                  for it in pg["items"])]
    check(bool(toc), "there is a Contents page")
    if toc:
        rows = [it[1] for it in toc[0]["items"] if it[0] == "text"]
        check("I" in rows and "II" in rows,
              "the Contents carries the chapter numerals too")

    check("The Lantern Keeper" in texts, "the cover shows the title")
    check(n._author and n._author in texts,
          "the cover shows the author once one is set")
    n2 = build()
    n2._prepare_render()
    cover2 = [it[1] for it in n2._render_pages[0]["items"] if it[0] == "text"]
    check(all(t.strip() for t in cover2),
          "with no author set the cover prints no empty line in its place")

    # ---- 5. the imposed sheet is what a printer is being asked for ----------
    out = os.path.join(os.environ["NB_HOME"], "zine.pdf")
    sides = nbprint.booklet_pdf(out, count, n._draw_page)
    check(sides == total4 // 2, "one sheet side per two book pages")
    # The fold line is drawn on sheet 1's FRONT only — the outside of the
    # finished booklet, between back cover and front cover. A line on the inner
    # sheets would be buried in the fold and only costs ink.
    plain = os.path.join(os.environ["NB_HOME"], "nofold.pdf")
    nbprint.booklet_pdf(plain, count, n._draw_page, fold_line=False)
    withfold = os.path.join(os.environ["NB_HOME"], "fold.pdf")
    nbprint.booklet_pdf(withfold, count, n._draw_page, fold_line=True)
    check(os.path.getsize(withfold) > os.path.getsize(plain),
          "asking for a fold line puts something extra on the sheet")
    check(nbprint.FOLD_LINE_INK == (0.0, 0.0, 0.0),
          "the fold line is black, like everything else on the page")
    check(os.path.getsize(out) > 0, "the imposed PDF is written")
    check((nbprint.SHEET_W_PT, nbprint.SHEET_H_PT) == (792, 612),
          "the sheet is a letter page turned on its side")

    st = n._serialize()
    check(st.get("author") == n._author,
          "the author is stored with the project")

    print()
    if FAILED:
        print("novel zine selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("novel zine selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
