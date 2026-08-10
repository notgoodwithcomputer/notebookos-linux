# 030 — Academics, day one of the per-app pass

**Lane:** app-improve (24h/app loop, alphabetical) · **Streams:** S1 truth ·
S4 ALIVE · S5 FORGIVING · S7 flow
**Status:** OPEN — academics.py claimed 2026-08-07 10:10, day 1 of 1

Baseline before any edit: all 8 academics suites green (56/56 on the main one).
Nothing here was found by reading the ROADMAP; every item came from rendering
the app at the real budget, measuring it, or from a review pass.

## What landed

1. **The week now fits the week.** `_draw_timetable` drew at a single fixed
   density of 1.05 px per minute, so the grid demanded `(hi-lo)*1.05` pixels
   however much room it had. At the 1024x722 budget the schedule viewport is
   623px and an ordinary 08:00–18:00 week wanted 710: **the last hour of every
   weekday sat 87px below the fold**, one evening class put it 213px down, a
   07:30 lab and a 21:00 seminar 339px. The view whose whole promise is the week
   at a glance could not show the week at a glance on any screen this OS
   supports — and _grid_bounds stretching to reach an outlying class made it
   worse rather than better. Density is now fitted to the pane between a
   legibility floor (0.62, an hour row of 37px) and the tuned ceiling (1.05, so
   a two-hour term is not smeared over the whole pane). `_size_grid` asks for
   the floor height as a REQUEST, and the viewport grows it.

2. **The app stopped speaking two dialects of date.** The sidebar printed the
   store's own `2026-08-03` on every lecture while Homework said "today",
   "Friday", "2 days ago". Lecture rows now speak the same way, with the exact
   date on hover. Fixing that exposed two real defects in `_pretty_due`:
   - `if days < 0: "%d days ago"` caught EVERY past date however old, so the
     dated form below it was unreachable looking backwards — a lecture from last
     November read **"266 days ago"**. Counting now stops at a week each way.
   - the month was translated ALONE and concatenated in English word order.
     nbi18n does not merely translate a date, it REORDERS one, and
     `_date_lookup` only fires on a whole-date string. Measured against the
     shipped catalogs: **"14 九月"** where Chinese writes 9月14日, "14 9月" for
     Japanese, "14 9월" for Korean's 9월 14일, "14 Septiembre" for Spanish's
     "14 de septiembre", plus a mid-sentence capital in French and Russian.
     Six of seventeen languages. The whole string now goes through `_t()`.
   - a year is added when the date is not in the current one, and `m == 0` is
     rejected explicitly (`_MONTHS[m-1]` is December, not an IndexError — the
     same negative-index trap this file already paid for with `cls == -1`).

3. **`note` on an assignment became reachable.** It has been in the saved
   schema, preserved by `_clean_homework` and capped at 200 chars, since the
   homework list was written — with no field to type it in and no line to show
   it, so nothing could ever put a character there. Same defect `room` and
   `instructor` had on a class. Added a Note field to the dialog and a third
   line to the row. `_homework_dialog` now returns a **dict** (the shape
   `_class_dialog` already returns) rather than a positional 3-tuple, so the
   next field added cannot silently break its call sites.

4. **A corrupted class record no longer re-files the surviving work.** Skipping
   a malformed class closes the gap it left, moving every later class DOWN one
   index — while the lectures and assignments that name those classes still hold
   the file's numbering. With a bad record at index 1, a lecture belonging to the
   class at index 2 was filed under the FIRST class and its assignment was untied
   from any class at all. `_load_from_disk` now carries a file-index → loaded-
   index map through both `lectures` and `_clean_homework`. Found by a Codex
   review pass; **verified independently against a real store on disk before
   being believed** — see below.

