#!/usr/bin/env python3
"""
xrootbg.py — paint the desktop backdrop in a way a COMPOSITOR can see.

  xrootbg.py "#DED4C2"

Why this exists
---------------
`xsetroot -solid` only sets the root window's background *pixel*. With no
compositor the X server fills the root with it and the desktop looks right —
which is why this was never needed before.

Under a compositing manager the root window is not what you see: xcompmgr
paints its own "root tile" beneath every redirected window, and it builds that
tile from the _XROOTPMAP_ID (or ESETROOT_PMAP_ID) property. When neither
property exists it falls back to a flat grey — the "desktop is grey" symptom,
which appears the moment the compositor starts and has nothing to do with the
colour xsetroot was asked for.

So: create a real pixmap, fill it with the backdrop colour, hand it to the root
window AND publish it as _XROOTPMAP_ID/ESETROOT_PMAP_ID so the compositor has
something to composite. The pixmap is retained after this process exits
(RetainPermanent) — otherwise the server frees it and the compositor is back to
grey the moment we quit.

ctypes rather than Gdk for the same reason as xshape.py: this Buildroot's
PyGObject is built without the pycairo bridge, so the Gdk paths that would do
this are unavailable. See xshape.py.
"""
import ctypes
import sys
from ctypes import c_char_p, c_int, c_long, c_uint, c_ulong, c_void_p

_x11 = ctypes.CDLL("libX11.so.6")

_x11.XOpenDisplay.restype = c_void_p
_x11.XOpenDisplay.argtypes = [c_char_p]
_x11.XDefaultRootWindow.restype = c_ulong
_x11.XDefaultRootWindow.argtypes = [c_void_p]
_x11.XDefaultScreen.restype = c_int
_x11.XDefaultScreen.argtypes = [c_void_p]
_x11.XDefaultDepth.restype = c_int
_x11.XDefaultDepth.argtypes = [c_void_p, c_int]
_x11.XCreatePixmap.restype = c_ulong
_x11.XCreatePixmap.argtypes = [c_void_p, c_ulong, c_uint, c_uint, c_uint]
_x11.XCreateGC.restype = c_void_p
_x11.XCreateGC.argtypes = [c_void_p, c_ulong, c_ulong, c_void_p]
_x11.XSetForeground.argtypes = [c_void_p, c_void_p, c_ulong]
_x11.XFillRectangle.argtypes = [c_void_p, c_ulong, c_void_p,
                                c_int, c_int, c_uint, c_uint]
_x11.XFreeGC.argtypes = [c_void_p, c_void_p]
_x11.XSetWindowBackgroundPixmap.argtypes = [c_void_p, c_ulong, c_ulong]
_x11.XClearWindow.argtypes = [c_void_p, c_ulong]
_x11.XInternAtom.restype = c_ulong
_x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
_x11.XChangeProperty.argtypes = [c_void_p, c_ulong, c_ulong, c_ulong, c_int,
                                 c_int, c_void_p, c_int]
_x11.XGetWindowProperty.argtypes = [c_void_p, c_ulong, c_ulong, c_long, c_long,
                                    c_int, c_ulong, c_void_p, c_void_p,
                                    c_void_p, c_void_p, c_void_p]
_x11.XFree.argtypes = [c_void_p]
_x11.XKillClient.argtypes = [c_void_p, c_ulong]
_x11.XSetCloseDownMode.argtypes = [c_void_p, c_int]
_x11.XFlush.argtypes = [c_void_p]
_x11.XSync.argtypes = [c_void_p, c_int]
_x11.XCloseDisplay.argtypes = [c_void_p]

XA_PIXMAP = 20                 # X.h predefined atom
PROP_MODE_REPLACE = 0
RETAIN_PERMANENT = 1           # X.h CloseDownMode


def _published_pixmap(dpy, root, atom):
    """Pixmap currently advertised on root, or 0 if absent/malformed."""
    actual, fmt, count, after = c_ulong(), c_int(), c_ulong(), c_ulong()
    data = c_void_p()
    try:
        status = _x11.XGetWindowProperty(
            dpy, root, atom, 0, 1, False, XA_PIXMAP,
            ctypes.byref(actual), ctypes.byref(fmt), ctypes.byref(count),
            ctypes.byref(after), ctypes.byref(data))
        if (status == 0 and actual.value == XA_PIXMAP and fmt.value == 32 and
                count.value == 1 and data.value):
            return ctypes.cast(data, ctypes.POINTER(c_ulong))[0]
        return 0
    finally:
        if data.value:
            _x11.XFree(data)


