#!/usr/bin/env python3
"""
xshape.py — set an X11 window's bounding / input shape from a list of
rectangles, via ctypes to the XShape extension.

Why ctypes and not Gdk.Window.shape_combine_region(): this Buildroot's
PyGObject is built -Dpycairo=disabled, so the gi<->pycairo bridge is absent
and passing a cairo.Region to any gi method fails with
    KeyError: 'could not find foreign type Region'
ctypes talks to libXext directly and sidesteps gi entirely. Shape is
server-side window state, so setting it from our own X connection (a second
XOpenDisplay) applies to the GTK-owned window all the same.
"""
import ctypes
from ctypes import (c_int, c_uint, c_ulong, c_short, c_ushort, c_void_p,
                    c_char_p, Structure, POINTER)

_x11 = ctypes.CDLL("libX11.so.6")
_xext = ctypes.CDLL("libXext.so.6")


class _XRectangle(Structure):
    _fields_ = [("x", c_short), ("y", c_short),
                ("width", c_ushort), ("height", c_ushort)]


_x11.XOpenDisplay.restype = c_void_p
_x11.XOpenDisplay.argtypes = [c_char_p]
_x11.XFlush.argtypes = [c_void_p]
_x11.XResizeWindow.argtypes = [c_void_p, c_ulong, c_uint, c_uint]
_xext.XShapeCombineRectangles.argtypes = [
    c_void_p, c_ulong, c_int, c_int, c_int,
    POINTER(_XRectangle), c_int, c_int, c_int]

SHAPE_BOUNDING = 0     # which pixels are drawn on screen
SHAPE_INPUT = 2        # which pixels accept pointer input
_SHAPE_SET = 0         # replace the existing shape
_UNSORTED = 0

_dpy = None
_SHORT_MIN, _SHORT_MAX = -32768, 32767
_USHORT_MAX = 65535


def _display():
    global _dpy
    if _dpy is None:
        _dpy = _x11.XOpenDisplay(None)     # honours $DISPLAY
    return _dpy


def validated_rects(rects):
    """XRectangle-safe integer tuples, or None if any rectangle is unsafe."""
    if rects is None:
        return None
    out = []
    try:
        for rect in rects:
            x, y, width, height = (int(value) for value in rect)
            if (not _SHORT_MIN <= x <= _SHORT_MAX
                    or not _SHORT_MIN <= y <= _SHORT_MAX
                    or not 1 <= width <= _USHORT_MAX
                    or not 1 <= height <= _USHORT_MAX):
                return None
            out.append((x, y, width, height))
    except (TypeError, ValueError, OverflowError):
        return None
    return out


def combine(xid, rects, kind):
    """Set the `kind` shape of window `xid` to the union of `rects`
    (each (x, y, w, h)). Replaces any previous shape of that kind."""
    values = validated_rects(rects)
    if not xid or values is None:
        return
    dpy = _display()
    if not dpy:
        return
    # An empty rectangle list is meaningful to XShape: replacing the input
    # shape with it makes a window fully click-through.  Pass NULL/count=0
    # rather than treating it as a no-op.
    arr = None if not values else (_XRectangle * len(values))(
        *[_XRectangle(int(x), int(y), int(w), int(h))
          for (x, y, w, h) in values])
    _xext.XShapeCombineRectangles(dpy, xid, kind, 0, 0, arr, len(values),
                                  _SHAPE_SET, _UNSORTED)
    _x11.XFlush(dpy)


def resize(xid, width, height):
    """Resize window `xid`. Used purely to force a ConfigureNotify (and thus
    a fresh expose/redraw) — GTK otherwise never repaints below the bar."""
    dpy = _display()
    if not dpy or not xid:
        return
    _x11.XResizeWindow(dpy, xid, int(width), int(height))
    _x11.XFlush(dpy)
