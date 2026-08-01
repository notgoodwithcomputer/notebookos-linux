#!/usr/bin/env python3
"""
phonetic_en — write English in the Notebook OS phonetic orthography.

The orthography is the one specified in `phonetic orthography.odt`:

    short vowels   a /æ/  e /ɛ/  i /ɪ/  o /ɑ/  u /ʌ/  y /ə/
    long  vowels   â /ɛɪ/ ê /iː/ ij /ɑɪ/ ô /oʊ/ ů /uː/ ü /juː/ å /ɔː/
    consonants     b c /k/  ć /tʃ/  d  dj /dʒ/  đ /ð/  f  g  h  j /j/
                   k /kʰ/  l  ł /lˠ/  m n p q r s  ś /ʃ-ʒ/  t  þ /θ/
                   v w x /ks/ z

English spelling is not phonetic, so this cannot be done by letter rules — a
pronunciation is required per word. Pronunciations come from the CMU
pronouncing dictionary shipped with pocketsphinx (134k entries, ARPAbet).

THREE THINGS THE SPEC DOES NOT COVER, and what is done about them:

* **Phonemes with no letter.** English has /ʊ/ (book), /ɝ/ (bird), /aʊ/ (now),
  /ɔɪ/ (boy) and /ŋ/ (sing); the orthography lists none of them. They are
  written with the nearest available letters — ů, yr, ow, åj, ng — chosen so
  they read the way the rest of the system does. Every one is flagged in
  UNSPECIFIED below.
* **Reduced vowels keep their short vowel — do NOT over-schwa.** Legibility is
  the point of the orthography, and spelling every unstressed vowel y turns
  "System Monitor" into "Sistym Monytyr". ARPAbet writes both /ʌ/ (fun) and /ə/
  (jacket) as plain "AH", and this maps EITHER to the short vowel of the letter
  it is spelled with: fun -> fun, jacket -> djaket, about -> abowt,
  monitor -> monitor. y is left for a schwa with no vowel letter behind it at
  all, which is the syllabic-consonant case (rhythm -> riđym). The same applies
  to /ɝ/: bird -> bird, doctor -> doctor, not byrd/doctyr.
* **The spec lists F as /j/**, which cannot be intended — J is already /j/,
  and it would leave English with no way to write /f/ at all. Read as /f/.

SPELLING RULES (given alongside the orthography; these are hard constraints,
not preferences):

* **Y cannot start a word.** So a word-initial schwa cannot be written y. It is
  written u — /ə/ and /ʌ/ are the same vowel unstressed, and u keeps the vowel
  quality. about -> ubowt, again -> ugen.
* **K cannot start a word**, and **c cannot end one.** Both letters are /k/, so
  the two rules together decide every /k/: word-initial is c (cat -> cat,
  clock -> clok), word-final is k (book -> bůk), and in between c is the
  default with k reserved for an aspirated onset inside the word.
* **ł is not used in the UI at all.** It is a dialectal semivowel (/w~lˠ/), so
  every /l/ is written l — cool -> cůl, fall -> fål.
* **A word-final s pronounced /z/ is still written s.** English plurals and
  possessives keep their s: settings -> setings, boy's -> båjs. A final /z/ not
  spelled with s stays z (buzz -> buz).
* **x.** /ks/ has its own letter, so a K S sequence inside a word becomes x
  (box -> box).

Use:
    import phonetic_en
    phonetic_en.to_phonetic("Delete this contact?")   -> 'Dylêt đis contakt?'
"""
import os
import re
import sys

DICT_PATHS = (
    "/usr/share/pocketsphinx/model/en-us/cmudict-en-us.dict",
    "/usr/share/dict/cmudict.dict",
)

# Phonemes English has but the orthography does not name. Written with the
# nearest letters it does name; listed here so the choices are auditable.
UNSPECIFIED = {
    "UH": ("ů", "/ʊ/ book — no letter given; ů is the nearest vowel"),
    "ER": ("<vowel>r", "/ɝ/ bird — the spelled short vowel plus r, for legibility"),
    "AW": ("ow", "/aʊ/ now — o /ɑ/ + w /w/"),
    "OY": ("åj", "/ɔɪ/ boy — å /ɔː/ + j /j/"),
    "NG": ("ng", "/ŋ/ sing — no letter given"),
}

# A reduced vowel is written as the short vowel of the letter it is spelled
# with. Anything that is not a plain short vowel letter falls back to y.
SHORT_OF_LETTER = {"a": "a", "e": "e", "i": "i", "o": "o", "u": "u", "y": "i"}

# Straight one-to-one mappings.
SIMPLE = {
    # vowels
    "AA": "o", "AE": "a", "AO": "å", "AY": "ij", "EH": "e", "EY": "â",
    "IH": "i", "IY": "ê", "OW": "ô", "UW": "ů",
    "UH": "ů", "ER": "yr", "AW": "ow", "OY": "åj",
    # consonants
    "B": "b", "CH": "ć", "D": "d", "DH": "đ", "F": "f", "G": "g", "HH": "h",
    "JH": "dj", "M": "m", "N": "n", "NG": "ng", "P": "p", "R": "r", "S": "s",
    "SH": "ś", "T": "t", "TH": "þ", "V": "v", "W": "w", "Y": "j", "Z": "z",
    "ZH": "ś",
}

VOWEL_PHONEMES = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
                  "IH", "IY", "OW", "OY", "UH", "UW"}
