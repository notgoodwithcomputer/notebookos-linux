#!/usr/bin/env python3
"""Static guards for native Video preview transport controls."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/video.py").read_text()
factory = text[text.index("    def _round("):text.index("    def _update_preview(")]
preview = text[text.index("        # transport controls"):
               text.index("    def _round(")]
checks = {
    "actionable round controls are native buttons":
        "Gtk.Box() if cb is None else Gtk.Button()" in factory,
    "decorative no-callback branch remains supported": "if cb is None:" in factory,
    "transport controls activate via clicked": 'b.connect("clicked", cb)' in factory,
    "transport factory has no pointer-only wrapper":
        "Gtk.EventBox" not in factory and "button-press-event" not in factory,
    "exact requested geometry remains": "b.set_size_request(size, size)" in factory,
    "icon handle is still returned": "return b, img" in factory,
    "previous, play and next actions remain wired":
        all(cb in preview for cb in ("self._on_prev", "self._on_play", "self._on_next")),
    "play button handle remains retained": "self._play_w = play_w" in preview,
    "transport actions remain described":
        all(t in preview for t in ("Previous clip", '"Play"', "Next clip")),
    "Papertone circles suppress theme chrome":
        ".roundbtn {" in text and "background-image: none; box-shadow: none; padding: 0" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
