#!/usr/bin/env python3
"""Headless logic test for Writer: character/paragraph formatting, links,
images, tables, the serialise round-trip, and the checkpoint undo history.
Drives the REAL handlers and inspects the buffer tag table — no painting.

    DISPLAY=:0 PYTHONPATH=<de> NB_HOME=<scratch> python3 tools/writer_selftest.py

NOTE FOR THE NEXT PERSON: this file used to test `_on_fmt`, a `style` combo and
"heading"/"quote" tags. None of those are Writer's — they are Novel's API, and
the test had been failing on its second assertion for some time while still
looking like coverage. Writer names character tags bold/italic/underline/strike
and paragraph styles "style:<Name>" from writer.STYLES; it has no _on_fmt.
Check against the module before adding cases.
"""
import os
import sys
import tempfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, Pango, GdkPixbuf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
# `--de DIR` points the suite at a scratch copy, which is how a red-proof
# sabotages the app without touching the tree. Without it a mutation run
# silently measures the REAL writer.py and reports a clean pass (measured).
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
if DE not in sys.path:
    sys.path.insert(0, DE)

# NB_HOME MUST BE SET BEFORE `import writer`. writer.py reads it at module
# level (writer.HOME / writer.CFG_DIR), and so does nbapp's single-instance
# scope, so setting it afterwards -- as this file used to -- silently ran the
# whole suite against the CALLER'S REAL HOME and put the instance markers in
# the unscoped /tmp/nb-apps. A per-process directory also stops two copies of
# this suite from colliding there: the loser is os._exit(0)ed by
# claim_single_instance() with no output and exit status 0, which reads as a
# pass while nothing was tested.
os.environ.setdefault(
    "NB_HOME", tempfile.mkdtemp(prefix="nbhome-writer-selftest-"))
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import writer  # noqa: E402

w = writer.Writer()
buf = w.buf
ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


def has(name, a, b):
    return w._range_has_tag(buf.get_iter_at_offset(a),
                            buf.get_iter_at_offset(b), name)


def effective(offset):
    """The (points, bold) a reader would actually SEE at offset.

    Checking that a tag is present is not the same as checking it has any
    effect: GtkTextBuffer resolves two tags that set the same property by
    priority, which is creation order, so a tag can be applied and lose. This
    suite asserted tag presence for a long time while Heading 1 was visibly
    doing nothing to any text whose size had been set from the toolbar.
    get_tags() returns tags in increasing priority, so the last setter wins."""
    it = buf.get_iter_at_offset(offset)
    pts, bold = float(writer.DEFAULT_SIZE), False
    for t in it.get_tags():
        if t.get_property("size-set"):
            pts = t.get_property("size-points")
        if t.get_property("weight-set"):
            bold = t.get_property("weight") >= Pango.Weight.BOLD
    return (round(pts, 1), bold)


def text():
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def select(a, b):
    buf.select_range(buf.get_iter_at_offset(a), buf.get_iter_at_offset(b))


# ---- the tags Writer actually defines ---------------------------------------
tt = buf.get_tag_table()
for t in ("bold", "italic", "underline", "strike"):
    check("character tag '%s' defined" % t, tt.lookup(t) is not None)
for s in writer.STYLES:
    check("paragraph style 'style:%s' defined" % s,
          tt.lookup("style:" + s) is not None)
for j in ("left", "center", "right", "fill"):
    check("alignment tag 'align:%s' defined" % j,
          tt.lookup("align:" + j) is not None)

# ---- character formatting ---------------------------------------------------
buf.set_text("the brown fox")
select(4, 9)                       # "brown"
w._toggle_char("bold")
check("bold applied to selection", has("bold", 4, 9))
check("bold not applied outside", not has("bold", 0, 3))
select(4, 9)
w._toggle_char("bold")
check("bold toggled off", not has("bold", 4, 9))

select(10, 13)                     # "fox"
w._toggle_char("italic")
select(10, 13)
w._toggle_char("underline")
select(10, 13)
w._toggle_char("strike")
check("italic applied", has("italic", 10, 13))
check("underline applied", has("underline", 10, 13))
check("strikethrough applied", has("strike", 10, 13))

# With no selection Writer QUEUES the style for the next typed run, the way a
# word processor does. Put the caret where the run will go FIRST: moving the
# insert mark cancels a queued style (_on_mark_set), which is the correct
# behaviour and is what the caret has to be settled before arming.
buf.place_cursor(buf.get_end_iter())
w._pending.clear()
w._toggle_char("bold")
check("bold with no selection queues for the next run", "bold" in w._pending)
before = buf.get_char_count()
buf.insert_at_cursor("!")
check("queued bold lands on the typed run",
      has("bold", before, buf.get_char_count()))
