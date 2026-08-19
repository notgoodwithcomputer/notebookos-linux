#!/usr/bin/env python3
"""
Notebook OS boot splash — the loading screen shown while the desktop session
comes up.

A standalone, undecorated, full-screen GTK3 window that paints the papertone
desktop field (#DED4C2 — the same tone session.sh sets as the root backdrop and
the desktop uses, so the whole boot reads as one continuous paper surface), the
snail brand mark, the name "Notebook OS" in the editorial serif, and a filling
progress bar. It is launched first by session.sh (kept above everything) while
the Finder, widget column and shell start underneath; it dismisses itself the
moment the shell signals readiness via the flag /tmp/nb-ready (written when the
panel maps).

The bar eases up toward ~90% on its own so there is always motion, then snaps to
100% and closes once the desktop is actually up. A hard timeout guarantees the
splash can never trap the session behind it.

Design language: papertone field, ink text, the serif wordmark, and the signage
red used only for the active "loading" state (the progress fill) and the snail
brand mark. No decorative colour, no marketing copy — a calm, honest boot.
"""
import os
import math
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf  # noqa: E402

try:
    import nbmotion  # noqa: E402
except Exception:    # motion is optional; boot handover is not
    nbmotion = None

# The splash is the FIRST thing anyone reads on this computer, so it has to be
# in their language too. nbi18n is a plain JSON read with no GTK dependency, but
# the splash must come up even if the desktop tree is damaged — so a failed
# import degrades to English rather than leaving the machine with no boot
# screen at all.
try:
    from nbi18n import _t  # noqa: E402
except Exception:          # pragma: no cover - defensive, see above
    def _t(s):
        return s

READY_FLAG = "/tmp/nb-ready"          # shell writes this once the panel maps
LOGO = "/opt/notebook/logo.png"       # the snail brand mark

LOGO_H = 132                          # snail height (px); scaled, aspect kept
BAR_W = 300                           # progress-bar width  (px)
BAR_H = 4                             # progress-bar height (px)

BAR_TICK_MS = 70                      # ease the bar upward this often
POLL_MS = 150                         # poll for /tmp/nb-ready this often
GRACE_MS = 180                        # linger after 100% so the full bar shows
MAX_MS = 30000                        # failsafe: never hang behind the splash

# Papertone surfaces + ink text; signage red (#C8341E) reserved for the active
# loading fill. Serif (Liberation Serif, aliased from Newsreader) for the large
# wordmark; Nimbus Sans for the small technical status label.
CSS = b"""
.splash      { background-color: #DED4C2; }
.splash-name { font-family: "Newsreader","Liberation Serif","Georgia",serif;
               font-size: 40px; font-weight: 600; color: #1A1916; }
.splash-sub  { font-family: "Nimbus Sans","Helvetica",sans-serif;
               font-size: 11px; font-weight: 600; color: #6E695E;
               letter-spacing: 0.20em; }
"""


