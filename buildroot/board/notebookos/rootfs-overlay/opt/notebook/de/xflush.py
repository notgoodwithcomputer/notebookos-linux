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

d = Gdk.Display.get_default()
if d is not None:
    ptr = d.get_default_seat().get_pointer()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    for _ in range(n):
        screen, x, y = ptr.get_position()
        ptr.warp(screen, x, y)
        d.flush()
        d.sync()
        time.sleep(0.2)
