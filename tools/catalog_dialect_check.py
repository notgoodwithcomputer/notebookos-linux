#!/usr/bin/env python3
"""
One language, one dialect.

`catalog_script_check` asks whether a catalog is written in one alphabet. This
asks the next question down: whether it is written in one variety of the
language. Serbian is the case that prompted it, and the same catalog that
shipped in two alphabets also shipped in two dialects — **233 Ijekavian word
forms against 474 Ekavian**, so a reader met `vrijeme` on one screen and `vreme`
on the next, `mjesto` and `mesto`, `riječ` and `reč`.

Both are correct Serbian. Neither is a typo, neither is a missing string, and
every other gate was satisfied: seventeen catalogs at 100%, every string
covered, one alphabet each. An interface may still only speak one of them.

Method
------
Yat, the Common Slavic vowel that split the dialects, has a small closed set of
reflexes in the vocabulary an interface actually uses. Each entry below is a
stem pair — the Ijekavian form and its Ekavian counterpart — and a catalog fails
if BOTH appear in it.

Stems, never a blanket ije->e rewrite. Most `-je` in Serbian is not yat at all:
`nije`, `koje`, `svoje`, `dvoje`, `boje` and the copula `je` would all be
mangled by a general rule. Two stems that look like yat and are not are excluded
by name — `započeti` (to begin) and `prijem` (reception; "Prijemno sanduče" is
the Inbox), both identical in either dialect.

The check is per-language and only Serbian declares a pair set today. Adding
another language means adding its variation axis here, not rewriting the tool.

Run:
  python3 catalog_dialect_check.py             # every catalog that declares one
  python3 catalog_dialect_check.py --de DIR    # a scratch copy, for red-proofs
  python3 catalog_dialect_check.py -v          # show every offending value
Exit status is nonzero if a catalog mixes dialects.
"""
import json
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

# lang -> (name of the standard variety, name of the other, [(other, standard)])
DIALECTS = {
    "sr": ("Ekavian", "Ijekavian", [
        ("vrijem", "vrem"), ("mjest", "mest"), ("riječ", "reč"),
        ("uspje", "uspe"), ("pjesm", "pesm"), ("cijel", "cel"),
        ("bijel", "bel"), ("mjer", "mer"), ("vrijed", "vred"),
        ("promijen", "promen"), ("zamijen", "zamen"), ("nedjelj", "nedelj"),
        ("poslije", "posle"), ("naprijed", "napred"), ("ovdje", "ovde"),
        ("gdje", "gde"), ("djelu", "delu"), ("odjelj", "odelj"),
        ("premješ", "premeš"), ("pomjer", "pomer"), ("primjer", "primer"),
        ("provjer", "prover"), ("nalijep", "nalep"), ("zalijep", "zalep"),
        ("smjen", "smen"), ("smjer", "smer"), ("umjest", "umest"),
        ("namjer", "namer"), ("zahtjev", "zahtev"), ("prijevod", "prevod"),
        ("dvije", "dve"), ("razdjel", "razdel"), ("vjerovat", "verovat"),
    ]),
}

# Sequences that look like a yat reflex and are not. `prije` is a real pair
# above, so `prijem`/`prijava`/`prijatelj` have to be named to protect them.
NOT_YAT = ("započ", "prijem", "prijav", "prijat")

WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def offenders(cat, stem):
    """Values containing `stem` in a word that is not on the not-yat list."""
    out = []
    for k, v in cat.items():
        if not isinstance(v, str):
            continue
        for w in WORDS.findall(v):
            lw = w.lower()
            if stem in lw and not any(n in lw for n in NOT_YAT):
                out.append((k, w, v))
                break
    return out


def main(argv):
    verbose = "-v" in argv
    total = 0
    findings = []

    for lang, (standard, other, pairs) in sorted(DIALECTS.items()):
        path = os.path.join(DE, "lang_%s.json" % lang)
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)

        other_hits = collections.Counter()
        std_hits = collections.Counter()
        examples = {}
        for a, b in pairs:
            oa = offenders(cat, a)
            ob = offenders(cat, b)
            if oa:
                other_hits[a] = len(oa)
                examples[a] = oa
            if ob:
                std_hits[b] = len(ob)
        total += len(pairs)

        n_other = sum(other_hits.values())
        n_std = sum(std_hits.values())
        print("%-3s %d value(s) in %s, %d in %s"
              % (lang, n_other, other, n_std, standard))
        for stem in sorted(other_hits):
            findings.append((lang, standard, other, stem, examples[stem]))
            if verbose:
                for k, w, v in examples[stem][:3]:
                    print("      %-10s %-38s %s" % (w, k[:38], v[:52]))

    for lang, standard, other, stem, ex in findings:
        k, w, v = ex[0]
        print("%s  %s is %s; the catalog is %s  (%d value%s, e.g. %r)"
              % (lang, w, other, standard, len(ex),
                 "" if len(ex) == 1 else "s", k[:56]))

    print("\n%d yat pair(s) checked, %d stem(s) in the wrong dialect"
          % (total, len(findings)))
    if findings:
        print("RESULT: MIXED DIALECT in %s"
              % ", ".join(sorted({f[0] for f in findings})))
        return 1
    print("RESULT: every catalog speaks one dialect")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
