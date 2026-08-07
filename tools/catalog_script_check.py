#!/usr/bin/env python3
"""
One language, one alphabet.

Serbian is digraphic: Ćirilica and Latinica are both correct Serbian, and a
translator working alone will reasonably pick either. Working across sessions,
they picked both. `lang_sr.json` shipped with 2941 values in Latin and 143 in
Cyrillic -- the whole Bill Tracker and Widget Board block, added later than the
rest -- so a Serbian user met "Račun" in the Finder and "Рачун" in Bill Tracker
and had no way to tell they were the same word.

Nothing could see it. `i18n_check` counts keys and found all seventeen catalogs
complete at 100%. `i18n_coverage_check` found every string covered. Both were
right: the strings all existed and all said the correct thing. They were simply
written in two alphabets.

Method
------
For each catalog, count the letters of each script across all values and take
the majority as the language's script. Any value written in a DIFFERENT script
is a finding. Latin is never itself a finding in a non-Latin catalog: every
language legitimately carries `PDF`, `GBA`, `XP`, `kB`, `%s`, and the ten
catalogs on non-Latin scripts each hold ~700 such values. The check is for a
value in a *second major script* -- one the language could have been written in
but was not.

Run:
  python3 catalog_script_check.py             # every catalog
  python3 catalog_script_check.py --de DIR    # a scratch copy, for red-proofs
  python3 catalog_script_check.py -v          # list every offending value
Exit status is nonzero if any catalog mixes scripts.
"""
import json
import os
import sys
import unicodedata
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

LANGS = ["de", "el", "eo", "es", "fr", "hi", "it", "ja", "ko", "nl", "pl", "pt",
         "ru", "sr", "tr", "yi", "zh"]

TAGS = ("CYRILLIC", "LATIN", "GREEK", "HEBREW", "DEVANAGARI", "HIRAGANA",
        "KATAKANA", "CJK", "HANGUL", "ARABIC")

# Japanese is written in four scripts at once and Chinese/Korean mix Han with
# their own; grouping them means a normal Japanese sentence is not reported as
# three languages fighting.
FAMILY = {"HIRAGANA": "JAPANESE", "KATAKANA": "JAPANESE", "CJK": "JAPANESE"}
FAMILY_BY_LANG = {
    "ja": {"HIRAGANA": "JAPANESE", "KATAKANA": "JAPANESE", "CJK": "JAPANESE"},
    "zh": {"CJK": "CHINESE"},
    "ko": {"HANGUL": "KOREAN", "CJK": "KOREAN"},
}


def script_of(ch, lang):
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    for tag in TAGS:
        if tag in name:
            return FAMILY_BY_LANG.get(lang, {}).get(tag, tag)
    return None


def scripts_in(s, lang, minrun=1):
    """Scripts present in `s`, counting only runs of `minrun`+ letters.

    A single letter from another script is a SYMBOL, not prose: the Calculator's
    "Pi (3.14159…)" is π in all seventeen languages and Greek in none of them.
    Two or more adjacent letters is a word. That one rule separates every real
    finding here from every false one, and it needs no list of exempt symbols to
    fall out of date.
    """
    out = collections.Counter()
    run_script, run_len = None, 0

    def flush():
        if run_script and run_len >= minrun:
            out[run_script] += run_len

    for ch in s:
        sc = script_of(ch, lang)
        if sc == run_script:
            run_len += 1
            continue
        flush()
        run_script, run_len = sc, 1
    flush()
    return out


def main(argv):
    verbose = "-v" in argv
    findings = []
    print("%-4s %-12s %s" % ("lang", "script", "values"))
    for lang in LANGS:
        path = os.path.join(DE, "lang_%s.json" % lang)
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)

        letters = collections.Counter()
        for v in cat.values():
            if isinstance(v, str):
                letters.update(scripts_in(v, lang))
        if not letters:
            continue
        primary = letters.most_common(1)[0][0]

        # Latin inside a non-Latin catalog is product vocabulary (PDF, GBA, kB)
        # and never a finding. Anything else is a second alphabet.
        offenders = []
        for k, v in cat.items():
            if not isinstance(v, str):
                continue
            for sc in scripts_in(v, lang, minrun=2):
                if sc == primary or sc == "LATIN":
                    continue
                offenders.append((sc, k, v))
                break

        print("%-4s %-12s %d values, %d in another alphabet"
              % (lang, primary.lower(), len(cat), len(offenders)))
        for sc, k, v in offenders:
            findings.append((lang, primary, sc, k, v))
            if verbose:
                print("      %-9s %-44s %s" % (sc.lower(), k[:44], v[:56]))

    print("")
    if findings:
        by_lang = collections.Counter(f[0] for f in findings)
        for lang, n in by_lang.most_common():
            prim = next(f[1] for f in findings if f[0] == lang)
            others = collections.Counter(f[2] for f in findings if f[0] == lang)
            print("%s: %d value(s) in %s, but the catalog is %s"
                  % (lang, n, "/".join(s.lower() for s in others),
                     prim.lower()))
        if not verbose:
            print("(-v lists every one)")
        print("RESULT: %d value(s) in the wrong alphabet" % len(findings))
        return 1
    print("RESULT: every catalog is written in one alphabet")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
