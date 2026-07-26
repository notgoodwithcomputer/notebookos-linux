#!/usr/bin/env python3
"""
widgets.py — the desktop-home widget column (right side of the Finder).

Per the imported design (assets/designs/screenshots/finder2.png), the desktop
home is the floating Finder on the left and a column of two cards on the right:

  • Tasks   — a checklist with an "N/M done" progress read-out
  • Calendar — the current month grid (today circled in signage red) plus a
               TODAY agenda of the day's events

Both cards show REAL data read from the shared stores the Tasks and Calendar
apps write — nothing is seeded or fabricated. Tasks come from
$NB_HOME/.config/notebook/tasks.json (the flat {"text","done"} list shared with
tasks.py, so ticking one here writes back and sticks); today's agenda and the
month's event dots come from $NB_HOME/.config/notebook/calendar.json (the flat
[{date,start,end,title,cal}] list Calendar writes). Empty or missing stores
render a concise technical empty-state ("No tasks", "No events"). The stores are
re-read whenever the desktop home returns after a fullscreen app closes, so
edits made in those apps appear here.

Design language: Nimbus Sans for the interface and card titles, a warm serif
(Newsreader / Liberation Serif) for the agenda's event titles — the one
editorial moment — signage red #C8341E only for today and alerts, papertone
surfaces, near-black hairline frames (matching the Finder).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio, Pango  # noqa: E402

import os
import json
import time
import datetime
import subprocess

import nbapp  # shared base: nbapp.screen_size() gives the REAL primary-monitor
from nbi18n import _t  # noqa: E402
              # size (never a hardcoded 1920x1080) for sizing this column.

# cairo is used to draw the task checkbox as a flat vector box (see _Check).
# Guarded so a construction on a stripped image can never hard-fail on the
# import; the checkbox degrades to a plain box drawn without round caps.
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

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# the DE scripts live beside this file; the Calendar app is launched the same
# way the rest of the desktop spawns apps — python3 <DE_DIR>/calendar.py with
# PYTHONPATH pinned to DE_DIR (see music.py / contacts.py).
DE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
TASKS_FILE = os.path.join(CFG_DIR, "tasks.json")        # shared flat task list
CAL_FILE = os.path.join(CFG_DIR, "calendar.json")       # Calendar app's events
# the desktop-home column belongs to the desktop, not on top of a running app.
# A launcher drops this flag file while a fullscreen app owns the screen; we
# hide while it exists and reappear when the desktop home returns.
APP_FLAG = "/tmp/nb-app-active"
# The ref-count dir nbapp writes one file per live app pid into. Checking it
# directly (is any pid still in /proc) is more reliable than trusting the flag
# file, which can be left stale by a crashed app or briefly missing.
APP_DIR = "/tmp/nb-apps"

PANEL_H = 46
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")
WEEKDAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
# Monday-first abbreviations for the card sub-header, formatted by index so we
# never touch strftime("%-d") (a glibc-only flag) or the stdlib-shadowed
# calendar module.
WD_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# This is a summary card on a FIXED-height column, not the full app. Cap how many
# rows each card renders so a long task list or a packed day can never push the
# other card off the bottom of the column (it can't scroll). When a list is
# longer than the cap the final row is a muted "+N more" read-out; the complete
# list stays available in the Tasks / Calendar apps. The cap counts that
# read-out row, so the rendered height is bounded either way.
MAX_TASK_ROWS = 6
MAX_AGENDA_ROWS = 6

# The column is a FIXED-height, NON-scrolling stack of two cards. On a real
# panel (which is NOT 1920x1080 — often 1366x768) the column is much shorter, so
# the MAX_* caps above — tuned for a tall screen — would push the calendar's
# agenda off the bottom of the display. _row_caps() trims the rows to what
# actually FITS the live column height using these per-element pixel figures.
# They are applied before anything is laid out (nothing is measurable when a
# card is built), but they are MEASURED values, not guesses: an earlier set was
# generous by up to a third, and combined with budgeting for the maximum number
# of rows rather than the real one it left a 768px panel showing a single task
# above 120px of empty desktop.
_HEAD_PX = 51         # a card header (.chead: 16+16 padding + 17px title + rule)
_TASK_ROW_PX = 55     # a .taskrow (17+17 padding + the 21px checkbox)
_MORE_ROW_PX = 34     # the quieter "+N more" tail row (.moretail)
_AGENDA_ROW_PX = 33   # an .agrow (6+6 padding + the 18px serif title)
# A card with nothing in it still shows a line ("No tasks" / "No events"), so an
# EMPTY card costs height too. Counting it as zero let a long task list next to
# an empty agenda take one row more than fits.
_TASK_EMPTY_PX = 40   # the .emptyrow "No tasks" line
_AGENDA_EMPTY_PX = 30  # the .agempty "No events" line
_GRID_WD_PX = 15      # the weekday header row of the month grid
_GRID_ROW_PX = 45     # one week of the month grid (32px day cell + dot + gap)
_GRID_PAD_PX = 18     # .calgrid vertical padding
_AGSEC_PX = 29        # the "TODAY" section label (+ its rule and padding)
_COL_CHROME_PX = 48   # column margin/spacing + tasklist padding + card borders

WIDGETS_CSS = b"""
/* fill the whole column window with the desktop papertone: with no compositor
   a transparent window paints black in the gaps between/below the cards. */
