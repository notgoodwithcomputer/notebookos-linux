#!/usr/bin/env python3
"""Headless checks for the Trash lifecycle of a dangling symlink.

A symlink whose target has gone is still a directory entry: it is listed, it
owns its name, and the user can select it. Finder tested os.path.exists on the
entry, which answers about the link's TARGET, so a dangling link could not be
moved to the Trash, could be silently overwritten inside the Trash, and could
not be put back once there.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-dangling-home-"))

import finder  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Model:
    """Column 4 is the row's path, column 1 its name (as Finder's store is)."""

    def __init__(self, path):
        self.path = path

    def get_value(self, _it, column):
        return self.path if column == 4 else os.path.basename(self.path)


def window(path, trash):
    win = finder.Finder.__new__(finder.Finder)
    model = Model(path)
    win._selected_iter = lambda: (model, object())
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    win._selected_paths = lambda: [model.path]
    win.abspath = lambda p: p
    win._trash_dir = lambda: trash
    win._inflight = set()
    win._flash_status = lambda message, *args: setattr(win, "status", message)
    win._set_undo = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._flash_undoable = lambda message: None
    win.status = ""
    win.undo = None
    win.rel = "Documents"
    return win


with tempfile.TemporaryDirectory(prefix="nb-dangling-trash-") as root:
    source_dir = os.path.join(root, "Documents")
    trash = os.path.join(root, ".Trash")
    os.makedirs(source_dir)
    os.makedirs(trash)
    missing = os.path.join(root, "no-such-target")

    # 1. A dangling link can be thrown away, and its origin is recorded so it
    #    can come back.
    src = os.path.join(source_dir, "link.txt")
    os.symlink(missing, src)
    win = window(src, trash)
    win._trash_selected()
    dst = os.path.join(trash, "link.txt")
    origin = os.path.join(trash, ".origins", "link.txt")
    check(not os.path.lexists(src), "a dangling link leaves its folder")
    check(os.path.islink(dst), "a dangling link arrives in the Trash as a link")
    check(os.readlink(dst) == missing, "the link still points where it did")
    check(os.path.lexists(origin), "the dangling link's origin is recorded")
    with open(origin, encoding="utf-8") as fh:
        check(fh.read() == src, "the origin names the folder it came from")

    # 2. A dangling link ALREADY in the Trash owns its name: a second item of
    #    the same name must be given a numbered destination, not written over
    #    the link standing there.
    occupant_identity = finder.Finder._path_identity(dst)
    second = os.path.join(source_dir, "link.txt")
    with open(second, "w", encoding="utf-8") as fh:
        fh.write("real file")
    win = window(second, trash)
    win._trash_selected()
    numbered = os.path.join(trash, "link.txt (1)")
    check(finder.Finder._path_identity(dst) == occupant_identity,
          "the dangling link in the Trash is left intact")
    check(os.path.islink(dst), "the dangling link is still a link, not the file")
    check(os.path.lexists(numbered), "the colliding item takes a numbered name")
    check(not os.path.islink(numbered) and open(numbered).read() == "real file",
          "the numbered entry holds the item that was just trashed")
    check(os.path.lexists(os.path.join(trash, ".origins", "link.txt (1)")),
          "the numbered entry gets its own origin record")

    # 3. Put Back returns the dangling link to where it came from and clears
    #    the committed sidecar with it.
    win = window(dst, trash)
    win._restore_selected()
    check(os.path.islink(src), "Put Back restores the dangling link")
    check(os.readlink(src) == missing,
          "the restored link is unchanged, target still missing")
    check(not os.path.lexists(dst), "the Trash no longer holds the link")
    check(not os.path.lexists(origin),
          "Put Back removes the origin sidecar it consumed")

print()
if failures:
    print("FINDER DANGLING TRASH SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    print("RESULT: FAIL")
    raise SystemExit(1)
print("FINDER DANGLING TRASH SELFTEST: %d checks, all pass" % checks)
print("RESULT: PASS")
