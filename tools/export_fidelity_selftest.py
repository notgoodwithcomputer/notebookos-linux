#!/usr/bin/env python3
"""
Formatting reaches paper.

Two ROADMAP entries said it did not: #35 (Journal's bold/italic/quote "saved,
restored on screen, and dropped from every export and print") and #36 (the
Academics highlighter — "the thing a student uses before an exam" — invisible in
export). Both are fixed; this is what establishes it and keeps it true.

HOW YOU TEST A LOOK, NOT A STRING
`pdftotext` cannot see any of this: bold text and plain text extract
identically, and a highlight is a rectangle behind the glyphs that carries no
text at all. So each property is checked with the tool that can actually see it:

  bold / italic   render the SAME entry twice, once with the tags and once
                  without, and require the rasterised pages to DIFFER.
                  `pdffonts` was the first attempt and was vacuous: the year
                  heading is emitted bold whatever the body does, so
                  DejaVuSerif-Bold was in the file even with every span
                  discarded. Presence of a face says nothing about whether a
                  RUN used it.
  highlight       rasterise with `pdftoppm` and COUNT PIXELS of the
                  highlighter's own colour. Nothing else can distinguish a
                  yellow band from its absence.

Run:
    tools/guestrun.sh python3 tools/export_fidelity_selftest.py
    tools/guestrun.sh python3 tools/export_fidelity_selftest.py --de DIR
"""
import os
import sys
import shutil
import tempfile
import subprocess

