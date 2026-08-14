#!/usr/bin/env python3
"""Headless Save As commit-semantics checks for Illustrator."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import illustrator  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def app(old_path, picked, succeeds):
    win = illustrator.Illustrator.__new__(illustrator.Illustrator)
    win._path = old_path
    win._choose_file = lambda save: picked
    win._write_png = lambda path: (setattr(win, "attempted", path)
                                   or succeeds)
    win._mark_saved = lambda: setattr(win, "marked_saved", True)
    win._flash_save = lambda text: setattr(win, "failure", text)
    win.attempted = None
    win.marked_saved = False
    win.failure = ""
    return win


old = "/Pictures/original.png"

# Regression: a failed write must retain the previous binding. The old code
# assigned _path before calling _file_save(), leaving the nonexistent target as
# the current document even though _write_png returned False.
win = app(old, "/read-only/new-name", False)
check(win._file_save_as() is False, "failed Save As reports failure")
check(win.attempted == "/read-only/new-name.png",
      "the default PNG extension is attempted")
check(win._path == old, "failed Save As keeps the previous valid file binding")
check(not win.marked_saved, "failed Save As never marks the canvas saved")
check(bool(win.failure), "failed Save As remains visibly actionable")

# A successful write commits the new identity only after _write_png succeeds.
win = app(old, "/Pictures/new-name.png", True)
check(win._file_save_as() is True, "successful Save As reports success")
check(win._path == "/Pictures/new-name.png",
      "successful Save As adopts the new file binding")
check(win.marked_saved, "successful Save As marks the canvas saved")
check(not win.failure, "successful Save As shows no failure")

# Cancel is a no-op: no write, no binding change, no status lie.
win = app(old, None, True)
check(win._file_save_as() is False, "cancel reports no save")
check(win.attempted is None and win._path == old,
      "cancel neither writes nor changes the document binding")

# The draft name. _write_png used to render to `path + ".new"` and os.replace
# it — atomic for the destination, but the draft's name is one a person can
# already own. Saving "drawing.png" silently overwrote a real
# "drawing.png.new" beside it, and a FAILED save then deleted it: a file this
# app never opened and the user never named. The draft has to be unguessable.
import tempfile                                                # noqa: E402
import cairo                                                   # noqa: E402

tmpdir = tempfile.mkdtemp(prefix="illu-draft-")
dest = os.path.join(tmpdir, "drawing.png")
bystander = dest + ".new"
with open(bystander, "wb") as fh:
    fh.write(b"a real file that happens to be named like a draft")
keep = open(bystander, "rb").read()

real = illustrator.Illustrator.__new__(illustrator.Illustrator)
real._flatten_surface = lambda: cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
ok = illustrator.Illustrator._write_png(real, dest)
check(ok is True, "the save itself still succeeds")
check(os.path.exists(bystander) and open(bystander, "rb").read() == keep,
      "a bystander file named like the draft survives a save")
check(os.path.exists(dest) and os.path.getsize(dest) > 0,
      "...and the drawing was actually written")

# ...and it is still atomic for the destination: a render that throws leaves
# the previous drawing whole.
before = open(dest, "rb").read()


def _boom():
    raise RuntimeError("flatten failed part-way")


real._flatten_surface = _boom
check(illustrator.Illustrator._write_png(real, dest) is False,
      "a failed save reports failure")
check(open(dest, "rb").read() == before,
      "a failed save leaves the previous drawing untouched")
check(not [n for n in os.listdir(tmpdir) if n.startswith(".nbw-")],
      "a failed save leaves no draft behind")

print()
if failures:
    print("ILLUSTRATOR SAVE AS SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("ILLUSTRATOR SAVE AS SELFTEST: %d checks, all pass" % checks)
