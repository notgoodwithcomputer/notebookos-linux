#!/usr/bin/env python3
"""Headless regression for rejected Music playlist undo persistence."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import music  # noqa: E402


class Probe:
    _undo_snapshot = music.Music._undo_snapshot
    _restore_undo_snapshot = music.Music._restore_undo_snapshot
    _apply_undo_snapshot = music.Music._apply_undo_snapshot

    def __init__(self, saves):
        self._playlists = ["Current"]
        self._playlist_tracks = {"Current": [{"path": "/music/current.ogg"}]}
        self._current_playlist = "Current"
        self.view = "songs"
        self._loaded_path = "/music/playing.ogg"
        self._playing = True
        self.saves = list(saves)
        self.save_calls = 0

    @staticmethod
    def _track_dict(track): return copy.deepcopy(track)
    @staticmethod
    def _link_track(track): return track
    def _save(self):
        self.save_calls += 1
        return self.saves.pop(0)
    def _refresh_transport(self): pass
    def _mark_playing_row(self): pass


target = {"playlists": ["Older"],
          "tracks": {"Older": [{"path": "/music/older.ogg"}]},
          "playlist": "Older", "view": "albums"}
failed = Probe([False, True])
before = failed._undo_snapshot()
passed = Probe([True])
checks = [
    (failed._restore_undo_snapshot(target) is False
     and failed._undo_snapshot() == before,
     "failed undo restores playlists, selection, and open view"),
    (failed.save_calls == 2 and failed._loaded_path == "/music/playing.ogg"
     and failed._playing is True,
     "failed undo repairs disk without disturbing the playback engine"),
    (passed._restore_undo_snapshot(target) is True
     and passed._current_playlist == "Older" and passed.view == "albums",
     "successful playlist undo persists the restored view"),
    (passed._loaded_path == "/music/playing.ogg" and passed._playing is True,
     "successful playlist undo also leaves playback uninterrupted"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
