#!/usr/bin/env python3
"""Every _t() literal in the source must exist in every catalog.

i18n_check walks the CATALOG and grades each entry: placeholders, padding,
dialect, emptiness. It reports 17 x 100% when all 3746 keys are translated
in all 17 languages — and it says exactly that while a string added to an
app that morning is missing from every one of them, because a key that was
never written down is not a key it iterates over. The half of the question
it answers is "are the translations we have any good"; this is the other
half: "is there a translation for everything the app can say".

The ledger below is the debt this check found the day it was written. It is
a ratchet: a module may not gain untranslated strings, and any module not
listed must have none at all. Fixing strings means shrinking a number here.
"""
from __future__ import annotations

import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Module -> how many untranslated _t() literals it carried on 2026-08-12.
# Every one of these is a string a person reads in English no matter which
# of the 17 languages they chose. Several are failure messages, which is the
# worst moment to fall back to a language someone may not read.
DEBT = {
    "academics.py": 11,
    "accounting.py": 7,
    "bills.py": 2,
    "calculator.py": 4,
    "calendar.py": 20,
    "cookbook.py": 2,
    "finder.py": 6,
    "gbabuild.py": 1,
    "journal.py": 2,
    "language.py": 3,
    "mealplanner.py": 2,
    "music.py": 1,
    "novel.py": 3,
    "screenplay.py": 2,
    "sequencer.py": 66,
    "settings.py": 7,
    "shell.py": 1,
    "tasks.py": 3,
    "usbwriter.py": 1,
    "video.py": 10,
    "workout.py": 1,
    "writer.py": 3,
}


def literals(path):
    """The string constants this module hands to _t()."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError as exc:
        print("PARSE FAIL %s %s" % (os.path.basename(path), exc))
        return None
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_t" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            text = node.args[0].value
            if text.strip():
                found.append((node.lineno, text))
    return found


def main():
    catalogs = {}
    for name in sorted(os.listdir(DE)):
        if name.startswith("lang_") and name.endswith(".json"):
            code = name[5:-5]
            with open(os.path.join(DE, name), encoding="utf-8") as handle:
                catalogs[code] = json.load(handle)
    if not catalogs:
        print("RESULT: FAILED: no catalogs found")
        return 1

    bad = 0
    counts = {}
    for name in sorted(os.listdir(DE)):
        if not name.endswith(".py") or name.startswith("lang_"):
            continue
        found = literals(os.path.join(DE, name))
        if found is None:
            bad += 1
            continue
        gaps = []
        for line, text in found:
            absent = [code for code, cat in catalogs.items() if text not in cat]
            if absent:
                gaps.append((line, text, absent))
        if not gaps:
            continue
        counts[name] = len(gaps)
        allowed = DEBT.get(name, 0)
        if len(gaps) > allowed:
            bad += 1
            print("=== %s: %d untranslated, ledger allows %d"
                  % (name, len(gaps), allowed))
            for line, text, absent in gaps[:8]:
                where = ("all %d languages" % len(catalogs)
                         if len(absent) == len(catalogs)
                         else ", ".join(sorted(absent)))
                print("  %s:%d  %r  missing in %s" % (name, line, text, where))

    for name, allowed in sorted(DEBT.items()):
        actual = counts.get(name, 0)
        if actual < allowed:
            bad += 1
            print("LEDGER STALE  %s now has %d untranslated, ledger says %d — "
                  "lower the number so it cannot climb back" % (name, actual, allowed))

    total = sum(counts.values())
    print("%d untranslated _t() literals in %d modules (ledger allows %d)"
          % (total, len(counts), sum(DEBT.values())))
    print("RESULT: " + ("PASS" if not bad else "FAILED: %d module(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
