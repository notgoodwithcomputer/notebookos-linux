#!/usr/bin/env python3
"""
Prototype: a full-screen DOCK window that is X-shaped down to just its top
bar, with a dropdown menu drawn INSIDE the same window (so it paints on the
one surface matchbox reliably renders). Proves the overlay-menu technique
before baking it into shell.py.

Run in-guest:  DISPLAY=:0 python3 shape-proto.py
It opens with the menu already down so a screendump shows both the bar and
the menu, with the desktop visible through the shaped-out area.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402
import cairo

PANEL_H = 46

CSS = b"""
.menubar { background: #F4F2EC; border-bottom: 1px solid #C9C4B6; }
.menubar, .menubar * { color: #1A1916; }
.menuitem { padding: 2px 8px; font-size: 15px; }
.sysmenu { background: #F8F7F2; border: 1px solid #1A1916; padding: 4px 0; }
.sysmenu-item { padding: 6px 28px 6px 16px; font-size: 14.5px;
                background: transparent; border: none; box-shadow: none;
                min-width: 190px; color: #1A1916; }
.sysmenu-item:hover { background: #1A1916; color: #F4F2EC; }
"""


class Panel(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)
        self.set_app_paintable(True)

        self.screen_w, self.screen_h = 1920, 1080
        self.set_default_size(self.screen_w, self.screen_h)
        self.move(0, 0)

        self._menu = None
        self._menu_rect = None

        self.fixed = Gtk.Fixed()
        self.add(self.fixed)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("menubar")
        bar.set_size_request(self.screen_w, PANEL_H)
        left = Gtk.Box(); left.set_margin_start(16)
        self.btn = Gtk.Button(label="Finder")
        self.btn.set_relief(Gtk.ReliefStyle.NONE)
        self.btn.get_style_context().add_class("menuitem")
        self.btn.connect("clicked", self._toggle)
        left.pack_start(self.btn, False, False, 0)
        bar.pack_start(left, False, False, 0)
        self.fixed.put(bar, 0, 0)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._maybe_dismiss)
        self.connect("realize", self._on_realize)

    def _on_realize(self, *_):
        self._apply_shape()

    def _apply_shape(self):
        win = self.get_window()
        if win is None:
            return
        region = cairo.Region(cairo.RectangleInt(0, 0, self.screen_w, PANEL_H))
        if self._menu_rect:
            region.union(cairo.RectangleInt(*self._menu_rect))
        # bounding shape: only the bar (+ menu) is drawn; desktop shows through
        win.shape_combine_region(region, 0, 0)
        # input shape: when a menu is open, capture the WHOLE screen so an
        # outside click dismisses it; otherwise only the bar takes input so
        # clicks pass through to the desktop/apps below.
        if self._menu_rect:
            full = cairo.Region(cairo.RectangleInt(0, 0, self.screen_w,
                                                   self.screen_h))
            win.input_shape_combine_region(full, 0, 0)
        else:
            win.input_shape_combine_region(region, 0, 0)

    def _toggle(self, *_):
        if self._menu is not None:
            self._close()
        else:
            self._open()

    def _open(self):
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        menu.get_style_context().add_class("sysmenu")
        for label in ("Finder", "Writer", "Novel", "Calculator", "Tetris"):
            it = Gtk.Button(label=label)
            it.set_relief(Gtk.ReliefStyle.NONE)
            it.get_style_context().add_class("sysmenu-item")
            it.get_child().set_xalign(0.0)
            it.connect("clicked", lambda _w: self._close())
            menu.pack_start(it, False, False, 0)
        bx, _by = self.btn.translate_coordinates(self.fixed, 0, 0)
        self.fixed.put(menu, bx, PANEL_H)
        menu.show_all()
        _min, nat = menu.get_preferred_size()
        self._menu = menu
        self._menu_rect = (bx, PANEL_H, nat.width, nat.height)
        self._apply_shape()
        menu.connect("size-allocate", self._menu_allocated)

    def _menu_allocated(self, _w, alloc):
        r = (alloc.x, alloc.y, alloc.width, alloc.height)
        if r != self._menu_rect:
            self._menu_rect = r
            self._apply_shape()

    def _close(self):
        if self._menu is not None:
            self.fixed.remove(self._menu)
            self._menu = None
            self._menu_rect = None
            self._apply_shape()

    def _maybe_dismiss(self, _w, ev):
        if self._menu_rect is None:
            return False
        mx, my, mw, mh = self._menu_rect
        if not (mx <= ev.x <= mx + mw and my <= ev.y <= my + mh):
            # click outside the menu and not on the bar toggle -> dismiss
            if ev.y > PANEL_H:
                self._close()
        return False


def main():
    prov = Gtk.CssProvider(); prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    p = Panel()
    p.show_all()
    GLib.timeout_add(800, lambda: (p._open(), False)[1])   # auto-open for shot
    GLib.timeout_add_seconds(40, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