_HOME = tempfile.mkdtemp(prefix="nb-fidelity-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GdkPixbuf  # noqa: E402

import nbprint  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(400):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def faces(pdf):
    """The font faces cairo embedded, e.g. {'DejaVuSerif', 'DejaVuSerif-Bold'}.
    The six-character subset prefix is stripped."""
    out = subprocess.run(["pdffonts", pdf], capture_output=True, text=True)
    got = set()
    for line in out.stdout.splitlines()[2:]:
        name = line.split()[0] if line.split() else ""
        if "+" in name:
            name = name.split("+", 1)[1]
        if name:
            got.add(name)
    return got


def raster(pdf):
    """The rendered pages as raw bytes, for comparing two documents."""
    stem = os.path.join(_HOME, "cmp")
    subprocess.run(["pdftoppm", "-r", "72", "-png", pdf, stem], check=True)
    pages = sorted(f for f in os.listdir(_HOME)
                   if f.startswith("cmp-") and f.endswith(".png"))
    blob = b""
    for p in pages:
        fp = os.path.join(_HOME, p)
        with open(fp, "rb") as fh:
            blob += fh.read()
        os.remove(fp)
    return blob


def colour_pixels(pdf, rgb, tol=12):
    """How many pixels of the rendered page are (about) `rgb`."""
    stem = os.path.join(_HOME, "raster")
    subprocess.run(["pdftoppm", "-r", "72", "-png", pdf, stem], check=True)
    pages = sorted(f for f in os.listdir(_HOME)
                   if f.startswith("raster-") and f.endswith(".png"))
    total = 0
    for p in pages:
        pb = GdkPixbuf.Pixbuf.new_from_file(os.path.join(_HOME, p))
        d, n, rs = pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()
        for y in range(pb.get_height()):
            row = y * rs
            for x in range(pb.get_width()):
                o = row + x * n
                if (abs(d[o] - rgb[0]) < tol and abs(d[o + 1] - rgb[1]) < tol
                        and abs(d[o + 2] - rgb[2]) < tol):
                    total += 1
        os.remove(os.path.join(_HOME, p))
    return total


# ---------------------------------------------------------------- journal
def test_journal():
    print("\n-- journal: bold, italic and quote (ROADMAP #35)")
    import journal
    app = journal.Journal()
    pump()
    # NO quote tag in this fixture, deliberately. A quote drives emit()'s
    # `italic=` and `indent=` arguments rather than a span, so an entry
    # containing one renders differently even with every SPAN discarded — and
    # the differential check below then passes while bold and italic are dead.
    # That is what the first version did. Spans are isolated here; the quote is
    # checked separately underneath.
    title = "Rain all day"
    body = "This word is bold and this one is italic."
    text = title + "\n" + body
    b0, i0 = text.index("bold"), text.index("italic")
    app.entries = [{"date": "Thursday, 6 August", "title": title, "text": text,
                    "meta": "", "day": "6", "wd": "Th",
                    "month_label": "August 2026", "preview": "",
                    "tags": [{"start": b0, "end": b0 + 4, "tag": "bold"},
                             {"start": i0, "end": i0 + 6, "tag": "italic"}]}]
    pdf = os.path.join(_HOME, "journal.pdf")
    app._render_pdf(pdf)
    made = check("the journal exports a PDF",
                 os.path.exists(pdf) and os.path.getsize(pdf) > 2048)
    if not made:
        not_reached("no PDF", "a bold run embeds a bold face",
                    "an italic run embeds an italic face")
        return
    # The SAME entry with the tags stripped. If the two documents rasterise
    # identically, the formatting had no effect on the page — which is exactly
    # what #35 described.
    plain = os.path.join(_HOME, "journal_plain.pdf")
    app.entries[0]["tags"] = []
    app._render_pdf(plain)
    with_tags, without = raster(pdf), raster(plain)
    check("both documents render", bool(with_tags) and bool(without))
    check("a bold/italic run changes the page",
          with_tags != without,
          "%d vs %d bytes of raster" % (len(with_tags), len(without)))

    # The quote, on its own: it is indented and italicised through emit()'s own
    # arguments, which is a different path from a span and needs its own check.
    qtext = title + "\nA whole quoted line."
    q0 = qtext.index("A whole quoted line.")
    app.entries[0]["text"] = qtext
    app.entries[0]["tags"] = [{"start": q0, "end": q0 + 20, "tag": "quote"}]
    quoted_pdf = os.path.join(_HOME, "journal_quote.pdf")
    app._render_pdf(quoted_pdf)
    app.entries[0]["tags"] = []
    flat_pdf = os.path.join(_HOME, "journal_flat.pdf")
    app._render_pdf(flat_pdf)
    check("a quoted line changes the page too",
          raster(quoted_pdf) != raster(flat_pdf))
    # And the faces are still worth reporting, as a weaker corroborating signal.
    f = faces(pdf)
    check("a bold and an italic face are embedded (%s)" % ", ".join(sorted(f)),
          any("Bold" in x for x in f) and any("Italic" in x for x in f))
    try:
        app.destroy()
    except Exception:
        pass


# -------------------------------------------------------------- academics
def test_academics():
    print("\n-- academics: the highlighter (ROADMAP #36)")
    import academics
    app = academics.Academics()
    pump()
    app._new_class()
    pump()
    app._new_lecture()
    pump()
    if not app.lectures:
        not_reached("no lecture could be created",
                    "the highlighter reaches the page",
                    "a bold run embeds a bold face")
        return

    # Typed into the REAL buffer and tagged through it. An earlier draft
    # assigned a synthetic lecture dict instead and measured ZERO highlighter
    # pixels — because _make_active_pdf calls _capture_active() first, which
    # reads the live buffer and overwrote it. That looked exactly like the
    # defect under test, and was the test's own doing.
    buf = app.body.get_buffer()
    buf.set_text("Mitochondria are the powerhouse of the cell.\n"
                 "This part is highlighted.")
    pump()
    buf.apply_tag_by_name("highlight", buf.get_iter_at_offset(44),
                          buf.get_iter_at_offset(69))
    buf.apply_tag_by_name("bold", buf.get_iter_at_offset(0),
                          buf.get_iter_at_offset(12))
    pump()

    pdf = os.path.join(_HOME, "academics.pdf")
    app._make_active_pdf(pdf)
    pump()
    ranges = app.lectures[app.active].get("ranges") or {}
    captured = check("the marks are captured off the buffer (%s)"
                     % sorted(ranges),
                     bool(ranges.get("highlight")) and bool(ranges.get("bold")))
    made = check("academics exports a PDF",
                 os.path.exists(pdf) and os.path.getsize(pdf) > 2048)
    if not (captured and made):
        not_reached("nothing to inspect", "the highlighter reaches the page",
                    "the note's bold survives too")
        return

    hits = colour_pixels(pdf, nbprint._HIGHLIGHT_RGB8
                         if hasattr(nbprint, "_HIGHLIGHT_RGB8")
                         else (0xFB, 0xE7, 0xA0))
    check("the highlighter reaches the page", hits > 200,
          "%d pixels of #FBE7A0" % hits)
    f = faces(pdf)
    check("the note's bold survives too (%s)" % ", ".join(sorted(f)),
          any("Bold" in x for x in f))
    try:
        app.destroy()
    except Exception:
        pass


# ----------------------------------------------------------------- writer
def test_writer():
    print("\n-- writer: a table across a page boundary (ROADMAP #14)")
    import writer
    app = writer.Writer()
    pump()
    # Enough prose to reach the foot of a page, then a 12-row table — the exact
    # shape the entry describes ("a 12-row table loses rows 8-12 entirely").
    # Objects used to be drawn at the cursor and only THEN checked against the
    # page bottom, so whatever crossed the edge was cut off by the paper and
    # appeared nowhere.
    rows = [["ROW%02d-A" % i, "ROW%02d-B" % i] for i in range(1, 13)]
    app.buf.set_text("\n".join("Filler line %d." % i for i in range(1, 42)))
    pump()
    app.buf.place_cursor(app.buf.get_end_iter())
    app._insert_table(rows)
    pump()

    pdf = os.path.join(_HOME, "writer_table.pdf")
    app._render_pdf(pdf)
    made = check("writer exports a PDF",
                 os.path.exists(pdf) and os.path.getsize(pdf) > 2048)
    if not made:
        not_reached("no PDF", "every row of the table reaches the paper",
                    "the table really did cross a page boundary")
        return
    txt = subprocess.run(["pdftotext", pdf, "-"],
                         capture_output=True, text=True).stdout
    found = [i for i in range(1, 13) if ("ROW%02d-A" % i) in txt]
    check("every row of the table reaches the paper",
          len(found) == 12, "missing %s" % [i for i in range(1, 13)
                                            if i not in found])
    # The control: if it all fitted on one page the check above proves nothing
    # about page breaking, which is the whole subject.
    check("the table really did cross a page boundary",
          txt.count("\f") >= 1, "%d page break(s)" % txt.count("\f"))
    try:
        app.destroy()
    except Exception:
        pass


def main():
    test_journal()
    test_academics()
    test_writer()
    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
