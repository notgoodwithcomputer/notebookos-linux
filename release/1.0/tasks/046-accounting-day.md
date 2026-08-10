# 046 — Accounting, day two of the per-app pass

**Lane:** app-improve (24h/app loop, alphabetical) · **Streams:** S1 truth ·
S4 ALIVE · S7 flow
**Status:** OPEN — accounting.py claimed 2026-08-08 00:01

Baseline before any edit: 4 suites green (`accounting_selftest`,
`accounting_dates` 17, `accounting_lifecycle` 11, `accounting_transition`).
Layout fits with room to spare — `minsize_sweep` at the 722 budget gives
`accounting[de] 965 x 389`, and `data_stress_sweep` reports no stored field
pushing it off the panel.

## Verified sound — recorded so the next pass does not re-litigate it

**The money adds up, under everything I could throw at it.** A ledger has one
invariant that matters — the last running balance, the headline BALANCE and
`opening + sum(amounts)` must be the same number — and it held for: seven
sub-cent credits (the case the `_cents` docstring was written for), a thousand
one-cent entries, `0.1 + 0.2`, ±1e6 cancelling to zero, ±1,234,567.89, a
thousand-entry ledger, a sub-cent OPENING balance, and a single
9,999,999,999.99 entry. The quantise-at-the-door design does exactly what it
claims. Degenerate stored values are rejected rather than propagated: NaN and
infinity are dropped, a boolean amount is dropped, a numeric STRING is accepted
and parsed, and negative zero renders "$0.00" and not "−$0.00".

**Deliberate designs I checked and left alone**, each carrying a written
rationale in the source: `_PAGE = 150` bounds a rebuild so adding an entry to a
five-year ledger costs what it costs on an empty one; `_GRID`'s column pitch
gives BALANCE the most room because it carries the largest figure on the row,
with description truncation mitigated by a tooltip; and `_show_more` rebuilds
rather than appends specifically to keep the scroll position, which was measured
both ways.

**Amount parsing is more forgiving than it looks, correctly.** `1,234.56`,
`$50`, whitespace, and Arabic-Indic digits (`١٢٣` → 123.0) all parse; `abc`,
empty, `0`, `-0` and `1.2.3` are refused. The sign is stripped because direction
comes from the form's own debit/credit control, not from the typed text.

## What landed

1. **The Find box could not find money that went out.** `_matches` indexed only
   `abs(amt)`, so "212.40" found an entry and **"-212.40" found nothing** —
   while the documented purpose of searching the figure is to find "one figure
   off a paper statement", and a statement writes a debit with its minus about
   as often as not. Worse, the ledger DISPLAYS a typographic minus (U+2212), so
   a figure copied out of this app's own column could never match itself. Both
   forms are now indexed and the term's minus is normalised: "3.50" still finds
   money in and out, "-3.50" and "−3.50" find only money out, and a credit of
   the same magnitude is correctly NOT dragged in by a negative query.

2. **Editing an entry's date left the CSV exporting a different day.** The
   ledger keeps two dates per entry: `date`, the short string the column can
   fit ("6 Aug"), and `iso`, the machine-readable one the CSV leads with so a
   spreadsheet can sort it. `_save_edit` carried `iso` over unconditionally —
   with a comment correctly explaining that editing a DESCRIPTION must not cost
   the row its only sortable date. But the editor exposes the DATE too, and
   when that changed the stale iso came along: an entry retyped from "03 Aug"
   to "01 Jan" exported as **`['2026-08-03', '01 Jan', ...]`**, the two date
   columns naming different days in the one file whose whole purpose is to be
   sortable.

   `_edited_iso` now moves the iso WITH the date. Dropping it was the first fix
   I tried, and the existing suite was right to fail me for it — an edit that
   costs a row its sortable date is its own small data loss. Keeping the year
   and taking the new day and month invents nothing, since the entry already
   carried that year; a row that never recorded a year still gets none, and a
   year the user types is believed.

   **The existing check asserted PRESENCE, not correctness.** "editing does not
   drop the ISO date" passed throughout — the field was there, and wrong. The
   suite's own fixture had been exporting `['2026-08-08', '6 Aug', ...]` all
   along, green. Another instance of the house blind-spot: verifying a field
   exists rather than that it says the right thing.

3. **A deleted ledger entry can be taken back.** There was no undo of any kind
   — no history, no Edit-menu entries, no Ctrl+Z — and the confirm card's
   "This cannot be undone" was telling the truth: a Delete key on a focused row
   destroyed a real financial record permanently, in the one app in this OS
   whose whole subject is being right about money. Undo now spans the whole
   book (every entry plus the opening balance), so one mechanism reverses an
   add, an edit and a delete alike; it is wired to the Edit menu, to Ctrl+Z /
   Ctrl+Shift+Z at the window level, and each step is NAMED ("Undo Delete
   Entry").

   The confirm card is deliberately LEFT IN PLACE for now. The campaign's
   recorded decision is that undo replaces confirmation, but removing the
   confirm in one app makes it inconsistent with the other thirty until they
   catch up — same reasoning as academics yesterday. Accounting is now ready
   for that coordinated pass rather than ahead of it.

4. **A damaged ledger lost its opening balance, and every figure with it.**
   `_salvage_tx` walks a broken file and recovers every intact transaction —
   deliberately keeping only objects carrying `amt`, which means the outer
   wrapper is skipped. `opening` lives in that wrapper, and the wrapper is
   exactly the part a truncated file never closes. So a recovered ledger came
   back with an opening balance of **zero** and every balance on screen was out
   by that amount, silently, while the status line read "Recovered N entries
   from a damaged ledger file" and looked like a complete account of the loss.
   Measured: a store with `"opening": 250.5` recovered as 0.0.

   `_salvage_opening` now scans the raw text with the same string-awareness,
   and accepts the key ONLY at outer brace depth — recovering a WRONG opening
   balance would be worse than recovering none.

   Worth noting how small the real-world blast radius is, and why I fixed it
   anyway: the app has no UI for setting an opening balance, so it is non-zero
   only in an imported or hand-edited file. But this is a ledger, the failure is
   silent, and it moves every number on the screen.

5. **The exported PDF truncated with the wrong ellipsis, on a comment that
   stated a false fact.** `fit()` appended ASCII `"..."` and explained itself:
   *"it is the ellipsis every other export in this OS uses"*. Checked, and it is
   not — academics, journal, installer and gbahelp all append U+2026, and so
   does the delete-confirm card in accounting.py itself, forty lines of scroll
   away. The comment's other justification (cairo's toy font API does no
   per-glyph fallback, so an exotic character risks a tofu box) was real once,
   and the same comment already admitted it had stopped applying when the
   renderer moved to PangoCairo. So the only live reason was the one that was
   untrue. Now `…`, matching the rest of the OS and the rest of this file.

   The check proves the glyph RESOLVED, not merely that it extracted.
   `pdftotext` reads the text layer whether or not a face was found, so a tofu
   box and a real ellipsis extract identically — the exact trap
   `pango_render_selftest` exists for. Unknown glyphs are counted at the Pango
   layer as well, and the count must be zero.

