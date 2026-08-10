# 020 — What no suite ever runs

**Lane:** E (harness) · **Streams:** S1 truth defects, S6 gates
**Status:** CLOSED

The untested-module sweep (017–019) asked *which modules has no suite ever
imported*. It ran out after three finds. This sharpens the same question by one
level — **which functions does no suite ever enter** — and it immediately found
the last unguarded export in the tree.

## The tool

`tools/func_coverage.py`. For a module, it finds every suite that reaches it,
runs them all with `sys.setprofile` attached, and lists the functions never
entered. Profile events fire on call and return only, so it costs a few percent
rather than the ten times of line tracing.

**It is an audit, not a gate, and deliberately not in `run_all_gates`.** Running
every suite under a profiler costs about what the whole gate run costs, and a
forty-minute gate is one people skip. It is for choosing what to work on.

### Its own blind spot, found before it was believed

The first version matched imports by name and reported `Packages.__init__` as
never entered — 8 of 39 functions covered. `construct_all_host.py` builds all
thirty apps through `importlib.import_module(name)`, which no name-regex can
see. The real figure is **26 of 39**. That single omission overstated the gap
for every app in the tree, which is worse than not measuring: a checker that
manufactures work gets acted on. Dynamic importers now count for every module.

## What it found in bills

`bills_selftest` covers the data model. **25 of bills.py's 76 functions were
never entered**, and they were the user-facing half: `_open_form` and its
commit, `_open_payment` and its commit, `_confirm_delete`, `_do_delete`,
`_render_pdf`, `_export_pdf`, `_print`, `_on_key`. Everything you can do to a
bill.

**`_export_pdf` wrote `Documents/Bills.pdf` at a fixed name with no question
asked.** A second export destroyed the first, and destroyed anything else in
Documents with that name. ROADMAP #5 fixed exactly this in Journal, Novel,
Cookbook and Academics — bills was not on that list and kept the defect. A
sweep of every fixed-path export confirms it was **the last one in the tree**
(the only other hits are `tempfile.mkstemp`, which is atomic and unique).

Fixed with the pattern the other four use, in bills' own overlay idiom, reusing
the same three strings — `Replace file?`, `“%s” already exists in Documents.
Replace it?`, `Replace` — which all seventeen catalogs already carry, so there
is one wording for "you are about to overwrite". Cancel takes the focus, as on
the delete confirm: a stray Return must not be the keystroke that overwrites a
file.

## Gate

`tools/bills_flows_selftest.py`, 19 checks, driving the real sheets: add a bill
and confirm it reaches disk; record a payment and confirm the bill **moves on
to its next due date** (the POST BY date is the whole point of the app); the
delete confirm with Cancel focused, Cancel keeping the bill, Delete removing it
from memory and disk; and the export in all three states.

**A decoy at the real destination.** Asserting the export "wrote a file" passes
whether or not it asked first. Known bytes are planted and read back: DECLINE
must leave them untouched, ACCEPT must replace them with a real `%PDF`, and a
free name must be written with no question at all.

bills is now 72 of 78 functions entered.

**Red-proof, four mutations:**

| mutation | result |
|---|---|
| the export guard removed (the shipped code) | 6 fail, ending "the decoy survived — it did not" |
| Return focused on Replace instead of Cancel | 1 fail |
| a payment kept in memory but never saved | 1 fail |
| Delete drops the record from the list but not from disk | 1 fail |

## A trap in the suite, worth recording

`app._overlay` is the `Gtk.Overlay` wrapping the **entire window**; the card is
`app._overlay_card`. Walking the former found the menu bar's buttons and every
row in the list, so "is a question being asked?" was true at all times and
`press()` clicked whichever menu item happened to share a label with the one the
card wanted. Both failure modes fired on the first run — a false pass on the
export question and a false failure on the payment.

## Next

`packages` is the worst remaining ratio: 13 of 39 never entered, all of them
interaction handlers — search, sort, navigation, row selection, keyboard,
`_on_open`, `menu_items`.
