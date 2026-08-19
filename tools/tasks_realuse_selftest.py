#!/usr/bin/env python3
"""Real-use regression drive for the Tasks app, on the real widget tree.

Each check below is a thing a person did with the app and got wrong, driven
the same way (typing into the quick-add, the row menu, the Edit menu) through
tools/appdrive on an offscreen holder. Every check is named; a check fails by
name, never by crash.

  undo step granularity      an add, New List and Rename are undo steps of
                             their own, so Undo reverses exactly the last
                             action — it used to step back to the launch
                             baseline / last delete and write THAT to disk
  Remove List label          the Lists item acts at once, so it carries no
                             ellipsis (docs/MENU-CONVENTIONS.md §1)
  Next week heading          the row menu's "Next week" files the task under
                             NEXT WEEK, and This Week is the calendar week
  move to list               the row menu can file a task on a list / Inbox
  Upcoming title + order     the title covers Later; dated rows are in date
                             order inside a group

Run under the guest theme:
  NB_DRIVE_HOME_ROOT=<scratch> tools/guestrun.sh python3 tools/tasks_realuse_selftest.py
"""
import os
import sys
import json
import shutil
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NB_DRIVE_HOME_ROOT",
                      tempfile.mkdtemp(prefix="tasks-realuse-"))
HOME_ROOT = os.environ["NB_DRIVE_HOME_ROOT"]

import appdrive                                                  # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -- " + detail) if (detail and not cond) else ""))


def fresh():
    shutil.rmtree(HOME_ROOT, ignore_errors=True)
    os.makedirs(HOME_ROOT, exist_ok=True)
    return appdrive.Drive("tasks")


def add(d, text):
    e = d.find(Gtk.Entry)[0]
    e.grab_focus()
    d.type(text)
    d.key("Return")
    d.pump(0.2)


def titles(d):
    return [t["title"] for t in d.app.tasks]


def projects(d):
    return [n for n, _ in d.mod.PROJECTS]


def edit_menu(d):
    return [(it[0], it[1] is not None) for it in d.menu("Edit")
            if isinstance(it, tuple)]


def undo_label(d):
    return edit_menu(d)[0][0]


def row_menu(d, idx):
    """Open the REAL row menu; returns [(label, sensitive, button)]."""
    d.app._open_task_menu(idx, 300, 200)
    d.pump(0.1)
    return [(b.get_label(), b.get_sensitive(), b)
            for b in d.walk(d.app._tm_layer) if isinstance(b, Gtk.Button)]


def row_menu_click(d, idx, label):
    hits = [b for lab, _s, b in row_menu(d, idx) if lab == label]
    if not hits:
        d.app._close_task_menu()
        return False
    hits[0].clicked()
    d.pump(0.2)
    return True


def act(d, menu_name, prefix):
    """Fire a menu item by label prefix; False (not a crash) when absent."""
    try:
        return bool(d.menu_action(menu_name, prefix))
    except LookupError:
        return False


def new_list(d, name):
    d.menu_action("Lists", "New List")
    d.pump(0.2)
    d.app._nl_entry.grab_focus()
    d.type(name)
    d.key("Return")
    d.pump(0.3)


