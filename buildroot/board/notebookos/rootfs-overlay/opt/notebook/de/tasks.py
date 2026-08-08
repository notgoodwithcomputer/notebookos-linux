#!/usr/bin/env python3
"""
Tasks — the Notebook OS task manager (native GTK).

Three columns: a Lists sidebar (Today / Upcoming / Inbox plus any user lists), a
task list with an inline quick-add row, and a Schedule rail with today's events
and a mini month calendar. Ships empty — no seed tasks, lists or events; the
first run shows a technical empty-state. All state persists (tasks.json plus a
private sidecar) and the File menu reads/writes user documents under
$NB_HOME/Documents.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import re
import json
import copy
import time
from datetime import date, timedelta

import nbapp
import nbpicker
import nbicons
from nbi18n import _t  # noqa: E402

# Persistence — the flat text/done list stays shared with the desktop widget
# (widgets.py): both read/write tasks.json in the SAME {"text","done"} shape, so
# a task added here shows up in the desktop Tasks card (after its refresh) and
# vice versa. This app's richer state — per-task project/due/time/prio, the
# schedule events, and any user-added lists — round-trips through a private
# SIDECAR file (tasks-app.json) the widget never touches, so ticking a task in
# the widget still works and the widget can never clobber the richer fields.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
TASKS_FILE = os.path.join(CFG_DIR, "tasks.json")        # shared, flat
META_FILE = os.path.join(CFG_DIR, "tasks-app.json")     # this app's rich state
# User documents (File ▸ Open / Save / Save As) live under Documents; the two
# files above stay as automatic session-recovery for the running app.
DOCS_DIR = os.path.join(HOME, "Documents")
# The Schedule rail reads the SAME event store the Calendar app writes, so the
# two never diverge — an event added / edited / deleted in Calendar shows up
# here on next open, and the rail's own add-event writes straight back to it.
CAL_FILE = os.path.join(CFG_DIR, "calendar.json")       # Calendar app's events
# Named-calendar definitions (name + colour) so a rail event can be drawn in its
# calendar's colour, exactly as the Calendar app shows it.
CALENDARS_FILE = os.path.join(CFG_DIR, "calendars.json")

INK = "#1A1916"
# Tertiary/hint ink — one warm gray for every sublabel, timestamp, count and
# placeholder tint below the secondary ink (#6E695E), so the third tier is a
# single hue rather than a drift of near-identical grays.
MUTED = "#8A857A"
# The signal red is reserved for the active/selected state and alerts only — it
# is NEVER used decoratively. A rail event whose calendar happens to be red is
# clamped to the neutral below so the schedule stays within the design language.
SIGNAL_RED = "#C8341E"
EVENT_NEUTRAL = "#9A9484"   # neutral rail-event colour (matches Calendar)

# Monoline weight for this app's BESPOKE hand-drawn glyphs — the checkbox tick
# and the schedule timeline-marker ring. Kept a touch heavier than the nbicons
# icon canon (1.6px) because these boxes are larger, but as ONE rule so the two
# never drift into separate magic stroke widths (matches calendar.py's calbox
# tick at 2.0). Routed through _draw_check and _draw_marker.
GLYPH_STROKE = 2.0

# Lists ship EMPTY — there are no built-in projects. Every list is user-created
# via "New List", persisted to the sidecar (see _save_tasks) and restored on
# launch. PROJECTS/PROJ_COLOR are populated at runtime from that store.
PROJECTS = []
PROJ_COLOR = {}
# No names are reserved as built-in, so every list persists.
DEFAULT_PROJECT_NAMES = set()
# Colour palette offered in the New List editor (avoids the reserved signal red).
# Deliberately off the papertone tokens: these are IDENTITY tints a person picks
# to tell one list from another at a glance, and the neutrals cannot supply six
# values that stay distinguishable at dot size.
LIST_COLORS = ["#4A5E73", "#6E7B57", "#9A7B4F", "#8A857A", "#7A5C8A", "#3F6B6B"]

DUE_ORDER = ["overdue", "today", "anytime", "tomorrow", "week", "later", "inbox"]
DUE_LABELS = {
    "overdue": "Overdue", "today": "Today", "anytime": "Anytime",
    "tomorrow": "Tomorrow", "week": "This Week", "later": "Later",
    "inbox": "Unsorted",
}

# A task may carry a real calendar day ("date": "YYYY-MM-DD"). Its group is then
# worked out from that day EVERY time the list is drawn, so tomorrow's task
# becomes today's when tomorrow arrives and slips into Overdue if it is missed —
# instead of sitting under a "Tomorrow" heading for a fortnight. A task with no
# date keeps the fixed group it was filed under (older stores, and the
# deliberately undated Anytime / Unsorted groups).


def _today():
    """Today as a date, from the local clock (never time.strptime)."""
    n = time.localtime()
    return date(n.tm_year, n.tm_mon, n.tm_mday)


def _iso(d):
    return "%04d-%02d-%02d" % (d.year, d.month, d.day)


def _from_iso(s):
    """'YYYY-MM-DD' -> date, or None. Plain int splitting, so this file never
    needs time.strptime or the stdlib calendar module that de/calendar.py
    shadows on the guest's PYTHONPATH."""
    try:
        y, m, d = str(s).split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None

VIEWS = [
    ("view:today", "Today", "star"),
    ("view:upcoming", "Upcoming", "calendar"),
    ("view:inbox", "Inbox", "inbox"),
]

WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday"]


def _display_date(lt):
    """A whole local date phrase, so nbi18n may reorder it for CJK locales."""
    return _t("%s, %d %s" % (DAYS[lt[6]], lt[2], MONTHS[lt[1] - 1]))


def _build_when_tokens():
    """The words the quick-add's '>' token accepts, in English AND in the
    language the app is running in — the tooltip promises '>friday' in English
    and '>vendredi' in French, so both have to work. Weekday words map to a
    weekday number (Monday=0, as date.weekday() counts); the group words map to
    a due-group id. Three-letter forms ('>fri') are accepted too."""
    days, groups = {}, {}
    for i, name in enumerate(DAYS):
        for form in (name, _t(name)):
            low = form.lower()
            days.setdefault(low, i)
            days.setdefault(low[:3], i)
    for gid, label in DUE_LABELS.items():
        for form in (gid, label, _t(label)):
            groups.setdefault(form.lower(), gid)
    return days, groups


DAY_TOKENS, GROUP_TOKENS = _build_when_tokens()

# No first-run seed: Tasks ships empty. Tasks come from the persisted store (or
# a File ▸ Open), and Schedule events come from the Calendar app's shared store
# (CAL_FILE). A fresh install therefore shows a technical empty-state.


