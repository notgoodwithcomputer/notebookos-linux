# Task 047 — bills, day 3 of the per-app loop

**Session:** app-improve. **Claimed:** CLAIMS.md 2026-08-09 00:01.
**Module:** `de/bills.py` (2015 lines).

## Baseline, recorded before any edit

    tools/bills_selftest.py        green   (prints "bills selftest: OK", no count)
    tools/bills_flows_selftest.py  green   19 checks
    => 2 suites, 19 counted checks, 0 red

`bills` IS in the OS-wide `store_damage_selftest`, and the campaign verified its
damage coverage is genuine (it opens a malformed store, reopens, and asserts
byte-for-byte survival across autosaves including the `.bak`-rotation
subtlety). So the day goes to features, UI and defects rather than to building a
defence.

## Coverage audit

**49 of 78 methods had never been named by any suite** — a worse ratio than
accounting's 33 of 89. Untested included `needs_paying` and `sort_key`, which
are the core domain logic of a bill tracker, all of the date math
(`add_days`, `_day_from_ordinal`, `fmt_due`), the whole detail pane, and both
`_render_pdf` / `_write_export_pdf`.

## Three harness artefacts before a single real defect

The routine says render at 1024x722 and LOOK. Doing that honestly took four
attempts, and the first three each produced a picture of a catastrophic bug that
was not there. Recorded in full because every remaining app-day inherits them.

1. **`set_size_request` is a MINIMUM.** `Gtk.OffscreenWindow` allocates its child
   the child's NATURAL size, so bills — natural width 1172 — rendered at 1172
   however it was asked. `tools/uishot.shot_window` has the same shape, which
   means **no render of this app has ever been taken at the panel it ships on.**
2. **A ScrolledWindow does not fix it.** `EXTERNAL` hands the child its natural
   size and CLIPS — producing a screenshot with the bill's AMOUNT and its Edit
   button sliced off, which looks exactly like the worst possible defect in a
   bill tracker. `NEVER` requests the child's full natural width; `AUTOMATIC`
   scrolls. None is what a real window does. What works is a container that
   reports the budget as both minimum AND natural and allocates its child
   exactly that — including `do_get_preferred_height_for_width`, because GTK3
   lays out height-for-width and without that override the child collapsed to
   its minimum height and rendered a 1024x239 strip. See `scratchpad/clamp.py`.
3. **`nbapp.screen_size()` AT BUILD TIME.** With the allocation genuinely
   clamped to 1024, content STILL overflowed by 148px. `bills.py:1034` sizes the
   detail column as `max(430, min(COLUMN_W, sw - SIDEBAR_W - 112))` — offscreen,
   `sw` is the HOST monitor, so the app builds a 1920 layout. At a real `sw` of
   1024 the column is 660 and everything fits. **There is no overflow defect.**

**The tell that saved the false report:** the top-level reported a 1024
allocation while its children summed to 1172, and a Box cannot do that. When the
numbers are internally inconsistent, the instrument is wrong. Pinning
`nbapp.screen_size = lambda: (1024, 768)` before construction gives the true
picture, and at the true picture bills lays out correctly in both English and
German (`Rechnungsverwaltung`, `TAGE POSTLAUFZEIT`, `ZAHLUNG ERFASSEN` all fit).

Filed to APP-LOOP.md step 3 and to HANDOFF.md for the campaign, since uishot is
campaign-owned.

## Defect 1 — a posted bill with no address printed NO DEADLINE

The report's docstring calls it "the copy that goes by the phone", and the
POST BY date is the single thing this app exists to produce. It was emitted
inside `if bill["address"]`:

    if bill["method"] == "mail" and bill["address"]:
        ...address lines...
        if info["post_by"]:
            text.emit(_t("Post by %s") % ...)

But the deadline does not depend on knowing the address — the LEAD DAYS are what
set it. Measured: `due_info` returned `post_by 2026-08-12` while the printed
report said only

    Due 17 Aug  ·  Account NOADDR  ·  By post

