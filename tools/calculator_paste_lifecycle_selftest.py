#!/usr/bin/env python3
"""Headless regression for a clipboard reply delivered after Calculator closes."""
import ast
import os
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/calculator.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef)
              and node.name == "_paste_expression")


class Clipboard:
    callback = None

    @classmethod
    def get(cls, _selection):
        return cls()

    def request_text(self, callback):
        type(self).callback = callback


namespace = {
    "Gtk": types.SimpleNamespace(Clipboard=Clipboard),
    "Gdk": types.SimpleNamespace(SELECTION_CLIPBOARD=object()),
}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class FakeCalculator:
    _paste_expression = namespace["_paste_expression"]

    def __init__(self):
        self._closed = False
        self.expr = "original"
        self.error = True
        self.refreshes = 0

    @staticmethod
    def _clipboard_expression(text):
        return text

    def _refresh(self):
        self.refreshes += 1


app = FakeCalculator()
app._paste_expression()
assert callable(Clipboard.callback)
app._closed = True
Clipboard.callback(None, "1+2")
assert app.expr == "original" and app.error is True
assert app.refreshes == 0, "late clipboard reply must not repaint a closed window"
print("PASS Calculator drops clipboard replies delivered after close")
print("RESULT: PASS")
