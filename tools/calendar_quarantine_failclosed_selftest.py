#!/usr/bin/env python3
"""Calendar's two stores abort writes when protective moves fail."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import calendar as cal  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-calendar-q-") as root:
    events = os.path.join(root, "calendar.json")
    calendars = os.path.join(root, "calendars.json")
    Path(events).write_text('{"foreign":"events"}', encoding="utf-8")
    Path(calendars).write_text('{"foreign":"calendars"}', encoding="utf-8")
    old_events, old_calendars = cal.EVENTS_FILE, cal.CALENDARS_FILE
    cal.EVENTS_FILE, cal.CALENDARS_FILE = events, calendars
    app = cal.Calendar.__new__(cal.Calendar)
    app.events = []
    app.calendars = [{"name": "Personal", "color": "#123456"}]
    app._events_quarantine = True
    app._calendars_quarantine = True
    app._save_warned = app._calendars_save_warned = False
    app._flash_status = lambda _message: None
    real_q = cal._quarantine_store
    real_write = cal.nbapp.atomic_write_json
    writes = []
    cal._quarantine_store = lambda _path: False
    cal.nbapp.atomic_write_json = lambda *_args, **_kw: writes.append(_args)
    try:
        assert app._save_events() is False
        assert app._save_calendars() is False
    finally:
        cal._quarantine_store = real_q
        cal.nbapp.atomic_write_json = real_write
        cal.EVENTS_FILE, cal.CALENDARS_FILE = old_events, old_calendars
    assert not writes
    assert app._events_quarantine and app._calendars_quarantine
    assert Path(events).read_text() == '{"foreign":"events"}'
    assert Path(calendars).read_text() == '{"foreign":"calendars"}'
    print("PASS Calendar keeps both stores and retry flags after failed preservation")

print("RESULT: ALL PASS")
