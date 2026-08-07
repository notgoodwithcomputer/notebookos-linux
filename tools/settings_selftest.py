#!/usr/bin/env python3
"""
settings_selftest — prove the Settings > Default Applications page survives an
invalid persisted choice.

THE DEFECT. settings["default_apps"] is a plain {ext: module} dict on disk.
Its values are not guaranteed to name an app this build still offers: earlier
images listed more of them (Novel and Academic were dropped because they ignore
argv[1]), and the file is hand-editable. The page resolved a category with

    chosen = current.get(exts[0], default_mod)
    combo.set_active(mods.index(chosen) if chosen in mods else 0)

so ANY unrecognised value landed on index 0 of APP_CHOICES — which is Writer.
The Images row then read "Writer", a word processor that cannot open a PNG, as
though the user had chosen it; and because nothing on the page writes a row the
user did not touch, Finder went on being handed the dead module name and the
disagreement came back after every restart.

METHOD. No display and no widgets: the resolution rule now lives in the
module-level settings.resolve_default_app(), so the choice a row will show can
be computed and compared against the old inline formula directly. Each case
below also asserts what the OLD code answered, so the test states the
regression rather than only the fix.

    python3 tools/settings_selftest.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(_HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import settings  # noqa: E402


def old_choice(mapping, exts, default_mod):
    """The pre-fix formula, verbatim, as an index into APP_CHOICES."""
    mods = [m for m, _d in settings.APP_CHOICES]
    chosen = mapping.get(exts[0], default_mod)
    return mods[mods.index(chosen) if chosen in mods else 0]


def category(prefix):
    for label, exts, default_mod in settings.DEFAULT_APP_CATEGORIES:
        if label.startswith(prefix):
            return exts, default_mod
    raise AssertionError("no %r category in DEFAULT_APP_CATEGORIES" % prefix)


CASES = [
    # (why, stored mapping, category prefix, what the row must show)
    ("an app an older image offered and this one does not",
     {".png": "novel", ".jpg": "novel", ".jpeg": "novel", ".gif": "novel"},
     "Images", "media"),
    ("a hand-edited name that was never an app here",
     {".epub": "evince", ".pdf": "evince"}, "E-books", "ebook"),
    ("a null left by a truncated write",
     {".png": None}, "Images", "media"),
    ("nothing stored at all", {}, "Images", "media"),
    ("a real choice the user made", {".png": "ebook"}, "Images", "ebook"),
    ("the category default, stored explicitly",
     {".epub": "ebook"}, "E-books", "ebook"),
    ("a mapping that is not a dict", "corrupt", "Images", "media"),
    # The page writes every extension of a category together, but a mapping
    # that arrived any other way can carry only one of them.
    ("a partial mapping missing the first extension",
     {".jpg": "ebook"}, "Images", "ebook"),
]


def main():
    fails, regressions = [], 0
    for why, mapping, prefix, want in CASES:
        exts, default_mod = category(prefix)
        got = settings.resolve_default_app(mapping, exts, default_mod)
        if got != want:
            fails.append("%s: %s -> %r, wanted %r" % (prefix, why, got, want))
            continue
        note = ""
        if isinstance(mapping, dict):
            was = old_choice(mapping, exts, default_mod)
            if was != want:
                regressions += 1
                note = "   (old code said %r)" % was
        print("  ok  %-8s %-52s -> %s%s" % (prefix, why, got, note))
    for f in fails:
        print("  FAIL %s" % f)
    # A regression test that no longer exercises the regression has stopped
    # being one, so the count is asserted too.
    if not fails and regressions < 3:
        print("  FAIL only %d case(s) differ from the old formula" % regressions)
        fails.append("coverage")
    print("\n%s — %d case(s), %d of them the old bug"
          % ("FAIL" if fails else "PASS", len(CASES), regressions))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
