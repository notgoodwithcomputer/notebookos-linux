#!/usr/bin/env python3
"""
controlshot — render the whole Papertone control set on one sheet.

WHY. The theme is edited as 280 lines of CSS, but it is EXPERIENCED as a set of
controls sitting next to each other. Radii, control heights, hairline weights
and padding only read as coherent — or don't — in company: a 2px corner looks
fine alone and looks like 1998 beside a rounded card. Every previous change to
this theme was verified by opening one app and looking at one widget, which is
how the OS ended up with buttons, checks, entries and switches that each made
sense on their own and did not agree with each other about what decade it was.

So: every control, one sheet, rendered offscreen through the real theme.

    FONTCONFIG_FILE=tools/guest-fonts.conf DISPLAY=:0 \
        python3 tools/controlshot.py /tmp/controls.png

Render before and after a theme edit and put the two side by side. That is the
only way to see whether a change improved the system or just moved one control.
"""
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uishot  # noqa: E402


def _row(label_text, *widgets):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    lab = Gtk.Label(label=label_text)
    lab.set_xalign(0.0)
    lab.set_size_request(96, -1)
    lab.get_style_context().add_class("gallerylabel")
    box.pack_start(lab, False, False, 0)
    for w in widgets:
        box.pack_start(w, False, False, 0)
    return box


def build():
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    root.set_border_width(20)

    # --- buttons -----------------------------------------------------------
    b_norm = Gtk.Button(label="Open")
    b_sugg = Gtk.Button(label="Save")
    b_sugg.get_style_context().add_class("suggested-action")
    b_dest = Gtk.Button(label="Delete Forever")
    b_dest.get_style_context().add_class("destructive-action")
    root.pack_start(_row("Buttons", b_norm, b_sugg, b_dest), False, False, 0)

    # A pressed/checked toggle, which is a different visual state entirely.
    t_off = Gtk.ToggleButton(label="Bold")
    t_on = Gtk.ToggleButton(label="Italic")
    t_on.set_active(True)
    root.pack_start(_row("Toggles", t_off, t_on), False, False, 0)

    # --- text entry --------------------------------------------------------
    e = Gtk.Entry()
    e.set_text("Untitled notebook")
    e.set_width_chars(22)
    e_ph = Gtk.Entry()
    e_ph.set_placeholder_text("Search")
    e_ph.set_width_chars(14)
    root.pack_start(_row("Entry", e, e_ph), False, False, 0)

    sp = Gtk.SpinButton.new_with_range(0, 100, 1)
    sp.set_value(12)
    root.pack_start(_row("Spin", sp), False, False, 0)

    # --- small controls ----------------------------------------------------
    ck_on = Gtk.CheckButton(label="Wrap text")
    ck_on.set_active(True)
    ck_off = Gtk.CheckButton(label="Spell check")
    rb = Gtk.RadioButton(label="Portrait")
    rb2 = Gtk.RadioButton.new_with_label_from_widget(rb, "Landscape")
    root.pack_start(_row("Check", ck_on, ck_off), False, False, 0)
    root.pack_start(_row("Radio", rb, rb2), False, False, 0)

    sw_on = Gtk.Switch()
    sw_on.set_active(True)
    sw_off = Gtk.Switch()
    swbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    swbox.pack_start(sw_on, False, False, 0)
    swbox.pack_start(sw_off, False, False, 0)
    root.pack_start(_row("Switch", swbox), False, False, 0)

    # --- sliders / progress ------------------------------------------------
    sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    sc.set_value(62)
    sc.set_draw_value(False)
    sc.set_size_request(180, -1)
    pb = Gtk.ProgressBar()
    pb.set_fraction(0.45)
    pb.set_size_request(140, -1)
    pb.set_valign(Gtk.Align.CENTER)
    root.pack_start(_row("Slider", sc, pb), False, False, 0)

    # --- a list with a selected row ---------------------------------------
    lb = Gtk.ListBox()
    lb.set_size_request(300, -1)
    for i, name in enumerate(("Yesterday", "Chapter one", "Notes on rain")):
        r = Gtk.ListBoxRow()
        l = Gtk.Label(label=name)
        l.set_xalign(0.0)
        l.set_margin_start(12)
        l.set_margin_end(12)
        l.set_margin_top(8)
        l.set_margin_bottom(8)
        r.add(l)
        lb.add(r)
    lb.select_row(lb.get_row_at_index(1))
    frame = Gtk.Frame()
    frame.add(lb)
    root.pack_start(_row("List", frame), False, False, 0)

    # --- a statusbar, the one shared component ----------------------------
    sb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    sb.get_style_context().add_class("statusbar")
    sl = Gtk.Label(label="1,284 words · Saved")
    sl.set_xalign(0.0)
    sb.pack_start(sl, True, True, 0)
    sb.set_size_request(-1, 32)
    root.pack_start(sb, False, False, 0)

    return root


GALLERY_CSS = b"""
.gallerylabel { font-size: 11px; color: #6E695E; }
"""


def main(argv):
    path = argv[1] if len(argv) > 1 else "/tmp/nb-controls.png"
    uishot.load_theme()
    uishot.shot(build, 640, 560, path, app_css=GALLERY_CSS)
    print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
