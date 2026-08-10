# Task 059 — Composer staff notation

## Shipped scope

Composer is now the notation home. `PianoRoll` was removed completely and replaced by a horizontally scrolling score with one generously spaced staff per track. Melodic tracks choose treble or bass from the true median pitch (treble at 60 or above); percussion uses a single-line staff and x heads. Treble, bass, and percussion clefs are Cairo silhouettes, not font characters.

The score draws time-signature-derived bars and quiet folio measure numbers, diatonic pitch positions, sharp accidentals for every black key, required ledger lines, and whole/half/quarter/eighth/sixteenth note glyphs. Single-dotted forms are supported. Eighths and sixteenths deliberately use individual flags; v1 does not beam notes. Stems point up below the middle line and down on or above it. Measure gaps are filled with whole/half blocks or simplified quarter/eighth rest paths.

Display quantization is presentation-only. Arbitrary tick durations select the nearest glyph without writing that value into the song. The tick model, PPQ 480 timing, private MIDI metadata, foreign MIDI import, preview synth, transport, store handling, and exact MIDI round trip remain unchanged.

The second control row now contains five compact Cairo note-glyph buttons plus dot and sharp toggles. Clicking a staff line or space inserts the palette duration at a snap-aligned time. Clicking a note selects it; Shift toggles selection; rubber-band selection remains. Horizontal drag moves by snap, vertical drag moves by diatonic steps while preserving a sharp, Delete removes, and Esc only deselects. All model-changing gestures use `UndoHistory`. Piano-roll edge resizing was intentionally removed: duration is chosen before insertion from the palette in notation v1.

Nine new functional strings are supplied in all 17 release languages under `059-composer-notation`; Serbian uses Gaj's Latin script.

## Suite changes

`tools/composer_selftest.py` now reports 39 named checks (previously 21), all passing.

- `add note applies snapped model values` became `staff palette add applies its exact duration`, matching the palette contract while retaining the add/undo proof.
- `Undo Resize Notes restores the complete song` was replaced by `staff vertical drag moves diatonically and preserves sharp` and `staff diatonic drag has a complete undo checkpoint`. Edge resize no longer exists by design; its destructive-edit/undo obligation is preserved by the actual replacement gesture.
- `Esc only leaves and never deletes` retains its name and now uses a staff test double instead of the removed roll member.
- New named checks cover both clefs, median selection, ledger cases, sharps, percussion, every base and dotted duration, nearest-glyph non-mutation, rests in partial and empty measures, five staff-line pixels, expected notehead pixels, measure-boundary bar pixels, staff click insertion, horizontal staff undo, and the notation-geometry mutant.
- The four MIDI checks are unchanged, including exact model round-trip and foreign running-status import. The three fresh open/close damage cycles, quarantined-sibling check, and destructive store mutant remain unchanged and pass.
- The new scratch-copy mutant uses `COMPOSER_MODULE_DIR` with the dependency directory inserted first and the mutant inserted in front; collapsing every pitch onto one staff line is rejected by name.

## Width proof

There is no X/display connection in this sandbox: GTK aborts while creating a style context, so an honest `get_preferred_width()` window construction is display-owed. I used the specified fallback: the committed Russian catalog was merged in memory with task 053 and task 059 Russian fragments, then measured using headless Cairo/Pango layouts at Sans 11 with conservative GTK chrome and the source's fixed/capped control widths.

The initial one-row design measured **1124 px** and was rejected. Moving the symbol-only duration palette to the existing second control row produced a widest-row floor of **886 px**, leaving **138 px** against 1024. The calculation includes the capped 12-character track combo, translated Russian Mute/Tempo/Snap/Play strings, snap popup button width, spin control, control padding, all row gaps, and root borders. Palette tooltips do not participate in minimum width.

## Verification

- `python3 tools/composer_selftest.py` — **39 checks, 0 failed**
- Python byte-compilation — passed
- All 17 task fragment files parse, contain the same nine non-empty keys, and Serbian contains no Cyrillic — passed
- `git diff --check` — passed
- Headless Cairo `ImageSurface` proofs — five lines, expected quarter-note position, and time-signature bar boundaries passed

## Display-owed rerun

- Construct Composer with the scratch symlink directory and merged task-059 Russian catalog first on `sys.path`; record the real child `get_preferred_width()` and confirm it is at most 1024.
- Visually inspect treble, bass, percussion, accidentals, ledgers, rests, un-beamed flags, selection color, stacked-track spacing, and horizontal scrolling in the Papertone GTK theme.
- Exercise click/Shift/rubber-band selection, diatonic and snap drags, palette insertion, Esc/Delete, undo/redo, and the transport playhead in a mapped window.

## Dispatcher verification (batch-0810, 2026-08-10)
Display rerun: composer_selftest 39/39 (incl. the collapsed-geometry mutant);
construct_all 40/0. Personal red-proof (staff_step +1 in a scratch copy via
COMPOSER_MODULE_DIR): 3 named FAILs — both clef mappings and staff-click
insertion. ru width with 053+059 fragments INJECTED: 741px (283 spare).
Guest-theme render reviewed: clef silhouettes, stems/flags/dots, rests,
percussion x-heads, measure numbers, glyph-button palette. Fragments 17×9,
sr Latin. VERIFIED per M2.
