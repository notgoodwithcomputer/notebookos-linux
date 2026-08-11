# ANIMATION — the Notebook OS 2D animation studio (normative spec)

The two workflows this app exists for, named by their benchmarks:

**A. The Dr. Katz bar** — a *broadcastable limited-animation dialogue show*.
The recording comes first; the drawings hold, boil and flap around it. Two
characters in two rooms, cut back and forth; a static pose that stays alive
because its outline wobbles; mouths that open when the voice does; a title
card; a real 1920×1080 file with the mixed audio in it. Very pixely, and
watchable on a television.

**B. The Stauber / PilotRedSun / cboyardee bar** — the *lo-fi pixel short*.
A crunchy low-resolution canvas, a handful of colours with checker-dither
shading, timing that is deliberately uneven (a 2-frame jolt, then a 30-frame
stare), cuts on the beat of a song, a loop that reads as a GIF and as a video.

Both bars are limited animation. Neither contains a single tween. That is the
design opening: the app that serves them is an **exposure sheet**, not a
keyframe interpolator — and an exposure sheet is a visible grid, which is the
most Notebook OS object imaginable (PAPER-PHYSICS Article E: the structure is
visible).

Functionally the app is the merger of three shipped mechanisms:
**Illustrator's byte-exact pixel engine** (the drawing), **Sequencer's sound
machinery** (the arecord pump and the appsrc playback pipeline), and **Video
Editor's ffmpeg export path** (the encoder probe and the Videos contract).

Everything below is decided. Where this spec names a shipped app's mechanism,
copy that mechanism's *behaviour and rules* (with an attribution comment), not
a private import — `de/animation.py` must not import `illustrator`, `sequencer`
or `video`; the shared code it may import is the `nb*` family, exactly as
every other app does.

---

## 0. The four laws

