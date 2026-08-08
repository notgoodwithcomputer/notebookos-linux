#!/usr/bin/env python3
"""finder_confirm_card_selftest — destructive confirms grow from their control
(Article B, app.confirm) AND stay safe.

The finder confirm stopped being a modal dialog and became a card that grows
from the control that raised it (Empty Trash from its toolbar button, Delete
Immediately from the selected row). The MOTION must not have weakened the
SAFETY: Cancel takes focus only once the card is shown, Esc and scrim cancel,
and the danger action is single-shot. Construction needs a display, so the
contract is read from the source (commands_selftest's way). Exit status is
the failures.
"""
import inspect
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="finder-confirm-card-")

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
import finder                                                 # noqa: E402

FAILS = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


conf = inspect.getsource(finder.Finder._confirm)

# 1. origin: grows from an anchor via the shared presenter
check("the confirm carries its inventory origin marker",
      "nbmotion-inventory: app.confirm" in conf)
check("the confirm grows from its anchor via the presenter",
      "_present_card_from(" in conf and "anchor" in
      inspect.signature(finder.Finder._confirm).parameters)

# 2. SAFETY — the whole point of a destructive confirm
check("Cancel takes focus only ON SHOWN, not before the card exists",
      "on_shown=cancel.grab_focus" in conf)
check("the danger action is single-shot (_once)",
      "self._once(accepted)" in conf)
check("Cancel routes through the card's close, destroys nothing else",
      "self._info_close()" in conf)
check("Esc cancels via the shared info-key path",
      'connect("key-press-event", self._info_key)' in conf)
# accept must capture the close BEFORE running on_yes (on_yes can open its own
# card and reassign self._info_close)
acc = conf[conf.index("def accepted"):]
check("accept captures its own close before the action can reassign it",
      "close = self._info_close" in acc
      and acc.index("close = self._info_close") < acc.index("on_yes()"))

# 3. both destructive callers pass an in-window anchor
et = inspect.getsource(finder.Finder._confirm_empty_trash)
df = inspect.getsource(finder.Finder._confirm_delete_forever)
check("Empty Trash grows from the toolbar button",
      "widget_rect(self.empty_btn" in et)
check("Delete Immediately grows from the selected row",
      "_selected_row_anchor()" in df)

# 4. on_shown fires on BOTH the animated and the instant path (a confirm that
# never focuses Cancel because motion was off would be a safety hole)
pres = inspect.getsource(finder.Finder._present_card_from)
check("on_shown fires wherever the card is revealed",
      pres.count("on_shown()") >= 1 and "def reveal" in pres
      and "reveal()" in pres)

print("\n%d failure(s)" % len(FAILS))
sys.exit(len(FAILS))
