# Apple-quality release punch list (lane: apple-quality, opened 2026-08-13)

Mandate: every app at consumer/Apple bar; 6-10 days to release. User found ~10
brief-glance bugs in 2.0 (Comics/Animation called "nigh-unusable", GBA SDK
suspect, unenumerated). Method: real-use driving of the booted ISO + host
harness + Codex static audits. This file is the living ledger; strike items
with the commit that closes them.

## FIXED (committed)
- Comics: bubble editor input invisible + never focused; Ctrl+Enter applies
  (bd8fcddf). Verified on real X pipeline.
- Comics: colour palette 210px below the dock fold at 722 (bd8fcddf).
- Comics: fit_zoom ladder-snap rendered page up to 25% small (bd8fcddf).
- Comics: pixel-grid full-page path build per repaint at 8x+ (bd8fcddf).
- Comics: Esc/Ctrl+W/Q dead — _on_key never fell through to nbapp's ladder;
  menu printed "Close Esc" beside a dead key (7acd2314). Guest-confirmed
  pre-fix, real-XTest verified post-fix.
- Animation: close-guard Discard rendered as stray full-width button under
  the row (3b41f9a5).
- Animation: closing an unbound recovery-backed film interrogated the user
  while the chip said "Saved" — unbound close now silent (Comics model);
  bound/save-error films still guarded; New/Open guards kept (3b41f9a5).
- Build: __pycache__ shipped beside the .app stubs and listed as the FIRST
  row of Applications in Finder (post-build.sh sweep line; rides the
  integration sweep as a shared-file edit).
- Gate: shell_finder_ux_selftest red at HEAD against correct code (brittle
  exact-string pin; 3cdc1400).
- Gate: toyfont ledger ratchet + reasoned keeps; nbprint's own fixture call
  removed (16e812b5).
- Comics: _on_key had no _prompt_layer guard — every lowercase tool-shortcut
  letter and bare Delete fired even while the bubble editor's TextView was
  focused, switching tools mid-sentence or destroying the bubble being
  lettered. Guarded the whole bare-key block on `_prompt_layer is None`,
  illustrator's own idiom (5683fb77). Red-proved in isolation (monkeypatched
  pre-fix _on_key: tool-letter swallowed + changed to eraser, Delete dropped
  the bubble 1->0) before landing against the real fix.
- Comics: this round's Codex audit-and-fix pass over the previously-
  unaudited flows closes that whole line below — see FIXED entry and
  103-check suite in 5683fb77: _place_scale upscale bug (a 20x20 placed
  image blew up 82x against a 1650x2550 page — now caps at 1.0), low-zoom
  selection handles resolved by nearest on-screen distance instead of
  first-match (dead-center clicks on a min-size bubble at 1/8 zoom picked
  the wrong handle), export/print write through a temp file + atomic
  replace (a failed run could previously corrupt an existing good PDF),
  _impose leak-proofed (try/finally surface.finish() + per-slot clip),
  _place_image given a properly rect-sized undo frame + hidden-layer
  flash. Plus my own fix alongside it: _export/_print now snapshot via
  Layer.encode() (PNG bytes) instead of autosave_snapshot()'s raw-surface
  copy — that shape is fine for autosave's usual 1-2-dirty-page case but
  unbounded for a caller visiting every page; opening an old format-1
  document marks every page dirty at once, and autosave-on-open was
  copying every one of those surfaces before a single export byte reached
  disk (~485MB on top of the migration cost for a real 32-page legacy
  book).
- Gate: button_contrast_check sampled a freshly constructed window's style
  properties before Papertone's 90ms button background transition had
  settled, so Comics' default-selected Pencil tool button was caught
  mid-flight (1.32:1 INVISIBLE in the full sweep, 5.12:1 once actually
  rendered — no real user ever sees the pre-cascade frame). Now blocks on
  a real 300ms timeout before sampling; also registered comics in the
  default APPS list (121866ea). Verified stable (0 findings) across 4
  full OS-wide runs.

## GBA SDK trio — ALL 16 AUDIT ITEMS CLOSED (d151cea0, 77479de1, b1bd8988,
## 7b95eea9): async build (#9), open-confirm (#1), scalar quarantine (#3),
## real discard keys (#5), EEPROM DMA fixed-addressing + selectable types
## (#6/#11), no-health every-instance (#8), help identifiers (#12), gbaemu
## quarantine (#13) + bounded post-paint scan (#10), honest aside… (#16 spec
## side), Build & Play home in Build menu + play-anyway card (#7), affine
## survives loading (#4), spec shipping-note (#14/#15). Suite 574/574.
## STILL OPEN:
- FIXED 6d837782 (pending on-target re-test): nbgame re-asserts game
  focus every 2s for the whole session + reclaims focus when a stray key
  hits the stage — the D-pad death + late-window black-stage class.