## Verified, NOT a defect — the Reports card

Rendered the Ledger Summary overlay and it came out half off the right edge
with every figure clipped (`$2,4…`, `+$1,1…`, `−$1,9…`). It is not a bug. The
render harness reparents the window's child into a `Gtk.OffscreenWindow`, so
`self.get_allocation()` returns nothing and `_overlay_size` falls back to
`nbapp.screen_size()` — the host's 1920 — while the picture being rendered is
1024 wide. The card was centred at x=810, which is exactly (1920−300)/2. With
`_overlay_size` pinned to the size actually being rendered, the card centres
correctly and every figure is readable, and its arithmetic reconciles
(2400 + 1105 − 1943.80 = 1561.20).

Recorded here and in APP-LOOP.md's routine because every remaining app with a
modal overlay will reproduce it, and it looks like a serious layout defect.

## The session suite, and the value that hides an addend

Added `tools/accounting_session_selftest.py` (38 checks): one ledger driven end
to end — open empty, load an opening balance, build it up, search, edit, delete,
undo, export CSV and PDF, close, reopen — asserting after EVERY step that the
three routes to the balance agree to the cent (`_refresh`'s headline, a plain
summation, and `_balance_series`' last point, which are three different code
paths, so agreement is evidence rather than tautology).

Its first version ran the whole session at `opening == 0.0` and was therefore
blind to every mutation involving the opening balance: breaking `_refresh` to
drop it, and breaking `_balance_series` to start from zero, BOTH left all 24
checks green. Zero is the value that hides an addend. Seeded at 2400.00 the same
two mutations fail every step in the session.

That is the fourth check of mine in two days that passed for the wrong reason,
and the fourth caught by running the mutation rather than by reading it. The
pattern is consistent enough to name: **a fixture built from default or empty
values cannot see arithmetic that operates on them.**

6. **The app was left denying its own undo — a defect I introduced.** After
   adding the undo history (item 3), the delete-confirm card still read
   *"Delete “X”? This cannot be undone."* That sentence was true when it was
   written and false the moment undo landed, and it is false in the
   FRIGHTENING direction: it tells somebody a reversible action is permanent,
   so they keep a row they meant to remove. Three code comments and the module
   docstring said the same thing. All four corrected, and the sentence dropped
   rather than replaced with "you can undo this" — the confirm itself is on its
   way out under the campaign's undo-replaces-confirmation decision, and this is
   the smallest change that makes the card honest today.

   Found by RENDERING the card, not by reading the diff. The rule worth keeping:
   whoever adds undo to an app owns finding the copy that says there is none.

   **String change for the merge:** `"Delete “%s”? This cannot be undone."` →
   `"Delete “%s”?"` (old key retires).

## The chart had no coverage at all

`tools/accounting_chart_selftest.py` (26 checks). The chart is the only claim
this app makes about money that is not a number you can read back, and nothing
tested it. It now covers `_balance_series` point by point (not just its ends —
an accumulator that resets still lands on the right total surprisingly often),
cent-exactness at a thousand entries and at ±1e6, and both renderers surviving
every degenerate ledger that reaches for a zero divisor.

Two things learned building it, both from mutations:

* **There are TWO renderers.** `_render_chart` draws the exported PDF;
  `_paint_chart` draws what is on screen all day. My first version covered only
  the PDF — i.e. the half nobody looks at.
* **"It put ink down" is too coarse a question.** The chart fills an area,
  strokes a baseline and draws a border before it reaches the balance line, so a
  check for "some pixel differs from the background" stayed GREEN with the line
  deleted entirely. The line is now counted specifically, as pixels dark enough
  to be INK rather than the 6%-alpha fill.

Built by me after cancelling the Codex dispatch for it: that job sat in
"verifying" for 21 minutes waiting on its own delegated worker without producing
a file. Running Codex yield is now **3 clean of 6**, with one partial, one
self-deleted deliverable and one stall.

7. **A view the user turned off came back at the next launch.** View ▸ Hide
   Balance Chart only ever touched the live widget, so the choice lasted until
   the window closed. This is the shape the campaign already has a name for —
   applied to the running process, never written down. The preference is now
   stored in the ledger file and applied at startup (`set_no_show_all` plus an
   explicit `set_visible`, since `show_all()` would otherwise reveal it
   regardless), and a store written before the key existed still opens with the
   chart shown, which is what every current user already has.

   Tracked as a FLAG rather than read off the widget at save time: an
   unrealised window reports every child invisible, so asking the widget would
   persist "hidden" for any session that closed early. The probe that first
   found this bug was wrong for precisely that reason — it asked an unrealised
   widget, got False, and called it "persisted". Third harness artefact of the
   two days, and the second where the instrument said the opposite of the truth.

8. **Saving destroyed anything the file carried that this version did not
   know.** `_autosave` rebuilt the document from `{"tx", "opening"}`, so a
   top-level key written by a newer build — or by hand — was deleted by the act
   of saving. Identical to the academics defect fixed on day one. Unknown keys
   are now carried through, and the check asserts both directions: they survive,
   AND nothing unexplained is invented.

