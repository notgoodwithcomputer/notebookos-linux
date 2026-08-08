#!/usr/bin/env python3
"""
Calendar — the Notebook OS month/week/day calendar (native GTK).

A month grid beside a sidebar: a mini-month, a user-managed list of named
calendars (add / delete, each with a color, toggleable), and a New Event
control. The main area carries the period title, ‹ › navigation, a Today jump,
and a Day/Week/Month view toggle. Every event belongs to a named calendar.

Ships empty: no seeded events, and a single default calendar ("Personal"). The
File menu operates on user-chosen documents under $NB_HOME/Documents (New /
Open / Save / Save As); events auto-persist to $NB_HOME/.config/notebook/
calendar.json for session recovery, and named calendars to calendars.json in
the same directory. calendar.json keeps the flat [{date,start,end,title,cal}]
shape that tasks.py / widgets.py read.

Date arithmetic is done with plain int math — this file is named calendar.py
and shadows the stdlib `calendar` module on PYTHONPATH, so `import calendar`
and `time.strptime` are never used here.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import math
import os
import re
import sys
import json
import copy
import subprocess
from datetime import date, timedelta

import cairo

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402
# _upper, not str.upper: monotonic Greek DROPS the tonos in capitals (ΥΛΙΚΑ, not
# ΥΛΙΚΆ) and Python's upper() keeps it. Used for the short weekday headers.
from nbi18n import _upper  # noqa: E402


def _monthrange(y, m):
    """(weekday of the 1st, Monday=0; number of days in month) — no stdlib
    `calendar` import, which this file's name would otherwise shadow."""
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first.weekday(), (nxt - first).days


_ID_SEQ = [0]


def _gen_id():
    """A stable, reasonably-unique per-event id. Prefers os.urandom and falls
    back to a pid+counter token — never imports a new module (this file shadows
    the stdlib `calendar`, and time.strptime is likewise avoided)."""
    try:
        return os.urandom(8).hex()
    except Exception:
        _ID_SEQ[0] += 1
        return "e%d-%d" % (os.getpid(), _ID_SEQ[0])


INK = "#1A1916"
ACCENT = "#C8341E"          # signage-red: today / active caret / alerts only
# Darker-beige used for selected-state chrome (never black, per the design
# language) — the sole ring around the chosen color swatch.
SELECT_RING = "#8A857A"

# Persist to $NB_HOME/.config/notebook/, matching the widgets.py / tasks.py
# pattern. Events go to calendar.json (the flat list tasks.py/widgets.py read);
# the named-calendar definitions go to calendars.json. Both are rewritten on
# every relevant change and on close. User-chosen File-menu documents live under
# $NB_HOME/Documents.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
EVENTS_FILE = os.path.join(CFG_DIR, "calendar.json")
CALENDARS_FILE = os.path.join(CFG_DIR, "calendars.json")


def _quarantine_store(path):
    """Move a store this app could not read AS ITS OWN aside, under the same
    <name>.damaged-<stamp> name nbapp.preserve_damaged uses. nbapp quarantines
    a store that fails to PARSE on every write; it deliberately cannot cover
    this case — valid JSON of the wrong shape parses perfectly, and only this
    app knows the shape is not a calendar. Without this, the next flush would
    write a fresh store straight over whatever the file really held."""
    import time
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
    except OSError:
        pass
# The Academics app's store. Class meetings are MIRRORED onto this calendar,
# never copied into it: Academics owns them, so they are rebuilt from that file
# on every load and are deliberately absent from calendar.json. That keeps one
# writer per store, and means editing your timetable in Academics is the only
# way a class time can change — there is no second copy to drift.
ACADEMICS_FILE = os.path.join(CFG_DIR, "academics.json")
# The same store under the name it carried when the app was called Academic
# Notes; academics.py reads it as a fallback and so must every mirror of it.
ACADEMICS_LEGACY = os.path.join(CFG_DIR, "academic.json")
# The calendar mirrored classes appear on. Reserved: it is not in calendars.json
# and cannot be renamed or deleted, because it is not really a calendar of the
# user's, it is a window onto another app.
CLASSES_CAL = "Classes"
# Work shifts live on their own calendar rather than in a separate app: a job
# needs nothing from a scheduler that a calendar does not already do, so a
# shift is an ordinary event with a shift-shaped way of entering it. Created on
# demand the first time a shift is added, and colour-fast so a rota reads as a
# block at a glance.
WORK_CAL = "Work"
WORK_COLOR = "#417E74"
# How far either side of this week the weekly class pattern is expanded into
# real dates. Bounded on purpose: a timetable is a repeating rule, and turning
# it into occurrences forever would grow without limit.
_CLASS_WEEKS_BACK = 8
_CLASS_WEEKS_AHEAD = 26
DOCUMENTS = os.path.join(HOME, "Documents")

# Custom dialog response for the Event detail's Delete button (positive so it
# never collides with the negative Gtk.ResponseType built-ins).
RESPONSE_DELETE = 20

# Fixed palette a new calendar's color is chosen from — muted, neutral tones.
# Bounded so chip border-colors map to a small set of CSS classes (see
# _install_css / _color_class). Deliberately NOT the signage red: that one is
# reserved for today (and for alerts), and a calendar painted in it put a
# second, competing #C8341E on the same screen as the today marker.
def _fmt_hours(hours):
    """A shift length said the way a person would say it: "8 hours",
    "7 hours 30 minutes", "45 minutes"."""
    total = int(round(hours * 60))
    h, m = divmod(total, 60)
    # Each count gets a WHOLE sentence of its own rather than a glued-on "s".
    # "1 minutes" and "1 hour 1 minutes" both read as bugs in English, and the
    # suffix trick that would paper over them cannot be expressed at all in
    # Russian, Polish or Serbian — and cannot be expressed TWICE in one string
    # in any language, because nbi18n picks a translation's form from the
    # FIRST count only. Two counts therefore need four separate sentences.
    if not h:
        return _t("1 minute") if m == 1 else _t("%d minutes") % m
    if not m:
        return _t("1 hour") if h == 1 else _t("%d hours") % h
    if h == 1:
        return _t("1 hour 1 minute") if m == 1 else _t("1 hour %d minutes") % m
    return (_t("%d hours 1 minute") % h if m == 1
            else _t("%d hours %d minutes") % (h, m))


