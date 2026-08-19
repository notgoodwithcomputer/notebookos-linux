#!/usr/bin/env python3
"""Static guards for keyboard-operable Calendar event chips."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calendar.py").read_text()
# The chip block opens with a "Show up to ..." comment; the count in it is
# no longer a fixed three (the month measures what fits), so anchor on
# the prefix rather than the number.
month = text[text.index("        # Show up to "):
             text.index("        ev.add(cell)")]
timed = text[text.index("    def _time_chip("):text.index("    # ------------------------------------------------------------ persistence")]
checks = {
    "month event chips are native buttons": "chipbox = Gtk.Button()" in month,
    "month event chips use clicked semantics": 'chipbox.connect("clicked"' in month,
    "month event detail tooltip remains": "self._chip_detail(e)" in month,
    "month overflow is a native button": "morebox = Gtk.Button()" in month,
    "month overflow uses clicked semantics": 'morebox.connect("clicked"' in month,
    "month overflow action is described": "Show all events for this day" in month,
    "day and week event chips are native buttons": "box = Gtk.Button()" in timed,
    "timed event chips use clicked semantics": 'box.connect("clicked"' in timed,
    "continuation segments leave the Tab chain": "box.set_can_focus(lead)" in timed,
    "timed chips retain full event details": "self._chip_detail(e)" in timed,
    "event chip sections are not pointer-only":
        all("Gtk.EventBox" not in s and "button-press-event" not in s
            for s in (month, timed)),
    "Papertone style suppresses theme chrome":
        ".eventhit {" in text and "background-image: none; box-shadow: none" in text,
    "month grid remains one roving focus stop":
        'grid.connect("key-press-event", self._on_month_grid_key)' in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
