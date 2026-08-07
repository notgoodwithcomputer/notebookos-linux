#!/usr/bin/env python3
"""
Headless selftest for the desktop widget BOARD (widgets.py) and the screen that
configures it (widgetsettings.py).

The board is the first thing anyone sees and it cannot scroll, so the failures
worth catching are layout ones and store ones:

  1. Pinned column — Tasks over the area of two tiles with the calendar under
     it, fitting every panel size including a six-week month.
  2. Tile geometry — a tile is exactly half the Tasks card, and the grid never
     lays out more tiles than the board is tall enough to hold.
  3. Tile readers  — every one survives a missing, unparseable or wrong-shaped
     store, and a bad store costs its OWN tile and nothing else.
  4. Visibility    — the board store decides which tiles are on, the defaults
     are used when it is absent, and the Workout app's old switch is honoured.
  5. Round trip    — what Widget Settings writes is what the board reads.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=<overlay>/opt/notebook/de \
  python3 board_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                             # noqa: E402

# Measure under the SHIPPED theme. Without it the host GTK theme's larger
# paddings inflate every card and the fit checks read ~20px high.
_THEME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "buildroot/board/notebookos/rootfs-overlay/usr/share/"
                      "themes/Papertone/gtk-3.0/gtk.css")
try:
    _prov = Gtk.CssProvider()
    _prov.load_from_path(_THEME)
    Gtk.StyleContext.add_provider_for_screen(
        __import__("gi.repository.Gdk", fromlist=["Gdk"]).Screen.get_default(),
        _prov, 500)
except Exception:
    pass

RESULTS = []
PANELS = ((1920, 1080), (1366, 768), (1024, 740))


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def pump():
    for _ in range(30):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def fresh_home():
    home = tempfile.mkdtemp(prefix="nb-board-")
    os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
    return home


def put(home, name, obj):
    path = os.path.join(home, ".config", "notebook", name)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def board(home, w=1920, h=1080):
    """A freshly imported board against `home`, at a given panel size."""
    os.environ["NB_HOME"] = home
    for mod in ("widgets", "widgetsettings", "nbapp"):
        sys.modules.pop(mod, None)
    import nbapp
    nbapp.screen_size = lambda: (w, h)
    import widgets
    return widgets, widgets.Widgets()


def day(n):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


def laid_out(wmod, on):
    """How many tiles the grid should hold for a given on/off map.

    There are SEVEN tiles and six slots, so this is not len(TILE_ORDER) any
    more and never was a safe thing to hard-code: switching a seventh tile on
    must add nothing to the grid, and every count in this file has to agree
    about that or the tests disagree with the board they are checking."""
    return min(sum(1 for t in wmod.TILE_ORDER if on.get(t)),
               wmod.TILE_COLS * wmod.TILE_ROWS)


def full_home():
    """A home with something real in every store the board reads."""
    home = fresh_home()
    put(home, "tasks.json", [{"text": "Task %d" % i, "done": False}
                             for i in range(6)])
    put(home, "calendar.json", [{"title": "Dentist", "date": day(-3),
                                 "time": "09:30"}])
    log = {day(n): {"a": [15, 15, 15], "b": [20, 20]} for n in range(6)}
    put(home, "workout.json",
        {"log": log, "goals": {d: 5 for d in log},
         "exercises": [{"id": "a", "name": "Push-ups", "sets": 3, "reps": 15},
                       {"id": "b", "name": "Squats", "sets": 2, "reps": 20}]})
    put(home, "academics.json", {
        "classes": [{"name": "Organic Chemistry", "room": "Lab B4",
                     "meets": [{"day": d, "start": "14:00"} for d in range(7)]}],
        "homework": [{"title": "Problem set 4", "course": "Linear Algebra",
                      "due": day(-2), "done": False}]})
    # NOTE: these are the shapes the apps THEMSELVES write, verified against
    # journal.py / language.py / accounting.py. Seeding a shape the board
    # merely expects proves nothing — three readers were written against
    # invented keys ("transactions"/"amount", a "course" name, an ISO "date")
    # and every one of them showed its empty state forever on a real machine.
    now = time.localtime()
    put(home, "journal.json", {"entries": [
        {"day": str(now.tm_mday),
         "month_label": "%s %d" % (("January", "February", "March", "April",
                                    "May", "June", "July", "August",
                                    "September", "October", "November",
                                    "December")[now.tm_mon - 1], now.tm_year),
         "date": "today", "title": "An entry", "text": "x"}], "active": 0})
    # The shape bills.py itself writes: whole cents, an anchor due date and
    # the months between repeats. One overdue and one still to come, so the
    # tile has both a marked row and a quiet one to render.
    put(home, "bills.json", {"bills": [
        {"id": "b1", "payee": "City Light", "account": "44-99", "amount": 8420,
         "due": day(-2), "every": 1, "method": "mail", "lead": 5,
         "address": "PO Box 1188", "phone": "", "note": "", "paid": []},
        {"id": "b2", "payee": "Water Board", "account": "88-22", "amount": 4165,
         "due": day(20), "every": 1, "method": "phone", "lead": 0,
         "address": "", "phone": "555-0132", "note": "", "paid": []}]})
    put(home, "language.json", {"crowns": {"eo:0:0": 2}, "xp": 240,
                                "streak": 4, "goal": 20, "day_xp": 20,
                                "day": time.strftime("%Y-%m-%d")})
    # The shapes these apps themselves write. contacts keeps a free-text
    # birthday field; ebook keeps a fraction through each book; novel keeps
    # chapter bodies, which the card counts words in.
    put(home, "contacts.json", {"people": [
        {"name": "Ada Whitfield", "bday": time.strftime("%d %B"),
         "phone": "555-0101"},
        {"name": "Idris Mbeki", "bday": "3 January", "phone": "555-0102"}]})
    put(home, "ebook.json", {"books": [
        {"path": "/root/Documents/a.epub", "title": "The Long Winter",
         "fmt": "epub", "pos": 40, "frac": 0.42, "total": 300},
        {"path": "/root/Documents/b.epub", "title": "Sea Room",
         "fmt": "epub", "pos": 0, "frac": 0.0, "total": 280}],
        "open": "/root/Documents/a.epub"})
    put(home, "novel.json", {"title": "The Salt Road", "active": 0,
                             "chapters": [
        {"num": 1, "title": "Landfall", "body": "word " * 120},
        {"num": 2, "title": "The Crossing", "body": "word " * 45}]})
    put(home, "accounting.json", {"tx": [{"date": "2026-07-28", "amt": 12.5}],
                                  "opening": 0.0})
    put(home, "mealplanner.json", {"plan": {time.strftime("%Y-%m-%d"): {
        "breakfast": {"kind": "recipe", "title": "Porridge"},
        "lunch": {"kind": "note", "title": "Leftovers"},
        "dinner": {"kind": "takeout", "title": "Curry house"}}}})
    # Every tile the board knows about, read from the board rather than
    # listed here: a hardcoded list turns "a tile was added" into eight
    # unrelated test failures.
    import widgets as _w_probe
    put(home, "widgets.json",
        {"tiles": {t: True for t in _w_probe.TILE_ORDER}})
    return home


# -- 1. the pinned column ----------------------------------------------------
home = full_home()
wmod, b = board(home)
check("the column holds exactly Tasks then the calendar, nothing else",
      len(b._col.get_children()) == 2, len(b._col.get_children()))
check("the row budget covers the column only, not the tiles",
      len(b._row_caps()) == 2, b._row_caps())
check("Tasks gets at least four rows on a full-size panel",
      b._row_caps()[0] >= 4, b._row_caps())
b.destroy()

# A tile is half the Tasks card, which is what "Tasks takes the area of two
# widgets" means — check it against the card as RENDERED, not the arithmetic.
wmod, b = board(home)
tasks_card = b._col.get_children()[0]
off = Gtk.OffscreenWindow()
col = b.get_child()
b.remove(col)
off.set_size_request(1888, 1002)
off.add(col)
off.show_all()
pump()
tasks_h = tasks_card.get_preferred_height_for_width(b._col_w)[0]
# Tasks and the calendar SHARE the right-hand column, stacked, so Tasks is one
# tile tall -- and it must not demand more than that, or it squeezes the
# calendar underneath it off the bottom of the screen.
check("Tasks fits its share of the pinned column",
      tasks_h <= b._tile_h + 2,
      "card %d, one tile %d" % (tasks_h, b._tile_h))
off.destroy()

for W, H in PANELS:
    wmod, b = board(home, W, H)
    col = b.get_child().get_children()[-1]
    b.get_child().remove(col)
    off = Gtk.OffscreenWindow()
    colh = max(360, H - wmod.PANEL_H - 2 * wmod.BOARD_MARGIN)
    off.set_size_request(b._col_w, colh)
    off.add(col)
    off.show_all()
    pump()
    need = col.get_preferred_height_for_width(b._col_w)[0]
    check("the pinned column fits a %dx%d panel" % (W, H), need <= colh,
          "needs %d of %d" % (need, colh))
    off.destroy()

# A six-week month is the worst case the column ever lays out, and the one a
# five-week month hides.
SIX = time.localtime(time.mktime((2026, 8, 15, 12, 0, 0, 0, 1, -1)))
_real_localtime = time.localtime
time.localtime = lambda *a: SIX
try:
    for W, H in PANELS:
        wmod, b = board(home, W, H)
        check("a six-week month really is six weeks",
              len(wmod._month_weeks(2026, 8)) == 6) if (W, H) == PANELS[0] else None
        col = b.get_child().get_children()[-1]
        b.get_child().remove(col)
        off = Gtk.OffscreenWindow()
        colh = max(360, H - wmod.PANEL_H - 2 * wmod.BOARD_MARGIN)
        off.set_size_request(b._col_w, colh)
        off.add(col)
        off.show_all()
        pump()
        need = col.get_preferred_height_for_width(b._col_w)[0]
        check("the column fits a six-week month at %dx%d" % (W, H),
              need <= colh, "needs %d of %d" % (need, colh))
        off.destroy()
finally:
    time.localtime = _real_localtime


def tile_words(b, tid):
    """(text, empty) for one tile under the board's current contract.

    `text` is every word the tile would render -- its summary, its rows and its
    empty-state call to action -- flattened. A tile is allowed to have no
    summary, or no rows; what it may never be is silent.
    """
    meta, rows, cta, empty = b._tile_content(tid)
    # A card may answer with a single drawn MARK instead of rows (the Journal's
    # written-today tick or cross). Its words live in that mark.
    mk = getattr(b, "_mark", None)
    bits = [meta or ""]
    if mk:
        bits += [str(mk.get("label") or ""), str(mk.get("date") or "")]
    for r in rows or []:
        lead = r[0]
        if isinstance(lead, (tuple, list)):
            lead = ""                      # a checkbox, not a word
        bits += [str(lead or ""), str(r[1] or ""), str(r[2] or "")]
    if cta:
        bits += [str(cta[0] or ""), str(cta[1] or "")]
    return " ".join(x for x in bits if x).strip(), empty

# -- 2. the tile grid --------------------------------------------------------
for W, H in PANELS:
    wmod, b = board(home, W, H)
    laid = len(b._tilegrid.get_children())
    cols = b._tile_columns()
    rows = max(1, (b._avail_h + wmod.BOARD_GAP) //
               (b._tile_h + wmod.BOARD_GAP))
    check("every switched-on tile is laid out at %dx%d" % (W, H),
          laid == min(len(wmod.TILE_ORDER), cols * rows),
          "laid %d, room for %d"
          % (laid, cols * rows))
    check("the tiles fit the board's width at %dx%d" % (W, H),
          cols * b._tile_w + (cols - 1) * wmod.BOARD_GAP <= (wmod.TILE_COLS * b._tile_w
           + (wmod.TILE_COLS - 1) * wmod.BOARD_GAP) + 1,
          "%d cols in %dpx" % (cols, (wmod.TILE_COLS * b._tile_w)))
    b.destroy()


# -- 3. every tile reader survives a broken store ----------------------------
# Read from the board, not restated: a reader added without a test here
# would otherwise go unchecked.
import widgets as _w_readers
READERS = _w_readers.TILE_ORDER
FILES = {"academics": "academics.json", "homework": "academics.json",
         "workout": "workout.json", "journal": "journal.json",
         "language": "language.json", "accounting": "accounting.json",
         "bills": "bills.json", "birthdays": "contacts.json",
         "reading": "ebook.json", "novel": "novel.json"}
for damage, blob in (("missing", None),
                     ("unparseable", "{oh no"),
                     ("a bare list", "[1, 2, 3]"),
                     ("wrong types", json.dumps({"classes": 7, "homework": "x",
                                                 "entries": 3, "course": [],
                                                 "transactions": "nope",
                                                 "progress": 9}))):
    broken = full_home()
    for name in set(FILES.values()):
        if blob is None:
            os.remove(os.path.join(broken, ".config", "notebook", name))
        else:
            put(broken, name, blob)
    wmod, b = board(broken)
    ok = True
    for tid in READERS:
        try:
            # Whatever the reader did or failed to do, the tile must come
            # back with words in it. A reader may legitimately return None
            # (nothing to report) -- it then falls back to its empty state,
            # which is still words. A SILENT tile is the failure.
            words, _empty = tile_words(b, tid)
            if not words:
                ok = False
        except Exception as exc:                                # noqa: BLE001
            ok = False
            check("  %s reader on a %s store" % (tid, damage), False, repr(exc))
    check("every tile reader survives a %s store" % damage, ok)
    check("...and the board still lays its tiles out",
          len(b._tilegrid.get_children()) >= 1,
          len(b._tilegrid.get_children()))
    b.destroy()
    shutil.rmtree(broken, ignore_errors=True)


# One bad store must cost its OWN tile only.
part = full_home()
put(part, "accounting.json", "{truncated")
wmod, b = board(part)
check("a single bad store does not take the other tiles with it",
      len(b._tilegrid.get_children()) == laid_out(wmod, b.board),
      len(b._tilegrid.get_children()))
b.destroy()

# The readers must actually READ. An empty state where there is data is the
# failure mode a "does not crash" test cannot see.
live = full_home()
wmod, b = board(live)
speaks = {}
for tid in READERS:
    speaks[tid] = not tile_words(b, tid)[1]
check("every tile with data in its store has something to say",
      all(speaks.values()), speaks)
# The journal tile is the one card whose job is to PROMPT. Written today: it
# reports, and offers no call to action. Not written: it says so and invites an
# entry. Checking both directions, because the prompt only earns its place if
# it goes away once you have written.
# The Journal card answers ONE question with ONE mark: written today, or not.
_jr = b._read_journal()
_jmark = _jr[4] if len(_jr) >= 5 else None
check("the journal tile sees an entry written today",
      bool(_jmark) and _jmark.get("done") is True, _jmark)
check("...and says so in words, with the date under it",
      bool(_jmark) and _jmark.get("label") and _jmark.get("date"), _jmark)
check("...and it does NOT list the back catalogue", not _jr[1], _jr[1])
check("the accounting tile totals opening + tx, formatted like the app",
      b._read_accounting()[0] == "$12.50", b._read_accounting())
# A tile reader may hand back anything a hand-edited JSON file can hold. The
# board used to build the label straight from it, so a class called 7 was a
# TypeError that took the WHOLE desktop down at launch, not one tile.
b._read_academics = lambda: (7, ["not", "a", "string"])
b._read_homework = lambda: None
b._read_workout = lambda: ()
b._read_journal = lambda: "just one string"
words, _empty = tile_words(b, "academics")
check("a reader that returns a number still renders a tile", bool(words), words)
for tid in ("homework", "workout", "journal"):
    words, empty = tile_words(b, tid)
    check("  a %s reader returning the wrong shape falls back to its empty "
          "state" % tid, empty and bool(words), (words, empty))
b._rebuild_tiles()
check("...and the grid still holds every switched-on tile",
      len(b._tilegrid.get_children()) == laid_out(wmod, b.board),
      len(b._tilegrid.get_children()))
b.destroy()
shutil.rmtree(live, ignore_errors=True)
shutil.rmtree(part, ignore_errors=True)


# A ROW'S VALUE CELL MUST RENDER ITS TEXT, not an ellipsis standing in for it.
#
# tile_words above reads the tile's content MODEL, which is why this went
# unseen: the strings were all present and correct, and the widget that drew
# them was one character wide. An ellipsizing label reports max_width_chars as
# its natural width, and a pack_end child with expand=False gets exactly that
# -- so the Homework card's due column shipped as three bare "..." marks.
#
# MEASURED INSIDE A REALISED WINDOW, and that is not a detail: a label that is
# not in one resolves no font and answers 0 to every width question, so the
# first version of this check compared 0 against 0 and passed against the
# broken code it was written for. The invariant is stated against the OUTPUT --
# a ten-character value must be drawn wider than a bare ellipsis is -- so no
# particular choice of constant can satisfy it by accident.
wmod, b = board(full_home())


def drawn_value_width(spec):
    """How wide the right-hand cell of a built content row is actually drawn."""
    row = b._content_row(spec)
    cells = [c for c in row.get_children() if isinstance(c, Gtk.Label)]
    if not cells:
        return 0
    win = Gtk.OffscreenWindow()
    win.add(row)
    win.set_size_request(320, 40)
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    got = cells[-1].get_allocated_width()
    win.destroy()
    return got


_dots = drawn_value_width((None, "Some Payee", "\u2026", False))
for _what, _value in (("a plain value", "in 12 days"),
                      ("a marked value", ("alert", "Post now"))):
    _got = drawn_value_width((None, "Some Payee", _value, False))
    check("%s is drawn at its own width, not squeezed to an ellipsis" % _what,
          _got > _dots, (_got, _dots))
b.destroy()


# -- 4. which tiles are on ---------------------------------------------------
plain = fresh_home()
wmod, b = board(plain)
check("with no board store the defaults decide",
      b.board == wmod.TILE_DEFAULT_ON, b.board)
# THE SHIPPED BOARD IS EXACTLY FULL. The grid is a fixed 3x2 that fills the
# desktop, so a tile short of six leaves a hole in it and a seventh switched on
# is simply never drawn. Both directions are checked, because either one on its
# own passes on a board that is wrong the other way.
slots = wmod.TILE_COLS * wmod.TILE_ROWS
check("the shipped defaults fill the grid exactly",
      sum(1 for t in wmod.TILE_ORDER if b.board[t]) == slots,
      b.board)
check("...and there are more tiles to choose from than the grid can hold",
      len(wmod.TILE_ORDER) > slots, len(wmod.TILE_ORDER))
check("...so the rest ship switched off, ready to be chosen",
      sum(1 for t in wmod.TILE_ORDER if not b.board[t])
      == len(wmod.TILE_ORDER) - slots, b.board)
check("...and Bills is one of the six that ship", b.board["bills"] is True,
      b.board)
check("...and every switched-on tile is actually laid out",
      len(b._tilegrid.get_children()) == slots,
      len(b._tilegrid.get_children()))
b.destroy()

put(plain, "workout.json", {"show_widget": True, "exercises": [], "log": {}})
wmod, b = board(plain)
check("the Workout app's old desktop switch is carried over",
      b.board["workout"] is True, b.board)
b.destroy()

# A tile id this build does not have -- an older release's, or a hand edit --
# must simply be ignored rather than switching something else off by accident.
# ("language" used to be the example here; it is a real tile again, which is
# exactly the kind of drift that makes a hard-coded name the wrong test.)
put(plain, "widgets.json", {"tiles": {"journal": False, "weather": False}})
wmod, b = board(plain)
check("the board store switches a tile off", b.board["journal"] is False)
check("...and leaves the tiles it does not mention alone",
      b.board["meals"] is True and b.board["workout"] is True, b.board)
check("a switched-off tile is not laid out",
      len(b._tilegrid.get_children()) == laid_out(wmod, b.board),
      len(b._tilegrid.get_children()))
check("...and a stored tile that no longer exists is ignored",
      "weather" not in b.board, sorted(b.board))
b.destroy()

# UPGRADING A DESKTOP THAT PREDATES THE BILL TRACKER. The store on such a
# machine names the old six tiles and knows nothing about bills, so left alone
# the new tile is appended seventh and never drawn -- installing the app would
# look like it had done nothing to the desktop. See widgets.adopt_bills.
OLD_SIX = ("academics", "homework", "meals", "workout", "journal",
           "accounting")
put(plain, "widgets.json", {"tiles": {t: True for t in OLD_SIX},
                            "order": list(OLD_SIX)})
wmod, b = board(plain)
check("an upgraded board puts Bills on the desktop",
      b.board["bills"] is True, b.board)
check("...by standing Accounting down, not by growing the grid",
      b.board["accounting"] is False
      and len(b._tilegrid.get_children()) == wmod.TILE_COLS * wmod.TILE_ROWS,
      (b.board, len(b._tilegrid.get_children())))
check("...into the slot Accounting held, leaving the rest where they were",
      b.board_order[:6] == list(OLD_SIX[:5]) + ["bills"], b.board_order)
check("...and every other tile keeps the state it was saved with",
      all(b.board[t] is True for t in OLD_SIX[:5]), b.board)
b.destroy()

# ...and it is a ONE-TIME move, not a rule that keeps re-asserting itself. A
# store that names bills has been through Widget Settings, so whatever it says
# is the owner's own choice and must survive an app restart.
put(plain, "widgets.json",
    {"tiles": dict({t: True for t in OLD_SIX}, bills=False),
     "order": list(OLD_SIX) + ["bills"]})
wmod, b = board(plain)
check("a board that already knows about Bills is left exactly as saved",
      b.board["bills"] is False and b.board["accounting"] is True, b.board)
b.destroy()

put(plain, "widgets.json", "{not json at all")
wmod, b = board(plain)
check("a corrupt board store falls back to the defaults rather than an "
      "empty desktop", len(b._tilegrid.get_children()) >= 1,
      len(b._tilegrid.get_children()))
b.destroy()
shutil.rmtree(plain, ignore_errors=True)


# -- 5. Widget Settings round trip -------------------------------------------
rt = full_home()
os.environ["NB_HOME"] = rt
for mod in ("widgets", "widgetsettings", "nbapp"):
    sys.modules.pop(mod, None)
import nbapp                                                    # noqa: E402
nbapp.screen_size = lambda: (1280, 800)
import widgets as _w                                            # noqa: E402
import widgetsettings                                           # noqa: E402

ws = widgetsettings.WidgetSettings()
check("Widget Settings lists every tile the board can show",
      sorted(ws._switches) == sorted(_w.TILE_ORDER), sorted(ws._switches))
check("...and does not offer to switch off the pinned two",
      "tasks" not in ws._switches and "calendar" not in ws._switches)
ws._set_all(False)
wmod, b = board(rt)
check("switching everything off empties the tile grid",
      len(b._tilegrid.get_children()) == 0,
      len(b._tilegrid.get_children()))
check("...but the pinned column is untouched",
      len(b._col.get_children()) == 2, len(b._col.get_children()))
b.destroy()

os.environ["NB_HOME"] = rt
for mod in ("widgets", "widgetsettings", "nbapp"):
    sys.modules.pop(mod, None)
import nbapp                                                    # noqa: E402,F811
nbapp.screen_size = lambda: (1280, 800)
import widgets as _w                                            # noqa: E402,F811
import widgetsettings                                           # noqa: E402,F811
ws = widgetsettings.WidgetSettings()
ws._set_all(True)
wmod, b = board(rt)
check("and switching them back on restores the board",
      len(b._tilegrid.get_children()) == laid_out(wmod, b.board),
      len(b._tilegrid.get_children()))
check("...and never draws more tiles than the grid has slots for",
      len(b._tilegrid.get_children())
      == wmod.TILE_COLS * wmod.TILE_ROWS,
      len(b._tilegrid.get_children()))
b.destroy()

# The whole row is the target, not just the switch: a click that lands on the
# name and does nothing reads as a dead control.
os.environ["NB_HOME"] = rt
for mod in ("widgets", "widgetsettings", "nbapp"):
    sys.modules.pop(mod, None)
import nbapp                                                    # noqa: E402,F811
nbapp.screen_size = lambda: (1024, 740)
import widgets as _w                                            # noqa: E402,F811
import widgetsettings                                           # noqa: E402,F811
ws = widgetsettings.WidgetSettings()
# From the SHIPPED board, not from whatever the block above left behind: the
# checks below are about a row click on an ordinary desktop, and the previous
# block deliberately leaves the store in the over-full state a hand edit can
# produce -- where a tile switched off cannot be switched back on until room
# is made. That rule has its own checks in widgetsettings_selftest.
ws._set_all(False)
ws._fill_board()
was = ws.data["journal"]


class _Press(object):
    button = 1


ws._on_row_press(None, _Press(), "journal")
check("clicking a settings row toggles that widget",
      ws.data["journal"] is (not was)
      and ws._switches["journal"].get_active() is (not was), ws.data)
check("...and the click is written to the store the board reads",
      json.load(open(os.path.join(rt, ".config", "notebook",
                                  "widgets.json")))["tiles"]["journal"]
      is (not was))
ws._on_row_press(None, _Press(), "journal")
check("...and clicking it again puts it back", ws.data["journal"] is was)

# The reading column is a fixed 620px. A wrapping label reports its whole
# unwrapped line as its natural width, so one longer sentence in the lede used
# to stretch the column to 851px and drag every switch across the screen.
body = ws.content.get_children()[0]
ws.content.remove(body)
off = Gtk.OffscreenWindow()
off.set_size_request(1024, 700)
off.add(body)
off.show_all()
pump()
col_alloc = None
stack = [body]
while stack:
    node = stack.pop()
    if isinstance(node, Gtk.Box) and node.get_halign() == Gtk.Align.CENTER:
        col_alloc = node.get_allocation()
        break
    if isinstance(node, Gtk.Container):
        stack.extend(node.get_children())
check("the settings screen keeps its 620px reading column at 1024 wide",
      col_alloc is not None
      and col_alloc.width == widgetsettings.COLUMN_W,
      col_alloc.width if col_alloc else None)
check("...and the whole screen fits a 1024x740 panel without scrolling",
      body.get_preferred_height_for_width(1024)[0] <= 740 - 46,
      body.get_preferred_height_for_width(1024)[0])
off.destroy()
shutil.rmtree(rt, ignore_errors=True)


# -- 6. how the board LOOKS --------------------------------------------------
# Everything above proves the board holds the right things. This section is
# about where they land, because the board cannot scroll and nothing on it can
# be moved: the failures here are the ones a user calls "broken".

def render(b):
    """The board laid out at the size it would really get, so allocations can
    be read off it."""
    root = b.get_child()
    b.remove(root)
    off = Gtk.OffscreenWindow()
    # The REAL toplevel is the content area PLUS a BOARD_MARGIN frame on every
    # side: the window covers the whole desktop under the panel and the board
    # box is inset inside it, so the outermost cards have somewhere to cast a
    # shadow (a CSS box-shadow cannot paint outside its toplevel). Sizing this
    # window to the content alone made the margins eat into the grid instead,
    # and every tile measured short.
    off.set_size_request(
        (wmod.TILE_COLS * b._tile_w) + b._col_w + wmod.BOARD_GAP
        + 2 * wmod.BOARD_MARGIN,
        b._avail_h + 2 * wmod.BOARD_MARGIN)
    off.add(root)
    off.show_all()
    pump()
    return off


def tiles_of(b):
    return sorted((t.get_allocation() for t in b._tilegrid.get_children()),
                  key=lambda a: (a.y, a.x))


# The tile grid packs against the PINNED COLUMN, not the left screen edge. The
# Finder floats over the middle of the desktop; left-packed, the grid put every
# tile behind it bar a ~24px slice at the screen edge, which reads as cards
# failing to draw. Its right edge must sit one BOARD_GAP from the column.
packed = full_home()
for W, H in PANELS:
    wmod, b = board(packed, W, H)
    off = render(b)
    grid = b._tilegrid.get_allocation()
    col = b._col.get_allocation()
    check("the tiles pack against the pinned column at %dx%d" % (W, H),
          abs((col.x - wmod.BOARD_GAP) - (grid.x + grid.width)) <= 1,
          "grid ends %d, column starts %d" % (grid.x + grid.width, col.x))
    allocs = tiles_of(b)
    check("every tile is the same width at %dx%d" % (W, H),
          len({a.width for a in allocs}) == 1, sorted({a.width for a in allocs}))
    # ...and exactly one tile height. A tile whose three
    # lines no longer fit (a bigger font, a taller translation) grows its row
    # and quietly breaks "Tasks takes the area of two widgets".
    check("every tile is exactly half the Tasks card at %dx%d" % (W, H),
          {a.height for a in allocs} == {b._tile_h},
          sorted({a.height for a in allocs}))
    rows = {}
    for a in allocs:
        rows.setdefault(a.y, []).append(a)
    # Allocations are in WINDOW coordinates, and the board box is inset by
    # BOARD_MARGIN, so the content floor is the margin plus the board height.
    floor = b._avail_h + wmod.BOARD_MARGIN
    check("no tile is laid out below the bottom of the board at %dx%d" % (W, H),
          all(a.y + a.height <= floor for a in allocs),
          "board %d, floor %d" % (b._avail_h, floor))
    # A last row that does not fill the grid is INDENTED, so every row still
    # ends flush against the column. The hole belongs on the far side, where
    # there is desktop, not beside the pinned cards where it reads as a gap.
    ends = {r[-1].x + r[-1].width for r in rows.values()}
    check("a part-full last row still ends flush with the rest at %dx%d"
          % (W, H), len(ends) == 1, sorted(ends))
    off.destroy()
    b.destroy()

# A 40-character class name must not stretch its own tile: an ellipsizing
# GtkLabel still reports the whole string as its natural width, and the grid
# shares spare width out in proportion to that (measured: 311px and 355px side
# by side on a 1366 panel).
put(packed, "academics.json", {
    "classes": [{"name": "Advanced Organic Chemistry Laboratory II",
                 "room": "Science Building Annexe, Laboratory B4",
                 "meets": [{"day": d, "start": "14:00"} for d in range(7)]}],
    "homework": [{"title": "Write up the titration experiment results",
                  "course": "Advanced Organic Chemistry Laboratory II",
                  "due": day(-1), "done": False}]})
wmod, b = board(packed, 1366, 768)
off = render(b)
widths = {a.width for a in tiles_of(b)}
check("a 40-character class name does not stretch its own tile",
      widths == {b._tile_w}, sorted(widths))
off.destroy()
b.destroy()

# Two tiles must not be laid out as two tiles and four empty column-widths.
put(packed, "widgets.json", {"tiles": {t: t in ("academics", "accounting")
                                       for t in wmod.TILE_ORDER}})
wmod, b = board(packed, 1920, 1080)
off = render(b)
allocs = tiles_of(b)
check("two switched-on tiles are laid out as a pair, not a sparse grid",
      len(allocs) == 2 and allocs[0].y == allocs[1].y, [(a.x, a.y) for a in allocs])
check("...still ending flush against the pinned column",
      abs((b._col.get_allocation().x - wmod.BOARD_GAP)
          - (allocs[-1].x + allocs[-1].width)) <= 1)
off.destroy()
b.destroy()
shutil.rmtree(packed, ignore_errors=True)

# The pinned column overflowed EVERY panel below 1920 on a day with six or more
# events: the 22px gap between the two cards was real height that the row
# budget never counted, so the bottom of the calendar card was cut off the
# screen. The empty-store and one-event cases above cannot see it.
SIX = time.localtime(time.mktime((2026, 8, 15, 12, 0, 0, 0, 1, -1)))
for weeks, fake in (("a five-week month", None), ("a six-week month", SIX)):
    time.localtime = (lambda *a: fake) if fake else _real_localtime
    now = time.localtime()
    iso = "%04d-%02d-%02d" % (now.tm_year, now.tm_mon, now.tm_mday)
    worst = None
    try:
        for ntask, nev in ((0, 6), (4, 6), (20, 12), (20, 0), (0, 0)):
            packed = fresh_home()
            put(packed, "tasks.json", [{"text": "A task with a long name %d" % i,
                                        "done": False} for i in range(ntask)])
            put(packed, "calendar.json", [{"title": "Event %d" % i, "date": iso,
                                           "start": 9 + i * 0.5}
                                          for i in range(nev)])
            for W, H in PANELS:
                wmod, b = board(packed, W, H)
                col = b._col
                b.get_child().remove(col)
                off = Gtk.OffscreenWindow()
                off.set_size_request(b._col_w, b._avail_h)
                off.add(col)
                off.show_all()
                pump()
                slack = b._avail_h - col.get_preferred_height_for_width(
                    b._col_w)[0]
                if worst is None or slack < worst[0]:
                    worst = (slack, "%dx%d, %d tasks, %d events"
                             % (W, H, ntask, nev))
                off.destroy()
                b.destroy()
            shutil.rmtree(packed, ignore_errors=True)
    finally:
        time.localtime = _real_localtime
    check("the pinned column fits a packed day in %s" % weeks, worst[0] >= 0,
          "%dpx short at %s" % (-worst[0], worst[1]))

# A CSS blob is a BYTES literal. One non-ASCII character in it — a curly
# apostrophe pasted into a comment is enough — makes load_from_data fail and
# takes the WHOLE stylesheet with it, which on the board means unstyled cards
# on a black desktop.
import ast                                                       # noqa: E402
_DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
for _src in ("widgets.py", "widgetsettings.py"):
    _path = os.path.join(_DE, _src)
    _blobs = [n.value for n in ast.walk(ast.parse(open(_path,
                                                       encoding="utf-8").read()))
              if isinstance(n, ast.Constant) and isinstance(n.value, bytes)]
    _bad = [b for b in _blobs if any(ch > 127 for ch in b)]
    check("every CSS blob in %s is pure ASCII" % _src,
          _blobs and not _bad, "%d blob(s), %d bad" % (len(_blobs), len(_bad)))

# Every tile has written empty-state copy, and it names the app the tile opens
# so the sentence is something a person can act on.
import widgets as _wm                                            # noqa: E402
check("every tile has an empty state written for it",
      sorted(_wm.TILE_EMPTY) == sorted(_wm.TILE_ORDER), sorted(_wm.TILE_EMPTY))
check("...and each one says what to do about it",
      all(_wm.TILE_APP_NAME[t] in _wm.TILE_EMPTY[t][1]
          for t in _wm.TILE_ORDER),
      {t: _wm.TILE_EMPTY[t][1] for t in _wm.TILE_ORDER})

# "Next class" that only looks at today and tomorrow goes blank all weekend —
# exactly when someone wants to know what Monday holds.
sat = fresh_home()
put(sat, "academics.json",
    {"classes": [{"name": "Linear Algebra", "room": "Hall 2",
                  "meets": [{"day": 0, "start": "09:00"}]}],
     "homework": []})
SAT = time.localtime(time.mktime((2026, 8, 15, 12, 0, 0, 0, 1, -1)))   # Saturday
time.localtime = lambda *a: SAT
try:
    wmod, b = board(sat, 1366, 768)
    words, empty = tile_words(b, "academics")
    check("the next class is found later in the week, not just tomorrow",
          not empty and "Linear Algebra" in words and "09:00" in words, words)
    check("...and it is named by its weekday",
          wmod._t("Monday") in words, words)
    b.destroy()
finally:
    time.localtime = _real_localtime
shutil.rmtree(sat, ignore_errors=True)
shutil.rmtree(home, ignore_errors=True)


print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
