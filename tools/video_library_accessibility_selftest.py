#!/usr/bin/env python3
"""Static guards for Video transition and media-bin keyboard controls."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/video.py").read_text()
palette = text[text.index("        for i, (icon, name) in enumerate(TRANSITIONS):"):
               text.index("        tr.pack_start(grid")]
media = text[text.index("        for i, m in enumerate(self._bin):"):
             text.index("        scroll.add(lst)")]
checks = {
    "transition choices are native buttons": "evt = Gtk.Button()" in palette,
    "transitions activate through clicked": 'evt.connect("clicked", self._on_transition_click' in palette,
    "transition artwork and state handles remain":
        'add_class("transcell")' in palette and "self._trans_cells[icon] = cell" in palette,
    "transition actions are described": "Apply %s transition" in palette,
    "media rows are native buttons": "evt = Gtk.Button()" in media,
    "media rows activate through clicked": 'evt.connect("clicked"' in media,
    "media selection artwork remains": 'add_class("binsel")' in media,
    "media actions include name and kind": "Select %s (%s)" in media,
    "stage-one controls are not pointer-only":
        all("Gtk.EventBox" not in s and "button-press-event" not in s
            for s in (palette, media)),
    "neutral wrapper preserves custom artwork":
        ".videohit {" in text and "background-image: none; box-shadow: none" in text,
    "hover feedback follows native wrappers":
        ".binhit:hover .binrow" in text and ".transhit:hover .transcell" in text,
    "later Video interaction stages remain explicit":
        "def _lane_click_wrap" in text and "def _round(" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
