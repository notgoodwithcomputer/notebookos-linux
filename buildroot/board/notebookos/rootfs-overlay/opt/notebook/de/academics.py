#!/usr/bin/env python3
"""
Academics — Notebook OS class organiser (native GTK).

Three views over one term's work, switched from the sidebar:

  * Notes    — the lecture-note editor: a sidebar of classes and their numbered
               lectures, and a canvas with a format bar (Style, B / I /
               highlight, bullet / number lists), live word count and save
               state. A new lecture prefills itself from the timetable: the
               class that meets now (or next), and the number after the last
               one you took.
  * Schedule — the week as a timetable, each class drawn in its own colour at
               the hours it actually meets, with today's column marked.
  * Homework — every assignment against the day it is due, grouped by how soon
               that is. Accent red means one thing here: overdue.

The desktop board reads the same file for its Next Class and Homework tiles.

Ships empty: no classes, no lectures, no assignments. The app auto-persists the
whole term to a JSON file on every edit; the File menu exports the active
lecture to a PDF under $NB_HOME/Documents.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib  # noqa: E402

import os
import sys
import json
import time
import cairo

import nbapp
import nbicons
import nbprint
import nbtransitions
from nbi18n import _t  # noqa: E402

CLASS_COLORS = ["#9A7B4F", "#4A5E73", "#6E7B57", "#8A6D5B", "#566E86"]


# ---------------------------------------------------------------- drawn text
# EVERY string painted on the timetable goes through Pango, never cairo's
# "toy" font API (cr.select_font_face + cr.show_text).
#
# THE BUG THIS EXISTS FOR: the toy API binds ONE FreeType face and does NO
# per-character fallback, so it draws .notdef boxes for anything that face does
# not carry. Nimbus Sans carries no CJK, no Devanagari and no Hebrew, so the
# weekly Schedule's seven DAY HEADERS were seven empty boxes in Japanese,
# Chinese, Korean, Hindi and Yiddish — measured 7/7, 14/14, 7/7, 25/25 and
# 25/25 glyphs missing — and so was every class name typed in those scripts.
# tofu_sweep.py could not see it: it asks whether SOME shipped face has the
# glyph (Pango's question), which was true the whole time and had nothing to do
# with what show_text actually drew. Pango picks a face per glyph, so the same
# strings come out at 0 missing.

def _layout(cr, text, size, bold=False):
    """A Pango layout for `text` at `size` px in the interface face."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription("Nimbus Sans")
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    layout.set_text(text, -1)
    return layout


def _text_w(cr, text, size, bold=False):
    """The drawn width of `text`, for centring and truncation."""
    return _layout(cr, text, size, bold).get_pixel_size()[0]


def _show_text(cr, x, y, text, size, bold=False):
    """Draw `text` with its BASELINE at y — the same anchor cr.show_text uses,
    so call sites keep the geometry they were tuned with."""
    layout = _layout(cr, text, size, bold)
    cr.move_to(x, y - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)


