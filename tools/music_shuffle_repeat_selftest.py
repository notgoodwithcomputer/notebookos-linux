#!/usr/bin/env python3
"""Shuffle must honor Repeat-off at the end of one complete cycle."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import music  # noqa: E402


class Toggle:
    def __init__(self, active):
        self.active = active
    def get_active(self):
        return self.active


def main() -> None:
    app = music.Music.__new__(music.Music)
    tracks = [object(), object(), object()]
    app.shuffle = Toggle(True)
    app.repeat = Toggle(False)
    app._shuffle_queue_key = ()
    app._shuffle_remaining = []
    app._visible_tracks = lambda: tracks
    played, stopped = [], []
    app._play_track = lambda track: (played.append(track), setattr(app, "_current", track))
    app._stop_playback = lambda: stopped.append(True)
    app._current = tracks[0]
    for _ in range(3):
        app._advance(auto=True, direction=1)
    assert len(played) == 2 and len({id(t) for t in played}) == 2
    assert stopped == [True]

    app.repeat.active = True
    app._advance(auto=True, direction=1)
    assert len(played) == 3 and not stopped[1:]
    print("PASS shuffle plays one cycle then obeys Repeat-off")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
