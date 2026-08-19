#!/usr/bin/env python3
"""Workout-supported store shapes render on the desktop Workout tile."""

import json
from pathlib import Path
import sys
import tempfile
import time
import types
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import widgets  # noqa: E402
import workout  # noqa: E402


class BoardReader:
    _workout_days = staticmethod(widgets.Widgets._workout_days)
    _wo_streak = staticmethod(widgets.Widgets._wo_streak)


def read(payload):
    with tempfile.TemporaryDirectory(prefix="workout-widget-") as td:
        path = Path(td) / "workout.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        owner = types.SimpleNamespace()
        with mock.patch.object(workout, "STORE", str(path)), \
                mock.patch.object(widgets, "WORKOUT_FILE", str(path)):
            app_data = workout.Workout._load(owner)
            card = widgets.Widgets._load_workout(BoardReader())
    return app_data, card


def main():
    checks = []
    today = time.strftime("%Y-%m-%d")
    exercise = {"id": "push", "name": "Push-ups", "sets": 3, "reps": 10}
    normal = {
        "exercises": [exercise],
        "log": {today: {"push": [10, 10]}},
        "goals": {today: 3},
    }
    keyed = {
        "exercises": {"push": exercise},
        "log": [{today: {"push": [10, 10]}}],
        "goals": {"days": {today: 3}},
    }
    for label, payload in (("normal store", normal),
                           ("repair-compatible store", keyed)):
        app_data, card = read(payload)
        ok = ([item["name"] for item in app_data["exercises"]] == ["Push-ups"]
              and card["rows"] == [("Push-ups", 2, 3)]
              and card["done"] == 2 and card["goal"] == 3)
        checks.append(ok)
        print(("PASS " if ok else "FAIL ") + label)
    passed = sum(checks)
    print("RESULT: %d checks, ALL PASS (%d/%d)" %
          (len(checks), passed, len(checks)) if passed == len(checks) else
          "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
    raise SystemExit(passed != len(checks))


if __name__ == "__main__":
    main()
