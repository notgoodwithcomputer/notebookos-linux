#!/usr/bin/env python3
"""Headless (no-display) test for the Tasks app's list/task reconciliation.

THE BUG THIS EXISTS FOR: a task's list assignment and the list definitions are
persisted as two separate facts. A task carries {"project": "Home"}; the "Home"
list itself lives under the sidecar's "projects" key. So the definitions can go
missing on their own -- a sidecar that lost its wrapper is read as a bare task
list with no "projects" key at all (tasks._read_meta handles exactly this), and
a Tasks document can carry tasks with no "lists" beside them.

The tasks then loaded onto a list that appeared in NO sidebar row, in NO Lists
menu and in NO view: unreachable. Worse, Tasks.__init__ saves immediately after
loading, so that first write persisted "projects": [] over the store -- turning
a mismatch that still had the list NAME on every task into a permanent loss.

Runs the real loader on real files; builds no window, so it needs no DISPLAY.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/tasks_selftest.py
"""
import json
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))

os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbtasks-"))
HOME = os.environ["NB_HOME"]
CFG = os.path.join(HOME, ".config", "notebook")
os.makedirs(CFG, exist_ok=True)

import gi                                                    # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

import tasks                                                 # noqa: E402

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


class Probe(object):
    """The Tasks load/save path with no widgets: the real methods, bound to a
    bare object. Only the display-side collaborators are stubbed."""
    for _m in ("_read_meta", "_read_flat", "_load_tasks", "_norm_task",
               "_overlay_flat", "_from_flat", "_adopt_orphan_lists",
               "_load_state", "_save_tasks", "_merge_external_ticks",
               "_open_doc", "_delete_task", "_toggle"):
        locals()[_m] = getattr(tasks.Tasks, _m)
    del _m
    _done_by_occurrence = staticmethod(tasks.Tasks._done_by_occurrence)

    view = "view:today"
    _doc_path = None

    def _load_events(self):
        return []

    def _rebuild_sidebar(self):
        pass

    def _flash(self, _text):
        pass

    def _close_task_menu(self):
        pass

    def _refresh(self):
        pass

    def _update_counts(self):
        pass


def forget_lists():
    """Clear only the module-global list registry — a fresh process, same
    store on disk."""
    tasks.PROJECTS[:] = []
    tasks.PROJ_COLOR.clear()


def reset():
    """Clear the list registry AND the store, so a case starts from a fresh
    install."""
    forget_lists()
    for p in (tasks.TASKS_FILE, tasks.META_FILE):
        if os.path.exists(p):
            os.remove(p)


def write(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh)


T_HOME = {"title": "Bleed the radiators", "project": "Home", "due": "today",
          "date": "", "time": "", "prio": 0, "done": False}
T_WORK = {"title": "Send the invoice", "project": "Work", "due": "today",
          "date": "", "time": "", "prio": 1, "done": False}

# --- case 1: a sidecar that lost its {tasks, projects} wrapper ---------------
# _read_meta reads a bare list as the task list it plainly is, so the tasks
# (and their "Home"/"Work" assignments) survive -- but nothing carries the list
# definitions, and every task is left on a list the app does not have.
reset()
write(tasks.META_FILE, [T_HOME, T_WORK])
write(tasks.TASKS_FILE, [{"text": T_HOME["title"], "done": False},
                         {"text": T_WORK["title"], "done": False}])
p = Probe()
p._load_state()

check("case 1: both tasks loaded", len(p.tasks) == 2)
check("case 1: list assignments kept",
      [t["project"] for t in p.tasks] == ["Home", "Work"])
check("case 1: 'Home' list exists", "Home" in tasks.PROJ_COLOR)
check("case 1: 'Work' list exists", "Work" in tasks.PROJ_COLOR)
check("case 1: no task is on a list the app cannot show",
      all(t["project"] in tasks.PROJ_COLOR for t in p.tasks if t["project"]))
check("case 1: recreated lists get distinct colours",
      tasks.PROJ_COLOR.get("Home") != tasks.PROJ_COLOR.get("Work"))

# The launch-time save must now PERSIST those lists. Before the fix it wrote
# "projects": [] straight over the store, making the mismatch permanent.
p._save_tasks()
with open(tasks.META_FILE) as fh:
    saved = json.load(fh)
check("case 1: lists persisted on the launch-time save",
      sorted(n for n, _c in saved.get("projects", [])) == ["Home", "Work"])

# ...and a second launch off that saved store is stable: same lists, no dupes.
forget_lists()
p2 = Probe()
p2._load_state()
check("case 1: reload restores exactly the two lists",
      sorted(n for n, _c in tasks.PROJECTS) == ["Home", "Work"])

