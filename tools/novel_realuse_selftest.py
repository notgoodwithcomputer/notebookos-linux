#!/usr/bin/env python3
"""Novel driven the way a writer drives it, looking at what the screen says.

    tools/guestrun.sh python3 tools/novel_realuse_selftest.py

Every check here was RED on the tree it was written against, and each one names
what a writer actually saw: a chapter that exported as an empty page, a chapter
heading that scrolled out of sight the moment they clicked into the body, a
word count that counted the app's own bullet, a menu item promising a question
it never asked, a save chip describing a manuscript that had been replaced, and
an undo that quietly re-bound the book to an older file.

It hosts the real widget tree through tools/appdrive.py, so every handler,
store and menu is the app's own.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import appdrive                                            # noqa: E402
from gi.repository import Gtk                              # noqa: E402

ROOT = os.environ.get("NB_REALUSE_DIR") or tempfile.mkdtemp(
    prefix="nb-novel-realuse-")
os.makedirs(ROOT, exist_ok=True)
print("drive home + shots: %s\n" % ROOT)
FAILED = []


def check(cond, what):
    print("%-68s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def drive(name):
    """A fresh app on a private home, so no walk inherits another's store."""
    return appdrive.Drive("novel", home=os.path.join(ROOT, name))


def canvas_of(d):
    """The ScrolledWindow the writing surface sits in, found by walking the
    real tree rather than by asking the app for an attribute name."""
    for w in d.walk():
        if isinstance(w, Gtk.ScrolledWindow) and d.app.view in d.walk(w):
            return w
    raise LookupError("no canvas around the writing surface")


def visible_band(d):
    adj = canvas_of(d).get_vadjustment()
    return adj.get_value(), adj.get_value() + adj.get_page_size()


def y_in_canvas(d, widget):
    """`widget`'s top and bottom in the scrolled page's own coordinates."""
    here = widget.translate_coordinates(d.app.page, 0, 0)
    return here[1], here[1] + widget.get_allocated_height()


def caret_y(d):
    buf = d.app.view.get_buffer()
    rect = d.app.view.get_iter_location(buf.get_iter_at_mark(buf.get_insert()))
    _x, wy = d.app.view.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET, rect.x, rect.y)
    return d.app.view.translate_coordinates(d.app.page, 0, wy)[1], rect.height


def words_on_chip(d):
    m = re.search(r"[\d,]+", d.app.count_lbl.get_text())
    return int(m.group(0).replace(",", "")) if m else -1


def menu_label(d, menu, prefix):
    for item in d.menu(menu):
        if isinstance(item, (tuple, list)) and str(item[0]).startswith(prefix):
            return item[0]
    return ""


# ===================================================================== 1
# THE CHAPTER HEADING STAYS ON THE PAGE WHILE THE CHAPTER IS WRITTEN.
# Clicking into the body used to scroll the eyebrow and the chapter title off
# the top of the canvas before a single word was typed — the writer worked
# under a blank strip for the whole of writing — because the page-tall writing
# surface is a focus child of the canvas viewport, whose focus adjustment
# clamps a focused child's top to the top of the view.
d = drive("scroll")
adj = canvas_of(d).get_vadjustment()
check(adj.get_value() == 0, "a chapter opens at the top of its page")
d.app.view.grab_focus()
d.pump(0.3)
check(adj.get_value() == 0,
      "clicking into the body does not move the page")
top, bottom = visible_band(d)
etop, _eb = y_in_canvas(d, d.app.eyebrow)
_tt, tbot = y_in_canvas(d, d.app.chapter_title)
check(top <= etop and tbot <= bottom,
      "the chapter eyebrow and title are both still on screen")
d.shot(os.path.join(ROOT, "01-focused.png"))

# ...and the canvas DOES follow the caret, which is the only thing worth
# following. The writing surface is not the scroller's own child, so its
# scroll_to_mark moved an adjustment nothing was watching: past the first
# screenful a writer typed into a line they could not see.
buf = d.app.view.get_buffer()
for i in range(60):
    buf.insert_at_cursor("Line %d of the evening she kept writing.\n" % i)
d.pump(0.4)
# One more character, typed for real. The scroll is deferred so it measures the
# page AFTER the layout settles; this holder has no frame clock, so the layout
# pass only happens inside pump() (see tools/appdrive.py) and the settled
# measurement lands on the next keystroke rather than this one.
d.type("x")
d.pump(0.4)
cy, ch_ = caret_y(d)
top, bottom = visible_band(d)
check(top <= cy and cy + ch_ <= bottom,
      "the canvas follows the caret past the first screenful")