buf.place_cursor(buf.get_start_iter())     # clears _pending, as a click would
check("moving the caret cancels the queued style", not w._pending)

# ---- paragraph styles / alignment / indent / lists --------------------------
buf.set_text("Title line\nbody\n")
buf.place_cursor(buf.get_iter_at_offset(3))
w._set_style("Heading 1")
check("Heading 1 applied to the paragraph", has("style:Heading 1", 0, 10))
w._set_style("Body")
check("Body replaces the previous style", not has("style:Heading 1", 0, 10))
check("...and applies its own", has("style:Body", 0, 10))
w._set_style("Quote")
check("Quote applied", has("style:Quote", 0, 10))
check("styles are mutually exclusive", not has("style:Body", 0, 10))

w._set_align("center")
check("centre alignment applied", has("align:center", 0, 10))
w._set_align("right")
check("alignment is mutually exclusive",
      has("align:right", 0, 10) and not has("align:center", 0, 10))

w._indent(1)
check("indent level 1", has("indent:1", 0, 10))
w._indent(1)
check("indent steps to level 2",
      has("indent:2", 0, 10) and not has("indent:1", 0, 10))
w._indent(-2)
check("indent returns to none", not has("indent:1", 0, 10)
      and not has("indent:2", 0, 10))

w._toggle_list("bullet")
check("bullet list applied", has("list:bullet", 0, 10))
w._toggle_list("bullet")
check("bullet list toggles off", not has("list:bullet", 0, 10))

# ---- styles must CHANGE THE TEXT, not merely be applied ----------------------
# The reported bug: pick Heading 1 and nothing happens. It happened to any text
# whose size had ever been set from the toolbar, and it stuck across save and
# reopen, because a lazily created "size:N" tag outranks every paragraph style.
buf.set_text("Chapter One\nbody\n")
buf.place_cursor(buf.get_iter_at_offset(3))
w._set_style("Heading 1")
check("Heading 1 actually makes the text heading-sized",
      effective(3) == (float(writer.STYLES["Heading 1"][0]), True))
w._set_style("Body")
check("Body actually returns the text to body size",
      effective(3) == (float(writer.DEFAULT_SIZE), False))

select(0, 11)
w._apply_value_tag("size:", "size:12")
buf.place_cursor(buf.get_iter_at_offset(3))
w._set_style("Heading 1")
check("Heading 1 still takes after the size dropdown has been used",
      effective(3) == (float(writer.STYLES["Heading 1"][0]), True))
w._set_style("Title")
check("Title takes over from Heading 1",
      effective(3) == (float(writer.STYLES["Title"][0]), True))

# ...and a size chosen AFTER a style must still win, or the size dropdown would
# be the thing that stopped working.
select(0, 11)
w._apply_value_tag("size:", "size:9")
check("a size chosen after a style still wins", effective(3) == (9.0, True))

# ---- superscript / subscript -------------------------------------------------
buf.set_text("H2O and x2\n")
select(1, 2)
w._toggle_char("sub")
check("subscript applied to a selection", has("sub", 1, 2))
select(1, 2)
w._toggle_char("super")
check("superscript replaces subscript rather than stacking with it",
      has("super", 1, 2) and not has("sub", 1, 2))
select(1, 2)
w._toggle_char("super")
check("superscript toggles off", not has("super", 1, 2))

# It has to reach the PRINTED page too, not just the screen: a marker that is
# raised on screen and full-size on the baseline in the PDF is the same bug
# class as the styles that were applied but had no effect.
select(1, 2)
w._toggle_char("super")
_al = w._line_attrs(buf.get_iter_at_offset(0), buf.get_iter_at_offset(10))


def _pango_attrs(al):
    got = []
    it = al.get_iterator()
    while True:
        for a in it.get_attrs():
            got.append(a.klass.type)
        if not it.next():
            break
    return got


check("the PDF renderer raises a superscript run",
      Pango.AttrType.RISE in _pango_attrs(_al))
check("the PDF renderer also shrinks it",
      Pango.AttrType.SIZE in _pango_attrs(_al))
check("both scripts are in the saved tag list",
      "super" in w._serial_tag_names() and "sub" in w._serial_tag_names())
_doc = w._serialize()
buf.set_text("")
w._deserialize(_doc)
check("superscript survives a save and reopen", has("super", 1, 2))

