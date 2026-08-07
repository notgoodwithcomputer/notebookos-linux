# GBA Development Suite — specification

Written 2026-08-03. Supersedes `GBA-SDK-ROADMAP.md` entirely.

## What this is

**An accessible interface that structures and generates C for everything Game
Boy Advance development requires**, with hand-written C available for whatever
lies beyond the drag-and-drop. Everything the AGB SDK did is covered natively,
behind buttons.

It is **not a game maker with a GBA exporter.** That was the earlier framing and
it is withdrawn. This is a GBA development tool — much closer in shape to ROM
hacking software than to GameMaker: structured editors over a known engine.

**The ambition is to be the apex tool for GBA homebrew.**

## Who it is for — both ends, not a compromise between them

| | |
|---|---|
| **Entry level** | Someone with the taste of **Advance Map** and **YAPE**: see the thing, edit it directly, properties in a panel, no ceremony. They never write code. |
| **Expert** | Someone who does **arbitrary code execution in Pokémon Gen III**. They must feel at home: nothing hidden, addresses and bytes visible, raw ARM/Thumb and inline C available, and every generated artefact inspectable. |

These are not served by two products or by a compromise. They are served by the
layering in Part 0: the same capability at three depths, where the beginner
never sees the depth and the expert is never blocked by the surface.

The two benchmarks in Part I — Pokémon Gen III and a near-1:1 Undertale —
remain the completeness test.

## What carries over, and what is withdrawn

**Carries over:** the workspace shell (multi-window inside one app, Phase 1,
built), the asset browser and its five editors, the events-and-actions sheet as
the entry-level authoring surface, the C code generator, the on-guest
toolchain, and the whole hardware programme in Parts III and VI.

**Withdrawn:** the GML flavour. The scripting language is a curated subset of C
(Part 0), so level 2 and level 3 are one language. Concretely: the
`execute_code` action and its Help must stop saying "GML".

## The design bar

The interface must be **as beautiful as the rest of the OS**, not merely
legible. Writer, Finder and Novel set the standard and this is held to it —
Papertone paper, near-black hairline frames, signage red reserved for alerts,
Nimbus Sans for interface and a serif for reading, drawn glyphs rather than
emoji, every surface explicitly backgrounded.

The difficulty is specific and worth naming: **ROM hacking tools are
traditionally ugly.** Advance Map is a dense Delphi window; YAPE is a wall of
WinForms fields. Their density is a virtue — it is why experts like them — but
their appearance is not. This suite has to carry that density in the OS's own
visual language, which means typography and spacing do the work that borders and
grey panels do in those tools. Dense and graceful, not sparse and pretty.

## Part 0 — The bar, and the principle that makes it reachable

**The bar is a Nintendo licensed AGB development kit**, not a hobby toolchain
and not a game maker with a GBA exporter. What a licensee received was: total
register-level access to the hardware, the official libraries (sound driver,
compression, save drivers, link and multiboot), asset conversion tooling,
cartridge header and checksum compliance, and documentation to a reference
standard. Every one of those is in scope here.

Everything about this machine is public and settled — the registers, the
timings, the BIOS calls, the sound engine, the save chips. There is no
reverse-engineering left to do. **So the suite has no excuse for a gap:
anything the GBA can do, this must be able to author.**

At the same time the audience starts at "has never written a line of code", and
the interface stays in the GameMaker design language — an asset browser, an
events-and-actions sheet, a room you place things in.

### The principle: layered depth, not two products

Those two goals only conflict if depth is a *separate* product from
accessibility. It is not. **Every capability is reachable at three levels, and
they are the same capability, not three feature sets:**

| Level | Who | Form |
|---|---|---|
| 1 — Actions | has never coded | the drag-drop events-and-actions sheet |
| 2 — Script | learning | the scripting language, taught in Help |
| 3 — Direct | expert | engine calls, hardware registers, inline C |

Three rules make this work, and they are binding on every subsystem:

1. **Each level lowers to the one below it.** An action compiles to script,
   script compiles to engine calls, engine calls are registers. Nothing at a
   higher level can do something unreachable from a lower one.
2. **You can always look down.** Any action can show the script it produces,
   and any script the C it produces. This is the single best teaching device in
   the tool: a novice sees what their own game just compiled to, in their own
   terms, without being asked to leave the level they are comfortable at.
3. **You can always drop down, in place.** Mixing an inline-C block into an
   otherwise drag-drop object is normal, not an escape hatch. No feature is
   gated behind "rewrite this properly first".

The consequence for the roadmap: **no subsystem may be designed for one level
only.** A palette manager that only novices can use is as wrong as an HDMA
authoring tool only experts can reach.

### The language at level 2: a curated subset of C

**Decision: the scripting language is C, subsetted and extended — not a
separate language.**

The starting point today is GML-flavoured (`if (…)`, `while (…)`, `repeat (…)`,
`var`, `global.`, `[]` arrays; the action is even labelled "GML"). That is
already "a simplified C-like script", so the question is only which direction
to finish it in.

**Why not adopt an existing scripting language.** The obvious candidates —
PAWN (explicitly "C-like without pointers", built for constrained embedding),
Squirrel, AngelScript, Lua — are all *runtime virtual machines*. Using one means
either shipping its VM on a 16.78 MHz CPU, which Part 0's codegen decision
already rules out, or taking only its syntax and writing our own compiler to C
regardless. **There is no implementation to reuse here, only a specification to
copy.** The saving is a weekend of language design, not the compiler.

**Why C specifically.** Under the three-level model, level 2 and level 3 being
*different languages* is the weak joint: "drop down in place" becomes a language
switch, and the Help has to teach two things. If level 2 is a subset of C then:

* dropping to level 3 is **adding**, not switching — the same file, more of the
  same language
* the Help's "C for GBA" chapter is simultaneously the scripting manual, so
  every page written serves both audiences
* what a beginner learns transfers to real GBA development, which is what a
  licensed-kit-caliber tool owes them
* the generator's output is readable by the person who wrote the input — which
  is what makes Part 0's "you can always look down" rule actually teach

**What is removed** (the parts that hurt beginners, the GBA, or both):
pointers, manual allocation, header files, the preprocessor beyond named
constants, and undefined behaviour wherever it can be defined instead.

**What is added**, because plain C is actively wrong for this machine:

* a native **fixed-point** number type — the ARM7TDMI has no FPU, so `float` is
  a trap rather than a convenience; the language should make the right thing
  the easy thing
* `var` locals with inferred type
* a bounded built-in `string`, no null terminators to get wrong
* arrays that know their own length
* the engine API as ordinary calls, not a library to include

Precedent: **Small-C** (Ron Cain, 1980) is the canonical demonstration that a
useful C subset is small enough for one person to implement, and PAWN is the
canonical demonstration that removing pointers from C-like syntax loses nothing
an embedded scripter needs.

### What is generated, and what is written

The suite is closer in shape to the Gen III ROM-hacking toolchain (Porymap,
AdvanceMap, XSE/Poryscript) than to GameMaker: **structured editors over a
known engine**, not a general engine you assemble. That is a clarification of
what the editors *are*, not a change of audience — the browser, the
events-and-actions sheet and the room editor stay exactly as they are.

It makes one question load-bearing: **what does the tool generate, and what
does a person have to write?**

**Pure data — no code at all, just bytes placed in ROM.** Nobody writes these,
ever: graphics, palettes, tilemaps, collision layers, map headers, connections,
warps, encounter tables, species/move/item tables, dialogue strings, song
sequences and instrument banks, menu layouts, animation frame tables, save
schemas.

**Generated code — a structured editor produces the C.** The author edits a
picture, a grid, a table or an action sheet; the generator writes the source:

| Authored as | Generates |
|---|---|
| events-and-actions sheet | per-object event functions |
| timeline / cutscene editor | coroutine state machines |
| dialogue tree | script dispatch and text calls |
| visual state machine | `switch`/`goto` flow |
| map graph, warps, encounters | tables plus the load/transition code |
| battle move effects | effect table plus generated dispatch |
| save schema | serialisation, checksums, versioned migration |
| **every asset placed anywhere** | **its load, decompress and slot assignment** |

**And all of the memory business, always.** This is the part that must never be
the author's problem unless they ask for it: EWRAM vs IWRAM placement, section
attributes, ARM vs Thumb selection, DMA setup, **VRAM tile allocation, OAM slot
assignment, palette slot assignment**, ROM banking and section layout, and
installing the interrupt handlers the features in use require. The generator
knows what the whole project allocates; a person hand-tracking 128 OAM entries
and sixteen 16-colour palettes does not. It is generated, budgeted and reported
(II.13) — and a level-3 author can override any of it explicitly.

**Written in C — genuinely new logic.** The residue, and it should stay small:

* a per-frame algorithm nobody anticipated (a bespoke bullet pattern, a custom
  physics solver)
* a hardware trick the engine does not template (a particular HDMA effect)
* an inner loop the author wants to hand-tune in IWRAM/ARM
* custom compression, or talking to something the suite does not model

**The rule:** codegen covers everything that is a *configuration of known
patterns*. C is for what is genuinely new. If an author is writing C to place a
sprite, allocate a palette or load a map, the generator has a gap and the gap is
the bug.

Help therefore carries the "C for GBA" chapter (Part 0) **beside** the map,
palette and script tools rather than in a separate expert manual — because the
person who needs it is a person who already built a map and now wants one thing
the actions cannot express.

### What "Nintendo caliber" adds to Part II

Beyond the seventeen subsystems already specified:

* **Total hardware access.** Every register in Part III reachable from level 3,
  named and documented, with nothing the engine refuses to expose.
* **Cartridge compliance.** Header, complement checksum, region and version
  metadata, RTC/EEPROM/Flash declarations, ROM padding and mirroring — the
  things lotcheck inspected. A build must be able to report its own conformance.
* **Conformance guidance in Help**: the standards a licensed title was held to,
  as checkable rules rather than prose — key-repeat and soft-reset conventions,
  the sleep/pause requirement, battery and RTC behaviour, and the flashing-image
  limits that exist for photosensitivity.
* **Reference-grade documentation** (II.16), because a licensed kit shipped a
  manual and this machine has no internet.

---

## Part I — The benchmarks, decomposed

The two are chosen well because they stress almost orthogonal axes. Pokémon is
a *data and systems* problem; Undertale is a *per-frame authoring and
choreography* problem. A suite that does one does not get the other for free.

### I.1 What Pokémon Gen III actually requires

Ruby/Sapphire/Emerald/FireRed are ~16 MB carts. Decomposed into what the
tooling must provide:


|Requirement                                                                        |What it means for the suite                                                                                                          |
|-----------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
|~400 species, ~350 moves, items, abilities, learnsets, evolutions, TM compatibility|A **relational data editor** — tables, typed columns, foreign keys, validation. This is not a game-object feature; it is a database tool.|
|~200 maps with connections, warps, encounter tables, per-map scripts               |A **world editor** above the room editor: map graph, edge connections, warp targets, encounter zones.                                |
|Overworld NPCs with movement scripts, facing, scripted cutscenes                   |A **cutscene/sequence system** with coroutines — "walk here, face left, say this, wait, give item".                                  |
|Branching dialogue, hundreds of thousands of characters of text                    |A **text database** with a variable-width font engine, control codes, string buffers, and word-wrap at author time.                  |
|Nested menus: party, bag, PC storage, shops, summary screens                       |A **UI/menu framework** — stateful, nested, navigable, reusable. Pokémon is at least half menus.                                     |
|Turn-based battle engine, hundreds of move effects                                 |A **state-machine authoring tool** and a scripting language strong enough to express move effects as data + code.                    |
|128 KB flash save, multiple blocks, checksums, rotating sectors                    |A **save architecture** with schema versioning and integrity, not a byte blob.                                                       |
|Real-time clock (berry growth, tides)                                              |**RTC support** in the runtime and a way to simulate it while testing.                                                               |
|Link cable trading and battling                                                    |**SIO** — multiplayer transport, serialisation, and a way to test two instances.                                                     |
|16 MB of compressed graphics                                                       |**LZ77/Huffman/RLE** via BIOS, plus asset banking and a ROM layout tool.                                                             |
|Sequenced music (M4A/Sappy) with instrument banks                                  |A **sequenced audio engine** with sample-based instruments, not just PSG.                                                            |
|Multiple languages                                                                 |**Localisation** — all authored text externalised, per-language builds.                                                              |

### I.2 What a near-1:1 Undertale requires


|Requirement                                                        |What it means for the suite                                                                    |
|-------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
|Bullet-hell battles, often >128 simultaneous projectiles           |The single hardest collision with the hardware. See Part IV.1.                                 |
|Every boss is bespoke — unique mechanics per encounter             |Scripting power approaching a real language, plus per-encounter state machines.                |
|Typewriter dialogue, per-character sound, portraits, multiple fonts|Text engine with per-glyph timing and audio hooks.                                             |
|Sprite rotation, scaling, distortion, palette cycling, screen shake|**Affine sprites/backgrounds**, palette animation, **HDMA raster effects**.                    |
|Alpha blending, additive glow                                      |GBA hardware blending — layer-restricted, see Part IV.2.                                       |
|Meta-narrative persistent state across resets and "deletions"      |Save architecture with out-of-band flags.                                                      |
|Precise non-grid movement and collision                            |Fixed-point physics, sub-pixel positioning, pixel-accurate collision masks.                    |
|A ~2-hour soundtrack with leitmotifs                               |Sequenced music with instrument banks; streaming 2 hours of PCM is not possible. See Part IV.3.|
|Frame-perfect choreography                                         |A **timeline/cutscene editor** with frame-level control.                                       |

- - -
## Part II — The suite

Seventeen subsystems. Each is a substantial piece of software; together they are
the "enormous" the brief asks for.

### II.1 Project & asset management

Folders, search, tags, rename-with-reference-update, dependency graph, orphan
detection, per-asset metadata. Project sizes to plan for: **10,000+ assets, 16 MB
ROM**. Must include asset *banking* (which assets live in which ROM region) and
a visual ROM map.

### II.2 The language

A real language, compiled to C. Required: functions, locals, arrays, **structs**, `
for`/`while`/`switch`/`break`/`continue`/`return`, comments, full operator set
including `%` and bitwise, string type with buffers, fixed-point maths library,
constants and enums, `\#include`\-equivalent (script imports), and **inline C
escape** for the last 1%.

Additionally required by the benchmarks: **coroutines** (`wait`, `wait_until`, `
yield`) — cutscenes and turn-based battles are unwritable without them — and `
with()` blocks over instance sets.

### II.3 Object & entity system

Parenting with virtual events, per-object declared instance variables with
types and defaults, user events, full event set (create, destroy, step ×3,
draw, alarm ×8+, collision, animation end, outside room, key ×N, user ×16),
instance pools, `instance_nearest/find/count`, deactivation regions (essential
for large maps), and depth/layer sorting.

### II.4 World, rooms and tiles

Multi-layer rooms mapped onto the four hardware backgrounds, parallax,
collision layers separate from visual layers, autotiling, **map graph with
connections and warps**, encounter zones, per-map scripts, room creation code,
and large scrolling maps with streaming.

### II.5 Graphics pipeline

Frame/animation editor with onion-skinning and per-frame hitboxes; **palette
manager** (16×16 for 4bpp, 256-colour mode, per-asset assignment, quantisation,
palette animation/cycling); tile deduplication; sprite sheet packing into the
OAM tile budget; affine sprite and background support; mosaic; windowing
(WIN0/WIN1/OBJWIN); blending; and **HDMA raster effect authoring** (per-scanline
scroll, palette, window — this is how Pokémon does water and Undertale does
distortion).

### II.6 Text and fonts

Variable-width font editor; control codes (colour, speed, pause, portrait,
sound, variable substitution); automatic word-wrap at author time; string
buffers; **a text database** with per-language columns; typewriter timing with
per-glyph callbacks.

### II.7 Data tables

The Pokémon requirement, and useful far beyond it. Typed columns, foreign keys
into other tables and into assets, validation rules, CSV import/export,
generated C tables placed in ROM, and generated accessors in the language.

### II.8 UI and menus

A menu framework: nested screens, cursor navigation, scrolling lists, tiled
window frames, transitions, input focus stack, and a visual layout editor.
Non-negotiable for the Pokémon benchmark.

### II.9 Cutscenes, sequences and timelines

A timeline editor with frame-level control over instances, camera, audio, text
and screen effects; branching; and compilation to coroutines from II.2.

### II.10 Battle / state-machine authoring

A visual state machine (states, transitions, guards, entry/exit actions) that
compiles to generated C. Serves both turn-based battle flow and per-boss
bullet-pattern choreography.

### II.11 Audio engine

A **sequenced engine with sample-based instruments** (the M4A/Sappy model):
instrument bank editor, per-channel priority and allocation across 2 PSG square

+ wave + noise + 2 DMA PCM, streaming from ROM, SFX ducking, and sample import
  with automatic rate/loop conversion.

### II.11a The score editor — a MuseScore-class sub-app

Music for these two benchmarks is written, not patched together from patterns.
The suite therefore carries a **notation editor** of its own, at the level of
MuseScore, as a sub-app inside the SDK:

* staves, clefs, key and time signatures, pickup bars, repeats, voltas
* note entry by mouse, by computer keyboard, and by **MIDI keyboard** if one is
  attached over USB
* multiple instruments/parts with a score view and per-part views
* dynamics, articulations, slurs, ties, tuplets, grace notes, tempo and
  expression marks
* transposition, and playback of the score as written
* **MIDI import and export** — the single most valuable feature here, since most
  existing game music exists as MIDI, and it is how a composer moves work in
  and out of a machine with no internet

A tracker/pattern view stays alongside it, because percussion and sound effects
are written that way and notation is the wrong tool for them. Both edit the
same underlying sequence: **notation and tracker are two views of one song**, not
two file formats.

**The reduction problem.** A score may have twenty staves; the GBA has six voices
— two PSG squares, one wave, one noise, and two DMA PCM. The editor must
therefore own **voice allocation**: mapping parts to channels, with priority,
automatic reduction, and a live indication of where the arrangement exceeds
what the hardware will play. Doing this well is most of the work, and it is the
difference between a notation editor that happens to export GBA music and one
that composes *for* the GBA.

### II.12 Save, persistence and hardware peripherals

Save schema with versioning, migration and checksums; drivers for **SRAM, Flash
(64K/128K) and EEPROM (512B/8K)**; RTC; rumble; solar sensor; gyro; multiboot;
and **SIO link** with a serialisation layer.

### II.13 Build, budget, profile, debug

Incremental build; **resource budget report** (ROM, EWRAM, IWRAM, VRAM, OAM
slots, palette slots, per-asset attribution); IWRAM placement control and
ARM/Thumb selection per function; a **cycle profiler**; a debug overlay; error
mapping back to the authored row/line; and deterministic replay for testing.

### II.14 The workspace — many windows in one app

Sixteen other subsystems cannot live behind one maximised pane. The SDK becomes
a **workspace shell**: dockable, splittable, tabbed panes, several open at once,
each hosting an editor or a sub-app (score editor, sprite editor, map editor,
data table, script). Layouts are saveable per project and per task —
"composing", "level building", "debugging".

**This has to be internal, not OS windows.** Notebook OS runs one app at a time
and its window manager keeps the focused window topmost; real child `Gtk.Window`
s would fight it, and panel menus already render behind focused windows for exactly this reason. So the shell implements its own in-app window management — docking, splitting, tabs, and MDI-style floating panes drawn *
inside* the app's own canvas.

This also softens the constraint in IV.5: once the SDK is a workspace rather
than a single editor, the argument for hosting an emulator pane inside it
becomes much stronger, and the edit-test loop stops crossing an app boundary.

### II.15 Link Cable and multiplayer
Serial I/O is a first-class subsystem, not a driver detail. Both benchmarks and
most of the GBA's catalogue depend on it, and it is the hardest thing in the
suite to get right because it is the only place where two machines must agree.

**Hardware modes, all required:**

* **Multi-play** — up to 4 GBAs, 16-bit words, 9600/38400/57600/115200 baud;
  the mode almost every commercial multiplayer game uses
* **Normal (SPI)** — 8/32-bit, master/slave, two units
* **UART** — general serial
* **JOY bus** — GBA-to-GameCube
* **General-purpose I/O** on the same port
* **Multiboot** — send an executable to an unflashed GBA over the cable, which
  is how single-cartridge multiplayer works

**What the suite provides above the hardware:**

* a **session model** — discover, connect, assign player IDs, detect drop-out,
  and recover, all authored as events rather than hand-written protocol
* a **serialisation layer** — declare which variables, instances or data-table
  rows travel, and the generator emits the packing and unpacking; this is what
  makes Pokémon-style trading expressible
* a choice of **synchronisation model**: lockstep (deterministic, every unit
  runs the same simulation from the same inputs — right for battles) or
  state-push (one unit authoritative — right for trading and menus)
* **latency and desync tooling**: a desync detector that checksums simulation
  state per frame and reports the first frame and variable that diverged
* **testing with two emulator panes** wired to a virtual cable, so multiplayer
  is developed and debugged on one machine

