#!/usr/bin/env python3
"""Notebook OS native, layout-aware touchscreen keyboard."""
import ctypes
import os
import signal
import sys
from ctypes import (c_bool, c_char_p, c_int, c_uint, c_ulong, c_void_p,
                    c_ubyte, c_ushort, POINTER, Structure)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from nbdiacritics import HOLD_MS, TABLE


# X11's conventional physical key positions.  Legends never come from here.
LETTER_ROWS = ((24, 25, 26, 27, 28, 29, 30, 31, 32, 33),
               (38, 39, 40, 41, 42, 43, 44, 45, 46),
               (52, 53, 54, 55, 56, 57, 58))
NUMBER_ROW = (10, 11, 12, 13, 14, 15, 16, 17, 18, 19)
SHIFT_KEYCODE = 50
SPECIAL = {"backspace": 22, "enter": 36, "space": 65,
           "comma": 59, "period": 60}
SYMBOLS = ((0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x30),
           (0x21, 0x40, 0x23, 0x24, 0x25, 0x5e, 0x26, 0x2a, 0x28, 0x29),
           (0x2d, 0x5f, 0x3d, 0x2b, 0x5b, 0x5d, 0x7b, 0x7d, 0x5c),
           (0x3b, 0x3a, 0x27, 0x22, 0x2c, 0x3c, 0x2e, 0x3e, 0x2f, 0x3f))


def keysym_text(keysym):
    """Return a printable legend for a keysym, without layout assumptions."""
    if not keysym:
        return ""
    cp = Gdk.keyval_to_unicode(keysym)
    return chr(cp) if cp and chr(cp).isprintable() else ""


class KeymapLabels:
    """Small adapter seam: tests supply lookup(code, group, level)."""
    def __init__(self, lookup, group=0):
        self.lookup = lookup
        self.group = group

    def value(self, code, level=0):
        sym = self.lookup(code, self.group, level)
        return (keysym_text(sym), sym)

    def row(self, codes, level=0):
        return [self.value(code, level)[0] for code in codes]


class KeyboardState:
    """Phone-style pages and shift: off -> one-shot -> latched -> off."""
    def __init__(self):
        self.page = "letters"
        self.shift = 0                 # 0 off, 1 one-shot, 2 latched

    def tap_shift(self):
        self.shift = (self.shift + 1) % 3

    def toggle_page(self):
        self.page = "symbols" if self.page == "letters" else "letters"

    def intent(self, keycode):
        shifted = bool(self.shift)
        if self.shift == 1:
            self.shift = 0
        return keycode, shifted


class LongPressModel:
    def __init__(self, table=TABLE, threshold=HOLD_MS):
        self.table, self.threshold = table, threshold
        self.base, self.elapsed, self.open = None, 0, False

    def begin(self, character):
        self.base, self.elapsed, self.open = character, 0, False

    def advance(self, milliseconds):
        self.elapsed += milliseconds
        if self.base in self.table and self.elapsed >= self.threshold:
            self.open = True
        return self.table.get(self.base, ()) if self.open else ()

    def select(self, index, inject_character):
        items = self.table.get(self.base, ()) if self.open else ()
        self.cancel()
        if 0 <= index < len(items):
            inject_character(items[index])
            return True
        return False

    def cancel(self):
        self.base, self.elapsed, self.open = None, 0, False


