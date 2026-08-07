# Paper Physics — the Notebook OS material and motion specification

**Status:** normative. Written 2026-08-05 for the 1.0 completeness campaign. No
production code was changed to produce it.

**Relationship to the Interaction Constitution.** `Article VI — Motion` of
`docs/NOTEBOOKOS-INTERACTION-CONSTITUTION.md` defines *when motion happens and
how fast*. Its duration tokens, easing tokens and invariants are incorporated
here by reference and remain binding, **except for the two amendments in §0.5**.
This document defines the rest: *what the interface is made of, where a surface
comes from, where it goes, and what it travels along.*

**Rule of construction**, inherited from the Constitution. Where an article
states a rule the repository already follows, it is a **codification** — it names
the existing implementation so a new app cannot re-invent it. Where it states a
rule the repository does not yet follow, it is marked **[GAP]** and appears in
the migration inventory in §9.

**Nothing in this document authorises a visual redesign.** The palette, the type
scale, the radius scale and the papertone identity are settled and unchanged.

---

## §0 — The thesis, and what changed

### 0.1 Letterpress, not glass

macOS is hypnotic through four mechanisms. Being precise about them matters,
because only three are available here.

1. **It never cuts.** Every discontinuity is replaced by a transformation — the
   icon *becomes* the window, the cell *becomes* the detail view. The eye tracks
   one object through the change and never re-parses the screen.
2. **It is inferable.** One physics, applied consistently enough that a surface
   you have never seen behaves the way you predicted. Predictive accuracy is the
   actual source of "effortless"; simplicity is a side effect of it.
3. **Depth carries information.** Layering states what is modal, what is
   beneath, and what is dismissible.
4. **Blur, translucency and specular light.** — *Not available.* That is the
   vocabulary of **glass**, and this interface is not made of glass.

Glass is frictionless, immaterial, perfect, anonymous. **Paper** is exact
registration on a surface with visible tooth: rigorous typesetting on stock with
fibre in it. High craft with a visible hand. It is the canonical *textured but
precise* material, and it is already this project's design language — papertone
surfaces, ink text, the editorial serif, one signage red.

Every rule below is that thesis made checkable.

### 0.2 Reveal the structure; do not conceal it

**Apple conceals its structure in order to feel effortless. A well-set page
reveals its structure in order to feel ordered. This interface reveals.**

Visible rules, hairlines, columns and margins; a typographic hierarchy that can
be read directly off the screen. The beauty is the perceived order — the
coherence and the beauty are one act, not decoration applied over logic.

This has a direct motion consequence, and it is the central idea of this
document: **a revealed structure gives motion somewhere to go.** A panel slides
down its own column. A sheet arrives against a rule that was already there. Every
transition then *demonstrates the layout* instead of decorating it.

### 0.3 The affect

Competent, calm, warm, unhurried, slightly serious. The interface respects the
person using it and does not perform delight at them. No confetti, no
celebration, no exclamation marks, nothing ambient, nothing idle.

### 0.4 Earned roughness

Grain reads as *authored* only against a substrate of obvious control. The same
misaligned element is texture in a precise interface and a defect in a sloppy
one. **Character is not available until the machine is demonstrably well made**,
which is why every rule here is paired with a gate.

### 0.5 Two amendments to Article VI

**Amendment 1 — the `NB_ACCEL` animation gate is reversed.**
Article VI §4 makes software rendering suppress motion entirely
(`nbapp._apply_motion_policy()` sets `gtk-enable-animations` false;
`nbmotion.policy()` returns 0 for every duration). Its reasoning was sound — a
90 ms transition is six frames, and six unevenly dropped frames read as *a
computer struggling*, which is worse than an honest snap.

That conclusion is now reversed, and **the render path becomes the problem to
solve rather than the reason to stand still.** Motion is pervasive on both paths.
The obligation this creates is Article F: motion must be made cheap enough to
run on the software rasteriser, by damage-limiting it rather than by shortening
it. `NB_ACCEL` no longer gates animation. **Reduced Motion is unaffected** and
remains a fully honest, independent accessibility switch.

