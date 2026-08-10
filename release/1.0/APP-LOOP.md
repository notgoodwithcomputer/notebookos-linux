# The per-app loop — one app per day, alphabetical

**Owner:** app-improve session. **Started:** 2026-08-07.
**Mandate:** one app per 24 hours, alphabetical, improving features, UI,
functionality and smoothness; when an app runs out of improvements, weed bugs.
Switch at midnight. Runs until the roster is done (two weeks or more).

This file is the loop's memory. A Claude session does not survive a restart, and
an in-session cron job dies with it — so the ROSTER AND THE POSITION live here,
on disk, where the next session can pick the loop back up without being told
where it got to.

## Roster

Derived from `finder.APP_MODULES` (the desktop's own launch table — the same
source `minsize_sweep` uses, so a newly added app joins the loop automatically),
ordered by module name. Excluded, with reasons:

* `gbasdk`, `gbahelp`, `gbabuild`, `gbaworkspace` — the GBA SDK has its own
  dedicated session (user's instruction). `gbaemu` is the emulator front-end
  rather than the SDK, so it stays in the roster, but check CLAIMS.md before
  touching it — the GBA session's lane runs close to it.
* `installer` — campaign-owned (LANES.md).
* `shell`, `widgets`, `widgetsettings`, `login`, `splash`, `firstrun`,
  `desktopbg`, `nb*`, `x*` — desktop/session infrastructure and shared modules,
  not apps, and campaign-owned.

| # | Module | Day | Status |
|---|---|---|---|
| 1 | academics | 2026-08-07 | **DONE** — task 030, 17 items, 8 → 17 suites, 238 → 252 checks |
| 2 | accounting | 2026-08-08 | **DONE** — task 046; 19 defects + 1 feature + the add-entry fast path (245ms -> 17ms); 4 -> 20 suites, 401 checks. PDF export, keyboard, RTL and salvage had NO coverage; the RTL find became an OS-wide gate. Date ordering handed up as a product decision. |
| 3 | bills | 2026-08-09 | **DONE** — task 047; 4 defects fixed incl. DATA LOSS ON OPEN (3 sites) and a forgotten sort preference; 2 -> 8 suites, 19 -> 357 checks, 28 red proofs. 49 of 78 methods had no coverage. Now in the OS-wide damage + preservation gates. Also fixed accounting's 3rd preservation site, found by that gate. |
| 4 | calculator | 2026-08-08 | **DONE** — task 048; **17 defects**. DATA LOSS on the second open (a store that parses but is not an object); a session-wide silent save gate; the digits were not in a number pad; the card did not fit 1024x722; the graph redrew at ~8fps; **the whole keypad was mirrored in Yiddish and the bracket keys had swapped faces** — the key that looked like `)` typed `(`; the trace walked off the graph and never came back; Zoom Fit on a flat curve crashed the draw; a typed NaN window and a zero Table Step both reached the maths; STO→ could not store a negative typed on the keypad. 5 → 10 suites, 258 → **421 checks**, 0 red. FOUR of six existing suites hardcoded the module path, so every red proof against them was vacuous. Mutation sweep: 98 mutations, 82% of behaviour-changing mutants caught after triaging all 47 survivors by fingerprint diff; all 11 real gaps closed. Two OS-wide gate blind spots + two sweep-methodology findings handed up. |
| 5 | calendar | 2026-08-09 | **IN PROGRESS** — claimed 00:05, task 049. Baseline 9 suites / 157 checks; now **10 / 218**. calendar_quickadd_selftest (61 checks, 9 red proofs) + 3 defects, all found by RENDERING in German: the French "today" needed a TYPOGRAPHIC apostrophe the keyboard cannot make; Turkish lost its word when typed in capitals (str.lower() is not locale-aware, dotted vs dotless i); noon/midday/midnight were the only day-time vocabulary still hard-coded to English. Recurrence checked and CORRECT — primitives and caller both — so the shortlist's top item is settled. Reconnaissance from 2026-08-08 20:20: unclaimed (last claim released 08-07; bugfix's 08-08 05:40 pass committed and done). 3644 lines, **9 existing suites**, and already in BOTH damage gates with a fixture (calendar.json + calendars.json) — so it is NOT on the defended-untested list and the day-4 job of building a first guard does not apply. The question here is what nine suites still miss. Known trap: the module SHADOWS stdlib `calendar` on PYTHONPATH, so run construct_all_host after every change. **Baseline taken 22:30 read-only: 157 checks, 0 failing** (accessibility 6, adversarial 19, customization 14, event_accessibility 13, lifecycle 21, mirror 44, month_keyboard 14, rollover_lifecycle 8, selftest 18). The shape to note: 157 checks over 3644 lines is **1 per 23 lines**, where calculator STARTED at 1 per 8 and closed at 1 per 5. So calendar is covered BROADLY — nine suites touching many areas — and THINLY. Expect the day to be about depth inside areas that already have a suite, not about finding unguarded ones.