def _hhmm_to_hours(hhmm):
    """"HH:MM" -> hours as a float (14:30 -> 14.5), or None if it is not a
    time. Calendar stores start/end as float hours, Academics stores them as
    clock strings; this is the seam between the two."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h + m / 60.0


PALETTE = [
    "#4A5E73",  # slate
    "#6E7B57",  # olive
    "#9A7B4F",  # ochre
    "#7A5B73",  # mauve
    "#417E74",  # teal
    "#8A5340",  # sienna
    "#5B5E8A",  # indigo
    "#A34A3C",  # brick
]
# The single calendar present on a fresh install. "Personal" matches the name
# tasks.py stamps on its quick-added events, so those round-trip into a real
# calendar here instead of becoming orphaned.
DEFAULT_CAL = {"name": "Personal", "color": PALETTE[0]}

WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
WEEKDAYS_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
# This app's filename shadows Python's stdlib calendar module whenever the app
# directory is on sys.path.  _strptime imports these four public tables from
# ``calendar`` lazily, so provide the compatible English data it expects rather
# than making an unrelated ``time.strptime`` call crash inside the process.
day_name = tuple(WEEKDAYS_FULL)
day_abbr = tuple(name[:3] for name in WEEKDAYS_FULL)
month_name = ("",) + tuple(MONTHS)
month_abbr = ("",) + tuple(name[:3] for name in MONTHS)
HOURS = list(range(8, 21))  # 08:00 .. 20:00

# How often an event comes round again. Stored on every occurrence as "repeat"
# so a series can be recognised, retimed or removed as a whole; the occurrences
# themselves are REAL records in calendar.json, which is what lets the Tasks
# schedule rail and the desktop widget see next Tuesday's bin day without either
# of them knowing that repeats exist.
REPEATS = [
    ("none", "Does not repeat"),
    ("day", "Every day"),
    ("week", "Every week"),
    ("fortnight", "Every 2 weeks"),
    ("month", "Monthly (same date)"),
    ("year", "Every year"),
]
REPEAT_LABELS = dict(REPEATS)
# A year of a weekly event is 53 records of ~90 bytes; a year of a daily one is
# 366. Capped so no rule can ever write an unbounded store.
REPEAT_LIMIT = {"day": 366, "week": 53, "fortnight": 27, "month": 12,
                "year": 5}
# The cap alone made every repeat a silent time bomb: "Every week" ran out
# thirteen months later and simply was not there, and a birthday set to "Every
# year" died after five. The run is therefore RE-EXTENDED (see
# Calendar._extend_series) every time the app opens, so each rule always has
# this much of the future already written in front of today. Days, not
# occurrences, so a daily event does not have to grow a year at a time.
REPEAT_AHEAD = {"day": 120, "week": 400, "fortnight": 400, "month": 400,
                "year": 1830}


def _next_repeat(d, rule, n=1):
    """`d` advanced by n turns of `rule`, or None for a rule with no period."""
    if rule == "day":
        return d + timedelta(days=n)
    if rule == "week":
        return d + timedelta(days=7 * n)
    if rule == "fortnight":
        return d + timedelta(days=14 * n)
    if rule == "month":
        return _add_months(d, n)
    if rule == "year":
        return _add_months(d, 12 * n)
    return None


def _add_months(d, n):
    """`d` shifted by n whole months, clamped to the end of a short month (the
    31st of January plus one month is the 28th/29th of February)."""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    _lead, dim = _monthrange(y, m)
    return date(y, m, min(d.day, dim))


def _whole_periods(anchor, d, rule):
    """How many whole turns of `rule` separate `anchor` from `d`. Only the
    calendar-month rules need it (see Calendar._extend_series), and for those
    the answer is exact: every occurrence of a month/year series is `anchor`
    shifted by a whole number of months, so the month difference IS the turn
    count even when a short month clamped the day."""
    months = (d.year - anchor.year) * 12 + (d.month - anchor.month)
    return months // 12 if rule == "year" else months


def _repeat_dates(start, rule, end_date=None):
    """Every day a series occupies, starting at `start`, for the rule's cap.
    Returns [start] for an unknown or non-repeating rule, so a caller can always
    treat the result as the full set of days to write."""
    n = REPEAT_LIMIT.get(rule, 0)
    if not n:
        return [start]
    out = []
    for i in range(n):
        try:
            if rule == "day":
                occurrence = start + timedelta(days=i)
            elif rule == "week":
                occurrence = start + timedelta(days=7 * i)
            elif rule == "fortnight":
                occurrence = start + timedelta(days=14 * i)
            elif rule == "month":
                occurrence = _add_months(start, i)
            else:                                   # year
                occurrence = _add_months(start, 12 * i)
            if end_date is not None and occurrence > end_date:
                break
            out.append(occurrence)
        except (OverflowError, ValueError):
            break                                   # ran past the date range
    return out or [start]


# ---------------------------------------------------------- quick-add parsing
# "dentist thursday 3pm" has to become an event without anyone opening a form.
# The tokens below are matched against the words of what was typed; whatever is
# left over is the event's name. Day and month words are accepted in English AND
# in the language the app is running in, because the placeholder that teaches
# this is itself translated.
_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?(am|pm)?$", re.I)
_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
_ORDINAL_RE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?$", re.I)


# The abbreviations people actually write that are NOT simply the first three
# letters of the month. "Sept" is the common one, and leaving it out was not
# harmless: "sept 3 checkup" fell through to the time parser, which ate the 3 as
# an hour and filed the event on today at 15:00 under the name "sept checkup".
# Kept as an explicit list rather than a blanket four-letter rule, which would
# have made "Marc" a month and quietly deleted a man's name from his own event.
_MONTH_EXTRA = {"sept": 9, "sep.": 9, "sept.": 9, "jan.": 1, "feb.": 2,
                "mar.": 3, "apr.": 4, "jun.": 6, "jul.": 7, "aug.": 8,
                "oct.": 10, "nov.": 11, "dec.": 12}


def _word_tokens():
    """(weekday word -> 0-6, month word -> 1-12), in English and in the active
    language, full names and three-letter forms."""
    days, months = {}, {}
    for i, name in enumerate(WEEKDAYS_FULL):
        for form in (name, _t(name)):
            low = form.lower()
            days.setdefault(low, i)
            days.setdefault(low[:3], i)
    for i, name in enumerate(MONTHS):
        for form in (name, _t(name)):
            low = form.lower()
            months.setdefault(low, i + 1)
            months.setdefault(low[:3], i + 1)
    for form, i in _MONTH_EXTRA.items():
        months.setdefault(form, i)
    return days, months


DAY_WORDS, MONTH_WORDS = _word_tokens()
# Words that only glue a phrase together ("dentist AT 3pm", "bins ON thursday")
# and should not be left dangling on the end of the event's name once the thing
# they introduced has been taken out of it. Deliberately not run through _t():
# these are grammar, not interface text, and a catalog keyed on UI strings would
# map "on" and "at" to whatever unrelated label happens to share the word.
_GLUE = {"at", "on", "the", "of", "next", "this",
         "a", "à", "au", "le", "la", "el", "en", "de", "u", "na"}
# Words that ANNOUNCE a clock time, so the bare number after one of them is
# read as an hour ("lunch at 1") where every other bare number stays in the
# event's name. Grammar, not interface text — deliberately not run through _t()
# for the same reason as _GLUE above.
_TIME_LEAD = {"at", "@", "from", "à", "um", "alle"}


def _parse_time(word, bare_ok=False):
    """'3pm', '15:00', '9.30am' -> an hour as a float, or None.

    A BARE number ('7') is a time only when `bare_ok` — i.e. when something in
    the line said a time was coming, such as "at". Reading every stray integer
    as a clock hour is why "meeting with 3 people" became a 15:00 event called
    "meeting with people", "table for 4" moved to 16:00 and "buy 2 tickets"
    lost its 2: the number was silently taken out of the name. A bare hour that
    would land before breakfast is still nudged to the afternoon ('bridge at 7'
    is 19:00, not 07:00) — nobody writes a bare number meaning seven in the
    morning."""
    m = _TIME_RE.match(word)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    suffix = (m.group(3) or "").lower()
    if mm > 59:
        return None
    if suffix:
        if not 1 <= hh <= 12:
            return None
        hh = hh % 12 + (12 if suffix == "pm" else 0)
    else:
        if m.group(2) is None:
            # a bare number: a clock hour only when a time was announced, and
            # only a plausible one
            if not bare_ok:
                return None
            if not 1 <= hh <= 23:
                return None
            if hh < 8:
                hh += 12
        elif hh > 23:
            return None
    return hh + mm / 60.0


def parse_quick_event(text, base_day, cal_names=()):
    """Turn what somebody typed into (title, day, start-hour, calendar).

    Understands, in any order and mixed into the name: a time ('3pm', '15:00',
    '9.30am', 'noon', 'at 7'), a day ('today', 'tomorrow', 'thursday', '14/8',
    '14 August', 'August 14', 'Sept 3'), and '#Calendar' to file it. Whatever
    is left is the name. A weekday or a bare date always resolves FORWARD, so
    'thursday' typed on a Friday means the Thursday coming, not the one just
    gone.

    A number on its own is a TIME only after a word that announces one ("at").
    "table for 4" is a table for four, not a 16:00 appointment called "table
    for", and the caller shows the parsed title back so the difference is
    visible before Enter.

    Returns None when there is no name left to file — a time on its own is not
    an event. `base_day` is the day used when none was typed. Never raises."""
    words = str(text or "").split()
    if not words:
        return None
    day = None
    hour = None
    cal = None
    keep = []
    i = 0
    while i < len(words):
        w = words[i]
        low = w.lower().strip(",")
        nxt = words[i + 1].lower().strip(",") if i + 1 < len(words) else ""
        prev = words[i - 1].lower().strip(",") if i else ""

        if len(w) > 1 and w[0] == "#" and cal_names:
            key = low[1:]
            hit = next((c for c in cal_names if c.lower().startswith(key)), None)
            if hit:
                cal = hit
                i += 1
                continue
        if hour is None and low in ("noon", "midday"):
            hour = 12.0
            i += 1
            continue
        if hour is None and low == "midnight":
            hour = 0.0
            i += 1
            continue
        if day is None and low in ("today", _t("Today").lower()):
            day = base_day
            i += 1
            continue
        if day is None and low in ("tomorrow", _t("Tomorrow").lower()):
            day = base_day + timedelta(days=1)
            i += 1
            continue
        if day is None and low in DAY_WORDS:
            # forward, and the named day counts when it IS that day — the same
            # rule the Tasks quick-add uses for ">thursday".
            ahead = (DAY_WORDS[low] - base_day.weekday()) % 7
            day = base_day + timedelta(days=ahead)
            i += 1
            # A weekday that OPENS the line is usually part of the name —
            # "Sunday lunch", "Friday prayers", "Monday club" — so use it for
            # the day but leave it in the name as well. Dropping it silently
            # turned "Sunday lunch at noon" into an event called "lunch".
            if not keep:
                keep.append(w)
            continue
        if day is None:
            m = _DMY_RE.match(low)
            if m:
                got = _day_month_year(int(m.group(1)), int(m.group(2)),
                                      m.group(3), base_day)
                if got is not None:
                    day = got
                    i += 1
                    continue
            # "14 August" / "August 14", with or without an ordinal suffix
            om = _ORDINAL_RE.match(low)
            if om and nxt in MONTH_WORDS:
                got = _day_month_year(int(om.group(1)), MONTH_WORDS[nxt],
                                      None, base_day)
                if got is not None:
                    day = got
                    i += 2
                    continue
            if low in MONTH_WORDS:
                om = _ORDINAL_RE.match(nxt)
                if om:
                    got = _day_month_year(int(om.group(1)), MONTH_WORDS[low],
                                          None, base_day)
                    if got is not None:
                        day = got
                        i += 2
                        continue
        if hour is None:
            # "3 pm" written with a space is two words; join them before
            # giving up, or the "pm" is left stranded in the event's name.
            if nxt in ("am", "pm"):
                got = _parse_time(low + nxt)
                if got is not None:
                    hour = got
                    i += 2
                    continue
            # A bare integer counts as a time ONLY when the word before it
            # announced one ("lunch at 1"). Without that test every number in
            # a title was eaten as a clock hour — see _parse_time.
            got = _parse_time(low, bare_ok=(prev in _TIME_LEAD))
            if got is not None:
                hour = got
                i += 1
                continue
        keep.append(w)
        i += 1

    while keep and keep[-1].lower().strip(",") in _GLUE:
        keep.pop()
    title = " ".join(keep).strip(" ,-")
    if not title:
        return None
    if all(w.lower().strip(",") in DAY_WORDS for w in keep):
        return None      # "thursday" on its own names a day, not an event
    return (title, day if day is not None else base_day,
            9.0 if hour is None else hour, cal)


def _day_month_year(d, m, y, base_day):
    """Build a date from typed day/month/(optional year) parts, resolving a
    missing year FORWARD — '14/1' typed in December means next January. Returns
    None when the parts are not a real date."""
    if y is None:
        for year in (base_day.year, base_day.year + 1):
            try:
                got = date(year, m, d)
            except ValueError:
                return None
            if got >= base_day:
                return got
        return got
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


# --------------------------------------------------------- month-grid keys
# The month grid is ONE focus stop that moves a selection inside itself, not 42
# tab stops — a month you have to Tab through a day at a time is a maze, not
# navigation. These maps are the whole of what the grid claims: every other key
# (Tab, Ctrl+N, the menu accelerators, Esc) falls through untouched, so nothing
# here amounts to a global arrow interception.
#
# Keypad duplicates are listed because a keyboard with a numeric pad sends the
# KP_ keyvals when Num Lock is off, and a grid that ignored them would look
# broken on exactly the machines most likely to type dates.
_MONTH_DAY_KEYS = {
    Gdk.KEY_Left: -1,   Gdk.KEY_KP_Left: -1,
    Gdk.KEY_Right: 1,   Gdk.KEY_KP_Right: 1,
    Gdk.KEY_Up: -7,     Gdk.KEY_KP_Up: -7,
    Gdk.KEY_Down: 7,    Gdk.KEY_KP_Down: 7,
}
_MONTH_MONTH_KEYS = {
    Gdk.KEY_Page_Up: -1,   Gdk.KEY_KP_Page_Up: -1,
    Gdk.KEY_Page_Down: 1,  Gdk.KEY_KP_Page_Down: 1,
}
# Home/End are the ends of the SELECTED WEEK (the row you are standing on),
# which is what the row-shaped grid makes them mean — not the ends of the month.
_MONTH_WEEKEND_KEYS = {
    Gdk.KEY_Home: 0,     Gdk.KEY_KP_Home: 0,      # Monday of that week
    Gdk.KEY_End: 6,      Gdk.KEY_KP_End: 6,       # Sunday of that week
}
_MONTH_OPEN_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
                    Gdk.KEY_space, Gdk.KEY_KP_Space)


class Calendar(nbapp.AppWindow):
    app_name = "Calendar"
    menus = ("File", "Edit", "View", "Go")

    def __init__(self, initial_date=None):
        super().__init__()
        self._install_css()

        # Lifecycle flag, set the moment the window starts tearing down. Every
        # timer this app owns checks it before touching a widget, so nothing
        # runs against a destroyed window (see _on_destroy).
        self._closed = False
        self._rollover_id = 0

        self.today = date.today()
        # Optional launch target: the desktop Calendar widget starts us as
        # "calendar.py YYYY-MM-DD" to open straight onto a clicked day. Fully
        # guarded — a bad or absent argument falls back to today, so a normal
        # launch (and any garbage in argv) is never affected.
        start = self._parse_initial_date(initial_date)
        if start is None:
            start = self.today
        self.cur_y = start.year
        self.cur_m = start.month
        self.sel = start
        self.view = "month"

        # Named calendars (user-managed) and their per-name visibility.
        self.calendars = self._load_calendars()   # [{"name","color"}, ...]
        self.cals_on = {c["name"]: True for c in self.calendars}
        # Restore persisted events; ship empty on a fresh install (no seed).
        self.events = self._load_events()  # dicts {id,date,start,end,title,cal}
        # Classes mirrored from Academics: derived, read-only, and kept in their
        # OWN list so that every path which writes calendar.json iterates
        # self.events and therefore cannot persist them by accident.
        self.class_events = self._load_class_events()
        # Tokens of every event this session has read, created or adopted. Lets
        # merge-on-write (see _save_events) tell a foreign, concurrently-added
        # disk record (keep it) from one this session deliberately deleted (an
        # event whose tokens are seen but is no longer in memory — keep it gone).
        self._seen = set()
        for e in self.events:
            self._mark_seen(e)
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()
        # Keep every repeating series running past today (see _extend_series).
        # Done on open, before anything is drawn, so a weekly event that reached
        # the end of its written run is already back on the grid rather than
        # missing until some later action happens to rebuild it. Persisted
        # after the window exists so the write goes through the normal path.
        self._extended = self._extend_series()

        self._doc_path = None          # current File-menu document (None=unsaved)
        self.seg_btns = {}
        self.month_grid = None         # live month Gtk.Grid (see _build_month)
        self._new_event_hour = None    # slot-click seed for the New Event dialog
        self._status_tok = 0

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True); body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._build_sidebar(), False, False, 0)
        body.pack_start(self._build_main(), True, True, 0)

        self._refresh()
        if self._extended:
            self._save_events()

        # Flush on close so the final add / edit / delete is never lost.
        self.connect("destroy", self._on_destroy)

        # self.today is computed once at launch; if the OS is left running
        # across midnight the accent-red 'today' marker would freeze to the boot
        # day. Poll the local date each minute (mirrors widgets.py) and re-render
        # only on an actual date change, so it stays cheap. The source id is kept
        # so close can remove it: an anonymous timeout holds a reference to this
        # window forever, so every Calendar the user ever closed would keep
        # waking each minute and, on the next date change, refresh widgets that
        # are already destroyed.
        self._rollover_id = GLib.timeout_add_seconds(
            60, self._check_date_rollover)

    @staticmethod
    def _parse_initial_date(arg):
        """Resolve the optional launch-to-a-day target. `arg` is an explicit
        ISO 'YYYY-MM-DD' string (or a date); when it is None, fall back to
        sys.argv[1] so a standalone "calendar.py 2026-07-18" invocation lands on
        that day too. Returns a date, or None on anything absent/malformed — the
        caller then defaults to today. Never raises, and uses the same plain
        int-splitting parser as the rest of this file (no `import calendar`)."""
        if arg is None:
            try:
                argv = sys.argv[1:]
            except Exception:
                argv = []
            arg = next((a for a in argv if a and not a.startswith("-")), None)
        if arg is None:
            return None
        if isinstance(arg, date):
            return arg
        return Calendar._iso_to_date(arg)

    def _undo_snapshot(self):
        """The user-owned calendar state needed to reverse a destructive edit."""
        visible = {c["name"]: self.cals_on.get(c["name"], True)
                   for c in self.calendars}
        return (copy.deepcopy(self.events), copy.deepcopy(self.calendars),
                visible, copy.deepcopy(getattr(self, "_orphans", [])),
                set(getattr(self, "_seen", set())))

    def _undo_restore(self, state):
        events, calendars, visible, orphans, seen = copy.deepcopy(state)
        self.events = events
        self.calendars = calendars
        self._orphans = orphans
        self._seen = seen
        self.cals_on = {c["name"]: visible.get(c["name"], True)
                        for c in calendars}
        self._save_calendars()
        self._save_events()
        self._populate_cal_list()
        self._refresh()

    # ------------------------------------------------------------------ menus
    def menu_items(self, name):
        """File carries the calendar's create actions; View carries the view
        switch and per-calendar visibility (and New Calendar); Go carries date
        navigation. Everything else falls back to the base so
        Cut/Copy/Paste/Close/About keep working."""
        if name == "File":
            # calendar.json / calendars.json are the sole source of truth and
            # are rewritten on every edit, so there is no document to Save and
            # nothing a Save As would rescue. The old New / Open / Save /
            # Save As were worse than redundant: Open REPLACED the whole store —
            # the same store the Tasks app and the desktop widget read — so a
            # mistaken pick silently wiped every event. File now offers only
            # what the app can actually make (see docs/MENU-CONVENTIONS.md, the
            # single-store File menu).
            return [
                ("New Event…    Ctrl+N", lambda: self._open_new_event()),
                ("Add a Shift…", lambda: self._shift_dialog()),
                ("New Calendar…", lambda: self._new_calendar()),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "View":
            items = []
            for key, label in (("day", "Day"), ("week", "Week"),
                               ("month", "Month")):
                mark = "•  " if self.view == key else "     "
                items.append((mark + label,
                              lambda k=key: self._on_view(None, k)))
            items.append(nbapp.SEP)
            for cal in self.calendars:
                cname = cal["name"]
                on = self.cals_on.get(cname, True)
                mark = "•  " if on else "     "
                items.append(
                    (mark + cname, lambda n=cname: self._toggle_cal_by_name(n)))
            # New Calendar… lives in File with the app's other create actions;
            # repeating it here would put the same item twice in one menu bar.
            return items
        if name == "Edit":
            return nbapp.undo_menu_items(self.undo)
        if name == "Go":
            unit = {"month": "Month", "week": "Week", "day": "Day"}.get(
                self.view, "Month")
            return [
                ("Today", self._on_today),
                nbapp.SEP,
                ("Previous " + unit, self._on_prev),
                ("Next " + unit, self._on_next),
            ]
        return super().menu_items(name)

    def _toggle_cal_by_name(self, name):
        """Flip a calendar's visibility from the View menu; crash-safe if the
        swatch DrawingArea handle is missing."""
        self.cals_on[name] = not self.cals_on.get(name, True)
        area = self.cals_on.get(name + "_area")
        if area is not None:
            area.queue_draw()
        self._rebuild_body()

    def _on_key(self, w, ev):
        """Ctrl+N starts a new event — the one thing this app creates.

        The Ctrl+S / Ctrl+Shift+S / Ctrl+O document shortcuts were removed with
        the File items they belonged to: the calendar has no document, and a key
        that opened a save picker with nothing in the menu to name it was a
        shortcut nobody could discover (docs/MENU-CONVENTIONS.md rule 3). The
        base already connects this via self._on_key, so the override is picked
        up without a second connect (a double-connect would fire it twice)."""
        try:
            if ev.state & Gdk.ModifierType.CONTROL_MASK:
                shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
                if ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                    return nbapp.undo_keys(self.undo, ev)
                if ev.keyval in (Gdk.KEY_n, Gdk.KEY_N) and not shift:
                    self._open_new_event()
                    return True
        except Exception:
            pass
        return super()._on_key(w, ev)

    # ------------------------------------------------------------- calendars
    def _cal_names(self):
        return [c["name"] for c in self.calendars]

    def _cal_color(self, name):
        """The color of a named calendar, or a neutral gray for an unknown name
        (e.g. an event whose calendar was deleted, or a foreign store)."""
        for c in self.calendars:
            if c["name"] == name:
                return c["color"]
        if name == CLASSES_CAL:
            return "#6E695E"
        return "#9A9484"

    def _event_color(self, e):
        """An event's colour. A mirrored class carries its own — the colour it
        has in Academics — so the timetable and the calendar agree at a glance
        about which class is which."""
        return e.get("color") or self._cal_color(e["cal"])

    def _color_class(self, color):
        """CSS class carrying a chip's left-border color. Palette colors map to
        chipbar-<i>; anything else falls back to a neutral class."""
        try:
            return "chipbar-%d" % PALETTE.index(color)
        except ValueError:
            return "chipbar-x"

    def _chip_class(self, cal_name):
        return self._color_class(self._cal_color(cal_name))

    def _event_chip_class(self, e):
        """The chip class for one event, honouring a mirrored class's own
        colour so the calendar and the Academics timetable agree about which
        class is which."""
        return self._color_class(self._event_color(e))

    def _load_calendars(self):
        """Restore named calendars from calendars.json, or the single default
        on a fresh / unreadable / empty store. Never raises."""
        self._calendars_quarantine = False
        try:
            with open(CALENDARS_FILE) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return [dict(DEFAULT_CAL)]
        except Exception:
            # Unreadable bytes: nbapp's writer asides them at the next write;
            # saving must keep working (see _load_events).
            return [dict(DEFAULT_CAL)]
        cals = self._norm_calendars(data)
        if not cals:
            if data and data != {"calendars": []}:
                # Parsed, non-empty, and not a calendar list in any shape the
                # normaliser reads: only this app can see that. Aside at the
                # next write, never overwrite.
                self._calendars_quarantine = True
            return [dict(DEFAULT_CAL)]
        return cals

    def _norm_calendars(self, data):
        """Coerce raw calendar records into [{name,color}], dropping blanks and
        de-duplicating names (case-insensitive). Returns [] on garbage."""
        out, seen = [], set()
        if isinstance(data, dict):
            # Calendars stored as an object keyed by name: the values are still
            # the user's calendars. _save_calendars rewrites this file on close,
            # so a wrapper of the wrong type used to cost every calendar in it.
            #
            # A WRAPPED list ({"calendars": [...]}) is the same file with one
            # more layer, and taking .values() alone left a list-inside-a-list
            # that matched nothing: _load_calendars fell back to the single
            # stock "Personal", and closing the window wrote that over every
            # named calendar the user had. _event_list has recognised this shape
            # for its own file since round 3 — this is the same store, one
            # directory entry along. Never raises.
            inner = data.get("calendars")
            if not isinstance(inner, list):
                inner = next((v for v in data.values()
                              if isinstance(v, list)
                              and any(isinstance(x, dict) for x in v)), None)
            data = inner if inner is not None else list(data.values())
        if not isinstance(data, list):
            return out
        for item in data:
            if not isinstance(item, dict):
                continue
            nm = str(item.get("name", "")).strip()
            if not nm or nm.lower() in seen:
                continue
            color = str(item.get("color", PALETTE[0]))
            out.append({"name": nm, "color": color})
            seen.add(nm.lower())
        return out

    def _save_calendars(self):
        """Persist named calendars to calendars.json. Never raises."""
        if getattr(self, "_calendars_quarantine", False):
            _quarantine_store(CALENDARS_FILE)
            self._calendars_quarantine = False
        try:
            nbapp.atomic_write_json(
                CALENDARS_FILE,
                [{"name": c["name"], "color": c["color"]}
                 for c in self.calendars])
        except Exception:
            pass

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrap.get_style_context().add_class("calsidebar")
        # 256 + the 22px margins either side = the design's 300px sidebar. The
        # request used to be 300 on top of the margins, i.e. 344 wide, which on
        # a 1024-wide panel stole the width the month grid needs.
        wrap.set_size_request(256, -1)
        wrap.set_margin_top(24); wrap.set_margin_bottom(20)
        wrap.set_margin_start(22); wrap.set_margin_end(22)

        # mini-month (rebuilt on navigation)
        self.mini_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.mini_box.set_margin_bottom(24)
        wrap.pack_start(self.mini_box, False, False, 0)

        head = Gtk.Label(label=_t("CALENDARS"), xalign=0)
        head.get_style_context().add_class("calsectionhead")
        head.set_margin_bottom(8)
        wrap.pack_start(head, False, False, 0)

        # The named-calendar list scrolls independently, so any number of
        # calendars fits between the mini-month and the pinned controls.
        listscroll = Gtk.ScrolledWindow()
        listscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.cal_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        listscroll.add(self.cal_list_box)
        wrap.pack_start(listscroll, True, True, 0)
        self._populate_cal_list()

        add = Gtk.Button(); add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("caladd")
        add.set_margin_top(4)
        ainner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ainner.pack_start(
            nbicons.image("plus", 15, "#6E695E"),
            False, False, 0)
        albl = Gtk.Label(label=_t("Add Calendar"), xalign=0)
        albl.get_style_context().add_class("caladdlabel")
        ainner.pack_start(albl, False, False, 0)
        add.add(ainner)
        add.connect("clicked", self._new_calendar)
        wrap.pack_start(add, False, False, 0)

        # Quick add + New Event, pinned to the bottom.
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.get_style_context().add_class("calfoot")
        foot.set_margin_top(16)

        # One line that files a whole event: "dentist thursday 3pm". The form
        # below it still exists for anything unusual, but the common case — a
        # name, a day and a time — should never need eight fields and twenty
        # clicks on a day arrow. The hint under the field reads back the day and
        # time that were understood, so nobody has to trust it blindly.
        qa = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        qa.get_style_context().add_class("quickadd")
        qa.pack_start(
            nbicons.image("plus", 15, "#8A857A"),
            False, False, 0)
        self.quick = Gtk.Entry()
        self.quick.set_has_frame(False)
        self.quick.get_style_context().add_class("quickentry")
        self.quick.set_placeholder_text(_t("Add event"))
        self.quick.set_tooltip_text(
            _t("Event name, with an optional day and time, then Enter. "
               "Example: dentist thursday 3pm"))
        self.quick.set_width_chars(8)   # never sets the sidebar's width
        self.quick.connect("changed", self._on_quick_changed)
        self.quick.connect("activate", self._on_quick_add)
        qa.pack_start(self.quick, True, True, 0)
        foot.pack_start(qa, False, False, 0)

        self.quick_hint = Gtk.Label(label="", xalign=0)
        self.quick_hint.get_style_context().add_class("quickhint")
        self.quick_hint.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self.quick_hint.set_margin_bottom(10)
        foot.pack_start(self.quick_hint, False, False, 0)
        self._on_quick_changed()

        btn = Gtk.Button(); btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("newevent")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        inner.set_halign(Gtk.Align.CENTER)
        inner.pack_start(
            nbicons.image("plus", 17, "#2A2620"),
            False, False, 0)
        # The ellipsis distinguishes the full form from the quick-add line
        # directly above it — the same convention the File menu already uses.
        lbl = Gtk.Label(label=_t("New Event…"))
        lbl.get_style_context().add_class("newevlabel")
        inner.pack_start(lbl, False, False, 0)
        btn.add(inner)
        btn.connect("clicked", self._open_new_event)
        foot.pack_start(btn, False, False, 0)
        wrap.pack_end(foot, False, False, 0)
        return wrap

    # ------------------------------------------------------------- quick add
    def _quick_parse(self):
        """What the quick-add box currently says, resolved against the selected
        day. None when it is empty or holds no event name yet."""
        try:
            text = self.quick.get_text()
        except Exception:
            return None
        return parse_quick_event(text, self.sel, self._cal_names())

    def _on_quick_changed(self, *_):
        """Read back the event that was understood, live, under the field — so
        'dentist thursday 3pm' visibly becomes 'dentist · Thu 30 Jul · 15:00'
        before Enter is pressed, and an empty box teaches the shape by example.

        The NAME leads the readback. It used to show only the day and time,
        which meant any word the parser took out of the title vanished without
        trace until after Enter — the one part of the guess nobody could
        check."""
        got = self._quick_parse()
        if got is None:
            self.quick_hint.set_text(_t("Example: dentist thursday 3pm"))
            return
        title, day, hour, cal = got
        h = int(hour)
        stamp = _t("%s  ·  %s %d %s  ·  %02d:%02d") % (
            title,
            _t(WEEKDAYS_FULL[day.weekday()])[:3], day.day,
            _t(MONTHS[day.month - 1])[:3],
            h, int(round((hour - h) * 60)))
        if cal:
            stamp += "  ·  " + cal
        self.quick_hint.set_text(stamp)

    def _on_quick_add(self, *_):
        """Enter in the quick-add box: file the event, move the view to the day
        it landed on so it is actually seen, and say so in the status line."""
        got = self._quick_parse()
        if got is None:
            return
        title, day, hour, cal = got
        names = self._cal_names()
        cal = cal or (names[0] if names else DEFAULT_CAL["name"])
        self._new_event(day, {"start": hour, "end": hour + 1.0,
                              "title": title, "cal": cal})
        self.quick.set_text("")
        # Follow the event to its day, and make sure its calendar is showing —
        # an event you cannot see was not really added.
        self.sel = day
        self.cur_y, self.cur_m = day.year, day.month
        self.cals_on[cal] = True
        swatch = self.cals_on.get(cal + "_area")
        if swatch is not None:
            swatch.queue_draw()
        self._save_events()
        self._refresh()
        self._flash_status(_t("Added %s") % title)

    def _populate_cal_list(self):
        """(Re)build the sidebar's named-calendar rows from self.calendars."""
        for c in self.cal_list_box.get_children():
            self.cal_list_box.remove(c)
        for cal in self.calendars:
            self.cals_on.setdefault(cal["name"], True)
            self.cal_list_box.pack_start(
                self._cal_row(cal["name"], cal["color"]), False, False, 0)
        self.cal_list_box.show_all()

    def _build_mini(self):
        for c in self.mini_box.get_children():
            self.mini_box.remove(c)

        title = Gtk.Label(label=_t("%s %d") % (
                              _t(MONTHS[self.cur_m - 1]), self.cur_y),
                          xalign=0)
        title.get_style_context().add_class("minititle")
        title.set_margin_bottom(12)
        self.mini_box.pack_start(title, False, False, 0)

        grid = Gtk.Grid(); grid.set_column_homogeneous(True)
        for c, w in enumerate(WEEKDAYS):
            lbl = Gtk.Label(label=w)
            lbl.get_style_context().add_class("minidow")
            grid.attach(lbl, c, 0, 1, 1)

        lead, dim = _monthrange(self.cur_y, self.cur_m)  # Monday=0
        # Which days of the shown month carry something. The mini-month is the
        # one place you can see a whole month at a glance while standing in the
        # Day or Week view, and it used to be a bare grid of numbers that told
        # you nothing about where the busy days were.
        busy = {e["date"].day for e in self.events
                if e["date"].year == self.cur_y and e["date"].month == self.cur_m
                and self.cals_on.get(e["cal"], True)}
        r, cpos = 1, lead
        for d in range(1, dim + 1):
            cell = Gtk.EventBox()
            cell.set_size_request(-1, 30)
            wc = date(self.cur_y, self.cur_m, d)
            pill = Gtk.Label(label=str(d))
            pill.set_halign(Gtk.Align.CENTER); pill.set_valign(Gtk.Align.CENTER)
            if wc == self.today:
                pill.get_style_context().add_class("minitoday")
            elif wc == self.sel:
                pill.get_style_context().add_class("minisel")
            elif d in busy:
                pill.get_style_context().add_class("minibusy")
            else:
                pill.get_style_context().add_class("miniday")
            box = Gtk.Box(); box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.pack_start(pill, False, False, 0)
            cell.add(box)
            cell.connect("button-press-event", self._on_pick_day, wc)
            grid.attach(cell, cpos, r, 1, 1)
            cpos += 1
            if cpos > 6:
                cpos = 0; r += 1
        self.mini_box.pack_start(grid, False, False, 0)
        self.mini_box.show_all()

    def _cal_row(self, name, color):
        """One calendar row: a clickable [swatch + name] that toggles the
        calendar's visibility, and a trailing delete button. The last remaining
        calendar's delete is disabled (a calendar must always exist to file
        events into)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("calrow")

        toggle = Gtk.Button()
        toggle.set_relief(Gtk.ReliefStyle.NONE)
        toggle.get_style_context().add_class("caltoggle")
        toggle.set_tooltip_text(_t("Show or hide calendar"))
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        inner.set_margin_top(2); inner.set_margin_bottom(2)
        swatch = Gtk.DrawingArea(); swatch.set_size_request(18, 18)
        swatch.set_valign(Gtk.Align.CENTER)
        swatch.connect("draw", self._draw_calbox, name)
        self.cals_on[name + "_area"] = swatch  # keep a handle for redraw
        inner.pack_start(swatch, False, False, 0)
        lbl = Gtk.Label(label=name, xalign=0)
        lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        lbl.get_style_context().add_class("callabel")
        inner.pack_start(lbl, True, True, 0)
        toggle.add(inner)
        toggle.connect("clicked", self._on_toggle_cal_clicked, name)
        row.pack_start(toggle, True, True, 0)

        delbtn = Gtk.Button(); delbtn.set_relief(Gtk.ReliefStyle.NONE)
        delbtn.get_style_context().add_class("caldel")
        delbtn.set_valign(Gtk.Align.CENTER)
        delbtn.add(nbicons.image("trash", 14, "#9A9484"))
        delbtn.set_tooltip_text(_t("Delete calendar"))
        if len(self.calendars) <= 1:
            delbtn.set_sensitive(False)
        delbtn.connect("clicked", self._on_delete_cal, name)
        row.pack_end(delbtn, False, False, 0)
        return row

    def _draw_calbox(self, area, ctx, name):
        # Geometry from the LIVE allocation (an 18px request, but the first draw
        # on the software stack can land at 0x0). Bail on a degenerate area
        # rather than stroke an inside-out box, and never let a paint error tear
        # down the sidebar row. All coordinates scale off w/h — nothing stale.
        w = area.get_allocated_width(); h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return False
        try:
            on = self.cals_on.get(name, True)
            r, g, b = nbicons._hex(self._cal_color(name))
            self._round_rect(ctx, 1, 1, w - 2, h - 2, 3)
            if on:
                ctx.set_source_rgb(r, g, b); ctx.fill()
                ctx.set_source_rgb(*nbicons._hex("#FCFBF8"))
                ctx.set_line_width(2.0)
                ctx.set_line_cap(cairo.LINE_CAP_ROUND)
                ctx.set_line_join(cairo.LINE_JOIN_ROUND)
                ctx.move_to(w * 0.27, h * 0.52)
                ctx.line_to(w * 0.43, h * 0.68)
                ctx.line_to(w * 0.74, h * 0.33)
                ctx.stroke()
            else:
                ctx.set_source_rgb(r, g, b)
                ctx.set_line_width(1.5); ctx.stroke()
        except Exception:
            pass
        return False

    def _on_toggle_cal_clicked(self, _w, name):
        self.cals_on[name] = not self.cals_on.get(name, True)
        area = self.cals_on.get(name + "_area")
        if area is not None:
            area.queue_draw()
        self._rebuild_body()
        return True

    def _round_rect(self, ctx, x, y, w, h, r):
        # Software-render safety: a draw signal can arrive before the
        # DrawingArea has a real allocation (w/h == 0), and a corner radius
        # larger than half the box drives the four corner arcs inside-out and
        # paints garbage on the plain framebuffer (virtio-gpu masked this). Bail
        # on a degenerate box and clamp r so the outline is always valid.
        if w <= 0 or h <= 0:
            return
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        ctx.new_sub_path()
        ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        ctx.close_path()

    # ------------------------------------------------- add / delete calendar
    def _new_calendar(self, *_):
        """Create a named calendar: a name and a color from the palette."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        ok = dlg.add_button("Add Calendar", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        area = dlg.get_content_area()
        area.set_spacing(12)
        area.set_margin_top(24); area.set_margin_bottom(16)
        area.set_margin_start(28); area.set_margin_end(28)

        t = Gtk.Label(label=_t("New Calendar"), xalign=0)
        t.get_style_context().add_class("dlgtitle")
        area.pack_start(t, False, False, 0)

        name = Gtk.Entry(); name.set_placeholder_text(_t("Calendar name"))
        name.set_activates_default(True)
        area.pack_start(name, False, False, 0)

        err = Gtk.Label(label="", xalign=0)
        err.get_style_context().add_class("dlgerror")
        err.set_no_show_all(True)
        area.pack_start(err, False, False, 0)
        name.connect("changed", lambda *_: (
            name.get_style_context().remove_class("field-error"), err.hide()))

        used = {c["color"] for c in self.calendars}
        selected = [next((i for i, c in enumerate(PALETTE) if c not in used), 0)]
        swrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        for i, color in enumerate(PALETTE):
            ev = Gtk.Button(); ev.set_relief(Gtk.ReliefStyle.NONE)
            ev.get_style_context().add_class("calswatch")
            ev.set_tooltip_text(_t("Choose color %d") % (i + 1))
            da = Gtk.DrawingArea(); da.set_size_request(26, 26)
            da.connect("draw", self._draw_swatch, i, color, selected)
            ev.add(da)
            ev.connect("clicked",
                       self._on_pick_swatch, i, selected, swrow)
            swrow.pack_start(ev, False, False, 0)
        area.pack_start(self._field("Color", swrow), False, False, 0)

        dlg.show_all()
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            nm = name.get_text().strip()
            if not nm:
                err.set_text("Enter a calendar name.")
                name.get_style_context().add_class("field-error")
                err.show(); name.grab_focus()
                continue
            if nm.lower() in {c["name"].lower() for c in self.calendars}:
                err.set_text("A calendar with that name already exists.")
                name.get_style_context().add_class("field-error")
                err.show(); name.grab_focus()
                continue
            self.calendars.append({"name": nm, "color": PALETTE[selected[0]]})
            self.cals_on[nm] = True
            self._save_calendars()
            self._populate_cal_list()
            self._refresh()
            break
        dlg.destroy()

    def _draw_swatch(self, area, ctx, idx, color, selected):
        # Live allocation, with the same 0x0 first-paint guard as the sidebar
        # swatch; _round_rect clamps the ring radii so the nested rings never
        # invert on the software framebuffer. Crash-safe.
        w = area.get_allocated_width(); h = area.get_allocated_height()
        if w <= 0 or h <= 0:
            return False
        try:
            self._round_rect(ctx, 2, 2, w - 4, h - 4, 4)
            ctx.set_source_rgb(*nbicons._hex(color)); ctx.fill()
            if selected[0] == idx:
                # Selected swatch: a white inner ring plus a darker-beige outer
                # ring — readable on every palette color without black chrome
                # (the design reserves black for ink, not for selection).
                self._round_rect(ctx, 3, 3, w - 6, h - 6, 3)
                ctx.set_source_rgb(*nbicons._hex("#FCFBF8"))
                ctx.set_line_width(2.0); ctx.stroke()
                self._round_rect(ctx, 1, 1, w - 2, h - 2, 5)
                ctx.set_source_rgb(*nbicons._hex(SELECT_RING))
                ctx.set_line_width(1.5); ctx.stroke()
        except Exception:
            pass
        return False

    def _on_pick_swatch(self, _btn, idx, selected, swrow):
        # `clicked`, so no event argument and nothing to return. The row's
        # children are Gtk.Buttons now rather than EventBoxes; both are Gtk.Bin,
        # so get_child() still reaches the DrawingArea that does the painting.
        selected[0] = idx
        for child in swrow.get_children():
            da = child.get_child() if isinstance(child, Gtk.Bin) else None
            if da is not None:
                da.queue_draw()

    def _on_delete_cal(self, _btn, name):
        """Delete a named calendar and every event filed under it (undoable).
        Blocked when it is the only calendar left."""
        if len(self.calendars) <= 1:
            return
        self.undo.checkpoint("Delete Calendar")
        self.calendars = [c for c in self.calendars if c["name"] != name]
        self.events = [e for e in self.events if e["cal"] != name]
        self.cals_on.pop(name, None)
        self.cals_on.pop(name + "_area", None)
        self._save_calendars()
        self._save_events()
        self._populate_cal_list()
        self._refresh()
        self.undo.commit()

    def _confirm(self, title, body, ok_label):
        """A small modal Cancel / <ok_label> confirmation. Returns True on the
        positive response. ok_label is styled destructive (these discard data)."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        # The shared OS treatment (Papertone .destructive-action), the same red
        # slab the Finder, Contacts, Cookbook, Journal and Terminal use for a
        # single destructive choice. The quieter local .destructive stays for
        # the two places it is right: a Delete that sits BESIDE a primary Save
        # (_event_dialog), and _confirm_series, where both choices destroy
        # something and neither is "the" action.
        ok.get_style_context().add_class("destructive-action")
        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_margin_top(24); area.set_margin_bottom(16)
        area.set_margin_start(28); area.set_margin_end(28)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("dlgtitle")
        area.pack_start(t, False, False, 0)
        b = Gtk.Label(label=body, xalign=0)
        b.set_line_wrap(True); b.set_max_width_chars(40)
        b.get_style_context().add_class("dlgbody")
        area.pack_start(b, False, False, 0)
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    # ------------------------------------------------------------------- main
    def _build_main(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("calmain")
        col.set_hexpand(True)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.set_margin_top(26); head.set_margin_bottom(16)
        # 24, not 40: it lines the title and the view buttons up with the Day /
        # Week grid gutter below them, and the 32px it gives back is what the
        # period title needs to spell itself out on a 1024-wide screen.
        head.set_margin_start(24); head.set_margin_end(24)

        # Spacing is set PER GAP, not uniformly. The 20px after the title is a
        # design fact (see the ‹ › note below); the gap between ‹ › and Today is
        # not, and it is where the ten pixels come from that let the period
        # title spell itself out in Greek. Measured at 1024 wide with NB_LANG=el:
        # the header has 676px, the title/nav/Today group NEEDS 436 and the view
        # segment 250 — a 10px deficit, which the title absorbed on its own
        # because it is the only ellipsizing widget in the row. The heading read
        # "Αυγούστου 2…", losing the YEAR: the one token a calendar's own title
        # cannot afford to drop, while the mini-calendar in the sidebar six
        # inches away printed "Αυγούστου 2026" in full.
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.title_lbl = Gtk.Label(label="", xalign=0)
        self.title_lbl.get_style_context().add_class("caltitle")
        # The period title takes the width its text needs (the ‹ › buttons sit
        # 20px after it, as in the design) and shortens with an ellipsis on a
        # small panel. It used to demand a flat 360px, which alone put the
        # window's minimum width past a 1024-wide screen.
        self.title_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        left.pack_start(self.title_lbl, False, False, 0)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        prev = self._nav_btn("back", self._on_prev, "Previous")
        nxt = self._nav_btn("fwd", self._on_next, "Next")
        nav.pack_start(prev, False, False, 0)
        nav.pack_start(nxt, False, False, 0)
        nav.set_margin_start(20)         # the documented gap, unchanged
        left.pack_start(nav, False, False, 0)

        today = Gtk.Button(label=_t("Today")); today.set_relief(Gtk.ReliefStyle.NONE)
        today.get_style_context().add_class("todaybtn")
        today.connect("clicked", self._on_today)
        today.set_margin_start(10)       # 20 -> 10: the ten pixels, taken here
        left.pack_start(today, False, False, 0)
        head.pack_start(left, False, False, 0)

        # Transient status line (Save/Open/New feedback), centered in the bar.
        self.status_lbl = Gtk.Label(label="", xalign=0.5)
        self.status_lbl.set_ellipsize(3)
        self.status_lbl.get_style_context().add_class("statusmsg")
        # No padding: this label is empty at rest, and its padding was reserved
        # width the title could not use on a narrow screen.
        head.pack_start(self.status_lbl, True, True, 0)

        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("segwrap")
        seg.set_valign(Gtk.Align.CENTER)
        for i, key in enumerate(("day", "week", "month")):
            b = Gtk.Button(label=key.capitalize())
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            if i == 2:
                b.get_style_context().add_class("seglast")
            b.connect("clicked", self._on_view, key)
            self.seg_btns[key] = b
            seg.pack_start(b, False, False, 0)
        head.pack_end(seg, False, False, 0)
        col.pack_start(head, False, False, 0)

        # body area, rebuilt per view
        self.body_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body_area.set_vexpand(True); self.body_area.set_hexpand(True)
        col.pack_start(self.body_area, True, True, 0)
        return col

    def _nav_btn(self, icon, handler, tip=None):
        b = Gtk.Button(); b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("navbtn")
        # Icon-only, so name it: nbi18n patches set_tooltip_text, which is why
        # a plain English string here still reaches the catalogs.
        if tip:
            b.set_tooltip_text(tip)
        b.add(nbicons.image(icon, 18, "#1A1916"))
        b.connect("clicked", handler)
        return b

    def _empty_hint(self):
        # An empty calendar is the first thing anyone sees, so this line points
        # at the FASTEST way to fill it — the quick-add box in the sidebar —
        # and shows what to type rather than naming a button.
        lbl = Gtk.Label(
            label=_t("No events. Add one in the box on the left."),
            xalign=0)
        lbl.set_line_wrap(True)   # a longer translation wraps, never widens
        lbl.get_style_context().add_class("emptyhint")
        return lbl

    # ------------------------------------------------------------- month view
    def _dow_text(self, c):
        """Column c's heading: the full weekday name, or the short one once the
        full ones have been measured not to fit (see _dow_fit)."""
        if getattr(self, "_dow_short", False):
            # WEEKDAYS holds the two-letter forms the mini-calendar already
            # shows, and they are translated per language ("Δε", "Пн", "2ª").
            # _t() explicitly, then nbi18n's own upper-caser: nbi18n REFUSES to
            # translate a two-letter capital on purpose (its "FR" collided with
            # the weekday "Fr"), so up-casing first and letting the widget walk
            # translate it would leave "MO" in every language.
            return _upper(_t(WEEKDAYS[c]))
        return WEEKDAYS_FULL[c].upper()

    def _dow_fit(self, _grid, _alloc):
        """Once, per session: if the full names do not fit, switch to short.

        Asked after allocation rather than computed up front, because the answer
        depends on the panel width, the shipped face and the active language all
        at once, and the only honest way to know a label is being cut is to ask
        the laid-out label."""
        if getattr(self, "_dow_short", False):
            return
        for lbl in getattr(self, "_dow_labels", []):
            lay = lbl.get_layout()
            if lay is not None and lay.is_ellipsized():
                self._dow_short = True
                break
        if getattr(self, "_dow_short", False):
            # Not inside size-allocate: setting a label's text there re-enters
            # the layout that is already running.
            GLib.idle_add(self._dow_shorten)

    def _dow_shorten(self):
        for c, lbl in enumerate(getattr(self, "_dow_labels", [])):
            lbl.set_text(self._dow_text(c))
        return False

    def _build_month(self):
        if not self._month_has_events():
            self.body_area.pack_start(self._empty_hint(), False, False, 0)

        # weekday header
        dowg = Gtk.Grid(); dowg.set_column_homogeneous(True)
        self._dowg = dowg
        dowg.get_style_context().add_class("dowhead")
        self._dow_labels = []
        for c, w in enumerate(WEEKDAYS_FULL):
            lbl = Gtk.Label(label=self._dow_text(c), xalign=0)
            # Seven homogeneous columns sized to fit WEDNESDAY used to set the
            # whole window's minimum width. The names still print in full at
            # 1024 and shorten with an ellipsis only if a column gets narrower.
            lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            lbl.get_style_context().add_class("dowcell")
            dowg.attach(lbl, c, 0, 1, 1)
            self._dow_labels.append(lbl)
        # ...and "only if a column gets narrower" was measured in ENGLISH. At
        # 1024 the columns are ~103px, which fits MONDAY and ΔΕΥΤΕΡΑ and does
        # not fit DONNERSTAG, ПОНЕДЕЛЬНИК, PONIEDZIAŁEK, DONDERDAG, PONEDELJAK
        # or — worst — Portuguese, where SEGUNDA-FEIRA through SEXTA-FEIRA meant
        # FIVE of the seven headers were cut. So the header falls back to the
        # SHORT weekday names this app already uses in its own mini-calendar
        # (WEEKDAYS, translated: Δε · Mo · Пн · 2ª, which is the correct
        # Portuguese form), and only in the languages that need it. Nothing
        # changes for a language whose full names fit.
        dowg.connect("size-allocate", self._dow_fit)
        self.body_area.pack_start(dowg, False, False, 0)

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True); grid.set_row_homogeneous(True)
        grid.set_hexpand(True); grid.set_vexpand(True)

        # One focus stop for the whole month, with the keys that move the
        # selection inside it (see _on_month_grid_key). The cells stay
        # unfocusable: 42 tab stops per month is not keyboard access.
        grid.get_style_context().add_class("monthgrid")
        grid.set_can_focus(True)
        grid.connect("key-press-event", self._on_month_grid_key)
        # Say what the keys DO — an accessible name alone ("Month grid") tells
        # someone they have landed on it and nothing about how to leave.
        grid.set_tooltip_text(_t(
            "Arrow keys move a day, Page Up and Page Down move a month, "
            "Enter opens the selected day"))
        try:
            acc = grid.get_accessible()
            acc.set_name(_t("Month grid"))
            acc.set_description(_t(
                "Arrow keys move a day, Page Up and Page Down move a month, "
                "Enter opens the selected day"))
        except Exception:
            pass    # a11y bridge missing is never worth failing a render over
        self.month_grid = grid

        lead, dim = _monthrange(self.cur_y, self.cur_m)
        total = lead + dim
        rows = (total + 6) // 7
        for idx in range(rows * 7):
            col = idx % 7; row = idx // 7
            dnum = idx - lead + 1
            if dnum < 1 or dnum > dim:
                cell = Gtk.Box()
                cell.get_style_context().add_class("monthcell")
                cell.get_style_context().add_class("blankcell")
                grid.attach(cell, col, row, 1, 1)
                continue
            grid.attach(self._month_cell(date(self.cur_y, self.cur_m, dnum),
                                         col >= 5), col, row, 1, 1)
        self.body_area.pack_start(grid, True, True, 0)

    @staticmethod
    def month_key_target(keyval, sel):
        """The date a month-grid key moves the selection to, or None if the key
        is not one the grid claims.

        Pure arithmetic on a plain date, deliberately separate from the widget:
        this is the part that can be wrong (a month step has to clamp, a week
        step has to cross a month boundary), and it is the part a headless test
        can check without a display. See tools/calendar_grid_keys_selftest.py."""
        step = _MONTH_DAY_KEYS.get(keyval)
        if step is not None:
            return sel + timedelta(days=step)
        months = _MONTH_MONTH_KEYS.get(keyval)
        if months is not None:
            # 31 January + one month is 28/29 February, not 3 March: _add_months
            # clamps, which is the same rule the repeating-series code uses.
            return _add_months(sel, months)
        weekday = _MONTH_WEEKEND_KEYS.get(keyval)
        if weekday is not None:
            return sel + timedelta(days=weekday - sel.weekday())
        return None

    def _on_month_grid_key(self, _w, ev):
        """Keyboard navigation for the month grid, bounded to the grid itself.

        Only fires while the grid holds focus, and only for the keys in the
        maps above — anything else (including every modified key, so Ctrl+N
        still reaches _on_key) returns False and carries on to the window."""
        try:
            if ev.state & (Gdk.ModifierType.CONTROL_MASK |
                           Gdk.ModifierType.MOD1_MASK):
                return False
            if ev.keyval in _MONTH_OPEN_KEYS:
                # The selected day, opened — the same destination a '+N more'
                # chip goes to, reached without the pointer.
                self._on_view(None, "day")
                return True
            target = self.month_key_target(ev.keyval, self.sel)
            if target is None:
                return False
            self._select_day(target)
            return True
        except Exception:
            return False

    def _select_day(self, d):
        """Move the selection to `d` and re-render. Crossing a month boundary
        (31 Jan → 1 Feb) brings the displayed month with it, so the selected day
        is always a day the grid is actually showing."""
        self.sel = d
        self.cur_y, self.cur_m = d.year, d.month
        self._refresh()
        self._focus_month_grid()

    def _focus_month_grid(self):
        """Put focus back on the month grid after a rebuild.

        _refresh() destroys the grid that handled the key and builds a new one,
        so without this the FIRST arrow would work and every one after it would
        go nowhere — focus having fallen back to the window."""
        grid = self.month_grid
        if grid is None or self.view != "month":
            return
        try:
            grid.grab_focus()
        except Exception:
            pass

    @staticmethod
    def _hhmm(value):
        try:
            h = int(value)
            return "%02d:%02d" % (h, int(round((float(value) - h) * 60)))
        except (TypeError, ValueError):
            return "--:--"

    def _chip_detail(self, e):
        """'09:30 - 10:30  ·  Blood test  ·  Doctor' — the whole of an event in
        one line, for the hover on a chip too narrow to say it all."""
        when = (_t("All Day") if e.get("all_day") else "%s - %s" % (
            self._hhmm(e.get("start")), self._hhmm(e.get("end"))))
        parts = [when, e.get("title", ""), e.get("location", ""),
                 e.get("cal", ""), e.get("notes", "")]
        return "  ·  ".join(p for p in parts if p)

    def _month_cell(self, d, weekend):
        ev = Gtk.EventBox(); ev.set_hexpand(True); ev.set_vexpand(True)
        # 2px between chips: a six-row month whose busiest day carries three of
        # them has to fit six rows into a 768px screen.
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ctx = cell.get_style_context(); ctx.add_class("monthcell")
        if weekend:
            ctx.add_class("weekend")
        if d == self.sel and d != self.today:
            ctx.add_class("selcell")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        num = Gtk.Label(label=str(d.day))
        nctx = num.get_style_context(); nctx.add_class("daynum")
        if d == self.today:
            nctx.add_class("today")
        elif d == self.sel:
            nctx.add_class("selnum")
        top.pack_end(num, False, False, 0)
        cell.pack_start(top, False, False, 0)

        # Show up to three event chips; when a day holds more, keep two and add a
        # '+N more' chip that jumps to that day's Day view instead of silently
        # dropping the overflow. Each chip is its own click target that opens the
        # event for edit/delete (returns True so it doesn't also pick the day).
        day_events = self._events_on(d)
        shown = day_events if len(day_events) <= 3 else day_events[:2]
        for e in shown:
            chip = Gtk.Label(label=e["title"], xalign=0)
            chip.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            cc = chip.get_style_context()
            cc.add_class("evchip"); cc.add_class(self._event_chip_class(e))
            chipbox = Gtk.Button(); chipbox.set_relief(Gtk.ReliefStyle.NONE)
            chipbox.set_hexpand(bool(e.get("all_day", False)))
            chipbox.get_style_context().add_class("eventhit")
            chipbox.add(chip)
            # A month cell is too narrow to print the time beside the name
            # without eating the name, so it goes on the hover instead — along
            # with the full name, which is what an ellipsis just took away.
            chipbox.set_tooltip_text(self._chip_detail(e))
            chipbox.connect("clicked", self._on_event_clicked, e)
            cell.pack_start(chipbox, False, False, 0)
        if len(day_events) > len(shown):
            more = Gtk.Label(label=_t("+%d more") % (len(day_events) - len(shown)),
                             xalign=0)
            more.get_style_context().add_class("evmore")
            morebox = Gtk.Button(); morebox.set_relief(Gtk.ReliefStyle.NONE)
            morebox.get_style_context().add_class("eventhit")
            morebox.set_tooltip_text(_t("Show all events for this day"))
            morebox.add(more)
            morebox.connect("clicked", self._on_show_more, d)
            cell.pack_start(morebox, False, False, 0)

        ev.add(cell)
        # True: a click in the grid also parks keyboard focus there, so the
        # arrows carry on from the day just clicked. The chips above return
        # True from their own handler, so opening an event never gets here and
        # never silently moves the selection out from under the pointer.
        ev.connect("button-press-event", self._on_pick_day, d, True)
        return ev

    # -------------------------------------------------------------- day/week
    def _build_day(self):
        # The day carries a column header in the same style as the Week view's,
        # rather than the full date restated as a sub-heading — that line read
        # exactly like the title directly above it.
        all_day_ev, day_ev = self._partition_day_events(self.sel)
        if not day_ev:
            self.body_area.pack_start(self._empty_hint(), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        grid = Gtk.Grid(); grid.set_hexpand(True)
        grid.set_margin_start(24); grid.set_margin_end(40)

        corner = Gtk.Box(); corner.set_size_request(56, -1)
        grid.attach(corner, 0, 0, 1, 1)
        dayhead = Gtk.Label(
            label=_t("%s %d") % (
                _upper(_t(WEEKDAYS_FULL[self.sel.weekday()])), self.sel.day),
            xalign=0)
        dayhead.set_hexpand(True)
        dhc = dayhead.get_style_context(); dhc.add_class("weekdaycell")
        if self.sel == self.today:
            dhc.add_class("istoday")
        grid.attach(dayhead, 1, 0, 1, 1)

        # All-day events live above the clock, not misleadingly at 00:00.
        if all_day_ev:
            band = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            band.get_style_context().add_class("alldayband")
            for event in all_day_ev:
                band.pack_start(self._time_chip(event, True), False, False, 0)
            grid.attach(band, 1, 1, 1, 1)
            row_offset = 1
        else:
            row_offset = 0

        for i, h in enumerate(HOURS):
            i += 1 + row_offset         # row 0 is the day header
            hl = Gtk.Label(label="%02d:00" % h, xalign=1)
            hl.get_style_context().add_class("hourlabel")
            hl.set_size_request(56, 62); hl.set_valign(Gtk.Align.START)
            hl.set_margin_end(12)
            grid.attach(hl, 0, i, 1, 1)
            slot = Gtk.EventBox()
            slot.get_style_context().add_class("hourslot")
            slot.set_hexpand(True); slot.set_size_request(-1, 62)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            slot.add(inner)
            # Draw the chip in every hour slot the event covers (start hour
            # through its 'end'), so a 3h event fills three rows and reads
            # visibly taller than a 30min one. Only the first row carries the
            # title — the rows below continue the same block — otherwise a 3h
            # event printed its name three times and read as three separate
            # events. _covers also pins out-of-band events to an edge row so
            # they never disappear here.
            for e in day_ev:
                if self._covers(e, h):
                    # expand+fill: the chip covers its whole hour row, so the
                    # rows of one event join into a single tall block instead
                    # of a titled bar followed by a stranded blank one.
                    inner.pack_start(
                        self._time_chip(e, h == self._first_row(e)),
                        True, True, 0)
            slot.connect("button-press-event", self._on_pick_slot, self.sel, h)
            grid.attach(slot, 1, i, 1, 1)
        scroll.add(grid)
        self.body_area.pack_start(scroll, True, True, 0)
        self._scroll_to_hour(scroll, self._focus_hour([self.sel]), 62)

    def _build_week(self):
        days = self._week_dates()
        if not any(self._events_on(d) for d in days):
            self.body_area.pack_start(self._empty_hint(), False, False, 0)

        # One grid holds the weekday header (row 0) and the hour rows, so the day
        # columns line up exactly — the gutter column stays narrow while the
        # seven day columns share the remaining width equally.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        grid = Gtk.Grid(); grid.set_hexpand(True)
        grid.set_margin_start(24); grid.set_margin_end(24)

        corner = Gtk.Box(); corner.set_size_request(56, -1)
        grid.attach(corner, 0, 0, 1, 1)
        for i, d in enumerate(days):
            lbl = Gtk.Label(label=_t("%s %d") % (_t(WEEKDAYS[i]), d.day),
                            xalign=0)
            lbl.set_hexpand(True)
            cc = lbl.get_style_context(); cc.add_class("weekdaycell")
            if d == self.today:
                cc.add_class("istoday")
            grid.attach(lbl, i + 1, 0, 1, 1)

        cells = {}
        for i, h in enumerate(HOURS):
            hl = Gtk.Label(label="%02d:00" % h, xalign=1)
            hl.get_style_context().add_class("hourlabel")
            hl.set_size_request(56, 52); hl.set_valign(Gtk.Align.START)
            hl.set_margin_end(10)
            grid.attach(hl, 0, i + 1, 1, 1)
            for di in range(7):
                slot = Gtk.EventBox()
                slot.get_style_context().add_class("weekcell")
                slot.set_hexpand(True); slot.set_size_request(-1, 52)
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                slot.add(inner)
                slot.connect("button-press-event",
                             self._on_pick_slot, days[di], h)
                grid.attach(slot, di + 1, i + 1, 1, 1)
                cells[(di, h)] = inner
        for di, d in enumerate(days):
            for e in self._partition_day_events(d)[1]:
                # Fill every hour cell the event spans (start hour .. end), so
                # the week block grows with its duration; only its first row is
                # titled (see _build_day); out-of-band events are pinned to an
                # edge row by _covers rather than vanishing.
                first = self._first_row(e)
                for h in HOURS:
                    if self._covers(e, h):
                        slot = cells.get((di, h))
                        if slot is not None:
                            slot.pack_start(
                                self._time_chip(e, h == first, narrow=True),
                                True, True, 0)
        scroll.add(grid)
        self.body_area.pack_start(scroll, True, True, 0)
        self._scroll_to_hour(scroll, self._focus_hour(days), 52)

    def _focus_hour(self, days):
        """The hour row the Day / Week grid should open on: the earliest event
        across `days`, else the current hour when today is among them, else the
        top of the band. Without this the grid always opened at 08:00, so a day
        holding one 19:00 appointment showed an empty morning and looked like a
        day with nothing in it."""
        starts = [e["start"] for d in days for e in self._events_on(d)]
        if starts:
            return max(HOURS[0], min(int(min(starts)), HOURS[-1]))
        if self.today in days:
            try:
                import time as _time
                return max(HOURS[0], min(_time.localtime().tm_hour, HOURS[-1]))
            except Exception:
                pass
        return HOURS[0]

    def _scroll_to_hour(self, scroll, hour, row_h):
        """Bring the Day / Week grid's `hour` row into view, scrolling the LEAST
        that does it. Scrolling further would push the day-name header row off
        the top, and in the Week view that header is the only thing saying which
        column is which day — so a day whose first event is at 10:00 is left
        exactly where it was.

        Deferred to an idle callback: the adjustment has no range until the grid
        has been allocated, and a value set before that is silently discarded."""
        if hour <= HOURS[0]:
            return

        def _apply():
            try:
                adj = scroll.get_vadjustment()
                if adj is None:
                    return False
                page = adj.get_page_size()
                upper = adj.get_upper()
                # whatever is above the first hour row is the header row
                head = max(0.0, upper - len(HOURS) * row_h)
                top = head + (hour - HOURS[0]) * row_h
                need = top + 2 * row_h - page      # the row, plus a little after
                adj.set_value(max(0.0, min(need, max(0.0, upper - page))))
            except Exception:
                pass
            return False
        GLib.idle_add(_apply)

    def _first_row(self, e):
        """The first hour row of the Day/Week grid that event `e` paints in —
        the row that carries its title. None when it paints in none of them
        (impossible in practice: _covers pins out-of-band events to an edge)."""
        for h in HOURS:
            if self._covers(e, h):
                return h
        return None

    def _time_chip(self, e, lead=True, narrow=False):
        """One block of an event in the Day/Week grid. `lead` marks the row the
        event starts in — the only one that prints the title; later rows are
        untitled continuations of the same block. `narrow` caps the width the
        chip ASKS for (it still fills its column) so one long title cannot
        stretch a week-view day column wider than the other six."""
        label = e["title"] if lead else ""
        if lead and e.get("location"):
            label += "  ·  " + e["location"]
        chip = Gtk.Label(label=label, xalign=0)
        chip.set_ellipsize(3)
        if narrow:
            chip.set_max_width_chars(6)
        # The title sits at the TOP of the block — where the event starts —
        # rather than floating in the middle of a tall one.
        chip.set_yalign(0.0)
        cc = chip.get_style_context()
        cc.add_class("evchip"); cc.add_class(self._event_chip_class(e))
        if not lead:
            cc.add_class("evcont")
        # A continuation sits flush under the row above so the hours of one
        # event read as a single block rather than a stack of small chips.
        chip.set_margin_top(2 if lead else 0)
        chip.set_margin_start(3); chip.set_margin_end(3)
        # Wrap in a native button so the chip opens the event from pointer or
        # keyboard. Continuation rows remain clickable but stay out of the Tab
        # chain; the event's lead row is its single keyboard stop.
        # for edit/delete and stops the click from reaching the empty-slot
        # handler (which would otherwise start a blank New Event).
        box = Gtk.Button(); box.set_relief(Gtk.ReliefStyle.NONE)
        box.get_style_context().add_class("eventhit")
        box.set_can_focus(lead)
        box.set_tooltip_text(self._chip_detail(e))
        box.add(chip)
        box.connect("clicked", self._on_event_clicked, e)
        return box

    # ------------------------------------------------------------ persistence
    def _load_class_events(self):
        """Build the mirrored class events from the Academics store.

        Academics keeps a WEEKLY PATTERN ("Organic Chemistry, Wednesdays
        14:00-15:30"), not dated occurrences, so this expands that pattern
        across a window either side of today — far enough that browsing a few
        months back or forward still shows the timetable, bounded so a term of
        classes can never turn into an unbounded list.

        Never raises: a missing or damaged academics.json simply means no
        classes are mirrored, which is also the correct answer for anyone who
        does not use that app.
        """
        # Resolved per read, not once at import: on a machine that upgraded from
        # the release where this app was called Academic Notes the term still
        # lives under the old name, and mirroring nothing would quietly empty
        # the timetable out of the calendar. Matches academics.LEGACY_FILE.
        path = ACADEMICS_FILE
        try:
            if not os.path.exists(path) and os.path.exists(ACADEMICS_LEGACY):
                path = ACADEMICS_LEGACY
        except OSError:
            pass
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        classes = data.get("classes")
        if not isinstance(classes, list):
            return []

        today = date.today()
        first = today - timedelta(days=today.weekday() + 7 * _CLASS_WEEKS_BACK)
        out = []
        for c in classes:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or c.get("label") or "").strip()
            if not name:
                continue
            color = str(c.get("color") or "")
            default_room = str(c.get("room") or "")
            meets = c.get("meets")
            if not isinstance(meets, list):
                continue
            for m in meets:
                if not isinstance(m, dict):
                    continue
                try:
                    weekday = int(m.get("day"))
                except (TypeError, ValueError):
                    continue
                start = _hhmm_to_hours(m.get("start"))
                if not 0 <= weekday <= 6 or start is None:
                    continue
                end = _hhmm_to_hours(m.get("end"))
                if end is None or end <= start:
                    end = min(start + 1.0, 23.99)
                room = str(m.get("room") or default_room)
                for wk in range(_CLASS_WEEKS_BACK + _CLASS_WEEKS_AHEAD):
                    d = first + timedelta(days=weekday + 7 * wk)
                    out.append({
                        "id": "class:%s:%d:%s:%s" % (name, weekday,
                                                     m.get("start"), d),
                        "date": d, "start": start, "end": end,
                        "title": name, "cal": CLASSES_CAL,
                        "repeat": "none", "series": "",
                        "color": color,
                        "room": room,
                        # The flag every write path checks. A derived event is
                        # not the user's to edit here.
                        "derived": "academics",
                    })
        return out

    @staticmethod
    def _event_list(data):
        """The list of event records inside whatever calendar.json holds.

        The store is a bare list, but a wrapped one ({"events": [...]}) or one
        keyed by id is still the user's calendar, and _save_events rewrites the
        whole file — so refusing to recognise the wrapper used to delete every
        event in it on close. Returns None when there is no list of records to
        be found, which is the only honest 'this is not a calendar'."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("events")
            if isinstance(inner, list):
                return inner
            vals = list(data.values())
            if vals and all(isinstance(v, dict) for v in vals):
                return vals                      # object keyed by event id
            for v in vals:
                if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                    return v
        return None

    def _load_events(self):
        """Restore saved events from calendar.json (the shape _save_events
        writes). On a fresh install — no file, or an unreadable/foreign one —
        ship empty. An existing empty list is honoured. Never raises.

        Records this loader cannot make sense of are kept verbatim in
        self._orphans and written back out by _save_events. An event whose date
        no longer parses cannot be drawn on a grid of days, but its TITLE is
        something the user typed and nothing else can reproduce, so it is
        carried through the round trip instead of being quietly deleted on
        close."""
        self._orphans = []
        self._events_quarantine = False
        try:
            with open(EVENTS_FILE) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except Exception:
            # Unreadable bytes. nbapp.atomic_write_json moves the original
            # aside (preserve_damaged) immediately before its next replacing
            # write, so the appointments' bytes land in calendar.json.damaged-*
            # and persistence KEEPS WORKING. Refusing every later write kept
            # the file but silently killed saving for the whole session —
            # journal shipped that cure and the save-failure gate caught it.
            return []
        items = self._event_list(data)
        if items is None and data:
            # Parsed fine, but not a shape this loader reads as events — the
            # case only the app can judge (valid JSON sails through nbapp's
            # parse check). _save_events moves the file aside immediately
            # before its replacing write, so there is never a window with no
            # store, and recovery bytes end up where the OS contract says.
            self._events_quarantine = True
            return []
        if items is None:
            return []
        out = []
        for item in items:
            ev = self._norm_event(item)
            if ev is not None:
                out.append(ev)
            elif item not in (None, "", [], {}):
                self._orphans.append(item)
        return out

    def _norm_event(self, item):
        """Coerce one stored record into the in-memory event shape, or None if
        it can't be salvaged (bad/missing date). Carries the record's stable id
        (minting one when the record has none — e.g. an event tasks.py appended)
        and remembers the content key it was read under, so merge-on-write can
        recognise it again on disk. An unknown calendar name is kept as-is (it
        still renders, neutrally colored) rather than being silently reassigned.
        Never raises."""
        if not isinstance(item, dict):
            return None
        d = self._iso_to_date(item.get("date"))
        if d is None:
            return None
        all_day = bool(item.get("all_day", False))
        try:
            start = float(item.get("start", 9.0)) if not all_day else 0.0
            end = float(item.get("end", start + 1)) if not all_day else 24.0
        except (TypeError, ValueError):
            start, end = 9.0, 10.0
        default_cal = self._cal_names()[0] if self.calendars else "Personal"
        cal = str(item.get("cal", default_cal)) or default_cal
        rid = str(item.get("id") or "").strip()
        rep = str(item.get("repeat", "none") or "none")
        ev = {"id": rid if rid else _gen_id(), "date": d, "start": start,
              "end": end, "title": str(item.get("title", "")), "cal": cal,
              "repeat": rep if rep in REPEAT_LABELS else "none",
              "series": str(item.get("series", "") or ""),
              "location": str(item.get("location", "") or ""),
              "notes": str(item.get("notes", "") or ""),
              "all_day": all_day,
              "series_end": str(item.get("series_end", "") or ""),
              "pattern_date": str(item.get("pattern_date", "") or ""),
              "detached": bool(item.get("detached", False)),
              "cancelled": bool(item.get("cancelled", False))}
        ev["_loadkey"] = self._content_key(ev["title"], d, start)
        return ev

    def _content_key(self, title, d, start):
        """A stable (title, date, start) match key, used to recognise an event
        that has no id (e.g. one tasks.py appended to the shared store). The
        date normalises to ISO and the start to float so an in-memory event and
        its on-disk record collate to the same token."""
        if isinstance(d, date):
            iso = self._date_to_iso(d)
        else:
            parsed = self._iso_to_date(d)
            iso = self._date_to_iso(parsed) if parsed is not None else str(d)
        try:
            s = float(start)
        except (TypeError, ValueError):
            s = 0.0
        return ("c", str(title), iso, s)

    def _event_tokens(self, ev):
        """Match tokens for an event: its stable id, its current content key,
        and the content key it was first read under (so an event edited in
        memory still matches its as-yet-id-less on-disk record)."""
        toks = set()
        eid = ev.get("id")
        if eid:
            toks.add(("id", str(eid)))
        toks.add(self._content_key(ev.get("title", ""), ev.get("date"),
                                   ev.get("start")))
        lk = ev.get("_loadkey")
        if lk is not None:
            toks.add(lk)
        return toks

    def _mark_seen(self, ev):
        """Record an event's tokens as known to this session."""
        self._seen |= self._event_tokens(ev)

    def _ensure_id(self, ev):
        if not ev.get("id"):
            ev["id"] = _gen_id()
        return ev["id"]

    def _read_events_file(self):
        """The raw list of event dicts currently in calendar.json, or None when
        the file is missing / unreadable / not a list. Never raises."""
        try:
            with open(EVENTS_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None
        items = self._event_list(data)
        if items is None:
            return None
        return [it for it in items if isinstance(it, dict)]

    def _merge_disk_events(self):
        """Fold back any event that appeared in calendar.json since this session
        last synced — e.g. one the Tasks app appended while Calendar was open —
        so the wholesale rewrite below never silently drops it. An event this
        session deliberately deleted (its tokens are in self._seen but it is no
        longer in memory) is NOT resurrected; an event edited in memory wins over
        its stale on-disk copy. Foreign additions are adopted into self.events so
        they are neither lost on this write nor mistaken for a deletion next
        time. Never raises."""
        disk = self._read_events_file()
        if not disk:
            return
        mem_tokens = set()
        for e in self.events:
            mem_tokens |= self._event_tokens(e)
        orphans = getattr(self, "_orphans", None)
        if orphans is None:
            orphans = self._orphans = []
        for raw in disk:
            norm = self._norm_event(raw)
            if norm is None:
                # Unsalvageable: carried through the write untouched rather than
                # dropped (the equality test is what stops our own orphans, read
                # back off disk here, from being appended twice per save).
                if raw not in orphans:
                    orphans.append(raw)
                continue
            rt = self._event_tokens(norm)
            if rt & mem_tokens:
                continue                 # already in memory (memory wins on edits)
            if rt & self._seen:
                continue                 # known here and now gone => deleted
            self.events.append(norm)     # genuinely foreign, concurrent add
            self._mark_seen(norm)
            mem_tokens |= rt

    def _event_record(self, e):
        """One event as a JSON-safe record: the stable id plus the flat
        {date,start,end,title,cal} shape tasks.py / widgets.py read (they ignore
        the extra id key). The date is an ISO YYYY-MM-DD string.

        A repeating event is written out as REAL records, one per occurrence,
        tagged with the rule and a shared series id. That is deliberate: the
        Tasks schedule rail and the desktop Calendar card read this file without
        knowing repeats exist, and they still have to show next Tuesday's bin
        day. The tags are only there so the series can be edited or removed as a
        whole; anything that ignores them still reads a correct calendar."""
        rec = {"id": self._ensure_id(e),
               "date": self._date_to_iso(e["date"]),
               "title": e["title"], "cal": e["cal"]}
        if not e.get("all_day", False):
            rec.update({"start": e["start"], "end": e["end"]})
        for key, default in (("location", ""), ("notes", ""),
                             ("all_day", False), ("series_end", ""),
                             ("pattern_date", ""),
                             ("detached", False), ("cancelled", False)):
            if e.get(key, default) != default:
                rec[key] = e[key]
        if e.get("series"):
            rec["series"] = e["series"]
            rec["repeat"] = e.get("repeat", "none")
        return rec

    def _save_events(self, merge=True):
        """Persist every event to calendar.json (date as an ISO YYYY-MM-DD
        string). Written on every add / edit / delete and on close.

        MERGE ON WRITE by default: re-read the file first and fold back any event
        another writer appended while Calendar was open (the Tasks app's schedule
        add writes the same store), so a concurrently-added event is never
        clobbered by this wholesale rewrite. merge=False writes authoritatively —
        used only by New / Open, which intentionally replace the whole store.
        Never crashes the app on an I/O error."""
        try:
            os.makedirs(CFG_DIR, exist_ok=True)
        except Exception:
            pass
        pending = getattr(self, "_events_quarantine", False)
        if merge and not pending:
            try:
                self._merge_disk_events()
            except Exception:
                pass   # a merge hiccup must never cost us the in-memory events
        if pending:
            # The store parsed but was not a calendar (see _load_events):
            # move it aside NOW, immediately before the replacing write — the
            # same moment nbapp picks for files it can detect — and skip the
            # merge, which could only re-read the shape we already refused.
            _quarantine_store(EVENTS_FILE)
            self._events_quarantine = False
        try:
            data = [self._event_record(e) for e in self.events]
            # Records this session could not read (see _load_events) ride along
            # untouched, so a title the user typed is not deleted by the act of
            # opening and closing the app. New / Open replace the store on
            # purpose (merge=False), and those carry nothing forward.
            if merge:
                data.extend(getattr(self, "_orphans", []))
            else:
                self._orphans = []
            nbapp.atomic_write_json(EVENTS_FILE, data)
            self._save_warned = False
        except Exception as exc:
            # See academics._save_to_disk. Silence here reads as "Calendar
            # deleted my appointments": the store keeps whatever the last write
            # that succeeded put there, so an event added after the disk filled
            # up is simply absent next time. Warn once per run of failures.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash_status(
                        nbapp.save_failure_reason(exc, EVENTS_FILE))
                except Exception:
                    pass

    def _on_destroy(self, *_):
        # Idempotent: GTK can emit "destroy" more than once through nested
        # teardown paths, and a second pass must not remove a source id that has
        # since been reused by another timer, nor write the store twice.
        if getattr(self, "_closed", False):
            return
        # Marked BEFORE anything else, so a rollover poll that fires during the
        # saves below sees a closing window and stays away from the widgets.
        self._closed = True
        rid = getattr(self, "_rollover_id", 0)
        self._rollover_id = 0
        if rid:
            try:
                GLib.source_remove(rid)
            except Exception:
                pass
        self._save_events()
        self._save_calendars()

    @staticmethod
    def _date_to_iso(d):
        return "%04d-%02d-%02d" % (d.year, d.month, d.day)

    @staticmethod
    def _iso_to_date(s):
        """Parse 'YYYY-MM-DD' with plain int splitting — never time.strptime /
        `import calendar` (this file shadows the stdlib calendar module). Returns
        None on anything malformed."""
        try:
            y, m, d = str(s).split("-")
            return date(int(y), int(m), int(d))
        except (ValueError, TypeError):
            return None

    def _events_on(self, d):
        """Everything on a day: the user's own events plus the classes mirrored
        from Academics. The mirror obeys the same show/hide toggle as any
        calendar, so a term timetable can be turned off without leaving
        Academics."""
        return sorted(
            [e for e in list(self.events) + list(self.class_events)
             if e["date"] == d and not e.get("cancelled")
             and self.cals_on.get(e["cal"], True)],
            key=lambda e: (not e.get("all_day", False), e.get("start", 0)))

    def _partition_day_events(self, d):
        """Return (all-day, timed) visible events for the day."""
        events = self._events_on(d)
        return ([e for e in events if e.get("all_day", False)],
                [e for e in events if not e.get("all_day", False)])

    def _covers(self, e, h):
        """Whether event `e` should paint in the Day/Week grid row for hour `h`.
        Normal events cover their start hour through (not including) their end.
        An event lying WHOLLY outside the visible 08:00–21:00 band — e.g. an
        early-morning item typed into the Tasks quick-add, or a foreign record —
        is pinned to the nearest edge row so it never silently vanishes from
        these views. Never raises on a malformed start/end."""
        try:
            s = int(e["start"])
            en = float(e["end"])
        except (TypeError, ValueError, KeyError):
            s, en = 9, 10.0
        if en <= HOURS[0]:            # ends at/before 08:00 -> pin to first row
            return h == HOURS[0]
        if s >= HOURS[-1] + 1:        # starts at/after 21:00 -> pin to last row
            return h == HOURS[-1]
        return s <= h < en

    def _month_has_events(self):
        return any(e["date"].year == self.cur_y and e["date"].month == self.cur_m
                   and self.cals_on.get(e["cal"], True) for e in self.events)

    def _week_dates(self):
        start = self.sel - timedelta(days=self.sel.weekday())
        return [start + timedelta(days=i) for i in range(7)]

    def _shift_month(self, delta):
        m = self.cur_m - 1 + delta
        self.cur_y += m // 12
        self.cur_m = m % 12 + 1
        self.sel = date(self.cur_y, self.cur_m, 1)

    def _on_prev(self, *_):
        if self.view == "month":
            self._shift_month(-1)
        else:
            self.sel -= timedelta(days=7 if self.view == "week" else 1)
            self.cur_y, self.cur_m = self.sel.year, self.sel.month
        self._refresh()

    def _on_next(self, *_):
        if self.view == "month":
            self._shift_month(1)
        else:
            self.sel += timedelta(days=7 if self.view == "week" else 1)
            self.cur_y, self.cur_m = self.sel.year, self.sel.month
        self._refresh()

    def _on_today(self, *_):
        self.today = date.today()
        self.sel = self.today
        self.cur_y, self.cur_m = self.today.year, self.today.month
        self._refresh()

    def _on_view(self, _btn, key):
        self.view = key
        self._refresh()

    def _on_pick_day(self, _w, _ev, d):
        self._select_day(d)
        return True

    def _on_pick_slot(self, _w, _ev, d, h):
        """Clicking an empty Day/Week time slot starts a New Event on that day,
        pre-seeded to that hour — the same flow the New Event button uses. Bad
        day/hour just falls back to the plain (09:00) dialog, never crashes."""
        try:
            self.sel = d
            self.cur_y, self.cur_m = d.year, d.month
            self._new_event_hour = int(h)
        except (TypeError, ValueError, AttributeError):
            self._new_event_hour = None
        self._open_new_event()
        return True

    # ------------------------------------------------------------- new event
    def _open_new_event(self, *_):
        """Sidebar / File-menu / empty-slot entry point — a blank event form."""
        self._event_dialog(None)

    def _on_event_clicked(self, _w, e):
        """Click an event chip (month, day or week) → its detail dialog,
        prefilled, with Save (edit in place) and Delete. Returns True so the
        click doesn't also fall through to the day-pick / new-event handler on
        the cell or slot behind the chip."""
        if e.get("derived"):
            # A class is a reflection of the timetable in Academics. Offering an
            # editor here would either lie (the edit cannot be saved) or create
            # a second copy that drifts from the real one — so send them to the
            # place the change actually belongs.
            self._open_derived(e)
            return True
        self._event_dialog(e)
        return True

    def _open_derived(self, e):
        """Explain where a mirrored event lives, and offer to go there."""
        # A card, not a stock MessageDialog: the rest of this app's dialogs are
        # undecorated Papertone sheets, and a window-manager title bar here made
        # one explanation look like it came from a different program.
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        _box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        _box.set_margin_top(18); _box.set_margin_bottom(6)
        _box.set_margin_start(20); _box.set_margin_end(20)
        _hd = Gtk.Label(label=_t("“%s” is a class") % e.get("title", ""),
                        xalign=0)
        _hd.get_style_context().add_class("dlghead")
        _box.pack_start(_hd, False, False, 0)
        _msg = Gtk.Label(
            label=_t("Class times are edited in Academics."), xalign=0)
        _msg.set_line_wrap(True); _msg.set_width_chars(38)
        _box.pack_start(_msg, False, False, 0)
        dlg.get_content_area().add(_box)
        dlg.add_button(_t("Not Now"), Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(_t("Open Academics"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                subprocess.Popen(
                    ["python3", os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "academics.py"),
                     "schedule"],
                    env=dict(os.environ,
                             PYTHONPATH=os.path.dirname(
                                 os.path.abspath(__file__))))
            except (OSError, ValueError):
                pass

    def _on_show_more(self, _w, d):
        """The month cell's '+N more' overflow chip → that day's Day view, so
        every event is reachable even when the cell can't show them all."""
        self.sel = d
        self.cur_y, self.cur_m = d.year, d.month
        self.view = "day"
        self._refresh()
        return True

    def _event_dialog(self, existing=None):
        """One dialog for both New Event and Event detail. With `existing` it
        opens prefilled and offers Save (edits the event in place) and Delete
        (removes it, then persists); without, it's the blank New Event form.
        Either way an empty title keeps the dialog open and flags the field
        rather than silently discarding the event. Every successful add / edit /
        delete is written straight to disk."""
        editing = existing is not None
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        if editing:
            delbtn = dlg.add_button("Delete", RESPONSE_DELETE)
            delbtn.get_style_context().add_class("destructive")
        ok = dlg.add_button("Save" if editing else "Add Event",
                            Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        area = dlg.get_content_area()
        area.set_spacing(12)
        area.set_margin_top(24); area.set_margin_bottom(16)
        area.set_margin_start(28); area.set_margin_end(28)

        t = Gtk.Label(label=_t("Edit Event") if editing else "New Event", xalign=0)
        t.get_style_context().add_class("dlgtitle")
        area.pack_start(t, False, False, 0)
        # The event's day is editable here, so an event put on the wrong day can
        # be moved without deleting and re-creating it. ‹ › step one day either
        # way; the date reads back in full so the target day is never ambiguous.
        dayval = [existing["date"] if editing else self.sel]
        daterow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sub = Gtk.Label(xalign=0)
        sub.get_style_context().add_class("dlgsub")

        def _fmt_day(*_):
            d = dayval[0]
            sub.set_text(_t("%s, %d %s %d") % (
                _t(WEEKDAYS_FULL[d.weekday()]), d.day,
                _t(MONTHS[d.month - 1]), d.year))
        _fmt_day()
        daterow.pack_start(sub, True, True, 0)

        def _step_day(delta):
            try:
                dayval[0] = dayval[0] + timedelta(days=delta)
            except (OverflowError, OSError, ValueError):
                return   # at the date min/max bound — leave the day unchanged
            _fmt_day()

        prevd = Gtk.Button(); prevd.set_relief(Gtk.ReliefStyle.NONE)
        prevd.get_style_context().add_class("daystep")
        prevd.add(nbicons.image("back", 14, INK))
        prevd.set_tooltip_text(_t("Previous day"))
        prevd.connect("clicked", lambda *_: _step_day(-1))
        nextd = Gtk.Button(); nextd.set_relief(Gtk.ReliefStyle.NONE)
        nextd.get_style_context().add_class("daystep")
        nextd.add(nbicons.image("fwd", 14, INK))
        nextd.set_tooltip_text(_t("Next day"))
        nextd.connect("clicked", lambda *_: _step_day(1))
        daterow.pack_start(prevd, False, False, 0)
        daterow.pack_start(nextd, False, False, 0)
        area.pack_start(daterow, False, False, 0)

        title = Gtk.Entry(); title.set_placeholder_text(_t("Event title"))
        title.set_activates_default(True)  # Enter submits (Save / Add Event)
        if editing:
            title.set_text(existing["title"])
        area.pack_start(title, False, False, 0)

        location = Gtk.Entry()
        location.set_placeholder_text(_t("Location"))
        location.set_max_length(120)
        if editing:
            location.set_text(existing.get("location", ""))
        area.pack_start(location, False, False, 0)

        all_day = Gtk.CheckButton(label=_t("All Day"))
        all_day.set_active(bool(existing and existing.get("all_day", False)))
        area.pack_start(all_day, False, False, 0)

        # Inline empty-title flag (hidden until the user tries to save blank);
        # clears itself the moment they start typing.
        err = Gtk.Label(label=_t("Enter an event title."), xalign=0)
        err.get_style_context().add_class("dlgerror")
        err.set_no_show_all(True)
        area.pack_start(err, False, False, 0)

        def _clear_err(*_):
            title.get_style_context().remove_class("field-error")
            err.hide()
        title.connect("changed", _clear_err)

        fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        start = Gtk.ComboBoxText()
        for h in HOURS:
            for mm in ("00", "30"):
                start.append_text("%02d:%s" % (h, mm))
        start.set_active(self._start_index(existing))
        dur = Gtk.ComboBoxText()
        for label in ("30 minutes", "1 hour", "2 hours", "3 hours"):
            dur.append_text(label)
        dur.set_active(self._duration_index(existing))
        fields.pack_start(self._field("Starts", start), True, True, 0)
        fields.pack_start(self._field("Duration", dur), True, True, 0)
        area.pack_start(fields, False, False, 0)
        all_day.connect("toggled", lambda w: fields.set_sensitive(
            not w.get_active()))
        fields.set_sensitive(not all_day.get_active())

        calrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        calpick = Gtk.ComboBoxText()
        cal_names = self._cal_names()
        # Preserve an event's own calendar even if it is no longer defined.
        if editing and existing["cal"] not in cal_names:
            cal_names = [existing["cal"]] + cal_names
        for cname in cal_names:
            calpick.append_text(cname)
        if editing and existing["cal"] in cal_names:
            calpick.set_active(cal_names.index(existing["cal"]))
        elif cal_names:
            calpick.set_active(0)
        calchoice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        calswatch = Gtk.DrawingArea(); calswatch.set_size_request(18, 18)
        def _draw_event_cal(_area, ctx):
            idx = calpick.get_active()
            name = cal_names[idx] if 0 <= idx < len(cal_names) else ""
            self._round_rect(ctx, 1, 1, 16, 16, 3)
            ctx.set_source_rgb(*nbicons._hex(self._cal_color(name)))
            ctx.fill()
            return False
        calswatch.connect("draw", _draw_event_cal)
        calpick.connect("changed", lambda *_: calswatch.queue_draw())
        calchoice.pack_start(calswatch, False, False, 0)
        calchoice.pack_start(calpick, True, True, 0)
        calrow.pack_start(self._field("Calendar", calchoice), True, True, 0)

        # Bin day, the monthly standing order, a Tuesday class: these are the
        # events people actually keep, and before this they had to be typed out
        # one week at a time. Picking a rule writes the whole series.
        reppick = Gtk.ComboBoxText()
        for _key, label in REPEATS:
            reppick.append_text(label)
        cur_rule = existing.get("repeat", "none") if editing else "none"
        keys = [k for k, _l in REPEATS]
        reppick.set_active(keys.index(cur_rule) if cur_rule in keys else 0)
        calrow.pack_start(self._field("Repeats", reppick), True, True, 0)
        area.pack_start(calrow, False, False, 0)

        end_entry = Gtk.Entry()
        end_entry.set_placeholder_text(_t("Optional end date (YYYY-MM-DD)"))
        if editing:
            end_entry.set_text(existing.get("series_end", ""))
        area.pack_start(self._field(_t("Series End Date"), end_entry),
                        False, False, 0)

        notes = Gtk.TextView()
        notes.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        notes.set_size_request(-1, 64)
        if editing:
            notes.get_buffer().set_text(existing.get("notes", ""))
        area.pack_start(self._field(_t("Notes"), notes), False, False, 0)

        dlg.show_all()
        # Focus the title so typing starts there, not on the day-step arrows the
        # dialog now shows above it (they are earlier in the tab order).
        title.grab_focus()
        while True:
            resp = dlg.run()
            if resp == RESPONSE_DELETE:
                # Deleting an event is destructive — confirm first. Declining
                # keeps the edit dialog open rather than losing the event. A
                # repeating event asks WHICH: cancelling one week's class must
                # not wipe the whole term.
                if not self._delete_event(existing):
                    continue
                break
            if resp != Gtk.ResponseType.OK:
                break   # Cancel / Escape / close — no change
            name = title.get_text().strip()
            if not name:
                # Don't discard — flag the field and keep the dialog up.
                title.get_style_context().add_class("field-error")
                err.show()
                title.grab_focus()
                continue
            # Parse the combos defensively: a malformed time or an inactive
            # duration (get_active() == -1) must not crash the handler.
            try:
                st = start.get_active_text() or "09:00"
                hh, mm = st.split(":")
                sh = int(hh) + int(mm) / 60.0
                dh = {0: 0.5, 1: 1, 2: 2, 3: 3}[dur.get_active()]
            except (ValueError, KeyError):
                break
            default_cal = cal_names[0] if cal_names else "Personal"
            # By INDEX into cal_names, never calpick.get_active_text(): nbi18n
            # translates what a ComboBoxText shows, so the stock calendar
            # "Personal" reads back as "Личное" / "Personnel" / "个人" and that
            # is what got filed on the event. The event then belonged to a
            # calendar that does not exist — no colour, dropped by the calendar
            # filter, and reopening it silently reset the picker to row 0.
            ci = calpick.get_active()
            cal = cal_names[ci] if 0 <= ci < len(cal_names) else default_cal
            ridx = reppick.get_active()
            rule = keys[ridx] if 0 <= ridx < len(keys) else "none"
            # Honour the chosen duration verbatim — do NOT clamp end back to
            # 21:00, or a "3 hours" pick made late in the day would silently
            # store (and read back as) 30 minutes. The Day/Week band still caps
            # the drawn rows at 20:00; _covers keeps the paint in-bounds.
            end = sh + dh
            target_day = dayval[0]
            nb = notes.get_buffer()
            note_text = nb.get_text(nb.get_start_iter(), nb.get_end_iter(), True)
            is_all_day = all_day.get_active()
            fields = {"start": 0.0 if is_all_day else sh,
                      "end": 24.0 if is_all_day else end,
                      "title": name, "cal": cal,
                      "location": location.get_text().strip(),
                      "notes": note_text, "all_day": is_all_day}
            series_end = self._iso_to_date(end_entry.get_text().strip())
            if end_entry.get_text().strip() and series_end is None:
                end_entry.get_style_context().add_class("field-error")
                end_entry.grab_focus()
                continue
            if editing:
                fields["series_end"] = (self._date_to_iso(series_end)
                                        if series_end else "")
                if existing.get("series") and rule == cur_rule:
                    scope = self._choose_series_scope(_t("Edit Repeating Event"))
                    if scope is None:
                        continue
                    self._edit_series_scope(existing, target_day, fields, scope)
                else:
                    self._save_edit(existing, target_day, fields, rule, cur_rule)
            else:
                self._create_event(target_day, fields, rule, series_end)
            # Follow the event to its (possibly moved) day so it stays visible
            # after Save — the selection and current month track the result.
            self.sel = target_day
            self.cur_y, self.cur_m = target_day.year, target_day.month
            # If the target calendar was toggled off, turn it back on so the
            # event isn't invisible the instant it's saved.
            self.cals_on[cal] = True
            swatch = self.cals_on.get(cal + "_area")
            if swatch is not None:
                swatch.queue_draw()
            self._save_events()
            self._refresh()
            break
        dlg.destroy()

    # ------------------------------------------------------- repeating events
    def _series_members(self, ev):
        """Every event in the same series as `ev`, in date order. Just [ev] when
        it does not repeat."""
        sid = ev.get("series")
        if not sid:
            return [ev]
        return sorted((e for e in self.events if e.get("series") == sid),
                      key=lambda e: e["date"])

    def _extend_series(self, today=None):
        """Grow every repeating series so it still runs REPEAT_AHEAD days past
        today. Returns True when anything was written.

        A repeat is stored as real dated records and the run has to be capped
        (see REPEAT_LIMIT), which on its own meant every repeating event
        silently stopped: a weekly event created in January 2026 held its last
        occurrence in January 2027 and after that the bin day, the class and
        the standing order were simply gone, with the picker still saying
        "Every week" and no end date anywhere. Extending on open makes the run
        effectively endless for anyone who opens the Calendar within the
        horizon, while every write stays bounded — at most REPEAT_LIMIT new
        records per series per open, and none at all once the run is long
        enough."""
        today = today or self.today
        groups = {}
        for e in self.events:
            sid = e.get("series")
            rule = e.get("repeat", "none")
            if sid and rule in REPEAT_AHEAD:
                groups.setdefault((sid, rule), []).append(e)
        added = False
        for (sid, rule), members in groups.items():
            active = [m for m in members if not m.get("cancelled")
                      and not m.get("detached")]
            if not active:
                continue
            seed = max(active, key=lambda m: m["date"])
            last = seed["date"]
            want = today + timedelta(days=REPEAT_AHEAD[rule])
            end_date = self._iso_to_date(seed.get("series_end"))
            if end_date is not None and end_date < want:
                want = end_date
            if last >= want:
                continue
            fields = {"start": seed.get("start", 9.0),
                      "end": seed.get("end", 10.0),
                      "title": seed.get("title", ""),
                      "cal": seed.get("cal", DEFAULT_CAL["name"]),
                      "location": seed.get("location", ""),
                      "notes": seed.get("notes", ""),
                      "all_day": seed.get("all_day", False),
                      "series_end": seed.get("series_end", "")}
            # A month / year rule is counted from the day the series STARTED,
            # exactly as _repeat_dates wrote the original run. Stepping one turn
            # off the LAST occurrence instead compounds the short-month clamp:
            # once "the 31st" has been written out as a 28 February, every later
            # turn is taken from that 28th, so a standing order on the 31st
            # quietly becomes one on the 28th — for good, and a 29 February
            # birthday never returns to the 29th. Day / week / fortnight carry
            # no clamp, so they still step from the last occurrence.
            clamped = rule in ("month", "year")
            anchor = min(members, key=lambda m: m["date"])["date"]
            turn = _whole_periods(anchor, last, rule) if clamped else 0
            d = last
            for _ in range(REPEAT_LIMIT.get(rule, 0)):
                try:
                    if clamped:
                        turn += 1
                        nxt = _next_repeat(anchor, rule, turn)
                    else:
                        nxt = _next_repeat(d, rule)
                except (OverflowError, ValueError):
                    break
                if nxt is None or nxt > want:
                    break
                # A detached edit or cancellation is a persisted exception at
                # its original pattern date.  Never regenerate a second copy.
                occupied = any(m["date"] == nxt or
                               self._iso_to_date(m.get("pattern_date")) == nxt
                               for m in members)
                if nxt <= last or occupied:
                    d = nxt
                    continue      # an occurrence that was moved off the pattern
                self._new_event(nxt, fields, rule, sid)
                d = nxt
                added = True
        return added

    def _new_event(self, day, fields, rule="none", series=""):
        """Build one stored event and register it with the merge-on-write
        bookkeeping, so a concurrent writer can never resurrect or clobber it."""
        ev = {"id": _gen_id(), "date": day, "repeat": rule, "series": series}
        ev.update(fields)
        ev["_loadkey"] = self._content_key(ev["title"], ev["date"],
                                            ev.get("start", 0.0))
        self.events.append(ev)
        self._mark_seen(ev)
        return ev

    # ---------------------------------------------------------------- shifts
    def _ensure_work_calendar(self):
        """The Work calendar, created the first time a shift is added.

        Not seeded on a fresh install: someone who never works a rota should
        not have an empty Work calendar sitting in their sidebar forever."""
        for c in self.calendars:
            if c["name"] == WORK_CAL:
                return
        self.calendars.append({"name": WORK_CAL, "color": WORK_COLOR})
        self.cals_on.setdefault(WORK_CAL, True)
        self._save_calendars()

    def _shift_dialog(self):
        """Enter a work shift.

        Deliberately NOT the event dialog. That one offers starts from 08:00 to
        20:30 in half hours and a maximum duration of three hours — it cannot
        express a 06:00 start, a ten-hour day, or a night shift at all. Here the
        times are typed, any hour is allowed, and a shift is assumed to repeat
        weekly because most rotas do.
        """
        # Built the way every other dialog in this app is built (see
        # _event_dialog): an undecorated .nbdialog with its buttons added as
        # real action widgets, so Enter and Escape behave.
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        ok = dlg.add_button(_t("Add Shift"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_spacing(12)
        box.set_margin_top(24)
        box.set_margin_bottom(16)
        box.set_margin_start(28)
        box.set_margin_end(28)
        heading = Gtk.Label(label=_t("Add a shift"), xalign=0)
        heading.get_style_context().add_class("dlgtitle")
        box.pack_start(heading, False, False, 0)

        dayval = [self.sel]
        sub = Gtk.Label(xalign=0)
        sub.get_style_context().add_class("dlgsub")

        def _fmt(*_):
            d = dayval[0]
            sub.set_text(_t("%s, %d %s %d") % (
                _t(WEEKDAYS_FULL[d.weekday()]), d.day,
                _t(MONTHS[d.month - 1]), d.year))
        _fmt()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(sub, True, True, 0)

        def _step(delta):
            try:
                dayval[0] = dayval[0] + timedelta(days=delta)
            except (OverflowError, OSError, ValueError):
                return
            _fmt()

        for icon, tip, delta in (("back", _t("Previous day"), -1),
                                 ("fwd", _t("Next day"), 1)):
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("daystep")
            b.add(nbicons.image(icon, 14, INK))
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _w, d=delta: _step(d))
            row.pack_start(b, False, False, 0)
        box.pack_start(self._field(_t("Day"), row), False, False, 0)

        what = Gtk.Entry()
        what.set_placeholder_text(_t("Example: Late shift"))
        what.set_activates_default(True)
        box.pack_start(self._field(_t("Name"), what),
                       False, False, 0)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        s_ent = Gtk.Entry(text="09:00")
        s_ent.set_width_chars(7)
        s_ent.set_max_length(5)
        e_ent = Gtk.Entry(text="17:00")
        e_ent.set_width_chars(7)
        e_ent.set_max_length(5)
        times.pack_start(self._field(_t("Starts"), s_ent), False, False, 0)
        times.pack_start(self._field(_t("Ends"), e_ent), False, False, 0)
        box.pack_start(times, False, False, 0)

        reppick = Gtk.ComboBoxText()
        rep_keys = ["none", "week", "fortnight"]
        for key in rep_keys:
            reppick.append_text(_t(REPEAT_LABELS[key]))
        reppick.set_active(1)          # most rotas are the same every week
        box.pack_start(self._field(_t("Repeats"), reppick), False, False, 0)

        note = Gtk.Label(xalign=0)
        note.get_style_context().add_class("dlgsub")
        box.pack_start(note, False, False, 0)

        err = Gtk.Label(xalign=0)
        err.get_style_context().add_class("dlgerror")
        err.set_no_show_all(True)
        box.pack_start(err, False, False, 0)

        def _preview(*_):
            sh, eh = _hhmm_to_hours(s_ent.get_text()), _hhmm_to_hours(
                e_ent.get_text())
            if sh is None or eh is None or sh == eh:
                note.set_text("")
                return
            hours = (eh - sh) if eh > sh else (24 - sh + eh)
            over = ("  ·  " + _t("finishes the next morning")) if eh <= sh else ""
            # The tail is concatenated OUTSIDE the key. With "%s long%s" the
            # second %s is the optional tail, but it looks exactly like an
            # English plural suffix to the spec checker, so the key could
            # never carry both specs and _t() fell back to English here.
            note.set_text((_t("%s long") % _fmt_hours(hours)) + over)
        s_ent.connect("changed", _preview)
        e_ent.connect("changed", _preview)
        _preview()

        dlg.show_all()
        err.hide()
        what.grab_focus()
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return
            sh = _hhmm_to_hours(s_ent.get_text())
            eh = _hhmm_to_hours(e_ent.get_text())
            if sh is None or eh is None:
                err.set_text(_t("Times look like 09:00."))
                err.show()
                continue
            if sh == eh:
                err.set_text(_t("A shift needs to start and finish at "
                                "different times."))
                err.show()
                continue
            ridx = reppick.get_active()
            rule = rep_keys[ridx] if 0 <= ridx < len(rep_keys) else "none"
            name = what.get_text().strip() or _t("Work")
            dlg.destroy()
            self._create_shift(dayval[0], name, sh, eh, rule)
            return

    def _create_shift(self, day, name, start, end, rule):
        """Write a shift — and, past midnight, the two halves of one.

        A calendar event belongs to a single day, so a 22:00-06:00 shift is
        stored as an evening block on the day it starts and a morning block on
        the day after, sharing a series id. That is what makes it DRAWABLE: one
        record with end < start would render as a negative-height block, and one
        clamped to 23:59 would quietly lose the morning."""
        self._ensure_work_calendar()
        sid = _gen_id()
        overnight = end <= start
        for d in _repeat_dates(day, rule):
            if overnight:
                self._new_event(d, {"start": start, "end": 24.0,
                                    "title": name, "cal": WORK_CAL},
                                rule, sid)
                self._new_event(d + timedelta(days=1),
                                {"start": 0.0, "end": end,
                                 "title": name, "cal": WORK_CAL},
                                rule, sid)
            else:
                self._new_event(d, {"start": start, "end": end,
                                    "title": name, "cal": WORK_CAL},
                                rule, sid)
        self.sel = day
        self.cur_y, self.cur_m = day.year, day.month
        self.cals_on[WORK_CAL] = True
        self._save_events()
        self._refresh()

    def _create_event(self, day, fields, rule, end_date=None):
        """Add an event — or, with a repeat rule, the whole run of them."""
        if rule == "none":
            self._new_event(day, fields)
            return
        sid = _gen_id()
        enriched = dict(fields)
        enriched["series_end"] = (self._date_to_iso(end_date)
                                  if end_date is not None else "")
        for d in _repeat_dates(day, rule, end_date):
            self._new_event(d, enriched, rule, sid)

    def _edit_series_scope(self, existing, day, fields, scope="one"):
        """Apply fields to one, following, or every occurrence.

        A one-occurrence edit remains in the series but is marked detached;
        its original pattern date is occupied, so regeneration cannot clone it.
        """
        members = self._series_members(existing)
        if scope == "one":
            original = existing["date"]
            existing.update(fields)
            existing["date"] = day
            existing["detached"] = True
            existing["pattern_date"] = self._date_to_iso(original)
            return
        targets = members if scope == "all" else [
            e for e in members if e["date"] >= existing["date"]]
        delta = day - existing["date"]
        for e in targets:
            e.update(fields)
            if scope == "following":
                e["date"] += delta

    def _delete_series_scope(self, existing, scope="one"):
        """Delete one/following/all without allowing extension to resurrect it."""
        members = self._series_members(existing)
        if scope == "one":
            # Keep a tombstone at the pattern date. It is omitted from views
            # but persisted and blocks _extend_series regeneration.
            existing["cancelled"] = True
            existing["detached"] = True
            existing["pattern_date"] = self._date_to_iso(existing["date"])
            return
        cutoff = existing["date"]
        doomed = members if scope == "all" else [e for e in members
                                                  if e["date"] >= cutoff]
        if scope == "following":
            previous = [e for e in members if e["date"] < cutoff]
            end = cutoff - timedelta(days=1)
            for e in previous:
                e["series_end"] = self._date_to_iso(end)
        for e in doomed:
            if e in self.events:
                self.events.remove(e)

    def _save_edit(self, existing, day, fields, rule, was):
        """Apply the dialog's fields to an event.

        A one-off is simply updated. A member of a series carries its siblings
        with it: renaming "Bin day" or moving the class to 7pm should not leave
        fifty-two stale copies behind, which is exactly what editing one record
        in place would do. The DAY only ever moves the occurrence that was
        opened, so a single week can still be shifted. Turning the repeat off
        drops the other occurrences; changing the rule rebuilds the run from
        this occurrence forward."""
        members = self._series_members(existing)
        if rule != was or (rule == "none" and not existing.get("series")):
            for e in members:
                if e is not existing:
                    try:
                        self.events.remove(e)
                    except ValueError:
                        pass
            existing.update(fields)
            existing["date"] = day
            existing["repeat"] = "none"
            existing["series"] = ""
            if rule != "none":
                sid = _gen_id()
                existing["repeat"] = rule
                existing["series"] = sid
                for d in _repeat_dates(day, rule)[1:]:
                    self._new_event(d, fields, rule, sid)
            return
        for e in members:
            e.update(fields)
        existing["date"] = day
        # Opening a series for editing is also when its run gets topped up —
        # the behaviour the comment beside REPEAT_LIMIT has always claimed and
        # which, until this call existed, nothing anywhere actually did.
        self._extend_series()

    def _delete_event(self, existing):
        """Remove an event with undo. One of a series asks whether to drop
        just that occurrence or the whole run — cancelling a single week's class
        must not clear the term. Returns True when something was deleted."""
        members = self._series_members(existing)
        if len(members) > 1:
            choice = self._choose_series_scope(_t("Delete Repeating Event"))
            if choice is None:
                return False
        else:
            choice = "one"
        self.undo.checkpoint("Delete Event")
        if len(members) > 1:
            self._delete_series_scope(existing, choice)
        else:
            try:
                self.events.remove(existing)
            except ValueError:
                pass
        self._save_events()
        self._refresh()
        self.undo.commit()
        return True

    def _choose_series_scope(self, title):
        """Ask how far a repeating edit/delete reaches."""
        responses = {31: "one", 32: "following", 33: "all"}
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_t("This Occurrence Only"), 31)
        dlg.add_button(_t("This and Following"), 32)
        dlg.add_button(_t("Whole Series"), 33)
        area = dlg.get_content_area()
        area.set_margin_top(20); area.set_margin_bottom(12)
        area.set_margin_start(24); area.set_margin_end(24)
        label = Gtk.Label(label=title, xalign=0)
        label.get_style_context().add_class("dlgtitle")
        area.pack_start(label, False, False, 0)
        dlg.show_all()
        response = dlg.run()
        dlg.destroy()
        return responses.get(response)

    def _confirm_series(self, title, n):
        """Ask which part of a repeating event to delete. Returns 'one', 'all'
        or None (cancelled)."""
        RESP_ONE, RESP_ALL = 21, 22
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("nbdialog")
        cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        cancel.get_style_context().add_class("dlgcancel")
        one = dlg.add_button("This Event", RESP_ONE)
        one.get_style_context().add_class("destructive")
        every = dlg.add_button("All Events", RESP_ALL)
        every.get_style_context().add_class("destructive")
        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_margin_top(24); area.set_margin_bottom(16)
        area.set_margin_start(28); area.set_margin_end(28)
        t = Gtk.Label(label=_t("Delete Repeating Event"), xalign=0)
        t.get_style_context().add_class("dlgtitle")
        area.pack_start(t, False, False, 0)
        b = Gtk.Label(
            label=_t("“%s” repeats %d times in the calendar.")
            % (title, n), xalign=0)
        b.set_line_wrap(True); b.set_max_width_chars(40)
        b.get_style_context().add_class("dlgbody")
        area.pack_start(b, False, False, 0)
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp == RESP_ONE:
            return "one"
        return "all" if resp == RESP_ALL else None

    def _start_index(self, existing):
        """Combo index for a start time. New events honour a slot-click seed
        (one-shot) or default to 09:00; edits reflect the event's own start."""
        if existing is not None:
            sh = int(existing["start"])
            sm = 30 if (existing["start"] - sh) >= 0.5 else 0
            idx = (sh - HOURS[0]) * 2 + (1 if sm else 0)
            if 0 <= idx < len(HOURS) * 2:
                return idx
            return 2
        pref = self._new_event_hour
        self._new_event_hour = None   # one-shot seed; reset for later opens
        if pref in HOURS:
            return (pref - HOURS[0]) * 2
        return 2  # 09:00

    def _duration_index(self, existing):
        """Combo index for a duration; edits map end-start back to the nearest
        offered length, defaulting to 1 hour."""
        if existing is None:
            return 1
        span = round(existing["end"] - existing["start"], 1)
        return {0.5: 0, 1.0: 1, 2.0: 2, 3.0: 3}.get(span, 1)

    def _field(self, label, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl = Gtk.Label(label=label.upper(), xalign=0)
        lbl.get_style_context().add_class("dlgfield")
        box.pack_start(lbl, False, False, 0)
        box.pack_start(widget, False, False, 0)
        return box

    # --------------------------------------------------------- file documents
    def _serialize_document(self):
        """The full calendar as a portable document: named calendars plus every
        event (dates as ISO strings)."""
        return {
            "calendars": [{"name": c["name"], "color": c["color"]}
                          for c in self.calendars],
            "events": [self._event_record(e) for e in self.events],
        }

    def _apply_document(self, data):
        """Load a parsed document into the model. Accepts either the document
        shape {calendars, events} or a bare event list (calendar.json's own
        shape). Ensures at least one calendar always exists. Returns False —
        touching no state — on an unusable structure, including a foreign JSON
        dict from the shared folder (e.g. a ledger) that lacks a 'calendars' or
        'events' key, so a mismatched Open cannot wipe the calendar."""
        if isinstance(data, dict):
            # Validate a recognizable calendar document before any mutation: a
            # dict must carry 'calendars' or 'events', and 'events' (when
            # present) must be a list. Otherwise reject so the caller flashes
            # 'Unrecognized file' and leaves the model/path/autosave untouched.
            if "calendars" not in data and "events" not in data:
                return False
            raw_evs = data.get("events")
            if raw_evs is not None and not isinstance(raw_evs, list):
                return False
            raw_cals = data.get("calendars")
        elif isinstance(data, list):
            raw_cals, raw_evs = None, data
        else:
            return False
        if raw_cals is not None:
            cals = self._norm_calendars(raw_cals)
            if cals:
                self.calendars = cals
        if not self.calendars:
            self.calendars = [dict(DEFAULT_CAL)]
        evs = []
        if isinstance(raw_evs, list):
            for it in raw_evs:
                ev = self._norm_event(it)
                if ev is not None:
                    evs.append(ev)
        self.events = evs
        # This document is now the whole store; reset the seen-set to just its
        # events so a later merge-on-write starts from a clean baseline (the
        # store is overwritten authoritatively by the caller's merge=False save).
        self._seen = set()
        for e in self.events:
            self._mark_seen(e)
        self.cals_on = {c["name"]: True for c in self.calendars}
        return True

    def _file_new(self):
        """File ▸ New — start a blank calendar file: no events and the single
        default calendar. Confirms first when there is data to discard."""
        has_data = bool(self.events) or self._cal_names() != [DEFAULT_CAL["name"]]
        if has_data and not self._confirm(
                "New Calendar File",
                "Discard all events and calendars and start a blank file?",
                "Discard"):
            return
        self.events = []
        self._seen = set()
        self.calendars = [dict(DEFAULT_CAL)]
        self.cals_on = {DEFAULT_CAL["name"]: True}
        self._doc_path = None
        # Authoritative write: New intentionally empties the store, so do NOT
        # merge back the events it is discarding.
        self._save_events(merge=False); self._save_calendars()
        self._populate_cal_list()
        self._refresh()
        self._flash_status("New calendar file")

    def _file_open(self):
        """File ▸ Open — pick a JSON document under $NB_HOME/Documents and load
        it, replacing the current calendars and events."""
        self._file_dialog("Open Calendar", "Open", "", self._load_document)

    def _file_save(self):
        """File ▸ Save — write to the current document path, prompting via Save
        As when there is no path yet."""
        if self._doc_path:
            self._write_document(self._doc_path)
        else:
            self._file_save_as()

    def _file_save_as(self):
        """File ▸ Save As — pick a target filename under $NB_HOME/Documents."""
        default = (os.path.basename(self._doc_path)
                   if self._doc_path else "calendar.json")
        self._file_dialog("Save Calendar As", "Save", default,
                          self._write_document)

    def _write_document(self, path):
        """Write the whole calendar to `path` and adopt it as the current
        document. Never raises; reports the outcome in the status line."""
        try:
            nbapp.atomic_write_json(path, self._serialize_document(), indent=2)
            self._doc_path = path
            self._flash_status("Saved " + os.path.basename(path))
            return True
        except Exception:
            self._flash_status("Write failed")
            return False

    def _load_document(self, path):
        """Read a JSON document, replace the model, adopt it as the current
        document and mirror it into session recovery (calendar.json /
        calendars.json). Never raises."""
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception:
            self._flash_status("Open failed")
            return
        # Confirm before discarding: Open replaces the whole store and then
        # writes it back authoritatively (merge=False below), so opening a file
        # would silently wipe the user's accumulated events and calendars — the
        # same store the Tasks app and desktop widget read from. Guard it exactly
        # as File ▸ New does, and as the other document apps guard their Open, so
        # a stray file pick can never destroy built-up data without consent.
        has_data = bool(self.events) or self._cal_names() != [DEFAULT_CAL["name"]]
        if has_data and not self._confirm(
                "Open Calendar File",
                "Discard the current events and calendars and open this file?",
                "Discard"):
            return
        if not self._apply_document(data):
            self._flash_status("Unrecognized file")
            return
        self._doc_path = path
        # Authoritative write: Open replaces the whole store with this document,
        # so do NOT merge back the events it is replacing.
        self._save_events(merge=False); self._save_calendars()
        self._populate_cal_list()
        self._refresh()
        self._flash_status("Opened " + os.path.basename(path))

    def _file_dialog(self, title, action_label, default_name, on_pick):
        """Finder-style file picker over $NB_HOME/Documents, unified with the
        Finder engine via nbpicker. A save flow (non-empty default_name) shows
        the Save picker; otherwise the Open picker. The resolved path is passed
        to on_pick, matching the previous callback contract."""
        try:
            os.makedirs(DOCUMENTS, exist_ok=True)
        except Exception:
            pass
        if default_name:
            path = nbpicker.save_file(self, title=title, start_dir=DOCUMENTS,
                                      suggested_name=default_name,
                                      patterns=("*.json",), default_ext=".json")
        else:
            path = nbpicker.open_file(self, title=title, start_dir=DOCUMENTS,
                                      patterns=("*.json",))
        if path:
            on_pick(path)
    def _flash_status(self, text):
        """Show a transient message in the toolbar status line, cleared after a
        few seconds (unless superseded)."""
        try:
            self.status_lbl.set_text(text)
        except Exception:
            return
        self._status_tok += 1
        tok = self._status_tok

        def _clear():
            if self._status_tok == tok:
                try:
                    self.status_lbl.set_text("")
                except Exception:
                    pass
            return False
        GLib.timeout_add_seconds(4, _clear)

    # --------------------------------------------------------------- refresh
    def _refresh(self):
        # title
        if self.view == "month":
            self.title_lbl.set_text(_t("%s %d") % (
                _t(MONTHS[self.cur_m - 1]), self.cur_y))
        elif self.view == "week":
            days = self._week_dates()
            a, b = days[0], days[6]
            if a.month == b.month:
                self.title_lbl.set_text(_t("%s %d–%d, %d") % (
                    _t(MONTHS[a.month - 1]), a.day, b.day, b.year))
            else:
                self.title_lbl.set_text(_t("%s %d – %s %d") % (
                    _t(MONTHS[a.month - 1]), a.day,
                    _t(MONTHS[b.month - 1]), b.day))
        else:
            # No weekday name here: the day column header right below already
            # reads "FRIDAY 24", and spelling out "Wednesday, 30 September
            # 2026" overran the header bar on a 1024-wide screen and came out
            # as "Wednesday, 30 Septem...". This also matches the shape of the
            # month and week titles.
            self.title_lbl.set_text(_t("%d %s %d") % (
                self.sel.day, _t(MONTHS[self.sel.month - 1]), self.sel.year))

        # segmented state
        for key, btn in self.seg_btns.items():
            c = btn.get_style_context()
            if key == self.view:
                c.add_class("active")
            else:
                c.remove_class("active")

        self._build_mini()
        self._rebuild_body()

    def _rebuild_body(self):
        self.month_grid = None
        for c in self.body_area.get_children():
            self.body_area.remove(c)
        if self.view == "month":
            self._build_month()
        elif self.view == "week":
            self._build_week()
        else:
            self._build_day()
        self.body_area.show_all()

    def _check_date_rollover(self):
        # Midnight guard: self.today only changes here or via the Today button,
        # so across a date boundary refresh it and re-render the affected views
        # (month grid 'today' highlight, mini-month, week header). Only re-render
        # on an actual change to keep the 60s poll cheap; never crash the app.
        # A closed window drops the poll entirely (return False): self.today is
        # not updated and nothing is re-rendered, because its widgets are gone.
        if getattr(self, "_closed", False):
            return False
        try:
            now = date.today()
            if now != self.today:
                self.today = now
                self._refresh()
        except Exception:
            pass
        return True   # keep polling every minute

    # ------------------------------------------------------------------- CSS
    def _install_css(self):
        chips = "".join(
            ".evchip.chipbar-%d { border-left: 3px solid %s; }\n" % (i, c)
            for i, c in enumerate(PALETTE))
        chips += ".evchip.chipbar-x { border-left: 3px solid #9A9484; }\n"
        css = ("""
        .calsidebar, .calsidebar *,
        .calmain, .calmain *,
        .nbdialog, .nbdialog * {
            font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .calsidebar { background: #F1EEE6; border-right: 1px solid #C9C4B6; }
        .minititle { font-size: 15px; font-weight: 700; color: #1A1916; }
        .minidow { font-size: 10px; color: #9A9484; font-weight: 600;
                   letter-spacing: 0.04em; }
        .miniday { font-size: 12px; color: #6E695E; min-width: 26px;
                   min-height: 26px; }
        /* A day with something on it: ink and bold rather than a coloured
           dot, so the busy days read at a glance without adding a second
           accent colour to the sidebar (matches the Tasks mini-calendar). */
        .minibusy { font-size: 12px; color: #1A1916; font-weight: 700;
                    min-width: 26px; min-height: 26px; }
        .minitoday { font-size: 12px; color: #FCFBF8; font-weight: 700;
                     background: #C8341E; border-radius: 50%;
                     min-width: 26px; min-height: 26px; padding: 0; }
        .minisel { font-size: 12px; color: #1A1916; font-weight: 600;
                   background: #DED4C2; border-radius: 50%;
                   min-width: 26px; min-height: 26px; padding: 0; }
        .calsectionhead { font-size: 11px; letter-spacing: 0.14em;
                          color: #9A9484; font-weight: 700; }
        .calrow { padding: 6px 4px; border-radius: 6px; background: transparent; }
        .calrow:hover { background: #EAE3D2; }
        .callabel { font-size: 14px; color: #1A1916; }
        .caltoggle { padding: 0; border: none; background: transparent;
                     background-image: none; box-shadow: none; }
        .caltoggle:hover { background: #EAE3D2; border-radius: 6px; }
        /* Palette swatch in the New Calendar dialog: same neutral treatment as
           .caltoggle, plus a 0 minimum so the theme's shared button height
           cannot inflate the 26px artwork. No `outline: none` -- these are now
           keyboard-reachable and the focus ring is what says so. */
        .calswatch { padding: 0; border: none; background: transparent;
                     background-image: none; box-shadow: none;
                     min-width: 0; min-height: 0; }
        .caldel { padding: 2px 6px; min-width: 22px; min-height: 22px;
                  border: none; background: transparent; box-shadow: none; }
        .caldel:hover { background: #EAE3D2; border-radius: 6px; }
        .caladd { padding: 8px 4px; border: none; background: transparent;
                  box-shadow: none; }
        .caladd:hover { background: #EAE3D2; border-radius: 6px; }
        .caladdlabel { font-size: 13px; color: #6E695E; font-weight: 600; }
        .calfoot { border-top: 1px solid #D7D2C5; }
        /* Quick add: the same dashed one-line field the Tasks app puts its
           "Add task" in, so the two daily-life apps take an entry the same
           way. */
        .quickadd { min-height: 40px; border: 1px dashed #C9C4B6;
                    border-radius: 8px; margin-top: 12px; padding-left: 9px; }
        .quickentry { background: transparent; border: none; box-shadow: none;
                      font-size: 14px; color: #1A1916; }
        .quickentry:focus { border: none; box-shadow: none; }
        .quickhint { font-size: 11px; color: #9A9484; padding: 5px 2px 0 2px; }
        /* Primary "New Event" — the same paper-outline treatment every other
           app's create button uses (Novel's New Chapter, Academic's New
           Lecture, Cookbook's New Recipe). It used to be a solid black slab,
           the one thing the papertone language never does, and the only create
           button in the OS that looked different. */
        .newevent { min-height: 42px; border: 1px solid #C9C4B6;
                    border-radius: 8px; background: #FCFBF8; box-shadow: none; }
        .newevent:hover { background: #F1EEE6; }
        .newevlabel { font-size: 14px; font-weight: 600; color: #2A2620; }

        .calmain { background: #FCFBF8; }
        .caltitle { font-size: 30px; font-weight: 700; color: #1A1916; }
        .statusmsg { font-size: 12px; color: #6E695E; }
        .navbtn { min-width: 34px; min-height: 34px; padding: 0;
                  border: 1px solid #C9C4B6; border-radius: 8px;
                  background: #FCFBF8; box-shadow: none; }
        .navbtn:hover { background: #F1EEE6; }
        .todaybtn { min-height: 34px; padding: 0 16px; border: 1px solid #C9C4B6;
                    border-radius: 8px; background: #FCFBF8; box-shadow: none;
                    font-size: 14px; font-weight: 600; color: #1A1916; }
        .todaybtn:hover { background: #F1EEE6; }
        .segwrap { border: 1px solid #C9C4B6; border-radius: 8px; }
        .segbtn { min-height: 32px; padding: 0 16px; font-size: 14px;
                  color: #6E695E; background: #FCFBF8; box-shadow: none;
                  border: none; border-right: 1px solid #C9C4B6;
                  border-radius: 0; }
        .segbtn.seglast { border-right: none; }
        .segbtn:hover { background: #F1EEE6; }
        .segbtn.active { background: #DED4C2; font-weight: 600; }
        /* The theme sets `* { color: ink }`, which lands directly on a
           button's label node and beats the colour inherited from the button
           — so every text colour a button sets has to name the label too, or
           the label silently stays ink (a black-on-black primary button). */
        .segbtn label { color: #6E695E; }
        .segbtn.active label { color: #1A1916; font-weight: 600; }

        .emptyhint { font-size: 13px; color: #9A9484;
                     padding: 10px 40px 4px 40px; }
        .dowhead { background: #F4F2EC; border-top: 1px solid #D7D2C5;
                   border-bottom: 1px solid #D7D2C5; }
        .dowcell { padding: 9px 12px; font-size: 11px; letter-spacing: 0.1em;
                   color: #9A9484; font-weight: 600; }
        /* 6px, not 7px, top/bottom: a six-row month whose busiest day shows
           three chips was 2px taller than a 768px screen can display. */
        .monthcell { border-right: 1px solid #D7D2C5;
                     border-bottom: 1px solid #D7D2C5; background: #FCFBF8;
                     padding: 6px 9px; }
        .monthcell.weekend { background: #F4F2EC; }
        .monthcell.blankcell { background: #F1EEE6; }
        .monthcell.selcell { background: #EAE3D2; }
        .daynum { font-size: 14px; color: #1A1916; min-width: 28px;
                  min-height: 28px; }
        .daynum.today { background: #C8341E; color: #FCFBF8; font-weight: 700;
                        border-radius: 50%; }
        .daynum.selnum { background: #DED4C2; color: #1A1916;
                         border-radius: 50%; }
        .evchip { font-size: 12px; color: #1A1916; padding: 2px 8px;
                  border-radius: 4px; background: #F1EEE6; }
        .eventhit { padding: 0; border: none; background: transparent;
                    background-image: none; box-shadow: none; }
        /* Later rows of a multi-hour event: the same block, no repeated title. */
        .evchip.evcont { min-height: 15px; }

        .subhead { font-size: 15px; font-weight: 600; color: #1A1916; }
        .hourlabel { font-size: 11px; color: #9A9484; }
        .hourslot { border-bottom: 1px solid #D7D2C5;
                    border-left: 1px solid #D7D2C5; }
        .weekcell { border-bottom: 1px solid #D7D2C5;
                    border-left: 1px solid #D7D2C5; }
        .weekdaycell { padding: 9px 12px; font-size: 12px; letter-spacing: 0.06em;
                       font-weight: 600; color: #9A9484;
                       border-left: 1px solid #D7D2C5;
                       border-bottom: 1px solid #D7D2C5; }
        .weekdaycell.istoday { color: #C8341E; }

        .nbdialog { background: #FCFBF8; border: 1px solid #C9C4B6; }
        .dlgtitle { font-size: 17px; font-weight: 700; color: #1A1916; }
        .dlgsub { font-size: 13px; color: #6E695E; }
        .daystep { min-width: 26px; min-height: 26px; padding: 0;
                   border: 1px solid #C9C4B6; border-radius: 8px;
                   background: #FCFBF8; box-shadow: none; }
        .daystep:hover { background: #F1EEE6; }
        .dlgbody { font-size: 13px; color: #1A1916; }
        .dlgfield { font-size: 11px; letter-spacing: 0.1em; color: #9A9484;
                    font-weight: 600; }
        .nbdialog entry { background: #FCFBF8; border: 1px solid #C9C4B6;
                          border-radius: 8px; padding: 6px 9px; color: #1A1916;
                          box-shadow: none; }
        .nbdialog entry:focus { border-color: #C8341E; }
        .filelist { background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 12px; }
        .filerow { font-size: 13px; color: #1A1916; padding: 7px 10px; }
        .emptylist { font-size: 13px; color: #9A9484; padding: 14px; }
        .suggested-action { background: #1A1916; color: #FCFBF8; border: none;
                            box-shadow: none; font-weight: 600; }
        .suggested-action:hover { background: #2A2620; }
        .destructive { background: #FCFBF8; color: #C8341E; font-weight: 600;
                       border: 1px solid #C9C4B6; box-shadow: none; }
        .destructive:hover { background: #F1EEE6; }
        /* Cancel is a plain button, so when it is the dialog's focus-default
           (the confirm dialogs set no other default) the base GTK stylesheet
           ringed it in its own blue — a colour this design does not contain.
           Name it and it keeps the taupe border every other button has. The
           keyboard focus ring is NOT suppressed here: this button is the one
           the confirm hands focus to, so hiding its ring would leave a keyboard
           user unable to see what Enter is about to do (Article VII §1). */
        .dlgcancel { background: #FCFBF8; color: #1A1916;
                     border: 1px solid #C9C4B6; box-shadow: none; }
        .dlgcancel:hover { background: #F1EEE6; }
        .dlgcancel label { color: #1A1916; }
        /* Name the label node too — see the .segbtn note above. Without this
           the Add Event / Save button is ink text on an ink slab. */
        .suggested-action label { color: #FCFBF8; font-weight: 600; }
        .destructive label { color: #C8341E; font-weight: 600; }
        .todaybtn label { color: #1A1916; font-weight: 600; }
        .nbdialog entry.field-error { border-color: #C8341E; }
        .dlgerror { font-size: 12px; color: #C8341E; }
        .evmore { font-size: 11px; color: #6E695E; padding: 1px 8px; }
        .evmore:hover { color: #1A1916; }
        """ + chips).encode("utf-8")
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    # Optional "calendar.py YYYY-MM-DD" opens straight onto that day (the
    # desktop Calendar widget's day-click uses this). Parsing/guarding lives in
    # Calendar._parse_initial_date; nbapp.run just needs a zero-arg factory.
    _arg = next((a for a in sys.argv[1:] if a and not a.startswith("-")), None)
    nbapp.run(lambda: Calendar(_arg))
