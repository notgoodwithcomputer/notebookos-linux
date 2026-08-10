# 006 — Undo in Cookbook, and the record that had stopped being true

**Lane:** A (cookbook) + C (harness, ROADMAP) · **Streams:** S5 FORGIVING, S2
**Status:** CLOSED 2026-08-06

## A. What the search for silent failures actually found

The plan lists twelve apps with "no status channel at all" and describes a
codebase leaking machinery. Measured today, that is mostly no longer true, and
saying so is worth more than adding channels nobody needs:

* **345 `try/except` handlers around a risky call swallow the exception** — but
  narrowed to a *user-initiated mutation* the count is 100, and reading them,
  nearly all are correct: best-effort cleanup, `makedirs(exist_ok=True)` in a
  helper whose caller reports, quarantine paths, geometry. Three that looked
  like real finds were not: `media._do_trash` has both the EXDEV fallback and a
  `_show_notice` (my detector saw only the inner handler); `gbasdk._bundle_write`
  re-raises after restoring; `sequencer` returns `(False, message)` tuples.
  **The detector was too imprecise to become a gate and was not kept.**
* **24 of 32 apps have a status channel.** Of the eight without, `ebook` and
  `novel` do too (`_show_message`, `_set_save_error` — my name hints missed
  them), `maps` reports its fatal states through a drawn empty state, and
  `calculator` has `_err_why`, which puts the reason in the display —
  *"Cannot divide by zero"*, verified by running it. That leaves `g2048`,
  `terminal` and `packages`, and a game and a terminal do not obviously need one.

## B. Cookbook has undo  (ROADMAP #33)

The only text-editing app in the OS without it. Selecting a whole method and
typing over it was final, and Delete Recipe was guarded by a dialog whose own
sentence read *"This cannot be undone."*

Wired to the shared `nbapp.UndoHistory`, reusing `_serialize()` as the snapshot
so undo can never capture a different subset of the model than the autosave
writes. Checkpoints around New Recipe and Delete Recipe; `_touch()` — already on
every edit path — now also calls `undo.touch()`, so a burst of typing collapses
into one step. `nbapp.undo_menu_items` puts both in the Edit menu, named
("Undo Delete Recipe"), and `nbapp.undo_keys` binds Ctrl+Z ahead of the
cook-mode arrow handling so it works while the caret is in an ingredient field —
which is where the loss happens.

**The confirm is deleted, not unified.** Its sentence is now false, and a
confirm that lies is worse than none. Delete acts at once and the status line
says `Deleted “%s” — Ctrl+Z to undo` (added to all 17 catalogs, each using its
own Edit-menu verb for Undo so the hint and the menu say the same word). This is
S5's principle applied for the first time: friction belongs to commitment, never
to mechanism.

**Gate: `tools/cookbook_undo_selftest.py`** — the real app, the real delete
path, and Ctrl+Z through `_on_key` with a synthesised `Gdk.EventKey` rather than
calling `undo()` directly, because the shortcut being *reachable* is the half
that matters. 14 checks: the recipe returns in its original position with its
contents, redo removes it again, a wiped method comes back, a category is
undoable, and the menu names the action.

**Red-proof (2026-08-06)** — `UndoHistory` replaced by a no-op so the app still
constructs: **7 of 14 failed**, with the four dependent assertions correctly
reporting `[not reached: precondition failed]` rather than passing.

### The vacuous pass, third session running
`the Undo item names what it would reverse` passed on `'Undo    Ctrl+Z'` — it
counted words on the whole label, and the **accelerator** supplied the second
one. Rewritten to strip the accelerator and require the exact action name.

Chasing it also produced a false alarm worth recording: the corrected check then
failed, and the label looked like an app bug. It was the *test's* sequence — it
asked at the end of the run, after undoing back to the baseline, a state that
legitimately has no label. Isolating it on a clean instance showed the app was
right all along (`Undo New Recipe`). **A failing assertion is not automatically
a defect in the code under test**; the probe that separated them took two
minutes and would otherwise have "fixed" correct behaviour.

## C. The ROADMAP had stopped being true

The campaign plan counts 26 of 40 items closed. The ROADMAP file marked **2**.
Its cut order and effort estimates are read off that table.

Reconciled: five items struck through with the evidence that closed them, four
of them **executed** rather than read —

| # | Evidence |
|---|---|
| 4 | `Replace video?` guard — the model task 002 copied to four PDF exports |
| 8 | ran it: `200+10%`=220, `200-10%`=180, `50%`=0.5, `200*10%`=20 |
| 16 | `tools/finder_crossfs_trash_selftest.py`, real EXDEV across two mounts |
| 24 | the inert Mouse page is gone; its orphan call was the task 001 crash |
| 31 | `tools/calendar_selftest.py` 18/18, including the short-month clamp |

A note at the head of the inventory now says plainly that the rest is
**unverified, not known-open**, and lists which items a grep pass suggests are
already done (#7, #17, #22, #23, #26, #27, #28, #30, #32, #35, #36, #39, #40)
and which look genuinely open (#15, #20, #21, #29, #33→now closed, #34, #37).

It says explicitly that grep is a hint and not a verdict — **#8 is the proof**:
the machinery was present *and* the behaviour was correct, but only running it
established the second part, and a symbol being present has no bearing on
whether the feature works.
