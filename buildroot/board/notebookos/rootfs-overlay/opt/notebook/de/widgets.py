#!/usr/bin/env python3
"""
widgets.py -- the desktop board: what the desktop shows when nothing is open.

The board fills the desktop under the floating Finder as a TWO-ROW grid that
covers the whole area, with nothing clustered in a corner and no hole in it:

    +--------+--------+--------+----------+----------+
    | Classes|Homework|  Meals |          |          |
    +--------+--------+--------+ Calendar |  Tasks   |
    | Workout|Journal |Accounts|          |          |
    +--------+--------+--------+----------+----------+

  * SIX app tiles, all exactly the same size, in a 3x2 grid on the left.
  * Tasks -- a checklist with an "N/M done" read-out -- pinned to the last
    column over the area of TWO tiles (one column wide, two rows tall).
  * Calendar -- the month grid (today circled in signage red) plus a TODAY
    agenda -- pinned beside it, over the same two-tile area.

Why the calendar is two cells and not one: six tiles plus Tasks (two cells)
plus a one-cell calendar is NINE cells, and nine cells cannot tile a two-row
grid -- one cell would be left empty, and a hole in the board reads as the
desktop being broken. Giving the calendar the column above it costs nothing
(the month grid and the agenda both want the height) and leaves the grid
completely filled. See tools/board_selftest.py, which pins this geometry.

EVERY card on this board -- the six tiles and the two pinned cards alike --
is the same object: a `.card` with a `.chead` header (a `.ctitle` on the left,
a `.cmeta` summary on the right) over a body of CONTENT ROWS. A row is
[lead][name..........][value]: the lead is a time, a meal name or a drawn
checkbox, the value a figure, a due date or -- in Workout -- a run of drawn
dots. That one shape is what makes six unrelated apps read as one board.

Which tiles are on lives in ONE place -- Widget Settings, opened from the
board's own right-click menu (see widgetsettings.py, the only writer of
widgets.json) -- not in a switch buried in each app.

NOTHING HERE SCROLLS. Anything that does not fit is off the screen for good, so
every card caps its rows against the REAL panel height (measured `_*_PX`
constants, see tools/measure_widget_rows.py and board_selftest's calibration
section) and never lays out a row it has no room for.

Every card shows REAL data read from the store its app writes -- nothing is
seeded or fabricated, and a store that is missing, truncated or the wrong shape
costs a card its CONTENT (it falls back to written empty-state copy), never its
place on the board. A tile reader must be checked against the app that OWNS the
store, not against what the tile wishes were in it: three of them were once
written to invented keys ("transactions"/"amount", an ISO "date", a course
name) and every one of them showed its empty state forever on a real machine
while every test passed.

Design language: Nimbus Sans for the interface and card titles, a warm serif
(Newsreader / Liberation Serif) for the agenda's event titles -- the one
editorial moment -- signage red #C8341E for exactly one thing on this screen
(today's date in the month grid), papertone surfaces, near-black hairline
frames (matching the Finder).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gio, Pango, PangoCairo  # noqa: E402

import os
import json
import time
import datetime
import subprocess

import nbapp  # shared base: nbapp.screen_size() gives the REAL primary-monitor
# The shared motion engine, for the board's settle-in (PAPER-PHYSICS G1).
# Never fatal: a desktop that cannot animate still shows its cards.
try:
    import nbmotion
except Exception:                                                 # noqa: BLE001
    nbmotion = None
from nbi18n import _t  # noqa: E402
              # size (never a hardcoded 1920x1080) for sizing this board.

# cairo draws the task checkbox and the Workout set dots as flat vectors (see
# _Check / _Dots). Guarded so a construction on a stripped image can never
# hard-fail on the import; the checkbox degrades to a plain box drawn without
# round caps and the dots simply do not paint.
try:
    import cairo
    _CAP_ROUND = cairo.LINE_CAP_ROUND
    _JOIN_ROUND = cairo.LINE_JOIN_ROUND
except Exception:      # pragma: no cover - cairo is present on the real image
    cairo = None
    _CAP_ROUND = _JOIN_ROUND = None

# palette (see the docstring): papertone surfaces, near-black structural ink,
# muted grey. Signage red is reserved for today + alerts and lives in the CSS.
_PAPER = (0xF8 / 255.0, 0xF7 / 255.0, 0xF2 / 255.0)
_INK = (0x1A / 255.0, 0x19 / 255.0, 0x16 / 255.0)
_GREY = (0x9A / 255.0, 0x95 / 255.0, 0x8A / 255.0)
# the muted green a met goal is marked in (matching language.py's streak line).
# NOT signage red: red means exactly one thing on this screen, today's date.
_GOOD = (0x4F / 255.0, 0x7A / 255.0, 0x3A / 255.0)

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# the DE scripts live beside this file; the Calendar app is launched the same
# way the rest of the desktop spawns apps -- python3 <DE_DIR>/calendar.py with
# PYTHONPATH pinned to DE_DIR (see music.py / contacts.py).
DE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
TASKS_FILE = os.path.join(CFG_DIR, "tasks.json")        # shared flat task list
WORKOUT_FILE = os.path.join(CFG_DIR, "workout.json")    # Workout app
CAL_FILE = os.path.join(CFG_DIR, "calendar.json")       # Calendar app's events
BOARD_FILE = os.path.join(CFG_DIR, "widgets.json")      # which tiles are on
ACADEMICS_FILE = os.path.join(CFG_DIR, "academics.json")  # classes + homework
# The same store under the name it had when the app was called Academic Notes.
# The app itself reads this as a fallback (academics.LEGACY_FILE); the board has
# to as well, or on a machine that upgraded, the Classes and Homework tiles sit
# there showing "No classes" over a term the user can plainly see in the app.
ACADEMICS_LEGACY = os.path.join(CFG_DIR, "academic.json")
JOURNAL_FILE = os.path.join(CFG_DIR, "journal.json")
ACCOUNTING_FILE = os.path.join(CFG_DIR, "accounting.json")
MEALS_FILE = os.path.join(CFG_DIR, "mealplanner.json")
BILLS_FILE = os.path.join(CFG_DIR, "bills.json")           # Bill Tracker
CONTACTS_FILE = os.path.join(CFG_DIR, "contacts.json")     # Contacts
EBOOK_FILE = os.path.join(CFG_DIR, "ebook.json")           # E-book Reader
LANGUAGE_FILE = os.path.join(CFG_DIR, "language.json")     # Language
NOVEL_FILE = os.path.join(CFG_DIR, "novel.json")           # Novel

# The app tiles, in the order they are laid out (left to right, top to bottom)
# and the order Widget Settings lists them. `mod` is the app a click opens.
# Tasks and the calendar are NOT here: they are pinned to the board and cannot
# be switched off, because they are the two things the desktop is for.
#
# THERE ARE ELEVEN OF THESE AND SIX SLOTS, and the difference is the point.
# The grid stays 3x2 -- a fourth tile column takes every card on the board from
# 454px wide to 359px (230 to 180 on a 1024 panel, where a payee no longer fits
# on a row), and tiles left over in a fixed grid leave holes in it. What the
# desktop is FOR differs from person to person, though, so the answer is not a
# bigger board but a real choice of what goes on it: six of eleven, picked in
# Widget Settings, which draws the board as the switches are flipped.
#
# The first six are what a new machine ships with (see TILE_DEFAULT_ON); the
# rest are there to be chosen.
TILE_ORDER = ("academics", "homework", "meals",
              "workout", "journal", "bills",
              "accounting", "birthdays", "reading", "language", "novel")
TILE_APP = {"academics": "academics", "homework": "academics",
            "meals": "mealplanner", "workout": "workout",
            "journal": "journal", "bills": "bills",
            "accounting": "accounting", "birthdays": "contacts",
            "reading": "ebook", "language": "language", "novel": "novel"}
# The view the app should open ON, so clicking the Homework tile lands on
# Homework instead of dropping you in the app to go and find it.
TILE_ARG = {"academics": "schedule", "homework": "homework"}
# The app a tile belongs to, for its tooltip. NOT the tile's own title: a tile
# called "Classes" opens Academics, and "Open Classes" names something that is
# not a thing you can open.
TILE_APP_NAME = {"academics": "Academics", "homework": "Academics",
                 "meals": "Meal Planner", "workout": "Workout",
                 "journal": "Journal", "bills": "Bill Tracker",
                 "accounting": "Accounting", "birthdays": "Contacts",
                 "reading": "E-book Reader", "language": "Language",
                 "novel": "Novel"}
# English source strings; nbi18n translates them at _t() like any other label.
TILE_TITLE = {"academics": "Classes", "homework": "Homework",
              "meals": "Meals", "workout": "Workout",
              "journal": "Journal", "bills": "Bills",
              "accounting": "Accounting", "birthdays": "Birthdays",
              "reading": "Reading", "language": "Language",
              "novel": "Novel"}
# SIX ON, THE REST OFF, AND THE GRID IS EXACTLY FULL. Not "every tile on": the
# board has six slots, so shipping more than six switched on would leave some
# of them undrawable, and shipping fewer leaves a hole in a fixed grid.
#
# Which six is a judgement about a machine nobody has used yet, so it is the
# six that need no setting up to say something true: a term timetable, what is
# owed, today's meals, today's sets, whether today has been written. Accounting
# is held back because Bills is on -- same subject, but only one of the two has
# a DEADLINE, and a reminder tile is for what has to be done and by when. The
# other four are chosen rather than shipped because they are worth a tile only
# to someone who uses that app: a reader, a learner, a novelist, someone who
# keeps birthdays. Widget Settings draws the board while the switches are
# flipped, so swapping any of it is one click and no guesswork.
SHIPPED_TILES = ("academics", "homework", "meals",
                 "workout", "journal", "bills")
TILE_DEFAULT_ON = {tid: tid in SHIPPED_TILES for tid in TILE_ORDER}


def board_order(data):
    """The order the tiles sit in, out of a parsed widgets.json.

    Shared with Widget Settings so the writer and the reader of the store can
    never disagree about what a stored order means. Anything the file does not
    account for keeps its default position rather than vanishing: an order that
    dropped a tile would take that tile off the desktop with no switch ever
    having been touched, and nothing on screen would say why."""
    seen, out = set(), []
    stored = data.get("order") if isinstance(data, dict) else None
    if isinstance(stored, list):
        for tid in stored:
            if tid in TILE_DEFAULT_ON and tid not in seen:
                seen.add(tid)
                out.append(tid)
    for tid in TILE_ORDER:
        if tid not in seen:
            out.append(tid)
    return out


def adopt_bills(on, order):
    """A board laid out before the Bill Tracker existed, brought forward.

    The grid holds six tiles and there are now seven. Left alone, an existing
    widgets.json names the old six, `bills` is appended after them, and the
    seventh position is never drawn -- so installing the app would appear to
    have added nothing to the desktop at all, with no switch off and nothing on
    screen saying why. That is the exact failure the whole board is written to
    avoid.

    So Bills takes the slot Accounting held and Accounting moves to seventh,
    switched off. Nothing else on the board moves. This is the same choice
    TILE_DEFAULT_ON makes for a new machine, applied once to an old one, so the
    two kinds of machine do not end up with different desktops."""
    on = dict(on)
    on["bills"] = True
    on["accounting"] = False
    order = [tid for tid in order if tid != "bills"]
    if "accounting" in order:
        order.insert(order.index("accounting"), "bills")
    else:
        order.append("bills")
    return on, board_order({"order": order})


def board_state(data):
    """(which tiles are on, the order they sit in) out of a parsed widgets.json.

    ONE reading of the file, shared by the desktop that draws the board and the
    Widget Settings screen that writes it -- for the reason board_order exists:
    they are separate processes that never talk, and a disagreement about what
    the file means takes a tile off the desktop with nothing on screen saying
    why. Never raises; anything the file does not account for keeps its
    default."""
    on = dict(TILE_DEFAULT_ON)
    tiles = data.get("tiles") if isinstance(data, dict) else None
    if isinstance(tiles, dict):
        for tid in TILE_ORDER:
            if tid in tiles:
                on[tid] = bool(tiles[tid])
    order = board_order(data)
    # A store that names tiles but has never heard of this one was written by
    # an older build. A store with no tiles section at all is not an upgrade,
    # it is a first run, and the defaults above are already right for it.
    if isinstance(tiles, dict) and "bills" not in tiles:
        on, order = adopt_bills(on, order)
    return on, order
# Cards with a FIXED, small number of rows (today's three meals; today's
# classes). Their rows expand to share the card, and are set a size larger,
# instead of huddling at the top under a block of blank paper.
FILL_TILES = ("meals",)
# What a card says when its reader has nothing to report. Two lines in the same
# shape as a card with data -- the state, then where the data is entered, naming
# the app the tile opens (clicking the tile opens it). They live together here,
# rather than one per reader, so the empty board can be read at once; a reader
# with nothing to say returns None and gets these.
TILE_EMPTY = {
    "academics": ("No classes", "Add classes in Academics"),
    "homework": ("No assignments", "Add assignments in Academics"),
    "meals": ("No meals planned", "Plan meals in Meal Planner"),
    "workout": ("No exercises", "Add exercises in Workout"),
    "journal": ("No entries", "Write entries in Journal"),
    "bills": ("No bills", "Add bills in Bill Tracker"),
    "accounting": ("No entries", "Add entries in Accounting"),
    "birthdays": ("No birthdays", "Add birthdays in Contacts"),
    "reading": ("No books", "Add books in E-book Reader"),
    "language": ("No course started", "Start a course in Language"),
    "novel": ("No chapters", "Write chapters in Novel"),
}
# How many tiles the board can draw at once. One number, exported, because the
# board, Widget Settings and both their selftests all have to agree about it --
# and with more tiles than slots, "how many fit" stopped being the same
# question as "how many are there".
def slot_count():
    return TILE_COLS * TILE_ROWS
# the desktop-home board belongs to the desktop, not on top of a running app.
# A launcher drops this flag file while a fullscreen app owns the screen; we
# hide while it exists and reappear when the desktop home returns.
APP_FLAG = nbapp.APP_FLAG
# The ref-count dir nbapp writes one file per live app pid into. Checking it
# directly (is any pid still in /proc) is more reliable than trusting the flag
# file, which can be left stale by a crashed app or briefly missing.
APP_DIR = nbapp.APP_DIR

PANEL_H = 46
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
# Monday-first abbreviations for the card sub-header, formatted by index so we
# never touch strftime("%-d") (a glibc-only flag) or the stdlib-shadowed
# calendar module.
WD_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Monday-first, matching academics.DAY_NAMES (and the same source strings, so
# the translations the catalogs already carry for the Academics app are the
# ones the Classes tile uses).
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
# mealplanner.MEALS / MEAL_NAMES, restated so a tile can name a meal without
# importing the app just to read a label (the plan itself IS read through
# mealplanner.read_plan, which owns the parsing).
MEAL_KEYS = ("breakfast", "lunch", "dinner")
MEAL_NAMES = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}

# -- the board ---------------------------------------------------------------
BOARD_MARGIN = 18     # board inset from the screen edges
BOARD_GAP = 22        # between every card on the board
# FOUR columns, not five: Tasks and the calendar now share ONE column, stacked
# (Tasks on top, calendar beneath it), instead of standing side by side. That
# hands a whole column back to the six tiles, so every card on the board is
# wider, and the extra gap above has room to breathe.
GRID_COLS = 4         # 3 tile columns + one column holding Tasks + calendar
GRID_ROWS = 2
TILE_COLS = 3         # the tile grid is 3 wide...
TILE_ROWS = 2         # ...and 2 tall, which is exactly TILE_ORDER

# These cards are summaries on a FIXED-height board, not the full apps. Cap how
# many rows each renders so a long list can never push a card off the bottom of
# the screen (nothing here scrolls). When a list is longer than the cap the
# final row becomes a muted "+N more" read-out; the complete list stays in the
# app. The cap counts that read-out row, so the rendered height is bounded
# either way.
# How long a burst of store-change events is collected before the board is
# rebuilt (see _queue_reload). Long enough to swallow the several events one
# atomic save produces, short enough that a change made in an app still appears
# on the desktop as it is switched back to.
_RELOAD_COALESCE_MS = 180

MAX_TASK_ROWS = 14
MAX_AGENDA_ROWS = 8
MAX_TILE_ROWS = 10

# How wide a content row's VALUE cell may ask to be, in characters.
#
# IT MUST NOT BE 1, and this was a shipped bug for as long as any tile had
# something to put there: the Homework card's due column rendered as three bare
# ellipses on the desktop, because an ellipsizing GtkLabel with
# max_width_chars(1) reports one character as its NATURAL width -- and a
# pack_end child with expand=False is given exactly its natural width, so the
# one character it got was the ellipsis. (max_width_chars(1) is right for the
# NAME beside it, which is packed expand=True and only needs its runaway
# natural width pinned down.) _card_shell learned this on the header summary
# and left the same trap in the rows underneath it.
#
# 18 fits every value any tile produces -- "in 12 days", "Post in 3 days",
# "$1,180.00" -- with room for a longer translation, while still stopping a
# nonsense value from pushing the name off its own row.
_VALUE_CHARS = 18

# EVERY figure below is MEASURED, not estimated. Two tools keep them honest:
#
#   DISPLAY=:0 PYTHONPATH=tools:<de> python3 tools/measure_widget_rows.py
#       measures the pinned pair's parts against the live column and must
#       print "all constants track the real widgets";
#   DISPLAY=:0 PYTHONPATH=<de>       python3 tools/board_selftest.py
#       measures the TILE card's parts (head, row, tail, empty block) against
#       a real rendered tile, which the first tool does not see.
#
# Both directions of drift are bugs. Under-counting hands the budget space the
# cards are really using and clips the bottom of a card off the screen;
# over-counting is what once left a 768px panel refusing to grant rows it had
# 100px of room for.
_HEAD_PX = 42         # a card header (.chead: 11+11 padding + title + rule)
_TASK_ROW_PX = 31     # a .taskrow (5+5 padding + the 21px checkbox)
_MORE_ROW_PX = 34     # the quieter "+N more" tail row (.moretail)
_AGENDA_ROW_PX = 33   # an .agrow (6+6 padding + the 18px serif title)
_CROW_PX = 29         # one content row of a tile (.crow)
_CTA_PX = 56          # the two-line call-to-action block (.emptyrow)
# A card with nothing in it still shows its empty state, so an EMPTY card costs
# height too -- and costs MORE than a row, because it is two lines (the state
# plus the line telling you what to do about it).
_TASK_EMPTY_PX = 56   # the .emptyrow "Nothing to do yet" block
_AGENDA_EMPTY_PX = 30  # the .agempty "No events" line
_GRID_WD_PX = 12      # the weekday header row of the month grid
_GRID_ROW_PX = 22     # one week of the grid (the 22px day cell)
_GRID_PAD_PX = 10     # .calgrid vertical padding
_AGSEC_PX = 29        # the "TODAY" section label (+ its rule and padding)
_BODY_PAD_PX = 10     # .cbody / .tasklist vertical padding
_CARD_BORDER_PX = 2   # the card's own 1px hairline, top and bottom
# The gap between the calendar and Tasks. It is the pinned pair's Box spacing,
# so measure_widget_rows can read it back off the live widget; it is BOARD_GAP
# because every gap on this board is the same gap.
_COL_SPACING_PX = BOARD_GAP

# A row is stretched to fill its card, but never past this: a card holding
# three meals must not render them as three 150px bands. Above it the leftover
# collects as blank paper at the foot of the card, which is what a card with
# little to say should look like.
_ROW_TARGET_PX = 46
# ...and the month grid is stretched the same way, up to a comfortable week
# row. Beyond that the calendar card would be one enormous month and nothing
# else, which is not what the card is for.
_GRID_TARGET_PX = 46


WIDGETS_CSS = b"""
/* fill the whole board window with the desktop papertone: with no compositor
   a transparent window paints black in the gaps between/below the cards. */
