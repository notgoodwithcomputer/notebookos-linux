#!/usr/bin/env python3
"""Headless selftest for the E-book Reader font-size buttons.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/root python3 ebook_selftest.py

Validates the newly-wired reading font-size controls:
  * stored size starts at its default,
  * "larger" increases it,
  * "smaller" decreases it,
  * it clamps at the min and max bounds.
"""
import inspect

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import ebook  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def find_window_cls(mod):
    for _, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    raise SystemExit("no Gtk.Window subclass found in module")


def main():
    cls = find_window_cls(ebook)
    win = cls()

    lo, hi, default = cls.READ_PT_MIN, cls.READ_PT_MAX, cls.READ_PT_DEFAULT

    # 1. starts at default
    check("starts_at_default(%d)" % default, win._read_pt == default)

    # 2. "larger" increases the stored size
    before = win._read_pt
    win._on_text_larger()
    check("larger_increases", win._read_pt == before + 1 and win._read_pt > before)

    # 3. "smaller" decreases the stored size
    before = win._read_pt
    win._on_text_smaller()
    check("smaller_decreases", win._read_pt == before - 1 and win._read_pt < before)

    # sanity: back to default after one up + one down
    check("round_trip_to_default", win._read_pt == default)

    # 4a. clamps at the maximum
    for _ in range(100):
        win._on_text_larger()
    check("clamps_at_max(%d)" % hi, win._read_pt == hi)

    # one more click at the ceiling must not overshoot
    win._on_text_larger()
    check("no_overshoot_above_max", win._read_pt == hi)

    # 4b. clamps at the minimum
    for _ in range(100):
        win._on_text_smaller()
    check("clamps_at_min(%d)" % lo, win._read_pt == lo)

    # one more click at the floor must not undershoot
    win._on_text_smaller()
    check("no_undershoot_below_min", win._read_pt == lo)

    # the restyle path actually ran without error and left a live provider
    check("read_css_provider_present", isinstance(win._read_css, Gtk.CssProvider))
    check("read_labels_wired", len(getattr(win, "_read_labels", ())) >= 1)

    print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED"))


if __name__ == "__main__":
    main()
