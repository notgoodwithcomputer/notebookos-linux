#!/usr/bin/env python3
"""Screenplay, driven the way a screenwriter uses it: write past the bottom of
the window, jump about the script, name a file, come back to it tomorrow.

THE BUGS THIS EXISTS FOR. The script body is a TextView packed INSIDE the paper
sheet (title block above it, page number over it), so it is not the scrolling
child of anything: GTK allocates it its whole content height and its own
adjustment has nothing left to move. Every "bring that on screen" in the app
went through body.scroll_to_mark on that dead adjustment, so:

  * typing at the bottom of the window ran off it — the writer typed blind;
  * View ▸ Go to End moved the caret to the last line and left the window on
    the title page, and Find selected a match nobody could see;
  * the viewport GTK inserted also clamped the page to whatever child took
    focus, and that child is the whole tall TextView — so clicking back into
    the script after editing the byline threw a scrolled script to line 1.

And around the same page: View ▸ Word Wrap grew the paper to the longest line
in the script with no way to scroll to what fell off the window, Save As
replaced the title the writer had typed with the file name upper-cased and
stripped of punctuation, plain-text saves dropped the title page silently, and
a finished PDF export was announced in the same red as a failed save.

    tools/guestrun.sh python3 tools/screenplay_realuse_selftest.py

Exit status is the number of failed checks.
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["NB_DRIVE_HOME_ROOT"] = tempfile.mkdtemp(prefix="nb-screenplay-realuse-")

import appdrive  # noqa: E402
from gi.repository import Gtk, Gdk  # noqa: E402

FAILS = []
COUNT = 0
DOCS = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "screenplay", "Documents")


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name
          + (": " + str(detail) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def desk(app):
    """The ScrolledWindow the paper sits on, found by walking, not by name."""
    w = app.body
    while w is not None and not isinstance(w, Gtk.ScrolledWindow):
        w = w.get_parent()
    return w


def caret_y(app, scroll):
    """The caret's y within the visible desk (negative or past the height
    means the writer cannot see what she is typing)."""
    buf = app.body.get_buffer()
    it = buf.get_iter_at_mark(buf.get_insert())
    rect = app.body.get_iter_location(it)
    wy = app.body.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET, rect.x, rect.y)[1]
    pos = app.body.translate_coordinates(scroll, 0, wy)
    return None if pos is None else pos[1]


def caret_visible(app, scroll):
    y = caret_y(app, scroll)
    return y is not None and 0 <= y <= scroll.get_allocation().height


def fill(d, lines, text="Beat"):
    for i in range(1, lines + 1):
        d.type("%s %d" % (text, i))
        d.key("Return")


def main():
    d = appdrive.Drive("screenplay")
    app = d.app
    scroll = desk(app)
    page = [w for w in d.walk()
            if "page" in w.get_style_context().list_classes()][0]
    import nbpicker
    fountain = os.path.join(DOCS, "the-long-night.fountain")
    try:
        # ---- SP-1: the page follows the caret ---------------------------
        app.body.grab_focus()
        d.pump(0.1)
        fill(d, 40)
        d.pump(0.4)
        check("typing past the bottom of the window keeps the caret on screen",
              caret_visible(app, scroll),
              "caret y=%s in a %dpx desk" % (caret_y(app, scroll),
                                             scroll.get_allocation().height))

        scroll.get_vadjustment().set_value(0)
        d.pump(0.2)
        d.menu_action("View", "Go to End")
        d.pump(0.4)
        check("View > Go to End brings the end of the script on screen",
              caret_visible(app, scroll),
              "caret y=%s" % (caret_y(app, scroll),))

        scroll.get_vadjustment().set_value(0)
        d.pump(0.2)
        d.key("f", ctrl=True)
        d.type("Beat 37")
        d.key("Return")
        d.pump(0.4)
        found = app.find_count.get_text()
        check("Find brings a match below the fold on screen",
              caret_visible(app, scroll) and found.startswith("1"),
              "count %r, caret y=%s" % (found, caret_y(app, scroll)))
        d.key("Escape")
        d.pump(0.2)

        # A different script on the desk starts at its own top, and an undo
        # that puts a long script back comes with the caret it restored.
        d.menu_action("File", "New")
        d.pump(0.5)
        check("File > New shows the top of the new page",
              scroll.get_vadjustment().get_value() == 0,
              "desk at %.0f" % scroll.get_vadjustment().get_value())
        d.menu_action("Edit", "Undo")
        d.pump(0.6)
        check("undoing that brings the restored caret back on screen",
              caret_visible(app, scroll),
              "caret y=%s in a %dpx desk" % (caret_y(app, scroll),
                                             scroll.get_allocation().height))

        # ---- SP-4: focus never moves the page ---------------------------
        adj = scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        d.pump(0.2)
        bottom = adj.get_value()
        app.scriptsubtitle.grab_focus()
        d.pump(0.3)
        after_byline = adj.get_value()
        app.body.grab_focus()
        d.pump(0.3)
        check("editing the byline and going back to the script leaves the "
              "page where it was",
              abs(after_byline - bottom) < 2 and abs(adj.get_value() - bottom) < 2,
              "%.0f -> byline %.0f -> body %.0f"
              % (bottom, after_byline, adj.get_value()))

        # ...but the title page must still be reachable when it takes focus.
        ev = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
        ev.window = app.get_window()
        ev.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
        app.scripttitle.emit("focus-in-event", ev)
        d.pump(0.3)
        title_pos = app.scripttitle.translate_coordinates(scroll, 0, 0)
        check("the title page scrolls into view when the title takes focus",
              title_pos is not None
              and 0 <= title_pos[1] <= scroll.get_allocation().height,
              "title y=%s" % (title_pos,))

        # ...and coming back to the window (the same widget handed focus again)
        # must not move the page.
        adj.set_value(adj.get_upper() - adj.get_page_size())
        d.pump(0.2)
        bottom = adj.get_value()
        app.scripttitle.emit("focus-in-event", ev)
        d.pump(0.3)
        check("coming back to the window leaves the page where it was",
              abs(adj.get_value() - bottom) < 2,
              "%.0f -> %.0f" % (bottom, adj.get_value()))

        # ---- SP-2: nothing in View may blow the page measure ------------
        measure = 800
        app.body.grab_focus()
        d.type("MARIA leans on the counter and says the same long sentence she "
               "has said every night this week, right to the end of the line.")
        d.pump(0.4)
        for item in d.menu("View"):
            if (not isinstance(item, (tuple, list)) or len(item) < 2
                    or not callable(item[1])):
                continue        # a separator
            d.menu_action("View", item[0])
            d.pump(0.3)
            check("View > %s leaves the paper its own width"
                  % item[0].split("   ")[0],
                  page.get_allocation().width <= scroll.get_allocation().width
                  and app.body.get_allocation().width == measure,
                  "page %d, body %d, desk %d" % (page.get_allocation().width,
                                                 app.body.get_allocation().width,
                                                 scroll.get_allocation().width))
        d.pump(0.2)
        app.body.grab_focus()
        d.key("Return")
        d.type("s" * 90)                 # one word longer than the measure
        d.pump(0.5)
        buf = app.body.get_buffer()
        end_x = app.body.get_iter_location(buf.get_end_iter()).x
        check("a word longer than the measure breaks inside the page",
              app.body.get_allocation().width == measure
              and page.get_allocation().width <= scroll.get_allocation().width
              and end_x < measure,
              "page %d, body %d, the word ends at x=%d"
              % (page.get_allocation().width,
                 app.body.get_allocation().width, end_x))

        # ---- SP-3: Save As names a file, it does not retitle the script --
        app.scripttitle.grab_focus()
        app.scripttitle.select_region(0, -1)
        d.type("Don't Look Up!")
        app.scriptsubtitle.grab_focus()
        app.scriptsubtitle.select_region(0, -1)
        d.type("written by Ana")
        d.pump(1.2)
        named = os.path.join(DOCS, app._default_name())    # don-t-look-up.json
        nbpicker.save_file = lambda *a, **k: named
        d.menu_action("File", "Save As")
        d.pump(0.5)
        saved = json.load(open(named, encoding="utf-8"))
        check("Save As keeps the title the writer typed",
              app.scripttitle.get_text() == "Don't Look Up!"
              and saved.get("title") == "Don't Look Up!",
              "page %r, file %r" % (app.scripttitle.get_text(),
                                    saved.get("title")))

        # ---- SP-6: plain text carries the title page ---------------------
        nbpicker.save_file = lambda *a, **k: fountain
        d.menu_action("File", "Save As")
        d.pump(0.5)
        raw = open(fountain, encoding="utf-8").read()
        check("a script saved as plain text keeps its title and byline",
              "Don't Look Up!" in raw and "written by Ana" in raw,
              "file opens %r" % raw[:60])

        # ---- SP-5: a finished export is not an error --------------------
        nbpicker.save_file = lambda *a, **k: os.path.join(DOCS, "look-up.pdf")
        d.menu_action("File", "Export to PDF")
        d.pump(0.5)
        chip = app.saved.get_label()
        check("a finished export is not shown in the error colour",
              "Exported" in chip and "#C8341E" not in chip, chip)
        d.pump(3.4)
        check("the save state comes back after the export message",
              "Saved" in app.saved.get_label(), app.saved.get_label())
    finally:
        d.close()

    # ---- SP-6 (continued): reopen that plain script tomorrow -------------
    d2 = appdrive.Drive("screenplay")
    app2 = d2.app
    try:
        import nbpicker as picker
        picker.open_file = lambda *a, **k: fountain
        d2.menu_action("File", "Open")
        d2.pump(0.5)
        buf = app2.body.get_buffer()
        body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        check("a plain script reopens with the title page it was saved with",
              app2.scripttitle.get_text() == "Don't Look Up!"
              and app2.scriptsubtitle.get_text() == "written by Ana",
              "title %r byline %r" % (app2.scripttitle.get_text(),
                                      app2.scriptsubtitle.get_text()))
        check("...and with its script, without the title-page keys in it",
              body.startswith("Beat 1") and "Title:" not in body,
              "body opens %r" % body[:60])

        # a script that simply BEGINS like a key must keep its first line
        plain = os.path.join(DOCS, "fade.fountain")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write("FADE IN:\n\nINT. HALL - DAY\n")
        picker.open_file = lambda *a, **k: plain
        d2.menu_action("File", "Open")
        d2.pump(0.5)
        buf = app2.body.get_buffer()
        body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        check("a script that opens 'FADE IN:' keeps its first line",
              body.startswith("FADE IN:"), "body opens %r" % body[:40])
        check("...and takes its title from the file name when it has none",
              app2.scripttitle.get_text() == "FADE",
              app2.scripttitle.get_text())
    finally:
        d2.close()
        shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)

    print("%d checks, %d passed, %d FAILED"
          % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for name in FAILS:
            print("  " + name)
        return len(FAILS)
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
