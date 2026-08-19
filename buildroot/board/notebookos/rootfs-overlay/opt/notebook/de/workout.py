#!/usr/bin/env python3
"""
Workout — track the sets and reps you do each day, against a daily goal.

You list the exercises you want to do (Push-ups, 5 sets of 10), and each time
you finish a set you log it. The point of the app is the answer to one
question: have I done today's work yet? So today's progress is the biggest
thing on screen, the week beside it shows which days you hit, and logging a
set is a single click.

Optionally the same day's progress appears as a card on the desktop; desktop
tile visibility is managed centrally in Widget Settings.

Data lives in $NB_HOME/.config/notebook/workout.json:

    {"exercises": [{"id": .., "name": .., "sets": 5, "reps": 10}],
     "log": {"2026-07-28": {"<id>": [10, 10, 8]}},
     "goals": {"2026-07-28": 8},
     "goal_sets": {"2026-07-28": {"<id>": 5}},
     "show_widget": false}

A set is stored as the number of reps actually done, so the log keeps its
meaning if the goal changes later. widgets.py reads this same file.

"goals" is the day's goal as a TOTAL and "goal_sets" the same goal exercise by
exercise, both stamped for the day they were run against (_stamp_today_goal).
Two shapes for one fact because they answer different questions: the total is
what the desktop card reads, while whether the day COUNTS is decided exercise
by exercise — six sets of push-ups do not finish the squats that were skipped.
"""
import os
import time
import copy

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("Atk", "1.0")
from gi.repository import Atk, Gdk, GLib, Gtk, Pango  # noqa: E402

import nbapp  # noqa: E402
import nbicons  # noqa: E402
import nbi18n
from nbi18n import _t  # noqa: E402

STORE = os.path.join(os.environ.get("NB_HOME", os.path.expanduser("~")),
                     ".config", "notebook", "workout.json")
MAX_STORE_BYTES = 16 * 1024 * 1024


def _set_user_text(label, text, fallback=""):
    """Put an exercise the USER named on a label, exactly as they named it.

    "Push-ups" is a catalog key; so are "Reading", "Practice" and "Set". On a
    French install the card read "Pompes" while workout.json went on saying
    "Push-ups" — the exercise on screen was not the exercise in the file."""
    value = str(text or "")
    if value:
        nbi18n.set_verbatim(label, value)
        return
    empty = _t(fallback) if fallback else ""
    try:
        label.set_text(empty)
    except AttributeError:
        label.set_label(empty)


class WorkoutStoreTooLarge(ValueError):
    pass


def _read_store_json(path=None, limit=MAX_STORE_BYTES):
    """Read workout history without allowing a damaged store to stall launch."""
    import json
    if path is None:
        path = STORE
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise WorkoutStoreTooLarge("workout store is too large")
    return json.loads(data)

SIDEBAR_W = 240
# The reading column the exercise list is held to, so one short row does not
# stretch the width of a 1920 panel (the cap Settings also uses).
COLUMN_W = 620

DAY_LETTERS = ("M", "T", "W", "T", "F", "S", "S")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
HAIR = "#C9C4B6"
ACCENT = "#C8341E"
PAPER = "#FCFBF8"

MAX_SETS = 20
MAX_REPS = 999


def today_key(when=None):
    t = time.localtime(when) if when is not None else time.localtime()
    return "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)


def _display_date(lt):
    """Translate the complete phrase so locales may reorder day/month fields."""
    return _t("%s %d %s" % (DAY_NAMES[lt[6]], lt[2], MONTHS[lt[1] - 1]))


# Consecutive-day arithmetic, shared with the desktop card so both count a
# streak by exactly the same rule. See nbapp.day_ordinal for why it is not
# strptime and not timestamp arithmetic.
_ordinal = nbapp.day_ordinal


