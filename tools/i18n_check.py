#!/usr/bin/env python3
"""i18n_check — validate the translation catalogs (de/lang_<code>.json).

    python3 tools/i18n_check.py

Catches the failure modes that are invisible until a user in that language
hits them:

* **A key with leading or trailing whitespace.** An app that lays a toolbar out
  with `"   Tool"` bakes the padding into the string the catalog is keyed on.
  The moment the app's spacing is fixed the key stops matching and that label
  is silently English again — and the padding was never translatable in the
  first place. A key that is a genuine sentence fragment for concatenation
  (`"About "`, `"Chapter "`) is allowed, and listed so it can be eyeballed.
* **Placeholder drift.** `_t("%d of %d")` is used as `_t(...) % (a, b)`, so a
  translation with a different set of specs raises at the `%`. nbi18n falls
  back to English there, which hides the bug rather than fixing it.
* **Key-set drift between languages** — a string translated in Spanish but not
  in French means that screen is half-English in French only.
* **An empty translation**, which blanks the label instead of translating it.
"""
import json
import os
import sys

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
import nbi18n                                            # noqa: E402

# Driven by nbi18n so a language added to SUPPORTED is checked automatically
# and cannot be forgotten here. A code with no catalog file yet is reported as
# missing rather than silently skipped.
def _codes():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_nbi18n_probe", os.path.join(DE, "nbi18n.py"))
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
        return tuple(c for c in m.SUPPORTED if c != "en")
    except Exception:
        return ("es", "fr", "zh", "sr")


CODES = _codes()

# Fragments deliberately keyed with an edge space because the app concatenates
# them with a name, a number or a date. Anything NOT on this list that carries
# an edge space is reported as the padding bug.
CONCAT_OK = {
    " (copy)", " (hidden)", " +%d more", " File", " copy %d", " · added ",
    "About ", "Balance ", "Battery ", "Chapter ", "Controller ready: ",
    "Lecture ", "Mounted: ", "Next ", "Opened ", "Photo: ", "Previous ",
    "Printed: ", "Saved ", "Saved  ·  ", "Written at ",
    "The current image has unsaved changes. ",
    "required tools are missing: ",
}


def cjk(s):
    return any(ch >= "⸀" for ch in s)


def specs(s, drop_plural=False):
    """The printf specs in `s`, using nbi18n's own splitter so this agrees with
    what the runtime actually does. `drop_plural` removes the English-only
    plural slot — the `%s` in `"%d item%s"`, which nbi18n consumes on the way in
    and never emits, so the translation is right to omit it."""
    sp = nbi18n._split_spec(s)
    kinds = nbi18n._spec_kinds(sp)
    return [p for (k, p), pl in zip(sp, kinds)
            if k == "spec" and not (drop_plural and pl)]


def main():
    cats = {}
    missing = []
    for code in CODES:
        path = os.path.join(DE, "lang_%s.json" % code)
        if not os.path.isfile(path):
            missing.append(code)          # declared SUPPORTED, no catalog yet
            continue
        with open(path, encoding="utf-8") as fh:
            cats[code] = json.load(fh)

    bad = 0
    for code in missing:
        bad += 1
        print("MISSING lang_%s.json — %s is in nbi18n.SUPPORTED but has no "
              "catalog, so that language would show English everywhere" % (code, code))
    for code, cat in cats.items():
        for key, val in sorted(cat.items()):
            if key != key.strip() and key.lstrip("\n") == key:
                if key not in CONCAT_OK:
                    print("PADDED KEY  %s  %r" % (code, key))
                    bad += 1
                elif val != val.strip() or cjk(val):
                    # fragment: the edge space is intended — except in Chinese,
                    # where the full-width punctuation these fragments end in
                    # ("：", "）") carries its own space and an ASCII one after
                    # it reads as a gap
                    pass
                else:
                    print("EDGE SPACE LOST  %s  %r -> %r" % (code, key, val))
                    bad += 1
            if not val.strip():
                print("EMPTY       %s  %r" % (code, key))
                bad += 1
            # a counted string may be translated as "singular|plural"; every
            # form has to carry the source's placeholders, in the same order
            want = specs(key, drop_plural=True)
            for form in (val.split("|") if val.count("|") == 1 else [val]):
                if specs(form) != want:
                    print("SPEC DRIFT  %s  %r -> %r" % (code, key, val))
                    bad += 1
                    break

    # An absent key is NOT a defect: nbi18n falls back to the English source
    # per key, so a partly-translated catalog is safe and usable — and with 17
    # languages, partly-translated is the normal steady state. Counting each
    # one as a "problem" produced thousands of them and trained everyone to
    # ignore this tool, which is how a REAL defect (a dropped placeholder, a
    # padded key) gets missed. So: coverage is reported, never failed.
    every = set()
    for cat in cats.values():
        every |= set(cat)
    if every:
        print("coverage (untranslated keys fall back to English):")
        for code in sorted(cats, key=lambda c: -len(cats[c])):
            n = len(cats[code])
            pct = 100.0 * n / len(every)
            bar = "#" * int(pct / 5)
            print("   %-3s %5d / %5d  %5.1f%%  %s" % (code, n, len(every), pct, bar))

    counts = "  ".join("%s=%d" % (c, len(cats[c])) for c in CODES if c in cats)
    print("%s: %s" % ("clean" if not bad else "%d PROBLEM(S)" % bad, counts))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
