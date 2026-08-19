#!/usr/bin/env python3
"""Real-use checks for the E-book Reader: what a reader can see and keep.

These are driven through tools/appdrive.py — the app's own widget tree in an
offscreen holder at the panel size — because every defect they cover was
invisible to a check that reads state. The Library sheet reported itself
revealed while nothing was drawn; the table's computed column widths were
right while the ALLOCATED cells were not; and a disabled control's only tell is
its ink. So these look at allocations and at pixels, not at the values the app
was asked to use.

Covers, one named check per behaviour:
  * the Library sheet actually appears (and goes away again);
  * a table's columns line up in every row and stay inside the reading measure;
  * a nav/table-of-contents document is not counted as chapter one;
  * page and size controls that cannot be used are printed faintly;
  * the reading size is remembered across a relaunch, and the steppers stop
    offering a step they cannot take;
  * a file that cannot be read is never shelved as a book;
  * a notice that tells the reader to choose a file offers the control for it.

Run:
    tools/guestrun.sh python3 tools/ebook_realuse_selftest.py
    tools/guestrun.sh python3 tools/ebook_realuse_selftest.py --de DIR
"""
import os
import sys
import json
import shutil
import zipfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import appdrive                                                # noqa: E402
if "--de" in sys.argv:                     # drive an alternate tree (red proof)
    sys.path.insert(0, os.path.abspath(sys.argv[sys.argv.index("--de") + 1]))

import cairo                                                   # noqa: E402
from gi.repository import Gtk                                  # noqa: E402

FAILED, N = [], [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    """A check that could not be run is a FAIL BY NAME, never a traceback: a
    suite that explodes says the fixture was thin, not that the app is well."""
    for name in names:
        check("%s  [not reached: %s]" % (name, reason), False)


# --------------------------------------------------------------- fixtures
def _xhtml(title, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title>'
            '</head><body>%s</body></html>' % (title, body))


# One cell holds a whole sentence: it is the long cell that used to be handed
# its unwrapped natural width, which pulled that row's columns out of line with
# every other row and dragged the table past the reading measure.
TABLE = ("<table><tr><th>Item</th><th>Qty</th><th>Unit price</th>"
         "<th>Notes</th></tr>"
         + "".join(
             "<tr><td>Widget %d</td><td>%d</td><td>$%d.%02d</td><td>%s</td></tr>"
             % (i, i * 3, i * 7, i,
                "A rather long note that should wrap inside its own cell "
                "instead of spilling across the page" if i == 2 else "ok")
             for i in range(1, 6))
         + "</table>")

CH1 = _xhtml("One", "<h1>Chapter One: The Cafe</h1>" + "".join(
    "<p>Paragraph %d. The cafe on the corner served coffee and everyone "
    "agreed it was a fine place to sit.</p>" % i for i in range(1, 9)))
CH2 = _xhtml("Two", "<h1>Chapter Two: Figures</h1><p>Before the table.</p>"
             + TABLE + "<p>After the table.</p>")
CH3 = _xhtml("Three", "<h1>Chapter Three: The Long Road</h1>" + "".join(
    "<p>Long paragraph number %d.</p>" % i for i in range(1, 20)))
NAV = _xhtml("Contents",
             '<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">'
             '<h1>Contents</h1><ol>'
             '<li><a href="text/ch1.xhtml">Chapter One</a></li>'
             '<li><a href="text/ch2.xhtml">Chapter Two</a></li>'
             '<li><a href="text/ch3.xhtml">Chapter Three</a></li>'
             '</ol></nav>')
CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles>'
             '</container>')


def _opf(title, spine_head=""):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<package '
            'xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="uid"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="uid">urn:uuid:1</dc:identifier>'
            '<dc:title>%s</dc:title><dc:creator>Ada Tester</dc:creator>'
            '<dc:language>en</dc:language></metadata><manifest>'
            '<item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>'
            '<item id="c1" href="text/ch1.xhtml" '
            'media-type="application/xhtml+xml"/>'
            '<item id="c2" href="text/ch2.xhtml" '
            'media-type="application/xhtml+xml"/>'
            '<item id="c3" href="text/ch3.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine>%s<itemref idref="c1"/><itemref idref="c2"/>'
            '<itemref idref="c3"/></spine></package>' % (title, spine_head))


