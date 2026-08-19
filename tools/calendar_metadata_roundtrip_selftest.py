#!/usr/bin/env python3
"""Regression: Calendar preserves newer fields on valid event records."""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import calendar as calendar_app  # noqa: E402


def main():
    app = calendar_app.Calendar.__new__(calendar_app.Calendar)
    app.calendars = [{"name": "Personal", "color": "#000000"}]
    source = {
        "id": "event-1", "date": "2026-08-17", "start": 9.0,
        "end": 10.0, "title": "Planning", "cal": "Personal",
        "conference": {"room": "Blue", "dial_in": "123"},
        "sync_revision": 14,
    }
    event = app._norm_event(json.loads(json.dumps(source)))
    assert event is not None
    saved = json.loads(json.dumps(app._event_record(event)))
    assert saved["conference"] == {"room": "Blue", "dial_in": "123"}
    assert saved["sync_revision"] == 14
    assert "_loadkey" not in saved
    print("PASS newer event metadata survives normalization and save")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
