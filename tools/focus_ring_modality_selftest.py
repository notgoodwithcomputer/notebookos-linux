#!/usr/bin/env python3
"""Focus rings belong to the keyboard, and to nothing else.

Papertone draws a 2px accent ring wherever GTK asks for one. GTK asks whenever
`gtk_widget_has_visible_focus()` is true, and that reads GtkWindow's
`focus-visible` property -- which DEFAULTS TO TRUE and which GTK3 never lowers
on its own. So on a real machine every control that takes focus by CLICK wore
the keyboard ring: measured on target 2026-08-17, clicking Calendar's sidebar
row drew a red rectangle around it.

The theme comment claimed the opposite ("a mouse user never sees a ring --
verified: all 28 apps render pixel-identically with this rule added"). That
verification used OFFSCREEN renders, where the window is never the active
toplevel and no ring can draw either way: the instrument could not see the
thing it certified. This suite uses the REAL property, both ways.

    tools/guestrun.sh python3 tools/focus_ring_modality_selftest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(
    os.path.dirname(HERE),
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402
import nbapp  # noqa: E402

FAILS = []
COUNT = 0


def check(name, ok, detail=""):
    global COUNT
    COUNT += 1
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


def build():
    win = Gtk.Window()
    btn = Gtk.Button(label="Press me")
    win.add(btn)
    win.realize()
    btn.realize()
    btn.grab_focus()
    return win, btn


def event(kind, widget):
    ev = Gdk.Event.new(kind)
    ev.window = widget.get_window()
    if kind == Gdk.EventType.KEY_PRESS:
        ev.keyval = Gdk.KEY_Tab
        ev.state = Gdk.ModifierType(0)
        ev.string = ""
    else:
        ev.x, ev.y, ev.button = 2.0, 2.0, 1
        ev.state = Gdk.ModifierType(0)
    return ev


def main():
    win, btn = build()

    # ---- the starting point that made this a bug ------------------------
    check("a fresh window starts with focus-visible ON (GTK's default, which "
          "is why a click used to draw the ring)", win.get_focus_visible() is True,
          repr(win.get_focus_visible()))

    # ---- a click lowers it ----------------------------------------------
    changed = nbapp.note_input_modality(event(Gdk.EventType.BUTTON_PRESS, btn))
    check("a button press turns the focus ring off", changed is True
          and win.get_focus_visible() is False, repr(win.get_focus_visible()))
    check("...and pressing again changes nothing (idempotent, no churn)",
          nbapp.note_input_modality(event(Gdk.EventType.BUTTON_PRESS, btn)) is False)
    check("...a double-click keeps it off",
          nbapp.note_input_modality(event(Gdk.EventType._2BUTTON_PRESS, btn)) is False
          and win.get_focus_visible() is False)

    # ---- a keystroke brings it straight back ----------------------------
    changed = nbapp.note_input_modality(event(Gdk.EventType.KEY_PRESS, win))
    check("a key press turns the focus ring back on -- on the same keystroke "
          "that needs it", changed is True and win.get_focus_visible() is True,
          repr(win.get_focus_visible()))

    # ---- events that say nothing about modality leave it alone ----------
    win.set_focus_visible(True)
    moved = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
    moved.window = btn.get_window()
    moved.x = moved.y = 4.0
    check("moving the mouse without pressing changes nothing",
          nbapp.note_input_modality(moved) is False
          and win.get_focus_visible() is True)

    # ---- it can never take input down -----------------------------------
    check("a rubbish event is ignored rather than raising",
          nbapp.note_input_modality(None) is False)

    # ---- the dispatcher is installed, once, and still delivers ----------
    nbapp._MODALITY_HOOKED = False
    nbapp.track_input_modality()
    check("install puts the hook in place", nbapp._MODALITY_HOOKED is True)
    delivered = []
    real_do_event = Gtk.main_do_event
    try:
        Gtk.main_do_event = lambda ev: delivered.append(ev.type)
        nbapp._MODALITY_HOOKED = False
        nbapp.track_input_modality()
        win.set_focus_visible(True)
        Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        # push a real event through the installed dispatcher
        ev = event(Gdk.EventType.BUTTON_PRESS, btn)
        ev.put()                      # goes onto the GDK queue
        for _ in range(50):
            if not Gtk.events_pending():
                break
            Gtk.main_iteration_do(False)
    finally:
        Gtk.main_do_event = real_do_event
    check("every event still reaches GTK through the hook (input cannot die)",
          any(t == Gdk.EventType.BUTTON_PRESS for t in delivered),
          "delivered=%r" % (delivered[:6],))

    win.destroy()
    print("%d checks, %d passed, %d FAILED" % (COUNT, COUNT - len(FAILS), len(FAILS)))
    if FAILS:
        print("RESULT: FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
