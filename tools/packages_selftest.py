#!/usr/bin/env python3
"""Packages: the inspector's result-line timer.

Display-free. The Packages window's Verify/Open result line ("Checked: this
package is complete") is taken away again by a GLib timer. This drives that
timer's lifecycle directly — the flash methods are called as plain functions on
a stand-in object, with a fake GLib recording every source — so no X display,
no widget and no main loop is involved.

Regression under test: the timer's source id was not kept, so

  * a second press scheduled a SECOND timer while the first was still pending,
    and the first one fired on its old schedule and wiped the new message —
    within a fraction of a second of it appearing, if the presses were close
    together (_clear_flash only checks which PACKAGE a result belongs to, not
    which result); and
  * the timer outlived the window, waking up afterwards to rebuild an
    inspector that had already been destroyed.
"""
import inspect
import os
import sys
import tempfile

DE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
DE = os.path.normpath(DE)
sys.path.insert(0, DE)

import packages as pk  # noqa: E402

FAILURES = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILURES.append(what)


class FakeGLib(object):
    """Records timers instead of running them, so a test can ask what is still
    pending without a main loop."""

    def __init__(self):
        self.next_id = 100
        self.pending = {}       # id -> (callback, args)
        self.removed = []

    def timeout_add_seconds(self, _secs, cb, *args):
        self.next_id += 1
        self.pending[self.next_id] = (cb, args)
        return self.next_id

    def source_remove(self, tid):
        self.removed.append(tid)
        if tid not in self.pending:
            raise ValueError("no such source")   # what GLib does; must not escape
        del self.pending[tid]
        return True

    def fire(self, tid):
        cb, args = self.pending.pop(tid)
        return cb(*args)


class Harness(object):
    """Just enough of a Packages window to exercise the flash lifecycle: the
    real methods, bound to an object that has no widgets at all."""

    _flash = pk.Packages._flash
    _cancel_flash_timer = pk.Packages._cancel_flash_timer
    _clear_flash = pk.Packages._clear_flash
    _on_verify = pk.Packages._on_verify
    _verify_module = pk.Packages._verify_module

    def __init__(self):
        self.sel = 0
        self.query = ""
        self._flash_src = None
        self._flash_text = ""
        self._flash_err = False
        self._flash_timer = None
        self.rebuilds = 0

    def _rebuild_detail(self):
        self.rebuilds += 1


def main():
    print("Packages — inspector result-line timer")

    # A real, parseable module file, so Verify takes its success path.
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    tmp.write('"""Fixture — a package file that parses."""\n')
    tmp.close()
    pkg = ["Fixture", "box", "Application", "1 KB", 1024, "1 Jan 2026",
           0.0, "", tmp.name]
    real_glib, real_packages = pk.GLib, pk.PACKAGES
    fake = FakeGLib()
    pk.GLib = fake
    pk.PACKAGES = [tuple(pkg)]
    try:
        h = Harness()

        # --- one press: a message, and exactly one pending timer -------------
        h._on_verify()
        first = h._flash_timer
        check(h._flash_src == 0 and h._flash_text, "Verify shows a result line")
        check(first is not None and first in fake.pending,
              "the clearing timer's source id is kept")

        # --- a second press while the first timer is still pending -----------
        h._on_verify()
        check(len(fake.pending) == 1,
              "a second Verify leaves ONE pending timer, not two")
        check(first in fake.removed, "the first press's timer was cancelled")
        check(h._flash_timer in fake.pending and h._flash_timer != first,
              "the pending timer is the second press's")

        # The symptom itself: fire every timer that is NOT the current one —
        # i.e. anything left over from the first press. There should be none;
        # if one is still pending it takes the fresh message away early.
        for tid in [t for t in list(fake.pending) if t != h._flash_timer]:
            fake.fire(tid)
        check(h._flash_src == 0 and h._flash_text,
              "no leftover timer wipes the second result early")

        # --- the surviving timer still does its job --------------------------
        if h._flash_timer in fake.pending:
            fake.fire(h._flash_timer)
        check(h._flash_src is None, "the timer clears the result line")
        check(h._flash_timer is None, "a fired timer leaves no id to cancel")

        # --- closing the window cancels a pending timer ----------------------
        h._on_verify()
        pending = h._flash_timer
        h._cancel_flash_timer()
        check(not fake.pending,
              "closing the window leaves no timer to fire into a dead window")
        check(h._flash_timer is None, "the cancelled id is forgotten")

        # cancelling twice, or cancelling a source GLib has already dropped,
        # must not raise — teardown is not a place to crash
        try:
            h._cancel_flash_timer()
            h._flash_timer = pending
            h._cancel_flash_timer()
            check(True, "cancelling an already-gone timer is harmless")
        except Exception as e:
            check(False, "cancelling an already-gone timer is harmless (%s)" % e)

        # --- the window really wires that up on destroy ----------------------
        src = inspect.getsource(pk.Packages.__init__)
        check("destroy" in src and "_cancel_flash_timer" in src,
              "__init__ cancels the timer on destroy")
    finally:
        pk.GLib, pk.PACKAGES = real_glib, real_packages
        os.unlink(tmp.name)

    print("\n%s" % ("FAILED: %d" % len(FAILURES) if FAILURES else "all ok"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