So the paper copy told somebody to post on the 17th a bill that had to be in the
post by the 12th. Fixed by emitting the deadline for any bill that HAS one —
`post_by` is already None unless the bill is posted with a lead, so the date's
own existence is guard enough, and the `lead=0` case correctly still prints
nothing.

## Defect 2 — a posted bill WITH an address silently dropped its note

Same if/elif chain, opposite branch: the note hung off the address test, so the
bills that had an address printed everything except the user's own words.
Measured: a bill noting `Quote 88` printed payee, amount, address and deadline,
and not the note. The note is now emitted for every method.

Both defects are the same shape — one chain deciding two unrelated questions,
"where do I send this" and "what else should this page say".


## The domain model — pinned, and one hole found in my own suite

`due_info`, `needs_paying`, `sort_key` and `month_total` are the whole domain
model, and `needs_paying`/`sort_key`/`month_total` had never been named. They
are also what `widgets.py` reads for the desktop tile, through the same
`read_bills()`/`due_info()` pair on purpose, so a regression here is wrong in
two places at once.

**Measured, nothing was wrong.** Ten bill shapes walked day by day toward their
due dates: urgency never goes backwards (later -> soon -> post -> today ->
overdue) and `needs_paying` never flips back to False. Thirteen `month_total`
cases exact, including the last day of the month, 29 Feb in a leap year, 28 Feb
in a common year, and the December/January boundary. The July bug-fix audit of
this math holds up.

`bills_schedule_selftest`: **61 checks**, three red proofs.

### The third proof found a hole in the suite I had just written

Dropping `"post"` from `ACTIVE_KINDS` — which makes a cheque that must go in
today's post vanish from the sidebar count, the desktop tile and the top of the
sort order — left all 48 checks GREEN.

Both of my invariants were blind to it by construction. Removing "post" makes
`needs_paying` go False EARLIER, which is neither a backward step in urgency nor
a True->False flip. The explicit state/`needs_paying` table exists because of
that proof, not before it.

**An invariant about how a value CHANGES cannot see a change in which values it
takes.** Sibling of day 2's "a second route to a figure hides a broken first
route": both are checks that constrain the shape of an answer without ever
pinning the answer.

The first proof made the same point from the other side: breaking `post_days
<= 0` to `< 0` produced the state `"Post in 0 days"` — still `kind == "post"`,
so monotonicity stayed green, and only the literal day-by-day word list caught
it. **An invariant and the actual words both have to be pinned.**

## Close

    tools/bills_selftest.py            green
    tools/bills_flows_selftest.py      green   19 checks
    tools/bills_keyboard_selftest.py   green   19 checks   (new)
    tools/bills_report_selftest.py     green   35 checks   (new)
    tools/bills_schedule_selftest.py   green  209 checks   (new)
    => 4 suites, 110 counted checks, 0 red   (baseline 2 suites / 19)

Five red proofs in total, each applied alone to a scratch COPY. Plus
`construct_one`, `py_compile`, `voice_check`, `jargon_sweep`,
`i18n_placeholder_check`, `css_parse_check`, `rtl_check`, `dead_setting_check`
and the OS-wide `store_damage_selftest` — all clean.

**New English source strings: NONE.**

## Postscript — the minsize fixture, and a claim I withdrew

The campaign corrected my reading of the 622-vs-1172 gap: it was NOT minsize
under-reporting bills' width. 622 was the EMPTY shell; 1172 was bills filling a
1920 screen it was never on, because my build was unpinned. bills is a
FILL-THE-PANEL app — `col = max(430, min(820, sw - 364))` — so its natural width
tracks the screen and it never overflows. "ALL FIT" *was* evidence for bills.
The real weakness was the one they then fixed: the sweep only ever proved that
EMPTY apps fit.

Cross-checked their populated figure from a different harness: **bills at the
1024 panel measures 1012, matching exactly.**

Added `tools/minsize_fixtures/accounting.json` (60 entries, the widest figures
the money columns can hold). It found something:

    accounting[de]   needs at least 1001 x 389   <-- TIGHT: 23px to spare
    accounting        needs at least  986 x 389
    (the empty shell had reported 964)

So accounting has been sitting 23px from the edge in German and the gate could
not see it. Sweep still exits 0, ALL FIT.

