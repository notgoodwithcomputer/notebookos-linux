#!/usr/bin/env python3
"""Headless test for Finder Trash 'Put Back' (restore to original location)."""
import os
import shutil
import tempfile

# finder.HOME is read at IMPORT time and defaults to the CALLER'S REAL HOME, so
# this suite used to rmtree ~/.Trash and write into ~/Documents on the
# developer's machine. Pin a throwaway one first (the rule
# finder_fileops_selftest already follows), which also keeps the
# single-instance guard off the unscoped /tmp/nb-apps -- a real app's marker
# there makes claim_single_instance() os._exit(0) mid-suite: no output, exit 0,
# a silent false pass.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbfinder-restore-"))
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


def select_in(rel, name):
    w.load(rel)
    for i in range(len(w.store)):
        if w.store[i][1] == name:
            w.tree.get_selection().select_path(
                Gtk.TreePath.new_from_string(str(i)))
            return True
    return False


docs = os.path.join(HOME, "Documents")
trash = os.path.join(HOME, ".Trash")
os.makedirs(docs, exist_ok=True)
# clean slate
if os.path.isdir(trash):
    shutil.rmtree(trash)
for n in list(os.listdir(docs)):
    if n.startswith("restore_"):
        p = os.path.join(docs, n)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

# --- trash a file from Documents, then Put Back ---
src = os.path.join(docs, "restore_a.txt")
with open(src, "w") as fh:
    fh.write("data")
check("select file", select_in("Documents", "restore_a.txt"))
w._trash_selected()
check("moved to Trash", os.path.exists(os.path.join(trash, "restore_a.txt")))
check("origin recorded",
      os.path.exists(os.path.join(trash, ".origins", "restore_a.txt")))
check("gone from Documents", not os.path.exists(src))

# in trash view, restore button shows
w.load(".Trash")
check("restore btn visible in Trash", w.restore_btn.get_visible())
check("empty btn visible in Trash", w.empty_btn.get_visible())
check("origins hidden from Trash view",
      ".origins" not in [w.store[i][1] for i in range(len(w.store))])

check("select trashed item", select_in(".Trash", "restore_a.txt"))
w._restore_selected()
check("restored to original Documents path", os.path.exists(src))
check("removed from Trash", not os.path.exists(os.path.join(trash, "restore_a.txt")))
check("origin sidecar cleared",
      not os.path.exists(os.path.join(trash, ".origins", "restore_a.txt")))

# --- restore when original dir is gone -> recreated ---
sub = os.path.join(docs, "restore_sub")
os.makedirs(sub, exist_ok=True)
s2 = os.path.join(sub, "restore_b.txt")
with open(s2, "w") as fh:
    fh.write("b")
select_in(os.path.join("Documents", "restore_sub"), "restore_b.txt") or \
    select_in("Documents/restore_sub", "restore_b.txt")
w._trash_selected()
shutil.rmtree(sub)              # original folder disappears
check("sub removed", not os.path.exists(sub))
select_in(".Trash", "restore_b.txt")
w._restore_selected()
check("restore recreated original dir + file", os.path.exists(s2))

# --- restore button not visible outside Trash ---
w.load("Documents")
check("restore btn hidden outside Trash", not w.restore_btn.get_visible())

# cleanup
if os.path.isdir(trash):
    shutil.rmtree(trash)
for n in list(os.listdir(docs)):
    if n.startswith("restore_"):
        p = os.path.join(docs, n)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
