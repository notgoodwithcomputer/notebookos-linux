#!/usr/bin/env python3
"""Headless logic test for the Finder's history / search / trash features.
Drives the real code paths and checks state directly, so it does not depend
on the window actually painting (which is flaky under TCG)."""
import os
import tempfile
# Hermetic HOME so this test is deterministic regardless of the runner's
# environment: the Finder lists $NB_HOME/Applications/*.app and uses
# $NB_HOME/.Trash, so give it a fresh home with a representative app set
# (mirrors how the OS image provisions /root/Applications). Must be set before
# `import finder`, which reads NB_HOME at import time.
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbfinder-")
_apps = os.path.join(os.environ["NB_HOME"], "Applications")
os.makedirs(_apps, exist_ok=True)
for _n in ("Calculator", "Calendar", "Contacts", "Writer", "Music",
           "Settings", "Install Notebook OS"):
    with open(os.path.join(_apps, _n + ".app"), "w") as _fh:
        _fh.write("#!/bin/sh\n# Notebook OS application package\n")
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import finder  # noqa: E402

finder.install_css()
w = finder.Finder()
HOME = finder.HOME
ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


def sb_selected(rel):
    for r, row in w._sb_rows:
        if r == rel:
            return row.get_style_context().has_class("selected")
    return None


# --- initial state ---
check("init rel=Applications", w.rel == "Applications")
check("init history", w._history == ["Applications"] and w._hpos == 0)
check("back disabled at start", not w.back_btn.get_sensitive())
check("fwd disabled at start", not w.fwd_btn.get_sensitive())

# --- navigate ---
w.load("Documents")
check("nav to Documents", w.rel == "Documents")
check("history grew", w._history == ["Applications", "Documents"] and w._hpos == 1)
check("back enabled after nav", w.back_btn.get_sensitive())
check("sidebar highlights Documents", sb_selected("Documents") is True)
check("sidebar unhighlights Music", sb_selected("Music") is False)

# --- back / forward ---
w.go_back()
check("back -> Applications", w.rel == "Applications" and w._hpos == 0)
check("fwd enabled after back", w.fwd_btn.get_sensitive())
w.go_forward()
check("fwd -> Documents", w.rel == "Documents" and w._hpos == 1)

# --- new navigation truncates forward history ---
w.go_back()            # -> Applications (hpos 0)
w.load("Music")        # new branch
check("truncate forward", w._history == ["Applications", "Music"] and w._hpos == 1)
check("fwd disabled after new branch", not w.fwd_btn.get_sensitive())

# --- search filter ---
w.load("Applications")
total = len(w.store)
w.search.set_text("calc")
w._on_search(w.search)
names = [w.store[i][1] for i in range(len(w.store))]
check("search reduces list", 1 <= len(names) < total)
check("search all match 'calc'", names and all("calc" in n.lower() for n in names))
w.load("Applications")           # real nav should reset the filter
check("nav clears filter", w._filter == "" and len(w.store) == total)
check("nav clears search box", w.search.get_text() == "")

# --- trash: move a file to .Trash, then empty it ---
docs = os.path.join(HOME, "Documents")
os.makedirs(docs, exist_ok=True)
tf = os.path.join(docs, "trashme.txt")
with open(tf, "w") as fh:
    fh.write("scratch")
w.load("Documents")
# select the row for trashme.txt
found = False
for i in range(len(w.store)):
    if w.store[i][1] == "trashme.txt":
        w.tree.get_selection().select_path(Gtk.TreePath.new_from_string(str(i)))
        found = True
        break
check("temp file listed", found)
w._trash_selected()
check("file left Documents", not os.path.exists(tf))
check("file now in .Trash", os.path.exists(os.path.join(HOME, ".Trash", "trashme.txt")))
w.load(".Trash")
check("trash view shows empty-trash btn", w.empty_btn.get_visible())
check("trash view hides move-to-trash btn", not w.trash_btn.get_visible())
w._empty_trash()
check("empty trash clears it",
      not os.path.exists(os.path.join(HOME, ".Trash", "trashme.txt")))

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
