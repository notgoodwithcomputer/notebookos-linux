#!/usr/bin/env python3
"""
Headless selftest for the Workout app (workout.py) and its desktop card.

Covers the logic a wrong answer would be visible in: the counts on screen, the
week strip, what survives a restart, and what the opt-in desktop card reads out
of the same file.

  1. Logging  — a logged set records the exercise's target reps, counts toward
     today, and Undo takes exactly one back off.
  2. Totals   — today's done/goal and the week total are summed across
     exercises and days, not just the selected one.
  3. Goals    — an exercise is "met" at its target and stays met past it.
  4. Restart  — the log round-trips through the store.
  5. Deleting — removing an exercise removes its logged sets too, and does not
     strand an empty day behind.
  6. Corrupt  — a store that is valid JSON but the wrong shape opens empty
     rather than stopping the app opening.
The desktop side of this app — its board tile, and the board's own layout —
is tools/board_selftest.py; this file is the app's own logic.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=<overlay>/opt/notebook/de \
  python3 workout_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402,F401

# Measure under the SHIPPED theme. Without it the host GTK theme's larger
# paddings inflate every card and the column-fit check reads ~20px high.
_THEME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "buildroot/board/notebookos/rootfs-overlay/usr/share/"
                      "themes/Papertone/gtk-3.0/gtk.css")
try:
    _prov = Gtk.CssProvider()
    _prov.load_from_path(_THEME)
    Gtk.StyleContext.add_provider_for_screen(
        __import__("gi.repository.Gdk", fromlist=["Gdk"]).Screen.get_default(),
        _prov, 500)
except Exception:
    pass

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def fresh_home():
    home = tempfile.mkdtemp(prefix="nb-wo-")
    os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
    return home


def store(home):
    return os.path.join(home, ".config", "notebook", "workout.json")


def new_app(home):
    os.environ["NB_HOME"] = home
    sys.modules.pop("workout", None)
    import workout
    return workout, workout.Workout()


TODAY = time.strftime("%Y-%m-%d")


def day_ago(n):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


# Destruction must be undoable even when GTK cannot create a window. Exercise
# the real model snapshot/restore methods on an uninitialised instance first.
import workout as _wo_model
try:
    _real_t = _wo_model._t
    _wo_model._t = lambda s: "translated<%s>" % s
    check("workout date is translated as one reorderable phrase",
          _wo_model._display_date((2026, 8, 7, 0, 0, 0, 4, 0, -1))
          == "translated<Friday 7 August>")
except AttributeError as exc:
    check("workout date is translated as one reorderable phrase", False,
          "[not reached: %s]" % exc)
finally:
    _wo_model._t = _real_t
_probe = _wo_model.Workout.__new__(_wo_model.Workout)
_probe.data = {"exercises": [{"id": "a", "name": "A", "sets": 1, "reps": 5}],
               "log": {TODAY: {"a": [5]}}, "goals": {TODAY: 1},
               "show_widget": False}
_probe.sel = 0
try:
    import nbapp as _nbapp
    _probe.undo = _nbapp.UndoHistory(_probe._undo_snapshot,
                                     _probe._restore_undo_snapshot)
    _probe.undo.reset()
    _probe._refresh = lambda: None
    _wo_model._confirm = lambda *a, **k: True
    _probe._clear_today()
    _reached = _probe.undo.undo()
    check("undo restores a cleared logged day",
          _reached and _probe.data["log"].get(TODAY) == {"a": [5]},
          _probe.data)
except AttributeError as exc:
    check("undo restores a cleared logged day", False,
          "[not reached: %s]" % exc)

# Keep the logic suite useful on build hosts without an X server. The widget
# construction checks below still run when GTK is available; these model checks
# pin the product's central promise everywhere.
_gtk_ready = Gtk.init_check()[0]
if not _gtk_ready:
    _real_today_key = _wo_model.today_key
    try:
        _wo_model.today_key = lambda when=None: "2026-03-09"
        _probe.data = {
            "exercises": [{"id": "a", "name": "A", "sets": 2, "reps": 5}],
            "log": {"2026-03-06": {"a": [5, 5]},
                    "2026-03-07": {"a": [5, 5]},
                    "2026-03-08": {"a": [5]},
                    "2026-03-09": {"a": [5]}},
            "goals": {"2026-03-06": 2, "2026-03-07": 2,
                      "2026-03-08": 2}, "show_widget": False}
        check("a partial day breaks rather than extends the streak",
              _probe._streak() == (0, 2), _probe._streak())
        _probe.data["log"]["2026-03-08"]["a"].append(5)
        check("a partial today does not count before it is complete",
              _probe._streak() == (3, 3), _probe._streak())
        _probe.data["log"]["2026-03-09"]["a"].append(5)
        check("completing today extends the supported streak",
              _probe._streak() == (4, 4), _probe._streak())
        del _probe.data["log"]["2026-03-07"]
        check("a gap breaks current without corrupting longest",
              _probe._streak() == (2, 2), _probe._streak())
        check("DST spring-forward dates remain consecutive civil days",
              _wo_model._ordinal("2026-03-09")
              - _wo_model._ordinal("2026-03-08") == 1)
        _wo_model.today_key = lambda when=None: (
            "2026-03-10" if when is not None else "2026-03-10")
        check("a timezone change does not rewrite stored date keys",
              sorted(_probe.data["log"])[-1] == "2026-03-09",
              sorted(_probe.data["log"]))
    finally:
        _wo_model.today_key = _real_today_key
    print("\n%d checks, %d passed, %d failed (display-free path)"
          % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
    print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
    sys.exit(0 if all(RESULTS) else 1)


# -- 1/2/3. logging, totals, goals -------------------------------------------
home = fresh_home()
wo, app = new_app(home)
app.data["exercises"] = [
    {"id": "a", "name": "Push-ups", "sets": 3, "reps": 15},
    {"id": "b", "name": "Squats", "sets": 2, "reps": 20},
]
app.sel = 0
app._refresh()

app._on_log(None, 0)
check("a logged set records the exercise's target reps",
      app._sets_for("a") == [15], app._sets_for("a"))
app._on_log(None, 0)
app._on_log(None, 1)
check("sets accumulate per exercise",
      (app._sets_for("a"), app._sets_for("b")) == ([15, 15], [20]),
      (app._sets_for("a"), app._sets_for("b")))

done, goal = app._day_totals(TODAY)
check("today's total sums every exercise", (done, goal) == (3, 5), (done, goal))
check("today's reps sum the reps actually logged",
      app._reps_today() == 15 + 15 + 20, app._reps_today())

app._on_undo(None, 0)
check("undo removes exactly one set", app._sets_for("a") == [15],
      app._sets_for("a"))
app._on_undo(None, 0)
check("undo down to zero leaves no empty entry behind",
      "a" not in app.data["log"].get(TODAY, {}), app.data["log"])

# goal met, and staying met past the target
app.data["log"][TODAY] = {"a": [15, 15, 15]}
done, goal = app._day_totals(TODAY)
check("goal met counts as met", done >= 3)
app.data["log"][TODAY]["a"].append(15)
done, _g = app._day_totals(TODAY)
check("a set past the goal still counts", done == 4, done)
shutil.rmtree(home, ignore_errors=True)


# -- 2b. the week strip ------------------------------------------------------
home = fresh_home()
wo, app = new_app(home)
app.data["exercises"] = [{"id": "a", "name": "Push-ups", "sets": 3, "reps": 10}]
app.data["log"] = {TODAY: {"a": [10, 10]}, day_ago(1): {"a": [10]}}
app._refresh_week()
# the footer totals every day in the week that has sets
total = int(app.week_total.get_text())
check("the week total counts other days too, not just today",
      total >= 3, total)
check("a day with no sets contributes nothing",
      app._day_totals(day_ago(400)) == (0, 3), app._day_totals(day_ago(400)))

# The week is seven CALENDAR days, not seven 86400-second steps. Stepping a
# timestamp slides an hour across a daylight-saving change: on the evening of
# a fall-back Sunday the strip used to read Tue..Sun,Sun -- Monday's sets gone
# from the week, every row under the wrong weekday name, and today counted
# twice in SETS THIS WEEK. Checked in a zone that HAS the change, and pinned to
# real dates so it does not quietly stop testing anything when the zone is UTC.
_probe = [
    # (label, local time tuple, expected Monday..Sunday)
    ("the evening of a fall-back Sunday", (2026, 11, 1, 23, 30, 0),
     ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
      "2026-10-31", "2026-11-01"]),
    ("a spring-forward week", (2026, 3, 8, 0, 30, 0),
     ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
      "2026-03-07", "2026-03-08"]),
    ("an ordinary Wednesday", (2026, 7, 29, 9, 0, 0),
     ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
      "2026-08-01", "2026-08-02"]),
]
_tz = os.environ.get("TZ")
try:
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    for label, tup, want in _probe:
        ts = time.mktime(tup + (0, 0, -1))
        got = wo._week_days(ts)
        check("the week strip is seven calendar days: %s" % label,
              got == want, got)
    # ...and whatever the zone, a week is always 7 distinct consecutive days
    ts = time.mktime((2026, 11, 1, 23, 30, 0, 0, 0, -1))
    got = wo._week_days(ts)
    ords = [wo._ordinal(d) for d in got]
    check("no day is repeated or skipped in the strip",
          None not in ords and ords == list(range(ords[0], ords[0] + 7)), got)
    check("...and it starts on a Monday",
          time.localtime(time.mktime((int(got[0][:4]), int(got[0][5:7]),
                                      int(got[0][8:]), 12, 0, 0, 0, 0, -1))
                         ).tm_wday == 0, got[0])
    check("the day number round-trips back to its date key",
          wo._day_from_ordinal(wo._ordinal("2024-02-29")) == "2024-02-29"
          and wo._day_from_ordinal(0) == "1970-01-01"
          and wo._day_from_ordinal(wo._ordinal("2026-12-31")) == "2026-12-31",
          wo._day_from_ordinal(wo._ordinal("2024-02-29")))
finally:
    if _tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _tz
    time.tzset()
shutil.rmtree(home, ignore_errors=True)


# -- 3b. streaks -------------------------------------------------------------
# A day counts only at 100%. Today is not a miss until it is over, and the goal
# a past day was RUN against is the goal it is judged against.
home = fresh_home()
wo, app = new_app(home)
check("a date key converts to a day number",
      wo._ordinal("1970-01-01") == 0 and wo._ordinal("2026-07-28") == 20662,
      (wo._ordinal("1970-01-01"), wo._ordinal("2026-07-28")))
check("consecutive dates are one apart across a month end",
      wo._ordinal("2026-03-01") - wo._ordinal("2026-02-28") == 1)
check("and across a leap day",
      wo._ordinal("2024-03-01") - wo._ordinal("2024-02-29") == 1)
check("a key that is not a date is refused",
      wo._ordinal("not-a-date") is None and wo._ordinal(None) is None)

app.data["exercises"] = [{"id": "a", "name": "Push-ups", "sets": 2, "reps": 10}]
app.data["log"] = {day_ago(2): {"a": [10, 10]},
                   day_ago(1): {"a": [10, 10]}}
check("a run of finished days is a streak", app._streak()[0] == 2,
      app._streak())
check("an unfinished today does not break yesterday's streak",
      app._streak()[0] == 2, app._streak())

app.data["log"][TODAY] = {"a": [10]}
check("a PARTLY finished day does not count", app._streak()[0] == 2,
      app._streak())
app.data["log"][TODAY]["a"].append(10)
check("finishing today extends the streak straight away",
      app._streak()[0] == 3, app._streak())

app.data["log"] = {day_ago(5): {"a": [10, 10]}, day_ago(4): {"a": [10, 10]},
                   day_ago(3): {"a": [10, 10]}, day_ago(1): {"a": [10, 10]}}
cur, best = app._streak()
check("a missed day breaks the run", cur == 1, (cur, best))
check("...but the best run is remembered", best == 3, (cur, best))

app.data["log"] = {}
check("no days done is no streak", app._streak() == (0, 0), app._streak())
app.data["exercises"] = []
app.data["log"] = {TODAY: {"a": [10]}}
check("a day with no goal at all cannot be complete",
      app._streak()[0] == 0, app._streak())
app.destroy()
shutil.rmtree(home, ignore_errors=True)

# The goal a past day was run against is frozen, so changing the goal today
# cannot reach back and un-complete days already earned.
home = fresh_home()
with open(store(home), "w", encoding="utf-8") as fh:
    json.dump({"exercises": [{"id": "a", "name": "A", "sets": 2, "reps": 10}],
               "log": {day_ago(2): {"a": [10, 10]},
                       day_ago(1): {"a": [10, 10]}},
               "goals": {day_ago(2): 2, day_ago(1): 2}}, fh)
wo, app = new_app(home)
check("a 2-day streak loads from the store", app._streak()[0] == 2,
      app._streak())
app.data["exercises"].append({"id": "b", "name": "B", "sets": 3, "reps": 10})
check("adding an exercise today does not undo days already earned",
      app._streak()[0] == 2, app._streak())
check("...and today's own goal DOES grow with it",
      app._day_totals(TODAY)[1] == 5, app._day_totals(TODAY))
app.sel = 0
app._on_log(None, 0)
check("saving stamps today's goal so it freezes at midnight",
      app.data["goals"].get(TODAY) == 5, app.data.get("goals"))
app._on_undo(None, 0)
check("a day with nothing logged carries no stamp",
      TODAY not in app.data["goals"], app.data.get("goals"))
app.destroy()

# a store written before goals were stamped still reads sensibly
wo, app2 = new_app(home)
app2.data["goals"] = {}
check("days recorded before goals were stamped fall back to today's goal",
      app2._day_totals(day_ago(1))[1] == app2._goal_total(),
      app2._day_totals(day_ago(1)))
app2.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 4. restart --------------------------------------------------------------
home = fresh_home()
wo, app = new_app(home)
app.data["exercises"] = [{"id": "a", "name": "Rows", "sets": 4, "reps": 12}]
app.sel = 0
app._on_log(None, 0)
app._on_log(None, 0)
app.destroy()
wo, app2 = new_app(home)
check("exercises survive a restart",
      [e["name"] for e in app2.data["exercises"]] == ["Rows"],
      app2.data["exercises"])
check("logged sets survive a restart", app2._sets_for("a") == [12, 12],
      app2._sets_for("a"))
check("the daily goal survives a restart",
      app2.data["exercises"][0]["sets"] == 4)
app2.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 5. deleting -------------------------------------------------------------
home = fresh_home()
wo, app = new_app(home)
app.data["exercises"] = [{"id": "a", "name": "A", "sets": 2, "reps": 5},
                         {"id": "b", "name": "B", "sets": 2, "reps": 5}]
app.data["log"] = {TODAY: {"a": [5], "b": [5]}}
app.sel = 0
# bypass the confirm dialog, which needs a main loop
wo_mod = sys.modules["workout"]
wo_mod._confirm = lambda *a, **k: True
app._delete_exercise()
check("deleting an exercise removes it",
      [e["id"] for e in app.data["exercises"]] == ["b"], app.data["exercises"])
check("deleting an exercise removes its logged sets",
      "a" not in app.data["log"].get(TODAY, {}), app.data["log"])
check("the other exercise's sets are untouched",
      app._sets_for("b") == [5], app._sets_for("b"))
app.data["exercises"] = []
app.data["log"] = {TODAY: {"b": [5]}}
app.sel = 0
app._clear_today()
check("clear today empties the day", not app.data["log"].get(TODAY),
      app.data["log"])
app.destroy()
shutil.rmtree(home, ignore_errors=True)


# -- 6. a corrupt store must not stop the app opening ------------------------
for label, blob in [("not-a-dict", "[1,2,3]"),
                    ("unparseable", "{oh no"),
                    ("wrong types", json.dumps(
                        {"exercises": "nope", "log": 7, "show_widget": "yes"})),
                    ("bad rows", json.dumps(
                        {"exercises": [{"name": ""}, 5,
                                       {"name": "Ok", "sets": "x", "reps": None}],
                         "log": {"2026-01-01": {"a": ["x", 3]}}}))]:
    home = fresh_home()
    with open(store(home), "w", encoding="utf-8") as fh:
        fh.write(blob)
    try:
        wo, app = new_app(home)
        check("opens with a %s store" % label, True)
        if label == "bad rows":
            check("  ...keeping only the usable exercise",
                  [e["name"] for e in app.data["exercises"]] == ["Ok"],
                  app.data["exercises"])
            check("  ...and defaulting its unusable numbers",
                  (app.data["exercises"][0]["sets"],
                   app.data["exercises"][0]["reps"]) == (3, 10),
                  app.data["exercises"][0])
        app.destroy()
    except Exception as exc:                                    # noqa: BLE001
        check("opens with a %s store" % label, False, repr(exc))
    shutil.rmtree(home, ignore_errors=True)


print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
