#!/usr/bin/env python3
"""Headless regression for Calendar's multi-store undo transaction."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import calendar as calendar_app  # noqa: E402


class Probe:
    _undo_snapshot = calendar_app.Calendar._undo_snapshot
    _undo_restore = calendar_app.Calendar._undo_restore

    def __init__(self, event_save):
        self.events = [{"summary": "Current"}]
        self.calendars = [{"name": "Home"}]
        self.cals_on = {"Home": True}
        self._orphans = []
        self._seen = {"current"}
        self._doc_path = "/docs/current.ics"
        self.sel = "current"
        self.cur_y, self.cur_m, self.view = 2026, 8, "month"
        self.event_save = event_save
        self.calendar_saves = 0
        self.event_saves = 0

    def _save_calendars(self):
        self.calendar_saves += 1
        return True
    def _save_events(self, merge=False):
        self.event_saves += 1
        return self.event_save if self.event_saves == 1 else True
    def _populate_cal_list(self): pass
    def _refresh(self): pass


target = ([{"summary": "Older"}], [{"name": "Work"}], {"Work": False},
          [], {"older"}, "/docs/older.ics", "older", 2025, 12, "day")
failed = Probe(False)
before = copy.deepcopy(failed.events)
passed = Probe(True)
checks = [
    (failed._undo_restore(target) is False and failed.events == before
     and failed._doc_path == "/docs/current.ics",
     "failed event-store undo restores model and document identity"),
    (failed.calendar_saves == 2 and failed.event_saves == 2,
     "failed undo restores both on-disk calendar projections"),
    (passed._undo_restore(target) is True
     and passed.events[0]["summary"] == "Older"
     and passed.cals_on == {"Work": False},
     "successful multi-store undo commits normally"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