d.shot(os.path.join(ROOT, "02-caret-followed.png"))
# The title is the one other thing in the canvas that takes the keyboard, and
# it lives at the top of the page: reaching it has to bring the top back.
d.app.chapter_title.grab_focus()
d.pump(0.3)
check(canvas_of(d).get_vadjustment().get_value() == 0,
      "reaching the chapter title brings the top of the page back")
d.close()

# ===================================================================== 2
# THE WORD COUNT COUNTS THE WRITER'S WORDS, NOT THE EDITOR'S MARKS.
# Typing "--" becomes a real em dash and Insert > Bullet List writes a literal
# "• " into the buffer; both were then counted as words of the manuscript.
d = drive("count")
d.app.view.grab_focus()
d.type("one -- two")
d.pump(0.6)
body = d.app._buffer_text(d.app.chapters[0]["buffer"])
check("—" in body, "the typed dash really did become an em dash")
check(words_on_chip(d) == 2, "an em dash between two words is not a word")
d.menu_action("Insert", "Bullet List")
d.pump(0.6)
check(d.app._buffer_text(d.app.chapters[0]["buffer"]).startswith("•"),
      "the bullet really was written into the prose")
check(words_on_chip(d) == 2, "the bullet the app inserted is not a word")
d.shot(os.path.join(ROOT, "03-wordcount.png"))
d.close()

# ===================================================================== 3
# A CLEARED CHAPTER TITLE READS THE SAME EVERYWHERE, AT ONCE. The sidebar row
# went blank until something else rebuilt the list, and only then said
# "Chapter 2" — the name the entry, the eyebrow and the printed book use.
d = drive("title")
d.menu_action("File", "New Chapter")
d.pump(0.3)
d.app.chapter_title.grab_focus()
d.key("a", ctrl=True)
d.key("BackSpace")
d.pump(0.4)
row = d.app.chapters[1].get("_row_title")
check(row is not None and row.get_text() == "Chapter 2",
      "a cleared chapter title shows its fallback in the sidebar at once")
d.shot(os.path.join(ROOT, "04-cleared-title.png"))

# ===================================================================== 4
# FIND SAYS HOW FAR THE MATCHES REACH. The "n matches in m chapters" summary
# was written into the label and overwritten by "k of n" inside the same call,
# so nobody ever saw it.
d.app.view.grab_focus()
d.type("the lantern")
d.pump(0.4)
d.app._select_chapter(0)
d.app.view.grab_focus()
d.type("the lantern again")
d.pump(0.6)
d.key("f", ctrl=True)
d.app.find_entry.grab_focus()
d.type("lantern")
d.pump(0.6)
said = d.app.find_count.get_text()
check("1 of 2" in said, "find still counts the writer through the matches")
check("2 chapters" in said,
      "...and says how many chapters the matches are spread over: %r" % said)
d.shot(os.path.join(ROOT, "05-find.png"))
d.close()

# ===================================================================== 5
# THE MENU PROMISES WHAT THE ACTION DOES. Both items carried the ellipsis that
# means "this will ask you something" (docs/MENU-CONVENTIONS.md rule 1) while
# deleting at once — the confirmation was retired OS-wide in favour of undo.
d = drive("delete")
d.menu_action("File", "New Chapter")
d.pump(0.2)
d.menu_action("File", "New Part")
d.pump(0.3)
if getattr(d.app, "_prompt_layer", None) is not None:
    d.key("Return")
    d.pump(0.3)
chapter_item = menu_label(d, "File", "Delete Chapter")
part_item = menu_label(d, "File", "Delete Part")
check(chapter_item and "…" not in chapter_item,
      "Delete Chapter does not promise a question: %r" % chapter_item)
check(part_item and "…" not in part_item,
      "Delete Part does not promise a question: %r" % part_item)
before = len(d.app.chapters)
d.menu_action("File", "Delete Chapter")
d.pump(0.3)
check(getattr(d.app, "_prompt_layer", None) is None
      and len(d.app.chapters) == before - 1,
      "...and it deletes at once, as the label now says")
undo = [it for it in d.menu("Edit")
        if isinstance(it, (tuple, list)) and str(it[0]).startswith("Undo")]
check(bool(undo) and undo[0][1] is not None
      and "Delete Chapter" in undo[0][0],
      "Edit offers to undo it by name: %r" % (undo[0][0] if undo else None))
d.close()

