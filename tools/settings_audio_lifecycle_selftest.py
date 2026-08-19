#!/usr/bin/env python3
"""Headless regression for Settings' worker-owned audio-route change."""
import ast
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                    "opt/notebook/de/settings.py")
tree = ast.parse(open(PATH, encoding="utf-8").read())
names = {"_on_audio_out", "_choose_audio", "_audio_applied"}
methods = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name in names}

class Audio:
    calls = []
    @classmethod
    def choose(cls, key):
        cls.calls.append(key); return key

class Jobs:
    def __init__(self): self.starts = []
    def start(self, *args, **kwargs): self.starts.append((args, kwargs))

class NBJobs: REPLACE = "replace"

ns = {"nbaudio": Audio, "nbjobs": NBJobs}
exec(compile(ast.fix_missing_locations(
    ast.Module(body=list(methods.values()), type_ignores=[])), PATH, "exec"), ns)

class Combo:
    def get_active_id(self): return "hdmi"

class Job:
    def __init__(self): self.checked = 0
    def checkpoint(self): self.checked += 1

class App:
    _on_audio_out = ns["_on_audio_out"]
    _choose_audio = ns["_choose_audio"]
    _audio_applied = ns["_audio_applied"]
    def __init__(self):
        self._audio_jobs = Jobs(); self._audio_lock = threading.Lock()
        self._alive = True; self.pages = []
    def _reopen_page(self, page): self.pages.append(page)

app = App(); app._on_audio_out(Combo())
assert Audio.calls == [], "changed callback ran mixer work on the UI thread"
args, kwargs = app._audio_jobs.starts[0]
assert args[0] == "route" and kwargs["policy"] == NBJobs.REPLACE
job = Job(); assert args[1](job) == "hdmi" and job.checked == 1
kwargs["on_done"]("hdmi")
assert app.pages == ["Sound"] and not app._audio_save_error
app._alive = False; app._audio_applied(None)
assert app.pages == ["Sound"], "closed window accepted a late result"
print("PASS audio routing runs under a replaceable worker and drops late results")
print("RESULT: PASS")
