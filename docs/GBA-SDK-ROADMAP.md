# GBA SDK — roadmap to a professional game-development tool

Written 2026-08-03, before any of the expansion work. It is a plan, not a
record: nothing below is built unless a later note says so.

---

## 1. Where it actually stands

The starting point is much further along than "an SDK". Measured, not guessed:

| | |
|---|---|
| `de/gbasdk.py` | 4,846 lines — asset browser + five editors |
| `de/gbabuild.py` | 1,372 lines — project → C code generator, incl. a small language compiler |
| `gbaruntime/runtime.c` | 1,483 lines — the on-cart engine |
| `gbaruntime/runtime.h` | ~75 engine calls |
| Authoring vocabulary | 21 event kinds, ~45 drag-drop actions in 8 groups |
| Script language | `var` / `global` / `if…then…else` / `while` / `repeat` / `exit`, full expressions |
| Output | a real `.gba`, built on-guest with arm-none-eabi-gcc |

The engine already does instances, tile collision, 8-way movement, chase,
fixed-point trig, sprite flip/depth/angle/scale, animation ranges, room
transitions with fade, a follow camera, fade/flash/shake, music + SFX, text and
panel drawing, save/load and a high-score table.

**This is roughly Game Maker 5/6, complete, with a real compiler behind it.**
The distance to GameMaker Studio is therefore specific and nameable rather than
open-ended — which is the whole reason to write this down before starting.

Current hard limits in the runtime: **128 instances, 12 variables per instance,
4 alarms, 32 globals, one tilemap layer per room.**

---

## 2. What "GameMaker Studio level" means when the target is a GBA

GMS is a general 2D engine for machines with gigabytes and a GPU. The GBA is:

- ARM7TDMI at 16.78 MHz, **no floating-point unit** — fixed point only
- 240×160, 15-bit colour, **4 backgrounds, 128 sprites**
- 96 KB VRAM, 256 KB EWRAM (slow), 32 KB IWRAM (fast)
- no operating system on the target — the runtime *is* the OS

Two consequences worth stating plainly, because they shape everything after:

**The ceiling is a feature.** A large part of GMS exists to manage
possibilities the GBA does not have (3D, shaders, arbitrary resolutions,
networking). Cutting those is not a compromise; it removes most of the surface
area and leaves a tool that can be genuinely *complete* for its target rather
than perpetually behind.

**Codegen, not a VM.** GMS interprets/JITs GML. On a 16 MHz CPU an interpreter
loop would dominate the frame budget. The existing design — compile the
project to C and let GCC optimise it — is the right one and should stay. Every
language feature below has to be expressible as generated C.

---

## 3. The gap, by area

### 3.1 Language
The largest single gap. Present: expressions, `if/while/repeat`, variables.
Missing: **functions/scripts**, `for`, `switch`, arrays, `break`/`continue`,
`return`, local variables, comments, `%` and bitwise operators, a maths library
(`abs/min/max/clamp/lerp/sign/sqrt` in fixed point), string building.

Also missing and important: **errors that point back at the thing the author
wrote.** A compile error currently surfaces from the generated C. It has to name
the action row or the script line instead.