# --- case 2: a healthy store is left completely alone -----------------------
# The reconciliation must only ADD what is missing: a real store's own colours
# and list order have to survive untouched, and Inbox (project None) must never
# become a list.
reset()
write(tasks.META_FILE, {
    "tasks": [T_HOME, dict(T_WORK, project=None)],
    "projects": [["Home", "#4A5E73"]]})
write(tasks.TASKS_FILE, [{"text": T_HOME["title"], "done": False},
                         {"text": T_WORK["title"], "done": False}])
p3 = Probe()
p3._load_state()
check("case 2: stored list colour untouched",
      tasks.PROJ_COLOR.get("Home") == "#4A5E73")
check("case 2: no list invented for an unfiled task",
      [n for n, _c in tasks.PROJECTS] == ["Home"])

# Loading another instance in the same interpreter must not inherit the first
# instance's module-global list registry. This happens in tests, profile
# switching, and any launcher that reuses the Python process. Before the fix,
# the empty profile acquired Home and __init__'s launch-time save persisted it.
write(tasks.META_FILE, {"tasks": [], "projects": []})
write(tasks.TASKS_FILE, [])
p_empty = Probe()
p_empty._load_state()
check("case 2: a second empty profile does not inherit prior lists",
      tasks.PROJECTS == [] and tasks.PROJ_COLOR == {})

# --- case 3: a document whose tasks name lists it never defined -------------
reset()
doc = os.path.join(HOME, "Documents", "shopping.json")
os.makedirs(os.path.dirname(doc), exist_ok=True)
write(doc, {"tasks": [dict(T_HOME, project="Garden")]})
p4 = Probe()
p4.tasks = []
check("case 3: document opened", p4._open_doc(doc) is True)
check("case 3: 'Garden' list recreated from the task", "Garden" in tasks.PROJ_COLOR)
check("case 3: task still on its list",
      p4.tasks[0]["project"] == "Garden")

try:
    real_t = tasks._t
    tasks._t = lambda s: "translated<%s>" % s
    shown = tasks._display_date((2026, 8, 7, 0, 0, 0, 4, 0, -1))
    check("dates are translated as one reorderable phrase",
          shown == "translated<Friday, 7 August>")
except AttributeError as exc:
    check("dates are translated as one reorderable phrase", False)
    print("[not reached: %s]" % exc)
finally:
    tasks._t = real_t

# --- case 4: destructive task actions participate in OS undo ---------------
p5 = Probe()
p5.tasks = [dict(T_HOME), dict(T_WORK)]
p5._flat_base = []
p5._save_warned = False
try:
    import nbapp
    p5._undo_snapshot = tasks.Tasks._undo_snapshot.__get__(p5, Probe)
    p5._restore_undo_snapshot = tasks.Tasks._restore_undo_snapshot.__get__(p5, Probe)
    p5.undo = nbapp.UndoHistory(p5._undo_snapshot, p5._restore_undo_snapshot)
    p5.undo.reset()
    p5._delete_task(0)
    reached = p5.undo.undo()
    restored = [t["title"] for t in p5.tasks]
    check("case 4: undo restores a deleted task in its original order",
          reached and restored == [T_HOME["title"], T_WORK["title"]])
    p5._rows = {}
    p5._toggle(None, 0)
    completed = p5.tasks[0]["done"]
    p5.undo.undo()
    check("case 4: undo reverses completion without dropping the row",
          completed is True and p5.tasks[0]["done"] is False
          and len(p5.tasks) == 2)
except AttributeError as exc:
    check("case 4: undo restores a deleted task in its original order",
          False)
    print("[not reached: %s]" % exc)

# case 5: the Today title never truncates a date — it degrades WHOLE.
# Greek's full weekday phrase outgrows the 1024 centre column (the sweep
# caught 'Παρασκευή, 7 Αυγο…'); when the full phrase cannot fit, the label
# must switch to the whole day-and-month phrase, and switch back when room
# returns. Language-independent mechanism test through the real _fit_title
# against measured pixel widths; the Greek case is one instance of it.
try:
    from gi.repository import Gdk, Gtk
    p6 = tasks.Tasks.__new__(tasks.Tasks)
    lbl = Gtk.Label(label="")
    p6.title_lbl = lbl
    p6._title_full = "Friday, 7 August in its very longest form"
    p6._title_short = "7 August"
    full_w = lbl.create_pango_layout(p6._title_full).get_pixel_size()[0]
    tight = Gdk.Rectangle()
    tight.width, tight.height = max(10, full_w - 10), 35
    roomy = Gdk.Rectangle()
    roomy.width, roomy.height = full_w + 10, 35
    p6._fit_title(lbl, tight)
    check("case 5: a date too wide for its column degrades whole, not cut",
          lbl.get_text() == p6._title_short)
    p6._fit_title(lbl, roomy)
    check("case 5: the full date returns when the room does",
          lbl.get_text() == p6._title_full)
    p6._title_short = None
    lbl.set_text("Kitchen renovation and general household repairs")
    p6._fit_title(lbl, tight)
    check("case 5: a list name keeps the plain ellipsis (no shorter truth)",
          lbl.get_text().startswith("Kitchen"))
