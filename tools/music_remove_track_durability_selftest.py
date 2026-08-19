#!/usr/bin/env python3
"""Headless regression for failed Music playlist-track removal."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import music  # noqa: E402


class Undo:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_ok):
    app = music.Music.__new__(music.Music)
    first = {"path": "/music/one.ogg"}
    second = {"path": "/music/two.ogg"}
    app._playlist_tracks = {"Mix": [first, second]}
    app.undo = Undo()
    app._save = lambda: save_ok
    app.view = None
    app._current_playlist = "Mix"
    app.populates = 0
    app._populate = lambda: setattr(app, "populates", app.populates + 1)
    return app, first, second


app, first, second = bare(False)
original = app._playlist_tracks["Mix"]
app._remove_from_playlist(first, "Mix")
assert app._playlist_tracks["Mix"] == [first, second]
assert app._playlist_tracks["Mix"] is not original
assert app.populates == 1 and app.undo.calls[-1] == ("commit", None)
print("PASS failed track removal restores playlist and visible rows")

app, first, second = bare(True)
app.view = "songs"
app._remove_from_playlist(first, "Mix")
assert app._playlist_tracks["Mix"] == [second]
assert app.undo.calls[-1] == ("commit", None)
print("PASS durable track removal remains committed")
print("RESULT: PASS")
