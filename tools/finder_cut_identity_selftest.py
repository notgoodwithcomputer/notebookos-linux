#!/usr/bin/env python3
"""finder_cut_identity_selftest — whose file does a cross-disk move delete?

    python3 tools/finder_cut_identity_selftest.py

WHY THIS FILE EXISTS
Cut an item, then Paste it onto a USB stick: that is not a rename, it is a
full copy followed by a delete of the original. The copy runs on a worker and
can take minutes. `_paste` used to finish it by calling `_undo_remove(src)` —
by NAME. In those minutes the original can be deleted and something else can
take the same name: a different document, a folder, a symlink. The move then
finished by erasing that replacement, which nobody cut, and said "Moved".

A pathname is not an identity. `_paste` now records the identity of the entry
being cut (device, inode, and kind) before the copy starts, and removes the
original only if that exact entry is still standing there. If it is not, the
finished copy is kept — it is the only remaining record of what was cut — the
replacement is left untouched, and the status says what really happened
instead of "Moved".

No display is needed. `_paste` is the shipped one; `_same_filesystem` is
forced to answer "different disks" (the USB case, which cannot be created in a
temp dir) and `_copy` is stood in for so the window between "copy finished on
disk" and "on_done(True)" can be opened deliberately. The stand-in keeps the
real contract: it claims the destination, publishes it, releases the claim,
then reports through on_done.
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_LANG", "en")
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-cutid-home-"))

import finder                                                   # noqa: E402

CHECKS = [0]
FAILURES = []


def check(cond, what):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILURES.append(what)


class Win(finder.Finder):
    """The paste path only, with no window and no disk to move between."""

    def __init__(self, dest_dir):                              # noqa: D107
        self.rel = "dst"
        self._dest = dest_dir
        self._clipboard = None
        self._inflight = set()
        self._undo = None
        self.status = []
        self.pending = []              # copies started but not yet reported
        self.removals = []             # every attempt to remove the original

    def abspath(self, _rel):
        return self._dest

    def load(self, *a, **k):
        pass

    def _update_paste(self):
        pass

    def _flash_status(self, msg, restore_ms=2400):
        self.status.append(msg)

    def _flash_undoable(self, msg):
        self.status.append(msg)

    @staticmethod
    def _same_filesystem(_src, _dest_dir):
        return False                   # the USB stick, which is the whole case

    def _copy(self, src, dst, on_done):
        """finder._copy's contract without the worker: the destination is
        written when the job is settled, not when it is started."""
        self._inflight.add(dst)
        body = None
        if not os.path.islink(src) and os.path.isfile(src):
            with open(src, "rb") as fh:
                body = fh.read()

        def settle(ok):
            if ok:
                if body is None:       # a folder or a link: shape is enough
                    os.makedirs(dst, exist_ok=True)
                else:
                    with open(dst, "wb") as fh:
                        fh.write(body)
            self._inflight.discard(dst)
            on_done(ok)

        self.pending.append(settle)

    def _undo_remove(self, path, identity=None):
        self.removals.append(path)
        return finder.Finder._undo_remove(self, path, identity)


def new_case(root, name, kind="file"):
    """A cut item on the clipboard and an empty destination folder."""
    src_dir = os.path.join(root, name + "-src")
    dst_dir = os.path.join(root, name + "-dst")
    os.makedirs(src_dir)
    os.makedirs(dst_dir)
    src = os.path.join(src_dir, "thesis.txt")
    if kind == "file":
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("the original, cut by the user")
    elif kind == "link":
        target = os.path.join(src_dir, "target.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("link target")
        os.symlink(target, src)
    win = Win(dst_dir)
    win._clipboard = (src, True)       # Cut
    return win, src, dst_dir


def said(win):
    return " ".join(win.status)


def main():
    root = tempfile.mkdtemp(prefix="nb-cutid-")
    try:
        print("\n-- a file replacing the original under its name survives")
        win, src, dst_dir = new_case(root, "file")
        win._paste()
        check(win.pending and not os.path.exists(
            os.path.join(dst_dir, "thesis.txt")),
            "the copy is under way and has not landed yet")
        os.remove(src)                 # the original goes...
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("SOMEONE ELSE'S WORK")   # ...and a new file takes the name
        win.pending.pop()(True)        # the long copy finishes successfully
        check(os.path.lexists(src), "the replacement is still there")
        with open(src, encoding="utf-8") as fh:
            check(fh.read() == "SOMEONE ELSE'S WORK",
                  "with its own contents, untouched")
        landed = os.path.join(dst_dir, "thesis.txt")
        check(os.path.exists(landed),
              "the finished copy is kept — it is what was actually cut")
        with open(landed, encoding="utf-8") as fh:
            check(fh.read() == "the original, cut by the user",
                  "and holds what the user cut, not the replacement")
        check("Moved" not in said(win),
              "nothing claims a move happened: %r" % (win.status,))
        check("original changed" in said(win),
              "the status says the original changed: %r" % (win.status,))
        check(win._inflight == set(),
              "the destination claim is released: %r" % (win._inflight,))
        check(win._clipboard is None, "and the cut stays one-shot")

        print("\n-- a symlink taking the name is an entry, not a target")
        win, src, dst_dir = new_case(root, "link")
        elsewhere = os.path.join(root, "link-elsewhere")
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, "keep.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("must survive")
        win._paste()
        os.remove(src)
        os.symlink(elsewhere, src)     # a link now stands at the pathname
        win.pending.pop()(True)
        check(os.path.islink(src), "the link is still there, as a link")
        check(os.path.realpath(src) == os.path.realpath(elsewhere)
              and os.listdir(elsewhere) == ["keep.txt"],
              "and the folder it points at was not walked into and emptied")
        check("Moved" not in said(win),
              "no move is claimed over a replaced source: %r" % (win.status,))

        print("\n-- a folder taking the name is refused too")
        win, src, dst_dir = new_case(root, "folder")
        win._paste()
        os.remove(src)
        os.makedirs(src)
        with open(os.path.join(src, "inside.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("someone's folder")
        win.pending.pop()(True)
        check(os.path.isdir(src) and os.listdir(src) == ["inside.txt"],
              "the folder standing at the name keeps its contents")
        check("Moved" not in said(win),
              "and no move is claimed: %r" % (win.status,))

        print("\n-- the original vanishing is not a move either")
        win, src, dst_dir = new_case(root, "gone")
        win._paste()
        os.remove(src)
        win.pending.pop()(True)
        check(os.path.exists(os.path.join(dst_dir, "thesis.txt")),
              "the finished copy is kept")
        check("Moved" not in said(win),
              "with no move claimed: %r" % (win.status,))

        print("\n-- an unchanged original IS removed, once, and says Moved")
        win, src, dst_dir = new_case(root, "clean")
        win._paste()
        win.pending.pop()(True)
        check(not os.path.lexists(src), "the original is gone")
        check(win.removals == [src],
              "removed exactly once, by the move itself: %r" % (win.removals,))
        landed = os.path.join(dst_dir, "thesis.txt")
        check(os.path.exists(landed), "the copy is in the destination")
        check("Moved" in said(win),
              "and the move is reported: %r" % (win.status,))

        print("\n-- an unchanged symlink moves as itself")
        win, src, dst_dir = new_case(root, "cleanlink", kind="link")
        win._paste()
        win.pending.pop()(True)
        check(not os.path.lexists(src), "the link is gone from the source")
        check(os.path.exists(os.path.join(os.path.dirname(src),
                                          "target.txt")),
              "the file it pointed at was not removed with it")
        check("Moved" in said(win),
              "and the move is reported: %r" % (win.status,))

        print("\n-- a failed copy still changes nothing")
        win, src, dst_dir = new_case(root, "failed")
        win._paste()
        win.pending.pop()(False)
        check(os.path.exists(src), "the original survives a failed copy")
        check(win.removals == [],
              "nothing was removed: %r" % (win.removals,))
        check("Moved" not in said(win),
              "and no move is claimed: %r" % (win.status,))

        print("\n-- and the check can go red: by name, the replacement dies")
        win, src, dst_dir = new_case(root, "mutation")
        # The behaviour before the fix, expressed exactly: let the pathname BE
        # the identity, so whatever stands at the name at the end counts as
        # the thing that was cut.
        win._path_identity = lambda path: "name:" + path
        win._paste()
        os.remove(src)
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("SOMEONE ELSE'S WORK")
        win.pending.pop()(True)
        check(not os.path.lexists(src),
              "unchecked, the finished move really does destroy the "
              "replacement — so the checks above are testing something")
    finally:
        shutil.rmtree(root, True)

    print()
    if FAILURES:
        print("FINDER CUT IDENTITY SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        for f in FAILURES:
            print("   - %s" % f)
        return 1
    print("FINDER CUT IDENTITY SELFTEST: %d checks, all pass" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
