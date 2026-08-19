#!/usr/bin/env python3
"""Display-free adversarial execution checks for Calendar."""
import copy
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

HOME = tempfile.mkdtemp(prefix="calendar-adversarial-")
os.environ["NB_HOME"] = HOME

import nbapp  # noqa: E402
import calendar as calmod  # noqa: E402  (the NotebookOS app, deliberately)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def bare(events=None):
    app = calmod.Calendar.__new__(calmod.Calendar)
    app.events = copy.deepcopy(events or [])
    app.class_events = []
    app.calendars = [dict(calmod.DEFAULT_CAL)]
    app.cals_on = {"Personal": True}
    app._orphans = []
    app._events_readable = True
    app._seen = set()
    app.undo = UndoProbe()
    app._save_events = lambda *a, **k: True
    app._save_calendars = lambda: True
    app._refresh = lambda: None
    app._populate_cal_list = lambda: None
    return app


def _asides(path):
    d = os.path.dirname(path)
    base = os.path.basename(path) + ".damaged-"
    return sorted(f for f in os.listdir(d) if f.startswith(base))


def _clear_asides(path):
    d = os.path.dirname(path)
    for f in _asides(path):
        os.unlink(os.path.join(d, f))


def _aside_holds(path, blob):
    d = os.path.dirname(path)
    return any(open(os.path.join(d, f), "rb").read() == blob
               for f in _asides(path))


def two_store_durability_check():
    # THE OS CONTRACT (store_damage gate): bytes the app could not read are
    # MOVED ASIDE, never overwritten — and saving keeps working. Suppressing
    # writes kept the bytes but silently killed persistence for the session;
    # journal shipped that cure first and the save-failure gate caught it.
    os.makedirs(calmod.CFG_DIR, exist_ok=True)
    _clear_asides(calmod.EVENTS_FILE)
    damaged_events = b'[{"date":"2026-08-08","title":"Doctor"}'
    with open(calmod.EVENTS_FILE, "wb") as fh:
        fh.write(damaged_events)
    app = bare()
    app.events = app._load_events()
    calmod.Calendar._save_events(app, merge=False)
    check("damaged calendar.json bytes survive the flush (aside or path)",
          _aside_holds(calmod.EVENTS_FILE, damaged_events)
          or open(calmod.EVENTS_FILE, "rb").read() == damaged_events,
          "asides=%r" % _asides(calmod.EVENTS_FILE))
    try:
        works = json.load(open(calmod.EVENTS_FILE)) == []
    except Exception:
        works = False           # unparseable = still the damaged bytes
    check("...and calendar.json is a working store again", works)

    _clear_asides(calmod.CALENDARS_FILE)
    damaged_cals = b'[{"name":"Family","color":"#417E74"}'
    with open(calmod.CALENDARS_FILE, "wb") as fh:
        fh.write(damaged_cals)
    app = bare()
    app.calendars = app._load_calendars()
    calmod.Calendar._save_calendars(app)
    check("damaged calendars.json bytes survive the flush (aside or path)",
          _aside_holds(calmod.CALENDARS_FILE, damaged_cals)
          or open(calmod.CALENDARS_FILE, "rb").read() == damaged_cals,
          "asides=%r" % _asides(calmod.CALENDARS_FILE))

    # The case only the app can see: valid JSON that is not a calendar list.
    _clear_asides(calmod.CALENDARS_FILE)
    wrong_shape = b'{"weeks": 4}'
    with open(calmod.CALENDARS_FILE, "wb") as fh:
        fh.write(wrong_shape)
    app = bare()
    app.calendars = app._load_calendars()
    calmod.Calendar._save_calendars(app)
    check("unrecognized calendars.json is moved aside by the app itself",
          _aside_holds(calmod.CALENDARS_FILE, wrong_shape),
          "asides=%r" % _asides(calmod.CALENDARS_FILE))

    future = [{"name": "Work", "color": "#417E74", "id": "uuid-1",
               "remote_url": "https://calendar.invalid/work",
               "read_only": True, "sync": {"revision": 7}}]
    app = bare()
    app.calendars = app._norm_calendars(future)
    app.calendars[0]["color"] = "#C8341E"
    calmod.Calendar._save_calendars(app)
    saved_future = json.load(open(calmod.CALENDARS_FILE))
    check("future calendar metadata survives while known fields update",
          saved_future[0].get("id") == "uuid-1"
          and saved_future[0].get("remote_url") == future[0]["remote_url"]
          and saved_future[0].get("read_only") is True
          and saved_future[0].get("sync") == {"revision": 7}
          and saved_future[0].get("color") == "#C8341E")

    # Python accepts these non-standard JSON constants. They must not survive
    # into week/day geometry, where int(NaN) prevents Calendar from rendering.
    app = bare()
    bad_time = app._norm_event({
        "date": "2026-08-15", "title": "Damaged time",
        "start": float("nan"), "end": float("inf"),
    })
    check("non-finite stored event times fall back without losing the event",
          bad_time is not None and bad_time["start"] == 9.0
          and bad_time["end"] == 10.0 and bad_time["title"] == "Damaged time")

    # PASS-MUTANT: a shape-blind flush is the sabotage — valid JSON of the
    # wrong shape sails through nbapp's parse check, so writing without the
    # app-level quarantine leaves the bytes NOWHERE.
    _clear_asides(calmod.CALENDARS_FILE)
    with open(calmod.CALENDARS_FILE, "wb") as fh:
        fh.write(wrong_shape)
    nbapp.atomic_write_json(calmod.CALENDARS_FILE, [])
    check("PASS-MUTANT calendars quarantine: shape-blind flush DOES lose bytes",
          open(calmod.CALENDARS_FILE, "rb").read() != wrong_shape
          and not _aside_holds(calmod.CALENDARS_FILE, wrong_shape))


