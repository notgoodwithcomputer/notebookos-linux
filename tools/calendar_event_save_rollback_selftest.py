#!/usr/bin/env python3
"""Display-free model rollback for a failed Calendar dialog save."""
import datetime
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="calendar-save-"))
import calendar as appmod  # noqa: E402


app = appmod.Calendar.__new__(appmod.Calendar)
first = {"title": "Class", "date": datetime.date(2026, 8, 15), "series": "s"}
second = {"title": "Class", "date": datetime.date(2026, 8, 22), "series": "s"}
app.events = [first, second]
app.sel = datetime.date(2026, 8, 15)
app.cur_y, app.cur_m = 2026, 8
marker = object()
app.cals_on = {"Personal": False, "Personal_area": marker}
app._seen = {"before-token"}
app._orphans = [{"future": "record"}]
snapshot = app._event_attempt_snapshot()

first["title"] = "Changed"
second["title"] = "Changed"
app.events.append({"title": "Phantom", "date": datetime.date(2026, 8, 29)})
app.sel = datetime.date(2027, 1, 1)
app.cals_on["Personal"] = True
app._seen.add("concurrent-task-token")
app._orphans.append({"adopted": "during failed merge"})
app._restore_event_attempt(snapshot)

assert app.events == [first, second] and first["title"] == "Class"
assert second["title"] == "Class" and len(app.events) == 2
assert app.events[0] is first and app.events[1] is second
assert app.sel == datetime.date(2026, 8, 15)
assert app.cals_on == {"Personal": False, "Personal_area": marker}
assert app._seen == {"before-token"}
assert app._orphans == [{"future": "record"}]
print("PASS failed event save restores a whole series and removes phantom rows")
print("PASS rollback preserves the dialog's existing-event identity")
print("PASS rollback restores merge tokens and orphans with the event rows")
print("RESULT: PASS")