# ---- forgiving readers ------------------------------------------------------
# A store section of the wrong TYPE must cost only itself. These exist because
# `for item in (raw.get("exercises") or [])` raised TypeError on a number --
# outside _load's try, so the app would not open at all -- and because a section
# that came back as an object simply read as "no data", after which the next
# _save rewrote the file and a year of training was gone.
def _records(v):
    """A store section as a list of records: a list as-is, an object as its
    values in file order, anything else as nothing."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return []


def _daymap(v, depth=0):
    """A date-keyed section as (day, value) pairs. Accepts the normal object,
    and also a list of one-day objects (the shape a repair tool or an older
    exporter can leave behind), so a wrapper of the wrong type never costs the
    log it holds. Non-string keys are dropped; only the day they name is lost.

    A key that is not a DATE and whose value is itself a day-map is a wrapper
    ({"log": {"days": {...}}}), not a day: descend into it. Taking that key at
    face value handed the whole log back as one bogus "day" whose values were
    day-maps rather than sets, every one of them was then dropped as malformed,
    and the next save wrote the empty log over a training history."""
    out = []
    parts = [v] if isinstance(v, dict) else [x for x in _records(v)
                                            if isinstance(x, dict)]
    for part in parts:
        for day, val in part.items():
            if not isinstance(day, str):
                continue
            if _ordinal(day) is None and depth < 3:
                nested = _daymap(val, depth + 1)
                if nested and any(_ordinal(d) is not None for d, _ in nested):
                    out.extend(nested)
                    continue
            out.append((day, val))
    return out


def _day_from_ordinal(o):
    """The "YYYY-MM-DD" key for a day number — the inverse of _ordinal.

    The standard civil-from-days algorithm, for the reason nbapp.day_ordinal
    gives: a day is a CALENDAR step, not 86400 seconds. See _week_days.
    """
    z = int(o) + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return "%04d-%02d-%02d" % (y + (m <= 2), m, d)


def _week_days(when=None):
    """The seven day keys of the week `when` falls in, Monday first.

    Counted in whole calendar days from today's day number, NOT by stepping a
    timestamp 86400 seconds at a time. Under a daylight-saving change that
    arithmetic slides an hour and the whole strip shifts: on the evening of a
    fall-back Sunday it read Tue..Sun+Sun, so Monday's sets disappeared from
    the week, every row wore the wrong weekday name, and Sunday was counted
    twice in SETS THIS WEEK. The same trap nbapp.day_ordinal was written for.
    """
    today = today_key(when)
    o = _ordinal(today)
    # 1970-01-01 was a Thursday, so (o + 3) % 7 is the weekday with Monday 0.
    monday = o - (o + 3) % 7
    return [_day_from_ordinal(monday + i) for i in range(7)]


class Pips(Gtk.DrawingArea):
    """One dot per set in the goal, filled for each set actually done.

    Drawn rather than composed from labels because the whole value of this row
    is being readable at a glance: a filled/empty run of dots answers "how far
    through am I" without counting. Extra sets beyond the goal are drawn in the
    accent, so going past the target reads as a win rather than an overflow.
    """

    DOT = 13
    GAP = 7

    def __init__(self, goal, done):
        super().__init__()
        self.goal = max(0, int(goal))
        self.done = max(0, int(done))
        shown = max(self.goal, self.done)
        self.set_size_request(max(1, shown * self.DOT + max(0, shown - 1) * self.GAP),
                              self.DOT)
        # A DrawingArea left at FILL smears its drawing across whatever cell it
        # lands in; pin it to the size it asked for.
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        r = self.DOT / 2.0
        shown = max(self.goal, self.done)
        for i in range(shown):
            cx = i * (self.DOT + self.GAP) + r
            cy = r
            cr.arc(cx, cy, r - 1, 0, 6.2832)
            if i < self.done:
                # done: filled ink, or the accent for anything past the goal
                if i >= self.goal:
                    cr.set_source_rgb(0.784, 0.204, 0.118)     # #C8341E
                else:
                    cr.set_source_rgb(0.102, 0.098, 0.086)     # #1A1916
                cr.fill()
            else:
                cr.set_source_rgb(0.788, 0.769, 0.714)         # #C9C4B6
                cr.set_line_width(1.4)
                cr.stroke()
        return False


class Ring(Gtk.DrawingArea):
    """A small completion ring for one day in the week strip."""

    def __init__(self, frac, size=16, today=False):
        super().__init__()
        self.frac = max(0.0, min(1.0, float(frac)))
        self.today = today
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self._size = size
        self.connect("draw", self._draw)

    def _draw(self, _w, cr):
        r = self._size / 2.0 - 1.5
        cx = cy = self._size / 2.0
        cr.set_line_width(2.0)
        cr.set_source_rgb(0.855, 0.839, 0.788)                 # empty track
        cr.arc(cx, cy, r, 0, 6.2832)
        cr.stroke()
        if self.frac > 0:
            if self.frac >= 1.0:
                cr.set_source_rgb(0.102, 0.098, 0.086)         # complete: ink
            else:
                cr.set_source_rgb(0.431, 0.412, 0.369)         # partial: muted
            cr.arc(cx, cy, r, -1.5708, -1.5708 + 6.2832 * self.frac)
            cr.stroke()
        if self.frac >= 1.0:
            cr.set_source_rgb(0.102, 0.098, 0.086)
            cr.arc(cx, cy, r - 4.0, 0, 6.2832)
            cr.fill()
        return False


class Workout(nbapp.AppWindow):
    app_name = "Workout"
    # The actions menu is named for what it acts on, never for the app itself:
    # nbapp keys its menu-bar buttons by name, so a menu called "Workout" was
    # stored over the app-name button and took its place — the bar read
    # "Workout File Edit Workout", both of them opened the exercise actions,
    # About Workout could not be reached at all, and the app-name button kept
    # the open menu's shading for the rest of the session.
    menus = ("File", "Edit", "Exercise")

    def __init__(self):
        super().__init__()
        self.data = self._load()
        self.sel = 0 if self.data["exercises"] else -1
        self.undo = nbapp.UndoHistory(self._undo_snapshot,
                                      self._restore_undo_snapshot)
        self.undo.reset()
        self._build()
        self._install_css()
        self._refresh()
        self._closed = False
        self._shown_day = today_key()
        self._day_rollover_id = GLib.timeout_add_seconds(
            30, self._check_day_rollover)
        self.connect("destroy", self._on_destroy)

    # -- store ---------------------------------------------------------------

    def _load(self):
        """Read the store, tolerating anything that is not the right shape.

        Every field is re-validated rather than trusted: this file is also
        written by nothing else, but a truncated or hand-edited one must open
        the app empty instead of stopping it from opening at all.
        """
        blank = {"exercises": [], "log": {}, "goals": {}, "goal_sets": {},
                 "show_widget": False}
        self._load_error = ""
        self._quarantine_pending = False
        had_store = os.path.exists(STORE)
        try:
            raw = _read_store_json()
        except WorkoutStoreTooLarge:
            # A valid but pathological object would not be moved by the shared
            # parse-damage guard. Preserve it through the app-aware path before
            # any blank fallback history is allowed to save.
            self._damaged_path = nbapp.quarantine_unrecognized(STORE)
            self._quarantine_pending = had_store and os.path.exists(STORE)
            self._load_error = (_t("Your workout history could not be read. "
                                  "The records were kept.")
                                if self._damaged_path else _t("Not saved"))
            return blank
        except (OSError, ValueError):
            # THE BYTES GO ASIDE BEFORE THE BLANK CAN REPLACE THEM. Opening on
            # `blank` is right; leaving the unreadable file where it is was not,
            # because the very next _save writes this blank over it — and _save
            # runs on ordinary use, not just on a deliberate edit. Months of
            # sets, goals and streaks were destroyed by launching the app, with
            # no action from the person at all. That is the worst outcome this
            # OS has produced before, and this was the last store with no
            # protection against it.
            self._damaged_path = nbapp.preserve_damaged(STORE)
            self._quarantine_pending = had_store and os.path.exists(STORE)
            if self._damaged_path:
                self._load_error = _t(
                    "Your workout history could not be read. "
                    "The records were kept.")
            elif self._quarantine_pending:
                self._load_error = _t("Not saved")
            return blank
        if not isinstance(raw, dict):
            # Valid JSON of a shape this app does not recognise parses fine, so
            # preserve_damaged cannot see it — only the app knows its own shape.
            self._damaged_path = nbapp.quarantine_unrecognized(STORE)
            self._quarantine_pending = os.path.exists(STORE)
            if self._damaged_path:
                self._load_error = _t(
                    "Your workout history could not be read. "
                    "The records were kept.")
            elif self._quarantine_pending:
                self._load_error = _t("Not saved")
            return blank

        known_root = {"exercises", "log", "goals", "goal_sets", "show_widget"}
        out = {k: copy.deepcopy(v) for k, v in raw.items()
               if k not in known_root}
        out.update({"exercises": [], "log": {}, "goals": {}, "goal_sets": {},
                    "show_widget": bool(raw.get("show_widget", False))})
        seen = set()
        # A section of the wrong type must cost only itself. `raw.get(...) or []`
        # was not enough: a number here raised TypeError straight out of this
        # method (it is outside the try above), so the app would not open at all,
        # and a section that is an object still holds the user's records in its
        # values -- reading them beats throwing a training history away, because
        # the next _save rewrites this whole file.
        for item in _records(raw.get("exercises")):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            eid = item.get("id")
            if not isinstance(eid, str) or not eid or eid in seen:
                eid = "e%d%s" % (len(out["exercises"]), os.urandom(3).hex())
            seen.add(eid)
            known_exercise = {"id", "name", "sets", "reps"}
            exercise = {k: copy.deepcopy(v) for k, v in item.items()
                        if k not in known_exercise}
            exercise.update({
                "id": eid,
                "name": name.strip()[:60],
                "sets": _clamp_int(item.get("sets"), 1, MAX_SETS, 3),
                "reps": _clamp_int(item.get("reps"), 1, MAX_REPS, 10),
            })
            out["exercises"].append(exercise)
        for day, entry in _daymap(raw.get("log")):
            if not isinstance(entry, dict):
                continue
            clean = {}
            for eid, sets in entry.items():
                if not isinstance(eid, str):
                    continue
                if not isinstance(sets, list):
                    # A day's sets stored as a single number is still a set the
                    # user did; read it as one rather than dropping the day.
                    n = _clamp_int(sets, 0, MAX_REPS, -1)
                    if n < 0:
                        continue
                    sets = [n]
                clean[eid] = [_clamp_int(s, 0, MAX_REPS, 0)
                              for s in sets][:MAX_SETS * 4]
            if clean:
                out["log"][day] = clean
        for day, total in _daymap(raw.get("goals")):
            if _ordinal(day) is not None:
                out["goals"][day] = _clamp_int(total, 0, MAX_SETS * 200, 0)
        for day, per in _daymap(raw.get("goal_sets")):
            if _ordinal(day) is None or not isinstance(per, dict):
                continue
            # A goal this reader cannot make sense of costs only itself: it
            # reads as nothing asked of that exercise, so the day falls back to
            # being judged on the total it was stamped with rather than being
            # made impossible to have completed.
            clean = {eid: _clamp_int(want, 0, MAX_SETS, 0)
                     for eid, want in per.items() if isinstance(eid, str)}
            if clean:
                out["goal_sets"][day] = clean
        return out

    def _save(self):
        self._stamp_today_goal()
        try:
            if getattr(self, "_quarantine_pending", False):
                nbapp.quarantine_unrecognized(STORE)
                if os.path.exists(STORE):
                    raise OSError("could not preserve unreadable workout history")
                self._quarantine_pending = False
            os.makedirs(os.path.dirname(STORE), exist_ok=True)
            nbapp.atomic_write_json(STORE, self.data, indent=1)
            self._save_error = ""
            # The damage notice explains an app that opened EMPTY. The person's
            # own work is now on disk under a healthy file, so the strip goes
            # back to reporting today (the preserved bytes are still beside it).
            # Left set, the notice outranked every later line, and a session
            # that started on a damaged store never showed its sets, reps or
            # streak again — not even "Today is done" — however much was done.
            self._load_error = ""
            return True
        except OSError as exc:
            # A read-only home must never stop the app working — but it must not
            # be silent either. See academics._save_to_disk: without this, a full
            # disk looks exactly like the app forgetting today's sets, because
            # the store keeps whatever the last successful write left there. The
            # reason is held rather than flashed, and _refresh_status shows it
            # until a save succeeds, because the status strip is rewritten on
            # every refresh and a one-shot message would be wiped immediately.
            self._save_error = nbapp.save_failure_reason(exc, STORE)
            return False

    def _undo_snapshot(self):
        return {"data": copy.deepcopy(self.data), "sel": self.sel}

    def _restore_undo_snapshot(self, state):
        before = self._undo_snapshot()
        self.data = copy.deepcopy(state.get("data", {}))
        self.sel = state.get("sel", -1)
        if not self._save():
            reason = getattr(self, "_save_error", "")
            self.data = before["data"]
            self.sel = before["sel"]
            self._save_error = reason
            if hasattr(self, "list_box"):
                self._refresh()
            return False
        if hasattr(self, "list_box"):
            self._refresh()
        return True

    def _save_or_rollback(self, before):
        """Make persistence the commit point for a visible workout edit."""
        if self._save():
            return True
        reason = getattr(self, "_save_error", "")
        self.data = copy.deepcopy(before["data"])
        self.sel = before["sel"]
        self._save_error = reason
        self._refresh()
        return False

    def _stamp_today_goal(self):
        """Record what the goal IS today, so that once today is in the past it
        keeps the goal it was actually run against.

        Without this the streak is computed against whatever the goal happens
        to be right now, so adding a fourth exercise this morning would reach
        back and un-complete every day of a run you had already earned. Only
        today is ever stamped — a past day's numbers are frozen — and a day with
        nothing logged carries no stamp, so the maps stay the same size as the
        log rather than growing forever.

        The goal is stamped twice: as a total, which is what the desktop card
        reads, and exercise by exercise, which is what decides whether the day
        counted (see _day_progress). Without the second one a past day could
        only ever be judged on its total, so a day that skipped an exercise
        stayed banked forever.
        """
        goals = self.data.setdefault("goals", {})
        per_day = self.data.setdefault("goal_sets", {})
        today = today_key()
        if self.data["log"].get(today):
            goals[today] = self._goal_total()
            per_day[today] = {ex["id"]: ex["sets"]
                              for ex in self.data["exercises"]}
        else:
            goals.pop(today, None)
            per_day.pop(today, None)
        for day in [d for d in goals if d not in self.data["log"]]:
            goals.pop(day, None)
        for day in [d for d in per_day if d not in self.data["log"]]:
            per_day.pop(day, None)

    # -- data helpers --------------------------------------------------------

    def _sets_for(self, eid, day=None):
        return list(self.data["log"].get(day or today_key(), {}).get(eid, []))

    def _goal_total(self):
        """Sets in the whole daily goal, as it stands right now."""
        return sum(ex["sets"] for ex in self.data["exercises"])

    def _day_totals(self, day):
        """(sets done, sets in the goal) for a day.

        Today's goal is always the live one — add an exercise this morning and
        today's target grows with it. A PAST day keeps the goal it was logged
        against (see _stamp_today_goal), falling back to the live goal for days
        recorded before goals were stamped.
        """
        entry = self.data["log"].get(day, {})
        done = sum(len(sets) for sets in entry.values()
                   if isinstance(sets, list))
        if day == today_key():
            return done, self._goal_total()
        return done, self.data.get("goals", {}).get(day, self._goal_total())

    def _goal_sets_for(self, day):
        """{exercise id: sets that exercise asked for} on a day.

        Today is measured against the live goal, so an exercise added this
        morning is part of today. A PAST day keeps the per-exercise goal it was
        logged against (see _stamp_today_goal); a day stamped before those were
        kept has no map at all, and its total is the only thing the store still
        remembers about it.
        """
        if day == today_key():
            return {ex["id"]: ex["sets"] for ex in self.data["exercises"]}
        stamped = self.data.get("goal_sets", {}).get(day)
        return dict(stamped) if isinstance(stamped, dict) else {}

    def _day_progress(self, day):
        """(sets that counted toward the goal, sets in the goal) for a day.

        A set counts for the exercise it was done for, and only up to that
        exercise's own target: six sets of push-ups are six sets done, but they
        are not the three squats that were skipped. This — not the day's total
        — is what the week ring fills with and what _day_complete asks, because
        the promise the streak makes is that a day counts only when the WHOLE
        goal was done. Comparing totals let one exercise pay for another: a day
        with an exercise untouched read "Today is done", filled its ring and
        started a streak. The score keeps the raw total, because going past a
        target is a win and reads as one (see Pips).
        """
        per = self._goal_sets_for(day)
        goal = sum(per.values())
        if not goal:
            # Nothing records which exercises that day held, so its total is
            # all there is to judge it by. Judging it harshly instead would
            # reach back and un-earn days already banked.
            done, total = self._day_totals(day)
            return min(done, total), total
        entry = self.data["log"].get(day, {})
        counted = 0
        for eid, want in per.items():
            sets = entry.get(eid)
            counted += min(len(sets) if isinstance(sets, list) else 0, want)
        return counted, goal

    def _day_complete(self, day):
        """Was the WHOLE goal done that day? Nothing partial counts — that is
        the point of the streak — and no exercise pays for another."""
        done, goal = self._day_progress(day)
        return goal > 0 and done >= goal

    def _streak(self):
        """(current run, best run ever) of days completed end to end.

        Today does not count against you until it is over: a run earned up to
        yesterday still reads as a live streak all day today, and only ends if
        today itself ends unfinished. Finishing today extends it immediately.
        """
        done_days = set()
        for day in self.data["log"]:
            o = _ordinal(day)
            if o is not None and self._day_complete(day):
                done_days.add(o)

        best = run = 0
        prev = None
        for o in sorted(done_days):
            run = run + 1 if prev is not None and o == prev + 1 else 1
            prev = o
            best = max(best, run)

        cur = 0
        at = _ordinal(today_key())
        if at not in done_days:
            at -= 1               # today is still in progress, not a miss
        while at in done_days:
            cur += 1
            at -= 1
        return cur, best

    def _reps_today(self):
        entry = self.data["log"].get(today_key(), {})
        return sum(sum(v) for v in entry.values())

    def _active(self):
        if 0 <= self.sel < len(self.data["exercises"]):
            return self.data["exercises"][self.sel]
        return None

    # -- ui ------------------------------------------------------------------

    def _install_css(self):
        # b"..." must stay ASCII: one non-ASCII byte silently kills the whole
        # stylesheet for the app.
        css = b"""
        .wo-side { background: #F1EEE6; border-right: 1px solid #C9C4B6; }
        .wo-side * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .wo-eyebrow { font-size: 11px; letter-spacing: 0.14em; font-weight: 700;
                      color: #6E695E; padding: 22px 16px 12px 16px; }
        .wo-day { padding: 7px 16px; }
        /* TODAY is marked in INK, not the accent. Two reasons, both hard:
           (1) on this screen the accent means GOAL MET (.wo-hit, the live
           streak, the past-goal pips) and a red edge on a weekday made the
           colour say a second thing right beside it -- the same bug already
           fixed for .wo-card.sel below;
           (2) OS-wide, a 3px accent edge means SELECTED (Tasks, Academics,
           Journal, Cookbook, Contacts, Packages, Music's sidebar). Today is
           not a selection, so it must not wear the selection's clothes. */
        .wo-day.today { background: #EFEBE0;
                        box-shadow: inset 3px 0 0 #1A1916; }
        .wo-dayname { font-size: 14px; color: #1A1916; }
        .wo-day.today .wo-dayname { font-weight: 700; }
        .wo-daymeta { font-size: 12px; color: #9A9484; }
        .wo-sidefoot { border-top: 1px solid #D7D2C5; padding: 14px 16px; }
        .wo-footnum { font-size: 20px; color: #1A1916; }
        .wo-footlabel { font-size: 11px; letter-spacing: 0.12em;
                        font-weight: 700; color: #6E695E; }
        /* A live streak carries the accent for the same reason a met goal
           does: it IS a run of met goals, not a second meaning for the
           colour. At zero it is an ordinary number and says nothing. */
        .wo-footnum.live { color: #C8341E; }
        .wo-footbest { font-size: 11px; color: #9A9484; }

        .wo-main { background: #FCFBF8; }
        .wo-main * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .wo-title { font-size: 30px; font-weight: 700; color: #1A1916; }
        .wo-date { font-size: 14px; color: #6E695E; }
        .wo-score { font-size: 30px; font-weight: 700; color: #1A1916; }
        .wo-scorelabel { font-size: 11px; letter-spacing: 0.12em;
                         font-weight: 700; color: #6E695E; }
        .wo-rule { background: #D7D2C5; }

        .wo-card { background: #F8F7F2; border: 1px solid #D7D2C5;
                   border-radius: 12px; padding: 16px 18px; }
        /* Selected = the card Edit/Delete act on. Deliberately quiet: the
           accent on this screen means GOAL MET, and a red selection edge next
           to a red "GOAL MET" made one colour say two things. */
        .wo-card.sel { border-color: #B3AD9E; background: #F1EEE6; }
        /* Keyboard focus, drawn in INK for the same reason the selection is
           quiet: the accent on this screen means GOAL MET, and a red edge
           around the card the keyboard happens to be on would make one colour
           say a third thing. Ink is also what marks TODAY in the sidebar, so
           the screen keeps one "you are here" colour.
           An inset shadow, not a border: a wrapper EventBox draws no focus
           ring of its own (GTK only renders one for widgets that ask), and
           thickening the card's real border would reflow every card below it
           on each Tab. Nothing moves; only the edge changes. The .sel
           background is left alone, so a card that is both focused and
           selected still reads as selected. */
        .wo-cardhit:focus .wo-card { border-color: #1A1916;
                                     box-shadow: inset 0 0 0 2px #1A1916; }
        .wo-name { font-size: 17px; font-weight: 700; color: #1A1916; }
        .wo-goal { font-size: 13px; color: #6E695E; }
        .wo-count { font-size: 15px; color: #1A1916; }
        .wo-count.hit { font-weight: 700; }
        .wo-hit { font-size: 12px; letter-spacing: 0.08em; font-weight: 700;
                  color: #C8341E; }

        .wo-add { background: #F8F7F2; border: 1px solid #C9C4B6;
                  border-radius: 8px; padding: 7px 16px; font-size: 14px;
                  color: #1A1916; box-shadow: none; }
        .wo-add:hover { background: #EFEBE0; }
        .wo-undo { background: transparent; border: 1px solid transparent;
                   border-radius: 8px; padding: 7px 10px; font-size: 14px;
                   color: #6E695E; box-shadow: none; }
        .wo-undo:hover { background: #EFEBE0; border-color: #D7D2C5; }

        .wo-empty-title { font-size: 17px; color: #1A1916; }
        .wo-empty-body { font-size: 14px; color: #6E695E; }
        .wo-cta { background: #F8F7F2; border: 1px solid #C9C4B6;
                  border-radius: 8px; padding: 9px 18px; font-size: 14px;
                  color: #1A1916; box-shadow: none; }
        .wo-cta:hover { background: #EFEBE0; }
        .wo-status { padding: 7px 16px; font-size: 12px; color: #6E695E;
                     border-top: 1px solid #D7D2C5; background: #F8F7F2; }

        /* A Save that cannot be pressed must not LOOK pressable. The theme
           paints .suggested-action solid ink in every state, so the sheet's
           Save, held until the exercise has a name, still came up as the
           filled black button and read as broken rather than as waiting. The
           label is named beside the button because a colour set on a button
           never reaches the label inside it. */
        button.suggested-action:disabled { background: #EFEBE0;
                                           border-color: #D7D2C5;
                                           color: #9A9484; box-shadow: none; }
        button.suggested-action:disabled label { color: #9A9484; }
        """
        try:
            prov = Gtk.CssProvider()
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:                                       # noqa: BLE001
            pass          # styling is cosmetic; never block launch

    def _build(self):
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # ---- the week, down the left ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.set_size_request(SIDEBAR_W, -1)
        side.get_style_context().add_class("wo-side")
        eyebrow = Gtk.Label(label=_t("THIS WEEK"), xalign=0)
        eyebrow.get_style_context().add_class("wo-eyebrow")
        side.pack_start(eyebrow, False, False, 0)
        self.week_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.pack_start(self.week_box, False, False, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        foot.get_style_context().add_class("wo-sidefoot")
        foot.set_valign(Gtk.Align.END)

        # The streak leads, because it is what the goal is FOR: every other
        # number here describes a day, this one is the thing you are keeping.
        streak = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.streak_num = Gtk.Label(label="0", xalign=0)
        self.streak_num.get_style_context().add_class("wo-footnum")
        self.streak_lbl = Gtk.Label(label=_t("DAY STREAK"), xalign=0)
        self.streak_lbl.get_style_context().add_class("wo-footlabel")
        self.streak_best = Gtk.Label(xalign=0)
        self.streak_best.get_style_context().add_class("wo-footbest")
        # Hidden until there is a best worth naming; show_all() must not
        # override that, or an empty line sits under the label forever.
        self.streak_best.set_no_show_all(True)
        streak.pack_start(self.streak_num, False, False, 0)
        streak.pack_start(self.streak_lbl, False, False, 0)
        streak.pack_start(self.streak_best, False, False, 0)
        foot.pack_start(streak, False, False, 0)

        week = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.week_total = Gtk.Label(label="0", xalign=0)
        self.week_total.get_style_context().add_class("wo-footnum")
        flabel = Gtk.Label(label=_t("SETS THIS WEEK"), xalign=0)
        flabel.get_style_context().add_class("wo-footlabel")
        week.pack_start(self.week_total, False, False, 0)
        week.pack_start(flabel, False, False, 0)
        foot.pack_start(week, False, False, 0)
        side.pack_end(foot, False, False, 0)
        body.pack_start(side, False, False, 0)

        # ---- today, on the right ----
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.get_style_context().add_class("wo-main")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_hexpand(False)
        inner.set_halign(Gtk.Align.CENTER)
        sw, _sh = nbapp.screen_size()
        inner.set_size_request(max(360, min(COLUMN_W, sw - SIDEBAR_W - 40)), -1)
        inner.set_margin_top(30)
        inner.set_margin_bottom(24)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=_t("Today"), xalign=0)
        t.get_style_context().add_class("wo-title")
        self.datelbl = Gtk.Label(xalign=0)
        self.datelbl.get_style_context().add_class("wo-date")
        titles.pack_start(t, False, False, 0)
        titles.pack_start(self.datelbl, False, False, 0)
        head.pack_start(titles, False, False, 0)

        scorebox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scorebox.set_valign(Gtk.Align.CENTER)
        self.score = Gtk.Label(xalign=1)
        self.score.get_style_context().add_class("wo-score")
        slabel = Gtk.Label(label=_t("SETS DONE"), xalign=1)
        slabel.get_style_context().add_class("wo-scorelabel")
        scorebox.pack_start(self.score, False, False, 0)
        scorebox.pack_start(slabel, False, False, 0)
        head.pack_end(scorebox, False, False, 0)
        inner.pack_start(head, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("wo-rule")
        rule.set_size_request(-1, 1)
        rule.set_margin_top(18)
        rule.set_margin_bottom(18)
        inner.pack_start(rule, False, False, 0)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.pack_start(self.list_box, False, False, 0)

        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        holder.pack_start(inner, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(holder)
        main.pack_start(scroll, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("wo-status")
        # A fixed bottom strip must be pinned, or GTK3 propagates vexpand up
        # from the content above and the strip floats mid-window.
        self.status.set_vexpand(False)
        main.pack_start(self.status, False, False, 0)

        body.pack_start(main, True, True, 0)
        self.content.pack_start(body, True, True, 0)

    # -- rendering -----------------------------------------------------------

    def _refresh(self):
        self._refresh_week()
        self._refresh_today()
        self._refresh_status()

    def _check_day_rollover(self):
        """Move the Today screen and week/streak summaries to the new day."""
        if self._closed:
            return False
        day = today_key()
        if day != self._shown_day:
            self._shown_day = day
            self._refresh()
        return True

    def _on_destroy(self, *_):
        self._closed = True
        source_id = getattr(self, "_day_rollover_id", 0)
        self._day_rollover_id = 0
        if source_id:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        return False

    def _refresh_week(self):
        for ch in self.week_box.get_children():
            self.week_box.remove(ch)
        days = _week_days()
        now = today_key()
        total = 0
        for i in range(7):
            day = days[i]
            # SETS THIS WEEK counts every set that was actually done, past a
            # target or not. The ring beside it asks the stricter question, by
            # the same rule the streak counts by, so a full ring and a banked
            # day always mean the same thing: sets count for the exercise they
            # were done for, and only up to its own target.
            done = self._day_totals(day)[0]
            total += done
            counted, want = self._day_progress(day)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.get_style_context().add_class("wo-day")
            if day == now:
                row.get_style_context().add_class("today")
            row.pack_start(Ring(counted / want if want else 0.0,
                                today=(day == now)), False, False, 0)
            name = Gtk.Label(label=_t(DAY_NAMES[i]), xalign=0)
            name.get_style_context().add_class("wo-dayname")
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(11)
            row.pack_start(name, True, True, 0)
            meta = Gtk.Label(label=("%d" % done) if done else "", xalign=1)
            meta.get_style_context().add_class("wo-daymeta")
            row.pack_end(meta, False, False, 0)
            self.week_box.pack_start(row, False, False, 0)
        self.week_total.set_text("%d" % total)
        self._refresh_streak()
        self.week_box.show_all()

    def _refresh_streak(self):
        cur, best = self._streak()
        self.streak_num.set_text("%d" % cur)
        ctx = self.streak_num.get_style_context()
        ctx.add_class("live") if cur else ctx.remove_class("live")
        # The best run is only worth the line when it is not the one already
        # on screen — otherwise it just repeats the number above it.
        if best > cur:
            self.streak_best.set_text(_t("best %d") % best)
            self.streak_best.set_visible(True)
        else:
            self.streak_best.set_visible(False)

    def _refresh_today(self):
        for ch in self.list_box.get_children():
            self.list_box.remove(ch)

        lt = time.localtime()
        self.datelbl.set_text(_display_date(lt))
        done, goal = self._day_totals(today_key())
        self.score.set_text("%d/%d" % (done, goal) if goal else "0")

        self._exercise_hits = {}
        self._exercise_actions = {}
        if not self.data["exercises"]:
            self.list_box.pack_start(self._empty_state(), False, False, 0)
        else:
            for idx, ex in enumerate(self.data["exercises"]):
                self.list_box.pack_start(self._exercise_card(idx, ex),
                                         False, False, 0)
        self.list_box.show_all()
        if getattr(self, "_restore_card_focus", False):
            self._restore_card_focus = False
            hit = self._exercise_hits.get(self.sel)
            if hit is not None:
                hit.grab_focus()
        action_focus = getattr(self, "_restore_action_focus", None)
        self._restore_action_focus = None
        if action_focus is not None:
            action = self._exercise_actions.get(action_focus)
            if action is not None:
                action.grab_focus()

    def _empty_state(self):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wrap.set_halign(Gtk.Align.CENTER)
        wrap.set_margin_top(40)
        img = nbicons.image("workout", 34, FAINT)
        wrap.pack_start(img, False, False, 0)
        title = Gtk.Label(label=_t("No exercises"))
        title.get_style_context().add_class("wo-empty-title")
        wrap.pack_start(title, False, False, 0)
        cta = Gtk.Button(label=_t("Add an exercise"))
        cta.get_style_context().add_class("wo-cta")
        cta.set_halign(Gtk.Align.CENTER)
        cta.connect("clicked", lambda *_a: self._new_exercise())
        wrap.pack_start(cta, False, False, 6)
        return wrap

    def _exercise_card(self, idx, ex):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("wo-card")
        if idx == self.sel:
            card.get_style_context().add_class("sel")

        sets = self._sets_for(ex["id"])
        done, goal = len(sets), ex["sets"]

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        nm = Gtk.Label(xalign=0)
        _set_user_text(nm, ex["name"])
        nm.get_style_context().add_class("wo-name")
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        nm.set_max_width_chars(30)
        names.pack_start(nm, False, False, 0)
        goal_lbl = Gtk.Label(
            label=_t("%d set%s of %d rep%s")
            % (ex["sets"], _pl(ex["sets"]), ex["reps"], _pl(ex["reps"])),
            xalign=0)
        goal_lbl.get_style_context().add_class("wo-goal")
        names.pack_start(goal_lbl, False, False, 0)
        top.pack_start(names, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        right.set_valign(Gtk.Align.CENTER)
        cnt = Gtk.Label(label="%d / %d" % (done, goal), xalign=1)
        cnt.get_style_context().add_class("wo-count")
        if done >= goal:
            cnt.get_style_context().add_class("hit")
        right.pack_start(cnt, False, False, 0)
        if done >= goal:
            # The one moment worth the accent: the goal is met.
            hit = Gtk.Label(label=_t("GOAL MET"), xalign=1)
            hit.get_style_context().add_class("wo-hit")
            right.pack_start(hit, False, False, 0)
        top.pack_end(right, False, False, 0)
        card.pack_start(top, False, False, 0)

        card.pack_start(Pips(goal, done), False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add = Gtk.Button(label=_t("Log a set"))
        add.get_style_context().add_class("wo-add")
        add.connect("clicked", self._on_log, idx)
        self._exercise_actions[(idx, "log")] = add
        btns.pack_start(add, False, False, 0)
        undo = Gtk.Button(label=_t("Undo"))
        undo.get_style_context().add_class("wo-undo")
        undo.set_sensitive(bool(sets))
        undo.connect("clicked", self._on_undo, idx)
        self._exercise_actions[(idx, "undo")] = undo
        btns.pack_start(undo, False, False, 0)
        card.pack_start(btns, False, False, 0)

        # Clicking anywhere on the card selects it, so the Workout menu's
        # Edit/Delete act on the one you are looking at.
        #
        # WHY AN EVENTBOX AND NOT A Gtk.Button, which is how every other
        # keyboard-operable card in the OS is built (Meal Planner's week
        # slots): this card already CONTAINS two buttons, Log a set and Undo,
        # and GTK will not put a button inside a button -- the nested pair
        # would stop taking clicks. So the three things a button would have
        # given for free are put on by hand: focus (set_can_focus), a name to
        # read out (the tooltip, which nbapp turns into the accessible name),
        # and Enter/Space activation (_on_card_key). Without them the card was
        # pointer-only: selecting an exercise, and therefore Edit and Delete,
        # could not be reached from the keyboard at all.
        hit = Gtk.EventBox()
        hit.set_visible_window(False)
        hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        hit.set_can_focus(True)
        hit.get_style_context().add_class("wo-cardhit")
        hit.set_tooltip_text(_t("Select %s") % ex["name"])
        accessible = hit.get_accessible()
        accessible.set_role(Atk.Role.RADIO_BUTTON)
        accessible.set_name(_t("Select %s") % ex["name"])
        selected = idx == self.sel
        accessible.notify_state_change(Atk.StateType.CHECKED, selected)
        accessible.notify_state_change(Atk.StateType.SELECTED, selected)
        self._exercise_hits[idx] = hit
        hit.add(card)
        hit.connect("button-press-event", self._on_select, idx)
        hit.connect("key-press-event", self._on_card_key, idx)
        return hit

    def _refresh_status(self):
        done, goal = self._day_totals(today_key())
        reps = self._reps_today()
        # A save that did not happen outranks anything else this strip says.
        if getattr(self, "_save_error", ""):
            self.status.set_text(self._save_error)
            return
        # Then why the app opened empty. Ranked under a failed save because
        # that one is about right now, but above everything else: an empty
        # history with no explanation reads as the app having forgotten.
        if getattr(self, "_load_error", ""):
            self.status.set_text(self._load_error)
            return
        if not self.data["exercises"]:
            self.status.set_text(_t("No exercises"))
        elif self._day_complete(today_key()):
            # The day is banked, so the sets count is old news (it is the big
            # number on screen); what changed is the streak.
            # A whole sentence per count, not a "%s" glued to "rep": the line
            # already carries two numbers, and nbi18n chooses a translation's
            # grammatical form from the FIRST count only \u2014 so a suffix on the
            # second one could never come out right in any language but this
            # one. At one rep the old line read "1 reps".
            streak = self._streak()[0]
            self.status.set_text(
                (_t("Today is done  \u00b7  %d day streak  \u00b7  1 rep")
                 % streak) if reps == 1 else
                (_t("Today is done  \u00b7  %d day streak  \u00b7  %d reps")
                 % (streak, reps)))
        else:
            self.status.set_text(
                (_t("%d of %d sets today  \u00b7  1 rep so far") % (done, goal))
                if reps == 1 else
                (_t("%d of %d sets today  \u00b7  %d reps so far")
                 % (done, goal, reps)))

    # -- actions -------------------------------------------------------------

    def _on_select(self, _w, _ev, idx):
        self.sel = idx
        self._restore_card_focus = True
        self._refresh_today()
        return False

    def _on_card_key(self, widget, ev, idx):
        """Enter or Space on a focused card chooses it, exactly as a click does.

        `idx` arrives as this connection's own user data, so each card carries
        the row it was built for rather than reading a loop variable that has
        moved on. Handled keys are swallowed (True) so Space cannot also scroll
        the list out from under the card that just took the selection; every
        other key falls through, which is what leaves Tab free to move on and
        the nested Log a set / Undo buttons free to be reached.
        """
        if ev.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._on_select(widget, ev, idx)
            return True
        return False

    # Logging a set is an edit like any other, so it is a step of its own —
    # the checkpoint/commit pair around every structural edit below. Without it
    # the history never saw a set: Ctrl+Z then pushed the whole day's logging
    # as ONE unnamed step and stepped back over it (nbapp.UndoHistory.undo),
    # while the Edit menu still offered the last thing it HAD recorded. An
    # afternoon of sets disappeared under an item that read "Undo Delete
    # Exercise", and the exercise stayed deleted. Any path that writes
    # self.data and saves belongs between checkpoint() and commit().
    def _on_log(self, _b, idx):
        self.undo.checkpoint("Log a Set")
        before = self._undo_snapshot()
        ex = self.data["exercises"][idx]
        day = self.data["log"].setdefault(today_key(), {})
        day.setdefault(ex["id"], []).append(ex["reps"])
        self.sel = idx
        saved = self._save_or_rollback(before)
        self.undo.commit()
        if saved:
            if getattr(_b, "has_focus", lambda: False)():
                self._restore_action_focus = (idx, "log")
            self._refresh()

    def _on_undo(self, _b, idx):
        ex = self.data["exercises"][idx]
        day = self.data["log"].get(today_key(), {})
        if day.get(ex["id"]):
            self.undo.checkpoint("Remove Set")
            before = self._undo_snapshot()
            day[ex["id"]].pop()
            if not day[ex["id"]]:
                del day[ex["id"]]
            if not day:
                self.data["log"].pop(today_key(), None)
            self.sel = idx
            saved = self._save_or_rollback(before)
            self.undo.commit()
            if saved:
                if getattr(_b, "has_focus", lambda: False)():
                    self._restore_action_focus = (idx, "undo")
                self._refresh()

    def _exercise_dialog(self, title, name="", sets=3, reps=10):
        """Shared add/edit sheet. Returns (name, sets, reps) or None."""
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        # Undecorated: a window-manager title bar makes a dialog look like it
        # belongs to another computer. The card already builds its own
        # .dlghead heading, so nothing is lost by dropping the bar.
        dlg.set_decorated(False)
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(_t("Save"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(18); box.set_margin_bottom(14)
        box.set_margin_start(20); box.set_margin_end(20)

        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("dlghead")
        box.add(head)

        ent = Gtk.Entry()
        ent.set_text(name)
        ent.set_max_length(60)
        ent.set_placeholder_text(_t("Push-ups"))
        ent.set_activates_default(True)
        box.add(_field(_t("Exercise"), ent))
        # An exercise IS its name, so there is nothing to save without one.
        # Save stays out of reach until one is typed rather than closing the
        # sheet on a press that quietly did nothing — which is what an edit
        # whose name had been cleared did, taking a changed goal down with it.
        # Return activates the same default, so it simply leaves the sheet open.
        def _named(entry, *_a):
            ok.set_sensitive(bool(entry.get_text().strip()))
        ent.connect("changed", _named)
        _named(ent)

        s_adj = Gtk.Adjustment(value=sets, lower=1, upper=MAX_SETS,
                               step_increment=1, page_increment=1)
        s_spin = Gtk.SpinButton(adjustment=s_adj, climb_rate=1, digits=0)
        s_spin.set_numeric(True)
        box.add(_field(_t("Sets a day"), s_spin))

        r_adj = Gtk.Adjustment(value=reps, lower=1, upper=MAX_REPS,
                               step_increment=1, page_increment=5)
        r_spin = Gtk.SpinButton(adjustment=r_adj, climb_rate=1, digits=0)
        r_spin.set_numeric(True)
        box.add(_field(_t("Reps a set"), r_spin))

        dlg.show_all()
        out = None
        if dlg.run() == Gtk.ResponseType.OK:
            nm = ent.get_text().strip()
            if nm:
                out = (nm[:60], int(s_spin.get_value()), int(r_spin.get_value()))
        dlg.destroy()
        return out

    def _new_exercise(self):
        got = self._exercise_dialog(_t("New Exercise"))
        if not got:
            return
        self.undo.checkpoint("New Exercise")
        before = self._undo_snapshot()
        name, sets, reps = got
        self.data["exercises"].append({
            "id": "e%s" % os.urandom(5).hex(), "name": name,
            "sets": sets, "reps": reps})
        self.sel = len(self.data["exercises"]) - 1
        saved = self._save_or_rollback(before)
        self.undo.commit()
        if saved:
            self._refresh()

    def _edit_exercise(self):
        ex = self._active()
        if not ex:
            return
        got = self._exercise_dialog(_t("Edit Exercise"), ex["name"],
                                    ex["sets"], ex["reps"])
        if not got:
            return
        self.undo.checkpoint("Edit Exercise")
        before = self._undo_snapshot()
        ex["name"], ex["sets"], ex["reps"] = got
        saved = self._save_or_rollback(before)
        self.undo.commit()
        if saved:
            self._refresh()

    def _delete_exercise(self):
        ex = self._active()
        if not ex:
            return
        if not _confirm(
                self, _t("Delete Exercise"),
                _t("Delete %s and all of its logged sets?") % ex["name"],
                _t("Delete")):
            return
        self.undo.checkpoint("Delete Exercise")
        before = self._undo_snapshot()
        eid = ex["id"]
        del self.data["exercises"][self.sel]
        for entry in self.data["log"].values():
            entry.pop(eid, None)
        self.data["log"] = {d: e for d, e in self.data["log"].items() if e}
        self.sel = min(self.sel, len(self.data["exercises"]) - 1)
        saved = self._save_or_rollback(before)
        self.undo.commit()
        if saved:
            self._refresh()

    def _clear_today(self):
        if not self.data["log"].get(today_key()):
            return
        if not _confirm(
                self, _t("Clear Today"),
                _t("Remove all sets logged today?"), _t("Clear")):
            return
        self.undo.checkpoint("Clear Today")
        before = self._undo_snapshot()
        self.data["log"].pop(today_key(), None)
        saved = self._save_or_rollback(before)
        self.undo.commit()
        if saved:
            self._refresh()

    def _on_key(self, w, ev):
        if hasattr(self, "undo") and nbapp.undo_keys(self.undo, ev):
            return True
        return super()._on_key(w, ev)

    # -- menus ---------------------------------------------------------------

    def menu_items(self, name):
        have = self._active() is not None
        if name == "File":
            return [
                ("New Exercise…", self._new_exercise),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            return nbapp.undo_menu_items(self.undo)
        if name == "Exercise":
            sets = self._sets_for(self._active()["id"]) if have else []
            return [
                ("Log a Set", (lambda: self._on_log(None, self.sel))
                 if have else None),
                ("Undo Last Set", (lambda: self._on_undo(None, self.sel))
                 if sets else None),
                nbapp.SEP,
                # Both of these stop and ask before they destroy anything
                # (_delete_exercise and _clear_today each run a confirmation),
                # so both carry the ellipsis that promises the question.
                ("Edit Exercise…", self._edit_exercise if have else None),
                ("Delete Exercise…", self._delete_exercise if have else None),
                nbapp.SEP,
                ("Clear Today…", self._clear_today
                 if self.data["log"].get(today_key()) else None),
            ]
        return super().menu_items(name)


def _pl(n):
    """The English plural suffix, in the shape nbi18n expects: a translation
    omits it entirely and forms plurals its own way (see nbi18n._spec_kinds)."""
    return "" if n == 1 else "s"


def _clamp_int(value, low, high, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _field(label, widget):
    """A dialog row: label on the left, control on the right."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    lbl = Gtk.Label(label=label, xalign=0)
    lbl.set_size_request(120, -1)
    row.pack_start(lbl, False, False, 0)
    widget.set_hexpand(True)
    row.pack_start(widget, True, True, 0)
    return row


def _confirm(parent, title, message, ok_label):
    """House-style destructive confirmation (see the Papertone dialog rules)."""
    dlg = Gtk.Dialog(transient_for=parent, modal=True)
    dlg.set_decorated(False)
    dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
    ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
    ok.get_style_context().add_class("destructive-action")
    dlg.set_default_response(Gtk.ResponseType.CANCEL)
    area = dlg.get_content_area()
    area.set_spacing(10)
    area.set_margin_top(18); area.set_margin_bottom(14)
    area.set_margin_start(20); area.set_margin_end(20)
    head = Gtk.Label(label=title, xalign=0)
    head.get_style_context().add_class("dlghead")
    area.add(head)
    msg = Gtk.Label(label=message, xalign=0)
    msg.set_line_wrap(True)
    msg.set_max_width_chars(44)
    area.add(msg)
    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    return resp == Gtk.ResponseType.OK


if __name__ == "__main__":
    nbapp.run(Workout)
