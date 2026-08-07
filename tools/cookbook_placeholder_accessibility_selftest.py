#!/usr/bin/env python3
"""Static guards for keyboard-operable Cookbook empty-state actions."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/cookbook.py").read_text()
section = text[text.index("    def _render_placeholder("):
               text.index("    def _enter_panel_edit(")]
checks = {
    "empty-state action is a native button": "evt = Gtk.Button()" in section,
    "empty-state action uses clicked semantics": 'evt.connect("clicked"' in section,
    "empty-state action is not pointer-only":
        "Gtk.EventBox" not in section and "button-press-event" not in section,
    "ingredient and instruction actions are distinguished":
        "Add ingredients" in section and "Add instructions" in section,
    "translated placeholder retains wrapping":
        "set_line_wrap(True)" in section and "Pango.WrapMode.WORD_CHAR" in section,
    "both targets retain shared edit routing": "self._enter_panel_edit(kind)" in section,
    "Papertone style suppresses theme chrome":
        ".renderphbtn {" in text and "background-image: none; box-shadow: none" in text,
    "placeholder typography remains on the child label":
        'lbl.get_style_context().add_class("renderph")' in section,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
