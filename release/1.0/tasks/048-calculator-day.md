# 048 — calculator, day 4 of the per-app loop

Claimed 2026-08-08 11:59, `de/calculator.py`. Baseline: 5 suites, 258 checks,
0 red — the best-covered app the loop has opened so far.

Going in, the job was supposed to be small: `store_damage_selftest.py:649` lists
calculator as *"defended-untested: several shapes not rewritten at all; the
tape; needs a gate"*, so the day was to build the guard for a defence that was
already there. Measuring the defence is what turned the day over. It was the
wrong defence, and it was hiding two ways to lose a person's work.

Everything below was found by rendering the app at 1024x722 and by planting
damaged stores and opening the app — never by reading the source and reasoning
about it. Three of my own predictions were wrong and are recorded as wrong.

---

## Defects fixed

### 1. A store that parses but is not an object was destroyed on the second open

`_load_state` read `data if isinstance(data, dict) else {}`. A `calculator.json`
holding a JSON **array** or a **bare string** — the shape a bad merge or a sync
tool that concatenates files produces — is valid JSON, so
`nbapp.preserve_damaged` waved it through to its "keep one previous-good copy"
branch. The app saw "not a dict", opened blank, and the close-time flush wrote
that blank over the file. On the SECOND open the `.bak` was refreshed from the
blank state and the last copy was gone.

Measured on the module as it stood, a store holding three variables and a tape:

```
                     open+close #1        open+close #2
  array payload      kept in .bak         kept in .bak        survives
  bare string        kept in .bak         *** NOTHING ***     LOST
```

**Two opens, two closes, no user action, no message.**

The array surviving is what makes this easy to miss, and I nearly published the
wrong mechanism because of it: `preserve_damaged`'s `_bak_would_shrink` guard
compares payload *weight*, and an array of the user's real keys outweighs the
blank default while a bare string does not. Whether the loss reproduces depends
on the shape of the damage, not on the app.

Fixed by calling the helper that exists for exactly this and that this app
called nowhere: `nbapp.quarantine_unrecognized`. Both damage classes now move
the original to `<name>.damaged-<stamp>` at LOAD, before the first save.

> **Correction to my own first report.** I initially wrote that one open+close
> destroyed three variables. That was measured by reading only
> `calculator.json` and missing the `.bak` beside it. The loss is real but it
> takes two cycles and only for payloads lighter than the blank default. The
> figures above are the re-measured ones.

### 2. An unreadable store silently disabled saving for the whole session

`_load_state` set `_store_readable = False` on any parse failure and
`_save_prefs` returned early for the rest of the run. The damaged file survived,
and so did nothing else: everything the person did afterwards was dropped at
close, with no warning at open and none at close.

`contacts.py:494` already records this exact cure shipping in journal and the
save-failure gate catching it. It was still here because
**`save_failure_selftest.py` has no calculator case.**

`_store_readable` now means one narrow thing — the original could not be moved
out of harm's way — which is the only case where refusing to save is the lesser
loss. Measured after: all three unparseable shapes now persist the session's
work, and the original bytes are in a `.damaged-` file.

### 3. Nothing ever told the person

No damage notice existed anywhere in the app. Their stored variables and tape
were simply not on screen. Added a dismissible notice on the card's own paper
above the readout, revealed only when the file could not be READ — not when
`sanitize_state` merely repaired odd fields, which stays silent:

```
  truncated empty notjson array string   -> notice shown, work saved, bytes kept
  wrongtypes desync nan                  -> silent, repaired, nothing moved
```

### 4. The digits were not in a number pad

`KEYS` is written in six groups. Five are six wide and read exactly as a
calculator does — `√ π 7 8 9 ÷` / `x² e 4 5 6 ×` / `1/x x! 1 2 3 −` /
`± % 0 . = +`. The first group is **eight**: the function keys. Folding all 38
with `divmod(i, 6)` pushed every row after the first two cells left:

```
  )   ⌫    √    π    7  8      <- 7 and 8 marooned at the end of a row
  9   ÷    x²   e    4  5      <- 9 alone at the start of the next
  6   ×    1/x  x!   1  2
  3   −    ±    %    0  .
  =   +    .    .    .  .      <- and a hole four cells wide
```

Nothing was wrong with the list; it was folded at the wrong width. The eight
function keys now get their own eight-wide strip and the remaining **thirty**
fold at six into exactly five full rows — so no key had to be added, moved or
removed, and there is no empty cell anywhere.

### 5. The card did not fit the OS's own smallest panel

`sw, _sh = nbapp.screen_size()` — the card sized itself from the screen WIDTH
and threw the height away. At 1024x768 the card wants 732px and gets 595 once
the 46px shell strut, the view bar and the stage padding are taken out, so the
bottom of the keypad — **"=" among it** — sat below the fold and had to be
scrolled to.

```
  before   card 732   avail 595    57px below the fold
  after    card 507   avail 647    140px headroom  (with the notice up: 97px)
  1440x900 card 651   avail 773    full-size layout preserved, not compacted
```

### 6. `_load_prefs` — a second reader of the same file

Defined, never called by the app: `__init__` has taken the angle mode from
`_load_state`/`sanitize_state` since the tape was added. It opened the same file
with *different damage semantics* — no quarantine, no notice, no
`_store_readable` — so wiring it back up for one small preference would have
quietly restored the loss above. Deleted, with a comment where it stood.

It was not, however, unused: see below.

### 7. The graph view redrew at about 8 frames a second

`_eval_x` substituted the sample INTO the source — `sin(X)` became
`sin((1.2345))` — so each of the 401 points in a curve was a brand-new string
to be re-mangled, re-parsed, re-`_PowGuard`ed and re-compiled. Every zoom, every
pan and every arrow key while tracing paid for all of it again.

```
  one _draw_graph, one curve, 900x500 — five INTERLEAVED rounds
    before   median 129.0 ms   (min  86.7, max 238.1)   ~8fps
    after    median  39.8 ms   (min  16.6, max  44.7)   ~25fps
             3.2x on medians, 5.2x on bests
```

> **Correction to my own first figure.** I published this as "130 ms → 16 ms,
> 8x". That was one before/after pair on a shared box. A second pair taken
> minutes later said 1.6x. Neither was a measurement — the machine drifts more
> than the effect does. Alternating the two builds five times and comparing
> medians is what the numbers above are, and ~3x is the honest answer. The 16.6
> ms best case is real, it is just the idle end of a wide spread.
>
> The same slip reached the suite's timing check: it asserted "under 60 ms",
> passed alone, and went red the moment ten suites ran back to back — exactly
> the fragility its own comment predicted. It is a load-cancelling *ratio* now,
> and defeating the cache had to be done per LOOKUP, not once per draw (clearing
> it per draw only costs one of the 401 compiles, which is why that first ratio
> came back a meaningless 1.1x).

X is bound as a NAME in the evaluation environment instead. Single uppercase
letters are already this calculator's variables, so nothing new was needed to
resolve it — and because the source no longer changes per sample, one compiled
form serves the whole curve (`_CODE_CACHE`, keyed on the mangled source).

The risk that came with it is the reason `calculator_graph_selftest` exists: a
cache keyed on source text is only correct if the source is the ONLY thing that
decides the answer, and it is not. `sin(X)` differs between degrees and radians;
`A+1` differs as A changes; `Ans` changes with every `=`. Those live in the
environment, which is rebuilt per call, so the cache holds the parse and never a
value. Red proof 4 wraps a result cache around the eval and **exactly those four
checks fail together** — which is the argument for the design, measured rather
than asserted.

Found by rendering the Graph view, which had never been rendered: the first
attempt simply timed out.

### 8. The Table view's numbers were centre-aligned