5. **The Russian sidebar gave 53px back to the note column.** The three
   segmented view-switcher labels measured 278px against a sidebar asking for
   220, so the sidebar swelled to 279 and the difference came straight off the
   reading measure on every view. The labels now WRAP (Pango WORD mode, so a
   label's minimum is its longest unbreakable word) instead of widening;
   "Домашние задания" sits on two lines. Russian sidebar 279 → 226. The note
   column reaches its ideal 720px measure in Russian for the first time.
   English is byte-identical. Height was the right currency: this pane had
   442px of vertical headroom and zero horizontal. Closes the bug-fix session's
   `academics[ru]` handoff.

6. **The timetable became directly manipulable.** An empty slot on a week grid
   is a place a class could go, and the only way to put one there was a button
   at the top of the pane whose dialog always opened on **Monday 09:00**,
   whatever part of the week you were looking at and had just pointed to.
   Double-clicking an empty slot now opens it with the day and the hour filled
   in from where the pointer was, snapped to the half hour (nobody schedules a
   class for 10:17), pulled back near the end of the day so the default
   one-hour length still fits. DOUBLE, not single: a single click has to stay
   free to focus the grid for keyboard use and drop the selection, and a modal
   that opens because somebody clicked the background is a trap — there is a
   check pinning exactly that. The grid's tooltip now says the gesture, since
   nothing else would.

7. **A titleless assignment stopped being destroyed.** `_clean_homework`
   required a title and dropped any record without one — taking the note and
   due date on it too. Measured: two records into the homework list, one out,
   and the close-time save wrote that over the only copy. Anything carrying
   content is now salvaged under "Untitled assignment"; a record with nothing
   in it at all is still let go, because that is not salvage. Naming the
   absence rather than inventing a title out of the note: putting words in
   somebody's mouth is the other way to fail here.

8. **A dormant lecture-shredder behind a docstring saying it did not exist.**
   `_prune_empty_classes` opens "Renumber `cls` indices after a class is
   removed. Nothing is pruned." — and then ran
   `self.lectures = [l for l in self.lectures if 0 <= l["cls"] < n]`, silently
   DELETING every lecture whose class index was out of range, while the very
   next loop merely untied an assignment in exactly the same state. Measured
   side by side: one orphaned lecture in, **zero** out, its text gone from the
   model; one orphaned assignment in, one out. Backwards, in the one file where
   a note is the thing that cannot be re-derived. Both call sites happen to
   hand it in-range indices today so it never fired — which is precisely how
   the previous two rounds of this bug got in. Orphans are now parked under the
   recovery class, exactly as `_load_from_disk` already parks them.

9. **One creator disagreed with the class schema.** `_append_class` — the path
   "New Lecture" takes on a fresh install — built a class from only
   `{label, color}`, so it was the only class in the app with no `room`,
   `instructor` or `meets`. `_remove_meeting` indexes `["meets"]` directly,
   twice; nothing reaches it today only because a class with no `meets` key
   contributes no meetings to the timetable and so has no block to select. The
   loader fills the fields in, which is why save-and-reopen quietly healed it
   and nothing ever showed. Now every route makes the whole record, and the
   class suite checks all three routes.

10. **The first screen of a fresh install stopped repeating itself.** Academics
    ships empty, so the empty state IS what every new user sees. The sidebar
    heading said "No classes" and the line directly beneath it said "No
    classes" — verbatim, one line apart, on all three views. The comment above
    that line described what it was supposed to say instead ("explains what the
    pane is for rather than repeating it"); the code had regressed to exactly
    the thing the comment said not to do. Schedule carried two further copies,
    in the subtitle and the empty-state title: **four instances of the same two
    words on one screen**, measured. Each view's sidebar now names what that
    pane will hold, the subtitles say nothing rather than restating an empty
    state directly below them, and the timetable's empty state always reads "No
    class times" — that pane's subject is when your classes meet, and an empty
    term has none of those whichever way it is described.

11. **Opening the app stopped shrinking the file.** The loader normalises every
    record to a fixed schema and the next save writes that normalisation
    straight back, so any key this version did not recognise was DELETED by the
    mere act of opening Academics — no user action, no warning. Measured on one
    open-and-save: top-level `"term"`, a meeting's `"zoom"`, a lecture's
    `"starred"` and an assignment's `"weight"` all gone. Survivable only while
    exactly one program ever writes this file AND the schema never changes, and
    neither of those stays true — a store written by a newer build, hand-edited,
    or extended by a later version of this app loses whatever the reader happens
    not to know about. Unknown keys are now carried through untouched at all
    five levels (top of file, class, meeting, lecture, assignment) while the
    known fields are still normalised, and the new suite checks BOTH — a
    carry-through implemented by echoing the raw record straight back out would
    pass the preservation checks while the loader had quietly stopped doing its
    job.

12. **The export path had its own copy of the negative-index trap.**
    `_pdf_name` and `_make_active_pdf` both did `self.classes[lec["cls"]]`. A
    `cls` of -1 means "no class", and a negative index is the LAST element
    rather than a miss — so an untied lecture exported under whichever class
    happened to be last, in the PDF's header AND in its filename
    (`maths-loose-note.pdf`, measured). This is the same trap `_class_label`
    and `_class_color` are written the way they are to avoid; the export path
    never got the memo. Added `_class_of`, and swept the other three direct
    lookups: `_refresh_canvas` and `_delete_lecture` take the guarded form,
    while `_rename_class` resolves and BAILS instead — it mutates the record it
    finds, so an empty-dict fallback would be worse than a crash, landing the
    rename in a throwaway dict while the app reported success.

    Also checked while in here: `_pdf_name` cannot escape the Documents
    directory. Every non-alphanumeric character is replaced with a space before
    the name is assembled, so `../../etc/passwd` comes out `etc-passwd.pdf`.
    Not a defect — verified, not assumed.

13. **VERIFIED, NOT A DEFECT: bold with no selection does nothing.** Pressing
    B / Ctrl+B with no selection returns focus to the note and applies nothing.
    That looks like a silent no-op on a button's path — a defect class this
    project names explicitly — but `journal.py:998` does exactly the same thing,
    character for character, so it is a deliberate OS-wide convention among the
    rich-text editors rather than an Academics bug. Changing it here alone would
    have manufactured an inconsistency. Recording it because the next person to
    look will have the same suspicion.

14. **Academics can hold an exam.** Homework was the only dated item, so the
    single date a term is actually organised around could not be recorded at
    all. An exam rides the same list rather than becoming a fourth concept —
    same due-date grouping, same class tie, same tick when it is behind you —
    carried by one new `kind` field that is either "work" or "exam". Anything
    that is not the word "exam" reads as ordinary work, so every store written
    before today keeps working with nothing migrated and nothing rewritten. In
    a row the marker is set in small caps and ink, NOT in the accent: red means
    "late" on this screen and must go on meaning only that. Two things due the
    same day are not equally heavy, so an exam sorts above work within its
    group. It reaches the printed list too — paper that quietly drops a
    distinction the screen makes is the same lie as not having the field.

    Left for the campaign session to rule on: the view is still called
    "Homework" while it now also holds exams. Renaming it is a 17-catalog
    change in a lane I do not own, and "Homework" is not wrong so much as
    narrow.

15. **A lecture can be moved to another class.** It could not be. A lecture's
    class was chosen once, at creation, and was never editable afterwards — and
    `_new_lecture` GUESSES it from the timetable (whichever class meets now, or
    next). A note taken in a free period, in the wrong room, or ten minutes
    before the hour was filed under the wrong class permanently. An ASSIGNMENT
    has had a class combo in its dialog all along; the thing you actually write
    during a lecture had nothing. Same shape as `room` and `instructor` living
    in the schema with no UI: the model could express it, the interface could
    not. Added Edit ▸ Move to Class…, which takes the next free number in the
    destination class (moving on top of an existing "01" would give that class
    two of them, and the number is how the sidebar tells them apart). The item
    is absent with only one class, since it could then only move a lecture to
    where it already is.

16. **Clicking a lecture stopped rebuilding the notebook.** `_select` called
    `_refresh_sidebar`, which destroys and reconstructs every class header and
    every lecture row there is — to change which row carries one CSS class. On a
    term of 24 classes and 600 lectures (a four-year degree kept in one file,
    which is what an app that never asks you to start a new notebook invites)
    that is **383ms of widget construction on every click**. `_set_active_row`
    now moves the highlight in place — **383ms → 4.4ms, 87x** — and falls back
    to the full rebuild only when the row is not on screen (another view, or
    filtered out by a search), which is the case that genuinely needs one.

    Measured as a RATIO inside one process, not as a millisecond threshold: this
    machine may be building an ISO, encoding a map or running two hundred other
    suites at the same time, and an absolute bound on it is a coin-toss that
    eventually fails for reasons unrelated to the code. Wall-clock numbers taken
    minutes apart during this work varied 3x on load alone.

17. **The app could show a day that does not exist.** `nbapp.day_ordinal` is
    deliberately forgiving — it takes `2026-02-29` (2026 is not a leap year) and
    hands back the ordinal for `2026-03-01`, and the same for `2026-04-31` and
    `2026-01-32`. The homework dialog validated with it and then stored the RAW
    STRING the user typed, which the list printed back. Measured: a due date
    could read **"32 January"**, **"31 April"**, **"30 February"** or "29
    February" in a non-leap year, while being grouped and sorted as the
    following month. Two lies in one row — a day that is not on the calendar,
    and behaviour belonging to a different day. `_canonical_date` now requires
    the ordinal to round-trip to the same calendar day; three locks on the door
    (dialog, loader, and `_pretty_due` itself, since it is also handed lecture
    dates from wherever a store has been). Formatting slop is NOT the same
    mistake and is normalised rather than refused: `2026-1-5` still means the
    fifth of January. The dialog now gives that its own sentence, because
    answering "a due date looks like 2026-08-11" to somebody who typed
    2026-02-29 tells them nothing about what is wrong with it.

    The second red proof is the one to read: WITHOUT the round-trip comparison
    the app does not print a fake day, it prints a REAL day that is not the one
    the user typed (`2026-02-29` → "1 March") — the quieter and worse failure.

## Gates

New: `tools/academics_fit_selftest.py` (9 checks),
`tools/academics_dates_selftest.py` (21),
`tools/academics_timetable_selftest.py` (13),
`tools/academics_emptystate_selftest.py` (7),
`tools/academics_roundtrip_selftest.py` (10),
`tools/academics_exam_selftest.py` (18),
`tools/academics_session_selftest.py` (45 — a whole term driven end to end),
`tools/academics_select_selftest.py` (10 — correctness AND cost), and
`tools/academics_export_selftest.py` (7 — **built by Codex**, see below).
Extended:
`tools/academics_selftest.py` 56 → 58, `tools/academics_class_selftest.py`
16 → 35, `tools/academics_damage_selftest.py` +2 cases and an ownership
assertion (11 → 13 cases). All **12** academics suites green, plus data_safety / config_resilience / document_safety /
minsize_sweep / i18n_placeholder / toyfont / css_parse / ascii_css /
construct_one, and voice_check + jargon_sweep both CLEAN (no ledger entries
touched).

**New English strings** for the campaign session's i18n_merge: `"Kind"`,
`"Assignment"`, `"Exam"` (the exam selector and its row/paper marker), `"Note"` and the
placeholder `"Chapters 4 to 7, show working"` (homework dialog),
`"Untitled assignment"` (salvage placeholder), `"There is no such day in that month."` (due-date validation), the three sidebar empty-state
lines (`"Lectures appear here, under the class they belong to"`,
`"Classes appear here, with the times they meet"`,
`"Classes appear here, with what is left to do"`), and the grid tooltip is now
`"Click a class time to change or remove it. Double-click an empty slot to add
one."` — the old shorter form is superseded. No catalog was touched.

THREE of my own gates were caught being VACUOUS or TOO WEAK, every one of them
by running the mutation rather than by reading the check — which is the whole
argument for M1 in one day's work:

- the **exam** suite's language checks could not fail. They exist to catch a
  dialog that reads a widget's LABEL instead of its position, which breaks in
  every language but English. Mutating the app to do exactly that left the suite
  GREEN in French and Chinese — because "Exam" and "Assignment" are new source
  strings no catalog has been merged with yet, so `_t()` returned the English
  word and the French label WAS "Exam". The child now injects catalog entries
  itself and asserts the label really changed.
- the **session** suite deleted the LAST class, which shifts no index, so
  breaking the reindex in `_delete_class_at` left all 42 checks green. It now
  builds three classes and deletes the MIDDLE one, with work hanging off the
  class after it.
- the **fit** suite measured the last class BLOCK instead of the grid bottom,
  and then recomputed the density itself instead of reading what the draw used.

A fourth was a MUTATION that proved nothing rather than a weak check. Deleting
`undo.checkpoint()` from the new Move-to-Class path left its undo checks green,
and the honest reading is not "the check is weak" but "that mutation changes no
behaviour in that scenario": checkpoint's job is to flush a half-finished TYPING
step so a structural edit becomes its own, and the scenario had no typing in
flight. The check now types a sentence, moves the lecture, and undoes once —
which is the real user story (Ctrl+Z after re-filing must undo the re-filing,
not swallow what you just wrote). With the checkpoint removed that fails, and
the sentence is gone. A mutation that does not change behaviour is not evidence
about the gate, in either direction.

Every one of these was **watched failing** before being trusted — mutations and
their measured output are recorded in each suite's docstring. Two of the gates
were themselves caught being too weak, both by running them against the broken
build rather than by reading them:

- the fit suite first measured only the bottom of the last CLASS BLOCK. A week
  whose last class ends at 16:50 keeps every block above the fold while the
  17:00 and 18:00 rows are still cut off — the bug — and it called that a pass.
- it then computed the grid's bottom by calling `_px_per_min()` itself. That is
  not a measurement: with the fix reverted the app painted an 823px grid while
  the suite calmly reported 573 and passed. `_draw_timetable` now records
  `_grid_ppm`/`_grid_bottom` as it paints, the way it already recorded
  `_blocks`, and the suite reads those.
- the damage suite counted records and never asked which class they came back
  attached to, and its one malformed-class case appended the junk at the END of
  the list where skipping it shifts nothing. Both closed. Note what the counters
  say on the broken build: `(2,3,2,1)` in memory and on disk, identical to the
  passing control. Nothing was lost — it was filed under the wrong name.

## On the Codex pass

**The recipe that works, from the campaign session, confirmed here: Codex is a
BUILDER in this repo, not a reviewer.** Three review dispatches yielded one real
defect, one false positive and one half-right finding, at the cost of two total
failures (one content-flagged, one that fought its sandbox for 45 minutes and
was cancelled). Re-dispatched as a BUILD task — one new file, all facts inline,
a scratch directory INSIDE the repo rather than /tmp, red proofs required, write
scope limited to the deliverable — it produced `tools/academics_export_selftest.py`
(7 checks, green, with its three mutations and their measured failure text in
the docstring) in about 25 minutes and restored academics.py byte-for-byte.
The one piece of residue was an empty scratch directory, since removed.



Two dispatches. The first **failed and produced nothing**: the sandbox could not
create a temp dir, then the run was content-flagged and aborted. Reworded and
given a writable scratch dir, the second returned three findings. Verified each
against a real store rather than accepting them:

- **class-index retargeting — REAL**, fixed above (item 4).
- **`ranges` with a non-iterable value crashes `_copy_lectures` — FALSE
  POSITIVE at the app level.** The repro called `_copy_lectures` directly with a
  hand-built dict; the loader sanitises `ranges` to `{}`, so no store can reach
  it. Not fixed, deliberately.
- **round-trip drops unknown fields — PARTLY REAL.** Unknown keys (`term`,
  `zoom`, `starred`, `weight`) are dropped, which matters only for forward
  compatibility on a file with one writer. But a homework record with a note and
  **no title is destroyed outright** (2 records in, 1 out) — fixed, item 7.

The lesson worth keeping: the monkeypatching in the supplied repro (it replaced
`builtins.open`) could manufacture a defect that no user can reach, and did once
out of three.

## Still open on this app

- The campaign's "destruction gets no friction — undo replaces confirmation"
  decision has NOT been applied here. Academics still has five `_confirm` calls
  (`_delete_class_at`, `_delete_lecture`, `_remove_homework`, `_delete_homework`,
  `_remove_meeting`). Left alone deliberately: doing it in one app first makes
  Academics inconsistent with the other thirty until they catch up, so this
  wants to be a coordinated pass rather than a unilateral one. Campaign to call
  the ordering.
- No notion of an exam or an assessment anywhere in the app; homework is the
  only dated item. The largest genuine feature gap, not attempted today.

## Handoffs raised

- campaign: `minsize_sweep` cannot distinguish "fills the width by design" from
  "barely fits" for an app with an elastic reading column; academics is the case
  that shows it.
- campaign: the Russian catalog gives "14 ноябрь 2025" where Russian wants the
  genitive "14 ноября". Catalog-owned; likely the same in the other Slavic
  catalogs.