class Tasks(nbapp.AppWindow):
    app_name = "Tasks"
    menus = ("File", "Edit", "View", "Lists")

    def __init__(self):
        super().__init__()
        self._install_css()

        # Lifecycle flag, set the moment the window starts tearing down. Every
        # timer this app owns checks it before touching a widget, so nothing
        # runs against a destroyed window (see _on_destroy).
        self._closed = False
        self._day_rollover_id = 0

        self.view = "view:today"
        self._doc_path = None    # current File-menu document (None until saved)
        self._side_rows = {}     # id -> (row widget, count label)
        self._rows = {}          # task idx -> {check, title, flag} handles for
        #                          the in-place toggle (see _toggle/_task_row)
        # Load persisted state BEFORE building the UI from the model: sets
        # self.tasks + self.events (richer models) and restores any user-added
        # lists into PROJECTS. Nothing is seeded — a fresh install loads to an
        # empty model; falls back cleanly on a missing/foreign/legacy file.
        self._load_state()
        self.undo = nbapp.UndoHistory(self._undo_snapshot,
                                      self._restore_undo_snapshot)
        self.undo.reset()

        self._body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._body.set_hexpand(True); self._body.set_vexpand(True)
        self.content.pack_start(self._body, True, True, 0)

        self._sidebar = self._build_sidebar()
        self._body.pack_start(self._sidebar, False, False, 0)
        self._body.pack_start(self._build_center(), True, True, 0)
        self._body.pack_start(self._build_rail(), False, False, 0)

        # Final flush on close so the last add/toggle is never lost.
        self.connect("destroy", self._on_destroy)

        self._refresh()
        # Persist once on launch so a legacy flat-only file we just enriched into
        # the richer model sticks for next time. A fresh install writes an empty
        # store (nothing is seeded).
        self._save_tasks()
        # Land the caret in the quick-add so a task can be typed the instant the
        # window appears — no click needed to start.
        try:
            self.draft.grab_focus()
        except Exception:
            pass

    # ------------------------------------------------------------- persistence
    def _load_state(self):
        """Load the full app model before the UI is built. Restores user-added
        lists into PROJECTS first (so the sidebar/menus include them), then the
        richer tasks and the schedule events."""
        meta = self._read_meta()
        if isinstance(meta, dict):
            # A garbage sidecar might carry "projects" as something other than a
            # list (a number, a string, an object); coerce anything non-list to
            # empty so a corrupt file opens to a clean empty state, never a crash.
            raw_projects = meta.get("projects")
            if isinstance(raw_projects, dict):
                raw_projects = list(raw_projects.values())
            if not isinstance(raw_projects, list):
                raw_projects = []
            for item in raw_projects:
                try:
                    name, color = str(item[0]), str(item[1])
                except Exception:
                    continue
                if name and name not in PROJ_COLOR:
                    PROJECTS.append((name, color))
                    PROJ_COLOR[name] = color
        self.tasks = self._load_tasks(meta)
        self._adopt_orphan_lists()
        # Events are NOT part of this app's sidecar any more — they live in the
        # Calendar app's shared store so the two stay in sync (see _load_events).
        self.events = self._load_events()

    def _adopt_orphan_lists(self):
        """Re-create any list a loaded TASK still names but the list store no
        longer holds, so the two halves of the same fact can never disagree.

        The task's list assignment and the list definitions are persisted
        separately (a task carries "project": "Home"; the "Home" list itself
        lives under "projects"), so the definitions can be lost on their own —
        a sidecar that lost its wrapper is read as a bare task list with no
        "projects" key at all (see _read_meta), and a document written before
        lists existed has tasks but no "lists". The tasks then kept a list that
        was in NO sidebar row, in no Lists menu and in no view — and because
        __init__ saves immediately, the next write persisted "projects": [],
        turning a recoverable mismatch into a permanent one with the list name
        still sitting on every one of its tasks."""
        for t in self.tasks:
            name = t.get("project")
            if name and name not in PROJ_COLOR:
                color = LIST_COLORS[len(PROJECTS) % len(LIST_COLORS)]
                PROJECTS.append((name, color))
                PROJ_COLOR[name] = color

    def _read_meta(self):
        """This app's private sidecar ({tasks, events, projects}) or None.

        A sidecar that lost its wrapper and is a bare LIST is read as the task
        list it plainly is. The flat file would still carry the titles, so this
        never looked like data loss — but the due dates, priorities and lists
        the user set only exist here, and _save_tasks rewrites this file on
        close, so shrugging the whole thing off quietly flattened every task
        back to an undated Today."""
        try:
            with open(META_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"tasks": data}
        return None

    def _read_flat(self):
        """The shared flat file ([{text,done}, ...]) or None. This is what the
        desktop widget writes, so it is authoritative for tick state."""
        try:
            with open(TASKS_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None
        if isinstance(data, dict):
            data = list(data.values())   # keyed object -> its records, in order
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
        return None

    def _load_tasks(self, meta):
        """Build the richer task model. Preference order:
        1. the sidecar's rich task list (normal steady state) — with tick state
           overlaid from the shared flat file so a widget tick is reflected;
        2. a legacy/foreign flat-only tasks.json — adopted as-is (backward
           compatible, no loss);
        3. nothing on disk — ship empty."""
        flat = self._read_flat()
        # Baseline for the three-way merge in _save_tasks.  The desktop Tasks
        # card writes this same file while this window is open; remembering
        # what we originally saw lets us distinguish its newer tick from a
        # completion change made here.
        self._flat_base = [dict(t) for t in flat] \
            if isinstance(flat, list) else []
        rich = meta.get("tasks") if isinstance(meta, dict) else None
        # Tasks stored as an object keyed by id: the values are still the rich
        # tasks, and falling through to the flat file instead would silently
        # strip every due date, priority and list assignment on the next save.
        if isinstance(rich, dict):
            rich = list(rich.values())
        if isinstance(rich, list) and rich:
            tasks = [self._norm_task(t) for t in rich if isinstance(t, dict)]
            if isinstance(flat, list):
                self._overlay_flat(tasks, flat)
            return tasks
        if isinstance(flat, list) and flat:
            return self._from_flat(flat)
        return []

    def _norm_task(self, t):
        """Coerce a task dict to the full in-memory shape with safe defaults."""
        due = t.get("due", "today")
        # `due` from a foreign/garbage document may be any type; guard the dict
        # membership test (an unhashable list would raise) before trusting it.
        if not isinstance(due, str) or due not in DUE_LABELS:
            due = "today"
        proj = t.get("project")
        proj = None if proj is None else str(proj)
        try:
            prio = int(t.get("prio", 0))
        except Exception:
            prio = 0
        prio = max(0, min(2, prio))
        # An optional real day. Anything unparseable is dropped rather than
        # kept, so a foreign document can never leave a task in a group that
        # can't be computed.
        day = t.get("date")
        day = _iso(_from_iso(day)) if _from_iso(day) is not None else ""
        return {"title": str(t.get("title", "")), "project": proj, "due": due,
                "date": day, "time": str(t.get("time", "") or ""),
                "prio": prio, "done": bool(t.get("done"))}

    @staticmethod
    def _due_of(t):
        """The group a task belongs to RIGHT NOW. A dated task is bucketed from
        its day every time the list is drawn — which is what makes 'Tomorrow'
        turn into 'Today' overnight and a missed task turn red — while an
        undated one keeps the fixed group it was filed under."""
        d = _from_iso(t.get("date"))
        if d is None:
            due = t.get("due", "today")
            return due if due in DUE_LABELS else "today"
        now = _today()
        if d < now:
            return "overdue"
        if d == now:
            return "today"
        if d == now + timedelta(days=1):
            return "tomorrow"
        if d <= now + timedelta(days=7):
            return "week"
        return "later"

    @staticmethod
    def _day_note(t):
        """The short day a dated task falls on ('Mon 27 Jul'), for the row's
        meta line. Empty for an undated task and for one due today — the group
        heading directly above it already says Today."""
        d = _from_iso(t.get("date"))
        if d is None:
            return ""
        now = _today()
        if d == now:
            return ""
        note = "%s %d %s" % (DAYS[d.weekday()][:3], d.day,
                              MONTHS[d.month - 1][:3])
        return _t(note if d.year == now.year else "%s %d" % (note, d.year))

    def _overlay_flat(self, tasks, flat):
        """Reflect the shared flat file onto the rich model: the widget can tick
        a task there, so match by title and adopt its done state. Any flat task
        with no rich counterpart is kept (never drop data)."""
        pending = {}
        for f in flat:
            pending.setdefault(str(f.get("text", "")), []).append(
                bool(f.get("done")))
        for t in tasks:
            lst = pending.get(t["title"])
            if lst:
                t["done"] = lst.pop(0)
        for text, dones in pending.items():
            for d in dones:
                tasks.append(self._norm_task(
                    {"title": text, "project": None, "due": "today",
                     "done": d}))

    def _from_flat(self, flat):
        """Adopt a legacy flat-only file. Each entry becomes an unassigned task
        (no list) due today, carrying over its done state. No data lost."""
        out = []
        for f in flat:
            out.append(self._norm_task(
                {"title": str(f.get("text", "")), "project": None,
                 "due": "today", "done": f.get("done")}))
        return out

    def _load_events(self):
        """Schedule events come from the CALENDAR APP'S shared store
        (calendar.json), so an event added / edited / deleted in Calendar shows
        up in this rail (and vice versa) instead of the two diverging. Each
        Calendar record is {date:'YYYY-MM-DD', start:float, end:float, title,
        cal}; map it to the rail's row shape. No events are seeded — a missing or
        empty store simply yields an empty schedule."""
        raw = self._read_calendar()
        if raw is None:
            return []
        colors = self._calendar_colors()
        out = []
        for item in raw:
            ev = self._event_from_cal(item, colors)
            if ev is not None:
                out.append(ev)
        return out

    def _read_calendars(self):
        """The Calendar app's named-calendar store ([{name,color}, ...]) or None
        when it is missing/unreadable. Never raises."""
        try:
            with open(CALENDARS_FILE) as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict)]
        except Exception:
            pass
        return None

    def _calendar_colors(self):
        """Map each named calendar to its colour (from calendars.json) so a rail
        event is drawn in its calendar's colour, matching the Calendar app. Only
        well-formed #RRGGBB values are kept; the reserved signal red is clamped
        to the neutral so it is never used decoratively here."""
        colors = {}
        for c in (self._read_calendars() or []):
            name = str(c.get("name", "")).strip()
            color = str(c.get("color", "") or "")
            if name and re.match(r"^#[0-9A-Fa-f]{6}$", color):
                colors[name] = (EVENT_NEUTRAL if color.upper() == SIGNAL_RED
                                else color)
        return colors

    def _read_calendar(self):
        """The Calendar app's event store ([{date,start,end,title,cal}, ...]) or
        None when the file is missing/unreadable (Calendar has never run). An
        existing-but-empty list returns []. Never raises."""
        try:
            with open(CAL_FILE) as fh:
                data = json.load(fh)
        except Exception:
            return None
        if isinstance(data, dict):
            # A wrapped store ({"events": [...]}, or an object keyed by id):
            # take the first list of records inside it. This matters far more
            # than the rail it feeds — _append_calendar_event writes this file
            # back, so a store it could not read used to be REPLACED by the one
            # event being added, wiping the user's calendar from another app.
            inner = data.get("events")
            if not isinstance(inner, list):
                vals = list(data.values())
                inner = vals if all(isinstance(v, dict) for v in vals) else None
                for v in data.values():
                    if isinstance(v, list) and any(isinstance(x, dict)
                                                   for x in v):
                        inner = v
                        break
            data = inner
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        return None

    def _event_from_cal(self, item, colors=None):
        """Map one Calendar record onto the rail's row model. Dates are parsed
        by plain int split — NEVER time.strptime / import calendar, which the
        DE's calendar.py shadows on PYTHONPATH. The row colour comes from the
        event's named calendar (see _calendar_colors), falling back to neutral.
        Returns None on a record with no salvageable date."""
        ymd = self._parse_iso(item.get("date"))
        if ymd is None:
            return None
        try:
            start = float(item.get("start", 9.0))
        except (TypeError, ValueError):
            start = 9.0
        try:
            end = float(item.get("end", start + 1))
        except (TypeError, ValueError):
            end = start + 1
        cal = str(item.get("cal", "") or "")
        color = (colors or {}).get(cal, EVENT_NEUTRAL)
        return {"start": self._fmt_hhmm(start),
                "dur": self._fmt_dur(start, end),
                "title": str(item.get("title", "")),
                "where": "",   # Calendar's schema carries no location field
                "color": color,
                "ymd": ymd}

    @staticmethod
    def _parse_iso(s):
        """'YYYY-MM-DD' -> (year, month, day) by plain int split, or None on
        anything malformed. No time.strptime / import calendar (calendar.py
        shadows the stdlib module here)."""
        try:
            y, m, d = str(s).split("-")
            return (int(y), int(m), int(d))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _fmt_hhmm(val):
        """A Calendar float hour (9.0, 18.5) -> 'HH:MM'."""
        try:
            h = int(val)
            m = int(round((val - h) * 60))
            if m >= 60:
                h += 1
                m -= 60
            return "%02d:%02d" % (h, m)
        except Exception:
            return ""

    @staticmethod
    def _fmt_dur(start, end):
        """A Calendar start/end float pair -> a compact duration label
        ('2h', '30m', '1h30m'), or '' when the span is empty or negative."""
        try:
            mins = int(round((float(end) - float(start)) * 60))
        except Exception:
            return ""
        if mins <= 0:
            return ""
        h, m = divmod(mins, 60)
        if h and m:
            return "%dh%dm" % (h, m)
        if h:
            return "%dh" % h
        return "%dm" % m

    @staticmethod
    def _done_by_occurrence(rows):
        """Map (title, occurrence) to done for a flat task snapshot.

        The shared format predates stable IDs and permits duplicate titles, so
        title alone is not an identity.  Numbering equal titles in their stored
        order preserves independent duplicate rows without changing the file
        format understood by the desktop widget.
        """
        seen = {}
        out = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text", ""))
            n = seen.get(text, 0)
            seen[text] = n + 1
            out[(text, n)] = bool(row.get("done"))
        return out

    def _merge_external_ticks(self, outgoing):
        """Fold widget-only completion changes into this pending save.

        This is a three-way merge: the flat list loaded with this window is the
        baseline, tasks.json now is the other writer, and ``outgoing`` is this
        app.  Disk wins only when disk changed and this app did not; an edit in
        Tasks wins a same-record conflict.  Additions and deletions remain
        authoritative here because the widget can only tick rows, never create
        or remove them.
        """
        disk = self._read_flat()
        if not isinstance(disk, list):
            return
        base = self._done_by_occurrence(getattr(self, "_flat_base", []))
        current = self._done_by_occurrence(disk)
        seen = {}
        for i, row in enumerate(outgoing):
            text = str(row.get("text", ""))
            n = seen.get(text, 0)
            seen[text] = n + 1
            key = (text, n)
            if key not in base or key not in current:
                continue
            mine = bool(row.get("done"))
            if current[key] != base[key] and mine == base[key]:
                row["done"] = current[key]
                # Keep the in-memory model and the file in agreement so a
                # second autosave cannot undo the merge it just performed.
                if i < len(self.tasks):
                    self.tasks[i]["done"] = current[key]

    def _save_tasks(self):
        """Persist the TASKS. The shared flat file keeps the widget's
        {"text","done"} shape (so the desktop card stays in sync and can never
        be broken); the richer tasks and any user-added lists go to the private
        sidecar. Schedule events are NOT stored here — they live in the Calendar
        app's shared store (see _append_calendar_event). Never crash on I/O."""
        try:
            flat = [{"text": t.get("title", ""), "done": bool(t.get("done"))}
                    for t in self.tasks]
            self._merge_external_ticks(flat)
            nbapp.atomic_write_json(TASKS_FILE, flat)
            self._flat_base = [dict(t) for t in flat]
        except Exception:
            pass
        try:
            extra = [[n, c] for (n, c) in PROJECTS
                     if n not in DEFAULT_PROJECT_NAMES]
            meta = {"tasks": self.tasks, "projects": extra}
            nbapp.atomic_write_json(META_FILE, meta)
            self._save_warned = False
        except Exception as exc:
            # See academics._save_to_disk. The sidecar holds everything the flat
            # file cannot express — due dates, notes, which list a task is on —
            # so a silently failed write loses the part of a task that took the
            # longest to enter. Warn once per run of failures.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash(nbapp.save_failure_reason(exc, META_FILE))
                except Exception:
                    pass

    def _undo_snapshot(self):
        return {"tasks": copy.deepcopy(self.tasks),
                "projects": copy.deepcopy(PROJECTS),
                "view": self.view}

    def _restore_undo_snapshot(self, state):
        self.tasks = copy.deepcopy(state.get("tasks", []))
        PROJECTS[:] = copy.deepcopy(state.get("projects", []))
        PROJ_COLOR.clear()
        PROJ_COLOR.update(PROJECTS)
        self.view = state.get("view", "view:today")
        if self.view.startswith("proj:") and self.view[5:] not in PROJ_COLOR:
            self.view = "view:today"
        self._save_tasks()
        if hasattr(self, "_body"):
            self._rebuild_sidebar()
        elif hasattr(self, "listbox"):
            self._refresh()

    def _on_destroy(self, *_):
        # Idempotent: GTK can emit "destroy" more than once through nested
        # teardown paths, and a second pass must not remove a source id that has
        # since been reused by another timer, nor write the store twice.
        if getattr(self, "_closed", False):
            return False
        # Marked BEFORE anything else, so a rollover poll that fires during the
        # save below sees a closing window and stays away from the widgets.
        self._closed = True
        rid = getattr(self, "_day_rollover_id", 0)
        self._day_rollover_id = 0
        if rid:
            try:
                GLib.source_remove(rid)
            except Exception:
                pass
        self._save_tasks()
        return False

    def _on_key(self, w, ev):
        # Esc backs out of an open overlay card (New List or Remove List) FIRST.
        # Without this, the base nbapp._on_key treats Esc as a quit (no card
        # overlay of its own to dismiss) and closes the whole app — so pressing
        # Esc to cancel the card used to kill Tasks. _close_newlist() /
        # _close_removelist() return True only when a card was actually open (and
        # close it); otherwise we fall through to the base handling (menu close /
        # quit), mirroring novel.py's prompt intercept.
        if ev.keyval == Gdk.KEY_Escape and (self._close_task_menu()
                                            or self._close_rename()
                                            or self._close_clearcard()
                                            or self._close_newlist()
                                            or self._close_removelist()):
            return True
        if hasattr(self, "undo") and nbapp.undo_keys(self.undo, ev):
            return True
        return super()._on_key(w, ev)

    # ------------------------------------------------------- File menu (docs)
    # The File menu operates on user-chosen documents under $NB_HOME/Documents,
    # a task/list collection serialised as JSON ({"tasks": [...], "lists": [...]})
    # — separate from the automatic session store (tasks.json + sidecar), which
    # keeps running unchanged for recovery and desktop-widget sync.
    def _file_new_task(self):
        """Focus the quick-add field so a new task can be entered; Enter files
        it. A real, wired action — the entry's activate handler does the add."""
        try:
            self.draft.set_text("")
            self.draft.grab_focus()
        except Exception:
            pass

    def _doc_dict(self):
        """The current model as a portable document: tasks plus the user lists
        (so a document restores its own lists when opened elsewhere)."""
        return {"tasks": self.tasks,
                "lists": [[n, c] for (n, c) in PROJECTS]}

    def _write_doc(self, path):
        """Serialise the task/list document to `path`. Returns True on success."""
        try:
            nbapp.atomic_write_json(path, self._doc_dict(),
                                    ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _open_doc(self, path):
        """Load a task/list document, replacing the in-memory tasks and merging
        in any lists it references. Returns True on success.

        Every DE app saves into the SAME Documents folder, so the chosen file
        may well belong to another app (a Calendar document, accounting's
        ledger, …). VALIDATE this app's document shape — a top-level object
        whose "tasks" is a list — BEFORE mutating anything: replacing the model
        and autosaving over the shared tasks.json would silently wipe the data.
        On an unrecognised shape flash and change nothing (no model swap, no
        _doc_path, no list merge, no autosave)."""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            self._flash("Open failed")
            return False
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            self._flash("Not a Tasks file")
            return False
        tasks = [self._norm_task(t) for t in data["tasks"]
                 if isinstance(t, dict)]
        for item in (data.get("lists") or []):
            try:
                lname, lcolor = str(item[0]), str(item[1])
            except Exception:
                continue
            if lname and lname not in PROJ_COLOR:
                PROJECTS.append((lname, lcolor))
                PROJ_COLOR[lname] = lcolor
        self.tasks = tasks
        # A document may name lists on its tasks without defining them above
        # (one written before "lists" was stored, or hand-made); re-create them
        # rather than leaving those tasks on a list the app cannot show.
        self._adopt_orphan_lists()
        self._doc_path = path
        if self.view.startswith("proj:") and self.view[5:] not in PROJ_COLOR:
            self.view = "view:today"
        self._rebuild_sidebar()   # lists may have changed; rebuilds + refreshes
        self._save_tasks()        # sync the session store to the opened document
        self._flash("Opened " + os.path.basename(path))
        return True

    def _file_open(self):
        try:
            self._close_menu()
        except Exception:
            pass
        path = self._choose_file(save=False)
        if path and os.path.isfile(path):
            self._open_doc(path)

    def _file_save(self):
        """Write to the current document; prompt via Save As if there is none."""
        try:
            self._close_menu()
        except Exception:
            pass
        if not self._doc_path:
            return self._file_save_as()
        if self._write_doc(self._doc_path):
            self._flash("Saved " + os.path.basename(self._doc_path))
        else:
            self._flash("Save failed")

    def _file_save_as(self):
        """Pick a path and write the document there, then adopt it."""
        try:
            self._close_menu()
        except Exception:
            pass
        path = self._choose_file(save=True)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"        # default extension for a bare name
        self._doc_path = path
        if self._write_doc(path):
            self._flash("Saved " + os.path.basename(path))
        else:
            self._flash("Save failed")

    def _choose_file(self, save):
        """Finder-style in-app picker under Documents; return a path or None."""
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
        except Exception:
            pass
        base = os.path.dirname(self._doc_path) if self._doc_path else DOCS_DIR
        start = base if os.path.isdir(base) else DOCS_DIR
        if save:
            suggested = (os.path.basename(self._doc_path) if self._doc_path
                         else "tasks.json")
            return nbpicker.save_file(self, title="Save Tasks As",
                                      start_dir=start, suggested_name=suggested,
                                      patterns=("*.json",), default_ext=".json")
        return nbpicker.open_file(self, title="Open Tasks",
                                  start_dir=start, patterns=("*.json",))

    def _flash(self, text):
        """Surface a transient file-op result in the header's right-hand slot,
        then restore the running 'N remaining' count. Crash-safe."""
        try:
            self.remaining.set_text(text)
        except Exception:
            pass
        GLib.timeout_add_seconds(2, self._restore_remaining)

    def _restore_remaining(self):
        try:
            self._update_counts()
        except Exception:
            pass
        return False   # one-shot

    # -------------------------------------------------------------- menu bar
    def menu_items(self, name):
        if name == "File":
            # tasks.json and its sidecar are the sole source of truth and are
            # rewritten on every edit, so there is no document to Save and
            # nothing a Save As would rescue. The old Open… was worse than
            # redundant: it REPLACED the whole store — the same store the
            # desktop widget reads — from a file picked out of the shared
            # Documents folder. File now offers only what the app can actually
            # make (docs/MENU-CONVENTIONS.md, the single-store File menu).
            return [
                ("New Task", self._file_new_task),
                ("New List", lambda: self._on_new_list(None)),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            return nbapp.undo_menu_items(self.undo)
        if name == "View":
            def mk(vid, label):
                mark = "•  " if self.view == vid else "    "
                return (mark + label, lambda v=vid: self._on_select(None, v))
            # "Look for New Events" names what the action is FOR — pulling in
            # anything added in Calendar since this window opened — instead of
            # the machine's word for how it does it ("Refresh").
            items = [mk("view:today", "Today"),
                     mk("view:upcoming", "Upcoming"),
                     mk("view:inbox", "Inbox"), nbapp.SEP,
                     ("Look for New Events", self._do_refresh)]
            # Clear Completed stays VISIBLE with nothing to clear and greys out,
            # so the menu does not shift under the hand of someone reaching for
            # the item below it.
            items.append(nbapp.SEP)
            items.append(("Clear Completed", self._open_clearcard
                          if any(t.get("done") for t in self.tasks) else None))
            return items
        if name == "Lists":
            items = [("New List", lambda: self._on_new_list(None))]
            # Remove List targets the currently selected list, so it greys out
            # (never disappears) while a built-in view is showing. It confirms
            # first — hence the ellipsis — then reassigns that list's tasks to
            # Inbox; never a silent delete.
            on_list = (self.view.startswith("proj:")
                       and self.view[5:] in PROJ_COLOR)
            nm = self.view[5:] if on_list else ""
            items.append(
                ("Remove List…",
                 (lambda n=nm: self._open_removelist(n)) if on_list else None))
            if PROJECTS:
                items.append(nbapp.SEP)
                for pname, _color in PROJECTS:
                    vid = "proj:" + pname
                    mark = "•  " if self.view == vid else "    "
                    items.append(
                        (mark + pname, lambda v=vid: self._on_select(None, v)))
            return items
        return super().menu_items(name)

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self):
        # Reset the row registry so a rebuild (after New List) starts clean and
        # never keeps stale widget references.
        self._side_rows = {}
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrap.get_style_context().add_class("sidebar")
        # The two fixed rails (this and the Schedule rail) plus the centre
        # column set the window's minimum width. At 284 + 392 they left a
        # 1024-wide panel only 348px for the tasks themselves — the primary
        # content, squeezed under a third of the screen. Both are trimmed to
        # widths their content still sits comfortably in.
        wrap.set_size_request(252, -1)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_margin_top(22); inner.set_margin_bottom(22)
        inner.set_margin_start(14); inner.set_margin_end(14)

        for vid, label, glyph in VIEWS:
            inner.pack_start(self._side_row(vid, label, glyph=glyph), False, False, 0)

        # The Lists section only appears once the user has created a list; a
        # fresh install shows no orphan header (the New List footer is the
        # affordance to create one).
        if PROJECTS:
            head = Gtk.Label(label=_t("LISTS"), xalign=0)
            head.get_style_context().add_class("sectionhead")
            head.set_margin_top(24); head.set_margin_bottom(4)
            head.set_margin_start(12)
            inner.pack_start(head, False, False, 0)

            for name, color in PROJECTS:
                inner.pack_start(
                    self._side_row("proj:" + name, name, dotcolor=color),
                    False, False, 0)

        scroll.add(inner)
        wrap.pack_start(scroll, True, True, 0)

        # pinned footer — "New List" (matches the design's sidebar base)
        foot = Gtk.Button(); foot.set_relief(Gtk.ReliefStyle.NONE)
        foot.get_style_context().add_class("newlist")
        frow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fico = nbicons.image("plus", 15, MUTED)
        frow.pack_start(fico, False, False, 0)
        flab = Gtk.Label(label=_t("New List"), xalign=0)
        flab.get_style_context().add_class("newlistlabel")
        frow.pack_start(flab, True, True, 0)
        foot.add(frow)
        foot.connect("clicked", self._on_new_list)
        wrap.pack_end(foot, False, False, 0)
        return wrap

    def _on_new_list(self, _btn):
        # Real "New List" editor: pick a name + colour, append to Projects,
        # rebuild the sidebar and persist. Drawn as an in-window overlay (same
        # approach as the About card) so it needs no popup window.
        try:
            self._close_menu()
        except Exception:
            pass
        self._open_newlist()

    def _surface_size(self):
        """The card surface to size a scrim against and centre a card on: the
        LIVE window allocation, falling back to the real primary monitor before
        the window is allocated. Never a hardcoded 1920x1080 — on a 1366- or
        1024-wide panel that put every card off-centre and its right-hand edge
        past the screen, where it could not be reached (see nbapp.screen_size)."""
        # The overlay is the surface the card is actually placed on, so measure
        # THAT (it fills the window); fall back to the window, then the monitor.
        alloc = self._overlay.get_allocation()
        if alloc.width <= 1 or alloc.height <= 1:
            alloc = self.get_allocation()
        sw, sh = nbapp.screen_size()
        return (alloc.width if alloc.width > 1 else sw,
                alloc.height if alloc.height > 1 else sh)

    @staticmethod
    def _centre_card(layer, holder, W, H):
        """Centre an already-shown card on a W x H surface using its measured
        natural size, so cards of any size land in the middle at any panel
        resolution (mirrors nbapp's About overlay)."""
        try:
            _min, nat = holder.get_preferred_size()
            cw = nat.width if nat.width > 1 else 380
            ch = nat.height if nat.height > 1 else 220
            layer.move(holder, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        except Exception:
            pass

    def _open_newlist(self):
        self._close_newlist()
        self._nl_color_idx = 0
        self._nl_swatches = []
        W, H = self._surface_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_newlist(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nlcard")
        title = Gtk.Label(label=_t("New List"), xalign=0)
        title.get_style_context().add_class("nltitle")
        card.pack_start(title, False, False, 0)

        self._nl_entry = Gtk.Entry()
        self._nl_entry.get_style_context().add_class("nlentry")
        self._nl_entry.set_placeholder_text(_t("List name"))
        self._nl_entry.set_width_chars(24)
        self._nl_entry.connect("activate", self._nl_create)
        card.pack_start(self._nl_entry, False, False, 0)

        clab = Gtk.Label(label=_t("Colour"), xalign=0)
        clab.get_style_context().add_class("nlhint")
        card.pack_start(clab, False, False, 0)
        sw = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        for i, c in enumerate(LIST_COLORS):
            sw.pack_start(self._swatch(c, i), False, False, 0)
        card.pack_start(sw, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_margin_top(6); btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nlbtn")
        cancel.connect("clicked", lambda *a: self._close_newlist())
        btns.pack_start(cancel, False, False, 0)
        create = Gtk.Button(label=_t("Create"))
        create.set_relief(Gtk.ReliefStyle.NONE)
        create.get_style_context().add_class("nlbtn")
        create.get_style_context().add_class("nlcreate")
        create.connect("clicked", self._nl_create)
        btns.pack_start(create, False, False, 0)
        card.pack_start(btns, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, holder, W, H)
        self._nl_layer = layer
        self._nl_entry.grab_focus()

    def _close_newlist(self):
        layer = getattr(self, "_nl_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._nl_layer = None
            return True
        return False

    # ------------------------------------------------------------- list removal
    def _open_removelist(self, name):
        """Confirmation card for deleting a list. Reuses the New List overlay
        approach (an in-window layer, reliable on the no-compositor stack). The
        list's tasks are NOT deleted — they are reassigned to Inbox (no list)."""
        if name not in PROJ_COLOR:
            return
        try:
            self._close_menu()
        except Exception:
            pass
        self._close_newlist()
        self._close_removelist()
        n = sum(1 for t in self.tasks if t.get("project") == name)

        W, H = self._surface_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_removelist(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nlcard")
        title = Gtk.Label(label=_t("Remove List"), xalign=0)
        title.get_style_context().add_class("nltitle")
        card.pack_start(title, False, False, 0)

        # Say what happens to the tasks, in a full sentence and the app's own
        # typographic quotes: the old fragment ('… 3 tasks reassigned to
        # Inbox.') read like a log line and left it unclear whether the tasks
        # were about to be deleted with the list.
        msg = ("Remove the list “%s”? Its %d task%s %s kept and moved to Inbox."
               % (name, n, "" if n == 1 else "s", "is" if n == 1 else "are"))
        if n == 0:
            msg = "Remove the list “%s”? It has no tasks in it." % name
        body = Gtk.Label(label=msg, xalign=0)
        body.get_style_context().add_class("nlbody")
        body.set_line_wrap(True)
        body.set_max_width_chars(30)
        card.pack_start(body, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_margin_top(6); btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nlbtn")
        cancel.connect("clicked", lambda *a: self._close_removelist())
        btns.pack_start(cancel, False, False, 0)
        remove = Gtk.Button(label=_t("Remove"))
        remove.set_relief(Gtk.ReliefStyle.NONE)
        remove.get_style_context().add_class("nlbtn")
        remove.get_style_context().add_class("nlremove")
        remove.connect("clicked", lambda *a, nm=name: self._remove_list(nm))
        btns.pack_start(remove, False, False, 0)
        card.pack_start(btns, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, holder, W, H)
        self._rl_layer = layer

    def _close_removelist(self):
        layer = getattr(self, "_rl_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._rl_layer = None
            return True
        return False

    def _remove_list(self, name):
        """Delete a list: reassign its tasks to Inbox (project=None, never
        dropped), drop it from PROJECTS/PROJ_COLOR, rebuild + persist."""
        self._close_removelist()
        if name not in PROJ_COLOR:
            return
        for t in self.tasks:
            if t.get("project") == name:
                t["project"] = None
        # Mutate the module lists in place (do not rebind the globals).
        PROJECTS[:] = [(n, c) for (n, c) in PROJECTS if n != name]
        PROJ_COLOR.pop(name, None)
        if self.view == "proj:" + name:
            self.view = "view:today"
        self._rebuild_sidebar()   # rebuilds the sidebar and refreshes the list
        self._save_tasks()

    def _swatch(self, color, idx):
        button = Gtk.Button(); button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("nlswatch")
        button.set_tooltip_text(_t("Choose colour %d") % (idx + 1))
        da = Gtk.DrawingArea(); da.set_size_request(28, 28)
        da.connect("draw", self._draw_swatch, (color, idx))
        button.add(da)
        button.connect("clicked", self._on_pick_color, idx)
        self._nl_swatches.append(da)
        return button

    def _draw_swatch(self, area, ctx, data):
        color, idx = data
        try:
            w = area.get_allocated_width(); h = area.get_allocated_height()
            cx, cy = w / 2.0, h / 2.0
            rad = min(w, h) / 2.0 - 3
            ctx.arc(cx, cy, rad, 0, 6.2832)
            ctx.set_source_rgb(*nbicons._hex(color)); ctx.fill()
            if idx == getattr(self, "_nl_color_idx", 0):
                ctx.set_source_rgb(*nbicons._hex("#1A1916"))
                ctx.set_line_width(2)
                ctx.arc(cx, cy, rad + 2.5, 0, 6.2832); ctx.stroke()
        except Exception:
            pass
        return False

    def _on_pick_color(self, _btn, idx):
        # `clicked` handler, so no event argument and no return value to stop
        # propagation with -- the old `button-press-event` signature had both.
        self._nl_color_idx = idx
        for da in getattr(self, "_nl_swatches", []):
            da.queue_draw()

    def _nl_create(self, *_):
        try:
            name = self._nl_entry.get_text().strip()
        except Exception:
            name = ""
        if not name:
            try:
                self._nl_entry.grab_focus()
            except Exception:
                pass
            return
        if name not in PROJ_COLOR:
            color = LIST_COLORS[self._nl_color_idx % len(LIST_COLORS)]
            PROJECTS.append((name, color))
            PROJ_COLOR[name] = color
        self._close_newlist()
        self._rebuild_sidebar()
        self._save_tasks()
        self._on_select(None, "proj:" + name)

    def _rebuild_sidebar(self):
        old = getattr(self, "_sidebar", None)
        self._sidebar = self._build_sidebar()
        if old is not None:
            try:
                self._body.remove(old)
            except Exception:
                pass
        self._body.pack_start(self._sidebar, False, False, 0)
        self._body.reorder_child(self._sidebar, 0)
        self._sidebar.show_all()
        self._refresh()

    def _side_row(self, vid, label, glyph=None, dotcolor=None):
        btn = Gtk.Button(); btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("siderow")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        if glyph:
            img = nbicons.image(glyph, 18, "#3A362E")
            row.pack_start(img, False, False, 0)
        else:
            dot = Gtk.DrawingArea(); dot.set_size_request(11, 11)
            dot.connect("draw", self._draw_dot, dotcolor)
            dot.set_valign(Gtk.Align.CENTER)
            row.pack_start(dot, False, False, 0)

        name = Gtk.Label(label=label, xalign=0)
        name.get_style_context().add_class("siderowlabel")
        # A long list name ellipsizes inside the fixed-width sidebar instead of
        # stretching the chrome.
        name.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(name, True, True, 0)

        count = Gtk.Label(label="0")
        count.get_style_context().add_class("sidecount")
        row.pack_end(count, False, False, 0)

        btn.add(row)
        btn.connect("clicked", self._on_select, vid)
        self._side_rows[vid] = (btn, count)
        return btn

    def _draw_dot(self, area, ctx, color):
        try:
            r, g, b = nbicons._hex(color)
            ctx.set_source_rgb(r, g, b)
            w = area.get_allocated_width(); h = area.get_allocated_height()
            d = min(w, h)
            ctx.arc(w / 2, h / 2, d / 2, 0, 6.2832)
            ctx.fill()
        except Exception:
            # A bad colour string or cairo error must not escape the draw
            # handler; simply skip the dot.
            pass
        return False

    def _on_select(self, _btn, vid):
        self.view = vid
        self._refresh()

    # ----------------------------------------------------------------- center
    def _build_center(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("centercol")
        col.set_hexpand(True)

        # header
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.set_margin_top(34); head.set_margin_bottom(22)
        head.set_margin_start(48); head.set_margin_end(48)

        # Eyebrow row: the section name on the left, the running count on the
        # right. The count used to sit on the TITLE row, where at any width
        # below ~1200px it collided with the 34px date/list name; up here it has
        # a line of its own and the title gets the full column.
        eyerow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.eyebrow = Gtk.Label(label="", xalign=0)
        self.eyebrow.get_style_context().add_class("eyebrow")
        eyerow.pack_start(self.eyebrow, False, False, 0)
        self.remaining = Gtk.Label(label="", xalign=1)
        self.remaining.get_style_context().add_class("remaining")
        # This slot also carries the File-menu flash messages ("Saved
        # tasks.json"), whose length varies with the file name — ellipsize so a
        # long name can never widen the window.
        self.remaining.set_ellipsize(Pango.EllipsizeMode.END)
        self.remaining.set_max_width_chars(28)
        eyerow.pack_end(self.remaining, False, False, 0)
        head.pack_start(eyerow, False, False, 0)

        titlerow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        titlerow.set_margin_top(8)
        self.title_lbl = Gtk.Label(label="", xalign=0)
        self.title_lbl.get_style_context().add_class("viewtitle")
        # The title is live text — today's date, or a user list name of any
        # length. Unellipsized it set the window's MINIMUM width, so a list
        # called "Kitchen renovation and general household repairs" pushed that
        # minimum past 1600px and put the right-hand third of the app off a
        # 1024- or 1366-wide panel, where GTK cannot shrink the window to fit.
        self.title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        titlerow.pack_start(self.title_lbl, True, True, 0)
        head.pack_start(titlerow, False, False, 0)
        col.pack_start(head, False, False, 0)

        # quick add
        add = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        add.get_style_context().add_class("quickadd")
        add.set_margin_start(48); add.set_margin_end(48)
        add.set_margin_bottom(8)
        plus = nbicons.image("plus", 18, MUTED)
        plus.set_margin_start(16)
        add.pack_start(plus, False, False, 0)
        self.draft = Gtk.Entry()
        self.draft.set_has_frame(False)
        self.draft.get_style_context().add_class("draftentry")
        self.draft.set_placeholder_text(_t("Add task"))
        self.draft.set_tooltip_text(
            _t("Task name, then Enter. Optional: a time (14:30), a list "
               "(#Home), a day (>tomorrow, >friday), a priority (! or !!)."))
        self.draft.connect("activate", self._on_add)
        add.pack_start(self.draft, True, True, 0)
        col.pack_start(add, False, False, 0)

        # list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.listbox.set_margin_top(14); self.listbox.set_margin_bottom(60)
        self.listbox.set_margin_start(48); self.listbox.set_margin_end(48)
        scroll.add(self.listbox)
        col.pack_start(scroll, True, True, 0)
        return col

    def _on_add(self, entry):
        raw = entry.get_text().strip()
        if not raw:
            return
        title, p_proj, p_due, p_date, p_time, p_prio = self._parse_quickadd(raw)
        if not title:
            return
        # Defaults come from the active view; the quick-add tokens override them.
        # No list is assigned by default (tasks are unfiled unless a list view is
        # active or a #List token is given) — nothing fabricated.
        #
        # A task filed under Today or Tomorrow gets that REAL day, so it moves
        # with the calendar instead of sitting under a heading that stops being
        # true the next morning. Inbox and the undated groups stay dateless.
        v = self.view
        if v.startswith("proj:"):
            d_proj, d_due, d_date = v[5:], "today", _iso(_today())
        elif v == "view:upcoming":
            d_proj, d_due = None, "tomorrow"
            d_date = _iso(_today() + timedelta(days=1))
        elif v == "view:inbox":
            d_proj, d_due, d_date = None, "inbox", ""
        else:
            d_proj, d_due, d_date = None, "today", _iso(_today())
        self.tasks.append({
            "title": title,
            "project": p_proj if p_proj is not None else d_proj,
            "due": p_due if p_due is not None else d_due,
            "date": p_date if p_due is not None else d_date,
            "time": p_time or "", "prio": p_prio, "done": False})
        entry.set_text("")
        self._save_tasks()
        self._refresh()

    @staticmethod
    def _due_token(word):
        """Resolve one '>…' quick-add token to (group, ISO day). A weekday name
        ('>friday', '>fri') resolves to the NEXT such day — today when today is
        that day — so "bins >thu" lands on the right Thursday without anyone
        working out the date. 'today'/'tomorrow'/'week' get their real day too;
        the dateless groups (anytime, inbox) keep no day. Returns (None, None)
        when the token is not a day at all."""
        key = word.lower()
        now = _today()
        if key in DAY_TOKENS:
            ahead = (DAY_TOKENS[key] - now.weekday()) % 7
            return "today", _iso(now + timedelta(days=ahead))
        gid = GROUP_TOKENS.get(key)
        if gid == "today":
            return "today", _iso(now)
        if gid == "tomorrow":
            return "tomorrow", _iso(now + timedelta(days=1))
        if gid == "week":
            return "week", _iso(now + timedelta(days=7))
        if gid is not None:
            return gid, ""
        return None, None

    def _parse_quickadd(self, raw):
        """Pull optional metadata out of the quick-add text so a task can be
        filed, dated, timed and flagged as it is typed. Recognised tokens
        (surfaced in the entry tooltip):
            #List     a known project by name or prefix   -> project
            HH:MM     a time-of-day                       -> time
            >when     today/tomorrow/friday/week/…        -> due day
            ! / !!    priority                            -> ink flag / red high
        Everything else is the title. Returns (title, project, due, date, time,
        prio) where project/due/time are None when unspecified."""
        project = due = time_note = None
        day = ""
        prio = 0
        keep = []
        for w in raw.split():
            low = w.lower()
            if len(w) > 1 and w[0] == "#":
                key = low[1:]
                match = next((n for n, _ in PROJECTS if n.lower().startswith(key)),
                             None)
                if match:
                    project = match
                    continue
            if len(w) > 1 and w[0] == ">":
                group, iso = self._due_token(low[1:])
                if group is not None:
                    due, day = group, iso
                    continue
            if w in ("!", "!!", "!!!"):
                prio = min(2, len(w))
                continue
            if re.match(r"^\d{1,2}:\d{2}$", w):
                # Only a REAL time-of-day becomes the task's time. The HH:MM
                # shape alone accepts "9:75" or "25:99", which then showed on
                # the task as an impossible clock time (the same trap the
                # schedule's add-event row already guards). A bad token stays
                # part of the title, where the user can see and correct it.
                hh, mm = (int(x) for x in w.split(":"))
                if 0 <= hh < 24 and 0 <= mm < 60:
                    time_note = "%02d:%02d" % (hh, mm)
                    continue
            keep.append(w)
        return " ".join(keep).strip(), project, due, day, time_note, prio

    def _view_dues(self, view):
        """Which due groups a built-in view shows. Today carries the work that
        is due now — Overdue, Today, and the undated Anytime tail; Upcoming is
        everything with a day still ahead of it. Kept in one place so the list
        scope and the sidebar counts can never drift apart.

        'Later' sits under Upcoming rather than under Today: now that a dated
        task is grouped from its real day, Later means "more than a week away",
        which is the definition of upcoming, not of today."""
        if view == "view:today":
            return ("overdue", "today", "anytime")
        if view == "view:upcoming":
            return ("tomorrow", "week", "later")
        if view == "view:inbox":
            return ("inbox",)
        return ()

    def _scoped(self):
        out = []
        v = self.view
        if v.startswith("proj:"):
            name = v[5:]
            for i, t in enumerate(self.tasks):
                if t["project"] == name:
                    out.append((i, t))
        else:
            dues = self._view_dues(v)
            for i, t in enumerate(self.tasks):
                if self._due_of(t) in dues:
                    out.append((i, t))
        return out

    def _toggle(self, _btn, idx):
        # Ticking a checkbox flips only this task's `done`; it never changes list
        # membership, grouping or ordering, so a full _refresh() (which tears
        # down and rebuilds every group header + row, each with fresh
        # DrawingAreas — visibly slow under swrast) is wasted work. Mutate the
        # model, persist, then update just this row's widgets in place plus the
        # running counts. Structural ops (add/delete/quick-add/view switch) keep
        # using _refresh().
        self.undo.checkpoint("Complete Task" if not self.tasks[idx]["done"]
                             else "Reopen Task")
        done = not self.tasks[idx]["done"]
        self.tasks[idx]["done"] = done
        self._save_tasks()
        self.undo.commit()
        handles = getattr(self, "_rows", {}).get(idx)
        if handles is None:
            # No cached widgets for this idx (shouldn't happen — the clicked row
            # is always on screen — but stay correct): fall back to a rebuild.
            self._refresh()
            return
        chk = handles.get("check")
        if chk is not None:
            chk.queue_draw()   # _draw_check re-reads the live task dict
        title = handles.get("title")
        if title is not None:
            tctx = title.get_style_context()
            if done:
                tctx.remove_class("tasktitle"); tctx.add_class("taskdone")
            else:
                tctx.remove_class("taskdone"); tctx.add_class("tasktitle")
        flag = handles.get("flag")
        if flag is not None:
            flag.set_visible(not done)   # priority flag hides on a done task
        self._update_counts()

    # ------------------------------------------------- task actions (row menu)
    def _on_task_press(self, widget, ev, idx):
        """Right-click a task to rename or delete it. A left-click falls through
        (return False) to the button's own 'clicked' handler — the done toggle —
        so the primary gesture is unchanged."""
        try:
            if ev.button == 3:
                xy = widget.translate_coordinates(
                    self._overlay, int(ev.x), int(ev.y))
                x, y = xy if xy is not None else (int(ev.x), int(ev.y))
                self._open_task_menu(idx, x, y)
                return True
        except Exception:
            pass
        return False

    def _open_task_menu(self, idx, x, y):
        """A small right-click menu for one task: when it is due, Rename… and
        Delete. Drawn as an in-window overlay (the same reliable no-compositor
        approach as the base dropdowns), positioned at the click point and
        clamped on-screen.

        The reschedule rows are the point of the menu: before them, a task filed
        under Tomorrow could only be moved by deleting it and typing it again."""
        self._close_task_menu()
        if not (0 <= idx < len(self.tasks)):
            return
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        menu.get_style_context().add_class("nbmenu")

        def additem(label, cb, cls=None):
            it = Gtk.Button(label=label); it.set_relief(Gtk.ReliefStyle.NONE)
            it.get_style_context().add_class("nbmenu-item")
            if cls:
                it.get_style_context().add_class(cls)
            try:
                it.get_child().set_xalign(0.0)
            except Exception:
                pass
            it.connect("clicked",
                       lambda _b, fn=cb: (self._close_task_menu(), fn()))
            menu.pack_start(it, False, False, 0)

        def addsep():
            s = Gtk.Box(); s.get_style_context().add_class("nbmenu-sep")
            menu.pack_start(s, False, False, 0)

        # No "currently selected" marker on these four: the group heading the
        # task sits under already says which day it is on, and a marker would
        # have to carry padding into the label — which the catalogs cannot key
        # on, leaving these rows the only English left on a translated menu.
        now = _today()
        for label, group, day in (
                ("Today", "today", _iso(now)),
                ("Tomorrow", "tomorrow", _iso(now + timedelta(days=1))),
                ("Next week", "week", _iso(now + timedelta(days=7))),
                ("Anytime", "anytime", "")):
            additem(label,
                    lambda i=idx, g=group, d=day: self._reschedule(i, g, d))
        addsep()
        additem("Rename…", lambda i=idx: self._open_rename(i))
        additem("Delete task", lambda i=idx: self._delete_task(i),
                "taskmenu-del")

        W, H = self._surface_size()
        layer = Gtk.Fixed()
        # No dimming for a context menu (the base window's own dropdowns don't
        # dim either) — the scrim here only catches the click-away.
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_task_menu(), True)[1])
        layer.put(scrim, 0, 0)
        menu_win = Gtk.EventBox()   # own GdkWindow so it blits over the body
        menu_win.add(menu)
        # Clamp inside the LIVE surface, not a hardcoded 1920x1080: a
        # right-click near the right edge of a 1366- or 1024-wide panel used to
        # open the menu partly off-screen.
        px = max(0, min(int(x), W - 190))
        # Clamp against the menu's MEASURED height (six rows and a rule now,
        # not two), so a right-click low in the list still opens it fully
        # on-screen instead of running off the bottom edge.
        try:
            _min, nat = menu.get_preferred_size()
            mh = max(110, nat.height + 10)
        except Exception:
            mh = 240
        py = max(46, min(int(y), max(46, H - mh)))
        layer.put(menu_win, px, py)
        self._overlay.add_overlay(layer)
        layer.show_all()
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            mw = menu_win.get_window()
            if mw is not None:
                mw.raise_()
        except Exception:
            pass
        self._tm_layer = layer

    def _close_task_menu(self):
        layer = getattr(self, "_tm_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._tm_layer = None
            return True
        return False

    def _reschedule(self, idx, group, day):
        """Move one task to another day from the row menu. Writes BOTH the real
        day (so it keeps rolling forward on its own) and the group id (so a
        dateless choice like Anytime still lands somewhere sensible), persists,
        and re-files the list. Un-ticks a completed task that is being given a
        new day — putting a finished task back on the calendar means it is to be
        done again, and leaving it struck through would hide it in plain sight."""
        self._close_task_menu()
        if not (0 <= idx < len(self.tasks)):
            return
        t = self.tasks[idx]
        t["due"] = group
        t["date"] = day
        if day and t.get("done"):
            t["done"] = False
        self._save_tasks()
        self._refresh()

    def _delete_task(self, idx):
        """Remove one task outright. Reachable only via the deliberate
        right-click ▸ Delete gesture, so it needs no extra confirm card. Persists
        and refreshes; the shared flat file is rewritten so the desktop widget
        drops it too."""
        self._close_task_menu()
        if not (0 <= idx < len(self.tasks)):
            return
        self.undo.checkpoint("Delete Task")
        del self.tasks[idx]
        self._save_tasks()
        self.undo.commit()
        self._refresh()

    def _open_rename(self, idx):
        """Edit a task's text in place via a small overlay card (same idiom as
        New List). Pre-fills the current title and selects it so it can be
        overtyped at once; Enter or Save commits, Esc / Cancel backs out."""
        self._close_task_menu()
        if not (0 <= idx < len(self.tasks)):
            return
        self._close_rename()
        self._rn_idx = idx

        W, H = self._surface_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_rename(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nlcard")
        title = Gtk.Label(label=_t("Rename Task"), xalign=0)
        title.get_style_context().add_class("nltitle")
        card.pack_start(title, False, False, 0)

        self._rn_entry = Gtk.Entry()
        self._rn_entry.get_style_context().add_class("nlentry")
        self._rn_entry.set_width_chars(30)
        self._rn_entry.set_text(self.tasks[idx].get("title", ""))
        self._rn_entry.connect("activate", self._rename_commit)
        card.pack_start(self._rn_entry, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_margin_top(6); btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nlbtn")
        cancel.connect("clicked", lambda *a: self._close_rename())
        btns.pack_start(cancel, False, False, 0)
        save = Gtk.Button(label=_t("Save"))
        save.set_relief(Gtk.ReliefStyle.NONE)
        save.get_style_context().add_class("nlbtn")
        save.get_style_context().add_class("nlcreate")
        save.connect("clicked", self._rename_commit)
        btns.pack_start(save, False, False, 0)
        card.pack_start(btns, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, holder, W, H)
        self._rn_layer = layer
        self._rn_entry.grab_focus()
        try:
            self._rn_entry.select_region(0, -1)
        except Exception:
            pass

    def _close_rename(self):
        layer = getattr(self, "_rn_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._rn_layer = None
            return True
        return False

    def _rename_commit(self, *_):
        idx = getattr(self, "_rn_idx", None)
        try:
            text = self._rn_entry.get_text().strip()
        except Exception:
            text = ""
        if idx is None or not (0 <= idx < len(self.tasks)):
            self._close_rename()
            return
        if not text:
            # An empty title is not a valid task — keep the card open with the
            # caret in the field rather than blanking the task.
            try:
                self._rn_entry.grab_focus()
            except Exception:
                pass
            return
        self.tasks[idx]["title"] = text
        self._close_rename()
        self._save_tasks()
        self._refresh()

    # --------------------------------------------------------- clear completed
    def _open_clearcard(self):
        """Confirm removing every finished task (a batch delete, so it confirms
        first — unlike the single right-click ▸ Delete). Reuses the New List
        overlay approach. Does nothing if nothing is completed."""
        try:
            self._close_menu()
        except Exception:
            pass
        self._close_task_menu()
        self._close_rename()
        self._close_newlist()
        self._close_removelist()
        self._close_clearcard()
        n = sum(1 for t in self.tasks if t.get("done"))
        if n == 0:
            return

        W, H = self._surface_size()
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_clearcard(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("nlcard")
        title = Gtk.Label(label=_t("Clear Completed"), xalign=0)
        title.get_style_context().add_class("nltitle")
        card.pack_start(title, False, False, 0)

        # ASK, don't announce. Every other confirm in this app opens with a
        # question ("Remove the list “%s”? …"), and a card that states what is
        # about to happen reads as a notification of something already decided
        # — next to a Cancel button that says otherwise.
        msg = ("Remove %d completed task%s? This cannot be undone."
               % (n, "" if n == 1 else "s"))
        body = Gtk.Label(label=msg, xalign=0)
        body.get_style_context().add_class("nlbody")
        body.set_line_wrap(True)
        body.set_max_width_chars(30)
        card.pack_start(body, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_margin_top(6); btns.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("nlbtn")
        cancel.connect("clicked", lambda *a: self._close_clearcard())
        btns.pack_start(cancel, False, False, 0)
        remove = Gtk.Button(label=_t("Remove"))
        remove.set_relief(Gtk.ReliefStyle.NONE)
        remove.get_style_context().add_class("nlbtn")
        remove.get_style_context().add_class("nlremove")
        remove.connect("clicked", lambda *a: self._clear_completed())
        btns.pack_start(remove, False, False, 0)
        card.pack_start(btns, False, False, 0)

        holder = Gtk.EventBox()   # own GdkWindow so it blits over the app body
        holder.add(card)
        layer.put(holder, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        self._centre_card(layer, holder, W, H)
        self._cc_layer = layer

    def _close_clearcard(self):
        layer = getattr(self, "_cc_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._cc_layer = None
            return True
        return False

    def _clear_completed(self):
        """Drop every done task (kept non-done ones untouched), persist, refresh.
        Reached only through the confirm card above."""
        self._close_clearcard()
        self.undo.checkpoint("Clear Completed")
        self.tasks = [t for t in self.tasks if not t.get("done")]
        self._save_tasks()
        self.undo.commit()
        self._refresh()

    def _do_refresh(self):
        """View ▸ Refresh: re-read the Calendar app's shared event store so a
        meeting added/edited/removed in Calendar shows up here, repaint the
        schedule rail and mini-calendar, then rebuild the task list. Without the
        event reload, 'Refresh' would only redraw stale in-memory data."""
        try:
            self.events = self._load_events()
            self._populate_events()
            self._evbox.show_all()
            self._refresh_calendar()
        except Exception:
            # A schedule repaint must never break the menu action; the task
            # list still refreshes below.
            pass
        self._refresh()

    # ------------------------------------------------------------------- rail
    def _build_rail(self):
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rail.get_style_context().add_class("rail")
        rail.set_size_request(344, -1)   # see the sidebar note on rail widths

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("railhead")
        head.set_margin_top(34); head.set_margin_bottom(18)
        head.set_margin_start(32); head.set_margin_end(32)
        eb = Gtk.Label(label=_t("Schedule"), xalign=0)
        eb.get_style_context().add_class("eyebrow")
        head.pack_start(eb, False, False, 0)
        t = Gtk.Label(label=_t("Today’s events"), xalign=0)
        t.get_style_context().add_class("railtitle")
        t.set_margin_top(6)
        head.pack_start(t, False, False, 0)
        rail.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._evbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._evbox.set_margin_top(8); self._evbox.set_margin_bottom(20)
        self._evbox.set_margin_start(32); self._evbox.set_margin_end(32)
        self._populate_events()
        scroll.add(self._evbox)
        rail.pack_start(scroll, True, True, 0)

        self._cal_rail = rail
        self._cal_widget = self._mini_calendar()
        self._cal_day = time.localtime()[:3]
        rail.pack_start(self._cal_widget, False, False, 0)
        # Poll the local date each minute and re-render only on an actual date
        # change, so it stays cheap. The source id is kept so close can remove
        # it: an anonymous timeout holds a reference to this window forever, so
        # every Tasks the user ever closed would keep waking each minute and,
        # on the next date change, rebuild a mini-calendar, event list and main
        # view whose widgets are already destroyed. The rail is built exactly
        # once (from __init__), but drop any recorded source first so a future
        # rebuild can never leak the previous minute timer.
        prior = getattr(self, "_day_rollover_id", 0)
        self._day_rollover_id = 0
        if prior:
            try:
                GLib.source_remove(prior)
            except Exception:
                pass
        self._day_rollover_id = GLib.timeout_add_seconds(
            60, self._check_day_rollover)
        return rail

    def _populate_events(self):
        """(Re)fill the schedule list with today's events, sorted by start time,
        followed by the always-present inline add-event row. An empty schedule
        shows the 'No events' empty-state above the add row."""
        for c in self._evbox.get_children():
            self._evbox.remove(c)
        now = time.localtime()
        today = (now.tm_year, now.tm_mon, now.tm_mday)
        # ymd is the (y, m, d) tuple of a Calendar event; keep only today's (a
        # defensive None — no dated record — reads as today's).
        todays = [e for e in self.events if e.get("ymd") in (None, today)]
        todays.sort(key=self._event_sortkey)
        if not todays:
            # A bare "No events" leaves the reader with nowhere to go. The
            # inline add row sits directly beneath this label, so point at it —
            # in its own words ("Add event" is its placeholder) — and say what
            # the field expects, which is not obvious from an empty box.
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            empty.set_margin_top(12)
            head = Gtk.Label(label=_t("No events"), xalign=0)
            head.get_style_context().add_class("emptytext")
            hint = Gtk.Label(
                label=_t("Add one in the box below."),
                xalign=0)
            hint.get_style_context().add_class("emptyhint")
            hint.set_line_wrap(True)
            hint.set_max_width_chars(26)
            hint.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            empty.pack_start(head, False, False, 0)
            empty.pack_start(hint, False, False, 0)
            self._evbox.pack_start(empty, False, False, 0)
        else:
            for ev in todays:
                self._evbox.pack_start(self._event_row(ev), False, False, 0)
        self._evbox.pack_start(self._event_add_row(), False, False, 0)

    def _event_sortkey(self, ev):
        m = re.match(r"^(\d{1,2}):(\d{2})$", ev.get("start", "") or "")
        if m:
            return (0, int(m.group(1)) * 60 + int(m.group(2)))
        return (1, 0)   # timeless events sort to the end

    def _event_add_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("eventadd")
        try:
            ico = nbicons.image("plus", 14, MUTED)
            row.pack_start(ico, False, False, 0)
        except Exception:
            pass
        e = Gtk.Entry(); e.set_has_frame(False)
        e.get_style_context().add_class("eventaddentry")
        e.set_placeholder_text(_t("Add event"))
        e.set_tooltip_text(_t("Optional HH:MM, then the event name"))
        e.connect("activate", self._on_add_event)
        row.pack_start(e, True, True, 0)
        return row

    def _on_add_event(self, entry):
        """Add an event straight into the Calendar app's shared store, so it
        appears in this rail AND in Calendar. Input is 'HH:MM Title' — the
        leading time is optional (defaults to 09:00) and everything after it is
        the event name. A name is required: a time-only entry is left in place to
        be completed rather than filing a placeholder event into the shared
        store. Written in Calendar's own {date, start, end, title, cal} schema so
        it round-trips through its New Event / edit / delete dialog."""
        raw = entry.get_text().strip()
        if not raw:
            return
        start = 9.0
        parts = raw.split(None, 1)
        if parts and re.match(r"^\d{1,2}:\d{2}$", parts[0]):
            hh, mm = (int(x) for x in parts[0].split(":"))
            # Only consume the leading token as a time if it's a REAL time —
            # the HH:MM shape alone would accept "9:75" or "25:99" and file an
            # event at an impossible hour (start > 24). A bad token stays part
            # of the title (visible), and the event defaults to 09:00.
            if 0 <= hh < 24 and 0 <= mm < 60:
                start = hh + mm / 60.0
                raw = parts[1].strip() if len(parts) > 1 else ""
        title = raw.strip()
        if not title:
            # Nothing but a time was entered; keep it in the field so a name can
            # be added instead of writing an untitled placeholder to Calendar.
            return
        now = time.localtime()
        record = {
            "date": "%04d-%02d-%02d" % (now.tm_year, now.tm_mon, now.tm_mday),
            "start": start, "end": start + 1.0,
            "title": title, "cal": "Personal"}
        if not self._append_calendar_event(record):
            # The calendar store could not be read, so it was left untouched
            # (see _append_calendar_event). Keep what was typed in the field —
            # clearing it would lose the user's words as well as the event.
            return
        entry.set_text("")
        # Re-read the shared store so the new event (and any other Calendar
        # change) is reflected, then repaint the rail + mini-calendar dots.
        self.events = self._load_events()
        self._populate_events()
        self._refresh_calendar()
        self._evbox.show_all()

    def _append_calendar_event(self, record):
        """Read-modify-write one event into the Calendar app's store
        (calendar.json). Creates the file/dir if Calendar has never run. Never
        raises — a schedule add must never crash Tasks. Returns True when the
        event was actually written.

        REFUSES to write over a store it could not read. This is a wholesale
        rewrite of ANOTHER app's file: when the read came back empty because the
        store was shaped in a way this app did not recognise, appending to [] and
        writing replaced the user's entire calendar with the one event just
        typed. Tasks owns tasks, not the calendar, and must never be the thing
        that destroys it. Missing, empty, or not-JSON-at-all is not this case —
        the first two start a fresh store, and the third is quarantined by
        nbapp.atomic_write_json before anything replaces it."""
        data = self._read_calendar()
        if data is None:
            try:
                with open(CAL_FILE) as fh:
                    json.load(fh)
                return False       # parses, but is not a store we understand
            except (OSError, ValueError):
                data = []          # missing / unreadable -> safe to start one
        data.append(record)
        try:
            nbapp.atomic_write_json(CAL_FILE, data)
            return True
        except Exception:
            return False

    def _refresh_calendar(self):
        """Swap the mini month calendar for a freshly built one (new event dots,
        current 'today' pill). Reused by add-event and the day-rollover check."""
        try:
            self._cal_rail.remove(self._cal_widget)
        except Exception:
            pass
        self._cal_widget = self._mini_calendar()
        self._cal_rail.pack_start(self._cal_widget, False, False, 0)
        self._cal_widget.show_all()

    def _check_day_rollover(self):
        # Rebuild the mini month calendar when the local date changes so the red
        # "today" pill tracks the real day instead of the boot day.
        # A closed window drops the poll entirely (return False): _cal_day is
        # not updated and nothing is re-rendered, because its widgets are gone.
        if getattr(self, "_closed", False):
            return False
        try:
            d = time.localtime()[:3]
            if d != getattr(self, "_cal_day", d):
                self._cal_day = d
                self._refresh_calendar()
                # The schedule rail is "today's" events, so re-file it too.
                self._populate_events()
                self._evbox.show_all()
                # Refresh the main view too so its header date tracks the new
                # day. Runs only on an actual date change, so it stays cheap.
                self._refresh()
        except Exception:
            pass
        return True   # keep checking every minute

    def _event_row(self, ev):
        # Matches the mockup: a right-aligned time column, a coloured timeline
        # marker (ring + connector), then a paper card with the event's colour
        # as a left bar.
        color = ev.get("color", EVENT_NEUTRAL)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.get_style_context().add_class("eventrow")
        row.set_margin_bottom(14)

        tcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tcol.set_size_request(52, -1)
        tcol.set_valign(Gtk.Align.START)
        tlab = Gtk.Label(label=ev.get("start", ""), xalign=1)
        tlab.get_style_context().add_class("eventtime")
        tcol.pack_start(tlab, False, False, 0)
        dur = ev.get("dur")
        if dur:
            dl = Gtk.Label(label=dur, xalign=1)
            dl.get_style_context().add_class("eventdur")
            tcol.pack_start(dl, False, False, 0)
        row.pack_start(tcol, False, False, 0)

        # timeline marker — one DrawingArea so the connector line always fills
        # the (card-driven) row height, whatever the card's content height is.
        marker = Gtk.DrawingArea(); marker.set_size_request(12, -1)
        marker.connect("draw", self._draw_marker, color)
        row.pack_start(marker, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        card.get_style_context().add_class("eventcard")
        bar = Gtk.DrawingArea(); bar.set_size_request(3, -1)
        bar.connect("draw", self._draw_vrule, color)
        card.pack_start(bar, False, False, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_margin_top(12); content.set_margin_bottom(12)
        content.set_margin_start(14); content.set_margin_end(16)
        content.set_valign(Gtk.Align.CENTER)
        et = Gtk.Label(label=ev.get("title", ""), xalign=0)
        et.get_style_context().add_class("eventtitle")
        et.set_line_wrap(True); et.set_max_width_chars(26)
        # max_width_chars caps where the text WRAPS, not how narrow the label
        # can get: a wrapping label's minimum width is its widest WORD, and
        # WORD mode refuses to break inside one. A single 34-character token —
        # ordinary in German, Dutch or Finnish, and in any filename-style title
        # — reported 1030px of minimum on its own and pushed the whole app off a
        # 1024px panel. WORD_CHAR keeps normal titles breaking at spaces but
        # lets a monster word break mid-word, so the minimum is one character.
        et.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        content.pack_start(et, False, False, 0)
        where = (ev.get("where") or "").strip()
        if where:
            es = Gtk.Label(label=where, xalign=0)
            es.get_style_context().add_class("eventsub")
            es.set_margin_top(3)
            # Same mechanism, less protection: this one had no wrap at ALL, so
            # its whole string was the minimum width. A place name is free text
            # from the Calendar app and can be any length.
            es.set_line_wrap(True); es.set_max_width_chars(26)
            es.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            content.pack_start(es, False, False, 0)
        card.pack_start(content, True, True, 0)
        row.pack_start(card, True, True, 0)
        return row

    def _draw_marker(self, area, ctx, color):
        """The schedule timeline marker: a coloured ring near the top with a
        thin connector running to the bottom of the row. Crash-safe."""
        try:
            w = area.get_allocated_width(); h = area.get_allocated_height()
            cx = w / 2.0
            cy, rad = 9.0, 4.5
            ctx.set_source_rgb(*nbicons._hex("#D7D2C5"))
            ctx.set_line_width(1)
            ctx.move_to(cx, cy + rad + 1)
            ctx.line_to(cx, h)
            ctx.stroke()
            ctx.arc(cx, cy, rad, 0, 6.2832)
            ctx.set_source_rgb(*nbicons._hex("#FCFBF8"))
            ctx.fill()
            ctx.arc(cx, cy, rad, 0, 6.2832)
            ctx.set_source_rgb(*nbicons._hex(color))
            ctx.set_line_width(GLYPH_STROKE)
            ctx.stroke()
        except Exception:
            pass
        return False

    def _draw_vrule(self, area, ctx, color):
        try:
            r, g, b = nbicons._hex(color)
            ctx.set_source_rgb(r, g, b)
            w = area.get_allocated_width(); h = area.get_allocated_height()
            ctx.rectangle(0, 0, w, h)
            ctx.fill()
        except Exception:
            pass
        return False

    def _marked_days(self, now):
        """Which day-of-month numbers get an event dot in the mini-calendar:
        the days of the CURRENTLY shown month (now.tm_year/tm_mon) that carry a
        Calendar event."""
        marked = set()
        for ev in self.events:
            ymd = ev.get("ymd")
            if ymd and ymd[0] == now.tm_year and ymd[1] == now.tm_mon:
                marked.add(ymd[2])
        return marked

    def _mini_calendar(self):
        now = time.localtime()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.get_style_context().add_class("minical")
        box.set_margin_top(20); box.set_margin_bottom(26)
        box.set_margin_start(32); box.set_margin_end(32)

        title = Gtk.Label(label=_t("%s %d" % (MONTHS[now.tm_mon - 1], now.tm_year)),
                          xalign=0)
        title.get_style_context().add_class("minititle")
        title.set_margin_bottom(12)
        box.pack_start(title, False, False, 0)

        grid = Gtk.Grid(); grid.set_column_homogeneous(True)
        for c, w in enumerate(WEEKDAYS):
            lbl = Gtk.Label(label=w)
            lbl.get_style_context().add_class("caldow")
            grid.attach(lbl, c, 0, 1, 1)

        # first weekday of month (Monday=0), days in month — computed locally
        # because `import calendar` here would pick up this DE dir's own
        # calendar.py (on PYTHONPATH), which has no monthrange().
        from datetime import date
        try:
            _first = date(now.tm_year, now.tm_mon, 1)
            _nxt = (date(now.tm_year + 1, 1, 1) if now.tm_mon == 12
                    else date(now.tm_year, now.tm_mon + 1, 1))
            lead, dim = _first.weekday(), (_nxt - _first).days
        except Exception:
            # A wildly out-of-range system clock (e.g. a dead RTC reporting
            # year 9999) must not break the calendar; fall back to a plain
            # 30-day grid starting on Monday.
            lead, dim = 0, 30
        # Dotted days = the days of THIS month that carry a Calendar event
        # (shared store), so the dots track Calendar exactly.
        marked = self._marked_days(now)
        r, cpos = 1, lead
        for d in range(1, dim + 1):
            cell = Gtk.Box(); cell.set_size_request(-1, 30)
            cell.set_halign(Gtk.Align.CENTER); cell.set_valign(Gtk.Align.CENTER)
            pill = Gtk.Label(label=str(d))
            if d == now.tm_mday:
                pill.get_style_context().add_class("caltoday")
            elif d in marked:
                pill.get_style_context().add_class("calmarked")
            else:
                pill.get_style_context().add_class("calday")
            cell.pack_start(pill, True, True, 0)
            # A month grid invites clicks (the red "today" pill especially), so
            # each day is a live cell. A bare Gtk.Box gets no pointer events, so
            # wrap it in a windowless EventBox — no visual change, but the click
            # now lands. Jumps to the task view that matches the picked day.
            daycell = Gtk.EventBox()
            daycell.set_visible_window(False)
            daycell.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            daycell.connect("button-press-event", self._on_cal_day, d)
            daycell.add(cell)
            grid.attach(daycell, cpos, r, 1, 1)
            cpos += 1
            if cpos > 6:
                cpos = 0; r += 1
        box.pack_start(grid, False, False, 0)
        return box

    def _on_cal_day(self, _widget, _event, day):
        # Clicking a calendar day selects the task view that best matches it:
        # a future date -> Upcoming, today or a past date -> Today (which also
        # carries the overdue group). Both are existing views, so this reuses
        # _on_select and can never desync the app state.
        try:
            today = time.localtime().tm_mday
            target = ("view:upcoming"
                      if isinstance(day, int) and day > today
                      else "view:today")
            self._on_select(None, target)
        except Exception:
            # A calendar click must never crash the app; a bad clock or a
            # missing view id simply leaves the current view in place.
            pass
        return True

    # ---------------------------------------------------------------- refresh
    def _update_counts(self, scoped=None):
        """Recompute the header 'N remaining' and the sidebar per-list counts
        and write them onto the EXISTING labels — no widget teardown. Shared by
        the full refresh and the in-place toggle so the two can never drift."""
        if scoped is None:
            scoped = self._scoped()
        rem = sum(1 for _, t in scoped if not t["done"])
        self.remaining.set_text("%d remaining" % rem)

        def cnt(dues):
            return sum(1 for t in self.tasks
                       if self._due_of(t) in dues and not t["done"])
        counts = {vid: cnt(self._view_dues(vid))
                  for vid in ("view:today", "view:upcoming", "view:inbox")}
        for name, _ in PROJECTS:
            counts["proj:" + name] = sum(
                1 for t in self.tasks
                if t["project"] == name and not t["done"])
        for vid, (btn, clabel) in self._side_rows.items():
            clabel.set_text(str(counts.get(vid, 0)))

    def _refresh(self):
        now = time.localtime()
        # header
        if self.view == "view:today":
            eb, ti = "Today", _display_date(now)
        elif self.view == "view:upcoming":
            # Matches what this view actually holds — the Tomorrow and This
            # Week groups. "The next two weeks" promised a range the view has
            # no bucket for.
            eb, ti = "Upcoming", "The week ahead"
        elif self.view == "view:inbox":
            eb, ti = "Inbox", "Unsorted tasks"
        else:
            eb, ti = "List", self.view[5:]
        self.eyebrow.set_text(eb.upper())
        self.title_lbl.set_text(ti)

        scoped = self._scoped()
        # Header 'N remaining' + sidebar per-list counts (shared with the
        # in-place toggle via _update_counts, so the numbers never drift).
        self._update_counts(scoped)

        # sidebar selection — only changes on a view switch, so it stays in the
        # full refresh rather than on the toggle hot path.
        for vid, (btn, _clabel) in self._side_rows.items():
            ctx = btn.get_style_context()
            if vid == self.view:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

        # task list — rebuilt wholesale here (structural: view switch, add,
        # delete, quick-add). Reset the per-row handle registry so the toggle
        # hot path only holds live widgets for the rows currently shown.
        self._rows = {}
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        if not scoped:
            # Pair the empty label with a how-to-start hint pointing at the
            # always-visible quick-add — every sibling list app gives its
            # primary empty state an action line, not a bare label — and
            # distinguish "nothing anywhere" from "nothing in THIS view" the
            # way cookbook distinguishes a filtered category.
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_top(60); box.set_margin_bottom(60)
            # Branch on a plain `if` so BOTH labels go through _t(): written
            # `_t(a if x else b)` the catalog lookup happens on whichever
            # branch ran, and written without _t() at all — as this was — the
            # app's primary empty state stays English in all 17 languages.
            if not self.tasks:
                head = _t("No tasks")
            else:
                head = _t("No tasks in this view")
            lbl = Gtk.Label(label=head)
            lbl.get_style_context().add_class("emptytext")
            box.pack_start(lbl, False, False, 0)
            hint = Gtk.Label(label=_t("Add one in the box above."))
            hint.get_style_context().add_class("emptysub")
            box.pack_start(hint, False, False, 0)
            self.listbox.pack_start(box, False, False, 0)
        else:
            for due in DUE_ORDER:
                items = [(i, t) for i, t in scoped if self._due_of(t) == due]
                if not items:
                    continue
                self.listbox.pack_start(self._group_head(due, len(items)),
                                        False, False, 0)
                for i, t in items:
                    self.listbox.pack_start(self._task_row(i, t),
                                            False, False, 0)
        self.listbox.show_all()

    def _group_head(self, due, n):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(18); row.set_margin_bottom(6)
        lbl = Gtk.Label(label=DUE_LABELS[due].upper(), xalign=0)
        lbl.get_style_context().add_class(
            "groupover" if due == "overdue" else "grouphead")
        row.pack_start(lbl, False, False, 0)
        line = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        line.get_style_context().add_class("groupline")
        line.set_valign(Gtk.Align.CENTER)
        row.pack_start(line, True, True, 0)
        c = Gtk.Label(label=str(n))
        c.get_style_context().add_class("groupcount")
        row.pack_end(c, False, False, 0)
        return row

    def _task_row(self, idx, t):
        btn = Gtk.Button(); btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("taskrow")
        btn.connect("clicked", self._toggle, idx)
        btn.connect("button-press-event", self._on_task_press, idx)
        btn.set_tooltip_text(
            _t("Complete task · right-click to move it to another day"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        chk = Gtk.DrawingArea(); chk.set_size_request(22, 22)
        # Aligned to the FIRST line of the title, not to the middle of the row:
        # a long title wraps to three or four lines, and a centred box floated
        # halfway down the row beside line two with nothing to align to. The box
        # (22px) and the 16px title line are near enough the same height that
        # START lines them up.
        chk.set_valign(Gtk.Align.START)
        chk.connect("draw", self._draw_check, t)
        row.pack_start(chk, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=t["title"], xalign=0)
        title.get_style_context().add_class(
            "taskdone" if t["done"] else "tasktitle")
        # Long titles wrap (breaking mid-word if a single token is huge) rather
        # than getting clipped by the non-scrolling viewport.
        title.set_line_wrap(True)
        title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(52)
        # max-width-chars caps only the NATURAL width; a Box child defaults to
        # halign FILL, so on a wide screen the row stretched the label to the
        # full column and it wrapped at ~900px instead of at 52 characters —
        # one unreadable line right across the window. Pin it to the start.
        title.set_halign(Gtk.Align.START)
        mid.pack_start(title, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        meta.set_margin_top(5)
        color = PROJ_COLOR.get(t["project"], "#C9C4B6")
        pdot = Gtk.DrawingArea(); pdot.set_size_request(9, 9)
        pdot.set_valign(Gtk.Align.CENTER)
        pdot.connect("draw", self._draw_dot, color)
        meta.pack_start(pdot, False, False, 0)
        pl = Gtk.Label(label=t["project"] or "Inbox", xalign=0)
        pl.get_style_context().add_class("taskproj")
        # A user list can be named anything; unellipsized this sublabel set the
        # window's minimum width just as the header title did.
        pl.set_ellipsize(Pango.EllipsizeMode.END)
        pl.set_max_width_chars(34)
        meta.pack_start(pl, False, False, 0)
        # A dated task says WHICH day, so "This Week" and "Overdue" are answered
        # on the row rather than only by the group heading. Today is left off —
        # the heading above it already says Today.
        late = self._due_of(t) == "overdue"
        for note in (self._day_note(t), (t.get("time") or "").strip()):
            if not note:
                continue
            sep = Gtk.Label(label="·", xalign=0)
            sep.get_style_context().add_class("taskdot")
            meta.pack_start(sep, False, False, 0)
            tl = Gtk.Label(label=note, xalign=0)
            tl.get_style_context().add_class(
                "tasknoteover" if late else "tasknote")
            meta.pack_start(tl, False, False, 0)
        mid.pack_start(meta, False, False, 0)
        row.pack_start(mid, True, True, 0)

        # priority marker on the trailing edge (red = high, ink = normal-flag).
        # Built for every prioritised task but hidden while done, so the in-place
        # toggle only flips its visibility (no create/destroy). set_no_show_all
        # keeps show_all() from un-hiding a done task's flag.
        prio = t.get("prio", 0)
        flag = None
        if prio:
            pc = "#C8341E" if prio >= 2 else "#1A1916"
            flag = Gtk.DrawingArea(); flag.set_size_request(8, 8)
            flag.set_valign(Gtk.Align.CENTER)
            flag.set_margin_end(6)
            flag.connect("draw", self._draw_dot, pc)
            flag.set_no_show_all(True)
            flag.set_visible(not t["done"])
            row.pack_end(flag, False, False, 0)

        btn.add(row)
        # Register the mutable handles so _toggle can update this row in place.
        self._rows[idx] = {"check": chk, "title": title, "flag": flag}
        return btn

    def _draw_check(self, area, ctx, task):
        # `task` is the live task dict (not a snapshot bool), so a queue_draw()
        # after an in-place toggle re-reads the current done state.
        try:
            done = task.get("done") if isinstance(task, dict) else bool(task)
            w = area.get_allocated_width(); h = area.get_allocated_height()
            radius = 3
            ctx.set_line_width(1.5)
            if done:
                r, g, b = nbicons._hex(INK)
                ctx.set_source_rgb(r, g, b)
                self._round_rect(ctx, 1, 1, w - 2, h - 2, radius)
                ctx.fill()
                # tick
                ctx.set_source_rgb(*nbicons._hex("#FCFBF8"))
                ctx.set_line_width(GLYPH_STROKE)
                ctx.set_line_cap(1)
                ctx.move_to(w * 0.28, h * 0.52)
                ctx.line_to(w * 0.44, h * 0.70)
                ctx.line_to(w * 0.74, h * 0.32)
                ctx.stroke()
            else:
                ctx.set_source_rgb(*nbicons._hex("#B3AD9E"))
                self._round_rect(ctx, 1, 1, w - 2, h - 2, radius)
                ctx.stroke()
        except Exception:
            # Never let a checkbox render error escape the draw handler.
            pass
        return False

    def _round_rect(self, ctx, x, y, w, h, r):
        import math
        ctx.new_sub_path()
        ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        ctx.close_path()

    # ------------------------------------------------------------------- CSS
    def _install_css(self):
        css = b"""
        .sidebar, .centercol, .rail {
            font-family: "Nimbus Sans","Helvetica",sans-serif; }

        .sidebar { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        .sidebar scrolledwindow, .sidebar viewport { background: #F1EEE6; }
        .sectionhead { font-size: 11px; letter-spacing: 0.13em; color: #8A857A;
                       font-weight: 700; }
        .siderow { padding: 9px 12px; border-radius: 6px; background: transparent;
                   border: none; box-shadow: none; margin-bottom: 2px; }
        .siderow:hover { background: #F0EADC; }
        .siderow.selected { background: #EAE3D2;
                            box-shadow: inset 3px 0 0 #C8341E; }
        .siderowlabel { font-size: 15px; color: #1A1916; font-weight: 500; }
        .sidecount { font-size: 12px; color: #8A857A; }
        .newlist { padding: 15px 26px; background: #F1EEE6; border: none;
                   box-shadow: none; border-top: 1px solid #D7D2C5;
                   border-radius: 0; }
        .newlist:hover { background: #EAE3D2; }
        .newlistlabel { font-size: 14px; color: #6E695E; font-weight: 500; }

        .centercol { background: #FCFBF8; border-right: 1px solid #D7D2C5; }
        .eyebrow { font-size: 12px; letter-spacing: 0.16em; color: #8A857A;
                   font-weight: 700; }
        .viewtitle { font-size: 34px; font-weight: 700; color: #1A1916; }
        .remaining { font-size: 14px; color: #8A857A; }
        .quickadd { min-height: 48px; border: 1px dashed #C9C4B6;
                    border-radius: 8px; }
        .draftentry { background: transparent; border: none; box-shadow: none;
                      font-size: 15px; color: #1A1916; }
        .draftentry:focus { border: none; box-shadow: none; }

        /* A task row is a LIST row, not a card: the theme's default button
           border drew a full box around every task, so the list read as a
           column of boxes instead of hairline-separated rows. Clear all four
           sides first, then put the hairline back on the bottom only. */
        .taskrow { padding: 13px 6px; border-radius: 0; border: none;
                   border-bottom: 1px solid #D7D2C5; background: transparent;
                   box-shadow: none; }
        .taskrow:hover { background: #F8F7F2; }
        .tasktitle { font-size: 16px; color: #1A1916; }
        .taskdone { font-size: 16px; color: #B3AD9E;
                    text-decoration-line: line-through; }
        .taskproj { font-size: 12px; color: #6E695E; }
        .taskdot { font-size: 12px; color: #C9C4B6; }
        .tasknote { font-size: 12px; color: #8A857A; }
        .tasknoteover { font-size: 12px; color: #C8341E; font-weight: 600; }
        .grouphead { font-size: 12px; letter-spacing: 0.1em; font-weight: 700;
                     color: #6E695E; }
        .groupover { font-size: 12px; letter-spacing: 0.1em; font-weight: 700;
                     color: #C8341E; }
        .groupline { background: #D7D2C5; min-height: 1px; }
        .groupcount { font-size: 12px; color: #8A857A; }

        .rail { background: #FCFBF8; }
        .railhead { border-bottom: 1px solid #D7D2C5; }
        .railtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .emptytext { font-size: 14px; color: #8A857A; }
        .emptyhint { font-size: 12px; color: #9A9484; }
        .emptysub { font-size: 13px; color: #8A857A; }
        .eventtime { font-size: 13px; color: #1A1916; font-weight: 600; }
        .eventdur { font-size: 11px; color: #8A857A; }
        .eventcard { background: #FCFBF8; border: 1px solid #D7D2C5;
                     border-radius: 12px; }
        .eventtitle { font-size: 15px; color: #1A1916; font-weight: 600; }
        .eventsub { font-size: 12px; color: #8A857A; }
        .minical { border-top: 1px solid #D7D2C5; }
        .minititle { font-size: 14px; font-weight: 700; color: #1A1916; }
        .caldow { font-size: 10px; color: #B3AD9E; font-weight: 700; }
        .calday { font-size: 12px; color: #6E695E; }
        .calmarked { font-size: 12px; color: #1A1916; font-weight: 700; }
        .caltoday { font-size: 12px; color: #FCFBF8; font-weight: 700;
                    background: #C8341E; border-radius: 50%;
                    min-width: 26px; min-height: 26px; padding: 0; }
        .eventadd { border-top: 1px solid #D7D2C5; padding-top: 8px;
                    margin-top: 6px; }
        .eventaddentry { background: transparent; border: none; box-shadow: none;
                         font-size: 13px; color: #6E695E; }
        .eventaddentry:focus { border: none; box-shadow: none; }

        /* Dim the app behind a card so the decision in front of the user is
           clearly the only live thing on screen (matches ebook's confirm). */
        .scrim { background: rgba(26,25,22,0.18); }
        .nlcard { font-family: "Nimbus Sans","Helvetica",sans-serif;
                  background: #FCFBF8; border: 1px solid #1A1916;
                  padding: 26px 30px; }
        .nltitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .nlbody { font-size: 14px; color: #4A473F; }
        .nlhint { font-size: 11px; letter-spacing: 0.13em; color: #8A857A;
                  font-weight: 700; }
        /* The colour picker: a real button, so the keyboard can reach it, but
           wearing none of the button chrome -- the DrawingArea inside IS the
           control's appearance. min-width/min-height are pinned to 0 so the
           theme's shared 20px button minimum can never grow past the 28px
           artwork and shift the row. No `outline: none`: the global focus ring
           is the only thing telling a keyboard user where they are. */
        .nlswatch { padding: 0; border: none; background: transparent;
                    background-image: none; box-shadow: none;
                    min-width: 0; min-height: 0; }
        .nlentry { background: #FCFBF8; border: 1px solid #C9C4B6;
                   border-radius: 8px; padding: 8px 10px; font-size: 15px;
                   color: #1A1916; }
        .nlentry:focus { border: 1px solid #B3AD9E; box-shadow: none; }
        .nlbtn { padding: 8px 18px; border-radius: 8px; border: 1px solid #C9C4B6;
                 background: #F1EEE6; color: #1A1916; font-size: 14px;
                 box-shadow: none; }
        .nlbtn:hover { background: #EAE3D2; }
        /* A colour set on the BUTTON node never reaches the label inside it:
           the theme's `* { color: ink }` matches that label node directly and
           beats the inherited value. Without the `label` selectors below, the
           Create / Remove buttons render as solid slabs with ink text on ink
           background: an invisible primary action. Same for the destructive
           menu item further down. */
        .nlcreate { background: #1A1916; border: 1px solid #1A1916; }
        .nlcreate, .nlcreate label { color: #FCFBF8; }
        .nlcreate:hover { background: #2A2620; }
        .nlremove { background: #C8341E; border: 1px solid #C8341E; }
        .nlremove, .nlremove label { color: #FCFBF8; }
        .nlremove:hover { background: #B12D19; }

        /* Destructive item in the task right-click menu: red is the reserved
           alert use, kept red on hover too. */
        .taskmenu-del, .taskmenu-del label { color: #C8341E; }
        .taskmenu-del:hover, .taskmenu-del:hover label { color: #C8341E; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # Styling is cosmetic: a CSS parse error or a missing default
            # screen must not stop the app window from constructing.
            pass


if __name__ == "__main__":
    nbapp.run(Tasks)
