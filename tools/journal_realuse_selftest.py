#!/usr/bin/env python3
"""Journal, driven the way a person writes in it: real page, real scroll.

The defects this pins are all about what is ON THE SCREEN after an ordinary
sequence, and none of them can be seen from a stand-in widget:

  * the writing column was fitted with set_size_request from a size-allocate
    handler, which is a one-way ratchet — hide the entries list and show it
    again (or drag the window wide and back) and the column stayed at the wide
    measure, pushing the save chip off a 1024px panel for good;
  * the canvas kept its scroll offset across a buffer swap, so an entry opened
    from the bottom of a long one came up with its date and first paragraphs
    above the top edge;
  * the canvas viewport's FOCUS adjustment scrolled the page to the TextView
    whenever focus arrived from the + button or the search field, which shoved
    the big date off the top of a page nobody had scrolled;
  * Export to PDF printed the sidebar's 60-character title cut as the heading
    and then started the body after the first line, so everything past the
    60th character of line one was in no PDF and on no printout;
  * the word count counted the "• " its own Bullet control inserts, and read a
    Japanese sentence as one word;
  * File ▸ New Entry promised a card with an ellipsis it never opened;
  * the green chip said "Saved" after a row switch and "Saved 14:32" after
    typing, so its wording depended on the route the save took.

    tools/guestrun.sh python3 tools/journal_realuse_selftest.py
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = tempfile.mkdtemp(prefix="nb-journal-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = ROOT

import appdrive  # noqa: E402
from gi.repository import Gtk  # noqa: E402

PANEL_W = 1024
FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name
          + ((": " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def fresh():
    """A journal nobody has written in yet."""
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    return appdrive.Drive("journal")


def canvas(app):
    """The writing canvas' ScrolledWindow: page -> viewport -> scroller. Found
    by walking up from the page so this suite still measures the real thing if
    the app stops keeping a handle to it."""
    return app.page.get_parent().get_parent()


def rows(d):
    """The entries list rows, in list order (each is a real Gtk.Button)."""
    return [w for w in d.walk()
            if isinstance(w, Gtk.Button)
            and w.get_style_context().has_class("entryrowhit")]


def right_edge(d, widget):
    """A widget's right edge in panel coordinates."""
    pos = widget.translate_coordinates(d.child, 0, 0)
    if pos is None:
        return None
    return pos[0] + widget.get_allocation().width


def long_entry_text(marker="pretzel"):
    return ("First entry line with %s here.\n\n" % marker) + "\n\n".join(
        "Paragraph %d: lorem ipsum dolor sit amet, consectetur adipiscing "
        "elit, sed do eiusmod tempor." % i for i in range(1, 25)
    ) + "\n\nLASTWORD"


def two_entries(d):
    """A long entry and, on top of it, a short one — the shape of a journal
    somebody has been keeping."""
    app = d.app
    d.menu_action("File", "New Entry")
    d.pump(0.2)
    app.body.get_buffer().set_text(long_entry_text())
    d.pump(1.3)
    d.menu_action("File", "New Entry")
    d.pump(0.2)
    app.body.get_buffer().set_text("Second short entry.\n\nSecond body.")
    d.pump(1.3)
    return app


def word_number(app):
    """The number in the word-count label ('7 words' -> 7)."""
    m = re.search(r"[\d,]+", app.count.get_text() or "")
    return int(m.group(0).replace(",", "")) if m else None


# ---------------------------------------------------------------- the page --
def page_geometry():
    d = fresh()
    try:
        app = two_entries(d)
        width0 = app.page.get_allocation().width
        chip0 = right_edge(d, app.save)
        d.menu_action("View", "Show / Hide")      # entries list away
        d.pump(0.3)
        d.menu_action("View", "Show / Hide")      # and back
        d.pump(0.3)
        width1 = app.page.get_allocation().width
        chip1 = right_edge(d, app.save)
        check("the writing column returns to its measure when the entries "
              "list comes back",
              width1 == width0,
              "page was %s wide, is %s after hide/show" % (width0, width1))
        check("...and the save chip is still on the panel",
              chip1 is not None and chip1 <= PANEL_W,
              "chip right edge %s on a %s panel" % (chip1, PANEL_W))

        d.resize(1366, 740)
        d.pump(0.3)
        d.resize(PANEL_W, 740)
        d.pump(0.3)
        width2 = app.page.get_allocation().width
        chip2 = right_edge(d, app.save)
        minimum = d.child.get_preferred_width()[0]
        check("a window dragged wider and back leaves the column at the "
              "panel measure",
              width2 == width0,
              "page was %s wide, is %s after 1366 and back" % (width0, width2))
        check("...and the app still fits the 1024px panel",
              chip2 is not None and chip2 <= PANEL_W and minimum <= PANEL_W,
              "chip right edge %s, window minimum %s" % (chip2, minimum))
    finally:
        d.close()


