#!/usr/bin/env python3
"""Display-free adversarial checks for Meal Planner persistence and undo."""
import copy
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="mealplanner-adversarial-")
os.environ["NB_HOME"] = HOME
DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import mealplanner  # noqa: E402

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


def bare(plan):
    app = mealplanner.MealPlanner.__new__(mealplanner.MealPlanner)
    app.plan = copy.deepcopy(plan)
    app.undo = UndoProbe()
    app._save = lambda: True
    app._refresh = lambda: None
    app._confirm = lambda *_args: True
    return app


def destructive_undo_check():
    day = "2026-08-03"
    original = {day: {"dinner": {"kind": "note", "title": "Stew"}}}
    app = bare(original)
    app._set_slot(day, "dinner", mealplanner.KIND_NOTE, "")
    check("clearing one meal creates an undo step",
          app.plan == {} and app.undo.calls == [
              ("checkpoint", "Clear Meal"), ("commit", None)],
          repr(app.undo.calls))

    app = bare(original)
    app.week = mealplanner._week_start(day)
    app._clear_week()
    check("confirmed week clearing is undoable",
          app.plan == {} and app.undo.calls == [
              ("checkpoint", "Clear Week"), ("commit", None)],
          repr(app.undo.calls))

    app = bare(original)
    app.week = mealplanner._week_start(day)
    asked = []
    app._confirm = lambda *args: (asked.append(args), False)[1]
    app._clear_week()
    # The detail now states the consequence instead of asking the heading
    # back: "Clear this week" / "Clear this week?" was the same question
    # twice and never said how many meals went or what survived. The sentence
    # it uses was already in every catalog, written for this app.
    check("cancelling week clear changes and checkpoints nothing",
          app.plan == original and app.undo.calls == []
          and asked == [(mealplanner._t("Clear this week"),
                         mealplanner._t("Remove %d planned meal%s? "
                                        "Recipes in Cookbook are kept.")
                         % (1, ""),
                         mealplanner._t("Clear"))],
          repr((app.plan, app.undo.calls, asked)))

    check("MUTANT: destructive edits without history DO lack undo",
          [] != [("checkpoint", "Clear Week"), ("commit", None)])


def date_math_check():
    cases = {
        "2025-12-29": "2025-12-29",
        "2026-01-01": "2025-12-29",
        "2026-03-08": "2026-03-02",
        "2026-11-01": "2026-10-26",
    }
    got = {day: mealplanner._date_key(mealplanner._week_start(day))
           for day in cases}
    check("weeks start Monday across year and DST boundaries", got == cases,
          repr(got))
    mutant = {day: mealplanner._date_key(
        mealplanner.nbapp.day_ordinal(day)
        - ((mealplanner.nbapp.day_ordinal(day) + 4) % 7)) for day in cases}
    check("MUTANT: Sunday-based arithmetic DOES change audited weeks",
          mutant != cases)


def damaged_store_check():
    os.makedirs(mealplanner.CFG_DIR, exist_ok=True)
    original = b'{"plan":{"2026-08-03":{"dinner":'
    with open(mealplanner.STORE, "wb") as fh:
        fh.write(original)
    check("damaged meal store survives open+close byte-for-byte",
          mealplanner.read_plan() == {}
          and open(mealplanner.STORE, "rb").read() == original)
    mealplanner.nbapp.atomic_write_json(mealplanner.STORE, {"plan": {}})
    check("MUTANT: unconditional close save DOES rewrite damaged meal store",
          open(mealplanner.STORE, "rb").read() != original)


def extension_roundtrip_check():
    day = "2026-08-15"
    app = mealplanner.MealPlanner.__new__(mealplanner.MealPlanner)
    app._store_raw = {
        "timezone": "UTC",
        "plan": {day: {
            "breakfast": {"kind": "note", "title": "Eggs", "servings": 2},
            "brunch": {"kind": "note", "title": "Waffles", "guests": ["A"]},
        }},
    }
    app.plan = {day: {"breakfast": {"kind": "note", "title": "Toast"}}}
    saved = app._serialize_store()
    check("unknown top-level meal metadata survives",
          saved.get("timezone") == "UTC", repr(saved))
    check("future meal types survive",
          saved["plan"][day].get("brunch", {}).get("title") == "Waffles",
          repr(saved))
    check("known slot extension fields survive while edits win",
          saved["plan"][day]["breakfast"] == {
              "kind": "note", "title": "Toast", "servings": 2}, repr(saved))


try:
    destructive_undo_check()
    date_math_check()
    damaged_store_check()
    extension_roundtrip_check()
    print("\n%d/%d checks passed" % (passed, passed + failed))
finally:
    shutil.rmtree(HOME, ignore_errors=True)
raise SystemExit(1 if failed else 0)