9. **The opening balance became settable.** `opening` has been in the saved
   schema from the beginning: the loader reads it, the balance adds it, the
   Ledger Summary prints it as a line of its own, the running-balance column
   starts from it, and today's salvage fix goes to real trouble to recover it —
   and **there was no way for anybody to set it.** It could only ever be
   non-zero in a hand-edited or imported file. Exactly the shape as a class's
   `room` and an assignment's `note` in academics: a field the model can express
   and the interface cannot reach, which reads to the user as the app not having
   the feature at all.

   It matters most on the first day, which is the day this app is least
   forgiving: a ledger you start today does not start from nothing, it starts
   from whatever is already in the account, and without this every balance was
   wrong by that amount until you invented a fake first entry to correct it.

   Edit ▸ Opening Balance…, on the same overlay-card pattern as the row editor.
   It carries a direction (an account can be overdrawn, and `_parse_amount`
   strips the sign deliberately), refuses rubbish rather than taking it as zero,
   treats empty as the way to clear it, is undoable like any other edit, and
   persists.

   **New English strings:** `"Opening Balance"`, `"Opening Balance…"`,
   `"What the account held before the first entry."`, `"In credit"`,
   `"Overdrawn"`, `"Opening balance set"`.

## Keyboard operability — audited, and one more near-miss

Drove the whole app without a mouse. It holds up: Enter commits the entry form
from either field, Ctrl+Z / Ctrl+Shift+Z work at the window level, the Delete
key on a focused row really does remove it (the row tooltip promises this, so I
drove it rather than assuming), every ledger row is focusable, and Esc unwinds a
chain — row editor, then report, then the entry form, then the search.

**The Esc chain read as a bug and was not one.** My probe reported "Esc does not
clear the search"; the entry form was open from an earlier step, and Esc had
correctly closed the form instead. That is the fourth measurement error of these
two days and the second where the instrument said the opposite of the truth. It
is now pinned by three checks, including the ordering, because the order IS the
design and is easy to break by adding a new overlay anywhere but the top of the
chain.

## I BROKE THE CHART, AND MY OWN CHECK PASSED

Worth writing up in full, because it is the sharpest lesson of the two days.

Making the chart preference survive `show_all()` (item 7), I reached for
`chartwrap.set_no_show_all(True)` and then `set_visible(...)`. That looks right
and is wrong: `no_show_all` on a container stops `show_all()` recursing into its
CHILDREN as well, so the wrapper could be made visible while the DrawingArea
inside it never was. Allocation 1x1. Nothing drawn. **The entire "Balance over
time" block vanished from the app.**

`accounting_prefs_selftest` — which I had just written specifically for this
feature, and red-proofed three ways — stayed GREEN throughout. It asked
`chartwrap.get_visible()`, and the wrapper was perfectly visible. It was
checking the box around the thing instead of the thing.

I did not find it by reading the diff, or by running the suite, or by any of the
gates. I found it while probing something else entirely (whether the chart's
cached image could go stale) and noticing the allocation come back as 1x1 in a
debug line I had added for a different reason.

Fixed by hanging the preference off the WRAPPER'S OWN "map" signal — the wrapper
is mapped whichever route shows the tree, including a render harness that
reparents the content and never maps the window, where a window-level handler
silently never runs. The check now asserts the chart's own visibility AND a real
allocation, and fails three ways against the exact broken version.

Two rules out of it:
* **Assert the thing, not the container.** "Is the wrapper visible" is a proxy;
  "is the chart on screen with a real allocation" is the question.
* **After any visibility or packing change, RENDER THE APP.** Nothing else
  caught this — not the suite written for the feature, not the OS-wide gates.

(The cache probe that led me here came back clean, incidentally: the chart
redraws after an add, a delete, an undo and an opening-balance change, and the
post-undo image is byte-identical to the pre-delete one.)

## Verified true: the chart cache follows the device scale

The chart is rendered once into an ImageSurface and blitted, because every
expose repaints in software on this hardware. Its source comment claims the
cache is keyed on the device scale as well as the allocation, so a window
dragged to a HiDPI monitor re-renders rather than blitting a soft 1x surface.

After two comments today turned out to state false facts, I measured it instead
of believing it. It is true: at scale 1 the cached surface is 616x156 for a
616x156 allocation; forcing scale 2 rebuilds it at 1232x312; going back to 1
rebuilds again. Now pinned by three checks in the chart suite, red-proofed by
removing `sf` from the cache-key comparison.

## Coverage audit: 35 methods no suite had ever named

Listed every method in accounting.py and grepped the whole suite set for each.
35 of 79 were never mentioned. Most are drawing helpers a render exercises
anyway, but three were user-facing features that ADD UP MONEY with nothing
checking them:

* **Reports ▸ Ledger Summary**, including the filtered variant, which reports a
  NET rather than a balance because a subset of a ledger has no balance.
* **The find summary** beside the search box ("N matches · net").
* **Paging.** If `_show_more` is wrong, every entry past the first 150 is simply
  unreachable — on a five-year ledger, most of it.

`tools/accounting_report_selftest.py` (23 checks) covers all three, with every
figure checked against an independent summation rather than against another of
the app's own numbers. All three red proofs land: summing the whole ledger
instead of the matches, labelling a filtered figure BALANCE instead of NET, and
paging that never advances (which loses 250 of 400 entries and is caught three
ways).

A fourth uncovered path came out of the same audit and is now pinned in the
session suite: **the row editor's debit/credit control changes the SIGN of a
real financial record**, and nothing had ever exercised it. Getting it wrong
turns money out into money in — an error of twice the amount, in the direction
that flatters the balance. Verified correct (−950 becomes +950, the balance
moves by exactly 1900, the editor opens on the entry's own direction, undo puts
it back) and red-proofed by hard-coding the sign, which fails three ways.

Nothing was broken — this is coverage, not repair. Worth doing anyway: the
untested-module audit is the method that beat the ROADMAP once already in this
project, and "no suite has ever named this" is a cheap thing to ask.

## Also verified sound

* **HiDPI.** Rendered the whole app under `GDK_SCALE=2`: a true 2048x1444
  surface, text sharp at 1:1, and the chart line crisp rather than upscaled —
  the scale-aware cache doing what its comment claims. This is a documented past
  problem area for the OS, so it was worth a look rather than an assumption.
* **Printing.** Delegates to the shared `nbprint.print_document` using the SAME
  `_render_pdf` the export uses, so the printed and exported documents cannot
  drift apart; a failure to open the dialog is caught and surfaced as "Print
  failed" rather than crashing. The dialog, the printer picker and the
  no-printer case are nbprint's, which is campaign-owned.
* **Debit/credit direction on an edit** — see the coverage audit above.

## Fuzzing the store — and the seventh check that measured a difference

