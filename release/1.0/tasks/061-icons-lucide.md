# Task 061 — Lucide icon rendering

Lane: `batch-0810`  
Final method correction to tasks 057 and 060

## Outcome

The coordinate-authored icon language has been removed. Every one of the 137 public glyph keys now selects a professionally designed Lucide 1.31.0 SVG. `tools/gen_nbicons.py` compiles the pinned SVG geometry into `nbicons_data.py`; `nbicons.py` only interprets those generated commands with Cairo. No Notebook OS glyph coordinates are authored by hand.

The generator uses only `xml.etree.ElementTree` and parses `M/L/H/V/C/S/Q/T/A/Z`, absolute and relative forms. Quadratics and smooth commands preserve the SVG reflection rules; endpoint-parameterized elliptical arcs are split into cubic Beziers. Circle, ellipse, rectangle, line, polyline, and polygon elements are normalized with paths to `m/l/c/z` tuples. Filled Lucide subpaths are emitted separately and filled with the caller’s ink color. Floats are rounded to four places with stable formatting, and regeneration is byte-identical.

The public APIs and behavior remain intact: `draw(ctx, name, size, color, width, mirror)`, `glyph_for` and fallback, aliases, `_DIRECTIONAL` RTL behavior, pixbuf memoization, `surface`, `image`, `set_image`, `scale_factor`, and all existing HiDPI machinery. The renderer scales Lucide’s native 24 grid to the requested size, defaults to the 1.6 house line weight, and uses round caps and joins.

## Complete mapping