class XBoundary:
    """Thin ctypes boundary, replaceable by the headless recorder."""
    def __init__(self):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.restype = c_void_p
        self.x11.XOpenDisplay.argtypes = [c_char_p]
        self.x11.XCloseDisplay.argtypes = [c_void_p]
        self.x11.XFlush.argtypes = [c_void_p]
        self.x11.XKeysymToKeycode.restype = c_uint
        self.x11.XKeysymToKeycode.argtypes = [c_void_p, c_ulong]
        self.x11.XDisplayKeycodes.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
        self.x11.XGetKeyboardMapping.restype = POINTER(c_ulong)
        self.x11.XGetKeyboardMapping.argtypes = [c_void_p, c_uint, c_int, POINTER(c_int)]
        self.x11.XChangeKeyboardMapping.argtypes = [c_void_p, c_int, c_int,
                                                    POINTER(c_ulong), c_int]
        self.x11.XkbGetState.argtypes = [c_void_p, c_uint, c_void_p]
        self.x11.XFree.argtypes = [c_void_p]
        self.xtst.XTestFakeKeyEvent.restype = c_bool
        self.xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_bool, c_ulong]
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("X display unavailable")

    def keycode_for(self, keysym):
        return int(self.x11.XKeysymToKeycode(self.display, keysym))

    def active_group(self):
        class State(Structure):
            _fields_ = [("group", c_ubyte), ("locked_group", c_ubyte),
                        ("base_group", c_ushort), ("latched_group", c_ushort),
                        ("mods", c_ubyte), ("base_mods", c_ubyte),
                        ("latched_mods", c_ubyte), ("locked_mods", c_ubyte),
                        ("compat_state", c_ubyte), ("grab_mods", c_ubyte),
                        ("compat_grab_mods", c_ubyte), ("lookup_mods", c_ubyte),
                        ("compat_lookup_mods", c_ubyte), ("ptr_buttons", c_ushort)]
        state = State()
        return int(state.group) if self.x11.XkbGetState(
            self.display, 0x0100, ctypes.byref(state)) == 0 else 0

    def key(self, code, down):
        if not self.xtst.XTestFakeKeyEvent(self.display, code, down, 0):
            raise RuntimeError("XTest key injection failed")

    def flush(self):
        self.x11.XFlush(self.display)

    def unused(self):
        lo, hi = c_int(), c_int()
        self.x11.XDisplayKeycodes(self.display, ctypes.byref(lo), ctypes.byref(hi))
        count, per = hi.value - lo.value + 1, c_int()
        ptr = self.x11.XGetKeyboardMapping(self.display, lo.value, count,
                                           ctypes.byref(per))
        if not ptr:
            raise RuntimeError("cannot read X keymap")
        try:
            for offset in range(count):
                vals = tuple(ptr[offset * per.value + j] for j in range(per.value))
                if not any(vals):
                    return lo.value + offset, vals
        finally:
            self.x11.XFree(ptr)
        raise RuntimeError("no unused X keycode")

    def remap(self, code, values):
        vals = tuple(values) or (0,)
        arr = (c_ulong * len(vals))(*vals)
        self.x11.XChangeKeyboardMapping(self.display, code, len(vals), arr, 1)
        self.flush()

    def close(self):
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


class Injector:
    def __init__(self, boundary):
        self.x = boundary

    def keycode(self, code, shifted=False):
        if shifted:
            self.x.key(SHIFT_KEYCODE, True)
        try:
            self.x.key(code, True)
            self.x.key(code, False)
        finally:
            if shifted:
                self.x.key(SHIFT_KEYCODE, False)
            self.x.flush()

    def character(self, character):
        keysym = 0x01000000 | ord(character)
        code = self.x.keycode_for(keysym)
        if code:
            self.keycode(code)
            return
        code, original = self.x.unused()
        try:
            self.x.remap(code, (keysym,) + tuple(original[1:]))
            self.keycode(code)
        finally:                         # never strand a borrowed keycode
            self.x.remap(code, original)


def gdk_lookup(keymap):
    def lookup(code, group, level):
        found, keys, values = keymap.get_entries_for_keycode(code)
        if not found:
            return 0
        for key, value in zip(keys, values):
            if key.group == group and key.level == level:
                return value
        return 0
    return lookup


TOKENS = {"paper": "#FCFBF8", "ink": "#1A1916", "hair": "#C9C4B6",
          "warm": "#EAE3D2", "shadow": "#9A9484"}