# ===================================================================== 6
# EVERY PARAGRAPH REACHES THE EXPORTED BOOK. The renderer skipped line 0 of
# each chapter — a leftover from the format where the heading WAS that line —
# so the opening paragraph of every chapter was dropped, and a chapter of one
# paragraph exported as a heading over an empty page.
d = drive("export")
d.app.view.grab_focus()
d.type("Alpha opens the book.\nBeta follows it.")
d.pump(0.4)
d.menu_action("File", "New Chapter")
d.pump(0.2)
d.app.view.grab_focus()
d.type("Gamma is the whole of chapter two.")
# Long enough for the autosave debounce to land, so the chip below is reporting
# the export rather than a save that happened to fire on top of it.
d.pump(1.5)
paras = [[m for _s, m in d.app._chapter_paras(c)] for c in d.app.chapters]
check(any("Alpha" in m for m in paras[0]),
      "the first paragraph of a chapter is part of the book")
check(any("Gamma" in m for m in paras[1]),
      "a chapter whose body is one paragraph is not an empty chapter")
d.menu_action("File", "Export to PDF")
d.pump(0.2)
d.key("Return")
d.pump(2.0)
pdf = os.path.join(d.home, "Documents", "Untitled Novel.pdf")
check(os.path.exists(pdf), "the export writes a PDF")
try:
    text = subprocess.run(["pdftotext", "-layout", pdf, "-"],
                          capture_output=True, text=True, timeout=60).stdout
except Exception:                                          # noqa: BLE001
    text = None
if text is None:
    print("SKIP  pdftotext is not installed; the page model was checked above")
else:
    for word in ("Alpha", "Beta", "Gamma"):
        check(word in text, "“%s” is in the exported PDF" % word)

# ...and the chip goes on describing THIS manuscript. Exporting replaced the
# save state with a green "Exported", which then sat over a brand-new blank
# book after File > New.
check("Exported" in d.app.save_lbl.get_text(), "an export says it exported")
d.menu_action("File", "New")
d.pump(0.6)
chip = d.app.save_lbl.get_text()
check("Exported" not in chip and "Saved" in chip,
      "a new manuscript carries its own save state, not the last one's: %r"
      % chip)
d.shot(os.path.join(ROOT, "06-after-new.png"))
d.close()

# ===================================================================== 7
# UNDO MOVES THE WORDS; THE FILE BINDING MOVES ONLY WHEN THE MENU SAYS SO.
# Save As rode inside whatever typing step was open, so one Ctrl+Z — labelled
# "Undo Typing" — silently bound the book back to the file it had been saved
# as before, and the next Ctrl+S wrote over that older file while the one the
# writer had just named stayed stale.
d = drive("saveas")
import nbpicker                                            # noqa: E402
docs = os.path.join(d.home, "Documents")
os.makedirs(docs, exist_ok=True)
first, second = os.path.join(docs, "first.json"), os.path.join(docs, "copy.json")
d.app.view.grab_focus()
d.type("Opening line.")
d.pump(0.8)
nbpicker.save_file = lambda *a, **k: first
d.menu_action("File", "Save As")
d.pump(0.5)
d.app.view.grab_focus()
d.type(" Second line.")
d.pump(0.8)
nbpicker.save_file = lambda *a, **k: second
d.menu_action("File", "Save As")
d.pump(0.5)
check(d.app.doc_path == second, "Save As binds the manuscript to the new file")
d.app.view.grab_focus()
d.type(" Third line.")
d.pump(1.2)
d.key("z", ctrl=True)
d.pump(0.8)
check(d.app.doc_path == second,
      "undoing the typing leaves the book bound where the writer put it")
d.key("s", ctrl=True)
d.pump(0.6)
kept = open(first, encoding="utf-8").read()
check("Second line." not in kept and "Third line." not in kept,
      "...so Ctrl+S writes the file that was chosen, not the older one")
d.close()

# ===================================================================== 8
# A SESSION THAT WILL NEVER SAVE DOES NOT OPEN UNDER A GREEN "SAVED". The
# store had been kept aside because it could not be read; the chip still
# announced a save that had not happened and never would.
home = os.path.join(ROOT, "quarantined")
cfg = os.path.join(home, ".config", "notebook")
os.makedirs(cfg, exist_ok=True)
with open(os.path.join(cfg, "novel.json"), "w", encoding="utf-8") as fh:
    fh.write('{"chapters": [')
d = appdrive.Drive("novel", home=home)
d.pump(0.5)
check(d.app._store_read_only, "the unreadable store was kept aside")
chip = d.app.save_lbl.get_text()
check("Not saved" in chip,
      "a session that cannot write says so from the first frame: %r" % chip)
d.shot(os.path.join(ROOT, "07-quarantined.png"))
d.close()

print()
if FAILED:
    print("novel real-use selftest: %d FAILED" % len(FAILED))
    for f in FAILED:
        print("  - %s" % f)
    print("RESULT: FAIL")
    sys.exit(1)
print("novel real-use selftest: OK")
print("RESULT: PASS")