| Notebook key | Lucide stem | Rationale |
|---|---|---|
| `writer` | `file-pen-line` | Direct Lucide semantic match. |
| `novel` | `book-open` | Direct Lucide semantic match. |
| `comics` | `panels-top-left` | Panels give a distinct comic-page silhouette without inventing artwork. |
| `academic` | `graduation-cap` | Direct Lucide semantic match. |
| `journal` | `notebook-pen` | Direct Lucide semantic match. |
| `screenplay` | `scroll-text` | Scroll-text denotes a script and leaves the clapperboard uniquely available to Video. |
| `tasks` | `list-todo` | Direct Lucide semantic match. |
| `calendar` | `calendar` | Direct Lucide semantic match. |
| `workout` | `dumbbell` | Direct Lucide semantic match. |
| `cookbook` | `cooking-pot` | Direct Lucide semantic match. |
| `mealplanner` | `utensils` | Utensils prioritizes meal meaning and stays distinct from Cookbook’s pot. |
| `ebook` | `book` | Direct Lucide semantic match. |
| `calculator` | `calculator` | Direct Lucide semantic match. |
| `accounting` | `scale` | Balance scales communicate balanced books without duplicating Book or Calculator. |
| `bills` | `mail` | Postal mail matches the Bill Tracker’s bill-by-post concept and differs from Inbox. |
| `contacts` | `contact-round` | Direct Lucide semantic match. |
| `messages` | `message-circle-more` | The dotted speech bubble denotes an ongoing message thread. |
| `g2048` | `layout-grid` | A four-cell grid is the nearest Lucide match for the 2048 board. |
| `tetris` | `blocks` | Blocks provides the closest professionally drawn falling-block silhouette. |
| `gamepad` | `gamepad-2` | Direct Lucide semantic match. |
| `mappin` | `map-pin` | Direct Lucide semantic match. |
| `globe` | `languages` | Languages represents the Language app more precisely than a generic globe. |
| `cartridge` | `cassette-tape` | Cassette-tape is the closest Lucide removable-media cartridge silhouette. |
| `illustrator` | `pen-tool` | Direct Lucide semantic match. |
| `sequencer` | `audio-lines` | Audio-lines reads as waveform sequencing and differs from both music apps. |
| `composer` | `music` | A single music note means composition while remaining distinct from Music’s record. |
| `video` | `clapperboard` | Clapperboard is reserved for video production, avoiding a Screenplay collision. |
| `media` | `image` | Direct Lucide semantic match. |
| `music` | `disc-3` | Disc-3 gives the music player a distinct album/playback silhouette. |
| `packages` | `package` | Direct Lucide semantic match. |
| `signal` | `radio-tower` | Radio-tower is the strongest Lucide broadcast-signal mark. |
| `play` | `play` | Direct Lucide semantic match. |
| `stopsq` | `square` | Square is Lucide’s apt stop transport symbol. |
| `pause` | `pause` | Direct Lucide semantic match. |
| `wclose` | `x` | Direct Lucide semantic match. |
| `wzoom` | `maximize-2` | Direct Lucide semantic match. |
| `wshade` | `chevron-up` | Chevron-up denotes collapsing/shading a window title bar. |
| `rew` | `rewind` | Direct Lucide semantic match. |
| `ff` | `fast-forward` | Direct Lucide semantic match. |
| `folder` | `folder` | Direct Lucide semantic match. |
| `home` | `house` | Direct Lucide semantic match. |
| `desktop` | `monitor` | Direct Lucide semantic match. |
| `disk` | `hard-drive` | Direct Lucide semantic match. |
| `trash` | `trash-2` | Direct Lucide semantic match. |
| `search` | `search` | Direct Lucide semantic match. |
| `back` | `chevron-left` | Direct Lucide semantic match. |
| `backspace` | `delete` | Direct Lucide semantic match. |
| `fwd` | `chevron-right` | Direct Lucide semantic match. |
| `up` | `arrow-up` | Direct Lucide semantic match. |
| `down` | `arrow-down` | Direct Lucide semantic match. |
| `viewlist` | `list` | Direct Lucide semantic match. |
| `viewgrid` | `grid-2x2` | Direct Lucide semantic match. |
| `check` | `check` | Direct Lucide semantic match. |
| `link` | `link` | Direct Lucide semantic match. |
| `quote` | `quote` | Direct Lucide semantic match. |
| `plus` | `plus` | Direct Lucide semantic match. |
| `star` | `star` | Direct Lucide semantic match. |
| `inbox` | `inbox` | Direct Lucide semantic match. |
| `bullet` | `list` | List is the standard bulleted-list control. |
| `number` | `list-ordered` | Direct Lucide semantic match. |
| `highlight` | `highlighter` | Direct Lucide semantic match. |
| `toc` | `rows-3` | Rows-3 represents a compact table of contents. |
| `alignleft` | `text-align-start` | Direct Lucide semantic match. |
| `aligncenter` | `text-align-center` | Direct Lucide semantic match. |
| `alignright` | `text-align-end` | Direct Lucide semantic match. |
| `alignjustify` | `text-align-justify` | Direct Lucide semantic match. |
| `indent` | `list-indent-increase` | Direct Lucide semantic match. |
| `outdent` | `list-indent-decrease` | Direct Lucide semantic match. |
| `table` | `table-2` | Direct Lucide semantic match. |
| `eject` | `eject` | Direct Lucide semantic match. |
| `library` | `library-big` | Library-big distinguishes the collection from individual Book/Novel glyphs. |
| `bookmark` | `bookmark` | Direct Lucide semantic match. |
| `pencil` | `pencil` | Direct Lucide semantic match. |
| `brush` | `paintbrush` | Direct Lucide semantic match. |
| `eraser` | `eraser` | Direct Lucide semantic match. |
| `fill` | `paint-bucket` | Direct Lucide semantic match. |
| `picker` | `pipette` | Direct Lucide semantic match. |
| `line` | `slash` | Slash is the nearest pure line-tool glyph in the professional set. |
| `rect` | `rectangle-horizontal` | Direct Lucide semantic match. |
| `duplicate` | `copy` | Direct Lucide semantic match. |
| `ellipse` | `circle` | Circle is the nearest Lucide closed curved-shape tool. |
| `eye` | `eye` | Direct Lucide semantic match. |
| `eyeoff` | `eye-off` | Direct Lucide semantic match. |
| `prev` | `skip-back` | Direct Lucide semantic match. |
| `next` | `skip-forward` | Direct Lucide semantic match. |
| `zoomin` | `zoom-in` | Direct Lucide semantic match. |
| `zoomout` | `zoom-out` | Direct Lucide semantic match. |
| `rotate` | `rotate-cw` | Direct Lucide semantic match. |
| `trfade` | `blend` | Blend represents one image fading into another. |
| `trdissolve` | `layers-2` | Layers-2 represents two image layers dissolving together. |
| `trwipe` | `square-split-horizontal` | Square-split-horizontal shows the divided fields of a wipe. |
| `trslide` | `arrow-right-to-line` | Arrow-right-to-line expresses a frame sliding to its boundary. |
| `triris` | `scan` | Scan’s corner aperture is the closest abstract iris transition. |
| `trblack` | `square` | Square is a neutral full-frame field for fade-to-black. |
| `album` | `disc-album` | Disc-album explicitly joins record and album meaning. |
| `artist` | `user-round` | Direct Lucide semantic match. |
| `vol` | `volume-2` | Direct Lucide semantic match. |
| `shuffle` | `shuffle` | Direct Lucide semantic match. |
| `repeat` | `repeat-2` | Direct Lucide semantic match. |
| `box` | `box` | Direct Lucide semantic match. |
| `update` | `refresh-cw` | Direct Lucide semantic match. |
| `sources` | `server` | Server represents stacked package/update sources. |
| `sys` | `settings` | Settings is the canonical gear for the system settings app. |
| `terminal` | `square-terminal` | Direct Lucide semantic match. |
| `sysmon` | `activity` | Direct Lucide semantic match. |
| `installer` | `hard-drive-download` | Hard-drive-download directly depicts installing onto a disk. |
| `gbasdk` | `square-code` | Square-code marks the SDK as a source-code tool, not a game cartridge. |
| `usbwriter` | `usb` | USB names the target medium; Installer already owns drive-download. |
| `cup` | `coffee` | Coffee is the closest handled-cup subject mark. |
| `palette` | `palette` | Direct Lucide semantic match. |
| `family` | `users-round` | Users-round is the clearest professionally drawn family/group mark. |
| `bolt` | `zap` | Direct Lucide semantic match. |
| `question` | `circle-question-mark` | Circle-question-mark remains legible as a standalone help subject. |
| `nosign` | `ban` | Direct Lucide semantic match. |
| `shirt` | `shirt` | Direct Lucide semantic match. |
| `paw` | `paw-print` | Direct Lucide semantic match. |
| `leaf` | `leaf` | Direct Lucide semantic match. |
| `clock` | `clock-3` | Direct Lucide semantic match. |
| `cloud` | `cloud` | Direct Lucide semantic match. |
| `compass` | `compass` | Direct Lucide semantic match. |
| `bus` | `bus-front` | Direct Lucide semantic match. |
| `plane` | `plane` | Direct Lucide semantic match. |
| `heart` | `heart` | Direct Lucide semantic match. |
| `body` | `person-standing` | Person-standing is the closest whole-body subject mark. |
| `cross` | `cross` | Direct Lucide semantic match. |
| `briefcase` | `briefcase-business` | Direct Lucide semantic match. |
| `coins` | `coins` | Coins communicates money without colliding with Accounting’s scales. |
| `cart` | `shopping-cart` | Direct Lucide semantic match. |
| `ball` | `circle-dot` | Circle-dot is a deliberately generic ball subject; Lucide has no neutral sports ball. |
| `tree` | `tree-pine` | Direct Lucide semantic match. |
| `city` | `building-2` | Building-2 is the clearest city/buildings subject silhouette. |
| `flame` | `flame` | Direct Lucide semantic match. |
| `crown` | `crown` | Direct Lucide semantic match. |
| `lock` | `lock-keyhole` | Direct Lucide semantic match. |
| `trophy` | `trophy` | Direct Lucide semantic match. |
| `target` | `target` | Direct Lucide semantic match. |
| `speech` | `message-circle` | Direct Lucide semantic match. |

