#!/usr/bin/env python3
"""board_settle_selftest — the desktop board does NOT animate its arrival.

The staggered settle-in this suite used to drive was REMOVED on the design
owner's direction (2026-08-09): the board's cards change from AMBIENT
sources — a store monitor firing, an app closing and the board remapping —
and animating an ambient change is motion the user did not cause, which the
design forbids. It also ran on every return from a closed app and read as
buggy.

So this suite now pins the REMOVAL, the way panel_menu_selftest pins the
retired panel drop: re-adding a settle animation (or leaving its machinery
behind) is the regression. Exit status is the number of failures.
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="board-settle-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import widgets                                                # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


source = open(os.path.join(DE, "widgets.py"), encoding="utf-8").read()

check("the settle machinery is gone from the class",
      not hasattr(widgets.Widgets, "_SETTLE_RISE")
      and not hasattr(widgets.Widgets, "_settle_progress"))

check("no settle constant or tick remains in the source",
      "_SETTLE_RISE" not in source and "settle_tick" not in source)

check("the removal is recorded beside the mapping code, with the reason",
      "board-settle transition" in source
      and "2026-08-09" in source)

# The board must not re-grow a whole-surface arrival animation under another
# name: nbmotion may only be imported for the shared policy plumbing, never
# driven with a translate on the board's own draw. A draw-translate on the
# top-level grid is the settle by any name.
check("the board's draw path carries no arrival translate",
      "translate(0, -" not in source)

print("board settle: %d checks, %d failed" % (4, len(FAILS)))
print("RESULT: " + ("ALL PASS" if not FAILS else "SOME FAILED"))
sys.exit(len(FAILS))
