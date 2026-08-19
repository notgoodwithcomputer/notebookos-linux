#!/usr/bin/env python3
"""Display-free colour-boundary checks for the desktop backdrop."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import desktopbg  # noqa: E402


def rgb(value):
    colour = desktopbg._rgba(value)
    return tuple(round(v * 255) for v in
                 (colour.red, colour.green, colour.blue))


default = rgb(desktopbg.DEFAULT_COLOR)
checks = [
    (rgb("#123AbC") == (0x12, 0x3A, 0xBC),
     "a canonical #RRGGBB startup colour is honoured"),
    (rgb("transparent") == default,
     "transparent cannot turn the opaque backdrop black"),
    (rgb("black") == default,
     "a named colour is rejected by the documented #RRGGBB boundary"),
    (rgb("#1234") == default,
     "a malformed hex colour falls back to Papertone"),
]


class DestroyedWindow:
    def get_window(self):
        raise RuntimeError("already destroyed")


checks.append((desktopbg._lower_once(DestroyedWindow()) is False,
               "a queued lower callback safely expires after window teardown"))
quit_calls = []
real_quit = desktopbg.Gtk.main_quit
desktopbg.Gtk.main_quit = lambda: quit_calls.append(True)
try:
    destroy_result = desktopbg._window_destroyed()
finally:
    desktopbg.Gtk.main_quit = real_quit
checks.append((destroy_result is False and quit_calls == [True],
               "destroying the sole backdrop window quits its process loop"))

failed = 0
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
    failed += not ok
print("TALLY: %d/%d passed" % (len(checks) - failed, len(checks)))
print("RESULT: %s" % ("FAILED" if failed else "PASS"))
raise SystemExit(1 if failed else 0)