def build_epub(path, title, spine_head=""):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", _opf(title, spine_head))
        z.writestr("OEBPS/nav.xhtml", NAV)
        z.writestr("OEBPS/text/ch1.xhtml", CH1)
        z.writestr("OEBPS/text/ch2.xhtml", CH2)
        z.writestr("OEBPS/text/ch3.xhtml", CH3)
    return path


# ----------------------------------------------------------- instruments
def min_luminance(png, alloc):
    """The darkest pixel inside a widget's rectangle: how heavily it is inked."""
    surface = cairo.ImageSurface.create_from_png(png)
    data, stride = surface.get_data(), surface.get_stride()
    darkest = 255
    for y in range(alloc.y, alloc.y + alloc.height):
        for x in range(alloc.x, alloc.x + alloc.width):
            off = y * stride + x * 4
            lum = int(0.2126 * data[off + 2] + 0.7152 * data[off + 1]
                      + 0.0722 * data[off])
            darkest = min(darkest, lum)
    return darkest


def table_rows(drive):
    """(table box, [(row allocation, [cell allocations])]) for the open page."""
    tables = [w for w in drive.walk()
              if isinstance(w, Gtk.Box)
              and w.get_style_context().has_class("readtable")]
    if not tables:
        return None, []
    table = tables[0]
    rows = []
    for row in table.get_children():
        rows.append((row.get_allocation(),
                     [c.get_allocation() for c in row.get_children()]))
    return table, rows


def open_book(drive, path):
    """Open `path` the way a reader does: File > Open, through the picker."""
    drive.mod.nbpicker.open_file = lambda *a, **k: path
    drive.menu_action("File", "Open")
    drive.pump(0.3)


# ---------------------------------------------------------------- checks
def library_checks(drive, shots):
    """F1 — the Library icon and Library > Open Library... show the sheet.

    The sheet lives inside a scrim marked set_no_show_all(True), and
    gtk_widget_show_all() RETURNS IMMEDIATELY on such a widget: calling it on
    the scrim showed nothing at all, so both ways into the Library did nothing
    a reader could see while the revealer reported itself revealed."""
    names = ("the Library control shows the Library sheet",
             "Library > Open Library... shows the same sheet",
             "and Done puts the sheet away")
    sheet_widget = drive.app._library_sheet
    scrim = (sheet_widget.get_child()
             if getattr(drive.app, "_library_sheet_revealer", False)
             else sheet_widget)
    if scrim is None:
        not_reached("no Library scrim", *names)
        return
    sheet = scrim.get_child()

    drive.app._library_trigger.clicked()
    drive.pump(0.4)
    drive.shot(os.path.join(shots, "library-open.png"))
    alloc = sheet.get_allocation()
    check(names[0],
          scrim.get_visible() and scrim.get_mapped() and sheet.get_visible()
          and alloc.width > 400 and alloc.height > 100
          and bool(drive.find(Gtk.Button, label="Done")))

    drive.app._close_library()
    drive.pump(0.3)
    closed = not sheet_widget.get_visible()

    drive.menu_action("Library", "Open Library")
    drive.pump(0.4)
    check(names[1],
          scrim.get_visible() and scrim.get_mapped()
          and sheet.get_allocation().width > 400)

    # The app's own Done button, not a lookup by label: with the sheet never
    # shown there is no visible button to find, and this check has to report
    # that by NAME rather than raise out of the suite.
    done = getattr(drive.app, "_library_done_btn", None)
    if done is not None:
        done.clicked()
    drive.pump(0.3)
    check(names[2], closed and not sheet_widget.get_visible())


