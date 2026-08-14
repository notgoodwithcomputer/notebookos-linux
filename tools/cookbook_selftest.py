#!/usr/bin/env python3
"""Cookbook: prove a store this app cannot READ is never overwritten.

THE BUG THIS EXISTS FOR. cookbook.py autosaves the whole library over
cookbook.json on every edit and again on close, so anything the loader shrugs
off is destroyed a moment later. _holds_records() is the guard against that: if
the file plainly holds recipe-shaped records and we adopted none of them, the
file is moved aside (<store>.damaged-<stamp>) instead of being replaced. Valid
JSON of the wrong shape parses perfectly, so nbapp's generic quarantine cannot
see it -- this guard is the only thing standing between a mis-shaped store and
a year of recipes.

The guard looked for one shape only: a list of record dicts exactly one level
below the top. It answered False -- silently, with no error anywhere -- for a
file that IS a map of recipes keyed by title, and for one whose recipes sit
under a nested wrapper. Opening and closing the app destroyed both, with no
user action at all.

Display-free and static: nothing here builds a widget. The loader/saver are
driven on a bare instance (__new__, no __init__) against a temporary store.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/cookbook_selftest.py
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

import cookbook  # noqa: E402

RECIPE = {"title": "Grandma's stew", "cat": "Dinner", "desc": "Sunday",
          "time": "3h", "makes": "Serves 6", "effort": "Easy",
          "ing": "beef - 1kg\ncarrot - 3", "steps": "brown\nsimmer"}
SECOND = {"title": "Soda bread", "cat": "Baking", "desc": "",
          "time": "1h", "makes": "Makes 1", "effort": "Easy",
          "ing": "flour - 500g", "steps": "bake"}

# Stores this app's loader does not read. Each one is somebody's whole cookbook.
UNREADABLE = {
    # A map of recipes keyed by title -- no wrapper at all.
    "title-keyed map": {RECIPE["title"]: RECIPE, SECOND["title"]: SECOND},
    # The library under a nested wrapper.
    "nested wrapper": {"cookbook": {"recipes": [RECIPE, SECOND]}},
    # A record list two wrappers down.
    "deep wrapper": {"data": {"v2": {"entries": [RECIPE]}}},
}

# Stores that must NOT be moved aside: a real empty library, and a good one.
EMPTY_LIB = {"cats": [], "active_cat": 0, "sel": -1, "recipes": []}
GOOD_LIB = {"cats": ["Dinner", "Baking"], "active_cat": 0, "sel": 0,
            "recipes": [RECIPE, SECOND]}

failures = []


def check(name, cond, detail=""):
    print("  %-6s %s%s" % ("ok" if cond else "FAIL", name,
                           "" if cond else "  -- " + detail))
    if not cond:
        failures.append(name)


def run_case(root, label, store, expect_recipes, expect_quarantine):
    """Write `store`, open the app on it, close it, and report what survived."""
    cfg = os.path.join(root, label.replace(" ", "_"))
    os.makedirs(cfg)
    path = os.path.join(cfg, "cookbook.json")
    raw = json.dumps(store, indent=1)
    with open(path, "w") as fh:
        fh.write(raw)
    cookbook.COOKBOOK_FILE = path        # every store path reads this global

    app = cookbook.Cookbook.__new__(cookbook.Cookbook)   # no widgets, no display
    app._load_state()
    loaded = len(app.recipes)
    app._save_state()                    # what closing the window does

    aside = [f for f in os.listdir(cfg) if ".damaged-" in f]
    kept = ""
    if aside:
        with open(os.path.join(cfg, aside[0])) as fh:
            kept = fh.read()
    with open(path) as fh:
        now = fh.read()

    print("%s  (%d recipe%s loaded)" % (label, loaded,
                                        "" if loaded == 1 else "s"))
    check("%s: recipes loaded" % label, loaded == expect_recipes,
          "got %d, want %d" % (loaded, expect_recipes))
    if expect_quarantine:
        # The bytes must still exist SOMEWHERE after open+close. Either the
        # loader read the recipes back (then the rewritten store holds them),
        # or the file it could not read was moved aside untouched.
        check("%s: original bytes survive" % label, kept == raw,
              "no .damaged- copy of the store" if not aside
              else "the copy is not the original file")
    else:
        check("%s: not quarantined" % label, not aside,
              "moved aside: %s" % aside)
        check("%s: store still readable" % label,
              json.loads(now).get("recipes") is not None, now[:80])


def main():
    root = tempfile.mkdtemp(prefix="cookbook-selftest-")
    try:
        for label, store in UNREADABLE.items():
            run_case(root, label, store, 0, True)
        run_case(root, "empty library", EMPTY_LIB, 0, False)
        run_case(root, "good library", GOOD_LIB, 2, False)

        # The guard itself, stated directly: it has to be able to go red, and
        # it has to stay green on a legitimately empty cookbook.
        print("guard")
        for label, store in UNREADABLE.items():
            check("_holds_records: %s" % label, cookbook._holds_records(store))
        check("_holds_records: empty library",
              not cookbook._holds_records(EMPTY_LIB))
        check("_holds_records: not a cookbook at all",
              not cookbook._holds_records({"tracks": ["a", "b"], "vol": 7}))

        # Exporting must not destroy the PDF already sitting there.
        #
        # The export rendered straight onto Documents/<name>. The usual reason
        # to export a second time is that the recipe changed, so the file being
        # overwritten is the user's previous good PDF — and a render that threw
        # part-way had already truncated it, right after they answered
        # "Replace". Same class as the one fixed in Comics: render beside the
        # destination, move it into place only when complete.
        print("export")
        docs = cookbook.DOCUMENTS
        os.makedirs(docs, exist_ok=True)
        dest_name = "Existing.pdf"
        dest = os.path.join(docs, dest_name)
        with open(dest, "wb") as fh:
            fh.write(b"%PDF-1.4 the good one the user already had")
        good = open(dest, "rb").read()

        app = cookbook.Cookbook.__new__(cookbook.Cookbook)
        app._flash_status = lambda *a, **k: None

        def _render_then_fail(path, _r):
            # A real cairo failure leaves a partly-written file behind, so
            # write before raising: a check that only ever sees an untouched
            # zero-byte draft would pass against the broken code too.
            with open(path, "wb") as fh:
                fh.write(b"%PDF-1.4 half a render")
            raise RuntimeError("render failed part-way")

        app._render_pdf = _render_then_fail
        app._write_export_pdf(dest_name, {"name": "R", "ing": "", "steps": ""})
        check("a failed export leaves the existing PDF untouched",
              open(dest, "rb").read() == good,
              "%r" % open(dest, "rb").read()[:40])
        leftovers = [n for n in os.listdir(docs)
                     if n.startswith(".cookbook-export-")]
        check("a failed export leaves no draft behind", not leftovers,
              repr(leftovers))

        # ...and the successful path still lands the real bytes.
        app._render_pdf = lambda path, _r: open(path, "wb").write(b"%PDF new")
        app._write_export_pdf(dest_name, {"name": "R", "ing": "", "steps": ""})
        check("a successful export replaces the file",
              open(dest, "rb").read() == b"%PDF new")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n%s  (%d check%s failed)"
          % ("FAIL" if failures else "PASS", len(failures),
             "" if len(failures) == 1 else "s"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
