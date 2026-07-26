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


def _display():
    global _dpy
    if _dpy is None:
        _dpy = _x11.XOpenDisplay(None)     # honours $DISPLAY
    return _dpy


def combine(xid, rects, kind):
    """Set the `kind` shape of window `xid` to the union of `rects`
    (each (x, y, w, h)). Replaces any previous shape of that kind."""
    dpy = _display()
    if not dpy or not xid or not rects:
        return
    arr = (_XRectangle * len(rects))(
        *[_XRectangle(int(x), int(y), int(w), int(h))
          for (x, y, w, h) in rects])
    _xext.XShapeCombineRectangles(dpy, xid, kind, 0, 0, arr, len(rects),
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
