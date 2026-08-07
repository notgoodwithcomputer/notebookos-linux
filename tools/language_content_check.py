#!/usr/bin/env python3
"""Check every de/course_*.json against docs/LANGUAGE-COURSE-FORMAT.md.

This is the MECHANICAL half of course review. It cannot tell you that an IPA
transcription is wrong, but it can tell you that a course ships a phrase the
word bank cannot be built from, a multiple-choice skill with two words glossed
"big", a tip written in cheerleader voice, or an `ipa` field that is really just
the target word typed again -- every one of which produces a lesson that looks
fine in the file and is broken or pointless in the app.

    python3 tools/language_content_check.py [code ...]
    python3 tools/language_content_check.py --shape-only     # skip depth checks

Exit status 0 only when every course passes. --shape-only drops the "is this
course the full 10x4 curriculum" checks, for working on a course mid-authoring.
"""
import os
import re
import sys
import json
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))

POS = {"noun", "verb", "adj", "adv", "pron", "prep", "conj", "num", "interj",
       "det"}

UNITS = [
    ("BASICS", ["Greetings", "People", "Phrases", "Numbers"]),
    ("EVERYDAY", ["Food", "Drinks", "Colors", "Family"]),
    ("ACTIONS", ["Verbs", "Adjectives", "Questions", "Negation"]),
    ("AT HOME", ["House", "Objects", "Clothing", "Animals"]),
    ("TIME", ["Days", "Months", "Time of Day", "Weather"]),
    ("GETTING AROUND", ["Places", "Directions", "Transport", "Travel"]),
    ("PEOPLE", ["Feelings", "Body", "Health", "Describing"]),
    ("WORK AND SCHOOL", ["School", "Work", "Big Numbers", "Money"]),
    ("OUT AND ABOUT", ["Restaurant", "Shopping", "Hobbies", "Sports"]),
    ("THE WORLD", ["Nature", "City", "Countries", "More Verbs"]),
]

WORDS_PER_SKILL = 12
PHRASES_PER_SKILL = 4

# Characters that belong to an IPA transcription and to nothing else. A field
# that contains none of these, for a language whose spelling is not already
# phonetic, is almost always the target word typed a second time.
IPA_MARKS = set("ˈˌːˑ.‿͡")
IPA_ONLY = set("ɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɝɞɟʄɡɠɢʛɦɧħɥʜɨɪʝɭɬɫɮʟɱɯɰŋɳɲɴøɵɸθœɶʘɹɺɾɻʀʁ"
               "ɽʂʃʈʧʉʊʋⱱʌɣɤʍχʎʏʑʐʒʔʡʕʢǀǁǂǃˠˤ̥̪̃̊͜ʰʲʷɪ̯")

# Voice: the OS rule is that UI text describes function and nothing else. These
# are the phrasings that keep turning up in generated tip prose.
VOICE_BAD = [
    (r"\bdon'?t worry\b", "reassurance"),
    (r"\byou'?ll\b", "second person future"),
    (r"\byou'?ve\b", "second person"),
    (r"\byou'?re\b", "second person"),
    (r"\bas you can see\b", "narration"),
    (r"\bremember,?\b", "instruction to the reader"),
    (r"\bjust\b", "minimiser"),
    (r"\beasy\b", "reassurance"),
    (r"\bsimply\b", "minimiser"),
    (r"\bgreat\b", "praise"),
    (r"\bnotice that\b", "narration"),
    (r"\blet'?s\b", "first person plural"),
    (r"!", "exclamation mark"),
]


def norm(s):
    """The grader's normalisation, copied from language._norm: this is the
    identity two entries are compared under, so it is the identity a clash has
    to be looked for under."""
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").strip().lower())
                if unicodedata.category(c) != "Mn")
    return " ".join("".join(c for c in s if c.isalnum() or c.isspace()).split())