# ---- page breaks ---------------------------------------------------------
buf.set_text("one\ntwo\nthree\n")
buf.place_cursor(buf.get_iter_at_offset(5))       # inside "two"
w._toggle_page_break()
check("a page break is set on the paragraph the caret is in",
      w._line_break(buf.get_iter_at_offset(5)))
check("...and not on its neighbours",
      not w._line_break(buf.get_iter_at_offset(1))
      and not w._line_break(buf.get_iter_at_offset(10)))
_flags = [b for (_t_, _a, _j, _i, _s, _lk, _li, _o, b) in w._para_iter()]
check("the paragraph iterator reports exactly one break", _flags.count(True) == 1)

# It has to reach the paper: a break that only draws a red line on screen and
# prints nothing is worse than no break at all.
import tempfile as _tf


def _render_pages(text_setup):
    """Render to a throwaway PDF and return how many sheets came out."""
    fd, path = _tf.mkstemp(suffix=".pdf")
    os.close(fd)
    text_setup()
    n = w._render_pdf(path)
    ok_size = os.path.getsize(path) > 0
    os.unlink(path)
    return n, ok_size


_n_with, _ok = _render_pages(lambda: None)
check("a document with a page break renders", _ok)
check("the break puts the rest of the document on a second sheet",
      _n_with == 2)

# ...and the same three lines with no break stay on one sheet, so the count
# above is the break's doing and not the paper simply running out.
_saved = w._serialize()
buf.set_text("one\ntwo\nthree\n")
_n_without, _ = _render_pages(lambda: None)
check("the same text with no break stays on one sheet", _n_without == 1)
w._deserialize(_saved)

_doc = w._serialize()
buf.set_text("")
w._deserialize(_doc)
check("the page break survives a save and reopen",
      w._line_break(buf.get_iter_at_offset(5)))
buf.place_cursor(buf.get_iter_at_offset(5))
w._toggle_page_break()
check("toggling again takes the break away",
      not w._line_break(buf.get_iter_at_offset(5)))

# ---- zoom, tab stops, and pagination by LINE --------------------------------
check("a fresh document is at actual size", w._zoom == writer.DEFAULT_ZOOM)
w._zoom_step(1)
check("Zoom In magnifies", w._zoom > writer.DEFAULT_ZOOM)
check("...and the paper grows with the text",
      w._px_per_in() == writer.PX_PER_IN * w._zoom)
w._set_zoom(99.0)
check("zoom cannot exceed the largest step", w._zoom == writer.ZOOM_STEPS[-1])
w._set_zoom(0.01)
check("...nor go below the smallest", w._zoom == writer.ZOOM_STEPS[0])
w._set_zoom(1.0)

check("a fresh document has no tab stops of its own", w._tabs() == [])
w._page["tabs"] = [1.0, 2.5, 2.5]
check("stops are sorted and de-duplicated", w._tabs() == [1.0, 2.5])
w._apply_tabs()
check("the view is given the stops", w.body.get_tabs() is not None)
w._clear_tabs()
check("Clear Tab Stops empties them", w._tabs() == [])

# The renderer used to draw a paragraph as ONE block and move it whole to the
# next sheet, so a paragraph taller than a page ran off the bottom of the paper
# and every line past the edge was lost.
_long = ("Lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
         "eiusmod tempor incididunt ut labore. ") * 90
buf.set_text(_long)
_fd, _p = _tf.mkstemp(suffix=".pdf")
os.close(_fd)
_pages = w._render_pdf(_p)
os.unlink(_p)
check("one paragraph longer than a page is split across pages, not clipped "
      "(%d pages)" % _pages, _pages >= 3)
buf.set_text("short\n")
_fd, _p = _tf.mkstemp(suffix=".pdf")
os.close(_fd)
check("...while a short document is still one page", w._render_pdf(_p) == 1)
os.unlink(_p)
check("widow and orphan minimums are set",
      writer.ORPHAN_MIN >= 2 and writer.WIDOW_MIN >= 2)

# ---- links: a real tagged span, with the href in the tag name ---------------
URL = "https://example.org/a"
buf.set_text("")
buf.insert_at_cursor("Notebook")
select(0, 8)
w._link_dialog = lambda sel: ("Notebook", URL)      # stand in for the modal
w._insert_link()
check("link text inserted verbatim", text() == "Notebook")
check("link is NOT raw markdown", "[" not in text() and "](" not in text())
ltag = tt.lookup(writer.LINK_PREFIX + URL)
check("per-URL link tag created", ltag is not None)
check("link tag is underlined", ltag is not None and int(
    ltag.get_property("underline")) == int(Pango.Underline.SINGLE))
