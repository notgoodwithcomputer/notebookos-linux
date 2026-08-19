#!/usr/bin/env python3
"""Headless regression for Music's transient-message timeout ownership."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/music.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
method = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_flash")


class FakeGLib:
    next_id = 1
    removed = []

    @classmethod
    def timeout_add(cls, _delay, _callback, _serial):
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


class FakeMusic:
    _flash = namespace["_flash"]

    def __init__(self):
        self._closed = False
        self._nowlbl = Label()
        self._flash_serial = 0
        self._flashing = False
        self._flash_timer = 0

    def _unflash(self, _serial):
        return False


app = FakeMusic()
app._flash("First")
first = app._flash_timer
app._flash("Second")
assert first in FakeGLib.removed
assert app._flash_timer != first and app._nowlbl.text == "Second"
assert app._flash_serial == 2
print("PASS Music replaces rather than accumulates flash restore timers")
print("RESULT: PASS")