- Codex fix-round job task-msrvz3qu-v6gmg3 is a ZOMBIE (silent 1.5h+);
  USER: /codex:cancel it. All its items were completed in-session.

## OPEN — apps
- Animation: guest playhead/clock chip wording pass — "Saved HH:MM" chip
  meaning (recovery) vs document staleness; consider naming the store.
  Low-priority now the unbound close is silent.
- Comics: Zine Print, Export PDF, Place Image, low-zoom selection handles
  and CJK lettering all audited this round (Codex static pass + host-side
  real-handler suite, see FIXED above, 5683fb77) — the bugs found there are
  fixed and covered by 103 real-app checks. STILL OWED: an actual on-target
  drive-through (booted guest, real speed/resolution) of Export/Zine Print
  at genuine multi-page scale — nothing this round exercised the guest.
- FIXED 52672195: breadcrumb folds whole pills behind a leading "…"
  (navigates to the deepest hidden ancestor, tooltip carries the path);
  the mid-letter "e" sliver class is dead. Verified by synchronous draw
  + six finder suites.
- FIXED b8f1a012: board tiles pack flush against the pinned column (a
  content tile's hexpand bubbled into the grid; pinned hexpand=False;
  board_selftest 101/101, its first full pass) + Journal day-zero shows
  the gentle empty state instead of the red cross.
- Comics: export/zine run synchronously on the UI thread (~10s guest);
  give them the animation-style worker card. Lower priority than it was.
- sequencer/settings/writer toy-font rows: writer with fonts session;
  sequencer/settings are reasoned keeps (digits / fixed-English test page).
- packages[el]: tightest minsize in OS (13px/35px slack) — preventive.

## FIXED IN THE AGGREGATE TRIAGE (b9e3c9a6)
- Finder read only the bare-list removed-apps store: one uninstall un-hid
  every removed app OS-wide. REAL consumer bug a byte-pinned suite nearly
  laundered; suite now proves the store through Finder's own reader.
- Novel froze on Ctrl+Y after undoing a chapter delete (place_cursor never
  returned mid-restore; caret now lands one idle later, both sites).
- 52 Serbian Disc-Burner strings arrived in Cyrillic vs the sr-Latin law —
  transliterated. ja called Music 音楽 in one string — ミュージック now.
- ellipsis_sweep 0 cuts OS-wide (comics dock geometry + 11px tool names;
  composer hints shortened x3).
- board_settle rewritten to pin the REMOVED settle (red-proved);
  document_safety/performance_ux stop pinning single spellings; packages
  suites drive the real prefs writer (getattr-hardened app-side).

## SHORTCUT-LADDER CLASS (Codex audits R2+R3, Aug 13 late — full reports:
## .codex-scratch/shortcut-ladder-audit.md + R3 in session log)
Class: window-level key handlers firing while an editable text widget has
focus. 57/75 + 54/54 modules covered. FIXED this session (each red-proved):
- Calculator: whole keypad ladder (digits/letters/operators/=/BackSpace/
  Delete/Return/Up/Down) stole every key from the Y1-Y4 graph fields —
  formulas were untypeable. Guard after the Escape branch.
- Sequencer: Space toggled playback while typing a track name (the space
  in "Chorus 2"); Space hoisted below the typing guard, guard widened to
  (Editable|TextView).
- Composer (R3, DESTRUCTIVE): bare Delete deleted the SELECTED NOTES and
  arrows moved them while the Tempo SpinButton (an Editable) had focus;
  Space played the piece mid-edit. Bare-keys guard added; Ctrl chords and
  Escape keep window meaning.
- Finder + Music: Entry-only guards widened to (Editable|TextView),
  defensive (no current TextView in either tree).
- Comics: peer's lane; their _prompt_layer gate confirmed landed by R3.
REPEAT-BUILD WEDGE (guest-arm peer finding, root-caused jointly): gcc
exceeding build_rom's 120s cap left subprocess.run blocked forever
draining pipes held by orphaned cc1/as/ld → work() never returned → job
never retired → "Compiling…" forever, no card; later attempts got a
silent REJECT-None. FIXED: _run_capped (own session + killpg on timeout,
both compiler steps), REJECT-None now clears _building + says so, and
the whole path is phase-logged to ~/.config/notebook/gbasdk-build.log
(gbasdk requested/accepted/rejected/worker/landed + build_rom find_gcc/
generate/write/gcc/objcopy phases). Suite: gbasdk 581/581 incl. a
watchdog check that a timed-out step dies as a group (sabotage-proved).
ON-TARGET RE-DRIVE OWED: repeat Build & Play x3, wedge log inspection.