.wcol { background: #DED4C2; }
/* the flat-Swiss card: warm paper on a near-black hairline frame, matching the
   Finder window's frame (de/finder.py). The near-black is a structural frame,
   never a decorative accent; signage red is reserved for today + alerts.
   EVERY card on this board is this card -- the six tiles and the pinned pair
   alike -- which is the whole reason the board reads as one thing. */
/* The desktop's own right-click menu. It carried this class for a long time
   with no rule anywhere to match it, so the one menu the desktop owns rendered
   in stock GTK grey on a board that is otherwise entirely papertone. Built to
   the same frame as a card: warm paper, a near-black hairline, no rounding. */
.boardmenu, .boardmenu menu { background: #F8F7F2; border: 1px solid #1A1916;
    border-radius: 0; padding: 5px 0; }
.boardmenu menuitem { padding: 7px 18px; min-height: 22px; }
.boardmenu menuitem label { font-family: "Nimbus Sans","Helvetica",sans-serif;
    font-size: 14px; color: #1A1916; }
/* A hovered row is painted by the theme as a background-IMAGE, so a colour-only
   rule here would leave it Adwaita blue. */
.boardmenu menuitem:hover { background-image: none; background: #EFEBE0; }
.boardmenu menuitem:hover label { color: #1A1916; }
.boardmenu separator { background: #D7D2C5; margin: 4px 0; min-height: 1px; }

.boardhit { padding: 0; margin: 0; border: none;
    background: transparent; background-image: none; box-shadow: none;
    min-width: 0; min-height: 0; }

/* EACH CARD IS ITS OWN OBJECT ON THE DESK, and casts its own shadow.
   The board is one window, so the compositor could only ever shadow the WHOLE
   grid -- one soft rectangle around all eight cards, which read as a single
   slab laid over the desktop rather than as eight separate sheets on it.
   picom.conf now excludes this window by name (it is desktop furniture, like
   the panel and the backdrop, not a floating window) and the elevation is
   carried here instead, per card.
   Two layers, the same language as Papertone's tooltips and menus: a tight
   contact shadow plus a wide soft one. Weaker than either of those on purpose
   -- a menu floats above a window, a card only rests on the desk, and it is
   the lowest thing on the screen that is an object at all. */
.card { background: #F8F7F2; border: 1px solid #1A1916;
        box-shadow: 0 1px 2px rgba(26, 25, 22, 0.08),
                    0 4px 12px rgba(26, 25, 22, 0.10); }
.card .chead { padding: 11px 14px; border-bottom: 1px solid #1A1916; }
.ctitle { font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 15px;
          font-weight: 700; letter-spacing: 0.02em; color: #1A1916; }
.cmeta  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 12px; color: #6E695E; }
/* the one card whose summary IS the fact (Accounting's cash balance) states it
   in ink rather than in the quiet grey the other summaries use. */
.cmeta.strong { color: #1A1916; font-weight: 700; font-size: 13px; }
/* the body of content rows every card shares. */
.cbody { padding: 3px 14px 7px 14px; }
/* one content row: [lead][name..........][value], on a hairline so a card of
   them reads as a chart rather than as a paragraph of scraps. */
.crow  { padding: 6px 0; border-bottom: 1px solid #D7D2C5; }
.crow:last-child { border-bottom: 0; }
.clead { font-family: "Nimbus Sans","Helvetica",sans-serif;
         font-size: 12px; font-weight: 600; color: #1A1916; }
.cname { font-family: "Nimbus Sans","Helvetica",sans-serif;
         font-size: 13px; color: #2A2620; }
/* a goal that has been met -- every set logged, an assignment handed in. A
   muted green, NOT the signage red, which means today's date and nothing
   else anywhere on this screen. */
.cname.hit { color: #4F7A3A; }
.cval  { font-family: "Nimbus Sans","Helvetica",sans-serif;
         font-size: 12px; color: #6E695E; }
.cval.hit { color: #4F7A3A; }
/* ...and the opposite of a met goal: a deadline that has arrived or gone.
   The signage red is otherwise reserved on this board for TODAY in the
   calendar, which is the same kind of statement -- this is the day it is
   about. A tile that painted every row red would be saying nothing, so only
   a bill actually needing action gets it (see _read_bills). */
.cval.alert { color: #C8341E; font-weight: 700; }
/* the Journal card's single answer */
.jmark { font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 15px; }
.jmark.done { color: #4F7A3A; }
.jmark.todo { color: #C8341E; }
.jdate { font-family: "Nimbus Sans","Helvetica",sans-serif;
         font-size: 12px; color: #6E695E; }
/* A fill card (today's three meals) has only a few rows and the whole tile to
   put them in, so it is set larger -- at the shared 13px those three lines sat
   in the top third of the card and the rest read as a tile that failed to
   draw. The lead is wider too: at the shared 46px "Breakfast" came out as
   "Brea...", which is a strange thing for a card to call a meal. */
.crow.fill { padding: 10px 0; }
.crow.fill .clead { font-size: 14px; }
.crow.fill .cname { font-size: 16px; }
.crow.fill .cval  { font-size: 13px; }
.tasklist { padding: 3px 14px 7px 14px; }
/* each row is a whole-width clickable surface (a GtkEventBox). It carries an
   OPAQUE papertone background: a windowed EventBox left transparent can paint
   black on the no-compositor framebuffer, so we paint the paper explicitly.
   The row's PADDING lives on .taskrowbody, the plain Box inside it: GTK3's
   GtkEventBox draws a background and border from CSS but does not add padding
   to its size request, so setting it here gave a 21px row squeezed against its
   own hairline while the "+N more" line below (a Box, which does honour it)
   stood a full 48px tall. */
.taskrow  { border-bottom: 1px solid #D7D2C5; background: #F8F7F2; }
.taskrow:last-child { border-bottom: 0; }
.taskrow:focus { outline: 2px solid #1A1916; outline-offset: -2px; }
.taskrowbody { padding: 5px 0; }
/* the "+N more" tail: a quieter line than a task row, not a full-height one */
.moretail { padding: 10px 0; }
.emptyrow { padding: 12px 0; }
/* 13px matches .cname and .calday: the cards sit on one board and a one-pixel
   difference between their list text reads as a mistake. */
.tasktext { font-family: "Nimbus Sans","Helvetica",sans-serif;
            font-size: 13px; color: #2A2620; }
.tasktext.done { color: #9A9484; }
.emptytext { font-family: "Nimbus Sans","Helvetica",sans-serif;
             font-size: 14px; color: #6E695E; }
/* the one line under an empty card's heading that says what to do about it:
   quieter than the state above it, so it reads as guidance, not as content. */
.emptyhint { font-family: "Nimbus Sans","Helvetica",sans-serif;
             font-size: 12px; color: #9A9484; }
/* the "+N more" overflow read-out shown when a card is longer than its cap. */
.moretext { font-family: "Nimbus Sans","Helvetica",sans-serif;
            font-size: 12px; color: #6E695E; }
.calgrid { padding: 4px 12px 6px 12px; }
.calwd  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 10px; font-weight: 600; color: #6E695E; letter-spacing: 0.06em; }
/* min-width and min-height must stay EQUAL: today's marker is a 50% radius on
   this box, so an uneven pair turns the circle into an ellipse. */
.calday { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 12px; color: #2A2620; min-width: 22px; min-height: 22px; }
/* a day that carries an event is bold ink, NOT signage red -- red is reserved
   for today (see the docstring). This matches the Tasks app's mini-calendar,
   where a day with an event is bold ink, and unlike a dot underneath the
   number it costs the week row no height at all. */
.calday.hasev { font-weight: 700; color: #1A1916; }
.calday.today { background: #C8341E; color: #FCFBF8; border-radius: 50%;
                font-weight: 700; }
.agsec  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 11px; color: #6E695E; letter-spacing: 0.14em;
          padding: 12px 14px 4px 14px; border-top: 1px solid #D7D2C5; }
.agrow  { padding: 6px 14px; }
.agtime { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 12px; font-weight: 600; color: #1A1916; }
.agtext { font-family: "Newsreader","Liberation Serif","Georgia",serif;
          font-size: 17px; color: #2A2620; }
.agempty { font-family: "Nimbus Sans","Helvetica",sans-serif;
           font-size: 14px; color: #6E695E; padding: 6px 14px 8px 14px; }
"""


def _css():
    prov = Gtk.CssProvider()
    prov.load_from_data(WIDGETS_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _minutes(hhmm):
    """"HH:MM" -> minutes since midnight, or None if it is not a time."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
    except (AttributeError, TypeError, ValueError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def _fmt_time(hhmm):
    mins = _minutes(hhmm)
    return "" if mins is None else "%02d:%02d" % (mins // 60, mins % 60)


def _classes_for_day(classes, weekday):
    """Clean one weekday's meetings into drawable schedule records."""
    out = []
    for cls in classes if isinstance(classes, list) else []:
        if not isinstance(cls, dict):
            continue
        name = cls.get("name") or cls.get("label")
        if not name:
            continue
        meets = cls.get("meets") if isinstance(cls.get("meets"), list) else []
        for meet in meets:
            if not isinstance(meet, dict) or meet.get("day") != weekday:
                continue
            start = _minutes(meet.get("start"))
            end = _minutes(meet.get("end"))
            if start is None:
                continue
            if end is None or end <= start:
                end = min(24 * 60, start + 60)
            out.append({"start": start, "end": end, "name": str(name),
                        "room": str(meet.get("room") or cls.get("room") or "")})
    return sorted(out, key=lambda event: (event["start"], event["end"],
                                          event["name"]))


def _classes_window(events):
    """Whole-hour span with one hour of context around today's classes."""
    if not events:
        return None
    first = min(event["start"] for event in events)
    last = max(event["end"] for event in events)
    return max(0, (first // 60 - 1) * 60), min(
        24 * 60, ((last + 59) // 60 + 1) * 60)


def _classes_block_geometry(start, end, window_start, window_end, height):
    """Proportional (top, height) for a meeting in a bounded time window."""
    span = max(1.0, float(window_end - window_start))
    top = (max(window_start, start) - window_start) * float(height) / span
    bottom = (min(window_end, end) - window_start) * float(height) / span
    return top, max(1.0, bottom - top)


def _classes_collision_lanes(events):
    """Return copies carrying lane/lane_count for each overlapping run."""
    result = []
    run = []
    run_end = None

    def flush():
        lanes = []
        placed = []
        for event in run:
            for lane, free in enumerate(lanes):
                if event["start"] >= free:
                    lanes[lane] = event["end"]
                    placed.append((event, lane))
                    break
            else:
                lanes.append(event["end"])
                placed.append((event, len(lanes) - 1))
        for event, lane in placed:
            item = dict(event)
            item.update({"lane": lane, "lane_count": len(lanes)})
            result.append(item)

    for event in sorted(events, key=lambda item: (item["start"], item["end"])):
        if run and event["start"] >= run_end:
            flush()
            run = []
            run_end = None
        run.append(event)
        run_end = event["end"] if run_end is None else max(run_end, event["end"])
    if run:
        flush()
    return result


def _classes_now_position(now_minutes, window_start, window_end, school_day,
                          height):
    """Y for the now rule, or None outside a populated school-day window."""
    if not school_day or not window_start <= now_minutes <= window_end:
        return None
    return ((now_minutes - window_start) * float(height) /
            max(1.0, float(window_end - window_start)))


def _classes_text_layout(cr, text, size, bold=False):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription("Nimbus Sans")
    font.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    font.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_text(str(text), -1)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    return layout


def _classes_show_text(cr, x, baseline, text, size, width, bold=False):
    """Classes-tile Pango baseline helper; never use cairo's toy text API."""
    if width <= 2:
        return
    layout = _classes_text_layout(cr, text, size, bold)
    layout.set_width(int(width * Pango.SCALE))
    cr.move_to(x, baseline - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)


def _fmt_money(total):
    """A balance formatted the way the Accounting app itself formats one:
    thousands separated, a real Unicode minus, and the currency sign. The two
    are read side by side on the same screen, so they must not disagree about
    what money looks like. See accounting._money."""
    try:
        if total != total or total in (float("inf"), float("-inf")):
            total = 0.0
        cents = round(abs(total), 2)
    except (TypeError, ValueError, OverflowError):
        total, cents = 0.0, 0.0
    # The sign comes from the ROUNDED magnitude, so a sub-cent negative
    # remainder reads "$0.00" and never "-$0.00".
    sign = "−" if (total < 0 and cents != 0) else ""
    return "%s$%s" % (sign, format(cents, ",.2f"))


def _month_weeks(year, month):
    """Weeks (Mon-first) of `month` as lists of 7 ints/None."""
    try:
        first = datetime.date(year, month, 1)
        nxt = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
        lead, ndays = first.weekday(), (nxt - first).days
    except Exception:
        # A wildly out-of-range system clock (e.g. a dead RTC reporting
        # year 9999, which pushes date() past MAXYEAR) must never blank the
        # desktop board; fall back to a plain 30-day, Monday-start grid --
        # mirrors tasks.py's mini-calendar hardening.
        lead, ndays = 0, 30
    cells = [None] * lead + list(range(1, ndays + 1))
    while len(cells) % 7:
        cells.append(None)
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


class _Check(Gtk.DrawingArea):
    """A checkbox, drawn with cairo instead of a native Gtk.CheckButton.

    On the GPU-less software framebuffer a themed check indicator can paint
    blank, garbage or at the wrong size when a theme's assets are missing; a
    cairo box is deterministic and matches the flat-Swiss design exactly -- a
    21px square: a grey hairline outline when open, ink-filled (#1A1916) with a
    white tick when done. It draws from the LIVE allocation (never a hardcoded
    size), no-ops on a not-yet-allocated 0x0 area, paints an opaque paper base
    so no stray pixel shows through on the framebuffer, and only repaints when
    its state actually flips (queue_draw is never called on a timer).

    Used by the Tasks card, where a click toggles it, and by the Homework tile,
    where it is a read-only mark -- the two lists are the same list of things
    to get done, so they are drawn with the same object."""

    SIZE = 21

    def __init__(self, done, size=None):
        super().__init__()
        self._done = bool(done)
        self._size = int(size or self.SIZE)
        self.set_size_request(self._size, self._size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_done(self, done):
        """Flip the tick in place, repainting only this box (not the row)."""
        done = bool(done)
        if done != self._done:
            self._done = done
            self.queue_draw()

    def _draw(self, _area, cr):
        try:
            w = self.get_allocated_width()
            h = self.get_allocated_height()
            if w <= 0 or h <= 0:            # not yet allocated -- nothing to paint
                return False
            # opaque base first, so the corners outside the square never show a
            # black/garbage pixel on the compositor-less framebuffer.
            cr.set_source_rgb(*_PAPER)
            cr.paint()
            # a tight square centred in whatever the row actually allocated us.
            side = min(w, h, self._size)
            inset = 1.0                     # keep the 1.5px stroke inside the box
            bx = (w - side) / 2.0 + inset
            by = (h - side) / 2.0 + inset
            bs = side - 2 * inset
            if bs <= 0:
                return False
            cr.set_line_width(1.5)
            if self._done:
                cr.rectangle(bx, by, bs, bs)
                cr.set_source_rgb(*_INK)
                cr.fill_preserve()
                cr.stroke()
                # white tick -- the design's check path (M5 12.5 L10 17.5 L19 7 on
                # a 24 grid) scaled into a centred sub-region so it sits inside
                # the box at any allocation.
                tsz = bs * 0.62
                tx = bx + (bs - tsz) / 2.0
                ty = by + (bs - tsz) / 2.0
                s = tsz / 24.0
                cr.set_source_rgb(*_PAPER)
                cr.set_line_width(max(2.0, bs * 0.12))
                if _CAP_ROUND is not None:
                    cr.set_line_cap(_CAP_ROUND)
                    cr.set_line_join(_JOIN_ROUND)
                cr.move_to(tx + 5 * s, ty + 12.5 * s)
                cr.line_to(tx + 10 * s, ty + 17.5 * s)
                cr.line_to(tx + 19 * s, ty + 7 * s)
                cr.stroke()
            else:
                cr.rectangle(bx, by, bs, bs)
                cr.set_source_rgb(*_GREY)
                cr.stroke()
        except Exception:
            # a cairo hiccup must never escape the draw handler and blank the row.
            pass
        return False


class _Dots(Gtk.DrawingArea):
    """A run of set dots for one Workout exercise: one dot per set in the day's
    goal, INK-FILLED for each set actually logged and hollow for the rest.

    This is the Workout tile's whole point -- a glance says both how much is
    done and how much is left, which no "3 of 5" can. Drawn rather than
    composed from labels because a row of GtkLabels carrying a bullet character
    depends on a font having that glyph (the shipped face has no emoji at all,
    and a missing glyph is a tofu box), and because the dots have to line up
    exactly under each other down the card."""

    DOT = 9          # dot diameter
    GAP = 5          # between dots
    MAX = 8          # beyond this the run is wider than a tile; see _dots_cell

    def __init__(self, done, goal):
        super().__init__()
        self._goal = max(0, min(self.MAX, int(goal)))
        self._done = max(0, min(self._goal, int(done)))
        w = self._goal * self.DOT + max(0, self._goal - 1) * self.GAP
        self.set_size_request(max(1, w), self.DOT)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def _draw(self, _area, cr):
        try:
            if cairo is None or self._goal <= 0:
                return False
            h = self.get_allocated_height()
            w = self.get_allocated_width()
            if h <= 0 or w <= 0:
                return False
            run = self._goal * self.DOT + max(0, self._goal - 1) * self.GAP
            x0 = w - run                     # right-aligned inside the cell
            cy = h / 2.0
            r = self.DOT / 2.0 - 0.75        # keep the 1.5px stroke inside
            cr.set_line_width(1.5)
            for i in range(self._goal):
                cx = x0 + i * (self.DOT + self.GAP) + self.DOT / 2.0
                cr.arc(cx, cy, max(1.0, r), 0, 6.2831853)
                if i < self._done:
                    cr.set_source_rgb(*_GOOD if self._done >= self._goal
                                      else _INK)
                    cr.fill()
                else:
                    cr.set_source_rgb(*_GREY)
                    cr.stroke()
        except Exception:
            pass
        return False


class _ClassesSchedule(Gtk.DrawingArea):
    """The Calendar day view reduced to one non-scrolling board tile."""

    GUTTER = 43

    def __init__(self, events, window, now_minutes):
        super().__init__()
        self.events = _classes_collision_lanes(events)
        self.window = window
        self.now_minutes = now_minutes
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.connect("draw", self._draw)

    def _draw(self, _area, cr):
        try:
            width = self.get_allocated_width()
            height = self.get_allocated_height()
            if width <= self.GUTTER + 8 or height <= 0:
                return False
            start, end = self.window
            span = max(1, end - start)
            cr.set_line_width(1)
            for minute in range(start, end + 1, 60):
                y = (minute - start) * height / float(span)
                cr.set_source_rgb(0.843, 0.824, 0.773)  # board hairline
                cr.move_to(self.GUTTER, y + 0.5)
                cr.line_to(width, y + 0.5)
                cr.stroke()
                if minute < end:
                    cr.set_source_rgb(*_GREY)
                    _classes_show_text(cr, 0, y + 11,
                                       "%02d:00" % (minute // 60), 9,
                                       self.GUTTER - 7)
            content_w = width - self.GUTTER
            for event in self.events:
                top, block_h = _classes_block_geometry(
                    event["start"], event["end"], start, end, height)
                lanes = max(1, event["lane_count"])
                lane_w = max(1.0, (content_w - 5) / lanes)
                x = self.GUTTER + 3 + event["lane"] * lane_w
                block_w = max(1.0, lane_w - 2)
                # Calendar's quiet colour wash + solid class-colour spine,
                # translated into the board's neutral papertone idiom.
                cr.set_source_rgb(0.902, 0.886, 0.831)
                cr.rectangle(x, top + 1, block_w, max(1, block_h - 2))
                cr.fill()
                cr.set_source_rgb(*_GOOD)
                cr.rectangle(x, top + 1, 3, max(1, block_h - 2))
                cr.fill()
                pad = 7 if block_w >= 70 else 5
                cr.set_source_rgb(*_INK)
                _classes_show_text(cr, x + pad, top + 13, event["name"],
                                   10.5, block_w - pad - 4, True)
                if block_h >= 28 and event["room"]:
                    cr.set_source_rgb(0.431, 0.412, 0.369)
                    _classes_show_text(cr, x + pad, top + 25, event["room"],
                                       9, block_w - pad - 4)
            now_y = _classes_now_position(self.now_minutes, start, end,
                                          bool(self.events), height)
            if now_y is not None:
                cr.set_source_rgb(0.784, 0.204, 0.118)  # board signage red
                cr.set_line_width(1.5)
                cr.move_to(self.GUTTER - 3, now_y)
                cr.line_to(width, now_y)
                cr.stroke()
                cr.arc(self.GUTTER - 3, now_y, 2.5, 0, 6.2831853)
                cr.fill()
        except Exception:
            pass
        return False


class Widgets(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        _css()
        self.set_decorated(False)
        # WM_NAME the window manager keys on: the matchbox patch
        # (0004-desktop-widget-column-below-windows) pins any DIALOG with this
        # title to the very bottom of the stack, just above the wallpaper and
        # below every app and the Finder -- so the cards can NEVER render in
        # front of a window.
        # NOT translated: this is the name the WM patch matches on, not a
        # label anyone reads. A translated title would silently stop matching
        # and let the cards float above real windows.
        self.set_title("nb-desktop-widgets")
        nbapp.force_opaque_visual(self)   # see nbapp: no RGBA visual
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        # ...but PINNED TO THE DESKTOP LAYER. The hide-while-an-app-is-active
        # rule only covers apps that set the app-active flag; the Finder is
        # desktop furniture like this board and never sets it. Both are
        # DIALOGs, so clicking the board made the window manager raise it --
        # and the cards then sat ON TOP of the Finder. This board is part of
        # the desktop home and must never come forward, so it is kept below and
        # re-lowered whenever something tries to raise it.
        self.set_keep_below(True)
        self.connect("map-event", self._stay_down)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._stay_down)
        # Right-click anywhere on the board is how the board is configured --
        # the desktop is the thing being changed, so it is where the setting
        # lives. Connected after _stay_down so the board still re-lowers.
        self.connect("button-press-event", self._on_board_press)
        # The board covers the whole desktop below the menu bar. Size + position
        # it against the ACTUAL screen size -- real hardware panels are not
        # 1920x1080, and a hardcoded geometry pushed the whole thing off the
        # right edge (and off the bottom) of a smaller display.
        sw, sh = nbapp.screen_size()
        board_w = max(360, sw - 2 * BOARD_MARGIN)
        h = max(320, sh - PANEL_H - 2 * BOARD_MARGIN)
        self._avail_h = h
        self._board_w = board_w
        # ONE cell size for the whole board: five equal columns across the full
        # width, two equal rows down the full height. Everything else on the
        # board is derived from these two numbers, which is what keeps the six
        # tiles identical and the pinned pair exactly two tiles tall.
        self._tile_w = max(120, (board_w - (GRID_COLS - 1) * BOARD_GAP)
                           // GRID_COLS)
        self._tile_h = max(140, (h - BOARD_GAP) // GRID_ROWS)
        # The pinned pair take the last two columns and the FULL height, so
        # each is exactly two tiles plus the gap between them.
        # The pinned pair share ONE column now, so it is one tile wide, not two.
        self._col_w = self._tile_w
        # THE WINDOW CARRIES THE MARGIN; the cards do not move.
        #
        # This window used to be inset by BOARD_MARGIN on every side, so the
        # outermost cards sat FLUSH against its edge -- and a CSS box-shadow
        # cannot paint outside the toplevel it is in. The bottom row therefore
        # had no shadow beneath it while the top row had one, which is a worse
        # kind of wrong than no shadows at all.
        #
        # So the window now covers the whole desktop under the panel and the
        # inset is applied to the board box INSIDE it (below). The content area
        # is identical to the pixel, every card lands where it did, and there
        # are now BOARD_MARGIN pixels of window for the outer shadows to fall
        # on. Covering the extra frame costs nothing: desktopbg.py paints one
        # fixed #DED4C2 field and .wcol is the same colour, so nothing is
        # hidden.
        self.set_default_size(board_w + 2 * BOARD_MARGIN,
                              h + 2 * BOARD_MARGIN)
        self.move(0, PANEL_H)
        self.get_style_context().add_class("wcol")

        self.tasks = self._load_tasks()
        self.workout = self._load_workout()
        # Both stores are read up front: the tasks card is built first but its
        # row budget depends on how many events today actually holds, so the
        # calendar's data cannot wait for its own card.
        self.events = self._load_events()
        self.board = self._load_board()

        # board = [ 3x2 tile grid ][ Tasks over calendar ]
        board = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=BOARD_GAP)
        # The board's inset from the screen edges, applied here rather than by
        # positioning the window (see set_default_size above): it is what
        # leaves room inside the toplevel for the outer cards' shadows.
        board.set_margin_start(BOARD_MARGIN)
        board.set_margin_end(BOARD_MARGIN)
        board.set_margin_top(BOARD_MARGIN)
        board.set_margin_bottom(BOARD_MARGIN)
        self.add(board)
        self._board = board

        self._tilegrid = Gtk.Grid(row_spacing=BOARD_GAP,
                                  column_spacing=BOARD_GAP,
                                  row_homogeneous=True,
                                  column_homogeneous=True)
        # Pinned to exactly three tile columns wide so the rounding left over
        # from dividing the panel into four is absorbed by the pinned column,
        # never by the tiles -- six tiles that differ by a pixel read as a
        # broken grid.
        self._tilegrid.set_size_request(
            TILE_COLS * self._tile_w + (TILE_COLS - 1) * BOARD_GAP, -1)
        board.pack_start(self._tilegrid, False, False, 0)

        # The pinned pair, STACKED: Tasks on top, the calendar beneath it. A
        # HOMOGENEOUS vertical box, so each takes exactly half the board height
        # whatever its content asks for -- the calendar's month grid would
        # otherwise claim more than its share and leave Tasks squeezed.
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=_COL_SPACING_PX, homogeneous=True)
        board.pack_end(col, True, True, 0)
        self._col = col
        self._cal_card = self._calendar_card()
        # Fill the column. Both pinned cards are packed into a HORIZONTAL box,
        # so pack_start's expand/fill governs their WIDTH only — without an
        # explicit vexpand the calendar card stops where its content stops and
        # leaves a white gap down to the bottom of the screen, which reads as a
        # tile that failed to draw rather than as a deliberate edge.
        self._cal_card.set_vexpand(True)
        self._cal_day = time.localtime()[:3]   # (year, mon, mday) it was built for
        _tasks = self._tasks_card()
        _tasks.set_vexpand(True)
        col.pack_start(_tasks, True, True, 0)      # Tasks on top ...
        col.pack_start(self._cal_card, True, True, 0)   # ... calendar beneath
        self._rebuild_tiles()

        # nbmotion-inventory: system.desktop-board-appearing
        # The board's arrival (G1): cards settle in, staggered along their
        # columns. ONE linear Scalar carries the whole board; each card's own
        # progress is a clamped remap of it, offset by its column and eased
        # on arrival — many short damped settles from one driver, no timers.
        # A card's PAINT is translated 12px up until its progress lands
        # (draw-handler translate inside its clip: allocation never animates,
        # F2), and each frame invalidates only the cards still moving.
        self._settle_v = 1.0
        self._settle = None
        self._settle_cards = []      # (widget, column) for every card
        for card, colidx in ((_tasks, TILE_COLS), (self._cal_card, TILE_COLS)):
            card.connect("draw", self._card_settle_draw)
            card._nb_col = colidx
            self._settle_cards.append(card)
        self.connect("map-event", self._on_board_map)

        GLib.timeout_add(2000, self._ensure_mapped)
        GLib.timeout_add(6000, self._ensure_mapped)
        # Watch the app-active flag with an inotify-backed file monitor instead
        # of stat-polling it ~2.5x a second forever.
        self._app_flag_monitor = None
        try:
            _flag = Gio.File.new_for_path(APP_FLAG)
            self._app_flag_monitor = _flag.monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._app_flag_monitor.connect(
                "changed", lambda *_a: (self._poll_home(), False)[1])
        except Exception:
            pass
        # Watch the stores the CARDS read, the same way. Without this the board
        # only re-reads on the app-closed transition, so switching a tile on in
        # Widget Settings -- or logging a set, or ticking off an assignment --
        # appears to do nothing until something else happens to open and close.
        # That reads exactly like the feature being broken, and did.
        self._store_monitors = []
        # A monitor event NEVER rebuilds the board on the spot -- it asks for a
        # rebuild and the burst is coalesced into one (see _queue_reload).
        self._reload_pending = 0
        for path in (BOARD_FILE, WORKOUT_FILE, ACADEMICS_FILE, ACADEMICS_LEGACY,
                     JOURNAL_FILE, ACCOUNTING_FILE, MEALS_FILE, TASKS_FILE,
                     CAL_FILE):
            try:
                mon = Gio.File.new_for_path(path).monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed",
                            lambda *_a: (self._queue_reload(), False)[1])
                self._store_monitors.append(mon)
            except Exception:
                pass
        # Drop a pending rebuild when the board goes away, so a coalescing
        # timeout can never fire into a destroyed window.
        self.connect("destroy", lambda *_a: self._cancel_reload())
        # Reconcile once after start: covers a flag already present when the
        # board (re)launches, which produces no future monitor event.
        GLib.timeout_add(500, lambda: (self._poll_home(), False)[1])
        # Periodic backstop (every 2s): the Gio monitor can MISS a flag
        # create/delete, and the app-flag itself is best-effort.
        GLib.timeout_add_seconds(2, self._poll_home)
        # rebuild the calendar when the day rolls over so the circled day, date
        # header and TODAY agenda stay correct if the OS runs across midnight.
        GLib.timeout_add_seconds(60, self._check_day_rollover)

    # -- the settle-in (G1: system.board-settle) --------------------------
    _SETTLE_RISE = 12                # px of travel: a short damped arrival
    _SETTLE_STAG = 0.05              # s between column starts

    def _settle_t(self, col):
        """A card's own progress: a clamped remap of the one global value,
        offset by its column, eased on arrival. Columns start left to right;
        every card lands exactly on 1."""
        v = self._settle_v
        if v >= 1.0 or nbmotion is None:
            return 1.0
        dur = nbmotion.SURFACE_IN / 1000.0
        total = dur + self._SETTLE_STAG * TILE_COLS
        t = (v * total - col * self._SETTLE_STAG) / dur
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        return nbmotion.ease_out(t)

    def _card_settle_draw(self, card, cr):
        t = self._settle_t(getattr(card, "_nb_col", 0))
        if t < 1.0:
            cr.translate(0, -(1.0 - t) * self._SETTLE_RISE)
        return False

    def _on_board_map(self, *_a):
        """The board's arrival — first map at session start, and every
        return from a closed app (_poll_home's show_all remaps us)."""
        self._settle_run()
        return False

    def _settle_run(self):
        if nbmotion is None:
            self._settle_v = 1.0
            return
        if self._settle is None:
            self._settle = nbmotion.Scalar(
                widget=self, value=1.0, on_frame=self._settle_frame,
                duration=int((nbmotion.SURFACE_IN / 1000.0 +
                              self._SETTLE_STAG * TILE_COLS) * 1000),
                easing=nbmotion.LINEAR)
        self._settle_v = 0.0
        self._settle.jump_to(0.0)
        self._settle.animate_to(1.0)

    def _settle_frame(self, v):
        self._settle_v = v
        # Invalidate only the cards still travelling (F1): each one's strip,
        # grown by the rise, in window coordinates.
        # Every card's strip, every frame of the settle: a card that lands
        # needs one more paint AT rest, and eight card strips are still a
        # fraction of the window (never a full-window invalidation).
        for card in self._settle_cards:
            try:
                at = card.translate_coordinates(self, 0, 0)
                if at is None:
                    continue
                alloc = card.get_allocation()
                self.queue_draw_area(
                    at[0], at[1] - self._SETTLE_RISE - 2,
                    alloc.width, alloc.height + self._SETTLE_RISE + 4)
            except Exception:                                     # noqa: BLE001
                pass

    def _stay_down(self, *_a):
        """Re-assert the desktop layer. keep-below is a request the WM may
        re-evaluate on a click, so the X window is also explicitly lowered."""
        try:
            self.set_keep_below(True)
            gw = self.get_window()
            if gw is not None:
                gw.lower()
        except Exception:
            pass
        return False

    def _app_active(self):
        """True if a real app process is alive -- read the ref-count dir and
        confirm at least one pid is still in /proc, rather than trusting the
        (best-effort, sometimes-stale) flag file."""
        try:
            live = False
            for name in os.listdir(APP_DIR):
                if name.isdigit() and os.path.isdir("/proc/" + name):
                    live = True
                    break
            return live
        except OSError:
            return os.path.exists(APP_FLAG)

    def _poll_home(self):
        # follow the desktop home: hide while a fullscreen app owns the screen,
        # and -- crucially -- keep the board BELOW every real window whenever it
        # is shown, so a window is never rendered beneath it.
        try:
            active = self._app_active()
            if active and self.get_visible():
                self.hide()
            elif not active and not self.get_visible():
                # desktop home is returning (a fullscreen app closed) -- re-read
                # the shared stores, then show, then LOWER (show_all maps the
                # window fresh and matchbox stacks it on top).
                self._reload()
                self.show_all()
                self._stay_down()
            elif not active and self.get_visible():
                self._stay_down()
        except Exception:
            pass
        return True

    def _ensure_mapped(self):
        win = self.get_window()
        if win is not None and not win.is_viewable():
            self.hide()
            self.show_all()
            self._stay_down()      # re-lower: show_all remaps on top
        return False

    @staticmethod
    def _safe(step):
        """Run one rebuild step, swallowing anything it raises.

        Per STEP, not per reload: a single try/except around the whole reload
        meant a store that had gone bad cost not only its own card but every
        card rebuilt after it."""
        try:
            step()
        except Exception:
            pass

    def _load_stores(self):
        self.tasks = self._load_tasks()
        self.events = self._load_events()
        self.workout = self._load_workout()
        self.board = self._load_board()

    def _queue_reload(self):
        """Ask for a rebuild, at most one per burst of store changes.

        ONE save is never one monitor event: an app writes its store through
        nbapp.atomic_write_json (write a temp file, rename it into place), and
        Gio reports that as a run of events on the watched path -- DELETED,
        CREATED, CHANGED, ATTRIBUTE_CHANGED, CHANGES_DONE_HINT -- with the
        polling backend adding its own. Rebuilding on each one tore down and
        rebuilt all eight cards several times over for a single edit, and the
        worst case was the board's OWN write: ticking a task on the desktop
        writes tasks.json, which came straight back through this monitor and
        rebuilt the list under the pointer -- undoing the in-place restyle
        _toggle_task does precisely to avoid that. So the burst is collected
        into a single rebuild a moment later; the source id is kept so a second
        request inside the window joins the pending one instead of adding
        another timeout."""
        if self._reload_pending:
            return True
        self._reload_pending = GLib.timeout_add(
            _RELOAD_COALESCE_MS, self._reload_now)
        return True

    def _reload_now(self):
        self._reload_pending = 0
        self._reload()
        return False           # one-shot: the next burst arms a fresh timeout

    def _cancel_reload(self):
        """Drop a pending coalesced rebuild (the board is reloading for another
        reason, or is going away)."""
        pending, self._reload_pending = getattr(self, "_reload_pending", 0), 0
        if pending:
            try:
                GLib.source_remove(pending)
            except Exception:
                pass

    def _reload(self):
        # Re-read every shared store and rebuild from it, so a task, an event, a
        # logged set or an assignment added in an app shows up on the board.
        # Anything already queued is now redundant -- this IS that rebuild.
        self._cancel_reload()
        self._safe(self._load_stores)
        self._safe(self._rebuild_tasks)
        self._safe(self._rebuild_calendar)
        self._safe(self._rebuild_tiles)

    def _rebuild_calendar(self):
        # Swap the calendar card for a freshly built one (current circled day,
        # this-month event dots, today's agenda), tracking the day it is for.
        self._cal_day = time.localtime()[:3]
        try:
            self._col.remove(self._cal_card)
        except Exception:
            pass
        self._cal_card = self._calendar_card()
        self._cal_card.set_vexpand(True)
        self._col.pack_start(self._cal_card, True, True, 0)
        # The calendar is rebuilt on a day rollover; put it back BENEATH Tasks
        # rather than at position 0, or the daily rebuild silently swaps the
        # two cards over.
        self._col.reorder_child(self._cal_card, -1)
        self._cal_card.show_all()

    def _check_day_rollover(self):
        # The calendar card computes "today" once when built; if the OS is left
        # running across midnight, rebuild it so the circled day, date header
        # and TODAY agenda track the real date instead of the boot day.
        try:
            if time.localtime()[:3] != self._cal_day:
                self._rebuild_calendar()
                # The tiles date from the same moment: "due in 2 days", "next
                # class tomorrow", "written today" are all measured against the
                # day the tile was built on, so a machine left on overnight
                # spent the next day quoting yesterday's arithmetic.
                self._safe(self._rebuild_tiles)
        except Exception:
            pass
        return True   # keep checking every minute

    # -- stores --
    def _load_workout(self):
        """Read the Workout app's store (workout.json). Returns
        {"on": bool, "rows": [(name, done, goal)], "done": n, "goal": n}.

        Never raises: a bad store must not break the desktop, so every field is
        re-validated rather than trusted."""
        blank = {"on": False, "rows": [], "done": 0, "goal": 0, "streak": 0}
        try:
            with open(WORKOUT_FILE) as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return blank
            today = time.strftime("%Y-%m-%d")
            log = data.get("log")
            entry = log.get(today, {}) if isinstance(log, dict) else {}
            if not isinstance(entry, dict):
                entry = {}
            rows, done, goal = [], 0, 0
            for ex in (data.get("exercises") or []):
                if not isinstance(ex, dict):
                    continue
                name = ex.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                try:
                    want = max(1, min(20, int(ex.get("sets", 3))))
                except (TypeError, ValueError):
                    want = 3
                sets = entry.get(ex.get("id"))
                have = len(sets) if isinstance(sets, list) else 0
                rows.append((name.strip(), have, want))
                done += have
                goal += want
            return {"on": True, "rows": rows, "done": done, "goal": goal,
                    "streak": self._wo_streak(data, goal)}
        except Exception:
            return blank

    @staticmethod
    def _wo_streak(data, live_goal):
        """Consecutive days the WHOLE goal was completed, ending today -- or
        yesterday, while today is still in progress.

        The rule belongs to the Workout app (workout.Workout._streak); this
        reads the same file by the same rule, off the same shared date
        arithmetic, so the desktop and the app can never quote two different
        numbers at the same moment. A past day is measured against the goal it
        was LOGGED against (data["goals"]), not today's."""
        log = data.get("log")
        if not isinstance(log, dict):
            return 0
        goals = data.get("goals")
        goals = goals if isinstance(goals, dict) else {}
        today = time.strftime("%Y-%m-%d")
        done_days = set()
        for day, entry in log.items():
            o = nbapp.day_ordinal(day)
            if o is None or not isinstance(entry, dict):
                continue
            n = sum(len(v) for v in entry.values() if isinstance(v, list))
            goal = live_goal if day == today else goals.get(day, live_goal)
            if isinstance(goal, int) and goal > 0 and n >= goal:
                done_days.add(o)
        at = nbapp.day_ordinal(today)
        if at not in done_days:
            at -= 1                 # today is unfinished, not yet a miss
        cur = 0
        while at in done_days:
            cur += 1
            at -= 1
        return cur

    # -- the tile board ------------------------------------------------------

    def _load_board(self):
        """Which tiles are switched on. Written only by Widget Settings, so
        there is one place that owns the desktop's layout instead of a switch
        buried in each app's menus. Never raises: a bad store falls back to the
        defaults rather than leaving the desktop empty."""
        on = dict(TILE_DEFAULT_ON)
        self.board_order = board_order({})
        try:
            with open(BOARD_FILE) as fh:
                data = json.load(fh)
            on, self.board_order = board_state(data)
        except Exception:
            pass
        return on

    @staticmethod
    def _tile_columns():
        """How many tiles sit side by side. Fixed: the board is a grid of a
        known shape, not a pack that reflows with the panel."""
        return TILE_COLS

    def _rebuild_tiles(self):
        """Lay the switched-on tiles into the 3x2 grid, in reading order."""
        for child in self._tilegrid.get_children():
            self._tilegrid.remove(child)
        order = getattr(self, "board_order", None) or list(TILE_ORDER)
        on = [tid for tid in order
              if self.board.get(tid)][:TILE_COLS * TILE_ROWS]
        # Tiles are recreated on every rebuild; the two right-column cards
        # (column index TILE_COLS) persist. Keep those, re-hook the rest.
        self._settle_cards = [c for c in getattr(self, "_settle_cards", [])
                              if getattr(c, "_nb_col", None) == TILE_COLS]
        for slot, tid in enumerate(on):
            try:
                tile = self._tile(tid)
            except Exception:
                continue        # never a hole AND never a crash: see _tile_card
            self._tilegrid.attach(tile, slot % TILE_COLS, slot // TILE_COLS,
                                  1, 1)
            tile._nb_col = slot % TILE_COLS
            tile.connect("draw", self._card_settle_draw)
            self._settle_cards.append(tile)
        self._tilegrid.show_all()

    # -- one tile ------------------------------------------------------------
    #
    # A reader returns (meta, rows, cta) -- or None when it has nothing to
    # report, or raises, or hands back something that is not that shape -- and
    # the tile falls back to its written empty state. It is NEVER dropped: a
    # hole in the grid reads as the board being broken rather than as one app
    # having nothing to say.
    #
    #   meta : the one-line summary in the header, right-aligned
    #   rows : [(lead, name, value, hit)], where
    #            lead  = None | str | ("check", done)
    #            name  = str, ellipsized, .hit when `hit`
    #            value = None | str | ("dots", done, goal)
    #   cta  : None, or (line, hint) -- a two-line call to action shown ABOVE
    #          the rows. Only Journal uses it, and only when today is unwritten:
    #          that tile's job is to get an entry written, not to report.

    def _tile_content(self, tid):
        """(meta, rows, cta, is_empty) for one tile, whatever its reader did."""
        try:
            got = getattr(self, "_read_" + tid)()
        except Exception:
            got = None
        meta, rows, cta, chart, mark = "", [], None, None, None
        if isinstance(got, (tuple, list)) and len(got) >= 5 \
                and isinstance(got[4], dict):
            mark = got[4]
        if isinstance(got, (tuple, list)) and len(got) >= 4 \
                and isinstance(got[3], (tuple, list)):
            chart = [v for v in got[3]
                     if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if isinstance(got, (tuple, list)) and len(got) >= 2:
            meta = str(got[0] or "")
            rows = [r for r in (got[1] or [])
                    if isinstance(r, (tuple, list)) and len(r) >= 4]
            if len(got) >= 3 and isinstance(got[2], (tuple, list)) \
                    and len(got[2]) >= 2:
                cta = (str(got[2][0] or ""), str(got[2][1] or ""))
        self._chart = chart
        self._mark = mark
        if mark is not None:
            return meta, rows, cta, False
        if rows or cta or (chart and len(chart) >= 2):
            return meta, rows, cta, False
        state, action = TILE_EMPTY.get(
            tid, ("No data", "Open %s" % TILE_APP_NAME.get(tid, "")))
        return "", [], (_t(state), _t(action)), True

    def _tile(self, tid):
        """One app tile: the same card as Tasks and the calendar, with the app's
        name in the header, its summary on the right, and its content rows
        beneath."""
        if tid == "academics":
            return self._classes_tile()
        meta, rows, cta, empty = self._tile_content(tid)
        card, body = self._card_shell(_t(TILE_TITLE[tid]), meta)
        card.set_size_request(self._tile_w, -1)

        # Height budget: the card is exactly one grid cell tall and cannot
        # scroll, so work out what fits BEFORE laying anything out.
        chart = getattr(self, "_chart", None)
        # A card that carries ONE answer (the Journal's written-today mark)
        # draws it centred and is done -- no rows, no height budget, nothing
        # else competing with the single thing it is there to say.
        mark = getattr(self, "_mark", None)
        if mark is not None:
            body.pack_start(self._mark_block(mark), True, True, 0)
            return self._clickable(card, TILE_APP[tid], TILE_ARG.get(tid),
                                   _t("Open %s") % _t(TILE_APP_NAME[tid]))
        avail = (self._tile_h - _HEAD_PX - _BODY_PAD_PX - _CARD_BORDER_PX
                 - (_CTA_PX if cta else 0))
        fit = max(0, min(MAX_TILE_ROWS, avail // _CROW_PX))
        if cta:
            # With rows beneath it the prompt heads the card. With NOTHING
            # beneath it -- the Journal's reminder, an empty state -- it is the
            # card's entire contents, so it sits in the middle of the paper
            # rather than clinging to the top edge over a blank half.
            alone = not rows and not (chart and len(chart) >= 2)
            blk = self._cta_block(cta[0], cta[1])
            if alone:
                blk.set_valign(Gtk.Align.CENTER)
            body.pack_start(blk, alone, alone, 0)
        # Over the cap the LAST slot becomes the "+N more" read-out rather than
        # a row -- except when only one row fits at all, where a card whose
        # entire body is "+6 more" tells you nothing; then it shows the row.
        if fit <= 0:
            shown, hidden = [], 0
        elif len(rows) <= fit:
            shown, hidden = rows, 0
        elif fit == 1:
            shown, hidden = rows[:1], 0
        else:
            shown = rows[:fit - 1]
            hidden = len(rows) - len(shown)
        fill = tid in FILL_TILES
        for spec in shown:
            body.pack_start(self._content_row(spec, fill), True, True, 0)
        if hidden:
            body.pack_start(self._more_row(hidden), True, True, 0)
        if chart and len(chart) >= 2:
            # The card's whole body IS the curve. It expands, so it fills
            # whatever the rows left rather than sitting in a fixed strip.
            area = Gtk.DrawingArea()
            area.set_vexpand(True)
            area.connect("draw", self._chart_draw, list(chart))
            body.pack_start(area, True, True, 0)
        elif tid in FILL_TILES:
            # These cards say everything they have to say in a few rows, so the
            # rows SHARE the card instead of stacking at the top over a block of
            # blank paper -- which read as a half-drawn tile.
            pass
        else:
            # ...stretch what there is towards a comfortable row height, with
            # the leftover collecting as blank paper at the foot of the card.
            self._pad_rows(body, len(shown) + (1 if hidden else 0),
                           avail, _CROW_PX, fit)

        return self._clickable(card, TILE_APP[tid], TILE_ARG.get(tid),
                               _t("Open %s") % _t(TILE_APP_NAME[tid]))

    def _classes_tile(self):
        """Classes as the Calendar day schedule in the existing card shell."""
        try:
            schedule = self._read_classes_schedule()
        except Exception:
            schedule = None
        card, body = self._card_shell(_t(TILE_TITLE["academics"]),
                                      _t("Today") if schedule else "")
        card.set_size_request(self._tile_w, -1)
        if schedule:
            events, window, now_minutes = schedule
            body.pack_start(_ClassesSchedule(events, window, now_minutes),
                            True, True, 0)
        else:
            state, action = TILE_EMPTY["academics"]
            block = self._cta_block(_t(state), _t(action))
            block.set_valign(Gtk.Align.CENTER)
            body.pack_start(block, True, True, 0)
        return self._clickable(card, TILE_APP["academics"],
                               TILE_ARG.get("academics"),
                               _t("Open %s") % _t(TILE_APP_NAME["academics"]))

    def _card_shell(self, title, meta, strong=False):
        """(card, body) -- the header row every card on this board shares."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("card")
        head = Gtk.Box(spacing=8)
        head.get_style_context().add_class("chead")
        tl = Gtk.Label(label=title, xalign=0)
        tl.get_style_context().add_class("ctitle")
        tl.set_ellipsize(Pango.EllipsizeMode.END)
        # max_width_chars pins the NATURAL width: an ellipsizing GtkLabel still
        # reports its whole string as its natural size, and a homogeneous grid
        # hands spare width out in proportion to that -- one long title used to
        # stretch its own column and shrink its neighbour.
        tl.set_max_width_chars(1)
        head.pack_start(tl, True, True, 0)
        ml = Gtk.Label(label=meta or "", xalign=1)
        mctx = ml.get_style_context()
        mctx.add_class("cmeta")
        if strong:
            mctx.add_class("strong")
        # The summary keeps its NATURAL width -- it is the fact the header is
        # for, and a summary rendered as a bare ellipsis is worse than none.
        # max_width_chars caps how far a runaway one can push the title, but it
        # must never be 1: on a pack_end child that IS the width it gets, and
        # every summary on the board came out as a single "..." once.
        ml.set_ellipsize(Pango.EllipsizeMode.END)
        ml.set_max_width_chars(18)
        head.pack_end(ml, False, False, 0)
        card.pack_start(head, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.get_style_context().add_class("cbody")
        card.pack_start(body, True, True, 0)
        return card, body

    def _content_row(self, spec, fill=False):
        """[lead][name..........][value] -- the one row shape on this board.

        `fill` is the variant used by cards with only a handful of rows and a
        whole tile to put them in (see FILL_TILES): larger type, more room to
        breathe, and a wider lead column."""
        lead, name, value, hit = spec[0], spec[1], spec[2], spec[3]
        row = Gtk.Box(spacing=9)
        row.get_style_context().add_class("crow")
        if fill:
            row.get_style_context().add_class("fill")
        if isinstance(lead, (tuple, list)) and lead and lead[0] == "check":
            chk = _Check(bool(lead[1]), 15)
            row.pack_start(chk, False, False, 0)
        elif lead:
            ll = Gtk.Label(label=str(lead), xalign=0)
            ll.get_style_context().add_class("clead")
            ll.set_ellipsize(Pango.EllipsizeMode.END)
            ll.set_max_width_chars(1)
            ll.set_size_request(74 if fill else 46, -1)
            row.pack_start(ll, False, False, 0)
        nl = Gtk.Label(label=str(name), xalign=0)
        nctx = nl.get_style_context()
        nctx.add_class("cname")
        if hit:
            nctx.add_class("hit")
        nl.set_ellipsize(Pango.EllipsizeMode.END)
        nl.set_max_width_chars(1)
        row.pack_start(nl, True, True, 0)
        if isinstance(value, (tuple, list)) and value and value[0] == "dots":
            row.pack_end(self._dots_cell(value[1], value[2], hit),
                         False, False, 0)
        elif isinstance(value, (tuple, list)) and value and value[0] == "alert":
            # The one row tone that means ACT ON THIS. `hit` cannot carry it:
            # that flag is green (.cname.hit / .cval.hit) and means finished,
            # which is the opposite thing. Reserved for a state a person is
            # late for -- an accent that appears on every row means nothing.
            al = Gtk.Label(label=str(value[1]), xalign=1)
            actx = al.get_style_context()
            actx.add_class("cval")
            actx.add_class("alert")
            al.set_ellipsize(Pango.EllipsizeMode.END)
            al.set_max_width_chars(_VALUE_CHARS)
            row.pack_end(al, False, False, 0)
        elif value:
            vl = Gtk.Label(label=str(value), xalign=1)
            vctx = vl.get_style_context()
            vctx.add_class("cval")
            if hit:
                vctx.add_class("hit")
            vl.set_ellipsize(Pango.EllipsizeMode.END)
            vl.set_max_width_chars(_VALUE_CHARS)
            row.pack_end(vl, False, False, 0)
        return row

    def _mark_block(self, mark):
        """A single answer, centred: a tick or a cross, the word, the date.

        Drawn rather than typed: the shipped interface font has no check or
        cross glyph, so a literal one would come out as an empty box on the
        real machine (the same trap the whole OS pays for with nbicons).
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        area = Gtk.DrawingArea()
        area.set_size_request(56, 56)
        area.set_halign(Gtk.Align.CENTER)
        area.connect("draw", self._mark_draw, bool(mark.get("done")))
        box.pack_start(area, False, False, 0)
        lb = Gtk.Label(label=mark.get("label", ""), xalign=0.5)
        lb.get_style_context().add_class("jmark")
        lb.get_style_context().add_class("done" if mark.get("done") else "todo")
        box.pack_start(lb, False, False, 0)
        dt = Gtk.Label(label=mark.get("date", ""), xalign=0.5)
        dt.get_style_context().add_class("jdate")
        dt.set_ellipsize(Pango.EllipsizeMode.END)
        dt.set_max_width_chars(24)
        box.pack_start(dt, False, False, 0)
        return box

    @staticmethod
    def _mark_draw(area, cr, done):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.0 - 3
        if done:
            cr.set_source_rgb(0.310, 0.478, 0.227)      # the OS's green
        else:
            cr.set_source_rgb(0.784, 0.204, 0.118)      # signage red
        cr.set_line_width(3.4)
        cr.set_line_cap(1)                              # ROUND
        if done:
            cr.move_to(cx - r * 0.55, cy + r * 0.02)
            cr.line_to(cx - r * 0.14, cy + r * 0.44)
            cr.line_to(cx + r * 0.58, cy - r * 0.44)
        else:
            cr.move_to(cx - r * 0.45, cy - r * 0.45)
            cr.line_to(cx + r * 0.45, cy + r * 0.45)
            cr.move_to(cx + r * 0.45, cy - r * 0.45)
            cr.line_to(cx - r * 0.45, cy + r * 0.45)
        cr.stroke()
        return False

    @staticmethod
    def _chart_draw(area, cr, series):
        """The running cash balance as a filled curve.

        Deliberately unlabelled: the exact figure is already in the card's
        header, so this only has to answer "which way is it going". A flat
        ledger still draws a line through the middle rather than nothing."""
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        if w < 8 or h < 8 or len(series) < 2:
            return False
        pad_t, pad_b = 10.0, 8.0
        lo, hi = min(series), max(series)
        span = (hi - lo) or 1.0
        usable = max(1.0, h - pad_t - pad_b)
        n = len(series) - 1
        pts = [(w * i / float(n), pad_t + usable * (1.0 - (v - lo) / span))
               for i, v in enumerate(series)]

        # Zero line, when the ledger crosses it -- the one gridline that means
        # something on a cash balance.
        if lo < 0 < hi:
            zy = pad_t + usable * (1.0 - (0 - lo) / span)
            cr.set_source_rgb(0.788, 0.769, 0.722)
            cr.set_line_width(1)
            cr.move_to(0, zy + 0.5)
            cr.line_to(w, zy + 0.5)
            cr.stroke()

        cr.move_to(pts[0][0], h)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], h)
        cr.close_path()
        up = series[-1] >= series[0]
        if up:
            cr.set_source_rgba(0.310, 0.478, 0.227, 0.16)   # the OS's green
        else:
            cr.set_source_rgba(0.784, 0.204, 0.118, 0.14)   # signage red
        cr.fill()

        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        if up:
            cr.set_source_rgb(0.310, 0.478, 0.227)
        else:
            cr.set_source_rgb(0.784, 0.204, 0.118)
        cr.set_line_width(1.6)
        cr.stroke()
        return False

    @staticmethod
    def _dots_cell(done, goal, hit):
        """The set-dot run, or -- for a goal too long to draw at tile width --
        the same fact as a plain "4 / 12"."""
        try:
            done, goal = int(done), int(goal)
        except (TypeError, ValueError):
            done, goal = 0, 0
        if goal > _Dots.MAX:
            lbl = Gtk.Label(label="%d / %d" % (done, goal), xalign=1)
            ctx = lbl.get_style_context()
            ctx.add_class("cval")
            if hit:
                ctx.add_class("hit")
            return lbl
        return _Dots(done, goal)

    @staticmethod
    def _cta_block(line, hint):
        """The two-line "here is what to do" block -- an empty card's state and
        its invitation, and the Journal tile's whole reason to exist on a day
        nothing has been written."""
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.get_style_context().add_class("emptyrow")
        top = Gtk.Label(label=line, xalign=0)
        top.get_style_context().add_class("emptytext")
        top.set_ellipsize(Pango.EllipsizeMode.END)
        top.set_max_width_chars(1)
        row.pack_start(top, False, False, 0)
        sub = Gtk.Label(label=hint, xalign=0)
        sub.get_style_context().add_class("emptyhint")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(1)
        row.pack_start(sub, False, False, 0)
        return row

    @staticmethod
    def _more_row(count):
        row = Gtk.Box()
        row.get_style_context().add_class("moretail")
        more = Gtk.Label(label=_t("+%d more") % count, xalign=0)
        more.get_style_context().add_class("moretext")
        more.set_ellipsize(Pango.EllipsizeMode.END)
        more.set_max_width_chars(1)
        row.pack_start(more, True, True, 0)
        return row

    @staticmethod
    def _pad_rows(body, n, avail, row_px, fit):
        """Stretch `n` rows towards _ROW_TARGET_PX and park the rest as blank.

        Every row in a card is packed to expand, so a card with a lot to say
        fills itself. A card with three meals in it would otherwise render them
        as three enormous bands, so invisible spacers of one row's height are
        added until the share works out at a comfortable row -- the leftover
        then collects at the FOOT of the card, where blank paper belongs."""
        if n <= 0 or avail <= 0 or row_px <= 0:
            return
        want = max(1, min(fit if fit > 0 else n, avail // _ROW_TARGET_PX))
        for _ in range(max(0, want - n)):
            pad = Gtk.Box()
            pad.set_size_request(-1, row_px)
            body.pack_start(pad, True, True, 0)

    @staticmethod
    def _academics_store():
        """Where the Academics store actually is, resolved on every read.

        Resolved at READ time rather than once at import because the board is
        long-running: it starts before the app has ever been opened, and the
        moment Academics first saves, the store appears under the new name.
        """
        try:
            if not os.path.exists(ACADEMICS_FILE) \
                    and os.path.exists(ACADEMICS_LEGACY):
                return ACADEMICS_LEGACY
        except OSError:
            pass
        return ACADEMICS_FILE

    @staticmethod
    def _read_store(path):
        """An app's store as a dict, or an empty one.

        Never raises. Every reader goes through here, so a store that is
        missing, truncated or simply not a dict costs that card its CONTENT --
        it falls back to its empty state -- rather than costing it its place on
        the board."""
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _as_list(value):
        """`value` if it is a list, else an empty one. A store field that
        should hold a list can hold anything at all once it has been
        hand-edited, and `for x in 7` raises."""
        return value if isinstance(value, list) else []

    @staticmethod
    def _when(days):
        """A due/starts-on date, as few words as a value column can carry."""
        if days < 0:
            return _t("overdue")
        if days == 0:
            return _t("today")
        if days == 1:
            return _t("tomorrow")
        return _t("in %d days") % days

    # -- readers -------------------------------------------------------------

    def _read_classes_schedule(self, now=None):
        """Today's safe, drawable Classes schedule, or None when empty."""
        data = self._read_store(self._academics_store())
        if now is None:
            now = time.localtime()
        events = _classes_for_day(self._as_list(data.get("classes")),
                                  now.tm_wday)
        window = _classes_window(events)
        if not events or window is None:
            return None
        return events, window, now.tm_hour * 60 + now.tm_min

    def _read_academics(self):
        """TODAY'S TIMETABLE, one row per class: when it starts, what it is and
        where. A day with nothing on falls forward to the next day that has
        something, because a blank card on a Sunday is a card that answers the
        wrong question -- what someone wants then is what Monday holds.

        academics.json holds classes as {"name"|"label", "room", "meets":
        [{"day": 0-6 Monday-first, "start": "HH:MM", "room"}]} -- a WEEKLY
        pattern, not dated occurrences."""
        data = self._read_store(self._academics_store())
        classes = [c for c in self._as_list(data.get("classes"))
                   if isinstance(c, dict) and (c.get("name") or c.get("label"))]
        if not classes:
            return None
        now = time.localtime()
        byday = {}
        for c in classes:
            for m in self._as_list(c.get("meets")):
                if not isinstance(m, dict):
                    continue
                day, start = m.get("day"), _minutes(m.get("start"))
                if start is None or not isinstance(day, int) \
                        or not 0 <= day <= 6:
                    continue
                byday.setdefault(day, []).append(
                    (start, str(c.get("name") or c.get("label")),
                     str(m.get("room") or c.get("room") or ""),
                     _fmt_time(m.get("start"))))
        if not byday:
            return None
        # today first, then forward through the week to the next day that has
        # anything at all.
        for ahead in range(0, 8):
            day = (now.tm_wday + ahead) % 7
            if byday.get(day):
                break
        else:                                   # pragma: no cover - byday is set
            return None
        todays = sorted(byday[day])
        if ahead == 0:
            meta = _t("Today")
        elif ahead == 1:
            meta = _t("Tomorrow")
        else:
            meta = _t(DAY_NAMES[day])
        mins_now = now.tm_hour * 60 + now.tm_min
        rows = [(at, name, room, ahead == 0 and start < mins_now)
                for start, name, room, at in todays]
        return meta, rows, None

    def _read_homework(self):
        """The assignment list, read like the Tasks list beside it: a checkbox
        mark, the title, and when it is due. Unfinished first, in due order --
        the cap must never bury a pending assignment behind handed-in ones."""
        data = self._read_store(self._academics_store())
        items = [h for h in self._as_list(data.get("homework"))
                 if isinstance(h, dict) and h.get("title")]
        if not items:
            return None
        today = nbapp.day_ordinal(time.strftime("%Y-%m-%d"))
        open_, done_ = [], []
        for h in items:
            o = nbapp.day_ordinal(h.get("due"))
            (done_ if h.get("done") else open_).append((o, h))
        open_.sort(key=lambda p: (p[0] is None, p[0] or 0))
        done_.sort(key=lambda p: (p[0] is None, p[0] or 0), reverse=True)
        rows = []
        for o, h in open_ + done_:
            is_done = bool(h.get("done"))
            if is_done:
                when = _t("done")
            elif o is None or today is None:
                when = ""
            else:
                when = self._when(o - today)
            rows.append((("check", is_done), str(h["title"]), when, is_done))
        n = len(open_)
        meta = _t("Nothing to do") if not n else _t("%d to do") % n
        return meta, rows, None

    def _read_bills(self):
        """What is owed, soonest first, and what it comes to this month.

        Parsed by bills.read_bills / bills.due_info rather than re-read here,
        the same arrangement the Meals tile has with mealplanner: the app owns
        what its file means, so the tile and the app can never disagree about
        which day a bill is due -- and this store's dates carry repeat rules
        and a postal lead time, which is far too much meaning to copy.

        A bill that has to be acted on now is marked, because the value column
        is the only place on a tile where "overdue" and "due in three weeks"
        can be told apart at a glance, and that difference is the whole reason
        this tile is on the desktop."""
        try:
            import bills
        except Exception:
            return None
        items = bills.read_bills(BILLS_FILE)
        if not items:
            return None
        today = bills.today_key()
        pairs = [(b, bills.due_info(b, today)) for b in items]
        pairs.sort(key=lambda p: bills.sort_key(p[0], p[1]))
        rows = []
        for bill, info in pairs:
            state = info["state"]
            rows.append((None, bill["payee"],
                         ("alert", state) if bills.needs_paying(info)
                         else state, False))
        return bills.money(bills.month_total(items, today)), rows, None

    def _read_birthdays(self):
        """Whose birthday is coming, and how soon.

        The dates are read through contacts.days_until_birthday, which owns
        what the app's free-text birthday field can hold (and what a 29
        February birthday does in a common year). A birthday TODAY is marked
        the way a met goal is: it is the one row here that is good news rather
        than a countdown."""
        try:
            import contacts
        except Exception:
            return None
        data = self._read_store(CONTACTS_FILE)
        people = self._as_list(data.get("people"))
        soon = []
        for person in people:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            try:
                days = contacts.days_until_birthday(person.get("bday"))
            except Exception:
                days = None
            if days is None or days > contacts.BIRTHDAY_SOON:
                continue
            soon.append((days, name))
        if not soon:
            return None
        soon.sort()
        rows = [(None, name, self._when(days), days == 0)
                for days, name in soon]
        return _t("%d coming up") % len(soon), rows, None

    def _read_reading(self):
        """The shelf, and how far through each book is.

        The book currently OPEN heads the list -- it is the one the card is
        about -- and a finished book is marked, because on this board a mark
        means a thing that is done."""
        data = self._read_store(EBOOK_FILE)
        books = []
        for book in self._as_list(data.get("books")):
            if not isinstance(book, dict):
                continue
            title = str(book.get("title") or "").strip()
            if not title:
                continue
            try:
                frac = min(max(float(book.get("frac") or 0.0), 0.0), 1.0)
            except (TypeError, ValueError):
                frac = 0.0
            books.append({"title": title, "frac": frac,
                          "path": str(book.get("path") or "")})
        if not books:
            return None
        open_path = data.get("open")
        open_path = open_path if isinstance(open_path, str) else ""
        # Reading order: the open book, then the ones already started (furthest
        # through first), then the ones not begun. A shelf sorted by title
        # would bury the book actually being read halfway down the card.
        books.sort(key=lambda b: (b["path"] != open_path or not open_path,
                                  b["frac"] <= 0.0, -b["frac"], b["title"]))
        rows = [(None, b["title"], "%d%%" % round(b["frac"] * 100),
                 b["frac"] >= 0.999) for b in books]
        return _t("%d books") % len(books), rows, None

    def _read_language(self):
        """Practised today, or not, and the streak riding on it.

        ONE mark, like the Journal card beside it, because the store answers
        exactly one question a desktop can use: has today's goal been met. The
        XP under the mark says how far off it is when it has not."""
        data = self._read_store(LANGUAGE_FILE)
        try:
            import language
            goals, default = language.GOALS, language.DEFAULT_GOAL
        except Exception:
            goals, default = (10, 20, 30, 50), 20
        goal = data.get("goal")
        goal = goal if goal in goals else default

        def _count(key):
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0
            return max(0, int(value))
        streak = _count("streak")
        # day_xp belongs to the day stamped beside it. Without that check a
        # learner who practised yesterday and not today reads as done, which is
        # the one thing this card exists to get right.
        today = time.strftime("%Y-%m-%d")
        day_xp = _count("day_xp") if data.get("day") == today else 0
        if not streak and not day_xp and not data.get("crowns"):
            return None
        done = day_xp >= goal
        return (_t("%d day streak") % streak if streak else "", [], None, None,
                {"done": done,
                 "label": _t("Practised") if done else _t("Not practised"),
                 "date": _t("%d of %d XP") % (day_xp, goal)})

    def _read_novel(self):
        """The manuscript, chapter by chapter, counted in words.

        A word count is the only number a novel has that means anything from
        outside it, and the card's job is to show the shape of the book without
        opening it."""
        data = self._read_store(NOVEL_FILE)
        chapters = []
        for i, chapter in enumerate(self._as_list(data.get("chapters"))):
            if not isinstance(chapter, dict):
                continue
            body = chapter.get("body")
            words = len(body.split()) if isinstance(body, str) else 0
            title = str(chapter.get("title") or "").strip()
            if not title:
                num = chapter.get("num")
                title = _t("Chapter %d") % (num if isinstance(num, int)
                                            and not isinstance(num, bool)
                                            else i + 1)
            chapters.append((title, words))
        if not chapters:
            return None
        total = sum(w for _t_, w in chapters)
        rows = [(None, title, _t("%d words") % words, False)
                for title, words in chapters]
        return _t("%d words") % total, rows, None

    def _read_meals(self):
        """TODAY'S THREE MEALS as a chart -- breakfast, lunch and dinner, each
        with what is planned or an honest blank. Not just the next one: the
        question the card answers is "what am I eating today".

        The plan is parsed by mealplanner.read_plan rather than re-read here,
        so the tile and the app can never disagree about what is in it."""
        try:
            import mealplanner
            plan = mealplanner.read_plan(MEALS_FILE)
        except Exception:
            return None
        if not plan:
            return None
        today = time.strftime("%Y-%m-%d")
        slots = plan.get(today) or {}
        rows, planned = [], 0
        for meal in MEAL_KEYS:
            slot = slots.get(meal)
            name = _t(MEAL_NAMES[meal])
            if not slot:
                rows.append((name, _t("Nothing planned"), "", False))
                continue
            planned += 1
            note = _t("takeaway") if slot.get("kind") == "takeout" else ""
            rows.append((name, str(slot.get("title") or ""), note, False))
        if not planned:
            # today is blank, but the plan itself is not: say when the next
            # planned day is rather than pretending the app is empty.
            nxt = sorted(d for d in plan if d > today)
            if not nxt:
                return None
            rows = []
            for meal in MEAL_KEYS:
                slot = (plan.get(nxt[0]) or {}).get(meal)
                if slot:
                    rows.append((_t(MEAL_NAMES[meal]),
                                 str(slot.get("title") or ""), "", False))
            if not rows:
                return None
            return _t("Tomorrow") if nxt[0] == self._day_after(today) \
                else _t("Coming up"), rows, None
        return _t("Today"), rows, None

    @staticmethod
    def _day_after(iso):
        """The ISO day after `iso`, or "" -- used only to decide whether the
        next planned day can honestly be called tomorrow."""
        try:
            y, m, d = (int(p) for p in str(iso).split("-"))
            nxt = datetime.date(y, m, d) + datetime.timedelta(days=1)
            return "%04d-%02d-%02d" % (nxt.year, nxt.month, nxt.day)
        except Exception:
            return ""

    def _read_workout(self):
        """Today against the goal, exercise by exercise: the name, and a run of
        dots -- one per set in the goal, filled for each set logged. The header
        carries the day's total, or the streak once the day is complete."""
        wo = self.workout
        goal, done = wo.get("goal", 0), wo.get("done", 0)
        rows = [r for r in wo.get("rows") or []
                if isinstance(r, (tuple, list)) and len(r) == 3]
        if not goal or not rows:
            return None
        streak = wo.get("streak") or 0
        if done >= goal:
            meta = (_t("%d day streak") % streak if streak
                    else _t("Today is done"))
        else:
            meta = _t("%d of %d sets") % (done, goal)
        out = [(None, name, ("dots", have, want), have >= want)
               for name, have, want in rows]
        return meta, out, None

    def _read_journal(self):
        """Written today, or not. That is the whole card.

        It carries ONE fact, so it shows one mark: a green check when today has
        been written, a red cross when it has not, with the date under it. The
        earlier version listed past entries beneath the prompt, which buried
        the only question the card exists to ask.

        Returns (meta, rows, cta, chart, mark) -- `mark` is what _tile() draws.
        """
        data = self._read_store(JOURNAL_FILE)
        entries = [e for e in self._as_list(data.get("entries"))
                   if isinstance(e, dict)]
        today = nbapp.day_ordinal(time.strftime("%Y-%m-%d"))
        newest = None
        for e in entries:
            o = self._journal_ordinal(e)
            if o is not None and (newest is None or o > newest):
                newest = o
        done = (newest is not None and today is not None and newest >= today)
        when = time.strftime("%A %d %B")
        return ("", [], None, None,
                {"done": done,
                 "label": _t("Written") if done else _t("Not written"),
                 "date": when})

    def _read_journal_history(self):
        """Kept for the tests that walk the older shape; not used by the tile."""
        data = self._read_store(JOURNAL_FILE)
        entries = [e for e in self._as_list(data.get("entries"))
                   if isinstance(e, dict)]
        today = nbapp.day_ordinal(time.strftime("%Y-%m-%d"))
        dated = sorted(((self._journal_ordinal(e), e) for e in entries),
                       key=lambda p: (p[0] is None, -(p[0] or 0)))
        rows = []
        for o, e in dated:
            title = str(e.get("title") or "").strip() or _t("Untitled entry")
            if o is None or today is None:
                when = ""
            elif o >= today:
                when = _t("today")
            elif o == today - 1:
                when = _t("yesterday")
            else:
                when = _t("%d days ago") % (today - o)
            rows.append((None, title, when, o is not None and o >= today))
        return rows

    @staticmethod
    def _journal_ordinal(entry):
        """A journal entry's day number, from its `day` + `month_label`."""
        try:
            day = int(str(entry.get("day")).strip())
            month, year = str(entry.get("month_label")).rsplit(" ", 1)
            return nbapp.day_ordinal("%04d-%02d-%02d"
                                     % (int(year), MONTHS.index(month) + 1, day))
        except (AttributeError, TypeError, ValueError):
            return None

    def _read_accounting(self):
        """The CASH BALANCE, and the entries it is made of.

        Accounting stores {"tx": [{"date", "desc", "amt"}], "opening": ...} --
        not a "transactions" list, and the amount key is "amt". The balance is
        the opening figure plus every amount, formatted exactly as
        accounting._money formats it, so the tile and the app can never show
        two different balances for the same ledger."""
        data = self._read_store(ACCOUNTING_FILE)
        txns = [t for t in self._as_list(data.get("tx")) if isinstance(t, dict)]
        try:
            total = float(data.get("opening") or 0)
        except (TypeError, ValueError):
            total = 0.0
        for t in txns:
            try:
                total += float(t.get("amt") or 0)
            except (TypeError, ValueError):
                continue
        if not txns and not total:
            return None
        # The balance, and the SHAPE of how it got there -- nothing else. The
        # list of individual entries belongs in the app; on a card it was six
        # lines of detail answering a question ("am I alright?") that the
        # number and the curve answer at a glance.
        try:
            opening = float(data.get("opening") or 0)
        except (TypeError, ValueError):
            opening = 0.0
        series = [opening]
        for t in sorted(txns, key=lambda x: str(x.get("date") or "")):
            try:
                series.append(series[-1] + float(t.get("amt") or 0))
            except (TypeError, ValueError):
                continue
        return _fmt_money(total), [], None, series

    def _load_tasks(self):
        """Read the shared flat task list (tasks.json: [{text, done}, ...]) the
        Tasks app writes. Nothing is seeded -- a missing / unreadable / empty
        store yields [] and the card shows its empty-state. Never raises."""
        try:
            with open(TASKS_FILE) as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return [{"text": str(t.get("text", "")), "done": bool(t.get("done"))}
                        for t in data if isinstance(t, dict)]
        except Exception:
            pass
        return []

    def _save_tasks(self, tasks):
        """Write the flat list back in the shared {"text","done"} shape so a tick
        made on the desktop card round-trips into the Tasks app."""
        try:
            nbapp.atomic_write_json(TASKS_FILE, tasks)
        except Exception:
            pass

    def _load_events(self):
        """Read the Calendar app's shared event store (calendar.json:
        [{date, start, end, title, cal}, ...]). Returns normalized events
        {ymd:(y,m,d), start_min:int|None, time:'HH:MM', title:str}. Dates are
        parsed by plain int split -- NEVER import calendar / time.strptime (the
        DE's calendar.py shadows the stdlib module on PYTHONPATH). A missing /
        unreadable / empty store yields []. Never raises."""
        try:
            with open(CAL_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            ymd = self._parse_iso(item.get("date"))
            if ymd is None:
                continue
            start_min = self._start_minutes(item.get("start"))
            out.append({"ymd": ymd, "start_min": start_min,
                        "time": self._fmt_hhmm(start_min),
                        "title": str(item.get("title", ""))})
        return out

    @staticmethod
    def _parse_iso(s):
        """'YYYY-MM-DD' -> (year, month, day) by plain int split, or None on
        anything malformed. No time.strptime / import calendar."""
        try:
            y, m, d = str(s).split("-")
            return (int(y), int(m), int(d))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _start_minutes(val):
        """A Calendar float hour (9.0, 18.5) -> minutes since midnight, or None
        when absent/unparseable (a timeless event, which sorts last)."""
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        h = int(f)
        m = int(round((f - h) * 60))
        if m >= 60:
            h += 1
            m -= 60
        return h * 60 + m

    @staticmethod
    def _fmt_hhmm(mins):
        """Minutes since midnight -> 'HH:MM', or '' for a timeless event."""
        if mins is None:
            return ""
        return "%02d:%02d" % (mins // 60, mins % 60)

    def _today_events(self):
        """Today's events from the cached store, ordered by start time; timeless
        events (start_min is None) sort last."""
        now = time.localtime()
        ymd = (now.tm_year, now.tm_mon, now.tm_mday)
        agenda = [e for e in getattr(self, "events", []) if e["ymd"] == ymd]
        agenda.sort(key=lambda e: (e["start_min"] is None,
                                   e["start_min"] if e["start_min"] is not None
                                   else 0))
        return agenda

    def _row_caps(self):
        """(task_rows, agenda_rows) the two pinned cards may render so they FIT
        their cells on the REAL panel.

        Tasks and the calendar now SHARE one column, stacked, so each gets ONE
        tile height -- not the whole column. Budgeting both from the full column
        height (which is what this did when they stood side by side) let each
        card ask for the entire board, and GTK answers an impossible request by
        squeezing: the bottom tasks and the agenda were clipped off with no
        scrollbar to reach them. The budget comes from the live cell height,
        never a hardcoded 1080. Called only when a card is (re)built."""
        try:
            avail = int(self._tile_h)
        except Exception:
            avail = 490
        # Tasks: the whole card, less its header, padding and hairline.
        task_room = avail - _HEAD_PX - _BODY_PAD_PX - _CARD_BORDER_PX
        task_cap = max(1, min(MAX_TASK_ROWS, task_room // _TASK_ROW_PX))
        # Calendar: the same, less the month grid (a SIX-week month is the worst
        # case and the one a five-week month hides) and the TODAY label.
        now = time.localtime()
        weeks = len(_month_weeks(now.tm_year, now.tm_mon))
        grid = _GRID_WD_PX + weeks * _GRID_ROW_PX + _GRID_PAD_PX
        ag_room = (avail - _HEAD_PX - _CARD_BORDER_PX - grid - _AGSEC_PX)
        ag_cap = max(0, min(MAX_AGENDA_ROWS, ag_room // _AGENDA_ROW_PX))
        return task_cap, ag_cap

    def _clickable(self, child, mod, arg=None, tip=None):
        """Wrap `child` so activating it opens the app that owns this card.

        A REAL Gtk.Button, not the windowless EventBox this used to be. An
        EventBox answers a pointer and nothing else, so every way INTO an app
        from this board -- a whole tile, the Tasks heading, an agenda line, a
        "+N more" tail -- could only be reached with a mouse. A button is
        activated by the keyboard as well (Space or Enter once it is tabbed to),
        it takes the focus ring the rest of the OS uses, and it tells assistive
        technology that this card is a control rather than decoration.

        It is still an input-only layer laid over an already-painted card: a
        GtkButton, like a windowless EventBox, is a no-window widget -- it
        realizes an INPUT_ONLY child window and draws into its parent's -- so it
        adds no window that could scan out black on the no-compositor
        framebuffer.

        Relief NONE plus the neutral `.boardhit` rules (see WIDGETS_CSS) leave
        the button contributing nothing of its own: the papertone surface, the
        hairline frame, the padding and every pixel of the geometry stay owned
        by `child`, exactly as they were under the EventBox.

        NOTE the board window sets accept_focus False (it is desktop furniture
        and must never come forward), so these buttons are reachable from the
        keyboard within the board, not from a window-manager focus switch."""
        hit = Gtk.Button()
        hit.set_relief(Gtk.ReliefStyle.NONE)
        hit.get_style_context().add_class("boardhit")
        hit.add(child)
        if tip:
            hit.set_tooltip_text(tip)
        # "clicked" is the one signal both paths emit -- a primary-button press
        # and release, and a keyboard activation. It already means "the primary
        # button", so this wrapper no longer sets an event mask or sifts through
        # pointer buttons by hand the way the EventBox had to.
        hit.connect("clicked", self._on_open_clicked, mod, arg)
        return hit

    def _on_open_clicked(self, _w, mod, arg):
        # _launch already degrades silently on a failed spawn, so there is
        # nothing here to guard against.
        self._launch(mod, arg)

    # -- Tasks card --
    def _tasks_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("card")

        head = Gtk.Box(spacing=8)
        head.get_style_context().add_class("chead")
        title = Gtk.Label(label=_t("Tasks"), xalign=0)
        title.get_style_context().add_class("ctitle")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(1)
        head.pack_start(title, True, True, 0)
        self._progress = Gtk.Label(xalign=1)
        self._progress.get_style_context().add_class("cmeta")
        self._progress.set_ellipsize(Pango.EllipsizeMode.END)
        self._progress.set_max_width_chars(1)
        head.pack_end(self._progress, False, False, 0)
        # The card could tick a task but never ADD one -- the app that owns it
        # was unreachable from the desktop. Its heading now opens it.
        card.pack_start(self._clickable(head, "tasks", tip=_t("Open Tasks")),
                        False, False, 0)

        self._tasklist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._tasklist.get_style_context().add_class("tasklist")
        card.pack_start(self._tasklist, True, True, 0)
        self._rebuild_tasks()
        return card

    def _rebuild_tasks(self):
        for ch in self._tasklist.get_children():
            self._tasklist.remove(ch)
        # store-index -> label / checkbox, so a tick restyles that one row in
        # place instead of tearing down and rebuilding the list from inside its
        # own click handler. Keyed by store index (not a flat list) because the
        # rows are shown unfinished-first and capped, so display order != store
        # order.
        self._task_labels = {}
        self._task_checks = {}
        avail = self._avail_h - _HEAD_PX - _BODY_PAD_PX - _CARD_BORDER_PX
        if not self.tasks:
            # The card is also the way in: the second line names where tasks are
            # entered, and the block itself opens Tasks.
            row = self._cta_block(_t("No tasks"),
                                  _t("Click to add a task"))
            self._tasklist.pack_start(
                self._clickable(row, "tasks", tip=_t("Open Tasks")),
                False, False, 0)
            self._tasklist.show_all()
            self._progress.set_text("")
            return
        # Unfinished tasks first -- the actionable ones a demanding user wants on
        # the desktop, and so the cap never buries a pending task behind ticked
        # ones. The stable sort keeps each group in its store order.
        order = sorted(range(len(self.tasks)),
                       key=lambda i: self.tasks[i]["done"])
        # Cap to what FITS the real cell height (see _row_caps).
        cap = self._row_caps()[0]
        hidden = 0
        if len(order) > cap:
            hidden = len(order) - (cap - 1)
            order = order[:cap - 1]
        for i in order:
            t = self.tasks[i]
            # The whole row toggles the task (matching the design's full-row
            # target and the Tasks app). A visible-window EventBox is the styled,
            # opaque .taskrow surface: it draws the hairline + papertone reliably
            # on the framebuffer and, being the child widgets' parent window,
            # catches a click anywhere on the row -- including on the checkbox.
            hit = Gtk.EventBox()
            hit.get_style_context().add_class("taskrow")
            hit.set_can_focus(True)
            hit.set_tooltip_text(_t("Toggle task"))
            hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            row = Gtk.Box(spacing=12)
            row.get_style_context().add_class("taskrowbody")
            hit.add(row)
            chk = _Check(t["done"])
            self._task_checks[i] = chk
            row.pack_start(chk, False, False, 0)
            lbl = Gtk.Label(label=t["text"], xalign=0)
            lbl.get_style_context().add_class("tasktext")
            # a long task title must ellipsize, never widen the fixed column.
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(1)
            self._apply_task_style(lbl, t["done"])
            self._task_labels[i] = lbl
            row.pack_start(lbl, True, True, 0)
            hit.connect("button-press-event", self._on_task_row_press, i)
            hit.connect("key-press-event", self._on_task_row_key, i)
            self._tasklist.pack_start(hit, True, True, 0)
        if hidden:
            # "+3 more" is a promise that the rest is somewhere. Clicking it
            # has to take you there, or it is only an apology.
            self._tasklist.pack_start(
                self._clickable(self._more_row(hidden), "tasks",
                                tip=_t("Open Tasks")), True, True, 0)
        self._pad_rows(self._tasklist, len(order) + (1 if hidden else 0),
                       avail, _TASK_ROW_PX, cap)
        self._tasklist.show_all()
        self._update_progress()

    @staticmethod
    def _apply_task_style(lbl, done):
        """Reflect a task's done-state on its label: muted + struck through when
        done, plain otherwise. Idempotent, so it doubles as the in-place update."""
        ctx = lbl.get_style_context()
        attrs = Pango.AttrList()
        if done:
            ctx.add_class("done")
            attrs.insert(Pango.attr_strikethrough_new(True))
        else:
            ctx.remove_class("done")
        lbl.set_attributes(attrs)

    def _on_task_row_press(self, _w, ev, idx):
        # Clicking anywhere on the row toggles the task. Left button only, so a
        # stray right-click doesn't flip it.
        try:
            if ev.button == 1:
                self._toggle_task(idx)
                return True
        except Exception:
            pass
        return False

    def _on_task_row_key(self, _w, ev, idx):
        """Give the framebuffer-safe row the semantics of a native button."""
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._toggle_task(idx)
            return True
        return False

    @staticmethod
    def _find_task(disk, text, was_done, idx):
        """Where the task we are showing sits in the list that is ACTUALLY on
        disk right now. Prefers the same slot (the ordinary case), then a slot
        holding the same text in the state we last saw it in, then any slot
        with that text. None means it is not on disk at all -- it was edited or
        deleted elsewhere, and must not be written back."""
        if 0 <= idx < len(disk) and disk[idx]["text"] == text:
            return idx
        for i, t in enumerate(disk):
            if t["text"] == text and t["done"] == was_done:
                return i
        for i, t in enumerate(disk):
            if t["text"] == text:
                return i
        return None

    def _toggle_task(self, idx):
        # READ-MODIFY-WRITE against the file, never a blind write of the list
        # this card happens to be holding. self.tasks is a snapshot taken when
        # the desktop last came back, and the Tasks app can have written newer
        # tasks since. Writing the snapshot back would silently erase everything
        # added in between; applying the single change to what is on disk cannot.
        if not (0 <= idx < len(self.tasks)):
            return
        shown = self.tasks
        text, was = shown[idx]["text"], shown[idx]["done"]
        disk = self._load_tasks()
        pos = self._find_task(disk, text, was, idx)
        if pos is None:
            # gone from the store (renamed or deleted in the Tasks app): show
            # what is really there rather than re-creating what the user removed
            self.tasks = disk
            self._rebuild_tasks()
            return
        done = not was
        disk[pos]["done"] = done
        self._save_tasks(disk)
        same_shape = (len(disk) == len(shown)
                      and all(a["text"] == b["text"]
                              for a, b in zip(disk, shown)))
        self.tasks = disk
        if not same_shape:
            # tasks were added or removed elsewhere while this card was up: the
            # rows on screen no longer line up with the store, so rebuild from
            # it instead of restyling rows that mean something else now.
            self._rebuild_tasks()
            return
        lbl = self._task_labels.get(idx)
        if lbl is not None:
            self._apply_task_style(lbl, done)
        chk = self._task_checks.get(idx)
        if chk is not None:
            chk.set_done(done)          # repaints just the box, not the row
        self._update_progress()

    def _update_progress(self):
        done = sum(1 for t in self.tasks if t["done"])
        self._progress.set_text(_t("%d / %d done") % (done, len(self.tasks)))

    # -- Calendar card --
    def _calendar_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("card")
        now = time.localtime()
        y, m, today = now.tm_year, now.tm_mon, now.tm_mday

        events = self.events
        # this-month event marks + today's agenda, both from the real store.
        event_days = {e["ymd"][2] for e in events
                      if e["ymd"][0] == y and e["ymd"][1] == m}
        agenda = self._today_events()

        head = Gtk.Box(spacing=8)
        head.get_style_context().add_class("chead")
        title = Gtk.Label(label="%s %d" % (_t(MONTHS[m - 1]), y), xalign=0)
        title.get_style_context().add_class("ctitle")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(1)
        head.pack_start(title, True, True, 0)
        sub = Gtk.Label(label="%s %d" % (_t(WD_ABBR[now.tm_wday]), today),
                        xalign=1)
        sub.get_style_context().add_class("cmeta")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(1)
        head.pack_end(sub, False, False, 0)
        # the heading opens the Calendar on today, exactly as clicking a day
        # opens it on that day -- the card is a way in, not just a read-out.
        today_iso = "%04d-%02d-%02d" % (y, m, today)
        card.pack_start(
            self._clickable(head, "calendar", today_iso, _t("Open Calendar")),
            False, False, 0)

        # No row spacing: each day cell already carries its own height, and the
        # gap between week rows is the cheapest vertical space on the card.
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=0, column_spacing=2)
        grid.get_style_context().add_class("calgrid")
        weeks = _month_weeks(y, m)
        for c, wd in enumerate(WEEKDAYS):
            l = Gtk.Label(label=_t(wd))
            l.get_style_context().add_class("calwd")
            grid.attach(l, c, 0, 1, 1)
        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                if day is None:
                    continue
                lbl = Gtk.Label(label=str(day))
                # CENTER (not the default FILL) so today's red border-radius:50%
                # background is a tight circle, not a column-wide pill.
                lbl.set_halign(Gtk.Align.CENTER)
                lbl.set_valign(Gtk.Align.CENTER)
                ctx = lbl.get_style_context()
                ctx.add_class("calday")
                if day == today:
                    ctx.add_class("today")
                elif day in event_days:
                    ctx.add_class("hasev")
                # Clicking a day opens the Calendar app to that day. A windowless
                # EventBox (set_visible_window False) is an input-only click
                # target laid over the already-painted card surface, so it adds
                # NO window that could paint black on the no-compositor
                # framebuffer while still catching the press.
                hit = Gtk.EventBox()
                hit.set_visible_window(False)
                hit.set_halign(Gtk.Align.CENTER)
                hit.set_vexpand(True)
                hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                hit.add(lbl)
                iso = "%04d-%02d-%02d" % (y, m, day)
                hit.connect("button-press-event", self._on_day_press, iso)
                grid.attach(hit, c, r, 1, 1)
        # The month is the anchor of this card, so it takes the slack the card
        # has left over -- but only up to a comfortable week row. Past that the
        # card would be one enormous month with a footnote, so the surplus is
        # parked as blank paper at the foot of the card by an explicit filler.
        #
        # The stretch is done with VEXPAND, never a size request: a request
        # would change the grid's PREFERRED height, and that is the number
        # measure_widget_rows reads _GRID_ROW_PX / _GRID_PAD_PX back off. The
        # constants must keep describing the unstretched month.
        grid.set_vexpand(True)
        card.pack_start(grid, True, True, 0)

        cap = self._row_caps()[1]
        n_agenda = min(cap, len(agenda)) if agenda else (1 if cap else 0)
        natural = (_HEAD_PX + _CARD_BORDER_PX
                   + _GRID_WD_PX + len(weeks) * _GRID_ROW_PX + _GRID_PAD_PX
                   + (_AGSEC_PX + n_agenda * _AGENDA_ROW_PX if cap else 0))
        # ONE tile height, not the whole column: the calendar sits UNDER Tasks
        # now and gets half the column. Measured against _avail_h it stretched
        # its month grid to fill the entire board, and in a homogeneous column
        # that demand was then doubled -- the pinned pair asked for 1760px of a
        # 998px screen, and GTK resolved it by clipping both.
        spare = max(0, self._tile_h - natural)
        grid_share = min(spare, len(weeks) * (_GRID_TARGET_PX - _GRID_ROW_PX))

        if cap:
            sec = Gtk.Label(label=_t("TODAY"), xalign=0)
            sec.get_style_context().add_class("agsec")
            card.pack_start(sec, False, False, 0)
            if not agenda:
                # "No events" is technically true and completely inert. This
                # says the same thing in the user's words and offers the way in.
                empty = Gtk.Label(label=_t("Nothing scheduled today"), xalign=0)
                empty.get_style_context().add_class("agempty")
                empty.set_ellipsize(Pango.EllipsizeMode.END)
                empty.set_max_width_chars(1)
                card.pack_start(
                    self._clickable(empty, "calendar", today_iso,
                                    _t("Open Calendar")),
                    False, False, 0)
            else:
                # Cap a packed day so the agenda can't run off the fixed card;
                # the tail is summed into a "+N more" line.
                hidden = 0
                if len(agenda) > cap:
                    hidden = len(agenda) - (cap - 1)
                    agenda = agenda[:cap - 1]
                for ev in agenda:
                    row = Gtk.Box(spacing=12)
                    row.get_style_context().add_class("agrow")
                    tl = Gtk.Label(label=ev["time"], xalign=0)
                    tl.get_style_context().add_class("agtime")
                    tl.set_size_request(42, -1)
                    row.pack_start(tl, False, False, 0)
                    xl = Gtk.Label(label=ev["title"], xalign=0)
                    xl.get_style_context().add_class("agtext")
                    xl.set_ellipsize(Pango.EllipsizeMode.END)
                    xl.set_max_width_chars(1)
                    row.pack_start(xl, True, True, 0)
                    # Every part of this card opens the Calendar on the day it
                    # is about -- the heading, a day in the grid, and an event
                    # in the agenda. An event you cannot click is the one dead
                    # spot.
                    card.pack_start(
                        self._clickable(row, "calendar", today_iso,
                                        _t("Open Calendar")), False, False, 0)
                if hidden:
                    more = Gtk.Label(label=_t("+%d more") % hidden, xalign=0)
                    more.get_style_context().add_class("agempty")
                    more.set_ellipsize(Pango.EllipsizeMode.END)
                    more.set_max_width_chars(1)
                    card.pack_start(
                        self._clickable(more, "calendar", today_iso,
                                        _t("Open Calendar")), False, False, 0)
        # The blank paper below the agenda. Sized rather than expanded, so the
        # month grid above it -- the only vexpanding child -- takes exactly
        # `grid_share` of the slack and no more.
        if spare > grid_share:
            foot = Gtk.Box()
            foot.set_size_request(-1, spare - grid_share)
            card.pack_start(foot, False, False, 0)
        return card

    def _on_day_press(self, _w, ev, iso):
        # Left-click a mini-month day to open the Calendar app to that date; a
        # stray right-click is ignored so nothing launches unexpectedly.
        try:
            if ev.button == 1:
                self._open_calendar(iso)
                return True
        except Exception:
            pass
        return False

    def _open_calendar(self, iso):
        """Open the Calendar app on a given ISO 'YYYY-MM-DD' day."""
        self._launch("calendar", iso)

    def _on_board_press(self, _w, ev):
        """Right-click the desktop -> the board's own menu."""
        if getattr(ev, "button", 0) != 3:
            return False
        try:
            menu = Gtk.Menu()
            menu.get_style_context().add_class("boardmenu")
            for label, mod in ((_t("Widget Settings…"), "widgetsettings"),):
                item = Gtk.MenuItem(label=label)
                item.connect("activate",
                             lambda _i, m=mod: self._launch(m))
                menu.append(item)
            menu.show_all()
            # attach_to_widget so GTK tears the menu down with the board, and
            # nbapp.popup_at so it opens where the click was rather than at
            # the top-left of a full-desktop window.
            menu.attach_to_widget(self, None)
            nbapp.popup_at(menu, event=ev)
        except Exception:
            return False
        return True

    def _launch(self, mod, arg=None):
        """Launch a DE app the same way the desktop spawns every app --
        python3 <DE_DIR>/<mod>.py with PYTHONPATH pinned to DE_DIR -- optionally
        handing it an argv[1]. A failed launch (missing python3 or module)
        degrades silently, never crashing the desktop board."""
        argv = ["python3", os.path.join(DE_DIR, mod + ".py")]
        if arg:
            argv.append(arg)
        try:
            subprocess.Popen(argv, env=dict(os.environ, PYTHONPATH=DE_DIR))
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    _css()
    w = Widgets()
    w.connect("destroy", Gtk.main_quit)
    w.show_all()
    Gtk.main()
