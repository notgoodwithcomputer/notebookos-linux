#!/usr/bin/env python3
"""Headless regression for Language's close-during-toast lifecycle."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/language.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_toast")


class Style:
    def add_class(self, _name):
        pass

    def remove_class(self, _name):
        pass


class Label:
    def __init__(self):
        self.text = ""
        self.parent = object()
        self.style = Style()

    def set_text(self, text):
        self.text = text

    def get_style_context(self):
        return self.style

    def get_parent(self):
        return self.parent


class FakeLanguage:
    def __init__(self):
        self._course_toast = Label()
        self._toast_id = 0
        self.delay = None
        self.callback = None

    def _lesson_later(self, delay, callback):
        self.delay = delay
        self.callback = callback


namespace = {}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)
FakeLanguage._toast = namespace["_toast"]

app = FakeLanguage()
app._toast("Hearts are full")
assert app._course_toast.text == "Hearts are full"
assert app.delay == 3200 and callable(app.callback), \
    "toast cleanup must use the lifecycle-owned scheduler"
assert "GLib.timeout_add" not in ast.unparse(method)
print("PASS Language toast cleanup is owned by the window lifecycle")
print("RESULT: PASS")
