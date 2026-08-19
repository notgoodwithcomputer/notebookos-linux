#!/usr/bin/env python3
"""Calendar-supported store shapes also appear on desktop calendar cards."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import widgets  # noqa: E402

spec = importlib.util.spec_from_file_location("nb_calendar_app", DE / "calendar.py")
calendar_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calendar_app)


def read(payload):
    with tempfile.TemporaryDirectory(prefix="calendar-widget-") as td:
        path = Path(td) / "calendar.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        class BoardReader:
            _parse_iso = staticmethod(widgets.Widgets._parse_iso)
            _start_minutes = staticmethod(widgets.Widgets._start_minutes)
            _fmt_hhmm = staticmethod(widgets.Widgets._fmt_hhmm)
            _event_records = staticmethod(widgets.Widgets._event_records)

        board = BoardReader()
        with mock.patch.object(widgets, "CAL_FILE", str(path)):
            card = widgets.Widgets._load_events(board)
    owner = calendar_app.Calendar._event_list(payload)
    return owner, card


def main():
    checks = []
    event = {
        "date": "2026-08-15", "start": 9, "end": 10,
        "title": "Dentist", "cal": "Personal",
    }
    cases = [
        ("bare list", [event]),
        ("events wrapper", {"events": [event]}),
        ("id-keyed object", {"evt-1": event}),
        ("legacy wrapper", {"calendar": [event], "version": 1}),
    ]
    for label, payload in cases:
        owner, card = read(payload)
        ok = (owner is not None and len(owner) == 1
              and [item["title"] for item in card] == ["Dentist"])
        checks.append(ok)
        print(("PASS " if ok else "FAIL ") + label)

    owner, card = read({"unrelated": "data"})
    ok = owner is None and card == []
    checks.append(ok)
    print(("PASS " if ok else "FAIL ") + "foreign store")
    passed = sum(checks)
    print("RESULT: %d checks, ALL PASS (%d/%d)" %
          (len(checks), passed, len(checks)) if passed == len(checks) else
          "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
    raise SystemExit(passed != len(checks))


if __name__ == "__main__":
    main()
