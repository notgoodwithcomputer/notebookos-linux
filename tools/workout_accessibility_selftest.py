#!/usr/bin/env python3
"""Headless contract for keyboard-selectable Workout cards."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import workout  # noqa: E402

fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


class Event:
    def __init__(self, keyval): self.keyval = keyval


app = workout.Workout.__new__(workout.Workout)
selected = []
app._on_select = lambda _w, _ev, idx: selected.append(idx) or False
for key in (workout.Gdk.KEY_Return, workout.Gdk.KEY_KP_Enter,
            workout.Gdk.KEY_space):
    check(app._on_card_key(None, Event(key), 4) is True,
          "activation key is consumed")
check(selected == [4, 4, 4], "Enter, keypad Enter, and Space share selection")
check(app._on_card_key(None, Event(workout.Gdk.KEY_Down), 4) is False
      and selected == [4, 4, 4], "unrelated keys remain available")

with open(os.path.join(DE, "workout.py"), encoding="utf-8") as fh:
    source = fh.read()
check("hit.set_can_focus(True)" in source and 'add_class("wo-cardhit")' in source,
      "card wrapper joins the focus chain")
check('set_tooltip_text(_t("Select %s") % ex["name"])' in source,
      "focusable card has an exercise-specific accessible name")
check("accessible.set_role(Atk.Role.RADIO_BUTTON)" in source and
      "Atk.StateType.SELECTED" in source and "Atk.StateType.CHECKED" in source,
      "exercise cards expose actionable role and selected state")
check("self._restore_card_focus = True" in source and
      "hit.grab_focus()" in source,
      "selection rebuild restores focus to the chosen card")
check('connect("button-press-event", self._on_select, idx)' in source,
      "existing click-anywhere selection remains")
check('add.connect("clicked", self._on_log, idx)' in source
      and 'undo.connect("clicked", self._on_undo, idx)' in source,
      "nested Log and Undo buttons remain real actions")
check("self._restore_action_focus = (idx, \"log\")" in source and
      "self._restore_action_focus = (idx, \"undo\")" in source and
      "action.grab_focus()" in source,
      "Log and Undo rebuilds restore their own keyboard focus")
focus_start = source.find(".wo-cardhit:focus .wo-card")
focus_rule = source[focus_start:source.find("}", focus_start) + 1]
check(focus_start >= 0 and "box-shadow: inset" in focus_rule
      and "#C8341E" not in focus_rule,
      "keyboard focus gets a visible non-red ink ring")
check("border-width" not in focus_rule and "padding" not in focus_rule
      and "margin" not in focus_rule,
      "focus feedback does not change card geometry")

print("\n%d failed" % len(fails))
print("RESULT: %s" % ("FAILED" if fails else "PASS"))
sys.exit(1 if fails else 0)
