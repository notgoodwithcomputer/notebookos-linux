#!/usr/bin/env python3
"""xflush.py — force freshly-mapped windows' first paint onto the screen.

On the software (swrast) + virtio-gpu / modesetting stack there is no real
vblank, so the driver only flushes a new window's initial paint to the scanout
framebuffer when the X server's block handler runs — which happens when the
event loop is woken by input. A window otherwise stays blank until the user
happens to move the mouse.

A pointer warp to the CURRENT position emits a MotionNotify (which wakes the
block handler and runs the shadow-fb flush) WITHOUT moving the cursor;
flush()+sync() force it through. Done from a THROWAWAY process on purpose — the
same warp issued from a window's own busy main loop is treated inconsistently
and usually skips. Retried a few times because a slow (TCG) map settles late.

  xflush.py [count]      # count = number of nudges (default 6)
"""
import gi
import sys
import time
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402


DEFAULT_NUDGES = 6
MAX_NUDGES = 30


def nudge_count(value=None):
    """A safe retry count for the short-lived redraw helper.

    A bad launcher/config value must not crash before the first nudge, and an
    absurd value must not leave hundreds of sleeping helpers behind. Explicit
    zero remains useful when diagnosing the software-rendering workaround.
    """
    if value is None:
        return DEFAULT_NUDGES
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_NUDGES
    return max(0, min(count, MAX_NUDGES))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    d = Gdk.Display.get_default()
    if d is None:
        return 0
    # During X startup (and briefly while an input device is hotplugged) GDK
    # may have opened the display before it has a default seat/pointer.  This
    # is a best-effort paint wakeup, so that transient state must be a no-op,
    # not an AttributeError from a helper launched for every new window.
    seat = d.get_default_seat()
    if seat is None:
        return 0
    ptr = seat.get_pointer()
    if ptr is None:
        return 0
    n = nudge_count(argv[0] if argv else None)
    for _ in range(n):
        try:
            screen, x, y = ptr.get_position()
            if screen is None:
                break
            ptr.warp(screen, x, y)
            d.flush()
            d.sync()
        except Exception:                                        # noqa: BLE001
            # The pointer and display are live objects, not snapshots. Input
            # hot-unplug or an X restart can invalidate either between retry
            # ticks; the helper has no recovery work beyond stopping cleanly.
            break
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
