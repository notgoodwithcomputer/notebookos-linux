#!/usr/bin/env python3
"""Display-free ownership checks for AppWindow's shared chrome timer."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
import nbapp  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class UntouchableLabel:
    def set_text(self, _text):
        raise AssertionError("closed clock touched a destroyed label")


win = nbapp.AppWindow.__new__(nbapp.AppWindow)
win._base_closed = False
win._clock_source_id = 417
win._clock = win._date = UntouchableLabel()
win._clock_txt = win._date_txt = None

removed = []
unregistered = []
real_remove = nbapp.GLib.source_remove
real_unregister = nbapp._unregister_app
nbapp.GLib.source_remove = lambda source_id: removed.append(source_id) or True
nbapp._unregister_app = lambda: unregistered.append(True)
try:
    first = win._on_base_destroy()
    second = win._on_base_destroy()
finally:
    nbapp.GLib.source_remove = real_remove
    nbapp._unregister_app = real_unregister

check(first is False and second is False and win._base_closed,
      "base destroy is idempotent and closes the timer gate")
check(removed == [417] and win._clock_source_id == 0,
      "base destroy removes exactly its owned clock source once")
check(unregistered == [True],
      "base destroy unregisters the application exactly once")
check(win._tick() is False,
      "a dispatched clock tick after destroy stops without touching labels")

source = open(os.path.join(DE, "nbapp.py"), encoding="utf-8").read()
check("self._clock_source_id = GLib.timeout_add_seconds(1, self._tick)" in source,
      "the repeating clock source ID is retained when scheduled")

print("%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
raise SystemExit(1 if failures else 0)
