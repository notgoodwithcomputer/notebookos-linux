#!/usr/bin/env python3
"""Static guard for keyboard recall of Calculator history."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/calculator.py").read_text()
section = text[text.index("        self._histbox = Gtk.EventBox()"):
               text.index("    # ---- keypad ----")]
checks = {
    "full-allocation root carries opaque paper background":
        ('shell.get_style_context().add_class("calcroot")' in text
         and ".calcroot { background: #F8F7F2;" in text),
    "framebuffer-safe history target remains an EventBox": "Gtk.EventBox()" in section,
    "history target is keyboard focusable": "set_can_focus(True)" in section,
    "history action is described": "Recall last calculation" in section,
    "named keyboard handler is connected":
        'connect("key-press-event", self._on_history_key)' in section,
    "return, keypad enter and space recall history":
        all(k in section for k in ("Gdk.KEY_Return", "Gdk.KEY_KP_Enter", "Gdk.KEY_space")),
    "keyboard recall consumes handled input": "self.recall(-1)" in section and "return True" in section,
    "focus is visibly indicated": ".hist-box:focus" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