`tools/accounting_fuzz_selftest.py`: 33 malformed ledger files — empty, a bare
number, `tx` as a string, amounts as dicts, a 100,000-character description,
2,000 entries, truncation at both ends, leading and trailing garbage, NUL bytes,
a BOM, CRLF throughout, Arabic and CJK and emoji — each opened, closed, and
OPENED AGAIN. **0 crashes, 0 losses.** The damage path is genuinely solid: the
quarantine, the salvage and the empty-model guard all do what they claim.

The interesting part is the hole the red proof found in the suite itself. Its
only assertion was "the second open must not hold fewer entries than the first"
— a DIFFERENCE. Gutting `_salvage_tx` so it recovers nothing makes both opens
return zero, the difference is still zero, and the suite stayed green against a
mutation that destroys the entire recovery path. My own docstring had argued
against pinning counts, on the grounds that it would make the file a
change-detector; that reasoning was right for most cases and wrong for the ones
where the data is intact and only the wrapper is damaged, where recovering
SOMETHING is the whole point.

A floor of one entry for those six cases makes the mutation fail five ways.

**A check that measures a difference is blind to anything that moves both
sides.** Seventh check of mine in two days to pass for the wrong reason, and the
seventh caught by running the mutation rather than by reading it.

## Column alignment — the fifth measurement error

`_sync_head_gutter` matches the header's right gutter to the rows' scrollbar so
the columns line up whether or not one is showing. I probed it by comparing
allocations and got "311px MISALIGNED" in both the scrollbar and no-scrollbar
cases. Cropping the actual render settles it: the BALANCE header's right edge
sits exactly on the balance figures, DEBIT on the debits, and the header rule
stops just before the scrollbar. The gutter tracks correctly too (1px with no
scrollbar, 17px with one).

The probe was picking the wrong label out of the row — the app is right. Fifth
measurement error of the two days, and the fifth caught by looking at the
picture instead of trusting the number. Recorded because "measure it" is the
method of this whole pass, and the method has a failure mode: a measurement of
the wrong thing is more confident than no measurement at all.

## Considered and declined

**The four validation messages are correct; one is merely less helpful.** With
BOTH fields wrong and the amount a sub-cent value, `_missing_msg` says "Enter a
description and an amount" and drops the "of at least $0.01" hint that the same
input gets when the description is present — so the reader learns about the
minimum on the second attempt rather than the first. Not false (a sub-cent value
is not an amount this ledger accepts) and reachable only when two things are
wrong at once. Fixing it means a fifth string and a 17-catalog cost for that
combination. Recorded rather than churned; if the campaign is merging accounting
strings anyway, `"Enter a description, and an amount of at least $0.01"` is the
wording.

## Gates

New: `tools/accounting_prefs_selftest.py` (24 checks),
`tools/accounting_chart_selftest.py` (26 checks),
`tools/accounting_session_selftest.py` (38 checks),
`tools/accounting_undo_selftest.py` (24 checks, including three that
read the rendered confirm card back and refuse any claim that a reversible
deletion is permanent) and
`tools/accounting_find_selftest.py` (14 checks). Extended:
`tools/accounting_selftest.py` +9 (opening-balance salvage with its two decoys, and the PDF ellipsis with a glyph-resolution proof) — the text half, the
figure half, combined queries, and malformed input. Extended:
`tools/accounting_dates_selftest.py` 17 → 25, including an end-to-end check
that reads the exported CSV back and asserts its two date columns name the same
day (a helper agreeing with itself proves nothing if the app never calls it).

Its red proof is the cleanest kind: the suite was written BEFORE the fix and run
against the **shipped** app, so no mutation was needed — the broken behaviour
was the one in the tree:

    FAIL a debit is found by its signed figure         <- '-3.50' matched 0 of 1
    FAIL the app's own typographic minus finds it too  <- '−3.50' matched 0 of 1
    14 checks, 2 failed

Note the third figure check ("a credit is NOT found by a negative figure")
passed in that run **vacuously** — nothing matched a negative query at all, so
"it didn't wrongly match" was free. It only becomes evidence once the other two
pass, which is the argument for keeping all three.

## On the Codex pass — a hazard in the recipe

The CSV dispatch (`tools/accounting_csv_selftest.py`) landed, passes, and
carries **three real measured red proofs** in its docstring — quoting disabled,
a `$` prefixed onto the exported amounts, and the cumulative balance replaced by
a per-row one, each with the actual failure text. That is the first Codex
deliverable to satisfy the proof requirement unprompted-by-a-retry, and the
difference from the one that self-destructed is the explicit instruction added
to this dispatch: *never delete your own deliverable; a partial file with an
honest note beats nothing*. But the
red-proof mandate makes a Codex task MUTATE AND RESTORE the app module, and
while it was doing that I edited `accounting.py` myself. Codex restored the file
from the pristine copy it had taken **before** my edit and my change vanished —
silently, and looking exactly like "my fix didn't work". `git diff --stat` came
back empty on a file I had just edited, and `inspect.getsource` on the live
module showed the old body.

Filed to the campaign session with three options; the one worth taking is to run
red proofs against a COPY of the module inside the scratch directory so nothing
in `buildroot/` is ever mutated. Until then: **a module targeted by an in-flight
Codex red-proof dispatch is read-only to everyone, including the dispatcher.**

Running yield across five build dispatches: THREE fully clean (academics export,
accounting CSV, plus the earlier one), one partial, one that wrote its
deliverable and then DELETED it after failing to complete the red proofs. Every
dispatch now carries "never delete your own deliverable — a partial file with an
honest note beats nothing", and that is the dispatch that produced the first
unprompted three-proof deliverable.

---

## Day 2, second half (2026-08-08) — the export and the columns

A method-coverage audit of `accounting.py` (89 defs against all 12 suites at the
time) found **33 methods no suite had ever named**. The largest coherent block
was the PDF statement export — `_render_pdf` plus `text_at`, `right_at`,
`desc_room`, `table_header`, `_pdf_name` — a multi-page document renderer for
the one artefact this app produces that LEAVES THE MACHINE, and nothing checked
where its ink landed. `_export_csv` was already covered by two suites; the PDF
was covered by none.

### The method: instrument, then measure geometry

`_show_text` was wrapped to record every draw with its real Pango extents, and
the drawn boxes checked for the two things reading cannot check — whether any
two on one baseline OVERLAP, and whether any lands outside the printable area.
Page boundaries come from the draw stream (within a page the baseline only moves
down); `cairo.PDFSurface` is a C type and cannot be subclassed to count
`show_page`, which is what the first attempt tried.

