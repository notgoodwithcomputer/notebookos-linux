# 005 — Serbian was three inconsistencies deep, and nothing could see any of them

**Lane:** C (catalogs, harness) · **Streams:** S2 evidence, S10 releasable
**Status:** CLOSED 2026-08-06

Task 002 fixed the Serbian catalog's *script*. Underneath that were three more
layers, each invisible to every existing gate for the same reason: the strings
all existed, all said the correct thing, and were all counted complete.
`i18n_check` reported seventeen catalogs at 100%. `i18n_coverage` reported every
string covered. Both were right every time.

## A. Two dialects  (233 Ijekavian vs 474 Ekavian)

Serbian's two varieties differ in the reflex of yat: `vrijeme`/`vreme`,
`mjesto`/`mesto`, `riječ`/`reč`. The catalog used both, so a reader met one on
one screen and the other on the next. Both are correct Serbian; an interface may
speak only one.

**Ekavian**, on two grounds: it was already the 2:1 majority, and it is the
standard of Serbia, which is also what the Latin script settled in task 002.

**A blanket `ije`→`e` rewrite would have corrupted the catalog.** Most `-je` in
Serbian is not yat: `nije`, `koje`, `svoje`, `dvoje`, `boje` and the copula `je`
would all be mangled. The conversion was driven from an explicit map of **95
whole word forms**, generated from stem rules but printed in full and read
before anything was written, with casing preserved (`MJESTA`→`MESTA`,
`Mjesta`→`Mesta`). It refused to write unless every placeholder and every
`singular|plural` split survived and no yat form remained. 212 values changed.

Two stems that look like yat and are not, excluded by name:
`započeti` (to begin) and `prijem` — *"Prijemno sanduče"* is the Inbox — both
identical in either dialect.

## B. Three words for "folder", one of which was the word for "map"

`mapa` (23 strings), `fascikla` (22), and the loanword `folder` inside
`Početni folder`, its name for Home. The sidebar row said `Mapa`.

This is not untidiness. **This OS ships a Maps app**, and Serbian for "map" is
also `mapa`, so the shipped catalog contained:

    Ova mapa nije mogla da se pročita     "this map could not be read"
    Ova mapa je prazna                    "this folder is empty"

Identical phrasing, two different things.

**Resolved to `fascikla`** — the standard term in Serbian localisation practice
(GNOME sr, Windows sr-Latn), consistent with the Serbia standard just settled in
A, and the only one of the two with no collision. Nineteen values converted;
`mapa` and `fascikla` are both feminine a-stems so the cases map one to one.
Which side to unify on is a judgement a Serbian speaker may reverse — the
collision argument is the part that is not a matter of taste.

**The English key decided the sense**, because the Serbian no longer could:
only keys saying "folder" and not "map" were touched, so `World Map`,
`No maps installed in %s` and `This map could not be read` were left alone.

Then `Home` itself: `Početni folder` used a third word for the very noun just
unified. French renders Home as `Dossier personnel` beside `Dossier`, Polish as
`Folder domowy` beside `Folder` — the compound reuses the language's own word.
Serbian now says `Početna fascikla`. That is a **gender change** (folder is
masculine, fascikla feminine), so the agreement moves with it:
`Početnom folderu`→`Početnoj fascikli`, `Početnog foldera`→`Početne fascikle`.
One stranded `vašeg Početne fascikle` survived the mechanical pass and was
caught by reading the output — a noun swap cannot see agreement.

Three strings also had the English word `Home` sitting inside a Serbian
sentence. A fourth, `(#Home)`, is a literal the task parser matches and is
English in all seventeen catalogs; correctly left.

## C. Yiddish called a folder a file

Found by the new gate while verifying B. Yiddish had three words: `פּאַפּקע`
(papke, folder — 33 uses, and the `Folder` label), `טעקע` (teke — 116 uses, and
the `File` label), and `פּאָרטפֿעל` (portfel, a briefcase — 2 uses).

Four strings used `טעקע` where the English says *folder*, so the OS told a
Yiddish reader to *"try the Documents **file**"* and that it *"cannot write in
the Videos **file**"*. Not a synonym — a different object. All six fixed,
including two article-agreement fixes: `פּאַפּקע` is feminine and `פּאָרטפֿעל` was
masculine, so `דעם` had to become `די` — **the same gender trap as B**, in a
different language, in the same hour. `Move to folder` was aligned with the
already-correct `Move to Folder…` rather than left as a mechanical noun swap.

## The gates

**`tools/anchored_term_check.py`** — supersedes and deletes
`folder_term_check.py` from task 002, which it strictly contains. Some English
keys ARE a term: `Folder`, `Documents`, `Trash`, `Printer` are bare labels on
the rows a user navigates by, so whatever a catalog says there is what that
language calls the thing. Every other string naming it must agree. Nine anchors,
850 mentions across 17 languages.

**Red-proof:** sr calling a folder `mapa` again and yi using its word for FILE —
both caught, `EXIT=1`.

**A correction worth recording.** Dropping the locative-frame requirement for
`Folder` looked obviously right and found four more real Yiddish errors — but
also flagged two *correct* translations: German renders "Up one folder" as
`Eine Ebene höher`, which names no folder and is what German systems say, and
Russian renders "your Home folder" with «Мои файлы», its own name for Home. A
translation may legitimately use an idiom or a place-name instead of the generic
noun. The frame requirement stayed, the tool is documented as a **regression
guard over framed mentions rather than a complete sweep**, and the six Yiddish
strings were found by reading all thirty-two `folder` keys by hand. A gate with
false positives stops being read, and then it catches nothing at all.

**`tools/catalog_dialect_check.py`** — the sibling of `catalog_script_check`,
one question further down: not which alphabet, but which variety. 33 yat stem
pairs; a catalog fails if both members appear. Only Serbian declares a pair set;
another language means adding its axis, not rewriting the tool.

**Red-proof:** `Vrijeme` and `Premještanje` replanted → both reported, `EXIT=1`.
`Prijemno sanduče` planted as a not-yat control → correctly silent.
