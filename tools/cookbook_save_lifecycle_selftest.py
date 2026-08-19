#!/usr/bin/env python3
"""Headless regression for a Cookbook autosave dispatched during close."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/cookbook.py")
source = open(PATH, encoding="utf-8").read()
tree = ast.parse(source)
# _mark_saved now renders the chip through _show_save_state/_dot instead of
# stamping the clock itself (the chip has to be able to say "Not saved", and a
# refresh must never claim a save that did not happen), so those two come along
# — the point of this suite, that nothing is written after close, is unchanged.
WANTED = ("_mark_saved", "_show_save_state", "_dot")
methods = [node for node in ast.walk(tree)
           if isinstance(node, ast.FunctionDef) and node.name in WANTED]
assert len(methods) == len(WANTED), [m.name for m in methods]
namespace = {"time": __import__("time"),
             "_t": lambda s: s,
             "GLib": type("GLib", (), {"markup_escape_text": staticmethod(
                 lambda s: s)})}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=methods, type_ignores=[])), PATH, "exec"), namespace)


class Label:
    def __init__(self):
        self.writes = []

    def set_markup(self, text):
        self.writes.append(text)


class FakeCookbook:
    _mark_saved = namespace["_mark_saved"]
    _show_save_state = namespace["_show_save_state"]
    _dot = namespace["_dot"]

    def __init__(self, closed):
        self._closed = closed
        self._save_timer = 7
        self.savestate = Label()
        self.saves = 0
        # What the chip renders from: only a write that reached the file sets
        # these, so a refresh can no longer invent a save time.
        self._saved_at = None
        self._save_failed = False
        self._recovery_dirty = False
        self._flash_until = 0.0

    def _save_state(self):
        self.saves += 1
        self._saved_at = "09:15"
        return True


closed = FakeCookbook(True)
assert closed._mark_saved() is False
assert closed.saves == 0 and closed.savestate.writes == []
assert closed._save_timer is None

open_app = FakeCookbook(False)
assert open_app._mark_saved() is False
assert open_app.saves == 1 and len(open_app.savestate.writes) == 1
assert open_app._save_timer is None
# The one write it does make names the time the bytes reached the file.
assert "09:15" in open_app.savestate.writes[0], open_app.savestate.writes
print("PASS Cookbook drops an autosave callback dispatched after close")
print("PASS the save chip reports the write, not the moment it was drawn")
print("RESULT: PASS")