## Weak or approximate matches

- `cartridge → cassette-tape`: Lucide has no GBA/ROM cartridge; this is the closest removable magnetic-media shell and is preferable to drawing one.
- `g2048 → layout-grid`: it communicates the board, not the number-merging rule.
- `tetris → blocks`: it communicates interlocking game blocks but not a canonical tetromino.
- `trfade → blend`, `trdissolve → layers-2`, `trwipe → square-split-horizontal`, `trslide → arrow-right-to-line`, `triris → scan`, and `trblack → square`: Lucide has no video-transition family, so these are the nearest coherent abstract controls.
- `ball → circle-dot`: Lucide has sport-specific balls but no neutral ball; the generic circular object avoids choosing the wrong sport.
- `line → slash` and `ellipse → circle`: these are the nearest professionally drawn primitive-tool marks.

No weak match was supplemented with hand-authored geometry.

## Retired task-060 checks

The filled-family weight band and 16 px silhouette-dominance checks are retired. They enforced the rejected mid-century filled language rather than correctness of the Lucide method; retaining them would incorrectly penalize Lucide’s intentionally open, finely balanced stroke family. Nonzero rendering, bounds, determinism, mapping provenance, and mirroring remain enforced.

## Proof and gates

- `.codex-scratch/icons-lucide-before.png`: the rejected task-060 set, captured before replacement, all 137 keys labelled at 24 and 48 px.
- `.codex-scratch/icons-lucide-after.png`: the generated Lucide set, all 137 keys labelled at 24 and 48 px.
- `.codex-scratch/icons-lucide-insitu.png`: trash, music, folder, writer, sys, and terminal at 16/22/24/48 on `#F1EEE6`.
- `python3 -m py_compile` over `nbicons.py`, `nbicons_data.py`, `gen_nbicons.py`, and `nbicons_selftest.py`: **PASS**.
- `python3 tools/nbicons_selftest.py`: **PASS**, 137 keys at 16/24/48; complete mapping and vendored SVG coverage, generator-current byte comparison, nonzero ink, bounds with AA tolerance, determinism, directional mirroring, application mapping uniqueness, LICENSE presence, and generated ISC provenance.
- Corrupted command tuple in a scratch data copy: **PASS-MUTANT**, fails `FAIL bounds: writer`.
- Duplicated app mapping in a scratch data copy: **PASS-MUTANT**, fails `FAIL app-uniqueness`.
- Missing LICENSE in a scratch vendor copy: **PASS-MUTANT**, fails `FAIL license: vendor/lucide/LICENSE is missing`.
- Explicit generator rerun plus byte comparison: **PASS**.
- `git diff --check` on the scoped implementation: **PASS**.

## Display-owed

`python3 tools/construct_all_host.py` was attempted without an X server. It discovered 40 constructors; GTK could not initialize, yielding `CONSTRUCT: 0 ok, 40 crashed`. This is the only display-owed gate. All pixel proofs and the exhaustive suite ran headlessly through Cairo `ImageSurface`s as required.

## Dispatcher verification (batch-0810, 2026-08-10)
Display rerun: nbicons_selftest PASS (all three mutants fire), construct_all
40/0. Personal red-proof (one path tuple corrupted to (480,-333) in a scratch
copy): the generator drift check AND the bounds checks at 16/24/48 all fail
by name. Contact sheets + in-situ strip reviewed: legible at 16px, uniform
professional weight throughout. Idempotence proven by the suite's
regenerate-and-byte-compare. VERIFIED per M2.