Every cell was a plain `Gtk.Label`, which centres. So a column of numbers had
its digits centred against each other and the decimal points wandered row to
row — `0.0174524064373` over `0.13917310096` over `0.5`, none of them lined up,
and the column could not be read downwards at all. Right-aligned, with the
header given its own weight and a rule under it.

The 12-significant-digit precision is NOT a defect and was left alone:
`format_number` is `%.12g` for the display, the trace and the table alike, so
it is one deliberate mode rather than an inconsistency.

### 9. In Yiddish the whole keypad was mirrored

`nbapp` sets the process-wide GTK direction to RTL for `nbi18n.RTL` languages,
and `Gtk.Grid` mirrors its columns with it. Nothing told it not to:

```
      LTR                              yi, as it shipped
      √ π  7 8 9 ÷                     ÷ 9 8 7  π √
      x² e  4 5 6 ×                    × 6 5 4  e x²
      1/x x! 1 2 3 −                   − 3 2 1  x! 1/x
      ± %  0 . = +                     + = . 0  % ±
```

Digits are written left to right in Yiddish, Hebrew and Arabic alike, and every
calculator sold into those markets has the standard Western pad. A mirrored one
is not a translation, it is a different machine — and the failure mode is
silently wrong arithmetic from correct muscle memory, not an error.

### 10. ...and fixing that left the key FACES mirrored

Pinning the grid straightened the geometry and the pad *looked* right. It was
not: under an RTL paragraph direction the bidi algorithm draws `(` as `)`, so
the two bracket keys had swapped faces while still inserting what they always
had. **The key that looked like `)` typed `(`.** Only reading the bracket row
caught it. The faces are pinned separately, and the suite checks geometry and
glyphs apart for exactly this reason.

The readout went with them: it is a number, it must hug the same edge in every
language, and it was sitting against the left with its room to grow on the wrong
side.

Deliberately **not** pinned, and checked so the fix cannot quietly become a
blanket one: the kicker labels, the view bar and the menus are text, they are
translated, and they keep reading right to left. The rule is that the
INSTRUMENT is left-to-right and the CHROME follows the language.

### 11. Zoom Fit on a flat curve stopped the graph painting

`_zoom("fit")` set `ymin, ymax = min(vals), max(vals)`. For a CONSTANT function
those are the same number, so the window had no height and `graph_to_pixel`
divided by it on the very next draw.

**Y1 = 5, press Zoom Fit, ZeroDivisionError inside the draw handler.** Two
keystrokes and a button, and the graph stops painting. A flat line now gets room
to be a flat line.

### 12. A typed window accepted NaN — and the hole was the guard's SHAPE

The Window dialog validated `if xmin >= xmax or ymin >= ymax or xscl <= 0 or
yscl <= 0: raise`. Every one of those comparisons is False when either side is
NaN, so an all-NaN window satisfied all four and the next draw died with
`cannot convert float NaN to integer`. `float()` parses `"nan"` and `"inf"`
without complaint.

The interesting part is what fixed it. I lifted the rule into
`window_is_valid()` — so it could be checked at all, since inside the dialog it
sat behind a `dialog.run()` no test can drive — and wrote it as what a good
window *is*: `xmin < xmax and ymin < ymax and xscl > 0 and yscl > 0`. **That
alone closes the NaN hole**, because NaN fails a positive test just as it fails
a negative one, and failing the positive one means *refused*. Measured: with the
explicit `isfinite` line removed, every NaN case is still rejected and only
`inf` gets through.

So the defect was not a missing condition. It was a guard written as "reject
what is bad" instead of "accept what is good", and the two are not equivalent in
the presence of a value that compares False to everything. The `isfinite` line
stays for `inf`, which orders perfectly well and is still not a window.

### 13. STO→ could not store a negative number typed on the keypad

`_store_dialog` read the value as `float(self.expr)` behind an
`except ValueError: pass`. It worked when the display already held a bare
decimal; every other case did nothing **and said nothing** — the dialog closed,
no variable appeared, and whatever the variable held before was still there.