This amendment is load-bearing on hardware coverage: the shipped kernel carries
`CONFIG_DRM_I915` but has **no AMD or Nouveau source at all**, so every AMD and
NVIDIA machine renders in software permanently. That is the majority of the
second-hand laptops this OS targets, and it is why Article F is not optional.

**Amendment 2 — the §12 conflict resolves toward the full band.**
Article VI §2 flags rows 4 and 5 of its duration table (surface arrive/depart at
140–180 ms, page transitions at 180–220 ms) as conflicting with the theme's
ease-out-everywhere rule, and defers to §12. With motion now pervasive, the full
band is adopted as written. The theme's vocabulary continues to govern **CSS
state transitions** (90 ms, colour and border only); the longer tokens govern
**surfaces and pages**, which the theme has no vocabulary for. They do not
overlap.

### 0.6 Platform facts that bound every rule below

Restated from Constitution §0, plus two of this document's own. A rule that
ignores any of these is unimplementable here.

1. **GTK3 / PyGObject.** No `Gtk.Application`, no `GAction`/`GMenu`. Menus,
   confirms and About cards are drawn as `Gtk.Fixed` layers inside the window's
   own `Gtk.Overlay`, wrapped in a `Gtk.EventBox` so they get a `GdkWindow` and
   actually blit.
2. **Often no compositor.** `picom`/`xcompmgr` run only when `NB_ACCEL=1`, so a
   real popup window is unreliable and **shadow cannot be delegated to the
   compositor for anything inside a window**.
3. **Software rendering is a first-class target**, and after Amendment 1 it is a
   first-class *motion* target.
4. **The layout budget is 1024×740** — the smallest supported panel minus the
   28 px desktop panel.
5. **One app, one process, fullscreen.** There is no window management to
   animate.
6. **Only colour and border may animate in CSS.** No transition on `width`,
   `height`, `margin` or `padding`: those force a re-layout every frame. Motion
   that changes position or size must be expressed as opacity, as a cairo-drawn
   offset inside a fixed allocation, or through `nbmotion.Scalar` — **never as an
   animated allocation.**
7. **The shared engine already exists.** `de/nbmotion.py` (frame-clock driven,
   retargetable, instant-equivalent) and `de/nbtransitions.py` (page switch,
   reveal, replace, highlight). Nothing in this document authorises a second
   motion implementation in an app.

---

## Article A — Material

### §A1 One light (codification)

Light comes from **directly above**. Every shadow offsets straight down. A
diagonal offset implies a light source the rest of the interface does not have,
and immediately reads as a sticker rather than a sheet.

### §A2 Exactly two elevations (codification)

The theme's ELEVATION section already establishes this and it is binding:

| Level | Meaning | Shadow |
|---|---|---|
| **RAISED** | attached to something on the page — menus, tooltips, popovers | `0 1px 2px rgba(26,25,22,.08–.10)`, `0 6–10px 18–28px rgba(26,25,22,.14)` |
| **FLOATING** | took over the page — dialogs, overlay cards, confirms | as RAISED, wider ambient layer |

**Two levels, and deliberately no more.** A third invites a fourth.

Three properties of every shadow, all already stated in the theme and restated
here so a motion pass cannot lose them:

- **Two layers** — a tight contact edge plus a wider ambient one. A single blur
  reads as a 1990s drop shadow.
- **Cast in warm ink** `rgba(26, 25, 22, α)` at very low alpha, **never pure
  black**, which on a warm paper field goes visibly grey-blue.
- **Straight down only.**

### §A3 Depth by stacking, never by blur (normative)

A sheet resting on another sheet **casts onto it and occludes it**. It does not
tint it, glow onto it, or blur what is behind it. **You never see through a
sheet.**

This is the honest paper analogue of modality, and it is also the only depth
model a software rasteriser can afford — the material thesis and the render
constraint agree here rather than fight, which is the strongest possible sign
that the thesis is the right one for this machine.

### §A4 Edges are cut, not feathered (codification)

Boundaries are hairlines and radii from `RADIUS_SCALE = [0, 4, 6, 8, 12, 100]`,
whose steps already carry meaning: `0` square by intent, `4` marks inside text,
`6` a row inside a container, `8` controls, `12` containers, `100` conceptually
round. No feathered edges, no gradient borders, no glow.