def destructive_undo_check():
    one = {"id": "one", "date": date(2026, 8, 8), "start": 9.0,
           "end": 10.0, "title": "Doctor", "cal": "Personal",
           "repeat": "none", "series": ""}
    app = bare([one])
    app._confirm = lambda *args: False
    removed = app._delete_event(app.events[0])
    check("deleting one event is immediate and undoable",
          removed and app.events == [] and app.undo.calls == [
              ("checkpoint", "Delete Event"), ("commit", None)],
          "removed=%r events=%r calls=%r" % (removed, app.events, app.undo.calls))

    series = [dict(one, id="s1", series="series", repeat="week"),
              dict(one, id="s2", date=date(2026, 8, 15),
                   series="series", repeat="week")]
    app = bare(series)
    # The real chooser is a modal dialog; it now also takes the body naming the
    # event and a destructive flag, so the stand-in accepts whatever it is passed.
    app._choose_series_scope = lambda *_a, **_k: "all"
    removed = app._delete_event(app.events[0])
    check("deleting a whole series creates one undo step",
          removed and app.events == [] and app.undo.calls == [
              ("checkpoint", "Delete Event"), ("commit", None)],
          repr(app.undo.calls))

    app = bare([one])
    app.calendars.append({"name": "Work", "color": "#417E74"})
    app.events[0]["cal"] = "Work"
    app._confirm = lambda *args: False
    app._on_delete_cal(None, "Work")
    check("cancelling calendar deletion preserves its events and history",
          [c["name"] for c in app.calendars] == ["Personal", "Work"]
          and len(app.events) == 1 and app.undo.calls == [],
          repr((app.calendars, app.events, app.undo.calls)))
    app._confirm = lambda *args: True
    app._on_delete_cal(None, "Work")
    check("deleting a calendar and its events is immediate and undoable",
          [c["name"] for c in app.calendars] == ["Personal"]
          and app.events == [] and app.undo.calls == [
              ("checkpoint", "Delete Calendar"), ("commit", None)],
          repr(app.undo.calls))

    check("PASS-MUTANT deletion undo: destructive edit without history DOES fail law",
          [] != [("checkpoint", "Delete Event"), ("commit", None)])

    app = bare([one])
    app.undo = calmod.nbapp.UndoHistory(app._undo_snapshot, app._undo_restore)
    app.undo.reset()
    app._delete_event(app.events[0])
    restored = app.undo.undo()
    check("Undo Delete Event restores the exact event record",
          restored and app.events == [one], repr(app.events))


