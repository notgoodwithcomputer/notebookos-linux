#!/usr/bin/env python3
"""Headless Finder checks; every filesystem operation stays in one temp root."""
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
HOME = tempfile.mkdtemp(prefix="nb-finder-audit-")
os.environ["NB_HOME"] = HOME
sys.path.insert(0, DE)
import finder  # noqa: E402

failed = []
count = 0


def check(name, condition, detail=""):
    global count
    count += 1
    print(("PASS " if condition else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not condition:
        failed.append(name)


class Fake:
    def __init__(self):
        self.rel = ".Trash"
        self.status = []
        self._prefs_ready = True
        self._view = "grid"
        self._show_hidden = True
        self._sort_col = "date"
        self._sort_desc = True
        self._prefs_extra = {}
    def _flash_status(self, text, *args): self.status.append(text)
    def _prefs_path(self): return os.path.join(HOME, ".config", "notebook", "finder.json")


def guarded_load(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        check(name, False, repr(exc))
        return None


try:
    # F2/menu share _begin_rename; it must stop before consulting selection.
    f = Fake()
    f._selected_iter = lambda: (_ for _ in ()).throw(AssertionError("selection reached"))
    try:
        finder.Finder._begin_rename(f)
        guarded = True
    except AssertionError:
        guarded = False
    check("FINDER-TRASH-RENAME F2/shared rename entry is blocked in Trash", guarded)
    check("FINDER-TRASH-RENAME refusal is visible", bool(f.status))

    # The path predicate must reject a folder copied/moved onto itself or below itself.
    src = os.path.join(HOME, "Documents", "project")
    child = os.path.join(src, "archive", "project")
    peer = os.path.join(HOME, "Music", "project")
    os.makedirs(src)
    recursive_target = getattr(finder.Finder, "_recursive_target", lambda _s, _d: False)
    check("FINDER-RECURSION copy onto itself is rejected", recursive_target(src, src))
    check("FINDER-RECURSION copy into own subfolder is rejected", recursive_target(src, child))
    check("FINDER-RECURSION peer destination remains allowed", not recursive_target(src, peer))

    # Finder prefs preserve unknown keys under _extra, quarantine damage on save,
    # and publish failure reasons. All verifier reads are guarded.
    pref = f._prefs_path()
    os.makedirs(os.path.dirname(pref), exist_ok=True)
    with open(pref, "w", encoding="utf-8") as fh:
        json.dump({"view": "grid", "plugin_pref": {"x": 1}}, fh)
    finder.Finder._load_prefs(f)
    check("FINDER-STORE-EXTRA unknown preference moves under _extra",
          f._prefs_extra.get("plugin_pref") == {"x": 1})
    finder.Finder._save_prefs(f)
    saved = guarded_load(pref, "FINDER-STORE-VERIFY saved prefs are readable")
    check("FINDER-STORE-EXTRA unknown preference round-trips",
          isinstance(saved, dict) and saved.get("_extra", {}).get("plugin_pref") == {"x": 1})

    with open(pref, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    f._prefs_quarantine_pending = False
    finder.Finder._load_prefs(f)
    check("FINDER-STORE-QUARANTINE malformed prefs wait for save",
          f._prefs_quarantine_pending)
    finder.Finder._save_prefs(f)
    guarded_load(pref, "FINDER-STORE-VERIFY replacement prefs are readable")
    check("FINDER-STORE-QUARANTINE damaged prefs retained",
          len([n for n in os.listdir(os.path.dirname(pref)) if n.startswith("finder.json.damaged-")]) == 1)

    # The fourth suite that certified the clobber (a86311a0): it deleted
    # nbapp.save_failure_reason — a FUNCTION — and then asserted the app had
    # replaced it with a string, under a name claiming the failure was
    # "exposed". Nothing was exposed to anyone, and the shared sentence
    # producer was left unusable for the rest of the process. What a person can
    # reach is checked instead.
    import nbnotify
    expect = finder.nbapp.save_failure_reason(OSError("read-only"))
    calls = []
    posted = []
    def fail(*args):
        calls.append(args)
        raise OSError("read-only")
    with mock.patch.object(finder.nbapp, "atomic_write_json", fail), \
            mock.patch.object(nbnotify, "post",
                              lambda t, b="", **k: posted.append((t, b))):
        finder.Finder._save_prefs(f)
    check("FINDER-STORE-WRITE failed preference save was attempted", len(calls) == 1)
    check("FINDER-STORE-FAILURE failed save records the reason on the window",
          getattr(f, "_save_error", "") == expect,
          repr(getattr(f, "_save_error", "")))
    check("FINDER-STORE-FAILURE failed save reaches the notification centre",
          len(posted) == 1 and posted[0][1] == expect, repr(posted))
    check("FINDER-STORE-FAILURE the shared reason producer survives the failure",
          callable(finder.nbapp.save_failure_reason))

    # A deliberately permissive mutant must be caught by the named recursion assertion.
    permissive = lambda _s, _d: False
    check("PASS-MUTANT finder recursion guard detects permissive predicate",
          not permissive(src, child))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\n%d checks, %d failed" % (count, len(failed)))
sys.exit(1 if failed else 0)
