#!/usr/bin/env python3
"""Headless checks that a Finder move is atomic and never replaces a name.

_rename_noreplace is the one place every same-disk move goes through: Paste of
a cut, Rename, Move to Trash, Put Back. It must move the entry or refuse it in
a single step, so that a destination created between the look and the move is
refused rather than consumed. These checks drive the real function against a
real filesystem, and separately prove that the racy calls it used to fall back
to (os.path.lexists / os.link / os.rename) are not on the path at all.
"""
import ctypes
import errno
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


def refuses(src, dst):
    """Run the move expecting FileExistsError; return (raised, exception)."""
    try:
        finder.Finder._rename_noreplace(src, dst)
    except FileExistsError as exc:
        return True, exc
    except OSError as exc:
        return False, exc
    return False, None


# ---- the move succeeds, atomically, for every kind of entry ----
with tempfile.TemporaryDirectory(prefix="nb-noreplace-move-") as root:
    src = os.path.join(root, "notes.txt")
    dst = os.path.join(root, "moved notes.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("the only copy")

    finder.Finder._rename_noreplace(src, dst)
    check(not os.path.lexists(src), "a moved file leaves its old name")
    with open(dst, encoding="utf-8") as fh:
        check(fh.read() == "the only copy", "the moved file keeps its contents")

    folder = os.path.join(root, "Project")
    os.makedirs(folder)
    with open(os.path.join(folder, "inside.txt"), "w", encoding="utf-8") as fh:
        fh.write("still here")
    moved_folder = os.path.join(root, "Project archived")
    finder.Finder._rename_noreplace(folder, moved_folder)
    check(not os.path.lexists(folder) and os.path.isdir(moved_folder),
          "a directory moves whole (no hard link needed)")
    check(os.listdir(moved_folder) == ["inside.txt"],
          "the moved directory keeps its contents")

    missing = os.path.join(root, "missing-target")
    link = os.path.join(root, "shortcut")
    os.symlink(missing, link)
    moved_link = os.path.join(root, "shortcut moved")
    finder.Finder._rename_noreplace(link, moved_link)
    check(os.path.islink(moved_link) and not os.path.lexists(link),
          "a dangling link moves as a link")
    check(os.readlink(moved_link) == missing,
          "the moved link keeps its identical link text")
    check(not os.path.lexists(missing),
          "moving the link never materialised its missing target")


# ---- an occupied name is refused, with BOTH sides left as they were ----
with tempfile.TemporaryDirectory(prefix="nb-noreplace-refuse-") as root:
    src = os.path.join(root, "source.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("source contents")

    occupied = os.path.join(root, "occupied.txt")
    with open(occupied, "w", encoding="utf-8") as fh:
        fh.write("do not lose me")
    raised, exc = refuses(src, occupied)
    check(raised, "moving onto an existing file raises FileExistsError")
    check(getattr(exc, "errno", None) == errno.EEXIST,
          "the refusal carries EEXIST")
    check(os.path.exists(src), "a refused move keeps the source")
    with open(occupied, encoding="utf-8") as fh:
        check(fh.read() == "do not lose me",
              "a refused move keeps the destination file untouched")

    empty_dir = os.path.join(root, "Empty Folder")
    os.makedirs(empty_dir)
    raised, _ = refuses(src, empty_dir)
    check(raised, "moving onto an EMPTY directory is refused, not moved into")
    check(os.path.exists(src) and os.listdir(empty_dir) == [],
          "the empty directory did not swallow the source")

    dangling = os.path.join(root, "shortcut")
    target = os.path.join(root, "missing-target")
    os.symlink(target, dangling)
    raised, _ = refuses(src, dangling)
    check(raised, "moving onto a DANGLING link is refused")
    check(os.path.islink(dangling) and os.readlink(dangling) == target,
          "the dangling link is left exactly as it was")
    check(os.path.exists(src) and not os.path.lexists(target),
          "the refused move wrote nothing through the link")


# ---- the racy calls are not on this path at all ----
# The old implementation looked at the name (os.path.lexists), hard-linked
# (os.link), then renamed (os.rename). If any of those were still reachable,
# there would still be a window in which a destination can appear. Booby-trap
# all three — plus shutil.move, the other name-eating call — and the move must
# still behave exactly as above.
class Exploded(AssertionError):
    pass


def explode(*_args, **_kwargs):
    raise Exploded("a racy call was made on the move path")


with tempfile.TemporaryDirectory(prefix="nb-noreplace-trap-") as root:
    src = os.path.join(root, "source.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("source contents")
    occupied = os.path.join(root, "occupied.txt")
    with open(occupied, "w", encoding="utf-8") as fh:
        fh.write("do not lose me")
    free = os.path.join(root, "free.txt")

    saved = (os.path.lexists, os.link, os.rename, finder.shutil.move)
    os.path.lexists = explode
    os.link = explode
    os.rename = explode
    finder.shutil.move = explode
    try:
        trapped_refusal = refuses(src, occupied)
        try:
            finder.Finder._rename_noreplace(src, free)
            trapped_move = None
        except Exploded as exc:
            trapped_move = exc
    finally:
        os.path.lexists, os.link, os.rename, finder.shutil.move = saved

    check(trapped_refusal[0],
          "the refusal still raises FileExistsError with lexists/link/rename"
          " booby-trapped")
    check(not isinstance(trapped_refusal[1], Exploded),
          "refusing an occupied name calls none of lexists/link/rename")
    check(trapped_move is None,
          "a successful move calls none of lexists/link/rename/shutil.move")
    check(os.path.exists(free) and not os.path.lexists(src),
          "the booby-trapped move still moved the file")


# ---- the errno the kernel reports is the errno the caller sees ----
# Injected at the libc boundary: the syscall itself cannot be made to fail with
# EXDEV inside one temporary directory, and EXDEV is the one Paste acts on (it
# is the difference between "refuse" and "copy across disks").
real_resolver = finder._libc_renameat2


def failing_libc(err):
    def fake_renameat2(*_args):
        ctypes.set_errno(err)
        return -1
    return lambda: fake_renameat2


for injected, expect_subclass in ((errno.EXDEV, None),
                                  (errno.EEXIST, FileExistsError),
                                  (errno.EACCES, PermissionError)):
    finder._libc_renameat2 = failing_libc(injected)
    try:
        try:
            finder.Finder._rename_noreplace("/tmp/nb-src", "/tmp/nb-dst")
            seen = None
        except OSError as exc:
            seen = exc
    finally:
        finder._libc_renameat2 = real_resolver
    check(getattr(seen, "errno", None) == injected,
          "errno %d is passed through untranslated" % injected)
    if expect_subclass is not None:
        check(isinstance(seen, expect_subclass),
              "errno %d arrives as %s" % (injected, expect_subclass.__name__))
    else:
        check(seen is not None and not isinstance(seen, FileExistsError),
              "EXDEV is NOT mistaken for a name collision")

# A libc without the symbol refuses the move rather than degrading to a racy
# fallback — the whole point of having no fallback.
saved_libc = finder._libc
finder._libc = object()          # no renameat2 attribute
try:
    try:
        finder.Finder._rename_noreplace("/tmp/nb-src", "/tmp/nb-dst")
        seen = None
    except OSError as exc:
        seen = exc
finally:
    finder._libc = saved_libc
check(seen is not None and seen.errno == errno.ENOTSUP,
      "a libc with no renameat2 fails with ENOTSUP instead of falling back")


# ---- end to end: a same-disk cut/paste that loses the race ----
class SelectedModel:
    def __init__(self, name, path):
        self.name = name
        self.path = path

    def get_value(self, _it, column):
        return self.path if column == 4 else self.name


def paste_window(dest_dir, clipboard):
    win = finder.Finder.__new__(finder.Finder)
    win._inflight = set()
    win._clipboard = clipboard
    win.abspath = lambda path: dest_dir
    win.rel = os.path.basename(dest_dir)
    win.status = ""
    win.undo = None
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._flash_undoable = lambda text: setattr(win, "status", text)
    win._set_undo_move = lambda *args: setattr(win, "undo", args)
    win._set_undo_remove = lambda *args: setattr(win, "undo", args)
    win._update_paste = lambda: None
    win.load = lambda *args, **kwargs: None
    return win


with tempfile.TemporaryDirectory(prefix="nb-noreplace-paste-") as root:
    source_dir = os.path.join(root, "Documents")
    dest_dir = os.path.join(root, "Archive")
    os.makedirs(source_dir)
    os.makedirs(dest_dir)
    src = os.path.join(source_dir, "report.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("the only copy")
    collision = os.path.join(dest_dir, "report.txt")
    with open(collision, "w", encoding="utf-8") as fh:
        fh.write("someone else's file")

    win = paste_window(dest_dir, (src, True))
    # The race, reproduced: the name reads as free when Paste picks it (the
    # download/second-window landed a moment later), so nothing but the move
    # itself stands between the user and a destroyed file.
    win._taken = lambda path: False
    win._paste()

    check(win._clipboard == (src, True),
          "a refused cut/paste keeps the cut on the clipboard")
    check(win.undo is None, "a refused cut/paste installs no Undo")
    with open(src, encoding="utf-8") as fh:
        check(fh.read() == "the only copy",
              "the cut item is still where it was, intact")
    with open(collision, encoding="utf-8") as fh:
        check(fh.read() == "someone else's file",
              "the destination file was not consumed by the paste")
    check("already exists" in win.status,
          "the status line reports the collision, not 'Moved here'")

    # ...and the same Paste onto a free name still moves, instantly.
    win = paste_window(dest_dir, (src, True))
    win._taken = lambda path: os.path.lexists(path)
    win._paste()
    moved = os.path.join(dest_dir, "report copy.txt")
    check(os.path.exists(moved) and not os.path.lexists(src),
          "a cut/paste onto a free name still moves the item")
    check(win._clipboard is None, "a completed cut clears the clipboard")
    check(win.undo is not None and win.undo[1:3] == (moved, src),
          "a completed move installs the Undo that puts it back")


# ---- inline Rename refuses a destination that appeared after its check ----
class RenameStore:
    def __init__(self, name, path):
        self.name = name
        self.path = path

    def get_iter_from_string(self, path):
        return object() if path == "0" else None

    def get_value(self, _it, column):
        return self.name if column == 1 else self.path


with tempfile.TemporaryDirectory(prefix="nb-noreplace-rename-") as root:
    src = os.path.join(root, "draft.txt")
    dst = os.path.join(root, "final.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("draft")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("late arrival")
    win = finder.Finder.__new__(finder.Finder)
    win.store = RenameStore("draft.txt", src)
    win.abspath = lambda path: path
    win._taken = lambda _path: False
    win._end_rename_mode = lambda: None
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._set_undo_move = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._select_name = lambda *args: None
    win.rel = ""
    win.undo = None
    win._on_name_edited(None, "0", "final.txt")
    check(open(src, encoding="utf-8").read() == "draft",
          "a late Rename collision preserves the source")
    check(open(dst, encoding="utf-8").read() == "late arrival",
          "a late Rename collision preserves the destination")
    check(win.undo is None and "already exists" in win.status,
          "a refused Rename installs no Undo and explains the collision")


# ---- Move to Trash rolls back metadata when its chosen name arrives late ----
with tempfile.TemporaryDirectory(prefix="nb-noreplace-trash-") as root:
    documents = os.path.join(root, "Documents")
    trash = os.path.join(root, ".Trash")
    origins = os.path.join(trash, ".origins")
    os.makedirs(documents)
    os.makedirs(origins)
    src = os.path.join(documents, "notes.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("source")
    model = SelectedModel("notes.txt", src)
    win = finder.Finder.__new__(finder.Finder)
    win._selected_iter = lambda: (model, object())
    win.abspath = lambda path: path
    win._trash_dir = lambda: trash
    win._origins_dir = lambda: origins
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._flash_undoable = lambda text: None
    win._set_undo_move = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win.rel = "Documents"
    win.undo = None
    real_move = win._rename_noreplace

    def arrive_then_move(source, destination):
        with open(destination, "w", encoding="utf-8") as fh:
            fh.write("late arrival")
        return real_move(source, destination)

    win._rename_noreplace = arrive_then_move
    win._trash_selected()
    collision = os.path.join(trash, "notes.txt")
    origin = os.path.join(origins, "notes.txt")
    check(open(src, encoding="utf-8").read() == "source",
          "a late Trash collision preserves the source")
    check(open(collision, encoding="utf-8").read() == "late arrival",
          "a late Trash collision preserves the arriving entry")
    check(not os.path.exists(origin),
          "a refused Trash move rolls back its staged origin metadata")
    check(win.undo is None and "Could not move" in win.status,
          "a refused Trash move installs no Undo and reports failure")


print("\n%d checks, %d failed" % (checks, len(failures)))
sys.exit(1 if failures else 0)
