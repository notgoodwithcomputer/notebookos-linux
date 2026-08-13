# Task 060 — Mid-century modern icons

Lane: `batch-0810`  
Correction to: task 057

## Family rules

- The default body is solid `#1A1916` ink: filled polygons, circles, ellipses,
  rounded slabs, and tapered masses. Open strokes survive only as substantial
  2.6-unit accents or intrinsically text-like marks.
- Detail is normally transparent paper carved out of the body. The shared
  radius vocabulary is 1–2 units; the optical box is generally 3..21 on the
  24-unit grid.
- Shapes use the period's circle/slab/wedge vocabulary, a frontal stance, and
  at most one useful asymmetry. Small controls use the same weight: transport
  is solid triangles/bars and alignment is made from heavy rounded rules.
- The family must first read from its outer silhouette at 16 px. Decorative
  detail is subordinate and no glyph is allowed to collapse into a lone dash,
  sparse ticks, or an unexplained frame.

The renderer adds `PF` (filled polygon), `RF`/`RRF` (filled rectangle/rounded
rectangle), and `E` (filled, optionally rotated ellipse). `KPF`, `KRF`, `KA`,
and `KE` carve paper-coloured negative space. Existing `M/L/B/AR/R/RR/C/A/F`
operations keep their old meanings. Nothing below `draw()` was changed;
`glyph_for`, `_DIRECTIONAL`, pixbuf/surface/HiDPI, scaling, and image helpers are
unchanged.

## Three standards-setters

- **trash:** replaced the outline basket with a tapered/rounded solid bin, a
  separate floating lid slab and handle, and a carved horizontal slot.
- **music:** replaced stroked circles and stems with two true, 20-degree tilted
  ellipse heads, stout stems, and one rising filled beam slab.
- **folder:** replaced the traced contour with overlapping solid folder masses,
  an integrated tab, a confident sloping front, and a carved paper gap.

These were rendered at 24 px and inspected before applying the same silhouette,
slab, and negative-space logic to the rest of the table.

## Weight and silhouette gates

Across the deliberately mixed app/UI inventory, the accepted antialiased ink
coverage is **0.085–0.53** at 16, 24, and 48 px. The lower edge admits compact
but bold chevrons/checks; the upper edge admits discs and substantial framed
objects without accepting a full-cell blob. This is much higher than the old
outline floor and is stable across scale.

Application identities receive the stricter 16 px silhouette test: thresholded
ink must have at least **0.16** cell coverage and its largest 4-connected
component must contain at least **55%** of total ink. Fifty-five percent permits
intentional floating parts (a lid, note head, or binding) while rejecting a set
whose identity is mostly disconnected fragments. The added hairline Writer
mutant fails by name as `FAIL silhouette: writer@16`.

## Per-glyph completeness audit

The current table contains **137** keys (task 057's document said 136, but the
tree also contains `composer`). Every actual key was audited below. “Complete”
means the 16 px mark reads as the named object/action, not merely that it draws.

