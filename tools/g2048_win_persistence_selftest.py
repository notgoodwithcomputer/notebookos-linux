#!/usr/bin/env python3
"""Regression: closing on the 2048 banner preserves the resumable board."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import g2048  # noqa: E402


def saved_for(status):
    game = g2048.Game2048.__new__(g2048.Game2048)
    game.status = status
    game.board = [[2048, 4, 0, 0], [0, 0, 0, 0],
                  [0, 0, 0, 0], [0, 0, 0, 0]]
    game.score, game.best = 22000, 22000
    game._extra = {}
    game._quarantine_pending = False
    captured = []
    old_write = g2048.nbapp.atomic_write_json
    try:
        g2048.nbapp.atomic_write_json = lambda _path, data: captured.append(data)
        assert game._save_best()
    finally:
        g2048.nbapp.atomic_write_json = old_write
    return captured[0]


def main():
    winning = saved_for("win")
    assert winning["board"][0][0] == 2048 and winning["score"] == 22000
    lost = saved_for("lose")
    assert "board" not in lost and "score" not in lost
    # Terminal verdict for the release runner (run_all_gates SUCCESSWORD): a
    # stream of PASS lines with a zero exit is not a report it will trust —
    # a suite that dies half way prints those too.
    print("PASS win boards remain resumable while terminal losses are retired")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