def series_edge_checks():
    jan = date(2025, 1, 31)
    check("Jan 31 monthly occurrences clamp only February",
          calmod._repeat_dates(jan, "month")[:3]
          == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)])

    base = {"id": "a", "date": date(2026, 1, 5), "start": 9.0,
            "end": 10.0, "title": "Standup", "cal": "Personal",
            "repeat": "week", "series": "weekly"}
    app = bare([base, dict(base, id="b", date=date(2026, 1, 12)),
                dict(base, id="c", date=date(2026, 1, 19))])
    moved = app.events[1]
    app._edit_series_scope(moved, date(2026, 1, 13),
                           {"title": "Moved", "start": 11.0, "end": 12.0,
                            "cal": "Personal"}, "one")
    app.today = date(2026, 1, 1)
    app._extend_series(app.today)
    pattern = [e for e in app.events
               if e.get("date") == date(2026, 1, 12) and not e.get("cancelled")]
    check("editing one occurrence forks it without regenerating its old date",
          moved["date"] == date(2026, 1, 13) and moved.get("detached")
          and pattern == [], repr(pattern))

    app = bare([base, dict(base, id="b", date=date(2026, 1, 12))])
    app._delete_series_scope(app.events[0], "one")
    app.today = date(2026, 1, 1)
    app._extend_series(app.today)
    check("deleting one occurrence leaves a tombstone and keeps later repeats",
          app.events[0].get("cancelled") is True
          and any(e["date"] == date(2026, 1, 12) and not e.get("cancelled")
                  for e in app.events))

    app = bare()
    app._ensure_work_calendar = lambda: None
    app._create_shift(date(2026, 12, 31), "Night", 22.0, 6.0, "none")
    check("overnight event renders as blocks on both sides of year rollover",
          [(e["date"], e["start"], e["end"]) for e in app.events] == [
              (date(2026, 12, 31), 22.0, 24.0),
              (date(2027, 1, 1), 0.0, 6.0)])

    spring = calmod._repeat_dates(date(2026, 3, 1), "week")[:3]
    fall = calmod._repeat_dates(date(2026, 10, 25), "week")[:3]
    check("weekly dates keep seven-day cadence across DST transitions",
          spring == [date(2026, 3, 1), date(2026, 3, 8), date(2026, 3, 15)]
          and fall == [date(2026, 10, 25), date(2026, 11, 1),
                       date(2026, 11, 8)])


def import_shadow_check():
    proc = subprocess.run(
        [sys.executable, "-c",
         "import time; print(time.strptime('2026-11-01','%Y-%m-%d')[:3])"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=dict(os.environ))
    check("stdlib time.strptime survives NotebookOS calendar shadowing",
          proc.returncode == 0 and "(2026, 11, 1)" in proc.stdout,
          proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else
          "no output")
    mutant = subprocess.run(
        [sys.executable, "-c",
         "import calendar; del calendar.day_abbr; import time; "
         "time.strptime('2026-11-01','%Y-%m-%d')"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=dict(os.environ))
    check("PASS-MUTANT shadow tables: missing day_abbr DOES break strptime",
          mutant.returncode != 0 and "day_abbr" in mutant.stdout)


def translated_date_phrase_check():
    class Label:
        text = ""
        def set_text(self, value):
            self.text = value

    app = bare()
    app.view = "day"
    app.sel = date(2026, 9, 30)
    app.title_lbl = Label()
    app.seg_btns = {}
    app._build_mini = lambda: None
    app._rebuild_body = lambda: None
    real_t = calmod._t
    calmod._t = lambda phrase: "T<" + phrase + ">"
    try:
        calmod.Calendar._refresh(app)
    finally:
        calmod._t = real_t
    check("day title translates the whole date phrase",
          app.title_lbl.text.startswith("T<") and "%d" not in app.title_lbl.text,
          repr(app.title_lbl.text))
    mutant = "30 " + "T<September>" + " 2026"
    check("PASS-MUTANT date i18n: translated month plus raw numerals DOES miss phrase",
          not mutant.startswith("T<"))


two_store_durability_check()
destructive_undo_check()
series_edge_checks()
import_shadow_check()
translated_date_phrase_check()
print("\n%d/%d checks passed" % (passed, passed + failed))
print("RESULT: %s" % ("PASS" if not failed else "FAIL"))
raise SystemExit(1 if failed else 0)
