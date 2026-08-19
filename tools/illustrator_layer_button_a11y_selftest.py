#!/usr/bin/env python3
"""Dynamic layer actions expose the same message to hover and AT."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import illustrator  # noqa: E402


class Accessible:
    def __init__(self): self.name = None
    def set_name(self, text): self.name = text


class Button:
    def __init__(self): self.tip = None; self.acc = Accessible()
    def set_tooltip_text(self, text): self.tip = text
    def get_accessible(self): return self.acc


def main():
    btn = Button()
    for message in ("Bring layer forward",
                    "This layer is already at the front.",
                    "Bring layer forward"):
        illustrator.Illustrator._button_message(btn, message)
        assert btn.tip == message and btn.acc.name == message
    print("PASS dynamic layer tooltip and accessible action stay identical")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
