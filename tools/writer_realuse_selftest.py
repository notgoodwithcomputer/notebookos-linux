#!/usr/bin/env python3
"""Writer, driven the way a person uses it: real widgets, real caret, pixels.

Every check here failed on the shipped app before the fix it names, and each
one is a thing a person does in a minute of ordinary use — type in a table
cell, press Ctrl+Z, pick Heading 1 on a blank line, select a word and set its
size, press File > New. The stand-in widgets in writer_format_selftest cannot
see any of it: an inert set_active hid the toolbar recursion, and a Writer
built with __new__ has no dialogs, no scrolled desk and no table cells at all.

    tools/guestrun.sh python3 tools/writer_realuse_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["NB_DRIVE_HOME_ROOT"] = tempfile.mkdtemp(prefix="nb-writer-realuse-")

import appdrive  # noqa: E402
from appdrive import pump  # noqa: E402
import cairo  # noqa: E402
from gi.repository import Gtk  # noqa: E402

FAILS = []
COUNT = 0
HOME = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "writer")
RECOVERY = os.path.join(HOME, ".config", "notebook", "writer.json")
_REAL_RUN = Gtk.Dialog.run


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def drive():
    """A Writer on a home of its own, with nothing carried over."""
    shutil.rmtree(HOME, ignore_errors=True)
    return appdrive.Drive("writer")


def tag_map(buf):
    it = buf.get_start_iter()
    out = []
    while not it.is_end():
        out.append((it.get_char(), tuple(sorted(
            t.get_property("name") or "" for t in it.get_tags()))))
        it.forward_char()
    return out


def tags_at(buf, offset):
    return sorted(t.get_property("name") or ""
                  for t in buf.get_iter_at_offset(offset).get_tags())


def body_text(app):
    return app.buf.get_text(app.buf.get_start_iter(), app.buf.get_end_iter(),
                            False)


def cells(app):
    return [t.serialize() for t in app._tables.values()]


def toolbar_button(d, tooltip_prefix):
    for w in d.find(Gtk.ToggleButton):
        if (w.get_tooltip_text() or "").startswith(tooltip_prefix):
            return w
    return None


def only_table(app):
    return list(app._tables.values())[0]


def widget_png(widget):
    """A synchronous render of one widget (a dialog is not in the holder)."""
    alloc = widget.get_allocation()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(alloc.width, 1),
                              max(alloc.height, 1))
    widget.draw(cairo.Context(surf))
    surf.flush()
    return surf


def pixel(surf, x, y):
    data, stride = surf.get_data(), surf.get_stride()
    i = y * stride + x * 4
    return (data[i + 2], data[i + 1], data[i])


def ink_and_paper(surf, widget, root, inset=0):
    """(dark pixels, light pixels) inside `widget`'s box on `root`'s render."""
    x, y = widget.translate_coordinates(root, 0, 0)
    alloc = widget.get_allocation()
    dark = light = 0
    for yy in range(y + inset, y + alloc.height - inset):
        for xx in range(x + inset, x + alloc.width - inset):
            r, g, b = pixel(surf, xx, yy)
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum > 200:
                light += 1
            elif lum < 140:
                dark += 1
    return dark, light


def combo_toggle(combo):
    """The drop-down toggle inside a combo, which is an INTERNAL child."""
    found = []

    def dig(widget):
        if isinstance(widget, Gtk.ToggleButton):
            found.append(widget)
            return
        if isinstance(widget, Gtk.Container):
            widget.forall(lambda ch, *_a: dig(ch))

    dig(combo)
    return found[0] if found else None


def walk_all(widget, out=None):
    out = [] if out is None else out
    out.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            walk_all(child, out)
    return out


# =============================================================================
#  the toolbar mirror (the defect this suite started for)
# =============================================================================
def toolbar_mirror():
    errors = []
    real_hook = sys.excepthook

    def hook(et, ev, tb):
        errors.append(et.__name__)
        real_hook(et, ev, tb)
    sys.excepthook = hook

    d = drive()
    try:
        tv = d.find(Gtk.TextView)[0]
        tv.grab_focus()
        d.pump()
        buf = tv.get_buffer()
        bold = toolbar_button(d, "Bold")
        center = toolbar_button(d, "Center")
        check("the toolbar has real Bold and Center toggles",
              bold is not None and center is not None)

        # ---- bold in the middle of plain text -----------------------------
        d.type("plain ")
        bold.clicked(); d.pump()
        d.type("bold")
        bold.clicked(); d.pump()
        d.type(" plain2")
        before = tag_map(buf)
        check("typing with Bold on and off gives a bold run inside plain text",
              [c for c, t in before if "bold" in t] == list("bold"),
              repr(before))

        # ---- the caret walks through it -----------------------------------
        errors.clear()
        d.key("Home"); d.pump(0.05)
        seen_bold_state = []
        for _ in range(9):
            d.key("Right"); d.pump(0.03)
            seen_bold_state.append(bool(bold.get_active()))
        d.key("End"); d.pump(0.05)
        after = tag_map(buf)
        check("moving the caret through bold text raises nothing",
              not errors, "exceptions: %r" % (errors,))
        check("...and changes no formatting", before == after,
              "diff at %r" % [i for i, (a, b) in enumerate(zip(before, after)) if a != b][:5])
        # caret positions 1..9 from Home: "plain " is 6 chars, so after 6
        # Rights the caret sits at the start of "bold" (bold state shows once
        # the caret is INSIDE the run, i.e. after the 7th Right).
        check("...while the Bold toggle mirrors the caret (off in plain, on in bold)",
              seen_bold_state[:5] == [False] * 5 and any(seen_bold_state[7:9]),
              repr(seen_bold_state))
        check("...without the mirror leaving the toggle stuck on at the end",
              bold.get_active() is False, repr(bold.get_active()))

        # ---- a centred paragraph next to a plain one ----------------------
        d.key("Return"); d.pump(0.03)
        center.clicked(); d.pump()
        d.type("centred line")
        d.key("Return"); d.pump(0.03)
        # a new paragraph inherits the alignment; make this one plain again
        left = toolbar_button(d, "Align left")
        if left is not None:
            left.clicked(); d.pump()
        d.type("plain again")
        errors.clear()
        snapshot = tag_map(buf)
        d.key("Up"); d.pump(0.05)          # caret into the centred paragraph
        d.key("Up"); d.pump(0.05)          # caret into the first plain one
        d.key("Down"); d.pump(0.05)
        d.key("Down"); d.pump(0.05)
        check("moving the caret across a centred paragraph raises nothing",
              not errors, "exceptions: %r" % (errors,))
        check("...and centres nothing else", tag_map(buf) == snapshot,
              "tags changed under a caret move")
    finally:
        sys.excepthook = real_hook
        d.close()


# =============================================================================
#  table cells are document text
# =============================================================================
def table_cells_are_document():
    d = drive()
    app = d.app
    seen = []
    try:
        app.body.grab_focus()
        d.type("Inventory")
        d.menu_action("Table", "Insert Table")
        d.pump(1.2)
        app._file_dirty = False
        app._set_save_chip("Saved 00:00", ok=True)
        d.pump(0.2)

        tbl = only_table(app)
        tbl._cells[0][0]["tv"].grab_focus(); d.type("Widgets")
        tbl._cells[0][1]["tv"].grab_focus(); d.type("42")
        d.pump(2.5)                        # past the 900ms autosave
        check("typing in a table cell marks the document as being edited",
              app._file_dirty and "Editing" in app.save_chip.get_text(),
              "dirty=%r chip=%r" % (app._file_dirty, app.save_chip.get_text()))
        stored = []
        try:
            with open(RECOVERY) as fh:
                stored = [t.get("data") for t in json.load(fh).get("tables", [])]
        except (OSError, ValueError) as exc:
            stored = [repr(exc)]
        check("...and the autosave carries the cell text to the recovery store",
              stored == [[["Widgets", "42"], ["", ""]]], repr(stored))

        def record(dlg):
            seen.append(dlg.get_title())
            return Gtk.ResponseType.CANCEL
        Gtk.Dialog.run = record
        d.menu_action("File", "New")
        d.pump(0.3)
        Gtk.Dialog.run = _REAL_RUN
        check("...and File > New asks before discarding a typed-in cell",
              seen == ["Discard changes?"] and
              cells(app) == [[["Widgets", "42"], ["", ""]]],
              "cards=%r cells=%r" % (seen, cells(app)))
    finally:
        Gtk.Dialog.run = _REAL_RUN
        d.close()


def undo_in_a_table_cell():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.type("Stock"); d.key("Return")
        d.menu_action("Table", "Insert Table")
        d.pump(1.2)
        tbl = only_table(app)
        tbl._cells[0][0]["tv"].grab_focus(); d.type("Bolts")
        tbl._cells[0][1]["tv"].grab_focus(); d.type("100")
        d.pump(1.2)                        # past the 600ms undo checkpoint
        d.key("z", ctrl=True); d.pump(0.4)
        after_undo = cells(app)
        check("Ctrl+Z with the caret in a cell leaves the table on the page",
              len(after_undo) == 1 and "Stock" in body_text(app),
              "tables=%r body=%r" % (after_undo, body_text(app)))
        d.key("z", ctrl=True, shift=True); d.pump(0.4)
        check("...and Redo brings the cell text back",
              cells(app) == [[["Bolts", "100"], ["", ""]]], repr(cells(app)))
    finally:
        d.close()


def delete_the_row_the_caret_is_in():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.menu_action("Table", "Insert Table")
        d.pump(1.0)
        d.menu_action("Table", "Add Row")
        d.pump(0.4)
        tbl = only_table(app)
        for row, word in enumerate(("first", "second", "third")):
            tbl._cells[row][0]["tv"].grab_focus(); d.type(word)
        d.pump(0.5)
        tbl._cells[0][0]["tv"].grab_focus(); d.pump(0.1)
        d.menu_action("Table", "Delete Row")
        d.pump(0.4)
        check("Delete Row removes the row the caret is in",
              [r[0] for r in tbl.serialize()] == ["second", "third"],
              repr(tbl.serialize()))

        d.menu_action("Table", "Add Column")
        d.pump(0.4)
        tbl._cells[0][2]["tv"].grab_focus(); d.type("kept")
        d.pump(0.4)
        tbl._cells[0][0]["tv"].grab_focus(); d.pump(0.1)
        d.menu_action("Table", "Delete Column")
        d.pump(0.4)
        check("Delete Column removes the column the caret is in",
              tbl.serialize()[0] == ["", "kept"], repr(tbl.serialize()))
    finally:
        d.close()


# =============================================================================
#  paragraph formatting picked on a blank line
# =============================================================================
def format_a_blank_line():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.pump(0.2)
        heading = writer_style_index("Heading 1")
        app.style_combo.set_active(heading)
        d.pump(0.2)
        check("the style combo holds a style picked on a blank line",
              app.style_combo.get_active() == heading,
              repr(app.style_combo.get_active_text()))
        d.type("My Title")
        d.pump(0.3)
        check("a style picked on a blank line applies to the words typed next",
              "style:Heading 1" in tags_at(app.buf, 0), repr(tags_at(app.buf, 0)))

        d.key("End"); d.key("Return"); d.pump(0.2)
        bullet = app._fmt_btns["list:bullet"]
        bullet.clicked(); d.pump(0.2)
        check("a bullet picked on a blank line lights its button",
              bool(bullet.get_active()))
        d.type("Apples"); d.pump(0.3)
        off = body_text(app).index("Apples")
        check("...and bullets the words typed next",
              "list:bullet" in tags_at(app.buf, off), repr(tags_at(app.buf, off)))

        d.key("End"); d.key("Return"); d.pump(0.2)
        d.menu_action("Format", "Center"); d.pump(0.2)
        d.type("Centred"); d.pump(0.3)
        off = body_text(app).index("Centred")
        check("Centre picked on a blank line centres the words typed next",
              "align:center" in tags_at(app.buf, off), repr(tags_at(app.buf, off)))
    finally:
        d.close()


def format_a_blank_middle_line():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.type("Alpha"); d.key("Return"); d.key("Return"); d.type("Gamma")
        d.pump(0.3)
        d.key("Up"); d.key("Home"); d.pump(0.2)
        app._fmt_btns["list:number"].clicked(); d.pump(0.2)
        d.type("Beta"); d.pump(0.3)
        off = body_text(app).index("Beta")
        # The tag used to land on the blank line's "\n" alone, and text typed
        # BEFORE a character never inherits that character's tags.
        check("a numbered list picked on a blank line between paragraphs holds",
              "list:number" in tags_at(app.buf, off), repr(tags_at(app.buf, off)))
    finally:
        d.close()


def writer_style_index(name):
    import writer
    return writer.STYLE_ORDER.index(name)


# =============================================================================
#  the size box
# =============================================================================
def size_box():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.type("Selected words here")
        d.pump(0.3)
        buf = app.buf
        buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(8))
        d.pump(0.2)
        ent = app.size_combo.get_child()
        ent.grab_focus()
        d.pump(0.3)
        ent.set_text("18")
        d.key("Return")
        d.pump(0.3)
        check("a size typed in the box applies to the words selected before it",
              "size:18" in tags_at(buf, 0), repr(tags_at(buf, 0)))
        check("...and Return in the size box puts the caret back on the page",
              app.get_focus() is app.body, repr(app.get_focus()))

        buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(8))
        ent.grab_focus(); d.pump(0.2)
        ent.set_text("9999")
        d.key("Return"); d.pump(0.3)
        check("an out-of-range size shows the size actually applied",
              ent.get_text() == "200" and "size:200" in tags_at(buf, 0),
              "entry=%r tags=%r" % (ent.get_text(), tags_at(buf, 0)))
        check("...and says what the sizes are",
              "6" in app.status.get_text() and "200" in app.status.get_text(),
              repr(app.status.get_text()))

        buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(8))
        ent.grab_focus(); d.pump(0.2)
        ent.set_text("abc")
        d.key("Return"); d.pump(0.3)
        check("a size that is not a number is put back, not silently ignored",
              ent.get_text() == "200" and "size:200" in tags_at(buf, 0),
              "entry=%r tags=%r" % (ent.get_text(), tags_at(buf, 0)))

        # the list still applies on its own (this is what used to work)
        buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(8))
        d.pump(0.1)
        import writer
        app.size_combo.set_active(writer.FONT_SIZES.index(20))
        d.pump(0.3)
        check("...and a size picked from the list still applies",
              "size:20" in tags_at(buf, 0), repr(tags_at(buf, 0)))

        # typed, then clicked away from without pressing Return
        buf.select_range(buf.get_iter_at_offset(9), buf.get_iter_at_offset(14))
        ent.grab_focus(); d.pump(0.2)
        ent.set_text("28")
        app.body.grab_focus(); d.pump(0.3)
        check("a size typed and then left behind is applied, not dropped",
              "size:28" in tags_at(buf, 10), repr(tags_at(buf, 10)))
    finally:
        d.close()


# =============================================================================
#  page furniture, New, word count
# =============================================================================
def header_band_and_new():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.type("Inventory")
        d.pump(0.3)
        app._header = "HDR {title} p{page}"
        app._footer = "FTR {title} p{page}"
        app._page = dict(app._page, size="Legal", orientation="landscape")
        app._page_numbers = True
        app._apply_page_geometry()
        d.pump(0.3)
        check("the header band previews {title} and {page} as the footer does",
              app.header_lbl.get_text() == "HDR Inventory p1",
              repr(app.header_lbl.get_text()))

        for i in range(40):
            d.type("Line %d" % i)
            d.key("Return")
        d.pump(0.5)
        adj = app._scroll.get_vadjustment()
        adj.set_value(adj.get_upper())     # read to the end of the document
        d.pump(0.3)
        Gtk.Dialog.run = lambda dlg: Gtk.ResponseType.OK
        d.menu_action("File", "New")
        d.pump(0.6)
        Gtk.Dialog.run = _REAL_RUN
        check("File > New starts from the default page setup",
              app._page.get("size") == "Letter"
              and app._page.get("orientation") == "portrait"
              and app._header == "" and app._footer == ""
              and app._page_numbers is False,
              "page=%r header=%r footer=%r pn=%r" % (
                  app._page, app._header, app._footer, app._page_numbers))
        check("...and shows the top of the new page",
              adj.get_value() == adj.get_lower(),
              "value=%r lower=%r" % (adj.get_value(), adj.get_lower()))
        check("...with no band left over from the document before it",
              app.footer_lbl.get_text() == "" and app.header_lbl.get_text() == "",
              "header=%r footer=%r" % (app.header_lbl.get_text(),
                                       app.footer_lbl.get_text()))
    finally:
        Gtk.Dialog.run = _REAL_RUN
        d.close()


def word_count():
    d = drive()
    app = d.app
    try:
        app.body.grab_focus()
        d.type("one - two three")
        d.pump(0.6)
        app._update_wordcount()
        check("a lone dash is not counted as a word",
              app.wc_label.get_text().startswith("3 "),
              repr(app.wc_label.get_text()))
    finally:
        d.close()


# =============================================================================
#  what the cards and the toolbar actually look like
# =============================================================================
def card_and_toolbar_pixels():
    d = drive()
    app = d.app
    seen = {}
    try:
        def look(name):
            def run(dlg):
                pump(0.2)
                dlg.show_all()
                dlg.check_resize()
                pump(0.2)
                surf = widget_png(dlg)
                for w in walk_all(dlg):
                    if isinstance(w, Gtk.Button) and "suggested-action" in \
                            w.get_style_context().list_classes():
                        label = w.get_child()
                        colour = label.get_style_context().get_color(
                            Gtk.StateFlags.NORMAL) if isinstance(
                                label, Gtk.Label) else None
                        dark, light = ink_and_paper(surf, w, dlg)
                        seen[name] = {"label": w.get_label(), "light": light,
                                      "dark": dark,
                                      "fg": None if colour is None else
                                      (colour.red, colour.green, colour.blue)}
                    if isinstance(w, Gtk.FlowBox):
                        rows = {}
                        for child in w.get_children():
                            alloc = child.get_allocation()
                            rows.setdefault(alloc.y, []).append(alloc.x)
                        seen[name] = {"rows": [len(v) for _k, v in
                                               sorted(rows.items())]}
                return Gtk.ResponseType.CANCEL
            return run

        app.body.grab_focus()
        d.type("Link me")
        d.pump(0.2)
        Gtk.Dialog.run = look("link")
        d.menu_action("Insert", "Link")
        d.pump(0.2)
        Gtk.Dialog.run = look("page")
        d.menu_action("File", "Page Setup")
        d.pump(0.2)
        Gtk.Dialog.run = look("colour")
        app._fg_btn.clicked()
        d.pump(0.2)
        Gtk.Dialog.run = _REAL_RUN

        for name in ("link", "page"):
            card = seen.get(name, {})
            fg = card.get("fg") or (0, 0, 0)
            check("the primary button on the %s card has a readable label"
                  % name,
                  sum(fg) > 2.4 and card.get("light", 0) > 60, repr(card))
        rows = seen.get("colour", {}).get("rows") or []
        check("the colour picker lays its swatches out in rows of six",
              rows[:1] == [6] and len(rows) <= 3, repr(rows))

        # the size box's drop-down toggle: an empty rounded box reads as a
        # broken button, so it has to carry a mark of its own. Found by
        # looking, not by name, so an app without one fails this by name too.
        toggle = combo_toggle(app.size_combo)
        dark = 0
        if toggle is not None:
            surf = widget_png(d.off.get_child())
            dark, _light = ink_and_paper(surf, toggle, d.off.get_child(),
                                         inset=4)
        check("the size box's drop-down toggle draws a chevron",
              toggle is not None and dark > 5,
              "toggle=%r dark pixels inside it: %d" % (toggle, dark))
    finally:
        Gtk.Dialog.run = _REAL_RUN
        d.close()


def main():
    for part in (toolbar_mirror, table_cells_are_document,
                 undo_in_a_table_cell, delete_the_row_the_caret_is_in,
                 format_a_blank_line, format_a_blank_middle_line, size_box,
                 header_band_and_new, word_count, card_and_toolbar_pixels):
        part()
    shutil.rmtree(os.environ["NB_DRIVE_HOME_ROOT"], ignore_errors=True)
    print("%d checks, %d passed, %d FAILED" % (COUNT, COUNT - len(FAILS),
                                               len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
