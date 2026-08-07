#!/usr/bin/env python3
"""
A word the interface puts on a button must be the word its sentences use.

Some English keys ARE a term: `Folder`, `Documents`, `Trash`, `Printer`. Each is
a bare label on a sidebar row or a button, so whatever a catalog says there is
what that language calls the thing, on screen, in the place the user navigates
by. Every other string that names the same thing has to agree with it.

Serbian did not. It had THREE words for a folder — `mapa` (23 strings),
`fascikla` (22) and the loanword `folder` (inside `Početni folder`, its name for
Home) — and the sidebar row said `Mapa`. Worse than untidy: this OS ships a Maps
app, and Serbian for "map" is also `mapa`, so the same catalog contained

    Ova mapa nije mogla da se pročita     "this map could not be read"
    Ova mapa je prazna                    "this folder is empty"

Identical phrasing, two different things. That is not a matter of taste, and no
other gate could see it: `i18n_check` counted seventeen complete catalogs,
`i18n_coverage` found every string covered, and both were right.

Method
------
The bare key is canonical. For every other English key naming the same term, the
translation must contain that root — stemmed per word, so German `Dokumenten`
and Polish `Dokumentów` still match, and matched whole for uninflected scripts.

Sense is decided by the ENGLISH key, which is the only place the two meanings
are still distinguishable: a key mentioning a term's declared rival ("map" for
Folder) is skipped rather than judged. Anything a term genuinely shares with
another sense belongs in `rivals`, not in a suppression list.

Run:
  python3 anchored_term_check.py             # every anchor, every language
  python3 anchored_term_check.py --de DIR    # a scratch copy, for red-proofs
  python3 anchored_term_check.py -v          # show every string checked
Exit status is nonzero if any language names one thing two ways.
"""
import json
import os
import sys
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

LANGS = ["de", "el", "eo", "es", "fr", "hi", "it", "ja", "ko", "nl", "pl", "pt",
         "ru", "sr", "tr", "yi", "zh"]

# anchor key -> other English words that mean something ELSE and must not be
# judged against it. Empty tuple means the word has only one sense here.
#
# `Desktop` is deliberately absent as an anchor: the one English word names both
# the Places folder and the surface widgets sit on, so a translator is right to
# render "Desktop Widgets" and the sidebar row differently, and including it
# produced thirty findings that could never be fixed.
# The second field says whether a LOCATIVE frame is required. Every anchor
# requires one, and the reason is worth recording, because dropping it for
# `Folder` looked obviously right and was not.
#
# Doing so found four more real Yiddish errors — strings telling the reader to
# write into the Documents FILE — but also flagged two correct translations:
# German renders "Up one folder" as "Eine Ebene höher" (one level up), which
# names no folder and is what German systems actually say, and Russian renders
# "your Home folder" with «Мои файлы», its own NAME for Home. A translation is
# allowed to use an idiom or a place-name instead of the generic noun.
#
# So this stays a REGRESSION GUARD over framed mentions rather than a complete
# sweep. It is not the whole job and does not pretend to be: the six Yiddish
# strings were fixed by reading all thirty-two `folder` keys by hand. A gate
# with false positives stops being read, and then it catches nothing at all.
ANCHORS = {
    "Documents": ((), True),
    "Pictures":  ((), True),
    "Music":     (("song", "songs", "track", "tracks", "audio", "soundtrack"),
                  True),
    "Videos":    ((), True),
    "Downloads": ((), True),
    "Folder":    (("map", "maps", "file", "files"), True),
    "Trash":     ((), True),
    "Printer":   ((), True),
    "Playlist":  ((), True),
}

# The frames that mean the word is being used as the thing, not as a modifier.
# "Add Background Music" is about audio, not the Music folder; "the %s" is
# absent because it matched "an effect layers over the music".
LOCATIVE = ("in %s", "to %s", "from %s", "into %s", "under %s", "on %s",
            "%s folder", "%s as ", "a %s", "this %s", "%s is", "%s could",
            "%s cannot", "%s has", "new %s", "%s名")

UNINFLECTED = {"ja", "zh", "ko"}
STEM_RATIO = 0.6


def norm(s):
    s = unicodedata.normalize("NFD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def stems(phrase, lang):
    """Stem each word separately: a multi-word name inflects word by word, and
    stemming the phrase as one string cuts it at the first word."""
    out = []
    for word in norm(phrase).split():
        out.append(word if lang in UNINFLECTED
                   else word[:max(3, int(len(word) * STEM_RATIO))])
    return out


def mentions(key, anchor, rivals, needs_frame):
    low = key.lower()
    a = anchor.lower()
    if needs_frame:
        if not any(frame % a in low for frame in LOCATIVE):
            return False
    elif not re.search(r"\b%s\b" % re.escape(a), low):
        return False
    return not any(re.search(r"\b%s\b" % re.escape(r), low) for r in rivals)


def main(argv):
    verbose = "-v" in argv
    cats = {}
    for l in LANGS:
        with open(os.path.join(DE, "lang_%s.json" % l), encoding="utf-8") as fh:
            cats[l] = json.load(fh)
    english = sorted(cats["de"].keys())

    findings, checked, skipped = [], 0, []
    for anchor, (rivals, needs_frame) in sorted(ANCHORS.items()):
        if anchor not in cats["de"]:
            skipped.append("%s: no such key" % anchor)
            continue
        keys = [k for k in english if k != anchor
                and mentions(k, anchor, rivals, needs_frame)]
        if not keys:
            skipped.append("%s: no sentence mentions it" % anchor)
            continue
        for lang in LANGS:
            canon = cats[lang].get(anchor)
            if not canon:
                findings.append((lang, anchor, anchor, "<untranslated>"))
                continue
            st = stems(canon, lang)
            for key in keys:
                val = cats[lang].get(key)
                if not val:
                    continue
                checked += 1
                if all(s in norm(val) for s in st):
                    if verbose:
                        print("ok   %s %-10s %s" % (lang, anchor, key[:56]))
                    continue
                findings.append((lang, anchor, key, val))

    for lang, anchor, key, val in findings:
        print("%s  %s: named differently from the label the user navigates by\n"
              "     label : %s\n"
              "     key   : %s\n"
              "     value : %s"
              % (lang, anchor, cats[lang].get(anchor), key[:88], str(val)[:110]))

    print("\n%d anchor(s), %d translated mention(s) checked across %d languages,"
          " %d finding(s)" % (len(ANCHORS) - len(skipped), checked, len(LANGS),
                              len(findings)))
    if verbose:
        for s in skipped:
            print("  skip %s" % s)
    if findings:
        langs = sorted({f[0] for f in findings})
        print("RESULT: INCONSISTENT in %s" % ", ".join(langs))
        return 1
    print("RESULT: every anchored term has one name per language")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