**Method-coverage map, taken 23:05 read-only.** calendar.py defines 135 functions; **94 are never NAMED by any of the nine suites**, of which 46 are widget/handler plumbing (fine) and **48 are domain logic**. Treat "named" as a PROXY, not proof — a suite can drive a method through the UI without naming it, and calendar is already in both damage gates, so the store paths below are exercised at least indirectly. The ranked shortlist for day 5:

* **recurrence** — `_next_repeat`, `_whole_periods`, `_series_members`. This is the heart of a calendar and where the notorious bugs live (month-end, DST, leap years). The bugfix lane already found one DST defect of exactly this family in language.py on 08-08.
* **quick natural-language entry** — `parse_quick_event`, `_quick_parse`, `_word_tokens`, `_parse_time`, `_day_month_year`, `_hhmm_to_hours`. A parser with no suite naming it.
* **date maths** — `_monthrange`, `_week_dates`, `_shift_month`, `_covers`, `_month_has_events`, `_date_to_iso`, `_iso_to_date`.
* **the store** — `_quarantine_store`, `_read_events_file`, `_merge_disk_events`, `_norm_calendars`, `_ensure_id`, `_content_key`, `_mark_seen`.
* **document save/load** — `_serialize_document`, `_apply_document`, `_write_document`, `_load_document`, and the four `_file_*` entry points.
* **menus / keys** — `menu_items`, `_on_key`, `month_key_target`. Day 4 found the keyboard and menu surface unguarded in calculator too, and it is where "advertised but dead" shortcuts hide. |
| 6 | contacts | 2026-08-12 | pending |
| 7 | cookbook | 2026-08-13 | pending |
| 8 | ebook | 2026-08-14 | pending |
| 9 | g2048 | 2026-08-15 | pending |
| 10 | gbaemu | 2026-08-16 | pending — check CLAIMS.md first |
| 11 | illustrator | 2026-08-17 | pending |
| 12 | journal | 2026-08-18 | pending |
| 13 | language | 2026-08-19 | pending |
| 14 | maps | 2026-08-20 | pending |
| 15 | mealplanner | 2026-08-21 | pending |
| 16 | media | 2026-08-22 | pending |
| 17 | music | 2026-08-23 | pending |
| 18 | novel | 2026-08-24 | pending |
| 19 | packages | 2026-08-25 | pending |
| 20 | screenplay | 2026-08-26 | pending |
| 21 | sequencer | 2026-08-27 | pending |
| 22 | settings | 2026-08-28 | pending |
| 23 | sysmon | 2026-08-29 | pending |
| 24 | tasks | 2026-08-30 | pending |
| 25 | terminal | 2026-08-31 | pending |
| 26 | usbwriter | 2026-09-01 | pending |
| 27 | video | 2026-09-02 | pending |
| 28 | workout | 2026-09-03 | pending |
| 29 | writer | 2026-09-04 | pending |

Dates are the intended schedule, not a promise: the rule is one app per day in
this order, so if a day is missed the roster continues from the first `pending`
row rather than skipping to match the calendar.

## The routine for each app

1. Claim the module in `CLAIMS.md` before the first edit (LANES.md rule 1).
2. Baseline: run every `tools/<app>_*_selftest.py` and record the numbers
   BEFORE touching anything. A later "all green" means nothing without it.