```
  3         stored 3.0
  1+2       stored NOTHING     any unevaluated expression
  sqrt(9)   stored NOTHING     anything with a function
  2*PI      stored NOTHING     anything with a constant
  −5        stored NOTHING     <- the keypad's OWN minus key
```

The last row is what makes it a defect rather than a limitation: the minus key
inserts U+2212 MINUS SIGN, not ASCII hyphen, and `float()` will not take it. The
calculator could not store a negative number typed on its own keypad.

The value goes through the app's own evaluator now — the same one `=` uses. And
as with the window, the read had to be **lifted out of the dialog** to be
checkable at all: it sat behind a `dialog.run()` no suite can drive, which is
exactly why a feature that silently failed on most of its inputs shipped.

### 14. Two dialogs used a different number format from the rest of the app

The Variables list built its text with `str(float)`. A stored 1 read `A = 1.0`
where the display says `1`, and storing `0.1+0.2` listed
`B = 0.30000000000000004` where the display shows `0.3` — the float noise
`%.12g` exists to hide, visible in exactly one place. The Window dialog had the
same fault in its entries (`-10.0`) and labelled its rows with the dict keys:
`xmin`, `xmax`, `yscl`. Both now use `format_number` with the Fix setting, and
the Window rows read `Xmin`/`Xmax`/`Yscl`, the labels a graphing calculator has
always used. Left untranslated on purpose, like the `sin`/`cos`/`log` key
faces: mathematical notation, not prose.

Worth noting how these were found at all. Every dialog in this app builds and
`run()`s inside one method, so **none of them had ever been reachable by a
test** — which is how a feature that silently failed on most of its inputs
(defect 13) shipped alongside them. Stubbing `Gtk.Dialog.run` to return CANCEL
builds and reads a real dialog with no modal loop, and that one technique
opened the last unexamined surface in the app.

### 15. The graph trace walked off the edge and never came back

`_on_graph_key` moved `trace_x` by a hundredth of the window per arrow press and
clamped nothing. Held down, the trace left the graph and kept going: **400
presses from x=0 in a [-10, 10] window put it at x=80** — pixel 4050 of a 900px
canvas — while the readout confidently reported `Y1 X=80 Y=0.984807753012` for a
point nobody could see.

And `trace_x` is persisted, so closing the app did not recover it. It reopened
just as lost, with no way to find out why the cursor was missing. Clamped on the
key, and clamped again in `sanitize_state` so a store already saved off the edge
comes back on screen instead of reopening in the same place. Verified: a store
holding `trace_x = 999` now loads at 10.0.

The coordinate transforms themselves are exact — `graph_to_pixel` and
`pixel_to_graph` round-trip to 1.78e-15 over 30 points — and are now pinned,
because tracing reads a value back out of a pixel and a drift there would put
the readout on the wrong point.

### 16. Two opaque keys had no tooltip

`STO→` and `MATH` — the only two keys that open a dialog, and so the two most in
need of saying what they do. Every other non-obvious key (`√`, `x!`, `1/x`, `π`,
`±`, `2nd`, `AC`…) had one. Both now reuse the wording of their own menu item,
already translated in all 17 catalogs, so naming them cost **no new strings**.

### 17. A typed Table Step of zero filled the table with one row

`_table_setting` took `float(entry.get_text())` and no more. Measured with the
table showing `Y1 = X`:

```
  Table Step = 0      all 40 rows read x = 0
  Table Step = nan    every x was nan
  Table Step = inf    every x was inf, and the first was nan
  Table Step = 1e400  parses to inf, same again
```

Zero is the interesting one — a plausible typo, accepted in silence, producing a
table that looks broken with nothing to say why. Guarded the same way and for
the same reason as the graph window, and written the same way round: as what a
good value *is*. `sanitize_state` screens a stored zero too, so an existing bad
store recovers rather than reopening broken.