| Glyph | Verdict |
|---|---|
| academic | Complete — solid mortarboard and hanging supports. |
| accounting | Complete — substantial banknote/seal object. |
| album | Complete — bold record with centre knockout. |
| aligncenter | Complete — four heavy centred text rules. |
| alignjustify | Complete — four equal heavy text rules. |
| alignleft | Complete — alternating left-set text rules. |
| alignright | Complete — alternating right-set text rules. |
| artist | Complete — head and confident shoulder sweep. |
| back | Complete — stout directional chevron. |
| backspace | Complete — key silhouette plus negative cross. |
| ball | Complete — bounded ball with panel identity. |
| bills | Complete — solid envelope with carved flap. |
| body | Complete — whole figure, not stray anatomy ticks. |
| bolt | Complete — filled lightning wedge. |
| bookmark | Complete — full ribbon silhouette and notch. |
| box | Complete — dimensional parcel silhouette. |
| briefcase | Complete — solid case, handle, and centre rule. |
| brush | Complete — handle, ferrule, and pointed bristles. |
| bullet | Complete — three substantial bullets with rules. |
| bus | Complete — solid vehicle body, windows, and wheels. |
| calculator | Complete — solid instrument with carved display/keys. |
| calendar | Complete — bound page mass with open date field. |
| cart | Complete — basket, handle, and two wheels. |
| cartridge | Complete — solid ROM shell, label knockout, notches. |
| check | Complete — heavy, balanced confirmation mark. |
| city | Complete — three weighty building masses. |
| clock | Complete — bold dial and joined hands. |
| cloud | Complete — unified overlapping circular silhouette. |
| coins | Complete — coin disc with currency identity. |
| comics | Complete — complete panel page, not loose rules. |
| compass | Complete — disc plus directional needle. |
| composer | Complete — connected score slab with carved staff and blocks. |
| contacts | Complete — unified head-and-shoulders silhouette. |
| cookbook | Complete — full lidded pot and body. |
| cross | Complete — filled medical cross. |
| crown | Complete — crown body, points, and base. |
| cup | Complete — cup body, rim, and handle. |
| desktop | Complete — monitor body and stand. |
| disk | Complete — rounded drive with slot and lamp. |
| down | Complete — substantial arrow, not a tick. |
| duplicate | Complete — two overlapping sheets. |
| ebook | Complete — reader slab with carved copy and home key. |
| eject | Complete — filled wedge above a bold base. |
| ellipse | Complete — thick elliptical ring, not a hairline oval. |
| eraser | Complete — tilted eraser body and ground. |
| eye | Complete — almond silhouette and pupil. |
| eyeoff | Complete — complete eye crossed by bold slash. |
| family | Complete — two whole, differentiated figures. |
| ff | Complete — two solid transport wedges. |
| fill | Complete — tipped paint vessel and falling drop. |
| flame | Complete — closed organic flame with inner tongue. |
| folder | Complete — tabbed mass, sloped flap, paper gap. |
| fwd | Complete — stout directional chevron. |
| g2048 | Complete — unified four-cell tile field with cross gap. |
| gamepad | Complete — controller body and controls. |
| gbasdk | Complete — heavy complete code-bracket emblem. |
| globe | Complete — bold globe disc and latitude/longitude structure. |
| heart | Complete — closed warm heart silhouette. |
| highlight | Complete — broad marker body and baseline. |
| home | Complete — house mass, roof, and door. |
| illustrator | Complete — substantial geometric drafting A. |
| inbox | Complete — full tray with receiving notch. |
| indent | Complete — bold text block and entering arrow. |
| installer | Complete — solid drive receiving an arrow. |
| journal | Complete — solid bound book with carved binding/copy. |
| leaf | Complete — asymmetric leaf body and midrib. |
| library | Complete — three standing, weighty volumes. |
| line | Complete — broad diagonal tool stroke with endpoint. |
| link | Complete — two joined circular links. |
| lock | Complete — closed shackle and solid lock body. |
| mappin | Complete — pin silhouette with centre knockout. |
| mealplanner | Complete — menu card with plate and cutlery. |
| media | Complete — solid picture card with carved landscape/sun. |
| messages | Complete — speech panel, tail, and dots. |
| music | Complete — two filled notes on one bold beam. |
| next | Complete — solid next wedge and stop bar. |
| nosign | Complete — heavy prohibition disc/slash. |
| novel | Complete — whole open-book silhouette. |
| number | Complete — numbered list with weighty rules. |
| outdent | Complete — bold text block and exiting arrow. |
| packages | Complete — complete dimensional parcel. |
| palette | Complete — palette disc, thumb hole, paint wells. |
| pause | Complete — two solid rounded transport bars. |
| paw | Complete — pad plus four readable toe forms. |
| pencil | Complete — full pencil body, point, and ferrule. |
| picker | Complete — complete eyedropper silhouette. |
| plane | Complete — filled swept paper-plane silhouette. |
| play | Complete — solid transport triangle. |
| plus | Complete — weighty balanced addition mark. |
| prev | Complete — solid previous wedge and stop bar. |
| question | Complete — substantial hook and terminal dot. |
| quote | Complete — paired, grounded quotation bodies. |
| rect | Complete — thick rounded rectangular tool mark. |
| repeat | Complete — two complete return sweeps and arrowheads. |
| rew | Complete — two solid reverse wedges. |
| rotate | Complete — complete circular action with arrowhead. |
| screenplay | Complete — clapper mass, angled lid, open slate field. |
| search | Complete — bold lens and joined handle. |
| sequencer | Complete — substantial rails and fader blocks. |
| shirt | Complete — full garment silhouette, sleeves, neck. |
| shuffle | Complete — two complete crossing routes and arrowheads. |
| signal | Complete — weighted radio dot and two broadcast arcs. |
| sources | Complete — two solid source units with status lamps. |
| speech | Complete — complete balloon with tail and dots. |
| star | Complete — closed five-point emblem. |
| stopsq | Complete — solid rounded transport square. |
| sys | Complete — joined gear/sunburst with hub. |
| sysmon | Complete — grounded chart and complete activity trace. |
| table | Complete — solid table field with readable cell structure. |
| target | Complete — bold concentric target and centre. |
| tasks | Complete — substantial list body and check identity. |
| terminal | Complete — solid screen with carved prompt and cursor. |
| tetris | Complete — recognisable joined T tetromino. |
| toc | Complete — three heavy rules plus offset terminal dot. |
| trash | Complete — solid tapered bin, floating lid, carved slot. |
| trblack | Complete — full transition card and diagonal black field. |
| trdissolve | Complete — full card and readable dissolve spots. |
| tree | Complete — tiered conifer body and trunk. |
| trfade | Complete — two complete adjoining fade fields. |
| triris | Complete — full card and central iris disc. |
| trophy | Complete — cup, handles, stem, and base. |
| trslide | Complete — full card and solid movement arrow. |
| trwipe | Complete — two complete wipe fields. |
| up | Complete — substantial arrow, not sparse ticks. |
| update | Complete — complete rotation body and action head. |
| usbwriter | Complete — full stick, plug, and contacts. |
| video | Complete — solid camera body and lens wedge. |
| viewgrid | Complete — four substantial grid tiles. |
| viewlist | Complete — three bold bullets with list rules. |
| vol | Complete — solid speaker body and broadcast curve. |
| wclose | Complete — heavy window-close cross. |
| workout | Complete — full connected dumbbell. |
| writer | Complete — folded sheet with carved copy rules. |
| wshade | Complete — window-control slab with carved shade slot. |
| wzoom | Complete — complete window-control object. |
| zoomin | Complete — bold lens with joined handle and plus. |
| zoomout | Complete — bold lens with joined handle and minus. |

