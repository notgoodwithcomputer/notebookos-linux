#!/usr/bin/env python3
"""Headless close-guard state checks for Novel recovery-save failures."""
import errno
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


def close_fixture(dirty, retry):
    win = novel.Novel.__new__(novel.Novel)
    win._recovery_dirty = dirty
    win._save_error = OSError(errno.ENOSPC, "disk full")
    win._closeprompt = None
    win._prompt_layer = None
    win._discarded = False
    win.retry_count = 0
    win.confirm_count = 0
    win.destroyed = False

    def save():
        win.retry_count += 1
        if retry:
            win._recovery_dirty = False
            win._save_error = None
            return True
        win._recovery_dirty = True
        win._save_error = OSError(errno.ENOSPC, "disk full")
        return False

    def confirm(title, message, action, callback):
        win.confirm_count += 1
        win.confirm = (title, message, action, callback)
        win._prompt_layer = object()

    win._save_state = save
    win._confirm = confirm
    win.destroy = lambda: setattr(win, "destroyed", True)
    return win


# A durable manuscript closes immediately: no write, no card, no friction.
win = close_fixture(False, retry=False)
check(win._on_delete() is False, "clean close is allowed")
check(win.retry_count == 0 and win.confirm_count == 0,
      "clean close neither rewrites nor prompts")

# Dirty does not mean doomed: retry once and close normally when disk accepts
# it, which is the common case after a transient removable-media hiccup.
win = close_fixture(True, retry=True)
check(win._on_delete() is False, "successful close-time retry allows close")
check(win.retry_count == 1 and not win._recovery_dirty,
      "successful retry makes the current model durable")
check(win.confirm_count == 0, "successful retry stays silent")

# Regression: a failed final write must veto destruction while the window is
# the only remaining copy of the new prose.
win = close_fixture(True, retry=False)
check(win._on_delete() is True, "failed retry vetoes close")
check(not win.destroyed and win._recovery_dirty,
      "failed retry keeps both window and dirty recovery state alive")
check(win.confirm_count == 1, "failed retry offers one explicit decision")
check("not saved" in win.confirm[0].lower(),
      "the confirmation names the actual risk")
check("disk is full" in win.confirm[1].lower(),
      "the confirmation gives an actionable failure reason")

# Repeated Esc/close while the card is open reuses it instead of stacking
# another modal layer or recursively invoking the guard.
win._closeprompt = win._prompt_layer
check(win._on_delete() is True, "a repeated close remains vetoed")
check(win.confirm_count == 1, "a repeated close does not stack confirmations")

# Explicit discard is the sole path that destroys an unsaved window. It marks
# intent first so destroy-time cleanup does not contradict the chosen action.
win.confirm[3]()
check(win._discarded and win.destroyed,
      "Close Without Saving records consent before destruction")
check(win._closeprompt is None, "discard clears the close-card lifecycle token")

print()
if failures:
    print("NOVEL CLOSE RECOVERY SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("NOVEL CLOSE RECOVERY SELFTEST: %d checks, all pass" % checks)
