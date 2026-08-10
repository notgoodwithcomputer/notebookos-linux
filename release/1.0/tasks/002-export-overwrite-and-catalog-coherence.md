# 002 — Silent export overwrite, and two kinds of catalog drift

**Lane:** A (apps) + C (catalogs, harness) · **Streams:** S1, S2, S10
**Status:** CLOSED 2026-08-06

## A. Export destroyed the previous export, silently  (ROADMAP #5)

`novel`, `journal`, `cookbook` and `academics` each write their PDF to a
DETERMINISTIC path under Documents — `timetable.pdf` and `homework.pdf` are
fixed, journal's is `journal-<today>.pdf`, cookbook's is a slug of the recipe
title, novel's is the book title offered back in the name field. Exporting twice
is not an edge case; it is how the apps are used: export, spot a mistake, fix it,
export again. All four overwrote the earlier file with no dialog, no status line
and no trace. There is no network and no cloud — the file under `$NB_HOME` was
the only copy. The Video Editor had had the guard since its own fix; these four
never got it.

**Fix.** All four now ask before replacing, and — the part worth more than the
guard itself — they ask with the strings that ALREADY EXISTED for this exact
moment: `Replace file?` / `“%s” already exists in Documents. Replace it?` /
`Replace`, which Novel's Save As has used all along and which all seventeen
catalogs already carry. The first draft of this fix invented `Replace PDF?` and
a new sentence, which would have added two keys to seventeen catalogs and given
the OS a third wording for one question. Checking the catalog first turned an
i18n debt into zero new keys.

**Gate:** `tools/export_overwrite_selftest.py` — an execution test. It builds the
real app, plants a decoy at the real destination, calls the real export, and
reads the bytes back. Three assertions per app, in both directions, because a
guard that can only say no is a dead end rather than a fix:

    DECLINE  the confirm is raised, and the decoy survives byte-for-byte
    ACCEPT   the confirm's action runs, and a real PDF replaces the decoy
    FRESH    exporting to an unoccupied name asks nothing and still writes

Asserting the confirm was *raised* is what keeps it honest: a test that merely
patched the dialog away and checked the file would pass against an app that
never asks — it would be measuring its own mock.

**Red-proof (2026-08-06).** The guard stripped back out of all four on a scratch
copy, via `--de`:

    FAIL journal:   exporting onto an existing file asks first (0 asked)
    FAIL journal:   declining leaves the earlier file byte-for-byte intact
    FAIL cookbook:  ... (same two)
    FAIL academics: ... (same two)
    FAIL novel:     ... (same two)
    8 checks, 0 passed, 8 FAILED

The second line of each pair is the one that matters: the decoy was really gone.
Clean tree: `28 checks, 28 passed, 0 FAILED`.

## B. Serbian shipped in two alphabets

`lang_sr.json` held 2941 values in Latin and **143 in Cyrillic** — the entire
Bill Tracker and Widget Board block, translated in a later session that chose
the other script. Serbian is digraphic, so both are correct Serbian; but a user
met `Račun` in the Finder and `Рачун` in Bill Tracker with no way to know they
were the same word.

`i18n_check` reported all seventeen catalogs complete at 100%. `i18n_coverage`
reported every string covered. Both were right — the strings existed and said
the correct thing. They were written in two alphabets.

**Fix.** All 143 transliterated to Latin (Gaj's), the house script by 2941 to
143. The transliteration is 1:1; the only care needed was the digraphs Љ Њ Џ,
which are `Lj Nj Dž` before lowercase and `LJ NJ DŽ` inside an all-caps run —
`ПОНАВЉАЊЕ` → `PONAVLJANJE`. The script refused to write unless every value came
out free of Cyrillic and every `%s`/`%d` survived intact, and a byte-identical
round-trip of the untouched file was checked first so the diff is 143 lines and
no reformatting.

**Gate:** `tools/catalog_script_check.py`. Majority script per catalog; any value
in a *second major script* is a finding. Latin inside a non-Latin catalog is
never a finding (every language carries PDF, GBA, kB, %s — ~700 values each).
A lone letter is a symbol, not prose: runs of 2+ only, which is why the
Calculator's `π` — Greek, in all seventeen catalogs — is correctly ignored
without needing a list of exempt symbols that could fall out of date.

**Red-proof (2026-08-06).** Three of the original Cyrillic strings replanted in
`sr`, plus a lone `π` planted in `de` that must NOT be reported:

    de   latin        3084 values, 0 in another alphabet     <- the π, correctly ignored
    sr   latin        3084 values, 3 in another alphabet
          cyrillic  Bill Tracker   Евиденција рачуна
          cyrillic  Payee          Прималац
          cyrillic  REPEATS        ПОНАВЉАЊЕ
    RESULT: 3 value(s) in the wrong alphabet   EXIT=1

## C. One folder, two names

Found while adding A. Chinese labelled the Documents folder 文档 in the Finder
sidebar and called it 文稿 in five export and save messages — so the OS told a
Chinese reader to look in a folder that was not on their screen. Japanese did the
same with 書類 / ドキュメント, and Videos drifted in four more languages: Hindi
left the English word `Videos` untranslated inside two Devanagari sentences,
Korean said 비디오 for a sidebar reading 동영상, Serbian `Video` for
`Videozapisi`, Yiddish וידעאָ for ווידעאָס.

**Fix.** 14 values across ja/zh/hi/ko/sr/yi, plus one more in zh that the gate
does not flag but is the same drift, plus a lone `「文档」` where the other 134
quoted values in that catalog use `“”`. Written out key by key rather than as a
search-and-replace, because **zh uses 文稿 for both the Documents folder and the
noun "document"** — `这份文稿无法保存…` is correct and must not be touched.

**Gate:** `tools/folder_term_check.py`. The bare key (`Documents`) is canonical,
because that is the Finder Places label the user navigates by; every other
string that names the folder must use it. Inflecting languages match on a
per-word stem, so German `Dokumenten` and Polish `Dokumentów` still match. Two
deliberate exclusions, both recorded in the file: `Desktop`, because the one
English word names both the folder and the surface and a translator is right to
render `Desktop Widgets` differently; and the frame `the %s`, which matched "an
effect layers over the music".

**Red-proof (2026-08-06).** zh's 文稿 reinstated and a plausible French
abbreviation planted:

    fr  Documents: canonical (sidebar) Documents | value: Exporté vers Docs
    zh  Documents: canonical (sidebar) 文档      | value: 已导出到“文稿”
    238 translated mentions checked across 17 languages, 2 finding(s)   EXIT=1

Clean tree: `238 checked, 0 finding(s)`.

## Left open, deliberately
* **sr renders "folder" as both `fascikla` (20) and `mapa` (17)**, while the
  canonical keys `Folder`/`New Folder` say `Mapa`. Same class as C but about a
  common noun rather than a proper folder name, so it needs the OS-wide
  `term_consistency_check` from S2 rather than another bespoke gate.
* **sr mixes Ekavian and Ijekavian** — `Zamijeni` beside `Zameniti`,
  `nije uspjela` beside `mesta`. Same shape of problem, needs a dialect pass.
* **23 strings are still uncovered by the catalogs** (was 31; the 8 this task
  would have added were avoided by reusing existing keys). All are accessibility
  names added by recent a11y work.
