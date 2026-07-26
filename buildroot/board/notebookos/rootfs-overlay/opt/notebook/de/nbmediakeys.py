#!/usr/bin/env python3
"""nbmediakeys — global volume/brightness media keys with a papertone OSD.

Grabs the standard XF86 media keysyms on the root window and, on each press,
adjusts the level (volume via ALSA `amixer`, brightness via /sys/class/backlight)
and shows a small on-screen popup with an icon, a level bar and the percentage.

Handled keys:
  XF86AudioRaiseVolume / XF86AudioLowerVolume / XF86AudioMute
  XF86MonBrightnessUp  / XF86MonBrightnessDown

Design notes for THIS stack:
 - The key grab + X event loop run on their OWN X connection in a background
   thread (the same ctypes-to-libX11 idiom as de/xshape.py), because GTK offers
   no global-hotkey API and mixing a raw XNextEvent loop into GTK's own X
   connection is fragile. Grabs are server-side, so a second connection receives
   the grabbed KeyPress events fine.
 - GTK is not thread-safe, so the worker only ever touches the OSD via
   GLib.idle_add — every GTK call happens on the main loop.
 - Brightness needs a backlight device (real hardware); with none present (QEMU,
   a desktop with no panel) the brightness keys simply no-op — no OSD, no error.
"""
import ctypes
import glob
import os
import subprocess
from ctypes import c_int, c_uint, c_ulong, c_long, c_void_p, c_char_p, Structure

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import cairo

# ---- XF86 media keysyms ----------------------------------------------------
XF86_AudioLowerVolume = 0x1008FF11
XF86_AudioMute = 0x1008FF12
XF86_AudioRaiseVolume = 0x1008FF13
XF86_MonBrightnessUp = 0x1008FF02
XF86_MonBrightnessDown = 0x1008FF03

_KEYPRESS = 2
_ANY_MODIFIER = 1 << 15
_GRAB_ASYNC = 1

VOL_STEP = 5          # percent per press
BRIGHT_STEP = 8       # percent per press
OSD_MS = 1400         # how long the popup stays up after the last press

# palette (papertone)
_PAPER = (0xFC / 255, 0xFB / 255, 0xF8 / 255)
_INK = (0x1A / 255, 0x19 / 255, 0x16 / 255)
_HAIR = (0xC9 / 255, 0xC4 / 255, 0xB6 / 255)
_TRACK = (0xDE / 255, 0xD4 / 255, 0xC2 / 255)
_MUTED = (0x8A / 255, 0x85 / 255, 0x7A / 255)
_RED = (0xC8 / 255, 0x34 / 255, 0x1E / 255)


# ---- X key grab (own connection, ctypes) -----------------------------------
class _XKeyEvent(Structure):
    # Padded to the size of an XEvent (192 bytes on x86_64) so XNextEvent has a
    # big enough buffer; we only read .type (first field) and .keycode.
    _fields_ = [
        ("type", c_int), ("serial", c_ulong), ("send_event", c_int),
        ("display", c_void_p), ("window", c_ulong), ("root", c_ulong),
        ("subwindow", c_ulong), ("time", c_ulong),
        ("x", c_int), ("y", c_int), ("x_root", c_int), ("y_root", c_int),
        ("state", c_uint), ("keycode", c_uint), ("same_screen", c_int),
        ("_pad", c_long * 16),
    ]