def _pixel(color):
    """'#RRGGBB' -> a 24-bit pixel value. Any malformed value falls back to the
    papertone field rather than leaving the desktop unpainted."""
    try:
        s = color.lstrip("#")
        if len(s) == 3:                     # #abc -> #aabbcc
            s = "".join(c * 2 for c in s)
        if len(s) == 8:                     # #rrggbbaa -> drop alpha
            s = s[:6]
        if len(s) != 6:
            raise ValueError(color)
        return int(s, 16)
    except Exception:
        return 0xDED4C2


def set_root_background(color="#DED4C2"):
    """Fill the desktop with `color` and publish it for the compositor.
    Returns True on success; never raises (a failure just leaves the backdrop
    as it was — the session must come up regardless)."""
    dpy = None
    try:
        dpy = _x11.XOpenDisplay(None)
        if not dpy:
            return False
        root = _x11.XDefaultRootWindow(dpy)
        screen = _x11.XDefaultScreen(dpy)
        depth = _x11.XDefaultDepth(dpy, screen)

        root_atom = _x11.XInternAtom(dpy, b"_XROOTPMAP_ID", False)
        eset_atom = _x11.XInternAtom(dpy, b"ESETROOT_PMAP_ID", False)
        old_root = _published_pixmap(dpy, root, root_atom) if root_atom else 0
        old_eset = _published_pixmap(dpy, root, eset_atom) if eset_atom else 0
        # Matching properties are Esetroot's ownership marker. Do not kill a
        # live client that happened to publish only one root-pixmap property.
        old_pix = old_root if old_root and old_root == old_eset else 0

        # A 1x1 tile is all a solid colour needs; the server repeats it.
        pix = _x11.XCreatePixmap(dpy, root, 1, 1, depth)
        if not pix:
            return False
        gc = _x11.XCreateGC(dpy, pix, 0, None)
        if not gc:
            # Xlib's drawing calls expect a real GC pointer; passing NULL can
            # crash this helper rather than reporting resource exhaustion.
            return False
        _x11.XSetForeground(dpy, gc, _pixel(color))
        _x11.XFillRectangle(dpy, pix, gc, 0, 0, 1, 1)
        _x11.XFreeGC(dpy, gc)

        _x11.XSetWindowBackgroundPixmap(dpy, root, pix)
        _x11.XClearWindow(dpy, root)

        # What the compositor actually reads. Both names are published:
        # xcompmgr checks _XROOTPMAP_ID, other tools use the Esetroot name.
        pid = ctypes.c_ulong(pix)
        for name in (b"_XROOTPMAP_ID", b"ESETROOT_PMAP_ID"):
            atom = _x11.XInternAtom(dpy, name, False)
            if atom:
                _x11.XChangeProperty(dpy, root, atom, XA_PIXMAP, 32,
                                     PROP_MODE_REPLACE,
                                     ctypes.byref(pid), 1)

        # Keep the pixmap alive after this process exits; without it the server
        # frees the pixmap, the property dangles and the compositor greys out.
        _x11.XSetCloseDownMode(dpy, RETAIN_PERMANENT)
        _x11.XSync(dpy, False)
        # The former helper has disconnected in RetainPermanent mode. Killing
        # the client owning its published resource releases that old pixmap
        # (and the retained client record) only after our replacement is live.
        if old_pix and old_pix != pix:
            _x11.XKillClient(dpy, old_pix)
            _x11.XSync(dpy, False)
        return True
    except Exception:
        return False
    finally:
        if dpy:
            try:
                _x11.XFlush(dpy)
                # RetainPermanent takes effect when the client disconnects.
                # Closing explicitly also matters when this helper is imported
                # by a longer-lived process instead of run as a one-shot CLI.
                _x11.XCloseDisplay(dpy)
            except Exception:
                pass


if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "#DED4C2"
    ok = set_root_background(col)
    sys.exit(0 if ok else 1)
