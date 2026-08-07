#!/usr/bin/env python3
"""
Move to Trash, and Put Back, across a filesystem boundary.

The Trash is a single folder under $NB_HOME. Everything on a USB stick, an SD
card or a second disk is therefore on the far side of a filesystem boundary from
it, and the kernel answers a rename across that boundary with EXDEV rather than
moving anything. Finder's Move to Trash was one `os.rename`, so on removable
media the command was offered on every row and could never once succeed: it
always answered "Could not move ... to Trash". Paste had had the cross-disk
branch for its cut all along; Trash never got it. (ROADMAP #16, which records
this as living on the restore path — it does not; only Paste had it, and Put
Back was missing it too.)

This uses a REAL second filesystem rather than a mocked one. /dev/shm and /tmp
are separate mounts on any Linux and both are writable without root, so the
stick lives on one and $NB_HOME on the other and `os.rename` between them raises
a genuine EXDEV. A test that monkeypatched `_same_filesystem` to return False
would prove only that the new branch runs when told to; it could not prove the
branch is reached by the condition that actually occurs on a stick, which is the
half that was broken.

The suite refuses to run rather than pass if it cannot obtain two filesystems --
a cross-filesystem test that quietly ran on one filesystem would be the most
misleading possible green.

Run:
    tools/guestrun.sh python3 tools/finder_crossfs_trash_selftest.py
"""
import os
import sys
import shutil
import tempfile

# Two real filesystems, or nothing. NB_HOME is pinned before finder is imported:
# finder.HOME is read at import time and defaults to the caller's real home.
CANDIDATES = ["/dev/shm", "/run/user/%d" % os.getuid(), "/tmp",
              os.path.expanduser("~")]
_seen, _fs = set(), []
for _c in CANDIDATES:
    try:
        dev = os.stat(_c).st_dev
    except OSError:
        continue
    if dev in _seen or not os.access(_c, os.W_OK):
        continue
    _seen.add(dev)
    _fs.append(_c)
if len(_fs) < 2:
    print("SKIPPED-AS-FAILURE: need two writable filesystems, found %d (%s)"
          % (len(_fs), ", ".join(_fs)))
    print("RESULT: FAILED")
    sys.exit(1)

HOME_FS, STICK_FS = _fs[0], _fs[1]
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nbfinder-xfs-home-",
                                         dir=HOME_FS)
