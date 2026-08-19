#!/usr/bin/env python3
"""Real-use regression drive for Academics, on the real widget tree.

Each check below is something a student did with the app and got wrong, driven
the way she did it (the sidebar's Add button, the real dialogs and their real
buttons, the File menu, a row's own checkbox) through tools/appdrive on an
offscreen holder at the 1024x740 panel. Every check is named; a check fails by
name, never by crash.

  AC-1 sidebar counts     a homework edit updates the sidebar's per-class
                          "N to do" rows, not only the header count
  AC-2 New Lecture        made from Schedule or Homework it brings the Notes
                          view forward, so the lecture and the title field
                          being typed into are on screen
  AC-3 delete-class card  says the assignments are KEPT, which is what the
                          code then does with them
  AC-4 status + chooser   a Schedule/Homework action reports itself on that
                          screen, and Delete Class asks which class instead of
                          pointing at a sidebar that cannot answer
  AC-5 blank class name   the card stays open and says what is missing rather
                          than closing and discarding the room and instructor
  AC-6 Next week          the quick button does not file the assignment under
                          the heading THIS WEEK
  AC-7 Schedule header    a class in session is named as running now
  AC-8 sidebar count      "2 classes · 2 lec…" is not truncated at 1024
  AC-9 save indicator     a fresh install claims no save until one happens,
                          and a new lecture is written to the store

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 \\
      tools/academics_realuse_selftest.py
"""
import os
import sys
import json
import time
import shutil
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="academics-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]
STORE = os.path.join(HOME_ROOT, "academics", ".config", "notebook",
                     "academics.json")

import appdrive                                                   # noqa: E402
from gi.repository import Gtk, GLib                                # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name
          + (("  -- " + detail) if (detail and not cond) else ""))


# ------------------------------------------------------------------ fixtures
def dkey(off):
    return (date.today() + timedelta(days=off)).isoformat()


def lecture(cls, num, title):
    return {"cls": cls, "num": num, "title": title, "date": dkey(0),
            "meta": "", "notes": "notes", "ranges": {}}


def two_classes(homework=True):
    """A term a person would actually have: two classes, a lecture each, and
    a spread of assignments over both."""
    hw = [
        {"title": "Problem set 3", "cls": 0, "due": dkey(1), "done": False,
         "kind": "work", "note": ""},
        {"title": "Midterm", "cls": 0, "due": dkey(2), "done": False,
         "kind": "exam", "note": ""},
        {"title": "Lab report", "cls": 0, "due": dkey(3), "done": False,
         "kind": "work", "note": ""},
        {"title": "Titration write-up", "cls": 1, "due": dkey(3),
         "done": False, "kind": "work", "note": ""},
        {"title": "Reading", "cls": 1, "due": "", "done": False,
         "kind": "work", "note": ""},
    ]
    return {"classes": [
        {"label": "Physics 101", "color": "#9A7B4F", "room": "",
         "instructor": "", "meets": [{"day": 1, "start": "09:00",
                                      "end": "10:00", "room": ""}]},
        {"label": "Chemistry", "color": "#4A5E73", "room": "",
         "instructor": "", "meets": [{"day": 2, "start": "11:00",
                                      "end": "12:00", "room": ""}]}],
        "lectures": [lecture(0, "01", "Kinematics"),
                     lecture(1, "01", "Bonds")],
        "homework": hw if homework else [],
        "active": 0}


def fresh(store=None):
    """A drive on an empty NB_HOME, with `store` written first when given."""
    shutil.rmtree(HOME_ROOT, ignore_errors=True)
    if store is not None:
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        with open(STORE, "w") as fh:
            json.dump(store, fh)
    return appdrive.Drive("academics")


# ------------------------------------------------------------------ helpers
def walk(w):
    out, i = [w], 0
    while i < len(out):
        x = out[i]
        i += 1
        if isinstance(x, Gtk.Container):
            out.extend(x.get_children())
    return out


