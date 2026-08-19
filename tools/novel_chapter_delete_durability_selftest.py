#!/usr/bin/env python3
"""Headless control-flow regression for failed Novel chapter deletion."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import novel  # noqa: E402


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(save_ok):
    app = novel.Novel.__new__(novel.Novel)
    app.chapters = [
        {"num": "1", "title": "Opening", "buffer": object(), "wc": 4},
        {"num": "2", "title": "Ending", "buffer": object(), "wc": 6},
    ]
    app.active = 0
    app._total_words = 10
    app.undo = UndoProbe()
    snapshot = {"sentinel": "complete manuscript"}
    app._undo_snapshot = lambda: snapshot
    app._show_buffer = lambda _buffer: None
    app._place_cursor_body = lambda _buffer: None
    app._refresh_chapter_list = lambda: None
    app._recount = lambda: None
    app._save_state = lambda: save_ok
    app.restored = None
    app._restore = lambda state: setattr(app, "restored", state)
    app._init_counts = lambda: None
    return app


app = bare(False)
app._delete_chapter(0, app.chapters[0])
assert app.restored == {"sentinel": "complete manuscript"}, app.restored
assert app.undo.calls[-1] == ("commit", None)
print("PASS failed chapter deletion restores the full serialized manuscript")

app = bare(True)
app._delete_chapter(0, app.chapters[0])
assert len(app.chapters) == 1 and app.chapters[0]["title"] == "Ending"
assert app.restored is None
print("PASS durable chapter deletion remains committed")


def bare_part(save_ok):
    app = novel.Novel.__new__(novel.Novel)
    app.parts = ["Part One", "Part Two"]
    app.chapters = [
        {"part": 0, "title": "Opening"},
        {"part": 1, "title": "Ending"},
    ]
    app.undo = UndoProbe()
    snapshot = {"sentinel": "parts and chapter ownership"}
    app._undo_snapshot = lambda: snapshot
    app._refresh_chapter_list = lambda: None
    app._recount = lambda: None
    app._save_state = lambda: save_ok
    app.restored = None
    app._restore = lambda state: setattr(app, "restored", state)
    app._init_counts = lambda: None
    return app


app = bare_part(False)
app._remove_part(0)
assert app.restored == {"sentinel": "parts and chapter ownership"}
assert app.undo.calls[-1] == ("commit", None)
print("PASS failed part deletion restores the full serialized manuscript")

app = bare_part(True)
app._remove_part(0)
assert app.parts == ["Part Two"]
assert [c["part"] for c in app.chapters] == [0, 0]
assert app.restored is None
print("PASS durable part deletion remains committed")
print("RESULT: PASS")