def _records(v):
    """A store section as a list of records: a list as-is, an object as its
    values in file order, anything else as nothing.

    THE BUG THIS EXISTS FOR: `classes` stored as an object (keyed by name or id
    — the shape every other loader in this OS already tolerates) was read as
    "no classes", and because a lecture whose class cannot be resolved was then
    DISCARDED, opening and closing Academics deleted every lecture note in the
    file. The homework list survived, so the empty-model guard in _save_to_disk
    never fired and nothing warned anybody. A wrapper of the wrong type must
    never cost a term of notes."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return []


def _minutes(hhmm):
    """"HH:MM" -> minutes since midnight, or None if it is not a time."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
    except (AttributeError, TypeError, ValueError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def _hhmm(mins):
    return "%02d:%02d" % (mins // 60, mins % 60)


def _today_key():
    t = time.localtime()
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def _date_key(ordinal):
    """A day number back to "YYYY-MM-DD" — the inverse of nbapp.day_ordinal,
    by the same civil-date arithmetic and for the same reason (the stdlib
    `calendar` this OS shadows is off limits)."""
    z = ordinal + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return "%04d-%02d-%02d" % (y + (1 if m <= 2 else 0), m, d)


def _canonical_date(s):
    """"YYYY-MM-DD" for a day that really exists, or None.

    THE BUG THIS EXISTS FOR: `nbapp.day_ordinal` is deliberately forgiving — it
    takes 2026-02-29 (2026 is not a leap year) and hands back the ordinal for
    2026-03-01, and the same for 2026-04-31 and 2026-01-32. The app validated
    with it, then stored the RAW STRING the user typed and printed that back. So
    a due date of "2026-01-32" showed in the homework list as **32 January**,
    and was grouped and sorted as 1 February. Measured, all of these: "29
    February" in a non-leap year, "30 February", "31 April", "32 January".
    Showing a day that does not exist, and then behaving as a different day, is
    two lies in one row.

    Formatting slop is fine and is normalised rather than refused: "2026-1-5"
    means the fifth of January and comes back as "2026-01-05". Only a day that
    is not on the calendar returns None."""
    o = nbapp.day_ordinal(s)
    if o is None:
        return None
    canon = _date_key(o)
    try:
        y, m, d = (int(x) for x in str(s).split("-"))
        cy, cm, cd = (int(x) for x in canon.split("-"))
    except (AttributeError, TypeError, ValueError):
        return None
    # The ordinal round-trips to a DIFFERENT calendar day than the one named,
    # which is precisely how "31 April" is detected: it comes back as 1 May.
    return canon if (y, m, d) == (cy, cm, cd) else None


def _field(label, widget):
    """A labelled control for the dialogs: the label sits above the thing it
    names, so a narrow dialog never has to choose between a readable label and
    a usable field."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    lbl = Gtk.Label(label=label, xalign=0)
    lbl.get_style_context().add_class("ac-fieldlabel")
    box.pack_start(lbl, False, False, 0)
    box.pack_start(widget, False, False, 0)
    box.set_margin_bottom(12)
    return box


def _hex_rgb(hexcolor):
    """"#RRGGBB" -> (r, g, b) in 0..1, falling back to the first class colour
    so a hand-edited store can never make a block invisible."""
    try:
        h = hexcolor.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (AttributeError, IndexError, TypeError, ValueError):
        h = CLASS_COLORS[0].lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _weekday(ordinal):
    """Monday-first weekday for a day number. 1970-01-01 was a Thursday."""
    return (ordinal + 3) % 7


_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _pretty_due(due):
    """A due date said the way it would be spoken.

    A day that is not on the calendar is said as NOTHING rather than repeated
    back. The dialog and the loader both refuse one now, so this is the third
    lock on the same door — but it is the one at the point of display, and this
    function is also handed lecture dates, which come from anywhere a store has
    been. Better a row with no date than a row claiming 32 January.
    """
    canon = _canonical_date(due)
    if canon is None:
        return ""
    due = canon
    o = nbapp.day_ordinal(due)
    if o is None:
        return ""
    today = nbapp.day_ordinal(_today_key())
    days = o - today
    if days == 0:
        return _t("today")
    if days == 1:
        return _t("tomorrow")
    if days == -1:
        return _t("yesterday")
    if 0 < days <= 6:
        return _t(DAY_NAMES[_weekday(o)])
    # Counted days, but only while counting is still how a person would say it.
    # This branch used to catch EVERY past date however old, so the dated form
    # below was unreachable looking backwards and a lecture from last November
    # was labelled "266 days ago" — a number nobody can turn back into a day.
    # A week is the limit in both directions: past that, name the date.
    if -7 < days < 0:
        return _t("%d days ago") % -days
    # Further out than a week. Every other row on this screen speaks in words
    # ("tomorrow", "Friday", "14 days ago") and this one alone answered with a
    # raw 2026-08-07, which is the file's date format, not a person's. Month
    # names are already translated in all 17 catalogs (the Calendar uses them).
    try:
        y, m, d = (int(x) for x in due.split("-"))
    except (ValueError, IndexError):
        return due
    # m == 0 would make _MONTHS[m - 1] "December" rather than raise: a negative
    # index is the LAST element, not a miss. Same trap as cls == -1 further down
    # this file, which once labelled every untied assignment with whichever class
    # happened to be last.
    if not 1 <= m <= 12:
        return due
    stamp = "%d %s" % (d, _MONTHS[m - 1])
    # A bare "14 September" is a different day depending on the year it is read
    # in, so the year is spelled out whenever it is not the current one. It stays
    # off for this year, where it would be noise on every single row.
    if y != int(_today_key()[:4]):
        stamp = "%s %d" % (stamp, y)
    # THE WHOLE DATE goes through _t(), never the month word on its own. nbi18n
    # does not merely translate a date, it REORDERS one — Chinese, Japanese and
    # Korean write it big-endian and numerically, and Spanish binds the month to
    # the day with "de" — and _date_lookup only fires when the entire string is
    # a date. Translating just the month and concatenating in English order gave,
    # measured: "14 九月" where Chinese wants 9月14日, "14 9月" for Japanese's
    # 9月14日, "14 9월" for Korean's 9월 14일, "14 Septiembre" for "14 de
    # septiembre", and a mid-sentence capital in French and Russian.
    return _t(stamp)

# Persistence: the classes and every lecture (titles + notes + formatting) live
# in one JSON file under the shared Notebook config dir so the session survives
# app close / reboot. Missing/invalid file -> open empty, exactly as a fresh
# install does. This JSON is the sole source of truth, rewritten on every edit;
# there is no file-based document management.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
ACADEMICS_FILE = os.path.join(CFG_DIR, "academics.json")
# The store this app used when it was called Academic Notes. Read once, if the
# new one does not exist yet, so nobody's term of notes disappears behind a
# rename.
LEGACY_FILE = os.path.join(CFG_DIR, "academic.json")

# Monday-first, matching the desktop calendar and the rest of the OS.
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# The timetable never draws outside these hours; a class earlier or later than
# this stretches the grid to reach it.
DEFAULT_DAY_START = 8 * 60
DEFAULT_DAY_END = 18 * 60
# File ▸ Export to PDF writes rendered lectures here.
DOCS_DIR = os.path.join(HOME, "Documents")


class Academics(nbapp.AppWindow):
    app_name = "Academics"
    menus = ("File", "Edit", "Format", "Insert", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        # {label, color, room, instructor, meets:[{day,start,end,room}]}
        self.classes = []
        self.lectures = []     # {cls, num, title, date, meta, notes, ranges}
        self.homework = []     # {title, cls, due, done, note}
        # Top-level keys read out of the store that this version does not know
        # about, carried back on save. Empty on a fresh install.
        self._extra_top = {}
        self.view = "notes"    # notes | schedule | homework
        self.grid_area = None  # built with the schedule view
        self._blocks = []      # drawn timetable blocks, for hit-test + keys
        self._sel_block = -1   # which one the keyboard is standing on
        self._grid_ppm = self._MAX_PX   # density the last paint really used
        self._grid_bottom = 0           # ...and where that paint ended
        self.sched_sub = None
        self.hw_sub = None
        self.active = -1
        # Live handle to the active lecture row's title Gtk.Label, captured on
        # every sidebar rebuild so per-keystroke title edits update it in place
        # instead of triggering a full sidebar rebuild.
        self._active_title_label = None
        self._save_timer = None
        # Debounce for the per-keystroke note sync + live word count, so typing
        # a long note doesn't re-serialize the whole buffer twice per keypress.
        self._notes_timer = None

        # Load any saved notebook BEFORE building the UI from the model, so the
        # sidebar / canvas render the restored classes and lectures. On a fresh
        # install (no file) this leaves the empty default untouched.
        self._load_from_disk()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        self._sidebar = self._build_sidebar()
        body.pack_start(self._sidebar, False, False, 0)
        # One stack, three views. The editor keeps its own state alive while
        # you are looking at the timetable, so switching back does not lose an
        # unsaved keystroke or the caret.
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._pager = nbtransitions.PageSwitcher(
            self.stack, order=["notes", "schedule", "homework"],
            duration=nbtransitions.PAGE)
        self.stack.add_named(self._build_editor(), "notes")
        self.stack.add_named(self._build_schedule(), "schedule")
        self.stack.add_named(self._build_homework(), "homework")
        body.pack_start(self.stack, True, True, 0)

        self._refresh_sidebar()
        self._refresh_canvas()
        self._set_view(getattr(self, "_open_view", "notes"))
        # Undo/redo over the whole notebook, not the open note: deleting a
        # lecture takes its class with it when it was the last one, and that is
        # the operation that actually loses a term of notes. Built here so its
        # baseline is the notebook as restored. See nbapp.UndoHistory.
        self.undo = nbapp.UndoHistory(self._undo_snapshot, self._undo_restore)
        self.undo.reset()

        # Flush the final edit when the window closes so nothing is lost.
        self.connect("destroy", self._on_destroy)

    # ---------------- schedule + homework editing ----------------

    # What a dialog returns when its Remove button was pressed — a third
    # outcome beside "saved these values" and "cancelled", kept distinct from
    # None so a cancel can never be mistaken for a delete.
    _REMOVE = object()

    def _class_combo(self, selected=-1, allow_none=False):
        combo = Gtk.ComboBoxText()
        if allow_none:
            combo.append_text(_t("No class"))
        for c in self.classes:
            combo.append_text(c.get("label", ""))
        offset = 1 if allow_none else 0
        combo.set_active(max(0, (selected + offset) if selected >= 0 else 0))
        return combo

    def _combo_class_index(self, combo, allow_none=False):
        """The chosen class as a MODEL index. Read from the combo's position,
        never from its text: nbi18n translates widget labels in place, so
        get_active_text() returns the translation and matching on it silently
        breaks in every non-English language."""
        i = combo.get_active()
        return (i - 1) if allow_none else i

    def _meeting_dialog(self, title, ci=0, day=0, start="09:00", end="10:00",
                        room="", ok_label=None, removable=False):
        dlg = self._dialog_shell(title)
        # The fields go in dlg._box, NOT in the content area: _dialog_shell put
        # dlg._box in there first, so anything packed into the content area
        # becomes a SIBLING that lands below the heading-and-buttons card. That
        # is exactly what happened — Cancel / Save rendered directly under the
        # title with every field beneath them, outside the card's padding.
        box = dlg._box
        cls = self._class_combo(ci)
        box.pack_start(_field(_t("Class"), cls), False, False, 0)

        daybox = Gtk.ComboBoxText()
        for d in range(7):
            daybox.append_text(_t(DAY_NAMES[d]))
        daybox.set_active(max(0, min(6, day)))
        box.pack_start(_field(_t("Day"), daybox), False, False, 0)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        s_ent = Gtk.Entry(text=start)
        s_ent.set_width_chars(7)
        s_ent.set_max_length(5)
        s_ent.set_activates_default(True)
        e_ent = Gtk.Entry(text=end)
        e_ent.set_width_chars(7)
        e_ent.set_max_length(5)
        e_ent.set_activates_default(True)
        times.pack_start(_field(_t("Starts"), s_ent), False, False, 0)
        times.pack_start(_field(_t("Ends"), e_ent), False, False, 0)
        box.pack_start(times, False, False, 0)

        room_ent = Gtk.Entry(text=room)
        room_ent.set_placeholder_text(_t("Room or building"))
        room_ent.set_activates_default(True)
        box.pack_start(_field(_t("Room"), room_ent), False, False, 0)

        warn = Gtk.Label(xalign=0)
        warn.set_line_wrap(True)
        warn.set_max_width_chars(34)
        warn.get_style_context().add_class("ac-warn")
        box.pack_start(warn, False, False, 0)

        self._dialog_buttons(dlg, ok_label or _t("Add"), destructive=False,
                             remove_label="Remove" if removable else None)
        dlg.show_all()
        warn.hide()
        s_ent.grab_focus()
        while True:
            resp = dlg.run()
            if resp == Gtk.ResponseType.REJECT:
                dlg.destroy()
                return self._REMOVE
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return None
            s, e = _minutes(s_ent.get_text()), _minutes(e_ent.get_text())
            if s is None:
                warn.set_text(_t("Start time must look like 09:00"))
                warn.show()
                continue
            if e is None:
                warn.set_text(_t("End time must look like 10:30"))
                warn.show()
                continue
            if e <= s:
                warn.set_text(_t("End time must be after the start time."))
                warn.show()
                continue
            if not self.classes:
                warn.set_text(_t("Add a class first."))
                warn.show()
                continue
            out = (self._combo_class_index(cls), daybox.get_active(),
                   _hhmm(s), _hhmm(e), room_ent.get_text().strip()[:40])
            dlg.destroy()
            return out

    def _add_meeting(self, day=None, start=None):
        """Add a class time. `day` and `start` prefill the dialog when the
        caller knows where on the week the request came from — a double-click
        on an empty slot does; the button at the top of the pane does not."""
        # First run in the Schedule view: there is nothing to put on the
        # timetable yet, so ask what the class is called instead of inventing
        # an "Untitled Class 1" (and a stray blank lecture) behind her back.
        if not self.classes:
            name = self._name_dialog("New Class", "Add",
                                     placeholder=_t("Organic Chemistry"))
            if not name:
                return
            self.undo.checkpoint("New Class")
            self.classes.append({"label": name,
                                 "color": CLASS_COLORS[0], "room": "",
                                 "instructor": "", "meets": []})
            self.undo.commit()
            self._refresh_sidebar()
            self._save_to_disk()
        kw = {}
        if day is not None:
            kw["day"] = day
        if start is not None:
            kw["start"] = start
            # An hour is the default length; the dialog's own 10:00 would be
            # before the start for any slot picked after it.
            s = _minutes(start)
            if s is not None:
                kw["end"] = _hhmm(min(s + 60, 24 * 60 - 1))
        got = self._meeting_dialog(_t("Add a class time"), **kw)
        if got is None or got is self._REMOVE:
            return
        ci, day, start, end, room = got
        self.undo.checkpoint("Add a Class Time")
        self.classes[ci].setdefault("meets", []).append(
            {"day": day, "start": start, "end": end, "room": room})
        self.classes[ci]["meets"] = self._clean_meets(self.classes[ci]["meets"])
        self._save_to_disk()
        self.undo.commit()
        self._refresh_schedule()
        self._refresh_sidebar()

    # ---------------- classes as first-class things ----------------
    #
    # A class used to be reachable only THROUGH a lecture note: renaming one
    # meant selecting one of its lectures first, its colour could not be
    # changed at all, and `room` / `instructor` were in the saved file with no
    # way to ever set them. That left the Schedule view -- the view whose whole
    # subject is classes -- with no way to manage a class, and a class created
    # from the timetable could never be renamed at all, because it had no
    # lecture to select. The editor below is the one place a class is managed,
    # and the sidebar opens it in every view.

    def _class_dialog(self, title, ci=None, ok_label="Save"):
        """Create or edit a class. Returns a dict of its fields, _REMOVE, or
        None if it was cancelled."""
        cur = self.classes[ci] if ci is not None and 0 <= ci < len(self.classes) \
            else {}
        dlg = self._dialog_shell(title)
        box = dlg._box

        name = Gtk.Entry(text=cur.get("label", ""))
        name.set_placeholder_text(_t("Organic Chemistry"))
        name.set_activates_default(True)
        name.set_max_length(60)
        name.set_size_request(300, -1)
        name.get_style_context().add_class("acdlgentry")
        box.pack_start(_field(_t("Class"), name), False, False, 0)

        # Colour: the swatch that identifies this class everywhere else in the
        # app -- on the timetable, in the sidebar, on the desktop tile. Radio
        # buttons so exactly one is chosen and the keyboard can reach them.
        swrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chosen = cur.get("color", CLASS_COLORS[len(self.classes)
                                               % len(CLASS_COLORS)])
        first, picks = None, {}
        for col in CLASS_COLORS:
            rb = Gtk.RadioButton.new_from_widget(first)
            if first is None:
                first = rb
            rb.set_mode(False)
            rb.get_style_context().add_class("ac-swatchbtn")
            da = Gtk.DrawingArea()
            da.set_size_request(22, 22)
            da.connect("draw", self._swatch_draw, col)
            rb.add(da)
            rb.set_tooltip_text(_t("Class colour"))
            if col == chosen:
                rb.set_active(True)
            picks[rb] = col
            swrow.pack_start(rb, False, False, 0)
        box.pack_start(_field(_t("Colour"), swrow), False, False, 0)

        room = Gtk.Entry(text=cur.get("room", ""))
        room.set_placeholder_text(_t("Doherty 2210"))
        room.set_activates_default(True)
        room.set_max_length(40)
        room.get_style_context().add_class("acdlgentry")
        box.pack_start(_field(_t("Room"), room), False, False, 0)

        who = Gtk.Entry(text=cur.get("instructor", ""))
        who.set_placeholder_text(_t("Dr Peraza"))
        who.set_activates_default(True)
        who.set_max_length(60)
        who.get_style_context().add_class("acdlgentry")
        box.pack_start(_field(_t("Instructor"), who), False, False, 0)

        self._dialog_buttons(dlg, ok_label, destructive=False,
                             remove_label="Delete" if ci is not None else None)
        dlg.show_all()
        name.grab_focus()
        name.select_region(0, -1)
        resp = dlg.run()
        out = None
        if resp == Gtk.ResponseType.REJECT:
            out = self._REMOVE
        elif resp == Gtk.ResponseType.OK:
            label = name.get_text().strip()
            if label:
                col = chosen
                for rb, c in picks.items():
                    if rb.get_active():
                        col = c
                        break
                out = {"label": label[:60], "color": col,
                       "room": room.get_text().strip()[:40],
                       "instructor": who.get_text().strip()[:60]}
        dlg.destroy()
        return out

    def _new_class_only(self):
        """Add a class WITHOUT inventing a lecture for it.

        `_new_class` (the Notes view's action) appends a blank lecture too,
        which is right when you are about to type notes into it and wrong from
        the Schedule, where a class you have not sat yet would arrive with a
        phantom "01" note attached."""
        got = self._class_dialog("Add a class", ok_label="Add")
        if not got or got is self._REMOVE:
            return
        self.undo.checkpoint("Add a Class")
        got["meets"] = []
        self.classes.append(got)
        self._save_to_disk()
        self.undo.commit()
        self._refresh_schedule()
        self._refresh_homework()
        self._refresh_sidebar()

    def _edit_class(self, ci):
        """Edit one class by INDEX -- no lecture needs to be selected, and none
        needs to exist."""
        if not 0 <= ci < len(self.classes):
            return
        got = self._class_dialog("Edit class", ci=ci)
        if got is None:
            return
        if got is self._REMOVE:
            self._delete_class_at(ci)
            return
        self.undo.checkpoint("Edit Class")
        self.classes[ci].update(got)
        # The note canvas prints the class name in its header, so an open
        # lecture has to be re-rendered or it keeps the old one.
        self._capture_active()
        self._save_to_disk()
        self.undo.commit()
        self._refresh_schedule()
        self._refresh_homework()
        self._refresh_sidebar()
        self._refresh_canvas()

    def _delete_class_at(self, ci):
        """Delete a class by index, with everything that hangs off it."""
        if not 0 <= ci < len(self.classes):
            return
        cl = self.classes[ci]
        n_lec = sum(1 for l in self.lectures if l.get("cls") == ci)
        n_hw = sum(1 for h in self.homework if h.get("cls") == ci)
        bits = []
        if n_lec:
            bits.append(_t("%d lecture%s") % (n_lec, "" if n_lec == 1 else "s"))
        if n_hw:
            bits.append(_t("%d assignment%s")
                        % (n_hw, "" if n_hw == 1 else "s"))
        if bits:
            detail = _t("“%s”, its class times and %s will be removed.") % (
                cl.get("label", ""), _t(" and ").join(bits))
        else:
            detail = _t("“%s” and its class times will be removed.") % cl.get(
                "label", "")
        if not self._confirm("Delete this class?", detail):
            return
        # Flush the open lecture's live buffer into ITS OWN record before the
        # model moves under it. Deleting a class is NOT the same as deleting
        # the lecture you happen to be typing in (unlike _delete_lecture, where
        # dropping the debounce is right), so those keystrokes are the user's
        # work -- and the capture only lands in the right place while `active`
        # still points at the lecture the buffer belongs to.
        self._capture_active()
        self._may_empty = True
        self.undo.checkpoint("Delete Class")
        del self.classes[ci]
        self.lectures = [l for l in self.lectures if l.get("cls") != ci]
        for l in self.lectures:
            if l.get("cls", 0) > ci:
                l["cls"] -= 1
        # An assignment OUTLIVES its class rather than being destroyed with it
        # -- it is still work you have to do. It loses its class tag instead.
        for h in self.homework:
            c = h.get("cls", -1)
            if c == ci:
                h["cls"] = -1
            elif c > ci:
                h["cls"] = c - 1
        self.active = 0 if self.lectures else -1
        self._sel_block = -1
        # Rebuild the note view BEFORE saving, the same order _delete_lecture
        # uses. _save_to_disk opens with its own _capture_active, and until the
        # canvas has been rebuilt for the new `active` the live buffer still
        # holds the OUTGOING lecture's text and tag spans -- so saving first
        # copied that text straight over lectures[0], destroying a surviving
        # lecture of a class the user never deleted, and persisting it.
        self._refresh_schedule()
        self._refresh_homework()
        self._refresh_sidebar()
        self._refresh_canvas()
        self._save_to_disk()
        self.undo.commit()

    def _edit_meeting(self, ci, meeting):
        got = self._meeting_dialog(_t("Edit class time"), ci=ci,
                                   day=meeting["day"], start=meeting["start"],
                                   end=meeting["end"],
                                   room=meeting.get("room", ""),
                                   ok_label=_t("Save"), removable=True)
        if got is None:
            return
        if got is self._REMOVE:
            self._remove_meeting(ci, meeting)
            return
        new_ci, day, start, end, room = got
        self.undo.checkpoint("Edit Class Time")
        try:
            self.classes[ci]["meets"].remove(meeting)
        except (KeyError, ValueError, IndexError):
            pass
        self.classes[new_ci].setdefault("meets", []).append(
            {"day": day, "start": start, "end": end, "room": room})
        for c in self.classes:
            c["meets"] = self._clean_meets(c.get("meets"))
        self._save_to_disk()
        self.undo.commit()
        self._refresh_schedule()

    def _remove_meeting(self, ci, meeting):
        """Take one class time off the timetable. A wrongly entered meeting
        used to be uneditable-away: the dialog could only move it."""
        if not self._confirm(
                "Remove this class time?",
                _t("%s on %s at %s will be removed. The class and its notes "
                   "are kept.")
                % (self._class_label(ci) or _t("This class"),
                   _t(DAY_NAMES[meeting["day"] % 7]), meeting["start"]),
                ok_label="Remove"):
            return
        self.undo.checkpoint("Remove Class Time")
        try:
            self.classes[ci]["meets"].remove(meeting)
        except (KeyError, ValueError, IndexError):
            pass
        self._sel_block = -1
        self._save_to_disk()
        self.undo.commit()
        self._refresh_schedule()
        self._refresh_sidebar()

    def _homework_dialog(self, title, name="", cls=-1, due="", note="",
                         kind="work", ok_label=None, removable=False):
        """Create or edit an assignment. Returns a dict of its fields, _REMOVE,
        or None if it was cancelled — the same three outcomes, in the same
        shapes, as _class_dialog."""
        dlg = self._dialog_shell(title)
        box = dlg._box            # see _meeting_dialog: never the content area
        ent = Gtk.Entry(text=name)
        ent.set_placeholder_text(_t("Assignment name"))
        ent.set_activates_default(True)
        box.pack_start(_field(_t("Assignment"), ent), False, False, 0)

        # An exam is a dated commitment to a class exactly as a piece of work
        # is, and it is the date a term is actually organised around — but there
        # was nowhere to record one, so the one thing a student most needs this
        # app to remember was the one thing it could not hold. It rides the same
        # list rather than becoming a fourth concept: same due-date grouping,
        # same class tie, same tick when it is behind you.
        kinds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        kind_btns = {}
        first_kind = None
        for key, label in (("work", _t("Assignment")), ("exam", _t("Exam"))):
            rb = Gtk.RadioButton.new_with_label_from_widget(first_kind, label)
            if first_kind is None:
                first_kind = rb
            rb.set_mode(False)
            rb.get_style_context().add_class("ac-segbtn")
            if key == kind:
                rb.set_active(True)
            kind_btns[key] = rb
            kinds.pack_start(rb, True, True, 0)
        box.pack_start(_field(_t("Kind"), kinds), False, False, 0)

        combo = self._class_combo(cls, allow_none=True)
        box.pack_start(_field(_t("Class"), combo), False, False, 0)

        today = nbapp.day_ordinal(_today_key())
        due_ent = Gtk.Entry(text=due)
        # An example date rather than "YYYY-MM-DD": the pattern is a piece of
        # programmer shorthand, and one real date shows the shape of it to
        # anybody.
        due_ent.set_placeholder_text(_t("Example: %s") % _date_key(today))
        due_ent.set_activates_default(True)
        box.pack_start(_field(_t("Due"), due_ent), False, False, 0)

        quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, offset in ((_t("Today"), 0), (_t("Tomorrow"), 1),
                              (_t("Next week"), 7)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("ac-quiet")
            b.connect("clicked",
                      lambda _b, o=offset: due_ent.set_text(_date_key(today + o)))
            quick.pack_start(b, False, False, 0)
        clear = Gtk.Button(label=_t("No date"))
        clear.get_style_context().add_class("ac-quiet")
        clear.connect("clicked", lambda _b: due_ent.set_text(""))
        quick.pack_start(clear, False, False, 0)
        box.pack_start(quick, False, False, 0)

        # WHAT the assignment actually is. `note` has been in the saved schema,
        # preserved by _clean_homework and capped at 200 characters, since the
        # homework list was written — with no field to type it in and no line to
        # show it, so nothing could ever put a character there. That is the same
        # defect `room` and `instructor` had on a class: a field the file keeps,
        # the UI cannot reach, and the reader is never told about.
        note_ent = Gtk.Entry(text=note)
        note_ent.set_placeholder_text(_t("Chapters 4 to 7, show working"))
        note_ent.set_activates_default(True)
        note_ent.set_max_length(200)      # the cap _clean_homework enforces
        note_ent.get_style_context().add_class("acdlgentry")
        box.pack_start(_field(_t("Note"), note_ent), False, False, 0)

        warn = Gtk.Label(xalign=0)
        warn.set_line_wrap(True)
        warn.set_max_width_chars(34)
        warn.get_style_context().add_class("ac-warn")
        box.pack_start(warn, False, False, 0)

        self._dialog_buttons(dlg, ok_label or _t("Add"), destructive=False,
                             remove_label="Remove" if removable else None)
        dlg.show_all()
        warn.hide()
        ent.grab_focus()
        while True:
            resp = dlg.run()
            if resp == Gtk.ResponseType.REJECT:
                dlg.destroy()
                return self._REMOVE
            if resp != Gtk.ResponseType.OK:
                dlg.destroy()
                return None
            text = ent.get_text().strip()
            if not text:
                warn.set_text(_t("Enter an assignment name."))
                warn.show()
                continue
            d = due_ent.get_text().strip()
            if d:
                canon = _canonical_date(d)
                if canon is None:
                    # Two different mistakes, two different sentences: a date
                    # this app cannot read at all, versus a date it can read
                    # perfectly well that is not on the calendar. Answering
                    # "a due date looks like 2026-08-11" to somebody who typed
                    # 2026-02-29 tells them nothing about what is wrong with it.
                    warn.set_text(
                        _t("A due date looks like %s") % _date_key(today)
                        if nbapp.day_ordinal(d) is None
                        else _t("There is no such day in that month."))
                    warn.show()
                    continue
                d = canon       # store the normalised form, never the raw text
            # Read the radio's POSITION, never its label: nbi18n translates
            # widget text in place, so matching on the string breaks in every
            # language but English. Same rule as _combo_class_index.
            out = {"title": text[:120],
                   "cls": self._combo_class_index(combo, allow_none=True),
                   "due": d, "note": note_ent.get_text().strip()[:200],
                   "kind": ("exam" if kind_btns["exam"].get_active()
                            else "work")}
            dlg.destroy()
            return out

    def _new_homework(self):
        # Prefill the class the same way a new lecture does: whatever you are
        # in, or about to be in.
        nxt = self._next_meeting()
        got = self._homework_dialog(_t("Add an assignment"),
                                    cls=nxt[0] if nxt else -1)
        if got is None or got is self._REMOVE:
            return
        self.undo.checkpoint("Add an Assignment")
        self.homework.append({"title": got["title"], "cls": got["cls"],
                              "due": got["due"], "done": False,
                              "kind": got.get("kind", "work"),
                              "note": got.get("note", "")})
        self._save_to_disk()
        self.undo.commit()
        self._refresh_homework(focus=len(self.homework) - 1)

    def _edit_homework(self, index):
        try:
            h = self.homework[index]
        except IndexError:
            return
        got = self._homework_dialog(_t("Edit assignment"), name=h["title"],
                                    cls=h["cls"], due=h["due"],
                                    note=h.get("note", ""),
                                    kind=h.get("kind", "work"),
                                    ok_label=_t("Save"), removable=True)
        if got is None:
            return
        if got is self._REMOVE:
            self._remove_homework(index)
            return
        self.undo.checkpoint("Edit Assignment")
        h.update(got)
        self._save_to_disk()
        self.undo.commit()
        self._refresh_homework(focus=index)

    def _remove_homework(self, index):
        """Take one assignment off the list. Until now the only way to lose a
        mistyped assignment was to tick it done and clear the finished ones —
        which quietly told the desktop tile you had done work you had not."""
        try:
            h = self.homework[index]
        except IndexError:
            return
        if not self._confirm(
                "Remove this assignment?",
                _t("“%s” will be removed.") % h["title"], ok_label="Remove"):
            return
        self.undo.checkpoint("Remove Assignment")
        del self.homework[index]
        self._save_to_disk()
        self.undo.commit()
        self._refresh_homework()

    def _delete_homework(self):
        """Clear out what is already finished — the only bulk delete here, and
        the only one worth having: a finished assignment is history, not
        something you keep."""
        done = [i for i, h in enumerate(self.homework) if h["done"]]
        if not done:
            return
        if not self._confirm(_t("Clear finished homework"),
                             _t("Remove %d finished assignment%s? "
                                "Unfinished assignments are kept.")
                             % (len(done), "" if len(done) == 1 else "s"),
                             ok_label="Remove"):
            return
        self._may_empty = True
        self.undo.checkpoint("Clear Finished Homework")
        self.homework = [h for h in self.homework if not h["done"]]
        self._save_to_disk()
        self.undo.commit()
        self._refresh_homework()

    # ---------------- views ----------------

    def _on_view_toggled(self, btn, key):
        if btn.get_active():
            self._set_view(key)

    def _set_view(self, key):
        """Show one of the three views and refresh what it shows.

        Each view is refreshed on the way IN rather than on every edit: a note
        typed in the editor changes the timetable's word of nothing, and
        rebuilding a hidden view on every keystroke is how a note-taking app
        starts dropping characters."""
        self.view = key
        try:
            # A Stack will not switch to a child that has never been shown, so
            # the switch silently did nothing when it ran before the window was
            # mapped — which is exactly when __init__ picks the opening view.
            self.stack.show_all()
            self._pager.switch(key)
        except Exception:
            return
        btn = self._view_buttons.get(key)
        if btn is not None and not btn.get_active():
            btn.set_active(True)
        if key == "schedule":
            self._refresh_schedule()
        elif key == "homework":
            self._refresh_homework()
        # The sidebar's CONTENT depends on the view (lectures in Notes, the
        # class list in Schedule and Homework), so it has to be rebuilt on the
        # way in too -- not just the search box's visibility.
        try:
            self._refresh_sidebar()
        except Exception:
            pass

    def _refresh_schedule(self):
        if getattr(self, "sched_sub", None) is None:
            return                       # called before the view was built
        meets = self._all_meets()
        nxt = self._next_meeting()
        # The subtitle's job is to say what is coming up next. With nothing on
        # the timetable there is no next, and the empty state below already
        # names the situation and offers the way out of it — so the subtitle
        # says NOTHING rather than repeating it. It used to say "No classes"
        # directly under a heading reading "No classes" and directly above an
        # empty state reading "No classes": the same three words three times on
        # one screen, which is the first screen of a fresh install.
        if not meets:
            self.sched_sub.set_text("")
        elif nxt is None:
            self.sched_sub.set_text(_t("No more classes this week"))
        else:
            ci, m, ahead = nxt
            if ahead == 0:
                when = _t("today")
            elif ahead == 1:
                when = _t("tomorrow")
            elif ahead >= 7:
                # A wrap all the way round the week: "Tuesday" on a Tuesday
                # reads as today, which is the one thing it is not.
                when = _t("next %s") % _t(DAY_NAMES[m["day"]])
            else:
                when = _t(DAY_NAMES[m["day"]])
            self.sched_sub.set_text(
                _t("Next: %s, %s at %s") % (self._class_label(ci), when,
                                            m["start"]))
        # No class times at all -> the empty state, not a blank grid.
        #
        # ALWAYS "No class times", including when there are no classes either.
        # This pane's subject is when your classes meet, and with an empty term
        # it has none of those whichever way you say it — while "No classes"
        # here was the third copy of the words already in the sidebar heading.
        # The button below reads "Add a class time" and _add_meeting asks for
        # the class name first when there is not one yet, so the way out of the
        # empty state is the same either way.
        self.sched_empty_title.set_text(_t("No class times"))
        self.sched_stack.set_visible_child_name("grid" if meets else "empty")
        if self.grid_area is not None:
            if not (0 <= self._sel_block < len(getattr(self, "_blocks", []))):
                self._sel_block = -1
            self._size_grid()
            self.grid_area.queue_draw()

    def _size_grid(self):
        """Ask for the SMALLEST height the drawn week is still legible at.

        A request, not a demand: the grid sits in a viewport, which hands the
        child whatever room it has when that is more, and _draw_timetable then
        spreads the day across it (see _px_per_min). Asking for the full tuned
        height here instead is what used to push the last hour of the week below
        the fold — the request became the size, and the size never fitted."""
        lo, hi = self._grid_bounds()
        self.grid_area.set_size_request(
            -1, int((hi - lo) * self._MIN_PX) + self._HDR_H + 8)

    # ---------------- homework view ----------------

    _HW_COLUMN_W = 620      # the reading column every list in this OS is held to

    def _build_homework(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrap.get_style_context().add_class("ac-main")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        sw, _sh = nbapp.screen_size()
        inner.set_size_request(max(320, min(self._HW_COLUMN_W, sw - 480)), -1)
        inner.set_margin_top(26)
        inner.set_margin_bottom(24)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=_t("Homework"), xalign=0)
        t.get_style_context().add_class("ac-title")
        titles.pack_start(t, False, False, 0)
        self.hw_sub = Gtk.Label(xalign=0)
        self.hw_sub.get_style_context().add_class("ac-sub")
        self.hw_sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.hw_sub.set_max_width_chars(40)
        titles.pack_start(self.hw_sub, False, False, 0)
        head.pack_start(titles, True, True, 0)
        add = Gtk.Button(label=_t("Add an assignment"))
        add.get_style_context().add_class("ac-cta")
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _b: self._new_homework())
        head.pack_end(add, False, False, 0)
        # Tidying up used to live only in the View menu, where nobody looking at
        # a list of ticked-off work would think to go for it.
        self.hw_clear = Gtk.Button(label=_t("Clear finished"))
        self.hw_clear.get_style_context().add_class("ac-quiet")
        self.hw_clear.set_valign(Gtk.Align.CENTER)
        self.hw_clear.set_no_show_all(True)
        self.hw_clear.connect("clicked", lambda _b: self._delete_homework())
        head.pack_end(self.hw_clear, False, False, 0)
        inner.pack_start(head, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("ac-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(18)
        rule.set_margin_bottom(6)
        inner.pack_start(rule, False, False, 0)

        self.hw_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.pack_start(self.hw_list, False, False, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(inner, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(holder)
        wrap.pack_start(scroll, True, True, 0)
        return wrap

    def _refresh_homework(self, focus=None):
        """Rebuild the homework list. `focus` is the model index of the row the
        keyboard should land back on: ticking a box moves its row to another
        group, and without this the focus fell off the list entirely and the
        next Space did nothing."""
        if getattr(self, "hw_sub", None) is None:
            return                       # called before the view was built
        for ch in self.hw_list.get_children():
            self.hw_list.remove(ch)
        today = nbapp.day_ordinal(_today_key())
        left = sum(1 for h in self.homework if not h["done"])
        overdue = sum(1 for h in self.homework
                      if not h["done"] and nbapp.day_ordinal(h["due"])
                      is not None and nbapp.day_ordinal(h["due"]) < today)
        finished = len(self.homework) - left
        if not self.homework:
            # Same rule as the Schedule subtitle: the empty state below already
            # says "No assignments", so saying it here too puts the same two
            # words twice on one screen.
            self.hw_sub.set_text("")
        elif not left:
            self.hw_sub.set_text(_t("Nothing to do"))
        elif overdue:
            self.hw_sub.set_text(_t("%d to do  ·  %d overdue") % (left, overdue))
        else:
            self.hw_sub.set_text(_t("%d to do") % left)
        self.hw_clear.set_visible(bool(finished))

        self._hw_ticks = {}
        buckets = self._homework_buckets()
        if not buckets:
            self.hw_list.pack_start(self._hw_empty(), False, False, 0)
            self.hw_list.show_all()
            return
        for key, name, idxs in buckets:
            sec = Gtk.Label(label=name, xalign=0)
            sec.get_style_context().add_class("ac-eyebrow")
            sec.set_margin_top(20)
            sec.set_margin_bottom(6)
            if key == "overdue":
                sec.get_style_context().add_class("late")
            self.hw_list.pack_start(sec, False, False, 0)
            for i in idxs:
                self.hw_list.pack_start(
                    self._hw_row(i, late=(key == "overdue")), False, False, 0)
        self.hw_list.show_all()
        tick = self._hw_ticks.get(focus)
        if tick is not None:
            tick.grab_focus()

    def _hw_empty(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wrap.set_halign(Gtk.Align.CENTER)
        wrap.set_margin_top(60)
        title = Gtk.Label(label=_t("No assignments"))
        title.get_style_context().add_class("ac-empty-title")
        btn = Gtk.Button(label=_t("Add an assignment"))
        btn.get_style_context().add_class("ac-cta")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: self._new_homework())
        wrap.pack_start(title, False, False, 0)
        wrap.pack_start(btn, False, False, 0)
        return wrap

    def _hw_row(self, index, late=False):
        h = self.homework[index]
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("ac-hwrow")

        tick = Gtk.CheckButton()
        tick.set_active(bool(h["done"]))
        tick.set_valign(Gtk.Align.CENTER)
        # The tooltip has to say what ticking will DO, which is the opposite of
        # the state it is in — "Mark as done" over an already-done box is a
        # small lie the reader has to work around.
        if h["done"]:
            tick.set_tooltip_text(_t("Mark as not done"))
        else:
            tick.set_tooltip_text(_t("Mark as done"))
        tick.connect("toggled", self._on_hw_toggle, index)
        row.pack_start(tick, False, False, 0)
        self._hw_ticks[index] = tick

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=h["title"], xalign=0)
        tctx = title.get_style_context()
        tctx.add_class("ac-hwtitle")
        if h["done"]:
            tctx.add_class("done")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(40)
        # A long assignment is trimmed to keep the column its measure, so the
        # full wording has to stay readable somewhere.
        if len(h["title"]) > 40:
            title.set_tooltip_text(h["title"])
        text.pack_start(title, False, False, 0)

        # The meta line, built as a list of parts joined by dots rather than as
        # three hand-wired if-branches — an EXAM marker had to go in front of
        # the class name, and the old shape could not gain a part without
        # another pair of "is there a separator needed" conditionals.
        label = self._class_label(h["cls"])
        due = _pretty_due(h["due"]) if h["due"] else ""
        parts = []
        if h.get("kind") == "exam":
            # An exam reads as a different KIND of thing, not a more urgent one:
            # the accent on this screen means "late" and nothing else, so this
            # is said in small caps and ink rather than in red.
            parts.append((_t("Exam"), ["ac-hwkind"], 0))
        if label:
            # Without a cap a long class name stretches the whole reading column
            # past its measure and shoves the heading off centre.
            parts.append((label, ["ac-hwmeta"], 34))
        if due:
            # Accent means ONE thing on this screen: this is late. Only the DATE
            # carries it — reddening the class name too made the class itself
            # look like the problem.
            cls_names = ["ac-hwmeta"]
            if late and not h["done"]:
                cls_names.append("late")
            parts.append((due, cls_names, 0))
        if parts:
            meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            for i, (txt, names, cap) in enumerate(parts):
                if i:
                    dot = Gtk.Label(label="  ·  ", xalign=0)
                    dot.get_style_context().add_class("ac-hwmeta")
                    meta.pack_start(dot, False, False, 0)
                lb = Gtk.Label(label=txt, xalign=0)
                ctx = lb.get_style_context()
                for n in names:
                    ctx.add_class(n)
                if cap:
                    lb.set_ellipsize(Pango.EllipsizeMode.END)
                    lb.set_max_width_chars(cap)
                meta.pack_start(lb, False, False, 0)
            text.pack_start(meta, False, False, 0)

        # The note, on its own line under the date. Worth a row of its own
        # rather than a tooltip: what the assignment actually asks for is the
        # thing you are looking at this list to remember, and a tooltip cannot
        # be read without a mouse or seen while scanning the page.
        note = (h.get("note") or "").strip()
        if note:
            nl = Gtk.Label(label=note, xalign=0)
            nctx = nl.get_style_context()
            nctx.add_class("ac-hwnote")
            if h["done"]:
                nctx.add_class("done")
            nl.set_ellipsize(Pango.EllipsizeMode.END)
            nl.set_max_width_chars(46)
            if len(note) > 46:
                nl.set_tooltip_text(note)
            text.pack_start(nl, False, False, 0)
        row.pack_start(text, True, True, 0)

        edit = Gtk.Button(label=_t("Edit"))
        edit.get_style_context().add_class("ac-quiet")
        edit.set_valign(Gtk.Align.CENTER)
        edit.set_tooltip_text(_t("Change or remove this assignment"))
        edit.connect("clicked", lambda _b, i=index: self._edit_homework(i))
        row.pack_end(edit, False, False, 0)

        # A colour spine on the left tying the row to its class, the same
        # language the timetable blocks use.
        if h["cls"] >= 0:
            spine = Gtk.DrawingArea()
            spine.set_size_request(3, -1)
            spine.connect("draw", self._draw_spine, self._class_color(h["cls"]))
            wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            wrap.pack_start(spine, False, False, 0)
            wrap.pack_start(row, True, True, 0)
            return wrap
        pad = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        blank = Gtk.Box()
        blank.set_size_request(3, -1)
        pad.pack_start(blank, False, False, 0)
        pad.pack_start(row, True, True, 0)
        return pad

    @staticmethod
    def _draw_spine(area, cr, color):
        r, g, b = _hex_rgb(color)
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 4, area.get_allocated_width(),
                     max(0, area.get_allocated_height() - 8))
        cr.fill()
        return False

    def _on_hw_toggle(self, btn, index):
        try:
            self.homework[index]["done"] = bool(btn.get_active())
        except IndexError:
            return
        self.undo.checkpoint("Tick Off")
        self._save_to_disk()
        self.undo.commit()
        self._refresh_homework(focus=index)

    # ---------------- schedule view ----------------

    def _build_schedule(self):
        """The week as a timetable. Drawn rather than built from widgets: a
        class block has to sit at a position and a height that mean something
        (when it starts, how long it runs), and a box laid out by GTK cannot
        say that."""
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrap.get_style_context().add_class("ac-main")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.set_margin_top(26)
        head.set_margin_start(30)
        head.set_margin_end(30)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=_t("Week"), xalign=0)
        t.get_style_context().add_class("ac-title")
        titles.pack_start(t, False, False, 0)
        self.sched_sub = Gtk.Label(xalign=0)
        self.sched_sub.get_style_context().add_class("ac-sub")
        # "Next: <class name>, ..." must not be able to widen the header (and
        # with it the window's minimum) when a class has a long name.
        self.sched_sub.set_ellipsize(Pango.EllipsizeMode.END)
        self.sched_sub.set_max_width_chars(52)
        titles.pack_start(self.sched_sub, False, False, 0)
        head.pack_start(titles, True, True, 0)

        add = Gtk.Button(label=_t("Add a class time"))
        add.get_style_context().add_class("ac-cta")
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _b: self._add_meeting())
        head.pack_end(add, False, False, 0)
        wrap.pack_start(head, False, False, 0)

        self.grid_area = Gtk.DrawingArea()
        self.grid_area.set_margin_top(18)
        self.grid_area.set_margin_start(30)
        self.grid_area.set_margin_end(30)
        self.grid_area.set_margin_bottom(24)
        self.grid_area.connect("draw", self._draw_timetable)
        # Reachable without a mouse: the timetable takes focus, arrow keys walk
        # the class blocks and Return opens the one you are on. A drawn surface
        # is invisible to GTK's focus chain unless it asks for it, so every
        # class time used to be mouse-only.
        self.grid_area.set_can_focus(True)
        self.grid_area.set_tooltip_text(
            _t("Click a class time to change or remove it. "
               "Double-click an empty slot to add one."))
        self.grid_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.grid_area.connect("button-press-event", self._on_timetable_press)
        self.grid_area.connect("key-press-event", self._on_timetable_key)
        self.grid_area.connect("focus-in-event",
                               lambda *_a: (self.grid_area.queue_draw(), False)[1])
        self.grid_area.connect("focus-out-event",
                               lambda *_a: (self.grid_area.queue_draw(), False)[1])
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.grid_area)

        # Two states, one pane: a real empty state when there is nothing on the
        # timetable, instead of an eight-hour blank grid that tells a first-time
        # user nothing about what to do with it.
        self.sched_stack = Gtk.Stack()
        self.sched_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.sched_stack.add_named(scroll, "grid")
        self.sched_stack.add_named(self._sched_empty(), "empty")
        wrap.pack_start(self.sched_stack, True, True, 0)
        return wrap

    def _sched_empty(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wrap.set_halign(Gtk.Align.CENTER)
        wrap.set_valign(Gtk.Align.START)
        wrap.set_margin_top(90)
        self.sched_empty_title = Gtk.Label(label=_t("No class times"))
        self.sched_empty_title.get_style_context().add_class("ac-empty-title")
        btn = Gtk.Button(label=_t("Add a class time"))
        btn.get_style_context().add_class("ac-cta")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: self._add_meeting())
        wrap.pack_start(self.sched_empty_title, False, False, 0)
        wrap.pack_start(btn, False, False, 0)
        return wrap

    _GUTTER_W = 56          # the hour labels down the left
    _HDR_H = 30             # the day names across the top
    # Pixels per minute. The week is drawn at whatever density makes the whole
    # day fit the pane, between these two bounds.
    #
    # THE BUG THIS EXISTS FOR: the density was a single fixed 1.05, so the grid
    # always demanded (hi-lo)*1.05 pixels no matter how much room it had. On the
    # smallest panel this OS supports the viewport is 623px, and an ordinary
    # 08:00-18:00 week wanted 710 — so the last hour of every weekday sat 87px
    # BELOW THE FOLD, behind a scrollbar. One evening class put it 213px down,
    # a 07:30 lab and a 21:00 seminar 339px. A view whose whole promise is the
    # week at a glance could not show the week at a glance on any supported
    # screen, and stretching the grid to reach an outlying class made it worse
    # rather than better. Now the day is compressed to fit until an hour row
    # would stop being legible (_MIN_PX), and never drawn looser than the
    # density it was originally tuned at (_MAX_PX), so a two-hour term does not
    # get smeared over the whole pane.
    _MIN_PX = 0.62          # an hour row is 37px: still holds its time + room
    _MAX_PX = 1.05          # the tuned density — a comfortable hour row

    def _px_per_min(self, height, lo, hi):
        """The density to draw at, given the height the grid has to work with."""
        span = hi - lo
        if span <= 0:
            return self._MAX_PX
        avail = height - self._HDR_H - 8
        return min(self._MAX_PX, max(self._MIN_PX, avail / float(span)))

    def _draw_timetable(self, area, cr):
        lo, hi = self._grid_bounds()
        ndays = self._grid_days()
        w = area.get_allocated_width()
        ppm = self._px_per_min(area.get_allocated_height(), lo, hi)
        rows_h = int((hi - lo) * ppm)
        # What this paint actually used, recorded for the same reason _blocks is:
        # so a check can measure the picture that was drawn instead of
        # recomputing what it thinks should have been. A gate that re-derives the
        # density cannot see a draw that ignored it — measured, that let a
        # knowingly-broken build report a 573px grid while painting an 823px one.
        self._grid_ppm = ppm
        self._grid_bottom = self._HDR_H + rows_h
        col_w = max(64.0, (w - self._GUTTER_W) / float(ndays))
        # Enough to turn a point on this grid back into a day and a time, for
        # the empty-slot gesture in _on_timetable_press. Recorded here for the
        # same reason as everything above it: the geometry a click is tested
        # against has to be the geometry that was drawn.
        self._grid_geom = (lo, hi, ndays, col_w)
        today = time.localtime().tm_wday

        # No cr.select_font_face here: every string on this grid is drawn with
        # _show_text (Pango), which carries its own font description.

        # today's column, tinted before anything else so every line and block
        # draws over it rather than under
        if today < ndays:
            cr.set_source_rgb(0.937, 0.922, 0.878)          # #EFEBE0
            cr.rectangle(self._GUTTER_W + today * col_w, 0, col_w,
                         rows_h + self._HDR_H)
            cr.fill()

        # day names
        for d in range(ndays):
            x = self._GUTTER_W + d * col_w
            bold = d == today
            if bold:
                cr.set_source_rgb(0.102, 0.098, 0.086)      # ink
            else:
                cr.set_source_rgb(0.431, 0.412, 0.369)      # muted
            label = _t(DAY_ABBR[d])
            wide = _text_w(cr, label, 12, bold)
            _show_text(cr, x + (col_w - wide) / 2, 19, label, 12, bold)

        # hour lines + gutter labels
        cr.set_line_width(1)
        for mins in range(lo, hi + 1, 60):
            y = self._HDR_H + (mins - lo) * ppm
            cr.set_source_rgb(0.918, 0.890, 0.824)          # #EAE3D2
            cr.move_to(self._GUTTER_W, y + 0.5)
            cr.line_to(self._GUTTER_W + ndays * col_w, y + 0.5)
            cr.stroke()
            if mins < hi:
                cr.set_source_rgb(0.604, 0.580, 0.518)      # faint
                text = _hhmm(mins)
                _show_text(cr, self._GUTTER_W - 10 - _text_w(cr, text, 11),
                           y + 4, text, 11)
        # day separators
        cr.set_source_rgb(0.918, 0.890, 0.824)
        for d in range(ndays + 1):
            x = self._GUTTER_W + d * col_w
            cr.move_to(x + 0.5, self._HDR_H)
            cr.line_to(x + 0.5, self._HDR_H + rows_h)
            cr.stroke()

        # the classes themselves, laid out so two classes at the same hour sit
        # side by side (see _day_layout) instead of one painting over the other
        self._blocks = []
        # is_focus(), not has_focus(): has_focus() is False whenever the
        # toplevel itself is not the active window, which would blink the
        # keyboard marker off every time a dialog opened over the week.
        focused = self.grid_area.is_focus()
        for day in range(ndays):
            for ci, m, slot, nslots in self._day_layout(day):
                top = self._HDR_H + (_minutes(m["start"]) - lo) * ppm
                height = max(22.0, (_minutes(m["end"]) - _minutes(m["start"]))
                             * ppm)
                lane = (col_w - 6) / float(nslots)
                x = self._GUTTER_W + day * col_w + 3 + slot * lane
                bw = lane - (2 if nslots > 1 else 0)
                pad = 10 if bw >= 70 else 6
                self._blocks.append((x, top, bw, height, ci, m))
                r, g, b = _hex_rgb(self._class_color(ci))
                # a soft wash of the class colour with a solid spine down its
                # left edge: the block reads as belonging to that class without
                # shouting
                cr.set_source_rgb(r + (1 - r) * 0.82, g + (1 - g) * 0.82,
                                  b + (1 - b) * 0.82)
                cr.rectangle(x, top, bw, height)
                cr.fill()
                cr.set_source_rgb(r, g, b)
                cr.rectangle(x, top, 3, height)
                cr.fill()

                cr.set_source_rgb(0.102, 0.098, 0.086)
                self._clip_text(cr, self._class_label(ci), x + pad, top + 15,
                                bw - pad - 6, 12)
                if height >= 34:
                    cr.set_source_rgb(0.431, 0.412, 0.369)
                    # A narrow lane (two or three classes stacked over the same
                    # hour) cannot hold "09:00-10:30 · Lab B4"; showing the
                    # start time alone beats showing "09:0…".
                    if bw >= 92:
                        detail = "%s-%s" % (m["start"], m["end"])
                        room = (m.get("room")
                                or self.classes[ci].get("room") or "")
                        if room:
                            detail += "  ·  " + room
                    else:
                        detail = m["start"]
                    self._clip_text(cr, detail, x + pad, top + 29,
                                    bw - pad - 6, 10.5)
                # Keyboard focus ring, in ink — the accent on this screen is
                # spoken for by the view switcher.
                if focused and len(self._blocks) - 1 == self._sel_block:
                    cr.set_source_rgb(0.102, 0.098, 0.086)
                    cr.set_line_width(2)
                    cr.rectangle(x + 1, top + 1, max(2.0, bw - 2),
                                 max(2.0, height - 2))
                    cr.stroke()
                    cr.set_line_width(1)
        return False

    def _day_layout(self, day):
        """One day's meetings as [(class index, meeting, slot, slots)].

        Two classes booked over the same hour used to be drawn one on top of
        the other at full column width: the earlier one vanished, its length
        was unreadable, and a click always landed on whichever happened to be
        first. They are now packed into side-by-side lanes, the way a diary
        does it — every overlapping run gets as many lanes as it needs, and a
        run that overlaps nothing still gets the full column."""
        meets = sorted((p for p in self._all_meets() if p[1]["day"] == day),
                       key=lambda p: (_minutes(p[1]["start"]),
                                      _minutes(p[1]["end"])))
        out = []
        run, run_end = [], None

        def flush():
            lanes = []                      # lanes[i] = when lane i frees up
            placed = []
            for ci, m in run:
                s, e = _minutes(m["start"]), _minutes(m["end"])
                for i, free in enumerate(lanes):
                    if s >= free:
                        lanes[i] = e
                        placed.append((ci, m, i))
                        break
                else:
                    lanes.append(e)
                    placed.append((ci, m, len(lanes) - 1))
            for ci, m, i in placed:
                out.append((ci, m, i, len(lanes)))

        for ci, m in meets:
            s, e = _minutes(m["start"]), _minutes(m["end"])
            if run and s >= run_end:
                flush()
                run, run_end = [], None
            run.append((ci, m))
            run_end = e if run_end is None else max(run_end, e)
        if run:
            flush()
        return out

    @staticmethod
    def _clip_text(cr, text, x, y, maxw, size):
        """Draw `text`, cut with an ellipsis rather than spilling out of its
        block — cairo will happily paint straight over the next column.

        Measured and drawn through Pango (see _show_text): a class name typed in
        Japanese, Chinese, Korean, Hindi or Yiddish came out of the toy font API
        as a row of empty boxes, and was MEASURED as those boxes too, so even the
        truncation was computed against the wrong width."""
        if maxw <= 8:
            return
        if _text_w(cr, text, size) <= maxw:
            _show_text(cr, x, y, text, size)
            return
        ell = "…"
        cut = text
        while cut and _text_w(cr, cut + ell, size) > maxw:
            cut = cut[:-1]
        _show_text(cr, x, y, cut + ell if cut else "", size)

    def _on_timetable_press(self, _area, ev):
        self.grid_area.grab_focus()
        for i, (x, y, w, h, ci, m) in enumerate(getattr(self, "_blocks", [])):
            if x <= ev.x <= x + w and y <= ev.y <= y + h:
                self._sel_block = i
                self.grid_area.queue_draw()
                self._edit_meeting(ci, m)
                return True
        self._sel_block = -1
        self.grid_area.queue_draw()
        # An empty slot on a timetable is a place a class could go, and the only
        # way to put one there was a button at the top of the pane that opened
        # on Monday 09:00 whatever you had been looking at. Double-click fills
        # the day and the hour in from where you actually pointed.
        #
        # DOUBLE, not single: a single click has to stay free to focus the grid
        # for keyboard use and to drop the selection, and a modal that opens
        # because somebody clicked the background is a trap. The tooltip on the
        # grid says the gesture out loud, since nothing else would.
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            slot = self._slot_at(ev.x, ev.y)
            if slot is not None:
                day, start = slot
                self._add_meeting(day=day, start=start)
                return True
        return False

    def _slot_at(self, x, y):
        """(day, "HH:MM") for a point on the grid, or None if it is not on one.

        The time is snapped to the half hour: a click lands wherever the pointer
        was, and 10:17 is not a time anybody schedules a class for."""
        geom = getattr(self, "_grid_geom", None)
        if not geom or not self._grid_ppm:
            return None
        lo, hi, ndays, col_w = geom
        if y < self._HDR_H or x < self._GUTTER_W:
            return None              # the day header or the hour gutter
        day = int((x - self._GUTTER_W) // col_w)
        if not 0 <= day < ndays:
            return None
        mins = lo + (y - self._HDR_H) / self._grid_ppm
        snapped = int(mins // 30) * 30
        # Never offer a start the grid cannot show, and leave room for the hour
        # the meeting dialog defaults to.
        snapped = max(lo, min(hi - 60, snapped))
        return day, _hhmm(max(0, snapped))

    def _on_timetable_key(self, _area, ev):
        """Walk the class blocks with the arrow keys and open one with Return —
        the whole timetable was mouse-only before this."""
        blocks = getattr(self, "_blocks", [])
        if not blocks:
            return False
        # Deliberately NOT Tab: swallowing it would make the timetable a place
        # the keyboard can get into and never get out of.
        if ev.keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self._sel_block = (self._sel_block + 1) % len(blocks)
        elif ev.keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self._sel_block = (self._sel_block - 1) % len(blocks)
        elif ev.keyval in (Gdk.KEY_Home,):
            self._sel_block = 0
        elif ev.keyval in (Gdk.KEY_End,):
            self._sel_block = len(blocks) - 1
        elif ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if 0 <= self._sel_block < len(blocks):
                _x, _y, _w, _h, ci, m = blocks[self._sel_block]
                self._edit_meeting(ci, m)
                return True
            self._sel_block = 0
        elif ev.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            # DELETE removes; Escape never does, anywhere in this OS. Removing
            # a class time still goes through the same confirm the dialog's
            # Remove button uses -- a key press is not a licence to skip it.
            if 0 <= self._sel_block < len(blocks):
                _x, _y, _w, _h, ci, m = blocks[self._sel_block]
                self._remove_meeting(ci, m)
                return True
            return False
        else:
            return False
        self.grid_area.queue_draw()
        return True

    # ---------------- schedule + homework model ----------------

    @staticmethod
    def _clean_meets(raw):
        """A class's meeting times, keeping only the usable ones.

        Deliberately forgiving where the notes loader is strict: a timetable
        entry with a nonsense time is worth dropping on its own, but must never
        take the term's lecture notes down with it."""
        out = []
        for m in _records(raw):
            if not isinstance(m, dict):
                continue
            try:
                day = int(m.get("day"))
            except (TypeError, ValueError):
                continue
            start = _minutes(m.get("start"))
            end = _minutes(m.get("end"))
            if not 0 <= day <= 6 or start is None:
                continue
            if end is None or end <= start:
                end = min(start + 60, 24 * 60 - 1)
            rec = dict(m)          # carry anything this version does not know
            rec.update({"day": day, "start": _hhmm(start), "end": _hhmm(end),
                        "room": str(m.get("room") or "")})
            out.append(rec)
        out.sort(key=lambda m: (m["day"], m["start"]))
        # Every meeting the user entered is kept. This used to end `[:14]`, and
        # a cap applied at LOAD time is written back by the very next save, so
        # the 15th meeting on a class's timetable was not merely hidden — it was
        # deleted from the only copy. Same defect as the homework cap below.
        return out

    @staticmethod
    def _clean_homework(raw, class_at):
        """`class_at` maps a class's index IN THE FILE to its index in the
        loaded list — see _load_from_disk. It is not a count: a malformed class
        record is skipped, which shifts every class after it, and an assignment
        that still names the file's numbering has to be moved with it or it ends
        up on a class its owner never chose."""
        out = []
        for h in _records(raw):
            if not isinstance(h, dict):
                continue
            title = str(h.get("title") or "").strip()
            if not title:
                # An assignment with no name would render as a blank row, which
                # is why this dropped it — but dropping the record destroys
                # everything ELSE it carried, and the note field is free text
                # somebody typed. Measured on a store with a titleless record
                # holding a note: two assignments went in, one came out, and the
                # close-time save wrote that over the only copy.
                #
                # So: salvage anything with content in it, under a name that
                # says plainly that it has none — inventing a title out of the
                # note would put words in the user's mouth. A record with
                # nothing in it at all is not salvage, and costs nothing to let
                # go.
                if not (str(h.get("note") or "").strip()
                        or str(h.get("due") or "").strip()):
                    continue
                title = _t("Untitled assignment")
            try:
                cls = int(h.get("cls", -1))
            except (TypeError, ValueError):
                cls = -1
            cls = class_at.get(cls, -1) if cls >= 0 else -1
            if cls < 0:
                cls = -1           # an assignment can outlive its class
            # Canonical, never the raw text. A store written before the dialog
            # checked this — or edited by hand — can carry "2026-01-32", which
            # the list would render as "32 January" while sorting it as the 1st
            # of February. Anything the calendar does have keeps its day and
            # gains a normal shape ("2026-1-5" -> "2026-01-05"); anything it
            # does not have loses the date rather than the assignment, which is
            # the same trade _clean_meets makes for a nonsense class time.
            due = _canonical_date(str(h.get("due") or "")) or ""
            rec = dict(h)          # carry anything this version does not know
            # NOT truncated on the way in. The dialogs already cap what can be
            # TYPED (set_max_length on the entries), so slicing here protected
            # nothing — it only shortened values that were already stored, by a
            # store from another release, a hand edit, or the board writing
            # alongside. And because the next save serialises this model, the
            # tail was gone for good: the same load-shorten-save shape as the
            # `out[:200]` cap that once cost a student sixty assignments, moved
            # from record COUNTS to the contents of a field.
            rec.update({"title": title, "cls": cls,
                        "due": due,
                        "done": bool(h.get("done")),
                        # Anything that is not the word "exam" is ordinary work,
                        # so a store written before this field existed — and a
                        # store with nonsense in it — both read as work.
                        "kind": ("exam"
                                 if str(h.get("kind") or "").lower() == "exam"
                                 else "work"),
                        "note": str(h.get("note") or "")})
            out.append(rec)
        # EVERY assignment is kept. This used to end `return out[:200]`, and
        # that cap DESTROYED work with no user action at all: a student with 260
        # assignments opened the app, the loader silently dropped 60 of them, and
        # the close-time save wrote the 200 that were left straight over the
        # store. Measured, not theorised — 60 assignments gone on one open+close.
        #
        # The .bak did not save it either. preserve_damaged keeps one
        # previous-good copy guarded by _bak_would_shrink, but _save_to_disk
        # writes a DERIVED "course" field onto every homework record that the
        # loader above never reads back, so 200 saved records (weight 3 each)
        # outweighed 260 original ones (weight 2 each), the guard saw no
        # regression, and the SECOND open overwrote the only remaining copy.
        # Two opens, two closes, nothing the user did. See _bak_would_shrink,
        # which now also compares record COUNTS for exactly this reason.
        #
        # A cap on records the user typed is never the right trade here: this is
        # the only copy of their term. Bounding the UI is the renderer's job.
        return out

    # cls == -1 means "not tied to a class" -- an assignment that outlived the
    # class it belonged to. A NEGATIVE INDEX MUST BE REJECTED EXPLICITLY:
    # self.classes[-1] is the LAST class, not a miss, so IndexError never fires
    # and every untied assignment was labelled and coloured as though it
    # belonged to whichever class happened to be last in the list. Deleting a
    # class therefore appeared to move its homework to a different class.
    def _class_label(self, cls):
        try:
            cls = int(cls)
        except (TypeError, ValueError):
            return ""
        if cls < 0:
            return ""
        try:
            return self.classes[cls].get("label", "")
        except (IndexError, AttributeError):
            return ""

    def _class_color(self, cls):
        try:
            cls = int(cls)
        except (TypeError, ValueError):
            return CLASS_COLORS[0]
        if cls < 0:
            return CLASS_COLORS[0]
        try:
            return self.classes[cls].get("color", CLASS_COLORS[0])
        except (IndexError, AttributeError):
            return CLASS_COLORS[0]

    def _all_meets(self):
        """[(class index, meeting)] across every class, in week order."""
        out = []
        for ci, c in enumerate(self.classes):
            for m in c.get("meets", []):
                out.append((ci, m))
        out.sort(key=lambda p: (p[1]["day"], p[1]["start"]))
        return out

    def _next_meeting(self):
        """(class index, meeting, days ahead) for the next class due to start,
        looking from right now and wrapping into the coming week."""
        meets = self._all_meets()
        if not meets:
            return None
        now = time.localtime()
        mins_now = now.tm_hour * 60 + now.tm_min
        for ahead in range(8):
            day = (now.tm_wday + ahead) % 7
            for ci, m in meets:
                if m["day"] != day:
                    continue
                if ahead == 0 and _minutes(m["start"]) < mins_now:
                    continue
                return ci, m, ahead
        return None

    def _grid_bounds(self):
        """(first minute, last minute) the timetable has to cover — the normal
        working day, stretched if a class falls outside it."""
        lo, hi = DEFAULT_DAY_START, DEFAULT_DAY_END
        for _ci, m in self._all_meets():
            lo = min(lo, _minutes(m["start"]))
            hi = max(hi, _minutes(m["end"]))
        lo = (lo // 60) * 60
        hi = -(-hi // 60) * 60
        return lo, max(hi, lo + 120)

    def _grid_days(self):
        """How many day columns to draw: the working week, extended only if a
        class actually meets at the weekend."""
        days = 5
        for _ci, m in self._all_meets():
            days = max(days, m["day"] + 1)
        return days

    def _homework_buckets(self):
        """Assignments grouped the way someone actually thinks about them:
        what is late, what is today, what is this week, what is after that —
        and everything already finished at the bottom.

        Each group carries a stable key beside its heading. The caller used to
        decide which group was the late one by comparing the heading against
        _t("OVERDUE") — which is the translated string only as long as nothing
        else translates it differently, and is exactly the class of bug the
        combo-box rule in this OS exists to stop."""
        today = nbapp.day_ordinal(_today_key())
        groups = [("overdue", _t("OVERDUE"), []), ("today", _t("TODAY"), []),
                  ("week", _t("THIS WEEK"), []), ("later", _t("LATER"), []),
                  ("nodate", _t("NO DUE DATE"), []), ("done", _t("DONE"), [])]
        for i, h in enumerate(self.homework):
            if h["done"]:
                groups[5][2].append(i)
                continue
            o = nbapp.day_ordinal(h["due"])
            if o is None:
                groups[4][2].append(i)
            elif o < today:
                groups[0][2].append(i)
            elif o == today:
                groups[1][2].append(i)
            elif o - today <= 7:
                groups[2][2].append(i)
            else:
                groups[3][2].append(i)
        # Within a group: by date, then EXAMS FIRST, then by title. Two things
        # due the same day are not equally heavy — the exam is the one the day
        # is actually organised around.
        for _key, _name, idxs in groups:
            idxs.sort(key=lambda i: (nbapp.day_ordinal(self.homework[i]["due"])
                                     if self.homework[i]["due"] else 10 ** 9,
                                     0 if self.homework[i].get("kind") == "exam"
                                     else 1,
                                     self.homework[i]["title"].lower()))
        return [g for g in groups if g[2]]

    # ---------------- sidebar ----------------
    def _build_sidebar(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The sidebar scales with the panel instead of always taking 340px.
        # At 340 this app's minimum width came to exactly 1024 — it "fits" a
        # 1024 panel with nothing to spare, and any drift in a font or a
        # padding pushes the timetable off the right edge.
        sw, _sh = nbapp.screen_size()
        col.set_size_request(min(340, max(220, sw // 5)), -1)
        col.get_style_context().add_class("sidebar")

        # The three views of a term, at the top of the sidebar where the thing
        # being switched is in view. Radio buttons drawn as a segmented strip:
        # exactly one is on, which is what a view switcher means, and GTK gives
        # the keyboard and screen-reader behaviour for free.
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        seg.get_style_context().add_class("ac-seg")
        self._view_buttons = {}
        first = None
        for key, label in (("notes", _t("Notes")),
                           ("schedule", _t("Schedule")),
                           ("homework", _t("Homework"))):
            btn = Gtk.RadioButton.new_with_label_from_widget(first, label)
            if first is None:
                first = btn
            btn.set_mode(False)                  # a button, not a radio dot
            btn.get_style_context().add_class("ac-segbtn")
            # A view name that does not fit the strip WRAPS; it never widens it.
            # A label's minimum width is its longest unbreakable word once it is
            # allowed to wrap, and this strip's minimum is the sidebar's, and the
            # sidebar's is the window's. Measured in Russian, where the three
            # names run to 278px against a sidebar asking for 220: the strip won,
            # the sidebar swelled to 279, and the 59px came straight off the note
            # column — every view paid, in reading measure, for a tab bar. Height
            # is the one thing this pane has to spare (442px of it), so the cost
            # is paid there. WORD, not WORD_CHAR: breaking a single long word
            # mid-glyph to save pixels makes the name unreadable, which is the
            # opposite of the point.
            lbl = btn.get_child()
            if isinstance(lbl, Gtk.Label):
                lbl.set_line_wrap(True)
                lbl.set_line_wrap_mode(Pango.WrapMode.WORD)
                lbl.set_justify(Gtk.Justification.CENTER)
                lbl.set_max_width_chars(11)
            btn.connect("toggled", self._on_view_toggled, key)
            self._view_buttons[key] = btn
            seg.pack_start(btn, True, True, 0)
        col.pack_start(seg, False, False, 0)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("side-head")
        eyebrow = Gtk.Label(label=_t("CLASSES"), xalign=0)
        eyebrow.get_style_context().add_class("side-eyebrow")
        head.pack_start(eyebrow, False, False, 0)
        # Model summary (class/lecture counts), updated on every sidebar refresh
        # in place of the removed semester label.
        self.side_summary = Gtk.Label(label="", xalign=0)
        self.side_summary.get_style_context().add_class("side-term")
        # This line is set at 21px bold, so "12 classes · 140 lectures" was
        # setting the sidebar's minimum width — and with it the window's, on a
        # panel that already has only a few pixels to spare.
        self.side_summary.set_ellipsize(Pango.EllipsizeMode.END)
        self.side_summary.set_max_width_chars(16)
        head.pack_start(self.side_summary, False, False, 0)

        # Search. Notes taken across a whole term run to dozens of lectures in
        # several classes; the only way back to the one that covered eigenvalues
        # was to open them one at a time. This filters the list by class name,
        # lecture title AND note text. Hidden until there is something to search.
        self.search = Gtk.SearchEntry()
        nbicons.style_search_entry(self.search)
        self.search.set_placeholder_text(_t("Search notes"))
        self.search.get_style_context().add_class("acsearch")
        self.search.set_no_show_all(True)     # driven by hand (_refresh_sidebar)
        self.search.connect("search-changed", self._on_search)
        self.search.connect("activate", lambda *_: self._first_match())
        self._query = ""
        self._filter_timer = None
        head.pack_start(self.search, False, False, 0)
        col.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.side_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.side_list.get_style_context().add_class("side-list")
        scroll.add(self.side_list)
        col.pack_start(scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.get_style_context().add_class("side-foot")
        # The sidebar's primary action follows the VIEW. It used to read "New
        # Lecture" on all three, so the biggest button on the Schedule screen
        # made a note instead of a class, and on Homework it made a note
        # instead of an assignment -- the wrong verb on two views out of three.
        newbtn = Gtk.Button()
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("newlecture")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        inner.set_halign(Gtk.Align.CENTER)
        inner.pack_start(
            nbicons.image("plus", 16, "#1A1916"),
            False, False, 0)
        self.newbtn_label = Gtk.Label(label=_t("New Lecture"))
        inner.pack_start(self.newbtn_label, False, False, 0)
        newbtn.add(inner)
        newbtn.connect("clicked", lambda *_: self._sidebar_new())
        self.newbtn = newbtn
        foot.pack_start(newbtn, False, False, 0)
        col.pack_start(foot, False, False, 0)
        return col

    # What the sidebar's primary button does, per view. One table so the
    # label, the tooltip and the action can never drift apart.
    _SIDEBAR_NEW = {"notes":    ("New Lecture", "_new_lecture"),
                    "schedule": ("Add a class", "_new_class_only"),
                    "homework": ("Add an assignment", "_new_homework")}

    # What this pane will hold once there is a term in it, said per view — the
    # empty-state line under the heading. It must not restate the heading,
    # which already says there are no classes.
    _SIDEBAR_EMPTY = {
        "notes":    "Lectures appear here, under the class they belong to",
        "schedule": "Classes appear here, with the times they meet",
        "homework": "Classes appear here, with what is left to do",
    }

    def _sidebar_new(self):
        _lbl, meth = self._SIDEBAR_NEW.get(getattr(self, "view", "notes"),
                                           self._SIDEBAR_NEW["notes"])
        getattr(self, meth)()

    def _refresh_sidebar_button(self):
        label, _m = self._SIDEBAR_NEW.get(getattr(self, "view", "notes"),
                                          self._SIDEBAR_NEW["notes"])
        try:
            self.newbtn_label.set_text(_t(label))
            self.newbtn.set_tooltip_text(_t(label))
        except AttributeError:
            pass                      # called before the sidebar was built

    def _class_row(self, ci, cl, detail):
        """One class in the sidebar: swatch, name, and a line of detail. Click
        opens the class editor -- the same gesture in every view."""
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("cls-row")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sw = Gtk.DrawingArea()
        sw.set_size_request(11, 11)
        sw.set_valign(Gtk.Align.START)
        sw.set_margin_top(5)
        sw.connect("draw", self._swatch_draw, cl.get("color",
                                                     CLASS_COLORS[0]))
        row.pack_start(sw, False, False, 0)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl = Gtk.Label(label=cl.get("label", ""), xalign=0)
        lbl.get_style_context().add_class("cls-rowname")
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(24)
        txt.pack_start(lbl, False, False, 0)
        if detail:
            sub = Gtk.Label(label=detail, xalign=0)
            sub.get_style_context().add_class("cls-rowsub")
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            sub.set_max_width_chars(26)
            txt.pack_start(sub, False, False, 0)
        row.pack_start(txt, True, True, 0)
        btn.add(row)
        btn.set_tooltip_text(_t("Edit %s") % cl.get("label", ""))
        btn.connect("clicked", lambda *_a, i=ci: self._edit_class(i))
        return btn

    def _refresh_sidebar(self):
        # Drop any stale label handle; it's re-captured below for the row that
        # matches self.active, keeping the in-place title update in sync with
        # the model across new/delete/select/rename/reorder.
        self._active_title_label = None
        # Live handles to the lecture rows, by model index, so SELECTING a
        # lecture can move the highlight instead of rebuilding the list. See
        # _set_active_row.
        self._lec_rows = {}
        for c in self.side_list.get_children():
            self.side_list.remove(c)

        nc, nl = len(self.classes), len(self.lectures)
        # This counted the LECTURES to decide whether there were any CLASSES,
        # so a term with three classes and a full timetable but no notes yet
        # sat under the heading "No classes yet" with those three classes
        # listed directly beneath it. And the counted form was never passed
        # through _t(), so it stayed English in all sixteen other languages.
        if not nc:
            summary = _t("No classes")
        elif not nl:
            summary = _t("%d class%s") % (nc, "" if nc == 1 else "es")
        else:
            summary = "%s · %s" % (
                _t("%d class%s") % (nc, "" if nc == 1 else "es"),
                _t("%d lecture%s") % (nl, "" if nl == 1 else "s"))
        self.side_summary.set_text(summary)
        # Only worth showing once there is a notebook to search — and only on
        # the view it searches. Every sidebar rebuild used to re-show it, so
        # adding a class time popped a "Search notes" box into the Schedule.
        self.search.set_visible(bool(self.lectures)
                                and getattr(self, "view", "notes") == "notes")
        self._refresh_sidebar_button()
        view = getattr(self, "view", "notes")

        if not self.classes:
            # The header above already says there are no classes, so this line
            # explains what the pane is for rather than repeating it.
            # Name what THIS view will put here. "classes and their lectures"
            # is notes-speak, and it was the line the Schedule and Homework
            # screens showed too.
            #
            # AND FOR A WHILE IT SAID "No classes" — the same three words the
            # heading directly above it was already showing, so the first screen
            # of a fresh install repeated itself verbatim, one line apart, on
            # all three views. The comment above describes what this line is
            # for; the code had regressed to the thing the comment says not to
            # do. Each view now names what IT will put in this pane.
            empty = Gtk.Label(label=_t(self._SIDEBAR_EMPTY.get(
                view, self._SIDEBAR_EMPTY["notes"])))
            empty.set_line_wrap(True)
            empty.get_style_context().add_class("side-empty")
            self.side_list.pack_start(empty, False, False, 0)
            self.side_list.show_all()
            return

        # Schedule and Homework are about CLASSES, not about notes, so the
        # sidebar lists the classes themselves and each one opens its editor.
        # Both views used to show the lecture list, which meant the Schedule --
        # the screen whose entire subject is your classes -- offered no way to
        # rename one, recolour one or delete one.
        if view in ("schedule", "homework"):
            for ci, cl in enumerate(self.classes):
                if view == "schedule":
                    n = len(cl.get("meets") or [])
                    detail = (_t("No class times") if not n
                              else _t("%d a week") % n)
                    room = cl.get("room", "")
                    if room:
                        detail = "%s · %s" % (detail, room)
                else:
                    n = sum(1 for h in self.homework
                            if h.get("cls") == ci and not h.get("done"))
                    detail = (_t("Nothing to do") if not n
                              else _t("%d to do") % n)
                self.side_list.pack_start(self._class_row(ci, cl, detail),
                                          False, False, 0)
            if view == "homework":
                n = sum(1 for h in self.homework
                        if h.get("cls", -1) < 0 and not h.get("done"))
                if n:
                    other = Gtk.Label(
                        label=_t("%d not tied to a class") % n, xalign=0)
                    other.get_style_context().add_class("side-count")
                    self.side_list.pack_start(other, False, False, 0)
            self.side_list.show_all()
            return

        keep = self._match_lectures()
        if keep is not None:
            if not keep:
                empty = Gtk.Label(
                    label=_t("No note matches “%s”") % self._query)
                empty.set_line_wrap(True)
                empty.set_max_width_chars(26)
                empty.get_style_context().add_class("side-empty")
                self.side_list.pack_start(empty, False, False, 0)
                self.side_list.show_all()
                return
            n = len(keep)
            cnt = Gtk.Label(label=(_t("1 lecture found") if n == 1
                                   else _t("%d lectures found") % n), xalign=0)
            cnt.get_style_context().add_class("side-count")
            self.side_list.pack_start(cnt, False, False, 0)

        for ci, cl in enumerate(self.classes):
            if keep is not None and not any(
                    l["cls"] == ci and i in keep
                    for i, l in enumerate(self.lectures)):
                continue          # this class has no matching lecture
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            hdr.get_style_context().add_class("cls-head")
            # Per-class colour swatch (the darker class palette, never the
            # signage red), matching the mockup's class header marker.
            sw = Gtk.DrawingArea()
            sw.set_size_request(11, 11)
            sw.set_valign(Gtk.Align.CENTER)
            sw.connect("draw", self._swatch_draw, cl["color"])
            hdr.pack_start(sw, False, False, 0)
            lbl = Gtk.Label(label=cl["label"].upper(), xalign=0)
            lbl.get_style_context().add_class("cls-label")
            # A long class name is trimmed rather than allowed to stretch the
            # sidebar (which would push the whole window past the screen edge).
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(28)
            hdr.pack_start(lbl, True, True, 0)
            self.side_list.pack_start(hdr, False, False, 0)

            for li, lec in enumerate(self.lectures):
                if lec["cls"] != ci or (keep is not None and li not in keep):
                    continue
                self.side_list.pack_start(self._lecture_row(li, lec), False,
                                          False, 0)
        self.side_list.show_all()

    # ---------------- search ----------------
    def _match_lectures(self):
        """The set of lecture indices the search text matches, or None when no
        search is active (meaning: show everything)."""
        if not self._query:
            return None
        q = self._query.lower()
        keep = set()
        for i, lec in enumerate(self.lectures):
            cls = lec.get("cls", 0)
            label = (self.classes[cls].get("label", "")
                     if 0 <= cls < len(self.classes) else "")
            hay = "%s %s %s" % (label, lec.get("title", ""), lec.get("notes", ""))
            if q in hay.lower():
                keep.add(i)
        return keep

    def _on_search(self, _entry):
        """Filter the lecture list as the search text is typed, debounced so a
        big notebook is not rebuilt on every keystroke."""
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_timer = GLib.timeout_add(120, self._filter_tick)

    def _filter_tick(self):
        self._filter_timer = None
        # Read the field HERE rather than in the signal handler, so the filter
        # is whatever is actually in the box at the moment it is applied.
        self._query = self.search.get_text().strip()
        # Pull the live buffer into the model first, so words typed a moment ago
        # are searchable.
        self._capture_active()
        self._refresh_sidebar()
        return False

    def _first_match(self):
        """Enter in the search field opens the first lecture that matched."""
        if self._filter_timer:
            GLib.source_remove(self._filter_timer)
        self._filter_tick()
        keep = self._match_lectures()
        if keep:
            for i in self._display_order():
                if i in keep:
                    self._select(i)
                    break
        self._focus_note()

    def _focus_search(self):
        """Ctrl+F / View ▸ Search Notes — put the caret in the search field."""
        if self.lectures:
            self.search.grab_focus()

    def _clear_search(self):
        """Drop the filter and show the whole notebook again."""
        if not self._query and not self.search.get_text():
            return False
        self.search.set_text("")
        self._query = ""
        self._refresh_sidebar()
        return True

    def _lecture_row(self, index, lec):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("lec-row")
        if index == self.active:
            row.get_style_context().add_class("active")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)

        num = Gtk.Label(label=lec["num"])
        num.get_style_context().add_class("lec-num")
        if index == self.active:
            num.get_style_context().add_class("active")
        num.set_valign(Gtk.Align.CENTER)
        box.pack_start(num, False, False, 0)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=lec["title"] or "Untitled Lecture", xalign=0)
        title.get_style_context().add_class("lec-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        if index == self.active:
            # Remember this label so _on_title_changed can update it in place.
            self._active_title_label = title
        txt.pack_start(title, False, False, 0)
        # Said the way the Homework list says it. This row showed the store's
        # own "2026-08-03" while every dated line on the other two views spoke
        # in words ("today", "Friday", "2 days ago"), so one app answered the
        # same question in two dialects depending on which view you asked from.
        # The full date stays reachable on hover, because "3 days ago" is the
        # friendlier form and the exact one is still sometimes the one you want.
        when = _pretty_due(lec["date"]) if lec.get("date") else ""
        if when:
            date = Gtk.Label(label=when, xalign=0)
            date.get_style_context().add_class("lec-date")
            if when != lec["date"]:
                date.set_tooltip_text(lec["date"])
            txt.pack_start(date, False, False, 0)
        box.pack_start(txt, True, True, 0)

        row.add(box)
        row.connect("clicked", lambda *_a, i=index: self._select(i))
        self._lec_rows[index] = (row, num, title)
        return row

    def _swatch_draw(self, area, cr, color):
        # Never let a malformed colour (e.g. a hand-edited JSON) raise inside a
        # draw callback; fall back to the first class colour instead.
        try:
            r, g, b = nbicons._hex(color)
        except Exception:
            r, g, b = nbicons._hex(CLASS_COLORS[0])
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, area.get_allocated_width(),
                     area.get_allocated_height())
        cr.fill()
        return False

    # ---------------- editor ----------------
    def _build_editor(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("editor")

        fbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        fbar.get_style_context().add_class("formatbar")

        # Refs to every format control so the empty state (no lecture open) can
        # grey them out — there is nothing to format on a blank canvas.
        self._fmt_btns = []
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        stylebtn = Gtk.Button()
        stylebtn.set_relief(Gtk.ReliefStyle.NONE)
        stylebtn.get_style_context().add_class("stylebtn")
        stylebtn.set_tooltip_text(
            _t("Paragraph style: Body, Heading, Subheading"))
        sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        self.stylelbl = Gtk.Label(label=_t("Body"))
        sb.pack_start(self.stylelbl, False, False, 0)
        car = Gtk.Label(label="▾")
        car.get_style_context().add_class("caret")
        sb.pack_start(car, False, False, 0)
        stylebtn.add(sb)
        stylebtn.connect("clicked", lambda *_: self._cycle_style())
        left.pack_start(stylebtn, False, False, 0)
        self._fmt_btns.append(stylebtn)
        self._style_btn = stylebtn
        left.pack_start(self._sep(), False, False, 10)

        b = self._txtbtn("B", "bold")
        b.set_tooltip_text(_t("Bold (Ctrl+B)"))
        b.connect("clicked", lambda *_: self._toggle_tag("bold"))
        left.pack_start(b, False, False, 0)
        self._fmt_btns.append(b)
        i = self._txtbtn("I", "ital")
        i.set_tooltip_text(_t("Italic (Ctrl+I)"))
        i.connect("clicked", lambda *_: self._toggle_tag("italic"))
        left.pack_start(i, False, False, 0)
        self._fmt_btns.append(i)
        hi = self._iconbtn("highlight")
        hi.set_tooltip_text(_t("Highlight"))
        hi.connect("clicked", lambda *_: self._toggle_tag("highlight"))
        left.pack_start(hi, False, False, 0)
        self._fmt_btns.append(hi)
        self._highlight_btn = hi
        left.pack_start(self._sep(), False, False, 10)
        blt = self._iconbtn("bullet")
        blt.set_tooltip_text(_t("Bullet list"))
        blt.connect("clicked", lambda *_: self._insert_list("• "))
        left.pack_start(blt, False, False, 0)
        self._fmt_btns.append(blt)
        self._bullet_btn = blt
        num = self._iconbtn("number")
        num.set_tooltip_text(_t("Numbered list"))
        num.connect("clicked", lambda *_: self._insert_list("1. "))
        left.pack_start(num, False, False, 0)
        self._fmt_btns.append(num)
        self._number_btn = num
        fbar.pack_start(left, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.wordlbl = Gtk.Label(label=_t("0 words"))
        self.wordlbl.set_tooltip_text(_t("Words in this lecture"))
        self.wordlbl.get_style_context().add_class("wordcount")
        right.pack_end(self._make_savebox(), False, False, 0)
        right.pack_end(self._sep(), False, False, 0)
        right.pack_end(self.wordlbl, False, False, 0)
        fbar.pack_end(right, False, False, 0)
        col.pack_start(fbar, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("canvaswrap")
        # The paper surface must SPAN the scroller, not just sit under the note
        # column: a centred canvas leaves the viewport's own bin-window exposed
        # either side, and that window is native — with a TextView inside it, it
        # is never repainted and comes up solid BLACK on this no-compositor
        # stack (the Writer bug). So the canvas fills the width and paints the
        # paper, and the note itself lives in a centred column inside it.
        self.canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.canvas.set_halign(Gtk.Align.FILL)
        self.canvas.get_style_context().add_class("canvas")
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.column.set_halign(Gtk.Align.CENTER)
        # Start at the NARROW measure and widen to COLUMN_W once we know how
        # much room the editor really has (below). Requesting the full 720 up
        # front would make it the window's minimum width, which is what pushed
        # the app past a 1024-wide panel; a request can only ever be grown into
        # space that exists.
        self.column.set_size_request(self.COLUMN_MIN_W, -1)
        self.canvas.pack_start(self.column, True, True, 0)
        self._column_w = self.COLUMN_MIN_W
        scroll.connect("size-allocate", self._on_canvas_alloc)
        scroll.add(self.canvas)
        col.pack_start(scroll, True, True, 0)
        return col

    # Ideal note measure, and the narrowest it may be squeezed to on a small
    # panel before the window would otherwise overflow the screen.
    COLUMN_W = 720
    COLUMN_MIN_W = 460

    def _on_canvas_alloc(self, scroll, alloc):
        """Re-fit the note column to the editor's width (window resize)."""
        # Everything around the column inside the scroller — the canvas padding,
        # the scrollbar, any frame — measured rather than guessed, so the column
        # lands exactly inside the space that exists instead of creeping a few
        # pixels wider on every allocation.
        chrome = max(0, scroll.get_preferred_width()[0] - self._column_w)
        want = max(self.COLUMN_MIN_W,
                   min(self.COLUMN_W, alloc.width - chrome))
        if want != self._column_w:
            self._column_w = want
            self.column.set_size_request(want, -1)

    def _make_savebox(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.savedot = Gtk.DrawingArea()
        self.savedot.set_size_request(8, 8)
        self.savedot.set_valign(Gtk.Align.CENTER)
        self._saved = True
        self.savedot.connect("draw", self._draw_savedot)
        box.pack_start(self.savedot, False, False, 0)
        self.savelbl = Gtk.Label(label=_t("Saved %s") % time.strftime("%H:%M"))
        self.savelbl.get_style_context().add_class("savestate")
        box.pack_start(self.savelbl, False, False, 0)
        return box

    def _draw_savedot(self, area, cr):
        # No compositor: paint the whole widget with the opaque formatbar
        # surface first, or the disc's corners render solid black on real HW.
        r, g, b = nbicons._hex("#FCFBF8")
        cr.set_source_rgb(r, g, b)
        cr.rectangle(0, 0, area.get_allocated_width(),
                     area.get_allocated_height())
        cr.fill()
        color = "#7FA98C" if self._saved else "#C8341E"
        r, g, b = nbicons._hex(color)
        cr.set_source_rgb(r, g, b)
        cr.arc(4, 4, 4, 0, 2 * 3.14159265)
        cr.fill()
        return False

    def _refresh_canvas(self):
        for c in self.column.get_children():
            self.column.remove(c)

        if self.active < 0 or not self.lectures:
            # Nothing open: grey the format bar and reset the live indicators so
            # no stale word count / style label lingers from a deleted lecture.
            self._set_fmt_sensitive(False)
            self.stylelbl.set_text("Body")
            self.wordlbl.set_text("0 words")
            wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            wrap.get_style_context().add_class("empty-wrap")
            t = Gtk.Label(label=_t("No lectures"))
            t.get_style_context().add_class("empty-title")
            wrap.pack_start(t, False, False, 0)
            # The action itself, on the pane the reader is looking at: the only
            # way in used to be a button in the far corner of the other pane.
            b = Gtk.Button(label=_t("New Lecture"))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_halign(Gtk.Align.CENTER)
            b.get_style_context().add_class("emptybtn")
            b.connect("clicked", lambda *_: self._new_lecture())
            wrap.pack_start(b, False, False, 0)
            self.column.pack_start(wrap, False, False, 0)
            self.column.show_all()
            return
        self._set_fmt_sensitive(True)

        lec = self.lectures[self.active]
        cl = self._class_of(lec)

        eb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        eb.get_style_context().add_class("canvas-eyebrow-row")
        sw = Gtk.DrawingArea()
        sw.set_size_request(11, 11)
        sw.set_valign(Gtk.Align.CENTER)
        # The eyebrow marker is the class colour (as in the mockup), NOT the
        # signage red — red is reserved for the active/alert states only.
        sw.connect("draw", self._swatch_draw, cl["color"])
        eb.pack_start(sw, False, False, 0)
        eyebrow = Gtk.Label(
            label=("%s · Lecture %s" % (cl["label"], lec["num"])).upper(),
            xalign=0)
        eyebrow.get_style_context().add_class("canvas-eyebrow")
        # A long class name must not be able to widen the note column (and with
        # it the whole window) — let the eyebrow take the row's width and trim
        # to whatever actually fits.
        eyebrow.set_ellipsize(Pango.EllipsizeMode.END)
        eyebrow.set_max_width_chars(48)
        eb.pack_start(eyebrow, True, True, 0)
        # A first lecture makes its class for you, and it is called "Untitled
        # Class 1" until somebody renames it — with the only route to doing so
        # buried in the Edit menu. The class name is right here on the page, so
        # let it be clicked, exactly as the lecture title below it can be.
        # A Button, not an EventBox: an EventBox has no focus, no activation and
        # no accessible role, so "clicked" here meant "clicked with a mouse" and
        # nothing else -- a keyboard or screen-reader user could not reach this
        # rename at all. A button joins the Tab ring, fires on Space/Enter,
        # announces itself as a control, and takes its accessible name from the
        # tooltip below (nbapp's naming hook). Relief NONE plus .doctitlebtn
        # keep it looking exactly like the bare eyebrow row it wraps.
        ebtn = Gtk.Button()
        ebtn.set_relief(Gtk.ReliefStyle.NONE)
        ebtn.get_style_context().add_class("doctitlebtn")
        ebtn.set_tooltip_text(_t("Rename class"))
        ebtn.add(eb)
        ebtn.connect("clicked", lambda *_a: self._rename_class())
        self.column.pack_start(ebtn, False, False, 0)

        # Title: a read/edit pair, not a bare Gtk.Entry. An Entry cannot wrap, so
        # a real lecture title ("Thermodynamics II — the Clausius inequality")
        # ran off the end of the 40px serif field and could never be read back
        # in full; the writer had to arrow through their own heading. The label
        # wraps and shows all of it; clicking swaps in the entry to edit.
        self.title_lbl = Gtk.Label(xalign=0)
        self.title_lbl.set_line_wrap(True)
        self.title_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title_lbl.get_style_context().add_class("doctitle")
        self.title_ev = Gtk.Button()
        self.title_ev.set_relief(Gtk.ReliefStyle.NONE)
        self.title_ev.get_style_context().add_class("doctitlebtn")
        self.title_ev.set_tooltip_text(_t("Rename lecture"))
        self.title_ev.add(self.title_lbl)
        self.title_ev.connect("clicked", lambda *_a: self._focus_title())

        self.title = Gtk.Entry()
        self.title.set_has_frame(False)
        self.title.set_placeholder_text(_t("Lecture title"))
        self.title.get_style_context().add_class("doctitle")
        self.title.set_text(lec["title"])
        self.title.connect("changed", self._on_title_changed)
        # Enter in the title jumps to the note body, so naming a lecture flows
        # straight into typing it up.
        self.title.connect("activate", lambda *_: self._focus_note())
        # Leaving the field goes back to the wrapped, fully readable heading.
        self.title.connect("focus-out-event",
                           lambda *_a: (self._show_title_label(), False)[1])
        self.title.set_no_show_all(True)
        self.column.pack_start(self.title_ev, False, False, 0)
        self.column.pack_start(self.title, False, False, 0)
        self._show_title_label()

        meta = Gtk.Label(label=lec["meta"], xalign=0)
        meta.get_style_context().add_class("canvas-meta")
        self.column.pack_start(meta, False, False, 0)

        self.body = Gtk.TextView()
        self.body.set_wrap_mode(Gtk.WrapMode.WORD)
        self.body.get_style_context().add_class("docbody")
        self.body.set_pixels_below_lines(9)
        self.body.set_pixels_inside_wrap(8)
        # Height only: the note takes its width from the (adaptive) column, and
        # a hard 720 here would pin the window's minimum width above 1024.
        self.body.set_size_request(-1, 460)
        buf = self.body.get_buffer()
        buf.set_text(lec["notes"])
        buf.create_tag("bold", weight=Pango.Weight.BOLD)
        buf.create_tag("italic", style=Pango.Style.ITALIC)
        buf.create_tag("highlight", background="#F0E2C0")
        buf.create_tag("heading", weight=Pango.Weight.BOLD, scale=1.6)
        buf.create_tag("subheading", weight=Pango.Weight.BOLD, scale=1.22)
        # Re-apply the lecture's saved formatting spans so bold/italic/highlight/
        # heading survive switching lectures (before, only plain text restored).
        self._apply_ranges(buf, lec.get("ranges"))
        buf.connect("changed", self._on_notes_changed)
        # Track the caret so the Style indicator always names the paragraph style
        # at the cursor (matching the Writer / Novel toolbars).
        buf.connect("mark-set", self._on_mark_set)
        self._sync_style_label()
        self.column.pack_start(self.body, True, True, 0)

        self.column.show_all()
        self._recount()

    # ---------------- rich-text ranges ----------------
    # Formatting is stored per lecture as {tag_name: [[start_off, end_off], ...]}
    # so bold / italic / highlight / heading / subheading survive lecture
    # switches and disk saves — previously only plain "notes" was synced, so
    # every tag was dropped the moment you left a lecture and came back.
    _RANGE_TAGS = ("bold", "italic", "highlight", "heading", "subheading")

    def _capture_ranges(self):
        """Snapshot the live note buffer's tag spans into the active lecture."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        tbl = buf.get_tag_table()
        end_off = buf.get_char_count()
        ranges = {}
        for name in self._RANGE_TAGS:
            tag = tbl.lookup(name)
            if tag is None:
                continue
            spans = []
            it = buf.get_start_iter()
            # A span open at offset 0 has no preceding toggle, so seed from has_tag.
            start_off = 0 if it.has_tag(tag) else None
            while it.forward_to_tag_toggle(tag):
                off = it.get_offset()
                if it.begins_tag(tag):
                    start_off = off
                elif it.ends_tag(tag) and start_off is not None:
                    spans.append([start_off, off])
                    start_off = None
            if start_off is not None:
                spans.append([start_off, end_off])
            if spans:
                ranges[name] = spans
        self.lectures[self.active]["ranges"] = ranges

    def _apply_ranges(self, buf, ranges):
        """Re-apply serialized tag spans; tolerates old files with no ranges."""
        if not isinstance(ranges, dict):
            return
        n = buf.get_char_count()
        for name in self._RANGE_TAGS:
            spans = ranges.get(name)
            if not isinstance(spans, list):
                continue
            for span in spans:
                try:
                    s, e = int(span[0]), int(span[1])
                except (TypeError, ValueError, IndexError):
                    continue
                s = max(0, min(s, n))
                e = max(0, min(e, n))
                if e <= s:
                    continue
                buf.apply_tag_by_name(
                    name, buf.get_iter_at_offset(s), buf.get_iter_at_offset(e))

    # ---------------- actions ----------------
    def _append_class(self):
        """Append a new untitled class and return its index.

        THE WHOLE SHAPE, not just the two fields this needed. `room`,
        `instructor` and `meets` were left off, so the class that "New Lecture"
        makes on a fresh install was the only class in the app missing them —
        and `_remove_meeting` indexes `["meets"]` directly (twice). Nothing
        reaches that today only because a class with no `meets` key contributes
        no meetings to the timetable, so there is no block to select and remove:
        the app is one refactor away from a KeyError, and one creator disagreeing
        with the schema is how that refactor becomes a crash. The loader already
        fills these in, which is why a save-and-reopen quietly healed it and
        nothing ever showed."""
        color = CLASS_COLORS[len(self.classes) % len(CLASS_COLORS)]
        self.classes.append(
            {"label": "Untitled Class %d" % (len(self.classes) + 1),
             "color": color, "room": "", "instructor": "", "meets": []})
        return len(self.classes) - 1

    def _next_num(self, cls):
        """Next zero-padded lecture number for class index `cls`.

        Parse each lecture's `num` independently: a single non-numeric value
        (a hand-edited / foreign academics.json can persist any string into
        'num') must not discard the rest, or the next lecture gets numbered
        '01' again and silently duplicates an existing number.
        """
        nums = []
        for l in self.lectures:
            if l["cls"] != cls:
                continue
            try:
                nums.append(int(l["num"]))
            except (TypeError, ValueError):
                continue
        return "%02d" % (max(nums) + 1 if nums else 1)

    def _blank_lecture(self, cls=0, num="01"):
        """A fresh lecture, filled in from the timetable where it can be.

        The point of keeping a schedule is that the app already knows which
        class you are sitting in and which lecture of it this is — typing that
        in by hand every time is the work the schedule exists to remove. When
        a meeting is running now (or starts within the hour) its room and time
        go into the subtitle too."""
        meta = self._long_date() + " · added " + time.strftime("%H:%M")
        here = self._meeting_now(cls)
        if here is not None:
            where = here.get("room") or self.classes[cls].get("room") or ""
            meta = "%s %s-%s%s" % (self._long_date(), here["start"],
                                   here["end"],
                                   " · " + where if where else "")
        return {
            "cls": cls, "num": num,
            "title": "%s %s" % (_t("Lecture"), num),
            "date": self._short_date(),
            "meta": meta,
            "notes": "", "ranges": {}}

    def _meeting_now(self, cls):
        """The meeting of `cls` happening right now, or starting within the
        hour. None when the class does not meet around now."""
        now = time.localtime()
        mins = now.tm_hour * 60 + now.tm_min
        # cls < 0 ("not tied to a class") must be rejected BEFORE indexing:
        # self.classes[-1] is the last class, so IndexError never fires and the
        # caller would get somebody else's timetable. Same trap as _class_label.
        try:
            if int(cls) < 0:
                return None
            meets = self.classes[int(cls)].get("meets", [])
        except (IndexError, TypeError, ValueError, AttributeError):
            return None
        for m in meets:
            if m["day"] != now.tm_wday:
                continue
            start, end = _minutes(m["start"]), _minutes(m["end"])
            if start is None or end is None:
                continue
            if start - 60 <= mins <= end:
                return m
        return None

    def _class_meeting_now(self):
        """The class index whose meeting is running now (or starts within the
        hour), else None — what a new lecture should default to."""
        for ci in range(len(self.classes)):
            if self._meeting_now(ci) is not None:
                return ci
        return None

    def _new_lecture(self):
        if not self.classes:
            self._new_class()
            return
        self.undo.checkpoint("New Lecture")
        # The class that is meeting right now wins over whatever was last open:
        # if you open the app in a lecture, that is the lecture you are taking.
        cls = self._class_meeting_now()
        if cls is None:
            cls = self.lectures[self.active]["cls"] if self.active >= 0 else 0
        # Flush the outgoing lecture's note text + formatting before we switch.
        self._capture_active()
        # A blank lecture matches no search, so drop the filter first or the row
        # about to be created would not be in the list at all.
        self._clear_search()
        self.lectures.append(self._blank_lecture(cls, self._next_num(cls)))
        self.active = len(self.lectures) - 1
        self._refresh_sidebar()
        self._refresh_canvas()
        self.undo.commit()
        # Land the cursor in the title so she can name the new lecture at once.
        self._focus_title()

    def _new_class(self):
        # Flush the outgoing lecture's note text + formatting before we switch.
        self._capture_active()
        self.undo.checkpoint("New Class")
        self._clear_search()
        ci = self._append_class()
        self.lectures.append(self._blank_lecture(ci, "01"))
        self.active = len(self.lectures) - 1
        self._refresh_sidebar()
        self._refresh_canvas()
        self.undo.commit()
        self._focus_title()

    def _rename_class(self):
        # Rename the active class — it was frozen at "Untitled Class N" with no
        # way to change it (only lecture titles were editable).
        if self.active < 0 or not self.lectures:
            return
        # Resolved and CHECKED, not indexed. This one mutates the record it
        # finds, so the empty-dict fallback _class_of returns would be worse
        # than a crash: the rename would land in a throwaway dict and the app
        # would report success having changed nothing.
        ci = self.lectures[self.active].get("cls", -1)
        if not 0 <= ci < len(self.classes):
            return
        cl = self.classes[ci]
        name = self._name_dialog("Rename Class", "Rename",
                                 text=cl.get("label", ""))
        if not name or name == cl.get("label", ""):
            return
        self.undo.checkpoint("Rename Class")
        cl["label"] = name
        self._refresh_sidebar()
        # Flush the live buffer (note text + tag ranges) before the canvas
        # rebuilds; otherwise just-applied edits are dropped.
        self._capture_active()
        self._refresh_canvas()
        try:
            self._save_to_disk()
        except Exception:
            pass
        self.undo.commit()

    def _dialog_shell(self, title):
        """An undecorated papertone dialog card with a heading — the pattern the
        rest of the OS uses (journal, cookbook), in place of a stock GTK dialog
        wearing a window-manager title bar. Content goes in dlg._box."""
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("acdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.get_style_context().add_class("acdlgbox")
        hd = Gtk.Label(label=_t(title), xalign=0)
        hd.get_style_context().add_class("acdlgtitle")
        box.pack_start(hd, False, False, 0)
        area.add(box)
        dlg._box = box
        return dlg

    def _dialog_buttons(self, dlg, ok_label, destructive=True,
                        remove_label=None, default_ok=True):
        """Cancel + <ok_label> row for a _dialog_shell card, with an optional
        Remove on the left for the dialogs that edit an existing thing. A
        destructive action takes the signage red; an ordinary primary (Rename)
        takes dark ink — red is reserved for alerts.

        The confirming button is made the dialog's DEFAULT so Return finishes
        the form: without it, Enter in a text field did nothing at all and the
        keyboard route through these dialogs dead-ended. A confirm-a-delete
        card passes default_ok=False, so there Return still means Cancel."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("acdlgcancel")
        cancel.connect("clicked",
                       lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        ok = Gtk.Button(label=_t(ok_label))
        ok.get_style_context().add_class(
            "acdlgok" if destructive else "acdlgprimary")
        ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        if remove_label:
            rm = Gtk.Button(label=_t(remove_label))
            rm.get_style_context().add_class("acdlgremove")
            rm.connect("clicked",
                       lambda *_: dlg.response(Gtk.ResponseType.REJECT))
            row.pack_start(rm, False, False, 0)
        # pack_end reverses, so this puts Cancel then the primary at the right.
        row.pack_end(ok, False, False, 0)
        row.pack_end(cancel, False, False, 0)
        dlg._box.pack_start(row, False, False, 0)
        dlg._cancel = cancel
        dflt = ok if default_ok else cancel
        dflt.set_can_default(True)
        try:
            dlg.set_default(dflt)
        except Exception:
            pass
        return ok

    def _name_dialog(self, title, ok_label, text="", placeholder=""):
        """Ask for one short name (a class). Returns the trimmed text, or None
        if it was cancelled or left blank."""
        dlg = self._dialog_shell(title)
        entry = Gtk.Entry()
        entry.set_text(text)
        if placeholder:
            entry.set_placeholder_text(placeholder)
        entry.set_activates_default(True)
        entry.set_max_length(60)
        entry.set_size_request(280, -1)
        entry.get_style_context().add_class("acdlgentry")
        dlg._box.pack_start(entry, False, False, 0)
        self._dialog_buttons(dlg, ok_label, destructive=False)
        dlg.show_all()
        entry.grab_focus()
        entry.select_region(0, -1)
        name = entry.get_text().strip() \
            if dlg.run() == Gtk.ResponseType.OK else ""
        dlg.destroy()
        return name or None

    def _confirm(self, heading, detail, ok_label="Delete"):
        """Modal warning confirm; returns True only if the user chose the
        confirming action. The default response is Cancel so an accidental
        Enter never deletes. `ok_label` matches the button to the heading — a
        card that asks "Remove this class time?" should not answer "Delete".
        """
        dlg = self._dialog_shell(heading)
        msg = Gtk.Label(label=detail, xalign=0)
        msg.set_line_wrap(True)
        # width-chars sets the card's measure (max-width-chars alone only caps
        # it, leaving GTK free to size the dialog to a cramped ~25 characters).
        msg.set_width_chars(38)
        msg.set_max_width_chars(40)
        msg.get_style_context().add_class("acdlgmsg")
        dlg._box.pack_start(msg, False, False, 0)
        self._dialog_buttons(dlg, ok_label, default_ok=False)
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        dlg.show_all()
        dlg._cancel.grab_focus()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _delete_lecture(self):
        """Remove the active lecture after a confirm, then re-point the
        selection at the nearest remaining lecture (or the empty state)."""
        if not (0 <= self.active < len(self.lectures)):
            return
        lec = self.lectures[self.active]
        cl = self._class_of(lec)
        # If this is the class's only lecture, deleting it empties the class —
        # and an empty class can't hold or gain lectures, so it's removed too.
        # Say so up front rather than let a ghost class header appear.
        last_in_class = sum(1 for l in self.lectures
                            if l["cls"] == lec["cls"]) == 1
        # Undoable now, and the confirm says so: a warning that a delete is
        # permanent sends a student looking for a backup that does not exist.
        # Wrapped in _t() as well — the catalogs have carried these sentences
        # all along, and nothing ever applied them.
        if last_in_class:
            detail = (_t("“%s” (Lecture %s) is the only lecture in %s, so the "
                         "class is removed too.")
                      % (lec.get("title") or "Untitled Lecture",
                         lec.get("num", ""), cl.get("label", "")))
        else:
            detail = (_t("“%s” (Lecture %s · %s) will be removed.")
                      % (lec.get("title") or "Untitled Lecture",
                         lec.get("num", ""), cl.get("label", "")))
        if not self._confirm("Delete this lecture?", detail):
            return
        self._may_empty = True
        self.undo.checkpoint("Delete Lecture")
        # The outgoing lecture is being discarded, so just drop any pending
        # debounce rather than flushing it back into a row we're deleting.
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
            self._notes_timer = None
        del self.lectures[self.active]
        # Drop any class this left with no lectures (reindexes cls fields) so no
        # stranded, un-addable class header lingers in the sidebar.
        self._prune_empty_classes()
        # Deleting shifts every later lecture down one, so the old index now
        # points at the following lecture; clamp into range (-1 when empty).
        self.active = min(self.active, len(self.lectures) - 1)
        self._refresh_sidebar()
        self._refresh_canvas()
        try:
            self._save_to_disk()
        except Exception:
            pass
        self.undo.commit()

    def _prune_empty_classes(self):
        """Renumber `cls` indices after a class is removed. Nothing is pruned.

        This used to DELETE any class nothing referred to, and it destroyed the
        first thing every student does. Create a class -- no lectures yet, no
        timetable yet, no homework yet -- press Esc, reopen, and it was gone,
        silently, with no confirm and no undo. It looked like the app was
        eating entries because it was.

        The rule made sense when a class existed only to hold lectures: an
        empty one was a dead sidebar header. It stopped making sense the moment
        a class became a thing in its own right, with a room, an instructor, a
        timetable and assignments. A class the user made is theirs until they
        delete it, and File > Delete Class is how that happens.

        Kept under the old name because the call sites want exactly what it
        still does: keep every lecture's and assignment's `cls` pointing at the
        class it actually belongs to.

        AND IT PRUNED NOTHING EXCEPT LECTURES. The line that read

            self.lectures = [l for l in self.lectures if 0 <= l["cls"] < n]

        silently DELETED every lecture whose class index was out of range —
        while the very next loop merely untied an assignment in exactly the
        same state. Measured side by side: one orphaned lecture in, zero out,
        its text gone from the model; one orphaned assignment in, one out. That
        is backwards. A note is the one thing in this file that cannot be
        re-derived, which is why the loader parks orphans under a recovery class
        rather than dropping them, and why the docstring above says nothing is
        pruned. Both call sites happen to hand this method in-range indices
        today, so the filter never fired — a dormant lecture-shredder sitting
        behind a comment promising it does not exist, which is how the last two
        rounds of this bug got in. Orphans are now parked, exactly as the loader
        parks them.
        """
        n = len(self.classes)
        orphans = [l for l in self.lectures if not 0 <= l.get("cls", -1) < n]
        if orphans:
            self.classes.append({"label": _t("Recovered notes"),
                                 "color": CLASS_COLORS[0], "room": "",
                                 "instructor": "", "meets": []})
            recovered = len(self.classes) - 1
            for l in orphans:
                l["cls"] = recovered
        for h in self.homework:
            if not 0 <= h.get("cls", -1) < n:
                h["cls"] = -1          # an assignment can outlive its class

    def _current_class(self):
        """The class the menu acts on: the open lecture's, or the only class
        there is. Returns -1 when that is genuinely ambiguous, in which case
        the sidebar (where every class is listed) is the way in."""
        if 0 <= self.active < len(self.lectures):
            ci = self.lectures[self.active].get("cls", -1)
            if 0 <= ci < len(self.classes):
                return ci
        return 0 if len(self.classes) == 1 else -1

    def _move_lecture(self):
        """Re-file the open lecture under a different class.

        A lecture's class was chosen once, at creation, and could never be
        changed afterwards — and _new_lecture GUESSES it from the timetable
        (whichever class meets now, or next). A note taken in a free period, or
        in the wrong room, or just before the hour, was filed under the wrong
        class permanently. An ASSIGNMENT has had a class combo in its dialog all
        along; the thing you actually write during a lecture had nothing. Same
        shape as `room` and `instructor` living in the schema with no UI: the
        model could express it and the interface could not."""
        if not (0 <= self.active < len(self.lectures)) or len(self.classes) < 2:
            return
        lec = self.lectures[self.active]
        was = lec.get("cls", -1)
        dlg = self._dialog_shell(_t("Move to class"))
        combo = self._class_combo(was)
        dlg._box.pack_start(_field(_t("Class"), combo), False, False, 0)
        self._dialog_buttons(dlg, _t("Move"), destructive=False)
        dlg.show_all()
        resp = dlg.run()
        ci = self._combo_class_index(combo)
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or ci == was:
            return
        if not 0 <= ci < len(self.classes):
            return
        # Flush the live buffer before the model moves under it, exactly as the
        # class-delete path does — the capture only lands in the right record
        # while `active` still points at the lecture the buffer belongs to.
        self._capture_active()
        self.undo.checkpoint("Move Lecture")
        # Take the next number in the DESTINATION class, computed before the
        # move so this lecture is not counted against itself. Without it a
        # class can end up with two "01"s, and the number is how the sidebar
        # tells one lecture of a class from another.
        lec["num"] = self._next_num(ci)
        lec["cls"] = ci
        self._save_to_disk()
        self.undo.commit()
        self._refresh_sidebar()
        self._refresh_canvas()

    def _edit_current_class(self):
        ci = self._current_class()
        if ci < 0:
            self._flash("Select a class in the sidebar.")
            return
        self._edit_class(ci)

    def _delete_class(self):
        """File ▸ Delete Class. Works out WHICH class is meant, then hands to
        the one implementation.

        This used to carry its own copy of the deletion, and that copy shifted
        every LECTURE's class index down past the hole but not the HOMEWORK's
        -- so deleting a class quietly re-tagged your assignments to whichever
        class slid into the freed slot."""
        ci = self._current_class()
        if ci < 0:
            self._flash("Select a class in the sidebar.")
            return
        self._delete_class_at(ci)

    def _set_active_row(self, index):
        """Move the sidebar's selection highlight to `index` IN PLACE.

        Returns False when the row is not on screen (another view, or filtered
        out by a search), in which case the caller has to do the full rebuild.

        THE COST THIS EXISTS FOR: selecting a lecture called _refresh_sidebar,
        which destroys and reconstructs every class header and every lecture row
        in the notebook. Measured on a term of 24 classes and 600 lectures — a
        four-year degree kept in one file, which is exactly what this app invites
        — that is 375ms of widget construction ON EVERY CLICK, to change which
        row carries one CSS class. Clicking through a list of notes should not
        cost a third of a second a note."""
        rows = getattr(self, "_lec_rows", None)
        if not rows or index not in rows:
            return False
        old = rows.get(self.active)
        if old is not None:
            old[0].get_style_context().remove_class("active")
            old[1].get_style_context().remove_class("active")
        self.active = index
        row, num, title = rows[index]
        row.get_style_context().add_class("active")
        num.get_style_context().add_class("active")
        # _on_title_changed writes through this handle, so it has to follow the
        # selection or per-keystroke title edits update the wrong row.
        self._active_title_label = title
        return True

    def _select(self, i):
        if i == self.active:
            return
        # Flush the outgoing lecture (note text + tag ranges) before the canvas
        # rebuilds; otherwise set_text on the new lecture drops the old edits.
        # This must run while `active` still points at the outgoing lecture.
        self._capture_active()
        if not self._set_active_row(i):
            self.active = i
            self._refresh_sidebar()
        self._refresh_canvas()

    def _on_title_changed(self, entry):
        if self.active >= 0:
            new = entry.get_text()
            # Only act on a real change: the canvas-rebuild set_text fires
            # "changed" with new == stored, so skipping keeps this cheap and
            # avoids a redundant sidebar rebuild during canvas construction.
            if new != self.lectures[self.active]["title"]:
                self.lectures[self.active]["title"] = new
                # Update the active row's title label in place instead of
                # rebuilding the whole sidebar on every keystroke — the full
                # O(classes×lectures) rebuild + show_all() caused visible typing
                # lag on the software-rendered VM. Structural changes (new/
                # delete/select/rename) still go through _refresh_sidebar, which
                # re-captures this handle.
                if self._active_title_label is not None:
                    # Mirror the sidebar's empty-title fallback so clearing the
                    # title shows "Untitled Lecture" there, not a blank row.
                    self._active_title_label.set_text(new or "Untitled Lecture")
            self._mark_editing()

    def _on_notes_changed(self, buf):
        # Keep the per-keystroke path cheap: schedule ONE debounced buffer read
        # (note-text sync + live word count) instead of scanning the whole
        # buffer twice on every keypress (once to store notes, once to recount).
        # The save-state indicator still flips immediately so typing stays live.
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
        self._notes_timer = GLib.timeout_add(150, self._flush_notes)
        self._mark_editing()

    def _flush_notes(self):
        self._notes_timer = None
        self._sync_notes()
        return False

    def _sync_notes(self):
        """One buffer read: store the note text into the active lecture and
        refresh the live word count. Shared by the debounce and every flush
        point (lecture switch / save / export) so a note is never left stale."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.lectures[self.active]["notes"] = txt
        self.wordlbl.set_text(self._wordcount_text(txt))

    def _capture_active(self):
        """Flush the live buffer (note text + tag ranges) into the active
        lecture and cancel any pending notes debounce, so switching lectures or
        saving never loses keystrokes typed inside the debounce window."""
        if self._notes_timer:
            GLib.source_remove(self._notes_timer)
            self._notes_timer = None
        self._sync_notes()
        self._capture_ranges()

    def _toggle_tag(self, name):
        """Toggle a character tag (bold / italic / highlight) over the current
        selection: remove it if the whole run already carries it, otherwise
        apply it — so B / I un-format as well as format, like every editor.
        With no selection there is nothing to format, so just return focus to
        the note (never silently do the wrong thing)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        bounds = buf.get_selection_bounds()
        if not bounds:
            self.body.grab_focus()
            return
        start, end = bounds
        tag = buf.get_tag_table().lookup(name)
        if tag is None:
            return
        it = start.copy()
        fully = True
        while it.compare(end) < 0:
            if not it.has_tag(tag):
                fully = False
                break
            it.forward_char()
        self.undo.checkpoint("Formatting")
        if fully:
            buf.remove_tag(tag, start, end)
        else:
            buf.apply_tag(tag, start, end)
        # A tag change fires no "changed" signal, so flip the save state (and
        # schedule the disk write) here or the formatting looks unsaved / is
        # only persisted on the next text edit.
        self._mark_editing()
        self.undo.commit()

    _STYLE_ORDER = ("Body", "Heading", "Subheading")

    def _line_style(self, it):
        tbl = self.body.get_buffer().get_tag_table()
        for name, label in (("heading", "Heading"), ("subheading", "Subheading")):
            tag = tbl.lookup(name)
            if tag is not None and it.has_tag(tag):
                return label
        return "Body"

    def _sync_style_label(self):
        """Set the Style indicator to the paragraph style at the caret, so the
        toolbar always names what the writer is standing in."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        start = buf.get_iter_at_line(
            buf.get_iter_at_mark(buf.get_insert()).get_line())
        label = self._line_style(start)
        if self.stylelbl.get_text() != label:
            self.stylelbl.set_text(label)

    def _on_mark_set(self, buf, _it, mark):
        # Only the insertion caret drives the Style indicator.
        if mark is buf.get_insert():
            self._sync_style_label()

    def _cycle_style(self):
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        ins = buf.get_iter_at_mark(buf.get_insert())
        start = buf.get_iter_at_line(ins.get_line())
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        cur = self._line_style(start)
        nxt = self._STYLE_ORDER[
            (self._STYLE_ORDER.index(cur) + 1) % len(self._STYLE_ORDER)]
        buf.remove_tag_by_name("heading", start, end)
        buf.remove_tag_by_name("subheading", start, end)
        if nxt == "Heading":
            buf.apply_tag_by_name("heading", start, end)
        elif nxt == "Subheading":
            buf.apply_tag_by_name("subheading", start, end)
        self.stylelbl.set_text(nxt)
        self.body.grab_focus()
        self._mark_editing()

    def _insert_list(self, prefix):
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        it = buf.get_iter_at_mark(buf.get_insert())
        it.set_line_offset(0)
        # Numbered list: continue the sequence from the previous line instead
        # of always inserting "1. " (which produced "1. 1. 1.").
        if prefix.strip().rstrip(".").isdigit():
            n, line = 1, it.get_line()
            if line > 0:
                prev = buf.get_iter_at_line(line - 1)
                pend = prev.copy(); pend.forward_to_line_end()
                head = buf.get_text(prev, pend, False).lstrip().split(".", 1)[0]
                if head.strip().isdigit():
                    n = int(head.strip()) + 1
            prefix = "%d. " % n
        buf.insert(it, prefix)
        self.body.grab_focus()

    @staticmethod
    def _wordcount_text(txt):
        stripped = txt.strip()
        n = len(stripped.split()) if stripped else 0
        return "%d word%s" % (n, "" if n == 1 else "s")

    def _recount(self):
        if not hasattr(self, "body"):
            self.wordlbl.set_text("0 words")
            return
        buf = self.body.get_buffer()
        txt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.wordlbl.set_text(self._wordcount_text(txt))

    def _mark_editing(self):
        # One undo step per burst of typing. _mark_editing is reached by every
        # content change — the note, the title, a tag toggle, a style change —
        # and only re-arms a timer, so it costs nothing per keystroke.
        self.undo.touch()
        self._saved = False
        self.savelbl.set_text("Saving…")
        self.savedot.queue_draw()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add(900, self._mark_saved)

    def _mark_saved(self):
        # The debounce has settled: this is where the real disk write happens,
        # so the "Saved HH:MM" indicator only lights green after a genuine save.
        if self._save_to_disk():
            self._saved = True
            self.savelbl.set_text("Saved %s" % time.strftime("%H:%M"))
        else:
            # I/O failed — don't claim "Saved"; leave the dot red so the state
            # is honest, but never crash the app over a disk error.
            self._saved = False
            self.savelbl.set_text("Saving…")
        self.savedot.queue_draw()
        self._save_timer = None
        return False

    # ---------------- persistence ----------------
    @staticmethod
    def _valid_hex(c):
        """True only for a '#RRGGBB' string nbicons._hex can parse, so a
        foreign / hand-edited colour never reaches a swatch draw."""
        if not isinstance(c, str) or len(c) != 7 or c[0] != "#":
            return False
        try:
            int(c[1:], 16)
            return True
        except ValueError:
            return False

    def _load_from_disk(self):
        """Restore classes + lectures from ACADEMICS_FILE.

        Validates the shape defensively: any missing/malformed/foreign data
        leaves the empty default in place so the app still opens exactly as a
        fresh install (no classes) does.
        """
        path = ACADEMICS_FILE
        if not os.path.exists(path) and os.path.exists(LEGACY_FILE):
            path = LEGACY_FILE          # opened under the app's old name
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception:
            # Not JSON at all. Quarantine NOW, at the moment the damage is
            # discovered, rather than leaving it to whatever writes next: the
            # empty-model guard in _save_to_disk (correctly) refuses to write
            # over a non-empty file, which meant nothing ever moved this aside
            # and the app opened blank forever with no way back. Moving it to
            # <name>.damaged-<stamp> keeps every byte and lets the app start.
            self._quarantined = nbapp.preserve_damaged(path)
            return
        if not isinstance(data, dict):
            self._quarantined = nbapp.preserve_damaged(path)
            return
        # A missing/unusable section costs THAT section, not the file. Rejecting
        # the whole store here is what destroyed terms: the app opened blank
        # and the next save wrote that blankness over the real data. A section
        # stored as an OBJECT still holds the user's records in its values, so
        # read them (see _records) rather than calling the section empty.
        damaged = False
        # Anything at the top of the file this version has no idea about. The
        # loader normalises every record to a fixed schema and the next save
        # writes that normalisation back over the file, so a key nobody here
        # recognises was being DELETED by the mere act of opening the app. That
        # is fine while this is the only program that writes the file and the
        # schema never changes — and neither of those stays true. A store the
        # app cannot fully understand must not come out smaller than it went in,
        # so what is not understood is carried, untouched, to the next save.
        self._extra_top = {k: v for k, v in data.items()
                           if k not in ("classes", "lectures", "homework",
                                        "active")}
        raw_classes = _records(data.get("classes"))
        raw_lectures = _records(data.get("lectures"))
        if not isinstance(data.get("classes"), list):
            damaged = True
        if not isinstance(data.get("lectures"), list):
            damaged = True

        classes = []
        # Where each class in the FILE ended up in `classes`.
        #
        # THE BUG THIS EXISTS FOR: skipping a malformed class record closes the
        # gap it left, so every class after it moves DOWN one index — while the
        # lectures and assignments that refer to those classes still hold the
        # file's numbering. With a bad record at index 1, a lecture belonging to
        # the class at index 2 was read as pointing one past the end of a
        # two-class list, filed under the FIRST class, and its assignment was
        # untied from any class at all. Measured: a term's chemistry notes
        # appeared under maths, and nothing said so. This is the same
        # index-aliasing wound as the one _delete_class paid for, arriving by a
        # different road — and the damage suite could not see it, because it
        # counts records and never asks which class they belong to.
        class_at = {}
        for fi, c in enumerate(raw_classes):
            if not isinstance(c, dict):
                damaged = True
                continue          # skip this one, keep the rest of the term
            class_at[fi] = len(classes)
            color = c.get("color")
            # Start from what was IN THE FILE so a field this version does not
            # know about is carried through instead of being deleted by the next
            # save — see the note on `keep` in _load_from_disk. The known fields
            # below then overwrite their normalised forms on top.
            rec = dict(c)
            rec.update({
                "label": str(c.get("label") or c.get("name")
                             or "Untitled Class"),
                "color": color if self._valid_hex(color) else CLASS_COLORS[0],
                "room": str(c.get("room") or ""),
                "instructor": str(c.get("instructor") or ""),
                # A malformed meeting is SKIPPED, not fatal. The whole-file
                # bail-out above is right for the notes (a half-restored
                # notebook is worse than none) but not for a timetable: one
                # bad time must not cost a term of lecture notes.
                "meets": self._clean_meets(c.get("meets")),
            })
            classes.append(rec)

        lectures = []
        for lec in raw_lectures:
            if not isinstance(lec, dict):
                damaged = True
                continue
            try:
                cls = int(lec.get("cls", 0))
            except (TypeError, ValueError):
                damaged = True
                cls = -1
            else:
                # Follow the class through any shift the skip above caused. With
                # nothing skipped this map is the identity, so a healthy file
                # behaves exactly as before.
                cls = class_at.get(cls, -1) if cls >= 0 else -1
            if cls < 0 or cls >= len(classes):
                # The note survives; it just loses the class it pointed at,
                # exactly as an assignment does. Discarding it (let alone the
                # whole file) would throw away the one thing that cannot be
                # re-derived: what somebody actually wrote.
                #
                # -1 MEANS "no class yet", NOT "the last one", and it must not
                # mean "delete this". The old code dropped the lecture whenever
                # the class list came back empty, so a `classes` section this
                # loader could not read cost every lecture note in the file on
                # the very next save. Orphans are parked below a recovery class
                # instead, so the writing is always still there to re-file.
                damaged = True
                cls = 0 if classes else -1
            # Tolerate old files that saved plain text with no formatting ranges.
            raw_ranges = lec.get("ranges")
            rec = dict(lec)          # carry anything this version does not know
            rec.update({
                "cls": cls,
                "num": str(lec.get("num", "01")),
                "title": str(lec.get("title", "")),
                "date": str(lec.get("date", self._short_date())),
                "meta": str(lec.get("meta", "")),
                "notes": str(lec.get("notes", "")),
                "ranges": raw_ranges if isinstance(raw_ranges, dict) else {},
            })
            lectures.append(rec)

        # Notes that ended up with no class at all (the store had none this
        # loader could read) get one, so they are visible, editable and can be
        # re-filed. Every other route out of here deleted them.
        if any(lec["cls"] < 0 for lec in lectures):
            classes.append({"label": _t("Recovered notes"),
                            "color": CLASS_COLORS[0], "room": "",
                            "instructor": "", "meets": []})
            recovered = len(classes) - 1
            for lec in lectures:
                if lec["cls"] < 0:
                    lec["cls"] = recovered

        # Adopt what could be read. `_damaged` is remembered so the save path
        # can protect whatever it could NOT read (see _save_to_disk).
        self._damaged = damaged
        self.classes = classes
        self.lectures = lectures
        self.homework = self._clean_homework(data.get("homework"), class_at)
        try:
            active = int(data.get("active", -1))
        except (TypeError, ValueError):
            active = -1
        if 0 <= active < len(lectures):
            self.active = active
        elif lectures:
            self.active = 0
        else:
            self.active = -1
        # A hand-edited / foreign file can carry a class with no lectures, which
        # would render as an un-addable ghost header; drop those on load so the
        # restored notebook obeys the same one-lecture-minimum invariant we keep.
        self._prune_empty_classes()

    def _save_to_disk(self):
        """Persist the full editable model. Returns True on success.

        Wrapped so a disk error can never crash the app; the caller decides
        whether to show the "Saved" state from the return value.
        """
        # Pull the live buffer's note text + formatting into the model so both
        # persist even if the notes debounce hasn't fired yet.
        self._capture_active()
        # Top-level keys this version does not recognise go back exactly as they
        # came in, underneath everything it does (so they can never shadow a
        # real section). See _extra_top in _load_from_disk.
        data = dict(getattr(self, "_extra_top", None) or {})
        data.update({
            # "name" alongside "label" because the desktop board reads this
            # file too, and a tile should not have to know this app's private
            # spelling for the thing a class is called.
            "classes": [dict(c, name=c.get("label", "")) for c in self.classes],
            "lectures": [dict(lec) for lec in self.lectures],
            "homework": [dict(h, course=self._class_label(h.get("cls")))
                         for h in self.homework],
            "active": self.active,
        })
        # LAST-RESORT GUARD. An empty model must never be allowed to erase a
        # file that still has something in it. Every legitimate route to empty
        # goes through a delete the user asked for and confirmed, and those set
        # `_may_empty` first; anything else reaching this point with nothing to
        # say is a bug somewhere upstream (a loader that gave up, a refresh
        # that ran too early), and the correct response to a bug is to keep the
        # user's work rather than to write our confusion over it.
        if not (self.classes or self.lectures or self.homework) \
                and not getattr(self, "_may_empty", False):
            try:
                if os.path.getsize(ACADEMICS_FILE) > 2:
                    return False
            except OSError:
                pass
        try:
            nbapp.atomic_write_json(ACADEMICS_FILE, data)
            self._save_warned = False
            return True
        except Exception as exc:
            # NEVER fail silently. Every caller here ignores the return value,
            # so without this a full disk or a read-only filesystem looks
            # exactly like "Academics deleted my classes": the file keeps the
            # last write that worked, and everything entered afterwards is gone
            # the moment the app closes. Warn once per run of failures so a
            # jammed disk does not strobe the status line.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash(nbapp.save_failure_reason(exc, ACADEMICS_FILE))
                except Exception:
                    pass
            return False

    # ---------------- undo / redo ----------------
    # The snapshot is the same model the autosave writes: every class, every
    # lecture's title / notes / formatting, and the selection. One mechanism
    # therefore reverses typing, a deleted lecture and a deleted class alike.
    def _undo_snapshot(self):
        self._capture_active()          # fold the live buffer into the model
        return {"classes": self._copy_classes(self.classes),
                "lectures": self._copy_lectures(self.lectures),
                # The timetable and the homework list are part of the term too.
                # They were left out of the snapshot, so Ctrl+Z after clearing
                # finished homework silently kept the deletion — and worse,
                # restoring an OLDER snapshot pasted its stale class list over
                # a homework list that still pointed into the new one.
                "homework": [dict(h) for h in self.homework],
                "active": self.active,
                "_caret": self._caret_offset()}

    @staticmethod
    def _copy_classes(classes):
        """Fresh dicts AND fresh meeting lists per class. `dict(c)` alone left
        every snapshot sharing one live "meets" list, so adding a class time
        rewrote the history that was supposed to undo it."""
        out = []
        for c in classes:
            copy = dict(c)
            copy["meets"] = [dict(m) for m in (c.get("meets") or [])]
            out.append(copy)
        return out

    @staticmethod
    def _copy_lectures(lectures):
        """Fresh dicts (and fresh range lists) per lecture, so a snapshot can
        never be edited from under itself by the next _capture_active. The
        note text inside is an immutable string and is shared, which is what
        keeps a full notebook's history small."""
        out = []
        for lec in lectures:
            copy = dict(lec)
            copy["ranges"] = {k: [list(sp) for sp in v]
                              for k, v in (lec.get("ranges") or {}).items()}
            out.append(copy)
        return out

    def _caret_offset(self):
        try:
            buf = self.body.get_buffer()
            return buf.get_iter_at_mark(buf.get_insert()).get_offset()
        except Exception:
            return 0

    def _undo_restore(self, state):
        self.classes = self._copy_classes(state["classes"])
        self.lectures = self._copy_lectures(state["lectures"])
        self.homework = [dict(h) for h in state.get("homework") or []]
        self.active = state["active"]
        self._sel_block = -1
        self._clear_search()       # a filter can hide the row we just restored
        self._refresh_sidebar()
        self._refresh_canvas()     # rebuilds the title field and the note view
        # The view being looked at has to redraw too, or an undo taken on the
        # timetable or the homework list appears to have done nothing.
        self._refresh_schedule()
        self._refresh_homework()
        try:
            buf = self.body.get_buffer()
            caret = min(max(0, state.get("_caret", 0)), buf.get_char_count())
            buf.place_cursor(buf.get_iter_at_offset(caret))
            self.body.grab_focus()
        except Exception:
            pass
        self._save_to_disk()

    def _on_destroy(self, *_a):
        """Flush a final save on window close so the last edit isn't lost."""
        self.undo.cancel()
        for attr in ("_save_timer", "_notes_timer", "_filter_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    GLib.source_remove(tid)
                except Exception:
                    pass
                setattr(self, attr, None)
        # _save_to_disk -> _capture_active still pulls the live buffer in, so a
        # keystroke typed inside the debounce window is persisted on close.
        # Wrapped: _capture_active touches the live GtkTextBuffer, and a widget
        # already part-way through teardown can raise there. Unwrapped, that
        # exception skipped the final save entirely -- the one save whose whole
        # job is to not lose the last thing the user did.
        try:
            self._save_to_disk()
        except Exception:
            pass
        return False

    # ---------------- File menu: export active lecture to PDF -----------------
    # academics.json stays the sole source of truth (autosaved on every edit).
    # The File menu offers a one-way render of the ACTIVE lecture — its
    # class/lecture eyebrow, title, meta and note body (heading spans honoured)
    # — to a paginated PDF under $NB_HOME/Documents. No file open/save.
    def _class_of(self, lec):
        """The class record a lecture belongs to, or an empty one.

        NEVER `self.classes[lec["cls"]]`. A cls of -1 means "no class", and a
        negative index is the LAST element rather than a miss — so an untied
        lecture silently exported under whichever class happened to be last in
        the list, in the PDF's header AND in its filename. This is the same trap
        _class_label and _class_color are written the way they are to avoid; the
        export path had its own copy of the bug."""
        try:
            cls = int(lec.get("cls", -1))
        except (TypeError, ValueError):
            return {}
        if 0 <= cls < len(self.classes):
            return self.classes[cls]
        return {}

    def _pdf_name(self, lec):
        """A neutral PDF filename derived from the class + lecture title."""
        cl = self._class_of(lec)
        raw = "%s %s" % (cl.get("label", ""), lec.get("title", ""))
        words = "".join(c if c.isalnum() else " " for c in raw).split()
        base = "-".join(words).lower()[:70] if words else "lecture"
        return base + ".pdf"

    def _make_active_pdf(self, path):
        """Write the active lecture to a PDF at `path` — the single renderer
        shared by File ▸ Export to PDF and File ▸ Print. Flushes the live buffer
        first so the output reflects the on-screen note, not the last debounced
        snapshot. Raises if there is no active lecture (callers guard for this)."""
        self._capture_active()
        lec = self.lectures[self.active]
        self._render_pdf(path, lec, self._class_of(lec))

    # Print and Export follow the VIEW you are looking at. They used to render
    # the open lecture note whatever was on screen, so printing from the
    # timetable handed you somebody's chemistry notes -- and on a term with no
    # notes yet, the Schedule could not be printed at all.

    def _print_target(self):
        """(renderer, job name, "nothing to print" message) for the open view."""
        view = getattr(self, "view", "notes")
        if view == "schedule":
            return (self._make_schedule_pdf, "Timetable",
                    "No class times to print")
        if view == "homework":
            return (self._make_homework_pdf, "Homework",
                    "No assignments to print")
        return (self._make_active_pdf, "Lecture", "No lecture to print")

    def _have_to_print(self):
        view = getattr(self, "view", "notes")
        if view == "schedule":
            return bool(self._all_meets())
        if view == "homework":
            return bool(self.homework)
        return 0 <= self.active < len(self.lectures)

    def _print_doc(self, *_a):
        """Print what the open view shows, via the shared themed Print dialog
        and the same renderer Export uses. The no-printer case lives in
        nbprint."""
        render, job, nothing = self._print_target()
        if not self._have_to_print():
            self._flash(nothing)
            return
        nbprint.print_document(self, render, job_name=job)

    def _export_pdf(self, *_a):
        """Render what the open view shows to a PDF under Documents. Reports a
        neutral status line; never crashes on a bad path or a failed write."""
        render, _job, nothing = self._print_target()
        if not self._have_to_print():
            self._flash(nothing.replace("print", "export"))
            return
        view = getattr(self, "view", "notes")
        if view == "schedule":
            name = "timetable.pdf"
        elif view == "homework":
            name = "homework.pdf"
        else:
            name = self._pdf_name(self.lectures[self.active])
        # Every one of these names is deterministic — timetable.pdf and
        # homework.pdf are fixed, and a lecture's is derived from its title — so
        # re-exporting after taking more notes, the usual reason to export
        # twice, lands on the earlier PDF. It used to destroy it without a word.
        # Ask, using the same three strings as Novel's Save As -- one wording
        # for "you are about to overwrite", already carried by all seventeen
        # catalogs. This _confirm is the modal, boolean one; its default is
        # Cancel, so an accidental Return keeps the existing file.
        if os.path.exists(os.path.join(DOCS_DIR, name)) and not self._confirm(
                _t("Replace file?"),
                _t("“%s” already exists in Documents. Replace it?")
                % name,
                _t("Replace")):
            return
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            # Same renderer the Print dialog uses, so the exported and printed
            # PDFs are identical byte-for-byte in layout.
            render(os.path.join(DOCS_DIR, name))
        except Exception:
            self._flash("Export failed")
            return
        # Success. Settle the autosave first (cancel the pending timer and write
        # now) so it can't overwrite this confirmation a moment later, then say
        # where the PDF landed — a novice needs to know it's under Documents.
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = None
        self._saved = self._save_to_disk()
        try:
            self.savedot.queue_draw()
            self.savelbl.set_text("Exported to Documents")
        except Exception:
            pass

    @staticmethod
    def _line_style_at(a, b, ranges):
        """Block style ('heading'/'subheading'/'body') for the char span [a,b),
        from the lecture's stored tag ranges; tolerant of malformed data."""
        if isinstance(ranges, dict):
            for name in ("heading", "subheading"):
                spans = ranges.get(name)
                if not isinstance(spans, list):
                    continue
                for span in spans:
                    try:
                        s, e = int(span[0]), int(span[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if s < b and e > a:
                        return name
        return "body"

    @staticmethod
    def _line_spans(ranges, lo, length):
        """Inline bold / italic / highlight runs for the buffer line starting at
        char offset `lo`, in nbprint.PdfText's (start, end, kind) shape.

        Export used to read only the heading/subheading block styles, so every
        bold word and every highlighted line — the marks a student makes the
        night before an exam — printed as plain body text."""
        spans = []
        if not isinstance(ranges, dict):
            return spans
        for kind in nbprint.SPAN_KINDS:
            for span in ranges.get(kind) or ():
                try:
                    s, e = int(span[0]), int(span[1])
                except (TypeError, ValueError, IndexError):
                    continue
                ls = max(0, s - lo)
                le = min(length, e - lo)
                if le > ls:
                    spans.append((ls, le, kind))
        return spans

    def _make_schedule_pdf(self, path):
        """The week as a printable day-by-day list.

        A list, not a picture of the grid: on paper a reader wants to find
        Wednesday, and a scaled-down screenshot of a timetable is the hardest
        possible way to do that."""
        surf, cr, page = nbprint.report_page(path)
        page.emit(_t("Week").upper(), 9.5, False, "#6E695E", gap_after=6)
        page.emit(_t("Timetable"), 26, True, "#1A1916", gap_after=3)
        page.rule("#D7D2C5")
        for day in range(7):
            meets = [(ci, m) for ci, m in self._all_meets()
                     if m["day"] == day]
            if not meets:
                continue          # a free day needs no heading
            meets.sort(key=lambda p: _minutes(p[1]["start"]))
            page.emit(_t(DAY_NAMES[day]).upper(), 11, True, "#1A1916",
                      gap_before=12, gap_after=4)
            for ci, m in meets:
                cl = self.classes[ci] if 0 <= ci < len(self.classes) else {}
                page.emit("%s-%s   %s" % (m["start"], m["end"],
                                          cl.get("label", "")),
                          11, False, "#1A1916")
                where = m.get("room") or cl.get("room", "")
                who = cl.get("instructor", "")
                sub = " · ".join(x for x in (where, who) if x)
                if sub:
                    page.emit(sub, 9.5, False, "#6E695E", indent=54.0,
                              gap_after=2)
        surf.finish()

    def _make_homework_pdf(self, path):
        """Everything outstanding, then everything finished."""
        surf, cr, page = nbprint.report_page(path)
        todo = [h for h in self.homework if not h.get("done")]
        done = [h for h in self.homework if h.get("done")]
        page.emit(_t("Homework").upper(), 9.5, False, "#6E695E", gap_after=6)
        page.emit(_t("%d to do") % len(todo), 26, True, "#1A1916", gap_after=3)
        page.rule("#D7D2C5")

        def group(heading, items):
            if not items:
                return
            page.emit(_t(heading).upper(), 11, True, "#1A1916",
                      gap_before=12, gap_after=4)
            # Undated work sorts last rather than first: an empty due date is
            # not "due at the dawn of time".
            for h in sorted(items, key=lambda x: (not x.get("due"),
                                                  x.get("due") or "")):
                page.emit(h.get("title", ""), 11, False, "#1A1916")
                # The exam marker goes on paper too. A printed list that quietly
                # drops the distinction the screen makes is the same lie as not
                # having the field.
                bits = [b for b in (
                    _t("Exam") if h.get("kind") == "exam" else "",
                    self._class_label(h.get("cls", -1)),
                    _pretty_due(h.get("due", ""))) if b]
                if h.get("note"):
                    bits.append(h["note"])
                if bits:
                    page.emit(" · ".join(bits), 9.5, False, "#6E695E",
                              indent=14.0, gap_after=2)

        group("To do", todo)
        group("Done", done)
        surf.finish()

    def _render_pdf(self, path, lec, cl):
        """Draw `lec` onto a cairo PDF at `path`, paginating when the cursor
        overflows the page. Serif body + ink palette to match the canvas.

        Laid out with nbprint.PdfText (PangoCairo). The old cairo toy-font
        helpers printed an empty box for every Chinese, Japanese, Korean and
        Devanagari character, and knew nothing about bold or the highlighter."""
        surf, cr, page = nbprint.report_page(path)

        # Header: class/lecture eyebrow, title, meta, then a hairline rule.
        page.emit(("%s · Lecture %s" % (cl.get("label", ""),
                                        lec.get("num", ""))).upper(),
                  9.5, False, "#6E695E", gap_after=6)
        page.emit(lec.get("title", "") or "Untitled Lecture", 26, True,
                  "#1A1916", gap_after=3)
        meta = lec.get("meta", "")
        if meta:
            page.emit(meta, 10, False, "#9A9484", gap_after=6)
        page.rule("#D7D2C5")

        # Body: one buffer line at a time so heading/subheading spans size their
        # whole line. Char offsets are reconstructed exactly as the buffer counts
        # them (each newline is one char), matching _capture_ranges.
        ranges = lec.get("ranges", {})
        off = 0
        for raw in lec.get("notes", "").split("\n"):
            style = self._line_style_at(off, off + len(raw), ranges)
            spans = self._line_spans(ranges, off, len(raw))
            if style == "heading":
                page.emit(raw, 17, True, "#1A1916", gap_before=10, gap_after=2,
                          spans=spans)
            elif style == "subheading":
                page.emit(raw, 13.5, True, "#1A1916", gap_before=7, gap_after=2,
                          spans=spans)
            else:
                page.emit(raw, 11, False, "#1A1916", spans=spans)
            off += len(raw) + 1

        surf.finish()

    def _flash(self, text):
        """Surface a transient status/error line in the save indicator
        (crash-safe; the next edit or successful save resets it)."""
        try:
            self._saved = False
            self.savelbl.set_text(text)
            self.savedot.queue_draw()
        except Exception:
            pass

    # ---------------- menu bar ----------------
    def menu_items(self, name):
        if name == "File":
            # academics.json is the sole source of truth (autosaved on every
            # edit). File offers only the in-memory new/delete-item actions plus
            # a one-way render of the active lecture to a PDF under
            # $NB_HOME/Documents — no file open / save / save-as. The delete
            # actions need an open lecture, so they disable in the empty state.
            have = 0 <= self.active < len(self.lectures)
            # Print / Export name what the OPEN VIEW would produce, and are
            # live whenever that view has something to put on paper -- not
            # only when a lecture note happens to be open.
            view = getattr(self, "view", "notes")
            what = {"schedule": "Timetable", "homework": "Homework"}.get(
                view, "Lecture")
            paper = self._have_to_print()
            return [("New Lecture", self._new_lecture),
                    ("New Class…", self._new_class_only),
                    ("Add a Class Time…", self._add_meeting
                     if self.classes else None),
                    ("Add an Assignment…", self._new_homework),
                    nbapp.SEP,
                    ("Delete Lecture…", self._delete_lecture if have else None),
                    ("Delete Class…", self._delete_class
                     if self.classes else None),
                    nbapp.SEP,
                    ("Export %s to PDF" % what,
                     self._export_pdf if paper else None),
                    ("Print %s…" % what, self._print_doc if paper else None),
                    nbapp.SEP,
                    ("Close    Esc", self.close)]
        if name == "Edit":
            # Base Cut/Copy/Paste/Select All, plus the class-rename action —
            # an in-memory model edit (not a file operation), needing an open
            # lecture so it disables in the empty state.
            # Undo/redo lead the menu, as they do in every editor — and they
            # have to be VISIBLE, not just bound to a key nobody can discover.
            # "Edit Class" replaces the old rename-only item: a class now has a
            # colour, a room and an instructor as well as a name, and it no
            # longer needs an open lecture to be reachable -- a class created
            # from the timetable has none, and used to be uneditable forever.
            have = 0 <= self.active < len(self.lectures)
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit") + [
                nbapp.SEP,
                ("Edit Class…", self._edit_current_class
                 if self.classes else None),
                # Only offered when there is somewhere else to put it: with one
                # class, "Move to Class" can only move a lecture to where it
                # already is, and a live item that cannot change anything is
                # worse than an absent one.
                ("Move to Class…", self._move_lecture
                 if (have and len(self.classes) > 1) else None)]
        if name == "Format":
            # Formatting acts on the open note, so every item disables in the
            # empty state rather than looking live but doing nothing.
            have = 0 <= self.active < len(self.lectures)
            return [("Bold    Ctrl+B",
                     (lambda: self._toggle_tag("bold")) if have else None),
                    ("Italic    Ctrl+I",
                     (lambda: self._toggle_tag("italic")) if have else None),
                    ("Highlight",
                     (lambda: self._toggle_tag("highlight")) if have else None),
                    nbapp.SEP,
                    ("Body Text",
                     (lambda: self._set_style("Body")) if have else None),
                    ("Heading",
                     (lambda: self._set_style("Heading")) if have else None),
                    ("Subheading",
                     (lambda: self._set_style("Subheading")) if have else None),
                    nbapp.SEP,
                    ("Cycle Style", self._cycle_style if have else None)]
        if name == "Insert":
            have = 0 <= self.active < len(self.lectures)
            return [("Bullet List",
                     (lambda: self._insert_list("• ")) if have else None),
                    ("Numbered List",
                     (lambda: self._insert_list("1. ")) if have else None),
                    nbapp.SEP,
                    ("Date",
                     (lambda: self._insert_at_cursor(self._long_date()))
                     if have else None),
                    ("Time",
                     (lambda: self._insert_at_cursor(time.strftime("%H:%M")))
                     if have else None)]
        if name == "View":
            have = 0 <= self.active < len(self.lectures)
            finished = sum(1 for h in self.homework if h["done"])
            return [("Notes", (lambda: self._set_view("notes"))
                     if self.view != "notes" else None),
                    ("Schedule", (lambda: self._set_view("schedule"))
                     if self.view != "schedule" else None),
                    ("Homework", (lambda: self._set_view("homework"))
                     if self.view != "homework" else None),
                    nbapp.SEP,
                    ("Clear Finished Homework",
                     self._delete_homework if finished else None),
                    nbapp.SEP,
                    ("Search Notes    Ctrl+F",
                     self._focus_search if self.lectures else None),
                    ("Show All Lectures",
                     (lambda: self._clear_search()) if self._query else None),
                    nbapp.SEP,
                    ("Previous Lecture",
                     (lambda: self._nav(-1)) if have else None),
                    ("Next Lecture",
                     (lambda: self._nav(1)) if have else None),
                    nbapp.SEP,
                    ("Focus Note", self._focus_note if have else None),
                    ("Refresh Word Count", self._recount if have else None)]
        return super().menu_items(name)

    def _on_key(self, w, ev):
        # Esc drops an active search before it reaches the base handler (which
        # would close the whole app) — the escape a filtered list needs.
        if ev.keyval == Gdk.KEY_Escape and self._clear_search():
            self._focus_note()
            return True
        # Ctrl+F puts the caret in the search field wherever focus happens to be.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self._focus_search()
            return True
        # Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, handled at the window level so they
        # work from the sidebar and the title field too, not only the note.
        if nbapp.undo_keys(self.undo, ev):
            return True
        # Ctrl+B / Ctrl+I toggle the selection's formatting — the shortcuts the
        # toolbar tooltips promise. Only when a lecture is open; Esc / menu keys
        # stay with the base handler. Modal dialogs run their own loops, so their
        # keys are unaffected.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and 0 <= self.active < len(self.lectures)):
            if ev.keyval in (Gdk.KEY_b, Gdk.KEY_B):
                self._toggle_tag("bold")
                return True
            if ev.keyval in (Gdk.KEY_i, Gdk.KEY_I):
                self._toggle_tag("italic")
                return True
        return super()._on_key(w, ev)

    def _set_style(self, target):
        """Set the current line to a specific style (Body/Heading/Subheading)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        buf = self.body.get_buffer()
        ins = buf.get_iter_at_mark(buf.get_insert())
        start = buf.get_iter_at_line(ins.get_line())
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        self.undo.checkpoint("Style")
        buf.remove_tag_by_name("heading", start, end)
        buf.remove_tag_by_name("subheading", start, end)
        if target == "Heading":
            buf.apply_tag_by_name("heading", start, end)
        elif target == "Subheading":
            buf.apply_tag_by_name("subheading", start, end)
        self.stylelbl.set_text(target)
        self.body.grab_focus()
        self._mark_editing()
        self.undo.commit()

    def _insert_at_cursor(self, text):
        """Insert plain text at the note's cursor (no-op if no note open)."""
        if not hasattr(self, "body") or self.active < 0:
            return
        self.body.get_buffer().insert_at_cursor(text)
        self.body.grab_focus()

    def _display_order(self):
        """Lecture indices in the order the sidebar shows them (grouped by
        class), which can differ from raw self.lectures creation order."""
        order = []
        for ci in range(len(self.classes)):
            for li, lec in enumerate(self.lectures):
                if lec["cls"] == ci:
                    order.append(li)
        return order

    def _nav(self, delta):
        """Move selection to the previous/next lecture, clamped in range.

        Navigate in the sidebar's grouped display order — stepping by raw
        creation order made Next jump to a lecture in a different class.
        """
        order = self._display_order()
        if not order:
            return
        cur = self.active if self.active in order else order[0]
        pos = order.index(cur)
        self._select(order[max(0, min(len(order) - 1, pos + delta))])

    def _focus_note(self):
        """Put keyboard focus in the note body (no-op if no note open)."""
        if hasattr(self, "body") and self.active >= 0:
            self.body.grab_focus()

    def _focus_title(self):
        """Swap the heading into edit mode and select it, so a just-created
        lecture can be named immediately and a click lands in the field."""
        if getattr(self, "title", None) is None:
            return
        self.title_ev.hide()
        self.title.show()
        self.title.grab_focus()
        self.title.select_region(0, -1)

    def _show_title_label(self):
        """Back to the read view: the wrapped heading (or a ghost prompt when
        the lecture has no title yet)."""
        if getattr(self, "title", None) is None:
            return
        text = self.title.get_text().strip()
        self.title_lbl.set_text(text or _t("Lecture title"))
        ctx = self.title_lbl.get_style_context()
        (ctx.add_class if not text else ctx.remove_class)("ghost")
        self.title.hide()
        self.title_ev.show()

    def _set_fmt_sensitive(self, have):
        """Enable the format-bar controls only when a lecture is open, so a
        blank canvas never shows a live-looking but inert toolbar."""
        for b in getattr(self, "_fmt_btns", []):
            b.set_sensitive(have)
            if b is getattr(self, "_style_btn", None):
                b.set_tooltip_text(_t("Paragraph style: Body, Heading, Subheading")
                                   if have else _t("Open a lecture to choose a paragraph style."))
            elif b is getattr(self, "_highlight_btn", None):
                b.set_tooltip_text(_t("Highlight") if have else
                                   _t("Open a lecture to highlight text."))
            elif b is getattr(self, "_bullet_btn", None):
                b.set_tooltip_text(_t("Bullet list") if have else
                                   _t("Open a lecture to add a bullet list."))
            elif b is getattr(self, "_number_btn", None):
                b.set_tooltip_text(_t("Numbered list") if have else
                                   _t("Open a lecture to add a numbered list."))

    # ---------------- helpers ----------------
    def _sep(self):
        s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        s.get_style_context().add_class("fsep")
        return s

    def _txtbtn(self, label, cls):
        b = Gtk.Button(label=label)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.get_style_context().add_class(cls)
        return b

    def _iconbtn(self, name):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("fmtbtn")
        b.add(nbicons.image(name, 19, "#1A1916"))
        return b

    def _short_date(self):
        # %-d (no-pad) is a glibc extension; fall back to %d on libcs that
        # reject it (musl/uClibc raise ValueError) so date stamps never crash.
        try:
            return time.strftime("%a %-d %b")
        except ValueError:
            return time.strftime("%a %d %b")

    def _long_date(self):
        try:
            return time.strftime("%A %-d %B %Y")
        except ValueError:
            return time.strftime("%A %d %B %Y")

    # ---------------- style ----------------
    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        /* -- the three-view switcher, drawn as one segmented strip -- */
        .ac-seg { background: #F1EEE6; border-bottom: 1px solid #D7D2C5; }
        .ac-segbtn { background: transparent; border: 0;
                     border-right: 1px solid #D7D2C5; border-radius: 0;
                     padding: 11px 6px; font-size: 13px; color: #6E695E;
                     box-shadow: none; }
        .ac-segbtn:last-child { border-right: 0; }
        .ac-segbtn:hover { background: #EAE3D2; }
        /* The selected view carries the accent as a 2px underline, not a
           filled block: it marks WHERE YOU ARE, which is the same job the
           accent edge does on the Workout app's week strip. */
        .ac-segbtn:checked { background: #FCFBF8; color: #1A1916;
                             font-weight: 700;
                             box-shadow: inset 0 -2px 0 #C8341E; }

        /* -- schedule + homework panes -- */
        .ac-main { background: #FCFBF8; }
        .ac-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .ac-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .ac-sub { font-size: 13px; color: #6E695E; }
        .ac-rule { background: #D7D2C5; }
        .ac-eyebrow { font-size: 11px; letter-spacing: 0.14em;
                      font-weight: 700; color: #9A9484; }
        /* Accent means exactly ONE thing on these screens: this is late. */
        .ac-eyebrow.late { color: #C8341E; }
        .ac-hwrow { padding: 9px 0; }
        .ac-hwtitle { font-size: 15px; color: #1A1916; }
        .ac-hwtitle.done { color: #9A9484; }
        .ac-hwmeta { font-size: 12px; color: #6E695E; }
        .ac-hwmeta.late { color: #C8341E; font-weight: 700; }
        .ac-hwkind { font-size: 11px; color: #4A4638; font-weight: 700;
                     letter-spacing: 0.09em; }
        .ac-hwnote { font-size: 12px; color: #857F71; }
        .ac-hwnote.done { color: #A9A395; }
        .ac-cta { background: #F8F7F2; border: 1px solid #C9C4B6;
                  border-radius: 8px; padding: 7px 16px; font-size: 14px;
                  color: #1A1916; box-shadow: none; }
        .ac-cta:hover { background: #F1EEE6; }
        .ac-quiet { background: transparent; border: 1px solid transparent;
                    border-radius: 8px; padding: 5px 10px; font-size: 13px;
                    color: #6E695E; box-shadow: none; }
        .ac-quiet:hover { background: #F1EEE6; border-color: #D7D2C5; }
        .ac-empty-title { font-size: 17px; color: #1A1916; }
        .ac-empty-body { font-size: 14px; color: #6E695E; }
        .ac-fieldlabel { font-size: 11px; letter-spacing: 0.1em;
                         font-weight: 700; color: #9A9484; }
        .ac-warn { font-size: 12px; color: #C8341E; }

        .sidebar { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        /* No compositor: every scroller/viewport must own an opaque surface or
           it renders solid black on real hardware. */
        .sidebar scrolledwindow, .sidebar viewport,
        .side-list { background: #F1EEE6; }
        .canvaswrap viewport, .canvas { background: #FCFBF8; }
        .sidebar *, .editor * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .side-head { padding: 24px 26px 20px; border-bottom: 1px solid #D7D2C5; }
        .side-eyebrow { font-size: 11px; letter-spacing: 0.16em; color: #9A9484;
                        font-weight: 600; margin-bottom: 8px; }
        .side-term { font-size: 20px; font-weight: 700; color: #1A1916; }
        .acsearch { margin-top: 16px; font-size: 13px; color: #1A1916;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; box-shadow: none; min-height: 30px; }
        .acsearch:focus { border: 1px solid #8A857A; }
        .side-count { font-size: 11px; letter-spacing: 0.1em; color: #9A9484;
                      font-weight: 700; padding: 0 10px; margin: 2px 0 10px; }
        .side-list { padding: 16px 14px; }
        .side-empty { padding: 30px 12px; font-size: 13px; color: #9A9484; }
        .cls-head { padding: 0 10px; margin: 10px 0 9px; }
        /* A class in the Schedule / Homework sidebar. It is a button, so it
           must not look like the OS's raised buttons -- it is a row. */
        .cls-row { padding: 8px 10px; margin: 0; border: none;
                   background: transparent; border-radius: 0; }
        .cls-row:hover { background: #EFEBE0; }
        .cls-rowname { font-size: 13px; color: #1A1916; }
        .cls-rowsub { font-size: 11px; color: #6E695E; }
        /* The colour picker in the class editor: a swatch, with the chosen one
           ringed in ink. set_mode(False) makes these ordinary buttons, which
           would otherwise arrive wearing the full button chrome. */
        .ac-swatchbtn { padding: 3px; min-width: 0; min-height: 0;
                        background: transparent; border: 2px solid transparent;
                        border-radius: 4px; box-shadow: none; }
        .ac-swatchbtn:hover { border-color: #C9C4B6; }
        .ac-swatchbtn:checked { border-color: #1A1916; background: transparent; }
        .cls-label { font-size: 11px; letter-spacing: 0.1em; color: #6E695E;
                     font-weight: 700; }
        .lec-row { padding: 10px 10px; margin-bottom: 2px; border-radius: 6px;
                   background: transparent; border: none; box-shadow: none; }
        .lec-row:hover { background: #F0EADC; }
        .lec-row.active { background: #EAE3D2; box-shadow: inset 3px 0 0 #C8341E; }
        .lec-num { min-width: 30px; min-height: 24px; padding: 0 6px;
                   font-size: 12px; border-radius: 4px; color: #9A9484;
                   border: 1px solid #D7D2C5; }
        .lec-num.active { background: #1A1916; color: #FCFBF8; font-weight: 600;
                          border: 1px solid #1A1916; }
        .lec-title { font-size: 14px; color: #1A1916; font-weight: 500; }
        .lec-date { font-size: 12px; color: #9A9484; margin-top: 2px; }
        .side-foot { border-top: 1px solid #D7D2C5; padding: 14px 18px; }
        .newlecture { min-height: 40px; border: 1px solid #C9C4B6;
                      border-radius: 8px; background: #FCFBF8; color: #1A1916;
                      font-size: 14px; font-weight: 500; box-shadow: none; }
        .newlecture:hover { background: #F1EEE6; }

        .editor { background: #FCFBF8; }
        .formatbar { background: #FCFBF8; border-bottom: 1px solid #D7D2C5;
                     padding: 10px 36px; min-height: 34px; }
        .stylebtn { min-height: 34px; padding: 0 13px; border: 1px solid #D7D2C5;
                    border-radius: 8px; background: #FCFBF8; color: #1A1916;
                    font-size: 14px; font-weight: 500; box-shadow: none; }
        .stylebtn:hover { background: #F1EEE6; }
        .stylebtn .caret { font-size: 11px; color: #9A9484; }
        .fmtbtn { min-width: 34px; min-height: 34px; padding: 0;
                  background: transparent; border: none; box-shadow: none;
                  border-radius: 8px; color: #1A1916; font-size: 17px; }
        .fmtbtn:hover { background: #EFEBE0; }
        /* With no lecture open the format bar is insensitive, but Body / B / I
           still looked live: the rules above set their colour outright, and a
           declaration that lands on the button's own LABEL node beats any
           colour inherited from the button, so GTK's insensitive dimming never
           showed. The icon buttons greyed (GTK dims the image itself), which
           left half a greyed toolbar. Name the labels explicitly, as journal
           does for the same bar. */
        .fmtbtn:disabled, .fmtbtn:disabled label { color: #B3AD9E; }
        .stylebtn:disabled, .stylebtn:disabled label,
        .stylebtn:disabled .caret { color: #B3AD9E; }
        .fmtbtn.bold { font-weight: 700; }
        .fmtbtn.ital { font-style: italic; }
        .fsep { color: #D7D2C5; min-width: 1px; }
        .wordcount, .savestate { font-size: 13px; color: #9A9484; }
        .canvaswrap { background: #FCFBF8; }
        .canvas { padding: 56px 24px 160px; }
        .canvas-eyebrow-row { margin-bottom: 18px; }
        .canvas-eyebrow { font-size: 12px; letter-spacing: 0.1em; color: #6E695E;
                          font-weight: 700; }
        .doctitle { font-family: "Newsreader","Liberation Serif",serif;
                    font-weight: 700; font-size: 40px; color: #1A1916;
                    background: transparent; border: none; padding: 0;
                    margin-bottom: 8px; }
        .doctitle.ghost { color: #B3AD9E; }
        /* The two rename hit areas on the page (class eyebrow, lecture title)
           are real Gtk.Buttons so they are focusable and operable from the
           keyboard -- but they must go on looking like the plain rows they
           wrap. Papertone's `button` gives every button a fill, a 1px hair
           border, 5px 14px padding and a 20px min-height; inherited here that
           would box the eyebrow and shove the 40px serif heading off its
           margin. Neutralise the whole of it, including the `background-image`
           and `@select` fill the theme paints for :active/:checked -- a
           selected state is drawn as an IMAGE, so a colour-only reset loses.
           Everything is flat and 2D; only the hover tint marks the target.
           NO `outline: none` ANYWHERE IN HERE: the global focus ring in
           Papertone is what makes these reachable controls visible to a
           keyboard user, and suppressing it would give back with one hand
           exactly what the conversion won with the other. */
        .doctitlebtn { border-radius: 8px; padding: 0; margin: 0; border: none;
                       background: transparent; background-image: none;
                       box-shadow: none; text-shadow: none;
                       min-height: 0; min-width: 0; }
        .doctitlebtn:hover { background: #F1EEE6; background-image: none;
                             border: none; box-shadow: none; }
        .doctitlebtn:active, .doctitlebtn:checked {
                       background: #EAE3D2; background-image: none;
                       border: none; box-shadow: none; }
        .canvas-meta { font-size: 13px; color: #9A9484; margin-bottom: 34px;
                       padding-bottom: 24px; border-bottom: 1px solid #D7D2C5; }
        .docbody { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 17px; color: #1A1916; background: #FCFBF8;
                   margin-top: 14px; caret-color: #C8341E; }
        .docbody text { background: #FCFBF8; }
        .docbody text selection { background-color: #EAE3D2; color: #1A1916; }
        .empty-wrap { padding: 60px 0 0; }
        .empty-title { font-family: "Newsreader","Liberation Serif",serif;
                       font-size: 20px; color: #1A1916; margin-bottom: 6px; }
        .empty-sub { font-size: 13px; color: #9A9484; margin-bottom: 16px; }
        .emptybtn { min-height: 36px; padding: 0 18px; font-size: 14px;
                    background: #FCFBF8; border: 1px solid #C9C4B6;
                    border-radius: 8px; box-shadow: none; color: #1A1916; }
        .emptybtn:hover { background: #F1EEE6; }

        /* Rename / delete cards: papertone, undecorated, matching the rest of
           the OS rather than a stock GTK dialog in a window-manager frame.
           Each inverted button colours its LABEL node as well as itself: the
           theme's `* { color: ink }` matches the label directly and would
           otherwise beat the colour inherited from the button. */
        .acdlg { background: #FCFBF8; border: 1px solid #C9C4B6; }
        .acdlgbox { padding: 24px 28px 20px; }
        .acdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .acdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                      font-size: 20px; font-weight: 600; color: #1A1916; }
        .acdlgmsg { font-size: 13px; color: #6E695E; }
        .acdlgentry { min-height: 38px; padding: 0 10px; background: #FCFBF8;
                      border: 1px solid #C9C4B6; border-radius: 8px;
                      font-size: 14px; color: #1A1916; }
        .acdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                       background: #FCFBF8; border: 1px solid #C9C4B6;
                       border-radius: 8px; box-shadow: none; }
        .acdlgcancel:hover { background: #F1EEE6; }
        .acdlgok { font-size: 13px; padding: 6px 16px; background: #C8341E;
                   border: 1px solid #C8341E; border-radius: 8px;
                   box-shadow: none; font-weight: 600; }
        .acdlgok label { color: #FCFBF8; }
        .acdlgok:hover { background: #B12D19; border-color: #B12D19; }
        .acdlgprimary { font-size: 13px; padding: 6px 16px; background: #1A1916;
                        border: 1px solid #1A1916; border-radius: 8px;
                        box-shadow: none; font-weight: 600; }
        .acdlgprimary label { color: #FCFBF8; }
        .acdlgprimary:hover { background: #3A362E; border-color: #3A362E; }
        /* Remove sits apart from Cancel/Save, quiet until you go for it: it is
           the way out of a wrong entry, not the thing the card is for. */
        .acdlgremove { font-size: 13px; padding: 6px 14px; color: #B12D19;
                       background: transparent; border: 1px solid transparent;
                       border-radius: 8px; box-shadow: none; }
        .acdlgremove:hover { background: #F1EEE6; border-color: #C8341E; }
        """
        prov = Gtk.CssProvider()
        prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    # argv[1] may name the view to open on, so a click on the desktop's
    # Homework tile lands on Homework rather than on the notes editor and
    # leaves you to find it. Anything else is ignored.
    _want = sys.argv[1] if len(sys.argv) > 1 else ""
    if _want in ("notes", "schedule", "homework"):
        _cls = type("AcademicsOn" + _want.title(), (Academics,),
                    {"_open_view": _want})
        nbapp.run(_cls)
    else:
        nbapp.run(Academics)
