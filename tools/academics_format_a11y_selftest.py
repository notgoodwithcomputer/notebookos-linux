#!/usr/bin/env python3
"""Lecture-format actions expose the same live state to pointer and AT."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import academics  # noqa: E402


class Acc:
    def __init__(self): self.name = None
    def set_name(self, value): self.name = value


class Button:
    def __init__(self): self.on = None; self.tip = None; self.acc = Acc()
    def set_sensitive(self, value): self.on = value
    def set_tooltip_text(self, value): self.tip = value
    def get_accessible(self): return self.acc


def main():
    app = academics.Academics.__new__(academics.Academics)
    buttons = [Button() for _ in range(4)]
    (app._style_btn, app._highlight_btn,
     app._bullet_btn, app._number_btn) = buttons
    app._fmt_btns = buttons
    for have in (False, True, False):
        app._set_fmt_sensitive(have)
        assert all(b.on is have and b.tip == b.acc.name for b in buttons)
    assert all("Open a lecture" in b.tip for b in buttons)
    print("PASS lecture-format sensitivity, tooltip and ATK name stay aligned")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