for _d in ("Documents", "Applications", "Pictures", "Music", "Videos"):
    os.makedirs(os.path.join(os.environ["NB_HOME"], _d), exist_ok=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import finder  # noqa: E402

HOME = finder.HOME
STICK = tempfile.mkdtemp(prefix="nbfinder-xfs-stick-", dir=STICK_FS)
TRASH = os.path.join(HOME, ".Trash")
PAYLOAD = b"the only copy of this file\n"

FAILED = []
N = [0]


def check(name, cond):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILED.append(name)


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration()
        i += 1


def select_in(w, rel, name):
    w.load(rel)
    for i in range(len(w.store)):
        if w.store[i][1] == name:
            w.tree.get_selection().select_path(
                Gtk.TreePath.new_from_string(str(i)))
            return True
    return False


def main():
    print("home  %s  (fs %s)" % (HOME, os.stat(HOME).st_dev))
    print("stick %s  (fs %s)" % (STICK, os.stat(STICK).st_dev))

    # The premise of the whole suite: these really are two filesystems, and a
    # rename between them really does fail. If this ever stops being true the
    # rest of the checks are meaningless and must not report PASS.
    a = os.path.join(STICK, ".probe")
    with open(a, "wb") as fh:
        fh.write(b"x")
    try:
        os.rename(a, os.path.join(HOME, ".probe"))
        check("a rename from the stick to home raises EXDEV", False)
        return 1
    except OSError as exc:
        import errno
        check("a rename from the stick to home raises EXDEV",
              exc.errno == errno.EXDEV)
    finally:
        for p in (a, os.path.join(HOME, ".probe")):
            if os.path.lexists(p):
                os.remove(p)

    w = finder.Finder()
    pump()

    # ---- Move to Trash, off the stick --------------------------------
    src = os.path.join(STICK, "notes.txt")
    with open(src, "wb") as fh:
        fh.write(PAYLOAD)

    check("Finder lists the stick", select_in(w, STICK, "notes.txt"))
    w._trash_selected()
    pump()

    trashed = os.path.join(TRASH, "notes.txt")
    check("the item reaches the Trash", os.path.lexists(trashed))
    check("the bytes survive the crossing",
          os.path.exists(trashed) and open(trashed, "rb").read() == PAYLOAD)
    check("it is gone from the stick", not os.path.lexists(src))
    check("its origin is recorded so Put Back knows where it came from",
          os.path.exists(os.path.join(TRASH, ".origins", "notes.txt")))
    # The staging entry _copy writes must never be left behind on the stick or
    # in the Trash.
    check("no staging entry is left in the Trash",
          not [n for n in os.listdir(TRASH) if n.startswith(".nbcopy-")])

    # ---- Put Back, onto the stick ------------------------------------
    # Every Put Back assertion below is only meaningful if the item actually
    # reached the Trash and left the stick. When the trash step fails, the file
    # is still sitting on the stick, and "Put Back returns it to the stick"
    # would then pass without Put Back having done anything at all -- a vacuous
    # green on the very path under test. Refuse to evaluate them instead.
    ready = os.path.lexists(trashed) and not os.path.lexists(src)
    if not ready:
        for name in ("the Trash lists it",
                     "Put Back returns it to the stick",
                     "the bytes survive the return trip",
                     "it is gone from the Trash",
                     "its origin record is cleaned up",
                     "no staging entry is left on the stick"):
            check(name + "  [not reached: nothing was trashed]", False)
        return 1

    check("the Trash lists it", select_in(w, ".Trash", "notes.txt"))
    w._restore_selected()
    pump()

    check("Put Back returns it to the stick", os.path.lexists(src))
    check("the bytes survive the return trip",
          os.path.exists(src) and open(src, "rb").read() == PAYLOAD)
    check("it is gone from the Trash", not os.path.lexists(trashed))
    check("its origin record is cleaned up",
          not os.path.exists(os.path.join(TRASH, ".origins", "notes.txt")))
    check("no staging entry is left on the stick",
          not [n for n in os.listdir(STICK) if n.startswith(".nbcopy-")])

    # ---- A folder, not just a file -----------------------------------
    d = os.path.join(STICK, "photos")
    os.makedirs(os.path.join(d, "sub"), exist_ok=True)
    with open(os.path.join(d, "sub", "a.txt"), "wb") as fh:
        fh.write(PAYLOAD)
    check("Finder lists the folder", select_in(w, STICK, "photos"))
    w._trash_selected()
    pump()
    check("a folder reaches the Trash whole",
          os.path.exists(os.path.join(TRASH, "photos", "sub", "a.txt")))
    check("the folder is gone from the stick", not os.path.lexists(d))

    # ---- Same-filesystem trash still takes the fast path -------------
    same = os.path.join(HOME, "Documents", "local.txt")
    with open(same, "wb") as fh:
        fh.write(PAYLOAD)
    check("Finder lists the home file", select_in(w, "Documents", "local.txt"))
    w._trash_selected()
    pump()
    check("a same-disk trash still works",
          os.path.lexists(os.path.join(TRASH, "local.txt"))
          and not os.path.lexists(same))
    # Only the same-disk path offers a one-step Undo; the cross-disk one
    # deliberately does not, because undoing it would be a second long copy.
    check("a same-disk trash is undoable", w._undo is not None)

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    for p in (HOME, STICK):
        shutil.rmtree(p, ignore_errors=True)
sys.exit(rc)