# ------------------------------------------------------------- the scroll --
def opens_at_the_top():
    d = fresh()
    try:
        app = two_entries(d)
        sc = canvas(app)
        app.select_entry(1)                     # the long one
        d.pump(0.3)
        va = sc.get_vadjustment()
        va.set_value(va.get_upper() - va.get_page_size())   # read to the end
        d.pump(0.2)
        deep = va.get_value()
        row = rows(d)[0]                        # the other entry's row
        row.clicked()
        d.pump(0.3)
        check("a long entry can be scrolled to its end",
              deep > 0, "vadjustment stayed at %s" % deep)
        check("an entry opened from the bottom of another one opens at its top",
              va.get_value() == 0 and app.active == 0,
              "vadjustment %s, active %s" % (va.get_value(), app.active))
        check("...with its date on screen",
              app.date_lbl.get_allocation().y >= va.get_value(),
              "date at y=%s, page scrolled to %s"
              % (app.date_lbl.get_allocation().y, va.get_value()))

        app.select_entry(1)
        d.pump(0.3)
        va.set_value(va.get_upper() - va.get_page_size())
        d.pump(0.2)
        d.menu_action("File", "New Entry")
        d.pump(0.3)
        check("a new entry started from the bottom of a long one opens at "
              "its top",
              va.get_value() == 0, "vadjustment %s" % va.get_value())
    finally:
        d.close()


def focus_does_not_scroll():
    d = fresh()
    try:
        app = two_entries(d)
        sc = canvas(app)
        va = sc.get_vadjustment()
        app.select_entry(1)                     # the long entry, at its top
        d.pump(0.3)
        # The entry has to be taller than the canvas or nothing here could
        # scroll and every check below would pass on an empty promise.
        tall = va.get_upper() > va.get_page_size()
        app.search.grab_focus()                 # focus leaves the text...
        d.type("pretzel")                       # a word in the OPEN entry
        d.pump(0.5)
        d.key("Return")                         # ...and Enter sends it back
        d.pump(0.4)
        check("opening a search match from the search field leaves the "
              "entry's date on screen",
              tall and d.focus() is app.body and va.get_value() == 0,
              "page %s/%s, focus %r, vadjustment %s"
              % (va.get_page_size(), va.get_upper(), d.focus(),
                 va.get_value()))

        app.select_entry(0)
        d.pump(0.3)
        app.select_entry(1)                     # back to the long entry
        d.pump(0.3)
        plus = [w for w in d.walk()
                if isinstance(w, Gtk.Button)
                and w.get_style_context().has_class("newbtn")][0]
        plus.grab_focus()                       # a button takes focus on click
        plus.clicked()
        d.pump(0.4)
        check("starting an entry with the + button leaves its date on screen",
              va.get_value() == 0, "vadjustment %s" % va.get_value())
    finally:
        d.close()


