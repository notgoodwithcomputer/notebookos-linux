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
from gi.repository import Gtk, Pango, GdkPixbuf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if DE not in sys.path:
    sys.path.insert(0, DE)

import writer  # noqa: E402

os.environ.setdefault("NB_HOME", "/tmp/nbhome-writer-selftest")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

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

# ---- images: a real embedded pixbuf that persists as a path reference --------
img_path = os.path.join(tempfile.gettempdir(), "writer_selftest_img.png")
have_img = True
try:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 10)
    pb.fill(0x3366ffff)
    pb.savev(img_path, "png", [], [])
except Exception as e:
    have_img = False
    check("test image written (%s)" % e, False)

if have_img:
    buf.set_text("A B")
    buf.place_cursor(buf.get_iter_at_offset(1))
    it = buf.get_iter_at_mark(buf.get_insert())
    buf.insert_pixbuf(it, pb)
    w._img_meta[pb] = {"path": img_path, "ow": 16}
    doc = w._serialize()
    check("image placeholder present in the serialised body",
          writer.OBJ in doc["body"])
    check("image path collected for persistence",
          any(i.get("path") == img_path for i in doc["images"]))
    buf.set_text("")
    w._img_meta.clear()
    w._deserialize(doc)
    found = False
    probe = buf.get_start_iter()
    while True:
        if probe.get_pixbuf() is not None:
            found = True
            break
        if not probe.forward_char():
            break
    check("image re-embedded after a save/reopen round-trip", found)

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

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
