#!/usr/bin/env python3
"""Headless regression for failed Academic Notes lecture deletion."""
import copy
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import academics  # noqa: E402


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_ok):
    app = academics.Academics.__new__(academics.Academics)
    app.classes = [{"label": "Math", "color": "#000", "meets": []}]
    app.lectures = [{"cls": 0, "num": "01", "title": "Limits",
                     "notes": "irreplaceable"}]
    app.homework = []
    app.active = 0
    app._notes_timer = None
    app._confirm = lambda *_args: True
    app.undo = UndoProbe()
    app._undo_snapshot = lambda: {
        "classes": copy.deepcopy(app.classes),
        "lectures": copy.deepcopy(app.lectures),
        "homework": [], "active": app.active}
    app._class_of = lambda lecture: app.classes[lecture["cls"]]
    app._prune_empty_classes = lambda: app.classes.clear()
    app._refresh_sidebar = lambda: None
    app._refresh_canvas = lambda: None
    app._save_to_disk = lambda: save_ok
    app.rolled_back = False
    def rollback(state):
        app.rolled_back = True
        app.classes = state["classes"]
        app.lectures = state["lectures"]
        app.active = state["active"]
    app._rollback_failed_save = rollback
    return app


app = bare(False)
app._delete_lecture()
assert app.rolled_back and app.lectures[0]["notes"] == "irreplaceable"
assert app.active == 0
print("PASS failed lecture deletion restores the saved snapshot")

app = bare(True)
app._delete_lecture()
assert app.lectures == [] and app.classes == [] and app.active == -1
assert app.rolled_back is False
print("PASS durable lecture deletion remains committed")
print("RESULT: PASS")