**The rule for which apps need one, since "it scrolls" is not the answer.**
Accounting qualified DESPITE having a ScrolledWindow: its money columns are
fixed pitch (`_GRID = (80, -1, 118, 118, 140)`) and only the description is
elastic, so content raises the floor by 17px even though the rows scroll. The
test is whether any COLUMN has a fixed width, not whether the content scrolls.

### A finding I measured, believed, and withdrew

I measured populated minimums across en/de/el/ru/pl, got an identical number in
all five for both apps, and was one message away from reporting "populated width
is structural, not text-derived — one fixture per app suffices, no per-language
variant needed."

It was an artefact. The probe re-imported a CACHED module, so `NB_LANG` never
took effect after the first iteration and I measured English five times. The
sweep's own 986-vs-1001 delta is the true one, and it is real text growth. The
correct conclusion is the OPPOSITE of the one I nearly filed.

**Ninth harness artefact in three app-days, and the tell was the same as always:
a number that is too stable.** Five languages agreeing to the pixel is not a
finding, it is a broken instrument — the same shape as day 2's suite that passed
while every row printed -$1.00.

## Recording a payment — defended, and the guard is not the one it looks like

`bills_flows_selftest` already walks the happy path. This took the paths either
side: the ways a person can commit the sheet more than once, and what they can
put in the amount field.

**Why double-click is the check that matters here.** Recording a payment
ADVANCES a recurring bill. So a second commit would not write an obvious
duplicate — it could file against a month that has not been paid, and the cost
is a missed bill discovered by a late fee. An impatient double-click on a button
labelled "Record Payment" is an ordinary thing to do.

Measured: **defended.** One, two and three clicks record exactly one payment;
Enter-then-click likewise; a non-numeric amount records nothing and leaves the
sheet up to say so; a blank amount on a varies bill is a legitimate record
(stored as no figure, not as zero); a one-off settles rather than advancing.
24 checks in `bills_payment_selftest`.

### The red proof corrected me twice about my own explanation

I wrote that the guard is `_commit` calling `_close_overlay()` BEFORE it appends
the payment. **It is not.** Moving that call below the append left all 24 checks
green: the sheet is torn down on commit either way, so the second click has no
button left to press. The guard is that the sheet CLOSES AT ALL, not where in
`_commit` it closes — and a refactor that kept the sheet up to show a
confirmation in place would reopen the hole however carefully the ordering was
preserved. The docstring says that now; it said the wrong thing first.

And the failure mode is not the one I predicted either. With the close removed,
the duplicates settle the SAME occurrence rather than consecutive months,
because `settles` is captured once when the sheet opens. So the real damage is a
doubled payment against one month while the bill still advances only one
occurrence — **a check on the due date alone would have missed it entirely.**

**Asserting a mechanism is not the same as asserting an outcome, and only the
outcome is worth pinning.** I have now written a wrong mechanism into a docstring
twice in three days; both times the red proof was what caught it. That is the
argument for running the proof even when the check is already green.

### One more probe artefact

Every payment check first read as "nothing was recorded". The detail pane behind
the sheet carries its OWN "Record Payment" button — the one that opens the sheet
— and a whole-window search finds that one first, so each click re-opened the
sheet instead of committing. Scoped the search to `_overlay_card`. A defect in
the probe that looks exactly like a defect in the app: **tenth in three days.**

## Four more areas measured, all clean, and one blast-radius check