def table_checks(drive, shots):
    """F2 — a table's cells are the columns the reader can see.

    A size request only raises a widget's MINIMUM width; a wrapping label's
    NATURAL width is its text on one line. The row box handed a long cell that
    natural width, so its columns did not line up with the rows above it, the
    bottom rule ran on past the last cell as a phantom column, and the table
    dragged the whole reading column out to the screen edge at large type."""
    names = ("every table row uses the same column widths",
             "the table stays inside the reading column at every type size")
    drive.app._next_btn.clicked()          # page 2 carries the table
    drive.pump(0.4)
    drive.shot(os.path.join(shots, "table-17pt.png"))
    table, rows = table_rows(drive)
    if table is None or len(rows) < 3:
        not_reached("no table on the page", *names)
        return

    def columns(rows):
        return {tuple((c.x, c.width) for c in cells) for _row, cells in rows}

    aligned = len(columns(rows)) == 1
    # And the row is only as wide as its columns: no empty bordered region
    # trailing off to the right of the last cell.
    last = rows[0][1][-1]
    row_alloc = rows[0][0]
    aligned = aligned and (row_alloc.x + row_alloc.width) - (last.x
                                                            + last.width) < 12
    inside = []
    for step, size in ((0, "17pt"), (11, "28pt"), (-16, "12pt")):
        for _ in range(abs(step)):
            (drive.app._larger_btn if step > 0
             else drive.app._smaller_btn).clicked()
        drive.pump(0.4)
        drive.shot(os.path.join(shots, "table-%s.png" % size))
        table, rows = table_rows(drive)
        column = drive.app._epub_col.get_allocation()
        if table is None or not rows:
            not_reached("the table vanished at %s" % size, *names)
            return
        aligned = aligned and len(columns(rows)) == 1
        inside.append(table.get_allocation().width <= column.width
                      and column.width <= 620)
    check(names[0], aligned)
    check(names[1], all(inside))


def nav_spine_checks(mod, books):
    """F3 — a contents document is not chapter one.

    EPUB puts a spine itemref marked linear="no", and the manifest's
    properties="nav" document, OUTSIDE the default reading order. Counting
    either as a chapter opened the book on a dead list of links called
    "CHAPTER 1" and numbered every real chapter one too high."""
    name = "a nav document in the spine is not counted as a chapter"
    plain, _err = mod._epub_load(books["plain"])
    withnav, _err2 = mod._epub_load(books["nav"])
    if plain is None or withnav is None:
        not_reached("a fixture EPUB did not load", name)
        return
    first = withnav[0][0] if withnav[0] else ("", "")
    check(name,
          len(withnav) == len(plain) == 3
          and first[0] == "h" and "Chapter One" in first[1])


def ink_checks(drive, shots, book):
    """F4 — a control that cannot be used is printed faintly.

    Measured in pixels: at launch all four page/size controls are insensitive
    and used to render in exactly the same solid ink as a live control, so the
    reader could not tell an unavailable step from an available one."""
    names = ("controls that cannot be used are printed faintly",
             "and the ones that can be used are not")
    shot = drive.shot(os.path.join(shots, "toolbar-empty.png"))
    buttons = ("_prev_btn", "_next_btn", "_smaller_btn", "_larger_btn")
    off = {}
    for attr in buttons:
        button = getattr(drive.app, attr)
        if button.get_sensitive():
            not_reached("%s was sensitive with no document" % attr, *names)
            return
        off[attr] = min_luminance(shot, button.get_allocation())

    open_book(drive, book)
    shot = drive.shot(os.path.join(shots, "toolbar-reading.png"))
    on = {}
    for attr in buttons:
        button = getattr(drive.app, attr)
        on[attr] = (button.get_sensitive(),
                    min_luminance(shot, button.get_allocation()))
    live = [lum for sensitive, lum in on.values() if sensitive]
    dead = [lum for sensitive, lum in on.values() if not sensitive]
    if not live or not dead:
        not_reached("the reading toolbar had no mix of states", *names)
        return
    # Faint means faint: nothing in an unavailable control may be inked as
    # heavily as the live controls beside it.
    check(names[0], min(off.values()) > 90 and min(dead) > 90
          and min(dead) > max(live) + 40)
    check(names[1], max(live) < 60)