def texts_of(w):
    out = []
    for x in walk(w):
        if not x.get_visible():
            continue
        if isinstance(x, Gtk.Label):
            out.append(x.get_text())
        elif isinstance(x, Gtk.Entry):
            out.append("[entry:%s]" % x.get_text())
    return out


def side_texts(d):
    return texts_of(d.app.side_list)


def dialogs():
    return [w for w in Gtk.Window.list_toplevels()
            if isinstance(w, Gtk.Dialog) and w.get_visible()]


def dlg_button(dlg, label):
    for w in walk(dlg):
        if not isinstance(w, Gtk.Button):
            continue
        child = w.get_child()
        if isinstance(child, Gtk.Label) and child.get_text() == label:
            return w
        try:
            if w.get_label() == label:
                return w
        except Exception:                                         # noqa: BLE001
            pass
    return None


def dlg_entries(dlg):
    return [w for w in walk(dlg) if isinstance(w, Gtk.Entry)]


def dlg_combos(dlg):
    return [w for w in walk(dlg) if isinstance(w, Gtk.ComboBoxText)]


# The poll waiting for a dialog, so a wait that never found one can be called
# off. A LEFT-OVER POLL IS A LIAR: when the app under test is missing the fix
# and never opens the card, the poll from that check is still running when the
# NEXT check opens one, grabs it, cancels it, and reports "no dialog appeared"
# against a check that had nothing wrong with it.
_POLL = {"id": None}


def _stop_poll():
    if _POLL["id"]:
        try:
            GLib.source_remove(_POLL["id"])
        except Exception:                                         # noqa: BLE001
            pass
        _POLL["id"] = None


def on_dialog(fn, delay=100, tries=40):
    """Drive the next modal dialog from a timer.

    dlg.run() blocks the caller, so the dialog can only be answered from the
    main loop it is spinning. Returns a dict the caller reads afterwards;
    ["seen"] says whether a dialog ever appeared."""
    _stop_poll()
    state = {"seen": False, "n": 0}

    def poll():
        ds = dialogs()
        if not ds:
            state["n"] += 1
            if state["n"] < tries:
                return True
            _POLL["id"] = None
            return False
        _POLL["id"] = None
        state["seen"] = True
        try:
            fn(ds[-1], state)
        except Exception as exc:                                  # noqa: BLE001
            state["exc"] = "%s: %s" % (type(exc).__name__, exc)
            ds[-1].response(Gtk.ResponseType.CANCEL)
        return False
    _POLL["id"] = GLib.timeout_add(delay, poll)
    return state


def later(fn, delay=250):
    """Run fn on a later turn of the loop the dialog is spinning."""
    GLib.timeout_add(delay, lambda: (fn(), False)[1])


def hw_checkbox(d, title):
    """The real tick box of the homework row titled `title`."""
    rows = walk(d.app.hw_list)
    box = None
    for w in rows:
        if isinstance(w, Gtk.Label) and w.get_text() == title:
            box = w
            break
    if box is None:
        return None
    node = box
    for _ in range(5):
        node = node.get_parent()
        if node is None:
            return None
        found = [c for c in walk(node) if isinstance(c, Gtk.CheckButton)]
        if found:
            return found[0]
    return None


def add_assignment(d, name, cls_pos=None, quick=None, due=None):
    """Add one assignment through the real 'Add an assignment' card.
    `cls_pos` is the combo position (0 = No class); `quick` a quick-date
    button label."""
    def fill(dlg, _st):
        ents = dlg_entries(dlg)
        ents[0].set_text(name)
        if cls_pos is not None:
            dlg_combos(dlg)[0].set_active(cls_pos)
        if quick:
            dlg_button(dlg, quick).clicked()
        if due is not None:
            ents[1].set_text(due)
        _st["due"] = ents[1].get_text()
        dlg_button(dlg, "Add").clicked()
    st = on_dialog(fill)
    d.click("Add an assignment")
    d.pump(0.2)
    return st


