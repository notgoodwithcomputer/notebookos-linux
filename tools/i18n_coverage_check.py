#!/usr/bin/env python3
"""
i18n_coverage_check — user-visible strings that are in NO catalog.

WHY THIS EXISTS, AND WHY i18n_check CANNOT SEE IT: i18n_check compares the
seventeen catalogs to EACH OTHER. A string that is missing from all seventeen is
therefore invisible to it, and it will happily report 100% while whole screens
are English-only in every other language. This bit the project once before, when
a newly added app was absent from every catalog and coverage still read 100%;
check_chrome() was added then, but it only inspects MENU labels.

This asks the other question: for every string the code actually shows a person,
is there a catalog entry at all? A miss means that string is English in the
sixteen non-English languages, however green i18n_check looks.

  python3 tools/i18n_coverage_check.py [--file X.py ...] [--fail] [-v]

Exit 0 always unless --fail (then non-zero when anything is uncovered).
"""
import argparse
import ast
import io
import re
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Calls whose first argument is shown to a person. nbi18n patches these, so a
# bare literal here IS displayed and IS translated when the catalogs know it.
CALLS = {"set_text", "set_label", "set_markup", "set_tooltip_text",
         "set_placeholder_text", "set_title", "_t", "_flash", "append_text",
         "prepend_text"}
CTORS = {"Label", "Button", "CheckButton", "MenuItem", "RadioButton"}

# Not prose: paths, format scaffolding, single glyphs, pure punctuation.
# A user-visible string may LEAD with a placeholder and still be prose somebody
# reads -- "%d to do" and "%d of %d sets" are on the desktop board. Excluding
# everything starting with "%" hid 39 such strings and made this tool report
# "FULLY COVERED" while they displayed in English in all sixteen languages.
# Judge a string on the words left once the placeholders are removed.
_FMT = re.compile(r"%[-+ #0]*[\d*]*(?:\.\d+)?[hlL]?[a-zA-Z%]")


def is_prose(v):
    v = v.strip()
    if len(v) < 4 or not any(c.isalpha() for c in v):
        return False
    if v.startswith(("/", "http", "#", "_")):
        return False
    if v.startswith(".") and " " not in v:      # ".json", a dotted path
        return False
    if sum(c.isalpha() for c in _FMT.sub("", v)) < 3:
        return False                             # "%s", "%d%%" -- no words
    if v in ("None", "True", "False"):
        return False
    return True


def shown_strings(path):
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return set()
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
        args = []
        if name in CALLS and n.args:
            args = [n.args[0]]
        elif name in CTORS:
            args = [kw.value for kw in n.keywords
                    if kw.arg in ("label", "text", "title")]
        for a in args:
            if isinstance(a, ast.Call) and \
                    getattr(a.func, "id", None) == "_t" and a.args:
                a = a.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and is_prose(a.value):
                out.add(a.value.strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", nargs="+", default=None)
    ap.add_argument("--fail", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    try:
        with io.open(os.path.join(DE, "lang_es.json"), encoding="utf-8") as fh:
            cat = set(json.load(fh))
    except (OSError, ValueError):
        print("could not read a catalog to compare against")
        return 2

    files = ([os.path.join(DE, f) for f in a.file] if a.file else
             sorted(os.path.join(DE, f) for f in os.listdir(DE)
                    if f.endswith(".py")))
    total = 0
    for path in files:
        gap = sorted(s for s in shown_strings(path) if s not in cat)
        if not gap:
            continue
        print("\n%s   %d uncovered" % (os.path.basename(path), len(gap)))
        for s in (gap if a.verbose else gap[:6]):
            print("    %r" % s[:96])
        if not a.verbose and len(gap) > 6:
            print("    ... and %d more (-v for all)" % (len(gap) - 6))
        total += len(gap)

    print("\n%d user-visible string(s) with no catalog entry, across %d file(s)"
          % (total, len(files)))
    print("Each one displays in ENGLISH in the sixteen non-English languages.")
    print("RESULT: " + ("FULLY COVERED" if not total else
                        "%d UNCOVERED" % total))
    return 1 if (total and a.fail) else 0


if __name__ == "__main__":
    sys.exit(main())