The fixture is in the gate now (bills PASSES preservation independently, top and
per-record, confirmed by the campaign in a clean process), so
`release/1.0/bills-store-damage-fixture.py` was deleted rather than left to
drift from the copy that ships.

  * **A save that FAILS.** Pointed the store at an unwritable path mid-session:
    the error is set, surfaced in the window, SURVIVES a refresh (which is why
    `_save_error` is held rather than flashed — the status strip is rewritten on
    every refresh and a one-shot message would be wiped a moment later), the app
    stays usable, and it clears on the next good save. Correct.
  * **Extreme but reachable data** — a 78-character payee, a $1,234,567.89
    amount, a 30-day lead, a four-line address, twelve payments, a $0.00 bill,
    a bill 400 days overdue. Rendered at a genuinely clamped 1024x722:
    **0 widgets past the right edge.** Long text ellipsizes, seven figures fit.
  * **The empty first-run state.** Icon, "No bills", a line saying what a bill
    holds, and an Add button. Sound. One observation not acted on: "No bills"
    appears three times on that screen (sidebar list, centre state, footer
    strip). Noted rather than unified — this project's record is that most such
    "inconsistencies" are deliberate, and three panes legitimately each need
    something to say.
  * **The desktop tile still agrees with the app.** This one is a check on MY
    OWN change: `normalise()` is shared with `widgets.py` through
    `read_bills()`, on purpose, so the tile and the app can never disagree about
    what is due. After carrying unknown keys through it, both see the same three
    bills, the same states, and the same month total (12575 cents, correctly
    excluding the varies bill), and the extra keys reach the tile harmlessly.
    **A fix inside a function two surfaces share needs its second surface
    measured, not assumed.**

## Hiding a field must not discard it

The bill sheet shows a different set of rows per payment method: an address and
a posting lead for post, a phone number for post and phone, a note for
everything EXCEPT post. So switching method makes a filled-in field vanish from
the sheet — and the question is whether it vanishes from the BILL.

Measured both directions through the real sheet: a phone bill with a note
switched to post keeps the note; a posted bill with an address and a lead
switched to phone keeps both. **Correct** — the rows are hidden rather than
destroyed and `_commit` reads every widget while they are all still alive.

That is also what makes a note on a POSTED bill reachable at all (set it under
another method, then switch), which is exactly the case defect 2 fixed on the
printed side. The two guard the same user's data from opposite ends, and neither
was pinned before today.

Two red proofs, both the "tidy up the fields this method does not use" edit a
later reader would think obviously correct:

    "note": ... if method["id"] != "mail" else ""      -> the note destroyed
    "address": ... if method["id"] == "mail" else ""   -> the address destroyed

**One probe error worth the line it costs.** My first run showed the method
never changing and both fields intact — which reads as "switching does nothing".
The Save button is labelled **"Save Bill"**, not "Save"/"Done", so my filter
found nothing and committed nothing. A probe that never presses the button
cannot tell a preserved field from an unreached one. Eleventh probe artefact of
the loop; the tell was that BOTH cases passed and the method had not moved.

## Escape leaves, and never acts — pinned across all four overlays

`_on_key` was named by no suite. It carries the OS-wide rule in its own comment
("Esc leaves, it does not act") and a scar: "without this it closed the whole
app from under them."

Four overlays, and Escape has to mean the same thing in all of them. Measured,
all five behaviours hold:

    half-filled ADD sheet   -> closes, no bill created
    PAYMENT sheet           -> closes, no payment, the bill has not moved on
    EDIT sheet              -> closes, edit discarded, history untouched
    REPLACE-FILE question   -> closes, the file on disk is byte-identical
    nothing open            -> deletes nothing; Ctrl+Z restores one, with its
                               payment history

The replace-file case is the one that would cost most: **pressing a key to get
out must never be read as consent to overwrite** what is already in Documents.

19 checks, two red proofs (4 and 2 failures).

### Two admissions in the docstring rather than around them

**I shipped another `or True`.** `check("...and the app is still alive",
app.get_child() is not None or True)` cannot be false — the same slip as the
accounting keyboard suite, two days running. Deleted rather than repaired and
replaced with something real: Escape must be CONSUMED, not passed to the base
handler.

**And that replacement is weaker than it looks, so the docstring says so.** It
does not fire under the "Escape falls through" proof, because the base handler
also returns True — it is busy closing the window. So it distinguishes "handled"
from "ignored" but not "handled here" from "handled by the thing that shuts the
app". The four "closes" checks are what actually catch the scar. Kept with its
limit stated rather than presented as the guard it is not.

**A check that cannot fail is worse than none; a check that fails for a
different reason than advertised is the same problem wearing a better coat.**

## Bills older than the app, and the shape of a dangerous failure

