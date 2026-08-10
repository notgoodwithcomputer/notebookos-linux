# Task 057 — OS icon redesign

Lane: `batch-0810`

## Family rules

- Every mark remains a 24×24, 1.6-unit ink-line pictogram in `#1A1916`, with
  round caps and joins and roughly two units of breathing room.
- Objects stand frontally and upright. Frames now share a restrained 1.2-unit
  corner radius (automatically capped on narrow parts), replacing the mix of
  hard boxes and unrelated rectangle piles.
- A glyph is an outer silhouette or one confident gesture plus at most one
  identifying accent wherever the subject allows it. Fine detail was kept only
  where it survives the 16 px rendering.
- Axis-aligned strokes retain half-grid placement where optical crispness
  matters. Open marks remain open and framed devices remain framed, preventing
  the family from acquiring one uniform, heavy visual density.
- All 136 names, aliases, directional semantics, and the lane's existing
  `composer` glyph remain present. Nothing below `draw()` was changed.

## Drawing vocabulary

The op table now documents and supports four compact additions:

- `RR x y w h radius`: stroked rounded rectangle.
- `B c1x c1y c2x c2y x y`: true cubic Bezier segment.
- `AR cx cy radius start end`: stroked arc segment, with angles in radians.
- `F`: fill the current path.

Legacy `M`, `L`, `R`, `C`, `A`, and `Q` behavior is unchanged. Authored `R`
operations in `ICONS` are normalized to `RR` after table construction; this
gives every existing object the family corner language while leaving `R`
available with its original square semantics.

## Largest redraws

- **Novel** is now a gently opened book drawn as one continuous, curved
  silhouette instead of two adjacent rectangles.
- **Journal** uses a rounded cover, binding rule, and three small stitch marks;
  **Screenplay** uses a rounded clapper silhouette with a readable striped top.
- **Tasks** is an open list gesture with a single check accent, replacing the
  generic box-around-check construction.
- **Calculator** has a softened instrument body, display, and four legible
  operator marks instead of six dense dots. **Accounting** is a banknote with
  curved guilloche-like bands and one seal rather than three unrelated circles.
- **2048** and **Tetris** retain their instantly recognizable tile systems, but
  use softened tiles and sparse interior ticks to keep their game identities
  without becoming black grids.
- **Sequencer** is a set of three fader gestures on rails. The existing
  **Composer** staff-and-block concept was preserved and refined with small
  rounded note regions, explicitly building on the lane's uncommitted glyph.
- **Video** has a compact rounded camera body and confident lens silhouette;
  **Media** has one rounded picture frame and a cleaner landscape gesture.
- **Music** is a joined beamed pair with an open diagonal stance, giving its
  notes more motion and less rectangular weight.
- All remaining app, Finder, transport, formatting, game, and subject glyphs
  were reviewed together on the same sheet. Their silhouettes were already
  strong enough to retain; every rectangular component now receives the shared
  radius treatment, including calendar, contacts-adjacent frames, terminal,
  settings/system marks, packages, GBA tools, USB writer, documents, and all
  auxiliary framed controls.

## Proof and tests

- Before proof: `.codex-scratch/icons-before.png` (136 keys at 24 and 48 px).
- After proof: `.codex-scratch/icons-after.png` (same order, sizes, labels, and
  warm-paper background).
- `python3 -m py_compile .../nbicons.py tools/nbicons_selftest.py`: **PASS**.
- `python3 tools/nbicons_selftest.py`: **PASS**, 136 keys at 16/24/48; drawing,
  bounds, nonzero coverage, family weight, determinism, and semantic mirroring.
- Scratch-module out-of-bounds mutant: **PASS-MUTANT**, fails by name as
  `FAIL bounds: writer`.
- Scratch-module empty glyph mutant: **PASS-MUTANT**, fails by name as
  `FAIL coverage: writer has an empty op list`.
- `git diff --check` for the implementation and suite: **PASS**.

## Display-owed

`python3 tools/construct_all_host.py` was run headlessly. It found all 39
constructors but reported `Gtk couldn't be initialized` for each and finished
`CONSTRUCT: 0 ok, 39 crashed`. This is the only display-owed gate; no display
server was probed or started. The cairo proof sheets and exhaustive icon suite
both ran headlessly.
