#!/usr/bin/env python3
"""Headless regression for Writer's overlapping status-message timers."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/writer.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_flash")


class FakeGLib:
    callbacks = {}
    removed = []
    next_id = 1

    @classmethod
    def timeout_add(cls, _delay, callback):
        source_id = cls.next_id
        cls.next_id += 1
        cls.callbacks[source_id] = callback
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


class FakeWriter:
    _flash = namespace["_flash"]

    def __init__(self):
        self.status = Label()
        self._flash_timer = None
        self._flash_id = 0
        self.restores = 0

    def _update_status(self):
        self.restores += 1


writer = FakeWriter()
writer._flash("First")
first = writer._flash_timer
writer._flash("Second")
second = writer._flash_timer
assert first in FakeGLib.removed and writer.status.text == "Second"

# A callback may already be dispatched when source_remove runs. Its generation
# guard must keep it from clearing the newer message early.
assert FakeGLib.callbacks[first]() is False
assert writer.restores == 0 and writer._flash_timer == second
assert FakeGLib.callbacks[second]() is False
assert writer.restores == 1 and writer._flash_timer is None
print("PASS Writer keeps the newest flash and retires the owned timer")
print("RESULT: PASS")
