#!/usr/bin/env python3
"""Headless test for Finder column sorting (folders-first, by name/size/date)
and the show-hidden-files toggle."""
import os
import shutil
import tempfile

# This suite CREATES AND DELETES files under finder.HOME, which is read at
# IMPORT time and defaults to the CALLER'S REAL HOME -- so it used to build and
# rmtree ~/_sorttest in the developer's own home. Pin a throwaway one first
# (the same rule finder_fileops_selftest already follows). It also keeps the
# single-instance guard off the unscoped /tmp/nb-apps, where a real app's
# marker would make claim_single_instance() os._exit(0) mid-suite: no output,
# exit 0, a silent false pass.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbfinder-sort-"))
for _d in ("Documents", "Applications", "Pictures", "Music", "Videos"):
    os.makedirs(os.path.join(os.environ["NB_HOME"], _d), exist_ok=True)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import finder  # noqa: E402

w = finder.Finder()
HOME = finder.HOME
ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


def order():
    return [w.store[i][1] for i in range(len(w.store))]


scratch = os.path.join(HOME, "_sorttest")
shutil.rmtree(scratch, ignore_errors=True)
os.makedirs(os.path.join(scratch, "zzz_folder"))
with open(os.path.join(scratch, "a_big.txt"), "w") as fh:
    fh.write("x" * 1000)
with open(os.path.join(scratch, "m_small.txt"), "w") as fh:
    fh.write("x" * 10)
with open(os.path.join(scratch, ".hidden.txt"), "w") as fh:
    fh.write("h")
os.utime(os.path.join(scratch, "a_big.txt"), (1000, 1000))            # oldest
os.utime(os.path.join(scratch, "m_small.txt"), (2000000000, 2000000000))  # new

w.load("_sorttest")

# default: name ascending, folders first
check("default folder first", order()[0] == "zzz_folder")
check("default files name-asc", order()[1:] == ["a_big.txt", "m_small.txt"])
check("dotfile hidden by default", ".hidden.txt" not in order())

# sort by size ascending (folders first, then 10 then 1000 bytes)
w.store.set_sort_column_id(6, Gtk.SortType.ASCENDING)
check("size-asc: folder, small, big",
      order() == ["zzz_folder", "m_small.txt", "a_big.txt"])

# sort by size descending
w.store.set_sort_column_id(6, Gtk.SortType.DESCENDING)
check("size-desc: big before small (folder still grouped)",
      order().index("a_big.txt") < order().index("m_small.txt"))

# sort by date ascending (folder first; a_big oldest, m_small newest)
w.store.set_sort_column_id(7, Gtk.SortType.ASCENDING)
check("date-asc: folder, old, new",
      order() == ["zzz_folder", "a_big.txt", "m_small.txt"])

# show-hidden toggle via the real handler path
tog = Gtk.ToggleButton()
tog.set_active(True)
w._on_toggle_hidden(tog)
check("toggle on -> dotfile visible", ".hidden.txt" in order())
tog.set_active(False)
w._on_toggle_hidden(tog)
check("toggle off -> dotfile hidden", ".hidden.txt" not in order())

shutil.rmtree(scratch, ignore_errors=True)
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
