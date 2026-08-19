#!/usr/bin/env python3
"""Headless regression for failed Video Editor clip deletion."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import video  # noqa: E402


def bare(save_ok):
    app = video.VideoEditor.__new__(video.VideoEditor)
    app.clips = [{"name": "Opening", "transition": "fade"},
                 {"name": "Ending", "transition": "none"}]
    app._sel_cell = 0
    app._undo = []
    app._undo_names = []
    app._stop_playback = lambda reset: None
    app._push_undo = lambda name=None: (
        app._undo.append("snapshot"), app._undo_names.append(name))
    app._save_project = lambda: save_ok
    app.events = []
    app._render_all = lambda: app.events.append("render")
    app._highlight_palette = lambda value: app.events.append(("palette", value))
    app._load_props = lambda clip: app.events.append(
        ("props", clip.get("name") if clip else None))
    return app


app = bare(False)
opening = app.clips[0]
app._menu_delete()
assert app.clips[0] is opening and app._sel_cell == 0
assert app._undo == [] and app._undo_names == []
assert app.events[-1] == ("props", "Opening"), app.events
print("PASS failed clip deletion restores clip, selection, and undo history")

app = bare(True)
app._menu_delete()
assert [clip["name"] for clip in app.clips] == ["Ending"]
assert app._sel_cell is None and app._undo_names == ["Delete Clip"]
assert app.events[-1] == ("props", None), app.events
print("PASS durable clip deletion remains committed")
print("RESULT: PASS")
