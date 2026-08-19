#!/usr/bin/env python3
"""Disc Burner must not run a 60-second media probe on GTK's main loop."""

import ast
import copy
import os
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/burner.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(node for node in tree.body
           if isinstance(node, ast.ClassDef) and node.name == "DiscBurner")
methods = [copy.deepcopy(next(node for node in cls.body
                             if isinstance(node, ast.FunctionDef)
                             and node.name == name))
           for name in ("_on_add", "_duration_ready")]
module = ast.Module(body=methods, type_ignores=[])
ast.fix_missing_locations(module)


class Jobs:
    def start(self, key, worker, **kwargs):
        self.key, self.worker, self.done = key, worker, kwargs["on_done"]
        return object()


calls = []
scope = {
    "os": os,
    "HOME": "/home",
    "MUSIC_DIR": "/music",
    "VIDEOS_DIR": "/videos",
    "AUDIO_EXTS": (".wav",),
    "VIDEO_EXTS": (".mp4",),
    "MAX_TITLES": 9,
    "_t": lambda text: text,
    "nbpicker": SimpleNamespace(open_file=lambda *_a, **_k: "/music/song.wav"),
    "nbjobs": SimpleNamespace(REJECT="reject"),
    "media_duration": lambda path: calls.append(path) or 42.0,
}
exec(compile(module, str(SOURCE), "exec"), scope)


class Probe:
    AUDIO = "audio"; VIDEO = "video"
    _on_add = scope["_on_add"]
    _duration_ready = scope["_duration_ready"]
    def __init__(self):
        self.busy = False; self._add_pending = False; self.mode = self.AUDIO
        self.items = []; self._jobs = Jobs(); self.refreshes = 0
    def _refresh(self): self.refreshes += 1
    def _say(self, _text): pass


app = Probe()
app._on_add(None)
assert calls == [] and app.items == [] and app._add_pending
seconds = app._jobs.worker(object())
assert calls == ["/music/song.wav"] and seconds == 42.0
app._jobs.done(seconds)
assert not app._add_pending and app.items == [{
    "path": "/music/song.wav", "name": "song", "seconds": 42.0}]
print("PASS media probing leaves the UI callback and completes through its owner")
print("RESULT: ALL PASS")
