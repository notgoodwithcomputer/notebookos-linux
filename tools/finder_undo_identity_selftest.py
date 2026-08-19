#!/usr/bin/env python3
"""Headless identity checks for Finder's one-step Undo.

Undo names one concrete item, not a pathname. Between an action and Ctrl+Z the
thing Finder made can be deleted and something else can take the name, so an
Undo that removes "whatever is called this now" destroys work nobody asked to
lose — and reports "Undone" while doing it. These fixtures create real files,
record their lstat identity, replace them, and assert what Undo touches.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import finder  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def window(root):
    """A Finder with a real folder under it and no GTK attached."""
    win = finder.Finder.__new__(finder.Finder)
    win.messages = []
    win.rel = ""
    win._undo = None
    win._clipboard = None
    win._inflight = set()
    win._flash_status = lambda text, *args: win.messages.append(text)
    win.load = lambda *args, **kwargs: None
    win._select_name = lambda name: True
    win._update_paste = lambda: None
    win.get_mapped = lambda: False
    win.abspath = lambda rel: os.path.join(root, rel) if rel else root
    return win


def last(win):
    return win.messages[-1] if win.messages else ""


def refused(win):
    return "changed" in last(win).lower() and "nothing" in last(win).lower()


def read(path):
    """Contents, or None if the check under test destroyed the file."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def entries(path):
    """Directory listing, or None if the check under test destroyed it."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return None


def ino(path):
    st = os.lstat(path)
    return st.st_dev, st.st_ino


NEW = "untitled folder"


# ---- the created thing is still the created thing: Undo works -------------
with tempfile.TemporaryDirectory(prefix="nb-undo-valid-") as root:
    win = window(root)
    win._new_folder()
    path = os.path.join(root, NEW)
    check(os.path.isdir(path), "New Folder creates the folder")
    with open(os.path.join(path, "inside.txt"), "w", encoding="utf-8") as fh:
        fh.write("made by the folder's owner")
    win._do_undo()
    check(not os.path.lexists(path), "Undo removes the folder it made")
    check("Undone" in last(win), "a real Undo says it was undone")
    check(win._undo is None, "a spent Undo is cleared")
    win._do_undo()
    check("nothing to undo" in last(win).lower(),
          "the spent Undo is offered exactly once")

with tempfile.TemporaryDirectory(prefix="nb-undo-dup-") as root:
    win = window(root)
    src = os.path.join(root, "notes.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("notes")
    win._selected_path = lambda: src
    win._duplicate_selected()
    dup = os.path.join(root, "notes copy.txt")
    check(os.path.exists(dup), "Duplicate makes the copy")
    win._do_undo()
    check(not os.path.lexists(dup), "Undo removes the duplicate it made")
    check(os.path.exists(src), "Undo leaves the original alone")

with tempfile.TemporaryDirectory(prefix="nb-undo-link-") as root:
    # A created entry that is itself a symlink is removed as itself. Following
    # it would delete the target's contents somewhere else entirely.
    win = window(root)
    target = os.path.join(root, "target")
    os.makedirs(target)
    with open(os.path.join(target, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep")
    link = os.path.join(root, "link")
    os.symlink(target, link)
    win._set_undo_remove("Paste", link)
    win._do_undo()
    check(not os.path.lexists(link), "Undo removes a created symlink")
    check(entries(target) == ["keep.txt"],
          "Undo does not follow a created symlink into its target")


# ---- a different item took the name: Undo refuses ------------------------
with tempfile.TemporaryDirectory(prefix="nb-undo-file-") as root:
    win = window(root)
    win._new_folder()
    path = os.path.join(root, NEW)
    made = ino(path)
    os.rmdir(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("A YEAR OF SOMEBODY'S NOTES")
    check(ino(path) != made, "the replacement is a different inode")
    win._do_undo()
    check(os.path.exists(path), "a replacement file is not deleted by Undo")
    check(read(path) == "A YEAR OF SOMEBODY'S NOTES",
          "the replacement's contents are untouched")
    check(refused(win), "a stale Undo says the item changed and nothing ran")
    check(win._undo is None, "a refused Undo is cleared, not left to re-fire")
    win._do_undo()
    check("nothing to undo" in last(win).lower(),
          "the refused Undo is not offered a second time")

with tempfile.TemporaryDirectory(prefix="nb-undo-dir-") as root:
    win = window(root)
    win._new_folder()
    path = os.path.join(root, NEW)
    os.rmdir(path)
    os.makedirs(path)
    with open(os.path.join(path, "thesis.txt"), "w", encoding="utf-8") as fh:
        fh.write("chapter one")
    win._do_undo()
    check(os.path.isdir(path), "a replacement folder is not removed by Undo")
    check(entries(path) == ["thesis.txt"],
          "the replacement folder's contents survive")
    check(refused(win), "a replaced folder is reported as changed")

with tempfile.TemporaryDirectory(prefix="nb-undo-symlink-") as root:
    win = window(root)
    win._new_folder()
    path = os.path.join(root, NEW)
    real = os.path.join(root, "real")
    os.makedirs(real)
    with open(os.path.join(real, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep")
    os.rmdir(path)
    os.symlink(real, path)
    win._do_undo()
    check(os.path.islink(path), "a replacement symlink is not removed by Undo")
    check(entries(real) == ["keep.txt"],
          "Undo does not walk a replacement symlink into a live folder")
    check(refused(win), "a replaced link is reported as changed")

with tempfile.TemporaryDirectory(prefix="nb-undo-gone-") as root:
    win = window(root)
    win._new_folder()
    os.rmdir(os.path.join(root, NEW))
    win._do_undo()
    check(refused(win), "an item that has gone is reported honestly")
    check(win._undo is None, "the Undo for a vanished item is cleared")


# ---- move / trash: the same replacement race on the source ---------------
with tempfile.TemporaryDirectory(prefix="nb-move-valid-") as root:
    win = window(root)
    old = os.path.join(root, "old.txt")
    new = os.path.join(root, "new.txt")
    with open(old, "w", encoding="utf-8") as fh:
        fh.write("body")
    os.rename(old, new)
    win._set_undo_move("Rename", new, old)
    win._do_undo()
    check(os.path.exists(old) and not os.path.lexists(new),
          "Undo Rename puts the name back")
    check("Undone" in last(win), "a real move Undo says it was undone")

with tempfile.TemporaryDirectory(prefix="nb-move-stale-") as root:
    win = window(root)
    origin = os.path.join(root, "doc.txt")
    trashed = os.path.join(root, ".Trash", "doc.txt")
    os.makedirs(os.path.dirname(trashed))
    with open(trashed, "w", encoding="utf-8") as fh:
        fh.write("the trashed doc")
    sidecar = os.path.join(root, ".Trash", ".origins", "doc.txt")
    os.makedirs(os.path.dirname(sidecar))
    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write(origin)
    win._set_undo_move("Move to Trash", trashed, origin, sidecar)
    os.remove(trashed)
    with open(trashed, "w", encoding="utf-8") as fh:
        fh.write("A DIFFERENT ITEM TRASHED LATER")
    win._do_undo()
    check(not os.path.lexists(origin),
          "a replaced Trash entry is not restored over the original name")
    check(read(trashed) == "A DIFFERENT ITEM TRASHED LATER",
          "the replacement stays in the Trash, untouched")
    check(os.path.exists(sidecar),
          "a refused put-back keeps the origin record it did not use")
    check(refused(win), "a stale put-back is reported as changed")

with tempfile.TemporaryDirectory(prefix="nb-move-link-") as root:
    # A symlink whose target has gone is still an item somebody put there.
    win = window(root)
    origin = os.path.join(root, "link")
    trashed = os.path.join(root, "trashed-link")
    os.symlink(os.path.join(root, "nowhere"), trashed)
    win._set_undo_move("Move to Trash", trashed, origin)
    win._do_undo()
    check(os.path.islink(origin), "Undo restores a symlink with no target")

with tempfile.TemporaryDirectory(prefix="nb-move-occupied-") as root:
    win = window(root)
    origin = os.path.join(root, "doc.txt")
    trashed = os.path.join(root, "trashed")
    with open(trashed, "w", encoding="utf-8") as fh:
        fh.write("trashed")
    os.symlink(os.path.join(root, "nowhere"), origin)
    win._set_undo_move("Move to Trash", trashed, origin)
    win._do_undo()
    check(os.path.islink(origin),
          "Undo does not move onto a dangling link at the destination")
    check("could not undo" in last(win).lower(),
          "an occupied destination is reported as a failure")


# ---- the internal cleanup callers keep their tolerant contract -----------
with tempfile.TemporaryDirectory(prefix="nb-undo-cleanup-") as root:
    win = window(root)
    stage = os.path.join(root, ".nbcopy-stage")
    with open(stage, "w", encoding="utf-8") as fh:
        fh.write("half a copy")
    win._undo_remove(stage)
    check(not os.path.lexists(stage), "cleanup removes a staged file")
    win._undo_remove(stage)
    check(True, "cleanup of an already-gone path raises nothing")
    stagedir = os.path.join(root, ".nbcopy-dir")
    os.makedirs(os.path.join(stagedir, "sub"))
    win._undo_remove(stagedir)
    check(not os.path.lexists(stagedir), "cleanup removes a staged folder")


print("\n%d checks, %d failed" % (checks, len(failures)))
for line in failures:
    print("  FAILED: " + line)
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
