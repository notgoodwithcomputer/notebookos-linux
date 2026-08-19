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
    # A store that read fine. The quarantined case has its own fixture below:
    # the guard says something different there, because retrying cannot work.
    win._store_read_only = False
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

    def confirm(title, message, action, callback, **kw):
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

# A QUARANTINED STORE IS NOT A FULL DISK. This session refuses, by design, to
# write over the manuscript it could not read, so "make room and close again to
# try once more" sends the writer to clear space that is not short and to retry
# a save that can never happen. Say what is true instead.
win = close_fixture(True, retry=False)
win._store_read_only = True
check(win._on_delete() is True, "a quarantined session still vetoes the close")
check("make room" not in win.confirm[1].lower(),
      "the quarantined close guard does not blame the disk")
check("kept" in win.confirm[1].lower()
      and "saved over" in win.confirm[1].lower(),
      "...it says the manuscript was kept and will not be written over")
check("typed here" in win.confirm[1].lower(),
      "...and that closing loses what was typed in this session")

# A manuscript that could not be read leaves a seeded Chapter 1 on screen —
# indistinguishable from a new book, and the most alarming thing this app can
# show someone who had a book here yesterday. It used to say nothing at all;
# the only signal was "Not saved" appearing after the first sentence typed.
def unreadable_notice_fixture(read_only):
    win = novel.Novel.__new__(novel.Novel)
    win._store_read_only = read_only
    said = []
    win._confirm = lambda title, body, ok, cb, **kw: said.append(
        (title, body, ok, kw))
    return win, said


win, said = unreadable_notice_fixture(True)
win._say_store_unreadable()
check(len(said) == 1, "an unreadable manuscript is explained, not left blank")
check(bool(said) and "could not be read" in said[0][0],
      "the card says the manuscript could not be read")
check(bool(said) and "kept" in said[0][1],
      "...and that the writing was kept")
check(bool(said) and "saved over" in said[0][1],
      "...and that nothing typed here will overwrite it")
# No path, no errno: a dated quarantine name is not something a writer can act
# on, and an error number is not a fact about their manuscript.
check(bool(said) and ".damaged-" not in said[0][1] and "rrno" not in said[0][1],
      "the card names no path and no error number")
# It TELLS the writer something; it does not ask them to decide anything. Two
# buttons that do the same nothing — one of them painted the red of a
# destructive action — offer a choice that does not exist.
check(bool(said) and said[0][3].get("cancel") is False,
      "the notice is acknowledged with one button, not chosen between two")
check(bool(said) and said[0][3].get("danger") is False,
      "...and that button is not dressed as a destructive one")

# THE NOTICE BELONGS TO THE DAMAGED CASE ONLY, and the card cannot enforce
# that itself — it is the CALL that must be gated, so that is what is checked.
# Structurally, not by grep: "the word _store_read_only appears nearby" would
# pass on a comment mentioning it.
import ast

tree = ast.parse(open(os.path.join(DE, "novel.py"), encoding="utf-8").read())


def mentions(node, name):
    # Both spellings of "reads this flag": self._flag, and the
    # getattr(self, "_flag", default) form this codebase uses wherever a
    # half-constructed window might not carry it yet.
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == name:
            return True
        if isinstance(n, ast.Constant) and n.value == name:
            return True
    return False


call_sites = []
for node in ast.walk(tree):
    if not isinstance(node, ast.If):
        continue
    for branch, guarded in (("body", True), ("orelse", False)):
        for stmt in getattr(node, branch):
            # An `elif` is an If nested in the outer If's orelse, so a nested
            # branch would otherwise count the same call twice — once
            # unguarded (as the outer's orelse) and once guarded.
            if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try)):
                continue
            if mentions(stmt, "_say_store_unreadable"):
                call_sites.append((guarded and mentions(node.test,
                                                        "_store_read_only"),
                                   ast.dump(node.test)[:60]))
check(len(call_sites) == 1,
      "the notice has exactly one call site")
check(bool(call_sites) and call_sites[0][0],
      "...and it is reached only when the store could not be read")

deferred = any(isinstance(n, ast.Attribute) and n.attr == "idle_add"
               for site in [t for t in ast.walk(tree)
                            if isinstance(t, ast.If)
                            and mentions(t, "_say_store_unreadable")]
               for n in ast.walk(site))
check(deferred,
      "the notice is deferred to idle, after the window it sits in exists")

print()
if failures:
    print("NOVEL CLOSE RECOVERY SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    print("RESULT: FAIL")
    raise SystemExit(1)
print("NOVEL CLOSE RECOVERY SELFTEST: %d checks, all pass" % checks)
print("RESULT: PASS")
