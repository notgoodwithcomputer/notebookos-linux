#!/usr/bin/env python3
"""Headless regression for Language progress-backed settings."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import language  # noqa: E402


class Probe:
    _toggle_hearts = language.Language._toggle_hearts
    _set_goal = language.Language._set_goal
    def __init__(self, progress, save_ok):
        self.progress = copy.deepcopy(progress)
        self.save_ok = save_ok
        self.refreshes = 0
    def _fill_hearts(self):
        self.progress["hearts"] = language.HEARTS_MAX
        self.progress["heart_time"] = 0
    def _save_progress(self): return self.save_ok
    def _refresh_after_setting(self): self.refreshes += 1


profile = {"hearts_on": False, "hearts": 1, "heart_time": 123, "goal": 20}
hearts_failed = Probe(profile, False)
goal_failed = Probe(profile, False)
passed = Probe(profile, True)
checks = [
    (hearts_failed._toggle_hearts() is None
     and hearts_failed.progress == profile and hearts_failed.refreshes == 1,
     "failed hearts write restores switch, count, and timer"),
    (goal_failed._set_goal(50) is False
     and goal_failed.progress["goal"] == 20,
     "failed daily-goal write restores the durable target"),
    (passed._set_goal(50) is True and passed.progress["goal"] == 50,
     "successful daily-goal write remains committed"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
