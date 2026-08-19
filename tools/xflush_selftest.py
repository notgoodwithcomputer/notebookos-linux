#!/usr/bin/env python3
"""Display-free checks for xflush retry-count input handling."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import xflush  # noqa: E402


class _DisplayStage:
    def __init__(self, seat):
        self._seat = seat

    def get_default_seat(self):
        return self._seat


class _SeatStage:
    def get_pointer(self):
        return None


class _VanishingPointer:
    def get_position(self):
        raise RuntimeError("input device vanished")


class _PointerSeat:
    def get_pointer(self):
        return _VanishingPointer()


real_gdk = xflush.Gdk
try:
    xflush.Gdk = type("FakeGdk", (), {
        "Display": type("Display", (), {
            "get_default": staticmethod(lambda: _DisplayStage(None)),
        }),
    })
    missing_seat_ok = xflush.main(["1"]) == 0
    xflush.Gdk.Display.get_default = staticmethod(
        lambda: _DisplayStage(_SeatStage()))
    missing_pointer_ok = xflush.main(["1"]) == 0
    xflush.Gdk.Display.get_default = staticmethod(
        lambda: _DisplayStage(_PointerSeat()))
    vanished_pointer_ok = xflush.main(["1"]) == 0
finally:
    xflush.Gdk = real_gdk


checks = {
    "missing count keeps the working default":
        xflush.nudge_count() == xflush.DEFAULT_NUDGES,
    "malformed count falls back instead of aborting redraw":
        xflush.nudge_count("not-a-number") == xflush.DEFAULT_NUDGES,
    "negative count cannot create a nonsensical retry loop":
        xflush.nudge_count(-5) == 0,
    "explicit zero remains available for diagnosis":
        xflush.nudge_count("0") == 0,
    "ordinary count is unchanged": xflush.nudge_count("9") == 9,
    "absurd count is bounded":
        xflush.nudge_count("999999") == xflush.MAX_NUDGES,
    "a display with no input seat exits cleanly": missing_seat_ok,
    "a seat with no pointer exits cleanly": missing_pointer_ok,
    "a pointer lost between retry ticks exits cleanly": vanished_pointer_ok,
}

failed = []
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    if not passed:
        failed.append(name)
print("RESULT: %s" % ("ALL PASS" if not failed else "%d FAILED" % len(failed)))
raise SystemExit(1 if failed else 0)