## Proof and gates

- `.codex-scratch/icons-mc-before.png`: untouched task-057 set, all 137 keys,
  labelled at 24 and 48 px.
- `.codex-scratch/icons-mc-after.png`: replacement set in identical ordering.
- `.codex-scratch/icons-mc-insitu.png`: trash/music/folder plus
  writer/sys/terminal at 16, 22, 24, and 48 on `#F1EEE6`.
- `python3 -m py_compile .../nbicons.py tools/nbicons_selftest.py`: **PASS**.
- `python3 tools/nbicons_selftest.py`: **PASS**, 137 keys × three sizes,
  determinism, bounds, coverage, weight, semantic mirroring, and silhouette.
- Bounds mutant: **PASS-MUTANT**, `FAIL bounds: writer`.
- Empty mutant: **PASS-MUTANT**, `FAIL coverage: writer has an empty op list`.
- Hairline mutant: **PASS-MUTANT**, `FAIL silhouette: writer@16`.

## Display-owed

`python3 tools/construct_all_host.py` was attempted headlessly. It discovered
40 constructors; GTK could not initialize without a display, so the honest
result is `CONSTRUCT: 0 ok, 40 crashed`. No X server was started or probed. The
cairo ImageSurface proofs and the full icon suite are green headlessly.

## Dispatcher verification (batch-0810, 2026-08-10)
Display rerun: nbicons_selftest PASS (bounds/empty/hairline-silhouette
mutants all fire), construct_all 40/0. Contact sheets + in-situ strip
reviewed: the filled-silhouette family holds at 16px; worked examples
(trash/music/folder) meet the brief. VERIFIED per M2.

## REJECTED by the design owner (2026-08-10, after commit)
"Most of the icons are still bad... it's invariably turned out bad every time
that method has been used. They have to be ELEGANT." Third rejection of a
coordinate-authored set — the METHOD is judged, not the style. Superseded by
task 061: the vendored Lucide 1.31.0 set rendered as real SVG vector paths;
hand-authored coordinate glyphs are banned going forward.