class _KeyGrabber:
    """Grabs the media keys on root and calls on_key(keysym) per press, driven
    from the GTK main loop via a watch on the X connection fd — NOT a thread.
    Xlib is not thread-safe without XInitThreads, and a worker-thread XNextEvent
    on the raw connection did not reliably deliver the grabbed keys; polling
    XPending on the main loop when the fd is readable is simple and correct."""

    def __init__(self, on_key):
        self.on_key = on_key
        self._x11 = ctypes.CDLL("libX11.so.6")
        self._x11.XOpenDisplay.restype = c_void_p
        self._x11.XOpenDisplay.argtypes = [c_char_p]
        self._x11.XDefaultRootWindow.restype = c_ulong
        self._x11.XDefaultRootWindow.argtypes = [c_void_p]
        self._x11.XKeysymToKeycode.restype = c_int
        self._x11.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
        self._x11.XGrabKey.argtypes = [c_void_p, c_int, c_uint, c_ulong,
                                       c_int, c_int, c_int]
        self._x11.XNextEvent.argtypes = [c_void_p, c_void_p]
        self._x11.XPending.restype = c_int
        self._x11.XPending.argtypes = [c_void_p]
        self._x11.XConnectionNumber.restype = c_int
        self._x11.XConnectionNumber.argtypes = [c_void_p]
        self._x11.XFlush.argtypes = [c_void_p]
        self.dpy = None
        self.ok = False
        self._code_to_sym = {}

    def setup(self):
        self.dpy = self._x11.XOpenDisplay(None)
        if not self.dpy:
            return False
        root = self._x11.XDefaultRootWindow(self.dpy)
        # STANDARD evdev keycodes the physical media keys emit (X keycode =
        # evdev + 8), grabbed directly so this works even where the running X
        # keymap does not map the XF86 keysyms (XKeysymToKeycode would return 0
        # there). Also grab any keysym-resolved keycode, for keymaps that DO
        # map them (normal on real hardware).
        std = {
            121: XF86_AudioMute, 122: XF86_AudioLowerVolume,
            123: XF86_AudioRaiseVolume, 232: XF86_MonBrightnessUp,
            233: XF86_MonBrightnessDown,
        }
        for code, sym in std.items():
            self._code_to_sym[code] = sym
        for sym in (XF86_AudioLowerVolume, XF86_AudioMute,
                    XF86_AudioRaiseVolume, XF86_MonBrightnessUp,
                    XF86_MonBrightnessDown):
            code = self._x11.XKeysymToKeycode(self.dpy, sym)
            if code:
                self._code_to_sym[code] = sym
        # Grab with AnyModifier AND with no modifiers (some servers do not honour
        # an AnyModifier passive grab; the explicit 0-modifier grab is the sure
        # one). BadAccess on a duplicate is harmless.
        for code in self._code_to_sym:
            for mod in (_ANY_MODIFIER, 0):
                self._x11.XGrabKey(self.dpy, code, mod, root,
                                   False, _GRAB_ASYNC, _GRAB_ASYNC)
        self._x11.XFlush(self.dpy)
        self.ok = bool(self._code_to_sym)
        return self.ok

    def start(self):
        """Watch the X connection fd on the GLib main loop."""
        if not (self.dpy and self.ok):
            return
        fd = self._x11.XConnectionNumber(self.dpy)
        GLib.io_add_watch(fd, GLib.IO_IN, self._on_fd)

    def _on_fd(self, *_a):
        ev = _XKeyEvent()
        try:
            while self._x11.XPending(self.dpy) > 0:
                self._x11.XNextEvent(self.dpy, ctypes.byref(ev))
                if ev.type == _KEYPRESS:
                    sym = self._code_to_sym.get(ev.keycode)
                    if sym is not None:
                        self.on_key(sym)
        except Exception:
            pass
        return True


# ---- level backends --------------------------------------------------------
def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def _amixer_state():
    """(percent:int, muted:bool) for Master, or (None, False) if unavailable."""
    rc, out = _run(["amixer", "-M", "get", "Master"])
    if rc != 0:
        return None, False
    pct, muted = None, False
    for ln in out.splitlines():
        if "%]" in ln:
            try:
                pct = int(ln.split("[", 1)[1].split("%", 1)[0])
            except (IndexError, ValueError):
                pass
            muted = "[off]" in ln
            break
    return pct, muted


def _volume(delta=None, toggle=False):
    if toggle:
        _run(["amixer", "-M", "-q", "sset", "Master", "toggle"])
    elif delta:
        sign = "%d%%%s" % (abs(delta), "+" if delta > 0 else "-")
        _run(["amixer", "-M", "-q", "sset", "Master", sign, "unmute"])
    return _amixer_state()


def _backlight_dev():
    devs = sorted(glob.glob("/sys/class/backlight/*"))
    return devs[0] if devs else None