3. Render the app at the real budget (1024×722) in English and at least one
   long-word language, and LOOK at it. Most of day one's findings came from
   this and from measuring, not from reading code or the ROADMAP.

   **AND WHEN A MEASUREMENT DISAGREES WITH THE PICTURE, BELIEVE THE PICTURE
   until you know why.** Five times over the first two app-days a probe reported
   a defect that was not there — a card centred for the host monitor, a widget
   asking an unrealised tree whether it was visible, a comparison picking the
   wrong label out of a row. Twice the instrument reported the exact opposite of
   the truth. Measuring is still the method; it just is not self-verifying.

   **TRAP — overlay cards and the offscreen harness.** The usual render trick
   reparents the window's child into a `Gtk.OffscreenWindow`, which leaves the
   real window with no allocation. Any app that centres a modal card on
   `self.get_allocation()` then falls back to `nbapp.screen_size()` — the HOST
   monitor — and the card is centred for 1920 while the render is 1024, so it
   sits half off the right edge with its contents clipped. That looks exactly
   like a serious layout bug and is not one. Pin the app's size accessor before
   rendering (`app._overlay_size = lambda: (1024, 722)` in accounting) and
   re-render before believing it. Cost me a near-miss false report on day 2;
   every app with a modal overlay will hit it.

   **THE SAME TRAP, ONE LEVEL DEEPER — `nbapp.screen_size()` AT BUILD TIME.**
   An app may size its ordinary layout from the screen, not just its overlays:
   `bills.py:1034` sets its detail column to
   `max(430, min(COLUMN_W, sw - SIDEBAR_W - 112))`. Rendered offscreen, `sw` is
   the HOST monitor, so the app builds a 1920 layout, comes out 1172px wide, and
   the bill's AMOUNT and its Edit button sit past the right edge. That looks
   like the worst kind of overflow bug in a bill tracker and is entirely the
   harness — at a real `sw` of 1024 the column is 660 and everything fits. Pin
   it BEFORE constructing the app:

       import nbapp; nbapp.screen_size = lambda: (1024, 768)

   (768, not 722: the app gets 722 after shell.py's 46px strut, but
   `screen_size` reports the panel.) Day 3 cost me three false readings before
   I chased the inconsistency — the top-level reported a 1024 allocation while
   its children summed to 1172, which a Box can never do, and that is the tell.

   **AND uishot CANNOT RENDER AT THE BUDGET ON ITS OWN.** `Gtk.OffscreenWindow`
   allocates its child the child's NATURAL size, and `set_size_request` is only
   a MINIMUM — so `uishot.shot_window(win, 1024, 722, ...)` silently renders any
   app whose natural width exceeds 1024 at its natural width instead. A
   ScrolledWindow does not fix it (EXTERNAL clips, NEVER requests natural,
   AUTOMATIC scrolls). What works is a container that reports the budget as both
   its minimum AND its natural size and allocates its child exactly that —
   including `do_get_preferred_height_for_width`, because GTK3 lays out
   height-for-width and without it the child collapses to its minimum height.
   See `scratchpad/clamp.py`. Filed for the campaign in HANDOFF.md.
3b. **Render it RIGHT-TO-LEFT as well.** `nbi18n.RTL == {"yi"}` — Yiddish ships,
   and `nbapp` calls `Gtk.Widget.set_default_direction(RTL)` for it, flipping
   widget order for the whole process. Two app-days in, NO app had ever been run
   in that state. GTK mirrors containers for free, so the layout will usually
   look right and tempt you to move on — the damage is inside the LABELS:

       Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)   # then build

   A "+" or "-" in front of a number is a bidi-WEAK character before a run of
   European numerals, so the Unicode algorithm resolves it to the paragraph
   direction and lays it out on the FAR SIDE. Accounting held '+$1,105.00' and
   Pango drew '$1,105.00+'; a debit drew as '$950.00-'. **Unsigned figures were
   unaffected, which is how it hides** — the headline and the balance column
   looked perfect. Fix: wrap the finished string in U+2066..U+2069 (an isolate,
   not an LRM, so it cannot leak into neighbouring text), gated on
   `Gtk.Widget.get_default_direction()` — the direction actually in force, not
   the language name — so the other sixteen languages are untouched byte for
   byte. See `Accounting._ltr` and `tools/accounting_rtl_selftest.py`, which
   measures the VISUAL order Pango resolves rather than checking the isolate is
   present: the character being there proves the fix was applied, not that it
   worked.

   Suspect any label mixing a sign, a unit or a bracket with digits: `+%d more`,
   `+%d XP`, `%d%%`, `(3)`, a time range, a temperature.
