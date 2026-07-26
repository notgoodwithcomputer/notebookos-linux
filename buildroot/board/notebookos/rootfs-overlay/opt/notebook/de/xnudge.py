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

xid = int(sys.argv[1], 0)
w, h = int(sys.argv[2]), int(sys.argv[3])

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
    x.XResizeWindow(d, xid, w, h - 1)
    x.XFlush(d)