# ------------------------------------------------------------------- AC-1
def t_ac1_sidebar_counts_follow_a_homework_edit():
    d = fresh(two_classes())
    try:
        d.app._set_view("homework")
        d.pump(0.2)
        before = side_texts(d)
        tick = hw_checkbox(d, "Problem set 3")
        if tick is None:
            check("AC-1 the homework row carries a real tick box", False)
            return
        tick.set_active(True)
        d.pump(0.2)
        after = side_texts(d)
        check("AC-1 ticking an assignment updates its class row in the sidebar",
              before[:2] == ["Physics 101", "3 to do"]
              and after[:2] == ["Physics 101", "2 to do"],
              "before %r after %r" % (before, after))
        add_assignment(d, "Essay draft", cls_pos=2, due=dkey(2))
        rows = side_texts(d)
        check("AC-1 adding an assignment updates its class row in the sidebar",
              rows[2:4] == ["Chemistry", "3 to do"], repr(rows))
        add_assignment(d, "Loose end", cls_pos=0, due=dkey(2))
        rows = side_texts(d)
        check("AC-1 an untied assignment is counted in the sidebar at once",
              "1 not tied to a class" in rows, repr(rows))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-2
def t_ac2_new_lecture_shows_the_notes_view():
    d = fresh(two_classes())
    try:
        for view in ("homework", "schedule"):
            d.app._set_view(view)
            d.pump(0.2)
            n = len(d.app.lectures)
            d.menu_action("File", "New Lecture")
            d.pump(0.3)
            made = len(d.app.lectures) == n + 1
            focus = d.app.get_focus()
            check("AC-2 New Lecture from the %s view opens the Notes view"
                  % view,
                  made and d.app.view == "notes",
                  "view=%r lectures %d->%d" % (d.app.view, n,
                                               len(d.app.lectures)))
            check("AC-2 the title field New Lecture focuses from the %s view "
                  "is on screen" % view,
                  focus is not None and focus.get_mapped(),
                  "focus=%r mapped=%s" % (focus, focus and focus.get_mapped()))
        d.type("Seismic waves")
        d.pump(0.2)
        check("AC-2 what is typed after New Lecture is visible on screen",
              "Seismic waves" in d.texts()
              and d.app.lectures[-1]["title"] == "Seismic waves",
              repr(d.app.lectures[-1]["title"]))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-3
def t_ac3_delete_class_card_says_assignments_are_kept():
    d = fresh(two_classes())
    try:
        n_hw = len(d.app.homework)

        def read(dlg, st):
            st["texts"] = texts_of(dlg)
            dlg_button(dlg, "Delete").clicked()
        st = on_dialog(read)
        d.app._delete_class_at(0)
        d.pump(0.3)
        said = " ".join(st.get("texts") or [])
        check("AC-3 the delete-class card does not claim the assignments "
              "will be removed",
              st["seen"] and "assignments will be removed" not in said,
              repr(said))
        check("AC-3 the delete-class card says the assignments are kept",
              "assignments are kept" in said, repr(said))
        kept = len(d.app.homework)
        check("AC-3 the assignments really are kept, untied",
              kept == n_hw and sorted(h["cls"] for h in d.app.homework)[:3]
              == [-1, -1, -1],
              "%d -> %d, cls %r" % (n_hw, kept,
                                    [h["cls"] for h in d.app.homework]))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-4