def _brightness(delta):
    """Adjust the first backlight by delta percent; return the new percent, or
    None if there is no backlight device."""
    dev = _backlight_dev()
    if not dev:
        return None
    try:
        with open(os.path.join(dev, "max_brightness")) as fh:
            mx = int(fh.read().strip())
        with open(os.path.join(dev, "brightness")) as fh:
            cur = int(fh.read().strip())
        if mx <= 0:
            return None
        step = max(1, mx * abs(delta) // 100)
        new = cur + step if delta > 0 else cur - step
        new = max(0, min(mx, new))
        with open(os.path.join(dev, "brightness"), "w") as fh:
            fh.write(str(new))
        return int(round(new * 100.0 / mx))
    except (OSError, ValueError):
        return None


# ---- the OSD popup ---------------------------------------------------------
class _OSD(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_keep_above(True)
        # DIALOG: matchbox floats a dialog at its requested size/position
        # (free-dialog mode) — the same reason the Finder floats — whereas a
        # NOTIFICATION toplevel is not shown. keep_above + raise put it on top.
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_app_paintable(True)
        try:                              # opaque visual — never an alpha hole
            vis = self.get_screen().get_system_visual()
            if vis is not None:
                self.set_visual(vis)
        except Exception:
            pass
        self._w, self._h = 300, 92
        self.set_size_request(self._w, self._h)
        self._kind = "volume"    # "volume" | "brightness"
        self._pct = 0
        self._muted = False
        self._hide_src = None
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)

    def _position(self):
        try:
            scr = self.get_screen()
            mon = scr.get_primary_monitor() if hasattr(scr, "get_primary_monitor") else 0
            geo = scr.get_monitor_geometry(mon if isinstance(mon, int) else 0)
            x = geo.x + (geo.width - self._w) // 2
            y = geo.y + geo.height - self._h - 80
            self.move(x, y)
        except Exception:
            self.move(600, 900)

    def show_level(self, kind, pct, muted=False):
        self._kind, self._pct, self._muted = kind, int(pct), bool(muted)
        self._position()
        self.area.queue_draw()
        self.show_all()
        try:
            gw = self.get_window()
            if gw is not None:
                gw.raise_()
        except Exception:
            pass
        if self._hide_src is not None:
            GLib.source_remove(self._hide_src)
        self._hide_src = GLib.timeout_add(OSD_MS, self._auto_hide)

    def _auto_hide(self):
        self._hide_src = None
        self.hide()
        return False

    # ---- drawing ----
    def _rrect(self, cr, x, y, w, h, r):
        import math
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _w, cr):
        W, H = self._w, self._h
        # The window uses the opaque system visual (no alpha), so fill the whole
        # window as the papertone card with a hairline frame — no transparent
        # margin (which would scan out as black on an opaque visual).
        cr.set_source_rgb(*_PAPER); cr.paint()
        cr.rectangle(0.5, 0.5, W - 1, H - 1)
        cr.set_source_rgb(*_HAIR); cr.set_line_width(1); cr.stroke()
        # icon
        cr.save(); cr.translate(30, H / 2)
        cr.set_source_rgb(*(_MUTED if (self._kind == "volume" and self._muted)
                            else _INK))
        if self._kind == "volume":
            self._speaker(cr)
        else:
            self._sun(cr)
        cr.restore()
        # level bar
        bx, bw, by, bh = 66, W - 66 - 64, H / 2 - 4, 8
        self._rrect(cr, bx, by, bw, bh, 4)
        cr.set_source_rgb(*_TRACK); cr.fill()
        fillw = max(0, min(bw, bw * self._pct / 100.0))
        if fillw > 1 and not (self._kind == "volume" and self._muted):
            self._rrect(cr, bx, by, fillw, bh, 4)
            cr.set_source_rgb(*_INK); cr.fill()
        # percent / MUTED
        cr.select_font_face("Nimbus Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(16)
        label = "Muted" if (self._kind == "volume" and self._muted) \
            else "%d%%" % self._pct
        te = cr.text_extents(label)
        cr.set_source_rgb(*(_RED if (self._kind == "volume" and self._muted)
                            else _INK))
        cr.move_to(W - 20 - te.width, H / 2 + te.height / 2 - 1)
        cr.show_text(label)
        return False

    def _speaker(self, cr):
        cr.set_line_width(1.6)
        cr.move_to(-11, -5); cr.line_to(-5, -5); cr.line_to(2, -11)
        cr.line_to(2, 11); cr.line_to(-5, 5); cr.line_to(-11, 5)
        cr.close_path(); cr.fill()
        if self._muted:
            cr.set_line_width(1.8)
            cr.move_to(6, -7); cr.line_to(14, 7); cr.stroke()
            cr.move_to(14, -7); cr.line_to(6, 7); cr.stroke()
        else:
            import math
            for i, r in enumerate((5, 9)):
                cr.arc(2, 0, r, -math.pi / 4, math.pi / 4)
                cr.set_line_width(1.6); cr.stroke()

    def _sun(self, cr):
        import math
        cr.arc(0, 0, 5, 0, 2 * math.pi); cr.fill()
        cr.set_line_width(1.6)
        for k in range(8):
            a = k * math.pi / 4
            cr.move_to(8 * math.cos(a), 8 * math.sin(a))
            cr.line_to(11 * math.cos(a), 11 * math.sin(a))
            cr.stroke()


class MediaKeys:
    def __init__(self):
        self.osd = _OSD()
        self.grab = _KeyGrabber(self._on_key)

    def start(self):
        if not self.grab.setup():
            return False
        self.grab.start()
        return True

    def _on_key(self, sym):
        if sym == XF86_AudioRaiseVolume:
            pct, muted = _volume(delta=VOL_STEP)
            if pct is not None:
                self.osd.show_level("volume", pct, muted)
        elif sym == XF86_AudioLowerVolume:
            pct, muted = _volume(delta=-VOL_STEP)
            if pct is not None:
                self.osd.show_level("volume", pct, muted)
        elif sym == XF86_AudioMute:
            pct, muted = _volume(toggle=True)
            if pct is not None:
                self.osd.show_level("volume", pct, muted)
        elif sym == XF86_MonBrightnessUp:
            pct = _brightness(BRIGHT_STEP)
            if pct is not None:
                self.osd.show_level("brightness", pct)
        elif sym == XF86_MonBrightnessDown:
            pct = _brightness(-BRIGHT_STEP)
            if pct is not None:
                self.osd.show_level("brightness", pct)
        return False


def main():
    mk = MediaKeys()
    if not mk.start():
        return          # no X / no keys to grab: exit quietly
    Gtk.main()


if __name__ == "__main__":
    main()
