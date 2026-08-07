#!/usr/bin/env python3
"""Static guards for keyboard-operable Novel manuscript navigation."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/novel.py").read_text()
title = text[text.index("        title_ev = Gtk.Button()"):
             text.index("        # Plain running word total")]
part = text[text.index("    def _part_header("):text.index("    def _chapter_row(")]
chapter = text[text.index("    def _chapter_row("):text.index("    # ============================ COUNTING")]
checks = {
    "manuscript title is a native button": "Gtk.Button()" in title,
    "manuscript rename uses clicked semantics": 'title_ev.connect("clicked"' in title,
    "manuscript title retains bounded wrapping":
        "set_line_wrap(True)" in title and "set_lines(3)" in title,
    "part headers are native buttons": "Gtk.Button()" in part,
    "part rename uses clicked semantics": 'ev.connect("clicked"' in part,
    "chapter rows are native buttons": "Gtk.Button()" in chapter,
    "chapter selection uses clicked semantics": 'ev.connect("clicked"' in chapter,
    "hierarchy controls are no longer pointer-only":
        all("Gtk.EventBox" not in s and "button-press-event" not in s
            for s in (title, part, chapter)),
    "chapter rows retain active and live-update state":
        'add_class("active")' in chapter and 'ch["_row_title"] = t' in chapter,
    "chapter action is described": "Open chapter %s: %s" in chapter,
    "flat styling suppresses theme chrome":
        ".nvflatbtn {" in text and "background-image: none; box-shadow: none" in text,
    "modal scrims remain out of scope": "scrim = Gtk.EventBox()" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