def t_ac4_a_view_action_reports_itself_on_that_view():
    d = fresh(two_classes())
    try:
        d.app._set_view("homework")
        # No lecture open and two classes: which class is genuinely ambiguous.
        d.app.active = -1
        d.pump(0.2)
        st = on_dialog(lambda dlg, s: (s.__setitem__("texts", texts_of(dlg)),
                                       dlg.response(Gtk.ResponseType.CANCEL)))
        d.menu_action("File", "Delete Class")
        d.pump(0.3)
        check("AC-4 Delete Class with no open lecture asks which class",
              st["seen"], "no dialog appeared; savelbl=%r"
              % d.app.savelbl.get_text())
        d.menu_action("File", "Export")
        d.pump(0.5)
        made = os.path.exists(os.path.join(HOME_ROOT, "academics", "Documents",
                                           "homework.pdf"))
        line = d.app.hw_sub.get_text()
        check("AC-4 an export from the Homework view says so on that screen",
              made and line == "Exported to Documents"
              and d.app.hw_sub.get_mapped(),
              "pdf=%s hw_sub=%r mapped=%s"
              % (made, line, d.app.hw_sub.get_mapped()))
        # ...and the header goes back to being the header.
        d.app._refresh_homework()
        check("AC-4 the header returns to its own subtitle after the message",
              d.app.hw_sub.get_text().endswith("to do"),
              repr(d.app.hw_sub.get_text()))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-5
def t_ac5_a_blank_class_name_keeps_the_card():
    d = fresh(two_classes())
    try:
        def blank_name(dlg, st):
            ents = dlg_entries(dlg)
            ents[0].set_text("")
            ents[1].set_text("Baker Hall A51")
            ents[2].set_text("Dr Peraza")
            dlg_button(dlg, "Save").clicked()

            def inspect():
                st["open"] = dlg in dialogs()
                st["texts"] = texts_of(dlg) if st["open"] else []
                if st["open"]:
                    dlg.response(Gtk.ResponseType.CANCEL)
            later(inspect)
        st = on_dialog(blank_name)
        d.app._edit_class(0)
        d.pump(0.8)
        said = " ".join(st.get("texts") or [])
        check("AC-5 a blank class name keeps the card open",
              bool(st.get("open")), "card closed; class=%r"
              % d.app.classes[0].get("label"))
        check("AC-5 a blank class name says what is missing",
              "Enter a name" in said, repr(said))
        check("AC-5 the room typed beside a blank name is still in the card",
              "[entry:Baker Hall A51]" in (st.get("texts") or []), repr(said))

        def blank_rename(dlg, st):
            dlg_entries(dlg)[0].set_text("   ")
            dlg_button(dlg, "Rename").clicked()

            def inspect():
                st["open"] = dlg in dialogs()
                if st["open"]:
                    dlg.response(Gtk.ResponseType.CANCEL)
            later(inspect)
        st2 = on_dialog(blank_rename)
        d.app._rename_class()
        d.pump(0.8)
        check("AC-5 a blank rename keeps the card open",
              st2["seen"] and bool(st2.get("open")),
              "seen=%s open=%s" % (st2["seen"], st2.get("open")))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-6
def t_ac6_next_week_is_not_this_week():
    d = fresh(two_classes())
    try:
        d.app._set_view("homework")
        d.pump(0.2)
        n0 = len(d.app.homework)
        st = add_assignment(d, "Exactly a week", cls_pos=0, quick="Next week")
        i = len(d.app.homework) - 1
        h = d.app.homework[i] if d.app.homework else {}
        # The check is only worth anything if the assignment really was added,
        # by the real button, exactly seven days out.
        added = (len(d.app.homework) == n0 + 1
                 and h.get("title") == "Exactly a week"
                 and h.get("due") == dkey(7))
        group = [(key, name) for key, name, idxs in d.app._homework_buckets()
                 if i in idxs]
        check("AC-6 the Next week button does not file the assignment under "
              "THIS WEEK",
              added and bool(group) and group[0][0] != "week",
              "added=%s due %r landed in %r" % (added, h.get("due"), group))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-7