### §A5 Elevation changes are motion, not state (normative) **[GAP]**

A surface that gains elevation gains it *over time*, on the `SURFACE_IN` token.
Shadow appearing instantly under a card that faded in is the single most common
way a paper interface reveals that its depth is painted rather than physical.

### §A6 Gate

**[GAP]** `tools/material_check.py` — every `box-shadow` in the theme and in
every app's `b"""CSS"""` resolves to one of the two elevation levels; every
shadow colour is warm ink; every vertical offset is positive and every horizontal
offset is zero; no `filter: blur`, no `opacity` used as a depth cue on a surface
that also carries a shadow. Red-proof: introduce a diagonal offset and a pure
black shadow; both must fail.

---

## Article B — Origin

### §B1 Nothing appears from nowhere (normative)

**Every surface that arrives must name the thing it came from, and visibly come
from it.** This is the single rule that does the most work in this document,
because it is what converts a cut into a transformation (§0.1 mechanism 1).

A surface with no sensible origin is a design defect *before* it is a motion
defect: if you cannot say what raised it, the user cannot either.

### §B2 The origin table (normative)

| Surface | Origin | Motion |
|---|---|---|
| Panel dropdown menu | its own menu-bar title | drops from the title's bottom edge, down its own column |
| Logo menu | the snail mark | drops from the mark |
| Context menu | the pointer | grows from the click point |
| Confirm / prompt card | **the control that raised it** | grows from that control's rectangle |
| About card | the app-name menu title | drops from it |
| Save / Open picker | the menu item that opened it | grows from it |
| Tooltip | its widget | fades in place at RAISED, no travel |
| App window | **its icon in the Finder or the panel** | grows from the icon's rectangle |
| Document view | the row that was opened | grows from the row |
| Sheet / inline pane | the edge that owns it | slides in from that edge, along the grid |
| Toast / status flash | its own status bar | reveals in place, does not fly |

### §B3 Departure retraces arrival (normative)

A surface leaves the way it came, on `SURFACE_OUT` with `DEPART` easing. A card
that grew from a button collapses back into that button. An app that grew from
its icon returns to the icon. **A surface that arrives from an anchor and then
fades out in place is a broken promise** — the user learned where it lives and
was then told that was a lie.

### §B4 Gate

**[GAP]** `tools/origin_check.py` — every call site that presents an overlay,
menu, card or window passes an anchor widget or rectangle; the shared
presentation helpers refuse to present without one. Red-proof: remove an anchor
from one call site; the check must name it.

---

## Article C — Identity

### §C1 The thing that moves stays the same thing (normative)

When one surface becomes another, it **is** the same object throughout: same
identity, continuous position, continuous size. A card that becomes a page grows
into the page. It does not cross-fade into a different rectangle that happens to
contain similar content.

### §C2 Cross-fade is for replacement, never for transformation (normative)

- Two *different* things, one replacing the other → crossfade is correct.
- One thing *becoming* a larger or smaller version of itself → crossfade is
  wrong, and reads as a cut with a delay in front of it.

`nbtransitions.CROSSFADE` is therefore the default for a page switch between
unrelated panes and is **forbidden** for open/close of an item into its detail.

### §C3 One object, one animation (codification)

Article VI §3 already requires transitions to retarget from their current value
rather than stacking. Restated as an identity rule: an object has at most one
in-flight animation per property, and a second request **retargets the first**.
`nbmotion.Scalar` already implements this; nothing may bypass it.

### §C4 Gate

`tools/motion_selftest.py` already covers retargeting and cancellation. **[GAP]**
extend it with an identity assertion: for every transition in Article G marked
*transform*, the widget instance before and after is the same object.

---

## Article D — Weight

### §D1 Duration scales with mass (codification + normative mapping)

The tokens are `nbmotion`'s and are unchanged. What is new is the binding of each
token to the class of thing it may move:

| Token | ms | Band | May be used for |
|---|---|---|---|
| `INSTANT` | 0 | — | the end state, now; Reduced Motion; press-down |
| `FEEDBACK` | 90 | 70–100 | hover, press release, a value changing in place, a toggle |
| `SELECT` | 120 | 100–140 | selection and focus travelling between rows |
| `SURFACE_IN` | 160 | 140–180 | a menu, card, tooltip, sheet or app arriving |
| `SURFACE_OUT` | 160 | 140–180 | the same, departing |
| `PAGE` | 200 | 180–220 | a whole page or document view replacing another |

**An app passes a token, never a number.** A literal `250` in an app is how one
house style becomes seventeen.

### §D2 Settle, never bounce (codification)

Easing is cubic and cubic only. `ARRIVE = ease_out`, `DEPART = ease_in`,
`MOVE = ease_in_out`, `linear` only for a value that is already a physical
quantity (a playhead, an elapsed-time bar) where a curve would be a lie about the
data.

Nothing overshoots, springs, bounces, pulses or wobbles. A spring returns values
outside `[0, 1]`, which on a flat papertone surface reads as a defect. **Paper
has inertia: it arrives and it stops.**

### §D3 Many short settles, not few long floats (normative)

Pervasive motion in this material means a large number of *short, damped*
movements — not a smaller number of long, floating ones. This is simultaneously
the correct character and the cheaper render, and it is the reason pervasive
motion is affordable at all. When in doubt, take the shorter token.

### §D4 Press is the exception (codification)

The press-down state has **no** transition — `transition-duration: 0ms` on
`button:active`, `button:checked`, `check:checked`, `radio:checked`,
`scale slider:active`. A press is a physical act and any delay between finger and
ink reads as lag rather than polish. Only the *release* back to rest is eased.

### §D5 Gate

`tools/motion_selftest.py` reads `nbmotion.DURATION_BANDS` and already asserts
each token sits inside its band. **[GAP]** extend to: no numeric literal duration
appears in any `de/*.py` animation call; no easing function returns a value
outside `[0, 1]` for `t ∈ [0, 1]`. Red-proof: add an overshoot curve; it must
fail.

---

## Article E — The grid

### §E1 The structure is visible (normative)

Rules, hairlines, column edges and margins are **part of the design, not
scaffolding to be hidden**. They are what makes the order perceptible, and they
are what motion travels along.

### §E2 Motion travels along the structure (normative)

A surface moves **parallel to a rule that is already on screen**, and comes to
rest **against** one. A panel drops down the column it belongs to. A sheet slides
in from the edge that owns it and stops at the hairline that separates it from
what it covers. Diagonal travel is forbidden: there is no diagonal in the layout
for it to follow.

### §E3 The grid (normative)

Mostly **codification**. The OS already had a coherent system that nobody had
written down; this names it, corrects two genuine inconsistencies, and adds only
what was actually missing.

#### E3.1 The unit

**`UNIT = 4 px`.** Already `design_tokens.SPACING_STEP`. Every horizontal and
vertical measurement in this section is a multiple of it.

#### E3.2 The vertical ladder, and why it looked off-grid

The most-used control heights in the OS are **22, 26, 30, 34, 38** — a clean
4 px-stepped ladder that appears to sit off the grid because every value is
`≡ 2 (mod 4)`.

It is not off-grid. **The offset is the hairline pair.** Subtract the 1 px border
at top and bottom and every value lands exactly:

| Rendered | − border | = interior | units |
|---|---|---|---|
| 22 | −2 | 20 | 5u |
| 26 | −2 | 24 | 6u |
| 30 | −2 | 28 | 7u |
| 34 | −2 | 32 | 8u |
| 38 | −2 | 36 | 9u |