A bill anchored years back is ordinary — imported, or one somebody stopped
paying. Measured, all correct and quick:

    monthly, unpaid since 2019   Overdue, outstanding = the ANCHOR, counts
                                 ONCE this month (not once per missed month)
    one-off from 2019, unpaid    Overdue
    monthly, 91 payments filed   advanced to 2026-08-15, "Due in 7 days"
    one-off, its payment filed   Paid
    200 bills x 40 payments      open 161ms, month_total 5ms, refresh 78ms

`month_total` at that scale came to 211116 cents against a naive sum of 219900.
Derived the difference independently rather than accepting it: the 8 bills whose
due DAY is the 1st have their August occurrence settled by the 2026-08-01
payment and advance out of the month; their amounts sum to 8784, and
219900 - 8784 = 211116 exactly. **A total that does not match the naive sum is
either a defect or an explanation, and the only way to tell is to derive it.**

### The red proof shows why these cases earn their lines

Mutating `occurrences` to yield only the anchor:

    FAIL a bill paid every month since 2019 has advanced to the current one
    FAIL ...and reads as due, not overdue and not paid  <- ('settled', 'Paid')

A bill with 91 payments filed and one still owing reads as **Paid**. Running out
of occurrences does not look like an error — **it looks like good news**, and it
is the one wrong answer a bill tracker must never give. That is the argument for
testing the aged shapes rather than only the fresh ones: the failure mode of the
old-data path is silence in the reassuring direction.

## All seventeen languages, and a control against my own tell

The routine asks for English plus one long-word language; ran all seventeen, a
FRESH PROCESS each (a reused module keeps the first locale — that mistake is
already in this file once).

    every language: min=1012, chrome shortened: 0

No heading, caption or button is ellipsised in any language; only user data is,
which is what ellipsis is for.

**Identical in all seventeen is the "too stable" signature**, and I have been
fooled by it once already this loop. So the probe carries a CONTROL that prints
a translated marker alongside the number:

    en [PAYMENTS]   de [ZAHLUNGEN]   el [ΠΛΗΡΩΜΕΣ]
    ru [ПЛАТЕЖИ]    ja [支払い履歴]    yi [צאָלונגען]

The locale really changed and the width really did not, which is expected for
THIS app and only this app: bills is fill-the-panel — `col = max(430, min(820,
sw - 364))` — so its width tracks the SCREEN, not the text. Accounting, whose
columns are fixed pitch, shows a real 15px en->de delta in the same measurement.

**A suspicious number is not a defect and not a pass; it is a demand for a
control.** Cheaper than either believing it or re-deriving it.

## HiDPI — no risk of its own, and the limit of what I measured

This OS once forced 1080p on 4K panels, so a scale-2 render is worth doing for
any app. bills at `GDK_SCALE=2`:

    logical alloc 1024x722, widgets report scale_factor=2, 0 zero-sized images

**bills draws nothing itself.** Its PDF goes through `nbprint.report_page` and
its only two raster surfaces are `nbicons.image("plus", 16)` and
`nbicons.image("bills", 34)` — and `nbicons` is scale-aware by construction:
`image()` returns `Gtk.Image.new_from_surface(surface(...))`, and `surface()`
calls `set_device_scale(scale, scale)`, which is the documented HiDPI pattern
from the earlier Retina run. So there is no scale-1 assumption in this app to
find.

**What this did NOT prove, said plainly:** `Gtk.OffscreenWindow.get_pixbuf()`
returned 1024x722 at scale 2 as well as at scale 1, so the render is not
evidence about pixel-level sharpness — it only shows the widgets are allocated
correctly, report the right scale factor, and produce no zero-sized images. The
sharpness claim rests on reading `nbicons`, which is inspection, not
measurement. Same standing as the maps write-path caveat: worth recording as
low-risk, not worth recording as verified.

## A bill due on the 31st must not drift

`add_months` states the rule in its own docstring — "the day of the month is a
rule, not an offset, so the clamp is applied to the ANCHOR every time rather
than carried forward" — and names both wrong answers: adding 30 days walks a
monthly bill backwards through the year, and adding one month to the PREVIOUS
RESULT pins a 31st bill to the 28th for good after one February.