check("link span covers the text", has(writer.LINK_PREFIX + URL, 0, 8))
# a blank URL must insert nothing and must not raise (the real dialog strips
# its fields, so whitespace reaches _insert_link as the empty string)
buf.set_text("x")
w._link_dialog = lambda sel: ("ignored", "")
w._insert_link()
check("blank-URL link is a no-op", text() == "x")

# ---- serialise / deserialise round-trip -------------------------------------
buf.set_text("Chapter One\nSome body text.")
select(0, 11)
w._toggle_char("bold")
buf.place_cursor(buf.get_iter_at_offset(3))
w._set_style("Heading 2")
doc = w._serialize()
check("serialised body carries the text", doc["body"].startswith("Chapter One"))
check("serialised runs carry the bold span",
      any(r[2] == "bold" for r in doc["runs"]))
check("serialised runs carry the paragraph style",
      any(r[2] == "style:Heading 2" for r in doc["runs"]))
buf.set_text("")
w._deserialize(doc)
check("round-trip restores the text", text().startswith("Chapter One"))
check("round-trip restores bold", has("bold", 0, 11))
check("round-trip restores the paragraph style", has("style:Heading 2", 0, 11))

# ---- images: the BYTES travel with the document, not a path to them ----------
# ROADMAP #1. This used to hand-build _img_meta with just {"path", "ow"} and
# then assert a path was collected — so it never touched the embedding at all,
# and round-tripped through the load-from-path fallback instead. It would have
# gone on passing with the fix removed. The picker is patched out (a dialog is
# not reachable from here); everything after it is the real code path.
img_path = os.path.join(tempfile.gettempdir(), "writer_selftest_img.png")
have_img = True
try:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 10)
    pb.fill(0x3366ffff)
    pb.savev(img_path, "png", [], [])
except Exception as e:
    have_img = False
    check("test image written (%s)" % e, False)

def first_pixbuf():
    probe = buf.get_start_iter()
    while True:
        pb = probe.get_pixbuf()
        if pb is not None:
            return pb
        if not probe.forward_char():
            return None


def is_the_test_image():
    """The SAME picture, not merely a picture.

    When neither the bytes nor the original file can be found, writer draws a
    visible placeholder — correct behaviour, and a pixbuf. So "a pixbuf is
    present" passes with the embedding removed (measured: it did). 16x10 of
    one known colour is unmistakable.
    """
    pb = first_pixbuf()
    if pb is None:
        return False, "no pixbuf at all"
    if (pb.get_width(), pb.get_height()) != (16, 10):
        return False, "%dx%d — the placeholder, not the picture" % (
            pb.get_width(), pb.get_height())
    px = pb.get_pixels()[:3]
    if tuple(px) != (0x33, 0x66, 0xff):
        return False, "wrong colour %r" % (tuple(px),)
    return True, "16x10 #3366ff"


if have_img:
    buf.set_text("A B")
    buf.place_cursor(buf.get_iter_at_offset(1))
    _real_open = writer.nbpicker.open_file
    writer.nbpicker.open_file = lambda *a, **k: img_path
    try:
        w._insert_image()
    finally:
        writer.nbpicker.open_file = _real_open
    doc = w._serialize()
    check("image placeholder present in the serialised body",
          writer.OBJ in doc["body"])
    recs = doc["images"]
    check("image path collected for persistence",
          any(i.get("path") == img_path for i in recs))
    embedded = any(i.get("data") for i in recs)
    check("the picture itself is embedded, not just its path (%s)"
          % ", ".join(sorted({k for i in recs for k in i})), embedded)

    # The whole point: the source goes away. A USB stick is pulled, a file is
    # tidied up — and the document has to be complete on its own.
    os.remove(img_path)
    buf.set_text("")
    w._img_meta.clear()
    w._deserialize(doc)
    same, why = is_the_test_image()
    check("the picture survives the original file being deleted (%s)" % why,
          same)

    # And it must still be there after an autosave/undo cycle, which is where
    # a path reference used to lose it a second time.
    doc2 = w._serialize()
    buf.set_text("")
    w._img_meta.clear()
    w._deserialize(doc2)
    same2, why2 = is_the_test_image()
    check("...and through a second round-trip with the source gone (%s)" % why2,
          same2)

# ---- tables ------------------------------------------------------------------
buf.set_text("")
w._insert_table([["a", "b"], ["c", "d"]])
doc = w._serialize()
check("table serialised with its cells",
      bool(doc["tables"]) and doc["tables"][0]["data"][0][0] == "a")