4. Work the list: real defects first, then features, then polish.
5. Every change gets a check, and every check is WATCHED FAILING before it is
   trusted (M1). Three of day one's own gates were vacuous or too weak, and
   every one was caught by running the mutation rather than by reading it.

   **NEVER mutate the app file to red-proof it.** A Codex dispatch once mutated
   `accounting.py`, then "restored" it from a snapshot taken before another
   session's fix, silently reverting landed work. Mutate a COPY and redirect the
   import — `scratchpad/redproof.py` does this with a `sys.meta_path` hook, and
   the hook must let the SUITE trigger the import, because pre-importing the
   copy caches `HOME`/`TX_FILE` from whatever `NB_HOME` is set at that moment
   and every ledger then comes up empty.

   **A CRASHED red proof reads exactly like a clean pass.** Twice on day 2 a
   mutation produced no output at all — once an IndentationError from dropping a
   line without its leading spaces, once a TypeError from calling a method with
   an argument it does not take — and in both cases the run printed nothing and
   looked like "no failures". The harness must say `*** SUITE CRASHED ***` out
   loud. And a suite that raises under a regression reports nothing, so read
   optional state with `getattr(app, "_layer", None)`, never as an attribute.

   **A check must not read past its own subject.** A source-level check that
   opened the app file *by its expected path* stayed green against a mutated
   module. Read `module.__file__` / `inspect.getsource(...)` so the check
   follows the red proof into the copy.

   **A second route to a figure hides a broken first route.** A 32-check PDF
   suite passed with every row of the BALANCE column printing `-$1.00`, because
   the only balance checked was the footer's — computed by a separate line the
   mutation never touched. Neighbour of "zero hides an addend".
5a. **A DECISION BEHIND A `dialog.run()` IS UNREACHABLE BY EVERY SUITE, AND
   THAT IS WHERE THE BUGS ARE.** Three of calculator's seventeen were sitting
   inside modal dialogs, and the reason all three shipped is the same: no test
   can drive a modal loop, so nothing had ever executed those lines.

   * the Window dialog accepted an all-NaN window (every ordering comparison is
     False against NaN, so all four guards passed it) and the next draw died
   * STO-> stored nothing for `1+2`, `sqrt(9)`, `2*PI` or `-5` — silently, the
     dialog just closed — because it read `float(self.expr)` and the keypad's
     minus is U+2212, which `float()` will not take
   * two dialogs formatted numbers with `str()` instead of the app's own
     formatter, so a stored 1 read "1.0" and 0.1+0.2 read "0.30000000000000004"

   Two moves, both cheap:

   * **Lift the DECISION out into a named function** the suite can call —
     `window_is_valid(values)`, `_store_value()`. The dialog keeps the widgets;
     the judgement moves somewhere a check can reach it.
   * **Stub `Gtk.Dialog.run` to return CANCEL** to build and read a real dialog
     with no modal loop. That one technique opened calculator's last unexamined
     surface, and it is four lines.

   ...and while lifting a guard out, **write it as what a GOOD value IS, not as
   a list of bad ones.** `if xmin >= xmax or ...: raise` passes NaN, because NaN
   compares False to everything including `>=`. `xmin < xmax and ...` refuses it
   for exactly the same reason. Measured: with the explicit `isfinite` line
   removed, the positive form still rejects every NaN case. The bug was the
   SHAPE of the guard, not a missing condition.