That makes **three** places in this app where a number typed or stored by the
user reached a computation with only `float()` between: the graph window, the
Table Step, and STO→. All three are screened now.

## The mutation sweep (routine step 5b)

`tools/mutation_sweep.py` is campaign-owned and has no calculator entry, so this
ran from a scratchpad copy carrying a `REGIONS` range and a **watched**
`SENTINEL` — `values["ymin"] < values["ymax"]` → `<=`, confirmed beforehand to
turn *"a flat y range is refused"* red and nothing else. The sentinel came back
caught, so the harness was genuinely grading mutants.

```
  98 mutations in the swept regions (module-level maths, store paths,
                                     press/evaluate, graph handlers, zooms)
  51 caught  — but 2 of those were TIMEOUTS, not a check failing: swapping
               `action == "store"` routes more keys into a modal dialog and the
               harness hangs on dialog.run(). A hang is a catch of a sort; it is
               not a check detecting wrong behaviour, and counting it as one
               flatters the score. Honest raw figure: 49 of 98.
  47 survived
```

(My first count said 52. `grep -c "caught by"` also matched the sentinel's own
result line — an off-by-one in the instrument, not the sweep. 51 + 47 = 98.)

**Triaged by measurement, not by reading** — 636 behaviour probes fingerprinted
against the true module and against each survivor, the battery first proven
deterministic (0 of 636 differ between two runs of the same module):

```
  11 of the 47 CHANGE BEHAVIOUR   -> real coverage gaps
  36 are equivalent               -> nothing measurable moves
```

So the honest figure is **49 of 60 behaviour-changing mutants caught (82%)**,
not the raw 52%. Reading the survivor list and calling it equivalent would have
missed eleven real gaps — the same lesson bills taught, where 7 of 16 "obviously
equivalent" mutants turned out to change behaviour.

All eleven are now closed:

| what survived | closed by |
|---|---|
| the three zoom kinds (`in`, `out`, `standard`) | Zoom In halves / Out doubles / Standard resets |
| trace curve cycling | Up and Down walk the *enabled* curves only |
| the table's enabled-column filter | a column per enabled function and no others |
| `sanitize_state`'s variable filter (3 swaps) | only single capitals holding finite numbers are kept |
| the `fix` range | a stored fix of −1 / 10 / True / "2" / 1.5 loads as None |
| the window ordering guard | a reversed **and** a flat window are both reset |
| the `2nd` modifier guard | `sin` on its own does not give `asin(` |

**Confirmed by re-running the sweep afterwards**: 59% raw, and all eleven gaps
now caught — six of them by the re-run directly, and the other five verified
one at a time because the re-run had already swept past those lines before the
checks for them landed. That is the moving-tree problem again, this time inside
my own run, and it is why the re-run's raw number is not the interesting one.
Every behaviour-changing mutant in the swept domain is now caught; the 36 that
still survive are the measured-equivalent set.

Two are worth their own note. The window guard needed a **flat** fixture, not
just a reversed one: `>=` written as `>` still rejects 5..−5 and quietly accepts
5..5, so the reversed case alone could not tell the two guards apart — measured,
that mutation survived it. And a score is only meaningful against a stated tree
state: this one was taken at 388 checks, and six of the eleven were already
closed by checks added while the sweep was still running.

---

## A second sweep, over the regions the first one excluded

The first sweep skipped widget construction on the theory that a swap there only
means "the card looks different". That theory deserved testing once there were
layout, RTL and accessibility suites reading widget structure.

**The first attempt reported 100% and was worthless**, which is the more useful
finding. `mutation_sweep.caught()` scores a mutation caught on `returncode != 0`
— and an import error exits non-zero too. My variant MOD name
(`calculator-widgets`) made `caught()` write `calculator-widgets.py` while the
runner imports `calculator`, so the work directory had no `calculator.py`, every
suite died on import, and **every mutation was scored caught, sentinel
included**. The one check meant to prove the harness works is exactly the check
that cannot notice it is broken: it cannot tell a detection from a crash.