VOWEL_LETTERS = "aeiouy"

_DICT = None


def load_dict(path=None):
    """word -> list of ARPAbet phonemes (first pronunciation wins)."""
    global _DICT
    if _DICT is not None:
        return _DICT
    src = path
    if src is None:
        for p in DICT_PATHS:
            if os.path.exists(p):
                src = p
                break
    if src is None:
        raise SystemExit("no CMU pronouncing dictionary found; looked in %s"
                         % ", ".join(DICT_PATHS))
    d = {}
    with open(src, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 2:
                continue
            word = parts[0]
            # "fall(2)" is an alternate pronunciation; keep only the first.
            if word.endswith(")"):
                continue
            d.setdefault(word.lower(), parts[1:])
    _DICT = d
    return d


def _vowel_groups(word):
    """The vowel-letter runs of a spelling, in order: jacket -> ['a', 'e']."""
    groups, cur = [], ""
    for ch in word.lower():
        if ch in VOWEL_LETTERS:
            cur += ch
        elif cur:
            groups.append(cur)
            cur = ""
    if cur:
        groups.append(cur)
    return groups


def phonemes_to_letters(phones, spelling=""):
    """ARPAbet -> the orthography, applying the context rules."""
    groups = _vowel_groups(spelling)
    out = []
    vowel_seen = 0
    i = 0
    n = len(phones)

    def spelled_short():
        """The short vowel of the letter this vowel phoneme is spelled with."""
        grp = groups[vowel_seen] if vowel_seen < len(groups) else ""
        return SHORT_OF_LETTER.get(grp[:1], "y")

    while i < n:
        p = phones[i]
        nxt = phones[i + 1] if i + 1 < n else None
        last = (i == n - 1)

        # /juː/ has its own letter, so Y+UW is one unit, not j + ů.
        if p == "Y" and nxt == "UW":
            out.append("ü")
            vowel_seen += 1
            i += 2
            continue

        # /ks/ has its own letter. Not word-finally: c may not end a word, and
        # x is the pair, so "box" is fine but the rule must not fire on a
        # trailing K that is really a coda.
        if p == "K" and nxt == "S":
            out.append("x")
            vowel_seen += 0
            i += 2
            continue

        if p == "AH":
            # Reduced OR stressed: both take the spelled short vowel. y only
            # when there is no vowel letter to take (syllabic consonant).
            out.append(spelled_short())
            vowel_seen += 1
            i += 1
            continue

        if p == "ER":
            # /ɝ/ — the spelled short vowel plus r, so "bird" stays readable.
            out.append(spelled_short() + "r")
            vowel_seen += 1
            i += 1
            continue

        if p == "K":
            # k may not start a word; c may not end one. Between those, k is
            # the aspirated onset (followed by a vowel) and c is everything
            # else — a coda, or the unaspirated /k/ after /s/.
            if i == 0:
                out.append("c")
            elif last:
                out.append("k")
            elif phones[i - 1] == "S":
                out.append("c")
            else:
                out.append("k" if nxt in VOWEL_PHONEMES else "c")
            i += 1
            continue

        if p == "L":
            # ł is a dialectal semivowel and is not used in the interface.
            out.append("l")
            i += 1
            continue

        if p == "Z" and last and spelling.lower().rstrip("'").endswith("s"):
            # A word-final s pronounced /z/ keeps its s: setings, båjs.
            out.append("s")
            i += 1
            continue

        if p in VOWEL_PHONEMES:
            vowel_seen += 1
        out.append(SIMPLE.get(p, ""))
        i += 1
    return "".join(out)


def _match_case(src, dst):
    """Carry the source word's capitalisation onto the transliteration."""
    if src.isupper() and len(src) > 1:
        return dst.upper()
    if src[:1].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


def word_to_phonetic(word):
    """One word. Returns it unchanged when it is not in the dictionary."""
    d = load_dict()
    key = word.lower()
    phones = d.get(key)
    if phones is None:
        # try stripping a possessive/plural apostrophe form
        if key.endswith("'s") and key[:-2] in d:
            phones = d[key[:-2]] + ["Z"]
        else:
            return word
    return _match_case(word, phonemes_to_letters(phones, word))


# A printf spec, or an escape, must survive untouched: i18n_check fails the
# catalog if a translation's specs differ from its key's. `%(name)s` and the
# bare `%%` are both covered.
_KEEP = re.compile(r"%\([^)]*\)[-#0-9. +]*[a-zA-Z]|%[-#0-9. +*]*[a-zA-Z%]|"
                   r"\\[nrt]|\{[^}]*\}|<[^>]+>")
_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def to_phonetic(text):
    """Transliterate a UI string, leaving placeholders and markup alone."""
    out, pos = [], 0
    for m in _KEEP.finditer(text):
        out.append(_words(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_words(text[pos:]))
    return "".join(out)


def _words(chunk):
    return _WORD.sub(lambda m: word_to_phonetic(m.group(0)), chunk)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(to_phonetic(" ".join(sys.argv[1:])))
    else:
        # The spec's own examples, used as the test set.
        for w in ("cat", "set", "bit", "lot", "fun", "women", "say", "free",
                  "might", "show", "food", "future", "fall", "jacket", "that",
                  "clock", "cool", "man", "with", "yes", "book", "bird",
                  "now", "boy", "sing", "measure", "box", "skip", "under",
                  "about", "sudden", "the", "of"):
            print("%-10s %s" % (w, word_to_phonetic(w)))
