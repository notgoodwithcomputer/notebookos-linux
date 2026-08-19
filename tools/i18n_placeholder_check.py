#!/usr/bin/env python3
"""
i18n_placeholder_check — a translation must carry its key's placeholders.

WHY THIS EXISTS: a catalog value is substituted with `%`. Drop a `%s`, add one,
or swap two round and the result is not a typo — it is a TypeError at the `%`
(the app dies, or nbi18n silently falls back to English for that one string and
the screen goes half-English), or worse, the right number printed in the wrong
slot. i18n_check compares the catalogs to each other and i18n_coverage_check
asks whether a key exists at all; neither reads the placeholders.

The rule this enforces is nbi18n's own, so read it there before changing it:

  * _t() refuses a translation whose specs differ from the source's and returns
    English (nbi18n._t), so an ordinary key's value carries exactly the key's
    specs, in the key's order.
  * _spec_kinds() CONSUMES the two placeholders that only exist to make English
    agree with a count: the "-s" glued to a word in "%d item%s", and the verb
    standing between a counted noun and its predicate ("Its %d task%s %s kept").
    No other language forms plurals that way, so the translation omits them and
    may instead give both grammatical numbers as "singular|plural".

So: every form of the value carries the key's ORDINARY specs, in order, and
nothing else.

  python3 tools/i18n_placeholder_check.py [--lang de es ...] [-q]

Exit non-zero when any value is wrong.
"""
import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

from nbi18n import SUPPORTED, _split_spec, _spec_kinds   # noqa: E402


def specs(s):
    return [p for k, p in _split_spec(s) if k == "spec"]


# nbi18n's spec regex is `%[-#0-9.+ ]*[a-zA-Z%]`, which does NOT match `%(name)s`
# -- so a NAMED placeholder is invisible to specs() and every value passed,
# including one with the name misspelt. That ships as a green check and a
# KeyError the moment the string is shown. Proven on
# "...cropped to %(new)d x %(new)d pixels...": `%(newe)d` -> RESULT: PLACEHOLDERS
# OK, then KeyError: 'newe' at runtime.
NAMED = re.compile(r"%\(([A-Za-z_][A-Za-z_0-9]*)\)([-#0-9.+ ]*[a-zA-Z])")
VALID_CONVERSIONS = set("diouxXeEfFgGcrsa")


def named_specs(s):
    """Every %(name)X as a typed multiset; dict placeholder order is free."""
    out = {}
    for name, spec in NAMED.findall(s):
        token = (name, spec)
        out[token] = out.get(token, 0) + 1
    return out


def bare_percents(s):
    """Count `%` that begin neither a named nor an ordinary spec. A stray one
    raises "not enough arguments" when the string is formatted with a dict."""
    n = 0
    i = 0
    while i < len(s):
        if s[i] != "%":
            i += 1
            continue
        if s.startswith("%%", i):
            i += 2
            continue
        m = NAMED.match(s, i)
        if m:
            i = m.end()
            continue
        m2 = re.match(r"%[-#0-9.+ ]*[a-zA-Z]", s[i:])
        if m2:
            i += m2.end()
            continue
        n += 1
        i += 1
    return n


def check(key, val):
    """Reasons `val` is not a usable translation of `key`. Empty list = fine."""
    sp = _split_spec(key)
    kinds = _spec_kinds(sp)
    want = [p for (k, p), kind in zip(sp, kinds) if k == "spec" and not kind]
    counted = any(kinds)
    bad = []
    invalid_key = [p for p in specs(key) if p[-1] not in VALID_CONVERSIONS]
    invalid_key += ["%%(%s)%s" % (name, spec) for name, spec in named_specs(key)
                    if spec[-1] not in VALID_CONVERSIONS]
    if invalid_key:
        bad.append("source key has unsupported conversions %s" % invalid_key)
    forms = [val]
    if val.count("|") == 1:
        if counted:
            forms = val.split("|")
        else:
            bad.append("has a | but the key has no counted noun to pick a form "
                       "with, so the whole string including the | is printed")
    elif val.count("|") > 1:
        bad.append("more than one |")
    # Named placeholders: same names, types, and number of uses.
    kn = named_specs(key)
    if kn:
        for i, form in enumerate(forms):
            vn = named_specs(form)
            if vn != kn:
                bad.append("named placeholders are %s, the key needs %s"
                           % (vn or "none", kn))

    stray_k = bare_percents(key)
    formatted = bool(want or kn or counted)
    if stray_k and formatted:
        bad.append("source key has %d unrecognized %% that will raise when formatted"
                   % stray_k)
    for i, form in enumerate(forms):
        invalid = [p for p in specs(form) if p[-1] not in VALID_CONVERSIONS]
        invalid += ["%%(%s)%s" % (name, spec)
                    for name, spec in named_specs(form)
                    if spec[-1] not in VALID_CONVERSIONS]
        if invalid:
            bad.append("unsupported conversions %s" % invalid)
        # This applies to positional strings too. Previously it lived under
        # ``if kn``, so ``Hello %s`` -> ``Hola %s %`` passed despite raising
        # ValueError at the first interpolation.
        stray = bare_percents(form)
        if stray and formatted:
            bad.append("%d stray %% that will raise when formatted" % stray)
        got = specs(form)
        if got != want:
            where = ("" if len(forms) == 1 else
                     " (%s form)" % ("singular" if not i else "plural"))
            bad.append("placeholders%s are %s, the key needs %s in that order"
                       % (where, got or "none", want or "none"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", nargs="+", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()
    langs = a.lang or [c for c in SUPPORTED if c != "en"]
    total = 0
    for code in langs:
        path = os.path.join(DE, "lang_%s.json" % code)
        try:
            with io.open(path, encoding="utf-8") as fh:
                cat = json.load(fh)
        except (OSError, ValueError) as exc:
            print("lang_%s.json: cannot read (%s)" % (code, exc))
            total += 1
            continue
        bad = []
        for key in sorted(cat):
            val = cat[key]
            if not isinstance(val, str):
                bad.append((key, val, ["value is not a string"]))
                continue
            if "%" not in key and "%" not in val:
                continue
            why = check(key, val)
            if why:
                bad.append((key, val, why))
        total += len(bad)
        print("lang_%s: %d of %d entries wrong" % (code, len(bad), len(cat)))
        for key, val, why in bad:
            print("    key %r" % key)
            print("    got %r" % val)
            for w in why:
                print("      - " + w)
    print("\nRESULT: " + ("PLACEHOLDERS OK" if not total else
                          "%d BAD VALUE(S)" % total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
