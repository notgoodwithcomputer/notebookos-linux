#!/usr/bin/env python3
"""Sprite preview control presents the action it will perform next."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import gbasdk  # noqa: E402


class Acc:
    def __init__(self): self.name = None
    def set_name(self, value): self.name = value


class Button:
    def __init__(self): self.child = "old"; self.tip = None; self.acc = Acc()
    def get_child(self): return self.child
    def remove(self, _child): self.child = None
    def add(self, child): self.child = child
    def set_tooltip_text(self, value): self.tip = value
    def get_accessible(self): return self.acc
    def show_all(self): pass


def main():
    old_image = gbasdk.nbicons.image
    gbasdk.nbicons.image = lambda name, *_a: name
    try:
        app = gbasdk.GbaSdk.__new__(gbasdk.GbaSdk)
        app._play_btn = Button()
        app._sync_preview_button(True)
        assert app._play_btn.child == "stopsq"
        assert "Stop preview" in app._play_btn.tip
        assert app._play_btn.tip == app._play_btn.acc.name
        app._sync_preview_button(False)
        assert app._play_btn.child == "play"
        assert "Preview the animation" in app._play_btn.tip
        assert app._play_btn.tip == app._play_btn.acc.name
        print("PASS preview glyph, tooltip and ATK action track playback")
        print("RESULT: PASS")
        return 0
    finally:
        gbasdk.nbicons.image = old_image


if __name__ == "__main__":
    raise SystemExit(main())
