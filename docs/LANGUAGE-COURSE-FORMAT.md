# Language course format

`de/language.py` ships no lessons. It ships a lesson *engine*, and every lesson
it shows is generated at run time from one `de/course_<code>.json` file. A course
file is therefore the entire curriculum: get it right and the app is a real
course, get it wrong and the app cheerfully generates a question nobody can
answer.

This document is the contract. `tools/language_content_check.py` enforces the
mechanical half of it; the rest is on the author.

---

## 1. The shape of a file

```json
{
  "code": "es",
  "name": "Spanish",
  "from": "English",
  "note": "optional single line shown beside the course title",
  "alphabet": [
    {"c": "ñ", "ipa": "ɲ", "e": "ny, as in canyon"}
  ],
  "units": [
    {
      "title": "BASICS",
      "subtitle": "Greet people, count, and say who is who",
      "skills": [
        {
          "name": "Greetings",
          "icon": "contacts",
          "tips": [
            {
              "h": "Two ways to say you",
              "b": "Spanish has a familiar tú for friends and family and a "
                   "polite usted for strangers and anyone older.",
              "eg": [["¿Cómo estás?", "How are you? (familiar)"],
                     ["¿Cómo está?", "How are you? (polite)"]]
            }
          ],
          "words": [
            {"t": "hola", "e": "hello", "ipa": "ˈo.la", "pos": "interj"},
            {"t": "hombre", "e": "man", "ipa": "ˈom.bɾe", "pos": "noun",
             "note": "el hombre (m)"}
          ],
          "phrases": [
            {"t": "¿Cómo estás?", "e": "How are you?",
             "ipa": "ˈko.mo es.ˈtas", "lit": "how you-are"}
          ]
        }
      ]
    }
  ]
}
```

### Course keys

| key | required | meaning |
|---|---|---|
| `code` | yes | two-letter course code; also the progress-file key prefix, so **never change it** once shipped or every learner's crowns detach from their skills |
| `name` | yes | the language, in English, as the picker and the "Translate to %s" instruction show it |
| `from` | yes | the language taught *from*. Always `English` today |
| `note` | no | one line beside the course title for a caveat the learner meets immediately (Mandarin is taught in pinyin, not characters) |
| `alphabet` | no | pronunciation primer rows, shown on the course's Alphabet card. `c` = the letter or digraph, `ipa` = its sound, `e` = an English anchor |
| `units` | yes | the tree, in order |

### Unit keys

| key | required | meaning |
|---|---|---|
| `title` | yes | short, upper case, ≤ 18 chars — it is a banner heading |
| `subtitle` | yes | one plain sentence naming what the unit covers. Shown under the title on the banner and on the checkpoint card |
| `skills` | yes | 4 skills, in teaching order |

### Skill keys

| key | required | meaning |
|---|---|---|
| `name` | yes | ≤ 14 chars: it sits under a 64px node and wraps at 11 characters |
| `icon` | no | an `nbicons` glyph name. Omitted, the app picks one from the skill name |
| `tips` | yes | 1–3 tip cards. See below |
| `words` | yes | 12 single terms |
| `phrases` | yes | 4 whole sentences |

### Word keys

| key | required | meaning |
|---|---|---|
| `t` | yes | the term in the target language, as a learner would write it |
| `e` | yes | its English meaning |
| `ipa` | yes | IPA for `t`. No slashes or brackets — the app adds them |
| `pos` | yes | part of speech, from the fixed list below |
| `note` | no | ≤ 24 chars of grammar the bare word hides: gender, an irregular plural, a required preposition |

`pos` is one of: `noun` `verb` `adj` `adv` `pron` `prep` `conj` `num` `interj`
`det`.

It is not decoration. Multiple-choice distractors are drawn **from the same part
of speech** as the answer, so a question about a verb offers three other verbs
instead of a verb, a colour and the number seven — which is the difference
between a test and a giveaway.

### Phrase keys

Same as a word, minus `pos`, plus:

| key | required | meaning |
|---|---|---|
| `lit` | no | a word-for-word gloss, for a sentence whose English is not built the way the target is. Shown on the teaching card under the meaning |

### Tip keys

| key | required | meaning |
|---|---|---|
| `h` | yes | heading, ≤ 40 chars, sentence case, no final full stop |
| `b` | yes | 1–3 sentences of plain prose. No second person imperatives, no encouragement — state the rule |
| `eg` | no | up to 4 `[target, english]` example pairs, rendered as a two-column table |

---

## 2. The curriculum

Every course teaches the same 10 units of 4 skills, in the same order, under the
same English skill names. That is deliberate: a learner who has done half of
Spanish and starts French meets a tree they already know how to read, and the
app can key an icon, a Tips heading or a checkpoint off a skill name without
five special cases.

