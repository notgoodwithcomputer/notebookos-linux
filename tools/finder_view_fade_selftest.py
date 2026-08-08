#!/usr/bin/env python3
"""finder_view_fade_selftest — list<->grid settles in place (Article C, G2).

Switching between the list and grid presentations of the same rows fades the
incoming view in (PAGE, arrival) rather than jumping — a crossfade in place,
not a transform (Article C: different presentations of one model). Crucially
it animates only on a DELIBERATE toggle, never on the show_all remaps a
launched-app round trip triggers, or the view would flash on every return.
Construction needs a display, so the contract is read from the source.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="finder-view-fade-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                 # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


av = inspect.getsource(finder.Finder._apply_view)
sv = inspect.getsource(finder.Finder._set_view)
sa = inspect.getsource(finder.Finder.show_all)

check("_apply_view carries the list-grid inventory marker",
      "nbmotion-inventory: finder.list-grid" in av)
check("the incoming view fades in on the arrival token",
      "fade_to(incoming, 1.0, nbmotion.PAGE" in av
      and "nbmotion.EASE_OUT" in av)
check("the incoming view starts from transparent when animating",
      "fade_to(incoming, 0.0, 0)" in av)
# animate is opt-in: only a deliberate toggle passes it
check("a deliberate view toggle animates", "_apply_view(animate=True)" in sv)
check("show_all's remap does NOT animate (no flash on app round-trips)",
      "_apply_view(" in sa and "animate=True" not in sa)
# the non-animated path still guarantees full opacity (a prior fade can't
# leave the view stuck translucent)
check("the non-animated path forces full opacity",
      "fade_to(incoming, 1.0, 0)" in av)
# instant equivalence: nbmotion None => no crash, just show/hide
check("nbmotion absent degrades to plain show/hide",
      "if nbmotion is not None" in av or "nbmotion is not None" in av)

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
