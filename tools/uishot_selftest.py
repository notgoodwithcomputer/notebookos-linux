#!/usr/bin/env python3
"""uishot_selftest — the render harness must show what the APP left on screen.

WHY THIS EXISTS. Several apps finish construction with the sequence

    self.show_all()
    self.prog.hide()
    self.stop_btn.hide()

which is correct and is what the guest displays: no progress bar, no Stop
button, until a burn starts. uishot.shot_window used to call show_all() on the
lifted tree, which UN-HIDES every one of those widgets, so the render showed a
live Stop button and a progress meter over an idle app.

That is worse than a cosmetic bug in a tool. Every visual audit in this repo
reads these PNGs as evidence, and this one invented a defect that does not
exist — a reviewer looking at the picture would file "Stop is enabled with
nothing running", go to the code, and find the code correct. An instrument that
manufactures findings costs more than no instrument.

So: if the app already showed itself, whatever it had hidden stays hidden.

Run as:
  DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf python3 tools/uishot_selftest.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gi                                                     # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                 # noqa: E402

import uishot                                                 # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (("  <- " + detail)
                                                 if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


if not Gtk.init_check()[0]:
    print("REFUSING to pass vacuously: this harness renders, so it needs a "
          "display. Run with DISPLAY set.")
    raise SystemExit(1)


def build(hide_after_show_all):
    """A window shaped like a real app: shows itself, then hides a control."""
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    always = Gtk.Label(label="always visible")
    hidden = Gtk.Button(label="Stop")
    box.pack_start(always, False, False, 0)
    box.pack_start(hidden, False, False, 0)
    win.add(box)
    if hide_after_show_all:
        win.show_all()
        hidden.hide()
    return win, always, hidden


out = os.path.join(tempfile.mkdtemp(prefix="uishot-selftest-"), "shot.png")

# 1. The defect this file exists for.
win, always, hidden = build(True)
uishot.shot_window(win, 320, 200, out)
check("a control the app hid after show_all stays hidden in the render",
      not hidden.get_visible(),
      "the harness re-showed it, so the PNG shows a control the guest does not")
check("...and the rest of the app is still shown", always.get_visible())
check("...and a file was actually written", os.path.exists(out)
      and os.path.getsize(out) > 0)

# 2. The other half: an app that never showed itself must still be rendered,
#    or the fix above would blank every such window.
win2, always2, hidden2 = build(False)
out2 = out.replace("shot.png", "shot2.png")
uishot.shot_window(win2, 320, 200, out2)
check("an app that never called show_all is still shown by the harness",
      always2.get_visible() and hidden2.get_visible())

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
