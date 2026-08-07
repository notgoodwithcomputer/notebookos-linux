#!/usr/bin/env python3
"""Static guards for keyboard-operable Academics rename headings."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/academics.py").read_text()
section = text[text.index("        ebtn = Gtk.Button()"):
               text.index("        self.title = Gtk.Entry()")]
checks = {
    "class heading is a native button": "ebtn = Gtk.Button()" in section,
    "class rename uses clicked semantics": 'ebtn.connect("clicked"' in section,
    "lecture heading is a native button": "self.title_ev = Gtk.Button()" in section,
    "lecture rename uses clicked semantics": 'self.title_ev.connect("clicked"' in section,
    "rename headings are not pointer-only":
        "Gtk.EventBox" not in section and "button-press-event" not in section,
    "class heading retains its content": "ebtn.add(eb)" in section,
    "lecture heading retains wrapped label":
        "set_line_wrap(True)" in section and "self.title_ev.add(self.title_lbl)" in section,
    "both actions retain descriptions":
        "Rename class" in section and "Rename lecture" in section,
    "flat Papertone style suppresses theme chrome":
        ".doctitlebtn {" in text and "background-image: none" in text and "box-shadow: none" in text,
    "timetable keeps its dedicated keyboard canvas model":
        'self.grid_area.connect("key-press-event", self._on_timetable_key)' in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