### Defect 9 — the PDF date column was unbounded, and reachable by typing

The date column is 58pt. The string in it was bounded by nothing: `date`
round-trips from the file verbatim, and the row editor accepts a retyped date
INCLUDING A YEAR on purpose (`_short_date_parts` documents that). The SCREEN
ellipsizes this column — it was hardened after a 690-character date took the
window's minimum width to 5309px — but the PDF drew `str(t["date"])` raw. The
identical defect, already found and fixed once, still live in the export.

Measured, through the plain interface (type it into the row editor, export):

| typed date | width at 9.5pt | result |
|---|---|---|
| `01 Aug` | 29pt | fits |
| `26 Sep 2026` | 52pt | fits |
| `26 September 2026` | **81pt** | ends x=135 against a description starting at x=112 |
| `一月二十三日` | **60pt** | ends x=114 |
| a 690-character date | **3320pt** | on a 612pt page |

Fixed with the renderer's own `fit()` helper and a 6pt gutter (`DATE_W`), chosen
so the common `26 Sep 2026` survives intact rather than being truncated by the
bound.

### Defect 10 — every money heading sat 3px off its column, but only when scrolling

`_sync_head_gutter` exists precisely to stop the header and the rows drifting
apart when a scrollbar appears, and had never been named by a suite. It reserved
`vsb.get_allocated_width()`. Measured at 1024x722 with 200 rows: the scrollbar
allocates **17px** but the viewport gives up **20px** — the extra 3px is CSS
spacing that a widget's allocation does not report. So DEBIT, CREDIT and BALANCE
each sat 3px right of their figures whenever the ledger was long enough to
scroll, and 1px off when it was not. Fixed by measuring the space the rows
actually LOSE (`scrolled.get_allocated_width() - child.get_allocated_width()`).
Both states now measure exact.

### Defect 11 — `add_entry` produced entries with no ISO date

`_on_add` (the form) stamps an `iso`; `add_entry`, documented as the "public
entry point for programmatic use", appended `{"date", "desc", "amt"}` and no
`iso` at all. Such an entry reaches a spreadsheet with an EMPTY Date column —
the column the CSV export's own docstring calls the thing that makes the sheet
"sortable and reconcilable" — and gives `_edited_iso` no year to carry when the
date is later retyped. Added `_iso_for()` and stamped it.

### Two probe errors caught before they became claims

* The suite's own "the CJK date is drawn" check failed at first. `fit()` had
  correctly truncated to `一月二十三…`, so the drawn text no longer STARTED with
  the full string. The check was wrong, not the app: what is drawn must be a
  PREFIX of the real date — truncation allowed, corruption not.
* A first alignment probe reported a 117px drift. It identified money cells by
  "contains a digit", which matched a description reading `Entry 199`, and rows
  with no debit have one money cell fewer, so it paired DEBIT against the
  description. Cells are now found by STYLE CLASS. This is the same false-drift
  shape as the earlier "311px MISALIGNED" reading.

### The red proof that found a hole in its own suite