## OPEN — OS-wide sweeps not yet run at the new bar
- Remaining ~38 apps: real-use drive-through each (task #6). Order by
  consumer surface: media, music, video, writer*, illustrator*, maps,
  calendar, tasks, cookbook, journal, ... (*peer-held files: report, don't
  edit).
- i18n_coverage_check: 141 uncovered (61 new) + 35 stale baseline rows.
- TREE LANDED: all stranded lanes committed suite-green (5a000f10,
  d369456e, 15d4b7ee, d151cea0, 7b95eea9, 32c0030a, ebc50cc3 + catalogs
  in 77479de1). Remaining dirty = the fonts/tablet session's claim
  (burner/writer/ebook/illustrator/nbicons_data/fonts/.config/kbuild/
  xorg/desktop.config/.gitignore/their suites) — they land their own.

## ON-TARGET MATRIX, 2.2-consumer (Aug 13 ~22:30-22:45)
PASSED on the booted image: bell left of clock; __pycache__ gone;
Govorimo gone (34 items); Comics fit 26% + colour above fold + BUBBLE
EDITOR visible/focused/typing + full Esc ladder (palette→card→app);
GBA Build & Play first in the Build menu + UI ALIVE mid-compile (menu
opened over "Compiling…"); nbgame re-embed caught vbam's window swap
(log: "found via pid").
RESOLVED ACROSS SESSIONS (80219c80 + e1c4c6cc): Finder-over-SDK fixed
(flag unlink + monitor reconcile); repeat-build wedge root-caused JOINTLY
(REJECT-None ignored + gcc timeout leaving orphaned grandchildren holding
pipes — subprocess.run blocked forever in drain) and fixed via
Popen(start_new_session)+killpg group-kill, sabotage-proved; nbgame logs
its exit route; the Ctrl+Esc grab now requires real Ctrl; nbdiacritics
replays palette-open letters to the text widget; run-1's "early exit" was
the harness's own delayed Esc (X starved by gcc) landing on vbam.
STILL OWED ON-TARGET after the next respin: repeat Build & Play x3 +
build-log read; D-pad click-into-game discriminator (arrows PROVEN
delivered in X via the Finder-selection oracle); Finder-over-SDK re-check.
OLD notes (superseded above):
- FIXED (pending on-target re-drive): FINDER RE-PRESENTED ITSELF OVER
  THE COMPILING SDK — root cause was TWO-SIDED: gbasdk._emulator_exited
  unlinked the shared app-active flag unconditionally while the SDK was
  still alive (now recounts via nbapp.refresh_app_flag), AND finder's
  _sync_app_flag reappeared on bare flag-absence with no reconciliation
  (now heals a wrongly-dropped flag while any de app is /proc-alive,
  and only returns — with present + nudges — when none is). Suites:
  finder_launch +6 checks, gbasdk +2, both red-proved against HEAD.
- Run 1: game exited cleanly ~seconds in, cause STILL UNPINNED (guest
  serial shell dead this session, held by a peer — vbam.log unread).
  Instrumented instead: nbgame.stop() now logs WHICH route fired
  ("Ctrl+Esc grab" / "stage Esc before embed" / "exit button" /
  "external"), and the grab handler now requires the Ctrl state on the
  delivered event, not just keycode 9 — a bare-Esc event can no longer
  end a running game. Next boot's log names the culprit or proves vbam
  died on its own.
- Run 2 (Ctrl+R): still "Compiling…" at 3.5 min under full-system TCG
  load when the session closed — verify completion + D-pad next boot.
- FIXED (Codex, M2-verified 64/64 + 3-check red-proof): nbdiacritics
  palette-open letters now REPLAY into the tracked text widget and the
  raw event is consumed — they can no longer fall through to an app's
  shortcut ladder. NOTE: the observed "Eraser while lettering" symptom
  was two bugs; the comics half (unguarded _on_key tool map eats
  lowercase tool letters even with the palette closed) is HANDED OFF to
  the comics claim-holder (HANDOFF 2026-08-13, illustrator's
  _saveprompt_layer guard is the idiom).

## DECISIONS FOR THE USER
- D-close: Animation now matches Comics for unbound films (silent close,
  recovery restores). Bound films still ask. OK?
- D-tree: may I verify-and-commit the dead lanes' uncommitted app diffs
  wholesale after their suites pass, or do you want per-lane review?
- D-x264: BR2_PACKAGE_X264=y is in .config; next ISO needs
  `make -C buildroot ffmpeg-dirclean` first (HANDOFF note) — include in
  respin?