except AttributeError as exc:
    check("case 5: a date too wide for its column degrades whole, not cut",
          False)
    print("[not reached: %s]" % exc)

# ---- a damaged sidecar: the bytes are kept, and the person is TOLD -------
# The flat file carries every task TITLE, so nothing looks lost — but the due
# dates, priorities and lists live only in the sidecar, and the launch save
# rewrites it. Every task came back as an undated Today item with no list and
# nothing said so. The store IS the data here, so this keeps saving (the
# damaged-store doctrine); what was missing was the telling.
import json as _json
_d = tempfile.mkdtemp(prefix="tasks-damaged-")
os.environ["NB_HOME"] = _d
_cfg = os.path.join(_d, ".config", "notebook")
os.makedirs(_cfg, exist_ok=True)
_meta = os.path.join(_cfg, os.path.basename(tasks.META_FILE))
_rich = '{"tasks": [{"text": "File tax return", "due": "2026-09-01"}] BROKEN'
open(_meta, "w").write(_rich)

_p = tasks.Tasks.__new__(tasks.Tasks)
_real_meta = tasks.META_FILE
tasks.META_FILE = _meta
try:
    got = tasks.Tasks._read_meta(_p)
finally:
    tasks.META_FILE = _real_meta
check("a sidecar that will not parse is marked damaged, so the person is told",
      getattr(_p, "_meta_damaged", False) is True)
check("...and it still reads as no usable metadata", got is None)

# A file can parse as JSON and still have no valid Tasks-store shape. Scalars
# must take the same damaged-store path as malformed JSON; otherwise launch
# treats them as first-run and silently replaces them.
for _scalar in (42, "bad", None):
    with open(_meta, "w") as _fh:
        _json.dump(_scalar, _fh)
    _scalar_probe = tasks.Tasks.__new__(tasks.Tasks)
    _damaged_before = set(glob.glob(_meta + ".damaged-*"))
    tasks.META_FILE = _meta
    try:
        _scalar_got = tasks.Tasks._read_meta(_scalar_probe)
    finally:
        tasks.META_FILE = _real_meta
    check("valid JSON scalar %r is marked damaged" % (_scalar,),
          _scalar_got is None
          and getattr(_scalar_probe, "_meta_damaged", False) is True)
    _damaged_after = set(glob.glob(_meta + ".damaged-*"))
    check("valid JSON scalar %r is quarantined before launch save" % (_scalar,),
          not os.path.exists(_meta)
          and len(_damaged_after - _damaged_before) == 1)

# A MISSING sidecar is first run, not damage — or every fresh install accuses
# itself of losing data that never existed.
_p2 = tasks.Tasks.__new__(tasks.Tasks)
tasks.META_FILE = os.path.join(_d, "nothing-here.json")
try:
    tasks.Tasks._read_meta(_p2)
finally:
    tasks.META_FILE = _real_meta
check("a MISSING sidecar is first run, not damage",
      getattr(_p2, "_meta_damaged", False) is False)

# The flash timer is recorded, so closing inside its two seconds cannot leave
# it calling into a torn-down window.
_p3 = tasks.Tasks.__new__(tasks.Tasks)
_p3.remaining = type("L", (), {"set_text": lambda *a: None})()
tasks.Tasks._flash(_p3, "Exported")
check("a flash records its timer instead of firing and forgetting",
      getattr(_p3, "_flash_timer", None))
# getattr with a default throughout: against code that never records the
# timer these must fail BY NAME, not die on AttributeError three lines later
# and take the rest of the suite with them.
_first = getattr(_p3, "_flash_timer", None)
tasks.Tasks._flash(_p3, "Exported again")
check("...and flashing twice does not leave two timers racing",
      getattr(_p3, "_flash_timer", None) not in (None, _first))
if hasattr(tasks.Tasks, "_cancel_flash_timer"):
    tasks.Tasks._cancel_flash_timer(_p3)
    check("...and it is cancellable",
          getattr(_p3, "_flash_timer", "missing") is None)
else:
    check("...and it is cancellable  [not reached: no _cancel_flash_timer]",
          False)
shutil.rmtree(_d, ignore_errors=True)

shutil.rmtree(HOME, ignore_errors=True)
print("OK" if ok else "FAILURES")
sys.exit(0 if ok else 1)
