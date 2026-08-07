#!/usr/bin/env python3
"""Meal Planner: prove a day key is filed under the date it NAMES.

THE BUG THIS EXISTS FOR. nbapp.day_ordinal is deliberately lenient about how a
day is spelled: it reads "2026-8-4" as 4 August and rolls the day-of-month that
does not exist ("2026-02-30") forward to 2 March. read_plan used that leniency
only as a yes/no test and then kept the key exactly as written -- but every
other part of the app looks a day up by _date_key(), which is always zero-padded
and always in range. So a plan holding "2026-8-4" was accepted, its meal was
counted in the status line and could keep the desktop Meals tile pointing at a
dish, yet the week grid showed that cell EMPTY. Filling it in wrote a second
entry for the same date; "Clear This Week" could not reach the first one; and
because every edit rewrites the whole store, the unreachable entry was faithfully
saved again for good.

read_plan now files each day under _date_key(day_ordinal(day)), which is the
date the key already claimed to mean, merging two spellings of one date with the
first in the file winning.

Display-free and static: nothing here builds a widget, and read_plan is a
module-level function taking a path.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/mealplanner_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if DE not in sys.path:
    sys.path.insert(0, DE)

import mealplanner  # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "  -- " + detail))
    if not ok:
        FAIL.append(name)


def write(tmp, obj):
    path = os.path.join(tmp, "mealplanner.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def slot(title, kind=mealplanner.KIND_NOTE):
    return {"kind": kind, "title": title}


def main():
    tmp = tempfile.mkdtemp(prefix="mp-selftest-")
    try:
        # 1. An unpadded day is the same day as its padded spelling, and the
        #    grid can only ever ask for the padded one.
        path = write(tmp, {"plan": {"2026-8-4": {"dinner": slot("Fish pie")}}})
        plan = mealplanner.read_plan(path)
        check("unpadded day is filed under the canonical key",
              list(plan) == ["2026-08-04"], "got keys %r" % (list(plan),))
        check("unpadded day keeps its meal",
              (plan.get("2026-08-04") or {}).get("dinner", {}).get("title")
              == "Fish pie", "got %r" % (plan.get("2026-08-04"),))

        # 2. Every key read_plan returns must be a key the week grid can build:
        #    _date_key(week + i) round-trips exactly.
        path = write(tmp, {"plan": {
            "2026-02-30": {"lunch": slot("Soup")},        # rolls to 2 March
            "2026-1-01": {"breakfast": slot("Toast")},
            "2026-12-31": {"dinner": slot("Goose")},
        }})
        plan = mealplanner.read_plan(path)
        bad = [d for d in plan
               if mealplanner._date_key(mealplanner.nbapp.day_ordinal(d)) != d]
        check("every returned key round-trips through _date_key",
              not bad, "not reachable from the grid: %r" % (bad,))
        check("an out-of-range day lands on the date it means",
              (plan.get("2026-03-02") or {}).get("lunch", {}).get("title")
              == "Soup", "got %r" % (sorted(plan),))
        check("meal count is unchanged by canonicalising",
              sum(len(m) for m in plan.values()) == 3,
              "got %d" % sum(len(m) for m in plan.values()))

        # 3. Two spellings of one date merge into one day rather than one of
        #    them vanishing; the first in the file wins a contested meal.
        path = write(tmp, {"plan": {
            "2026-08-04": {"dinner": slot("Fish pie")},
            "2026-8-04": {"lunch": slot("Soup"), "dinner": slot("Curry")},
        }})
        plan = mealplanner.read_plan(path)
        check("two spellings of one date merge",
              list(plan) == ["2026-08-04"], "got keys %r" % (list(plan),))
        day = plan.get("2026-08-04") or {}
        check("the merged day keeps both meals",
              sorted(day) == ["dinner", "lunch"], "got %r" % (sorted(day),))
        check("the first spelling wins a contested meal",
              day.get("dinner", {}).get("title") == "Fish pie",
              "got %r" % (day.get("dinner"),))

        # 4. Nothing above loosened what read_plan REJECTS.
        path = write(tmp, {"plan": {
            "not-a-day": {"dinner": slot("Ignored")},
            "2026-13-01": {"dinner": slot("Ignored")},
            "2026-08-05": {"brunch": slot("Ignored")},
            "2026-08-06": {"dinner": slot("   ")},
            "2026-08-07": {"dinner": slot("Stew", "nonsense")},
        }})
        plan = mealplanner.read_plan(path)
        check("a non-date key is still refused", "not-a-day" not in plan)
        check("an impossible month is still refused",
              not any(d.startswith("2027-01") or "13" in d.split("-")[1]
                      for d in plan), "got %r" % (sorted(plan),))
        check("an unknown meal is still refused", "2026-08-05" not in plan)
        check("a blank title is still refused", "2026-08-06" not in plan)
        check("an unknown kind still falls back to a note",
              (plan.get("2026-08-07") or {}).get("dinner", {}).get("kind")
              == mealplanner.KIND_NOTE, "got %r" % (plan.get("2026-08-07"),))

        # 5. The ordinary case is untouched.
        path = write(tmp, {"plan": {"2026-08-04": {
            "breakfast": slot("Porridge"),
            "dinner": slot("Pizza", mealplanner.KIND_TAKEOUT)}}})
        plan = mealplanner.read_plan(path)
        check("a well-formed plan is returned unchanged",
              plan == {"2026-08-04": {
                  "breakfast": {"kind": "note", "title": "Porridge"},
                  "dinner": {"kind": "takeout", "title": "Pizza"}}},
              "got %r" % (plan,))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print("%d check(s) failed: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
