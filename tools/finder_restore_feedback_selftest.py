#!/usr/bin/env python3
"""Headless feedback and metadata checks for Finder Put Back."""
import os
import stat
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

# finder.HOME is read at IMPORT time and falls back to the caller's REAL home,
# which is exactly where Put Back sends an item whose origin metadata is
# missing. Pin a throwaway one first so a case that loses its sidecar cannot
# write into the developer's own Home.
os.environ.setdefault("NB_HOME",
                      tempfile.mkdtemp(prefix="nbfinder-restore-feedback-"))

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
    def __init__(self, name):
        self.name = name

    def get_value(self, _it, column):
        return self.name


def window(trash, origins, name):
    win = finder.Finder.__new__(finder.Finder)
    model = Model(name)
    win._selected_iter = lambda: (model, object())
    win._trash_dir = lambda: trash
    win._origins_dir = lambda: origins
    win._inflight = set()
    win.rel = ".Trash"
    win.loads = 0
    win.status = []
    win.reloads = []

    def load(*args, **kwargs):
        win.reloads.append((args, kwargs))
        win.loads = len(win.reloads)

    win.load = load
    win._flash_status = lambda text, *args: win.status.append(text)
    return win


def refreshed_in_place(win):
    """Exactly one reload of the folder being looked at, keeping its filter.

    keep_filter matters as much as the reload itself: a Trash narrowed by the
    search box would otherwise spring back to every entry underneath a message
    about one of them, and record=False keeps the redraw out of the Back
    history — nobody navigated anywhere.
    """
    return win.reloads == [((".Trash",), {"record": False, "keep_filter": True})]


def sidecar(origins, name, destination):
    path = os.path.join(origins, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(destination)
    return path


def snapshot(root):
    """Every entry under `root` as (relative path, kind, contents).

    lstat, never stat: a dangling link is an entry in its own right here, so a
    case that claims to touch nothing cannot hide a link that was followed,
    replaced, or resolved into a real file.
    """
    seen = set()
    for base, dirs, files in os.walk(root):
        for nm in dirs + files:
            path = os.path.join(base, nm)
            rel = os.path.relpath(path, root)
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                seen.add((rel, "link", os.readlink(path)))
            elif stat.S_ISDIR(mode):
                seen.add((rel, "dir", ""))
            else:
                with open(path, "rb") as fh:
                    seen.add((rel, "file", fh.read()))
    return seen


with tempfile.TemporaryDirectory(prefix="nb-restore-feedback-") as root:
    trash = os.path.join(root, ".Trash")
    origins = os.path.join(trash, ".origins")
    os.makedirs(origins)

    # The row is drawn from a listing taken at load time. Delete the entry from
    # a second window and the row stays, the button stays enabled, and the
    # click lands on nothing — a stale row and a dead control were, until this,
    # indistinguishable, because the click said nothing either way.
    stale_meta = sidecar(origins, "gone.txt", os.path.join(root, "gone.txt"))
    with open(os.path.join(trash, "bystander.txt"), "w", encoding="utf-8") as fh:
        fh.write("still here")
    before = snapshot(root)

    win = window(trash, origins, "gone.txt")
    win._restore_selected()
    check(win.loads == 1, "a stale Put Back row refreshes exactly once")
    check(refreshed_in_place(win),
          "the stale row is redrawn away in place, filter kept")
    check(win.status == ["That item no longer exists"],
          "a stale Put Back explains that the item vanished")
    check(snapshot(root) == before,
          "the refused Put Back changes nothing on disk")
    check(os.path.exists(stale_meta),
          "the orphaned origin sidecar is not quietly reaped")
    os.remove(stale_meta)
    os.remove(os.path.join(trash, "bystander.txt"))

    name = "notes.txt"
    src = os.path.join(trash, name)
    dest = os.path.join(root, "Documents", name)
    os.makedirs(os.path.dirname(dest))
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("notes")
    meta = sidecar(origins, name, dest)
    win = window(trash, origins, name)
    win._restore_selected()
    check(os.path.exists(dest) and not os.path.lexists(src),
          "successful Put Back restores the item")
    check(not os.path.exists(meta), "successful Put Back removes its sidecar")
    check(win.loads == 1 and win.status == ["Put back “notes.txt”"],
          "successful Put Back refreshes and confirms the named item")
    check(win.status == ["Put back “%s”" % finder.display_name("notes.txt")],
          "the confirmation reads the item out the way the row does")
    check(refreshed_in_place(win),
          "the restored row leaves the Trash in place, filter kept")

    name = "shortcut"
    src = os.path.join(trash, name)
    dest = os.path.join(root, "Documents", name)
    target = os.path.join(root, "missing-target")
    os.symlink(target, src)
    meta = sidecar(origins, name, dest)
    win = window(trash, origins, name)
    win._restore_selected()
    check(os.path.islink(dest) and os.readlink(dest) == target,
          "Put Back restores a dangling link as itself")
    check(not os.path.exists(meta) and "Put back" in win.status[-1],
          "restored dangling link gets success feedback and loses its sidecar")
    check(not os.path.lexists(src),
          "the link leaves the Trash rather than being copied out of it")
    check(win.loads == 1 and refreshed_in_place(win),
          "a dangling link's Put Back refreshes the Trash in place too")

    name = "collision.txt"
    src = os.path.join(trash, name)
    dest = os.path.join(root, "Documents", name)
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("trashed")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("late arrival")
    meta = sidecar(origins, name, dest)
    win = window(trash, origins, name)
    win._taken = lambda _path: False  # it arrived after unique-name choice
    win._restore_selected()
    check(os.path.exists(src) and open(src, encoding="utf-8").read() == "trashed",
          "late Put Back collision preserves the Trash entry")
    check(open(dest, encoding="utf-8").read() == "late arrival",
          "late Put Back collision preserves the destination")
    check(os.path.exists(meta), "refused Put Back keeps its origin sidecar")
    check(win.loads == 1 and "already exists" in win.status[-1]
          and "Put back" not in win.status[-1],
          "refused Put Back refreshes and reports collision, not success")
    check(refreshed_in_place(win),
          "a refused Put Back still refreshes the Trash in place")

    # Any other refusal follows the same rule as the collision: keep the entry,
    # keep the metadata that makes it restorable, and say plainly that it did
    # not happen. Silence here would leave the item in the Trash with the user
    # believing it had gone home.
    name = "photo.png"
    src = os.path.join(trash, name)
    documents = os.path.join(root, "Documents")
    dest = os.path.join(documents, name)
    with open(src, "wb") as fh:
        fh.write(b"\x89PNG")
    meta = sidecar(origins, name, dest)
    os.chmod(documents, 0o500)          # the origin folder is no longer writable
    try:
        win = window(trash, origins, name)
        win._restore_selected()
    finally:
        os.chmod(documents, 0o700)
    check(win.status == ["Could not put that back"],
          "a failed move says so rather than saying nothing")
    check(not any("Put back" in text for text in win.status),
          "a failed Put Back never reports success")
    check(win.loads == 1 and refreshed_in_place(win),
          "a failed Put Back refreshes the Trash in place")
    check(os.path.exists(src) and not os.path.lexists(dest),
          "the item is left in the Trash where the user can still find it")
    check(os.path.exists(meta),
          "the origin sidecar survives a failed Put Back")

print("\n%d checks, %d failed" % (checks, len(failures)))
sys.exit(1 if failures else 0)