Which of my runs that voided was settled by *looking in the work directories*,
not by assuming: the domain runs used plain `MOD=calculator`, `mutwork/` holds a
real `calculator.py`, and their numbers stand.

Fixed (right filename, and `caught()` now marks a catch `[CRASH]` when the
output carries no FAIL line, so a run made of crashes looks wrong instead of
perfect), the honest result is:

```
  14 mutations   3 survived — 79% caught
                 of the 11 catches, 2 are [CRASH] rather than a failed check
```

Two of the three survivors were real: the `_refresh` and `_sync_dynamic_keys`
cache guards. Those caches exist so typing a digit does not force a keypad
relayout, and the failure they can produce is a display that quietly stops
following the state — the mode changes, the label still reads DEGREES. **I had
checked exactly that by hand earlier and written it up as correct**, which is
not the same as pinning it; the sweep found the difference. Both are now checked,
and the red proofs show the symptom directly: `('DEGREES', 'DEGREES')`, and
inverse faces that never flip.

The third survivor, `self._compact = sh < 860` → `<=`, is equivalent for any
real panel: the two differ only at a screen exactly 860px tall. The threshold
*value* is already pinned — at 722 with a wrong threshold the card stops fitting
and the layout check fails — so only the unreachable boundary is loose. Recorded
rather than papered over with a check at a height no machine has.

## A third sweep: arithmetic, the class that can reach a formula

The tool's nine swaps are all comparison and boolean. For a *calculator* that is
a thin test: none of them can touch a formula, so every transposed operator in
`graph_to_pixel`, `sample_segments`, `_sci` and the zoom maths was outside the
sweep's reach entirely. Added `+`/`-`, `*`/`/`, `//`, `**`, off-by-one on
literals and index `[0]`/`[1]`, behind a `SWEEP_ARITH=1` flag — opt-in because
arithmetic mutants are far likelier to be genuinely equivalent and would muddy
the comparison-swap score.

```
  72 mutations   16 survived — 78% caught
                 39 real FAIL catches, 18 [CRASH]  (arithmetic breaks things
                 outright far more often than a comparison swap does)
  triaged: 3 of the 16 change behaviour, 13 equivalent
           -> 56 of 59 behaviour-changing caught = 95%
```

The three, all now checked:

* **`dx = (xmax - xmin) / max(1, samples - 1)`** — nothing checked *where the
  samples land*, only that a curve drew at all, so the whole plot could shift or
  compress with every check green. Now: a curve is sampled from one end of the
  window to the other, at an even step.
* **`self.trace_curve + 1`** — the readout would say Y0 while tracing Y1. An
  off-by-one in a label is the kind of thing nobody writes a check for and
  everybody notices.
* **`"−(" + self.expr + ")"`** — survived because *nothing pressed ± on a
  non-empty expression*; the empty-display branch is the one every existing
  check happened to take. Worth its own note on proving it: the sweep's own
  mutation can only ever raise `TypeError`, so it crashes the suite rather than
  failing a check. The proof that actually discriminates is the plausible
  refactor — drop the brackets — which is silent and wrong: `−1+2` evaluates to
  **+1** where `−(1+2)` is **−3**.

## Rendered in all 17 languages

The routine asks for English plus one long-word language. This went wider once
compact mode landed, because a longer language is exactly what compact mode
could break. 17 languages x 3 views = 54 renders at 1024x722:

```
  54 renders, 0 clipped labels, 0 overflow
  card height by language
    507 px  de el en eo es fr it nl pl pt ru sr tr
    509 px  yi
    510 px  hi
    512 px  ja ko zh
  worst headroom of the 54: 135 px  (against 647 available)
```

CJK is the tallest and still has 135px to spare, so the compact metrics are not
tuned to English.

## Examined and found correct

Recorded because "we looked and it was fine" is a result, and the next person
should not have to re-derive it:

* **`tape_window` / `tape_rows`** — 840 adversarial combinations (desynced
  lists, negative offsets, offsets past the end, zero and negative counts).
  Zero problems: every window is a contiguous slice, never longer than its
  count, and no `None` result ever leaks into a painted row.
* **The `2nd` toggle** — `2nd`+`sin` gives `asin(`, a double press cancels, any
  non-inverse key resets it, the sin/cos/tan faces flip to `sin⁻¹` and back, and
  the button's `active` class follows the state.
* **`_sci`** — 39 cases up to 375 digits, graded against `decimal` at 80
  digits: mantissa always in [1, 10), never more than 12 significant figures,
  relative error under 5e-12. (My first grading of this reported seven failures
  and every one was the *check* being wrong: 21 nines rounds to 12 figures by
  carrying into 1e+21, so the exponent is supposed to move.)
* **`_copy_result`** — copies `3`, and declines to put `Cannot divide by zero`
  on the clipboard. Now pinned, because that is a deliberate decision and
  exactly the kind a later tidy-up removes.
* **The `_refresh` fast-path caches** — no staleness across deg/rad toggles,
  fix-mode changes or history updates, and the face/tooltip caches stay at 4
  entries as their design comment claims.
* **The evaluator** — 88 pathological expressions, 0 uncaught exceptions, no
  sandbox escape. 6000 random keypad presses, 0 uncaught, tape correctly capped.
* **Persistence** — all 12 stored fields round-trip through a real
  close-and-reopen in a fresh process.

---

## What was wrong with the tests

**`calculator_selftest.py` asserted persistence through a method the app never
calls.** Its two "angle mode survives a relaunch" checks called `_load_prefs()`.
A relaunch that genuinely lost the setting would not have failed either line.
Repointed at the real path (`C().deg` after a fresh construction), and
red-proved: with `sanitize_state` forced to always answer "degrees",
`radians is remembered` now fails.

**Four of the six suites hardcoded the module path, so every red proof against
them was vacuous.** `CALCULATOR_MODULE_DIR` was ignored, the mutated copy went
unread, and the pristine file was measured instead — a sabotage reported
all-green. Measured and confirmed: a `format_number -> "0"` mutation now turns
`calculator_ti_selftest` red, and all four importing suites load the mutant from
the scratch directory. `calculator_accessibility_selftest` reads the module as
TEXT and had the same hole, patched the same way.

This is the third day running that a check was found reading past its own
subject. It is the most reliable defect class I have.

---

## Suites

| suite | checks | new |
|---|---|---|
| `calculator_selftest` | **144** | repointed at the real persistence path; + Copy Result and the `_sci` sweep |
| `calculator_ti_selftest` | 75 | |
| `calculator_keyboard_selftest` | 31 | |
| `calculator_damage_selftest` | **35** | NEW — the guard the damage list asked for |
| `calculator_graph_selftest` | **61** | NEW — plotting matches typing; the compile cache holds parses, not answers |
| `calculator_variables_selftest` | **17** | NEW — STO→ stores what is on the display; the dialogs' number format |
| `calculator_rtl_selftest` | **12** | NEW — the pad is not mirrored, the faces are not mirrored, the chrome still is |
| `calculator_tape_selftest` | 20 | |
| `calculator_layout_selftest` | **13** | NEW — the number pad, the fit, and the table column |
| `calculator_accessibility_selftest` | **13** | upgraded from pure source-grep to source + real widgets | |
| **total** | **421** | from 258, 0 red |

29 red proofs, every one watched failing against a scratch copy (plus three measured EQUIVALENT and recorded as such rather than dressed up as coverage). **Three of my
four layout predictions were wrong** and the corrections are in the docstrings:

* `if i < STRIP_KEYS:` → `if False:` does NOT restore the old bug — the strip
  keys get negative rows and the pad keeps its offsets, so the block stays
  intact. The mutation that restores it is `divmod(i, PAD_COLS)` over all 38.