.wcol { background: #DED4C2; }
/* the flat-Swiss card: warm paper on a near-black hairline frame, matching the
   Finder window's frame (de/finder.py). The near-black is a structural frame,
   never a decorative accent; signage red is reserved for today + alerts. */
.card { background: #F8F7F2; border: 1px solid #1A1916; }
.card .chead { padding: 16px 20px; border-bottom: 1px solid #1A1916; }
.ctitle { font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 17px;
          font-weight: 700; letter-spacing: 0.02em; color: #1A1916; }
.cmeta  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 13px; color: #6E695E; }
.tasklist { padding: 4px 20px 14px 20px; }
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
.taskrowbody { padding: 17px 0; }
/* the "+N more" tail: a quieter line than a task row, not a full-height one */
.moretail { padding: 10px 0; }
.emptyrow { padding: 12px 0; }
.tasktext { font-family: "Nimbus Sans","Helvetica",sans-serif;
            font-size: 15px; color: #2A2620; }
.tasktext.done { color: #A8A296; }
.emptytext { font-family: "Nimbus Sans","Helvetica",sans-serif;
             font-size: 15px; color: #6E695E; }
/* the one line under an empty card's heading that says what to do about it:
   quieter than the state above it, so it reads as guidance, not as content. */
.emptyhint { font-family: "Nimbus Sans","Helvetica",sans-serif;
             font-size: 13px; color: #9A9484; }
/* the "+N more" overflow read-out shown when a card is longer than its cap. */
.moretext { font-family: "Nimbus Sans","Helvetica",sans-serif;
            font-size: 13px; color: #6E695E; }
.calgrid { padding: 8px 20px 10px 20px; }
.calwd  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 11px; font-weight: 600; color: #6E695E; letter-spacing: 0.06em; }
.calday { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 14px; color: #2A2620; min-width: 32px; min-height: 32px; }
.calday.today { background: #C8341E; color: #FFFFFF; border-radius: 50%;
                font-weight: 700; }
/* a day that carries an event is marked with a neutral ink dot, NOT signage
   red -- red is reserved for today + alerts (see the docstring). This matches
   the Tasks app's mini-calendar, where a day with an event is bold ink. */
.caldot { color: #2A2620; font-size: 10px; }
.agsec  { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 11px; color: #6E695E; letter-spacing: 0.14em;
          padding: 12px 20px 4px 20px; border-top: 1px solid #D7D2C5; }
.agrow  { padding: 6px 20px; }
.agtime { font-family: "Nimbus Sans","Helvetica",sans-serif;
          font-size: 13px; font-weight: 600; color: #1A1916; }
.agtext { font-family: "Newsreader","Liberation Serif","Georgia",serif;
          font-size: 18px; color: #2A2620; }
.agempty { font-family: "Nimbus Sans","Helvetica",sans-serif;
           font-size: 15px; color: #6E695E; padding: 6px 20px 8px 20px; }
"""


def _css():
    prov = Gtk.CssProvider()
    prov.load_from_data(WIDGETS_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _month_weeks(year, month):
    """Weeks (Mon-first) of `month` as lists of 7 ints/None."""
    try:
        first = datetime.date(year, month, 1)
        nxt = datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)
        lead, ndays = first.weekday(), (nxt - first).days
    except Exception:
        # A wildly out-of-range system clock (e.g. a dead RTC reporting
        # year 9999, which pushes date() past MAXYEAR) must never blank the
        # desktop widget column; fall back to a plain 30-day, Monday-start
        # grid — mirrors tasks.py's mini-calendar hardening.
        lead, ndays = 0, 30
    cells = [None] * lead + list(range(1, ndays + 1))
    while len(cells) % 7:
        cells.append(None)
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


class _Check(Gtk.DrawingArea):
    """The task checkbox, drawn with cairo instead of a native Gtk.CheckButton.

    On the GPU-less software framebuffer a themed check indicator can paint
    blank, garbage or at the wrong size when a theme's assets are missing; a
    cairo box is deterministic and matches the flat-Swiss design exactly — a
    21px square: a grey hairline outline when open, ink-filled (#1A1916) with a
    white tick when done. It draws from the LIVE allocation (never a hardcoded
    size), no-ops on a not-yet-allocated 0x0 area, paints an opaque paper base
    so no stray pixel shows through on the framebuffer, and only repaints when
    its state actually flips (queue_draw is never called on a timer)."""

    SIZE = 21

    def __init__(self, done):
        super().__init__()
        self._done = bool(done)
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def set_done(self, done):
        """Flip the tick in place, repainting only this 21px box (not the row)."""
        done = bool(done)
        if done != self._done:
            self._done = done
            self.queue_draw()

    def _draw(self, _area, cr):
        try:
            w = self.get_allocated_width()
            h = self.get_allocated_height()
            if w <= 0 or h <= 0:            # not yet allocated — nothing to paint
                return False
            # opaque base first, so the corners outside the square never show a
            # black/garbage pixel on the compositor-less framebuffer.
            cr.set_source_rgb(*_PAPER)
            cr.paint()
            # a tight square centred in whatever the row actually allocated us.
            side = min(w, h)
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
                # white tick — the design's check path (M5 12.5 L10 17.5 L19 7 on
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


class Widgets(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        _css()
        self.set_decorated(False)
        # WM_NAME the window manager keys on: the matchbox patch
        # (0004-desktop-widget-column-below-windows) pins any DIALOG with this
        # title to the very bottom of the stack, just above the wallpaper and
        # below every app and the Finder — so the cards can NEVER render in
        # front of a window. The keep-below / re-lower / hide-when-active code
        # below is now only a best-effort backstop, not the guarantee.
        # NOT translated: this is the name the WM patch matches on, not a
        # label anyone reads. A translated title would silently stop matching
        # and let the cards float above real windows.
        self.set_title("nb-desktop-widgets")
        nbapp.force_opaque_visual(self)   # see nbapp: no RGBA visual
        # DIALOG so matchbox floats this column at its requested size/position
        # (620 x band, pinned to the right edge) instead of stretching it to
        # fill the screen like a DESKTOP-hint background. Layering ("widgets
        # don't cover apps") is handled by _poll_home hiding the column while a
        # fullscreen app owns the screen.
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        # ...but PINNED TO THE DESKTOP LAYER. The hide-while-an-app-is-active
        # rule above only covers apps that set the app-active flag; the Finder
        # is desktop furniture like this column and never sets it. Both are
        # DIALOGs, so clicking the widget column made the window manager raise
        # it — and the Tasks/Calendar cards then sat ON TOP of the Finder (and
        # of any window that had not claimed the flag). This column is part of
        # the desktop home and must never come forward, so it is kept below and
        # re-lowered whenever something tries to raise it.
        self.set_keep_below(True)
        self.connect("map-event", self._stay_down)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._stay_down)
        # Right column, below the menu bar, mirroring the Finder's vertical band.
        # Size + position it against the ACTUAL screen size — real hardware panels
        # are not 1920x1080, and a hardcoded x/height pushed the whole column off
        # the right edge (and off the bottom) of a smaller display. nbapp.screen_size()
        # returns the real primary-monitor pixels (never a literal 1920x1080).
        sw, sh = nbapp.screen_size()
        w = min(620, max(320, sw // 3))
        h = max(360, sh - PANEL_H - 40)
        # The live column height caps how many rows each card renders so the
        # pair always fits this non-scrolling column on the real panel.
        self._avail_h = h
        self.set_default_size(w, h)
        self.move(max(0, sw - w - 40), PANEL_H + 16)
        self.get_style_context().add_class("wcol")

        self.tasks = self._load_tasks()
        # Both stores are read up front: the tasks card is built first but its
        # row budget depends on how many events today actually holds (see
        # _row_caps), so the calendar's data cannot wait for its own card.
        self.events = self._load_events()

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        col.set_margin_top(4)
        self.add(col)
        self._col = col
        col.pack_start(self._tasks_card(), False, False, 0)
        self._cal_card = self._calendar_card()
        self._cal_day = time.localtime()[:3]   # (year, mon, mday) it was built for
        col.pack_start(self._cal_card, False, False, 0)

        GLib.timeout_add(2000, self._ensure_mapped)
        GLib.timeout_add(6000, self._ensure_mapped)
        # Watch the app-active flag with an inotify-backed file monitor instead
        # of stat-polling it ~2.5x a second forever. The flag only flips when an
        # app opens or closes, so a poll spends the entire session waking the CPU
        # to learn nothing — and this desktop may be rendering every pixel in
        # software, where those wakes compete with actual drawing. finder.py
        # already made exactly this change; this mirrors it.
        self._app_flag_monitor = None
        try:
            _flag = Gio.File.new_for_path(APP_FLAG)
            self._app_flag_monitor = _flag.monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._app_flag_monitor.connect(
                "changed", lambda *_a: (self._poll_home(), False)[1])
        except Exception:
            pass
        # Reconcile once after start: covers a flag already present when the
        # widget column (re)launches, which produces no future monitor event.
        GLib.timeout_add(500, lambda: (self._poll_home(), False)[1])
        # Periodic backstop (every 2s, ~0.5 Hz — negligible, not the old 2.5 Hz
        # wake): the Gio monitor can MISS a flag create/delete, and the app-flag
        # itself is best-effort, so without this the column can be left showing
        # over a running app OR sitting above a window. _poll_home also
        # re-asserts keep-below each tick, so the column can never drift on top
        # of a real window even if a raise slipped past _stay_down.
        GLib.timeout_add_seconds(2, self._poll_home)
        # rebuild the calendar when the day rolls over so the circled day, date
        # header and TODAY agenda stay correct if the OS runs across midnight.
        GLib.timeout_add_seconds(60, self._check_day_rollover)

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
        """True if a real app process is alive — read the ref-count dir and
        confirm at least one pid is still in /proc, rather than trusting the
        (best-effort, sometimes-stale) flag file. Falls back to the flag if the
        dir is unreadable."""
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
        # and — crucially — keep the column BELOW every real window whenever it
        # is shown, so a window is never rendered beneath it.
        try:
            active = self._app_active()
            if active and self.get_visible():
                self.hide()
            elif not active and not self.get_visible():
                # desktop home is returning (a fullscreen app closed) — re-read
                # the shared stores so a task/event added or edited in the Tasks
                # or Calendar app shows here, then show, then LOWER (show_all
                # maps the window fresh and matchbox stacks it on top; we must
                # not depend on map-event alone to re-lower it).
                self._reload()
                self.show_all()
                self._stay_down()
            elif not active and self.get_visible():
                # already desktop home — re-assert the desktop layer so the
                # column stays under the Finder even if a raise slipped past
                # _stay_down (a window must never end up beneath it).
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

    def _reload(self):
        # Re-read tasks.json + calendar.json and rebuild both cards from the
        # live stores. Crash-safe — a bad store must never break the desktop.
        try:
            self.tasks = self._load_tasks()
            self.events = self._load_events()
            self._rebuild_tasks()
            self._rebuild_calendar()
        except Exception:
            pass

    def _rebuild_calendar(self):
        # Swap the calendar card for a freshly built one (current circled day,
        # this-month event dots, today's agenda), tracking the day it is for.
        self._cal_day = time.localtime()[:3]
        try:
            self._col.remove(self._cal_card)
        except Exception:
            pass
        self._cal_card = self._calendar_card()
        self._col.pack_start(self._cal_card, False, False, 0)
        self._cal_card.show_all()

    def _check_day_rollover(self):
        # The calendar card computes "today" once when built; if the OS is left
        # running across midnight, rebuild it so the circled day, date header
        # and TODAY agenda track the real date instead of the boot day.
        try:
            if time.localtime()[:3] != self._cal_day:
                self._rebuild_calendar()
        except Exception:
            pass
        return True   # keep checking every minute

    # -- stores --
    def _load_tasks(self):
        """Read the shared flat task list (tasks.json: [{text, done}, ...]) the
        Tasks app writes. Nothing is seeded — a missing / unreadable / empty
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
        parsed by plain int split — NEVER import calendar / time.strptime (the
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
        events (start_min is None) sort last. Read by BOTH the calendar card and
        the row budget, so it must not depend on either being built."""
        now = time.localtime()
        ymd = (now.tm_year, now.tm_mon, now.tm_mday)
        agenda = [e for e in getattr(self, "events", []) if e["ymd"] == ymd]
        agenda.sort(key=lambda e: (e["start_min"] is None,
                                   e["start_min"] if e["start_min"] is not None
                                   else 0))
        return agenda

    @staticmethod
    def _rows_px(n, cap, row_px, tail_px, empty_px):
        """Height a card's row area really takes for `n` items under `cap`.
        Over the cap the last slot becomes the shorter "+N more" line, which is
        exactly how _rebuild_tasks / _calendar_card render it; with nothing to
        show the card still draws its one-line empty state, not nothing."""
        if n <= 0:
            return empty_px
        if n > cap:
            return (cap - 1) * row_px + tail_px
        return n * row_px

    def _row_caps(self):
        """(task_rows, agenda_rows) the two cards may render so the pair FITS the
        fixed, non-scrolling column on the REAL panel. The column height comes
        from the live monitor (never 1920x1080), so on a short panel (e.g. 768px)
        this trims rows instead of pushing the calendar's agenda off the bottom
        of the screen; a tall panel keeps the full MAX_* caps. Called only when a
        card is (re)built — never per frame — so it costs nothing to redraw."""
        try:
            avail = int(self._avail_h)
        except Exception:
            avail = 994
        now = time.localtime()
        weeks = len(_month_weeks(now.tm_year, now.tm_mon))
        # Fixed (non-row) chrome: both headers, the month grid, the agenda label,
        # and the column's margins/paddings. Whatever's left is the row budget.
        fixed = (2 * _HEAD_PX + _GRID_WD_PX + weeks * _GRID_ROW_PX + _GRID_PAD_PX
                 + _AGSEC_PX + _COL_CHROME_PX)
        budget = avail - fixed
        # Start from what each card actually HAS to show, never from the maxima.
        # Budgeting for six agenda rows on a day that holds one event used to
        # spend 165px of a 768px panel's column on nothing, which starved the
        # task list to a single row and still left the column short of the
        # bottom of the screen.
        n_tasks = len(self.tasks)
        n_events = len(self._today_events())
        task_cap = min(MAX_TASK_ROWS, max(1, n_tasks))
        ag_cap = min(MAX_AGENDA_ROWS, max(1, n_events))

        def used(tc, ac):
            return (self._rows_px(n_tasks, tc, _TASK_ROW_PX, _MORE_ROW_PX,
                                  _TASK_EMPTY_PX)
                    + self._rows_px(n_events, ac, _AGENDA_ROW_PX,
                                    _AGENDA_ROW_PX, _AGENDA_EMPTY_PX))
        # Trim the taller consumer first until the pair fits, flooring at 2 rows
        # each (any real panel is >= ~720px tall, which clears that floor).
        while used(task_cap, ag_cap) > budget:
            t_px = self._rows_px(n_tasks, task_cap, _TASK_ROW_PX, _MORE_ROW_PX,
                                 _TASK_EMPTY_PX)
            a_px = self._rows_px(n_events, ag_cap, _AGENDA_ROW_PX,
                                 _AGENDA_ROW_PX, _AGENDA_EMPTY_PX)
            if task_cap > 2 and t_px >= a_px:
                task_cap -= 1
            elif ag_cap > 2:
                ag_cap -= 1
            elif task_cap > 2:
                task_cap -= 1
            else:
                break
        return task_cap, ag_cap

    def _clickable(self, child, mod, arg=None, tip=None):
        """Wrap `child` so clicking it opens the app that owns this card.

        A WINDOWLESS EventBox (visible_window False): it is an input-only layer
        over the already-painted card, so it adds no window that could scan out
        black on the no-compositor framebuffer — the same trick the mini-month
        day cells use."""
        hit = Gtk.EventBox()
        hit.set_visible_window(False)
        hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        hit.add(child)
        if tip:
            hit.set_tooltip_text(tip)
        hit.connect("button-press-event", self._on_open_press, mod, arg)
        return hit

    def _on_open_press(self, _w, ev, mod, arg):
        try:
            if ev.button == 1:
                self._launch(mod, arg)
                return True
        except Exception:
            pass
        return False

    # -- Tasks card --
    def _tasks_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("card")

        head = Gtk.Box()
        head.get_style_context().add_class("chead")
        title = Gtk.Label(label=_t("Tasks"), xalign=0)
        title.get_style_context().add_class("ctitle")
        head.pack_start(title, False, False, 0)
        self._progress = Gtk.Label(xalign=1)
        self._progress.get_style_context().add_class("cmeta")
        head.pack_end(self._progress, False, False, 0)
        # The card could tick a task but never ADD one — the app that owns it
        # was unreachable from the desktop. Its heading now opens it.
        card.pack_start(self._clickable(head, "tasks", tip=_t("Open Tasks")),
                        False, False, 0)

        self._tasklist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._tasklist.get_style_context().add_class("tasklist")
        card.pack_start(self._tasklist, False, False, 0)
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
        if not self.tasks:
            # An empty card that says only "No tasks" is a wasted quarter of
            # the desktop on the day someone first switches the machine on. Say
            # what to do about it, and be the thing that does it.
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            row.get_style_context().add_class("emptyrow")
            empty = Gtk.Label(label=_t("Nothing to do yet"), xalign=0)
            empty.get_style_context().add_class("emptytext")
            row.pack_start(empty, False, False, 0)
            hint = Gtk.Label(label=_t("Open Tasks to write your first one"),
                             xalign=0)
            hint.get_style_context().add_class("emptyhint")
            row.pack_start(hint, False, False, 0)
            self._tasklist.pack_start(
                self._clickable(row, "tasks", tip=_t("Open Tasks")),
                False, False, 0)
            self._tasklist.show_all()
            self._progress.set_text("")
            return
        # Unfinished tasks first — the actionable ones a demanding user wants on
        # the desktop, and so the cap never buries a pending task behind ticked
        # ones. The stable sort keeps each group in its store order. Ticking a
        # row updates it in place (never reorders under the cursor), so this only
        # re-sorts on a full reload.
        order = sorted(range(len(self.tasks)),
                       key=lambda i: self.tasks[i]["done"])
        # Cap to what FITS the real column height (see _row_caps): on a short
        # panel show fewer so the calendar card below can never be pushed off the
        # bottom of the screen. On a tall panel this stays at MAX_TASK_ROWS.
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
            # catches a click anywhere on the row — including on the checkbox.
            hit = Gtk.EventBox()
            hit.get_style_context().add_class("taskrow")
            hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            row = Gtk.Box(spacing=14)
            row.get_style_context().add_class("taskrowbody")
            hit.add(row)
            chk = _Check(t["done"])
            self._task_checks[i] = chk
            row.pack_start(chk, False, False, 0)
            lbl = Gtk.Label(label=t["text"], xalign=0)
            lbl.get_style_context().add_class("tasktext")
            # a long task title must ellipsize, never widen the fixed column.
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            self._apply_task_style(lbl, t["done"])
            self._task_labels[i] = lbl
            row.pack_start(lbl, True, True, 0)
            hit.connect("button-press-event", self._on_task_row_press, i)
            self._tasklist.pack_start(hit, False, False, 0)
        if hidden:
            row = Gtk.Box()
            row.get_style_context().add_class("moretail")
            more = Gtk.Label(label=_t("+%d more") % hidden, xalign=0)
            more.get_style_context().add_class("moretext")
            row.pack_start(more, True, True, 0)
            self._tasklist.pack_start(row, False, False, 0)
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

    @staticmethod
    def _find_task(disk, text, was_done, idx):
        """Where the task we are showing sits in the list that is ACTUALLY on
        disk right now. Prefers the same slot (the ordinary case), then a slot
        holding the same text in the state we last saw it in, then any slot
        with that text. None means it is not on disk at all — it was edited or
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
        # the desktop last came back (see _poll_home), and the Tasks app can
        # have written newer tasks since — the app-active flag it is keyed on
        # clears before that app has finished saving. Writing the snapshot back
        # would silently erase everything added in between; applying the single
        # change to what is on disk cannot. (Same pattern tasks.py uses for
        # calendar.json.)
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
            chk.set_done(done)          # repaints just the 21px box, not the row
        self._update_progress()

    def _update_progress(self):
        done = sum(1 for t in self.tasks if t["done"])
        self._progress.set_text("%d / %d done" % (done, len(self.tasks)))

    # -- Calendar card --
    def _calendar_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("card")
        now = time.localtime()
        y, m, today = now.tm_year, now.tm_mon, now.tm_mday

        events = self.events
        # this-month event dots + today's agenda, both from the real store.
        event_days = {e["ymd"][2] for e in events
                      if e["ymd"][0] == y and e["ymd"][1] == m}
        agenda = self._today_events()

        head = Gtk.Box()
        head.get_style_context().add_class("chead")
        title = Gtk.Label(label="%s %d" % (MONTHS[m - 1], y), xalign=0)
        title.get_style_context().add_class("ctitle")
        head.pack_start(title, False, False, 0)
        sub = Gtk.Label(label="%s %d %s" % (WD_ABBR[now.tm_wday], today, MONTHS[m - 1]),
                        xalign=1)
        sub.get_style_context().add_class("cmeta")
        # Ellipsize + take the leftover width so a long date ("Wed 30 September")
        # can never force the card wider than a narrow column and off the right
        # edge of the panel; it still right-aligns, so short dates look unchanged.
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        head.pack_end(sub, True, True, 0)
        # the heading opens the Calendar on today, exactly as clicking a day
        # opens it on that day — the card is a way in, not just a read-out.
        today_iso = "%04d-%02d-%02d" % (y, m, today)
        card.pack_start(
            self._clickable(head, "calendar", today_iso, _t("Open Calendar")),
            False, False, 0)

        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=2)
        grid.get_style_context().add_class("calgrid")
        for c, wd in enumerate(WEEKDAYS):
            l = Gtk.Label(label=wd)
            l.get_style_context().add_class("calwd")
            grid.attach(l, c, 0, 1, 1)
        for r, week in enumerate(_month_weeks(y, m), start=1):
            for c, day in enumerate(week):
                if day is None:
                    continue
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                cell.set_halign(Gtk.Align.CENTER)
                lbl = Gtk.Label(label=str(day))
                # CENTER (not the default FILL) so today's red border-radius:50%
                # background is a tight circle, not a column-wide pill.
                lbl.set_halign(Gtk.Align.CENTER)
                lbl.set_valign(Gtk.Align.CENTER)
                lbl.get_style_context().add_class("calday")
                if day == today:
                    lbl.get_style_context().add_class("today")
                cell.pack_start(lbl, False, False, 0)
                dot = Gtk.Label(label="•" if (day in event_days and day != today) else " ")
                dot.set_halign(Gtk.Align.CENTER)
                dot.get_style_context().add_class("caldot")
                cell.pack_start(dot, False, False, 0)
                # Clicking a day opens the Calendar app to that day. A windowless
                # EventBox (set_visible_window False) is an input-only click
                # target laid over the already-painted card surface, so it adds
                # NO window that could paint black on the no-compositor
                # framebuffer while still catching the press.
                hit = Gtk.EventBox()
                hit.set_visible_window(False)
                hit.set_halign(Gtk.Align.CENTER)
                hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                hit.add(cell)
                iso = "%04d-%02d-%02d" % (y, m, day)
                hit.connect("button-press-event", self._on_day_press, iso)
                grid.attach(hit, c, r, 1, 1)
        card.pack_start(grid, False, False, 0)

        sec = Gtk.Label(label=_t("TODAY"), xalign=0)
        sec.get_style_context().add_class("agsec")
        card.pack_start(sec, False, False, 0)
        if not agenda:
            # "No events" is technically true and completely inert. This says
            # the same thing in the user's words and offers the way in.
            empty = Gtk.Label(label=_t("Nothing scheduled today"), xalign=0)
            empty.get_style_context().add_class("agempty")
            card.pack_start(
                self._clickable(empty, "calendar", today_iso,
                                _t("Open Calendar")),
                False, False, 0)
        else:
            # Cap a packed day so the agenda can't run off the fixed column; the
            # tail is summed into a "+N more" line (full day in the Calendar app).
            cap = self._row_caps()[1]
            hidden = 0
            if len(agenda) > cap:
                hidden = len(agenda) - (cap - 1)
                agenda = agenda[:cap - 1]
            for ev in agenda:
                row = Gtk.Box(spacing=16)
                row.get_style_context().add_class("agrow")
                tl = Gtk.Label(label=ev["time"], xalign=0)
                tl.get_style_context().add_class("agtime")
                tl.set_size_request(48, -1)
                row.pack_start(tl, False, False, 0)
                xl = Gtk.Label(label=ev["title"], xalign=0)
                xl.get_style_context().add_class("agtext")
                xl.set_ellipsize(Pango.EllipsizeMode.END)
                row.pack_start(xl, True, True, 0)
                card.pack_start(row, False, False, 0)
            if hidden:
                more = Gtk.Label(label=_t("+%d more") % hidden, xalign=0)
                more.get_style_context().add_class("agempty")
                card.pack_start(more, False, False, 0)
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

    def _launch(self, mod, arg=None):
        """Launch a DE app the same way the desktop spawns every app —
        python3 <DE_DIR>/<mod>.py with PYTHONPATH pinned to DE_DIR — optionally
        handing it an argv[1]. A failed launch (missing python3 or module)
        degrades silently, never crashing the desktop widget column."""
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