**The rule: the grid governs the interior box; a border is drawn outside it.** A
bordered control is `interior + 2`; an unbordered one is the interior height
directly (24, 28, 32, 36, 40 — the second ladder already in the tree, and now
explained rather than contradicted; 32 was missing from this list until
`grid_check`'s first sweep found it shipping five times as a legal 8u step).

Above the control band (> 40) named steps stop and the same rule continues
generally: a compound row — a calculator key, a track lane, an album row — is
conforming when its interior sits on the grid, i.e. rendered height ≡ 0 (open)
or ≡ 2 (bordered) mod 4. A 76 px sequencer lane is 19u; a 66 px calculator key
is interior 16u; a 45 px anything is a defect. Below the control band (< 22)
live spacers, progress tracks and drag handles, which are field elements, not
boxes — the ladder does not govern them.

Text rhythm: **`LINE = 20` (5u)** for the 13 px body, a 1.54 ratio.
Grouping: **12 (3u)** within a group, **24 (6u)** between groups.

#### E3.3 The horizontal system: fixed rails, fluid field

A twelve-column fluid grid is a web idiom for reflowing content and is wrong
here. This OS's sidebars hold *fixed-width content* — place names, tool names,
track heads — which does not want to grow to 400 px because the window did. So:

> **Rails are fixed. The field is fluid. The field has a maximum measure.**

| Token | Value | Units | Meaning |
|---|---|---|---|
| `MARGIN` | 24 | 6u | inset from the window edge; content never touches it |
| `RAIL` | 240 | 60u | the one sidebar width |
| `GUTTER` | 24 | 6u | between columns inside the field |
| `HAIRLINE` | 1 | — | sits **on** the boundary, inside neither side |

`field = window_w − 2·MARGIN − Σ(RAIL + HAIRLINE)`

| Window | one rail | two rails |
|---|---|---|
| 1024 | 735 | 494 |
| 1366 | 1077 | 836 |
| 1920 | 1631 | 1390 |

**`RAIL = 240` corrects a real inconsistency.** Sidebars currently ship at 210
(`workout`), 212 (`packages`), 240 (`illustrator` layers), 252 (`bills`,
`illustrator` dock). 210 versus 212 cannot mean anything; 240 sits inside the
cluster and is already in use. A dock wider than `RAIL` is a **documented
exception that must prove its window minimum still fits 1024** — note
`illustrator`'s dock is exactly the element that once set the window minimum and
clipped in CJK, so widening it is not a free move.

#### E3.4 Measures

The cap on content width inside the field, by content kind:

| Measure | Value | For |
|---|---|---|
| `MEASURE_READ` | 640 (160u) | continuous prose — ~65 characters at 13 px |
| `MEASURE_FORM` | 1040 (260u) | label/value rows — **already shipping** in `settings.MAX_W`, verified good at 1920 |
| none | full field | tables, canvases, timelines, maps, the board |

A measure is a **maximum, not a width**: below it the content is the field.

#### E3.5 The third pane collapses; it is not a second layout

Three panes need `2·MARGIN + 2·(RAIL+1) = 530 px` of chrome, leaving 494 px of
detail at 1024. That is too narrow to be honest. **A third pane is permitted only
at ≥ 1366; below that the middle pane collapses** and its selection moves into
the rail. Same grid, one pane hidden — not a second layout, and the only
responsive rule in the system.

#### E3.6 The vertical budget is wrong today, by 18 px

`shell.py` docks the menu bar with `_NET_WM_STRUT_PARTIAL` reserving
`PANEL_H = 46`, and `shell.py:456` computes its own menu space as
`screen_h − PANEL_H`. But `nbapp.py:1013` states the layout budget as
"1024×740 … 768 minus the 28 px desktop panel", and
`tools/minsize_sweep.py:159` checks `[(1024, 740), (1366, 740)]`.

**The panel is 46 px, not 28. The real budget on a 1366×768 panel is 722 px, and
the gate allows 740.** `nbapp`'s own measurement note records the tallest app as
"Video 725" — which fits the stated budget and **overflows the real one by 3 px**,
unreachably, on that hardware.

Normative:

    CANVAS_H = screen_h − PANEL_H        # 722 at 768, not 740

This is a Bar-1 truth defect of the M1 class — a gate that passes while the thing
it guards is broken — and it is filed as such in the campaign's S1 stream. The
grid depends on it because every vertical measure resolves against `CANVAS_H`.

#### E3.7 Where a rule may fall (normative)

A hairline is permitted in exactly five positions:

1. a rail ↔ field boundary;
2. the outer edge of a card, sheet or dialog;
3. beneath a header band — toolbar, table header;
4. between rows of a list that is ruled (all rows or none);
5. above a fixed bottom bar.

**Nowhere else.** A rule floating in a field is decoration, and decoration is
what this design language does not have.

#### E3.8 What motion may do with all of this

The point of E3, and the contract Article G is written against:

- **Down a rail: vertical travel only.** A panel menu drops the width of its own
  column and no wider.
- **Across the field: horizontal travel only,** by one field-width, for
  forward/back navigation.
- **Every arriving surface rests with an edge coincident with a rule or a
  margin** from E3.7. A surface that stops 11 px from a hairline is the single
  most visible way to prove the grid is decorative.
- **No diagonal travel**, because E3.3 gives motion no diagonal to follow.
- A transform (Article C) interpolates between two rectangles that are **both**
  on the grid; only the intermediate frames are off it.

#### E3.9 Implementation

These constants belong in `tools/design_tokens.py` beside `RADIUS_SCALE`,
`TYPE_SCALE` and `SPACING_STEP`, so the E4 checker reads them rather than
re-typing them — the same arrangement `motion_selftest.py` already has with
`nbmotion.DURATION_BANDS`. `de/nbapp.py` re-exports them for apps.

### §E4 Gate

**[GAP]** `tools/grid_check.py`, reading the constants from `design_tokens.py`:

1. every sidebar width equals `RAIL`, or is a listed exception whose window
   minimum still fits 1024;
2. every bordered control height is `interior + 2` for an interior on the 4u
   ladder (E3.2);
3. every hairline occupies one of the five permitted positions (E3.7);
4. no arriving surface's rest edge is off a rule or margin;
5. no animation travel vector has both a non-zero dx and dy;
6. `minsize_sweep` measures against `CANVAS_H = screen_h − PANEL_H`, not 740.

Red-proofs, each recorded with the failure text it produced: set one sidebar to
238; give one bordered control a height of 31; place a rule mid-field; give one
card a diagonal arrival; revert the budget to 740 and confirm Video's 725 px is
reported.

---

## Article F — Affordability

Amendment 1 makes this article the price of pervasive motion. It is not
optional and it is not a later optimisation.

### §F1 Damage-limited motion (normative) **[GAP]**

**Invalidate the smallest rectangle that changed.** `queue_draw()` on a
fullscreen window is a full-screen software repaint, and at 60 Hz on a CPU
rasteriser it is unaffordable. Every animation must:

- call `queue_draw_area()` over the moving region, never `queue_draw()`;
- cache static content to a `cairo.ImageSurface` once and recomposite only the
  moving layer per frame;
- animate at most one layer at a time over a given region.

Constitution Article VI §3 already states the invalidation rule; this article
makes it a precondition rather than an aspiration.

### §F2 Never animate allocation (codification)

Restated from §0.6.6 because it is the most expensive mistake available: no
transition on `width`, `height`, `margin` or `padding`. Growth is drawn, not
allocated — a cairo-drawn scale inside a fixed allocation, or opacity.

### §F3 The frame budget applies on both paths (normative) **[GAP]**

Every transition in Article G carries a measured budget on **both** the
accelerated and the software path. "Smooth" is a number, not an impression. A
transition that cannot meet its budget on software gets **shortened or
simplified — never silently disabled**, because a transition that exists on one
machine and not another breaks §0.1 mechanism 2, inferability.

### §F4 Instant remains exactly equivalent (codification)

`nbmotion` already guarantees that a duration-0 transition lands on **exactly**
the end state an animated one would, synchronously, with the completion callback
run. Reduced Motion and any un-clocked widget take that path. Callers never need
a second code path, and that equivalence stays the gate.

### §F5 Gate

**[GAP]** `tools/frame_pacing.py` — drives each Article G transition under both
`NB_ACCEL` values and reports frame count, longest frame and total duration
against the entry's budget. **[GAP]** a static check that no animating module
calls bare `queue_draw()` on a toplevel. Red-proof: replace one
`queue_draw_area` with `queue_draw`; the pacing run must regress visibly.

---

## Article G — The inventory

Every transition in the system, named. Each entry needs a spec, an
implementation and a conformance check; **an unnamed transition is not permitted
to exist.** Origin column refers to Article B; *transform* marks entries bound by
Article C identity.

### G1 System

| Transition | Origin | Motion | Token |
|---|---|---|---|
| Boot → session | — | splash progress, always advancing | `linear` |
| Splash → desktop | splash | splash lifts, desktop settles beneath | `PAGE` |
| **App launch** *transform* | **its icon** | icon rectangle grows into the window | `PAGE` |
| **App close** *transform* | the window | collapses back into the icon | `PAGE` |
| App → app | outgoing window | outgoing departs, incoming arrives from its icon | `PAGE` |
| Desktop board appearing | — | cards settle in, staggered along their columns | `SURFACE_IN` |
| Panel menu open | its title | drops from the title, down its column | `SURFACE_IN` |
| Panel menu close | — | retracts to the title | `SURFACE_OUT` |
| Login / first-run step | previous step | directional slide along the grid | `PAGE` |
| Sleep / wake | — | field dims and returns | `PAGE` |
| Shutdown | — | desktop settles out | `PAGE` |

**App launch is the highest-value entry in this table.** Today
`finder._launch_module` spawns the process and calls `self.hide()` immediately,
so on the software path the screen shows nothing but the backdrop for a second or
more. The Finder must not stand down until the app's window maps, and the gap is
filled by this transition. `splash.py`'s always-advancing progress is the
existing pattern to generalise.

### G2 Finder

| Transition | Origin | Motion | Token |
|---|---|---|---|
| Navigate forward | the opened row | contents slide left, along the grid | `PAGE` |
| Navigate back | — | contents slide right | `PAGE` |
| **Open a folder** *transform* | the clicked row | row grows into the new listing | `PAGE` |
| List ↔ grid | — | crossfade in place (different presentations, not a transform) | `PAGE` |
| Selection change | previous row | the highlight **travels** between rows | `SELECT` |
| Sidebar reveal | its edge | slides along the column edge | `SURFACE_IN` |
| Search results | the search field | results settle in beneath it | `SURFACE_IN` |
| Copy / move / trash | the source row | the object visibly goes where it went | `PAGE` |
| Get Info | the selected row | card grows from the row | `SURFACE_IN` |

### G3 Within every app

| Transition | Origin | Motion | Token |
|---|---|---|---|
| Page / pane switch | previous pane | directional, consistent OS-wide | `PAGE` |
| Overlay card | the control that raised it | grows from that control | `SURFACE_IN` |
| Confirm | the destructive control | grows from it | `SURFACE_IN` |
| About | app-name title | drops from it | `SURFACE_IN` |
| Picker | the menu item | grows from it | `SURFACE_IN` |
| Tab / section change | previous tab | underline travels; content crossfades | `SELECT` / `PAGE` |
| List insert | insertion point | row opens and settles | `SURFACE_IN` |
| List remove | — | row closes; neighbours settle up | `SURFACE_OUT` |
| List reorder | the row's own position | rows travel to their new positions | `SELECT` |
| Inline edit begin / end | the field | field takes and releases its edit state | `FEEDBACK` |
| Toolbar state | the control | colour and border only | `FEEDBACK` |
| **Any value change** | the value | old value settles out, new settles in | `FEEDBACK` |
| **Any toggle** | the control | state travels, never jumps | `FEEDBACK` |
| Progress | — | **continuous**, never stepped | `linear` |
| Empty → populated | the empty state | empty departs, content settles | `PAGE` |
| Scroll | — | momentum, bounded rubber-band, no overshoot past the band | — |
| Zoom | the zoom origin | scales about the pointer or centre | `PAGE` |

*Pervasive means pervasive: the last several rows are the ones that make the
difference between a system that animates and a system that is alive.*

### G4 Content surfaces

| App | Transition | Token |
|---|---|---|
| Language | lesson flow, crown and streak moments | `PAGE` / `SURFACE_IN` |
| Illustrator | tool change, layer add/remove/reorder | `FEEDBACK` / `SELECT` |
| Sequencer | transport state, playhead travel | `FEEDBACK` / `linear` |
| Video | timeline scrub, clip select, trim handles | `linear` / `SELECT` |
| Media | fullscreen enter and exit | `PAGE` |
| Maps | pan and zoom | `linear` / `PAGE` |
| E-book | page turn | `PAGE` |
| 2048 | tile slide and merge | `FEEDBACK` |

**2048's tile motion is already the best in the OS and is the reference
implementation.** Anything in this table that feels worse than 2048 is not yet
done.

### G5 Gate

**[GAP]** `tools/motion_inventory_check.py` — every entry above has a named
implementation; every implementation names an entry; every entry has a recorded
frame-pacing result on both paths. An entry with no implementation and an
implementation with no entry are both failures. Red-proof: delete one
implementation binding; the check must name the orphaned entry.

---

## Article H — Forbidden

Explicitly, so that "more motion" is never read as "any motion".

**The glass vocabulary — forbidden entirely:**
blur-led styling · translucency used as depth · frosted or vibrant surfaces ·
specular highlights · glow · gradient borders · parallax.

**Motion behaviour — forbidden entirely:**
spring, bounce, elastic or any easing returning outside `[0, 1]` · overshoot ·
pulse · anything that moves while the user is not acting · anything ambient,
idle, looping or decorative · sliding in from off-screen merely to be noticed ·
diagonal travel · animating `width`, `height`, `margin` or `padding` ·
`queue_draw()` on a toplevel inside an animation.

**Affect — forbidden entirely:**
confetti · celebratory bursts · sound effects for ordinary actions · anything
that congratulates the user for using the computer.

**The test.** Would this exist in a well-made book, a good magazine, or on a
working desk? Then it is on-thesis. Does it exist only because a GPU can do it?
Then it is off-thesis, whatever it looks like.

---

## §9 — Migration inventory

Ordered. Each row is a work package under the campaign's Lane C unless noted.
**§E3 blocks Article G and must land first.**

| # | Item | Article | Blocks |
|---|---|---|---|
| 1 | Reverse the `NB_ACCEL` animation gate | §0.5 | everything |
| 2 | Damage-limited motion primitives in `nbmotion` | F1 | everything |
| 3a | ~~Define the grid~~ — **done, §E3** | E3 | — |
| 3b | Land the E3 constants in `design_tokens.py` + `nbapp` re-export | E3.9 | **all of Article G** |
| 3c | **Fix `CANVAS_H`** — panel is 46 px, not 28; budget is 722, not 740; `minsize_sweep` is 18 px too generous and Video already overflows by 3 | E3.6 | the layout gate |
| 3d | Converge sidebars on `RAIL = 240` (210 / 212 / 240 / 252 today) | E3.3 | G2, G3 |
| 4 | Anchor-carrying presentation helpers in `nbapp` | B1, B2 | G1–G3 |
| 5 | Elevation as an animated property | A5 | G1–G3 |
| 6 | App launch / close transform, and the Finder stand-down fix | G1 | — |
| 7 | Per-element depth for in-window cards (a compositor can only shadow a *window*, and all eight board cards share one) | A2, A3 | G1 |
| 8 | Finder inventory | G2 | — |
| 9 | Shared in-app inventory | G3 | — |
| 10 | Per-app content inventory | G4 | — |
| 11 | Gates: `material_check`, `origin_check`, `grid_check`, `frame_pacing`, `motion_inventory_check` | A6, B4, E4, F5, G5 | release |

Every gate above is subject to the campaign's meta-rule **M1**: it ships with a
recorded mutation that makes it fail, and the failure text it produced. A gate
nobody has seen fail is decoration.

---

## §10 — Conflicts resolved

**The theme's ease-out-everywhere rule vs. the longer tokens.** Resolved in §0.5
Amendment 2: they govern different domains and do not overlap. The theme owns CSS
state transitions at 90 ms, colour and border only. `nbmotion` owns surfaces and
pages. Neither may implement the other's domain.

**"No motion the user did not cause" vs. progress and the splash.** A progress
indicator reflects work the user *did* cause and is therefore not ambient. It is
the one continuously-moving element permitted, and only while its work is
running.

**Pervasive motion vs. the 1024×740 budget.** Unresolved by design: a transition
that cannot meet its frame budget at 1024×740 on software is **simplified, not
disabled** (§F3). If it cannot be simplified enough, the *design* is wrong, not
the budget.
