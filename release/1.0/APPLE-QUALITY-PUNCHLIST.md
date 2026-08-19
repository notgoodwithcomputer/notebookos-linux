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
- Animation: the save chip claimed "Saved HH:MM" over a bound film the disk
  had not seen — autosave writes the RECOVERY store, and the chip conflated
  it with the film, so the status bar and the close guard contradicted each
  other in one window. The store also never carried _doc_dirty, so a restart
  came back under the film's own name with a clean chip over a stale file.
  writer.py's words and shape ("Not saved to file", already x17). Closes the
  OPEN chip-wording item (2f0e7d5d, F59).
- Animation: opening a big film decoded every take to find damage — 2602ms
  at the 768-drawing cap, of which the scan was 2594ms. A PNG says whether
  it is whole from its own CRCs, 115x cheaper; the quick scan may only say
  YES, and anything it doubts still goes to the real decoder, which stays
  the only thing allowed to call a drawing damaged (2c2cc302, F52).
- Animation: the library built a 44x33 picture for EVERY row on open, when
  a dozen fit on screen — 1.12 of the 1.24s to open a 400-drawing film, and
  resident memory climbed with the library (128MB at the cap, on a machine
  built for two gigabytes). Rows paint when asked: 1962ms -> 157ms, memory
  flat, pixel-identical (0fa821a6, F53).
- Animation: tip, pattern, mirror and the project palette had NO menu entry
  and sat below the dock's fold (887px of controls in a 406px column at the
  design size). A Paint menu whose items press the dock's own buttons, so
  the two cannot disagree. The two mirror controls were also the only dock
  controls built without a label (c323ae3a, F54).
- Animation: 'Solid' is the GBA SDK's key for a tile you cannot walk
  through, so the fill pattern read as 通行不可 "impassable" in Japanese and
  통과 불가 in Korean, and as the physics sense in de/ru/tr/nl. No gate can
  see this: 17x100%, present, translated, wrong. This app now has its own
  key (92d5bda9).
- Animation: the drawings library rendered as ONE WHITE COLUMN — blank
  paper is white, rows sat flush, nothing framed a thumbnail, in a list
  whose purpose is navigating by picture (651cbe3a, F53).