def _rounded_rect(cr, x, y, w, h, r):
    """A rounded-rectangle path (r clamped so it never exceeds the box)."""
    r = min(r, w / 2.0, h / 2.0)
    if r <= 0:
        cr.rectangle(x, y, w, h)
        return
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class Splash(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)
        self.set_keep_above(True)
        # the splash needs no keyboard input; not taking focus keeps matchbox
        # from treating it as the focused client and starving the desktop parts
        # that start beneath it.
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.get_style_context().add_class("splash")

        # cover the whole screen even if the WM ignores fullscreen()
        scr = Gdk.Screen.get_default()
        if scr is not None:
            self.set_default_size(scr.get_width(), scr.get_height())
        self.fullscreen()
        # matchbox only honours post-map EWMH requests, so re-assert on map.
        self.connect("map-event", self._on_map)

        self._fraction = 0.0          # current bar fill 0..1
        self._done = False            # finish() has run (bar full, closing)

        # centered column: logo, name, subtitle, progress bar
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        self.add(content)

        img = Gtk.Image()
        try:
            # scale the snail preserving aspect (-1 width); a standard Gtk.Image
            # renders C-side, so it paints reliably regardless of the compositor.
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(LOGO, -1, LOGO_H, True)
            img.set_from_pixbuf(pb)
        except Exception:
            # logo missing/unreadable: fall back to just the wordmark + bar.
            pass
        img.set_halign(Gtk.Align.CENTER)
        img.set_margin_bottom(6)
        content.pack_start(img, False, False, 0)

        name = Gtk.Label(label=_t("Notebook OS"))
        name.get_style_context().add_class("splash-name")
        content.pack_start(name, False, False, 0)

        # "Desktop session" is what the machine calls this to itself. What is
        # happening, to the person watching, is that the computer is starting.
        sub = Gtk.Label(label=_t("STARTING UP"))
        sub.get_style_context().add_class("splash-sub")
        content.pack_start(sub, False, False, 0)

        self.bar = Gtk.DrawingArea()
        self.bar.set_size_request(BAR_W, BAR_H)
        self.bar.set_halign(Gtk.Align.CENTER)
        self.bar.set_margin_top(30)
        self.bar.connect("draw", self._draw_bar)
        content.pack_start(self.bar, False, False, 0)

        # the three drivers: ease the bar, poll for readiness, hard failsafe.
        GLib.timeout_add(BAR_TICK_MS, self._tick_bar)
        GLib.timeout_add(POLL_MS, self._poll_ready)
        GLib.timeout_add(MAX_MS, self._failsafe)

    def _on_map(self, *_a):
        # re-assert coverage + stacking; matchbox honours these only post-map.
        try:
            self.fullscreen()
            self.set_keep_above(True)
        except Exception:
            pass
        return False

    # ---- the bar ----
    def _draw_bar(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        r = h / 2.0
        # no-compositor black-safety: fill the whole area with the OPAQUE
        # papertone field first, so the DrawingArea's own window never shows
        # through as black on the software-rendered / non-composited stack.
        cr.set_source_rgb(222 / 255.0, 212 / 255.0, 194 / 255.0)  # #DED4C2
        cr.paint()
        # trough: a faint ink groove on the papertone field
        _rounded_rect(cr, 0, 0, w, h, r)
        cr.set_source_rgba(26 / 255.0, 25 / 255.0, 22 / 255.0, 0.12)
        cr.fill()
        # fill: signage red — the active "loading in progress" state
        fw = w * self._fraction
        if fw > 0.5:
            _rounded_rect(cr, 0, 0, fw, h, r)
            cr.set_source_rgb(200 / 255.0, 52 / 255.0, 30 / 255.0)  # #C8341E
            cr.fill()
        return False

    def _tick_bar(self):
        if self._done:
            return False
        # ease asymptotically toward 0.9 (plus a small floor so it always
        # creeps) — deliberately never reaches 1.0 on its own.
        prev = self._fraction
        self._fraction += (0.9 - self._fraction) * 0.08 + 0.003
        if self._fraction > 0.9:
            self._fraction = 0.9
        # Only repaint when the fill actually advanced. Once it caps at 0.9 the
        # bar is pixel-identical every tick, so skip the redundant 70ms redraw
        # of the DrawingArea; finish() fills it to 100% when the desktop is up.
        if self._fraction != prev:
            self.bar.queue_draw()
            return True
        # Capped: stop the timer outright rather than waking every 70ms to
        # decide there is nothing to do. This runs on a CPU-rendered machine
        # during the exact seconds the whole desktop is starting underneath it,
        # and it can sit at the cap for the rest of the 30-second failsafe.
        # _finish() paints the full bar directly, so nothing needs this timer.
        return False

    # ---- dismissal ----
    def _poll_ready(self):
        if self._done:
            return False
        try:
            ready = os.path.exists(READY_FLAG)
        except OSError:
            ready = False
        if ready:
            self._finish()
            return False
        return True

    def _finish(self):
        # the desktop is up (or the failsafe fired): fill the bar and close
        # after a short grace so the full bar is briefly visible.
        if self._done:
            return
        self._done = True
        self._fraction = 1.0
        self.bar.queue_draw()
        # Arm the handover before attempting any motion.  The lift gets only
        # the grace period that already existed; it can never extend boot.
        GLib.timeout_add(GRACE_MS, Gtk.main_quit)
        try:
            if nbmotion is None:
                raise RuntimeError("nbmotion unavailable")
            start_x, start_y = self.get_position()

            def _lift(value):
                # Move the already-painted toplevel: no relayout and no
                # full-screen opacity repaint on the software renderer.
                self.move(start_x, int(round(start_y - 32.0 * value)))

            # nbmotion-inventory: system.splash-desktop
            nbmotion.animate(self, _lift, 0.0, 1.0,
                             duration=nbmotion.PAGE,
                             easing=nbmotion.DEPART)
        except Exception:
            # Motion is strictly best-effort at boot.  A broken import,
            # primitive, widget or frame clock must not skip the already-armed
            # grace period: the completed bar should remain briefly visible.
            pass

    def _failsafe(self):
        # never let the splash trap the session behind it
        self._finish()
        return False


def main():
    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    except Exception:
        # styling is best-effort; the splash must still come up and, above all,
        # must still dismiss the session on its own.
        pass

    win = Splash()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
