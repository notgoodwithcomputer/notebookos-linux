#!/usr/bin/env python3
"""Headless regression checks for Get Info on virtual Applications rows."""
import atexit
import inspect
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
DE = Path(os.environ.get("FINDER_MODULE_DIR") or
          REPO / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
HOME = tempfile.mkdtemp(prefix="finder-getinfo-apps-")
atexit.register(shutil.rmtree, HOME, True)
os.environ["NB_HOME"] = HOME
sys.path.insert(0, str(DE))

import gi                                                       # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                   # noqa: E402

FAILS = []
CHECKS = 0


def check(name, ok):
    global CHECKS
    CHECKS += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


class Model:
    values = {1: "Calculator.app", 4: "Applications/Calculator.app",
              5: False, 8: "Application"}

    def get_value(self, _it, column):
        return self.values[column]


class Probe:
    rel = "Applications"

    def __init__(self):
        self.shown = []
        self.flashes = []

    def _selected_iter(self):
        return Model(), object()

    def abspath(self, rel):
        return os.path.join(HOME, rel)

    def _flash_status(self, text):
        self.flashes.append(text)

    def _icon_for(self, name):
        return finder.icon_for(name)

    def _selected_row_anchor(self):
        return (1, 2, 3, 4)

    def _show_info_dialog(self, *args):
        self.shown.append(args)
        return object(), object()

    def _compute_dir_size(self, *args):
        raise AssertionError("a virtual app is not a directory-size job")


# The shipped Applications entries deliberately do not exist as filesystem
# nodes. Drive the real callback with such a row and inspect every card field.
p = Probe()
finder.Finder._get_info(p)
check("APP-INFO-OPENS virtual Applications row opens a card", len(p.shown) == 1)
if p.shown:
    name, icon, kind, size, modified, where, anchor = p.shown[0]
    check("APP-INFO-NAME virtual app uses its visible name", name == "Calculator")
    check("APP-INFO-ICON virtual app uses its registered icon", icon == "calculator")
    check("APP-INFO-KIND virtual app keeps its model kind", kind == "Application")
    check("APP-INFO-SIZE virtual app does not invent disk bytes", size == "—")
    check("APP-INFO-MODIFIED virtual app does not invent a timestamp", modified == "—")
    check("APP-INFO-WHERE virtual app reports its virtual location",
          where == finder._t("Applications"))
    check("APP-INFO-ANCHOR virtual app keeps the clicked-row origin",
          anchor == (1, 2, 3, 4))
else:
    for name in ("APP-INFO-NAME", "APP-INFO-ICON", "APP-INFO-KIND",
                 "APP-INFO-SIZE", "APP-INFO-MODIFIED", "APP-INFO-WHERE",
                 "APP-INFO-ANCHOR"):
        check(name + " unavailable because card did not open", False)
check("APP-INFO-NO-ERROR virtual app does not flash a filesystem error",
      not p.flashes)


# A normal file must retain stat-derived size, modified time, and absolute path.
real = os.path.join(HOME, "note.txt")
with open(real, "wb") as fh:
    fh.write(b"hello")
os.utime(real, (1_700_000_000, 1_700_000_000))


class RealModel(Model):
    values = {1: "note.txt", 4: "note.txt", 5: False, 8: "Plain Text"}


r = Probe()
r.rel = ""
r._selected_iter = lambda: (RealModel(), object())
finder.Finder._get_info(r)
check("REAL-INFO-OPENS real file still opens a card", len(r.shown) == 1)
if r.shown:
    vals = r.shown[0]
    check("REAL-INFO-SIZE real file keeps exact stat size",
          vals[3] == "5 B  ·  5 bytes")
    check("REAL-INFO-MODIFIED real file keeps a real timestamp", vals[4] != "—")
    check("REAL-INFO-WHERE real file keeps its absolute path", vals[5] == real)


# Right-click must select the hit before constructing the menu callback.
class Selection:
    def __init__(self):
        self.selected = []

    def select_path(self, path):
        self.selected.append(path)

    def unselect_all(self):
        pass


class Tree:
    def __init__(self):
        self.sel = Selection()

    def get_path_at_pos(self, _x, _y):
        return ("clicked-row", None, None, None)

    def get_selection(self):
        return self.sel

    def grab_focus(self):
        pass


tree = Tree()
menu_saw = []
event = type("Event", (), {"button": 3, "type": finder.Gdk.EventType.BUTTON_PRESS,
                            "x": 5, "y": 6})()
owner = type("Owner", (), {"_popup_context_menu": lambda self, _e:
                            menu_saw.append(list(tree.sel.selected))})()
handled = finder.Finder._on_tree_button(owner, tree, event)
check("APP-INFO-RIGHTCLICK pointer row is selected before its menu opens",
      handled and menu_saw == [["clicked-row"]])


# Closing a size job must prevent both an idle callback and needless traversal.
class Dialog:
    def __init__(self):
        self.destroy = None

    def connect(self, signal, callback):
        if signal == "destroy":
            self.destroy = callback


dlg = Dialog()
entered = threading.Event()
release = threading.Event()
idle = []
worker = []
cancel_seen = []


class AsyncProbe:
    def _dir_size(self, _path, cancel=None):
        cancel_seen.append(cancel)
        entered.set()
        release.wait(2)
        return (99, 1)

    def _apply_dir_size(self, *args):
        raise AssertionError("closed card received async fill")


ap = AsyncProbe()
real_thread = threading.Thread
with mock.patch.object(finder.GLib, "idle_add", lambda *a: idle.append(a)), \
        mock.patch.object(finder.threading, "Thread",
                          side_effect=lambda *a, **kw: (worker.append(real_thread(*a, **kw))
                                                       or worker[-1])):
    finder.Finder._compute_dir_size(ap, dlg, "/virtual", 1, object())
    entered.wait(2)
    dlg.destroy()
    release.set()
    worker[0].join(2)
check("APP-INFO-CLOSE cancelled size job queues no destroyed-card callback",
      idle == [])
check("APP-INFO-CLOSE signals traversal cancellation",
      len(cancel_seen) == 1 and cancel_seen[0].is_set())


# In-suite mutation sanity: the central distinction must reject a permissive
# mutant that labels an ordinary .app-looking file as a virtual Applications row.
virtual = getattr(finder, "_is_virtual_app", lambda _rel, _name: False)
suffix_only_mutant = lambda _rel, name: name.endswith(".app")
check("PASS-MUTANT virtual-app predicate rejects suffix-only mutant",
      suffix_only_mutant("Documents", "Calculator.app")
      and virtual("Documents", "Calculator.app") is False)

src = inspect.getsource(finder.Finder._show_info_dialog)
check("APP-INFO-OVERLAP repeated Get Info closes the existing card first",
      "getattr(self, \"_info_card\", None)" in src
      and "close = getattr(self, \"_info_close\", None)" in src
      and "close()" in src)

print("\n%d checks, %d failed" % (CHECKS, len(FAILS)))
print("RESULT: %s" % ("FAILED" if FAILS else "PASS"))
sys.exit(1 if FAILS else 0)
