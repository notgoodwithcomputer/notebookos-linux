#!/usr/bin/env python3
"""Headless regressions for 2048 New Game persistence failures."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import g2048  # noqa: E402


def bare(save_ok):
    app = g2048.Game2048.__new__(g2048.Game2048)
    app.board = [[2, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    app.score = 72
    app.status = "play"
    app._won_shown = False
    app._new_game_undo = None
    app._finish_anim_now = lambda: None
    app._add_random = lambda: None
    app._save_best = lambda: save_ok
    app.refreshes = 0
    app._refresh = lambda: setattr(app, "refreshes", app.refreshes + 1)
    return app


app = bare(False)
old_board = [row[:] for row in app.board]
app.new_game()
assert app.board == old_board and app.score == 72 and app._new_game_undo is None
assert app.refreshes == 2
print("PASS failed New Game restores the durable board and prior undo state")

app = bare(True)
old_board = [row[:] for row in app.board]
app.new_game()
assert app.board == [[0] * 4 for _ in range(4)] and app.score == 0
assert app._new_game_undo[0] == old_board
print("PASS durable New Game retains one-step undo")

app._save_best = lambda: False
new_board = [row[:] for row in app.board]
assert app.undo_new_game() is False
assert app.board == new_board and app._new_game_undo[0] == old_board
print("PASS failed Undo New Game restores replacement board and keeps retry")
print("RESULT: PASS")