class Check:
    def __init__(self, path, shape_only=False):
        self.path = path
        self.file = os.path.basename(path)
        self.shape_only = shape_only
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append("%s: %s" % (where, msg))

    def warn(self, where, msg):
        self.warnings.append("%s: %s" % (where, msg))

    # ---------------- top level ----------------
    def run(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                c = json.load(fh)
        except Exception as e:
            self.err(self.file, "will not parse: %s" % e)
            return self
        if not isinstance(c, dict):
            self.err(self.file, "is not an object")
            return self
        self.course = c
        for k in ("code", "name", "from", "units"):
            if not c.get(k):
                self.err(self.file, "missing %r" % k)
        if self.errors:
            return self
        self.code = c["code"]
        self.check_alphabet()
        self.check_units()
        self.check_course_wide()
        return self

    def check_alphabet(self):
        rows = self.course.get("alphabet")
        if rows is None:
            return
        if not isinstance(rows, list):
            self.err("alphabet", "is not a list")
            return
        for i, r in enumerate(rows):
            if not isinstance(r, dict) or not r.get("c") or not r.get("e"):
                self.err("alphabet[%d]" % i, "needs c and e")

    def check_units(self):
        units = self.course["units"]
        if not self.shape_only and len(units) != len(UNITS):
            self.err(self.code, "has %d units, the curriculum is %d"
                     % (len(units), len(UNITS)))
        for ui, u in enumerate(units):
            uw = "%s u%d" % (self.code, ui + 1)
            if not isinstance(u, dict):
                self.err(uw, "is not an object")
                continue
            title = u.get("title") or ""
            if not title:
                self.err(uw, "has no title")
            if len(title) > 18:
                self.err(uw, "title %r is longer than 18 chars" % title)
            sub = u.get("subtitle") or ""
            if not sub:
                self.err(uw, "has no subtitle")
            elif len(sub) > 90:
                self.err(uw, "subtitle is %d chars, cap is 90" % len(sub))
            skills = u.get("skills") or []
            if not self.shape_only:
                want_title, want_skills = UNITS[ui] if ui < len(UNITS) else ("", [])
                if title != want_title:
                    self.err(uw, "title %r, the curriculum says %r"
                             % (title, want_title))
                got = [s.get("name") for s in skills if isinstance(s, dict)]
                if got != want_skills:
                    self.err(uw, "skills %r, the curriculum says %r"
                             % (got, want_skills))
            for si, s in enumerate(skills):
                self.check_skill("%s/%s" % (uw, (s or {}).get("name", si)), s)

    # ---------------- one skill ----------------
    def check_skill(self, where, s):
        if not isinstance(s, dict):
            self.err(where, "is not an object")
            return
        name = s.get("name") or ""
        if not name:
            self.err(where, "has no name")
        elif len(name) > 14:
            self.err(where, "name %r is longer than 14 chars" % name)
        words = s.get("words") or []
        phrases = s.get("phrases") or []
        if not self.shape_only:
            if len(words) != WORDS_PER_SKILL:
                self.err(where, "has %d words, the format says %d"
                         % (len(words), WORDS_PER_SKILL))
            if len(phrases) != PHRASES_PER_SKILL:
                self.err(where, "has %d phrases, the format says %d"
                         % (len(phrases), PHRASES_PER_SKILL))
        for i, w in enumerate(words):
            self.check_term("%s w%d" % (where, i + 1), w, phrase=False)
        for i, p in enumerate(phrases):
            self.check_term("%s p%d" % (where, i + 1), p, phrase=True)
        self.check_tips(where, s.get("tips"))

        # RULE 5: two words in one skill glossed the same make a multiple-choice
        # question with two right answers, and the grader accepts one of them.
        seen = {}
        for w in words:
            if not isinstance(w, dict):
                continue
            k = norm(w.get("e"))
            if k and k in seen:
                self.err(where, "two words glossed %r: %r and %r"
                         % (w.get("e"), seen[k], w.get("t")))
            seen[k] = w.get("t")
        # ... and two spellings the grader cannot tell apart.
        seent = {}
        for w in words:
            if not isinstance(w, dict):
                continue
            k = norm(w.get("t"))
            if k and k in seent and seent[k] != w.get("t"):
                self.err(where, "%r and %r are one term to the grader"
                         % (seent[k], w.get("t")))
            seent[k] = w.get("t")

    def check_term(self, where, w, phrase):
        if not isinstance(w, dict):
            self.err(where, "is not an object")
            return
        t, e, ipa = w.get("t"), w.get("e"), w.get("ipa")
        for key, v in (("t", t), ("e", e), ("ipa", ipa)):
            if not isinstance(v, str) or not v.strip():
                self.err(where, "%r is empty" % key)
                return
        if not phrase:
            pos = w.get("pos")
            if pos not in POS:
                self.err(where, "%r pos=%r, not one of the fixed list"
                         % (t, pos))
        if ipa.strip("/[] ") != ipa:
            self.err(where, "%r ipa carries its own slashes/brackets" % t)
        # RULE 2: an `ipa` that is the target word typed again teaches nothing.
        # Judge it on marks, not on spelling: a phonetic orthography legitimately
        # transcribes close to itself, so require only that SOMETHING phonetic is
        # present -- a stress mark, a syllable dot, or a non-orthographic letter.
        if not (set(ipa) & (IPA_MARKS | IPA_ONLY)):
            self.warn(where, "%r ipa=%r carries no IPA marks at all" % (t, ipa))
        if norm(t) == norm(e):
            self.warn(where, "%r is glossed as itself" % t)
        toks = t.split()
        if phrase:
            # RULE 1: the word bank splits on whitespace and asks for the
            # sentence back. One tile is not an exercise.
            if len(toks) < 3:
                self.err(where, "phrase %r is %d tokens; the word bank needs 3+"
                         % (t, len(toks)))
            if len(toks) > 8:
                self.warn(where, "phrase %r is %d tokens" % (t, len(toks)))
            if len(e.split()) < 2:
                self.warn(where, "phrase %r is glossed with one word" % t)
        else:
            if len(toks) > 3:
                self.warn(where, "word %r is %d tokens; is it a phrase?"
                          % (t, len(toks)))
        note = w.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 24):
            self.err(where, "%r note must be a string of 24 chars or fewer" % t)

    def check_tips(self, where, tips):
        if not isinstance(tips, list) or not tips:
            self.err(where, "has no tips")
            return
        if len(tips) > 3:
            self.err(where, "has %d tip cards, cap is 3" % len(tips))
        for i, tip in enumerate(tips):
            tw = "%s tip%d" % (where, i + 1)
            if not isinstance(tip, dict):
                self.err(tw, "is not an object")
                continue
            h, b = tip.get("h"), tip.get("b")
            if not isinstance(h, str) or not h.strip():
                self.err(tw, "has no heading")
            else:
                if len(h) > 40:
                    self.err(tw, "heading is %d chars, cap is 40" % len(h))
                if h.rstrip().endswith("."):
                    self.err(tw, "heading ends in a full stop")
            if not isinstance(b, str) or not b.strip():
                self.err(tw, "has no body")
                continue
            if len(b) > 400:
                self.err(tw, "body is %d chars, cap is 400" % len(b))
            for pat, why in VOICE_BAD:
                if re.search(pat, b, re.I):
                    self.err(tw, "body breaks the voice rule (%s): %r"
                             % (why, re.search(pat, b, re.I).group(0)))
            eg = tip.get("eg")
            if eg is None:
                continue
            if not isinstance(eg, list) or len(eg) > 4:
                self.err(tw, "eg must be a list of at most 4 pairs")
                continue
            for j, row in enumerate(eg):
                if (not isinstance(row, list) or len(row) != 2
                        or not all(isinstance(x, str) and x.strip()
                                   for x in row)):
                    self.err(tw, "eg[%d] is not a [target, english] pair" % j)

    # ---------------- whole course ----------------
    def check_course_wide(self):
        """Clashes only visible across skills. The exercise generator draws its
        distractors from the WHOLE course, so a collision two units apart still
        lands in one question."""
        by_t, by_e = {}, {}
        for u in self.course["units"]:
            for s in u.get("skills") or []:
                if not isinstance(s, dict):
                    continue
                for w in (s.get("words") or []):
                    if not isinstance(w, dict):
                        continue
                    t, e = w.get("t"), w.get("e")
                    if not isinstance(t, str) or not isinstance(e, str):
                        continue
                    at = "%s/%s" % (u.get("title"), s.get("name"))
                    by_t.setdefault(norm(t), []).append((t, e, at))
                    by_e.setdefault(norm(e), []).append((t, e, at))

        # RULE 4: a term with two readings is legal and handled -- but every one
        # should be deliberate, so list them for the author to confirm.
        for k, rows in sorted(by_t.items()):
            meanings = {norm(e) for _t, e, _a in rows}
            if len(meanings) > 1:
                self.warn(self.code, "%r is taught with %d meanings: %s"
                          % (rows[0][0], len(meanings),
                             "; ".join("%r in %s" % (e, a)
                                       for _t, e, a in rows)))
        # A single English gloss shared by two DIFFERENT target words makes
        # "Translate to <language>" ungradeable unless the engine's synonym map
        # picks it up -- it does, but only for words in the same course, so this
        # is a warning to confirm both really are synonyms.
        for k, rows in sorted(by_e.items()):
            targets = {norm(t) for t, _e, _a in rows}
            if len(targets) > 1:
                self.warn(self.code, "%r is the gloss of %d terms: %s"
                          % (rows[0][1], len(targets),
                             "; ".join("%r in %s" % (t, a)
                                       for t, _e, a in rows)))

        # Distractors for multiple choice come from the same part of speech.
        # A course with only two adverbs cannot build a four-option adverb
        # question, and the generator silently falls back to a shorter list.
        pos_count = {}
        for u in self.course["units"]:
            for s in u.get("skills") or []:
                for w in (s.get("words") or []):
                    if isinstance(w, dict):
                        pos_count[w.get("pos")] = pos_count.get(w.get("pos"), 0) + 1
        for p, n in sorted(pos_count.items()):
            if p in POS and n < 4:
                self.warn(self.code, "only %d %s in the whole course; "
                          "a same-pos question falls back to %d options"
                          % (n, p, n))


def main(argv):
    shape_only = "--shape-only" in argv
    codes = [a for a in argv if not a.startswith("-")]
    files = sorted(f for f in os.listdir(DE)
                   if f.startswith("course_") and f.endswith(".json"))
    if codes:
        files = [f for f in files
                 if f[len("course_"):-len(".json")] in codes]
    if not files:
        print("no course files matched")
        return 2
    bad = 0
    for f in files:
        c = Check(os.path.join(DE, f), shape_only).run()
        nw = len(c.warnings)
        if c.errors:
            bad += 1
            print("FAIL %s  (%d errors, %d warnings)" % (f, len(c.errors), nw))
            for e in c.errors:
                print("   x %s" % e)
        else:
            print("ok   %s  (%d warnings)" % (f, nw))
        if "-v" in argv or "--warnings" in argv:
            for w in c.warnings:
                print("   ? %s" % w)
    print()
    print("%d/%d courses pass" % (len(files) - bad, len(files)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
