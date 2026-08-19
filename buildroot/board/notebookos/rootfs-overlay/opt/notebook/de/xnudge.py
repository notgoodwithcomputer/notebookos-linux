#!/usr/bin/env python3
"""
xnudge.py — resize a window by one pixel and back, from a throwaway process.

Used by the panel to force GTK to repaint the below-bar region when a dropdown
menu opens (see shell.py _paint_below_bar). Doing the resize from a SEPARATE
process is what makes it reliable: GDK then handles the resulting
ConfigureNotify as a foreign event and does a full redraw, where the same
resize issued from the panel's own process is treated inconsistently and often
skips the repaint.

  xnudge.py <xid> <width> <height>
"""
import ctypes
import sys
import time
from ctypes import c_uint, c_ulong, c_void_p, c_char_p

MAX_X_DIMENSION = 65535       # CARD16 in the ConfigureWindow request
MAX_XID = 0xFFFFFFFF          # CARD32 on the X11 wire, even on 64-bit hosts


def validated_args(argv):
    try:
        xid = int(argv[1], 0)
        width, height = int(argv[2]), int(argv[3])
    except (IndexError, TypeError, ValueError):
        return None
    # XResizeWindow takes unsigned dimensions. A stale zero allocation would
    # turn height-1 into 4294967295 and trigger a fatal asynchronous X error.
    if (xid <= 0 or xid > MAX_XID or width < 2 or height < 2
            or width > MAX_X_DIMENSION or height > MAX_X_DIMENSION):
        return None
    return xid, width, height


def main(argv=None):
    parsed = validated_args(sys.argv if argv is None else argv)
    if parsed is None:
        return 0
    xid, width, height = parsed
    x = ctypes.CDLL("libX11.so.6")
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    x.XResizeWindow.argtypes = [c_void_p, c_ulong, c_uint, c_uint]
    x.XFlush.argtypes = [c_void_p]
    d = x.XOpenDisplay(None)
    if d:
        # single shrink; the panel's set_size_request bounces it back to full
        # height. This is the exact resize that reliably repainted the menu when
        # issued from a separate process.
        x.XResizeWindow(d, xid, width, height - 1)
        x.XFlush(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
