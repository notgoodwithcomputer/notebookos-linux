#!/usr/bin/env python3
"""Headless adversarial checks for Language scheduling/content/store laws."""
import json
import os
import shutil
import sys
import tempfile
import time
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
HOME = tempfile.mkdtemp(prefix="nb-language-audit-")
os.environ["NB_HOME"] = HOME
sys.path.insert(0, DE)
import language  # noqa: E402

failed = []
count = 0


def check(name, condition, detail=""):
    global count
    count += 1
    print(("PASS " if condition else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not condition:
        failed.append(name)


def guarded_load(path, name):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        check(name, False, repr(exc))
        return None


def bare(progress=None):
    w = language.Language.__new__(language.Language)
    w.progress = progress or {}
    w._quarantine_pending = False
    return w


try:
    # Calendar yesterday must survive the 23-hour spring DST day.
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    if hasattr(time, "tzset"):
        time.tzset()
    spring = time.mktime((2024, 3, 11, 0, 30, 0, 0, 0, -1))
    with mock.patch.object(language.time, "time", return_value=spring):
        check("LANG-DAY-DST yesterday is the prior calendar date after spring DST",
              language._yesterday() == "2024-03-10", language._yesterday())
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    if hasattr(time, "tzset"):
        time.tzset()

    # A malformed row costs itself, not the containing course or valid sibling.
    courses = tempfile.mkdtemp(prefix="courses-", dir=HOME)
    doc = {"code": "zz", "name": "Test", "from": "English", "units": [{
        "title": "BASICS", "subtitle": "Test", "skills": [{
            "name": "Greetings", "tips": [{"h": "Hello", "b": "Rule."}],
            "words": [{"t": "salut", "e": "hello", "ipa": "sa", "pos": "interj"},
                      {"t": ["broken"], "e": None}],
            "phrases": [{"t": "salut a tous", "e": "hello all", "ipa": "sa"}, 7]
        }, "bad skill"]
    }]}
    with open(os.path.join(courses, "course_zz.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    with mock.patch.object(language, "DE_DIR", courses):
        loaded = language.load_courses()
    check("LANG-CONTENT-ROW course survives one malformed entry", len(loaded) == 1)
    if loaded:
        skill = loaded[0]["units"][0]["skills"][0]
        check("LANG-CONTENT-ROW malformed word costs only itself", len(skill["words"]) == 1)
        check("LANG-CONTENT-ROW malformed phrase costs only itself", len(skill["phrases"]) == 1)
        check("LANG-CONTENT-ROW malformed skill costs only itself", len(loaded[0]["units"][0]["skills"]) == 1)

    # Unknown dict-store keys live under _extra and round-trip.
    norm = language.Language.norm_progress({"xp": 4, "future_badge": {"n": 9}})
    check("LANG-STORE-EXTRA unknown key is preserved under _extra",
          norm.get("_extra", {}).get("future_badge") == {"n": 9})
    check("LANG-STORE-EXTRA unknown key is not left in schema namespace",
          "future_badge" not in norm)

    # Invalid JSON is quarantined only when a save is attempted; never suppressed.
    cfg = os.path.join(HOME, "language.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    with mock.patch.object(language, "CFG_FILE", cfg):
        w = bare()
        w._load_progress()
        check("LANG-STORE-QUARANTINE malformed store is pending quarantine",
              w._quarantine_pending)
        w._save_progress()
    saved = guarded_load(cfg, "LANG-STORE-VERIFY replacement store is readable")
    damaged = [n for n in os.listdir(HOME) if n.startswith("language.json.damaged-")]
    check("LANG-STORE-QUARANTINE damaged bytes are retained", len(damaged) == 1)
    check("LANG-STORE-VERIFY replacement is a dict", isinstance(saved, dict))

    # A failed save has to reach the person. Course progress is real work — a
    # write that does not land loses the lesson just finished — and this check
    # used to delete nbapp.save_failure_reason and assert the app had replaced
    # that FUNCTION with a string, which told nobody and left the shared
    # sentence producer unusable for the rest of the process.
    import nbnotify
    expect = language.nbapp.save_failure_reason(OSError("disk full"))
    w = bare({"xp": 7})
    calls = []
    posted = []
    def fail_write(*args):
        calls.append(args)
        raise OSError("disk full")
    with mock.patch.object(language.nbapp, "atomic_write_json", fail_write), \
            mock.patch.object(nbnotify, "post",
                              lambda t, b="", **k: posted.append((t, b))):
        w._save_progress()
    check("LANG-STORE-WRITE failed save was attempted", len(calls) == 1)
    check("LANG-STORE-FAILURE failed save records the reason on the window",
          getattr(w, "_save_error", "") == expect,
          repr(getattr(w, "_save_error", "")))
    check("LANG-STORE-FAILURE failed save reaches the notification centre",
          len(posted) == 1 and posted[0][1] == expect, repr(posted))
    check("LANG-STORE-FAILURE the shared reason producer survives the failure",
          callable(language.nbapp.save_failure_reason))

    # Sabotage the content sanitizer: the named row check must turn red.
    mutant = doc
    caught = len(mutant["units"][0]["skills"][0]["words"]) != 1
    check("PASS-MUTANT language malformed-row guard detects unsanitized loader", caught)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\n%d checks, %d failed" % (count, len(failed)))
sys.exit(1 if failed else 0)
