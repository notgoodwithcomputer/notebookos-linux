# 052 — Novel chapter title field and empty-chapter alignment

Task: `052`, lane `batch-0810`  
Scope: `de/novel.py`, additive `tools/novel_title_selftest.py`, and new-string fragments only.  
Builder only: no commit was made.

## Outcome

- Chapter titles are now editable in a dedicated native entry above the prose buffer. The field occupies the former opening-heading position and keeps the 32pt medium serif letterpress treatment and rule. It binds directly to `ch["title"]`; body edits cannot rename a chapter.
- Title changes use the existing debounced `UndoHistory.touch()` typing path, update the live sidebar row, persist through the common serializer, and remain coherent with Contents, pagination, page maps, PDF export, zine export, chapter deletion, and default-title renumbering.
- User chapter titles are set with `nbi18n.set_verbatim`; they are content and are never translated. The sole new functional UI string is `Chapter title`, passed through `_t()` and supplied in all 17 requested fragments. Serbian is Gaj's Latin (`Naslov poglavlja`).
- The prose buffer starts empty and contains prose only. Body paragraph Heading/Quote/B/I/U formatting remains available; only the automatic special first-line heading was removed.
- The `Empty chapter` ghost now derives its x/y origin from the TextView's actual left/top margins and the body/ghost Pango ascents. `placeholder_offsets()` is pure and headless-testable; the widget adapter reads the resolved CSS font descriptions.

## Migration and recovery rules

The saved document now carries `"format_version": 2`.

For a store with no format marker (legacy format), each chapter is handled independently:

1. Read the stored title verbatim.
2. Compare it with the complete first body line, without stripping or normalization.
3. If they are exactly equal, remove that line and its following newline (when present) from the body. Shift every surviving formatting range left by the exact removed character count, clip a span crossing the boundary, and discard only spans wholly inside the removed heading.
4. If they differ—including an empty first line—preserve the entire body and the stored title independently.
5. Emit format 2 on the next save, so the migration cannot run twice.

A format-2 manuscript is never migrated, even if its first body line happens to equal its title. Unicode is compared and sliced as Python text, preserving the content and range offsets in character units.

An explicit unknown format version is unrecognized rather than guessed. For session recovery, malformed bytes go through `nbapp.preserve_damaged`; recognized JSON with an unrecognized shape/version goes through `nbapp.quarantine_unrecognized`. In both cases the original is recovered under the shared `.damaged-*` convention and the session is marked read-only so the blank fallback cannot overwrite it. File Open continues to reject an unrecognized chosen document without adopting its path or mutating the live manuscript.

## Behavior decisions

- Word counts now count every word in the prose buffer and exclude the dedicated title. This is the intended behavior change: the former first-line exclusion is gone because line one is now real prose.
- Manuscript-wide Find continues to search prose only, not chapter titles. Titles already have a continuously visible sidebar index and their own field; find hits retain precise selectable TextBuffer offsets and do not pretend an entry-field match is a body selection.

## M1 red → green

The additive suite was written and run before the implementation. Its initial named red result was `NOVEL TITLE SELFTEST: 14 FAILED`:

- `legacy mirrored heading migrates out of body`
- `migration shifts body formatting without losing it`
- `legacy heading-only formatting is removed with heading`
- `unicode mirrored headings migrate losslessly`
- `parsed state carries the dedicated-title format marker`
- `word counting treats every body line as prose`
- both named placeholder metric checks
- dedicated editable field, verbatim protection, save marker, and body/title decoupling checks
- both named damaged-session recovery checks

The adversarial controls that were already correct—different title/first line, empty first line, and an already-migrated manuscript—were green in the red run. After implementation the suite reports `NOVEL TITLE SELFTEST: all pass` (19 named checks). Its `NOVEL_MODULE_DIR` override runs a scratch-copy PASS-MUTANT which replaces the save marker and proves the suite fails by the name `every save writes the migration marker`; no tree file is mutated.

## Green checks

- `python3 -m py_compile de/novel.py tools/novel_title_selftest.py`
- `python3 tools/novel_title_selftest.py` — all 19 pass, including headless Pango baseline equality within 1px and PASS-MUTANT.
- `novel_close_recovery_selftest.py` — 14/14.
- `novel_hierarchy_accessibility_selftest.py` — all pass.
- `novel_lifecycle_selftest.py` — all pass.
- `novel_prompt_focus_selftest.py` — 9/9.
- `voice_check.py --file novel.py --fail` — clean.
- `jargon_sweep.py` — clean (allowlisted technical findings only).
- `self_attr_audit.py` — 135 classes, zero findings.
- All 17 fragment files pass `python3 -m json.tool`.
- `git diff --check` — clean.

## Display-blocked checks for dispatcher

No X/display probing or substitute renderer was used. These require the dispatcher's real display and were not claimed green here:

- `tools/novel_zine_selftest.py` (constructs `Novel()` and stops at GTK initialization).
- Novel cases in `tools/writing_apps_selftest.py`, `tools/undo_selftest.py`, `tools/reopen_shapes_selftest.py`, and `tools/text_stress_selftest.py` (window construction/render paths).
- Real-render verification that the ghost glyph and the first typed glyph coincide within 1px; the shared margin/Pango ascent math is green headlessly.
- Visual confirmation that the title field/rule matches the previous chapter opener and that the full layout remains within 1024×722.

`empty_state_selftest.py` independently passed its three headless checks and honestly skipped its real-display control drive.

## Not-a-defect ledger

- The body still supports a user-applied `Heading` paragraph tag. This is prose formatting, not a return of the implicit title line.
- Empty dedicated titles are allowed as user content; print/Contents retain the established `Chapter N` display fallback so exported navigation never becomes blank.
- Find excluding titles is deliberate and documented above, not a missed consumer.
- The title is excluded from word counts by design; moving it out of the buffer must not increase manuscript prose totals.
- `CH_OPEN_TOP` and print layout did not need new geometry: those consumers already read `ch["title"]` and now receive that value from the dedicated field.

## Dispatcher verification (batch-0810, 2026-08-10)
Display rerun: all 6 novel suites PASS (title, close-recovery, hierarchy,
lifecycle, prompt-focus, zine). Personal red-proof (migration guard `if first
!= title` disabled in a scratch copy via NOVEL_MODULE_DIR): exactly the two
data-preservation checks fail by name. Guest-theme render verified: title
field in 32pt serif over the letterpress rule, live sidebar binding, ghost
prompt aligned with the body origin. Fragments ×17 validated, sr in Gaj's
Latin. VERIFIED per M2.
