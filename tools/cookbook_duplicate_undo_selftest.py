#!/usr/bin/env python3
"""Recipe duplication is one structural undo step, never typing debounce."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import cookbook  # noqa: E402


class Undo:
    def __init__(self):
        self.calls = []
    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))
    def commit(self):
        self.calls.append(("commit", None))


def main() -> None:
    app = cookbook.Cookbook.__new__(cookbook.Cookbook)
    app.recipes = [{"title": "Soup", "ing": "", "steps": ""}]
    app.sel = 0
    app.undo = Undo()
    app._cur = lambda: app.recipes[app.sel]
    app.rebuild_list = lambda: None
    app._refresh_editor = lambda: None
    app._touch = lambda: app.undo.calls.append(("touch", None))
    app._duplicate_current()
    assert len(app.recipes) == 2 and app.sel == 1
    assert app.undo.calls[0] == ("checkpoint", "Duplicate Recipe")
    assert app.undo.calls[-1] == ("commit", None)
    print("PASS duplicate recipe commits its own structural undo boundary")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