def store(d):
    p = os.path.join(d.home, ".config", "notebook", "tasks-app.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:                                             # noqa: BLE001
        return None


def headings(d):
    return [w.get_text() for w in d.walk() if isinstance(w, Gtk.Label)
            and ("grouphead" in w.get_style_context().list_classes()
                 or "groupover" in w.get_style_context().list_classes())]


def row_titles(d):
    """Task titles in the order the list shows them."""
    return [w.get_text() for w in d.walk() if isinstance(w, Gtk.Label)
            and ("tasktitle" in w.get_style_context().list_classes()
                 or "taskdone" in w.get_style_context().list_classes())]


# --------------------------------------------------------------- undo steps
def t_undo_steps():
    d = fresh()
    try:
        for t in ("Alpha", "Bravo", "Charlie"):
            add(d, t)
        check("undo step: an add is an undo step (Edit menu offers it)",
              edit_menu(d)[0][1] and "New Task" in undo_label(d),
              repr(edit_menu(d)))
        row_menu_click(d, 1, "Delete task")
        add(d, "Delta")
        add(d, "Echo")
        new_list(d, "Home")
        add(d, "Fix tap")
        # Ctrl+Z after all that must take back ONLY the last add.
        d.key("z", ctrl=True)
        d.pump(0.3)
        check("undo step: Ctrl+Z after adds reverses only the last add",
              titles(d) == ["Alpha", "Charlie", "Delta", "Echo"]
              and projects(d) == ["Home"],
              "%r %r" % (titles(d), projects(d)))
        st = store(d)
        check("undo step: the store holds every task entered before the undo",
              st is not None
              and [t["title"] for t in st["tasks"]] == ["Alpha", "Charlie",
                                                        "Delta", "Echo"]
              and [p[0] for p in st["projects"]] == ["Home"],
              repr(st))
        # New List is a step of its own.
        d.key("z", ctrl=True)
        d.pump(0.3)
        check("undo step: New List is its own undo step",
              projects(d) == [] and titles(d) == ["Alpha", "Charlie",
                                                  "Delta", "Echo"],
              "%r %r" % (titles(d), projects(d)))
        gone = projects(d) == [] and "Fix tap" not in titles(d)
        d.key("z", ctrl=True, shift=True)
        d.pump(0.3)
        d.key("z", ctrl=True, shift=True)
        d.pump(0.3)
        check("undo step: Redo brings the list and the task back",
              gone and projects(d) == ["Home"] and titles(d)[-1] == "Fix tap",
              "%r %r" % (titles(d), projects(d)))
        # Rename is a step of its own, under its own name.
        d.app._open_rename(0)
        d.pump(0.1)
        d.app._rn_entry.set_text("Alpha two")
        d.key("Return")
        d.pump(0.2)
        check("undo step: Rename is its own undo step, named",
              titles(d)[0] == "Alpha two" and "Rename Task" in undo_label(d),
              "%r %r" % (titles(d), undo_label(d)))
        renamed = titles(d)[0] == "Alpha two"
        act(d, "Edit", "Undo")
        d.pump(0.2)
        check("undo step: Undo Rename restores the old title and nothing else",
              renamed
              and titles(d) == ["Alpha", "Charlie", "Delta", "Echo", "Fix tap"]
              and projects(d) == ["Home"],
              "%r %r" % (titles(d), projects(d)))
    finally:
        d.close()


# ---------------------------------------------------------- Remove List label
def t_remove_list_label():
    d = fresh()
    try:
        new_list(d, "Work")
        add(d, "Report")
        items = [it[0] for it in d.menu("Lists") if isinstance(it, tuple)]
        label = [l for l in items if l.startswith("Remove List")]
        check("Remove List: the Lists item carries no ellipsis (it acts at once)",
              label == ["Remove List"], repr(items))
        check("Remove List: no confirm card is left in the code",
              not hasattr(d.app, "_open_removelist"))
    finally:
        d.close()


# ------------------------------------------------------------ Next week heading
def t_next_week():
    d = fresh()
    try:
        add(d, "Water plants")
        ok = row_menu_click(d, 0, "Next week")
        d.menu_action("View", "    Upcoming")
        d.pump(0.1)
        heads = headings(d)
        check("Next week: the row menu files the task under NEXT WEEK",
              ok and heads == ["NEXT WEEK"], repr(heads))
        # The bucket rule itself, on a fixed clock: This Week ends on Sunday.
        mod = d.mod
        real_today = mod._today
        try:
            mod._today = lambda: date(2026, 8, 16)          # a Sunday
            due = mod.Tasks._due_of
            got = [due({"date": "2026-08-17"}), due({"date": "2026-08-18"}),
                   due({"date": "2026-08-23"}), due({"date": "2026-08-24"})]
            check("Next week: on a Sunday, next Sunday is not 'This Week'",
                  got == ["tomorrow", "nextweek", "nextweek", "later"],
                  repr(got))
            mod._today = lambda: date(2026, 8, 19)          # a Wednesday
            got = [due({"date": "2026-08-21"}), due({"date": "2026-08-23"}),
                   due({"date": "2026-08-24"}), due({"date": "2026-08-30"}),
                   due({"date": "2026-08-31"})]
            check("Next week: This Week runs to Sunday, Next week to the one after",
                  got == ["week", "week", "nextweek", "nextweek", "later"],
                  repr(got))
        finally:
            mod._today = real_today
        check("Next week: the Upcoming view counts the Next week group",
              "nextweek" in d.app._view_dues("view:upcoming"))
    finally:
        d.close()


# ---------------------------------------------------------------- move to list
def t_move_to_list():
    d = fresh()
    try:
        add(d, "Buy milk")
        new_list(d, "Groceries")
        d.menu_action("View", "    Today")
        d.pump(0.1)
        menu = row_menu(d, 0)
        labels = [lab for lab, _s, _b in menu]
        d.app._close_task_menu()
        check("move to list: the row menu offers each list and Inbox",
              "Groceries" in labels and "Inbox" in labels, repr(labels))
        check("move to list: the row for where the task already is greys out",
              [s for lab, s, _b in menu if lab == "Inbox"] == [False],
              repr([(lab, s) for lab, s, _b in menu]))
        row_menu_click(d, 0, "Groceries")
        check("move to list: choosing a list files the task on it",
              d.app.tasks[0]["project"] == "Groceries",
              repr(d.app.tasks[0]))
        st = store(d)
        check("move to list: the move is persisted",
              st is not None and st["tasks"][0]["project"] == "Groceries")
        check("move to list: the move is an undo step",
              "Move Task" in undo_label(d), undo_label(d))
        moved = d.app.tasks[0]["project"] == "Groceries"
        act(d, "Edit", "Undo")
        d.pump(0.2)
        check("move to list: Undo puts the task back",
              moved and d.app.tasks[0]["project"] is None)
        act(d, "Edit", "Redo")
        d.pump(0.2)
        moved = d.app.tasks[0]["project"] == "Groceries"
        row_menu_click(d, 0, "Inbox")
        check("move to list: Inbox takes the task off its list",
              moved and d.app.tasks[0]["project"] is None,
              repr(d.app.tasks[0]))
    finally:
        d.close()


# ------------------------------------------------------- Upcoming title + order
def t_upcoming():
    d = fresh()
    try:
        add(d, "Later one")
        add(d, "Sooner one")
        add(d, "Far away")
        far = date.today() + timedelta(days=60)
        # Two rows in the SAME group, entered later-first.
        d.app.tasks[0]["date"] = (far + timedelta(days=5)).isoformat()
        d.app.tasks[1]["date"] = far.isoformat()
        d.app.tasks[2]["date"] = (far + timedelta(days=30)).isoformat()
        d.app._save_tasks()
        d.menu_action("View", "    Upcoming")
        d.pump(0.1)
        check("Upcoming: the title covers a task weeks out (not 'The week ahead')",
              d.app.title_lbl.get_text() != "The week ahead"
              and headings(d) == ["LATER"],
              "%r %r" % (d.app.title_lbl.get_text(), headings(d)))
        check("Upcoming: dated rows are in date order inside a group",
              row_titles(d) == ["Sooner one", "Later one", "Far away"],
              repr(row_titles(d)))
    finally:
        d.close()


for fn in (t_undo_steps, t_remove_list_label, t_next_week, t_move_to_list,
           t_upcoming):
    try:
        fn()
    except Exception as exc:                                      # noqa: BLE001
        check("%s ran without raising" % fn.__name__, False,
              "%s: %s" % (type(exc).__name__, exc))

bad = [n for n, ok in RESULTS if not ok]
print("RESULT: %s (%d checks, %d failed)" % ("PASS" if not bad else "FAILED",
                                            len(RESULTS), len(bad)))
raise SystemExit(1 if bad else 0)
