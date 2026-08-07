#!/usr/bin/env python3
"""Static guards for native Music transport controls.

The substring checks below read CODE, not prose. The transport was rewritten
from an input-only Gtk.EventBox to a real Gtk.Button, and the comment recording
why still names the old approach -- so a bare `"Gtk.EventBox" not in factory`
was tripped by the sentence explaining that Gtk.EventBox is no longer used, and
this suite sat permanently red on a defect that had been fixed. Comments are
stripped first; a guard that cannot tell an implementation from a note about it
reports the documentation, not the code.
"""
import re
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/music.py").read_text()


def code_only(src):
    """Drop whole-line comments. Enough here: the strings being matched are
    identifiers, and none of them appears in a trailing comment in this file."""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


factory = code_only(text[text.index("    def _round("):
                         text.index("    def _refresh_transport(")])
playbar = code_only(text[text.index("    def _playbar("):
                         text.index("    def _round(")])
checks = {
    "transport factory creates native buttons": "button = Gtk.Button()" in factory,
    "transport controls use clicked semantics": 'button.connect("clicked"' in factory,
    "transport controls are not pointer-only wrappers":
        "Gtk.EventBox" not in factory and "button-press-event" not in factory,
    "circle sizes remain explicit": "button.set_size_request(size, size)" in factory,
    "play icon reference remains mutable": "self._play_img = img" in factory,
    "transport controls retain action tooltips":
        all(label in playbar for label in ("Previous track", "Play", "Next track")),
    "Papertone buttons suppress themed gradients and shadows":
        ".roundbtn {" in text and "background-image: none; box-shadow: none" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
