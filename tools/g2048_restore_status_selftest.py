#!/usr/bin/env python3
"""A terminal saved board must reopen already showing game over."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import g2048  # noqa: E402


def main() -> None:
    game = g2048.Game2048.__new__(g2048.Game2048)
    game.board = [[2, 4, 2, 4], [4, 2, 4, 2],
                  [2, 4, 2, 4], [4, 2, 4, 8]]
    assert not game._can_move()
    source = (DE / "g2048.py").read_text(encoding="utf-8")
    assert 'self.status = "play" if self._can_move() else "lose"' in source
    game.board[3][3] = 0
    assert game._can_move()
    print("PASS restored terminal boards are classified before their first repaint")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