### 3.2 Objects
Missing: **parent/child objects** (GMS's single most-used structuring tool),
per-object instance variables with declared defaults and types, `with()`,
`instance_nearest` / `instance_find`, user-defined events, an **animation-end**
event, a **draw** event, and outside-room / intersect-boundary events.

The 12-variables-per-instance cap has to become a per-object declared set so
the generator can allocate exactly what each object uses.

### 3.3 Rooms
Missing: **multiple tile layers** (the GBA has 4 backgrounds and rooms use one),
parallax per layer, per-room creation code, room-level instance properties,
multi-select and copy/paste in the placement grid, and larger-than-screen rooms
with proper scrolling bounds.

### 3.4 Graphics
Missing: a **frame/animation editor** (sprites are single images with an
animation *range* in the engine but no authoring for it), a **palette manager**
— the perennial GBA pain, 16 colours per 4bpp palette × 16 palettes, with
automatic quantisation and per-sprite palette assignment — **autotiling** in the
tile editor, affine (rotation/scaling) backgrounds, and font authoring.

### 3.5 Audio
Currently a piano roll over the PSG channels. Missing: **PCM sample import**
(the GBA's two DMA channels), a pattern/tracker editor for music, SFX priority
and channel allocation, and streaming music from ROM rather than holding it in
RAM.

### 3.6 Build, budget and debug
Build works. Missing: a **resource budget report** — ROM, VRAM, OAM slots,
palette slots, instances — which on this hardware is not a nicety but the main
thing standing between a project and a cartridge that will not fit; incremental
builds; and a **debug overlay** (FPS, instance count, variable watch) compiled
in behind a flag. Source-level breakpoint debugging is not realistic and should
not be promised.

**A constraint to design around:** Notebook OS runs one app at a time, so the
SDK cannot launch the emulator. Testing a build means leaving the SDK. A
hand-off — build, then open the GBA Emulator on that ROM, and come back — needs
designing rather than wishing away.

### 3.7 Ergonomics and learning
Missing: undo across every editor uniformly, copy/paste of objects/events/
actions, asset folders and search, rename-with-reference-update, project
templates, worked examples, and an offline reference. On a machine with no
internet the built-in reference is not documentation — it is the only
documentation.

---

## 4. Phases

Each phase ends in something shippable on its own. Order is chosen so that
later phases build on earlier ones and nothing is left half-migrated.

### Phase 1 — Structure for scale
Asset folders, search, rename-with-references. **Object parenting.** Per-object
declared instance variables. Uniform undo. Copy/paste of objects, events and
actions. Multi-select in the room grid.
*Done when:* a project with 40 objects and 15 rooms is navigable and editable
without dread, and a child object inherits and overrides its parent's events.

### Phase 2 — The language
Functions/scripts as first-class assets. `for`, `switch`, `break`, `continue`,
`return`, locals, comments, `%`, bitwise. Fixed-point maths library. Arrays.
**Errors that name the author's own row or line.** Actions and script become
two views of one thing — every action lowers to the same intermediate form.
*Done when:* a non-trivial behaviour can be written either way and the two
produce identical generated C.

### Phase 3 — Graphics
Frame/animation editor with onion-skinning. Palette manager with quantisation
and per-asset palette assignment. Multi-layer rooms mapped onto the four
hardware backgrounds, with parallax. Autotiling. Affine backgrounds. Fonts.
*Done when:* a parallax platformer with animated characters can be built
without hand-editing a palette.

### Phase 4 — Audio
PCM sample import and conversion. Tracker-style pattern editor. Channel
allocation and SFX priority. Music streaming from ROM.
*Done when:* a game ships with a looping soundtrack and layered SFX without
running out of RAM.

### Phase 5 — Build, budget, debug
Resource budget report with per-asset attribution. Incremental build. Debug
overlay behind a build flag. Emulator hand-off.
*Done when:* the SDK can say, before a build, why a project will not fit — and
which asset to blame.

### Phase 6 — Learning
Project templates (platformer, top-down, shmup). Worked examples that open and
build. Built-in reference for every action, engine call and language feature.
*Done when:* someone who has never programmed can get a moving character on
screen in ten minutes without leaving the machine.

---

## 5. Decisions to make before Phase 1

These change the shape of everything after, so they are worth settling first.

1. **Codegen stays** (recommended) rather than a bytecode VM. Confirm.
2. **Actions and script converge on one intermediate form** (recommended) —
   otherwise every new feature has to be built twice and they will drift.
3. **Fixed-point convention.** The runtime already uses fixed point; pin the
   format (16.16 recommended for position/velocity, 8.8 for scale/angle) and
   expose it consistently in the language.
4. **Authoring model.** GMS 8 kept drag-drop as primary with code as an escape
   hatch; GMS Studio went code-first. Recommendation: keep the action sheet
   primary — it is what makes this usable by someone who has never coded, which
   is the app's stated purpose — with script as a peer, not a fallback.
5. **Raise the runtime caps** (128 instances / 12 vars / 32 globals) or make
   them per-project and budgeted. Recommended: budgeted, reported in Phase 5.
6. **Scope of affine/mode 7.** Cheap to promise, expensive to do well.

---

## 6. What will actually hurt

Named now so they are not surprises later.

- **Palettes.** 16 colours per 4bpp palette is the constraint every GBA project
  eventually hits. A tool that hides it well is a real differentiator; a tool
  that hides it badly produces games that look wrong and authors who cannot
  find out why.
- **VRAM and OAM budgeting.** 128 sprites and 96 KB is not much. Without the
  Phase 5 budget report, projects will fail late and mysteriously.
- **16.78 MHz.** An action-sheet game with 100 instances each running a long
  event will miss frames. Profiling and instance caps are not optional.
- **The single-app constraint.** Every edit-test cycle crosses an app boundary.
  If that loop is slow or loses state, nothing else in this roadmap matters.
- **Scope.** "GameMaker Studio level" is unbounded as stated; the phases above
  are the bound. Each phase should ship before the next starts.