# ---------------------------------------------------------------- on paper --
def export_carries_the_whole_first_line():
    d = fresh()
    try:
        app = d.app
        d.menu_action("File", "New Entry")
        d.pump(0.2)
        first = ("Today I finally finished the garden fence and the "
                 "neighbours came over to admire it, which felt good.")
        app.body.get_buffer().set_text(first + "\n\nSecond paragraph here.")
        d.pump(1.3)
        check("the sidebar title stays the short display cut",
              app.entries[0]["title"] == first[:60],
              repr(app.entries[0]["title"]))
        d.menu_action("File", "Export to PDF")
        d.pump(3.0)
        docs = os.path.join(d.home, "Documents")
        made = ([os.path.join(docs, f) for f in sorted(os.listdir(docs))
                 if f.endswith(".pdf")] if os.path.isdir(docs) else [])
        text = ""
        if made:
            got = subprocess.run(["pdftotext", made[0], "-"], text=True,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            text = " ".join(got.stdout.split())
        check("Export to PDF writes a PDF into Documents",
              len(made) == 1 and text, "files %r" % (made,))
        check("the exported PDF carries the whole first line, not the "
              "60-character title cut",
              first in text, "PDF text was %r" % (text[:300],))
        check("...and the rest of the entry after it",
              "Second paragraph here." in text, "PDF text was %r"
              % (text[:300],))
    finally:
        d.close()


# ------------------------------------------------------------- the counter --
def word_count_counts_words():
    d = fresh()
    try:
        app = d.app
        d.menu_action("File", "New Entry")
        d.pump(0.2)
        buf = app.body.get_buffer()
        buf.set_text("one two three\n• apples\n• pears\nfour — five")
        d.pump(0.5)
        check("the word count skips the bullets and dashes the app itself "
              "inserts",
              word_number(app) == 7, app.count.get_text())
        buf.set_text("日本語のテストです")
        d.pump(0.5)
        check("...and counts a sentence written without spaces per character",
              word_number(app) == 9, app.count.get_text())
        buf.set_text("hello world")
        d.pump(0.5)
        check("...and still counts ordinary prose one word at a time",
              word_number(app) == 2, app.count.get_text())
    finally:
        d.close()


# ------------------------------------------------------- labels and states --
def new_entry_label_keeps_its_promise():
    d = fresh()
    try:
        app = d.app
        labels = [it[0] for it in app.menu_items("File")
                  if isinstance(it, tuple)]
        before = len(Gtk.Window.list_toplevels())
        d.menu_action("File", "New Entry")
        d.pump(0.3)
        after = len(Gtk.Window.list_toplevels())
        check("File offers New Entry without an ellipsis, because it asks "
              "nothing first",
              "New Entry" in labels and not any(
                  lab.startswith("New Entry…") for lab in labels),
              repr(labels))
        check("...and starting one opens no card",
              len(app.entries) == 1 and after == before,
              "%d entries, toplevels %d -> %d" % (len(app.entries), before,
                                                  after))
        labels = [it[0] for it in app.menu_items("File")
                  if isinstance(it, tuple)]
        check("Delete Entry carries no ellipsis either: it acts at once and "
              "is undoable",
              any(lab == "Delete Entry" for lab in labels), repr(labels))
        d.menu_action("File", "Delete Entry")
        d.pump(0.3)
        gone = len(app.entries)
        d.menu_action("Edit", "Undo")
        d.pump(0.3)
        check("...so a deleted entry comes back with Undo",
              gone == 0 and len(app.entries) == 1,
              "%d entries after delete, %d after undo" % (gone,
                                                          len(app.entries)))
    finally:
        d.close()


SAVED = re.compile(r"^● Saved \d\d:\d\d$")


def save_chip_reads_the_same_way():
    d = fresh()
    try:
        app = two_entries(d)
        typed = app.save.get_text()
        app.select_entry(1)
        d.pump(0.3)
        switched = app.save.get_text()
        app.body.grab_focus()
        d.type("x")
        d.pump(1.3)
        typed_again = app.save.get_text()
        d.menu_action("File", "New Entry")
        d.pump(0.3)
        started = app.save.get_text()
        check("the save chip reads the same after typing, after switching "
              "entries and after starting one",
              all(SAVED.match(t or "") for t in
                  (typed, switched, typed_again, started)),
              "typing %r, switch %r, typing again %r, new %r"
              % (typed, switched, typed_again, started))
    finally:
        d.close()


def main():
    try:
        page_geometry()
        opens_at_the_top()
        focus_does_not_scroll()
        export_carries_the_whole_first_line()
        word_count_counts_words()
        new_entry_label_keeps_its_promise()
        save_chip_reads_the_same_way()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print("%d checks, %d passed, %d FAILED"
          % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for name in FAILS:
            print("  " + name)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