Measured, correct:

    the 31st, monthly     2026-01-31  2026-02-28  2026-03-31  2026-04-30 ...
    the 31st, 2-monthly   2026-03-31  2026-05-31  2026-07-31  2026-09-30 ...
    29 Feb, yearly        2028-02-29  2029-02-28  2030-02-28  2031-02-28 ...

Both wrong answers are SILENT: every date they produce is a real date in roughly
the right week, and nothing looks broken until somebody notices their rent has
been due on the 28th since February.

### The proof strengthened a check I had just written

The "returns to the anchor day" check first read `max(seq)` — which the ANCHOR
ITSELF satisfies. Under the drift mutation it stayed GREEN while every
occurrence after February was pinned to the 28th for good. It measures `seq[1:]`
now and reports `[28]`.

**A claim that a value COMES BACK cannot be tested on a series that starts at
it.** Same family as the day-2 lesson that a second route to a figure hides a
broken first route, and the day-3 one that an invariant about how a value
changes cannot see a change in which values it takes: each time, the check
constrained something true but weaker than the claim in its own name.

Two red proofs (4 and 8 failures). bills: 8 suites, 228 checks, 0 red.

## A mutation sweep: how strong are the checks, really?

Having stopped finding defects, measured the SUITES instead. One decision point
at a time flipped in a copy of bills.py (`<=`->`<`, `and`->`or`,
`is None`->`is not None`), every bills suite run against the copy, survivors
reported. A survivor is a change to the app that no check notices — a gap in the
app's own terms rather than a coverage percentage.

**First run was wrong and said so loudly: 55 candidates, and 15 of the
"survivors" were `and`->`or` INSIDE DOCSTRING PROSE.** The filter skipped lines
STARTING with `#` or `"`, which is not the same as skipping strings. Re-ran with
`tokenize`, masking every STRING and COMMENT span before looking for an operator:
46 real candidates. **A sweep that mutates comments manufactures gaps** — and it
manufactures them in exactly the shape of a real finding.

    18 of 46 survived (61% caught)

Triaged rather than closed blindly. Most survivors are equivalent mutants — the
`1 <= y2 <= 9999` bound only differs at year 1, `every <= 0` vs `< 0` reaches
the same state by another route, `(o is not None and now is not None)` guards a
value already checked above. **Two were real:**

  * `sign = "\u2212" if n < 0 else ""` — flip to `<=` and every zero renders as
    MINUS ZERO. `money()` and `parse_money()` had no suite at all. A $0.00 bill
    is reachable (a correction, a waived charge), and "-$0.00" in a bill tracker
    is the kind of wrong that makes somebody distrust every other figure on the
    screen. `money()`'s docstring says it must match `accounting._money` because
    "the two apps are read on the same desk" — so it is a shared shape, not a
    private detail.
  * `int(frac[2]) >= 5` — the third-decimal rounding boundary. 8.405 becomes 840
    instead of 841.

Both now pinned (33 checks incl. a write-it-down-and-read-it-back round trip)
and both red-proofed: `money(0)` -> `-$0.00`, `parse_money('0.005')` -> 0.

## The bug-fix lane's error-honesty fixes, kept and guarded

That lane audited error paths across the OS and found two leaks in bills.py,
leaving the fixes uncommitted in my file rather than committing into it:
export exposed a raw `strerror` on a full disk, and print let a missing `lp`
escape as a raw exception.

Verified both rather than taking them on trust — all 8 suites still green,
`construct_one` OK, `store_damage` ALL PASS — and measured what a person
actually sees:

    ENOSPC on export  -> "The disk is full, so this could not be saved."
    no spooler        -> "Print failed"
    neither leaks the OS's own words, and the app stays usable

**Kept, and now guarded** (5 checks in `bills_report_selftest`) — they arrived
unpinned, which is how a repair gets undone by the next refactor.

One more probe artefact on the way, the twelfth: my first pass reported the
ENOSPC path surfacing NOTHING, because I grepped for "space"/"device" and the
message speaks of a DISK being FULL. **A filter written from the exception
rather than from the sentence.**