* Under that real mutation, "the number block is square" stays **green**: the
  old fold really did leave 7/4/1/0 in one column one row apart. It was the
  HORIZONTAL grouping that broke. A suite with only the column check would have
  called the scrambled pad correct.
* Tuning back only `.compact .key` does not break the fit — there is 140px of
  headroom now. It takes all three compact metrics.

---

## Two OS-wide gate blind spots, measured

**1. `minsize_sweep` cannot see a scrolled app overflow.** It measures the
WINDOW's preferred height. Calculator's home page is inside a `ScrolledWindow`,
which reports a small fixed minimum whatever it contains — so the number came
back **556 both before and after** the fix. It does not move because it cannot
move. The gate reported ALL FIT while "=" was below the fold. **Every scrolled
app in the OS passes that gate for free.**

**2. `reopen_damage_selftest` plants only a wrong-shape OBJECT.** Its `WRONG` is
`{k: MARK for k in (...)}`. I ran its own method with an ARRAY and a SCALAR
payload across all 28 apps in its list:

```
  array payload    0 LOST, 28 kept
  scalar payload   5 LOST: cookbook, terminal, calculator, g2048, gbasdk
```

All five lose it on cycle 2, through the `.bak` — the mechanism
`quarantine_unrecognized`'s own docstring predicts. calculator is fixed here;
**the other four are not mine** and are reported to the campaign.

Also noted, neither mine: `store_damage_selftest` fails on `sysmon` ("persists a
store but is NOT accounted for in COVERAGE"), and `i18n_check` fails on
`academics.py 'Move to Class…'` — both present at HEAD, both in files other
lanes are writing.

---

## New English strings — 1 new, 1 already pending

For the campaign's i18n merge. Catalogs untouched.

| string | note |
|---|---|
| `The saved calculator could not be read. A new one was started.` | new |
| `The damaged file was kept.` | **already pending from accounting (task 046)** — keyed identically on purpose so it costs one translation across both apps |

`Close` (the notice's dismiss tooltip) is already in all 17 catalogs.

---

## Handed up, not taken

**There is no way to get numbers back out of a `.damaged-<stamp>` file** — in
this app or any other. Recovery today means opening it in a text editor. Every
app in the OS now carefully preserves damaged stores that nothing can read. That
is a product decision about what quarantine is *for*, not a defect in
calculator, so it is written here rather than quietly solved in one app.

## State at hand-off

All green at 2026-08-08 18:15:

```
  10 calculator suites          421 checks, 0 red      (from 5 / 258)
  minsize_sweep                 ALL FIT
  reopen_damage (calculator)    ALL PASS
  save_failure                  ALL PASS
  construct_all_host            38 ok, 0 crashed
  css_parse / ascii_css         clean
  py_compile                    clean
  17 languages x 3 views        54 renders, 0 clipped, 0 overflow
  mutation sweep, domain        every behaviour-changing mutant caught
                                (36 measured-equivalent survive)
  mutation sweep, widgets       13 of 14; the 1 survivor measured equivalent
  mutation sweep, arithmetic    56 of 59 behaviour-changing caught (95%)
```

**17 defects**, and the ones worth naming to whoever picks this up: a store that
parses but is not an object was destroyed on the second open+close with no user
action; an unreadable store silently disabled saving for the whole session; the
keypad was mirrored in Yiddish and the bracket keys had swapped faces, so the
key that looked like `)` typed `(`; and the digits were not in a number pad at
all.

Only `de/calculator.py` and `tools/calculator_*_selftest.py` were touched. No
catalog, no shared module, no build file.

**Before an ISO build**, note the overlay rebuild trap: editing a file under
`rootfs-overlay/` does NOT invalidate the image, so the images must be removed
first and `output/target` grepped afterwards to confirm the new bytes actually
landed. The build train is campaign-owned; this lane ran none of it.

Left uncommitted for the campaign's integration pass.
