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

## GBA SDK trio — ALL 16 AUDIT ITEMS CLOSED (d151cea0, 77479de1, b1bd8988,
## 7b95eea9): async build (#9), open-confirm (#1), scalar quarantine (#3),
## real discard keys (#5), EEPROM DMA fixed-addressing + selectable types
## (#6/#11), no-health every-instance (#8), help identifiers (#12), gbaemu
## quarantine (#13) + bounded post-paint scan (#10), honest aside… (#16 spec
## side), Build & Play home in Build menu + play-anyway card (#7), affine
## survives loading (#4), spec shipping-note (#14/#15). Suite 574/574.
## STILL OPEN:
- gbaemu keyboard on target: arrows did not move the example player (known
  weak area — Aug 7 red-gate handoff). Needs vbam input-path work in the
  wrapper. Re-test after respin.
- Codex fix-round job task-msrvz3qu-v6gmg3 is a ZOMBIE (silent 1.5h+);
  USER: /codex:cancel it. All its items were completed in-session.

## OPEN — apps
- Animation: guest playhead/clock chip wording pass — "Saved HH:MM" chip
  meaning (recovery) vs document staleness; consider naming the store.
  Low-priority now the unbound close is silent.
- Comics: deeper flows still unaudited at Apple bar: Zine Print full run,
  Export PDF at scale on target, Place Image, select-tool handle affordances
  at low zoom, CJK bubble lettering at 100%.
- Finder: crumb bar clips the leading pill mid-letter (renders as a stray
  "e" button on the DEFAULT Applications view). Fix design: leading "…"
  pill per animation scene-strip precedent. finder.py now COMMITTED clean
  (7b95eea9) so this is a normal edit now.
- Board: tiles end 44-77px short of the pinned column at 1920/1366 (3
  board_selftest reds, pre-existing) — first screen on wide monitors.
- Journal board tile greets a fresh system with a red X "Not written" —
  alarm language on day zero; siblings use gentle grey. widgets.py edit.
- Comics: export/zine run synchronously on the UI thread (~10s guest);
  give them the animation-style worker card. Lower priority than it was.
- sequencer/settings/writer toy-font rows: writer with fonts session;
  sequencer/settings are reasoned keeps (digits / fixed-English test page).
- packages[el]: tightest minsize in OS (13px/35px slack) — preventive.

## OPEN — OS-wide sweeps not yet run at the new bar
- Remaining ~38 apps: real-use drive-through each (task #6). Order by
  consumer surface: media, music, video, writer*, illustrator*, maps,
  calendar, tasks, cookbook, journal, ... (*peer-held files: report, don't
  edit).
- i18n_coverage_check: 141 uncovered (61 new) + 35 stale baseline rows.
- jargon_sweep: 5 unaccounted in govorimolib.
- TREE LANDED: all stranded lanes committed suite-green (5a000f10,
  d369456e, 15d4b7ee, d151cea0, 7b95eea9, 32c0030a, ebc50cc3 + catalogs
  in 77479de1). Remaining dirty = the fonts/tablet session's claim
  (burner/writer/ebook/illustrator/nbicons_data/fonts/.config/kbuild/
  xorg/desktop.config/.gitignore/their suites) — they land their own.

## DECISIONS FOR THE USER
- D-close: Animation now matches Comics for unbound films (silent close,
  recovery restores). Bound films still ask. OK?
- D-tree: may I verify-and-commit the dead lanes' uncommitted app diffs
  wholesale after their suites pass, or do you want per-lane review?
- D-x264: BR2_PACKAGE_X264=y is in .config; next ISO needs
  `make -C buildroot ffmpeg-dirclean` first (HANDOFF note) — include in
  respin?
