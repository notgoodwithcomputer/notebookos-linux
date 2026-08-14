#!/usr/bin/env python3
"""A runtime string too short for the format table to recognise.

nbi18n wraps the setters themselves — Gtk.Label.set_text, Button.set_label,
set_tooltip_text and the rest — so a bare literal handed to one of them at
runtime IS translated. Measured, not assumed: under NB_LANG=fr,
`label.set_text("Music")` reads back "Musique". That is why so much of this OS
passes bare English and still ships in seventeen languages, and it is why the
obvious version of this check — "flag every unwrapped literal reaching a
setter" — is WRONG. I wrote that version first. It reported 61 findings, every
one of them false.

WHAT IS ACTUALLY BROKEN is narrower and real. A string SUBSTITUTED before it
reaches the setter arrives as text that is no longer a catalog key: "%s used of
%s" becomes "2 GB used of 8 GB". nbi18n recovers those with a format table that
matches the substituted text back to its source pattern — but only when the
pattern has enough literal text to be unmistakable. Its own rule is at least
three non-space characters in the longest literal run, because anything shorter
would match a filename or a song title and mistranslate the user's own words.

So a format string whose longest literal run is under three characters can
NEVER be recovered, and renders in English in all sixteen other languages,
permanently. Measured under NB_LANG=fr: "%s used of %s" % (...) comes back
"2 GB utilisés sur 8 GB", and "of %s" % (...) comes back "of Vacation" — the
catalog holds "sur %s" and nothing ever reaches it.

The fix at each site is to ask the catalog directly — `_t("of %s") % name` —
which looks the key up instead of hoping the table can reverse-engineer it.

NEITHER EXISTING GATE SEES THIS: i18n_check compares catalogs against each
other and reports 100% x 17; i18n_source_coverage finds the literal present in
the source and the key present in the catalog. What is absent is the _t() call,
at exactly the site where the recovery heuristic cannot help.
"""
import argparse
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.normpath(os.path.join(
    HERE, "..", "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))

# The setters that put text on screen after the widget already exists.
SETTERS = ("set_text", "set_markup", "set_label", "set_tooltip_text",
           "set_placeholder_text", "set_title", "set_subtitle")

# Construction-time text is translated by the tree walk, so a literal there is
# the OS's normal idiom and not a finding. These are the methods that BUILD a
# window; anything else runs after the walk.
BUILDERS = ("__init__",)


def catalog_keys():
    """Every string somebody has already translated."""
    for name in sorted(os.listdir(DE)):
        if name.startswith("lang_") and name.endswith(".json"):
            with open(os.path.join(DE, name), encoding="utf-8") as fh:
                return set(json.load(fh))
    return set()


def asks_for_translation(node):
    """True when this expression passes through _t() somewhere."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == "_t":
            return True
    return False


def literals_in(node):
    """The bare string constants this expression would display."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


# nbi18n's own threshold: a pattern needs three non-space characters of
# literal text before its substituted form can be matched back safely.
MIN_ANCHOR = 3


def recoverable(text):
    """Could the format table match this pattern's substituted form back?

    A string with no placeholder is not substituted at all and reaches the
    setter as its own key, so it is always fine. One WITH placeholders is only
    recoverable when some literal run survives long enough to anchor on."""
    import re
    parts = re.split(r"%[-#0-9.+ ]*[a-zA-Z%]|%\([A-Za-z_][A-Za-z_0-9]*\)"
                     r"[-#0-9.+ ]*[a-zA-Z]", text)
    if len(parts) == 1:
        return True                    # nothing substituted; the key arrives whole
    longest = max((p.replace(" ", "") for p in parts), key=len, default="")
    return len(longest) >= MIN_ANCHOR


def scan(path, keys):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    # Map every function to whether it builds the window or runs later.
    findings = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name in BUILDERS:
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in SETTERS or not call.args:
                continue
            arg = call.args[0]
            if asks_for_translation(arg):
                continue
            for text in literals_in(arg):
                if text in keys and not recoverable(text):
                    findings.append((call.lineno, fn.name, text))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail", action="store_true",
                    help="exit non-zero on any finding")
    args = ap.parse_args()

    keys = catalog_keys()
    if not keys:
        print("RESULT: FAILED: no catalogs found")
        return 1
    total = 0
    for name in sorted(os.listdir(DE)):
        if not name.endswith(".py") or name.startswith("lang_"):
            continue
        try:
            found = scan(os.path.join(DE, name), keys)
        except SyntaxError as exc:
            print("%s: does not parse (%s)" % (name, exc))
            total += 1
            continue
        if not found:
            continue
        print("=== %s: %d translated string(s) shown untranslated" %
              (name, len(found)))
        for line, fn, text in found:
            print("  %s:%d  %s()  %r" % (name, line, fn, text[:60]))
        total += len(found)

    print("\n%d runtime string(s) throwing away a translation somebody wrote"
          % total)
    print("RESULT: " + ("PASS" if not total else "FAILED"))
    return 1 if (total and args.fail) else 0


if __name__ == "__main__":
    sys.exit(main())