5b. **MEASURE THE SUITES, not just the app — a sentinel-checked mutation
   sweep.** Once an app stops yielding defects, the useful question changes from
   "is the app right" to "would these checks notice if it stopped being right".

       SWEEP_MOD=<app> tools/guestrun.sh python3 tools/mutation_sweep.py

   It found real gaps in two apps that were already "done", and the scores are a
   fair statement of where each stands (accounting 78% caught, bills 85%, every
   survivor equivalence-measured).

   **It is a DIAGNOSTIC, not a ratchet, and not in `run_all_gates`** (campaign's
   call, and the right one): ~5 min per app, it needs manual per-app setup, and
   a score FLOOR is premature — scores move legitimately as equivalent mutants
   come and go, so a hard ratchet would fail in the false direction. Revisit
   once a dozen apps have run clean with triaged survivors.

   **Adding an app costs two things, and the second is the one that matters:** a
   `REGIONS` range naming its domain logic, and a `SENTINEL` — a mutation whose
   catch you have WATCHED. The harness refuses to print a score if the sentinel
   survives. Do not reach for "the first candidate": on bills that is one of the
   genuinely EQUIVALENT mutations, so it failed for the right reason and the
   wrong cause. **A sentinel that can legitimately survive is not a sentinel.**

   **...and a sentinel that comes back CAUGHT can still be lying.** Day 4 got
   `0 of 14 survived — 100% caught`, sentinel green, out of a harness where no
   suite could import the module at all. `caught()` scores on
   `returncode != 0`, and an import error exits non-zero exactly like a failed
   check. The one check meant to prove the harness works is the one that cannot
   notice it is broken. Two habits fix it:

   * **Look in the work directory.** It must contain `<app>.py`. That single
     `ls` is what separated day 4's valid runs from its void one.
   * **Distinguish FAIL from CRASH.** If the suite output carries no `FAIL`
     line, record the catch as `[CRASH]`. A run made of crashes then looks
     wrong at a glance instead of looking perfect. Measured on calculator: 56
     catches were 39 real failures and 18 crashes, and you want to know which.

   **Triage survivors by FINGERPRINT DIFF, never by reading them.** Reading is
   how you conclude "obviously equivalent" and lose real gaps — bills 7 of 16,
   calculator 11 of 47. Write one probe that prints a JSON dict of a few hundred
   observable values: every module-level function over a spread of inputs,
   `sanitize_state` over a table of damaged stores, a battery of expressions
   through `evaluate`, key sequences through `press`, the store round-trip. Run
   it against the true module, then against each survivor, and diff.

   **Prove the probe deterministic FIRST** — run it twice against the SAME
   module and require zero differences. A probe with any wobble makes every
   mutant look changed and you will not notice.

   **Order the suites fastest-first.** `caught()` returns on the first suite
   that fails, so the order decides what a caught mutation costs. A
   subprocess-driving damage suite left in alphabetical position makes every
   caught mutation pay two minutes for it.

   **The nine default swaps are comparison and boolean only — they cannot touch
   a FORMULA.** For anything doing arithmetic that leaves the real maths
   unswept: calculator's sample spacing, its trace label's `+ 1`, its zoom
   factors. Adding `+`/`-`, `*`/`/`, `//`, `**`, off-by-one on literals and
   index `[0]`/`[1]` found three gaps no comparison swap could reach. Keep it
   opt-in — arithmetic mutants are far likelier to be genuinely equivalent and
   will muddy the comparison score if mixed in.

   Flip ONE decision point at a time in a COPY of the module (`<=`->`<`,
   `and`->`or`, `is None`->`is not None`), run every suite for that app against
   the copy, and report the SURVIVORS. A survivor is a change no check notices —
   a gap in the app's own terms rather than a coverage percentage.

   **The three traps, all of which cost me a wrong answer first:**

   * **Mask strings and comments before choosing an operator.** Skipping lines
     that START with `#` or `"` is not the same as skipping strings: it happily
     rewrites `and` inside docstring PROSE, and 15 of the first 55 "survivors"
     were exactly that. Use `tokenize` and blank out every STRING and COMMENT
     span first. A sweep that mutates comments manufactures gaps in the shape of
     real findings.
   * **REDIRECT THE IMPORT, never an env var.** Passing `<APP>_MODULE_DIR` and
     running each suite as a subprocess only works for suites that read it —
     6 of 20 for accounting. The rest imported the REAL module, so most
     survivors were never tested at all and both published scores were wrong.
     A `sys.meta_path` hook that returns a spec for the copy works whatever the
     suite does, and `runpy.run_path` the suite under it.
   * **SENTINEL-CHECK THE HARNESS BEFORE BELIEVING A NUMBER.** Pick a mutation
     you KNOW breaks a check, run it, and require it to come back CAUGHT. One
     extra run; it would have caught all of the above before a figure was
     published. This is class #15 in the campaign taxonomy — *a check reads past
     its own subject* — and the sweep is the third instance in three days.

   **Triage survivors BY MEASUREMENT, never by reading them.** "Equivalent
   mutant" is a claim like any other. Apply each survivor to a copy and compare a
   battery of real calls against the true module. On bills I dismissed 16 by
   reading and **7 of them actually changed behaviour**, `add_days` among them —
   the function that computes the POST-BY deadline, wrong by a day across month
   edges. And **a battery only certifies what it exercises**: it pronounced a
   `fmt_due` mutant equivalent purely because it never called `fmt_due`. Check
   what the battery covered before trusting its verdict, and label anything it
   never called "unexercised", not "equivalent".

   Report the score as *caught / behaviour-changing*, and say plainly that
   "measured equivalent" means equivalent across the battery's inputs — evidence,
   not proof.
