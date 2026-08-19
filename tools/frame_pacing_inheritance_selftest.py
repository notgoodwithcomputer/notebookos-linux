#!/usr/bin/env python3
"""Indirect app-window subclasses remain inside the repaint gate."""

import ast
import frame_pacing_check as gate


def main():
    tree = ast.parse("""
class BaseWindow(AppWindow): pass
class Middle(BaseWindow): pass
class Player(Middle):
    def tick(self): self.queue_draw()
class Canvas(DrawingArea):
    def tick(self): self.queue_draw()
class CanvasChild(Canvas): pass
""")
    got = gate._window_classes(tree)
    expected = {"BaseWindow", "Middle", "Player"}
    if not expected <= got:
        print("FAIL: indirect window inheritance was missed: %r" % got)
        return 1
    if {"Canvas", "CanvasChild"} & got:
        print("FAIL: small-widget inheritance was treated as a window: %r" % got)
        return 1
    player = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                  and n.name == "Player")
    if not any(gate._is_self_queue_draw(n) for n in ast.walk(player)):
        print("FAIL: indirect window repaint call was not recognized")
        return 1
    print("PASS: one- and two-hop AppWindow subclasses remain gated")
    print("PASS: indirect DrawingArea subclasses remain correctly scoped")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
