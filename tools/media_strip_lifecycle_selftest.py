#!/usr/bin/env python3
"""Headless stale-idle checks for Media's deferred filmstrip scrolling."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import media  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Allocation:
    def __init__(self, x):
        self.x = x
        self.width = 20


class Button:
    def __init__(self, x):
        self.allocation = Allocation(x)

    def get_allocation(self):
        return self.allocation


class Adjustment:
    def __init__(self):
        self.values = []

    def get_page_size(self):
        return 100

    def get_lower(self):
        return 0

    def get_upper(self):
        return 1000

    def set_value(self, value):
        self.values.append(value)


class Scroll:
    def __init__(self, adjustment):
        self.adjustment = adjustment

    def get_hadjustment(self):
        return self.adjustment


def fixture(path="/Pictures/a.png"):
    viewer = media.MediaViewer.__new__(media.MediaViewer)
    button = Button(420)
    adjustment = Adjustment()
    viewer._closed = False
    viewer._media_path = path
    viewer._strip_btns = {path: button}
    viewer._strip_scroll = Scroll(adjustment)
    return viewer, button, adjustment


captured = []
real_idle = media.GLib.idle_add
media.GLib.idle_add = lambda callback, *args: (captured.append(
    (callback, args)) or len(captured))
try:
    # Current owner: the deferred callback is allowed and centres its button.
    viewer, button, adjustment = fixture()
    viewer._scroll_strip_to("/Pictures/a.png")
    callback, args = captured.pop(0)
    check(callback(*args) is False, "current callback is one-shot")
    check(len(adjustment.values) == 1,
          "current callback scrolls the current filmstrip")

    # Regression: A was queued, then B replaced the strip. A must not use its
    # detached allocation against B's adjustment.
    viewer, old_button, adjustment = fixture()
    viewer._scroll_strip_to("/Pictures/a.png")
    callback, args = captured.pop(0)
    viewer._media_path = "/Pictures/b.png"
    viewer._strip_btns = {"/Pictures/b.png": Button(40)}
    check(callback(*args) is False, "stale callback removes itself")
    check(adjustment.values == [],
          "old media cannot scroll the replacement filmstrip")

    # Rebuilding the same path also replaces widget ownership; identity, not
    # filename alone, decides whether the saved allocation is still valid.
    viewer, old_button, adjustment = fixture()
    viewer._scroll_strip_to("/Pictures/a.png")
    callback, args = captured.pop(0)
    viewer._strip_btns["/Pictures/a.png"] = Button(80)
    check(callback(*args) is False and adjustment.values == [],
          "a detached same-path button cannot mutate the rebuilt strip")

    # Destroy is a terminal ownership change even if path and map survive until
    # GTK tears their children down.
    viewer, button, adjustment = fixture()
    viewer._scroll_strip_to("/Pictures/a.png")
    callback, args = captured.pop(0)
    viewer._closed = True
    check(callback(*args) is False and adjustment.values == [],
          "post-destroy callback is inert and one-shot")
finally:
    media.GLib.idle_add = real_idle

print()
if failures:
    print("MEDIA STRIP LIFECYCLE SELFTEST: %d checks, %d FAILED" %
          (checks, len(failures)))
    raise SystemExit(1)
print("MEDIA STRIP LIFECYCLE SELFTEST: %d checks, all pass" % checks)
