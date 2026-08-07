#!/usr/bin/env python3
"""Static guards for native Language course-path navigation controls."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/language.py").read_text()
course = text[text.index("        evt = Gtk.Button()"):
              text.index("    # ==================================================================\n    # course:")]
skill = text[text.index("    def _skill_node("):text.index("    def _test_node(")]
test = text[text.index("    def _test_node("):text.index("    def _node_label(")]
checks = {
    "course cards are native buttons": "Gtk.Button()" in course,
    "course cards activate via clicked": 'evt.connect("clicked"' in course,
    "skill nodes are native buttons": "Gtk.Button()" in skill,
    "skill nodes activate via clicked": 'evt.connect("clicked"' in skill,
    "test nodes are native buttons": "Gtk.Button()" in test,
    "test nodes activate via clicked": 'evt.connect("clicked"' in test,
    "primary navigation is no longer pointer-only":
        all("Gtk.EventBox" not in s and "button-press-event" not in s
            for s in (course, skill, test)),
    "locked nodes remain actionable and explain activation":
        "locked; activate for requirements" in skill and
        "locked; activate for requirements" in test and
        "set_sensitive(False)" not in skill + test,
    "custom node dimensions remain intact":
        "set_size_request(NODE, NODE)" in skill and "set_size_request(104, 60)" in test,
    "flat wrapper preserves custom artwork":
        ".pathhit {" in text and "background-image: none; box-shadow: none" in text,
    "course hover feedback follows the native wrapper":
        ".coursehit:hover .coursecard" in text,
    "modal scrim remains out of scope": "scrim = Gtk.EventBox()" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
