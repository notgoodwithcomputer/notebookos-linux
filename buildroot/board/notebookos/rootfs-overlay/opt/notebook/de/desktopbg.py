#!/usr/bin/env python3
"""
desktopbg.py — the desktop backdrop, as a real window.

  desktopbg.py "#DED4C2"

Why a window and not the X root
-------------------------------
Painting the root (xsetroot -solid, or a root pixmap + _XROOTPMAP_ID) works
only when nothing is compositing. Once a compositor owns the screen the root is
no longer what you see, and each compositor invents its own answer for what
sits behind the windows:

    xcompmgr : a flat GREY tile   (measured #808080)
    picom    : BLACK              (measured #000000)

Neither is the papertone field, and no amount of setting the root colour
changes it — which is why the desktop "went grey" the moment the compositor
started, and black once the compositor changed.

A full-screen window with _NET_WM_WINDOW_TYPE_DESKTOP is composited like any
other window, so the backdrop is simply *there*, identically with a compositor,
without one, and whichever compositor is running. This is how desktop
environments have always drawn wallpaper.

It is kept below everything, takes no focus, stays off the taskbar/pager, and
does not accept input — clicking the desktop must not raise it above real
windows.
"""
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

DEFAULT_COLOR = "#DED4C2"


def _rgba(color):
    """Parse '#RRGGBB' into a Gdk.RGBA, falling back to the papertone field so a
    malformed value never leaves the desktop unpainted."""
    rgba = Gdk.RGBA()
    if not (isinstance(color, str) and rgba.parse(color)):
        rgba.parse(DEFAULT_COLOR)
    return rgba


class Backdrop(Gtk.Window):
    def __init__(self, color=DEFAULT_COLOR):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._rgba = _rgba(color)

        # A desktop-type window: the WM stacks it at the very bottom and leaves
        # it out of the task list and alt-tab.
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_keep_below(True)
        self.set_app_paintable(True)
        # opaque visual: the backdrop must be solid paper, never an
        # alpha hole the compositor fills with black (see nbapp).
        try:
            vis = self.get_screen().get_system_visual()
            if vis is not None:
                self.set_visual(vis)
        except Exception:
            pass

        # Cover the whole screen, and follow it if the mode changes.
        screen = Gdk.Screen.get_default()
        self._fit(screen)
        try:
            screen.connect("size-changed", lambda *_: self._fit(screen))
            screen.connect("monitors-changed", lambda *_: self._fit(screen))
        except Exception:
            pass

        self.connect("draw", self._on_draw)
        self.connect("map-event", self._on_map)

    def _fit(self, screen):
        try:
            w = screen.get_width()
            h = screen.get_height()
        except Exception:
            w, h = 1920, 1080
        if w > 0 and h > 0:
            self.move(0, 0)
            self.resize(w, h)
            self.set_default_size(w, h)

    def _on_draw(self, _widget, cr):
        c = self._rgba
        cr.set_source_rgb(c.red, c.green, c.blue)
        cr.paint()
        return False

    def _on_map(self, *_a):
        # Re-assert the bottom of the stack once mapped: matchbox honours
        # keep-below on request, and a later-mapped window must not end up
        # underneath the backdrop.
        try:
            gw = self.get_window()
            if gw is not None:
                gw.lower()
        except Exception:
            pass
        # Take no pointer input at all, so a click on the desktop reaches
        # whatever is really there rather than this sheet. An EMPTY input shape
        # is the way to say that. It goes through xshape (ctypes) because this
        # PyGObject is built without the pycairo bridge, so the Gdk region call
        # that would normally do this is unavailable — see xshape.py.
        try:
            import xshape
            gw = self.get_window()
            if gw is not None:
                xshape.combine(gw.get_xid(), [], xshape.SHAPE_INPUT)
        except Exception:
            pass
        return False

    def set_color(self, color):
        self._rgba = _rgba(color)
        self.queue_draw()


def main():
    color = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_COLOR
    win = Backdrop(color)
    win.show_all()
    try:
        gw = win.get_window()
        if gw is not None:
            gw.lower()
    except Exception:
        pass
    # A repeated lower for the first few seconds: apps launched during startup
    # map after us, and on this WM the stacking settles a beat later.
    GLib.timeout_add(1500, lambda: (win.get_window() and win.get_window().lower(),
                                    False)[1])
    Gtk.main()


if __name__ == "__main__":
    main()
