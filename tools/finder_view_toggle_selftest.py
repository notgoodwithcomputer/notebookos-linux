#!/usr/bin/env python3
"""Pressing the Finder's Grid button must switch the view, not kill the window.

The list/grid pair became Gtk.RadioButtons so assistive technology can tell
which presentation is chosen. A radio group looks immune to the set_active
re-entrancy trap (the group keeps exactly one member lit) but it is NOT: when
one member activates, GTK deactivates its sibling with set_active(FALSE), and
that emits "clicked" on the sibling too. With both buttons wired to "clicked"
-> _set_view, choosing Grid deactivated List, List's handler chose "list",
_set_view relit List, which deactivated Grid, ... until the recursion limit
killed the process. Switching to Icon view closed the window.

finder_view_fade_selftest reads _set_view's SOURCE and stayed green through
this. This one presses the actual buttons and watches the model.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
home = tempfile.mkdtemp(prefix="finder-view-toggle-")
os.environ["NB_HOME"] = home
os.makedirs(os.path.join(home, "Documents"), exist_ok=True)
open(os.path.join(home, "Documents", "note.txt"), "w").write("x")

# A ping-pong blows this long before the default 1000, and it fails as a
# RecursionError instead of hanging the suite.
sys.setrecursionlimit(200)

import gi                                            # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib                  # noqa: E402
import finder                                        # noqa: E402

FAILS = []
CALLS = []


def chk(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % detail))
    if not ok:
        FAILS.append(name)


def pump(n=5):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        GLib.usleep(2000)


app = finder.Finder()
app.show_all()
pump()

# count real _set_view entries so a hidden double-fire (the shape that
# blew the stack) is visible even where it happens not to recurse forever
orig = app._set_view


def counted(mode):
    CALLS.append(mode)
    return orig(mode)


app._set_view = counted

chk("a new window opens in list view", app._view == "list", app._view)
chk("the list radio is lit", app.view_list_btn.get_active())

# --- press the button a person presses ------------------------------------
try:
    app.view_grid_btn.clicked()
    pump()
    chk("Grid press switches the view", app._view == "grid", app._view)
    chk("Grid press shows the grid scroller",
        app._grid_sw.get_visible() and not app._list_sw.get_visible())
    chk("the grid radio is lit and the list radio is not",
        app.view_grid_btn.get_active() and not app.view_list_btn.get_active())
    chk("one press is one view change (no sibling echo)",
        CALLS == ["grid"], "calls=%r" % (CALLS,))
    chk("the CSS 'active' class follows the choice",
        app.view_grid_btn.get_style_context().has_class("active")
        and not app.view_list_btn.get_style_context().has_class("active"))
except RecursionError:
    chk("Grid press does not recurse", False, "RecursionError")

# --- and back, and again ---------------------------------------------------
try:
    del CALLS[:]
    app.view_list_btn.clicked()
    pump()
    chk("List press switches back", app._view == "list"
        and app._list_sw.get_visible() and not app._grid_sw.get_visible())
    chk("one press back is one view change", CALLS == ["list"], repr(CALLS))
    del CALLS[:]
    app.view_grid_btn.clicked()
    app.view_grid_btn.clicked()          # pressing the lit one is a no-op
    pump()
    chk("pressing the already-lit segment does nothing",
        app._view == "grid" and CALLS == ["grid"], repr(CALLS))
except RecursionError:
    chk("repeated presses do not recurse", False, "RecursionError")

# --- a restored preference restates the row without a press ---------------
del CALLS[:]
app._set_view("list")
pump()
chk("restating the row programmatically fires no press handler",
    CALLS == ["list"] and app.view_list_btn.get_active()
    and not app.view_grid_btn.get_active(), repr(CALLS))

app.destroy()
pump()
print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
