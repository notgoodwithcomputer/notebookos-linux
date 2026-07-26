#!/usr/bin/env python3
"""Headless test for the Finder's file operations (new folder / copy / cut /
paste, incl. directory recursion and collision handling). Filesystem-level
checks, independent of painting.

This test CREATES AND DELETES files, so it runs against a throwaway NB_HOME —
never the caller's real Documents/Music/Videos. NB_HOME is pinned before finder
is imported, because finder binds HOME at module scope."""
import atexit
import os
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="finder_fileops_")
os.environ["NB_HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, True)

import gi  # noqa: E402
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


def clean(d):
    os.makedirs(d, exist_ok=True)
    for n in os.listdir(d):
        if n.startswith("fileop_") or n == "untitled folder":
            p = os.path.join(d, n)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def select(rel, name):
    w.load(rel)
    for i in range(len(w.store)):
        if w.store[i][1] == name:
            w.tree.get_selection().select_path(
                Gtk.TreePath.new_from_string(str(i)))
            return True
    return False


docs = os.path.join(HOME, "Documents")
music = os.path.join(HOME, "Music")
videos = os.path.join(HOME, "Videos")
for d in (docs, music, videos):
    clean(d)

# a test file and a test directory-with-content in Documents
with open(os.path.join(docs, "fileop_a.txt"), "w") as fh:
    fh.write("hello")
os.makedirs(os.path.join(docs, "fileop_dir"))
with open(os.path.join(docs, "fileop_dir", "inner.txt"), "w") as fh:
    fh.write("inner")

# --- copy a file: Documents -> Music ---
# The toolbar Paste button was removed; the context/Edit menus now derive Paste
# sensitivity from self._clipboard each time they open, so that is what these
# checks assert. (finder.paste_btn survives as a deliberate None.)
def paste_offered():
    w._update_paste()          # still called on every clipboard change
    return w._clipboard is not None


check("paste not offered initially", not paste_offered())
check("select file to copy", select("Documents", "fileop_a.txt"))
w._copy_selected()
check("clipboard = copy", w._clipboard is not None and w._clipboard[1] is False)
check("paste offered after copy", paste_offered())
w.load("Music")
w._paste()
check("file copied into Music", os.path.exists(os.path.join(music, "fileop_a.txt")))
check("original remains in Documents",
      os.path.exists(os.path.join(docs, "fileop_a.txt")))

# --- paste again into Music -> collision becomes ' copy' ---
w._paste()
check("second paste -> ' copy'",
      os.path.exists(os.path.join(music, "fileop_a copy.txt")))

# --- copy a directory (recursive): Documents -> Videos ---
check("select dir to copy", select("Documents", "fileop_dir"))
w._copy_selected()
w.load("Videos")
w._paste()
check("dir copied recursively",
      os.path.isdir(os.path.join(videos, "fileop_dir")) and
      os.path.exists(os.path.join(videos, "fileop_dir", "inner.txt")))

# --- new folder in Videos ---
before = set(os.listdir(videos))
w._new_folder()
after = set(os.listdir(videos))
check("new folder created",
      "untitled folder" in (after - before) and
      os.path.isdir(os.path.join(videos, "untitled folder")))
# second new folder -> numbered
w._new_folder()
check("second new folder numbered",
      os.path.isdir(os.path.join(videos, "untitled folder 2")))

# --- cut (move): Music copy -> Documents ---
check("select for cut", select("Music", "fileop_a.txt"))
w._cut_selected()
check("clipboard = cut", w._clipboard is not None and w._clipboard[1] is True)
w.load("Documents")
# Documents already has fileop_a.txt -> collision -> ' copy'
w._paste()
check("cut moved out of Music",
      not os.path.exists(os.path.join(music, "fileop_a.txt")))
check("cut landed in Documents (as copy)",
      os.path.exists(os.path.join(docs, "fileop_a copy.txt")))
check("clipboard cleared after cut", w._clipboard is None)
check("paste not offered after cut", not paste_offered())

for d in (docs, music, videos):
    clean(d)
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
