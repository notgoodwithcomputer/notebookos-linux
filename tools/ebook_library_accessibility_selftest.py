#!/usr/bin/env python3
"""Static guards for keyboard-operable eBook Library rows."""
import re
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/ebook.py").read_text()
row = text[text.index("    def _book_row("):text.index("    def _populate_shelf(")]
# Scope the styling checks to the .bookopen rule itself. A file-wide substring
# search goes green on a declaration that belongs to some OTHER rule, which is
# how a flat-styling gate passes with the open target still wearing the theme's
# button chrome.
rule = re.search(r"\.bookopen \{(.*?)\}", text, re.S)
rule = rule.group(1) if rule else ""
checks = {
    "book open action is a native button": "open_area = Gtk.Button()" in row,
    "book open action uses clicked semantics": 'open_area.connect("clicked"' in row,
    "book open action is not pointer-only":
        "open_area = Gtk.EventBox" not in row and "button-press-event" not in row,
    "book title remains in the open target": "open_area.add(entry)" in row,
    "open action remains described": 'set_tooltip_text(_t("Open %s")' in row,
    "remove remains an isolated native action": 'rm.connect("clicked"' in row,
    # clicked hands the callback (button, user_data): the path moves from the
    # THIRD positional slot to the second, because button-press-event carried
    # the Gdk event in between. A stale 3-arg signature silently opens nothing.
    "open callback matches clicked's arity":
        "def _on_book_open(self, _row, path):" in text,
    "open target still fills the row": "row.pack_start(open_area, True, True, 0)" in row,
    "active-row styling survives": 'add_class("sheetbookopen")' in row,
    "flat styling suppresses theme chrome":
        all(d in rule for d in ("background: transparent", "background-image: none",
                                "box-shadow: none", "border: none", "padding: 0")),
    "flat styling keeps the focus ring": "outline" not in rule,
    "modal click-away scrims remain out of scope": "scrim = Gtk.EventBox()" in text,
    "remove confirmation exposes a dialog role":
        "dialog_acc.set_role(Atk.Role.DIALOG)" in text,
    "remove confirmation exposes modal state":
        "notify_state_change(Atk.StateType.MODAL, True)" in text,
    "remove confirmation starts on Cancel": "cancel.grab_focus()" in text,
    "remove confirmation traps Tab":
        "Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab" in text,
    "closing confirmation restores prior focus":
        "self._confirm_restore_focus" in text and "prior.grab_focus()" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
