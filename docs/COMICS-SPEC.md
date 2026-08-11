# COMICS — the Notebook OS comic zine studio (normative spec)

The workflow this app exists for: draw a comic zine end to end — pixel-art
pages with panel frames and hand-lettered word bubbles — organised as a
5.5×8.5" booklet, then print it as a fold-down-the-middle saddle-stitch zine
on letter paper, or export it as a PDF. The visual idiom is Homestuck: hard
pixel edges, a comic hand-lettering face, chunky white bubbles with black
rims. Functionally it is the merger of two shipped apps: **Illustrator's
byte-exact pixel drawing engine** and **Novel's zine print pipeline**.

Everything below is decided. Where this spec names a shipped app's mechanism,
copy that mechanism's *behaviour and rules* (with an attribution comment),
not a private import — `de/comics.py` must not import `illustrator` or
`novel`; the shared code it may import is the `nb*` family, exactly as every
other app does.

---

## 0. The three inherited laws

1. **The artwork is a byte buffer, never a cairo path** (Illustrator's law).
   Every mark is written into premultiplied ARGB32 by run/span/point
   routines; a painted pixel is either exactly the colour or exactly
   untouched. `set_antialias(ANTIALIAS_NONE)` on every chrome/canvas cairo
   context that touches artwork presentation. Copy Illustrator's
   `px4`/`CLEAR4`, `brush_runs`, `_line_points`, `_ellipse_spans`,
   `_ellipse_outline`, `_snap45`, `_square`, the scratch-preview scheme, and
   the stamp/span writers, with a comment naming illustrator.py as the
   source of truth for the shared geometry.

2. **One page model backs every output** (Novel's law). What the canvas
   shows, what the thumbnail shows, what Export to PDF writes and what Zine
   Print imposes are all produced by ONE flatten routine per page. There is
   no text re-layout at print time — Novel's RecordingSurface/PDFSurface
   metric trap is *structurally impossible here* because pages leave the app
   as finished pixels. Keep it that way: the PDF path embeds the page
   raster; it never re-renders text or objects at print resolution.

3. **The store laws** (the campaign's data-safety law, as landed in
   novel.py/journal.py): damaged store → `nbapp.preserve_damaged` aside,
   never overwritten; wrong-shape store → quarantine, blank start, and the
   session must say so; `_extra` unknown top-level keys survive a save;
   every save is `nbapp.atomic_write_json`; a failed save surfaces
   `nbapp.save_failure_reason` in the status chip; Save As adopts its
   destination only after bytes landed (Illustrator's two-phase lesson).
   Mirror the CURRENT novel.py implementation of load/save/close-guard —
   it is the most recently audited document app.

---

## 1. Page geometry — the one clean number

A page is a fixed **550 × 850 px** pixel canvas. That is 100 px per inch of
the 5.5 × 8.5" zine page, and it maps onto the print page (396 × 612 pt,
`nbprint.HALF_W_PT/HALF_H_PT`) at **exactly 0.72 pt per px** — no rounding
anywhere in the pipeline. Both numbers are module constants (`PAGE_PX_W`,
`PAGE_PX_H`, `PRINT_SCALE = 0.72`); nothing else may restate them.

There is ONE page size. No per-document density option: one grid, one look,
and a page drawn today composites beside a page drawn next month. (Finer
work belongs to Illustrator; a placed image — §8 — brings it in.)

* Document = ordered pages, **minimum 4, maximum 32** (a zine, not a graphic
  novel; the cap also bounds memory). New document = **8 pages**.
* Page 1 is the front cover, the last page is the back cover, pages 2 and
  N-1 are the inside covers. This is *positional*, not stored — the Pages
  strip labels those four rows (Front cover / Inside front / Inside back /
  Back cover) and everything else is "Page N".
* Per page: raster **layers** (1..4, Illustrator's Layer semantics; the
  bottom layer is created `fill_white=True` — paper, not transparency),
  **panels** (§5), **bubbles** (§6), and one flag `mask_gutters` (§5).
* Render order everywhere (canvas, thumbnail, flatten, print):
  **paper → layers bottom→top (visible × opacity) → gutter mask (if flag
  and ≥1 panel) → panel borders → bubbles.** Objects are always above art;
  art can never be painted over a bubble. This is the app's one honest
  z-rule and it is not configurable.

### Memory model
Only the ACTIVE page (and the previously active page, as a ping-pong cache)
holds decoded `cairo.ImageSurface` layers. Every other page holds its layers
as PNG bytes (the same encoding the save file uses). Switching pages encodes
the leaving page's layers back to bytes and decodes the entering page's.
A 550×850 ARGB32 layer is 1.87 MB; the two decoded pages are ≤ 15 MB, and a
32-page document costs its PNG bytes, not its pixels. Thumbnails are small
persistent surfaces (§7) and cost nothing that matters.

Undo frames own their own byte copies, so history is independent of this
cache (§9 for bounds).

---

## 2. Window layout

Left → right: **dock (240 px — the grid_check RAIL; in a ScrolledWindow
NEVER/AUTOMATIC — the Illustrator CJK lesson) · canvas mat (expands, scrolls) · right column
(240 px)**. Below: the status bar. The whole window must lay out at
**1024×722** in every language (`minsize_sweep --one comics`), which the
three fixed-width columns plus scrolling interiors guarantee by
construction.

* **Dock** (top to bottom, the sentence a comic is made in): TOOLS →
  BRUSH SIZE / SHAPES (Illustrator's groups; **no Mirror group** — pages are
  not sprites) → BUBBLE (§6's settings) → COLOUR (Illustrator's 112-swatch
  palette, active chip + name, Mix Colour…, Recent row — same vocabulary,
  same reuse of its catalog keys). Groups stay put and **dim** when the
  active tool cannot use them (never reflow); the dock scrolls.
* **Canvas mat**: Illustrator's mat verbatim — kinetic scrolling and
  capture-button-press disabled, papertone field, centred `canvasframe`,
  integer zoom with FILTER_NEAREST ≥ 1 and BILINEAR below, pixel grid from
  8× (G), the brush-footprint outline under the pointer, scratch-surface
  shape preview. Zoom steps/fit = Illustrator's (`ZOOM_STEPS`, fit on first
  allocate and on document open).
* **Right column**: PAGES strip (§7) on top, expanding; LAYERS panel below
  it, fixed height — Illustrator's layer rows (eye / name / opacity %),
  raise/lower/new/delete buttons, opacity slider, its catalog keys reused
  verbatim. Layer cap 4 per page ("New Layer" disabled at the cap, the
  Illustrator idiom).
* **Status bar**: left = current tool's hint (ellipsized; the only place
  Shift-constrain and tool behaviour are discoverable); centre = cursor
  "x, y" in page px, or the live W × H of a dragging shape (LTR-isolated
  `_dims`, copied); right = "Page %d of %d" → zoom stepper (minus / % /
  plus / fit, Illustrator's marks) → save chip.

Motion: no bespoke canvas animation. Page switches repaint immediately (a
pixel tool wants immediacy); chrome transitions come from the theme.

---

## 3. Tools

Illustrator's eight, plus three of Comics' own. Single-key shortcuts in
tooltips; same dock grid of icon + NAME, two columns.

| Tool | Key | Hint (status bar, function-only) |
|---|---|---|
| Select | V | Click a bubble or panel. Drag to move it; handles resize; Delete removes it. |
| Pencil | P | Drag to draw. Square tip, hard edges. |
| Brush | B | Drag to draw. Round tip, hard edges. |
| Eraser | E | Drag to rub back to the paper. |
| Fill | F | Click an area to flood it with the colour. |
| Line | L | Drag end to end. Hold Shift for 45° steps. |
| Rectangle | R | Drag corner to corner. Hold Shift for a square. |
| Ellipse | O | Drag corner to corner. Hold Shift for a circle. |
| Eyedropper | I | Click the artwork to take that colour. |
| Bubble | W | Click to place a word bubble. Click a bubble to edit its text. |
| Panel | N | Drag corner to corner to frame a panel. |

The eight drawing tools reuse Illustrator's hint strings verbatim (existing
catalog keys) — except the Eraser's: on paper-white pages "transparent" is a
lie, so the Eraser writes **opaque white paper** on the bottom layer's
composite? No — simpler and honest: the Eraser erases to transparency
exactly as Illustrator's does (upper layers), and the bottom layer is
white paint, so erasing on the bottom layer reveals nothing darker. Keep
Illustrator's semantics, change only the hint wording to the one above
(new key). The eyedropper samples the ART LAYERS composite (not objects),
exactly what its hint says; it returns to the previous drawing tool after
one pick (Illustrator's rule).

Keyboard beyond tools: `[` `]` brush size, `+`/`-`/`0` zoom in/out/actual
(and Ctrl+Plus/Minus/0/9 as menu accelerators), `G` grid, **Page Down /
Page Up** next/previous page, arrows nudge a selected object 1 px (Shift:
10 px), Delete removes a selected object, **Esc deselects / cancels the
open prompt — Esc NEVER deletes and never destroys work** (OS law; Esc with
nothing open closes the app through the ordinary close guard).

---

## 4. Colour

Illustrator's palette, verbatim: `_HUES/_VALUES/_MUTED/_NEUTRALS/_STAPLES`
builders, 112 swatches, the same composed hover names (existing catalog
keys), active chip + colour name, Mix Colour… overlay, Recent row persisted
in the app's config. Bubbles and panels do NOT take the palette colour —
their ink is black on white by convention (that is what makes a page read
as comics); the palette drives the drawing tools only.

---

## 5. Panels

A panel is `{x, y, w, h, border}` in integer page px; border width default
**3**, range 1..8. The interior is transparent — a panel is a *frame over
the art*, so art may be drawn edge to edge and the frame reads on top. The
border is a pixel rect ring (outer rect minus inner rect, span-filled, pure
black), drawn above the layers.

* **Create**: Panel tool drag (live scratch preview like the shape tools;
  Shift = square). Or **Page ▸ Panel Layout…** — an overlay card with the
  preset grid choices **1 · 2 rows · 3 rows · 2×2 · 2×3 · 3×3**, drawn as
  little diagrams (marks, not words), plus Margin and Gutter number entries
  (defaults **30** and **14** px). Applying replaces the page's panels —
  one undo frame ("Undo Panel Layout"). The overlay uses the **state-dict
  pattern** (the Canvas-Size lesson: the card's widgets are destroyed
  before the callback runs, so every input mirrors into a plain dict on
  "changed" and the apply reads ONLY the dict).
* **Select/move/resize**: Select tool. Hit order: bubbles first (topmost
  = latest-created), then panels (interior counts). Drag moves; 8 square
  handles resize (min 24×24); arrows nudge. Selection is drawn as a
  1 px marching-ants-free hairline + handles in signage red `#C8341E` —
  screen-only chrome, never rendered into a flatten.
* **Delete**: Delete key or the selection's context — immediate + undoable
  ("Undo Delete Panel"). No confirm (the undo law).
* **Hide art outside panels** — per page, Page menu, `✓` tick, default
  off. When on and the page has ≥ 1 panel, the flatten paints opaque paper
  white over everything OUTSIDE the union of panel interiors (even-odd
  cairo path of integer rects — native, cheap, pixel-aligned), between the
  art and the borders. Gutter discipline without gutter tedium.

---

## 6. Word bubbles — the lettering engine

A bubble is `{style, x, y, w, h, tail, text, size, align, bold, italic}`;
`tail` is `[tx, ty]` page-px or `None`; `style` ∈ `speech | thought |
shout | caption`.

**Rendering (pixel-crisp, zero AA, white fill + 2 px black rim):**

* *speech*: filled black ellipse spans over the bubble rect, then white
  ellipse spans inset 2 px → a ring. Tail: a triangle from the ellipse edge
  to `tail`, base width ~⅕ of the bubble's smaller dimension: fill the
  triangle white FIRST over the ring (opens the bubble into the tail), then
  stamp its two outer edges black with a 2 px brush along Bresenham lines.
* *thought*: the same ring, no attached tail; instead 2 shrinking ellipse
  rings (≈10×7 then 6×4 px) stepped from the bubble toward `tail`.
* *shout*: a 14-spike starburst polygon fitted to the rect (alternating
  outer radius = rect half-extents, inner = 0.72×), white scanline
  polygon fill + 2 px black stamped edges; straight tail like speech.
* *caption*: plain rectangle, white fill, 2 px black rect ring, **square
  corners** (letterpress, not glass), no tail.

**Text**: family **"Komika Hand"** (ships with this app —
`usr/share/fonts/notebookos/KomikaHand-*.ttf`; Bold/Italic/BoldItalic are
real faces, so Pango weight/style selects them). Laid out with PangoCairo
onto the object raster with **font options ANTIALIAS_NONE + HINT_FULL** set
on the target context and **absolute pixel size**
(`FontDescription.set_absolute_size(size * Pango.SCALE)` — a bubble's
"13 px" means 13 page pixels at every zoom and on paper, where it prints at
9.4 pt). Wrap WORD_CHAR to the bubble's text box: the inscribed rect for
ellipse styles (0.72 × w/h, centred), the rect minus 8 px padding for
caption/shout. Alignment: centre for speech/thought/shout, left for
caption. Sizes on a ramp **10 · 13 · 16 · 20 · 26** (the dock's BUBBLE
group: style choices drawn as marks with names, the size ramp, B and I
toggles). Komika covers Western-European Latin only; other scripts fall
back per character through Pango to the shipped faces and still rasterise
crisp — correct behaviour, note it in the module docstring.

**Auto-height**: text wraps to the bubble's width; if it needs more height,
the bubble GROWS downward to fit (part of the same undo frame). It never
shrinks below the user's dragged height. So lettering never overflows or
clips — the bubble is always big enough for its words, which is the one
thing a lettering tool must guarantee.

**Editing**: Bubble tool click on empty canvas = place a new bubble
(default 180×90 speech at the click, tail 45 px below-left, empty text) and
open the editor. Click on an existing bubble (either tool) = select;
Bubble-tool click or Select-tool double-click = open the editor. The editor
is an overlay card (the app's one prompt idiom, §10): a 4-line TextView
styled with Komika, the style/size/B/I controls mirrored, **live preview**
— every change re-rasterises that bubble on the canvas as you type (cheap:
one object redraw). State-dict pattern; Apply commits ONE undo frame
("Undo Edit Bubble"); Cancel restores the pre-open object exactly. Keys in
the card: Esc = cancel, Ctrl+Enter = apply (plain Enter is a newline —
bubbles have line breaks).

**Arrange**: bubbles render in list order; Page ▸ Bring Bubble Forward /
Send Bubble Backward act on the selected bubble (disabled otherwise —
rule 5 of MENU-CONVENTIONS).

Object rasterisation is cached per page in one ARGB32 overlay surface,
invalidated by any object edit; a repaint composites layers + overlay and
never re-rasterises text per frame.

---

## 7. The Pages strip

Right column, top. A vertical scrolling list of page cards: **thumbnail
(96×148, BILINEAR — chrome, not artwork), page number, and the positional
label** for the four cover rows. Active page's card carries the selected
style (papertone active row, same idiom as the Layers rows).

Header buttons (nbicons, tooltips): **Add** (after current), **Duplicate**
(current, deep copy), **Delete** (disabled at 4 pages), **Move up / Move
down**. All undoable, no confirms; delete's frame holds the full page
(bytes) so undo restores it exactly, and the frame is named ("Undo Delete
Page"). Click a card to switch (PgUp/PgDn from the keyboard). Add/
duplicate/delete/move keep the SAME operations in the Page menu (rule 5:
visible, greyed when impossible).

Footer, one quiet line, only when `count % 4 != 0`:
**"Prints as %d sheets. %d pages print blank before the back cover."**
— the zine arithmetic, said where the pages are managed (see §8 padding).

Thumbnails refresh on a 600 ms debounce after any commit to their page;
page switch and reorder refresh immediately.

---

## 8. Files, print, and the budget option

Comics is a **document app** (MENU-CONVENTIONS File menu A) with Novel's
session recovery underneath.

* **Session recovery**: the full document autosaves (2.5 s debounce after
  any commit, plus close-time flush) to
  `$NB_HOME/.config/notebook/comics.json`. Novel's exact close-guard law:
  refuse to close while the last save failed, offer Save / Discard /
  Cancel; `_store_read_only` when the recovery store was damaged/foreign.
* **Documents**: File ▸ New / Open… / Save / Save As… against
  **`$NB_HOME/Documents/*.comic`** via `nbpicker` (patterns `("*.comic",)`,
  default_ext `.comic`). Format:

  ```json
  {"format": 1, "app": "comics",
   "pages": [{"layers": [{"name": "...", "visible": true, "opacity": 100,
                          "png": "<base64 PNG>"}],
              "panels": [{"x":0,"y":0,"w":0,"h":0,"border":3}],
              "bubbles": [{"style":"speech","x":0,"y":0,"w":0,"h":0,
                           "tail":[0,0],"text":"","size":13,
                           "align":"c","bold":false,"italic":false}],
              "mask_gutters": false}]}
  ```

  Unknown keys at every level ride along (`_extra` law). A page whose PNG
  fails to decode loads as a blank layer AND the load reports it (never
  silently); the store itself is never rewritten by the failed read.
* **Recovery-vs-document precedence** on launch: restore the recovery
  store if present, else a fresh 8-page document. `doc_path` binds after
  Open/Save As exactly as Novel's.
* **Registration**: `finder.APP_MODULES["Comics"] = "comics"`,
  `APP_KIND["Comics"] = "Cartooning"`, `FILE_APPS[".comic"] = "comics"`,
  `"comics"` added to `FILE_OPENERS`, and `comics.py` accepts a document
  path as `sys.argv[1]`. `root/Applications/Comics.app` stub (the 2-line
  sh file, mode 755 — the overlay-644 trap). nbicons gains a **"comics"**
  glyph: a page outline divided into panels (e.g. outer R + one horizontal
  rule + one vertical rule in the top half) — visually distinct from
  "novel" (open book), "writer" (sheet), "illustrator" (easel A-frame);
  `icon_uniqueness_selftest` must stay green.

### Export to PDF… (sequential pages, for sharing)
Overlay card: filename entry (prefilled from the bound document's basename
or "comic"), and a check **"Inside pages in black and white"** (off).
Writes to `$NB_HOME/Documents/<name>.pdf` via `nbprint.simple_pdf(path,
count, draw_page, PAGE_W, PAGE_H)`. Existing file → the OS's standard
Replace confirm (Novel's exact three catalog keys). Success flashes
"Exported %H:%M" in the chip; failures give one calm sentence (error-path
law), never an exception repr.

### Zine Print… (the point of the app)
Overlay card, then `nbprint.print_booklet`:

* **Sheets** choice: **Everything · Cover sheet · Inside sheets** (three
  radio rows). Budget production runs the cover sheet separately on card
  stock and the insides on plain paper — this choice is that workflow.
* **"Inside pages in black and white"** check (off). Note line under it:
  "The cover sheet and its inside faces print in colour." (function-only
  voice, under the prose-in-ui length rule).
* Page math line: the §7 sentence when padding applies.

**Imposition**: pad the logical page list to a multiple of 4 by inserting
blanks **immediately before the back cover** — the back cover must land on
the final slot, so Novel's pad-at-the-end behaviour is wrong here and
`booklet_pdf` is only usable via a remap. Implement
`_page_order(n) -> [ids]` (document pages 1..n → padded logical list with
`None` blanks before the last element) and impose with a local
`_impose(path, order, sheets_filter, fold_line)` that mirrors
`nbprint.booklet_pdf`'s placement (2-up 792×612, front/back per sheet,
`fold_line` hairline on sheet 1 front only). The selftest pins the sheet
order against `nbprint._booklet_order` for n = 4..32 — the local copy may
never drift from the OS's imposition.

* Sheet filter: Everything = all; Cover sheet = sheet 1 only; Inside
  sheets = 2..S. (With ≤ 4 pages there are no inside sheets — that radio
  disables, with its reason in its tooltip.)
* **Cover sheet stays colour**: under the B/W option, pages
  {1, 2, M-1, M} of the padded order (M = padded count — exactly the four
  faces of physical sheet 1) render in colour; every other page renders
  desaturated.

**Black-and-white**: desaturate a flatten with
`OPERATOR_HSL_SATURATION` (paint a 0-saturation source over the page rect)
— native, one op; guard with `hasattr(cairo, "OPERATOR_HSL_SATURATION")`
and fall back to a per-pixel luma loop (print-time only, so a loop is
acceptable). **View ▸ Black-and-White Inside** (`✓`, session-only) applies
the same treatment live to interior pages on the canvas — draw the flatten,
then one desaturating wash — so the artist sees exactly what budget
production will print. When the operator is unavailable the menu item is
absent (never a control that does nothing).

`draw_page(cr, page_no, w, h)` (both routes): opaque white ground; look up
the padded order; blanks draw nothing further; otherwise
`set_source_surface(flatten, 0, 0)` scaled ×0.72 with **FILTER_NEAREST**
(pixels stay square at the RIP; cairo marks the image not-interpolated) and
`paint()`. Flattens are built per page on demand and memoised for the run.

### Copy Page as Image
Edit ▸ Copy Page as Image — the flatten (white matte) to the clipboard via
the PixbufLoader route (Illustrator's `_flatten_pixbuf` note: never
`Gdk.pixbuf_get_from_surface` on this build).

### Place Image…
Page ▸ Place Image… — open a PNG via nbpicker (Pictures), draw it onto the
ACTIVE LAYER at 1:1 (OPERATOR_OVER, so transparency composites), centred,
clipped to the page; an image wider/taller than the page scales DOWN to fit
with FILTER_NEAREST. One undo frame ("Undo Place Image"). This is the
bridge from Illustrator (draw a cover there, place it here) and from any
scanned art in Pictures.

---

## 9. Undo

Illustrator's frame machinery, generalised; `StackHistory` adapter so
`nbapp.undo_menu_items` words the Edit menu ("Undo Delete Page").
`UNDO_DEPTH = 200`, `HISTORY_BYTES = 96 MB` shared across frame kinds.

* **Pixel frames**: page index + layer index + touched rect + before bytes
  (Illustrator's `_begin_edit`/`_commit_edit` shape).
* **Object frames**: page index + deep-copied panels+bubbles lists (plain
  data, cheap). One frame per gesture (a move is one frame from
  press→release, not per motion).
* **Structure frames**: page add/delete/move/duplicate, layer ops, panel
  layout, place-image — whatever page data they displace rides in the
  frame (encoded bytes are fine).

A frame records its page; **applying a frame for a non-active page
switches to that page first**, so what changed is on screen the moment it
changes. Undo names come from the catalog (translated at display time, the
StackHistory `_top` rule).

Every destructive operation in this app — delete page/layer/panel/bubble,
clear layer, panel-layout replace, place-image overdraw, New/Open document
replacing unsaved work — is either undoable (all the in-document ones) or
close-guarded (the document-level ones, Novel's guard). **No destructive
confirms anywhere else** (the confirm/undo law), and every accelerator
checks the same enablement its menu item does (the disabled-action law:
Delete with nothing selected does nothing, at 4 pages Delete Page's key
path refuses too).

---

## 10. Chrome, prompts, CSS

* One overlay-prompt idiom, copied from Illustrator (`_overlay_prompt`,
  `_saveprompt_layer`, Esc closes): used by the close guard, Export,
  Zine Print, Panel Layout, the bubble editor. **Every input mirrors into
  a state dict** — the canvas-size lesson is a standing rule here.
* CSS: one `b"""..."""` sheet, ASCII ONLY (the em-dash trap), APPLICATION
  priority, design tokens only (papertone `#FCFBF8`, ink `#1A1916`,
  secondary `#6E695E`, muted `#9A9484`, hairline `#C9C4B6`, field
  `#DED4C2`, wash `#EAE3D2`, signage red `#C8341E` for selection/handles
  and the unsaved dot, the green `#7FA98C` saved dot). Radius 0
  everywhere. No new colours, no new font sizes outside the type scale;
  `button label { color: inherit }` exists in the theme — do not restate
  per-button label colours.
* Save chip: Novel's semantics (the chip reports the RECOVERY autosave —
  "Saved %H:%M" green / "Unsaved changes" red / transient flashes for
  export+errors), because recovery is what makes work durable here;
  File-level state lives in the title ("Comics — name.comic" once bound,
  via the base window title, Novel's pattern).
* The window opens at the OS default size; first fit-to-window zoom runs
  once on allocate (Illustrator's `_fit_src` idle pattern, with the same
  `_closed`/source-cancel teardown discipline on destroy — every timer and
  idle this app arms is cancelled in `_on_destroy`, and re-entrancy guards
  ride the handlers the way the re-entrancy sweep left novel/sequencer).

## 11. i18n

* Everything user-visible goes through `_t()` / the nbi18n auto-walk.
  **Reuse existing catalog keys verbatim wherever the English already
  exists** (the Illustrator tool names/hints, layer panel, zoom items,
  "Saved %s"/"Unsaved changes", the Replace-file trio, "%s px", "%d px",
  palette vocabulary, File-menu items). New keys only where Comics says
  something no app has said; keep them enumerable (they ship as i18n
  fragment 059-comics; the catalogs themselves are not touched by this
  work).
* No widget-text readback ever decides behaviour (`get_active_text` ban —
  indexes only); no message-text matching (exception classes); "%d
  thing(s)" plural hacks banned — two sentences or a count-after-noun
  form; no string starts with a placeholder.
* RTL: layout mirrors free via nbapp; the canvas is artwork (LTR content);
  `_dims`-style LTR isolation for every "W × H" readout.

## 12. The selftest (tools/comics_selftest.py)

Headless-first (no display needed for the model/raster checks; GTK checks
guarded and honestly SKIP-named without one; fonts via the ABSOLUTE
`tools/guest-fonts.conf` so Komika resolves from the overlay). Families:

1. **Geometry**: painted pixel == `px4(colour)` bytes; brush footprint
   counts; ellipse/rect ring thickness exactly 2 px; polygon fill vs edge
   agreement on the starburst; panel ring bytes.
2. **Bubble raster**: with Komika resolved, a lettered bubble's pixels are
   ONLY paper-white/black/untouched — **no grey anywhere** proves
   ANTIALIAS_NONE end to end. This check FAILS (not skips) if Komika is
   absent: the font ships in-tree, so absence is a build defect.
3. **Auto-height**: text that needs more lines grows the bubble; growth is
   undone with the same frame.
4. **Imposition**: `_page_order` pads before the back cover for every
   count 4..32; local sheet order == `nbprint._booklet_order`; cover-sheet
   set == the four faces of sheet 1; subset PDFs hold exactly the chosen
   sheets' sides; B/W flatten of an interior page has R==G==B at every
   pixel while the cover keeps a colour pixel; the 0.72 scale places a
   known pixel at the right PDF coordinate.
5. **Store law**: damaged/zero-byte/wrong-shape recovery stores → aside +
   read-only session + never rewritten (drive open+close); `_extra`
   round-trips; Save As on a read-only dir keeps the old binding; a
   damaged `.comic` chosen via Open is reported and not rewritten.
6. **Undo law**: every destructive op named in §9 restores byte-identical
   page state; the disabled-action law on Delete accelerators.
7. **Dialog-driven**: the real Panel Layout overlay and bubble editor are
   driven through their actual widgets (the canvas-size lesson: a test
   that bypasses a dialog cannot see a dialog bug).
8. **Limits**: page min/max, layer cap, bubble min size, nudge bounds.

Red-proof discipline: the suite must be shown able to fail — sabotage per
family during development (the gate-blind-spot law), and the imposition
check in particular must go red if `_page_order` pads at the end.

## 13. What is deliberately NOT in v1

Non-rect panels · bubble colour styling · per-document page density ·
multi-select · art-over-bubbles · onion skin · spread (2-page) editing ·
SVG/vector export · CMYK. Each is a coherent later feature; none blocks
the workflow the app exists for.
