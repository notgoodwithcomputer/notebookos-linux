#!/usr/bin/env python3
"""finder_sidebar_reveal_selftest — a mounted volume opens into the column.

Plugging in a USB stick used to make the WHOLE sidebar flash: `_poll_devices`
noticed the mount set had changed and called `_fill_sidebar`, which removes
every child and repacks the column from scratch. Every row the person was
already looking at was destroyed and rebuilt to show one new one.

Now only the ARRIVING volume opens — inside a Gtk.Revealer, sliding down the
column edge it belongs to (SURFACE_IN) — and every other row is packed plainly.

THE TRAP THIS GUARDS. The obvious implementation reveals every device row on
every fill, which looks right the first time you plug something in and is wrong
every other time: the rebuild happens on any mount change, so an UNPLUG, or a
second stick, would restage rows that never moved. That is a worse flash than
the one being fixed, so "only the new one opens" is pinned behaviourally below,
not just asserted in a comment.

The second trap is that the motion must never gate the FUNCTION. A drive that is
plugged in has to appear in the sidebar whether or not the animation works, so
the fallbacks are checked too.

Behavioural half drives the real code path with a stubbed `_devices()`; without
a display the source contract still runs and the skip is PRINTED, not laundered.
"""
import ast
import atexit
import inspect
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Same override the open-folder suite uses: a red proof sabotages a COPY of de/
# and must then be GRADED on that copy, not on the pristine tree file.
DE = Path(os.environ.get("FINDER_MODULE_DIR")
          or REPO / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


home = tempfile.mkdtemp(prefix="finder_sidebar_reveal_")
atexit.register(shutil.rmtree, home, True)
os.environ["NB_HOME"] = home

sys.path.insert(0, str(DE))
import finder                                                    # noqa: E402
import nbmotion                                                  # noqa: E402

fill_src = inspect.getsource(finder.Finder._fill_sidebar)
poll_src = inspect.getsource(finder.Finder._poll_devices)
pack_src = inspect.getsource(finder.Finder._sb_pack)

check("_fill_sidebar carries the sidebar-reveal inventory marker",
      "nbmotion-inventory: finder.sidebar-reveal" in fill_src)
check("_fill_sidebar takes the arriving set",
      "arriving" in inspect.signature(finder.Finder._fill_sidebar).parameters)
check("_poll_devices works out what is NEW before refilling",
      "arriving" in poll_src)
# The row must survive a broken animation: a drive you cannot see is a drive you
# cannot open, and that would be a functional regression dressed as polish.
check("a failed reveal still forces the row visible",
      "set_reveal_child(True)" in fill_src)
check("a Revealer that cannot be built degrades to a plain pack",
      "except" in pack_src and "pack_start" in pack_src)


def uses_call(src, name):
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == name:
                return True
    return False


check("the reveal goes through the shared primitive, not a hand-rolled one",
      uses_call(fill_src, "reveal"))

display_ready = False
if os.environ.get("DISPLAY"):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        display_ready = Gtk.init_check()[0]
    except Exception:                                            # noqa: BLE001
        display_ready = False

if not display_ready:
    print("SKIP behavioural half: no usable display "
          "(run under tools/guestrun.sh with DISPLAY set)")
else:
    win = finder.Finder(start="")
    devs = [("Notebook", "disk", "")]
    win._devices = lambda: list(devs)

    def revealers():
        return [c for c in win._sb.get_children()
                if isinstance(c, Gtk.Revealer)]

    def row_labels():
        """Every label rendered in the column, however it is wrapped — the
        check that the drive is actually THERE, not merely animated."""
        found = []

        def walk(w):
            if isinstance(w, Gtk.Label):
                found.append(w.get_text())
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c)
        walk(win._sb)
        return found

    win._fill_sidebar()
    check("a first fill opens nothing (the column is not staged on arrival)",
          revealers() == [])

    devs.append(("USB STICK", "usb", "/media/usb0"))
    win._poll_devices()
    revs = revealers()
    check("a newly mounted volume gets exactly one Revealer", len(revs) == 1)
    check("...which is opening, not left shut",
          bool(revs) and revs[0].get_reveal_child() is True)
    check("...on the surface-arriving token (160ms)",
          bool(revs) and revs[0].get_transition_duration() == 160)
    check("the drive is actually IN the column, not merely animated",
          "USB STICK" in row_labels())
    check("the volume that was already there did NOT restage",
          len(revs) == 1 and "Notebook" in row_labels())

    # The poll fires on a timer; only a CHANGED mount set may rebuild.
    before = revealers()
    win._poll_devices()
    check("an idle poll rebuilds nothing", revealers() == before)

    # Unplugging changes the set, so the column is rebuilt — but nothing
    # ARRIVES, so nothing may open.
    devs.pop()
    win._poll_devices()
    check("an unplug opens nothing", revealers() == [])
    check("...and the drive is gone from the column",
          "USB STICK" not in row_labels())

    # Instant equivalence (PAPER-PHYSICS §F4).
    nbmotion.set_reduced_motion(True)
    try:
        devs.append(("USB TWO", "usb", "/media/usb1"))
        win._poll_devices()
        revs2 = revealers()
        check("under reduced motion the row is revealed with no animation",
              all(r.get_reveal_child() for r in revs2))
        check("...and the drive still appears", "USB TWO" in row_labels())
    finally:
        nbmotion.set_reduced_motion(False)

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