CSS = b"""
.osk { background: %(paper)s; padding: 7px; }
.osk-key { background: %(paper)s; color: %(ink)s; border: 1px solid %(hair)s;
 border-radius: 7px; box-shadow: 0 3px 0 %(hair)s; padding: 4px; }
.osk-key:active, .osk-key.on { background: %(warm)s; box-shadow: none;
 margin-top: 3px; }
.osk-special { font-weight: bold; }
.osk-palette { background: %(paper)s; border: 1px solid %(hair)s; padding: 5px; }
.osk-palette button { background: %(paper)s; color: %(ink)s;
 border: 1px solid %(hair)s; font-size: 22px; padding: 10px 14px; }
""" % {key.encode(): value.encode() for key, value in TOKENS.items()}


class OSKWindow(Gtk.Window):
    def __init__(self, injector=None):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)          # fallback if WM ignores dock struts
        self.set_resizable(False)
        self.connect("delete-event", lambda *_a: self.hide() or True)
        self.injector = injector or Injector(XBoundary())
        self.state = KeyboardState()
        self.hold = LongPressModel()
        self._hold_source = 0
        self._held_button = None
        self._palette = None
        keymap = Gdk.Keymap.get_default()
        self._labels = KeymapLabels(gdk_lookup(keymap), self._active_group())
        keymap.connect("keys-changed", self._keymap_changed)
        keymap.connect("state-changed", self._keymap_changed)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.get_style_context().add_class("osk")
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        geo = mon.get_geometry()
        scale = max(1, int(os.environ.get("NB_SCALE", "1") or "1"))
        height = max(4 * 48 * scale, int(geo.height * 0.36))
        self.set_default_size(geo.width, height)
        self.move(geo.x, geo.y + geo.height - height)
        self._rebuild()

    def _active_group(self):
        try:
            return self.injector.x.active_group()
        except Exception:
            return 0

    def _keymap_changed(self, *_args):
        self._labels.group = self._active_group()
        self._rebuild()

    def _button(self, label, callback, special=False):
        button = Gtk.Button(label=label)
        button.set_can_focus(False)
        button.set_focus_on_click(False)
        button.set_hexpand(True)
        button.set_vexpand(True)
        button.set_size_request(-1, 48)
        button.get_style_context().add_class("osk-key")
        if special:
            button.get_style_context().add_class("osk-special")
        button.connect("clicked", callback)
        return button

    def _rebuild(self):
        child = self.get_child()
        if child:
            self.remove(child)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        self.add(box)
        level = 1 if self.state.shift else 0
        if self.state.page == "symbols":
            for row_index, row in enumerate(SYMBOLS):
                line = Gtk.Box(spacing=6)
                box.pack_start(line, True, True, 0)
                for index, sym in enumerate(row):
                    # The first two rows are the physical number row at levels
                    # zero and one.  This keeps both legends and injection true
                    # to AZERTY and other layouts.  Remaining punctuation has
                    # no universal physical row, so it uses direct keysyms.
                    if row_index < 2:
                        code = NUMBER_ROW[index]
                        label, found = self._labels.value(code, row_index)
                        if found:
                            line.pack_start(self._button(label,
                                lambda _b, c=code, sh=bool(row_index):
                                    self.injector.keycode(c, sh)), True, True, 0)
                            continue
                    label = keysym_text(sym)
                    line.pack_start(self._button(label,
                        lambda _b, ch=label: self.injector.character(ch)), True, True, 0)
        else:
            rows = (NUMBER_ROW,) + LETTER_ROWS
            for codes in rows:
                line = Gtk.Box(spacing=6)
                box.pack_start(line, True, True, 0)
                for code in codes:
                    label, _sym = self._labels.value(code, level)
                    line.pack_start(self._letter_button(label, code), True, True, 0)
        bottom = Gtk.Box(spacing=6)
        box.pack_start(bottom, True, True, 0)
        shift = self._button("\u21e7", self._shift, True)
        if self.state.shift:
            shift.get_style_context().add_class("on")
        bottom.pack_start(shift, True, True, 0)
        bottom.pack_start(self._button("ABC" if self.state.page == "symbols" else "?123",
                                      self._page, True), True, True, 0)
        comma = self._labels.value(SPECIAL["comma"], level)[0] or ","
        period = self._labels.value(SPECIAL["period"], level)[0] or "."
        for label, name in ((comma, "comma"), (" ", "space"), (period, "period")):
            bottom.pack_start(self._button(label, lambda _b, n=name: self._tap(SPECIAL[n]),
                                           name == "space"), True, True, 0)
        bottom.pack_start(self._button("\u232b", lambda _b: self._tap(SPECIAL["backspace"]), True), True, True, 0)
        bottom.pack_start(self._button("\u21b5", lambda _b: self._tap(SPECIAL["enter"]), True), True, True, 0)
        bottom.pack_start(self._button("\u2304", lambda _b: self.hide(), True), False, True, 0)
        self.show_all()

    def _letter_button(self, label, code):
        button = self._button(label, lambda _b: self._release_letter(button, code))
        button.connect("button-press-event", self._press_letter, label, button)
        return button

    def _press_letter(self, _widget, _event, label, button):
        self.hold.begin(label)
        self._held_button = button
        if self._hold_source:
            GLib.source_remove(self._hold_source)
        self._hold_source = GLib.timeout_add(HOLD_MS, self._open_palette, button)
        return False

    def _release_letter(self, _button, code):
        if self._hold_source:
            GLib.source_remove(self._hold_source)
            self._hold_source = 0
        if not self.hold.open:
            self._tap(code)
        self.hold.cancel()

    def _open_palette(self, button):
        self._hold_source = 0
        items = self.hold.advance(HOLD_MS)
        if not items:
            return False
        pop = Gtk.Popover.new(button)
        pop.set_modal(False)
        pop.get_style_context().add_class("osk-palette")
        row = Gtk.Box(spacing=3)
        pop.add(row)
        for index, char in enumerate(items):
            key = self._button(char, lambda _b, ix=index: self._pick(ix))
            key.connect("enter-notify-event", self._palette_enter, index)
            row.pack_start(key, True, True, 0)
        pop.connect("button-release-event", self._palette_release)
        pop.connect("closed", lambda *_a: self.hold.cancel())
        self._palette = pop
        pop.show_all()
        return False

    def _palette_enter(self, _button, _event, index):
        self._palette_index = index       # slide target under the fingertip
        return False

    def _palette_release(self, *_args):
        index = getattr(self, "_palette_index", -1)
        if index >= 0:
            self._pick(index)
        else:
            self.hold.cancel()            # release elsewhere means no input
        self._palette_index = -1
        return False

    def _pick(self, index):
        self.hold.select(index, self.injector.character)
        if self._palette:
            self._palette.popdown()

    def _tap(self, code):
        code, shifted = self.state.intent(code)
        self.injector.keycode(code, shifted)
        if not self.state.shift:
            self._rebuild()

    def _shift(self, _button):
        self.state.tap_shift()
        self._rebuild()

    def _page(self, _button):
        self.state.toggle_page()
        self._rebuild()


def _claim_instance():
    path = "/tmp/notebook-osk.pid"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return path
    except FileExistsError:
        try:
            with open(path, encoding="ascii") as handle:
                pid = int(handle.read())
            os.kill(pid, 0)
            return None
        except (OSError, ValueError):
            try:
                os.unlink(path)
            except OSError:
                pass
            return _claim_instance()


def main():
    lock = _claim_instance()
    if not lock:
        return 0
    window = OSKWindow()
    def stop(*_args):
        window.hide()
        Gtk.main_quit()
    signal.signal(signal.SIGTERM, stop)
    window.connect("destroy", lambda *_a: Gtk.main_quit())
    window.show_all()
    try:
        Gtk.main()
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass
        window.injector.x.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
