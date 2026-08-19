#!/usr/bin/env python3
"""Headless lifecycle checks for Novel's in-window prompt focus."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import novel  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Focus:
    def __init__(self, raises=False):
        self.calls = 0
        self.raises = raises

    def grab_focus(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("widget was replaced")


class Overlay:
    def __init__(self):
        self.removed = []

    def remove(self, layer):
        self.removed.append(layer)


def fixture(focus):
    win = novel.Novel.__new__(novel.Novel)
    win._prompt_layer = object()
    win._prompt_return_focus = focus
    win._overlay = Overlay()
    return win


# Cancel, scrim click and Escape all converge on _close_prompt. Once the
# prompt's focused Entry/Button is removed, the original editor must regain
# focus or keyboard input has nowhere to go.
focus = Focus()
win = fixture(focus)
layer = win._prompt_layer
check(win._close_prompt() is True, "an open prompt is dismissed")
check(win._overlay.removed == [layer], "the topmost prompt layer is removed")
check(focus.calls == 1, "focus returns to the prompt's logical invoker")
check(win._prompt_layer is None and win._prompt_return_focus is None,
      "prompt and focus lifecycle tokens are cleared together")

# A second Escape must fall through to the next layer/window exactly once; it
# cannot refocus or dismiss the already-removed prompt again.
check(win._close_prompt() is False, "an absent prompt is not dismissed twice")
check(focus.calls == 1, "repeated Escape does not repeat focus restoration")

# The invoker can legitimately disappear while a prompt is open (Open/New can
# rebuild the editor). Focus restoration is best-effort, but cleanup must still
# finish so the next keyboard event is not trapped behind a phantom overlay.
gone = Focus(raises=True)
win = fixture(gone)
check(win._close_prompt() is True, "a replaced invoker cannot block dismissal")
check(gone.calls == 1, "focus restoration was attempted once")
check(win._prompt_layer is None and win._prompt_return_focus is None,
      "failed restoration still clears all prompt ownership")

print()
if failures:
    print("NOVEL PROMPT FOCUS SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    print("RESULT: FAIL")
    raise SystemExit(1)
print("NOVEL PROMPT FOCUS SELFTEST: %d checks, all pass" % checks)
print("RESULT: PASS")