**bills: 8 suites, 266 checks, 0 red.**

## Verifying my own "equivalent mutants" — 7 of 16 were wrong

The sweep left 18 survivors. I closed 2 and dismissed 16 as equivalent mutants
**by reading them**. Then measured: applied each surviving mutation to a copy and
compared a battery of real domain-function calls against the true module.

    7 of 16 "equivalent" survivors actually CHANGE behaviour

  * lines 207-208, the civil-from-days conversion inside `_day_from_ordinal`:
    `add_days` wrong by a day across month and year edges. **That is the
    function that turns a lead into a POST-BY date** — the one figure this app
    exists to produce — and it was pinned only at the two points the existing
    post-by cases happen to touch.
  * lines 226/249, the `_parts` guards: date FORMATTING changes.
  * lines 374/377/433: raise rather than mis-answer, so they are caught by
    crashing rather than by a named failure — a catch, but a weak one.

Now pinned: `add_days` across eleven boundaries plus an out-and-back identity at
six dates x six offsets, the three written date forms, and `fmt_due`'s year rule
(carry the year only when it is not this year — "on a bill due in three weeks
the year is noise; on one due in fourteen months it is the whole point").
Re-proved: the two civil-conversion mutants now fail 13 and 18 checks; the
`fmt_due` one fails 8.

**"Equivalent mutant" is a claim, and it needs measuring like any other.** I had
just spent three days catching myself asserting things I had not measured, and
then wrote off sixteen of them in a paragraph.

**And a battery only certifies what it exercises.** My equivalence battery
pronounced the `fmt_due` mutant equivalent for the plain reason that it never
called `fmt_due`. A survivor inside an unexercised function is not evidence of
equivalence — it is the absence of evidence, wearing the same face.

One correction the new checks forced on themselves: I asserted `add_days`
refuses an impossible date like 2026-02-30. It does not — it goes through
`_ordinal`, which normalises. That is not a defect and not reachable: the
boundary guard is `_parts`, which rejects, and every caller crosses it first.
The checks now assert the leniency where it lives and the rejection where it
lives, and a bill stored with an impossible due date is measured getting today
rather than a date that does not exist.

**bills: 8 suites, 357 checks, 0 red.**

## Not mine, reported: voice_check is red at HEAD

`voice_check` went RED after another lane's 09:32-09:45 batch:
`finder.py:1602`, a NEW prose-in-ui string unaccounted in `tools/voice_ledger.json`.
bills.py is not in that batch and is not flagged. Reported to that lane and left
alone — their string, a campaign-owned ledger, and no business of this day's work.

## Day 3 close

    tools/bills_selftest.py            green
    tools/bills_dates_selftest.py      green   19 checks   (new)
    tools/bills_extra_selftest.py      green   32 checks   (new)
    tools/bills_flows_selftest.py      green   19 checks
    tools/bills_keyboard_selftest.py   green   19 checks   (new)
    tools/bills_payment_selftest.py    green   24 checks   (new)
    tools/bills_report_selftest.py     green   35 checks   (new)
    tools/bills_schedule_selftest.py   green  209 checks   (new)
    => 8 suites, 357 counted checks, 0 red   (baseline 2 suites / 19)

**4 real defects fixed**, twenty-eight red proofs, no new English strings:

  1. the printed report dropped the POST-BY deadline when no address was set
  2. the printed report dropped the note when one was
  3. DATA LOSS ON OPEN — every unknown field destroyed, at three separate sites
  4. the View menu's sort order was never written down

Plus accounting's third preservation site, found by the OS-wide gate on an app I
had already closed as done, fixed under a short re-claim.

bills is now IN the OS-wide store-damage and preservation gates (7 damage cases,
top+per-record sentinels), verified independently by the campaign rather than
only by the suites I wrote.

Measured clean and pinned rather than changed: the domain model (due_info,
needs_paying, sort_key, month_total), the payment path, the date picker in three
languages, Escape across four overlays, method-switch field preservation, aged
bills, save-failure messaging, the desktop tile's agreement with the app, and
all seventeen interface languages.

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
