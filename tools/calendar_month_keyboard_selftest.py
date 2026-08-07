#!/usr/bin/env python3
"""Headless behavioral checks for Calendar's roving month-grid keyboard model."""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

DE = Path(__file__).resolve().parents[1] / \
    "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
sys.modules.pop("calendar", None)
import calendar as app  # noqa: E402

ok = True


def check(name, condition):
    global ok
    print(("PASS " if condition else "FAIL ") + name)
    ok &= condition


check("month delta clamps January 31 into February",
      app._add_months(date(2025, 1, 31), 1) == date(2025, 2, 28))
check("month delta respects leap day",
      app._add_months(date(2024, 1, 31), 1) == date(2024, 2, 29))
check("month delta crosses years",
      app._add_months(date(2025, 12, 30), 1) == date(2026, 1, 30))


def bare(day):
    obj = app.Calendar.__new__(app.Calendar)
    obj.sel = day; obj.cur_y = day.year; obj.cur_m = day.month
    obj.view = "month"; obj.refreshes = 0; obj.focuses = 0
    obj._refresh = lambda: setattr(obj, "refreshes", obj.refreshes + 1)
    obj.month_grid = SimpleNamespace(
        grab_focus=lambda: setattr(obj, "focuses", obj.focuses + 1))
    return obj


def press(obj, key):
    return app.Calendar._on_month_grid_key(
        obj, None, SimpleNamespace(keyval=key, state=0))


w = bare(date(2026, 8, 5))
check("Right advances one day", press(w, app.Gdk.KEY_Right) and w.sel == date(2026, 8, 6))
check("Down advances one week", press(w, app.Gdk.KEY_Down) and w.sel == date(2026, 8, 13))
check("Home moves to Monday", press(w, app.Gdk.KEY_Home) and w.sel.weekday() == 0)
check("End moves to Sunday", press(w, app.Gdk.KEY_End) and w.sel.weekday() == 6)
check("navigation restores the single grid focus", w.focuses == 4)
check("navigation refreshes once per key", w.refreshes == 4)

w = bare(date(2025, 1, 31))
check("Page Down clamps and changes visible month",
      press(w, app.Gdk.KEY_Page_Down) and
      (w.sel, w.cur_y, w.cur_m) == (date(2025, 2, 28), 2025, 2))
w = bare(date(2026, 8, 5))
check("Enter opens selected Day view",
      press(w, app.Gdk.KEY_Return) and w.view == "day" and w.refreshes == 1)
check("unhandled keys remain available to GTK",
      app.Calendar._on_month_grid_key(
          w, None, SimpleNamespace(keyval=app.Gdk.KEY_a, state=0)) is False)

source = (DE / "calendar.py").read_text()
check("month grid is one focusable control", "grid.set_can_focus(True)" in source)
check("individual month cells remain outside the Tab chain",
      "ev.set_can_focus(True)" not in source[source.index("    def _month_cell("):
                                              source.index("    # -------------------------------------------------------------- day/week")])

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