6. Close: all of that app's suites, plus construct_one, plus the OS-wide gates
   the change could touch (voice_check and jargon_sweep carry ratchet ledgers —
   see LANES.md).
7. Write `release/1.0/tasks/NNN-<app>-day.md` (next free integer — check
   CLAIMS.md for numbers already promised to in-flight dispatches) and update
   the row above.

   **New English strings.** LANES rule 1 was AMENDED: any session may ADD
   catalog keys for its own new strings, provided all 17 languages are
   translated in the same change and the catalog checks are run. What stays
   campaign-only is EDITING or DELETING existing keys, renames, and running
   `i18n_merge.py`. If you cannot translate responsibly, list the strings in the
   task file and the campaign merges them — that is the
   `release/1.0/i18n-fragments/NNN-<app>/<lang>.json` route, and the campaign
   DELETES a fragment once its keys are in the catalogs, which is the protocol
   working, not data loss.

   **Prefer adding a sentence to rewording one.** A reworded string is a string
   with seventeen stale translations. Accounting needed one new fact on a
   recovery message and got it by joining a NEW complete sentence to the
   already-translated one (`_unreadable_note`), so the merge cost one short
   string instead of seventeen re-reviews.

8. **RELEASE THE CLAIM** in `CLAIMS.md` when the day is done — a `done` line
   naming what changed, the suite/check counts, and anything left for the
   campaign. An unreleased claim blocks every other lane from the module
   indefinitely, and the two app sessions must never hold the same file. If a
   later day needs to touch a released app (a gate finds something, a rule
   changes), take a SHORT re-claim, do the one thing, and release again.

## Known coverage debt, for the days that reach these apps

From a sweep on 2026-08-08 (details in HANDOFF.md): eight apps in this roster
persist a store and have NO damage-path coverage anywhere — not in the OS-wide
`store_damage_selftest`, not in a suite of their own:

    calculator · ebook · g2048 · maps · novel · terminal · video · writer

(ebook and novel were wrongly listed as covered at first — their suites matched
a keyword in a COMMENT, not a damaged store. Eight, not six. That heuristic was
wrong 2 times in 11, so treat any "covered" claim in this file as unverified
until a suite has been watched writing a broken store.)

ALL EIGHT ARE NOW MEASURED, and **every one of them defends correctly** — eight
to eleven damaged shapes each (not json, empty, bare number, bare string,
top-level list, truncated, trailing garbage, all-nulls), 0 crashes, 0 data lost:

    writer · video · novel ....... original kept as `.bak` on every bad store;
                                   writer carries the scar at `writer.py:139`
                                   from the incident that hardened it
    ebook ........................ quarantines as `ebook.json.damaged-<stamp>`
    terminal ..................... `.damaged-<stamp>`, or `.bak` on a wrong type
    g2048 ........................ `.bak` every time
    calculator ................... several shapes it declines to rewrite at all
    maps ......................... READ path safe by measurement AND by
                                   construction (`_load_cfg` catches
                                   OSError/ValueError, returns `{}`). Its WRITE
                                   path is still unmeasured: `_save_cfg` opens
                                   with `if not self.pack: return`, so a probe
                                   without a real map pack never reaches it.
                                   Needs a pack fixture. Do NOT read the clean
                                   row as a clean app.

So all eight are COVERAGE debts, not open wounds: the defence is real, and
nothing guards it. When a day reaches one of these apps the job is to write the
damage suite that PINS the existing defence — not to build a defence. Do that
before feature work; a hardening nothing guards is one refactor from being
undone, and this project has lost a term of notes, a year of recipes and three
apps in one week to exactly that.