| # | Unit | Subtitle covers | Skills |
|---|---|---|---|
| 1 | BASICS | greeting, naming people, counting to ten | Greetings, People, Phrases, Numbers |
| 2 | EVERYDAY | food, drink, colour, household people | Food, Drinks, Colors, Family |
| 3 | ACTIONS | the present tense and how to ask and deny | Verbs, Adjectives, Questions, Negation |
| 4 | AT HOME | the house and what is in it | House, Objects, Clothing, Animals |
| 5 | TIME | the calendar, the clock and the weather | Days, Months, Time of Day, Weather |
| 6 | GETTING AROUND | places, directions and transport | Places, Directions, Transport, Travel |
| 7 | PEOPLE | feeling, body, health, description | Feelings, Body, Health, Describing |
| 8 | WORK AND SCHOOL | school, jobs, numbers past ten, money | School, Work, Big Numbers, Money |
| 9 | OUT AND ABOUT | ordering, buying, and what people do for fun | Restaurant, Shopping, Hobbies, Sports |
| 10 | THE WORLD | nature, the city, countries, more verbs | Nature, City, Countries, More Verbs |

Totals per course: **40 skills, 480 words, 160 phrases, ~80 tip cards.**

---

## 3. Rules the engine imposes

These are not style preferences. Each one is a lesson that cannot be completed
if you break it.

1. **A phrase is built from its own tokens.** The word-bank exercise splits `t`
   on whitespace and asks the learner to reassemble it. A phrase whose tokens
   are punctuation-glued (`¿Cómo`) is fine — the grader normalises punctuation
   away — but a one-token phrase makes a bank of one tile. Keep phrases 3–7
   tokens.

2. **`ipa` must be IPA.** Not a respelling, not the target text again. It is
   rendered in DejaVu Sans, which is the only shipped face with full IPA
   coverage, and it is the *only* pronunciation this offline app can give — there
   is no audio anywhere on this system.

3. **Distinct terms must be distinctly spelled.** Two entries whose `t` differs
   only by accent or case are one term to the grader (`_norm` strips accents,
   because a learner may not have the keys), so they must not carry different
   meanings.

4. **One meaning, or say so deliberately.** A `t` may appear in two skills with
   two meanings — French *fille* is "girl" and "daughter" — and the engine
   accepts both readings everywhere and never offers them as rival options. But
   an *accidental* second reading silently widens the grader. Repeat a term only
   when the second meaning is real.

5. **English glosses inside one skill must be distinct.** Twelve words in one
   skill produce four multiple-choice options from that skill; two words glossed
   "big" make a question with two right answers.

6. **No English gloss may be a substring-identical duplicate of its target.**
   Spanish *no* / English "no" is unavoidable and handled, but it makes a
   matching round ambiguous to read; keep it to the genuine cases.

7. **12 and 4.** The lesson generator draws a level-1 lesson from 6 new terms
   and builds later levels from the whole skill. Fewer than 12 words makes level
   5 repetitive; more makes level 1 never finish teaching.

---

## 4. Voice

The OS-wide rule applies here too, and tips are the easiest place to break it:
**every piece of UI text describes function and nothing else.** No second
person cheerleading, no "don't worry", no "as you can see". A tip states how the
language works.

Wrong: *"Don't worry if this feels strange — you'll get used to gendered nouns!"*

Right: *"Every Spanish noun is masculine or feminine. The article changes with
it: el libro, la mesa. The gender is listed beside each noun."*

---

## 5. Checking your work

Four gates, cheapest first. A course is not finished until all four are green.

```
python3 tools/language_content_check.py <code> -v
```
The mechanical half of this document: shape, counts, the fixed `pos` list, tip
voice, phrase length, duplicate glosses, IPA that is really the word retyped.
It also prints WARNINGS, which are judgement calls rather than errors — a term
taught with two meanings, a gloss shared by two words. Read them; keep the ones
that are real and say which.

```
DISPLAY=:0 python3 tools/language_smoke.py
```
Opens every screen of the app against every installed course and fails on the
first one that raises. Seconds.

```
DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf \
  NB_SHOT_COURSE=<code> python3 tools/language_shots.py /tmp/shots 1024x740
```
Renders every screen and every exercise type to PNGs under the guest theme and
fonts, at the smallest panel the OS supports. This is the only way to see that a
script renders, that an IPA string has the glyphs it needs, and that nothing
overflows a 1024-wide laptop. **Look at the pictures.**

```
DISPLAY=:0 python3 tools/language_course_selftest.py <code>
```
Builds every exercise the course can produce and shape-checks all of them, then
sits every lesson to a perfect score through the real widgets and proves the
crown lands. Half an hour for all five courses. This is the one that catches a
question nobody can answer.
