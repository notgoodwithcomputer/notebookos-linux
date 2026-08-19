#!/usr/bin/env python3
"""Journal format controls speak their current action or disabled reason."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import journal  # noqa: E402


class Acc:
    def __init__(self): self.name = None
    def set_name(self, text): self.name = text


class Button:
    def __init__(self): self.on = None; self.tip = None; self.acc = Acc()
    def set_sensitive(self, on): self.on = on
    def set_tooltip_text(self, text): self.tip = text
    def get_accessible(self): return self.acc


def main():
    app = journal.Journal.__new__(journal.Journal)
    app._quote_btn, app._bullet_btn = Button(), Button()
    app._fmt_btns = [app._quote_btn, app._bullet_btn]
    for on in (False, True, False):
        app._set_fmt_enabled(on)
        for btn in app._fmt_btns:
            assert btn.on is on and btn.tip == btn.acc.name
    assert "Open a journal entry" in app._quote_btn.tip
    print("PASS Journal format sensitivity, tooltip and ATK name stay aligned")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
