#!/usr/bin/env python3
"""Static guards for keyboard-reachable Calendar sidebar controls."""
from pathlib import Path

src = (Path(__file__).resolve().parents[1] /
       "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calendar.py")
text = src.read_text()
row = text[text.index("    def _cal_row("):text.index("    def _draw_calbox(")]

checks = {
    "visibility toggle exposes checked state":
        "toggle = Gtk.ToggleButton()" in row and "toggle.set_active" in row,
    "visibility toggle uses clicked semantics":
        'toggle.connect("clicked", self._on_toggle_cal_clicked, name)' in row,
    "visibility toggle is not pointer-only":
        "Gtk.EventBox" not in row and "button-press-event" not in row,
    "visibility action is described":
        'action_name = _t("Show or hide calendar") + ": " + name' in row
        and "get_accessible().set_name(action_name)" in row,
    "calendar name is kept verbatim": "nbi18n.set_verbatim(lbl, name)" in row,
    "button retains neutral Papertone styling":
        'add_class("caltoggle")' in row and ".caltoggle {" in text,
    "dense calendar grids remain a separate navigation task":
        'cell = Gtk.EventBox()' in text and 'slot = Gtk.EventBox()' in text,
}

ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok = ok and passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