1. **The artwork is a byte buffer, never a cairo path** (Illustrator's law).
   Every mark is written into premultiplied ARGB32 by run/span/point routines;
   a painted pixel is either exactly the colour or exactly untouched.
   `set_antialias(ANTIALIAS_NONE)` on every context that presents artwork.
   Copy Illustrator's `px4`/`CLEAR4`, `brush_runs`, `_line_points`,
   `_ellipse_spans`, `_ellipse_outline`, `_snap45`, `_square`, the
   scratch-preview scheme, the stamp/span writers and the Mirror group
   (`symx`/`symy` — characters ARE sprites, so unlike Comics the symmetry
   tools stay), with a comment naming illustrator.py as the source of truth.

2. **The sheet is the only motion.** Render time never invents movement.
   Every frame of output is the composite of the exposures stored on the
   sheet for that frame — nothing else. Every assist in this app (wobble
   takes, mouths from loudness, slides) **writes plain exposures** the user
   can read, edit and delete afterwards; none of them leaves behind a live
   object that animates at render time. There is no tweening anywhere in
   this app. This is not a deferred feature; it is the design.

3. **The recording is the spine.** Sound rows are anchored to scene time.
   Inserting or removing frames ripples *drawings*; it never moves a sound.
   Drawings conform to the take, never the reverse — that is how a
   dialogue show is actually made, and the app enforces it structurally.

4. **The store laws** (the campaign's data-safety law, as landed in
   novel.py/journal.py and restated in COMICS-SPEC §0.3): damaged store →
   `nbapp.preserve_damaged` aside, never overwritten; wrong-shape →
   `nbapp.quarantine_unrecognized`, blank start, and the session says so;
   `_extra` unknown keys survive a save; every save is
   `nbapp.atomic_write_json`; failures surface `nbapp.save_failure_reason`
   in the chip; Save As adopts its destination only after bytes landed.
   **Loading is never all-or-nothing**: one undecodable drawing or one
   missing sound file costs exactly that item, reported, never the project.

---

## 1. The document model

A project is ONE JSON store (the Comics/Novel shape, not a folder):

```json
{"format": 1, "app": "animation",
 "canvas": [320, 240], "fps": 12, "boil_every": 2,
 "palette": ["#1A1916", "..."],
 "palette_only": false,
 "cels": [{"id": 7, "name": "...",
           "takes": ["<base64 PNG>", "..."]}],
 "scenes": [{"name": "...", "length": 96,
             "layers": [{"name": "...", "visible": true,
                         "mouth_slots": null,
                         "runs": [{"cel": 7, "start": 0, "len": 24,
                                   "dx": 0, "dy": 0, "take": 0}]}],
             "sounds": [{"path": "...", "start": 0,
                         "in_smp": 0, "out_smp": 0, "mute": false,
                         "peaks": "<base64>", "sig": [123, 456]},
                        null],
             "markers": [{"frame": 0, "text": "..."}]}]}
```

* **Canvas** is chosen at New… from seven presets and never changes:
  **160×120 · 240×240 · 320×180 · 320×240 (default) · 480×270 · 640×360 ·
  640×480**. One project, one grid (Comics' one-clean-number law). All
  presets are even in both axes (yuv420p safety, §9).
* **fps** ∈ {6, 8, 10, 12, 15, 24}, default **12**, fixed at creation.
  The master sample rate is **48000 Hz** because every supported rate
  divides it exactly — samples per frame: 6→8000, 8→6000, 10→4800,
  12→4000, 15→3200, 24→2000. Every sound offset in this app is an
  integer number of samples; there is no rounding anywhere in the
  audio path. This table is a module constant (`SPF[fps]`) and a selftest.
* **Cels** live in a project-wide library. A cel is a canvas-sized ARGB32
  buffer with **1..5 takes** (§6). Cap **768 cels**.
* **Scenes** are the shots. Cap **64 scenes**, **4800 frames** per scene,
  **43,200 frames** per project (30 min at 24 fps, 60 at 12). A scene has
  **1..6 layers** (z-order bottom→top), **2 sound rows**, and markers.
* **Runs** are the exposure sheet: per layer, an ordered list of
  non-overlapping `(cel, start, len, dx, dy, take)` spans. `take = 0`
  means *boil* (cycle the cel's takes, §6); `take = n>0` pins take n.
  `dx/dy` slide the whole run as a unit (integer px). Empty frames are
  simply uncovered. The invariants — sorted, disjoint, inside the scene —
  hold after every operation and are property-tested.
* **Sounds** reference files by path (video.py's model — the project does
  not embed audio bytes). `start` is a frame; `in_smp`/`out_smp` trim the
  source in samples; `sig` is `[size, mtime]` for peak-cache invalidation;
  `peaks` is the rendered min/max envelope. A missing/changed file loads
  as a hatched silent row, reported in the banner, and **the store is not
  rewritten by the failed read**.
* At every cap the creating control disables with its reason in the
  tooltip (disabled-never-absent); nothing truncates silently.

### Memory model

Only the ACTIVE scene's exposed cels (plus a ping-pong of the previously
active scene — Comics §1's exact scheme) hold decoded `cairo.ImageSurface`
takes; every other cel holds its takes as PNG bytes, which is also the save
encoding, so serialising a project re-encodes only cels whose pixels changed
since their bytes were last made. A 320×240 ARGB32 take is 300 KB decoded;
a working set of two dialogue setups is a few MB. Composited *frames* are a
separate LRU (§8). Undo frames own their own byte copies (§11).

---

## 2. Window layout

```
┌ menubar ──────────────────────────────────────────────────────────┐
├ dock 240 ──┬─ canvas mat (expands) ──────────────┬─ right col 240 ┤
│ TOOLS      │                                     │ CELS library   │
│ SIZE/SHAPE │        pixel canvas                 │ (expands)      │
│ MIRROR     │        integer zoom, onion,         │────────────────│
│ PATTERN    │        brush footprint              │ LAYERS panel   │
│ COLOUR     │                                     │ (fixed)        │
│ PALETTE    │                                     │                │
├────────────┴─────────────────────────────────────┴────────────────┤
│ SCENES strip ▸ [sc 1][sc 2][sc 3]      ⏵ ⟲  0:07+03   Stamp Mouths│
│ ruler ──────────────────────────────────────────────── markers ── │
│ layer 6..1 exposure rows (≤6 × 22 px)                             │
│ sound rows (2 × 22 px, waveform)                                  │
└ status bar ───────────────────────────────────────────────────────┘
```

* **Dock**: 240 px, the grid_check RAIL, in a ScrolledWindow
  NEVER/AUTOMATIC (the Illustrator CJK lesson). Groups top to bottom:
  TOOLS → BRUSH SIZE / SHAPES → MIRROR (Illustrator's, kept) →
  PATTERN (§3) → COLOUR (Illustrator's 112-swatch palette, verbatim keys) →
  PALETTE (§4). Groups dim when the active tool cannot use them; never
  reflow.
* **Canvas mat**: Illustrator's mat verbatim — papertone field, centred
  `canvasframe`, integer zoom with FILTER_NEAREST ≥ 1, pixel grid from 8×
  (G), brush-footprint outline, scratch-surface previews, `ZOOM_STEPS` and
  fit-on-first-allocate. Onion skin and the playhead frame are drawn here.
* **Right column**: 240 px. CELS library on top, expanding — one row per
  cel (thumb, name, take count), the active cel's row selected; under the
  list the TAKES strip for the active cel (§6). LAYERS panel below, fixed —
  Illustrator's rows (eye / name), raise/lower/new/delete, cap 6, a small
  mouth glyph on a layer with mouth slots.
* **Timeline pane** (bottom, ~244 px): the SCENES strip (horizontal cards:
  name, length, thumb; click switches, drag reorders; PgUp/PgDn) with the
  transport on its right (Play ⏵ Space, Loop ⟲, `m:ss+ff` readout,
  LTR-isolated; the Stamp Mouths toggle, §7). Below: the frame ruler with
  markers, six layer rows, two sound rows. Frame columns zoom 3/6/12/24 px
  via a stepper in the ruler's right corner. Runs render as bars carrying
  the cel name with classic ditto marks through held frames; boiling runs
  carry a small tilde; the sheet scrolls horizontally as one unit.
* **Status bar**: left = active tool's hint (Illustrator's catalog keys);
  centre = cursor "x, y" in canvas px, or the live W × H of a dragging
  shape (LTR-isolated `_dims`); right = "Scene %d of %d" → zoom stepper →
  save chip.

The whole window lays out at **1024×722 in every language**
(`minsize_sweep --one animation`) — three fixed columns plus scrolling
interiors guarantee it by construction. The timeline is **content, not
chrome: it stays LTR in every locale**, exactly like the canvas and
Sequencer's timeline; RTL mirrors the chrome around it (nbapp).

Both the canvas and the timeline are `Gtk.DrawingArea`s: focusable, they
draw their own focus ring, take space/Enter activation, and carry accessible
names (Constitution VII §1–2).

---

## 3. Tools

Illustrator's eight, verbatim — same keys, same hints, same catalog keys,
same dock grid: **Select V · Pencil P · Brush B · Eraser E · Fill F ·
Line L · Rectangle R · Ellipse O · Eyedropper I** (eyedropper returns to
the previous tool after one pick; Shift constrains as in Illustrator; the
Mirror group applies to the drawing tools exactly as in Illustrator).

What differs is what they draw ON — the exposure sheet:

* **Drawing lands on the active layer's cel at the playhead.** If that
  frame is uncovered on that layer, the first stroke **creates a new cel
  and exposes it** from the playhead to the next exposure on that layer
  (or the scene end) — the flipbook gesture: step, draw, step, draw. If
  the frame is inside a run, the stroke edits that run's cel — its ACTIVE
  TAKE (§6) — and therefore every frame that shows it. The status hint
  says which ("Drawing on 'katz pose' — 24 frames show this." /
  "Drawing a new cel."), because hold-vs-new is the one thing a flipbook
  user must always know.
* **N — New Drawing**: split the hold at the playhead and expose a fresh
  empty cel from here. **D — Duplicate Drawing**: the same, but the new
  cel starts as a copy of the current one (the pose-to-pose gesture).
  Both are Timeline-menu items with their keys printed.
* **Select V** on the canvas picks the topmost visible layer with an
  opaque pixel under the click, selects that layer's run at the playhead,
  and drags it — writing the run's `dx/dy`. Arrows nudge 1 px, Shift 10.
  A run slides as a unit; sliding is not animation (law 2) — for movement
  across time, stamp more exposures (or §5's Slide).

**PATTERN** (Animation's one addition to the drawing engine): a 3-row dock
group applying a fixed pixel mask to Pencil, Brush and Fill —
**Solid** (all px) · **Checker** (`(x+y)&1==0`, 50%) · **Sparse**
(`x&1==0 and y&1==0`, 25%). Masks are applied inside the span/stamp
writers in canvas coordinates, so dither is stable under redraw and
byte-exact testable. Shape outlines and the Eraser are always Solid.
This is the PilotRedSun shading kit: three honest halftones, no opacity.

Keyboard beyond tools: `[` `]` brush size, `+`/`-`/`0` zoom (and
Ctrl+Plus/Minus/0 as menu accelerators), `G` grid, **`,` / `.` step one
frame** (always, regardless of focus), Shift+`,`/`.` step one second,
Home/End scene start/end, PgUp/PgDn previous/next scene, Space play/stop,
`M` marker at playhead (card to name it), Delete = clear selected
exposure(s) / remove selected sound (undoable), Esc deselects / cancels
the open prompt — **Esc NEVER deletes** (OS law; with nothing open it
closes the app through the ordinary close guard).

---

## 4. Colour and the project palette

Illustrator's palette, verbatim: `_HUES/_VALUES/_MUTED/_NEUTRALS/_STAPLES`,
112 swatches, composed hover names (existing catalog keys), active chip +
name, Mix Colour…, Recent row persisted in the app's config.

**PALETTE** (dock group, below COLOUR) is the genre-bar constraint kit:

* A row of up to **16 chips**, starting empty. "Add current colour" appends
  the active colour; selecting a chip makes it the active colour; a small
  remove button drops the selected chip.
* **"Draw with palette only"** check: while on, every colour the tools
  receive (including the eyedropper's pick and Mix Colour…'s result) snaps
  to the nearest chip (RGB distance). The dock's 112 swatches stay visible
  and snap too — constraint, not concealment.
* **Cel ▸ Recolor Drawing to Palette** quantises the active cel (all
  takes) to the chips — one undo frame. This is how found art or an
  Illustrator import joins a 12-colour film.

The palette is stored in the project (it is part of the work, not a
preference). Chips render as pixel squares, radius 0.

---

## 5. The timeline

The exposure sheet is the app's centre of gravity. All operations write
runs; all are undoable; none confirms.

* **Extend Hold** `=` — the run under the playhead grows 1 frame (pushing
  nothing: it consumes uncovered frames, stops at the next run).
  **Shorten Hold** `-`. Both repeat.
* **Split Hold** `/` — the run under the playhead splits at the playhead;
  both halves show the same cel (then D or drawing diverges them).
* **Clear Exposure** Delete — selected run(s) become uncovered frames.
* **Block selection**: click a run selects it; Shift+click extends across
  frames and layers (a rectangle of sheet). **Ctrl+X/C/V** cut/copy/paste
  the block at the playhead on the same layers (paste refuses—with the
  status sentence—if it would overlap or overrun; nothing is half-pasted).
* **Repeat Selection…** Ctrl+R — a card with a count; stamps N copies of
  the block end to end. Cycles are STAMPS, not references (law 2): a walk
  loop is pasted, visible, and each copy is editable. There are no live
  cycle objects.
* **Slide Between Exposures** — with two runs of the SAME cel on one layer
  selected, fills the uncovered frames between them with stepped copies:
  integer `dx/dy` interpolated linearly, one exposure per step, count =
  the gap. A title crawl or a prop drifting across the couch is authored
  in one gesture and lands as plain, editable stamps. (Movement is
  drawings; the assist merely writes them — law 2.)
* **Insert Frames… / Remove Frames…** (cards: a count, applied at the
  playhead) — ripple every layer's runs right/left. **Sounds do not
  move**; the card says so in one quiet line ("Sounds stay where they
  are." — law 3). Remove refuses to swallow a run's last frame silently:
  runs shrink, and a run that would vanish is deleted by name in the undo
  frame.
* **Markers**: `M` at the playhead, text card; drawn as flags on the
  ruler; Ctrl+`.` / Ctrl+`,` jump next/previous. Markers are the beat
  sheet — for bar B they are where the cuts land; for bar A they are the
  retroscript's line starts.
* **Scene Length…** (card, frames entry; shortening that would orphan runs
  or sound starts says what it would cut and refuses until they fit —
  honest, no truncation). **New Scene / Duplicate Scene / Delete Scene /
  Move Scene Left / Move Scene Right / Rename Scene…** on the Scene menu,
  all mirrored as scene-strip card affordances, all undoable, no confirms
  (delete's frame carries the full scene, bytes included).

Playback plays through scenes in order (the strip IS the edit — episode
assembly needs no other app).

---

## 6. Cels, takes and the boil

A cel's **takes** are 1..5 sibling buffers of the same drawing. The TAKES
strip under the library preview shows [1][2][3][+][−]: click selects the
ACTIVE take (what the paused canvas shows and the tools edit), [+] adds a
copy of the active take (≤5), [−] removes (≥1; disabled at 1; undoable).

* **The boil**: a run with `take = 0` cycles its cel's takes — take index
  = `((frame − start) // boil_every) % ntakes`, `boil_every` a project
  constant (default **2**). Deterministic, so scrubbing is stable and a
  selftest can pin any frame. A 12 fps project boils at 6 takes/s — the
  Squigglevision rate. **Choose Take…** on a selected run pins one take
  (a photo background must not wobble).
* **Add Wobble Takes…** (Cel menu, card: Takes **3 · 5**; Strength
  **Subtle 0.7 px · Standard 1.1 px · Rough 1.8 px**): generates takes
  2..N from take 1 by a value-noise displacement — two scalar fields
  (x and y) sampled on a 6 px grid, bilinearly interpolated, amplitude =
  strength, nearest-neighbour remap of the source buffer, alpha carried
  with the pixel. Seeded by `(cel id, take index, strength)`:
  **deterministic**, so the selftest replays it byte-for-byte. Lines
  drift coherently (neighbouring pixels share the field) — the
  hand-retraced look, not fizz. One undo frame; regenerate = run it
  again (same seed, same result; change strength to change the drawing).
* Hand boils remain first-class: draw take 1, [+], redraw lines over the
  copy with the Pencil, repeat — the authentic Dr. Katz method, three
  drawings, and the app simply cycles them.

Deleting a cel is disabled while any run shows it, with the reason in the
tooltip ("Shown in %d scenes."); an unused cel deletes undoably (bytes in
the frame).

---

## 7. Sound

Two rows per scene, drawn as waveform bars under the layer rows.

* **Add Sound…** — nbpicker over Music and Documents
  (`("*.wav","*.mp3","*.ogg","*.flac")`). The clip lands at the playhead
  on the first free row, referenced **by path** (video.py's model). Drag
  moves it (frame snap); dragging its ends trims (`in_smp`/`out_smp`,
  shown snapped to frames, stored in samples); Delete removes; a mute dot
  per row. Rows carry the file's basename.
* **Waveform**: peaks are min/max pairs per column, computed once from a
  one-shot decode — `ffmpeg -i src -f s16le -ac 1 -ar 48000 -` piped and
  reduced as it streams (never held whole), on a worker under the job
  contract (generation token; Constitution V §2). Cached in the store
  with `sig = [size, mtime]`; a changed file re-renders, a missing file
  hatches the row and reports in the banner. A `.wav` decodes through the
  `wave` module when ffmpeg is absent (the host), so selftests see real
  peaks; anything else without ffmpeg says so in the row.
* **Record Sound…** (Sound menu card): input level meter live from the
  capture device (nbaudio's `capture_device`/`unmute` — the
  three-ALSA-controls lesson is already solved there), Record/Stop.
  Capture is Sequencer's **arecord pump**, copied with attribution: raw
  s16 frames on arecord's stdout, a pump thread writing the WAV via the
  `wave` module. Writes `$NB_HOME/Music/<project> take %d.wav`
  (48 kHz mono) and places the reference at the playhead on stop. The
  card states the destination — the file is the user's, discoverable in
  Music, reusable in Sequencer.
* **Playback** is a Python mixer feeding Sequencer's appsrc pipeline
  (`AudioOut`: `appsrc ! audioconvert ! audioresample ! alsasink`,
  Gst touched only at start — never at import, the host-construct law).
  Each pull mixes the scene's live clips for the next block with the
  `array` module (s16 saturating add — audioop is gone in Python 3.13,
  Sequencer's own note). Offsets are exact by the SPF table (§1).
  **Scrub**: dragging the playhead plays each crossed frame's slice
  (one frame = one exact block); `,`/`.` step-plays the same slice.
* **Mouth layers.** **Layer ▸ Mouth Slots…** assigns up to 8 library cels
  to numbered slots on that layer ("Slot 1 plays when quiet." is the
  card's one explanatory line — the closed mouth). The layer row gains
  the mouth glyph, and two workflows light up:
  * **Stamp Mouths** (transport toggle): playback loops the selection
    (or scene); number keys **1..8** stamp that slot's cel at the live
    playhead on the active mouth layer, one-frame exposures replacing as
    passes repeat; `0` stamps slot 1. Lip sync becomes playing a piano
    along with the take. Toggle off, keys return to normal; every stamp
    is an ordinary run (law 2), nudged and re-held like any drawing.
  * **Layer ▸ Mouth from Loudness…** (card): range = selection or scene;
    two sliders, **Quiet 0.10 · Loud 0.45** (thresholds on per-frame RMS,
    normalised to the range's peak, computed from the same s16 decode as
    the peaks); a live preview strip over the waveform shows the slot
    lane it will write. RMS < Quiet → slot 1; < Loud → slot 2; else
    slot 3. Runs shorter than 2 frames merge forward (no chatter).
    Apply writes the exposures — one undo frame ("Undo Mouth from
    Loudness"), then the artist fixes the handful of frames that need a
    real mouth shape. This is the honest 1995 technology, and it is 90%
    of the Dr. Katz look for 0% of the tedium.

---

## 8. Playback and the frame cache

* The engine is a `Gtk.Widget.add_tick_callback` on the canvas (the frame
  clock, `time.monotonic()` — Constitution VI §3; this will be the DE's
  first tick-callback consumer, which is correct: playback is sustained
  motion). When sound is playing, the **audio clock is the master**: the
  frame index derives from samples delivered; if painting falls behind it
  **skips frames and never slides sync** (a late mouth is a broken take;
  a dropped frame is limited animation). Without sound, monotonic × fps.
* **Playback is content, not chrome.** It runs identically under
  `NB_ACCEL=0` — like video.py's playback, it is the app's function, not
  a transition. Chrome (cards, strips, dock) takes the theme's motion per
  nbmotion/PAPER-PHYSICS as amended; the canvas obeys only the sheet.
* **The frame cache**: a composited frame is keyed by the tuple of
  per-layer `(cel, take, dx, dy, version)` actually visible (version = a
  per-cel edit counter). LRU of **64** ARGB32 frames (≤ 20 MB at
  320×240). Holds and boils repeat keys, so a Dr. Katz scene plays from
  cache almost entirely; worst case is ≤ 6 surface paints per frame —
  inside B2 (≤ 50 ms) with an order of magnitude to spare on the software
  path. Composite once per key: paper white → layers bottom→top with
  per-run offset. Onion skin (View: Off / One Drawing / Two Drawings,
  Ctrl+E cycles) draws neighbouring exposures under the current frame at
  paint time — previous in signage red `#C8341E`, next in the green
  `#7FA98C`, both washed to ~25% — chrome-only, never cached, never
  exported.
* The playhead line, run bars, waveforms and markers repaint by
  invalidating only the strips that changed (B5); the transport readout
  updates only when its text changes (B8).

---

## 9. Export

**File ▸ Export Movie…** — one overlay card, then a worker under the job
contract (Preparing… → Working with a real % → Cancelling… → Completed /
an actionable sentence; cancel stops at a frame boundary; teardown removes
the partial file — video.py's `_export_teardown` discipline).

Card rows, all with their honest math printed beside them:

* **Range**: Everything · This scene · Selection.
* **Kind**: **Video** (`.mp4`, into `$NB_HOME/Videos` — video.py's
  contract, including its exact Replace confirm wording) · **GIF**
  (into `$NB_HOME/Pictures`) · **PNG frames**
  (`$NB_HOME/Pictures/<name>/frame-%04d.png`).
* **Size** (video): three radios computed from the canvas —
  "640 × 480 (2×)" · the largest integer scale that fits 1920×1080 ·
  "1920 × 1080 (4× with borders)". Integer scale, then pad; **never
  fractional, never blurred** — `scale=iw*K:ih*K:flags=neighbor` then
  `pad=1920:1080:(ow-iw)/2:(oh-ih)/2`. GIF sizes: 1× · 2× · 3×.
* **Speed** (video): the project rate conforms to broadcast by exact
  duplication — **6, 8, 12, 24 → 24 fps; 10, 15 → 30 fps** (every base
  divides one of the two; the card states "Each drawing shows %d frames.").
  Exact-size exports may instead keep the native rate (a checkbox). GIF
  and PNG frames always keep the native rate; the GIF muxer's 1/100 s
  time base is accumulated per frame by ffmpeg, so 10 and 20 fps are
  exact and other rates diffuse the rounding — one quiet line in the
  card says the actual average rate.

**Video pipeline**: frames render through §8's cache (holds cost nothing),
piped as rawvideo rgb24 into one ffmpeg: neighbour scale → pad → fps
conform → encoder from **video.py's `_video_encoder` probe, copied with
attribution** (libx264 → libopenh264 → mpeg4 + `-pix_fmt yuv420p`; the
probe is cached, the args are not). Audio: every referenced file is an
input; per clip `atrim` (samples) → `adelay=<S>S` at its exact global
sample offset (§1's table; scene offsets included) → `amix` → aac 192k
(video.py's audio args). One process, `-progress` to a scratch file for
the % (video.py's pattern). A range with a **missing sound file disables
Export with the filenames in the reason tooltip** — a mix with silent
holes is not what the person asked for.

**GIF**: two passes, `palettegen=stats_mode=diff` then
`paletteuse=dither=bayer:bayer_scale=2`, `-loop 0` — the bayer crunch is
the genre's native texture and matches PATTERN's checkers.

**Interop is files, not features**: Illustrator art arrives via
**Cel ▸ Place Image…** (PNG via nbpicker from Pictures, onto the active
cel's active take, centred, OPERATOR_OVER, scaled down only if oversized,
FILTER_NEAREST, one undo frame — Comics' rule); a finished frame leaves
via **Edit ▸ Copy Frame as Image** (flatten to clipboard through the
PixbufLoader route — never `Gdk.pixbuf_get_from_surface`, Illustrator's
note) or the PNG-frames export; the movie plays in Media; the takes were
recorded here or in Sequencer; the music came from Composer or anywhere.

---

## 10. Files, recovery, registration

Animation is a **document app** (MENU-CONVENTIONS File menu A) with
Novel's session recovery underneath.

* **File menu**: `New…    Ctrl+N` (the ellipsis is honest: it asks —
  size preset + fps, plus one quiet line "Size and speed are fixed once
  the project starts.") · `Open…    Ctrl+O` · `Save    Ctrl+S` ·
  `Save As…    Ctrl+Shift+S` · `Export Movie…` · `Close    Esc`.
  No Print (an animation does not print; a frame exports).
* **Documents**: `$NB_HOME/Documents/*.anim` via nbpicker
  (patterns `("*.anim",)`, default_ext `.anim`).
* **Session recovery**: the full store autosaves (2.5 s debounce after
  any commit + close-time flush) to
  `$NB_HOME/.config/notebook/animation.json`; Novel's exact close-guard
  law (refuse to close while the last save failed: Save / Discard /
  Cancel), `_store_read_only` on damaged/foreign recovery, recovery
  precedence on launch, `doc_path` binding per Novel. A fresh launch is a
  usable 320×240 @ 12 project with one empty scene — the flipbook is
  live before any card is answered.
* **Store size** stays sane by the caps (§1): a full 30-minute project is
  single-digit MB of sparse PNGs plus peaks; serialisation re-encodes
  only dirty cels (§1's memory model), so the debounced autosave costs
  milliseconds, not the project.
* **Registration** (the Comics checklist, exactly):
  `finder.APP_MODULES["Animation"] = "animation"`,
  `APP_KIND["Animation"] = "Cartooning"` (it shelves beside Comics),
  `FILE_APPS[".anim"] = "animation"`, `"animation"` added to
  `FILE_OPENERS`, `sys.argv[1]` accepted;
  `root/Applications/Animation.app` 2-line stub, **mode 755** (the
  overlay-644 trap); `nbapp.claim_single_instance()`. nbicons gains an
  **"animation"** key mapped to the Lucide icon **`film`** — a filmstrip
  of frames, the app's literal subject; the editor of shot footage
  already owns `clapperboard`, stills own `image`. Mapped in
  `tools/gen_nbicons.py` and regenerated into `de/nbicons_data.py` by
  that script (hand-authored coordinate glyphs are banned — task 061);
  the icon drift check and `icon_uniqueness_selftest` stay green.
* Ships in `finder.HIDDEN_APPS` with its reason string until fragment
  062-animation merges (§16).

---

## 11. Undo

Comics' generalised frame machinery (`StackHistory` adapter over
`nbapp.undo_menu_items`/`undo_keys`), `UNDO_DEPTH = 200`,
`HISTORY_BYTES = 96 MB`:

* **Pixel frames**: cel + take + touched rect + before bytes
  (Illustrator's `_begin_edit`/`_commit_edit` shape). One frame per
  gesture.
* **Take frames**: add/remove/wobble — the cel's take list, bytes
  included.
* **Exposure frames**: the affected layers' run lists, deep-copied plain
  data (cheap — this is why runs are dumb tuples). Stamp Mouths batches
  one frame per loop pass, not per key.
* **Structure frames**: scene add/delete/move/duplicate/length, layer
  ops, sound ops, palette ops — whatever they displace rides in the
  frame.

A frame records its scene; **applying a frame for a non-active scene
switches there first** (Comics' rule — what changed is on screen the
moment it changes). Every destructive operation in the app is undoable —
**no destructive confirms anywhere**; the only guards are document-level
(New/Open/Close over unsaved work → Novel's close guard). Every
accelerator checks the same enablement as its menu item (the
disabled-action law).

---

## 12. Chrome, prompts, CSS, i18n

* One overlay-prompt idiom (Illustrator's `_overlay_prompt` lineage);
  **every input mirrors into a state dict** (the Canvas-Size lesson —
  standing rule for New…, Wobble, Mouth Slots, Mouth from Loudness,
  Repeat, Insert/Remove Frames, Scene Length, Record, Export, the marker
  card). Esc cancels; Ctrl+Enter applies where a TextView is present.
* CSS: one `b"""…"""` sheet, **ASCII only**, APPLICATION priority, design
  tokens only (papertone `#FCFBF8`, ink `#1A1916`, secondary `#6E695E`,
  muted `#9A9484`, hairline `#C9C4B6`, field `#DED4C2`, wash `#EAE3D2`,
  signage red `#C8341E`, green `#7FA98C`), radius 0, no new colours, no
  new font sizes; `button label { color: inherit }` is in the theme — do
  not restate.
* Save chip: Novel's semantics (the chip reports the RECOVERY autosave;
  the title carries the document binding: "Animation — couch.anim").
* Timers/idles all cancelled in `_on_destroy`; every worker completion
  guarded by generation + liveness (Constitution V §2); nothing touches
  Gst or ffmpeg at import (Sequencer's law — construct on the host must
  succeed with neither).
* i18n: everything through `_t()`/the auto-walk; **reuse existing catalog
  keys verbatim** (Illustrator's tool names/hints/palette vocabulary,
  layer panel, zoom items, the save chip strings, the Replace trio,
  "%s px"/"%d px", File items); new keys only where Animation says
  something no app has said (~90 keys), shipped as **i18n fragment
  062-animation**, catalogs untouched by this work. `get_active_text` ban
  (indexes only); no "%d thing(s)"; no string starts with a placeholder;
  timeline strings that compose numbers are LTR-isolated.

---

## 13. The selftest (tools/animation_selftest.py)

Headless-first; GTK checks guarded and honestly SKIP-named without a
display; fonts via the absolute `tools/guest-fonts.conf`. Families:

1. **Geometry parity**: the copied span/stamp writers reproduce
   Illustrator's byte vectors (painted px == `px4`); PATTERN masks paint
   exactly the predicate pixels; mirror writes both halves.
2. **Sheet invariants** (property tests): after any sequence of stamp /
   extend / shorten / split / clear / cut / paste / repeat / slide /
   insert / remove — runs sorted, disjoint, in-bounds; sounds unmoved by
   ripple (law 3); paste refusal leaves the sheet byte-identical.
3. **Flipbook semantics**: draw on uncovered frame → new cel exposed to
   the next run; draw inside a hold → the run's cel changed, run
   untouched; N/D split exactly at the playhead.
4. **Boil determinism**: take index formula pinned for every (fps,
   boil_every, ntakes) in range; pinned takes never cycle; wobble
   generation replays byte-identical from its seed, preserves alpha, and
   displaces no pixel farther than its strength ceiling.
5. **Loudness mouths**: a synthetic WAV built of known silent/quiet/loud
   blocks yields EXACTLY the expected slot runs, including the 2-frame
   merge; **red-proof**: swapping the thresholds must fail it.
6. **Sample exactness**: the SPF table; a 2-second fixture export's mix
   length equals frames × SPF; on the guest, ffprobe (shipped:
   `BR2_PACKAGE_FFMPEG_FFPROBE=y`) pins the .mp4's duration, rate and
   frame size; **red-proof**: an off-by-one conform table must fail it.
   Host without ffmpeg: SKIP by name, never green by silence.
7. **Store law**: damaged / zero-byte / wrong-shape recovery →
   aside + read-only session + never rewritten, driven through a real
   open+close (the fresh-process damage cycle, as Comics red-proved);
   `_extra` round-trips; a damaged cel PNG loads as placeholder +
   report + no rewrite; a missing sound file loads hatched + reported +
   path preserved; Save As on a read-only dir keeps the old binding.
8. **Undo law**: every §11 frame kind restores byte-identical state
   (including cross-scene apply-switches); disabled-action law on Delete
   at caps.
9. **Dialog-driven**: New…, Wobble, Mouth Slots, Mouth from Loudness,
   Export cards driven through their real widgets (the canvas-size
   lesson); Stamp Mouths stamps land on the frames a mocked clock says.

Red-proof discipline throughout: every family is shown able to fail by a
deliberate sabotage during development (the gate-blind-spot law). Whole-OS
gates to run before claiming green: `construct_all_host` / `construct_one`,
`minsize_sweep --one animation` (all 17 languages), merged-fragment
`i18n_check` dress rehearsal (the Comics protocol), `menu_conformance`,
`voice_check`, `css_parse_check` + `ascii_css_check`, `icon_uniqueness`,
`perf_baseline` (register the app), and `data_safety` — whose write-path
detector must be SHOWN to see this app (point one save at a raw
`open(...,'w')` once and watch it go red; the 4th-instance lesson).

---

## 14. Acceptance — the two benchmarks, produced for real

Acceptance is two finished works made in the shipped app, start to finish,
not a simulation of one:

**A. "The couch" (the Dr. Katz bar).** A 3-minute two-character dialogue
scene: session audio recorded via Record Sound… (or in Sequencer and
imported), two alternating shots (two scenes reusing two setups), boiling
characters from Add Wobble Takes over single poses, Mouth from Loudness
plus hand-stamped fixes on both mouth layers, a hand-drawn title-card
scene, markers on the line starts, exported at 1920×1080/24 with the mix —
in one sitting. Feasibility rails behind it: a cap-sized project (43,200
frames, 768 cels) must open, scrub, autosave and export inside the
Constitution's budgets (B2 during scrub; the export may take minutes and
says so with a live %).

**B. "The buttercup bar" (the Stauber/PilotRedSun/cboyardee bar).** A
30-second 160×120 @ 12 loop: palette locked to ≤ 12 chips, checker/sparse
shading, deliberately uneven holds (2-frame hits against 30-frame stares),
cuts placed on beat markers against an imported song, one Slide title
crawl, exported as a bayer-dithered GIF and an .mp4 — and the GIF loops
seamlessly.

Both files play in Media; both projects reopen byte-identical; both go in
the release notes as the app's proof.

---

## 15. What is deliberately NOT in v1

Tweening (**never** — law 2, not a deferral) · vector art · bones/puppets ·
camera pan/zoom keyframes · live cycle references (cycles are stamps) ·
per-clip gain/fades (mix at unity; level the takes in Sequencer) · more
than 2 sound rows · per-layer opacity · palette-swap variants · VHS/grain
export filters · importing video frames · Screenplay import (scene names
from a .screenplay is a coherent later bridge) · collect-audio-copies
into the document · a bundled tutorial project · retiming an existing
project's fps · onion skin over sounds · export presets beyond the card.

Each is a coherent later feature; none blocks the two benchmarks.

---

## 16. Ship notes

* Ships behind `finder.HIDDEN_APPS` until i18n fragment **062-animation**
  (≈90 keys × 17) is validated and merged — the Comics protocol,
  including the merged-catalog `i18n_check` dress rehearsal and the
  fragment-injected minsize pass in the risk languages.
* **One open build decision, flagged not decided**: the guest ffmpeg has
  neither libx264 nor libopenh264 (`BR2_PACKAGE_LIBOPENH264 is not set`;
  no x264 entry), so exports today land on video.py's mpeg4 fallback —
  playable everywhere, larger files, softer at 1080p. `FFMPEG_GPL=y` is
  already set; enabling the x264 package is a one-line .config change
  that upgrades every export path in the OS (Video Editor's too). Worth
  deciding before benchmark A is cut.
* Implementation follows the standing Codex pipeline (task file
  `release/1.0/tasks/062-animation.md`), with the display-reverify law:
  anything GTK-visual re-proven under `tools/guestrun.sh` on a display,
  not in the Codex sandbox.
