#!/usr/bin/env python3
"""Static guards for keyboard-operable Tasks and Calendar palettes."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
de = root / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
tasks = (de / "tasks.py").read_text()
calendar = (de / "calendar.py").read_text()
task_section = tasks[tasks.index("    def _swatch("):tasks.index("    def _draw_swatch(")]
cal_section = calendar[calendar.index("        swrow = Gtk.Box("):
                       calendar.index("        area.pack_start(self._field(\"Color\"")]
checks = {
    "Tasks swatches are native buttons": "button = Gtk.Button()" in task_section,
    "Tasks swatches use clicked semantics": 'connect("clicked"' in task_section,
    "Tasks swatches retain 28px custom artwork": "set_size_request(28, 28)" in task_section,
    "Tasks palette is no longer pointer-only":
        "Gtk.EventBox" not in task_section and "button-press-event" not in task_section,
    "Calendar swatches are native buttons": "ev = Gtk.Button()" in cal_section,
    "Calendar swatches use clicked semantics": 'ev.connect("clicked"' in cal_section,
    "Calendar swatches retain 26px custom artwork": "set_size_request(26, 26)" in cal_section,
    "Calendar palette is no longer pointer-only":
        "Gtk.EventBox" not in cal_section and "button-press-event" not in cal_section,
    "both palettes describe their choices":
        "Choose colour %d" in task_section and "Choose color %d" in cal_section,
    "flat styles retain the Papertone artwork":
        ".nlswatch {" in tasks and ".calswatch {" in calendar,
    "dense day cells and modal scrims remain out of scope":
        "daycell = Gtk.EventBox()" in tasks and "cell = Gtk.EventBox()" in calendar,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