def t_ac7_schedule_header_names_the_class_in_session():
    now = time.localtime()
    mins = now.tm_hour * 60 + now.tm_min
    start, end = max(0, mins - 30), min(24 * 60 - 1, mins + 30)
    running = {"classes": [{"label": "Biology", "color": "#4A5E73",
                            "room": "Room 4", "instructor": "",
                            "meets": [{"day": now.tm_wday,
                                       "start": "%02d:%02d" % (start // 60,
                                                               start % 60),
                                       "end": "%02d:%02d" % (end // 60,
                                                             end % 60),
                                       "room": "Room 4"}]}],
               "lectures": [], "homework": [], "active": -1}
    d = fresh(running)
    try:
        d.app._set_view("schedule")
        d.pump(0.3)
        said = d.app.sched_sub.get_text()
        check("AC-7 the Schedule header names the class in session now",
              said.startswith("Now:") and "Biology" in said, repr(said))
    finally:
        d.close()
    # Control: nothing running, and the header still says what is next.
    later_day = dict(running)
    later_day["classes"] = [dict(running["classes"][0])]
    later_day["classes"][0]["meets"] = [{"day": (now.tm_wday + 2) % 7,
                                         "start": "09:00", "end": "10:00",
                                         "room": "Room 4"}]
    d = fresh(later_day)
    try:
        d.app._set_view("schedule")
        d.pump(0.3)
        said = d.app.sched_sub.get_text()
        check("AC-7 with no class running the header still names the next one",
              said.startswith("Next:") and "Biology" in said, repr(said))
    finally:
        d.close()


# ------------------------------------------------------------------- AC-8
def t_ac8_sidebar_count_is_not_truncated():
    d = fresh(two_classes())
    try:
        lbl = d.app.side_summary
        d.pump(0.2)
        cut = lbl.get_layout().is_ellipsized()
        wide = d.app.side_list.get_parent().get_allocated_width()
        check("AC-8 the sidebar count is not cut off at 1024",
              not cut and "2 classes" in lbl.get_text()
              and "2 lectures" in lbl.get_text(),
              "ellipsized=%s text=%r" % (cut, lbl.get_text()))
        check("AC-8 showing it whole did not widen the sidebar",
              wide <= 240, "sidebar %dpx" % wide)
    finally:
        d.close()


# ------------------------------------------------------------------- AC-9
def t_ac9_save_indicator_tells_the_truth():
    d = fresh()
    try:
        said = d.app.savelbl.get_text()
        check("AC-9 a fresh install claims no save before anything is written",
              said == "" and not os.path.exists(STORE),
              "savelbl=%r store=%s" % (said, os.path.exists(STORE)))
        d.click("New Lecture")
        d.pump(0.5)
        check("AC-9 a new lecture is written to the store",
              os.path.exists(STORE), "store still absent")
        # ...and says WHEN, from the store's own timestamp rather than from
        # the clock the window happened to open at.
        stamp = (time.strftime("%H:%M", time.localtime(os.path.getmtime(STORE)))
                 if os.path.exists(STORE) else None)
        check("AC-9 the indicator reports the save that just happened",
              stamp is not None
              and d.app.savelbl.get_text() == "Saved %s" % stamp
              and d.app.savedot.get_visible(),
              "savelbl=%r stamp=%r dot=%s" % (d.app.savelbl.get_text(), stamp,
                                              d.app.savedot.get_visible()))
    finally:
        d.close()


for fn in (t_ac1_sidebar_counts_follow_a_homework_edit,
           t_ac2_new_lecture_shows_the_notes_view,
           t_ac3_delete_class_card_says_assignments_are_kept,
           t_ac4_a_view_action_reports_itself_on_that_view,
           t_ac5_a_blank_class_name_keeps_the_card,
           t_ac6_next_week_is_not_this_week,
           t_ac7_schedule_header_names_the_class_in_session,
           t_ac8_sidebar_count_is_not_truncated,
           t_ac9_save_indicator_tells_the_truth):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))
    _stop_poll()

bad = [n for n, ok in RESULTS if not ok]
print("RESULT: %s (%d checks, %d failed)"
      % ("PASS" if not bad else "FAILED", len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