buf.set_text("")
w._tables.clear()
w._deserialize(doc)
check("table restored after a round-trip", len(w._tables) == 1)

# ---- checkpoint undo history (the machinery nbapp.UndoHistory generalises) ---
buf.set_text("")
w._history = []
w._hi = -1
w._push_history()
buf.insert_at_cursor("The road bent north.")
w._checkpoint()
kept = text()
buf.delete(buf.get_start_iter(), buf.get_end_iter())   # select-all + delete
w._checkpoint()
check("wipe emptied the document", text() == "")
w._undo()
check("undo brings the wiped document back", text() == kept)
w._redo()
check("redo wipes it again", text() == "")
w._undo()
check("undo again", text() == kept)
check("history is capped at 100 checkpoints", len(w._history) <= 100)

# ---- an undo leaves the document UNSAVED ------------------------------------
# Undo rebuilds the buffer through _deserialize, which suppresses the buffer's
# "changed" handler on purpose — so undo and redo used to be the only edits in
# Writer that never reached _mark_dirty. The document on screen no longer
# matched the file on disk while the chip still read "● Saved 14:32", no
# autosave was armed for it, and File > New / File > Open discarded the undone
# work without asking, because _confirm_discard consults _file_dirty alone.
# (History here is ["", kept, ""] with _hi at `kept`, so both moves are live.)
def _clean():
    """Pretend the document was just written to its file."""
    w._file_dirty = False
    if w._save_timer:
        GLib.source_remove(w._save_timer)
        w._save_timer = None


_clean()
w._redo()
check("redo marks the document unsaved", w._file_dirty is True)
check("redo arms the autosave", w._save_timer is not None)
_clean()
w._undo()
check("undo marks the document unsaved", w._file_dirty is True)
check("undo arms the autosave", w._save_timer is not None)
check("undo still restored the text", text() == kept)
# ...and a redo with nothing to redo must not claim an edit that never happened
while w._hi < len(w._history) - 1:
    w._redo()
_clean()
w._redo()
check("a redo at the end of the history changes nothing and stays saved",
      w._file_dirty is False and w._save_timer is None)

# ---- the printed page's header and footer are the AUTHOR'S OWN text --------
# They were drawn with cairo's toy API bound to Liberation Sans, which carries
# no CJK, no Devanagari and no Hebrew: a header typed in any of those printed
# as .notdef, which in that face is INVISIBLE — not a box — while the rest of
# the page came out correctly. Measure ink, because that is the only thing that
# distinguishes the two cases.
import cairo  # noqa: E402


def _furniture_ink(header, size=9):
    """Non-blank pixels _pdf_show leaves for `header`."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 420, 60)
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0, 0, 0)
    w._pdf_show(cr, 6, 40, header, size)
    surf.flush()
    data, stride = surf.get_data(), surf.get_stride()
    n = 0
    for y in range(60):
        for x in range(420):
            i = y * stride + x * 4
            if bytes(data[i:i + 3]) != b"\xff\xff\xff":
                n += 1
    return n


for _name, _sample in (("latin", "Chapter One"),
                       ("japanese", "第一章"),
                       ("chinese", "第一章"),
                       ("korean", "제1장"),
                       ("hindi", "अध्याय एक"),
                       ("yiddish", "קאַפּיטל איין"),
                       ("russian", "Глава первая"),
                       ("greek", "Κεφάλαιο")):
    check("a header in %s prints as real glyphs" % _name,
          _furniture_ink(_sample) > 0)

# the size must not have shifted when this moved off the toy API: PDF units are
# points, so Pango's resolution has to be pinned to 72dpi or every header would
# come out a third larger than the author set
_small = _furniture_ink("Chapter One", 9)
_large = _furniture_ink("Chapter One", 18)
check("header size still scales with the size asked for", _large > _small * 1.5)

# and the page label on the canvas draws through Pango too
_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 40)
_cr = cairo.Context(_surf)
_cr.set_source_rgb(1, 1, 1)
_cr.paint()
_cr.set_source_rgb(0, 0, 0)
w._page_label(_cr, 6, 28, "ページ 2")
_surf.flush()
_d, _s = _surf.get_data(), _surf.get_stride()
_ink = sum(1 for y in range(40) for x in range(200)
           if bytes(_d[y * _s + x * 4:y * _s + x * 4 + 3]) != b"\xff\xff\xff")
check("the page-break label draws a non-Latin page number", _ink > 0)

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
