#!/usr/bin/env python3
"""finder_empty_state_selftest — the empty message settles in / departs.

finder.empty-populated (motion inventory): the empty-folder / no-results message
(`self._empty_label`) FADES IN when a view empties and OUT when it populates,
rather than blinking — a fade on the label's opacity, the same nbmotion.fade_to
primitive list<->grid and search-results use. Only on the empty<->populated
BOUNDARY: a populate that stays empty (a search narrowing to nothing) just
rewrites the text and never re-fades, so there is no flicker while typing.

The contract is read from the source that runs (the way the Finder's other
motion suites work — constructing and driving nbmotion's frame clock is display-
and timing-dependent, and the fade's landing is gated by motion_selftest). Exit
status is the failure count.

RED-PROOF (recorded 2026-08-08): changing the settle-in `fade_to(lbl, 1.0,
nbmotion.SURFACE_IN, ...)` to `fade_to(lbl, 0.0, ...)` aims the appearance at a
dim and turns "settles in ... fades UP to full" red; removing the
`# nbmotion-inventory: finder.empty-populated` marker turns the marker check red;
dropping `if was_empty` / `if not was_empty` turns the boundary-guard checks red.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="finder-empty-"))

import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                    # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


src = inspect.getsource(finder.Finder._update_empty_state)

check("carries the finder.empty-populated marker",
      "nbmotion-inventory: finder.empty-populated" in src)

# the boundary guard: the prior empty state is read, and BOTH fades are gated on
# it, so only an empty<->populated transition animates (never a stay-empty
# populate, which would flicker per keystroke).
check("reads the prior empty state before deciding (boundary guard)",
      "was_empty = lbl.get_visible()" in src)
check("departs on populate: fades OUT then hides, only if it WAS showing",
      "if was_empty" in src
      and "fade_to(lbl, 0.0, nbmotion.SURFACE_OUT" in src
      and "lbl.hide()" in src)
check("settles in on empty: starts hidden, fades UP to full, only if it was NOT "
      "showing",
      "if not was_empty" in src
      and "set_opacity(0.0)" in src
      and "fade_to(lbl, 1.0, nbmotion.SURFACE_IN" in src)

# F2: opacity is the only thing animated — never a layout property.
check("animates opacity only (no layout property, F2)",
      "fade_to" in src and not any(b in src for b in
      ("set_size_request", "set_margin", "set_padding", "set_border_width")))

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
