#!/usr/bin/env python3
"""Headless identity and at-most-once checks for Finder permanent deletion."""
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


def window(origins):
    win = finder.Finder.__new__(finder.Finder)
    win.messages = []
    win.loads = 0
    win._flash_status = lambda text, *args: win.messages.append(text)
    win.load = lambda *args, **kwargs: setattr(win, "loads", win.loads + 1)
    win._origins_dir = lambda: origins
    win.rel = ".Trash"
    win._undo = {"stale": True}
    return win


# A queued double-click/Enter pair can invoke a GTK clicked handler twice before
# destruction is processed. The commit gate must make that one mutation.
calls = []
accept = finder.Finder._once(lambda: calls.append("deleted"))
accept()
accept()
check(calls == ["deleted"], "destructive acceptance executes at most once")

with tempfile.TemporaryDirectory(prefix="nb-delete-confirm-") as root:
    trash = os.path.join(root, ".Trash")
    origins = os.path.join(trash, ".origins")
    os.makedirs(origins)

    # Regression: confirm old.txt, replace that directory entry with a new
    # inode, then accept. The replacement was never named by the confirmation.
    path = os.path.join(trash, "old.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("old")
    identity = finder.Finder._path_identity(path)
    replacement = os.path.join(root, "replacement")
    with open(replacement, "w", encoding="utf-8") as fh:
        fh.write("new and important")
    os.replace(replacement, path)
    win = window(origins)
    win._delete_forever(path, "old.txt", identity)
    check(os.path.exists(path), "a replacement item is not deleted")
    with open(path, encoding="utf-8") as fh:
        check(fh.read() == "new and important",
              "the replacement contents remain intact")
    check(win.messages and "changed" in win.messages[-1].lower(),
          "stale confirmation explains that nothing was deleted")
    check(win._undo == {"stale": True},
          "refused deletion does not alter Undo state")

    # The unchanged item the person actually confirmed is deleted, its origin
    # metadata is cleared, and no Undo is promised for an irreversible action.
    confirmed = os.path.join(trash, "confirmed.txt")
    with open(confirmed, "w", encoding="utf-8") as fh:
        fh.write("confirmed")
    with open(os.path.join(origins, "confirmed.txt"), "w") as fh:
        fh.write("/Documents/confirmed.txt")
    identity = finder.Finder._path_identity(confirmed)
    win = window(origins)
    win._delete_forever(confirmed, "confirmed.txt", identity)
    check(not os.path.exists(confirmed), "the unchanged confirmed item is deleted")
    check(not os.path.exists(os.path.join(origins, "confirmed.txt")),
          "its Put Back metadata is removed")
    check(win._undo is None, "permanent deletion clears any stale Undo promise")

    # lstat identity plus explicit symlink handling deletes the Trash entry,
    # never the directory it points at (and also handles broken symlinks).
    target = os.path.join(root, "target")
    os.makedirs(target)
    link = os.path.join(trash, "folder-link")
    os.symlink(target, link)
    identity = finder.Finder._path_identity(link)
    win = window(origins)
    win._delete_forever(link, "folder-link", identity)
    check(not os.path.lexists(link), "confirmed symlink entry is removed")
    check(os.path.isdir(target), "symlink target directory is untouched")

print()
if failures:
    print("FINDER DESTRUCTIVE SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("FINDER DESTRUCTIVE SELFTEST: %d checks, all pass" % checks)
