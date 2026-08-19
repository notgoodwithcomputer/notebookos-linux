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
are still distinguishable, and there are two ways it decides.

A term whose OTHER sense is a different English word declares it as a `rival`:
a key mentioning "map" is not judged against `Folder`. A term whose other sense
is the SAME word declares itself a name instead, and then only the capitalised
spelling counts: `Music` is the Places row the recording is saved in, `music` is
the audio, and "A music CD needs a blank CD-R or CD-RW" is about a kind of disc
— which is why Japanese is right to write 音楽 CD there and ミュージック for the
folder. Neither mechanism is a suppression list — both say which sense is being
enforced, so a new string lands on the right side of the rule without anyone
editing this file.

Mentions of a name are found by that spelling and not by a frame list, so this
part IS a sweep: "(Documents works well)", buried in the middle of the GBA SDK's
empty-state paragraph, is checked, and it was wrong in Chinese.

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
# So for a COMMON NOUN this stays a REGRESSION GUARD over framed mentions rather
# than a complete sweep. It is not the whole job and does not pretend to be: the
# six Yiddish strings were fixed by reading all thirty-two `folder` keys by hand.
# A gate with false positives stops being read, and then it catches nothing at
# all. (A NAME is swept by its spelling and needs no frame — third field below.)
#
# The third field says whether the label is a NAME or a common noun, and it is
# what decides the SENSE of an English mention:
#
#     Music   the name on the Places row, and the app's title
#     music   the ordinary noun, the audio itself
#
# English marks that difference by capitalising the name it made out of the
# noun, and this OS's own strings hold the line: all twelve of the sentences
# that mean the folder write `Documents`, and
#
#     The disc in the drive cannot be used. A music CD needs a blank CD-R or
#     CD-RW.
#
# writes `music`, because a music CD is a KIND OF DISC. It is not a reference to
# the Music folder and never was. Japanese renders it 音楽 CD and calls the
# folder ミュージック — the same two words, kept apart the same way, and the
# catalog's own `Music CD` key already says 音楽 CD. Matching the anchor
# lowercased made that correct translation a finding, which is the failure this
# file warns about two paragraphs up: a gate with false positives stops being
# read, and then it catches nothing at all.
#
# So a NAME anchor matches only where the English spells it as a name. A common
# noun matches either way, because "A folder cannot be copied inside itself" and
# "Move to Folder" are the same word doing the same job — and the split is not a
# guess: of the mentions this gate finds, every Places one is capitalised and 30
# of the 35 for `Folder`, `Printer` and `Playlist` are not.
#
# Capitalisation alone would not be enough: "Add Background Music", "Music Box"
# and "Music CD" capitalise the ordinary noun, because a Title Case label
# capitalises everything. So a name counts where it is framed as the thing
# (below), or where it stands among lowercase words rather than inside a
# capitalised phrase — see `standalone`, which is the half of the rule the frame
# list can never finish.
ANCHORS = {
    #              rivals                          framed  is a name
    "Documents": ((),                              True,   True),
    "Pictures":  ((),                              True,   True),
    "Music":     ((),                              True,   True),
    "Videos":    ((),                              True,   True),
    "Downloads": ((),                              True,   True),
    "Folder":    (("map", "maps", "file", "files"), True,  False),
    "Trash":     ((),                              True,   True),
    "Printer":   ((),                              True,   False),
    "Playlist":  ((),                              True,   False),
}
# `Music` used to declare song/track/audio as rivals, to keep the audio sense
# out. The name/noun rule states that distinction where it actually lives, in
# the anchor's own spelling, so the rival list is gone — and it was not idle:
# it was skipping "No audio files in Home / Music" and "Tracks are read from
# Home / Music", two genuine mentions of the folder, in all seventeen
# languages. A rival that hides real mentions is a blind spot, not a guard.

# The frames that mean the word is being used as the thing, not as a modifier.
# "Add Background Music" is about audio, not the Music folder.
#
# "the %s" is still absent, and it is worth saying why it stays out now that
# `standalone` reaches "moved to the Trash" without it: a frame applies to every
# anchor, names and common nouns alike, and for a common noun "the %s" swallows
# "an effect layers over the music" and "its %d track" all over again. What made
# those six Trash strings reachable was not a new frame but the sense rule.
LOCATIVE = ("in %s", "to %s", "from %s", "into %s", "under %s", "on %s",
            "%s folder", "%s as ", "a %s", "this %s", "%s is", "%s could",
            "%s cannot", "%s has", "new %s", "%s名")

UNINFLECTED = {"ja", "zh", "ko"}
STEM_RATIO = 0.75


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


def has_stems(value, wanted, lang):
    text = norm(value)
    if lang in UNINFLECTED:
        return all(stem in text for stem in wanted)
    words, current = [], []
    for char in text:
        if char == "_" or unicodedata.category(char)[0] in ("L", "M", "N"):
            current.append(char)
        elif current:
            words.append("".join(current)); current = []
    if current:
        words.append("".join(current))
    return all(any(word.startswith(stem) for word in words)
               for stem in wanted)


def prose(text):
    """Frame text is fixed prose and its case carries no sense, so it matches
    either way; only the ANCHOR's own spelling decides anything."""
    return "(?i:%s)" % re.escape(text) if text else ""


def word(subject, a):
    """The anchor, ending at a word boundary: "in musical notation" is not a
    reference to the Music folder."""
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(a), subject) is not None


def framed(subject, a):
    return any(re.search(prose(frame.split("%s")[0]) +
                         r"(?<!\w)" + re.escape(a) + r"(?!\w)" +
                         prose(frame.split("%s")[1]), subject)
               for frame in LOCATIVE)


def standalone(key, anchor):
    """A NAME among lowercase words is the place; the same name INSIDE a Title
    Case phrase may be no more than the ordinary noun wearing a capital.

    That is the whole difference between `Music Box`, `Music CD` and `Add
    Background Music` — labels, where the capital is the label's, not the
    folder's — and `read from Home / Music` or `(Documents works well)`, where
    nothing but the folder is meant. Adjacency is a single space, so `Home /
    Music` is two phrases and not one.

    This is what the frame list cannot reach: it has to enumerate the shapes
    prose puts a term in ("%s is", "%s could"), and prose will always have one
    more. A name needs no frame, because it is already the mark of the sense.
    """
    for at in re.finditer(r"(?<!\w)%s(?!\w)" % re.escape(anchor), key):
        before = re.search(r"([A-Za-z][\w'\u2019]*) $", key[:at.start()])
        after = re.match(r" ([A-Za-z][\w'\u2019]*)", key[at.end():])
        if not ((before and before.group(1)[0].isupper()) or
                (after and after.group(1)[0].isupper())):
            return True
    return False


def mentions(key, anchor, rivals, needs_frame, is_name):
    low = key.lower()
    # A name is looked for as it is WRITTEN; a common noun in either case. This
    # is the sense test: `Music` is the Places row, `music` is the audio, and
    # "A music CD" is a kind of disc rather than a mention of the folder. See
    # ANCHORS.
    subject, a = (key, anchor) if is_name else (low, anchor.lower())
    here = framed(subject, a) if needs_frame else word(subject, a)
    if not here and not (is_name and standalone(key, anchor)):
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
    for anchor, (rivals, needs_frame, is_name) in sorted(ANCHORS.items()):
        if anchor not in cats["de"]:
            skipped.append("%s: no such key" % anchor)
            continue
        keys = [k for k in english if k != anchor
                and mentions(k, anchor, rivals, needs_frame, is_name)]
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
                if has_stems(val, st, lang):
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
    print("RESULT: PASS — every anchored term has one name per language")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