**And keep the two questions apart: "is it defended" and "is it guarded".** I
conflated them in the sweep that produced this list and had to correct a peer
session twice in an hour.

## Measuring a performance claim

**One before/after pair on a shared box is not a measurement.** Day 4's first
pair said the graph got 8x faster; a second pair, minutes later on the same
machine, said 1.6x. Both were single samples of a number that drifts more than
the effect does. Alternate the two builds — A, B, A, B, A — and compare
MEDIANS:

```
  before   median 129.0 ms   (min  86.7, max 238.1)
  after    median  39.8 ms   (min  16.6, max  44.7)
           3.2x on medians, 5.2x on bests
```

The honest answer was ~3x. The 8x figure had already been written into a task
file, a suite docstring and a message to the user before it was checked.

**Do not put a wall-clock bound in a suite.** That check has now been wrong
three ways on one app, each failing differently:

* `"under 60ms"` — true measured alone, RED the moment ten suites ran back to
  back. Exactly what its own comment predicted.
* a warm-vs-cold RATIO — load does NOT cancel out. Scheduler noise is ADDITIVE,
  so both halves inflate equally and the ratio is squeezed toward 1. It went red
  as collateral on two mutations that never touched the thing being timed.
* counting cache MISSES — invisible to a mutation that stops consulting the
  cache at all, because the counter only sees lookups that happen.

**Count the work instead, at the lowest layer every route must pass through.**
Calculator's became "a 401-sample curve parses its expression once, not once per
sample", counted at `ast.parse`. Exact, and no amount of load can move it.

## The trap that cost an hour on day 4

**Never `ln -s` repo files into a scratch directory you are going to write to.**
The red-proof recipe builds a mirror of `de/` and drops a mutated module in it.
Linking the mirror is the obvious way to do that — and then

    git show HEAD:.../calculator.py > $SCRATCH/mirror/calculator.py

writes THROUGH the symlink and overwrites the real module in the repo. A whole
morning of edits went in one redirect, with no error and nothing in `git status`
except a file that had quietly returned to HEAD. **Copy, never link:**

    cp $DE/*.py $DE/*.json $SCRATCH/mirror/

The tell, if it happens: `git diff` on the file you have been editing all day is
suddenly empty.

**And do not `pkill -f <pattern>` to stop a background run.** The pattern
matches your own shell's command line, so the shell dies mid-command and
everything after it in that call silently does not run. Twice on day 4. Use
`ps -eo pid,args | grep "[s]weep_calc"` — the character class stops the grep
matching itself — and kill by PID. The work is recoverable from the transcript; the measurements
taken before the clobber stay valid, because they ran against the file as it
was at the time.

## Two things that will fail a gate if you forget them

**PRESERVE_DEBT is a ratchet that fails on STALE debt.** The OS-wide
store-preservation check lists the apps known not to round-trip unknown keys.
When a day fixes one, **remove its PRESERVE_DEBT entry in the same change** — a
fixed app still listed fails the gate by design, so the bookkeeping is forced.
Currently listed and waiting for their day: `cookbook` (7), `mealplanner` (15),
`tasks` (24), `workout` (28). `calendar` and `journal` belong to the bug-fix
lane.

**A `minsize_fixtures/<store>.json` is needed only for a particular shape.**
The sweep measures EMPTY apps unless a fixture seeds a store, so an app whose
populated content is wider than its empty chrome is measured wrongly. But the
test is not "does it have a fixed column" — it is whether the populated state
adds a fixed-column ROW GRID that **sums across the row**:

    accounting   QUALIFIES — five fixed money columns sum across a row; the
                 fixture moved its German figure 964 -> 1001, i.e. 23px from
                 the edge, which the empty sweep could not see
    bills        QUALIFIES — fill-the-panel, 622 empty vs 1012 populated
    cookbook     does NOT — its content flows INTO fixed columns and ELLIPSISES
    academics    does NOT — measured +5px in English, 5px NARROWER in German;
                 its empty-state message is the widest thing in the app
    music        OUT OF REACH — its library is a filesystem scan, not a store

Send a fixture to the campaign rather than landing it; they own the set and
verify before adopting.

## Handover note

If you are a session picking this up cold: read LANES.md first, then the last
`DONE` row's task file for the working method, then start on the first
`pending` row.
