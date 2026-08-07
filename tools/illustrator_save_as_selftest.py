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

print()
if failures:
    print("ILLUSTRATOR SAVE AS SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("ILLUSTRATOR SAVE AS SELFTEST: %d checks, all pass" % checks)