**The honest part:** lockstep multiplayer is the hardest correctness problem in
this document. It requires the whole simulation to be deterministic — no
uninitialised reads, no pointer-order iteration, no floating point (which the
hardware forbids anyway). Determinism has to be a property the engine
*guarantees and verifies* (II.13's deterministic replay), or link play will
desync in ways no author can debug.

### II.16 Help and reference

The machine has no internet, so the built-in reference is not supporting
material — it is the only documentation that exists. It must therefore be
complete rather than introductory:

* **every** drag-drop action: parameters, types, ranges, what it compiles to
* **every** engine call in `runtime.h`: signature, units, cost, constraints
* **every** language construct, operator and library function
* **"C for GBA"** — one chapter that is both the scripting manual and the
  introduction to level 3, since they are the same language (see Part 0)
* **every** hardware register and BIOS call the suite exposes, with the GBA
  constraints that apply — VRAM regions, OAM limits, palette layout, DMA timing
* per-subsystem guides: palettes, ROM banking, voice allocation, link sync
* templates (platformer, top-down RPG, shmup, turn-based battler) and worked
  examples that open and build
* searchable, and reachable from the pane already open — a reference that has
  to be hunted for is not consulted

**Language rule, enforced.** Every word describes function. No second person,
no reassurance, no encouragement, no scene-setting. A heading is a noun, an
action is a verb, a description states behaviour, a constraint states a limit
and its consequence. This is the OS-wide rule — see `docs/MENU-CONVENTIONS.md`
and `tools/voice_check.py`, which lints for the failure modes.

- - -
## Part III — Hardware coverage matrix

"Every single aspect of GBA development" made concrete. Status is measured from `
gbaruntime/` today.


|Subsystem                |Today (measured 2026-08-05)|Required                                          |
|-------------------------|---------------------------|--------------------------------------------------|
|BG modes 0/1/2 (tiled)   |Mode 0. Modes 1/2 reachable via `rt_video_mode`, but no affine MAP data is emitted|0, 1, 2 incl. affine|
|BG modes 3/4/5 (bitmap)  |Absent — one unused `MODE3` constant|Full, incl. page flipping                |
|Sprites (OAM)            |**Done** — regular + affine + double-size + OBJWIN (2026-08-07)|Regular + affine + double-size + OBJWIN|
|Palettes                 |Static banks + **cycling** (2026-08-07, 4 slots, BG+OBJ); runtime reassignment still absent|Full 256+256, per-asset assignment, cycling|
|Windowing (WIN0/1/OBJ)   |**Done** (2026-08-07) — and the enable bits now survive the frame loop, which they never did|Full|
|Blending / fade / mosaic |**Done** — alpha, brightness, fade, mosaic|Full                                  |
|Interrupts (IRQ)         |**Done** — `rt_irq_set`, all sources defined|VBlank, HBlank, VCount, Timer, DMA, SIO, Key|
|Timers ×4                |**Done** — start/stop/read       |Full — required for DMA audio                    |
|DMA 0–3                  |**Done** — `rt_dma`, `rt_hdma_start`|All four, incl. HDMA for raster effects       |
|BIOS calls               |LZ77 (WRAM+VRAM), Huffman, RLE, div, sqrt. No halt/stop|+ halt/stop           |
|Sound: PSG ×4            |**Done**                         |Yes                                              |
|Sound: DMA PCM ×2        |**Two voices** (2026-08-07): looping soundtrack on B under one-shots on A, shared timer; no sample-summing mixer yet|Two, timer-driven, with mixer|
|Save: SRAM               |**SRAM + Flash 64K/128K** (2026-08-07); EEPROM absent|SRAM + Flash 64K/128K + EEPROM 512B/8K|
|RTC                      |**Done** — present/read          |Required (Pokémon)                               |
|SIO / link               |**Done** — 11 calls, multiboot image builds|Required (Pokémon)                     |
|Rumble / solar / gyro    |Absent                           |Supported                                        |
|Multiboot                |**Done** — `gba_mb.ld`, built and checked|Supported                                |
|Sleep / power management |Absent                           |Supported                                        |
|Cartridge header / gbafix|**Done**                         |Yes + region/version metadata                    |

**Nine of nineteen rows are done.** The previous version of this table was
written before Movement B and listed interrupts, timers, DMA, BIOS calls, RTC,
SIO and multiboot as absent when all seven now work; it also implied the save
and PCM rows were further along than they are. Measured, not remembered.

The three rows that block the Definition of Done: **save** (the Pokémon test
needs 128 KB flash and this is SRAM only), **PCM** (one channel and no mixer
cannot carry the Undertale test's soundtrack over its sound effects), and
**palettes** (no cycling, no runtime assignment).

- - -
## Part IV — The collisions, and honest answers

These are the places where "near-1:1" and the hardware genuinely fight. Naming
them now prevents promising something the machine cannot do.

### IV.1 Undertale's bullet count vs 128 sprites

Several Undertale attacks put more than 128 projectiles on screen. The GBA has 
**128 OAM entries, hard**. Three techniques, all of which the suite must support
because different attacks need different ones:

1.  **Sprite multiplexing** — rebuild OAM per scanline band via HBlank IRQ.
    Raises the effective count several-fold; costs CPU.
2.  **Bullet fields as tiled background** — render dense uniform patterns into a
    BG layer instead of sprites. Effectively unlimited, but coarse (8×8 grid)
    and costs a background layer.
3.  **Bitmap-mode particle rendering** (mode 4, double-buffered) — arbitrary
    count and shape, but at 16.78 MHz only for modest counts, and it costs the
    tiled layers.

**Honest answer:** a 1:1 port is achievable per attack, with the author choosing
a technique per attack. The suite must expose all three and *report* the cost.
It must not pretend a single renderer covers every case.

### IV.2 Alpha blending

GBA blending is hardware, per-layer, one blend at a time — not per-sprite
alpha. Additive glow is possible via BLDCNT in "brightness increase" mode.
Effects requiring several independent alpha values simultaneously must be
approximated by layer assignment and palette tricks. The suite must make layer
budget visible so authors design within it rather than discovering it late.

### IV.3 The soundtrack

Undertale's original is 44.1 kHz stereo, ~2 hours. Streaming that from ROM is
impossible at any acceptable quality within 32 MB alongside 16 MB of other
assets. **The right answer is the platform's own:** a sequenced engine with
sampled instruments (II.11), which is how every commercial GBA soundtrack was
made, at a fraction of the size. Near-1:1 means *the same music, arranged for
the hardware* — which is a content task the suite must make possible, not a
technical one it can automate.

### IV.3a Twenty staves, six voices

A score written in the notation editor can carry any number of parts; the GBA
plays six — two PSG squares, one wave, one noise, two DMA PCM. Every
arrangement therefore passes through a **reduction**, and the composer has to be
able to see and control it rather than discover it on playback.

The DMA channels change the shape of this: they play *samples*, so a single
channel can carry a drum kit or a sustained pad that the PSG channels cannot.
Practical allocation is closer to "two melodic PSG voices, one bass/wave, one
noise/percussion, and two sampled channels doing the heavy lifting" — which is
how commercial GBA soundtracks were arranged, and why they sound the way they
do. The editor must make that arrangement visible, not hide it.

### IV.4 Pokémon's scale vs the authoring machine

A 16 MB project with 10,000 assets must open, search and build on a modest
laptop running Notebook OS. This forces: lazy asset loading, an on-disk project
format that is not one JSON blob, incremental builds, and a project format
diffable enough to keep under version control.

### IV.5 The edit-test loop — DECIDED
Notebook OS runs one app at a time, so the suite cannot hand a build to the GBA
Emulator app without leaving the editor. At this scale the edit-test loop is the
dominant cost, so:

> **The suite hosts its own emulator pane.** Build and run happen inside the
> workspace, beside the editor that produced them.

Consequences to design for, not discover:

* the emulator core is **embedded**, not launched — a library the pane drives,
  sharing the process and therefore the project state
* build → run is one action with no app switch and no state loss
* the pane exposes **frame step, pause, reset, save state and state restore**,
  because they are what makes a bug reproducible
* the debug overlay (II.13) and the profiler render into this pane
* **two panes can run at once**, which is what makes Link Cable (II.15) testable
  without two consoles

---

## Part V — Architecture

A suite this size collapses without structure. Four rules:

1.  **One intermediate representation.** Drag-drop actions, the visual state
    machine, the timeline editor and the text language all lower to the *same*
    IR, which is what generates C. Anything else means building every feature
    several times and watching them drift.
2.  **The engine is a library, not a framework.** Every runtime capability is
    callable from generated code; nothing is reachable only through the action
    sheet. This is what makes the Undertale bespoke-boss case possible.
3.  **The project format is many files, not one.** Per-asset files, text database
    as tables, so a project is diffable, partially loadable and survivable.
4.  **The shell owns layout; editors own nothing but their own pane.** Every
    editor and sub-app is a pane that can be docked, split, tabbed or floated,
    and knows nothing about where it sits. Anything that assumes it is the
    whole window cannot be composed with the other fifteen.
5.  **Every subsystem reports its budget.** ROM, RAM, VRAM, OAM, palettes,
    cycles. On this hardware a tool that cannot answer "what will not fit and
    why" is not finishable.

- - -
## Part VI — Phases

Seventeen phases. Each ships. Grouped into four movements.

**Movement A — Make it a real programming environment (1–5)**

1.  **The workspace shell**: docking, splitting, tabs, in-app floating panes,
    saved layouts. First, because every later subsystem is a pane inside it and
    retro-fitting a shell under fifteen finished editors is the expensive way
    to get one.
2.  Project structure at scale: multi-file format, folders, search, references,
    undo everywhere.
3.  The language: functions, arrays, structs, control flow, strings,
    fixed-point maths, inline C.
4.  Objects: parenting, declared instance variables, full event set,
    deactivation.
5.  One IR: actions, script and state machines converge; error mapping to
    authored rows.

**Movement B — Make it a real GBA (6–10)** 6. Interrupts, timers, DMA/HDMA, BIOS
calls — the hardware foundation now absent. 7. Graphics: affine, windowing,
blending, mosaic, palette manager, raster effects. 8. Multi-layer rooms,
autotiling, world graph, warps, streaming maps. 9. Audio: sequenced engine,
instrument banks, PCM mixer, streaming — **and the score editor** (notation, MIDI
import/export, voice allocation) with the tracker view beside it. 10. Save
drivers, RTC, SIO, peripherals, multiboot.

**Movement C — Make it build these two games (11–14)** 11. Data tables +
generated accessors. 12. Text engine: variable-width fonts, control codes, text
database, localisation. 13. UI/menu framework and layout editor. 14. Cutscenes,
timelines, coroutines, state machines.

**Movement D — Make it finishable (15–17)**

15. Budget, profiler, IWRAM/ARM placement, deterministic replay.
16. **The emulator pane**: embedded core, build-and-run with no app switch,
    frame step, pause, reset, save state — and two instances at once, which is
    what makes Link Cable testable on one machine (IV.5, decided).
17. **Help and reference**: complete coverage of every action, engine call,
    language construct and hardware register; templates; worked examples.

- - -
## Part VII — Definition of done

The suite is complete when both hold:

**Pokémon test.** A team can author 400 species and 350 moves as data, 200
connected maps with encounters and warps, a nested menu system, a turn-based
battle engine, branching dialogue in three languages, a 128 KB flash save with
RTC, and link-cable trading — and build it to a 16 MB cartridge that runs on
hardware.

**Undertale test.** A single author can build a bullet-hell encounter with
bespoke mechanics exceeding 128 on-screen projectiles, frame-accurate
choreographed cutscenes, typewriter dialogue with per-character audio, affine
sprite distortion and palette effects, a sequenced soundtrack, and persistent
meta-narrative save state — and have it run at 60 fps.

Neither test says "easily". They say **possible without leaving the suite**.

- - -
## Part VIII — What this costs, honestly

This is a multi-year programme, not a feature list. Movement A alone is larger
than the entire current SDK. Two things make it tractable:

- **The foundation is real.** A working codegen, a 1,483-line engine, five
  editors and a shipping toolchain already exist. This is an expansion, not a
  rewrite.
- **The ceiling is fixed.** The GBA does not move. Unlike a general engine, this
  suite can actually be *finished* — Part III is a complete list, and when every
  row reads "done", the hardware is covered.

The risk is not difficulty. It is starting Movement C before Movement A is
solid, and ending up with fourteen subsystems that each work alone and do not
compose.


- - -
## Part IX — Built, verified, dated

A record, not a plan. Everything below is in the overlay and covered by a
selftest that fails if it stops being true.

### Phase 6 runtime — hardware the engine could not reach (2026-08-03)

| Added | Where | Verified by |
|---|---|---|
| Interrupts: `rt_irq_set`, 14 sources, `rt_frame_count` | `runtime.c` | ROM builds, header checksum valid |
| Timers 0–3: `rt_timer_start/stop/read`, cascade, 4 clock rates | `runtime.c` | compiled, `gbahelp_selftest` |
| DMA 0–3 and HDMA: `rt_dma`, `rt_dma_stop`, `rt_hdma_start` | `runtime.c` | per-scanline recipe compiles |
| Sound FIFOs, `REG_FIFO_A/B`, `DMA_SPECIAL` | `gba.h` | reference lists them |
| BIOS: LZ77 (VRAM/WRAM), Huffman, RLE, `rt_div`, `rt_sqrt` | `runtime.c` | SWI encodings checked in objdump |

**The trap, recorded because it is silent.** In ARM state the BIOS call number
sits in bits 23–16: `swi 0x120000`, not `swi 0x12`. The short form assembles to
call 0, SoftReset — the cartridge reboots, with no error anywhere. Every SWI in
`runtime.c` was disassembled to confirm the encoding.

### Scripts — file-scope C as a resource kind

An Execute Code action is emitted *inside* an event function, so it could not
declare a function, a table or a static — which meant an interrupt handler, the
thing the runtime's own API asks for, could not be written anywhere in the tool.

A **script** is a sixth resource kind holding file-scope C, emitted once before
every object. The action-code compiler now knows the functions a project's
scripts define, so calling one from an action is not rejected as unknown. Before
this, such a call had its whole block replaced by a comment and the ROM built
anyway.

### Execute Code takes two languages, chosen on the row

| Setting | The text is | Reaches |
|---|---|---|
| Script | the subset the drag-drop actions lower to | bare `x`, `score`, the built-ins |
| C | handed to the compiler untouched | `self->x`, registers, script functions, all of C |

Detecting which language was written would be a guess that is wrong silently.
A chooser is wrong loudly, if at all.

### Help — the reference and the course in applied C

`de/gbahelp.py`. 62 topics: 16 lessons, 9 recipes, 8 action groups, 17
engine-call sections, 6 guides, 7 hardware sections.

Three properties it is built to keep:

1. **The reference is derived, not written.** Actions come from `ACTION_DEFS`;
   engine calls are parsed out of `runtime.h` with the comments above and
   beside them; hardware names come from `gba.h`. 92 of 93 engine calls carry a
   description, and the one that does not is a typedef. A hand-copied reference
   is wrong within a month.
2. **Checkpoints read the real project.** Nine of them, each proven in the
   selftest to go *both* ways — a checkpoint that can only report "not done"
   makes the course read as unfinishable and gives no way to tell a bug from
   one's own work.
3. **Recipes are inserted, not transcribed**, and routed by scope: an event
   recipe becomes a C action, a script recipe becomes a script. Every recipe is
   compiled by the real toolchain in the scope it is filed under — a check that
   caught three broken recipes the day it was written.

**Show C** puts the spec's Part 0 rule into one button: any event displays the
C it compiles to, with its build problems, beside the actions that produced it.

**A correction worth keeping.** The course first said C has no function inside a
function. GCC accepts one as an extension, so that was too strong; what is
actually true is that a *static* local function is an error, which is why an
interrupt handler specifically cannot live in an action. The selftest now
asserts the structural reason and only applies the compiler where it holds.

### Phase 7 runtime — the effects the hardware draws (2026-08-04)

Movement B continues. Four features that change the screen without redrawing
anything, all previously absent: the only graphics registers the engine named
were the three blend ones behind `rt_fade`.

| Added | API | Note |
|---|---|---|
| Blending | `rt_blend_alpha`, `rt_blend_brightness`, `rt_blend_off` | both target sets nameable, not just BLD_ALL |
| Windows | `rt_window`, `rt_window_off` | 2 rectangles, layer masks in and out |
| Mosaic | `rt_mosaic` | sizes 1–16, where 1 is off |
| Affine BG | `rt_bg_affine` | anchor stated as texture pixel + screen pixel |
| Affine sprites | `rt_obj_affine` | 32 shared transform groups |

**The affine call is the piece worth having written once.** The P registers hold
the *inverse* transform, and X/Y hold where texture (0,0) lands — not a centre.
Writing the rotation centre into them makes the picture swing across the screen
instead of turning on the spot, and scaling up divides. `rt_bg_affine` takes the
intent — put texture pixel (tx,ty) at screen pixel (sx,sy), turned by `angle`,
scaled by `scale` — and does that arithmetic.

Verified numerically rather than by compiling: the anchor texel stays fixed at
every angle across a full turn, scale 512 yields PA=128 and scale 128 yields
PA=512, and 90° gives exactly (0, −256, 256, 0).

**Three hardware traps, each of which reads as "the feature does not work":**

- A window of width 0 covers the **whole screen**, because the hardware reads a
  right edge at or before the left as 240. `rt_window` clamps to nothing.
- `REG_MOSAIC` alone changes nothing: the layer must also carry `BGCNT_MOSAIC`
  or `OBJ_MOSAIC`.
- Alpha blending needs **both** layer sets named. Naming only what is blended
  produces no visible change and no error.

Course lesson 15 covers all four; recipes Spotlight, Rotating background and
Mosaic dissolve are compiled by the real toolchain in their filed scope. The
reference absorbed the new calls and 41 new register names with no edit — the
derivation working as intended.

### Phase 7 authoring — the Palettes pane (2026-08-04)

Part VI names palettes as the constraint every GBA project eventually hits, and
says a tool that hides it badly produces games that look wrong and authors who
cannot find out why. The allocator was already correct — it refused to overflow
and it reported what it dropped. Hiding it badly was everything around that: the
report existed only in a build log, after the fact, one sprite at a time, and
never said how much room was left or which sprites were sharing a bank.

`gbabuild.palette_report(model)` runs the **real allocator** — the same call the
generator makes — so the report cannot describe an allocation different from the
one that ships. The pane shows banks in use of 16, colours of 240, each bank's
16 swatches with index 0 drawn crossed rather than coloured, how many colours
still fit, and which sprites share it.

**Pinning.** A sprite may be pinned to a bank. The allocator packs in sprite
order and has no way to know two sprites are the same character; pinning both to
one set is what lets them share tiles and cost less VRAM. A pin that cannot fit
is reported and the sprite is placed elsewhere — not silently ignored, and not
honoured at the cost of dropping colours. Verified through to the generated
sprite table, not just the report.

**Three defects found while building it, all in the seams rather than the
allocator:**

- `_pane()` unconditionally added **Rename and Delete**, which act on whatever
  is selected in the browser. On a pane showing the project as a whole they
  would have deleted a sprite the pane never mentioned. `_pane(..., resource=
  False)` now omits them, and a selftest asserts their absence.
- Overflow warnings printed the sprite's internal **id** while every other
  surface printed its name, so the one message telling an author which sprite
  was broken named something they could not map back to it.
- "1 colours".

### Phase 8 (part) — solid tiles and the parallax layer (2026-08-04)

Opening Movement B's map phase turned up a defect older than any of this work.

**Tile collision did not work in any game the SDK has ever built.** `nb_Room`
has carried `tile_solid`, `far_tiles`, `far_div` and `edge_open` since they were
written, and `gen_rooms` emitted an initialiser of seven fields — so all four
were zero-filled. With `tile_solid` null, `g_has_solid` stayed 0, every tile
test answered "free", and a tile floor stopped nothing; `rt_on_ground` could
only ever see a solid *object*. The ROM built, the header was valid, and nothing
reported it, because a table of zeroes is a valid table.

Three finished runtime features were unreachable from the tool for the same
reason: collision, the parallax layer, and the open-edge room.

| Now | Where |
|---|---|
| `solid` flag per tile, with the tile outlined in the strip | tile set editor |
| `nb_tile_solid[]`, one byte per **cell** | `_emit_tile_solid` |
| `far` / `far_div` parallax map on BG3 | room model |
| `edge_open` | room model |

**The factor that had to be right.** Solidity is authored per tile and consumed
per 8×8 cell, so a solid 16×16 tile marks four cells and a 32×32 marks sixteen.
Marking one would leave three quarters of every wall passable — which reads as
collision being unreliable rather than absent, and is much harder to diagnose
than either.

The example project's wall tile is now solid, so the example demonstrates
collision rather than merely containing a picture of a wall.

**`tools/gbaruntime_selftest.py`** is new and is the reason this is trustworthy.
It extracts `affine_matrix`, `cell_solid` and `box_free` **from runtime.c by
brace matching** and runs them on the host — a copy would be a second
implementation that agrees until someone edits one of them. It asserts the old
behaviour as well as the new (a floor reading "free" without the table), so a
fix is distinguishable from a no-op, and it was proven able to fail: breaking
the cell fan-out to mark one cell instead of four turns it red.

Still open in Phase 8: autotiling, the world graph, warps, streaming maps.

### Phase 8 (part 2) — warps (2026-08-04)

Room-to-room links, the thing Advance Map is mostly *for*. A warp is a rectangle
in one room, the room it opens into, and where arriving there puts the traveller.

```c
typedef struct {
    u16 x, y, w, h;   /* the rectangle, in room pixels */
    s16 room;         /* destination */
    u16 tx, ty;       /* where the traveller lands */
} nb_Warp;
```

Checked against the instance the camera follows — the player, by convention —
because a warp any instance could trip would fire on every wandering enemy.
Checked *after* movement, because a warp is entered by arriving on it and
testing first fires a frame early from outside it. `rt_room_goto_at(room, x, y)`
does the same from code.

**Two decisions that decide whether doors feel reliable:**

- **Overlap, not containment.** A door one tile wide would otherwise be stepped
  straight over by anything moving faster than its width — a door that works
  only sometimes, which is far harder to diagnose than one that never works.
- **A warp naming a deleted room is reported and left out**, rather than built
  as a door that goes nowhere. A dead door is indistinguishable from one placed
  in the wrong spot, and only one of those is the author's mistake.

The arrival position is cleared as soon as it is applied; leaving it set would
make every later entry to that room inherit one warp's destination.

Verified in `gbaruntime_selftest.py` by extracting `rt_warp_check` from
runtime.c: standing on a door fires it, a box merely overlapping a one-tile door
fires it, standing clear does not, the right column and wrong row does not, a
pending room change is not overwritten, and a destroyed traveller triggers
nothing. A two-room ROM linked in both directions builds at 28,652 bytes.

Still open in Phase 8: autotiling, the world graph, streaming maps. *(Auto-tiling landed in part 4; the world graph in the closing sweep; map streaming was already in the runtime.)*

### Phase 8 (part 3) — the door editor (2026-08-04)

Warps were in the model and in the runtime with no way to place one. That is the
same unreachable-feature shape as the tile-collision bug this phase opened with,
and it is worth naming as a pattern: **a feature finished on one side of the
seam and never connected on the other fails silently, because every individual
piece is correct.**

The room editor gains a third mode beside Objects and Tiles. **Leads to** picks
the destination first — a door with no destination is the one thing a warp
cannot be, and finding that out from a build report is a poor way to find out.
A click puts a door in that cell, a right-click removes it, and doors are drawn
in *every* mode, because a placed door that is invisible is state the author has
to remember.

Three details that are each a small refusal to be silent:

- **A room is not offered as its own destination.** A door back into its own
  room fires the instant the traveller lands on it, so the room reloads forever.
  Leaving it out of the list is cheaper than explaining it afterwards.
- **A door with no destination is drawn crossed through**, rather than looking
  like the ones that work.
- **A new door arrives at (120, 140), not (0, 0)** — the corner is inside the
  wall of most rooms.

Placing on an occupied cell does not stack a second door.

**A render artifact worth recording**, because it will mislead again:
`tools/uishot.py` calls `show_all()` after building the widget, which undoes
every `set_visible(False)`. A pane rendered through it shows controls the real
app hides. Verify mode visibility by driving the app and reading
`get_visible()`, not by looking at a screenshot.

### Phase 8 (part 4) — auto-tiling (2026-08-04)

Sixteen variants of one terrain, chosen by which of the four orthogonal
neighbours are the same terrain: bit 0 north, 1 east, 2 south, 3 west. Variant 0
is an isolated block, variant 15 fully enclosed. A run is declared by marking one
tile Auto-tile; it and the fifteen after it are the run.

**Authoring only.** What lands in the room's tilemap is an ordinary tile index,
so the cartridge pays nothing and the runtime never learns it happened.

Three decisions:

- **The neighbours are re-fitted, not just the cell.** A tile placed beside an
  existing one changes what *that* one should look like. A tool that only fits
  the cell under the pointer leaves a seam behind every stroke — auto-tiling
  that half works, which is worse than none.
- **Outside the room counts as the same terrain**, so a field running off the
  edge is drawn as continuing. The alternative puts a coastline around every
  level.
- **A run that has not got fifteen tiles after its start is refused**, with the
  numbers, and **dropped on load** if the set later shrinks. Accepting it would
  declare a run that the painter silently ignores, or one that reads past the
  end into another terrain's tiles — which looks like corruption rather than a
  bad setting.

Verified by painting an 8×4 block: the interior cell is variant 15 and the top
edge is variant 14, and the project builds to a valid 27,284-byte ROM.

**Phase 8 status.** Multi-layer rooms (parallax on BG3), solid tiles, warps, the
door editor and auto-tiling are done; map streaming was already in the runtime.
What remains is a **world-graph view** — a picture of the rooms and the doors
between them. The links themselves exist; only the drawing of them does not.

### Phase 9 (part) — the audio fields nothing could reach (2026-08-04)

**The fourth stranded feature of the night, and the pattern is now the finding.**
`nb_Sound` has carried five appended fields since they were written —
`drum`, `kind`, `duty`, `vol`, `decay` — and `gen_sounds` emitted an initialiser
of five fields, so all five were zero-filled. The runtime reads every one of
them (`runtime.c:1031-1033`).

What that cost, in every game the SDK has ever built:

- **No percussion.** The noise channel was never driven; the drum track existed
  in the runtime and had no way to be written.
- **Every sound effect stopped the music.** `kind=1` is documented in the struct
  as "plays on the wave channel, layers over music". Left at 0, a coin or a jump
  silenced the soundtrack for its duration.
- **No timbre at all.** Every note was a 50% square at full volume that held,
  because duty, volume and decay were always the defaults.

Now emitted, authored and reachable: Drums joins Lead and Bass in the sound
editor, and Sound / Width / Volume / Decay sit in its toolbar.

**Drums are four kinds, not a pitch range**, so they occupy the top four rows of
the same roll — crash, hat, snare, kick, top down, the order a drum machine
uses. Reusing the roll keeps one set of click maths, one ruler and one set of
keys rather than a second editor that behaves almost the same. They stay drawn
while the melody is written, because the roll is one picture of the whole sound.

A sound with no drum steps emits no drum array, so silence costs nothing.

**The pattern across Phases 7–9.** Four times tonight a feature was finished on
one side of a seam and never connected on the other: tile collision, the
parallax layer, the open-edge room, and now the whole audio expansion. Each one
compiled, built a valid ROM, and reported nothing — because every individual
piece was correct. The lesson for the remaining phases: **when a struct grows by
appending, the generator's initialiser is the thing that silently does not
grow with it.** `gbaruntime_selftest.py` now counts initialiser fields for
exactly this reason.

### The append-and-forget check, and a correction (2026-08-04)

`gbaruntime_selftest.py` now counts the fields each `nb_*` struct declares in
runtime.h and compares that with what the generator writes into the matching
initialiser. It is six assertions and it would have caught every one of
tonight's silent bugs on the day the field was added.

**Writing it immediately found a fifth, and it corrects an earlier claim in this
document.** `nb_Object` declared 13 fields and emitted 7. Among the six missing
was `tilecol`, and `inst_move` reads it *first*:

```c
if (!ob->tilecol) { in->x += dx; in->y += dy; return; }
```

So the Phase 8 entry above is only half right. Emitting `nb_tile_solid` made the
table exist; **no object consulted it**, because every object's `tilecol` was
zero. Tile collision was still inert end to end. Both halves are needed and they
fail independently — the room must point at a solid table, and the object must
be marked as reading it. The runtime harness now asserts them together, and the
example game's player is marked so the example demonstrates collision rather
than containing a picture of a wall.

Also now emitted: `depth` (drawing layer) and the four collision-box insets. A
new object is created Stopped by → Solid tiles, which is what painting a floor
leads someone to expect; a project made before these fields existed loads with
zero and moves exactly as it did.

**Two mistakes of my own, recorded because both are silent in Python.** A dict
literal in the help selftest carried two `"objects"` keys, so the one I added
was discarded and the checkpoint tested nothing — the file now scans itself for
duplicate keys. And an assertion briefly went in with an empty label and a
tautological condition, printing PASS while testing nothing, which is worse for
a suite's credibility than a missing test.

### Phase 9 (part 2) — the twelve effects nobody could reach (2026-08-04)

The runtime carries twelve sound effects in code — `rt_sfx(NB_SFX_COIN)` and
eleven siblings — that need no data at all. **Play Sound only ever offered the
project's own sounds**, so a new project, which has none, could not make a noise
until somebody had written a tune. That is a long way from a first jump, and it
is the same shape as the other stranded features: the capability existed, the
route to it did not.

Play Sound now lists the project's sounds first and the twelve built-ins after,
named (Coin, Jump, Explode) rather than shown as the routing token `sfx:coin`.
A Play Sound action added to a project with no sounds defaults to one of them,
so it makes a noise the moment it is added rather than being a live-looking
row that does nothing.

A project sound still takes precedence, and a name matching neither is still
reported — adding the built-ins must not turn a broken reference into silence,
which the selftest asserts directly.

**On the audit that found it.** After five append-and-forget bugs, the same
question asked of the API rather than the structs — *which engine calls can no
level of the tool reach?* — produced 83 of 100. Most are fine: an Execute Code
block set to C reaches every one of them, and the spec only forbids a higher
level doing what a lower cannot. `rt_sfx` was the exception worth acting on,
because its whole value is being available to someone who has authored nothing.

### Phase 9 (part 3) — sampled audio (2026-08-04)

Direct Sound was entirely absent: `REG_SOUNDCNT_H` was set to PSG output and
nothing else. One PCM voice now plays on Direct Sound A, and **File ▸ Import
Sound** brings a `.wav` in and converts it.

Three decisions, each of which removes a class of bug rather than adding an
option:

- **Timer 1, not timer 0.** Direct Sound A can take either. Timer 0 is what the
  Help's own interrupt example arms, so taking it here would break that example
  the moment a project played a sample, with nothing to say why.
- **One rate, 16384 Hz, converted on import.** The hardware has no resampler —
  the timer period *is* the sample rate. A per-sample rate would mean re-arming
  the timer on every play and retuning anything else sharing it, and "it plays
  too fast" is the symptom.
- **Playback counts itself out.** The DMA repeats forever, so a sample that is
  not stopped loops its buffer — a stuck note, easily mistaken for a hung game.

Import handles 8- and 16-bit WAV, mono or stereo, at any rate, capped at 8
seconds because a second costs 16 KB of ROM. Two conversions that are silent
when wrong and are therefore asserted directly: **8-bit WAV is unsigned and the
FIFO is signed** (getting it backwards plays a loud buzz at the right pitch,
which reads as a rate problem), and the length is padded to a whole number of
32-bit words, because a short final word sends whatever follows the array to
the speaker.

Play Sound routes a sampled sound to `rt_pcm_play` rather than the pattern
player — falling through would sound its empty lead and bass tracks, i.e.
silence, from a sound that plainly has audio in it.

Verified end to end on a 440 Hz 44.1 kHz 16-bit stereo tone: 8192 samples out
for half a second in, signed, word-aligned, and still crossing zero about 10.7
times per 200 samples — the frequency survived the resample. A ROM carrying it
builds at 35,908 bytes, 8 KB of which is the sample.

### Phase 9 (part 4) — voice allocation, and one rule instead of two (2026-08-04)

**Priority.** There is one wave channel, and it was last-wins: a footstep cut off
a death the frame after it started. A sound now carries a priority 0–7 and a
playing effect is only replaced by one of **equal or higher** priority. Equal
still wins on purpose — a gun fired twice must be heard twice; only lower is
refused. Priority 0 is exactly the old behaviour, so nothing written before this
changes. The twelve built-in effects carry their own priorities, so an explosion
outranks a step without anyone configuring it.

**And a divergence I introduced an hour earlier, corrected.** Part 3 routed
sampled sounds in the *generator*: the Play Sound action emitted `rt_pcm_play`
directly. That worked for the action and silently did not work anywhere else —
a line of C calling `rt_play_sound(0)` on a sampled sound played its empty
pattern tracks, which is silence from a sound that plainly has audio in it. One
rule with two behaviours, which is the same shape as every stranded-feature bug
this document records.

The sample now travels in `nb_Sound` as `pcm` / `pcm_len` and **the runtime
routes**, so the action and the C call get the same answer. The field-count
check enforced the consequence immediately: adding two struct fields turned
`nb_Sound` red until the generator emitted them.

Priority is verified on the host by extracting `sfx_may_start` from runtime.c —
an idle channel accepts anything, a footstep does not cut off a death, an
explosion does, and an equal-priority repeat restarts rather than being lost.
A ROM with looping music, a prioritised death and an imported voice sample
builds at 36,216 bytes.

### Phase 9 (part 5) — the score view (2026-08-04)

The same pattern read as notation: two staves braced together, lead on the
treble and bass on the bass, drums on a one-line staff beneath. A **view**, not
a second document — editing either changes the one sound.

**Every note is one step long, and the staff says so.** The model is a step
sequencer; it carries no durations. Inventing them would draw a rhythm nobody
wrote, so the score shows evenly spaced notes with bar lines every four steps.

**Staff position is diatonic** — seven letters per octave, not twelve
semitones — so C sharp shares C's line and the sharp is what tells them apart.
Spacing by semitone is the mistake that makes a chromatic run look like a
scale, and it still looks like music, which is why the selftest asserts that an
octave is exactly seven staff steps and checks all ten staff lines by name.

**One trap avoided by checking rather than assuming.** MIDI puts C4 at 60, so
`divmod(64, 12)` says octave 5. Writing the staff base out by hand as "octave 4,
letter 2" is an off-by-one that puts every note an octave out — the base is now
derived by running E4 and G2 through the same function.

**And one caught by this project's own history.** The sharp was first typed as
U+266F. Nimbus Sans does not have it; only the CJK fallback does, so on the
guest it would render as a box or in a face that does not match the notes beside
it. It is now drawn in cairo, which also lets it scale with the staff rather
than with the interface font.

Phase 9 is complete: drums, timbre, priority, sampled audio, built-in effects,
and the score view.

### Phase 10 (part) — the link cable (2026-08-04)

Multiplayer mode: two to four units, **one halfword from each per transfer**.
That is the entire budget — about 16 bytes per frame shared by the whole
session at 9600 baud. The shape that fits is exchanging *input* and running the
same simulation on every unit, which is why the API is one word and not a way
to send objects.

Nothing blocks. A game that waits for a transfer drops frames on a cable that
is merely slow, so `rt_link_poll` reports what arrived and the caller carries on.

**Three details that decide whether a link works at all:**

- **Two registers select the mode and both matter.** RCNT bits 15-14 choose SIO
  at all; only then do SIOCNT bits 13-12 choose multiplayer. Setting SIOCNT
  alone leaves an RTC cartridge's port in GPIO, where the link does nothing and
  says nothing. `rt_link_open` writes RCNT first, and the selftest asserts the
  order.
- **A failed transfer leaves the previous words in the registers.** Reading
  without checking the error flag hands the game last frame's input as though it
  were this frame's, and two units drift apart with nothing on screen to explain
  it. `rt_link_poll` checks first and returns 0.
- **Only the parent starts a transfer.** A child that tried would be talking
  over it, so `rt_link_start` refuses on a child rather than corrupting the
  exchange.

**A claim I removed.** The first version wrote the error bit back with a comment
saying it was write-1-to-clear "as the hardware requires". That is not something
this codebase has verified on hardware, and a confident wrong claim about a
register is worse than no claim: it becomes the thing the next reader trusts.
The flag is now left for the hardware and the comment says so. What the code
guarantees is only that it does not read data on error, which is what the test
asserts.

Verified by extracting `rt_link_open` and `rt_link_poll` from runtime.c and
running them against simulated registers: opening leaves GPIO for multiplayer at
the requested baud without writing the read-only ID field, a busy transfer
yields nothing, a failed one yields nothing and leaves no stale word, and a
clean one yields the four words. A ROM doing lockstep input exchange builds at
28,516 bytes.

### Phase 10 (part 2) — the cartridge clock (2026-08-04)

A Seiko S-3511A read over three bit-banged GPIO pins. This is what the Gen III
benchmark needs for day and night, and it is a **cartridge** feature: the
console has no clock, so whether a game can tell the time depends on the
cartridge it is in.

**The failure mode this is built around.** A cartridge without the chip does not
answer with an error. It answers with whatever the bus floats to, and 0xFF
everywhere converts out of BCD into a perfectly readable 255th of the 255th. So
every field is range-checked before it is believed, and `rt_rtc_read` returns 0
and writes nothing rather than handing back a date. A day-night cycle that
silently believes it is midnight on the 1st of January is worse than one that
knows it cannot tell the time.

**Stated plainly, in the header, the code and the Help: the bit-banged transfer
has not been run against the chip.** What is verified is everything around it —
the command encoding (the fixed 0110 prefix that, wrong, makes the chip ignore
every request so the clock simply never advances), the BCD conversion, and the
rejection of an absent clock. The transfer follows the S-3511A's published
sequence. Marking the boundary is the point: this document has already recorded
one confident wrong claim about a register, and the cost of that is that the
next reader trusts it.

A day-night ROM that dims the screen after 8pm, and stays in daylight when there
is no clock, builds at 28,960 bytes.

### Phase 10 (part 3) — multiboot, and Phase 10 closed (2026-08-04)

**File ▸ Export for a Link Cable** builds a multiboot image: the same game
linked to run from the console's own memory rather than from a cartridge, so it
can be sent to a console with no cartridge in it.

`gba_mb.ld` is **derived from `gba.ld` by one substitution** — the ROM region
swapped for EWRAM — rather than copied, so the two cannot drift into behaving
differently. Verified by reading the linked ELF: the cartridge build runs from
0x08000000, the multiboot build from 0x02000000, and `.data` still runs in
IWRAM in both because only where it is loaded from changes.

**The size limit refuses rather than warns.** An oversized image links, produces
a file, and is then never sent — which looks like a cable fault. 256 KB is
checked before the file is handed over.

**Two of my own tools were wrong, both in the same way.** The selftest's
re-entry guard wrapped every method as `guarded(*a)` and its `build_rom` stub
took a fixed signature, so adding one keyword argument to a real method made the
*test harness* raise as though the method were broken. Both now forward
`**kw`: a harness that constrains the signatures it wraps produces failures that
point at the wrong file.

The ELF is also now read in Python rather than through `objdump` — which
binutils sit beside the compiler varies, and a check that skips because a tool
is missing is a check that stops being run.

**Phase 10 is complete**: SRAM saves with a signature the emulators recognise,
the cartridge clock, the link cable, and multiboot. Movement B (phases 6–10) is
finished.

### Phase 11 — data tables (2026-08-04)

Movement C opens. A **table** is a seventh resource kind: rows with named
columns, emitted as a C struct array with a count beside it. This is what a game
of any size is mostly made of — species, moves, items, prices — and the thing
that otherwise becomes a thousand-line script nobody can edit without reading
all of it. It is also, plainly, what YAPE is.

| Column type | In C |
|---|---|
| Number | `s32` |
| Text | `const char*` |
| Yes/No | `u8` |

Three columns only, and deliberately: each has an obvious C type *and* an
obvious cell editor. A type with neither becomes a column nobody can fill in.

**Four decisions that keep the grid and the cartridge agreeing:**

- **The count is emitted beside the array.** C cannot ask an array its length
  once it has decayed to a pointer, and a game that writes the row count in by
  hand reads past the end the first time a row is added.
- **Headings are rewritten, not rejected.** Authors write "Base HP" and
  "attack%"; C takes neither. A table that refuses a space in a heading is a
  table nobody finishes filling in.
- **A Number column keeps a number** even when the typed text is not one.
  Storing "45kg" would build a C initialiser that does not compile, and the
  error would name the generated file rather than the cell.
- **Adding a column widens every existing row**, and loading pads or trims rows
  to the header. A short row means a column was added, a long one means a
  column was deleted; both are ordinary edits and neither should cost the row.

Text is escaped, so a quote in a cell cannot end the C string literal early.
A table with no columns gains one rather than emitting an empty struct, which C
rejects.

A ROM whose script walks a four-row species table and draws from it builds at
28,924 bytes, with the strings visible in the cartridge image.

### Phase 12 (part) — dialogue (2026-08-04)

A message revealed a character at a time, in a panel, advanced by A. **The
engine owns it** because by hand it is a timer, a cursor, a page counter and a
wait-for-button — five of the twelve variables an instance has, spent per
speaking object. Written once it costs nothing per object and behaves the same
everywhere, which is what makes a game's dialogue feel like one game.

Control codes live **in the text**, because dialogue is authored as text and
anything assembled from parts stops being editable by whoever is writing the
words: `\n`, `{p}` page, `{s:N}` speed, `{c:N}` colour, `{v:N}` a global,
`{w:N}` pause.

**An unknown code prints as written.** Swallowing it would erase the rest of a
sentence over a typo, which is the worst thing a text engine can do to somebody
writing prose. The selftest checks both a bad code and an unclosed one.

**Two failures of my own, and one of them mattered.**

The Say action was registered in `ACTION_DEFS` and the generator, but the edit
that adds it to a **palette group** failed its assertion and I did not notice
until the ROM built: for one build the generator could emit an action the
palette never offered. An action reaches an author through three tables, and
being in only some of them is the same unreachable-feature shape this document
records five times over. There is now a check that every palette action is
defined, every defined action is in the palette, and every action has a tip.

And the runtime harness's brace matcher counted `'{'` **inside a character
literal**, so extracting `rt_say_step` stopped one brace early and produced a
function with its tail missing that still looked like C. It now skips string
literals, character literals and comments. All 90 extractions still pass, so
nothing else was being quietly truncated.

### Phase 12 (part 2) — word wrap, and an honest note on fonts (2026-08-04)

**A defect in what shipped an hour ago:** the dialogue engine broke lines at
column 26 regardless of where it was in a word. Wrapping now happens at the
*start* of a word that will not fit, which is the single most obvious thing a
text box can get wrong.

**A word is measured without its control codes**, so `{c:3}Bulbasaur` takes ten
cells and not fifteen. Measuring the raw characters wraps a line that would have
fitted, and the ragged right edge reads as a fault in the writing rather than in
the measurement. A word longer than the whole panel is drawn anyway rather than
wrapped to nowhere, and a leading space is dropped at the start of a line.

**Variable-width fonts: scoped, and deliberately not started.** The font is
tile-mapped — one pre-rendered 8×8 tile per character, uploaded to a charblock,
with text drawing being nothing more than writing tile indices into the BG1 map.
Proportional text means compositing glyphs into a RAM tile buffer at arbitrary
pixel offsets and DMAing that to VRAM: a rewrite of the exact path the dialogue
engine now depends on, plus per-glyph widths the font generator does not yet
produce.

That is two or three sessions of work done properly, and half of it would leave
the text system worse than it is. It is recorded here as the remaining Phase 12
item rather than started and abandoned midway.

### Phase 12 (part 3) — per-glyph widths, and a de-risked font (2026-08-04)

Groundwork for proportional text, plus one bug it exposed.

**`nb_font_w[]`** — a width per glyph, measured from the ink rather than from
the source font's advance, because these glyphs are hand-rasterised into 8×8
cells and several are overridden by hand. The tile data is **byte-identical**
to before: nothing moved, so nothing looks different.

**`rt_text_width` (pixels) and `rt_text_cells` (whole cells)**, both ignoring
control codes — a coloured string measures as what it will look like rather than
as what was typed.

**The bug that found:** `rt_draw_text_centre` counted raw characters, so a
banner carrying `{c:3}` was pushed five cells left of centre. It now counts
cells.

**The font swap is proven viable and deliberately not taken.**
`DejaVuSansCondensed` passes the generator's legibility audit with **one** extra
override — a hand-drawn `j`, which the rasteriser otherwise draws as a bare stem
identical to both `i` and `|` — and makes a typical line **34% narrower than
fixed cells**.

*(Corrected in part 5: that comparison is against fixed-cell drawing, so it
credits the face with what the renderer does. Measured against the existing face
rendered proportionally, the condensed face is worth 6%.)*

It is not switched on because text is still drawn one glyph per cell, and
proportional glyphs in fixed cells look *worse* than monospaced ones: an `i`
three pixels wide leaves five pixels of gap. The swap belongs with the renderer.
Both the font and the override are recorded in `gen_gba_font.py` so the next
session starts from a proven configuration rather than re-deriving it.

**Also fixed:** `runtime.h` carried the dialogue block twice, from a heredoc
that ran after a failed `cd`. Harmless to the compiler, and exactly the kind of
thing that makes a header untrustworthy to read.

### Phase 12 (part 4) — the proportional renderer (2026-08-04)

`rt_vwf(1)` and the dialogue panel draws glyph-by-pixel: each glyph advances by
its own width rather than by a whole 8-pixel cell, and wrapping measures in
pixels to match. About half again as much text in the same box.

**The panel only, and that is a memory decision.** Proportional text needs a RAM
copy of the tiles it draws into, because a glyph lands *across* a tile boundary
and tiles are the only thing VRAM takes. The panel is 26×4 = 104 tiles = 3.3 KB.
The whole 30×20 text layer would be 19 KB — most of IWRAM — to make a score
read-out slightly narrower. The HUD stays on cells, and both paths live in one
engine with the cursor held in pixels or cells rather than two copies of it.

**Colour resolves per tile.** 4bpp colour comes from the map entry's palette
bank and a tile has one, so a colour change takes effect at the next tile
boundary. Stated in the header, the Help and here, because the alternative —
silently recolouring the two or three pixels of the previous letter that share
the tile — looks like a rendering fault rather than a hardware limit.

**What the test actually checks.** A renderer that clips at tile edges still
draws recognisable text, so the assertion that matters is that a glyph placed at
pixel 6 appears in *both* tile 0 and tile 1 — clipping it is how every other
letter quietly loses its right-hand columns. Also that a glyph advances by its
own width, that colour reaches the tile the pixels landed in, and that row 1
draws into row 1 and leaves row 0 alone.

A ROM with proportional wrapped dialogue builds at 32,580 bytes.

### Phase 12 (part 5) — the font swap, measured and rejected (2026-08-04)

With the proportional renderer in place, the font swap held over from part 3 was
finally measurable against the thing it should be compared with. It does not pay.

| Change | Narrower by |
|---|---|
| fixed cells → proportional rendering | **33%** |
| monospaced face → condensed face | **6%** |

On all-capitals text the condensed face is **wider**. A second face costs 3,040
bytes of every cartridge, and 6% of a dialogue box does not buy that.

**Nearly all of the gain belonged to the renderer, not the face** — and part 3
of this document said "34% narrower" without saying that the comparison was
against fixed cells, which credited the face with the renderer's work. That
entry is corrected in place.

The generator keeps the capability: `rasterise()` takes a path and extra
overrides, so a future face that is a bigger jump is one call away. The second
face is left unmade rather than made and half-justified, and the measurement is
recorded so nobody has to re-derive it to reach the same answer.

**Phase 12 is complete**: dialogue with control codes, word wrap, per-glyph
widths, text measurement, and the proportional renderer.

### Phase 13 (part) — menus (2026-08-04)

A list with a cursor, drawn in a panel: up and down move, A chooses, B cancels.
Most of a Pokémon-class interface is this.

**Non-blocking**, like the dialogue engine and for the same reason: a menu that
spins its own loop stops the music, the animation and the link cable while it is
open. `rt_menu_step` is called once a frame and reports what happened.

**It draws only when something changed.** Rewriting the panel every frame is
about 200 tile writes for a picture identical to the last one — on a 16.78 MHz
CPU that is a real fraction of the budget spent on nothing. The selftest asserts
that an idle menu draws *once* and once more when it moves, which is the kind of
thing that is invisible until a game is already slow.

**Scrolling moves one row, not one page.** A page scroll moves the item the
player was looking at. Eight rows show at a time with an arrow at the edge when
there is more above or below, because a long list that looks short is a list
whose rest is never found.

**Tables and menus are for each other**: the table holds the data, the menu
shows a column of it, and the index it returns indexes the table straight back.
A shop of nine items driven from a table builds at 34,800 bytes.

One documented trap: the item array must outlive the menu, because the menu
holds the pointer rather than copying the strings. An array declared inside the
function that opened it is gone by the time it draws.

### Phase 13 (part 2) — Show Menu, and a check earning its keep (2026-08-04)

The menu runtime was reachable from C and not from the sheet. **Show Menu**
closes that: up to four lines and the name of a variable.

**The design problem is real and worth stating.** A menu spans frames; an action
does not. A row of the sheet cannot wait for a choice. So the action opens the
menu and names a variable, the engine writes the answer there when it closes,
and the next Step event branches on it with an ordinary If Variable — the whole
interaction stays inside the vocabulary somebody using the sheet already has.

| Value | Meaning |
|---|---|
| `-1` | still choosing |
| `0`–`3` | the line chosen |
| `-2` | the player backed out |

**Held at −1 while the menu is up**, so a Step event can tell "still choosing"
from "chose the first line". Without it the sheet reads a stale 0 and acts on a
choice nobody made.

A Show Menu with no lines, or with no variable to answer into, is **reported at
build time**: both produce a menu that opens and throws the choice away, which
looks like the menu not working rather than a setting left blank. The item array
is emitted at file scope and before the object that points at it, because the
menu holds the pointer rather than copying the strings.

**The registration check from Phase 12 caught this exact mistake twice.** An
edit to the palette group aborted on a stale anchor both times, leaving `menu`
defined in the generator and absent from the palette. The check names the
offending action — `every defined action is in a palette group <- ['menu']` —
and was proven able to fail by removing the entry again. Adding it after the
first occurrence is the reason the second one cost a minute rather than a
release.

### Phase 14 — cutscenes (2026-08-04)

A scripted scene is things happening over time. The engine already had alarms
for sequencing, so what was missing was the two parts a Step event cannot
express without a counter and a pile of branches.

**Glide To** moves an instance to a point over N frames. By hand that is a
start, a target, a frame count and a division per axis per frame — four of the
twelve variables an instance has, spent on arithmetic.

**The arithmetic that matters:** the remaining distance is divided by the
remaining *frames*, not stepped by a precomputed amount. 100 pixels over 7
frames is 14.28 a frame; stepping by 14 lands at 98 and the scene is subtly
wrong with nothing to point at. Dividing what is left by what is left cannot
miss, and the selftest checks exactly that case.

A glide overrides speed while it runs, because two things moving one instance
is a fight whose cause cannot be seen.

**Lock Input** stops a cutscene being one the player can walk out of. It is
applied inside `rt_key_held` / `rt_key_pressed` rather than by clearing the key
state, and `rt_key_raw` reads past it — a pause menu has to work while
everything else is frozen, and a menu reading its own keys through the lock
cannot.

A whole scene from the sheet — lock, walk in, speak, walk out, unlock, sequenced
by alarms — builds at 35,064 bytes.

**Two checks caught their own kind of mistake.** The action-registration check
passed first time here because I read the palette group out of the file instead
of assuming its text, which is what had made the last two edits abort. And the
second-person check flagged sample *game dialogue* ("You made it") in a Help
code block — it cannot tell a character's line from UI copy, so the sample now
says something a narrator would rather than the checker growing an exception.

### Phase 15 (part) — the budget report (2026-08-04)

Movement D opens. **Build ▸ What This Game Costs**, before building rather than
after: a game that will not fit is otherwise found out at link time, by an error
naming a section of the output file rather than an asset.

| Counted | Limit |
|---|---|
| Sprite tiles | 1024 — every frame of every sprite, at once |
| Background tiles | 512 |
| Sprite colour sets | 16 |
| Objects in a room | 128 live at a time |
| Sampled audio | 16 KB per second |

**Every line names its largest contributors, and that is the whole point.**
"Over by 40 tiles" is a fact nobody can act on. "Boss is 64×64 with 20 frames,
which is 1280 of them" is a decision. The report is sorted largest-first and
carries the shape of the asset, not just its cost.

**A 16×16 tile counts as four and a 32×32 as sixteen**, because the hardware
works in 8×8. Counting authored tiles would under-report by a factor of four or
sixteen and let a project sail past the limit while the report said it was fine
— which is worse than no report.

Objects created while the game runs are not counted, because they cannot be. The
room figure is what is *placed*; the 128 is what can be *alive*. Said in the
report rather than left for someone to discover.

The limits live in one `BUDGET` dict rather than in the messages, because a
limit quoted in three places is a limit that will one day disagree with itself.
It reuses the real palette allocator for the colour-set line, so the budget
cannot report a different allocation than the one that ships.

### Phase 15 (part 2) — the profiler and IWRAM (2026-08-04)

**The profiler** measures the engine's own step, movement and drawing, with
slots 3–7 for the project, and `rt_prof_overlay()` draws a corner read-out.

**Timer 2, because the others are spoken for**: timer 0 is the project's (the
Help's interrupt example arms it) and timer 1 clocks sampled audio. Taking
either would make measuring break the thing being measured.

**TM_FREQ_64, because a frame is 280,896 cycles and a 16-bit counter is not.**
At one tick per cycle the counter wraps six times a frame and every reading is
nonsense. At 64 cycles a tick a frame is 4,389 ticks.

**The measurement survives a wrap.** Counters are `u16` and subtracted
unsigned, so a section spanning the roll-over reports 66 ticks rather than
−65,470. That is precisely the reading that would be wrong when the frame is
busiest, which is the only time anyone looks.

**IWRAM code.** `IWRAM_CODE` puts a function in the 32-bit no-wait-state RAM
instead of the 16-bit cartridge bus with wait states. `cell_solid` and
`box_free` are there because they run thousands of times a frame; 252 bytes of
32 KB shared with every variable in the game, so this is for the few functions
that earn it.

**A section that is placed but never copied is a jump into whatever IWRAM
held.** `.iwram` therefore shares a load group with `.data`, so crt0's existing
copy loop moves both — verified on the linked ELF: `.iwram` at 0x03000000,
`.data` immediately after it at 0x030000FC, and `__data_start`/`__data_end`
spanning both.

**And a warning it introduced, handled rather than left.** Code and data in one
IWRAM segment makes that segment RWX, which the linker warns about. On a console
with no MMU that is what IWRAM *is*. Silenced with `--no-warn-rwx-segments` and
a comment saying why, because a warning nobody can act on is a warning everybody
learns to scroll past — and the selftest asserts builds are warning-free, so it
would have gone red every time from here on.

### Phase 16 — the emulator hand-off, and what it is not (2026-08-04)

**Compile & Play** (Ctrl+R) builds the game and opens it in the GBA Emulator;
closing the emulator returns here with the project as it was.

**This is not the embedded emulator pane the spec asked for, and it cannot be.**
Notebook OS runs one app at a time — Part 3.6 of the roadmap named this
constraint before any of this work started, and nothing since has changed it.
An emulator core inside this window would need a second fullscreen client that
the window manager will not give it.

What *can* be fixed is the walk. Exporting, closing the SDK, opening Finder,
finding the file and opening the emulator by hand is six steps between a change
and seeing it — and that loop is what decides whether a game gets finished. It
is now one menu item.

**Three refusals, because the failure mode here is severe.** The window *hides*
during the hand-off, so anything that goes wrong looks like the machine
freezing:

- A project with no object or room says so and stays.
- A game that does not compile stays, keeps the log, and points at Build
  Details. Hiding behind an emulator that never opened is indistinguishable
  from a hang.
- A missing or unstartable emulator says which, rather than hiding.

**The project is saved before the emulator opens**, because this window is not
on screen to be saved from while the emulator has it.

A game that compiles but has problems still plays, with a count of the rows that
will not do anything — the choice to run a partly-broken game is the author's.

Two instances for link-cable testing remain out of reach for the same one-app
reason, and are recorded as such rather than left looking unfinished.

### Phase 17 — proving the reference complete, and a check that could not fail
(2026-08-04)

The Help was built in Phase 12 and has grown with every phase since. Phase 17's
bar is **completeness**, so this session tried to prove it mechanically rather
than by reading.

**Result: 43 of 43 actions, 88 of 88 hardware registers, and 144 of 145 engine
calls.** The one gap was `rt_menu_open_var` — declared, implemented, wired to
an action, and absent from the reference because the parser read runtime.h a
line at a time and its declaration wrapped across two. Any wrapped declaration
was invisible. Fixed, and the reference now lists 147.

**Then the check turned out to be tautological, which is the more useful
finding.** The reference is *derived* from runtime.h, so comparing the two
cannot catch a call nobody documented: adding a declaration adds a reference
entry by construction. Verified by adding a bare declaration and watching the
section stay green.

What it genuinely catches is the parser missing something the header declares —
a silent hole in the only documentation on the machine — which is exactly how
the one gap was found. Reverting the multi-line fix turns it red. The comment
now says that, instead of claiming completeness it cannot check.

**The complementary check had the same weakness in a subtler form.** "No engine
call is a bare signature" treated a **section banner as covering every call
beneath it**, so a call added to an existing section was never flagged. The rule
now is: a call is documented if it has its own note **or its section's prose
names it**.

25 calls failed that immediately — `rt_menu_close`, `rt_gliding`,
`rt_timer_read`, `rt_mosaic` and others: accessors whose names "say what they
do", which is precisely the reasoning that leaves a reference incomplete. All 25
now carry a note in the header, where it serves anyone reading the source too.
Adding an undocumented call now turns the suite red.

**All seventeen phases have been through this document.** Movements A–D are
recorded; what remains unbuilt is recorded as unbuilt, with the reason.

### The world map — the last recorded gap (2026-08-04)

With all seventeen phases through this document, the gaps it records are: the
embedded emulator pane and two-instance link testing (both impossible under the
one-app constraint), the proportional font swap (measured and rejected), and
the **world graph**. That last one was buildable, so it is built.

**View ▸ World Map** draws every room and the doors between them, and clicking a
room opens it — the reason to find a room on a map is to go and change it.

**A door to a deleted room is drawn as a red stub with a cross, and the heading
counts them.** That is what the view is *for*: a room with no way back is
invisible in a list and obvious on a map.

**A grid, in project order, not a force-directed layout.** A graph that
rearranges itself when a room is added is one nobody can navigate twice, and
the thing being looked for here is a room that was fine yesterday.

Placing a door refreshes the map, so it cannot be stale beside the room editor
that changed it.

**One defect found by the selftest rather than the eye:** an unrealised
GtkDrawingArea reports a width of **1**, not 0, so the `or 600` fallback never
fired and the very first draw stacked every room into a single column. Anything
narrower than one box is now treated as "not allocated yet". The test caught it
because it asserts the layout goes left to right, which a screenshot taken after
allocation never would.

### Hardening — one project that uses everything (2026-08-04)

Every subsystem here has its own checks, and every one passes against a project
built to exercise that subsystem alone. None of them would notice a feature that
works by itself and breaks the moment it shares a cartridge with another.

`tools/gba_integration_selftest.py` builds **one project that uses all of it**:
every resource kind, every runtime subsystem, sheet actions and hand-written C
in both languages, a 480×320 streaming room, two rooms linked both ways, a
sample, a drum track, a data table, a menu, a cutscene, and a script driving
interrupts, timers, DMA, affine, windows, blending, mosaic, the clock, the link
cable and the profiler. It requires no reported problems, a clean compile, a
valid cartridge, and the same project as a multiboot image.

**45,660 bytes, warning-free, first run.** The accumulated integration holds.

**But a test that has never failed is not yet a test.** Forcing the class it
exists for — a table named so its generated array collides with a menu's —
produced two `nb_menu_1` arrays and a compile error in *generated* code, naming
a line nobody wrote.

Two fixes, in the right order:

- **The generator now keeps one register of every file-scope name it invents.**
  Tables and menus both mint names from author text and neither could see the
  other's. Colliding is a compile error; renaming is a table whose C name gains
  a suffix.
- **The duplicate check now looks for the actual thing** — any file-scope array
  defined twice — rather than only at table arrays, which is why the case that
  really happened slipped past it into the compiler. Disabling the register
  turns it red, which is how that was confirmed.

### Hardening — damaged files (2026-08-04)

The worst defect this OS has had was on the read side: opening and closing an
app destroyed a damaged store, with no user action at all. Tonight added three
resource kinds and a dozen fields to the read path, and only *migration* had
been tested — an old project gaining new keys. Damage had not.

**Nothing raised**, which is the first thing that matters: the loader runs
before the window exists, and an exception there is an app that will not open on
a file it could have repaired.

**But seven kinds of damage were being discarded with `lost = 0`** — table
headings, a script's code, a room's doors, which tiles are solid, an auto-tile
run with no room left, a sampled sound, a parallax layer. Each replaced by a
default, silently.

That number is not cosmetic. `lost` is what arms the whole safety net: it makes
the app say the file was damaged, **and it is what causes nbapp to keep the file
as it was beside the repaired one.** With `lost = 0` a damaged project was
normalised on open and written back on close with no note and no copy — the
original defect, reintroduced through new fields.

All seven now count, and a healthy project still reports zero, because a warning
on every open trains people to ignore the one that matters.

**One thing the test got wrong before the code did.** The end-to-end check
initially failed because nbapp backs a store up **once per process** — the copy
is meant to be "the version from before this session", and refreshing it on
every save destroys what it is for. The suite had already opened that path many
times. The test now simulates a fresh session explicitly, and asserts the guard
as well: a second open must *not* refresh the backup, or it overwrites the copy
the first one saved.

### Hardening — damaged bundles (2026-08-04)

A project is a directory of eight files. The read path inside each one is now
careful; the path that *reads the files* was not.

**A part that would not read was dropped in silence, and totally.** A corrupt,
truncated, wrongly-typed or unreadable `tables.json` took every table in the
project with it, reported nothing, and the next save wrote the emptiness back
over the file that still had them. Same for sprites, rooms, objects — any of the
seven.

Now counted, and the count reaches the same warning and the same backup as a
damaged record inside a file.

**Absent is not the same as unreadable, and only one is damage.** A part that
simply is not there is a kind the project never had — an older bundle saved
before that kind existed. Alarming about it would cry wolf on every migration,
which is how a warning stops being read. A part that is *there* and will not
open is data that has gone missing.

**A damaged marker raised.** `_bundle_read` is documented to return None for
"not a bundle" and a caller was written believing it; a corrupt `project.json`
threw `JSONDecodeError` straight past that caller. An unreadable directory is
not a bundle — saying so lets the open fail with a sentence instead of a
traceback.

Six checks, including the distinction between absent and unreadable, which is
the one that would have been got wrong by anybody fixing this in a hurry.

### Hardening — a save that fails (2026-08-04)

The bundle writer builds into a `.part` directory, moves the saved project aside
to `.old`, moves the new one in, and deletes `.old`. The design is right. Two
things it did not survive:

**A part failing part-way through** left the saved project untouched — correct —
but abandoned a half-written `.part` directory beside it, which looks like a
bundle to anything scanning the folder. Now cleaned up on the way out.

**A failed swap lost the project.** Between moving the saved copy aside and
moving the new one in, the project does not exist at its own path. If that
second rename failed it *stayed* that way: the author's work sitting in a `.old`
directory they have no reason to look in, and nothing at the path they saved to.

That is the exact window the whole dance exists to protect, and it was the one
case with no recovery. A failed swap now puts the old copy back; if even that
fails, `.old` is left in place rather than deleted, because a directory with an
odd name is a better outcome than no directory.

Verified by making each rename fail in turn: the project stays at its path,
whole, and still reads back. Reverting the restore turns the check red.

### The way in — 2026-08-04

The empty state still described the app as it stood before any of this work:
"…Compile & Export writes a .gba file." Export is no longer the shortest path
to a running game, and the sentence was teaching the slower one. It now names
Compile & Play. A third, quieter entry sits under the two buttons — **Learn to
make one** — opening lesson 1. The course was reachable only from a menu nobody
opens before they already have a reason to.

### The Serbian catalog was two alphabets — 2026-08-04

Serbian is digraphic: Latin and Cyrillic are both correct, and a product picks
one. This one had picked both. 2681 entries were Latin — including every piece
of desktop chrome (`Datoteka`, `Uredi`, `Sačuvaj`) — and 139 were Cyrillic. The
Cyrillic ones were the GBA SDK and Sequencer strings, the most recently added:
a Serbian user got `Dodaj` in the desktop and `Додај` in the app that shipped
last, in one interface.

Converted to Latin. The Cyrillic→Latin direction is exact and lossless (the
reverse is not — `lj`, `nj`, `dž` are ambiguous), so this is mechanical: the
30-letter azbuka, with the digraphs title-cased inside a word and upper-cased in
all-caps. 151 strings changed; placeholders and plural separators asserted
unchanged on every one.

**A mistake worth recording.** Mid-way I ran `git checkout -- lang_sr.json` to
undo a formatting misstep. The catalogs carry 261 entries that are not committed
yet, so that reverted the file to 2571 entries and destroyed them. Recovered in
full from `buildroot/output/target`, which the last build had mirrored from the
overlay — and *proved* recovered rather than assumed: the restored key set is
exactly the key set of a healthy sibling, and all 16 untouched siblings are
byte-identical to their own build copies once that day's edits are removed, so
the overlay had not diverged from the build and the mirror was exact. In a tree
where most work is uncommitted, `git checkout --` is a destructive command.

### A gate that could not go red — 2026-08-04

Adding three strings to the welcome pane put them in the code and in no catalog.
`i18n_check` reported **clean, 100%, all 17 languages**. It compares the
catalogs with each other, so a string missing from all seventeen is a string
they agree about perfectly.

`check_chrome()` had closed this for menus and app names after an entire app
shipped half-English. I started to close the same hole for body text inside the
same tool — and then found `tools/i18n_coverage_check.py`, which already reads
the code and demands a catalog entry for every user-visible string, and which
had been reporting `136 UNCOVERED` all along. I had spent the effort rebuilding
a tool that existed, with a worse filter than the one it already had. The
duplicate is gone; **look for the tool before writing it.**

What was actually missing was not the check but a way to *use* it. The tool has
a `--fail` flag that no one could turn on, because the count is never zero and a
gate that is always red is a gate that is always ignored. It now reads a
baseline of known gaps: `--fail` passes on recorded debt and fails on a **new**
omission, naming it. `--update-baseline` rewrites the file, and stale entries
are reported so it shrinks instead of rotting. The 7 gaps in this app are now
translated into all 17 languages; 136 remain recorded, 101 of them in
`sequencer.py` and 36 in `usbwriter.py`.

Proven able to fail in both directions before being trusted: a new untranslated
literal is named and exits 1, and a stale baseline line is reported.

This is the fourth thing found by asking what a green gate is structurally
incapable of noticing, after the data-safety write path, the Help coverage
tautology, and the struct-field count — and the first where the answer was that
someone had already asked, and I had not looked.

### Paying the translation debt, and what the render found — 2026-08-04

The 136 recorded gaps were not evenly distributed in importance. 36 were in
`usbwriter.py`, which is the app that erases disks, and they included the
sentences a person reads *while deciding whether to erase one*:

    Everything on %s (%s, %s) will be erased and replaced with %s.
    This cannot be undone.
    The stick is only part-written. Stopping now leaves it unusable
    until it is written again.

Shown in English to everyone not reading English. Those 36 are now in all 17
catalogs, along with the 10 remaining gaps outside `sequencer.py` (writer,
settings, widgetsettings, gbaworkspace). Placeholder sequences were asserted
identical per language before writing — `%d%%   %s of %s   %s/s   about %s left`
has six specs whose order the translation cannot change, because positional
`%` formatting has no way to reorder.

**Then I rendered it, and the render found three more the checker could not.**
Under Russian the app read `ШАГ 1  The image to write`. The step headings sit at
the call site of a helper:

    def _step(self, box, n, text):
        lbl = Gtk.Label(label=_t("STEP %d   %s") % (n, _t(text)))
    ...
        self._step(inner, 1, "The image to write")

The helper does the right thing — it calls `_t()` — but the literal is at the
caller, and a scanner looking for `_t("…")` sees nothing at either place. **158
call sites across 27 apps translate a variable this way.**

`i18n_coverage_check` now follows that shape: find every function that hands one
of its own parameters to `_t()`, then collect the literals passed to that
parameter at each call, matched by parameter *name* so keyword calls count too.
It immediately surfaced 10 more strings — including exactly the three the render
had shown, which is the point: the static check now sees what previously only a
screenshot could. Verified red by planting a step heading.

Debt: 136 → 96, and every one of the 96 is in `sequencer.py`, which another
session owns. Confirmed on screen in Russian and Japanese at the full 1024×740
budget: no English left, no tofu, nothing pushed off the bottom.

**The rule this is the third instance of: a coverage tool is only as good as the
shape it looks for.** First `is_prose()` rejected anything starting with `%` and
hid the desktop board. Then a dict of literals was invisible. Now a literal
handed to a translating helper. Each time the data was fine and the filter was
the bug.

### Rendering the SDK in Russian — 2026-08-04

Three findings, one of which was mine and not the app's.

**The resource browser was half-translated.** `KINDS` is a module-level tuple of
tuples, and its banner strings reach the screen through `_t(heading)` on a
variable unpacked from it — invisible to both the literal scan and the new
parameter tracing. `SPRITES`, `SCRIPTS` and `TABLES` happened to also appear as
literals elsewhere and so were translated; `TILESETS`, `SOUNDS`, `OBJECTS`,
`ROOMS` did not and were English. The sidebar therefore showed three translated
headings and three English ones in the same list. All six now translated, plus
`Tile set` and `New Tile Set`.

**The status banner was ellipsized into nonsense.** It was `EllipsizeMode.MIDDLE`
capped at 52 characters. MIDDLE is right for a path or a file name, where head
and tail both carry meaning; on a *sentence* it removes the verb, and Russian
rendered `выб… ровать»`. The English message is 63 characters and the longest
translation of it is 98 (Yiddish), so every language was truncated — English
included. Now `END` at 64 characters, so a long translation trails off instead
of losing its middle. The message also still said "Compile & Export it to a .gba
to play"; like the welcome pane, it was teaching the slower path. It now names
Compile & Play, which is also what made it fit.

**A hang that was the harness, not the app.** Rendering began blocking forever
in `_file_example`. The stack showed `_ok_to_discard` → `_confirm` →
`Gtk.Dialog.run()`: a modal "replace the open project?" with nobody to answer.
The cause was `os.environ.setdefault("NB_HOME", …)` in my own render script —
`guestrun.sh` already exports `/tmp/nb-guestrun-home`, so `setdefault` deferred
to it and every render shared one home. The first run loaded the example and
**autosaved** it, so every later run correctly asked before replacing it. The
app was right on all three counts (autosave, confirm-before-replace, and the
confirm being modal); only the harness was wrong. Worth recording because the
symptom — a render that used to work and now hangs forever — reads exactly like
a regression in the app.

Also measured while there: the window's minimum width is 606 px in English and
666 px in Russian, both well inside the 1024 budget. The 1247-pixel render is
`OffscreenWindow` taking the *natural* size, because `set_size_request` is a
minimum — not an overflow, and worth knowing before someone reports one.

### One concept, one word — 2026-08-04

Six languages were calling a tile more than one thing, inside this app, several
of them in adjacent labels:

    es   baldosa (12) · mosaico (7) · losa (2) · pieza (1)
    it   tessera (12) · tile (8) · casella (1)
    pt   peça (11) · mosaico (10)
    ru   плитка (15) · тайл (7)
    tr   karo (11) · taş (9) · döşeme (1)
    yi   פּליטקע (17) · קאַפֿל (8)

A Spanish reader met "Añadir una baldosa", "Mosaicos sólidos" and "juego de
piezas" and had to work out these were the same thing. All 25 SDK-owned tile
keys now use one word per language, chosen as the one already in the majority.

This could not be done by substituting words. `mosaico` is masculine and
`baldosa` feminine, so "Mosaicos sólidos" → "Baldosas sólidas" and "este
mosaico" → "esta baldosa"; Yiddish `קאַפֿל` is masculine and `פּליטקע` feminine,
so "דעם קאַפֿל" → "די פּליטקע" and "נאָך אים" → "נאָך איר". Every one was
rewritten as a sentence.

**Two measurement errors of my own, both caught before they became edits.**

*Substring matching invented an inconsistency.* Italian looked mixed because
the verb `mettile` ("put them") contains `tile`. Roots must match on word
boundaries.

*Substring matching also invented a homograph.* I recorded last iteration that
`TILES` and `1 tile` were shared with the desktop and therefore unfixable. They
are not: `TILES` matched the identifier `FILL_TILES` in widgets.py, and `1
tile` matched a **comment** in xrootbg.py ("A 1x1 tile is all a solid colour
needs"). Both keys belong to this app alone and are now unified with the rest.
That earlier entry was wrong and this paragraph replaces it.

What the ownership scan got RIGHT was more important: Spanish uses `ficha` for
the desktop board tiles, the 2048 tiles and the sliding-tile puzzle. That is a
different concept and correctly a different word — unifying it would have been
the defect, not the fix.

`tools/term_consistency_check.py` now holds the agreed term per language and
fails if a second one appears, naming the language, the stray word and the key
it is in. Proven red by putting "Mosaicos sólidos" back. It covers 25 keys, not
the 20 findable as single quoted literals: five are wrapped across two source
lines, and a check that silently dropped a fifth of its subject would be worth
very little.

### The rest of the nouns — 2026-08-04

Having done tiles, I screened the SDK's other concepts the same way. Four more
splits, in four languages:

    sprite  eo   sprajto (7) · rolfiguro / figuro (5)
    sprite  sr   sprajt (7) · figura (1)
    sprite  pl   duszek (8) · postać (1)
    frame   sr   kadar (6) · sličica (3)
    event   tr   olay (12) · etkinlik (5)

Gender again decided the rewrite rather than a substitution: Serbian `figura`
and `sličica` are feminine and `sprajt` and `kadar` masculine, so "ovu figuru"
became "ovaj sprajt" and "praznu sličicu" became "prazan kadar"; Polish "tę
postać" became "tego duszka".

**The Turkish one is the interesting case, and it is not a mistake.**
`etkinlik` is genuinely the right Turkish word — for a *diary appointment*. A
thing a game object reacts to is an `olay`. Both are "event" in English, so
they would share one catalog key and no catalog edit could satisfy both. The
SDK had already been given its own keys for this exact reason — `OBJECT
EVENTS`, `Add Object Event`, `No object events`, each with a comment naming the
collision — and that fix holds: no key is now shared between this app and
Calendar or Tasks. What had drifted was only the SDK's *own* strings, which had
picked up the calendar's word. Checking confirmed the split is complete;
`Select an event first` and `Select an event first.` are two separate keys, and
only the one with the full stop had gone wrong.

`term_consistency_check.py` now covers four concepts and 63 keys. Extending it
immediately paid: it found an Esperanto string my own manual pass had missed,
because that key is wrapped across two source lines and so is invisible to the
ownership lookup — the case the `also` list exists for. Event and frame were
each proven able to fail by planting the old word back.

**A screening pass needs a human at the end of it.** The automatic sweep also
reported `action/ja`, `sprite/ja` and `colour/zh` as split, with "dominant
words" like `中のアク` and `ライトを`. Japanese and Chinese have no spaces, so a
word-frequency measure built on `\w+` runs is meaningless for them; those
languages have to be read, not counted. Nothing was changed on that evidence.

### What reading the Japanese found — 2026-08-04

The screening sweep could not judge Japanese or Chinese, so I rendered the SDK
in both and read them. The sidebar, the room pane and the hints are clean and
consistent. Two things were not.

**A message naming a menu the reader cannot see.** "This game did not compile.
Build ▸ Build Details says why." — shown at the moment someone most needs to
find that menu — kept the English word `Build` before the arrow in **all
seventeen languages**, while the menu bar showed Erstellen, Compilar, Собрать,
ビルド, 构建. The message pointed at a menu that is not on screen.

The item half was wrong too, in a subtler way: it quoted a shortened name
invented for the message rather than the item's own text. German said
`Build-Details` where the menu reads `Einzelheiten zum Erstellen`; Serbian
`Detalji` where it reads `Pojedinosti izgradnje`. All seventeen are now built by
splicing the catalog's own `Build` and `Build Details` entries into a
per-language template, so the message can only name what the menu bar shows.

`i18n_check.check_menu_paths()` now compares every quoted menu path against
that menu's translation. It is precise about the negative case: Italian keeps
`File` as `File`, which is correct and is not flagged. Proven red by putting the
English word back.

**A dead key with a bug in it.** `Export failed — see Build ▸ Build Log` names a
menu item that does not exist — the Build menu holds Compile & Export…, What
This Game Costs… and Build Details…, and there is no Build Log anywhere. It is
also unreachable: no code uses that key. Worth checking liveness before
reporting a defect, and worth knowing the catalogs carry keys the code has
stopped using.

**Still open, and the source of both:** English itself uses two words for one
action. The menu is `Build`, the buttons are `Compile & Export` and `Compile &
Play`, and the messages say both ("Build finished", "Nothing to compile yet").
Japanese then inverts them — `Build finished` → コンパイル完了, `Compile & Play`
→ ビルドしてプレイ. The translations are downstream of an English split, so the
English is what has to be decided first. Left for its own pass.

### What the translations are allowed to do that English is not — 2026-08-04

Measured while checking voice: 110 Japanese strings end in `ください`, 80
Chinese contain `请`, 25 Korean `주세요` — "please", in none of the English.

**This is not the assistant's voice creeping in and must not be "fixed".** It is
the required register for a consumer interface in those languages; an English
rule against politeness markers would make every one of them read curtly wrong.
`tools/voice_check.py` now says so in its own docstring, because it is exactly
the tool someone would reach for to do the damage.

What IS drift is a translation that addresses the reader where the English does
not. Six were found and fixed by hand: Japanese `ましょう` ("let's") against a
plain SDK instruction, and "Ya puedes retirar la unidad" / "Ora puoi togliere
l'unità" / "Teraz możesz wyjąć nośnik" for the status statement "Safe to remove
the drive", and Russian "Вы вошли как" for the label "Signed in as". The
`ましょう` in the language app's exercises and in 2048 was left alone —
encouragement is a defensible register for a lesson and a game, and those are
not this app's to retune.

There is no automatic check for this, and there should not be: telling required
politeness apart from added familiarity needs the language, not a pattern.

### One action, one word: Build — 2026-08-04

English used two words for the single action that turns a project into a .gba.
The menu said **Build**; the buttons said **Compile & Export** and **Compile &
Play**; the messages said both ("Build finished", "Nothing to compile yet").
Every one of the seventeen translations inherited that split, and Japanese
inverted it — `Build finished` → コンパイル完了, `Compile & Play` → ビルドしてプレイ.

**Build wins, and not on the count.** Compile appeared in more strings (8 to 5),
but the deciding test is whether one word can fill every slot without
awkwardness. Build can: Build menu, Build Details, Build finished, Build &
Export, Build & Play, "did not build", "the build log". Compile cannot: "Compile
Details", "the compile log" and "the working files for the compile" all read
badly. Build also leaves the course prose ("what a build produces") already
correct, and — the point that matters most — keeps **the compiler** free to mean
the tool. "The compiler is not installed" is about arm-none-eabi-gcc and stays
exactly as it is; it was never part of the inconsistency.

**The translations were not made to say "build".** Each language needed ONE
word, not a literal rendering of the English one: Spanish "Compilar" used
throughout is right and "Construir" would be worse. Six languages were already
internally consistent (es, fr, it, pt, tr, ru). The other eleven each settled on
their own majority word — de Kompilieren, el Μεταγλώττιση, nl Compileren, pl
kompilacja, sr Kompajlirati, yi קאָמפּילירן, zh 编译, hi कंपाइल, eo Kompili —
except Japanese and Korean, which settled on ビルド and 빌드. 55 strings, plus the
failure message rebuilt in all 17 by splicing each catalog's own Build and Build
Details so the menu path can never drift from the menu again.

**A bug in my own gate, found by extending it.** `owned_by()` compared keys
using `json.dumps(key)`, which escapes non-ASCII — so every key containing an
ellipsis, an em dash or the ▸ arrow was compared against `"…"` and matched
nothing. The checker had been silently examining fewer keys than it reported for
*every* concept. With `ensure_ascii=False` the build concept went from 9 keys to
12 and frame from 9 to 10, and the extra key immediately exposed a Serbian
string still reading `×16 sličica/korak`. A gate that under-counts its own
subject reports CONSISTENT for the part it never looked at.

**A test that went red for the right reason.** `gbasdk_selftest` asserted the
empty-project card title contained "compile". That is a rename, not a
regression. Re-anchored on what the check is actually for — that the card
explains a game needs an object and a room — so the next vocabulary decision
does not break it.

Two dead-key observations while here: seven of the thirteen catalog keys the
rename touched are used by no code at all (leftovers naming the SDK's buttons
from other apps' text, and two sequencer strings). They were left alone. The
catalogs carry cruft, which is harmless but means "this string exists" is not
evidence anyone can see it.

### German was speaking to the reader three different ways — 2026-08-04

Running `gbasdk_selftest` under `NB_LANG=de` and `ja` was meant to catch the
bug class where code reads a widget's text back and silently no-ops in every
non-English language. It found none: the seven German and ten Japanese
"failures" are all the app producing correct translated text while the test
asserts an English substring. Worth knowing the non-English path is sound.

What it did surface was a line of German output — `Wähle zuerst ein Ereignis` —
in a register the rest of the OS does not use. Measured across the catalog, and
comparing like with like (full sentences only, because a count of "infinitive"
button labels says nothing about prose):

    412 German sentences
      367  impersonal   ("Alles, was darin nicht gespeichert ist, geht verloren.")
       27  formal Sie   ("Wählen Sie ein Paket aus der Liste")
       18  informal du  ("Wähle etwas zum Kopieren")

Three registers in one interface, and the two minorities were scattered across
apps rather than confined to one — a German user met "Wählen Sie" in Packages
and "Wähle" in the Finder. All of them are now impersonal: **0 formal, 0
informal, 2896 entries.**

**The measure had to be argued before it could be trusted.** Three of the
apparent hits were not defects:

* `Sie gehen verloren` is "**they** are lost", about the unsaved changes — not
  formal address. `Ihre %d Aufgabe` is "**its** tasks", about the list.
* `Suche leeren` is the **noun** "search", not the imperative "search!".
* Nineteen strings whose English genuinely says "you" are faithful, not drift —
  until you check what German does with English "you" elsewhere: 49 of 68 such
  sentences render it impersonally. That made the house rule explicit and the
  nineteen drift after all.

The first pass also missed a whole shape: `Nimm den Datenträger heraus und
starte neu` is a du-imperative with no pronoun in it, so a scan for `du`/`dein`
never saw it. Catching those needed a second pattern for imperative verb forms
after a full stop or an `und`.

This is catalog-only work — no source file changed — so it does not collide with
another session's edits.

### The other sixteen languages — 2026-08-04

Having found three registers in German, I measured the same thing in every
language that distinguishes familiar from polite address. Sentence-level, using
pronouns and possessives rather than verb morphology:

    es  informal    it  informal    pt  informal    pl  informal    nl  informal
    fr  formal      ru  formal      sr  formal      tr  formal      el  formal

**Every one of them is already internally consistent, and that they disagree
with each other is not a defect.** Spanish software conventionally addresses
the reader as *tú* and French as *vous*; the requirement is one register per
language, not one register across languages.

Greek was the only apparent exception, and it took three passes to establish it
was not:

* `σε` is the preposition "in/at" far more often than the informal pronoun, and
  `σας` is the polite possessive — so a pronoun scan reported 81 informal.
* Greek verb endings did no better: `ερωτήσεις` is the plural **noun**
  "questions", not a 2nd-person singular verb.
* Testing for singular-vs-plural imperatives finally worked — 149 formal
  against 6 — and four of those six were `Δεν άνοιξε η εικόνα`, third-person
  past ("did not open"), not an imperative at all. `διαλέξεις` is the noun
  "lectures".

One genuine string, `Άνοιξε τις Εργασίες για να προσθέσεις την πρώτη`, now
matches the other 149.

**A gate for German only, and deliberately not for the rest.**
`i18n_check.check_de_register()` holds patterns validated against every false
positive above: it fires on an imperative followed by `Sie`, on informal
pronouns, and on the pronoun-less du-imperative — and it does **not** fire on
"Sie gehen verloren", which is "they are lost". Proven in both directions.

The other sixteen are not checked. A generic register detector produced far
more noise than findings — six hits and one real in Greek alone — and a gate
whose output has to be re-litigated every time is worse than no gate, because
the standing temptation is to "fix" the false positives. Same conclusion as the
politeness measurement earlier today: some distinctions need the language, not
a pattern.

### Renaming a room broke every door into it — 2026-08-04

Back to behaviour rather than text. The project model has one shared reference
walker, `_walk_refs`, used by rename, by the delete confirm's "used %d times
elsewhere" count, and by the post-delete cleanup — deliberately one walker so
the three can never disagree. Its own comment records why it exists: rename
once knew about `play_sound` and nothing else, so renaming an object left
Create Instance, Destroy Object and If Collision pointing at a name that had
gone.

**Doors were added after that walker and never joined it.** A door is a
`warp` on a room holding `{"x","y","w","h","room": <id>}`. Measured, not
assumed:

    before:  door in rm_a leads to 'rm_b'
    _refs_to('room','rm_b') reports: 0        <- a door uses it
    after renaming rm_b to rm_cave:
      the door still leads to 'rm_b'
      doors leading nowhere: 1

So renaming a room silently broke every door into it, and deleting one said
"used 0 times" while a door used it. This is the seventh instance of the
session's recurring shape — a feature finished on one side of a seam — and the
first where the seam had *already been identified and centralised*, and the new
feature simply did not connect to it.

Warps now join the walker. Rename re-points them; delete blanks the target and
leaves `_was`, so the world map's existing "leads nowhere" count shows it and
the author can still see where it went.

**The harness had the identical blind spot.** `refs_in()` in the selftest
enumerates every id a project points at, and it did not know about doors
either — so the test that existed to catch this class could not have. Both are
fixed, with five checks over rename, the reference count, the delete cleanup
and the `_was` note. Reverting the one-line walker change turns all five red.

Also checked while there, so the remaining surface is known rather than
guessed: every parameter an action can take is one of `int`, `str`, a choice
list, `code`, or one of the four resource specs `obj`/`room`/`snd`/`spr` — all
four of which the walker covers. Tables and scripts are named only inside
free-form C in an Execute Code action, which a rename cannot rewrite and should
not try to.

### Changing a tile set's size repainted every room — 2026-08-04

Working through the model operations one at a time. Two were already sound and
one was not.

**Undo across a delete is correct**, and for the right reason: the snapshot is
a `copy.deepcopy` on both the save and the restore side. Had it been shallow,
`_forget_refs` mutating a nested warp dict after the checkpoint would have
corrupted the snapshot too and undo would have restored the broken state.
Verified end to end — deleting a room that a door led to and undoing brings
back the room, the door's target, and removes the `_was` note.

**Deleting a tile set renumbers rooms correctly**, including the awkward case:
a 16px tile occupies four hardware entries where an 8px tile occupies one.
Three sets of mixed size, a room painted with one cell from every id, delete
the middle set — `1,2` keep, `3..10` become 0, `11,12,13` become `3,4,5`.
Exactly right.

**Changing a set's SIZE did not renumber anything.** `ts["size"]` decides how
many combined entries the set occupies, so editing it shifts every later set
along — the identical corruption `_forget_tiles` exists to prevent, arriving
through the size combo instead of the delete menu:

    before  ts_A=(0,2)  ts_C=(2,5)   room holds 1,2,3,4,5
    ts_A 8px -> 16px
    after   ts_A=(0,8)  ts_C=(8,11)  room still holds 1,2,3,4,5

Every cell painted with ts_C's tiles now showed ts_A's artwork — "a level
painted in grass came back painted in whatever now sat at that index", which is
`_forget_tiles`' own comment describing the bug it was written to fix.

`_resize_tiles` now runs before the size changes, while the old geometry is
still readable. Cells after the set shift by the difference, which is exact.
Cells *inside* it move to the first hardware entry of whichever authored tile
they were showing — an 8px tile becoming 16px now spans four room cells where
it spanned one, so the painted map cannot be preserved exactly and naming the
right tile is the honest approximation. Grow and shrink round-trip: `1,2,3,4,5`
→ `1,5,9,10,11` → `1,2,3,4,5`.

Three checks, red without the fix.

**Both of today's behaviour bugs are the same shape**: a central helper written
to prevent a specific corruption, and a second code path that causes the same
corruption without calling it. Worth asking of any such helper — not only "is
it correct?" but "who else changes the thing it protects?"

### A table named "score" would not compile — 2026-08-04

Carrying the same question — *who else changes what this helper protects?* —
through the rest of the shared helpers. Five held, one did not.

**Verified sound, so the surface is known rather than assumed:**

* `_fix_start_room` has a second guardian: the loader independently validates
  `start_room` against the rooms it actually kept, and falls back to the first.
* `_uid` is used by the WAV import too, and a loaded project goes through
  `_safe_id`, which de-duplicates ids within each list.
* `_tile_pb_cache` cannot go stale **by construction** — the key is
  `(tuple(tile), scale)`, the pixel data itself — and `_restore` clears it
  anyway.
* Every undo checkpoint reaches a `commit`, via a helper where not inline (40
  checkpoints, 34 direct commits, the rest through `_frame_done` and friends).
* Every checkpointed mutation persists: the six that looked orphaned all save
  through `_frame_done` or `_room_touched`.

**The one that did not: generated C names.** `_unique_c` keeps tables and menus
from colliding with each other, and knew nothing about the runtime. A table's
symbol is minted from what the author typed, so a table called **score** emitted
`nb_score`, which `runtime.h` declares as `extern s32`:

    error: conflicting types for 'nb_score'; have 'const nb_row_score[]'
    runtime.h:203: note: previous declaration

That is what a beginner sees for naming a table "score" — and Score, Rooms,
Objects and Health are exactly what a first game's tables get called. Proven by
building a real ROM with the shipped toolchain, not by reading the generator.

`gbabuild.RESERVED_C` now holds all 37 `nb_*` identifiers the runtime owns, and
seeds the allocator. **The first attempt at this did nothing**, and the reason
is worth keeping: `_unique_c` was being handed the bare ident `score`, while the
`nb_` prefix is added at emission — so it compared `score` against a set of
`nb_*` names and found no clash every time. It now takes the actual symbol
forms a table becomes (`nb_row_%s`, `nb_%s`, `nb_%s_count`) and checks all
three. "score" builds as `nb_score_2`; "monsters" is left alone.

`gbaruntime_selftest` re-derives the `nb_*` names from the runtime sources and
fails if `RESERVED_C` has fallen behind — the list describes another file, so it
will drift the moment someone adds a global. Both checks proven red by removing
one name from the list.

**Not mine, and left alone:** `construct_all_host` reports usbwriter crashing on
`'UsbWriter' object has no attribute '_on_stop'`, and the coverage gate reports
one new untranslated string in the same file. Another session has been editing
`usbwriter.py`, `contacts.py`, `media.py`, `calendar.py` and `nbapp.py` within
the last half hour; both are that work in flight. Everything outside that file
is green.

### An accent in the dialogue silently lost a letter — 2026-08-04

A new angle: adversarial *author text*. Everything an author types that reaches
generated C — dialogue, table cells, names — goes through `_cstr`, which
escaped anything non-ASCII as `\xNN`.

**A C hex escape consumes every hex digit that follows it.** So a table row
reading `café2` was emitted as `"caf\xe92"`, which the compiler reads as the
single escape `\xe92` — out of range, and one character shorter than what was
typed. Measured against the real compiler rather than argued:

    'café2'  want 5 bytes, got 4   CORRUPT
    'über9'  want 5 bytes, got 3   CORRUPT      ('b' is a hex digit too)
    'café'   want 4 bytes, got 4   ok

The build still succeeded — it is a warning, not an error — so the only symptom
was the author's own words coming out wrong on the cartridge, and only when an
accented letter happened to be followed by `0-9` or `a-f`. `über9` lost two
characters.

Octal escapes fix it because they stop after three digits: `"caf\3512"` is
`\351` then `"2"`. Now zero warnings and every byte preserved, confirmed by
building a ROM whose table holds `café2`, `über9` and `naïve1`.

**The test asks the property, not the implementation.** It decodes the emitted
literal by C's own rules — greedy hex, three-digit octal — and requires it to
equal what the author typed, with a marker for an escape that swallowed
following text. That stays honest whichever escape form the generator picks
later. Reverting to `\xNN` fails it with `got b'caf\x92', want b'caf\xe92'
(escape swallowed text)`.

This is the same shape as the reserved-names bug an iteration earlier: author
text is data, and every path that turns it into code has to be checked against
what the *compiler* does with it, not against what looks reasonable.

### A column called "char" — 2026-08-05

Third pass down the author-text-into-code thread, through the two remaining
paths: identifiers, and numbers.

**`_c_ident` had no keyword guard.** It rewrites "Base HP" and "attack%" into
something C accepts, but a name that is *already* a valid identifier went
through untouched — including C's own keywords. A table column called **char**,
which is what a character table's first column gets called, emitted `char
char;`:

    error: two or more data types in declaration specifiers

`short`, `double`, `int` and `return` do the same. `class` is fine — it is C++,
not C — and GCC accepts the accented identifier from `café`, so those two are
left alone rather than "fixed" on suspicion. Keywords now get a trailing
underscore, and the existing `while fn in seen` loop already handles the case
where a real column is called `char_`: the fields come out `char_`, `char__`.

**Numbers were safe except at the top end.** Garbage coerces to 0 with no
injection and no crash — `abc`, `8px`, `5)`, `x + 1` all build, all zero. But a
value too *large* was passed through verbatim into an `s32`, where it wraps.
`add_score 999999999999` emitted exactly that, GCC said nothing (the only
"warning" in the log turned out to be the `-Wall` in the echoed command line),
and `budget_report` listed no problems. A number the hardware cannot hold was
being treated more leniently than a typo. `_int` now clamps to the s32 range.

Nine checks across the three, all proven red by removing the guard they cover.

**What this thread has produced, three for three:** every path that turns typed
text into code had a hole — string literals ate the following letter, table
names collided with the runtime, identifiers could be keywords. The common
error was reasoning about the *text* rather than about what the compiler does
with it, and the only reliable way to tell has been to build a real ROM and
read the error.

### The tool knew the game was too big and said nothing — 2026-08-05

`budget_report` measures a project against what the console has, and it is
accurate: 17 frames of a 64×64 sprite is 1088 tiles against a cap of 1024, and
it says so at exactly the right boundary, 129 instances over 128 likewise.

**Nothing ever asked it at the moment that mattered.** The only caller was the
"What This Game Costs…" menu item, which the author has to choose deliberately.
Its own docstring says a game that will not fit is "otherwise found out at link
time" — and in fact it is not found out at all: the over-budget project builds
cleanly, `check_project` returns no problems, and the card says **"Build
finished — 68.9 kB, saved in Documents."** The author gets a cheerful success
and a game that glitches on hardware.

Export already had the right shape for this — a card listing what will not work,
offering *Go Back and Fix* or *Export Anyway*, used for code the compiler could
not read. Budget overruns now join it, because they have the same consequence:
it builds, and it does not do what was meant. Three checks; without the change
the test sees the "Build finished" card and goes red.

**And the string I added would have shipped in English.** `i18n_check` rejected
it with SPEC DRIFT in all seventeen languages, which turned out to be a real
runtime property, not a checker quirk:

    "%(what)s: %(used)s, and the console…"   -> nbi18n REJECTS, falls back to English
    "Every tile in this set is cropped to %(new)d…" -> translates fine

**A string that STARTS with a named placeholder is rejected by nbi18n**; the
same construct mid-sentence is accepted. Reworded to begin with a word ("Too
big: %(what)s uses…") and it translates. That is also the RTL rule from the
i18n work — a leading placeholder filled with Latin text forces an LTR base —
arriving from a completely different direction.

While there: `_show_budget` printed its line names and notes raw, so that pane
showed "Sprite tiles" and "objects created while playing count too" in English
under translated chrome. They are catalog keys now, in all 17.

### The compiler caught the classic C mistake and nobody passed it on — 2026-08-05

Carrying the previous question — *is this diagnostic consulted at the moment it
matters, or only when someone goes looking?* — across the rest of them.

**Two were already fine**, which is worth recording so the surface is known:
a sprite painted in more colours than a bank holds, and a door leading to a room
that no longer exists, are both reported by `check_project`, which export does
consult. Their wording is good too — "it is painted in 21 colours, and a Game
Boy Advance sprite can hold 15. The 6 after the first 15 will come out as
holes."

**One was not: warnings from a build that SUCCEEDED.** The generated C compiles
clean — that is a property the integration test holds to — so a warning in the
log comes from an Execute Code action, which is the author's own C. Two
examples, both building happily and saying nothing:

    int unused_thing = 5;   -> warning: unused variable 'unused_thing'
    if (nb_score = 3) { }   -> warning: suggest parentheses around assignment
                                        used as truth value

The second is the classic C mistake — `=` where `==` was meant — in an app
whose Help pane teaches C. GCC caught it, the log recorded it, the card said
"Build finished — 34.9 kB, saved in Documents", and the log is behind a menu
item. The success card now says how many remarks there were and offers *Show
the Details*, the same pattern the failure card already used.

`_warning_count` matches `": warning:"` rather than the word anywhere, because
a build log echoes its own command line and `-Wall` in that line is not a
warning — a confusion that produced a wrong reading earlier in this same
session, so the test for it is written down.

**A near miss worth recording.** My first attempt at this measured nothing: the
fixture used `{"kind": "code"}` and the real action is `execute_code`, so the
author's C was never emitted and the build was warning-free for the wrong
reason. It looked like a clean negative result. The check that caught it was
asking whether the code appeared in the generated output at all — the same
discipline as counting the files a glob matched before trusting a count of
zero.

### Two clean bills and one column that went ragged — 2026-08-05

**Accelerators: clean.** Enumerated from the live menus rather than by grepping
`_acc(...)`, because seven of them are minted in a loop (`Ctrl+%d` over `KINDS`)
and a regex over the source misses every one. Fifteen distinct keys, and the
single apparent duplicate — `Ctrl+B` in both File and Build — is the same label
bound to the same callback, offered in two places on purpose.

**Esc: clean.** The OS rule is that Esc only ever leaves, never destroys. The
app's one Esc binding dismisses a result card with `CANCEL`, and the menu binds
it to File ▸ Close, which autosaves. Nothing destructive.

**The costing pane went ragged in Japanese**, and that one was mine. Translating
its line names an iteration ago put CJK text into a `%-22s` column: padding
counts *characters*, and a monospace terminal draws a CJK glyph two columns
wide, so `スプライトタイル` (8 characters, 16 columns) and `背景タイル` (5 and
10) were padded to the same character count and different widths. Latin and
Cyrillic were unaffected, which is why it did not show until Japanese was
rendered.

`_pad()` now counts columns via `east_asian_width`. Japanese, Chinese and German
all line up; a Latin name pads exactly as `%-22s` did, and a name already wider
than the column is not truncated. Three checks.

Worth noting the shape: **the defect was introduced by a fix**. Making the pane
translatable was right, and it moved a latent layout assumption from
never-exercised to visible. A change that is correct in one dimension can be the
first thing to exercise a weakness in another.

**Not mine, and left alone:** `novel.py` has three new untranslated strings and
`settings.py` does not parse — `SyntaxError: expected 'except' or 'finally'
block` at line 3104. Both files were edited by another session minutes ago
(01:40 and 01:55). Every GBA module compiles, has no new untranslated strings,
and no GBA app crashes.

### Rendering tonight's own changes in Japanese — 2026-08-05

Having introduced a CJK layout defect with a fix, I rendered everything changed
tonight in Japanese rather than assuming the rest were fine.

**The welcome pane, the main window and the sidebar are correct.** Toolbar
`ビルドして書き出す` agrees with the `ビルド` menu — the terminology decision
holds — the status banner shows in full rather than ellipsized, and every
sidebar heading is translated. The welcome pane's body wraps mid-word, which is
correct Japanese: there are no word delimiters and Pango applies kinsoku.

**The fixed-width class is closed OS-wide.** Two `%-Ns` string fields exist in
the whole desktop: the costing pane, fixed last iteration, and a table in
`nbaudio.describe()` — which no app calls, holds ALSA device ids, and is a
command-line diagnostic. Nothing else shares a monospace grid with translated
text.

**One error found, and it was in my own translation.** Rendering the export card
showed `スプライトタイル は 1088 使い` — a space before the particle は, which
Japanese does not take. The same mistake was in Chinese: `精灵图图块 用了`, a
space between a noun and its verb. Both came from writing the template around a
`%(what)s` placeholder and leaving the spacing that reads naturally in English.
Fixed, re-rendered, and the rest of tonight's CJK strings scanned for a stray
space between two CJK runs — none.

That is the third CJK spacing slip of the session (the first two were a Latin
space after `。`), all from the same cause: composing a sentence around a
placeholder and carrying English spacing across. Worth checking by eye every
time a CJK string is built from parts, because no checker in the tree looks for
it and the text is perfectly valid to a parser.

### `hspee = 2` compiled fine and the object sat still — 2026-08-05

`check_project`'s docstring promised this exact diagnostic: *"obj_player · Step ·
line 3 — hspee is not a word this code knows"*. It never fired.

The error message exists, in the read path — and is unreachable. `_gml_user_vars`
gives a var[] slot to **every** identifier it does not recognise, which is
deliberate and correct: it is what lets an author write `wobble = 7` without
declaring anything. But it also means a misspelt built-in is indistinguishable
from a new variable. `hspee = 2` compiles to `self->var[0] = 2`, the object never
moves, and nothing anywhere says so. Both directions were silent — `x = hspee`
too.

Guessing at typos is the wrong instrument. What is *certain* is this: a slot
lives on the instance and nothing outside the object can reach it, so a variable
**set and never read**, or **read and never set**, does nothing in every case.
That is a fact about the language, not a heuristic, and it catches the typo as a
side effect:

    obj_player sets hspee but never reads it, so it has no effect. Did you mean hspeed?
    obj_player reads nosuchthing but never sets it, so it is always 0.

The near-miss is offered only at edit distance **one**. Two edits reaches far
enough to propose `grav` for `drag`, which is a different idea and a worse guess
than saying nothing.

**The suites immediately found two cases where the rule was wrong**, which is
the whole reason to run them before believing a new check:

* **Show Menu writes its variable from the engine**, not from a script line, so
  `choice` looked read-only. Menu now counts as setting its variable.
* **"Show C" previews one event**, often one still being written, where a
  variable set in a sibling the author has not reached yet is not a mistake.
  The audit is off while previewing and on for the pre-export gate — the two
  have different jobs.

A third apparent failure was the check being right: a Help fixture read `hp` via
`if_var` and never set it, which in a real game is always false. The fixture now
sets it.

Six checks, red without the audit. The docstring now describes what the tool
actually does.

### `alarm[9] = 30` wrote past the end of the instance — 2026-08-05

Continuing through the script language, and the variable audit from the last
iteration immediately paid for itself twice: once by catching a real bug, once
by being wrong in a way that pointed at another.

**It was wrong about `alarm`.** `alarm[0] = 30` was reported as "reads alarm but
never sets it" — a false positive on correct code, introduced by me an hour
earlier. Two causes, both fixed: `alarm` is a built-in *array* and was being
given a variable slot it never needed, and the read/write split did not know
that `name[i] =` sets `name`. The bracket now gets walked to its match before
deciding.

**Following that led to the real one.** An instance has `NB_MAX_ALARMS` = **4**
alarms. `alarm[9] = 30` — which is what anyone who assumes there are ten will
write — emitted `self->alarm[9] = 30`, a write nine words past a four-word
array, straight into the `var[]` slots that follow it in the struct. The build
succeeded. GCC does say something:

    warning: array subscript 9 is above array bounds of 's32[4]' {aka 'int[4]'}

which names a C type the author has never seen, in a log that until this session
nobody was told existed. Now:

    obj_player · Step · line 1 — there is no alarm 9; an object has 4,
    numbered 0 to 3

Only a *literal* index is judged: `alarm[i]` cannot be known here, and
`alarm[1 + 2]` is left to the compiler rather than half-evaluated. A bare
literal arrives as `3` but anything else is parenthesised, so `-1` reaches the
check as `(-1)` — which is why `alarm[-1]` was accepted on the first attempt and
needed the parentheses peeled before matching.

The generator now carries a copy of a runtime constant, so `gbaruntime_selftest`
holds the copy to the original and fails if `NB_MAX_ALARMS` moves. Same shape as
`RESERVED_C`. Six checks plus the drift gate, all proven red.

### The messages that explain a broken game were English-only — 2026-08-05

Three findings in the script compiler, then the thing they all had in common.

**A construct the language does not have now names the one it does.** `for (i =
0; i < 3; i = i + 1)` parsed as a call to a function named `for` and was
reported as "expected ) here, found =" — a complaint about a bracket, when what
the author needs is which loop to write. `SCRIPT_NOT_HERE` covers the words a C,
JavaScript or Python habit reaches for: `for`, `do`, `switch`, `case`, `break`,
`continue`, `function`, `def`, `elif`, `print`, each answered with what this
language has instead. They are also excluded from the variable scan, which was
reporting `function` as a variable that is never set, ahead of the message that
explains the mistake.

**An error in the right-hand side of an assignment was thrown away.** The
statement parser tries an assignment and, on failure, backtracks to read the
line as a bare call — correct recovery for `instance_destroy()`, and wrong once
the `=` has been consumed. `hspeed = abs(1, 2)` reported "= does not belong
here", a complaint about the one part of the line that was right; the real error
— a plain wrong argument count — was discarded. Past the `=` it is certainly an
assignment, so the error is now raised rather than swallowed.

**And the wording of the count itself.** `instance_destroy(1)` said "needs 0
values"; it now says "takes nothing inside its brackets". The `%s` plural slot
in "%d value%s" became two whole sentences, which is this codebase's convention
because nbi18n hands back the English whenever a translation's placeholders
differ from the source's.

**What they had in common: `gbabuild` had no i18n at all.** Twenty-three problem
and error templates, none of them a catalog key, none translated — the messages
an author reads *when their game does not work*, and the only English left in
the app after a night of closing these gaps. The module had no `_t` import.

It has one now, with a fallback so a headless tool without the catalogs still
runs, and all 23 templates are translated into the 17 languages. Wrapping them
was done through the AST rather than by regex: several are implicit multi-line
concatenations where the `%` binds to the whole joined string, and `_t(` has to
open before the first fragment and close before the operator.

    de: es gibt keine Funktion namens foo
    ja: アラーム 9 はありません。オブジェクトには 4 個あり、0 から 3 までです

### A display helper that never calls the translator — 2026-08-05

Having found that `gbabuild` had no i18n at all, I checked whether any other
module had the same shape: user-visible text in a file that never imports the
translator.

**Most candidates were false alarms**, and saying why is worth as much as the
finding. A first pass counted "prose-looking literals" and accused thirteen
modules — because it was counting *docstrings*. Excluding those left six, and of
those, `nbaudio`, `nbjobs` and `nbsynth` turned out to be command-line
diagnostics no app displays, and eleven of `nbgame`'s sixteen go to `_log()`.
`nbpicker` was clean outright: all twelve of its strings are already catalog
keys, because `nbi18n` monkeypatches `set_text` and translates them even though
the module never mentions the translator.

**The real finding was in the checker.** `nbgame` shows its messages through

    def _set_banner(self, text):
        self._banner.set_text(text)

and the coverage scanner traced a parameter only into `_t()`. A parameter handed
straight to a **widget's** text is display text just as surely, so the tracing
now follows any of the calls it already knows about — `set_text`, `set_label`,
`set_markup`, `set_title`, `set_tooltip_text`. That immediately surfaced **31
strings the tool had never been able to see**, in six apps.

Three of them were mine to fix and are now translated into all 17: the
emulator's two failure banners, and the file picker's "No files here that this
app can open." — which sat beside two sibling messages that *are* translated,
because those two happened to already be keys.

The other 29 belong to apps another session is working in. They are recorded in
the baseline with a note saying what happened, because a file that grows by 29
overnight otherwise reads as someone having been careless: **nobody added a
string — the scanner learned to see what was already there.** Reverting the
widening makes 13 of them vanish again, which is the evidence that the change is
load-bearing rather than incidental.

Still not visible to it: a literal assigned to a local variable and then shown,
which is how `nbpicker` builds its empty-state message. That needs constant
propagation inside a function, and is the next thing worth doing to this tool.

### The last shape the scanner could not see — 2026-08-05

A literal assigned to a local and then displayed. `nbpicker` picks its
empty-state message in an if/elif/else and shows the variable, so none of the
three was a literal at a display call — and two of them happened to be catalog
keys anyway, which left the third untranslated *between two translated
siblings* with nothing to indicate it.

`_via_local_strings` collects every string literal assigned to a name inside a
function and, if that name later reaches a display call, treats all of them as
shown. **Every** assignment, not the last: each branch really is displayed on
its own path.

Six more strings became visible. Three were in this app's reach and are
translated into all 17 — the emulator's "No controller detected. Keyboard:
arrow keys, Z, X." and "The log is empty.", and the picker's missing
empty-state. One belongs to the installer and is recorded.

**The other two were my new check being wrong**, which is worth more than the
finding. Illustrator's swatch template —

    <span foreground="#C8341E">●</span>  <span foreground="#6E695E">%s</span>

— has no words of its own; the sentence is whatever fills the `%s`. Offering it
for translation asks a translator to render a colour. `is_prose` now strips
markup tags before deciding whether anything alphabetic remains, and prose that
merely *contains* markup ("Use the <b>arrow keys</b> to move.") is still seen.
Left as a baseline entry it would have been noise that trains everyone to
ignore the tool.

Also checked and clean, rather than assumed: **no `Gtk.Dialog(title=...)`
literal anywhere in the desktop is missing from the catalogs**, so the
constructor list not naming `Dialog` costs nothing today.

The scanner now follows text into `_t()`, into a widget-text call, through a
function parameter, and out of a local variable. What remains invisible is text
assembled from fragments — `txt += " +%d more"` — which cannot be a catalog key
in that form anyway and would need the sentence rewritten, not the tool
improved.

### Two diagnostics disagreeing about the same fact — 2026-08-05

The palette allocator is correct at its limit: sixteen sprites each get one of
the sixteen colour sets, and a seventeenth is reported — "the game has run out
of sprite colour sets (there are 16), so this sprite will be drawn in another
sprite's colours". It folds onto set 0, which is the only thing it can do.

**The costing pane said the project was fine.** Its line read `16 / 16`, not
over, for a project with four sprites that will render in someone else's
colours — because it was counting `pal["used"]`, the sets that *exist* and which
is capped at sixteen, rather than the number the project *asked for*. A budget
line that can never exceed its own cap is not a budget line.

    17 sprites -> budget 16 / 16  over=False   problems=1     (before)
    17 sprites -> budget 17 / 16  over=True    problems=1     (after)

That mattered more than the arithmetic: the two diagnostics contradicted each
other, and the reassuring one is the easier to reach — the costing pane is a
menu item, the problem list appears only when exporting. Since the previous
iteration put budget overruns *into* the export gate, the contradiction would
also have meant one gate silently disagreeing with the other about whether to
stop.

The invariant is now a check in its own right: **for the same project, "the
costing pane reports something over" and "the problem list is non-empty" must
agree.** It runs over 8, 16, 17 and 20 sprites and is the check that would catch
the next such divergence, whichever side moved. Both new checks go red when the
demand count is reverted to the supply count.

A fixture mistake worth recording, since it nearly hid all of this: my first
attempt gave each sprite fifteen distinct colours, which with transparent is
sixteen and over the per-sprite limit, so *every* sprite reported a problem and
the bank arithmetic was never reached. A test project has to be legal in every
dimension except the one under test.

### The lines only some projects ever see — 2026-08-05

Checking the rest of the costing pane after the colour-sets fix. The arithmetic
is right everywhere: background tiles go over at exactly the point they should
(511 authored tiles fill the 512-entry charblock, because index 0 is the
reserved empty tile), objects-in-a-room at 129, sampled audio past 32 MB.

**What was wrong was translation, in the lines a plain project never shows.**
"Sampled audio", its note "16 KB per second", and the per-asset details
("%.1f seconds", "%d tiles at %dx%d", "%dx%d, %d frames", "set %d") only appear
when a project *has* that kind of content. Every line name I translated two
iterations ago came from a report I ran on an empty project, so these six were
never in front of me. They would have shown in English inside an otherwise
translated pane.

`%dx%d, %d frame%s` also carried the plural slot that nbi18n rejects, and is now
two whole sentences like the others.

**The test for this took three attempts, and the wrong ones are instructive.**

*First:* compare every string the report emits against the catalog. It failed on
`16x16, 2 frames` and `s` — because details are translated *inside* gbabuild and
then filled with numbers and asset names, so the finished string is not a key
and never will be. A test must know which strings are supposed to be keys.

*Second:* require every prose literal in `budget_report` to be wrapped in
`_t()`. That failed on the line names — which are deliberately *not* wrapped,
because the pane translates them at display. Wrapping is not the requirement.

*Third, and correct:* every prose literal must be **either wrapped or a catalog
key** — the two routes this module actually uses. Anything else reaches a
reader in English. That version passes, and goes red for both kinds of damage:
renaming a line so its key no longer exists, and removing a `_t()` wrapper from
a detail. The second is the one the earlier version could not see, because a
string that stops being wrapped simply leaves the set being checked.

### A coupling my own change nearly broke — 2026-08-05

Looking for more conditional text, I found `_failure_reason`, which turns a
build log into the one sentence an author reads when a build stops. It picks
that sentence by matching **English phrases in the log**:

    if "isn't installed" in low:                  -> The compiler is not installed.
    if "could not write generated source" in low: -> The working files ... could not be written.

Two iterations ago I made `gbabuild` translate its messages. Those three log
strings come from `gbabuild`. Had the AST wrapper caught them, every build
failure would have fallen through to the generic "The compiler stopped part-way
through" in all seventeen languages — including in English, since the phrases
would still have matched only by luck.

**It did not happen**: the wrapper targeted `_problem` and `GmlError` calls, and
these are plain `return False, None, "..."` values. Verified by driving
`_failure_reason` with the exact strings `gbabuild` returns — toolchain missing,
generator failed, source unwritable, disk full, and unrecognised noise all map
to distinct, correct sentences, including the two that differ only by errno.

But nothing recorded *why* those three must stay English, and the next person to
run a translation pass over the module would have had no reason to spare them.
Each site now says so, and a check holds the two halves together: every phrase
`_failure_reason` matches must still be produced by `gbabuild`. Wrapping one of
them in `_t()` — exactly the edit that would look like tidying up — turns it
red.

This is the third time this session that a *correct* change created a hazard
somewhere else: translating the costing pane exposed a CJK padding assumption,
putting budget overruns in the export gate made a disagreeing diagnostic
load-bearing, and translating build messages nearly cut the failure sentences
loose. The pattern is worth naming: **when text becomes translatable, look for
code that was reading it.**

### A bug I created by translating — 2026-08-05

Acting on the pattern named last iteration, I swept the desktop for code that
matches English text. Two shapes, and the results were opposite.

**Reading text back from a widget: zero.** The recorded bug class —
`combo.get_active_text()` returning the *translation*, which once made
paragraph styles a silent no-op in every non-English language — is fully gone.
Nothing anywhere compares a widget's text to a literal.

**Matching a phrase inside a message: thirteen sites, and one was live.**
`video.py` matches ffmpeg's output and `gbasdk` matches gcc's; both are external
tools that speak English, and both are fine. `usbwriter` was not:

    raise OSError(_t("The stick ran out of room before the image finished."))
    ...
    if "ran out of room" in message:

The raised message is built with `_t()`. That match worked only while those
strings were **missing from the catalogs** — and I added them, in this session,
about six hours ago. From that moment a German reader whose stick filled up was
told the stick may have been unplugged. Demonstrated rather than reasoned:

    the raised message, in German:
      Auf dem Stick war kein Platz mehr, bevor das Image fertig war.
    old match  "ran out of room" in message -> False
    new match  kind == "_OutOfRoom"         -> True

`nbjobs.JobError` already carried a `kind` field for precisely this, and its
docstring already said `message` "is NOT fit to show a person as-is — apps map
it to their own sentence". The mapping just did it by the words. Two small
exception classes now carry the meaning, and a class name is the same in every
language.

**One of the two new checks would not have caught it.** "The sentence chosen
does not depend on the wording of the error" passes on the broken code, because
the broken code is *consistently* wrong — every message falls to the generic
sentence, so changing the wording changes nothing. The check that catches it is
the blunter one: three different failures must produce three different
sentences. A test for invariance is worth little if the thing it watches is
invariantly broken.

### A lesson that could only be finished by writing C — 2026-08-05

The Help course's twenty checkpoints were already tested both ways: red on an
empty project, green on a full one. That is a two-point test, and a checkpoint
that returned `bool(project)` would pass it — the same weakness as the
invariance test found in the last iteration.

So I measured discrimination instead: remove one kind of content at a time from
the full project and record which checkpoints change their answer. All twenty
react to something, so none is a constant. But the map showed one oddity:

    uses_menu  <- scripts

Show Menu is an **action**. The checkpoint searched only for `rt_menu_open(` in
code, so the menu lesson could be completed by writing C and **not** by adding
the action — the drag-drop path this tool exists to offer, and the one the
checkpoint's own words promise: *"A script or action opens a menu."* The action
was added after the checkpoint and never reached it. Its sibling `uses_glide`
already counted both, which is what the fix now does.

**The audit that found it was behavioural; the textual one found nothing.**
Comparing each checkpoint's label against whether its body mentions
`_all_actions` or `_code_blocks` flagged seven — and all seven were noise,
because `_code_blocks` *is* "the code inside Execute Code actions", so a
checkpoint whose label says "action" and whose body reads code blocks is
correct. The keyword audit could not know that. Asking what each checkpoint
actually reacts to needed no such knowledge.

Three checks now cover the menu lesson: the action completes it, the C call
completes it, and neither leaves it uncompleted. Removing the action count turns
the first red.

### Messages nobody could ever see — 2026-08-05

Applying the discrimination measure to the generator's own diagnostics: it can
emit nineteen distinct messages, so I tried to trigger each one. Three could not
be reached at all, and two more were buried.

**Unreachable.** `1 = 2` and `abs(1) = 2` both reported "= does not belong
here", a complaint about the one part of the line that was right. The statement
parser tries an assignment and backtracks to "bare call" on failure, and the
guard I added earlier read `if self.pos > save and self._committed(save)` —
but `_lvalue` fails **without consuming anything**, so `self.pos == save` and the
short-circuit backtracked before the check ran. `_committed` now also looks
*ahead* for a top-level assignment operator: an `=` before the statement ends
means an assignment was meant, however badly the left side parsed. Bare calls
still backtrack, because `instance_destroy()` has no assignment operator in it.

**"global.%s is never set anywhere"** could not fire either. `_gml_globals`
collects every `global.NAME` mention, read or write alike, so the "is it known?"
test the message hung on always passed. Assignments are now tracked separately —
the same shape as the instance-variable audit, one scope up, and the same fix.

**Two more were buried by noise I introduced earlier tonight.** The variable
audit was treating `global` as a variable called "global", and `foo` in
`foo[0]` as a variable rather than an array. Both produced a "never set" note
*ahead* of the message that actually explained the mistake — "global. must be
followed by a name", "unknown array foo". A namespace prefix, a member name
after a dot, and an identifier followed by `[` are now all excluded. The
did-you-mean suggestion is also off for names under three letters, because one
edit away from a two-letter name is half the alphabet: a variable called `c` was
being asked whether it meant `x`.

Eight checks, and the ones covering the parser change go red when the guard is
disabled.

**The measure that found all five was the same as the last iteration's**: not
reading the code and judging it, but trying every branch and seeing which never
answers. A message that cannot be reached is a promise the tool does not keep,
and it is invisible to every test that only checks the messages it *does*
produce.

### Enumerating the UI the same way — 2026-08-05

The app can show five cards, thirty distinct flash messages and two confirms.
Listing them was worth doing on its own, before trying to reach any.

**Two messages for one situation.** `Select an event first` and `Select an event
first.` — the same sentence, differing by a full stop, reached from two places
and living as two catalog keys. German had already drifted into two wordings for
them (*auswählen* and *wählen*), so the same failure said different things
depending on which pane you were in. Merged onto the one that matches its
sibling `Select a row first`.

**A plural hack in a shape the earlier sweep did not look for.** `Playing, with
%d thing(s) that will not work` — parentheses rather than the `%s` slot I had
been splitting, so it passed every check. German rendered it *"mit %d Ding(en)"*,
and languages that do not form plurals by suffix have no way to render it at
all. Two whole sentences now, like the rest.

**The two failure cards had no test.** Of the five cards, three were covered.
The two that were not are the failure paths — a build that fails, and a build
that works but cannot be written out — which are exactly the ones nobody
exercises by hand. Both are reachable, and they are now checked to be reachable
*and distinct*, because "could not be built" and "could not be saved" send an
author to different places.

The pattern holds from the last two iterations: **listing everything a component
can say, and then trying to reach each one, finds things that reading the code
does not.** Here it was not unreachability but duplication and a plural shape
that no pattern-based check was looking for.

Not mine: `finder.py` gained an untranslated string three minutes before this
run and is being edited by another session as I write. Recorded in the baseline,
left alone.

### What the runtime tests do not reach — 2026-08-05

Enumerating the runtime the same way: 215 functions, of which 167 are the `rt_`
API. The selftest names 28 of them.

That number on its own means little, and saying why matters: most of the API
touches hardware registers and cannot run on a host at all — the integration
test compiles every one of them, which is the coverage they actually get.
Separating them properly gives **51 that touch hardware and 116 that are pure
logic**, and of the pure ones **102 were never exercised**. (First written
as 46/121 from a looser regex that counted a few register-writing helpers as
pure; corrected here rather than left standing.) Those are the real
gap: nothing stopped them being run on the host except nobody having done it.

Three of them carry arithmetic everything else leans on, and are now tested:
`dir_of` and `hypot_i` — how chase, nearest, and every "move toward" behave —
and `rt_random`, which is every bit of variety in a game. All three are correct:
the eight cardinal and diagonal directions are exact, `hypot_i(3,4)` is 5 on the
nose, the generator covers its whole range without a value dominating, a range
of zero returns zero rather than dividing by it, and the same seed replays.

**My first assertion was wrong, and the way it was wrong is the point.** I
sampled sixteen angles, saw an error of zero, and asserted zero for all 256.
Over the full circle the worst error is 1 — and that 1 is the *harness*:
`(int)(1000·cos θ)` does not sit exactly on the ray for most angles, so the
input is already off before `dir_of` sees it. The exact-direction test proves
the function itself: all eight directions a player can actually hold come back
exactly right. The circle sweep now asserts what is true — within one step of
256 — and says why it cannot assert more.

An assertion derived from a sample is a guess about everything it did not
sample. This one happened to be a guess about the test rather than the code,
which is the harmless direction; the same mistake in the other direction is how
a real defect gets asserted as correct behaviour.


### Collision, and the number under every score — 2026-08-05

Next off the untested list, chosen by what a game cannot survive being wrong:
`rt_bbox_at` with `rt_meeting` / `rt_place_meeting`, and `int_to_str`.

**The collision maths is exact.** Two 16-pixel boxes come apart at exactly 16
pixels, and over every offset in a 41×41 grid the overlap region is 961 hits —
which is not an observation but the prediction, 31×31, from boxes that overlap
for offsets −15..15 on each axis. Asserting the count rather than "some hits"
is what makes it a measurement of the box arithmetic instead of a check that
the function runs. `rt_meeting` and `rt_place_meeting` are the same loop
written twice, and they agree at every one of those offsets.

`int_to_str` is correct at its edges, and they are tight ones. The widest thing
it can print is the most negative s32 — sign plus ten digits, 11 characters —
into a 12-byte buffer, with the terminator taking the last byte and nothing to
spare. Canaries either side of the buffer confirm nothing is written past it,
including on the padded path where a caller asks for ten leading zeros on a
negative number. Anything that widens the output has to grow the buffer.

**One real defect, found and fixed: an over-large inset moved the hit box off
the sprite instead of collapsing it.** Collision insets are per-edge `u8`s and
nothing checked them against the sprite's own size; the generator clamps each
to 0..64, which on an 8×8 sprite lets a facing pair cross. The runtime's repair
was `if (*r < *l) *r = *l`, which keeps the box at the *left* inset — so with a
64px inset the hit box sat 64 pixels clear of the artwork. That instance would
collide with what it is nowhere near and pass through what it is touching.
`rt_bbox_at` now clamps the near edge to the sprite's far edge first, so the
box always collapses *inside* the sprite. Every inset pair the generator can
emit, on all four sprite sizes, now stays in bounds — 14,120 of those 17,956
combinations take the collapse path, so the fix is exercised rather than merely
compiled. It is currently unreachable from the UI, which never exposes the
control; it was worth fixing because the object editor will expose it.

**The best result here is which test caught what.** Sabotaging one edge of the
overlap comparison from `<=` to `<` left `first_clear` reading 16 — the test
that knew the right answer was looking at the wrong side of the box, and passed.
The symmetry check, which knows no expected values at all and only asserts that
if A meets B then B meets A, caught it immediately: 62 asymmetric offsets.

An invariant does not need to know the answer, which is exactly why it keeps
working in the places you did not think to check. When a function has a
property that must hold everywhere, assert the property and sweep the domain;
save the hand-picked expected values for the boundaries you specifically care
about.

### The frame loop was quietly undoing what the book taught — 2026-08-05

Refreshing the Part III matrix turned up the worst class of defect this project
has produced: **an API that is correct, documented, taught with a worked
example, covered by a passing test, and cannot work.**

`rt_flush()` — the per-VBlank flush — ends with `REG_DISPCNT = g_dispcnt`, and
`g_dispcnt` was a constant fixed at `MODE0` that nothing ever changed. The
"Rotating background" recipe opened by setting mode 1 directly:

```c
REG_DISPCNT = MODE_1 | BG0_ON | BG2_ON | OBJ_ON | OBJ_1D_MAP;
```

The next frame overwrote it. The recipe compiled, ran, and did nothing. Behind
it, `rt_bg_affine` is correct — its matrix arithmetic has a passing host test
that sweeps all 256 angles. **That test could never have caught this**, because
a host test of pure arithmetic cannot see which register the frame loop owns.
The maths was right and the picture never moved.

Fixed by giving the mode a home the frame loop reads: `rt_video_mode` /
`rt_video_mode_get`, with the header stating plainly that writing `REG_DISPCNT`
directly does not last. Affine backgrounds still need map data the room editor
does not emit — 8-bit indices in a different layout — so that remains Phase 7,
and the reference now says so instead of implying otherwise.

**The gate, and what it found on its first run.** A register the frame loop
assigns unconditionally belongs to the runtime; teaching a reader to write it
is teaching something that lasts one frame. The new check extracts `rt_flush`,
collects every register it assigns outright — `REG_DISPCNT`, `REG_BG0HOFS`,
`REG_BG0VOFS`, `REG_BG3HOFS`, `REG_BG3VOFS` — and fails if any example in the
book assigns one. It went red immediately on **course topic c12**, the lesson
that introduces hardware registers, whose very first example was
`REG_BG0HOFS = 32; REG_DISPCNT = MODE_0 | …`. A learner's first register write,
demonstrating that registers do nothing. That lesson now uses registers the
runtime does not own and carries a section naming the five that it does.

**A second gate, from my own mistake.** Writing the fix, I documented a call
named `rt_camera_set`, which has never existed. The reference gate did not
notice: it runs one way only, checking that every call *declared* in runtime.h
is described. Nothing checked that every call the book *names* is real. The
reverse gate went red on its first run with two more — `rt_instance` and
`rt_distance`, in the "Reaching other instances" lesson, where the snippet
declares `rt_instance *other` (the type is `Instance`) and calls a distance
function that does not exist. **That snippet cannot compile.** Recipes are
compile-tested; course code blocks are not, which is the structural gap that
let it sit there.

Neither gate needed sabotaging to prove it can go red. Both went red against
real defects on their first run and green after the fixes — which is the
strongest form of the proof, because the failure was not one I planted.

The lesson generalises past this project: **a test that exercises a component
in isolation cannot see a contract the rest of the system breaks.** Ask, for
each thing the code writes, who else writes it and who writes it last.

### The Undertale slice: what actually composes — 2026-08-05

The composition test, finally attempted. A bullet-hell encounter — a soul that
moves, a projectile object, a ring pattern fired on a cadence from inline C,
collision costing health, a hit sound — built through the real generator and
the real ARM toolchain. **It builds: 35 KB.** Three things came out of making
it, and only one of them was the thing I expected to find.

**1. Inline C could not name anything the editors made.** Actions resolve an
object to its index in `nb_objects[]` and emit the bare number —
`rt_meeting(self, 1)`. Inline C had nothing to resolve with, so an author
writing bespoke behaviour had to hard-code that `1`, and reordering or deleting
an object silently repointed it at a different one. Two subsystems that each
worked alone and did not compose — the exact risk Part VIII names, found the
first time anything tried to use both at once. The generator now emits
`NB_OBJ_*`, `NB_SPR_*`, `NB_SND_*` and `NB_ROOM_*` before any authored line,
minted through the same identifier rules, keyword guard and collision check as
every other generated name.

**2. Creates the pool refuses were discarded by every caller.** `rt_create`
returns 0 when 128 instances are live. The room loader ignores it; the
`create_instance` action emits `rt_create(...);` and ignores it. A bullet
pattern that outruns the pool simply stops firing — no error, no log, nothing,
and which instances survive depends on placement order, so a player placed last
in a crowded room does not exist and the camera follows a projectile. Refusals
are counted now and shown on the profiler overlay beside a live pool gauge:
`OBJ 128/128  LOST 42`. That is where an author is already looking when a busy
scene misbehaves, and a pattern that quietly stops firing looks like a logic
bug until that number is seen climbing.

**3. What I expected to find, and did not.** A room with 200 placed instances
builds without complaint from `build_rom`, and I was ready to call that a
silent failure. It is not: the export gate consults `budget_report`, which
counts 201 against a cap of 128 and blocks with "Too big: Objects in a room…".
The library API has no gate; the product does. Checking before reporting cost
one command and would have made a false claim otherwise. What *is* missing is
narrower and real: the budget counts only instances **placed** in a room, while
its own note says "objects created while playing count too" — and nothing
counts those. A bullet-hell exhausts the pool entirely at runtime, where the
budget cannot see it. The overlay counter is the answer to that, because the
number is not knowable before the game runs.

**A correction to the Definition of Done.** The Undertale test asks for
"exceeding 128 on-screen projectiles". `NB_MAX_INSTANCES` is 128, and that is
not an arbitrary choice: OAM holds exactly 128 sprite entries, `g_oam[128]` is
copied whole each frame, and there is no mid-frame OAM reuse. **More than 128
on-screen sprites is beyond the hardware**, not merely beyond this runtime, and
per-scanline OBJ limits bite well before that when they share rows. The
requirement should read *128 simultaneous projectiles, the hardware maximum,
with the pool shared against everything else on screen* — which is a demanding
and achievable target, unlike the one written.

### The slice was run, and it does not draw — 2026-08-05

The ROM built last iteration had never been executed. It has now, on the
vendored VBA-M core running headless on the build host, and **the game does not
display its sprites.**

What is established, from emulated hardware and the runtime's own variables
read out of IWRAM with gdb:

* The ROM runs. Startup completes all the way through `rt_render()`.
* `REG_DISPCNT` is `0x1740` — mode 0, OBJ on, BG0/1/2 on. Correct.
* The room's backdrop renders as exactly the colour the project asked for.
* Sprite tiles and the OBJ palette are both uploaded to VRAM correctly.
* The main loop iterates continuously at about frame rate.
* **Hardware OAM stays `0x0200` — every entry hidden — for the whole run,
  while the shadow buffer `g_oam[0]` holds a valid sprite, `0x0066`.**

So the runtime computes its sprites correctly and they never reach the screen:
the OAM handoff in `rt_flush()` is not landing. Nothing is drawn but the
backdrop — no sprites, no text, no profiler overlay.

**The root cause is not isolated, and I am not going to guess at one.** The
instrumentation I added to bisect it produced a self-contradictory reading — a
counter immediately after `rt_vsync()` stuck at 0 while the loop counter around
it advanced past 400 — which is impossible in a single-threaded program and
means the measurement is wrong, not that a line is unreachable. An unreliable
instrument's output is not evidence. Two other readings during this session had
to be thrown out for the same reason: a counter that was not `volatile` was
kept in a register and never stored, and symbol addresses moved between builds
so two runs compared different variables.

**The finding that matters is not the defect, it is the gap it came through.**
Every check this project has — 544 SDK checks, the runtime suite, the help
suite, the integration test that builds a real ROM with the real ARM toolchain
— verifies the SDK by *compiling and inspecting*. Not one of them ever ran the
output. A ROM that builds, links, passes a header check and contains correct
data is not a ROM that draws anything, and for however long this has been true,
every green suite has been consistent with a game that shows a blank screen.

The emulator harness is written down now (vbam headless + gdb, the vbam globals
worth reading, and the three measurement traps already paid for) so the next
session bisects instead of rediscovering. **Running the output belongs in the
integration test**, not in a one-off investigation: the check to add is that a
built ROM, after N frames, has at least one visible OAM entry.

### The no-draw bug, root-caused and fixed — 2026-08-07

**Every ROM this SDK ever built hung at its first VBlankIntrWait, and the
cause was one attribute.** `rt_irq_entry` carried
`__attribute__((interrupt("IRQ")))`, on reasoning the old comment stated
confidently: an IRQ handler is what it is. But the GBA's hardware exception
lands in the BIOS, and the BIOS calls the user vector at 0x03007FFC as a
*plain ARM function*, performing the exception entry and exit itself. The
attribute double-applied that frame inside the BIOS's own: `sub lr, lr, #4`
bent the return address into the middle of the BIOS epilogue, and the
SPSR-restoring exception return completed the damage. Every compile-side suite
stayed green throughout, because the handler's C is correct — no host test
executes the BIOS calling convention around it.

Removed, with the war story as the comment. **Proof by execution:** the
bullet-hell slice draws — ring of bones, soul, live profiler overlay reading
`OBJ 128/128 LOST 0023` at 81% frame budget — the kitchen sink draws, and the
integration suite's execution fixture shows a visible hardware OAM entry.
Full battery green, now including a stage that actually runs a ROM.

Two process notes worth the ink:

* **The instrument had to be proven before the reading counted.** The
  watchpoint that settled it (gdb from launch, arm after CPUInit `finish`,
  watch the emulated vector word) fired once — rt_irq_init's legitimate write —
  and then never again. Silence was the datum: the vector was never corrupted;
  the contract around it was wrong. Three earlier readings had been discarded
  for instrument faults, and the discipline of throwing them out is why this
  one could be believed.
* **Two sessions attacked the same bug from opposite ends.** While this
  session bisected the runtime, the bugfix session claimed `gbaruntime/` on
  the campaign board and briefed Codex on a byte-write-to-OAM hypothesis.
  The board caught the overlap: the finding, the proof, and the not-the-cause
  note are appended under their claim rather than fought over in the files.
  A comment that explains why code is correct deserves suspicion in
  proportion to its confidence — the one that owned this bug asserted the
  attribute "emits the right frame."

### Executable composition slices, and what the second one caught — 2026-08-07

`tools/gba_fixtures.py`: whole projects through the real generator and ARM
toolchain, EXECUTED on the vendored core, asserted on hardware state and
pixels. Two slices so far — the bullet-hell (128 hardware sprites, profiler
overlay visible in the frame) and a typewriter dialogue scene (say action,
control codes, panel glyphs present in the lower rows by capture time). Both
pass; the frames are kept.

The dialogue slice found a generator bug before it ever ran: the variable
audit reported "sets gold but never reads it" for a variable whose only read
is `{v:0}` in the dialogue text. A say line printing a slot IS a read, made by
the engine while typing; the audit only scanned code. It now resolves say
control codes against the slot order, including a line that prints a slot
before the walk reaches the action declaring its variable. The suggested fix
in the old message — delete the set — would have broken the dialogue that
displays it.

Codex lanes running in parallel this iteration: the Help book
compile-as-taught gate, and Phase 2 project search/references. Claims on the
board; their suites gate their own lanes.

### Third slice: a save that survives the power cycle — 2026-08-07

`tools/gba_fixtures.py persist`: first boot finds no save, sets a score,
saves, and records a fresh-cartridge probe; the emulator is told to flush its
battery file (vbam only writes it on clean exit, and the harness kills the
process — `sdlWriteBattery()` called over gdb is the flush); second boot, same
battery file, must restore the score into the probe. It does: 1234 comes back
through a genuine process death and restart. That exercises the SRAM magic,
the WAITCNT save timing, vbam's save-type detection off the embedded
SRAM_V113 signature, and the load path — the same path the data-safety work
once found destroying damaged stores.

Probe addresses are read from each build's own ELF, never remembered across
builds — the trap that produced two contradictory readings during the no-draw
hunt is now structurally impossible in this harness.

### Fourth slice: the whole loop — and what composing it exposed — 2026-08-07

`tools/gba_fixtures.py encounter`: dialogue types while the phase probe reads
1–2, an alarm opens the waves, rings fire at a soul parked on their centre,
collisions cost health, four seconds of waves end in `rt_game_save()`, and the
battery file exists after the flush. All asserted in one run of one ROM:
phase 2→3, 124 sprites in flight at capture, panel glyphs at two seconds.
The three pillars hold together.

**The composition exposed a design gap the isolated slices could not:**
health finished at **−30**. Contact damage fires on every overlapping frame —
a bone crossing the soul at 60 fps costs ~200 health in under four seconds of
waves — and `nb_health` has no floor, no death hook, and no
invincibility-frame primitive. The benchmark game cannot be built on
per-frame contact damage: Undertale's encounters are survivable because a hit
buys you mercy frames. An author can hand-roll them today (alarm + a variable
+ if_var guards around add_health), which is exactly the kind of boilerplate
the runtime exists to absorb. Queued for the runtime work list behind
flash/PCM/palettes: an engine invincibility window after a collision event
runs (opt-in, per object), a health floor at zero, and a death event — the
Game-Maker-shaped answer this SDK's model already implies.

Runtime remains under the bugfix session's claim, so today this is recorded
with evidence rather than patched around their in-flight work.


### Flash save, landed the hour the runtime freed — 2026-08-07

The generator now owns the save part: `save_type` (sram | flash64 | flash128)
emits `nb_save_type` and the ONE signature string emulators and flash carts
scan for — the runtime's baked `SRAM_V113` is gone, because two signatures in
one ROM would leave battery sizing to scan order. The runtime dispatches on
the type: SRAM stays plain byte writes; flash is the real command protocol —
program 1→0 only, 4 KiB sector erase before rewrite, every operation finished
by bounded DQ7 polling so a worn part hangs the save and never the game. The
128K part is banked and gets pinned to bank 0 at boot. The high score shares
sector 0 with the save block, so both writers carry the other's value across
the erase — the kind of invariant that silently dies when two functions each
assume the other does not exist.

Proof is the same power-cycle fixture that guards SRAM, run on flash128: the
battery file comes out exactly 131072 bytes — the signature sized the part —
and 1234 comes back through a process kill and second boot. The ROM built for
flash carries FLASH1M_V102 and no SRAM string; the SRAM ROM carries SRAM_V113
and no flash string. gbaruntime, integration (which now executes a ROM every
run), and all five composition slices pass.

Cross-session note: the bugfix lane closed its runtime claim this hour,
committing the interrupt-attribute fix verbatim after independent clean-process
red/green verification — and its Codex disassembly track had converged on the
same diagnosis. The runtime queue is now this lane's: PCM second channel +
mixer, palette cycling, health floor + death event + invincibility frames.


### The second PCM voice — 2026-08-07

Direct Sound B now carries a looping sampled soundtrack underneath the
one-shot effects on A — the arrangement a sampled-music game needs and one
FIFO cannot provide. Both voices share timer 1 (timer 0 stays the Help
example's), and the sharing is the design constraint that matters: starting
the second voice must not re-arm the clock or the first voice hiccups, so the
timer starts only when no voice was active and stops with the last one.
`nb_Sound` gains an appended `pcm_loop` (zero = the old behaviour, per the
compatibility rule); a sampled sound marked loop routes to B and
`rt_stop_music()` silences it, because a looping sample is the music.

Proof at the register level: the jukebox slice plays both at once and asserts
`SOUNDCNT_H` carries both voices' volume, routing and timer bits (0x770E),
DMA1 feeds FIFO A while DMA2 feeds FIFO B, and the shared timer is enabled.
What remains for the matrix row is a sample-summing mixer — more than two
simultaneous samples — which is CPU-budget work, not hardware plumbing, and
is queued behind palette cycling and the health primitives.


### Palette cycling — and the vacuous fixture the gate caught — 2026-08-07

Four independent cycle slots rotate contiguous BG or OBJ palette ranges in
the VBlank flush, so a step is never torn mid-frame. Rotation is lossless:
stopping leaves the same colours one slot along. A cycle over entry 0 fights
the room loader's backdrop write — legitimate as a sky effect, documented as
the author's fight to pick.

The proof asserts three properties over two samples of emulated palette RAM:
the cycled ranges differ between samples, the neighbours do not, and each
range holds the same SET of colours both times — rotation rearranges, never
invents. **The first run of that gate failed against correct code**, and the
failure was the fixture: a room with no tileset populates no BG palette, so
the test was rotating five zeros — vacuous, exactly the trap class already on
record from the sprite-bank arithmetic. The live cycle state (tick advancing)
plus all-zero entries made the diagnosis one gdb read. The fixture now paints
real colours into both ranges before asking whether they move.

### Design: the damage-cadence primitives — 2026-08-07

What the encounter slice proved missing (health 200 → −30 in four seconds of
contact), designed before implementation:

* **Floor.** `nb_health` clamps at zero in the engine's own writers (the
  add_health action path); raw C keeps raw access, because Level 3 is allowed
  to break rules knowingly.
* **Mercy frames, opt-in and engine-owned.** `nb_Object` gains an appended
  `u8 hurt_frames` (zero = today's behaviour). After a collision event runs,
  if `nb_health` DROPPED during it and the object declares hurt_frames, the
  instance gets that many frames of invincibility: collision events are
  skipped entirely while it counts down, and the sprite blinks (hidden toggled
  every other frame) so the state is visible without costing the author a
  single action. Detection is by comparing health around the event call — the
  engine does not care HOW the event hurt the player, only that it did.
* **A death event.** `nb_Object` gains an appended `nb_event_fn on_no_health`
  (null = nothing, preserving zero-fill compatibility). When the floor clamps
  health to exactly zero from above, the event fires once; it will not refire
  until health has risen above zero — the Game-Maker-shaped contract, where
  death logic is authored, not imposed.

Instance side: an appended `u8 inv` countdown (Instance is runtime-owned, no
generator contract). Generator side: `hurt_frames` from the object dict, a
`no_health` event type wired to the new slot — both zero-default, both
covered by the struct-drift gate that checks arity from both sides.


### The damage-cadence primitives, landed as designed — 2026-08-07

Implemented exactly as the design entry above specifies, with one refinement
worth recording: mercy-frame detection watches the health LEDGER, not the
weapon. The engine snapshots `nb_health` around each instance's step — which
is where the generator folds every collision event — and a drop plus a
declared `hurt_frames` arms the invincibility. The skip lives inside
`rt_meeting`/`rt_place_meeting`, because generated collision blocks cannot be
wrapped but every one of them asks those functions first. The blink is a
draw-skip on `inv & 2`, so `hidden` stays the author's. The floor is enforced
twice — at the `add_health` action and at end of step — so raw C keeps raw
access mid-step and no frame ever ENDS negative. `on_no_health` fires once
and re-arms only when health rises above zero: death logic is authored, the
latch is not.

Proof by execution: the new mortal slice (three health, contact every frame,
no mercy) holds the floor at exactly 0 and fires the death event exactly
once; the encounter slice, its soul now declaring 30 mercy frames, bleeds 9
health through the same waves that cost 230 without them — and still reaches
its save. Eight slices compose; the 550-check SDK suite (grown +6 by the
search lane), runtime, integration and help suites are green.

With this, the runtime queue that opened after the matrix refresh is CLEARED:
flash save, PCM voice B, palette cycling, health primitives — all landed with
execution proof, all inside one campaign day. Remaining against Part III:
the sample-summing mixer, OBJWIN, bitmap modes, EEPROM, rumble/solar/gyro,
sleep/power, and the affine-map emission (Phase 7).

### The palette gate's three failures, none of them the runtime — 2026-08-07

The cycling code was correct from its first compile; proving it took four
attempts, and each failure taught a different harness lesson worth keeping:

1. **Vacuous fixture.** A room with no tileset populates no BG palette, so
   the first gate rotated five zeros and asked whether they changed. The
   live cycle state (tick advancing) beside all-zero entries made the
   diagnosis one gdb read.
2. **Aliasing under load.** Wall-clock sample gaps mean nothing when the
   emulator runs at whatever rate the loaded host allows: a gap can land on
   an exact multiple of the cycle's period and photograph a rotating range
   as still. Two unequal gaps reduced the odds; load found the coincidence
   anyway.
3. **Sampler resonance.** The fix — pairing each palette dump with the
   runtime's own frame counter and only judging frame-distinct pairs — then
   failed once more, because 0.25 s of sleep plus ~0.4 s of gdb dump time
   comes to almost exactly the BG cycle's 40-frame period: every sample
   landed on the same phase, legitimately frame-distinct pairs never
   existed, and the early-exit accepted three samples. A measurement cadence
   must never be commensurate with the period it measures; the sleeps now
   grow per sample so no period can lock on, and "no valid pair" is its own
   named failure rather than a false "does not rotate".

After the third fix: four consecutive full-set runs, eight slices each, all
compose. The failure evidence printer stays armed — any future occurrence
prints every sample's frame number and both arrangements instead of a
verdict to argue with.


### OBJWIN, and the bug class's second member — 2026-08-07

`rt_set_objwin` turns any sprite into the OBJ-window stencil (OAM mode 2:
its opaque pixels become a hole, not a drawing) and `rt_window_obj` names
the layers visible through it. The spotlight slice proves it by pixels:
exactly 256 lit pixels through a 16×16 stencil over a backdrop-dark room.

The slice's first red was the day's second instance of the frame-loop
ownership class: I set `WINOBJ_ON` in `REG_DISPCNT`, the register the flush
rewrites every VBlank — the same mistake removed from the display-mode path
this morning, made fresh by the same hand within hours. And pulling that
thread found the latent original: `rt_window`'s rectangle windows have died
one frame after arming since Phase 7, because no test ever RAN one. All
window enables now live in `g_dispcnt`. A documented bug class does not
immunise its documenter; only a gate does.

One more harness lesson for the pile: 0x7FE0 reads yellow to RGB eyes and is
GREEN in BGR555 — the lit-pixel test now counts backdrop-difference and
decodes nothing.
