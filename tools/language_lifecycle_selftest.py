#!/usr/bin/env python3
"""Headless ownership checks for Language lesson feedback timers."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import language  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


scheduled = {}
removed = []
next_id = [20]
real_add = language.GLib.timeout_add
real_remove = language.GLib.source_remove


def add(_delay, callback):
    next_id[0] += 1
    scheduled[next_id[0]] = callback
    return next_id[0]


language.GLib.timeout_add = add
language.GLib.source_remove = lambda source_id: removed.append(source_id)
try:
    win = language.Language.__new__(language.Language)
    win._closed = False
    win._lesson_gen = 0
    win._lesson_sources = set()
    calls = []
    first = win._lesson_later(750, lambda: calls.append("old-advance"))
    second = win._lesson_later(900, lambda: calls.append("old-hearts"))
    check(win._lesson_sources == {first, second},
          "lesson one-shots are recorded by their owner")

    win._cancel_lesson_callbacks()
    check(set(removed) == {first, second} and win._lesson_sources == set(),
          "a lesson boundary removes and clears every owned source")
    scheduled[first]()
    scheduled[second]()
    check(calls == [],
          "already-queued callbacks from the old generation do nothing")

    fresh = win._lesson_later(250, lambda: calls.append("fresh-grade"))
    check(scheduled[fresh]() is False and calls == ["fresh-grade"],
          "a current live callback runs exactly once")
    check(fresh not in win._lesson_sources,
          "a delivered one-shot unregisters itself")

    # The concrete regression: quit while the out-of-hearts delay is pending.
    win._lesson = {"i": 0}
    win._save_progress = lambda: calls.append("save")
    win._back_to_course = lambda: calls.append("course")
    stale_hearts = win._lesson_later(
        900, lambda: calls.append("OUT OF HEARTS SHELL"))
    win._quit_lesson()
    scheduled[stale_hearts]()
    check(win._lesson is None and calls[-2:] == ["save", "course"],
          "quitting returns to the course and clears the lesson")
    check("OUT OF HEARTS SHELL" not in calls,
          "a stale out-of-hearts callback cannot replace the course screen")

    # Close ordering and idempotence.
    pending = win._lesson_later(750, lambda: calls.append("after-close"))
    saves = []
    win._save_progress = lambda: saves.append(win._closed)
    win._on_destroy()
    win._on_destroy()
    scheduled[pending]()
    check(win._closed is True and "after-close" not in calls,
          "destroy closes the gate before queued delivery")
    check(removed.count(pending) == 1 and win._lesson_sources == set(),
          "destroy removes each pending lesson source exactly once")
    check(saves == [True],
          "destroy saves progress once, after marking the owner closed")
finally:
    language.GLib.timeout_add = real_add
    language.GLib.source_remove = real_remove

print("\n%d checks, %d failed" % (checks, len(failures)))
sys.exit(1 if failures else 0)
