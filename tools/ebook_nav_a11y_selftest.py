#!/usr/bin/env python3
"""E-book navigation exposes boundary states consistently to AT."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import ebook  # noqa: E402


class Acc:
    def __init__(self): self.name = None
    def set_name(self, value): self.name = value


class Button:
    def __init__(self): self.on = None; self.tip = None; self.acc = Acc()
    def set_sensitive(self, value): self.on = value
    def set_tooltip_text(self, value): self.tip = value
    def get_accessible(self): return self.acc


class Label:
    def set_text(self, _value): pass


def state(total, page):
    app = ebook.EbookReader.__new__(ebook.EbookReader)
    app._page_total, app._page = total, page
    app._mode = "epub" if total else "empty"
    app._prev_btn = Button(); app._next_btn = Button()
    app._smaller_btn = Button(); app._larger_btn = Button()
    app._page_lbl = Label()
    app._update_nav()
    return app._prev_btn, app._next_btn


def main():
    for total, page, prev_on, next_on in (
            (0, 0, False, False), (3, 0, False, True),
            (3, 1, True, True), (3, 2, True, False)):
        prev, nxt = state(total, page)
        assert (prev.on, nxt.on) == (prev_on, next_on)
        assert prev.tip == prev.acc.name and nxt.tip == nxt.acc.name
    print("PASS page actions and boundary reasons have matching ATK names")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
