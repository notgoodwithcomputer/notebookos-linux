#!/usr/bin/env python3
"""Regression: a failed playlist rename rolls the visible model back."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import music  # noqa: E402


def main():
    app = music.Music.__new__(music.Music)
    app._playlists = ["Road Trip"]
    app._playlist_tracks = {"Road Trip": []}
    app._current_playlist = None
    app.view = "songs"
    attempts = []
    app._save = lambda: (attempts.append(1) or False)
    app._apply_rename("Road Trip", "Holiday")
    assert app._playlists == ["Road Trip"]
    assert set(app._playlist_tracks) == {"Road Trip"}
    assert len(attempts) == 2  # failed action, then best-effort restored state
    print("PASS failed playlist rename rolls back to durable identity")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