- Animation: wobble 306ms/take (1263ms at 640x480, x4 takes on the GTK
  thread), recolour 621ms per take over every take, and the encoder's
  per-frame RGB conversion 44.6ms — ~96s of a 2160-frame export. All three
  byte-identical after (5e86c838, 46f85434, 8179a7b8, F55-F57).
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
- Comics: Zine Print, Export PDF, Place Image, low-zoom selection handles
  and CJK lettering all audited this round (Codex static pass + host-side
  real-handler suite, see FIXED above, 5683fb77) — the bugs found there are
  fixed and covered by 103 real-app checks. STILL OWED: an actual on-target
  drive-through of Export/Zine Print itself at genuine multi-page scale —
  see the FIXED entry below for what WAS on-target verified this round
  (the _on_key guard, not Export/Zine Print's own render path).
- FIXED, on-target confirmed (not just host-side): the _on_key prompt-guard
  fix (5683fb77). Rebuilt the rootfs from a clean tree (buildroot/output;
  verified the fix landed via grep against output/target before booting),
  booted a private isolated guest (NB_WORK=/tmp/nb-comics-verify, NB_GL=0 —
  see [[qemu-guest-harness-isolation]] for two harness traps this hit),
  and drove it for real: opened the bubble editor, typed "eraser test"
  starting with the exact letter that switches to the Eraser tool — it
  landed as literal text, tool stayed on Bubble, dialog stayed open;
  pressed Delete — bubble and dialog both survived; Apply committed a real
  "ERASER TEST" speech bubble on the page. Screenshots taken at every step.
  A first attempt read as still-broken (typed text DID switch tools) but
  turned out to be a test-methodology bug, not a code bug — a second,
  premature click landed on the dialog's scrim once a QUEUED first click
  finally opened it, closing the very dialog under test; a clean redo with
  no overlapping input confirmed the fix is correct.
- FIXED 52672195: breadcrumb folds whole pills behind a leading "…"
  (navigates to the deepest hidden ancestor, tooltip carries the path);
  the mid-letter "e" sliver class is dead. Verified by synchronous draw
  + six finder suites.
- FIXED b8f1a012: board tiles pack flush against the pinned column (a
  content tile's hexpand bubbled into the grid; pinned hexpand=False;
  board_selftest 101/101, its first full pass) + Journal day-zero shows
  the gentle empty state instead of the red cross.
- FIXED: Comics Export/Zine Print now show a persistent progress overlay
  (real per-page meter via the existing draw() callback + GLib.idle_add,
  matching animation.py's shape) instead of closing to a bare toast.
  Export is honestly cancellable (nbjobs' existing checkpoint/cancel
  machinery, already used elsewhere — not new plumbing); Zine Print's
  overlay is deliberately non-dismissible/no-Cancel during render since
  nbprint.print_booklet owns its own cancellable dialog already and a
  second, different-acting Cancel would be a lie. Dispatched to Codex
  (task-mssfdknj-pftytu); the process died silently ~9 min in with no
  crash trace (codex-companion's own status tracking kept reporting
  "running" for 24+ more minutes after the PID was confirmed gone —
  ANOTHER Codex zombie, same pattern as task-msrvz3qu-v6gmg3 earlier this
  campaign). The landed diff was sound: py_compile clean, both new
  self-verifying checks logically correct, only broken by one missed
  local import (comics_selftest.py's gtk_family() used GLib in a helper
  without importing it) — the process died before it ever got to run its
  own verification loop. Fixed the import, deleted 5 genuinely-dead
  __init__ fields left from an abandoned first approach
  (_render_generation/_progress_bar/_progress_words/_progress_cancel/
  _progress_cancelling — grepped zero other references). Full gate
  battery green (105/105 selftest, ascii_css, menu_conformance,
  button_contrast, construct_all, icon_uniqueness, minsize). ON-TARGET
  CONFIRMED: rebuilt rootfs, isolated guest, drove the real File > Export
  to PDF menu item on an 8-page book — screenshotted "Rendering pages… /
  Working - 12%" with a real filling meter and Cancel button mid-render,
  then the overlay closing and "Exported HH:MM" landing in the status
  bar on completion. Zine Print's own overlay could NOT be reached the
  same way on this harness: nbprint.print_booklet's pre-flight printer
  check fires first and this guest has no printer configured ("No
  printer found... File > Export to PDF saves this document as a file
  instead" — correct, expected behavior, not a bug). That path's
  coverage stays at the host-side fake-harness level (105/105), matching
  how this codebase already tests real printing via its own separate
  chroot-cupsd harness rather than the QEMU click-through path.
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

## MEDIA (Codex static audit + my verification, Aug 14)
FIXED 3e00c26f: trashing a photo wrote NO origin sidecar, so the Finder's
Put Back had no folder to return it to — bytes preserved, address lost,
and the old check ("bytes reached .Trash") passed the whole time. Now
writes <trash>/.origins/<name-in-trash> under the name the file actually
took; proved end-to-end by pointing Finder's own _origins_dir() reader at
a file Media trashed. Also: "Move to Trash…" dropped its ellipsis and the
docstring stopped claiming it confirms (the confirm was retired OS-wide;
_confirm is dead code nothing calls), and _on_destroy now cancels the
fullscreen auto-hide timer — the one timer teardown missed.
STILL OPEN (verified findings, not yet fixed):
- NO in-app one-step Undo for the trash. This is the class the campaign
  caught in tasks ("Remove List had no undo"). Media declares only
  ("File","View") menus, so adding Edit ▸ Undo is a MENU-CONTRACT
  decision (calendar sets the precedent: an Edit menu holding only
  undo items). nbapp.UndoHistory is snapshot-over-a-document and fits a
  filesystem move poorly — a purpose-built one-step undo is the shape.
- Decode cap disabled when dimensions are UNKNOWN: get_file_info failure
  becomes 0x0 and falls through to an unscaled new_from_file. Same class
  for SVG (rsvg-convert unbounded when probing fails) and ffmpeg (the
  scale filter runs AFTER the source frame is decoded). MAX_PIX is a
  max SIDE, not a pixel-count budget: 8000x8000 RGBA is ~256MB.
- A file deleted underneath the viewer leaves a stale surface: the decoded
  pixbuf stays visible, the strip keeps the dead cell, Trash silently
  no-ops, and _step can land on the missing entry repeatedly. No
  Gio.FileMonitor anywhere in the app.
- Codec-missing text groups an OS capability failure with file damage
  ("may be damaged, or saved in a format Notebook OS does not read"),
  so the one case the user could act on reads as a broken file.

## OS-WIDE FIRST-GLANCE PASS (rendered at 1024x740, guest theme+fonts)
12 unclaimed apps driven and LOOKED at: cookbook, journal, contacts,
mealplanner, workout, academics, accounting, bills, ebook, language,
maps, packages. All 12 fit the smallest panel. One real defect found and
fixed (cookbook doubled empty state, a825e996). Two candidates MEASURED
AND CLEARED rather than "fixed": accounting's out-of-order ledger dates
are an append-only daybook's insertion order (running balance depends on
it), and calendar's week/day views ignoring a new event was my harness
constructing a fresh window per state — a relaunch legitimately opens on
today. Contacts shows a milder version of the cookbook doubling (terse
"No contacts" in the list beside the full sentence in the pane) — left
as-is, noted, since the list label is short and Finder/Music use the
same shape.

## EXPORT-ATOMICITY CLASS (Aug 14) — 7 apps, 4 fixed here
An export that renders straight onto its destination destroys the file it
is replacing when the render fails part-way, and the usual reason to
export twice is that the document CHANGED, so the casualty is the user's
previous good copy. Fixed already: Comics, Journal (peers), Cookbook
(62dd0215). Fixed in 19f18ed5 via a new shared primitive
nbapp.atomic_write_via: Writer (worst — the destination is a path the
user navigated to in the save dialog), Accounting PDF + CSV, Contacts.
Cookbook migrated off its private copy onto the shared one.
STILL OPEN — VIDEO, and it is the worst of the seven (Codex audit):
ffmpeg is given the final destination with -y (video.py:3570, 3764), and
on failure OR CANCEL the destination is then DELETED (4174-4179). So
cancelling a re-export does not just truncate the old movie, it removes
it. video_selftest:367-377 pins that deletion as correct behaviour but
never starts with a valuable file at the destination — a suite
certifying the defect, the fourth instance of that shape this week.
Fix shape: encode to a temp name in VIDEOS_DIR, verify success and
non-empty, then os.replace; cancel/fail deletes only the temp.
ALSO OPEN, from the same audit (verified reads, not yet fixed):
- Missing ffprobe is treated as "this clip has no audio" (3345-3369 ->
  3623), so on an image without ffprobe a video with sound exports
  SILENT and still reports "Saved". Probe state needs a third value:
  present / absent / could-not-check, and could-not-check must refuse.
- Export ffmpeg is Popen'd with no new session (3970) and cancel only
  terminate()s the direct child (4216-4225) — the same process-group
  defect that wedged the GBA build. No stall timeout either.
- A clip whose source vanished is silently replaced with generated
  colour at export (3586-3588, 3635-3638) and the movie is called
  saved, with "Missing file: %s" appended after the fact.
- "Delete Clip…" advertises a confirm that does not exist (undo is real
  and correct); nearby comments describe a no-undo model that is false.

## GATE FINDING: the write-path check cannot see a PDF export at all
See HANDOFF 2026-08-14 for the full write-up, including the obvious fix
that turns out VACUOUS (every export renderer is also the print
renderer, so exempting the print path blinds the check to the export
path — proved by reverting Contacts to the real bug and watching the
improved gate go 126/126 green). Not landed; the honest fix checks the
CALL SITE rather than the renderer.

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

## THE SAVE-FAILURE CLASS — nine apps, four suites (Aug 14, /loop iteration 5)

FIXED a86311a0 / 5d9855a5 / 0c3bc2ae. Nine apps ended their save handler
with `nbapp.save_failure_reason = str(exc)`, believing a module attribute
was a channel to publish a reason through. It is a FUNCTION. The
assignment reached nobody — nothing anywhere reads that name as a value —
and replaced the shared sentence producer with a string for the rest of
the process, so the next caller in that process gets a TypeError instead
of a sentence. Calculator, Language, Packages, Maps, Terminal, 2048,
Finder, System Monitor, Music. maps.py wrote the belief down in a comment
("Publish the reason through the shared module attribute, as the other
apps do"), which is how it reached nine files.

WHAT IT COST A PERSON: course progress, a calculator tape, a best score,
a view state or a removed-apps list failed to reach the disk and the app
carried on showing work that was no longer anywhere — the exact outcome
nbapp's own docstring calls the worst this OS can produce.

FOUR SUITES CERTIFIED IT. tail_adversarial checked, under the name
"calculator failed save surfaces reason", that the attribute had become a
string. maps_adversarial, language_adversarial and finder_adversarial
went further: they `delattr`'d the real function first so their assertion
could pass. Nothing was surfaced to any person in any of them. This is
the green gate that is green BECAUSE of the defect — fixing the app turns
it red, which is the shape that keeps a defect alive across nine files.
(I found three of the four; the fourth was surfaced by a peer session
hitting the red at HEAD, after I truncated my own grep with `head -20`
and cut off the finder row at line 21.)

THE FIX: nbapp.note_save_failure(owner, exc, path) records the sentence
on the app for a status line that is still on screen, and leaves ONE
message in the notification centre. That second half is the real repair —
these saves fire on a timer and again from the destroy handler, so the
failure usually lands when the window is already going away and the app's
own status line is going with it, which is precisely what the
notification centre exists for. Once per owner, never once per write: a
full disk fails every autosave, and a tray filling with one repeated
sentence is the single failure a notification centre cannot survive.

PACKAGES, separately: its "Remove application" handler guarded the save
with `except (OSError, TypeError, ValueError)` around a helper that
already swallows those same types — dead code, unreachable for the case
it was written for. A failed removal rebuilt the inspector as though it
had worked: the app vanished from the listing and came back at the next
launch with nothing to explain it. The helper now reports whether the
store reached the disk; the listing re-reads the file rather than trusting
what it remembered.

MUSIC, separately (5d9855a5): the two caches Music keeps in its store —
track lengths and tags, both rebuilt from the audio files and both
documented in place as "simply dropped and re-read" — set the same
`damaged` flag a corrupt playlist sets, and that flag locks the store
read-only for the session. One bad length row and Music opens normally,
accepts every playlist made afterwards, and writes none of it. No Save
button in the app to press again, nothing on screen. The read-only law
stays for playlist damage (byte-for-byte, sabotage-proof included); a
cache that regenerates itself is not the work that law protects.

## JOURNAL — export off the GTK thread (Aug 14)

FIXED 3906683d. File ▸ Export to PDF laid out the whole journal on the
GTK thread; PangoCairo shapes every line of every entry with its bold,
italic and quote runs, and until it finished there was no repaint, no
scrolling and no way to stop. Now an nbjobs worker draws a snapshot of
the entry list (so an entry typed mid-export cannot change the file being
written) onto a "<name>.part" sibling that is moved into place — it used
to write directly onto the destination, so a failure replaced a good PDF
with a partial one. The sabotage that proved the atomic check exposed the
loudest form: the previous PDF DELETED outright by the cleanup path.

STILL OPEN, same class, HANDED OFF (cookbook.py is the media/cookbook
lane's file): cookbook.py:1551 `_render_pdf` lays out text and writes the
PDF synchronously — long recipes freeze the window with no repaint and no
cancel. Comics has the pattern to copy (ed0e748b).

STILL OPEN, media.py (Codex task-mst31jv1-dgdyk7 dispatched, verify on
landing): `_pixbuf_any` decodes on the GTK thread on open — the WebP/SVG
fallback is subprocess.run(timeout=25), so one click can freeze the
window for 25 seconds; `_thumb_tick`'s docstring claims GLib.idle_add
means it "never blocks the GTK main loop", which is false.

ON-TARGET this tick (2.2 guest, real clicks): Journal write → autosave
("Saved 00:23") → close → the desktop board's Journal tile flips from the
day-zero empty state to "Written ✓ Friday 14 August". Also fixed a real
gap in the harness itself: tools/guestdrive.py could not send a modifier
combo at all, so every Ctrl+key ever sent to a guest landed as a bare
letter.

### The class swept closed, and one warning for the on-target matrix

A peer session verified the nine-app fix independently rather than taking
it on report (`save_failure_reason =` now has ZERO occurrences in app
code; the only surviving match in de/ is the explanatory comment in
nbapp.py, and ten files call note_save_failure), then swept the GENERAL
class — "assigning to a shared module's attribute believing it is a
channel" — across every de/*.py: no `nbapp.X = …`, no equivalent on
nbprefs/nbicons/nbcommands/nbmotion/nbtransitions/nbi18n/nbjobs/nbnotify/
nbpicker/nbprint/nbstate/nbaudio/nbgame/nbvideo/nbkeyboard, and no
setattr() form on any of them. Run without an indent anchor so it would
catch module-level rebinds too. So the CLASS is closed, not just its nine
instances — there is no second one of these sitting under a different
attribute name.

WARNING FOR WHOEVER WRITES THE ON-TARGET MATRIX (from the same peer, on
the ⤢ maximize fix, 0da06a2b): the zoom check must be run on a Finder
window that has been MOVED AND RESIZED first. Zoom-then-restore against
default geometry passes even when the restore path is broken, because the
"previous" size IS the default size — a vacuous pass of exactly the kind
this campaign keeps catching. Also worth carrying generally:
maximize()/unmaximize() are NO-OPS on this image (matchbox 1.2 has no
_NET_WM_STATE_MAXIMIZED atom); set geometry directly.

## ON-TARGET, 2.3-audit (Aug 14 ~16:40) — the ⤢ box, verified at last
USER-REPORTED BUG CLOSED ON REAL HARDWARE. Booted release/notebookos-2.3-
audit.iso under TCG and drove the real UI over QMP:
- Clicking ⤢ on a Finder window FILLS THE WORK AREA under the panel
  (1280 wide, from y=46 to the bottom). Before 0da06a2b it did nothing at
  all, because Gtk maximize() sets _NET_WM_STATE_MAXIMIZED_* and matchbox
  1.2 has no such atom.
- Clicking it again RESTORES: the screenshot is pixel-identical to the
  pre-zoom one except the clock and a hover tooltip.
WHAT THIS RUN COULD NOT TEST, stated rather than glossed: the window could
not be MOVED or RESIZED first, so "previous geometry" was also the default
geometry and this run cannot distinguish "restores what you had" from
"restores the default". That distinction IS covered host-side —
finder_lifecycle_selftest asserts a restore to an explicit non-default
(800x600 at 120,90) and is red-proved. So: the suite covers the arithmetic,
the guest covers the part the suite cannot (that matchbox honours a
dialog's own configure requests at all).
WHY THE WINDOW WOULD NOT MOVE, measured, NOT a defect: both the title-bar
move (finder.py:1490) and the resize grip (:1516) are gated on
NB_ACCEL=="1", and the grip is only ADDED when accelerated (:971) — so on a
software-rendered machine there is no move, no resize, and correctly NO
VISIBLE AFFORDANCE for either. Coherent and deliberate (the comment says
the compositor is what makes the drag repaint cheaply). Worth knowing: on
exactly that hardware profile — which is what real machines boot with, per
the simpledrm note — ⤢ and Collapse are the ONLY ways to change a window's
size, which makes the fix above matter more than it first appeared.

### Cleared by measurement, not fixed (Aug 14, late)

**The subprocess-cancel sweep: one defect in four candidates.** Burner was
real and is fixed (0b51114c). The other three are correct as they stand:
USB Writer does the image write ITSELF in a Python loop with job
checkpoints — cancel already works, and its only subprocesses are umount
and sync, neither of which spawns anything. Sequencer escalates
terminate → SIGKILL and its child is arecord, which has no children.
The Installer deliberately BLOCKS the window close while the destructive
worker runs, so there is no mid-install cancel to be unresponsive to —
a half-installed disk is worse than an uninterruptible one.

**Text drawn on a canvas, an i18n path nothing else watches.** Text
painted with cairo/Pango never passes through a GTK setter, so neither
nbi18n's setter wrappers nor its construction-time tree walk can reach
it. Swept every drawing call whose literal is a catalog key: TWO hits,
both in the printer test page, both correct. "Notebook OS" is identical
in all seventeen catalogs (a product name). "Printer Test Page" IS
translated everywhere — but the page is drawn deliberately in guaranteed
Latin glyphs only, recorded in the toyfont_check KEEP ledger with its
reason and restated in the function's own comment: a test page is the one
artifact that must render identically on any machine in any locale, and
cr.show_text() renders empty boxes for Greek, CJK and Devanagari anyway.
Wrapping it in _t() would have replaced English with blank boxes for
exactly the users it was meant to help. NO GATE ADDED: the class is
empty, and toyfont_check already watches those same call sites for the
related glyph-coverage reason.

**Regression state:** all sixteen suites covering today's 110 changed
files are green, 42/42 apps construct.

## ON-TARGET, 2.3-audit, second pass (Aug 14 ~17:15) — driven over QMP
Everything below was clicked on the booted image, no shell (see HANDOFF on
the nbdebug gate: gsh cannot work on a normally-booted ISO).
- COOKBOOK's doubled empty state is GONE on target: one message, in the
  main pane ("No recipes / Add one with New Recipe, below the list."), the
  sidebar list correctly silent. Closes a825e996's on-target owing.
- The DESKTOP BOARD renders all 8 tiles with honest empty states, each
  naming the app that fills it ("Add events in Calendar"). Closing an app
  returns to it cleanly — the app-exit path around the shared app-active
  flag (80219c80) behaves on target across three app open/close cycles
  (Cookbook, Workout, Calendar).
- WORKOUT's empty state is single, centred, with its own CTA.
STILL NOT VERIFIED ON TARGET, and why — so nobody records these as done:
- COMICS bubble lettering (the peer's _prompt_layer guard + my nbdiacritics
  replay). Reachable only by clicking through Finder, and each round trip
  under TCG is ~15s; two coordinate misses cost more than the check is
  worth in one sitting. Worth doing as a planned sequence next boot.
- GBASDK .part/.old backup row: needs a shell to create a bystander
  directory beside a saved project. Blocked on the nbdebug gate, NOT on
  the fix. Host suite covers it with 4 red-proved checks.
- EBOOK damaged-library notice: needs a shell to plant a damaged store.
  Same gate. Host suite covers it with 5.

**The leaked-timer class is empty, and the reason is architectural.** Swept
every GLib.timeout_add/idle_add in de/. Two shapes looked wrong and neither
is. (1) Sources STORED on self but seemingly never removed: gbaemu's launch
idle and packages' flash timer both are removed — through a local
(`tid = self._x; self._x = None; GLib.source_remove(tid)`), which a naive AST
walk cannot follow. (2) Sources whose id is DISCARDED entirely, so nothing
could ever cancel them: 41 of those, 10 repeating. They are all safe, because
`nbapp.run` connects destroy to Gtk.main_quit — one app, one process, so a
window closing ENDS the process and every timer with it. The repeating ones
that live in processes which do NOT exit (shell, splash, panel/widgets,
desktopbg) are session-lifetime by design. Sequencer's export tick guards on
_closed anyway, and Composer's playback tick dies with the player its destroy
handler clears.
WORTH KNOWING BEFORE ANYONE "FIXES" THIS: settings.py has no _closed gate and
its print-test poll repeats for up to 90 seconds, which reads as a leak until
you follow the process lifetime. It is not one. If this OS ever grows a
second window inside one process, this class stops being empty — that is the
condition to re-check, not the code.

## ON-TARGET, third pass (Aug 14 ~17:50) — the shell rows, now that they run
tools/run-iso.sh --debug-shell (f9bfb357) unblocked these. Driven by
running the SHIPPED modules on the guest against real files, which is the
right verification for data-path fixes — where the bytes end up, not what
the screen shows.
- GBASDK: a directory named <project>.old beside a saved project SURVIVES
  a save, the project still saves, and no staging directory is left
  behind. That is 5197c92e — the worst data-loss bug found this week,
  where saving recursively deleted the author's hand-made backup —
  confirmed fixed on the real image.
- MEDIA: a photo trashed from the viewer records its origin sidecar, so
  the Finder's Put Back knows where to return it, and the bytes are in
  the Trash. That is 3e00c26f on the real image.
- EBOOK: NOT VERIFIABLE ON 2.3-audit, and my first attempt was my own
  error rather than a defect. The image predates 8771d9e0, so it ships the
  superseded read-only behaviour (_state_read_only, no _store_damaged) —
  which I had already established from the squashfs and WARNED the peer
  about, then wrote a row against current-tree behaviour anyway. The
  failure was a JSONDecodeError escaping my own final json.load, because
  the shipped read-only version correctly refuses to save and leaves the
  damaged bytes in place. Re-checked against the shipped version's OWN
  design: it marks the session and leaves the damaged bytes untouched,
  both pass. So the image is not broken, only older. The current
  keep-saving behaviour needs the next respin.

### Focus after an in-window prompt (Aug 14, late)

FIXED 266ccb9c in comics.py: GTK leaves no focus owner when the widget
holding focus is removed, and an overlay prompt removes its whole layer.
Measured on the real window — focus was a Button in, None out — so after
dismissing any card the keyboard belonged to nothing until the person
clicked back into the page. Novel's capture/restore idiom applied; red-
proved; comics 106/106.

MEASURED AND CLOSED — this replaces the "UNMEASURED" note that stood here.
All three were driven through their own real prompts (none of them share a
prompt API: animation takes rows and a callback, illustrator takes
(label, style, callback) triples and closes via _close_saveprompt,
sequencer opens through _confirm):
  animation    focus returns to the invoker      OK, never broken
  sequencer    focus returns to the invoker      OK, never broken
  illustrator  before=Button after=None          BROKEN -> fixed ee2c69dc
So half the grep's list was wrong, and fixing on its say-so would have
meant two unnecessary changes to working code.

NOTE THE IMPACT IS NARROWER THAN IT LOOKS, which is why this is worth
measuring rather than fixing on sight: these apps put their shortcuts on
the WINDOW's key-press-event, so Escape, Ctrl+S and the tool letters keep
working with no focus owner. What breaks is text entry and keyboard
navigation — the person types and nothing appears.

## ON-TARGET, fourth pass (Aug 14 ~18:20) — COMICS LETTERING, the original report
Driven on the booted image by opening the REAL bubble editor (a bubble
created exactly as a canvas click creates one, then _bubble_editor) and
sending keys to the app's own _on_key:
- Typing e / v / p / b / n / w while the bubble editor is open does NOT
  switch tools. That is the symptom the user reported on 2.0 — lettering a
  bubble selected the Eraser mid-word — gone on the real image.
- Bare Delete while lettering does NOT destroy the bubble being lettered.
- With the editor CLOSED, 'e' still picks the eraser, so the guard did not
  cost the shortcuts their function.
My first attempt at this row PROVED NOTHING and said so: _add_bubble does
not exist, so no editor opened, the letters correctly switched tools, and a
careless reading would have recorded a FAIL against a working guard. The
second attempt asserts the editor is open before typing.

## TASKS — damaged sidecar, VERIFIED but NARROWER than the audit said
Measured, not taken on report: a damaged tasks-app.json IS preserved —
tasks-app.json.damaged-<stamp> holds the original bytes, because
atomic_write_json calls preserve_damaged before replacing. So the audit's
"the original sidecar bytes are gone" is WRONG.
WHAT IS REAL: the rich metadata does not survive. A task with a due date,
a priority and a list comes back as an undated Today item with no project,
and NOTHING TELLS THE PERSON. _read_meta's own docstring already names
this outcome ("quietly flattened every task back to an undated Today") for
the lost-wrapper case it handles; the PARSE-FAILURE case has no such care.
Tasks' store IS the data, so under the damaged-store doctrine the fix is
preserve + keep saving + TELL, exactly as ebook. It needs one string pair,
so it goes to the i18n lane rather than half-landing here. Suggested shape:
a title naming the tasks, plus a body that CARRIES ITS OWN NOUN — the
lesson from the ebook pair, where borrowing music's body would have shipped
wrong gender agreement in six languages.

**Focus-after-prompt, measured and closed (Aug 14).** Comics 266ccb9c and
Illustrator ee2c69dc. The other two on the grep's list — animation and
sequencer — RESTORE FOCUS CORRECTLY and were never broken. Driving each
real window through its real prompt is the only thing that separated them;
half the list was wrong.

**Construct count moved 42 -> 41 for an EXTERNAL reason, not a
regression.** tools/construct_all_host.py derives its app list from
finder.APP_MODULES, and finder.py currently carries somebody's in-flight
change: an app-signing gate (`import nbtrust`, fail-closed on a missing
module, per a docs/APP-TRUST.md that is not in the tree yet) and the
REMOVAL of Terminal from both APP_MODULES and APP_KIND. So the launcher
lists one app fewer and construct honestly reports 41. Nothing crashed.
Recorded because "42/42" has been this session's baseline all day and the
next person to see 41 will reasonably suspect a break. Whoever owns that
change should say whether dropping Terminal from the Applications list is
intended for release — it is a product decision sitting uncommitted, like
the four files already filed above.

### ON TARGET, this session's own drive (Aug 14, private guest /tmp/nbfv)

Booted the CURRENT built target — not the 2.3 ISO, which predates today's
work — in a private instance so the ebook/media lane kept boot-work/.
No respin needed: the overlay had already been synced into
buildroot/output/target, confirmed by grepping the target's own copies for
`size-prepared` (ebook) and `_prompt_return_focus` (comics) before booting.

  * Desktop came up clean: panel, board tiles, Finder listing 35 items.
  * COMICS LAUNCHES AND IS HEALTHY with the day's changes in it — Pencil
    marked selected at launch, dock in the corrected order (Tools, Brush
    Size, Colour + Recent, Shapes, Bubble), status line reading "Drag to
    draw. Square tip, hard edges.", store writing ("Saved 19:31"), 8-page
    recovery book at 26% Fit.
  * THE PROMPT PATH I CHANGED WORKS ON TARGET: Mix Colour opens as a
    proper card (live preview well, R/G/B sliders, Cancel/Apply), Esc
    dismisses it, and typing `e` afterwards selects the Eraser — so the
    keyboard reaches the app once a card has closed, which is the
    user-visible half of 266ccb9c.

HONEST LIMIT ON THAT LAST ONE: pressing `e` proves the WINDOW-level
shortcut ladder still works after a close, which is what a person notices
first. It does not by itself prove `get_focus()` returned to the invoker,
because a window key handler fires with no focus owner at all — that half
is measured directly on the real widget tree (comics_selftest,
illustrator_selftest, both red-proved). Observing focus on target would
need code running inside the app's own process; QMP cannot see it. Said
plainly rather than counted as more than it is.

## REAL-USE DRIVE LANE (Aug 16, apple-quality-2) — the host harness, and what it found first

New instrument: **tools/appdrive.py** drives the REAL app on the host under
guestrun (real key ladder, real in-window menus, synchronous shots at
1024x740, reopen in the same home). Four traps it took to make a key land
are in memory (appdrive-harness). Fan-out in flight: one drive agent per
shipped app → an independent skeptic per finding → a per-app fix agent with
a red-proved named check; results land below as they come.

FIXED IN TREE (uncommitted by agreement with the finder-sweep session; each
red-proved; commit per app once the sweep lands):
- **Writer RecursionError on every caret move through formatted text**
  (writer.py `_flag`): the Aug-15 Codex edit made the format buttons
  ToggleButtons and mirrored caret state with plain set_active, which emits
  "clicked" and ran the edit handler; entering bold text toggled bold off,
  the toolbar re-synced, toggled it back… writer_selftest hung 400s on it,
  and writer_format_selftest hid it with an inert fake Button. Fix:
  nbapp.set_active_quietly for real ToggleButtons. New
  tools/writer_realuse_selftest.py (8 checks; sabotage → 'moving the caret
  through bold text raises nothing' RED). Shipping in 3.0-nomaps (built
  from this tree at 12:44) — Writer is broken for formatted text there.
- **Calendar RecursionError on the sidebar toggle** (calendar.py
  `_on_toggle_cal_clicked`): negated the model and pushed it back with
  set_active; after any path re-showed a hidden calendar without
  rebuilding the row (quick-add, dialog Save, Academics mirror, View menu)
  the next click ping-ponged to RecursionError. Fix: `_set_cal_on(name,
  on)` — one place, row toggle mirrored quietly, five call sites. New
  tools/calendar_realuse_selftest.py (9 checks; sabotage → 5 RED).
- music.py: `_play_track`/destroy left `_flash_timer` holding a fired
  source; now cleared wherever the serial bumps (audit medium).
- Suites repaired to the tree's real contracts (not laundered):
  music_adversarial (its `_save` fakes returned None; the app now rolls a
  FALSY save back, so a stand-in must say True; NoneRow fakes needed hide),
  video_playback (spy on `open_async`, the call the editor makes now).
Batch-1 suites otherwise ALL GREEN on the tree (142 suites).

AUDIT (read-only, Codex Aug-15 diff, my 11 files) — still to act on:
- media.py `_show` routes EVERY image through `_decode_in_background` +
  an "Opening %s" notice → stage flips to the notice per arrow key /
  slideshow step (flicker on virgl, visible pause on TCG). Measure.
- nbvideo.py `open_async` treats a >3s preroll as failure → video.py marks
  `_live_failed` and stays on still frames until Stop; sync `open()` now
  raises on a rejected seek where it used to play from 0. TCG regression?
- writer/cookbook/journal (contacts while editing): delete-event VETOED
  while the autosave fails → on a full/read-only disk the window cannot be
  closed, only killed; the reason is one status flash. UX trap.
- music `_load` now QUARANTINES on partial shape faults (was read-only);
  the visible store can shrink to the subset that parsed with zero user
  action (data survives in the .damaged sidecar). Design regression?
- (14:1x) **Meal Planner crashed on the first save after opening a store
  with a junk day** (mealplanner.py `_serialize_store`: a date key whose
  value was not a dict came through the first pass verbatim and the second
  pass called `.pop` on it — str has no .pop). store_damage "one day is
  junk" was `(crash)`. Fix: a non-dict under a date key is replaced by the
  live day's slots. store_damage ALL PASS; 9 mealplanner suites green.
- (14:1x) **Screenplay: the Codex edit RE-INTRODUCED the "discard unsaved
  changes?" confirmation on New and Open** that 8ddfd945 retired (undo,
  never confirmation) — and broke writing_apps_selftest with it (a
  headless confirm answers Cancel, so Open silently refused). Removed both
  calls; the undo checkpoints stay. writing_apps 115/115.
- (14:0x) store_damage_selftest's flush proxy was stale: bills/workout/
  mealplanner grew a timer-only `_on_destroy`, so the harness "closed"
  without ever saving and reported the untouched not-json file as a
  missing quarantine (kept 0/3, aside=NONE) — no data was ever lost.
  Explicit ACTION_SAVERS now. Also pruned 4 stale PRESERVE_DEBT rows
  (journal/calendar/cookbook/tasks now carry unknown keys — the ratchet
  said so). Gate: ALL PASS. undo/writing_apps/stable_surface/notify/
  packages_*/settings_prefs/splash all green on the tree at 14:15.
- (14:4x) **A window you could not close** (writer/cookbook/journal/
  contacts): the Codex delete-event guards vetoed close while the store
  write failed, with no card and no way out — on a full or read-only disk
  the app could only be killed. New shared **nbapp.close_unsaved_card(win,
  exc, path)**: the papertone "Not saved" card (reason a person can act
  on + "Closing now loses the writing since the last save…", Cancel
  focused, danger "Close Without Saving") — the shape Screenplay/Novel
  already had, now one implementation. All four apps record the failing
  exception and offer the card; the four Codex close suites now assert the
  card is OFFERED and that choosing to close really closes. Rendered and
  looked at (/tmp/card.png). Zero new strings.
- (14:3x) **Media: every image no longer flashes "Opening…"** — the stage
  holds the previous picture until the next is ready; the card appears
  only if a decode outlasts NOTICE_AFTER_MS (220 ms), cancelled by done/
  failed/newer request/destroy. Measured with the harness: a small photo
  swap never shows the card; a 6000x4000 PNG shows it at ~220 ms and lands
  the picture after. media_adversarial re-pinned to that contract (an
  instant refusal is ONE card; a slow one says Opening first). 11 media
  suites green.
- (15:0x) **UNDO ATE EVERYTHING ADDED SINCE THE LAST RECORDED STEP — the
  worst find of the lane so far, and it predates Codex.** Tasks: type three
  tasks, delete one by mistake, Ctrl+Z → ZERO tasks, written to disk. Same
  in Calendar (quick-add three, delete one, undo → none) and by construction
  in every app whose additions never touch() the history: nbapp.UndoHistory
  only recorded a state on touch() (typing) or checkpoint()+commit(), so
  the newest recorded state was the one from launch and Undo of a later
  Delete restored THAT. The undo-law conversion (delete without confirm)
  made the exposure worse. FIX at the primitive: checkpoint() pushes the
  on-screen state (unlabelled, deduped) before the edit it brackets, so
  Undo lands exactly one step back from what the person saw. New
  tools/undo_baseline_selftest.py drives the REAL Tasks and Calendar (add
  3 / delete 1 / Ctrl+Z → 3) — sabotage → both RED by name. undo,
  undo_completeness, nbapp_datasafety and 9 app undo suites stay green.
  Found by drive agents tasks F1 and calendar F1 (and calendar F2, contacts
  C2 look like the same root; the verify pass will say).
- (15:0x) nbvideo: async preroll limit 3 s → 12 s (PREROLL_LIMIT_MS). The
  old sync open waited 3 s and carried on; treating 3 s as failure on the
  software path turned a slow-to-decode clip into still frames until Stop.
- (15:2x) **ABOUT AND GET INFO DREW NOTHING — in every app, since the
  foundations landing 15d4b7ee (Aug 13).** nbtransitions.present_card put
  the scrim, the grow frame and the card into a Gtk.Fixed layer, added the
  layer as an overlay… and never show()ed the layer — a visible child under
  a hidden parent maps nothing. Every drive agent reported "About does
  nothing visible / positioned at 0,0 / Gtk-CRITICAL gtk_fixed_put" and
  filed it as a harness artefact; it is real. Three defects in one
  presenter, all fixed in nbtransitions.py: (1) `layer.show()`; (2) the
  card was positioned with a SECOND `layer.put` — a Gtk-CRITICAL no-op —
  so it stayed at (0,0): now `layer.move`; (3) it was MEASURED HIDDEN (GTK
  reports 0x0), so the paper frame always grew to the 340x220 fallback
  around a 247x110 About revealed top-left inside it: shown for the
  measurement, hidden again before any paint. Also: a box handed in
  unshown is show_all'd (About's own box never was — an empty frame). Four
  new checks in present_card_selftest, each sabotage → RED by name;
  finder_confirm_card / finder_info_card / getinfo_apps / about_origin
  green. Rendered and looked at: About now centred with name + version.
  ON-TARGET OWED: About + Get Info at the next respin (grow motion path).

### ON TARGET, Aug 17 06:05-06:20 (private guest /tmp/nb-aq2, current tree rootfs)
Rootfs rebuilt from the tree (rm images + make; layer.show and
close_unsaved_card grep-verified in output/target), booted headless TCG at
1280x800 with a FAT "USB stick" attached (new tools/mkstick.sh +
NB_QEMU_EXTRA usb-storage) — **the stick route WORKS**: it enumerates,
automounts, and shows in Finder's Devices as FIXTURES with an eject glyph.
That unblocks the three owed file-fixture rows from the Aug-14 HANDOFF.
  * Calendar > About: the card now appears CENTRED with "Calendar / Notebook
    OS" (before the fix nothing appeared at all). Esc closes it.
  * Calendar sidebar toggle: hide Personal, quick-add an event (calendar
    re-shows), click the toggle again → hides cleanly, no recursion.
  * Tasks: type three tasks, right-click > Delete task on the middle one,
    Ctrl+Z → ONLY the deleted task comes back, the other two survive.
  * Desktop board reflects the Calendar event (TODAY 09:00 …).
Harness notes: guestdrive `type` drops ':' (send-key has no shifted colon —
"Dentist 10:00" arrived as "Dentist 1000"; a harness gap, not the app);
guestdrive gained `wheel` and `rclick`; qmp.py now honours NB_WORK.
SEEN, NOT FIXED (decisions):
  * After ANY key press in a window, every button you then CLICK draws the
    2px accent-red focus ring (GTK3 keeps focus-visible sticky once the
    keyboard has been used; the theme comment's "a mouse user never sees a
    ring" holds only until the first keystroke). On target the clicked
    calendar row wore a red rectangle. Design call: keep (accessibility) or
    scope the ring to keyboard navigation via a `:focus(visible)`-equivalent.
  * Finder toolbar shows a breadcrumb pill labelled "." between Actions and
    Applications (the root crumb?) — finder is the sweep session's file;
    told them.

## THE DRIVE LANE'S LEDGER (Aug 17 ~12:00) — 33 surfaces driven, 25 verified

Every shipped app plus the desktop panel, the board and the login screen was
driven by an agent using tools/appdrive.py the way a person does, then EVERY
finding was handed to an independent skeptic told to REFUTE it (fresh home,
own reproduction script, "already fixed in the tree" counts as refuted).

**214 findings CONFIRMED** so far: 14 DATA_LOSS, 55 WRONG_ANSWER, 42 BROKEN,
35 HOLLOW, 68 VISUAL. 19 were refuted — mostly "already fixed in the tree"
(the undo class, the calendar toggle) or harness artefacts, which is the
verify pass doing its job.

Worst confirmed, by app (the fix agents are working these now):
  * writer 13 (2 DATA_LOSS: typing in a table cell never marks the document
    dirty so File > New discards it; Ctrl+Z in a cell deletes the table)
  * video 13, Media Viewer 12 (Delete during a decode trashes the NEXT file)
  * cookbook 12, calculator 12 (Escape on Graph/Table closes the whole app)
  * sequencer 12 (2 DATA_LOSS), novel 11 (New/Open replace an unsaved
    manuscript with no confirm and overwrite recovery)
  * contacts 10 (2 DATA_LOSS: the ★ button and Ctrl+Z discard the open form)
  * academics 9, calendar 8 (all 8 fixed), meal planner 8, journal 7,
    music 7, accounting 7, ebook 7, illustrator 7 (2 DATA_LOSS),
    bills 6, screenplay 6 (Save As .fountain drops the title silently),
    tasks 5 (all 5 fixed), settings 5
  * batch 3 (language, workout, packages, 2048, gbaemu, sysmon, installer,
    usbwriter, burner, shell panel, board, login) drove 100+ findings; their
    verify pass is running now.
- (12:1x) **THE FOCUS RING WAS ON FOR THE MOUSE, OS-WIDE — and the comment
  that said otherwise was certified by a blind instrument.** Papertone draws
  a 2px accent ring wherever GTK asks; GTK asks when the toplevel's
  `focus-visible` is set, and that property DEFAULTS TO TRUE and GTK3 never
  lowers it. So from the first frame every control focused by CLICK wore the
  keyboard ring (seen on target: a red rectangle round Calendar's sidebar
  row). The theme's own comment claims "a mouse user never sees a ring —
  verified: all 28 apps render pixel-identically with this rule added": that
  verification used OFFSCREEN renders, where the window is never active, so
  `has_visible_focus()` was False in every one and NO ring could have drawn
  either way — the instrument could not see what it certified (another entry
  for [[instrument-reports-not-code]]). FIX: nbapp.note_input_modality() +
  track_input_modality() install one GDK dispatcher hook (from install_css,
  so every app inherits it): a button press or touch lowers focus-visible for
  that window, a key press raises it — rings return on the same keystroke
  that needs them. Guarded so it can never take input down (every event is
  handed on whatever happens). New tools/focus_ring_modality_selftest.py,
  9 checks, sabotage → 4 RED by name. construct_all 36/0, segment_row,
  present_card, nbapp_datasafety, undo_baseline all still green.

### FIRST RUN (de/firstrun.py) — driven Aug 17 12:0x (its drive agent died twice on limits)
The OEM new-owner screen, driven host-side with every system write redirected
into a scratch tree (OEM_MARKER/HOSTNAME_FILE/USER_NAME_FILE/SHADOW/XKB_CONF
repointed; host-affecting commands blocked). It is in good shape:
  * The form reads right at 1280x800 (Name, Computer name, Language, Keyboard,
    Password, Password again, Show password, start-without-a-password, Finish)
    and every validation message is specific and true: empty password →
    "Choose a password, or tick the box below to start without one."; mismatch
    → "The two passwords are different."; a bad computer name → "Use letters,
    digits and - for the name."
  * A clean run writes /etc/hostname, /etc/notebookos-user, the XKB conf and
    $NB_HOME/.config/notebook/locale.json ({"lang","keyboard"} — nbi18n's own
    key names), leaves root LOCKED when no password was chosen, and clears the
    marker LAST. Verified by reading every file back.
  * PASSWORD HASHING WORKS ON THE IMAGE, checked by content rather than
    assumed: output/target ships python3.11 WITH `_crypt` (crypt.py +
    _crypt.cpython-311.so) and /usr/bin/openssl as the fallback. The
    docstring's "openssl is NOT in the image" is stale (the walled garden put
    it there); harmless, since crypt is tried first.
  * ALL 18 OFFERED KEYBOARDS RESOLVE against the image's own xkb tree —
    every layout has a symbols file and every variant (jp kana) is defined in
    it. So no one can pick a keyboard the server will reject.
FOR THE USER — a latent dead end, measured but NOT changed:
  `write_keyboard()` returns False when the LIVE `setxkbmap` fails even though
  the boot-time conf was written, and `apply()` then refuses to set the
  password AND keeps the marker. The screen says "This could not be saved:
  Keyboard. Try again." and pressing Finish again does exactly the same thing
  — setup can never complete. tools/firstrun_keyboard_password_selftest.py
  pins that guard deliberately ("a failed live keyboard switch must block
  password mutation"), and its reasoning is sound: a password typed on one
  layout while another is persisted can lock the owner out. I did NOT rewrite
  it, because the trigger is unreachable on this image (above) and the guard
  is somebody's considered decision. DECISION: if a live switch ever does
  fail, should setup (a) finish with no password and say so, (b) keep the
  screen but say which way out to take, or (c) stay as it is? (b) is the
  smallest honest change.
- (12:2x) **NEW GATE: tools/cross_app_contract_selftest.py** — the contracts
  BETWEEN apps, which no per-app suite can see ([[who-writes-it-last]]).
  calendar.json is written by Tasks' Add-event rail, written by Calendar, and
  read by both plus the desktop board: three programs, one flat list. The
  suite drives the REAL Tasks, then opens the REAL Calendar on the SAME home
  and looks for what Tasks wrote, adds one from the Calendar side, finds it
  back in Tasks' rail, then opens the board and checks all three show. 11
  checks incl. an in-suite MUTANT (a Calendar on a different home must NOT
  see the event, so the contract checks can go red). ALL PASS — the triangle
  survived both apps being rewritten by different agents this morning, which
  is exactly the moment it could have broken silently.

### TWO GATED CONTRACTS THAT DISAGREE — New/Open on a document with no file
(Aug 17 ~13:00) Screenplay: this morning I REMOVED the confirm the Aug-15
Codex batch had added to File > New and File > Open, citing 8ddfd945 ("undo
replaces confirmation"), because writing_apps_selftest went red — a headless
worker cannot answer a dialog, so Open silently refused. Two UNTRACKED suites
from that same batch (screenplay_replace_confirm_selftest,
screenplay_replace_status_selftest) pin the confirm and are now RED.
I was too quick. **Undo does not survive a close.** For a script or manuscript
that has no file yet, the recovery store IS the only copy, and New/Open
overwrite it: undo can put the document back while the window is open, and
nothing can once it has closed. That is precisely novel's F3 — "File > New /
Ctrl+N and File > Open replace an unsaved manuscript with no confirmation and
overwrite the recovery store; close and reopen and the book is gone" — which
the independent verifier CONFIRMED as DATA_LOSS. Writer, meanwhile, has always
confirmed (_confirm_discard).
So the OS currently holds three different answers for the same act, and the
undo law's premise fails exactly where the data matters most.
DECISION NEEDED (one decision, three apps): either
  (a) New/Open confirm when the document is unsaved and has no file — Writer's
      shape, and what the Codex batch reached for; or
  (b) no confirm, but New/Open PRESERVE the outgoing document (keep the
      recovery snapshot aside, the way a damaged store is quarantined) so a
      close cannot lose it — the undo law with a durable floor, and the better
      experience of the two.
I have NOT flipped screenplay again: novel's fix agent is deciding the same
question right now, and whatever lands there, screenplay and writer must match
it — along with the two untracked suites and writing_apps_selftest (whose
worker must ANSWER the dialog rather than being the reason to delete it).

### THE RELEASE GATE WAS DEAF TO 20 OF ITS OWN SUITES (Aug 17 ~13:00)
A full `run_all_gates` (744 gates) recorded **34 reds, and most were not
failures at all**: the runner refuses to read success into a zero exit with no
recognised terminal verdict — rightly, because a suite that dies half way also
prints PASS lines and also exits 0 under `sys.exit(len(FAILS))`. Twenty suites
were passing while the aggregate filed them as DID NOT RUN, i.e. protecting
nothing:
  animation (its ending was `TALLY total=377 passed=307 failed=0` — a WORK
  COUNT, not a report), automount_concurrency, desktop_recovery_shape,
  hidpi_icon, journal_close_recovery, journal_structural_save, locale_write,
  messaging_honesty, nbicons, oem_install, osk_lifecycle,
  pinyin_unknown_punctuation, present_card, sleep_lock_order,
  stable_surface_safety, settings_blank_durability, xclipd, xtabletd,
  toggle_fuzz_check — and **construct_all_host**, the gate that catches an app
  crashing on launch.
Each now ends with `RESULT: ALL PASS` / `RESULT: FAILED`. Two more were skip
cases, and a skip is not coverage: `oem_install` skipped "…and the chosen
password verifies" because Python 3.13 dropped `crypt` on this host — so the
one assertion proving a new owner can SIGN IN was inert on the machine that
runs the gates. It now verifies through openssl (present here AND in the image,
and firstrun's own fallback), so it really runs. `xclipd`'s skip (a clipboard
manager already owns the developer's display) is now DECLARED in
run_all_gates.ALLOWED_SKIPS rather than silently swallowing the suite.
REAL failures found in the same run, both from today's fix wave:
  * **toyfont_check 1 BROKEN — calculator.py drew text with cairo's toy API**
    (new graph axis labels). Read every show_text site in the file: there is
    exactly one and its content is format_number() = "%.12g"/"%.Nf" — ASCII
    digits, minus, point, exponent. Recorded as a reasoned KEEP beside
    sequencer's ruler numerals, with the reading that justifies it.
  * i18n_check / i18n_coverage / i18n_source_coverage / jargon_sweep are red
    on strings the in-flight fixes introduced (bills, contacts, novel) — one
    catalog fragment closes them once the wave lands.
STILL OWNED BY IN-FLIGHT AGENTS (do not touch until their fix lands):
  board, cookbook_verbatim, g2048_win_persistence, gbaemu_storage,
  illustrator_recent_shape, mealplanner_cookbook, novel_title, sequencer,
  widgets_timezone, writer_new_durability, screenplay_replace_*,
  contacts_save_failure_actions (FAIL), workout (FAIL), disabled_reason_check
  (installer's new ToggleButtons have no tooltips), accelerator_promise_check
  (writer advertises Ctrl+C/V/X it does not consume).
- (13:2x) **EVERY APP'S `.bak` WAS WORLD-READABLE while its store was 0600.**
  nbapp.preserve_damaged keeps one previous-good copy of a store before it is
  overwritten — and wrote it with a plain `open(path + ".bak", "w")`, which
  takes the umask (0644 here), while atomic_write_json gives the store itself
  0600 through _keep_mode. A full copy of a private journal, address book or
  ledger sat beside the protected file, readable by every account on the
  machine — the one file nobody thinks to look at being the open one. Handed
  over by NOVEL's fix agent (its F12) as OS-wide and outside its edit scope;
  taken here. The .bak now carries the STORE's own mode in both directions
  (a store its owner deliberately opened up keeps a matching .bak — forcing
  0600 would be the mirror mistake _keep_mode exists to avoid). Two new checks
  in nbapp_datasafety_selftest (44 total): reverting the fix reds "a private
  store's .bak is private too (never the umask's 0644)" by name.
- (14:0x) **THE BREADCRUMB SLIVER CAME BACK, and I helped cause it.** This
  morning I measured a 2-pill trail folding at the Finder's default 775px
  width and handed that to the finder session; they guarded `_fold_leading`
  so a ≤2-pill trail never folds — correctly reasoned (a "…" pill is nearly
  as wide as "Home", so folding wins no width). But the trail STILL does not
  fit at 775: "Home › Applications" needs 162px and the crumb scroller was
  allocated 123, because the search entry's natural width (172) took the
  slack from the one child that expands. The scroller then anchored right to
  keep the current folder visible and cut the root pill mid-letter, so the
  first screen every user sees read **"Hidden | Actions | e | Applications"**
  — the exact sliver 52672195 removed, by another route. Seen ON TARGET in
  the 14:00 boot, then reproduced on the host.
  FIX (measurement, not taste): the search entry takes width_chars=10 and a
  130px floor, so the crumb gets 165 ≥ 162 and nothing scrolls. A search
  field is a control that can shrink; a path is content that cannot.
  The existing finder_crumb_fold_selftest could not see any of this — it
  reads the widget tree, and the row genuinely HELD both pills at full width
  while the scroller showed 19px of one. It now also measures the scroller
  (value == 0 and page >= natural) at all three sizes; reverting the search
  width reds "every pill of Home > Applications is fully visible at 775x715
  (no mid-letter sliver)" by name.
- (15:0x) **The confirm-vs-undo contradiction is RESOLVED, one way.** The
  tracked gate tools/confirm_undo_adversarial_selftest.py names
  `self._confirm_replace("New Script")` and `..."Open Script"` in its
  CASES["screenplay"] FORBIDDEN list — the exact calls the Aug-15 Codex batch
  re-added and I removed this morning. Novel's fix agent hit the same wall
  from the other side (its F3, declined: "DOCUMENTED DECISION … CASES['novel']
  forbids 'Discard this manuscript?'"). So option (a) — confirm before New/Open
  — is not available without reversing a campaign decision and reding a
  tracked gate. The two UNTRACKED suites the batch left behind
  (screenplay_replace_confirm_selftest, screenplay_replace_status_selftest)
  pinned the opposite and were red; they are deleted, because two gates that
  can never both be green is not a ratchet, it is a coin toss.
  WHAT REMAINS TRUE, and is now the ONLY path: undo does not survive a close,
  so New/Open on an unsaved, unbound document still loses it — novel F3,
  CONFIRMED DATA_LOSS by an independent verifier. The fix has to be a DURABLE
  FLOOR rather than a question: keep the outgoing text where the person can
  find it. Doing that next in novel (the app the finding names), then
  screenplay; writer already confirms and is not exposed.
- (15:3x) **THE DURABLE FLOOR, in Novel: File > New / Open can no longer lose
  an unsaved book.** With a confirm forbidden by a tracked gate and undo
  unable to survive a close, the answer is neither a question nor a warning:
  before New or Open replaces an unsaved, UNBOUND manuscript that holds
  something, the app writes it into Documents as a real manuscript under its
  own title ("Winter Ships 2026-08-17 1424.json") and posts a notification
  saying where it went. A bound manuscript is untouched (already on disk); an
  empty one writes nothing. Undo still puts the book back on screen — the file
  is what makes CLOSING survivable.
  The message goes to the notification centre and NOT the save chip: measured,
  the next autosave rewrote the chip within a second, and — the stronger
  reason — the chip describes the manuscript ON SCREEN, so putting the
  outgoing book's fate there recreates the exact confusion
  novel_realuse_selftest pins ("a new manuscript carries its own save state,
  not the last one's"). That check caught my first attempt; its expectation
  was right and mine was wrong.
  New tools/novel_new_open_floor_selftest.py, 10 checks (New, Open, empty,
  already-bound, the notification, and that the kept file holds what was on
  SCREEN rather than what was opened); sabotaging _keep_outgoing reds six of
  them by name. 17 novel suites green. Strings "Kept as %s in Documents" and
  "Manuscript kept" are in the catalogs (fragment 071), 17x.
  STILL OWED: the same floor in screenplay (same exposure, same shape).
- (15:4x) **...and the same floor in Screenplay**, which had the identical
  exposure (unsaved + unbound + New/Open overwrite screenplay.json). Same
  shape: write the outgoing pages into Documents under the script's own title,
  post "Script kept" to the notification centre, leave a bound or empty script
  alone. tools/new_open_floor_selftest.py (renamed from the novel-only one)
  now covers BOTH apps in 14 checks. screenplay_open_metadata's Probe learned
  the new contract (it opens onto a bare stand-in, so there is nothing to
  keep). 12 screenplay suites green; string "Script kept" in the catalogs
  (fragment 072).
  WRITER is NOT exposed: it still confirms (_confirm_discard), which its own
  gate allows.
- OWED, blocked on an agent still editing the file: **sequencer re-added three
  retired confirms** in the Aug-15 batch — `_t("New project?")`,
  `_t("Open this project?")`, `_t("Shorten to %s?")` — and the tracked gate
  confirm_undo_adversarial_selftest is RED on all three. Same shape as
  screenplay's, same resolution: drop the confirms (the law) AND lay the floor
  (New/Open keep the outgoing project in Documents), because sequencer has the
  same unsaved+unbound exposure. Do it the moment its fix agent lands.
- OWED (menu_promise_check, after the wave's menu changes): academics
  "New Class…", "Add an Assignment…", "Edit Class…" and cookbook
  "Move to Category…" are flagged "promises to ask but acts at once", and
  academics "New Lecture" / journal "New Entry" / cookbook "Cut" as "asks with
  no ellipsis". The second group is at least partly the gate's own false
  positive — it counts a REBUILT overlay child as a card appearing, and an app
  that rebuilds its page after an action therefore reads as having asked
  (journal's fix agent reported the same conclusion independently). I tried
  the obvious narrowing (ignore the overlay's base child) and MEASURED it
  worse: 4 apps flagged became 27, so it was reverted. The first group cannot
  be that false positive — it is the opposite direction — and wants a look at
  each label. Both are for the next pass; the probe also needs Gtk.Dialog.run
  patched, or it hangs on a modal (it reports "probe blocked" for music).
- (16:0x) CLOSED, by sequencer's own fix agent: the three re-added confirms are
  gone (confirm_undo_adversarial_selftest is PASS again) and it laid the SAME
  durable floor novel and screenplay got — its notification reads "Project
  kept". Three apps, one answer, arrived at independently: New/Open never
  asks, and never loses what it replaces.
- (Aug 17, evening) **CATALOGS CLOSED for the whole wave.** Every string the
  241-fix wave introduced is translated into all 17 languages: fragments
  068 (calendar/calculator), 069 (academics, accounting, bills, contacts,
  cookbook, illustrator, language, media, novel, music), 070 (the four menu
  labels novel and cookbook changed — the two novel ones took the existing
  translation minus its ellipsis rather than inventing a second wording),
  071 (novel's floor), 072 (screenplay's floor) and 073 (burner, installer,
  sequencer, usbwriter — 26 strings, including the installer's shutdown
  instruction and every Disc Burner refusal). Catalogs: 3999 keys x 17.
  i18n_check clean, i18n_coverage FULLY COVERED, i18n_source_coverage PASS,
  catalog_script PASS. The Serbian half of fragment 068 had to be
  transliterated: I wrote it in Cyrillic and this project's sr catalog is
  LATIN — the same defect a previous session transliterated away for 52
  values, caught here by catalog_script_check.
- (18:2x) **THE BOARD SUITE WAS RUNNING HALF OF ITSELF, AND THE HALF IT SKIPPED
  HELD SEVEN REAL FAILURES.** board_selftest exited silently at check 61 of
  103 with status 0 and no output at all: it reloads nbapp three times (to
  re-pin screen_size per panel), so nbapp.claim_single_instance's in-process
  registry was EMPTY in the fresh module while the previous incarnation still
  held the flock — the process took its own lock for a rival copy and
  os._exit(0)'d, and block-buffered stdout went with it. The aggregate filed
  it as DID NOT RUN; the board's own fix agent ran it and saw a clean exit.
  FIX in nbapp: the lock file now carries the holder's pid, so a blocked
  flock is compared against our own — same process, same claim, return
  instead of exiting. (The peer's earlier `me in _INSTANCE_LOCKS` guard was
  right and could not survive a module reload.)
  WHAT THE OTHER 42 CHECKS SAID, once they could run: the pinned column
  needed 1004px of a 998px panel at 1920x1080 and laid a tile 18px BELOW the
  bottom edge — a regression from this morning's board fixes, which correctly
  replaced Tasks' and the calendar's hand-rolled headers with the shared
  builder (that is what stopped "2 / 10 done" rendering as "…") and thereby
  made both cards taller than the row budget's model of them. _HEAD_PX now
  errs HIGH (46 against a measured 39) with the reasoning written down: a row
  fewer is a row you can still reach, a row too many is one you cannot.
  board_selftest 103/103; every widgets suite green; media_adversarial 35/35.

## THE FINAL GATE RUN, TRIAGED (Aug 17 ~17:30–18:30)

The 768-gate run finished at **24 reds**. Every one was triaged; none of them
were "the code is fine, the gate is noisy" — they split three ways.

**A. Gates that were DEAF, not passing (13).** A suite that exits 0 while
printing only per-check lines is reported DID NOT RUN, because a suite that
dies half way prints those lines too. Terminal verdicts added to
`sequencer_selftest` (158 checks that nobody was reading), `rail_measured_check`
(24), `grid_e4_rest_check` (4), `widgets_timezone_selftest`, `tofu_sweep`, and
`data_stress_sweep` — the last of which said `RESULT: no stored field pushes an
app off the panel`, prose the runner cannot grade, now `RESULT: PASS — …`.
`i18n_check` was deaf for a different reason: its hidden-app SKIP lines had no
`ALLOWED_SKIPS` entry, so a clean 17×3999 run was read as partial coverage.
Declared, keyed to the same HIDDEN_APPS list the other two skip rules use.

**B. Real defects (4).**
- `media.py:_do_trash` reached `self._thumb_cache` unguarded — the same
  fixture-contract regression the calendar/journal/music stores took. A
  housekeeping line must not be what raises. `getattr(self, "_thumb_cache", {})`.
- `undo_completeness_selftest` asserted the Trash held **exactly one** file of
  that name. NB_HOME is shared across guestrun invocations, so it was measuring
  the harness — and the "(1) (2) (3)" names it tripped over were the app doing
  the right thing, never overwriting something already trashed. It now diffs
  against a pre-snapshot.
- `voice_check`: one NEW string, usbwriter's `A USB drive is plugged in but
  cannot be identified, so it is not offered.` — ledgered as `allow` with its
  reason, because with a stick in the port `No USB drive is plugged in.` is
  FALSE and the person blames the stick, the port or the machine.
- `silent_refusal_check`: the `video.py:_menu_add_transition` DEBT row went
  STALE — the gate now resolves that guard to the item's own `has_clip and k>0`
  gating. Ratcheted down rather than carried.

**C. Reported TIMEOUT, but working the whole time (1).**
`menu_promise_check` constructs 32 apps and INVOKES all 348 enabled menu items.
Measured cost here: **8m55s**, against the 300s default. A timeout reported as a
failure is a lie about the code; it now has its own entry (1200s). With the time
it needs it is honestly RED: 55 violations in 5 apps, plus `music.py: probe
blocked or exceeded 90 seconds` — a menu item that can block the UI thread for
90s is a shipping defect, not a harness artefact, and is being driven now.

Left running at the end of this triage: the four OS-wide check failures
(`anchored_term`, `button_contrast` — 13 labels under 3.0:1, worst 2.61:1 —
`accelerator_promise`, `disabled_reason`), the menu_promise + silent_refusal
app work, `transition_pacing_probe` coverage (18 of 35 transitions measured; all
18 in band), and a second-pass regression drive of the six apps that took the
most fixes in the wave.

### THE FOUR OS-WIDE CHECKS, CLOSED (Aug 17 ~18:00–19:30)

- **`anchored_term_check`** — the two findings were on OPPOSITE sides. `ja` was
  the CHECK over-anchoring: `A music CD needs a blank CD-R` is the ordinary
  noun used attributively, and the catalog keeps the two senses apart on
  purpose (`音楽` the audio, `ミュージック` the Places row) — the key `Music CD`
  was ALREADY `音楽 CD` and had never been flagged. The anchor list now carries
  a NAME-vs-common-noun field and a `standalone()` predicate, so a name matches
  only where English spells it as one. `zh` was a REAL catalog defect, and the
  hand-sweep found a second one no frame could ever have reached (the GBA SDK
  empty state): the *folder* `Documents` was written `文稿`, the word that
  catalog uses for the content noun — exactly the one-word-two-things collision
  this gate exists for. Net: 969 → **1105** mentions checked. The gate got
  stronger, not looser.
- **`button_contrast_check`** — all 13 labels were the same tone, `#9A9484`
  (`muted-2`, spec'd for "placeholder text, disabled marks"), used as text a
  person must read. Cookbook's placeholders → `muted-3 #8A857A` (3.55:1, still
  quieter than the value they stand in for); novel's chapter disc and video's
  shot numbers → `muted #6E695E`, because their grounds fill with `@select
  #EAE3D2` under hover/selection, where `muted-3` falls back to 2.87:1 — the
  number was faintest exactly when you reach for it. **Now 0 under 3.0:1.**
- **`accelerator_promise_check`** — the DEBT tail was a red herring; six
  findings above it were the failure, and TWO of them were the check LYING
  about the apps: it flattened the enclosing `if`-chain to text, so
  `not (ev.state & CONTROL_MASK)` read as *Ctrl+*. Media was reported as
  binding chords it explicitly REFUSES, and four such false rows were already
  sitting in the ledger. Fixed with real modifier polarity (boolean-tree walk
  tracking `not`/`else` arms). Writer's Ctrl+A/C/V/X were then PROVEN by
  driving the real window — buffer as witness, clipboard as witness — so the
  check now understands that a focused GtkTextView answering the registry's own
  edit commands IS a binding, gated three ways so a renamed row or a repointed
  callback cannot claim it. DEBT 37 → 29, and `tools/writer_clipboard_selftest.py`
  now asserts what nothing in the repo asserted.
- **`disabled_reason_check`** — 9 findings across burner and installer. Both
  now DERIVE sensitivity from the reason (`set_reason(btn, text)`), so a
  disabled control cannot exist without one. Installer's step rail says "This
  step opens when the step before it is finished." and, once a run has begun,
  "The installation has started. Steps cannot be reopened."; burner's Burn/Move
  buttons name the condition that actually holds. 12 new strings, 17 catalogs,
  3999 → **4011** keys, merged through the fragment path (074-disabled-reasons).

### MOTION PACING: 18 → 35 OF 35 ANSWERED, AND TWO GATES THAT COULD NOT GO RED

`transition_pacing_probe` measured 18 of 35 transitions; 10 had no driver.
Six drivers written (splash lift, app picker, video selection, maps view,
media surface, ebook chapter), two resolved as Gtk.Revealer-owned
(`configured-verified` read from the app's own declared token by AST, not
re-typed from the helper), one as genuinely continuous (`system.boot-session`
is a `GLib.timeout_add(70)` easing toward a 0.9 cap it never passes, bounded
only by MAX_MS=30000 — there is no end state to time).

It found:
- **the media surface swap ran at 400ms, twice the PAGE budget** — it faded out
  for a full token AND in for a full token, where every other content
  replacement in the OS splits one token end to end. `media_motion_selftest`
  had pinned the WRONG contract; it now asserts both halves and their sum.
- **a `configured-verified` FAIL could not turn the gate red** — the aggregate
  only consulted `measured` rows, so all 8 configured rows could print FAIL
  under a green RESULT. `motion_inventory_check.pacing_problems` had the same
  hole. Both fixed; every current row passes.
- **`--apply` rewrote the whole inventory file** (indent/ASCII churn: 1289+/1172−
  for a measurement update). Now 146+/29−, measurements only.

**OWNER DECISION — `finder.selection-change`.** The inventory declared a
highlight TRAVELLING between rows, tokened SELECT. That motion does not exist
in the code; what ships is the theme's 90ms colour ease on `treeview.view`. I
called it: **selection feedback is FEEDBACK, not SELECT — friction belongs to
commitment, not to moving a selection**, and a custom travelling highlight over
a cell-renderer TreeView is not work I want landed days before a release. The
entry now declares the motion the app actually performs, at the token that
describes it, with the band NOT widened and no exemption. It is verified by
reading the duration out of **GTK's own parse** of the shipped theme (display
independent, unlike constructing a widget, which aborts without a display), and
red-proved through a new `NB_THEME_CSS` override so a proof never mutates the
shipped theme. Evidence recorded in the entry's note: on a realized widget with
a frame clock the rule DOES interpolate (3 intermediate values over ~40–57ms,
with a Gtk.Button in the same rule as the control, so the negative case could
not be vacuous); what remains unproven is that a per-ROW change eases, because
GTK3 paints TreeView rows through cell renderers against a saved context. The
entry stays `partial` for exactly that reason, and says what would have to
change to build the travelling highlight.

**`RESULT: GREEN — every eligible transition measured in band`**, 35 of 35
answered (24 measured from a real frame trace, 9 configured-verified, 2
continuous-untraced, 0 unanswered).

### BATCH 3 CLOSED — the wave is complete (Aug 17 ~19:45)

All 39 agents of the third drive/verify/fix batch finished: language, workout,
packages, 2048, GBA emulator, System Monitor, installer, USB writer, Disc
Burner, desktop panel, desktop board, **login** and **First Run** — the last two
had each lost an agent to a limit earlier in the day and were retried to
completion. Worst of what the batch fixed, in its own words:

- **Disc Burner: every burn died before the first track.** `_step` polled
  `job.cancelled()`, but `nbjobs.Job.cancelled` is a **property** — calling it
  raised `TypeError` on the worker, which `_burn_error` then mapped to the
  generic "The disc was not written." Both poll sites fixed.
- **2048: the About card did not block the game.** The modal guard read
  `self._about_layer`, a name nothing in the tree ever assigns (nbapp stores
  `_about_card`/`_about_close`), so it was permanently falsy: arrow keys slid
  tiles and spawned new ones behind the card.
- **Language: the word bank graded a correctly built sentence as WRONG** when
  the phrase repeats a word and the learner took one duplicate tile back —
  `chosen.remove(word)` drops the FIRST copy while the screen loses the tapped
  one. 9 shipped phrases have a repeated tile. It cost a heart, too.
- **Packages: Escape closed the whole app** instead of clearing the search box.
- **Desktop panel: the fullscreen-video watch was never scheduled** at all.
- **Desktop board: the Tasks summary and the calendar day drew as a bare "…"** —
  two hand-rolled copies of a header that `_card_shell` had already had fixed.
- **System Monitor: re-sorting pushed the PROCESSOR column off the right edge.**
- **USB Writer: the outcome sentence was overwritten inside the same callback**
  that produced it, so a finished/stopped/failed write said nothing.
- **Login: "Show password" selected the whole field**, so the next keystroke
  replaced the password (reproduced in 5 of 6 fresh processes).
- **First Run: "start without a password" left the disabled fields looking live.**

## THE CONTRAST GATE COULD ONLY SEE BUTTONS (Aug 17 ~19:00–20:30)

`button_contrast_check` measures real rendered labels — but only labels INSIDE
BUTTONS. Everything else a person reads was ungraded, and the tone that failed
in three apps (`#9A9484` muted-2, spec'd for "placeholder text, disabled marks")
was being used as ordinary text across the OS.

**New gate: `tools/text_contrast_check.py`.** It constructs 43 surfaces, each in
its own subprocess, settles the 90ms transition, and measures the colours GTK
COMPUTES — never the hex in the source — in three passes: RESTING (labels,
per-markup-run colours, entry text AND placeholder, TextView text, every
TreeView cell per column per row with the model's own foreground applied,
column headers, menus, tab labels, header bars, popovers), REACHED FOR (hover
and selection really set on the widget that can take them), and DECLARED (for
rules the walk never reached, a real widget tree carrying the real classes and
ancestors). Dead CSS and unprobeable selectors are counted and printed, never
silently skipped. Bars: **4.5:1** small readable text (WCAG AA 1.4.3), 3.0:1
large/heavy, 3.0:1 genuine placeholders (a stated departure — held at the
1.4.11 perceivability floor, because a placeholder at body contrast makes an
empty field look filled), 3.0:1 marks set in text, 1.5:1 disabled ink. Placeholder
and disabled are never inferred from a CLASS NAME (a name is a claim a rename
can silence) but from `get_placeholder_text()`, `is_sensitive()`, and an
evidence table where each row cites the line of code that proves it.

**It found two bugs in itself, both by counting.** `walk()` held `id()` in a
`seen` set without holding a reference, so PyGObject reused freed wrapper
addresses and live subtrees tested as already-seen — novel measured 23 nodes
instead of 90, differently each run. And `button_contrast_check`'s import put
the real `de/` at `sys.path[0]`, so its `--selfcheck` imported the UNSABOTAGED
app and reported that the gate stayed green on unreadable text: the gate was
right, the harness was lying.

**The repaint: 262 failing rules over 698 nodes → 0.** 119 rule edits in 22
files by the sweep agent, then the remaining **81 rules across the 13 files it
was forbidden to touch** (they had live owners) applied here once those agents
landed — every anchor matched exactly once. Worst single case: journal's
`.datebox.active .dbwd` at **1.75:1**, the least readable text in the OS.
Both documented traps were live code: `gbasdk .runbtn` set `color` on the
BUTTON so the OS's one red action drew ink-on-red at 3.32:1, and novel and
calendar were using `@select` as their HOVER fill — the exact collapse `@hover`
was added to the palette to undo.

Two things the sweep left as owner decisions rather than patching:
1. **The saved-state dot** `@ok #7FA98C` is 2.55:1 on paper and appears in nine
   modules. It is exempt today under "a mark its own label also spells out"
   (it always sits inside "● Saved 18:13"). To make it clear 1.4.11 on its own
   the change is `#7FA98C → #4F7A3A` in **all nine at once** — fragmenting one
   state signal across nine apps is worse than the dot.
2. **GTK3 cannot separate placeholder ink from disabled ink** — it draws an
   entry's placeholder in the insensitive colour. Installer's disabled tone put
   its "Name" placeholder at 2.16:1; the placeholder was dropped (the field
   already carries a visible label).

## THE ELLIPSIS RATCHET WENT TO ZERO (Aug 17 ~21:30)

`menu_promise_check`'s DEBT ledger held **55 allowed violations across 24 apps**
at HEAD. It is now **empty**, and the sweep measures **0 violations across 32
apps / 347 invoked items**. 51 of those 55 were slack — apps fixed long ago
whose ceilings were never lowered, which is headroom a regression can climb
back into without turning anything red. The last four were closed here:

- **calculator `Variables…` → `Variables`** (real): the dialog lists stored
  variables under a single Close button. Nothing to answer, so nothing to
  promise. The ellipsis stays on `Function Catalog…` and `Display Mode…`, which
  do ask.
- **settings `Backup`, video `Add Title Card` / `Add Credits`** were the
  INSTRUMENT, not the apps: a fifth evidence defect convicted an item whenever
  an attribute *whose name contains "card" or "prompt"* came to hold something
  — settings' own "Where to copy it" section box, video's lazily-built pixbuf
  cache. Retired as evidence.
- And that heuristic was **hiding a real one**: exactly one item was PASSING on
  it — **contacts `New Contact…`**, which appends the person, selects it, enters
  edit mode and calls `_save()` (on disk before a field is typed) and never asks
  anything. Now `New Contact`, which is also what MENU-CONVENTIONS §2B prints
  for a single-store app.

Two stale ratchets fell out of the same pass: `silent_refusal_check` was red on
a row for `illustrator._show_all_layers` whose menu gating now repeats the
method's guard word for word (pruned, with a red-proof that the row is only
safe while that gating stands), and **`menu_promise_check_selftest` was red AND
vacuous** — it stubbed `subprocess.run` while the parent had moved to capturing
into files, so every case read an empty file and reported "probe failed"; worse,
the blanket stub also answered `tracked()`'s `git ls-files`, so a probe
reporting a violation returned `NOT YET COMMITTED … RESULT: PASS`.

**Still open, and now being worked:** the probe replaces every attribute
starting with `_flash`, and thirteen apps keep a non-callable there
(`music._flash_serial`, `packages._flash_serial`, `cookbook._flash_until`), so
the next comparison raises and the item is never judged — cookbook loses 6 of
its 18 candidates. A "0 violations" that quietly skips items is the shape this
codebase keeps getting burned by, so the fix must also COUNT and NAME what it
still cannot judge.

## THE HOST RENDERS ON A DIFFERENT PANGO THAN THE IMAGE (Aug 17 ~20:30)

**Novel segfaulted on launch on the real image.** No traceback, no window, exit
status 0 — which is exactly why every gate stayed green and every host-side
test passed. Measured on target with `faulthandler`:

    novel.py:742 in _sync_placeholder_position → :636 _editor → :254 __init__

`Gtk.StyleContext.get_property("font", state)` hands back a
`PangoFontDescription` that is **not safe to touch on the Pango the image ships
(1.50.14)** — even `.to_string()` on it segfaults, let alone `get_metrics()`.
The host builds against **1.56.3**, where the same call is harmless. That
version gap is the whole reason it shipped: every host-side gate exercises 1.56.
Fixed by taking the font from the widget's own Pango context
(`get_pango_context().get_font_description()`), which is both safe and the font
GTK actually renders with.

Two latent instances of the same family were found with it: `calendar.py` and
`login.py` both passed `None` as `get_metrics`' language argument, and Pango
1.50 carries no `(nullable)` annotation there, so PyGObject hands the C function
a NULL it dereferences. **A segfault in login.py is an unusable machine.**

This is a new entry for the gate-blind-spot ledger, and the widest one yet: a
host-side suite cannot see a defect that only exists at the shipped library's
version. The only instrument that catches this class is running the app ON THE
IMAGE — which is what `tools/target_app_sweep.py` is for, and why it stays in
the endgame.

Also closed in that pass: **Calendar could not record an event's real length.**
The dialog offered a four-item DURATION picker (30 min / 1 / 2 / 3 hours), so a
09:15–10:45 meeting could not be written down at all and anything off those four
lengths was filed as the nearest one that was. Replaced with an explicit
Starts / Ends pair on the same half-hour grid (plus a closing 21:00 slot);
the stored record has always carried `start` and `end`, so nothing below the UI
changed. Ending before the start is refused inline rather than silently swapped,
changing the start nudges the end along, and reopening rounds the end UP so
editing can never quietly shorten what was written.

### STILL OPEN — sequencer playback on real hardware

On the guest the engine reaches PLAYING, accepts buffers, raises no bus error,
and `nbsynth` renders genuinely non-silent audio — but the mixdown produced
~2.3s of audio in ~3s of wall clock (**14 underruns in 3 seconds**), which is
what "no sound" sounds like. That guest is TCG software emulation (~10x slow),
so the starvation cannot be attributed from here. The renderer already drops
effects rather than the sound when it runs behind (`_render_loop`'s
`overloaded`/`bypassed` path, after 40 low-queue blocks) and the status line
says so. The ALSA-muted hypothesis is CLOSED: `session.sh` unmutes Master,
Speaker and PCM at 85–90% on every login.
**It needs one observation on the real machine: does Music play while Sequencer
does not?** If yes, it is renderer speed and the fix is a deeper prime or an
earlier effects bypass; if neither plays, it is the mixer or the card.

## THE SECOND-PASS DRIVE, AND WHAT THE SKEPTIC FOUND UNDERNEATH IT (Aug 17 ~21:00)

Six apps that took the most fixes in the wave were driven again, then an
independent skeptic tried to REFUTE every claim with its own scripts and its own
sabotage. It confirmed all eight of the highest-stakes findings — and found
work nobody had reported.

**The worst defect of the day: Video's Play had never streamed anything.**
`_play_clip_live` read `clip.get("path")`, and no clip has ever carried one —
`_new_clip()` stores `media`, an index into the bin. So Play was a silent
slideshow for **every clip ever made**, and the wave's whole new async-preroll
apparatus was unreachable code. **The suite that certified playback was
building its fixture in the shape of the bug** (`{"kind":"video","path":…}`, a
clip the app cannot produce) — as was the failure-fallback suite. Both repaired
and now red on the unfixed app.

Also confirmed: a still with an effect or a caption vanished from the preview
(`-ss 0.000` before `-i` on a single-frame input decodes nothing and exits 0);
Contacts lost the open form to File ▸ Import vCard and dropped Role from every
exported vCard; Sequencer lost a take to Escape and lost a second take to
File ▸ New after Save As; Calculator got four arithmetic answers wrong
(`1/x` after `=`, `±` then `×`, a Fix-0 store, and a paste after `=`);
Illustrator saved layer opacities and reopened them at 100% with a green
"Saved" chip over a file that said 55; Media emptied the whole viewer when a
film was trashed from a folder of photographs.

### The skeptic's own findings — none of which anyone had reported

- **A FAKE RED-PROOF.** `confirm_undo_adversarial_selftest`'s PASS-MUTANT block
  read `caught = all(phrase in (source + "\n" + phrase) …)` — true whatever the
  guard does. Proved by killing the guard AND restoring a retired confirm into
  `sequencer.py`: the suite printed PASS for every phrase. Repaired to one named
  predicate both halves call; dead-guard now gives 9 FAILs. (It is still a
  literal-substring grep, so it remains blind to a REWORDING — which is exactly
  how "Clear this track's takes?" walked back in past a green gate.)
- **The durable floor had a hole I left in it.** `_keep_outgoing` in novel and
  screenplay returned early on `if self.doc_path` — "it has a file, so it is
  already on disk" — which stopped being true the moment the writer typed one
  more sentence after Save. File ▸ New then replaced the model AND the recovery
  store, and those words existed nowhere. FIXED in both: novel asks the file
  itself (`_file_behind()` compares the content the serializer writes, minus the
  two view-state keys, and treats any read problem as behind), screenplay uses
  the `_file_dirty` flag `_touch` maintains. `tools/new_open_floor_selftest.py`
  is now 21 checks; with the guards reverted in a scratch tree, 4 go red naming
  the lost work. `tools/appdrive.py` gained `NB_DRIVE_DE` so a red-proof can
  point the driver at a scratch copy instead of mutating a release tree.
- **A picker title could not find its own translation.** nbpicker uppercases the
  title; nbi18n's upper-case fallback tries `capitalize()` and `title()`, and
  neither reaches a key spelled `Export to PDF` ("Export to pdf" / "Export To
  Pdf"). Fixed with a case-folded index consulted **only where the fold names
  exactly one key**, so it can never pick between two entries —
  `tools/i18n_uppercase_fallback_selftest.py` pins both the recovery and the
  refusal to guess. `Export as Audio` had no key at all; added to all 17
  catalogs, each derived from that catalog's own `Export as Audio…`.
- **The notice about a disk that will not take a write could not be written.**
  Every message that matters most is posted by an app whose config directory is
  exactly what just refused — and the spool lived inside it, so `post()`
  returned "" and the person was told nothing. There is now a second spool on
  the temp filesystem, keyed by NB_HOME so sessions never read each other's
  tray, and every reader merges the two (`load`, `prune`, `clear_all`, and the
  key the panel polls once a second). `tools/notify_unwritable_home_selftest.py`
  makes the spool genuinely unwritable — proving the state first, so the suite
  cannot be vacuous — and 6 of its 14 checks go red without the fallback.
- **Still open, handed on:** tasks.py draws the person's own list names through
  the interface catalog ("Home" → "Accueil" on a French install) while the store
  correctly holds what was typed — the same defect novel had, and only 10 of 76
  modules call `nbi18n.set_verbatim`. An OS-wide sweep of that class is running.

## ON TARGET, Aug 17 21:30–22:15 — THE SWEEP WAS LYING, AND THE FINDER HAD NO KEYBOARD

Two findings, and the first one hid the second for an hour.

**1. `target_app_sweep` reported `29/29 apps painted · RESULT: ALL PAINTED`
while not one app had opened.** The sweep types into the Finder's search field
at a measured position and double-clicks the first row; its only verdict is
"the framebuffer changed". On that boot the desktop board was covering the
Finder (an older matchbox in that rootfs did not carry the
`0004-desktop-widget-column-below-windows` patch), so every click landed on the
BOARD — which opened its own app — and every row read PAINTED. The screenshots
prove it: `19-novel.png` is a bare desktop, `24-system-monitor.png` is the
widget board. FIXED: the sweep now proves the launcher is there before it
sweeps — it types into the search field and requires the screen to answer,
refusing to run otherwise — and each app's verdict is measured against the
FINDER screen rather than the first shot, because "the desktop" IS the Finder
over the board and an app that never opened leaves exactly that. Entered as
gate blind-spot shape 28.

**2. The Finder's search box cannot be typed into on the real machine.**
Measured on the current image, where the Finder IS visible over the board:
`_NET_ACTIVE_WINDOW` and `xdotool getwindowfocus` both name **0x…003, a 1×1
window at (-1,-1)** — GTK's group-leader window for the Finder process, not the
Finder's toplevel. Clicking the window does not move the focus (matchbox does
not activate a DIALOG on click), so every keystroke goes nowhere: the search
box keeps its placeholder, and the board's first card wears the keyboard focus
ring instead. Mouse works — rows select, double-click launches — and a launched
app DOES get focus (`xdotool getwindowfocus getwindowname` → `calculator.py`),
so this is the Finder alone. Proved by forcing it: `xdotool windowfocus
0x1e00007` and the same keystrokes land ("cal" appears in the box with a caret).
FIXED in `de/finder.py`: the window asks for the keyboard itself
(`get_window().focus()`) 900ms after start and on every button press, guarded
on `get_visible()` so a hidden Finder can never pull focus off the app in front
of it.

Both were invisible to every host-side gate: the host has no matchbox, no
group-leader race, and appdrive gives the window focus itself.

## THE PERSON'S OWN WORDS RAN THROUGH THE INTERFACE CATALOG — 33 APPS (Aug 17, late)

`nbi18n` translates the widget tree, which is right for chrome and wrong for
content. Only 10 of 76 modules called `set_verbatim`. A full sweep found it in
**33 apps** and fixed every one; two lookup behaviours had widened the blast
radius far beyond exact matches: `_lookup` re-cases UPPER strings, so
`.upper()` was never protection (Finder's title strip, Academics' class header,
Cookbook's kicker, Journal's month heading all bit), and **single letters are
catalog keys** — `B`, `I`, `M`, `S` are the Bold/Italic toolbar glyphs — so
Contacts' alphabetical divider drew `Ж` for the "B" section in Russian and
Animation's mouth badge `M` became `Б`.

**Four cases where widget text had become DATA** (each red-proved):
- **accounting** read the date back out of its own label. `_date_lookup`
  rewrites month names inside date-shaped strings, so on French the row was
  stored as `"17 août"` **and `_iso_for()` could not parse it, so the ISO date
  was empty** — a ledger row reaching the CSV and the PDF with no date at all.
- **language** rebuilt the word-bank answer from `button.get_label()` and
  graded against it: on French a correctly built sentence assembled as
  `['is','big']`, costing a heart and the skill's crown.
- **screenplay** compared its status chip against the ENGLISH markup while the
  widget held the translated one, so "Exported PDF" stuck forever in all 17
  non-English languages, hiding the real save state.
- **novel**'s chapter list re-translated on every REBUILD; the known fix only
  covered the typing path.

Also fixed: the desktop board (every card), every notification's title and body
in the panel, Finder's breadcrumb and USB volume labels, the installer's
computer and account names **on the last screen before an irreversible erase**,
the login greeting, ebook's entire book text, and 20 more.

Two techniques worth keeping: a `ComboBoxText` row is not a widget, so
`set_verbatim` cannot reach it — `combo.append(None, text)` is unpatched, fills
the same column and reads back exactly; and `set_tooltip_markup` is unpatched
where `set_tooltip_text` is not, but because `nbapp._name_hook` fills a missing
ACCESSIBLE NAME from the tooltip, each helper sets the accessible name itself,
or the fix would have traded a translated tooltip for an anonymous control.

The new gate `tools/user_content_verbatim_selftest.py` drives 27 apps twice in
the same language — once with a made-up name the catalog cannot know, once with
a catalog word — and requires the COUNT of widgets showing it to match. Counting
is load-bearing: the first version asked only "is it on screen somewhere" and a
sabotaged Tasks passed, because the header still showed the name one row above
the broken sidebar. Apps that persist are closed and reopened on the same Home,
so the assertion is about what a person sees next time. **54 checks, 0 failed.**

### THE FIX THAT DID NOT TAKE, AND THE ONE THAT DID (Aug 17 ~23:00)

The first Finder-focus fix — `get_window().focus()` on a window-level
`button-press-event` — **did not work on the rebuilt image**, and the reason is
worth keeping: a `GtkEntry` STOPS the button press, so it never bubbles up to
the toplevel handler. Clicking the search box was exactly the case that could
not reach it.

The rule belongs one layer down, in the GDK dispatcher `nbapp.track_input_modality`
already installs for the focus-ring modality — the one place that sees every
event before any widget does. **Clicking a window now gives it the keyboard**,
OS-wide: on any button or touch press, if that toplevel is not active, its GDK
window asks for the focus. The window manager on this machine never does it
(matchbox activates what it maps and does not move focus for a click on a
DIALOG it did not activate — which is every window this OS opens), and every
app already gets focus at launch, so for them the call is a no-op. The startup
`_claim_focus` timer in `de/finder.py` stays as the belt to that braces: the
board maps after the Finder and used to end up holding the keyboard from boot.

### ROOT CAUSE, MEASURED: THE DESKTOP BOARD HELD THE KEYBOARD ALL SESSION

Three app-side fixes failed before the real cause was found, and each failure
was informative:

1. `get_window().focus()` on a window-level `button-press-event` — **never
   ran**: a `GtkEntry` stops the press, so it never bubbles to the toplevel.
2. The same call from `nbapp`'s GDK dispatcher (which sees every event first)
   — **never ran either**: the Finder, the board and the panel each install
   their OWN stylesheet and so never reached `nbapp.install_css`, where the
   dispatcher is armed. That is now fixed on all three (with
   `tools/input_rules_armed_selftest.py` to keep it that way), and it means the
   focus-ring modality rule had been missing on those three surfaces too.
3. With the dispatcher armed, `Gdk.Window.focus()` STILL did nothing — and so
   did `xdotool windowactivate`. matchbox advertises `_NET_ACTIVE_WINDOW` in
   `_NET_SUPPORTED` and ignores it.

The cause was one line in `de/widgets.py`: **`self.set_accept_focus(True)` on
the desktop board.** The board maps last, so matchbox made it the focused
client and re-asserted focus onto it after every click — `xdotool
getwindowfocus getwindowname` answered `nb-desktop-widgets` whatever was
clicked, and even a direct `XSetInputFocus` was taken back on the next press.
Proved by killing the board on the running guest (the Finder appeared and
worked) and then by flipping the flag on the guest and restarting the board:
focus moved to `finder.py` immediately and the search box typed `calcal`.

The board now declines the keyboard. The cost is stated in the code and in
`board_selftest` (whose assertion is now the reverse of what it was): the
board's cards cannot be reached by Tab. They stay clickable, they keep their
accessible names, and every app they open is fully keyboard-driven — which is
the trade a desktop backdrop should make. A window that is furniture must not
hold the keyboard for the whole session.

### WHAT THE REWRITTEN SWEEP NOW SAYS (Aug 18 ~00:15) — AND THE ONE THING LEFT

With the launcher working and the sweep judging by the MENU BAR rather than by
the picture, the run is honest and it is **RED**: `22/29 painted, RESULT:
FAILED`. Reading the shots is what settles it — every "painted" frame carries
the same name, **Academics**. So the search text is not reaching the box during
the loop (it does at preflight: "zqxv" filtered the list and the probe passed),
and the double-click therefore always lands on the same row of the unfiltered
list. The seven "no change" rows are the ones where the double-click hit
nothing at all.

That is an instrument problem, not an app problem, and the app-level facts it
was built to check are already established by hand on this same image: the
Finder is on screen at boot, its search box types and filters, apps open from
it, apps close, and the desktop comes back. **The next step for this file is to
make the loop re-focus the search entry the way the preflight does** (the
preflight clicks and types immediately; the loop clicks after an app has just
closed, and the click almost certainly lands before the Finder is back) — then
re-run and read the names again. Do not delete the check: it is now the only
instrument in the tree that can tell an app that opened from a picture that
changed, and it caught its own predecessor's 29/29 lie.

### THE ISO REFUSED TO PUBLISH ITSELF, AND IT WAS RIGHT (Aug 18 ~00:30)

`mkrelease --iso-only` ended with **`RESULT: NOT BOOTABLE — DO NOT SHIP`**, on
one check of eight: *a nonempty in-image EFI System partition (type 0xEF) is
declared* — the image carried only `0xee`, the GPT protective entry.

Cause, from the log: **`/tmp` ran out of space** (it is a 16G tmpfs shared with
every build and guest on this box, and my own QEMU work dirs were holding 4.5G
of it). The Secure Boot re-master extracts the WHOLE ISO and writes a second
one; its copies failed one at a time —
`cp: error copying '…/sb/efi.img' … No space left on device` — the re-master
was abandoned with a warning, and what came out was the UNSIGNED grub-mkrescue
ISO with no ESP. The gate caught it and refused to publish, which is exactly
the behaviour that keeps a broken image out of a release.

Hardened `tools/mkiso.sh`: it now picks the larger of `$TMPDIR`, `/var/tmp` and
`/tmp`, PRINTS which it chose and how much room it has, and **dies with a
readable message if there is less than 6 GB** rather than silently producing an
ISO that cannot boot a UEFI machine. A warning that only appears in the middle
of a 900-line build log is not a report.
