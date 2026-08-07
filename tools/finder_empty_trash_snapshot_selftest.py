#!/usr/bin/env python3
"""Headless snapshot checks for Finder's Empty Trash.

Empty Trash confirms against a list of names the person can read. Everything
that arrives in the Trash after that card is on screen was never named by it,
so it must survive the acceptance. These checks drive the real code paths over
real temporary files, with no display.
"""
import os
import sys
import shutil
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-empty-home-"))

import finder  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def window(trash):
    win = finder.Finder.__new__(finder.Finder)
    win.messages = []
    win.loads = 0
    win._trash_dir = lambda: trash
    win._origins_dir = lambda: origins_dir(trash)
    win._flash_status = lambda text, *args: win.messages.append(text)
    win.load = lambda *args, **kwargs: setattr(win, "loads", win.loads + 1)
    win.rel = ".Trash"
    win._undo = None
    return win


def origins_dir(trash):
    d = os.path.join(trash, ".origins")
    os.makedirs(d, exist_ok=True)
    return d


def put(path, text="x"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def sidecar(trash, name, origin="/home/user/Documents/x"):
    return put(os.path.join(origins_dir(trash), name), origin)


def last(win):
    return win.messages[-1] if win.messages else ""


# --- 1. the race the confirmation card actually loses -----------------------
# A: listed on the card. B: arrives while the card is open. Accepting the card
# must destroy A and only A.
with tempfile.TemporaryDirectory(prefix="nb-empty-race-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins = origins_dir(trash)

    a = put(os.path.join(trash, "a-confirmed.txt"), "confirmed")
    sidecar(trash, "a-confirmed.txt")
    win = window(trash)
    captured = win._trash_snapshot()          # what the card names
    check([n for n, _i in captured] == ["a-confirmed.txt"],
          "the confirmation captures exactly the visible entries")

    b = put(os.path.join(trash, "b-later.txt"), "arrived later")
    sidecar(trash, "b-later.txt")
    win._empty_trash(captured)

    check(not os.path.lexists(a), "the confirmed item is deleted")
    check(os.path.exists(b), "an item that arrived after the card survives")
    with open(b, encoding="utf-8") as fh:
        check(fh.read() == "arrived later", "its contents are intact")
    check(not os.path.exists(os.path.join(origins, "a-confirmed.txt")),
          "the deleted item's Put Back record is removed")
    check(os.path.exists(os.path.join(origins, "b-later.txt")),
          "the survivor keeps its Put Back record")
    check(win.loads >= 1, "the view is refreshed after emptying")

# --- 2. same name, different item ------------------------------------------
with tempfile.TemporaryDirectory(prefix="nb-empty-swap-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins = origins_dir(trash)

    stale = put(os.path.join(trash, "notes.txt"), "the one on the card")
    keep = put(os.path.join(trash, "keep.txt"), "also on the card")
    sidecar(trash, "notes.txt")
    win = window(trash)
    captured = win._trash_snapshot()
    os.replace(put(os.path.join(root, "replacement"), "a different file"),
               stale)
    win._empty_trash(captured)

    check(os.path.exists(stale), "an entry replaced under the same name is kept")
    with open(stale, encoding="utf-8") as fh:
        check(fh.read() == "a different file",
              "the replacement's contents are intact")
    check(os.path.exists(os.path.join(origins, "notes.txt")),
          "a kept entry keeps its Put Back record")
    check(not os.path.lexists(keep), "the unchanged confirmed item still goes")
    check("kept" in last(win).lower() or "stayed" in last(win).lower(),
          "the skipped entry is reported rather than passed over in silence")

# --- 3. every entry kind, without following anything -----------------------
with tempfile.TemporaryDirectory(prefix="nb-empty-kinds-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins_dir(trash)

    outside = os.path.join(root, "real-folder")
    os.makedirs(outside)
    put(os.path.join(outside, "precious.txt"), "not in the Trash")
    outside_file = put(os.path.join(root, "real-file.txt"), "also not")

    folder = os.path.join(trash, "folder")
    os.makedirs(os.path.join(folder, "nested"))
    put(os.path.join(folder, "nested", "deep.txt"))
    os.symlink(outside, os.path.join(trash, "dir-link"))
    os.symlink(outside_file, os.path.join(trash, "file-link"))
    os.symlink(os.path.join(root, "nothing-here"),
               os.path.join(trash, "broken-link"))
    put(os.path.join(trash, "plain.txt"))

    win = window(trash)
    win._empty_trash(win._trash_snapshot())

    for name in ("folder", "dir-link", "file-link", "broken-link", "plain.txt"):
        check(not os.path.lexists(os.path.join(trash, name)),
              "%s is removed from the Trash" % name)
    check(os.path.isdir(outside) and
          os.path.exists(os.path.join(outside, "precious.txt")),
          "a symlinked directory outside the Trash is never followed")
    check(os.path.exists(outside_file),
          "a symlinked file outside the Trash is never followed")
    check(os.path.isdir(os.path.join(trash, ".origins")),
          "the Put Back store itself is not treated as a trashed item")

# --- 4. partial failure is reported, and keeps its own metadata ------------
with tempfile.TemporaryDirectory(prefix="nb-empty-partial-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins = origins_dir(trash)

    stuck = os.path.join(trash, "stuck")
    os.makedirs(stuck)
    put(os.path.join(stuck, "inside.txt"))
    sidecar(trash, "stuck")
    gone = put(os.path.join(trash, "gone.txt"))
    sidecar(trash, "gone.txt")

    real_rmtree = finder.shutil.rmtree

    def refuse(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(stuck):
            raise OSError("read-only file system")
        return real_rmtree(path, *a, **k)

    win = window(trash)
    finder.shutil.rmtree = refuse
    try:
        win._empty_trash(win._trash_snapshot())
    finally:
        finder.shutil.rmtree = real_rmtree

    check(os.path.isdir(stuck), "an entry that cannot be deleted stays")
    check(not os.path.lexists(gone), "the entries that can be deleted still go")
    check(os.path.exists(os.path.join(origins, "stuck")),
          "a failed entry keeps its Put Back record")
    check(not os.path.exists(os.path.join(origins, "gone.txt")),
          "a deleted entry loses its Put Back record")
    check("could not" in last(win).lower(),
          "the failure is stated, not swallowed")

# --- 5. one acceptance, one purge ------------------------------------------
with tempfile.TemporaryDirectory(prefix="nb-empty-once-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins_dir(trash)
    put(os.path.join(trash, "one.txt"))

    win = window(trash)
    captured = win._trash_snapshot()
    runs = []

    def purge():
        runs.append(len(captured))
        win._empty_trash(captured)

    accept = finder.Finder._once(purge)
    accept()
    accept()                                   # queued double-activation
    check(runs == [1], "acceptance runs the purge at most once")

    late = put(os.path.join(trash, "two.txt"), "after")
    accept()
    check(os.path.exists(late), "a spent acceptance cannot purge again")

# --- 6. an empty Trash and an unreadable Trash both no-op safely -----------
with tempfile.TemporaryDirectory(prefix="nb-empty-edge-") as root:
    trash = os.path.join(root, ".Trash")
    os.makedirs(trash)
    origins_dir(trash)
    win = window(trash)
    check(win._trash_snapshot() == [],
          "a Trash holding only the Put Back store reads as empty")
    win._empty_trash([])
    check(os.path.isdir(trash), "emptying nothing leaves the Trash in place")

    missing = window(os.path.join(root, "no-such-trash"))
    missing._origins_dir = lambda: os.path.join(root, "no-such-trash", ".origins")
    check(missing._trash_snapshot() == [],
          "a missing Trash directory reads as empty rather than raising")

print()
if failures:
    print("FINDER EMPTY TRASH SNAPSHOT SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("FINDER EMPTY TRASH SNAPSHOT SELFTEST: %d checks, all pass" % checks)
