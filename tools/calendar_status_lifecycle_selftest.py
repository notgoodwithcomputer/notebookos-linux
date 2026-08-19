#!/usr/bin/env python3
"""Headless regression for Calendar's transient status source ownership."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/calendar.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_flash_status")


class FakeGLib:
    next_id = 1
    removed = []

    @classmethod
    def timeout_add_seconds(cls, _delay, _callback):
        source_id = cls.next_id
        cls.next_id += 1
        return source_id

    @classmethod
    def source_remove(cls, source_id):
        cls.removed.append(source_id)


namespace = {"GLib": FakeGLib}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=[method], type_ignores=[])), PATH, "exec"), namespace)


class Label:
    def __init__(self):
        self.text = ""

    def set_text(self, text):
        self.text = text


class FakeCalendar:
    _flash_status = namespace["_flash_status"]

    def __init__(self):
        self.status_lbl = Label()
        self._status_tok = 0
        self._status_timer = 0


app = FakeCalendar()
app._flash_status("Imported")
first = app._status_timer
app._flash_status("Not saved")
assert first in FakeGLib.removed
assert app._status_timer != first and app.status_lbl.text == "Not saved"
print("PASS Calendar replaces rather than accumulates status timers")
print("RESULT: PASS")
