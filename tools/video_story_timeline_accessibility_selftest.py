#!/usr/bin/env python3
"""Static guards for Video storyboard and timeline keyboard controls."""
from pathlib import Path

text = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/video.py").read_text()
story = text[text.index("    def _story_card("):text.index("    def _card_mat(")]
add = text[text.index("    def _story_add_card("):text.index("    # Timeline scale:")]
lane = text[text.index("    def _lane_click_wrap("):text.index("    def _lane_chip(")]
checks = {
    "story cards are native buttons": "evt = Gtk.Button()" in story,
    "story selection uses clicked semantics": 'evt.connect("clicked"' in story,
    "story actions name clip number and title": "Select clip %d: %s" in story,
    "add card is a native button": "evt = Gtk.Button()" in add,
    "add card explains ready and prerequisite states":
        "Add selected media" in add and "Select media before adding a clip" in add,
    "timeline lane targets are native buttons": "evt = Gtk.Button()" in lane,
    "timeline targets use clicked semantics": 'evt.connect("clicked"' in lane,
    "timeline actions name lane, clip, and content": "%s lane, clip %d: %s" in lane,
    "background music action is described": "Select background music: %s" in text,
    "stage-three controls are not pointer-only":
        all("Gtk.EventBox" not in s and "button-press-event" not in s
            for s in (story, add, lane)),
    "selected artwork remains intact":
        'add_class("storysel")' in story and 'add_class("lanesel")' in text,
    "native hover retains Papertone feedback":
        ".storyhit:hover .storycell" in text and ".lanehit:hover .lanecell" in text,
}
ok = True
for name, passed in checks.items():
    print(("PASS " if passed else "FAIL ") + name)
    ok &= passed
print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