def size_memory_checks(drive, home, shots):
    """F5 — the reading size is a preference, and a stepper at its limit stops.

    Returns the size the reader left the app at, for the relaunch check."""
    names = ("A+ stops offering a step it cannot take",
             "A- stops offering a step it cannot take")
    app = drive.app
    for _ in range(40):
        app._larger_btn.clicked()
    drive.pump(0.2)
    check(names[0], app._read_pt == app.READ_PT_MAX
          and not app._larger_btn.get_sensitive()
          and app._smaller_btn.get_sensitive())
    for _ in range(40):
        app._smaller_btn.clicked()
    drive.pump(0.2)
    check(names[1], app._read_pt == app.READ_PT_MIN
          and not app._smaller_btn.get_sensitive()
          and app._larger_btn.get_sensitive())
    # Nine steps up from the floor: a size the reader plainly chose, and not
    # the default, so a relaunch that quietly resets to 17pt cannot pass.
    for _ in range(9):
        app._larger_btn.clicked()
    drive.pump(0.2)
    drive.shot(os.path.join(shots, "size-set.png"))
    return app._read_pt


def unreadable_checks(drive, books, shots):
    """F6/F7 — a file that cannot be read is not a book, and a card that says
    to choose a file offers the control that chooses one."""
    names = ("a file that cannot be read is not added to the library",
             "the notice says what went wrong, not just where the file is",
             "a notice that says to choose a file offers Open Book")
    app = drive.app
    shelf_before = [b["path"] for b in app._books]
    open_book(drive, books["broken"])
    drive.shot(os.path.join(shots, "unreadable.png"))
    shelved = [b["path"] for b in app._books]
    store = os.path.join(drive.home, ".config", "notebook", "ebook.json")
    try:
        with open(store, encoding="utf-8") as fh:
            written = json.dumps(json.load(fh))
    except OSError:
        written = ""
    check(names[0],
          shelved == shelf_before and app._open_path != books["broken"]
          and os.path.basename(books["broken"]) not in written)
    said = drive.texts()
    check(names[1],
          app._card_detail.get_text().endswith(".")
          and "EPUB archive" in app._card_detail.get_text()
          and any("could not be read" in t for t in said))
    action = app._card_action.get_visible()
    open_book(drive, books["txt"])
    drive.shot(os.path.join(shots, "unsupported.png"))
    check(names[2], action and app._card_action.get_visible()
          and app._card_action.get_label() and app._card_action.get_sensitive())


def main():
    root = tempfile.mkdtemp(prefix="nb-ebook-realuse-")
    shots = os.path.join(root, "shots")
    os.makedirs(shots)
    books = {
        "plain": build_epub(os.path.join(root, "plain.epub"), "The Test Book"),
        "nav": build_epub(os.path.join(root, "nav.epub"), "Nav In Spine",
                          '<itemref idref="nav" linear="no"/>'),
        "broken": os.path.join(root, "broken.epub"),
        "txt": os.path.join(root, "notes.txt"),
    }
    with open(books["broken"], "w", encoding="utf-8") as fh:
        fh.write("this is not a zip archive at all\n")
    with open(books["txt"], "w", encoding="utf-8") as fh:
        fh.write("plain text\n")

    if not Gtk.init_check()[0]:
        not_reached(
            "no display", "the Library control shows the Library sheet",
            "every table row uses the same column widths",
            "controls that cannot be used are printed faintly",
            "A+ stops offering a step it cannot take",
            "a file that cannot be read is not added to the library",
            "a notice that says to choose a file offers Open Book")
        return report(root)

    home = os.path.join(root, "home")
    drive = appdrive.Drive("ebook", home=home)
    nav_spine_checks(drive.mod, books)
    ink_checks(drive, shots, books["plain"])     # opens the book
    library_checks(drive, shots)
    table_checks(drive, shots)
    left_at = size_memory_checks(drive, home, shots)
    drive.close()

    # The reader comes back to the app: same home, a freshly built window.
    again = appdrive.Drive("ebook", home=home)
    again.pump(0.3)
    again.shot(os.path.join(shots, "relaunch.png"))
    check("the reading size is still the one the reader chose",
          again.app._read_pt == left_at
          and left_at != again.app.READ_PT_DEFAULT)
    unreadable_checks(again, books, shots)
    again.close()
    return report(root)


def report(root):
    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for name in FAILED:
            print("  " + name)
        print("shots kept in %s" % root)
        return 1
    shutil.rmtree(root, ignore_errors=True)
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
