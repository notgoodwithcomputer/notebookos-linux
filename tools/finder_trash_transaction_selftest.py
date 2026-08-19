#!/usr/bin/env python3
"""Headless transaction checks for Finder's Move to Trash operation."""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-trash-txn-home-"))

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
    def __init__(self, path):
        self.path = path

    def get_value(self, _it, column):
        return self.path if column == 4 else os.path.basename(self.path)


def window(src, trash):
    win = finder.Finder.__new__(finder.Finder)
    model = Model(src)
    win._selected_iter = lambda: (model, object())
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    win._selected_paths = lambda: [model.path]
    win.abspath = lambda path: path
    win._trash_dir = lambda: trash
    win._flash_status = lambda message, *args: setattr(win, "status", message)
    win._set_undo = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._flash_undoable = lambda message: None
    win.status = ""
    win.undo = None
    win.rel = "Documents"
    return win


with tempfile.TemporaryDirectory(prefix="nb-trash-txn-") as root:
    source_dir = os.path.join(root, "Documents")
    trash = os.path.join(root, ".Trash")
    os.makedirs(source_dir)
    os.makedirs(trash)

    # The regression: a sidecar write failure must not move the item and lose
    # its original location.  The old implementation moved first and swallowed
    # this exact exception.
    src = os.path.join(source_dir, "work.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("work")
    win = window(src, trash)
    real_write = finder.nbapp.atomic_write_text
    finder.nbapp.atomic_write_text = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError("disk full"))
    try:
        win._trash_selected()
    finally:
        finder.nbapp.atomic_write_text = real_write
    check(os.path.exists(src), "metadata failure leaves the source in place")
    check(not os.path.exists(os.path.join(trash, "work.txt")),
          "metadata failure never creates a destination without an origin")
    check(bool(win.status), "metadata failure is reported")

    # A successful operation commits both halves and hands Undo the exact
    # sidecar it must remove.
    win = window(src, trash)
    win._trash_selected()
    dst = os.path.join(trash, "work.txt")
    origin = os.path.join(trash, ".origins", "work.txt")
    check(os.path.exists(dst) and not os.path.exists(src),
          "successful transaction moves the item")
    check(os.path.exists(origin), "successful transaction records its origin")
    with open(origin, encoding="utf-8") as fh:
        check(fh.read() == src, "origin record contains the exact source path")
    check(win.undo is not None and win.undo[-2] == origin,
          "Undo receives the committed origin record")
    check(win.undo is not None and win.undo[-1] == finder.Finder._path_identity(dst),
          "Undo is bound to the committed Trash entry")

    # If the second half fails, roll the first half back rather than leaving
    # stale metadata that could later be paired with another same-named item.
    src2 = os.path.join(source_dir, "blocked.txt")
    with open(src2, "w", encoding="utf-8") as fh:
        fh.write("blocked")
    win = window(src2, trash)
    real_rename = win._rename_noreplace
    win._rename_noreplace = lambda *_a, **_k: (_ for _ in ()).throw(
        OSError("rename refused"))
    try:
        win._trash_selected()
    finally:
        win._rename_noreplace = real_rename
    origin2 = os.path.join(trash, ".origins", "blocked.txt")
    check(os.path.exists(src2), "move failure leaves the source in place")
    check(not os.path.exists(origin2), "move failure removes the staged origin")

    # Unix filenames may end in whitespace. The origin sidecar has no newline
    # delimiter, so Put Back must read it byte-for-byte rather than strip it.
    spaced = os.path.join(source_dir, "report \t")
    exact_origin = os.path.join(trash, ".origins", "spaced")
    with open(exact_origin, "w", encoding="utf-8") as fh:
        fh.write(spaced)
    check(finder._read_origin_record(exact_origin) == spaced,
          "Put Back preserves trailing whitespace in the original path")

print()
if failures:
    print("FINDER TRASH TRANSACTION SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("FINDER TRASH TRANSACTION SELFTEST: %d checks, all pass" % checks)
