#!/usr/bin/env python3
"""Headless regression for rejected Cookbook undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import cookbook  # noqa: E402


class Stack:
    """Stand-in for a column's read/edit Gtk.Stack. _apply_undo_state asks both
    columns whether they are mid-edit, so that an undo taken with the caret in
    the ingredient editor leaves the editor open (it used to close it)."""
    def get_visible_child_name(self):
        return "view"


class Probe:
    _undo_restore = cookbook.Cookbook._undo_restore
    _apply_undo_state = cookbook.Cookbook._apply_undo_state
    _make_recipe = cookbook.Cookbook._make_recipe
    _visible_indices = cookbook.Cookbook._visible_indices
    _cat_indices = cookbook.Cookbook._cat_indices
    query = ""
    _valid_cat = cookbook.Cookbook._valid_cat
    _filter_for = cookbook.Cookbook._filter_for
    _cur = cookbook.Cookbook._cur
    _enter_panel_edit = lambda self, kind: None
    # A successful undo hands the save chip back to the document (the message
    # from a delete has been answered); neither is what this suite measures.
    _clear_flash = lambda self: None
    _show_save_state = lambda self: None
    ing_stack = Stack()
    steps_stack = Stack()

    def __init__(self, saves):
        self.cats = ["Current"]
        self.recipes = [self._make_recipe(title="Current", cat="Current")]
        self.active_cat = 1
        self.sel = 0
        self._extra = {"future": {"keep": True}}
        self._save_timer = None
        self.saves = list(saves)
        self.save_calls = 0
    def _undo_snapshot(self):
        return dict(copy.deepcopy(self._extra), cats=list(self.cats),
                    active_cat=self.active_cat, sel=self.sel,
                    recipes=copy.deepcopy(self.recipes))
    def rebuild_chips(self): pass
    def rebuild_list(self): pass
    def _refresh_editor(self): pass
    def _save_state(self):
        self.save_calls += 1
        return self.saves.pop(0)


failed = Probe([False, True])
before = failed._undo_snapshot()
passed = Probe([True])
target = {"cats": ["Older"], "active_cat": 1, "sel": 0,
          "recipes": [failed._make_recipe(title="Older", cat="Older")],
          "future": {"version": 2}}
checks = [
    (failed._undo_restore(target) is False
     and failed._undo_snapshot() == before,
     "failed undo restores library, selection, and extension metadata"),
    (failed.save_calls == 2,
     "failed undo best-effort repairs the durable cookbook"),
    (passed._undo_restore(target) is True
     and passed.recipes[0]["title"] == "Older"
     and passed._extra == {"future": {"version": 2}},
     "successful undo persists the full cookbook snapshot"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
