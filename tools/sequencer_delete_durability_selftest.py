#!/usr/bin/env python3
"""Headless regression for failed Sequencer clip removal."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import sequencer  # noqa: E402


def bare(save_ok):
    app = sequencer.Sequencer.__new__(sequencer.Sequencer)
    clip = {"s": 0.0, "e": 1.0, "wav": "take.wav"}
    app.tracks = [{"clips": [clip]}]
    app.sel = (0, clip)
    app._undo_stack = []
    app._undo_names = []
    app._redo_stack = ["redo-before"]
    app._redo_names = ["Redo Before"]
    app._remember = lambda name=None: (
        app._undo_stack.append("snapshot"), app._undo_names.append(name),
        app._redo_stack.clear(), app._redo_names.clear())
    app._validate_sel = lambda: setattr(app, "sel", None)
    app._save = lambda: save_ok
    app.events = []
    app._engine_changed = lambda: app.events.append("engine")
    app._sync_editor = lambda: app.events.append("editor")
    app.refresh = lambda: app.events.append("refresh")
    return app, clip


app, clip = bare(False)
app._delete_selected()
assert app.tracks[0]["clips"] == [clip] and app.sel == (0, clip)
assert app._undo_stack == [] and app._undo_names == []
assert app._redo_stack == ["redo-before"] and app._redo_names == ["Redo Before"]
print("PASS failed clip removal restores lane, selection, undo, and redo")

app, clip = bare(True)
app._delete_selected()
assert app.tracks[0]["clips"] == [] and app.sel is None
assert app._undo_names == ["Remove Clip"] and app._redo_stack == []
print("PASS durable clip removal remains committed")
print("RESULT: PASS")
