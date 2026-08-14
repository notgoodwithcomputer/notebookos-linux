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
