#!/usr/bin/env python3
"""Novel style-menu traversal wraps in both directions."""
import os
import sys
from types import SimpleNamespace

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import novel  # noqa: E402


class Item:
    def __init__(self): self.focused = False
    def grab_focus(self): self.focused = True


def target(key, state=0):
    app = novel.Novel.__new__(novel.Novel)
    app._style_items = [Item(), Item(), Item()]
    assert app._on_style_item_key(
        app._style_items[0], SimpleNamespace(keyval=key, state=state))
    return next(i for i, item in enumerate(app._style_items) if item.focused)


def main():
    assert target(novel.Gdk.KEY_Tab) == 1
    assert target(novel.Gdk.KEY_Down) == 1
    assert target(novel.Gdk.KEY_ISO_Left_Tab) == 2
    assert target(novel.Gdk.KEY_Up) == 2
    assert target(novel.Gdk.KEY_Tab,
                  novel.Gdk.ModifierType.SHIFT_MASK) == 2
    print("PASS Tab/Shift+Tab and arrows wrap within the style popup")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