`accounting_pdf_selftest` passed 32/32 with the running-balance accumulator
gutted (`bal = round(bal + t["amt"], 2)` -> `bal = round(t["amt"], 2)`), every
row of the BALANCE column printing `-$1.00`. The only balance being checked was
the FOOTER's — computed by a SEPARATE line (`total = round(self.opening +
sum(...))`) that the mutation never touched. Two independent computations, one
of them guarded. The per-row check exists because of that proof, not before it.

**Zero is not the only value that hides an addend; a second route to the same
number hides a broken first route.**

### Deliberately NOT changed, having been measured

* The export draws the WHOLE ledger while a find query is active. A running
  balance and a closing balance are meaningless on a filtered subset — the
  report card already handles a query by reporting a NET instead, which is right
  for a summary and wrong for a ledger.
* The export draws the chart even when View ▸ Hide Balance Chart is off
  (confirmed by spying on `_render_chart`, not by counting text draws — the
  chart is pure vector and draws no text, so the first measurement was vacuous).
  The View menu is scoped to the window, not to a document written to Documents.
* **The ledger is not held in date order.** A back-dated entry is appended, so
  the display (reverse-insertion) shows it at the TOP above later dates, with
  the final running balance printed beside an old date. Measured and REPORTED
  rather than fixed: the running balance is correct as a total of the recorded
  sequence, the app is self-consistent, and silently re-sorting a money app's
  stored ledger is a product decision, not a bug fix. See HANDOFF.md.

### Codex, and a contract collision

Dispatched on the entry form / edit / delete path, with an explicit
mutate-a-COPY rule after the earlier clobber. It found and fixed three real
defects: a year-boundary race where the visible date and the persisted ISO came
from SEPARATE `strftime` calls (an entry committed at midnight on 31 Dec could
be stored `31 Dec` / `2027-01-01`), an edit path that reconstructed entries from
three fields and so DROPPED unknown metadata, and a missing shape guard in
`_parse_amount`. Its suite also pins the OS-wide "Esc never deletes" rule on the
delete confirmation.

Its `_parse_amount` guard went too far and **broke four pinned contracts**
(`0.005`->0.01, `1.004`->1.0, `12.345`->12.35, `1e3`->1000.0) while leaving its
own docstring describing the old permissive behaviour. Worse, the refusal path's
only voice is `_missing_msg`, whose docstring records that answering "Enter an
amount" to somebody who plainly typed one reads as a bug — which is exactly what
rejecting `12.345` now did. Resolved by keeping the guard's real value (SHAPE
validation: mis-grouped thousands, and non-ASCII digits, since `float("٣")` is
3.0) and forgiving PRECISION, then moving the two cases across in Codex's suite.

Its cached `_form_iso`/`_form_date` pair, which fixes the year-boundary race,
falls back to `""` and is only populated once the form has been toggled open —
so every non-form route to a committed entry produced an entry with no ISO, and
`accounting_dates_selftest` went red on five checks. Fixed by falling back to
`self._iso_for(shown)` rather than a blank.

**Suites 12 -> 15. Checks 185 -> 244.** All green, plus py_compile,
construct_one, css_parse_check, ascii_css_check, toyfont_check,
i18n_placeholder_check, dead_setting_check, voice_check, and minsize_sweep
(accounting[de] 964x389, ALL FIT).

### Defect 12 — a true sentence doing the worst thing a true sentence can do

When the ledger cannot be read at all, the user was told:

> The ledger file could not be read. A new ledger was started.

True, and it reads as "your figures are gone". Measured: the original bytes
ALWAYS survive as `accounting.json.damaged-<stamp>` — on the open-and-close path
with no edit at all, as well as after an edit. The app was quietly doing the
right thing and letting the user believe the opposite.

Not fixed by rewording. That sentence appears at three call sites and all
seventeen catalogs carry it; rewording is seventeen stale translations for one
added fact. `_unreadable_note()` now returns two COMPLETE sentences joined by a
space, the first being the already-translated one — the same reasoning
`_recovered_note` uses to pick between whole sentences rather than suffix a
plural. Merge cost: one short new string, `"The damaged file was kept."`

**The axis this exposes:** "does the app TELL the user what it did" is orthogonal
to "does the app DEFEND the data", and the whole store-damage sweep only ever
asked the second. Accounting defends perfectly and still told the user their
ledger was gone. Filed to the campaign; it wants its own gate.

### Defect 13 — the summary panel did not add up

The sidebar showed CREDIT +$1,105.00 and DEBIT -$2,280.74 against a BALANCE of
$1,224.26. Those three disagree by exactly the term that was not on screen. The
ledger has always stored `opening`, the report card has always printed it, and
the sidebar — the figures somebody actually looks at — left it out. A money app
whose own summary does not reconcile.

Added an OPENING row, shown only when non-zero (at zero the other three
reconcile by themselves and the row is noise; a sub-cent opening snaps to zero
via `_cents` and stays hidden). The row is hidden, not just the figure — a
caption left behind with no value is worse than no row, and `set_no_show_all`
guards against a later `show_all()` putting it back, which is the exact trap
that once hid the balance chart's DrawingArea while its wrapper still reported
itself visible. It also fills the dead space the FIND box was originally added
to address, and makes the settable opening balance discoverable.

Pinned in `accounting_prefs_selftest` (4 openings x 3 checks), computing the
reconciliation from **the labels the user reads**, not from the model behind
them — otherwise it would only be checking that arithmetic still works. Two red
proofs recorded.

### A seventh measurement that contradicted an inference

`CREDIT`, `DEBIT` and `ENTRIES` are passed to `_stat` as RAW strings while
`BALANCE` and `FIND` go through `_t()`, and a literal catalog lookup for the key
`"CREDIT"` in `lang_es.json` came back missing. That looked like three
untranslated captions in the sidebar of a money app across seventeen languages.
Rendered under es/fr/de/zh/ru before reporting it:

    es  ['SALDO', 'CRÉDITO', 'DÉBITO', 'ENTRADAS', 'BUSCAR', ...]
    ru  ['БАЛАНС', 'ПРИХОД', 'РАСХОД', 'ЗАПИСИ', 'ПОИСК', ...]

All correct — nbi18n resolves raw Label text by a different key form than the
literal one. **No defect.** Seventh time today an inference lost to a rendering.

**Suites 15, checks 244 -> 254, all green.**

### Defect 14 — four modal cards, four unstyled scrims

Accounting builds FOUR modal scrims (row editor, delete confirm, opening
balance, report card) and styled NONE of them: there was no `.scrim` rule in the
file at all, so every card floated over the ledger at full contrast while bills,
ebook, illustrator, installer, media, music, novel, sequencer, settings, tasks
and video all dim theirs. Accounting was the outlier.

It matters more here than in a text app. **A card covers the LEFT of the figures
behind it**, so a row reading `$950.00` shows through as `50.00` and `$51.40` as
`51.40` — a delete confirmation drawn on top of a ledger displaying plausible
WRONG NUMBERS. The confirm therefore carries a heavier veil (0.32) than the other
three (0.18), the distinction settings.py already draws for the same reason.

Measured, not eyeballed: undimmed paper is (252,251,248), the 0.18 veil lands at
(211,210,207) and the 0.32 confirm veil at (180,179,176). **Two renders minutes
apart were not distinguishable by eye** — 211 against 180 looked identical — and
only the pixel values settled it. `accounting_cards_selftest` therefore samples
the rendered pixbuf rather than asking a widget anything: asking whether the
EventBox has the class passes with the CSS deleted, and asking whether it is
"visible" passes with no background at all, because an EventBox owns a GdkWindow
and paints nothing without one.

### The keyboard suite, and a stub that had to be replaced

The second Codex dispatch produced no app change and a placeholder
`accounting_keyboard_selftest.py` that exited non-zero unconditionally, on the
premise that GTK could not initialize under `tools/guestrun.sh`. Measured, that
premise is false — `Gtk.init_check()[0]` is True there and the offscreen pixbuf
renders. Reporting a blocked measurement honestly is right; the blockage has to
be real, and a permanently-red suite in `tools/` would have broken the
campaign's aggregate run. Rewritten as 24 real checks over `_row_key` and the
ordered Escape chain, including the OS-wide rule: **Delete on a row ASKS, and
Esc never deletes.**

### Three more instruments corrected by their own red proofs

* A mutation that dropped a line WITHOUT its indentation gave an IndentationError,
  the suite never ran, and the harness printed nothing — reading exactly like a
  clean pass. The harness now prints `*** SUITE CRASHED ... this is NOT a red
  proof ***`. Twice today a crashed proof laundered as zero failures.
* `accounting_cards_selftest`'s source-level check opened the repo copy of
  accounting.py **by its expected path**, so it stayed green against a mutated
  module with every `add_class` stripped. It reads `accounting.__file__` now.
  A check that reads past its own subject cannot go red.
* The keyboard suite shipped, for one run, a check reading
  `... or True`. It was deleted rather than repaired: **a check that cannot fail
  is worse than no check, because it reads like coverage.**

## Day 2 close

**Suites 4 -> 17. Counted checks 284.** All green at exit code 0, plus
py_compile, construct_one, css_parse_check, ascii_css_check, toyfont_check,
voice_check, jargon_sweep, i18n_placeholder_check, dead_setting_check, and
minsize_sweep (accounting[de] 964x389, ALL FIT).

**17 defects fixed: 14 mine, 3 Codex.** One product decision (date ordering)
handed up rather than taken. One new English string for the x17 merge.

### Defect 15 — in Yiddish, every signed figure showed its sign on the wrong end

`nbi18n.RTL == {"yi"}` and `nbapp` calls
`Gtk.Widget.set_default_direction(RTL)` for it, flipping widget order for the
whole process. **Nothing had ever run this app in that state.**

The columns mirror correctly (GTK handles that: DATE rightmost, BALANCE
leftmost, sidebar on the right). What does not survive is the SIGN. A leading
"+" or MINUS is a bidi-WEAK character (class ES) followed by a run of European
numerals (EN), so the Unicode bidi algorithm resolves the sign to the paragraph
direction and lays it out on the far side. Measured under yi:

    label holds '+$1,105.00'    ->  Pango drew  '$1,105.00+'
    label holds '-$1,974.39'    ->  Pango drew  '$1,974.39-'

**Unsigned figures were unaffected** — which is exactly why this hides. The
balance headline, the opening balance and the whole running-balance column all
looked perfectly correct while every credit and every debit had its sign on the
wrong end. In a ledger the minus is the only thing on the row that says which
way the money went.

Fixed with `Accounting._ltr`, wrapping the finished signed string in U+2066
LEFT-TO-RIGHT ISOLATE .. U+2069 POP DIRECTIONAL ISOLATE — which, unlike an LRM,
cannot leak its direction into the surrounding text — applied at the seven
label sites and gated on the direction ACTUALLY IN FORCE (`Gtk.Widget
.get_default_direction()`), not on the language name, because the direction is
what Pango lays out against. The PDF renderer is left alone: it positions text
at absolute coordinates and is LTR regardless of interface language.

**In the other sixteen languages the string is unchanged, byte for byte** — all
17 existing suites stayed green with no edits, which is the point of gating it.

`accounting_rtl_selftest` (14 checks) measures the VISUAL order Pango resolves,
by asking the laid-out layout where each logical character landed on the x axis.
Checking that the isolate character is PRESENT would only confirm the fix was
applied — it would pass just as well if the isolate did nothing. That assertion
is kept, but as a second one, never as the only one.

**The same shape exists elsewhere in the OS at lower stakes**, reported to the
campaign rather than fixed here (other lanes' files): `calendar.py` and
`widgets.py` build `_t("+%d more")`, `language.py` builds `_t("+%d XP")`.

**Suites 18, checks 301.**

### Defect 16 — a form left open across midnight stamped yesterday

`_stamp_today`'s own docstring promises "a long-open window never stamps a new
entry with a stale day". It was only ever called at the moment the form was
revealed, and there is no timer. Measured with a mocked clock:

    form opened at 23:59 31 Dec -> label shows '31 Dec'
    clock is now 00:01 1 Jan    -> label STILL shows '31 Dec'
    the committed entry:  date='31 Dec'  iso='2026-12-31'

**The repair belongs on the LABEL, not at commit time.** Re-stamping when the
entry is committed would store 1 Jan while the form on screen still read
31 Dec — which is precisely the desynchronisation Codex's year-boundary fix
exists to forbid, and that check would have caught me. A once-a-minute tick
while the form is open refreshes the visible label and the cached ISO together,
so what the person can see stays exactly what gets committed.

Periodic rather than a single timer armed for midnight: this is a laptop OS that
suspends, and a timer scheduled for a moment the machine spends asleep does not
fire on resume. The tick retires itself on finding the form shut, because Esc
closes the form without going through `_toggle_form` — cheaper than trusting
every closing path to say so — and `_on_destroy` stops it as well, alongside the
search timer, for the same reason that one is stopped.

Four checks in `accounting_form_selftest`, two red proofs. Worth recording what
the first proof does NOT turn red: the rollover checks call `_tick_form_clock()`
directly, so they still pass with nothing scheduled to call it. **The tick's
BEHAVIOUR and the fact that it is ARMED are separate facts** — the same
handler-versus-binding split as the keyboard suite — and each needs its own
check. Neither alone catches the defect.

**Suites 18, checks 305, 0 red.**

### The salvage path — pinned, no defect found, and that is the honest result

The third Codex dispatch (the salvage path) FAILED: its provider refused the
prompt — "This content was flagged for possible cybersecurity risk" — a false
positive on a data-integrity fuzzing task described entirely in terms of
malformed JSON. It left no partial file. **Codex yield across the day: 3
dispatches, 1 useful.** Written up so the recipe carries it: describing
robustness work in terms of "breaking" a parser is enough to trip the filter,
even when the parser is our own and the goal is recovering the user's data.

Did it myself. `_salvage_tx` walks a corrupt ledger cutting out balanced `{...}`
runs, string-aware so a description containing a brace or a quote cannot confuse
it — a claim about the user's own words, which are the one part of the file they
chose. Ten hostile descriptions: a brace, an unbalanced brace either way, an
escaped quote, a trailing backslash, nested braces, a quote-then-brace, a
newline, a full-width brace lookalike, and **a description holding a complete
fake transaction with its own `amt`**.

    0 of 10 confused the scanner. Nothing invented, nothing duplicated,
    and the entries either side survived every time.

**Two failure modes, not one.** Losing an entry is the obvious one; INVENTING
one, or recovering one twice, is just as bad in a ledger and far harder to
notice — a balance that is wrong in the app's favour still looks like a balance.
Every check compares what came back against the entries really in the file, by
value, rather than counting them.

`accounting_salvage_selftest`: 55 checks, three red proofs. **It pins defence
that was already there; it did not find a defect.** Worth stating plainly,
because a suite arriving with no defect attached usually means the tests are too
weak — the red proofs are the evidence that this one is not.

One check of mine was wrong and the code was right: `_num("3.5")` coerces to
3.5, and I had asserted 0.0. On a SALVAGE path a hand-edited file carrying
`"amt": "12.50"` should give back 12.50 rather than zero — recovering data, not
validating typed input, which is `_parse_amount`'s job and is strict.

And recorded in the docstring: mutation 1 (dropping the scan's string-awareness)
does NOT turn the "invents nothing" checks red, because the inner quotes are
backslash-escaped in the raw text so the cut-out substring fails `json.loads`.
That mutation's failure mode is LOST entries, not invented ones. Saying which
proof covers which check matters more than the count of proofs.

**Final: 19 suites, 360 checks, 0 red.**

### Stale card indices — defended, and now pinned

Every overlay card captures a row INDEX into `self.tx`, and Ctrl+Z is bound at
the WINDOW so it fires straight through the modal scrim. If undo reshapes the
ledger while a card is open, that captured index names a different entry — and
the card's next action is a DELETE. This is the academics index-remap class
aimed at somebody's money.

Measured across three combinations:

    delete confirm open + Ctrl+Z  -> the card CLOSES; no stale index survives
    row editor open     + Ctrl+Z  -> the card CLOSES
    delete confirm open + search  -> the card stays open (right: a filter
                                     changes only what is DISPLAYED) and
                                     confirming still removes the row it was
                                     opened for, because the index refers to
                                     self.tx and not to the filtered view

**Defended by design rather than by luck, and nothing pinned it** — a refactor
could quietly drop the close-on-undo and the defect would stay silent until it
deleted the wrong row. Eight checks added to `accounting_keyboard_selftest`.

Also measured and clean: quarantine filename collisions. Five damaged opens
inside 0.09s (well within one `%H%M%S` stamp), on both the unreadable path and
the wrong-shape path — five distinct aside files, 5/5 originals recoverable.
Both `_quarantine` and `nbapp.preserve_damaged` carry a de-duplicating counter.

### `_ltr` promoted OS-wide, and accounting now delegates to it

The campaign lifted this method into `nbi18n.ltr` after finding the same shape
in eleven other apps, and added `tools/rtl_check.py` as a static guard (18
at-risk labels, ratcheted). `Accounting._ltr` now delegates, so there is one
implementation rather than two that can drift.

Kept as a thin METHOD rather than inlined at the seven call sites, deliberately:
a red proof must have a name to mutate in its OWN module and must never reach
into a campaign-owned file. The two proofs were retargeted accordingly, and the
second now mutates a CALL SITE rather than the helper — which is the regression
a future edit is most likely to introduce, the helper staying correct while one
caller quietly stops using it.

**A trap worth recording:** the delegation compiled clean and would have raised
`NameError` on every refresh. `accounting.py` imported only `from nbi18n import
_t`, not the module. `py_compile` cannot see an undefined global, and neither
can a static gate — `construct_one` is what catches it, and it only catches it
because it actually builds the window.

**Final: 19 suites, 368 checks, 0 red.** rtl_check PASS, voice_check CLEAN,
minsize ALL FIT.

### Smoothness — adding an entry rebuilt the whole page

Profiled `_refresh()` on a 600-entry ledger: **153 ms**, made of 44 ms building
150 row widgets, most of the rest destroying the 150 that were already there,
and the GTK settle after. `_autosave` was 4 ms — the disk was never the problem.
All of that for a change that touches exactly ONE row.

Appending cannot alter any existing row. A running balance is the total AFTER
that entry, so every earlier row keeps its own; and every existing entry's
chronological index is unchanged because the new one goes on the end. So
`_append_one_row` inserts a single row at position 0, trims the page back to
`_shown`, and rebuilds only the one-widget footer.

Measured, add latency by ledger size:

    entries      0     50    200   1000   5000
    before     28ms  115ms  245ms  197ms  246ms
    after      17ms   23ms   17ms   29ms   68ms

At 5000 entries the remaining 68 ms is the autosave of 5000 entries, which is
real work. The host is much faster than the target hardware, so 245 ms here is
the kind of lag that is plainly felt there, on the most common action in the app.

**The shortcut is gated hard and falls back on anything unusual** — a filter, an
empty ledger, an empty row list. The search is checked BOTH ways, the parsed
`_terms` AND the raw entry text, because clearing the box only schedules a
130 ms timer: for that window `_terms` is already empty while the rows on screen
are still filtered, and the fast path would have inserted into a filtered view.

**The only thing that licenses a shortcut is that it changes nothing**, so
`accounting_fastpath_selftest` (33 checks) does not primarily assert that it is
faster — it asserts the widget tree after a fast add is INDISTINGUISHABLE from
the one a full rebuild produces: same rows, same cell texts in order, same
footer, same sidebar. Checked at seven ledger sizes including 149 / 150 / 151,
the boundaries either side of a page. Speed is asserted only as a loose ceiling
(under half a full rebuild), never as a wall-clock number — a millisecond
threshold on shared hardware is a gate that fails for the wrong reason.

Three red proofs, each caught by the equivalence check rather than by a
purpose-built assertion: dropping the page trim, appending at the bottom instead
of the top, and running the fast path while a search is active.

**Final: 20 suites, 401 checks, 0 red.** All OS-wide gates clean, minsize ALL FIT.

## CORRECTION — the mutation scores I first reported were wrong

The sweep passed a `*_MODULE_DIR` environment variable and ran each suite as a
subprocess. **Only 6 of 20 accounting suites and 6 of 8 bills suites read that
variable.** The rest imported the REAL module, so most "survivors" were never
tested against the mutant at all: they survived because nothing mutated was ever
loaded. The published 50% (accounting) and 61% (bills) were both understatements
of the suites and overstatements of the gaps.

**How it surfaced:** line 1627 changes behaviour (the opening row stops
revealing) and `accounting_prefs_selftest` checks exactly that — yet the sweep
called it a survivor. Both cannot be true. Same shape as day 3's tell, where a
Box could not allocate 1024 while its children summed to 1172: **the
contradiction is the finding, not either number on its own.**

Fixed by redirecting the IMPORT through `sys.meta_path` (scratchpad/runsuite.py),
which works whatever a suite does about env vars, and sanity-checked against a
known-real mutation that must come back caught.

**Third instance in three days of "a check reads past its own subject"** — the
cards suite opening the repo file by path, the transition suite parsing a
hardcoded SOURCE, and now the sweep itself. Standing rule: **any tool that
mutates a copy must PROVE the copy is what got loaded, and the cheapest proof is
a known-real mutation that must come back caught.**

### Corrected, and better than the wrong figures suggested

    accounting   32 candidates,  7 survived   78% caught
    bills        46 candidates,  7 survived   85% caught

**Every one of those 14 survivors is a mutant an equivalence battery
independently MEASURED as behaviour-preserving** — the salvage-scanner internals,
the money sign (`n < 0 and cents != 0`: the second clause already handles zero,
so `<` vs `<=` cannot differ), the credit/debit boundaries against a
zero-amount ledger, and bills' civil-date and guard expressions. Cross-referenced
mechanically: **unexplained survivors, none.**

So of the mutants that actually change behaviour — 25 in accounting, 39 in bills
— **every one is caught.**

The honest limit: "measured equivalent" means equivalent across the battery's
inputs, not proven equivalent for all inputs. It is evidence, not a proof.
