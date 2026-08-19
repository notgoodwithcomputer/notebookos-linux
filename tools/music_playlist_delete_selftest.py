#!/usr/bin/env python3
"""Display-free regression for durable playlist deletion."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import music  # noqa: E402


class Undo:
    def __init__(self): self.calls = []
    def checkpoint(self, label): self.calls.append(("checkpoint", label))
    def commit(self): self.calls.append(("commit", None))


class Box:
    def remove(self, _row): pass


class Empty:
    def set_no_show_all(self, _value): pass
    def show(self): pass


class Probe:
    _delete_current_playlist = music.Music._delete_current_playlist
    _remove_playlist = music.Music._remove_playlist
    def __init__(self, save_ok):
        self._playlists = ["Mix"]
        self._playlist_tracks = {"Mix": [{"path": "/song.ogg"}]}
        self._playlist_rows = [object()]
        self._current_playlist = "Mix"
        self.view = "playlist"
        self._pl_box = Box(); self._none = Empty()
        self.undo = Undo(); self.flashes = []
        self._save_ok = save_ok
    def _track_dict(self, track): return dict(track)
    def _undo_snapshot(self):
        return {"playlists": list(self._playlists),
                "tracks": {k: [dict(t) for t in v]
                           for k, v in self._playlist_tracks.items()},
                "playlist": self._current_playlist, "view": self.view}
    def _restore_undo_snapshot(self, state):
        self._playlists = list(state["playlists"])
        self._playlist_tracks = state["tracks"]
        self._current_playlist = state["playlist"]
        self.view = state["view"]
        self._save()
    def _apply_undo_snapshot(self, state):
        self._playlists = list(state["playlists"])
        self._playlist_tracks = state["tracks"]
        self._current_playlist = state["playlist"]
        self.view = state["view"]
    def _save(self): return self._save_ok
    def _select(self, view): self.view = view
    def _flash(self, text): self.flashes.append(text)


failed = Probe(False); failed._delete_current_playlist()
passed = Probe(True); passed._delete_current_playlist()
checks = [
    (failed._playlists == ["Mix"] and not failed.flashes,
     "failed save restores playlist and does not announce deletion"),
    (passed._playlists == [] and len(passed.flashes) == 1,
     "successful save removes playlist and announces success"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
